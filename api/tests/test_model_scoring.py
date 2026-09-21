"""Testes do score híbrido (W13): o modelo entra **ao lado** das regras, nunca no lugar.

A regra de ouro desta feature, e o que a maior parte destes testes verifica: o nível 🟢🟡🔴, o
limite e os motivos continuam vindo só das regras. Se o modelo mexer em algum deles, é bug.
"""

import json
from collections.abc import Callable, Iterator
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.core.clock import today_local
from app.main import app
from app.schemas.risk import ModelInfo
from app.services import model_scoring
from app.services.terrain import build_terrain, clear_terrain_cache
from tests.conftest import FIXTURES_DIR
from tests.test_limits import make_farm
from tests.test_risk_route import forecast_payload

# O modelo recebe `DailyWeather` (o clima do dia), não o `DayRisk` já avaliado.
from tests.test_risk_rules import make_day

FARM_ID = "cafe-carmo-de-minas"
RISK_URL = f"/api/v1/farms/{FARM_ID}/risk"


def elevation(short_name: str = "carmo") -> list[float]:
    path = FIXTURES_DIR / f"terrain_elevation_{short_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["elevation"]


def metadata_sample(**changes: object) -> dict:
    """Metadados no formato que a D3 grava, com os números do artefato real de 20/09."""
    base = {
        "version": "v1",
        "trained_at": "2026-09-20T14:51:27+00:00",
        "chosen": "regressao_logistica",
        "beats_baseline": False,
        "split_sizes": {"train": 775, "validation": 254, "test": 155},
        "test": {
            "regressao_logistica": {"n": 155, "positives": 8, "pr_auc": 0.0727, "roc_auc": 0.6054},
            "baseline_regras": {"n": 155, "positives": 8, "pr_auc": 0.0915, "roc_auc": 0.6216},
        },
        "importances": [
            {"feature": "state", "importance": 0.01692},
            {"feature": "dry_spell_max_days", "importance": 0.00595},
            {"feature": "elevation_range_m", "importance": 0.00459},
            {"feature": "pct_lowland", "importance": 0.001},
        ],
    }
    base.update(changes)
    return base


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    clear_terrain_cache()
    yield
    clear_terrain_cache()
    app.dependency_overrides.clear()


@pytest.fixture
def api(make_client: Callable[..., OpenMeteoClient]) -> Callable[..., TestClient]:
    def factory() -> TestClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if "elevation" in request.url.path:
                path = FIXTURES_DIR / "terrain_elevation_carmo.json"
                return httpx.Response(200, json=json.loads(path.read_text(encoding="utf-8")))
            return httpx.Response(200, json=forecast_payload(today_local()))

        app.dependency_overrides[get_open_meteo_client] = lambda: make_client(handler)
        return TestClient(app)

    return factory


# --- O bloco de contexto do modelo --------------------------------------------------------------


def test_the_model_info_comes_from_the_artifact() -> None:
    """Nada é constante no código: a D3 retreina e o número muda sozinho."""
    info = model_scoring.model_info(metadata_sample())

    assert info is not None
    assert info.version == "v1"
    assert info.algorithm == "regressao_logistica"
    assert info.test_pr_auc == 0.0727
    assert info.baseline_pr_auc == 0.0915
    assert info.test_samples == 155
    assert info.test_positives == 8
    assert info.beats_baseline is False


def test_a_retrained_model_changes_the_block_without_touching_the_code() -> None:
    """O `dev-dados` roda o dataset completo e a resposta acompanha, sem alterar código."""
    info = model_scoring.model_info(
        metadata_sample(
            version="v2",
            beats_baseline=True,
            test={
                "regressao_logistica": {"n": 900, "positives": 60, "pr_auc": 0.31, "roc_auc": 0.78},
                "baseline_regras": {"n": 900, "positives": 60, "pr_auc": 0.12, "roc_auc": 0.63},
            },
        )
    )

    assert info is not None
    assert info.version == "v2"
    assert info.beats_baseline is True
    assert info.test_pr_auc == 0.31
    assert "superou o baseline" in info.note


def test_without_metadata_there_is_no_model_block() -> None:
    assert model_scoring.model_info(None) is None
    assert model_scoring.model_info({}) is None


def test_only_three_drivers_are_exposed() -> None:
    info = model_scoring.model_info(metadata_sample())

    assert info is not None
    assert [driver.feature for driver in info.drivers] == [
        "state",
        "dry_spell_max_days",
        "elevation_range_m",
    ]


# --- A ressalva viaja com o número ---------------------------------------------------------------


def test_the_note_says_the_model_did_not_beat_the_rules() -> None:
    """Cuidado nº 1 da W13: a honestidade tem que estar no dado, não só no slide."""
    info = model_scoring.model_info(metadata_sample())

    assert info is not None
    assert "não superou" in info.note
    assert "as regras continuam sendo a base do alerta" in info.note


def test_the_note_carries_the_metric_and_the_sample_size() -> None:
    info = model_scoring.model_info(metadata_sample())

    assert info is not None
    assert "0,073" in info.note  # AUC-PR do modelo
    assert "0,091" in info.note  # AUC-PR do baseline
    assert "155 linhas e 8 sinistros" in info.note
    assert "1184 linhas" in info.note


def test_the_note_warns_about_the_daily_window() -> None:
    """O modelo foi treinado por safra e aqui é aplicado a um dia: quem lê precisa saber."""
    info = model_scoring.model_info(metadata_sample())

    assert info is not None
    assert "janela de um dia" in info.note
    assert "não como probabilidade calibrada" in info.note


# --- As variáveis que o modelo recebe -----------------------------------------------------------


def test_the_features_mix_terrain_and_weather() -> None:
    farm = make_farm()
    terrain = build_terrain(farm, elevation())

    features = model_scoring.build_features(farm, terrain, make_day(rain_72h_mm=42.0, rain_mm=14.5))

    assert features["slope_max_deg"] == terrain.stats.slope_max_deg
    assert features["pct_lowland"] == 25.0
    assert features["rain_total_mm"] == 14.5
    assert features["days_rain72h_ge30"] == 1
    assert features["state"] == "MG"
    assert features["crop_group"] == "Café"
    assert features["start_month"] == 9


def test_an_unknown_crop_becomes_an_unknown_category() -> None:
    """Categoria fora do treino entra nula, e o pipeline da D3 trata — não quebra."""
    farm = make_farm().model_copy(update={"crop": "quinoa"})
    terrain = build_terrain(farm, elevation())

    assert model_scoring.build_features(farm, terrain, make_day())["crop_group"] is None


def test_the_features_cover_everything_the_model_declares() -> None:
    """Se a D3 acrescentar uma variável, este teste avisa antes de a previsão sair torta."""
    from app.services.model import declared_features

    farm = make_farm()
    features = model_scoring.build_features(farm, build_terrain(farm, elevation()), make_day())
    numeric, categorical = declared_features()

    assert set(numeric + categorical) <= set(features)


# --- O modelo não decide o nível -----------------------------------------------------------------


def test_the_model_never_changes_the_level_or_the_limit(api: Callable[..., TestClient]) -> None:
    """Cuidado nº 3 da W13: se o modelo entrar no cálculo do nível, é bug."""
    client = api()

    body = client.get(RISK_URL).json()

    for day in body["days"]:
        levels = {cell["level"] for cell in day["cells"]}
        expected = "red" if "red" in levels else "yellow" if "yellow" in levels else "green"
        # O pior nível continua sendo o das células, que saem só das regras.
        assert day["worst_level"] == expected
        # E o limite continua sendo floor_0.5(L_ref × fator do solo).
        assert day["tilt_limit_deg"] in {15.0, 12.5, 10.0}


def test_the_reasons_never_mention_the_model(api: Callable[..., TestClient]) -> None:
    """Os motivos que o operador lê são das regras: o modelo não escreve alerta."""
    client = api()

    for day in client.get(RISK_URL).json()["days"]:
        for cell in day["cells"]:
            for reason in cell["reasons"]:
                assert reason["hazard"] in {"rollover", "bogging", "lightning", "wind", "fire"}


def test_the_day_carries_the_model_fields(api: Callable[..., TestClient]) -> None:
    client = api()

    body = client.get(RISK_URL).json()

    day = body["days"][0]
    assert "model_probability" in day
    assert "model_version" in day
    assert "model_drivers" in day


def test_with_an_artifact_the_probability_is_filled(api: Callable[..., TestClient]) -> None:
    """Critério de aceite: com artefato, vem probabilidade e versão."""
    from app.services.model import get_model

    if get_model() is None:
        pytest.skip("sem artefato de modelo neste ambiente")

    body = api().get(RISK_URL).json()

    assert body["model"] is not None
    assert body["model"]["version"]
    for day in body["days"]:
        assert 0.0 <= day["model_probability"] <= 1.0
        assert day["model_version"] == body["model"]["version"]
        assert len(day["model_drivers"]) == 3


def test_without_an_artifact_everything_is_null(
    api: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critério de aceite: sem modelo, os campos vêm `null` e nada quebra."""
    monkeypatch.setattr("app.services.model.get_model", lambda: None)
    monkeypatch.setattr("app.services.model.load_metadata", lambda path=None: None)

    response = api().get(RISK_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["model"] is None
    assert all(day["model_probability"] is None for day in body["days"])
    assert all(day["model_version"] is None for day in body["days"])
    # E o que importa continua lá: o nível das regras.
    assert all(day["worst_level"] in {"green", "yellow", "red"} for day in body["days"])


def test_a_broken_model_does_not_break_the_response(
    api: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Artefato corrompido, previsão que estoura: o alerta ao operador não pode cair junto."""

    def explode(*args: object, **kwargs: object) -> float:
        raise RuntimeError("modelo quebrado")

    monkeypatch.setattr("app.services.model.predict_proba", explode)

    response = api().get(RISK_URL)

    assert response.status_code == 200
    body = response.json()
    assert all(day["model_probability"] is None for day in body["days"])
    assert all(day["worst_level"] in {"green", "yellow", "red"} for day in body["days"])


def test_apply_model_does_nothing_without_a_pipeline() -> None:
    farm = make_farm()
    terrain = build_terrain(farm, elevation())
    info = model_scoring.model_info(metadata_sample())

    # Não levanta e não escreve nada.
    model_scoring.apply_model([], farm, terrain, [], None, info)
    model_scoring.apply_model([], farm, terrain, [], object(), None)


def test_the_score_is_none_without_a_pipeline() -> None:
    farm = make_farm()
    terrain = build_terrain(farm, elevation())

    assert model_scoring.score_day(None, farm, terrain, make_day(), "v1") is None


# --- Trilha de auditoria (I5 + W13) ---------------------------------------------------------------


def test_the_model_version_reaches_the_audit_trail(api: Callable[..., TestClient]) -> None:
    """Critério de aceite: cada consulta com modelo gera linha com a versão da regra e do modelo."""
    from sqlmodel import Session

    from app.db import get_engine
    from app.repositories import audit as audit_repository
    from app.schemas.audit import DecisionType
    from app.services.model import get_model

    if get_model() is None:
        pytest.skip("sem artefato de modelo neste ambiente")

    with Session(get_engine()) as session:
        before = len(audit_repository.list_decisions(session, entity_id=FARM_ID, limit=500))

    api().get(RISK_URL)

    with Session(get_engine()) as session:
        rows = audit_repository.list_decisions(
            session, entity_id=FARM_ID, decision_type=DecisionType.RISK_SCORE, limit=500
        )

    assert len(rows) > before - 1
    latest = rows[0]
    assert latest.rule_version.startswith("regras-de-risco/")
    assert latest.model_version is not None


def test_a_model_info_is_serialisable(api: Callable[..., TestClient]) -> None:
    """O bloco vai inteiro para o front: versão, métricas, drivers e a nota."""
    info = model_scoring.model_info(metadata_sample())

    assert info is not None
    payload = json.loads(ModelInfo.model_validate(info).model_dump_json())
    assert set(payload) == {
        "version",
        "trained_at",
        "algorithm",
        "test_pr_auc",
        "test_roc_auc",
        "baseline_pr_auc",
        "baseline_roc_auc",
        "test_samples",
        "test_positives",
        "beats_baseline",
        "drivers",
        "note",
    }


def test_the_forecast_date_is_used_for_the_month() -> None:
    """`start_month` é do dia previsto, não do dia de hoje."""
    farm = make_farm()
    terrain = build_terrain(farm, elevation())

    features = model_scoring.build_features(farm, terrain, make_day(day=date(2026, 12, 25)))

    assert features["start_month"] == 12


# --- As duas metades do modelo andam juntas (revisão da W13) -------------------------------------


def test_an_unreadable_pipeline_hides_the_whole_model_block(
    api: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.joblib` ilegível com `.json` intacto: o estado que um retreino interrompido produz.

    Sem a amarração, a resposta anunciava "modelo v1, AUC-PR 0,073…" **sem uma única
    probabilidade** — e a trilha assinava a decisão com um modelo que não pontuou nada.
    """
    monkeypatch.setattr("app.services.model.get_model", lambda: None)
    # Os metadados continuam lá e legíveis, como no disco depois de uma escrita interrompida.
    monkeypatch.setattr("app.services.model.load_metadata", lambda path=None: metadata_sample())

    response = api().get(RISK_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["model"] is None, "anunciou o modelo sem ter pontuado nada"
    assert all(day["model_probability"] is None for day in body["days"])
    assert all(day["model_version"] is None for day in body["days"])
    assert all(day["model_drivers"] is None for day in body["days"])
    # E o que o operador segue continua de pé.
    assert all(day["worst_level"] in {"green", "yellow", "red"} for day in body["days"])


def test_an_unreadable_pipeline_leaves_the_audit_without_a_model_version(
    api: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Garantia da I5: a trilha não pode registrar uma decisão assinada por um modelo ausente."""
    from sqlmodel import Session

    from app.db import get_engine
    from app.repositories import audit as audit_repository
    from app.schemas.audit import DecisionType

    monkeypatch.setattr("app.services.model.get_model", lambda: None)
    monkeypatch.setattr("app.services.model.load_metadata", lambda path=None: metadata_sample())

    api().get(RISK_URL)

    with Session(get_engine()) as session:
        rows = audit_repository.list_decisions(
            session, entity_id=FARM_ID, decision_type=DecisionType.RISK_SCORE, limit=1
        )

    assert rows
    assert rows[0].model_version is None
    assert rows[0].rule_version.startswith("regras-de-risco/")


def test_missing_metadata_with_a_working_pipeline_still_scores(
    api: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """O contrário também precisa ser coerente: sem `.json`, não há bloco nem versão por dia."""
    from app.services.model import get_model

    if get_model() is None:
        pytest.skip("sem artefato de modelo neste ambiente")

    monkeypatch.setattr("app.services.model.load_metadata", lambda path=None: None)

    body = api().get(RISK_URL).json()

    assert body["model"] is None
    assert all(day["model_probability"] is None for day in body["days"])

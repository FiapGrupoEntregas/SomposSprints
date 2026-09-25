"""Testes de `GET /api/v1/farms/{farm_id}/risk` (W3). A Open-Meteo é sempre mockada.

A previsão usada aqui é a resposta real salva em `tests/fixtures/open_meteo_forecast.json`,
**deslocada** para começar em hoje − 3 dias. Assim os testes continuam valendo em qualquer data,
sem acessar a rede.
"""

import json
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.core.clock import today_local
from app.main import app
from app.schemas.risk import SoilState
from app.services import farms as farms_service
from app.services import model as model_service
from app.services import model_scoring
from app.services.risk import PAST_DAYS, tilt_limit
from app.services.terrain import GRID_SIZE, clear_terrain_cache
from tests.conftest import FIXTURES_DIR, RequestRecorder, load_fixture

DEMO_FARM_ID = "cafe-carmo-de-minas"
UNKNOWN_FARM_ID = "fazenda-inexistente"
RISK_URL = f"/api/v1/farms/{DEMO_FARM_ID}/risk"

CELL_COUNT = GRID_SIZE * GRID_SIZE

# regras-de-risco §3 e §4 — com L_ref 15° o solo encharcado derruba o limite para 10°
SATURATED_TILT_LIMIT_DEG = 10.0

# L_ref das fazendas do `farms.json` e o de uma máquina mais frágil, usada no teste do §4
DEMO_TILT_LIMIT_DEG = 15.0
FRAGILE_TILT_LIMIT_DEG = 12.0

ClientFactory = Callable[..., OpenMeteoClient]


def elevation_payload(short_name: str) -> dict:
    path = FIXTURES_DIR / f"terrain_elevation_{short_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def forecast_payload(today: date) -> dict:
    """Previsão real da fixture, com as horas deslocadas para começar em `today` − 3 dias."""
    payload = load_fixture("open_meteo_forecast.json")
    hourly = payload["hourly"]
    moments = [datetime.fromisoformat(value) for value in hourly["time"]]
    shift = (today - timedelta(days=PAST_DAYS)) - moments[0].date()
    hourly["time"] = [(moment + shift).isoformat(timespec="minutes") for moment in moments]
    return payload


def open_meteo_handler(
    today: date, short_name: str = "carmo"
) -> Callable[[httpx.Request], httpx.Response]:
    """Responde à Elevation API com o relevo da fazenda e à Forecast API com a previsão."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "elevation" in request.url.path:
            return httpx.Response(200, json=elevation_payload(short_name))
        return httpx.Response(200, json=forecast_payload(today))

    return handler


def failing_handler(_: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="erro na origem")


@pytest.fixture
def today() -> date:
    """A data de hoje em São Paulo, capturada **uma vez por teste**.

    Chamar `today_local()` várias vezes no mesmo teste daria dias diferentes se ele atravessasse
    a virada da meia-noite.
    """
    return today_local()


@pytest.fixture(autouse=True)
def clean_terrain_cache() -> Iterator[None]:
    """O relevo fica 24 h em cache: cada teste começa com ele vazio."""
    clear_terrain_cache()
    model_service.get_optional_neural_model.cache_clear()
    yield
    clear_terrain_cache()
    model_service.get_optional_neural_model.cache_clear()
    app.dependency_overrides.clear()


@pytest.fixture
def api(
    make_client: ClientFactory,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], TestClient]:
    def factory(
        handler: Callable[[httpx.Request], httpx.Response],
        raise_server_exceptions: bool = True,
    ) -> TestClient:
        open_meteo = make_client(handler)
        app.dependency_overrides[get_open_meteo_client] = lambda: open_meteo
        return TestClient(app, raise_server_exceptions=raise_server_exceptions)

    return factory


def test_risk_returns_seven_days_from_today(api: Callable[..., TestClient], today: date) -> None:
    client = api(open_meteo_handler(today))

    response = client.get(RISK_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["farm_id"] == DEMO_FARM_ID
    assert body["scenario"] is None
    assert [day["date"] for day in body["days"]] == [
        (today + timedelta(days=offset)).isoformat() for offset in range(7)
    ]
    assert all(len(day["cells"]) == CELL_COUNT for day in body["days"])


def test_every_day_carries_the_summary_the_front_needs(
    api: Callable[..., TestClient], today: date
) -> None:
    client = api(open_meteo_handler(today))

    day = client.get(RISK_URL).json()["days"][0]

    assert set(day) == {
        "date",
        "rain_mm",
        "rain_72h_mm",
        "wind_max_kmh",
        "gust_max_kmh",
        "temp_max_c",
        "rh_min_pct",
        "cape_max",
        "thunderstorm",
        "soil_state",
        "tilt_limit_deg",
        "worst_level",
        "pct_levels",
        "top_reasons",
        "cells",
        # Score híbrido (W13): nulos quando não há artefato de modelo carregado.
        "model_probability",
        "model_version",
        "model_drivers",
        "experimental_mlp_score",
    }
    assert set(day["pct_levels"]) == {"green", "yellow", "red"}
    assert day["soil_state"] in {"dry", "moist", "saturated"}


def test_the_worst_level_of_the_day_matches_its_cells(
    api: Callable[..., TestClient], today: date
) -> None:
    """Critério de aceite: o pior nível do card bate com o pior nível das células daquele dia."""
    client = api(open_meteo_handler(today))

    for day in client.get(RISK_URL).json()["days"]:
        levels = {cell["level"] for cell in day["cells"]}
        expected = "red" if "red" in levels else "yellow" if "yellow" in levels else "green"
        assert day["worst_level"] == expected


def test_every_yellow_or_red_cell_explains_itself_with_numbers(
    api: Callable[..., TestClient], today: date
) -> None:
    """Critério de aceite: cada célula 🟡 ou 🔴 explica o porquê, com números.

    A **única** exceção é a tempestade (§5.3 por `weather_code`): não há número a citar — ou o dia
    tem código de tempestade, ou não tem. Convenção fixada na W7.
    """
    client = api(open_meteo_handler(today))

    for day in client.get(RISK_URL).json()["days"]:
        for cell in day["cells"]:
            if cell["level"] == "green":
                assert cell["reasons"] == []
                continue

            assert cell["reasons"]
            for reason in cell["reasons"]:
                categorical = reason["hazard"] == "lightning" and "Tempestade" in reason["message"]
                has_number = any(character.isdigit() for character in reason["message"])
                assert has_number or categorical, reason["message"]


def test_days_parameter_limits_the_window(api: Callable[..., TestClient], today: date) -> None:
    client = api(open_meteo_handler(today))

    body = client.get(RISK_URL, params={"days": 1}).json()

    assert [day["date"] for day in body["days"]] == [today.isoformat()]


def test_experimental_mlp_is_disabled_by_default(
    api: Callable[..., TestClient], today: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_load() -> None:
        pytest.fail("A MLP opcional não deve ser carregada sem opt-in.")

    monkeypatch.setattr(model_service, "load_optional_neural_model", unexpected_load)
    client = api(open_meteo_handler(today))

    response = client.get(RISK_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["experimental_mlp"] is None
    assert all(day["experimental_mlp_score"] is None for day in body["days"])


def test_experimental_mlp_returns_separate_score_version_and_note(
    api: Callable[..., TestClient], today: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_features: list[dict] = []
    built_features: list[dict] = []
    monkeypatch.setattr(model_service, "load_optional_neural_model", lambda: object())
    monkeypatch.setattr(model_service, "NEURAL_MODEL_VERSION", "v-experimental-test")
    original_build_features = model_scoring.build_features

    def capture_build_features(farm, terrain, day) -> dict:
        features = original_build_features(farm, terrain, day)
        built_features.append(features)
        return features

    def score(_: object, features: dict) -> float:
        captured_features.append(features)
        return 0.73126

    monkeypatch.setattr(model_scoring, "build_features", capture_build_features)
    monkeypatch.setattr(model_service, "score_optional_neural_model", score)
    client = api(open_meteo_handler(today))

    response = client.get(RISK_URL, params={"include_experimental_mlp": "true", "days": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["experimental_mlp"]["available"] is True
    assert body["experimental_mlp"]["version"] == "v-experimental-test"
    assert "não é uma probabilidade calibrada" in body["experimental_mlp"]["note"]
    assert body["days"][0]["experimental_mlp_score"] == 0.7313
    assert "probability" not in body["experimental_mlp"]
    assert len(captured_features) == 1
    assert captured_features[0] == built_features[-1]


def test_enabled_and_disabled_mlp_keep_official_risk_fields_identical(
    api: Callable[..., TestClient], today: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_service, "load_optional_neural_model", lambda: object())
    monkeypatch.setattr(model_service, "score_optional_neural_model", lambda *_: 0.42)
    client = api(open_meteo_handler(today))

    recommendations_before = client.get(
        f"/api/v1/farms/{DEMO_FARM_ID}/recommendations", params={"days": 1}
    ).json()
    disabled = client.get(RISK_URL).json()
    enabled = client.get(RISK_URL, params={"include_experimental_mlp": True}).json()
    recommendations_after = client.get(
        f"/api/v1/farms/{DEMO_FARM_ID}/recommendations", params={"days": 1}
    ).json()

    assert disabled["model"] == enabled["model"]
    for disabled_day, enabled_day in zip(disabled["days"], enabled["days"], strict=True):
        assert {
            key: value for key, value in disabled_day.items() if key != "experimental_mlp_score"
        } == {key: value for key, value in enabled_day.items() if key != "experimental_mlp_score"}
        assert enabled_day["experimental_mlp_score"] == 0.42
    assert recommendations_before == recommendations_after


def test_opt_in_without_neural_artifact_reports_unavailability(
    api: Callable[..., TestClient], today: date, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(model_service, "NEURAL_MODEL_PATH", tmp_path / "missing.joblib")
    client = api(open_meteo_handler(today))

    response = client.get(RISK_URL, params={"include_experimental_mlp": True, "days": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["experimental_mlp"]["available"] is False
    assert body["experimental_mlp"]["version"] is None
    assert "artefato mlp experimental ausente" in body["experimental_mlp"]["note"].lower()
    assert body["days"][0]["experimental_mlp_score"] is None


def test_corrupted_neural_artifact_is_an_error_not_a_success_response(
    api: Callable[..., TestClient], today: date, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    corrupted_model = tmp_path / "corrupted.joblib"
    corrupted_model.write_bytes(b"not a joblib model")
    monkeypatch.setattr(model_service, "NEURAL_MODEL_PATH", corrupted_model)
    client = api(open_meteo_handler(today), raise_server_exceptions=False)

    response = client.get(RISK_URL, params={"include_experimental_mlp": True})

    assert response.status_code == 500


def test_neural_inference_failure_is_visible_as_an_error(
    api: Callable[..., TestClient], today: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_service, "load_optional_neural_model", lambda: object())

    def fail_inference(*_: object) -> float:
        raise ValueError("inferência MLP falhou")

    monkeypatch.setattr(model_service, "score_optional_neural_model", fail_inference)
    client = api(open_meteo_handler(today), raise_server_exceptions=False)

    response = client.get(RISK_URL, params={"include_experimental_mlp": True})

    assert response.status_code == 500


@pytest.mark.parametrize("days", [0, 8, -1])
def test_days_outside_one_to_seven_returns_422(
    api: Callable[..., TestClient], today: date, days: int
) -> None:
    client = api(open_meteo_handler(today))

    assert client.get(RISK_URL, params={"days": days}).status_code == 422


def test_unknown_scenario_returns_422(api: Callable[..., TestClient], today: date) -> None:
    """regras-de-risco §10: só os cenários implementados são aceitos."""
    client = api(open_meteo_handler(today))

    assert client.get(RISK_URL, params={"scenario": "chuva_forte"}).status_code == 422


def test_heavy_rain_scenario_saturates_day_plus_two(
    api: Callable[..., TestClient], today: date
) -> None:
    """Critério de aceite: com `heavy_rain` o dia +2 fica encharcado e `scenario` vem preenchido."""
    client = api(open_meteo_handler(today))

    body = client.get(RISK_URL, params={"scenario": "heavy_rain"}).json()

    assert body["scenario"] == "heavy_rain"
    day_plus_two = body["days"][2]
    assert day_plus_two["date"] == (today + timedelta(days=2)).isoformat()
    assert day_plus_two["rain_72h_mm"] >= 30.0
    assert day_plus_two["soil_state"] == "saturated"
    assert day_plus_two["tilt_limit_deg"] == SATURATED_TILT_LIMIT_DEG


def test_heavy_rain_scenario_turns_the_steep_cells_red_in_carmo_de_minas(
    api: Callable[..., TestClient], today: date
) -> None:
    """Critério de aceite: com `rain_72h ≥ 30 mm`, as células com inclinação ≥ 10° ficam 🔴."""
    client = api(open_meteo_handler(today))

    risk = client.get(RISK_URL, params={"scenario": "heavy_rain"}).json()["days"][2]
    terrain = client.get(f"/api/v1/farms/{DEMO_FARM_ID}/terrain").json()

    steep = {
        (cell["row"], cell["col"])
        for cell in terrain["cells"]
        if cell["slope_deg"] >= SATURATED_TILT_LIMIT_DEG
    }
    red = {(cell["row"], cell["col"]) for cell in risk["cells"] if cell["level"] == "red"}

    assert steep, "A fixture de Carmo de Minas precisa ter encostas de 10° ou mais."
    assert steep <= red
    assert risk["worst_level"] == "red"
    assert risk["pct_levels"]["red"] > 0.0


def test_the_scenario_never_applies_by_default(api: Callable[..., TestClient], today: date) -> None:
    """regras-de-risco §10: sem `scenario=`, a resposta é a previsão real."""
    client = api(open_meteo_handler(today))

    real = client.get(RISK_URL).json()["days"][2]
    simulated = client.get(RISK_URL, params={"scenario": "heavy_rain"}).json()["days"][2]

    assert real["rain_mm"] < simulated["rain_mm"]


def test_unknown_farm_returns_404(
    api: Callable[..., TestClient], recorder: RequestRecorder, today: date
) -> None:
    client = api(open_meteo_handler(today))

    response = client.get(f"/api/v1/farms/{UNKNOWN_FARM_ID}/risk")

    assert response.status_code == 404
    assert response.json()["detail"] == "Fazenda não encontrada"
    assert recorder.count == 0


def test_open_meteo_down_returns_503(api: Callable[..., TestClient]) -> None:
    client = api(failing_handler)

    response = client.get(RISK_URL)

    assert response.status_code == 503
    assert response.json()["detail"] == "Serviço de clima indisponível"


def test_the_forecast_reuses_the_cached_terrain_and_weather(
    api: Callable[..., TestClient], recorder: RequestRecorder, today: date
) -> None:
    """Uma chamada de elevação e uma de previsão; a segunda requisição não vai à rede."""
    client = api(open_meteo_handler(today))

    first = client.get(RISK_URL)
    second = client.get(RISK_URL)

    assert first.status_code == 200
    assert second.status_code == 200
    assert recorder.count == 2


def test_date_of_the_first_day_is_today_in_sao_paulo(
    api: Callable[..., TestClient], today: date
) -> None:
    client = api(open_meteo_handler(today))

    body = client.get(RISK_URL).json()

    assert date.fromisoformat(body["days"][0]["date"]) == today
    assert body["generated_at"].endswith(("-03:00", "-02:00"))


def test_the_day_limit_follows_the_most_fragile_machine(
    api: Callable[..., TestClient], today: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    """regras-de-risco §4: o mapa da fazenda usa o **menor** `base_tilt_limit_deg` dela.

    Este teste cobre a **ligação** entre a rota e `farm_reference_tilt_limit_deg`: como todo
    equipamento do `farms.json` vale 15,0°, voltar a usar o `reference_tilt_limit_deg` da fazenda
    passaria despercebido sem uma máquina mais frágil no catálogo.

    A segunda asserção não depende da chuva da fixture: o limite da máquina de 12° é menor que o
    da de 15° nos três estados do solo (12 < 15 · 10 < 12,5 · 8 < 10).
    """
    farm = farms_service.get_farm(DEMO_FARM_ID)
    assert farm is not None
    fragile = farm.model_copy(
        update={
            "devices": [
                farm.devices[0].model_copy(update={"base_tilt_limit_deg": FRAGILE_TILT_LIMIT_DEG})
            ]
        }
    )
    monkeypatch.setattr(
        farms_service, "get_farm", lambda farm_id: fragile if farm_id == DEMO_FARM_ID else None
    )
    client = api(open_meteo_handler(today))

    body = client.get(RISK_URL).json()

    assert body["days"]
    for day in body["days"]:
        soil = SoilState(day["soil_state"])
        assert day["tilt_limit_deg"] == tilt_limit(FRAGILE_TILT_LIMIT_DEG, soil)
        assert day["tilt_limit_deg"] < tilt_limit(DEMO_TILT_LIMIT_DEG, soil)

"""Testes dos relatórios e tendências (W12).

Os 1,5 milhão de apólices reais vivem no `agrishield.db` local, que não vai para o Git e não
existe na CI. Por isso estes testes montam um **PSR em miniatura** em memória, com as mesmas
colunas e a mesma categoria `sem_sinistro` da D1: o que se verifica é a agregação, não o volume.
"""

import csv
import io
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.db import get_session
from app.main import app
from app.models import Policy
from app.repositories.devices import save_event, save_telemetry
from app.schemas.mqtt import EventMessage, TelemetryMessage
from app.services.reports import (
    NO_CLAIM_CATEGORY,
    TELEMETRY_INTERVAL_S,
    crop_summary,
    equipment_trend,
    region_summary,
)
from app.services.terrain import clear_terrain_cache
from tests.conftest import FIXTURES_DIR, load_fixture

DEVICE_ID = "tractor-01"
FARM_ID = "cafe-carmo-de-minas"
UNKNOWN_DEVICE_ID = "trator-fantasma"

NOW = datetime(2026, 9, 20, 12, 0)

# O valor que a **D1 grava** para apólice sem sinistro. Escrito por extenso: se o serviço mudar a
# constante dele, este teste tem que acusar, não acompanhar.
NO_CLAIM_IN_THE_DATABASE = "sem_sinistro"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    clear_terrain_cache()
    yield
    clear_terrain_cache()
    app.dependency_overrides.clear()


@pytest.fixture
def api(session: Session, make_client: Callable[..., OpenMeteoClient]) -> Callable[..., TestClient]:
    def factory(failing: bool = False) -> TestClient:
        def handler(_: httpx.Request) -> httpx.Response:
            if failing:
                return httpx.Response(500, text="erro na origem")
            path = FIXTURES_DIR / "terrain_elevation_carmo.json"
            return httpx.Response(200, json=json.loads(path.read_text(encoding="utf-8")))

        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_open_meteo_client] = lambda: make_client(handler)
        return TestClient(app)

    return factory


def add_policy(
    session: Session,
    proposal_id: str,
    state: str = "MG",
    municipality: str = "Carmo de Minas",
    crop: str = "Café",
    year: int = 2024,
    event_category: str = NO_CLAIM_IN_THE_DATABASE,
    indemnity: float | None = None,
) -> None:
    session.add(
        Policy(
            proposal_id=proposal_id,
            state=state,
            municipality=municipality,
            crop=crop,
            policy_year=year,
            event_category=event_category,
            indemnity_value=indemnity,
            lat=-22.12,
            lon=-45.13,
            coordinate_source="decimal",
        )
    )
    session.commit()


def seed_psr(session: Session) -> None:
    """PSR em miniatura: 10 apólices em MG e 4 no RS, com sinistros conhecidos."""
    for index in range(6):
        add_policy(session, f"mg-ok-{index}")
    add_policy(session, "mg-granizo-1", event_category="granizo", indemnity=10_000.0)
    add_policy(session, "mg-granizo-2", event_category="granizo", indemnity=20_000.0)
    add_policy(
        session, "mg-seca-1", municipality="Patrocínio", event_category="seca", indemnity=30_000.0
    )
    add_policy(session, "mg-antiga", year=2019, event_category="geada", indemnity=5_000.0)

    for index in range(2):
        add_policy(
            session, f"rs-uva-ok-{index}", state="RS", municipality="Bento Gonçalves", crop="Uva"
        )
    add_policy(
        session,
        "rs-uva-granizo",
        state="RS",
        municipality="Bento Gonçalves",
        crop="Uva",
        event_category="granizo",
        indemnity=50_000.0,
    )
    add_policy(
        session,
        "rs-trigo-geada",
        state="RS",
        municipality="Farroupilha",
        crop="Trigo",
        event_category="geada",
        indemnity=15_000.0,
    )


def seed_telemetry(session: Session, alert_levels: list[str], when: datetime) -> None:
    payload = load_fixture("telemetry_sample.json")
    for index, level in enumerate(alert_levels):
        save_telemetry(
            session,
            TelemetryMessage.model_validate(payload | {"seq": index, "alert_level": level}),
            received_at=when,
        )


# --- Equipamento --------------------------------------------------------------------------------


def test_the_equipment_report_counts_hours_and_time_above_the_limit(session: Session) -> None:
    seed_telemetry(session, ["green", "green", "red", "rollover"], NOW - timedelta(hours=1))

    report = equipment_trend(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert report.readings == 4
    # 4 leituras a cada 5 s = 20 s
    assert report.operating_hours == round(4 * TELEMETRY_INTERVAL_S / 3600, 2)
    assert report.pct_time_above_limit == 50.0
    assert report.device_id == DEVICE_ID
    assert report.farm_id == FARM_ID


def test_the_equipment_report_counts_alerts_and_rollovers(session: Session) -> None:
    rollover = load_fixture("rollover_event.json")
    save_event(session, EventMessage.model_validate(rollover), received_at=NOW - timedelta(hours=2))
    save_event(
        session,
        EventMessage.model_validate(
            {
                "device_id": DEVICE_ID,
                "event_id": "tractor-01-1789500000-2",
                "ts": 1789500000,
                "type": "tilt_alert",
                "roll_deg": 13,
                "tilt_limit_deg": 10,
            }
        ),
        received_at=NOW - timedelta(hours=1),
    )
    save_event(
        session,
        EventMessage.model_validate(
            {
                "device_id": DEVICE_ID,
                "event_id": "tractor-01-1789500000-3",
                "ts": 1789500000,
                "type": "limit_applied",
                "tilt_limit_deg": 10,
            }
        ),
        received_at=NOW - timedelta(hours=1),
    )

    report = equipment_trend(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    # `limit_applied` é confirmação de recebimento, não alerta.
    assert report.alerts == 2
    assert report.rollovers == 1


def test_the_equipment_trend_has_one_line_per_day(session: Session) -> None:
    seed_telemetry(session, ["green"], NOW - timedelta(days=2))
    seed_telemetry(session, ["red", "green"], NOW - timedelta(days=1))

    report = equipment_trend(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert [day.date for day in report.trend] == [
        (NOW - timedelta(days=2)).date(),
        (NOW - timedelta(days=1)).date(),
    ]
    assert report.trend[-1].pct_time_above_limit == 50.0
    assert report.trend[0].max_roll_deg == 12.4


def test_the_window_excludes_what_is_older(session: Session) -> None:
    seed_telemetry(session, ["green"], NOW - timedelta(days=30))
    seed_telemetry(session, ["green"], NOW - timedelta(days=1))

    assert equipment_trend(session, DEVICE_ID, FARM_ID, days=7, now=NOW).readings == 1


def test_a_period_without_data_is_not_an_error(session: Session) -> None:
    """Critério de aceite: período sem dados → mensagem clara, não erro."""
    report = equipment_trend(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert report.readings == 0
    assert report.operating_hours == 0.0
    assert report.pct_time_above_limit == 0.0
    assert report.trend == []


def test_the_equipment_report_says_what_it_is_for(session: Session) -> None:
    """Critério de aceite: cada relatório diz para quem serve e que decisão apoia."""
    report = equipment_trend(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert "gestor de frota" in report.purpose
    assert "apoia a decisão" in report.purpose


# --- Região -------------------------------------------------------------------------------------


def test_the_region_report_aggregates_the_real_columns(session: Session) -> None:
    seed_psr(session)

    report = region_summary(session, state="MG")

    assert report.policies == 10
    assert report.claims == 4
    assert report.claim_rate_pct == 40.0
    assert report.indemnity_total == 65_000.0
    assert report.indemnity_mean == 16_250.0


def test_the_region_report_filters_by_harvest_year(session: Session) -> None:
    seed_psr(session)

    report = region_summary(session, state="MG", from_year=2024, to_year=2024)

    assert report.policies == 9
    assert report.claims == 3


def test_the_region_report_ranks_the_causes(session: Session) -> None:
    seed_psr(session)

    report = region_summary(session, state="MG")

    assert [event.event_category for event in report.top_events][:2] == ["granizo", "seca"]
    assert report.top_events[0].claims == 2
    assert report.top_events[0].pct_of_claims == 50.0
    # `sem_sinistro` não é causa de nada e fica de fora do ranking.
    assert all(event.event_category != NO_CLAIM_IN_THE_DATABASE for event in report.top_events)


def test_the_region_report_ranks_the_municipalities(session: Session) -> None:
    seed_psr(session)

    report = region_summary(session, state="MG")

    top = report.top_municipalities[0]
    assert top.municipality == "Carmo de Minas"
    assert top.claims == 3
    assert top.policies == 9


def test_the_region_report_accepts_a_state_without_data(session: Session) -> None:
    seed_psr(session)

    report = region_summary(session, state="AC")

    assert report.policies == 0
    assert report.claims == 0
    assert report.claim_rate_pct == 0.0
    assert report.indemnity_mean == 0.0
    assert report.top_events == []


def test_the_region_report_says_what_it_is_for(session: Session) -> None:
    assert "analista da seguradora" in region_summary(session, state="MG").purpose


# --- Cultura ------------------------------------------------------------------------------------


def test_the_crop_report_ranks_by_claim_rate(session: Session) -> None:
    seed_psr(session)

    report = crop_summary(session)

    rates = [crop.claim_rate_pct for crop in report.crops]
    assert rates == sorted(rates, reverse=True)
    by_crop = {crop.crop: crop for crop in report.crops}
    assert by_crop["Trigo"].claim_rate_pct == 100.0
    assert by_crop["Uva"].claim_rate_pct == pytest.approx(33.3, abs=0.1)
    assert by_crop["Café"].claim_rate_pct == 40.0


def test_the_crop_report_names_the_most_frequent_cause(session: Session) -> None:
    seed_psr(session)

    by_crop = {crop.crop: crop for crop in crop_summary(session).crops}

    assert by_crop["Uva"].top_event == "granizo"
    assert by_crop["Trigo"].top_event == "geada"


def test_the_crop_report_can_be_filtered_by_state(session: Session) -> None:
    seed_psr(session)

    report = crop_summary(session, state="RS")

    assert {crop.crop for crop in report.crops} == {"Uva", "Trigo"}
    assert report.policies == 4


def test_a_crop_without_claims_has_no_top_event(session: Session) -> None:
    add_policy(session, "so-sem-sinistro", crop="Soja")

    by_crop = {crop.crop: crop for crop in crop_summary(session).crops}

    assert by_crop["Soja"].claims == 0
    assert by_crop["Soja"].top_event is None


def test_the_crop_report_says_what_it_is_for(session: Session) -> None:
    purpose = crop_summary(session).purpose
    assert "subscrição" in purpose
    assert "PSR/SISSER" in purpose
    assert "não representa o tipo de operação" in purpose


# --- LGPD ---------------------------------------------------------------------------------------


def test_no_report_ever_exposes_the_proposal_id(
    api: Callable[..., TestClient], session: Session
) -> None:
    """O `proposal_id` é chave de junção de volta ao CSV público, que tem o nome do segurado.

    Cobre **as duas saídas**: o JSON dos resumos e o texto das exportações. As rotas `.csv` têm
    gerador de linhas próprio, que é código diferente — a garantia de LGPD (ADR-011) precisa
    valer nos dois caminhos.
    """
    seed_psr(session)
    client = api()

    payloads = [
        region_summary(session, state="MG").model_dump_json(),
        crop_summary(session).model_dump_json(),
        equipment_trend(session, DEVICE_ID, FARM_ID, now=NOW).model_dump_json(),
        client.get("/api/v1/reports/region.csv", params={"state": "MG"}).text,
        client.get("/api/v1/reports/crop.csv").text,
        client.get(f"/api/v1/reports/equipment/{DEVICE_ID}.csv").text,
    ]

    for payload in payloads:
        assert "proposal_id" not in payload
        assert "mg-granizo-1" not in payload
        assert "rs-uva-granizo" not in payload


# --- Rotas --------------------------------------------------------------------------------------


def test_the_equipment_route_answers(api: Callable[..., TestClient], session: Session) -> None:
    seed_telemetry(session, ["green", "red"], datetime.now(UTC).replace(tzinfo=None))

    response = api().get(f"/api/v1/reports/equipment/{DEVICE_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == DEVICE_ID
    assert body["readings"] == 2
    assert body["purpose"]


def test_an_unknown_device_returns_404(api: Callable[..., TestClient]) -> None:
    response = api().get(f"/api/v1/reports/equipment/{UNKNOWN_DEVICE_ID}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Equipamento não encontrado"


@pytest.mark.parametrize("days", [0, 91, -1])
def test_an_invalid_window_returns_422(api: Callable[..., TestClient], days: int) -> None:
    assert (
        api().get(f"/api/v1/reports/equipment/{DEVICE_ID}", params={"days": days}).status_code
        == 422
    )


def test_the_region_route_brings_the_demo_farm_profile(
    api: Callable[..., TestClient], session: Session
) -> None:
    """A joia do relatório: sinistro real da UF ao lado do relevo da fazenda (W8 + W12)."""
    seed_psr(session)

    body = api().get("/api/v1/reports/region", params={"state": "MG"}).json()

    assert body["policies"] == 10
    assert body["demo_farms"]
    assert body["demo_farms"][0]["farm_id"] == FARM_ID
    assert body["demo_farms"][0]["risk_class"] in {"A", "B", "C"}


def test_the_region_route_survives_open_meteo_being_down(
    api: Callable[..., TestClient], session: Session
) -> None:
    """O valor está nos sinistros reais: uma API externa fora não pode esconder o relatório."""
    seed_psr(session)

    response = api(failing=True).get("/api/v1/reports/region", params={"state": "MG"})

    assert response.status_code == 200
    assert response.json()["policies"] == 10
    assert response.json()["demo_farms"] == []


def test_the_crop_route_answers(api: Callable[..., TestClient], session: Session) -> None:
    seed_psr(session)

    body = api().get("/api/v1/reports/crop").json()

    assert body["policies"] == 14
    assert body["crops"]


@pytest.mark.parametrize("params", [{"state": "M"}, {"state": "MGX"}, {"from_year": 1800}])
def test_invalid_filters_return_422(api: Callable[..., TestClient], params: dict) -> None:
    assert api().get("/api/v1/reports/crop", params=params).status_code == 422


# --- Exportação CSV -------------------------------------------------------------------------------


def parse_csv(text: str) -> list[dict[str, str]]:
    assert text.startswith("﻿"), "o Excel precisa do BOM para não estragar os acentos"
    return list(csv.DictReader(io.StringIO(text[1:]), delimiter=";"))


def test_the_region_csv_opens_in_excel(api: Callable[..., TestClient], session: Session) -> None:
    """Critério de aceite: UTF-8 com BOM e vírgula decimal."""
    seed_psr(session)

    response = api().get("/api/v1/reports/region.csv", params={"state": "MG"})

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    rows = parse_csv(response.text)
    assert rows[0]["municipio"] == "Carmo de Minas"
    # Vírgula decimal, e por isso o separador é `;`.
    assert "," in rows[0]["taxa_de_sinistro_pct"]


def test_the_csv_keeps_the_accents(api: Callable[..., TestClient], session: Session) -> None:
    add_policy(session, "acentos", municipality="Virgínia", event_category="granizo", indemnity=1.0)

    rows = parse_csv(api().get("/api/v1/reports/region.csv", params={"state": "MG"}).text)

    assert rows[0]["municipio"] == "Virgínia"


def test_the_crop_csv_lists_the_crops(api: Callable[..., TestClient], session: Session) -> None:
    seed_psr(session)

    rows = parse_csv(api().get("/api/v1/reports/crop.csv").text)

    assert {row["cultura"] for row in rows} == {"Café", "Uva", "Trigo"}


def test_the_equipment_csv_has_one_line_per_day(
    api: Callable[..., TestClient], session: Session
) -> None:
    seed_telemetry(session, ["green", "red"], datetime.now(UTC).replace(tzinfo=None))

    rows = parse_csv(api().get(f"/api/v1/reports/equipment/{DEVICE_ID}.csv").text)

    assert len(rows) == 1
    assert rows[0]["leituras"] == "2"


def test_an_empty_csv_explains_itself(api: Callable[..., TestClient]) -> None:
    """Arquivo de zero byte confunde; uma linha dizendo que não há dado, não."""
    response = api().get("/api/v1/reports/region.csv", params={"state": "AC"})

    assert response.status_code == 200
    assert "sem dados no periodo selecionado" in response.text


def test_the_service_uses_the_category_that_the_ingestion_writes() -> None:
    """Amarra o serviço ao contrato da D1: mudar um lado só zeraria toda taxa de sinistro."""
    assert NO_CLAIM_CATEGORY == NO_CLAIM_IN_THE_DATABASE

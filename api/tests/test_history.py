"""Testes do histórico do equipamento — a prévia do Passaporte Digital (W11).

Nenhuma tabela nova: tudo sai de `telemetry` e `device_event`, que a I3 já grava. Por isso o
teste mais importante deste arquivo não é sobre o histórico em si, e sim sobre ele **concordar
com o relatório do equipamento** (W12), que lê as mesmas linhas para responder outra pergunta.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.repositories.devices import save_event, save_telemetry
from app.schemas.mqtt import EventMessage, TelemetryMessage
from app.services.history import (
    DEFAULT_HISTORY_DAYS,
    MAX_HISTORY_DAYS,
    MAX_TIMELINE_EVENTS,
    count_events,
    device_history,
    summarize,
)
from app.services.reports import TELEMETRY_INTERVAL_S, equipment_trend, operating_hours
from tests.conftest import load_fixture

DEVICE_ID = "tractor-01"
FARM_ID = "cafe-carmo-de-minas"
UNKNOWN_DEVICE_ID = "trator-fantasma"
HISTORY_URL = f"/api/v1/devices/{DEVICE_ID}/history"

NOW = datetime(2026, 9, 20, 12, 0)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_telemetry(
    session: Session, when: datetime, alert_level: str = "green", **changes: object
) -> None:
    payload = load_fixture("telemetry_sample.json") | {"alert_level": alert_level} | changes
    save_telemetry(session, TelemetryMessage.model_validate(payload), received_at=when)


def add_event(session: Session, when: datetime, event_type: str, suffix: str) -> None:
    save_event(
        session,
        EventMessage.model_validate(
            {
                "device_id": DEVICE_ID,
                "event_id": f"tractor-01-{suffix}",
                "ts": 1789500000,
                "type": event_type,
                "roll_deg": 13,
                "tilt_limit_deg": 10,
            }
        ),
        received_at=when,
    )


def seed(session: Session) -> None:
    """Duas horas de operação, com um trecho acima do limite e quatro tipos de evento."""
    for index in range(10):
        add_telemetry(
            session,
            NOW - timedelta(hours=2, minutes=index),
            alert_level="red" if index < 3 else "green",
            roll_deg=20.0 if index == 0 else 5.0,
            pitch_deg=-8.0 if index == 1 else -2.0,
        )
    add_event(session, NOW - timedelta(hours=2), "tilt_alert", "a1")
    add_event(session, NOW - timedelta(hours=1), "tilt_alert", "a2")
    add_event(session, NOW - timedelta(minutes=50), "rollover", "r1")
    add_event(session, NOW - timedelta(minutes=40), "incident_report", "i1")
    add_event(session, NOW - timedelta(minutes=30), "limit_applied", "l1")


# --- O resumo ------------------------------------------------------------------------------------


def test_the_summary_counts_what_was_recorded(session: Session) -> None:
    """Critério de aceite: os números batem com os dados gravados."""
    seed(session)

    history, _ = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert history.readings == 10
    assert (
        history.operating_hours == operating_hours(10) == round(10 * TELEMETRY_INTERVAL_S / 3600, 2)
    )
    assert history.pct_time_above_limit == 30.0
    assert history.max_roll_deg == 20.0
    assert history.max_pitch_deg == 8.0  # módulo: −8° é o maior em valor absoluto
    assert history.device_id == DEVICE_ID
    assert history.farm_id == FARM_ID


def test_each_event_type_is_counted_apart(session: Session) -> None:
    """O Passaporte precisa distinguir "quase aconteceu" de "aconteceu"."""
    seed(session)

    history, _ = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert history.alerts == 2
    assert history.rollovers == 1
    assert history.incident_reports == 1
    assert history.limits_applied == 1


def test_the_window_bounds_the_history(session: Session) -> None:
    add_telemetry(session, NOW - timedelta(days=30))
    add_telemetry(session, NOW - timedelta(days=1))

    history, _ = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert history.readings == 1


def test_the_first_and_last_reading_frame_the_period(session: Session) -> None:
    add_telemetry(session, NOW - timedelta(hours=5))
    add_telemetry(session, NOW - timedelta(hours=1))

    history, _ = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert history.first_seen_at == NOW - timedelta(hours=5)
    assert history.last_seen_at == NOW - timedelta(hours=1)


def test_a_period_without_data_is_not_an_error(session: Session) -> None:
    """Critério de aceite: sem dados → resposta amigável, não erro."""
    history, events = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert history.readings == 0
    assert history.operating_hours == 0.0
    assert history.pct_time_above_limit == 0.0
    assert history.max_roll_deg is None
    assert history.first_seen_at is None
    assert events == []


def test_the_history_says_what_it_is_not_yet(session: Session) -> None:
    """A tela não pode prometer o Passaporte completo; o texto vem da API."""
    history, _ = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert "Prévia do Passaporte Digital" in history.roadmap_note
    assert "roadmap" in history.roadmap_note


def test_summarize_is_pure(session: Session) -> None:
    """A função do resumo não toca banco nem relógio: dá para testá-la com listas."""
    history = summarize([], {}, DEVICE_ID, FARM_ID, days=3)

    assert history.days == 3
    assert history.readings == 0


def test_count_events_is_the_pure_counterpart_of_the_aggregation(session: Session) -> None:
    """A contagem em memória e a do banco precisam dar o mesmo resultado."""
    seed(session)
    _, events = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert count_events(events) == {
        "tilt_alert": 2,
        "rollover": 1,
        "incident_report": 1,
        "limit_applied": 1,
    }


# --- A linha do tempo -----------------------------------------------------------------------------


def test_the_timeline_comes_newest_first(session: Session) -> None:
    seed(session)

    _, events = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    received = [event.received_at for event in events]
    assert received == sorted(received, reverse=True)
    assert events[0].type == "limit_applied"


def test_the_timeline_is_bounded(session: Session) -> None:
    for index in range(MAX_TIMELINE_EVENTS + 10):
        add_event(session, NOW - timedelta(minutes=index), "tilt_alert", f"t{index}")

    _, events = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert len(events) == MAX_TIMELINE_EVENTS


def test_the_counters_ignore_the_timeline_ceiling(session: Session) -> None:
    """O teto é da linha do tempo, não da contagem.

    Não é hipotético: a API republica o `config` de hora em hora, então uma janela de 7 dias já
    passa de 150 `limit_applied`. Se os contadores saírem da lista truncada, eles param em 100 em
    silêncio — e o histórico passa a mentir justamente sobre a máquina que mais trabalhou.
    """
    limits = MAX_TIMELINE_EVENTS + 68  # 168 = uma republicação por hora em 7 dias
    alerts = 7
    for index in range(limits):
        add_event(session, NOW - timedelta(minutes=index), "limit_applied", f"l{index}")
    for index in range(alerts):
        add_event(session, NOW - timedelta(minutes=index), "tilt_alert", f"a{index}")

    history, events = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert len(events) == MAX_TIMELINE_EVENTS
    assert history.limits_applied == limits
    assert history.alerts == alerts
    assert history.rollovers == 0
    assert history.incident_reports == 0


def test_the_counters_still_agree_with_the_report_beyond_the_ceiling(session: Session) -> None:
    """A concordância com o W12 não pode depender de a janela ser pequena.

    O relatório do equipamento conta os eventos sem teto; o histórico precisa contar igual mesmo
    quando a linha do tempo é cortada.
    """
    seed(session)
    for index in range(MAX_TIMELINE_EVENTS + 20):
        add_event(session, NOW - timedelta(minutes=index), "limit_applied", f"l{index}")
        add_event(session, NOW - timedelta(minutes=index), "tilt_alert", f"x{index}")

    history, events = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)
    report = equipment_trend(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert len(events) == MAX_TIMELINE_EVENTS
    assert history.alerts == MAX_TIMELINE_EVENTS + 20 + 2
    assert history.limits_applied == MAX_TIMELINE_EVENTS + 20
    assert report.alerts == history.alerts + history.rollovers
    assert report.rollovers == history.rollovers


# --- Concordância com o relatório do equipamento (W12) -------------------------------------------


def test_the_history_and_the_report_agree(session: Session) -> None:
    """Duas respostas que contam a mesma coisa não podem contar diferente.

    O W11 (acumulado, "o que aconteceu com esta máquina") e o W12 (série diária, "está
    piorando?") leem as mesmas linhas. Se divergirem em leituras, horas ou tempo acima do limite,
    é porque uma das duas tem fórmula própria — e é isso que este teste impede.
    """
    seed(session)

    history, _ = device_history(session, DEVICE_ID, FARM_ID, days=7, now=NOW)
    report = equipment_trend(session, DEVICE_ID, FARM_ID, days=7, now=NOW)

    assert history.readings == report.readings
    assert history.operating_hours == report.operating_hours
    assert history.pct_time_above_limit == report.pct_time_above_limit
    # E os alertas do relatório são a soma dos dois tipos que o histórico conta separados.
    assert report.alerts == history.alerts + history.rollovers
    assert report.rollovers == history.rollovers


# --- A rota ---------------------------------------------------------------------------------------


def test_the_route_answers_with_the_summary_and_the_timeline(
    client: TestClient, session: Session
) -> None:
    seed(session)

    response = client.get(HISTORY_URL, params={"days": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == DEVICE_ID
    assert body["readings"] == 10
    assert body["rollovers"] == 1
    assert len(body["timeline"]) == 5
    assert body["roadmap_note"]


def test_the_timeline_uses_the_same_event_shape_as_the_live_panel(
    client: TestClient, session: Session
) -> None:
    """O front reaproveita o componente de evento do W5; a forma tem de ser a mesma."""
    save_event(
        session,
        EventMessage.model_validate(load_fixture("rollover_event.json")),
        received_at=NOW,
    )

    body = client.get(HISTORY_URL).json()
    from_events = client.get(f"/api/v1/devices/{DEVICE_ID}/events").json()

    assert body["timeline"][0].keys() == from_events[0].keys()
    assert body["timeline"][0]["context"]["fields"]


def test_the_default_window_is_a_week(client: TestClient, session: Session) -> None:
    body = client.get(HISTORY_URL).json()

    assert body["days"] == DEFAULT_HISTORY_DAYS == 7


def test_an_unknown_device_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/devices/{UNKNOWN_DEVICE_ID}/history")

    assert response.status_code == 404
    assert response.json()["detail"] == "Equipamento não encontrado"


@pytest.mark.parametrize("days", [0, -1, MAX_HISTORY_DAYS + 1])
def test_an_invalid_window_returns_422(client: TestClient, days: int) -> None:
    assert client.get(HISTORY_URL, params={"days": days}).status_code == 422


def test_an_empty_history_answers_200(client: TestClient) -> None:
    """Máquina que nunca publicou não é erro: é uma máquina sem histórico ainda."""
    response = client.get(HISTORY_URL)

    assert response.status_code == 200
    assert response.json()["readings"] == 0
    assert response.json()["timeline"] == []

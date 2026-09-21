"""Testes das rotas do painel ao vivo (W5): status, telemetria e eventos.

Banco em memória populado pelo repositório (I3), sem rede e sem broker.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.repositories.devices import save_event, save_telemetry, set_status
from app.schemas.mqtt import EventMessage, StatusMessage, TelemetryMessage
from app.services.devices import MAX_SERIES_POINTS, OFFLINE_AFTER_S, downsample, resolve_state
from tests.conftest import load_fixture

DEVICE_ID = "tractor-01"
UNKNOWN_DEVICE_ID = "trator-fantasma"

STATUS_URL = f"/api/v1/devices/{DEVICE_ID}/status"
LATEST_URL = f"/api/v1/devices/{DEVICE_ID}/telemetry/latest"
SERIES_URL = f"/api/v1/devices/{DEVICE_ID}/telemetry"
EVENTS_URL = f"/api/v1/devices/{DEVICE_ID}/events"


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


def telemetry_message(**changes: object) -> TelemetryMessage:
    return TelemetryMessage.model_validate(load_fixture("telemetry_sample.json") | changes)


def event_message(**changes: object) -> EventMessage:
    return EventMessage.model_validate(load_fixture("rollover_event.json") | changes)


def seed_telemetry(session: Session, seconds_ago: list[float], **changes: object) -> datetime:
    """Grava uma leitura para cada idade pedida e devolve o "agora" usado."""
    now = datetime.now(UTC).replace(tzinfo=None)
    for index, age in enumerate(seconds_ago):
        save_telemetry(
            session,
            telemetry_message(seq=index, **changes),
            received_at=now - timedelta(seconds=age),
        )
    return now


# --- Funções puras ------------------------------------------------------------------------------


def test_resolve_state_without_any_telemetry_is_offline() -> None:
    """Anunciou `online` mas nunca mediu nada: ainda não é um equipamento vivo."""
    now = datetime(2026, 9, 19, 15, 0)

    state, last_seen, silence = resolve_state(None, None, now)

    assert state.value == "offline"
    assert last_seen is None
    assert silence is None


def test_downsample_keeps_short_series_untouched() -> None:
    rows = list(range(10))

    assert downsample(rows, max_points=300) == rows


def test_downsample_keeps_the_first_and_the_last_point() -> None:
    rows = list(range(1000))

    sampled = downsample(rows, max_points=MAX_SERIES_POINTS)

    assert len(sampled) == MAX_SERIES_POINTS
    assert sampled[0] == 0
    assert sampled[-1] == 999
    assert sampled == sorted(sampled)


def test_downsample_rejects_an_empty_budget() -> None:
    with pytest.raises(ValueError, match="pelo menos 1 ponto"):
        downsample([1, 2, 3], max_points=0)


# --- GET /status --------------------------------------------------------------------------------


def test_status_is_online_with_recent_telemetry(client: TestClient, session: Session) -> None:
    set_status(session, StatusMessage(device_id=DEVICE_ID, state="online"))
    seed_telemetry(session, [2.0])

    body = client.get(STATUS_URL).json()

    assert body["state"] == "online"
    assert body["reported_state"] == "online"
    assert body["seconds_since_last_telemetry"] < OFFLINE_AFTER_S


def test_status_is_offline_after_the_silence_limit(client: TestClient, session: Session) -> None:
    """W5: sem telemetria por mais de 20 s, o equipamento cai, mesmo tendo anunciado `online`."""
    set_status(session, StatusMessage(device_id=DEVICE_ID, state="online"))
    seed_telemetry(session, [OFFLINE_AFTER_S + 5])

    body = client.get(STATUS_URL).json()

    assert body["state"] == "offline"
    assert body["reported_state"] == "online"
    assert body["seconds_since_last_telemetry"] > OFFLINE_AFTER_S


def test_status_is_offline_when_the_lwt_says_so(client: TestClient, session: Session) -> None:
    """W5: o LWT derruba o equipamento mesmo com telemetria recente no banco."""
    seed_telemetry(session, [1.0])
    set_status(session, StatusMessage(device_id=DEVICE_ID, state="offline"))

    body = client.get(STATUS_URL).json()

    assert body["state"] == "offline"
    assert body["reported_state"] == "offline"


def test_status_of_a_device_that_never_spoke(client: TestClient) -> None:
    """A tela mostra "Aguardando o equipamento conectar…" — não é erro."""
    body = client.get(STATUS_URL).json()

    assert body["state"] == "offline"
    assert body["reported_state"] is None
    assert body["last_seen_at"] is None
    assert body["seconds_since_last_telemetry"] is None


def test_status_of_an_unknown_device_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/devices/{UNKNOWN_DEVICE_ID}/status")

    assert response.status_code == 404
    assert response.json()["detail"] == "Equipamento não encontrado"


# --- GET /telemetry/latest ----------------------------------------------------------------------


def test_latest_telemetry_returns_the_newest_reading(client: TestClient, session: Session) -> None:
    seed_telemetry(session, [30.0, 1.0, 15.0])

    body = client.get(LATEST_URL).json()

    assert body["device_id"] == DEVICE_ID
    assert body["seq"] == 1
    assert body["tilt_limit_deg"] == 10.0
    assert body["alert_level"] == "red"


def test_latest_telemetry_without_data_returns_404(client: TestClient) -> None:
    response = client.get(LATEST_URL)

    assert response.status_code == 404
    assert response.json()["detail"] == "Sem telemetria ainda"


def test_latest_telemetry_of_an_unknown_device_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/devices/{UNKNOWN_DEVICE_ID}/telemetry/latest")

    assert response.json()["detail"] == "Equipamento não encontrado"


# --- GET /telemetry -----------------------------------------------------------------------------


def test_the_series_comes_in_chronological_order(client: TestClient, session: Session) -> None:
    seed_telemetry(session, [500.0, 60.0, 5.0, 120.0])

    body = client.get(SERIES_URL, params={"minutes": 10}).json()

    timestamps = [point["received_at"] for point in body["points"]]
    assert timestamps == sorted(timestamps)
    assert body["total"] == 4
    assert body["sampled"] is False
    assert body["minutes"] == 10


def test_the_series_only_covers_the_window(client: TestClient, session: Session) -> None:
    seed_telemetry(session, [30.0, 15 * 60.0])

    body = client.get(SERIES_URL, params={"minutes": 10}).json()

    assert body["total"] == 1


def test_a_long_series_is_sampled_to_three_hundred_points(
    client: TestClient, session: Session
) -> None:
    """W5: o gráfico recebe no máximo 300 pontos, com o último sempre presente."""
    seed_telemetry(session, [float(seconds) for seconds in range(600, 0, -1)])

    body = client.get(SERIES_URL, params={"minutes": 30}).json()

    assert body["total"] == 600
    assert body["sampled"] is True
    assert len(body["points"]) == MAX_SERIES_POINTS


def test_the_series_of_a_silent_device_is_empty(client: TestClient) -> None:
    body = client.get(SERIES_URL).json()

    assert body["points"] == []
    assert body["total"] == 0
    assert body["sampled"] is False


@pytest.mark.parametrize("minutes", [0, -1, 1441])
def test_an_invalid_window_returns_422(client: TestClient, minutes: int) -> None:
    assert client.get(SERIES_URL, params={"minutes": minutes}).status_code == 422


# --- GET /events --------------------------------------------------------------------------------


def test_events_come_newest_first_with_the_context(client: TestClient, session: Session) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for index in range(3):
        save_event(
            session,
            event_message(event_id=f"tractor-01-1789500000-{index}"),
            received_at=now - timedelta(minutes=index),
        )

    body = client.get(EVENTS_URL).json()

    assert [event["event_id"] for event in body] == [
        "tractor-01-1789500000-0",
        "tractor-01-1789500000-1",
        "tractor-01-1789500000-2",
    ]
    assert body[0]["type"] == "rollover"
    # O contexto dos 30 s é o que alimenta o gráfico do banner de capotamento.
    assert body[0]["context"]["fields"] == ["t_s", "roll_deg", "pitch_deg", "accel_g"]
    assert len(body[0]["context"]["rows"]) == 30


def test_an_event_without_context_comes_with_null(client: TestClient, session: Session) -> None:
    payload = {
        "device_id": DEVICE_ID,
        "event_id": "tractor-01-1789500000-9",
        "ts": 1789500000,
        "type": "tilt_alert",
        "roll_deg": 13,
        "tilt_limit_deg": 10,
    }
    save_event(session, EventMessage.model_validate(payload))

    body = client.get(EVENTS_URL).json()

    assert body[0]["type"] == "tilt_alert"
    assert body[0]["context"] is None
    assert body[0]["roll_deg"] == 13.0


def test_the_event_limit_is_respected(client: TestClient, session: Session) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for index in range(5):
        save_event(
            session,
            event_message(event_id=f"tractor-01-1789500000-{index}"),
            received_at=now - timedelta(minutes=index),
        )

    assert len(client.get(EVENTS_URL, params={"limit": 2}).json()) == 2


def test_events_of_a_silent_device_are_an_empty_list(client: TestClient) -> None:
    assert client.get(EVENTS_URL).json() == []


def test_events_of_an_unknown_device_return_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/devices/{UNKNOWN_DEVICE_ID}/events")

    assert response.status_code == 404


def test_the_event_payload_survives_the_round_trip(client: TestClient, session: Session) -> None:
    """O que o firmware mandou tem que chegar igual na tela (e no laudo)."""
    original = load_fixture("rollover_event.json")
    save_event(session, event_message())

    event = client.get(EVENTS_URL).json()[0]

    assert event["context"]["rows"] == json.loads(json.dumps(original["context"]["rows"]))


def test_the_series_limit_is_the_one_the_feature_defines() -> None:
    """Âncora: os testes acima usam a constante como valor esperado e não veriam a mudança."""
    assert MAX_SERIES_POINTS == 300
    assert OFFLINE_AFTER_S == 20.0

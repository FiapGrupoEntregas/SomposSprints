"""Testes do repositório de equipamentos (I3).

O banco é **em memória** (`sqlite://` com `StaticPool`), como no I3: nenhum teste deixa arquivo
para trás. Os payloads vêm das fixtures reais do firmware.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import DeviceEvent, DeviceStatus, PublishedConfig, Telemetry
from app.repositories.devices import (
    TELEMETRY_RETENTION_DAYS,
    latest_status,
    latest_telemetry,
    list_events,
    purge_old_telemetry,
    resolve_timestamp,
    save_event,
    save_published_config,
    save_telemetry,
    set_status,
    telemetry_since,
    utc_naive,
)
from app.schemas.mqtt import ConfigMessage, EventMessage, StatusMessage, TelemetryMessage
from tests.conftest import load_fixture

DEVICE_ID = "tractor-01"
OTHER_DEVICE_ID = "harvester-01"

# O SQLite guarda data e hora **em UTC e sem fuso** (ver `utc_naive`), então é assim que os
# testes escrevem e comparam.
NOW = datetime(2026, 9, 19, 15, 0)
NOW_AWARE = NOW.replace(tzinfo=UTC)
# `ts` do contrato-mqtt: epoch em segundos, UTC.
SAMPLE_TS = 1789500000
SAMPLE_TS_UTC = datetime.fromtimestamp(SAMPLE_TS, tz=UTC).replace(tzinfo=None)


@pytest.fixture
def session() -> Iterator[Session]:
    """Banco em memória, com `StaticPool` para a conexão sobreviver entre as sessões."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def telemetry_message(**changes: object) -> TelemetryMessage:
    return TelemetryMessage.model_validate(load_fixture("telemetry_sample.json") | changes)


def event_message(**changes: object) -> EventMessage:
    return EventMessage.model_validate(load_fixture("rollover_event.json") | changes)


# --- Hora do dispositivo × hora de recepção ----------------------------------------------------


def test_resolve_timestamp_uses_the_device_clock() -> None:
    assert resolve_timestamp(SAMPLE_TS, NOW) == SAMPLE_TS_UTC


def test_resolve_timestamp_falls_back_to_the_arrival_time() -> None:
    """contrato-mqtt: `ts: 0` = NTP ainda não sincronizou; vale a hora em que a API recebeu."""
    assert resolve_timestamp(0, NOW) == NOW


def test_utc_naive_converts_an_aware_moment() -> None:
    """Um horário com fuso (o de São Paulo, por exemplo) entra no banco convertido para UTC."""
    sao_paulo = datetime(2026, 9, 19, 12, 0, tzinfo=timezone(timedelta(hours=-3)))

    assert utc_naive(sao_paulo) == datetime(2026, 9, 19, 15, 0)
    assert utc_naive(NOW_AWARE) == NOW


# --- Telemetria --------------------------------------------------------------------------------


def test_save_telemetry_stores_the_real_firmware_payload(session: Session) -> None:
    row = save_telemetry(session, telemetry_message(), received_at=NOW)

    assert row.id is not None
    assert row.device_id == DEVICE_ID
    assert row.seq == 42
    # A ArduinoJson manda `10` no lugar de `10.0`; o banco guarda float.
    assert row.tilt_limit_deg == 10.0
    assert row.humidity_pct == 28.0
    assert row.alert_level == "red"
    assert row.fire_conditions == 2
    assert row.received_at == NOW


def test_save_telemetry_without_a_synced_clock_uses_the_arrival_time(session: Session) -> None:
    row = save_telemetry(session, telemetry_message(ts=0), received_at=NOW)

    assert row.ts == NOW


def test_latest_telemetry_returns_the_newest_reading(session: Session) -> None:
    for minutes in (30, 10, 20):
        save_telemetry(
            session,
            telemetry_message(seq=minutes),
            received_at=NOW - timedelta(minutes=minutes),
        )

    latest = latest_telemetry(session, DEVICE_ID)

    assert latest is not None
    assert latest.seq == 10


def test_latest_telemetry_of_a_silent_device_is_none(session: Session) -> None:
    assert latest_telemetry(session, OTHER_DEVICE_ID) is None


def test_telemetry_since_returns_the_window_in_chronological_order(session: Session) -> None:
    for minutes in (30, 9, 1, 5):
        save_telemetry(
            session,
            telemetry_message(seq=minutes),
            received_at=NOW - timedelta(minutes=minutes),
        )

    window = telemetry_since(session, DEVICE_ID, minutes=10, now=NOW)

    assert [row.seq for row in window] == [9, 5, 1]


def test_telemetry_since_only_sees_its_own_device(session: Session) -> None:
    save_telemetry(session, telemetry_message(), received_at=NOW)
    save_telemetry(session, telemetry_message(device_id=OTHER_DEVICE_ID), received_at=NOW)

    assert len(telemetry_since(session, DEVICE_ID, minutes=10, now=NOW)) == 1


# --- Eventos -----------------------------------------------------------------------------------


def test_save_event_keeps_the_whole_payload(session: Session) -> None:
    row = save_event(session, event_message(), received_at=NOW)

    assert row is not None
    assert row.type == "rollover"
    assert row.ts == SAMPLE_TS_UTC
    payload = json.loads(row.payload_json)
    # O `context` dos 30 s é o que sustenta o laudo do sinistro: precisa chegar inteiro.
    assert len(payload["context"]["rows"]) == 30


def test_the_same_event_id_does_not_create_a_second_row(session: Session) -> None:
    """Critério de aceite: `event_id` duplicado não vira linha nova (UNIQUE + tratamento)."""
    first = save_event(session, event_message(), received_at=NOW)
    second = save_event(session, event_message(), received_at=NOW + timedelta(seconds=1))

    assert first is not None
    assert second is None
    assert len(session.exec(select(DeviceEvent)).all()) == 1


def test_the_session_still_works_after_a_duplicate(session: Session) -> None:
    """O `rollback` do duplicado não pode deixar a sessão inutilizável para a próxima mensagem."""
    save_event(session, event_message(), received_at=NOW)
    save_event(session, event_message(), received_at=NOW)

    saved = save_event(session, event_message(event_id="tractor-01-1789500100-8"), received_at=NOW)

    assert saved is not None
    assert len(session.exec(select(DeviceEvent)).all()) == 2


def test_list_events_returns_the_newest_first(session: Session) -> None:
    for index in range(3):
        save_event(
            session,
            event_message(event_id=f"tractor-01-1789500000-{index}"),
            received_at=NOW + timedelta(seconds=index),
        )

    events = list_events(session, DEVICE_ID)

    assert [event.event_id for event in events] == [
        "tractor-01-1789500000-2",
        "tractor-01-1789500000-1",
        "tractor-01-1789500000-0",
    ]


def test_list_events_respects_the_limit(session: Session) -> None:
    for index in range(5):
        save_event(
            session,
            event_message(event_id=f"tractor-01-1789500000-{index}"),
            received_at=NOW + timedelta(seconds=index),
        )

    assert len(list_events(session, DEVICE_ID, limit=2)) == 2


# --- Status ------------------------------------------------------------------------------------


def test_set_status_keeps_one_row_per_device(session: Session) -> None:
    set_status(session, StatusMessage(device_id=DEVICE_ID, state="online"), updated_at=NOW)
    row = set_status(
        session,
        StatusMessage(device_id=DEVICE_ID, state="offline"),
        updated_at=NOW + timedelta(minutes=1),
    )

    assert row.state == "offline"
    assert row.updated_at == NOW + timedelta(minutes=1)
    assert len(session.exec(select(DeviceStatus)).all()) == 1


def test_latest_status_of_an_unknown_device_is_none(session: Session) -> None:
    assert latest_status(session, OTHER_DEVICE_ID) is None


# --- Config publicado ----------------------------------------------------------------------------


def test_save_published_config_records_what_was_sent(session: Session) -> None:
    config = ConfigMessage(tilt_limit_deg=10.0, soil_state="saturated", reason="42 mm em 72 h")

    row = save_published_config(session, DEVICE_ID, config, published_at=NOW)

    assert row.device_id == DEVICE_ID
    payload = json.loads(row.payload_json)
    assert payload == {
        "tilt_limit_deg": 10.0,
        "soil_state": "saturated",
        "reason": "42 mm em 72 h",
    }
    assert len(session.exec(select(PublishedConfig)).all()) == 1


# --- Retenção ------------------------------------------------------------------------------------


def test_retention_only_removes_telemetry_older_than_seven_days(session: Session) -> None:
    """I3: a telemetria vence em 7 dias. A borda exata (7 dias em ponto) ainda fica."""
    for days in (8, TELEMETRY_RETENTION_DAYS, 6):
        save_telemetry(session, telemetry_message(seq=days), received_at=NOW - timedelta(days=days))

    removed = purge_old_telemetry(session, now=NOW)

    assert removed == 1
    assert sorted(row.seq for row in session.exec(select(Telemetry))) == [6, 7]


def test_retention_keeps_events_status_and_configs(session: Session) -> None:
    """Só a telemetria é volumosa; a memória do sinistro fica."""
    old = NOW - timedelta(days=30)
    save_event(session, event_message(), received_at=old)
    set_status(session, StatusMessage(device_id=DEVICE_ID, state="online"), updated_at=old)
    save_published_config(session, DEVICE_ID, ConfigMessage(tilt_limit_deg=10.0), published_at=old)

    purge_old_telemetry(session, now=NOW)

    assert len(session.exec(select(DeviceEvent)).all()) == 1
    assert len(session.exec(select(DeviceStatus)).all()) == 1
    assert len(session.exec(select(PublishedConfig)).all()) == 1


def test_retention_on_an_empty_database_removes_nothing(session: Session) -> None:
    assert purge_old_telemetry(session, now=NOW) == 0

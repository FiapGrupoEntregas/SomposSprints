"""Testes do boot da API: banco criado e ponte MQTT ligada no `lifespan` (I2 + I3).

O `conftest.py` deixa `AGRISHIELD_MQTT_ENABLED=false` e o banco em memória, então **nada aqui
toca o broker nem o disco**. O caminho completo (mensagem → ponte → repositório → SQLite) é
exercitado chamando `handle_message` direto, que é o mesmo ponto de entrada da thread do paho.
"""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.core.config import Settings, get_settings
from app.db import create_db, get_engine
from app.main import app, build_bridge
from app.models import DeviceEvent, DeviceStatus, Telemetry
from tests.conftest import load_fixture

PREFIX = "agrishield/fiap-sompo-2026"
DEVICE_ID = "tractor-01"


@pytest.fixture
def clean_database() -> Iterator[None]:
    """Banco em memória do processo, vazio antes e depois de cada teste."""
    engine = get_engine()
    SQLModel.metadata.drop_all(engine)
    create_db(engine)
    yield
    SQLModel.metadata.drop_all(engine)
    create_db(engine)


@pytest.fixture
def settings() -> Settings:
    return Settings(mqtt_enabled=False, mqtt_topic_prefix=PREFIX, database_url="sqlite://")


def topic(suffix: str) -> str:
    return f"{PREFIX}/devices/{DEVICE_ID}/{suffix}"


def test_the_api_boots_with_the_mqtt_bridge_turned_off() -> None:
    """Critério de aceite do I2: a API sobe sem depender do broker."""
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        assert app.state.mqtt is not None
        assert app.state.mqtt.is_connected is False


def test_the_lifespan_creates_the_tables(clean_database: None) -> None:
    """I3: `create_db()` roda no boot, então a primeira mensagem já encontra as tabelas."""
    SQLModel.metadata.drop_all(get_engine())

    with TestClient(app):
        pass

    with Session(get_engine()) as session:
        assert session.exec(select(Telemetry)).all() == []


def test_the_mqtt_settings_are_off_in_the_test_suite() -> None:
    """Rede de segurança: se isto falhar, algum teste vai tentar falar com o broker de verdade."""
    assert get_settings().mqtt_enabled is False


def test_telemetry_goes_from_the_bridge_to_the_database(
    clean_database: None, settings: Settings
) -> None:
    bridge = build_bridge(settings)

    assert (
        bridge.handle_message(topic("telemetry"), json.dumps(load_fixture("telemetry_sample.json")))
        is True
    )

    with Session(get_engine()) as session:
        rows = session.exec(select(Telemetry)).all()

    assert len(rows) == 1
    assert rows[0].device_id == DEVICE_ID
    assert rows[0].tilt_limit_deg == 10.0


def test_status_goes_from_the_bridge_to_the_database(
    clean_database: None, settings: Settings
) -> None:
    bridge = build_bridge(settings)
    payload = json.dumps({"device_id": DEVICE_ID, "state": "online"})

    assert bridge.handle_message(topic("status"), payload) is True

    with Session(get_engine()) as session:
        status = session.get(DeviceStatus, DEVICE_ID)

    assert status is not None
    assert status.state == "online"


def test_an_event_repeated_after_a_restart_is_still_saved_once(
    clean_database: None, settings: Settings
) -> None:
    """As duas travas da deduplicação: a memória da ponte (I2) e a `UNIQUE` do banco (I3).

    Uma ponte nova simula a API reiniciada: a memória dos `event_id` recentes zera, e quem segura
    a repetição é o banco.
    """
    payload = json.dumps(load_fixture("rollover_event.json"))

    build_bridge(settings).handle_message(topic("events"), payload)
    restarted = build_bridge(settings)
    assert restarted.handle_message(topic("events"), payload) is True

    with Session(get_engine()) as session:
        rows = session.exec(select(DeviceEvent)).all()

    assert len(rows) == 1
    assert rows[0].type == "rollover"


def test_an_invalid_payload_saves_nothing(clean_database: None, settings: Settings) -> None:
    """Critério de aceite do I2: payload inválido não grava nada e a ponte segue de pé."""
    bridge = build_bridge(settings)

    assert bridge.handle_message(topic("telemetry"), "{quebrado") is False

    with Session(get_engine()) as session:
        assert session.exec(select(Telemetry)).all() == []

    assert (
        bridge.handle_message(topic("telemetry"), json.dumps(load_fixture("telemetry_sample.json")))
        is True
    )

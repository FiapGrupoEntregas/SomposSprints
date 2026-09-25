"""Testes da trilha de auditoria (I5): registro das decisões, integridade e consulta.

Critério de aceite central: **calcular um risco, publicar um limite e receber um alerta geram
linhas em `decision_log`, com entrada, saída e versões.**
"""

import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.core.clock import today_local
from app.core.config import Settings, get_settings
from app.core.versions import MODEL_VERSION, RULES_VERSION
from app.db import get_session
from app.main import app
from app.models import DecisionLog, EventIntegrity
from app.mqtt.bridge import get_mqtt
from app.repositories import audit as audit_repository
from app.schemas.audit import DecisionSource, DecisionType
from app.schemas.mqtt import EventMessage
from app.services import model as model_service
from app.services.terrain import clear_terrain_cache
from tests.conftest import FIXTURES_DIR, load_fixture
from tests.test_devices_limit_route import FakeBridge
from tests.test_risk_route import forecast_payload

FARM_ID = "cafe-carmo-de-minas"
DEVICE_ID = "tractor-01"
API_KEY = "chave-de-teste-1234"
AUTH = {"X-API-Key": API_KEY}

AUDIT_URL = "/api/v1/audit"
RISK_URL = f"/api/v1/farms/{FARM_ID}/risk"
PUBLISH_URL = f"/api/v1/devices/{DEVICE_ID}/limit/publish"

NOW = datetime(2026, 9, 19, 15, 0)


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
def api(make_client: Callable[..., OpenMeteoClient], session: Session) -> Callable[..., TestClient]:
    def factory(mqtt: object | None = None) -> TestClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if "elevation" in request.url.path:
                path = FIXTURES_DIR / "terrain_elevation_carmo.json"
                return httpx.Response(200, json=json.loads(path.read_text(encoding="utf-8")))
            return httpx.Response(200, json=forecast_payload(today_local()))

        app.dependency_overrides[get_open_meteo_client] = lambda: make_client(handler)
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_settings] = lambda: Settings(api_keys=API_KEY)
        app.dependency_overrides[get_mqtt] = lambda: mqtt if mqtt is not None else FakeBridge()
        return TestClient(app)

    return factory


def sample_decision(session: Session, **changes: object) -> DecisionLog:
    payload: dict = {
        "decision_type": DecisionType.RISK_SCORE,
        "entity_id": FARM_ID,
        "inputs": {"days": 7},
        "output": {"worst_level": "red"},
        "source": DecisionSource.API,
    }
    payload.update(changes)
    return audit_repository.record_decision(session, **payload)  # type: ignore[arg-type]


# --- Repositório --------------------------------------------------------------------------------


def test_a_decision_records_inputs_output_and_versions(session: Session) -> None:
    row = sample_decision(session, request_id="abc123", created_at=NOW)

    assert row.id is not None
    assert row.decision_type == "risk_score"
    assert row.entity_id == FARM_ID
    assert json.loads(row.inputs_json) == {"days": 7}
    assert json.loads(row.output_json) == {"worst_level": "red"}
    assert row.rule_version == RULES_VERSION
    assert row.model_version == MODEL_VERSION is None
    assert row.source == "api"
    assert row.request_id == "abc123"
    assert row.created_at == NOW


def test_decisions_come_back_newest_first(session: Session) -> None:
    for index in range(3):
        sample_decision(
            session, entity_id=f"fazenda-{index}", created_at=NOW + timedelta(minutes=index)
        )

    rows = audit_repository.list_decisions(session)

    assert [row.entity_id for row in rows] == ["fazenda-2", "fazenda-1", "fazenda-0"]


def test_decisions_can_be_filtered(session: Session) -> None:
    sample_decision(session, entity_id=FARM_ID, decision_type=DecisionType.RISK_SCORE)
    sample_decision(session, entity_id=DEVICE_ID, decision_type=DecisionType.TILT_LIMIT)

    assert len(audit_repository.list_decisions(session, entity_id=DEVICE_ID)) == 1
    assert len(audit_repository.list_decisions(session, decision_type=DecisionType.ALERT)) == 0
    assert len(audit_repository.list_decisions(session, decision_type=DecisionType.TILT_LIMIT)) == 1


def test_the_limit_caps_the_result(session: Session) -> None:
    for _ in range(5):
        sample_decision(session)

    assert len(audit_repository.list_decisions(session, limit=2)) == 2


# --- Integridade --------------------------------------------------------------------------------


def test_the_event_hash_is_of_the_raw_bytes(session: Session) -> None:
    raw = b'{"device_id":"tractor-01","event_id":"e-1"}'

    row = audit_repository.record_event_integrity(session, "e-1", DEVICE_ID, raw)

    assert row is not None
    assert row.payload_sha256 == audit_repository.payload_sha256(raw)
    assert row.payload_bytes == len(raw)
    # Um byte diferente muda o hash: é isso que detecta alteração.
    assert row.payload_sha256 != audit_repository.payload_sha256(raw + b" ")


def test_the_same_event_keeps_the_first_hash(session: Session) -> None:
    """O ESP32 publica 3 vezes: vale o hash da cópia que virou linha em `device_event`."""
    audit_repository.record_event_integrity(session, "e-1", DEVICE_ID, b'{"a":1}')

    assert audit_repository.record_event_integrity(session, "e-1", DEVICE_ID, b'{"a":2}') is None

    stored = audit_repository.event_integrity(session, "e-1")
    assert stored is not None
    assert stored.payload_sha256 == audit_repository.payload_sha256(b'{"a":1}')


# --- As decisões nascem dos caminhos reais ------------------------------------------------------


def test_asking_for_the_risk_records_a_decision(
    api: Callable[..., TestClient], session: Session
) -> None:
    """Critério de aceite: calcular um risco gera linha na trilha."""
    client = api()

    response = client.get(RISK_URL, params={"days": 3})

    assert response.status_code == 200
    rows = audit_repository.list_decisions(session, decision_type=DecisionType.RISK_SCORE)
    assert len(rows) == 1
    output = json.loads(rows[0].output_json)
    assert rows[0].entity_id == FARM_ID
    assert json.loads(rows[0].inputs_json)["days"] == 3
    assert len(output["days"]) == 3
    # O resumo entra, as 700 células não: a trilha não pode crescer mais que o banco.
    assert "cells" not in output
    assert rows[0].request_id == response.headers["X-Request-ID"]


def test_risk_opt_in_and_experimental_scores_are_audited_separately(
    api: Callable[..., TestClient],
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_service, "get_optional_neural_model", lambda: object())
    monkeypatch.setattr(model_service, "score_optional_neural_model", lambda *_: 0.625)
    client = api()

    response = client.get(RISK_URL, params={"days": 1, "include_experimental_mlp": True})

    assert response.status_code == 200
    rows = audit_repository.list_decisions(session, decision_type=DecisionType.RISK_SCORE)
    assert len(rows) == 1
    inputs = json.loads(rows[0].inputs_json)
    output = json.loads(rows[0].output_json)
    assert inputs["include_experimental_mlp"] is True
    assert output["experimental_mlp"]["available"] is True
    assert output["experimental_mlp"]["version"] == model_service.NEURAL_MODEL_VERSION
    assert "não é uma probabilidade calibrada" in output["experimental_mlp"]["note"]
    assert output["experimental_mlp"]["days"][0]["score"] == 0.625
    assert output["days"][0]["model_probability"] == response.json()["days"][0]["model_probability"]


def test_publishing_a_limit_records_a_decision(
    api: Callable[..., TestClient], session: Session
) -> None:
    """Critério de aceite: publicar um limite gera linha na trilha."""
    client = api()

    response = client.post(PUBLISH_URL, json={"scenario": "heavy_rain"}, headers=AUTH)

    assert response.status_code == 200
    rows = audit_repository.list_decisions(session, decision_type=DecisionType.TILT_LIMIT)
    assert len(rows) == 1
    assert rows[0].entity_id == DEVICE_ID
    assert rows[0].source == "api"
    assert rows[0].request_id == response.headers["X-Request-ID"]
    inputs = json.loads(rows[0].inputs_json)
    output = json.loads(rows[0].output_json)
    assert inputs["scenario"] == "heavy_rain"
    assert inputs["farm_id"] == FARM_ID
    # A saída registrada é exatamente o que foi para o broker.
    assert output == response.json()["payload"]


def test_a_refused_publication_records_nothing(
    api: Callable[..., TestClient], session: Session
) -> None:
    """A trilha só registra limite que saiu de verdade."""
    client = api(FakeBridge(connected=True, accepts=False))

    assert client.post(PUBLISH_URL, json={}, headers=AUTH).status_code == 503
    assert audit_repository.list_decisions(session, decision_type=DecisionType.TILT_LIMIT) == []


def test_an_alert_event_records_a_decision_and_its_hash(session: Session) -> None:
    """Critério de aceite: o alerta do equipamento também é uma decisão rastreável."""
    from app.mqtt.handlers import _save_event

    raw = json.dumps(load_fixture("rollover_event.json")).encode("utf-8")
    message = EventMessage.model_validate_json(raw)

    _save_event(session, message, raw)

    decisions = audit_repository.list_decisions(session, decision_type=DecisionType.ALERT)
    assert len(decisions) == 1
    assert decisions[0].entity_id == DEVICE_ID
    assert decisions[0].source == "device"
    assert decisions[0].request_id is None
    assert json.loads(decisions[0].output_json)["payload_sha256"] == (
        audit_repository.payload_sha256(raw)
    )
    assert session.get(EventIntegrity, message.event_id) is not None


def test_an_acknowledgement_is_not_an_alert(session: Session) -> None:
    """`limit_applied` é confirmação de recebimento, não uma decisão de risco."""
    from app.mqtt.handlers import _save_event

    payload = {
        "device_id": DEVICE_ID,
        "event_id": "tractor-01-1789500000-5",
        "ts": 1789500000,
        "type": "limit_applied",
        "tilt_limit_deg": 10,
    }
    raw = json.dumps(payload).encode("utf-8")

    _save_event(session, EventMessage.model_validate(payload), raw)

    assert audit_repository.list_decisions(session, decision_type=DecisionType.ALERT) == []
    # A integridade, essa, vale para todo evento.
    assert session.get(EventIntegrity, payload["event_id"]) is not None


# --- GET /api/v1/audit --------------------------------------------------------------------------


def test_the_audit_endpoint_returns_the_trail(
    api: Callable[..., TestClient], session: Session
) -> None:
    client = api()
    sample_decision(session, created_at=NOW)

    body = client.get(AUDIT_URL, headers=AUTH).json()

    assert len(body) == 1
    assert body[0]["decision_type"] == "risk_score"
    assert body[0]["entity_id"] == FARM_ID
    assert body[0]["inputs"] == {"days": 7}
    assert body[0]["output"] == {"worst_level": "red"}
    assert body[0]["rule_version"] == RULES_VERSION
    assert body[0]["model_version"] is None
    assert body[0]["source"] == "api"


def test_the_audit_endpoint_filters_and_limits(
    api: Callable[..., TestClient], session: Session
) -> None:
    client = api()
    sample_decision(session, entity_id=FARM_ID, decision_type=DecisionType.RISK_SCORE)
    sample_decision(session, entity_id=DEVICE_ID, decision_type=DecisionType.TILT_LIMIT)

    assert len(client.get(AUDIT_URL, params={"entity": DEVICE_ID}, headers=AUTH).json()) == 1
    assert len(client.get(AUDIT_URL, params={"decision_type": "alert"}, headers=AUTH).json()) == 0
    assert len(client.get(AUDIT_URL, params={"limit": 1}, headers=AUTH).json()) == 1


def test_an_unknown_decision_type_returns_422(api: Callable[..., TestClient]) -> None:
    client = api()

    response = client.get(AUDIT_URL, params={"decision_type": "chute"}, headers=AUTH)

    assert response.status_code == 422


def test_the_trail_never_carries_the_api_key(
    api: Callable[..., TestClient], session: Session
) -> None:
    """Nem a chave que autorizou a publicação entra na trilha."""
    client = api()

    client.post(PUBLISH_URL, json={}, headers=AUTH)

    rows = session.exec(select(DecisionLog)).all()
    assert rows
    for row in rows:
        assert API_KEY not in row.inputs_json
        assert API_KEY not in row.output_json


# --- Retenção da trilha (I5) --------------------------------------------------------------------


def test_retention_removes_only_the_old_decisions(session: Session) -> None:
    """`GET /farms/{id}/risk` é aberto e grava a cada chamada: a trilha precisa de teto."""
    for days in (31, audit_repository.DECISION_RETENTION_DAYS, 29):
        sample_decision(session, entity_id=f"fazenda-{days}", created_at=NOW - timedelta(days=days))

    removed = audit_repository.purge_old_decisions(session, now=NOW)

    assert removed == 1
    remaining = {row.entity_id for row in session.exec(select(DecisionLog))}
    assert remaining == {"fazenda-30", "fazenda-29"}


def test_retention_on_an_empty_trail_removes_nothing(session: Session) -> None:
    assert audit_repository.purge_old_decisions(session, now=NOW) == 0


def test_retention_respects_a_custom_window(session: Session) -> None:
    sample_decision(session, created_at=NOW - timedelta(days=8))

    assert audit_repository.purge_old_decisions(session, days=7, now=NOW) == 1

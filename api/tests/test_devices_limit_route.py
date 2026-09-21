"""Testes das rotas de limite do equipamento (W4). Sem rede e sem broker.

A Open-Meteo é mockada com as fixtures do I1/W2 e a ponte MQTT é um dublê que só guarda o que
foi publicado.
"""

import json
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.core.clock import today_local
from app.core.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import PublishedConfig
from app.mqtt.bridge import CONFIG_QOS, get_mqtt
from app.services.terrain import clear_terrain_cache
from tests.conftest import FIXTURES_DIR, load_fixture
from tests.test_risk_route import forecast_payload

DEVICE_ID = "tractor-01"
FARM_ID = "cafe-carmo-de-minas"
UNKNOWN_DEVICE_ID = "trator-fantasma"

LIMIT_URL = f"/api/v1/devices/{DEVICE_ID}/limit"
PUBLISH_URL = f"{LIMIT_URL}/publish"
CONFIG_TOPIC = f"agrishield/fiap-sompo-2026/devices/{DEVICE_ID}/config"

# I5 — publicar limite exige `X-API-Key` (ADR-013). O 401 tem teste próprio em test_security.py.
API_KEY = "chave-de-teste-1234"
AUTH = {"X-API-Key": API_KEY}

ClientFactory = Callable[..., OpenMeteoClient]


class FakeBridge:
    """Dublê da `MqttBridge`: guarda as publicações e finge (ou não) estar conectado."""

    def __init__(self, connected: bool = True, accepts: bool = True) -> None:
        self.is_connected = connected
        self._accepts = accepts
        self.published: list[dict[str, Any]] = []

    def topic_for(self, device_id: str, suffix: str) -> str:
        return f"agrishield/fiap-sompo-2026/devices/{device_id}/{suffix}"

    def publish_config(self, device_id: str, config: Any) -> bool:
        if not self._accepts:
            return False
        self.published.append(
            {
                "topic": self.topic_for(device_id, "config"),
                "payload": json.loads(config.model_dump_json(exclude_none=True)),
                "qos": CONFIG_QOS,
                "retain": True,
            }
        )
        return True


@pytest.fixture
def today() -> date:
    """Hoje em São Paulo, capturado uma vez por teste."""
    return today_local()


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def bridge() -> FakeBridge:
    return FakeBridge()


@pytest.fixture(autouse=True)
def clean_caches() -> Iterator[None]:
    clear_terrain_cache()
    yield
    clear_terrain_cache()
    app.dependency_overrides.clear()


@pytest.fixture
def api(
    make_client: ClientFactory, session: Session, bridge: FakeBridge, today: date
) -> Callable[..., TestClient]:
    """`TestClient` com Open-Meteo mockada, banco em memória e ponte falsa."""

    def factory(mqtt: Any | None = None) -> TestClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if "elevation" in request.url.path:
                path = FIXTURES_DIR / "terrain_elevation_carmo.json"
                return httpx.Response(200, json=json.loads(path.read_text(encoding="utf-8")))
            return httpx.Response(200, json=forecast_payload(today))

        app.dependency_overrides[get_open_meteo_client] = lambda: make_client(handler)
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_mqtt] = lambda: mqtt if mqtt is not None else bridge
        app.dependency_overrides[get_settings] = lambda: Settings(api_keys=API_KEY)
        return TestClient(app)

    return factory


# --- GET /limit ---------------------------------------------------------------------------------


def test_get_limit_returns_todays_limit(api: Callable[..., TestClient], today: date) -> None:
    client = api()

    response = client.get(LIMIT_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == DEVICE_ID
    assert body["farm_id"] == FARM_ID
    assert body["date"] == today.isoformat()
    assert body["scenario"] is None
    assert body["reference_tilt_limit_deg"] == 15.0
    assert body["warn_ratio"] == 0.8
    assert body["tilt_limit_deg"] in {15.0, 12.5, 10.0}
    assert body["reason"] == body["reason"].encode("ascii", "ignore").decode()


def test_get_limit_accepts_a_future_date(api: Callable[..., TestClient], today: date) -> None:
    client = api()

    body = client.get(LIMIT_URL, params={"date": (today + timedelta(days=2)).isoformat()}).json()

    assert body["date"] == (today + timedelta(days=2)).isoformat()


def test_the_heavy_rain_scenario_lowers_the_limit(
    api: Callable[..., TestClient], today: date
) -> None:
    """O momento da demo: o mesmo dia, com chuva simulada, derruba o limite do equipamento."""
    client = api()
    day_plus_two = (today + timedelta(days=2)).isoformat()

    real = client.get(LIMIT_URL, params={"date": day_plus_two}).json()
    simulated = client.get(
        LIMIT_URL, params={"date": day_plus_two, "scenario": "heavy_rain"}
    ).json()

    assert simulated["scenario"] == "heavy_rain"
    assert simulated["soil_state"] == "saturated"
    assert simulated["tilt_limit_deg"] == 10.0
    assert simulated["tilt_limit_deg"] <= real["tilt_limit_deg"]
    assert "encharcado" in simulated["reason"]


def test_a_date_outside_the_forecast_returns_404(
    api: Callable[..., TestClient], today: date
) -> None:
    client = api()

    response = client.get(LIMIT_URL, params={"date": (today - timedelta(days=1)).isoformat()})

    assert response.status_code == 404
    assert "Sem previsão" in response.json()["detail"]


def test_an_unknown_device_returns_404(api: Callable[..., TestClient]) -> None:
    client = api()

    response = client.get(f"/api/v1/devices/{UNKNOWN_DEVICE_ID}/limit")

    assert response.status_code == 404
    assert response.json()["detail"] == "Equipamento não encontrado"


def test_an_unknown_scenario_returns_422(api: Callable[..., TestClient]) -> None:
    client = api()

    assert client.get(LIMIT_URL, params={"scenario": "tsunami"}).status_code == 422


# --- POST /limit/publish ------------------------------------------------------------------------


def test_publish_sends_the_config_retained_with_qos_one(
    api: Callable[..., TestClient], bridge: FakeBridge
) -> None:
    """Critério de aceite: o ESP32 recebe o último limite ao conectar, então é retained."""
    client = api()

    response = client.post(PUBLISH_URL, json={}, headers=AUTH)

    assert response.status_code == 200
    assert len(bridge.published) == 1
    sent = bridge.published[0]
    assert sent["topic"] == CONFIG_TOPIC
    assert sent["qos"] == CONFIG_QOS
    assert sent["retain"] is True
    assert set(sent["payload"]) <= {
        "tilt_limit_deg",
        "warn_ratio",
        "soil_state",
        "risk_level",
        "wind_max_kmh",
        "valid_until",
        "reason",
    }
    assert sent["payload"]["warn_ratio"] == 0.8


def test_publish_answers_with_what_was_sent(
    api: Callable[..., TestClient], bridge: FakeBridge
) -> None:
    client = api()

    body = client.post(PUBLISH_URL, json={}, headers=AUTH).json()

    assert body["topic"] == CONFIG_TOPIC
    assert body["payload"] == bridge.published[0]["payload"]
    assert body["limit"]["device_id"] == DEVICE_ID
    assert body["published_at"]


def test_publish_works_without_a_body(api: Callable[..., TestClient], bridge: FakeBridge) -> None:
    """O front pode chamar sem corpo nenhum: vale hoje e a previsão real."""
    client = api()

    assert client.post(PUBLISH_URL, headers=AUTH).status_code == 200
    assert bridge.published


def test_publish_accepts_date_and_scenario(
    api: Callable[..., TestClient], bridge: FakeBridge, today: date
) -> None:
    """Critério de aceite: a demo envia o limite do "dia chuvoso"."""
    client = api()

    body = client.post(
        PUBLISH_URL,
        json={"date": (today + timedelta(days=2)).isoformat(), "scenario": "heavy_rain"},
        headers=AUTH,
    ).json()

    assert body["limit"]["scenario"] == "heavy_rain"
    assert body["payload"]["tilt_limit_deg"] == 10.0
    assert body["payload"]["soil_state"] == "saturated"


def test_publish_has_no_accents_in_the_reason(
    api: Callable[..., TestClient], bridge: FakeBridge
) -> None:
    """Critério de aceite: a fonte do OLED não tem acento (contrato-mqtt, E7)."""
    client = api()

    client.post(PUBLISH_URL, json={}, headers=AUTH)

    reason = bridge.published[0]["payload"]["reason"]
    assert reason == reason.encode("ascii", "ignore").decode()


def test_publish_records_what_was_sent(api: Callable[..., TestClient], session: Session) -> None:
    """I3: todo envio fica registrado, para auditoria (I5)."""
    client = api()

    client.post(PUBLISH_URL, json={}, headers=AUTH)

    rows = session.exec(select(PublishedConfig)).all()
    assert len(rows) == 1
    assert rows[0].device_id == DEVICE_ID
    assert "tilt_limit_deg" in json.loads(rows[0].payload_json)


def test_publish_with_the_bridge_disconnected_returns_503(
    api: Callable[..., TestClient], session: Session
) -> None:
    """Critério de aceite: com o MQTT fora, o front mostra erro amigável em vez de fingir envio."""
    offline = FakeBridge(connected=False)
    client = api(offline)

    response = client.post(PUBLISH_URL, json={}, headers=AUTH)

    assert response.status_code == 503
    assert response.json()["detail"] == "Equipamento sem conexão MQTT"
    assert offline.published == []
    # Nada foi enviado, então nada pode aparecer no registro de auditoria.
    assert session.exec(select(PublishedConfig)).all() == []


def test_without_the_broker_the_weather_is_not_even_consulted(
    make_client: ClientFactory, session: Session
) -> None:
    """Com o broker fora **e** a Open-Meteo em 429, o front tem que ler "sem conexão MQTT".

    A conexão é checada antes do cálculo: a resposta sai na hora e diz o que realmente impede o
    envio, em vez de culpar o clima.
    """
    recorder_requests: list[httpx.Request] = []

    def failing_weather(request: httpx.Request) -> httpx.Response:
        recorder_requests.append(request)
        return httpx.Response(429, text="Too Many Requests")

    app.dependency_overrides[get_open_meteo_client] = lambda: make_client(failing_weather)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_mqtt] = lambda: FakeBridge(connected=False)
    app.dependency_overrides[get_settings] = lambda: Settings(api_keys=API_KEY)
    client = TestClient(app)

    response = client.post(PUBLISH_URL, json={}, headers=AUTH)

    assert response.status_code == 503
    assert response.json()["detail"] == "Equipamento sem conexão MQTT"
    assert recorder_requests == [], "foi à Open-Meteo mesmo sem broker para publicar"


def test_publish_refused_by_the_broker_returns_503(api: Callable[..., TestClient]) -> None:
    client = api(FakeBridge(connected=True, accepts=False))

    response = client.post(PUBLISH_URL, json={}, headers=AUTH)

    assert response.status_code == 503
    assert response.json()["detail"] == "Equipamento sem conexão MQTT"


def test_publish_to_an_unknown_device_returns_404(
    api: Callable[..., TestClient], bridge: FakeBridge
) -> None:
    client = api()

    response = client.post(
        f"/api/v1/devices/{UNKNOWN_DEVICE_ID}/limit/publish", json={}, headers=AUTH
    )

    assert response.status_code == 404
    assert bridge.published == []


def test_publish_with_an_unknown_field_returns_422(api: Callable[..., TestClient]) -> None:
    client = api()

    assert client.post(PUBLISH_URL, json={"dia": "2026-09-21"}, headers=AUTH).status_code == 422


def test_the_telemetry_fixture_is_still_the_one_the_firmware_sends() -> None:
    """Amarra o teste ao payload real: se a fixture mudar, alguém mexeu no contrato."""
    assert load_fixture("telemetry_sample.json")["device_id"] == DEVICE_ID

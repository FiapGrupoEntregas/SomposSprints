"""Testes da ponte MQTT (I2) — document/contrato-mqtt.md.

**Nenhum teste fala com um broker.** `handle_message` é chamado direto, e o único `start()` do
arquivo aponta para uma porta local fechada, só para provar que a API sobe com o broker fora.
"""

import json
import logging
from typing import Any

import paho.mqtt.client as mqtt
import pytest

from app.core.config import Settings
from app.mqtt.bridge import (
    CONFIG_QOS,
    RECENT_EVENT_IDS,
    MqttBridge,
    RecentEventIds,
)
from app.schemas.mqtt import AlertLevel, ConfigMessage, DeviceState, EventType
from tests.conftest import load_fixture

PREFIX = "agrishield/fiap-sompo-2026"
DEVICE_ID = "tractor-01"

TELEMETRY_TOPIC = f"{PREFIX}/devices/{DEVICE_ID}/telemetry"
EVENTS_TOPIC = f"{PREFIX}/devices/{DEVICE_ID}/events"
STATUS_TOPIC = f"{PREFIX}/devices/{DEVICE_ID}/status"

# Porta local fechada: a conexão falha na hora, sem DNS e sem sair da máquina.
CLOSED_PORT = 1


class FakePahoClient:
    """Dublê do `paho.mqtt.Client`: guarda o que foi publicado, sem nenhuma rede."""

    def __init__(self, rc: int = mqtt.MQTT_ERR_SUCCESS) -> None:
        self.published: list[dict[str, Any]] = []
        self._rc = rc

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> Any:
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return type("Info", (), {"rc": self._rc})()


class RecordingPahoClient(FakePahoClient):
    """Dublê que registra como a ponte configurou a conexão, sem abrir socket nenhum."""

    def __init__(self) -> None:
        super().__init__()
        self.connect_kwargs: dict[str, Any] = {}
        self.connect_args: tuple[Any, ...] = ()
        self.reconnect_kwargs: dict[str, Any] = {}
        self.loop_started = False

    def reconnect_delay_set(self, **kwargs: Any) -> None:
        self.reconnect_kwargs = kwargs

    def connect_async(self, *args: Any, **kwargs: Any) -> None:
        self.connect_args, self.connect_kwargs = args, kwargs

    def loop_start(self) -> None:
        self.loop_started = True


class Collector:
    """Callback que guarda as mensagens recebidas (e, no evento, os bytes crus).

    O callback de evento leva um segundo argumento desde a I5: o payload cru, sobre o qual o hash
    de integridade é calculado.
    """

    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.raw_payloads: list[bytes] = []

    def __call__(self, message: Any, raw: bytes | None = None) -> None:
        self.messages.append(message)
        if raw is not None:
            self.raw_payloads.append(raw)


@pytest.fixture
def settings() -> Settings:
    return Settings(mqtt_enabled=False, mqtt_topic_prefix=PREFIX)


@pytest.fixture
def collectors() -> tuple[Collector, Collector, Collector]:
    return Collector(), Collector(), Collector()


@pytest.fixture
def bridge(settings: Settings, collectors: tuple[Collector, Collector, Collector]) -> MqttBridge:
    telemetry, events, status = collectors
    return MqttBridge(
        settings,
        on_telemetry=telemetry,
        on_event=events,
        on_status=status,
        client=FakePahoClient(),
    )


def telemetry_payload(**changes: Any) -> str:
    """Telemetria real do firmware (`tests/fixtures/telemetry_sample.json`), com ajustes."""
    payload = load_fixture("telemetry_sample.json") | changes
    return json.dumps(payload)


def event_payload(**changes: Any) -> str:
    """Evento `rollover` real do firmware, com os 30 s de contexto."""
    payload = load_fixture("rollover_event.json") | changes
    return json.dumps(payload)


def status_payload(state: str = "online", device_id: str = DEVICE_ID) -> str:
    return json.dumps({"device_id": device_id, "state": state})


# --- Tópicos -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "topic",
    [
        f"{PREFIX}/devices/{DEVICE_ID}/unknown",
        f"{PREFIX}/devices/{DEVICE_ID}",
        f"{PREFIX}/devices//telemetry",
        f"{PREFIX}/gadgets/{DEVICE_ID}/telemetry",
        "outro-prefixo/devices/tractor-01/telemetry",
        f"{PREFIX}/devices/{DEVICE_ID}/telemetry/extra",
    ],
)
def test_topics_outside_the_contract_are_ignored(bridge: MqttBridge, topic: str) -> None:
    assert bridge.handle_message(topic, telemetry_payload()) is False


def test_topic_for_follows_the_contract(bridge: MqttBridge) -> None:
    assert bridge.topic_for(DEVICE_ID, "config") == f"{PREFIX}/devices/{DEVICE_ID}/config"


# --- Telemetria --------------------------------------------------------------------------------


def test_real_firmware_telemetry_is_accepted(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    """A ArduinoJson manda `10.0` como `10`: o schema precisa aceitar `int` onde o campo é float."""
    telemetry, _, _ = collectors

    assert bridge.handle_message(TELEMETRY_TOPIC, telemetry_payload()) is True

    message = telemetry.messages[0]
    assert message.device_id == DEVICE_ID
    assert message.tilt_limit_deg == 10.0
    assert isinstance(message.tilt_limit_deg, float)
    assert message.humidity_pct == 28.0
    assert message.alert_level is AlertLevel.RED
    assert message.fire_conditions == 2


def test_telemetry_accepts_bytes_as_paho_delivers_them(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    telemetry, _, _ = collectors

    assert bridge.handle_message(TELEMETRY_TOPIC, telemetry_payload().encode("utf-8")) is True
    assert telemetry.messages


def test_telemetry_accepts_null_from_a_broken_dht(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    """contrato-mqtt: `temp_c` e `humidity_pct` vêm `null` quando o DHT22 falha."""
    telemetry, _, _ = collectors

    payload = telemetry_payload(temp_c=None, humidity_pct=None)
    payload = json.dumps(json.loads(payload) | {"temp_c": None, "humidity_pct": None})

    assert bridge.handle_message(TELEMETRY_TOPIC, payload) is True
    assert telemetry.messages[0].temp_c is None
    assert telemetry.messages[0].humidity_pct is None


def test_telemetry_without_fire_conditions_is_accepted(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    """O campo é **omitido** quando o ESP32 não conhece nenhuma das três condições (E6)."""
    telemetry, _, _ = collectors
    payload = load_fixture("telemetry_sample.json")
    del payload["fire_conditions"]

    assert bridge.handle_message(TELEMETRY_TOPIC, json.dumps(payload)) is True
    assert telemetry.messages[0].fire_conditions is None


def test_telemetry_with_unsynced_clock_is_accepted(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    """contrato-mqtt: `ts: 0` enquanto o NTP não sincroniza; quem grava usa a hora de recepção."""
    telemetry, _, _ = collectors

    assert bridge.handle_message(TELEMETRY_TOPIC, telemetry_payload(ts=0)) is True
    assert telemetry.messages[0].ts == 0


def test_telemetry_ignores_unknown_fields(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    """contrato-mqtt: acrescentar um campo é compatível; a API ignora o que não conhece."""
    telemetry, _, _ = collectors

    assert bridge.handle_message(TELEMETRY_TOPIC, telemetry_payload(campo_novo=123)) is True
    assert telemetry.messages


@pytest.mark.parametrize(
    "changes",
    [
        {"alert_level": "roxo"},
        {"fire_conditions": 4},
        {"tilt_limit_deg": 0},
        {"seq": -1},
        {"ts": -1},
    ],
)
def test_telemetry_outside_the_contract_is_discarded(
    bridge: MqttBridge,
    collectors: tuple[Collector, Collector, Collector],
    changes: dict[str, Any],
) -> None:
    telemetry, _, _ = collectors

    assert bridge.handle_message(TELEMETRY_TOPIC, telemetry_payload(**changes)) is False
    assert telemetry.messages == []


def test_telemetry_missing_a_required_field_is_discarded(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    telemetry, _, _ = collectors
    payload = load_fixture("telemetry_sample.json")
    del payload["roll_deg"]

    assert bridge.handle_message(TELEMETRY_TOPIC, json.dumps(payload)) is False
    assert telemetry.messages == []


@pytest.mark.parametrize("payload", ["{isso não é json", "[]", '"texto"', ""])
def test_broken_payload_is_discarded_without_breaking_the_bridge(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector], payload: str
) -> None:
    """Critério de aceite: payload inválido → descarte, e a ponte continua funcionando."""
    telemetry, _, _ = collectors

    assert bridge.handle_message(TELEMETRY_TOPIC, payload) is False

    assert bridge.handle_message(TELEMETRY_TOPIC, telemetry_payload()) is True
    assert len(telemetry.messages) == 1


def test_payload_from_another_device_is_discarded(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    """O broker é público: se o `device_id` do payload não bate com o do tópico, descarta."""
    telemetry, _, _ = collectors

    assert (
        bridge.handle_message(TELEMETRY_TOPIC, telemetry_payload(device_id="tractor-99")) is False
    )
    assert telemetry.messages == []


# --- Eventos -----------------------------------------------------------------------------------


def test_rollover_event_keeps_the_thirty_second_context(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    _, events, _ = collectors

    assert bridge.handle_message(EVENTS_TOPIC, event_payload()) is True

    message = events.messages[0]
    assert message.type is EventType.ROLLOVER
    assert message.context is not None
    assert message.context.fields == ["t_s", "roll_deg", "pitch_deg", "accel_g"]
    assert len(message.context.rows) == 30
    assert message.context.rows[0][0] == -29.0
    assert message.context.rows[-1][0] == 0.0


def test_the_same_event_arriving_three_times_is_processed_once(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    """Critério de aceite: o ESP32 publica cada evento 3 vezes com o mesmo `event_id`."""
    _, events, _ = collectors
    payload = event_payload()

    results = [bridge.handle_message(EVENTS_TOPIC, payload) for _ in range(3)]

    assert results == [True, False, False]
    assert len(events.messages) == 1


def test_the_event_callback_receives_the_raw_payload(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    """I5: o hash de integridade é dos **bytes que chegaram**, não do JSON reserializado."""
    _, events, _ = collectors
    payload = event_payload()

    bridge.handle_message(EVENTS_TOPIC, payload.encode("utf-8"))

    assert events.raw_payloads == [payload.encode("utf-8")]


def test_a_different_event_id_is_processed(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    _, events, _ = collectors

    bridge.handle_message(EVENTS_TOPIC, event_payload())
    bridge.handle_message(EVENTS_TOPIC, event_payload(event_id="tractor-01-1789500100-8"))

    assert len(events.messages) == 2


def test_event_without_context_is_accepted(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    """`tilt_alert` e `limit_applied` não levam `context` (contrato-mqtt)."""
    _, events, _ = collectors
    payload = {
        "device_id": DEVICE_ID,
        "event_id": "tractor-01-1789500000-1",
        "ts": 1789500000,
        "type": "tilt_alert",
        "roll_deg": 13,
        "tilt_limit_deg": 10,
    }

    assert bridge.handle_message(EVENTS_TOPIC, json.dumps(payload)) is True
    assert events.messages[0].context is None
    assert events.messages[0].type is EventType.TILT_ALERT


def test_event_with_an_unknown_type_is_discarded(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    _, events, _ = collectors

    assert bridge.handle_message(EVENTS_TOPIC, event_payload(type="explosao")) is False
    assert events.messages == []


# --- Status ------------------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["online", "offline"])
def test_status_is_accepted(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector], state: str
) -> None:
    _, _, status = collectors

    assert bridge.handle_message(STATUS_TOPIC, status_payload(state)) is True
    assert status.messages[0].state is DeviceState(state)


def test_status_with_an_unknown_state_is_discarded(
    bridge: MqttBridge, collectors: tuple[Collector, Collector, Collector]
) -> None:
    _, _, status = collectors

    assert bridge.handle_message(STATUS_TOPIC, status_payload("dormindo")) is False
    assert status.messages == []


# --- Deduplicação (estrutura) --------------------------------------------------------------------


def test_recent_event_ids_forgets_the_oldest_first() -> None:
    recent = RecentEventIds(maxlen=2)

    assert recent.add("a") is True
    assert recent.add("b") is True
    # Rever "a" o deixa como o mais recente, então quem sai na entrada de "c" é "b".
    assert recent.add("a") is False
    assert recent.add("c") is True
    assert recent.add("c") is False
    # "b" já foi esquecido e volta a contar como evento novo.
    assert recent.add("b") is True


def test_recent_event_ids_default_matches_the_feature() -> None:
    assert RECENT_EVENT_IDS == 500


# --- Publicação do config ------------------------------------------------------------------------


def test_publish_config_uses_qos_one_and_retained(settings: Settings) -> None:
    """Critério de aceite: retained é o que entrega o último limite a quem acabou de conectar."""
    client = FakePahoClient()
    bridge = MqttBridge(settings, client=client)

    assert bridge.publish_config(DEVICE_ID, ConfigMessage(tilt_limit_deg=10.0)) is True

    sent = client.published[0]
    assert sent["topic"] == f"{PREFIX}/devices/{DEVICE_ID}/config"
    assert sent["qos"] == CONFIG_QOS
    assert sent["retain"] is True
    assert json.loads(sent["payload"]) == {"tilt_limit_deg": 10.0}


def test_publish_config_sends_every_field_that_was_filled(settings: Settings) -> None:
    client = FakePahoClient()
    bridge = MqttBridge(settings, client=client)
    config = ConfigMessage(
        tilt_limit_deg=10.0,
        warn_ratio=0.8,
        soil_state="saturated",
        risk_level="red",
        wind_max_kmh=22.0,
        valid_until=1789550000,
        reason="42 mm de chuva em 72 h",
    )

    bridge.publish_config(DEVICE_ID, config)

    assert json.loads(client.published[0]["payload"])["reason"] == "42 mm de chuva em 72 h"


def test_publish_config_reports_a_refused_publication(settings: Settings) -> None:
    bridge = MqttBridge(settings, client=FakePahoClient(rc=mqtt.MQTT_ERR_NO_CONN))

    assert bridge.publish_config(DEVICE_ID, ConfigMessage(tilt_limit_deg=10.0)) is False


# --- Conexão -------------------------------------------------------------------------------------


def test_the_bridge_starts_even_with_the_broker_down(settings: Settings) -> None:
    """Critério de aceite: a API sobe com o broker fora do ar.

    A porta local fechada faz a conexão falhar na hora, sem sair da máquina. `start()` não pode
    levantar exceção: quem reconecta é o próprio paho, em segundo plano.
    """
    offline = settings.model_copy(update={"mqtt_host": "127.0.0.1", "mqtt_port": CLOSED_PORT})
    bridge = MqttBridge(offline)

    bridge.start()
    try:
        assert bridge.is_connected is False
    finally:
        bridge.stop()

    assert bridge.is_connected is False


# --- Keepalive: é ele que descobre a queda quando não chega mais tráfego (D-1) -------------------


def test_the_bridge_connects_with_an_explicit_short_keepalive(settings: Settings) -> None:
    """Com o padrão do paho (60 s) o painel ao vivo ficava cego ~58 s depois de o broker cair."""
    client = RecordingPahoClient()
    bridge = MqttBridge(settings, client=client)

    bridge.start()

    assert client.connect_args == (settings.mqtt_host, settings.mqtt_port)
    assert client.connect_kwargs["keepalive"] == settings.mqtt_keepalive_s
    assert client.loop_started is True


def test_the_default_keepalive_keeps_the_worst_case_around_half_a_minute() -> None:
    """A detecção leva de 1× a 2× o keepalive: com 15 s, 30 s no pior caso — não os ~60 s."""
    keepalive = Settings().mqtt_keepalive_s

    assert keepalive == 15
    assert keepalive * 2 <= 30


def test_the_keepalive_can_be_tuned_by_environment(settings: Settings) -> None:
    client = RecordingPahoClient()
    bridge = MqttBridge(settings.model_copy(update={"mqtt_keepalive_s": 5}), client=client)

    bridge.start()

    assert client.connect_kwargs["keepalive"] == 5


def test_the_backoff_of_the_reconnection_is_configured(settings: Settings) -> None:
    client = RecordingPahoClient()

    MqttBridge(settings, client=client).start()

    assert client.reconnect_kwargs == {
        "min_delay": settings.mqtt_reconnect_min_s,
        "max_delay": settings.mqtt_reconnect_max_s,
    }


# --- Payload vazio em tópico retained é "apagou o retained", não erro (I6) ----------------------


@pytest.mark.parametrize("suffix", ["status", "config"])
def test_clearing_a_retained_topic_is_not_an_error(
    bridge: MqttBridge,
    collectors: tuple[Collector, Collector, Collector],
    caplog: pytest.LogCaptureFixture,
    suffix: str,
) -> None:
    """Apagar um retained é publicar zero byte. O `--clean-retained` do simulador faz isso.

    Não basta a mensagem ser descartada: ela não pode virar WARNING, senão polui a trilha de
    rastreabilidade que a I5 produz e que é evidência da demo.
    """
    _, _, status = collectors

    with caplog.at_level(logging.DEBUG, logger="app.mqtt.bridge"):
        handled = bridge.handle_message(f"{PREFIX}/devices/{DEVICE_ID}/{suffix}", b"")

    assert handled is False
    assert status.messages == []
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_a_cleared_retained_does_not_reach_the_warning_log(
    bridge: MqttBridge, caplog: pytest.LogCaptureFixture
) -> None:
    """A trilha de rastreabilidade (I5) é evidência da demo: alarme falso ali atrapalha."""
    with caplog.at_level(logging.DEBUG, logger="app.mqtt.bridge"):
        bridge.handle_message(STATUS_TOPIC, b"")

    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("Retained apagado" in record.message for record in caplog.records)


def test_a_non_empty_invalid_payload_still_warns(
    bridge: MqttBridge, caplog: pytest.LogCaptureFixture
) -> None:
    """O silêncio vale só para o payload **vazio**: JSON quebrado continua sendo aviso."""
    with caplog.at_level(logging.DEBUG, logger="app.mqtt.bridge"):
        bridge.handle_message(STATUS_TOPIC, b"{quebrado")

    assert [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_an_empty_payload_on_a_non_retained_topic_still_warns(
    bridge: MqttBridge, caplog: pytest.LogCaptureFixture
) -> None:
    """Telemetria e eventos nunca são retained: vazio ali é mensagem malformada de verdade."""
    with caplog.at_level(logging.DEBUG, logger="app.mqtt.bridge"):
        bridge.handle_message(TELEMETRY_TOPIC, b"")

    assert [record for record in caplog.records if record.levelno >= logging.WARNING]

#!/usr/bin/env python3
"""Simulador de dispositivo ESP32 para testes de integração (I6).

Publica no broker exatamente o que o firmware publica, seguindo
[document/contrato-mqtt.md](../document/contrato-mqtt.md): `telemetry`, `events` (3 cópias com o
mesmo `event_id`), `status` retained e LWT `offline`. Também **assina `config`** e imprime o
limite que a API mandou, o que valida a W4 sem abrir o Wokwi.

⚠️ **Isto é ferramenta de teste, não substituto do Wokwi.** A demo usa o dispositivo simulado no
Wokwi (`iot/`); este script existe para rodar os cenários de ponta a ponta sem depender do
navegador, e para medir tempos.

Uso:

```bash
uv run --project api python scripts/simulate_device.py --scenario normal
uv run --project api python scripts/simulate_device.py --scenario rajada --count 100
uv run --project api python scripts/simulate_device.py --scenario queda --summary-json /tmp/s.json
```

O `device_id` padrão é `tractor-02` (existe no catálogo da W1 e **não** é o `tractor-01` da demo),
para nenhum cenário sujar o `status`/`config` retained do equipamento que vai ao ar na
apresentação. Com `--clean-retained` o script ainda apaga o que deixou retained ao terminar.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import paho.mqtt.client as mqtt

# --- contrato-mqtt -------------------------------------------------------------------------------

DEFAULT_HOST = os.environ.get("AGRISHIELD_MQTT_HOST", "broker.hivemq.com")
DEFAULT_PORT = int(os.environ.get("AGRISHIELD_MQTT_PORT", "1883"))
DEFAULT_PREFIX = os.environ.get("AGRISHIELD_MQTT_TOPIC_PREFIX", "agrishield/fiap-sompo-2026")
# Não é o `tractor-01` da demo, de propósito (ver docstring do módulo).
DEFAULT_DEVICE_ID = "tractor-02"

TELEMETRY_SUFFIX = "telemetry"
EVENTS_SUFFIX = "events"
STATUS_SUFFIX = "status"
CONFIG_SUFFIX = "config"

# O ESP32 publica telemetria e eventos em QoS 0 (limitação do PubSubClient) e `status` em QoS 1.
TELEMETRY_QOS = 0
STATUS_QOS = 1
# Cada evento vai 3 vezes, com 500 ms de intervalo e o **mesmo** `event_id`.
EVENT_COPIES = 3
EVENT_COPY_INTERVAL_S = 0.5

# --- firmware (E2, E5) ---------------------------------------------------------------------------

DEFAULT_TILT_LIMIT_DEG = 10.0
DEFAULT_WARN_RATIO = 0.8
ROLLOVER_TILT_DEG = 45.0
ROLLOVER_IMPACT_G = 2.5
# Janela de contexto que acompanha `rollover` e `incident_report`: 30 linhas a 1 Hz.
CONTEXT_FIELDS = ["t_s", "roll_deg", "pitch_deg", "accel_g"]
CONTEXT_ROWS = 30

# Keepalive curto de propósito: o broker só publica o LWT depois de 1,5 × keepalive sem sinal,
# e o cenário `queda` precisa ver o `offline` chegar dentro do teste.
DEFAULT_KEEPALIVE_S = 5


def arduino_number(value: float | None, digits: int = 2) -> float | int | None:
    """Arredonda como a ArduinoJson serializa: `10.0` vira `10`, sem casa decimal.

    O contrato avisa que float inteiro chega como `int`; reproduzir isso aqui é o que faz o
    cenário exercitar a coerção do pydantic de verdade (ver `tests/fixtures/telemetry_sample.json`).
    """
    if value is None:
        return None
    rounded = round(float(value), digits)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def alert_level_for(roll_deg: float, pitch_deg: float, limit_deg: float, accel_g: float) -> str:
    """Nível do alerta local, como o firmware calcula (E2/E5, sem a histerese de 1°).

    A histerese não é reproduzida porque os cenários sobem e descem a inclinação em degraus
    determinísticos, longe da borda — o que o teste precisa é do nível certo, não do piscar.
    """
    tilt = max(abs(roll_deg), abs(pitch_deg))
    if tilt >= ROLLOVER_TILT_DEG or accel_g >= ROLLOVER_IMPACT_G:
        return "rollover"
    if tilt >= limit_deg:
        return "red"
    if tilt >= DEFAULT_WARN_RATIO * limit_deg:
        return "yellow"
    return "green"


# --- registro do que saiu e do que voltou ----------------------------------------------------


@dataclass
class Sent:
    """Uma publicação feita pelo simulador."""

    n: int
    at: float
    suffix: str
    topic: str
    payload: str
    valid: bool
    note: str = ""


@dataclass
class Received:
    """Um `config` recebido da API (W4)."""

    at: float
    topic: str
    payload: str
    retained: bool


@dataclass
class Summary:
    """Resumo da execução, salvo com `--summary-json` e consumido pelos testes `e2e`."""

    scenario: str
    device_id: str
    prefix: str
    broker: str
    started_at: float = 0.0
    finished_at: float = 0.0
    sent: list[Sent] = field(default_factory=list)
    configs: list[Received] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def valid_sent(self) -> list[Sent]:
        return [item for item in self.sent if item.valid]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["counts"] = {
            "sent": len(self.sent),
            "valid": len(self.valid_sent),
            "invalid": len(self.sent) - len(self.valid_sent),
            "telemetry_valid": len([i for i in self.valid_sent if i.suffix == TELEMETRY_SUFFIX]),
            "events_sent": len([i for i in self.sent if i.suffix == EVENTS_SUFFIX]),
            "configs_received": len(self.configs),
        }
        return data


class DeviceSimulator:
    """Um ESP32 de mentira: conecta, publica no contrato e ouve o `config`."""

    def __init__(
        self,
        device_id: str = DEFAULT_DEVICE_ID,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        prefix: str = DEFAULT_PREFIX,
        tilt_limit_deg: float = DEFAULT_TILT_LIMIT_DEG,
        qos: int = TELEMETRY_QOS,
        keepalive: int = DEFAULT_KEEPALIVE_S,
        client_suffix: str | None = None,
        verbose: bool = True,
    ) -> None:
        self.device_id = device_id
        self.host = host
        self.port = port
        self.prefix = prefix
        self.tilt_limit_deg = tilt_limit_deg
        self.qos = qos
        self.keepalive = keepalive
        self.verbose = verbose
        self.seq = 0
        self.event_counter = 0
        self.summary = Summary(
            scenario="", device_id=device_id, prefix=prefix, broker=f"{host}:{port}"
        )
        self._client_id = f"agrishield-sim-{client_suffix or os.getpid()}"
        self._client: mqtt.Client | None = None
        self._connected = False

    # --- conexão ---------------------------------------------------------------------------

    def topic(self, suffix: str) -> str:
        return f"{self.prefix}/devices/{self.device_id}/{suffix}"

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def connect(self, timeout_s: float = 15.0, announce: bool = True) -> float:
        """Conecta, registra o LWT `offline` e (por padrão) anuncia `online` retained.

        Devolve quantos segundos a conexão levou.
        """
        started = time.time()
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        # LWT: se o dispositivo cair sem avisar, o broker publica `offline` retained por ele.
        client.will_set(
            self.topic(STATUS_SUFFIX),
            json.dumps({"device_id": self.device_id, "state": "offline"}),
            qos=STATUS_QOS,
            retain=True,
        )
        self._client = client
        client.connect(self.host, self.port, keepalive=self.keepalive)
        client.loop_start()

        deadline = time.time() + timeout_s
        while not self._connected and time.time() < deadline:
            time.sleep(0.05)
        if not self._connected:
            raise TimeoutError(f"Não conectou em {self.host}:{self.port} em {timeout_s:.0f} s.")

        elapsed = time.time() - started
        self.log(f"[sim] conectado a {self.host}:{self.port} em {elapsed:.2f} s")
        if announce:
            self.publish_status("online")
        return elapsed

    def disconnect(self, announce_offline: bool = True) -> None:
        """Sai de forma limpa (DISCONNECT). O LWT **não** dispara neste caminho."""
        if self._client is None:
            return
        if announce_offline:
            self.publish_status("offline")
        self._client.disconnect()
        self._client.loop_stop()
        self._connected = False
        self._client = None
        self.log("[sim] desconectado (saída limpa)")

    def kill(self) -> None:
        """Mata a conexão sem DISCONNECT, como um ESP32 que perde energia ou Wi-Fi.

        É o único jeito de fazer o broker publicar o LWT `offline` — ele só o faz quando a
        conexão morre sem aviso, depois de 1,5 × keepalive sem sinal.
        """
        if self._client is None:
            return
        try:
            sock = self._client.socket()
            if sock is not None:
                sock.close()
        except Exception as error:  # noqa: BLE001 — é uma queda simulada, falhar aqui é normal
            self.log(f"[sim] aviso ao matar o socket: {error}")
        self._client.loop_stop()
        self._connected = False
        self._client = None
        self.log(
            f"[sim] conexão morta sem DISCONNECT (o LWT dispara em ~{self.keepalive * 1.5:.0f} s)"
        )

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        rc: Any,
        props: Any = None,
    ) -> None:
        if getattr(rc, "is_failure", False):
            self.log(f"[sim] conexão recusada: {rc}")
            return
        self._connected = True
        client.subscribe(self.topic(CONFIG_SUFFIX), qos=1)

    def _on_disconnect(self, *args: Any, **kwargs: Any) -> None:
        self._connected = False

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        payload = message.payload.decode("utf-8", errors="replace")
        if not payload:
            # Payload vazio retained é o apagar de uma mensagem retida, não um `config`.
            return
        self.summary.configs.append(
            Received(at=time.time(), topic=message.topic, payload=payload, retained=message.retain)
        )
        limit = "?"
        with contextlib.suppress(json.JSONDecodeError):
            limit = str(json.loads(payload).get("tilt_limit_deg", "?"))
        self.log(
            f"[sim] config recebido (retained={message.retain}): limite={limit}° | {payload[:160]}"
        )

    # --- publicação ------------------------------------------------------------------------

    def _publish(
        self,
        suffix: str,
        payload: str,
        qos: int | None = None,
        retain: bool = False,
        valid: bool = True,
        note: str = "",
    ) -> Sent:
        if self._client is None:
            raise RuntimeError("Simulador desconectado: chame connect() antes de publicar.")
        topic = self.topic(suffix)
        info = self._client.publish(
            topic, payload, qos=self.qos if qos is None else qos, retain=retain
        )
        # Espera o pacote sair do cliente; em QoS 0 é o que garante que a perda medida no cenário
        # `rajada` seja perda de rede/broker, e não mensagem parada na fila do simulador.
        info.wait_for_publish(timeout=10)
        record = Sent(
            n=len(self.summary.sent) + 1,
            at=time.time(),
            suffix=suffix,
            topic=topic,
            payload=payload,
            valid=valid,
            note=note,
        )
        self.summary.sent.append(record)
        return record

    def publish_status(self, state: str) -> Sent:
        payload = json.dumps({"device_id": self.device_id, "state": state})
        record = self._publish(STATUS_SUFFIX, payload, qos=STATUS_QOS, retain=True, note=state)
        self.log(f"[sim] status -> {state} (retained)")
        return record

    def telemetry_payload(
        self,
        roll_deg: float,
        pitch_deg: float = -3.1,
        accel_g: float = 1.01,
        temp_c: float | None = 31.5,
        humidity_pct: float | None = 28.0,
        fire_conditions: int | None = 2,
        ts: int | None = None,
    ) -> dict[str, Any]:
        """Monta a telemetria do contrato, já com a serialização da ArduinoJson."""
        self.seq += 1
        level = alert_level_for(roll_deg, pitch_deg, self.tilt_limit_deg, accel_g)
        return {
            "device_id": self.device_id,
            "ts": int(time.time()) if ts is None else ts,
            "seq": self.seq,
            "roll_deg": arduino_number(roll_deg),
            "pitch_deg": arduino_number(pitch_deg),
            "accel_g": arduino_number(accel_g),
            "temp_c": arduino_number(temp_c, 1),
            "humidity_pct": arduino_number(humidity_pct, 1),
            "fire_conditions": fire_conditions,
            "tilt_limit_deg": arduino_number(self.tilt_limit_deg, 1),
            "alert_level": level,
        }

    def publish_telemetry(self, **kwargs: Any) -> Sent:
        payload = self.telemetry_payload(**kwargs)
        record = self._publish(TELEMETRY_SUFFIX, json.dumps(payload, separators=(",", ":")))
        self.log(
            f"[sim] telemetria seq={payload['seq']} roll={payload['roll_deg']}° "
            f"nível={payload['alert_level']}"
        )
        return record

    def next_event_id(self, ts: int) -> str:
        self.event_counter += 1
        return f"{self.device_id}-{ts}-{self.event_counter}"

    def publish_event(
        self,
        event_type: str,
        roll_deg: float,
        pitch_deg: float = -3.1,
        accel_g: float = 1.01,
        with_context: bool = False,
        copies: int = EVENT_COPIES,
    ) -> tuple[str, list[Sent]]:
        """Publica um evento `copies` vezes com o **mesmo** `event_id` (contrato-mqtt)."""
        ts = int(time.time())
        event_id = self.next_event_id(ts)
        payload: dict[str, Any] = {
            "device_id": self.device_id,
            "event_id": event_id,
            "ts": ts,
            "type": event_type,
            "roll_deg": arduino_number(roll_deg),
            "pitch_deg": arduino_number(pitch_deg),
            "accel_g": arduino_number(accel_g),
            "tilt_limit_deg": arduino_number(self.tilt_limit_deg, 1),
        }
        if with_context:
            payload["context"] = self._context(roll_deg, pitch_deg, accel_g)

        text = json.dumps(payload, separators=(",", ":"))
        self.log(f"[sim] evento '{event_type}' id={event_id} ({len(text)} bytes) × {copies} cópias")
        records = []
        for copy_index in range(copies):
            if copy_index:
                time.sleep(EVENT_COPY_INTERVAL_S)
            records.append(
                self._publish(EVENTS_SUFFIX, text, note=f"{event_type} cópia {copy_index + 1}")
            )
        return event_id, records

    def _context(self, roll_deg: float, pitch_deg: float, accel_g: float) -> dict[str, Any]:
        """30 linhas a 1 Hz, da mais antiga (`-29`) até a do evento (`0`)."""
        rows = []
        for index in range(CONTEXT_ROWS):
            t_s = index - (CONTEXT_ROWS - 1)
            if index < 10:
                row_roll, row_accel = 9.8, 1.0
            elif index < CONTEXT_ROWS - 1:
                # Rampa determinística de 9,8° até quase o valor do evento.
                ratio = (index - 9) / (CONTEXT_ROWS - 10)
                row_roll = round(9.8 + (roll_deg - 9.8) * ratio, 1)
                row_accel = round(1.0 + (accel_g - 1.0) * ratio, 2)
            else:
                row_roll, row_accel = roll_deg, accel_g
            rows.append(
                [
                    t_s,
                    arduino_number(row_roll, 1),
                    arduino_number(pitch_deg, 1),
                    arduino_number(row_accel),
                ]
            )
        return {"fields": list(CONTEXT_FIELDS), "rows": rows}

    def publish_raw(self, suffix: str, payload: str, note: str) -> Sent:
        """Publica um payload cru — é assim que o cenário `sujo` manda lixo de propósito."""
        record = self._publish(suffix, payload, valid=False, note=note)
        self.log(f"[sim] payload INVÁLIDO ({note}): {payload[:120]}")
        return record

    def clean_retained(self) -> None:
        """Apaga o que ficou retained em `status` e `config` deste equipamento.

        Payload vazio com retained é como o MQTT apaga uma mensagem retida. Sem isso, o broker
        público guardaria para sempre o último `status` do simulador.
        """
        if self._client is None:
            return
        for suffix in (STATUS_SUFFIX, CONFIG_SUFFIX):
            info = self._client.publish(self.topic(suffix), payload=b"", qos=1, retain=True)
            info.wait_for_publish(timeout=10)
            self.log(f"[sim] retained apagado em {self.topic(suffix)}")

    def wait_for_config(self, timeout_s: float, since: float | None = None) -> Received | None:
        """Espera um `config` chegar (W4). Devolve o primeiro recebido depois de `since`."""
        floor = since if since is not None else 0.0
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for item in self.summary.configs:
                if item.at >= floor:
                    return item
            time.sleep(0.02)
        return None


# --- cenários ------------------------------------------------------------------------------------


def scenario_normal(sim: DeviceSimulator, count: int, interval: float) -> None:
    """Operação tranquila: inclinação baixa, uma leitura a cada `interval` segundos."""
    rolls = [4.2, 5.1, 6.3, 5.4, 4.8, 6.0]
    for index in range(count):
        if index:
            time.sleep(interval)
        sim.publish_telemetry(roll_deg=rolls[index % len(rolls)])


def scenario_alerta(sim: DeviceSimulator, count: int, interval: float) -> None:
    """A inclinação sobe até passar do limite e o dispositivo emite um `tilt_alert`."""
    limit = sim.tilt_limit_deg
    alerted = False
    for index in range(count):
        if index:
            time.sleep(interval)
        roll = round(4.0 + (limit * 1.3 - 4.0) * index / max(count - 1, 1), 1)
        sim.publish_telemetry(roll_deg=roll)
        if roll >= limit and not alerted:
            alerted = True
            event_id, _ = sim.publish_event("tilt_alert", roll_deg=roll)
            sim.summary.extra["tilt_alert_event_id"] = event_id
            sim.summary.extra["tilt_alert_roll_deg"] = roll
    sim.summary.extra["alerted"] = alerted


def scenario_capotamento(sim: DeviceSimulator, count: int, interval: float) -> None:
    """60° por 3 s: telemetria em `rollover` e o evento com a janela de 30 s de contexto."""
    for index in range(3):
        if index:
            time.sleep(1.0)
        sim.publish_telemetry(roll_deg=60.0, pitch_deg=2.3, accel_g=2.8)
    event_id, records = sim.publish_event(
        "rollover", roll_deg=60.0, pitch_deg=2.3, accel_g=2.8, with_context=True
    )
    sim.summary.extra["rollover_event_id"] = event_id
    sim.summary.extra["rollover_copies"] = len(records)
    sim.summary.extra["rollover_payload_bytes"] = len(records[0].payload.encode("utf-8"))


# Lixo publicado pelo cenário `sujo`. Cada linha é um jeito diferente de o mundo real quebrar.
DIRTY_PAYLOADS: list[tuple[str, str, str]] = [
    (TELEMETRY_SUFFIX, '{"device_id":"DEVICE","ts":1789500000,"seq":1,', "JSON quebrado"),
    (
        TELEMETRY_SUFFIX,
        '{"device_id":"DEVICE","ts":1789500000,"seq":2,"roll_deg":5,"pitch_deg":-3,'
        '"accel_g":1,"alert_level":"green"}',
        "campo obrigatório faltando (tilt_limit_deg)",
    ),
    (
        TELEMETRY_SUFFIX,
        '{"device_id":"DEVICE","ts":1789500000,"seq":3,"roll_deg":5,"pitch_deg":-3,'
        '"accel_g":1,"fire_conditions":9,"tilt_limit_deg":10,"alert_level":"green"}',
        "valor fora de faixa (fire_conditions=9)",
    ),
    (
        TELEMETRY_SUFFIX,
        '{"device_id":"DEVICE","ts":1789500000,"seq":4,"roll_deg":5,"pitch_deg":-3,'
        '"accel_g":1,"tilt_limit_deg":0,"alert_level":"green"}',
        "valor fora de faixa (tilt_limit_deg=0)",
    ),
    (
        TELEMETRY_SUFFIX,
        '{"device_id":"DEVICE","ts":1789500000,"seq":5,"roll_deg":5,"pitch_deg":-3,'
        '"accel_g":1,"tilt_limit_deg":10,"alert_level":"purple"}',
        "enum desconhecido (alert_level=purple)",
    ),
    (
        TELEMETRY_SUFFIX,
        '{"device_id":"intruso-99","ts":1789500000,"seq":6,"roll_deg":5,"pitch_deg":-3,'
        '"accel_g":1,"tilt_limit_deg":10,"alert_level":"green"}',
        "device_id do payload diferente do tópico",
    ),
    (TELEMETRY_SUFFIX, "[1,2,3]", "JSON que não é objeto"),
    (TELEMETRY_SUFFIX, "", "payload vazio"),
    (
        EVENTS_SUFFIX,
        '{"device_id":"DEVICE","ts":1789500000,"type":"tilt_alert","roll_deg":12}',
        "evento sem event_id",
    ),
    (
        EVENTS_SUFFIX,
        '{"device_id":"DEVICE","event_id":"x-1","ts":1789500000,"type":"aliens","roll_deg":12}',
        "tipo de evento desconhecido",
    ),
    (STATUS_SUFFIX, '{"device_id":"DEVICE","state":"talvez"}', "estado inválido no status"),
]


def scenario_sujo(sim: DeviceSimulator, count: int, interval: float) -> None:
    """Payloads inválidos, um de cada tipo, e uma leitura boa no fim (a sentinela).

    A sentinela é o que prova que a ponte continuou viva depois do lixo: se ela for gravada, a
    thread do MQTT sobreviveu a todos os descartes.
    """
    for suffix, template, note in DIRTY_PAYLOADS:
        sim.publish_raw(suffix, template.replace("DEVICE", sim.device_id), note)
        time.sleep(0.1)
    sentinel = sim.publish_telemetry(roll_deg=7.7)
    sim.summary.extra["sentinel_seq"] = json.loads(sentinel.payload)["seq"]
    sim.summary.extra["dirty_count"] = len(DIRTY_PAYLOADS)


def scenario_rajada(sim: DeviceSimulator, count: int, interval: float) -> None:
    """`count` mensagens seguidas (padrão 100), para medir perda e duplicidade."""
    started = time.time()
    for index in range(count):
        if index and interval:
            time.sleep(interval)
        # Inclinação determinística e diferente em cada mensagem: a comparação campo a campo do
        # teste de consistência só vale se as leituras não forem todas iguais.
        roll = round(3.0 + 6.0 * math.sin(index / 7.0), 2)
        sim.publish_telemetry(roll_deg=roll)
    elapsed = time.time() - started
    sim.summary.extra["burst_count"] = count
    sim.summary.extra["burst_seconds"] = round(elapsed, 3)
    sim.summary.extra["burst_rate_msg_s"] = round(count / elapsed, 1) if elapsed else None


def scenario_queda(sim: DeviceSimulator, count: int, interval: float, down_seconds: float) -> None:
    """Publica, mata a conexão sem avisar (LWT), espera e reconecta.

    Mede quanto tempo leva entre voltar a ter conexão e a primeira mensagem nova sair.
    """
    for _ in range(2):
        sim.publish_telemetry(roll_deg=5.5)
        time.sleep(0.5)

    killed_at = time.time()
    sim.kill()
    sim.summary.extra["killed_at"] = killed_at
    sim.log(f"[sim] fora do ar por {down_seconds:.0f} s")
    time.sleep(down_seconds)

    reconnect_started = time.time()
    sim.connect()
    first = sim.publish_telemetry(roll_deg=6.6)
    sim.summary.extra["down_seconds"] = down_seconds
    sim.summary.extra["reconnect_started_at"] = reconnect_started
    sim.summary.extra["recovery_seconds"] = round(first.at - reconnect_started, 3)
    sim.summary.extra["first_message_after_reconnect_seq"] = json.loads(first.payload)["seq"]
    for _ in range(2):
        time.sleep(0.5)
        sim.publish_telemetry(roll_deg=5.0)


SCENARIOS = ("normal", "alerta", "capotamento", "sujo", "rajada", "queda")

# Quantas mensagens e com que intervalo cada cenário roda quando ninguém passa `--count`.
DEFAULTS: dict[str, tuple[int, float]] = {
    "normal": (6, 5.0),
    "alerta": (8, 1.0),
    "capotamento": (3, 1.0),
    "sujo": (0, 0.1),
    "rajada": (100, 0.05),
    "queda": (5, 0.5),
}


def run_scenario(
    sim: DeviceSimulator, name: str, count: int, interval: float, down_seconds: float
) -> None:
    if name == "normal":
        scenario_normal(sim, count, interval)
    elif name == "alerta":
        scenario_alerta(sim, count, interval)
    elif name == "capotamento":
        scenario_capotamento(sim, count, interval)
    elif name == "sujo":
        scenario_sujo(sim, count, interval)
    elif name == "rajada":
        scenario_rajada(sim, count, interval)
    elif name == "queda":
        scenario_queda(sim, count, interval, down_seconds)
    else:  # pragma: no cover — o argparse já barra
        raise ValueError(f"Cenário desconhecido: {name}")


# --- linha de comando ------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulador de dispositivo ESP32 para os testes de integração (I6).",
        epilog="Ferramenta de teste: a demo continua sendo o dispositivo simulado no Wokwi.",
    )
    parser.add_argument("--scenario", choices=SCENARIOS, required=True, help="Cenário a rodar.")
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID, help="Padrão: tractor-02.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="Prefixo dos tópicos MQTT.")
    parser.add_argument("--count", type=int, default=None, help="Quantas mensagens publicar.")
    parser.add_argument("--interval", type=float, default=None, help="Segundos entre mensagens.")
    parser.add_argument(
        "--limit", type=float, default=DEFAULT_TILT_LIMIT_DEG, help="tilt_limit_deg em uso."
    )
    parser.add_argument("--qos", type=int, choices=(0, 1), default=TELEMETRY_QOS)
    parser.add_argument("--keepalive", type=int, default=DEFAULT_KEEPALIVE_S)
    parser.add_argument(
        "--down-seconds", type=float, default=10.0, help="Cenário `queda`: tempo fora do ar."
    )
    parser.add_argument(
        "--listen-seconds",
        type=float,
        default=0.0,
        help="Depois do cenário, fica ouvindo `config` por N segundos (valida a W4).",
    )
    parser.add_argument(
        "--clean-retained",
        action="store_true",
        help="Apaga o `status`/`config` retained deste equipamento ao terminar.",
    )
    parser.add_argument("--summary-json", default=None, help="Salva o resumo em JSON.")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    default_count, default_interval = DEFAULTS[args.scenario]
    count = args.count if args.count is not None else default_count
    interval = args.interval if args.interval is not None else default_interval

    sim = DeviceSimulator(
        device_id=args.device_id,
        host=args.host,
        port=args.port,
        prefix=args.prefix,
        tilt_limit_deg=args.limit,
        qos=args.qos,
        keepalive=args.keepalive,
        verbose=not args.quiet,
    )
    sim.summary.scenario = args.scenario
    sim.summary.started_at = time.time()
    sim.log(
        f"[sim] cenário '{args.scenario}' | device={args.device_id} | "
        f"broker={args.host}:{args.port} | prefixo={args.prefix}"
    )

    exit_code = 0
    try:
        sim.connect()
        run_scenario(sim, args.scenario, count, interval, args.down_seconds)
        if args.listen_seconds > 0:
            sim.log(f"[sim] ouvindo `config` por {args.listen_seconds:.0f} s")
            time.sleep(args.listen_seconds)
    except Exception as error:  # noqa: BLE001 — o resumo precisa sair mesmo quando algo falha
        sim.summary.extra["error"] = f"{type(error).__name__}: {error}"
        sim.log(f"[sim] ERRO: {type(error).__name__}: {error}")
        exit_code = 1
    finally:
        if args.clean_retained:
            try:
                sim.clean_retained()
            except Exception as error:  # noqa: BLE001
                sim.log(f"[sim] aviso ao limpar retained: {error}")
        # Com `--clean-retained` o `offline` do fim **não** é publicado: ele também é retained e
        # deixaria de volta no broker justamente o que acabamos de apagar.
        sim.disconnect(announce_offline=not args.clean_retained)
        sim.summary.finished_at = time.time()
        payload = sim.summary.to_dict()
        if args.summary_json:
            with open(args.summary_json, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            sim.log(f"[sim] resumo salvo em {args.summary_json}")
        counts = payload["counts"]
        sim.log(
            f"[sim] fim: {counts['sent']} publicações "
            f"({counts['valid']} válidas, {counts['invalid']} inválidas), "
            f"{counts['configs_received']} config(s) recebido(s), "
            f"{sim.summary.finished_at - sim.summary.started_at:.1f} s"
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

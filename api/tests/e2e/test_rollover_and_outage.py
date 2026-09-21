"""Capotamento (deduplicação + trilha) e queda do dispositivo (LWT + reconexão) — I6."""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.e2e.conftest import (
    API_KEY,
    INGEST_TIMEOUT_S,
    ApiServer,
    assert_online,
    db_now,
    wait_until,
)

pytestmark = pytest.mark.e2e

# Critério de aceite do I6: depois de reconectar, as mensagens voltam em menos de 30 s.
RECOVERY_BUDGET_S = 30.0
# O broker só publica o LWT depois de 1,5 × keepalive sem sinal (keepalive do simulador = 5 s).
LWT_BUDGET_S = 20.0


def test_rollover_is_stored_once_with_context_and_audit(api: ApiServer, run_simulator) -> None:
    """Cenário `capotamento`: 3 cópias do evento → 1 linha, com contexto e trilha completos."""
    since = db_now()
    run = run_simulator("capotamento")

    event_id = run.summary["extra"]["rollover_event_id"]
    copies = run.summary["extra"]["rollover_copies"]
    payload_bytes = run.summary["extra"]["rollover_payload_bytes"]
    assert copies == 3, "o firmware publica cada evento 3 vezes (contrato-mqtt)"

    events, elapsed = wait_until(lambda: api.events_since(since) or None, INGEST_TIMEOUT_S)
    assert events is not None, "o evento de capotamento não foi gravado"
    assert len(events) == 1, f"o evento foi gravado {len(events)} vezes; deveria ser 1"
    assert events[0]["event_id"] == event_id
    assert events[0]["type"] == "rollover"
    print(
        f"[e2e] capotamento: 3 cópias publicadas ({payload_bytes} bytes cada), 1 linha gravada "
        f"({event_id}), visível {elapsed:.2f} s depois"
    )

    # O contexto de 30 s chegou inteiro e idêntico ao publicado.
    published = json.loads(next(item for item in run.sent if item["suffix"] == "events")["payload"])
    stored = json.loads(events[0]["payload_json"])
    assert stored["context"]["fields"] == published["context"]["fields"]
    assert len(stored["context"]["rows"]) == 30
    assert stored["context"]["rows"] == published["context"]["rows"]

    # Integridade (I5): o hash do banco é o dos bytes exatos que saíram do dispositivo.
    raw = next(item for item in run.sent if item["suffix"] == "events")["payload"].encode("utf-8")
    integrity = api.query("SELECT * FROM event_integrity WHERE event_id = ?", (event_id,))
    assert len(integrity) == 1
    assert integrity[0]["payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert integrity[0]["payload_bytes"] == len(raw)

    # Trilha de auditoria (I5): o alerta virou decisão registrada, com entrada e saída.
    audit = api.get(
        "/audit",
        params={"entity": run.summary["device_id"], "decision_type": "alert"},
        headers={"X-API-Key": API_KEY},
    )
    assert audit.status_code == 200
    entries = [item for item in audit.json() if item["output"].get("event_id") == event_id]
    assert len(entries) == 1, "o capotamento não deixou linha em decision_log"
    entry = entries[0]
    assert entry["inputs"]["event_type"] == "rollover"
    assert entry["inputs"]["roll_deg"] == float(published["roll_deg"])
    assert entry["output"]["payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert entry["source"] == "device"

    # E o painel mostra o evento com o contexto (W5).
    listed = api.get(f"/devices/{run.summary['device_id']}/events", params={"limit": 20})
    assert listed.status_code == 200
    shown = next(item for item in listed.json() if item["event_id"] == event_id)
    assert len(shown["context"]["rows"]) == 30


def test_device_outage_triggers_lwt_and_recovers(api: ApiServer, run_simulator) -> None:
    """Cenário `queda`: o LWT marca `offline` e, ao reconectar, a telemetria volta."""
    since = db_now()
    device_id = "tractor-02"
    run = run_simulator("queda", "--down-seconds", "12", timeout_s=300.0)

    extra = run.summary["extra"]
    recovery = extra["recovery_seconds"]
    print(
        f"[e2e] queda: fora do ar por {extra['down_seconds']} s; "
        f"primeira mensagem nova {recovery:.2f} s após o início da reconexão"
    )
    assert recovery < RECOVERY_BUDGET_S

    # O broker publicou o LWT enquanto o dispositivo estava morto, e a API registrou.
    logs = api.logs()
    assert f"Equipamento {device_id} está offline." in logs, (
        "o LWT `offline` não chegou à API dentro do teste"
    )
    assert logs.rindex(f"Equipamento {device_id} está online.") > logs.index(
        f"Equipamento {device_id} está offline."
    ), "a API não registrou o `online` depois da reconexão"

    # E as mensagens posteriores à reconexão estão no banco.
    seq_after = extra["first_message_after_reconnect_seq"]
    rows, elapsed = wait_until(
        lambda: [row for row in api.telemetry_since(since) if row["seq"] >= seq_after] or None,
        INGEST_TIMEOUT_S,
    )
    assert rows is not None, "nada foi gravado depois da reconexão"
    print(
        f"[e2e] queda: {len(rows)} leitura(s) gravada(s) depois da reconexão "
        f"(a partir de seq={seq_after}), visíveis {elapsed:.2f} s depois"
    )

    status = assert_online(api, device_id)
    print(
        f"[e2e] queda: painel de volta em `{status['state']}` "
        f"({status['seconds_since_last_telemetry']} s desde a última telemetria)"
    )


def test_tilt_alert_crossing_the_limit_is_stored_once(api: ApiServer, run_simulator) -> None:
    """Cenário `alerta`: a inclinação sobe, o nível muda e o `tilt_alert` vira uma linha só."""
    since = db_now()
    run = run_simulator("alerta", "--count", "8", "--interval", "0.3")

    extra = run.summary["extra"]
    assert extra["alerted"] is True, "a inclinação subiu acima do limite e nenhum alerta saiu"
    event_id = extra["tilt_alert_event_id"]

    expected = len(run.valid_telemetry)
    rows, elapsed = wait_until(
        lambda: api.telemetry_since(since) if len(api.telemetry_since(since)) >= expected else None,
        INGEST_TIMEOUT_S,
    )
    assert rows is not None, "a telemetria do cenário `alerta` não chegou inteira"

    # O nível acompanha a subida da inclinação: 🟢 → 🟡 → 🔴, sem voltar atrás.
    levels = [row["alert_level"] for row in rows]
    assert levels[0] == "green"
    assert levels[-1] == "red"
    assert "yellow" in levels
    print(
        f"[e2e] alerta: níveis gravados {levels} (evento {event_id} em "
        f"{extra['tilt_alert_roll_deg']}°, visível {elapsed:.2f} s depois)"
    )

    events, _ = wait_until(lambda: api.events_since(since) or None, INGEST_TIMEOUT_S)
    assert events is not None, "o `tilt_alert` não foi gravado"
    assert len(events) == 1, f"as 3 cópias viraram {len(events)} linhas; deveria ser 1"
    assert events[0]["type"] == "tilt_alert"
    assert json.loads(events[0]["payload_json"]).get("context") is None

    # Alerta é decisão: tem de estar na trilha, com entrada, saída e origem (I5).
    audit = api.get(
        "/audit",
        params={"entity": run.summary["device_id"], "decision_type": "alert"},
        headers={"X-API-Key": API_KEY},
    )
    assert audit.status_code == 200
    entry = next(item for item in audit.json() if item["output"]["event_id"] == event_id)
    assert entry["inputs"]["event_type"] == "tilt_alert"
    assert entry["inputs"]["roll_deg"] == extra["tilt_alert_roll_deg"]
    assert entry["created_at"], "a decisão precisa ter horário"

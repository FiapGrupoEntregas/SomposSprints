"""Confiabilidade da coleta: rajada de 100 mensagens e payloads inválidos (I6).

São os dois cenários que o enunciado cobra de forma mais direta: *perda de mensagem* e
*tratamento de exceção*.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.conftest import INGEST_TIMEOUT_S, ApiServer, db_now, wait_until

pytestmark = pytest.mark.e2e

BURST_SIZE = 100


def test_burst_of_100_messages_has_no_loss_and_no_duplicates(api: ApiServer, run_simulator) -> None:
    """Cenário `rajada`: 100 publicadas → 100 gravadas, sem repetição."""
    since = db_now()
    run = run_simulator("rajada", "--count", str(BURST_SIZE), "--interval", "0.05")

    published = run.valid_telemetry
    assert len(published) == BURST_SIZE

    rows, elapsed = wait_until(
        lambda: (
            api.telemetry_since(since) if len(api.telemetry_since(since)) >= BURST_SIZE else None
        ),
        INGEST_TIMEOUT_S,
    )
    stored = api.telemetry_since(since)
    lost = BURST_SIZE - len(stored)
    print(
        f"[e2e] rajada: {BURST_SIZE} publicadas em "
        f"{run.summary['extra']['burst_seconds']} s "
        f"({run.summary['extra']['burst_rate_msg_s']} msg/s), {len(stored)} gravadas, "
        f"{lost} perdida(s); todas visíveis {elapsed:.2f} s após o fim da rajada"
    )
    assert rows is not None, f"{lost} mensagem(ns) da rajada não chegaram ao banco"

    # Sem duplicidade: um `seq` por mensagem, e exatamente os que foram publicados.
    stored_seqs = [row["seq"] for row in stored]
    published_seqs = [json.loads(item["payload"])["seq"] for item in published]
    assert len(stored_seqs) == len(set(stored_seqs)), "há telemetria duplicada no banco"
    assert stored_seqs == published_seqs, "a ordem ou o conteúdo dos `seq` mudou no caminho"

    # Consistência dos valores: nada de arredondamento no caminho.
    for item, row in zip(published, stored, strict=True):
        payload = json.loads(item["payload"])
        assert row["roll_deg"] == float(payload["roll_deg"])
        assert row["tilt_limit_deg"] == float(payload["tilt_limit_deg"])
        assert row["alert_level"] == payload["alert_level"]

    # E a API continua devolvendo a série inteira ao painel.
    # 100 pontos numa janela de 10 min cabem inteiros na série (o teto é 300), então a asserção
    # é direta: nada de aceitar "ou foi amostrado".
    series = api.get(f"/devices/{run.summary['device_id']}/telemetry", params={"minutes": 10})
    assert series.status_code == 200
    body = series.json()
    assert body["sampled"] is False
    assert set(published_seqs) <= {point["seq"] for point in body["points"]}


def test_dirty_payloads_are_discarded_and_api_survives(api: ApiServer, run_simulator) -> None:
    """Cenário `sujo`: nada inválido é gravado, e a sentinela prova que a ponte seguiu viva."""
    since = db_now()
    status_before = api.get("/devices/tractor-02/status").json()
    run = run_simulator("sujo")

    dirty_count = run.summary["extra"]["dirty_count"]
    sentinel_seq = run.summary["extra"]["sentinel_seq"]
    assert run.summary["counts"]["invalid"] == dirty_count

    rows, elapsed = wait_until(lambda: api.telemetry_since(since) or None, INGEST_TIMEOUT_S)
    assert rows is not None, "nem a leitura sentinela foi gravada: a ponte pode ter morrido"

    # Só a sentinela entrou.
    assert [row["seq"] for row in rows] == [sentinel_seq]
    assert api.events_since(since) == []
    print(
        f"[e2e] sujo: {dirty_count} payloads inválidos descartados, 1 sentinela gravada "
        f"(seq={sentinel_seq}, visível {elapsed:.2f} s depois)"
    )

    # O `status` inválido ("talvez") não pode ter mudado o estado do equipamento.
    status_after = api.get("/devices/tractor-02/status").json()
    assert status_after["reported_state"] in {status_before["reported_state"], "online"}
    assert status_after["reported_state"] != "talvez"

    # A API continua respondendo em todos os endpoints do painel.
    assert api.get("/health").status_code == 200
    assert api.get("/devices/tractor-02/telemetry/latest").status_code == 200
    assert api.get("/devices/tractor-02/events").status_code == 200

    # E cada descarte deixou rastro no log (rastreabilidade): uma linha por payload recusado.
    logs = api.logs()
    discards = logs.count("descartado") + logs.count("descartada")
    assert discards >= dirty_count, (
        f"o log tem {discards} descartes registrados, menos que os {dirty_count} payloads "
        "inválidos publicados: algum foi engolido sem rastro"
    )

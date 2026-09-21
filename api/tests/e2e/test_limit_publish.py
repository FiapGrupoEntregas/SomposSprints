"""Limite dinâmico chegando ao dispositivo (W4) e o tempo que isso leva — I6.

Este é o teste que valida a W4 **sem o Wokwi**: o simulador assina `config` igual ao firmware,
alguém aperta o botão "Enviar ao equipamento" (`POST .../limit/publish`) e o teste cronometra a
chegada.

⚠️ Depende da Open-Meteo, porque o limite do dia vem da previsão real (W3). Se o serviço de clima
estiver fora, a rota devolve 503 e o teste falha apontando isso — que é a informação certa.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.e2e.conftest import API_KEY, INGEST_TIMEOUT_S, ApiServer, db_now, wait_until

pytestmark = pytest.mark.e2e

# W4 — o `config` precisa chegar ao dispositivo em ≤ 3 s depois do clique.
CONFIG_BUDGET_S = 3.0


def test_published_limit_reaches_the_device_in_time(api: ApiServer, live_device) -> None:
    """Publica o limite do dia e mede quanto tempo ele leva para chegar ao equipamento."""
    device_id = live_device.device_id
    since = db_now()

    # A previsão é buscada uma vez antes da medição: o primeiro acesso à Open-Meteo enche o cache
    # da API e não faz parte do que o operador espera ao clicar (a tela já mostra o limite do dia).
    warmup = api.get(f"/devices/{device_id}/limit")
    assert warmup.status_code == 200, f"não deu para calcular o limite: {warmup.text}"

    clicked_at = time.time()
    response = api.post(f"/devices/{device_id}/limit/publish", json={})
    responded_at = time.time()
    assert response.status_code == 200, (
        f"a publicação falhou: {response.status_code} {response.text}"
    )
    body = response.json()

    received = live_device.wait_for_config(timeout_s=INGEST_TIMEOUT_S, since=clicked_at)
    assert received is not None, "o `config` não chegou ao dispositivo"
    total_latency = received.at - clicked_at

    print(
        f"[e2e] W4 — config entregue ao dispositivo em {total_latency:.3f} s depois do clique "
        f"(a API respondeu em {responded_at - clicked_at:.3f} s; broker → dispositivo levou "
        f"{received.at - responded_at:.3f} s). Orçamento: {CONFIG_BUDGET_S:.0f} s"
    )

    # O que chegou é exatamente o que a API disse ter publicado.
    assert json.loads(received.payload) == body["payload"]
    assert received.topic == body["topic"]
    assert body["payload"]["tilt_limit_deg"] == body["limit"]["tilt_limit_deg"]
    assert total_latency <= CONFIG_BUDGET_S

    # Rastreabilidade: o envio ficou registrado em `published_config` e em `decision_log` (I5).
    published = api.query(
        "SELECT * FROM published_config WHERE device_id = ? AND published_at >= ? ORDER BY id",
        (device_id, since),
    )
    assert len(published) == 1
    assert json.loads(published[0]["payload_json"]) == body["payload"]

    audit = api.get(
        "/audit",
        params={"entity": device_id, "decision_type": "tilt_limit", "limit": 5},
        headers={"X-API-Key": API_KEY},
    )
    assert audit.status_code == 200
    entry = audit.json()[0]
    assert entry["output"]["tilt_limit_deg"] == body["limit"]["tilt_limit_deg"]
    assert entry["source"] == "api"
    assert entry["rule_version"], "a decisão precisa dizer com qual versão de regra foi tomada"


def test_device_receives_retained_config_when_it_connects(api: ApiServer, live_device) -> None:
    """Um equipamento que liga depois já recebe o último limite, porque `config` é retained (W4).

    É o que garante que o ESP32 não fique com o limite de fábrica até a próxima republicação.
    """
    device_id = live_device.device_id
    api.get(f"/devices/{device_id}/limit")
    response = api.post(f"/devices/{device_id}/limit/publish", json={})
    assert response.status_code == 200
    expected = response.json()["payload"]

    # Um segundo dispositivo, recém-ligado, assinando o mesmo tópico.
    from tests.e2e.conftest import simulate_device  # noqa: PLC0415 — só este teste precisa

    newcomer = simulate_device.DeviceSimulator(
        device_id=device_id,
        host=api.mqtt_host,
        port=api.mqtt_port,
        prefix=api.prefix,
        client_suffix="newcomer",
    )
    newcomer.connect(announce=False)
    try:
        received, elapsed = wait_until(
            lambda: newcomer.summary.configs[0] if newcomer.summary.configs else None, 10.0, 0.02
        )
        assert received is not None, "o `config` retained não chegou ao dispositivo novo"
        assert received.retained is True
        assert json.loads(received.payload) == expected
        print(f"[e2e] W4 — dispositivo novo recebeu o config retained em {elapsed:.3f} s")
    finally:
        newcomer.disconnect(announce_offline=False)

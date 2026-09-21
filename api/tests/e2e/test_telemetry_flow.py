"""Fluxo completo da telemetria e os tempos que a W5 cobra (I6).

Dispositivo → broker → ponte MQTT → SQLite → endpoints do painel, com comparação campo a campo
entre o que foi publicado e o que a API devolve.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.e2e.conftest import INGEST_TIMEOUT_S, ApiServer, assert_online, db_now, wait_until

pytestmark = pytest.mark.e2e

# W5 — o nível precisa aparecer no painel em ≤ 3 s e o gráfico em ≤ 7 s depois de a inclinação
# mudar. O front (Streamlit) recarrega o bloco ao vivo a cada 2 s, então o que sobra para o
# caminho broker → ponte → banco → API é o orçamento abaixo. É esse pedaço que este teste mede.
FRONT_REFRESH_S = 2.0
LEVEL_BUDGET_S = 3.0 - FRONT_REFRESH_S
CHART_BUDGET_S = 7.0 - FRONT_REFRESH_S


def _telemetry_fields(payload: dict) -> dict:
    """Só os campos que viram coluna no banco, para a comparação campo a campo."""
    return {
        "seq": payload["seq"],
        "roll_deg": float(payload["roll_deg"]),
        "pitch_deg": float(payload["pitch_deg"]),
        "accel_g": float(payload["accel_g"]),
        "temp_c": None if payload["temp_c"] is None else float(payload["temp_c"]),
        "humidity_pct": (
            None if payload["humidity_pct"] is None else float(payload["humidity_pct"])
        ),
        "fire_conditions": payload["fire_conditions"],
        "tilt_limit_deg": float(payload["tilt_limit_deg"]),
        "alert_level": payload["alert_level"],
    }


def test_normal_scenario_reaches_database_and_endpoints(api: ApiServer, run_simulator) -> None:
    """Cenário `normal`: tudo o que o dispositivo publicou está no banco e nos endpoints."""
    since = db_now()
    run = run_simulator("normal", "--count", "6", "--interval", "0.5")

    published = run.valid_telemetry
    assert len(published) == 6, "o simulador deveria ter publicado 6 leituras"

    rows, elapsed = wait_until(
        lambda: (
            api.telemetry_since(since)
            if len(api.telemetry_since(since)) >= len(published)
            else None
        ),
        INGEST_TIMEOUT_S,
    )
    assert rows is not None, f"só {len(api.telemetry_since(since))} de 6 leituras foram gravadas"
    print(f"[e2e] 6/6 leituras gravadas; última visível {elapsed:.2f} s após o fim do cenário")

    # Consistência: campo a campo, o que entrou é o que saiu.
    for sent, row in zip(published, rows, strict=True):
        expected = _telemetry_fields(json.loads(sent["payload"]))
        got = {key: row[key] for key in expected}
        assert got == expected, f"a leitura seq={expected['seq']} chegou diferente ao banco"

    # E o mesmo vale para o que a API devolve ao front.
    last = json.loads(published[-1]["payload"])
    latest = api.get(f"/devices/{run.summary['device_id']}/telemetry/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert body["seq"] == last["seq"]
    assert body["roll_deg"] == float(last["roll_deg"])
    assert body["alert_level"] == last["alert_level"]
    assert body["tilt_limit_deg"] == float(last["tilt_limit_deg"])

    series = api.get(f"/devices/{run.summary['device_id']}/telemetry", params={"minutes": 10})
    assert series.status_code == 200
    seqs = [point["seq"] for point in series.json()["points"]]
    assert set(last["seq"] - 5 + index for index in range(6)) <= set(seqs)

    status = assert_online(api, run.summary["device_id"])
    print(
        f"[e2e] status do painel: {status['state']} "
        f"({status['seconds_since_last_telemetry']} s desde a última telemetria)"
    )


def test_panel_latency_is_within_budget(api: ApiServer, live_device) -> None:
    """Mede quanto o painel demora para ver uma mudança de inclinação (W5).

    O dispositivo muda de 5° (🟢) para 17° (🔴) e o teste cronometra dois instantes: quando
    `/telemetry/latest` passa a devolver o novo nível (o número grande do painel) e quando o
    ponto entra na série de 10 min (o gráfico).
    """
    live_device.publish_telemetry(roll_deg=5.0)
    sent = live_device.publish_telemetry(roll_deg=17.3)
    payload = json.loads(sent.payload)
    published_at = sent.at
    device_id = live_device.device_id

    def latest_has_seq() -> dict | None:
        response = api.get(f"/devices/{device_id}/telemetry/latest")
        if response.status_code != 200:
            return None
        body = response.json()
        return body if body["seq"] == payload["seq"] else None

    body, _ = wait_until(latest_has_seq, INGEST_TIMEOUT_S, 0.02)
    level_latency = time.time() - published_at
    assert body is not None, "a leitura nova não apareceu em /telemetry/latest"
    assert body["alert_level"] == "red"

    def series_has_seq() -> bool:
        response = api.get(f"/devices/{device_id}/telemetry", params={"minutes": 10})
        if response.status_code != 200:
            return False
        return any(point["seq"] == payload["seq"] for point in response.json()["points"])

    found, _ = wait_until(series_has_seq, INGEST_TIMEOUT_S, 0.02)
    chart_latency = time.time() - published_at
    assert found, "o ponto novo não entrou na série de 10 minutos"

    print(
        f"[e2e] W5 — nível disponível na API em {level_latency:.3f} s e ponto no gráfico em "
        f"{chart_latency:.3f} s depois da publicação "
        f"(orçamento: {LEVEL_BUDGET_S:.1f} s e {CHART_BUDGET_S:.1f} s, já descontados os "
        f"{FRONT_REFRESH_S:.0f} s de recarga do front)"
    )
    assert level_latency <= LEVEL_BUDGET_S
    assert chart_latency <= CHART_BUDGET_S

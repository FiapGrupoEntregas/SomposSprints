"""Exceções e mundo real: equipamento nunca visto, banco vazio, chave faltando (I6).

Nenhum destes casos pode derrubar a API nem devolver 500 — é o que o enunciado chama de
"tratamento de exceções".
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import API_KEY, ApiServer

pytestmark = pytest.mark.e2e

# Está no catálogo (W1) mas nunca publicou nada nesta execução: é o "equipamento nunca visto".
UNSEEN_DEVICE = "harvester-01"
UNKNOWN_DEVICE = "trator-fantasma-99"


def test_unseen_device_answers_without_data_instead_of_failing(api: ApiServer) -> None:
    """Equipamento do catálogo que nunca publicou: resposta vazia e coerente, nunca 500."""
    status = api.get(f"/devices/{UNSEEN_DEVICE}/status")
    assert status.status_code == 200
    body = status.json()
    assert body["state"] == "offline"
    assert body["reported_state"] is None
    assert body["last_seen_at"] is None

    latest = api.get(f"/devices/{UNSEEN_DEVICE}/telemetry/latest")
    assert latest.status_code == 404
    assert latest.json()["detail"] == "Sem telemetria ainda"

    series = api.get(f"/devices/{UNSEEN_DEVICE}/telemetry", params={"minutes": 10})
    assert series.status_code == 200
    assert series.json()["points"] == []
    assert series.json()["total"] == 0

    events = api.get(f"/devices/{UNSEEN_DEVICE}/events")
    assert events.status_code == 200
    assert events.json() == []


def test_unknown_device_is_a_clean_404(api: ApiServer) -> None:
    """Equipamento fora do catálogo: 404 com mensagem em português, sem vazar detalhe interno."""
    for path in ("/status", "/telemetry/latest", "/telemetry", "/events", "/limit"):
        response = api.get(f"/devices/{UNKNOWN_DEVICE}{path}")
        assert response.status_code == 404, f"{path} devolveu {response.status_code}"
        assert response.json()["detail"] == "Equipamento não encontrado"


def test_write_endpoints_require_the_api_key(api: ApiServer) -> None:
    """Sem `X-API-Key`, nem publica limite nem lê a trilha de auditoria (I5)."""
    import httpx  # noqa: PLC0415 — só aqui a requisição precisa sair sem o cabeçalho padrão

    no_key = httpx.post(f"{api.base_url}/devices/tractor-02/limit/publish", json={}, timeout=30.0)
    assert no_key.status_code == 401

    wrong_key = httpx.get(
        f"{api.base_url}/audit", headers={"X-API-Key": "chave-errada"}, timeout=30.0
    )
    assert wrong_key.status_code == 401

    with_key = httpx.get(f"{api.base_url}/audit", headers={"X-API-Key": API_KEY}, timeout=30.0)
    assert with_key.status_code == 200


def test_window_and_limit_parameters_are_validated(api: ApiServer) -> None:
    """Parâmetros fora de faixa viram 422, não 500 nem uma consulta gigante ao banco."""
    assert api.get("/devices/tractor-02/telemetry", params={"minutes": 0}).status_code == 422
    assert api.get("/devices/tractor-02/telemetry", params={"minutes": 99999}).status_code == 422
    assert api.get("/devices/tractor-02/events", params={"limit": 0}).status_code == 422
    assert api.get("/devices/tractor-02/events", params={"limit": 9999}).status_code == 422


def test_limit_for_a_date_outside_the_forecast_window(api: ApiServer) -> None:
    """Pedir o limite de um dia fora da janela da previsão devolve 404 explicando o porquê."""
    response = api.get("/devices/tractor-02/limit", params={"date": "2020-01-01"})
    assert response.status_code == 404
    assert "previsão" in response.json()["detail"]

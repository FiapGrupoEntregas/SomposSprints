import json

import httpx
import pytest

from services.api_client import ApiClient, ApiError, ApiKeyError, NotFoundError

FARM_SUMMARY = {
    "id": "cafe-carmo-de-minas",
    "name": "Sítio Café da Serra (exemplo)",
    "municipality": "Carmo de Minas",
    "state": "MG",
    "crop": "café",
}


def make_client(handler) -> ApiClient:
    return ApiClient(base_url="http://api.test", transport=httpx.MockTransport(handler))


def test_health_calls_versioned_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/health"
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0", "environment": "test"})

    assert make_client(handler).health()["status"] == "ok"


def test_list_farms_returns_summaries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/farms"
        return httpx.Response(200, json=[FARM_SUMMARY])

    farms = make_client(handler).list_farms()

    assert [farm["id"] for farm in farms] == ["cafe-carmo-de-minas"]


def test_get_farm_returns_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/farms/cafe-carmo-de-minas"
        return httpx.Response(
            200,
            json={**FARM_SUMMARY, "center": {"lat": -22.12, "lon": -45.13}, "devices": []},
        )

    farm = make_client(handler).get_farm("cafe-carmo-de-minas")

    assert farm["center"]["lat"] == -22.12


def test_get_farm_not_found_raises_message_from_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Fazenda não encontrada"})

    with pytest.raises(NotFoundError, match="Fazenda não encontrada"):
        make_client(handler).get_farm("xyz")


def test_get_terrain_returns_cells() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/farms/cafe-carmo-de-minas/terrain"
        return httpx.Response(
            200,
            json={
                "farm_id": "cafe-carmo-de-minas",
                "grid_size": 10,
                "cell_size_m": {"x_m": 103.2, "y_m": 110.5},
                "stats": {"slope_max_deg": 21.6},
                "cells": [{"row": 0, "col": 0, "slope_deg": 3.2}],
            },
        )

    terrain = make_client(handler).get_terrain("cafe-carmo-de-minas")

    assert terrain["grid_size"] == 10
    assert terrain["cells"][0]["slope_deg"] == 3.2


def test_server_error_becomes_api_error_with_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "Serviço de clima indisponível"})

    with pytest.raises(ApiError, match="Serviço de clima indisponível"):
        make_client(handler).get_terrain("cafe-carmo-de-minas")


def test_connection_failure_becomes_api_error_with_instructions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexão recusada", request=request)

    with pytest.raises(ApiError, match="http://api.test"):
        make_client(handler).list_farms()


def test_get_risk_sends_days_and_scenario() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/farms/cafe-carmo-de-minas/risk"
        assert dict(request.url.params) == {"days": "7", "scenario": "heavy_rain"}
        return httpx.Response(
            200,
            json={
                "farm_id": "cafe-carmo-de-minas",
                "generated_at": "2026-09-19T21:00:00-03:00",
                "scenario": "heavy_rain",
                "days": [],
            },
        )

    forecast = make_client(handler).get_risk("cafe-carmo-de-minas", days=7, scenario="heavy_rain")

    assert forecast["scenario"] == "heavy_rain"


def test_get_risk_omits_scenario_when_it_is_the_real_forecast() -> None:
    """A API recusa `scenario` vazio, então o parâmetro não pode ser enviado."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"days": "3"}
        return httpx.Response(
            200,
            json={
                "farm_id": "graos-sorriso",
                "generated_at": "2026-09-19T21:00:00-03:00",
                "scenario": None,
                "days": [],
            },
        )

    assert make_client(handler).get_risk("graos-sorriso", days=3)["scenario"] is None


def test_get_risk_rejected_scenario_becomes_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "Cenário inválido"})

    with pytest.raises(ApiError, match="Cenário inválido"):
        make_client(handler).get_risk("cafe-carmo-de-minas", scenario="tornado")


# --- equipamento: limite do dia (W4) e painel ao vivo (W5) ---


def test_get_device_limit_sends_date_and_scenario() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/devices/tractor-01/limit"
        assert dict(request.url.params) == {"date": "2026-09-22", "scenario": "heavy_rain"}
        return httpx.Response(200, json={"device_id": "tractor-01", "tilt_limit_deg": 10.0})

    limit = make_client(handler).get_device_limit(
        "tractor-01", date="2026-09-22", scenario="heavy_rain"
    )

    assert limit["tilt_limit_deg"] == 10.0


def test_get_device_limit_without_date_asks_for_today() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {}
        return httpx.Response(200, json={"device_id": "tractor-01", "tilt_limit_deg": 15.0})

    assert make_client(handler).get_device_limit("tractor-01")["tilt_limit_deg"] == 15.0


def test_publish_device_limit_posts_the_optional_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/devices/tractor-01/limit/publish"
        assert json.loads(request.content) == {"date": "2026-09-22"}
        return httpx.Response(
            200,
            json={
                "limit": {"tilt_limit_deg": 10.0},
                "topic": "agrishield/devices/tractor-01/config",
                "published_at": "2026-09-19T21:00:00",
                "payload": {},
            },
        )

    published = make_client(handler).publish_device_limit("tractor-01", date="2026-09-22")

    assert published["topic"].endswith("/config")


def test_publish_device_limit_without_mqtt_becomes_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "Equipamento sem conexão MQTT"})

    with pytest.raises(ApiError, match="Equipamento sem conexão MQTT"):
        make_client(handler).publish_device_limit("tractor-01")


def test_get_device_status_calls_the_status_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/devices/tractor-01/status"
        return httpx.Response(200, json={"device_id": "tractor-01", "state": "online"})

    assert make_client(handler).get_device_status("tractor-01")["state"] == "online"


def test_get_latest_telemetry_without_data_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/devices/tractor-01/telemetry/latest"
        return httpx.Response(404, json={"detail": "Sem telemetria ainda"})

    with pytest.raises(NotFoundError, match="Sem telemetria ainda"):
        make_client(handler).get_latest_telemetry("tractor-01")


def test_get_telemetry_series_sends_the_window_in_minutes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/devices/tractor-01/telemetry"
        assert dict(request.url.params) == {"minutes": "10"}
        return httpx.Response(200, json={"device_id": "tractor-01", "points": [], "total": 0})

    assert make_client(handler).get_telemetry_series("tractor-01", minutes=10)["total"] == 0


def test_get_device_events_sends_the_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/devices/tractor-01/events"
        assert dict(request.url.params) == {"limit": "20"}
        return httpx.Response(200, json=[{"event_id": "e1", "type": "rollover"}])

    events = make_client(handler).get_device_events("tractor-01", limit=20)

    assert events[0]["type"] == "rollover"


# --- chave de API nas escritas protegidas (I5) ---


def test_publish_sends_the_api_key_header_when_configured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-API-Key") == "chave-da-demo"
        return httpx.Response(
            200,
            json={
                "limit": {"tilt_limit_deg": 10.0},
                "topic": "agrishield/devices/tractor-01/config",
                "published_at": "2026-09-20T09:00:00",
                "payload": {},
            },
        )

    client = ApiClient(
        base_url="http://api.test",
        transport=httpx.MockTransport(handler),
        api_key="chave-da-demo",
    )

    assert client.publish_device_limit("tractor-01")["topic"].endswith("/config")


# Toda leitura do cliente, para garantir que nenhuma leva segredo (I5).
READ_CALLS = {
    "health": lambda client: client.health(),
    "list_farms": lambda client: client.list_farms(),
    "get_farm": lambda client: client.get_farm("cafe-carmo-de-minas"),
    "get_terrain": lambda client: client.get_terrain("cafe-carmo-de-minas"),
    "get_risk": lambda client: client.get_risk("cafe-carmo-de-minas"),
    "get_recommendations": lambda client: client.get_recommendations("cafe-carmo-de-minas"),
    "get_device_limit": lambda client: client.get_device_limit("tractor-01"),
    "get_device_status": lambda client: client.get_device_status("tractor-01"),
    "get_latest_telemetry": lambda client: client.get_latest_telemetry("tractor-01"),
    "get_telemetry_series": lambda client: client.get_telemetry_series("tractor-01"),
    "get_device_events": lambda client: client.get_device_events("tractor-01"),
    "get_device_history": lambda client: client.get_device_history("tractor-01"),
    "get_underwriting": lambda client: client.get_underwriting("cafe-carmo-de-minas"),
    "get_equipment_report": lambda client: client.get_equipment_report("tractor-01"),
    "get_region_report": lambda client: client.get_region_report("RS"),
    "get_crop_report": lambda client: client.get_crop_report(),
    "list_replay_cases": lambda client: client.list_replay_cases(),
    "get_replay_summary": lambda client: client.get_replay_summary(),
    # POST por causa do corpo, mas é leitura: não muda nada e não pode levar a chave.
    "run_replay": lambda client: client.run_replay(case_id="jabora"),
}

# Métodos que **devem** mandar a chave; o resto é leitura e entra no laço acima.
WRITE_METHODS = {"publish_device_limit"}

# Ajudantes que não fazem requisição (só montam URL de download).
HELPER_METHODS = {"csv_url"}


@pytest.mark.parametrize("read", sorted(READ_CALLS))
def test_reads_never_send_the_api_key(read: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "X-API-Key" not in request.headers, f"{read} vazou a chave de API"
        return httpx.Response(200, json={})

    client = ApiClient(
        base_url="http://api.test",
        transport=httpx.MockTransport(handler),
        api_key="chave-da-demo",
    )

    READ_CALLS[read](client)


def test_every_public_method_is_classified_as_read_or_write() -> None:
    """Método novo no cliente entra numa das duas listas — senão este teste falha."""
    public = {
        name
        for name in dir(ApiClient)
        if not name.startswith("_") and callable(getattr(ApiClient, name))
    }

    assert public == set(READ_CALLS) | WRITE_METHODS | HELPER_METHODS


def test_publish_without_configured_key_explains_what_to_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "X-API-Key" not in request.headers
        return httpx.Response(401, json={"detail": "Chave de API ausente ou inválida"})

    client = ApiClient(
        base_url="http://api.test", transport=httpx.MockTransport(handler), api_key=""
    )

    with pytest.raises(ApiKeyError, match="AGRISHIELD_API_KEY"):
        client.publish_device_limit("tractor-01")


def test_publish_with_a_rejected_key_says_the_key_is_wrong() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Chave de API ausente ou inválida"})

    client = ApiClient(
        base_url="http://api.test",
        transport=httpx.MockTransport(handler),
        api_key="chave-errada",
    )

    with pytest.raises(ApiKeyError, match="recusou a chave"):
        client.publish_device_limit("tractor-01")


def test_get_recommendations_sends_days_and_scenario() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/farms/uva-serra-gaucha/recommendations"
        assert dict(request.url.params) == {"days": "2", "scenario": "storm"}
        return httpx.Response(
            200,
            json=[{"date": "2026-09-20", "windows": [], "messages": ["Sem restrições."]}],
        )

    days = make_client(handler).get_recommendations("uva-serra-gaucha", days=2, scenario="storm")

    assert days[0]["messages"] == ["Sem restrições."]


# --- subscrição (W8) e relatórios (W12) ---


def test_get_underwriting_calls_the_farm_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/farms/cafe-carmo-de-minas/underwriting"
        return httpx.Response(200, json={"terrain_score": 41.2, "risk_class": "B"})

    profile = make_client(handler).get_underwriting("cafe-carmo-de-minas")

    assert profile["risk_class"] == "B"


def test_get_equipment_report_sends_the_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/reports/equipment/tractor-01"
        assert dict(request.url.params) == {"days": "30"}
        return httpx.Response(200, json={"device_id": "tractor-01", "days": 30, "trend": []})

    assert make_client(handler).get_equipment_report("tractor-01", days=30)["days"] == 30


def test_get_region_report_omits_empty_year_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/reports/region"
        assert dict(request.url.params) == {"state": "RS"}
        return httpx.Response(200, json={"state": "RS", "claims": 91998})

    assert make_client(handler).get_region_report("RS")["claims"] == 91998


def test_get_crop_report_sends_the_filters_it_has() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"state": "MG", "from_year": "2020", "to_year": "2025"}
        return httpx.Response(200, json={"crops": []})

    make_client(handler).get_crop_report(from_year=2020, to_year=2025, state="MG")


def test_csv_url_points_to_the_api_and_keeps_the_filters() -> None:
    client = ApiClient(base_url="http://api.test", transport=httpx.MockTransport(lambda r: None))

    url = client.csv_url("/reports/region.csv", {"state": "RS", "from_year": None})

    assert url == "http://api.test/api/v1/reports/region.csv?state=RS"


# --- replay (W9) ---


def test_list_replay_cases_calls_the_cases_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/replay/cases"
        return httpx.Response(200, json=[{"id": "jabora", "title": "Trator capota"}])

    assert make_client(handler).list_replay_cases()[0]["id"] == "jabora"


def test_run_replay_posts_the_case_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/replay"
        assert json.loads(request.content) == {"case_id": "jabora"}
        return httpx.Response(200, json={"would_alert": True})

    assert make_client(handler).run_replay(case_id="jabora")["would_alert"] is True


def test_run_replay_posts_a_manual_point() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "lat": -27.14,
            "lon": -51.74,
            "date": "2026-07-18",
        }
        return httpx.Response(200, json={"would_alert": False})

    make_client(handler).run_replay(lat=-27.14, lon=-51.74, date="2026-07-18")


def test_run_replay_with_a_future_date_becomes_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "A data do replay precisa estar no passado"})

    with pytest.raises(ApiError, match="precisa estar no passado"):
        make_client(handler).run_replay(lat=-27.0, lon=-51.0, date="2099-01-01")


def test_run_replay_with_an_unknown_case_is_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Caso de replay não encontrado"})

    with pytest.raises(NotFoundError, match="Caso de replay não encontrado"):
        make_client(handler).run_replay(case_id="nao-existe")


def test_get_replay_summary_calls_the_summary_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/replay/summary"
        return httpx.Response(
            200,
            json={"total_cases": 5, "would_alert_at_point": 2, "would_alert_in_grid": 3},
        )

    summary = make_client(handler).get_replay_summary()

    assert (summary["would_alert_at_point"], summary["would_alert_in_grid"]) == (2, 3)


def test_get_device_history_sends_the_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/devices/tractor-01/history"
        assert dict(request.url.params) == {"days": "30"}
        return httpx.Response(200, json={"device_id": "tractor-01", "days": 30, "timeline": []})

    assert make_client(handler).get_device_history("tractor-01", days=30)["days"] == 30

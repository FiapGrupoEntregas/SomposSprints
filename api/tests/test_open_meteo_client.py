"""Testes do cliente da Open-Meteo (I1). Rodam offline: fixtures + httpx.MockTransport."""

from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from app.clients import open_meteo
from app.clients.open_meteo import MAX_ELEVATION_POINTS, WeatherUnavailableError
from app.core.cache import TTLCache
from tests.conftest import load_fixture

ELEVATION = load_fixture("open_meteo_elevation.json")
FORECAST = load_fixture("open_meteo_forecast.json")
HISTORICAL = load_fixture("open_meteo_historical.json")
ARCHIVE = load_fixture("open_meteo_archive.json")

LAT = -22.12
LON = -45.13


def responder(payload: dict, status_code: int = 200):
    """Handler de `MockTransport` que devolve sempre o mesmo JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return handler


def failing(request: httpx.Request) -> httpx.Response:
    """Handler que simula a Open-Meteo fora do ar."""
    raise httpx.ConnectError("rede indisponível", request=request)


def query_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.url.query.decode())


def grid_coordinates(size: int) -> tuple[list[float], list[float]]:
    """Gera `size` pontos quaisquer, só para exercitar o limite de pontos."""
    lats = [-22.12 + index * 0.0001 for index in range(size)]
    lons = [-45.13 + index * 0.0001 for index in range(size)]
    return lats, lons


# --- elevação ---------------------------------------------------------------------------------


def test_fetch_elevations_sends_one_request_for_100_points(make_client, recorder) -> None:
    client = make_client(responder(ELEVATION))
    lats, lons = grid_coordinates(MAX_ELEVATION_POINTS)

    elevations = client.fetch_elevations(lats, lons)

    assert recorder.count == 1
    assert len(elevations) == MAX_ELEVATION_POINTS
    assert all(isinstance(value, float) for value in elevations)
    query = query_of(recorder.last)
    assert len(query["latitude"][0].split(",")) == MAX_ELEVATION_POINTS
    assert len(query["longitude"][0].split(",")) == MAX_ELEVATION_POINTS


def test_fetch_elevations_rejects_more_than_100_points(make_client, recorder) -> None:
    client = make_client(responder(ELEVATION))
    lats, lons = grid_coordinates(MAX_ELEVATION_POINTS + 1)

    with pytest.raises(ValueError, match="no máximo 100 pontos"):
        client.fetch_elevations(lats, lons)

    assert recorder.count == 0


def test_fetch_elevations_rejects_unbalanced_or_empty_lists(make_client) -> None:
    client = make_client(responder(ELEVATION))

    with pytest.raises(ValueError, match="mesmo tamanho"):
        client.fetch_elevations([-22.1, -22.2], [-45.1])

    with pytest.raises(ValueError, match="ao menos um ponto"):
        client.fetch_elevations([], [])


def test_fetch_elevations_rejects_response_with_wrong_size(make_client) -> None:
    client = make_client(responder({"elevation": [900.0]}))
    lats, lons = grid_coordinates(4)

    with pytest.raises(WeatherUnavailableError):
        client.fetch_elevations(lats, lons)


# --- previsão ---------------------------------------------------------------------------------


def test_fetch_hourly_forecast_builds_expected_params(make_client, recorder) -> None:
    client = make_client(responder(FORECAST))

    client.fetch_hourly_forecast(LAT, LON)

    query = query_of(recorder.last)
    assert query["past_days"] == ["3"]
    assert query["forecast_days"] == ["7"]
    assert query["timezone"] == ["America/Sao_Paulo"]
    assert query["hourly"][0].split(",") == list(open_meteo.HOURLY_VARIABLES)


def test_fetch_hourly_forecast_parses_all_series(make_client) -> None:
    client = make_client(responder(FORECAST))

    hourly = client.fetch_hourly_forecast(LAT, LON)

    assert len(hourly.time) == 240  # 10 dias × 24 h (3 passados + 7 futuros)
    assert hourly.timezone == "America/Sao_Paulo"
    assert len(hourly.precipitation) == 240
    assert len(hourly.soil_moisture) == 240
    assert any(value is not None for value in hourly.cape)


def test_forecast_within_ttl_does_not_touch_the_network(make_client, recorder) -> None:
    client = make_client(responder(FORECAST))

    first = client.fetch_hourly_forecast(LAT, LON)
    second = client.fetch_hourly_forecast(LAT, LON)

    assert recorder.count == 1
    assert first == second


def test_different_params_use_different_cache_keys(make_client, recorder) -> None:
    client = make_client(responder(FORECAST))

    client.fetch_hourly_forecast(LAT, LON)
    client.fetch_hourly_forecast(LAT, LON, forecast_days=5)

    assert recorder.count == 2


# --- falhas e stale-if-error ------------------------------------------------------------------


def test_stale_if_error_returns_expired_cache(
    make_client, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """Com o cache vencido e a rede fora, a previsão antiga é reaproveitada (protege a demo)."""
    monkeypatch.setattr(open_meteo, "FORECAST_TTL_S", 0)
    cache = TTLCache()

    fresh = make_client(responder(FORECAST), cache=cache).fetch_hourly_forecast(LAT, LON)

    offline = make_client(failing, cache=cache)
    with caplog.at_level("WARNING"):
        stale = offline.fetch_hourly_forecast(LAT, LON)

    assert stale == fresh
    assert "vencida do cache" in caplog.text


def test_network_failure_without_cache_raises_weather_unavailable(make_client) -> None:
    client = make_client(failing)

    with pytest.raises(WeatherUnavailableError, match="Serviço de clima indisponível"):
        client.fetch_hourly_forecast(LAT, LON)


def test_http_error_without_cache_raises_weather_unavailable(make_client) -> None:
    client = make_client(responder({"error": True, "reason": "falha"}, status_code=500))

    with pytest.raises(WeatherUnavailableError):
        client.fetch_hourly_forecast(LAT, LON)


def test_payload_without_hourly_raises_weather_unavailable(make_client) -> None:
    client = make_client(responder({"latitude": LAT, "longitude": LON}))

    with pytest.raises(WeatherUnavailableError):
        client.fetch_hourly_forecast(LAT, LON)


def test_malformed_200_does_not_poison_the_cache(make_client, recorder) -> None:
    """Uma resposta 200 sem `hourly` sai do cache: a próxima chamada tenta a rede de novo."""
    cache = TTLCache()
    broken = make_client(responder({"latitude": LAT, "longitude": LON}), cache=cache)

    with pytest.raises(WeatherUnavailableError):
        broken.fetch_hourly_forecast(LAT, LON)

    assert recorder.count == 1

    recovered = make_client(responder(FORECAST), cache=cache)
    hourly = recovered.fetch_hourly_forecast(LAT, LON)

    assert recorder.count == 2  # não reaproveitou o payload quebrado
    assert len(hourly.time) == 240


def test_misaligned_series_raise_weather_unavailable_and_clear_the_cache(
    make_client, recorder
) -> None:
    """Dado parcial (série menor que `time`) vira WeatherUnavailableError, não ValidationError."""
    cache = TTLCache()
    misaligned = {
        "latitude": LAT,
        "longitude": LON,
        "timezone": "America/Sao_Paulo",
        "hourly": {
            "time": ["2026-09-16T00:00", "2026-09-16T01:00"],
            "temperature_2m": [21.0],
        },
    }

    broken = make_client(responder(misaligned), cache=cache)
    with pytest.raises(WeatherUnavailableError, match="desalinhadas"):
        broken.fetch_hourly_forecast(LAT, LON)

    assert recorder.count == 1

    recovered = make_client(responder(FORECAST), cache=cache)
    hourly = recovered.fetch_hourly_forecast(LAT, LON)

    assert recorder.count == 2  # a resposta desalinhada não ficou no cache
    assert len(hourly.time) == 240


def test_misaligned_history_response_also_raises_weather_unavailable(make_client) -> None:
    payload = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": {"time": ["2023-01-10T00:00", "2023-01-10T01:00"], "precipitation": [0.2]},
    }
    client = make_client(responder(payload))

    with pytest.raises(WeatherUnavailableError, match="desalinhadas"):
        client.fetch_hourly_history(LAT, LON, date(2023, 1, 10), date(2023, 1, 10))


def test_unexpected_hourly_shape_raises_weather_unavailable(make_client) -> None:
    """`time` que não é lista também não pode vazar TypeError."""
    client = make_client(responder({"hourly": {"time": "2026-09-16T00:00"}}))

    with pytest.raises(WeatherUnavailableError):
        client.fetch_hourly_forecast(LAT, LON)


def test_malformed_200_is_not_reused_as_stale(
    make_client, recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O payload quebrado também não pode voltar pelo stale-if-error."""
    monkeypatch.setattr(open_meteo, "FORECAST_TTL_S", 0)
    cache = TTLCache()

    with pytest.raises(WeatherUnavailableError):
        make_client(responder({"latitude": LAT}), cache=cache).fetch_hourly_forecast(LAT, LON)

    with pytest.raises(WeatherUnavailableError):
        make_client(failing, cache=cache).fetch_hourly_forecast(LAT, LON)


def test_malformed_elevation_response_does_not_poison_the_cache(make_client, recorder) -> None:
    cache = TTLCache()
    lats, lons = grid_coordinates(4)

    broken = make_client(responder({"elevation": [900.0]}), cache=cache)
    with pytest.raises(WeatherUnavailableError):
        broken.fetch_elevations(lats, lons)

    recovered = make_client(responder({"elevation": [900.0, 910.0, 920.0, 930.0]}), cache=cache)

    assert recovered.fetch_elevations(lats, lons) == [900.0, 910.0, 920.0, 930.0]
    assert recorder.count == 2


# --- histórico --------------------------------------------------------------------------------


def test_history_from_2022_uses_historical_forecast_api(make_client, recorder) -> None:
    client = make_client(responder(HISTORICAL))

    client.fetch_hourly_history(LAT, LON, date(2022, 1, 1), date(2022, 1, 3))

    assert recorder.last.url.host == "historical-forecast-api.open-meteo.com"
    assert query_of(recorder.last)["hourly"][0].split(",") == list(open_meteo.HOURLY_VARIABLES)


def test_history_before_2022_uses_archive_api(make_client, recorder) -> None:
    client = make_client(responder(ARCHIVE))

    hourly = client.fetch_hourly_history(LAT, LON, date(2021, 12, 31), date(2022, 1, 2))

    assert recorder.last.url.host == "archive-api.open-meteo.com"
    query = query_of(recorder.last)
    assert query["hourly"][0].split(",") == list(open_meteo.ARCHIVE_HOURLY_VARIABLES)
    assert "cape" not in query["hourly"][0]
    # A Archive API não tem CAPE e usa soil_moisture_0_to_7cm.
    assert all(value is None for value in hourly.cape)
    assert any(value is not None for value in hourly.soil_moisture)


def test_history_sends_the_requested_period(make_client, recorder) -> None:
    client = make_client(responder(HISTORICAL))

    client.fetch_hourly_history(LAT, LON, date(2023, 1, 10), date(2023, 1, 12))

    query = query_of(recorder.last)
    assert query["start_date"] == ["2023-01-10"]
    assert query["end_date"] == ["2023-01-12"]


def test_history_rejects_inverted_period(make_client, recorder) -> None:
    client = make_client(responder(HISTORICAL))

    with pytest.raises(ValueError, match="anterior à data inicial"):
        client.fetch_hourly_history(LAT, LON, date(2023, 1, 12), date(2023, 1, 10))

    assert recorder.count == 0


# --- timeout ------------------------------------------------------------------------------------


def test_requests_carry_the_10s_timeout(make_client, recorder) -> None:
    client = make_client(responder(FORECAST))

    client.fetch_hourly_forecast(LAT, LON)

    timeout = recorder.last.extensions["timeout"]
    assert timeout["connect"] == open_meteo.REQUEST_TIMEOUT_S
    assert timeout["read"] == open_meteo.REQUEST_TIMEOUT_S

"""Testes do replay de acidentes reais (W9) — regras-de-risco §9.

Nada aqui vai à rede: o histórico e a elevação são servidos por `httpx.MockTransport`. A cota da
Open-Meteo já caiu uma vez neste projeto, e um teste que a consome é um teste que vai falhar na
véspera da apresentação.
"""

import json
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.core.clock import today_local
from app.main import app
from app.schemas.farm import DEFAULT_REFERENCE_TILT_LIMIT_DEG, LatLon
from app.schemas.replay import HistoricalSource, LocationPrecision
from app.schemas.weather import HourlyWeather
from app.services import replay as replay_service
from app.services.risk import assess_day
from app.services.terrain import build_terrain, clear_terrain_cache
from app.services.weather import aggregate_daily
from tests.conftest import load_fixture

CASES_URL = "/api/v1/replay/cases"
REPLAY_URL = "/api/v1/replay"

POINT = LatLon(lat=-18.5, lon=-46.5)
PAST_DAY = date(2024, 2, 9)
ARCHIVE_DAY = date(2019, 3, 10)

# Relevo com um degrau, como o de Jaborá: a célula do meio é mansa (~9,7°, abaixo dos 70% do
# limite) e a de leste é uma ribanceira (~18,9°, acima dos 15°). Numa grade de ~350 m por célula,
# 120 m de desnível dão exatamente esse contraste.
STEEP_ELEVATION = [900.0, 900.0, 1020.0] * 3


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    clear_terrain_cache()
    replay_service.load_cases.cache_clear()
    yield
    clear_terrain_cache()
    replay_service.load_cases.cache_clear()
    app.dependency_overrides.clear()


def history_payload(
    day: date,
    rain_per_hour_mm: float = 0.0,
    gust_kmh: float = 10.0,
    weather_code: int = 0,
    temp_c: float = 22.0,
    humidity_pct: float = 60.0,
    cape: float | None = 100.0,
) -> dict:
    """Três dias de histórico (d−2 a d), no formato da Historical Forecast API."""
    times = [
        (datetime.combine(day - timedelta(days=offset), datetime.min.time()) + timedelta(hours=h))
        for offset in (2, 1, 0)
        for h in range(24)
    ]
    size = len(times)
    hourly = {
        "time": [moment.isoformat(timespec="minutes") for moment in times],
        "temperature_2m": [temp_c] * size,
        "relative_humidity_2m": [humidity_pct] * size,
        "precipitation": [rain_per_hour_mm] * size,
        "weather_code": [weather_code] * size,
        "wind_speed_10m": [10.0] * size,
        "wind_gusts_10m": [gust_kmh] * size,
        "soil_moisture_3_to_9cm": [0.3] * size,
    }
    if cape is not None:
        hourly["cape"] = [cape] * size
    return {
        "latitude": POINT.lat,
        "longitude": POINT.lon,
        "timezone": "America/Sao_Paulo",
        "hourly": hourly,
    }


def _parse_history(payload: dict) -> HourlyWeather:
    """Reconstrói a série horária a partir do mesmo JSON que o mock devolveu."""
    hourly = payload["hourly"]
    size = len(hourly["time"])
    return HourlyWeather(
        latitude=payload["latitude"],
        longitude=payload["longitude"],
        timezone=payload["timezone"],
        time=hourly["time"],
        temperature_2m=hourly["temperature_2m"],
        relative_humidity_2m=hourly["relative_humidity_2m"],
        precipitation=hourly["precipitation"],
        weather_code=hourly["weather_code"],
        wind_speed_10m=hourly["wind_speed_10m"],
        wind_gusts_10m=hourly["wind_gusts_10m"],
        cape=hourly.get("cape", [None] * size),
        soil_moisture=hourly["soil_moisture_3_to_9cm"],
    )


def _cell_signature(day) -> list:  # type: ignore[no-untyped-def]
    """`(linha, coluna, nível, mensagens)` de cada célula — o que o operador veria."""
    return [
        (cell.row, cell.col, cell.level.value, [reason.message for reason in cell.reasons])
        for cell in day.cells
    ]


@pytest.fixture
def make_replay_client(
    make_client: Callable[..., OpenMeteoClient],
) -> Callable[..., OpenMeteoClient]:
    """Cliente da Open-Meteo que devolve elevação e histórico controlados, sem rede."""

    def factory(
        elevation: list[float] | None = None,
        history: dict | None = None,
        urls: list | None = None,
        **history_kwargs: object,
    ) -> OpenMeteoClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if urls is not None:
                urls.append(str(request.url))
            if "elevation" in request.url.path:
                return httpx.Response(200, json={"elevation": elevation or [900.0] * 9})
            if history is not None:
                return httpx.Response(200, json=history)
            # Sem payload fixo, o histórico é gerado para a **data pedida**: é o que permite
            # rodar os cinco casos, que têm datas diferentes.
            end_date = request.url.params.get("end_date", PAST_DAY.isoformat())
            payload = history_payload(date.fromisoformat(end_date), **history_kwargs)  # type: ignore[arg-type]
            return httpx.Response(200, json=payload)

        return make_client(handler)

    return factory


@pytest.fixture
def api(make_replay_client: Callable[..., OpenMeteoClient]) -> Callable[..., TestClient]:
    def factory(**kwargs: object) -> TestClient:
        app.dependency_overrides[get_open_meteo_client] = lambda: make_replay_client(**kwargs)
        return TestClient(app)

    return factory


# --- Os casos curados ---------------------------------------------------------------------------


def test_the_curated_cases_load_and_validate() -> None:
    cases = replay_service.load_cases()

    assert len(cases) >= 3, "o critério de aceite pede pelo menos 3 casos reais"
    for case in cases:
        assert case.source_url.startswith("http"), "nenhum caso vira evidência sem a fonte"
        assert case.date < today_local()
        assert case.description


def test_every_case_declares_its_location_precision() -> None:
    """Critério de aceite: a precisão da localização aparece sempre."""
    for case in replay_service.load_cases():
        assert case.location_precision in set(LocationPrecision)


def test_a_case_is_found_by_id() -> None:
    first = replay_service.load_cases()[0]

    assert replay_service.get_case(first.id) == first
    assert replay_service.get_case("nao-existe") is None


def test_broken_cases_file_gives_a_clear_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "cases.json"
    path.write_text("{não é lista}", encoding="utf-8")

    with pytest.raises(replay_service.ReplayCasesError, match="não são um JSON válido"):
        replay_service.load_cases_from_file(path)


def test_a_case_missing_the_source_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"id": "x", "title": "y"}]), encoding="utf-8")

    with pytest.raises(replay_service.ReplayCasesError, match="Caso inválido"):
        replay_service.load_cases_from_file(path)


# --- §9 A escolha da API histórica --------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2021, 12, 31), HistoricalSource.ARCHIVE),
        (date(2022, 1, 1), HistoricalSource.HISTORICAL_FORECAST),
        (date(2024, 2, 9), HistoricalSource.HISTORICAL_FORECAST),
        (date(2015, 6, 1), HistoricalSource.ARCHIVE),
    ],
)
def test_the_source_boundary_is_2022(day: date, expected: HistoricalSource) -> None:
    """regras-de-risco §9: a partir de 2022 é Historical Forecast; antes, Archive (ERA5)."""
    assert replay_service.source_for(day) is expected


def test_an_old_date_uses_the_archive_api(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """Critério de aceite: data anterior a 2022 roda pela Archive API, sem CAPE e sem erro."""
    urls: list[str] = []
    client = make_replay_client(history=history_payload(ARCHIVE_DAY, cape=None), urls=urls)

    result = replay_service.run_replay(client, POINT, ARCHIVE_DAY)

    assert result.source is HistoricalSource.ARCHIVE
    assert result.weather.cape_max is None
    assert any("archive" in url for url in urls)


def test_the_archive_limitation_is_declared(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """Sem essa ressalva, comparar um replay de 2019 com um de 2024 é comparar maçã com laranja."""
    client = make_replay_client(history=history_payload(ARCHIVE_DAY, cape=None))

    result = replay_service.run_replay(client, POINT, ARCHIVE_DAY)

    assert any("Archive API" in limitation for limitation in result.limitations)
    assert any(
        "sem CAPE" in limitation or "não tem CAPE" in limitation
        for limitation in result.limitations
    )


def test_a_recent_date_uses_the_historical_forecast_api(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    urls: list[str] = []
    client = make_replay_client(urls=urls)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert result.source is HistoricalSource.HISTORICAL_FORECAST
    assert any("historical-forecast" in url for url in urls)


# --- A cota da Open-Meteo -----------------------------------------------------------------------


def test_the_grid_is_small_enough_for_the_quota(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """A Elevation API recusa chamada multiponto grande com 429; aqui a grade é 3 × 3."""
    urls: list[str] = []
    client = make_replay_client(urls=urls)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert result.grid_size == 3
    assert len(result.day.cells) == 9
    elevation_url = next(url for url in urls if "elevation" in url)
    assert len(elevation_url.split("latitude=")[1].split("&")[0].split("%2C")) == 9


def test_repeating_the_same_case_does_not_spend_the_quota_again(
    make_replay_client: Callable[..., OpenMeteoClient], recorder
) -> None:  # type: ignore[no-untyped-def]
    """O relevo fica 24 h em cache: rodar o mesmo caso duas vezes não pede elevação de novo."""
    client = make_replay_client()

    replay_service.run_replay(client, POINT, PAST_DAY)
    first = recorder.count
    replay_service.run_replay(client, POINT, PAST_DAY)

    elevation_calls = [r for r in recorder.requests if "elevation" in str(r.url)]
    assert len(elevation_calls) == 1
    assert recorder.count >= first


# --- O veredito ---------------------------------------------------------------------------------


def test_a_calm_day_on_flat_ground_would_not_alert(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """Resultado honesto: terreno plano e tempo bom não produzem alerta."""
    client = make_replay_client(elevation=[900.0] * 9)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert result.would_alert is False
    assert result.would_alert_in_grid is False
    assert result.point_cell.reasons == []
    assert "não** teria alertado" in result.verdict


def test_a_storm_would_alert(make_replay_client: Callable[..., OpenMeteoClient]) -> None:
    client = make_replay_client(history=history_payload(PAST_DAY, weather_code=95))

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert result.would_alert is True
    assert result.point_cell.reasons
    assert "teria alertado no ponto" in result.verdict


def test_the_verdict_carries_the_reason(make_replay_client: Callable[..., OpenMeteoClient]) -> None:
    """O veredito precisa dizer **por quê**, com os números do dia."""
    client = make_replay_client(history=history_payload(PAST_DAY, rain_per_hour_mm=3.0))

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert result.would_alert is True
    assert any(character.isdigit() for character in result.verdict)


def test_the_verdict_lists_every_reason(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """Imbituva marcou atolamento **e** raio; o acidente foi por raio.

    Um veredito que citasse só o primeiro motivo pareceria um acerto por acaso.
    """
    client = make_replay_client(
        history=history_payload(PAST_DAY, rain_per_hour_mm=2.5, weather_code=95)
    )

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert len(result.point_cell.reasons) >= 2
    for reason in result.point_cell.reasons:
        assert reason.message in result.verdict


def test_the_grid_can_alert_where_the_point_does_not(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """Jaborá: a célula central é mansa e a vizinha, a 300 m, é uma ribanceira."""
    client = make_replay_client(elevation=STEEP_ELEVATION)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert result.would_alert_in_grid is True
    if not result.would_alert:
        assert "outra célula da mesma grade" in result.verdict


def test_the_point_cell_is_the_closest_to_the_accident(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """§9: a resposta traz o nível da célula mais próxima do ponto — a do centro, aqui."""
    client = make_replay_client(elevation=STEEP_ELEVATION)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    # Grade 3 × 3 centrada no ponto: a célula do meio é a (1, 1).
    assert (result.point_cell.row, result.point_cell.col) == (1, 1)


# --- As ressalvas -------------------------------------------------------------------------------


def test_a_municipality_point_says_so(make_replay_client: Callable[..., OpenMeteoClient]) -> None:
    """A ressalva mais importante: a coordenada é do município, não da lavoura."""
    client = make_replay_client()

    result = replay_service.run_replay(
        client, POINT, PAST_DAY, precision=LocationPrecision.MUNICIPIO
    )

    assert result.location_precision is LocationPrecision.MUNICIPIO
    assert any("quilômetros da lavoura" in limitation for limitation in result.limitations)


def test_the_limitations_are_never_empty(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """Mesmo no melhor caso, o replay diz que não afirma ter evitado o acidente."""
    client = make_replay_client()

    result = replay_service.run_replay(client, POINT, PAST_DAY, precision=LocationPrecision.EXATO)

    assert result.limitations
    assert any("não afirma" in limitation for limitation in result.limitations)


def test_the_weather_of_the_day_is_reported(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    client = make_replay_client(history=history_payload(PAST_DAY, rain_per_hour_mm=1.0))

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert result.weather.rain_mm == 24.0
    # d−2, d−1 e d, com 24 mm cada
    assert result.weather.rain_72h_mm == 72.0
    assert result.date == PAST_DAY


# --- Rotas --------------------------------------------------------------------------------------


def test_the_cases_route_lists_the_curated_cases(api: Callable[..., TestClient]) -> None:
    response = api().get(CASES_URL)

    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 3
    assert all(case["source_url"] for case in body)
    assert all(case["location_precision"] for case in body)


def test_replaying_a_case_answers_the_question(api: Callable[..., TestClient]) -> None:
    case = replay_service.load_cases()[0]
    client = api(history=history_payload(case.date))

    response = client.post(REPLAY_URL, json={"case_id": case.id})

    assert response.status_code == 200
    body = response.json()
    assert body["case"]["id"] == case.id
    assert body["case"]["source_url"]
    assert isinstance(body["would_alert"], bool)
    assert body["verdict"]
    assert body["limitations"]


def test_replaying_a_manual_point(api: Callable[..., TestClient]) -> None:
    client = api()

    response = client.post(
        REPLAY_URL, json={"lat": POINT.lat, "lon": POINT.lon, "date": PAST_DAY.isoformat()}
    )

    assert response.status_code == 200
    assert response.json()["case"] is None


def test_an_unknown_case_returns_404(api: Callable[..., TestClient]) -> None:
    response = api().post(REPLAY_URL, json={"case_id": "nao-existe"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Caso de replay não encontrado"


def test_a_future_date_returns_422(api: Callable[..., TestClient]) -> None:
    """Replay é sobre o passado; prever o futuro já é o `/risk`."""
    tomorrow = today_local() + timedelta(days=1)

    response = api().post(
        REPLAY_URL, json={"lat": POINT.lat, "lon": POINT.lon, "date": tomorrow.isoformat()}
    )

    assert response.status_code == 422
    assert "passado" in response.json()["detail"]


def test_today_is_also_refused(api: Callable[..., TestClient]) -> None:
    response = api().post(
        REPLAY_URL,
        json={"lat": POINT.lat, "lon": POINT.lon, "date": today_local().isoformat()},
    )

    assert response.status_code == 422


def test_an_empty_body_returns_422(api: Callable[..., TestClient]) -> None:
    response = api().post(REPLAY_URL, json={})

    assert response.status_code == 422
    assert "case_id" in response.json()["detail"]


def test_open_meteo_down_returns_503(make_client: Callable[..., OpenMeteoClient]) -> None:
    app.dependency_overrides[get_open_meteo_client] = lambda: make_client(
        lambda _: httpx.Response(500, text="erro na origem")
    )
    client = TestClient(app)

    response = client.post(
        REPLAY_URL, json={"lat": POINT.lat, "lon": POINT.lon, "date": PAST_DAY.isoformat()}
    )

    assert response.status_code == 503


def test_the_replay_is_recorded_in_the_audit_trail(api: Callable[..., TestClient]) -> None:
    """I5 reservou o `decision_type` `replay` desde o começo; agora ele tem quem o escreva."""
    from sqlmodel import Session

    from app.db import get_engine
    from app.repositories import audit as audit_repository
    from app.schemas.audit import DecisionType

    case = replay_service.load_cases()[0]
    client = api(history=history_payload(case.date))

    response = client.post(REPLAY_URL, json={"case_id": case.id})

    with Session(get_engine()) as session:
        rows = audit_repository.list_decisions(
            session, entity_id=case.id, decision_type=DecisionType.REPLAY
        )

    assert rows
    output = json.loads(rows[0].output_json)
    assert output["would_alert"] == response.json()["would_alert"]
    assert output["verdict"]


@pytest.mark.parametrize(
    ("rain_per_hour_mm", "expected_soil"),
    [
        # Um dia por estado do solo (§3). Sem isto, um motor paralelo que divergisse **só** em
        # solo seco ou úmido passaria: o cenário de 3 mm/h satura o solo e nunca exercita os
        # outros dois ramos. Foi a mutação que escapou na revisão.
        (0.0, "dry"),  # 0 mm em 72 h
        (0.25, "moist"),  # 18 mm em 72 h
        (3.0, "saturated"),  # 216 mm em 72 h
    ],
)
def test_the_engine_is_the_same_one_the_map_uses(
    make_replay_client: Callable[..., OpenMeteoClient],
    rain_per_hour_mm: float,
    expected_soil: str,
) -> None:
    """O valor da feature é **não haver código diferente para o passado**.

    Este teste não se contenta em ver enums válidos: ele recalcula o dia chamando
    `risk.assess_day` direto, com o mesmo relevo e o mesmo clima, e exige **igualdade**. Uma
    reimplementação do motor dentro do replay — ainda que devolvesse valores plausíveis — reprova
    aqui, que é o único jeito de a promessa da W9 valer alguma coisa.

    Roda nos **três estados do solo**, porque um teste só vale nos cenários que exercita.
    """
    history = history_payload(
        PAST_DAY, rain_per_hour_mm=rain_per_hour_mm, gust_kmh=64.0, weather_code=95
    )
    client = make_replay_client(elevation=STEEP_ELEVATION, history=history)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    # O mesmo relevo e o mesmo clima que o replay usou, montados por fora.
    farm = replay_service.replay_farm(POINT, DEFAULT_REFERENCE_TILT_LIMIT_DEG)
    terrain = build_terrain(farm, STEEP_ELEVATION, n=replay_service.REPLAY_GRID_SIZE)
    weather = {day.date: day for day in aggregate_daily(_parse_history(history))}[PAST_DAY]

    expected = assess_day(terrain, weather, DEFAULT_REFERENCE_TILT_LIMIT_DEG)

    # O cenário precisa mesmo cair no estado que o caso promete, senão os três seriam o mesmo.
    assert result.day.soil_state.value == expected_soil
    assert result.day == expected, "o replay não está usando o mesmo motor do mapa"


@pytest.mark.parametrize(
    ("rain_per_hour_mm", "expected_soil"),
    [(0.0, "dry"), (0.25, "moist"), (3.0, "saturated")],
)
def test_the_replay_day_matches_the_engine_cell_by_cell(
    make_replay_client: Callable[..., OpenMeteoClient],
    rain_per_hour_mm: float,
    expected_soil: str,
) -> None:
    """A mesma garantia, quebrada em pedaços, para o diagnóstico apontar o que divergiu."""
    history = history_payload(
        PAST_DAY, rain_per_hour_mm=rain_per_hour_mm, gust_kmh=64.0, weather_code=95
    )
    client = make_replay_client(elevation=STEEP_ELEVATION, history=history)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    farm = replay_service.replay_farm(POINT, DEFAULT_REFERENCE_TILT_LIMIT_DEG)
    terrain = build_terrain(farm, STEEP_ELEVATION, n=replay_service.REPLAY_GRID_SIZE)
    weather = {day.date: day for day in aggregate_daily(_parse_history(history))}[PAST_DAY]
    expected = assess_day(terrain, weather, DEFAULT_REFERENCE_TILT_LIMIT_DEG)

    assert expected.soil_state.value == expected_soil
    assert result.day.soil_state == expected.soil_state
    assert result.day.tilt_limit_deg == expected.tilt_limit_deg
    assert result.day.worst_level == expected.worst_level
    assert result.day.pct_levels == expected.pct_levels
    assert _cell_signature(result.day) == _cell_signature(expected)
    # E o cenário precisa ter produzido perigo de verdade, senão a igualdade seria trivial.
    assert any(cell.reasons for cell in expected.cells)


def test_the_replay_grid_is_three_by_three() -> None:
    """Âncora: `len(cells) == 9` acerta por coincidência de escrita, isto não."""
    assert replay_service.REPLAY_GRID_SIZE == 3
    assert replay_service.HALF_SIDE_DEG == 0.005


def test_the_replay_uses_the_shared_default_limit() -> None:
    """O `L_ref` padrão é o do catálogo (§4), não uma cópia local que pode divergir."""
    assert replay_service.DEFAULT_REFERENCE_TILT_LIMIT_DEG is DEFAULT_REFERENCE_TILT_LIMIT_DEG


def test_the_telemetry_fixture_is_untouched() -> None:
    """Só para garantir que este arquivo não mexeu em fixture de outra feature."""
    assert load_fixture("telemetry_sample.json")["device_id"] == "tractor-01"


# --- Geometria das células, na mesma forma do /terrain (pedido do dev-front) --------------------


def test_the_replay_carries_the_cell_geometry(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """Sem `polygon`, o front teria de reproduzir a convenção de bbox que mora na API."""
    client = make_replay_client(elevation=STEEP_ELEVATION)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert len(result.terrain.cells) == len(result.day.cells)
    for cell in result.terrain.cells:
        assert len(cell.polygon) == 4
        assert all(len(pair) == 2 for pair in cell.polygon)


def test_the_geometry_and_the_levels_join_by_row_and_col(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """É o mesmo join que o mapa da fazenda já faz entre `/terrain` e `/risk`."""
    client = make_replay_client(elevation=STEEP_ELEVATION)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    geometry = {(cell.row, cell.col) for cell in result.terrain.cells}
    levels = {(cell.row, cell.col) for cell in result.day.cells}
    assert geometry == levels


def test_the_terrain_block_has_the_same_shape_as_the_farm_endpoint(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """O front reaproveita o código que já existe, então a forma tem de ser idêntica."""
    client = make_replay_client(elevation=STEEP_ELEVATION)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    assert result.terrain.grid_size == replay_service.REPLAY_GRID_SIZE
    assert result.terrain.cell_size_m.x_m > 0
    assert result.terrain.stats.slope_max_deg > 0
    cell = result.terrain.cells[0]
    assert {"row", "col", "lat", "lon", "polygon", "elevation_m", "slope_deg"} <= set(
        cell.model_dump()
    )


def test_jabora_shows_the_steep_cell_next_to_the_calm_one(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """O melhor argumento visual do projeto: o ponto é manso e a ribanceira ao lado é 🔴."""
    client = make_replay_client(elevation=STEEP_ELEVATION)

    result = replay_service.run_replay(client, POINT, PAST_DAY)

    by_position = {(cell.row, cell.col): cell for cell in result.terrain.cells}
    point_slope = by_position[(result.point_cell.row, result.point_cell.col)].slope_deg
    steepest = max(cell.slope_deg for cell in result.terrain.cells)

    assert steepest > point_slope, "a grade precisa mostrar o contraste, não achatá-lo"


# --- Placar agregado ------------------------------------------------------------------------------


def test_the_summary_counts_both_verdicts(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    client = make_replay_client(weather_code=95)

    summary = replay_service.summarize_cases(client)

    assert summary.total_cases == len(replay_service.load_cases())
    assert summary.evaluated == summary.total_cases
    assert summary.failed_case_ids == []
    # A tempestade em todos os casos mockados faz todos alertarem.
    assert summary.would_alert_at_point == summary.evaluated
    assert summary.would_alert_in_grid == summary.evaluated
    assert len(summary.cases) == summary.evaluated


def test_the_summary_note_frames_the_number(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """A ressalva viaja colada: cinco casos não são amostra."""
    client = make_replay_client()

    summary = replay_service.summarize_cases(client)

    assert "não é taxa de acerto do produto" in summary.note
    assert "não são amostra" in summary.note
    assert "coordenada de município" in summary.note
    assert str(summary.evaluated) in summary.note


def test_the_summary_says_when_a_case_could_not_be_evaluated(
    make_client: Callable[..., OpenMeteoClient],
) -> None:
    """Critério: o placar não pode mentir por omissão dizendo "2 de 5" tendo avaliado 3."""
    cases = replay_service.load_cases()
    failing_id = cases[0].id

    def handler(request: httpx.Request) -> httpx.Response:
        # A primeira fazenda sintética tem o id do caso; derrubamos só a elevação dela.
        if "elevation" in request.url.path and not _elevation_already_cached[0]:
            _elevation_already_cached[0] = True
            return httpx.Response(500, text="erro na origem")
        if "elevation" in request.url.path:
            return httpx.Response(200, json={"elevation": [900.0] * 9})
        return httpx.Response(200, json=history_payload(PAST_DAY))

    _elevation_already_cached = [False]
    summary = replay_service.summarize_cases(make_client(handler))

    assert summary.evaluated < summary.total_cases
    assert summary.failed_case_ids
    assert failing_id in summary.failed_case_ids
    assert "não puderam ser avaliados" in summary.note


def test_the_summary_reuses_the_cached_terrain(
    make_replay_client: Callable[..., OpenMeteoClient], recorder
) -> None:  # type: ignore[no-untyped-def]
    """O resumo é caro só na primeira vez; senão vira candidato a 429 no pior momento."""
    client = make_replay_client()

    replay_service.summarize_cases(client)
    after_first = recorder.count
    replay_service.summarize_cases(client)

    assert recorder.count == after_first, "o segundo resumo foi à rede de novo"


def test_the_summary_route_answers(api: Callable[..., TestClient]) -> None:
    client = api()

    response = client.get("/api/v1/replay/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_cases"] >= 3
    assert body["note"]
    assert all(case["source_url"] for case in body["cases"])


def test_the_summary_matches_the_individual_replays(
    make_replay_client: Callable[..., OpenMeteoClient],
) -> None:
    """A terceira medição do mesmo número: o placar tem de bater com os replays um a um."""
    client = make_replay_client(weather_code=95)

    summary = replay_service.summarize_cases(client)
    one_by_one = sum(
        replay_service.run_case(client, case).would_alert for case in replay_service.load_cases()
    )

    assert summary.would_alert_at_point == one_by_one

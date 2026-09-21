"""Testes da agregação diária do clima (I1) — regras-de-risco §2."""

from datetime import date, datetime, timedelta

import httpx

from app.schemas.weather import HourlyWeather
from app.services.weather import THUNDERSTORM_WEATHER_CODES, aggregate_daily
from tests.conftest import load_fixture

FORECAST = load_fixture("open_meteo_forecast.json")

FIRST_DAY = date(2026, 9, 16)


def build_hourly(days: list[dict]) -> HourlyWeather:
    """Monta uma série horária sintética: cada item de `days` descreve as 24 h de um dia.

    Chaves aceitas (todas opcionais): `rain`, `temps`, `rh`, `wind`, `gusts`, `cape`, `codes`,
    `soil`. Valores escalares são repetidos nas 24 h; listas são usadas nas primeiras horas.
    """
    times: list[datetime] = []
    series: dict[str, list] = {
        "temperature_2m": [],
        "relative_humidity_2m": [],
        "precipitation": [],
        "weather_code": [],
        "wind_speed_10m": [],
        "wind_gusts_10m": [],
        "cape": [],
        "soil_moisture": [],
    }
    mapping = {
        "temps": "temperature_2m",
        "rh": "relative_humidity_2m",
        "rain": "precipitation",
        "codes": "weather_code",
        "wind": "wind_speed_10m",
        "gusts": "wind_gusts_10m",
        "cape": "cape",
        "soil": "soil_moisture",
    }

    for offset, day in enumerate(days):
        day_start = datetime.combine(FIRST_DAY + timedelta(days=offset), datetime.min.time())
        times.extend(day_start + timedelta(hours=hour) for hour in range(24))
        for key, name in mapping.items():
            given = day.get(key)
            if given is None:
                series[name].extend([None] * 24)
            elif isinstance(given, list):
                series[name].extend(given + [None] * (24 - len(given)))
            else:
                series[name].extend([given] * 24)

    return HourlyWeather(
        latitude=-22.12, longitude=-45.13, timezone="America/Sao_Paulo", time=times, **series
    )


SERIES_NAMES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
    "cape",
    "soil_moisture",
)


def _subset(hourly: HourlyWeather, indices: list[int]) -> HourlyWeather:
    """Recorta a série horária nos índices dados (para simular lacunas e horas fora de ordem)."""
    return HourlyWeather(
        latitude=hourly.latitude,
        longitude=hourly.longitude,
        timezone=hourly.timezone,
        time=[hourly.time[i] for i in indices],
        **{name: [getattr(hourly, name)[i] for i in indices] for name in SERIES_NAMES},
    )


def test_rain_72h_sums_the_current_and_two_previous_days() -> None:
    """regras-de-risco §2: rain_72h_mm do dia d = chuva de d−2 + d−1 + d."""
    hourly = build_hourly(
        [
            {"rain": [5.0]},
            {"rain": [10.0]},
            {"rain": [20.0]},
            {"rain": [3.0]},
        ]
    )

    days = aggregate_daily(hourly)

    assert [day.rain_mm for day in days] == [5.0, 10.0, 20.0, 3.0]
    # Os dois primeiros dias não têm histórico completo: somam só o que existe.
    assert [day.rain_72h_mm for day in days] == [5.0, 15.0, 35.0, 33.0]


def test_rain_72h_treats_a_missing_day_as_zero() -> None:
    """Com lacuna na série, a janela é por data: o dia que falta vale 0, sem puxar um dia antigo."""
    hourly = build_hourly([{"rain": [5.0]}, {"rain": [10.0]}, {"rain": [20.0]}, {"rain": [3.0]}])
    # Remove o 2º dia (16/09 + 1) da série, mantendo as datas dos demais.
    keep = [index for index, moment in enumerate(hourly.time) if moment.date() != date(2026, 9, 17)]
    with_gap = _subset(hourly, keep)

    days = aggregate_daily(with_gap)

    assert [day.date for day in days] == [date(2026, 9, 16), date(2026, 9, 18), date(2026, 9, 19)]
    # 18/09 = 20 (d) + 0 (17/09 ausente) + 5 (16/09); 19/09 = 3 + 20 + 0.
    assert [day.rain_72h_mm for day in days] == [5.0, 25.0, 23.0]


def test_days_come_out_in_chronological_order_even_if_hours_do_not() -> None:
    hourly = build_hourly([{"rain": [5.0]}, {"rain": [10.0]}, {"rain": [20.0]}])
    shuffled = _subset(hourly, list(range(48, 72)) + list(range(0, 48)))

    days = aggregate_daily(shuffled)

    assert [day.date for day in days] == [date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18)]
    assert [day.rain_72h_mm for day in days] == [5.0, 15.0, 35.0]


def test_rain_mm_sums_every_hour_of_the_day() -> None:
    hourly = build_hourly([{"rain": [0.1, 0.2, 0.4, 0.3]}])

    assert aggregate_daily(hourly)[0].rain_mm == 1.0


def test_max_and_min_use_the_whole_day() -> None:
    hourly = build_hourly(
        [
            {
                "temps": [18.0, 31.5, 24.0],
                "rh": [80.0, 28.0, 55.0],
                "wind": [5.0, 33.0, 12.0],
                "gusts": [9.0, 61.0, 20.0],
                "cape": [100.0, 2400.0, 300.0],
                "soil": [0.30, 0.36, 0.33],
            }
        ]
    )

    day = aggregate_daily(hourly)[0]

    assert day.temp_max_c == 31.5
    assert day.rh_min_pct == 28.0
    assert day.wind_max_kmh == 33.0
    assert day.gust_max_kmh == 61.0
    assert day.cape_max == 2400.0
    assert day.soil_moisture == 0.36


def test_thunderstorm_only_for_codes_95_96_and_99() -> None:
    """Bordas do limiar: 94 e 97/98 não são tempestade; 95, 96 e 99 são."""
    assert {95, 96, 99} == THUNDERSTORM_WEATHER_CODES

    for code, expected in [(94, False), (95, True), (96, True), (97, False), (99, True)]:
        day = aggregate_daily(build_hourly([{"codes": [code]}]))[0]
        assert day.thunderstorm is expected, f"weather_code {code}"


def test_thunderstorm_is_false_when_no_hour_has_a_storm_code() -> None:
    hourly = build_hourly([{"codes": [0, 3, 61, 63]}])

    assert aggregate_daily(hourly)[0].thunderstorm is False


def test_missing_values_become_none_and_rain_zero() -> None:
    """A Archive API não tem `cape`: o dia fica com `cape_max = None`, sem quebrar."""
    hourly = build_hourly([{"temps": 20.0}])

    day = aggregate_daily(hourly)[0]

    assert day.rain_mm == 0.0
    assert day.rain_72h_mm == 0.0
    assert day.cape_max is None
    assert day.soil_moisture is None
    assert day.thunderstorm is False
    assert day.temp_max_c == 20.0


def test_partial_day_is_still_aggregated() -> None:
    """Dia com menos de 24 h (borda da série) continua virando um `DailyWeather`."""
    truncated = _subset(build_hourly([{"rain": [2.0]}]), [0, 1, 2])

    days = aggregate_daily(truncated)

    assert len(days) == 1
    assert days[0].rain_mm == 2.0


def test_real_forecast_fixture_yields_ten_days_with_every_field(make_client) -> None:
    """Critério de aceite: 10 dias agregados (3 passados + 7 futuros) com os campos de §2."""
    client = make_client(lambda request: httpx.Response(200, json=FORECAST))

    days = aggregate_daily(client.fetch_hourly_forecast(-22.12, -45.13))

    assert len(days) == 10
    assert [day.date for day in days] == sorted({day.date for day in days})
    for day in days:
        assert isinstance(day.rain_mm, float)
        assert isinstance(day.rain_72h_mm, float)
        assert day.temp_max_c is not None
        assert day.rh_min_pct is not None
        assert day.wind_max_kmh is not None
        assert day.gust_max_kmh is not None
        assert day.cape_max is not None
        assert day.soil_moisture is not None
        assert isinstance(day.thunderstorm, bool)

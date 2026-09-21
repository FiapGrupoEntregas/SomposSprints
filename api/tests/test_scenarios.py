"""Testes dos cenários simulados da demo (W3 e W7) — regras-de-risco §10."""

from datetime import date, datetime, timedelta

import pytest

from app.schemas.risk import Scenario
from app.schemas.weather import HourlyWeather
from app.services.scenarios import (
    HEATWAVE_END_HOUR,
    HEATWAVE_HUMIDITY_PCT,
    HEATWAVE_START_HOUR,
    HEATWAVE_TEMP_C,
    HEATWAVE_WIND_KMH,
    HEAVY_RAIN_END_HOUR,
    HEAVY_RAIN_START_HOUR,
    HEAVY_RAIN_TOTAL_MM,
    HEAVY_RAIN_WEATHER_CODE,
    SCENARIOS,
    STORM_END_HOUR,
    STORM_GUST_KMH,
    STORM_START_HOUR,
    STORM_TOTAL_MM,
    STORM_WEATHER_CODE,
    apply_scenario,
)
from app.services.weather import aggregate_daily

TODAY = date(2026, 9, 19)

# A previsão da API cobre 3 dias passados e 7 futuros (regras-de-risco §2).
FIRST_DAY = TODAY - timedelta(days=3)
TOTAL_DAYS = 10


def build_hourly(rain_per_hour_mm: float = 0.0) -> HourlyWeather:
    """Série horária sintética de 10 dias, com a mesma chuva em toda hora."""
    times = [
        datetime.combine(FIRST_DAY + timedelta(days=offset), datetime.min.time())
        + timedelta(hours=hour)
        for offset in range(TOTAL_DAYS)
        for hour in range(24)
    ]
    size = len(times)
    return HourlyWeather(
        latitude=-22.12,
        longitude=-45.13,
        timezone="America/Sao_Paulo",
        time=times,
        temperature_2m=[20.0] * size,
        relative_humidity_2m=[70.0] * size,
        precipitation=[rain_per_hour_mm] * size,
        weather_code=[0] * size,
        wind_speed_10m=[10.0] * size,
        wind_gusts_10m=[20.0] * size,
        cape=[100.0] * size,
        soil_moisture=[0.3] * size,
    )


def rain_by_day(hourly: HourlyWeather) -> dict[date, float]:
    return {day.date: day.rain_mm for day in aggregate_daily(hourly)}


def test_heavy_rain_adds_forty_five_millimetres_on_day_plus_two() -> None:
    """regras-de-risco §10: `heavy_rain` soma 45 mm no dia +2."""
    hourly = build_hourly()

    changed = apply_scenario(hourly, Scenario.HEAVY_RAIN, TODAY)

    assert rain_by_day(changed)[TODAY + timedelta(days=2)] == HEAVY_RAIN_TOTAL_MM


def test_heavy_rain_leaves_every_other_day_untouched() -> None:
    hourly = build_hourly(rain_per_hour_mm=0.1)

    before = rain_by_day(hourly)
    after = rain_by_day(apply_scenario(hourly, Scenario.HEAVY_RAIN, TODAY))

    target = TODAY + timedelta(days=2)
    assert {day: value for day, value in after.items() if day != target} == {
        day: value for day, value in before.items() if day != target
    }
    assert after[target] == round(before[target] + HEAVY_RAIN_TOTAL_MM, 1)


def test_heavy_rain_marks_the_afternoon_hours_as_rainy() -> None:
    """As horas de 12h a 17h do dia +2 recebem o `weather_code` 63 (chuva moderada)."""
    changed = apply_scenario(build_hourly(), Scenario.HEAVY_RAIN, TODAY)

    target = TODAY + timedelta(days=2)
    rainy_hours = [
        moment.hour
        for moment, code in zip(changed.time, changed.weather_code, strict=True)
        if code == HEAVY_RAIN_WEATHER_CODE
    ]
    rainy_days = {
        moment.date()
        for moment, code in zip(changed.time, changed.weather_code, strict=True)
        if code == HEAVY_RAIN_WEATHER_CODE
    }

    assert rainy_days == {target}
    assert rainy_hours == list(range(HEAVY_RAIN_START_HOUR, HEAVY_RAIN_END_HOUR))


def test_heavy_rain_does_not_change_the_original_series() -> None:
    """As funções de cenário são puras: devolvem uma cópia."""
    hourly = build_hourly()

    apply_scenario(hourly, Scenario.HEAVY_RAIN, TODAY)

    assert set(hourly.precipitation) == {0.0}


def test_without_a_scenario_the_forecast_is_untouched() -> None:
    hourly = build_hourly(rain_per_hour_mm=0.2)

    assert apply_scenario(hourly, None, TODAY) is hourly


def test_a_series_without_the_target_day_is_returned_unchanged() -> None:
    """Se a previsão não cobrir o dia +2, o cenário não inventa horas."""
    hourly = build_hourly()

    changed = apply_scenario(hourly, Scenario.HEAVY_RAIN, TODAY + timedelta(days=30))

    assert rain_by_day(changed) == rain_by_day(hourly)


# --- storm (W7) ---------------------------------------------------------------------------------


def test_storm_hits_day_plus_two_in_the_afternoon() -> None:
    """regras-de-risco §10: dia +2, das 14h às 17h, com weather_code 95, 70 km/h e +15 mm."""
    changed = apply_scenario(build_hourly(), Scenario.STORM, TODAY)

    target = TODAY + timedelta(days=2)
    stormy = [
        (moment.hour, code, gust)
        for moment, code, gust in zip(
            changed.time, changed.weather_code, changed.wind_gusts_10m, strict=True
        )
        if moment.date() == target and code == STORM_WEATHER_CODE
    ]

    assert [hour for hour, _, _ in stormy] == list(range(STORM_START_HOUR, STORM_END_HOUR))
    assert all(gust == STORM_GUST_KMH for _, _, gust in stormy)
    assert rain_by_day(changed)[target] == STORM_TOTAL_MM


def test_storm_turns_the_day_into_a_thunderstorm_day() -> None:
    """O `weather_code` 95 é o que aciona o perigo de raio (§5.3)."""
    changed = apply_scenario(build_hourly(), Scenario.STORM, TODAY)

    days = {day.date: day for day in aggregate_daily(changed)}

    assert days[TODAY + timedelta(days=2)].thunderstorm is True
    assert days[TODAY + timedelta(days=2)].gust_max_kmh == STORM_GUST_KMH
    assert days[TODAY + timedelta(days=1)].thunderstorm is False


def test_storm_never_lowers_an_already_stronger_gust() -> None:
    """O cenário agrava a previsão; ele não pode suavizar uma rajada real maior."""
    hourly = build_hourly()
    stronger = hourly.model_copy(update={"wind_gusts_10m": [90.0] * len(hourly.time)})

    changed = apply_scenario(stronger, Scenario.STORM, TODAY)

    assert max(value for value in changed.wind_gusts_10m if value is not None) == 90.0


# --- heatwave (W7) -------------------------------------------------------------------------------


def test_heatwave_hits_day_plus_one_at_midday() -> None:
    """regras-de-risco §10: dia +1, das 12h às 16h, com 34 °C, UR de 22% e vento de 35 km/h."""
    changed = apply_scenario(build_hourly(), Scenario.HEATWAVE, TODAY)

    target = TODAY + timedelta(days=1)
    hot = [
        (moment.hour, temp, humidity, wind)
        for moment, temp, humidity, wind in zip(
            changed.time,
            changed.temperature_2m,
            changed.relative_humidity_2m,
            changed.wind_speed_10m,
            strict=True,
        )
        if moment.date() == target and temp == HEATWAVE_TEMP_C
    ]

    assert [hour for hour, _, _, _ in hot] == list(range(HEATWAVE_START_HOUR, HEATWAVE_END_HOUR))
    assert all(humidity == HEATWAVE_HUMIDITY_PCT for _, _, humidity, _ in hot)
    assert all(wind == HEATWAVE_WIND_KMH for _, _, _, wind in hot)


def test_heatwave_activates_the_three_fire_conditions() -> None:
    """Critério de aceite: o cenário aciona a regra dos 30 inteira (§5.5)."""
    changed = apply_scenario(build_hourly(), Scenario.HEATWAVE, TODAY)

    day = {day.date: day for day in aggregate_daily(changed)}[TODAY + timedelta(days=1)]

    assert day.temp_max_c == HEATWAVE_TEMP_C
    assert day.rh_min_pct == HEATWAVE_HUMIDITY_PCT
    assert day.wind_max_kmh == HEATWAVE_WIND_KMH


def test_heatwave_never_raises_an_already_lower_humidity() -> None:
    """A onda de calor resseca o ar; ela não pode umedecer um dia que já estava mais seco."""
    hourly = build_hourly()
    drier = hourly.model_copy(update={"relative_humidity_2m": [10.0] * len(hourly.time)})

    changed = apply_scenario(drier, Scenario.HEATWAVE, TODAY)

    assert max(value for value in changed.relative_humidity_2m if value is not None) == 10.0


def test_heatwave_leaves_the_rain_alone() -> None:
    hourly = build_hourly(rain_per_hour_mm=0.1)

    assert rain_by_day(apply_scenario(hourly, Scenario.HEATWAVE, TODAY)) == rain_by_day(hourly)


@pytest.mark.parametrize("scenario", list(Scenario))
def test_every_scenario_is_implemented(scenario: Scenario) -> None:
    """Um valor no enum sem função no registro viraria 500 na rota, não 422."""
    assert scenario in SCENARIOS


@pytest.mark.parametrize("scenario", list(Scenario))
def test_no_scenario_changes_the_original_series(scenario: Scenario) -> None:
    hourly = build_hourly()
    before = (list(hourly.precipitation), list(hourly.temperature_2m), list(hourly.weather_code))

    apply_scenario(hourly, scenario, TODAY)

    assert (hourly.precipitation, hourly.temperature_2m, hourly.weather_code) == before


# --- Âncoras dos valores de §10 -----------------------------------------------------------------
#
# Os testes acima usam as constantes do módulo como valor esperado, então uma mudança nelas viaja
# junto e passa despercebida. Estes três travam os números **escritos no documento**.


def test_the_heavy_rain_numbers_are_the_ones_in_the_document() -> None:
    """regras-de-risco §10: +45 mm das 12h às 18h do dia +2, com `weather_code` 63."""
    assert (
        HEAVY_RAIN_TOTAL_MM,
        HEAVY_RAIN_START_HOUR,
        HEAVY_RAIN_END_HOUR,
        HEAVY_RAIN_WEATHER_CODE,
    ) == (45.0, 12, 18, 63)


def test_the_storm_numbers_are_the_ones_in_the_document() -> None:
    """regras-de-risco §10: dia +2, das 14h às 17h, `weather_code` 95, 70 km/h e +15 mm."""
    assert (STORM_GUST_KMH, STORM_TOTAL_MM, STORM_WEATHER_CODE) == (70.0, 15.0, 95)
    assert (STORM_START_HOUR, STORM_END_HOUR) == (14, 17)


def test_the_heatwave_numbers_are_the_ones_in_the_document() -> None:
    """regras-de-risco §10: dia +1, das 12h às 16h, 34 °C, UR de 22% e vento de 35 km/h."""
    assert (HEATWAVE_TEMP_C, HEATWAVE_HUMIDITY_PCT, HEATWAVE_WIND_KMH) == (34.0, 22.0, 35.0)
    assert (HEATWAVE_START_HOUR, HEATWAVE_END_HOUR) == (12, 16)

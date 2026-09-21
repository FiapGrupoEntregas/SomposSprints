"""Cenários simulados da demo (W3).

Setembro é época seca no Sudeste e a previsão real pode não ter nenhum dia de risco. Os cenários
modificam a **previsão horária real antes da agregação diária**, exatamente como descrito em
[document/regras-de-risco.md §10](../../../document/regras-de-risco.md#10-cenários-simulados-demo).

Regras do documento que valem para todo cenário:

- o cenário **nunca** é o padrão: sem `scenario=` a resposta é a previsão real;
- a resposta sempre carrega o nome do cenário, para o front mostrar "⚠️ Cenário simulado".

Funções puras: recebem a série horária e a data de referência e devolvem uma **cópia** modificada.
"""

import logging
from collections.abc import Callable
from datetime import date, timedelta

from app.schemas.risk import Scenario
from app.schemas.weather import HourlyWeather

logger = logging.getLogger(__name__)

# regras-de-risco §10 — `heavy_rain`: +45 mm das 12h às 18h do dia +2, com weather_code 63
HEAVY_RAIN_DAY_OFFSET = 2
HEAVY_RAIN_TOTAL_MM = 45.0
HEAVY_RAIN_START_HOUR = 12
HEAVY_RAIN_END_HOUR = 18
HEAVY_RAIN_WEATHER_CODE = 63

# regras-de-risco §10 — `storm`: dia +2, das 14h às 17h, com weather_code 95,
# rajadas de 70 km/h e +15 mm
STORM_DAY_OFFSET = 2
STORM_START_HOUR = 14
STORM_END_HOUR = 17
STORM_WEATHER_CODE = 95
STORM_GUST_KMH = 70.0
STORM_TOTAL_MM = 15.0

# regras-de-risco §10 — `heatwave`: dia +1, das 12h às 16h, 34 °C, UR 22% e vento de 35 km/h
HEATWAVE_DAY_OFFSET = 1
HEATWAVE_START_HOUR = 12
HEATWAVE_END_HOUR = 16
HEATWAVE_TEMP_C = 34.0
HEATWAVE_HUMIDITY_PCT = 22.0
HEATWAVE_WIND_KMH = 35.0

ScenarioFn = Callable[[HourlyWeather, date], HourlyWeather]


def _hours_of(
    hourly: HourlyWeather, today: date, day_offset: int, start_hour: int, end_hour: int
) -> list[int]:
    """Índices das horas cheias de `[start_hour, end_hour)` no dia `today + day_offset`.

    O intervalo é **meio aberto**, convenção fixada em regras-de-risco §10: "das 14h às 17h" são
    as horas 14, 15 e 16.
    """
    target_day = today + timedelta(days=day_offset)
    return [
        index
        for index, moment in enumerate(hourly.time)
        if moment.date() == target_day and start_hour <= moment.hour < end_hour
    ]


def _warn_and_return(
    hourly: HourlyWeather, scenario: str, today: date, day_offset: int
) -> HourlyWeather:
    """Série sem o dia alvo: o cenário não inventa horas, só avisa."""
    logger.warning(
        "Cenário '%s' não encontrou as horas de %s na série; previsão devolvida sem alteração.",
        scenario,
        today + timedelta(days=day_offset),
    )
    return hourly


def heavy_rain(hourly: HourlyWeather, today: date) -> HourlyWeather:
    """Chuva forte: soma 45 mm no dia +2, distribuídos das 12h às 18h (regras-de-risco §10).

    O intervalo é tratado como `[12h, 18h)`, isto é, as horas cheias 12, 13, 14, 15, 16 e 17 — são
    6 horas de 7,5 mm. Os 45 mm são divididos igualmente entre as horas realmente presentes na
    série, para o cenário continuar somando 45 mm mesmo se a previsão vier incompleta.
    """
    indices = _hours_of(
        hourly, today, HEAVY_RAIN_DAY_OFFSET, HEAVY_RAIN_START_HOUR, HEAVY_RAIN_END_HOUR
    )
    if not indices:
        return _warn_and_return(hourly, "heavy_rain", today, HEAVY_RAIN_DAY_OFFSET)

    per_hour_mm = HEAVY_RAIN_TOTAL_MM / len(indices)
    precipitation = list(hourly.precipitation)
    weather_code = list(hourly.weather_code)
    for index in indices:
        precipitation[index] = (precipitation[index] or 0.0) + per_hour_mm
        weather_code[index] = HEAVY_RAIN_WEATHER_CODE

    return hourly.model_copy(update={"precipitation": precipitation, "weather_code": weather_code})


def storm(hourly: HourlyWeather, today: date) -> HourlyWeather:
    """Tempestade: dia +2, das 14h às 17h, com `weather_code` 95, rajadas de 70 km/h e +15 mm.

    É o cenário que aciona o perigo de **raio** (§5.3) e o de **vento** (§5.4) ao mesmo tempo:
    nos topos expostos a célula fica 🔴 pelos dois motivos.
    """
    indices = _hours_of(hourly, today, STORM_DAY_OFFSET, STORM_START_HOUR, STORM_END_HOUR)
    if not indices:
        return _warn_and_return(hourly, "storm", today, STORM_DAY_OFFSET)

    precipitation = list(hourly.precipitation)
    weather_code = list(hourly.weather_code)
    gusts = list(hourly.wind_gusts_10m)
    per_hour_mm = STORM_TOTAL_MM / len(indices)
    for index in indices:
        precipitation[index] = (precipitation[index] or 0.0) + per_hour_mm
        weather_code[index] = STORM_WEATHER_CODE
        gusts[index] = max(gusts[index] or 0.0, STORM_GUST_KMH)

    return hourly.model_copy(
        update={
            "precipitation": precipitation,
            "weather_code": weather_code,
            "wind_gusts_10m": gusts,
        }
    )


def heatwave(hourly: HourlyWeather, today: date) -> HourlyWeather:
    """Onda de calor: dia +1, das 12h às 16h, com 34 °C, UR de 22% e vento de 35 km/h.

    É o cenário da **regra dos 30** (§5.5): as três condições ativas ao mesmo tempo deixam a
    fazenda inteira 🔴 de incêndio.
    """
    indices = _hours_of(hourly, today, HEATWAVE_DAY_OFFSET, HEATWAVE_START_HOUR, HEATWAVE_END_HOUR)
    if not indices:
        return _warn_and_return(hourly, "heatwave", today, HEATWAVE_DAY_OFFSET)

    temperature = list(hourly.temperature_2m)
    humidity = list(hourly.relative_humidity_2m)
    wind = list(hourly.wind_speed_10m)
    for index in indices:
        temperature[index] = max(temperature[index] or 0.0, HEATWAVE_TEMP_C)
        humidity[index] = min(
            humidity[index] if humidity[index] is not None else 100.0, HEATWAVE_HUMIDITY_PCT
        )
        wind[index] = max(wind[index] or 0.0, HEATWAVE_WIND_KMH)

    return hourly.model_copy(
        update={
            "temperature_2m": temperature,
            "relative_humidity_2m": humidity,
            "wind_speed_10m": wind,
        }
    )


# regras-de-risco §10 — só os cenários implementados entram aqui. Um `scenario=` fora deste
# registro é recusado com 422 pela rota.
SCENARIOS: dict[Scenario, ScenarioFn] = {
    Scenario.HEAVY_RAIN: heavy_rain,
    Scenario.STORM: storm,
    Scenario.HEATWAVE: heatwave,
}


def apply_scenario(hourly: HourlyWeather, scenario: Scenario | None, today: date) -> HourlyWeather:
    """Aplica o cenário à série horária. Sem cenário, devolve a previsão real intacta."""
    if scenario is None:
        return hourly

    apply = SCENARIOS.get(scenario)
    if apply is None:  # pragma: no cover — a rota só aceita cenários do registro
        raise ValueError(f"Cenário simulado desconhecido: '{scenario}'.")

    logger.info("Cenário simulado '%s' aplicado à previsão (regras-de-risco §10).", scenario.value)
    return apply(hourly, today)

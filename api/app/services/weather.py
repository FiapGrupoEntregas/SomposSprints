"""Agregação diária do clima horário (I1).

Função pura: recebe as séries horárias já buscadas pelo cliente e devolve um indicador por dia.
Fonte da verdade dos cálculos: document/regras-de-risco.md §2.
"""

from collections.abc import Iterable, Sequence
from datetime import date, timedelta

from app.schemas.weather import DailyWeather, HourlyWeather

# regras-de-risco §2 — thunderstorm = algum weather_code em {95, 96, 99}
THUNDERSTORM_WEATHER_CODES = frozenset({95, 96, 99})

# regras-de-risco §2 — rain_72h_mm do dia d = chuva de d−2 + d−1 + d
RAIN_WINDOW_DAYS = 3

# A precipitação da Open-Meteo vem em passos de 0,1 mm; arredondar evita ruído de ponto flutuante.
RAIN_DECIMALS = 1


def aggregate_daily(hourly: HourlyWeather) -> list[DailyWeather]:
    """Agrega as horas em dias, em ordem cronológica, conforme regras-de-risco §2.

    Horas com valor ausente (`None`) são ignoradas em cada indicador. Um dia sem nenhum valor de uma
    grandeza fica com `None` nela; `rain_mm` sem nenhuma leitura vale 0,0.
    `rain_72h_mm` é somado **por data** (d−2, d−1 e d): se a série tiver uma lacuna, o dia que falta
    conta como 0 mm, em vez de puxar por engano um dia mais antigo.
    """
    indices_by_day: dict[date, list[int]] = {}
    for index, moment in enumerate(hourly.time):
        indices_by_day.setdefault(moment.date(), []).append(index)

    # A ordem vem das datas, não da ordem de chegada das horas.
    ordered_days = sorted(indices_by_day)

    rain_by_day: dict[date, float] = {
        day: round(_sum(_pick(hourly.precipitation, indices_by_day[day])), RAIN_DECIMALS)
        for day in ordered_days
    }

    days: list[DailyWeather] = []

    for day in ordered_days:
        indices = indices_by_day[day]
        codes = [int(code) for code in _pick(hourly.weather_code, indices)]

        days.append(
            DailyWeather(
                date=day,
                rain_mm=rain_by_day[day],
                rain_72h_mm=_rain_72h_mm(rain_by_day, day),
                temp_max_c=_max(_pick(hourly.temperature_2m, indices)),
                rh_min_pct=_min(_pick(hourly.relative_humidity_2m, indices)),
                wind_max_kmh=_max(_pick(hourly.wind_speed_10m, indices)),
                gust_max_kmh=_max(_pick(hourly.wind_gusts_10m, indices)),
                cape_max=_max(_pick(hourly.cape, indices)),
                thunderstorm=any(code in THUNDERSTORM_WEATHER_CODES for code in codes),
                soil_moisture=_max(_pick(hourly.soil_moisture, indices)),
            )
        )

    return days


def _rain_72h_mm(rain_by_day: dict[date, float], day: date) -> float:
    """Chuva de d−2 + d−1 + d (regras-de-risco §2). Dia ausente da série conta como 0 mm."""
    window = (
        rain_by_day.get(day - timedelta(days=offset), 0.0) for offset in range(RAIN_WINDOW_DAYS)
    )
    return round(sum(window), RAIN_DECIMALS)


def _pick(
    series: Sequence[float | None] | Sequence[int | None], indices: Iterable[int]
) -> list[float]:
    """Devolve os valores da série nos índices pedidos, descartando os ausentes."""
    return [float(series[i]) for i in indices if series[i] is not None]


def _sum(values: Sequence[float]) -> float:
    return float(sum(values))


def _max(values: Sequence[float]) -> float | None:
    return max(values) if values else None


def _min(values: Sequence[float]) -> float | None:
    return min(values) if values else None

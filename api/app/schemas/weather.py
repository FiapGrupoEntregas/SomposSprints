"""Schemas de clima: séries horárias da Open-Meteo e os indicadores diários do motor de risco.

Os campos diários e o cálculo de cada um estão em
[document/regras-de-risco.md §2](../../../document/regras-de-risco.md#2-clima-agregação-diária-i1).
"""

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator


class HourlyWeather(BaseModel):
    """Séries horárias alinhadas por índice (todas têm o mesmo tamanho de `time`).

    As horas vêm no fuso pedido à Open-Meteo (`America/Sao_Paulo`), sem *offset* no texto, por isso
    os `datetime` são ingênuos (naive). Campos que a API não devolve viram listas de `None`:
    é o caso de `cape` na Archive API.
    """

    latitude: float
    longitude: float
    timezone: str
    time: list[datetime]
    temperature_2m: list[float | None]
    relative_humidity_2m: list[float | None]
    precipitation: list[float | None]
    weather_code: list[int | None]
    wind_speed_10m: list[float | None]
    wind_gusts_10m: list[float | None]
    cape: list[float | None]
    soil_moisture: list[float | None]

    @model_validator(mode="after")
    def check_series_alignment(self) -> "HourlyWeather":
        """Garante que toda série tem o mesmo tamanho de `time` (a agregação depende disso)."""
        expected = len(self.time)
        for name in (
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "weather_code",
            "wind_speed_10m",
            "wind_gusts_10m",
            "cape",
            "soil_moisture",
        ):
            got = len(getattr(self, name))
            if got != expected:
                raise ValueError(
                    f"Série horária desalinhada: '{name}' tem {got} valores "
                    f"e 'time' tem {expected}."
                )
        return self


class DailyWeather(BaseModel):
    """Indicadores de um dia, já agregados a partir das horas (regras-de-risco §2)."""

    date: date
    rain_mm: float = Field(description="Soma da precipitação no dia, em mm.")
    rain_72h_mm: float = Field(description="Chuva de d−2 + d−1 + d, em mm.")
    temp_max_c: float | None = Field(default=None, description="Temperatura máxima do dia, em °C.")
    rh_min_pct: float | None = Field(default=None, description="Umidade relativa mínima, em %.")
    wind_max_kmh: float | None = Field(default=None, description="Vento máximo, em km/h.")
    gust_max_kmh: float | None = Field(default=None, description="Rajada máxima, em km/h.")
    cape_max: float | None = Field(default=None, description="CAPE máximo, em J/kg.")
    thunderstorm: bool = Field(description="Houve código de tempestade (95, 96 ou 99) no dia.")
    soil_moisture: float | None = Field(
        default=None,
        description="Umidade do solo máxima, em m³/m³. Só exibida na v1 (regras-de-risco §3).",
    )

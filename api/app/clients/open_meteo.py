"""Cliente único da Open-Meteo: elevação, previsão e histórico (I1).

Toda chamada à Open-Meteo passa por aqui. O cliente cuida de timeout, cache com TTL e
*stale-if-error*; quem chama recebe dados prontos ou `WeatherUnavailableError`.

`WeatherUnavailableError` não precisa de `try/except` nas rotas: `app/main.py` registra um
`exception_handler` global que a converte em **503 "Serviço de clima indisponível"** (W2).

Fontes: document/dados-e-modelo.md §2 (endpoints e variáveis, verificados em 19/09/2026) e
document/regras-de-risco.md §2 e §9.
"""

import logging
from datetime import date
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import Depends
from pydantic import ValidationError

from app.core.cache import TTLCache
from app.core.config import Settings, get_settings
from app.schemas.weather import HourlyWeather

logger = logging.getLogger(__name__)

# padroes-de-codigo.md — chamadas externas têm timeout de 10 s
REQUEST_TIMEOUT_S = 10.0

# regras-de-risco §1 — a grade da fazenda é 10×10 e cabe em 1 chamada da Elevation API
MAX_ELEVATION_POINTS = 100

# Feature I1 — TTL do cache: elevação 24 h, previsão 1 h, histórico 7 dias
ELEVATION_TTL_S = 24 * 60 * 60
FORECAST_TTL_S = 60 * 60
HISTORY_TTL_S = 7 * 24 * 60 * 60

# regras-de-risco §2 — fuso da fazenda; a Open-Meteo devolve as horas já convertidas
TIMEZONE = "America/Sao_Paulo"

# regras-de-risco §9 — a partir de 2022 usamos a Historical Forecast API; antes, a Archive (ERA5)
HISTORICAL_FORECAST_MIN_DATE = date(2022, 1, 1)

# dados-e-modelo.md §2 — variáveis horárias da previsão e da Historical Forecast API
HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
    "cape",
    "soil_moisture_3_to_9cm",
)

# dados-e-modelo.md §2 — a Archive API não tem `cape` e a umidade do solo muda de nome
ARCHIVE_HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
    "soil_moisture_0_to_7cm",
)

# Nomes possíveis da umidade do solo, por API (o primeiro que existir é usado)
SOIL_MOISTURE_KEYS = ("soil_moisture_3_to_9cm", "soil_moisture_0_to_7cm")


class WeatherUnavailableError(RuntimeError):
    """A Open-Meteo falhou e não havia nada no cache (nem vencido) para reaproveitar."""


class OpenMeteoClient:
    """Fachada da Open-Meteo. Recebe o `httpx.Client` e o cache por injeção (testes usam mocks)."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self._settings = settings
        self._http = http_client or httpx.Client(timeout=REQUEST_TIMEOUT_S)
        self._cache = cache if cache is not None else _shared_cache

    def fetch_elevations(self, lats: list[float], lons: list[float]) -> list[float]:
        """Elevação (m) de cada ponto, na mesma ordem da entrada. Até 100 pontos por chamada.

        Levanta `ValueError` se as listas tiverem tamanhos diferentes, se estiverem vazias ou se
        passarem de 100 pontos (limite da Elevation API).
        """
        if len(lats) != len(lons):
            raise ValueError("As listas de latitude e longitude precisam ter o mesmo tamanho.")
        if not lats:
            raise ValueError("Informe ao menos um ponto para consultar a elevação.")
        if len(lats) > MAX_ELEVATION_POINTS:
            raise ValueError(
                f"A Elevation API aceita no máximo {MAX_ELEVATION_POINTS} pontos por chamada; "
                f"foram informados {len(lats)}."
            )

        params = {
            "latitude": ",".join(_format_coordinate(value) for value in lats),
            "longitude": ",".join(_format_coordinate(value) for value in lons),
        }
        url = self._settings.open_meteo_elevation_url
        payload = self._get_json(url, params, ELEVATION_TTL_S)
        elevations = payload.get("elevation")
        if not isinstance(elevations, list) or len(elevations) != len(lats):
            self._drop_from_cache(url, params)
            raise WeatherUnavailableError(
                "A Open-Meteo devolveu uma lista de elevações com tamanho inesperado."
            )
        return [float(value) for value in elevations]

    def fetch_hourly_forecast(
        self,
        lat: float,
        lon: float,
        past_days: int = 3,
        forecast_days: int = 7,
    ) -> HourlyWeather:
        """Previsão horária do ponto, com dias passados e futuros (regras-de-risco §2)."""
        params = {
            "latitude": _format_coordinate(lat),
            "longitude": _format_coordinate(lon),
            "hourly": ",".join(HOURLY_VARIABLES),
            "past_days": str(past_days),
            "forecast_days": str(forecast_days),
            "timezone": TIMEZONE,
        }
        url = self._settings.open_meteo_forecast_url
        payload = self._get_json(url, params, FORECAST_TTL_S)
        return self._parse_hourly_or_drop(payload, url, params)

    def fetch_hourly_history(self, lat: float, lon: float, start: date, end: date) -> HourlyWeather:
        """Histórico horário do período (replay, W9). A API é escolhida pela data inicial.

        Datas a partir de 2022-01-01 usam a Historical Forecast API (mesmas variáveis da previsão);
        datas anteriores usam a Archive API (ERA5), que não tem `cape`.
        """
        if end < start:
            raise ValueError("A data final não pode ser anterior à data inicial.")

        use_archive = start < HISTORICAL_FORECAST_MIN_DATE
        url = (
            self._settings.open_meteo_archive_url
            if use_archive
            else self._settings.open_meteo_historical_url
        )
        variables = ARCHIVE_HOURLY_VARIABLES if use_archive else HOURLY_VARIABLES

        params = {
            "latitude": _format_coordinate(lat),
            "longitude": _format_coordinate(lon),
            "hourly": ",".join(variables),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": TIMEZONE,
        }
        payload = self._get_json(url, params, HISTORY_TTL_S)
        return self._parse_hourly_or_drop(payload, url, params)

    def _parse_hourly_or_drop(
        self, payload: dict[str, Any], url: str, params: dict[str, str]
    ) -> HourlyWeather:
        """Interpreta a resposta e, se ela estiver malformada, tira o payload do cache."""
        try:
            return _parse_hourly(payload)
        except WeatherUnavailableError:
            self._drop_from_cache(url, params)
            raise

    def _drop_from_cache(self, url: str, params: dict[str, str]) -> None:
        """Remove do cache uma resposta 200 com conteúdo inválido.

        Sem isso, um payload quebrado ficaria valendo por 1 h (previsão) ou 7 dias (histórico) e
        ainda serviria de *stale-if-error* depois — melhor tentar a rede de novo na próxima chamada.
        """
        self._cache.invalidate(_cache_key(url, params))
        logger.warning("Resposta inválida da Open-Meteo em %s; removida do cache.", url)

    def _get_json(self, url: str, params: dict[str, str], ttl_s: float) -> dict[str, Any]:
        """Faz o GET com cache. Em caso de falha, tenta o valor vencido antes de desistir."""
        key = _cache_key(url, params)

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        try:
            response = self._http.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            stale = self._cache.get_stale(key)
            if stale is not None:
                logger.warning(
                    "Open-Meteo indisponível (%s). Usando resposta vencida do cache para %s.",
                    error,
                    url,
                )
                return stale
            logger.error("Open-Meteo indisponível (%s) e sem cache para %s.", error, url)
            raise WeatherUnavailableError("Serviço de clima indisponível") from error

        self._cache.set(key, payload, ttl_s)
        return payload


# Cache compartilhado por todas as instâncias criadas sem cache próprio (a API roda em um processo).
_shared_cache = TTLCache()

_shared_client: OpenMeteoClient | None = None


def get_open_meteo_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> OpenMeteoClient:
    """Dependência do FastAPI: um único cliente (e um único cache) para todo o processo."""
    global _shared_client
    if _shared_client is None:
        _shared_client = OpenMeteoClient(settings=settings)
    return _shared_client


def _format_coordinate(value: float) -> str:
    """Formata a coordenada com 6 casas (~0,1 m), o suficiente para a grade e estável no cache."""
    return f"{value:.6f}"


def _cache_key(url: str, params: dict[str, str]) -> str:
    """Chave do cache: a URL mais os parâmetros ordenados (I1)."""
    return f"{url}?{urlencode(sorted(params.items()))}"


def _parse_hourly(payload: dict[str, Any]) -> HourlyWeather:
    """Converte a resposta da Open-Meteo em `HourlyWeather`, normalizando a umidade do solo."""
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or "time" not in hourly:
        raise WeatherUnavailableError("A Open-Meteo devolveu uma resposta horária sem dados.")

    times = hourly["time"]
    if not isinstance(times, list):
        raise WeatherUnavailableError("A Open-Meteo devolveu uma resposta horária sem dados.")
    size = len(times)

    # Toda falha de validação vira WeatherUnavailableError: é o que o módulo promete a quem chama
    # (e o que `_parse_hourly_or_drop` captura para tirar o payload inválido do cache).
    try:
        return HourlyWeather(
            latitude=float(payload.get("latitude", 0.0)),
            longitude=float(payload.get("longitude", 0.0)),
            timezone=str(payload.get("timezone", TIMEZONE)),
            time=times,
            temperature_2m=_series(hourly, "temperature_2m", size),
            relative_humidity_2m=_series(hourly, "relative_humidity_2m", size),
            precipitation=_series(hourly, "precipitation", size),
            weather_code=_series(hourly, "weather_code", size),
            wind_speed_10m=_series(hourly, "wind_speed_10m", size),
            wind_gusts_10m=_series(hourly, "wind_gusts_10m", size),
            cape=_series(hourly, "cape", size),
            soil_moisture=_soil_moisture_series(hourly, size),
        )
    except ValidationError as error:
        # Caso mais provável: dado parcial, com uma série menor que `time`
        # (o validador `check_series_alignment` de HourlyWeather pega isso).
        raise WeatherUnavailableError(
            "A Open-Meteo devolveu séries horárias desalinhadas."
        ) from error
    except (TypeError, ValueError) as error:
        raise WeatherUnavailableError(
            "A Open-Meteo devolveu uma resposta horária em formato inesperado."
        ) from error


def _series(hourly: dict[str, Any], name: str, size: int) -> list[Any]:
    """Série da variável; se a API não a devolveu, uma lista de `None` do tamanho certo."""
    values = hourly.get(name)
    if not isinstance(values, list):
        return [None] * size
    return values


def _soil_moisture_series(hourly: dict[str, Any], size: int) -> list[Any]:
    """Umidade do solo: `soil_moisture_3_to_9cm` na previsão, `soil_moisture_0_to_7cm` no ERA5."""
    for name in SOIL_MOISTURE_KEYS:
        if isinstance(hourly.get(name), list):
            return hourly[name]
    return [None] * size

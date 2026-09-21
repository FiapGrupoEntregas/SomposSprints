"""Schemas do replay de acidentes reais (W9).

O replay roda **o mesmo motor** do mapa de risco sobre uma data passada e responde à pergunta que
a banca vai fazer: *"o sistema teria alertado?"*.

Por isso a resposta carrega, junto do veredito, tudo o que o limita: a precisão da coordenada, a
fonte da notícia e qual API histórica respondeu. Um "teríamos alertado" sem essas ressalvas é uma
afirmação que não se sustenta quando alguém pergunta de onde veio o ponto.
"""

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.farm import LatLon
from app.schemas.risk import CellRisk, DayRisk
from app.schemas.terrain import TerrainResponse


class LocationPrecision(StrEnum):
    """Quão perto do acidente está a coordenada do caso (W9)."""

    EXATO = "exato"
    APROXIMADO = "aproximado"
    MUNICIPIO = "municipio"


class HistoricalSource(StrEnum):
    """Qual API histórica respondeu (regras-de-risco §9)."""

    HISTORICAL_FORECAST = "historical_forecast"
    ARCHIVE = "archive"


class ReplayCase(BaseModel):
    """Um acidente real curado pelo time, com a fonte (W9)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, description="Identificador do caso.")
    title: str = Field(min_length=1, description="Título curto, como na notícia.")
    date: dt.date = Field(description="Data do acidente, confirmada na notícia.")
    location: LatLon = Field(description="Ponto usado no replay.")
    location_precision: LocationPrecision = Field(
        description="`exato`, `aproximado` ou `municipio`. **Sempre exibido na tela.**"
    )
    municipality: str = Field(min_length=1, description="Município.")
    state: str = Field(min_length=2, max_length=2, description="Sigla da UF.")
    machine: str | None = Field(default=None, description="Tipo de máquina envolvida.")
    description: str = Field(min_length=1, description="Resumo de uma ou duas frases.")
    source_url: str = Field(
        min_length=1, description="Link da notícia. **Nenhum caso vira evidência sem ele.**"
    )


class ReplayWeather(BaseModel):
    """O clima do dia do acidente, como o motor o viu."""

    model_config = ConfigDict(extra="forbid")

    rain_mm: float = Field(description="Chuva do dia, em mm.")
    rain_72h_mm: float = Field(description="Chuva de d−2 + d−1 + d, em mm.")
    temp_max_c: float | None = Field(default=None, description="Temperatura máxima, em °C.")
    rh_min_pct: float | None = Field(default=None, description="Umidade relativa mínima, em %.")
    wind_max_kmh: float | None = Field(default=None, description="Vento máximo, em km/h.")
    gust_max_kmh: float | None = Field(default=None, description="Rajada máxima, em km/h.")
    cape_max: float | None = Field(
        default=None, description="CAPE máximo. **Sempre nulo na Archive API** (§9)."
    )
    thunderstorm: bool = Field(description="Houve código de tempestade no dia.")


class ReplayResult(BaseModel):
    """Resposta de `POST /api/v1/replay` (W9)."""

    model_config = ConfigDict(extra="forbid")

    case: ReplayCase | None = Field(
        default=None, description="O caso cadastrado, quando o replay veio de um."
    )
    date: dt.date = Field(description="Data replicada.")
    location: LatLon = Field(description="Ponto central da grade.")
    location_precision: LocationPrecision = Field(
        description="Precisão da coordenada — a ressalva mais importante do veredito."
    )

    grid_size: int = Field(gt=0, description="Lado da grade avaliada em torno do ponto.")
    reference_tilt_limit_deg: float = Field(
        gt=0.0, description="`L_ref` usado, em graus (padrão 15°)."
    )

    point_cell: CellRisk = Field(description="A célula **mais próxima** do ponto do acidente.")
    day: DayRisk = Field(description="O dia inteiro avaliado, com todas as células.")
    terrain: TerrainResponse = Field(
        description=(
            "O relevo da grade, **na mesma forma de `GET /farms/{id}/terrain`**: é daqui que "
            "sai o `polygon` de cada célula. Sem isto o front teria de reproduzir a convenção "
            "de bbox (±0,005°) que mora na API, e o mapa viraria uma matriz ao lado do ponto."
        )
    )
    weather: ReplayWeather = Field(description="O clima do dia, como o motor o viu.")

    would_alert: bool = Field(
        description="O sistema teria alertado **no ponto**: nível da célula ≥ 🟡."
    )
    would_alert_in_grid: bool = Field(
        description="Alguma célula da grade estaria em 🟡 ou 🔴 naquele dia."
    )
    verdict: str = Field(min_length=1, description="O veredito em uma frase, em português.")

    source: HistoricalSource = Field(description="Qual API histórica respondeu (§9).")
    limitations: list[str] = Field(
        default_factory=list,
        description="O que limita a leitura deste replay. **Nunca vem vazio sem motivo.**",
    )


class ReplayRequest(BaseModel):
    """Corpo de `POST /api/v1/replay`: um caso cadastrado **ou** um ponto e uma data."""

    model_config = ConfigDict(extra="forbid")

    case_id: str | None = Field(default=None, description="Identificador de um caso cadastrado.")
    lat: float | None = Field(default=None, ge=-90.0, le=90.0, description="Latitude do ponto.")
    lon: float | None = Field(default=None, ge=-180.0, le=180.0, description="Longitude.")
    date: dt.date | None = Field(default=None, description="Data a replicar (passada).")
    reference_tilt_limit_deg: float | None = Field(
        default=None, gt=0.0, le=90.0, description="`L_ref` do equipamento, em graus."
    )


class ReplayCaseVerdict(BaseModel):
    """Uma linha do placar: o caso e o que o motor disse dele."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(description="Identificador do caso.")
    title: str = Field(description="Título do caso.")
    date: dt.date = Field(description="Data do acidente.")
    municipality: str = Field(description="Município.")
    state: str = Field(description="UF.")
    would_alert: bool = Field(description="Alertaria na célula do ponto.")
    would_alert_in_grid: bool = Field(description="Alertaria em alguma célula da grade.")
    point_level: str = Field(description="Nível da célula do ponto.")
    source_url: str = Field(description="A fonte da notícia, que acompanha o caso até a tela.")


class ReplaySummary(BaseModel):
    """Placar agregado dos casos cadastrados (W9).

    O número existe porque a banca vai querer somar os vereditos de qualquer jeito; é melhor que
    ele venha daqui, com o enquadramento colado, do que seja contado de cabeça na plateia.
    """

    model_config = ConfigDict(extra="forbid")

    total_cases: int = Field(ge=0, description="Casos cadastrados.")
    evaluated: int = Field(ge=0, description="Casos que o motor conseguiu avaliar agora.")
    failed_case_ids: list[str] = Field(
        default_factory=list,
        description="Casos que falharam (Open-Meteo fora, por exemplo). O resumo não omite.",
    )

    would_alert_at_point: int = Field(ge=0, description="Alertariam na célula do ponto.")
    would_alert_in_grid: int = Field(ge=0, description="Alertariam em alguma célula da grade.")

    note: str = Field(
        min_length=1,
        description="A ressalva que viaja com o número. **Cinco casos não são amostra.**",
    )
    cases: list[ReplayCaseVerdict] = Field(
        default_factory=list, description="O veredito de cada caso, para a tela detalhar."
    )

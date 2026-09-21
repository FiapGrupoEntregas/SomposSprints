"""Schemas do mapa de relevo (W2).

A grade, a inclinação, a orientação e as classes de terreno seguem
[document/regras-de-risco.md §1](../../../document/regras-de-risco.md#1-relevo-w2).
As faixas de inclinação das estatísticas são as mesmas do perfil de subscrição (§8).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TerrainClass(StrEnum):
    """Classe de terreno de uma célula (regras-de-risco §1)."""

    LOWLAND = "lowland"
    EXPOSED = "exposed"
    SLOPE = "slope"
    FLAT = "flat"


class AspectLabel(StrEnum):
    """Rótulo de 8 direções para a orientação da encosta (regras-de-risco §1)."""

    N = "N"
    NE = "NE"
    L = "L"
    SE = "SE"
    S = "S"
    SO = "SO"
    O = "O"  # noqa: E741 — é o rótulo de "oeste", não a letra solta
    NO = "NO"


class CellSize(BaseModel):
    """Tamanho aproximado de uma célula da grade, em metros."""

    model_config = ConfigDict(extra="forbid")

    x_m: float = Field(description="Distância leste-oeste entre colunas, em metros.")
    y_m: float = Field(description="Distância norte-sul entre linhas, em metros.")


class TerrainCell(BaseModel):
    """Uma célula da grade, com a geometria já pronta para o front desenhar."""

    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=0, description="Índice da linha, de norte (0) para sul.")
    col: int = Field(ge=0, description="Índice da coluna, de oeste (0) para leste.")
    lat: float = Field(description="Latitude do centro da célula.")
    lon: float = Field(description="Longitude do centro da célula.")
    polygon: list[list[float]] = Field(
        description="Contorno da célula como pares [lon, lat], fechado pelo próprio desenho."
    )
    elevation_m: float = Field(description="Elevação do centro da célula, em metros.")
    slope_deg: float = Field(ge=0.0, description="Inclinação do terreno, em graus.")
    aspect_deg: float = Field(
        ge=0.0, lt=360.0, description="Direção para onde a encosta desce (0 = N, sentido horário)."
    )
    aspect_label: AspectLabel = Field(description="A orientação em 8 direções.")
    terrain_class: TerrainClass = Field(description="Classe de terreno da célula.")


class TerrainStats(BaseModel):
    """Resumo do relevo da fazenda, usado nos KPIs do front."""

    model_config = ConfigDict(extra="forbid")

    elevation_min_m: float = Field(description="Menor elevação da grade, em metros.")
    elevation_max_m: float = Field(description="Maior elevação da grade, em metros.")
    elevation_range_m: float = Field(description="Amplitude (máx − mín), em metros.")
    slope_max_deg: float = Field(description="Maior inclinação da grade, em graus.")
    slope_mean_deg: float = Field(description="Inclinação média da grade, em graus.")
    pct_slope_lt8: float = Field(description="% de células com inclinação abaixo de 8°.")
    pct_slope_8_15: float = Field(description="% de células com inclinação de 8° a menos de 15°.")
    pct_slope_gt15: float = Field(description="% de células com inclinação de 15° ou mais.")


class TerrainResponse(BaseModel):
    """Resposta de `GET /api/v1/farms/{farm_id}/terrain`."""

    model_config = ConfigDict(extra="forbid")

    farm_id: str = Field(description="Identificador da fazenda.")
    grid_size: int = Field(gt=0, description="Lado da grade quadrada (10 = 10 × 10 células).")
    cell_size_m: CellSize = Field(description="Tamanho aproximado de cada célula, em metros.")
    stats: TerrainStats = Field(description="Resumo do relevo da fazenda.")
    cells: list[TerrainCell] = Field(
        description="Células, de norte para sul e de oeste para leste."
    )

"""Mapa de relevo: inclinação, orientação e classes de terreno (W2).

Fonte da verdade dos cálculos:
[document/regras-de-risco.md §1](../../../document/regras-de-risco.md#1-relevo-w2).

O módulo é quase todo de **funções puras** sobre `numpy`: a única função com I/O é `get_terrain`,
que busca a elevação pelo cliente da Open-Meteo (I1) e guarda o resultado pronto por 24 h.
"""

import logging
from typing import Final

import numpy as np

from app.clients.open_meteo import OpenMeteoClient
from app.core.cache import TTLCache
from app.schemas.farm import BBox, Farm
from app.schemas.terrain import (
    AspectLabel,
    CellSize,
    TerrainCell,
    TerrainClass,
    TerrainResponse,
    TerrainStats,
)

logger = logging.getLogger(__name__)

# regras-de-risco §1 — a bbox da fazenda vira 10 × 10 = 100 pontos (1 chamada da Elevation API)
GRID_SIZE = 10

# regras-de-risco §1 — conversão de graus para metros
METERS_PER_DEGREE_LON_AT_EQUATOR = 111_320.0
METERS_PER_DEGREE_LAT = 110_540.0

# regras-de-risco §1 — amplitude (máx − mín) abaixo disso: a fazenda inteira é `flat`
FLAT_ELEVATION_RANGE_M = 5.0

# regras-de-risco §1 — percentis da elevação que separam baixada (P25) e área exposta (P75)
LOWLAND_PERCENTILE = 25
EXPOSED_PERCENTILE = 75

# regras-de-risco §8 — faixas de inclinação das estatísticas: < 8°, [8°, 15°) e ≥ 15°
SLOPE_LOW_DEG = 8.0
SLOPE_HIGH_DEG = 15.0

# Feature W2 — o relevo não muda; o mapa pronto fica 24 h em memória (mesmo TTL da elevação no I1)
TERRAIN_TTL_S = 24 * 60 * 60

# regras-de-risco §1 — rótulos de 8 direções, do norte em sentido horário
ASPECT_LABELS: Final[tuple[AspectLabel, ...]] = (
    AspectLabel.N,
    AspectLabel.NE,
    AspectLabel.L,
    AspectLabel.SE,
    AspectLabel.S,
    AspectLabel.SO,
    AspectLabel.O,
    AspectLabel.NO,
)
ASPECT_SECTOR_DEG = 360.0 / len(ASPECT_LABELS)

# Casas decimais dos números publicados na resposta (evita ruído de ponto flutuante no JSON).
COORDINATE_DECIMALS = 6
DEGREE_DECIMALS = 2
METER_DECIMALS = 1
PERCENT_DECIMALS = 1

# Cache do relevo pronto, por fazenda. Compartilhado pelo processo, como o cache do I1.
_terrain_cache = TTLCache()


def build_grid(bbox: BBox, n: int = GRID_SIZE) -> tuple[list[float], list[float]]:
    """Centros das células: latitudes de **norte para sul**, longitudes de oeste para leste.

    A bbox é dividida em `n × n` células iguais e cada ponto fica no centro da sua célula, para que
    o polígono desenhado no mapa cubra exatamente o retângulo da fazenda.
    """
    if n < 2:
        raise ValueError("A grade precisa ter pelo menos 2 × 2 células para calcular a inclinação.")

    lat_step = (bbox.north - bbox.south) / n
    lon_step = (bbox.east - bbox.west) / n
    lats = [bbox.north - (row + 0.5) * lat_step for row in range(n)]
    lons = [bbox.west + (col + 0.5) * lon_step for col in range(n)]
    return lats, lons


def cell_size_m(bbox: BBox, n: int = GRID_SIZE) -> tuple[float, float]:
    """Tamanho aproximado de uma célula em metros: `(dx_m, dy_m)` (regras-de-risco §1).

    `dx` encolhe com a latitude (`cos(lat)`), medido no centro da fazenda.
    """
    if n < 2:
        raise ValueError("A grade precisa ter pelo menos 2 × 2 células para calcular a inclinação.")

    center_lat = (bbox.north + bbox.south) / 2.0
    lat_step = (bbox.north - bbox.south) / n
    lon_step = (bbox.east - bbox.west) / n
    dx_m = lon_step * METERS_PER_DEGREE_LON_AT_EQUATOR * np.cos(np.radians(center_lat))
    dy_m = lat_step * METERS_PER_DEGREE_LAT
    return float(dx_m), float(dy_m)


def compute_slope_aspect(
    elev: np.ndarray, dx_m: float, dy_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """Inclinação (graus) e orientação (graus, 0 = N, sentido horário) de cada célula.

    As linhas da matriz vão de norte para sul, então a derivada na direção norte troca de sinal
    (`dz_dnorte = -dz_dlinha`). O `aspect` aponta para onde a encosta **desce** (§1).
    """
    elevation = np.asarray(elev, dtype=float)
    if elevation.ndim != 2:
        raise ValueError("A matriz de elevação precisa ser bidimensional.")
    if min(elevation.shape) < 2:
        raise ValueError("A matriz de elevação precisa ter pelo menos 2 linhas e 2 colunas.")

    dz_dline, dz_dcolumn = np.gradient(elevation, dy_m, dx_m)
    dz_dnorth = -dz_dline
    dz_deast = dz_dcolumn

    slope_deg = np.degrees(np.arctan(np.hypot(dz_deast, dz_dnorth)))
    aspect_deg = (np.degrees(np.arctan2(-dz_deast, -dz_dnorth)) + 360.0) % 360.0
    return slope_deg, aspect_deg


def compute_tpi(elev: np.ndarray) -> np.ndarray:
    """TPI de cada célula: elevação − média dos vizinhos (até 8; nas bordas, só os existentes)."""
    elevation = np.asarray(elev, dtype=float)
    neighbor_sum = np.zeros_like(elevation)
    neighbor_count = np.zeros_like(elevation)

    for row_shift in (-1, 0, 1):
        for col_shift in (-1, 0, 1):
            if row_shift == 0 and col_shift == 0:
                continue
            shifted = _shift(elevation, row_shift, col_shift)
            present = ~np.isnan(shifted)
            neighbor_sum += np.where(present, shifted, 0.0)
            neighbor_count += present

    return elevation - neighbor_sum / neighbor_count


def classify_cells(elev: np.ndarray) -> np.ndarray:
    """Classe de terreno de cada célula (regras-de-risco §1).

    Amplitude da fazenda menor que 5 m: tudo vira `flat`. Caso contrário, `lowland` até o P25 com
    TPI ≤ 0, `exposed` a partir do P75 com TPI ≥ 0 e `slope` no resto.
    """
    elevation = np.asarray(elev, dtype=float)
    classes = np.full(elevation.shape, TerrainClass.SLOPE.value, dtype=object)

    elevation_range_m = float(elevation.max() - elevation.min())
    if elevation_range_m < FLAT_ELEVATION_RANGE_M:
        classes[:] = TerrainClass.FLAT.value
        return classes

    p25 = float(np.percentile(elevation, LOWLAND_PERCENTILE))
    p75 = float(np.percentile(elevation, EXPOSED_PERCENTILE))
    tpi = compute_tpi(elevation)

    # Com P25 == P75 (grade concentrada em poucos valores) uma célula pode cair nas duas regras.
    # O documento não define a precedência: `exposed` é aplicada depois e vence, porque é a leitura
    # conservadora — a célula entra nos perigos de raio e vento (regras-de-risco §5.3 e §5.4).
    classes[(elevation <= p25) & (tpi <= 0.0)] = TerrainClass.LOWLAND.value
    classes[(elevation >= p75) & (tpi >= 0.0)] = TerrainClass.EXPOSED.value
    return classes


def aspect_label(deg: float) -> AspectLabel:
    """Rótulo de 8 direções do `aspect`.

    As fronteiras (22,5°, 67,5°…) já pertencem ao setor seguinte.
    """
    sector = int(((float(deg) % 360.0) + ASPECT_SECTOR_DEG / 2.0) % 360.0 // ASPECT_SECTOR_DEG)
    return ASPECT_LABELS[sector]


def build_terrain(farm: Farm, elevations: list[float], n: int = GRID_SIZE) -> TerrainResponse:
    """Monta a resposta do relevo a partir das elevações da grade. Função pura (sem rede).

    `elevations` vem na mesma ordem de `build_grid`: linha por linha, de norte para sul, e dentro da
    linha de oeste para leste.
    """
    expected = n * n
    if len(elevations) != expected:
        raise ValueError(
            f"A grade {n} × {n} precisa de {expected} elevações; "
            f"foram informadas {len(elevations)}."
        )

    lats, lons = build_grid(farm.bbox, n)
    dx_m, dy_m = cell_size_m(farm.bbox, n)
    elevation = np.asarray(elevations, dtype=float).reshape(n, n)

    slope_deg, aspect_deg = compute_slope_aspect(elevation, dx_m, dy_m)
    classes = classify_cells(elevation)

    lat_step = (farm.bbox.north - farm.bbox.south) / n
    lon_step = (farm.bbox.east - farm.bbox.west) / n

    cells = [
        TerrainCell(
            row=row,
            col=col,
            lat=round(lats[row], COORDINATE_DECIMALS),
            lon=round(lons[col], COORDINATE_DECIMALS),
            polygon=_cell_polygon(lats[row], lons[col], lat_step, lon_step),
            elevation_m=round(float(elevation[row, col]), METER_DECIMALS),
            slope_deg=round(float(slope_deg[row, col]), DEGREE_DECIMALS),
            aspect_deg=_rounded_aspect_deg(float(aspect_deg[row, col])),
            aspect_label=aspect_label(float(aspect_deg[row, col])),
            terrain_class=TerrainClass(classes[row, col]),
        )
        for row in range(n)
        for col in range(n)
    ]

    return TerrainResponse(
        farm_id=farm.id,
        grid_size=n,
        cell_size_m=CellSize(x_m=round(dx_m, METER_DECIMALS), y_m=round(dy_m, METER_DECIMALS)),
        stats=_build_stats(elevation, slope_deg),
        cells=cells,
    )


def get_terrain(farm: Farm, client: OpenMeteoClient, n: int = GRID_SIZE) -> TerrainResponse:
    """Relevo da fazenda, buscando a elevação na Open-Meteo (I1) e guardando o mapa por 24 h.

    Propaga `WeatherUnavailableError` quando a Open-Meteo falha e não há nada em cache; o tratador
    global de `app/main.py` transforma isso em 503.
    """
    key = f"terrain:{farm.id}:{n}"
    cached = _terrain_cache.get(key)
    if cached is not None:
        return cached

    lats, lons = build_grid(farm.bbox, n)
    point_lats = [lat for lat in lats for _ in lons]
    point_lons = [lon for _ in lats for lon in lons]
    elevations = client.fetch_elevations(point_lats, point_lons)

    terrain = build_terrain(farm, elevations, n)
    _terrain_cache.set(key, terrain, TERRAIN_TTL_S)
    logger.info("Relevo da fazenda '%s' calculado e guardado em cache.", farm.id)
    return terrain


def clear_terrain_cache() -> None:
    """Esvazia o cache do relevo. Usado entre testes."""
    _terrain_cache.clear()


def _rounded_aspect_deg(deg: float) -> float:
    """Arredonda o `aspect` mantendo-o no intervalo [0°, 360°).

    Sem o `% 360`, um valor em [359,995°, 360°) viraria 360,0° no arredondamento e o schema
    (`lt=360`) recusaria a célula. 360° e 0° são a mesma direção (norte).
    """
    return round(deg, DEGREE_DECIMALS) % 360.0


def _build_stats(elevation: np.ndarray, slope_deg: np.ndarray) -> TerrainStats:
    """Resumo da grade: elevação, inclinação e a distribuição por faixa (regras-de-risco §8)."""
    total = slope_deg.size
    pct_lt8 = 100.0 * float(np.count_nonzero(slope_deg < SLOPE_LOW_DEG)) / total
    pct_gt15 = 100.0 * float(np.count_nonzero(slope_deg >= SLOPE_HIGH_DEG)) / total
    pct_8_15 = 100.0 - pct_lt8 - pct_gt15

    return TerrainStats(
        elevation_min_m=round(float(elevation.min()), METER_DECIMALS),
        elevation_max_m=round(float(elevation.max()), METER_DECIMALS),
        elevation_range_m=round(float(elevation.max() - elevation.min()), METER_DECIMALS),
        slope_max_deg=round(float(slope_deg.max()), DEGREE_DECIMALS),
        slope_mean_deg=round(float(slope_deg.mean()), DEGREE_DECIMALS),
        pct_slope_lt8=round(pct_lt8, PERCENT_DECIMALS),
        pct_slope_8_15=round(pct_8_15, PERCENT_DECIMALS),
        pct_slope_gt15=round(pct_gt15, PERCENT_DECIMALS),
    )


def _cell_polygon(lat: float, lon: float, lat_step: float, lon_step: float) -> list[list[float]]:
    """Retângulo da célula em torno do centro, como pares `[lon, lat]` (formato do pydeck)."""
    half_lat = lat_step / 2.0
    half_lon = lon_step / 2.0
    south = round(lat - half_lat, COORDINATE_DECIMALS)
    north = round(lat + half_lat, COORDINATE_DECIMALS)
    west = round(lon - half_lon, COORDINATE_DECIMALS)
    east = round(lon + half_lon, COORDINATE_DECIMALS)
    return [[west, south], [east, south], [east, north], [west, north]]


def _shift(elevation: np.ndarray, row_shift: int, col_shift: int) -> np.ndarray:
    """Desloca a matriz preenchendo com `NaN`, para que as bordas não ganhem vizinhos inventados."""
    shifted = np.full(elevation.shape, np.nan)
    rows, cols = elevation.shape

    src_row = slice(max(0, -row_shift), rows - max(0, row_shift))
    dst_row = slice(max(0, row_shift), rows - max(0, -row_shift))
    src_col = slice(max(0, -col_shift), cols - max(0, col_shift))
    dst_col = slice(max(0, col_shift), cols - max(0, -col_shift))

    shifted[dst_row, dst_col] = elevation[src_row, src_col]
    return shifted

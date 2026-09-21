"""Testes das funções puras do mapa de relevo (W2). Nada aqui acessa a rede.

Os casos sintéticos validam as fórmulas de `document/regras-de-risco.md §1`; os casos com as três
fazendas usam elevações reais salvas em `tests/fixtures/terrain_elevation_*.json`.
"""

import json

import numpy as np
import pytest

from app.schemas.farm import BBox
from app.schemas.terrain import AspectLabel, TerrainClass
from app.services.farms import get_farm
from app.services.terrain import (
    FLAT_ELEVATION_RANGE_M,
    GRID_SIZE,
    aspect_label,
    build_grid,
    build_terrain,
    cell_size_m,
    classify_cells,
    compute_slope_aspect,
    compute_tpi,
)
from tests.conftest import FIXTURES_DIR

# Uma bbox de ±0,005° em torno de um ponto, como as fazendas de demonstração (W1).
DEMO_BBOX = BBox(north=-22.115, south=-22.125, west=-45.135, east=-45.125)

# Grade sintética com células de 100 m, para conferir a inclinação na mão.
SYNTHETIC_CELL_M = 100.0
SYNTHETIC_SIZE = 5

# Um desnível de 10 m a cada 100 m dá atan(0,1) = 5,71°.
SLOPE_10_IN_100_DEG = 5.710593

# Inclinação leste-oeste que coloca o aspecto em 359,997°, a borda que arredondaria para 360,0°.
ASPECT_EDGE_EAST_GRADIENT = 5.236e-5


def load_elevation(short_name: str) -> list[float]:
    """Elevações reais de uma fazenda, na ordem de `build_grid`."""
    path = FIXTURES_DIR / f"terrain_elevation_{short_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["elevation"]


def plane(rows: int, cols: int, per_row: float, per_col: float) -> np.ndarray:
    """Plano inclinado: cada linha soma `per_row` e cada coluna soma `per_col`."""
    row_index, col_index = np.indices((rows, cols))
    return row_index * per_row + col_index * per_col


# --- Grade -------------------------------------------------------------------------------------


def test_build_grid_goes_north_to_south_and_west_to_east() -> None:
    lats, lons = build_grid(DEMO_BBOX, n=GRID_SIZE)

    assert len(lats) == GRID_SIZE
    assert len(lons) == GRID_SIZE
    # Latitudes decrescem (norte → sul) e longitudes crescem (oeste → leste).
    assert lats == sorted(lats, reverse=True)
    assert lons == sorted(lons)
    # Os pontos são centros de célula: ficam meia célula para dentro da bbox.
    step = (DEMO_BBOX.north - DEMO_BBOX.south) / GRID_SIZE
    assert lats[0] == pytest.approx(DEMO_BBOX.north - step / 2)
    assert lats[-1] == pytest.approx(DEMO_BBOX.south + step / 2)


def test_build_grid_rejects_grid_smaller_than_two() -> None:
    with pytest.raises(ValueError, match="2 × 2"):
        build_grid(DEMO_BBOX, n=1)


def test_cell_size_uses_cos_lat_on_the_east_west_axis() -> None:
    dx_m, dy_m = cell_size_m(DEMO_BBOX, n=GRID_SIZE)

    # 0,001° de latitude = 110,54 m; a longitude encolhe por cos(-22,12°) ≈ 0,9265.
    assert dy_m == pytest.approx(110.54, abs=0.01)
    assert dx_m == pytest.approx(110.54 * 0.9265, rel=0.01)
    assert dx_m < dy_m


def test_cell_size_shrinks_with_latitude() -> None:
    """A mesma largura em graus vale menos metros longe do equador."""
    tropical = BBox(north=-12.595, south=-12.605, west=-55.955, east=-55.945)
    southern = BBox(north=-29.185, south=-29.195, west=-51.575, east=-51.565)

    assert cell_size_m(tropical)[0] > cell_size_m(southern)[0]


# --- Inclinação e orientação -------------------------------------------------------------------


def test_plane_rising_to_the_east_slopes_down_to_the_west() -> None:
    """Sobe 10 m a cada 100 m para leste → slope ≈ 5,71° e aspect ≈ 270° (O)."""
    elevation = plane(SYNTHETIC_SIZE, SYNTHETIC_SIZE, per_row=0.0, per_col=10.0)

    slope_deg, aspect_deg = compute_slope_aspect(elevation, SYNTHETIC_CELL_M, SYNTHETIC_CELL_M)

    assert slope_deg == pytest.approx(SLOPE_10_IN_100_DEG)
    assert aspect_deg == pytest.approx(270.0)
    assert aspect_label(float(aspect_deg[2, 2])) is AspectLabel.O


def test_plane_rising_to_the_north_slopes_down_to_the_south() -> None:
    """As linhas vão de norte para sul: subir para o norte tem que dar aspect ≈ 180° (S)."""
    # A linha 0 é a mais ao norte, então a elevação cai conforme as linhas avançam.
    elevation = plane(SYNTHETIC_SIZE, SYNTHETIC_SIZE, per_row=-10.0, per_col=0.0)

    slope_deg, aspect_deg = compute_slope_aspect(elevation, SYNTHETIC_CELL_M, SYNTHETIC_CELL_M)

    assert slope_deg == pytest.approx(SLOPE_10_IN_100_DEG)
    assert aspect_deg == pytest.approx(180.0)
    assert aspect_label(float(aspect_deg[2, 2])) is AspectLabel.S


def test_plane_rising_to_the_south_slopes_down_to_the_north() -> None:
    """Espelho do caso anterior: sem a inversão de sinal, os dois dariam o mesmo resultado."""
    elevation = plane(SYNTHETIC_SIZE, SYNTHETIC_SIZE, per_row=10.0, per_col=0.0)

    _, aspect_deg = compute_slope_aspect(elevation, SYNTHETIC_CELL_M, SYNTHETIC_CELL_M)

    assert aspect_deg == pytest.approx(0.0)
    assert aspect_label(float(aspect_deg[2, 2])) is AspectLabel.N


def test_flat_surface_has_zero_slope() -> None:
    elevation = np.full((SYNTHETIC_SIZE, SYNTHETIC_SIZE), 500.0)

    slope_deg, _ = compute_slope_aspect(elevation, SYNTHETIC_CELL_M, SYNTHETIC_CELL_M)

    assert slope_deg == pytest.approx(0.0)


def test_compute_slope_aspect_rejects_grid_too_small() -> None:
    with pytest.raises(ValueError, match="2 linhas"):
        compute_slope_aspect(np.array([[1.0, 2.0]]), SYNTHETIC_CELL_M, SYNTHETIC_CELL_M)


# --- Rótulo da orientação ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deg", "expected"),
    [
        (0.0, AspectLabel.N),
        (22.4, AspectLabel.N),
        (22.5, AspectLabel.NE),
        (67.4, AspectLabel.NE),
        (67.5, AspectLabel.L),
        (112.5, AspectLabel.SE),
        (157.5, AspectLabel.S),
        (202.5, AspectLabel.SO),
        (247.5, AspectLabel.O),
        (292.5, AspectLabel.NO),
        (337.4, AspectLabel.NO),
        (337.5, AspectLabel.N),
        (359.9, AspectLabel.N),
    ],
)
def test_aspect_label_on_sector_boundaries(deg: float, expected: AspectLabel) -> None:
    """Cada fronteira (22,5°, 67,5°…) já pertence ao setor seguinte."""
    assert aspect_label(deg) is expected


# --- Classes de terreno --------------------------------------------------------------------------


@pytest.mark.parametrize("elevation_range_m", [0.0, 4.9, 4.999])
def test_range_below_five_meters_makes_everything_flat(elevation_range_m: float) -> None:
    """regras-de-risco §1: amplitude < 5 m → a fazenda inteira é `flat`."""
    elevation = plane(GRID_SIZE, GRID_SIZE, per_row=0.0, per_col=elevation_range_m / 9.0)

    classes = classify_cells(elevation)

    assert set(np.unique(classes)) == {TerrainClass.FLAT.value}


@pytest.mark.parametrize("elevation_range_m", [5.0, 5.1])
def test_range_of_five_meters_or_more_is_not_flat(elevation_range_m: float) -> None:
    """Exatamente 5 m já sai da regra do `flat` (a condição do documento é `< 5`)."""
    elevation = plane(GRID_SIZE, GRID_SIZE, per_row=0.0, per_col=elevation_range_m / 9.0)

    classes = classify_cells(elevation)

    assert TerrainClass.FLAT.value not in set(np.unique(classes))


def test_valley_has_lowland_at_the_bottom_and_exposed_on_the_high_edges() -> None:
    """Vale sintético em V: o fundo vira `lowland` e as bordas altas viram `exposed`."""
    _, col_index = np.indices((GRID_SIZE, GRID_SIZE))
    elevation = np.abs(col_index - 4.5) * 4.0  # colunas: 18, 14, 10, 6, 2, 2, 6, 10, 14, 18

    classes = classify_cells(elevation)

    bottom_columns = classes[:, 4:6]
    high_edge_columns = np.concatenate([classes[:, :1], classes[:, -1:]], axis=1)
    hillside_columns = classes[:, 2:3]

    assert set(np.unique(bottom_columns)) == {TerrainClass.LOWLAND.value}
    assert set(np.unique(high_edge_columns)) == {TerrainClass.EXPOSED.value}
    assert set(np.unique(hillside_columns)) == {TerrainClass.SLOPE.value}


def test_tpi_uses_only_existing_neighbours_on_the_edges() -> None:
    """Numa borda, a célula tem 5 vizinhos, e no canto, 3 — nada é inventado fora da grade."""
    elevation = np.array([[0.0, 0.0, 0.0], [0.0, 9.0, 0.0], [0.0, 0.0, 0.0]])

    tpi = compute_tpi(elevation)

    # Centro: 9 − média(8 zeros) = 9.
    assert tpi[1, 1] == pytest.approx(9.0)
    # Canto: 0 − média(0, 0, 9) = −3.
    assert tpi[0, 0] == pytest.approx(-3.0)
    # Meio da borda: 0 − média(0, 0, 0, 0, 9) = −1,8.
    assert tpi[0, 1] == pytest.approx(-1.8)


# --- Fazendas de demonstração (elevação real, salva em fixture) ----------------------------------


def test_carmo_de_minas_has_varied_relief() -> None:
    """Critério de aceite: a fazenda de café mostra relevo variado (inclinação máx ≥ 8°)."""
    farm = get_farm("cafe-carmo-de-minas")
    assert farm is not None

    terrain = build_terrain(farm, load_elevation("carmo"))

    assert terrain.stats.slope_max_deg >= 8.0
    assert terrain.stats.elevation_range_m >= FLAT_ELEVATION_RANGE_M
    classes = {cell.terrain_class for cell in terrain.cells}
    assert TerrainClass.LOWLAND in classes
    assert TerrainClass.EXPOSED in classes
    assert TerrainClass.FLAT not in classes


def test_sorriso_is_entirely_flat() -> None:
    """Critério de aceite: a fazenda plana (amplitude de 3 m) tem tudo `flat`."""
    farm = get_farm("graos-sorriso")
    assert farm is not None

    terrain = build_terrain(farm, load_elevation("sorriso"))

    assert terrain.stats.elevation_range_m < FLAT_ELEVATION_RANGE_M
    assert all(cell.terrain_class is TerrainClass.FLAT for cell in terrain.cells)
    assert terrain.stats.pct_slope_lt8 == pytest.approx(100.0)


def test_serra_gaucha_has_slopes_and_all_classes() -> None:
    farm = get_farm("uva-serra-gaucha")
    assert farm is not None

    terrain = build_terrain(farm, load_elevation("serra_gaucha"))

    assert terrain.stats.slope_max_deg >= 8.0
    assert terrain.stats.pct_slope_gt15 > 0.0


# --- Montagem da resposta ------------------------------------------------------------------------


def test_build_terrain_fills_the_whole_grid_and_the_polygons_tile_the_bbox() -> None:
    farm = get_farm("cafe-carmo-de-minas")
    assert farm is not None

    terrain = build_terrain(farm, load_elevation("carmo"))

    assert terrain.farm_id == farm.id
    assert terrain.grid_size == GRID_SIZE
    assert len(terrain.cells) == GRID_SIZE * GRID_SIZE
    # As células saem de norte para sul e, dentro da linha, de oeste para leste.
    assert [(cell.row, cell.col) for cell in terrain.cells[:3]] == [(0, 0), (0, 1), (0, 2)]

    first = terrain.cells[0]
    last = terrain.cells[-1]
    assert len(first.polygon) == 4
    # O canto noroeste do primeiro polígono e o sudeste do último são os cantos da bbox.
    assert max(point[1] for point in first.polygon) == pytest.approx(farm.bbox.north)
    assert min(point[0] for point in first.polygon) == pytest.approx(farm.bbox.west)
    assert min(point[1] for point in last.polygon) == pytest.approx(farm.bbox.south)
    assert max(point[0] for point in last.polygon) == pytest.approx(farm.bbox.east)


def test_slope_percentages_add_up_to_one_hundred() -> None:
    farm = get_farm("cafe-carmo-de-minas")
    assert farm is not None

    stats = build_terrain(farm, load_elevation("carmo")).stats

    total = stats.pct_slope_lt8 + stats.pct_slope_8_15 + stats.pct_slope_gt15
    assert total == pytest.approx(100.0)


def test_aspect_just_below_360_is_not_rounded_up_to_360() -> None:
    """Um aspecto em [359,995°, 360°) tem que voltar a 0°, e não estourar o schema (`lt=360`)."""
    farm = get_farm("cafe-carmo-de-minas")
    assert farm is not None

    # Grade inclinada quase toda para o sul, com um fio de inclinação para leste: o aspecto fica
    # em 359,997°, que arredondado para 2 casas daria exatamente 360,0°.
    dx_m, dy_m = cell_size_m(farm.bbox)
    row_index, col_index = np.indices((GRID_SIZE, GRID_SIZE))
    elevation = row_index * dy_m + col_index * (ASPECT_EDGE_EAST_GRADIENT * dx_m)
    _, aspect_deg = compute_slope_aspect(elevation, dx_m, dy_m)
    assert 359.995 <= float(aspect_deg[5, 5]) < 360.0

    terrain = build_terrain(farm, [float(value) for value in elevation.ravel()])

    assert all(0.0 <= cell.aspect_deg < 360.0 for cell in terrain.cells)
    assert terrain.cells[55].aspect_deg == pytest.approx(0.0)
    assert terrain.cells[55].aspect_label is AspectLabel.N


def test_build_terrain_rejects_the_wrong_number_of_elevations() -> None:
    farm = get_farm("cafe-carmo-de-minas")
    assert farm is not None

    with pytest.raises(ValueError, match="100 elevações"):
        build_terrain(farm, [900.0] * 99)

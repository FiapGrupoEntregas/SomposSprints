"""Testes das regras puras do motor de risco (W3) — regras-de-risco §3, §4, §5.1, §5.2 e §6.

Cada limiar é testado nas bordas: logo abaixo, exatamente no limiar e logo acima.
"""

from datetime import date, timedelta

import pytest

from app.schemas.farm import BBox, Device, Farm, LatLon
from app.schemas.risk import Hazard, HazardResult, RiskLevel, SoilState
from app.schemas.terrain import (
    AspectLabel,
    CellSize,
    TerrainCell,
    TerrainClass,
    TerrainResponse,
    TerrainStats,
)
from app.schemas.weather import DailyWeather
from app.services.risk import (
    HAZARDS,
    TILT_LIMIT_STEP_DEG,
    DayContext,
    assess_cell,
    assess_day,
    assess_farm,
    bogging_hazard,
    farm_reference_tilt_limit_deg,
    fire_conditions,
    fire_hazard,
    lightning_hazard,
    rollover_hazard,
    select_days,
    soil_state,
    tilt_limit,
    wind_hazard,
    worst_level,
)

FARM_ID = "cafe-carmo-de-minas"
TODAY = date(2026, 9, 19)

# regras-de-risco §4 — L_ref padrão do projeto
L_REF_DEG = 15.0


def make_cell(
    slope_deg: float = 0.0,
    terrain_class: TerrainClass = TerrainClass.SLOPE,
    row: int = 0,
    col: int = 0,
) -> TerrainCell:
    """Célula de relevo mínima: só a inclinação e a classe importam para os perigos do W3."""
    return TerrainCell(
        row=row,
        col=col,
        lat=-22.12,
        lon=-45.13,
        polygon=[[-45.13, -22.13], [-45.12, -22.13], [-45.12, -22.12], [-45.13, -22.12]],
        elevation_m=900.0,
        slope_deg=slope_deg,
        aspect_deg=0.0,
        aspect_label=AspectLabel.N,
        terrain_class=terrain_class,
    )


def make_day(
    rain_72h_mm: float = 0.0,
    rain_mm: float = 0.0,
    day: date = TODAY,
    thunderstorm: bool = False,
    **extra: float | None,
) -> DailyWeather:
    """Indicadores diários mínimos para o motor de risco."""
    return DailyWeather(
        date=day,
        rain_mm=rain_mm,
        rain_72h_mm=rain_72h_mm,
        thunderstorm=thunderstorm,
        **extra,  # type: ignore[arg-type]
    )


def make_terrain(cells: list[TerrainCell]) -> TerrainResponse:
    """Relevo sintético: o motor só usa `farm_id` e as células."""
    return TerrainResponse(
        farm_id=FARM_ID,
        grid_size=10,
        cell_size_m=CellSize(x_m=103.0, y_m=110.5),
        stats=TerrainStats(
            elevation_min_m=900.0,
            elevation_max_m=1000.0,
            elevation_range_m=100.0,
            slope_max_deg=max((cell.slope_deg for cell in cells), default=0.0),
            slope_mean_deg=0.0,
            pct_slope_lt8=100.0,
            pct_slope_8_15=0.0,
            pct_slope_gt15=0.0,
        ),
        cells=cells,
    )


def context(soil: SoilState, limit_deg: float = 10.0) -> DayContext:
    return DayContext(soil_state=soil, tilt_limit_deg=limit_deg)


def make_farm(*base_tilt_limits_deg: float, reference_tilt_limit_deg: float = 15.0) -> Farm:
    """Fazenda sintética com um equipamento por limite informado."""
    return Farm(
        id=FARM_ID,
        name="Fazenda de teste",
        municipality="Carmo de Minas",
        state="MG",
        crop="café",
        center=LatLon(lat=-22.12, lon=-45.13),
        bbox=BBox(north=-22.115, south=-22.125, west=-45.135, east=-45.125),
        reference_tilt_limit_deg=reference_tilt_limit_deg,
        devices=[
            Device(
                device_id=f"tractor-{index:02d}",
                name=f"Trator {index:02d}",
                type="tractor",
                base_tilt_limit_deg=limit,
            )
            for index, limit in enumerate(base_tilt_limits_deg, start=1)
        ],
    )


# --- §3 Estado do solo -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rain_72h_mm", "expected"),
    [
        (0.0, SoilState.DRY),
        (9.9, SoilState.DRY),
        (10.0, SoilState.MOIST),
        (29.9, SoilState.MOIST),
        (30.0, SoilState.SATURATED),
        (120.0, SoilState.SATURATED),
    ],
)
def test_soil_state_boundaries(rain_72h_mm: float, expected: SoilState) -> None:
    """regras-de-risco §3: seco < 10 mm ≤ úmido < 30 mm ≤ encharcado."""
    assert soil_state(rain_72h_mm) is expected


# --- §4 Limite dinâmico ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("soil", "expected"),
    [
        (SoilState.DRY, 15.0),
        (SoilState.MOIST, 12.5),
        (SoilState.SATURATED, 10.0),
    ],
)
def test_tilt_limit_with_the_default_reference(soil: SoilState, expected: float) -> None:
    """regras-de-risco §4: com L_ref 15° o limite é 15,0° · 12,5° · 10,0°."""
    assert tilt_limit(L_REF_DEG, soil) == expected


@pytest.mark.parametrize(
    ("l_ref_deg", "soil", "expected"),
    [
        # floor_0.5 arredonda para baixo: 20 × 0,85 = 17,0 e 18 × 0,85 = 15,3 → 15,0
        (20.0, SoilState.MOIST, 17.0),
        (18.0, SoilState.MOIST, 15.0),
        # 12 × 0,67 = 8,04 → 8,0 (e não 8,5)
        (12.0, SoilState.SATURATED, 8.0),
    ],
)
def test_tilt_limit_always_rounds_down_in_half_degrees(
    l_ref_deg: float, soil: SoilState, expected: float
) -> None:
    assert tilt_limit(l_ref_deg, soil) == expected


@pytest.mark.parametrize(
    ("l_ref_deg", "soil"),
    [
        # 0,7 × 0,67 = 0,469 → floor_0.5 daria 0,0°, que dividiria por zero em §5.1
        (0.7, SoilState.SATURATED),
        # 0,6 × 0,85 = 0,51 → 0,5° já sem precisar do piso (a borda de cima)
        (0.6, SoilState.MOIST),
        (0.4, SoilState.DRY),
    ],
)
def test_tilt_limit_never_goes_below_half_a_degree(l_ref_deg: float, soil: SoilState) -> None:
    """regras-de-risco §4: piso de 0,5°, senão `r = slope / L_dia` (§5.1) dividiria por zero."""
    assert tilt_limit(l_ref_deg, soil) == TILT_LIMIT_STEP_DEG


def test_the_floor_limit_makes_the_cell_red_instead_of_crashing() -> None:
    """Com o limite no piso, até uma célula quase plana sai 🔴 — e nada estoura."""
    limit_deg = tilt_limit(0.7, SoilState.SATURATED)

    result = rollover_hazard(
        make_cell(slope_deg=1.0),
        make_day(rain_72h_mm=42.0),
        context(SoilState.SATURATED, limit_deg),
    )

    assert limit_deg == TILT_LIMIT_STEP_DEG
    assert result is not None
    assert result.level is RiskLevel.RED
    assert result.message == (
        "Inclinação de 1° acima do limite de 0,5° (solo encharcado: 42 mm em 72 h)."
    )


def test_tilt_limit_rejects_a_reference_of_zero() -> None:
    with pytest.raises(ValueError, match="maior que zero"):
        tilt_limit(0.0, SoilState.DRY)


# --- §5.1 Capotamento --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slope_deg", "expected"),
    [
        (0.0, RiskLevel.GREEN),
        (6.9, RiskLevel.GREEN),
        (7.0, RiskLevel.YELLOW),
        (9.9, RiskLevel.YELLOW),
        (10.0, RiskLevel.RED),
        (21.5, RiskLevel.RED),
    ],
)
def test_rollover_boundaries_with_a_limit_of_ten_degrees(
    slope_deg: float, expected: RiskLevel
) -> None:
    """regras-de-risco §5.1: r < 0,7 🟢 · 0,7 ≤ r < 1,0 🟡 · r ≥ 1,0 🔴 (com L_dia = 10°)."""
    result = rollover_hazard(
        make_cell(slope_deg=slope_deg), make_day(rain_72h_mm=42.0), context(SoilState.SATURATED)
    )

    if expected is RiskLevel.GREEN:
        assert result is None
    else:
        assert result is not None
        assert result.hazard is Hazard.ROLLOVER
        assert result.level is expected


def test_rollover_message_has_the_numbers_in_portuguese() -> None:
    """Critério de aceite: o motivo traz inclinação, limite e a chuva que encharcou o solo."""
    result = rollover_hazard(
        make_cell(slope_deg=13.0), make_day(rain_72h_mm=42.0), context(SoilState.SATURATED)
    )

    assert result is not None
    assert result.message == (
        "Inclinação de 13° acima do limite de 10° (solo encharcado: 42 mm em 72 h)."
    )


def test_rollover_yellow_message_says_the_slope_is_close_to_the_limit() -> None:
    result = rollover_hazard(
        make_cell(slope_deg=9.9), make_day(rain_72h_mm=15.5), context(SoilState.MOIST, 12.5)
    )

    assert result is not None
    assert result.message == (
        "Inclinação de 9,9° próxima do limite de 12,5° (solo úmido: 15,5 mm em 72 h)."
    )


# --- §5.2 Atolamento ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terrain_class", "soil", "rain_mm", "expected"),
    [
        (TerrainClass.LOWLAND, SoilState.SATURATED, 0.0, RiskLevel.RED),
        (TerrainClass.LOWLAND, SoilState.MOIST, 0.0, RiskLevel.YELLOW),
        (TerrainClass.LOWLAND, SoilState.DRY, 0.0, RiskLevel.GREEN),
        (TerrainClass.SLOPE, SoilState.SATURATED, 0.0, RiskLevel.GREEN),
        (TerrainClass.EXPOSED, SoilState.SATURATED, 0.0, RiskLevel.GREEN),
        (TerrainClass.FLAT, SoilState.MOIST, 0.0, RiskLevel.GREEN),
        # Chuva do dia: 49,9 mm ainda é 🟢 e 50,0 mm já é 🟡 em qualquer célula
        (TerrainClass.SLOPE, SoilState.SATURATED, 49.9, RiskLevel.GREEN),
        (TerrainClass.SLOPE, SoilState.SATURATED, 50.0, RiskLevel.YELLOW),
        (TerrainClass.FLAT, SoilState.SATURATED, 62.0, RiskLevel.YELLOW),
    ],
)
def test_bogging_boundaries(
    terrain_class: TerrainClass, soil: SoilState, rain_mm: float, expected: RiskLevel
) -> None:
    """regras-de-risco §5.2: baixada encharcada 🔴 · baixada úmida ou chuva ≥ 50 mm 🟡."""
    result = bogging_hazard(
        make_cell(terrain_class=terrain_class),
        make_day(rain_72h_mm=max(rain_mm, 42.0), rain_mm=rain_mm),
        context(soil),
    )

    if expected is RiskLevel.GREEN:
        assert result is None
    else:
        assert result is not None
        assert result.hazard is Hazard.BOGGING
        assert result.level is expected


def test_bogging_messages_have_the_numbers_in_portuguese() -> None:
    saturated = bogging_hazard(
        make_cell(terrain_class=TerrainClass.LOWLAND),
        make_day(rain_72h_mm=42.0),
        context(SoilState.SATURATED),
    )
    heavy_rain = bogging_hazard(
        make_cell(terrain_class=TerrainClass.SLOPE),
        make_day(rain_72h_mm=62.0, rain_mm=62.0),
        context(SoilState.SATURATED),
    )

    assert saturated is not None
    assert saturated.message == "Baixada com solo encharcado (42 mm em 72 h): risco de atolamento."
    assert heavy_rain is not None
    assert heavy_rain.message == "Chuva de 62 mm no dia: risco de atolamento."


# --- Nível final da célula ---------------------------------------------------------------------


def test_cell_level_is_the_worst_hazard_and_keeps_every_reason() -> None:
    """A baixada íngreme acumula capotamento 🔴 e atolamento 🔴 no mesmo dia."""
    cell = assess_cell(
        make_cell(slope_deg=13.0, terrain_class=TerrainClass.LOWLAND),
        make_day(rain_72h_mm=42.0),
        context(SoilState.SATURATED),
    )

    assert cell.level is RiskLevel.RED
    assert {reason.hazard for reason in cell.reasons} == {Hazard.ROLLOVER, Hazard.BOGGING}


def test_cell_level_is_red_when_only_one_hazard_is_red() -> None:
    """Capotamento 🟡 + atolamento 🔴 = célula 🔴, com os dois motivos guardados."""
    cell = assess_cell(
        make_cell(slope_deg=8.0, terrain_class=TerrainClass.LOWLAND),
        make_day(rain_72h_mm=42.0),
        context(SoilState.SATURATED),
    )

    assert cell.level is RiskLevel.RED
    assert [reason.level for reason in cell.reasons] == [RiskLevel.RED, RiskLevel.YELLOW]


def test_green_cell_has_no_reasons() -> None:
    cell = assess_cell(
        make_cell(slope_deg=2.0, terrain_class=TerrainClass.SLOPE),
        make_day(rain_72h_mm=0.0),
        context(SoilState.DRY, 15.0),
    )

    assert cell.level is RiskLevel.GREEN
    assert cell.reasons == []


def test_worst_level_of_an_empty_set_is_green() -> None:
    assert worst_level([]) is RiskLevel.GREEN


# --- Motor extensível (W7 acrescenta perigos sem mexer na rota) ---------------------------------


def test_a_new_hazard_only_needs_to_join_the_registry() -> None:
    """O W7 acrescenta raio, vento e incêndio assim: uma função a mais em `HAZARDS`."""

    def always_red(cell: TerrainCell, day: DailyWeather, ctx: DayContext) -> HazardResult:
        return HazardResult(
            hazard=Hazard.BOGGING, level=RiskLevel.RED, message="Perigo de teste (0 mm)."
        )

    cell = assess_cell(
        make_cell(slope_deg=0.0),
        make_day(),
        context(SoilState.DRY, 15.0),
        hazards=[rollover_hazard, bogging_hazard, always_red],
    )

    assert cell.level is RiskLevel.RED
    assert cell.reasons[0].message == "Perigo de teste (0 mm)."


# --- §6 Resumo do dia --------------------------------------------------------------------------


def test_day_summary_matches_the_cells() -> None:
    """Critério de aceite: o pior nível do card bate com o pior nível das células daquele dia."""
    cells = [
        make_cell(slope_deg=13.0, row=0, col=0),  # 🔴 capotamento
        make_cell(slope_deg=8.0, row=0, col=1),  # 🟡 capotamento
        make_cell(slope_deg=1.0, row=1, col=0),  # 🟢
        make_cell(slope_deg=1.0, row=1, col=1),  # 🟢
    ]

    day_risk = assess_day(make_terrain(cells), make_day(rain_72h_mm=42.0), L_REF_DEG)

    assert day_risk.soil_state is SoilState.SATURATED
    assert day_risk.tilt_limit_deg == 10.0
    assert day_risk.worst_level is RiskLevel.RED
    assert day_risk.pct_levels.red == 25.0
    assert day_risk.pct_levels.yellow == 25.0
    assert day_risk.pct_levels.green == 50.0
    assert [reason.level for reason in day_risk.top_reasons] == [RiskLevel.RED, RiskLevel.YELLOW]
    assert all("°" in reason.message for reason in day_risk.top_reasons)


def test_day_summary_of_a_dry_day_is_all_green() -> None:
    cells = [make_cell(slope_deg=9.0, terrain_class=TerrainClass.LOWLAND)]

    day_risk = assess_day(make_terrain(cells), make_day(rain_72h_mm=0.0), L_REF_DEG)

    assert day_risk.soil_state is SoilState.DRY
    assert day_risk.tilt_limit_deg == 15.0
    assert day_risk.worst_level is RiskLevel.GREEN
    assert day_risk.pct_levels.green == 100.0
    assert day_risk.top_reasons == []


def test_top_reasons_are_ordered_by_severity_then_frequency() -> None:
    cells = [make_cell(slope_deg=8.0, col=index) for index in range(3)]
    cells.append(make_cell(slope_deg=13.0, col=3))

    day_risk = assess_day(make_terrain(cells), make_day(rain_72h_mm=42.0), L_REF_DEG)

    assert len(day_risk.top_reasons) == 2
    assert day_risk.top_reasons[0].level is RiskLevel.RED
    assert day_risk.top_reasons[1].level is RiskLevel.YELLOW
    # O 🟡 representa 3 células com a mesma inclinação, então é a mensagem delas que aparece.
    assert "8°" in day_risk.top_reasons[1].message


# --- Seleção dos dias --------------------------------------------------------------------------


def test_select_days_keeps_only_today_onward() -> None:
    daily = [make_day(day=TODAY + timedelta(days=offset)) for offset in range(-3, 7)]

    selected = select_days(daily, TODAY, days=7)

    assert [day.date for day in selected] == [TODAY + timedelta(days=n) for n in range(7)]


def test_select_days_limits_the_window() -> None:
    daily = [make_day(day=TODAY + timedelta(days=offset)) for offset in range(-3, 7)]

    assert [day.date for day in select_days(daily, TODAY, days=1)] == [TODAY]


@pytest.mark.parametrize("days", [0, 8])
def test_select_days_rejects_a_window_outside_one_to_seven(days: int) -> None:
    with pytest.raises(ValueError, match="de 1 a 7 dias"):
        select_days([], TODAY, days=days)


def test_assess_farm_keeps_the_farm_and_the_scenario() -> None:
    terrain = make_terrain([make_cell(slope_deg=13.0)])
    daily = [make_day(rain_72h_mm=42.0, day=TODAY + timedelta(days=offset)) for offset in range(3)]

    forecast = assess_farm(terrain, daily, L_REF_DEG)

    assert forecast.farm_id == FARM_ID
    assert forecast.scenario is None
    assert [day.date for day in forecast.days] == [TODAY + timedelta(days=n) for n in range(3)]
    assert forecast.generated_at.tzinfo is not None


# --- §4 L_ref do mapa da fazenda (leitura conservadora) ----------------------------------------


def test_farm_reference_uses_the_most_fragile_machine() -> None:
    """regras-de-risco §4: o mapa da fazenda usa o **menor** `base_tilt_limit_deg`."""
    farm = make_farm(15.0, 12.0)

    l_ref_deg = farm_reference_tilt_limit_deg(farm)

    assert l_ref_deg == 12.0
    assert tilt_limit(l_ref_deg, SoilState.DRY) == 12.0


def test_farm_reference_falls_back_to_the_farm_when_there_is_no_device() -> None:
    """Sem equipamento, vale o `reference_tilt_limit_deg` da fazenda (§4)."""
    # `Farm` exige ao menos um equipamento, então a lista vazia vem por `model_copy` (sem validar).
    farm = make_farm(15.0, reference_tilt_limit_deg=13.0).model_copy(update={"devices": []})

    assert farm_reference_tilt_limit_deg(farm) == 13.0


def test_farm_reference_ignores_the_farm_value_when_a_device_is_stricter() -> None:
    """O equipamento manda: um L_ref de fazenda mais alto não afrouxa o mapa."""
    farm = make_farm(10.0, reference_tilt_limit_deg=20.0)

    assert farm_reference_tilt_limit_deg(farm) == 10.0


# --- §5.3 Raio (W7) ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terrain_class", "thunderstorm", "cape_max", "expected"),
    [
        # Tempestade: 🔴 no topo exposto, 🟡 no resto
        (TerrainClass.EXPOSED, True, None, RiskLevel.RED),
        (TerrainClass.SLOPE, True, None, RiskLevel.YELLOW),
        (TerrainClass.LOWLAND, True, None, RiskLevel.YELLOW),
        (TerrainClass.FLAT, True, None, RiskLevel.YELLOW),
        # Sem tempestade: só o CAPE alto em célula exposta
        (TerrainClass.EXPOSED, False, 1999.0, RiskLevel.GREEN),
        (TerrainClass.EXPOSED, False, 2000.0, RiskLevel.YELLOW),
        (TerrainClass.EXPOSED, False, 3000.0, RiskLevel.YELLOW),
        (TerrainClass.SLOPE, False, 3000.0, RiskLevel.GREEN),
        (TerrainClass.EXPOSED, False, None, RiskLevel.GREEN),
    ],
)
def test_lightning_boundaries(
    terrain_class: TerrainClass, thunderstorm: bool, cape_max: float | None, expected: RiskLevel
) -> None:
    """regras-de-risco §5.3: tempestade + exposta 🔴 · tempestade 🟡 · CAPE ≥ 2000 e exposta 🟡."""
    day = make_day(thunderstorm=thunderstorm, cape_max=cape_max)

    result = lightning_hazard(make_cell(terrain_class=terrain_class), day, context(SoilState.DRY))

    if expected is RiskLevel.GREEN:
        assert result is None
    else:
        assert result is not None
        assert result.hazard is Hazard.LIGHTNING
        assert result.level is expected


def test_lightning_messages() -> None:
    exposed = lightning_hazard(
        make_cell(terrain_class=TerrainClass.EXPOSED),
        make_day(thunderstorm=True),
        context(SoilState.DRY),
    )
    cape = lightning_hazard(
        make_cell(terrain_class=TerrainClass.EXPOSED),
        make_day(cape_max=2100.0),
        context(SoilState.DRY),
    )

    assert exposed is not None
    assert exposed.message == "Tempestade prevista em topo exposto: risco de raio."
    assert cape is not None
    assert cape.message == "Instabilidade alta (CAPE de 2100 J/kg) em topo exposto: risco de raio."


# --- §5.4 Vento (W7) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terrain_class", "gust_max_kmh", "expected"),
    [
        (TerrainClass.EXPOSED, 44.9, RiskLevel.GREEN),
        (TerrainClass.EXPOSED, 45.0, RiskLevel.YELLOW),
        (TerrainClass.EXPOSED, 59.9, RiskLevel.YELLOW),
        (TerrainClass.EXPOSED, 60.0, RiskLevel.RED),
        (TerrainClass.SLOPE, 44.9, RiskLevel.GREEN),
        (TerrainClass.SLOPE, 45.0, RiskLevel.GREEN),
        (TerrainClass.SLOPE, 59.9, RiskLevel.GREEN),
        (TerrainClass.SLOPE, 60.0, RiskLevel.YELLOW),
        (TerrainClass.LOWLAND, 70.0, RiskLevel.YELLOW),
        (TerrainClass.EXPOSED, None, RiskLevel.GREEN),
    ],
)
def test_wind_boundaries(
    terrain_class: TerrainClass, gust_max_kmh: float | None, expected: RiskLevel
) -> None:
    """regras-de-risco §5.4: 🔴 ≥ 60 km/h em exposta · 🟡 ≥ 60 nas demais ou 45–60 em exposta."""
    day = make_day(gust_max_kmh=gust_max_kmh)

    result = wind_hazard(make_cell(terrain_class=terrain_class), day, context(SoilState.DRY))

    if expected is RiskLevel.GREEN:
        assert result is None
    else:
        assert result is not None
        assert result.hazard is Hazard.WIND
        assert result.level is expected


def test_wind_message_has_the_gust() -> None:
    result = wind_hazard(
        make_cell(terrain_class=TerrainClass.EXPOSED),
        make_day(gust_max_kmh=65.0),
        context(SoilState.DRY),
    )

    assert result is not None
    assert result.message == "Rajadas de 65 km/h em área exposta: risco para máquinas altas."


# --- §5.5 Incêndio, a "regra dos 30" (W7) --------------------------------------------------------


@pytest.mark.parametrize(
    ("temp_max_c", "rh_min_pct", "wind_max_kmh", "expected_conditions"),
    [
        # Cada limiar nas bordas: > 30 °C, < 30% e > 30 km/h
        (30.0, 40.0, 10.0, 0),
        (30.1, 40.0, 10.0, 1),
        (20.0, 30.0, 10.0, 0),
        (20.0, 29.9, 10.0, 1),
        (20.0, 40.0, 30.0, 0),
        (20.0, 40.0, 30.1, 1),
        (30.1, 29.9, 10.0, 2),
        (30.1, 29.9, 30.1, 3),
        (None, 29.9, 30.1, 2),
        (None, None, None, 0),
    ],
)
def test_fire_conditions_boundaries(
    temp_max_c: float | None,
    rh_min_pct: float | None,
    wind_max_kmh: float | None,
    expected_conditions: int,
) -> None:
    """regras-de-risco §5.5: temperatura > 30 °C, UR < 30% e vento > 30 km/h."""
    day = make_day(temp_max_c=temp_max_c, rh_min_pct=rh_min_pct, wind_max_kmh=wind_max_kmh)

    assert len(fire_conditions(day)) == expected_conditions


@pytest.mark.parametrize(
    ("temp_max_c", "rh_min_pct", "wind_max_kmh", "slope_deg", "expected"),
    [
        (30.1, 40.0, 10.0, 0.0, RiskLevel.GREEN),
        (30.1, 29.9, 10.0, 0.0, RiskLevel.YELLOW),
        (30.1, 29.9, 30.1, 0.0, RiskLevel.RED),
        # O fogo sobe a encosta: 2 condições viram 🔴 a partir de 15°
        (30.1, 29.9, 10.0, 14.9, RiskLevel.YELLOW),
        (30.1, 29.9, 10.0, 15.0, RiskLevel.RED),
        # Com as 3 condições já é 🔴 em qualquer inclinação
        (30.1, 29.9, 30.1, 15.0, RiskLevel.RED),
    ],
)
def test_fire_boundaries(
    temp_max_c: float,
    rh_min_pct: float,
    wind_max_kmh: float,
    slope_deg: float,
    expected: RiskLevel,
) -> None:
    day = make_day(temp_max_c=temp_max_c, rh_min_pct=rh_min_pct, wind_max_kmh=wind_max_kmh)

    result = fire_hazard(make_cell(slope_deg=slope_deg), day, context(SoilState.DRY))

    if expected is RiskLevel.GREEN:
        assert result is None
    else:
        assert result is not None
        assert result.hazard is Hazard.FIRE
        assert result.level is expected


def test_fire_message_lists_the_active_conditions() -> None:
    day = make_day(temp_max_c=32.0, rh_min_pct=25.0, wind_max_kmh=35.0)

    result = fire_hazard(make_cell(slope_deg=2.0), day, context(SoilState.DRY))

    assert result is not None
    assert result.message == (
        "Regra dos 30: 3 de 3 condições (32 °C, UR de 25%, vento de 35 km/h): risco de incêndio."
    )


def test_fire_message_explains_the_slope_escalation() -> None:
    day = make_day(temp_max_c=32.0, rh_min_pct=25.0, wind_max_kmh=10.0)

    result = fire_hazard(make_cell(slope_deg=16.0), day, context(SoilState.DRY))

    assert result is not None
    assert result.level is RiskLevel.RED
    assert "Encosta de 16°: o fogo sobe mais rápido." in result.message


# --- O registro de perigos (W7 entrou sem mexer no motor) ---------------------------------------


def test_every_hazard_of_the_document_is_registered() -> None:
    """regras-de-risco §5.1 a §5.5: os cinco perigos avaliados em cada célula."""
    assert [hazard.__name__ for hazard in HAZARDS] == [
        "rollover_hazard",
        "bogging_hazard",
        "lightning_hazard",
        "wind_hazard",
        "fire_hazard",
    ]


def test_a_storm_day_stacks_the_reasons_of_several_hazards() -> None:
    """Uma crista exposta, num dia de tempestade com vento: dois motivos 🔴 na mesma célula."""
    cell = assess_cell(
        make_cell(slope_deg=2.0, terrain_class=TerrainClass.EXPOSED),
        make_day(thunderstorm=True, gust_max_kmh=70.0),
        context(SoilState.DRY, 15.0),
    )

    assert cell.level is RiskLevel.RED
    assert {reason.hazard for reason in cell.reasons} == {Hazard.LIGHTNING, Hazard.WIND}
    assert all(reason.level is RiskLevel.RED for reason in cell.reasons)

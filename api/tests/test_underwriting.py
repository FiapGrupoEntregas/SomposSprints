"""Testes do perfil de subscrição (W8) — regras-de-risco §8."""

import json
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.main import app
from app.schemas.terrain import TerrainClass
from app.schemas.underwriting import RiskClass
from app.services.terrain import build_terrain, clear_terrain_cache
from app.services.underwriting import (
    CLASS_A_SCORE,
    CLASS_B_SCORE,
    MAX_DRIVERS,
    WEIGHTS_VERSION,
    class_for,
    score_drivers,
    terrain_profile,
    terrain_score,
)
from tests.conftest import FIXTURES_DIR
from tests.test_limits import make_farm
from tests.test_risk_rules import make_cell, make_terrain

CARMO_ID = "cafe-carmo-de-minas"
SORRISO_ID = "graos-sorriso"
SERRA_ID = "uva-serra-gaucha"


def elevation(short_name: str) -> list[float]:
    path = FIXTURES_DIR / f"terrain_elevation_{short_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["elevation"]


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    clear_terrain_cache()
    yield
    clear_terrain_cache()
    app.dependency_overrides.clear()


# --- §8 O score -------------------------------------------------------------------------------


def test_the_example_from_the_specification() -> None:
    """Critério de aceite: 23% > 15°, 40% de 8–15°, 8% baixada, 15% exposta → 48,5, classe B.

    100 − (1,0·23 + 0,5·40 + 0,5·8 + 0,3·15) = 100 − 51,5 = 48,5
    """
    score = terrain_score(
        pct_slope_gt15=23.0, pct_slope_8_15=40.0, pct_lowland=8.0, pct_exposed=15.0
    )

    assert score == 48.5
    assert class_for(score) is RiskClass.B


def test_a_perfect_terrain_scores_one_hundred() -> None:
    assert terrain_score(0.0, 0.0, 0.0, 0.0) == 100.0
    assert class_for(100.0) is RiskClass.A


def test_the_score_never_goes_below_zero() -> None:
    """Uma fazenda inteira íngreme, em baixada e exposta passaria de 100 de desconto."""
    score = terrain_score(
        pct_slope_gt15=100.0, pct_slope_8_15=100.0, pct_lowland=100.0, pct_exposed=100.0
    )

    assert score == 0.0
    assert class_for(score) is RiskClass.C


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        # Os números são os do **documento**, escritos à mão de propósito: usar as constantes do
        # código faria o teste acompanhar qualquer mudança nelas e nunca acusar nada.
        (0.0, RiskClass.C),
        (39.9, RiskClass.C),
        (40.0, RiskClass.B),
        (69.9, RiskClass.B),
        (70.0, RiskClass.A),
        (100.0, RiskClass.A),
    ],
)
def test_class_boundaries(score: float, expected: RiskClass) -> None:
    """regras-de-risco §8: A ≥ 70 · B de 40 a 69 · C < 40."""
    assert class_for(score) is expected


def test_the_class_thresholds_are_the_ones_in_the_document() -> None:
    """Trava as constantes nos valores de §8, já que o resto do módulo as usa por referência."""
    assert CLASS_A_SCORE == 70.0
    assert CLASS_B_SCORE == 40.0


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # Cada peso isolado, para provar que é o do documento
        ({"pct_slope_gt15": 10.0}, 90.0),
        ({"pct_slope_8_15": 10.0}, 95.0),
        ({"pct_lowland": 10.0}, 95.0),
        ({"pct_exposed": 10.0}, 97.0),
    ],
)
def test_each_weight_matches_the_document(kwargs: dict[str, float], expected: float) -> None:
    base = {
        "pct_slope_gt15": 0.0,
        "pct_slope_8_15": 0.0,
        "pct_lowland": 0.0,
        "pct_exposed": 0.0,
    }
    assert terrain_score(**{**base, **kwargs}) == expected


# --- "O que mais pesa" --------------------------------------------------------------------------


def test_the_drivers_come_ordered_by_how_much_they_cost() -> None:
    drivers = score_drivers(
        pct_slope_gt15=23.0, pct_slope_8_15=40.0, pct_lowland=8.0, pct_exposed=15.0
    )

    assert [driver.factor for driver in drivers] == [
        "pct_slope_gt15",  # 23,0 pontos
        "pct_slope_8_15",  # 20,0 pontos
        "pct_exposed",  # 4,5 pontos
    ]
    assert [driver.points for driver in drivers] == [23.0, 20.0, 4.5]
    assert len(drivers) <= MAX_DRIVERS


def test_the_driver_label_carries_the_number() -> None:
    drivers = score_drivers(
        pct_slope_gt15=23.0, pct_slope_8_15=0.0, pct_lowland=0.0, pct_exposed=0.0
    )

    assert drivers[0].label == "23% da área com inclinação de 15° ou mais"


def test_a_factor_that_costs_nothing_is_left_out() -> None:
    """ "0% em baixada" só ocuparia espaço na tela do subscritor."""
    drivers = score_drivers(
        pct_slope_gt15=10.0, pct_slope_8_15=0.0, pct_lowland=0.0, pct_exposed=0.0
    )

    assert [driver.factor for driver in drivers] == ["pct_slope_gt15"]


def test_a_flat_farm_has_no_drivers_at_all() -> None:
    assert score_drivers(0.0, 0.0, 0.0, 0.0) == []


# --- O perfil completo --------------------------------------------------------------------------


def test_the_profile_of_a_flat_terrain_is_class_a() -> None:
    """Critério de aceite: a fazenda plana tira A."""
    farm = make_farm()
    terrain = build_terrain(farm, elevation("sorriso"))

    profile = terrain_profile(terrain, farm)

    assert profile.risk_class is RiskClass.A
    assert profile.pct_slope_gt15 == 0.0
    assert profile.pct_lowland == 0.0
    assert profile.drivers == []


def test_the_profile_of_the_coffee_farm_is_b_or_c() -> None:
    """Critério de aceite: a fazenda de café tira B ou C."""
    farm = make_farm()
    terrain = build_terrain(farm, elevation("carmo"))

    profile = terrain_profile(terrain, farm)

    assert profile.risk_class in {RiskClass.B, RiskClass.C}
    assert profile.pct_slope_gt15 > 0.0
    assert profile.drivers


def test_the_profile_always_scores_between_zero_and_one_hundred() -> None:
    """Critério de aceite: o score fica sempre entre 0 e 100."""
    farm = make_farm()

    for short_name in ("carmo", "sorriso", "serra_gaucha"):
        profile = terrain_profile(build_terrain(farm, elevation(short_name)), farm)
        assert 0.0 <= profile.terrain_score <= 100.0


def test_the_class_percentages_come_from_the_grid() -> None:
    cells = [
        make_cell(col=0, terrain_class=TerrainClass.LOWLAND),
        make_cell(col=1, terrain_class=TerrainClass.EXPOSED),
        make_cell(col=2, terrain_class=TerrainClass.SLOPE),
        make_cell(col=3, terrain_class=TerrainClass.SLOPE),
    ]
    farm = make_farm()

    profile = terrain_profile(make_terrain(cells), farm)

    assert profile.pct_lowland == 25.0
    assert profile.pct_exposed == 25.0


def test_the_profile_says_the_weights_are_not_calibrated() -> None:
    """O subscritor precisa saber que o número ainda não foi ajustado em dado de sinistro."""
    farm = make_farm()

    profile = terrain_profile(build_terrain(farm, elevation("carmo")), farm)

    assert profile.weights_version == WEIGHTS_VERSION == "v1-arbitrario"
    assert "não calibrados" in profile.calibration_note
    assert "Sompo" in profile.calibration_note


# --- GET /api/v1/farms/{farm_id}/underwriting ----------------------------------------------------


@pytest.fixture
def api(make_client: Callable[..., OpenMeteoClient]) -> Callable[..., TestClient]:
    def factory(short_name: str = "carmo", failing: bool = False) -> TestClient:
        def handler(_: httpx.Request) -> httpx.Response:
            if failing:
                return httpx.Response(500, text="erro na origem")
            path = FIXTURES_DIR / f"terrain_elevation_{short_name}.json"
            return httpx.Response(200, json=json.loads(path.read_text(encoding="utf-8")))

        app.dependency_overrides[get_open_meteo_client] = lambda: make_client(handler)
        return TestClient(app)

    return factory


def test_the_route_returns_the_profile(api: Callable[..., TestClient]) -> None:
    client = api("carmo")

    response = client.get(f"/api/v1/farms/{CARMO_ID}/underwriting")

    assert response.status_code == 200
    body = response.json()
    assert body["farm_id"] == CARMO_ID
    assert body["municipality"] == "Carmo de Minas"
    assert body["risk_class"] in {"A", "B", "C"}
    assert 0.0 <= body["terrain_score"] <= 100.0
    assert body["drivers"]
    assert body["weights_version"] == "v1-arbitrario"


def test_the_flat_farm_is_class_a_through_the_route(api: Callable[..., TestClient]) -> None:
    client = api("sorriso")

    body = client.get(f"/api/v1/farms/{SORRISO_ID}/underwriting").json()

    assert body["risk_class"] == "A"
    assert body["terrain_score"] == 100.0


def test_the_vineyard_profile_comes_from_its_own_terrain(api: Callable[..., TestClient]) -> None:
    client = api("serra_gaucha")

    body = client.get(f"/api/v1/farms/{SERRA_ID}/underwriting").json()

    assert body["state"] == "RS"
    assert body["crop"] == "uva"
    assert body["pct_lowland"] > 0.0


def test_an_unknown_farm_returns_404(api: Callable[..., TestClient]) -> None:
    response = api().get("/api/v1/farms/fazenda-inexistente/underwriting")

    assert response.status_code == 404
    assert response.json()["detail"] == "Fazenda não encontrada"


def test_open_meteo_down_returns_503(api: Callable[..., TestClient]) -> None:
    response = api(failing=True).get(f"/api/v1/farms/{CARMO_ID}/underwriting")

    assert response.status_code == 503

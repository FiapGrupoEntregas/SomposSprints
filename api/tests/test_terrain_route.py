"""Testes de `GET /api/v1/farms/{farm_id}/terrain` (W2). A Open-Meteo é sempre mockada."""

import json
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.main import app
from app.services.terrain import GRID_SIZE, clear_terrain_cache
from tests.conftest import FIXTURES_DIR, RequestRecorder

DEMO_FARM_ID = "cafe-carmo-de-minas"
FLAT_FARM_ID = "graos-sorriso"
UNKNOWN_FARM_ID = "fazenda-inexistente"

CELL_COUNT = GRID_SIZE * GRID_SIZE

ClientFactory = Callable[..., OpenMeteoClient]


def elevation_payload(short_name: str) -> dict:
    """Resposta da Elevation API para a grade de uma fazenda, salva de uma chamada real."""
    path = FIXTURES_DIR / f"terrain_elevation_{short_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def clean_terrain_cache() -> Iterator[None]:
    """O relevo fica 24 h em cache: cada teste começa com ele vazio."""
    clear_terrain_cache()
    yield
    clear_terrain_cache()
    app.dependency_overrides.clear()


@pytest.fixture
def api(
    make_client: ClientFactory,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], TestClient]:
    """Devolve um `TestClient` cujo cliente da Open-Meteo usa o transporte falso informado."""

    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> TestClient:
        open_meteo = make_client(handler)
        app.dependency_overrides[get_open_meteo_client] = lambda: open_meteo
        return TestClient(app)

    return factory


def elevation_handler(short_name: str) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=elevation_payload(short_name))

    return handler


def failing_handler(_: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="erro na origem")


def test_terrain_returns_the_full_grid(api: Callable[..., TestClient]) -> None:
    client = api(elevation_handler("carmo"))

    response = client.get(f"/api/v1/farms/{DEMO_FARM_ID}/terrain")

    assert response.status_code == 200
    body = response.json()
    assert body["farm_id"] == DEMO_FARM_ID
    assert body["grid_size"] == GRID_SIZE
    assert len(body["cells"]) == CELL_COUNT
    assert body["cell_size_m"]["x_m"] > 0
    # Critério de aceite: Carmo de Minas tem relevo variado.
    assert body["stats"]["slope_max_deg"] >= 8.0
    assert body["stats"]["elevation_range_m"] > 0


def test_terrain_cell_has_everything_the_map_needs(api: Callable[..., TestClient]) -> None:
    client = api(elevation_handler("carmo"))

    cell = client.get(f"/api/v1/farms/{DEMO_FARM_ID}/terrain").json()["cells"][0]

    assert set(cell) == {
        "row",
        "col",
        "lat",
        "lon",
        "polygon",
        "elevation_m",
        "slope_deg",
        "aspect_deg",
        "aspect_label",
        "terrain_class",
    }
    assert len(cell["polygon"]) == 4
    assert cell["terrain_class"] in {"lowland", "exposed", "slope", "flat"}


def test_flat_farm_has_every_cell_flat(api: Callable[..., TestClient]) -> None:
    client = api(elevation_handler("sorriso"))

    body = client.get(f"/api/v1/farms/{FLAT_FARM_ID}/terrain").json()

    assert {cell["terrain_class"] for cell in body["cells"]} == {"flat"}


def test_terrain_asks_open_meteo_once_per_farm(
    api: Callable[..., TestClient], recorder: RequestRecorder
) -> None:
    """Cache de 24 h: a segunda chamada não vai à rede (critério de aceite dos < 200 ms)."""
    client = api(elevation_handler("carmo"))

    first = client.get(f"/api/v1/farms/{DEMO_FARM_ID}/terrain")
    second = client.get(f"/api/v1/farms/{DEMO_FARM_ID}/terrain")

    assert first.status_code == 200
    assert second.json() == first.json()
    assert recorder.count == 1


def test_terrain_sends_one_request_with_the_hundred_grid_points(
    api: Callable[..., TestClient], recorder: RequestRecorder
) -> None:
    client = api(elevation_handler("carmo"))

    client.get(f"/api/v1/farms/{DEMO_FARM_ID}/terrain")

    assert recorder.count == 1
    params = recorder.last.url.params
    assert len(params["latitude"].split(",")) == CELL_COUNT
    assert len(params["longitude"].split(",")) == CELL_COUNT


def test_unknown_farm_returns_404(
    api: Callable[..., TestClient], recorder: RequestRecorder
) -> None:
    client = api(elevation_handler("carmo"))

    response = client.get(f"/api/v1/farms/{UNKNOWN_FARM_ID}/terrain")

    assert response.status_code == 404
    assert response.json()["detail"] == "Fazenda não encontrada"
    # O 404 vem antes de qualquer chamada externa: fazenda inexistente não gasta a Open-Meteo.
    assert recorder.count == 0


def test_open_meteo_down_returns_503(api: Callable[..., TestClient]) -> None:
    """Pendência do I1: o tratador global converte `WeatherUnavailableError` em 503."""
    client = api(failing_handler)

    response = client.get(f"/api/v1/farms/{DEMO_FARM_ID}/terrain")

    assert response.status_code == 503
    assert response.json()["detail"] == "Serviço de clima indisponível"

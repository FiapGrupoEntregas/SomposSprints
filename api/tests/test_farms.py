"""Testes das fazendas de demonstração (W1). Nada aqui acessa a rede."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.farms import (
    FARMS_FILE,
    FarmsDataError,
    find_device,
    get_farm,
    list_farms,
    load_farms,
    load_farms_from_file,
)

client = TestClient(app)

DEMO_FARM_ID = "cafe-carmo-de-minas"
DEMO_DEVICE_ID = "tractor-01"
EXPECTED_FARM_COUNT = 3


def read_catalog() -> list[dict]:
    """Conteúdo cru do catálogo versionado, para montar variações nos testes."""
    return json.loads(FARMS_FILE.read_text(encoding="utf-8"))


def write_catalog(path: Path, farms: list[dict]) -> Path:
    path.write_text(json.dumps(farms), encoding="utf-8")
    return path


# --- Rotas -----------------------------------------------------------------------------------


def test_list_farms_returns_three_summaries() -> None:
    response = client.get("/api/v1/farms")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == EXPECTED_FARM_COUNT
    assert {farm["id"] for farm in body} == {
        DEMO_FARM_ID,
        "graos-sorriso",
        "uva-serra-gaucha",
    }
    # O resumo não carrega geometria nem equipamentos.
    assert set(body[0]) == {"id", "name", "municipality", "state", "crop"}


def test_get_farm_returns_geometry_and_devices() -> None:
    response = client.get(f"/api/v1/farms/{DEMO_FARM_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["municipality"] == "Carmo de Minas"
    assert body["state"] == "MG"
    assert body["center"] == {"lat": -22.12, "lon": -45.13}
    assert body["bbox"] == {
        "north": -22.115,
        "south": -22.125,
        "west": -45.135,
        "east": -45.125,
    }
    assert body["reference_tilt_limit_deg"] == 15.0
    assert [device["device_id"] for device in body["devices"]] == [DEMO_DEVICE_ID]


def test_get_farm_unknown_returns_404_in_portuguese() -> None:
    response = client.get("/api/v1/farms/xyz")

    assert response.status_code == 404
    assert response.json()["detail"] == "Fazenda não encontrada"


# --- Catálogo versionado ---------------------------------------------------------------------


def test_catalog_is_valid_and_cached() -> None:
    farms = load_farms()

    assert len(farms) == EXPECTED_FARM_COUNT
    # Lido uma única vez: a segunda chamada devolve exatamente o mesmo objeto.
    assert load_farms() is farms
    assert list_farms() is farms


def test_every_farm_has_center_inside_bbox_and_one_device() -> None:
    for farm in load_farms():
        assert farm.bbox.contains(farm.center), farm.id
        assert farm.devices, farm.id


def test_get_farm_and_find_device() -> None:
    farm = get_farm(DEMO_FARM_ID)

    assert farm is not None
    assert farm.crop == "café"
    assert get_farm("nao-existe") is None

    found = find_device(DEMO_DEVICE_ID)
    assert found is not None
    owner, device = found
    assert owner.id == DEMO_FARM_ID
    assert device.base_tilt_limit_deg == 15.0
    assert find_device("nao-existe") is None


# --- Validação do arquivo (a API não sobe com dado errado) -------------------------------------


def test_missing_field_names_the_field(tmp_path: Path) -> None:
    farms = read_catalog()
    del farms[0]["municipality"]
    path = write_catalog(tmp_path / "farms.json", farms)

    with pytest.raises(FarmsDataError) as error:
        load_farms_from_file(path)

    message = str(error.value)
    assert "posição 0" in message
    assert "municipality" in message


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    farms = read_catalog()
    farms[0]["municipallity"] = "Carmo de Minas"  # erro de digitação
    path = write_catalog(tmp_path / "farms.json", farms)

    with pytest.raises(FarmsDataError, match="municipallity"):
        load_farms_from_file(path)


def test_center_outside_bbox_is_rejected(tmp_path: Path) -> None:
    farms = read_catalog()
    farms[0]["center"]["lat"] = -23.0
    path = write_catalog(tmp_path / "farms.json", farms)

    with pytest.raises(FarmsDataError, match="fora da bbox"):
        load_farms_from_file(path)


def test_inverted_bbox_is_rejected(tmp_path: Path) -> None:
    farms = read_catalog()
    farms[0]["bbox"]["north"], farms[0]["bbox"]["south"] = (
        farms[0]["bbox"]["south"],
        farms[0]["bbox"]["north"],
    )
    path = write_catalog(tmp_path / "farms.json", farms)

    with pytest.raises(FarmsDataError, match="bbox inválida"):
        load_farms_from_file(path)


def test_duplicated_farm_id_is_rejected(tmp_path: Path) -> None:
    farms = read_catalog()
    farms[1]["id"] = farms[0]["id"]
    path = write_catalog(tmp_path / "farms.json", farms)

    with pytest.raises(FarmsDataError, match="'id' de fazenda repetido"):
        load_farms_from_file(path)


def test_duplicated_device_id_across_farms_is_rejected(tmp_path: Path) -> None:
    farms = read_catalog()
    farms[1]["devices"][0]["device_id"] = DEMO_DEVICE_ID
    path = write_catalog(tmp_path / "farms.json", farms)

    with pytest.raises(FarmsDataError, match="'device_id' repetido"):
        load_farms_from_file(path)


def test_farm_without_devices_is_rejected(tmp_path: Path) -> None:
    farms = read_catalog()
    farms[0]["devices"] = []
    path = write_catalog(tmp_path / "farms.json", farms)

    with pytest.raises(FarmsDataError, match="devices"):
        load_farms_from_file(path)


def test_broken_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "farms.json"
    path.write_text("[{", encoding="utf-8")

    with pytest.raises(FarmsDataError, match="não é um JSON válido"):
        load_farms_from_file(path)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FarmsDataError, match="Não foi possível ler"):
        load_farms_from_file(tmp_path / "nao-existe.json")


def test_empty_catalog_is_rejected(tmp_path: Path) -> None:
    path = write_catalog(tmp_path / "farms.json", [])

    with pytest.raises(FarmsDataError, match="pelo menos uma fazenda"):
        load_farms_from_file(path)

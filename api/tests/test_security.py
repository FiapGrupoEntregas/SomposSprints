"""Testes do controle de acesso por chave de API (I5, ADR-013)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import (
    INVALID_API_KEY_MESSAGE,
    is_valid_api_key,
    mask_api_key,
    parse_api_keys,
)
from app.main import app

DEVICE_ID = "tractor-01"
PUBLISH_URL = f"/api/v1/devices/{DEVICE_ID}/limit/publish"
AUDIT_URL = "/api/v1/audit"

API_KEY = "chave-de-teste-1234"
OTHER_KEY = "segunda-chave-9876"
PROTECTED_READ_PATHS = [
    "/api/v1/farms",
    "/api/v1/farms/farm-demo",
    "/api/v1/farms/farm-demo/terrain",
    "/api/v1/farms/farm-demo/risk",
    "/api/v1/farms/farm-demo/recommendations",
    "/api/v1/farms/farm-demo/underwriting",
    "/api/v1/devices/tractor-01/limit",
    "/api/v1/devices/tractor-01/status",
    "/api/v1/devices/tractor-01/telemetry/latest",
    "/api/v1/devices/tractor-01/telemetry",
    "/api/v1/devices/tractor-01/history",
    "/api/v1/devices/tractor-01/events",
    "/api/v1/reports/equipment/tractor-01",
    "/api/v1/reports/equipment/tractor-01.csv",
    "/api/v1/reports/region",
    "/api/v1/reports/region.csv",
    "/api/v1/reports/crop",
    "/api/v1/reports/crop.csv",
    "/api/v1/replay/cases",
    "/api/v1/replay/summary",
]


@pytest.fixture(autouse=True)
def clean_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="dev", api_keys=f"{API_KEY},{OTHER_KEY}"
    )
    return TestClient(app)


@pytest.fixture
def client_without_keys() -> TestClient:
    """API sem `AGRISHIELD_API_KEYS`: tem que **falhar fechada**."""
    app.dependency_overrides[get_settings] = lambda: Settings(environment="dev", api_keys="")
    return TestClient(app)


@pytest.fixture
def production_client() -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="production", api_keys=API_KEY
    )
    return TestClient(app)


# --- Leitura da configuração ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("uma-chave", ("uma-chave",)),
        ("a,b", ("a", "b")),
        (" a , b ", ("a", "b")),
        ("a,,b,", ("a", "b")),
        ("", ()),
        ("   ", ()),
    ],
)
def test_parse_api_keys(raw: str, expected: tuple[str, ...]) -> None:
    assert parse_api_keys(raw) == expected


# --- Mascaramento ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("chave-de-teste-1234", "****1234"),
        (None, "(ausente)"),
        ("", "(ausente)"),
        # Curta demais: mostrar a cauda entregaria quase a chave inteira.
        ("12345678", "****"),
        ("abc", "****"),
        ("123456789", "****6789"),
    ],
)
def test_mask_api_key(key: str | None, expected: str) -> None:
    assert mask_api_key(key) == expected


def test_the_mask_never_shows_the_whole_key() -> None:
    assert API_KEY not in mask_api_key(API_KEY)


# --- Comparação --------------------------------------------------------------------------------


def test_a_configured_key_is_accepted() -> None:
    assert is_valid_api_key(API_KEY, (API_KEY, OTHER_KEY)) is True
    assert is_valid_api_key(OTHER_KEY, (API_KEY, OTHER_KEY)) is True


@pytest.mark.parametrize(
    "candidate", [None, "", "errada", "chave-de-teste-123", "CHAVE-DE-TESTE-1234"]
)
def test_a_wrong_key_is_refused(candidate: str | None) -> None:
    assert is_valid_api_key(candidate, (API_KEY,)) is False


def test_without_configured_keys_nothing_is_valid() -> None:
    """Falha fechada: esquecer a variável de ambiente não pode virar porta aberta."""
    assert is_valid_api_key(API_KEY, ()) is False
    assert is_valid_api_key("", ()) is False


def test_a_key_with_accents_does_not_break_the_comparison() -> None:
    """`compare_digest` recusa `str` não-ASCII: por isso comparamos bytes."""
    assert is_valid_api_key("chave-com-acentuação", ("chave-com-acentuação",)) is True
    assert is_valid_api_key("chave-com-acentuação", (API_KEY,)) is False


# --- Na rota ------------------------------------------------------------------------------------


def test_publishing_without_a_key_returns_401(client: TestClient) -> None:
    """Critério de aceite: `POST .../limit/publish` sem chave → 401."""
    response = client.post(PUBLISH_URL, json={})

    assert response.status_code == 401
    assert response.json()["detail"] == INVALID_API_KEY_MESSAGE


def test_publishing_with_a_wrong_key_returns_401(client: TestClient) -> None:
    response = client.post(PUBLISH_URL, json={}, headers={"X-API-Key": "nao-e-essa"})

    assert response.status_code == 401


def test_the_401_does_not_reveal_whether_the_key_exists(client: TestClient) -> None:
    """Ausente e inválida devolvem a **mesma** mensagem, para não contar o estado do servidor."""
    missing = client.post(PUBLISH_URL, json={})
    wrong = client.post(PUBLISH_URL, json={}, headers={"X-API-Key": "nao-e-essa"})

    assert missing.json() == wrong.json()


def test_publishing_without_configured_keys_returns_401(client_without_keys: TestClient) -> None:
    response = client_without_keys.post(PUBLISH_URL, json={}, headers={"X-API-Key": API_KEY})

    assert response.status_code == 401


def test_the_audit_trail_requires_a_key(client: TestClient) -> None:
    """Critério de aceite: `GET /audit` exige chave."""
    assert client.get(AUDIT_URL).status_code == 401
    assert client.get(AUDIT_URL, headers={"X-API-Key": "nao-e-essa"}).status_code == 401


def test_reading_stays_open_in_the_demo(client: TestClient) -> None:
    """Leituras públicas são mantidas em `dev` explícito para a demo local."""
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/farms").status_code == 200


@pytest.mark.parametrize("path", PROTECTED_READ_PATHS)
def test_production_read_routes_require_a_key(path: str, production_client: TestClient) -> None:
    response = production_client.get(path)

    assert response.status_code == 401
    assert response.json()["detail"] == INVALID_API_KEY_MESSAGE


@pytest.mark.parametrize("path", PROTECTED_READ_PATHS)
def test_production_read_routes_reject_an_invalid_key(
    path: str, production_client: TestClient
) -> None:
    response = production_client.get(path, headers={"X-API-Key": "invalida"})

    assert response.status_code == 401
    assert response.json()["detail"] == INVALID_API_KEY_MESSAGE


def test_production_read_succeeds_with_a_valid_key(production_client: TestClient) -> None:
    response = production_client.get("/api/v1/farms", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200


def test_health_stays_public_in_production(production_client: TestClient) -> None:
    assert production_client.get("/api/v1/health").status_code == 200


def test_ambiguous_environment_does_not_open_reads() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(environment="", api_keys=API_KEY)
    response = TestClient(app).get("/api/v1/farms")

    assert response.status_code == 401


def test_missing_environment_defaults_to_a_closed_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGRISHIELD_ENVIRONMENT", raising=False)

    assert Settings(_env_file=None).environment == "production"


def test_the_401_carries_the_authenticate_header(client: TestClient) -> None:
    response = client.post(PUBLISH_URL, json={})

    assert response.headers["WWW-Authenticate"] == "X-API-Key"

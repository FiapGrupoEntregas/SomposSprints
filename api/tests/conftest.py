"""Utilidades comuns aos testes. Nada aqui acessa a rede **nem o broker MQTT**."""

import json
import os
from collections.abc import Callable
from pathlib import Path

# Precisa vir antes de qualquer import de `app`, porque `get_settings()` é cacheado no primeiro uso:
# a ponte MQTT (I2) fica desligada e o banco (I3) é em memória, para nenhum teste tocar a rede nem
# deixar um `agrishield.db` para trás.
os.environ["AGRISHIELD_ENVIRONMENT"] = "dev"
os.environ["AGRISHIELD_MQTT_ENABLED"] = "false"
os.environ["AGRISHIELD_DATABASE_URL"] = "sqlite://"
os.environ["AGRISHIELD_API_KEYS"] = ""
for _mqtt_credential_setting in (
    "AGRISHIELD_MQTT_CA_CERT",
    "AGRISHIELD_MQTT_CLIENT_CERT",
    "AGRISHIELD_MQTT_CLIENT_KEY",
    "AGRISHIELD_MQTT_USERNAME",
    "AGRISHIELD_MQTT_PASSWORD",
):
    os.environ[_mqtt_credential_setting] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.clients.open_meteo import OpenMeteoClient  # noqa: E402
from app.core.cache import TTLCache  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.db import create_db, get_engine  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Carrega uma resposta real da Open-Meteo salva em tests/fixtures/."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class RequestRecorder:
    """Guarda as requisições feitas pelo `MockTransport`, para contar acessos à rede."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    @property
    def count(self) -> int:
        return len(self.requests)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


@pytest.fixture
def recorder() -> RequestRecorder:
    return RequestRecorder()


@pytest.fixture
def make_client(
    recorder: RequestRecorder,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], OpenMeteoClient]:
    """Fábrica de `OpenMeteoClient` com transporte falso, cache limpo e contador de requisições."""

    def factory(
        handler: Callable[[httpx.Request], httpx.Response],
        cache: TTLCache | None = None,
    ) -> OpenMeteoClient:
        def recording_handler(request: httpx.Request) -> httpx.Response:
            recorder.requests.append(request)
            return handler(request)

        http_client = httpx.Client(transport=httpx.MockTransport(recording_handler))
        return OpenMeteoClient(
            settings=Settings(),
            http_client=http_client,
            cache=cache if cache is not None else TTLCache(),
        )

    return factory


@pytest.fixture(scope="session", autouse=True)
def create_tables() -> None:
    """Cria as tabelas no banco em memória do processo, uma vez por sessão de testes.

    Rotas que gravam na trilha de auditoria (I5) usam a sessão real quando o teste não injeta a
    dele. Sem isto, elas passariam ou falhariam conforme a **ordem** dos arquivos de teste — que
    é exatamente o tipo de fragilidade que só aparece na CI.
    """
    create_db(get_engine())

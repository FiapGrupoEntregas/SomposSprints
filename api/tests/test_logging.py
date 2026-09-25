"""Testes do log estruturado e do `request_id` (I5).

O ponto mais importante aqui não é o formato: é o que **não** aparece no log.
"""

import io
import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.config import Settings, get_settings
from app.core.logging import (
    MAX_REQUEST_BODY_BYTES,
    REQUEST_ID_HEADER,
    REQUEST_ID_PATTERN,
    JsonFormatter,
    _request_id_from,
    request_id_var,
)
from app.main import app

API_KEY = "chave-de-teste-1234"
PUBLISH_URL = "/api/v1/devices/tractor-01/limit/publish"


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """Captura o log da raiz já formatado em JSON, como ele sai em produção."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers, root.level
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        yield stream
    finally:
        root.handlers, root.level = previous_handlers, previous_level


@pytest.fixture(autouse=True)
def clean_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(api_keys=API_KEY)
    return TestClient(app)


def lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def access_lines(stream: io.StringIO) -> list[dict]:
    return [line for line in lines(stream) if line.get("logger") == "app.access"]


def app_output(stream: io.StringIO) -> str:
    """Só o que a **nossa** aplicação registrou.

    O `httpx` do `TestClient` registra a URL inteira de cada chamada que ele mesmo faz; em
    produção isso fica em WARNING (`NOISY_LIBRARY_LOGGERS`), mas aqui o filtro deixa o teste
    falar do log da API, que é o que a I5 promete.
    """
    return "\n".join(
        json.dumps(line, ensure_ascii=False)
        for line in lines(stream)
        if str(line.get("logger", "")).startswith("app")
    )


# --- Formato ------------------------------------------------------------------------------------


def test_every_record_is_one_json_line(log_stream: io.StringIO) -> None:
    logging.getLogger("teste").info("mensagem de teste")

    entry = lines(log_stream)[0]
    assert entry["level"] == "INFO"
    assert entry["logger"] == "teste"
    assert entry["message"] == "mensagem de teste"
    assert entry["ts"]


def test_the_extras_become_fields(log_stream: io.StringIO) -> None:
    logging.getLogger("teste").info("com extras", extra={"device_id": "tractor-01", "status": 200})

    entry = lines(log_stream)[0]
    assert entry["device_id"] == "tractor-01"
    assert entry["status"] == 200


def test_the_request_id_enters_every_line_of_the_request(log_stream: io.StringIO) -> None:
    token = request_id_var.set("abc123")
    try:
        logging.getLogger("teste").info("dentro da requisição")
    finally:
        request_id_var.reset(token)

    assert lines(log_stream)[0]["request_id"] == "abc123"


def test_without_a_request_there_is_no_request_id(log_stream: io.StringIO) -> None:
    logging.getLogger("teste").info("fora de requisição")

    assert "request_id" not in lines(log_stream)[0]


def test_an_exception_goes_into_the_json(log_stream: io.StringIO) -> None:
    try:
        raise ValueError("falha proposital")
    except ValueError:
        logging.getLogger("teste").exception("deu erro")

    assert "ValueError" in lines(log_stream)[0]["exception"]


# --- Cabeçalho e rastreio -----------------------------------------------------------------------


def test_every_response_carries_the_request_id(client: TestClient) -> None:
    """Critério de aceite: toda resposta traz `X-Request-ID`."""
    response = client.get("/api/v1/health")

    assert response.headers[REQUEST_ID_HEADER]


def test_a_client_supplied_request_id_is_reused(client: TestClient) -> None:
    """Deixa seguir uma chamada do front até a API sem cruzar horários."""
    response = client.get("/api/v1/health", headers={REQUEST_ID_HEADER: "id-do-front"})

    assert response.headers[REQUEST_ID_HEADER] == "id-do-front"


def test_the_same_id_appears_in_the_access_log(client: TestClient, log_stream: io.StringIO) -> None:
    """Critério de aceite: o id da resposta é o mesmo que está no log da requisição."""
    response = client.get("/api/v1/health")

    entry = access_lines(log_stream)[-1]
    assert entry["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert entry["method"] == "GET"
    assert entry["path"] == "/api/v1/health"
    assert entry["status"] == 200
    assert entry["duration_ms"] >= 0


def test_an_error_response_is_logged_too(client: TestClient, log_stream: io.StringIO) -> None:
    response = client.get("/api/v1/farms/fazenda-inexistente")

    assert response.status_code == 404
    assert access_lines(log_stream)[-1]["status"] == 404
    assert response.headers[REQUEST_ID_HEADER]


# --- O que nunca pode aparecer no log -----------------------------------------------------------


def test_the_api_key_never_reaches_the_log(client: TestClient, log_stream: io.StringIO) -> None:
    """Critério de aceite: nenhuma chave aparece no log — nem quando ela é aceita."""
    client.post(PUBLISH_URL, json={}, headers={"X-API-Key": API_KEY})

    assert API_KEY not in app_output(log_stream)
    # Também não pode vazar pelo log de terceiros que o TestClient produz.
    assert API_KEY not in log_stream.getvalue()


def test_a_refused_key_is_logged_masked(client: TestClient, log_stream: io.StringIO) -> None:
    """A tentativa é registrada (para investigar), mas só a cauda mascarada."""
    client.post(PUBLISH_URL, json={}, headers={"X-API-Key": "chave-errada-9999"})

    output = app_output(log_stream)
    assert "chave-errada-9999" not in output
    assert "****9999" in output


def test_the_query_string_and_the_body_stay_out_of_the_log(
    client: TestClient, log_stream: io.StringIO
) -> None:
    """O log registra o caminho, não a *query string* nem o corpo: é o que impede vazamento."""
    client.get("/api/v1/farms?segredo=nao-deve-aparecer")

    assert "nao-deve-aparecer" not in app_output(log_stream)
    assert access_lines(log_stream)[-1]["path"] == "/api/v1/farms"


# --- Tamanho do corpo ---------------------------------------------------------------------------


def test_a_huge_body_is_refused_with_413(client: TestClient) -> None:
    """I5, proteção de dados: a API só recebe JSON pequeno."""
    huge = "x" * (MAX_REQUEST_BODY_BYTES + 1)

    response = client.post(PUBLISH_URL, content=huge, headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert response.json()["detail"] == "Corpo da requisição grande demais"
    assert response.headers[REQUEST_ID_HEADER]


def test_a_normal_body_passes_the_size_check(client: TestClient) -> None:
    """O corpo real do W4 tem duas chaves: não pode esbarrar no teto."""
    response = client.post(PUBLISH_URL, json={"scenario": "heavy_rain"})

    assert response.status_code != 413


# --- O `X-Request-ID` do cliente é reaproveitado, mas não confiado (I5) --------------------------


@pytest.mark.parametrize(
    ("incoming", "reused"),
    [
        # O caso bom: um id simples do front é preservado.
        ("id-do-front", True),
        ("abc123", True),
        ("A_b.c-1", True),
        ("x" * 64, True),
        # Longo demais para um cabeçalho e para uma coluna indexada.
        ("x" * 65, False),
        # Tentativa de injetar cabeçalho.
        ("abc\r\nX-Injetado: sim", False),
        # Acento quebraria a montagem do cabeçalho (`UnicodeEncodeError`). Vai como bytes
        # porque o próprio httpx se recusa a **enviar** um cabeçalho `str` fora do ASCII.
        ("çççççççççç".encode("latin-1"), False),
        ("café".encode("latin-1"), False),
        # Vazio e espaços não identificam nada.
        ("", False),
        ("   ", False),
        ("id com espaco", False),
    ],
)
def test_a_client_request_id_is_only_reused_when_it_is_usable(
    client: TestClient, incoming: str | bytes, reused: bool
) -> None:
    """I5: o id do cliente vai para o cabeçalho, para o log e para o banco — filtre antes."""
    response = client.get("/api/v1/health", headers={REQUEST_ID_HEADER: incoming})

    assert response.status_code == 200
    returned = response.headers[REQUEST_ID_HEADER]
    sent = incoming.decode("latin-1") if isinstance(incoming, bytes) else incoming
    if reused:
        assert returned == sent
    else:
        assert returned != sent
        assert REQUEST_ID_PATTERN.match(returned)


def test_a_request_id_of_100k_characters_is_discarded_before_transport() -> None:
    """O caso extremo testa o filtro sem passar pelo limite de cabeçalho do Windows."""
    hostile_id = "x" * 100_000
    request = Request(
        {
            "type": "http",
            "headers": [(REQUEST_ID_HEADER.lower().encode(), hostile_id.encode())],
        }
    )

    request_id = _request_id_from(request)

    assert request_id != hostile_id
    assert REQUEST_ID_PATTERN.fullmatch(request_id)


def test_a_hostile_request_id_does_not_flood_the_log(
    client: TestClient, log_stream: io.StringIO
) -> None:
    """Um id de 100 mil caracteres não pode virar 98 KB de log numa requisição só."""
    client.get("/api/v1/health", headers={REQUEST_ID_HEADER: "x" * 100_000})

    assert len(log_stream.getvalue()) < 2_000

"""Log estruturado em JSON e `request_id` por requisição (I5).

O enunciado pede **registros de uso que permitam rastrear entradas, saídas e decisões**. O
`request_id` é o fio que costura tudo: ele vai no cabeçalho `X-Request-ID` da resposta, aparece em
toda linha de log daquela requisição e é gravado em `decision_log`. Com um id em mãos dá para
reconstruir o que entrou, o que a API respondeu e qual decisão saiu dali.

**O que nunca entra no log:** cabeçalhos, corpo da requisição e *query string*. É uma escolha, não
um esquecimento — é o que garante, por construção, que uma chave de API ou um dado pessoal não
vazem para o arquivo de log. O que se registra é método, caminho, status e duração.
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

REQUEST_ID_HEADER = "X-Request-ID"

# I5 — o `X-Request-ID` do cliente é **reaproveitado, mas não confiado**: ele vai para o cabeçalho
# da resposta, para toda linha de log e para a coluna indexada `decision_log.request_id`. Sem este
# filtro, um id com acento quebra a montagem do cabeçalho (`UnicodeEncodeError`), um id de 100 mil
# caracteres enche o log de uma requisição só, e um id com CRLF é tentativa de injetar cabeçalho.
# O que não passar no padrão é descartado em silêncio e substituído por um uuid novo.
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# I5 — teto do corpo de uma requisição. A API só recebe JSON pequeno (o maior é o POST de
# publicação, com duas chaves); qualquer coisa muito maior é engano ou abuso.
MAX_REQUEST_BODY_BYTES = 64 * 1024
PAYLOAD_TOO_LARGE_MESSAGE = "Corpo da requisição grande demais"

# Campos que o `logging` já põe em todo registro e que não devem virar "extras" no JSON.
_STANDARD_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}

# O id da requisição atual, visível para qualquer log emitido durante ela.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    """Formata cada registro como uma linha JSON, com o `request_id` quando houver."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


# Bibliotecas que registram a **URL inteira** de cada chamada que fazem (com query string) e que,
# por isso, ficam em WARNING: a URL da Open-Meteo não é segredo, mas log de terceiro é exatamente
# por onde um parâmetro sensível vazaria sem ninguém decidir isso.
NOISY_LIBRARY_LOGGERS = ("httpx", "httpcore")


def configure_logging(level: int = logging.INFO) -> None:
    """Liga o formato JSON na raiz do `logging`. Chamado na criação do app."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in NOISY_LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Dá um `request_id` a cada requisição, devolve-o no cabeçalho e registra o acesso.

    Também recusa corpo grande demais (413) antes de qualquer processamento.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _request_id_from(request)
        token = request_id_var.set(request_id)
        logger = logging.getLogger("app.access")

        started_at = time.perf_counter()
        try:
            too_large = _reject_large_body(request)
            response = too_large if too_large is not None else await call_next(request)

            duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
            logger.info(
                "requisição atendida",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)


def _request_id_from(request: Request) -> str:
    """Id da requisição: o do cliente quando é utilizável, senão um novo.

    Reaproveitar o `X-Request-ID` do front deixa rastrear uma chamada de ponta a ponta sem cruzar
    horários — mas só vale para um id que caiba num cabeçalho e numa coluna de banco.
    """
    incoming = request.headers.get(REQUEST_ID_HEADER)
    if incoming and REQUEST_ID_PATTERN.match(incoming):
        return incoming
    return uuid.uuid4().hex


def _reject_large_body(request: Request) -> JSONResponse | None:
    """413 quando o `Content-Length` passa do teto (I5, proteção de dados)."""
    declared = request.headers.get("content-length")
    if declared is None or not declared.isdigit():
        return None
    if int(declared) <= MAX_REQUEST_BODY_BYTES:
        return None

    return JSONResponse(status_code=413, content={"detail": PAYLOAD_TOO_LARGE_MESSAGE})

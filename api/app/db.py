"""Banco local em SQLite: engine, criação das tabelas e sessão (ADR-009).

Sem Alembic: as tabelas nascem com `create_all`. O arquivo `.db` está no `.gitignore`.
"""

from collections.abc import Iterator

from sqlalchemy import Engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings

# Importar `app.models` registra as tabelas no metadata antes do `create_all`.
from app import models  # noqa: F401  isort:skip

_engine: Engine | None = None


# Um banco em memória vive enquanto a conexão existir; com `StaticPool` todas as sessões (e a
# thread do MQTT) compartilham a mesma conexão, em vez de cada uma achar um banco vazio.
IN_MEMORY_URLS = frozenset({"sqlite://", "sqlite:///:memory:"})

# Milissegundos que uma conexão espera por um banco ocupado antes de desistir. A ponte MQTT (I2)
# grava a cada 5 s enquanto o painel ao vivo (W5) lê; sem isso, um `database is locked` viraria
# telemetria perdida no meio da demo.
BUSY_TIMEOUT_MS = 5000


def get_engine() -> Engine:
    """Engine única do processo.

    `check_same_thread=False` porque a ponte MQTT (I2) roda em outra thread.
    """
    global _engine
    if _engine is None:
        url = get_settings().database_url
        extra = {"poolclass": StaticPool} if url in IN_MEMORY_URLS else {}
        _engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            **extra,
        )
        if url not in IN_MEMORY_URLS:
            _configure_sqlite_file(_engine)
    return _engine


def _configure_sqlite_file(engine: Engine) -> None:
    """Liga o WAL e o `busy_timeout` em cada conexão nova (só no banco em arquivo).

    Com o journal padrão, uma leitura longa do painel (W5) bloqueia a escrita da telemetria. No
    modo WAL leitor e escritor convivem, que é exatamente o caso desta API: uma thread gravando e
    as requisições lendo. Em banco de memória o WAL não se aplica.
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()


def reset_engine() -> None:
    """Descarta a engine do processo. Usado entre testes que trocam o `database_url`."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def create_db(engine: Engine | None = None) -> None:
    """Cria as tabelas que ainda não existem. Chamado no lifespan da API."""
    SQLModel.metadata.create_all(engine or get_engine())


def get_session() -> Iterator[Session]:
    """Dependência do FastAPI: uma sessão por requisição."""
    with Session(get_engine()) as session:
        yield session

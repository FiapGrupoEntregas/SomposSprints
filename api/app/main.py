"""Ponto de entrada da API do AgriShield.

Desenvolvimento: `uv run fastapi dev app/main.py` (ou `uvicorn app.main:app --reload` com pip).
Documentação interativa: http://localhost:8000/docs
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.api.v1.router import api_router
from app.clients.open_meteo import WeatherUnavailableError
from app.core.config import Settings, get_settings
from app.core.logging import RequestContextMiddleware, configure_logging
from app.db import create_db, get_engine
from app.mqtt.bridge import MqttBridge
from app.mqtt.handlers import persist_event, persist_status, persist_telemetry
from app.mqtt.publisher import run_publish_cycle, wait_for_connection
from app.repositories.audit import purge_old_decisions
from app.repositories.devices import purge_old_telemetry
from app.services.farms import load_farms

logger = logging.getLogger(__name__)

# Mensagem única para o usuário quando a Open-Meteo falha e não há cache (I1, pendência do W2).
WEATHER_UNAVAILABLE_MESSAGE = "Serviço de clima indisponível"


async def weather_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    """Converte `WeatherUnavailableError` em 503, para nenhuma rota precisar de try/except (W2)."""
    logger.warning("Open-Meteo indisponível em %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": WEATHER_UNAVAILABLE_MESSAGE},
    )


async def publish_limits_periodically(bridge: MqttBridge, settings: Settings) -> None:
    """Republica o limite do dia de hora em hora, para todo equipamento conhecido (W4).

    O ciclo inteiro (Open-Meteo + banco + broker) vai para uma *thread*, então o `event loop`
    continua livre para atender as requisições.

    **O laço não pode morrer.** Uma exceção de qualquer origem — inclusive de fora do `try` por
    equipamento, como abrir a sessão ou ler `device_status` — encerraria a tarefa em silêncio: a
    API seguiria de pé e ninguém notaria que o limite parou de ser republicado. Por isso tudo é
    capturado e registrado, e só o `CancelledError` passa, que é o pedido de encerrar.
    """
    await wait_for_connection(bridge)

    while True:
        try:
            await asyncio.to_thread(run_publish_cycle, bridge, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Falha no ciclo de publicação periódica do limite; "
                "tentando de novo no próximo ciclo."
            )
        await asyncio.sleep(settings.mqtt_publish_interval_s)


def _warm_up_model() -> None:
    """Aquece o modelo preditivo no boot (W13/D3).

    A primeira previsão custa ~50 ms porque o `scikit-learn` monta caches na estreia; fazer isso
    aqui tira esse custo da primeira requisição de verdade. Sem artefato, apenas informa e segue:
    a API tem de subir só com as regras.
    """
    try:
        from app.services.model import warm_up
    except Exception:  # noqa: BLE001 — dependência do modelo ausente não derruba a API
        logger.warning("Serviço de modelo indisponível; a API segue só com o score por regras.")
        return

    if warm_up():
        logger.info("Modelo preditivo carregado e aquecido.")
    else:
        logger.info("Sem artefato de modelo; a API segue só com o score por regras.")


def build_bridge(settings: Settings) -> MqttBridge:
    """Ponte MQTT já ligada aos gravadores do banco (I2 + I3)."""
    return MqttBridge(
        settings,
        on_telemetry=persist_telemetry,
        on_event=persist_event,
        on_status=persist_status,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepara o banco (I3) e a ponte MQTT (I2) no boot, e encerra a ponte no shutdown.

    Nada aqui pode impedir a API de subir: o broker fora do ar vira aviso no log, e o `paho`
    reconecta sozinho em segundo plano.
    """
    settings = get_settings()

    create_db()
    _warm_up_model()
    with Session(get_engine()) as session:
        purge_old_telemetry(session)
        purge_old_decisions(session, days=settings.decision_retention_days)

    bridge = build_bridge(settings)
    app.state.mqtt = bridge
    publisher: asyncio.Task[None] | None = None
    if settings.mqtt_enabled:
        bridge.start()
        publisher = asyncio.create_task(publish_limits_periodically(bridge, settings))
    else:
        logger.info("Ponte MQTT desligada (AGRISHIELD_MQTT_ENABLED=false).")

    try:
        yield
    finally:
        if publisher is not None:
            # O `cancel()` interrompe a espera imediatamente. Se um ciclo estiver em andamento, a
            # thread dele termina por conta própria — por isso o cliente e a sessão vivem **dentro**
            # de `run_publish_cycle`, e não aqui: nada que a thread use morre junto com a tarefa.
            publisher.cancel()
            with suppress(asyncio.CancelledError):
                await publisher
        if settings.mqtt_enabled:
            bridge.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    # Log estruturado antes de qualquer coisa, para o próprio boot já sair em JSON (I5).
    configure_logging()
    # Valida o catálogo de fazendas (W1) no boot: JSON inválido derruba a API aqui, com o
    # campo errado na mensagem, em vez de falhar na primeira requisição da demo.
    load_farms()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Motor de risco relevo × clima e ponte MQTT com o equipamento.",
        lifespan=lifespan,
    )
    # O middleware do `request_id` fica por fora de tudo: até um 401 ou um 413 sai com o
    # cabeçalho `X-Request-ID` e vira uma linha de log (I5).
    app.add_middleware(RequestContextMiddleware)
    app.include_router(api_router, prefix="/api/v1")
    app.add_exception_handler(WeatherUnavailableError, weather_unavailable_handler)
    return app


app = create_app()

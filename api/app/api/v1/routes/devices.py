"""Equipamentos: limite do dia (W4) e painel ao vivo (W5).

A rota só traduz HTTP. O cálculo do limite está em `app/services/limits.py`, a publicação no
broker em `app/mqtt/publisher.py`, a leitura do banco em `app/repositories/devices.py` e o estado
online/offline em `app/services/devices.py`.

Falha da Open-Meteo vira 503 no tratador global de `app/main.py`.
"""

import json
import logging
from datetime import date as date_type
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlmodel import Session

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.core.clock import now_local, today_local
from app.core.logging import request_id_var
from app.core.security import require_api_key, require_read_access
from app.db import get_session
from app.models import DeviceEvent
from app.mqtt.bridge import MQTT_UNAVAILABLE_MESSAGE, MqttBridge, get_mqtt
from app.mqtt.publisher import LimitPublishError, day_risk_for, publish_limit
from app.repositories import devices as devices_repository
from app.schemas.audit import DecisionSource
from app.schemas.device import (
    DeviceEventResponse,
    DeviceLimit,
    DeviceLimitPublishRequest,
    DeviceStatusResponse,
    PublishedDeviceLimit,
    TelemetryPoint,
    TelemetrySeries,
)
from app.schemas.farm import Device, Farm
from app.schemas.history import DeviceHistory
from app.schemas.mqtt import EventMessage
from app.schemas.risk import Scenario
from app.services import devices as devices_service
from app.services import farms as farms_service
from app.services import history as history_service
from app.services.limits import compute_device_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])

DEVICE_NOT_FOUND_MESSAGE = "Equipamento não encontrado"
NO_TELEMETRY_MESSAGE = "Sem telemetria ainda"

# Feature W5 — janela padrão do gráfico e o teto que a rota aceita
DEFAULT_WINDOW_MINUTES = 10
MAX_WINDOW_MINUTES = 24 * 60

# Feature W5 — quantos eventos a lista traz por padrão
DEFAULT_EVENT_LIMIT = 20
MAX_EVENT_LIMIT = 200

DeviceIdPath = Annotated[str, Path(description="Identificador do equipamento (ex.: tractor-01).")]

LimitDateQuery = Annotated[
    date_type | None,
    Query(description="Dia do limite (AAAA-MM-DD). Sem ele, hoje no fuso America/Sao_Paulo."),
]

ScenarioQuery = Annotated[
    Scenario | None,
    Query(description="Cenário simulado (regras-de-risco §10), para a demo do dia chuvoso."),
]

MinutesQuery = Annotated[
    int,
    Query(ge=1, le=MAX_WINDOW_MINUTES, description="Tamanho da janela do gráfico, em minutos."),
]

HistoryDaysQuery = Annotated[
    int,
    Query(
        ge=1,
        le=history_service.MAX_HISTORY_DAYS,
        description="Tamanho da janela do histórico, em dias.",
    ),
]

EventLimitQuery = Annotated[
    int, Query(ge=1, le=MAX_EVENT_LIMIT, description="Quantos eventos devolver, no máximo.")
]

SessionDep = Annotated[Session, Depends(get_session)]
OpenMeteoDep = Annotated[OpenMeteoClient, Depends(get_open_meteo_client)]
MqttDep = Annotated[MqttBridge, Depends(get_mqtt)]


# --- W4: limite do dia -------------------------------------------------------------------------


@router.get(
    "/{device_id}/limit",
    response_model=DeviceLimit,
    dependencies=[Depends(require_read_access)],
)
def get_device_limit(
    device_id: DeviceIdPath,
    client: OpenMeteoDep,
    date: LimitDateQuery = None,
    scenario: ScenarioQuery = None,
) -> DeviceLimit:
    """Limite de inclinação do dia daquele equipamento (W4, regras-de-risco §4)."""
    farm, device = _require_device(device_id)
    return _build_limit(farm, device, client, date, scenario)


@router.post(
    "/{device_id}/limit/publish",
    response_model=PublishedDeviceLimit,
    # A chave é dependência **do decorador**, então o FastAPI a resolve antes de qualquer
    # parâmetro da função: sem chave, a requisição para aqui, sem tocar broker nem Open-Meteo.
    dependencies=[Depends(require_api_key)],
)
def publish_device_limit(
    device_id: DeviceIdPath,
    client: OpenMeteoDep,
    mqtt: MqttDep,
    session: SessionDep,
    body: DeviceLimitPublishRequest | None = None,
) -> PublishedDeviceLimit:
    """Publica o `config` do equipamento no broker, com QoS 1 e retained (W4).

    **Exige `X-API-Key`** (I5, ADR-013): é o único endpoint que muda o estado do equipamento no
    mundo físico. Devolve exatamente o que foi enviado. **503** se a ponte MQTT estiver
    desconectada, para o front poder dizer "equipamento sem conexão" em vez de fingir que enviou.
    """
    farm, device = _require_device(device_id)

    # A conexão é checada **antes** de calcular: sem broker, não adianta ir à Open-Meteo. Assim a
    # resposta é imediata e diz a verdade ("sem conexão MQTT") mesmo que o clima também esteja fora.
    if not mqtt.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=MQTT_UNAVAILABLE_MESSAGE
        )

    request = body if body is not None else DeviceLimitPublishRequest()
    limit = _build_limit(farm, device, client, request.date, request.scenario)

    try:
        return publish_limit(
            mqtt,
            session,
            farm,
            device,
            limit,
            source=DecisionSource.API,
            request_id=request_id_var.get(),
        )
    except LimitPublishError as error:
        logger.warning("Publicação recusada pelo broker: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=MQTT_UNAVAILABLE_MESSAGE
        ) from error


# --- W5: painel ao vivo ------------------------------------------------------------------------


@router.get(
    "/{device_id}/status",
    response_model=DeviceStatusResponse,
    dependencies=[Depends(require_read_access)],
)
def get_device_status(device_id: DeviceIdPath, session: SessionDep) -> DeviceStatusResponse:
    """Está online? (W5) `offline` pelo LWT **ou** por passar de 20 s sem telemetria."""
    _require_device(device_id)

    status_row = devices_repository.latest_status(session, device_id)
    latest = devices_repository.latest_telemetry(session, device_id)
    state, last_seen_at, silence_s = devices_service.resolve_state(
        status_row, latest, devices_repository.utc_naive(now_local())
    )

    return DeviceStatusResponse(
        device_id=device_id,
        state=state,
        reported_state=devices_service.reported_state(status_row),
        last_seen_at=last_seen_at,
        seconds_since_last_telemetry=round(silence_s, 1) if silence_s is not None else None,
    )


@router.get(
    "/{device_id}/telemetry/latest",
    response_model=TelemetryPoint,
    dependencies=[Depends(require_read_access)],
)
def get_latest_telemetry(device_id: DeviceIdPath, session: SessionDep) -> TelemetryPoint:
    """Última leitura do equipamento (W5). **404** enquanto ele não publicar nada."""
    _require_device(device_id)

    latest = devices_repository.latest_telemetry(session, device_id)
    if latest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_TELEMETRY_MESSAGE)
    return TelemetryPoint.model_validate(latest, from_attributes=True)


@router.get(
    "/{device_id}/telemetry",
    response_model=TelemetrySeries,
    dependencies=[Depends(require_read_access)],
)
def get_telemetry_series(
    device_id: DeviceIdPath,
    session: SessionDep,
    minutes: MinutesQuery = DEFAULT_WINDOW_MINUTES,
) -> TelemetrySeries:
    """Série dos últimos minutos, em ordem cronológica e com no máximo 300 pontos (W5)."""
    _require_device(device_id)

    rows = devices_repository.telemetry_since(session, device_id, minutes)
    points = devices_service.downsample(rows)

    return TelemetrySeries(
        device_id=device_id,
        minutes=minutes,
        total=len(rows),
        sampled=len(points) < len(rows),
        points=[TelemetryPoint.model_validate(row, from_attributes=True) for row in points],
    )


@router.get(
    "/{device_id}/history",
    response_model=DeviceHistory,
    dependencies=[Depends(require_read_access)],
)
def get_device_history(
    device_id: DeviceIdPath,
    session: SessionDep,
    days: HistoryDaysQuery = history_service.DEFAULT_HISTORY_DAYS,
) -> DeviceHistory:
    """Histórico do equipamento — a prévia do Passaporte Digital (W11).

    Acumulado do período e linha do tempo, sobre o que a I3 já guarda. Sem dados, os totais vêm
    zerados e a linha do tempo vazia: é um período sem registro, não um erro.
    """
    farm, device = _require_device(device_id)
    history, events = history_service.device_history(session, device.device_id, farm.id, days=days)
    history.timeline = [_to_event_response(event) for event in events]
    return history


@router.get(
    "/{device_id}/events",
    response_model=list[DeviceEventResponse],
    dependencies=[Depends(require_read_access)],
)
def get_device_events(
    device_id: DeviceIdPath,
    session: SessionDep,
    limit: EventLimitQuery = DEFAULT_EVENT_LIMIT,
) -> list[DeviceEventResponse]:
    """Eventos do equipamento, do mais recente para o mais antigo, com o `context` quando houver."""
    _require_device(device_id)

    rows = devices_repository.list_events(session, device_id, limit=limit)
    return [_to_event_response(row) for row in rows]


# --- internos ------------------------------------------------------------------------------------


def _require_device(device_id: str) -> tuple[Farm, Device]:
    """Busca o equipamento no catálogo (W1) ou devolve 404."""
    found = farms_service.find_device(device_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DEVICE_NOT_FOUND_MESSAGE)
    return found


def _build_limit(
    farm: Farm,
    device: Device,
    client: OpenMeteoClient,
    target_date: date_type | None,
    scenario: Scenario | None,
) -> DeviceLimit:
    """Calcula o limite do dia pedido. **404** se a data estiver fora da janela da previsão."""
    day_date = target_date if target_date is not None else today_local()
    day = day_risk_for(farm, client, day_date, scenario)
    if day is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Sem previsão para {day_date.isoformat()}. "
                "A previsão cobre de hoje até 6 dias à frente."
            ),
        )
    return compute_device_limit(device, farm, day, scenario=scenario)


def _to_event_response(row: DeviceEvent) -> DeviceEventResponse:
    """Monta a resposta juntando a linha do banco com o payload original do evento."""
    message = EventMessage.model_validate_json(row.payload_json)
    return DeviceEventResponse(
        event_id=row.event_id,
        device_id=row.device_id,
        type=message.type,
        ts=row.ts,
        received_at=row.received_at,
        roll_deg=message.roll_deg,
        pitch_deg=message.pitch_deg,
        accel_g=message.accel_g,
        tilt_limit_deg=message.tilt_limit_deg,
        context=json.loads(message.context.model_dump_json()) if message.context else None,
    )

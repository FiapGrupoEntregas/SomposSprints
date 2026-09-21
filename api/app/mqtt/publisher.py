"""Publicação do limite do dia para os equipamentos (W4).

Junta as peças que já existiam: previsão de risco (W3) → limite do equipamento
(`app/services/limits.py`) → `config` retained no broker (I2) → registro em `published_config` (I3).

É usado em dois lugares:

- pela rota `POST /devices/{device_id}/limit/publish` (o botão "Enviar ao equipamento" do front);
- pela **tarefa periódica** do `lifespan`, que republica de hora em hora.

## Para quem a tarefa publica

Só para os equipamentos que **já se anunciaram** (têm linha em `device_status`). O broker é
público e `config` é retained: publicar para o catálogo inteiro deixaria mensagem órfã pendurada
lá para sempre em `harvester-01` e `tractor-02`, que não têm firmware. O equipamento que nunca
falou recebe o limite assim que alguém apertar "Enviar ao equipamento" ou no ciclo seguinte —
e, como `device_status` sobrevive ao reinício, depois da primeira conexão ele entra sozinho na
lista. *(Decisão da W4, registrada na spec.)*
"""

import asyncio
import logging
from datetime import date, datetime

from sqlmodel import Session, select

from app.clients.open_meteo import OpenMeteoClient, WeatherUnavailableError, get_open_meteo_client
from app.core.clock import today_local
from app.core.config import Settings
from app.db import get_engine
from app.models import DeviceStatus
from app.mqtt.bridge import CONFIG_SUFFIX, MqttBridge
from app.repositories import audit as audit_repository
from app.repositories.devices import save_published_config
from app.schemas.audit import DecisionSource, DecisionType
from app.schemas.device import DeviceLimit, PublishedDeviceLimit
from app.schemas.farm import Device, Farm
from app.schemas.risk import DayRisk, Scenario
from app.services import farms as farms_service
from app.services import risk as risk_service
from app.services.limits import compute_device_limit, to_config_message

logger = logging.getLogger(__name__)

# Feature W4 — quanto a tarefa espera pela conexão com o broker antes do primeiro envio
CONNECT_WAIT_S = 15.0
CONNECT_POLL_S = 0.5


class LimitPublishError(RuntimeError):
    """O broker não aceitou a publicação (a rota transforma isso em 503)."""


def day_risk_for(
    farm: Farm,
    client: OpenMeteoClient,
    target_date: date,
    scenario: Scenario | None = None,
) -> DayRisk | None:
    """O dia da previsão de risco (W3) que corresponde à data pedida, ou `None` se não houver.

    Só existe de hoje até hoje + 6: datas passadas são assunto do replay (W9).
    """
    forecast = risk_service.get_risk_forecast(
        farm, client, days=risk_service.MAX_FORECAST_DAYS, scenario=scenario
    )
    for day in forecast.days:
        if day.date == target_date:
            return day
    return None


def publish_limit(
    bridge: MqttBridge,
    session: Session,
    farm: Farm,
    device: Device,
    limit: DeviceLimit,
    published_at: datetime | None = None,
    source: DecisionSource = DecisionSource.API,
    request_id: str | None = None,
) -> PublishedDeviceLimit:
    """Publica o `config` (QoS 1, retained), registra o envio e a decisão na trilha (I3 + I5).

    Levanta `LimitPublishError` quando o broker recusa a publicação — e, nesse caso, **nada é
    gravado**: a trilha só registra limite que saiu de verdade.
    """
    config = to_config_message(limit)
    if not bridge.publish_config(device.device_id, config):
        raise LimitPublishError(f"O broker não aceitou o config de '{device.device_id}'.")

    row = save_published_config(session, device.device_id, config, published_at=published_at)
    audit_repository.record_decision(
        session,
        decision_type=DecisionType.TILT_LIMIT,
        entity_id=device.device_id,
        inputs={
            "farm_id": farm.id,
            "date": limit.date.isoformat(),
            "scenario": limit.scenario.value if limit.scenario is not None else None,
            "soil_state": limit.soil_state.value,
            "rain_72h_mm": limit.rain_72h_mm,
            "reference_tilt_limit_deg": limit.reference_tilt_limit_deg,
        },
        output=config.model_dump(exclude_none=True),
        source=source,
        request_id=request_id,
        created_at=row.published_at,
    )
    return PublishedDeviceLimit(
        limit=limit,
        topic=bridge.topic_for(device.device_id, CONFIG_SUFFIX),
        published_at=row.published_at,
        payload=config.model_dump(exclude_none=True),
    )


def known_device_ids(session: Session) -> set[str]:
    """Equipamentos que já se anunciaram alguma vez (têm `status` gravado)."""
    return {row.device_id for row in session.exec(select(DeviceStatus))}


def publish_limits_for_known_devices(
    bridge: MqttBridge,
    client: OpenMeteoClient,
    session: Session,
    today: date | None = None,
) -> list[str]:
    """Publica o limite de hoje para cada equipamento conhecido. Devolve os `device_id` enviados.

    **Falha de um equipamento não contamina os outros**: cada um tem o próprio `try`, então nem a
    Open-Meteo fora do ar nem um broker instável interrompem a varredura.

    O que está **fora** desses `try` (ler `device_status`, por exemplo) ainda pode levantar — um
    banco travado, digamos. Quem garante que isso não mata a tarefa de segundo plano é o laço de
    `app/main.py::publish_limits_periodically`, que captura tudo menos o `CancelledError`.
    """
    if not bridge.is_connected:
        logger.warning("Ponte MQTT desconectada; a publicação periódica do limite foi adiada.")
        return []

    target_date = today if today is not None else today_local()
    device_ids = known_device_ids(session)
    if not device_ids:
        logger.info(
            "Nenhum equipamento se anunciou ainda; nada a publicar. O primeiro envio sai pelo "
            "botão 'Enviar ao equipamento' ou no ciclo seguinte à conexão do ESP32."
        )
        return []

    published: list[str] = []
    for device_id in sorted(device_ids):
        found = farms_service.find_device(device_id)
        if found is None:
            logger.warning(
                "O equipamento '%s' publicou status mas não está no catálogo; ignorado.", device_id
            )
            continue

        farm, device = found
        try:
            day = day_risk_for(farm, client, target_date)
            if day is None:
                logger.warning("Sem previsão para %s na fazenda '%s'.", target_date, farm.id)
                continue
            limit = compute_device_limit(device, farm, day)
            publish_limit(bridge, session, farm, device, limit, source=DecisionSource.SCHEDULER)
        except (WeatherUnavailableError, LimitPublishError) as error:
            logger.warning("Não foi possível publicar o limite de '%s': %s", device_id, error)
            continue
        except Exception:  # noqa: BLE001 — a tarefa periódica nunca pode morrer
            logger.exception("Erro inesperado ao publicar o limite de '%s'.", device_id)
            continue

        published.append(device_id)

    if published:
        logger.info("Limite do dia publicado para: %s", ", ".join(published))
    return published


def run_publish_cycle(bridge: MqttBridge, settings: Settings) -> list[str]:
    """Um ciclo completo de publicação, **inteiro dentro da thread que chama**.

    O cliente da Open-Meteo e a sessão do banco nascem e morrem aqui, no mesmo padrão de
    `app/mqtt/handlers.py`: se a sessão fosse aberta no `event loop` e só o trabalho fosse para a
    thread, um `cancel()` no shutdown fecharia a sessão enquanto a thread ainda a usa.
    """
    client = get_open_meteo_client(settings)
    with Session(get_engine()) as session:
        return publish_limits_for_known_devices(bridge, client, session)


async def wait_for_connection(bridge: MqttBridge, timeout_s: float = CONNECT_WAIT_S) -> bool:
    """Espera a ponte conectar, sem bloquear o *event loop*. Devolve se conseguiu."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not bridge.is_connected and loop.time() < deadline:
        await asyncio.sleep(CONNECT_POLL_S)
    return bridge.is_connected

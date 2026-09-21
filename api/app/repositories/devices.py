"""Leitura e escrita da telemetria, dos eventos, do status e dos configs publicados (I3).

Toda função recebe a `Session` de quem chama: a thread do paho (I2) abre a **própria sessão** por
mensagem e as rotas usam a dependência `get_session()`. Nada aqui decide regra de negócio — só
traduz a mensagem MQTT validada (`app/schemas/mqtt.py`) em linha do SQLite.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, delete, select

from app.models import DeviceEvent, DeviceStatus, PublishedConfig, Telemetry
from app.schemas.mqtt import ConfigMessage, EventMessage, StatusMessage, TelemetryMessage

logger = logging.getLogger(__name__)

# Feature I3 — a telemetria com mais de 7 dias é apagada quando a API sobe
TELEMETRY_RETENTION_DAYS = 7

# contrato-mqtt — `ts: 0` significa "o NTP ainda não sincronizou"
UNSYNCED_TIMESTAMP = 0


def utc_naive(moment: datetime) -> datetime:
    """Converte para **UTC sem fuso**, que é como o SQLite guarda data e hora.

    O SQLite não tem tipo com fuso: um `datetime` ciente entra e volta ingênuo. Normalizar na
    escrita e nos filtros deixa o que sai igual ao que entrou e evita comparar horário local com
    UTC sem perceber. Um `datetime` já ingênuo é considerado UTC.
    """
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _now() -> datetime:
    """Agora em UTC, no formato que vai para o banco (sem fuso)."""
    return utc_naive(datetime.now(UTC))


def resolve_timestamp(ts: int, received_at: datetime) -> datetime:
    """Hora do evento: a do dispositivo, ou a de recepção quando `ts` é `0` (contrato-mqtt).

    Sem isso, um equipamento que ainda não sincronizou o NTP gravaria tudo em 01/01/1970 e sumiria
    de qualquer consulta por janela de tempo. O resultado sai em UTC sem fuso, pronto para o banco.
    """
    if ts <= UNSYNCED_TIMESTAMP:
        return utc_naive(received_at)
    return utc_naive(datetime.fromtimestamp(ts, tz=UTC))


def save_telemetry(
    session: Session, message: TelemetryMessage, received_at: datetime | None = None
) -> Telemetry:
    """Grava uma leitura de telemetria."""
    moment = utc_naive(received_at) if received_at is not None else _now()
    row = Telemetry(
        device_id=message.device_id,
        ts=resolve_timestamp(message.ts, moment),
        received_at=moment,
        seq=message.seq,
        roll_deg=message.roll_deg,
        pitch_deg=message.pitch_deg,
        accel_g=message.accel_g,
        temp_c=message.temp_c,
        humidity_pct=message.humidity_pct,
        fire_conditions=message.fire_conditions,
        tilt_limit_deg=message.tilt_limit_deg,
        alert_level=message.alert_level.value,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def save_event(
    session: Session, message: EventMessage, received_at: datetime | None = None
) -> DeviceEvent | None:
    """Grava um evento e devolve `None` se o `event_id` já existia.

    O ESP32 publica cada evento 3 vezes com o mesmo `event_id` (contrato-mqtt). A deduplicação da
    ponte (I2) pega as repetições dentro da mesma execução; a `UNIQUE` desta tabela é a segunda
    trava, que continua valendo depois de um reinício da API.
    """
    moment = utc_naive(received_at) if received_at is not None else _now()
    row = DeviceEvent(
        event_id=message.event_id,
        device_id=message.device_id,
        type=message.type.value,
        ts=resolve_timestamp(message.ts, moment),
        received_at=moment,
        payload_json=message.model_dump_json(),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        logger.info("Evento '%s' já estava gravado; repetição ignorada.", message.event_id)
        return None

    session.refresh(row)
    return row


def set_status(
    session: Session, message: StatusMessage, updated_at: datetime | None = None
) -> DeviceStatus:
    """Guarda o último estado de conexão do equipamento (uma linha por `device_id`)."""
    moment = utc_naive(updated_at) if updated_at is not None else _now()
    row = session.get(DeviceStatus, message.device_id)
    if row is None:
        row = DeviceStatus(
            device_id=message.device_id, state=message.state.value, updated_at=moment
        )
    else:
        row.state = message.state.value
        row.updated_at = moment

    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def save_published_config(
    session: Session, device_id: str, config: ConfigMessage, published_at: datetime | None = None
) -> PublishedConfig:
    """Registra o `config` que a API publicou para o equipamento (auditoria, I5)."""
    row = PublishedConfig(
        device_id=device_id,
        published_at=utc_naive(published_at) if published_at is not None else _now(),
        payload_json=config.model_dump_json(exclude_none=True),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def latest_telemetry(session: Session, device_id: str) -> Telemetry | None:
    """Leitura mais recente do equipamento, ou `None` se ele nunca publicou."""
    statement = (
        select(Telemetry)
        .where(Telemetry.device_id == device_id)
        .order_by(col(Telemetry.received_at).desc(), col(Telemetry.id).desc())
        .limit(1)
    )
    return session.exec(statement).first()


def telemetry_since(
    session: Session, device_id: str, minutes: int, now: datetime | None = None
) -> list[Telemetry]:
    """Telemetria dos últimos `minutes` minutos, em **ordem cronológica** (do mais antigo)."""
    cutoff = (utc_naive(now) if now is not None else _now()) - timedelta(minutes=minutes)
    statement = (
        select(Telemetry)
        .where(Telemetry.device_id == device_id, col(Telemetry.received_at) >= cutoff)
        .order_by(col(Telemetry.received_at), col(Telemetry.id))
    )
    return list(session.exec(statement))


def list_events(session: Session, device_id: str, limit: int = 50) -> list[DeviceEvent]:
    """Eventos do equipamento, do mais recente para o mais antigo."""
    statement = (
        select(DeviceEvent)
        .where(DeviceEvent.device_id == device_id)
        .order_by(col(DeviceEvent.received_at).desc(), col(DeviceEvent.id).desc())
        .limit(limit)
    )
    return list(session.exec(statement))


def latest_status(session: Session, device_id: str) -> DeviceStatus | None:
    """Último estado de conexão conhecido, ou `None` se o equipamento nunca se anunciou."""
    return session.get(DeviceStatus, device_id)


def purge_old_telemetry(
    session: Session, days: int = TELEMETRY_RETENTION_DAYS, now: datetime | None = None
) -> int:
    """Apaga a telemetria com mais de `days` dias e devolve quantas linhas saíram (I3).

    Só a telemetria, que é volumosa (uma linha a cada 5 s por equipamento). Eventos, status e
    configs publicados ficam: são poucos e são a memória do sinistro.
    """
    cutoff = (utc_naive(now) if now is not None else _now()) - timedelta(days=days)
    result = session.exec(delete(Telemetry).where(col(Telemetry.received_at) < cutoff))  # type: ignore[call-overload]
    session.commit()
    removed = int(result.rowcount or 0)
    if removed:
        logger.info(
            "Retenção: %d leituras de telemetria com mais de %d dias apagadas.", removed, days
        )
    return removed

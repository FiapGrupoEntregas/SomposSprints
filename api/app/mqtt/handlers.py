"""Ligação entre a ponte MQTT (I2) e o banco (I3).

São os callbacks que a ponte chama quando uma mensagem passa na validação. Cada um abre a
**própria sessão**, porque eles rodam na thread do paho, e **nunca deixa uma exceção escapar**:
um erro de banco não pode derrubar a ponte e deixar o painel ao vivo mudo.
"""

import logging
from collections.abc import Callable

from sqlmodel import Session

from app.db import get_engine
from app.repositories import audit as audit_repository
from app.repositories import devices as devices_repository
from app.schemas.audit import DecisionSource, DecisionType
from app.schemas.mqtt import EventMessage, EventType, StatusMessage, TelemetryMessage

logger = logging.getLogger(__name__)

# I5 — eventos que **são** um alerta e, por isso, viram linha em `decision_log`.
# `limit_applied` é confirmação de recebimento e `incident_report` é registro do operador:
# nenhum dos dois é uma decisão de risco.
ALERT_EVENT_TYPES = frozenset({EventType.TILT_ALERT, EventType.ROLLOVER})


def persist_telemetry(message: TelemetryMessage) -> None:
    """Grava a telemetria recebida (I3)."""
    _in_session(
        "telemetria",
        message.device_id,
        lambda session: devices_repository.save_telemetry(session, message),
    )


def persist_event(message: EventMessage, raw: bytes) -> None:
    """Grava o evento, o **hash do payload cru** (I5) e, se for um alerta, a decisão (I5).

    `raw` são os bytes exatos que chegaram do broker: o hash é calculado sobre eles, antes de
    qualquer interpretação, que é o que permite provar depois que o evento guardado é o que o
    equipamento publicou.
    """
    _in_session("evento", message.device_id, lambda session: _save_event(session, message, raw))


def persist_status(message: StatusMessage) -> None:
    """Atualiza o estado de conexão do equipamento (I3)."""
    _in_session(
        "status",
        message.device_id,
        lambda session: devices_repository.set_status(session, message),
    )


def _save_event(session: Session, message: EventMessage, raw: bytes) -> None:
    """Evento + integridade + trilha, na mesma sessão."""
    saved = devices_repository.save_event(session, message)
    if saved is None:
        # Repetição que a ponte não pegou (a API reiniciou entre as 3 cópias): nada a acrescentar.
        return

    audit_repository.record_event_integrity(session, message.event_id, message.device_id, raw)

    if message.type in ALERT_EVENT_TYPES:
        audit_repository.record_decision(
            session,
            decision_type=DecisionType.ALERT,
            entity_id=message.device_id,
            inputs={
                "event_type": message.type.value,
                "roll_deg": message.roll_deg,
                "pitch_deg": message.pitch_deg,
                "accel_g": message.accel_g,
                "tilt_limit_deg": message.tilt_limit_deg,
            },
            output={
                "event_id": message.event_id,
                "recorded": True,
                "payload_sha256": audit_repository.payload_sha256(raw),
            },
            source=DecisionSource.DEVICE,
        )


def _in_session(kind: str, device_id: str, operation: Callable[[Session], object]) -> None:
    """Roda a operação numa sessão nova e engole o erro, deixando o rastro no log."""
    try:
        with Session(get_engine()) as session:
            operation(session)
    except Exception:  # noqa: BLE001 — falha de banco não pode derrubar a thread do MQTT
        logger.exception("Falha ao gravar %s de '%s'; mensagem perdida.", kind, device_id)

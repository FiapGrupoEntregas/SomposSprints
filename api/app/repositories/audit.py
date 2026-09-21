"""Gravação e leitura da trilha de auditoria (I5).

Duas responsabilidades:

- `record_decision(...)` — **toda** decisão que vira alerta, limite ou score gera uma linha, com
  entrada, saída e as versões de regra e modelo que decidiram (`app/core/versions.py`);
- `record_event_integrity(...)` — o hash SHA-256 dos bytes crus de cada evento MQTT, para provar
  depois que o que está gravado é o que o equipamento publicou.

**Nada aqui grava chave de API, nome ou documento.** Quem chama monta `inputs`/`output` campo a
campo, com números e identificadores técnicos — nunca um dicionário de cabeçalhos ou um payload
inteiro vindo de fora.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, col, delete, select

from app.core.versions import MODEL_VERSION, RULES_VERSION
from app.models import DecisionLog, EventIntegrity
from app.repositories.devices import utc_naive
from app.schemas.audit import DecisionLogEntry, DecisionSource, DecisionType

logger = logging.getLogger(__name__)

# Feature I5 — quantos registros `GET /api/v1/audit` devolve por padrão e no máximo
DEFAULT_AUDIT_LIMIT = 50
MAX_AUDIT_LIMIT = 500

# Feature I5 — por quantos dias a trilha é mantida. `GET /farms/{id}/risk` é **aberto** e grava uma
# linha por chamada, então sem retenção a única escrita disparável sem chave cresceria para sempre.
# 30 dias cobre a sprint inteira e a apresentação, que é o horizonte deste MVP; o valor é
# configurável por `AGRISHIELD_DECISION_RETENTION_DAYS`.
DECISION_RETENTION_DAYS = 30


def record_decision(
    session: Session,
    decision_type: DecisionType,
    entity_id: str,
    inputs: dict[str, Any],
    output: dict[str, Any],
    source: DecisionSource,
    request_id: str | None = None,
    created_at: datetime | None = None,
    rule_version: str = RULES_VERSION,
    model_version: str | None = MODEL_VERSION,
) -> DecisionLog:
    """Registra uma decisão na trilha. Devolve a linha gravada."""
    row = DecisionLog(
        created_at=utc_naive(created_at if created_at is not None else datetime.now(UTC)),
        request_id=request_id,
        decision_type=decision_type.value,
        entity_id=entity_id,
        inputs_json=json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str),
        output_json=json.dumps(output, ensure_ascii=False, sort_keys=True, default=str),
        rule_version=rule_version,
        model_version=model_version,
        source=source.value,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_decisions(
    session: Session,
    entity_id: str | None = None,
    decision_type: DecisionType | None = None,
    limit: int = DEFAULT_AUDIT_LIMIT,
) -> list[DecisionLog]:
    """Trilha do mais recente para o mais antigo, opcionalmente filtrada."""
    statement = select(DecisionLog)
    if entity_id is not None:
        statement = statement.where(DecisionLog.entity_id == entity_id)
    if decision_type is not None:
        statement = statement.where(DecisionLog.decision_type == decision_type.value)

    statement = statement.order_by(
        col(DecisionLog.created_at).desc(), col(DecisionLog.id).desc()
    ).limit(limit)
    return list(session.exec(statement))


def to_entry(row: DecisionLog) -> DecisionLogEntry:
    """Converte a linha do banco no schema da resposta, abrindo os dois JSON."""
    return DecisionLogEntry(
        id=row.id or 0,
        created_at=row.created_at,
        request_id=row.request_id,
        decision_type=DecisionType(row.decision_type),
        entity_id=row.entity_id,
        inputs=json.loads(row.inputs_json),
        output=json.loads(row.output_json),
        rule_version=row.rule_version,
        model_version=row.model_version,
        source=DecisionSource(row.source),
    )


def payload_sha256(payload: bytes | str) -> str:
    """SHA-256 dos **bytes crus** do payload, em hexadecimal."""
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(raw).hexdigest()


def record_event_integrity(
    session: Session, event_id: str, device_id: str, payload: bytes | str
) -> EventIntegrity | None:
    """Guarda o hash do evento. Devolve `None` se o `event_id` já tinha hash gravado.

    O ESP32 publica cada evento 3 vezes (contrato-mqtt): o hash é o da **primeira** cópia, que é
    também a que virou linha em `device_event`.
    """
    existing = session.get(EventIntegrity, event_id)
    if existing is not None:
        return None

    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    row = EventIntegrity(
        event_id=event_id,
        device_id=device_id,
        payload_sha256=payload_sha256(raw),
        payload_bytes=len(raw),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def event_integrity(session: Session, event_id: str) -> EventIntegrity | None:
    """Hash guardado daquele evento, ou `None` se não houver."""
    return session.get(EventIntegrity, event_id)


def purge_old_decisions(
    session: Session, days: int = DECISION_RETENTION_DAYS, now: datetime | None = None
) -> int:
    """Apaga as decisões com mais de `days` dias e devolve quantas saíram (I5).

    Chamada no boot, junto de `purge_old_telemetry`. A trilha é a memória do que o sistema
    decidiu, então a janela é bem maior que a da telemetria — mas não é infinita, porque a rota de
    risco é aberta e grava a cada consulta.
    """
    cutoff = (utc_naive(now) if now is not None else utc_naive(datetime.now(UTC))) - timedelta(
        days=days
    )
    result = session.exec(delete(DecisionLog).where(col(DecisionLog.created_at) < cutoff))  # type: ignore[call-overload]
    session.commit()
    removed = int(result.rowcount or 0)
    if removed:
        logger.info("Retenção: %d decisões com mais de %d dias apagadas da trilha.", removed, days)
    return removed

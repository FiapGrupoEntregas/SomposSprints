"""Consulta da trilha de auditoria (I5).

Protegida por chave de API (`X-API-Key`, ADR-013): a trilha mostra o que a API decidiu e com quais
entradas, então não é informação para ficar aberta na internet junto com o resto da demo.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.security import require_api_key
from app.db import get_session
from app.repositories import audit as audit_repository
from app.schemas.audit import DecisionLogEntry, DecisionType

router = APIRouter(prefix="/audit", tags=["audit"])

SessionDep = Annotated[Session, Depends(get_session)]

EntityQuery = Annotated[
    str | None,
    Query(description="Filtra por fazenda (`farm_id`) ou equipamento (`device_id`)."),
]

DecisionTypeQuery = Annotated[DecisionType | None, Query(description="Filtra por tipo de decisão.")]

LimitQuery = Annotated[
    int,
    Query(
        ge=1,
        le=audit_repository.MAX_AUDIT_LIMIT,
        description="Quantos registros devolver, do mais recente para o mais antigo.",
    ),
]


@router.get(
    "",
    response_model=list[DecisionLogEntry],
    # Resolvida antes dos parâmetros: sem chave, nem o banco é consultado.
    dependencies=[Depends(require_api_key)],
)
def list_audit(
    session: SessionDep,
    entity: EntityQuery = None,
    decision_type: DecisionTypeQuery = None,
    limit: LimitQuery = audit_repository.DEFAULT_AUDIT_LIMIT,
) -> list[DecisionLogEntry]:
    """Decisões registradas, do mais recente para o mais antigo (I5).

    Cada linha traz o que entrou, o que saiu, o `request_id` da origem e as versões de regra e de
    modelo que decidiram.
    """
    rows = audit_repository.list_decisions(
        session, entity_id=entity, decision_type=decision_type, limit=limit
    )
    return [audit_repository.to_entry(row) for row in rows]

"""Schemas da trilha de auditoria (I5).

A trilha responde à exigência do enunciado de **rastrear entradas, saídas e decisões**: cada
registro diz o que entrou, o que saiu, quem decidiu e com qual versão de regra.
"""

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DecisionType(StrEnum):
    """Tipos de decisão registrados (I5)."""

    RISK_SCORE = "risk_score"
    TILT_LIMIT = "tilt_limit"
    # A recomendação (W6) é a mesma decisão do dia escrita em português. Ela tem tipo próprio
    # porque o `output` tem outro formato: quem filtra a trilha não deveria precisar ramificar
    # em `inputs.endpoint` para saber o que vai encontrar.
    RECOMMENDATION = "recommendation"
    ALERT = "alert"
    REPLAY = "replay"


class DecisionSource(StrEnum):
    """De onde a decisão partiu."""

    API = "api"
    SCHEDULER = "scheduler"
    DEVICE = "device"


class DecisionLogEntry(BaseModel):
    """Uma linha da trilha, como `GET /api/v1/audit` devolve."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(description="Identificador da linha.")
    created_at: dt.datetime = Field(description="Quando a decisão foi registrada (UTC).")
    request_id: str | None = Field(
        default=None,
        description="Requisição que originou a decisão; nulo quando veio do MQTT ou da tarefa.",
    )
    decision_type: DecisionType = Field(description="Tipo da decisão.")
    entity_id: str = Field(description="Fazenda ou equipamento a que a decisão se refere.")
    inputs: dict = Field(description="O que entrou na decisão.")
    output: dict = Field(description="O que saiu dela.")
    rule_version: str = Field(description="Versão das regras (document/regras-de-risco.md).")
    model_version: str | None = Field(
        default=None, description="Versão do modelo preditivo, ou nulo enquanto não houver (D3)."
    )
    source: DecisionSource = Field(description="`api`, `scheduler` ou `device`.")

"""Schema do histórico do equipamento — a prévia do Passaporte Digital (W11).

A ideia que deu origem ao projeto: **cada alerta seguido ou ignorado vira histórico da máquina**,
útil na renovação do seguro, no sinistro e na revenda. Aqui é só a semente — um resumo do período
e a linha do tempo dos eventos, sobre o que a I3 já guarda. Score comportamental, portabilidade e
assinatura digital ficam no roadmap, e a tela diz isso.
"""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.device import DeviceEventResponse


class DeviceHistory(BaseModel):
    """Resumo do período e linha do tempo de um equipamento (W11)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(description="Identificador do equipamento.")
    farm_id: str = Field(description="Fazenda a que ele pertence.")
    days: int = Field(gt=0, description="Tamanho da janela, em dias.")

    first_seen_at: dt.datetime | None = Field(
        default=None, description="Primeira leitura do período (UTC), ou nulo."
    )
    last_seen_at: dt.datetime | None = Field(
        default=None, description="Última leitura do período (UTC), ou nulo."
    )

    readings: int = Field(ge=0, description="Leituras de telemetria no período.")
    operating_hours: float = Field(
        ge=0.0, description="Horas operando, **estimadas** pela contagem de leituras (E4)."
    )
    max_roll_deg: float | None = Field(default=None, description="Maior rolagem absoluta.")
    max_pitch_deg: float | None = Field(default=None, description="Maior arfagem absoluta.")
    pct_time_above_limit: float = Field(
        ge=0.0, le=100.0, description="% das leituras em alerta 🔴 ou capotamento."
    )

    alerts: int = Field(ge=0, description="Alertas de inclinação (`tilt_alert`).")
    rollovers: int = Field(ge=0, description="Capotamentos detectados (`rollover`).")
    incident_reports: int = Field(ge=0, description="Ocorrências abertas pelo operador (E8).")
    limits_applied: int = Field(ge=0, description="Limites novos aplicados pelo equipamento (E3).")

    timeline: list[DeviceEventResponse] = Field(
        default_factory=list, description="Eventos do período, do mais recente para o mais antigo."
    )

    roadmap_note: str = Field(
        min_length=1,
        description="O que este histórico **ainda não** é, para a tela não prometer demais.",
    )

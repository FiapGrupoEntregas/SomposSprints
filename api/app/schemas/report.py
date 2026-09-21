"""Schemas dos relatórios e tendências (W12).

Três recortes, um por perfil de usuário, como o enunciado pede: **equipamento** (gestor de frota),
**região** (analista da seguradora) e **cultura** (subscrição e produto).

Cada relatório carrega um `purpose`: uma linha dizendo **para quem serve** e **que decisão apoia**.
Sem isso, um relatório vira uma tabela bonita que ninguém sabe usar.
"""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class EquipmentDay(BaseModel):
    """Um dia na série do equipamento."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date = Field(description="Dia (UTC, como a telemetria foi gravada).")
    readings: int = Field(ge=0, description="Leituras de telemetria recebidas no dia.")
    operating_hours: float = Field(ge=0.0, description="Horas operando, estimadas das leituras.")
    pct_time_above_limit: float = Field(
        ge=0.0, le=100.0, description="% das leituras com alerta 🔴 ou capotamento."
    )
    alerts: int = Field(ge=0, description="Eventos de alerta no dia (`tilt_alert` e `rollover`).")
    max_roll_deg: float | None = Field(default=None, description="Maior rolagem absoluta do dia.")


class EquipmentReport(BaseModel):
    """Tendência de um equipamento nos últimos dias (W12)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(description="Identificador do equipamento.")
    farm_id: str = Field(description="Fazenda a que ele pertence.")
    days: int = Field(gt=0, description="Tamanho da janela, em dias.")
    purpose: str = Field(description="Para quem serve e que decisão apoia.")

    readings: int = Field(ge=0, description="Leituras no período inteiro.")
    operating_hours: float = Field(ge=0.0, description="Horas operando no período.")
    pct_time_above_limit: float = Field(
        ge=0.0, le=100.0, description="% do tempo acima do limite no período."
    )
    alerts: int = Field(ge=0, description="Alertas no período.")
    rollovers: int = Field(ge=0, description="Capotamentos detectados no período.")
    trend: list[EquipmentDay] = Field(
        default_factory=list, description="Série diária, do mais antigo para o mais recente."
    )


class EventShare(BaseModel):
    """Participação de um evento (causa do sinistro) no total."""

    model_config = ConfigDict(extra="forbid")

    event_category: str = Field(description="Causa normalizada do sinistro (D1).")
    claims: int = Field(ge=0, description="Apólices com sinistro dessa causa.")
    pct_of_claims: float = Field(ge=0.0, le=100.0, description="% dos sinistros do recorte.")
    indemnity_total: float = Field(ge=0.0, description="Soma indenizada, em reais.")


class MunicipalityShare(BaseModel):
    """Um município no ranking da região."""

    model_config = ConfigDict(extra="forbid")

    municipality: str = Field(description="Município.")
    policies: int = Field(ge=0, description="Apólices no recorte.")
    claims: int = Field(ge=0, description="Apólices com sinistro.")
    claim_rate_pct: float = Field(ge=0.0, le=100.0, description="Taxa de sinistro, em %.")
    indemnity_total: float = Field(ge=0.0, description="Soma indenizada, em reais.")


class FarmProfileSummary(BaseModel):
    """O perfil de terreno de uma fazenda de demonstração, para cruzar com a região (W8 + W12)."""

    model_config = ConfigDict(extra="forbid")

    farm_id: str = Field(description="Identificador da fazenda.")
    municipality: str = Field(description="Município.")
    terrain_score: float = Field(ge=0.0, le=100.0, description="Score do terreno (§8).")
    risk_class: str = Field(description="Classe A, B ou C.")


class RegionReport(BaseModel):
    """Sinistros reais do PSR por região, cruzados com o relevo das fazendas (W12)."""

    model_config = ConfigDict(extra="forbid")

    state: str = Field(description="UF do recorte.")
    from_year: int | None = Field(default=None, description="Safra inicial, inclusive.")
    to_year: int | None = Field(default=None, description="Safra final, inclusive.")
    purpose: str = Field(description="Para quem serve e que decisão apoia.")

    policies: int = Field(ge=0, description="Apólices no recorte.")
    claims: int = Field(ge=0, description="Apólices com sinistro.")
    claim_rate_pct: float = Field(ge=0.0, le=100.0, description="Taxa de sinistro, em %.")
    indemnity_total: float = Field(ge=0.0, description="Soma indenizada, em reais.")
    indemnity_mean: float = Field(ge=0.0, description="Indenização média por sinistro, em reais.")

    top_events: list[EventShare] = Field(
        default_factory=list, description="Causas mais frequentes, da maior para a menor."
    )
    top_municipalities: list[MunicipalityShare] = Field(
        default_factory=list, description="Municípios com mais sinistros."
    )
    demo_farms: list[FarmProfileSummary] = Field(
        default_factory=list, description="Fazendas de demonstração na UF, com o perfil de terreno."
    )


class CropShare(BaseModel):
    """Uma cultura no relatório por tipo de operação."""

    model_config = ConfigDict(extra="forbid")

    crop: str = Field(description="Cultura.")
    policies: int = Field(ge=0, description="Apólices no recorte.")
    claims: int = Field(ge=0, description="Apólices com sinistro.")
    claim_rate_pct: float = Field(ge=0.0, le=100.0, description="Taxa de sinistro, em %.")
    indemnity_total: float = Field(ge=0.0, description="Soma indenizada, em reais.")
    top_event: str | None = Field(default=None, description="Causa de sinistro mais frequente.")


class CropReport(BaseModel):
    """Taxa de sinistro por cultura e por evento, a partir do PSR (W12)."""

    model_config = ConfigDict(extra="forbid")

    from_year: int | None = Field(default=None, description="Safra inicial, inclusive.")
    to_year: int | None = Field(default=None, description="Safra final, inclusive.")
    state: str | None = Field(default=None, description="UF, quando o recorte foi filtrado.")
    purpose: str = Field(description="Para quem serve e que decisão apoia.")

    policies: int = Field(ge=0, description="Apólices no recorte.")
    claims: int = Field(ge=0, description="Apólices com sinistro.")
    claim_rate_pct: float = Field(ge=0.0, le=100.0, description="Taxa de sinistro, em %.")
    crops: list[CropShare] = Field(
        default_factory=list, description="Culturas, da maior para a menor taxa de sinistro."
    )
    events: list[EventShare] = Field(
        default_factory=list, description="Causas de sinistro no recorte."
    )

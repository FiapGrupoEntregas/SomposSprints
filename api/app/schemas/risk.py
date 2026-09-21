"""Schemas da previsão de risco relevo × clima (W3).

Os níveis, o estado do solo, o limite do dia e os perigos seguem
[document/regras-de-risco.md §3 a §6](../../../document/regras-de-risco.md#3-estado-do-solo).
Os cenários simulados estão em
[§10](../../../document/regras-de-risco.md#10-cenários-simulados-demo).
"""

# O módulo é importado como `datetime` inteiro porque `DayRisk` tem um campo chamado `date`:
# com `from datetime import date`, o nome do campo esconderia o tipo e o pydantic recusaria o
# modelo ("field name clashing with a type annotation").
import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(StrEnum):
    """Nível de risco de uma célula, de um perigo ou de um dia.

    A escala 🟢 / 🟡 / 🔴 está no topo de regras-de-risco.md.
    """

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class SoilState(StrEnum):
    """Estado do solo do dia, derivado da chuva em 72 h (regras-de-risco §3)."""

    DRY = "dry"
    MOIST = "moist"
    SATURATED = "saturated"


class Hazard(StrEnum):
    """Perigos avaliados por célula e por dia.

    Capotamento e atolamento vêm da W3; raio, vento e incêndio, da W7
    (regras-de-risco §5.1 a §5.5).
    """

    ROLLOVER = "rollover"
    BOGGING = "bogging"
    LIGHTNING = "lightning"
    WIND = "wind"
    FIRE = "fire"


class Scenario(StrEnum):
    """Cenários simulados aceitos pela API (regras-de-risco §10).

    Só entram aqui os cenários **implementados**: um valor fora desta lista vira 422 na rota.
    """

    HEAVY_RAIN = "heavy_rain"
    STORM = "storm"
    HEATWAVE = "heatwave"


class ModelDriver(BaseModel):
    """Uma variável que pesa no modelo, pela importância de permutação medida na D3.

    É importância **global** do modelo, não a contribuição daquele dia: a tela precisa dizer isso
    para não sugerir que o número explica o dia específico.
    """

    model_config = ConfigDict(extra="forbid")

    feature: str = Field(description="Nome da variável no dataset da D3.")
    importance: float = Field(description="Importância de permutação medida na validação.")


class ModelScore(BaseModel):
    """A leitura do modelo para um dia (W13)."""

    model_config = ConfigDict(extra="forbid")

    probability: float = Field(ge=0.0, le=1.0, description="Probabilidade de sinistro, 0 a 1.")
    version: str | None = Field(default=None, description="Versão do artefato que respondeu.")


class ModelInfo(BaseModel):
    """Contexto do modelo, lido do artefato **em tempo de execução** (W13).

    Vai uma vez na resposta, e não por dia, para a tela poder mostrar a ressalva sem repetir o
    mesmo bloco sete vezes.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = Field(description="Versão do artefato carregado.")
    trained_at: str | None = Field(default=None, description="Quando o modelo foi treinado.")
    algorithm: str | None = Field(default=None, description="Modelo escolhido pela D3.")

    test_pr_auc: float | None = Field(default=None, description="AUC-PR do modelo no teste.")
    test_roc_auc: float | None = Field(default=None, description="AUC-ROC do modelo no teste.")
    baseline_pr_auc: float | None = Field(
        default=None, description="AUC-PR do baseline por regras no mesmo teste."
    )
    baseline_roc_auc: float | None = Field(
        default=None, description="AUC-ROC do baseline por regras no mesmo teste."
    )
    test_samples: int | None = Field(default=None, description="Linhas do conjunto de teste.")
    test_positives: int | None = Field(default=None, description="Sinistros no conjunto de teste.")
    beats_baseline: bool = Field(
        description="Se o modelo superou o baseline por regras no teste. Hoje: não."
    )

    drivers: list[ModelDriver] = Field(
        default_factory=list, description="Variáveis que mais pesam no modelo."
    )
    note: str = Field(
        min_length=1,
        description="Ressalva de leitura, montada com os números do próprio artefato.",
    )


class HazardResult(BaseModel):
    """Um perigo que pesou no nível da célula, com o motivo já escrito para a tela."""

    model_config = ConfigDict(extra="forbid")

    hazard: Hazard = Field(description="Perigo avaliado.")
    level: RiskLevel = Field(description="Nível atribuído por este perigo.")
    message: str = Field(min_length=1, description="Motivo em português, com os números.")


class CellRisk(BaseModel):
    """Risco de uma célula da grade num dia. `row`/`col` casam com a grade do relevo (W2)."""

    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=0, description="Índice da linha, de norte (0) para sul.")
    col: int = Field(ge=0, description="Índice da coluna, de oeste (0) para leste.")
    level: RiskLevel = Field(description="Pior nível entre os perigos da célula.")
    reasons: list[HazardResult] = Field(
        default_factory=list,
        description="Perigos em 🟡 ou 🔴 que explicam o nível. Vazio quando a célula é 🟢.",
    )


class LevelPercentages(BaseModel):
    """Distribuição das células por nível, em % da área (regras-de-risco §6)."""

    model_config = ConfigDict(extra="forbid")

    green: float = Field(ge=0.0, le=100.0, description="% de células 🟢.")
    yellow: float = Field(ge=0.0, le=100.0, description="% de células 🟡.")
    red: float = Field(ge=0.0, le=100.0, description="% de células 🔴.")


class DayRisk(BaseModel):
    """Um dia da previsão: o clima do dia, o limite e o risco célula a célula."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date = Field(description="Data do dia, no fuso America/Sao_Paulo.")
    rain_mm: float = Field(description="Chuva do dia, em mm (regras-de-risco §2).")
    rain_72h_mm: float = Field(description="Chuva de d−2 + d−1 + d, em mm.")
    wind_max_kmh: float | None = Field(
        default=None,
        description="Vento máximo do dia, em km/h. Vai no `config` do equipamento (E6).",
    )
    gust_max_kmh: float | None = Field(default=None, description="Rajada máxima do dia, em km/h.")
    temp_max_c: float | None = Field(default=None, description="Temperatura máxima do dia, em °C.")
    rh_min_pct: float | None = Field(
        default=None, description="Umidade relativa mínima do dia, em % (regra dos 30, §5.5)."
    )
    cape_max: float | None = Field(
        default=None, description="CAPE máximo do dia, em J/kg (instabilidade, §5.3)."
    )
    thunderstorm: bool = Field(
        default=False, description="Houve código de tempestade (95, 96 ou 99) no dia (§2)."
    )
    soil_state: SoilState = Field(description="Estado do solo do dia (regras-de-risco §3).")
    tilt_limit_deg: float = Field(
        gt=0.0, description="Limite de inclinação do dia, em graus (regras-de-risco §4)."
    )
    worst_level: RiskLevel = Field(description="Pior nível entre as células do dia.")
    pct_levels: LevelPercentages = Field(description="% da área em cada nível.")
    top_reasons: list[HazardResult] = Field(
        default_factory=list,
        description="Motivos mais frequentes entre as células 🔴 e 🟡, do mais grave ao menos.",
    )
    cells: list[CellRisk] = Field(
        default_factory=list, description="Células, de norte para sul e de oeste para leste."
    )

    # --- Score híbrido (W13). Nulos quando não há modelo carregado; a tela esconde o bloco.
    # **Nada aqui entra no cálculo do nível**: `worst_level`, `tilt_limit_deg` e os motivos das
    # células vêm só das regras, que são o que o operador segue.
    model_probability: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Probabilidade de sinistro pelo modelo (D3)."
    )
    model_version: str | None = Field(
        default=None, description="Versão do artefato que produziu a probabilidade."
    )
    model_drivers: list[str] | None = Field(
        default=None, description="As variáveis que mais pesam no modelo."
    )


class RiskForecast(BaseModel):
    """Resposta de `GET /api/v1/farms/{farm_id}/risk`."""

    model_config = ConfigDict(extra="forbid")

    farm_id: str = Field(description="Identificador da fazenda.")
    generated_at: dt.datetime = Field(description="Momento do cálculo, no fuso America/Sao_Paulo.")
    scenario: Scenario | None = Field(
        default=None,
        description="Cenário simulado aplicado à previsão, ou nulo quando é a previsão real.",
    )
    days: list[DayRisk] = Field(description="Dias de hoje em diante, em ordem cronológica.")
    model: ModelInfo | None = Field(
        default=None,
        description=(
            "Contexto do modelo preditivo (W13): versão, métricas do teste e a ressalva de "
            "leitura. Nulo quando não há artefato carregado."
        ),
    )

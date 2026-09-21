"""Schema do perfil de subscrição (W8).

Os indicadores, o score e as classes estão em
[document/regras-de-risco.md §8](../../../document/regras-de-risco.md#8-perfil-de-subscrição-w8).

⚠️ **Os pesos do score são da v1 e são arbitrários.** Eles foram escolhidos por julgamento de
engenharia, não por ajuste em dados de sinistro. Calibrá-los com a base da Sompo é o primeiro item
do roadmap de ML — e é por isso que a resposta carrega `weights_version` e `calibration_note`: o
subscritor precisa saber o que está olhando.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RiskClass(StrEnum):
    """Classe de risco do terreno (regras-de-risco §8)."""

    A = "A"
    B = "B"
    C = "C"


class ScoreDriver(BaseModel):
    """Um fator que puxou o score para baixo, com quanto ele custou."""

    model_config = ConfigDict(extra="forbid")

    factor: str = Field(description="Identificador do fator (ex.: `pct_slope_gt15`).")
    label: str = Field(description="Descrição em português, com o número.")
    points: float = Field(ge=0.0, description="Quantos pontos este fator tirou do score.")


class UnderwritingProfile(BaseModel):
    """Perfil de risco do terreno de uma fazenda, para a cotação (W8)."""

    model_config = ConfigDict(extra="forbid")

    farm_id: str = Field(description="Identificador da fazenda.")
    farm_name: str = Field(description="Nome exibido da fazenda.")
    municipality: str = Field(description="Município.")
    state: str = Field(description="Sigla da UF.")
    crop: str = Field(description="Cultura principal.")

    pct_slope_lt8: float = Field(ge=0.0, le=100.0, description="% da área com inclinação < 8°.")
    pct_slope_8_15: float = Field(ge=0.0, le=100.0, description="% da área de 8° a menos de 15°.")
    pct_slope_gt15: float = Field(ge=0.0, le=100.0, description="% da área com 15° ou mais.")
    pct_lowland: float = Field(ge=0.0, le=100.0, description="% da área em baixada.")
    pct_exposed: float = Field(ge=0.0, le=100.0, description="% da área em topo exposto.")

    slope_max_deg: float = Field(ge=0.0, description="Maior inclinação da fazenda, em graus.")
    slope_mean_deg: float = Field(ge=0.0, description="Inclinação média, em graus.")
    reference_tilt_limit_deg: float = Field(
        gt=0.0,
        description=(
            "`L_ref` em solo seco, informado como contexto. **O score não depende dele** — §8 "
            "calcula o perfil só a partir do relevo."
        ),
    )

    terrain_score: float = Field(
        ge=0.0, le=100.0, description="Score do terreno, 0 a 100; **maior = melhor** (§8)."
    )
    risk_class: RiskClass = Field(description="A (≥ 70) · B (40–69) · C (< 40).")
    drivers: list[ScoreDriver] = Field(
        default_factory=list, description="Os fatores que mais pesaram, do maior para o menor."
    )

    weights_version: str = Field(description="Versão dos pesos usados no score.")
    calibration_note: str = Field(description="Aviso de que os pesos ainda não foram calibrados.")

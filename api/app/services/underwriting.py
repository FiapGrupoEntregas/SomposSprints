"""Perfil de risco do terreno para subscrição (W8).

Função pura sobre o relevo (W2), seguindo
[document/regras-de-risco.md §8](../../../document/regras-de-risco.md#8-perfil-de-subscrição-w8):

```
terrain_score = 100 − (1,0·pct_gt15 + 0,5·pct_8_15 + 0,5·pct_lowland + 0,3·pct_exposed)
```

limitado a [0, 100], com classe **A** (≥ 70), **B** (40–69) e **C** (< 40).

⚠️ **Os pesos são da v1 e são arbitrários.** Foram escolhidos por julgamento — encosta forte pesa
mais que meia-encosta, baixada atola, topo exposto pega raio e vento —, **não** por ajuste em
dados de sinistro. A resposta diz isso ao subscritor, em vez de deixar o número parecer calibrado.
Trocar estes pesos por coeficientes ajustados na base da Sompo é o primeiro item do roadmap de ML,
e é o gancho que o pitch usa.

O perfil **não depende da previsão**: é a característica permanente do terreno, que é exatamente
o que serve para precificar no momento da cotação.
"""

from typing import Final

from app.schemas.farm import Farm
from app.schemas.terrain import TerrainClass, TerrainResponse
from app.schemas.underwriting import RiskClass, ScoreDriver, UnderwritingProfile
from app.services.risk import format_number

# regras-de-risco §8 — peso de cada fator no desconto do score
WEIGHT_SLOPE_GT15 = 1.0
WEIGHT_SLOPE_8_15 = 0.5
WEIGHT_LOWLAND = 0.5
WEIGHT_EXPOSED = 0.3

# regras-de-risco §8 — o score começa em 100 e só desce
MAX_SCORE = 100.0
MIN_SCORE = 0.0

# regras-de-risco §8 — fronteiras das classes: A a partir de 70, B a partir de 40, C abaixo disso
CLASS_A_SCORE = 70.0
CLASS_B_SCORE = 40.0

# W8 — quantos fatores aparecem em "o que mais pesa"
MAX_DRIVERS = 3

# Versão dos pesos, para a resposta dizer com o que o número foi calculado (I5 usa a mesma ideia).
WEIGHTS_VERSION = "v1-arbitrario"
CALIBRATION_NOTE = (
    "Pesos da v1, escolhidos por julgamento de engenharia e ainda **não calibrados** com dados "
    "de sinistro. Calibrá-los com a base da Sompo é o primeiro item do roadmap."
)

SCORE_DECIMALS = 1
PERCENT_DECIMALS = 1

# Rótulo de cada fator, com o lugar do número.
DRIVER_LABELS: Final[dict[str, str]] = {
    "pct_slope_gt15": "{value}% da área com inclinação de 15° ou mais",
    "pct_slope_8_15": "{value}% da área entre 8° e 15°",
    "pct_lowland": "{value}% da área em baixada (atolamento)",
    "pct_exposed": "{value}% da área em topo exposto (raio e vento)",
}


def class_for(score: float) -> RiskClass:
    """Classe do score (regras-de-risco §8): A ≥ 70 · B de 40 a 69 · C < 40."""
    if score >= CLASS_A_SCORE:
        return RiskClass.A
    if score >= CLASS_B_SCORE:
        return RiskClass.B
    return RiskClass.C


def terrain_score(
    pct_slope_gt15: float, pct_slope_8_15: float, pct_lowland: float, pct_exposed: float
) -> float:
    """Score do terreno (regras-de-risco §8). Maior = melhor, sempre entre 0 e 100."""
    penalty = (
        WEIGHT_SLOPE_GT15 * pct_slope_gt15
        + WEIGHT_SLOPE_8_15 * pct_slope_8_15
        + WEIGHT_LOWLAND * pct_lowland
        + WEIGHT_EXPOSED * pct_exposed
    )
    return round(min(MAX_SCORE, max(MIN_SCORE, MAX_SCORE - penalty)), SCORE_DECIMALS)


def score_drivers(
    pct_slope_gt15: float, pct_slope_8_15: float, pct_lowland: float, pct_exposed: float
) -> list[ScoreDriver]:
    """ "O que mais pesa": os fatores que mais tiraram pontos, do maior para o menor (W8).

    Fator que não tirou ponto nenhum fica de fora — listar "0% em baixada" só ocuparia espaço na
    tela do subscritor.
    """
    contributions = {
        "pct_slope_gt15": WEIGHT_SLOPE_GT15 * pct_slope_gt15,
        "pct_slope_8_15": WEIGHT_SLOPE_8_15 * pct_slope_8_15,
        "pct_lowland": WEIGHT_LOWLAND * pct_lowland,
        "pct_exposed": WEIGHT_EXPOSED * pct_exposed,
    }
    values = {
        "pct_slope_gt15": pct_slope_gt15,
        "pct_slope_8_15": pct_slope_8_15,
        "pct_lowland": pct_lowland,
        "pct_exposed": pct_exposed,
    }

    ranked = sorted(contributions.items(), key=lambda item: item[1], reverse=True)
    return [
        ScoreDriver(
            factor=factor,
            label=DRIVER_LABELS[factor].format(value=format_number(values[factor])),
            points=round(points, SCORE_DECIMALS),
        )
        for factor, points in ranked[:MAX_DRIVERS]
        if points > 0.0
    ]


def class_percentages(terrain: TerrainResponse) -> tuple[float, float]:
    """`(pct_lowland, pct_exposed)` da grade (regras-de-risco §8)."""
    total = len(terrain.cells)
    if total == 0:
        return 0.0, 0.0

    lowland = sum(1 for cell in terrain.cells if cell.terrain_class is TerrainClass.LOWLAND)
    exposed = sum(1 for cell in terrain.cells if cell.terrain_class is TerrainClass.EXPOSED)
    return (
        round(100.0 * lowland / total, PERCENT_DECIMALS),
        round(100.0 * exposed / total, PERCENT_DECIMALS),
    )


def terrain_profile(terrain: TerrainResponse, farm: Farm) -> UnderwritingProfile:
    """Perfil de subscrição da fazenda a partir do relevo (W8). Função pura, sem rede.

    As faixas de inclinação vêm prontas de `TerrainStats` (W2), com as fronteiras `< 8°`,
    `[8°, 15°)` e `≥ 15°` fixadas lá — as mesmas que §5.5 usa para a escalada do incêndio.
    """
    stats = terrain.stats
    pct_lowland, pct_exposed = class_percentages(terrain)
    score = terrain_score(stats.pct_slope_gt15, stats.pct_slope_8_15, pct_lowland, pct_exposed)

    return UnderwritingProfile(
        farm_id=farm.id,
        farm_name=farm.name,
        municipality=farm.municipality,
        state=farm.state,
        crop=farm.crop,
        pct_slope_lt8=stats.pct_slope_lt8,
        pct_slope_8_15=stats.pct_slope_8_15,
        pct_slope_gt15=stats.pct_slope_gt15,
        pct_lowland=pct_lowland,
        pct_exposed=pct_exposed,
        slope_max_deg=stats.slope_max_deg,
        slope_mean_deg=stats.slope_mean_deg,
        reference_tilt_limit_deg=farm.reference_tilt_limit_deg,
        terrain_score=score,
        risk_class=class_for(score),
        drivers=score_drivers(stats.pct_slope_gt15, stats.pct_slope_8_15, pct_lowland, pct_exposed),
        weights_version=WEIGHTS_VERSION,
        calibration_note=CALIBRATION_NOTE,
    )

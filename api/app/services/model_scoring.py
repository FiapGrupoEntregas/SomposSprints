"""Ponte entre o motor de risco (W3) e o modelo preditivo (D3) — o score híbrido (W13).

O alerta ao operador continua vindo **só das regras**: nível, limite e motivos são calculados em
`app/services/risk.py` e nada aqui os altera. O modelo entra **ao lado**, como uma segunda
leitura para a seguradora. No teste de 2024 com o dataset completo ele **superou** o baseline por
regras (AUC-PR 0,144 × 0,074), mas por pouco, com 26 sinistros, e num alvo mais amplo do que o
perigo que o alerta trata — por isso o alerta continua sendo das regras. O texto da ressalva é
montado a partir do JSON do artefato, então acompanha o resultado sem edição manual.

## A ressalva que precisa viajar junto com o número

O modelo foi treinado com **uma linha por apólice/safra**: as variáveis de clima dele são somas e
máximos de um período de cobertura inteiro (`rain_total_mm`, `weather_days`, `dry_spell_max_days`).
Aqui ele é aplicado a **um dia**, que é uma janela muito mais curta do que qualquer linha que ele
viu no treino.

Isso não é um detalhe de implementação: é o que define como o número pode ser lido. Ele serve
para **comparar dias e fazendas entre si**, não como probabilidade calibrada de sinistro naquele
dia. Por isso a resposta carrega `note`, as métricas do teste e o tamanho da amostra — a
honestidade fica no dado, não só no slide.

O **relevo** tem a sua própria ressalva: o treino (D2) descreve cada apólice por uma grade
**3 × 3** de ~370 m por célula (`services/dataset.py::terrain_features`) e aqui as mesmas
variáveis são recalculadas sobre a grade **10 × 10** da fazenda (W2). A *definição* de cada
variável é a mesma — média da grade, orientação da célula central, % de baixada e de topo
exposto —, mas a **resolução** não, e média e percentuais mudam de valor com o tamanho da célula.
Mais um motivo para o número ser comparativo.
"""

import logging
from typing import Any

from app.schemas.farm import Farm
from app.schemas.risk import DayRisk, ModelDriver, ModelInfo, ModelScore
from app.schemas.terrain import TerrainResponse
from app.schemas.weather import DailyWeather
from app.services import model as model_service

# O limiar de solo saturado vem do módulo das regras (W3), não de uma cópia: é a mesma definição
# que a D2 usou para montar `days_rain72h_ge30` no dataset de treino.
from app.services.risk import SOIL_SATURATED_RAIN_72H_MM
from app.services.underwriting import class_percentages

logger = logging.getLogger(__name__)

# W13 — quantas variáveis aparecem em "o que mais pesa no modelo"
MAX_MODEL_DRIVERS = 3

# D3 — chave das métricas do modelo escolhido e do baseline no JSON de metadados
CHOSEN_KEY = "chosen"
BASELINE_KEY = "baseline_regras"

PROBABILITY_DECIMALS = 4

# As culturas do `farms.json` mapeadas para o agrupamento que o modelo usa (`crop_group`, D3).
# Uma cultura fora deste mapa entra como `None` e o pipeline trata como categoria desconhecida.
CROP_GROUP: dict[str, str] = {
    "café": "Café",
    "soja": "Soja",
    "uva": "Uva",
}


def build_features(farm: Farm, terrain: TerrainResponse, day: DailyWeather) -> dict[str, Any]:
    """Monta a linha de variáveis que o modelo espera, para **um dia** daquela fazenda.

    O relevo é o da grade (W2) e o clima é o do dia (I1). As variáveis de período recebem o que
    faz sentido para uma janela de um dia — e é justamente por isso que o resultado é indicativo
    (ver o cabeçalho do módulo).
    """
    pct_lowland, pct_exposed = class_percentages(terrain)
    stats = terrain.stats

    return {
        "slope_mean_deg": stats.slope_mean_deg,
        "slope_max_deg": stats.slope_max_deg,
        # Média das células, como no treino (`dataset.py::terrain_features` usa `grid.mean()`).
        # O ponto médio entre mínimo e máximo seria outra grandeza.
        "elevation_mean_m": _mean_elevation_m(terrain),
        "elevation_range_m": stats.elevation_range_m,
        "pct_lowland": pct_lowland,
        "pct_exposed": pct_exposed,
        "rain_total_mm": day.rain_mm,
        # Numa janela de um dia, o total e o máximo diário **são o mesmo número**. No treino
        # eram duas variáveis independentes (soma da safra × pior dia dela): mais um motivo
        # para o valor ser comparativo, e não uma probabilidade calibrada.
        "rain_max_day_mm": day.rain_mm,
        "days_rain72h_ge30": 1 if day.rain_72h_mm >= SOIL_SATURATED_RAIN_72H_MM else 0,
        "days_thunderstorm": 1 if day.thunderstorm else 0,
        "gust_max_kmh": day.gust_max_kmh,
        "temp_max_c": day.temp_max_c,
        "rh_min_pct": day.rh_min_pct,
        "dry_spell_max_days": 0 if day.rain_mm > 0 else 1,
        "weather_days": 1,
        "area_ha": None,
        "coverage_days": 1,
        "start_month": day.date.month,
        "crop_group": _crop_group(farm.crop),
        "state": farm.state,
        "aspect_label": _center_aspect_label(terrain),
        "coordinate_source": "decimal",
    }


def _crop_group(crop: str) -> str | None:
    """Agrupamento da cultura como o modelo o conhece, ou `None` com aviso no log.

    Uma cultura nova no `farms.json` vira categoria desconhecida em silêncio; o aviso poupa uma
    hora de diagnóstico quando a probabilidade parecer estranha.
    """
    group = CROP_GROUP.get(crop.lower())
    if group is None:
        logger.debug(
            "Cultura '%s' não está no mapa de `crop_group` do modelo; entra como desconhecida.",
            crop,
        )
    return group


def model_info(metadata: dict | None) -> ModelInfo | None:
    """O bloco de contexto do modelo, lido **do artefato em tempo de execução** (W13).

    Nada aqui é constante no código: a D3 retreina e o número muda sozinho na próxima subida da
    API. Sem metadados, devolve `None` e a tela esconde o bloco.
    """
    if not metadata:
        return None

    test = metadata.get("test", {})
    chosen_name = metadata.get(CHOSEN_KEY)
    chosen = test.get(chosen_name, {}) if chosen_name else {}
    baseline = test.get(BASELINE_KEY, {})

    return ModelInfo(
        version=str(metadata.get("version", "desconhecida")),
        trained_at=metadata.get("trained_at"),
        algorithm=chosen_name,
        test_pr_auc=chosen.get("pr_auc"),
        test_roc_auc=chosen.get("roc_auc"),
        baseline_pr_auc=baseline.get("pr_auc"),
        baseline_roc_auc=baseline.get("roc_auc"),
        test_samples=chosen.get("n"),
        test_positives=chosen.get("positives"),
        beats_baseline=bool(metadata.get("beats_baseline", False)),
        drivers=_drivers(metadata),
        note=_note(metadata, chosen, baseline),
    )


def score_day(
    pipeline: Any, farm: Farm, terrain: TerrainResponse, day: DailyWeather, version: str | None
) -> ModelScore | None:
    """Probabilidade do modelo para um dia, ou `None` se não houver modelo (ou se ele falhar).

    Falha de previsão **nunca** derruba a resposta: o alerta ao operador vem das regras, e o
    modelo é um extra. Sem ele, os campos vêm nulos e a tela esconde o bloco.
    """
    if pipeline is None:
        return None

    try:
        probability = model_service.predict_proba(pipeline, build_features(farm, terrain, day))
    except Exception:  # noqa: BLE001 — o modelo é acessório; as regras seguem valendo
        logger.exception(
            "Falha ao pontuar o dia %s da fazenda '%s' com o modelo.", day.date, farm.id
        )
        return None

    return ModelScore(
        probability=round(probability, PROBABILITY_DECIMALS),
        version=version,
    )


def apply_model(
    days: list[DayRisk],
    farm: Farm,
    terrain: TerrainResponse,
    daily: list[DailyWeather],
    pipeline: Any,
    info: ModelInfo | None,
) -> None:
    """Preenche `model_probability`, `model_version` e `model_drivers` em cada dia (W13).

    **Não toca em `level`, `worst_level`, `tilt_limit_deg` nem em `reasons`.** Se um dia o modelo
    mudar um desses, é bug: o nível é das regras, e é o que o operador segue.
    """
    if pipeline is None or info is None:
        return

    weather_by_date = {weather.date: weather for weather in daily}
    drivers = [driver.feature for driver in info.drivers][:MAX_MODEL_DRIVERS]

    for day_risk in days:
        weather = weather_by_date.get(day_risk.date)
        if weather is None:  # pragma: no cover — os dias vêm da mesma agregação
            continue

        score = score_day(pipeline, farm, terrain, weather, info.version)
        if score is None:
            continue

        day_risk.model_probability = score.probability
        day_risk.model_version = score.version
        day_risk.model_drivers = drivers


def _drivers(metadata: dict) -> list[ModelDriver]:
    """As variáveis que mais pesam no modelo, da importância de permutação da D3.

    São importâncias **globais** (do modelo, medidas na validação), não a contribuição daquele
    dia: é o que a D3 produz, e o rótulo na tela precisa dizer isso.
    """
    importances = metadata.get("importances") or []
    return [
        ModelDriver(
            feature=str(item.get("feature", "")),
            importance=float(item.get("importance", 0.0)),
        )
        for item in importances[:MAX_MODEL_DRIVERS]
    ]


def _note(metadata: dict, chosen: dict, baseline: dict) -> str:
    """A ressalva que viaja junto com o número, montada com os valores do próprio artefato."""
    sizes = metadata.get("split_sizes") or metadata.get("split", {}).get("sizes") or {}
    total = sum(int(value) for value in sizes.values()) if sizes else None
    samples = chosen.get("n")
    positives = chosen.get("positives")

    scores = ""
    if chosen.get("pr_auc") is not None and baseline.get("pr_auc") is not None:
        scores = f" (AUC-PR {_number(chosen['pr_auc'])} contra {_number(baseline['pr_auc'])})"

    size_note = f"{total} linhas" if total else "poucas linhas"
    test_note = f"{samples} linhas e {positives} sinistros" if samples else "uma amostra pequena"

    return (
        f"Modelo treinado com sinistros reais do PSR ({size_note}, uma linha por apólice/safra) "
        f"e testado em {test_note}. {_verdict(metadata, scores)} Aqui ele é aplicado a uma janela "
        "de um dia, bem mais curta que a safra em que foi treinado: use o valor para comparar "
        "dias e fazendas, não como probabilidade calibrada."
    )


def _verdict(metadata: dict, scores: str) -> str:
    """A comparação com o baseline **e o conectivo que combina com ela** (regras-de-risco §11).

    Quando o modelo perde, "então as regras continuam sendo a base" é a conclusão da derrota.
    Quando ele ganha, a conclusão é a mesma — mas não decorre da vitória, e emendar as duas com
    "então" entrega à banca um não-sequitur. Das três razões da §11 (explicabilidade, offline e o
    alvo), o ramo vencedor cita a do **alvo**: é a única que qualifica a própria comparação que a
    frase acabou de mostrar — o modelo ganha em "qualquer indenização", dominado por seca e geada,
    e não no encharcamento e na tempestade que o alerta trata. Explicabilidade e offline continuam
    escritas na §11 e no cartão ao lado; aqui elas não caberiam sem estourar o espaço do cartão.
    """
    if metadata.get("beats_baseline", False):
        return (
            f"No teste, ele superou o baseline por regras{scores}, mas num alvo mais amplo — "
            "qualquer indenização, dominada por seca e geada — do que o encharcamento e a "
            "tempestade que o alerta trata. **As regras continuam sendo a base do alerta ao "
            "operador.**"
        )
    return (
        f"No teste, ele **não superou** o baseline por regras{scores}, então **as regras "
        "continuam sendo a base do alerta ao operador**."
    )


def _center_aspect_label(terrain: TerrainResponse) -> str | None:
    """Orientação da **célula central**, como o dataset da D3 a registra.

    A D2 grava `aspect_label(aspect_deg[centro, centro])` — a orientação de onde fica a sede
    informada na apólice —, não a orientação mais frequente. Usar a moda aqui entregaria ao
    modelo uma variável definida de outro jeito que no treino.
    """
    center = terrain.grid_size // 2
    for cell in terrain.cells:
        if cell.row == center and cell.col == center:
            return cell.aspect_label.value
    return None


def _mean_elevation_m(terrain: TerrainResponse) -> float | None:
    """Elevação **média** das células, como `dataset.py::terrain_features` a calcula."""
    elevations = [cell.elevation_m for cell in terrain.cells]
    return sum(elevations) / len(elevations) if elevations else None


def _number(value: float) -> str:
    """Número da métrica com 3 casas e vírgula decimal, como o resto das mensagens."""
    return f"{value:.3f}".replace(".", ",")

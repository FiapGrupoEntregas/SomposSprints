"""Modelo preditivo de sinistro climático e o baseline por regras (D3).

O que este módulo faz, em ordem:

    baseline_score(df)      o score das regras de `document/regras-de-risco.md` sobre o dataset
    temporal_split(df)      treino ≤ 2021 · validação 2022–2023 · teste 2024
    train(df)               regressão logística e floresta, escolhe pela AUC-PR de validação
    evaluate(y, score, t)   AUC-ROC, AUC-PR, recall, precisão e matriz de confusão
    save_model / load_model artefato versionado (`joblib` + um JSON ao lado)
    predict_proba(features) probabilidade de uma apólice, para a API

**O modelo é comparado ao baseline no mesmo conjunto de teste e com a mesma métrica.** Se não
superar, as regras seguem no comando — e o documento diz isso (regras-de-risco §11).

**A API carrega, não treina.** Sem o arquivo do modelo, `load_model` devolve `None` e quem chama
segue só com as regras.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

# Os limiares do baseline vêm do módulo das regras (W3), não de cópias: se a regra mudar lá, o
# baseline muda junto, e a comparação continua justa.
from app.schemas.risk import SoilState
from app.services.risk import (
    BOGGING_RAIN_MM,
    FIRE_HUMIDITY_PCT,
    FIRE_TEMP_C,
    FIRE_WIND_KMH,
    ROLLOVER_DANGER_RATIO,
    WIND_DANGER_GUST_KMH,
    tilt_limit,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1"
MODEL_DIR = Path(__file__).resolve().parent.parent / "data" / "model"
MODEL_PATH = MODEL_DIR / f"risk_model_{MODEL_VERSION}.joblib"
METADATA_PATH = MODEL_DIR / f"risk_model_{MODEL_VERSION}.json"

#: Divisão temporal. 2025 **não** entra: as apólices estão em vigência e a ausência de indenização
#: é censura, não rótulo 0 (ver document/dados-e-modelo.md §1).
TRAIN_MAX_YEAR = 2021
VALIDATION_YEARS = (2022, 2023)
TEST_YEAR = 2024

#: `L_ref` padrão de 15° (regras-de-risco §4). O dataset não tem equipamento por propriedade, então
#: o baseline usa o valor de referência do documento.
BASELINE_REFERENCE_TILT_DEG = 15.0

#: Reamostragens do bootstrap para estimar a incerteza da AUC-PR de validação.
BOOTSTRAP_RESAMPLES = 400

#: Reamostragens para o intervalo de confiança do **veredito** (modelo × baseline no teste). É o
#: número que vai para o documento, então vale gastar mais do que na seleção.
VERDICT_RESAMPLES = 10_000
VERDICT_SEED = 20260920

#: Um falso negativo (não avisar e o sinistro acontecer) custa muito mais que um falso alarme, então
#: o limiar é escolhido por F-beta com **beta = 2**: recall pesa 4× mais que precisão.
THRESHOLD_BETA = 2.0

TARGET_CLAIM = "target_claim"
TARGET_RAIN_CLAIM = "target_rain_claim"

NUMERIC_FEATURES = (
    "slope_mean_deg",
    "slope_max_deg",
    "elevation_mean_m",
    "elevation_range_m",
    "pct_lowland",
    "pct_exposed",
    "rain_total_mm",
    "rain_max_day_mm",
    "days_rain72h_ge30",
    "days_thunderstorm",
    "gust_max_kmh",
    "temp_max_c",
    "rh_min_pct",
    "dry_spell_max_days",
    "weather_days",
    "area_ha",
    "coverage_days",
    "start_month",
)

CATEGORICAL_FEATURES = ("crop_group", "state", "aspect_label", "coordinate_source")

#: Colunas de propósito **fora** do modelo:
#: - `policy_year`: a divisão é temporal; aprender a taxa de cada ano não generaliza para o ano
#:   seguinte, que é exatamente o que queremos medir.
#: - `lat`/`lon`, `municipality`, `geocode_ibge`, `proposal_id`: com 2.256 linhas, o modelo
#:   decoraria localidades em vez de aprender relevo e clima. A geografia entra por `state`.
#: - `crop`: 28 valores para 775 linhas de treino; entra agrupado, como `crop_group`.
EXCLUDED_FROM_MODEL = (
    "proposal_id",
    "policy_year",
    "lat",
    "lon",
    "municipality",
    "geocode_ibge",
    "crop",
    "start_date",
    "end_date",
    TARGET_CLAIM,
    TARGET_RAIN_CLAIM,
)


# --- Baseline por regras -----------------------------------------------------------------------


def _rollover_red_days(row: pd.Series, saturated_days: float) -> float:
    """Dias 🔴 de capotamento (regras-de-risco §5.1): `slope_max / L_dia ≥ 1`.

    `L_dia` encolhe quando o solo encharca (§4). Com `L_ref = 15°`, o limite vai de 15° (seco) a
    10° (encharcado). Então:
    - inclinação acima do limite **seco**: 🔴 todos os dias;
    - acima só do limite **encharcado**: 🔴 nos dias encharcados, que é `days_rain72h_ge30`.
    """
    slope = float(row["slope_max_deg"])
    limit_dry = tilt_limit(BASELINE_REFERENCE_TILT_DEG, SoilState.DRY)
    limit_saturated = tilt_limit(BASELINE_REFERENCE_TILT_DEG, SoilState.SATURATED)

    if slope / limit_dry >= ROLLOVER_DANGER_RATIO:
        return float(row["weather_days"])
    if slope / limit_saturated >= ROLLOVER_DANGER_RATIO:
        return saturated_days
    return 0.0


def baseline_red_days(df: pd.DataFrame) -> pd.Series:
    """Estimativa do número de dias 🔴 da vigência, pelas regras.

    O dataset traz a vigência **agregada**, não dia a dia, então cada perigo entrega um **piso**
    do seu número de dias 🔴. Tomamos o **maior** desses pisos, não a soma: as regras já escolhem o
    pior perigo de cada dia (§6), e somar contaria o mesmo dia duas vezes.

    | Perigo | Piso de dias 🔴 | Regra |
    |---|---|---|
    | Capotamento | ver `_rollover_red_days` | §5.1 + §4 |
    | Atolamento | `days_rain72h_ge30`, se houver célula `lowland` | §5.2 |
    | Raio | `days_thunderstorm`, se houver célula `exposed` | §5.3 |
    | Vento | 1 dia, se a rajada máxima da safra passou de 60 km/h | §5.4 |
    | Incêndio | 1 dia, se as 3 condições da "regra dos 30" ocorreram | §5.5 |

    Vento e incêndio só entram como "pelo menos um dia" porque o dataset guarda o **extremo da
    safra**, não a série diária: sabemos que houve ao menos um dia no vermelho, não quantos.
    """
    saturated = df["days_rain72h_ge30"].astype(float)
    thunderstorm = df["days_thunderstorm"].astype(float)

    rollover = df.apply(lambda row: _rollover_red_days(row, saturated[row.name]), axis=1)
    bogging = saturated.where(df["pct_lowland"] > 0, 0.0)
    # §5.2 também marca 🟡 com chuva diária ≥ 50 mm; no piso de 🔴 isso não entra, mas a chuva
    # máxima do dia serve de piso quando a propriedade é baixada.
    bogging = bogging.where(
        ~((df["pct_lowland"] > 0) & (df["rain_max_day_mm"] >= BOGGING_RAIN_MM)),
        bogging.clip(lower=1.0),
    )
    lightning = thunderstorm.where(df["pct_exposed"] > 0, 0.0)
    wind = (df["gust_max_kmh"].fillna(0.0) >= WIND_DANGER_GUST_KMH).astype(float)
    fire = (
        (df["temp_max_c"].fillna(0.0) > FIRE_TEMP_C)
        & (df["rh_min_pct"].fillna(100.0) < FIRE_HUMIDITY_PCT)
        # O dataset guarda a rajada, não o vento sustentado: é um limite superior do vento.
        & (df["gust_max_kmh"].fillna(0.0) > FIRE_WIND_KMH)
    ).astype(float)

    return pd.concat([rollover, bogging, lightning, wind, fire], axis=1).max(axis=1)


def baseline_score(df: pd.DataFrame) -> pd.Series:
    """Score do baseline: fração da vigência que as regras classificariam 🔴, em [0, 1].

    É o baseline honesto contra o qual o modelo é medido — sem nenhum parâmetro ajustado aos
    dados, só os limiares de `document/regras-de-risco.md`.
    """
    days = df["weather_days"].astype(float).clip(lower=1.0)
    return (baseline_red_days(df) / days).clip(0.0, 1.0)


# --- Divisão temporal --------------------------------------------------------------------------


@dataclass
class Split:
    """Treino, validação e teste, separados **no tempo** (sem embaralhar)."""

    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame

    def sizes(self) -> dict[str, int]:
        return {
            "train": len(self.train),
            "validation": len(self.validation),
            "test": len(self.test),
        }


def temporal_split(df: pd.DataFrame) -> Split:
    """Treino ≤ 2021 · validação 2022–2023 · teste 2024.

    Divisão temporal, não aleatória: apólices vizinhas no espaço e no tempo se parecem, e um
    corte aleatório deixaria o modelo ver o futuro da mesma safra e da mesma região.
    """
    year = df["policy_year"].astype(int)
    return Split(
        train=df[year <= TRAIN_MAX_YEAR].copy(),
        validation=df[year.isin(VALIDATION_YEARS)].copy(),
        test=df[year == TEST_YEAR].copy(),
    )


# --- Métricas ----------------------------------------------------------------------------------


@dataclass
class Metrics:
    """Métricas de um score contínuo num conjunto, no limiar escolhido."""

    n: int
    positives: int
    positive_rate: float
    roc_auc: float | None
    pr_auc: float | None
    threshold: float
    recall: float
    precision: float
    true_negatives: int
    false_positives: int
    false_negatives: int
    true_positives: int

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate(y_true, y_score, threshold: float) -> Metrics:
    """AUC-ROC, AUC-PR, recall, precisão e matriz de confusão.

    Acurácia **não** entra: com 17% de positivos, prever "não" sempre daria 83% e zero utilidade
    (escopo da D3). Com uma só classe presente, as AUCs ficam `None` em vez de quebrar.
    """
    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score, dtype=float)
    positives = int(y_true.sum())
    both_classes = 0 < positives < len(y_true)

    y_pred = (y_score >= threshold).astype(int)
    matrix = confusion_matrix(y_true, y_pred, labels=[0, 1])
    true_negatives, false_positives, false_negatives, true_positives = matrix.ravel()

    return Metrics(
        n=len(y_true),
        positives=positives,
        positive_rate=round(positives / len(y_true), 4) if len(y_true) else 0.0,
        roc_auc=round(float(roc_auc_score(y_true, y_score)), 4) if both_classes else None,
        pr_auc=round(float(average_precision_score(y_true, y_score)), 4) if both_classes else None,
        threshold=round(float(threshold), 4),
        recall=round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        precision=round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        true_negatives=int(true_negatives),
        false_positives=int(false_positives),
        false_negatives=int(false_negatives),
        true_positives=int(true_positives),
    )


def choose_threshold(y_true, y_score, beta: float = THRESHOLD_BETA) -> float:
    """Limiar que maximiza F-beta (β = 2: recall pesa 4× mais que precisão).

    Escolhido **na validação**, nunca no teste. Justificativa em `document/dados-e-modelo.md`: no
    nosso problema, deixar de avisar custa uma máquina tombada; avisar à toa custa uma manhã
    parada.
    """
    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score, dtype=float)
    candidates = np.unique(np.round(y_score, 4))
    if candidates.size == 0:
        return 0.5

    best_threshold, best_score = float(candidates[0]), -1.0
    for threshold in candidates:
        y_pred = (y_score >= threshold).astype(int)
        recall = recall_score(y_true, y_pred, zero_division=0)
        precision = precision_score(y_true, y_pred, zero_division=0)
        denominator = beta**2 * precision + recall
        f_beta = (1 + beta**2) * precision * recall / denominator if denominator else 0.0
        if f_beta > best_score:
            best_threshold, best_score = float(threshold), f_beta
    return best_threshold


# --- Treino ------------------------------------------------------------------------------------


def feature_columns(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """As colunas numéricas e categóricas presentes no dataset, na ordem declarada."""
    numeric = [c for c in NUMERIC_FEATURES if c in df.columns]
    categorical = [c for c in CATEGORICAL_FEATURES if c in df.columns]
    return numeric, categorical


def declared_features() -> tuple[list[str], list[str]]:
    """As features que o modelo declara usar, independentemente de um dataset concreto."""
    return list(NUMERIC_FEATURES), list(CATEGORICAL_FEATURES)


def build_pipeline(estimator, numeric: list[str], categorical: list[str]) -> Pipeline:
    """Padroniza os números, faz *one-hot* nas categorias e encaixa o estimador."""
    # `area_ha` vem nula nas apólices em que a D1 corrigiu área não positiva (pecuária e
    # floresta não preenchem o campo). Imputar pela mediana **com indicador** preserva o sinal
    # "não sabemos a área" em vez de fingir que é a mediana.
    numeric_steps = Pipeline(
        [
            ("imput", SimpleImputer(strategy="median", add_indicator=True)),
            ("escala", StandardScaler()),
        ]
    )
    preprocess = ColumnTransformer(
        [
            ("num", numeric_steps, numeric),
            (
                "cat",
                Pipeline(
                    [
                        ("imput", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5)),
                    ]
                ),
                categorical,
            ),
        ],
        remainder="drop",
    )
    return Pipeline([("prep", preprocess), ("clf", estimator)])


def candidate_models(seed: int = 42) -> dict[str, Any]:
    """Dois candidatos simples e explicáveis (escopo da D3), ambos com classes balanceadas.

    A ordem importa: **do mais simples para o mais complexo**. `select_model` só troca o simples
    pelo complexo quando o ganho sai do ruído.
    """
    return {
        "regressao_logistica": LogisticRegression(
            max_iter=2000, class_weight="balanced", random_state=seed
        ),
        "floresta_aleatoria": RandomForestClassifier(
            n_estimators=300,
            # Árvore rasa e folha grande: com 775 linhas de treino, o resto é decorar.
            max_depth=6,
            min_samples_leaf=20,
            class_weight="balanced",
            random_state=seed,
            n_jobs=-1,
        ),
    }


def bootstrap_pr_auc_se(y_true, score_a, score_b, seed: int = 42) -> float:
    """Erro padrão da **diferença** de AUC-PR entre dois scores, por bootstrap pareado.

    Pareado de propósito: as duas AUC-PR são medidas nas mesmas linhas, então o que interessa é a
    variação da diferença, não a de cada uma.
    """
    y_true = np.asarray(y_true).astype(int)
    rng = np.random.default_rng(seed)
    n = len(y_true)
    differences = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        index = rng.integers(0, n, n)
        sample = y_true[index]
        if len(np.unique(sample)) < 2:
            continue
        differences.append(
            average_precision_score(sample, np.asarray(score_b)[index])
            - average_precision_score(sample, np.asarray(score_a)[index])
        )
    return float(np.std(differences)) if differences else 0.0


def compare_on_test(y_true, baseline, model, resamples: int = VERDICT_RESAMPLES) -> dict:
    """Quanto o modelo ganha (ou perde) do baseline em AUC-PR, **com incerteza**.

    Dizer "0,144 contra 0,074" sem intervalo convida a pergunta certa da banca: *isso é diferença
    de verdade?* Com 26 positivos no teste, o intervalo é largo: em 21/09 a diferença saiu +0,0697
    com IC 95% [+0,003, +0,169], ou seja, positiva por pouco. O bootstrap é **pareado** — o mesmo
    vetor de índices reamostra os dois scores — porque as duas AUC-PR são medidas nas mesmas
    linhas.
    """
    y_true = np.asarray(y_true).astype(int)
    baseline = np.asarray(baseline, dtype=float)
    model = np.asarray(model, dtype=float)
    if len(np.unique(y_true)) < 2:
        return {}

    observed = average_precision_score(y_true, model) - average_precision_score(y_true, baseline)
    rng = np.random.default_rng(VERDICT_SEED)
    differences = []
    for _ in range(resamples):
        index = rng.integers(0, len(y_true), len(y_true))
        sample = y_true[index]
        if len(np.unique(sample)) < 2:
            continue
        differences.append(
            average_precision_score(sample, model[index])
            - average_precision_score(sample, baseline[index])
        )
    if not differences:
        return {}

    differences = np.array(differences)
    low, high = np.percentile(differences, [2.5, 97.5])
    return {
        "pr_auc_difference": round(float(observed), 4),
        "ci95_low": round(float(low), 4),
        "ci95_high": round(float(high), 4),
        "p_model_better": round(float((differences > 0).mean()), 4),
        # Zero dentro do intervalo: a amostra não distingue os dois, em nenhuma direção.
        "indistinguishable": bool(low < 0 < high),
        "resamples": int(len(differences)),
    }


def select_model(validation_scores: dict[str, Any], y_true, order: list[str], seed: int = 42):
    """Escolhe o modelo pela AUC-PR de validação, com a **regra de um erro padrão**.

    Um candidato mais complexo só desbanca o mais simples se ganhar por **mais que um erro
    padrão** da diferença. Sem isso, com 29 positivos na validação uma vantagem de 0,002 de AUC-PR
    — que é puro ruído de amostragem — escolheria a floresta, e a escolha não se sustentaria fora
    da validação. É a mesma ideia do `1-SE rule` da validação cruzada, e atende ao "desempate pela
    simplicidade" que a D3 pede.
    """
    best = order[0]
    for candidate in order[1:]:
        gain = average_precision_score(y_true, validation_scores[candidate]) - (
            average_precision_score(y_true, validation_scores[best])
        )
        se = bootstrap_pr_auc_se(
            y_true, validation_scores[best], validation_scores[candidate], seed
        )
        if gain > se:
            logger.info(
                "%s supera %s por %.4f de AUC-PR (erro padrão %.4f): troca aceita",
                candidate,
                best,
                gain,
                se,
            )
            best = candidate
        else:
            logger.info(
                "%s ganha só %.4f de AUC-PR sobre %s (erro padrão %.4f): fica o mais simples",
                candidate,
                gain,
                best,
                se,
            )
    return best


@dataclass
class TrainResult:
    """O que o treino produziu: o vencedor, as métricas de todo mundo e o porquê da escolha."""

    target: str
    chosen: str
    threshold: float
    pipeline: Any = field(repr=False)
    split_sizes: dict[str, int] = field(default_factory=dict)
    validation: dict[str, dict] = field(default_factory=dict)
    test: dict[str, dict] = field(default_factory=dict)
    importances: list[dict] = field(default_factory=list)
    beats_baseline: bool = False
    #: Diferença de AUC-PR (modelo − baseline) no teste, com IC 95% bootstrap.
    #: Ver `compare_on_test`.
    verdict: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "chosen": self.chosen,
            "threshold": self.threshold,
            "split_sizes": self.split_sizes,
            "validation": self.validation,
            "test": self.test,
            "importances": self.importances,
            "beats_baseline": self.beats_baseline,
            "verdict": self.verdict,
        }


def train(df: pd.DataFrame, target: str = TARGET_CLAIM, seed: int = 42) -> TrainResult:
    """Treina os candidatos, escolhe na **validação** e mede uma única vez no **teste**.

    O baseline por regras é avaliado nos mesmos conjuntos, com as mesmas métricas e o seu próprio
    limiar — é a única comparação que significa alguma coisa.
    """
    split = temporal_split(df)
    if split.train.empty or split.validation.empty:
        raise ValueError(
            "Divisão temporal vazia: o dataset precisa de apólices até 2021 e de 2022–2023."
        )

    numeric, categorical = feature_columns(df)
    features = numeric + categorical
    y_train = split.train[target].astype(int)
    y_validation = split.validation[target].astype(int)

    # --- baseline: sem treino, só as regras ---
    baseline_validation_score = baseline_score(split.validation)
    baseline_threshold = choose_threshold(y_validation, baseline_validation_score)
    validation_metrics = {
        "baseline_regras": evaluate(
            y_validation, baseline_validation_score, baseline_threshold
        ).as_dict()
    }

    # --- candidatos ---
    fitted: dict[str, Pipeline] = {}
    validation_scores: dict[str, np.ndarray] = {}
    for name, estimator in candidate_models(seed).items():
        pipeline = build_pipeline(estimator, numeric, categorical)
        pipeline.fit(split.train[features], y_train)
        scores = pipeline.predict_proba(split.validation[features])[:, 1]
        threshold = choose_threshold(y_validation, scores)
        validation_metrics[name] = evaluate(y_validation, scores, threshold).as_dict()
        validation_scores[name] = scores
        fitted[name] = pipeline

    chosen = select_model(validation_scores, y_validation, list(fitted))
    chosen_threshold = validation_metrics[chosen]["threshold"]

    # --- teste: tocado uma vez só, no fim ---
    test_metrics: dict[str, dict] = {}
    verdict: dict = {}
    if not split.test.empty:
        y_test = split.test[target].astype(int)
        baseline_test_score = baseline_score(split.test)
        test_metrics["baseline_regras"] = evaluate(
            y_test, baseline_test_score, baseline_threshold
        ).as_dict()
        test_scores: dict[str, np.ndarray] = {}
        for name, pipeline in fitted.items():
            scores = pipeline.predict_proba(split.test[features])[:, 1]
            test_scores[name] = scores
            test_metrics[name] = evaluate(
                y_test, scores, validation_metrics[name]["threshold"]
            ).as_dict()
        verdict = compare_on_test(y_test, baseline_test_score, test_scores[chosen])

    beats = _beats_baseline(test_metrics, chosen)
    return TrainResult(
        target=target,
        chosen=chosen,
        threshold=chosen_threshold,
        pipeline=fitted[chosen],
        split_sizes=split.sizes(),
        validation=validation_metrics,
        test=test_metrics,
        importances=_importances(fitted[chosen], split.validation, features, y_validation, seed),
        beats_baseline=beats,
        verdict=verdict,
    )


def _beats_baseline(test_metrics: dict[str, dict], chosen: str) -> bool:
    """O modelo só "vence" se tiver AUC-PR maior que a das regras **no teste**."""
    model = (test_metrics.get(chosen) or {}).get("pr_auc")
    baseline = (test_metrics.get("baseline_regras") or {}).get("pr_auc")
    if model is None or baseline is None:
        return False
    return model > baseline


def _importances(
    pipeline: Pipeline, frame: pd.DataFrame, features: list[str], y, seed: int
) -> list[dict]:
    """Importância por permutação, medida na **validação**.

    Serve para os dois candidatos (coeficiente e árvore) e já vem na escala da métrica que
    interessa, a AUC-PR.
    """
    if frame.empty or len(np.unique(y)) < 2:
        return []
    result = permutation_importance(
        pipeline,
        frame[features],
        y,
        scoring="average_precision",
        n_repeats=10,
        random_state=seed,
        n_jobs=1,
    )
    ranked = sorted(
        (
            {
                "feature": name,
                "importance": round(float(mean), 5),
                "std": round(float(std), 5),
            }
            for name, mean, std in zip(
                features, result.importances_mean, result.importances_std, strict=True
            )
        ),
        key=lambda item: item["importance"],
        reverse=True,
    )
    return ranked


# --- Artefato ----------------------------------------------------------------------------------


def save_model(result: TrainResult, dataset_path: str, rows: int, directory: Path | None = None):
    """Grava o `joblib` e, ao lado, o JSON com data, dataset, features, métricas e limiar."""
    directory = directory or MODEL_DIR
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / f"risk_model_{MODEL_VERSION}.joblib"
    metadata_path = directory / f"risk_model_{MODEL_VERSION}.json"

    joblib.dump(result.pipeline, model_path)
    numeric, categorical = declared_features()
    metadata = {
        "version": MODEL_VERSION,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": {"path": dataset_path, "rows": rows},
        "split": {
            "train": f"<= {TRAIN_MAX_YEAR}",
            "validation": list(VALIDATION_YEARS),
            "test": TEST_YEAR,
            "sizes": result.split_sizes,
        },
        "features": {"numeric": numeric, "categorical": categorical},
        "excluded_from_model": list(EXCLUDED_FROM_MODEL),
        **result.as_dict(),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return model_path, metadata_path


def load_model(path: Path | None = None):
    """Carrega o modelo salvo. Devolve `None` (com aviso) se o arquivo não existir.

    A API tem de subir sem o modelo e seguir só com as regras — é critério de aceite da D3.
    """
    path = path or MODEL_PATH
    if not path.exists():
        logger.warning(
            "Modelo não encontrado em %s; a API segue só com o score por regras "
            "(rode scripts/train_model.py para gerar).",
            path,
        )
        return None
    try:
        return joblib.load(path)
    except Exception:  # noqa: BLE001 — artefato corrompido não pode derrubar a API
        logger.exception("Falha ao carregar o modelo em %s; seguindo só com as regras.", path)
        return None


@lru_cache(maxsize=1)
def get_model():
    """Modelo carregado uma vez por processo, para a API não ler o disco a cada requisição.

    Devolve `None` quando não há artefato — a API sobe igual e segue só com as regras. Use
    `get_model.cache_clear()` depois de treinar de novo.
    """
    return load_model()


def warm_up() -> bool:
    """Carrega o modelo e faz uma previsão de mentira, para a primeira real já sair rápida.

    A primeira chamada a `predict_proba` custa ~50 ms (o `sklearn` e o `pandas` montam caches
    internos na estreia); depois, a mediana é ~7 ms. Chamar isto no *lifespan* da API tira esse
    custo da primeira requisição de verdade. Devolve `False` quando não há artefato.
    """
    pipeline = get_model()
    if pipeline is None:
        return False
    predict_proba(pipeline, {})
    return True


def load_metadata(path: Path | None = None) -> dict | None:
    """Metadados do modelo salvo (versão, data, métricas, limiar), ou `None`."""
    path = path or METADATA_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.exception("JSON de metadados inválido em %s.", path)
        return None


def predict_proba(pipeline, features: dict | pd.DataFrame) -> float:
    """Probabilidade de sinistro de **uma** apólice/talhão.

    Aceita um dicionário de features ou um `DataFrame` de uma linha. Coluna que faltar entra como
    nula e é tratada pelo pipeline.
    """
    if pipeline is None:
        raise ValueError("Não há modelo carregado; use o score por regras.")
    frame = pd.DataFrame([features]) if isinstance(features, dict) else features
    numeric, categorical = declared_features()
    for column in numeric + categorical:
        if column not in frame.columns:
            frame[column] = np.nan
    return float(pipeline.predict_proba(frame[numeric + categorical])[:, 1][0])

"""Testes do modelo preditivo e do baseline por regras (D3). Tudo offline.

Os dados aqui são sintéticos e pequenos: o objetivo é provar o **mecanismo** (limiares corretos,
divisão temporal sem vazamento, métricas, artefato), não reproduzir o resultado de treino, que
está em `document/dados-e-modelo.md`.
"""

import json
import time

import numpy as np
import pandas as pd
import pytest

from app.schemas.risk import SoilState
from app.services.model import (
    CATEGORICAL_FEATURES,
    EXCLUDED_FROM_MODEL,
    NUMERIC_FEATURES,
    TARGET_CLAIM,
    TEST_YEAR,
    TRAIN_MAX_YEAR,
    VALIDATION_YEARS,
    baseline_red_days,
    baseline_score,
    choose_threshold,
    compare_on_test,
    evaluate,
    get_model,
    load_metadata,
    load_model,
    load_optional_neural_model,
    predict_proba,
    save_model,
    save_optional_neural_model,
    score_optional_neural_model,
    temporal_split,
    train,
    train_optional_neural_model,
    warm_up,
)
from app.services.risk import WIND_DANGER_GUST_KMH, tilt_limit

BASELINE_L_REF = 15.0


def make_row(**kwargs) -> dict:
    """Uma apólice plana, seca e sem nada acontecendo — o caso 🟢 de referência."""
    row = {
        "proposal_id": "P0001",
        "state": "PR",
        "municipality": "Cascavel",
        "geocode_ibge": "4104808",
        "crop": "Soja",
        "crop_group": "Soja",
        "area_ha": 100.0,
        "lat": -25.0,
        "lon": -53.0,
        "coordinate_source": "dms",
        "policy_year": 2020,
        "start_date": "2020-01-10",
        "end_date": "2020-07-10",
        "start_month": 1,
        "coverage_days": 182,
        "slope_mean_deg": 1.0,
        "slope_max_deg": 2.0,
        "elevation_mean_m": 600.0,
        "elevation_range_m": 10.0,
        "aspect_label": "N",
        "pct_lowland": 0.0,
        "pct_exposed": 0.0,
        "rain_total_mm": 400.0,
        "rain_max_day_mm": 20.0,
        "days_rain72h_ge30": 0,
        "days_thunderstorm": 0,
        "gust_max_kmh": 30.0,
        "temp_max_c": 28.0,
        "rh_min_pct": 55.0,
        "dry_spell_max_days": 10,
        "weather_days": 100,
        "target_claim": 0,
        "target_rain_claim": 0,
    }
    row.update(kwargs)
    return row


def make_frame(n: int = 240, seed: int = 7) -> pd.DataFrame:
    """Dataset sintético cobrindo 2016–2024, com sinal fraco mas real na inclinação e na chuva."""
    rng = np.random.default_rng(seed)
    rows = []
    for index in range(n):
        year = 2016 + index % 9
        slope = float(rng.uniform(0.5, 20.0))
        saturated = int(rng.integers(0, 40))
        # Probabilidade cresce com inclinação e dias encharcados: dá ao modelo algo a aprender.
        risco = 0.05 + 0.02 * slope + 0.005 * saturated
        rows.append(
            make_row(
                proposal_id=f"P{index:04d}",
                policy_year=year,
                start_date=f"{year}-01-10",
                end_date=f"{year}-07-10",
                state=("PR", "RS", "SP", "GO")[index % 4],
                crop_group=("Soja", "Trigo", "outras")[index % 3],
                aspect_label=("N", "S", "L", "O")[index % 4],
                slope_max_deg=slope,
                slope_mean_deg=slope * 0.6,
                days_rain72h_ge30=saturated,
                days_thunderstorm=int(rng.integers(0, 10)),
                gust_max_kmh=float(rng.uniform(20, 80)),
                temp_max_c=float(rng.uniform(25, 40)),
                rh_min_pct=float(rng.uniform(15, 70)),
                dry_spell_max_days=int(rng.integers(1, 60)),
                pct_lowland=float(rng.choice([0.0, 11.1, 22.2])),
                pct_exposed=float(rng.choice([0.0, 11.1, 22.2])),
                target_claim=int(rng.random() < min(risco, 0.9)),
            )
        )
    frame = pd.DataFrame(rows)
    frame["target_rain_claim"] = frame["target_claim"] * (frame["days_thunderstorm"] > 5).astype(
        int
    )
    return frame


# --- Baseline por regras ----------------------------------------------------------------


def test_baseline_zero_em_terreno_plano_e_seco() -> None:
    frame = pd.DataFrame([make_row()])
    assert baseline_score(frame).iloc[0] == pytest.approx(0.0)


def test_baseline_marca_todos_os_dias_quando_a_inclinacao_passa_do_limite_seco() -> None:
    """`slope_max ≥ L_dia` com solo seco é 🔴 todo dia (regras-de-risco §5.1 + §4)."""
    limite_seco = tilt_limit(BASELINE_L_REF, SoilState.DRY)
    frame = pd.DataFrame([make_row(slope_max_deg=limite_seco + 1.0, weather_days=100)])
    assert baseline_red_days(frame).iloc[0] == 100
    assert baseline_score(frame).iloc[0] == pytest.approx(1.0)


def test_baseline_so_conta_dias_encharcados_em_inclinacao_intermediaria() -> None:
    """Entre o limite encharcado e o seco, só os dias de solo encharcado viram 🔴."""
    limite_seco = tilt_limit(BASELINE_L_REF, SoilState.DRY)
    limite_encharcado = tilt_limit(BASELINE_L_REF, SoilState.SATURATED)
    meio = (limite_encharcado + limite_seco) / 2
    frame = pd.DataFrame(
        [make_row(slope_max_deg=meio, days_rain72h_ge30=12, weather_days=100, pct_lowland=0.0)]
    )
    assert baseline_red_days(frame).iloc[0] == 12


def test_baseline_atolamento_exige_celula_de_baixada() -> None:
    """§5.2: 🔴 só com célula `lowland` **e** solo encharcado."""
    comum = {"slope_max_deg": 1.0, "days_rain72h_ge30": 20, "weather_days": 100}
    sem_baixada = pd.DataFrame([make_row(pct_lowland=0.0, **comum)])
    com_baixada = pd.DataFrame([make_row(pct_lowland=22.2, **comum)])
    assert baseline_red_days(sem_baixada).iloc[0] == 0
    assert baseline_red_days(com_baixada).iloc[0] == 20


def test_baseline_raio_exige_celula_exposta() -> None:
    """§5.3: 🔴 com tempestade **e** célula `exposed`."""
    comum = {"slope_max_deg": 1.0, "days_thunderstorm": 7, "weather_days": 100}
    assert baseline_red_days(pd.DataFrame([make_row(pct_exposed=0.0, **comum)])).iloc[0] == 0
    assert baseline_red_days(pd.DataFrame([make_row(pct_exposed=11.1, **comum)])).iloc[0] == 7


def test_baseline_vento_conta_ao_menos_um_dia() -> None:
    """A rajada é o extremo da safra, então só dá para afirmar "houve ao menos um dia" (§5.4)."""
    frame = pd.DataFrame([make_row(gust_max_kmh=WIND_DANGER_GUST_KMH + 5, weather_days=100)])
    assert baseline_red_days(frame).iloc[0] == 1


def test_baseline_incendio_pela_regra_dos_30() -> None:
    """§5.5: temperatura > 30 °C, umidade < 30% e vento > 30 km/h."""
    frame = pd.DataFrame(
        [make_row(temp_max_c=35.0, rh_min_pct=20.0, gust_max_kmh=40.0, weather_days=100)]
    )
    assert baseline_red_days(frame).iloc[0] == 1


def test_baseline_toma_o_maior_piso_nao_a_soma() -> None:
    """As regras já escolhem o pior perigo do dia (§6); somar contaria o mesmo dia duas vezes."""
    frame = pd.DataFrame(
        [
            make_row(
                pct_lowland=22.2,
                days_rain72h_ge30=20,
                pct_exposed=22.2,
                days_thunderstorm=7,
                gust_max_kmh=WIND_DANGER_GUST_KMH + 5,
                weather_days=100,
            )
        ]
    )
    # max(atolamento 20, raio 7, vento 1) = 20, não 28.
    assert baseline_red_days(frame).iloc[0] == 20


def test_baseline_fica_entre_zero_e_um() -> None:
    score = baseline_score(make_frame())
    assert score.between(0.0, 1.0).all()


def test_baseline_nao_tem_parametro_ajustado() -> None:
    """O baseline é determinístico: mesmas linhas, mesmo score, sem treino no meio."""
    frame = make_frame()
    assert baseline_score(frame).equals(baseline_score(frame))


# --- Divisão temporal -------------------------------------------------------------------


def test_temporal_split_respeita_os_anos() -> None:
    split = temporal_split(make_frame())
    assert (split.train["policy_year"] <= TRAIN_MAX_YEAR).all()
    assert split.validation["policy_year"].isin(VALIDATION_YEARS).all()
    assert (split.test["policy_year"] == TEST_YEAR).all()


def test_temporal_split_nao_repete_apolice_entre_conjuntos() -> None:
    split = temporal_split(make_frame())
    ids = [set(parte["proposal_id"]) for parte in (split.train, split.validation, split.test)]
    assert ids[0].isdisjoint(ids[1])
    assert ids[0].isdisjoint(ids[2])
    assert ids[1].isdisjoint(ids[2])


def test_temporal_split_nao_deixa_o_futuro_no_treino() -> None:
    """Sem embaralhar: todo ano de treino é anterior a todo ano de validação e de teste."""
    split = temporal_split(make_frame())
    assert split.train["policy_year"].max() < split.validation["policy_year"].min()
    assert split.validation["policy_year"].max() < split.test["policy_year"].min()


def test_policy_year_nao_e_feature() -> None:
    """Aprender a taxa de cada ano não generaliza para o ano seguinte — que é o que medimos."""
    assert "policy_year" in EXCLUDED_FROM_MODEL
    assert "policy_year" not in NUMERIC_FEATURES + CATEGORICAL_FEATURES


def test_rotulos_nao_sao_features() -> None:
    for alvo in ("target_claim", "target_rain_claim"):
        assert alvo in EXCLUDED_FROM_MODEL
        assert alvo not in NUMERIC_FEATURES + CATEGORICAL_FEATURES


# --- Métricas ---------------------------------------------------------------------------


def test_evaluate_matriz_de_confusao() -> None:
    y = [0, 0, 1, 1]
    score = [0.1, 0.9, 0.2, 0.8]
    m = evaluate(y, score, threshold=0.5)
    assert (m.true_positives, m.false_positives, m.false_negatives, m.true_negatives) == (
        1,
        1,
        1,
        1,
    )
    assert m.recall == pytest.approx(0.5)
    assert m.precision == pytest.approx(0.5)
    assert m.positives == 2


def test_evaluate_com_uma_classe_so_devolve_auc_nulo() -> None:
    """Conjunto sem positivo não tem AUC — melhor `None` do que um número inventado."""
    m = evaluate([0, 0, 0], [0.1, 0.2, 0.3], threshold=0.5)
    assert m.roc_auc is None and m.pr_auc is None
    assert m.positives == 0


def test_evaluate_separacao_perfeita() -> None:
    m = evaluate([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9], threshold=0.5)
    assert m.roc_auc == pytest.approx(1.0)
    assert m.recall == pytest.approx(1.0) and m.precision == pytest.approx(1.0)


def test_evaluate_e_serializavel() -> None:
    m = evaluate([0, 1], [0.2, 0.8], threshold=0.5)
    assert json.loads(json.dumps(m.as_dict()))["n"] == 2


def test_choose_threshold_prefere_recall() -> None:
    """Com β = 2, deixar de avisar pesa 4× mais que avisar à toa."""
    y = [0, 0, 0, 0, 1, 1]
    score = [0.1, 0.2, 0.3, 0.4, 0.35, 0.9]
    limiar = choose_threshold(y, score, beta=2.0)
    previsto = [1 if s >= limiar else 0 for s in score]
    # O limiar escolhido pega os dois positivos, mesmo custando falsos alarmes.
    assert previsto[4] == 1 and previsto[5] == 1


# --- Treino -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def resultado():
    return train(make_frame(), target=TARGET_CLAIM, seed=42)


def test_train_avalia_baseline_e_modelos_no_mesmo_conjunto(resultado) -> None:
    """A comparação só significa algo se for o mesmo conjunto e a mesma métrica."""
    for conjunto in (resultado.validation, resultado.test):
        assert "baseline_regras" in conjunto
        assert "regressao_logistica" in conjunto
        assert "floresta_aleatoria" in conjunto
        tamanhos = {m["n"] for m in conjunto.values()}
        assert len(tamanhos) == 1, "baseline e modelos têm de ver exatamente as mesmas linhas"


def test_train_escolhe_pela_auc_pr_da_validacao(resultado) -> None:
    melhor = max(
        ("regressao_logistica", "floresta_aleatoria"),
        key=lambda nome: resultado.validation[nome]["pr_auc"] or 0.0,
    )
    assert resultado.validation[resultado.chosen]["pr_auc"] >= (
        resultado.validation[melhor]["pr_auc"] or 0.0
    )


def test_train_usa_o_limiar_da_validacao_nao_do_teste(resultado) -> None:
    """O teste não pode ter escolhido nada — nem modelo, nem limiar."""
    assert resultado.threshold == resultado.validation[resultado.chosen]["threshold"]
    assert resultado.test[resultado.chosen]["threshold"] == resultado.threshold


def test_veredito_traz_intervalo_de_confianca(resultado) -> None:
    """Dizer "0,073 contra 0,091" sem incerteza convida a pergunta certa da banca."""
    v = resultado.verdict
    assert v, "o veredito precisa acompanhar o resultado do teste"
    assert v["ci95_low"] <= v["pr_auc_difference"] <= v["ci95_high"]
    assert 0.0 <= v["p_model_better"] <= 1.0
    assert v["indistinguishable"] == (v["ci95_low"] < 0 < v["ci95_high"])


def test_compare_on_test_detecta_diferenca_real() -> None:
    """Score perfeito contra score aleatório: o intervalo tem de excluir o zero."""
    rng = np.random.default_rng(1)
    y = np.repeat([0, 1], [200, 60])
    perfeito = y + rng.normal(0, 0.05, len(y))
    aleatorio = rng.random(len(y))
    v = compare_on_test(y, aleatorio, perfeito, resamples=500)
    assert v["pr_auc_difference"] > 0
    assert v["ci95_low"] > 0
    assert v["indistinguishable"] is False
    assert v["p_model_better"] == 1.0


def test_compare_on_test_reconhece_empate() -> None:
    """Dois scores igualmente inúteis: o zero tem de cair dentro do intervalo."""
    rng = np.random.default_rng(2)
    y = np.repeat([0, 1], [150, 8])
    v = compare_on_test(y, rng.random(len(y)), rng.random(len(y)), resamples=500)
    assert v["indistinguishable"] is True


def test_compare_on_test_com_uma_classe_so_devolve_vazio() -> None:
    assert compare_on_test([0, 0, 0], [0.1, 0.2, 0.3], [0.3, 0.2, 0.1]) == {}


def test_veredito_e_pareado_logo_reprodutivel() -> None:
    """Mesma entrada, mesmo intervalo: a semente do bootstrap é fixa."""
    rng = np.random.default_rng(3)
    y = np.repeat([0, 1], [100, 20])
    a, b = rng.random(len(y)), rng.random(len(y))
    assert compare_on_test(y, a, b, resamples=300) == compare_on_test(y, a, b, resamples=300)


def test_train_reporta_se_supera_o_baseline(resultado) -> None:
    esperado = (resultado.test[resultado.chosen]["pr_auc"] or 0.0) > (
        resultado.test["baseline_regras"]["pr_auc"] or 0.0
    )
    assert resultado.beats_baseline == esperado


def test_train_e_reprodutivel() -> None:
    frame = make_frame()
    a = train(frame, seed=42)
    b = train(frame, seed=42)
    assert a.chosen == b.chosen
    assert a.test == b.test


def test_train_traz_importancia_das_variaveis(resultado) -> None:
    assert resultado.importances
    nomes = {item["feature"] for item in resultado.importances}
    assert nomes <= set(NUMERIC_FEATURES + CATEGORICAL_FEATURES)
    valores = [item["importance"] for item in resultado.importances]
    assert valores == sorted(valores, reverse=True)


def test_train_recusa_divisao_vazia() -> None:
    so_2024 = make_frame()
    so_2024["policy_year"] = 2024
    with pytest.raises(ValueError, match="Divisão temporal vazia"):
        train(so_2024)


def test_train_registra_os_tamanhos_da_divisao(resultado) -> None:
    frame = make_frame()
    assert resultado.split_sizes == temporal_split(frame).sizes()
    assert sum(resultado.split_sizes.values()) == len(frame)


# --- Artefato ---------------------------------------------------------------------------


def test_save_e_load_preservam_a_previsao(resultado, tmp_path) -> None:
    modelo_path, meta_path = save_model(resultado, "dataset.parquet", 240, directory=tmp_path)
    recarregado = load_model(modelo_path)

    linha = make_row()
    assert predict_proba(recarregado, linha) == pytest.approx(
        predict_proba(resultado.pipeline, linha)
    )
    meta = load_metadata(meta_path)
    assert meta["dataset"] == {"path": "dataset.parquet", "rows": 240}
    assert meta["trained_at"]
    assert meta["split"]["test"] == TEST_YEAR


def test_metadados_tem_exatamente_as_metricas_do_treino(resultado, tmp_path) -> None:
    """Critério de aceite: nenhum número do JSON é escrito à mão."""
    _, meta_path = save_model(resultado, "d.parquet", 240, directory=tmp_path)
    meta = load_metadata(meta_path)
    assert meta["test"] == resultado.test
    assert meta["validation"] == resultado.validation
    assert meta["threshold"] == resultado.threshold


def test_load_model_sem_arquivo_devolve_none_com_aviso(tmp_path, caplog) -> None:
    """Sem artefato, a API sobe igual e segue só com as regras."""
    with caplog.at_level("WARNING"):
        assert load_model(tmp_path / "nao_existe.joblib") is None
    assert "só com o score por regras" in caplog.text


def test_load_rede_opcional_ausente_avisa_e_score_seguro(tmp_path, caplog) -> None:
    with caplog.at_level("WARNING"):
        model = load_optional_neural_model(tmp_path / "ausente.joblib")
    assert model is None
    assert "Artefato da rede neural opcional não encontrado" in caplog.text
    assert score_optional_neural_model(model, make_row()) is None


def test_load_rede_opcional_corrompida_nao_engole_excecao(tmp_path) -> None:
    artifact = tmp_path / "corrompido.joblib"
    artifact.write_bytes(b"nao e um joblib valido")
    with pytest.raises(KeyError):
        load_optional_neural_model(artifact)


@pytest.fixture(scope="module")
def resultado_rede_opcional():
    return train_optional_neural_model(make_frame(), target=TARGET_CLAIM, seed=42)


def test_rede_opcional_salva_carrega_e_produz_score_limitado(
    resultado_rede_opcional, tmp_path
) -> None:
    model_path, metadata_path = save_optional_neural_model(
        resultado_rede_opcional, "dataset.parquet", 240, directory=tmp_path
    )
    loaded = load_optional_neural_model(model_path)
    score = score_optional_neural_model(loaded, make_row())
    assert score is not None and 0.0 <= score <= 1.0

    metadata = load_metadata(metadata_path)
    assert metadata["validation"] == resultado_rede_opcional.validation
    assert metadata["test"] == resultado_rede_opcional.test
    assert metadata["test_used_for_selection_or_tuning"] is False
    assert metadata["seed"] == resultado_rede_opcional.seed
    assert metadata["scikit_learn_version"]
    assert (
        metadata["threshold"]
        == resultado_rede_opcional.validation["rede_neural_opcional"]["threshold"]
    )
    assert resultado_rede_opcional.test["rede_neural_opcional"]["threshold"] == (
        resultado_rede_opcional.threshold
    )


def test_load_model_com_arquivo_corrompido_devolve_none(tmp_path, caplog) -> None:
    """Artefato quebrado não pode derrubar a API."""
    ruim = tmp_path / "ruim.joblib"
    ruim.write_bytes(b"isto nao e um joblib")
    with caplog.at_level("ERROR"):
        assert load_model(ruim) is None


def test_load_metadata_sem_arquivo_devolve_none(tmp_path) -> None:
    assert load_metadata(tmp_path / "nada.json") is None


def test_predict_proba_sem_modelo_recusa() -> None:
    with pytest.raises(ValueError, match="score por regras"):
        predict_proba(None, make_row())


def test_predict_proba_tolera_coluna_faltando(resultado) -> None:
    incompleta = {"slope_max_deg": 12.0, "days_rain72h_ge30": 20}
    valor = predict_proba(resultado.pipeline, incompleta)
    assert 0.0 <= valor <= 1.0


def test_predict_proba_responde_rapido(resultado) -> None:
    """Critério de aceite: menos de 50 ms por previsão."""
    linha = make_row()
    predict_proba(resultado.pipeline, linha)  # aquece
    inicio = time.perf_counter()
    for _ in range(20):
        predict_proba(resultado.pipeline, linha)
    media_ms = (time.perf_counter() - inicio) / 20 * 1000
    assert media_ms < 50, f"{media_ms:.1f} ms por previsão"


def test_load_model_e_rapido(resultado, tmp_path) -> None:
    """Critério de aceite: a API carrega o modelo em menos de 1 s."""
    modelo_path, _ = save_model(resultado, "d.parquet", 240, directory=tmp_path)
    inicio = time.perf_counter()
    load_model(modelo_path)
    assert (time.perf_counter() - inicio) < 1.0


def test_get_model_usa_cache() -> None:
    get_model.cache_clear()
    primeiro = get_model()
    assert get_model() is primeiro
    get_model.cache_clear()


def test_warm_up_sem_artefato_devolve_false(monkeypatch, tmp_path) -> None:
    """Sem modelo, o aquecimento não quebra: só avisa que não há o que aquecer."""
    import app.services.model as model_module

    get_model.cache_clear()
    monkeypatch.setattr(model_module, "MODEL_PATH", tmp_path / "nao_existe.joblib")
    assert warm_up() is False
    get_model.cache_clear()

#!/usr/bin/env python3
"""Treina o modelo de risco, compara com o baseline por regras e salva o artefato (D3).

Imprime **baseline × modelos na mesma tabela e no mesmo conjunto de teste**. Os números do JSON
são exatamente os impressos: nada é escrito à mão.

Uso:
    uv run --project api python scripts/train_model.py
    uv run --project api python scripts/train_model.py --alvo target_rain_claim
    uv run --project api python scripts/train_model.py --dataset data/dataset_treino.parquet

Resultados e limitações: document/dados-e-modelo.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

import pandas as pd  # noqa: E402

from app.services.model import (  # noqa: E402
    TARGET_CLAIM,
    TARGET_RAIN_CLAIM,
    OptionalNeuralResult,
    TrainResult,
    save_model,
    save_optional_neural_model,
    train,
    train_optional_neural_model,
)

DATASET_PATH = ROOT / "data" / "dataset_treino.parquet"
#: Abaixo disso a métrica do conjunto é ilustrativa, não conclusiva.
MIN_TEST_POSITIVES = 30


def render_table(metrics: dict[str, dict], title: str) -> str:
    """Baseline e modelos lado a lado, na mesma tabela."""
    header = (
        f"\n{title}\n"
        f"{'':<22} {'AUC-ROC':>8} {'AUC-PR':>8} {'Recall':>8} {'Precisão':>9} {'Limiar':>8}"
        f"  {'VP':>4} {'FP':>4} {'FN':>4} {'VN':>5}"
    )
    lines = [header, "-" * 100]
    for name, m in metrics.items():
        roc = f"{m['roc_auc']:.3f}" if m["roc_auc"] is not None else "—"
        pr = f"{m['pr_auc']:.3f}" if m["pr_auc"] is not None else "—"
        lines.append(
            f"{name:<22} {roc:>8} {pr:>8} {m['recall']:>8.3f} {m['precision']:>9.3f} "
            f"{m['threshold']:>8.3f}  {m['true_positives']:>4} {m['false_positives']:>4} "
            f"{m['false_negatives']:>4} {m['true_negatives']:>5}"
        )
    return "\n".join(lines)


def render_importances(result: TrainResult, top: int = 12) -> str:
    if not result.importances:
        return "\n(sem importância de variáveis: validação com uma classe só)"
    lines = ["\nImportância das variáveis (permutação sobre a AUC-PR, na validação)"]
    for item in result.importances[:top]:
        bar = "█" * max(0, int(item["importance"] * 200))
        lines.append(f"  {item['feature']:<22} {item['importance']:+.5f} ±{item['std']:.5f} {bar}")
    return "\n".join(lines)


def report_optional_neural(result: OptionalNeuralResult) -> None:
    """Imprime métricas da rede à parte; ela nunca participa da escolha publicada."""
    print(
        render_table(
            result.validation,
            "REDE OPCIONAL — VALIDAÇÃO (2022–2023), limiar escolhido neste corte",
        )
    )
    if result.test:
        print(
            render_table(
                result.test,
                "REDE OPCIONAL — TESTE (2024), avaliação final sem ajuste",
            )
        )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--alvo", choices=[TARGET_CLAIM, TARGET_RAIN_CLAIM], default=TARGET_CLAIM)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--nao-salvar", action="store_true", help="só imprime, não grava artefato")
    parser.add_argument(
        "--somente-rede-opcional",
        action="store_true",
        help="treina e salva só o experimento neural, sem tocar no modelo publicado",
    )
    parser.add_argument(
        "--incluir-rede-opcional",
        action="store_true",
        help="além do modelo publicado, treina e salva um experimento neural separado",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if not args.dataset.exists():
        print(
            f"[erro] dataset não encontrado em {args.dataset}.\n"
            "Rode antes: uv run --project api python scripts/build_dataset.py",
            file=sys.stderr,
        )
        return 1

    frame = pd.read_parquet(args.dataset)
    print(f"Dataset: {args.dataset.name} · {len(frame)} linhas · alvo: {args.alvo}")
    resolved_dataset_path = args.dataset.resolve()
    try:
        dataset_reference = resolved_dataset_path.relative_to(ROOT).as_posix()
    except ValueError:
        dataset_reference = str(resolved_dataset_path)

    if args.somente_rede_opcional:
        neural = train_optional_neural_model(frame, target=args.alvo, seed=args.seed)
        sizes = neural.split_sizes
        print(
            f"Divisão temporal — treino ≤ 2021: {sizes['train']} · "
            f"validação 2022–2023: {sizes['validation']} · teste 2024: {sizes['test']}"
        )
        report_optional_neural(neural)
        if not args.nao_salvar:
            model_path, metadata_path = save_optional_neural_model(
                neural, dataset_reference, len(frame)
            )
            print(f"\nArtefato neural separado: {model_path.relative_to(ROOT)}")
            print(f"Metadados neurais: {metadata_path.relative_to(ROOT)}")
            saved = json.loads(metadata_path.read_text(encoding="utf-8"))
            assert saved["validation"] == neural.validation
            assert saved["test"] == neural.test
        return 0

    result = train(frame, target=args.alvo, seed=args.seed)

    sizes = result.split_sizes
    print(
        f"Divisão temporal — treino ≤ 2021: {sizes['train']} · "
        f"validação 2022–2023: {sizes['validation']} · teste 2024: {sizes['test']}"
    )

    print(render_table(result.validation, "VALIDAÇÃO (2022–2023) — usada para escolher"))
    if result.test:
        print(render_table(result.test, "TESTE (2024) — tocado uma vez só"))

    print(
        f"\nModelo escolhido: {result.chosen}"
        " (melhor AUC-PR na validação, com a regra de um erro padrão:"
        " o mais complexo só entra se ganhar acima do ruído)"
    )
    veredito = "SUPERA" if result.beats_baseline else "NÃO supera"
    print(f"No teste, o modelo {veredito} o baseline por regras em AUC-PR.")
    if not result.beats_baseline:
        print("  → As regras seguem no comando (regras-de-risco §11). Resultado publicado assim.")

    v = result.verdict
    if v:
        print(
            f"  diferença de AUC-PR (modelo − baseline): {v['pr_auc_difference']:+.4f}"
            f"  ·  IC 95% [{v['ci95_low']:+.3f}, {v['ci95_high']:+.3f}]"
            f"  ·  P(modelo > baseline) = {v['p_model_better']:.2f}"
            f"  ({v['resamples']} reamostragens)"
        )
        if v["indistinguishable"]:
            print(
                "  → O zero está dentro do intervalo: com esta amostra os dois são "
                "estatisticamente INDISTINGUÍVEIS.\n"
                "    Não daria para demonstrar superioridade do modelo nem se ela existisse."
            )

    test = result.test.get(result.chosen)
    if test and test["positives"] < MIN_TEST_POSITIVES:
        print(
            f"\n⚠️  O teste tem só {test['positives']} positivos "
            f"(mínimo para conclusão firme: {MIN_TEST_POSITIVES}). "
            "Trate estas métricas como indicativas, não conclusivas."
        )

    print(render_importances(result))

    neural: OptionalNeuralResult | None = None
    if args.incluir_rede_opcional:
        neural = train_optional_neural_model(frame, target=args.alvo, seed=args.seed)
        report_optional_neural(neural)

    if args.nao_salvar:
        return 0

    model_path, metadata_path = save_model(result, dataset_reference, len(frame))
    print(f"\nArtefato: {model_path.relative_to(ROOT)}")
    print(f"Metadados: {metadata_path.relative_to(ROOT)}")
    if neural is not None:
        neural_path, neural_metadata_path = save_optional_neural_model(
            neural, dataset_reference, len(frame)
        )
        print(f"Artefato neural separado: {neural_path.relative_to(ROOT)}")
        print(f"Metadados neurais: {neural_metadata_path.relative_to(ROOT)}")
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved["test"] == result.test, "o JSON tem de ter exatamente as métricas impressas"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

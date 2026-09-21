#!/usr/bin/env python3
"""Carrega os CSVs do PSR/SISSER no SQLite e imprime o relatório de qualidade (D1).

Lê os arquivos de `data/raw/` (ou a amostra versionada, com `--sample`), passa pelo pipeline de
`app/services/psr_ingest.py` e grava na tabela `policy`. Recarregar é idempotente: proposta que já
está no banco é ignorada.

Uso:
    uv run --project api python scripts/load_psr.py                 # tudo o que houver em data/raw
    uv run --project api python scripts/load_psr.py --sample        # só a amostra versionada
    uv run --project api python scripts/load_psr.py --json relatorio.json

Fonte, licença (CC-BY) e limitações: document/dados-e-modelo.md §1.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from _bootstrap import bootstrap  # noqa: E402  módulo vizinho, em scripts/

ROOT = bootstrap()

from sqlmodel import Session  # noqa: E402

from app.db import create_db, get_engine  # noqa: E402
from app.services.psr_ingest import QualityReport, ingest_file  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
SAMPLE_PATH = ROOT / "data" / "sample" / "psr_amostra.csv"

RAW_FILES = (
    "dados_abertos_psr_2006a2015csv.csv",
    "dados_abertos_psr_2016a2024csv.csv",
    "dados_abertos_psr_2025csv.csv",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help="carrega só data/sample/")
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--json", type=Path, help="grava o relatório também em JSON")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.sample:
        paths = [SAMPLE_PATH]
    else:
        paths = [RAW_DIR / name for name in RAW_FILES if (RAW_DIR / name).exists()]

    if not paths:
        print(
            "[erro] nenhum CSV encontrado. Rode antes:\n"
            "  uv run --project api python scripts/download_psr.py",
            file=sys.stderr,
        )
        return 1

    create_db()
    total = QualityReport()
    per_file: dict[str, dict] = {}

    with Session(get_engine()) as session:
        for path in paths:
            print(f"\n=== {path.name} ===", flush=True)
            report = ingest_file(path, session, chunk_size=args.chunk_size)
            print(report.render())
            per_file[path.name] = report.as_dict()
            total.merge(report)

    if len(paths) > 1:
        print("\n=== TOTAL ===")
        print(total.render())

    if args.json:
        args.json.write_text(
            json.dumps(
                {"por_arquivo": per_file, "total": total.as_dict()}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(f"\nRelatório em {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

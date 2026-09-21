#!/usr/bin/env python3
"""Gera a amostra versionada do PSR/SISSER em `data/sample/psr_amostra.csv` (D1).

A amostra existe para que os testes rodem **offline** e para que a demo funcione mesmo com o portal
do Mapa fora do ar. Ela é feita de **linhas reais** dos CSVs de `data/raw/`, escolhidas de forma
estratificada para cobrir todas as categorias de evento, as duas formas de coordenada
(decimal e grau/minuto/segundo) e as principais UFs.

⚠️ **LGPD (ADR-011):** as colunas `NM_SEGURADO` e `NR_DOCUMENTO_SEGURADO` continuam existindo no
arquivo — de propósito, para que o teste de anonimização seja honesto: ele prova que o pipeline
descarta essas colunas mesmo quando elas estão presentes. Os **valores** são substituídos por
marcadores fixos (`ANONIMIZADO` e `***`), então nenhum dado pessoal é versionado.

Uso:
    uv run --project api python scripts/build_psr_sample.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SAMPLE_PATH = ROOT / "data" / "sample" / "psr_amostra.csv"

sys.path.insert(0, str(ROOT / "api"))
from app.services.psr_ingest import (  # noqa: E402
    CSV_ENCODING,
    CSV_SEPARATOR,
    MISSING_MARKERS,
    PERSONAL_COLUMNS,
    normalize_event,
)

#: Valores gravados no lugar dos dados pessoais.
REDACTED = {"NM_SEGURADO": "ANONIMIZADO", "NR_DOCUMENTO_SEGURADO": "***"}

#: Linhas por categoria de evento, para o rótulo raro não sumir da amostra.
ROWS_PER_EVENT = 55
#: Linhas do arquivo de 2025 (coordenada decimal, apólices ainda em vigência).
ROWS_FROM_2025 = 60
#: Linhas de Jataí/GO, que o teste de acento usa ("Jataí" e "Milho 2ª safra").
ROWS_JATAI = 10
RANDOM_SEED = 42

FILES = {
    "2016a2024": RAW_DIR / "dados_abertos_psr_2016a2024csv.csv",
    "2025": RAW_DIR / "dados_abertos_psr_2025csv.csv",
}


def _read_raw(path: Path, chunk_size: int = 200_000):
    """Lê o CSV bruto inteiro (com as colunas pessoais), em blocos."""
    return pd.read_csv(
        path,
        sep=CSV_SEPARATOR,
        encoding=CSV_ENCODING,
        dtype=str,
        chunksize=chunk_size,
        na_values=list(MISSING_MARKERS),
        keep_default_na=False,
    )


def collect() -> pd.DataFrame:
    """Percorre os brutos uma vez e junta as linhas escolhidas."""
    by_event: dict[str, list[pd.DataFrame]] = {}
    jatai: list[pd.DataFrame] = []
    from_2025: list[pd.DataFrame] = []

    for chunk in _read_raw(FILES["2016a2024"]):
        chunk = chunk.assign(_event=chunk["EVENTO_PREPONDERANTE"].map(normalize_event))
        for event, group in chunk.groupby("_event"):
            bucket = by_event.setdefault(str(event), [])
            if sum(len(part) for part in bucket) < ROWS_PER_EVENT:
                bucket.append(group.head(ROWS_PER_EVENT))

    for chunk in _read_raw(FILES["2025"]):
        if sum(len(part) for part in from_2025) < ROWS_FROM_2025:
            from_2025.append(chunk.head(ROWS_FROM_2025))
        hit = chunk[chunk["NM_MUNICIPIO_PROPRIEDADE"] == "Jataí"]
        if len(hit) and sum(len(part) for part in jatai) < ROWS_JATAI:
            jatai.append(hit.head(ROWS_JATAI))

    parts = [part.head(ROWS_PER_EVENT) for bucket in by_event.values() for part in bucket]
    parts += from_2025 + jatai
    frame = pd.concat(parts, ignore_index=True)
    frame = frame.drop(columns=["_event"])
    # Uma proposta só pode aparecer uma vez: a tabela `policy` tem UNIQUE em `proposal_id`.
    frame = frame.drop_duplicates(subset=["ID_PROPOSTA"])
    return frame.sample(frac=1.0, random_state=RANDOM_SEED).reset_index(drop=True)


def anonymize(frame: pd.DataFrame) -> pd.DataFrame:
    """Substitui os valores das colunas pessoais por marcadores fixos (ADR-011)."""
    frame = frame.copy()
    for column, placeholder in REDACTED.items():
        if column in frame.columns:
            frame[column] = placeholder
    return frame


def main() -> int:
    missing = [str(path) for path in FILES.values() if not path.exists()]
    if missing:
        print(f"[erro] faltam os brutos: {missing}", file=sys.stderr)
        print("Rode antes: uv run --project api python scripts/download_psr.py", file=sys.stderr)
        return 1

    frame = anonymize(collect())

    for column in PERSONAL_COLUMNS:
        unique = set(frame[column].unique())
        assert unique <= set(REDACTED.values()), f"{column} ainda tem valor real: {unique}"

    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # `na_rep="-"`: o SISSER marca o vazio com "-", e a amostra imita o arquivo original.
    frame.to_csv(SAMPLE_PATH, sep=CSV_SEPARATOR, encoding=CSV_ENCODING, index=False, na_rep="-")
    size_kb = SAMPLE_PATH.stat().st_size / 1024
    print(f"{SAMPLE_PATH.relative_to(ROOT)}: {len(frame)} linhas, {size_kb:.0f} KB")
    print(frame["EVENTO_PREPONDERANTE"].fillna("(vazio)").value_counts().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Baixa os CSVs do PSR/SISSER e o dicionário de dados para `data/raw/` (D1).

Os arquivos brutos somam ~480 MB e **não** vão para o Git (`data/raw/` está no `.gitignore`).
O que é versionado é este script e a amostra anonimizada em `data/sample/`.

O portal de dados abertos do Mapa responde **403 para o user-agent padrão do curl/requests**:
por isso enviamos um user-agent de navegador. O catálogo CKAN dá as URLs de download, que trazem
um UUID de recurso e mudam quando o Mapa republica o dataset — por isso resolvemos pelo catálogo,
com uma lista fixa de reserva.

Uso:
    uv run --project api python scripts/download_psr.py            # tudo
    uv run --project api python scripts/download_psr.py --only 2025
    uv run --project api python scripts/download_psr.py --force    # rebaixa o que já existe

Fonte, licença (CC-BY) e limitações: document/dados-e-modelo.md §1.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

CKAN_PACKAGE_URL = "https://dados.agricultura.gov.br/api/3/action/package_show?id=sisser3"

# O portal recusa o user-agent padrão das bibliotecas HTTP (403).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

DOWNLOAD_TIMEOUT_S = 1800.0
CATALOG_TIMEOUT_S = 60.0
CHUNK_BYTES = 1 << 20

# Abaixo disso o download claramente falhou (veio uma página de erro, por exemplo).
MIN_SIZE_BYTES = {"2006a2015": 100_000_000, "2016a2024": 200_000_000, "2025": 5_000_000}

# Primeira linha esperada dos CSVs (conferida em 19/09/2026).
EXPECTED_FIRST_COLUMNS = ("NM_RAZAO_SOCIAL", "CD_PROCESSO_SUSEP", "NR_PROPOSTA", "ID_PROPOSTA")
CSV_ENCODING = "ISO-8859-1"


@dataclass(frozen=True)
class Resource:
    """Um arquivo do dataset."""

    key: str
    filename: str
    url: str
    is_csv: bool


#: Reserva, usada se o catálogo CKAN estiver fora do ar (URLs conferidas em 19/09/2026).
FALLBACK_RESOURCES: tuple[Resource, ...] = (
    Resource(
        "2006a2015",
        "dados_abertos_psr_2006a2015csv.csv",
        "https://dados.agricultura.gov.br/dataset/baefdc68-9bad-4204-83e8-f2888b79ab48/resource/"
        "97f29a77-4e7e-44bf-99b3-a2d75911b6bf/download/dados_abertos_psr_2006a2015csv.csv",
        True,
    ),
    Resource(
        "2016a2024",
        "dados_abertos_psr_2016a2024csv.csv",
        "https://dados.agricultura.gov.br/dataset/baefdc68-9bad-4204-83e8-f2888b79ab48/resource/"
        "54e04a6b-15b3-4bda-a330-b8e805deabe4/download/dados_abertos_psr_2016a2024csv.csv",
        True,
    ),
    Resource(
        "2025",
        "dados_abertos_psr_2025csv.csv",
        "https://dados.agricultura.gov.br/dataset/baefdc68-9bad-4204-83e8-f2888b79ab48/resource/"
        "ac7e4351-974f-4958-9294-627c5cbf289a/download/dados_abertos_psr_2025csv.csv",
        True,
    ),
    Resource(
        "dicionario",
        "dicionariodedados-sisser.pdf",
        "https://dados.agricultura.gov.br/dataset/baefdc68-9bad-4204-83e8-f2888b79ab48/resource/"
        "2c5c55d0-1473-4749-b08f-cfaf887a9fa3/download/dicionariodedados-sisser.pdf",
        False,
    ),
)


def resolve_resources(client: httpx.Client) -> tuple[Resource, ...]:
    """Pega as URLs no catálogo CKAN; se ele falhar, usa a lista de reserva."""
    try:
        response = client.get(CKAN_PACKAGE_URL, timeout=CATALOG_TIMEOUT_S)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        print(f"[aviso] catálogo CKAN indisponível ({error}); usando as URLs de reserva")
        return FALLBACK_RESOURCES

    if not payload.get("success"):
        print("[aviso] catálogo CKAN respondeu sem sucesso; usando as URLs de reserva")
        return FALLBACK_RESOURCES

    resolved: list[Resource] = []
    for fallback in FALLBACK_RESOURCES:
        wanted = "PDF" if not fallback.is_csv else "CSV"
        match = next(
            (
                item
                for item in payload["result"]["resources"]
                if (item.get("format") or "").upper() == wanted
                and fallback.key in (item.get("url") or "").replace("-", "")
            ),
            None,
        )
        url = match["url"] if match else fallback.url
        resolved.append(Resource(fallback.key, fallback.filename, url, fallback.is_csv))
    return tuple(resolved)


def check_csv_header(path: Path) -> None:
    """Confere que o arquivo baixado é mesmo o CSV do SISSER, e não uma página de erro."""
    with path.open("r", encoding=CSV_ENCODING) as handle:
        header = handle.readline().strip()
    columns = header.split(";")
    if tuple(columns[: len(EXPECTED_FIRST_COLUMNS)]) != EXPECTED_FIRST_COLUMNS:
        raise ValueError(
            f"{path.name}: cabeçalho inesperado.\n"
            f"  esperado começar com: {';'.join(EXPECTED_FIRST_COLUMNS)}\n"
            f"  veio: {header[:200]}"
        )
    print(f"      cabeçalho ok ({len(columns)} colunas)")


def download(client: httpx.Client, resource: Resource, destination: Path, force: bool) -> bool:
    """Baixa um arquivo. Devolve `False` se pulou porque já existia (idempotência)."""
    if destination.exists() and not force:
        size_mb = destination.stat().st_size / 1e6
        print(f"[pular] {resource.filename} já existe ({size_mb:.1f} MB)")
        return False

    print(f"[baixar] {resource.filename}")
    partial = destination.with_suffix(destination.suffix + ".part")
    downloaded = 0
    with client.stream("GET", resource.url, timeout=DOWNLOAD_TIMEOUT_S) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for block in response.iter_bytes(CHUNK_BYTES):
                handle.write(block)
                downloaded += len(block)

    minimum = MIN_SIZE_BYTES.get(resource.key, 10_000)
    if downloaded < minimum:
        partial.unlink(missing_ok=True)
        raise ValueError(
            f"{resource.filename}: veio com {downloaded} bytes, menos que o mínimo {minimum}. "
            "O portal pode ter devolvido uma página de erro."
        )

    # Só troca o arquivo bom pelo novo depois que o download inteiro deu certo.
    partial.replace(destination)
    print(f"      {downloaded / 1e6:.1f} MB")
    if resource.is_csv:
        check_csv_header(destination)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        choices=[resource.key for resource in FALLBACK_RESOURCES],
        help="baixa só o(s) recurso(s) indicado(s)",
    )
    parser.add_argument("--force", action="store_true", help="rebaixa mesmo se já existir")
    parser.add_argument("--dest", type=Path, default=RAW_DIR, help="pasta de destino")
    args = parser.parse_args(argv)

    args.dest.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": BROWSER_USER_AGENT}

    with httpx.Client(headers=headers, follow_redirects=True) as client:
        resources = resolve_resources(client)
        selected = [r for r in resources if not args.only or r.key in args.only]
        for resource in selected:
            try:
                download(client, resource, args.dest / resource.filename, args.force)
            except (httpx.HTTPError, ValueError) as error:
                print(f"[erro] {resource.filename}: {error}", file=sys.stderr)
                return 1

    print(f"\nPronto. Arquivos em {args.dest} (fora do Git).")
    print("Para carregar no banco: uv run --project api python scripts/load_psr.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

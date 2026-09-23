#!/usr/bin/env python3
"""Mede o critério 7 da US-03: o mapa em < 3 s sem cache e < 200 ms com cache.

Sobe a API num processo próprio — cache vazio, MQTT desligado —, mede a **primeira** chamada de
cada rota (fria: bate na Open-Meteo) e depois repetições (quentes: servidas do `TTLCache`).

Antes de medir qualquer coisa, confere se a Open-Meteo está respondendo. Medir com a cota
esgotada mediria a **recusa**, não o mapa — o script recusa rodar nesse caso, de propósito.

Uso:
    uv run --project api python scripts/medir_mapa.py
    uv run --project api python scripts/medir_mapa.py --saida /tmp/medicao.json
    uv run --project api python scripts/medir_mapa.py --sem-clima   # só afere o mecanismo

A cota da Open-Meteo vira às 00:00 UTC (21:00 em Brasília). Resultado e método:
document/evidencias/ e document/user-stories.md (US-03).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from _bootstrap import bootstrap  # noqa: E402  módulo vizinho, em scripts/

ROOT = bootstrap()
API_DIR = ROOT / "api"

import httpx  # noqa: E402

#: Os limites do critério 7 da US-03, em milissegundos.
LIMITE_FRIO_MS = 3000
LIMITE_QUENTE_MS = 200

FAZENDAS = ("cafe-carmo-de-minas", "graos-sorriso", "uva-serra-gaucha")
ROTAS_COM_CLIMA = ("terrain", "risk")
#: Rotas que não tocam a Open-Meteo: servem para conferir o próprio medidor.
ROTAS_SEM_CLIMA = ("",)
REPETICOES_QUENTES = 5
PORTA = 8123


def cota_disponivel() -> tuple[bool, str]:
    """A Open-Meteo aceita chamada agora? Devolve (ok, motivo)."""
    try:
        resposta = httpx.get(
            "https://api.open-meteo.com/v1/elevation",
            params={"latitude": -21.9, "longitude": -45.1},
            timeout=20.0,
        )
    except httpx.HTTPError as erro:
        return False, f"não deu para falar com a Open-Meteo: {erro}"
    corpo = resposta.text[:200]
    if resposta.status_code == 200 and "elevation" in corpo:
        return True, "respondendo"
    if "Daily API request limit" in corpo:
        return False, "cota diária esgotada — vira às 00:00 UTC (21:00 em Brasília)"
    return False, f"HTTP {resposta.status_code}: {corpo}"


def sobe_api() -> subprocess.Popen:
    ambiente = dict(os.environ, AGRISHIELD_MQTT_ENABLED="false")
    processo = subprocess.Popen(
        [
            str(API_DIR / ".venv" / "bin" / "python"),
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(PORTA),
            "--log-level",
            "warning",
        ],
        cwd=API_DIR,
        env=ambiente,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        try:
            httpx.get(f"http://127.0.0.1:{PORTA}/api/v1/health", timeout=2.0)
            return processo
        except httpx.HTTPError:
            time.sleep(0.5)
    processo.terminate()
    raise SystemExit("[erro] a API não subiu em 30 s")


def mede(cliente: httpx.Client, url: str) -> dict:
    inicio = time.perf_counter()
    resposta = cliente.get(url)
    frio_ms = (time.perf_counter() - inicio) * 1000

    quentes = []
    for _ in range(REPETICOES_QUENTES):
        inicio = time.perf_counter()
        repetida = cliente.get(url)
        quentes.append((time.perf_counter() - inicio) * 1000)
        repetida.raise_for_status()

    return {
        "url": url,
        "status": resposta.status_code,
        "bytes": len(resposta.content),
        "frio_ms": round(frio_ms, 1),
        "quentes_ms": [round(valor, 1) for valor in quentes],
        "quente_mediana_ms": round(statistics.median(quentes), 1),
        "quente_maximo_ms": round(max(quentes), 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida", type=Path, help="grava o resultado em JSON")
    parser.add_argument(
        "--sem-clima",
        action="store_true",
        help="mede só rotas que não tocam a Open-Meteo (afere o medidor, não o critério)",
    )
    args = parser.parse_args(argv)

    if not args.sem_clima:
        ok, motivo = cota_disponivel()
        print(f"Open-Meteo: {motivo}")
        if not ok:
            print(
                "\n[parado] Medir agora mediria a recusa da Open-Meteo, não o mapa.\n"
                "         Rode de novo com cota disponível, ou use --sem-clima para conferir\n"
                "         só o mecanismo de medição.",
                file=sys.stderr,
            )
            return 2

    rotas = ROTAS_SEM_CLIMA if args.sem_clima else ROTAS_COM_CLIMA
    processo = sobe_api()
    base = f"http://127.0.0.1:{PORTA}/api/v1"
    resultados: list[dict] = []
    try:
        with httpx.Client(timeout=60.0) as cliente:
            for fazenda in FAZENDAS:
                for rota in rotas:
                    caminho = f"{base}/farms/{fazenda}" + (f"/{rota}" if rota else "")
                    medida = mede(cliente, caminho)
                    medida["fazenda"] = fazenda
                    medida["rota"] = rota or "detalhe"
                    resultados.append(medida)
                    print(
                        f"  {fazenda:<22} {medida['rota']:<8} "
                        f"frio {medida['frio_ms']:>8.1f} ms · "
                        f"quente mediana {medida['quente_mediana_ms']:>6.1f} ms "
                        f"(máx {medida['quente_maximo_ms']:.1f}) · HTTP {medida['status']}",
                        flush=True,
                    )
    finally:
        processo.terminate()
        processo.wait(timeout=10)

    frios = [item["frio_ms"] for item in resultados]
    quentes = [item["quente_maximo_ms"] for item in resultados]
    print(
        f"\nFrio: pior {max(frios):.1f} ms (limite {LIMITE_FRIO_MS}) · "
        f"Quente: pior {max(quentes):.1f} ms (limite {LIMITE_QUENTE_MS})"
    )
    if not args.sem_clima:
        passou = max(frios) < LIMITE_FRIO_MS and max(quentes) < LIMITE_QUENTE_MS
        print("Critério 7 da US-03:", "ATENDIDO" if passou else "NÃO ATENDIDO")

    if args.saida:
        args.saida.write_text(json.dumps(resultados, indent=2), encoding="utf-8")
        print("Gravado em", args.saida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

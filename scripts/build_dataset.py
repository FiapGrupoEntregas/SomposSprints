#!/usr/bin/env python3
"""Gera o dataset de treino relevo × clima × sinistro (D2).

Amostra as apólices da tabela `policy` (D1), busca o relevo (W2) e o clima da vigência (I1) e
escreve `data/dataset_treino.parquet`.

**Retomável.** Cada linha pronta é gravada na hora em `data/.dataset_parcial.jsonl`. Se o script
morrer, a execução seguinte lê esse arquivo, pula as apólices que já têm linha e continua. O
parquet final só é escrito no fim, a partir do parcial inteiro — então matar o script nunca deixa
um parquet pela metade.

Uso:
    uv run --project api python scripts/build_dataset.py                 # 3.000 apólices
    uv run --project api python scripts/build_dataset.py -n 500
    uv run --project api python scripts/build_dataset.py --reiniciar     # ignora o parcial
    uv run --project api python scripts/build_dataset.py --rpm 300       # limite de req/min

Fontes, features e limitações: document/dados-e-modelo.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from _bootstrap import bootstrap  # noqa: E402  módulo vizinho, em scripts/

ROOT = bootstrap()

import pandas as pd  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.clients.open_meteo import (  # noqa: E402
    OpenMeteoClient,
    WeatherUnavailableError,
    _cache_key,
)
from app.core.cache import TTLCache  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.services.dataset import (  # noqa: E402
    DATASET_COLUMNS,
    DatasetReport,
    build_features,
    sample_policies,
    save_dataset,
)

DATA_DIR = ROOT / "data"
PARTIAL_PATH = DATA_DIR / ".dataset_parcial.jsonl"
DATASET_PATH = DATA_DIR / "dataset_treino.parquet"
REPORT_PATH = DATA_DIR / "dataset_treino.json"

#: Fontes que entram em cada linha, para a ficha do dataset (document/dados-e-modelo.md).
FONTES = {
    "apolices": "PSR/SISSER — Ministério da Agricultura, CC-BY (D1, tabela policy)",
    "relevo": "Open-Meteo Elevation — Copernicus DEM GLO-90 (~90 m)",
    "clima_ate_2021": "Open-Meteo Archive (ERA5)",
    "clima_2022_em_diante": "Open-Meteo Historical Forecast",
}

DEFAULT_SAMPLE = 3000
DEFAULT_SEED = 42
#: Teto de requisições por minuto. A Open-Meteo **pondera** a chamada pelo número de pontos e de
#: variáveis, então uma chamada de elevação com 99 pontos vale por muitas: a 600/min (o teto
#: nominal da conta gratuita) o portal devolve 429. 60/min passou sem recusa.
DEFAULT_RPM = 120

#: A elevação (`api.open-meteo.com`) tem cota própria e bem mais apertada que a do histórico:
#: ver a medição em `app/services/dataset.py::PROPERTIES_PER_ELEVATION_CALL`.
DEFAULT_RPM_ELEVATION = 60

#: Espera entre as tentativas depois de uma recusa (429 ou instabilidade), em segundos.
RETRY_BACKOFF_S = (15.0, 45.0, 120.0)


def _display(path: Path) -> str:
    """Caminho relativo ao repositório quando possível; absoluto quando o destino é de fora."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


class RateLimiter:
    """Espaça as chamadas para não passar de `rpm` requisições por minuto."""

    def __init__(self, rpm: int) -> None:
        self._interval_s = 60.0 / rpm if rpm > 0 else 0.0
        self._next_at = 0.0

    def wait(self) -> None:
        if self._interval_s <= 0:
            return
        delay = self._next_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next_at = time.monotonic() + self._interval_s


class ThrottledClient(OpenMeteoClient):
    """`OpenMeteoClient` com ritmo controlado e nova tentativa depois de uma recusa.

    O cliente da I1 não tenta de novo, e está certo: quem serve uma requisição HTTP tem de
    responder rápido. Numa geração em lote de horas, porém, desistir na primeira recusa jogaria
    fora a apólice — então a retentativa mora aqui, no script, e não no cliente.
    """

    def __init__(
        self, limiter: RateLimiter, elevation_limiter: RateLimiter | None = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._limiter = limiter
        self._elevation_limiter = elevation_limiter or limiter
        self.retries = 0

    def _limiter_for(self, url: str) -> RateLimiter:
        """A elevação tem cota própria, mais apertada — e um limitador próprio."""
        return self._elevation_limiter if "elevation" in url else self._limiter

    def _get_json(self, url, params, ttl_s):  # type: ignore[override]
        # Resposta em cache não vai à rede, então não consome cota nem precisa de espera.
        if self._cache.get(_cache_key(url, params)) is not None:
            return super()._get_json(url, params, ttl_s)

        limiter = self._limiter_for(url)
        for attempt, backoff in enumerate((*RETRY_BACKOFF_S, None)):
            limiter.wait()
            try:
                return super()._get_json(url, params, ttl_s)
            except WeatherUnavailableError:
                if backoff is None:
                    raise
                self.retries += 1
                logging.warning(
                    "Open-Meteo recusou (tentativa %d); esperando %.0fs", attempt + 1, backoff
                )
                time.sleep(backoff)
        raise AssertionError("inalcançável")  # pragma: no cover


def load_partial() -> tuple[list[dict], set[str]]:
    """Lê as linhas já prontas. Linha corrompida (morte no meio de um write) é ignorada."""
    if not PARTIAL_PATH.exists():
        return [], set()
    rows: list[dict] = []
    for number, line in enumerate(PARTIAL_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            logging.warning("linha %d do parcial está truncada; ignorada", number)
    return rows, {str(row["proposal_id"]) for row in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--amostra", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--rpm", type=int, default=DEFAULT_RPM, help="req/min do histórico")
    parser.add_argument(
        "--rpm-elevacao", type=int, default=DEFAULT_RPM_ELEVATION, help="req/min da elevação"
    )
    parser.add_argument("--reiniciar", action="store_true", help="ignora o parcial e recomeça")
    parser.add_argument(
        "--consolidar",
        action="store_true",
        help="não busca nada: só escreve o parquet e a ficha a partir do parcial já pronto",
    )
    parser.add_argument("--saida", type=Path, default=DATASET_PATH)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.reiniciar:
        PARTIAL_PATH.unlink(missing_ok=True)

    done_rows, done_ids = load_partial()
    if done_rows:
        print(f"Retomando: {len(done_rows)} linhas já prontas em {PARTIAL_PATH.name}")

    with Session(get_engine()) as session:
        policies = sample_policies(session, n=args.amostra, seed=args.seed)

    if policies.empty:
        print(
            "[erro] nenhuma apólice rotulada no banco. Rode antes scripts/load_psr.py",
            file=sys.stderr,
        )
        return 1

    pending = policies[~policies["proposal_id"].astype(str).isin(done_ids)]
    if args.consolidar:
        # Fechar o dataset com o que já está pronto, sem gastar mais cota. A amostra é embaralhada
        # antes de ser processada, então parar no meio dá um subconjunto sem viés de estrato.
        print(f"Consolidando sem buscar nada: {len(done_rows)} linhas prontas")
        pending = pending.iloc[0:0]
    else:
        print(f"Amostra: {len(policies)} apólices · pendentes: {len(pending)}")

    report = DatasetReport()
    client: ThrottledClient | None = None
    if not pending.empty:
        client = ThrottledClient(
            limiter=RateLimiter(args.rpm),
            elevation_limiter=RateLimiter(args.rpm_elevacao),
            settings=get_settings(),
            cache=TTLCache(),
        )
        started = time.monotonic()

        with PARTIAL_PATH.open("a", encoding="utf-8") as handle:
            counter = {"n": 0}

            def persist(row: dict) -> None:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                handle.flush()
                counter["n"] += 1
                if counter["n"] % 50 == 0:
                    elapsed = time.monotonic() - started
                    rate = counter["n"] / elapsed if elapsed else 0.0
                    restantes = len(pending) - counter["n"]
                    eta_min = restantes / rate / 60 if rate else 0.0
                    print(
                        f"  {counter['n']}/{len(pending)} linhas · "
                        f"{report.api_calls} chamadas · ETA {eta_min:.0f} min",
                        flush=True,
                    )

            _, report = build_features(pending, client, report=report, on_row=persist)

    # O parcial é um cache que sobrevive entre execuções e pode conter linhas de uma amostra
    # anterior maior. O parquet leva **só** as apólices da amostra desta execução, senão um `-n`
    # menor depois de um maior devolveria uma mistura de duas amostragens.
    all_rows, _ = load_partial()
    wanted = set(policies["proposal_id"].astype(str))
    rows = [row for row in all_rows if str(row["proposal_id"]) in wanted]
    if len(rows) != len(all_rows):
        fora = len(all_rows) - len(rows)
        print(f"({fora} linhas do parcial são de outra amostra; ficam de fora do parquet)")
    frame = pd.DataFrame(rows, columns=list(DATASET_COLUMNS))
    if frame.empty:
        print("[erro] nenhuma linha gerada", file=sys.stderr)
        return 1

    save_dataset(frame, args.saida)

    print(f"\n{report.render()}")
    print(f"\n{_display(args.saida)}: {len(frame)} linhas")
    for label in ("target_claim", "target_rain_claim"):
        positives = int(frame[label].sum())
        print(f"  {label}: {positives} positivos ({positives / len(frame):.1%})")

    # A ficha descreve o **dataset**, não só a execução: numa consolidação (ou numa retomada) o
    # relatório desta rodada é quase vazio, mas a composição precisa estar lá do mesmo jeito.
    ficha = {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "linhas": len(frame),
        "amostra_pedida": args.amostra,
        "seed": args.seed,
        "fontes": FONTES,
        "features": [c for c in DATASET_COLUMNS if c not in ("target_claim", "target_rain_claim")],
        "rotulos": {
            "target_claim": {
                "positivos": int(frame["target_claim"].sum()),
                "taxa": round(float(frame["target_claim"].mean()), 4),
            },
            "target_rain_claim": {
                "positivos": int(frame["target_rain_claim"].sum()),
                "taxa": round(float(frame["target_rain_claim"].mean()), 4),
            },
        },
        "composicao": {
            "por_ano": {int(k): int(v) for k, v in frame["policy_year"].value_counts().items()},
            "por_uf": {str(k): int(v) for k, v in frame["state"].value_counts().items()},
            "coordinate_source": {
                str(k): int(v) for k, v in frame["coordinate_source"].value_counts().items()
            },
            "culturas_distintas": int(frame["crop"].nunique()),
        },
        "execucao_atual": report.as_dict(),
    }
    REPORT_PATH.write_text(
        json.dumps(ficha, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8"
    )
    print(f"Ficha em {_display(REPORT_PATH)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

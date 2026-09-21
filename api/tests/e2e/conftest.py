"""Infraestrutura dos testes de ponta a ponta (I6).

Estes testes **usam a rede de verdade**: sobem a API em um processo separado, conectam no
`broker.hivemq.com` e rodam o `scripts/simulate_device.py`. Por isso ficam **fora da CI padrão**,
atrás da marca `e2e` (`uv run pytest -m e2e`).

Três decisões que mantêm a execução determinística no broker público:

- **Prefixo de tópico único por execução** (`agrishield/fiap-sompo-2026-e2e-<8 hex>`). O broker é
  aberto: sem isso, um teste leria a telemetria do Wokwi de outra pessoa (ou do próprio colega
  ao lado) e contaria mensagem que ele não publicou. E, principalmente, nada do que estes testes
  publicam encosta no prefixo da demo.
- **Banco novo a cada execução**, em arquivo temporário: a contagem "enviadas × gravadas" só
  fecha se a tabela começar vazia.
- **`tractor-02`**, e não o `tractor-01` da demo. Ele existe no catálogo da W1 (então as rotas
  respondem 200) e não é o equipamento que vai ao ar na apresentação.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
API_DIR = REPO_ROOT / "api"
SIMULATOR = REPO_ROOT / "scripts" / "simulate_device.py"

# O simulador é um script, não um pacote: para instanciá-lo dentro do teste (medições de tempo
# precisam do relógio do próprio processo) ele entra no `sys.path` pelo caminho.
sys.path.insert(0, str(SIMULATOR.parent))
import simulate_device  # noqa: E402

# O equipamento usado nos cenários (ver docstring do módulo).
DEVICE_ID = "tractor-02"
# Chave de API usada só aqui; a rota de publicação (W4) e a trilha (I5) exigem `X-API-Key`.
API_KEY = "e2e-test-key"

BOOT_TIMEOUT_S = 60.0
# Quanto esperamos o caminho broker → ponte → SQLite fechar. O alvo da W5 é 3 s; a folga existe
# para o teste não quebrar por um soluço do broker público, e o tempo medido é sempre registrado.
INGEST_TIMEOUT_S = 20.0


def db_now() -> str:
    """Marca de tempo no formato em que o SQLModel grava (`UTC sem fuso`), para filtrar linhas.

    Cada teste guarda esta marca antes de publicar e só conta o que chegou depois dela: a mesma
    API e o mesmo equipamento atendem a sessão inteira.
    """
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def free_port() -> int:
    """Porta livre, pedida ao sistema operacional."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until(
    predicate: Callable[[], Any], timeout_s: float, interval_s: float = 0.1
) -> tuple[Any, float]:
    """Espera a condição virar verdadeira. Devolve `(valor, segundos até virar)`.

    Devolve `(None, timeout)` se estourar — quem chama decide se isso é falha.
    """
    started = time.monotonic()
    deadline = started + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value, time.monotonic() - started
        time.sleep(interval_s)
    return None, time.monotonic() - started


@dataclass
class ApiServer:
    """Uma API viva, com banco próprio e prefixo MQTT próprio."""

    base_url: str
    prefix: str
    db_path: Path
    log_path: Path
    process: subprocess.Popen[bytes]
    mqtt_host: str
    mqtt_port: int

    # --- HTTP ---------------------------------------------------------------------------------

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return httpx.get(f"{self.base_url}{path}", timeout=30.0, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"X-API-Key": API_KEY, **kwargs.pop("headers", {})}
        return httpx.post(f"{self.base_url}{path}", headers=headers, timeout=60.0, **kwargs)

    # --- banco --------------------------------------------------------------------------------

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Lê o SQLite da API em modo somente leitura (WAL permite o acesso concorrente)."""
        uri = f"file:{self.db_path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=10.0) as connection:
            connection.row_factory = sqlite3.Row
            return list(connection.execute(sql, params))

    def count(self, table: str, device_id: str = DEVICE_ID) -> int:
        rows = self.query(f"SELECT COUNT(*) AS n FROM {table} WHERE device_id = ?", (device_id,))
        return int(rows[0]["n"])

    def telemetry_since(self, since: str, device_id: str = DEVICE_ID) -> list[sqlite3.Row]:
        """Telemetria gravada a partir de `since` (marca de `db_now()`), em ordem de chegada."""
        return self.query(
            "SELECT * FROM telemetry WHERE device_id = ? AND received_at >= ? ORDER BY id",
            (device_id, since),
        )

    def events_since(self, since: str, device_id: str = DEVICE_ID) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM device_event WHERE device_id = ? AND received_at >= ? ORDER BY id",
            (device_id, since),
        )

    def logs(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")


def start_api(
    tmp_path: Path,
    prefix: str,
    mqtt_host: str = "broker.hivemq.com",
    mqtt_port: int = 1883,
    extra_env: dict[str, str] | None = None,
) -> ApiServer:
    """Sobe `uvicorn app.main:app` num processo à parte e espera ele ficar pronto."""
    port = free_port()
    db_path = tmp_path / "e2e.db"
    log_path = tmp_path / "api.log"

    env = {
        **os.environ,
        "AGRISHIELD_DATABASE_URL": f"sqlite:///{db_path}",
        "AGRISHIELD_MQTT_ENABLED": "true",
        "AGRISHIELD_MQTT_HOST": mqtt_host,
        "AGRISHIELD_MQTT_PORT": str(mqtt_port),
        "AGRISHIELD_MQTT_TOPIC_PREFIX": prefix,
        "AGRISHIELD_API_KEYS": API_KEY,
        "AGRISHIELD_ENVIRONMENT": "e2e",
        # Republicação horária: no teste ela não deve disparar sozinha no meio do cenário.
        "AGRISHIELD_MQTT_PUBLISH_INTERVAL_S": "3600",
        "PYTHONUNBUFFERED": "1",
    }
    env.update(extra_env or {})

    log_file = log_path.open("wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=API_DIR,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    server = ApiServer(
        base_url=f"http://127.0.0.1:{port}/api/v1",
        prefix=prefix,
        db_path=db_path,
        log_path=log_path,
        process=process,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
    )

    def healthy() -> bool:
        if process.poll() is not None:
            raise RuntimeError(f"A API morreu no boot:\n{server.logs()}")
        try:
            return server.get("/health").status_code == 200
        except httpx.HTTPError:
            return False

    ready, elapsed = wait_until(healthy, BOOT_TIMEOUT_S, 0.2)
    if not ready:
        process.kill()
        raise RuntimeError(f"A API não respondeu em {BOOT_TIMEOUT_S:.0f} s:\n{server.logs()}")

    # Só depois de "Assinado" nos três tópicos é que a ponte está de fato ouvindo.
    subscribed, subscribe_s = wait_until(
        lambda: server.logs().count("Assinado:") >= 3, BOOT_TIMEOUT_S, 0.2
    )
    if not subscribed:
        process.kill()
        raise RuntimeError(f"A ponte MQTT não assinou os tópicos:\n{server.logs()}")

    print(
        f"[e2e] API no ar em {elapsed:.1f} s, ponte MQTT assinada em +{subscribe_s:.1f} s "
        f"(prefixo {prefix}, broker {mqtt_host}:{mqtt_port})"
    )
    return server


def stop_api(server: ApiServer) -> None:
    server.process.terminate()
    try:
        server.process.wait(timeout=20)
    except subprocess.TimeoutExpired:  # pragma: no cover — só se o shutdown travar
        server.process.kill()


@pytest.fixture(scope="session")
def topic_prefix() -> str:
    """Prefixo isolado desta execução, para não cruzar com o Wokwi nem com a demo."""
    return f"agrishield/fiap-sompo-2026-e2e-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def api(tmp_path_factory: pytest.TempPathFactory, topic_prefix: str) -> Iterator[ApiServer]:
    """API de verdade, ligada ao broker público, viva durante toda a sessão de testes."""
    server = start_api(tmp_path_factory.mktemp("api"), topic_prefix)
    try:
        yield server
    finally:
        stop_api(server)


@dataclass
class SimulatorRun:
    """O que uma execução do simulador deixou: saída no terminal e resumo em JSON."""

    scenario: str
    command: list[str]
    stdout: str
    returncode: int
    summary: dict[str, Any]

    @property
    def sent(self) -> list[dict[str, Any]]:
        return self.summary["sent"]

    @property
    def valid_telemetry(self) -> list[dict[str, Any]]:
        return [item for item in self.sent if item["valid"] and item["suffix"] == "telemetry"]

    @property
    def command_line(self) -> str:
        return " ".join(self.command)


@pytest.fixture
def run_simulator(api: ApiServer, tmp_path: Path) -> Callable[..., SimulatorRun]:
    """Roda `scripts/simulate_device.py` como processo, do jeito que uma pessoa rodaria."""

    def runner(
        scenario: str, *args: str, device_id: str = DEVICE_ID, timeout_s: float = 300.0
    ) -> SimulatorRun:
        summary_path = tmp_path / f"sim-{scenario}-{uuid.uuid4().hex[:6]}.json"
        command = [
            sys.executable,
            str(SIMULATOR),
            "--scenario",
            scenario,
            "--device-id",
            device_id,
            "--prefix",
            api.prefix,
            "--host",
            api.mqtt_host,
            "--port",
            str(api.mqtt_port),
            "--summary-json",
            str(summary_path),
            # O broker é público e `status`/`config` são retained: cada cenário apaga o que
            # deixou pendurado lá, para a execução seguinte começar limpa.
            "--clean-retained",
            *args,
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_s, cwd=REPO_ROOT
        )
        stdout = completed.stdout + completed.stderr
        print(f"\n$ {' '.join(command)}\n{stdout}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return SimulatorRun(
            scenario=scenario,
            command=command,
            stdout=stdout,
            returncode=completed.returncode,
            summary=summary,
        )

    return runner


@pytest.fixture
def live_device(api: ApiServer) -> Iterator[Any]:
    """Simulador **dentro do processo de teste**, para medir tempo com o mesmo relógio.

    É o que permite dizer "a leitura saiu do dispositivo às 12:00:00,123 e apareceu na API
    12:00:00,4 s depois" sem o atraso de subir e derrubar um subprocesso no meio da conta.
    """
    device = simulate_device.DeviceSimulator(
        device_id=DEVICE_ID,
        host=api.mqtt_host,
        port=api.mqtt_port,
        prefix=api.prefix,
        client_suffix=f"live-{uuid.uuid4().hex[:6]}",
    )
    device.connect()
    try:
        yield device
    finally:
        # Nada de retained pendurado no broker público depois do teste.
        device.clean_retained()
        device.disconnect(announce_offline=False)


# W5 — o `/status` devolve `offline` quando passam mais de 20 s sem telemetria, mesmo com o
# `status` retained dizendo `online`. Os cenários consultam logo depois de publicar, então o
# silêncio é de poucos segundos; a checagem abaixo deixa isso explícito para uma falha aqui não
# ser confundida com defeito de produção.
SILENCE_LIMIT_S = 20.0


def assert_online(api: ApiServer, device_id: str = DEVICE_ID) -> dict[str, Any]:
    """Confere que o painel vê o equipamento como `online`, explicando a regra dos 20 s."""
    response = api.get(f"/devices/{device_id}/status")
    assert response.status_code == 200
    body = response.json()

    silence = body["seconds_since_last_telemetry"]
    assert body["reported_state"] == "online", (
        f"o `status` retained do equipamento não diz `online`: {body}"
    )
    assert silence is not None and silence < SILENCE_LIMIT_S, (
        f"passaram {silence} s desde a última telemetria (o limite da W5 é "
        f"{SILENCE_LIMIT_S:.0f} s): a consulta demorou demais depois do cenário, então este "
        f"resultado não diz nada sobre o sistema. Resposta: {body}"
    )
    assert body["state"] == "online", (
        f"com {silence} s de silêncio e `status` retained `online`, o painel deveria mostrar "
        f"`online`: {body}"
    )
    return body

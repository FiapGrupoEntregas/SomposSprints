"""Queda do **broker** vista pela API: ela reconecta sozinha e volta a gravar (I6).

O cenário `queda` do simulador derruba o *dispositivo*. Aqui derrubamos o caminho até o broker
para o lado da API, que é o outro jeito de a integração cair no mundo real (Wi-Fi da fazenda,
broker público em manutenção).

Como não dá para desligar o `broker.hivemq.com`, a API é apontada para um **proxy TCP local**
que encaminha para ele. Matar o proxy é, para o paho, exatamente a mesma coisa que o broker
sumir: a conexão morre no meio. Depois o proxy volta na mesma porta e o teste observa a API
reassinar os tópicos e voltar a gravar.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.e2e.conftest import (
    INGEST_TIMEOUT_S,
    db_now,
    free_port,
    simulate_device,
    start_api,
    stop_api,
    wait_until,
)

pytestmark = pytest.mark.e2e

DEVICE_ID = "tractor-02"
# `mqtt_reconnect_max_s` padrão é 30 s, mas quem manda no tempo total é o keepalive do paho
# (60 s, padrão): é ele que descobre que a conexão morreu quando não há mais tráfego chegando.
RECONNECT_BUDGET_S = 120.0
UPSTREAM = ("broker.hivemq.com", 1883)


class BrokerProxy:
    """Proxy TCP que dá para matar e ressuscitar na mesma porta."""

    def __init__(self, port: int) -> None:
        self.port = port
        self._server: socket.socket | None = None
        self._connections: list[socket.socket] = []
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self, bind_timeout_s: float = 20.0) -> None:
        """Sobe (ou ressuscita) o proxy na mesma porta.

        A porta pode ficar presa alguns segundos depois de uma queda, com as conexões antigas
        ainda em `TIME_WAIT`. Em vez de falhar o teste por isso, o bind é tentado de novo até o
        prazo — o que se quer medir é a reconexão da API, não o `TIME_WAIT` do Linux.
        """
        deadline = time.time() + bind_timeout_s
        while True:
            server = socket.socket()
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind(("127.0.0.1", self.port))
            except OSError:
                server.close()
                if time.time() >= deadline:
                    raise
                time.sleep(0.5)
                continue
            break
        server.listen(8)
        # `accept` com prazo: é o que deixa a thread perceber o `stop()` e sair sozinha, em vez
        # de ficar presa segurando a porta.
        server.settimeout(0.2)
        self._server = server
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        while self._running and self._server is not None:
            try:
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                upstream = socket.create_connection(UPSTREAM, timeout=10)
            except OSError:
                client.close()
                continue
            self._connections.extend((client, upstream))
            threading.Thread(target=self._pump, args=(client, upstream), daemon=True).start()
            threading.Thread(target=self._pump, args=(upstream, client), daemon=True).start()

    @staticmethod
    def _pump(source: socket.socket, target: socket.socket) -> None:
        try:
            while True:
                data = source.recv(4096)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (source, target):
                with_suppress(sock.close)

    def stop(self) -> None:
        """Derruba o proxy e todas as conexões abertas, sem aviso — como um broker que cai."""
        self._running = False
        if self._thread is not None:
            # Esperar a thread sair antes de fechar o socket é o que libera a porta de verdade:
            # um `accept` bloqueado mantém a porta ocupada mesmo depois do `close()`.
            self._thread.join(timeout=3)
            self._thread = None
        if self._server is not None:
            with_suppress(self._server.close)
            self._server = None
        for sock in self._connections:
            with_suppress(sock.close)
        self._connections.clear()


def with_suppress(action: Callable[[], None]) -> None:
    """Fecha socket ignorando erro: derrubar conexão já morta é o caso normal aqui."""
    with contextlib.suppress(OSError):
        action()


@pytest.fixture
def proxied_api(tmp_path: Path):
    """Uma API só para este teste, falando com o broker através do proxy."""
    proxy = BrokerProxy(free_port())
    proxy.start()
    server = start_api(
        tmp_path,
        prefix=f"agrishield/fiap-sompo-2026-e2e-{uuid.uuid4().hex[:8]}",
        mqtt_host="127.0.0.1",
        mqtt_port=proxy.port,
    )
    try:
        yield server, proxy
    finally:
        stop_api(server)
        proxy.stop()


def test_api_recovers_after_the_broker_goes_down(proxied_api) -> None:
    """Broker cai no meio da coleta; quando volta, a API reassina e grava de novo."""
    api, proxy = proxied_api
    device = simulate_device.DeviceSimulator(
        device_id=DEVICE_ID,
        host=UPSTREAM[0],
        port=UPSTREAM[1],
        prefix=api.prefix,
        client_suffix=f"outage-{uuid.uuid4().hex[:6]}",
    )
    device.connect()
    try:
        since = db_now()
        before = device.publish_telemetry(roll_deg=5.0)
        rows, _ = wait_until(lambda: api.telemetry_since(since) or None, INGEST_TIMEOUT_S)
        assert rows is not None, "a coleta já não funcionava antes da queda"

        subscriptions_before = api.logs().count("Assinado:")
        proxy.stop()
        down_at = time.time()
        print("[e2e] broker derrubado (proxy morto)")

        # Enquanto o broker está fora, o que o dispositivo publica se perde: é QoS 0 e não há
        # ninguém assinando. O que se cobra é que a API **volte**, não que recupere o passado.
        time.sleep(3)
        lost = device.publish_telemetry(roll_deg=6.0)

        proxy.start()
        back_at = time.time()
        print(f"[e2e] broker de volta {back_at - down_at:.1f} s depois")

        # Quanto a API demora só para **perceber** que caiu. Sem tráfego de entrada, quem descobre
        # isso é o keepalive do paho, e esse número é o tempo em que o painel ao vivo fica cego.
        noticed, notice_s = wait_until(
            lambda: "Ponte MQTT desconectada" in api.logs(), RECONNECT_BUDGET_S, 0.2
        )
        if noticed:
            print(f"[e2e] a API percebeu a queda {time.time() - down_at:.1f} s depois dela")
        else:
            print(f"[e2e] a API não registrou a desconexão em {notice_s:.0f} s")

        resubscribed, resubscribe_s = wait_until(
            lambda: api.logs().count("Assinado:") >= subscriptions_before + 3,
            RECONNECT_BUDGET_S,
            0.2,
        )
        assert resubscribed, (
            f"a API não reassinou os tópicos em {RECONNECT_BUDGET_S:.0f} s:\n{api.logs()[-3000:]}"
        )
        print(
            f"[e2e] API reassinou os três tópicos "
            f"{time.time() - back_at:.1f} s depois de o broker voltar "
            f"({time.time() - down_at:.1f} s depois da queda)"
        )

        # O dispositivo continua publicando a cada poucos segundos, como o firmware faz. Repetir
        # em vez de mandar uma única leitura é o comportamento real e evita cravar o teste no
        # instante exato em que o broker termina de restabelecer a assinatura.
        recovered_seq: int | None = None
        recovery_deadline = time.time() + INGEST_TIMEOUT_S
        while recovered_seq is None and time.time() < recovery_deadline:
            sent = device.publish_telemetry(roll_deg=7.0)
            seq = json.loads(sent.payload)["seq"]
            rows_now, _ = wait_until(
                lambda seq=seq: (
                    [row for row in api.telemetry_since(since) if row["seq"] == seq] or None
                ),
                3.0,
                0.1,
            )
            if rows_now:
                recovered_seq = seq

        assert recovered_seq is not None, (
            f"a API não voltou a gravar depois que o broker retornou:\n{api.logs()[-2000:]}"
        )
        print(
            f"[e2e] telemetria nova gravada depois da volta (seq={recovered_seq}, "
            f"{time.time() - back_at:.1f} s depois de o broker voltar)"
        )

        stored_seqs = {row["seq"] for row in api.telemetry_since(since)}
        before_seq = json.loads(before.payload)["seq"]
        lost_seq = json.loads(lost.payload)["seq"]
        assert before_seq in stored_seqs, "a leitura anterior à queda sumiu do banco"
        assert lost_seq not in stored_seqs, (
            "a leitura publicada com o broker fora do ar apareceu no banco; "
            "com QoS 0 ela deveria ter se perdido"
        )
        print(
            f"[e2e] seq gravados: {sorted(stored_seqs)} — a leitura publicada durante a queda "
            f"(seq={lost_seq}) se perdeu, como manda o QoS 0"
        )
    finally:
        device.clean_retained()
        device.disconnect(announce_offline=False)

"""Testes da tarefa periódica que republica o limite (W4).

O ponto central: **o laço não pode morrer**. Se um ciclo falhar por qualquer motivo — abrir a
sessão, ler `device_status`, a Open-Meteo, o broker —, a tarefa tem que registrar e tentar de novo
no ciclo seguinte. Se ela morresse, a API seguiria de pé e ninguém notaria que o limite parou de
ser republicado de hora em hora.
"""

import asyncio

import pytest

from app.core.config import Settings
from app.db import create_db, get_engine
from app.main import publish_limits_periodically
from app.mqtt.publisher import run_publish_cycle, wait_for_connection

# Intervalo zero: o `asyncio.sleep(0)` só devolve o controle, então o teste vê vários ciclos
# sem esperar a 1 h de produção. O tipo da configuração continua `int`, como em produção.
FAST_INTERVAL_S = 0


class FakeConnection:
    """Ponte que só responde se está conectada (é tudo o que a tarefa consulta)."""

    def __init__(self, connected: bool = True) -> None:
        self.is_connected = connected


class CycleSpy:
    """Dublê de `run_publish_cycle`: conta as chamadas e levanta nas que forem pedidas."""

    def __init__(self, raise_on: set[int] | None = None) -> None:
        self.calls = 0
        self._raise_on = raise_on or set()

    def __call__(self, bridge: object, settings: Settings) -> list[str]:
        self.calls += 1
        if self.calls in self._raise_on:
            raise RuntimeError(f"falha proposital no ciclo {self.calls}")
        return []


def fast_settings() -> Settings:
    return Settings(mqtt_enabled=True, mqtt_publish_interval_s=FAST_INTERVAL_S)


async def run_for(task_coroutine, cycles_expected: int, spy: CycleSpy) -> asyncio.Task:
    """Roda a tarefa até ela completar `cycles_expected` ciclos (ou estourar o tempo)."""
    task = asyncio.create_task(task_coroutine)
    for _ in range(500):
        if spy.calls >= cycles_expected:
            break
        await asyncio.sleep(0.01)
    return task


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_a_failing_cycle_does_not_kill_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """O caso que o revisor mediu: uma exceção no ciclo encerrava a tarefa em silêncio."""
    spy = CycleSpy(raise_on={1})
    monkeypatch.setattr("app.main.run_publish_cycle", spy)

    task = await run_for(publish_limits_periodically(FakeConnection(), fast_settings()), 2, spy)

    assert spy.calls >= 2, "a tarefa parou depois da primeira falha"
    assert not task.done(), "a tarefa morreu em vez de seguir para o próximo ciclo"
    await stop(task)


@pytest.mark.anyio
async def test_the_task_keeps_publishing_cycle_after_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = CycleSpy()
    monkeypatch.setattr("app.main.run_publish_cycle", spy)

    task = await run_for(publish_limits_periodically(FakeConnection(), fast_settings()), 3, spy)

    assert spy.calls >= 3
    await stop(task)


@pytest.mark.anyio
async def test_every_cycle_can_fail_and_the_task_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Banco fora do ar a manhã inteira: a tarefa continua tentando, sem derrubar nada."""
    spy = CycleSpy(raise_on=set(range(1, 100)))
    monkeypatch.setattr("app.main.run_publish_cycle", spy)

    task = await run_for(publish_limits_periodically(FakeConnection(), fast_settings()), 3, spy)

    assert spy.calls >= 3
    assert not task.done()
    await stop(task)


@pytest.mark.anyio
async def test_cancelling_stops_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """O `CancelledError` é o único que passa: é o shutdown pedindo para encerrar."""
    spy = CycleSpy()
    monkeypatch.setattr("app.main.run_publish_cycle", spy)

    task = await run_for(publish_limits_periodically(FakeConnection(), fast_settings()), 1, spy)
    await stop(task)

    assert task.cancelled()


@pytest.mark.anyio
async def test_the_task_waits_for_the_connection_before_the_first_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem conexão, `connect_async` ainda não terminou: publicar agora só geraria rc=NO_CONN."""
    spy = CycleSpy()
    monkeypatch.setattr("app.main.run_publish_cycle", spy)
    settings = fast_settings()
    offline = FakeConnection(connected=False)

    task = asyncio.create_task(publish_limits_periodically(offline, settings))
    await asyncio.sleep(0.05)
    assert spy.calls == 0, "publicou antes de a ponte conectar"

    offline.is_connected = True
    for _ in range(200):
        if spy.calls:
            break
        await asyncio.sleep(0.01)

    assert spy.calls >= 1
    await stop(task)


# --- O ciclo que roda dentro da thread ---------------------------------------------------------


def test_the_cycle_opens_its_own_session_and_client() -> None:
    """A sessão e o cliente nascem **dentro** da thread, não no `event loop`.

    Sem nenhum equipamento conhecido o ciclo devolve `[]` antes de tocar a Open-Meteo, então o
    teste continua offline — o que ele prova é que o ciclo se vira sozinho, sem nada vindo de fora.
    """
    create_db(get_engine())

    assert run_publish_cycle(FakeConnection(), fast_settings()) == []


# --- wait_for_connection -------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_connection_returns_true_when_already_connected() -> None:
    assert await wait_for_connection(FakeConnection(connected=True), timeout_s=0.1) is True


@pytest.mark.anyio
async def test_wait_for_connection_gives_up_after_the_timeout() -> None:
    """Sem conexão, a tarefa não fica presa aqui: ela segue e o ciclo apenas registra e adia."""
    assert await wait_for_connection(FakeConnection(connected=False), timeout_s=0.05) is False

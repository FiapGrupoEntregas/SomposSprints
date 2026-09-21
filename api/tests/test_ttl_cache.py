"""Testes do cache com TTL e leitura de valor vencido (I1)."""

import pytest

from app.core import cache as cache_module
from app.core.cache import TTLCache


class FakeClock:
    """Relógio monotônico controlado pelo teste."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(cache_module.time, "monotonic", fake)
    return fake


def test_get_returns_value_before_ttl(clock: FakeClock) -> None:
    cache = TTLCache()
    cache.set("k", {"v": 1}, ttl_s=60)

    clock.now += 59.9

    assert cache.get("k") == {"v": 1}


def test_get_returns_none_exactly_at_ttl(clock: FakeClock) -> None:
    """Na borda exata o valor já é considerado vencido (conservador)."""
    cache = TTLCache()
    cache.set("k", {"v": 1}, ttl_s=60)

    clock.now += 60

    assert cache.get("k") is None


def test_get_returns_none_after_ttl(clock: FakeClock) -> None:
    cache = TTLCache()
    cache.set("k", {"v": 1}, ttl_s=60)

    clock.now += 60.1

    assert cache.get("k") is None


def test_get_stale_returns_expired_value(clock: FakeClock) -> None:
    cache = TTLCache()
    cache.set("k", {"v": 1}, ttl_s=60)

    clock.now += 3_600

    assert cache.get("k") is None
    assert cache.get_stale("k") == {"v": 1}


def test_get_stale_returns_none_for_unknown_key() -> None:
    assert TTLCache().get_stale("nunca-guardado") is None


def test_set_overwrites_and_renews(clock: FakeClock) -> None:
    cache = TTLCache()
    cache.set("k", "antigo", ttl_s=10)
    clock.now += 20
    cache.set("k", "novo", ttl_s=10)

    assert cache.get("k") == "novo"


def test_invalidate_and_clear_remove_even_stale_values() -> None:
    cache = TTLCache()
    cache.set("a", 1, ttl_s=0)
    cache.set("b", 2, ttl_s=0)

    cache.invalidate("a")
    assert cache.get_stale("a") is None
    assert cache.get_stale("b") == 2

    cache.clear()
    assert cache.get_stale("b") is None

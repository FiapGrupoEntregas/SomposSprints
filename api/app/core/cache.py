"""Cache em memória com TTL e leitura de valores vencidos (*stale-if-error*).

Usado pelo cliente da Open-Meteo (I1) para não repetir chamadas externas e para manter a demo de pé
quando a API externa falha: se a chamada der erro e existir um valor vencido, ele é reaproveitado.

Não há cache em disco: o processo da API é reiniciado raramente e a demo dura poucos minutos.
"""

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class _Entry:
    """Valor guardado com o instante (monotônico) em que ele vence."""

    value: Any
    expires_at: float


class TTLCache:
    """Dicionário com validade por chave, seguro para uso concorrente.

    O relógio é `time.monotonic()`, que não anda para trás quando o relógio do sistema é ajustado.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """Devolve o valor se ele ainda estiver dentro do TTL; caso contrário, `None`."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.expires_at <= time.monotonic():
                return None
            return entry.value

    def get_stale(self, key: str) -> Any | None:
        """Devolve o valor mesmo vencido (*stale-if-error*); `None` se nunca foi guardado."""
        with self._lock:
            entry = self._entries.get(key)
            return None if entry is None else entry.value

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        """Guarda um valor que vale por `ttl_s` segundos."""
        with self._lock:
            self._entries[key] = _Entry(value=value, expires_at=time.monotonic() + ttl_s)

    def invalidate(self, key: str) -> None:
        """Remove uma chave (inclusive o valor vencido)."""
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        """Esvazia o cache. Usado entre testes."""
        with self._lock:
            self._entries.clear()

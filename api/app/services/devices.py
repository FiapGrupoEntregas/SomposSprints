"""Estado e série do equipamento para o painel ao vivo (W5).

Funções **puras** sobre o que o repositório (I3) já leu do banco: dizem se o equipamento está
online e reduzem a série ao tamanho que o gráfico aguenta. Quem consulta o banco é a rota.
"""

from datetime import datetime
from typing import TypeVar

from app.models import DeviceStatus, Telemetry
from app.schemas.mqtt import DeviceState

# Feature W5 — sem telemetria por mais que isto, o equipamento é dado como offline.
# O ESP32 publica a cada 5 s (E4), então 20 s aceitam três falhas seguidas antes de acusar queda.
OFFLINE_AFTER_S = 20.0

# Feature W5 — teto de pontos da série; acima disso ela é amostrada, para o gráfico não pesar.
MAX_SERIES_POINTS = 300

T = TypeVar("T")


def resolve_state(
    status: DeviceStatus | None, latest: Telemetry | None, now: datetime
) -> tuple[DeviceState, datetime | None, float | None]:
    """Estado do equipamento agora: `(state, last_seen_at, seconds_since_last_telemetry)`.

    Regra do W5: é `offline` se o **LWT** disser offline **ou** se passar de `OFFLINE_AFTER_S`
    sem telemetria. Os dois sinais importam: o LWT pega a queda limpa da conexão, e o silêncio
    pega o equipamento que travou sem o broker perceber.

    Sem nenhuma telemetria, o equipamento é `offline` mesmo que tenha anunciado `online`: ele
    conectou, mas ainda não provou que está medindo.
    """
    last_seen_at = latest.received_at if latest is not None else None
    silence_s = (now - last_seen_at).total_seconds() if last_seen_at is not None else None

    reported_offline = status is not None and status.state == DeviceState.OFFLINE.value
    silent = silence_s is None or silence_s > OFFLINE_AFTER_S

    state = DeviceState.OFFLINE if reported_offline or silent else DeviceState.ONLINE
    return state, last_seen_at, silence_s


def reported_state(status: DeviceStatus | None) -> DeviceState | None:
    """O que o próprio equipamento anunciou (`status` retained), ou `None` se ele nunca falou."""
    if status is None:
        return None
    return DeviceState(status.state)


def downsample(rows: list[T], max_points: int = MAX_SERIES_POINTS) -> list[T]:
    """Reduz a série a no máximo `max_points`, mantendo a ordem, o primeiro e o **último** ponto.

    O último ponto é o que o painel usa como "agora", então ele nunca pode ser descartado pela
    amostragem.
    """
    if max_points < 1:
        raise ValueError("A série amostrada precisa ter pelo menos 1 ponto.")
    if len(rows) <= max_points:
        return list(rows)

    step = len(rows) / max_points
    sampled = [rows[int(index * step)] for index in range(max_points)]
    if sampled[-1] is not rows[-1]:
        sampled[-1] = rows[-1]
    return sampled

"""Histórico do equipamento — a prévia do Passaporte Digital (W11).

Função **pura** sobre o que a I3 já guarda: `telemetry` e `device_event`. **Nenhuma tabela nova** —
tudo o que um histórico precisa já estava gravado, e a cinco dias do congelamento entregar menos
e íntegro vale mais que um esquema novo.

## Por que isto não é o mesmo que o relatório do equipamento (W12)

Os dois leem as mesmas linhas e compartilham **as funções** de `app/services/reports.py` —
`operating_hours` e `pct_above_limit`. Compartilhar a função, e não só a constante, torna a
concordância **estrutural**: não dá para os dois divergirem sem editar a mesma linha. O que muda
é a pergunta:

- **W12** responde "está piorando?", com uma série **por dia** para o gestor de frota;
- **W11** responde "o que aconteceu com esta máquina?", com um **acumulado do período** e a linha
  do tempo — que é o que acompanha o equipamento na renovação, no sinistro e na revenda.

`tests/test_history.py` ainda compara os dois sobre os mesmos dados — agora como rede de
segurança, não como única defesa: duas respostas que contam a mesma coisa não podem contar
diferente.
"""

import logging
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models import DeviceEvent, Telemetry
from app.schemas.history import DeviceHistory
from app.schemas.mqtt import EventType
from app.services.reports import operating_hours, pct_above_limit

logger = logging.getLogger(__name__)

# W11 — janela padrão e teto do histórico
DEFAULT_HISTORY_DAYS = 7
MAX_HISTORY_DAYS = 90

# W11 — quantos eventos a linha do tempo traz. **Só a linha do tempo**: os contadores do resumo
# saem de uma agregação sobre a janela inteira. A API republica o `config` de hora em hora
# (`mqtt_publish_interval_s`), então uma janela de 7 dias já passa de 150 `limit_applied` — contar
# a lista truncada faria os totais pararem em 100 em silêncio e divergirem do relatório (W12).
MAX_TIMELINE_EVENTS = 100

DECIMALS = 1

ROADMAP_NOTE = (
    "Prévia do Passaporte Digital: por enquanto é o histórico do período, sobre o que o "
    "equipamento publicou. Score comportamental, portabilidade na revenda e assinatura digital "
    "do registro ficam no roadmap."
)


def count_events(events: Iterable[DeviceEvent]) -> dict[str, int]:
    """Quantos eventos de cada tipo, a partir de uma lista já em memória.

    Contraparte pura da agregação que `device_history` faz no banco: serve para testar `summarize`
    sem sessão e para quem já tem os eventos carregados.
    """
    counts: dict[str, int] = {}
    for event in events:
        counts[event.type] = counts.get(event.type, 0) + 1
    return counts


def summarize(
    readings: list[Telemetry],
    event_counts: Mapping[str, int],
    device_id: str,
    farm_id: str,
    days: int,
) -> DeviceHistory:
    """Monta o histórico a partir das linhas já lidas. Função pura, sem banco e sem relógio.

    Recebe **os contadores por tipo**, não a lista de eventos, justamente porque a lista que vai
    para a linha do tempo é truncada em `MAX_TIMELINE_EVENTS` e contar por ela daria totais
    errados (ver o comentário da constante).
    """
    received = [row.received_at for row in readings]

    return DeviceHistory(
        device_id=device_id,
        farm_id=farm_id,
        days=days,
        first_seen_at=min(received) if received else None,
        last_seen_at=max(received) if received else None,
        readings=len(readings),
        operating_hours=operating_hours(len(readings)),
        max_roll_deg=_max_absolute(row.roll_deg for row in readings),
        max_pitch_deg=_max_absolute(row.pitch_deg for row in readings),
        pct_time_above_limit=pct_above_limit(readings),
        alerts=event_counts.get(EventType.TILT_ALERT.value, 0),
        rollovers=event_counts.get(EventType.ROLLOVER.value, 0),
        incident_reports=event_counts.get(EventType.INCIDENT_REPORT.value, 0),
        limits_applied=event_counts.get(EventType.LIMIT_APPLIED.value, 0),
        timeline=[],
        roadmap_note=ROADMAP_NOTE,
    )


def device_history(
    session: Session,
    device_id: str,
    farm_id: str,
    days: int = DEFAULT_HISTORY_DAYS,
    now: datetime | None = None,
) -> tuple[DeviceHistory, list[DeviceEvent]]:
    """Histórico do equipamento e os eventos brutos da linha do tempo (W11).

    Devolve os eventos junto porque quem monta a resposta HTTP já sabe convertê-los
    (`routes/devices.py::_to_event_response`), e duplicar essa conversão aqui criaria dois
    formatos para o mesmo evento.

    São **duas consultas** sobre `device_event`: uma agregação por tipo na janela inteira, que
    alimenta os contadores, e uma lista limitada, que alimenta a linha do tempo. Separá-las é o
    que mantém os totais de acordo com o relatório do equipamento (W12), que também conta tudo.
    """
    reference = now if now is not None else datetime.utcnow()
    cutoff = reference - timedelta(days=days)

    readings = list(
        session.exec(
            select(Telemetry)
            .where(Telemetry.device_id == device_id, col(Telemetry.received_at) >= cutoff)
            .order_by(col(Telemetry.received_at))
        )
    )
    event_filters = (DeviceEvent.device_id == device_id, col(DeviceEvent.received_at) >= cutoff)

    # Contadores: a janela inteira, agregada no banco — nenhum teto.
    event_counts = {
        event_type: total
        for event_type, total in session.exec(
            select(DeviceEvent.type, func.count())  # type: ignore[call-overload]
            .where(*event_filters)
            .group_by(col(DeviceEvent.type))
        )
    }

    # Linha do tempo: os mais recentes, com teto — é o que a tela mostra.
    events = list(
        session.exec(
            select(DeviceEvent)
            .where(*event_filters)
            .order_by(col(DeviceEvent.received_at).desc(), col(DeviceEvent.id).desc())
            .limit(MAX_TIMELINE_EVENTS)
        )
    )

    return summarize(readings, event_counts, device_id, farm_id, days), events


def _max_absolute(values) -> float | None:  # type: ignore[no-untyped-def]
    """Maior valor em módulo, arredondado — ou `None` quando não há leitura."""
    absolutes = [abs(value) for value in values]
    return round(max(absolutes), DECIMALS) if absolutes else None

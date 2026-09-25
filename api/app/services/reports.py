"""Relatórios e tendências de risco (W12).

Três recortes, um por perfil de usuário — é o entregável 5 do enunciado ("relatórios com
tendências por equipamento, região ou tipo de operação"):

| Recorte | Fonte | Para quem |
|---|---|---|
| Equipamento | telemetria e eventos gravados (I3) | gestor de frota e manutenção |
| Região | **1,5 milhão de apólices reais** do PSR (D1) + relevo (W8) | analista da seguradora |
| Cultura | as mesmas apólices, agrupadas por cultura e causa | subscrição e produto |

## Dois cuidados que valem mais que o código

1. **Índices.** A tabela `policy` tem 1.525.473 linhas. Toda consulta daqui filtra por
   `(state, policy_year)` ou agrupa por `event_category`, que são exatamente os índices que a D1
   criou. Uma varredura completa levaria segundos e travaria a demo.
2. **LGPD.** Nenhuma consulta seleciona `proposal_id`. Ele é chave de junção de volta ao CSV
   público do PSR, que traz o nome do segurado (ver o resíduo documentado em `api/README.md`);
   num relatório agregado ele não acrescenta nada e só aumentaria a exposição.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models import DeviceEvent, Policy, Telemetry
from app.schemas.mqtt import AlertLevel, EventType
from app.schemas.report import (
    CropReport,
    CropShare,
    EquipmentDay,
    EquipmentReport,
    EventShare,
    FarmProfileSummary,
    MunicipalityShare,
    RegionReport,
)

logger = logging.getLogger(__name__)

# contrato-mqtt (E4) — o ESP32 publica telemetria a cada 5 s; é daí que sai a estimativa de horas
TELEMETRY_INTERVAL_S = 5

# D1 — as apólices sem sinistro recebem esta categoria na ingestão
NO_CLAIM_CATEGORY = "sem_sinistro"

# W12 — níveis de alerta que contam como "tempo acima do limite"
ABOVE_LIMIT_LEVELS = frozenset({AlertLevel.RED.value, AlertLevel.ROLLOVER.value})

# W12 — eventos que contam como alerta no relatório do equipamento
ALERT_EVENT_TYPES = frozenset({EventType.TILT_ALERT.value, EventType.ROLLOVER.value})

# W12 — quantos itens entram em cada ranking
TOP_ITEMS = 10

DECIMALS = 1
MONEY_DECIMALS = 2

EQUIPMENT_PURPOSE = (
    "Para o gestor de frota e a manutenção: mostra quanto a máquina operou, quanto tempo ela "
    "passou acima do limite seguro e se isso está piorando — apoia a decisão de treinar o "
    "operador, remanejar a máquina ou revisar o equipamento."
)
REGION_PURPOSE = (
    "Para o analista da seguradora: mostra o histórico real de sinistros da região (PSR/SISSER) "
    "ao lado do perfil de terreno das fazendas — apoia a decisão de aceitar, precificar e "
    "priorizar inspeção por município."
)
CROP_PURPOSE = (
    "Para subscrição e produto: mostra quais culturas e quais causas concentram sinistro — "
    "apoia a decisão de onde criar cobertura, franquia diferenciada e ação de prevenção. "
    "Este recorte vem da carteira agrícola do PSR/SISSER; não representa o tipo de operação nem "
    "o risco de acidente de uma máquina."
)


def equipment_trend(
    session: Session, device_id: str, farm_id: str, days: int = 7, now: datetime | None = None
) -> EquipmentReport:
    """Tendência do equipamento nos últimos `days` dias (W12).

    As horas operando são **estimadas** a partir da contagem de leituras (uma a cada 5 s, E4):
    é o melhor que dá para dizer sem registrar liga/desliga no firmware, e o relatório não
    finge precisão que não tem.
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
    events = list(
        session.exec(
            select(DeviceEvent)
            .where(DeviceEvent.device_id == device_id, col(DeviceEvent.received_at) >= cutoff)
            .order_by(col(DeviceEvent.received_at))
        )
    )

    by_day: dict[date, list[Telemetry]] = defaultdict(list)
    for row in readings:
        by_day[row.received_at.date()].append(row)

    alerts_by_day: dict[date, int] = defaultdict(int)
    for event in events:
        if event.type in ALERT_EVENT_TYPES:
            alerts_by_day[event.received_at.date()] += 1

    trend = [
        EquipmentDay(
            date=day,
            readings=len(rows),
            operating_hours=operating_hours(len(rows)),
            pct_time_above_limit=pct_above_limit(rows),
            alerts=alerts_by_day.get(day, 0),
            max_roll_deg=round(max(abs(row.roll_deg) for row in rows), DECIMALS) if rows else None,
        )
        for day, rows in sorted(by_day.items())
    ]

    return EquipmentReport(
        device_id=device_id,
        farm_id=farm_id,
        days=days,
        purpose=EQUIPMENT_PURPOSE,
        readings=len(readings),
        operating_hours=operating_hours(len(readings)),
        pct_time_above_limit=pct_above_limit(readings),
        alerts=sum(1 for event in events if event.type in ALERT_EVENT_TYPES),
        rollovers=sum(1 for event in events if event.type == EventType.ROLLOVER.value),
        trend=trend,
    )


def region_summary(
    session: Session,
    state: str,
    from_year: int | None = None,
    to_year: int | None = None,
    demo_farms: list[FarmProfileSummary] | None = None,
) -> RegionReport:
    """Sinistros reais do PSR na UF, com o perfil de terreno das fazendas de demonstração (W12).

    Usa o índice `(state, policy_year)` criado pela D1 — sem ele, 1,5 milhão de linhas viram
    varredura completa e a demo trava.
    """
    filters = _policy_filters(state=state, from_year=from_year, to_year=to_year)

    policies, claims, indemnity = _totals(session, filters)
    events = _event_shares(session, filters, claims)
    municipalities = _municipality_shares(session, filters)

    return RegionReport(
        state=state,
        from_year=from_year,
        to_year=to_year,
        purpose=REGION_PURPOSE,
        policies=policies,
        claims=claims,
        claim_rate_pct=_rate(claims, policies),
        indemnity_total=round(indemnity, MONEY_DECIMALS),
        indemnity_mean=round(indemnity / claims, MONEY_DECIMALS) if claims else 0.0,
        top_events=events,
        top_municipalities=municipalities,
        demo_farms=demo_farms or [],
    )


def crop_summary(
    session: Session,
    from_year: int | None = None,
    to_year: int | None = None,
    state: str | None = None,
) -> CropReport:
    """Taxa de sinistro por cultura e por causa, a partir do PSR (W12)."""
    filters = _policy_filters(state=state, from_year=from_year, to_year=to_year)

    policies, claims, _ = _totals(session, filters)
    top_events = _top_event_by_crop(session, filters)

    statement = (
        select(
            Policy.crop,
            func.count().label("policies"),
            func.sum(_is_claim()).label("claims"),
            func.sum(func.coalesce(Policy.indemnity_value, 0.0)).label("indemnity"),
        )
        .where(*filters)
        .group_by(col(Policy.crop))
        .order_by(func.count().desc())
        .limit(TOP_ITEMS)
    )

    crops = [
        CropShare(
            crop=crop or "(não informada)",
            policies=int(crop_policies),
            claims=int(crop_claims or 0),
            claim_rate_pct=_rate(int(crop_claims or 0), int(crop_policies)),
            indemnity_total=round(float(indemnity or 0.0), MONEY_DECIMALS),
            top_event=top_events.get(crop),
        )
        for crop, crop_policies, crop_claims, indemnity in session.exec(statement)
    ]
    crops.sort(key=lambda item: item.claim_rate_pct, reverse=True)

    return CropReport(
        from_year=from_year,
        to_year=to_year,
        state=state,
        purpose=CROP_PURPOSE,
        policies=policies,
        claims=claims,
        claim_rate_pct=_rate(claims, policies),
        crops=crops,
        events=_event_shares(session, filters, claims),
    )


# --- internos ------------------------------------------------------------------------------------


def operating_hours(readings: int) -> float:
    """Horas operando, **estimadas** pela contagem de leituras (uma a cada 5 s, E4).

    Pública porque o histórico do equipamento (W11) usa a mesma conta. Compartilhar a **função**,
    e não só a constante, é o que torna a concordância entre os dois **estrutural**: não dá para
    divergir sem editar esta linha. O teste cruzado vira rede de segurança, não única defesa.
    """
    return round(readings * TELEMETRY_INTERVAL_S / 3600, 2)


def count_above_limit(rows: list[Telemetry]) -> int:
    """Quantas leituras estavam em alerta 🔴 ou capotamento. Compartilhada com a W11."""
    return sum(1 for row in rows if row.alert_level in ABOVE_LIMIT_LEVELS)


def pct_above_limit(rows: list[Telemetry]) -> float:
    """% das leituras em alerta 🔴 ou capotamento, arredondado. Compartilhada com a W11.

    Pública pelo mesmo motivo de `operating_hours`: o histórico do equipamento (W11) publica o
    mesmo número que o relatório (W12), e compartilhar a **função** é o que impede os dois de
    divergirem sem que alguém edite esta linha.
    """
    if not rows:
        return 0.0
    return round(100.0 * count_above_limit(rows) / len(rows), DECIMALS)


def _rate(part: int, total: int) -> float:
    return round(100.0 * part / total, DECIMALS) if total else 0.0


def _is_claim():  # type: ignore[no-untyped-def]
    """1 quando a apólice teve sinistro, 0 quando não teve (D1 usa `sem_sinistro`).

    Devolve a expressão por linha; quem soma é a consulta.
    """
    return func.iif(Policy.event_category != NO_CLAIM_CATEGORY, 1, 0)


def _policy_filters(
    state: str | None = None, from_year: int | None = None, to_year: int | None = None
) -> list:  # type: ignore[type-arg]
    """Filtros na ordem do índice `(state, policy_year)` da D1."""
    filters = []
    if state:
        filters.append(Policy.state == state.upper())
    if from_year is not None:
        filters.append(col(Policy.policy_year) >= from_year)
    if to_year is not None:
        filters.append(col(Policy.policy_year) <= to_year)
    return filters


def _totals(session: Session, filters: list) -> tuple[int, int, float]:  # type: ignore[type-arg]
    """`(apólices, sinistros, indenização total)` do recorte, numa única varredura."""
    statement = select(
        func.count(),
        func.sum(_is_claim()),
        func.sum(func.coalesce(Policy.indemnity_value, 0.0)),
    ).where(*filters)
    policies, claims, indemnity = session.exec(statement).one()
    return int(policies or 0), int(claims or 0), float(indemnity or 0.0)


def _event_shares(session: Session, filters: list, claims: int) -> list[EventShare]:  # type: ignore[type-arg]
    """Causas de sinistro do recorte. `sem_sinistro` fica de fora: não é causa de nada."""
    statement = (
        select(
            Policy.event_category,
            func.count().label("claims"),
            func.sum(func.coalesce(Policy.indemnity_value, 0.0)).label("indemnity"),
        )
        .where(*filters, Policy.event_category != NO_CLAIM_CATEGORY)
        .group_by(col(Policy.event_category))
        .order_by(func.count().desc())
        .limit(TOP_ITEMS)
    )
    return [
        EventShare(
            event_category=category,
            claims=int(count),
            pct_of_claims=_rate(int(count), claims),
            indemnity_total=round(float(indemnity or 0.0), MONEY_DECIMALS),
        )
        for category, count, indemnity in session.exec(statement)
    ]


def _municipality_shares(session: Session, filters: list) -> list[MunicipalityShare]:  # type: ignore[type-arg]
    """Municípios com mais sinistros no recorte, do maior para o menor."""
    statement = (
        select(
            Policy.municipality,
            func.count().label("policies"),
            func.sum(_is_claim()).label("claims"),
            func.sum(func.coalesce(Policy.indemnity_value, 0.0)).label("indemnity"),
        )
        .where(*filters)
        .group_by(col(Policy.municipality))
        .order_by(func.sum(_is_claim()).desc())
        .limit(TOP_ITEMS)
    )
    return [
        MunicipalityShare(
            municipality=municipality or "(não informado)",
            policies=int(policies),
            claims=int(claims or 0),
            claim_rate_pct=_rate(int(claims or 0), int(policies)),
            indemnity_total=round(float(indemnity or 0.0), MONEY_DECIMALS),
        )
        for municipality, policies, claims, indemnity in session.exec(statement)
    ]


def _top_event_by_crop(session: Session, filters: list) -> dict[str | None, str]:  # type: ignore[type-arg]
    """Causa mais frequente de **cada** cultura, numa consulta só.

    Uma consulta por cultura seria um N+1 sobre 1,5 milhão de linhas: medimos 2,8 s contra 0,6 s
    agrupando por `(crop, event_category)` de uma vez e escolhendo o topo em memória.
    """
    statement = (
        select(Policy.crop, Policy.event_category, func.count().label("claims"))
        .where(*filters, Policy.event_category != NO_CLAIM_CATEGORY)
        .group_by(col(Policy.crop), col(Policy.event_category))
        .order_by(func.count().desc())
    )

    top: dict[str | None, str] = {}
    for crop, category, _count in session.exec(statement):
        # A consulta já vem do mais frequente para o menos: o primeiro de cada cultura vence.
        top.setdefault(crop, category)
    return top

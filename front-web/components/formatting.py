"""Rótulos e formatação compartilhados pelas páginas.

Só apresentação: os valores (nível, estado do solo, limite) vêm prontos da API.
"""

import datetime as dt
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")

WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")

# Cores dos níveis: as mesmas em todo o projeto (document/padroes-de-codigo.md).
LEVEL_STYLE = {
    "green": ("🟢", "Baixo", "#2E7D32"),
    "yellow": ("🟡", "Atenção", "#F9A825"),
    "red": ("🔴", "Alto", "#C62828"),
}
UNKNOWN_LEVEL = ("⚪", "Não informado", "#9E9E9E")

SOIL_LABELS = {"dry": "solo seco", "moist": "solo úmido", "saturated": "solo encharcado"}


def num(value: float, decimals: int = 1) -> str:
    """Número no formato brasileiro (vírgula decimal)."""
    return f"{value:.{decimals}f}".replace(".", ",")


def short_date(iso_date: str) -> str:
    """'2026-09-22' → 'ter 22/09'."""
    try:
        day = dt.date.fromisoformat(iso_date)
    except ValueError:
        return iso_date
    return f"{WEEKDAYS[day.weekday()]} {day.day:02d}/{day.month:02d}"


def level_style(level: str) -> tuple[str, str, str]:
    """Emoji, rótulo e cor de um nível de risco."""
    return LEVEL_STYLE.get(level, UNKNOWN_LEVEL)


def soil_label(soil_state: str) -> str:
    return SOIL_LABELS.get(soil_state, soil_state)


def parse_utc(timestamp: str | None) -> dt.datetime | None:
    """Converte um instante da API em datetime com fuso.

    A API grava em UTC; quando o texto vem sem fuso, é UTC mesmo.
    """
    if not timestamp:
        return None
    try:
        moment = dt.datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return moment.replace(tzinfo=dt.UTC) if moment.tzinfo is None else moment


def local_time(timestamp: str | None) -> str:
    """Instante da API no horário de Brasília, como 'HH:MM:SS'."""
    moment = parse_utc(timestamp)
    return moment.astimezone(LOCAL_TZ).strftime("%H:%M:%S") if moment else "—"


def seconds_since(timestamp: str | None, now: dt.datetime | None = None) -> float | None:
    """Quantos segundos se passaram desde o instante informado."""
    moment = parse_utc(timestamp)
    if moment is None:
        return None
    reference = now or dt.datetime.now(dt.UTC)
    return (reference - moment).total_seconds()


def brl(value: float) -> str:
    """Valor em reais, abreviado quando é grande: R$ 5,44 bi · R$ 112,3 mil · R$ 980,00."""
    if value >= 1_000_000_000:
        return f"R$ {num(value / 1_000_000_000, 2)} bi"
    if value >= 1_000_000:
        return f"R$ {num(value / 1_000_000, 1)} mi"
    if value >= 1_000:
        return f"R$ {num(value / 1_000, 1)} mil"
    return f"R$ {num(value, 2)}"

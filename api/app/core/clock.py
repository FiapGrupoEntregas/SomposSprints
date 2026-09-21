"""Relógio local do projeto.

Toda data e hora de negócio do AgriShield está no fuso das fazendas (`America/Sao_Paulo`), que é
também o fuso pedido à Open-Meteo em `app/clients/open_meteo.py` (regras-de-risco §2). Concentrar o
"agora" aqui deixa os services puros: quem precisa de uma data a recebe por parâmetro e os testes
passam uma data fixa, sem depender do dia em que rodam.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

# regras-de-risco §2 — fuso das fazendas e da previsão horária
LOCAL_TIMEZONE_NAME = "America/Sao_Paulo"
LOCAL_TIMEZONE = ZoneInfo(LOCAL_TIMEZONE_NAME)


def now_local() -> datetime:
    """Momento atual no fuso das fazendas, com `tzinfo` preenchido."""
    return datetime.now(LOCAL_TIMEZONE)


def today_local() -> date:
    """Data de hoje no fuso das fazendas."""
    return now_local().date()

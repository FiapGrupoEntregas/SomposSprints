"""Janelas seguras de operação e recomendações em texto (W6).

Duas funções puras, uma por metade da feature:

- `safe_windows(hourly, day)` — as horas em que dá para operar, conforme
  [regras-de-risco §7](../../../document/regras-de-risco.md#7-janela-segura-de-operação-w6);
- `build_messages(day_risk, terrain)` — as frases, montadas por **template com números**. Nada de
  IA: a mesma situação tem que produzir sempre a mesma orientação.

⚠️ **Janela segura não é área liberada.** O §7 é explícito: as áreas 🔴 de capotamento continuam
proibidas mesmo dentro da janela. Quando o dia tem célula 🔴, a última frase diz isso ao operador,
porque é exatamente o mal-entendido que provoca acidente.
"""

from collections import Counter
from datetime import date, time

from app.clients.open_meteo import OpenMeteoClient
from app.schemas.farm import Farm
from app.schemas.recommendation import DayRecommendation, TimeWindow
from app.schemas.risk import DayRisk, Hazard, RiskLevel, Scenario
from app.schemas.terrain import AspectLabel, TerrainResponse
from app.schemas.weather import HourlyWeather
from app.services import risk as risk_service
from app.services.risk import LEVEL_SEVERITY, SOIL_STATE_LABEL, format_number
from app.services.weather import THUNDERSTORM_WEATHER_CODES

# regras-de-risco §7 — a janela de operação vai das 06h às 18h, intervalo meio aberto: a última
# hora cheia considerada é a das 17h, que termina exatamente às 18h.
WORK_START_HOUR = 6
WORK_END_HOUR = 18

# regras-de-risco §7 — uma hora é segura abaixo destes valores e sem código de tempestade
SAFE_RAIN_MM = 0.5
SAFE_GUST_KMH = 45.0

# regras-de-risco §7 — janelas mais curtas que isto não servem para operar
MIN_WINDOW_HOURS = 2

# regras-de-risco §7 — a recomendação vale para **hoje e amanhã**, e só
MIN_RECOMMENDATION_DAYS = 1
MAX_RECOMMENDATION_DAYS = 2

# W6 — direção da encosta por extenso, a partir do rótulo de 8 direções do relevo (§1)
ASPECT_NAME: dict[AspectLabel, str] = {
    AspectLabel.N: "norte",
    AspectLabel.NE: "nordeste",
    AspectLabel.L: "leste",
    AspectLabel.SE: "sudeste",
    AspectLabel.S: "sul",
    AspectLabel.SO: "sudoeste",
    AspectLabel.O: "oeste",
    AspectLabel.NO: "noroeste",
}

NO_RESTRICTIONS_MESSAGE = "Sem restrições de relevo e clima para hoje."
RED_AREAS_STILL_FORBIDDEN_MESSAGE = (
    "As janelas valem só para as áreas liberadas: as áreas em vermelho do mapa seguem "
    "proibidas mesmo dentro delas."
)


def is_safe_hour(rain_mm: float | None, gust_kmh: float | None, weather_code: int | None) -> bool:
    """Uma hora é segura com chuva < 0,5 mm, rajada < 45 km/h e sem tempestade (§7).

    Indicador **ausente conta como inseguro**: numa recomendação que o operador vai seguir, a
    falta de dado não pode virar permissão.
    """
    if rain_mm is None or gust_kmh is None or weather_code is None:
        return False
    if rain_mm >= SAFE_RAIN_MM or gust_kmh >= SAFE_GUST_KMH:
        return False
    return weather_code not in THUNDERSTORM_WEATHER_CODES


def safe_windows(hourly: HourlyWeather, day: date) -> list[TimeWindow]:
    """Janelas seguras daquele dia, com pelo menos 2 h, entre 06h e 18h (§7)."""
    safe_hours = sorted(
        moment.hour
        for index, moment in enumerate(hourly.time)
        if moment.date() == day
        and WORK_START_HOUR <= moment.hour < WORK_END_HOUR
        and is_safe_hour(
            hourly.precipitation[index],
            hourly.wind_gusts_10m[index],
            hourly.weather_code[index],
        )
    )

    windows: list[TimeWindow] = []
    for start, end in _consecutive_runs(safe_hours):
        # A hora cheia `end` termina em `end + 1`; com o teto em 17h, o fim máximo é 18h.
        window = TimeWindow(start=time(hour=start), end=time(hour=end + 1))
        if window.hours >= MIN_WINDOW_HOURS:
            windows.append(window)
    return windows


def build_messages(day_risk: DayRisk, terrain: TerrainResponse) -> list[str]:
    """Frases do dia, da mais grave para a menos (W6).

    Cada perigo vira uma frase de template com os números daquele dia — **em 🔴 e em 🟡**. O
    documento define 🟡 como "operar com cuidado, com velocidade reduzida e evitando manobras na
    encosta": isso é uma restrição, e o operador precisa lê-la.

    O último recurso é uma frase **genérica de atenção**, e não "sem restrições". Se um perigo
    futuro entrar em `HAZARDS` sem ganhar template aqui, o pior que acontece é o operador receber
    um aviso vago — nunca o contrário, que seria a tela dizer "pode operar" numa fazenda amarela.
    """
    attention_cells = _cells_at_least(day_risk, RiskLevel.YELLOW)
    red_cells = _cells_with(day_risk, RiskLevel.RED)
    if not attention_cells:
        return [NO_RESTRICTIONS_MESSAGE]

    messages: list[str] = []
    messages.extend(_rollover_messages(day_risk, terrain))

    bogging_message = _bogging_message(day_risk)
    if bogging_message is not None:
        messages.append(bogging_message)

    if _cells_with_hazard(day_risk, Hazard.LIGHTNING, RiskLevel.RED) or _cells_with_hazard(
        day_risk, Hazard.LIGHTNING, RiskLevel.YELLOW
    ):
        messages.append(
            "Previsão de tempestade: suspenda as atividades em áreas abertas e topos de morro."
        )

    messages.extend(_wind_messages(day_risk))

    if _cells_with_hazard(day_risk, Hazard.FIRE, RiskLevel.RED) or _cells_with_hazard(
        day_risk, Hazard.FIRE, RiskLevel.YELLOW
    ):
        messages.append(
            "Condição de incêndio (regra dos 30): redobre a atenção com a colheitadeira e "
            "deixe o aceiro pronto."
        )

    if not messages:
        # Há célula 🟡 ou 🔴, mas nenhum perigo com template: avisa genericamente em vez de
        # afirmar que o dia está livre.
        messages.append(_generic_attention_message(day_risk))

    if red_cells:
        messages.append(RED_AREAS_STILL_FORBIDDEN_MESSAGE)

    return messages


def _rollover_messages(day_risk: DayRisk, terrain: TerrainResponse) -> list[str]:
    """Capotamento: 🔴 proíbe a encosta, 🟡 manda reduzir a velocidade (escala de níveis)."""
    limit = format_number(day_risk.tilt_limit_deg)
    soil = SOIL_STATE_LABEL[day_risk.soil_state]

    red = _cells_with_hazard(day_risk, Hazard.ROLLOVER, RiskLevel.RED)
    if red:
        where = _slope_direction(red, terrain)
        return [f"Evite operar máquinas {where} (inclinação acima de {limit}°, {soil})."]

    yellow = _cells_with_hazard(day_risk, Hazard.ROLLOVER, RiskLevel.YELLOW)
    if yellow:
        where = _slope_direction(yellow, terrain)
        return [
            f"Reduza a velocidade e evite manobras {where}: a inclinação está perto do limite "
            f"de {limit}° ({soil})."
        ]
    return []


def _wind_messages(day_risk: DayRisk) -> list[str]:
    """Vento: 🔴 nas áreas expostas, 🟡 como atenção com pulverização."""
    if day_risk.gust_max_kmh is None:
        return []

    gust = format_number(day_risk.gust_max_kmh)
    if _cells_with_hazard(day_risk, Hazard.WIND, RiskLevel.RED):
        return [
            f"Rajadas de até {gust} km/h: evite pulverização e máquinas altas nas áreas expostas."
        ]
    if _cells_with_hazard(day_risk, Hazard.WIND, RiskLevel.YELLOW):
        return [f"Rajadas de até {gust} km/h: atenção com pulverização e máquinas altas."]
    return []


def _slope_direction(cells: list[tuple[int, int]], terrain: TerrainResponse) -> str:
    """ "na encosta sul", ou um genérico quando o relevo não identifica uma direção dominante."""
    direction = _dominant_aspect(cells, terrain)
    return f"na encosta {direction}" if direction else "nas encostas mais íngremes"


def _generic_attention_message(day_risk: DayRisk) -> str:
    """Rede de segurança: nomeia os perigos do dia mesmo sem template específico."""
    hazards = sorted(
        {
            reason.hazard.value
            for cell in day_risk.cells
            for reason in cell.reasons
            if reason.level in {RiskLevel.YELLOW, RiskLevel.RED}
        }
    )
    listed = ", ".join(hazards) if hazards else "condições adversas"
    return (
        f"Opere com atenção: há áreas em alerta hoje ({listed}). "
        "Veja os motivos célula a célula no mapa."
    )


def build_day_recommendation(
    day_risk: DayRisk, terrain: TerrainResponse, hourly: HourlyWeather
) -> DayRecommendation:
    """Junta as janelas e as frases de um dia (W6). Função pura."""
    return DayRecommendation(
        date=day_risk.date,
        windows=safe_windows(hourly, day_risk.date),
        messages=build_messages(day_risk, terrain),
    )


def _bogging_message(day_risk: DayRisk) -> str | None:
    """Frase do atolamento, citando **as duas causas** quando as duas estão presentes.

    O perigo (§5.2) dispara por baixada encharcada **ou** por chuva de 50 mm no dia. Quando o dia
    tem as duas coisas, a recomendação diz as duas — foi o que a revisão da W3 pediu, porque o
    motivo da célula sozinho só citava a baixada.
    """
    if not _cells_with_hazard(day_risk, Hazard.BOGGING, RiskLevel.RED) and not _cells_with_hazard(
        day_risk, Hazard.BOGGING, RiskLevel.YELLOW
    ):
        return None

    heavy_rain_today = day_risk.rain_mm >= 50.0
    if heavy_rain_today:
        return (
            f"Risco de atolamento: {format_number(day_risk.rain_mm)} mm de chuva no dia e baixadas "
            f"encharcadas ({format_number(day_risk.rain_72h_mm)} mm em 72 h). Evite tráfego pesado."
        )
    return "Risco de atolamento nas baixadas: evite tráfego pesado."


def _cells_with(day_risk: DayRisk, level: RiskLevel) -> list[tuple[int, int]]:
    """Células exatamente naquele nível."""
    return [(cell.row, cell.col) for cell in day_risk.cells if cell.level is level]


def _cells_at_least(day_risk: DayRisk, level: RiskLevel) -> list[tuple[int, int]]:
    """Células **naquele nível ou pior**.

    Era a distinção que faltava: perguntar só por 🔴 e por 🟡 separadamente deixou um dia 100%
    amarelo cair no texto de dia limpo.
    """
    floor = LEVEL_SEVERITY[level]
    return [(cell.row, cell.col) for cell in day_risk.cells if LEVEL_SEVERITY[cell.level] >= floor]


def _cells_with_hazard(
    day_risk: DayRisk, hazard: Hazard, level: RiskLevel
) -> list[tuple[int, int]]:
    """Células em que aquele perigo atingiu aquele nível."""
    return [
        (cell.row, cell.col)
        for cell in day_risk.cells
        if any(reason.hazard is hazard and reason.level is level for reason in cell.reasons)
    ]


def _dominant_aspect(cells: list[tuple[int, int]], terrain: TerrainResponse) -> str | None:
    """Orientação predominante (moda do `aspect_label`) das células informadas (W6).

    Empate é resolvido pela ordem do relevo (norte → sul, oeste → leste), que é determinística:
    duas execuções com os mesmos dados devolvem sempre a mesma direção.
    """
    wanted = set(cells)
    labels = [cell.aspect_label for cell in terrain.cells if (cell.row, cell.col) in wanted]
    if not labels:
        return None

    counts = Counter(labels)
    best = max(counts.values())
    for label in labels:
        if counts[label] == best:
            return ASPECT_NAME[label]
    return None  # pragma: no cover — `labels` não vazio garante um vencedor


def _consecutive_runs(hours: list[int]) -> list[tuple[int, int]]:
    """Agrupa horas em sequências contínuas: `[6, 7, 8, 11, 12]` → `[(6, 8), (11, 12)]`."""
    runs: list[tuple[int, int]] = []
    for hour in hours:
        if runs and hour == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], hour)
        else:
            runs.append((hour, hour))
    return runs


def get_recommendations(
    farm: Farm,
    client: OpenMeteoClient,
    days: int = MAX_RECOMMENDATION_DAYS,
    scenario: Scenario | None = None,
    today: date | None = None,
) -> list[DayRecommendation]:
    """Recomendações de hoje (e de amanhã) para a fazenda (W6).

    Reaproveita o mesmo contexto do mapa de risco (`risk.build_forecast_context`), então a frase
    que o operador lê e as cores que ele vê no mapa **saem do mesmo cálculo** — não há como uma
    dizer uma coisa e a outra dizer outra.

    Propaga `WeatherUnavailableError`, que o tratador global converte em 503.
    """
    if not MIN_RECOMMENDATION_DAYS <= days <= MAX_RECOMMENDATION_DAYS:
        raise ValueError(
            f"A recomendação cobre de {MIN_RECOMMENDATION_DAYS} a {MAX_RECOMMENDATION_DAYS} "
            f"dias (hoje e amanhã); foram pedidos {days}."
        )

    context = risk_service.build_forecast_context(farm, client, days, scenario, today)
    return [
        build_day_recommendation(day, context.terrain, context.hourly)
        for day in context.forecast.days
    ]

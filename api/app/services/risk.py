"""Motor de risco relevo × clima (W3): estado do solo, limite do dia e perigos por célula.

Fonte da verdade das regras e dos limiares:
[document/regras-de-risco.md §3 a §6](../../../document/regras-de-risco.md#3-estado-do-solo).

O módulo é feito de **funções puras** sobre o relevo (W2) e os indicadores diários do clima (I1).
A única função com I/O é `get_risk_forecast`, que junta as duas fontes e aplica o cenário (§10).

## Motor extensível

Cada perigo é uma função com a mesma assinatura, registrada em `HAZARDS`:

```python
def meu_perigo(cell: TerrainCell, day: DailyWeather, ctx: DayContext) -> HazardResult | None: ...
```

Ela devolve `None` quando a célula está 🟢 nesse perigo e um `HazardResult` com o motivo escrito em
português quando está 🟡 ou 🔴. O nível da célula é o **pior** entre os perigos e os motivos de
todos os perigos em 🟡 ou 🔴 são acumulados. O W7 (raio, vento e incêndio, §5.3 a §5.5) escreve
mais funções e as acrescenta a `HAZARDS`, sem mexer na rota, no formato da resposta nem no resto do
motor. Os únicos schemas que ele toca são os enums `Hazard` (ganha `LIGHTNING`, `WIND` e `FIRE`) e
`Scenario` (ganha `STORM` e `HEATWAVE`) — acréscimos que não quebram quem já consome a API.
"""

import logging
import math
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from app.clients.open_meteo import OpenMeteoClient
from app.core.clock import now_local, today_local
from app.schemas.farm import Farm
from app.schemas.risk import (
    CellRisk,
    DayRisk,
    Hazard,
    HazardResult,
    LevelPercentages,
    RiskForecast,
    RiskLevel,
    Scenario,
    SoilState,
)
from app.schemas.terrain import TerrainCell, TerrainClass, TerrainResponse
from app.schemas.weather import DailyWeather, HourlyWeather
from app.services import terrain as terrain_service
from app.services.scenarios import apply_scenario
from app.services.weather import aggregate_daily

logger = logging.getLogger(__name__)

# regras-de-risco §3 — estado do solo pela chuva acumulada em 72 h
SOIL_MOIST_RAIN_72H_MM = 10.0
SOIL_SATURATED_RAIN_72H_MM = 30.0

# regras-de-risco §3 — fator que o estado do solo aplica ao limite de referência
SOIL_LIMIT_FACTOR: Final[dict[SoilState, float]] = {
    SoilState.DRY: 1.00,
    SoilState.MOIST: 0.85,
    SoilState.SATURATED: 0.67,
}

# regras-de-risco §4 — o limite do dia é arredondado para baixo de 0,5 em 0,5 (conservador)
TILT_LIMIT_STEP_DEG = 0.5

# regras-de-risco §5.1 — capotamento: r = slope_deg / L_dia
ROLLOVER_WARN_RATIO = 0.7
ROLLOVER_DANGER_RATIO = 1.0

# regras-de-risco §5.2 — atolamento: qualquer célula com esta chuva no dia já fica 🟡
BOGGING_RAIN_MM = 50.0

# regras-de-risco §5.3 — raio: CAPE a partir disto deixa a célula exposta em 🟡
LIGHTNING_CAPE = 2000.0

# regras-de-risco §5.4 — vento: 🟡 a partir de 45 km/h em célula exposta, 🔴 a partir de 60 km/h
WIND_WARN_GUST_KMH = 45.0
WIND_DANGER_GUST_KMH = 60.0

# regras-de-risco §5.5 — "regra dos 30" do incêndio em colheitadeira
FIRE_TEMP_C = 30.0
FIRE_HUMIDITY_PCT = 30.0
FIRE_WIND_KMH = 30.0
# 3 condições = 🔴; 2 = 🟡. Com inclinação a partir daqui, o 🟡 vira 🔴 (o fogo sobe a encosta).
FIRE_DANGER_CONDITIONS = 3
FIRE_WARN_CONDITIONS = 2
FIRE_SLOPE_ESCALATION_DEG = 15.0

# regras-de-risco §6 — quantos motivos aparecem no resumo do dia
TOP_REASONS_LIMIT = 3

# Feature W3 — a rota devolve de 1 a 7 dias, a partir de hoje
MIN_FORECAST_DAYS = 1
MAX_FORECAST_DAYS = 7

# regras-de-risco §2 — a previsão precisa de 3 dias passados para fechar a chuva de 72 h do 1º dia
PAST_DAYS = 3

# Casas decimais dos percentuais publicados na resposta (mesma convenção do relevo, W2).
PERCENT_DECIMALS = 1

# O pior nível é o de maior severidade; a ordem vale para células, perigos e dias.
LEVEL_SEVERITY: Final[dict[RiskLevel, int]] = {
    RiskLevel.GREEN: 0,
    RiskLevel.YELLOW: 1,
    RiskLevel.RED: 2,
}

# Textos de tela (padroes-de-codigo.md: identificadores em inglês, mensagens em português).
SOIL_STATE_LABEL: Final[dict[SoilState, str]] = {
    SoilState.DRY: "solo seco",
    SoilState.MOIST: "solo úmido",
    SoilState.SATURATED: "solo encharcado",
}


@dataclass(frozen=True)
class DayContext:
    """O que um perigo precisa saber do dia além do clima bruto: solo e limite já calculados.

    Passar um contexto (em vez de um argumento diferente por perigo) é o que mantém a assinatura
    de `HAZARDS` uniforme e deixa o W7 acrescentar perigos sem mexer na rota nem nos schemas.
    """

    soil_state: SoilState
    tilt_limit_deg: float


HazardFn = Callable[[TerrainCell, DailyWeather, DayContext], HazardResult | None]


@dataclass(frozen=True)
class ForecastContext:
    """Tudo o que uma consulta de risco produziu, para quem precisa de mais que a resposta.

    A W6 precisa das **horas** (para as janelas seguras) e do **relevo** (para a direção da
    encosta) além dos dias já avaliados. Sem isto, ela repetiria a orquestração de
    `get_risk_forecast` e as duas poderiam divergir com o tempo.
    """

    terrain: TerrainResponse
    hourly: HourlyWeather
    daily: list[DailyWeather]
    forecast: RiskForecast


def soil_state(rain_72h_mm: float) -> SoilState:
    """Estado do solo pela chuva em 72 h (regras-de-risco §3).

    Seco abaixo de 10 mm, úmido de 10 mm a menos de 30 mm e encharcado a partir de 30 mm.
    """
    if rain_72h_mm >= SOIL_SATURATED_RAIN_72H_MM:
        return SoilState.SATURATED
    if rain_72h_mm >= SOIL_MOIST_RAIN_72H_MM:
        return SoilState.MOIST
    return SoilState.DRY


def tilt_limit(l_ref_deg: float, soil: SoilState) -> float:
    """Limite de inclinação do dia: `floor_0.5(L_ref × fator do solo)` (regras-de-risco §4).

    Com `L_ref = 15°`: seco 15,0° · úmido 12,5° · encharcado 10,0°.
    """
    if l_ref_deg <= 0.0:
        raise ValueError("O limite de referência (L_ref) precisa ser maior que zero.")

    scaled = l_ref_deg * SOIL_LIMIT_FACTOR[soil]
    # O `round` antes do `floor` evita que 12,5 guardado como 12,499999… caia para 12,0.
    steps = math.floor(round(scaled / TILT_LIMIT_STEP_DEG, 9))
    limit = steps * TILT_LIMIT_STEP_DEG

    # regras-de-risco §4 — piso de 0,5° (o próprio passo do floor_0.5): sem ele, um L_ref muito
    # pequeno daria L_dia = 0° e §5.1 dividiria por zero. Com 0,5° a fazenda inteira sai 🔴.
    return max(limit, TILT_LIMIT_STEP_DEG)


def farm_reference_tilt_limit_deg(farm: Farm) -> float:
    """`L_ref` do **mapa de risco da fazenda**: o menor limite entre os equipamentos dela (§4).

    É a leitura conservadora fixada na W3: o mapa vale para a fazenda inteira, então ele nunca
    libera uma área que seria proibida para a máquina mais frágil. Sem nenhum equipamento, cai no
    `reference_tilt_limit_deg` da fazenda.

    **Não confundir com o limite de um equipamento (W4)**, que usa o `base_tilt_limit_deg`
    **daquele** equipamento e só recorre à fazenda se ele faltar.
    """
    return min(
        (device.base_tilt_limit_deg for device in farm.devices),
        default=farm.reference_tilt_limit_deg,
    )


def rollover_hazard(cell: TerrainCell, day: DailyWeather, ctx: DayContext) -> HazardResult | None:
    """Capotamento (regras-de-risco §5.1): `r = slope_deg / L_dia`.

    🟢 com `r < 0,7`, 🟡 de 0,7 a menos de 1,0 e 🔴 a partir de 1,0. Usamos a inclinação máxima do
    terreno, que é a leitura conservadora (a inclinação real depende da direção da máquina).
    """
    ratio = cell.slope_deg / ctx.tilt_limit_deg
    if ratio < ROLLOVER_WARN_RATIO:
        return None

    level = RiskLevel.RED if ratio >= ROLLOVER_DANGER_RATIO else RiskLevel.YELLOW
    comparison = "acima do" if level is RiskLevel.RED else "próxima do"
    return HazardResult(
        hazard=Hazard.ROLLOVER,
        level=level,
        message=(
            f"Inclinação de {format_number(cell.slope_deg)}° {comparison} limite de "
            f"{format_number(ctx.tilt_limit_deg)}° "
            f"({SOIL_STATE_LABEL[ctx.soil_state]}: {format_number(day.rain_72h_mm)} mm em 72 h)."
        ),
    )


def bogging_hazard(cell: TerrainCell, day: DailyWeather, ctx: DayContext) -> HazardResult | None:
    """Atolamento / alagamento (regras-de-risco §5.2).

    🔴 em baixada com solo encharcado. 🟡 em baixada com solo úmido ou em qualquer célula com
    50 mm ou mais de chuva no dia.
    """
    is_lowland = cell.terrain_class is TerrainClass.LOWLAND

    if is_lowland and ctx.soil_state is SoilState.SATURATED:
        return HazardResult(
            hazard=Hazard.BOGGING,
            level=RiskLevel.RED,
            message=(
                f"Baixada com solo encharcado ({format_number(day.rain_72h_mm)} mm em 72 h): "
                "risco de atolamento."
            ),
        )

    if is_lowland and ctx.soil_state is SoilState.MOIST:
        return HazardResult(
            hazard=Hazard.BOGGING,
            level=RiskLevel.YELLOW,
            message=(
                f"Baixada com solo úmido ({format_number(day.rain_72h_mm)} mm em 72 h): "
                "risco de atolamento."
            ),
        )

    if day.rain_mm >= BOGGING_RAIN_MM:
        return HazardResult(
            hazard=Hazard.BOGGING,
            level=RiskLevel.YELLOW,
            message=f"Chuva de {format_number(day.rain_mm)} mm no dia: risco de atolamento.",
        )

    return None


def lightning_hazard(cell: TerrainCell, day: DailyWeather, ctx: DayContext) -> HazardResult | None:
    """Raio (regras-de-risco §5.3).

    🔴 em tempestade **e** célula exposta (topo ou crista, que é onde o raio cai). 🟡 em
    tempestade nas demais células, ou com CAPE a partir de 2000 J/kg em célula exposta.
    """
    is_exposed = cell.terrain_class is TerrainClass.EXPOSED

    if day.thunderstorm:
        level = RiskLevel.RED if is_exposed else RiskLevel.YELLOW
        where = " em topo exposto" if is_exposed else ""
        return HazardResult(
            hazard=Hazard.LIGHTNING,
            level=level,
            message=f"Tempestade prevista{where}: risco de raio.",
        )

    if is_exposed and day.cape_max is not None and day.cape_max >= LIGHTNING_CAPE:
        return HazardResult(
            hazard=Hazard.LIGHTNING,
            level=RiskLevel.YELLOW,
            message=(
                f"Instabilidade alta (CAPE de {format_number(day.cape_max)} J/kg) em topo exposto: "
                "risco de raio."
            ),
        )

    return None


def wind_hazard(cell: TerrainCell, day: DailyWeather, ctx: DayContext) -> HazardResult | None:
    """Vento (regras-de-risco §5.4), pelas **rajadas** do dia.

    🔴 a partir de 60 km/h em célula exposta. 🟡 a partir de 60 km/h nas demais, ou de 45 a menos
    de 60 km/h em célula exposta.
    """
    gust = day.gust_max_kmh
    if gust is None:
        return None

    is_exposed = cell.terrain_class is TerrainClass.EXPOSED

    if gust >= WIND_DANGER_GUST_KMH:
        level = RiskLevel.RED if is_exposed else RiskLevel.YELLOW
    elif is_exposed and gust >= WIND_WARN_GUST_KMH:
        level = RiskLevel.YELLOW
    else:
        return None

    where = " em área exposta" if is_exposed else ""
    return HazardResult(
        hazard=Hazard.WIND,
        level=level,
        message=f"Rajadas de {format_number(gust)} km/h{where}: risco para máquinas altas.",
    )


def fire_conditions(day: DailyWeather) -> list[str]:
    """Quais condições da "regra dos 30" estão ativas, já escritas para a mensagem (§5.5).

    Temperatura acima de 30 °C, umidade abaixo de 30% e vento acima de 30 km/h. Indicador
    ausente conta como **não atendido**: o motor não inventa condição que não mediu.
    """
    active: list[str] = []
    if day.temp_max_c is not None and day.temp_max_c > FIRE_TEMP_C:
        active.append(f"{format_number(day.temp_max_c)} °C")
    if day.rh_min_pct is not None and day.rh_min_pct < FIRE_HUMIDITY_PCT:
        active.append(f"UR de {format_number(day.rh_min_pct)}%")
    if day.wind_max_kmh is not None and day.wind_max_kmh > FIRE_WIND_KMH:
        active.append(f"vento de {format_number(day.wind_max_kmh)} km/h")
    return active


def fire_hazard(cell: TerrainCell, day: DailyWeather, ctx: DayContext) -> HazardResult | None:
    """Incêndio em colheitadeira pela "regra dos 30" (regras-de-risco §5.5).

    🔴 com as 3 condições, 🟡 com 2. Vale para todas as células — e o 🟡 **vira 🔴** onde a
    inclinação é de 15° ou mais, porque o fogo sobe a encosta mais rápido do que se corre.
    """
    active = fire_conditions(day)
    if len(active) < FIRE_WARN_CONDITIONS:
        return None

    level = RiskLevel.RED if len(active) >= FIRE_DANGER_CONDITIONS else RiskLevel.YELLOW
    escalated = level is RiskLevel.YELLOW and cell.slope_deg >= FIRE_SLOPE_ESCALATION_DEG
    if escalated:
        level = RiskLevel.RED

    reason = ", ".join(active)
    slope_note = ""
    if escalated:
        slope_note = f" Encosta de {format_number(cell.slope_deg)}°: o fogo sobe mais rápido."
    return HazardResult(
        hazard=Hazard.FIRE,
        level=level,
        message=(
            f"Regra dos 30: {len(active)} de {FIRE_DANGER_CONDITIONS} condições "
            f"({reason}): risco de incêndio.{slope_note}"
        ),
    )


# regras-de-risco §5 — perigos avaliados em cada célula, na ordem em que aparecem no documento.
# Acrescentar um perigo novo é acrescentar uma função aqui: a rota, os schemas e o resto do motor
# continuam iguais (foi assim que a W7 entrou depois da W3).
HAZARDS: list[HazardFn] = [
    rollover_hazard,
    bogging_hazard,
    lightning_hazard,
    wind_hazard,
    fire_hazard,
]


def assess_cell(
    cell: TerrainCell,
    day: DailyWeather,
    ctx: DayContext,
    hazards: Sequence[HazardFn] | None = None,
) -> CellRisk:
    """Risco de uma célula num dia: o pior nível entre os perigos, com os motivos acumulados."""
    reasons = [
        result
        for hazard in (HAZARDS if hazards is None else hazards)
        if (result := hazard(cell, day, ctx)) is not None
    ]
    reasons.sort(key=lambda result: LEVEL_SEVERITY[result.level], reverse=True)

    return CellRisk(
        row=cell.row,
        col=cell.col,
        level=worst_level(result.level for result in reasons),
        reasons=reasons,
    )


def assess_day(
    terrain: TerrainResponse,
    day: DailyWeather,
    l_ref_deg: float,
    hazards: Sequence[HazardFn] | None = None,
) -> DayRisk:
    """Avalia a fazenda inteira num dia e monta o resumo de §6."""
    soil = soil_state(day.rain_72h_mm)
    ctx = DayContext(soil_state=soil, tilt_limit_deg=tilt_limit(l_ref_deg, soil))
    cells = [assess_cell(cell, day, ctx, hazards) for cell in terrain.cells]

    return DayRisk(
        date=day.date,
        rain_mm=day.rain_mm,
        rain_72h_mm=day.rain_72h_mm,
        wind_max_kmh=day.wind_max_kmh,
        rh_min_pct=day.rh_min_pct,
        cape_max=day.cape_max,
        thunderstorm=day.thunderstorm,
        gust_max_kmh=day.gust_max_kmh,
        temp_max_c=day.temp_max_c,
        soil_state=soil,
        tilt_limit_deg=ctx.tilt_limit_deg,
        worst_level=worst_level(cell.level for cell in cells),
        pct_levels=_pct_levels(cells),
        top_reasons=_top_reasons(cells),
        cells=cells,
    )


def assess_farm(
    terrain: TerrainResponse,
    daily: Sequence[DailyWeather],
    l_ref: float,
    scenario: Scenario | None = None,
    generated_at: datetime | None = None,
    hazards: Sequence[HazardFn] | None = None,
) -> RiskForecast:
    """Previsão de risco da fazenda: um `DayRisk` para cada dia recebido. Função pura (sem rede).

    Os dias entram já filtrados (ver `select_days`), porque `rain_72h_mm` depende dos dias
    anteriores e precisa ser calculado sobre a série completa.
    """
    return RiskForecast(
        farm_id=terrain.farm_id,
        generated_at=generated_at if generated_at is not None else now_local(),
        scenario=scenario,
        days=[assess_day(terrain, day, l_ref, hazards) for day in daily],
    )


def select_days(
    daily: Sequence[DailyWeather], today: date, days: int = MAX_FORECAST_DAYS
) -> list[DailyWeather]:
    """Só os dias de hoje em diante, em ordem cronológica e no máximo `days` (feature W3)."""
    if not MIN_FORECAST_DAYS <= days <= MAX_FORECAST_DAYS:
        raise ValueError(
            f"A previsão aceita de {MIN_FORECAST_DAYS} a {MAX_FORECAST_DAYS} dias; "
            f"foram pedidos {days}."
        )

    upcoming = sorted((day for day in daily if day.date >= today), key=lambda day: day.date)
    return upcoming[:days]


def worst_level(levels: Iterable[RiskLevel]) -> RiskLevel:
    """O pior nível de um conjunto. Sem nenhum nível, a resposta é 🟢 (nenhum perigo disparou)."""
    return max(levels, key=LEVEL_SEVERITY.__getitem__, default=RiskLevel.GREEN)


def build_forecast_context(
    farm: Farm,
    client: OpenMeteoClient,
    days: int = MAX_FORECAST_DAYS,
    scenario: Scenario | None = None,
    today: date | None = None,
) -> ForecastContext:
    """Junta relevo (W2) e previsão (I1), aplica o cenário (§10) e roda o motor de risco.

    Propaga `WeatherUnavailableError` quando a Open-Meteo falha e não há nada em cache; o tratador
    global de `app/main.py` transforma isso em 503.
    """
    reference_day = today if today is not None else today_local()

    terrain = terrain_service.get_terrain(farm, client)
    hourly = client.fetch_hourly_forecast(
        farm.center.lat, farm.center.lon, past_days=PAST_DAYS, forecast_days=MAX_FORECAST_DAYS
    )
    hourly = apply_scenario(hourly, scenario, reference_day)

    daily = select_days(aggregate_daily(hourly), reference_day, days)
    if not daily:
        logger.warning(
            "A previsão da fazenda '%s' não trouxe nenhum dia a partir de %s.",
            farm.id,
            reference_day,
        )

    forecast = assess_farm(terrain, daily, farm_reference_tilt_limit_deg(farm), scenario=scenario)
    _attach_model(forecast, farm, terrain, daily)
    return ForecastContext(terrain=terrain, hourly=hourly, daily=daily, forecast=forecast)


def _attach_model(
    forecast: RiskForecast, farm: Farm, terrain: TerrainResponse, daily: list[DailyWeather]
) -> None:
    """Acrescenta a leitura do modelo (W13) **ao lado** do que as regras decidiram.

    Import tardio de propósito: `app/services/model.py` traz `pandas`, `numpy` e `scikit-learn`
    junto, e o motor de risco não pode depender disso para funcionar. Sem artefato, ou com ele
    corrompido, esta função simplesmente não preenche nada.
    """
    from app.services import model as model_service
    from app.services import model_scoring

    # As duas metades do modelo vêm de arquivos diferentes: o pipeline do `.joblib` e os
    # metadados do `.json`. Uma escrita interrompida no retreino deixa um sem o outro — e
    # anunciar "modelo v1, AUC-PR 0,073" sem nenhuma probabilidade seria o bloco que existe para
    # dizer a verdade descrevendo um modelo que não pontuou nada (com a trilha da I5 assinando a
    # decisão com ele). Por isso **o pipeline manda**: sem ele, não há bloco, campos nem versão.
    pipeline = model_service.get_model()
    if pipeline is None:
        return

    # Metadados lidos **a cada previsão**: quando a D3 retreinar, a versão e as métricas mudam
    # sozinhas, sem valor fixo no código.
    info = model_scoring.model_info(model_service.load_metadata())
    forecast.model = info
    model_scoring.apply_model(forecast.days, farm, terrain, daily, pipeline, info)


def get_risk_forecast(
    farm: Farm,
    client: OpenMeteoClient,
    days: int = MAX_FORECAST_DAYS,
    scenario: Scenario | None = None,
    today: date | None = None,
) -> RiskForecast:
    """Previsão de risco da fazenda (W3). Atalho para `build_forecast_context(...).forecast`."""
    return build_forecast_context(farm, client, days, scenario, today).forecast


def _pct_levels(cells: Sequence[CellRisk]) -> LevelPercentages:
    """% de células em cada nível (regras-de-risco §6).

    🔴 e 🟡 são calculados direto e 🟢 fica com o resto, para os três somarem 100,0 mesmo depois
    do arredondamento.
    """
    total = len(cells)
    if total == 0:
        return LevelPercentages(green=0.0, yellow=0.0, red=0.0)

    counts = Counter(cell.level for cell in cells)
    red = round(100.0 * counts[RiskLevel.RED] / total, PERCENT_DECIMALS)
    yellow = round(100.0 * counts[RiskLevel.YELLOW] / total, PERCENT_DECIMALS)
    green = round(100.0 - red - yellow, PERCENT_DECIMALS)
    return LevelPercentages(green=green, yellow=yellow, red=red)


def _top_reasons(cells: Sequence[CellRisk]) -> list[HazardResult]:
    """Motivos mais frequentes entre as células 🔴 e 🟡 (regras-de-risco §6).

    Os motivos trazem números, então duas células dificilmente têm o texto idêntico. Por isso eles
    são agrupados por (perigo, nível) e cada grupo é representado pela mensagem mais repetida nele.
    A ordem é do mais grave para o menos grave e, no mesmo nível, do mais frequente para o menos.
    """
    groups: dict[tuple[Hazard, RiskLevel], Counter[str]] = {}
    for cell in cells:
        for reason in cell.reasons:
            groups.setdefault((reason.hazard, reason.level), Counter())[reason.message] += 1

    ranked = sorted(
        groups.items(),
        key=lambda item: (LEVEL_SEVERITY[item[0][1]], sum(item[1].values())),
        reverse=True,
    )

    return [
        HazardResult(hazard=hazard, level=level, message=messages.most_common(1)[0][0])
        for (hazard, level), messages in ranked[:TOP_REASONS_LIMIT]
    ]


def format_number(value: float) -> str:
    """Número para a mensagem em português: uma casa decimal, com vírgula e sem o `,0`."""
    text = f"{value:.1f}".removesuffix(".0")
    return text.replace(".", ",")

"""Replay de acidentes reais (W9) — o mesmo motor, rodando sobre o passado.

O valor desta feature está em **não haver código diferente para o passado**: o relevo vem do W2,
o clima vem da I1 (histórico em vez de previsão) e o nível sai de `assess_day`, exatamente como
no mapa de hoje. Se houvesse um ramo especial aqui, o replay não provaria nada sobre o produto.

Duas escolhas que o [§9](../../../document/regras-de-risco.md#9-replay-w9) define:

- datas **a partir de 2022** vêm da Historical Forecast API; anteriores, da Archive (ERA5);
- na Archive, `cape` vem indefinido, então o perigo `lightning` (§5.3) passa a valer **só** pelo
  `weather_code`. Isso entra em `limitations`, porque comparar um replay de 2019 com um de 2024
  sem dizer isso é comparar maçã com laranja.

## A grade é pequena de propósito

O mapa da fazenda usa 10 × 10 = 100 pontos numa chamada de elevação. A Elevation API **recusa**
chamadas multiponto grandes com 429 — foi assim que a cota do time caiu em 19/09. Aqui a grade é
**3 × 3** (9 pontos), o que basta para responder "qual o relevo em volta do acidente" e cabe
folgado na cota, mesmo rodando os cinco casos seguidos.

## A ressalva que não pode sumir

Nenhuma das notícias deu coordenada: todos os casos têm `location_precision: "municipio"`. O
ponto é a mediana das coordenadas reais das apólices do PSR naquele município — um ponto agrícola
de verdade, mas que pode estar a quilômetros da lavoura do acidente. O veredito **carrega essa
ressalva junto**, porque sem ela "o sistema teria alertado" é uma afirmação que não se sustenta.
"""

import json
import logging
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from app.clients.open_meteo import HISTORICAL_FORECAST_MIN_DATE, OpenMeteoClient
from app.schemas.farm import DEFAULT_REFERENCE_TILT_LIMIT_DEG, BBox, Device, Farm, LatLon
from app.schemas.replay import (
    HistoricalSource,
    LocationPrecision,
    ReplayCase,
    ReplayCaseVerdict,
    ReplayResult,
    ReplaySummary,
    ReplayWeather,
)
from app.schemas.risk import CellRisk, DayRisk, RiskLevel
from app.schemas.terrain import TerrainResponse
from app.services import terrain as terrain_service
from app.services.risk import assess_day, format_number
from app.services.weather import RAIN_WINDOW_DAYS, aggregate_daily

logger = logging.getLogger(__name__)

# Os casos ficam dentro do pacote, como o catálogo de fazendas (W1).
CASES_FILE = Path(__file__).resolve().parents[1] / "data" / "replay_cases.json"

# regras-de-risco §9 — a grade é centrada no ponto do acidente, com ±0,005°
HALF_SIDE_DEG = 0.005

# W9 — 3 × 3 = 9 pontos de elevação. Ver "A grade é pequena de propósito", acima.
REPLAY_GRID_SIZE = 3

ARCHIVE_LIGHTNING_LIMITATION = (
    "Esta data vem da Archive API (ERA5), que não tem CAPE: o perigo de raio foi avaliado só "
    "pelo código de tempestade (regras-de-risco §9). Um replay desta época é menos sensível a "
    "raio do que um de 2022 em diante."
)
MUNICIPALITY_LIMITATION = (
    "A notícia não deu a coordenada do acidente: o ponto é o de uma lavoura típica do município "
    "(mediana das apólices do PSR ali). O relevo avaliado pode estar a quilômetros da lavoura "
    "onde o acidente aconteceu."
)
APPROXIMATE_LIMITATION = (
    "A coordenada é aproximada: o relevo avaliado é o da vizinhança do acidente, não o do ponto "
    "exato."
)
GRID_LIMITATION = (
    "A grade do replay é 3 × 3 (cerca de 1 km²) em volta do ponto, menor que a do mapa de uma "
    "fazenda: ela mostra o relevo da vizinhança, não o talhão inteiro."
)
ALWAYS_LIMITATION = (
    "O veredito diz o que o motor **teria mostrado** naquele dia e naquele ponto. Ele não afirma "
    "que o alerta teria evitado o acidente."
)


class ReplayCasesError(RuntimeError):
    """O arquivo de casos não pôde ser lido ou está inválido."""


def load_cases_from_file(path: Path) -> tuple[ReplayCase, ...]:
    """Lê e valida os casos. Erro vira `ReplayCasesError` em português."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReplayCasesError(f"Não foi possível ler os casos em '{path}': {error}.") from error
    except json.JSONDecodeError as error:
        raise ReplayCasesError(f"Os casos em '{path}' não são um JSON válido: {error}.") from error

    if not isinstance(payload, list):
        raise ReplayCasesError(f"Os casos em '{path}' precisam ser uma lista.")

    try:
        return tuple(ReplayCase.model_validate(item) for item in payload)
    except ValidationError as error:
        raise ReplayCasesError(f"Caso inválido em '{path}':\n{error}") from error


@lru_cache
def load_cases() -> tuple[ReplayCase, ...]:
    """Casos curados, lidos do disco uma vez por processo."""
    return load_cases_from_file(CASES_FILE)


def get_case(case_id: str) -> ReplayCase | None:
    """Caso pelo `id`, ou `None` (a rota transforma em 404)."""
    for case in load_cases():
        if case.id == case_id:
            return case
    return None


def source_for(day: date) -> HistoricalSource:
    """Qual API histórica responde por aquela data (regras-de-risco §9)."""
    if day < HISTORICAL_FORECAST_MIN_DATE:
        return HistoricalSource.ARCHIVE
    return HistoricalSource.HISTORICAL_FORECAST


def limitations_for(day: date, precision: LocationPrecision) -> list[str]:
    """Tudo o que limita a leitura deste replay, em português e na ordem de importância."""
    limitations: list[str] = []

    if precision is LocationPrecision.MUNICIPIO:
        limitations.append(MUNICIPALITY_LIMITATION)
    elif precision is LocationPrecision.APROXIMADO:
        limitations.append(APPROXIMATE_LIMITATION)

    if source_for(day) is HistoricalSource.ARCHIVE:
        limitations.append(ARCHIVE_LIGHTNING_LIMITATION)

    limitations.append(GRID_LIMITATION)
    limitations.append(ALWAYS_LIMITATION)
    return limitations


def replay_farm(point: LatLon, l_ref_deg: float, case_id: str | None = None) -> Farm:
    """Fazenda sintética de ±0,005° em volta do ponto, para reusar o relevo do W2 sem adaptação.

    O `id` vira a chave do cache de relevo: por caso, quando há caso, e pela coordenada
    arredondada quando o replay é manual — assim repetir o mesmo ponto não gasta a cota de novo.
    """
    identifier = case_id or f"replay:{point.lat:.4f},{point.lon:.4f}"
    return Farm(
        id=identifier,
        name=f"Replay em {point.lat:.4f}, {point.lon:.4f}",
        municipality="—",
        state="BR",
        crop="—",
        center=point,
        bbox=BBox(
            north=point.lat + HALF_SIDE_DEG,
            south=point.lat - HALF_SIDE_DEG,
            west=point.lon - HALF_SIDE_DEG,
            east=point.lon + HALF_SIDE_DEG,
        ),
        reference_tilt_limit_deg=l_ref_deg,
        devices=[
            Device(
                device_id=f"{identifier}-maquina",
                name="Máquina do caso",
                type="tractor",
                base_tilt_limit_deg=l_ref_deg,
            )
        ],
    )


def nearest_cell(day_risk: DayRisk, terrain: TerrainResponse, point: LatLon) -> CellRisk:
    """A célula cujo centro está mais perto do ponto do acidente (§9).

    A distância é euclidiana em graus, o que basta numa grade de 1 km — a diferença para a
    distância real é muito menor que a incerteza da própria coordenada do caso.
    """
    closest = min(
        terrain.cells,
        key=lambda cell: (cell.lat - point.lat) ** 2 + (cell.lon - point.lon) ** 2,
    )
    by_position = {(cell.row, cell.col): cell for cell in day_risk.cells}
    return by_position[(closest.row, closest.col)]


def build_verdict(point_cell: CellRisk, would_alert_in_grid: bool) -> str:
    """O veredito em uma frase, com **todos** os motivos — ou com a ausência deles.

    Citar só o primeiro motivo engana: em Imbituva, o motor marcou atolamento **e** raio no mesmo
    dia, e o acidente foi por raio. Um veredito que mostrasse só o atolamento pareceria um acerto
    por acaso, quando o motor tinha apontado a causa certa.
    """
    if point_cell.reasons:
        motives = " ".join(reason.message for reason in point_cell.reasons)
        return f"O sistema teria alertado no ponto do acidente. {motives}"

    if would_alert_in_grid:
        return (
            "O sistema **não** teria alertado na célula do ponto, mas marcou risco em outra "
            "célula da mesma grade — o relevo muda em poucas centenas de metros."
        )
    return (
        "O sistema **não** teria alertado neste ponto e nesta data: nenhum perigo do motor "
        "atingiu o nível de atenção."
    )


def run_replay(
    client: OpenMeteoClient,
    point: LatLon,
    day: date,
    l_ref_deg: float = DEFAULT_REFERENCE_TILT_LIMIT_DEG,
    precision: LocationPrecision = LocationPrecision.APROXIMADO,
    case: ReplayCase | None = None,
    grid_size: int = REPLAY_GRID_SIZE,
) -> ReplayResult:
    """Roda o motor de risco naquele ponto e naquela data. Propaga `WeatherUnavailableError`.

    A janela buscada é de d−2 a d, que é o que `rain_72h_mm` precisa (§2): o mesmo indicador que
    o mapa de hoje usa, calculado do mesmo jeito.
    """
    farm = replay_farm(point, l_ref_deg, case.id if case else None)
    terrain = terrain_service.get_terrain(farm, client, n=grid_size)

    start = day - timedelta(days=RAIN_WINDOW_DAYS - 1)
    hourly = client.fetch_hourly_history(point.lat, point.lon, start=start, end=day)
    daily = {weather.date: weather for weather in aggregate_daily(hourly)}

    weather = daily.get(day)
    if weather is None:
        raise ReplayCasesError(
            f"O histórico não trouxe o dia {day.isoformat()} para {point.lat}, {point.lon}."
        )

    day_risk = assess_day(terrain, weather, l_ref_deg)
    point_cell = nearest_cell(day_risk, terrain, point)
    would_alert_in_grid = any(cell.level is not RiskLevel.GREEN for cell in day_risk.cells)

    logger.info(
        "Replay de %s em %s, %s: célula do ponto %s (grade %s).",
        day.isoformat(),
        format_number(point.lat),
        format_number(point.lon),
        point_cell.level.value,
        "com alerta" if would_alert_in_grid else "sem alerta",
    )

    return ReplayResult(
        case=case,
        date=day,
        location=point,
        location_precision=precision,
        grid_size=grid_size,
        reference_tilt_limit_deg=l_ref_deg,
        point_cell=point_cell,
        day=day_risk,
        terrain=terrain,
        weather=ReplayWeather(
            rain_mm=weather.rain_mm,
            rain_72h_mm=weather.rain_72h_mm,
            temp_max_c=weather.temp_max_c,
            rh_min_pct=weather.rh_min_pct,
            wind_max_kmh=weather.wind_max_kmh,
            gust_max_kmh=weather.gust_max_kmh,
            cape_max=weather.cape_max,
            thunderstorm=weather.thunderstorm,
        ),
        would_alert=point_cell.level is not RiskLevel.GREEN,
        would_alert_in_grid=would_alert_in_grid,
        verdict=build_verdict(point_cell, would_alert_in_grid),
        source=source_for(day),
        limitations=limitations_for(day, precision),
    )


def run_case(
    client: OpenMeteoClient,
    case: ReplayCase,
    l_ref_deg: float = DEFAULT_REFERENCE_TILT_LIMIT_DEG,
) -> ReplayResult:
    """Replay de um caso cadastrado, com a precisão e a fonte dele."""
    return run_replay(
        client,
        point=case.location,
        day=case.date,
        l_ref_deg=l_ref_deg,
        precision=case.location_precision,
        case=case,
    )


# --- Placar agregado (W9) -----------------------------------------------------------------------

SUMMARY_NOTE_TEMPLATE = (
    "{evaluated} casos reais avaliados, {at_point} com alerta na célula do ponto e {in_grid} "
    "com alerta em alguma célula da grade. **Isto não é taxa de acerto do produto**: cinco casos "
    "noticiados não são amostra, todos têm coordenada de município (não da lavoura) e os casos "
    "foram escolhidos para exercitar perigos diferentes, não sorteados. O número serve para "
    "mostrar o motor aplicado a fatos reais, com os acertos e os erros à vista."
)
PARTIAL_NOTE = (
    " {failed} de {total} casos não puderam ser avaliados agora (serviço de clima indisponível), "
    "então o placar está incompleto."
)


def summarize_cases(
    client: OpenMeteoClient, l_ref_deg: float = DEFAULT_REFERENCE_TILT_LIMIT_DEG
) -> ReplaySummary:
    """Placar dos casos cadastrados, com a ressalva colada ao número (W9).

    Reaproveita o cache de cada replay individual: o relevo fica 24 h e o histórico 7 dias, então
    o resumo só é caro na **primeira** vez. Um caso que falhe entra em `failed_case_ids` — o
    placar não pode mentir por omissão dizendo "2 de 5" quando avaliou 3.
    """
    cases = load_cases()
    verdicts: list[ReplayCaseVerdict] = []
    failed: list[str] = []

    for case in cases:
        try:
            result = run_case(client, case, l_ref_deg=l_ref_deg)
        except Exception:  # noqa: BLE001 — um caso fora não pode derrubar o placar inteiro
            logger.warning("Não foi possível avaliar o caso '%s' agora.", case.id, exc_info=True)
            failed.append(case.id)
            continue

        verdicts.append(
            ReplayCaseVerdict(
                case_id=case.id,
                title=case.title,
                date=case.date,
                municipality=case.municipality,
                state=case.state,
                would_alert=result.would_alert,
                would_alert_in_grid=result.would_alert_in_grid,
                point_level=result.point_cell.level.value,
                source_url=case.source_url,
            )
        )

    at_point = sum(1 for verdict in verdicts if verdict.would_alert)
    in_grid = sum(1 for verdict in verdicts if verdict.would_alert_in_grid)

    note = SUMMARY_NOTE_TEMPLATE.format(evaluated=len(verdicts), at_point=at_point, in_grid=in_grid)
    if failed:
        note += PARTIAL_NOTE.format(failed=len(failed), total=len(cases))

    return ReplaySummary(
        total_cases=len(cases),
        evaluated=len(verdicts),
        failed_case_ids=failed,
        would_alert_at_point=at_point,
        would_alert_in_grid=in_grid,
        note=note,
        cases=verdicts,
    )

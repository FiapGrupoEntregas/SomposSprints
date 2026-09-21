"""Dataset de treino: relevo × clima × sinistro (D2).

Pega as apólices reais carregadas pela D1, busca o **relevo** do local (reaproveitando o W2) e o
**clima da vigência** (reaproveitando o cliente da I1) e devolve uma linha por apólice, com os dois
rótulos que a D3 vai prever.

    sample_policies(session, n, seed)   amostra estratificada por UF, cultura, ano e rótulo
    build_features(policies, client)    relevo + clima + contexto, uma linha por apólice
    save_dataset(df, path)              grava em parquet (ou CSV)

**Sem vazamento.** A janela climática vai do início ao fim da vigência (no máximo 12 meses), e
nada que só se saiba depois disso vira feature. As chamadas à Open-Meteo são feitas numa janela
arredondada para o mês (para várias apólices dividirem a mesma resposta), mas os indicadores são
calculados **só sobre os dias dentro da vigência** — o arredondamento é de cache, não de conteúdo.

Fontes e limitações: document/dados-e-modelo.md §1 e §2.
"""

import logging
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlmodel import Session, select

from app.clients.open_meteo import OpenMeteoClient, WeatherUnavailableError
from app.models import Policy
from app.schemas.farm import BBox
from app.schemas.terrain import TerrainClass
from app.schemas.weather import DailyWeather
from app.services.terrain import (
    aspect_label,
    build_grid,
    cell_size_m,
    classify_cells,
    compute_slope_aspect,
)
from app.services.weather import aggregate_daily

logger = logging.getLogger(__name__)

# --- Parâmetros da geração --------------------------------------------------------------------

#: Grade de relevo ao redor da propriedade: 3 × 3 pontos numa caixa de ±0,005° (~1,1 km de lado).
#: A D2 usa uma grade pequena porque o ponto do PSR é a sede, não o talhão.
TERRAIN_GRID_SIZE = 3
TERRAIN_HALF_DEG = 0.005

#: Célula usada para **agrupar chamadas de clima**: 0,25° ≈ 27 km, a mesma ordem de grandeza da
#: resolução do ERA5. Propriedades na mesma célula e na mesma janela dividem uma chamada.
WEATHER_CELL_DEG = 0.25

#: Quantas propriedades vão numa chamada de elevação.
#:
#: A Elevation API **documenta** 100 pontos por chamada, e o cliente da I1 aceita isso. Na prática,
#: medido em 20/09/2026: com 9 pontos por chamada e 0,5 s de pausa, 6 chamadas seguidas passam;
#: com 27 pontos, 5 de 6 voltam **429**; com 99 pontos, todas voltam 429, mesmo com 5 s de pausa.
#: O `api.open-meteo.com` pondera a chamada pelo número de pontos e tem cota própria, separada da
#: `archive-api` e da `historical-forecast-api`. Então mandamos **uma propriedade por chamada**.
PROPERTIES_PER_ELEVATION_CALL = 1
ELEVATION_POINTS_PER_CALL = PROPERTIES_PER_ELEVATION_CALL * TERRAIN_GRID_SIZE**2

#: Teto da janela climática (escopo da D2: "limitada a 12 meses").
MAX_WINDOW_DAYS = 366

# regras-de-risco §2 — solo encharcado é `rain_72h_mm ≥ 30`; seco é `rain_72h_mm < 10`.
SATURATED_RAIN_72H_MM = 30.0
DRY_RAIN_72H_MM = 10.0

#: 2025 ainda está em vigência: `indemnity_value` nulo ali significa "ainda não se sabe", não
#: "não houve". Incluir essas apólices com rótulo 0 ensinaria o modelo a errar.
LAST_LABELLED_YEAR = 2024

#: Vigência mínima para a apólice entrar no dataset.
#:
#: **Todas** as 430.843 apólices do arquivo de 2006–2015 têm `DT_INICIO_VIGENCIA` igual a
#: `DT_FIM_VIGENCIA` (conferido em 20/09/2026: 430.843 de 430.843; nenhuma linha de 2016 em diante
#: tem o problema). Aquele CSV simplesmente não traz data de fim utilizável. Com vigência de um
#: dia não há janela climática: `rain_total_mm` daria 0 e `dry_spell_max_days` daria 1 para todo
#: mundo. São 29% da base — e gastariam 29% da cota da Open-Meteo para produzir ruído.
#: Uma safra dura ~150–210 dias; 30 é um piso folgado que só corta o que está quebrado
#: (em 2016–2024 são 74 apólices, 0,007%).
MIN_COVERAGE_DAYS = 30

#: Culturas que viram estrato próprio; o resto cai em `outras`.
TOP_CROPS = ("Soja", "Milho 2ª safra", "Trigo", "Uva", "Café", "Milho 1ª safra")

#: Eventos que contam como sinistro de chuva para `target_rain_claim` (escopo da D2).
RAIN_EVENTS = ("chuva_excessiva", "granizo")

DATASET_COLUMNS = (
    "proposal_id",
    # contexto (tudo conhecido na emissão da apólice)
    "state",
    "municipality",
    "geocode_ibge",
    "crop",
    "crop_group",
    "area_ha",
    "lat",
    "lon",
    "coordinate_source",
    "policy_year",
    "start_date",
    "end_date",
    "start_month",
    "coverage_days",
    # relevo
    "slope_mean_deg",
    "slope_max_deg",
    "elevation_mean_m",
    "elevation_range_m",
    "aspect_label",
    "pct_lowland",
    "pct_exposed",
    # clima da vigência
    "rain_total_mm",
    "rain_max_day_mm",
    "days_rain72h_ge30",
    "days_thunderstorm",
    "gust_max_kmh",
    "temp_max_c",
    "rh_min_pct",
    "dry_spell_max_days",
    "weather_days",
    # rótulos
    "target_claim",
    "target_rain_claim",
)


@dataclass
class DatasetReport:
    """O que entrou, o que saiu e quanto custou de rede."""

    policies_sampled: int = 0
    rows_built: int = 0
    discarded: Counter[str] = field(default_factory=Counter)
    elevation_calls: int = 0
    weather_calls: int = 0
    weather_groups: int = 0
    by_state: Counter[str] = field(default_factory=Counter)
    by_year: Counter[int] = field(default_factory=Counter)

    @property
    def rows_discarded(self) -> int:
        return sum(self.discarded.values())

    @property
    def api_calls(self) -> int:
        return self.elevation_calls + self.weather_calls

    def as_dict(self) -> dict:
        return {
            "policies_sampled": self.policies_sampled,
            "rows_built": self.rows_built,
            "rows_discarded": self.rows_discarded,
            "discarded": dict(self.discarded.most_common()),
            "elevation_calls": self.elevation_calls,
            "weather_calls": self.weather_calls,
            "weather_groups": self.weather_groups,
            "api_calls": self.api_calls,
            "by_state": dict(self.by_state.most_common()),
            "by_year": dict(sorted(self.by_year.items())),
        }

    def render(self) -> str:
        lines = [
            "Relatório do dataset — relevo × clima × sinistro",
            f"  apólices amostradas ... {self.policies_sampled}",
            f"  linhas geradas ........ {self.rows_built}",
            f"  linhas descartadas .... {self.rows_discarded}",
        ]
        lines += [f"      {reason}: {count}" for reason, count in self.discarded.most_common()]
        lines += [
            f"  chamadas de elevação .. {self.elevation_calls}",
            f"  chamadas de clima ..... {self.weather_calls} "
            f"(em {self.weather_groups} grupos de célula × janela)",
            f"  total de chamadas ..... {self.api_calls}",
        ]
        return "\n".join(lines)


# --- 1. Amostragem ------------------------------------------------------------------------------


def crop_group(crop: object) -> str:
    """Agrupa a cultura: as 6 mais frequentes viram estrato próprio, o resto vira `outras`."""
    if crop is None or (isinstance(crop, float) and pd.isna(crop)):
        return "outras"
    text = str(crop).strip()
    return text if text in TOP_CROPS else "outras"


#: Colunas lidas do banco. Selecionar coluna a coluna, em vez de hidratar `Policy` inteiro, é o
#: que mantém a leitura de 1,5 milhão de linhas em segundos e em centenas de MB — com os objetos
#: ORM completos eram 28 s e ~4 GB.
POPULATION_COLUMNS = (
    "proposal_id",
    "state",
    "municipality",
    "geocode_ibge",
    "crop",
    "area_ha",
    "lat",
    "lon",
    "coordinate_source",
    "policy_year",
    "start_date",
    "end_date",
    "indemnity_value",
    "event_category",
)


def load_labelled_policies(session: Session) -> pd.DataFrame:
    """Lê da tabela `policy` as apólices que já têm desfecho conhecido.

    2025 fica de fora: a vigência não terminou, então a ausência de indenização é censura, não
    rótulo negativo — treinar com ela como 0 ensinaria o modelo a errar.
    """
    columns = [getattr(Policy, name) for name in POPULATION_COLUMNS]
    statement = select(*columns).where(Policy.policy_year <= LAST_LABELLED_YEAR)
    frame = pd.DataFrame(session.exec(statement).all(), columns=list(POPULATION_COLUMNS))
    if frame.empty:
        return frame

    # Sem vigência utilizável não há janela climática. Filtrar **aqui**, e não depois de já ter
    # gastado a chamada, é o que impede 29% da cota de virar ruído.
    span_days = (pd.to_datetime(frame["end_date"]) - pd.to_datetime(frame["start_date"])).dt.days
    usable = frame[span_days >= MIN_COVERAGE_DAYS]
    dropped = len(frame) - len(usable)
    if dropped:
        logger.info(
            "%d apólices fora do dataset por vigência menor que %d dias (%.1f%% da base rotulada)",
            dropped,
            MIN_COVERAGE_DAYS,
            100 * dropped / len(frame),
        )
    return _with_labels(usable)


def _with_labels(frame: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta `crop_group` e os dois rótulos."""
    frame = frame.copy()
    indemnity = pd.to_numeric(frame["indemnity_value"], errors="coerce").fillna(0.0)
    frame["crop_group"] = frame["crop"].map(crop_group)
    frame["target_claim"] = (indemnity > 0).astype(int)
    frame["target_rain_claim"] = (
        (indemnity > 0) & frame["event_category"].isin(RAIN_EVENTS)
    ).astype(int)
    return frame


def sample_policies(
    session: Session, n: int = 3000, seed: int = 42, frame: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Amostra estratificada por **UF × ano × cultura × rótulo**, com alocação proporcional.

    Proporcional, não balanceada: a taxa real de sinistro (~16%) precisa sobreviver à amostragem,
    porque é ela que a D3 tem de enfrentar. O arredondamento usa **maiores restos**, para o total
    bater com `n` sem distorcer os estratos pequenos.
    """
    population = load_labelled_policies(session) if frame is None else _with_labels(frame)
    if population.empty:
        return population
    if n >= len(population):
        return population.reset_index(drop=True)

    strata_keys = ["state", "policy_year", "crop_group", "target_claim"]
    sizes = population.groupby(strata_keys, dropna=False).size()
    quotas = _largest_remainder(sizes, n)

    rng = np.random.default_rng(seed)
    chunks: list[pd.DataFrame] = []
    for key, group in population.groupby(strata_keys, dropna=False, sort=True):
        quota = quotas.get(key, 0)
        if quota:
            chunks.append(group.sample(n=quota, random_state=rng.integers(0, 2**31 - 1)))

    sample = pd.concat(chunks, ignore_index=True) if chunks else population.iloc[0:0]
    # Embaralha para a ordem não carregar a estratificação (o corte temporal é feito na D3).
    return sample.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def _largest_remainder(sizes: pd.Series, total: int) -> dict:
    """Divide `total` entre os estratos na proporção do tamanho de cada um.

    Cada estrato leva a parte inteira da sua cota; as vagas que sobram vão para os maiores restos.
    Sem isso, `round()` por estrato erraria o total em dezenas de linhas.
    """
    exact = sizes / sizes.sum() * total
    floors = np.floor(exact).astype(int)
    remaining = total - int(floors.sum())
    if remaining > 0:
        order = (exact - floors).sort_values(ascending=False).index[:remaining]
        floors.loc[order] += 1
    # Nunca pedir mais linhas do que o estrato tem.
    floors = np.minimum(floors, sizes)
    return {key: int(value) for key, value in floors.items() if value > 0}


# --- 2. Relevo ----------------------------------------------------------------------------------


def property_bbox(lat: float, lon: float, half_deg: float = TERRAIN_HALF_DEG) -> BBox:
    """Caixa de ±`half_deg` ao redor do ponto da propriedade (escopo da D2: ±0,005°)."""
    return BBox(
        north=lat + half_deg,
        south=lat - half_deg,
        west=lon - half_deg,
        east=lon + half_deg,
    )


def terrain_grid_points(lat: float, lon: float) -> tuple[list[float], list[float]]:
    """Os 9 pontos da grade 3 × 3, achatados na ordem de leitura (norte→sul, oeste→leste).

    Usa `build_grid` do W2 para manter a convenção de **centros de célula** (regras-de-risco §1).
    """
    bbox = property_bbox(lat, lon)
    lats, lons = build_grid(bbox, n=TERRAIN_GRID_SIZE)
    return (
        [latitude for latitude in lats for _ in lons],
        [longitude for _ in lats for longitude in lons],
    )


def terrain_features(lat: float, lon: float, elevations: Sequence[float]) -> dict:
    """Features de relevo a partir das 9 elevações da grade (reaproveita as funções do W2)."""
    expected = TERRAIN_GRID_SIZE**2
    if len(elevations) != expected:
        raise ValueError(
            f"A grade 3 × 3 precisa de {expected} elevações; vieram {len(elevations)}."
        )

    grid = np.asarray(elevations, dtype=float).reshape(TERRAIN_GRID_SIZE, TERRAIN_GRID_SIZE)
    bbox = property_bbox(lat, lon)
    dx_m, dy_m = cell_size_m(bbox, n=TERRAIN_GRID_SIZE)
    slope_deg, aspect_deg = compute_slope_aspect(grid, dx_m, dy_m)
    classes = classify_cells(grid)

    center = TERRAIN_GRID_SIZE // 2
    return {
        "slope_mean_deg": float(slope_deg.mean()),
        "slope_max_deg": float(slope_deg.max()),
        "elevation_mean_m": float(grid.mean()),
        "elevation_range_m": float(grid.max() - grid.min()),
        # Orientação da célula central: é onde fica a sede informada na apólice.
        "aspect_label": aspect_label(float(aspect_deg[center, center])).value,
        "pct_lowland": float((classes == TerrainClass.LOWLAND.value).mean() * 100.0),
        "pct_exposed": float((classes == TerrainClass.EXPOSED.value).mean() * 100.0),
    }


# --- 3. Clima -----------------------------------------------------------------------------------


def weather_cell(lat: float, lon: float, step: float = WEATHER_CELL_DEG) -> tuple[float, float]:
    """Centro da célula grossa que agrupa as chamadas de clima (~27 km)."""
    return (round(round(lat / step) * step, 4), round(round(lon / step) * step, 4))


def weather_window(start: date, end: date) -> tuple[date, date]:
    """Janela **de busca**: meses inteiros cobrindo a vigência, no máximo 12 meses.

    Arredondar para o mês faz muitas apólices caírem na mesma chamada. Os indicadores depois são
    recortados para a vigência exata, então o arredondamento não entra no dataset.
    """
    if end < start:
        raise ValueError("A data final não pode ser anterior à data inicial.")
    window_start = start.replace(day=1)
    window_end = min(end, start + timedelta(days=MAX_WINDOW_DAYS))
    # Último dia do mês de `window_end`.
    first_of_next = (window_end.replace(day=1) + timedelta(days=32)).replace(day=1)
    return window_start, first_of_next - timedelta(days=1)


def clip_to_coverage(days: Iterable[DailyWeather], start: date, end: date) -> list[DailyWeather]:
    """Fica só com os dias **dentro da vigência** — é aqui que o vazamento é barrado."""
    limit = min(end, start + timedelta(days=MAX_WINDOW_DAYS))
    return [day for day in days if start <= day.date <= limit]


def climate_features(days: Sequence[DailyWeather]) -> dict:
    """Indicadores do período, a partir dos dias já recortados para a vigência.

    Grandezas ausentes em todos os dias ficam `None` em vez de virar 0: o modelo precisa saber a
    diferença entre "não choveu" e "não sabemos".
    """
    if not days:
        raise ValueError("Não há nenhum dia de clima dentro da vigência.")

    rain = [day.rain_mm for day in days]
    return {
        "rain_total_mm": round(sum(rain), 1),
        "rain_max_day_mm": round(max(rain), 1),
        "days_rain72h_ge30": sum(1 for day in days if day.rain_72h_mm >= SATURATED_RAIN_72H_MM),
        "days_thunderstorm": sum(1 for day in days if day.thunderstorm),
        "gust_max_kmh": _max_of(day.gust_max_kmh for day in days),
        "temp_max_c": _max_of(day.temp_max_c for day in days),
        "rh_min_pct": _min_of(day.rh_min_pct for day in days),
        "dry_spell_max_days": _longest_dry_spell(days),
        "weather_days": len(days),
    }


def _longest_dry_spell(days: Sequence[DailyWeather]) -> int:
    """Maior sequência de dias com solo seco (`rain_72h_mm < 10`, regras-de-risco §2)."""
    longest = current = 0
    for day in days:
        current = current + 1 if day.rain_72h_mm < DRY_RAIN_72H_MM else 0
        longest = max(longest, current)
    return longest


def _max_of(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(max(present), 1) if present else None


def _min_of(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(min(present), 1) if present else None


# --- 4. Montagem --------------------------------------------------------------------------------


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def iter_elevation_batches(
    points: Sequence[tuple[float, float]],
) -> Iterator[list[tuple[float, float]]]:
    """Fatia os pontos no tamanho de lote que a Elevation API aceita."""
    for start in range(0, len(points), PROPERTIES_PER_ELEVATION_CALL):
        yield list(points[start : start + PROPERTIES_PER_ELEVATION_CALL])


def fetch_elevation_grids(
    points: Sequence[tuple[float, float]], client: OpenMeteoClient, report: DatasetReport
) -> dict[tuple[float, float], list[float]]:
    """Busca a grade 3 × 3 de cada propriedade da lista.

    Propriedade cuja chamada falhou fica de fora do dicionário; quem chama conta o descarte.
    """
    grids: dict[tuple[float, float], list[float]] = {}
    size = TERRAIN_GRID_SIZE**2
    for batch in iter_elevation_batches(points):
        lats: list[float] = []
        lons: list[float] = []
        for lat, lon in batch:
            batch_lats, batch_lons = terrain_grid_points(lat, lon)
            lats += batch_lats
            lons += batch_lons
        try:
            elevations = client.fetch_elevations(lats, lons)
            report.elevation_calls += 1
        except (WeatherUnavailableError, ValueError) as error:
            logger.warning("Elevação indisponível para %d propriedades: %s", len(batch), error)
            continue
        for index, point in enumerate(batch):
            grids[point] = elevations[index * size : (index + 1) * size]
    return grids


class ElevationSource:
    """Grades de elevação buscadas **sob demanda**, não todas de uma vez.

    Buscar a elevação de milhares de propriedades antes de montar a primeira linha significa
    dezenas de minutos sem nada gravado: se o script morre no meio, toda a cota gasta se perde. Sob
    demanda, cada apólice termina o seu próprio caminho e é persistida na hora — que é o que torna
    a retomada útil de verdade.
    """

    def __init__(self, client: OpenMeteoClient, report: DatasetReport) -> None:
        self._client = client
        self._report = report
        self._grids: dict[tuple[float, float], list[float]] = {}
        self._failed: set[tuple[float, float]] = set()

    def grid_for(self, point: tuple[float, float]) -> list[float] | None:
        """A grade do ponto, ou `None` se a Open-Meteo não devolveu.

        Cada ponto é tentado uma vez só: a falha fica lembrada.
        """
        if point in self._grids:
            return self._grids[point]
        if point in self._failed:
            return None

        fetched = fetch_elevation_grids([point], self._client, self._report)
        if point not in fetched:
            self._failed.add(point)
            return None
        self._grids.update(fetched)
        return self._grids[point]


def build_features(
    policies: pd.DataFrame,
    client: OpenMeteoClient,
    report: DatasetReport | None = None,
    on_row: Callable[[dict], None] | None = None,
) -> tuple[pd.DataFrame, DatasetReport]:
    """Monta o dataset: uma linha por apólice, com relevo, clima, contexto e os dois rótulos.

    `on_row` é chamado a cada linha pronta (o script em lote usa para salvar parcial e retomar).
    """
    report = report or DatasetReport()
    report.policies_sampled += len(policies)
    if policies.empty:
        return pd.DataFrame(columns=list(DATASET_COLUMNS)), report

    elevation = ElevationSource(client, report)

    # Uma chamada de clima por (célula grossa × janela de meses), reaproveitada por todas as
    # apólices do grupo. É o que mantém o consumo dentro do limite diário da Open-Meteo.
    daily_cache: dict[tuple, list[DailyWeather] | None] = {}
    rows: list[dict] = []

    for policy in policies.itertuples(index=False):
        point = (policy.lat, policy.lon)
        grid = elevation.grid_for(point)
        if grid is None:
            report.discarded["relevo_indisponivel"] += 1
            continue

        start, end = _as_date(policy.start_date), _as_date(policy.end_date)
        cell = weather_cell(policy.lat, policy.lon)
        window = weather_window(start, end)
        key = (cell, window)

        if key not in daily_cache:
            report.weather_groups += 1
            try:
                hourly = client.fetch_hourly_history(cell[0], cell[1], window[0], window[1])
                report.weather_calls += 1
                daily_cache[key] = aggregate_daily(hourly)
            except (WeatherUnavailableError, ValueError) as error:
                logger.warning("Clima indisponível para %s em %s: %s", cell, window, error)
                daily_cache[key] = None

        daily = daily_cache[key]
        if daily is None:
            report.discarded["clima_indisponivel"] += 1
            continue

        covered = clip_to_coverage(daily, start, end)
        if not covered:
            report.discarded["janela_climatica_vazia"] += 1
            continue

        row = _build_row(policy, grid, covered, start, end)
        rows.append(row)
        report.rows_built += 1
        report.by_state[row["state"]] += 1
        report.by_year[row["policy_year"]] += 1
        if on_row is not None:
            on_row(row)

    frame = pd.DataFrame(rows, columns=list(DATASET_COLUMNS))
    return frame, report


def _build_row(
    policy,
    elevations: Sequence[float],
    days: Sequence[DailyWeather],
    start: date,
    end: date,
) -> dict:
    """Uma linha do dataset. Só entra aqui o que já se sabe no fim da vigência."""
    row: dict = {
        "proposal_id": str(policy.proposal_id),
        "state": policy.state,
        "municipality": policy.municipality,
        "geocode_ibge": policy.geocode_ibge,
        "crop": policy.crop,
        "crop_group": crop_group(policy.crop),
        "area_ha": policy.area_ha,
        "lat": policy.lat,
        "lon": policy.lon,
        # D1 §1: 92% das coordenadas vêm de DMS, com ~30 m de resolução. A D3 precisa saber.
        "coordinate_source": policy.coordinate_source,
        "policy_year": int(policy.policy_year),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "start_month": start.month,
        "coverage_days": (end - start).days,
        "target_claim": int(policy.target_claim),
        "target_rain_claim": int(policy.target_rain_claim),
    }
    row.update(terrain_features(policy.lat, policy.lon, elevations))
    row.update(climate_features(days))
    return row


# --- 5. Gravação --------------------------------------------------------------------------------


def save_dataset(frame: pd.DataFrame, path: str | Path) -> Path:
    """Grava em parquet (pelo sufixo) ou CSV. Devolve o caminho escrito."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False, encoding="utf-8")
    return path

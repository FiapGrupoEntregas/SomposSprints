"""Testes do dataset de treino relevo × clima × sinistro (D2). Tudo offline.

A Open-Meteo é substituída por um `MockTransport` que **sintetiza** a resposta a partir dos
parâmetros pedidos (número de pontos, intervalo de datas). Assim dá para testar o agrupamento de
chamadas e a janela climática sem depender de um payload fixo do tamanho certo.
"""

import json
from datetime import date, datetime, timedelta

import httpx
import pandas as pd
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.clients.open_meteo import OpenMeteoClient
from app.core.cache import TTLCache
from app.core.config import Settings
from app.models import Policy
from app.schemas.weather import DailyWeather
from app.services.dataset import (
    DATASET_COLUMNS,
    MAX_WINDOW_DAYS,
    MIN_COVERAGE_DAYS,
    PROPERTIES_PER_ELEVATION_CALL,
    TERRAIN_GRID_SIZE,
    TERRAIN_HALF_DEG,
    DatasetReport,
    build_features,
    climate_features,
    clip_to_coverage,
    crop_group,
    property_bbox,
    sample_policies,
    save_dataset,
    terrain_features,
    terrain_grid_points,
    weather_cell,
    weather_window,
)

# --- Open-Meteo falsa ----------------------------------------------------------------------------


class FakeOpenMeteo:
    """Responde elevação e histórico horário, e guarda o que foi pedido."""

    def __init__(self) -> None:
        self.elevation_requests: list[httpx.Request] = []
        self.history_requests: list[httpx.Request] = []
        #: chuva (mm) por dia, para os testes de vazamento mexerem em dias específicos
        self.rain_by_day: dict[date, float] = {}
        self.fail_history_for: set[tuple[str, str]] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if "elevation" in request.url.path:
            return self._elevation(request)
        return self._history(request)

    def _elevation(self, request: httpx.Request) -> httpx.Response:
        self.elevation_requests.append(request)
        lats = request.url.params["latitude"].split(",")
        lons = request.url.params["longitude"].split(",")
        # Elevação determinística e inclinada: cresce com a latitude e a longitude.
        elevations = [
            500.0 + abs(float(lat)) * 100.0 + abs(float(lon)) * 10.0
            for lat, lon in zip(lats, lons, strict=True)
        ]
        return httpx.Response(200, json={"elevation": elevations})

    def _history(self, request: httpx.Request) -> httpx.Response:
        self.history_requests.append(request)
        params = request.url.params
        key = (params["latitude"], params["longitude"])
        if key in self.fail_history_for:
            return httpx.Response(500)

        start = date.fromisoformat(params["start_date"])
        end = date.fromisoformat(params["end_date"])
        times: list[str] = []
        rain: list[float] = []
        day = start
        while day <= end:
            for hour in range(24):
                times.append(datetime(day.year, day.month, day.day, hour).isoformat())
                rain.append(self.rain_by_day.get(day, 0.0) / 24.0)
            day += timedelta(days=1)

        size = len(times)
        return httpx.Response(
            200,
            json={
                "latitude": float(params["latitude"]),
                "longitude": float(params["longitude"]),
                "timezone": "America/Sao_Paulo",
                "hourly": {
                    "time": times,
                    "precipitation": rain,
                    "temperature_2m": [25.0] * size,
                    "relative_humidity_2m": [60.0] * size,
                    "weather_code": [0] * size,
                    "wind_speed_10m": [10.0] * size,
                    "wind_gusts_10m": [30.0] * size,
                    "cape": [None] * size,
                    "soil_moisture_0_to_7cm": [0.2] * size,
                },
            },
        )


@pytest.fixture
def fake() -> FakeOpenMeteo:
    return FakeOpenMeteo()


@pytest.fixture
def client(fake: FakeOpenMeteo) -> OpenMeteoClient:
    return OpenMeteoClient(
        settings=Settings(),
        http_client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
        cache=TTLCache(),
    )


# --- Apólices de teste ---------------------------------------------------------------------------


def make_policy(index: int, *, claim: bool = False, event: str = "sem_sinistro") -> Policy:
    """Uma apólice sintética, espalhada pelo Sul/Sudeste."""
    return Policy(
        proposal_id=f"P{index:04d}",
        insurer="Seguradora Fictícia S/A",
        municipality=f"Município {index}",
        state=("PR", "RS", "SP", "GO")[index % 4],
        geocode_ibge=f"41{index:05d}",
        lat=-25.0 - (index % 10) * 0.3,
        lon=-51.0 - (index % 7) * 0.3,
        coordinate_source="dms" if index % 3 else "decimal",
        crop=("Soja", "Milho 2ª safra", "Uva")[index % 3],
        area_ha=100.0 + index,
        coverage_value=500_000.0,
        premium=20_000.0,
        start_date=date(2018 + index % 6, 1 + index % 9, 10),
        end_date=date(2018 + index % 6, 1 + index % 9, 10) + timedelta(days=180),
        policy_year=2018 + index % 6,
        indemnity_value=50_000.0 if claim else None,
        event_category=event,
    )


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        # 200 apólices: 40 com sinistro, das quais 20 de chuva/granizo.
        for index in range(200):
            if index % 5 == 0:
                event = "granizo" if index % 10 == 0 else "seca"
                session.add(make_policy(index, claim=True, event=event))
            else:
                session.add(make_policy(index))
        session.commit()
        yield session


@pytest.fixture
def policies(session: Session) -> pd.DataFrame:
    return sample_policies(session, n=20, seed=7)


# --- Amostragem ----------------------------------------------------------------------------------


def test_sample_policies_respeita_o_tamanho_pedido(session: Session) -> None:
    assert len(sample_policies(session, n=20, seed=7)) == 20


def test_sample_policies_e_deterministica(session: Session) -> None:
    primeira = sample_policies(session, n=20, seed=7)["proposal_id"].tolist()
    segunda = sample_policies(session, n=20, seed=7)["proposal_id"].tolist()
    assert primeira == segunda


def test_sample_policies_nao_repete_apolice(session: Session) -> None:
    sample = sample_policies(session, n=50, seed=7)
    assert sample["proposal_id"].is_unique


def test_sample_policies_preserva_a_taxa_de_sinistro(session: Session) -> None:
    """A amostra é proporcional, não balanceada: a D3 tem de enfrentar o desbalanceamento real."""
    sample = sample_policies(session, n=100, seed=7)
    assert sample["target_claim"].mean() == pytest.approx(0.20, abs=0.05)


def test_sample_policies_exclui_2025_que_nao_tem_rotulo(session: Session) -> None:
    """Vigência em aberto não é rótulo negativo — é censura."""
    session.add(make_policy(9001))
    em_aberto = make_policy(9002)
    em_aberto.proposal_id = "EM_ABERTO"
    em_aberto.policy_year = 2025
    em_aberto.start_date = date(2025, 1, 10)
    em_aberto.end_date = date(2025, 7, 10)
    session.add(em_aberto)
    session.commit()

    todas = sample_policies(session, n=10_000, seed=7)
    assert "EM_ABERTO" not in set(todas["proposal_id"])
    assert (todas["policy_year"] <= 2024).all()


def test_sample_policies_exclui_vigencia_de_um_dia_so(session: Session) -> None:
    """Todo o arquivo de 2006–2015 do PSR tem início igual a fim — não há janela climática.

    Sem este filtro, 29% da amostra (e da cota da Open-Meteo) iria para linhas em que
    `rain_total_mm` é 0 e `dry_spell_max_days` é 1 por construção.
    """
    quebrada = make_policy(8001)
    quebrada.proposal_id = "VIGENCIA_ZERO"
    quebrada.end_date = quebrada.start_date
    session.add(quebrada)
    session.commit()

    todas = sample_policies(session, n=10_000, seed=7)
    assert "VIGENCIA_ZERO" not in set(todas["proposal_id"])

    span = (pd.to_datetime(todas["end_date"]) - pd.to_datetime(todas["start_date"])).dt.days
    assert (span >= MIN_COVERAGE_DAYS).all()


def test_sample_policies_com_n_maior_que_a_populacao_devolve_tudo(session: Session) -> None:
    assert len(sample_policies(session, n=10_000, seed=7)) == 200


def test_sample_policies_cobre_varios_estratos(session: Session) -> None:
    sample = sample_policies(session, n=100, seed=7)
    assert sample["state"].nunique() >= 3
    assert sample["policy_year"].nunique() >= 4


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [("Soja", "Soja"), ("Uva", "Uva"), ("Abacaxi", "outras"), (None, "outras")],
)
def test_crop_group(entrada: object, esperado: str) -> None:
    assert crop_group(entrada) == esperado


# --- Relevo --------------------------------------------------------------------------------------


def test_property_bbox_tem_o_lado_pedido() -> None:
    bbox = property_bbox(-25.0, -51.0)
    assert bbox.north - bbox.south == pytest.approx(2 * TERRAIN_HALF_DEG)
    assert bbox.east - bbox.west == pytest.approx(2 * TERRAIN_HALF_DEG)


def test_terrain_grid_points_sao_centros_de_celula() -> None:
    """Convenção do W2 (regras-de-risco §1): os pontos são centros, não cantos."""
    lats, lons = terrain_grid_points(-25.0, -51.0)
    assert len(lats) == len(lons) == TERRAIN_GRID_SIZE**2
    passo = 2 * TERRAIN_HALF_DEG / TERRAIN_GRID_SIZE
    # O ponto mais ao norte fica meia célula abaixo da borda norte.
    assert max(lats) == pytest.approx(-25.0 + TERRAIN_HALF_DEG - passo / 2)
    assert lats[4] == pytest.approx(-25.0)  # centro da grade 3 × 3
    assert lons[4] == pytest.approx(-51.0)


def test_terrain_features_em_terreno_plano() -> None:
    features = terrain_features(-25.0, -51.0, [700.0] * 9)
    assert features["slope_mean_deg"] == pytest.approx(0.0)
    assert features["elevation_range_m"] == pytest.approx(0.0)
    assert features["pct_lowland"] == 0.0
    assert features["pct_exposed"] == 0.0


def test_terrain_features_em_encosta_voltada_ao_sul() -> None:
    """Elevação caindo de norte para sul: a encosta desce para o sul."""
    elevations = [800.0] * 3 + [750.0] * 3 + [700.0] * 3
    features = terrain_features(-25.0, -51.0, elevations)
    assert features["elevation_range_m"] == pytest.approx(100.0)
    assert features["slope_mean_deg"] > 0
    assert features["aspect_label"] == "S"


def test_terrain_features_recusa_grade_do_tamanho_errado() -> None:
    with pytest.raises(ValueError, match="9 elevações"):
        terrain_features(-25.0, -51.0, [700.0] * 4)


# --- Janela climática e vazamento ----------------------------------------------------------------


def test_weather_window_arredonda_para_meses_inteiros() -> None:
    inicio, fim = weather_window(date(2022, 3, 15), date(2022, 9, 10))
    assert inicio == date(2022, 3, 1)
    assert fim == date(2022, 9, 30)


def test_weather_window_limita_a_doze_meses() -> None:
    inicio, fim = weather_window(date(2020, 1, 10), date(2024, 12, 31))
    assert (fim - inicio).days <= MAX_WINDOW_DAYS + 31


def test_weather_window_recusa_intervalo_invertido() -> None:
    with pytest.raises(ValueError):
        weather_window(date(2022, 9, 10), date(2022, 3, 15))


def test_weather_cell_agrupa_vizinhos() -> None:
    assert weather_cell(-25.01, -51.02) == weather_cell(-25.06, -51.07)
    assert weather_cell(-25.01, -51.02) != weather_cell(-25.9, -51.02)


def _day(d: date, rain: float, rain72: float | None = None) -> DailyWeather:
    return DailyWeather(
        date=d,
        rain_mm=rain,
        rain_72h_mm=rain if rain72 is None else rain72,
        temp_max_c=30.0,
        rh_min_pct=40.0,
        wind_max_kmh=20.0,
        gust_max_kmh=50.0,
        cape_max=None,
        thunderstorm=False,
        soil_moisture=None,
    )


def test_clip_to_coverage_corta_os_dias_de_fora() -> None:
    """O teste que impede o vazamento: nada depois do fim da vigência entra."""
    dias = [_day(date(2022, 3, 1) + timedelta(days=i), 1.0) for i in range(60)]
    recortado = clip_to_coverage(dias, date(2022, 3, 10), date(2022, 3, 20))
    assert [d.date for d in recortado] == [date(2022, 3, 10) + timedelta(days=i) for i in range(11)]


def test_clip_to_coverage_respeita_o_teto_de_doze_meses() -> None:
    dias = [_day(date(2020, 1, 1) + timedelta(days=i), 1.0) for i in range(900)]
    recortado = clip_to_coverage(dias, date(2020, 1, 1), date(2022, 6, 1))
    assert len(recortado) == MAX_WINDOW_DAYS + 1


def test_climate_features_soma_so_o_periodo() -> None:
    dias = [_day(date(2022, 3, 1), 10.0), _day(date(2022, 3, 2), 5.0)]
    features = climate_features(dias)
    assert features["rain_total_mm"] == pytest.approx(15.0)
    assert features["rain_max_day_mm"] == pytest.approx(10.0)
    assert features["weather_days"] == 2


def test_climate_features_conta_dias_encharcados() -> None:
    """`rain_72h_mm ≥ 30` é o limiar de solo encharcado (regras-de-risco §2)."""
    dias = [
        _day(date(2022, 3, 1), 5.0, rain72=29.9),
        _day(date(2022, 3, 2), 5.0, rain72=30.0),
        _day(date(2022, 3, 3), 5.0, rain72=45.0),
    ]
    assert climate_features(dias)["days_rain72h_ge30"] == 2


def test_climate_features_maior_sequencia_seca() -> None:
    """Seco é `rain_72h_mm < 10` (regras-de-risco §2)."""
    valores = [0.0, 0.0, 0.0, 50.0, 0.0, 0.0]
    dias = [
        _day(date(2022, 3, 1) + timedelta(days=i), 0.0, rain72=v) for i, v in enumerate(valores)
    ]
    assert climate_features(dias)["dry_spell_max_days"] == 3


def test_climate_features_recusa_periodo_vazio() -> None:
    with pytest.raises(ValueError):
        climate_features([])


# --- Montagem do dataset -------------------------------------------------------------------------


def test_build_features_gera_uma_linha_por_apolice(
    policies: pd.DataFrame, client: OpenMeteoClient
) -> None:
    frame, report = build_features(policies, client)
    assert len(frame) == len(policies) == 20
    assert report.rows_built == 20
    assert report.rows_discarded == 0
    assert list(frame.columns) == list(DATASET_COLUMNS)
    assert frame["proposal_id"].is_unique


def test_build_features_traz_os_dois_rotulos(
    policies: pd.DataFrame, client: OpenMeteoClient
) -> None:
    frame, _ = build_features(policies, client)
    assert set(frame["target_claim"].unique()) <= {0, 1}
    assert set(frame["target_rain_claim"].unique()) <= {0, 1}
    # Um sinistro de chuva/granizo é sempre um sinistro.
    assert (frame["target_rain_claim"] <= frame["target_claim"]).all()


def test_build_features_registra_a_origem_da_coordenada(
    policies: pd.DataFrame, client: OpenMeteoClient
) -> None:
    """As coordenadas de DMS têm ~30 m de resolução; a D3 precisa poder separar."""
    frame, _ = build_features(policies, client)
    assert set(frame["coordinate_source"].unique()) <= {"dms", "decimal"}


def test_build_features_faz_uma_chamada_de_elevacao_por_coordenada_distinta(
    policies: pd.DataFrame, client: OpenMeteoClient, fake: FakeOpenMeteo
) -> None:
    """A Elevation API recusa chamadas multiponto (429), então vai uma propriedade por chamada.

    O que sobra de economia é a **deduplicação**: duas apólices no mesmo ponto usam uma chamada só.
    """
    distintas = policies.drop_duplicates(subset=["lat", "lon"])
    esperado = -(-len(distintas) // PROPERTIES_PER_ELEVATION_CALL)
    _, report = build_features(policies, client)

    assert report.elevation_calls == esperado == len(fake.elevation_requests)
    assert report.elevation_calls <= len(policies)
    # Cada chamada leva os 9 pontos da grade 3 × 3 de uma propriedade.
    pontos = fake.elevation_requests[0].url.params["latitude"].split(",")
    assert len(pontos) == TERRAIN_GRID_SIZE**2


def test_build_features_agrupa_as_chamadas_de_clima(
    policies: pd.DataFrame, client: OpenMeteoClient
) -> None:
    """Propriedades na mesma célula e na mesma janela dividem uma chamada."""
    _, report = build_features(policies, client)
    assert report.weather_calls == report.weather_groups
    assert report.weather_calls <= len(policies)


def test_build_features_nao_usa_clima_posterior_ao_fim_da_vigencia(
    session: Session, client: OpenMeteoClient, fake: FakeOpenMeteo
) -> None:
    """O teste de vazamento de ponta a ponta.

    Uma apólice de 10/01 a 10/07: 100 mm num dia **dentro** da vigência têm de aparecer em
    `rain_total_mm`; 100 mm num dia **depois** do fim não podem mudar nada. A chamada busca meses
    inteiros, então o dia de fora está na resposta — o recorte é que o mantém fora da feature.
    """
    uma = sample_policies(session, n=200, seed=7).iloc[[0]].copy()
    inicio = date.fromisoformat(str(uma.iloc[0]["start_date"])[:10])
    fim = date.fromisoformat(str(uma.iloc[0]["end_date"])[:10])

    base, _ = build_features(uma, client)

    fake.rain_by_day = {fim + timedelta(days=5): 100.0}
    depois, _ = build_features(uma, _fresh(fake))
    assert depois.iloc[0]["rain_total_mm"] == pytest.approx(base.iloc[0]["rain_total_mm"])

    fake.rain_by_day = {inicio + timedelta(days=5): 100.0}
    dentro, _ = build_features(uma, _fresh(fake))
    assert dentro.iloc[0]["rain_total_mm"] > base.iloc[0]["rain_total_mm"]


def test_chuva_anterior_ao_inicio_conta_para_o_acumulado_de_72h(
    session: Session, client: OpenMeteoClient, fake: FakeOpenMeteo
) -> None:
    """Buscar meses inteiros não é só cache: dá o embalo de 72 h do primeiro dia de vigência.

    `rain_72h_mm` do dia d soma d−2, d−1 e d. No primeiro dia da vigência, d−1 e d−2 são
    **anteriores** ao início — passado, disponível na hora da previsão, e portanto legítimo. Sem a
    janela arredondada para o mês, esses dois dias faltariam e o solo pareceria mais seco do que
    estava.
    """
    uma = sample_policies(session, n=200, seed=7).iloc[[0]].copy()
    inicio = date.fromisoformat(str(uma.iloc[0]["start_date"])[:10])

    base, _ = build_features(uma, client)
    fake.rain_by_day = {inicio - timedelta(days=1): 120.0}
    antes, _ = build_features(uma, _fresh(fake))

    # A chuva de véspera não entra em `rain_total_mm` (está fora da vigência)...
    assert antes.iloc[0]["rain_total_mm"] == pytest.approx(base.iloc[0]["rain_total_mm"])
    # ...mas encharca o solo no primeiro dia coberto.
    assert antes.iloc[0]["days_rain72h_ge30"] > base.iloc[0]["days_rain72h_ge30"]


def _fresh(fake: FakeOpenMeteo) -> OpenMeteoClient:
    """Cliente com cache limpo, para a resposta nova não vir do cache do I1."""
    return OpenMeteoClient(
        settings=Settings(),
        http_client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
        cache=TTLCache(),
    )


def test_build_features_conta_o_descarte_por_clima_indisponivel(
    policies: pd.DataFrame, client: OpenMeteoClient, fake: FakeOpenMeteo
) -> None:
    """Célula sem clima: a linha sai e é contada, nunca some em silêncio."""
    primeira = policies.iloc[0]
    cell = weather_cell(primeira["lat"], primeira["lon"])
    # O cliente formata a coordenada com 6 casas (`_format_coordinate`).
    fake.fail_history_for = {(f"{cell[0]:.6f}", f"{cell[1]:.6f}")}

    frame, report = build_features(policies, client)
    assert report.discarded["clima_indisponivel"] >= 1
    assert len(frame) + report.rows_discarded == len(policies)


def test_build_features_com_amostra_vazia(client: OpenMeteoClient) -> None:
    frame, report = build_features(pd.DataFrame(columns=["lat", "lon"]), client)
    assert frame.empty
    assert list(frame.columns) == list(DATASET_COLUMNS)
    assert report.rows_built == 0


def test_report_fecha_a_conta(policies: pd.DataFrame, client: OpenMeteoClient) -> None:
    _, report = build_features(policies, client)
    assert report.policies_sampled == report.rows_built + report.rows_discarded
    assert report.api_calls == report.elevation_calls + report.weather_calls
    assert "chamadas de clima" in report.render()


def test_on_row_recebe_cada_linha(policies: pd.DataFrame, client: OpenMeteoClient) -> None:
    """É o gancho que dá retomada ao script em lote."""
    vistas: list[dict] = []
    frame, _ = build_features(policies, client, on_row=vistas.append)
    assert len(vistas) == len(frame)
    assert {row["proposal_id"] for row in vistas} == set(frame["proposal_id"])


def test_on_row_permite_retomar_de_onde_parou(
    policies: pd.DataFrame, client: OpenMeteoClient, fake: FakeOpenMeteo
) -> None:
    """Simula a morte do script: metade das linhas gravadas, depois só o que falta."""
    primeira_metade = policies.iloc[:10]
    resto = policies.iloc[10:]

    salvas: list[dict] = []
    build_features(primeira_metade, client, on_row=salvas.append)
    assert len(salvas) == 10

    pendentes = resto[~resto["proposal_id"].isin({row["proposal_id"] for row in salvas})]
    frame, _ = build_features(pendentes, _fresh(fake), on_row=salvas.append)

    assert len(salvas) == 20
    assert len({row["proposal_id"] for row in salvas}) == 20
    assert frame["proposal_id"].is_unique


def test_elevacao_e_buscada_sob_demanda_nao_toda_de_uma_vez(
    policies: pd.DataFrame, fake: FakeOpenMeteo
) -> None:
    """Se a rede cair no meio, as apólices já processadas têm de estar gravadas.

    Com a elevação buscada toda de uma vez no começo, uma queda depois de 40 minutos de chamadas
    não deixaria nenhuma linha — e a retomada não teria de onde retomar.
    """
    salvas: list[dict] = []
    cliente = _fresh(fake)
    original = cliente.fetch_elevations
    chamadas = {"n": 0}

    def cai_depois_de_cinco(*args, **kwargs):
        chamadas["n"] += 1
        if chamadas["n"] > 5:
            raise httpx.ConnectError("rede caiu")
        return original(*args, **kwargs)

    cliente.fetch_elevations = cai_depois_de_cinco  # type: ignore[method-assign]

    with pytest.raises(httpx.ConnectError):
        build_features(policies, cliente, on_row=salvas.append)

    # As primeiras apólices completaram o caminho inteiro antes da queda.
    assert 0 < len(salvas) <= 5
    assert all(row["rain_total_mm"] is not None for row in salvas)


# --- Gravação ------------------------------------------------------------------------------------


def test_save_dataset_em_parquet(policies: pd.DataFrame, client: OpenMeteoClient, tmp_path) -> None:
    frame, _ = build_features(policies, client)
    destino = save_dataset(frame, tmp_path / "dataset.parquet")
    lido = pd.read_parquet(destino)
    assert len(lido) == len(frame)
    assert list(lido.columns) == list(DATASET_COLUMNS)


def test_save_dataset_em_csv(policies: pd.DataFrame, client: OpenMeteoClient, tmp_path) -> None:
    frame, _ = build_features(policies, client)
    destino = save_dataset(frame, tmp_path / "dataset.csv")
    lido = pd.read_csv(destino)
    assert len(lido) == len(frame)


def test_report_as_dict_e_serializavel(policies: pd.DataFrame, client: OpenMeteoClient) -> None:
    _, report = build_features(policies, client)
    assert json.loads(json.dumps(report.as_dict(), default=str))["rows_built"] == 20


def test_dataset_report_vazio_nao_quebra() -> None:
    report = DatasetReport()
    assert report.api_calls == 0
    assert "linhas geradas" in report.render()


def test_the_coverage_cut_is_thirty_days() -> None:
    """Âncora do corte da D2: é ele que impede as ~430 mil vigências de um dia de virarem treino.

    Os testes acima usam a constante como valor esperado, então uma mudança nela passaria
    despercebida — e o dataset mudaria de tamanho sem ninguém notar.
    """
    assert MIN_COVERAGE_DAYS == 30

"""Testes das janelas seguras e das recomendações (W6) — regras-de-risco §7."""

import json
from collections.abc import Callable, Iterator
from datetime import date, datetime, time, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.core.clock import today_local
from app.db import get_session
from app.main import app
from app.repositories import audit as audit_repository
from app.schemas.audit import DecisionType
from app.schemas.recommendation import TimeWindow
from app.schemas.risk import (
    CellRisk,
    DayRisk,
    Hazard,
    HazardResult,
    LevelPercentages,
    RiskLevel,
    SoilState,
)
from app.schemas.terrain import AspectLabel, TerrainClass
from app.schemas.weather import HourlyWeather
from app.services.recommendations import (
    MIN_WINDOW_HOURS,
    NO_RESTRICTIONS_MESSAGE,
    RED_AREAS_STILL_FORBIDDEN_MESSAGE,
    SAFE_GUST_KMH,
    SAFE_RAIN_MM,
    WORK_END_HOUR,
    WORK_START_HOUR,
    build_messages,
    get_recommendations,
    is_safe_hour,
    safe_windows,
)
from app.services.terrain import clear_terrain_cache
from tests.conftest import FIXTURES_DIR
from tests.test_risk_route import forecast_payload
from tests.test_risk_rules import make_cell as make_terrain_cell
from tests.test_risk_rules import make_terrain

TODAY = date(2026, 9, 21)

FARM_ID = "cafe-carmo-de-minas"
RECOMMENDATIONS_URL = f"/api/v1/farms/{FARM_ID}/recommendations"


@pytest.fixture
def real_today() -> date:
    """Hoje em São Paulo, capturado uma vez por teste."""
    return today_local()


@pytest.fixture
def audit_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    clear_terrain_cache()
    yield
    clear_terrain_cache()
    app.dependency_overrides.clear()


@pytest.fixture
def recommendation_api(
    make_client: Callable[..., OpenMeteoClient], audit_session: Session, real_today: date
) -> Callable[..., TestClient]:
    """`TestClient` com a Open-Meteo mockada pelas fixtures reais do I1/W2."""

    def factory(failing: bool = False) -> TestClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if failing:
                return httpx.Response(500, text="erro na origem")
            if "elevation" in request.url.path:
                path = FIXTURES_DIR / "terrain_elevation_carmo.json"
                return httpx.Response(200, json=json.loads(path.read_text(encoding="utf-8")))
            return httpx.Response(200, json=forecast_payload(real_today))

        app.dependency_overrides[get_open_meteo_client] = lambda: make_client(handler)
        app.dependency_overrides[get_session] = lambda: audit_session
        return TestClient(app)

    return factory


def build_hourly(
    rain: dict[int, float] | None = None,
    gusts: dict[int, float] | None = None,
    codes: dict[int, int] | None = None,
    day: date = TODAY,
) -> HourlyWeather:
    """Um dia de 24 h calmo, com as exceções informadas por hora."""
    rain, gusts, codes = rain or {}, gusts or {}, codes or {}
    times = [datetime.combine(day, time(hour=hour)) for hour in range(24)]
    return HourlyWeather(
        latitude=-22.12,
        longitude=-45.13,
        timezone="America/Sao_Paulo",
        time=times,
        temperature_2m=[22.0] * 24,
        relative_humidity_2m=[60.0] * 24,
        precipitation=[rain.get(hour, 0.0) for hour in range(24)],
        weather_code=[codes.get(hour, 0) for hour in range(24)],
        wind_speed_10m=[10.0] * 24,
        wind_gusts_10m=[gusts.get(hour, 20.0) for hour in range(24)],
        cape=[100.0] * 24,
        soil_moisture=[0.3] * 24,
    )


def make_cell_risk(
    row: int = 0,
    col: int = 0,
    level: RiskLevel = RiskLevel.GREEN,
    reasons: list[HazardResult] | None = None,
) -> CellRisk:
    return CellRisk(row=row, col=col, level=level, reasons=reasons or [])


def reason(hazard: Hazard, level: RiskLevel, message: str = "motivo") -> HazardResult:
    return HazardResult(hazard=hazard, level=level, message=message)


def make_day_risk(
    cells: list[CellRisk],
    soil_state: SoilState = SoilState.SATURATED,
    tilt_limit_deg: float = 10.0,
    rain_mm: float = 3.0,
    rain_72h_mm: float = 42.0,
    gust_max_kmh: float | None = 20.0,
    day: date = TODAY,
) -> DayRisk:
    levels = [cell.level for cell in cells]
    worst = (
        RiskLevel.RED
        if RiskLevel.RED in levels
        else RiskLevel.YELLOW
        if RiskLevel.YELLOW in levels
        else RiskLevel.GREEN
    )
    return DayRisk(
        date=day,
        rain_mm=rain_mm,
        rain_72h_mm=rain_72h_mm,
        gust_max_kmh=gust_max_kmh,
        soil_state=soil_state,
        tilt_limit_deg=tilt_limit_deg,
        worst_level=worst,
        pct_levels=LevelPercentages(green=0.0, yellow=0.0, red=100.0),
        cells=cells,
    )


def terrain_with(*aspects: AspectLabel):
    """Relevo com uma célula por orientação informada, na coluna 0, 1, 2…"""
    return make_terrain(
        [
            make_terrain_cell(slope_deg=13.0, col=index, terrain_class=TerrainClass.SLOPE)
            for index, _ in enumerate(aspects)
        ]
    ).model_copy(
        update={
            "cells": [
                make_terrain_cell(slope_deg=13.0, col=index).model_copy(
                    update={"aspect_label": aspect}
                )
                for index, aspect in enumerate(aspects)
            ]
        }
    )


# --- §7 Hora segura ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rain_mm", "gust_kmh", "weather_code", "expected"),
    [
        (0.0, 20.0, 0, True),
        # Chuva: 0,4 mm ainda é segura, 0,5 já não é
        (SAFE_RAIN_MM - 0.1, 20.0, 0, True),
        (SAFE_RAIN_MM, 20.0, 0, False),
        # Rajada: 44,9 km/h ainda é segura, 45 já não é
        (0.0, SAFE_GUST_KMH - 0.1, 0, True),
        (0.0, SAFE_GUST_KMH, 0, False),
        # Tempestade em qualquer intensidade
        (0.0, 20.0, 95, False),
        (0.0, 20.0, 96, False),
        (0.0, 20.0, 99, False),
        (0.0, 20.0, 63, True),
        # Dado ausente nunca vira permissão
        (None, 20.0, 0, False),
        (0.0, None, 0, False),
        (0.0, 20.0, None, False),
    ],
)
def test_is_safe_hour_boundaries(
    rain_mm: float | None, gust_kmh: float | None, weather_code: int | None, expected: bool
) -> None:
    """regras-de-risco §7: chuva < 0,5 mm, rajada < 45 km/h e sem código de tempestade."""
    assert is_safe_hour(rain_mm, gust_kmh, weather_code) is expected


# --- §7 Janelas --------------------------------------------------------------------------------


def test_a_calm_day_is_one_window_from_six_to_eighteen() -> None:
    windows = safe_windows(build_hourly(), TODAY)

    assert windows == [TimeWindow(start=time(WORK_START_HOUR), end=time(WORK_END_HOUR))]


def test_rain_splits_the_day_in_two_windows() -> None:
    windows = safe_windows(build_hourly(rain={12: 2.0, 13: 3.0}), TODAY)

    assert windows == [
        TimeWindow(start=time(6), end=time(12)),
        TimeWindow(start=time(14), end=time(18)),
    ]


def test_a_window_shorter_than_two_hours_is_discarded() -> None:
    """regras-de-risco §7: janela útil tem pelo menos 2 h."""
    # Sobram as 6h–7h (1 h, descartada) e as 9h–18h.
    windows = safe_windows(build_hourly(rain={7: 1.0, 8: 1.0}), TODAY)

    assert windows == [TimeWindow(start=time(9), end=time(18))]
    assert all(window.hours >= MIN_WINDOW_HOURS for window in windows)


def test_a_two_hour_window_is_kept() -> None:
    """A borda exata da duração mínima."""
    blocked = dict.fromkeys(range(8, 18), 1.0)

    windows = safe_windows(build_hourly(rain=blocked), TODAY)

    assert windows == [TimeWindow(start=time(6), end=time(8))]


def test_windows_never_leave_the_working_hours() -> None:
    """Madrugada calma não vira janela: a operação é das 06h às 18h."""
    windows = safe_windows(build_hourly(), TODAY)

    assert windows[0].start == time(WORK_START_HOUR)
    assert windows[-1].end == time(WORK_END_HOUR)


def test_a_stormy_afternoon_has_no_window_after_it_starts() -> None:
    codes = dict.fromkeys(range(13, 24), 95)

    windows = safe_windows(build_hourly(codes=codes), TODAY)

    assert windows == [TimeWindow(start=time(6), end=time(13))]


def test_a_day_without_any_safe_hour_has_no_window() -> None:
    assert safe_windows(build_hourly(rain=dict.fromkeys(range(24), 5.0)), TODAY) == []


def test_another_day_does_not_leak_into_the_window() -> None:
    hourly = build_hourly(day=TODAY + timedelta(days=1))

    assert safe_windows(hourly, TODAY) == []


def test_a_window_must_end_after_it_starts() -> None:
    with pytest.raises(ValueError, match="Janela inválida"):
        TimeWindow(start=time(10), end=time(10))


# --- Frases ------------------------------------------------------------------------------------


def test_a_green_day_says_there_are_no_restrictions() -> None:
    """Critério de aceite: dia sem célula 🟡 ou 🔴 → só a frase "Sem restrições…"."""
    day_risk = make_day_risk([make_cell_risk(), make_cell_risk(col=1)])

    assert build_messages(day_risk, terrain_with(AspectLabel.N, AspectLabel.N)) == [
        NO_RESTRICTIONS_MESSAGE
    ]


def test_the_rollover_message_names_the_dominant_slope_direction() -> None:
    """Critério de aceite: a direção citada bate com a maioria das células 🔴."""
    cells = [
        make_cell_risk(
            col=0, level=RiskLevel.RED, reasons=[reason(Hazard.ROLLOVER, RiskLevel.RED)]
        ),
        make_cell_risk(
            col=1, level=RiskLevel.RED, reasons=[reason(Hazard.ROLLOVER, RiskLevel.RED)]
        ),
        make_cell_risk(
            col=2, level=RiskLevel.RED, reasons=[reason(Hazard.ROLLOVER, RiskLevel.RED)]
        ),
    ]
    terrain = terrain_with(AspectLabel.S, AspectLabel.S, AspectLabel.NE)

    messages = build_messages(make_day_risk(cells), terrain)

    assert messages[0] == (
        "Evite operar máquinas na encosta sul (inclinação acima de 10°, solo encharcado)."
    )


def test_only_the_red_cells_decide_the_direction() -> None:
    """Uma célula 🟡 virada para outro lado não pode mudar a orientação citada."""
    cells = [
        make_cell_risk(
            col=0, level=RiskLevel.RED, reasons=[reason(Hazard.ROLLOVER, RiskLevel.RED)]
        ),
        make_cell_risk(
            col=1, level=RiskLevel.YELLOW, reasons=[reason(Hazard.ROLLOVER, RiskLevel.YELLOW)]
        ),
        make_cell_risk(
            col=2, level=RiskLevel.YELLOW, reasons=[reason(Hazard.ROLLOVER, RiskLevel.YELLOW)]
        ),
    ]
    terrain = terrain_with(AspectLabel.NO, AspectLabel.L, AspectLabel.L)

    assert "noroeste" in build_messages(make_day_risk(cells), terrain)[0]


def test_the_bogging_message_is_short_when_it_is_only_the_lowland() -> None:
    cells = [make_cell_risk(level=RiskLevel.RED, reasons=[reason(Hazard.BOGGING, RiskLevel.RED)])]

    messages = build_messages(make_day_risk(cells, rain_mm=3.0), terrain_with(AspectLabel.N))

    assert "Risco de atolamento nas baixadas: evite tráfego pesado." in messages


def test_the_bogging_message_cites_both_causes_when_both_are_present() -> None:
    """Pedido da revisão da W3: com baixada **e** chuva forte, a frase diz as duas coisas."""
    cells = [make_cell_risk(level=RiskLevel.RED, reasons=[reason(Hazard.BOGGING, RiskLevel.RED)])]

    messages = build_messages(
        make_day_risk(cells, rain_mm=62.0, rain_72h_mm=80.0), terrain_with(AspectLabel.N)
    )

    assert messages[0] == (
        "Risco de atolamento: 62 mm de chuva no dia e baixadas encharcadas "
        "(80 mm em 72 h). Evite tráfego pesado."
    )


def test_the_lightning_message_appears_even_in_yellow() -> None:
    """Tempestade é para suspender atividade, não para "operar com cuidado"."""
    cells = [
        make_cell_risk(level=RiskLevel.YELLOW, reasons=[reason(Hazard.LIGHTNING, RiskLevel.YELLOW)])
    ]

    messages = build_messages(make_day_risk(cells), terrain_with(AspectLabel.N))

    assert (
        "Previsão de tempestade: suspenda as atividades em áreas abertas e topos de morro."
        in messages
    )


def test_the_wind_message_carries_the_gust() -> None:
    cells = [make_cell_risk(level=RiskLevel.RED, reasons=[reason(Hazard.WIND, RiskLevel.RED)])]

    messages = build_messages(make_day_risk(cells, gust_max_kmh=72.0), terrain_with(AspectLabel.N))

    assert (
        "Rajadas de até 72 km/h: evite pulverização e máquinas altas nas áreas expostas."
        in messages
    )


def test_the_fire_message_appears_in_yellow_too() -> None:
    cells = [
        make_cell_risk(level=RiskLevel.YELLOW, reasons=[reason(Hazard.FIRE, RiskLevel.YELLOW)])
    ]

    messages = build_messages(make_day_risk(cells), terrain_with(AspectLabel.N))

    assert (
        "Condição de incêndio (regra dos 30): redobre a atenção com a colheitadeira e "
        "deixe o aceiro pronto." in messages
    )


def test_a_red_day_warns_that_the_window_does_not_free_the_red_areas() -> None:
    """§7: as áreas 🔴 de capotamento continuam proibidas **dentro** da janela."""
    cells = [make_cell_risk(level=RiskLevel.RED, reasons=[reason(Hazard.ROLLOVER, RiskLevel.RED)])]

    messages = build_messages(make_day_risk(cells), terrain_with(AspectLabel.N))

    assert messages[-1] == RED_AREAS_STILL_FORBIDDEN_MESSAGE


def test_a_yellow_only_day_does_not_carry_the_red_warning() -> None:
    """Um dia 100% 🟡 **tem** restrição: ele não pode cair no texto de dia limpo.

    O teste antigo só verificava a ausência da frase vermelha — e foi por isso que o defeito
    passou. Agora ele afirma também o que **tem** que aparecer.
    """
    cells = [
        make_cell_risk(level=RiskLevel.YELLOW, reasons=[reason(Hazard.ROLLOVER, RiskLevel.YELLOW)])
    ]

    messages = build_messages(make_day_risk(cells), terrain_with(AspectLabel.N))

    assert RED_AREAS_STILL_FORBIDDEN_MESSAGE not in messages
    assert NO_RESTRICTIONS_MESSAGE not in messages
    assert messages == [
        "Reduza a velocidade e evite manobras na encosta norte: a inclinação está perto do "
        "limite de 10° (solo encharcado)."
    ]


def test_several_hazards_produce_several_messages() -> None:
    cells = [
        make_cell_risk(
            level=RiskLevel.RED,
            reasons=[
                reason(Hazard.ROLLOVER, RiskLevel.RED),
                reason(Hazard.LIGHTNING, RiskLevel.RED),
                reason(Hazard.FIRE, RiskLevel.RED),
            ],
        )
    ]

    messages = build_messages(make_day_risk(cells), terrain_with(AspectLabel.S))

    assert len(messages) == 4  # capotamento, raio, incêndio e o aviso das áreas vermelhas
    assert messages[-1] == RED_AREAS_STILL_FORBIDDEN_MESSAGE


# --- Janela de dias ------------------------------------------------------------------------------


@pytest.mark.parametrize("days", [0, 3, -1])
def test_the_recommendation_covers_only_today_and_tomorrow(days: int) -> None:
    with pytest.raises(ValueError, match="hoje e amanhã"):
        get_recommendations(None, None, days=days)  # type: ignore[arg-type]


# --- GET /api/v1/farms/{farm_id}/recommendations -------------------------------------------------


def test_the_route_returns_today_and_tomorrow(
    recommendation_api: Callable[..., TestClient], real_today: date
) -> None:
    client = recommendation_api()

    response = client.get(RECOMMENDATIONS_URL)

    assert response.status_code == 200
    body = response.json()
    assert [day["date"] for day in body] == [
        (real_today + timedelta(days=offset)).isoformat() for offset in range(2)
    ]
    assert all(set(day) == {"date", "windows", "messages"} for day in body)
    assert all(day["messages"] for day in body)


def test_the_route_accepts_only_today(recommendation_api: Callable[..., TestClient]) -> None:
    body = recommendation_api().get(RECOMMENDATIONS_URL, params={"days": 1}).json()

    assert len(body) == 1


@pytest.mark.parametrize("days", [0, 3, -1])
def test_the_route_refuses_a_longer_window(
    recommendation_api: Callable[..., TestClient], days: int
) -> None:
    """§7: a janela vale para hoje e amanhã; 3 dias não é promessa que dê para cumprir."""
    assert recommendation_api().get(RECOMMENDATIONS_URL, params={"days": days}).status_code == 422


def test_the_route_accepts_a_scenario(recommendation_api: Callable[..., TestClient]) -> None:
    body = (
        recommendation_api()
        .get(RECOMMENDATIONS_URL, params={"scenario": "heatwave", "days": 2})
        .json()
    )

    assert any("incêndio" in message for day in body for message in day["messages"])


def test_an_unknown_scenario_returns_422(recommendation_api: Callable[..., TestClient]) -> None:
    assert (
        recommendation_api().get(RECOMMENDATIONS_URL, params={"scenario": "chuva"}).status_code
        == 422
    )


def test_an_unknown_farm_returns_404(recommendation_api: Callable[..., TestClient]) -> None:
    response = recommendation_api().get("/api/v1/farms/fazenda-inexistente/recommendations")

    assert response.status_code == 404
    assert response.json()["detail"] == "Fazenda não encontrada"


def test_open_meteo_down_returns_503(recommendation_api: Callable[..., TestClient]) -> None:
    client = recommendation_api(failing=True)

    response = client.get(RECOMMENDATIONS_URL)

    assert response.status_code == 503
    assert response.json()["detail"] == "Serviço de clima indisponível"


def test_the_recommendation_is_recorded_in_the_audit_trail(
    recommendation_api: Callable[..., TestClient], audit_session: Session
) -> None:
    """I5: o que o operador leu é parte do que precisa ser auditável."""
    client = recommendation_api()

    response = client.get(RECOMMENDATIONS_URL)

    rows = audit_repository.list_decisions(audit_session, decision_type=DecisionType.RECOMMENDATION)
    assert len(rows) == 1
    # Tipo próprio: quem filtra `risk_score` não recebe dois formatos de `output`.
    assert (
        audit_repository.list_decisions(audit_session, decision_type=DecisionType.RISK_SCORE) == []
    )
    output = json.loads(rows[0].output_json)
    assert output["days"][0]["messages"] == response.json()[0]["messages"]
    assert rows[0].request_id == response.headers["X-Request-ID"]


# --- Nenhum dia com alerta pode ser reportado como limpo (defeito achado na revisão da W6) ------


@pytest.mark.parametrize(
    "hazard",
    [Hazard.ROLLOVER, Hazard.BOGGING, Hazard.LIGHTNING, Hazard.WIND, Hazard.FIRE],
)
def test_no_yellow_hazard_is_reported_as_a_clean_day(hazard: Hazard) -> None:
    """Todo perigo em 🟡 vira frase: 🟡 é "operar com cuidado", não "sem restrição"."""
    cells = [make_cell_risk(level=RiskLevel.YELLOW, reasons=[reason(hazard, RiskLevel.YELLOW)])]

    messages = build_messages(make_day_risk(cells), terrain_with(AspectLabel.N))

    assert messages
    assert NO_RESTRICTIONS_MESSAGE not in messages


@pytest.mark.parametrize(
    "hazard",
    [Hazard.ROLLOVER, Hazard.BOGGING, Hazard.LIGHTNING, Hazard.WIND, Hazard.FIRE],
)
def test_no_red_hazard_is_reported_as_a_clean_day(hazard: Hazard) -> None:
    cells = [make_cell_risk(level=RiskLevel.RED, reasons=[reason(hazard, RiskLevel.RED)])]

    messages = build_messages(make_day_risk(cells), terrain_with(AspectLabel.N))

    assert NO_RESTRICTIONS_MESSAGE not in messages
    assert messages[-1] == RED_AREAS_STILL_FORBIDDEN_MESSAGE


def test_a_hazard_without_a_template_still_warns() -> None:
    """Rede de segurança: um perigo futuro sem frase própria não pode virar "sem restrições"."""
    # Simula um perigo que o motor conhece mas a W6 ainda não traduziu.
    cells = [
        make_cell_risk(
            level=RiskLevel.YELLOW,
            reasons=[HazardResult(hazard=Hazard.BOGGING, level=RiskLevel.GREEN, message="x")],
        )
    ]

    messages = build_messages(make_day_risk(cells), terrain_with(AspectLabel.N))

    assert NO_RESTRICTIONS_MESSAGE not in messages
    assert "Opere com atenção" in messages[0]


def test_the_yellow_wind_message_is_softer_than_the_red_one() -> None:
    yellow = build_messages(
        make_day_risk(
            [
                make_cell_risk(
                    level=RiskLevel.YELLOW, reasons=[reason(Hazard.WIND, RiskLevel.YELLOW)]
                )
            ],
            gust_max_kmh=50.0,
        ),
        terrain_with(AspectLabel.N),
    )
    red = build_messages(
        make_day_risk(
            [make_cell_risk(level=RiskLevel.RED, reasons=[reason(Hazard.WIND, RiskLevel.RED)])],
            gust_max_kmh=70.0,
        ),
        terrain_with(AspectLabel.N),
    )

    assert yellow[0] == "Rajadas de até 50 km/h: atenção com pulverização e máquinas altas."
    assert red[0].startswith("Rajadas de até 70 km/h: evite pulverização")


def test_the_wind_message_is_skipped_without_a_measured_gust() -> None:
    """`is not None`, não booleano: rajada de 0 km/h é medida, e ausência de medida não é 0."""
    cells = [
        make_cell_risk(level=RiskLevel.YELLOW, reasons=[reason(Hazard.WIND, RiskLevel.YELLOW)])
    ]

    messages = build_messages(make_day_risk(cells, gust_max_kmh=None), terrain_with(AspectLabel.N))

    assert not any("Rajadas" in message for message in messages)
    assert NO_RESTRICTIONS_MESSAGE not in messages

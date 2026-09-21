"""Testes do limite do dia de um equipamento (W4) — regras-de-risco §4 e contrato-mqtt."""

from datetime import date, datetime

import pytest

from app.core.clock import LOCAL_TIMEZONE
from app.schemas.farm import BBox, Device, Farm, LatLon
from app.schemas.risk import DayRisk, LevelPercentages, RiskLevel, Scenario, SoilState
from app.services.limits import (
    WARN_RATIO,
    compute_device_limit,
    device_reference_tilt_limit_deg,
    end_of_day_epoch,
    limit_reason,
    strip_accents,
    to_config_message,
)

FARM_ID = "cafe-carmo-de-minas"
DEVICE_ID = "tractor-01"
DAY = date(2026, 9, 21)


def make_device(base_tilt_limit_deg: float = 15.0, device_id: str = DEVICE_ID) -> Device:
    return Device(
        device_id=device_id,
        name="Trator cafeeiro 01",
        type="tractor",
        base_tilt_limit_deg=base_tilt_limit_deg,
    )


def make_farm(*devices: Device, reference_tilt_limit_deg: float = 15.0) -> Farm:
    return Farm(
        id=FARM_ID,
        name="Sítio Café da Serra (exemplo)",
        municipality="Carmo de Minas",
        state="MG",
        crop="café",
        center=LatLon(lat=-22.12, lon=-45.13),
        bbox=BBox(north=-22.115, south=-22.125, west=-45.135, east=-45.125),
        reference_tilt_limit_deg=reference_tilt_limit_deg,
        devices=list(devices) if devices else [make_device()],
    )


def make_day(
    soil_state: SoilState = SoilState.SATURATED,
    rain_72h_mm: float = 42.0,
    worst_level: RiskLevel = RiskLevel.RED,
    wind_max_kmh: float | None = 22.0,
    day: date = DAY,
) -> DayRisk:
    return DayRisk(
        date=day,
        rain_mm=14.5,
        rain_72h_mm=rain_72h_mm,
        wind_max_kmh=wind_max_kmh,
        gust_max_kmh=35.0,
        temp_max_c=27.0,
        soil_state=soil_state,
        tilt_limit_deg=10.0,
        worst_level=worst_level,
        pct_levels=LevelPercentages(green=10.0, yellow=20.0, red=70.0),
    )


# --- L_ref do equipamento (≠ do mapa da fazenda) -----------------------------------------------


def test_the_device_limit_uses_its_own_base_limit() -> None:
    """regras-de-risco §4: o limite de **um equipamento** é o `base_tilt_limit_deg` dele."""
    fragile = make_device(base_tilt_limit_deg=12.0, device_id="tractor-02")
    farm = make_farm(make_device(15.0), fragile, reference_tilt_limit_deg=15.0)

    assert device_reference_tilt_limit_deg(fragile, farm) == 12.0
    # A máquina de 15° na mesma fazenda continua com o limite dela: aqui **não** vale o menor.
    assert device_reference_tilt_limit_deg(make_device(15.0), farm) == 15.0


def test_without_a_device_the_farm_reference_is_used() -> None:
    """regras-de-risco §4: sem `base_tilt_limit_deg`, vale o `reference_tilt_limit_deg`."""
    farm = make_farm(reference_tilt_limit_deg=13.0)

    assert device_reference_tilt_limit_deg(None, farm) == 13.0


# --- Cálculo -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("soil", "rain_72h_mm", "expected_deg"),
    [
        (SoilState.DRY, 1.6, 15.0),
        (SoilState.MOIST, 15.0, 12.5),
        (SoilState.SATURATED, 42.0, 10.0),
    ],
)
def test_the_limit_matches_the_document(
    soil: SoilState, rain_72h_mm: float, expected_deg: float
) -> None:
    """Critério de aceite: 15 / 12,5 / 10 para seco / úmido / encharcado, com L_ref 15°."""
    limit = compute_device_limit(
        make_device(15.0), make_farm(), make_day(soil_state=soil, rain_72h_mm=rain_72h_mm)
    )

    assert limit.tilt_limit_deg == expected_deg
    assert limit.reference_tilt_limit_deg == 15.0
    assert limit.soil_state is soil


def test_a_more_fragile_machine_gets_a_lower_limit() -> None:
    fragile = make_device(base_tilt_limit_deg=12.0)

    limit = compute_device_limit(fragile, make_farm(fragile), make_day())

    # 12 × 0,67 = 8,04 → floor_0.5 = 8,0
    assert limit.tilt_limit_deg == 8.0
    assert limit.reference_tilt_limit_deg == 12.0


def test_the_limit_carries_everything_the_config_needs() -> None:
    limit = compute_device_limit(
        make_device(), make_farm(), make_day(), scenario=Scenario.HEAVY_RAIN
    )

    assert limit.device_id == DEVICE_ID
    assert limit.farm_id == FARM_ID
    assert limit.date == DAY
    assert limit.scenario is Scenario.HEAVY_RAIN
    assert limit.warn_ratio == WARN_RATIO == 0.8
    assert limit.risk_level is RiskLevel.RED
    assert limit.rain_72h_mm == 42.0
    assert limit.wind_max_kmh == 22.0


def test_valid_until_is_midnight_in_sao_paulo() -> None:
    """contrato-mqtt: `valid_until` é o fim do dia; depois disso o ESP32 avisa no Serial."""
    limit = compute_device_limit(make_device(), make_farm(), make_day())

    ends_at = datetime.fromtimestamp(limit.valid_until, tz=LOCAL_TIMEZONE)

    assert ends_at == datetime(2026, 9, 22, 0, 0, tzinfo=LOCAL_TIMEZONE)
    assert end_of_day_epoch(DAY) == limit.valid_until


# --- `reason` sem acento -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("soil", "rain_72h_mm", "expected"),
    [
        (SoilState.DRY, 1.6, "solo seco: 1,6 mm em 72 h"),
        (SoilState.MOIST, 15.0, "solo umido: 15 mm em 72 h"),
        (SoilState.SATURATED, 42.0, "solo encharcado: 42 mm em 72 h"),
    ],
)
def test_the_reason_is_short_and_has_no_accents(
    soil: SoilState, rain_72h_mm: float, expected: str
) -> None:
    """Critério de aceite: a fonte padrão do OLED não tem acento (contrato-mqtt, E7)."""
    reason = limit_reason(make_day(soil_state=soil, rain_72h_mm=rain_72h_mm))

    assert reason == expected
    assert reason.isascii() or all(ord(character) < 256 for character in reason)


def test_no_soil_state_produces_an_accent() -> None:
    """A rede de segurança: `úmido` é o único com acento, e ele precisa sair como `umido`."""
    for soil in SoilState:
        reason = limit_reason(make_day(soil_state=soil))
        assert reason == strip_accents(reason)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("solo úmido", "solo umido"),
        ("inclinação", "inclinacao"),
        ("42 mm em 72 h", "42 mm em 72 h"),
        ("São Paulo é ótimo", "Sao Paulo e otimo"),
    ],
)
def test_strip_accents(text: str, expected: str) -> None:
    assert strip_accents(text) == expected


# --- Conversão para o `config` do contrato --------------------------------------------------------


def test_the_config_message_follows_the_contract() -> None:
    limit = compute_device_limit(make_device(), make_farm(), make_day())

    config = to_config_message(limit)

    assert config.model_dump(exclude_none=True) == {
        "tilt_limit_deg": 10.0,
        "warn_ratio": 0.8,
        "soil_state": "saturated",
        "risk_level": "red",
        "wind_max_kmh": 22.0,
        "valid_until": limit.valid_until,
        "reason": "solo encharcado: 42 mm em 72 h",
    }


def test_the_config_omits_the_wind_when_the_forecast_has_none() -> None:
    """O ESP32 mantém o valor atual do campo que não vier (contrato-mqtt)."""
    limit = compute_device_limit(make_device(), make_farm(), make_day(wind_max_kmh=None))

    assert "wind_max_kmh" not in to_config_message(limit).model_dump(exclude_none=True)

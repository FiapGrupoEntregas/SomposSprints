"""Smoke tests das páginas.

As páginas nunca tocam na rede aqui: as funções do `api_client` são substituídas por
mocks, tanto no caminho feliz quanto no de API fora do ar. Assim a suíte dá o mesmo
resultado com a API local ligada ou desligada.
"""

import datetime as dt
import json
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from services.api_client import (
    MISSING_API_KEY_MESSAGE,
    ApiError,
    ApiKeyError,
    NotFoundError,
)

PAGES = [
    "home.py",
    "risk_map.py",
    "equipment.py",
    "underwriting.py",
    "reports.py",
    "replay.py",
]

# Páginas que dependem da API e precisam avisar em português quando ela não responde.
PAGES_WITH_API = [
    "home.py",
    "risk_map.py",
    "equipment.py",
    "underwriting.py",
    "reports.py",
    "replay.py",
]

# Perigos avaliados pela API (W3 + W7), na ordem em que a tela os oferece.
HAZARD_KEYS = ("rollover", "bogging", "lightning", "wind", "fire")

OFFLINE_MESSAGE = (
    "Não foi possível falar com a API em http://api.test. "
    "Suba a API com `uv run fastapi dev app/main.py` na pasta api/."
)

FARM_LABEL_SORRISO = "Fazenda Planalto de Grãos (exemplo) — Sorriso/MT · soja"

FAKE_FARMS = [
    {
        "id": "cafe-carmo-de-minas",
        "name": "Sítio Café da Serra (exemplo)",
        "municipality": "Carmo de Minas",
        "state": "MG",
        "crop": "café",
    },
    {
        "id": "graos-sorriso",
        "name": "Fazenda Planalto de Grãos (exemplo)",
        "municipality": "Sorriso",
        "state": "MT",
        "crop": "soja",
    },
]


def _detail(summary: dict, lat: float, lon: float) -> dict:
    return {
        **summary,
        "center": {"lat": lat, "lon": lon},
        "bbox": {
            "north": lat + 0.005,
            "south": lat - 0.005,
            "west": lon - 0.005,
            "east": lon + 0.005,
        },
        "reference_tilt_limit_deg": 15.0,
        "devices": [
            {
                "device_id": "tractor-01",
                "name": "Trator 01",
                "type": "tractor",
                "base_tilt_limit_deg": 15.0,
            }
        ],
    }


FAKE_FARM_DETAILS = {
    "cafe-carmo-de-minas": _detail(FAKE_FARMS[0], -22.12, -45.13),
    "graos-sorriso": _detail(FAKE_FARMS[1], -12.55, -55.71),
}


def _cell(row: int, col: int, slope_deg: float, terrain_class: str, elevation_m: float) -> dict:
    lat, lon = -22.12 + row * 0.001, -45.13 + col * 0.001
    return {
        "row": row,
        "col": col,
        "lat": lat,
        "lon": lon,
        "polygon": [
            [lon, lat],
            [lon + 0.001, lat],
            [lon + 0.001, lat - 0.001],
            [lon, lat - 0.001],
        ],
        "elevation_m": elevation_m,
        "slope_deg": slope_deg,
        "aspect_deg": 270.0,
        "aspect_label": "O",
        "terrain_class": terrain_class,
    }


def _terrain(farm_id: str, stats: dict, cells: list[dict]) -> dict:
    return {
        "farm_id": farm_id,
        "grid_size": 10,
        "cell_size_m": {"x_m": 103.1, "y_m": 110.5},
        "stats": stats,
        "cells": cells,
    }


# Relevo variado (números reais de Carmo de Minas).
TERRAIN_HILLY = _terrain(
    "cafe-carmo-de-minas",
    {
        "elevation_min_m": 893.0,
        "elevation_max_m": 1011.0,
        "elevation_range_m": 118.0,
        "slope_max_deg": 21.6,
        "slope_mean_deg": 9.7,
        "pct_slope_lt8": 37.0,
        "pct_slope_8_15": 46.0,
        "pct_slope_gt15": 17.0,
    },
    [
        _cell(0, 0, 2.0, "lowland", 900.0),
        _cell(0, 1, 11.5, "slope", 950.0),
        _cell(1, 0, 21.6, "exposed", 1011.0),
        _cell(1, 1, 7.0, "slope", 930.0),
    ],
)

# Fazenda praticamente plana (números reais de Sorriso): slope_max abaixo do piso da escala.
TERRAIN_FLAT = _terrain(
    "graos-sorriso",
    {
        "elevation_min_m": 362.0,
        "elevation_max_m": 365.0,
        "elevation_range_m": 3.0,
        "slope_max_deg": 0.6,
        "slope_mean_deg": 0.2,
        "pct_slope_lt8": 100.0,
        "pct_slope_8_15": 0.0,
        "pct_slope_gt15": 0.0,
    },
    [_cell(0, 0, 0.6, "flat", 365.0), _cell(0, 1, 0.1, "flat", 362.0)],
)

FAKE_TERRAINS = {
    "cafe-carmo-de-minas": TERRAIN_HILLY,
    "graos-sorriso": TERRAIN_FLAT,
}


# ----------------------------------------------------------------- previsão de risco (W3)

REASON_ROLLOVER_RED = {
    "hazard": "rollover",
    "level": "red",
    "message": "Inclinação de 16,7° acima do limite de 10° (solo encharcado: 31,9 mm em 72 h).",
}
REASON_BOGGING_RED = {
    "hazard": "bogging",
    "level": "red",
    "message": "Baixada com solo encharcado (31,9 mm em 72 h): risco de atolamento.",
}
REASON_LIGHTNING_RED = {
    "hazard": "lightning",
    "level": "red",
    "message": "Tempestade prevista em topo exposto: risco de raio.",
}
REASON_FIRE_YELLOW = {
    "hazard": "fire",
    "level": "yellow",
    "message": "Regra dos 30: 2 de 3 condições (36,5 °C, UR de 24%): risco de incêndio.",
}


def _risk_cells(levels: dict[str, int], reasons_by_level: dict | None = None) -> list[dict]:
    """Células na ordem da grade, com a quantidade pedida de cada nível."""
    reasons_by_level = reasons_by_level or {"red": [REASON_ROLLOVER_RED]}
    cells, position = [], 0
    for level, count in levels.items():
        for _ in range(count):
            reasons = list(reasons_by_level.get(level, []))
            cells.append(
                {
                    "row": position // 10,
                    "col": position % 10,
                    "level": level,
                    "reasons": reasons,
                }
            )
            position += 1
    return cells


def _risk_day(
    offset: int,
    rain_mm: float,
    rain_72h_mm: float,
    soil: str,
    limit: float,
    pct: dict,
    worst: str,
    top_reasons: list[dict],
    reasons_by_level: dict | None = None,
) -> dict:
    return {
        "date": (dt.date(2026, 9, 19) + dt.timedelta(days=offset)).isoformat(),
        "rain_mm": rain_mm,
        "rain_72h_mm": rain_72h_mm,
        "gust_max_kmh": 32.0,
        "wind_max_kmh": 19.0,
        "temp_max_c": 36.5,
        "rh_min_pct": 24.0,
        "cape_max": 2480.0,
        "thunderstorm": True,
        "soil_state": soil,
        "tilt_limit_deg": limit,
        "worst_level": worst,
        "pct_levels": pct,
        "top_reasons": top_reasons,
        "cells": _risk_cells(
            {
                "red": int(pct["red"]),
                "yellow": int(pct["yellow"]),
                "green": int(pct["green"]),
            },
            reasons_by_level,
        ),
    }


DRY_DAY_PCT = {"green": 57.0, "yellow": 25.0, "red": 18.0}
SATURATED_DAY_PCT = {"green": 10.0, "yellow": 20.0, "red": 70.0}
HEAVY_RAIN_PCT = {"green": 0.0, "yellow": 30.0, "red": 70.0}

# Carmo de Minas com a previsão real: o dia +3 é o do solo encharcado e 70% 🔴.
RISK_HILLY = {
    "farm_id": "cafe-carmo-de-minas",
    "generated_at": "2026-09-19T21:00:00-03:00",
    "scenario": None,
    "days": [
        _risk_day(0, 0.1, 1.6, "dry", 15.0, DRY_DAY_PCT, "red", [REASON_ROLLOVER_RED]),
        _risk_day(1, 0.0, 0.2, "dry", 15.0, DRY_DAY_PCT, "red", [REASON_ROLLOVER_RED]),
        _risk_day(2, 28.8, 28.9, "moist", 12.5, DRY_DAY_PCT, "red", [REASON_ROLLOVER_RED]),
        _risk_day(
            3,
            3.1,
            31.9,
            "saturated",
            10.0,
            SATURATED_DAY_PCT,
            "red",
            [REASON_ROLLOVER_RED, REASON_BOGGING_RED, REASON_LIGHTNING_RED, REASON_FIRE_YELLOW],
            reasons_by_level={
                "red": [REASON_ROLLOVER_RED, REASON_LIGHTNING_RED],
                "yellow": [REASON_FIRE_YELLOW],
            },
        ),
        _risk_day(
            4,
            14.5,
            46.4,
            "saturated",
            10.0,
            SATURATED_DAY_PCT,
            "red",
            [REASON_ROLLOVER_RED, REASON_BOGGING_RED],
        ),
        _risk_day(5, 6.9, 24.5, "moist", 12.5, DRY_DAY_PCT, "red", [REASON_ROLLOVER_RED]),
        _risk_day(6, 3.8, 25.2, "moist", 12.5, DRY_DAY_PCT, "red", [REASON_ROLLOVER_RED]),
    ],
}

# Mesma fazenda com o cenário simulado: o dia +2 vai a 0% verde / 30% amarelo / 70% vermelho.
RISK_HILLY_HEAVY_RAIN = {
    **RISK_HILLY,
    "scenario": "heavy_rain",
    "days": [
        *RISK_HILLY["days"][:2],
        _risk_day(
            2,
            73.8,
            73.9,
            "saturated",
            10.0,
            HEAVY_RAIN_PCT,
            "red",
            [REASON_ROLLOVER_RED, REASON_BOGGING_RED],
        ),
        *RISK_HILLY["days"][3:],
    ],
}

# Sorriso: plana, 100% 🟢 nos sete dias, mesmo com chuva forte.
GREEN_PCT = {"green": 100.0, "yellow": 0.0, "red": 0.0}
RISK_FLAT = {
    "farm_id": "graos-sorriso",
    "generated_at": "2026-09-19T21:00:00-03:00",
    "scenario": None,
    "days": [
        _risk_day(offset, 5.0, 12.0, "moist", 15.0, GREEN_PCT, "green", []) for offset in range(7)
    ],
}

# Sorriso como está hoje de verdade: plana, sem chuva, mas 100% 🟡 pela regra dos 30 (W7).
FIRE_DAY_PCT = {"green": 0.0, "yellow": 100.0, "red": 0.0}
RISK_FLAT_FIRE = {
    **RISK_FLAT,
    "days": [
        _risk_day(
            offset,
            0.0,
            0.6,
            "dry",
            15.0,
            FIRE_DAY_PCT,
            "yellow",
            [REASON_FIRE_YELLOW],
            reasons_by_level={"yellow": [REASON_FIRE_YELLOW]},
        )
        for offset in range(7)
    ],
}

# W13 — bloco do modelo, com os números reais do artefato treinado pela D3.
MODEL_NOTE = (
    "Modelo treinado com sinistros reais do PSR (1184 linhas, uma linha por apólice/safra) e "
    "testado em 155 linhas e 8 sinistros. No teste, ele **não superou** o baseline por regras "
    "(AUC-PR 0,073 contra 0,091 do baseline), então **as regras continuam sendo a base do "
    "alerta ao operador**. Aqui ele é aplicado a uma janela de um dia, bem mais curta que a "
    "safra em que foi treinado: use o valor para comparar dias e fazendas, não como "
    "probabilidade calibrada."
)
FAKE_MODEL_INFO = {
    "version": "v1",
    "trained_at": "2026-09-20T15:13:28+00:00",
    "algorithm": "regressao_logistica",
    "test_pr_auc": 0.0727,
    "test_roc_auc": 0.6054,
    "baseline_pr_auc": 0.0915,
    "baseline_roc_auc": 0.6216,
    "test_samples": 155,
    "test_positives": 8,
    "beats_baseline": False,
    "drivers": [
        {"feature": "state", "importance": 0.01692},
        {"feature": "dry_spell_max_days", "importance": 0.00595},
        {"feature": "elevation_range_m", "importance": 0.00459},
    ],
    "note": MODEL_NOTE,
}


def with_model(forecast: dict, probability: float | None = 0.0342) -> dict:
    """Mesma previsão, com os campos do modelo (W13) preenchidos."""
    return {
        **forecast,
        "model": FAKE_MODEL_INFO,
        "days": [
            {
                **day,
                "model_probability": probability,
                "model_version": "v1",
                "model_drivers": ["state", "dry_spell_max_days", "elevation_range_m"],
            }
            for day in forecast["days"]
        ],
    }


FAKE_RECOMMENDATION_DISCLAIMER = (
    "As janelas valem só para as áreas liberadas: as áreas em vermelho do mapa seguem "
    "proibidas mesmo dentro delas."
)

FAKE_RECOMMENDATIONS = {
    "cafe-carmo-de-minas": [
        {
            "date": "2026-09-19",
            "windows": [{"start": "07:00:00", "end": "12:00:00"}],
            "messages": [
                "Evite operar máquinas na encosta oeste (inclinação acima de 10°, solo "
                "encharcado).",
                "Previsão de tempestade: suspenda as atividades em áreas abertas e topos de morro.",
                FAKE_RECOMMENDATION_DISCLAIMER,
            ],
        },
        {
            "date": "2026-09-20",
            "windows": [],
            "messages": [
                "Risco de atolamento nas baixadas: evite tráfego pesado.",
                FAKE_RECOMMENDATION_DISCLAIMER,
            ],
        },
    ],
    "graos-sorriso": [
        {
            "date": "2026-09-19",
            "windows": [{"start": "06:00:00", "end": "18:00:00"}],
            "messages": ["Sem restrições de relevo e clima para hoje."],
        },
    ],
}

RECOMMENDATIONS_FIRE_DAY = {
    **FAKE_RECOMMENDATIONS,
    "graos-sorriso": [
        {
            "date": "2026-09-19",
            "windows": [{"start": "06:00:00", "end": "18:00:00"}],
            "messages": [
                "Condição de incêndio (regra dos 30): redobre a atenção com a colheitadeira e "
                "deixe o aceiro pronto."
            ],
        },
    ],
}

FAKE_RISKS = {
    ("cafe-carmo-de-minas", None): RISK_HILLY,
    ("cafe-carmo-de-minas", "heavy_rain"): RISK_HILLY_HEAVY_RAIN,
    ("graos-sorriso", None): RISK_FLAT,
    ("graos-sorriso", "heavy_rain"): {**RISK_FLAT, "scenario": "heavy_rain"},
}


def fake_get_risk(farm_id: str, days: int = 7, scenario: str | None = None) -> dict:
    return FAKE_RISKS[(farm_id, scenario)]


# ----------------------------------------------------------------- equipamento (W4 e W5)


def _utc_iso(seconds_ago: float) -> str:
    """Instante no passado, no formato que a API usa (UTC, sem fuso explícito)."""
    moment = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=seconds_ago)
    return moment.replace(tzinfo=None).isoformat(timespec="seconds")


FAKE_LIMIT = {
    "device_id": "tractor-01",
    "farm_id": "cafe-carmo-de-minas",
    "date": "2026-09-22",
    "scenario": None,
    "tilt_limit_deg": 10.0,
    "reference_tilt_limit_deg": 15.0,
    "warn_ratio": 0.8,
    "soil_state": "saturated",
    "risk_level": "red",
    "rain_72h_mm": 31.9,
    "wind_max_kmh": 15.4,
    "valid_until": 1789873200,
    "reason": "solo encharcado: 31,9 mm em 72 h",
}

FAKE_PUBLISHED = {
    "limit": FAKE_LIMIT,
    "topic": "agrishield/devices/tractor-01/config",
    "published_at": _utc_iso(0),
    "payload": {"tilt_limit_deg": 10.0},
}

FAKE_STATUS_ONLINE = {
    "device_id": "tractor-01",
    "state": "online",
    "reported_state": "online",
    "last_seen_at": _utc_iso(2),
    "seconds_since_last_telemetry": 2.0,
}

FAKE_STATUS_NEVER_SEEN = {
    "device_id": "tractor-01",
    "state": "offline",
    "reported_state": None,
    "last_seen_at": None,
    "seconds_since_last_telemetry": None,
}


def _telemetry_point(seconds_ago: float, roll: float, seq: int, level: str = "red") -> dict:
    return {
        "device_id": "tractor-01",
        "ts": _utc_iso(seconds_ago),
        "received_at": _utc_iso(seconds_ago),
        "seq": seq,
        "roll_deg": roll,
        "pitch_deg": -2.4,
        "accel_g": 1.0,
        "temp_c": 27.5,
        "humidity_pct": 62.0,
        "fire_conditions": 1,
        "tilt_limit_deg": 10.0,
        "alert_level": level,
    }


FAKE_TELEMETRY = _telemetry_point(2, 12.4, 120)
FAKE_SERIES = {
    "device_id": "tractor-01",
    "minutes": 10,
    "total": 3,
    "sampled": False,
    "points": [
        _telemetry_point(20, 4.1, 118),
        _telemetry_point(10, 8.9, 119),
        FAKE_TELEMETRY,
    ],
}

TILT_ALERT_EVENT = {
    "event_id": "tractor-01-1-1",
    "device_id": "tractor-01",
    "type": "tilt_alert",
    "ts": _utc_iso(90),
    "received_at": _utc_iso(90),
    "roll_deg": 11.2,
    "pitch_deg": -1.0,
    "accel_g": 1.0,
    "tilt_limit_deg": 10.0,
    "context": None,
}


def _rollover_event(seconds_ago: float) -> dict:
    return {
        "event_id": "tractor-01-2-1",
        "device_id": "tractor-01",
        "type": "rollover",
        "ts": _utc_iso(seconds_ago),
        "received_at": _utc_iso(seconds_ago),
        "roll_deg": 61.0,
        "pitch_deg": 2.3,
        "accel_g": 2.8,
        "tilt_limit_deg": 10.0,
        "context": {
            "fields": ["t_s", "roll_deg", "pitch_deg", "accel_g"],
            "rows": [[-2, 9.8, -2.9, 1.0], [-1, 10.4, -3.0, 1.0], [0, 61.0, 2.3, 2.8]],
        },
    }


FAKE_EVENTS = [TILT_ALERT_EVENT]

EMPTY_SERIES = {
    "device_id": "tractor-01",
    "minutes": 10,
    "total": 0,
    "sampled": False,
    "points": [],
}

NO_TELEMETRY_MESSAGE = "Sem telemetria ainda"


# ----------------------------------------------------------------- subscrição (W8)

CALIBRATION_NOTE = (
    "Pesos da v1, escolhidos por julgamento de engenharia e ainda **não calibrados** com dados "
    "de sinistro. Calibrá-los com a base da Sompo é o primeiro item do roadmap."
)


def _profile(
    farm: dict,
    score: float,
    risk_class: str,
    pct_gt15: float,
    pct_lowland: float,
    pct_exposed: float,
    drivers: list[dict],
) -> dict:
    return {
        "farm_id": farm["id"],
        "farm_name": farm["name"],
        "municipality": farm["municipality"],
        "state": farm["state"],
        "crop": farm["crop"],
        "pct_slope_lt8": 100.0 - pct_gt15,
        "pct_slope_8_15": 46.0,
        "pct_slope_gt15": pct_gt15,
        "pct_lowland": pct_lowland,
        "pct_exposed": pct_exposed,
        "slope_max_deg": 21.56,
        "slope_mean_deg": 9.73,
        "reference_tilt_limit_deg": 15.0,
        "terrain_score": score,
        "risk_class": risk_class,
        "drivers": drivers,
        "weights_version": "v1-arbitrario",
        "calibration_note": CALIBRATION_NOTE,
    }


FAKE_PROFILES = {
    "cafe-carmo-de-minas": _profile(
        FAKE_FARMS[0],
        41.2,
        "B",
        17.0,
        25.0,
        21.0,
        [
            {"factor": "pct_slope_8_15", "label": "46% da área entre 8° e 15°", "points": 23.0},
            {
                "factor": "pct_slope_gt15",
                "label": "17% da área com inclinação de 15° ou mais",
                "points": 17.0,
            },
        ],
    ),
    "graos-sorriso": _profile(FAKE_FARMS[1], 100.0, "A", 0.0, 0.0, 0.0, []),
}


# ----------------------------------------------------------------- relatórios (W12)

FAKE_EQUIPMENT_REPORT = {
    "device_id": "tractor-01",
    "farm_id": "cafe-carmo-de-minas",
    "days": 7,
    "purpose": "Para o gestor de frota e a manutenção: mostra quanto a máquina operou…",
    "readings": 4320,
    # Também fora de 4320 × 5 s, pelo mesmo motivo do histórico (W11).
    "operating_hours": 5.4,
    "pct_time_above_limit": 12.5,
    "alerts": 4,
    "rollovers": 1,
    "trend": [
        {
            "date": "2026-09-19",
            "readings": 2160,
            "operating_hours": 3.0,
            "pct_time_above_limit": 10.0,
            "alerts": 1,
            "max_roll_deg": 14.2,
        },
        {
            "date": "2026-09-20",
            "readings": 2160,
            "operating_hours": 3.0,
            "pct_time_above_limit": 15.0,
            "alerts": 3,
            "max_roll_deg": 61.0,
        },
    ],
}

EMPTY_EQUIPMENT_REPORT = {
    **FAKE_EQUIPMENT_REPORT,
    "readings": 0,
    "operating_hours": 0.0,
    "pct_time_above_limit": 0.0,
    "alerts": 0,
    "rollovers": 0,
    "trend": [],
}

FAKE_REGION_REPORT = {
    "state": "RS",
    "from_year": None,
    "to_year": None,
    "purpose": "Para o analista da seguradora: mostra o histórico real de sinistros da região…",
    "policies": 329595,
    "claims": 91998,
    "claim_rate_pct": 27.9,
    "indemnity_total": 5438964329.22,
    "indemnity_mean": 59120.46,
    "top_events": [
        {
            "event_category": "seca",
            "claims": 32986,
            "pct_of_claims": 35.9,
            "indemnity_total": 3475828986.26,
        },
        {
            "event_category": "granizo",
            "claims": 32951,
            "pct_of_claims": 35.8,
            "indemnity_total": 1125430883.78,
        },
    ],
    "top_municipalities": [
        {
            "municipality": "Bento Gonçalves",
            "policies": 12644,
            "claims": 2510,
            "claim_rate_pct": 19.9,
            "indemnity_total": 35165388.13,
        }
    ],
    "demo_farms": [
        {
            "farm_id": "uva-serra-gaucha",
            "municipality": "Bento Gonçalves",
            "terrain_score": 62.7,
            "risk_class": "B",
        }
    ],
}

EMPTY_REGION_REPORT = {
    **FAKE_REGION_REPORT,
    "policies": 0,
    "claims": 0,
    "claim_rate_pct": 0.0,
    "indemnity_total": 0.0,
    "indemnity_mean": 0.0,
    "top_events": [],
    "top_municipalities": [],
    "demo_farms": [],
}

FAKE_CROP_REPORT = {
    "from_year": None,
    "to_year": None,
    "state": None,
    "purpose": "Para subscrição e produto: mostra quais culturas e quais causas concentram…",
    "policies": 1525473,
    "claims": 358370,
    "claim_rate_pct": 23.5,
    "crops": [
        {
            "crop": "Maçã",
            "policies": 29454,
            "claims": 10011,
            "claim_rate_pct": 34.0,
            "indemnity_total": 704132013.47,
            "top_event": "granizo",
        }
    ],
    "events": [
        {
            "event_category": "seca",
            "claims": 173835,
            "pct_of_claims": 48.5,
            "indemnity_total": 13071925954.83,
        }
    ],
}


# ----------------------------------------------------------------- replay (W9)

FAKE_REPLAY_CASES = [
    {
        "id": "jabora-capotamento-trator-2026-07-18",
        "title": "Trator capota em ribanceira numa descida e mata trabalhador",
        "date": "2026-07-18",
        "location": {"lat": -27.14687, "lon": -51.74389},
        "location_precision": "municipio",
        "municipality": "Jaborá",
        "state": "SC",
        "machine": "trator",
        "description": "Trator capotou numa ribanceira em trecho de descida.",
        "source_url": "https://ndmais.com.br/exemplo",
    },
    {
        "id": "patos-de-minas-incendio-colheitadeira-2023-06-20",
        "title": "Colheitadeira pega fogo na colheita de sorgo",
        "date": "2023-06-20",
        "location": {"lat": -18.63407, "lon": -46.57236},
        "location_precision": "municipio",
        "municipality": "Patos de Minas",
        "state": "MG",
        "machine": "colheitadeira",
        "description": "Colheitadeira pegou fogo durante a colheita.",
        "source_url": "https://exemplo.com/patos",
    },
]

REASON_WIND_YELLOW = {
    "hazard": "wind",
    "level": "yellow",
    "message": "Rajadas de 72,4 km/h: risco para máquinas altas.",
}
REASON_ROLLOVER_RED_REPLAY = {
    "hazard": "rollover",
    "level": "red",
    "message": "Inclinação de 16,6° acima do limite de 15° (solo seco: 0,0 mm em 72 h).",
}

REPLAY_LIMITATIONS = [
    "A notícia não deu a coordenada do acidente: o ponto é o de uma lavoura típica do "
    "município (mediana das apólices do PSR ali). O relevo avaliado pode estar a quilômetros "
    "da lavoura onde o acidente aconteceu.",
    "A grade do replay é 3 × 3 (cerca de 1 km²) em volta do ponto, menor que a do mapa de uma "
    "fazenda: ela mostra o relevo da vizinhança, não o talhão inteiro.",
    "O veredito diz o que o motor **teria mostrado** naquele dia e naquele ponto. Ele não "
    "afirma que o alerta teria evitado o acidente.",
]


def _replay_terrain() -> dict:
    """Grade 3 × 3 de relevo, na mesma forma do `/farms/{id}/terrain` (W9)."""
    cells = []
    slopes = [7.1, 0.95, 4.62, 8.1, 2.2, 6.43, 7.86, 4.92, 16.55]
    for index, (row, col) in enumerate((r, c) for r in range(3) for c in range(3)):
        lat = -27.1435 - row * 0.0033
        lon = -51.7472 + col * 0.0033
        cells.append(
            {
                "row": row,
                "col": col,
                "lat": lat,
                "lon": lon,
                "polygon": [
                    [lon, lat],
                    [lon + 0.0033, lat],
                    [lon + 0.0033, lat - 0.0033],
                    [lon, lat - 0.0033],
                ],
                "elevation_m": 890.0 + index,
                "slope_deg": slopes[index],
                "aspect_deg": 11.22,
                "aspect_label": "N",
                "terrain_class": "slope",
            }
        )
    return {
        "farm_id": "replay",
        "grid_size": 3,
        "cell_size_m": {"x_m": 330.2, "y_m": 368.5},
        "stats": {
            "elevation_min_m": 890.0,
            "elevation_max_m": 898.0,
            "elevation_range_m": 8.0,
            "slope_max_deg": 16.55,
            "slope_mean_deg": 6.5,
            "pct_slope_lt8": 66.7,
            "pct_slope_8_15": 22.2,
            "pct_slope_gt15": 11.1,
        },
        "cells": cells,
    }


def _replay_grid(point_level: str) -> list[dict]:
    """Grade 3 × 3: o centro é a célula do ponto e a célula mais íngreme acusa 🔴."""
    cells = []
    for row in range(3):
        for col in range(3):
            if (row, col) == (1, 1):
                level, reasons = point_level, [REASON_WIND_YELLOW]
            elif (row, col) == (2, 2):
                # A célula mais íngreme da grade (16,55°), como no caso real de Jaborá.
                level, reasons = "red", [REASON_ROLLOVER_RED_REPLAY, REASON_WIND_YELLOW]
            else:
                level, reasons = "yellow", [REASON_WIND_YELLOW]
            cells.append({"row": row, "col": col, "level": level, "reasons": reasons})
    return cells


REPLAY_HIT = {
    "case": FAKE_REPLAY_CASES[0],
    "terrain": _replay_terrain(),
    "date": "2026-07-18",
    "location": {"lat": -27.14687, "lon": -51.74389},
    "location_precision": "municipio",
    "grid_size": 3,
    "reference_tilt_limit_deg": 15.0,
    "point_cell": {"row": 1, "col": 1, "level": "yellow", "reasons": [REASON_WIND_YELLOW]},
    "day": {
        "date": "2026-07-18",
        "pct_levels": {"green": 0.0, "yellow": 55.6, "red": 44.4},
        "worst_level": "red",
        "tilt_limit_deg": 15.0,
        "cells": _replay_grid("yellow"),
    },
    "weather": {
        "rain_mm": 0.0,
        "rain_72h_mm": 0.0,
        "temp_max_c": 25.7,
        "rh_min_pct": 44.0,
        "wind_max_kmh": 33.0,
        "gust_max_kmh": 72.4,
        "cape_max": 780.0,
        "thunderstorm": False,
    },
    "would_alert": True,
    "would_alert_in_grid": True,
    "verdict": (
        "O sistema teria alertado no ponto do acidente. Rajadas de 72,4 km/h: risco para "
        "máquinas altas."
    ),
    "source": "historical_forecast",
    "limitations": REPLAY_LIMITATIONS,
}

REPLAY_MISS = {
    **REPLAY_HIT,
    "case": FAKE_REPLAY_CASES[1],
    "date": "2023-06-20",
    "point_cell": {"row": 1, "col": 1, "level": "green", "reasons": []},
    "day": {
        **REPLAY_HIT["day"],
        "date": "2023-06-20",
        "pct_levels": {"green": 100.0, "yellow": 0.0, "red": 0.0},
        "worst_level": "green",
        "cells": [
            {"row": row, "col": col, "level": "green", "reasons": []}
            for row in range(3)
            for col in range(3)
        ],
    },
    "would_alert": False,
    "would_alert_in_grid": False,
    "verdict": (
        "O sistema não teria alertado no ponto do acidente: nenhuma das 3 condições da regra "
        "dos 30 estava presente."
    ),
    "limitations": REPLAY_LIMITATIONS,
}

FAKE_REPLAY_SUMMARY_NOTE = (
    "5 casos reais avaliados, 2 com alerta na célula do ponto e 3 com alerta em alguma célula "
    "da grade. **Isto não é taxa de acerto do produto**: cinco casos noticiados não são "
    "amostra, todos têm coordenada de município (não da lavoura) e os casos foram escolhidos "
    "para exercitar perigos diferentes, não sorteados."
)

FAKE_REPLAY_SUMMARY = {
    "total_cases": 5,
    "evaluated": 5,
    "failed_case_ids": [],
    "would_alert_at_point": 2,
    "would_alert_in_grid": 3,
    "note": FAKE_REPLAY_SUMMARY_NOTE,
    # Os cinco casos reais, para a tela de teste parecer com a da demo (2 no ponto, 3 na grade).
    "cases": [
        {
            "case_id": "patos-de-minas-incendio-colheitadeira-2023-06-20",
            "title": "Colheitadeira pega fogo na colheita de sorgo",
            "date": "2023-06-20",
            "municipality": "Patos de Minas",
            "state": "MG",
            "would_alert": False,
            "would_alert_in_grid": False,
            "point_level": "green",
            "source_url": "https://exemplo.com/patos",
        },
        {
            "case_id": "irai-de-minas-capotamento-trator-2024-02-09",
            "title": "Trator capota durante plantio e mata operador",
            "date": "2024-02-09",
            "municipality": "Iraí de Minas",
            "state": "MG",
            "would_alert": False,
            "would_alert_in_grid": True,
            "point_level": "green",
            "source_url": "https://exemplo.com/irai",
        },
        {
            "case_id": "castro-tombamento-trator-2025-07-04",
            "title": "Trator tomba ao tentar desatolar outro em pastagem encharcada",
            "date": "2025-07-04",
            "municipality": "Castro",
            "state": "PR",
            "would_alert": False,
            "would_alert_in_grid": False,
            "point_level": "green",
            "source_url": "https://exemplo.com/castro",
        },
        {
            "case_id": "jabora-capotamento-trator-2026-07-18",
            "title": "Trator capota em ribanceira numa descida e mata trabalhador",
            "date": "2026-07-18",
            "municipality": "Jaborá",
            "state": "SC",
            "would_alert": True,
            "would_alert_in_grid": True,
            "point_level": "yellow",
            "source_url": "https://ndmais.com.br/exemplo",
        },
        {
            "case_id": "imbituva-raio-lavoura-2026-08-31",
            "title": "Raio mata trabalhador em plantação de fumo durante tempestade",
            "date": "2026-08-31",
            "municipality": "Imbituva",
            "state": "PR",
            "would_alert": True,
            "would_alert_in_grid": True,
            "point_level": "yellow",
            "source_url": "https://exemplo.com/imbituva",
        },
    ],
}

PARTIAL_REPLAY_SUMMARY = {
    **FAKE_REPLAY_SUMMARY,
    "evaluated": 4,
    "failed_case_ids": ["castro-tombamento-trator-2025-07-04"],
    "note": (
        FAKE_REPLAY_SUMMARY_NOTE
        + " *1 de 5 casos não puderam ser avaliados agora (serviço de clima indisponível): "
        "o placar está incompleto.*"
    ),
}

FAKE_REPLAYS = {
    "jabora-capotamento-trator-2026-07-18": REPLAY_HIT,
    "patos-de-minas-incendio-colheitadeira-2023-06-20": REPLAY_MISS,
}


def fake_csv_url(path: str, params: dict | None = None) -> str:
    """Dublê do link de CSV que **mantém os filtros**, como o cliente real faz."""
    clean = {key: value for key, value in (params or {}).items() if value is not None}
    query = f"?{urlencode(clean)}" if clean else ""
    return f"http://api.test/api/v1{path}{query}"


# ----------------------------------------------------------------- passaporte (W11)

ROADMAP_NOTE = (
    "Prévia do Passaporte Digital: por enquanto é o histórico do período, sobre o que o "
    "equipamento publicou. Score comportamental, portabilidade na revenda e assinatura digital "
    "do registro ficam no roadmap."
)

FAKE_HISTORY = {
    "device_id": "tractor-01",
    "farm_id": "cafe-carmo-de-minas",
    "days": 7,
    "first_seen_at": _utc_iso(600),
    "last_seen_at": _utc_iso(2),
    "readings": 4320,
    # De propósito **não** é 4320 × 5 s = 6,0 h: quem calcula horas é a API, e um front que
    # refizesse a conta mostraria 6,0 aqui.
    "operating_hours": 5.4,
    "max_roll_deg": 61.0,
    "max_pitch_deg": 8.4,
    "pct_time_above_limit": 12.5,
    "alerts": 4,
    "rollovers": 1,
    "incident_reports": 2,
    "limits_applied": 3,
    "timeline": [TILT_ALERT_EVENT],
    "roadmap_note": ROADMAP_NOTE,
}

EMPTY_HISTORY = {
    **FAKE_HISTORY,
    "first_seen_at": None,
    "last_seen_at": None,
    "readings": 0,
    "operating_hours": 0.0,
    "max_roll_deg": None,
    "max_pitch_deg": None,
    "pct_time_above_limit": 0.0,
    "alerts": 0,
    "rollovers": 0,
    "incident_reports": 0,
    "limits_applied": 0,
    "timeline": [],
}


class FakeApi:
    def health(self) -> dict:
        return {"status": "ok", "version": "0.1.0", "environment": "test"}


@contextmanager
def api_online(
    farms: list[dict] | None = None,
    terrains: dict | None = None,
    status: dict | None = None,
    limit: dict | None = None,
    telemetry: dict | None = None,
    series: dict | None = None,
    events: list[dict] | None = None,
    publish_error: Exception | None = None,
    recommendations: dict | None = None,
    risks: dict | None = None,
    profiles: dict | None = None,
    equipment_report: dict | None = None,
    region_report: dict | None = None,
    crop_report: dict | None = None,
    replay_cases: list[dict] | None = None,
    replays: dict | None = None,
    replay_summary: dict | None = None,
    history: dict | None = None,
):
    """A API responde.

    `telemetry=None` significa "equipamento ainda sem telemetria" (a API devolve 404),
    que é espera e não erro. `publish_error` simula o 503 do MQTT desconectado.
    """
    farms = FAKE_FARMS if farms is None else farms
    terrains = FAKE_TERRAINS if terrains is None else terrains
    status = FAKE_STATUS_ONLINE if status is None else status
    limit = FAKE_LIMIT if limit is None else limit
    series = FAKE_SERIES if series is None else series
    events = FAKE_EVENTS if events is None else events
    recommendations = FAKE_RECOMMENDATIONS if recommendations is None else recommendations
    risks = FAKE_RISKS if risks is None else risks
    profiles = FAKE_PROFILES if profiles is None else profiles
    equipment_report = FAKE_EQUIPMENT_REPORT if equipment_report is None else equipment_report
    region_report = FAKE_REGION_REPORT if region_report is None else region_report
    crop_report = FAKE_CROP_REPORT if crop_report is None else crop_report
    replay_cases = FAKE_REPLAY_CASES if replay_cases is None else replay_cases
    replays = FAKE_REPLAYS if replays is None else replays
    replay_summary = FAKE_REPLAY_SUMMARY if replay_summary is None else replay_summary
    history = FAKE_HISTORY if history is None else history

    def fake_run_replay(case_id=None, lat=None, lon=None, date=None) -> dict:
        if case_id is not None:
            return replays[case_id]
        return {**REPLAY_HIT, "case": None, "location_precision": "aproximado"}

    def risk_for(farm_id: str, days: int = 7, scenario: str | None = None) -> dict:
        return risks[(farm_id, scenario)]

    def fake_latest(device_id: str) -> dict:
        if telemetry is None:
            raise NotFoundError(NO_TELEMETRY_MESSAGE)
        return telemetry

    def fake_publish(device_id: str, date: str | None = None, scenario: str | None = None) -> dict:
        if publish_error is not None:
            raise publish_error
        return FAKE_PUBLISHED

    with ExitStack() as stack:
        stack.enter_context(patch("components.farm_picker.list_farms", return_value=farms))
        stack.enter_context(patch("services.api_client.list_farms", return_value=farms))
        stack.enter_context(
            patch("services.api_client.get_farm", side_effect=lambda fid: FAKE_FARM_DETAILS[fid])
        )
        stack.enter_context(
            patch("services.api_client.get_terrain", side_effect=lambda fid: terrains[fid])
        )
        stack.enter_context(patch("services.api_client.get_risk", side_effect=risk_for))
        stack.enter_context(
            patch(
                "services.api_client.get_recommendations",
                side_effect=lambda farm_id, **kwargs: recommendations[farm_id],
            )
        )
        stack.enter_context(patch("services.api_client.get_device_limit", return_value=limit))
        stack.enter_context(
            patch("services.api_client.publish_device_limit", side_effect=fake_publish)
        )
        stack.enter_context(patch("services.api_client.get_device_status", return_value=status))
        stack.enter_context(
            patch("services.api_client.get_latest_telemetry", side_effect=fake_latest)
        )
        stack.enter_context(patch("services.api_client.get_telemetry_series", return_value=series))
        stack.enter_context(patch("services.api_client.get_device_events", return_value=events))
        stack.enter_context(patch("services.api_client.get_device_history", return_value=history))
        stack.enter_context(
            patch("services.api_client.get_underwriting", side_effect=lambda fid: profiles[fid])
        )
        stack.enter_context(
            patch("services.api_client.get_equipment_report", return_value=equipment_report)
        )
        stack.enter_context(
            patch("services.api_client.get_region_report", return_value=region_report)
        )
        stack.enter_context(patch("services.api_client.get_crop_report", return_value=crop_report))
        stack.enter_context(patch("services.api_client.report_csv_url", side_effect=fake_csv_url))
        stack.enter_context(
            patch("services.api_client.list_replay_cases", return_value=replay_cases)
        )
        stack.enter_context(patch("services.api_client.run_replay", side_effect=fake_run_replay))
        stack.enter_context(
            patch("services.api_client.get_replay_summary", return_value=replay_summary)
        )
        stack.enter_context(patch("services.api_client.get_api", return_value=FakeApi()))
        yield


@contextmanager
def api_offline():
    """A API está fora do ar: toda chamada levanta ApiError."""
    error = ApiError(OFFLINE_MESSAGE)
    with ExitStack() as stack:
        for target in (
            "components.farm_picker.list_farms",
            "services.api_client.list_farms",
            "services.api_client.get_farm",
            "services.api_client.get_terrain",
            "services.api_client.get_risk",
            "services.api_client.get_recommendations",
            "services.api_client.get_device_limit",
            "services.api_client.publish_device_limit",
            "services.api_client.get_device_status",
            "services.api_client.get_latest_telemetry",
            "services.api_client.get_telemetry_series",
            "services.api_client.get_device_events",
            "services.api_client.get_device_history",
            "services.api_client.get_underwriting",
            "services.api_client.get_equipment_report",
            "services.api_client.get_region_report",
            "services.api_client.get_crop_report",
            "services.api_client.list_replay_cases",
            "services.api_client.run_replay",
            "services.api_client.get_replay_summary",
            "services.api_client.get_api",
        ):
            stack.enter_context(patch(target, side_effect=error))
        yield


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page: str) -> None:
    """Com a API fora do ar, nenhuma página estoura (caminho relativo a este arquivo)."""
    with api_offline():
        at = AppTest.from_file(f"../views/{page}", default_timeout=15).run()

    assert not at.exception


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_api_online(page: str) -> None:
    with api_online():
        at = AppTest.from_file(f"../views/{page}", default_timeout=30).run()

    assert not at.exception


@pytest.mark.parametrize("page", PAGES_WITH_API)
def test_page_shows_error_when_api_is_down(page: str) -> None:
    """Com a API fora do ar a página avisa em português, sem traceback."""
    with api_offline():
        at = AppTest.from_file(f"../views/{page}", default_timeout=15).run()

    assert not at.exception
    messages = [element.value for element in at.error]
    assert messages, "a página deveria mostrar um st.error quando a API não responde"
    assert any(OFFLINE_MESSAGE in message for message in messages)


def _scenario_warnings(at: AppTest) -> list[str]:
    """Só as faixas de cenário simulado (a W6 também usa st.warning para o aviso das áreas 🔴)."""
    return [
        element.value
        for element in at.warning
        if "Cenário simulado — não é a previsão real" in element.value
    ]


def _run_risk_map(farm_id: str = "cafe-carmo-de-minas", **kwargs) -> AppTest:
    at = AppTest.from_file("../views/risk_map.py", default_timeout=30)
    at.session_state["farm_id"] = farm_id
    with api_online(**kwargs):
        at.run()
    return at


def test_risk_map_shows_terrain_kpis_and_map() -> None:
    at = _run_risk_map()

    assert not at.exception
    assert not at.error
    values = [metric.value for metric in at.metric]
    assert "118 m" in values  # amplitude
    assert "21,6°" in values  # inclinação máxima
    assert "17%" in values  # área com 15° ou mais
    # Um mapa por aba: relevo (W2) e risco (W3).
    assert len(at.get("deck_gl_json_chart")) == 2


def test_risk_map_switches_color_mode() -> None:
    at = _run_risk_map()

    assert at.radio[0].value == "Inclinação"
    with api_online():
        at.radio[0].set_value("Classe de terreno").run()

    assert not at.exception
    assert at.radio[0].value == "Classe de terreno"
    captions = " ".join(caption.value for caption in at.caption)
    assert "não de risco" in captions


def test_flat_farm_legend_shows_real_maximum_slope() -> None:
    """Fazenda quase plana: o piso de 5° é só da cor; a legenda mostra o valor real da API."""
    at = _run_risk_map("graos-sorriso")

    assert not at.exception
    assert "0,6°" in [metric.value for metric in at.metric]
    captions = " ".join(caption.value for caption in at.caption)
    assert "Inclinação máxima desta fazenda: 0,6°" in captions
    legend = " ".join(markdown.value for markdown in at.markdown)
    assert "5,0° (escala mínima)" in legend
    assert "5,0° (máximo desta fazenda)" not in legend


def test_farm_selection_survives_page_change() -> None:
    """Critério de aceite da W1, com navegação de verdade (`st.navigation`)."""
    at = AppTest.from_file("../app.py", default_timeout=60)

    with api_online():
        at.run()  # abre na Início
        assert at.selectbox[0].value == "cafe-carmo-de-minas"
        at.selectbox[0].set_value(FARM_LABEL_SORRISO).run()
        assert at.session_state["farm_id"] == "graos-sorriso"

        # Caminho realista, passando por uma página sem o seletor. Quem limpa o estado de
        # widget é a troca de página em si, não o fato de a próxima página não desenhá-lo.
        at.switch_page("views/underwriting.py").run()
        at.switch_page("views/risk_map.py").run()

    assert not at.exception
    assert at.session_state["farm_id"] == "graos-sorriso"
    assert at.selectbox[0].value == "graos-sorriso"
    # O detalhe exibido é o da fazenda escolhida, não o da primeira da lista.
    assert "Planalto de Grãos" in at.subheader[0].value
    assert "3 m" in [metric.value for metric in at.metric]


# ----------------------------------------------------------------- aba Previsão de risco (W3)


def test_risk_tab_shows_day_strip_panel_and_chart() -> None:
    at = _run_risk_map()

    assert not at.exception
    assert not at.error
    # 7 dias na faixa, cada um com o seu botão.
    assert len([button for button in at.button if button.label == "Ver dia"]) == 7
    # Painel do dia escolhido (o primeiro, por padrão): limite de 15,0° com solo seco.
    assert "15,0°" in [metric.value for metric in at.metric]
    # Gráfico de chuva.
    assert len(at.get("vega_lite_chart")) == 1
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Principais motivos" in markdowns
    assert "Inclinação de 16,7° acima do limite de 10°" in markdowns


def _selected_day_cards(at: AppTest) -> list[str]:
    """Cards da faixa desenhados como escolhidos (borda grossa)."""
    return [element.value for element in at.markdown if "border:3px" in element.value]


def test_risk_tab_changes_day_when_card_is_clicked() -> None:
    at = _run_risk_map()
    assert "sáb 19/09" in _selected_day_cards(at)[0]

    # Dia +3: solo encharcado, limite de 10,0° e 70% da área em 🔴.
    with api_online():
        [button for button in at.button if button.label == "Ver dia"][3].click().run()

    assert not at.exception
    assert at.session_state["selected_day_date"] == "2026-09-22"
    assert "10,0°" in [metric.value for metric in at.metric]
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Alto: **70%** da área" in markdowns
    assert "risco de atolamento" in markdowns
    # O destaque acompanha o clique na mesma rodada (não pode atrasar uma interação).
    selected = _selected_day_cards(at)
    assert len(selected) == 1
    assert "ter 22/09" in selected[0]


def test_scenario_toggle_asks_the_api_for_the_scenario_and_warns_on_screen() -> None:
    at = _run_risk_map()
    assert not _scenario_warnings(at)  # previsão real: sem faixa de cenário

    with api_online():
        at.toggle[0].set_value(True).run()

    assert not at.exception
    assert _scenario_warnings(at)
    with api_online():
        at.button[2].click().run()  # dia +2, o que o cenário altera
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Alto: **70%** da área" in markdowns


def test_flat_farm_risk_is_green_without_alerts() -> None:
    at = _run_risk_map("graos-sorriso")

    assert not at.exception
    successes = " ".join(element.value for element in at.success)
    assert "Nenhum alerta para este dia" in successes
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Baixo: **100%** da área" in markdowns


def test_risk_tab_shows_error_when_the_risk_endpoint_fails() -> None:
    """A rota de risco pode cair sozinha (ex.: 503 da Open-Meteo): a aba avisa, sem traceback."""
    message = "Serviço de clima indisponível (HTTP 503 em /farms/cafe-carmo-de-minas/risk)"
    at = AppTest.from_file("../views/risk_map.py", default_timeout=15)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"
    with api_online(), patch("services.api_client.get_risk", side_effect=ApiError(message)):
        at.run()

    assert not at.exception
    assert message in [element.value for element in at.error]
    # O relevo continua na tela: só a aba de risco fica indisponível.
    assert len(at.get("deck_gl_json_chart")) == 1


def test_no_scenario_banner_when_the_api_returns_the_real_forecast() -> None:
    """Toggle ligado, mas a resposta veio sem cenário: nada de faixa enganosa."""
    at = AppTest.from_file("../views/risk_map.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"
    at.session_state["risk_scenario_heavy_rain"] = True
    with api_online(), patch("services.api_client.get_risk", return_value=RISK_HILLY):
        at.run()

    assert not at.exception
    assert at.toggle[0].value is True
    assert not _scenario_warnings(at)


def test_scenario_banner_follows_the_response_not_the_toggle() -> None:
    """Toggle desligado, mas a resposta traz `scenario`: a faixa tem de aparecer mesmo assim."""
    at = AppTest.from_file("../views/risk_map.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"
    with api_online(), patch("services.api_client.get_risk", return_value=RISK_HILLY_HEAVY_RAIN):
        at.run()

    assert not at.exception
    assert at.toggle[0].value is False
    assert _scenario_warnings(at)


# ----------------------------------------------------------------- equipamento (W4 e W5)


def _run_equipment(**kwargs) -> AppTest:
    at = AppTest.from_file("../views/equipment.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"
    with api_online(**kwargs):
        at.run()
    return at


def test_limit_card_shows_the_day_limit_and_the_dry_soil_reference() -> None:
    at = _run_equipment()

    assert not at.exception
    assert not at.error
    markdowns = " ".join(element.value for element in at.markdown)
    # Cada número no seu lugar: trocar o limite do dia pela referência tem de quebrar o teste.
    assert "10,0°</div>" in markdowns  # limite do dia, no número grande
    assert "de <b>15,0°</b> em solo seco" in markdowns  # referência, na linha de contexto
    captions = " ".join(element.value for element in at.caption)
    assert "solo encharcado: 31,9 mm em 72 h" in captions  # motivo, como vai para o equipamento
    assert "31,9 mm" in [metric.value for metric in at.metric]


def test_publish_button_sends_the_limit_and_records_the_last_send() -> None:
    at = _run_equipment()

    send = [button for button in at.button if "Enviar ao equipamento" in button.label][0]
    with api_online():
        send.click().run()

    assert not at.exception
    assert at.session_state["last_limit_publish"]["tilt_limit_deg"] == 10.0
    successes = " ".join(element.value for element in at.success)
    assert "Último envio:" in successes
    assert "10,0°" in successes
    captions = " ".join(element.value for element in at.caption)
    assert "QoS 1 e retained" in captions


def test_publish_failure_is_friendly_and_does_not_say_the_limit_was_lost() -> None:
    at = _run_equipment()

    send = [button for button in at.button if "Enviar ao equipamento" in button.label][0]
    with api_online(publish_error=ApiError("Equipamento sem conexão MQTT")):
        send.click().run()

    assert not at.exception
    messages = " ".join(element.value for element in at.error)
    assert "Equipamento sem conexão MQTT" in messages
    assert "não foi enviado" in messages
    assert "nada se perdeu" in messages
    assert "perdido" not in messages
    assert "last_limit_publish" not in at.session_state


def test_publish_without_api_key_says_what_to_configure() -> None:
    """401 da I5: o erro é de configuração, e a tela diz qual variável falta."""
    at = _run_equipment()

    send = [button for button in at.button if "Enviar ao equipamento" in button.label][0]
    with api_online(publish_error=ApiKeyError(MISSING_API_KEY_MESSAGE)):
        send.click().run()

    assert not at.exception
    messages = " ".join(element.value for element in at.error)
    assert "AGRISHIELD_API_KEY" in messages
    assert "não foi enviado" in messages
    assert "last_limit_publish" not in at.session_state


def test_device_without_telemetry_is_waiting_not_an_error() -> None:
    at = _run_equipment(
        status=FAKE_STATUS_NEVER_SEEN, telemetry=None, series=EMPTY_SERIES, events=[]
    )

    assert not at.exception
    assert not at.error
    infos = " ".join(element.value for element in at.info)
    assert "Aguardando o equipamento conectar" in infos
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Offline" in markdowns
    assert "nunca se conectou" in markdowns


def test_live_panel_shows_status_banner_metrics_and_events() -> None:
    at = _run_equipment(telemetry=FAKE_TELEMETRY)

    assert not at.exception
    assert not at.error
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Online" in markdowns
    assert "PERIGO" in markdowns  # banner do nível do alerta local
    assert "Inclinação acima do limite" in markdowns  # lista de eventos
    pairs = [(metric.label, metric.value) for metric in at.metric]
    assert ("Rolagem (roll)", "12,4°") in pairs
    assert ("Arfagem (pitch)", "-2,4°") in pairs
    assert ("Limite no equipamento", "10,0°") in pairs
    # Um gráfico: roll e pitch dos últimos 10 min (sem capotamento recente, sem contexto).
    assert len(at.get("vega_lite_chart")) == 1


def test_recent_rollover_pins_the_alert_with_the_context_chart() -> None:
    at = _run_equipment(
        telemetry=_telemetry_point(2, 61.0, 130, level="rollover"),
        events=[_rollover_event(30), TILT_ALERT_EVENT],
    )

    assert not at.exception
    errors = " ".join(element.value for element in at.error)
    assert "CAPOTAMENTO detectado" in errors
    markdowns = " ".join(element.value for element in at.markdown)
    assert "🚨 CAPOTAMENTO" in markdowns  # banner do nível
    # Dois gráficos: a série de 10 min e o contexto de 30 s do evento.
    assert len(at.get("vega_lite_chart")) == 2


def test_old_rollover_stays_only_in_the_event_list() -> None:
    at = _run_equipment(telemetry=FAKE_TELEMETRY, events=[_rollover_event(600), TILT_ALERT_EVENT])

    assert not at.exception
    assert not at.error
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Capotamento detectado" in markdowns
    assert len(at.get("vega_lite_chart")) == 1


def test_day_selector_asks_the_api_for_the_chosen_date() -> None:
    calls: list[tuple] = []

    def recording_limit(device_id: str, date: str | None = None, scenario: str | None = None):
        calls.append((device_id, date, scenario))
        return FAKE_LIMIT

    at = AppTest.from_file("../views/equipment.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"
    with api_online(), patch("services.api_client.get_device_limit", side_effect=recording_limit):
        at.run()
        # O rótulo é formatado ("ter 22/09"), o valor enviado à API é a data ISO.
        at.selectbox[0].set_value("ter 22/09").run()

    assert not at.exception
    assert calls[-1] == ("tractor-01", "2026-09-22", None)


def test_scenario_toggle_reaches_the_limit_endpoint_and_warns_on_screen() -> None:
    calls: list[tuple] = []

    def recording_limit(device_id: str, date: str | None = None, scenario: str | None = None):
        calls.append((device_id, date, scenario))
        return {**FAKE_LIMIT, "scenario": scenario}

    at = AppTest.from_file("../views/equipment.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"
    with api_online(), patch("services.api_client.get_device_limit", side_effect=recording_limit):
        at.run()
        assert not at.warning
        at.toggle[0].set_value(True).run()

    assert not at.exception
    assert calls[-1][2] == "heavy_rain"
    assert _scenario_warnings(at)


def test_day_and_scenario_travel_between_pages() -> None:
    """Roteiro da demo, com navegação de verdade: dia chuvoso e cenário seguem para o equipamento.

    A cópia de `session_state` entre dois `AppTest` não serve aqui: ela pula a limpeza de
    estado de widget que o Streamlit faz **na troca de página** (`_remove_stale_widgets`).
    """
    calls: list[tuple] = []

    def recording_limit(device_id: str, date: str | None = None, scenario: str | None = None):
        calls.append((device_id, date, scenario))
        return {**FAKE_LIMIT, "scenario": scenario}

    at = AppTest.from_file("../app.py", default_timeout=60)

    with api_online(), patch("services.api_client.get_device_limit", side_effect=recording_limit):
        at.run()  # Início
        at.switch_page("views/risk_map.py").run()
        at.toggle[0].set_value(True).run()  # cenário: chuva forte
        [button for button in at.button if button.label == "Ver dia"][3].click().run()

        at.switch_page("views/home.py").run()  # passa pela Início antes de seguir
        at.switch_page("views/equipment.py").run()

    assert not at.exception
    assert at.session_state["selected_day_date"] == "2026-09-22"
    assert at.session_state["risk_scenario_heavy_rain"] is True
    assert calls[-1] == ("tractor-01", "2026-09-22", "heavy_rain")

    day_selectbox = [box for box in at.selectbox if box.label == "Dia"][0]
    assert day_selectbox.value == "2026-09-22"
    assert at.toggle[0].value is True
    assert _scenario_warnings(at)


def test_live_refresh_keeps_the_selectors() -> None:
    """Critério da W5: o recarregamento automático não reseta o que o usuário escolheu."""
    at = AppTest.from_file("../views/equipment.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"

    with api_online(telemetry=FAKE_TELEMETRY):
        at.run()
        at.selectbox[0].set_value("ter 22/09").run()
        at.toggle[0].set_value(True).run()
        chosen = (at.selectbox[0].value, at.toggle[0].value, at.session_state["farm_id"])

        # O pior caso do fragmento: a página inteira roda de novo.
        at.run()

    assert not at.exception
    assert (at.selectbox[0].value, at.toggle[0].value, at.session_state["farm_id"]) == chosen
    assert at.session_state["selected_day_date"] == "2026-09-22"


def test_live_fragment_has_no_widgets_and_no_weather_calls() -> None:
    """O bloco ao vivo só desenha: nada de widget (que resetaria) nem de rota de clima."""
    captured: dict = {}
    real_fragment = st.fragment

    def capturing_fragment(**kwargs):
        decorator = real_fragment(**kwargs)

        def wrap(function):
            captured["live_panel"] = function  # a função crua, sem o decorador
            return decorator(function)

        return wrap

    weather_calls: list[str] = []

    def recording_risk(farm_id: str, days: int = 7, scenario: str | None = None):
        weather_calls.append("risk")
        return fake_get_risk(farm_id, days=days, scenario=scenario)

    def recording_limit(device_id: str, date: str | None = None, scenario: str | None = None):
        weather_calls.append("limit")
        return FAKE_LIMIT

    at = AppTest.from_file("../views/equipment.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"

    widget_names = (
        "selectbox",
        "toggle",
        "button",
        "radio",
        "checkbox",
        "slider",
        "date_input",
        "text_input",
        "multiselect",
        "segmented_control",
    )

    with (
        api_online(telemetry=FAKE_TELEMETRY),
        patch("streamlit.fragment", capturing_fragment),
        patch("services.api_client.get_risk", side_effect=recording_risk),
        patch("services.api_client.get_device_limit", side_effect=recording_limit),
    ):
        at.run()
        assert not at.exception
        live_panel = captured["live_panel"]
        calls_before_the_fragment = list(weather_calls)

        with ExitStack() as stack:
            for name in widget_names:
                stack.enter_context(
                    patch(
                        f"streamlit.{name}",
                        side_effect=AssertionError(f"o bloco ao vivo criou um widget: st.{name}"),
                    )
                )
            live_panel("tractor-01")

    # Rodar o bloco ao vivo de novo não repete nenhuma chamada de previsão/limite.
    assert weather_calls == calls_before_the_fragment


# ----------------------------------------------------------------- W6 e W7


def test_hazard_icons_appear_in_the_reasons() -> None:
    at = _run_risk_map()
    with api_online():
        [button for button in at.button if button.label == "Ver dia"][3].click().run()

    markdowns = " ".join(element.value for element in at.markdown)
    assert "🚜 **Capotamento**" in markdowns
    assert "🟫 **Atolamento**" in markdowns
    assert "⚡ **Raio**" in markdowns
    assert "🔥 **Incêndio**" in markdowns


def test_hazard_filter_only_changes_what_is_shown() -> None:
    at = _run_risk_map()
    with api_online():
        [button for button in at.button if button.label == "Ver dia"][3].click().run()
        at.multiselect[0].set_value(["fire"]).run()

    assert not at.exception
    markdowns = " ".join(element.value for element in at.markdown)
    assert "🔥 **Incêndio**" in markdowns
    assert "🚜 **Capotamento**" not in markdowns  # filtrado da lista de motivos
    # As porcentagens continuam sendo as da API, e a tela avisa.
    assert "Alto: **70%** da área" in markdowns
    captions = " ".join(element.value for element in at.caption)
    assert "consideram **todos** os perigos" in captions


def test_weather_strip_shows_where_lightning_and_fire_came_from() -> None:
    at = _run_risk_map()

    captions = " ".join(element.value for element in at.caption)
    assert "36,5 °C" in captions  # temperatura máxima
    assert "UR mín. 24%" in captions
    assert "CAPE 2480 J/kg" in captions
    assert "tempestade prevista" in captions


def test_recommendations_card_shows_windows_and_highlights_the_disclaimer() -> None:
    at = _run_risk_map()

    markdowns = " ".join(element.value for element in at.markdown)
    assert "07h–12h" in markdowns  # janela como chip
    assert "Evite operar máquinas na encosta oeste" in markdowns
    # A frase obrigatória da W6 não fica perdida na lista: vira aviso em destaque.
    disclaimers = [element.value for element in at.warning if "seguem proibidas" in element.value]
    assert disclaimers
    assert "- As janelas valem só" not in markdowns


def test_recommendations_card_for_a_calm_day_has_no_disclaimer() -> None:
    at = _run_risk_map("graos-sorriso")

    markdowns = " ".join(element.value for element in at.markdown)
    assert "Sem restrições de relevo e clima para hoje." in markdowns
    assert not [element for element in at.warning if "seguem proibidas" in element.value]


def test_recommendations_failure_does_not_break_the_map() -> None:
    at = AppTest.from_file("../views/risk_map.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"
    with (
        api_online(),
        patch(
            "services.api_client.get_recommendations",
            side_effect=ApiError("Serviço de clima indisponível"),
        ),
    ):
        at.run()

    assert not at.exception
    assert len(at.get("deck_gl_json_chart")) == 2  # relevo e risco continuam na tela
    warnings = " ".join(element.value for element in at.warning)
    assert "Não foi possível carregar as recomendações" in warnings


def test_flat_farm_all_yellow_from_the_fire_rule() -> None:
    """Sorriso como está hoje: plana, sem chuva e 100% 🟡 pela regra dos 30 (W7).

    A fixture 100% 🟢 continua coberta no teste anterior: dia sem alerta é caminho válido.
    """
    fire_risks = {**FAKE_RISKS, ("graos-sorriso", None): RISK_FLAT_FIRE}
    at = _run_risk_map("graos-sorriso", risks=fire_risks, recommendations=RECOMMENDATIONS_FIRE_DAY)

    assert not at.exception
    assert not at.error
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Atenção: **100%** da área" in markdowns
    assert "Alto: **0%** da área" in markdowns
    assert "🟡 🔥 **Incêndio**" in markdowns
    # Clima que explica o incêndio.
    captions = " ".join(element.value for element in at.caption)
    assert "36,5 °C" in captions
    assert "UR mín. 24%" in captions
    # Card com orientação, janela do dia inteiro e **sem** o aviso de área vermelha.
    assert "Condição de incêndio (regra dos 30)" in markdowns
    assert "06h–18h" in markdowns
    assert not [element for element in at.warning if "seguem proibidas" in element.value]


def test_hazard_filter_note_appears_in_the_map_legend() -> None:
    """Numa captura de tela isolada, a legenda precisa dizer que há filtro ativo."""
    at = _run_risk_map()
    with api_online():
        at.multiselect[0].set_value(["fire"]).run()

    captions = " ".join(element.value for element in at.caption)
    assert "Mostrando só: 🔥 Incêndio." in captions

    with api_online():
        at.multiselect[0].set_value(list(HAZARD_KEYS)).run()

    captions = " ".join(element.value for element in at.caption)
    assert "Mostrando só" not in captions


# ----------------------------------------------------------------- Subscrição (W8)


def _run_underwriting(farm_id: str = "cafe-carmo-de-minas", **kwargs) -> AppTest:
    at = AppTest.from_file("../views/underwriting.py", default_timeout=30)
    at.session_state["farm_id"] = farm_id
    with api_online(**kwargs):
        at.run()
    return at


def test_underwriting_shows_class_score_and_drivers() -> None:
    at = _run_underwriting()

    assert not at.exception
    assert not at.error
    markdowns = " ".join(element.value for element in at.markdown)
    assert "41,2" in markdowns  # score
    assert ">B</div>" in markdowns  # selo da classe
    assert "46% da área entre 8° e 15°" in markdowns  # driver
    assert "−23,0 pontos" in markdowns
    values = [metric.value for metric in at.metric]
    assert "17%" in values  # área ≥ 15°
    assert "25%" in values  # baixada


def test_underwriting_shows_the_calibration_note_in_the_open() -> None:
    """Gancho do roadmap: os pesos são v1 e não calibrados — não pode virar tooltip."""
    at = _run_underwriting()

    infos = " ".join(element.value for element in at.info)
    assert "v1-arbitrario" in infos
    assert "não calibrados" in infos
    assert "base da Sompo" in infos
    assert not at.get("expandable"), "a nota de calibração não pode ficar em expander"


def test_calibration_note_comes_from_the_api_not_from_the_code() -> None:
    """Recalibrou os pesos, a tela muda sozinha: nada de nota congelada na view."""
    recalibrated = {
        farm_id: {
            **profile,
            "weights_version": "v2-calibrado",
            "calibration_note": (
                "Pesos da v2, calibrados com a base de sinistros da Sompo (12.400 apólices)."
            ),
        }
        for farm_id, profile in FAKE_PROFILES.items()
    }
    at = _run_underwriting(profiles=recalibrated)

    infos = " ".join(element.value for element in at.info)
    assert "v2-calibrado" in infos
    assert "calibrados com a base de sinistros da Sompo" in infos
    assert "v1-arbitrario" not in infos  # nada da nota antiga sobrou no código
    assert "não calibrados" not in infos


def test_underwriting_portfolio_is_sorted_by_score() -> None:
    at = _run_underwriting()

    assert at.dataframe, "a carteira deveria aparecer como tabela"
    portfolio = at.dataframe[0].value
    assert list(portfolio["Score"]) == sorted(portfolio["Score"], reverse=True)
    assert list(portfolio["Classe"])[0] == "A"  # Sorriso na frente
    assert "➡️" in list(portfolio[""])  # a fazenda escolhida fica marcada


def test_underwriting_flat_farm_has_no_penalising_driver() -> None:
    at = _run_underwriting("graos-sorriso")

    successes = " ".join(element.value for element in at.success)
    assert "não penaliza esta fazenda" in successes


# ----------------------------------------------------------------- Relatórios (W12)


def _run_reports(farm_id: str = "cafe-carmo-de-minas", **kwargs) -> AppTest:
    at = AppTest.from_file("../views/reports.py", default_timeout=30)
    at.session_state["farm_id"] = farm_id
    with api_online(**kwargs):
        at.run()
    return at


def test_reports_show_the_purpose_of_each_report() -> None:
    at = _run_reports()

    infos = " ".join(element.value for element in at.info)
    assert "Para o gestor de frota" in infos
    assert "Para o analista da seguradora" in infos
    assert "Para subscrição e produto" in infos


def test_report_purpose_comes_from_the_api_not_from_the_code() -> None:
    """`purpose` é critério de aceite da W12: quem escreve é a API, a tela só mostra."""
    at = _run_reports(
        equipment_report={**FAKE_EQUIPMENT_REPORT, "purpose": "Proposito novo do equipamento."},
        region_report={**FAKE_REGION_REPORT, "purpose": "Proposito novo da regiao."},
        crop_report={**FAKE_CROP_REPORT, "purpose": "Proposito novo da cultura."},
    )

    infos = " ".join(element.value for element in at.info)
    assert "Proposito novo do equipamento." in infos
    assert "Proposito novo da regiao." in infos
    assert "Proposito novo da cultura." in infos
    # Nenhum texto antigo ficou preso na view.
    assert "Para o gestor de frota" not in infos
    assert "Para o analista da seguradora" not in infos
    assert "Para subscrição e produto" not in infos


def test_equipment_report_shows_kpis_and_trend() -> None:
    at = _run_reports()

    assert not at.exception
    assert not at.error
    pairs = [(metric.label, metric.value) for metric in at.metric]
    assert ("Horas operando", "5,4 h") in pairs
    assert ("Tempo acima do limite", "12,5%") in pairs
    assert ("Alertas", "4") in pairs
    assert ("Capotamentos", "1") in pairs


def test_equipment_report_without_telemetry_is_not_an_error() -> None:
    at = _run_reports(equipment_report=EMPTY_EQUIPMENT_REPORT)

    assert not at.exception
    assert not at.error
    infos = " ".join(element.value for element in at.info)
    assert "Sem telemetria neste período" in infos


def test_region_report_shows_real_psr_numbers() -> None:
    at = _run_reports()

    pairs = [(metric.label, metric.value) for metric in at.metric]
    assert ("Apólices", "329.595") in pairs
    assert ("Sinistros", "91.998") in pairs
    assert ("Taxa de sinistro", "27,9%") in pairs
    assert ("Indenização total", "R$ 5,44 bi") in pairs
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Causas de sinistro" in markdowns
    assert "Fazendas de demonstração nesta UF" in markdowns


def test_region_report_without_data_warns_instead_of_failing() -> None:
    at = _run_reports(region_report=EMPTY_REGION_REPORT)

    assert not at.exception
    assert not at.error
    warnings = " ".join(element.value for element in at.warning)
    assert "Sem dados de sinistro para este recorte" in warnings


def test_crop_report_shows_rate_and_top_event() -> None:
    at = _run_reports()

    pairs = [(metric.label, metric.value) for metric in at.metric]
    assert ("Apólices", "1.525.473") in pairs
    assert ("Taxa de sinistro", "23,5%") in pairs
    frames = [frame.value for frame in at.dataframe]
    crops = [frame for frame in frames if "Cultura" in frame.columns]
    assert crops, "a tabela de culturas deveria aparecer"
    assert "Maçã" in list(crops[0]["Cultura"])
    assert "granizo" in list(crops[0]["Causa mais frequente"])


def test_csv_links_carry_the_filters_shown_on_screen() -> None:
    """O CSV é gerado pela API — e precisa ser o **mesmo recorte** que está na tela."""
    calls: list[tuple] = []

    def recording_csv_url(path: str, params: dict | None = None) -> str:
        calls.append((path, dict(params or {})))
        return fake_csv_url(path, params)

    at = AppTest.from_file("../views/reports.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"
    with (
        api_online(),
        patch("services.api_client.report_csv_url", side_effect=recording_csv_url),
    ):
        at.run()
        [box for box in at.selectbox if box.label == "UF"][0].set_value("RS").run()
        [slider for slider in at.slider if slider.label == "Período (dias)"][0].set_value(30).run()

    assert not at.exception
    assert ("/reports/equipment/tractor-01.csv", {"days": 30}) in calls
    assert (
        "/reports/region.csv",
        {"state": "RS", "from_year": None, "to_year": None},
    ) in calls
    assert ("/reports/crop.csv", {"from_year": None, "to_year": None, "state": None}) in calls

    urls = [element.proto.url for element in at.get("link_button")]
    assert "http://api.test/api/v1/reports/equipment/tractor-01.csv?days=30" in urls
    assert "http://api.test/api/v1/reports/region.csv?state=RS" in urls


def test_reports_never_leak_a_proposal_id_even_if_the_api_sends_one() -> None:
    """Nenhuma resposta traz `proposal_id`; se passar a trazer, a tela não pode repassar.

    As tabelas montam as colunas explicitamente — trocar isso por `pd.DataFrame(rows)` faria
    este teste falhar, que é o ponto.
    """
    dirty_region = deepcopy(FAKE_REGION_REPORT)
    for row in dirty_region["top_municipalities"]:
        row["proposal_id"] = "PROP-SEGREDO-42"
    for row in dirty_region["top_events"]:
        row["proposal_id"] = "PROP-SEGREDO-42"
    dirty_crop = deepcopy(FAKE_CROP_REPORT)
    for row in dirty_crop["crops"]:
        row["proposal_id"] = "PROP-SEGREDO-42"

    at = _run_reports(region_report=dirty_region, crop_report=dirty_crop)

    assert not at.exception
    tables = " ".join(frame.value.to_csv() for frame in at.dataframe)
    rendered = " ".join(
        element.value
        for group in (at.markdown, at.caption, at.info, at.warning)
        for element in group
    )
    for haystack in (tables, rendered):
        assert "proposal_id" not in haystack
        assert "PROP-SEGREDO-42" not in haystack


# ----------------------------------------------------------------- score híbrido (W13)


def test_model_card_shows_probability_version_and_the_api_note() -> None:
    risks = {**FAKE_RISKS, ("cafe-carmo-de-minas", None): with_model(RISK_HILLY)}
    at = _run_risk_map(risks=risks)

    assert not at.exception
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Probabilidade de sinistro (modelo)" in markdowns
    assert "3,42%" in markdowns  # 0,0342 em porcentagem
    assert "modelo v1" in markdowns
    # A ressalva vem pronta da API e é mostrada como veio (nada reescrito no front).
    assert MODEL_NOTE in markdowns


def test_model_card_is_secondary_to_the_rules_level() -> None:
    """O alerta continua sendo o nível por regras; o modelo é a segunda leitura."""
    risks = {**FAKE_RISKS, ("cafe-carmo-de-minas", None): with_model(RISK_HILLY)}
    at = _run_risk_map(risks=risks)

    rendered = [element.value for element in at.markdown]
    markdowns = " ".join(rendered)
    assert "🔴 Risco alto" in markdowns  # cabeçalho do painel por regras
    captions = " ".join(element.value for element in at.caption)
    assert "continua sendo o nível 🟢 🟡 🔴 das regras" in captions
    # Hierarquia: o alerta por regras vem **antes** do cartão do modelo na página.
    rules_position = next(index for index, text in enumerate(rendered) if "Risco alto" in text)
    model_position = next(
        index for index, text in enumerate(rendered) if "Probabilidade de sinistro" in text
    )
    assert rules_position < model_position


def test_model_drivers_are_labelled_as_global_importance() -> None:
    risks = {**FAKE_RISKS, ("cafe-carmo-de-minas", None): with_model(RISK_HILLY)}
    at = _run_risk_map(risks=risks)

    markdowns = " ".join(element.value for element in at.markdown)
    assert "O que mais pesa no modelo, em geral" in markdowns
    assert "UF da fazenda" in markdowns
    captions = " ".join(element.value for element in at.caption)
    assert "não é a explicação deste dia" in captions
    assert "AUC-PR no teste" in captions


def test_without_a_model_the_card_disappears() -> None:
    """Sem artefato, a API não manda o bloco `model` **nem** a probabilidade do dia (W13)."""
    no_model = {
        **RISK_HILLY,
        "days": [
            {**day, "model_probability": None, "model_version": None, "model_drivers": None}
            for day in RISK_HILLY["days"]
        ],
    }
    at = _run_risk_map(risks={**FAKE_RISKS, ("cafe-carmo-de-minas", None): no_model})

    assert not at.exception
    assert "model" not in no_model  # é assim que a API responde hoje
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Probabilidade de sinistro (modelo)" not in markdowns


def test_model_block_without_probability_shows_no_card() -> None:
    """Defesa: metadados presentes com probabilidade nula — nada de cartão vazio.

    Hoje a API não devolve mais esse estado (sem pipeline, o bloco `model` inteiro some), mas a
    tela continua tolerando a combinação em vez de renderizar um cartão pela metade.
    """
    risks = {
        **FAKE_RISKS,
        ("cafe-carmo-de-minas", None): with_model(RISK_HILLY, probability=None),
    }
    at = _run_risk_map(risks=risks)

    assert not at.exception
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Probabilidade de sinistro (modelo)" not in markdowns
    assert MODEL_NOTE not in markdowns
    # E a tela do risco por regras continua inteira.
    assert "🔴 Risco alto" in markdowns


# ----------------------------------------------------------------- replay (W9)


def _run_replay_page(**kwargs) -> AppTest:
    at = AppTest.from_file("../views/replay.py", default_timeout=30)
    with api_online(**kwargs):
        at.run()
    return at


def test_replay_shows_both_verdicts_with_their_meaning() -> None:
    """`would_alert` e `would_alert_in_grid` são coisas diferentes e a tela diz qual é qual."""
    at = _run_replay_page()

    assert not at.exception
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Teria alertado no ponto" in markdowns
    assert "Na vizinhança (grade em volta do ponto)" in markdowns
    captions = " ".join(element.value for element in at.caption)
    assert "não que o caso foi acertado" in captions


def test_replay_limitations_are_always_visible() -> None:
    """`limitations` inteiro na tela: nem `help=`, nem expander."""
    at = _run_replay_page()

    warnings = " ".join(element.value for element in at.warning)
    for limitation in REPLAY_LIMITATIONS:
        assert limitation in warnings
    assert "O que este replay não prova" in warnings
    assert not at.get("expandable"), "as limitações não podem ficar dentro de um expander"


def test_replay_shows_location_precision_and_source_link() -> None:
    at = _run_replay_page()

    markdowns = " ".join(element.value for element in at.markdown)
    captions = " ".join(element.value for element in at.caption)
    assert "ponto típico do município" in markdowns + captions
    assert "pode estar a quilômetros da lavoura" in markdowns + captions
    urls = [element.proto.url for element in at.get("link_button")]
    assert "https://ndmais.com.br/exemplo" in urls


def test_replay_miss_is_presented_as_information_not_failure() -> None:
    at = _run_replay_page()

    at.selectbox[0].set_value("patos-de-minas-incendio-colheitadeira-2023-06-20")
    with api_online():
        at.run()

    assert not at.exception
    assert not at.error  # um "não" não é erro de tela
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Não teria alertado no ponto" in markdowns
    assert "está fora do que o motor promete detectar" in markdowns
    assert "nenhuma das 3 condições da regra dos 30" in markdowns


def test_replay_grid_is_drawn_on_the_map_with_the_accident_marker() -> None:
    """Jaborá: o ponto só acusa vento e a vizinha íngreme acusa 🔴 — no mapa, não numa tabela."""
    at = _run_replay_page()

    decks = at.get("deck_gl_json_chart")
    assert len(decks) == 1
    spec = json.loads(decks[0].proto.json)
    layers = spec["layers"]
    polygon_layer = next(layer for layer in layers if layer["@@type"] == "PolygonLayer")
    marker_layer = next(layer for layer in layers if layer["@@type"] == "ScatterplotLayer")

    # Uma célula por célula da grade, colorida pelo nível que a API mandou.
    assert len(polygon_layer["data"]) == 9
    levels = {row["nivel"].split(" ")[0] for row in polygon_layer["data"]}
    assert {"🟡", "🔴"} <= levels
    point_rows = [row for row in polygon_layer["data"] if "célula do acidente" in row["nivel"]]
    assert len(point_rows) == 1
    assert point_rows[0]["inclinacao"] == "2,2°"  # o ponto é manso…
    steepest = max(
        polygon_layer["data"], key=lambda row: float(row["inclinacao"][:-1].replace(",", "."))
    )
    assert steepest["inclinacao"] == "16,6°"  # …e a vizinha, não
    assert "🚜" in steepest["motivos"] or "🔴" in steepest["nivel"]

    # O marcador do acidente fica por cima.
    assert marker_layer["data"][0]["position"] == [-51.74389, -27.14687]
    captions = " ".join(element.value for element in at.caption)
    assert "Inclinação máxima da vizinhança: **16,6°**" in captions


def test_replay_weather_summary_comes_from_the_api() -> None:
    at = _run_replay_page()

    pairs = [(metric.label, metric.value) for metric in at.metric]
    assert ("Rajada máxima", "72 km/h") in pairs
    assert ("Chuva no dia", "0,0 mm") in pairs
    captions = " ".join(element.value for element in at.caption)
    assert "Historical Forecast API" in captions


def test_replay_weather_follows_the_payload_not_a_frozen_copy() -> None:
    """Outro caso, outro clima: os números saem da resposta, não do código da tela."""
    other_weather = {
        **REPLAY_HIT,
        "weather": {
            **REPLAY_HIT["weather"],
            "rain_mm": 54.0,
            "gust_max_kmh": 31.0,
            "temp_max_c": 19.8,
            "thunderstorm": True,
        },
        "source": "archive",
    }
    at = _run_replay_page(replays={**FAKE_REPLAYS, FAKE_REPLAY_CASES[0]["id"]: other_weather})

    pairs = [(metric.label, metric.value) for metric in at.metric]
    assert ("Chuva no dia", "54,0 mm") in pairs
    assert ("Rajada máxima", "31 km/h") in pairs
    assert ("Temperatura máx.", "19,8 °C") in pairs
    assert ("Chuva no dia", "0,0 mm") not in pairs  # o valor antigo sumiu
    assert ("Rajada máxima", "72 km/h") not in pairs
    captions = " ".join(element.value for element in at.caption)
    assert "tempestade registrada" in captions
    assert "Archive API" in captions
    assert "Historical Forecast API" not in captions


def test_replay_api_failure_shows_a_friendly_message() -> None:
    at = AppTest.from_file("../views/replay.py", default_timeout=30)
    with (
        api_online(),
        patch(
            "services.api_client.run_replay",
            side_effect=ApiError("Serviço de clima indisponível"),
        ),
    ):
        at.run()

    assert not at.exception
    assert "Serviço de clima indisponível" in [element.value for element in at.error]


def test_model_card_follows_the_artifact_not_a_frozen_copy() -> None:
    """Retreinou, a tela muda sozinha: a nota e as métricas saem do artefato, não do código."""
    retrained = {
        **FAKE_MODEL_INFO,
        "version": "v2",
        "test_pr_auc": 0.142,
        "beats_baseline": True,
        "note": (
            "Modelo retreinado com mais safras. No teste, ele **superou** o baseline por regras "
            "(AUC-PR 0,142 contra 0,091), mas siga lendo o valor como comparação entre dias e "
            "fazendas."
        ),
    }
    forecast = with_model(RISK_HILLY)
    forecast = {
        **forecast,
        "model": retrained,
        "days": [{**day, "model_version": "v2"} for day in forecast["days"]],
    }
    at = _run_risk_map(risks={**FAKE_RISKS, ("cafe-carmo-de-minas", None): forecast})

    assert not at.exception
    markdowns = " ".join(element.value for element in at.markdown)
    captions = " ".join(element.value for element in at.caption)
    assert "superou** o baseline" in markdowns
    assert "não superou" not in markdowns  # nada da nota de hoje sobrou no código
    assert "modelo v2" in markdowns
    assert "0,142" in captions
    assert "0,073" not in captions


def test_a_tiny_probability_is_not_shown_as_zero() -> None:
    """Abaixo de 0,005% duas casas virariam "0,00%", que parece tela quebrada."""
    forecast = with_model(RISK_HILLY, probability=0.00002)
    at = _run_risk_map(risks={**FAKE_RISKS, ("cafe-carmo-de-minas", None): forecast})

    markdowns = " ".join(element.value for element in at.markdown)
    assert "menor que 0,01%" in markdowns
    assert "0,00%" not in markdowns


def test_replay_summary_never_shows_the_score_without_the_caveat() -> None:
    """O placar vem pronto da API e a ressalva anda colada nele."""
    at = _run_replay_page()

    assert not at.exception
    values = [metric.value for metric in at.metric]
    assert "2 de 5" in values  # no ponto
    assert "3 de 5" in values  # na grade
    assert "5 de 5" in values  # avaliados
    warnings = " ".join(element.value for element in at.warning)
    assert FAKE_REPLAY_SUMMARY_NOTE in warnings
    assert "não é taxa de acerto do produto" in warnings
    assert not at.get("expandable"), "a ressalva do placar não pode ficar em expander"


def test_replay_summary_says_when_a_case_could_not_be_evaluated() -> None:
    """Placar incompleto é dito na tela, não escondido num número menor."""
    at = _run_replay_page(replay_summary=PARTIAL_REPLAY_SUMMARY)

    assert not at.exception
    values = [metric.value for metric in at.metric]
    assert "4 de 5" in values  # avaliados
    warnings = " ".join(element.value for element in at.warning)
    assert "o placar está incompleto" in warnings
    errors = " ".join(element.value for element in at.error)
    assert "castro-tombamento-trator-2025-07-04" in errors


def test_replay_summary_failure_does_not_break_the_page() -> None:
    at = AppTest.from_file("../views/replay.py", default_timeout=30)
    with (
        api_online(),
        patch(
            "services.api_client.get_replay_summary",
            side_effect=ApiError("Serviço de clima indisponível"),
        ),
    ):
        at.run()

    assert not at.exception
    warnings = " ".join(element.value for element in at.warning)
    assert "Não foi possível carregar o placar" in warnings
    # O caso selecionado continua sendo exibido.
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Teria alertado no ponto" in markdowns


# ----------------------------------------------------------------- mutação de controle


def test_control_mutation_the_suite_really_sees_the_page_change(tmp_path) -> None:
    """Controle da varredura de mutações: prova que os testes enxergam o código atual.

    Numa varredura, é fácil obter falso negativo — copiar o projeto com `shutil.copytree`
    preserva o `__pycache__` e o `mtime`, e o bytecode velho faz a mutação "passar". Este teste
    remove, **de propósito**, a chamada que desenha o cartão do modelo numa cópia da view e
    exige que o cartão suma. Se ele passar a falhar, o problema não é a feature: é o arnês de
    teste rodando código antigo — e nenhuma outra mutação daquela rodada vale nada.

    **Cobre só `views/risk_map.py`.** Uma varredura que mute `equipment.py`, `reports.py` ou
    `replay.py` não tem rede equivalente: replique este controle para a view em questão antes
    de confiar no resultado.
    """
    source = Path(__file__).resolve().parent.parent / "views" / "risk_map.py"
    original = source.read_text(encoding="utf-8")
    call = '        _model_card(day, forecast.get("model"))\n'
    assert call in original, "a chamada do cartão mudou: atualize a mutação de controle"

    mutated_file = tmp_path / "risk_map_sem_cartao.py"
    mutated_file.write_text(original.replace(call, ""), encoding="utf-8")

    risks = {**FAKE_RISKS, ("cafe-carmo-de-minas", None): with_model(RISK_HILLY)}

    # A mesma previsão com modelo, na view original: o cartão aparece.
    intact = _run_risk_map(risks=risks)
    assert "Probabilidade de sinistro (modelo)" in " ".join(
        element.value for element in intact.markdown
    )

    # E, na cópia mutada, some.
    mutant = AppTest.from_file(str(mutated_file), default_timeout=30)
    mutant.session_state["farm_id"] = "cafe-carmo-de-minas"
    with api_online(risks=risks):
        mutant.run()

    assert not mutant.exception
    assert "Probabilidade de sinistro (modelo)" not in " ".join(
        element.value for element in mutant.markdown
    )


def test_replay_summary_lists_every_case_including_the_ones_that_failed() -> None:
    """A lista caso a caso é a prova de que o placar aparece inteiro.

    Filtrar só os casos que alertaram deixaria os números dizendo "2 de 5" enquanto a lista
    mostraria só vitórias — é o ajuste tentador na véspera da apresentação.
    """
    at = _run_replay_page()

    expected = FAKE_REPLAY_SUMMARY["cases"]
    listed = [
        element.value
        for element in at.markdown
        if " no ponto · " in element.value and " na grade — " in element.value
    ]
    assert len(listed) == len(expected)  # todos os casos, não só os que acertaram

    joined = " ".join(listed)
    for case in expected:
        assert case["title"] in joined
        assert case["source_url"] in joined
    # Os "não" aparecem com o mesmo peso dos "sim".
    assert sum("❌ no ponto" in line for line in listed) == sum(
        not case["would_alert"] for case in expected
    )
    assert any("❌ no ponto" in line for line in listed)
    assert any("✅ no ponto" in line for line in listed)
    assert any("❌ na grade" in line for line in listed)


def test_replay_summary_label_says_the_grid_is_the_neighbourhood() -> None:
    """Um print só da linha de KPIs precisa se explicar sozinho."""
    at = _run_replay_page()

    labels = [metric.label for metric in at.metric]
    assert "Alertariam na grade (vizinhança)" in labels


# ----------------------------------------------------------------- passaporte (W11)


def test_passport_preview_shows_the_period_summary() -> None:
    """KPIs pareados rótulo ↔ valor: trocar dois números tem de quebrar o teste."""
    at = _run_equipment(telemetry=FAKE_TELEMETRY)

    assert not at.exception
    assert not at.error
    pairs = [(metric.label, metric.value) for metric in at.metric]
    assert ("Horas operando", "5,4 h") in pairs
    assert ("Inclinação máxima", "61,0°") in pairs
    assert ("Tempo acima do limite", "12,5%") in pairs
    assert ("Leituras recebidas", "4320") in pairs
    assert ("Alertas de inclinação", "4") in pairs
    assert ("Capotamentos", "1") in pairs
    assert ("Ocorrências do operador", "2") in pairs
    assert ("Limites aplicados", "3") in pairs


def test_passport_preview_reuses_the_live_event_line() -> None:
    """A linha do tempo usa a mesma forma de evento de `/events` (mesmo componente)."""
    at = _run_equipment(telemetry=FAKE_TELEMETRY)

    subheaders = " ".join(element.value for element in at.subheader)
    assert "Passaporte (prévia)" in subheaders
    markdowns = " ".join(element.value for element in at.markdown)
    assert "Linha do tempo" in markdowns
    assert markdowns.count("Inclinação acima do limite") >= 2  # painel ao vivo + linha do tempo


def test_passport_roadmap_note_comes_from_the_api() -> None:
    """A ressalva do roadmap é da API: retreinou o texto, a tela muda sozinha."""
    at = _run_equipment(telemetry=FAKE_TELEMETRY)
    infos = " ".join(element.value for element in at.info)
    assert ROADMAP_NOTE in infos

    other_note = "Prévia: falta assinatura digital e portabilidade entre proprietários."
    at = _run_equipment(
        telemetry=FAKE_TELEMETRY, history={**FAKE_HISTORY, "roadmap_note": other_note}
    )

    infos = " ".join(element.value for element in at.info)
    assert other_note in infos
    assert ROADMAP_NOTE not in infos  # nada do texto antigo ficou preso na view
    assert not at.get("expandable"), "a ressalva do roadmap não pode ficar em expander"


def test_passport_without_history_is_waiting_not_an_error() -> None:
    at = _run_equipment(
        status=FAKE_STATUS_NEVER_SEEN,
        telemetry=None,
        series=EMPTY_SERIES,
        events=[],
        history=EMPTY_HISTORY,
    )

    assert not at.exception
    assert not at.error
    infos = " ".join(element.value for element in at.info)
    assert "Ainda sem histórico para este equipamento" in infos
    pairs = [(metric.label, metric.value) for metric in at.metric]
    assert ("Horas operando", "0,0 h") in pairs
    assert ("Inclinação máxima", "—") in pairs
    captions = " ".join(element.value for element in at.caption)
    assert "Sem leituras no período." in captions


def test_passport_history_failure_does_not_break_the_live_panel() -> None:
    at = AppTest.from_file("../views/equipment.py", default_timeout=30)
    at.session_state["farm_id"] = "cafe-carmo-de-minas"
    with (
        api_online(telemetry=FAKE_TELEMETRY),
        patch(
            "services.api_client.get_device_history",
            side_effect=ApiError("Serviço indisponível"),
        ),
    ):
        at.run()

    assert not at.exception
    assert "Serviço indisponível" in [element.value for element in at.error]
    markdowns = " ".join(element.value for element in at.markdown)
    assert "PERIGO" in markdowns  # o painel ao vivo continua de pé

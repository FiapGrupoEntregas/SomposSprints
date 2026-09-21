"""Relatórios e tendências de risco (W12) e exportação em CSV.

A rota só traduz HTTP; as agregações estão em `app/services/reports.py` e o perfil de terreno em
`app/services/underwriting.py`.

**A exportação é para o Excel em português**, que é onde esses relatórios vão parar: UTF-8 **com
BOM** (sem ele o Excel estraga os acentos), separador `;` e **vírgula decimal** — as duas coisas
andam juntas, porque com vírgula decimal a vírgula não pode ser separador de coluna.
"""

import csv
import io
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.clients.open_meteo import OpenMeteoClient, WeatherUnavailableError, get_open_meteo_client
from app.db import get_session
from app.schemas.report import CropReport, EquipmentReport, FarmProfileSummary, RegionReport
from app.services import farms as farms_service
from app.services import reports as reports_service
from app.services import terrain as terrain_service
from app.services.underwriting import terrain_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])

DEVICE_NOT_FOUND_MESSAGE = "Equipamento não encontrado"

# W12 — janela do relatório do equipamento
DEFAULT_TREND_DAYS = 7
MAX_TREND_DAYS = 90

# D1 — o PSR/SISSER carregado cobre estas safras
MIN_POLICY_YEAR = 2000
MAX_POLICY_YEAR = 2100

# Excel em pt-BR: BOM para os acentos, `;` como separador e `,` como decimal.
CSV_BOM = "﻿"
CSV_DELIMITER = ";"

SessionDep = Annotated[Session, Depends(get_session)]
OpenMeteoDep = Annotated[OpenMeteoClient, Depends(get_open_meteo_client)]

DeviceIdPath = Annotated[str, Path(description="Identificador do equipamento.")]
StateQuery = Annotated[str, Query(min_length=2, max_length=2, description="Sigla da UF.")]
FromYearQuery = Annotated[
    int | None,
    Query(ge=MIN_POLICY_YEAR, le=MAX_POLICY_YEAR, description="Safra inicial, inclusive."),
]
ToYearQuery = Annotated[
    int | None,
    Query(ge=MIN_POLICY_YEAR, le=MAX_POLICY_YEAR, description="Safra final, inclusive."),
]
TrendDaysQuery = Annotated[
    int, Query(ge=1, le=MAX_TREND_DAYS, description="Tamanho da janela, em dias.")
]
OptionalStateQuery = Annotated[
    str | None, Query(min_length=2, max_length=2, description="Filtra por UF (opcional).")
]


# --- Exportação em CSV ---------------------------------------------------------------------------
#
# ⚠️ As rotas `.csv` vêm **antes** das de JSON de propósito. O FastAPI casa na ordem de
# declaração, e `/equipment/{device_id}` casaria com `tractor-01.csv` capturando o `.csv` dentro
# do `device_id` — o que devolvia 404 "Equipamento não encontrado" no download.


@router.get("/equipment/{device_id}.csv", response_class=StreamingResponse)
def export_equipment_csv(
    device_id: DeviceIdPath, session: SessionDep, days: TrendDaysQuery = DEFAULT_TREND_DAYS
) -> StreamingResponse:
    """A série diária do equipamento, pronta para abrir no Excel."""
    report = get_equipment_report(device_id, session, days)
    rows = [
        {
            "data": day.date.isoformat(),
            "leituras": day.readings,
            "horas_operando": day.operating_hours,
            "pct_tempo_acima_do_limite": day.pct_time_above_limit,
            "alertas": day.alerts,
            "rolagem_maxima_deg": day.max_roll_deg,
        }
        for day in report.trend
    ]
    return _csv_response(rows, f"equipamento-{device_id}")


@router.get("/region.csv", response_class=StreamingResponse)
def export_region_csv(
    session: SessionDep,
    state: StateQuery,
    from_year: FromYearQuery = None,
    to_year: ToYearQuery = None,
) -> StreamingResponse:
    """Os municípios da UF, com taxa de sinistro e indenização."""
    report = reports_service.region_summary(
        session, state=state, from_year=from_year, to_year=to_year
    )
    rows = [
        {
            "uf": report.state,
            "municipio": item.municipality,
            "apolices": item.policies,
            "sinistros": item.claims,
            "taxa_de_sinistro_pct": item.claim_rate_pct,
            "indenizacao_total_brl": item.indemnity_total,
        }
        for item in report.top_municipalities
    ]
    return _csv_response(rows, f"regiao-{state.lower()}")


@router.get("/crop.csv", response_class=StreamingResponse)
def export_crop_csv(
    session: SessionDep,
    from_year: FromYearQuery = None,
    to_year: ToYearQuery = None,
    state: OptionalStateQuery = None,
) -> StreamingResponse:
    """As culturas, da maior para a menor taxa de sinistro."""
    report = reports_service.crop_summary(
        session, from_year=from_year, to_year=to_year, state=state
    )
    rows = [
        {
            "cultura": item.crop,
            "apolices": item.policies,
            "sinistros": item.claims,
            "taxa_de_sinistro_pct": item.claim_rate_pct,
            "indenizacao_total_brl": item.indemnity_total,
            "causa_mais_frequente": item.top_event,
        }
        for item in report.crops
    ]
    return _csv_response(rows, "culturas")


@router.get("/equipment/{device_id}", response_model=EquipmentReport)
def get_equipment_report(
    device_id: DeviceIdPath, session: SessionDep, days: TrendDaysQuery = DEFAULT_TREND_DAYS
) -> EquipmentReport:
    """Tendência do equipamento: horas, tempo acima do limite e alertas por dia (W12).

    Sem telemetria no período, a resposta vem com os totais zerados e a série vazia — é um período
    sem dados, não um erro.
    """
    farm, device = _require_device(device_id)
    return reports_service.equipment_trend(session, device.device_id, farm.id, days=days)


@router.get("/region", response_model=RegionReport)
def get_region_report(
    session: SessionDep,
    client: OpenMeteoDep,
    state: StateQuery,
    from_year: FromYearQuery = None,
    to_year: ToYearQuery = None,
) -> RegionReport:
    """Sinistros reais do PSR na UF, ao lado do perfil de terreno das fazendas (W12)."""
    return reports_service.region_summary(
        session,
        state=state,
        from_year=from_year,
        to_year=to_year,
        demo_farms=_demo_farm_profiles(state, client),
    )


@router.get("/crop", response_model=CropReport)
def get_crop_report(
    session: SessionDep,
    from_year: FromYearQuery = None,
    to_year: ToYearQuery = None,
    state: OptionalStateQuery = None,
) -> CropReport:
    """Taxa de sinistro por cultura e por causa, a partir do PSR (W12)."""
    return reports_service.crop_summary(session, from_year=from_year, to_year=to_year, state=state)


# --- internos ------------------------------------------------------------------------------------


def _require_device(device_id: str):  # type: ignore[no-untyped-def]
    found = farms_service.find_device(device_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DEVICE_NOT_FOUND_MESSAGE)
    return found


def _demo_farm_profiles(state: str, client: OpenMeteoClient) -> list[FarmProfileSummary]:
    """Perfil de terreno (W8) das fazendas de demonstração naquela UF.

    **Best-effort de propósito:** o valor do relatório está nos sinistros reais, que saem do banco
    local. Se a Open-Meteo estiver fora, o perfil some e o relatório continua útil — em vez de um
    503 que esconderia 1,5 milhão de linhas de dado real por causa de uma API externa.
    """
    profiles: list[FarmProfileSummary] = []
    for farm in farms_service.list_farms():
        if farm.state.upper() != state.upper():
            continue
        try:
            terrain = terrain_service.get_terrain(farm, client)
        except WeatherUnavailableError:
            logger.warning(
                "Perfil de terreno de '%s' indisponível; o relatório da UF %s sai sem ele.",
                farm.id,
                state,
            )
            continue

        profile = terrain_profile(terrain, farm)
        profiles.append(
            FarmProfileSummary(
                farm_id=profile.farm_id,
                municipality=profile.municipality,
                terrain_score=profile.terrain_score,
                risk_class=profile.risk_class.value,
            )
        )
    return profiles


def _csv_response(rows: list[dict[str, Any]], name: str) -> StreamingResponse:
    """Monta o CSV no formato que o Excel em português entende."""
    buffer = io.StringIO()
    buffer.write(CSV_BOM)

    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), delimiter=CSV_DELIMITER)
        writer.writeheader()
        writer.writerows([{key: _csv_value(value) for key, value in row.items()} for row in rows])
    else:
        # Arquivo vazio confunde: uma linha explicando é melhor que zero bytes.
        buffer.write("sem dados no periodo selecionado\n")

    stamp = datetime.now(UTC).strftime("%Y%m%d")
    filename = f"agrishield-{name}-{stamp}.csv"
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_value(value: Any) -> str:
    """Número com **vírgula decimal**; `None` vira vazio."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}".replace(".", ",")
    return str(value)

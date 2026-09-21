"""Replay de acidentes reais (W9).

A rota só traduz HTTP; o motor está em `app/services/replay.py` e é **o mesmo** do mapa de risco.
Falha da Open-Meteo vira 503 no tratador global de `app/main.py`.
"""

import logging
from datetime import date as date_type
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.core.clock import today_local
from app.core.logging import request_id_var
from app.db import get_session
from app.repositories import audit as audit_repository
from app.schemas.audit import DecisionSource, DecisionType
from app.schemas.farm import LatLon
from app.schemas.replay import (
    LocationPrecision,
    ReplayCase,
    ReplayRequest,
    ReplayResult,
    ReplaySummary,
)
from app.services import replay as replay_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/replay", tags=["replay"])

CASE_NOT_FOUND_MESSAGE = "Caso de replay não encontrado"
FUTURE_DATE_MESSAGE = "A data do replay precisa estar no passado"
MISSING_INPUT_MESSAGE = "Informe 'case_id' ou o trio 'lat', 'lon' e 'date'"

OpenMeteoDep = Annotated[OpenMeteoClient, Depends(get_open_meteo_client)]


@router.get("/cases", response_model=list[ReplayCase])
def list_cases() -> list[ReplayCase]:
    """Casos reais curados pelo time, cada um com a fonte e a precisão da coordenada (W9)."""
    return list(replay_service.load_cases())


@router.get("/summary", response_model=ReplaySummary)
def get_summary(client: OpenMeteoDep) -> ReplaySummary:
    """Placar dos casos cadastrados, com a ressalva junto (W9).

    Reaproveita o cache de cada replay (relevo 24 h, histórico 7 dias): caro só na primeira vez.
    Vale **aquecer antes da demo** — ver `--aquecer` em `scripts/run-demo.sh`.
    """
    return replay_service.summarize_cases(client)


@router.post("", response_model=ReplayResult)
def run_replay(
    client: OpenMeteoDep,
    session: Annotated[object, Depends(get_session)],
    body: ReplayRequest,
) -> ReplayResult:
    """Roda o motor de risco sobre um acidente real e responde "o sistema teria alertado?" (W9).

    Aceita `case_id` **ou** o trio `lat`/`lon`/`date`. Data futura → **422**: replay é sobre o
    passado, e prever o futuro já é o `/risk`.
    """
    result = _run(client, body)
    _record(session, result)
    return result


def _run(client: OpenMeteoClient, body: ReplayRequest) -> ReplayResult:
    """Escolhe entre caso cadastrado e ponto manual, validando a entrada."""
    l_ref = body.reference_tilt_limit_deg or replay_service.DEFAULT_REFERENCE_TILT_LIMIT_DEG

    if body.case_id:
        case = replay_service.get_case(body.case_id)
        if case is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=CASE_NOT_FOUND_MESSAGE
            )
        _require_past(case.date)
        return replay_service.run_case(client, case, l_ref_deg=l_ref)

    if body.lat is None or body.lon is None or body.date is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=MISSING_INPUT_MESSAGE
        )

    _require_past(body.date)
    return replay_service.run_replay(
        client,
        point=LatLon(lat=body.lat, lon=body.lon),
        day=body.date,
        l_ref_deg=l_ref,
        # Ponto informado à mão: quem informou sabe de onde ele veio, então a ressalva é a
        # intermediária. Só um caso curado pode afirmar `exato`.
        precision=LocationPrecision.APROXIMADO,
    )


def _require_past(day: date_type) -> None:
    """Replay é sobre o passado: hoje e adiante são assunto do `/risk`."""
    if day >= today_local():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=FUTURE_DATE_MESSAGE
        )


def _record(session: object, result: ReplayResult) -> None:
    """Registra o replay na trilha (I5). É o `decision_type` que a I5 já tinha reservado."""
    audit_repository.record_decision(
        session,  # type: ignore[arg-type]
        decision_type=DecisionType.REPLAY,
        entity_id=result.case.id if result.case else f"{result.location.lat},{result.location.lon}",
        inputs={
            "date": result.date.isoformat(),
            "lat": result.location.lat,
            "lon": result.location.lon,
            "location_precision": result.location_precision.value,
            "reference_tilt_limit_deg": result.reference_tilt_limit_deg,
            "source": result.source.value,
        },
        output={
            "would_alert": result.would_alert,
            "would_alert_in_grid": result.would_alert_in_grid,
            "point_level": result.point_cell.level.value,
            "verdict": result.verdict,
        },
        source=DecisionSource.API,
        request_id=request_id_var.get(),
    )

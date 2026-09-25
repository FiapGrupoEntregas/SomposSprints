"""Fazendas de demonstração (W1), o mapa de relevo (W2) e a previsão de risco (W3).

A rota só traduz HTTP; o catálogo vive em `app/services/farms.py`, o cálculo do relevo em
`app/services/terrain.py` e o motor de risco em `app/services/risk.py`.
Falha da Open-Meteo vira 503 no tratador global de `app/main.py`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlmodel import Session

from app.clients.open_meteo import OpenMeteoClient, get_open_meteo_client
from app.core.logging import request_id_var
from app.core.security import require_read_access
from app.db import get_session
from app.repositories import audit as audit_repository
from app.schemas.audit import DecisionSource, DecisionType
from app.schemas.farm import Farm, FarmSummary
from app.schemas.recommendation import DayRecommendation
from app.schemas.risk import RiskForecast, Scenario
from app.schemas.terrain import TerrainResponse
from app.schemas.underwriting import UnderwritingProfile
from app.services import farms as farms_service
from app.services import recommendations as recommendations_service
from app.services import risk as risk_service
from app.services import terrain as terrain_service
from app.services.underwriting import terrain_profile

router = APIRouter(prefix="/farms", tags=["farms"], dependencies=[Depends(require_read_access)])

FARM_NOT_FOUND_MESSAGE = "Fazenda não encontrada"

FarmIdPath = Annotated[str, Path(description="Identificador da fazenda (slug).")]

SessionDep = Annotated[Session, Depends(get_session)]

ForecastDaysQuery = Annotated[
    int,
    Query(
        ge=risk_service.MIN_FORECAST_DAYS,
        le=risk_service.MAX_FORECAST_DAYS,
        description="Quantos dias devolver, de hoje em diante (1 a 7).",
    ),
]

RecommendationDaysQuery = Annotated[
    int,
    Query(
        ge=recommendations_service.MIN_RECOMMENDATION_DAYS,
        le=recommendations_service.MAX_RECOMMENDATION_DAYS,
        description="Quantos dias devolver: 1 (hoje) ou 2 (hoje e amanhã).",
    ),
]

ScenarioQuery = Annotated[
    Scenario | None,
    Query(
        description=(
            "Cenário simulado aplicado à previsão real (regras-de-risco §10). "
            "Sem este parâmetro a resposta é a previsão real."
        )
    ),
]

ExperimentalMLPQuery = Annotated[
    bool,
    Query(
        description=(
            "Inclui scores da MLP experimental separada. Não calibrada e sem efeito nos "
            "alertas, limites ou recomendações; desativada por padrão."
        )
    ),
]


@router.get("", response_model=list[FarmSummary])
def list_farms() -> list[FarmSummary]:
    """Lista as fazendas de demonstração, para o seletor do front-web."""
    return [farm.summary() for farm in farms_service.list_farms()]


@router.get("/{farm_id}", response_model=Farm)
def get_farm(farm_id: FarmIdPath) -> Farm:
    """Detalhe de uma fazenda: geometria, limite de referência e equipamentos."""
    return _require_farm(farm_id)


@router.get("/{farm_id}/terrain", response_model=TerrainResponse)
def get_farm_terrain(
    farm_id: FarmIdPath,
    client: Annotated[OpenMeteoClient, Depends(get_open_meteo_client)],
) -> TerrainResponse:
    """Mapa de relevo da fazenda: grade 10 × 10 com inclinação, orientação e classe (W2)."""
    return terrain_service.get_terrain(_require_farm(farm_id), client)


@router.get("/{farm_id}/risk", response_model=RiskForecast)
def get_farm_risk(
    farm_id: FarmIdPath,
    client: Annotated[OpenMeteoClient, Depends(get_open_meteo_client)],
    session: SessionDep,
    days: ForecastDaysQuery = risk_service.MAX_FORECAST_DAYS,
    scenario: ScenarioQuery = None,
    include_experimental_mlp: ExperimentalMLPQuery = False,
) -> RiskForecast:
    """Previsão de risco relevo × clima, dia a dia e célula a célula (W3).

    Um `scenario` fora dos cenários implementados é recusado com 422 pela validação do FastAPI.
    A decisão entra na trilha de auditoria (I5), resumida por dia — as 700 células do detalhe
    ficam de fora, senão a trilha cresceria mais rápido que o banco inteiro.
    """
    forecast = risk_service.get_risk_forecast(
        _require_farm(farm_id),
        client,
        days=days,
        scenario=scenario,
        include_experimental_mlp=include_experimental_mlp,
    )
    _record_risk_decision(session, farm_id, days, scenario, forecast, include_experimental_mlp)
    return forecast


@router.get("/{farm_id}/recommendations", response_model=list[DayRecommendation])
def get_farm_recommendations(
    farm_id: FarmIdPath,
    client: Annotated[OpenMeteoClient, Depends(get_open_meteo_client)],
    session: SessionDep,
    days: RecommendationDaysQuery = recommendations_service.MAX_RECOMMENDATION_DAYS,
    scenario: ScenarioQuery = None,
) -> list[DayRecommendation]:
    """O que fazer hoje e amanhã: janelas seguras e orientações em texto (W6).

    As frases são **templates com números**, não texto gerado por IA, e saem do mesmo cálculo que
    colore o mapa. A decisão entra na trilha (I5) junto com as frases: o que o operador leu é
    parte do que precisa ser auditável.
    """
    farm = _require_farm(farm_id)
    recommendations = recommendations_service.get_recommendations(
        farm, client, days=days, scenario=scenario
    )
    _record_recommendation_decision(session, farm_id, days, scenario, recommendations)
    return recommendations


@router.get("/{farm_id}/underwriting", response_model=UnderwritingProfile)
def get_farm_underwriting(
    farm_id: FarmIdPath,
    client: Annotated[OpenMeteoClient, Depends(get_open_meteo_client)],
) -> UnderwritingProfile:
    """Perfil de risco do terreno para a cotação (W8, regras-de-risco §8).

    Não depende da previsão: é a característica permanente do terreno, que é o que serve para
    precificar. A resposta diz, em `calibration_note`, que os pesos da v1 **ainda não foram
    calibrados** com dados de sinistro.
    """
    farm = _require_farm(farm_id)
    return terrain_profile(terrain_service.get_terrain(farm, client), farm)


def _record_recommendation_decision(
    session: Session,
    farm_id: str,
    days: int,
    scenario: Scenario | None,
    recommendations: list[DayRecommendation],
) -> None:
    """Registra na trilha o que foi recomendado (I5).

    Tem `decision_type` próprio (`recommendation`) porque o `output` tem outro formato: quem
    filtra a trilha não deveria precisar ramificar em `inputs.endpoint` para saber o que vai
    encontrar. Guardar as frases é o que permite responder, depois de um acidente, "o que o
    sistema mandou fazer".
    """
    audit_repository.record_decision(
        session,
        decision_type=DecisionType.RECOMMENDATION,
        entity_id=farm_id,
        inputs={
            "days": days,
            "scenario": scenario.value if scenario is not None else None,
        },
        output={
            "days": [
                {
                    "date": day.date.isoformat(),
                    "windows": [
                        f"{window.start:%H:%M}-{window.end:%H:%M}" for window in day.windows
                    ],
                    "messages": day.messages,
                }
                for day in recommendations
            ]
        },
        source=DecisionSource.API,
        request_id=request_id_var.get(),
    )


def _record_risk_decision(
    session: Session,
    farm_id: str,
    days: int,
    scenario: Scenario | None,
    forecast: RiskForecast,
    include_experimental_mlp: bool,
) -> None:
    """Registra o score na trilha (I5), com um resumo por dia em vez das células."""
    audit_repository.record_decision(
        session,
        decision_type=DecisionType.RISK_SCORE,
        entity_id=farm_id,
        inputs={
            "days": days,
            "scenario": scenario.value if scenario is not None else None,
            "include_experimental_mlp": include_experimental_mlp,
            "generated_at": forecast.generated_at.isoformat(),
        },
        output={
            "days": [
                {
                    "date": day.date.isoformat(),
                    "soil_state": day.soil_state.value,
                    "tilt_limit_deg": day.tilt_limit_deg,
                    "worst_level": day.worst_level.value,
                    "pct_red": day.pct_levels.red,
                    "pct_yellow": day.pct_levels.yellow,
                    # O modelo entra na trilha **ao lado** do nível, nunca no lugar dele (W13).
                    "model_probability": day.model_probability,
                }
                for day in forecast.days
            ],
            **(
                {
                    "experimental_mlp": {
                        "available": forecast.experimental_mlp.available,
                        "version": forecast.experimental_mlp.version,
                        "note": forecast.experimental_mlp.note,
                        "days": [
                            {
                                "date": day.date.isoformat(),
                                "score": day.experimental_mlp_score,
                            }
                            for day in forecast.days
                        ],
                    }
                }
                if include_experimental_mlp and forecast.experimental_mlp is not None
                else {}
            ),
        },
        source=DecisionSource.API,
        request_id=request_id_var.get(),
        model_version=forecast.model.version if forecast.model else None,
    )


def _require_farm(farm_id: str) -> Farm:
    """Busca a fazenda ou devolve 404 com a mensagem padrão."""
    farm = farms_service.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=FARM_NOT_FOUND_MESSAGE)
    return farm

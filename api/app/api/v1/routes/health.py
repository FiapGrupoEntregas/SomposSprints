from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Verifica se a API está no ar (usado pelo front-web e pelo checklist da demo)."""
    return HealthResponse(status="ok", version=settings.version, environment=settings.environment)

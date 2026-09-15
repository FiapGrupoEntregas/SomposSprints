"""Ponto de entrada da API do AgriShield.

Desenvolvimento: `uv run fastapi dev app/main.py` (ou `uvicorn app.main:app --reload` com pip).
Documentação interativa: http://localhost:8000/docs
"""

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Motor de risco relevo × clima e ponte MQTT com o equipamento.",
    )
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()

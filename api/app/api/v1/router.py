"""Agrega todos os routers da v1. Cada feature nova registra seu router aqui."""

from fastapi import APIRouter

from app.api.v1.routes import audit, devices, farms, health, replay, reports

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(farms.router)
api_router.include_router(devices.router)
api_router.include_router(reports.router)
api_router.include_router(replay.router)
api_router.include_router(audit.router)

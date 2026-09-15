"""Agrega todos os routers da v1. Cada feature nova registra seu router aqui."""

from fastapi import APIRouter

from app.api.v1.routes import health

api_router = APIRouter()
api_router.include_router(health.router)

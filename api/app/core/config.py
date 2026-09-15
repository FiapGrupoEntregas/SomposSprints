"""Configurações da API, lidas de variáveis de ambiente com prefixo AGRISHIELD_ (ou do .env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AGRISHIELD_", extra="ignore")

    app_name: str = "AgriShield API"
    version: str = "0.1.0"
    environment: str = "dev"

    # Open-Meteo (I1) — gratuito, sem chave
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_elevation_url: str = "https://api.open-meteo.com/v1/elevation"
    open_meteo_historical_url: str = "https://historical-forecast-api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"

    # MQTT (I2) — ver docs/contrato-mqtt.md
    mqtt_host: str = "broker.hivemq.com"
    mqtt_port: int = 1883
    mqtt_topic_prefix: str = "agrishield/fiap-sompo-2026"


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Configurações da API, lidas de variáveis de ambiente com prefixo AGRISHIELD_ (ou do .env)."""

import secrets
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _random_client_id_suffix() -> str:
    """Sufixo aleatório do `client_id` MQTT.

    O broker público derruba a conexão antiga quando dois clientes usam o mesmo `client_id`; o
    sufixo garante que duas instâncias da API (ou um recarregamento do `fastapi dev`) convivam.
    """
    return secrets.token_hex(4)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AGRISHIELD_", extra="ignore")

    app_name: str = "AgriShield API"
    version: str = "0.1.0"
    environment: str = "dev"

    # Segurança (I5, ADR-013) — chaves aceitas em `X-API-Key`, separadas por vírgula.
    # Vazio por padrão, de propósito: sem chave configurada, **nenhuma escrita é aceita**
    # (falha fechada). Configure `AGRISHIELD_API_KEYS` no .env antes da demo.
    api_keys: str = ""
    # Por quantos dias a trilha de auditoria (`decision_log`) é mantida (I5).
    decision_retention_days: int = 30

    # Banco local (ADR-009) — o arquivo .db está no .gitignore
    database_url: str = "sqlite:///./agrishield.db"

    # Open-Meteo (I1) — gratuito, sem chave
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_elevation_url: str = "https://api.open-meteo.com/v1/elevation"
    open_meteo_historical_url: str = "https://historical-forecast-api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"

    # MQTT (I2) — ver document/contrato-mqtt.md
    mqtt_host: str = "broker.hivemq.com"
    mqtt_port: int = 1883
    mqtt_topic_prefix: str = "agrishield/fiap-sompo-2026"
    # Desligue (`false`) para subir a API sem tocar no broker; é o que os testes fazem.
    mqtt_enabled: bool = True
    mqtt_client_id_suffix: str = Field(default_factory=_random_client_id_suffix)
    # Segundos entre as tentativas de reconexão do paho (mínimo e máximo, com backoff).
    mqtt_reconnect_min_s: int = 1
    mqtt_reconnect_max_s: int = 30
    # Keepalive MQTT. É ele que descobre que a conexão morreu quando não chega mais tráfego:
    # o padrão do paho (60 s) deixaria o painel ao vivo cego por quase um minuto.
    mqtt_keepalive_s: int = 15
    # W4 — de quanto em quanto tempo a API republica o limite do dia (1 h)
    mqtt_publish_interval_s: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()

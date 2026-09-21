"""Schemas das rotas de equipamento: limite do dia (W4) e painel ao vivo (W5).

O limite segue `regras-de-risco §4`
([document/regras-de-risco.md](../../../document/regras-de-risco.md)); o que vai para o MQTT segue
[document/contrato-mqtt.md](../../../document/contrato-mqtt.md#config-w4--e3-retained).

As mensagens que **chegam** do ESP32 ficam em `app/schemas/mqtt.py`; aqui estão só as respostas
HTTP que o front consome.
"""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.mqtt import AlertLevel, DeviceState, EventType
from app.schemas.risk import RiskLevel, Scenario, SoilState


class DeviceLimit(BaseModel):
    """Limite de inclinação do dia de um equipamento (W4).

    `reference_tilt_limit_deg` é o limite em solo seco, que o front mostra ao lado do número
    grande ("10,0° hoje · 15,0° em solo seco").
    """

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(description="Identificador do equipamento.")
    farm_id: str = Field(description="Fazenda a que ele pertence.")
    date: dt.date = Field(description="Dia a que o limite se refere (fuso America/Sao_Paulo).")
    scenario: Scenario | None = Field(
        default=None, description="Cenário simulado usado no cálculo, ou nulo na previsão real."
    )

    tilt_limit_deg: float = Field(gt=0.0, description="Limite do dia, em graus (§4).")
    reference_tilt_limit_deg: float = Field(
        gt=0.0, description="`L_ref` do equipamento: o limite dele em solo seco."
    )
    warn_ratio: float = Field(
        gt=0.0, le=1.0, description="Fração do limite que acende 🟡 no equipamento (§4, E2)."
    )

    soil_state: SoilState = Field(description="Estado do solo do dia (§3).")
    risk_level: RiskLevel = Field(description="Pior nível da fazenda no dia (§6).")
    rain_72h_mm: float = Field(description="Chuva de 72 h que definiu o estado do solo, em mm.")
    wind_max_kmh: float | None = Field(
        default=None, description="Vento máximo previsto, em km/h (regra dos 30 local, E6)."
    )

    valid_until: int = Field(
        ge=0, description="Epoch em segundos do fim do dia; depois disso o ESP32 avisa no Serial."
    )
    reason: str = Field(
        min_length=1, description="Motivo curto para o display OLED, **sem acento** (E7)."
    )


class DeviceLimitPublishRequest(BaseModel):
    """Corpo de `POST /devices/{device_id}/limit/publish`. Os dois campos são opcionais."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date | None = Field(
        default=None, description="Dia do limite. Sem ele, hoje no fuso America/Sao_Paulo."
    )
    scenario: Scenario | None = Field(
        default=None, description="Cenário simulado (regras §10), para a demo do dia chuvoso."
    )


class PublishedDeviceLimit(BaseModel):
    """O que a API publicou no broker, devolvido para o front confirmar o envio (W4)."""

    model_config = ConfigDict(extra="forbid")

    limit: DeviceLimit = Field(description="Limite calculado.")
    topic: str = Field(description="Tópico MQTT em que o `config` foi publicado.")
    published_at: dt.datetime = Field(description="Quando a API publicou (UTC).")
    payload: dict = Field(description="O JSON exato entregue ao broker (contrato-mqtt).")


class DeviceStatusResponse(BaseModel):
    """Estado do equipamento para a faixa do painel ao vivo (W5)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(description="Identificador do equipamento.")
    state: DeviceState = Field(
        description="`offline` se o LWT disser offline **ou** se passar do silêncio máximo."
    )
    reported_state: DeviceState | None = Field(
        default=None, description="O que o próprio equipamento anunciou, ou nulo se nunca falou."
    )
    last_seen_at: dt.datetime | None = Field(
        default=None, description="Recepção da última telemetria (UTC), ou nulo se não houver."
    )
    seconds_since_last_telemetry: float | None = Field(
        default=None, ge=0.0, description="Segundos desde a última telemetria, ou nulo."
    )


class TelemetryPoint(BaseModel):
    """Uma leitura do equipamento, como o painel e o gráfico precisam (W5)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(description="Identificador do equipamento.")
    ts: dt.datetime = Field(description="Hora do dispositivo (UTC); a de recepção se o NTP falhou.")
    received_at: dt.datetime = Field(description="Quando a API recebeu (UTC).")
    seq: int = Field(ge=0, description="Contador do equipamento; detecta perda de mensagem.")
    roll_deg: float = Field(description="Rolagem, em graus.")
    pitch_deg: float = Field(description="Arfagem, em graus.")
    accel_g: float = Field(description="Módulo da aceleração, em g.")
    temp_c: float | None = Field(default=None, description="Temperatura em °C, ou nulo.")
    humidity_pct: float | None = Field(default=None, description="Umidade em %, ou nulo.")
    fire_conditions: int | None = Field(
        default=None, ge=0, le=3, description="Piso das condições da regra dos 30 (E6)."
    )
    tilt_limit_deg: float = Field(gt=0.0, description="Limite em uso no equipamento, em graus.")
    alert_level: AlertLevel = Field(description="Nível do alerta local.")


class TelemetrySeries(BaseModel):
    """Série do gráfico dos últimos minutos, já amostrada se for longa demais (W5)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(description="Identificador do equipamento.")
    minutes: int = Field(gt=0, description="Janela pedida, em minutos.")
    total: int = Field(ge=0, description="Quantas leituras existem na janela.")
    sampled: bool = Field(description="Se a série foi amostrada por passar do máximo de pontos.")
    points: list[TelemetryPoint] = Field(description="Leituras em ordem cronológica.")


class DeviceEventResponse(BaseModel):
    """Um evento do equipamento para a lista do painel (W5)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(description="Identificador único do evento.")
    device_id: str = Field(description="Identificador do equipamento.")
    type: EventType = Field(description="Tipo do evento.")
    ts: dt.datetime = Field(description="Hora do dispositivo (UTC).")
    received_at: dt.datetime = Field(description="Quando a API recebeu (UTC).")
    roll_deg: float | None = Field(default=None, description="Rolagem no evento, em graus.")
    pitch_deg: float | None = Field(default=None, description="Arfagem no evento, em graus.")
    accel_g: float | None = Field(default=None, description="Aceleração no evento, em g.")
    tilt_limit_deg: float | None = Field(default=None, description="Limite em uso, em graus.")
    context: dict | None = Field(
        default=None,
        description="Os 30 s antes do evento (`fields` + `rows`), só em rollover/incident_report.",
    )

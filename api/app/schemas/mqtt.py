"""Schemas das mensagens MQTT trocadas com o ESP32 (I2).

Espelham [document/contrato-mqtt.md](../../../document/contrato-mqtt.md). **Mudou lá, muda aqui e
no firmware, no mesmo trabalho.**

Três decisões que vêm direto do contrato e explicam o formato destes modelos:

- **`extra="ignore"` no que chega do dispositivo.** O contrato manda ignorar campos desconhecidos,
  para o firmware poder acrescentar um campo sem derrubar a API.
- **Nada de `StrictFloat`.** A ArduinoJson serializa `10.0` como `10`, então todo campo `float`
  precisa aceitar `int`. A coerção padrão do pydantic já faz isso (exemplo real em
  `tests/fixtures/telemetry_sample.json`).
- **`ts` pode ser `0`** enquanto o NTP não sincroniza; quem grava usa a hora de recepção
  (ver `app/repositories/devices.py`).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AlertLevel(StrEnum):
    """Nível do alerta local do equipamento (contrato-mqtt, `telemetry`).

    É diferente do `RiskLevel` do motor de risco: aqui existe `rollover`, que é o travamento do
    alerta máximo depois de um capotamento (E5).
    """

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    ROLLOVER = "rollover"


class EventType(StrEnum):
    """Tipos de evento que o ESP32 publica (contrato-mqtt, `events`)."""

    TILT_ALERT = "tilt_alert"
    ROLLOVER = "rollover"
    INCIDENT_REPORT = "incident_report"
    LIMIT_APPLIED = "limit_applied"


class DeviceState(StrEnum):
    """Estado da conexão do equipamento (contrato-mqtt, `status` retained + LWT)."""

    ONLINE = "online"
    OFFLINE = "offline"


class TelemetryMessage(BaseModel):
    """`{prefix}/devices/{device_id}/telemetry`, a cada 5 s (E4)."""

    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(min_length=1, description="Identificador do equipamento.")
    ts: int = Field(ge=0, description="Epoch em segundos (UTC). `0` = NTP ainda não sincronizou.")
    seq: int = Field(ge=0, description="Contador que reinicia no boot; detecta perda de mensagem.")
    roll_deg: float = Field(description="Rolagem filtrada, em graus.")
    pitch_deg: float = Field(description="Arfagem filtrada, em graus.")
    accel_g: float = Field(description="Módulo da aceleração, em g.")
    temp_c: float | None = Field(
        default=None, description="Temperatura em °C; nulo se o DHT falhar."
    )
    humidity_pct: float | None = Field(
        default=None, description="Umidade relativa em %; nulo se o DHT falhar."
    )
    fire_conditions: int | None = Field(
        default=None,
        ge=0,
        le=3,
        description=(
            "Condições ativas da regra dos 30 (E6). **Opcional e é um piso, não a contagem "
            "exata**: com dado parcial o ESP32 conta só o que conhece, e omite o campo quando "
            "não conhece nenhum dos três valores."
        ),
    )
    tilt_limit_deg: float = Field(gt=0.0, description="Limite em uso no equipamento, em graus.")
    alert_level: AlertLevel = Field(description="Nível do alerta local no momento da leitura.")


class EventContext(BaseModel):
    """Janela de 30 s que acompanha `rollover` e `incident_report` (1 amostra/s)."""

    model_config = ConfigDict(extra="ignore")

    fields: list[str] = Field(min_length=1, description="Nomes das colunas de `rows`.")
    rows: list[list[float]] = Field(
        description="Uma linha por segundo, da mais antiga (`t_s = -29`) até a do evento (`0`)."
    )


class EventMessage(BaseModel):
    """`{prefix}/devices/{device_id}/events` (E2, E5, E8).

    Cada evento chega **3 vezes** com o mesmo `event_id`; a ponte deduplica (I2).
    """

    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(min_length=1, description="Identificador do equipamento.")
    event_id: str = Field(
        min_length=1, description="`{device_id}-{ts ou millis}-{contador}`; chave da deduplicação."
    )
    ts: int = Field(ge=0, description="Epoch em segundos (UTC). `0` = NTP ainda não sincronizou.")
    type: EventType = Field(description="Tipo do evento.")
    roll_deg: float | None = Field(default=None, description="Rolagem no evento, em graus.")
    pitch_deg: float | None = Field(default=None, description="Arfagem no evento, em graus.")
    accel_g: float | None = Field(default=None, description="Aceleração no evento, em g.")
    tilt_limit_deg: float | None = Field(
        default=None, description="Limite em uso no equipamento, em graus."
    )
    context: EventContext | None = Field(
        default=None, description="Só em `rollover` e `incident_report`."
    )


class StatusMessage(BaseModel):
    """`{prefix}/devices/{device_id}/status`, retained, com `offline` via LWT."""

    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(min_length=1, description="Identificador do equipamento.")
    state: DeviceState = Field(description="`online` ao conectar, `offline` pelo LWT.")


class ConfigMessage(BaseModel):
    """`{prefix}/devices/{device_id}/config`, publicado pela API com QoS 1 e retained (W4).

    Só `tilt_limit_deg` é obrigatório; o ESP32 mantém o valor atual do que não vier.
    `reason` vai para o display OLED, cuja fonte padrão **não tem acento** (E7).
    """

    model_config = ConfigDict(extra="forbid")

    tilt_limit_deg: float = Field(gt=0.0, description="Limite do dia, em graus (regras §4).")
    warn_ratio: float | None = Field(
        default=None, gt=0.0, le=1.0, description="Fração do limite que acende 🟡 (padrão 0,8)."
    )
    soil_state: str | None = Field(default=None, description="`dry`, `moist` ou `saturated`.")
    risk_level: str | None = Field(default=None, description="Pior nível da fazenda hoje.")
    wind_max_kmh: float | None = Field(
        default=None, ge=0.0, description="Vento máximo previsto, em km/h (regra dos 30, E6)."
    )
    valid_until: int | None = Field(
        default=None, ge=0, description="Epoch em segundos até quando o limite vale."
    )
    reason: str | None = Field(default=None, description="Texto curto para o display, sem acento.")

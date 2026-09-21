"""Tabelas do SQLite (SQLModel).

Uma tabela por assunto, sempre com chave, `created_at` e índice para as consultas que existem de
verdade. As migrações não existem (ADR-009): mudou o esquema, apague o `.db` local e recarregue.

⚠️ **LGPD (ADR-011):** nenhuma coluna aqui guarda nome, documento ou qualquer dado pessoal. O CSV do
PSR traz `NM_SEGURADO` e `NR_DOCUMENTO_SEGURADO`, e eles são descartados na leitura
(`app/services/psr_ingest.py`), antes de virarem `DataFrame`.
"""

from datetime import UTC, date, datetime

from sqlmodel import Field, Index, SQLModel


def _now() -> datetime:
    """Agora em UTC, ciente do fuso.

    ⚠️ O SQLite **não guarda fuso**: este valor entra ciente e volta ingênuo na leitura. A
    convenção do projeto é que toda coluna de data e hora está em **UTC sem fuso** — quem monta
    uma linha à mão deve normalizar com `app.repositories.devices.utc_naive`, que é o que os
    repositórios já fazem. O comportamento aqui é o mesmo desde a D1 (`Policy.created_at`).
    """
    return datetime.now(UTC)


class Policy(SQLModel, table=True):
    """Uma apólice do PSR/SISSER, já limpa e anonimizada (D1).

    Cada linha é uma apólice subvencionada, com a coordenada da propriedade, a cultura, a vigência e
    — quando houve sinistro — o valor indenizado e a causa normalizada em `event_category`.
    Fonte e limitações: document/dados-e-modelo.md §1.
    """

    __tablename__ = "policy"
    __table_args__ = (
        # D2 seleciona a amostra de treino por UF e safra.
        Index("ix_policy_state_year", "state", "policy_year"),
        # D2 busca relevo e clima por coordenada, e agrupa propriedades vizinhas.
        Index("ix_policy_lat_lon", "lat", "lon"),
        # O relatório e o rótulo do modelo filtram por causa do sinistro.
        Index("ix_policy_event_category", "event_category"),
    )

    id: int | None = Field(default=None, primary_key=True)
    # `ID_PROPOSTA` no CSV: identifica a proposta e é o que usamos para deduplicar.
    proposal_id: str = Field(unique=True, index=True)

    insurer: str | None = None
    municipality: str | None = None
    state: str | None = Field(default=None, max_length=2)
    geocode_ibge: str | None = None

    lat: float
    lon: float
    # "decimal" = veio de NR_DECIMAL_LATITUDE/LONGITUDE; "dms" = convertido de grau/minuto/segundo.
    coordinate_source: str

    crop: str | None = None
    area_ha: float | None = None
    coverage_value: float | None = None
    premium: float | None = None

    start_date: date | None = None
    end_date: date | None = None
    policy_year: int | None = None

    # `None` = a apólice não teve sinistro registrado; 0.0 = houve registro com valor zero.
    indemnity_value: float | None = None
    event_category: str

    created_at: datetime = Field(default_factory=_now)


class Telemetry(SQLModel, table=True):
    """Uma leitura de telemetria do equipamento (I3, contrato-mqtt `telemetry`).

    `ts` é a hora do dispositivo e `received_at` a hora em que a API recebeu. Quando o ESP32 manda
    `ts: 0` (NTP ainda não sincronizou), `ts` recebe a hora de recepção — a regra está no contrato
    e é aplicada em `app/repositories/devices.py`.
    """

    __tablename__ = "telemetry"
    __table_args__ = (
        # O painel ao vivo (W5) e o histórico (W11) leem sempre por equipamento e janela de tempo.
        Index("ix_telemetry_device_received", "device_id", "received_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    device_id: str = Field(index=True)
    ts: datetime
    received_at: datetime = Field(default_factory=_now)
    seq: int

    roll_deg: float
    pitch_deg: float
    accel_g: float
    # Nulos quando o DHT22 falha (contrato-mqtt).
    temp_c: float | None = None
    humidity_pct: float | None = None
    # Piso das condições da regra dos 30 (E6); `None` = o dispositivo não soube dizer.
    fire_conditions: int | None = None

    tilt_limit_deg: float
    alert_level: str


class DeviceEvent(SQLModel, table=True):
    """Um evento do equipamento (I3, contrato-mqtt `events`).

    `event_id` é **único**: o ESP32 publica cada evento 3 vezes com o mesmo id e só a primeira
    cópia vira linha. O payload completo fica em `payload_json`, inclusive o `context` de 30 s dos
    eventos `rollover` e `incident_report` (~1 KB), que o laudo do sinistro usa.
    """

    __tablename__ = "device_event"
    __table_args__ = (
        # A tela do equipamento lista os eventos mais recentes daquele dispositivo.
        Index("ix_device_event_device_received", "device_id", "received_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(unique=True, index=True)
    device_id: str = Field(index=True)
    type: str
    ts: datetime
    received_at: datetime = Field(default_factory=_now)
    payload_json: str


class DeviceStatus(SQLModel, table=True):
    """Último estado de conexão conhecido de cada equipamento (I3, contrato-mqtt `status`)."""

    __tablename__ = "device_status"

    device_id: str = Field(primary_key=True)
    state: str
    updated_at: datetime = Field(default_factory=_now)


class PublishedConfig(SQLModel, table=True):
    """Cada `config` que a API publicou para um equipamento (I3, W4).

    Guardar o que foi enviado é o que permite auditar depois qual limite o equipamento recebeu e
    quando (I5).
    """

    __tablename__ = "published_config"
    __table_args__ = (Index("ix_published_config_device_published", "device_id", "published_at"),)

    id: int | None = Field(default=None, primary_key=True)
    device_id: str = Field(index=True)
    published_at: datetime = Field(default_factory=_now)
    payload_json: str


class DecisionLog(SQLModel, table=True):
    """Trilha de auditoria: **toda decisão** que vira alerta, limite ou score (I5).

    Guarda o que entrou (`inputs_json`), o que saiu (`output_json`) e **com qual versão de regra e
    de modelo** a decisão foi tomada. É o que torna o score defensável perante a seguradora: um
    mês depois ainda dá para reconstruir por que aquele limite foi 10° e não 12,5°.

    `request_id` amarra a linha à requisição HTTP que a originou (cabeçalho `X-Request-ID`), e é
    `None` quando a decisão nasceu fora de uma requisição — na tarefa periódica ou no MQTT.

    ⚠️ Nem `inputs_json` nem `output_json` guardam chave de API, nome ou documento: quem escreve
    monta o dicionário campo a campo (ver `app/repositories/audit.py`).
    """

    __tablename__ = "decision_log"
    __table_args__ = (
        # A consulta da trilha (`GET /api/v1/audit`) filtra por tipo e por entidade, sempre
        # ordenando do mais recente para o mais antigo.
        Index("ix_decision_log_type_created", "decision_type", "created_at"),
        Index("ix_decision_log_entity_created", "entity_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now, index=True)
    request_id: str | None = Field(default=None, index=True)

    # `risk_score` · `tilt_limit` · `alert` · `replay`
    decision_type: str = Field(index=True)
    # A quem a decisão se refere: `farm_id` no risco, `device_id` no limite e no alerta.
    entity_id: str = Field(index=True)

    inputs_json: str
    output_json: str

    rule_version: str
    model_version: str | None = None
    # De onde veio a decisão: `api`, `scheduler` ou `device`.
    source: str


class EventIntegrity(SQLModel, table=True):
    """Hash do payload cru de cada evento MQTT, para detectar alteração depois (I5).

    Fica numa tabela **à parte** de `device_event` de propósito: o projeto não usa migrações
    (ADR-009), e acrescentar coluna a uma tabela que já existe no `agrishield.db` local quebraria
    as gravações sem um `DROP` — que levaria junto o `policy`, com 1,5 milhão de linhas da D1.
    Tabela nova o `create_all` cria sozinho, sem tocar em nada do que já está gravado.

    `payload_sha256` é calculado sobre os **bytes exatos** que chegaram do broker, antes de
    qualquer interpretação: é isso que permite provar, no laudo de um sinistro, que o evento
    guardado é o que o equipamento publicou.
    """

    __tablename__ = "event_integrity"

    event_id: str = Field(primary_key=True)
    device_id: str = Field(index=True)
    payload_sha256: str
    payload_bytes: int
    created_at: datetime = Field(default_factory=_now)

"""Ponte MQTT entre a API e o ESP32 (I2).

Contrato: [document/contrato-mqtt.md](../../../document/contrato-mqtt.md).

O `paho-mqtt` roda em **thread própria** (`loop_start`), criada e encerrada no `lifespan` do
FastAPI. A API **sobe mesmo com o broker fora do ar**: `connect_async` não bloqueia e o paho
reconecta sozinho, com backoff.

O coração testável é `handle_message(topic, payload)`, que não depende de rede: ele extrai o
`device_id` do tópico, valida o JSON com os schemas de `app/schemas/mqtt.py`, deduplica evento
repetido e entrega a mensagem ao callback. **Payload inválido vira log e descarte**, nunca uma
exceção que derrube a thread — se a ponte morresse, o painel ao vivo morria junto.
"""

import ipaddress
import json
import logging
import ssl
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.schemas.mqtt import ConfigMessage, EventMessage, StatusMessage, TelemetryMessage

logger = logging.getLogger(__name__)

# contrato-mqtt — sufixos dos tópicos e QoS de cada direção
TELEMETRY_SUFFIX = "telemetry"
EVENTS_SUFFIX = "events"
STATUS_SUFFIX = "status"
CONFIG_SUFFIX = "config"

# contrato-mqtt — `config` vai com QoS 1 e retained, para o ESP32 receber o último limite ao ligar
CONFIG_QOS = 1
CONFIG_RETAINED = True

# O ESP32 publica telemetria em QoS 0 e status em QoS 1; assinamos tudo em 1, que é o que o broker
# pode entregar (a entrega real nunca passa do QoS com que a mensagem foi publicada).
SUBSCRIBE_QOS = 1

# Feature I2 — quantos `event_id` recentes ficam na memória para deduplicar as 3 cópias do ESP32
RECENT_EVENT_IDS = 500

# Mensagem única quando não dá para falar com o equipamento (W4).
MQTT_UNAVAILABLE_MESSAGE = "Equipamento sem conexão MQTT"

# Redes privadas aceitas para endereços IP configurados como broker de produção.
PRIVATE_BROKER_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)

# contrato-mqtt — tópicos publicados com **retained**. Apagar um retained em MQTT é publicar um
# payload de **zero byte**, então, só nestes, payload vazio é operação normal e não erro.
RETAINED_SUFFIXES = frozenset({CONFIG_SUFFIX, STATUS_SUFFIX})

TelemetryHandler = Callable[[TelemetryMessage], None]
# O evento leva junto os **bytes crus** que chegaram do broker: é sobre eles que a I5 calcula o
# hash de integridade, antes de qualquer interpretação.
EventHandler = Callable[[EventMessage, bytes], None]
StatusHandler = Callable[[StatusMessage], None]


class RecentEventIds:
    """Lembra os últimos `maxlen` `event_id` vistos, para descartar as repetições (I2).

    O ESP32 publica cada evento 3 vezes com o mesmo `event_id` (contrato-mqtt), porque o
    PubSubClient só publica em QoS 0. A trava definitiva é a `UNIQUE` do banco (I3); esta aqui
    evita ida ao banco e serve mesmo quando a persistência está desligada.
    """

    def __init__(self, maxlen: int = RECENT_EVENT_IDS) -> None:
        self._maxlen = maxlen
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, event_id: str) -> bool:
        """Registra o `event_id`. Devolve `False` se ele já tinha passado por aqui."""
        with self._lock:
            if event_id in self._seen:
                self._seen.move_to_end(event_id)
                return False
            self._seen[event_id] = None
            while len(self._seen) > self._maxlen:
                self._seen.popitem(last=False)
            return True

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


class MqttBridge:
    """Cliente MQTT da API: assina o que o equipamento publica e publica o `config` dele."""

    def __init__(
        self,
        settings: Settings,
        on_telemetry: TelemetryHandler | None = None,
        on_event: EventHandler | None = None,
        on_status: StatusHandler | None = None,
        client: mqtt.Client | None = None,
    ) -> None:
        self._settings = settings
        self._on_telemetry = on_telemetry
        self._on_event = on_event
        self._on_status = on_status
        self._recent_events = RecentEventIds()
        self._connected = threading.Event()
        tls_context = _build_mqtt_tls_context(settings)
        self._client = client if client is not None else self._build_client()
        if tls_context is not None:
            self._client.tls_set_context(tls_context)
        if settings.mqtt_username and settings.mqtt_password:
            self._client.username_pw_set(settings.mqtt_username.strip(), settings.mqtt_password)

    @property
    def prefix(self) -> str:
        """Prefixo dos tópicos, o mesmo do `TOPIC_PREFIX` do firmware."""
        return self._settings.mqtt_topic_prefix

    @property
    def is_connected(self) -> bool:
        """Se a ponte está conectada ao broker agora."""
        return self._connected.is_set()

    def topic_for(self, device_id: str, suffix: str) -> str:
        """Monta `{prefix}/devices/{device_id}/{suffix}` (contrato-mqtt)."""
        return f"{self.prefix}/devices/{device_id}/{suffix}"

    def start(self) -> None:
        """Conecta em segundo plano e começa a ouvir.

        Usa `connect_async`, então **não levanta exceção com o broker fora do ar**: a API sobe e o
        paho fica tentando reconectar sozinho (critério de aceite do I2).

        O **keepalive** é explícito e curto (15 s). Quem descobre que a conexão morreu, quando não
        chega mais tráfego, é ele: com o padrão do paho (60 s), medimos **57,9 s** entre derrubar
        o broker e a API perceber — um minuto de painel ao vivo cego, que é exatamente o que
        acontece com Wi-Fi de auditório.

        A detecção leva de **1× a 2× o keepalive**, dependendo de onde no ciclo de PINGREQ a
        conexão caiu: com 15 s, de 15 s a **30 s no pior caso**. O custo é um PINGREQ de 2 bytes
        a cada 15 s de silêncio, algo como 6 KB por dia — irrelevante perto de ficar cego na
        apresentação.
        """
        self._client.reconnect_delay_set(
            min_delay=self._settings.mqtt_reconnect_min_s,
            max_delay=self._settings.mqtt_reconnect_max_s,
        )
        self._client.connect_async(
            self._settings.mqtt_host,
            self._settings.mqtt_port,
            keepalive=self._settings.mqtt_keepalive_s,
        )
        self._client.loop_start()
        logger.info(
            "Ponte MQTT iniciada para %s:%s, prefixo '%s', keepalive de %s s.",
            self._settings.mqtt_host,
            self._settings.mqtt_port,
            self.prefix,
            self._settings.mqtt_keepalive_s,
        )

    def stop(self) -> None:
        """Desconecta e encerra a thread do paho.

        `loop_stop()` do paho v2 não tem timeout: ele marca o fim do laço e espera a thread sair.
        Com o broker fora do ar isso leva cerca de 1 s, porque a espera da reconexão acorda de
        segundo em segundo para checar o encerramento — medido, não suposto.
        """
        try:
            self._client.disconnect()
            self._client.loop_stop()
        except Exception:  # noqa: BLE001 — encerrar a API nunca pode falhar por causa do broker
            logger.warning(
                "Falha ao encerrar a ponte MQTT; seguindo com o shutdown.", exc_info=True
            )
        finally:
            self._connected.clear()
            logger.info("Ponte MQTT encerrada.")

    def publish_config(self, device_id: str, config: ConfigMessage) -> bool:
        """Publica o `config` do equipamento com **QoS 1 e retained** (contrato-mqtt, W4).

        Retained é o que faz o ESP32 receber o último limite assim que conecta.

        Devolve `False` quando o broker **não aceitou a publicação agora** — por exemplo, com a
        ponte desconectada. Isso **não** quer dizer "perdido para sempre": em QoS 1 o paho guarda
        a mensagem na fila de saída e a envia quando reconectar. Quem chama (a W4) deve tratar o
        `False` como *não confirmado*, nunca como *não será entregue*.
        """
        topic = self.topic_for(device_id, CONFIG_SUFFIX)
        payload = config.model_dump_json(exclude_none=True)
        info = self._client.publish(topic, payload, qos=CONFIG_QOS, retain=CONFIG_RETAINED)

        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning(
                "O broker não confirmou o config em %s agora (rc=%s); o paho tenta de novo ao "
                "reconectar.",
                topic,
                info.rc,
            )
            return False

        logger.info("Config publicado em %s: %s", topic, payload)
        return True

    def handle_message(self, topic: str, payload: bytes | str) -> bool:
        """Valida e encaminha uma mensagem. Devolve `True` quando ela foi processada.

        Descarta, com log e sem levantar exceção: tópico fora do contrato, JSON quebrado, payload
        que não passa no schema, `device_id` do payload diferente do tópico e evento repetido.
        """
        parsed_topic = self._parse_topic(topic)
        if parsed_topic is None:
            logger.debug("Tópico fora do contrato, ignorado: %s", topic)
            return False

        device_id, suffix = parsed_topic

        if not _as_bytes(payload) and suffix in RETAINED_SUFFIXES:
            # Payload vazio em tópico retained é o comando "esqueça o que estava guardado aqui" —
            # é o que o `--clean-retained` do simulador (I6) faz. Tratar como JSON inválido enchia
            # a trilha de rastreabilidade (I5) de alarme falso justamente na hora da demo.
            logger.debug("Retained apagado em %s.", topic)
            return False

        data = self._parse_json(topic, payload)
        if data is None:
            return False

        if suffix == TELEMETRY_SUFFIX:
            return self._handle_telemetry(topic, device_id, data)
        if suffix == EVENTS_SUFFIX:
            return self._handle_event(topic, device_id, data, _as_bytes(payload))
        if suffix == STATUS_SUFFIX:
            return self._handle_status(topic, device_id, data)

        logger.debug("Sufixo de tópico sem tratamento, ignorado: %s", topic)
        return False

    # --- internos ------------------------------------------------------------------------------

    def _build_client(self) -> mqtt.Client:
        """Cliente paho v2 com os callbacks já ligados."""
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"agrishield-api-{self._settings.mqtt_client_id_suffix}",
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        if getattr(reason_code, "is_failure", False):
            logger.warning(
                "Broker MQTT recusou a conexão (%s); o paho vai tentar de novo.", reason_code
            )
            return

        self._connected.set()
        for suffix in (TELEMETRY_SUFFIX, EVENTS_SUFFIX, STATUS_SUFFIX):
            topic = f"{self.prefix}/devices/+/{suffix}"
            client.subscribe(topic, qos=SUBSCRIBE_QOS)
            logger.info("Assinado: %s", topic)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, *args: Any, **kwargs: Any) -> None:
        # A assinatura do callback mudou entre as versões do paho; o que importa aqui é o estado.
        self._connected.clear()
        logger.warning("Ponte MQTT desconectada do broker; reconectando em segundo plano.")

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        """Entrada da thread do paho. Nada aqui pode levantar exceção."""
        try:
            self.handle_message(message.topic, message.payload)
        except Exception:  # noqa: BLE001 — uma mensagem ruim não pode derrubar a ponte
            logger.exception("Erro inesperado ao tratar a mensagem de %s.", message.topic)

    def _parse_topic(self, topic: str) -> tuple[str, str] | None:
        """`{prefix}/devices/{device_id}/{suffix}` → `(device_id, suffix)`, ou `None`."""
        if not topic.startswith(f"{self.prefix}/"):
            return None

        parts = topic[len(self.prefix) + 1 :].split("/")
        if len(parts) != 3 or parts[0] != "devices" or not parts[1]:
            return None
        return parts[1], parts[2]

    def _parse_json(self, topic: str, payload: bytes | str) -> dict[str, Any] | None:
        text = _as_bytes(payload).decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("JSON inválido em %s; mensagem descartada: %.200s", topic, text)
            return None

        if not isinstance(data, dict):
            logger.warning("Payload de %s não é um objeto JSON; descartado.", topic)
            return None
        return data

    def _validate(
        self, model: type[BaseModel], topic: str, device_id: str, data: dict[str, Any]
    ) -> Any:
        """Valida o payload e confere que ele fala do mesmo equipamento do tópico."""
        try:
            message = model.model_validate(data)
        except ValidationError as error:
            logger.warning(
                "Payload de %s fora do contrato; descartado. %s",
                topic,
                "; ".join(
                    f"{'.'.join(str(part) for part in detail['loc'])}: {detail['msg']}"
                    for detail in error.errors()
                ),
            )
            return None

        if message.device_id != device_id:  # type: ignore[attr-defined]
            logger.warning(
                "O 'device_id' do payload ('%s') não é o do tópico ('%s'); descartado. "
                "O broker é público, então a API não confia no payload divergente.",
                message.device_id,  # type: ignore[attr-defined]
                device_id,
            )
            return None
        return message

    def _handle_telemetry(self, topic: str, device_id: str, data: dict[str, Any]) -> bool:
        message = self._validate(TelemetryMessage, topic, device_id, data)
        if message is None:
            return False

        logger.info(
            "Telemetria de %s: seq=%s roll=%s° limite=%s° nível=%s",
            message.device_id,
            message.seq,
            message.roll_deg,
            message.tilt_limit_deg,
            message.alert_level.value,
        )
        if self._on_telemetry is not None:
            self._on_telemetry(message)
        return True

    def _handle_event(self, topic: str, device_id: str, data: dict[str, Any], raw: bytes) -> bool:
        message = self._validate(EventMessage, topic, device_id, data)
        if message is None:
            return False

        if not self._recent_events.add(message.event_id):
            logger.debug("Evento '%s' repetido, ignorado.", message.event_id)
            return False

        logger.info(
            "Evento '%s' de %s: %s", message.event_id, message.device_id, message.type.value
        )
        if self._on_event is not None:
            self._on_event(message, raw)
        return True

    def _handle_status(self, topic: str, device_id: str, data: dict[str, Any]) -> bool:
        message = self._validate(StatusMessage, topic, device_id, data)
        if message is None:
            return False

        logger.info("Equipamento %s está %s.", message.device_id, message.state.value)
        if self._on_status is not None:
            self._on_status(message)
        return True


def get_mqtt(request: Request) -> MqttBridge:
    """Dependência do FastAPI: a ponte do processo, guardada em `app.state.mqtt` no lifespan.

    Fica aqui (e não em `app/main.py`) para as rotas poderem importá-la sem import circular.

    Sem o lifespan não existe ponte: em vez de estourar um `KeyError` (500), isso vira o mesmo
    503 de broker fora do ar, que é a leitura correta para quem chamou.
    """
    bridge: MqttBridge | None = getattr(request.app.state, "mqtt", None)
    if bridge is None:
        logger.error("Ponte MQTT não inicializada (o lifespan não rodou).")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=MQTT_UNAVAILABLE_MESSAGE
        )
    return bridge


def _as_bytes(payload: bytes | str) -> bytes:
    """Os bytes exatos da mensagem. É sobre eles que o hash de integridade é calculado (I5)."""
    return payload if isinstance(payload, bytes) else payload.encode("utf-8")


def _build_mqtt_tls_context(settings: Settings) -> ssl.SSLContext | None:
    """Valida a política do broker e prepara TLS sem propagar detalhes de certificados."""
    if not settings.mqtt_enabled:
        return None

    production = settings.environment != "dev"
    host = settings.mqtt_host.strip().lower().rstrip(".")
    has_username = bool(settings.mqtt_username.strip())
    has_password = bool(settings.mqtt_password.strip())
    has_client_cert = bool(settings.mqtt_client_cert.strip())
    has_client_key = bool(settings.mqtt_client_key.strip())
    has_ca = bool(settings.mqtt_ca_cert.strip())

    if has_username != has_password:
        raise ValueError("Configuração MQTT incompleta: informe usuário e senha juntos.")
    if has_client_cert != has_client_key:
        raise ValueError("Configuração MQTT inválida: certificado e chave do cliente são pareados.")

    if production:
        if not _is_private_broker_host(host):
            raise ValueError("Configuração MQTT de produção exige um host de broker privado.")
        if not has_ca:
            raise ValueError("Configuração MQTT de produção exige TLS com CA configurada.")
        if not has_username:
            raise ValueError("Configuração MQTT de produção exige credenciais do broker.")

    if not any((has_ca, has_client_cert, has_client_key)):
        if has_username:
            raise ValueError("Credenciais MQTT não podem ser usadas sem TLS.")
        return None

    try:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        if has_ca:
            context.load_verify_locations(cafile=settings.mqtt_ca_cert.strip())
        if has_client_cert:
            context.load_cert_chain(
                certfile=settings.mqtt_client_cert.strip(),
                keyfile=settings.mqtt_client_key.strip(),
            )
    except (OSError, ssl.SSLError, ValueError):
        raise ValueError(
            "Configuração TLS MQTT inválida; confira CA e certificado do cliente."
        ) from None

    return context


def _is_private_broker_host(host: str) -> bool:
    """Aceita apenas IP RFC1918/ULA ou DNS de escopo interno."""
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host.endswith((".internal", ".local"))

    return any(
        address.version == network.version and address in network
        for network in PRIVATE_BROKER_NETWORKS
    )

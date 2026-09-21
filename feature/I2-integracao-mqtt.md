# I2 — Ponte MQTT na API

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api |
| Depende de | [contrato MQTT](../document/contrato-mqtt.md) |
| Janela | 18/09 |
| Responsável | Dev |
| Status | ✅ Pronto |

## Objetivo

Conectar a API ao broker MQTT para **receber** telemetria, eventos e status dos equipamentos e
**publicar** o limite do dia. Sem essa ponte, o ESP32 fica isolado.

## História de usuário

> Como **API**, quero **ouvir o que o equipamento manda e falar com ele**, para **mostrar os dados ao vivo e enviar o limite seguro**.

## Escopo

**Inclui**
- Cliente `paho-mqtt` (v2) rodando em thread própria, iniciado e parado no `lifespan` do FastAPI.
- Assinatura de `{prefix}/devices/+/telemetry`, `…/+/events` e `…/+/status`.
- Validação dos payloads com Pydantic. Payload inválido → log e descarte, **sem derrubar nada**.
- Deduplicação de eventos por `event_id` (o ESP32 manda cada evento 3 vezes).
- `publish_config(device_id, config)` com QoS 1 e **retained**.
- Encaminhamento das mensagens válidas para o repositório (I3).

**Não inclui**
- Broker próprio, TLS ou autenticação (ADR-008).

## Implementação

### API (`api/`)
- Dependência: `uv add paho-mqtt` + `scripts/sync-requirements.sh`.
- Settings novas: `mqtt_enabled: bool = True` (os testes usam `False`) e `mqtt_client_id_suffix` (aleatório por padrão).
- `app/schemas/mqtt.py`: `TelemetryMessage`, `EventMessage`, `StatusMessage` e `ConfigMessage`, espelhando o contrato.
- `app/mqtt/bridge.py`:
  - classe `MqttBridge(settings, on_telemetry, on_event, on_status)`, com `start()`, `stop()`, `publish_config()` e `is_connected`
  - `handle_message(topic, payload)` **puro e testável**: extrai o `device_id` do tópico, valida e deduplica (LRU com os últimos 500 `event_id`)
  - reconexão automática (`reconnect_delay_set(1, 30)`)
- `app/main.py`: `lifespan` que cria a ponte, guarda em `app.state.mqtt` e para no shutdown.
- Dependência FastAPI `get_mqtt(request)` para as rotas (W4 usa).

## Critérios de aceite

- [x] A API sobe mesmo com o broker fora do ar (loga um aviso e tenta de novo em segundo plano).
- [x] `mosquitto_pub` com uma telemetria válida → a mensagem é registrada (log em nível INFO; depois do I3, fica gravada).
- [x] Payload inválido (JSON quebrado ou campo obrigatório faltando) → log WARNING, nada gravado, a ponte continua funcionando.
- [x] O mesmo `event_id` recebido 3 vezes → processado **uma vez**.
- [x] `publish_config` → um novo assinante recebe o último config na hora (retained).
- [x] Os testes rodam sem broker (`mqtt_enabled=False` e testes de `handle_message`).

## Testes

- `tests/test_mqtt_bridge.py`: parse do tópico, validação de cada tipo, deduplicação e tópico desconhecido ignorado.

## Riscos e plano B

| Risco | Plano B |
|---|---|
| HiveMQ instável | Trocar `AGRISHIELD_MQTT_HOST` para `test.mosquitto.org` (e `MQTT_HOST` no firmware) |
| A thread do paho travar o shutdown | `disconnect()` + `loop_stop()` no lifespan. O `loop_stop()` do paho v2 não tem timeout: ele espera a thread sair, e a espera da reconexão acorda de segundo em segundo para checar o encerramento. Medido: ~1 s com o broker fora, 0 s conectado |
| Outro time publicando no mesmo tópico | O prefixo é próprio e a validação é estrita |

## Tarefas

- [x] `uv add paho-mqtt` + sincronizar os requirements
- [x] Schemas MQTT
- [x] `handle_message` + testes
- [x] `MqttBridge` + lifespan
- [x] Teste manual contra o `broker.hivemq.com` (com o `paho`, porque o `mosquitto-clients` não está instalado na máquina)
- [x] Atualizar `.env.example`, `api/README.md` e `README.md`

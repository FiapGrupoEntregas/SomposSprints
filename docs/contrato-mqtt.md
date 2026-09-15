# Contrato MQTT — ESP32 ⇄ API

Este é o contrato entre `iot/` e `api/`. **Mudou aqui, muda o firmware e a API no mesmo PR.**

## Broker

| Item | Valor |
|---|---|
| Host | `broker.hivemq.com` |
| Porta | `1883` (TCP, sem TLS) |
| Autenticação | nenhuma |
| Prefixo | `agrishield/fiap-sompo-2026`. Precisa ser igual em `AGRISHIELD_MQTT_TOPIC_PREFIX` (API) e em `TOPIC_PREFIX` (firmware) |

> ⚠️ O broker é **público**: qualquer pessoa pode ler e publicar nesses tópicos. **Nunca** mande dado
> pessoal, senha ou token. A API valida todo payload e descarta o que não estiver no formato.

## Tópicos

`{prefix}/devices/{device_id}/...`, com `device_id` no padrão `tractor-01`.

| Sufixo | Direção | QoS | Retained | Quando |
|---|---|---|---|---|
| `telemetry` | ESP32 → API | 0 | não | a cada 5 s (E4) |
| `events` | ESP32 → API | 0 (×3) | não | na hora do evento (E2, E5, E8) |
| `config` | API → ESP32 | 1 | **sim** | quando o limite muda e a cada 1 h (W4) |
| `status` | ESP32 → API | 1 | **sim** | ao conectar (`online`) e via LWT (`offline`) |

- O PubSubClient **só publica com QoS 0**. Para não perder eventos críticos, o ESP32 envia cada evento **3 vezes** (com 500 ms de intervalo) usando o **mesmo `event_id`**, e a API **deduplica** por `event_id`.
- A API assina `{prefix}/devices/+/telemetry`, `.../+/events` e `.../+/status`.

## Regras gerais

- JSON em `snake_case`. Os campos levam a unidade no nome: `_deg`, `_g`, `_c`, `_pct`, `_kmh`, `_mm`.
- `ts` = epoch em segundos (UTC), vindo do NTP (`pool.ntp.org`). Se o NTP ainda não sincronizou, o ESP32 manda `ts: 0` e a API usa a hora em que recebeu.
- Quem recebe **ignora campos desconhecidos** e **mantém o valor atual** quando um campo não vem.
- Adicionar um campo é compatível. Renomear ou remover exige atualizar este documento e os dois lados.

## Payloads

### `telemetry` (E4)

```json
{
  "device_id": "tractor-01",
  "ts": 1789500000,
  "seq": 1532,
  "roll_deg": 12.4,
  "pitch_deg": -3.1,
  "accel_g": 1.01,
  "temp_c": 31.5,
  "humidity_pct": 28.0,
  "fire_conditions": 2,
  "tilt_limit_deg": 10.0,
  "alert_level": "red"
}
```

| Campo | Tipo | Observação |
|---|---|---|
| `seq` | int | contador que reinicia no boot. Serve para detectar perda de mensagens |
| `temp_c`, `humidity_pct` | float \| null | `null` se o DHT22 falhar (NaN) |
| `fire_conditions` | int 0–3 \| ausente | **opcional**. Quantas condições da regra dos 30 estão ativas (E6) |
| `alert_level` | enum | `green` · `yellow` · `red` · `rollover` |

### `events` (E2, E5, E8)

```json
{
  "device_id": "tractor-01",
  "event_id": "tractor-01-1789500000-7",
  "ts": 1789500000,
  "type": "rollover",
  "roll_deg": 61.0,
  "pitch_deg": 2.3,
  "accel_g": 2.8,
  "tilt_limit_deg": 10.0,
  "context": {
    "fields": ["t_s", "roll_deg", "pitch_deg", "accel_g"],
    "rows": [[-29, 9.8, -2.9, 1.0], [-28, 10.4, -3.0, 1.0], [0, 61.0, 2.3, 2.8]]
  }
}
```

| `type` | Quando | Tem `context`? |
|---|---|---|
| `tilt_alert` | a inclinação entra no nível 🔴 (1 vez por entrada, com histerese) | não |
| `rollover` | capotamento detectado (E5) | sim, últimos 30 s a 1 Hz |
| `incident_report` | o operador apertou o botão de ocorrência (E8) | sim, últimos 30 s a 1 Hz |
| `limit_applied` | o ESP32 aplicou um limite novo recebido em `config` (E3) | não |

`event_id` = `{device_id}-{ts ou millis}-{contador}`. `t_s` é o tempo relativo ao evento, em segundos (≤ 0).
Com 30 linhas o payload fica em ~1 KB, e por isso o firmware usa `mqtt.setBufferSize(2048)`.

### `config` (W4 → E3), retained

```json
{
  "tilt_limit_deg": 10.0,
  "warn_ratio": 0.8,
  "soil_state": "saturated",
  "risk_level": "red",
  "wind_max_kmh": 22.0,
  "valid_until": 1789550000,
  "reason": "42 mm de chuva em 72 h"
}
```

| Campo | Uso no ESP32 |
|---|---|
| `tilt_limit_deg` | **obrigatório**. Novo limite (E2, E3) |
| `warn_ratio` | fração do limite que dispara 🟡 (padrão 0,8) |
| `soil_state` | `dry` · `moist` · `saturated`. Exibido no display (E7) |
| `risk_level` | pior nível da fazenda hoje. Exibido no display (E7) |
| `wind_max_kmh` | vento máximo previsto para hoje. Usado na regra dos 30 local (E6), já que não há anemômetro |
| `valid_until` | epoch. Depois disso o ESP32 continua usando o último limite, mas avisa no Serial |
| `reason` | texto curto, **sem acento** (a fonte padrão do OLED não tem acentos) |

### `status`, retained

```json
{ "device_id": "tractor-01", "state": "online" }
```

O LWT, registrado no `connect`, publica `"state": "offline"` com retained se o ESP32 cair.

## Como depurar

- HiveMQ WebSocket Client: http://www.hivemq.com/demos/websocket-client/. Assine `agrishield/fiap-sompo-2026/#`.
- `mosquitto_sub -h broker.hivemq.com -t 'agrishield/fiap-sompo-2026/#' -v`
- Para publicar um limite manualmente (teste do E3 sem a API):
  `mosquitto_pub -h broker.hivemq.com -r -t 'agrishield/fiap-sompo-2026/devices/tractor-01/config' -m '{"tilt_limit_deg":10}'`

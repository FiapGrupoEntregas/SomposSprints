# Contrato MQTT — ESP32 ⇄ API

Este é o contrato entre `iot/` e `api/`. **Mudou aqui, muda o firmware e a API no mesmo PR.**

## Segurança do transporte e ambientes

| Item | Valor |
|---|---|
| Demo local/Wokwi | `broker.hivemq.com:1883`, sem TLS nem autenticação; apenas dados sintéticos |
| Produção | broker privado, TLS com validação de certificado e credenciais próprias; ACL por dispositivo é configuração obrigatória do broker |
| Prefixo | `agrishield/fiap-sompo-2026`. Precisa ser igual em `AGRISHIELD_MQTT_TOPIC_PREFIX` (API) e em `TOPIC_PREFIX` (firmware) |

> ⚠️ O broker compartilhado é **público e não autenticado**: qualquer pessoa pode ler, publicar,
> sobrescrever mensagens retained e forjar telemetria/eventos nesses tópicos. Validar o formato do
> JSON não autentica a origem. Esse modo serve somente à demonstração com dados sintéticos; não
> conecte equipamento real nem envie dado pessoal, credencial ou informação operacional.

Com `AGRISHIELD_ENVIRONMENT` diferente de `dev`, a API trata o broker como ambiente de produção:
se MQTT estiver habilitado, falha no início sem host privado (IP privado ou DNS `.internal`/`.local`),
CA TLS válida ou usuário e senha. A conexão valida o certificado do broker e usa TLS 1.2 ou superior.
Certificado e chave de cliente são opcionais, mas precisam ser configurados juntos. As variáveis são
`AGRISHIELD_MQTT_CA_CERT`, `AGRISHIELD_MQTT_CLIENT_CERT`, `AGRISHIELD_MQTT_CLIENT_KEY`,
`AGRISHIELD_MQTT_USERNAME` e `AGRISHIELD_MQTT_PASSWORD`. Erros de configuração não exibem o
conteúdo de credenciais nem de certificados.

Esses guardrails protegem a configuração e o transporte da conexão da API; **não configuram nem
criam autenticação, autorização ou ACL no broker MQTT**. Use um broker privado com ACLs que permitam
a cada equipamento publicar apenas em seus tópicos de
telemetria/eventos/status e assinar apenas seu tópico de configuração; a API deve publicar
configurações e assinar os tópicos necessários com uma identidade de serviço. Não reutilize
credenciais entre dispositivos. TLS protege o transporte e a identidade do broker, mas não substitui
ACL, autenticação do cliente, autorização por tópico nem proteção contra replay de comandos. A
configuração retained deve ser assinada/autenticada por dispositivo e ter validade e controle
anti-replay antes de comandar equipamento real.

O prefixo abaixo é público por ser parte da demo e não é um segredo nem mecanismo de autorização.

## Tópicos

`{prefix}/devices/{device_id}/...`, com `device_id` no padrão `tractor-01`.

| Sufixo | Direção | QoS | Retained | Quando |
|---|---|---|---|---|
| `telemetry` | ESP32 → API | 0 | não | a cada 5 s (E4) |
| `events` | ESP32 → API | 0 (×3) | não | na hora do evento (E2, E5, E8) |
| `config` | API → ESP32 | 1 | **sim** | quando o limite muda e a cada 1 h (W4) |
| `status` | ESP32 → API | 1 | **sim** | ao conectar (`online`) e via LWT (`offline`) |

- O PubSubClient **só publica com QoS 0**. Para não perder eventos críticos, o ESP32 envia cada evento **3 vezes** (com 500 ms de intervalo) usando o **mesmo `event_id`**, e a API **deduplica** por `event_id`.
- A API assina `{prefix}/devices/+/telemetry`, `.../+/events` e `.../+/status`, **com QoS 1**. A entrega efetiva é o menor QoS entre publicação e assinatura, então a telemetria do ESP32 (QoS 0) continua QoS 0; quem ganha entrega confirmada é o `status`. *(Fixado na I2.)*

## Regras gerais

- JSON em `snake_case`. Os campos levam a unidade no nome: `_deg`, `_g`, `_c`, `_pct`, `_kmh`, `_mm`.
- `ts` = epoch em segundos (UTC), vindo do NTP (`pool.ntp.org`). Se o NTP ainda não sincronizou, o ESP32 manda `ts: 0` e a API usa a hora em que recebeu.
- Quem recebe **ignora campos desconhecidos** e **mantém o valor atual** quando um campo não vem.
- A API **descarta** a mensagem cujo `device_id` do payload não seja o do tópico. O broker é público: confiar no tópico e sobrescrever o payload faria uma leitura de qualquer pessoa entrar no histórico de um equipamento real. *(Fixado na I2.)*
- Adicionar um campo é compatível. Renomear ou remover exige atualizar este documento e os dois lados.
- **Float inteiro chega sem casa decimal.** O ArduinoJson serializa `10.0` como `10` e `28.0` como `28`. Quem valida na API precisa aceitar `int` onde o campo é `float` (o Pydantic faz essa coerção sozinho; só não use `StrictFloat`). Vale para `tilt_limit_deg`, `temp_c`, `humidity_pct`, `accel_g`, `roll_deg` e `pitch_deg`. Exemplo real em `api/tests/fixtures/telemetry_sample.json`.

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
| `fire_conditions` | int 0–3 \| ausente | **opcional**. Quantas condições da regra dos 30 estão ativas (E6): `temp_c > 30`, `humidity_pct < 30`, `wind_max_kmh > 30`. Com dado parcial é um **piso**, não a contagem exata: o ESP32 conta só o que conhece, então `1` com `temp_c: null` quer dizer "pelo menos 1 das 3". O campo é **omitido** quando nenhum dos três valores é conhecido (sem DHT e sem `wind_max_kmh`), porque `0` afirmaria "nenhuma condição ativa" e o caso é "não sei" |
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
As linhas vão da mais antiga (`-29`) para a do próprio evento (`0`), uma por segundo. Medido com a
ArduinoJson do firmware: **725 bytes** num `rollover` típico (738 num `incident_report`, que tem o `type` mais longo) e **1028 bytes** no pior caso com 30 linhas
— por isso o `mqtt.setBufferSize(2048)`.

Dentro do `context`, `roll_deg` e `pitch_deg` são a leitura filtrada (a mesma base da telemetria) e
`accel_g` é o **pico daquele segundo**: um impacto dura poucos décimos de segundo e uma média o
esconderia justamente onde ele importa para o laudo do sinistro.

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
| `risk_level` | pior nível da fazenda hoje. Guardado no ESP32, mas **não cabe no layout atual do display** (E7) |
| `wind_max_kmh` | vento máximo previsto para hoje. Usado na regra dos 30 local (E6), já que não há anemômetro |
| `valid_until` | epoch. Depois disso o ESP32 continua usando o último limite, mas avisa no Serial |
| `reason` | texto curto. Hoje só aparece no Serial do dispositivo; mantenha **sem acento** caso volte para a tela (a fonte padrão do OLED não tem acentos) |

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

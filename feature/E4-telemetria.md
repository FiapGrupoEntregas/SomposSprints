# E4 — Telemetria via MQTT

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | iot |
| Depende de | E1 |
| Janela | 19/09 |
| Responsável | Dev |
| Status | 🟦 Em revisão (implementado; falta o GIF da simulação) |

## Objetivo

Enviar à API, periodicamente, o estado da máquina, para o painel ao vivo (W5) e o histórico (W11).

## Escopo

**Inclui**
- Publicação a cada **5 s** no tópico `telemetry`, no formato do [contrato](../document/contrato-mqtt.md#telemetry-e4).
- Publicação **imediata** quando o nível de alerta muda (E2).
- Timestamp via NTP e contador `seq`.

**Não inclui**
- Buffer offline de telemetria. Se o MQTT cair, as leituras desse período se perdem, mas os **alertas locais continuam funcionando**.

## Implementação (`iot/src/main.ino`)

- `configTime(0, 0, "pool.ntp.org")` no `setup()`, depois do Wi-Fi. `currentEpochSeconds()` devolve 0 se o ano for < 2024 (NTP ainda não sincronizado).
- `publishTelemetry()`: monta um `JsonDocument` com `device_id, ts, seq, roll_deg, pitch_deg, accel_g, temp_c, humidity_pct, tilt_limit_deg, alert_level` e publica sem retained.
- Valores `NaN` do DHT → `null` (`doc["temp_c"] = nullptr`).
- Arredondar para 1 casa decimal (2 no `accel_g`) para economizar bytes.

## Critérios de aceite

- [ ] No `mosquitto_sub`, aparece uma mensagem a cada 5 s (±0,5 s) (conferir no Wokwi, T6). Implementado com `TELEMETRY_INTERVAL_MS` e reagendamento dentro de `publishTelemetry`, sem rajada depois de um envio imediato.
- [x] O payload é aceito pelo schema `TelemetryMessage` da API (I2) — exemplo real em `api/tests/fixtures/telemetry_sample.json`, gerado com o próprio ArduinoJson. **Contrato (já registrado):** float inteiro chega sem casa decimal (`10.0` → `10`), então o schema do I2 aceita `int` onde o campo é `float`. Ver "Regras gerais" em [contrato-mqtt.md](../document/contrato-mqtt.md).
- [x] Payload < 300 bytes: **171 bytes** no exemplo salvo.
- [x] Uma mudança de nível gera uma telemetria extra na hora (`updateAlert` → `publishTelemetry`).
- [ ] `ts` > 0 depois da sincronização do NTP (conferir no Wokwi, T6). Implementado em `startNtp` + `currentEpochSeconds`, com piso em 2024-01-01.

## Tarefas

- [x] NTP
- [x] `publishTelemetry` com ArduinoJson
- [x] Envio imediato na mudança de nível
- [x] Copiar um payload real para `api/tests/fixtures/telemetry_sample.json`
- [x] Atualizar o status

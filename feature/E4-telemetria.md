# E4 — Telemetria via MQTT

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | iot |
| Depende de | E1 |
| Janela | 19/09 |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Enviar à API, periodicamente, o estado da máquina, para o painel ao vivo (W5) e o histórico (W11).

## Escopo

**Inclui**
- Publicação a cada **5 s** no tópico `telemetry`, no formato do [contrato](../docs/contrato-mqtt.md#telemetry-e4).
- Publicação **imediata** quando o nível de alerta muda (E2).
- Timestamp via NTP e contador `seq`.

**Não inclui**
- Buffer offline de telemetria. Se o MQTT cair, as leituras desse período se perdem, mas os **alertas locais continuam funcionando**.

## Implementação (`iot/src/main.ino`)

- `configTime(0, 0, "pool.ntp.org")` no `setup()`, depois do Wi-Fi. `currentEpoch()` devolve 0 se o ano for < 2024 (NTP ainda não sincronizado).
- `publishTelemetry()`: monta um `JsonDocument` com `device_id, ts, seq, roll_deg, pitch_deg, accel_g, temp_c, humidity_pct, tilt_limit_deg, alert_level` e publica sem retained.
- Valores `NaN` do DHT → `null` (`doc["temp_c"] = nullptr`).
- Arredondar para 1 casa decimal (2 no `accel_g`) para economizar bytes.

## Critérios de aceite

- [ ] No `mosquitto_sub`, aparece uma mensagem a cada 5 s (±0,5 s).
- [ ] O payload é aceito pelo schema `TelemetryMessage` da API (I2). Teste colando um exemplo real num teste da API.
- [ ] Payload < 300 bytes.
- [ ] Uma mudança de nível gera uma telemetria extra na hora.
- [ ] `ts` > 0 depois da sincronização do NTP.

## Tarefas

- [ ] NTP
- [ ] `publishTelemetry` com ArduinoJson
- [ ] Envio imediato na mudança de nível
- [ ] Copiar um payload real para `api/tests/fixtures/telemetry_sample.json`
- [ ] Atualizar o status

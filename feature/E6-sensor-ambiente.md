# E6 — Temperatura/umidade e regra dos 30 local

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | iot |
| Depende de | E3 (`wind_max_kmh`), E4 |
| Janela | 22/09 |
| Responsável | Dev |
| Status | 🟦 Em revisão (implementado; falta o GIF da simulação) |

## Objetivo

Medir o microclima **na própria máquina** e combinar com o vento previsto para indicar a condição de
incêndio (regra dos 30). Uma colheitadeira quente em palha seca é uma fonte clássica de fogo.

## Escopo

**Inclui**
- Leitura do DHT22 a cada **2 s** (o mínimo do sensor), guardando o último valor válido.
- `fire_conditions` de 0 a 3: `temp_c > 30`, `humidity_pct < 30`, `wind_max_kmh > 30` (o vento vem do `config`, porque não há anemômetro).
- Campo `fire_conditions` na telemetria (campo opcional do contrato).

**Não inclui**
- Mudar os LEDs por causa do incêndio: os LEDs são da inclinação. O incêndio aparece no painel (W5) e no display (E7).

## Implementação (`iot/src/main.ino`)

- `const unsigned long DHT_INTERVAL_MS = 2000;` e `readEnv()` com temporizador próprio no `loop()`, separado do IMU (no código a função chama-se `readEnv`, não `readEnvironment`).
- NaN → mantém o último valor válido por até 10 s (`ENV_HOLD_MS`). Depois disso, publica `null`. A tolerância é a função pura `updateEnvHold(hold, leitura, nowMs)`, aplicada a cada canal (temperatura e umidade separados).
- `int fireConditions(tempC, humidityPct, windMaxKmh)`: função pura. Limiares **estritos** (exatamente 30 não conta) e NaN não conta como condição ativa.
- `fire_conditions` é omitido da telemetria quando nenhum dos três valores é conhecido: `0` afirmaria “nenhuma condição ativa”, que é diferente de “não sei”. Registrado na tabela do contrato MQTT.
- Log `[env] 32.1°C 25% vento prev. 35 km/h → 3/3 condicoes` (valor ausente sai como `--`).

## Critérios de aceite

- [x] No Wokwi, temperatura 35 e umidade 20 + `wind_max_kmh: 35` no config → `fire_conditions = 3`
  (repetir na simulação, T6). Verificado no host com a própria ArduinoJson: o payload sai com
  `"fire_conditions":3`.
- [x] Temperatura 35 e umidade 50 → 2 (com o mesmo vento). Mesma verificação.
- [ ] A leitura do DHT não atrasa o IMU (o IMU continua a 10 Hz) — conferir no Wokwi, T6. O
  `loop()` tem temporizadores independentes (`lastDhtReadMs` a 2 s, `lastImuReadMs` a 100 ms) e
  nenhum `delay()`; o custo do `readEnv()` é a leitura do sensor, fora do caminho do IMU.
- [x] Payload da telemetria continua abaixo do teto de 300 bytes: **191 bytes** com o campo novo
  (eram 171). Cópia real em `api/tests/fixtures/telemetry_sample.json`.

## Tarefas

- [x] Temporização separada do DHT (já existia do E1; ganhou a tolerância a falhas do `updateEnvHold`)
- [x] `fireConditions`
- [x] Campo na telemetria (a API aceita como opcional; fixture atualizada)
- [x] Atualizar o status

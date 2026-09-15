# E6 — Temperatura/umidade e regra dos 30 local

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | iot |
| Depende de | E3 (`wind_max_kmh`), E4 |
| Janela | 22/09 |
| Responsável | Dev |
| Status | 🟨 leitura básica do DHT22 pronta |

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

- `const unsigned long DHT_INTERVAL_MS = 2000;` e `readEnvironment()` separado do IMU.
- NaN → mantém o último valor válido por até 10 s. Depois disso, publica `null`.
- `int fireConditions()`: função pura.
- Log `[env] 32.1°C 25% vento prev. 35 km/h → 3/3 condições`.

## Critérios de aceite

- [ ] No Wokwi, temperatura 35 e umidade 20 + `wind_max_kmh: 35` no config → `fire_conditions = 3`.
- [ ] Temperatura 35 e umidade 50 → 2 (com o mesmo vento).
- [ ] A leitura do DHT não atrasa o IMU (o IMU continua a 10 Hz).

## Tarefas

- [ ] Temporização separada do DHT
- [ ] `fireConditions`
- [ ] Campo na telemetria (a API aceita como opcional)
- [ ] Atualizar o status

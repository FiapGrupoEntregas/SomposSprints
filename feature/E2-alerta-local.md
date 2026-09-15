# E2 — Alerta local (LEDs + buzzer)

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | iot |
| Depende de | E1 |
| Janela | 19/09 |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Avisar o operador **na hora e sem depender da internet** quando a máquina passar do limite seguro do dia.

## Escopo

**Inclui**
- 3 níveis com LEDs: exatamente **um** LED aceso por vez.
- Buzzer intermitente no 🔴.
- Histerese, para o LED não ficar piscando perto do limite.
- Evento `tilt_alert` ao **entrar** no 🔴.

**Não inclui**
- O estado de capotamento (E5), que tem prioridade sobre estes níveis.

## Regras e lógica

Com `L = tiltLimitDeg` e `w = warnRatio` (padrão 0,8), e histerese `H = 1,0°`:

| Nível | Entra quando | Sai (para baixo) quando |
|---|---|---|
| 🟢 green | `tilt < w·L` | — |
| 🟡 yellow | `tilt ≥ w·L` | `tilt < w·L − H` |
| 🔴 red | `tilt ≥ L` | `tilt < L − H` |

Buzzer: 🟢 desligado · 🟡 um bipe curto ao entrar · 🔴 intermitente a 2 Hz (`tone(PIN_BUZZER, 2000)` / `noTone`), sem bloquear o loop.

## Implementação (`iot/src/main.ino`)

- `enum AlertLevel { LEVEL_GREEN, LEVEL_YELLOW, LEVEL_RED, LEVEL_ROLLOVER };`
- `AlertLevel computeLevel(float tiltDeg, AlertLevel current)`: função pura, aplicando a histerese.
- `applyOutputs(AlertLevel level)`: LEDs e buzzer, temporizados com `millis()`.
- Mudança de nível → log `[alert] green → red (tilt 10.4° / limite 10.0°)` e **telemetria imediata** (E4).
- Entrada no 🔴 → evento `tilt_alert` (enviado 3 vezes, conforme o contrato).

## Critérios de aceite

Com `L = 10°` e `w = 0,8`:
- [ ] 7,9° → 🟢 · 8,0° → 🟡 · 10,0° → 🔴
- [ ] Voltando do 🔴: 9,5° continua 🔴 · 8,9° → 🟡
- [ ] Voltando do 🟡: 7,5° continua 🟡 · 6,9° → 🟢
- [ ] O buzzer toca intermitente só no 🔴, e o loop continua respondendo (MQTT ativo).
- [ ] Um `tilt_alert` por entrada no 🔴 (e não um a cada leitura).

## Tarefas

- [ ] `computeLevel` com histerese
- [ ] `applyOutputs` sem bloquear
- [ ] Evento `tilt_alert`
- [ ] GIF da simulação no PR + atualizar o status

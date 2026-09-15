# E5 — Detecção de capotamento

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | iot (+ exibição no W5) |
| Depende de | E1, E4 |
| Janela | 21/09 |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Se o pior acontecer, avisar **na hora** e com o **contexto dos 30 s anteriores**. Isso dá socorro
mais rápido ao operador e dados objetivos para o sinistro (ligação com o Passaporte Digital).

## Escopo

**Inclui**
- Detecção por inclinação sustentada **ou** por impacto.
- Buffer circular com 30 s de contexto (1 amostra/s).
- Evento `rollover` com o `context`, enviado 3 vezes com o mesmo `event_id`.
- Estado travado (latched) até a situação normalizar.

**Não inclui**
- Chamada de emergência automática (roadmap).

## Regras e lógica

- **Capotamento** quando `tiltDeg ≥ 45°` por **≥ 2 s** seguidos, **ou** `accel_g ≥ 2,5` (impacto).
- Picos com menos de 2 s acima de 45° **não** disparam.
- No estado `LEVEL_ROLLOVER`: LED vermelho piscando a 5 Hz e buzzer **contínuo**. Ele tem prioridade sobre o E2.
- O estado sai quando `tiltDeg < 20°` por **10 s** seguidos → volta ao cálculo normal do E2.
- `alert_level` na telemetria = `"rollover"` enquanto estiver no estado.

## Implementação (`iot/src/main.ino`)

- Buffer circular `ContextSample { int32_t tS; float rollDeg, pitchDeg, accelG; } ctx[30]`, alimentado a 1 Hz.
- `checkRollover(const ImuReading&)`: controla o tempo acima de 45° e o impacto.
- `publishEvent("rollover", withContext=true)`: monta `context.fields` e `context.rows` (o `t_s` é relativo ao evento) e publica 3 vezes com 500 ms de intervalo, **sem bloquear** (use uma pequena fila de reenvio controlada por `millis()`).
- Buffer do MQTT já é de 2048 bytes (`setBufferSize`).

## Critérios de aceite

- [ ] Slider em 60° por 3 s → evento `rollover` no broker em ≤ 3 s, com 30 linhas de contexto.
- [ ] Slider em 50° por 1 s e de volta → **não** dispara.
- [ ] Aceleração com módulo ≥ 2,5 g (ex.: X = 2,0, Z = 1,5) → dispara.
- [ ] A API grava **um** evento (deduplicação do I2/I3) e o painel (W5) mostra o banner.
- [ ] Voltando a < 20° por 10 s, o estado normal retorna.

## Tarefas

- [ ] Buffer circular de contexto
- [ ] `checkRollover` + estado travado
- [ ] Envio triplo sem bloquear
- [ ] Teste ponta a ponta com o painel
- [ ] GIF no PR + atualizar o status

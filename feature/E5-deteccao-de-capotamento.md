# E5 — Detecção de capotamento

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | iot (+ exibição no W5) |
| Depende de | E1, E4 |
| Janela | 21/09 |
| Responsável | Dev |
| Status | 🟦 Em revisão (estado `rollover` observado no Serial após cerca de 3 s; evento/contexto MQTT e saídas locais ainda não conferidos) |

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

- Buffer circular `ContextSample { unsigned long capturedMs; float rollDeg, pitchDeg, accelG; } contextBuffer[30]`, alimentado a 1 Hz. Guarda o `millis()` da coleta, e não o `t_s` do contrato, porque `t_s` é relativo ao evento: só dá para calculá-lo na hora de montar o payload (e a subtração de `unsigned long` continua correta no wrap do `millis()`).
- `evaluateRollover(estado, tiltDeg, accelG, nowMs)`: **função pura** que controla o tempo acima de 45° e o impacto, e devolve o estado seguinte com as transições. O I/O fica em `updateRollover(const ImuReading&)`, que aplica o nível, as saídas locais e o evento.
- `queueRolloverEvent()` + `fillEventContext()`: montam `context.fields` e `context.rows` (com `t_s` relativo ao evento, de −29 a 0) e entregam o payload à fila de eventos do E2/E3, que publica 3 vezes com 500 ms de intervalo, **sem bloquear**, temporizada por `millis()`.
- Buffer do MQTT já é de 2048 bytes (`setBufferSize`). O payload com 30 linhas mede 725 bytes no caso da demo e 1021 no pior caso, e `queueRolloverEvent` loga um aviso se algum dia chegar perto do teto.
- O acelerômetro precisa sair do padrão de ±2 g da biblioteca (`mpu.setAccelerometerRange(MPU6050_RANGE_8_G)`): em ±2 g a leitura satura e o impacto de 2,5 g nunca apareceria.
- **Máquina que liga já capotada:** o `setup()` **não** assume o estado travado — a regra é temporal e não há histórico no boot. O `loop()` começa a contar os 2 s na primeira leitura (ou trava na hora, se o impacto estiver presente), então o `rollover` sai ~2 s depois do boot, com as linhas de contexto que já existirem, e o `tilt_alert` do boot em 🔴 acima de 45° fica adiado até esse veredito.

## Critérios de aceite

- [ ] Slider em 60° por 3 s → evento `rollover` no broker em ≤ 3 s, com 30 linhas de contexto
  (conferir no Wokwi, T6). Implementado em `evaluateRollover` (trava 2 s depois de cruzar 45°) e
  `fillEventContext` (30 linhas com o buffer cheio, `t_s` de −29 a 0), com a publicação imediata
  pela fila já existente.
- [x] Slider em 50° por 1 s e de volta → **não** dispara. `evaluateRollover` zera o cronômetro
  assim que a inclinação cai abaixo de 45°; coberto pelos casos "pico de 1 s" e "dois picos de
  1,5 s não somam" da verificação da lógica pura.
- [x] Aceleração com módulo ≥ 2,5 g (ex.: X = 2,0, Z = 1,5) → dispara. Detecção na amostra **crua**
  (a média móvel de 0,5 s diluiria o impacto) e acelerômetro reconfigurado para **±8 g**: no padrão
  ±2 g da biblioteca a leitura satura em 2 g e 2,5 g nunca apareceria.
- [ ] A API grava **um** evento (deduplicação do I2/I3) e o painel (W5) mostra o banner — depende do
  I2/I3 e do W5. No dispositivo, é 1 `rollover` por entrada no estado (`computeLevel` mantém
  `LEVEL_ROLLOVER` travado) e nenhum `tilt_alert` em paralelo pela mesma inclinação (o alerta do E2
  fica adiado acima de 45° e é descartado se o estado travar).
- [x] Voltando a < 20° por 10 s, o estado normal retorna (`evaluateRollover`, ramo de saída; o nível
  é recalculado a partir do 🔴, a hipótese conservadora).
- [x] Payload com 30 linhas cabe no buffer MQTT: **725 bytes** no caso da demo e **1021 bytes** no
  pior caso, contra `setBufferSize(2048)`.

## Tarefas

- [x] Buffer circular de contexto (`ContextSample contextBuffer[30]`, 1 Hz, `pushContextSample`)
- [x] `checkRollover` + estado travado (`evaluateRollover`, função pura, + `updateRollover`)
- [x] Envio triplo sem bloquear (reaproveita a fila de eventos do E2/E3)
- [ ] Teste ponta a ponta com o painel (depende de I2/I3 e W5)
- [ ] GIF no PR + atualizar o status

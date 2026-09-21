# E8 — Botão de ocorrência

| Campo | Valor |
|---|---|
| Prioridade | P2 |
| Camadas | iot |
| Depende de | E5 (buffer de contexto) |
| Janela | só se sobrar tempo |
| Responsável | Dev |
| Status | 🟦 Em revisão (implementado; falta o GIF da simulação) |

## Objetivo

Permitir que o operador **registre uma ocorrência** (batida, atolamento, quase-acidente) com um
toque. O registro vai com o contexto dos últimos 30 s. É o dado objetivo para o sinistro.

## Escopo

**Inclui**
- Botão no pino **33** (`INPUT_PULLUP`, ligado ao GND), com debounce de 50 ms.
- Um toque → evento `incident_report` com o `context` (reaproveitando o buffer do E5).
- Confirmação ao operador: 3 bipes curtos.

## Implementação

- `iot/diagram.json`: `wokwi-pushbutton` (`btn1:1.l` → `esp:33`, `btn1:2.l` → `esp:GND.1`).
- `iot/src/main.ino`: `PIN_BUTTON = 33`, `debounceButton()` (função pura, 50 ms) chamada por
  `updateButton()` a cada volta do `loop()`, e `queueIncidentReport()` — que usa o
  `queueContextEvent()` compartilhado com o `rollover` do E5, com a mesma janela de 30 s.
- Confirmação: `startConfirmBeeps()` + `pumpConfirmBeep()`, 3 bipes de 80 ms com 120 ms de
  intervalo, temporizados por `millis()`. Enquanto tocam, eles mandam no buzzer; ao terminar,
  `buzzerResyncPending` faz o `applyOutputs()` devolver o som do nível atual.
- **Botão preso no boot não gera evento:** o estado inicial é lido do pino no `setup()`, então só um
  toque novo, depois de soltar, conta (e no boot não haveria contexto para anexar).
- Atualizar a tabela de pinos do `iot/README.md`.

## Critérios de aceite

- [ ] Um toque → 1 evento no broker (3 envios, 1 gravação na API) — conferir no Wokwi (T6) e
  depende da deduplicação do I2/I3. No dispositivo, `debounceButton` só confirma a borda de descida.
- [x] Segurar o botão não gera vários eventos — verificado no host: 5 s segurado = 1 evento, ruído
  de contato alternando a cada 5 ms = nenhum evento até assentar, pulso de 30 ms ignorado, e o
  debounce continua correto no wrap do `millis()`.
- [ ] O evento aparece na lista do painel (W5) — depende do W5.
- [x] Payload com contexto cabe no buffer MQTT: **738 bytes** no caso típico, **1028** no pior caso.

## Tarefas

- [x] Diagrama + pino (33, `INPUT_PULLUP`; tabela do `iot/README.md` atualizada)
- [x] Debounce + evento (`debounceButton` pura + `queueIncidentReport`)
- [x] Confirmação de 3 bipes sem bloquear
- [x] Atualizar o status

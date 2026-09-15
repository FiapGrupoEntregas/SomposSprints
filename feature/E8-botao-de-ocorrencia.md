# E8 — Botão de ocorrência

| Campo | Valor |
|---|---|
| Prioridade | P2 |
| Camadas | iot |
| Depende de | E5 (buffer de contexto) |
| Janela | só se sobrar tempo |
| Responsável | Dev |
| Status | ⬜ A fazer |

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
- `iot/src/main.ino`: `PIN_BUTTON = 33`, leitura com debounce no `loop()` e `publishEvent("incident_report", true)`.
- Atualizar a tabela de pinos do `iot/README.md`.

## Critérios de aceite

- [ ] Um toque → 1 evento no broker (3 envios, 1 gravação na API).
- [ ] Segurar o botão não gera vários eventos.
- [ ] O evento aparece na lista do painel (W5).

## Tarefas

- [ ] Diagrama + pino
- [ ] Debounce + evento
- [ ] Atualizar o status

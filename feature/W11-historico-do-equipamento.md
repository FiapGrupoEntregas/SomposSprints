# W11 — Histórico do equipamento (prévia do Passaporte Digital)

| Campo | Valor |
|---|---|
| Prioridade | P2 |
| Camadas | api, front-web |
| Depende de | I3 |
| Janela | só se sobrar tempo |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Mostrar a **semente do Passaporte Digital**: cada alerta seguido ou ignorado vira histórico do
equipamento, útil para renovação, sinistro e revenda. Liga a demo ao roadmap do pitch.

## Escopo

**Inclui**
- Resumo dos últimos N dias: tempo operando, inclinação máxima, % do tempo acima do limite, número de alertas, capotamentos e ocorrências.
- Linha do tempo dos eventos.

**Não inclui**
- Score comportamental, portabilidade na revenda ou assinatura digital (roadmap).

## Implementação

### API (`api/`)
- `app/services/history.py`: `device_history(telemetry, events, days) -> DeviceHistory`. Tempo operando = número de leituras × 5 s.
- `GET /api/v1/devices/{device_id}/history?days=7`

### Front-web (`front-web/`)
- Página **Equipamento ao vivo**, seção **"Passaporte (prévia)"**: KPIs + linha do tempo + o texto "Na versão completa, este histórico acompanha a máquina na renovação e na revenda".

## Critérios de aceite

- [ ] Os números batem com os dados gravados (teste com dados sintéticos).
- [ ] Sem dados → mensagem amigável.

## Tarefas

- [ ] Service + testes
- [ ] Rota
- [ ] Seção no front
- [ ] Atualizar os READMEs e o status

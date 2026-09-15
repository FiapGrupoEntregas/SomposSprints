# W5 — Painel do equipamento ao vivo

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api, front-web |
| Depende de | I2, I3, E4 |
| Janela | 20/09 |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Mostrar **em tempo real** o que acontece na máquina: inclinação, limite, nível de alerta, status e
eventos. É o que prova, na demo, que o dispositivo e a nuvem conversam.

## História de usuário

> Como **gestor da fazenda ou da seguradora**, quero **ver ao vivo se alguma máquina está em risco**, para **agir antes que um acidente aconteça**.

## Escopo

**Inclui**
- Status online/offline (pelo LWT + telemetria recente).
- Últimas leituras, gráfico dos últimos 10 min e lista de eventos.
- Um banner de emergência para `rollover`, com o gráfico do contexto de 30 s (quando o E5 existir).

**Não inclui**
- Várias máquinas ao mesmo tempo no mesmo painel (o seletor aceita, mas a demo usa uma).
- Mapa com a posição da máquina (sem GPS).

## Implementação

### API (`api/`)
- `GET /api/v1/devices/{device_id}/status` → `{state, last_seen_at, seconds_since_last_telemetry}`. O equipamento é considerado `offline` se o LWT disser offline **ou** se ficar mais de 20 s sem telemetria.
- `GET /api/v1/devices/{device_id}/telemetry/latest` → a última leitura, ou 404 "Sem telemetria ainda".
- `GET /api/v1/devices/{device_id}/telemetry?minutes=10` → série em ordem cronológica (no máximo 300 pontos, com amostragem se passar disso).
- `GET /api/v1/devices/{device_id}/events?limit=20` → os mais recentes primeiro, com o `context` quando houver.

### Front-web (`front-web/`)
- Página **Equipamento ao vivo**:
  - `@st.fragment(run_every="2s")` envolvendo **só** o bloco ao vivo (o resto da página não recarrega).
  - Faixa de status: 🟢 Online / ⚫ Offline há X s.
  - `st.metric`: roll, pitch, limite do dia e temperatura/umidade.
  - **Banner de nível**, grande e colorido: SEGURO / ATENÇÃO / PERIGO / 🚨 CAPOTAMENTO.
  - Gráfico de linha com roll e pitch (10 min) e uma linha horizontal no limite.
  - Lista de eventos com ícone, horário e descrição.
  - Evento `rollover` recente (< 5 min) → `st.error` fixo no topo + gráfico dos 30 s de contexto.

## Critérios de aceite

- [ ] Mexer no slider do MPU6050 → o nível muda no painel em **≤ 3 s** (o evento é imediato) e o gráfico em **≤ 7 s** (telemetria a cada 5 s).
- [ ] Parar a simulação → o painel mostra Offline em **≤ 30 s**.
- [ ] Com a API sem dados do equipamento, a página mostra "Aguardando o equipamento conectar…" em vez de um erro.
- [ ] A atualização automática não pisca nem reseta os seletores da página.

## Testes

- `tests/test_devices_telemetry_routes.py`: com o repositório populado em memória.
- Front: smoke test da página com a API fora do ar.

## Tarefas

- [ ] Rotas de status, telemetria e eventos + testes
- [ ] Fragmento ao vivo no front
- [ ] Banner de capotamento (depois do E5)
- [ ] Teste ponta a ponta com o Wokwi + atualizar os READMEs e o status

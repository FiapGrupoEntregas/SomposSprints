# W7 — Riscos extras: raio, vento e incêndio

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | api, front-web |
| Depende de | W3 |
| Janela | 22/09 |
| Responsável | Dev |
| Status | ✅ API + filtro e ícones no front |

## Objetivo

Completar o motor com os perigos em que **o relevo muda o risco climático**: topos de morro atraem
raios, cristas expostas pegam mais vento e o fogo sobe a encosta mais rápido.

## Escopo

**Inclui**
- `lightning`, `wind` e `fire`: [regras-de-risco §5.3–5.5](../document/regras-de-risco.md#53-raio-lightning-w7-p1).
- Cenários extras: `storm` e `heatwave` ([§10](../document/regras-de-risco.md#10-cenários-simulados-demo)).
- Filtro por tipo de perigo no front.

**Não inclui**
- Dados de descargas atmosféricas em tempo real.

## Implementação

### API (`api/`)
- `app/services/risk.py`: `lightning_hazard`, `wind_hazard` e `fire_hazard`, adicionadas a `HAZARDS`. **Nada muda na rota nem no schema** (os motivos já são uma lista).
- `app/services/scenarios.py`: `storm` e `heatwave`.

### Front-web (`front-web/`)
- `st.multiselect` "Perigos" (padrão: todos). O mapa recalcula a cor de cada célula como o pior nível **entre os perigos selecionados**. Isso é filtro de exibição, não regra nova.
- Ícones nos motivos: 🚜 capotamento · 🟫 atolamento · ⚡ raio · 💨 vento · 🔥 incêndio.

## Critérios de aceite

- [x] Tempestade no dia → células `exposed` 🔴 e as demais pelo menos 🟡.
- [x] Rajada de 50 km/h → só as `exposed` ficam 🟡. Rajada de 60 km/h → `exposed` 🔴 e as demais 🟡.
- [x] Regra dos 30 com as 3 condições → 🔴 em tudo. Com 2 condições → 🟡, e 🔴 onde a inclinação é ≥ 15°.
- [x] Os cenários `storm` e `heatwave` acionam os perigos esperados.

### Convenção fixada na W7: o motivo da tempestade não tem número

O critério da W3 dizia "cada célula 🟡 ou 🔴 tem pelo menos um motivo **com números**". O raio por
`weather_code` (§5.3) é a única exceção: ou o dia tem código de tempestade, ou não tem — não há
número a citar. O motivo sai como *"Tempestade prevista em topo exposto: risco de raio."* e o
teste da rota abre essa exceção **explicitamente**, em vez de afrouxar a regra para todos.

## Testes

- `tests/test_risk_rules.py`: bordas de cada limiar novo (CAPE 1999/2000, rajada 44,9/45/59,9/60, temperatura 30/30,1, UR 30/29,9, vento 30/30,1).

## Tarefas

- [x] 3 funções de perigo + testes (bordas de cada limiar novo)
- [x] 2 cenários + testes
- [x] Filtro e ícones no front
- [x] Atualizar os READMEs, `document/arquitetura.md` e o status

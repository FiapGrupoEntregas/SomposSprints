# W7 — Riscos extras: raio, vento e incêndio

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | api, front-web |
| Depende de | W3 |
| Janela | 22/09 |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Completar o motor com os perigos em que **o relevo muda o risco climático**: topos de morro atraem
raios, cristas expostas pegam mais vento e o fogo sobe a encosta mais rápido.

## Escopo

**Inclui**
- `lightning`, `wind` e `fire`: [regras-de-risco §5.3–5.5](../docs/regras-de-risco.md#53-raio-lightning-w7-p1).
- Cenários extras: `storm` e `heatwave` ([§10](../docs/regras-de-risco.md#10-cenários-simulados-demo)).
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

- [ ] Tempestade no dia → células `exposed` 🔴 e as demais pelo menos 🟡.
- [ ] Rajada de 50 km/h → só as `exposed` ficam 🟡. Rajada de 60 km/h → `exposed` 🔴 e as demais 🟡.
- [ ] Regra dos 30 com as 3 condições → 🔴 em tudo. Com 2 condições → 🟡, e 🔴 onde a inclinação é ≥ 15°.
- [ ] Os cenários `storm` e `heatwave` acionam os perigos esperados.

## Testes

- `tests/test_risk_rules.py`: bordas de cada limiar novo (CAPE 1999/2000, rajada 44,9/45/59,9/60, temperatura 30/30,1, UR 30/29,9, vento 30/30,1).

## Tarefas

- [ ] 3 funções de perigo + testes
- [ ] 2 cenários + testes
- [ ] Filtro e ícones no front
- [ ] Atualizar os READMEs e o status

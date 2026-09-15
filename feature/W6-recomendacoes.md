# W6 — Recomendações e janela segura de operação

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | api, front-web |
| Depende de | W3 (e W7, se existir) |
| Janela | 22/09 |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Traduzir o mapa em **ação**: "o que fazer hoje e amanhã", em frases simples, com as janelas de horário seguras.

## História de usuário

> Como **operador**, quero **uma orientação curta e direta**, para **não precisar interpretar mapas**.

## Escopo

**Inclui**
- Janelas seguras hora a hora para **hoje e amanhã**: [regras-de-risco §7](../docs/regras-de-risco.md#7-janela-segura-de-operação-w6).
- Frases geradas por **templates** (sem IA), a partir dos perigos do dia:

| Situação | Frase |
|---|---|
| Células 🔴 de `rollover` | "Evite operar máquinas na encosta {direção} (inclinação acima de {L}°, solo {estado})." |
| Células 🔴 de `bogging` | "Risco de atolamento nas baixadas: evite tráfego pesado." |
| `lightning` | "Previsão de tempestade: suspenda as atividades em áreas abertas e topos de morro." |
| `wind` 🔴 | "Rajadas de até {g} km/h: evite pulverização e máquinas altas nas áreas expostas." |
| `fire` | "Condição de incêndio (regra dos 30): redobre a atenção com a colheitadeira e deixe o aceiro pronto." |
| Tudo 🟢 | "Sem restrições de relevo e clima para hoje." |

- A direção da encosta é a orientação predominante (moda do `aspect_label`) das células 🔴.

**Não inclui**
- Texto gerado por IA (possível extra no roadmap).

## Implementação

### API (`api/`)
- `app/services/recommendations.py`: `safe_windows(hourly, day) -> list[TimeWindow]` e `build_messages(day_risk, terrain) -> list[str]`.
- `GET /api/v1/farms/{farm_id}/recommendations?days=2&scenario=` → `[{date, windows: [{start, end}], messages: [...]}]`.

### Front-web (`front-web/`)
- Página **Mapa de risco**: card **"O que fazer"** abaixo do mapa, com as abas Hoje e Amanhã, as frases em lista e as janelas como chips (`07h–12h`).

## Critérios de aceite

- [ ] Horas com chuva ≥ 0,5 mm, rajada ≥ 45 km/h ou tempestade nunca aparecem numa janela.
- [ ] Janelas com menos de 2 h são descartadas. Janelas só entre 06h e 18h.
- [ ] Um dia sem nenhuma célula 🟡 ou 🔴 → só a frase "Sem restrições…".
- [ ] A direção citada bate com a maioria das células 🔴.

## Testes

- `tests/test_recommendations.py`: janelas (bordas de 0,5 mm e 45 km/h, duração mínima), cada template e a direção predominante.

## Tarefas

- [ ] `safe_windows` + testes
- [ ] Templates + testes
- [ ] Rota + card no front
- [ ] Atualizar os READMEs e o status

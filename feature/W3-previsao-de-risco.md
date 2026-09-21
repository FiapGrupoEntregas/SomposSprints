# W3 — Previsão de risco em 7 dias (capotamento + atolamento)

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api, front-web |
| Depende de | I1, W2 |
| Janela | 17/09 |
| Responsável | Dev |
| Status | ✅ API + aba Previsão de risco no front |

## Objetivo

O **coração do produto**: cruzar relevo × clima célula a célula e dia a dia, e mostrar que **a mesma
encosta muda de risco conforme a chuva**.

## História de usuário

> Como **produtor**, quero **ver para cada dia da semana quais áreas estão perigosas para as máquinas e por quê**, para **planejar a operação sem acidentes**.

## Escopo

**Inclui**
- Estado do solo, limite do dia, e os perigos `rollover` e `bogging`: [regras-de-risco §3, §4, §5.1, §5.2, §6](../document/regras-de-risco.md#3-estado-do-solo).
- Motor extensível: cada perigo é uma função registrada numa lista. O W7 só adiciona funções.
- **Cenários simulados** ([regras-de-risco §10](../document/regras-de-risco.md#10-cenários-simulados-demo)): setembro é época seca em MG e a previsão real pode não ter chuva nenhuma. O cenário `heavy_rain` garante a demo, **sempre sinalizado na tela**.

**Não inclui**
- Raio, vento e incêndio (W7), recomendações em texto (W6).

## Implementação

### API (`api/`)
- `app/services/risk.py`:
  - `soil_state(rain_72h_mm) -> SoilState`
  - `tilt_limit(l_ref_deg, soil) -> float` (com `floor_0.5`)
  - `rollover_hazard(cell, day, limit) -> HazardResult | None`
  - `bogging_hazard(cell, day, soil) -> HazardResult | None`
  - `HAZARDS: list[Callable]`, que o W7 estende
  - `assess_farm(terrain, daily, l_ref) -> RiskForecast`
- `app/services/scenarios.py`: `apply_scenario(hourly, scenario) -> HourlyWeather` (antes da agregação).
- `app/schemas/risk.py`:
  - `HazardResult(hazard, level, message)`
  - `CellRisk(row, col, level, reasons: list[HazardResult])`
  - `DayRisk(date, rain_mm, rain_72h_mm, gust_max_kmh, temp_max_c, soil_state, tilt_limit_deg, worst_level, pct_levels: {green, yellow, red}, top_reasons, cells)`
  - `RiskForecast(farm_id, generated_at, scenario: str | None, days: list[DayRisk])`
- `GET /api/v1/farms/{farm_id}/risk?days=7&scenario=heavy_rain`. `days` vai de 1 a 7 (padrão 7), e só os dias de hoje em diante são retornados. `scenario` desconhecido → 422.
- Mensagens em português com números: *"Inclinação de 13° acima do limite de 10° (solo encharcado: 42 mm em 72 h)"*.

### Front-web (`front-web/`)
- Página **Mapa de risco**, aba **Risco**:
  - **Faixa dos 7 dias** (cards ou `st.segmented_control`) com o pior nível, a chuva do dia e o estado do solo. Clicar escolhe o dia.
  - **Mapa** das células coloridas por nível (🟢 `#2E7D32`, 🟡 `#F9A825`, 🔴 `#C62828`) no dia escolhido, com os motivos no tooltip.
  - **Gráfico** de chuva diária + chuva em 72 h, com as linhas de 10 e 30 mm.
  - Painel lateral: limite do dia, % da área em cada nível e os principais motivos.
  - Toggle **"Cenário: chuva forte (simulado)"** → faixa amarela bem visível: "⚠️ Cenário simulado — não é a previsão real".

## Critérios de aceite

- [x] Num dia com `rain_72h ≥ 30 mm` em Carmo de Minas, as células com inclinação ≥ 10° ficam 🔴.
- [x] O pior nível de cada card bate com o pior nível das células daquele dia.
- [x] Cada célula 🟡 ou 🔴 tem pelo menos um motivo com números.
- [x] Com `scenario=heavy_rain`, o dia +2 aparece como solo encharcado e o campo `scenario` vem preenchido (**API ✅**). O front liga o cenário por um toggle e mostra a faixa "⚠️ Cenário simulado — não é a previsão real" sempre que a resposta traz `scenario` (**front ✅**).
- [x] Resposta em < 3 s sem cache (0,9 s medido em Carmo de Minas, ~101 KB).

## Testes

- `tests/test_risk_rules.py`, nas bordas de cada limiar:
  - solo: 9,9 → `dry` · 10 → `moist` · 29,9 → `moist` · 30 → `saturated`
  - limite (L_ref 15): `dry` 15,0 · `moist` 12,5 · `saturated` 10,0
  - rollover (L = 10): 6,9° 🟢 · 7,0° 🟡 · 9,9° 🟡 · 10,0° 🔴
  - bogging: `lowland` + `saturated` 🔴 · `lowland` + `moist` 🟡 · `slope` + `saturated` 🟢 · chuva ≥ 50 mm 🟡
  - nível final = o pior, com os motivos acumulados
- `tests/test_scenarios.py`: `heavy_rain` soma 45 mm no dia +2.
- `tests/test_risk_route.py`: rota com mocks, `days` inválido e cenário inválido.

## Riscos e plano B

| Risco | Plano B |
|---|---|
| Semana seca na demo, tudo 🟢 | Cenário `heavy_rain` + replay (W9) de um dia chuvoso real |
| 700 células × dias deixando a resposta pesada | Aceitável (~100 KB). Se precisar, `?day=` devolve só um dia com células |

## Tarefas

- [x] Regras puras + testes de borda
- [x] Cenários + testes
- [x] `assess_farm` + rota
- [x] Aba Risco no front (faixa, mapa, gráfico, aviso de cenário)
- [x] Atualizar `document/arquitetura.md`, os READMEs e o status

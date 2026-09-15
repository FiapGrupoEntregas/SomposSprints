# W9 — Replay de acidentes reais

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | api, front-web |
| Depende de | W3, I1 (histórico), T2 (casos reais) |
| Janela | 22/09 |
| Responsável | Dev (casos: time) |
| Status | ⬜ A fazer |

## Objetivo

**Validar** o modelo com o passado: rodar o motor de risco na data e no local de acidentes reais
noticiados e responder, com honestidade, **"o sistema teria alertado?"**.

## História de usuário

> Como **avaliador da banca**, quero **ver o sistema aplicado a acidentes que realmente aconteceram**, para **acreditar que ele funciona**.

## Escopo

**Inclui**
- Casos curados pelo time (T2) num JSON versionado.
- Replay de um caso cadastrado **ou** de um ponto e data informados à mão.
- A mesma lógica do W3 com dados históricos: [regras-de-risco §9](../docs/regras-de-risco.md#9-replay-w9).
- Resultado honesto: se não teria alertado, mostramos isso e explicamos o porquê.

**Não inclui**
- Estatística de acerto em larga escala (roadmap, com a base de sinistros da Sompo).

## Implementação

### API (`api/`)
- `app/data/replay_cases.json`:
  ```json
  [
    {
      "id": "capotamento-exemplo-2024",
      "title": "Trator capota em lavoura de café",
      "date": "2024-01-15",
      "location": { "lat": -21.0, "lon": -45.0 },
      "location_precision": "municipio",
      "municipality": "…", "state": "MG",
      "machine": "trator",
      "description": "Resumo de 1–2 frases da notícia.",
      "source_url": "https://…"
    }
  ]
  ```
  `location_precision` vale `exato`, `aproximado` ou `municipio`, e **é exibido na tela**.
- `app/services/replay.py`: `run_replay(lat, lon, date, l_ref=15) -> ReplayResult`. Monta uma bbox de ±0,005° centrada no ponto, calcula o relevo (W2), busca o histórico de d−2 a d (I1) e roda o motor (W3/W7) só para o dia d.
- `ReplayResult(date, point_cell: CellRisk, day: DayRisk, would_alert: bool, weather_summary)`, com `would_alert = point_cell.level != green`.
- `GET /api/v1/replay/cases` e `POST /api/v1/replay` com `{case_id}` **ou** `{lat, lon, date}`. Data futura → 422.

### Front-web (`front-web/`)
- Página **Replay de acidentes**:
  - seletor de caso (título + data) ou uma aba "Ponto e data manuais";
  - resumo do caso com o link da fonte e a precisão da localização;
  - mapa de risco do dia com um marcador no ponto do acidente;
  - veredito grande: "✅ O sistema teria alertado — 🔴 capotamento: 14° com limite de 10° (38 mm em 72 h)" ou "❌ Não teria alertado — motivo: …";
  - resumo do clima do dia (chuva, 72 h, rajada).

## Critérios de aceite

- [ ] Pelo menos 3 casos reais, com fonte (T2).
- [ ] Datas anteriores a 2022 funcionam pela Archive API (sem CAPE) sem erro.
- [ ] O veredito sai coerente com o nível da célula do ponto.
- [ ] A precisão da localização aparece na tela sempre.

## Testes

- `tests/test_replay.py`: escolha da API por data, `would_alert` e validação de `replay_cases.json`.

## Riscos e plano B

| Risco | Plano B |
|---|---|
| A notícia só informa o município | Usar `location_precision: "municipio"` e escolher um ponto de relevo típico da região, deixando isso explícito |
| Nenhum caso "acerta" | Ser honesto no pitch, mostrar por que (ex.: capotamento em terreno plano por falha mecânica) e usar como argumento para calibrar com dados da Sompo |

## Tarefas

- [ ] Receber os casos do T2 e montar o `replay_cases.json`
- [ ] `run_replay` + testes
- [ ] Rotas
- [ ] Página Replay
- [ ] Atualizar os READMEs e o status

# W9 — Replay de acidentes reais

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | api, front-web |
| Depende de | W3, I1 (histórico), ~~T2 (casos reais)~~ — casos entregues por dev-dados em 20/09 |
| Janela | 22/09 |
| Responsável | Dev (casos: time) |
| Status | 🟦 Em revisão (API + página de replay prontas) |

## Objetivo

**Validar** o modelo com o passado: rodar o motor de risco na data e no local de acidentes reais
noticiados e responder, com honestidade, **"o sistema teria alertado?"**.

## História de usuário

> Como **avaliador da banca**, quero **ver o sistema aplicado a acidentes que realmente aconteceram**, para **acreditar que ele funciona**.

## Escopo

**Inclui**
- Casos curados pelo time (T2) num JSON versionado.
- Replay de um caso cadastrado **ou** de um ponto e data informados à mão.
- A mesma lógica do W3 com dados históricos: [regras-de-risco §9](../document/regras-de-risco.md#9-replay-w9).
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

- [x] Pelo menos 3 casos reais, com fonte — **5 entregues**, todos com link e data confirmada na notícia.
- [x] Datas anteriores a 2022 funcionam pela Archive API (sem CAPE) sem erro — e a resposta **declara** essa limitação em `limitations`, senão comparar um replay de 2019 com um de 2024 é comparar maçã com laranja.
- [x] O veredito sai coerente com o nível da célula do ponto, e cita **todos** os motivos dela.
- [x] A precisão da localização vai em `location_precision` e vira a primeira ressalva de `limitations` — **exibida** no resumo do caso, na legenda do mapa e na faixa "O que este replay não prova" (front).

### Resultado com o motor implementado: **2 de 5 no ponto, 3 de 5 na grade**

Rodado em 20/09 com a Open-Meteo real, grade 3 × 3 de ±0,005°, `L_ref` 15° — **os mesmos números
da avaliação preliminar do `dev-dados`**. Os dois placares estão lado a lado, com as diferenças
de texto e o que a convergência **não** prova, em
[document/evidencias/replay-w9-comparacao-placares.md](../document/evidencias/replay-w9-comparacao-placares.md).

| Caso | Ponto | Por quê |
|---|---|---|
| Patos de Minas (incêndio) | ❌ | 24,3 °C, UR 41%, vento 11 km/h: nenhuma das 3 condições da regra dos 30 |
| Iraí de Minas (capotamento) | ❌ (🟡 na grade) | solo úmido, limite 12,5°; a célula do ponto é mansa, uma vizinha acusa 🟡 |
| Castro (tombamento) | ❌ | 72 h com 4,6 mm → solo `dry`, apesar de a notícia falar em chuvas recentes |
| Jaborá (capotamento em ribanceira) | ✅ 🟡 | rajada de 72,4 km/h; a grade tem 🔴 em 44% da área |
| Imbituva (raio) | ✅ 🟡 | tempestade **e** 54 mm no dia — o motor apontou raio no dia em que um raio matou |

**Os três "não" são a parte honesta e útil.** Incêndio por falha mecânica e capotamento em
terreno plano não são o que este motor promete detectar. Castro é o mais instrutivo: a janela de
72 h (§3) pegou 4,6 mm, mas choveram 65,9 mm nos 21 dias anteriores — **a janela pode ser curta
demais para pastagem**, que segura água mais que lavoura. É candidato a calibração quando houver
base de sinistro de máquina, e vale mais no pitch do que cinco acertos escolhidos a dedo.

## Testes

- `tests/test_replay.py`: escolha da API por data, `would_alert` e validação de `replay_cases.json`.

## Riscos e plano B

| Risco | Plano B |
|---|---|
| A notícia só informa o município | Usar `location_precision: "municipio"` e escolher um ponto de relevo típico da região, deixando isso explícito |
| Nenhum caso "acerta" | Ser honesto no pitch, mostrar por que (ex.: capotamento em terreno plano por falha mecânica) e usar como argumento para calibrar com dados da Sompo |

## Tarefas

- [x] Receber os casos do T2 e montar o `replay_cases.json` — **5 casos curados** (dev-dados, 20/09)
- [x] `run_replay` + testes (47, todos com a Open-Meteo mockada)
- [x] Rotas `GET /replay/cases`, `GET /replay/summary` e `POST /replay`, com o replay registrado na trilha (I5)
- [x] Geometria das células (`terrain`) na resposta, na mesma forma do `/farms/{id}/terrain` — pedido do `dev-front`, que preferiu pedir a duplicar a convenção de bbox no front
- [x] Placar agregado com a ressalva colada ao número, e `failed_case_ids` quando um caso não puder ser avaliado
- [x] Página Replay
- [x] Atualizar os READMEs, `document/arquitetura.md` e o status

## Casos curados (dev-dados, 20/09/2026)

`api/app/data/replay_cases.json`, validado por `api/tests/test_replay_cases.py`.

| Caso | Data | Local | Perigo que exercita |
|---|---|---|---|
| Colheitadeira pega fogo na colheita de sorgo | 2023-06-20 | Patos de Minas/MG | incêndio (§5.5) |
| Trator capota durante plantio | 2024-02-09 | Iraí de Minas/MG | capotamento (§5.1) |
| Trator tomba ao desatolar outro em pastagem encharcada | 2025-07-04 | Castro/PR | capotamento + atolamento |
| Trator capota em ribanceira numa descida | 2026-07-18 | Jaborá/SC | capotamento em declive |
| Raio mata trabalhador em plantação de fumo | 2026-08-31 | Imbituva/PR | raio (§5.3) |

Todos com `location_precision: "municipio"`: nenhuma notícia deu coordenada. O ponto de cada caso
é a **mediana das coordenadas reais das apólices do PSR naquele município** (D1) — um ponto
agrícola de verdade da região, em vez do centroide do município ou de um palpite.

Todas as datas são ≥ 2023, então rodam pela Historical Forecast API. Nenhum caso exercita o
caminho da Archive API (< 2022), que o critério de aceite pede: vale um teste com data antiga.

### Resultado preliminar: **2 de 5 teriam sido alertados no ponto** (3 de 5 na grade)

Rodado com o motor real (`build_terrain` + `assess_day`), grade 3 × 3 de ±0,005°, `L_ref` 15°.

| Caso | Veredito no ponto | Por quê |
|---|---|---|
| Patos de Minas (incêndio) | ❌ não | 24,3 °C, UR 41%, vento 25 km/h — **nenhuma** das 3 condições da regra dos 30 |
| Iraí de Minas (capotamento) | ❌ não | terreno quase plano (máx 1,9°), limite do dia 12,5° |
| Castro (tombamento) | ❌ não | 72 h com 4,6 mm → solo `dry`, apesar de a notícia falar em chuvas recentes |
| Jaborá (capotamento) | ✅ sim | 🟡 vento (rajada 72,4 km/h); a grade tem 🔴 capotamento numa célula de 16,6° |
| Imbituva (raio) | ✅ sim | 🟡 raio (tempestade) + 🟡 atolamento (54 mm no dia) |

**Três leituras que valem o slide:**

1. **Imbituva é o acerto forte.** O motor marcou risco de raio exatamente no dia em que um raio
   matou um trabalhador — e naquele dia o Paraná registrou 37.501 raios, 122 deles no município.
2. **Jaborá mostra por que o mapa é por célula.** A célula central tem 2,2° e só acusa vento; uma
   célula a 300 m dali tem **16,6°** e acusa 🔴 capotamento acima do limite de 15°. A notícia diz
   que o trator capotou numa **ribanceira, em trecho de descida** — o motor achou o perigo certo,
   na célula certa. Um veredito que olhe só o ponto central subestima o caso.
3. **Castro expõe um limite real da regra.** A notícia diz "pastagem bastante afetada pelas chuvas
   recentes", mas a janela de 72 h (§3) pegou só 4,6 mm e classificou o solo como seco. Nos 21 dias
   anteriores choveram **65,9 mm**, com 16,3 mm quatro dias antes. **A janela de 72 h pode ser
   curta demais para pastagem**, que segura água por mais tempo que lavoura. Fica como candidato a
   calibração quando houver base de sinistro de máquina.

Os dois "não" restantes (Patos de Minas e Iraí de Minas) são honestos e úteis: incêndio por falha
mecânica e capotamento em terreno plano **não são** o que este motor promete detectar. Dizer isso
é mais forte do que escolher cinco casos que acertam.

> ⚠️ **Aviso de implementação para o `run_replay`.** A spec manda calcular o relevo com o W2, cuja
> grade padrão é 10 × 10 = **100 pontos numa chamada de elevação** — e a Elevation API **recusa**
> chamadas multiponto com 429 (ver o aviso em [dados-e-modelo.md](../document/dados-e-modelo.md)
> §2). Foi assim que a cota do time caiu em 19/09. Use uma grade pequena (a avaliação acima usou
> **3 × 3**, 9 pontos) ou fatie a chamada.

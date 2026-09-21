# W9 — Replay: os dois placares, lado a lado

**Data:** 20/09/2026 · **Responsável pelo registro:** dev-api

Este arquivo existe porque o argumento *"duas implementações independentes chegaram ao mesmo
resultado"* só vale se alguém puder conferir — e, **como se vê abaixo, ele precisou ser
reformulado**: as duas medições usam o mesmo motor de regras, então o que a convergência mostra é
estabilidade da montagem da entrada, não independência algorítmica. Aqui estão os dois placares,
como cada um foi obtido e onde os textos divergem.

## Como cada placar foi obtido

| | Avaliação preliminar | Implementação da API |
|---|---|---|
| Quem | `dev-dados`, ao curar os casos | `dev-api`, ao implementar a W9 |
| Quando | 20/09/2026, antes da feature existir | 20/09/2026, depois de `run_replay` pronto |
| Código | script de avaliação chamando `build_terrain` + `assess_day` | `app/services/replay.py::run_case`, pela rota |
| Grade | 3 × 3, ±0,005° | 3 × 3, ±0,005° |
| `L_ref` | 15° | 15° |
| Clima | Open-Meteo Historical Forecast | Open-Meteo Historical Forecast |
| Registro | tabela na spec `feature/W9-modo-replay.md` | execução única registrada no relatório da W9 |

As duas usam o **mesmo motor de regras** (`assess_day`), então o que se compara aqui não é a
regra: é se duas montagens diferentes da entrada — grade, ponto, janela de clima — levam ao mesmo
veredito. É uma verificação de integração, não de independência algorítmica, e **é assim que
deve ser apresentada**.

## Placar

**Os dois: 2 de 5 alertados no ponto · 3 de 5 na grade.**

| Caso | Preliminar (ponto) | API (ponto) | Confere? |
|---|---|---|---|
| Patos de Minas — incêndio em colheitadeira (2023-06-20) | ❌ | ❌ | ✅ |
| Iraí de Minas — capotamento no plantio (2024-02-09) | ❌ | ❌ (🟡 na grade) | ✅ |
| Castro — tombamento ao desatolar (2025-07-04) | ❌ | ❌ | ✅ |
| Jaborá — capotamento em ribanceira (2026-07-18) | ✅ | ✅ 🟡 | ✅ |
| Imbituva — raio mata trabalhador (2026-08-31) | ✅ | ✅ 🟡 | ✅ |

## Números medidos pela API

| Caso | Chuva dia | 72 h | T máx | UR mín | Vento | Rajada | Tempestade | Solo | Limite | Grade |
|---|---|---|---|---|---|---|---|---|---|---|
| Patos de Minas | 0,0 mm | 0,0 mm | 24,3 °C | 41% | 11,2 km/h | 24,8 km/h | não | seco | 15,0° | 0% 🔴 0% 🟡 |
| Iraí de Minas | 6,0 mm | 24,6 mm | 27,6 °C | 61% | 14,7 km/h | 31,3 km/h | não | úmido | 12,5° | 0% 🔴 33,3% 🟡 |
| Castro | 0,3 mm | 4,6 mm | 14,7 °C | 63% | 22,2 km/h | 41,8 km/h | não | seco | 15,0° | 0% 🔴 0% 🟡 |
| Jaborá | 0,0 mm | 0,0 mm | 25,7 °C | 44% | 33,0 km/h | 72,4 km/h | não | seco | 15,0° | 44,4% 🔴 55,6% 🟡 |
| Imbituva | 54,0 mm | 65,6 mm | 20,1 °C | 86% | 16,3 km/h | 38,9 km/h | **sim** | encharcado | 10,0° | 66,7% 🔴 33,3% 🟡 |

Motivos na célula do ponto, nos dois casos que alertaram:

- **Jaborá:** *"Rajadas de 72,4 km/h: risco para máquinas altas."*
- **Imbituva:** *"Chuva de 54 mm no dia: risco de atolamento."* e *"Tempestade prevista: risco de raio."*

## As três diferenças de texto (nenhuma muda o veredito)

1. **Patos de Minas, "vento 25 km/h".** A avaliação preliminar cita 25 km/h; a API mediu
   **vento 11,2 km/h e rajada 24,8 km/h**. É a mesma medição descrita por variáveis diferentes —
   provavelmente a rajada, arredondada. Não altera nada: a regra dos 30 (§5.5) usa
   `wind_max_kmh > 30`, e **nenhuma** das três condições é atendida nos dois relatos.
2. **Iraí de Minas, o alerta na grade.** A preliminar descreve o terreno como "quase plano (máx
   1.9°)", o que não explicaria um 🟡 de capotamento. A API registrou 33,3% da grade em 🟡 num dia
   de solo úmido — compatível com **atolamento em célula de baixada** (§5.2), não com
   capotamento. **Não re-medi os motivos célula a célula desse caso**, então isto é a explicação
   mais provável, não um fato conferido.
3. **Jaborá, a célula íngreme.** A preliminar cita uma célula de 16,6°; a API não imprimiu as
   inclinações, só os percentuais (44,4% da grade em 🔴). Os dois são consistentes, mas o número
   16,6° vem só da preliminar.

## O que este placar **não** prova

- **Não é validação estatística.** Cinco casos, todos com coordenada de município. Um placar de
  2/5 ou 3/5 aqui não estima taxa de acerto de nada.
- **Não são implementações algoritmicamente independentes.** As duas chamam o mesmo
  `assess_day`. O que a convergência mostra é que a montagem da entrada (grade, ponto, janela de
  clima) está estável entre um script solto e a rota da API.
- **Os números de clima não foram medidos duas vezes.** A comparação usa os valores que cada
  execução registrou; um novo `run` gastaria cota da Open-Meteo, que já respondeu 429 neste
  projeto em 19/09.
- **A grade de 3 × 3 joga contra o placar**, não a favor. Células de ~370 m suavizam mais o
  gradiente do DEM que as de ~110 m do mapa de fazenda, então a inclinação calculada sai **menor**
  e fica mais difícil cruzar o limite de capotamento. Quem quisesse inflar o resultado usaria uma
  grade mais fina, não mais grossa. *(Observação da revisão da W9.)*

## A terceira medição: `GET /replay/summary`

Desde 20/09 a API expõe o placar como endpoint. Ele roda **os mesmos** `run_case` da tabela acima,
então tem de dar o mesmo número — e um teste (`test_the_summary_matches_the_individual_replays`)
falha se divergir dos replays um a um.

O endpoint devolve, junto do placar, o texto que o enquadra: cinco casos não são amostra, as
coordenadas são de município e os casos foram escolhidos para exercitar perigos diferentes, não
sorteados. Se algum caso não puder ser avaliado (Open-Meteo fora), ele entra em `failed_case_ids`
e o texto diz quantos de quantos foram avaliados — **o placar não mente por omissão**.

```bash
curl -s localhost:8000/api/v1/replay/summary | jq '{evaluated, would_alert_at_point, would_alert_in_grid, note}'
```

## Como reproduzir

```bash
cd api
uv run fastapi dev app/main.py
for caso in $(curl -s localhost:8000/api/v1/replay/cases | jq -r '.[].id'); do
  curl -s -X POST localhost:8000/api/v1/replay -H 'content-type: application/json' \
    -d "{\"case_id\":\"$caso\"}" | jq '{id: .case.id, would_alert, would_alert_in_grid, verdict}'
done
```

⚠️ Cada caso gasta uma chamada de elevação (9 pontos) e uma de histórico. O relevo fica 24 h em
cache; o histórico, 7 dias. **Não rode em laço durante o desenvolvimento.**

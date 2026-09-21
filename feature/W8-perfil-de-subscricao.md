# W8 — Perfil de risco do terreno para subscrição

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | api, front-web |
| Depende de | W2 |
| Janela | 21/09 |
| Responsável | Dev |
| Status | ✅ API + página de subscrição |

## Objetivo

Entregar à **Sompo** um perfil objetivo do terreno no momento da cotação, **sem questionário e sem
hardware**. É a feature que fala direto com o negócio da seguradora.

## História de usuário

> Como **subscritor da Sompo**, quero **ver em segundos o quanto o terreno de uma fazenda é arriscado para máquinas**, para **precificar melhor e decidir a aceitação**.

## Escopo

**Inclui**
- Indicadores, score e classe A/B/C: [regras-de-risco §8](../document/regras-de-risco.md#8-perfil-de-subscrição-w8).
- Os 3 fatores que mais pesaram no score ("o que mais pesa").
- Uma tabela comparando **todas as fazendas cadastradas** (uma visão de "carteira").

**Opcional (P2)**
- Exposição climática nos últimos 12 meses: dias com solo `saturated` e dias com tempestade (Archive API).

**Não inclui**
- Preço ou prêmio (é decisão da Sompo). Mostramos apenas o risco.

## Implementação

### API (`api/`)
- `app/services/underwriting.py`: `terrain_profile(terrain, l_ref) -> UnderwritingProfile` (função pura).
- `app/schemas/underwriting.py`: `UnderwritingProfile(farm_id, pct_slope_lt8, pct_slope_8_15, pct_slope_gt15, pct_lowland, pct_exposed, terrain_score, risk_class, drivers: list[str])`.
- `GET /api/v1/farms/{farm_id}/underwriting`
- (P2) `GET /api/v1/farms/{farm_id}/underwriting?include_climate=true`

### Front-web (`front-web/`)
- Página **Subscrição**:
  - seletor de fazenda (compartilhado com o W1);
  - um selo grande com a classe (**A** verde / **B** amarela / **C** vermelha) e o score;
  - `st.metric` para cada indicador;
  - gráfico de barras com a distribuição da inclinação;
  - "O que mais pesa": os 3 drivers;
  - tabela da carteira (todas as fazendas, ordenadas pelo score);
  - uma nota: "Pesos v1, a calibrar com a base de sinistros da Sompo".

## Critérios de aceite

- [x] Um teste com os números do exemplo dá o resultado esperado. Exemplo: 23% > 15°, 40% entre 8 e 15°, 8% de baixada, 15% exposta → score **48,5**, classe **B**.
- [x] A fazenda plana tira classe A (Sorriso: **100,0**). A de café tira B ou C (Carmo de Minas: **41,2**, classe B, quase C).
- [x] O score fica sempre entre 0 e 100, inclusive com todos os fatores em 100%.

### A carteira, com o relevo real das três fazendas

| Classe | Score | Fazenda | O que mais pesa |
|---|---|---|---|
| **A** | 100,0 | `graos-sorriso` (Sorriso/MT) | nada: 100% da área abaixo de 8°, sem baixada nem topo exposto |
| **B** | 62,7 | `uva-serra-gaucha` (Bento Gonçalves/RS) | 27% entre 8° e 15° · 25% em baixada · 21% em topo exposto |
| **B** | 41,2 | `cafe-carmo-de-minas` (Carmo de Minas/MG) | 46% entre 8° e 15° · 17% acima de 15° · 25% em baixada |

*(Os pesos são v1 e arbitrários; a resposta da API diz isso em `calibration_note`.)*

### `L_ref` não entra no score

A spec passa `l_ref` para `terrain_profile`, mas a fórmula de §8 **não usa** o limite de
referência: o perfil é só do relevo. O `reference_tilt_limit_deg` vai na resposta como contexto,
e o schema diz explicitamente que o score não depende dele. *(Registrado na W8.)*

## Testes

- `tests/test_underwriting.py`: exemplo acima, limites de 0 e 100, bordas das classes (39,9/40/69,9/70).

## Tarefas

- [x] `terrain_profile` + testes (o exemplo da spec, as bordas 39,9/40/69,9/70 e cada peso isolado)
- [x] Rota `GET /api/v1/farms/{farm_id}/underwriting`
- [ ] Página Subscrição + tabela da carteira (o front chama a rota uma vez por fazenda; o relevo fica 24 h em cache)
- [x] Atualizar os READMEs, `document/arquitetura.md` e o status

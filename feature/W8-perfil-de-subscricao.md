# W8 — Perfil de risco do terreno para subscrição

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | api, front-web |
| Depende de | W2 |
| Janela | 21/09 |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Entregar à **Sompo** um perfil objetivo do terreno no momento da cotação, **sem questionário e sem
hardware**. É a feature que fala direto com o negócio da seguradora.

## História de usuário

> Como **subscritor da Sompo**, quero **ver em segundos o quanto o terreno de uma fazenda é arriscado para máquinas**, para **precificar melhor e decidir a aceitação**.

## Escopo

**Inclui**
- Indicadores, score e classe A/B/C: [regras-de-risco §8](../docs/regras-de-risco.md#8-perfil-de-subscrição-w8).
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

- [ ] Um teste com os números do exemplo dá o resultado esperado. Exemplo: 23% > 15°, 40% entre 8 e 15°, 8% de baixada, 15% exposta → score **48,5**, classe **B**.
- [ ] A fazenda plana tira classe A. A de café tira B ou C.
- [ ] O score fica sempre entre 0 e 100.

## Testes

- `tests/test_underwriting.py`: exemplo acima, limites de 0 e 100, bordas das classes (39,9/40/69,9/70).

## Tarefas

- [ ] `terrain_profile` + testes
- [ ] Rota
- [ ] Página Subscrição + tabela da carteira
- [ ] Atualizar os READMEs e o status

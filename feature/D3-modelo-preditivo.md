# D3 — Modelo preditivo de risco e métricas

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api (dados) |
| Depende de | D2 |
| Janela | 21/09 |
| Responsável | dev-dados |
| Status | ✅ Concluída (21/09/2026) — retreinada com 2.256 linhas: o modelo **supera** o baseline no teste, com ressalvas; as regras seguem comandando o alerta |

## Objetivo

Entregar o **modelo preditivo** que o enunciado cobra, treinado em dados reais (D2), com métricas
honestas e comparação contra o baseline de regras. O modelo **complementa** as regras: as regras
explicam o alerta ao operador, o modelo dá a probabilidade para a seguradora.

## Escopo

**Inclui**
- Baseline: o score por regras de [regras-de-risco.md](../document/regras-de-risco.md) aplicado ao dataset.
- Modelos candidatos: **regressão logística** (com padronização) e **floresta aleatória**. Vence o de melhor AUC-PR, com desempate pela simplicidade.
- Divisão **temporal**: **treino ≤ 2021 · validação 2022–2023 · teste 2024**. Sem embaralhar.
  > ⚠️ **2025 não pode ser teste** (corrigido na D2): as apólices de 2025 estão em vigência e `indemnity_value` é nulo — isso é **censura**, não rótulo 0. Testar em 2025 mediria 0% de sinistro e produziria métrica sem sentido. A população utilizável é **2016–2024**, e o arquivo de 2006–2015 sai inteiro porque tem vigência de duração zero.
- Métricas: AUC-ROC, **AUC-PR**, recall e precisão no limiar escolhido, matriz de confusão e curva de calibração.
- Importância das variáveis (coeficientes ou importância por permutação).
- Artefato versionado: `api/app/data/model/risk_model_v1.joblib` + `risk_model_v1.json` (data, dataset, features, métricas, limiar).
- Carregamento na API e uso no score (a integração na tela é a W13).
- Seção de resultados em `document/dados-e-modelo.md`, com as limitações.

**Não inclui**
- Treino em produção, ajuste automático de hiperparâmetros pesado, deep learning.

## Regras e lógica

- Evento raro: **nunca** use acurácia como métrica principal. Trate o desbalanceamento com `class_weight="balanced"`.
- Limiar escolhido pelo custo do erro: um falso negativo (não avisar e acontecer) é bem pior que um falso alarme. Justifique o limiar no documento.
- Se o modelo **não** superar o baseline, isso é um resultado válido: publique, explique e mantenha as regras no comando.

## Implementação

### API (`api/`)
- `app/services/model.py`: `train(df) -> TrainResult`, `evaluate(model, df) -> Metrics`, `load_model()`, `predict_proba(features) -> float`.
- `scripts/train_model.py`: treina, avalia, salva o artefato e imprime a tabela de métricas.
- Dependência: `uv add scikit-learn joblib`.

## Critérios de aceite

- [x] `scripts/train_model.py` roda de ponta a ponta e imprime **baseline × modelo** na mesma tabela.
- [x] As métricas do JSON são as mesmas impressas (sem número escrito à mão).
- [x] O conjunto de teste (**2024**) nunca foi usado para escolher nada — nem modelo, nem limiar, nem feature.
- [x] A API carrega o modelo em menos de 1 s e responde `predict_proba` em menos de 50 ms.
- [x] Sem o arquivo do modelo, a API sobe do mesmo jeito e usa só as regras (com aviso no log).
- [x] Limitações escritas: rótulo é seguro agrícola (não máquina), viés de quem contrata seguro, amostra.

## Tarefas

- [x] Baseline por regras sobre o dataset
- [x] Treino, avaliação e seleção + testes
- [x] Artefato versionado + carregamento na API
- [x] Seção de resultados e limitações na documentação

## Resultado (21/09/2026) — com o dataset completo

**O modelo supera o baseline por regras.** No teste (2024, 26 positivos em 305):

| | AUC-ROC | AUC-PR | Recall | Precisão |
|---|---|---|---|---|
| Baseline (regras) | 0,404 | 0,074 | 0,846 | 0,081 |
| **Regressão logística (escolhida)** | **0,654** | **0,144** | 0,923 | 0,101 |
| Floresta aleatória | 0,681 | 0,196 | 0,923 | 0,113 |

Bootstrap pareado da diferença de AUC-PR: **+0,0697**, IC 95% **[+0,003, +0,169]**,
P(modelo > baseline) = **0,98** (10.000 reamostragens). Na validação (62 positivos) o mesmo sinal:
0,151 × 0,126. `beats_baseline: true` no artefato.

**As três ressalvas que viajam com o número:**

1. **26 positivos no teste**, abaixo dos 30 que o próprio `train_model.py` declara como mínimo
   (`MIN_TEST_POSITIVES`) — o aviso disparou na execução.
2. **O IC 95% quase toca o zero** no extremo inferior (+0,003).
3. **A virada veio do conjunto de teste, não do modelo.** Auditado em 21/09 com os dois artefatos
   lado a lado: o modelo de 20/09, **sem retreino**, já venceria no teste novo (+0,059), e o de
   21/09 ainda perderia na fatia antiga (−0,023). Da variação total de +0,088 na vantagem, a troca
   do teste responde por +0,077 a +0,093 e o retreino por +0,011.

**Por que a fatia antiga enganava:** ela tinha 8 sinistros em 155 linhas (5,2%), contra **9,46%**
da população de 2024 no PSR. O teste de hoje, com 305 linhas e 8,5%, bate com a população
(binomial p = 0,70). Ano, cultura e rótulo geral não derivaram entre as linhas antigas e as novas
(χ² p = 0,33 · 0,50 · Fisher 0,54); só a UF derivou de leve (p = 0,015).

**E o baseline nunca discriminou este alvo:** sua AUC-ROC por safra fica entre 0,370 e 0,613 nas
nove safras, e o IC 95% contém 0,5 em todos os recortes — inclusive no 0,622 publicado em 20/09
([0,381, 0,827], com 8 positivos). O motivo de fundo: o baseline é um score de **encharcamento e
tempestade**, e `target_claim` é dominado por **seca** (74,4% das indenizações de 2024) e geada.
Contra `target_rain_claim`, que é o alvo das regras, o baseline marca AUC-ROC **0,625** no dataset
inteiro. **O modelo ganha no alvo amplo, não no perigo que o alerta trata** — por isso a
[regras-de-risco §11](../document/regras-de-risco.md) continua valendo sem mudança.

**Por que continua a regressão logística, com a floresta melhor no teste (0,196 × 0,144):** na
validação, o único lugar onde se pode escolher, a **logística ganhou** (0,151 × 0,146; a floresta
fica −0,0045 contra um erro padrão de 0,0190, então a regra de um erro padrão nem precisou
desempatar). Escolher pelo teste queimaria o único conjunto limpo. E a floresta é o próprio
exemplo do ruído: em 20/09 ela parecia melhor na validação e saiu pior no teste; em 21/09,
o inverso.

Números completos, decomposição, importância das variáveis e limitações em
[dados-e-modelo.md](../document/dados-e-modelo.md).

### Resultado de 20/09/2026 (mantido para histórico)

Com o dataset parcial de 1.184 linhas, o modelo **não** superava o baseline: no teste (8 positivos
em 155), baseline AUC-PR **0,091** × **0,073** do modelo, diferença −0,019 com IC 95%
[−0,128, +0,038] e P(modelo > baseline) = 0,26 — indistinguíveis. A conclusão publicada então era
correta para o que estava medido; a auditoria de 21/09 mostrou que era uma afirmação sobre **8
sinistros**, não sobre o modelo.

**Duas decisões que valem registro (válidas nas duas datas):**
- **Regra de um erro padrão na seleção.** O candidato mais complexo só entra se ganhar acima do
  erro padrão da diferença, estimado por bootstrap pareado na validação. Decidido sem olhar o teste.
- **`target_rain_claim` não é avaliável:** 4 positivos no teste (2 em 20/09). Métricas
  indicativas; a conclusão se apoia no `target_claim`.

**Integração (W13, concluída):** o serviço expõe `get_model()` (com cache) e `predict_proba()`, e a
W13 os liga à rota e à tela. O texto da ressalva exibido na API e no front é montado a partir do
JSON do artefato (`app/services/model_scoring.py::_note`), então ele acompanhou a virada do
resultado sem edição manual.

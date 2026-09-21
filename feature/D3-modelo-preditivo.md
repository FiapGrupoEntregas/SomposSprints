# D3 — Modelo preditivo de risco e métricas

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api (dados) |
| Depende de | D2 |
| Janela | 21/09 |
| Responsável | dev-dados |
| Status | ✅ Concluída (20/09/2026) — modelo **não** supera o baseline; as regras seguem no comando |

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

## Resultado (20/09/2026)

**O modelo não supera o baseline por regras.** No teste (2024, 8 positivos em 155):

| | AUC-ROC | AUC-PR | Recall | Precisão |
|---|---|---|---|---|
| **Baseline (regras)** | **0,622** | **0,091** | 0,875 | 0,053 |
| Regressão logística (escolhida) | 0,605 | 0,073 | 1,000 | 0,055 |

**E a amostra não permitiria provar que supera.** Bootstrap pareado da diferença de AUC-PR no
teste: diferença **−0,019**, IC 95% **[−0,128, +0,038]**, P(modelo > baseline) = **0,26**. O zero
está dentro do intervalo — com 8 positivos os dois são **indistinguíveis**. A leitura honesta é
"não superou, e não daria para demonstrar superioridade nem se ela existisse", não "o ganho é
zero".

Na **validação** (29 positivos) o modelo ganha (AUC-PR 0,124 × 0,118). A inversão entre validação
e teste é a própria tese do deslocamento temporal, não uma contradição.

Publicado assim, como [regras-de-risco §11](../document/regras-de-risco.md) já previa. Os números
completos, a importância das variáveis e a leitura do resultado estão em
[dados-e-modelo.md](../document/dados-e-modelo.md).

**O que o resultado diz:** a taxa de sinistro varia de 5,0% (2017) a 41,3% (2021) — oito vezes. O
regime macroclimático do ano domina qualquer sinal de relevo ou de clima da safra, e 9 safras não
bastam para aprendê-lo. A divisão temporal expôs isso; uma divisão aleatória teria escondido.

**Duas decisões que valem registro:**
- **Regra de um erro padrão na seleção.** A floresta ganhou por 0,002 de AUC-PR na validação, com 29 positivos — ruído. O candidato mais complexo só entra se ganhar acima do erro padrão da diferença (bootstrap pareado). A floresta não passou, e no teste de fato saiu pior (0,051). Decidido sem olhar o teste.
- **`target_rain_claim` não é avaliável:** 2 positivos no teste. Métricas indicativas; a conclusão se apoia no `target_claim`.

**Pendente para a W13:** o serviço expõe `get_model()` (com cache) e `predict_proba()`, prontos
para a rota. A ligação em `app/main.py` e no endpoint fica com a W13, que é dona daqueles arquivos.

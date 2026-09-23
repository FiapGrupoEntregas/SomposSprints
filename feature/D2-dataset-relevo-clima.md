# D2 — Dataset de treino: relevo × clima × sinistro

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api (dados) |
| Depende de | D1, I1, W2 |
| Janela | 20/09 |
| Responsável | dev-dados |
| Status | ✅ Concluída (21/09/2026) — dataset com **2.256 linhas**, acima das 2.000 que o critério pede |

## Objetivo

Transformar as apólices reais (D1) em um **dataset de treino**: para cada propriedade, o relevo do
local e o clima do período de vigência, com o rótulo "houve sinistro climático indenizado".

## Escopo

**Inclui**
- Amostra estratificada de **2.000 a 5.000 apólices** (por UF, cultura, ano e presença de indenização), para caber no limite de chamadas das APIs.
- **Relevo** por propriedade (reaproveita o W2): inclinação, orientação, classe de terreno e amplitude em uma grade pequena (3×3, ~±0,005°) ao redor do ponto.
- **Clima** do período de vigência (Open-Meteo Archive/Historical): chuva total, número de dias com chuva ≥ 30 mm em 72 h, dias com tempestade, rajada máxima, temperatura máxima, UR mínima e dias secos consecutivos.
- **Contexto**: cultura, área, UF, mês de início da vigência.
- Rótulos: `target_claim` (indenização > 0) e `target_rain_claim` (indenização com evento `chuva_excessiva` ou `granizo`).
- Cache agressivo e execução **em lote**, salvando parcial (dá para retomar).
- Dataset final versionado em `data/dataset_treino.parquet` (ou CSV, se pequeno) + ficha com data de geração.

**Não inclui**
- Dado que não existiria na hora da previsão (vazamento). Nada que dependa do resultado do sinistro.

## Regras e lógica

- Uma linha por apólice. Nada de repetir a mesma propriedade em treino e teste.
- Janela climática = da data de início à data de fim da vigência, limitada a 12 meses.
- Coordenada inválida ou clima indisponível → a linha é descartada **e contada** no relatório.
- Toda feature leva a unidade no nome e é documentada em `document/dados-e-modelo.md`.

## Implementação

### API (`api/`)
- `app/services/dataset.py`: `sample_policies(session, n, seed) -> DataFrame`, `build_features(policies, client) -> (DataFrame, DatasetReport)`, `save_dataset(df, path)`, mais `terrain_features`, `weather_window`, `clip_to_coverage` e `climate_features` como funções puras testáveis.
- `scripts/build_dataset.py`: lote com retomada, limitador de ritmo **separado para elevação** e nova tentativa com recuo exponencial.
- Reuso: `app/services/terrain.py` (W2) e `app/clients/open_meteo.py` (I1).
- `scripts/build_dataset.py`: roda em lote, com barra de progresso, retomada e limite de requisições por minuto.
- Dependência: `uv add pyarrow` (se for parquet).

## Critérios de aceite

- [x] O dataset tem pelo menos 2.000 linhas, com a taxa de sinistro real preservada — **2.256 linhas**, `target_claim` 17,3%. A fidelidade melhorou com o lote completado: o desvio médio da taxa de sinistro por safra contra a população caiu de 2,80 pp (1.184 linhas) para **0,90 pp**.
- [x] Nenhuma feature usa informação posterior ao fim da vigência.
- [x] A geração é retomável: matar o script no meio e rodar de novo não perde o que já foi feito.
- [x] Ficha no `document/dados-e-modelo.md` com a data de geração, o número de linhas, as fontes e as features.
- [x] Testes offline com 20 apólices e um cliente Open-Meteo mockado.

## Riscos e plano B

| Risco | Plano B |
|---|---|
| Muitas chamadas à Open-Meteo (limite diário) | Reduzir a amostra, agrupar por município (as fazendas próximas compartilham o clima) e usar cache em disco |
| Geração demorada | Rodar em segundo plano e versionar o dataset pronto. A demo não depende de gerar na hora |

## Tarefas

- [x] Amostragem estratificada + testes
- [x] Construção das features + testes
- [x] Script em lote com retomada
- [x] Dataset gerado, versionado e documentado (2.256 linhas)

## Resultado (21/09/2026)

`data/dataset_treino.parquet`: **2.256 linhas** (de 2.500 pedidas), 2016–2024, 15 UFs, 32 culturas.
`target_claim` 390 (**17,3%**) · `target_rain_claim` 94 (**4,2%**). Ficha em `data/dataset_treino.json`.

**Por que 2.256 e não 2.500.** A execução parou pela **cota diária** da Open-Meteo, depois de
2.226 chamadas bem-sucedidas e 644 recusas 429. A confirmação veio de uma chamada avulsa com
`curl` logo após a parada: os **três** subdomínios (elevação, `archive-api` e
`historical-forecast-api`) responderam 429 com `"Daily API request limit exceeded"`. A execução foi
encerrada à mão e o parquet fechado com `--consolidar`, que não gasta chamada. As 244 linhas
restantes não mudam nada: o critério pede 2.000. Descartes: 47 propriedades sem elevação e 112
janelas de clima recusadas.

> ⚠️ **Armadilha registrada:** chegamos a diagnosticar "limite de ritmo" porque o log não tem
> nenhum `Daily API request limit exceeded`. Ele **não pode ter**: o cliente chama
> `raise_for_status()` e o `httpx.HTTPStatusError` carrega só código e URL — o corpo, onde está o
> motivo, nunca chega ao logger. A única forma de distinguir cota de ritmo hoje é uma chamada
> avulsa olhando o corpo.

**As duas paradas da D2 foram pela mesma causa**, em subdomínios diferentes: 20/09 no `archive-api`
(elevação e `historical-forecast` ainda respondiam 200) e 21/09 nos três. O que estoura o teto é o
**peso**: a cota é de 10.000 unidades/dia e cada chamada de clima cobre a vigência inteira
(~300 dias), o que pela regra da própria Open-Meteo vale ~21 chamadas. Baixar o ritmo não ajuda.
Detalhes em
[dados-e-modelo.md](../document/dados-e-modelo.md#orçamento-de-chamadas-da-open-meteo).

### Resultado de 20/09/2026 (parcial, mantido para histórico)

`data/dataset_treino.parquet`: **1.184 linhas**. `target_claim` 199 (16,8%). Consumo: 2.345 chamadas.
Parou pela **cota diária** do `archive-api`.

**Dois achados que mudaram o desenho:**

1. **Todo o arquivo PSR de 2006–2015 tem vigência de zero dias** (430.843 de 430.843,
   `DT_INICIO_VIGENCIA == DT_FIM_VIGENCIA`). Sem janela climática, essas linhas só produziriam
   ruído — e consumiriam 29% da cota. A população utilizável é 2016–2024, e a taxa de sinistro
   dela é **18,2%**, não os 16,2% da base inteira.
2. **A Elevation API recusa chamadas multiponto**, apesar de documentar 100 pontos. Vai uma
   propriedade (9 pontos) por chamada, com limitador próprio.

### O que a amostra completada mostrou

A fatia de 2024 das primeiras 1.184 linhas tinha **5,2%** de sinistro; a população de 2024 tem
**9,46%** e a amostra completa tem **8,5%**. Era uma fatia atípica, e foi ela que sustentou a
conclusão da D3 publicada em 20/09 — ver [D3](D3-modelo-preditivo.md).

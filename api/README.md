# api/ — Backend FastAPI

A API é o cérebro do AgriShield:
- calcula o **relevo** (inclinação e classes de terreno) a partir da Open-Meteo Elevation API;
- roda o **motor de risco relevo × clima** ([document/regras-de-risco.md](../document/regras-de-risco.md));
- calcula o **limite de inclinação do dia** e publica para o ESP32 via MQTT;
- recebe a **telemetria e os eventos** do ESP32 e entrega tudo ao `front-web/` por REST.

O front-web **não tem regra de negócio**: ele só consome esta API.

## Estrutura

```
api/
├── app/
│   ├── main.py            # cria o FastAPI e registra os routers
│   ├── core/config.py     # Settings (variáveis AGRISHIELD_*)
│   ├── core/security.py   # chave de API (I5) · core/logging.py: log JSON + request_id
│   ├── core/versions.py   # versão das regras e do modelo gravada em cada decisão (I5)
│   ├── core/cache.py      # TTLCache em memória, com stale-if-error (I1)
│   ├── api/v1/router.py   # agrega os routers da v1
│   ├── api/v1/routes/     # uma rota por recurso (health, farms, devices, replay…)
│   ├── data/farms.json    # catálogo das fazendas de demonstração (W1)
│   ├── db.py              # engine do SQLite, create_db() e get_session()
│   ├── models.py          # tabelas SQLModel (policy, telemetry, device_event, device_status…)
│   ├── schemas/           # modelos Pydantic de entrada/saída (inclusive as mensagens MQTT)
│   ├── services/          # regras (risk, limits, recommendations, underwriting, reports,
│   │                      #  model_scoring, replay…)
│   ├── repositories/      # acesso ao banco (devices e audit: trilha de decisões)
│   ├── mqtt/              # ponte MQTT (bridge.py), gravação (handlers.py) e envio do limite (publisher.py)
│   └── clients/           # clientes externos (Open-Meteo, Telegram)
├── tests/                 # pytest
│   ├── fixtures/          # respostas reais da Open-Meteo, payloads reais do ESP32 e CSV sujo do PSR
│   │                      # (os testes unitários não usam rede nem broker)
│   └── e2e/               # ponta a ponta (I6): API + broker + simulador de verdade, marca `e2e`
├── pyproject.toml         # dependências (fonte da verdade) — uv
├── uv.lock                # lock do uv
├── requirements.txt       # gerado do uv.lock — para pip / deploy
└── requirements-dev.txt   # gerado do uv.lock — pip + ferramentas de dev
```

## Rodando

### Com uv (recomendado)

```bash
cd api
uv sync                              # cria .venv e instala tudo (inclui dev)
cp .env.example .env                 # usa dev explicitamente para a demo pública local
uv run fastapi dev app/main.py       # http://localhost:8000/docs
uv run pytest
uv run ruff check . && uv run ruff format .
```

### Com pip

```bash
cd api
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt  # só produção: requirements.txt
uvicorn app.main:app --reload        # http://localhost:8000/docs
pytest
```

## Dependências

`pyproject.toml` é a fonte da verdade. Para adicionar uma dependência:

```bash
cd api
uv add nome-do-pacote                # ou: uv add --dev nome-do-pacote
../scripts/sync-requirements.sh      # regenera requirements*.txt a partir do uv.lock
```

**Nunca edite `requirements*.txt` à mão.** A CI falha se eles estiverem fora de sincronia com o `uv.lock`.

## Ambientes e segurança

`AGRISHIELD_ENVIRONMENT=dev` precisa estar definido explicitamente para manter as leituras públicas
na demo local. Com a variável ausente, vazia ou diferente de `dev`, as rotas de fazendas,
equipamentos, relatórios e replay exigem `X-API-Key`; `/health` continua público. A mesma chave
configurada em `AGRISHIELD_API_KEYS` segue protegendo publicação de limites e `/audit`.

Com MQTT habilitado fora de `dev`, a API falha no início se o host não for privado (IP privado ou
DNS `.internal`/`.local`), se não houver CA TLS validável ou credenciais de usuário e senha. TLS
valida o certificado do broker e exige TLS 1.2 ou superior. Certificado e chave de cliente são
opcionais, mas devem ser informados juntos. Configure `AGRISHIELD_MQTT_CA_CERT`,
`AGRISHIELD_MQTT_CLIENT_CERT`, `AGRISHIELD_MQTT_CLIENT_KEY`, `AGRISHIELD_MQTT_USERNAME` e
`AGRISHIELD_MQTT_PASSWORD` no ambiente seguro; não os registre nem os versione.

Esses guardrails protegem configuração e transporte; **não criam autenticação, autorização ou ACL
no broker MQTT**. A ACL por dispositivo/identidade de serviço ainda precisa ser configurada no
broker. Veja [document/contrato-mqtt.md](../document/contrato-mqtt.md).

## Base de sinistros do PSR/SISSER (D1)

`app/services/psr_ingest.py` transforma os CSVs abertos do Ministério da Agricultura na tabela
`policy`, em quatro etapas testáveis:

| Função | Etapa |
|---|---|
| `read_psr(path)` / `iter_psr(path, chunk_size)` | extrair (`;`, ISO-8859-1, `-` como vazio) |
| `clean(df) -> (df, QualityReport)` | validar, limpar e transformar |
| `load_to_db(df, session)` | carregar (idempotente: `UNIQUE (proposal_id)`) |
| `ingest_file(path, session)` | o pipeline inteiro, em blocos |

```bash
uv run --project .. python ../scripts/download_psr.py   # baixa ~470 MB para data/raw/
uv run --project .. python ../scripts/load_psr.py       # carrega + relatório de qualidade
```

**LGPD (ADR-011).** `read_psr` passa uma lista branca de colunas ao pandas, então `NM_SEGURADO` e
`NR_DOCUMENTO_SEGURADO` não chegam nem a virar `DataFrame`.
`tests/test_psr_ingest.py::test_colunas_pessoais_nunca_chegam_ao_banco` falha se um nome ou
documento aparecer em qualquer campo da tabela `policy`.

### Resíduo de dado pessoal (LGPD)

⚠️ **Dito com todas as letras.** O banco e a amostra guardam o `proposal_id`
(`ID_PROPOSTA` no CSV), que é **chave de junção de volta ao CSV público do PSR — e esse CSV tem o
nome do segurado**. Ou seja: não é correto dizer "nenhum dado pessoal versionado" sem qualificar.
O que é correto dizer:

- nenhum nome, documento ou endereço está **no nosso repositório ou no nosso banco**;
- o `proposal_id` é guardado porque é a **única** chave que permite deduplicar a carga e refazer
  a ingestão de forma reproduzível — sem ele, recarregar o CSV duplicaria 1,5 milhão de linhas;
- quem quisesse o nome precisaria **baixar o CSV do Mapa por conta própria** e cruzar. A fonte é
  dado aberto (CC-BY) e já está publicada com o nome, então o `proposal_id` não amplia o acesso a
  nada que já não seja público — mas **facilita a junção**, e é isso que fica registrado aqui;
- se em algum momento a reprodutibilidade deixar de valer esse resíduo, o caminho é trocar o
  `proposal_id` por um hash com sal guardado fora do repositório. Está no roadmap, não feito.

**Relatório de qualidade.** `QualityReport` conta as linhas lidas, as descartadas **por motivo**, as
efetivamente **gravadas** (`rows_loaded`), os valores corrigidos, a origem de cada coordenada e a
distribuição por evento e por UF. Nada de `dropna()` silencioso — e numa recarga o relatório diz
`linhas gravadas 0` com um aviso, em vez de deixar parecer que gravou tudo de novo. Os números da carga de 19/09/2026 estão em
[document/dados-e-modelo.md](../document/dados-e-modelo.md).

## Dataset de treino (D2)

`app/services/dataset.py` cruza as apólices da tabela `policy` com o relevo (W2) e o clima da
vigência (I1), e entrega uma linha por apólice com os dois rótulos que a D3 vai prever.

| Função | Etapa |
|---|---|
| `sample_policies(session, n, seed)` | amostra estratificada por UF × ano × cultura × rótulo |
| `terrain_features(lat, lon, elevations)` | relevo de uma grade 3 × 3 de ±0,005° |
| `weather_window(start, end)` / `clip_to_coverage(...)` | janela de busca × janela real da vigência |
| `climate_features(days)` | indicadores do período |
| `build_features(policies, client)` | tudo junto, agrupando as chamadas de API |
| `save_dataset(frame, path)` | parquet ou CSV |

```bash
uv run --project .. python ../scripts/build_dataset.py            # 3.000 apólices
uv run --project .. python ../scripts/build_dataset.py --reiniciar
```

**Sem vazamento.** As chamadas buscam meses inteiros (para várias apólices dividirem a resposta),
mas `clip_to_coverage` recorta para a vigência exata **antes** de qualquer indicador ser calculado.
`tests/test_dataset.py::test_build_features_nao_usa_clima_posterior_ao_fim_da_vigencia` falha se
chuva posterior ao fim da vigência mexer em alguma feature.

**Retomável.** `build_features` chama `on_row` a cada linha pronta; o script grava em
`data/.dataset_parcial.jsonl` e pula na execução seguinte o que já existe.

## Modelo preditivo (D3)

`app/services/model.py` traz o baseline por regras, o treino, as métricas e o artefato.

| Função | O que faz |
|---|---|
| `baseline_score(df)` | score das regras de `regras-de-risco.md` sobre o dataset — os limiares são **importados** de `services/risk.py`, não copiados |
| `temporal_split(df)` | treino ≤ 2021 · validação 2022–2023 · teste 2024 |
| `train(df, target)` | logística e floresta, escolha pela AUC-PR de validação com **regra de um erro padrão** |
| `evaluate(y, score, t)` | AUC-ROC, AUC-PR, recall, precisão, matriz de confusão |
| `get_model()` / `predict_proba(...)` | o que a API usa |
| `train_optional_neural_model(...)` / `score_optional_neural_model(...)` | experimento MLP separado; não altera o modelo publicado nem as regras |

```bash
uv run --project .. python ../scripts/train_model.py
# experimento MLP opt-in, sem substituir o artefato publicado
uv run --project .. python ../scripts/train_model.py --somente-rede-opcional
```

O treino padrão não executa a MLP. Para comparar e salvar os dois artefatos na mesma execução,
use `--incluir-rede-opcional`. A rede tem artefato/metadados próprios
(`risk_neural_model_v1.*`); ausência do arquivo deixa o score neural indisponível e erros ao ler
um artefato presente são propagados.

O score MLP na API também é **opt-in**, sem alterar o default da rota de risco:

```text
GET /api/v1/farms/{farm_id}/risk?include_experimental_mlp=true
```

Com a opção ligada, a resposta informa `experimental_mlp.available`, a `version` do artefato e
uma `note`, além de `experimental_mlp_score` por dia. Esses campos são separados de
`model_probability` e `model_version`, usam as mesmas features de
`app/services/model_scoring.py::build_features` e não mudam nível, limite, recomendações, motivos
ou alertas operacionais. A nota deixa claro que o score **não é uma probabilidade calibrada**. Se
o artefato estiver ausente, a resposta indica indisponibilidade; corrupção do artefato e erro de
inferência são propagados como falhas HTTP, sem sucesso vazio. A trilha de auditoria registra o
valor da opção e, quando solicitados, os metadados e scores num bloco MLP separado.

A interface Streamlit oferece controle individual por sessão, desligado por padrão, e mostra o
score com a ressalva experimental. A chamada opt-in usa a mesma rota descrita acima; os campos
operacionais continuam vindo das regras e do modelo oficial.

**Resultado atual (21/09, com 2.256 linhas): o modelo supera o baseline** (AUC-PR de teste
**0,144 × 0,074**, diferença +0,070 · IC 95% [+0,003, +0,169]). Com três ressalvas: 26 sinistros no
teste (o mínimo declarado em `train_model.py` é 30), IC que quase toca o zero, e a virada veio do
**conjunto de teste** ter deixado de ser atípico, não de o modelo ter melhorado — o artefato de
20/09, sem retreino, também venceria no teste novo. As regras seguem comandando o alerta ao
operador (`regras-de-risco.md` §11). Números completos e a decomposição em
[dados-e-modelo.md](../document/dados-e-modelo.md#resultados-do-modelo-d3).

**Sem o artefato a API sobe igual:** `get_model()` devolve `None` com aviso no log e quem chama
segue só com as regras. O artefato versionado fica em `app/data/model/risk_model_v1.joblib` (7 KB)
com o JSON de métricas ao lado. **A API carrega, nunca treina.**

Desempenho medido: carregar leva **1 ms**; prever leva **7 ms na mediana** (p95 8,8 ms). A
**primeira** chamada custa ~50 ms, porque `sklearn` e `pandas` montam caches na estreia — por isso
existe `warm_up()`, para a W13 chamar no *lifespan* e tirar esse custo da primeira requisição real.

## Casos reais do replay (W9)

`app/data/replay_cases.json`: **5 acidentes noticiados** com máquina agrícola, cada um com data,
município, máquina, descrição e link da fonte. Validado por `tests/test_replay_cases.py`.

Nenhuma notícia trouxe coordenada, então todos têm `location_precision: "municipio"` e o ponto é a
**mediana das coordenadas reais das apólices do PSR naquele município** — um ponto agrícola de
verdade, em vez do centroide ou de um palpite. A precisão aparece na tela (W9).

Resultado preliminar do motor nesses casos: **2 de 5 teriam sido alertados no ponto**, 3 de 5 com
alguma célula da grade em alerta. A leitura caso a caso está em
[feature/W9-modo-replay.md](../feature/W9-modo-replay.md).

## Banco local (SQLite)

`app/db.py` cria a engine a partir de `AGRISHIELD_DATABASE_URL`
(padrão `sqlite:///./agrishield.db`, no `.gitignore`) e `create_db()` cria as tabelas com
`create_all` — sem Alembic (ADR-009). Mudou o esquema, apague o `.db` e recarregue.

## Cliente Open-Meteo (I1)

Todas as chamadas à Open-Meteo passam por `app/clients/open_meteo.py`. Nenhuma outra parte do código
fala com a internet.

| Método de `OpenMeteoClient` | O que faz | Cache |
|---|---|---|
| `fetch_elevations(lats, lons)` | Elevação de até **100 pontos** em 1 requisição (`ValueError` acima disso) | 24 h |
| `fetch_hourly_forecast(lat, lon, past_days=3, forecast_days=7)` | Previsão horária no fuso `America/Sao_Paulo` | 1 h |
| `fetch_hourly_history(lat, lon, start, end)` | Histórico: Historical Forecast API (≥ 2022) ou Archive/ERA5 (antes) | 7 dias |

- **Timeout:** 10 s. **Chave do cache:** URL + parâmetros ordenados.
- ***Stale-if-error*:** se a chamada falhar e existir resposta vencida no cache, ela é reaproveitada
  e fica um aviso no log. Sem nada no cache, o cliente levanta `WeatherUnavailableError`.
- **`WeatherUnavailableError` → 503:** `app/main.py` registra um `exception_handler` **global**
  (`weather_unavailable_handler`) que a converte em
  **503 `{"detail": "Serviço de clima indisponível"}`**. Nenhuma rota precisa de `try/except`.
- A Archive API não devolve `cape` (vira `None`) e chama a umidade do solo de
  `soil_moisture_0_to_7cm`; o schema `HourlyWeather` normaliza os dois nomes.
- Uma resposta **200 com conteúdo inválido** — inclusive séries horárias **desalinhadas** (dado
  parcial, uma variável menor que `time`) — vira `WeatherUnavailableError` e é removida do cache
  (`TTLCache.invalidate`), para não envenená-lo por 1 h ou 7 dias nem voltar depois como *stale*.
  O cliente nunca deixa vazar `ValidationError` do pydantic.

`app/services/weather.py` transforma as horas em dias (`aggregate_daily`), inclusive o
`rain_72h_mm` (chuva de d−2 + d−1 + d, somada **por data**: uma lacuna na série conta 0 mm no dia
que falta, em vez de puxar um dia mais antigo), conforme
[document/regras-de-risco.md §2](../document/regras-de-risco.md#2-clima-agregação-diária-i1).

### Testes sem internet

`tests/fixtures/open_meteo_*.json` guarda respostas **reais** da Open-Meteo (Carmo de Minas, MG,
capturadas em 19/09/2026). Os testes injetam um `httpx.MockTransport` no cliente, então `uv run
pytest` funciona offline.
O caso extremo de um `X-Request-ID` com 100 mil caracteres exercita diretamente o filtro da API,
sem depender do limite de tamanho de cabeçalho do sistema operacional.

Resultados da última validação local da API estão em
[document/evidencias/2026-09-25-validacao-geral.md](../document/evidencias/2026-09-25-validacao-geral.md).

### Testes de ponta a ponta (I6)

`tests/e2e/` é a exceção: esses testes **usam a rede**. Sobem a API num processo à parte (banco
SQLite temporário, prefixo MQTT exclusivo da execução), conectam no `broker.hivemq.com` e rodam o
simulador de dispositivo (`scripts/simulate_device.py`). Por isso ficam atrás da marca `e2e`, que o
`addopts = "-m 'not e2e'"` do `pyproject.toml` tira da execução padrão e da CI:

```bash
uv run pytest            # unitários (os e2e aparecem como "deselected")
uv run pytest -m e2e -s  # ponta a ponta, ~2 min, precisa de internet
```

O que eles cobrem: telemetria ponta a ponta com comparação campo a campo, rajada de 100 mensagens
(perda e duplicidade), payloads inválidos, capotamento (dedup + contexto + auditoria), queda do
dispositivo (LWT) e do broker, o `config` chegando ao equipamento (W4) e os tempos do painel (W5).
Relatórios e logs de cada execução em [`document/evidencias/`](../document/evidencias/).

## Fazendas de demonstração (W1)

O catálogo fica em `app/data/farms.json`, versionado no Git, e é lido **uma única vez** por
`app/services/farms.py` (`load_farms()`, com `lru_cache`). `app/main.py` chama essa função ao criar
o app: se o arquivo estiver faltando, malformado ou com um campo errado, a **API não sobe** e a
mensagem diz a posição da fazenda e o campo com problema.

| Fazenda (`id`) | Município | Cultura | Centro | Equipamento | Elevação na bbox (Copernicus DEM, 19/09/2026) |
|---|---|---|---|---|---|
| `cafe-carmo-de-minas` | Carmo de Minas, MG | café | `-22.12, -45.13` | `tractor-01` | 893–1011 m (amplitude 118 m) — **fazenda principal da demo** |
| `graos-sorriso` | Sorriso, MT | soja | `-12.60, -55.95` | `harvester-01` | 388–391 m (amplitude 3 m) — contraste plano, vira classe `flat` no W2 |
| `uva-serra-gaucha` | Bento Gonçalves, RS | uva | `-29.19, -51.57` | `tractor-02` | 476–550 m (amplitude 74 m) — segunda encosta |

Todas as bboxes são de ±0,005° em torno do centro (~1,1 km de lado, a grade 10 × 10 de
[regras-de-risco §1](../document/regras-de-risco.md#1-relevo-w2)). As amplitudes foram medidas com a
grade 10 × 10 da Open-Meteo Elevation API em 19/09/2026, nos **centros das células** da grade do
W2 (ver abaixo); a conferência das áreas no satélite é tarefa do time (T2).

Além do JSON, o service valida: `id` de fazenda e `device_id` únicos no catálogo inteiro (o
`device_id` é a chave dos tópicos MQTT), bbox com norte acima do sul e leste à direita do oeste,
centro dentro da bbox, pelo menos um equipamento por fazenda e **nenhum campo desconhecido**
(`extra="forbid"`, para pegar erro de digitação).

`find_device(device_id)` devolve `(Farm, Device)` e é o que o W4 vai usar para descobrir de qual
fazenda vem o clima do equipamento.

## Mapa de relevo (W2)

`app/services/terrain.py` transforma a bbox da fazenda em uma grade **10 × 10** com inclinação,
orientação e classe de terreno, seguindo
[regras-de-risco §1](../document/regras-de-risco.md#1-relevo-w2). Quase tudo são **funções puras**
sobre `numpy`; só `get_terrain` faz I/O (pelo cliente do I1).

| Função | O que faz |
|---|---|
| `build_grid(bbox, n=10)` | Centros das células: latitudes de **norte para sul**, longitudes de oeste para leste |
| `cell_size_m(bbox, n=10)` | `(dx_m, dy_m)`; `dx` encolhe com `cos(lat)` |
| `compute_slope_aspect(elev, dx_m, dy_m)` | `slope_deg` e `aspect_deg` (para onde a encosta desce, 0 = N) |
| `compute_tpi(elev)` | Elevação − média dos vizinhos (até 8; nas bordas, só os existentes) |
| `classify_cells(elev)` | `lowland` / `exposed` / `slope`, ou tudo `flat` se a amplitude for < 5 m |
| `aspect_label(deg)` | N, NE, L, SE, S, SO, O, NO (as fronteiras 22,5°, 67,5°… caem no setor seguinte) |
| `build_terrain(farm, elevations)` | Monta o `TerrainResponse` (função pura, sem rede) |
| `get_terrain(farm, client)` | Busca a elevação (1 requisição de 100 pontos) e guarda o mapa por 24 h |

- A bbox é dividida em `n × n` **células**, e cada ponto fica no **centro** da sua célula. Assim o
  passo é `0,001°` (~110 m de lado, a resolução do Copernicus GLO-90 citada no documento) e os
  polígonos ladrilham exatamente o retângulo da fazenda — o front só desenha o `polygon`
  (`[[lon, lat], …]`, formato do pydeck).
- As linhas vão de norte para sul, então `dz_dnorte = -dz_dlinha`. Um plano que sobe para o norte
  dá `aspect = 180°` (S), e um que sobe para leste dá `270°` (O).
- Duas camadas de cache de 24 h: a resposta da Elevation API (I1) e o `TerrainResponse` já montado
  (`clear_terrain_cache()` limpa a segunda nos testes).

Números das três fazendas (elevação real guardada em `tests/fixtures/terrain_elevation_*.json`).
**As fixtures foram capturadas na Open-Meteo Elevation API em 19/09/2026, amostrando os centros
das células da grade 10 × 10 (passo = lado da bbox ÷ 10), na ordem de `build_grid`: linha por
linha, de norte para sul, e dentro da linha de oeste para leste.** Mudou a convenção da grade
(`regras-de-risco §1`)? Recapture as fixtures, senão elas passam a descrever outra amostragem.

| Fazenda | Amplitude | Inclinação máx | Inclinação média | < 8° / 8–15° / ≥ 15° | Classes |
|---|---|---|---|---|---|
| `cafe-carmo-de-minas` | 118,0 m | **21,56°** | 9,73° | 37% / 46% / 17% | 25 `lowland`, 54 `slope`, 21 `exposed` |
| `graos-sorriso` | 3,0 m | 0,59° | 0,16° | 100% / 0% / 0% | 100 `flat` |
| `uva-serra-gaucha` | 74,0 m | 18,78° | 6,62° | 68% / 27% / 5% | 25 `lowland`, 54 `slope`, 21 `exposed` |

As faixas de `stats` usam os cortes do perfil de subscrição
([regras-de-risco §8](../document/regras-de-risco.md#8-perfil-de-subscrição-w8)), com o intervalo
do meio fechado à esquerda: `< 8°`, `[8°, 15°)` e `≥ 15°`.

## Motor de risco relevo × clima (W3)

`app/services/risk.py` cruza a grade de relevo (W2) com os indicadores diários do clima (I1) e
devolve o risco **de cada célula em cada dia**, seguindo
[regras-de-risco §3 a §6](../document/regras-de-risco.md#3-estado-do-solo). Tudo são funções
puras, menos `get_risk_forecast`, que junta as duas fontes.

| Função | O que faz |
|---|---|
| `soil_state(rain_72h_mm)` | `dry` < 10 mm ≤ `moist` < 30 mm ≤ `saturated` (§3) |
| `tilt_limit(l_ref_deg, soil)` | `floor_0.5(L_ref × fator)`, com piso de 0,5°; com L_ref 15°: 15,0° · 12,5° · 10,0° (§4) |
| `rollover_hazard(cell, day, ctx)` | Capotamento: `r = slope/L_dia`, 🟡 em 0,7 e 🔴 em 1,0 (§5.1) |
| `bogging_hazard(cell, day, ctx)` | Atolamento: baixada encharcada 🔴, baixada úmida ou chuva ≥ 50 mm 🟡 (§5.2) |
| `lightning_hazard(cell, day, ctx)` | Raio: tempestade + topo exposto 🔴, tempestade 🟡, CAPE ≥ 2000 em exposta 🟡 (§5.3) |
| `wind_hazard(cell, day, ctx)` | Vento: rajada ≥ 60 km/h em exposta 🔴; ≥ 60 nas demais ou 45–60 em exposta 🟡 (§5.4) |
| `fire_hazard(cell, day, ctx)` | Regra dos 30: 3 condições 🔴, 2 🟡 — e 🟡 vira 🔴 em encosta ≥ 15° (§5.5) |
| `farm_reference_tilt_limit_deg(farm)` | `L_ref` do mapa: o **menor** limite entre os equipamentos (§4, leitura conservadora) |
| `assess_cell` / `assess_day` / `assess_farm` | Pior nível, motivos acumulados e o resumo do dia (§6) |
| `select_days(daily, today, days)` | Só os dias de hoje em diante, no máximo 7 |
| `get_risk_forecast(farm, client, days, scenario)` | Relevo + previsão + cenário + motor |

**Motor extensível.** Cada perigo é uma função com a assinatura
`(cell, day, ctx) -> HazardResult | None`, registrada na lista `HAZARDS`. Devolver `None` significa
🟢. O nível da célula é o **pior** entre os perigos, com todos os motivos guardados em `reasons`.
Foi assim que a **W7 entrou depois da W3**: três funções novas na lista, três valores novos no
enum `Hazard` e dois no `Scenario`. A rota, o formato da resposta e o resto do motor não mudaram.
Hoje são cinco perigos: `rollover`, `bogging`, `lightning`, `wind` e `fire`.

**Um motivo sem número.** O raio por `weather_code` é a única exceção à regra de "todo motivo traz
números": ou o dia tem código de tempestade, ou não tem. O teste da rota abre essa exceção
explicitamente, em vez de afrouxar a regra para todos.

**Mensagens.** Cada célula 🟡 ou 🔴 explica o porquê com números, em português:
*"Inclinação de 13° acima do limite de 10° (solo encharcado: 42 mm em 72 h)."*

**Cenários simulados** (`app/services/scenarios.py`,
[§10](../document/regras-de-risco.md#10-cenários-simulados-demo)) alteram a previsão **antes** da
agregação diária, e nunca são o padrão. São três: `heavy_rain` (+45 mm das 12h
às 18h do dia +2, `weather_code` 63); `storm` e `heatwave` vêm no W7. Um valor fora da lista é
recusado com **422**. A resposta sempre traz o campo `scenario`, para o front avisar que o dado é
simulado.

```bash
curl "http://localhost:8000/api/v1/farms/cafe-carmo-de-minas/risk?days=7"
curl "http://localhost:8000/api/v1/farms/cafe-carmo-de-minas/risk?scenario=heavy_rain"
```

A resposta tem ~100 KB (7 dias × 100 células) e leva menos de 1 s sem cache.

## Ponte MQTT com o ESP32 (I2)

`app/mqtt/bridge.py` é o único ponto da API que fala MQTT. Contrato:
[document/contrato-mqtt.md](../document/contrato-mqtt.md) — **mudou lá, muda aqui e no firmware**.

- O `paho-mqtt` roda em **thread própria**, criada no `lifespan` e encerrada no shutdown.
- A ponte assina `{prefix}/devices/+/telemetry`, `.../+/events` e `.../+/status`, e publica
  `.../config` com **QoS 1 e retained** (`publish_config`), que é o que entrega o último limite ao
  ESP32 assim que ele conecta.
- **A API sobe mesmo com o broker fora do ar**: `connect_async` não bloqueia e o paho reconecta
  sozinho, com backoff de 1 s a 30 s.
- `handle_message(topic, payload)` é o coração testável, sem rede: extrai o `device_id` do tópico,
  valida com `app/schemas/mqtt.py`, deduplica e chama o callback.

O que é **descartado com log e sem derrubar a ponte**: tópico fora do contrato, JSON quebrado,
payload que não passa no schema, `device_id` do payload diferente do tópico (o broker é público) e
evento repetido.

**Payload vazio em tópico retained não é erro.** Apagar um retained em MQTT é publicar **zero
byte**; é o que o `--clean-retained` do simulador (I6) faz. Em `status` e `config` isso vira um
`debug` ("retained apagado"), não um WARNING — a trilha de rastreabilidade da I5 é evidência da
demo, e alarme falso ali atrapalha. Em `telemetry` e `events`, que nunca são retained, payload
vazio continua sendo aviso.

**Keepalive curto (15 s).** É o keepalive que descobre que a conexão morreu quando não chega mais
tráfego. Com o padrão do paho (60 s), medimos **57,9 s** entre derrubar o broker e a API perceber.
A detecção leva de **1× a 2× o keepalive**, então com 15 s o pior caso é **30 s**. Custa um
PINGREQ de 2 bytes a cada 15 s de silêncio (~6 KB/dia). Ajuste em `AGRISHIELD_MQTT_KEEPALIVE_S`.

**Deduplicação em duas camadas.** O ESP32 publica cada evento **3 vezes** com o mesmo `event_id`
(o PubSubClient só publica em QoS 0). A ponte lembra os últimos 500 `event_id`; a `UNIQUE` da
tabela `device_event` é a segunda trava, que continua valendo depois de um reinício da API.

**O que o firmware manda e o schema precisa aceitar** (verificado com payloads reais em
`tests/fixtures/telemetry_sample.json` e `tests/fixtures/rollover_event.json`):

| Situação | Como a API trata |
|---|---|
| `10.0` chega como `10` (ArduinoJson) | Campo `float` sem `StrictFloat`; o pydantic converte |
| `temp_c` / `humidity_pct` `null` (DHT22 falhou) | `float \| None` |
| `ts: 0` (NTP ainda não sincronizou) | Vale a hora de recepção (`resolve_timestamp`) |
| `fire_conditions` ausente | `int 0–3 \| None`; é um **piso**, não a contagem exata (E6) |
| `context` de 30 s em `rollover`/`incident_report` | Guardado inteiro em `payload_json` (~750 B a 1 KB) |
| Campo novo que a API não conhece | Ignorado (`extra="ignore"`), como manda o contrato |

## Persistência em SQLite (I3)

`app/models.py` e `app/repositories/devices.py`. O banco nasce no `lifespan` (`create_db()`), sem
migrações (ADR-009): mudou o esquema, apague o `.db` local.

| Tabela | O que guarda |
|---|---|
| `telemetry` | Uma linha a cada 5 s por equipamento, com índice em `(device_id, received_at)` |
| `device_event` | Um evento por `event_id` (**UNIQUE**), com o payload completo em `payload_json` |
| `device_status` | Uma linha por equipamento: `online`/`offline` e quando mudou |
| `published_config` | Todo `config` que a API publicou, para auditoria (I5) |

- **Retenção:** a telemetria com mais de **7 dias** é apagada quando a API sobe
  (`purge_old_telemetry`). Eventos, status e configs ficam: são poucos e são a memória do sinistro.
- **Fuso:** o SQLite não guarda fuso, então tudo é gravado em **UTC sem fuso** (`utc_naive`), e os
  filtros normalizam do mesmo jeito. `ts` é a hora do dispositivo e `received_at` a da API.
- A thread do MQTT abre a **própria sessão** por mensagem (`check_same_thread=False`), e uma falha
  de banco vira log, nunca a morte da ponte.

### Testar a ponte à mão

```bash
# 1. suba a API (ela conecta no broker e assina os tópicos)
cd api && uv run fastapi dev app/main.py

# 2. em outro terminal, faça o papel do ESP32
mosquitto_pub -h broker.hivemq.com -t 'agrishield/fiap-sompo-2026/devices/tractor-01/telemetry' \
  -f api/tests/fixtures/telemetry_sample.json

# 3. veja o que trafega no prefixo do projeto
mosquitto_sub -h broker.hivemq.com -t 'agrishield/fiap-sompo-2026/#' -v
```

Sem o `mosquitto-clients` instalado, dá para publicar com o próprio `paho` em três linhas de
Python. Para rodar a API **sem broker nenhum**, use `AGRISHIELD_MQTT_ENABLED=false`.

## Limite dinâmico do equipamento (W4)

`app/services/limits.py` calcula e `app/mqtt/publisher.py` envia. O cálculo é o mesmo
`regras-de-risco §4` do mapa; **o que muda é o `L_ref`**:

| Contexto | `L_ref` |
|---|---|
| Mapa da fazenda (W3) | o **menor** `base_tilt_limit_deg` entre os equipamentos dela (conservador) |
| Limite de um equipamento (W4) | o `base_tilt_limit_deg` **daquele** equipamento; sem ele, o `reference_tilt_limit_deg` da fazenda |

- `reason` sai **sem acento** (`strip_accents`), porque a fonte padrão do OLED não tem (E7):
  *"solo encharcado: 42 mm em 72 h"*.
- `valid_until` é a meia-noite que encerra o dia, no fuso das fazendas.
- Publicação em três momentos: **no boot**, **a cada 1 h**
  (`AGRISHIELD_MQTT_PUBLISH_INTERVAL_S`) e **sob demanda** pelo `POST .../limit/publish`.
- A tarefa periódica **não morre**: cada ciclo roda inteiro numa *thread* (cliente e sessão
  nascem lá dentro) e o laço captura qualquer exceção, deixando passar só o `CancelledError` do
  shutdown. Sem isso, um erro de banco encerraria a tarefa em silêncio e ninguém notaria que o
  limite parou de ser republicado.
- O `POST` confere a conexão MQTT **antes** de calcular: com o broker fora, ele responde na hora
  "Equipamento sem conexão MQTT" em vez de ir à Open-Meteo e talvez culpar o clima.
- **Para quem a tarefa periódica publica:** só para os equipamentos que **já se anunciaram** (têm
  linha em `device_status`). `config` é retained num broker público — publicar para o catálogo
  inteiro deixaria mensagem órfã em `harvester-01` e `tractor-02`, que não têm firmware. O
  equipamento novo recebe pelo envio manual e, daí em diante, entra sozinho na lista.

```bash
curl "http://localhost:8000/api/v1/devices/tractor-01/limit"
curl "http://localhost:8000/api/v1/devices/tractor-01/limit?scenario=heavy_rain"
curl -X POST "http://localhost:8000/api/v1/devices/tractor-01/limit/publish" \
  -H 'content-type: application/json' -d '{"scenario":"heavy_rain"}'
```

**503** quando a ponte MQTT está desconectada (`"Equipamento sem conexão MQTT"`) e **404** para
equipamento fora do catálogo ou data fora da janela da previsão.

## Painel do equipamento ao vivo (W5)

Rotas de leitura sobre o que a ponte gravou (I3), com as regras puras em `app/services/devices.py`.

| Rota | O que devolve |
|---|---|
| `GET .../status` | `state`, `reported_state`, `last_seen_at`, `seconds_since_last_telemetry` |
| `GET .../telemetry/latest` | A última leitura, ou **404 "Sem telemetria ainda"** |
| `GET .../telemetry?minutes=10` | Série cronológica, no máximo **300 pontos** (`sampled: true` se amostrada) |
| `GET .../events?limit=20` | Do mais recente para o mais antigo, com o `context` de 30 s quando houver |

- **Offline** é `offline` no LWT **ou** mais de **20 s** sem telemetria (o ESP32 publica a cada
  5 s, então 20 s aceitam três falhas seguidas antes de acusar queda).
- Equipamento que nunca falou **não é erro**: `state: "offline"`, `last_seen_at: null` e listas
  vazias — é com isso que o front escreve "Aguardando o equipamento conectar…".

## Histórico do equipamento — prévia do Passaporte Digital (W11)

`app/services/history.py`, sobre `telemetry` e `device_event`. **Nenhuma tabela nova**: tudo o que
um histórico precisa a I3 já gravava.

`GET /api/v1/devices/{device_id}/history?days=7` devolve o **acumulado do período** (leituras,
horas operando, inclinação máxima, % do tempo acima do limite, alertas, capotamentos, ocorrências
e limites aplicados) e a **linha do tempo** dos eventos, na mesma forma que o painel ao vivo usa.

Ele e o relatório do equipamento (W12) leem as mesmas linhas e **compartilham as funções**
(`operating_hours` e `pct_above_limit`, em `reports.py`), não só as constantes: a concordância
é **estrutural**, não verificada. O que muda é a pergunta — o W12 é a série por dia ("está
piorando?"), o W11 é o acumulado ("o que aconteceu com esta máquina?"). O teste que compara os
dois continua lá, agora como rede de segurança.

**O teto de 100 eventos é só da linha do tempo.** Os contadores do resumo saem de uma agregação
`GROUP BY type` sobre a janela inteira, em consulta separada. Não é preciosismo: a API republica
o `config` de hora em hora, então 7 dias já passam de 150 `limit_applied` — contar pela lista
truncada faria os totais pararem em 100 em silêncio e divergirem do W12, que conta tudo.

O campo `roadmap_note` diz o que isto **ainda não é**: score comportamental, portabilidade na
revenda e assinatura digital ficam no roadmap.

## Segurança e rastreabilidade (I5)

Requisito direto do enunciado da Sprint 4. Três peças:

### 1. Controle de acesso (ADR-013)

`app/core/security.py`. Chave no cabeçalho **`X-API-Key`**, obrigatória nos endpoints que escrevem
ou publicam — `POST /devices/{id}/limit/publish` e `GET /audit` — e nas leituras de fazendas,
equipamentos, relatórios e replay quando o ambiente não é `dev`. Leituras sem chave só ficam
públicas com `AGRISHIELD_ENVIRONMENT=dev` explícito; `/health` permanece público.

- Chaves em `AGRISHIELD_API_KEYS`, separadas por vírgula.
- Comparação em **tempo constante** (`secrets.compare_digest`, sobre bytes) e **sem sair do laço**
  na chave que casar.
- **Falha fechada**: sem `AGRISHIELD_API_KEYS` configurada, nenhuma chave é válida e toda escrita
  é recusada. Esquecer a variável não pode virar porta aberta.
- A mesma chave também é exigida para as leituras protegidas em produção.
- A chave é dependência **do decorador** da rota, então o FastAPI a resolve antes de qualquer
  parâmetro: sem chave, a requisição para ali, sem tocar broker nem Open-Meteo.
- 401 com a **mesma mensagem** para ausente, inválida e servidor sem chave, para não contar ao
  atacante em que pé está a configuração.

### 2. Log estruturado e `request_id`

`app/core/logging.py`. Cada requisição ganha um id (ou reaproveita o `X-Request-ID` que o cliente
mandou), que volta no cabeçalho da resposta, entra em toda linha de log daquela requisição e é
gravado na trilha. Com um id em mãos dá para reconstruir entrada, saída e decisão.

**O que nunca entra no log:** cabeçalhos, corpo e *query string*. É por construção, não por
descuido — é o que garante que uma chave não vaze. A tentativa recusada é registrada com a chave
**mascarada** (`****1234`). `httpx` e `httpcore`, que logam a URL inteira de cada chamada que
fazem, ficam em WARNING.

Corpo acima de 64 KB é recusado com **413** antes de qualquer processamento.

### 3. Trilha de auditoria

`app/models.py::DecisionLog` + `app/repositories/audit.py`. **Toda** decisão que vira score,
limite ou alerta gera uma linha:

| `decision_type` | Quando | `source` |
|---|---|---|
| `risk_score` | `GET /farms/{id}/risk` | `api` |
| `tilt_limit` | limite publicado no broker | `api` (botão) ou `scheduler` (ciclo de 1 h) |
| `alert` | `tilt_alert` ou `rollover` chegando do equipamento | `device` |
| `replay` | reservado para a W9 | — |

Cada linha traz `inputs_json`, `output_json`, `rule_version`, `model_version`, `source`,
`entity_id` e o `request_id` de origem (nulo no MQTT e na tarefa periódica).

- `rule_version` vem de `app/core/versions.py` e **é a versão do documento**
  `document/regras-de-risco.md`: `tests/test_versions.py` lê o título do documento e falha se os
  dois divergirem. `model_version` é `null` até a D3 existir — é a verdade, não um placeholder.
- No `risk_score`, a saída gravada é o **resumo por dia** (nível, limite, % da área). As 700
  células ficam de fora: a trilha não pode crescer mais rápido que o banco.
- Publicação recusada pelo broker **não** gera linha: a trilha só registra limite que saiu.

**Integridade** (`EventIntegrity`): o SHA-256 dos **bytes crus** de cada evento MQTT, calculado
antes de qualquer interpretação. É o que permite provar, num laudo de sinistro, que o evento
guardado é o que o equipamento publicou. Fica em tabela própria, e não como coluna de
`device_event`, porque o projeto não usa migrações (ADR-009) e acrescentar coluna a uma tabela
existente exigiria derrubar o `agrishield.db` — levando junto o `policy`, com 1,5 milhão de linhas.

```bash
curl -H "X-API-Key: $AGRISHIELD_API_KEYS" "http://localhost:8000/api/v1/audit?limit=5" | jq
curl -H "X-API-Key: $AGRISHIELD_API_KEYS" \
  "http://localhost:8000/api/v1/audit?entity=tractor-01&decision_type=tilt_limit" | jq
```

## Recomendações e janela segura (W6)

`app/services/recommendations.py`. Traduz o mapa em ação, para quem não vai interpretar cores.

| Função | O que faz |
|---|---|
| `is_safe_hour(rain, gust, code)` | Hora segura: chuva < 0,5 mm, rajada < 45 km/h e sem tempestade (§7) |
| `safe_windows(hourly, day)` | Sequências de horas seguras com **≥ 2 h**, entre 06h e 18h |
| `build_messages(day_risk, terrain)` | As frases, por **template com números** — nada de IA |
| `get_recommendations(farm, client, days, scenario)` | Hoje e amanhã, sobre o mesmo cálculo do mapa |

- **Indicador ausente conta como hora insegura.** Numa orientação que o operador vai seguir, falta
  de dado não pode virar permissão.
- **Janela segura ≠ área liberada.** Todo dia com célula 🔴 termina com a frase *"As janelas valem
  só para as áreas liberadas: as áreas em vermelho do mapa seguem proibidas mesmo dentro delas."*
  É o mal-entendido que provoca acidente, então está escrito, não subentendido.
- A direção da encosta é a **moda** do `aspect_label` das células 🔴 de capotamento; o empate é
  resolvido pela ordem do relevo, que é determinística.
- A frase do atolamento cita **as duas causas** quando as duas estão presentes (baixada encharcada
  e chuva do dia ≥ 50 mm).
- O mesmo `build_forecast_context` do mapa alimenta as duas rotas: a frase que o operador lê e a
  cor que ele vê **saem do mesmo cálculo**.

```bash
curl "http://localhost:8000/api/v1/farms/uva-serra-gaucha/recommendations" | jq
curl "http://localhost:8000/api/v1/farms/graos-sorriso/recommendations?scenario=heatwave" | jq
```

## Perfil de subscrição (W8)

`app/services/underwriting.py`, função pura sobre o relevo — **não depende da previsão**, porque é
a característica permanente do terreno, que é o que serve para precificar na cotação.

```
terrain_score = 100 − (1,0·pct_gt15 + 0,5·pct_8_15 + 0,5·pct_lowland + 0,3·pct_exposed)
```

limitado a [0, 100], com **A** (≥ 70), **B** (40–69) e **C** (< 40), conforme `regras-de-risco §8`.

⚠️ **Os pesos são v1 e arbitrários** — julgamento de engenharia, não ajuste em dado de sinistro. A
resposta carrega `weights_version` e `calibration_note` dizendo isso ao subscritor. Calibrá-los
com a base da Sompo é o primeiro item do roadmap de ML.

A carteira das três fazendas, com o relevo real:

| Classe | Score | Fazenda | O que mais pesa |
|---|---|---|---|
| **A** | 100,0 | Sorriso/MT (soja) | nada: 100% abaixo de 8° |
| **B** | 62,7 | Bento Gonçalves/RS (uva) | 27% de 8–15° · 25% em baixada · 21% em topo exposto |
| **B** | 41,2 | Carmo de Minas/MG (café) | 46% de 8–15° · 17% acima de 15° · 25% em baixada |

`L_ref` **não entra no score**: §8 calcula o perfil só a partir do relevo. Ele vai na resposta
como contexto, e o schema diz isso.

```bash
curl "http://localhost:8000/api/v1/farms/cafe-carmo-de-minas/underwriting" | jq
```

## Relatórios e tendências (W12)

`app/services/reports.py`. É o entregável 5 do enunciado: tendências **por equipamento, região ou
tipo de operação**. Cada relatório traz um campo `purpose`, dizendo para quem serve e que decisão
apoia — sem isso, relatório vira tabela bonita que ninguém sabe usar.

| Rota | Fonte | Para quem |
|---|---|---|
| `GET /reports/equipment/{device_id}?days=7` | telemetria e eventos (I3) | gestor de frota e manutenção |
| `GET /reports/region?state=MG&from_year=&to_year=` | **1,5 milhão de apólices reais** (D1) + relevo (W8) | analista da seguradora |
| `GET /reports/crop?from_year=&to_year=&state=` | as mesmas apólices, por cultura e causa | subscrição e produto; a cultura não representa o tipo de operação nem o risco de acidente de uma máquina |

Cada uma tem a gêmea `.csv`. **A exportação é para o Excel em português:** UTF-8 **com BOM** (sem
ele os acentos quebram), separador `;` e **vírgula decimal** — as duas últimas andam juntas.

Cuidados que valem mais que o código:

- **Índices.** Tudo filtra por `(state, policy_year)` ou agrupa por `event_category`, que são os
  índices da D1. `GET /reports/region?state=MG` responde em **0,19 s** sobre 1,5 milhão de linhas.
  O relatório por cultura levava 2,8 s por causa de um N+1 (uma consulta de causa por cultura) e
  caiu para **1,0 s** com um único `GROUP BY (crop, event_category)`.
- **LGPD.** Nenhuma consulta seleciona `proposal_id` — é chave de junção de volta ao CSV público
  do PSR, que traz o nome do segurado (ver o resíduo, acima). Um teste percorre os três
  relatórios serializados e falha se o campo aparecer.
- **`from_year`/`to_year`, não datas:** o grão do PSR é a safra, e é ela que está no índice.

```bash
curl "http://localhost:8000/api/v1/reports/region?state=RS" | jq '{policies, claims, claim_rate_pct}'
curl -o culturas.csv "http://localhost:8000/api/v1/reports/crop.csv"
```

## Score híbrido: regras + modelo (W13)

`app/services/model_scoring.py` liga o motor de risco ao modelo da D3. **O alerta ao operador
continua vindo só das regras**: nível, limite e motivos saem de `app/services/risk.py`, e nada do
modelo os altera. O modelo entra **ao lado**, como segunda leitura para a seguradora. Desde o
retreino de 21/09 ele **supera** o baseline no teste de 2024 (AUC-PR 0,144 × 0,074) e isso não
muda o desenho: o alerta segue nas regras por explicabilidade, por rodar offline no ESP32 e
porque a vitória é no alvo amplo "qualquer indenização", dominado por seca e geada, não no
encharcamento e na tempestade que o alerta trata (regras-de-risco §11).

Por dia, o `/risk` passa a trazer `model_probability`, `model_version` e `model_drivers`; e uma
vez por resposta, o bloco `model`, com a versão, as métricas do teste e a ressalva de leitura.

Três garantias que valem mais que o número:

- **O pipeline manda.** O `.joblib` e o `.json` são arquivos diferentes: um retreino interrompido
  deixa metadados novos com pipeline pela metade. Sem pipeline carregado, **não há bloco, não há
  campos e o `model_version` da trilha vem nulo** — os três coerentes entre si.
- **Nada de métrica no código.** Versão, AUC, importâncias e a própria frase da comparação saem
  do artefato em tempo de execução. Um retreino que mude o resultado inverte a frase sozinho — e
  o conectivo acompanha o ramo: perdendo, "então as regras continuam sendo a base do alerta";
  ganhando, a vitória é qualificada pelo alvo e a conclusão vem em frase própria, para o cartão
  nunca apresentar "as regras mandam" como consequência de o modelo ter ganho.
- **A ressalva viaja com o número.** O modelo foi treinado com uma linha por apólice/safra e aqui
  é aplicado a um dia: o valor serve para **comparar dias e fazendas**, não como probabilidade
  calibrada. Está escrito em `model.note`, com os números do próprio artefato.
- **A janela não é a única diferença: a resolução do relevo também muda.** O treino descreve cada
  apólice por uma grade **3 × 3** de ~370 m por célula (`services/dataset.py::terrain_features`) e
  a pontuação recalcula as mesmas variáveis sobre a grade **10 × 10** da fazenda (W2). A
  *definição* é a mesma dos dois lados — média da grade, orientação da célula central, % de
  baixada e de topo exposto, e o limiar de 72 h vindo de `services/risk.py` —, mas média e
  percentuais mudam de valor com o tamanho da célula. `tests/test_model_scoring.py` trava as
  definições comparando `build_features` com `dataset.terrain_features` sobre a mesma grade 3 × 3.

Custo: **72 ms** por requisição com o modelo, contra 24 ms sem (7 dias × 100 células, Open-Meteo
em cache). O `warm_up()` no lifespan tira da primeira requisição os ~50 ms de estreia do sklearn.

## Replay de acidentes reais (W9)

`app/services/replay.py` roda **o mesmo motor** sobre uma data passada e responde "o sistema
teria alertado?". Não há ramo especial para o passado — se houvesse, o replay não provaria nada.

| Rota | O que faz |
|---|---|
| `GET /api/v1/replay/cases` | Os casos curados, com fonte, data e precisão da coordenada |
| `POST /api/v1/replay` | `{case_id}` **ou** `{lat, lon, date}`. Data futura → 422 |
| `GET /api/v1/replay/summary` | O placar dos casos, com a ressalva colada ao número |

- **Fonte por data** (§9): a partir de 2022, Historical Forecast; antes, Archive (ERA5). Na
  Archive não há CAPE, então o raio vale só pelo `weather_code` — e a resposta **declara** isso
  em `limitations`.
- **Grade 3 × 3** (9 pontos de elevação), não 10 × 10: a Elevation API recusa chamada multiponto
  grande com 429, e foi assim que a cota do time caiu em 19/09.
- **A resposta traz a geometria** das células (`terrain`), na mesma forma de
  `GET /farms/{id}/terrain`: é o que deixa o front desenhar as células sobre o mapa reaproveitando
  o código que já tem, em vez de reproduzir a convenção de bbox que mora aqui.
- **O placar vem com o enquadramento.** `GET /replay/summary` conta os vereditos e devolve, junto,
  o texto dizendo que **cinco casos não são amostra** e que o número **não é taxa de acerto do
  produto**. Se um caso falhar, ele aparece em `failed_case_ids` — o placar não mente por omissão.
  Reaproveita o cache dos replays individuais, então é caro só na primeira vez: **aqueça antes da
  demo**.
- **A ressalva da coordenada.** Nenhuma notícia deu o ponto do acidente: todos os casos são
  `location_precision: "municipio"`, com o ponto na mediana das apólices do PSR daquele município.
  A resposta diz isso, porque "teríamos alertado" a quilômetros do local não se sustenta.
- **Resultado honesto: 2 de 5 no ponto, 3 de 5 na grade.** Os três "não" (incêndio por falha
  mecânica, capotamento em terreno plano e uma pastagem encharcada que a janela de 72 h não viu)
  valem mais no pitch do que cinco acertos escolhidos a dedo. Os dois placares (o da avaliação
  preliminar e o da API) estão comparados, com o que a convergência **não** prova, em
  [document/evidencias/replay-w9-comparacao-placares.md](../document/evidencias/replay-w9-comparacao-placares.md).
- A grade de 3 × 3 **joga contra** o placar: células de ~370 m suavizam mais o gradiente do DEM
  que as de ~110 m do mapa, então a inclinação sai menor e é mais difícil cruzar o limite de
  capotamento. Quem quisesse inflar o resultado usaria grade mais fina.

```bash
curl "http://localhost:8000/api/v1/replay/cases" | jq '.[] | {id, date, municipality}'
curl -X POST "http://localhost:8000/api/v1/replay" -H 'content-type: application/json' \
  -d '{"case_id":"imbituva-raio-lavoura-2026-08-31"}' | jq '{would_alert, verdict, limitations}'
```

## Endpoints

| Método | Rota | Status |
|---|---|---|
| GET | `/api/v1/health` | ✅ |
| GET | `/api/v1/farms` | ✅ |
| GET | `/api/v1/farms/{farm_id}` | ✅ |
| GET | `/api/v1/farms/{farm_id}/terrain` | ✅ |
| GET | `/api/v1/farms/{farm_id}/risk?days=7&scenario=` | ✅ (5 perigos) |
| GET | `/api/v1/farms/{farm_id}/recommendations?days=2&scenario=` | ✅ |
| GET | `/api/v1/farms/{farm_id}/underwriting` | ✅ |
| GET | `/api/v1/reports/equipment/{device_id}?days=7` (+ `.csv`) | ✅ |
| GET | `/api/v1/reports/region?state=&from_year=&to_year=` (+ `.csv`) | ✅ |
| GET | `/api/v1/reports/crop?from_year=&to_year=&state=` (+ `.csv`) | ✅ |
| GET | `/api/v1/devices/{device_id}/limit?date=&scenario=` | ✅ |
| POST | `/api/v1/devices/{device_id}/limit/publish` | ✅ (exige `X-API-Key`) |
| GET | `/api/v1/devices/{device_id}/status` | ✅ |
| GET | `/api/v1/devices/{device_id}/telemetry/latest` | ✅ |
| GET | `/api/v1/devices/{device_id}/telemetry?minutes=10` | ✅ |
| GET | `/api/v1/devices/{device_id}/events?limit=20` | ✅ |
| GET | `/api/v1/devices/{device_id}/history?days=7` | ✅ |
| GET | `/api/v1/replay/cases` | ✅ |
| GET | `/api/v1/replay/summary` | ✅ |
| POST | `/api/v1/replay` | ✅ |
| GET | `/api/v1/audit?entity=&decision_type=&limit=` | ✅ (exige `X-API-Key`) |

Os endpoints planejados estão em [document/arquitetura.md](../document/arquitetura.md#endpoints-da-api-v1).

# 21/09/2026 — Retomada da D2, correção dos scripts de banco e fechamento da W11 e da W13

> Sessão da madrugada de 21/09 (02:49 a 05:30 UTC). Cobre três coisas: a retomada do dataset da
> D2 depois que a cota da Open-Meteo virou o dia, um **bug real** que a retomada revelou nos
> scripts que falam com o banco, e a rodada de revisão que fechou a W11 e a W13.

## 1. A cota da Open-Meteo virou o dia

A [ficha de dados](../dados-e-modelo.md) registra que o reset é às **00:00 UTC** e que
a cota é **por subdomínio**. Conferido antes de gastar qualquer chamada do lote:

```bash
curl -s "https://archive-api.open-meteo.com/v1/archive?latitude=-25.4&longitude=-51.5\
&start_date=2018-01-01&end_date=2018-01-03&daily=precipitation_sum&timezone=UTC"
```

```json
{"latitude":-25.413006,"longitude":-51.484283,...,"daily":{"time":["2018-01-01","2018-01-02",
"2018-01-03"],"precipitation_sum":[6.30,42.70,2.10]}}
```

200 com dado, não `{"error":true,"reason":"Daily API request limit exceeded..."}`. O
`archive-api`, que tinha sido o subdomínio esgotado em 20/09, estava liberado.

## 2. Bug: os scripts de banco abriam um SQLite vazio quando rodados da raiz

A primeira tentativa de retomada morreu antes da primeira chamada de rede:

```
Retomando: 1184 linhas já prontas em .dataset_parcial.jsonl
sqlite3.OperationalError: no such table: policy
[SQL: SELECT policy.proposal_id, ... FROM policy WHERE policy.policy_year <= ?]
```

**Causa.** `Settings.database_url` tem como padrão um caminho **relativo**
(`sqlite:///./agrishield.db`, `api/app/core/config.py:34`), que o SQLAlchemy resolve contra o
**diretório de trabalho do processo**. `scripts/readme.md` manda rodar a partir da raiz
(`uv run --project api python scripts/build_dataset.py`), e nessa posição o SQLite abre — e
**cria** — um arquivo vazio em `./agrishield.db`, em vez do banco de 398 MB em
`api/agrishield.db`. Não era cota: era o script olhando para o banco errado.

Isso não é específico da D2. O `load_psr.py` (D1) tinha exatamente o mesmo defeito, com o agravante
de que ele **escreve**: rodado da raiz, ingeriria 1,5 milhão de apólices num banco que a API nunca
abriria.

**Correção.** Entrou `scripts/_bootstrap.py`, que os dois scripts passam a chamar no lugar do
`sys.path.insert` que faziam à mão:

```python
from _bootstrap import bootstrap  # noqa: E402  módulo vizinho, em scripts/

ROOT = bootstrap()
```

`bootstrap()` põe `api/` no `sys.path` e resolve `AGRISHIELD_DATABASE_URL` para o caminho
**absoluto** de `api/agrishield.db` **antes** de qualquer import de `app`. Usa `os.environ.setdefault`,
então quem exporta a variável continua escolhendo o banco — inclusive a CI e os testes.

Conferido depois da mudança:

```
$ uv run --project api python -c "...bootstrap()..."
sqlite:////home/ntl/workspace/SomposSprints/api/agrishield.db
```

O arquivo vazio criado por engano na raiz foi removido.

## 3. Retomada do dataset (D2)

```bash
uv run --project api python scripts/build_dataset.py -n 2500   # retoma nas 1.184 já prontas
```

A retomada funcionou como a feature promete: leu as 1.184 linhas do
`data/.dataset_parcial.jsonl`, pulou as apólices já resolvidas e foi buscar só as **1.316**
pendentes, sem regastar chamada nenhuma com o que já estava pronto.

**O que apareceu de novo: HTTP 429 em série, depois de ~2.100 chamadas.** O script reage com recuo
exponencial (15 s → 45 s → 120 s) e nova tentativa, e nenhuma linha se perde: o parcial é escrito a
cada linha pronta e a execução seguinte retoma de onde parou.

> ⚠️ **Cuidado ao ler o log: ele não diz qual 429 é qual.** A Open-Meteo recusa com 429 tanto por
> ritmo quanto por **cota diária esgotada**, e a diferença está no **corpo** da resposta
> (`{"error":true,"reason":"Daily API request limit exceeded. Please try again tomorrow."}`). O
> cliente chama `response.raise_for_status()` (`api/app/clients/open_meteo.py:196`) e o erro do
> httpx carrega só o **código** e a URL — o corpo nunca chega ao log. Procurar `Daily API request
> limit` no log e não achar **não** significa que a cota estava de pé; significa que o log não
> conta isso.
>
> **Foi a cota diária — confirmado.** Às 11:55 UTC, um `curl` avulso a cada um dos **três**
> subdomínios (`api.open-meteo.com`, `archive-api.open-meteo.com` e
> `historical-forecast-api.open-meteo.com`) devolveu 429 com o corpo do limite diário. Nenhum
> estava de pé.
>
> O motivo está na regra de preço da Open-Meteo: uma requisição que cobre mais de duas semanas
> conta como **várias** chamadas. A janela climática aqui é a vigência da apólice — mediana de 301
> dias —, o que dá ~21 chamadas por requisição de clima. As 1.040 requisições de clima de hoje
> valem, por essa conta, mais de 20 mil unidades contra um teto de 10 mil/dia. **O teto prático não
> é 10.000 apólices por dia, é ~465.** A conta completa, com a citação da página de preços e o que
> nela é inferência nossa, está em [dados-e-modelo.md](../dados-e-modelo.md).
>
> **Consequência prática:** o ritmo (120 req/min no histórico, 60 na elevação, contra os 600/min do
> plano) não era o gargalo, e baixá-lo não teria ajudado. O que resolve é **orçar por peso**:
> menos variáveis e janelas menores por chamada, ou aceitar duas sessões em dias diferentes — que
> foi, na prática, o que aconteceu.

A execução inteira, contada no log (salvo em
[logs/2026-09-21-build-dataset-d2.log](logs/2026-09-21-build-dataset-d2.log)):

| | |
|---|---|
| Requisições | 2.871 |
| Respostas 200 | 2.226 — 1.186 elevação · 656 archive · 384 historical |
| Respostas **429** | **644** — 450 no archive (41% de recusa) · 194 na elevação (14%) · nenhuma no historical |
| Respostas 503 | 1 |
| `Daily API request limit exceeded` | **0** |
| Esperas de recuo | 487 — 165 de 15 s · 161 de 45 s · 161 de 120 s |
| Descartes | 47 propriedades sem elevação · 112 janelas de clima recusadas |

**Como terminou.** Com o recuo já em 120 s e 141 recusas para cada 12 respostas boas nas últimas
200 linhas do log, o lote foi encerrado e o dataset **consolidado com o que estava pronto**:

```bash
uv run --project api python scripts/build_dataset.py -n 2500 --consolidar
```

```
Consolidando sem buscar nada: 2256 linhas prontas
data/dataset_treino.parquet: 2256 linhas
  target_claim: 390 positivos (17.3%)
  target_rain_claim: 94 positivos (4.2%)
```

**2.256 linhas de 2.500 pedidas — e acima das 2.000 do critério 1 da D2**, que era o que estava em
aberto desde 20/09. Consolidar em vez de insistir é o que a própria feature prevê: como a amostra
é embaralhada antes de ser processada, parar no meio dá um **subconjunto aleatório dos estratos**,
não um recorte enviesado. As 244 linhas que faltam para 2.500 continuam retomáveis com o mesmo
comando, sem `--consolidar`, quando a cota estiver livre.

A ficha completa — composição por ano, UF e cultura, features e chamadas consumidas — está em
[dados-e-modelo.md](../dados-e-modelo.md) e em `data/dataset_treino.json`, gerada pelo script.

> ⚠️ **Efeito colateral a ter em conta antes da demo:** ao fim desta sessão o subdomínio
> `api.open-meteo.com` (elevação **e** previsão) estava devolvendo 429 até para uma chamada
> avulsa. A API tem cache e *stale-if-error*, mas uma fazenda que nunca foi consultada não tem o
> que servir. **Não queime cota no dia da apresentação** — use `./scripts/run-demo.sh --aquecer`
> com antecedência.

## 4. W11 e W13: revisão, correção e aprovação

As duas estavam 🟦 "em revisão" desde 20/09, e a tabela ainda anotava "1 correção da API pendente"
na W13. Rodada completa do [fluxo de agentes](../fluxo-agentes.md): `revisor` → `dev-api` → `revisor`.

### Rodada 1 — o que o revisor achou

**W11 — 🔴 um defeito de correção.** `device_history` consultava os eventos com
`.limit(MAX_TIMELINE_EVENTS)` (100) e passava **a mesma lista truncada** para `summarize`, que
derivava dela `alerts`, `rollovers`, `incident_reports` e `limits_applied`. Acima de 100 eventos
na janela os contadores paravam **silenciosamente** em 100 no total, enquanto o relatório da W12
contava tudo. Não era hipótese: a API republica o `config` de hora em hora
(`mqtt_publish_interval_s = 3600`), o que sozinho passa de 150 `limit_applied` numa janela de 7
dias — ou seja, o número apareceria errado **na demo**, e o critério de aceite "o histórico
concorda com o relatório do equipamento" quebraria exatamente quando houvesse dado suficiente
para alguém reparar.

**W13 — 🟡 divergência entre treino e serviço (*skew*).** Três variáveis chegavam ao modelo
definidas de um jeito diferente do que ele viu no treino:

| Variável | No treino (`dataset.py`) | Na pontuação (`model_scoring.py`) |
|---|---|---|
| `aspect_label` | orientação da **célula central** | orientação **mais frequente** da grade |
| `elevation_mean_m` | `grid.mean()` — média das células | `(min + max) / 2` — ponto médio |
| `days_rain72h_ge30` | limiar do módulo de regras | literal `30.0` copiado |

Na grade de teste a diferença é mensurável: moda `SO` contra centro `O`; ponto médio 990,0 contra
média 992,8. O teste que deveria pegar isso (`test_the_features_cover_everything_the_model_declares`)
só conferia os **nomes** das variáveis.

### Rodada 2 — o que foi corrigido

- **W11:** `device_history` passou a fazer **duas** consultas — uma agregação
  `select(DeviceEvent.type, func.count()).group_by(...)` **sem teto** para os contadores e a lista
  com `.limit()` só para a linha do tempo. `summarize` agora recebe `event_counts: Mapping[str, int]`
  em vez da lista de eventos: a assinatura **força** a separação, em vez de depender de disciplina.
  Três testes novos, incluindo a concordância com a W12 **acima** do teto (240 eventos).
- **W13:** `_center_aspect_label` no lugar da moda, `_mean_elevation_m` com a média real das
  células, `SOIL_SATURATED_RAIN_72H_MM` importado de `app.services.risk` no lugar do literal, e um
  teste que compara `build_features` com `dataset.terrain_features` **na mesma grade** — travando a
  definição, que é o que estava solto.
- **Documentação:** a ressalva da W13 passou a dizer também que o relevo é recalculado em outra
  **resolução** (3 × 3 no treino, 10 × 10 na pontuação); `document/arquitetura.md` parou de afirmar
  que a W11 não foi implementada e a contagem de rotas subiu de 23 para **24**; `reports._pct_above_limit`
  virou público `pct_above_limit` e a W11 passou a chamá-lo, fechando a última fórmula duplicada
  entre as duas features.

### Veredito

**W11 ✅ APROVADO · W13 ✅ APROVADO.** `ruff check`, `ruff format --check` e `pytest` verdes:
**834 testes na api** no momento da aprovação (eram 828; +6 dos casos novos) e **149 no
front-web**. Ao fim do dia são **835**: o teste espelho do ramo vencedor da nota do modelo entrou
depois, junto da correção da §5.

O que ficou de fora virou linha em [backlog-pos-entrega.md](../backlog-pos-entrega.md): igualar a
**resolução** das grades (não só a definição) depende de regerar o dataset da D2, e a divergência
que sobra está declarada na docstring e no `api/README.md`.

## 5. Retreino do modelo (D3) — e a conclusão que virou

Com 2.256 linhas, `scripts/train_model.py` foi rodado de novo e **inverteu o resultado publicado em
20/09**: no teste de 2024 o modelo passou a superar o baseline por regras em AUC-PR (**0,144 ×
0,074**, diferença +0,070, IC 95% [+0,003, +0,169], P = 0,98). Até 20/09 o documento afirmava, com
números, o contrário.

Uma inversão dessas é exatamente o tipo de resultado que merece desconfiança antes de virar texto,
então ela foi **auditada antes de ser publicada**. O que a auditoria achou:

- **Não houve mudança de código.** `git diff` vazio no motor de regras, no treino e na geração do
  dataset; as 1.184 linhas antigas são subconjunto estrito das 2.256 e o `baseline_score` delas é
  idêntico. Toda a variação vem da **composição do conjunto de teste**.
- **O modelo de 20/09, sem retreino nenhum, já venceria no teste novo** (+0,059). Decompondo o
  ganho total de +0,088: a troca do conjunto de teste responde por ~+0,08 e o retreino com o dobro
  de linhas por ~+0,01. Ou seja, **a virada é do teste, não do modelo**.
- **O teste antigo é que era atípico.** A taxa de sinistro do PSR em 2024 é de 9,46%; a amostra de
  20/09 tinha 5,16% (8 em 155) e a atual tem 8,52% (26 em 305). O desvio médio por safra contra a
  população caiu de 2,80 pp para 0,90 pp — a amostra completada é cerca de **três vezes mais fiel**.
- **E o achado que mais importa para o pitch:** o baseline por regras nunca discriminou o alvo
  `target_claim`, em safra nenhuma (AUC-ROC de 0,370 a 0,613 nas nove, todas com 0,5 dentro do
  intervalo de confiança). O motivo é conceitual: as regras modelam **chuva, encharcamento e
  tempestade**, e `target_claim` é dominado por **seca e geada** — em 2024, 74,4% das indenizações
  foram por seca. Medido contra `target_rain_claim`, que é o perigo que as regras realmente tratam,
  o baseline **discrimina** (AUC-ROC 0,625 no dataset inteiro, 0,669 no teste).

A conclusão publicada, portanto, é "supera — com três ressalvas no mesmo parágrafo": 26 positivos
está **abaixo** do mínimo de 30 que o próprio script exige para conclusão firme, o extremo inferior
do IC quase toca o zero, e as duas metades do mesmo ano de teste dão vereditos opostos. O texto
completo, com as tabelas, está em
[dados-e-modelo.md](../dados-e-modelo.md) — esta nota registra só por que ele foi reescrito.

**O que a inversão não muda:** o alerta ao operador continua vindo das regras. Isso é decisão de
produto da W13 (explicabilidade e funcionamento offline), reafirmada em
[regras-de-risco.md §11](../regras-de-risco.md), e agora tem um terceiro motivo — o modelo vence
num alvo **mais amplo** do que o perigo que o alerta trata.

## 6. O que esta sessão **não** resolveu

- **`api/agrishield.db-shm` e `api/agrishield.db-wal` estão versionados** (entraram no commit
  `43ba121`). O `.gitignore` ganhou `*.db-shm` e `*.db-wal`, mas ignorar não desrastreia: falta
  `git rm --cached` nos dois.

  **Não houve vazamento**, e vale registrar com número em vez de deixar o susto no ar: o blob do
  `-wal` em `HEAD` tem **0 byte** e o do `-shm` tem 32 KB **sem uma única cadeia de texto
  extraível** (`git cat-file -p HEAD:api/agrishield.db-shm | strings -n 6` não devolve nada). O
  `-wal` é o *write-ahead log*, onde ficam as escritas recentes antes de assentarem no `.db` —
  então o risco é **estrutural** (num commit feito com a API escrevendo, ele carregaria telemetria),
  não realizado neste caso. Corrigir continua valendo: o próximo commit pode não ter a mesma sorte.
- **`document/user-stories.md` (US-04, US-05 e US-08) e `document/entregaveis.md`** ainda descrevem
  a W11 como não implementada, a D2 como parcial, repetem "23 rotas" e — o mais grave agora —
  afirmam que o modelo **não supera** o baseline. É o documento de entrega contradizendo o código:
  precisa entrar no passe do `doc-entrega` antes do congelamento de 25/09.
  **→ Resolvido ainda em 21/09**, no passe de consistência do `doc-entrega`: US-03, US-04, US-05 e
  US-08 e a matriz de rastreabilidade foram reescritas contra o código, a contagem virou 24 rotas
  e a afirmação sobre o modelo foi corrigida — **com as ressalvas junto** — em `demo.md`,
  `plano-de-implementacao.md`, `arquitetura.md`, `entregaveis.md`, `roteiro-video.md`,
  `user-stories.md`, `prints/README.md` e `feature/W13-score-hibrido.md`.
- **Critério 7 da US-03** (mapa em < 3 s sem cache, < 200 ms com cache) segue sem medição. A
  tentativa desta sessão esbarrou no 429: medir tempo de resposta com a Open-Meteo recusando
  mediria a recusa, não o mapa.
- **Prints (entregável 5)** e o **ensaio no Wokwi** (E1–E8, I4) continuam pendentes, como em 20/09.
  O print nº 11 (cartão do score híbrido) **só pode ser capturado agora**: o cartão lê versão e
  métricas do artefato em tempo de execução, e o artefato mudou hoje.
- **As 244 linhas que faltam para as 2.500 pedidas.** O comando retoma sozinho quando a cota
  estiver livre — mas aí o quadro de métricas da D3 precisa ser refeito junto, e os prints
  refeitos com ele. Completar depois de 25/09 não compensa o retrabalho.

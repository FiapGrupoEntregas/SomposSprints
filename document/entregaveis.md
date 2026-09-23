# Entregáveis do Challenge Sompo — Sprint 4

Checklist do enunciado. Mantido pelo agente `doc-entrega`. **Situação real, sem maquiagem.**

> **Conferido em 21/09/2026**, lendo o código, e não o plano — depois das entregas de W8, W9, W12,
> W13, D3 e I4, e da rodada de 21/09 que aprovou **W11** e **W13**, fechou a **D2** em 2.256
> linhas e **retreinou o modelo** (resultado invertido: ver o entregável 2).
>
> **Contagem de testes: de propósito, não há total fixado aqui.** O número cresceu várias vezes
> só nesta semana, e um total escrito num documento envelhece em horas. Reconte no fechamento com
> `uv run pytest --collect-only` na `api/` e no `front-web/` e registre a data ao lado. Quando um
> número aparece junto de um arquivo, ele conta **funções `def test_`** daquele arquivo; com
> parametrização o total coletado é maior.
>
> O código congela em 25/09, então esta tabela **será refeita** no fechamento.

Legenda: ⬜ a fazer · 🟨 em andamento · ⚠️ parcial · ✅ pronto com evidência

| # | Entregável | Situação | Onde está |
|---|---|---|---|
| 1 | Sistema consolidado: código modular, exceções tratadas, fluxo ponta a ponta reproduzível | ⚠️ software pronto; falta ensaio com Wokwi e macOS | `api/`, `front-web/`, `iot/` — [detalhe](#1-sistema-consolidado) |
| 2 | Banco de dados e modelo final, com métricas e justificativa | ✅ | `api/app/models.py`, `api/app/data/model/`, `scripts/train_model.py`, [dados-e-modelo.md](dados-e-modelo.md) — [detalhe](#2-banco-de-dados-e-modelo-final) |
| 3 | Validação da integração (confiabilidade da coleta e consistência) | ✅ | I6 · `api/tests/e2e/`, `scripts/simulate_device.py`, [evidencias/](evidencias/) — [detalhe](#3-validação-da-integração) |
| 4 | Segurança e rastreabilidade (acesso, proteção de dados, registros de uso) | ✅ | I5 · `api/app/core/security.py`, `logging.py`, `versions.py`, `repositories/audit.py` — [detalhe](#4-segurança-e-rastreabilidade) |
| 5 | Relatórios e interface (scores, tendências, alertas) com prints | ⚠️ **as 6 telas existem; nenhum print foi capturado** | `front-web/views/` — [detalhe](#5-relatórios-e-interface) |
| 6 | Diagrama de arquitetura final (entrada → banco → modelo → saída) | ✅ | [arquitetura.md](arquitetura.md#diagrama-final-entrada--banco--modelo--saída) — [detalhe](#6-diagrama-de-arquitetura-final) |
| 7 | Vídeo de até 5 min, narração humana, YouTube "não listado" | 🟨 | Roteiro em [roteiro-video.md](roteiro-video.md); gravação pendente |
| 8 | README final com evolução das 4 sprints e link do vídeo | ⚠️ | `README.md` — [detalhe](#8-readme-final) |
| 9 | Repositório **privado** compartilhado com `fiap-tutoria` | ⬜ | `github.com/FiapGrupoEntregas/SomposSprints` → Settings → Collaborators |

O enunciado também cobra **aderência às User Stories escolhidas pelo grupo**. As histórias dos quatro
perfis e a matriz de rastreabilidade (história → feature → arquivo → evidência → situação) estão em
[user-stories.md](user-stories.md), escritas na DOC1 e consolidadas na Sprint 4 — o grupo não tem
registro das histórias das sprints anteriores, e isso está declarado no próprio documento.

## Detalhe por entregável

### 1. Sistema consolidado

**O que já existe**

- **API modular** em quatro camadas, sem lógica nas rotas: `api/app/api/v1/routes/` (6 arquivos:
  `health`, `farms`, `devices`, `reports`, `replay`, `audit`) → `app/services/` (16 módulos:
  `terrain`, `weather`, `risk`, `limits`, `devices`, `farms`, `scenarios`, `dataset`,
  `psr_ingest`, `recommendations`, `underwriting`, `replay`, `reports`, `model`,
  `model_scoring`, `history`) → `app/clients/open_meteo.py` e `app/repositories/` → `app/db.py`.
  A ponte MQTT (`app/mqtt/`) roda no `lifespan`.
- **24 rotas registradas** em `app/api/v1/router.py` (health 1 · fazenda 6 · equipamento 7,
  incluindo o histórico da W11 · relatórios 6, contando os `.csv` · replay 3 · auditoria 1).
  Confira com `grep -c @router api/app/api/v1/routes/*.py`. A lista completa, com o status de
  cada uma, está em [arquitetura.md](arquitetura.md#endpoints-da-api-v1).
- **Tratamento de exceções com comportamento declarado**: Open-Meteo fora do ar ou em `429` →
  *stale-if-error* pelo cache e, sem cache, `503` com mensagem em português; broker fora do ar → a
  API **sobe do mesmo jeito** (testado em `api/tests/test_app_lifespan.py`); payload MQTT fora do
  contrato → descartado com log, sem derrubar a ponte; front sem API → mensagem de erro na tela, não
  *traceback* (`front-web/tests/test_pages.py::test_page_shows_error_when_api_is_down`).
- **Firmware completo, E1 a E8**, em `iot/src/main.ino`, compilando com `pio run`, sem `delay()` no
  `loop()`.
- **Suíte de testes verde na API e no front**, toda offline (Open-Meteo e MQTT mockados), mais os
  testes `e2e` com broker real fora da CI. Total: reconte no fechamento (ver a nota do topo).
- **Qualidade conferida em 20/09:** `ruff check` e `ruff format --check` limpos na `api/` e no
  `front-web/`, `pytest` verde nos dois, `pio run` com **SUCCESS** no firmware.

**O que falta para virar ✅ — e nada disso é código**

- **I4** — software pronto, ensaio humano pendente: `scripts/run-demo.sh` sobe API e front com um
  comando (confere as duas chaves, espera o `/health`, aquece o cache e derruba os dois com um
  Ctrl+C) e o **teste de clone limpo levou 14 s**, contra o teto de 10 min
  ([evidência](evidencias/2026-09-20-ambiente-de-demo.md)). Falta o ensaio completo com o Wokwi
  (2×), rodar o script **no macOS** e o vídeo de backup (T5).
- Wokwi: o firmware compila, mas **não há captura da simulação** provando os ângulos (tarefa T6).
  É a única evidência que falta para as oito features do dispositivo saírem de "em revisão".

**Resolvido em 20/09 — registrado aqui porque estava listado como pendência:**

- ✅ **Chave de API no front.** A pegadinha do passo 3 da demo (o front não carregava
  `front-web/.env` e o botão "Enviar ao equipamento" devolvia 401) **foi corrigida**:
  `front-web/pyproject.toml` passou a trazer `python-dotenv` e `front-web/config.py` faz
  `load_dotenv` do `.env` ao lado do próprio arquivo, com a variável de ambiente tendo precedência
  sobre o `.env` — que é do que o `run-demo.sh` depende para repassar a configuração aos dois
  processos.

### 2. Banco de dados e modelo final

**Banco — ✅ do lado da estrutura.** SQLite via SQLModel, 7 tabelas em `api/app/models.py`:

| Tabela | Para quê | Feature |
|---|---|---|
| `policy` | **1.525.473 apólices reais** do PSR/SISSER (2006–2025), anonimizadas | D1 |
| `telemetry` | leituras do equipamento, com retenção de 7 dias | I3 / E4 |
| `device_event` | eventos (`tilt_alert`, `rollover`, `incident_report`), deduplicados por `event_id` | I3 / E2, E5, E8 |
| `device_status` | último estado de conexão (LWT) | I3 |
| `published_config` | todo `config` que a API publicou | I3 / W4 |
| `decision_log` | **toda decisão**, com entrada, saída e versão de regra/modelo | I5 |
| `event_integrity` | hash SHA-256 do payload cru de cada evento | I5 |

**Pipeline — ✅ de ponta a ponta, com uma limitação declarada.**
`scripts/download_psr.py` → `load_psr.py` (D1) → `build_dataset.py` (D2, **retomável**) →
`train_model.py` (D3) → artefato em `api/app/data/model/`, carregado pela API. Os quatro passos
rodam e estão testados.

A limitação, atualizada em 21/09: o dataset fechou com **2.256 linhas** — **acima das 2.000 do
critério da D2**, e 244 abaixo das 2.500 pedidas. O lote foi encerrado quando a **cota diária** da
Open-Meteo se esgotou — confirmado às 11:55 UTC por `curl` nos três subdomínios, todos com
`Daily API request limit exceeded` no corpo — e consolidado com o que já estava pronto. A causa de
fundo é o **peso** da chamada, não o ritmo: requisição de mais de duas semanas conta como várias, e
a janela aqui é a vigência da apólice (~21 chamadas por requisição de clima), o que baixa o teto
prático para ~465 apólices por dia. Como a amostra é embaralhada antes de ser processada, parar no
meio dá um subconjunto aleatório dos estratos, não um recorte enviesado. A ficha `data/dataset_treino.json` registra o pedido e o
obtido, e [dados-e-modelo.md](dados-e-modelo.md#dataset-de-treino-d2) traz a composição. Completar
as 244 **não** exige refazer nada — mas obriga a refazer o quadro de métricas da D3 e os prints
junto (ver [evidência de 21/09](evidencias/2026-09-21-retomada-d2-e-fechamento-w11-w13.md)).

**Modelo — ✅ treinado, versionado e com métricas publicadas. Desde o retreino de 21/09 ele
supera o baseline por regras, e as ressalvas vão junto.**

- O artefato está em `api/app/data/model/risk_model_v1.joblib`, com o `.json` de métricas ao lado,
  **versionados no Git**. A API **carrega, nunca treina** (`app/services/model.py`), e sobe
  normalmente se o artefato não estiver lá — nesse caso os campos do modelo saem `null`.
- **O resultado honesto, que precisa aparecer em todo lugar onde o modelo for citado:** com a D2
  fechada em 2.256 linhas e o modelo retreinado em 21/09, o **modelo passou à frente do baseline**
  no teste de 2024 (em 20/09 era o contrário). A afirmação só vale com três ressalvas, e elas não
  se separam dela: **(1)** o teste tem **26 positivos**, abaixo do mínimo de 30 que o próprio
  `train_model.py` exige para conclusão firme; **(2)** o extremo inferior do IC 95% da diferença
  quase toca o zero; **(3)** a virada veio da **troca do conjunto de teste**, não de o modelo ter
  melhorado — auditado com o artefato antigo, que sem retreino nenhum já venceria no teste novo.
  Soma-se a isso que o baseline **nunca discriminou** o alvo `target_claim` em safra nenhuma,
  porque as regras modelam chuva e tempestade e esse alvo é dominado por seca e geada; no alvo que
  as regras tratam (`target_rain_claim`) quem discrimina é o baseline. Números, intervalo de
  confiança e a auditoria completa em
  [dados-e-modelo.md](dados-e-modelo.md#resultados-do-modelo-d3).
- **Não escreva a métrica aqui.** O modelo já foi retreinado uma vez (21/09) e pode ser de novo se
  as 244 linhas forem completadas; número copiado para dentro de documento envelhece calado — foi
  exatamente o que aconteceu com a frase "não supera o baseline", espalhada por oito documentos
  que precisaram ser corrigidos em 21/09. A API monta a ressalva **a partir do próprio artefato**
  em tempo de execução, e o cartão do front a exibe por inteiro: se um retreino inverter o
  resultado, a frase se inverte sozinha. O único lugar com a tabela de métricas é o
  `dados-e-modelo.md`, e lá ela vem com a data da medição.
- **Quem decide o alerta ao operador continua sendo a regra explicável** (`regras-de-risco/v1`).
  O modelo entra ao lado, como segunda leitura para a seguradora (W13).

> **Sobre `MODEL_VERSION = None` em `api/app/core/versions.py`:** está **certo por design** e não
> deve ser "corrigido". Essa constante é a versão usada quando a decisão foi tomada **sem**
> modelo — o caso das regras puras. A versão real do modelo viaja com a decisão, lida do artefato
> em tempo de execução, porque muda a cada retreino e não pode virar constante no código.

**Justificativa dos ajustes — ✅.** Está escrita e com número: exclusão de 2025 (censura, 46.137
apólices), exclusão de vigência < 30 dias (430.917), o achado de que **todas** as 430.843 apólices do
CSV de 2006–2015 têm início igual ao fim, o agrupamento de chamadas por célula de 0,25° e a medição
que derrubou o multiponto da Elevation API. Tudo em
[dados-e-modelo.md](dados-e-modelo.md#dataset-de-treino-d2).

### 3. Validação da integração

- **Existe:** testes automatizados por camada (API + front), com fixtures de respostas reais da
  Open-Meteo, teste de rajada e de deduplicação na ponte MQTT
  (`api/tests/test_mqtt_bridge.py`, 26 testes), teste de que a API sobe sem broker e teste de que o
  front não chama a Open-Meteo.
- **E agora também o fluxo completo (I6):** `scripts/simulate_device.py` publica no contrato do
  firmware e os 15 testes de `api/tests/e2e/` (marca `e2e`, fora da CI) sobem a API de verdade e
  falam com o `broker.hivemq.com`. Execução de 20/09/2026, tudo verde: **100/100 mensagens** numa
  rajada (sem perda nem duplicidade), 11 payloads inválidos descartados sem derrubar a ponte,
  capotamento gravado **uma vez** com os 30 s de contexto, hash e trilha de auditoria, LWT
  `offline` e volta da coleta em 1,32 s, `config` no equipamento em 0,307 s (W4) e nível novo
  disponível ao painel em 0,392 s (W5). Relatório com a saída real em
  [evidencias/2026-09-20-integracao-ponta-a-ponta.md](evidencias/2026-09-20-integracao-ponta-a-ponta.md).
- **Ressalva levantada na validação e já corrigida:** a execução de 20/09 mediu ~58 s para a API
  perceber a queda do broker, porque valia o keepalive padrão do paho (60 s). O `dev-api`
  corrigiu no mesmo dia — `mqtt_keepalive_s = 15` em `app/core/config.py`, passado ao
  `connect_async` —, o que leva a detecção para a faixa de 15 a 30 s (o protocolo detecta entre
  1× e 2× o keepalive). A API sempre se recuperou sozinha; o que mudou foi o tempo até ela
  perceber. O registro da medição e da correção está no próprio relatório de evidência.
- **Falta:** captura do Wokwi e prints das telas (verificação manual, T6 e DOC2).

### 4. Segurança e rastreabilidade

Implementado e testado (I5, aprovado em revisão):

- **Controle de acesso:** `X-API-Key` nos endpoints de escrita/publicação e na auditoria, com
  comparação em tempo constante, **falha fechada** (sem chave configurada, nada passa) e chave
  mascarada no log. `api/app/core/security.py` · `tests/test_security.py` (14 testes).
- **Proteção de dados (LGPD, ADR-011):** nome e documento do segurado são descartados na leitura do
  CSV; há teste que falha se qualquer coluna pessoal chegar ao banco
  (`test_colunas_pessoais_nunca_chegam_ao_banco`, em `tests/test_psr_ingest.py`).
- **Registros de uso:** log estruturado em JSON com `request_id`, e `X-Request-ID` em toda resposta
  (`app/core/logging.py`, 14 testes); `decision_log` com entrada, saída e **versão da regra**
  (`app/core/versions.py`, conferida contra o título de `regras-de-risco.md` por teste);
  `event_integrity` com o SHA-256 do payload cru; endpoint `GET /api/v1/audit` protegido
  (`tests/test_audit.py`, 15 testes).

**Onde entra a versão do modelo.** Desde a W13, a decisão de **score de risco** grava no
`decision_log` a versão da regra **e** a do modelo, lida do artefato. As decisões que não usam
modelo — limite publicado e alerta do equipamento — continuam gravando `model_version` como
`null`, e isso é o comportamento correto: a coluna diz *com o que aquela decisão foi tomada*, não
*o que existe no projeto*.

**Ressalva honesta:** falta um print da consulta de auditoria para a entrega visual (item 8 da
lista de prints).

### 5. Relatórios e interface

| Tela | Situação | Onde |
|---|---|---|
| Home (seletor de fazenda compartilhado) | ✅ | `front-web/views/home.py` |
| Mapa de risco · aba **Relevo** (grade 10×10, inclinação, classes, KPIs) | ✅ | `views/risk_map.py` |
| Mapa de risco · aba **Previsão de risco** (7 dias, células por nível, chuva, cenário simulado sinalizado) | ✅ | `views/risk_map.py` |
| Equipamento ao vivo (limite do dia, envio ao ESP32, status, gráfico de 10 min, eventos, alerta de capotamento) | ✅ | `views/equipment.py` |
| Subscrição (selo A/B/C, score 0–100, indicadores do relevo, drivers e carteira ordenada) | ✅ | `views/underwriting.py` · W8 |
| Replay de acidentes reais (5 casos com fonte, veredito no ponto e na grade, limitações à vista) | ✅ | `views/replay.py` · W9 |
| **Relatórios: tendências por equipamento / região / cultura**, com download em CSV | ✅ | `views/reports.py` · W12 |
| Score híbrido (nível por regra + probabilidade do modelo) e explicabilidade | ✅ | cartão em `views/risk_map.py` · W13 |

São **seis telas**, todas cobertas por testes de front com a API mockada.

**O que falta é só a captura: `document/evidencias/prints/` não tem nenhuma imagem.** Este é hoje
o maior risco de perder nota da entrega inteira — não porque falte software, mas porque o
enunciado cobra **prints** de scores, tendências e alertas, e a pasta está vazia. A lista do que
capturar está em [evidencias/prints/README.md](evidencias/prints/README.md), e **os três itens que
o enunciado cobra mais diretamente (subscrição, tendências e score híbrido) já são capturáveis**.

### 6. Diagrama de arquitetura final

O diagrama **entrada → banco → modelo → saída** está em
[arquitetura.md](arquitetura.md#diagrama-final-entrada--banco--modelo--saída) e cobre a camada de
dados do PSR, a ponte MQTT, as 7 tabelas do SQLite, o modelo da D3, o score híbrido e os 8 blocos
do firmware.

Foi **reconferido contra o código em 21/09**, depois das entregas de D3, W8, W9, W12, W13 e da
aprovação da **W11**. O histórico do equipamento entrou no desenho (bloco de saída) quando a W11
foi aprovada. Hoje **não há nenhuma caixa tracejada**: o que não faz parte do sistema — o alerta
pelo Telegram (**W10**, decidida **fora do escopo** por exigir token de bot) e o **SoilGrids**, que
nunca saiu do papel — simplesmente não aparece no desenho, para não dar a entender que existe. A
regra de leitura está escrita no próprio documento.

**Reconferir uma última vez depois do congelamento em 25/09**, que é quando o desenho passa a ser
definitivo.

### 8. README final

O `README.md` da raiz já tem descrição do problema, funcionalidades, estrutura de pastas e como
executar. **Falta**, para fechar o entregável:

- nomes dos integrantes, do tutor e do coordenador (estão como "Nome do integrante 1");
- **link do vídeo** no YouTube (não listado);
- seção de **evolução ao longo das 4 sprints** — hoje o histórico só tem a v0.1.0;
- revisão do parágrafo "Estado atual", que precisa bater com o código congelado.

## Observações importantes

- O convite ao tutor **expira em 7 dias**: envie perto da data de entrega.
- O repositório **não pode ser alterado depois da data limite**.
- Se o grupo quiser concorrer ao prêmio, o projeto fica visível para a Sompo. Caso contrário, é preciso declarar na primeira capa que não deseja concorrer.
- Todos os integrantes precisam ter contribuição visível (commits, PRs, issues ou documentos).

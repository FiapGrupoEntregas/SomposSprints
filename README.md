# FIAP - Faculdade de Informática e Administração Paulista

<p align="center">
<a href= "https://www.fiap.com.br/"><img src="assets/logo-fiap.png" alt="FIAP - Faculdade de Informática e Admnistração Paulista" border="0" width=40% height=40%></a>
</p>

<br>

# Sompo AgriShield — Relevo × Clima

> **Um terreno inclinado que é seguro hoje pode capotar um trator amanhã.**

## Nome do grupo

## 👨‍🎓 Integrantes: 
- <a href="https://www.linkedin.com/company/inova-fusca">Nome do integrante 1</a>
- <a href="https://www.linkedin.com/company/inova-fusca">Nome do integrante 2</a>
- <a href="https://www.linkedin.com/company/inova-fusca">Nome do integrante 3</a> 
- <a href="https://www.linkedin.com/company/inova-fusca">Nome do integrante 4</a> 
- <a href="https://www.linkedin.com/company/inova-fusca">Nome do integrante 5</a>

## 👩‍🏫 Professores:
### Tutor(a) 
- <a href="https://www.linkedin.com/company/inova-fusca">Nome do Tutor</a>
### Coordenador(a)
- <a href="https://www.linkedin.com/company/inova-fusca">Nome do Coordenador</a>


## 📜 Descrição

O seguro de máquinas agrícolas no Brasil é reativo: a seguradora só age depois do acidente. E um dos
fatores que mais pesam no risco de uma máquina, **o relevo**, praticamente não é considerado. Uma
encosta de 12° é tranquila com o solo seco. Depois de 40 mm de chuva o solo perde aderência, e a mesma
encosta passa a ter risco de capotamento e deslizamento. Nas baixadas a água acumula e a máquina
atola. Os topos de morro concentram raios e as cristas expostas pegam as rajadas mais fortes.

O **Sompo AgriShield** cruza **relevo** e **clima** para prevenir acidentes com máquinas agrícolas:

1. **Mapa de risco por talhão e por dia.** A partir da elevação por satélite (Open-Meteo / Copernicus DEM), calculamos a inclinação, a orientação e as classes de terreno (baixada, encosta, topo exposto) de cada fazenda. Cruzando com a previsão do tempo (chuva acumulada, vento, tempestade, calor e umidade), o sistema mostra para os próximos 7 dias **onde e quando** operar é perigoso, sempre com o motivo.
2. **Limite de inclinação dinâmico na máquina.** A plataforma calcula o limite seguro do dia (ex.: 15° com solo seco, 10° com solo encharcado) e envia via MQTT a um dispositivo **ESP32** instalado no equipamento (simulado no Wokwi). O dispositivo mede a inclinação real com um MPU6050 e alerta o operador na hora, com LEDs e buzzer, mesmo sem internet. Ele também detecta capotamentos e envia os 30 s de contexto do evento.
3. **Perfil de terreno para subscrição.** Na cotação, a Sompo vê um perfil objetivo do terreno (porcentagem da área íngreme, baixadas, áreas expostas, classe A/B/C), sem questionário e sem hardware.
4. **Replay de acidentes reais.** Rodamos o motor na data e no local de acidentes noticiados para responder, com honestidade: *o sistema teria alertado?*

O alerta ao operador vem de **regras explicáveis** ([document/regras-de-risco.md](document/regras-de-risco.md)):
cada alerta mostra o motivo, em números. Ao lado dele, um **modelo preditivo treinado com sinistros
reais do seguro rural** (PSR/SISSER) dá uma segunda leitura para a seguradora.

**E aqui vai o resultado como ele saiu: o modelo NÃO supera o baseline por regras.** No conjunto de
teste o baseline ficou à frente, e um bootstrap pareado mostrou que os dois são **indistinguíveis**
com a amostra que temos — não seria possível demonstrar superioridade nem se ela existisse. Por isso
as **regras seguem sendo a base do alerta**, e o modelo entra ao lado, nunca no lugar. Publicamos
isso porque é o resultado, não porque é o que gostaríamos de ter encontrado. Métricas, intervalo de
confiança e a leitura completa em
[document/dados-e-modelo.md](document/dados-e-modelo.md#resultados-do-modelo-d3); a mesma ressalva
aparece na tela, montada a partir do próprio artefato do modelo.

A explicação mais provável é o rótulo: o que a base pública tem é **sinistro de seguro agrícola**
(perda de lavoura), não sinistro de **máquina**. Calibrar com a base de sinistros de máquinas da
Sompo e evoluir para o **Passaporte Digital** é o roadmap — e é exatamente o que a parceria
destrava.

## 🧭 Funcionalidades

Visão completa do que será implementado, em ordem: [document/plano-de-implementacao.md](document/plano-de-implementacao.md).
Detalhamento de cada feature, com critérios de aceite, em [`feature/`](feature/README.md).
As **User Stories** dos quatro perfis (operador, gestor de frota, técnico de manutenção e analista da
seguradora) e a **matriz de rastreabilidade** história → feature → arquivo → evidência estão em
[document/user-stories.md](document/user-stories.md).

| Área | P0 (demo) | P1 | P2 |
|---|---|---|---|
| Plataforma | Fazendas de exemplo, mapa de relevo, risco em 7 dias, limite dinâmico, painel ao vivo | Recomendações, raio/vento/incêndio, perfil de subscrição, replay | Telegram, histórico do equipamento |
| Dispositivo | Inclinômetro, alerta local, limite via MQTT, telemetria | Detecção de capotamento, regra dos 30 local | Display OLED, botão de ocorrência |
| Dados e modelo | Ingestão do PSR (sinistros reais), dataset relevo × clima, modelo preditivo com métricas | Score híbrido na tela, relatórios e tendências | — |
| Infra | Cliente Open-Meteo, ponte MQTT, SQLite, segurança e auditoria, simulador e testes e2e, ambiente de demo | — | Deploy em nuvem |
| Entrega | User Stories rastreadas, README final, diagrama, evidências, vídeo | — | — |

**Estado atual (v0.1.0):** estrutura inicial. A API responde em `/api/v1/health`, já tem o cliente Open-Meteo com cache, *stale-if-error* e agregação diária do clima (I1) e serve as três fazendas de demonstração em `/api/v1/farms` e `/api/v1/farms/{id}` (W1) e o mapa de relevo em `/api/v1/farms/{id}/terrain` — grade 10 × 10 com inclinação, orientação e classe de terreno, com cache de 24 h (W2), e a **previsão de risco em 7 dias** em `/api/v1/farms/{id}/risk` — estado do solo, limite de inclinação do dia e os perigos de **capotamento** e **atolamento** célula a célula, cada alerta com o motivo em português e com números, mais o cenário simulado `?scenario=heavy_rain` para a demo (W3, lado da API; a tela sai com o front); o front já traz o **seletor de fazenda** compartilhado entre as páginas (W1) e a aba **Relevo** do Mapa de risco, com mapa `pydeck` da grade 10 × 10 colorido por inclinação ou por classe de terreno, tooltip por célula, KPIs (amplitude, inclinação máxima, % da área ≥ 15°) e legenda (W2), e a aba **Previsão de risco**, com a faixa dos 7 dias, o mapa das células por nível, o gráfico de chuva (dia e 72 h) e o toggle de cenário simulado devidamente sinalizado (W3), o filtro de perigos com ícones para raio, vento e incêndio (W7) e o card **O que fazer** com as janelas seguras e o aviso de que elas não liberam as áreas vermelhas (W6), a página **Equipamento ao vivo** com o card do limite do dia e o botão que envia o limite ao ESP32 (W4) e o painel ao vivo com status, banner de nível, gráfico de 10 min, eventos e alerta de capotamento (W5), a página **Subscrição** com o selo de classe, os drivers e a carteira ordenada pelo score (W8) e a página **Relatórios**, com os três recortes (equipamento, região e cultura), o `purpose` de cada um e o download em CSV gerado pela API (W12), além do cartão da probabilidade do modelo ao lado do nível por regras, com a ressalva de que o modelo ainda não supera o baseline (W13), a seção **Passaporte (prévia)** com o histórico do equipamento e a ressalva de roadmap (W11), e a página **Replay de acidentes**, que roda o motor sobre cinco acidentes reais noticiados e mostra o veredito no ponto e na vizinhança com as limitações à vista (W9); e o firmware mede a inclinação com o MPU6050 a 10 Hz (E1), faz o alerta local com LEDs, buzzer e evento `tilt_alert` (E2), aplica o limite do dia recebido no `config` retained com validação e persistência na NVS (E3) publica telemetria a cada 5 s, com `ts` do NTP (E4), detecta capotamento por inclinação sustentada ou impacto, travando o alerta máximo e enviando o evento `rollover` com os 30 s de contexto (E5), mede o microclima com o DHT22 para publicar as condições da regra dos 30 (E6), mostra tudo num display OLED (E7) e registra ocorrências do operador com um toque no botão, também com 30 s de contexto (E8). **O firmware está completo: E1 a E8.** Do lado da integração, a **ponte MQTT já liga a API ao equipamento**: a API assina telemetria, eventos e status no broker público, valida cada payload, deduplica os eventos que o ESP32 manda em triplicata e publica o `config` com QoS 1 e retained (I2); tudo o que chega é gravado em SQLite nas tabelas `telemetry`, `device_event`, `device_status` e `published_config`, com retenção de 7 dias para a telemetria (I3) — e a API sobe normalmente mesmo com o broker fora do ar. Com isso, o ciclo fecha: a API calcula o **limite de inclinação do dia de cada equipamento** e o publica retained no `config` — no boot, a cada 1 h e pelo botão de envio manual, com `date` e `scenario` para a demo do dia chuvoso (W4) — e expõe o **painel ao vivo** em `/api/v1/devices/{id}/status`, `/telemetry/latest`, `/telemetry?minutes=10` e `/events`, com offline por LWT ou por 20 s de silêncio (W5). O motor de risco agora avalia **cinco perigos** por célula — capotamento, atolamento, raio, vento e incêndio pela regra dos 30, este último subindo de 🟡 para 🔴 nas encostas de 15° ou mais (W7) — e a API traduz tudo em ação: `GET /api/v1/farms/{id}/recommendations` devolve as **janelas seguras** de hoje e amanhã e as orientações em português, sempre lembrando que as áreas vermelhas seguem proibidas mesmo dentro da janela (W6). Cada equipamento acumula ainda um **histórico** — horas operando, tempo acima do limite, alertas, capotamentos e a linha do tempo dos eventos —, que é a **prévia do Passaporte Digital** e liga a entrega ao roadmap (W11). O motor roda também **sobre o passado**: `POST /api/v1/replay` aplica as mesmas regras à data e ao local de **5 acidentes reais noticiados** e responde "o sistema teria alertado?" — hoje **2 de 5 no ponto e 3 de 5 na grade**, com os "não" explicados em vez de escondidos (W9). E, ao lado do nível por regras, cada dia traz a **probabilidade do modelo** treinado com sinistros reais do PSR, sempre acompanhada da ressalva de que ele **não superou** o baseline por regras no teste — as regras seguem sendo a base do alerta ao operador (W13). Para a seguradora, a API entrega ainda o **perfil de subscrição** de cada fazenda (`/underwriting`: indicadores do relevo, score 0–100 e classe A/B/C) e três **relatórios com tendências** — por equipamento, por região e por cultura —, estes últimos calculados sobre as **1.525.473 apólices reais** do PSR já carregadas, com exportação em CSV pronta para o Excel (W8 e W12). Fechando o requisito de **segurança e rastreabilidade** do enunciado, a API exige `X-API-Key` para publicar limite e consultar a auditoria, responde com log estruturado em JSON e um `X-Request-ID` em toda resposta, e grava **toda decisão** — score de risco, limite publicado e alerta do equipamento — na tabela `decision_log`, com entrada, saída e a versão da regra que decidiu, além do hash SHA-256 dos eventos recebidos (I5). Do lado dos dados, a **base de sinistros reais já está carregada**: 1.525.473 apólices do PSR/SISSER (2006–2025) na tabela `policy` do SQLite, com coordenada, cultura, vigência, valor indenizado e causa normalizada, e sem nenhum dado pessoal (D1). E a integração está **validada de ponta a ponta**: o simulador de dispositivo (`scripts/simulate_device.py`) publica no mesmo contrato do firmware e os testes `e2e` comprovam 100/100 mensagens gravadas numa rajada, payload inválido descartado sem derrubar a ponte, capotamento gravado uma única vez com os 30 s de contexto e trilha de auditoria, e a volta da coleta depois de uma queda — com os tempos medidos: limite no equipamento em 0,31 s e nível novo disponível ao painel em 0,39 s (I6). As evidências estão em [document/evidencias/](document/evidencias/).

## 🏗️ Arquitetura

```
Open-Meteo ──HTTP──► api/ (FastAPI: motor de risco + ponte MQTT + SQLite) ◄──REST── front-web/ (Streamlit)
                              ▲
                              │ MQTT (broker.hivemq.com)
                              ▼
                     iot/ (ESP32 no Wokwi: MPU6050 · DHT22 · LEDs · buzzer · OLED · botao)
```

Detalhes, fluxos e endpoints em [document/arquitetura.md](document/arquitetura.md). Contrato MQTT em
[document/contrato-mqtt.md](document/contrato-mqtt.md). Fontes de dados, pipeline e modelo em
[document/dados-e-modelo.md](document/dados-e-modelo.md).

**Dados reais:** o modelo é calibrado com as apólices e indenizações do **PSR/SISSER** (Ministério da
Agricultura, dados abertos CC-BY, 2006–2025), que trazem coordenada da propriedade, cultura, período
de vigência, valor indenizado e causa do sinistro.

## 📁 Estrutura de pastas

- <b>.claude</b>: agentes do Claude Code (`dev-api`, `dev-front`, `dev-iot`, `dev-dados`, `qa-integracao`, `doc-entrega`, `revisor`) e a skill `/implementar-feature`. Ver [document/fluxo-agentes.md](document/fluxo-agentes.md).
- <b>.github</b>: templates de PR e de issues (Task, Bug, Feature), guia de contribuição e a CI (`workflows/ci.yml`).
- <b>api</b>: backend **FastAPI** (motor de risco, integração Open-Meteo, ponte MQTT). Gerenciado com **uv** e compatível com **pip**. Ver [api/README.md](api/README.md).
- <b>assets</b>: imagens e outros arquivos não estruturados.
- <b>config</b>: arquivos de configuração compartilhados do projeto.
- <b>data</b>: amostra versionada do PSR (`sample/psr_amostra.csv`), o **dataset de treino** (`dataset_treino.parquet` + a ficha `.json`) e os dados brutos baixados por script (`raw/`, fora do Git). Ver [data/README.md](data/README.md).
- <b>document</b>: documentação técnica e **práticas do projeto** (arquitetura, regras de risco, contrato MQTT, fluxo git, padrões de código, definição de pronto, ambiente, decisões, demo, fluxo de agentes, [user stories e rastreabilidade](document/user-stories.md), entregáveis, [backlog pós-entrega](document/backlog-pos-entrega.md) e evidências). Ver [document/README.md](document/README.md).
- <b>feature</b>: especificação detalhada de **cada feature** (objetivo, escopo, implementação por camada, critérios de aceite, tarefas) e o cronograma. Ver [feature/README.md](feature/README.md).
- <b>front-web</b>: front-end **Streamlit** (só exibe dados vindos da API). Ver [front-web/README.md](front-web/README.md).
- <b>iot</b>: firmware do **ESP32** (PlatformIO + Wokwi): `src/main.ino`, `diagram.json`, `wokwi.toml`, `libraries.txt`. Ver [iot/README.md](iot/README.md).
- <b>scripts</b>: scripts auxiliares (`run-demo.sh` sobe a demo inteira, `sync-requirements.sh`, `create-labels.sh`), o **simulador de dispositivo** (`simulate_device.py`, usado nos testes de ponta a ponta) e o pipeline de dados (`download_psr.py`, `build_psr_sample.py`, `load_psr.py`, `build_dataset.py`). Ver [scripts/readme.md](scripts/readme.md).
- <b>CLAUDE.md</b>: instruções para o Claude Code neste repositório.
- <b>LICENSE</b>: licença MIT.
- <b>README.md</b>: este guia.

## 🔧 Como executar o código

### Pré-requisitos

- Python 3.11+ e **[uv](https://docs.astral.sh/uv/)** (recomendado) **ou** pip
- VS Code com as extensões **PlatformIO IDE** e **Wokwi Simulator** (ou uma conta em [wokwi.com](https://wokwi.com))

Passo a passo completo e solução de problemas em [document/ambiente-de-desenvolvimento.md](document/ambiente-de-desenvolvimento.md).

### 0. Demo completa com um comando (Linux e macOS)

```bash
cp api/.env.example api/.env                 # escolha uma chave em AGRISHIELD_API_KEYS
cp front-web/.env.example front-web/.env     # AGRISHIELD_API_KEY = uma das chaves acima
./scripts/run-demo.sh --aquecer              # sobe API + front, confere /health e aquece o cache
```

O script confere o ambiente antes de subir, avisa se as **duas chaves** não combinarem (é o que faz
o botão "Enviar ao equipamento" responder 401 no meio da apresentação), espera o `/health` e
derruba os dois processos com **um Ctrl+C**. `--conferir` só faz a conferência; `--sem-front` sobe
só a API; `--ajuda` lista tudo. Falta só abrir a simulação do Wokwi (passo 3).

**Windows:** o script é bash. Use o **WSL** (aí ele funciona igual) ou abra dois terminais do
PowerShell e rode o passo 1 num e o passo 2 no outro, definindo as variáveis antes:

```powershell
# terminal 1 — API
cd api
Copy-Item .env.example .env
uv sync ; uv run uvicorn app.main:app --port 8000

# terminal 2 — front
cd front-web
$env:AGRISHIELD_API_URL = "http://localhost:8000"
$env:AGRISHIELD_API_KEY = "a-mesma-chave-de-AGRISHIELD_API_KEYS"
uv sync ; uv run streamlit run app.py
```

Fechar os dois terminais (ou Ctrl+C em cada um) encerra a demo.

### 1. API (http://localhost:8000/docs)

```bash
cd api
uv sync && uv run fastapi dev app/main.py
# com pip:
# python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt && uvicorn app.main:app --reload
```

### 2. Front-web (http://localhost:8501)

```bash
cd front-web
export AGRISHIELD_API_URL=http://localhost:8000
export AGRISHIELD_API_KEY=a-mesma-chave-de-AGRISHIELD_API_KEYS   # só o botão "Enviar ao equipamento" usa
uv sync && uv run streamlit run app.py
# com pip:
# python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt && streamlit run app.py
```

Em vez de exportar, você pode copiar `front-web/.env.example` para `front-web/.env` e preencher: o
front carrega esse arquivo no início (como a API faz com o dela). Variável já exportada no terminal
**vence** o arquivo. Sem a chave, a leitura funciona e só o envio do limite ao ESP32 é recusado com 401.

Para o botão **"Enviar ao equipamento"** funcionar, exporte também `AGRISHIELD_API_KEY` com uma das
chaves de `AGRISHIELD_API_KEYS` configuradas na API (I5). Sem ela, a leitura continua normal e só o
envio é recusado, com a tela dizendo o que configurar.

### 3. Dispositivo (ESP32 no Wokwi)

- **VS Code:** abra `iot/`, compile com `pio run` e depois `F1` → *Wokwi: Start Simulator*.
- **Navegador:** cole `iot/src/main.ino`, `iot/diagram.json` e `iot/libraries.txt` num projeto ESP32 do wokwi.com ([passo a passo](iot/README.md)).

### 4. Base de sinistros reais (PSR/SISSER)

Opcional para rodar a API, necessário para o modelo (D2/D3). Os CSVs somam ~470 MB e **não** estão
no Git; a amostra de 453 linhas em `data/sample/` já vem versionada e basta para os testes.

```bash
uv run --project api python scripts/download_psr.py       # baixa para data/raw/
uv run --project api python scripts/load_psr.py           # carrega no SQLite + relatório de qualidade
uv run --project api python scripts/load_psr.py --sample  # só a amostra versionada (rápido)
uv run --project api python scripts/build_dataset.py     # gera o dataset de treino (D2)
uv run --project api python scripts/train_model.py       # treina e compara com o baseline (D3)
```

A carga completa leva ~5 min e gera um `api/agrishield.db` de ~420 MB (fora do Git). Fonte, licença
(CC-BY) e relatório de qualidade: [document/dados-e-modelo.md](document/dados-e-modelo.md).

### Testes e qualidade

```bash
cd api && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd front-web && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd iot && pio run
```

Mudou dependências Python? Rode `./scripts/sync-requirements.sh` para manter uv e pip em sincronia.

### Testes de ponta a ponta (I6)

Sobem a API de verdade, falam com o **broker público** e rodam o simulador de dispositivo. Por
usarem rede, ficam **fora da CI** e só rodam quando pedidos:

```bash
cd api && uv run pytest -m e2e -s        # ~2 min, precisa de internet
```

O simulador também roda sozinho, sem pytest (6 cenários: `normal`, `alerta`, `capotamento`,
`sujo`, `rajada`, `queda`):

```bash
uv run --project api python scripts/simulate_device.py --scenario rajada --count 100
```

⚠️ Ele é **ferramenta de teste**, não substitui o Wokwi na demo. Relatórios de cada execução em
[document/evidencias/](document/evidencias/).

## 🤝 Como contribuir

Leia [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md), [document/fluxo-git.md](document/fluxo-git.md) e a
[Definição de Pronto](document/definicao-de-pronto.md). Todo PR que altera o projeto **atualiza este README**.

Com o Claude Code, as features são implementadas por agentes especialistas e avaliadas por um agente
revisor: `/implementar-feature W2`. Ver [document/fluxo-agentes.md](document/fluxo-agentes.md).

## 🗃 Histórico de lançamentos

* 0.1.0 - 15/09/2026
    * Estrutura inicial: API FastAPI (uv + pip), front-web Streamlit, firmware ESP32 (PlatformIO + Wokwi), especificação das features, documentação de práticas, padrões do GitHub e CI, licença MIT.
    * Planejamento das features de dados e modelo (D1–D3), segurança e rastreabilidade (I5), validação ponta a ponta (I6), relatórios (W12), score híbrido (W13) e entrega acadêmica (DOC1, DOC2); agentes `dev-dados`, `qa-integracao` e `doc-entrega`.
    * Documentação consolidada em `document/` (o modelo antigo da FIAP foi removido). Agentes do Claude Code (devs por área + revisor) e a skill `/implementar-feature`.

## 📋 Licença

Este projeto está licenciado sob a **Licença MIT**. Veja o arquivo [LICENSE](LICENSE).

O modelo deste README é baseado no <a href="https://github.com/agodoi/template">MODELO GIT FIAP</a>, da <a href="https://fiap.com.br">Fiap</a>, licenciado sob <a href="http://creativecommons.org/licenses/by/4.0/?ref=chooser-v1" target="_blank" rel="license noopener noreferrer">Attribution 4.0 International</a>.

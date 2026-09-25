# FIAP - Faculdade de Informática e Administração Paulista

<p align="center">
<a href= "https://www.fiap.com.br/"><img src="assets/logo-fiap.png" alt="FIAP - Faculdade de Informática e Admnistração Paulista" border="0" width=40% height=40%></a>
</p>

<br>

# Sompo AgriShield — Relevo × Clima

> **Um terreno inclinado que é seguro hoje pode capotar um trator amanhã.**

## 👨‍🎓 Integrantes

- JonattasFelipe — RM572692
- NatanaelFilho — RM572474
- PedroHenrique — RM571394
- AndrewsOliveira — RM572311

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

**E aqui vai o resultado como ele saiu, incluindo a parte em que mudamos de ideia.** Com o dataset
completo (2.256 apólices), **o modelo supera o baseline por regras** no teste de 2024: AUC-PR
**0,144 contra 0,074**, diferença +0,070 com IC 95% [+0,003, +0,169]. Em 20/09 este README dizia o
contrário — e a auditoria mostrou por quê: o teste tinha então **155 linhas e 8 sinistros**, uma
fatia que subestimava a taxa real de 2024 (5,2% contra 9,5% na população). **A virada veio do
conjunto de teste, não do modelo**: o artefato antigo, sem retreino nenhum, também venceria no
teste novo. E as ressalvas continuam do tamanho do número — 26 sinistros no teste (abaixo dos 30
que o próprio script exige para conclusão firme), intervalo que quase toca o zero, e um alvo
(*qualquer* indenização, dominada por seca) que **não é** o perigo que o alerta trata. Por isso as
**regras seguem sendo a base do alerta**, e o modelo entra ao lado, nunca no lugar. A decomposição,
os intervalos e a leitura completa estão em
[document/dados-e-modelo.md](document/dados-e-modelo.md#resultados-do-modelo-d3); a ressalva
aparece na tela, montada a partir do próprio artefato do modelo.

Há também um experimento **opt-in** com uma pequena rede neural (MLP), treinada e versionada
separadamente por `scripts/train_model.py --somente-rede-opcional`. Ela não participa da seleção
do modelo publicado nem altera os alertas ou limites. A API permite solicitá-la separadamente em
`GET /api/v1/farms/{farm_id}/risk?include_experimental_mlp=true` (desligada por padrão), com score,
versão e nota próprios; o score **não é uma probabilidade calibrada**. Artefato ausente é informado
como indisponível e falhas de artefato/inferência não são ocultadas. A previsão de risco tem
controle individual por sessão, desligado por padrão, e mostra o resultado MLP identificado como
experimental e não calibrado, sem alterar os alertas ou limites oficiais.

O limite de fundo é o rótulo: o que a base pública tem é **sinistro de seguro agrícola**
(perda de lavoura), não sinistro de **máquina**. Calibrar com a base de sinistros de máquinas da
Sompo e evoluir para o **Passaporte Digital** é o roadmap — e é exatamente o que a parceria
destrava.

> **Como interpretar os dados:** o score do modelo estima sinistros agrícolas da base PSR/SISSER,
> não a probabilidade de acidente de uma máquina. Os relatórios por cultura usam a cultura como
> recorte disponível na base, não como registro do tipo de operação executada pelo equipamento.
> Na ingestão, nomes e documentos são descartados; o `proposal_id` é mantido para deduplicação e
> reprodutibilidade e pode ser associado ao CSV público do PSR, que contém o nome. Veja as
> limitações e decisões completas em [document/dados-e-modelo.md](document/dados-e-modelo.md) e
> [feature/I5-seguranca-rastreabilidade.md](feature/I5-seguranca-rastreabilidade.md).

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

> Esta tabela é de **prioridade**, não de status. Duas correções de rota valem registro: o
> **histórico do equipamento** (W11, P2) foi entregue e aprovado em 21/09, e o **alerta por
> Telegram** (W10, P2) foi **decidido fora do escopo** — exige token de bot, ou seja, credencial
> de terceiro e canal a manter. O status de cada feature está em
> [`feature/README.md`](feature/README.md).

**Estado atual:** o projeto já contém a API, o front Streamlit, a ingestão/modelo de dados, a ponte MQTT e o firmware descritos nesta seção; os status por feature e os itens ainda pendentes estão em [`feature/README.md`](feature/README.md). A cópia editada perdida não está disponível para um diff exato. O que foi reconstruído e o que já existia na cópia preservada estão separados em [document/historico-reconstrucao.md](document/historico-reconstrucao.md). A última verificação local (testes, build e smoke test) e os avisos não bloqueantes estão em [document/evidencias/2026-09-25-validacao-geral.md](document/evidencias/2026-09-25-validacao-geral.md).

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

### Limites de segurança

A configuração pública HiveMQ sem TLS/autenticação serve **somente à demo com dados sintéticos**:
mensagens e comandos MQTT podem ser lidos ou forjados por terceiros. Não conecte equipamento real
nesse broker. Em produção, MQTT habilitado exige na API host privado, TLS validado por CA e
credenciais próprias; as leituras de fazendas, equipamentos, relatórios e replay também exigem
`X-API-Key` fora de `AGRISHIELD_ENVIRONMENT=dev` explícito (`/health` permanece público). Configure
ACL por dispositivo no próprio broker: **a conexão segura da API não cria autenticação nem ACL no
broker MQTT**. Leia
[document/contrato-mqtt.md](document/contrato-mqtt.md) e [document/decisoes.md](document/decisoes.md)
antes de qualquer implantação. A chave de API compartilhada do MVP não substitui identidade de
usuário nem autorização por organização/dispositivo.

### Simulação pública do ESP32 no Wokwi

Projeto com firmware, circuito e bibliotecas do AgriShield: [abrir a simulação](https://wokwi.com/projects/476164477397501953).
A validação manual do inclinômetro e seus limites está registrada em
[document/evidencias/2026-09-25-inclinometro-wokwi.md](document/evidencias/2026-09-25-inclinometro-wokwi.md).
Em uma execução posterior, Wi-Fi, MQTT com broker público, aplicação de configuração retained e
publicação de `limit_applied` e de telemetrias sequenciais em intervalos de 5 s funcionaram; a
integração com a API e o painel ainda não foi validada nessa simulação.

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
- <b>scripts</b>: scripts auxiliares (`run-demo.sh` sobe a demo inteira, `sync-requirements.sh`, `create-labels.sh`), o **simulador de dispositivo** (`simulate_device.py`, usado nos testes de ponta a ponta) e o pipeline de dados (`download_psr.py`, `build_psr_sample.py`, `load_psr.py`, `build_dataset.py`, `train_model.py`, o `medir_mapa.py` que mede o tempo de resposta do mapa, mais o `_bootstrap.py` que resolve o caminho do banco). Ver [scripts/readme.md](scripts/readme.md).
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
**vence** o arquivo. No modo local `AGRISHIELD_ENVIRONMENT=dev`, as leituras continuam públicas e
só o envio do limite ao ESP32 exige a chave. Fora de `dev`, o front também precisa enviar
`X-API-Key` em todas as leituras protegidas.

Para o botão **"Enviar ao equipamento"** funcionar, exporte também `AGRISHIELD_API_KEY` com uma das
chaves de `AGRISHIELD_API_KEYS` configuradas na API (I5). Em `dev`, sem ela as leituras continuam
normais e só o envio é recusado, com a tela dizendo o que configurar.

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

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

O score é **baseado em regras explicáveis** ([docs/regras-de-risco.md](docs/regras-de-risco.md)),
porque ainda não temos a base de sinistros para treinar modelos. A calibração com os dados da Sompo,
os modelos de ML e o **Passaporte Digital** do equipamento formam o roadmap.

## 🧭 Funcionalidades

Detalhamento completo, com critérios de aceite, em [`feature/`](feature/README.md).

| Área | P0 (demo) | P1 | P2 |
|---|---|---|---|
| Plataforma | Fazendas de exemplo, mapa de relevo, risco em 7 dias, limite dinâmico, painel ao vivo | Recomendações, raio/vento/incêndio, perfil de subscrição, replay | Telegram, histórico do equipamento |
| Dispositivo | Inclinômetro, alerta local, limite via MQTT, telemetria | Detecção de capotamento, regra dos 30 local | Display OLED, botão de ocorrência |
| Infra | Cliente Open-Meteo, ponte MQTT, SQLite, ambiente de demo | — | Deploy em nuvem |

**Estado atual (v0.1.0):** estrutura inicial. A API responde em `/api/v1/health`, o front tem a navegação e as páginas de placeholder, e o firmware conecta no Wi-Fi e no MQTT e lê os sensores.

## 🏗️ Arquitetura

```
Open-Meteo ──HTTP──► api/ (FastAPI: motor de risco + ponte MQTT + SQLite) ◄──REST── front-web/ (Streamlit)
                              ▲
                              │ MQTT (broker.hivemq.com)
                              ▼
                     iot/ (ESP32 no Wokwi: MPU6050 · DHT22 · LEDs · buzzer)
```

Detalhes, fluxos e endpoints em [docs/arquitetura.md](docs/arquitetura.md). Contrato MQTT em [docs/contrato-mqtt.md](docs/contrato-mqtt.md).

## 📁 Estrutura de pastas

- <b>.github</b>: templates de PR e de issues (Task, Bug, Feature), guia de contribuição e a CI (`workflows/ci.yml`).
- <b>api</b>: backend **FastAPI** (motor de risco, integração Open-Meteo, ponte MQTT). Gerenciado com **uv** e compatível com **pip**. Ver [api/README.md](api/README.md).
- <b>assets</b>: imagens e outros arquivos não estruturados.
- <b>config</b>: arquivos de configuração compartilhados do projeto.
- <b>docs</b>: documentação técnica e **práticas do projeto** (arquitetura, regras de risco, contrato MQTT, fluxo git, padrões de código, definição de pronto, ambiente, decisões, demo). Ver [docs/README.md](docs/README.md).
- <b>document</b>: entregáveis acadêmicos da FIAP. Na subpasta "other", documentos complementares.
- <b>feature</b>: especificação detalhada de **cada feature** (objetivo, escopo, implementação por camada, critérios de aceite, tarefas) e o cronograma. Ver [feature/README.md](feature/README.md).
- <b>front-web</b>: front-end **Streamlit** (só exibe dados vindos da API). Ver [front-web/README.md](front-web/README.md).
- <b>iot</b>: firmware do **ESP32** (PlatformIO + Wokwi): `src/main.ino`, `diagram.json`, `wokwi.toml`, `libraries.txt`. Ver [iot/README.md](iot/README.md).
- <b>scripts</b>: scripts auxiliares (`sync-requirements.sh`, `create-labels.sh`).
- <b>CLAUDE.md</b>: instruções para o Claude Code neste repositório.
- <b>LICENSE</b>: licença MIT.
- <b>README.md</b>: este guia.

## 🔧 Como executar o código

### Pré-requisitos

- Python 3.11+ e **[uv](https://docs.astral.sh/uv/)** (recomendado) **ou** pip
- VS Code com as extensões **PlatformIO IDE** e **Wokwi Simulator** (ou uma conta em [wokwi.com](https://wokwi.com))

Passo a passo completo e solução de problemas em [docs/ambiente-de-desenvolvimento.md](docs/ambiente-de-desenvolvimento.md).

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
uv sync && uv run streamlit run app.py
# com pip:
# python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt && streamlit run app.py
```

### 3. Dispositivo (ESP32 no Wokwi)

- **VS Code:** abra `iot/`, compile com `pio run` e depois `F1` → *Wokwi: Start Simulator*.
- **Navegador:** cole `iot/src/main.ino`, `iot/diagram.json` e `iot/libraries.txt` num projeto ESP32 do wokwi.com ([passo a passo](iot/README.md)).

### Testes e qualidade

```bash
cd api && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd front-web && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd iot && pio run
```

Mudou dependências Python? Rode `./scripts/sync-requirements.sh` para manter uv e pip em sincronia.

## 🤝 Como contribuir

Leia [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md), [docs/fluxo-git.md](docs/fluxo-git.md) e a
[Definição de Pronto](docs/definicao-de-pronto.md). Todo PR que altera o projeto **atualiza este README**.

## 🗃 Histórico de lançamentos

* 0.1.0 - 15/09/2026
    * Estrutura inicial: API FastAPI (uv + pip), front-web Streamlit, firmware ESP32 (PlatformIO + Wokwi), especificação das features, documentação de práticas, padrões do GitHub e CI, licença MIT.

## 📋 Licença

Este projeto está licenciado sob a **Licença MIT**. Veja o arquivo [LICENSE](LICENSE).

O modelo deste README é baseado no <a href="https://github.com/agodoi/template">MODELO GIT FIAP</a>, da <a href="https://fiap.com.br">Fiap</a>, licenciado sob <a href="http://creativecommons.org/licenses/by/4.0/?ref=chooser-v1" target="_blank" rel="license noopener noreferrer">Attribution 4.0 International</a>.

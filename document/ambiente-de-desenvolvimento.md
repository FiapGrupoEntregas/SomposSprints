# Ambiente de desenvolvimento

## Pré-requisitos

| Ferramenta | Para quê | Instalação |
|---|---|---|
| Git | versionamento | https://git-scm.com |
| Python 3.11+ | api e front-web | https://python.org. Com uv, não é necessário: o uv baixa o Python sozinho |
| **uv** (recomendado) | ambientes e dependências Python | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"`) |
| pip | alternativa ao uv | já vem com o Python |
| VS Code | editor | https://code.visualstudio.com |
| Extensão **PlatformIO IDE** | compilar o firmware | Marketplace do VS Code |
| Extensão **Wokwi Simulator** | simular o ESP32 no VS Code | Marketplace. Pede uma licença gratuita no primeiro uso |
| Conta no wokwi.com | simular no navegador (opcional) | https://wokwi.com |
| `gh` (opcional) | criar labels e PRs pelo terminal | https://cli.github.com |

## Primeira vez

```bash
git clone git@github.com:FiapGrupoEntregas/SomposSprints.git
cd SomposSprints
```

### API

```bash
cd api
uv sync                                   # ou: python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
cp .env.example .env
uv run fastapi dev app/main.py            # ou: uvicorn app.main:app --reload
```
Abra http://localhost:8000/docs.

### Front-web

```bash
cd front-web
uv sync                                   # ou: pip install -r requirements-dev.txt (numa venv)
uv run streamlit run app.py               # ou: streamlit run app.py
```
Abra http://localhost:8501.

### IoT

- **VS Code:** abra a pasta `iot/` → compile (✓ do PlatformIO ou `pio run`) → `F1` → *Wokwi: Start Simulator*.
- **Navegador:** siga o passo a passo do [iot/README.md](../iot/README.md#opção-2-wokwi-no-navegador-mais-rápido-para-o-time-testar).

## Rodando tudo junto (demo local)

Use 3 terminais, mais o Wokwi:

| Terminal | Comando |
|---|---|
| 1 — API | `cd api && uv run fastapi dev app/main.py` |
| 2 — Front | `cd front-web && uv run streamlit run app.py` |
| 3 — MQTT (opcional) | `mosquitto_sub -h broker.hivemq.com -t 'agrishield/fiap-sompo-2026/#' -v` |
| Wokwi | simulação rodando (VS Code ou navegador) |

## Testar sem o Wokwi: simulador de dispositivo (I6)

O `scripts/simulate_device.py` faz o papel do ESP32 — publica telemetria, eventos e `status` no
mesmo contrato do firmware e assina o `config`. Serve para desenvolver e testar sem abrir o
navegador. **Não substitui o Wokwi na demo.**

```bash
# com a API rodando no terminal 1
uv run --project api python scripts/simulate_device.py --scenario normal
uv run --project api python scripts/simulate_device.py --scenario rajada --count 100
```

Os testes de ponta a ponta usam esse script e sobem a própria API; eles **não** rodam na CI:

```bash
cd api && uv run pytest -m e2e -s       # ~2 min, precisa de internet
```

Cenários, opções e a razão de o `device_id` padrão ser `tractor-02` estão em
[scripts/readme.md](../scripts/readme.md). As evidências de cada execução ficam em
[evidencias/](evidencias/).

## uv e pip ao mesmo tempo

- `pyproject.toml` é a **fonte da verdade** e o `uv.lock` trava as versões.
- `requirements.txt` e `requirements-dev.txt` são **gerados** do `uv.lock` por `scripts/sync-requirements.sh`, para quem usa pip e para plataformas de deploy (Streamlit Community Cloud, Render).
- Fluxo para adicionar uma dependência:
  ```bash
  cd api                       # ou front-web
  uv add numpy                 # dependência de produção
  uv add --dev respx           # só de desenvolvimento
  ../scripts/sync-requirements.sh
  git add pyproject.toml uv.lock requirements*.txt
  ```
- Se você só tem pip: peça a quem tem uv para adicionar a dependência, ou instale o uv (é um executável só).
- A CI roda o script e **falha se os arquivos gerados estiverem diferentes** do que foi commitado.

## Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| Front mostra "API indisponível" | API não está rodando ou a URL está errada | Suba a API. Confira `AGRISHIELD_API_URL` |
| `ModuleNotFoundError: app` nos testes da API | rodou o pytest fora da pasta `api/` | `cd api && uv run pytest` |
| `uv run pytest` diz "15 deselected" | são os testes de ponta a ponta (I6), que usam rede | Normal. Rode-os de propósito: `uv run pytest -m e2e` |
| Teste `e2e` falha com "não conectou em broker.hivemq.com" | sem internet ou broker instável | Repita mais tarde; o resultado não depende da máquina |
| ESP32 fica em `[wifi] conectando....` | SSID errado | Use `Wokwi-GUEST`, senha vazia, canal 6 |
| `[mqtt] falhou (state=-2)` | sem internet ou broker instável | Aguarde o retry (5 s). Plano B em [demo.md](demo.md) |
| ESP32 não recebe o limite | prefixo diferente entre a API e o firmware | Compare `AGRISHIELD_MQTT_TOPIC_PREFIX` com `TOPIC_PREFIX` |
| `MPU6050 não encontrado` | fiação do I2C | SDA → 21, SCL → 22 no `diagram.json` |
| CI falha em "requirements em sincronia" | editou o `pyproject.toml` sem regenerar | `scripts/sync-requirements.sh` e commite |

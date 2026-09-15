# api/ — Backend FastAPI

A API é o cérebro do AgriShield:
- calcula o **relevo** (inclinação e classes de terreno) a partir da Open-Meteo Elevation API;
- roda o **motor de risco relevo × clima** ([docs/regras-de-risco.md](../docs/regras-de-risco.md));
- calcula o **limite de inclinação do dia** e publica para o ESP32 via MQTT;
- recebe a **telemetria e os eventos** do ESP32 e entrega tudo ao `front-web/` por REST.

O front-web **não tem regra de negócio**: ele só consome esta API.

## Estrutura

```
api/
├── app/
│   ├── main.py            # cria o FastAPI e registra os routers
│   ├── core/config.py     # Settings (variáveis AGRISHIELD_*)
│   ├── api/v1/router.py   # agrega os routers da v1
│   ├── api/v1/routes/     # uma rota por recurso (health, farms, devices, replay…)
│   ├── schemas/           # modelos Pydantic de entrada/saída
│   ├── services/          # regras de negócio (funções puras, testáveis)
│   └── clients/           # clientes externos (Open-Meteo, Telegram)
├── tests/                 # pytest
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
cp .env.example .env                 # opcional
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

## Endpoints

| Método | Rota | Status |
|---|---|---|
| GET | `/api/v1/health` | ✅ |

Os endpoints planejados estão em [docs/arquitetura.md](../docs/arquitetura.md#endpoints-da-api-v1).

# front-web/ — Front-end Streamlit

Interface web do AgriShield. **Só exibe dados**: toda regra de negócio fica na `api/`.

## Estrutura

```
front-web/
├── app.py                 # entrada: registra as páginas (st.navigation)
├── config.py              # AGRISHIELD_API_URL
├── views/                 # uma página por arquivo
│   ├── home.py            # início + status da API
│   ├── risk_map.py        # W1, W2, W3, W6, W7
│   ├── equipment.py       # W4, W5, W11
│   ├── underwriting.py    # W8
│   └── replay.py          # W9
├── services/api_client.py # única porta de acesso à API
├── tests/                 # pytest (cliente + smoke test das páginas)
├── .streamlit/config.toml # tema
├── pyproject.toml / uv.lock
└── requirements*.txt      # gerados do uv.lock (Streamlit Community Cloud usa requirements.txt)
```

## Rodando

A API precisa estar no ar (ver [api/README.md](../api/README.md)).

### Com uv (recomendado)

```bash
cd front-web
uv sync
uv run streamlit run app.py          # http://localhost:8501
uv run pytest
```

### Com pip

```bash
cd front-web
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
streamlit run app.py
pytest
```

Para apontar para outra API: `export AGRISHIELD_API_URL=https://minha-api.onrender.com`.

## Dependências

Igual à API: `uv add pacote` e depois `../scripts/sync-requirements.sh`. Nunca edite `requirements*.txt` à mão.

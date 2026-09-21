"""Configuração do front-web (variáveis de ambiente).

O `.env` fica **ao lado deste arquivo** (`front-web/.env`) e é carregado no import, como a API
já faz com `pydantic-settings`. Assim `streamlit run app.py` dentro de `front-web/` e
`streamlit run front-web/app.py` a partir da raiz enxergam a mesma configuração.

Variável já exportada no ambiente **vence** o `.env` (padrão do `load_dotenv`): é disso que o
script de demo depende para repassar a configuração aos dois processos.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_FILE)

API_URL = os.getenv("AGRISHIELD_API_URL", "http://localhost:8000")

# Chave enviada em `X-API-Key` nas escritas protegidas da API (I5). No singular: a API aceita
# uma lista (`AGRISHIELD_API_KEYS`), o front usa **uma** dessa lista.
API_KEY = os.getenv("AGRISHIELD_API_KEY", "")

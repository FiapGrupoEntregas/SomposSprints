"""Configuração do front-web (variáveis de ambiente)."""

import os

API_URL = os.getenv("AGRISHIELD_API_URL", "http://localhost:8000")

#!/usr/bin/env python3
"""Ajustes compartilhados pelos scripts que falam com o banco (D1, D2).

Os scripts são documentados para rodar **da raiz** (`uv run --project api python scripts/...`),
mas `Settings.database_url` tem como padrão um caminho **relativo** (`sqlite:///./agrishield.db`),
que só aponta para o banco real quando o processo roda dentro de `api/`. Sem este ajuste, rodar da
raiz abre um SQLite vazio na raiz e o script morre com `no such table: policy`.

`AGRISHIELD_DATABASE_URL` continua mandando: quem exportar a variável escolhe o banco.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "api"


def bootstrap() -> Path:
    """Deixa `app` importável e o banco de `api/` acessível de qualquer diretório."""
    if str(API_DIR) not in sys.path:
        sys.path.insert(0, str(API_DIR))
    os.environ.setdefault(
        "AGRISHIELD_DATABASE_URL", f"sqlite:///{API_DIR / 'agrishield.db'}"
    )
    return ROOT

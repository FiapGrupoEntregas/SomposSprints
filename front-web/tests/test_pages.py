"""Smoke tests: cada página abre sem exceção (mesmo com a API fora do ar)."""

import pytest
from streamlit.testing.v1 import AppTest

PAGES = [
    "home.py",
    "risk_map.py",
    "equipment.py",
    "underwriting.py",
    "replay.py",
]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page: str) -> None:
    # Caminho relativo a este arquivo de teste.
    at = AppTest.from_file(f"../views/{page}", default_timeout=15).run()

    assert not at.exception

"""A versão das regras gravada na trilha tem que ser a do documento (I5).

`rule_version` só serve para alguma coisa se apontar para uma versão real. Este teste lê o título
de `document/regras-de-risco.md` e falha se alguém publicar uma v2 do documento sem atualizar a
constante — que é justamente o jeito de a trilha mentir sem ninguém perceber.
"""

import re
from pathlib import Path

from app.core.versions import MODEL_VERSION, RULES_DOCUMENT_VERSION, RULES_VERSION

# api/tests/ → api/ → raiz do repositório
RULES_DOCUMENT = Path(__file__).resolve().parents[2] / "document" / "regras-de-risco.md"


def document_version() -> str:
    """Lê o rótulo de versão do título: `# Regras de risco — relevo × clima (v1)`."""
    title = RULES_DOCUMENT.read_text(encoding="utf-8").splitlines()[0]
    match = re.search(r"\((v\d+)\)", title)
    assert match is not None, f"O título do documento não traz a versão: {title!r}"
    return match.group(1)


def test_the_rules_document_exists() -> None:
    assert RULES_DOCUMENT.is_file(), f"Documento das regras não encontrado em {RULES_DOCUMENT}"


def test_the_constant_matches_the_document_title() -> None:
    assert document_version() == RULES_DOCUMENT_VERSION


def test_the_recorded_rule_version_names_the_document() -> None:
    assert f"regras-de-risco/{RULES_DOCUMENT_VERSION}" == RULES_VERSION


def test_the_default_model_version_is_absent() -> None:
    """`MODEL_VERSION` é o **fallback** de uma decisão tomada sem modelo.

    A versão real não mora aqui: ela é lida do artefato em tempo de execução e passada em
    `record_decision(..., model_version=...)`, porque muda a cada retreino da D3 (W13).
    """
    assert MODEL_VERSION is None

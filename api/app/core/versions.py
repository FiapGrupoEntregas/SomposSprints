"""Versões das regras e do modelo que assinam cada decisão registrada (I5).

Toda linha de `decision_log` grava **com qual versão de regra e de modelo** a decisão foi tomada.
Sem isso a trilha não serve para nada: daqui a um mês ninguém sabe se um limite de 10° saiu da
regra de hoje ou de uma anterior.

`RULES_VERSION` **é a versão do documento** `document/regras-de-risco.md`, que se identifica no
título ("Regras de risco — relevo × clima (v1)"). Mudou o documento de versão? Mude aqui também —
`tests/test_versions.py` lê o título do documento e falha se os dois divergirem.
"""

# document/regras-de-risco.md — a versão está no título do documento
RULES_VERSION = "regras-de-risco/v1"

# O rótulo curto que aparece no título do documento, conferido pelo teste.
RULES_DOCUMENT_VERSION = "v1"

# Versão de modelo usada quando a decisão foi tomada **sem** modelo — o caso das regras puras.
# A versão real viaja com a decisão: quem pontua com o modelo (W13) passa a versão lida do
# artefato em `record_decision(..., model_version=...)`, porque ela muda a cada retreino da D3 e
# não pode virar constante no código.
MODEL_VERSION: str | None = None

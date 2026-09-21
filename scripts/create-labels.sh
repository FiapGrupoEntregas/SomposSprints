#!/usr/bin/env bash
# Cria/atualiza as labels padrão do projeto no GitHub (ver document/fluxo-git.md).
# Requer o GitHub CLI autenticado: gh auth login
set -euo pipefail

label() { gh label create "$1" --color "$2" --description "$3" --force; }

# Tipo
label "feature"   "1D76DB" "Nova funcionalidade"
label "task"      "0E8A16" "Tarefa ligada a uma feature"
label "bug"       "D73A4A" "Algo não funciona"
label "docs"      "0075CA" "Documentação"

# Área
label "api"       "5319E7" "Área: api/"
label "front-web" "FBCA04" "Área: front-web/"
label "iot"       "F9D0C4" "Área: iot/"
label "pesquisa"  "C5DEF5" "Pesquisa, casos reais, pitch"

# Prioridade
label "P0"        "B60205" "Obrigatório para a demo"
label "P1"        "D93F0B" "Importante"
label "P2"        "FEF2C0" "Se sobrar tempo"

# Status
label "bloqueado" "000000" "Impedido por dependência"

echo "Labels criadas/atualizadas."

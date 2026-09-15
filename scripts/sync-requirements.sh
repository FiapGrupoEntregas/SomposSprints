#!/usr/bin/env bash
# Mantém uv e pip em sincronia: atualiza o uv.lock e regenera requirements.txt /
# requirements-dev.txt de api/ e front-web/ a partir dele.
#
# Rode sempre que alterar dependências (uv add / uv remove / editar pyproject.toml).
# A CI roda este script e falha se houver diferença (git diff).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

for project in api front-web; do
  echo "==> $project"
  cd "$ROOT/$project"
  uv lock
  uv export --quiet --no-hashes --no-emit-project --no-dev -o requirements.txt
  uv export --quiet --no-hashes --no-emit-project --all-groups -o requirements-dev.txt
done

echo "requirements*.txt atualizados."

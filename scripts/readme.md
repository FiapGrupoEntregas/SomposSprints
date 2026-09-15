Scripts auxiliares do projeto (deploy, manutenção, automações).

| Script | O que faz |
|---|---|
| `sync-requirements.sh` | Atualiza o `uv.lock` e regenera `requirements.txt` / `requirements-dev.txt` de `api/` e `front-web/` (mantém uv e pip em sincronia). A CI verifica. |
| `create-labels.sh` | Cria ou atualiza as labels padrão do GitHub (precisa do `gh` autenticado). |

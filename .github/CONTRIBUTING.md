# Como contribuir

1. Leia [document/README.md](../document/README.md). As regras de ouro estão lá.
2. Escolha uma task no quadro (ou abra uma com o template **Task**), sempre ligada a uma feature de [`feature/`](../feature/README.md).
3. Crie uma branch a partir da `main`: `feat/W2-mapa-de-relevo` ([fluxo-git](../document/fluxo-git.md)).
4. Implemente seguindo os [padrões de código](../document/padroes-de-codigo.md) e os critérios de aceite da feature.
5. Rode as verificações:
   - `cd api && uv run ruff check . && uv run ruff format --check . && uv run pytest`
   - `cd front-web && uv run ruff check . && uv run ruff format --check . && uv run pytest`
   - `cd iot && pio run`
6. **Atualize o `README.md`** (e o README do subprojeto) e o status em `feature/README.md`.
7. Abra o PR preenchendo o template. Peça revisão a 1 pessoa, que vai testar seguindo o "Como testar".
8. Com a CI verde e o PR aprovado, faça **Squash and merge**.

Quem não programa também contribui: pesquisa, casos reais, pitch e **testes de PR** (ver [trilha do time](../feature/T-trilha-do-time.md)).

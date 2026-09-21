# Fluxo de trabalho no Git e no GitHub

## Branches

- `main`: **protegida** e sempre pronta para a demo. Só recebe mudanças por PR.
- Branches curtas (1–2 dias no máximo), criadas a partir da `main`:

| Prefixo | Uso | Exemplo |
|---|---|---|
| `feat/` | feature nova | `feat/W2-mapa-de-relevo` |
| `fix/` | correção | `fix/E3-limite-nao-persiste` |
| `docs/` | documentação | `docs/regras-de-risco-limiares` |
| `chore/` | infra, dependências, CI | `chore/atualiza-streamlit` |
| `test/` | só testes | `test/W3-casos-de-borda` |

Sempre que houver uma feature relacionada, coloque o **ID dela** (W1…W11, E1…E8, I1…I4, T1…T5) no nome da branch.

## Commits (Conventional Commits)

```
<tipo>(<escopo>): <descrição no imperativo, em português>
```

- **tipos**: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `style`
- **escopos**: `api`, `front`, `iot`, `docs`, `feature`, `ci`, `repo`
- Exemplos:
  - `feat(api): adiciona cálculo de inclinação da grade (W2)`
  - `fix(iot): corrige histerese do LED amarelo (E2)`
  - `docs(feature): detalha critérios de aceite do W9`

Faça commits pequenos e frequentes. Nada de `update`, `ajustes` ou `wip` na `main`.

## Pull Requests

1. Abra o PR cedo (pode ser como **Draft**) e preencha o template (`.github/pull_request_template.md`).
2. O **título do PR** segue Conventional Commits, porque vira o commit final (squash).
3. Referencie a issue: `Closes #12`.
4. **1 aprovação** é obrigatória. Quem não programa também pode (e deve) revisar: siga o "Como testar" do PR e confira se o resultado bate com os critérios de aceite da feature.
5. A CI precisa passar: lint, testes, requirements em sincronia e build do firmware.
6. Faça merge com **Squash and merge** e apague a branch depois.

## Issues e tasks

- **Feature**: use o template *Feature* só para propor algo **novo**. As features planejadas já estão em `feature/`.
- **Task**: a unidade de trabalho. Deve caber em **até 1 dia** e sempre aponta para uma feature (`[Task][W3] ...`).
- **Bug**: use o template *Bug*, colando a saída do console como texto (não como print).

### Quadro (GitHub Projects)

`Backlog` → `A fazer` → `Fazendo` → `Revisão` → `Pronto`

Regra: cada pessoa tem **no máximo 2 cards em "Fazendo"** ao mesmo tempo.

### Labels

Crie todas de uma vez com `scripts/create-labels.sh` (precisa do `gh` autenticado).

| Label | Uso |
|---|---|
| `feature` · `task` · `bug` · `docs` | tipo |
| `api` · `front-web` · `iot` · `pesquisa` | área |
| `P0` · `P1` · `P2` | prioridade (ver `feature/README.md`) |
| `bloqueado` | depende de algo que ainda não está pronto |

## Versões e releases

- **SemVer 0.x**, com uma tag por marco:

| Versão | Marco |
|---|---|
| `v0.1.0` | estrutura inicial do repositório |
| `v0.2.0` | relevo e risco no mapa (I1, W1–W3) |
| `v0.3.0` | IoT integrado de ponta a ponta (W4, W5, I2, E1–E4) |
| `v0.4.0` | P1 entregues (subscrição, replay, capotamento…) |
| `v1.0.0` | versão da apresentação final |

- A cada tag, atualize o **"Histórico de lançamentos" do `README.md`**.

## Proteção da `main` (configurar no GitHub)

Settings → Branches → Add rule para `main`:
- ✅ Require a pull request before merging (1 approval)
- ✅ Require status checks to pass (jobs da CI)
- ✅ Do not allow bypassing the above settings

## Congelamento

O **código congela em 25/09**. Depois disso, só entram PRs `fix:` de bugs que afetem a demo, com aprovação de 2 pessoas.

# DOC1 — User Stories e rastreabilidade

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | documentação |
| Depende de | — |
| Janela | 20/09 |
| Responsável | doc-entrega + time |
| Status | ⬜ A fazer |

## Objetivo

O enunciado cobra **aderência às User Stories escolhidas pelo grupo**. Elas precisam estar escritas,
ligadas às features e às evidências de teste.

## Escopo

**Inclui**
- `document/user-stories.md` com as histórias dos 4 perfis citados pelo enunciado:
  - **operador de máquinas** — saber se pode operar hoje e ser avisado na hora do risco;
  - **gestor de frota** — ver onde estão os riscos e planejar a operação da semana;
  - **técnico de manutenção** — ver o histórico de esforço e os eventos da máquina;
  - **analista da seguradora** — avaliar o terreno na cotação e ter contexto objetivo no sinistro.
- Cada história com: papel, desejo, motivo, **critérios de aceite** e prioridade.
- Matriz de rastreabilidade: história → features (IDs) → arquivos → evidência (teste ou print).

**Não inclui**
- Reescrever histórias de sprints anteriores que o grupo não tenha registrado. Se não houver registro, declare que foram consolidadas agora.

## Critérios de aceite

- [ ] Pelo menos 2 histórias por perfil, com critérios verificáveis.
- [ ] Toda história aponta para pelo menos uma feature existente em `feature/`.
- [ ] A matriz mostra o que está pronto, parcial ou não implementado — **sem maquiagem**.
- [ ] O `README.md` referencia o documento.

## Tarefas

- [ ] Escrever as histórias com o time
- [ ] Montar a matriz de rastreabilidade
- [ ] Revisar contra o que o código realmente faz

# DOC1 — User Stories e rastreabilidade

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | documentação |
| Depende de | — |
| Janela | 20/09 |
| Responsável | doc-entrega + time |
| Status | ✅ Concluída (21/09/2026) |

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

- [x] Pelo menos 2 histórias por perfil, com critérios verificáveis. **10 histórias:** operador
      US-01 e US-02; gestor US-03, US-04 e US-09; técnico US-05 e US-06; analista US-07, US-08 e
      US-10.
- [x] Toda história aponta para pelo menos uma feature existente em `feature/`. Declarado na seção
      "Cobertura: features sem história e histórias sem feature" — não há história órfã.
- [x] A matriz mostra o que está pronto, parcial ou não implementado — **sem maquiagem**. O que
      não tem evidência está declarado como ausente: prints (nenhum capturado), desempenho do mapa
      (US-03, critério 7, não medido), conferência dos ângulos no Wokwi e fontes dos limiares (T1).
- [x] O `README.md` referencia o documento (seção 🧭 Funcionalidades).

## Tarefas

- [x] Escrever as histórias com o time
- [x] Montar a matriz de rastreabilidade
- [x] Revisar contra o que o código realmente faz — conferência de 21/09, que atualizou **US-03**
      (tentativa de medição abortada com a cota diária da Open-Meteo esgotada), **US-04** (W10 ⛔ fora do escopo, não
      "não começou"), **US-05** (W11 entregue e aprovada: história passou a ✅) e **US-08** (D2 com
      2.256 linhas e a conclusão do modelo invertida pelo retreino)

## Pendência que não é da feature

A **revisão com o time** (leitura em grupo das histórias) é tarefa humana e não é critério de
aceite desta feature. As histórias foram conferidas contra o código, arquivo por arquivo. A
matriz precisa ser **reconferida no congelamento de 25/09**, como o próprio documento avisa.

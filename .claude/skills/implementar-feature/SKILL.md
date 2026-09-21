---
name: implementar-feature
description: 'Implementa uma feature do AgriShield de ponta a ponta com os agentes do projeto (dev-api, dev-front, dev-iot) e o revisor, em ciclos de implementação → revisão → correção até a aprovação. Use quando o usuário pedir para implementar uma feature pelo ID (ex.: "/implementar-feature W2", "implemente a E2").'
argument-hint: "<ID da feature, ex. W2>"
---

# Implementar feature com agentes

Feature pedida: **$ARGUMENTS**

Você é o **orquestrador**. Você não escreve o código da feature: delega aos agentes dev, manda o
resultado para o revisor e conduz as rodadas até a aprovação. Responda em português (pt-BR).
A dinâmica completa está em `document/fluxo-agentes.md`.

## 1. Preparar

1. Leia `feature/README.md` e o arquivo `feature/$ARGUMENTS-*.md`.
2. Confira as dependências (campo "Depende de"). Se alguma **não** estiver ✅, avise o usuário e pergunte se deve seguir mesmo assim (dá para seguir usando mocks).
3. Confira a branch: se estiver na `main`, crie `feat/$ARGUMENTS-<nome-curto>` antes de qualquer alteração.
4. Marque a feature como 🟨 Em andamento em `feature/README.md`.

## 2. Delegar por camada

Leia o campo "Camadas" da feature e chame os agentes:

| Camada | Agente | Ordem |
|---|---|---|
| api | `dev-api` | primeiro |
| iot | `dev-iot` | pode rodar **em paralelo** com a api (só depende do contrato) |
| front-web | `dev-front` | **depois** da api aprovada (consome os endpoints) |

Prompt para cada agente dev:

> Implemente a sua parte da feature **<ID>** (`feature/<arquivo>.md`), camada **<camada>**.
> Contexto extra: <decisões do usuário, dependências prontas ou mockadas, observações>.
> Siga o seu processo e devolva o relatório no formato padrão.

## 3. Revisar

Quando cada dev terminar, chame o `revisor` com o ID, a camada, o relatório do dev e o número da rodada.

- **✅ APROVADO** → siga para a próxima camada.
- **🔁 ALTERAÇÕES SOLICITADAS** → **continue o mesmo agente dev** (SendMessage com o nome ou ID dele, sem criar um agente novo) mandando a lista numerada de pedidos. Depois chame o revisor de novo, na rodada N+1.
- **No máximo 3 rodadas por camada.** Se na 3ª ainda não estiver aprovado, pare e mostre ao usuário os pontos em aberto e as opções.
- Se um dev reportar uma pendência em outra camada (ex.: o front precisa de um campo novo na API), resolva a pendência com o agente dessa camada **antes** de seguir.

## 4. Fechar

1. Rode a verificação final das camadas tocadas (lint, testes e `pio run`, conforme `CLAUDE.md`).
2. Marque a feature como 🟦 Em revisão em `feature/README.md`. Ela vira ✅ só depois do PR aprovado e mergeado por um humano.
3. Confira se o `README.md` foi atualizado (regra obrigatória do `CLAUDE.md`).
4. Entregue ao usuário:
   - resumo do que foi feito, por camada, e quantas rodadas de revisão cada uma levou;
   - o que o **humano precisa testar** (conferência visual do front e roteiro do Wokwi que os agentes escreveram);
   - pendências e sugestões "fora do escopo" do revisor (candidatas a issue);
   - sugestão de mensagem de commit (Conventional Commits) e título de PR.
5. **Não faça commit nem push** sem o usuário pedir.

# Evidências de validação

Relatórios gerados pelo agente `qa-integracao` (I6) e prints usados na entrega (DOC2).

Padrão de nome: `AAAA-MM-DD-<cenario>.md` (ex.: `2026-09-22-rajada-100-mensagens.md`).
Cada relatório traz o comando executado, a **saída real** e o veredito. Prints em `prints/` e
saídas completas de execução em `logs/`.

| Relatório | O que cobre |
|---|---|
| [2026-09-20-integracao-ponta-a-ponta.md](2026-09-20-integracao-ponta-a-ponta.md) | Os 6 cenários do simulador (I6), queda do broker, tempos da W4 e da W5, exceções e a ressalva D-1 |
| [2026-09-20-ambiente-de-demo.md](2026-09-20-ambiente-de-demo.md) | Clone limpo em 14 s, `run-demo.sh` (chaves, aquecimento, Ctrl+C) e o plano B do broker (I4) |
| [2026-09-21-retomada-d2-e-fechamento-w11-w13.md](2026-09-21-retomada-d2-e-fechamento-w11-w13.md) | Retomada do dataset da D2 (cota × 429), o bug dos scripts que abriam o banco errado e a revisão que aprovou a W11 e a W13 |
| [2026-09-21-degradacao-sem-cota.md](2026-09-21-degradacao-sem-cota.md) | O sistema com a Open-Meteo **realmente** fora do ar: o que responde, o que dá 503, os e2e nesse estado e o que isso exige da demo |

A lista dos 11 prints exigidos pelo entregável 5 está em [prints/README.md](prints/README.md).
**Nenhum foi capturado ainda.** Nenhum depende hoje de feature não implementada: os itens 5, 6 e 7
dependem do Wokwi rodando (tarefa T6) e o item 11 precisa ser capturado **depois** do retreino do
modelo de 21/09, porque o cartão lê versão e métricas do artefato em tempo de execução.

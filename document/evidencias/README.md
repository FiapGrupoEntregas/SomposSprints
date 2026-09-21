# Evidências de validação

Relatórios gerados pelo agente `qa-integracao` (I6) e prints usados na entrega (DOC2).

Padrão de nome: `AAAA-MM-DD-<cenario>.md` (ex.: `2026-09-22-rajada-100-mensagens.md`).
Cada relatório traz o comando executado, a **saída real** e o veredito. Prints em `prints/` e
saídas completas de execução em `logs/`.

| Relatório | O que cobre |
|---|---|
| [2026-09-20-integracao-ponta-a-ponta.md](2026-09-20-integracao-ponta-a-ponta.md) | Os 6 cenários do simulador (I6), queda do broker, tempos da W4 e da W5, exceções e a ressalva D-1 |
| [2026-09-20-ambiente-de-demo.md](2026-09-20-ambiente-de-demo.md) | Clone limpo em 14 s, `run-demo.sh` (chaves, aquecimento, Ctrl+C) e o plano B do broker (I4) |

A lista de prints exigidos pelo entregável 5, com o que já dá para capturar e o que depende de
feature não implementada, está em [prints/README.md](prints/README.md).

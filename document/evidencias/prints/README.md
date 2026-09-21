# Prints de tela para a entrega

O entregável 5 do enunciado pede **prints de scores, tendências por equipamento/região/operação e
alertas**. Esta pasta guarda essas imagens. **Nenhuma foi capturada ainda** (situação em
20/09/2026, fim do dia).

**Padrão de nome:** `AAAA-MM-DD-<tela>-<detalhe>.png` — ex.: `2026-09-25-risco-carmo-dia-vermelho.png`.
PNG, largura mínima de 1600 px, navegador em tela cheia, sem abas pessoais à mostra.

> **Todas as telas da lista abaixo existem e estão capturáveis hoje.** Na revisão da manhã de
> 20/09, os itens 9, 10 e 11 estavam marcados como bloqueados por feature não implementada — as
> features saíram no mesmo dia (W8, W12, W13/D3) e a marcação foi corrigida. São justamente os
> três que o enunciado cobra de forma mais direta em "relatórios e interface": **não pule esses**.

## Lista do que precisa existir

| # | Print | Tela | Depende de | Situação |
|---|---|---|---|---|
| 1 | Mapa de relevo com KPIs e legenda | Mapa de risco → Relevo | W2 (pronta) | ⬜ |
| 2 | Previsão de 7 dias com um dia vermelho aberto e os motivos no tooltip | Mapa de risco → Previsão | W3 (pronta) | ⬜ |
| 3 | Contraste **Carmo de Minas × Sorriso** no mesmo dia (dois prints lado a lado) | Mapa de risco | W1, W3 (prontas) | ⬜ |
| 4 | Card do limite do dia e confirmação de envio ao equipamento | Equipamento ao vivo | W4 (pronta) | ⬜ |
| 5 | Painel ao vivo com telemetria chegando e a lista de eventos | Equipamento ao vivo | W5 + Wokwi | ⬜ |
| 6 | **Alerta de capotamento** fixo no topo, com o gráfico dos 30 s de contexto | Equipamento ao vivo | W5, E5 + Wokwi | ⬜ |
| 7 | Alerta local no dispositivo: LED vermelho, buzzer e OLED | Wokwi | E2, E7 (prontas) | ⬜ |
| 8 | Resposta de `GET /api/v1/audit` com uma linha de `decision_log` | `/docs` | I5 (pronta) | ⬜ |
| 9 | **Perfil de terreno A/B/C**: selo da classe, score 0–100, indicadores e carteira ordenada | Subscrição | W8 (pronta) | ⬜ |
| 10 | **Tendências por equipamento, região e cultura** — um print por aba, com o gráfico e o botão de CSV à vista | Relatórios | W12 (pronta) | ⬜ |
| 11 | **Score híbrido**: cartão da probabilidade do modelo ao lado do nível por regras, **com a ressalva do baseline legível no print** | Mapa de risco | W13, D3 (prontas) | ⬜ |

## Cuidados que valem nota

- **Item 11 é o print mais delicado da entrega.** Ele precisa mostrar o cartão **inteiro**,
  incluindo a linha da AUC-PR e o texto que diz que o modelo **não superou o baseline por
  regras**. Um print cortado, que mostre só a probabilidade, transforma um resultado honesto em
  promessa vazia — e é exatamente o tipo de coisa que a banca pergunta. Role a página antes de
  capturar.
- **O modelo será retreinado antes da entrega.** Se isso acontecer depois de você capturar o item
  11, **refaça o print**: a versão e a métrica no cartão mudam, e um print com número velho vira
  divergência entre a imagem e o artefato.
- **Item 10:** são três abas (Equipamento, Região, Cultura). O enunciado fala em "equipamento,
  região e operação" — a nossa terceira dimensão é **cultura**, que é o recorte de operação que a
  base real do PSR permite. Capture as três.
- **Itens 5, 6 e 7 dependem do Wokwi rodando**, então saem junto com a tarefa T6.
- Se alguma tela não puder ser capturada até o congelamento (25/09), **não simule nem monte a
  imagem**: declare a ausência em [entregaveis.md](../../entregaveis.md) e no vídeo.

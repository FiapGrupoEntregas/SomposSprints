# W11 — Histórico do equipamento (prévia do Passaporte Digital)

| Campo | Valor |
|---|---|
| Prioridade | P2 |
| Camadas | api, front-web |
| Depende de | I3 |
| Janela | só se sobrar tempo |
| Responsável | Dev |
| Status | 🟦 Em revisão (API + seção no front prontas) |

## Objetivo

Mostrar a **semente do Passaporte Digital**: cada alerta seguido ou ignorado vira histórico do
equipamento, útil para renovação, sinistro e revenda. Liga a demo ao roadmap do pitch.

## Escopo

**Inclui**
- Resumo dos últimos N dias: tempo operando, inclinação máxima, % do tempo acima do limite, número de alertas, capotamentos e ocorrências.
- Linha do tempo dos eventos.

**Não inclui**
- Score comportamental, portabilidade na revenda ou assinatura digital (roadmap).

## Implementação

### API (`api/`)
- `app/services/history.py`: `device_history(telemetry, events, days) -> DeviceHistory`. Tempo operando = número de leituras × 5 s.
- `GET /api/v1/devices/{device_id}/history?days=7`

### Front-web (`front-web/`)
- Página **Equipamento ao vivo**, seção **"Passaporte (prévia)"**: KPIs + linha do tempo + o texto "Na versão completa, este histórico acompanha a máquina na renovação e na revenda".

## Critérios de aceite

- [x] Os números batem com os dados gravados (teste com dados sintéticos).
- [x] Sem dados → totais zerados, linha do tempo vazia e **200**, não erro.
- [x] **Nenhuma tabela nova**: tudo sai de `telemetry` e `device_event`, que a I3 já grava.
- [x] O histórico **concorda com o relatório do equipamento** (W12) sobre leituras, horas e tempo acima do limite — um teste compara os dois e falha se divergirem.

### Por que W11 e W12 coexistem sem duplicar

Os dois leem as mesmas linhas e **compartilham as funções** de `app/services/reports.py`
(`operating_hours` e `count_above_limit`), não só as constantes. Compartilhar a função torna a
concordância **estrutural**: não dá para os dois divergirem sem editar a mesma linha, e o teste
cruzado passa a ser rede de segurança em vez de única defesa. O que muda é a pergunta: o W12 responde *"está piorando?"*, com série **por dia** para o gestor de frota; o W11
responde *"o que aconteceu com esta máquina?"*, com **acumulado do período** e linha do tempo —
que é o que acompanha o equipamento na renovação, no sinistro e na revenda.

O campo `roadmap_note` da resposta diz o que o histórico **ainda não é**, para a tela não
prometer o Passaporte completo.

## Tarefas

- [x] Service + testes (18, com dados sintéticos e sem tabela nova)
- [x] Rota `GET /api/v1/devices/{device_id}/history?days=7`
- [x] Seção no front
- [x] Atualizar os READMEs, `document/arquitetura.md` e o status

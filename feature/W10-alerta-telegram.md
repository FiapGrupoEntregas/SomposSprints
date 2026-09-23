# W10 — Alerta pelo Telegram

| Campo | Valor |
|---|---|
| Prioridade | P2 |
| Camadas | api |
| Depende de | I2, E5 |
| Janela | só se sobrar tempo |
| Responsável | Dev |
| Status | ⛔ Fora do escopo (21/09/2026): exige token de bot do Telegram, que o time não tem. O alerta de capotamento continua existindo no painel ao vivo (W5) e na trilha de auditoria (I5) — o que não existe é a notificação **fora** do sistema. |

## Objetivo

Avisar o gestor no celular quando acontecer algo crítico. Na demo, o celular de alguém do time vibra na hora do capotamento.

## Escopo

**Inclui**
- Bot criado no @BotFather. Token e chat id em variáveis de ambiente.
- Mensagem para os eventos `rollover` e `incident_report` (e, opcionalmente, um resumo diário às 06h quando houver 🔴).

**Não inclui**
- WhatsApp: a API oficial é burocrática para 2 semanas.

## Implementação

### API (`api/`)
- Settings: `telegram_bot_token: str | None = None` e `telegram_chat_id: str | None = None`. **Sem token, a feature fica desligada** (e os testes não quebram).
- `app/clients/telegram.py`: `send_message(text) -> None` via `POST https://api.telegram.org/bot{token}/sendMessage` (httpx, timeout de 5 s, erro só vai para o log).
- Chamado no callback de evento (I2/I3) **depois** da deduplicação.
- Mensagem: `🚨 CAPOTAMENTO — tractor-01 (Sítio Café da Serra) às 14:32:05. Inclinação 61°. Abrir painel: <url>`.

## Critérios de aceite

- [ ] O capotamento no Wokwi gera a mensagem no Telegram em ≤ 5 s.
- [ ] O mesmo evento (3 mensagens MQTT) gera **1** mensagem no Telegram.
- [ ] Sem token → nada é enviado e nada quebra.
- [ ] O token não aparece em log nem no repositório.

## Tarefas

- [ ] Criar o bot e o grupo do time
- [ ] Cliente + teste com `MockTransport`
- [ ] Integrar no callback de evento
- [ ] Atualizar `.env.example` e os READMEs

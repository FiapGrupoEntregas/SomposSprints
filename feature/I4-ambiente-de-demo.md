# I4 — Ambiente de demo e deploy

| Campo | Valor |
|---|---|
| Prioridade | P0 (demo local) · P2 (deploy em nuvem) |
| Camadas | todas |
| Depende de | todos os P0 |
| Janela | 23/09 |
| Responsável | Dev + time |
| Status | ⬜ A fazer |

## Objetivo

Garantir que a demo **sempre** sobe, em qualquer máquina do time, em poucos minutos, e que existe um plano B para cada falha.

## Escopo

**Inclui**
- `scripts/run-demo.sh`: sobe a API e o front juntos (dois processos, um só Ctrl+C derruba os dois) e confere o `/health`.
- Checklist e plano B em [docs/demo.md](../docs/demo.md), revisados depois do ensaio 1.
- Um teste de "clone limpo": uma pessoa do time segue o README do zero numa máquina que não é a do dev.

**Opcional (P2): deploy**
- API no Render (plano gratuito, a partir de `api/requirements.txt`). ⚠️ O serviço gratuito "dorme" sem uso, e isso derruba a ponte MQTT. Só use na demo se tiver um ping periódico.
- Front no Streamlit Community Cloud (arquivo principal `front-web/app.py`, com a variável `AGRISHIELD_API_URL` nos secrets).

**Não inclui**
- Docker e Kubernetes: não compensa em 2 semanas.

## Critérios de aceite

- [ ] Do clone à demo rodando em **≤ 10 min** seguindo só o `README.md`.
- [ ] `scripts/run-demo.sh` funciona no Linux e no macOS. Para Windows há instruções equivalentes no README.
- [ ] O checklist do `docs/demo.md` foi executado de ponta a ponta pelo menos 2 vezes.
- [ ] O vídeo de backup foi gravado (T5).

## Tarefas

- [ ] `scripts/run-demo.sh`
- [ ] Teste de clone limpo, feito por alguém do time
- [ ] Ajustar o `docs/demo.md` depois do ensaio
- [ ] (P2) Deploy + atualizar o README com as URLs

# I4 — Ambiente de demo e deploy

| Campo | Valor |
|---|---|
| Prioridade | P0 (demo local) · P2 (deploy em nuvem) |
| Camadas | todas |
| Depende de | todos os P0 |
| Janela | 24/09 |
| Responsável | Dev + time |
| Status | 🟨 Em andamento (software pronto; falta ensaio com Wokwi, macOS e vídeo) |

## Objetivo

Garantir que a demo **sempre** sobe, em qualquer máquina do time, em poucos minutos, e que existe um plano B para cada falha.

## Escopo

**Inclui**
- `scripts/run-demo.sh`: sobe a API e o front juntos (dois processos, um só Ctrl+C derruba os dois) e confere o `/health`.
- Checklist e plano B em [document/demo.md](../document/demo.md), revisados depois do ensaio 1.
- Um teste de "clone limpo": uma pessoa do time segue o README do zero numa máquina que não é a do dev.

**Opcional (P2): deploy**
- API no Render (plano gratuito, a partir de `api/requirements.txt`). ⚠️ O serviço gratuito "dorme" sem uso, e isso derruba a ponte MQTT. Só use na demo se tiver um ping periódico.
- Front no Streamlit Community Cloud (arquivo principal `front-web/app.py`, com a variável `AGRISHIELD_API_URL` nos secrets).

**Não inclui**
- Docker e Kubernetes: não compensa em 2 semanas.

## Critérios de aceite

- [x] Do clone à demo rodando em **≤ 10 min** seguindo só o `README.md`. **14 s medidos** em 20/09/2026 (7 s de `uv sync` com cache frio + 7 s de boot) **num link muito rápido — 635 MB de pacotes em 7 s; numa conexão doméstica o `uv sync` leva alguns minutos, ainda assim com folga larga**. O teste achou 3 lacunas no README, já corrigidas.
- [ ] `scripts/run-demo.sh` funciona no Linux e no macOS. **Linux: validado** (subida, aquecimento, Ctrl+C real derrubando os dois). **macOS: falta rodar** — o script foi escrito para o bash 3.2, mas não tenho Mac aqui. **Windows: instruções no README** (WSL ou dois terminais do PowerShell). 
- [ ] O checklist do `document/demo.md` foi executado de ponta a ponta pelo menos 2 vezes. **Falta**: a parte de software foi executada, mas os passos 4 e 5 (Wokwi) dependem de pessoa.
- [ ] O vídeo de backup foi gravado (T5).

## Tarefas

- [x] `scripts/run-demo.sh` — confere ambiente e as duas chaves, sobe API + front, espera o `/health`, `--aquecer`, `--conferir`, `--sem-front`, Ctrl+C derruba os dois
- [x] Teste de clone limpo — feito pelo `qa-integracao` em 20/09/2026 ([evidência](../document/evidencias/2026-09-20-ambiente-de-demo.md)); falta repetir numa máquina que não é a do dev
- [x] Ajustar o `document/demo.md` — checklist e plano B revisados com o que a I6 mostrou (chaves, 20 s de silêncio, reconexão, porta presa, troca de broker)
- [ ] (P2) Deploy + atualizar o README com as URLs — **não começou** (a demo é local, por decisão)

## O que ficou pronto (20/09/2026)

| Item | Estado |
|---|---|
| `scripts/run-demo.sh` | ✅ Linux; ⚠️ macOS por rodar |
| Clone limpo ≤ 10 min | ✅ 14 s |
| README: passo 0 (um comando), chaves e Windows | ✅ |
| `document/demo.md`: checklist e plano B | ✅ revisados |
| Plano B do broker (`test.mosquitto.org`) | ✅ lado da API validado; ⚠️ firmware por testar |
| Ensaio completo com Wokwi (2×) e vídeo (T5) | ⬜ dependem de pessoa |

**Defeito encontrado e já resolvido:** o front não lia `front-web/.env` (faltava `python-dotenv`),
apesar de o `.env.example` e o README sugerirem que sim — o botão "Enviar ao equipamento" ficava em
401 para quem seguisse a documentação. **Corrigido em 20/09/2026 pelo `dev-front`**
(`python-dotenv` + `load_dotenv(ENV_FILE)` em `front-web/config.py`, coberto por
`front-web/tests/test_config.py`). O `run-demo.sh` segue lendo os dois `.env` e repassando, o que
continua valendo para quem prefere variáveis de ambiente.

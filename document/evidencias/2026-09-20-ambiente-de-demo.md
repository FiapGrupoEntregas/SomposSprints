# Ambiente de demo: clone limpo, `run-demo.sh` e plano B — 20/09/2026

**Feature:** [I4 — Ambiente de demo e deploy](../../feature/I4-ambiente-de-demo.md)
**Executado por:** agente `qa-integracao`
**Veredito:** ⚠️ **validado com ressalvas** — a demo sobe com um comando e o tempo do clone limpo
ficou **muito** abaixo do teto, mas achei **duas falhas de configuração que só apareceriam no
palco** (uma delas de produção) e três critérios dependem de pessoa (Wokwi, macOS, vídeo).

---

## 1. Teste de "clone limpo"

**Critério:** do clone à demo rodando em **≤ 10 min**, seguindo só o `README.md`.

**Como fiz:** copiei a árvore de trabalho para um diretório temporário fora do repositório,
**sem** `.git`, `.venv`, `__pycache__`, `*.db`, `data/raw/` e caches — ou seja, o que uma pessoa do
time recebe depois de `git clone`, já com o trabalho desta sprint. Depois segui o README ao pé da
letra, com **cache do uv vazio** (`UV_CACHE_DIR` novo), que é a situação de máquina nova.

```
$ rsync -a --exclude='.git/' --exclude='.venv/' --exclude='*.db' ... ./ /tmp/.../clone-limpo/
cópia do 'clone' em 1s

$ cd api && uv sync            # UV_CACHE_DIR vazio
API uv sync (cache frio): 5 s
$ cd front-web && uv sync
FRONT uv sync (cache frio): 2 s
TOTAL sync: 7 s                # 635 MB de cache baixados

$ cd api && uv run fastapi dev app/main.py
API pronta em 5 s (código 200)
{"status":"ok","version":"0.1.0","environment":"dev"}
$ cd front-web && uv run streamlit run app.py
Front pronto em 2 s (código 200)

TEMPO TOTAL do 'clone' à demo no ar: 14 s
```

✅ **14 s contra um teto de 10 min.** Ressalva honesta: os 7 s de `uv sync` são de um link muito
rápido (635 MB em 7 s). Numa conexão doméstica os mesmos pacotes levam alguns minutos — ainda
assim, com folga larga para os 10 min. Nada além de `uv` e `curl` foi necessário; o banco do PSR
(~420 MB) **não** é preciso para a demo.

### O que faltou no README (é o que o teste serve para achar)

| # | O que eu precisei saber e não estava escrito | Situação |
|---|---|---|
| G-1 | Que é preciso criar `api/.env` com `AGRISHIELD_API_KEYS` **e** passar `AGRISHIELD_API_KEY` ao front. Seguindo só o README, o passo 3 da demo (botão "Enviar ao equipamento") responde **401** | **Corrigido** no README (seção "Demo completa com um comando" + passo 2) e no `document/demo.md` |
| G-2 | Que o front **não lê `front-web/.env`** — é preciso `export` | **Documentado** (README e demo.md) e contornado pelo `run-demo.sh`; a correção de produção é do `dev-front` (ver defeitos) |
| G-3 | Que não havia instruções para **Windows** | **Corrigido** no README (WSL ou dois terminais do PowerShell, com as variáveis) |

Prova do G-1, no clone, seguindo só o README:

```
$ curl -s -o /dev/null -w 'POST /limit/publish -> %{http_code}\n' \
    -X POST http://127.0.0.1:8124/api/v1/devices/tractor-01/limit/publish -d '{}'
POST /limit/publish -> 401
```

Prova do G-2, com o `.env` do front **presente e preenchido**:

```
$ cd front-web && cat .env | grep API_KEY
AGRISHIELD_API_KEY=chave-do-env
$ uv run python -c "import config; print('config.API_KEY =', repr(config.API_KEY))"
config.API_KEY = ''
```

---

## 2. `scripts/run-demo.sh`

### Conferência do ambiente (as duas variáveis)

Sem nenhum `.env` — o estado de quem acabou de clonar:

```
$ ./scripts/run-demo.sh --conferir
1/5 · Conferindo o ambiente
✔ uv 0.11.17 e curl encontrados
⚠  AGRISHIELD_API_KEYS está vazia (api/.env). Sem ela a API recusa QUALQUER escrita (401):
⚠     o passo 3 da demo — o botão "Enviar ao equipamento" — não vai funcionar.
⚠     Corrija com: cp api/.env.example api/.env  (e escolha uma chave)
⚠  AGRISHIELD_API_KEY está vazia (front-web/.env). O front não manda chave nenhuma e o
⚠     botão "Enviar ao equipamento" responde 401.
⚠     Corrija com: cp front-web/.env.example front-web/.env
⚠  A demo sobe assim mesmo — só o passo 3 (enviar limite ao ESP32) fica fora.
exit=1
```

Com as duas presentes mas **diferentes** — a pegadinha que só aparece no palco:

```
$ AGRISHIELD_API_KEYS="chave-a,chave-b" AGRISHIELD_API_KEY="chave-c" ./scripts/run-demo.sh --conferir
✖ AGRISHIELD_API_KEY (front) NÃO está na lista AGRISHIELD_API_KEYS (API).
✖    É exatamente a falha que aparece só no palco: leitura funciona, envio dá 401.
exit=1
```

Compatíveis (e com espaço sobrando na lista, que é fácil de deixar):

```
$ AGRISHIELD_API_KEYS="chave-a, chave-b" AGRISHIELD_API_KEY="chave-b" ./scripts/run-demo.sh --conferir
✔ As duas chaves são compatíveis (AGRISHIELD_API_KEY está em AGRISHIELD_API_KEYS)
exit=0
```

### Leitura do `.env`: BOM do Windows e nome de variável inválido

*(acrescentado em 20/09/2026, depois da revisão — os dois casos nasceram de uma regressão introduzida
e corrigida no mesmo dia; ficam aqui porque são os que alguém vai querer repetir quando um `.env`
vindo do Windows der problema.)*

Um `.env` salvo no Notepad começa com três bytes invisíveis (BOM UTF-8) que grudam no nome da
**primeira** variável. Se essa variável for a chave de API, recusar a linha deixaria a demo sem chave
— por isso o script **consome** o BOM em vez de recusá-lo:

```
$ printf '\xef\xbb\xbfAGRISHIELD_API_KEY=chave-com-bom\nAGRISHIELD_MQTT_HOST=host-a\n' > /tmp/bom.env
   (como front-web/.env)
✔ chave carregada, sem aviso
   Broker MQTT: host-a:1883
```

Nome que não é identificador de shell: **avisa e segue**. O defeito que este caso pega não é a linha
ruim ser ignorada — é tudo o que vem **depois** dela sumir em silêncio:

```
CHAVE.RUIM=qualquer
AGRISHIELD_API_KEY=chave-depois-da-linha-ruim
AGRISHIELD_API_URL=http://depois:9000
```
```
⚠ Ignorando linha com nome de variável inválido em front-web/.env: CHAVE.RUIM
✔ chave carregada: chave-depois-da-linha-ruim
✔ URL: http://depois:9000
```

### Subida completa, com aquecimento

```
$ ./scripts/run-demo.sh --aquecer
1/5 · Conferindo o ambiente
✔ As duas chaves são compatíveis (AGRISHIELD_API_KEY está em AGRISHIELD_API_KEYS)
2/5 · Subindo a API (porta 8123)
✔ API respondeu em 2 s — http://127.0.0.1:8123/api/v1/health
✔ A API aceitou a chave do front (200 em /audit): o botão "Enviar ao equipamento" vai passar
3/5 · Aquecendo o cache das fazendas (Open-Meteo)
✔ cafe-carmo-de-minas/terrain em 1 s
✔ cafe-carmo-de-minas/risk em 0 s
✔ graos-sorriso/terrain em 0 s
✔ graos-sorriso/risk em 0 s
✔ uva-serra-gaucha/terrain em 0 s
✔ uva-serra-gaucha/risk em 1 s
✔ Aquecimento concluído em 2 s
4/5 · Subindo o front (porta 8511)
✔ front-web respondeu em 1 s — http://127.0.0.1:8511
5/5 · Demo no ar
```

O teste da chave é um `GET /api/v1/audit?limit=1` autenticado: prova o caminho inteiro
(front → API → `require_api_key`) **sem** gastar cota da Open-Meteo e **sem** publicar nada no
broker — importante, porque o `POST .../limit/publish` publicaria um `config` retained no tópico do
equipamento da demo.

O aquecimento faz **duas** chamadas por fazenda (relevo e previsão), seis no total, e imprime o
tempo de cada uma. Rodar na véspera e 30 min antes cobre os dois caches (24 h e 1 h).

### Um Ctrl+C derruba os dois

Testado com um Ctrl+C **de verdade**: o script foi iniciado num terminal (pty) e recebeu o byte
`0x03`, como se alguém tivesse apertado as teclas.

```
[1mCtrl+C derruba os dois processos.[0m
^C
Derrubando a demo
Encerrando front-web (pid 197428)…
Encerrando API (pid 197374)…
Logs desta execução: /tmp/agrishield-demo.X9AObj

>>> script terminou com codigo 130

$ pgrep -af "uvicorn app.main|streamlit run app.py"   # nenhum dos dois
$ ss -ltn | grep -E "8123|8511"                        # nenhuma porta presa
```

Detalhe que custou uma tentativa: `uv run` cria um processo **filho** (o uvicorn/streamlit de
verdade). Matar só o PID do `uv` deixaria o filho vivo segurando a porta — o "Address already in
use" do segundo ensaio. O script liga job control (`set -m`), o que dá a cada filho o seu próprio
grupo de processos, e mata **o grupo** (`kill -TERM -$pid`), com `KILL` depois de 10 s.
Verificado também pela via do `SIGTERM` (fechar o terminal): mesmo resultado.

---

## 3. Plano B do broker — `test.mosquitto.org`

Trocar de broker é mudar **dois** valores, um de cada lado:

| Onde | O quê |
|---|---|
| `api/.env` | `AGRISHIELD_MQTT_HOST=test.mosquitto.org` (porta continua 1883) |
| `iot/src/main.ino`, linha 37 | `const char* MQTT_HOST = "test.mosquitto.org";` |

O prefixo dos tópicos **não** muda. Testei o lado da API com o simulador de dispositivo (I6):

```
$ AGRISHIELD_MQTT_HOST=test.mosquitto.org uv run uvicorn app.main:app --port 8125
API (test.mosquitto.org) health=200
$ grep -c "Assinado:" api.log
3
$ uv run python scripts/simulate_device.py --scenario normal --count 3 --host test.mosquitto.org ...
[sim] telemetria seq=1 roll=4.2° nível=green
[sim] telemetria seq=2 roll=5.1° nível=green
[sim] telemetria seq=3 roll=6.3° nível=green
linhas de telemetria gravadas: 3
status: [('tractor-02', 'online', '2026-09-20 15:07:41.042672')]
```

✅ **Lado da API: funciona sem nenhuma outra mudança.** ⚠️ **Lado do firmware: não testado** — é
uma troca de constante e recompilar, mas só o Wokwi prova. Está no plano B do `document/demo.md`
com as duas linhas exatas.

---

## Adendo (mesmo dia) — aquecimento do resumo do replay (W9)

Depois deste ensaio, a W9 passou a expor `GET /api/v1/replay/summary`, que roda os cinco casos
reais de uma vez: **a frio são 5 elevações (9 pontos cada) + 5 históricos da Open-Meteo**. Entrou
no `--aquecer`, por último, para as fazendas já estarem quentes se ele demorar. Medido com um
processo de API novo (cache em memória vazio):

```
3/5 · Aquecendo os caches (Open-Meteo)
✔ cafe-carmo-de-minas/terrain em 1 s
✔ cafe-carmo-de-minas/risk em 0 s
✔ graos-sorriso/terrain em 0 s
✔ graos-sorriso/risk em 1 s
✔ uva-serra-gaucha/terrain em 0 s
✔ uva-serra-gaucha/risk em 0 s
✔ replay/summary (5 casos reais) em 3 s
✔ Aquecimento concluído em 5 s
```

Depois de aquecido, no mesmo processo:

```
tentativa 1: 200 em 0.005149s
tentativa 2: 200 em 0.005829s
tentativa 3: 200 em 0.008718s
```

**A frio 3 s, aquecido ~6 ms** — e o aquecimento inteiro (7 chamadas) passou de 2 s para **5 s**.
Conclusão para o roteiro: o resumo do replay **pode ficar na demo ao vivo**; 3 s no pior caso desta
máquina não justificam cortá-lo. O que continua valendo é a regra do processo: o cache vive dentro
da instância da API, então quem conta é o `--aquecer` da instância que ficar de pé.

Detalhes do comportamento: prazo de 180 s (a frio são 10 chamadas externas), 503 tratado como
aviso — igual às fazendas — e estouro de prazo também vira aviso. **Aquecimento não é
pré-requisito: nada aqui impede a demo de subir.**

## Defeitos encontrados

1. 🟡 **`front-web/config.py` + `front-web/.env.example` — o `.env` do front é um arquivo morto.**
   O front lê as variáveis só com `os.getenv`, e nada carrega `front-web/.env` (não há
   `python-dotenv` nas dependências). Mesmo assim, `.env.example` existe e o `front-web/README.md`
   termina a seção da chave com "Veja `.env.example`", o que leva a pessoa a criar o arquivo e
   concluir que configurou — e o botão "Enviar ao equipamento" continua dando 401. Reproduzir: o
   trecho do G-2 acima. **Corrige:** `dev-front` (adicionar `python-dotenv` e um `load_dotenv()` no
   `config.py`, ou tirar o `.env.example` e dizer no README que é `export`). Contornado no
   `run-demo.sh`, que lê os dois arquivos e repassa.
   **Fechamento — corrigido em 20/09/2026 pelo `dev-front`:** `python-dotenv` +
   `load_dotenv(ENV_FILE)` em `front-web/config.py`, com `front-web/tests/test_config.py`. O texto
   acima fica como está: é o registro do que foi observado neste ensaio.
2. ⚪ **Observação (não é defeito):** sem `.env` nenhum a API sobe e o painel funciona inteiro — só
   a escrita é recusada. É a falha fechada da I5 funcionando, e é por isso que o `run-demo.sh`
   **avisa mas não impede** a subida.

## Verificação manual necessária (humano)

1. **Ensaio completo com o Wokwi, duas vezes** (critério da I4). Eu rodo o software; os passos 4 e
   5 do roteiro dependem do simulador no navegador.
2. **`run-demo.sh` no macOS.** Escrevi para o bash 3.2 (sem `mapfile`, sem array associativo, sem
   `${var,,}`) e só tenho Linux aqui. Precisa de alguém com Mac rodando
   `./scripts/run-demo.sh --conferir` e depois a subida completa.
3. **Plano B do broker no firmware:** trocar a linha 37 do `main.ino`, recompilar e confirmar
   `[mqtt] conectado` no Serial com o `test.mosquitto.org`.
4. **Vídeo de backup (T5)** e os prints (DOC2) continuam pendentes.

## Arquivos desta evidência

- `scripts/run-demo.sh` — o script validado aqui
- Saídas coladas acima, todas desta data

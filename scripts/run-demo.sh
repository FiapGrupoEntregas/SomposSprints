#!/usr/bin/env bash
# Sobe a demo do AgriShield: API + front, com um Ctrl+C só para derrubar os dois (I4).
#
# Uso:
#   ./scripts/run-demo.sh                # sobe API e front e espera
#   ./scripts/run-demo.sh --aquecer      # aquece o cache das 3 fazendas antes de liberar a demo
#   ./scripts/run-demo.sh --sem-front    # só a API (útil com o Wokwi ou o simulador)
#   ./scripts/run-demo.sh --conferir     # confere o ambiente e sai, sem subir nada
#   ./scripts/run-demo.sh --porta-api 8010 --porta-front 8510   # portas (padrão 8000 e 8501)
#   ./scripts/run-demo.sh --ajuda
#
# As portas também saem de AGRISHIELD_DEMO_API_PORT e AGRISHIELD_DEMO_FRONT_PORT.
#
# Compatível com Linux e macOS (o bash do macOS é o 3.2: nada de `mapfile`, array associativo
# ou `${var,,}` aqui). Windows: ver a seção "Demo" do README.md.

set -u

# --- valores padrão ---------------------------------------------------------------------------

API_PORT="${AGRISHIELD_DEMO_API_PORT:-8000}"
FRONT_PORT="${AGRISHIELD_DEMO_FRONT_PORT:-8501}"
WARM_UP=0
START_FRONT=1
CHECK_ONLY=0
HEALTH_TIMEOUT_S=90
FRONT_TIMEOUT_S=90
# Aquecimento: prazos folgados de propósito, porque a frio cada um dispara chamadas externas.
FARM_WARM_TIMEOUT_S=60
# `replay/summary` roda os 5 casos de uma vez (5 elevações + 5 históricos) — é o mais caro (W9).
REPLAY_WARM_TIMEOUT_S=180

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agrishield-demo.XXXXXX")"
API_LOG="$LOG_DIR/api.log"
FRONT_LOG="$LOG_DIR/front.log"

API_PID=""
FRONT_PID=""

# Cores só quando a saída é um terminal (num log elas só atrapalham).
if [ -t 1 ]; then
  BOLD=$(printf '\033[1m'); RED=$(printf '\033[31m'); GREEN=$(printf '\033[32m')
  YELLOW=$(printf '\033[33m'); RESET=$(printf '\033[0m')
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

info()  { printf '%s\n' "$*"; }
ok()    { printf '%s✔%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()  { printf '%s⚠%s  %s\n' "$YELLOW" "$RESET" "$*"; }
fail()  { printf '%s✖%s %s\n' "$RED" "$RESET" "$*"; }
title() { printf '\n%s%s%s\n' "$BOLD" "$*" "$RESET"; }

usage() {
  sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --- argumentos -------------------------------------------------------------------------------

while [ $# -gt 0 ]; do
  case "$1" in
    --aquecer)    WARM_UP=1 ;;
    --sem-front)  START_FRONT=0 ;;
    --conferir)   CHECK_ONLY=1 ;;
    --porta-api)   shift; API_PORT="${1:-}" ;;
    --porta-front) shift; FRONT_PORT="${1:-}" ;;
    --ajuda|-h|--help) usage; exit 0 ;;
    *) fail "Opção desconhecida: $1"; usage; exit 2 ;;
  esac
  shift
done

# --- encerramento: um Ctrl+C derruba os dois ----------------------------------------------------

# Job control ligado: cada processo em segundo plano ganha o **próprio grupo**, e aí dá para matar
# o grupo inteiro. Sem isso, `kill` no `uv` deixaria o uvicorn (ou o streamlit) órfão segurando a
# porta — o erro clássico de "Address already in use" no segundo ensaio.
set -m

stop_process() {
  name="$1"; pid="$2"
  [ -z "$pid" ] && return 0
  kill -0 "$pid" 2>/dev/null || return 0
  info "Encerrando $name (pid $pid)…"
  kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  # Espera até 10 s pelo encerramento limpo (a API precisa fechar a ponte MQTT).
  i=0
  while [ $i -lt 100 ]; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.1
    i=$((i + 1))
  done
  warn "$name não saiu no prazo; mandando KILL."
  kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}

cleanup() {
  trap '' INT TERM
  # Nada iniciado (por exemplo `--conferir` ou erro no passo 1): sai calado.
  if [ -z "$API_PID" ] && [ -z "$FRONT_PID" ]; then
    rmdir "$LOG_DIR" 2>/dev/null || true
    return 0
  fi
  title "Derrubando a demo"
  stop_process "front-web" "$FRONT_PID"
  stop_process "API" "$API_PID"
  info "Logs desta execução: $LOG_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- utilidades -------------------------------------------------------------------------------

require_command() {
  command -v "$1" >/dev/null 2>&1 && return 0
  fail "'$1' não está instalado. $2"
  exit 1
}

# Lê um .env simples (CHAVE=valor) e exporta o que ainda não veio do ambiente. O que já está
# exportado **ganha**, para dar para sobrescrever na linha de comando sem editar arquivo.
load_env_file() {
  file="$1"
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    case "$line" in
      *=*) ;;
      *) continue ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    # tira espaços das pontas e aspas em volta do valor
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    value="$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")"
    # BOM do UTF-8: um `.env` salvo no Notepad começa com 3 bytes invisíveis, que grudam no nome da
    # **primeira** variável. É consumido em silêncio, e não só recusado — se a primeira variável for
    # a chave de API, recusar deixaria a demo sem chave, que é justamente o que este script evita.
    key="${key#$'\xef\xbb\xbf'}"
    [ -z "$key" ] && continue
    # Nome que não é identificador de shell: **avisa e segue**. Sem esta guarda, a indireção
    # `${!key}` abaixo é erro fatal de expansão e o bash **abandona o laço**, descartando em
    # silêncio tudo o que viesse depois no arquivo — inclusive a chave de API.
    case "$key" in
      [!A-Za-z_]*|*[!A-Za-z0-9_]*)
        warn "Ignorando linha com nome de variável inválido em $file: $key"
        continue ;;
    esac
    # Indireção do bash (`${!key}`) em vez de `eval`: uma linha estranha no .env não vira comando.
    if [ -z "${!key:-}" ]; then
      export "$key=$value"
    fi
  done < "$file"
}

# Devolve **um** código HTTP, sempre com 3 dígitos. Quando o curl falha (tempo esgotado, conexão
# recusada, DNS), ele já imprime `000` e sai com status ≠ 0 — por isso a saída é descartada e o
# `000` sai daqui, uma vez só: concatenar os dois viraria `000000`, que não casa com nada e ainda
# aparece com cara de defeito justo quando a demo está lenta.
#
# O `--max-time 10` é só o **padrão**: quem chama pode passar outro `--max-time` depois, e o curl
# usa a última ocorrência (é assim que o aquecimento do replay consegue os 180 s).
http_code() {
  out=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" 2>/dev/null) || out=""
  printf '%s' "${out:-000}"
}

# Um processo que morre em segundos não é "não respondeu no prazo" — é erro no boot, e quase
# sempre a porta ocupada por um ensaio anterior. Dizer isso na hora poupa minutos de pânico.
report_died() {
  who="$1"; log="$2"; port="$3"
  fail "$who morreu durante o boot, antes de responder. Últimas linhas do log:"
  tail -n 25 "$log" || true
  if grep -qi "address already in use\|endereço já em uso\|Errno 98" "$log" 2>/dev/null; then
    fail "→ A porta $port já está ocupada (sobrou processo de outro ensaio?)."
    fail "   Use outra porta: ./scripts/run-demo.sh --porta-api 8010 --porta-front 8510"
    fail "   Ou descubra quem está nela: lsof -i :$port   (Linux/macOS)"
  fi
}

# Uma requisição de aquecimento: mede, e **nunca** derruba a demo. Aquecimento é conforto, não
# pré-requisito — se a Open-Meteo estiver fora agora, o que era para ser rápido no palco fica
# lento, e isso é um aviso, não um erro fatal.
warm_up_get() {
  label="$1"; timeout_s="$2"; url="$3"
  start=$(date +%s)
  code=$(http_code --max-time "$timeout_s" "$url")
  now=$(date +%s)
  elapsed=$((now - start))
  case "$code" in
    200) ok "$label em $elapsed s" ;;
    503) warn "$label devolveu 503 (Open-Meteo fora ou cota estourada) em $elapsed s" ;;
    000) warn "$label sem resposta em $elapsed s (tempo esgotado ou conexão recusada); seguindo" ;;
    *)   warn "$label devolveu $code em $elapsed s" ;;
  esac
}

wait_for_http() {
  url="$1"; timeout_s="$2"; label="$3"; pid="$4"
  started=$(date +%s)
  while :; do
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      return 2
    fi
    if [ "$(http_code "$url")" = "200" ]; then
      now=$(date +%s)
      ok "$label respondeu em $((now - started)) s — $url"
      return 0
    fi
    now=$(date +%s)
    if [ $((now - started)) -ge "$timeout_s" ]; then
      return 1
    fi
    sleep 0.5
  done
}

# --- 1. ambiente ---------------------------------------------------------------------------------

title "1/5 · Conferindo o ambiente"

require_command curl "Instale o curl (ele confere o /health)."
require_command uv "Instale o uv: https://docs.astral.sh/uv/ (ou suba API e front à mão, ver README.md)."
ok "uv $(uv --version 2>/dev/null | awk '{print $2}') e curl encontrados"

load_env_file "$REPO_ROOT/api/.env"
load_env_file "$REPO_ROOT/front-web/.env"

API_KEYS="${AGRISHIELD_API_KEYS:-}"
FRONT_KEY="${AGRISHIELD_API_KEY:-}"
KEYS_OK=1

if [ -z "$API_KEYS" ]; then
  KEYS_OK=0
  warn "AGRISHIELD_API_KEYS está vazia (api/.env). Sem ela a API recusa QUALQUER escrita (401):"
  warn "   o passo 3 da demo — o botão \"Enviar ao equipamento\" — não vai funcionar."
  warn "   Corrija com: cp api/.env.example api/.env  (e escolha uma chave)"
fi

if [ -z "$FRONT_KEY" ]; then
  KEYS_OK=0
  warn "AGRISHIELD_API_KEY está vazia (front-web/.env). O front não manda chave nenhuma e o"
  warn "   botão \"Enviar ao equipamento\" responde 401."
  warn "   Corrija com: cp front-web/.env.example front-web/.env"
fi

if [ -n "$API_KEYS" ] && [ -n "$FRONT_KEY" ]; then
  # A API aceita uma lista separada por vírgula; o front usa **uma** dela. As vírgulas nas pontas
  # evitam casar um pedaço de outra chave, e o `-F` compara **texto literal**: sem ele, uma chave
  # com `.`, `*` ou `[` viraria expressão regular e a conferência daria falso positivo — justo a
  # conferência que existe para o 401 não aparecer no palco.
  if printf '%s' ",$API_KEYS," | tr -d ' ' | grep -qF ",$FRONT_KEY,"; then
    ok "As duas chaves são compatíveis (AGRISHIELD_API_KEY está em AGRISHIELD_API_KEYS)"
  else
    KEYS_OK=0
    fail "AGRISHIELD_API_KEY (front) NÃO está na lista AGRISHIELD_API_KEYS (API)."
    fail "   É exatamente a falha que aparece só no palco: leitura funciona, envio dá 401."
  fi
fi

if [ "$KEYS_OK" -eq 0 ]; then
  warn "A demo sobe assim mesmo — só o passo 3 (enviar limite ao ESP32) fica fora."
fi

info "Broker MQTT: ${AGRISHIELD_MQTT_HOST:-broker.hivemq.com}:${AGRISHIELD_MQTT_PORT:-1883}"
info "Prefixo dos tópicos: ${AGRISHIELD_MQTT_TOPIC_PREFIX:-agrishield/fiap-sompo-2026}"
info "Logs desta execução: $LOG_DIR"

if [ "$CHECK_ONLY" -eq 1 ]; then
  title "Só conferência (--conferir): nada foi iniciado."
  [ "$KEYS_OK" -eq 1 ] || exit 1
  exit 0
fi

# --- 2. API ---------------------------------------------------------------------------------------

title "2/5 · Subindo a API (porta $API_PORT)"

( cd "$REPO_ROOT/api" && exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" ) \
  >"$API_LOG" 2>&1 &
API_PID=$!

HEALTH_URL="http://127.0.0.1:$API_PORT/api/v1/health"
wait_for_http "$HEALTH_URL" "$HEALTH_TIMEOUT_S" "API" "$API_PID"
case $? in
  0) ;;
  2) report_died "A API" "$API_LOG" "$API_PORT"; exit 1 ;;
  *) fail "A API não respondeu em $HEALTH_TIMEOUT_S s. Últimas linhas do log:"
     tail -n 25 "$API_LOG" || true
     exit 1 ;;
esac

# A chave, na prática: uma leitura protegida (barata, sem broker e sem Open-Meteo).
if [ -n "$FRONT_KEY" ]; then
  code=$(http_code -H "X-API-Key: $FRONT_KEY" "http://127.0.0.1:$API_PORT/api/v1/audit?limit=1")
  case "$code" in
    200)
      ok "A API aceitou a chave do front (200 em /audit): o botão \"Enviar ao equipamento\" vai passar"
      ;;
    000)
      # Sem código HTTP não dá para dizer nada sobre a chave: o problema é outro, e dizer "000"
      # só assustaria.
      warn "A API não respondeu à conferência da chave (tempo esgotado ou conexão recusada)."
      warn "   A chave pode estar certa; o que não deu para confirmar foi a resposta."
      ;;
    *)
      fail "A API respondeu $code para a chave do front em /audit — o envio do limite vai falhar."
      ;;
  esac
fi

# --- 3. aquecimento do cache (opcional) -----------------------------------------------------------

if [ "$WARM_UP" -eq 1 ]; then
  title "3/5 · Aquecendo os caches (Open-Meteo)"
  info "Uma chamada de relevo e uma de previsão por fazenda, mais o resumo do replay (W9)."
  total_start=$(date +%s)

  farms=$(curl -s --max-time 30 "http://127.0.0.1:$API_PORT/api/v1/farms" \
    | tr ',' '\n' | grep '"id"' | sed -e 's/.*"id"[[:space:]]*:[[:space:]]*"//' -e 's/".*//')
  if [ -z "$farms" ]; then
    warn "Não deu para listar as fazendas; pulei o aquecimento delas."
  else
    for farm in $farms; do
      for path in terrain risk; do
        warm_up_get "$farm/$path" "$FARM_WARM_TIMEOUT_S" \
          "http://127.0.0.1:$API_PORT/api/v1/farms/$farm/$path"
      done
    done
  fi

  # W9 — o endpoint mais caro do projeto a frio: os cinco replays de uma vez, com 5 elevações e 5
  # históricos da Open-Meteo. Depois de aquecido responde em milissegundos (cache de 24 h no relevo
  # e de 7 dias no histórico). Vem por último de propósito: se demorar, as fazendas já aqueceram.
  warm_up_get "replay/summary (5 casos reais)" "$REPLAY_WARM_TIMEOUT_S" \
    "http://127.0.0.1:$API_PORT/api/v1/replay/summary"

  total_end=$(date +%s)
  ok "Aquecimento concluído em $((total_end - total_start)) s"
  info "Repita 30 min antes da apresentação: a previsão tem cache de 1 h, e o cache vive **dentro**"
  info "do processo da API — reiniciou a API, o aquecimento recomeça do zero (document/demo.md)."
else
  title "3/5 · Aquecimento do cache: pulado"
  info "Rode com --aquecer na véspera e 30 min antes (document/demo.md)."
fi

# --- 4. front -------------------------------------------------------------------------------------

if [ "$START_FRONT" -eq 1 ]; then
  title "4/5 · Subindo o front (porta $FRONT_PORT)"
  (
    cd "$REPO_ROOT/front-web" \
      && AGRISHIELD_API_URL="${AGRISHIELD_API_URL:-http://127.0.0.1:$API_PORT}" \
         AGRISHIELD_API_KEY="$FRONT_KEY" \
         exec uv run streamlit run app.py \
           --server.port "$FRONT_PORT" --server.headless true --server.address 127.0.0.1
  ) >"$FRONT_LOG" 2>&1 &
  FRONT_PID=$!

  wait_for_http "http://127.0.0.1:$FRONT_PORT" "$FRONT_TIMEOUT_S" "front-web" "$FRONT_PID"
  case $? in
    0) ;;
    2) report_died "O front" "$FRONT_LOG" "$FRONT_PORT"; exit 1 ;;
    *) fail "O front não respondeu em $FRONT_TIMEOUT_S s. Últimas linhas do log:"
       tail -n 25 "$FRONT_LOG" || true
       exit 1 ;;
  esac
else
  title "4/5 · Front: não iniciado (--sem-front)"
fi

# --- 5. pronto ------------------------------------------------------------------------------------

title "5/5 · Demo no ar"
info "  API .......... http://127.0.0.1:$API_PORT/api/v1/health"
info "  Docs ......... http://127.0.0.1:$API_PORT/docs"
[ "$START_FRONT" -eq 1 ] && info "  Front ........ http://127.0.0.1:$FRONT_PORT"
info "  Logs ......... $API_LOG"
[ "$START_FRONT" -eq 1 ] && info "                 $FRONT_LOG"
info ""
info "Falta o dispositivo: abra a simulação do Wokwi (iot/) e confira '[mqtt] conectado' no Serial."
info "Checklist completo da apresentação: document/demo.md"
info ""
info "${BOLD}Ctrl+C derruba os dois processos.${RESET}"

# `wait` sem argumento volta a cada sinal; o laço mantém o script vivo enquanto os filhos estiverem.
while :; do
  if [ -n "$API_PID" ] && ! kill -0 "$API_PID" 2>/dev/null; then
    fail "A API caiu. Últimas linhas:"; tail -n 25 "$API_LOG" || true; exit 1
  fi
  if [ "$START_FRONT" -eq 1 ] && [ -n "$FRONT_PID" ] && ! kill -0 "$FRONT_PID" 2>/dev/null; then
    fail "O front caiu. Últimas linhas:"; tail -n 25 "$FRONT_LOG" || true; exit 1
  fi
  sleep 1
done

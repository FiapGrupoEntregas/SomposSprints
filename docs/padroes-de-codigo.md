# Padrões de código

## Idioma

| O quê | Idioma |
|---|---|
| Identificadores (variáveis, funções, classes, arquivos de código, rotas, tópicos MQTT, campos JSON, enums) | **inglês** |
| Comentários, docstrings, documentação, textos da interface, mensagens de erro para o usuário, commits | **português** |

## Nomes com unidade

Grandezas físicas sempre levam a unidade no nome: `slope_deg`, `rain_mm`, `gust_max_kmh`,
`humidity_pct`, `temp_c`, `accel_g`, `elevation_m`. Em C++: `tiltLimitDeg`, `SENSOR_INTERVAL_MS`.

## Python (api/ e front-web/)

- Python **3.11+**, com type hints em toda função pública.
- **ruff** faz lint e formatação (linha de 100 caracteres). Rode antes de commitar:
  `uv run ruff check . --fix && uv run ruff format .`
- Use `logging`, não `print`.
- Nada de "números mágicos" espalhados. Limiares viram constantes nomeadas **no topo do módulo**, com um comentário apontando para a seção de `docs/regras-de-risco.md`.
- Dependências: adicione com `uv add` e rode `scripts/sync-requirements.sh`. **Nunca edite `requirements*.txt` à mão.**

### API (FastAPI)

- As camadas são `routes → services → clients` (ver [arquitetura.md](arquitetura.md)). **Rota não tem regra de negócio.**
- **Services são funções puras** sempre que possível: recebem dados e devolvem dados, sem I/O. Isso deixa o motor de risco testável sem internet.
- Toda entrada e saída HTTP usa **schema Pydantic** (`app/schemas/`).
- Configuração vem só de `app/core/config.py` (`AGRISHIELD_*`), injetada com `Depends(get_settings)`.
- Erros: `HTTPException` com `detail` em português (ex.: `404 "Fazenda não encontrada"`).
- As rotas ficam em `/api/v1/...`, com recursos no plural e em inglês (`/farms`, `/devices`).
- Chamadas externas usam `httpx` com **timeout** (10 s) e **cache** (relevo 24 h, previsão 1 h).
- Testes com `pytest` + `TestClient`. **Os testes não acessam a internet**: mocke a Open-Meteo com `httpx.MockTransport` ou use fixtures com JSON salvo em `tests/fixtures/`.

### Front-web (Streamlit)

- Uma página por arquivo em `views/`, registrada em `app.py`.
- **Zero regra de negócio**: nada de calcular risco ou limiar no front. Se precisar de um número, ele vem da API.
- Todo acesso à API passa por `services/api_client.py`, cacheado com `st.cache_data(ttl=...)`.
- Se a API estiver fora do ar, a página mostra `st.error` com instruções e **nunca** um traceback.
- O painel ao vivo usa `@st.fragment(run_every="2s")`, que atualiza só aquele pedaço da tela.
- As cores dos níveis são sempre as mesmas: 🟢 `#2E7D32`, 🟡 `#F9A825`, 🔴 `#C62828`.

## Firmware (iot/)

- **Um único `.ino`** (`iot/src/main.ino`), para continuar colável no Wokwi web.
- Ordem das seções: includes → configuração → estado → conectividade → sensores → lógica → setup/loop.
- Constantes em `UPPER_SNAKE_CASE`, funções e variáveis em `camelCase`.
- **Não use `delay()` dentro do `loop()`**. Use temporização com `millis()`. Só o `setup()` pode bloquear.
- Os pinos ficam em constantes `PIN_*` e têm que bater com o `diagram.json` e a tabela do `iot/README.md`.
- Logs no Serial levam um prefixo de módulo: `[wifi]`, `[mqtt]`, `[imu]`, `[alert]`, `[event]`.
- JSON é montado com **ArduinoJson** (nada de concatenar strings para payload).
- Toda biblioteca nova entra no `platformio.ini` **e** no `libraries.txt`.
- Garanta que compila com `pio run` antes de abrir o PR.

## Segredos e configuração

- **Nunca** commite `.env`, tokens (Telegram) ou `secrets.toml`. Eles já estão no `.gitignore`.
- Toda variável nova entra no `.env.example` correspondente, com comentário.

## Testes mínimos por tipo de mudança

| Mudança | Teste esperado |
|---|---|
| Regra de risco (services) | teste unitário com casos de borda de cada limiar |
| Endpoint novo | teste com `TestClient` (sucesso + 1 erro) |
| Página nova no front | adicionar em `tests/test_pages.py` (smoke test) |
| Firmware | `pio run` compila + evidência da simulação no PR (print/GIF do Serial) |

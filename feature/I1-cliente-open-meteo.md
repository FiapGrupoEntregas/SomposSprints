# I1 — Cliente Open-Meteo e agregação diária

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api |
| Depende de | — |
| Janela | 15/09 |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Concentrar **todas** as chamadas à Open-Meteo em um só lugar, com timeout, cache e tolerância a
falhas, e transformar os dados horários nos indicadores diários usados pelo motor de risco. É a base
de W2, W3, W4, W6, W7, W8 e W9.

## História de usuário

> Como **motor de risco**, quero **elevação e clima diário já agregados e confiáveis**, para **calcular o risco sem me preocupar com a API externa**.

## Escopo

**Inclui**
- Elevação para até 100 pontos por chamada.
- Previsão horária com `past_days=3` e `forecast_days=7` (fuso `America/Sao_Paulo`).
- Dados históricos para o replay: Historical Forecast API (datas ≥ 2022-01-01) e Archive API (antes disso).
- Agregação diária conforme [regras-de-risco §2](../docs/regras-de-risco.md#2-clima-agregação-diária-i1).
- Cache em memória com TTL e *stale-if-error*.

**Não inclui**
- Outras fontes (INMET, ANA…): ver ADR-005.
- Cache em disco.

## Regras e lógica

- Variáveis horárias: `temperature_2m, relative_humidity_2m, precipitation, weather_code, wind_speed_10m, wind_gusts_10m, cape, soil_moisture_3_to_9cm` (verificadas em 15/09/2026).
- Na Archive API, `cape` vem indefinido e a umidade do solo se chama `soil_moisture_0_to_7cm`. Trate os dois como opcionais (`None`).
- TTL do cache: elevação **24 h**, previsão **1 h**, histórico **7 dias**. A chave do cache é a URL mais os parâmetros.
- *Stale-if-error:* se a chamada falhar e existir um valor vencido no cache, devolva esse valor e registre um aviso no log. Isso protege a demo.
- Timeout: 10 s. Se falhar sem cache, lance `WeatherUnavailableError` (as rotas convertem em **503 "Serviço de clima indisponível"**).

## Implementação

### API (`api/`)
- `app/core/cache.py`: `TTLCache` simples (dict + `time.monotonic()`), com `get`, `set` e `get_stale`.
- `app/clients/open_meteo.py`:
  - `fetch_elevations(lats: list[float], lons: list[float]) -> list[float]`: `ValueError` se houver mais de 100 pontos.
  - `fetch_hourly_forecast(lat, lon, past_days=3, forecast_days=7) -> HourlyWeather`
  - `fetch_hourly_history(lat, lon, start: date, end: date) -> HourlyWeather`: escolhe a API pela data.
  - Recebe um `httpx.Client` injetável (para testes com `MockTransport`).
- `app/services/weather.py`:
  - `aggregate_daily(hourly: HourlyWeather) -> list[DailyWeather]`, que já preenche `rain_72h_mm`.
- `app/schemas/weather.py`: `HourlyWeather` (listas alinhadas por hora) e `DailyWeather` (campos da tabela §2 + `date`).
- Settings já existentes: `open_meteo_*_url`.

## Critérios de aceite

- [ ] 100 coordenadas → 1 requisição → 100 elevações. 101 coordenadas → `ValueError`.
- [ ] A previsão devolve 10 dias agregados (3 passados + 7 futuros) com todos os campos de §2.
- [ ] `rain_72h_mm` do dia d = chuva de d−2 + d−1 + d (teste com números conhecidos).
- [ ] Uma segunda chamada idêntica dentro do TTL **não** acessa a rede (teste contando requisições no `MockTransport`).
- [ ] Com a rede falhando e cache vencido, a função devolve o valor vencido. Sem cache, lança `WeatherUnavailableError`.
- [ ] Os testes rodam **offline**, com fixtures JSON em `tests/fixtures/open_meteo_*.json`.

## Testes

- `tests/test_open_meteo_client.py`: limite de 100 pontos, montagem dos parâmetros, cache, stale-if-error, escolha da API histórica pela data.
- `tests/test_weather_aggregation.py`: soma, máx, mín, thunderstorm e rain_72h nas bordas (primeiro dia sem histórico → soma o que houver).

## Riscos e plano B

| Risco | Plano B |
|---|---|
| Open-Meteo lenta ou fora na demo | stale-if-error + abrir a fazenda antes da demo para aquecer o cache |
| Limite de uso (10 mil chamadas/dia no plano gratuito) | O cache resolve. Nunca chamar dentro de loops por célula |

## Tarefas

- [ ] Salvar respostas reais como fixtures (`curl … > tests/fixtures/…json`)
- [ ] `TTLCache` + testes
- [ ] `open_meteo.py` + testes com `MockTransport`
- [ ] `aggregate_daily` + testes
- [ ] Atualizar o `README.md` (se algo mudou em "Como executar") e o status aqui

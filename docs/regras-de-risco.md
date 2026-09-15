# Regras de risco — relevo × clima (v1)

**Fonte da verdade** do motor de risco. O código fica em `api/app/services/` e **tem que bater com
este documento**. Mudou uma regra ou um limiar? Atualize este arquivo no mesmo PR.

> ⚠️ Todos os limiares são **valores iniciais para a v1**. A trilha de pesquisa do time (T1) valida
> cada um com fontes (NR-31, manuais de fabricantes, literatura) e registra aqui. Por isso o score é
> **baseado em regras e explicável**: cada alerta mostra o *motivo*.

## Escala de níveis

| Nível | Enum | Cor | Significado para o operador |
|---|---|---|---|
| Baixo | `green` | 🟢 | Operação normal |
| Atenção | `yellow` | 🟡 | Operar com cuidado, com velocidade reduzida e evitando manobras na encosta |
| Alto | `red` | 🔴 | Não operar máquinas nesta área neste dia |

O nível final de uma célula num dia é o **pior** nível entre todos os perigos. Os motivos de todos os perigos em `yellow` ou `red` são guardados para explicar o resultado.

## 1. Relevo (W2)

### Grade

- Retângulo (bbox) da fazenda dividido em **10 × 10 = 100 pontos**, que cabem em 1 chamada da Open-Meteo Elevation API.
- A elevação vem do Copernicus DEM (GLO-90), com **resolução de ~90 m**. Uma fazenda de ~1 km × 1 km dá células de ~110 m, compatíveis com essa resolução.
- As linhas da matriz vão de **norte para sul**, e as colunas de **oeste para leste**.

### Inclinação e orientação da encosta

```
dx = distância leste-oeste entre colunas (m) = Δlon × 111 320 × cos(lat)
dy = distância norte-sul entre linhas (m)   = Δlat × 110 540

dz_dlinha, dz_dcoluna = numpy.gradient(elev, dy, dx)
dz_dnorte = -dz_dlinha        # as linhas crescem para o sul, então o sinal inverte
dz_dleste =  dz_dcoluna

slope_deg  = degrees(arctan(hypot(dz_dleste, dz_dnorte)))
aspect_deg = (degrees(arctan2(-dz_dleste, -dz_dnorte)) + 360) % 360   # para onde a encosta "desce"; 0 = N, sentido horário
```

`aspect_deg` vira rótulo de 8 direções (N, NE, L, SE, S, SO, O, NO) para as recomendações ("encosta nordeste").

### Classes de terreno

`TPI` = elevação da célula − média dos vizinhos (até 8; nas bordas, só os que existem).

| Classe | Enum | Regra |
|---|---|---|
| Baixada | `lowland` | `elev ≤ P25` da fazenda **e** `TPI ≤ 0` |
| Exposta (topo/crista) | `exposed` | `elev ≥ P75` da fazenda **e** `TPI ≥ 0` |
| Encosta / meia-encosta | `slope` | demais células |
| Plano | `flat` | se a amplitude da fazenda (máx − mín) for **< 5 m**, todas as células viram `flat` |

## 2. Clima: agregação diária (I1)

Previsão horária da Open-Meteo para o centro da fazenda, no fuso `America/Sao_Paulo`, com `past_days=3` e `forecast_days=7`.

Variáveis horárias (nomes da API, verificados em 15/09/2026): `temperature_2m`, `relative_humidity_2m`,
`precipitation`, `weather_code`, `wind_speed_10m`, `wind_gusts_10m`, `cape`, `soil_moisture_3_to_9cm`.

| Campo diário | Cálculo |
|---|---|
| `rain_mm` | soma de `precipitation` no dia |
| `rain_72h_mm` | `rain_mm` de d−2 + d−1 + d |
| `temp_max_c` | máx `temperature_2m` |
| `rh_min_pct` | mín `relative_humidity_2m` |
| `wind_max_kmh` | máx `wind_speed_10m` |
| `gust_max_kmh` | máx `wind_gusts_10m` |
| `cape_max` | máx `cape` |
| `thunderstorm` | algum `weather_code` ∈ {95, 96, 99} |
| `soil_moisture` | máx `soil_moisture_3_to_9cm` (**só exibido na v1**, ver abaixo) |

## 3. Estado do solo

| Estado | Enum | Regra (v1) | Fator do limite |
|---|---|---|---|
| Seco | `dry` | `rain_72h_mm < 10` | 1,00 |
| Úmido | `moist` | `10 ≤ rain_72h_mm < 30` | 0,85 |
| Encharcado | `saturated` | `rain_72h_mm ≥ 30` | 0,67 |

> Por que não usar `soil_moisture` direto? O valor absoluto depende muito do tipo de solo. Em
> Carmo de Minas (MG), por exemplo, veio 0,36 m³/m³ em 15/09/2026 sem chuva nenhuma. Na v1 ele só é
> exibido. Calibrar depois é um item do roadmap.

## 4. Limite dinâmico de inclinação (W4)

```
L_dia = floor_0.5( L_ref × fator_do_solo )
```

- `L_ref` = limite de referência do equipamento em solo seco. Vem do `farms.json` e o padrão é **15°**.
- `floor_0.5` = arredonda **para baixo**, de 0,5 em 0,5 (conservador).
- Exemplo com `L_ref = 15°`: seco **15,0°** · úmido **12,5°** · encharcado **10,0°**.
- O ESP32 usa `warn_ratio = 0.8`: 🟡 a partir de 80% do limite e 🔴 a partir de 100% (ver E2).

## 5. Perigos por célula e por dia (W3 e W7)

### 5.1 Capotamento: `rollover` (W3, P0)

`r = slope_deg / L_dia`

| Nível | Condição |
|---|---|
| 🟢 | `r < 0.7` |
| 🟡 | `0.7 ≤ r < 1.0` |
| 🔴 | `r ≥ 1.0` |

Motivo: *"Inclinação de 13° acima do limite de 10° para solo encharcado (42 mm em 72 h)."*
Simplificação: usamos a inclinação máxima do terreno. A inclinação real da máquina depende da direção em que ela anda, então essa escolha é conservadora.

### 5.2 Atolamento / alagamento: `bogging` (W3, P0)

| Nível | Condição |
|---|---|
| 🔴 | célula `lowland` **e** solo `saturated` |
| 🟡 | célula `lowland` **e** solo `moist` · **ou** qualquer célula com `rain_mm ≥ 50` no dia |

### 5.3 Raio: `lightning` (W7, P1)

| Nível | Condição |
|---|---|
| 🔴 | `thunderstorm` **e** célula `exposed` |
| 🟡 | `thunderstorm` (demais células) · **ou** `cape_max ≥ 2000` **e** célula `exposed` |

### 5.4 Vento: `wind` (W7, P1)

| Nível | Condição |
|---|---|
| 🔴 | `gust_max_kmh ≥ 60` **e** célula `exposed` |
| 🟡 | `gust_max_kmh ≥ 60` (demais) · **ou** `45 ≤ gust_max_kmh < 60` **e** célula `exposed` |

### 5.5 Incêndio em colheitadeira, pela "regra dos 30": `fire` (W7, P1)

Condições: `temp_max_c > 30`, `rh_min_pct < 30`, `wind_max_kmh > 30`.

| Nível | Condição |
|---|---|
| 🔴 | as 3 condições |
| 🟡 | 2 das 3 condições |

Vale para todas as células. O fogo sobe a encosta mais rápido: células com `slope_deg ≥ 15` passam de 🟡 para 🔴.

## 6. Resumo da fazenda por dia

- `worst_level`: o pior nível entre as células.
- `% de células` em cada nível.
- `top_reasons`: os motivos mais frequentes entre as células 🔴 e 🟡.
- `soil_state` e `tilt_limit_deg` do dia.

## 7. Janela segura de operação (W6)

Vale para **hoje e amanhã**, hora a hora, das 06h às 18h. Uma hora é **segura** se:
`precipitation < 0.5 mm` **e** `wind_gusts_10m < 45 km/h` **e** `weather_code ∉ {95, 96, 99}`.
Janelas são sequências de horas seguras com **duração ≥ 2 h**. As áreas 🔴 de capotamento **continuam proibidas** mesmo dentro da janela.

## 8. Perfil de subscrição (W8)

Calculado com o relevo e `L_ref` em solo seco (não depende da previsão):

| Indicador | Cálculo |
|---|---|
| `pct_slope_lt8` / `pct_slope_8_15` / `pct_slope_gt15` | % de células por faixa de inclinação |
| `pct_lowland` / `pct_exposed` | % de células por classe |
| `terrain_score` (0–100, maior = melhor) | `100 − (1,0·pct_gt15 + 0,5·pct_8_15 + 0,5·pct_lowland + 0,3·pct_exposed)`, limitado a [0, 100] |
| Classe | **A** (≥ 70) · **B** (40–69) · **C** (< 40) |

Os pesos são arbitrários na v1. Calibrá-los com a base de sinistros da Sompo é o primeiro item do roadmap de ML.

## 9. Replay (W9)

É o mesmo motor, rodando sobre dados históricos:
- datas **a partir de 2022**: Historical Forecast API (tem as mesmas variáveis da previsão);
- datas **anteriores**: Archive API (ERA5). Nela `cape` vem indefinido, então o perigo `lightning` usa só `weather_code`.
- A grade é centrada no local do acidente (±0,005°). A resposta traz o nível da **célula mais próxima** do ponto e informa se "o sistema teria alertado?" (nível ≥ 🟡).

## 10. Cenários simulados (demo)

Setembro é época seca no Sudeste, e a previsão real pode não ter nenhum dia de risco. Para a demo, a
API aceita `scenario=` (W3, W4, W6), que **modifica a previsão real antes da agregação**:

| `scenario` | Efeito (sobre os dados horários) | Feature |
|---|---|---|
| `heavy_rain` | +45 mm distribuídos das 12h às 18h do **dia +2** (`weather_code` 63 nessas horas) | W3 |
| `storm` | dia +2, das 14h às 17h: `weather_code` 95, rajadas de 70 km/h e +15 mm | W7 |
| `heatwave` | dia +1, das 12h às 16h: 34 °C, UR 22% e vento de 35 km/h | W7 |

Regras:
- O cenário **nunca** é o padrão. A resposta traz `"scenario": "<nome>"` e o front mostra o aviso "⚠️ Cenário simulado".
- Os cenários ficam em `api/app/services/scenarios.py` e têm testes próprios.

## Limitações conhecidas (declarar no pitch)

- O DEM de 90 m suaviza encostas curtas. A inclinação real pode ser maior.
- A previsão é de um ponto só (o centro da fazenda). Chuva localizada pode escapar.
- Sem GPS, não sabemos em qual célula a máquina está. Por isso o limite enviado ao equipamento vale para a fazenda inteira.
- Os limiares ainda não foram calibrados com dados de sinistro.

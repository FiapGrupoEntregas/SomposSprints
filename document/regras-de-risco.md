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
- Os 100 pontos são os **centros** das células, com passo = lado da bbox ÷ 10 (0,001°, ou ~110 m, numa bbox de ±0,005°). Assim os polígonos ladrilham exatamente a bbox, sem sobra nem falta. *(Convenção fixada na W2; com passo ÷ 9 as células dariam ~123 m e não fechariam a bbox.)*
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

Variáveis horárias (nomes da API, verificados em 19/09/2026): `temperature_2m`, `relative_humidity_2m`,
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

> **A janela de 72 h pode ser curta para pastagem** *(achado na W9, 20/09/2026 — candidata a
> calibração)*. No replay do tombamento de **Castro (PR), 17/04/2023**, a máquina tombou ao tentar
> desatolar outra num terreno que a reportagem descreve como "pastagem bastante afetada pelas chuvas
> recentes" — mas a janela de 72 h pegou só **4,6 mm**, e o solo saiu como `dry`. Nos **21 dias**
> anteriores tinham caído **65,9 mm**. Pastagem em solo saturado por semanas se comporta como
> encharcada mesmo depois de três dias secos, e a regra v1 não enxerga isso.
>
> É um **limite da regra**, não uma justificativa para o erro: a v1 mede encharcamento recente, e
> era o que se podia medir sem base de sinistro. Uma janela mais longa (ou uma segunda janela, por
> tipo de cobertura do solo) é candidata concreta a calibração quando houver base de sinistro de
> máquina — este é o tipo de ajuste que os dados da Sompo permitiriam fazer com evidência.

## 4. Limite dinâmico de inclinação (W4)

```
L_dia = floor_0.5( L_ref × fator_do_solo )
```

- `L_ref` = limite de referência em solo seco, sempre em graus. O padrão é **15°**.
  - **Limite de um equipamento** (W4): `base_tilt_limit_deg` do equipamento no `farms.json`; se faltar, o `reference_tilt_limit_deg` da fazenda.
  - **Mapa de risco da fazenda** (W3): como o mapa vale para a fazenda inteira e ela pode ter máquinas diferentes, use o **menor** `base_tilt_limit_deg` entre os equipamentos dela; sem equipamento, o `reference_tilt_limit_deg`. É a leitura conservadora: o mapa nunca libera uma área que seria proibida para a máquina mais frágil. *(Convenção fixada na W3.)*
  - **Piso:** `L_dia` nunca fica abaixo de **0,5°** (o próprio passo do `floor_0.5`), senão §5.1 dividiria por zero. *(Fixado na W3.)*
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

Motivo: *"Inclinação de 13° acima do limite de 10° (solo encharcado: 42 mm em 72 h)."* — mesmo texto do código e de `feature/W3-previsao-de-risco.md`.
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
- `top_reasons`: no máximo 3 motivos, agrupados por **(perigo, nível)** e representados pela mensagem mais repetida do grupo, ordenados por gravidade e depois por frequência. Agrupar por texto não funciona, porque as mensagens trazem números e quase nunca se repetem. *(Convenção fixada na W3.)*
- `soil_state` e `tilt_limit_deg` do dia.

## 7. Janela segura de operação (W6)

Vale para **hoje e amanhã**, hora a hora, das 06h às 18h. Uma hora é **segura** se:
`precipitation < 0.5 mm` **e** `wind_gusts_10m < 45 km/h` **e** `weather_code ∉ {95, 96, 99}`.

*(Convenções fixadas na W6.)* O intervalo é **meio-aberto `[06h, 18h)`**, igual ao dos cenários de §10: a última hora cheia é a das 17h, que termina às 18h. E **hora com indicador ausente conta como insegura** — numa orientação que o operador vai seguir, falta de dado não pode virar permissão. Isso **parece** oposto à W7, onde indicador ausente não conta como condição ativa (§5.5), mas o princípio é o mesmo: **nunca afirmar o que não se mediu**. A W7 não afirma que uma condição existe; a W6 não afirma que uma hora é segura.
Janelas são sequências de horas seguras com **duração ≥ 2 h**. As áreas 🔴 de capotamento **continuam proibidas** mesmo dentro da janela.

## 8. Perfil de subscrição (W8)

Calculado com o relevo e `L_ref` em solo seco (não depende da previsão):

| Indicador | Cálculo |
|---|---|
| `pct_slope_lt8` / `pct_slope_8_15` / `pct_slope_gt15` | % de células por faixa: **`< 8°`**, **`[8°, 15°)`** e **`≥ 15°`**. O `≥ 15` é fechado embaixo para casar com §5.5 *(fronteiras fixadas na W2)* |
| `pct_lowland` / `pct_exposed` | % de células por classe |
| `terrain_score` (0–100, maior = melhor) | `100 − (1,0·pct_gt15 + 0,5·pct_8_15 + 0,5·pct_lowland + 0,3·pct_exposed)`, limitado a [0, 100] |
| Classe | **A** (≥ 70) · **B** (40–69) · **C** (< 40) |

Os pesos são arbitrários na v1. Calibrá-los com a base de sinistros da Sompo é o primeiro item do roadmap de ML.
A API devolve `weights_version` e `calibration_note` junto do score, para que quem consome o número receba o aviso junto.

As faixas de 8° e 15° são **fixas**: o `L_ref` citado acima explica de onde veio o 15 (o limite padrão em solo seco),
mas não entra na fórmula. Numa fazenda com `L_ref ≠ 15°`, a faixa `≥ 15°` deixa de significar "acima do limite em solo
seco" e o `terrain_score` muda de sentido sem mudar de fórmula — por isso a API devolve `reference_tilt_limit_deg`
como contexto. Tornar as faixas função do `L_ref` é decisão de calibração, junto com os pesos.

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
| `heavy_rain` | +45 mm distribuídos das 12h às 18h do **dia +2** (`weather_code` 63 nessas horas) — intervalo **meio-aberto `[12h, 18h)`**, ou seja, 6 horas de 7,5 mm *(fixado na W3)* | W3 |
| `storm` | dia +2, das 14h às 17h (`[14h, 17h)`, 3 horas de 5 mm): `weather_code` 95, rajadas de 70 km/h e +15 mm | W7 |
| `heatwave` | dia +1, das 12h às 16h: 34 °C, UR 22% e vento de 35 km/h | W7 |

Regras:
- O cenário **nunca** é o padrão. A resposta traz `"scenario": "<nome>"` e o front mostra o aviso "⚠️ Cenário simulado".
- Os cenários ficam em `api/app/services/scenarios.py` e têm testes próprios.

## 11. Relação com o modelo preditivo (D3, W13)

As regras deste documento continuam sendo **a base do alerta ao operador**, porque são explicáveis e
funcionam offline no dispositivo. O modelo preditivo ([dados-e-modelo.md](dados-e-modelo.md)) roda **ao
lado** delas e entrega uma probabilidade de sinistro para a seguradora. As regras também são o
**baseline** contra o qual o modelo é comparado. Se o modelo não superar o baseline, as regras seguem no comando.

**Situação em 21/09/2026: o modelo passou a superar o baseline** no teste de 2024 (AUC-PR 0,144 ×
0,074). **Isso não muda nada aqui** — o alerta ao operador continua vindo das regras, e a decisão
é da W13. São três razões, e nenhuma delas dependia de quem ganhava a comparação:

1. **Explicabilidade.** O alerta precisa dizer *por quê*, com o número que o disparou. Uma
   probabilidade não é um motivo.
2. **Offline.** O limite de inclinação roda no ESP32, sem rede; o modelo vive na API.
3. **O alvo.** O modelo ganha no alvo "qualquer indenização", que no PSR é dominado por seca e
   geada. O perigo que estas regras tratam é encharcamento e tempestade — e, nesse alvo
   (`target_rain_claim`), quem discrimina é o baseline. Ver a seção de resultados em
   [dados-e-modelo.md](dados-e-modelo.md).

O que o modelo ganha com isso é o lugar que já tinha: **a segunda leitura, para a seguradora**,
exibida ao lado do nível por regras e com as ressalvas junto do número.

## Fontes dos limiares (T1)

**Situação em 20/09/2026: esta tabela está vazia.** Nenhum limiar deste documento tem, até agora,
referência publicada — todos são **valores iniciais escolhidos para a v1**, como diz o aviso do
topo. Isso está declarado de propósito, em vez de citar uma norma de memória.

É a pendência de pesquisa **T1** ([feature/T-trilha-do-time.md](../feature/T-trilha-do-time.md)),
e é a pergunta mais provável de uma banca de seguradora: *"de onde saiu esse número?"*. O lugar de
responder é aqui.

**Como preencher:** uma linha por limiar, com o valor que está no código hoje, o valor que a fonte
sugere e o link. Se a fonte **confirmar** o valor, ótimo — a linha vira evidência. Se **divergir**,
não mude o código sozinho: registre a divergência, porque mudar limiar mexe em
`api/app/services/risk.py` e em `limits.py`, que têm teste, e o documento e o código precisam
andar juntos (regra do topo deste arquivo).

| Limiar | Onde está | Valor atual | Valor sugerido pela fonte | Fonte (link) |
|---|---|---|---|---|
| Inclinação de referência com solo seco (`L_ref`) | §4 | 15° | _(a preencher)_ | _(a preencher)_ |
| Redução do limite com solo úmido / encharcado | §4 | ver §4 | _(a preencher)_ | _(a preencher)_ |
| Chuva em 72 h que caracteriza solo encharcado | §3 | ver §3 | _(a preencher)_ | _(a preencher)_ |
| Inclinação de capotamento em solo encharcado | §5 | ver §5 | _(a preencher)_ | _(a preencher)_ |
| Rajada de vento (🟡 e 🔴) | §5 | ver §5 | _(a preencher)_ | _(a preencher)_ |
| Regra dos 30 (temperatura, umidade, vento) | §5 | 30 °C · 30% · 30 km/h | _(a preencher)_ | _(a preencher)_ |
| Escalada do risco de incêndio em encosta ≥ 15° | §5 | 15° | _(a preencher)_ | _(a preencher)_ |
| Tempestade em célula exposta (raio) | §5 | ver §5 | _(a preencher)_ | _(a preencher)_ |
| Janela segura: mínimo de 2 h, entre 06h e 18h | §7 | 2 h · 06–18h | _(a preencher)_ | _(a preencher)_ |
| Pesos do score de subscrição | §8 | ver §8 | _(a preencher)_ | _(a preencher)_ |

Fontes que valem procurar, na ordem: **NR-31** (segurança no trabalho rural), **ISO 5700 / ROPS**
(estrutura de proteção contra capotamento), manuais de fabricantes de trator, publicações da
**Fundacentro** e da **Embrapa**, e material do Corpo de Bombeiros para a regra dos 30.

## Limitações conhecidas (declarar no pitch)

- O DEM de 90 m suaviza encostas curtas. A inclinação real pode ser maior.
- A previsão é de um ponto só (o centro da fazenda). Chuva localizada pode escapar.
- Sem GPS, não sabemos em qual célula a máquina está. Por isso o limite enviado ao equipamento vale para a fazenda inteira.
- **Os limiares ainda não foram calibrados com dados de sinistro, e ainda não têm fonte publicada**
  (ver a seção anterior). São valores iniciais da v1 — e é justamente por isso que o score é
  **explicável**: cada alerta mostra a conta que fez, então dá para discordar do número sem ter de
  confiar na caixa-preta.

# iot/ — Firmware ESP32 (PlatformIO + Wokwi)

Dispositivo embarcado na máquina: um **inclinômetro com limite dinâmico**. Ele mede a inclinação
com o MPU6050, recebe da API o limite seguro do dia (via MQTT) e alerta o operador com LEDs e buzzer.

> O Wokwi **não simula Bluetooth**, então o ESP32 usa **Wi-Fi (`Wokwi-GUEST`) + MQTT** no broker
> público HiveMQ. Contrato completo em [document/contrato-mqtt.md](../document/contrato-mqtt.md).

## Estrutura

```
iot/
├── src/main.ino      # firmware (arquivo único, compatível com o Wokwi web)
├── platformio.ini    # build no PlatformIO (placa esp32dev, bibliotecas)
├── diagram.json      # circuito do Wokwi
├── wokwi.toml        # liga o firmware compilado ao simulador (VS Code)
└── libraries.txt     # bibliotecas para o Wokwi web (mesma lista do lib_deps)
```

## Circuito

| Componente | Pino ESP32 | Feature |
|---|---|---|
| MPU6050 (SDA / SCL) | 21 / 22 | E1, E5 |
| SSD1306 128×64 (SDA / SCL) | 21 / 22 | E7 |
| DHT22 (dados) | 4 | E6 |
| LED verde / amarelo / vermelho (com resistor 220 Ω) | 25 / 26 / 27 | E2 |
| Buzzer | 14 | E2, E5, E8 |
| Botão de ocorrência (`INPUT_PULLUP` → GND) | 33 | E8 |
| VCC dos sensores | 3V3 | — |

O MPU6050 (`0x68`) e o SSD1306 (`0x3C`) dividem o mesmo barramento I2C, em endereços diferentes. O
barramento roda a **400 kHz**: a 100 kHz uma atualização da tela levaria ~92 ms, e o `loop()` ficaria
parado nesse tempo.

Se mudar um pino, atualize **ao mesmo tempo** o `diagram.json`, as constantes `PIN_*` do `main.ino` e esta tabela.

## Opção 1: PlatformIO + Wokwi no VS Code (recomendado para desenvolver)

1. Instale as extensões **PlatformIO IDE** e **Wokwi Simulator** no VS Code. O Wokwi pede uma licença gratuita na primeira vez.
2. Abra a pasta `iot/` no VS Code.
3. Compile: botão ✓ da barra do PlatformIO, ou `pio run` no terminal.
4. `F1` → **Wokwi: Start Simulator**. O simulador usa o `wokwi.toml` e o `diagram.json`.

## Opção 2: Wokwi no navegador (mais rápido para o time testar)

1. Acesse https://wokwi.com e crie um projeto **ESP32** novo.
2. Cole o conteúdo de `src/main.ino` no `sketch.ino`.
3. Substitua o `diagram.json` pelo deste repositório.
4. Crie o arquivo `libraries.txt` (menu ▾ ao lado das abas, ou aba *Library Manager*) com o conteúdo do nosso `libraries.txt`.
5. Clique em ▶. O Serial Monitor deve mostrar `[wifi] conectado` e `[mqtt] conectado`.

## Simulando inclinação

Durante a simulação, clique no MPU6050 e ajuste a aceleração. Para inclinar lateralmente (roll) em θ
graus, use **Y = sen θ** e **Z = cos θ** (X = 0):

> Use **3 casas decimais** nos campos do MPU6050 (dá para digitar o valor, além de arrastar o
> slider). Com 2 casas, `Y = 0.17 / Z = 0.98` vira 9,84° e não chega a cruzar o limite de 10°.

| Inclinação | accel Y (g) | accel Z (g) | Lido | Uso na demo (limite 10°, `warn_ratio` 0,8) |
|---|---|---|---|---|
| 0° | 0.000 | 1.000 | 0,00° | parado no plano — 🟢 |
| 6,9° | 0.120 | 0.993 | 6,89° | vindo do 🟡, volta para 🟢 |
| 7,5° | 0.131 | 0.991 | 7,53° | vindo do 🟡, continua 🟡 (histerese) |
| 7,9° | 0.137 | 0.991 | 7,87° | ainda 🟢 |
| 8° | 0.140 | 0.990 | 8,05° | entra no 🟡 (bipe curto) |
| 8,9° | 0.155 | 0.988 | 8,92° | vindo do 🔴, volta para 🟡 |
| 9,5° | 0.165 | 0.986 | 9,50° | vindo do 🔴, continua 🔴 (histerese) |
| 10° | 0.174 | 0.985 | 10,02° | limite com solo encharcado — entra no 🔴 + `tilt_alert` |
| 12° | 0.208 | 0.978 | 12,01° | 🔴 acima do limite do dia chuvoso |
| 15° | 0.259 | 0.966 | 15,01° | limite com solo seco |
| 60° | 0.866 | 0.500 | 60,00° | capotamento por inclinação (E5) — segure 3 s |

O acelerômetro é configurado em **±8 g** (`mpu.setAccelerometerRange`). A biblioteca inicializa em
±2 g, e nessa faixa a leitura satura em 2 g — o impacto de 2,5 g do E5 nunca apareceria. Em troca, a
quantização passa de 16384 para 4096 LSB/g, então a coluna "Lido" pode variar em até **±0,01°** em
relação ao valor da tabela — nenhum cruzamento de limiar muda por causa disso.

Como a média móvel usa 5 amostras a 10 Hz, o Serial acompanha a mudança do slider em ~0,5 s.
Os níveis têm **histerese de 1°**: para sair do 🔴 é preciso descer abaixo de `limite − 1°`, e para
sair do 🟡, abaixo de `0,8 × limite − 1°`.

> No boot o limite é `DEFAULT_TILT_LIMIT_DEG = 15.0` (🟡 a partir de 12°, 🔴 a partir de 15°).
> Para reproduzir a tabela acima com limite de 10°, **publique um `config` retained** (E3):
>
> ```bash
> mosquitto_pub -h broker.hivemq.com -r \
>   -t 'agrishield/fiap-sompo-2026/devices/tractor-01/config' \
>   -m '{"tilt_limit_deg":10,"warn_ratio":0.8,"reason":"42 mm de chuva em 72 h"}'
> ```

## Simulando capotamento (E5)

O estado de capotamento é **travado**: entra por inclinação sustentada **ou** por impacto, e só sai
quando a máquina fica estável.

| Regra | Valor | Como reproduzir no Wokwi |
|---|---|---|
| Entrada por inclinação | `tilt ≥ 45°` por **≥ 2 s** seguidos | MPU6050 com `Y = 0.866`, `Z = 0.500` (60°) e segure 3 s |
| Não dispara | menos de 2 s acima de 45° | `Y = 0.766`, `Z = 0.643` (50°) por ~1 s e volte para `Y = 0`, `Z = 1` |
| Entrada por impacto | `accel_g ≥ 2,5` | `X = 2.000`, `Z = 1.500` (módulo 2,5 g), dispara na amostra seguinte |
| Saída | `tilt < 20°` por **10 s** seguidos | volte para `Y = 0`, `Z = 1` e espere 10 s |

Durante o estado travado: **LED vermelho piscando a 5 Hz** e **buzzer contínuo** (o 🔴 do E2 é LED
fixo com buzzer a 2 Hz — o capotamento tem prioridade), e a telemetria sai com
`"alert_level":"rollover"`.

O evento `rollover` leva os **30 s anteriores** (`context`, 1 amostra/s, `t_s` de `-29` a `0`) e é
publicado 3 vezes com o mesmo `event_id`, pela mesma fila do `tilt_alert`. Medido com a própria
ArduinoJson: **725 bytes** no caso da demo e **1021 bytes** no pior caso com 30 linhas — folga
confortável no `setBufferSize(2048)`.

```json
{"device_id":"tractor-01","event_id":"tractor-01-1789500000-7","ts":1789500000,"type":"rollover",
 "roll_deg":60,"pitch_deg":0,"accel_g":1,"tilt_limit_deg":10,
 "context":{"fields":["t_s","roll_deg","pitch_deg","accel_g"],
            "rows":[[-29,9.8,-2.9,1], ..., [-2,31.4,-2.9,1],[-1,58.7,-2.9,1],[0,60,0,1]]}}
```

No `context`, `roll_deg` e `pitch_deg` vêm da leitura filtrada (a mesma base da telemetria) e
`accel_g` é o **pico daquele segundo**: um impacto dura poucos décimos de segundo e uma média o
esconderia justamente onde ele importa para o laudo do sinistro. Pelo mesmo motivo, a detecção de
impacto usa a amostra **crua**, enquanto a regra dos 45° usa a leitura filtrada.

**Um evento por situação.** Enquanto o estado está travado, `computeLevel()` devolve
`LEVEL_ROLLOVER` e o E2 não consegue mudar de nível, então não sai `tilt_alert` em paralelo nem um
segundo `rollover`. E quando a inclinação entra no 🔴 já **acima de 45°**, o `tilt_alert` fica
**adiado** até o veredito do E5: se o estado travar, o `rollover` o substitui; se a inclinação cair
antes dos 2 s, o `tilt_alert` é publicado normalmente. A decisão se resolve em no máximo 2 s.

**Máquina que liga já capotada:** o `setup()` **não** assume o estado travado — a regra é temporal e
não há histórico no boot. O `loop()` começa a contar os 2 s na primeira leitura (ou trava na hora,
se o impacto estiver presente), então o `rollover` sai ~2 s depois do boot, com as poucas linhas de
contexto que já existirem. O `tilt_alert` do boot em 🔴 fica adiado pela mesma regra acima.

## Display OLED (E7)

Uma tela só, atualizada **no máximo 4 vezes por segundo** e **apenas quando algo muda** — cada
atualização completa move 1 KB pelo I2C (~23 ms a 400 kHz), e é tempo em que o `loop()` não anda.
Quem decide se vale redesenhar é a função pura `screenChanged()`, comparando o `ScreenModel`
(inclinação e limite em décimos inteiros, nível, Wi-Fi, MQTT e solo). Com a tela parada o custo é
zero; no pior caso são ~9% do tempo, sem atrapalhar o IMU a 10 Hz nem o LED de 5 Hz do capotamento.

```
AgriShield                W M     <- W = Wi-Fi, M = MQTT ("-" quando fora)
tractor-01
------------------------------
12.4°                             <- inclinacao atual, fonte grande
Limite 10.0°
SOLO ENCHARCADO
PERIGO                            <- invertido no vermelho e no capotamento
```

Sem acentos: a fonte padrão da Adafruit GFX não tem. O `°` sai do cp437 (`display.write(0xF8)`).

| Estado | Texto |
|---|---|
| `soil_state` do config | `SOLO SECO` · `SOLO UMIDO` · `SOLO ENCHARCADO` · `SOLO --` (ausente ou desconhecido) |
| nível 🟢 / 🟡 / 🔴 / capotamento | `OK` · `ATENCAO` · `PERIGO` · `CAPOTAMENTO` |

Se o SSD1306 não responder, o firmware loga `[oled] SSD1306 nao encontrado` e **segue normalmente**:
LEDs, buzzer, telemetria e eventos não dependem da tela.

## Botão de ocorrência (E8)

Botão no pino **33** (`INPUT_PULLUP`, ligado ao GND), lido a cada volta do `loop()` com debounce de
**50 ms** na função pura `debounceButton()`. Um toque publica `incident_report` com os mesmos 30 s
de contexto do capotamento (o buffer e o `fillEventContext()` do E5 são reaproveitados) e confirma
ao operador com **3 bipes curtos** (80 ms ligado, 120 ms desligado), temporizados por `millis()`.

- **Segurar não repete:** só a borda de descida confirmada vale um evento; soltar apenas rearma.
- **Botão preso no boot** não vira ocorrência: o estado inicial é lido do próprio pino no `setup()`,
  então só um toque novo, depois de soltar, gera evento (no boot também não haveria contexto).
- Os bipes têm a palavra sobre o buzzer enquanto tocam (~600 ms). No 🔴 e no capotamento isso
  interrompe o alarme por esse tempo e ele volta sozinho logo depois; o LED continua piscando o
  tempo todo. É o preço de o operador ter certeza de que a ocorrência foi registrada.
- Tamanho do payload: **738 bytes** no caso típico e **1028 bytes** no pior caso com 30 linhas,
  contra os 2048 do `setBufferSize`.

## Acompanhando as mensagens MQTT

- Navegador: HiveMQ WebSocket Client (http://www.hivemq.com/demos/websocket-client/). Conecte e assine `agrishield/fiap-sompo-2026/#`.
- Terminal: `mosquitto_sub -h broker.hivemq.com -t 'agrishield/fiap-sompo-2026/#' -v`.

### Recebendo o limite do dia (E3)

O dispositivo assina `.../config` com QoS 1. Como a mensagem é **retained**, o limite volta sozinho
depois de reiniciar a simulação — no Wokwi a flash é zerada a cada execução, então é o retained (e
não a NVS) que segura o limite lá.

```bash
# aplica o limite do dia chuvoso
mosquitto_pub -h broker.hivemq.com -r \
  -t 'agrishield/fiap-sompo-2026/devices/tractor-01/config' \
  -m '{"tilt_limit_deg":10,"warn_ratio":0.8,"soil_state":"saturated","risk_level":"red","wind_max_kmh":22,"valid_until":1789550000,"reason":"42 mm de chuva em 72 h"}'

# apaga o retained do broker (volta ao padrão de 15° no próximo boot)
mosquitto_pub -h broker.hivemq.com -r -n \
  -t 'agrishield/fiap-sompo-2026/devices/tractor-01/config'
```

| O que o firmware faz | Faixa aceita |
|---|---|
| `tilt_limit_deg` | **[3°, 45°]**. Fora disso, loga `[config] limite invalido` e mantém o atual |
| `warn_ratio` | **[0,5, 0,95]**. Ausente ou inválido, mantém o atual |
| `valid_until` vencido | continua usando o limite e loga `[config] limite vencido` (só depois do NTP sincronizar). Aceita inteiro ou decimal; um `config` **sem** o campo zera o vencimento em vez de herdar o anterior |
| JSON quebrado | loga `[config] JSON invalido` e ignora a mensagem |
| Campos desconhecidos | ignorados. `wind_max_kmh` fica guardado para o E6; `soil_state`, `risk_level` e `reason`, para o E7 |

O piso de 3° não é decorativo: com `tilt_limit_deg ≤ 1°` (a histerese), a saída do 🔴 cairia para
zero ou menos e o nível **nunca** voltaria para 🟡/🟢. Dois `static_assert` no `main.ino` travam o
build se alguém afrouxar essas faixas.

Ao aplicar um limite novo, o dispositivo grava na NVS (`Preferences`, namespace `agrishield`),
publica o evento `limit_applied` e **recalcula o nível na hora** — sem esperar a próxima amostra.

### Telemetria (E4)

A cada **5 s** (e também na hora em que o nível muda) sai uma mensagem em `.../telemetry`:

```json
{"device_id":"tractor-01","ts":1789500000,"seq":42,"roll_deg":12.4,"pitch_deg":-3.1,
 "accel_g":1.01,"temp_c":31.5,"humidity_pct":28,"fire_conditions":2,"tilt_limit_deg":10,
 "alert_level":"red"}
```

- **191 bytes** (medido com a própria ArduinoJson), bem abaixo do teto de 300 do contrato. Uma
  cópia real está em `api/tests/fixtures/telemetry_sample.json`, para o teste da ponte MQTT (I2).
- `ts` vem do NTP (`pool.ntp.org`, iniciado com `configTime` no boot). Enquanto não sincroniza, sai
  `ts: 0` e a API usa a hora de recebimento — é o que o contrato manda.
- `seq` reinicia no boot e conta **só o que foi publicado**: a lacuna na sequência denuncia perda.
- `temp_c` e `humidity_pct` viram `null` quando o DHT22 falha por mais de 10 s seguidos (E6).
- `fire_conditions` é opcional no contrato: sai sempre que dá para julgar pelo menos uma das três
  condições da regra dos 30, e é **omitido** quando não há nem DHT nem `wind_max_kmh` — `0` diria
  "nenhuma condição ativa", que é diferente de "não sei".
- **Não há buffer offline**: com o MQTT fora, a leitura daquele instante se perde. Os alertas locais
  continuam funcionando normalmente.

### Regra dos 30 (E6)

Três condições que, juntas, descrevem o risco de incêndio na máquina — uma colheitadeira quente em
palha seca é uma fonte clássica de fogo:

| Condição | Limiar | De onde vem |
|---|---|---|
| Temperatura | `temp_c > 30` | DHT22 na máquina |
| Umidade | `humidity_pct < 30` | DHT22 na máquina |
| Vento | `wind_max_kmh > 30` | campo `wind_max_kmh` do `config` (E3) — não há anemômetro |

`fireConditions()` é função pura e conta quantas estão ativas (0 a 3). Os limiares são **estritos**:
exatamente 30 °C, 30 % ou 30 km/h não contam. Um valor desconhecido (NaN) também não conta — na
dúvida, o firmware não inventa risco.

**Os LEDs continuam sendo da inclinação.** O incêndio não mexe em LED nem em buzzer: aparece só na
telemetria (e, mais tarde, no display do E7).

O DHT22 é lido a cada **2 s** (o mínimo do sensor), com temporizador próprio no `loop()`, sem
atravessar o IMU a 10 Hz. Quando a leitura falha (NaN), o último valor válido **vale por mais 10 s**
— uma falha isolada não apaga a telemetria; passados os 10 s, o campo vai como `null`.

Log a 1 Hz, no formato da spec:

```
[env] 32.1°C 25% vento prev. 35 km/h → 3/3 condicoes
[env] -- -- vento prev. -- → 0/3 condicoes      (DHT22 fora e sem config)
```

Para reproduzir no Wokwi: clique no DHT22 e ajuste **temperature** e **humidity**; o vento vem do
`config` retained:

```bash
mosquitto_pub -h broker.hivemq.com -r \
  -t 'agrishield/fiap-sompo-2026/devices/tractor-01/config' \
  -m '{"tilt_limit_deg":10,"warn_ratio":0.8,"wind_max_kmh":35,"reason":"vento forte e ar seco"}'
```

| DHT22 | `wind_max_kmh` | `fire_conditions` |
|---|---|---|
| 35 °C / 20 % | 35 | **3** |
| 35 °C / 50 % | 35 | **2** |
| 28 °C / 50 % | 35 | **1** |
| 28 °C / 50 % | 10 | **0** |

## Estado atual

O firmware conecta no Wi-Fi e no MQTT, publica `status` (com LWT), assina o `config` e publica
telemetria e eventos.

- **E1 — inclinômetro:** MPU6050 lido a 10 Hz, com média móvel de 5 amostras para `roll`, `pitch` e
  `accel_g`. `tiltDeg = max(|roll|, |pitch|)` é a grandeza comparada com o limite. Log a 1 Hz
  (`[imu]`), com o DHT22 em um intervalo próprio de 2 s (`[env]`).
- **E2 — alerta local:** três níveis (🟢/🟡/🔴) com um LED aceso por vez, histerese de 1°, buzzer com
  bipe curto ao entrar no 🟡 e intermitente a 2 Hz no 🔴. Ao entrar no 🔴, o evento `tilt_alert` é
  publicado 3 vezes com o mesmo `event_id` (contrato MQTT), em fila temporizada com `millis()`.
- **E3 — limite via MQTT:** `applyConfig()` valida o `config` retained, guarda o limite na NVS,
  publica `limit_applied` e recalcula o nível na hora. Detalhes na seção acima.
- **E4 — telemetria:** publicação a cada 5 s e imediata na mudança de nível, com `ts` do NTP e
  contador `seq`.
- **E5 — capotamento:** `evaluateRollover()` (função pura) trava o estado com 45° por 2 s ou 2,5 g
  de impacto e destrava com 20° por 10 s; buffer circular de 30 s a 1 Hz alimenta o `context` do
  evento `rollover`. LED vermelho a 5 Hz e buzzer contínuo, tudo temporizado por `millis()`.
  Detalhes na seção "Simulando capotamento" acima.
- **E7 — display OLED:** SSD1306 128×64 no mesmo I2C do MPU6050, redesenhado a no máximo 4 Hz e
  só quando o conteúdo muda (`screenChanged`, pura). Detalhes na seção "Display OLED" acima.
- **E8 — botão de ocorrência:** pino 33 com debounce de 50 ms (`debounceButton`, pura), evento
  `incident_report` com os 30 s de contexto do E5 e 3 bipes de confirmação por `millis()`.
- **E6 — ambiente e regra dos 30:** DHT22 a cada 2 s com tolerância de 10 s a falhas
  (`updateEnvHold`, pura) e `fireConditions()` (pura) publicando `fire_conditions` na telemetria.
  Detalhes na seção "Regra dos 30" acima.

O primeiro nível já é calculado e os LEDs acendem **antes** de o Wi-Fi ser ligado: sem rede o boot
pode levar até 20 s, e nesse tempo o operador precisa ver o alerta, não três LEDs apagados. Se a
máquina ligar **já acima do limite**, o `setup()` enfileira o `tilt_alert` dessa entrada no 🔴 — não
houve transição, então o `updateAlert()` do `loop()` nunca a veria, e o contrato pede um evento por
entrada. A fila segura o evento até o MQTT conectar. Acima de 45° esse `tilt_alert` do boot fica
adiado, porque o E5 pode estar a 2 s de travar o estado (ver "Simulando capotamento").

Se o Wi-Fi cair, os LEDs, o buzzer e o cálculo de nível continuam: a reconexão do Wi-Fi e do MQTT é
temporizada com `millis()`, sem travar o `loop()` (o `mqtt.connect()` é síncrono, mas limitado a 2 s
por `setSocketTimeout`). Um evento que passe de 30 s na fila sem conseguir sair é descartado, para o
operador não receber um alerta velho depois que a rede volta. Se a fila de 3 slots encher, sai o
evento **mais antigo**: o alerta recente é o que interessa.

Todas as features do firmware (E1 a E8) estão implementadas. Do `config`, `risk_level` e `reason` continuam guardados sem uso na tela — o layout do E7 não tem linha sobrando para eles.

> `Preferences` (NVS) vem no próprio core Arduino do ESP32 — não entra no `lib_deps` nem no
> `libraries.txt`.

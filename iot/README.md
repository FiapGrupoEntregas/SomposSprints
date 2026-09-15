# iot/ — Firmware ESP32 (PlatformIO + Wokwi)

Dispositivo embarcado na máquina: um **inclinômetro com limite dinâmico**. Ele mede a inclinação
com o MPU6050, recebe da API o limite seguro do dia (via MQTT) e alerta o operador com LEDs e buzzer.

> O Wokwi **não simula Bluetooth**, então o ESP32 usa **Wi-Fi (`Wokwi-GUEST`) + MQTT** no broker
> público HiveMQ. Contrato completo em [docs/contrato-mqtt.md](../docs/contrato-mqtt.md).

## Estrutura

```
iot/
├── src/main.ino      # firmware (arquivo único, compatível com o Wokwi web)
├── platformio.ini    # build no PlatformIO (placa esp32dev, bibliotecas)
├── diagram.json      # circuito do Wokwi
├── wokwi.toml        # liga o firmware compilado ao simulador (VS Code)
└── libraries.txt     # bibliotecas para o Wokwi web
```

## Circuito

| Componente | Pino ESP32 | Feature |
|---|---|---|
| MPU6050 (SDA / SCL) | 21 / 22 | E1, E5 |
| DHT22 (dados) | 4 | E6 |
| LED verde / amarelo / vermelho (com resistor 220 Ω) | 25 / 26 / 27 | E2 |
| Buzzer | 14 | E2, E5 |
| VCC dos sensores | 3V3 | — |

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

| Inclinação | accel Y (g) | accel Z (g) | Uso na demo |
|---|---|---|---|
| 0° | 0.00 | 1.00 | parado no plano |
| 8° | 0.14 | 0.99 | verde com limite de 10° |
| 10° | 0.17 | 0.98 | limite com solo encharcado |
| 12° | 0.21 | 0.98 | 🔴 acima do limite do dia chuvoso |
| 15° | 0.26 | 0.97 | limite com solo seco |
| 60° | 0.87 | 0.50 | capotamento (E5) |

## Acompanhando as mensagens MQTT

- Navegador: HiveMQ WebSocket Client (http://www.hivemq.com/demos/websocket-client/). Conecte e assine `agrishield/fiap-sompo-2026/#`.
- Terminal: `mosquitto_sub -h broker.hivemq.com -t 'agrishield/fiap-sompo-2026/#' -v`.

## Estado atual

Hoje o firmware conecta no Wi-Fi e no MQTT, publica `status` (com LWT), assina `config`, lê o MPU6050
e o DHT22 e mostra as leituras no Serial. As features E2 a E8 estão marcadas com `TODO(Ex)` no código.

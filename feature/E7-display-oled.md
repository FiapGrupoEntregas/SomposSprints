# E7 — Display OLED

| Campo | Valor |
|---|---|
| Prioridade | P2 |
| Camadas | iot |
| Depende de | E2, E3 |
| Janela | só se sobrar tempo |
| Responsável | Dev |
| Status | 🟦 Em revisão (implementado; falta o GIF da simulação) |

## Objetivo

Mostrar ao operador a inclinação atual, o limite do dia e o motivo, sem depender do celular.

## Escopo

**Inclui**
- SSD1306 128×64 via I2C, no mesmo barramento do MPU6050 (endereços `0x3C` e `0x68`, sem conflito).
- Uma tela única, atualizada no máximo 4 vezes por segundo.

## Layout

Com a fonte padrão cabem 21 caracteres por linha (6 px cada), então o cabeçalho do rascunho não
cabia em uma linha só e virou duas:

```
AgriShield                W M   ← W = Wi-Fi ok, M = MQTT ok ("-" quando fora)
tractor-01
------------------------------
12.4°                           ← inclinação atual (fonte grande, tamanho 2)
Limite 10.0°
SOLO ENCHARCADO
PERIGO                          ← nível (invertido no 🔴 e no capotamento)
```

Sem acentos: a fonte padrão da Adafruit GFX não tem. O `°` sai do cp437 (`display.write(0xF8)`).

## Implementação

- `iot/platformio.ini` **e** `iot/libraries.txt`: `adafruit/Adafruit SSD1306` e `adafruit/Adafruit GFX Library`.
- `iot/diagram.json`: peça `board-ssd1306` (GND→GND, VCC→3V3, SCL→22, SDA→21).
- `drawScreen(const ScreenModel&)` desenha; `updateScreen()` é chamada a cada 250 ms e **só**
  desenha quando `screenChanged()` (função pura) acusa diferença no `ScreenModel` — uma atualização
  completa custa ~23 ms de I2C, e redesenhar a cada 250 ms sem necessidade roubaria tempo do IMU e
  do LED de 5 Hz do capotamento.
- Barramento I2C a **400 kHz** (`Wire.setClock`, e `clkDuring`/`clkAfter` iguais no construtor do
  display): a 100 kHz cada desenho levaria ~92 ms.
- `soilLabel()` e `levelLabel()` (puras) devolvem os textos sem acento.
- Sem display o firmware segue: `initDisplay()` loga `[oled] SSD1306 nao encontrado` e `drawScreen`
  vira no-op.

## Critérios de aceite

- [ ] A tela reflete a mudança de limite e de nível em ≤ 1 s — conferir no Wokwi (T6). A janela de
  verificação é de 250 ms e o desenho custa ~23 ms, então o pior caso fica em ~0,3 s.
- [ ] O MPU6050 continua funcionando no mesmo barramento — conferir no Wokwi (T6). Endereços
  distintos (`0x68` e `0x3C`) e ambos suportam 400 kHz.
- [x] Sem acentos na tela (`soilLabel`/`levelLabel`, verificados no host) e nenhum rótulo passa dos
  21 caracteres da linha.

## Tarefas

- [x] Bibliotecas (`platformio.ini` + `libraries.txt`) + diagrama (`board-ssd1306`)
- [x] `drawScreen` + `updateScreen` + `screenChanged` (pura)
- [x] Atualizar a tabela de pinos do `iot/README.md` e o status

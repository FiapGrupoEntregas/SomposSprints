# E7 — Display OLED

| Campo | Valor |
|---|---|
| Prioridade | P2 |
| Camadas | iot |
| Depende de | E2, E3 |
| Janela | só se sobrar tempo |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Mostrar ao operador a inclinação atual, o limite do dia e o motivo, sem depender do celular.

## Escopo

**Inclui**
- SSD1306 128×64 via I2C, no mesmo barramento do MPU6050 (endereços `0x3C` e `0x68`, sem conflito).
- Uma tela única, atualizada no máximo 4 vezes por segundo.

## Layout

```
AgriShield  tractor-01  W M     ← W = Wi-Fi ok, M = MQTT ok
   12.4°                         ← inclinação atual (fonte grande)
Limite 10.0  SOLO ENCHARCADO
PERIGO                           ← nível (invertido no 🔴)
```

Sem acentos: a fonte padrão da Adafruit GFX não tem.

## Implementação

- `iot/platformio.ini` **e** `iot/libraries.txt`: `adafruit/Adafruit SSD1306` e `adafruit/Adafruit GFX Library`.
- `iot/diagram.json`: peça `board-ssd1306` (GND→GND, VCC→3V3, SCL→22, SDA→21).
- `drawScreen()` chamada a cada 250 ms.

## Critérios de aceite

- [ ] A tela reflete a mudança de limite e de nível em ≤ 1 s.
- [ ] O MPU6050 continua funcionando no mesmo barramento.

## Tarefas

- [ ] Bibliotecas + diagrama
- [ ] `drawScreen`
- [ ] Atualizar a tabela de pinos do `iot/README.md` e o status

# E1 — Inclinômetro (MPU6050)

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | iot |
| Depende de | — |
| Janela | 19/09 |
| Responsável | Dev |
| Status | ✅ Pronto (ângulos conferidos no Wokwi em 25/09/2026; evidência registrada) |

## Objetivo

Medir de forma estável a inclinação lateral (**roll**) e frontal (**pitch**) da máquina. Todas as
outras features do dispositivo dependem dessa leitura.

## Escopo

**Inclui**
- Leitura do acelerômetro a **10 Hz** (a cada 100 ms, com `millis()`).
- Média móvel das últimas 5 leituras para roll, pitch e `accel_g`.
- `tiltDeg = max(|roll|, |pitch|)`, a grandeza comparada com o limite (E2).
- Log no Serial a 1 Hz.

**Não inclui**
- Fusão com o giroscópio (filtro complementar/Kalman): o acelerômetro basta para a máquina parada ou lenta.
- Calibração de offset (desnecessária no Wokwi).

## Regras e lógica

```
ax, ay, az em g (aceleração / 9,80665)
roll  = atan2(ay, az)                    → graus
pitch = atan2(-ax, sqrt(ay² + az²))      → graus
accel_g = sqrt(ax² + ay² + az²)
```

## Implementação (`iot/src/main.ino`)

- `const unsigned long IMU_INTERVAL_MS = 100;` e `const int FILTER_WINDOW = 5;`
- `struct ImuReading { float rollDeg, pitchDeg, accelG; }` + um buffer circular para a média.
- `readImu()` chamada no `loop()` a cada 100 ms; `averageImu(imuSamples, imuSampleCount)`
  (função pura) devolve o valor filtrado e `tiltFromImu(reading)` devolve o `tiltDeg`.
- Separar a leitura do DHT (E6) da leitura do IMU: são intervalos diferentes.

## Critérios de aceite

Com os valores da tabela do [iot/README.md](../iot/README.md#simulando-inclinação):
- [x] 0°, 10°, 15° e 60° lidos com erro ≤ 0,5° (conferidos no Wokwi; ver
  [evidência de 25/09/2026](../document/evidencias/2026-09-25-inclinometro-wokwi.md)).
- [x] Mudar o slider reflete no Serial em ≤ 1 s (amostra a 100 ms, média de 5 → 500 ms; log a 1 Hz).
- [x] Nenhum `delay()` dentro do `loop()`.

## Tarefas

- [x] Temporização a 10 Hz + média móvel
- [x] `tiltDeg` (`tiltFromImu`)
- [x] Conferir a tabela de ângulos no Wokwi
- [x] Atualizar o `iot/README.md` e o status

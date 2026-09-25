# E3 — Receber o limite do dia via MQTT

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | iot |
| Depende de | [contrato MQTT](../document/contrato-mqtt.md#config-w4--e3-retained) |
| Janela | 19/09 |
| Responsável | Dev |
| Status | 🟦 Em revisão (configuração retained e `limit_applied` observados no Wokwi; falta teste manual com `mosquitto_pub` e envio pelo W4) |

## Objetivo

Fazer o dispositivo **aplicar o limite calculado pela API**: o limite de 15° vira 10° quando o solo está encharcado.

## Escopo

**Inclui**
- Parse do `config` com ArduinoJson.
- Validação dos valores.
- Persistência em `Preferences` (NVS).
- Evento `limit_applied`.

**Não inclui**
- Cálculo de limite no dispositivo: isso é papel da API (ADR-004).

## Regras e lógica

- `tilt_limit_deg` aceito só no intervalo **[3, 45]**. Fora disso → ignora e loga `[config] limite inválido`.
- `warn_ratio` aceito no intervalo **[0,5, 0,95]**. Se vier ausente ou inválido, mantém o valor atual.
- Campos desconhecidos são ignorados. `wind_max_kmh` é guardado para o E6. `soil_state`, `risk_level` e `reason` são guardados para o E7.
- `valid_until` vencido (quando o NTP estiver sincronizado) → continua usando o limite, mas loga `[config] limite vencido`.
- Na inicialização: carrega da NVS. Se não houver nada, usa `DEFAULT_TILT_LIMIT_DEG` (15°).

## Implementação (`iot/src/main.ino`)

- `#include <ArduinoJson.h>` e `#include <Preferences.h>`
- `onMqttMessage` → se `topic == topicConfig`, chama `applyConfig(payload)`.
- `applyConfig`: faz `deserializeJson`, valida, atualiza as variáveis globais, grava em `prefs.putFloat("tilt_limit", …)` e publica `limit_applied`.
- Log: `[config] limite 15.0° → 10.0° (42 mm de chuva em 72 h)`.

> No Wokwi a flash pode ser zerada a cada simulação. Na simulação, quem garante o limite após o
> reinício é a mensagem **retained**. A NVS vale para o hardware real.

## Critérios de aceite

- [ ] `mosquitto_pub -r … -m '{"tilt_limit_deg":10}'` → o limite muda em ≤ 2 s e o nível do E2 é recalculado na hora (conferir no Wokwi, T6). Implementado: `applyConfig` marca `alertRecheckPending` e o `loop()` chama `updateAlert` na volta seguinte, < 10 ms.
- [x] Reiniciar a simulação → o limite retained 10° volta ao conectar (observado no Wokwi em 25/09/2026; ver [evidência](../document/evidencias/2026-09-25-inclinometro-wokwi.md)). Implementado: `connectMqtt` assina `topicConfig` com QoS 1 logo após o `connect`, e o `config` é retained.
- [x] `{"tilt_limit_deg": 90}` e JSON quebrado são ignorados sem travar (`isValidTiltLimit` + guarda de `DeserializationError` em `applyConfig`; verificado no host com o ArduinoJson do `libdeps`).
- [x] O evento `limit_applied` é publicado no broker (três envios confirmados no Serial do Wokwi em 25/09/2026; ver [evidência](../document/evidencias/2026-09-25-inclinometro-wokwi.md)). Implementado em `queueLimitApplied`, 3 envios pela fila.

## Tarefas

- [x] `applyConfig` + validação (`isValidTiltLimit`, `isValidWarnRatio`, `static_assert` das faixas)
- [x] Preferences (`loadConfigFromNvs` / `persistConfig`, namespace `agrishield`)
- [x] Evento `limit_applied`
- [ ] Teste com `mosquitto_pub` e com o botão do W4 (o botão depende do W4)
- [x] Atualizar o status

# E3 — Receber o limite do dia via MQTT

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | iot |
| Depende de | [contrato MQTT](../docs/contrato-mqtt.md#config-w4--e3-retained) |
| Janela | 19/09 |
| Responsável | Dev |
| Status | 🟨 conexão, LWT e assinatura do `config` prontos |

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

- [ ] `mosquitto_pub -r … -m '{"tilt_limit_deg":10}'` → o limite muda em ≤ 2 s e o nível do E2 é recalculado na hora.
- [ ] Reiniciar a simulação → o limite 10° volta ao conectar (retained).
- [ ] `{"tilt_limit_deg": 90}` e JSON quebrado são ignorados sem travar.
- [ ] O evento `limit_applied` aparece no broker.

## Tarefas

- [ ] `applyConfig` + validação
- [ ] Preferences
- [ ] Evento `limit_applied`
- [ ] Teste com `mosquitto_pub` e com o botão do W4
- [ ] Atualizar o status

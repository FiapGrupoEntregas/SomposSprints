# W4 — Limite dinâmico e envio ao ESP32

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api, front-web |
| Depende de | W3, I2 |
| Janela | 18/09 |
| Responsável | Dev |
| Status | ⬜ A fazer |

## Objetivo

Calcular o **limite seguro de inclinação do dia** para cada equipamento e enviá-lo à máquina. É o elo
entre a nuvem e o trator, e o momento "uau" da demo: *o limite muda com a chuva*.

## História de usuário

> Como **operador**, quero que **o trator já saiba qual é a inclinação segura de hoje**, para **ser avisado na hora certa, mesmo sem olhar o celular**.

## Escopo

**Inclui**
- Cálculo do limite pelo estado do solo da fazenda do equipamento ([regras-de-risco §4](../docs/regras-de-risco.md#4-limite-dinâmico-de-inclinação-w4)).
- Publicação do `config` (retained) no formato do [contrato](../docs/contrato-mqtt.md#config-w4--e3-retained).
- Publicação automática quando a API sobe e **a cada 1 h**, mais o envio manual pelo front.
- Suporte a `date` e `scenario`, para a demo enviar o limite do "dia chuvoso".

**Não inclui**
- Limite diferente por célula (não temos GPS, ver as limitações).

## Implementação

### API (`api/`)
- `app/services/limits.py`: `compute_device_limit(device, day_risk: DayRisk) -> DeviceLimit`, que monta `tilt_limit_deg`, `warn_ratio=0.8`, `soil_state`, `risk_level`, `wind_max_kmh`, `valid_until` (fim do dia, em epoch) e `reason` (**sem acento**).
- Rotas (`app/api/v1/routes/devices.py`):
  - `GET /api/v1/devices/{device_id}/limit?date=&scenario=` → `DeviceLimit`
  - `POST /api/v1/devices/{device_id}/limit/publish`, com corpo `{ "date": "2026-09-17", "scenario": "heavy_rain" }` (os dois opcionais) → publica e devolve o payload enviado. **503** se o MQTT estiver desconectado. **404** se o equipamento não existir.
- Tarefa periódica no lifespan (`asyncio.create_task`): publica o limite de hoje de todos os equipamentos a cada 3600 s.
- Grava o que foi publicado (`PublishedConfig`, I3).

### Front-web (`front-web/`)
- Página **Equipamento ao vivo**, card **"Limite de hoje"**:
  - número grande (`10,0°`), a comparação com o limite de referência (`15,0° em solo seco`) e o motivo;
  - seletor de dia e o toggle de cenário (os mesmos do W3);
  - botão **"Enviar ao equipamento"** → POST → `st.toast("Limite enviado ao tractor-01")`;
  - "Último envio: 14:02 — 10,0°".

## Critérios de aceite

- [ ] O limite calculado bate com §4 (15 / 12,5 / 10 para seco / úmido / encharcado).
- [ ] Clicar em "Enviar" → o Serial do Wokwi mostra o config recebido em ≤ 3 s.
- [ ] Reiniciar a simulação → o ESP32 recebe o último limite assim que conecta (retained).
- [ ] Com o MQTT desconectado, o front mostra um erro amigável e não trava.
- [ ] O `reason` publicado não tem acentos.

## Testes

- `tests/test_limits.py`: cálculo e `reason` sem acento.
- `tests/test_devices_limit_route.py`: GET, POST com a ponte fake (verifica o tópico, o retained e o payload), 404 e 503.

## Tarefas

- [ ] `compute_device_limit` + testes
- [ ] Rotas + ponte fake nos testes
- [ ] Tarefa periódica
- [ ] Card no front
- [ ] Teste ponta a ponta com o Wokwi + atualizar os READMEs e o status

# W4 — Limite dinâmico e envio ao ESP32

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api, front-web |
| Depende de | W3, I2 |
| Janela | 18/09 |
| Responsável | Dev |
| Status | 🟨 API + card no front prontos; falta a conferência no Wokwi |

## Objetivo

Calcular o **limite seguro de inclinação do dia** para cada equipamento e enviá-lo à máquina. É o elo
entre a nuvem e o trator, e o momento "uau" da demo: *o limite muda com a chuva*.

## História de usuário

> Como **operador**, quero que **o trator já saiba qual é a inclinação segura de hoje**, para **ser avisado na hora certa, mesmo sem olhar o celular**.

## Escopo

**Inclui**
- Cálculo do limite pelo estado do solo da fazenda do equipamento ([regras-de-risco §4](../document/regras-de-risco.md#4-limite-dinâmico-de-inclinação-w4)).
- Publicação do `config` (retained) no formato do [contrato](../document/contrato-mqtt.md#config-w4--e3-retained).
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
- Tarefa periódica no lifespan (`asyncio.create_task`): publica o limite de hoje a cada 3600 s
  (`AGRISHIELD_MQTT_PUBLISH_INTERVAL_S`). O trabalho pesado vai para uma *thread*
  (`asyncio.to_thread`), e a tarefa é cancelada e aguardada no shutdown.
- Grava o que foi publicado (`PublishedConfig`, I3).

#### Decisão: para quem a tarefa periódica publica

**Só para os equipamentos que já se anunciaram** (têm linha em `device_status`). O broker é
público e `config` é **retained**: publicar para o catálogo inteiro deixaria mensagem órfã
pendurada para sempre em `harvester-01` e `tractor-02`, que não têm firmware. O equipamento que
nunca falou recebe o limite pelo botão **"Enviar ao equipamento"** (a rota manual, que é o momento
da demo) e, a partir daí, entra sozinho na lista — `device_status` sobrevive ao reinício da API.
*(Decisão da W4; testada em `tests/test_limit_publisher.py`.)*

### Front-web (`front-web/`)
- Página **Equipamento ao vivo**, card **"Limite de hoje"**:
  - número grande (`10,0°`), a comparação com o limite de referência (`15,0° em solo seco`) e o motivo;
  - seletor de dia e o toggle de cenário (os mesmos do W3);
  - botão **"Enviar ao equipamento"** → POST → `st.toast("Limite enviado ao tractor-01")`;
  - "Último envio: 14:02 — 10,0°".

## Critérios de aceite

- [x] O limite calculado bate com §4 (15 / 12,5 / 10 para seco / úmido / encharcado).
- [ ] Clicar em "Enviar" → o Serial do Wokwi mostra o config recebido em ≤ 3 s. *(API ✅ e botão do front ✅ — publicação verificada contra a API real, com toast e "Último envio: HH:MM — X°"; falta só a conferência no Wokwi.)*
- [x] Reiniciar a simulação → o ESP32 recebe o último limite assim que conecta (retained) — verificado com um assinante que entrou depois da publicação.
- [x] Com o MQTT desconectado, a API responde **503 "Equipamento sem conexão MQTT"** (o erro amigável na tela é do front).
- [x] O `reason` publicado não tem acentos.

## Testes

- `tests/test_limits.py`: cálculo e `reason` sem acento.
- `tests/test_devices_limit_route.py`: GET, POST com a ponte fake (verifica o tópico, o retained e o payload), 404 e 503.

## Tarefas

- [x] `compute_device_limit` + testes
- [x] Rotas + ponte fake nos testes
- [x] Tarefa periódica
- [x] Card no front
- [ ] Teste ponta a ponta com o Wokwi
- [x] Atualizar os READMEs, `document/arquitetura.md` e o status

# Validação de integração ponta a ponta — 20/09/2026

**Feature:** [I6 — Simulador de dispositivo e testes de ponta a ponta](../../feature/I6-simulador-e2e.md)
**Executado por:** agente `qa-integracao`
**Veredito:** ⚠️ **fluxo validado com uma ressalva** (D-1, **já corrigido pelo `dev-api` em 20/09**; ver [o registro](#defeito-encontrado))

---

## O que foi validado

O caminho inteiro, sem Wokwi e sem mão humana no meio:

```
simulate_device.py → broker.hivemq.com → ponte MQTT (I2) → SQLite (I3) → endpoints (W4, W5)
```

- **API de verdade** (`uvicorn app.main:app`), em processo separado, banco SQLite novo em arquivo
  temporário e chave de API própria.
- **Broker público de verdade** (`broker.hivemq.com:1883`), com **prefixo de tópico exclusivo por
  execução** (`agrishield/fiap-sompo-2026-e2e-<8 hex>`): nada do que os testes publicam chega ao
  prefixo da demo, e nenhuma mensagem de terceiros entra na contagem.
- **Equipamento `tractor-02`**, não o `tractor-01` da apresentação. Todo cenário roda com
  `--clean-retained`, que apaga `status` e `config` retidos no broker ao terminar.

## Ambiente

| Item | Valor |
|---|---|
| Data/hora | 2026-09-20T11:44:49-03:00 |
| Python | 3.12.13 · pytest 9.1.1 |
| Broker | `broker.hivemq.com:1883` (TCP, sem TLS) |
| Clima | Open-Meteo real (o cenário de limite depende dela) |
| Banco | SQLite em arquivo temporário, criado do zero pela execução |

## Comando

```bash
cd api && uv run pytest -m e2e -s -v
```

Saída completa: [`logs/2026-09-20-pytest-e2e.log`](logs/2026-09-20-pytest-e2e.log) (293 linhas).

```
========== 15 passed, 586 deselected, 2 warnings in 117.30s (0:01:57) ==========
```

Os 586 "deselected" são os testes unitários: os `e2e` estão atrás da marca `e2e` e **não rodam na
CI** (`addopts = "-m 'not e2e'"` em `api/pyproject.toml`).

---

## Cenários

| # | Cenário | Resultado | Números medidos |
|---|---|---|---|
| 1 | `normal` — 6 leituras | ✅ 6/6 gravadas, campo a campo idênticas | visíveis na API em < 0,01 s após o fim |
| 2 | `alerta` — a inclinação cruza o limite | ✅ 🟢→🟡→🔴 e 1 `tilt_alert` gravado (3 cópias) | alerta em 10,4° com limite de 10° |
| 3 | `capotamento` — 60° por 3 s | ✅ 3 cópias → **1 linha**, contexto de 30 linhas íntegro | payload de 773 bytes, hash SHA-256 conferido |
| 4 | `sujo` — 11 payloads inválidos | ✅ **0 gravados**, API e ponte de pé | 1 sentinela gravada depois do lixo |
| 5 | `rajada` — 100 mensagens seguidas | ✅ **100/100 gravadas, 0 perdidas, 0 duplicadas** | 19,9 msg/s (5,02 s) |
| 6 | `queda` — dispositivo cai e volta | ✅ LWT `offline` registrado, coleta retomada | 1,32 s entre reconectar e gravar de novo |
| 7 | queda do **broker** (proxy TCP) | ⚠️ recuperava sozinha, mas só ~1 min depois | ver [D-1](#defeito-encontrado), **corrigido em 20/09** |
| 8 | W4 — limite publicado chega ao equipamento | ✅ | **0,307 s** (orçamento: 3 s) |
| 9 | W4 — equipamento novo recebe o `config` retained | ✅ | 0,141 s |
| 10 | W5 — nível e gráfico depois de mudar a inclinação | ✅ | **0,392 s** e **0,408 s** (orçamento: 1 s e 5 s) |
| 11 | Exceções (equipamento nunca visto, desconhecido, sem chave, parâmetro fora de faixa, data sem previsão) | ✅ 5 testes, nenhum 500 | — |

---

### 1. `normal` — o fluxo inteiro, com consistência campo a campo

Comando e saída exatamente como estão no log anexado (linha 254 em diante), com o prefixo que
**esta** execução sorteou:

```
$ .venv/bin/python scripts/simulate_device.py --scenario normal --device-id tractor-02 \
    --prefix agrishield/fiap-sompo-2026-e2e-c62dff50 --host broker.hivemq.com --port 1883 \
    --summary-json /tmp/pytest-of-ntl/pytest-103/test_normal_scenario_reaches_d0/sim-normal-bf5800.json \
    --clean-retained --count 6 --interval 0.5
[sim] conectado a broker.hivemq.com:1883 em 0.99 s
[sim] status -> online (retained)
[sim] telemetria seq=1 roll=4.2° nível=green
...
[sim] telemetria seq=6 roll=6° nível=green
[e2e] 6/6 leituras gravadas; última visível 0.00 s após o fim do cenário
```

O teste compara **campo a campo** (`seq`, `roll_deg`, `pitch_deg`, `accel_g`, `temp_c`,
`humidity_pct`, `fire_conditions`, `tilt_limit_deg`, `alert_level`) o JSON publicado com a linha
do SQLite e com o que `/telemetry/latest` devolve. Nenhuma diferença — inclusive nos floats que a
ArduinoJson serializa sem casa decimal (`roll=6`, `tilt_limit_deg=10`), que o pydantic coage para
`float` sem alterar o valor.

### 2. `alerta` — o nível sobe e o evento sai uma vez só

```
[sim] telemetria seq=4 roll=7.9° nível=green
[sim] telemetria seq=5 roll=9.1° nível=yellow
[sim] telemetria seq=6 roll=10.4° nível=red
[sim] evento 'tilt_alert' id=tractor-02-1789915597-1 (167 bytes) × 3 cópias
[e2e] alerta: níveis gravados ['green','green','green','green','yellow','red','red','red']
      (evento tractor-02-1789915597-1 em 10.4°, visível 0.00 s depois)
```

As 3 cópias viraram **1 linha** em `device_event` e **1 linha** em `decision_log`
(`decision_type=alert`, `source=device`), com entrada (`roll_deg`, `tilt_limit_deg`) e saída
(`event_id`, `payload_sha256`) registradas.

### 3. `capotamento` — dedup, contexto e trilha

```
[sim] telemetria seq=1 roll=60° nível=rollover
[sim] telemetria seq=2 roll=60° nível=rollover
[sim] telemetria seq=3 roll=60° nível=rollover
[sim] evento 'rollover' id=tractor-02-1789915575-1 (773 bytes) × 3 cópias
[e2e] capotamento: 3 cópias publicadas (773 bytes cada), 1 linha gravada
      (tractor-02-1789915575-1), visível 0.00 s depois
```

Conferido:

- `device_event`: exatamente 1 linha para o `event_id`;
- `context.rows`: **30 linhas**, idênticas às publicadas (comparação de lista inteira);
- `event_integrity`: `payload_sha256` igual ao SHA-256 dos bytes exatos publicados, e
  `payload_bytes` igual ao tamanho do payload;
- `decision_log`: 1 decisão do tipo `alert`, com o mesmo hash na saída;
- `GET /devices/tractor-02/events` devolve o evento com as 30 linhas de contexto.

### 4. `sujo` — 11 maneiras de quebrar, nenhuma derruba

Publicados (e **todos descartados**): JSON truncado, campo obrigatório faltando, `fire_conditions=9`
(fora da faixa 0–3), `tilt_limit_deg=0`, `alert_level=purple`, `device_id` do payload diferente do
tópico, JSON que não é objeto, payload vazio, evento sem `event_id`, tipo de evento inexistente e
`state` inválido no `status`.

```
[sim] fim: 13 publicações (2 válidas, 11 inválidas), 0 config(s) recebido(s), 2.8 s
[e2e] sujo: 11 payloads inválidos descartados, 1 sentinela gravada (seq=1, visível 0.00 s depois)
```

Cada descarte deixou rastro no log estruturado da API. O arquivo
[`logs/2026-09-20-api-descartes-sujo.log`](logs/2026-09-20-api-descartes-sujo.log) traz **20
descartes e 2 acessos negados** da sessão inteira: os 11 do cenário `sujo` (1 JSON quebrado, 1
payload vazio e 9 reprovados no schema, no `device_id` ou por não serem objeto JSON), mais 9
mensagens de zero byte que são o **`--clean-retained` dos outros cenários** (apagar um retained em
MQTT é publicar payload vazio, e a ponte registra isso como descarte — ver a nota no fim desta
seção) e os 2 acessos negados do teste de chave de API. Exemplos:

```json
{"level":"WARNING","logger":"app.mqtt.bridge","message":"O 'device_id' do payload ('intruso-99') não é o do tópico ('tractor-02'); descartado. O broker é público, então a API não confia no payload divergente."}
{"level":"WARNING","logger":"app.mqtt.bridge","message":"Payload de .../telemetry fora do contrato; descartado. fire_conditions: Input should be less than or equal to 3"}
```

Depois do lixo todo — a sentinela é publicada **por último**, depois dos 11 inválidos — ela foi
gravada, e `/health`, `/telemetry/latest` e `/events` seguiram respondendo 200: a thread da ponte
sobreviveu.

> **Achado colateral, já encaminhado ao `dev-api`:** apagar um retained é publicar payload de zero
> byte, e a ponte loga cada um como `JSON inválido ... mensagem descartada`. Ou seja, toda limpeza
> de retained gera alarme falso na mesma trilha que a I5 usa para auditoria (as 9 linhas extras
> acima). Não afeta dado nenhum, mas polui o log.

### 5. `rajada` — 100 mensagens, nenhuma perdida

```
[e2e] rajada: 100 publicadas em 5.016 s (19.9 msg/s), 100 gravadas, 0 perdida(s);
      todas visíveis 0.00 s após o fim da rajada
```

Os 100 `seq` gravados são exatamente os 100 publicados, **na mesma ordem e sem repetição**, e os
valores de `roll_deg`, `tilt_limit_deg` e `alert_level` batem um a um.

> Observação honesta: a telemetria é QoS 0, como no firmware. Esta execução não perdeu nada, mas
> o QoS 0 **não garante** isso — é por essa razão que o `seq` existe no contrato. O teste relata a
> perda quando houver, em vez de escondê-la.

### 6. `queda` — o dispositivo cai sem avisar e volta

O simulador **mata o socket sem enviar DISCONNECT** (como um ESP32 que perde energia), fica 12 s
fora e reconecta:

```
[e2e] queda: fora do ar por 12.0 s; primeira mensagem nova 1.32 s após o início da reconexão
[e2e] queda: 3 leitura(s) gravada(s) depois da reconexão (a partir de seq=3), visíveis 0.00 s depois
```

O broker publicou o **LWT** e a API registrou `Equipamento tractor-02 está offline.`, seguido de
`... está online.` depois da volta. Critério do I6 (voltar a gravar em menos de 30 s): **1,32 s**.

### 8–10. Os tempos que a W4 e a W5 cobram

| Critério | Onde | Medido | Orçamento |
|---|---|---|---|
| `config` chega ao dispositivo depois do clique (W4) | `POST /devices/tractor-02/limit/publish` → simulador | **0,307 s** (API respondeu em 0,017 s; broker → dispositivo, 0,290 s) | 3 s |
| Nível novo disponível para o painel (W5) | inclinação muda 5° → 17,3° → `/telemetry/latest` | **0,392 s** | 3 s no total, dos quais 2 s são a recarga do front |
| Ponto novo na série do gráfico (W5) | → `/telemetry?minutes=10` | **0,408 s** | 7 s no total, idem |

O que o teste mede é o pedaço **broker → ponte → banco → API**. O front (Streamlit) recarrega o
bloco ao vivo a cada 2 s (`LIVE_REFRESH = "2s"` em `front-web/views/equipment.py`), então o tempo
que o operador vê é o medido **mais** até 2 s de espera pela próxima recarga: ≈ 2,4 s para o nível
e ≈ 2,4 s para o gráfico, dentro dos 3 s e 7 s. A confirmação visual na tela continua sendo
verificação manual (ver abaixo).

O `config` publicado é **idêntico** ao que a rota disse ter publicado (comparação do JSON inteiro),
ficou gravado em `published_config` e gerou decisão em `decision_log` (`tilt_limit`, `source=api`,
com `rule_version`). Um segundo dispositivo, ligando depois, recebeu o mesmo `config` **retained**
em 0,141 s.

### 11. Exceções e mundo real

| Caso | Resposta |
|---|---|
| Equipamento do catálogo que nunca publicou (`harvester-01`) | `status` 200 `offline`, `telemetry/latest` 404 "Sem telemetria ainda", série vazia, eventos `[]` |
| Equipamento fora do catálogo | 404 "Equipamento não encontrado" em todas as rotas |
| `POST .../limit/publish` sem `X-API-Key` | 401 |
| `/audit` com chave errada | 401; com a chave certa, 200 |
| `minutes=0`, `minutes=99999`, `limit=0`, `limit=9999` | 422 |
| Limite de uma data fora da janela da previsão | 404 explicando a janela |

Nenhum 500 em nenhum caso.

---

## Fora da CI, e por quê

Os `e2e` dependem de internet, do broker público e de ~2 min de relógio. Eles ficam atrás da marca
`e2e`, registrada em `api/pyproject.toml` (`markers = ["e2e: ..."]`, sem aviso do pytest) e
excluídos por `addopts = "-m 'not e2e'"`. A execução padrão — a mesma da CI — não roda nenhum
deles:

```
$ cd api && uv run pytest -q
586 passed, 15 deselected, 2 warnings in 9.51s

$ cd api && uv run pytest --collect-only -q -m e2e
15/601 tests collected (586 deselected) in 0.42s
```

(Contagens de 20/09/2026, 11h45. O total de unitários cresce a cada feature; o que importa aqui é
que os 15 `e2e` aparecem sempre como *deselected* na execução padrão.)

### Sobre o `status` `online` e a regra dos 20 s (W5)

O `/status` vira `offline` depois de 20 s sem telemetria, mesmo com o `status` retained dizendo
`online`. Os testes consultam logo após o cenário, e o helper `assert_online` **mede o silêncio**
antes de concluir qualquer coisa: se ele passar de 20 s, a falha diz que a consulta demorou demais
e que o resultado não fala sobre o sistema. Nesta execução o silêncio medido foi de **0,4 s** nos
dois pontos:

```
[e2e] status do painel: online (0.4 s desde a última telemetria)
[e2e] queda: painel de volta em `online` (0.4 s desde a última telemetria)
```

### Consumo de API externa

Os seis cenários **não tocam a Open-Meteo**: as rotas do painel (W5) leem só o SQLite. Quem
depende dela são os dois testes de limite (W4) e o de data fora da janela — e os três rodam no
**mesmo processo de API**, o da fixture de sessão, onde o cache de 24 h do relevo e o de 1 h da
previsão fazem a sessão inteira custar **uma consulta de elevação (a grade 10 × 10 do Vinhedo Vale
da Encosta) e uma de previsão**. A execução sobe **dois** processos de API (o log mostra
`API no ar` duas vezes): o segundo é o do teste de queda do broker, que só publica telemetria e
não chama a Open-Meteo. Nada de rajada contra ela.

---

## Defeito encontrado

<a id="defeito-encontrado"></a>

### 🟡 D-1 — a API demorava ~1 min para perceber que o broker caiu · **corrigido em 20/09/2026**

> **Status:** corrigido pelo `dev-api` **depois** desta medição: hoje existe
> `mqtt_keepalive_s = 15` em `app/core/config.py`, passado ao `connect_async`. A medição abaixo é
> do código anterior e fica como registro histórico — quem repetir o teste agora vai ver números
> menores.

**Onde (no código de 20/09, antes da correção):** `api/app/mqtt/bridge.py`, `MqttBridge.start()` —
`connect_async(host, port)` sem `keepalive`, então valia o padrão do paho (**60 s**), e não havia
`mqtt_keepalive_s` em `app/core/config.py`.

**O que acontece:** com o broker derrubado no meio da coleta, a API só registra
`Ponte MQTT desconectada` **57,9 s depois** da queda, e só então reconecta e reassina:

```
[e2e] broker derrubado (proxy morto)
[e2e] broker de volta 3.0 s depois
[e2e] a API percebeu a queda 57.9 s depois dela
[e2e] API reassinou os três tópicos 56.9 s depois de o broker voltar (59.9 s depois da queda)
[e2e] telemetria nova gravada depois da volta (seq=3, 57.2 s depois de o broker voltar)
[e2e] seq gravados: [1, 3] — a leitura publicada durante a queda (seq=2) se perdeu, como manda o QoS 0
```

Ou seja: **mesmo que o broker volte em 3 s, o painel fica cego por cerca de 1 minuto.** O `backoff`
de reconexão (`mqtt_reconnect_min_s=1`, `max=30`) não é o gargalo — o gargalo é a detecção.

**Como reproduzir:** `cd api && uv run pytest -m e2e tests/e2e/test_broker_outage.py -s`.

**Impacto:** não derruba nada e a recuperação é automática (o critério do I2 continua atendido),
mas numa demo com Wi-Fi instável a tela pode ficar até 1 min sem dados novos, sem explicação.

**Sobre o número, com honestidade:** 57,9 s **não é uma constante**. O paho manda o PINGREQ ao fim
do intervalo de keepalive e só declara a conexão morta se o PINGRESP não vier no intervalo
seguinte, então a detecção fica **entre 1× e 2× o keepalive** — com 60 s, algo entre ~60 s e
~120 s, e esta execução pegou a ponta rápida (57,9 s). Pela mesma conta, o `mqtt_keepalive_s = 15`
já aplicado pelo `dev-api` leva a detecção para **~15 a ~30 s**, não para os "~22 s" que eu havia
estimado antes.

**Corrigido por:** `dev-api`, em 20/09/2026 (`mqtt_keepalive_s = 15` em `Settings`, passado a
`connect_async(..., keepalive=...)`).

> Isto é uma **ressalva**, não uma reprovação: o sistema se recupera sozinho, sem intervenção, e
> nenhuma mensagem já gravada se perdeu.

---

## Verificação manual necessária (humano)

Estes pontos exigem olho na tela ou o Wokwi, e o agente não faz nenhum dos dois:

1. **Wokwi + painel, ponta a ponta.** Abrir o projeto em `iot/`, deixar o ESP32 publicando e
   confirmar no painel do front que o nível muda em até 3 s e o gráfico em até 7 s ao girar o
   potenciômetro (o teste mediu o caminho até a API; falta o desenho na tela).
2. **Botão "Enviar ao equipamento" (W4).** Clicar no front e conferir no Serial do Wokwi que o
   limite novo chegou e foi aplicado (evento `limit_applied`), cronometrando os 3 s.
3. **Desligar o ESP32 no Wokwi** e conferir que o painel passa a "offline" (LWT), e que volta a
   "online" ao religar.
4. **Print das telas** para a entrega, salvando em `document/evidencias/prints/`.

## Arquivos desta evidência

- [`logs/2026-09-20-pytest-e2e.log`](logs/2026-09-20-pytest-e2e.log) — saída completa da execução
- [`logs/2026-09-20-api-descartes-sujo.log`](logs/2026-09-20-api-descartes-sujo.log) — log JSON da
  API com os 11 descartes do cenário `sujo` e os acessos negados sem chave

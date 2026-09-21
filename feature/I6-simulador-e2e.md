# I6 — Simulador de dispositivo e testes de ponta a ponta

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api, testes |
| Depende de | I2, I3 |
| Janela | 22/09 |
| Responsável | qa-integracao |
| Status | ✅ Pronto |

## Objetivo

Validar o fluxo completo **sem depender do Wokwi** e produzir as **evidências de validação da
integração** que o enunciado cobra: confiabilidade da coleta e consistência dos dados.

## Escopo

**Inclui**
- `scripts/simulate_device.py`: publica telemetria e eventos no broker seguindo o [contrato MQTT](../document/contrato-mqtt.md), com cenários:
  - `normal` — inclinação baixa, 5 s de intervalo;
  - `alerta` — inclinação sobe até passar do limite (gera `tilt_alert`);
  - `capotamento` — 60° por 3 s, com `rollover` e janela de contexto;
  - `sujo` — payloads inválidos (JSON quebrado, campo faltando, valor fora de faixa);
  - `rajada` — 100 mensagens seguidas, para medir perda;
  - `queda` — desconecta no meio e reconecta.
- Assina `config` e imprime o limite recebido (valida o W4 sem o Wokwi).
- `tests/e2e/`: testes marcados com `@pytest.mark.e2e`, que sobem a API, rodam o simulador e conferem o banco e os endpoints.
- Relatórios em `document/evidencias/`.

**Não inclui**
- Substituir o Wokwi na demo. O simulador é ferramenta de teste, e a demo usa o dispositivo simulado no Wokwi.

## Critérios de aceite

- [x] Cenário `rajada`: 100 mensagens publicadas → 100 gravadas, sem duplicidade (relatar perda se houver). **100/100, 0 perdidas, 0 duplicadas** (20/09/2026).
- [x] Cenário `sujo`: nenhuma mensagem inválida é gravada e a API continua respondendo. **11 payloads inválidos descartados, sentinela gravada.**
- [x] Cenário `capotamento`: 1 evento gravado mesmo com os 3 envios, com contexto completo e trilha de auditoria. **1 linha, 30 linhas de contexto, hash e `decision_log` conferidos.**
- [x] Cenário `queda`: depois de reconectar, as mensagens voltam a ser gravadas em menos de 30 s. **1,32 s** (e o LWT `offline` chegou à API).
- [x] Consistência: os valores gravados são idênticos aos publicados (comparação campo a campo).
- [x] Os testes `e2e` ficam fora da CI padrão e rodam com `uv run pytest -m e2e`.
- [x] Cada execução gera um relatório em `document/evidencias/` com a saída real.

## Tarefas

- [x] Simulador com os 6 cenários — `scripts/simulate_device.py`
- [x] Testes `tests/e2e/` — `api/tests/e2e/` (15 testes, marca `e2e`)
- [x] Relatórios de evidência — [`document/evidencias/2026-09-20-integracao-ponta-a-ponta.md`](../document/evidencias/2026-09-20-integracao-ponta-a-ponta.md)
- [x] Documentar o uso no `README.md`, no `scripts/readme.md` e no `document/ambiente-de-desenvolvimento.md`

## Resultado da validação (20/09/2026)

15 testes `e2e`, todos passando em 2 min. Tempos medidos que outras features cobravam:

| Medida | Valor | Orçamento |
|---|---|---|
| `config` do limite chega ao equipamento depois do clique (W4) | 0,307 s | 3 s |
| Nível novo disponível para o painel (W5) | 0,392 s (+ até 2 s de recarga do front) | 3 s |
| Ponto novo no gráfico (W5) | 0,408 s (+ até 2 s de recarga do front) | 7 s |

**Ressalva aberta (para o `dev-api`):** com o broker fora do ar, a API leva ~58 s para *perceber*
a queda (keepalive padrão do paho, 60 s, em `MqttBridge.start()`), e só então reconecta e
reassina. Ela se recupera sozinha, mas o painel pode ficar até 1 min sem dados novos. Detalhes e
saída real no relatório de evidência.

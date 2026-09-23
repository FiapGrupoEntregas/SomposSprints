# 21/09/2026 — O sistema com a Open-Meteo realmente fora do ar

> Esta evidência **só podia ser produzida hoje**. A cota diária da Open-Meteo foi esgotada pela
> geração do dataset da D2 (ver [a nota da manhã](2026-09-21-retomada-d2-e-fechamento-w11-w13.md)),
> e os três subdomínios passaram a recusar toda chamada até as 00:00 UTC. Isso deu a chance de
> conferir a degradação **contra a indisponibilidade real**, e não contra um mock — que é o que os
> testes de `api/tests/` fazem, por construção.
>
> O critério 6 da US-03 pede: *"com a Open-Meteo fora do ar e sem cache, a tela mostra 'Serviço de
> clima indisponível' (HTTP 503) — não uma tela quebrada"*. Até hoje isso estava coberto só por
> teste com resposta simulada.

## 1. O que responde e o que não responde sem cota

API subida com `AGRISHIELD_MQTT_ENABLED=false`, cache vazio (processo novo), banco local intacto.

| Rota | Depende de clima? | Resultado |
|---|---|---|
| `GET /api/v1/health` | não | **200** `{"status":"ok",...}` |
| `GET /api/v1/farms` | não | **200** — as 3 fazendas |
| `GET /api/v1/farms/{id}/terrain` | sim (elevação) | **503** `{"detail":"Serviço de clima indisponível"}` |
| `GET /api/v1/farms/{id}/risk` | sim | **503** — mesma mensagem |
| `GET /api/v1/devices/{id}/limit` | sim | **503** — mesma mensagem |
| `GET /api/v1/reports/crop` | não (só PSR) | **200** — 9 culturas |
| `GET /api/v1/replay/cases` | não | **200** — os 5 casos reais |
| `GET /api/v1/audit` | não | **401** — sem chave de API, como manda a I5 (falha fechada) |

**Nenhum traceback, nenhum 500.** A indisponibilidade vira 503 com mensagem em português, e o que
não depende de clima continua servindo normalmente.

## 2. Pelo caminho que a tela usa

As mesmas chamadas pelo cliente do front (`front-web/services/api_client.py`), que é o código que
as telas executam:

```
OK       list_farms       → resposta com 3 itens
ApiError get_terrain      → Serviço de clima indisponível (HTTP 503 em /farms/cafe-carmo-de-minas/terrain)
ApiError get_risk         → Serviço de clima indisponível (HTTP 503 em /farms/cafe-carmo-de-minas/risk)
OK       get_crop_report  → resposta com 9 itens
```

O cliente traduz o 503 em `ApiError` com o texto da API, que é o que as telas exibem com
`st.error`. **Ressalva do que esta evidência cobre:** foi verificada a cadeia API → cliente do
front. A renderização da mensagem na tela continua coberta por teste de front com a API mockada
(`front-web/tests/test_pages.py`), não por captura visual — o print correspondente ainda não existe.

## 3. Testes de ponta a ponta (I6) no mesmo estado

```bash
cd api && uv run pytest -m e2e -q
```

```
3 failed, 12 passed, 835 deselected, 2 warnings in 73.51s
```

**Os 12 que passaram** são os que não dependem de clima: fluxo de telemetria, rajada de 100
mensagens, payload sujo descartado, capotamento com contexto, queda e reconexão do broker, LWT,
auditoria. **Ou seja: nada do que mudou hoje (W11, W13, D2/D3) quebrou a integração.**

**Os 3 que falharam, e por quê:**

| Teste | Falha |
|---|---|
| `test_edge_cases.py::test_limit_for_a_date_outside_the_forecast_window` | `assert 503 == 404` |
| `test_limit_publish.py::test_published_limit_reaches_the_device_in_time` | `{"detail":"Serviço de clima indisponível"}` |
| `test_limit_publish.py::test_device_receives_retained_config_when_it_connects` | `assert 503 == 200` |

Os três esbarram na mesma parede: **sem clima não há limite do dia para calcular**, e sem limite
não há `config` para publicar no broker. Não são regressão — são o 503 aparecendo onde o teste
esperava um caminho feliz. Reconferir depois das 00:00 UTC.

## 4. O que isso significa para a demo

Este é o achado prático, e ele é mais duro do que o "aqueça o cache antes" que já estava escrito:

- **Com a cota esgotada, não é só o mapa que cai.** Caem o **relevo**, a **previsão de risco**, o
  **limite do dia** e, por consequência, a **publicação do limite no MQTT** — ou seja, o ESP32 não
  recebe `config` nenhum. A parte da demo que mostra "a máquina recebeu o limite de hoje" depende
  de clima tanto quanto o mapa.
- **O que sobrevive** é o suficiente para não haver tela branca: fazendas, relatórios por
  cultura/região/equipamento, replay dos casos reais, painel ao vivo com telemetria e a trilha de
  auditoria. Uma demo de emergência existe — mas não é a demo.
- **Aquecer o cache deixa de ser recomendação e vira pré-requisito.** `./scripts/run-demo.sh
  --aquecer` precisa rodar **com cota disponível** e o processo da API **não pode ser reiniciado**
  depois, porque o cache é em memória: reiniciar apaga o aquecimento.
- **Não gere dataset no dia da apresentação.** Uma execução do `build_dataset.py` consome a cota do
  dia inteiro (ver a nota da manhã: ~21 chamadas por requisição de clima, teto prático de ~465
  apólices/dia).

Scripts auxiliares do projeto (deploy, manutenção, automações).

| Script | O que faz |
|---|---|
| `run-demo.sh` | **Sobe a demo inteira** (I4): confere o ambiente e as duas chaves de API, sobe API e front, espera o `/health`, aquece o cache das fazendas com `--aquecer` e derruba os dois com **um Ctrl+C**. Linux e macOS; no Windows use o WSL ou os dois terminais do README. |
| `sync-requirements.sh` | Atualiza o `uv.lock` e regenera `requirements.txt` / `requirements-dev.txt` de `api/` e `front-web/` (mantém uv e pip em sincronia). A CI verifica. |
| `create-labels.sh` | Cria ou atualiza as labels padrão do GitHub (precisa do `gh` autenticado). |
| `download_psr.py` | Baixa os CSVs do PSR/SISSER e o dicionário de dados para `data/raw/` (D1). Idempotente; envia user-agent de navegador porque o portal do Mapa recusa o padrão. |
| `build_psr_sample.py` | Gera `data/sample/psr_amostra.csv`: 453 linhas reais, estratificadas por evento e **anonimizadas** (D1). |
| `load_psr.py` | Roda o pipeline de ingestão e imprime o relatório de qualidade. `--sample` usa só a amostra versionada. |
| `simulate_device.py` | **Simulador de dispositivo ESP32** (I6): publica telemetria, eventos e `status` no broker seguindo o [contrato MQTT](../document/contrato-mqtt.md), assina `config` e imprime o limite recebido. Seis cenários: `normal`, `alerta`, `capotamento`, `sujo`, `rajada`, `queda`. |
| `build_dataset.py` | Gera `data/dataset_treino.parquet` cruzando as apólices (D1) com relevo (W2) e clima da vigência (I1). Retomável: pode ser morto e continuado. |
| `train_model.py` | Treina o modelo, compara com o **baseline por regras** na mesma tabela e salva o artefato versionado em `api/app/data/model/`. |

Os três de PSR usam o código da `api/`, então rode-os com o ambiente dela:

```bash
uv run --project api python scripts/download_psr.py
```


## Subir a demo (`run-demo.sh`)

```bash
./scripts/run-demo.sh              # API + front, Ctrl+C derruba os dois
./scripts/run-demo.sh --aquecer    # + aquece os caches: 3 fazendas e o resumo do replay (W9)
./scripts/run-demo.sh --conferir   # só confere o ambiente e sai (útil na véspera)
./scripts/run-demo.sh --sem-front  # só a API, para testar com o Wokwi ou o simulador
./scripts/run-demo.sh --porta-api 8010 --porta-front 8510
```

O que ele confere antes de subir: `uv` e `curl` instalados, `AGRISHIELD_API_KEYS` (API) e
`AGRISHIELD_API_KEY` (front) presentes **e compatíveis** — e, depois que a API sobe, testa a chave
de verdade com um `GET /api/v1/audit` autenticado. Ele lê `api/.env` e `front-web/.env` (variável
já exportada no terminal ganha do arquivo) e **repassa a chave ao front** por ambiente, que vence o
`front-web/.env` — assim a demo sobe com a mesma chave dos dois lados, mesmo que os arquivos divirjam.

Os logs de cada execução ficam num diretório temporário, cujo caminho o script imprime no início e
no fim.

## Simulador de dispositivo (`simulate_device.py`)

> ⚠️ **É ferramenta de teste, não substituto do Wokwi.** A demo continua sendo feita com o ESP32
> simulado no Wokwi (`iot/`), que é o entregável do enunciado. Este script existe para rodar os
> cenários de ponta a ponta sem depender do navegador e para **medir tempos** (W4 e W5).

```bash
# operação tranquila, uma leitura a cada 5 s
uv run --project api python scripts/simulate_device.py --scenario normal

# 100 mensagens seguidas, para medir perda
uv run --project api python scripts/simulate_device.py --scenario rajada --count 100

# payloads inválidos: a API tem de descartar todos e continuar de pé
uv run --project api python scripts/simulate_device.py --scenario sujo

# queda sem aviso (dispara o LWT) e reconexão
uv run --project api python scripts/simulate_device.py --scenario queda --down-seconds 12
```

| Cenário | O que faz |
|---|---|
| `normal` | Inclinação baixa, uma leitura a cada `--interval` segundos (padrão 5 s). |
| `alerta` | A inclinação sobe até passar do limite e sai um `tilt_alert` (3 cópias, mesmo `event_id`). |
| `capotamento` | 60° por 3 s, com `rollover` e a janela de 30 s de contexto. |
| `sujo` | 11 payloads inválidos (JSON quebrado, campo faltando, valor fora de faixa, `device_id` divergente…) e uma leitura boa no fim, a **sentinela**. |
| `rajada` | `--count` mensagens seguidas (padrão 100), para medir perda e duplicidade. |
| `queda` | Mata a conexão **sem DISCONNECT** (é o que faz o broker publicar o LWT `offline`), espera e reconecta. |

Opções úteis: `--device-id` (padrão `tractor-02`, **não** o `tractor-01` da demo), `--prefix`,
`--host`, `--port`, `--limit`, `--listen-seconds` (fica ouvindo `config` depois do cenário),
`--summary-json <arquivo>` (resumo do que saiu e do que voltou) e `--clean-retained` (apaga o
`status`/`config` retidos no broker ao terminar — **use sempre** que publicar no prefixo de
produção, porque o broker é público e a mensagem retida fica lá).

Os testes de ponta a ponta que usam este script estão em `api/tests/e2e/` e rodam com
`cd api && uv run pytest -m e2e`. Cada execução vira relatório em
[`document/evidencias/`](../document/evidencias/).

# data/

| Pasta | O quê | Vai para o Git? |
|---|---|---|
| `sample/` | Amostras pequenas (≤ 5 MB) usadas em testes e na demo | **sim** |
| `raw/` | CSVs completos baixados por `scripts/download_psr.py` (~470 MB) | **não** (no `.gitignore`) |

## `sample/psr_amostra.csv` (D1)

453 linhas **reais** do PSR/SISSER, 126 KB, geradas por `scripts/build_psr_sample.py` de forma
estratificada: **55 linhas por categoria de evento** (o rótulo raro não pode sumir da amostra),
as duas formas de coordenada (132 decimais e 320 em grau/minuto/segundo) e 12 UFs. É o que faz os
testes rodarem offline.

⚠️ **LGPD (ADR-011):** o arquivo **mantém** as colunas `NM_SEGURADO` e `NR_DOCUMENTO_SEGURADO`, com
os valores trocados por `ANONIMIZADO` e `***`. Isso é de propósito: o teste
`test_colunas_pessoais_nunca_chegam_ao_banco` só prova alguma coisa se as colunas existirem no CSV
de entrada. Nenhum valor pessoal real é versionado — há um teste que falha se aparecer.

## `dataset_treino.parquet` (D2)

O dataset de treino do modelo: uma linha por apólice, com relevo, clima da vigência, contexto e os
dois rótulos. Vai para o Git — são poucas centenas de KB. Ao lado dele, `dataset_treino.json` é a
**ficha**: data de geração, tamanho da amostra, semente, chamadas de API consumidas e taxa de cada
rótulo. Features e regra de não vazamento: [document/dados-e-modelo.md](../document/dados-e-modelo.md).

`.dataset_parcial.jsonl` é o arquivo de retomada (fora do Git): cada linha pronta é gravada nele na
hora, e a execução seguinte pula o que já foi feito.

## Como reproduzir

```bash
uv run --project api python scripts/download_psr.py       # baixa para data/raw/ (fora do Git)
uv run --project api python scripts/build_psr_sample.py   # regera data/sample/psr_amostra.csv
uv run --project api python scripts/load_psr.py           # carrega no SQLite + relatório
uv run --project api python scripts/build_dataset.py     # gera data/dataset_treino.parquet (D2)
```

Fontes, licenças e limitações: [document/dados-e-modelo.md](../document/dados-e-modelo.md).
Nenhum dado pessoal pode ser versionado aqui.

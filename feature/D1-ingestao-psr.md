# D1 — Ingestão de dados reais de sinistro (PSR/SISSER)

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api (dados) |
| Depende de | — |
| Janela | 19/09 |
| Responsável | dev-dados |
| Status | ✅ Concluída (19/09/2026) |

## Objetivo

Trazer **dados reais de seguro rural** para o projeto: as apólices subvencionadas pelo PSR (Programa de
Subvenção ao Prêmio do Seguro Rural), publicadas pelo Ministério da Agricultura como dados abertos,
com **latitude e longitude da propriedade, cultura, datas de vigência, valor indenizado e evento
preponderante** (a causa do sinistro). É a base de rótulos do modelo (D3) e a prova de que a solução
funciona com dado real, não simulado.

## História de usuário

> Como **analista da Sompo**, quero que o risco seja calibrado com **sinistros que realmente aconteceram**, para **confiar no score**.

## Escopo

**Inclui**
- Script de download dos CSVs do SISSER (2006–2015, 2016–2024, 2025) — ver [dados-e-modelo.md](../document/dados-e-modelo.md).
- Leitura correta: separador `;`, codificação **ISO-8859-1**, decimal com vírgula, datas `dd/mm/aaaa`.
- **Anonimização na ingestão** (LGPD, ver I5): descartar `NM_SEGURADO` e `NR_DOCUMENTO_SEGURADO` já na leitura. Eles **nunca** entram no banco nem no repositório.
- Limpeza: linhas sem coordenada (`-`), coordenadas fora do Brasil, duplicidades por `ID_PROPOSTA`, valores negativos, datas inconsistentes (fim antes do início).
- Normalização do `EVENTO_PREPONDERANTE` (o texto varia) em categorias: `seca`, `chuva_excessiva`, `granizo`, `geada`, `vento`, `outros`, `sem_sinistro`.
- Carga em SQLite (tabela `policy`) e ficha da fonte em `document/dados-e-modelo.md`.
- Relatório de qualidade: total lido, descartado por motivo, % com indenização, distribuição por evento e por UF.

**Não inclui**
- Dados pessoais de qualquer tipo.
- O CSV bruto no Git (só o script e uma **amostra** de até 5 MB em `data/sample/`).

## Implementação

### API (`api/`)
- `scripts/download_psr.py`: baixa os CSVs para `data/raw/` (fora do Git), com verificação de tamanho e cabeçalho.
- `app/services/psr_ingest.py`: `read_psr(path) -> DataFrame`, `clean(df) -> tuple[DataFrame, QualityReport]`, `normalize_event(text) -> str`, `load_to_db(df, session)`.
- `app/models.py`: `Policy(id, proposal_id UNIQUE, insurer, municipality, state, geocode_ibge, lat, lon, **coordinate_source**, crop, area_ha, coverage_value, premium, start_date, end_date, policy_year, indemnity_value, event_category, created_at)`, com índices em `(state, policy_year)`, `(lat, lon)` e `event_category`.
  - **`coordinate_source`** (`"decimal"` ou `"dms"`) não estava na especificação original e foi acrescentado na implementação: só 8% das apólices trazem a coordenada decimal, e as outras 92% são reconstruídas de grau/minuto/segundo, com resolução de ~30 m em vez de ~0,1 m. A **D2** precisa desse campo para saber quais pontos são menos precisos ao cruzar com o relevo; a **I3** precisa dele porque `create_all` cria a tabela com essa coluna.
- Dependência: `uv add pandas`.

## Critérios de aceite

- [x] O script baixa os 3 CSVs e o dicionário de dados, e é idempotente (não rebaixa o que já existe).
- [x] A leitura preserva acentos (teste com "Jataí" e "Milho 2ª safra") e converte decimal com vírgula.
- [x] Nenhuma coluna com nome ou documento de pessoa chega ao banco (teste automático que falha se aparecer).
- [x] O relatório de qualidade mostra as contagens de descarte por motivo. Nada de descarte silencioso.
- [x] `event_category` cobre 100% das linhas (o que não casar vai para `outros`, e os textos não reconhecidos aparecem no log).
- [x] Os testes rodam offline, com a amostra de `data/sample/psr_amostra.csv` (≈ 500 linhas).

## Ressalva de qualidade: vigência de duração zero (achada na D2, resolvida)

**100% do arquivo 2006–2015 tem `DT_INICIO_VIGENCIA` igual a `DT_FIM_VIGENCIA`** (430.843 de
430.843 linhas); nenhuma linha de 2016 em diante tem o problema.

**O que foi feito:** o `QualityReport` ganhou um terceiro tipo de contagem, `flags` — linhas
**mantidas** que carregam um defeito conhecido, ao lado de `discarded` (a linha sai) e
`corrections` (o valor é consertado). A ingestão agora reporta:

```
⚠️  mantidas com ressalva:
    vigencia_de_um_dia: 430843 (28.2% das mantidas)
      ↳ início = fim, então não há janela climática. Sem uso para features de período
        (D2/D3), que excluem estas linhas; seguem válidas para análise espacial e de evento.
```

**Por que ressalva e não descarte.** A apólice é real e íntegra: tem coordenada, cultura, valor
indenizado e evento válidos. Só as datas não servem. Descartar jogaria fora 430.843 registros —
56.885 deles com indenização paga — que continuam úteis para densidade espacial de sinistro e
distribuição por evento e por UF. A restrição é da **modelagem**, não da ingestão, e é a D2 que a
aplica (`MIN_COVERAGE_DAYS`). Data invertida (`fim < início`) continua sendo descarte: aquilo é
erro, isto é dado inútil mas correto.

**Consequência para quem usa a tabela `policy`:** a população com janela climática é **2016–2024,
1.048.419 apólices**, com taxa de sinistro de **18,2%** — não os 16,2% do total, diluídos pelo
período sem vigência.

Testes: `test_vigencia_de_um_dia_e_mantida_e_sinalizada`,
`test_ressalva_aparece_no_relatorio_com_a_consequencia`,
`test_vigencia_invertida_continua_sendo_descarte` e `test_ressalvas_somam_entre_blocos`.

## Riscos e plano B

| Risco | Plano B |
|---|---|
| Portal fora do ar na hora da demo | A amostra versionada e o banco já carregado bastam |
| Arquivo grande demais para a máquina | Ler em blocos (`chunksize`) e filtrar por UF/ano antes de concatenar |
| Bloqueio por user-agent | O script já envia um user-agent de navegador |

## Tarefas

- [x] Script de download + ficha da fonte
- [x] Leitura, limpeza e normalização + testes
- [x] Tabela `policy` + carga
- [x] Relatório de qualidade
- [x] Amostra versionada em `data/sample/`

## Resultado (19/09/2026)

**1.712.385** linhas lidas dos três CSVs · **1.525.473** carregadas na tabela `policy`
(**186.912** descartadas, todas contadas por motivo) · **247.297** com indenização (16,2%).
Relatório completo, vocabulário de eventos e limitações:
[document/dados-e-modelo.md §1](../document/dados-e-modelo.md).

Achado que mudou o pipeline: **92% das apólices só têm a coordenada em grau/minuto/segundo**.
Sem reconstruir o grau decimal a partir do DMS, a base útil cairia para 8%.

| Arquivo | O quê |
|---|---|
| `scripts/download_psr.py` | Baixa os 3 CSVs + o dicionário (user-agent de navegador, idempotente) |
| `scripts/build_psr_sample.py` | Gera `data/sample/psr_amostra.csv` (453 linhas reais, anonimizadas) |
| `scripts/load_psr.py` | Roda o pipeline e imprime o relatório de qualidade |
| `api/app/services/psr_ingest.py` | `read_psr` → `clean` → `load_to_db`, com `QualityReport` |
| `api/app/models.py`, `api/app/db.py` | Tabela `policy` e o SQLite |
| `api/tests/test_psr_ingest.py` | 38 testes offline, incluindo os de LGPD |

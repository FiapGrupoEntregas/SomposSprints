# W12 — Relatórios e tendências de risco

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | api, front-web |
| Depende de | I3, D1, W8 |
| Janela | 22/09 |
| Responsável | dev-api + dev-front |
| Status | ✅ API + página de relatórios (faltam os prints da DOC2) |

## Objetivo

Entregar o requisito de **relatórios com tendências de risco por equipamento, região ou tipo de
operação**, com leitura adequada para cada perfil de usuário (operador, gestor de frota, técnico de
manutenção e analista da seguradora).

## Escopo

**Inclui**
- **Por equipamento**: horas operando, % do tempo acima do limite, alertas por dia, tendência de 7 dias (usa a telemetria do I3).
- **Por região** (município/UF): sinistros reais do PSR (D1), perfil de terreno das fazendas (W8) e dias de risco previstos.
- **Por tipo de operação/cultura**: taxa de sinistro por cultura e por evento preponderante, a partir do PSR.
- Exportação em CSV de cada relatório.
- Página **Relatórios** no front, com filtros (período, UF, cultura, equipamento) e gráficos de série temporal.

**Não inclui**
- Agendamento de relatório por e-mail.

## Implementação

### API (`api/`)
- `app/services/reports.py`: `equipment_trend(device_id, days)`, `region_summary(state, start, end)`, `crop_summary(start, end)`. Funções puras sobre os dados já carregados.
- `GET /api/v1/reports/equipment/{device_id}?days=7`
- `GET /api/v1/reports/region?state=MG&from=&to=`
- `GET /api/v1/reports/crop?from=&to=`
- `GET /api/v1/reports/{tipo}.csv` para exportação.

### Front-web (`front-web/`)
- Nova página `views/reports.py` com 3 abas (Equipamento · Região · Cultura), KPIs, gráfico de linha e tabela, mais o botão de baixar CSV.

## Critérios de aceite

- [x] Os três relatórios abrem com dados reais: **1.525.473 apólices** do PSR e a telemetria gravada pela I3.
- [x] Período sem dados → totais zerados, listas vazias e, no CSV, a linha "sem dados no periodo selecionado". Nunca erro.
- [x] O CSV sai em UTF-8 **com BOM**, separador `;` e **vírgula decimal** — as duas últimas andam juntas, porque com vírgula decimal a vírgula não pode separar coluna.
- [x] Cada relatório traz o campo `purpose`, com para quem serve e que decisão apoia.

### Decisões de implementação

- **`from_year` / `to_year` em vez de datas.** A grão do PSR é a safra (`policy_year`), e o índice
  da D1 é `(state, policy_year)`. Filtrar por data exigiria varrer `start_date`, que não tem
  índice. *(Fixado na W12.)*
- **As rotas `.csv` são declaradas antes das de JSON.** O FastAPI casa na ordem de declaração, e
  `/equipment/{device_id}` capturava `tractor-01.csv` inteiro no `device_id` — o download
  devolvia 404. Achado por teste.
- **O perfil de terreno no relatório de região é best-effort.** O valor está nos sinistros reais,
  que saem do banco local; se a Open-Meteo estiver fora, o perfil some e o relatório continua de
  pé, em vez de um 503 esconder 1,5 milhão de linhas por causa de uma API externa.
- **Horas operando são estimadas** pela contagem de leituras × 5 s (E4). Sem registro de
  liga/desliga no firmware, é o melhor que dá para afirmar — e o relatório não finge precisão.

## Tarefas

- [x] Services + testes (PSR em miniatura em memória, com as colunas e a categoria da D1)
- [x] Endpoints + exportação CSV
- [x] Página no front
- [ ] Prints para as evidências (DOC2)

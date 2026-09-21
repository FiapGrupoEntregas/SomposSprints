# 🧾 Backlog pós-entrega

Sugestões que o **revisor** levantou fora do escopo da feature em revisão. Pela regra do
[fluxo-agentes.md](fluxo-agentes.md), elas **não entram na feature atual**: viram item aqui e, se houver
tempo antes do congelamento (25/09), viram issue.

Nada nesta lista bloqueia a entrega. É também material de roadmap para a banca: mostra o que o time
enxergou e escolheu **não** fazer agora, com o motivo.

| # | Item | Onde | Por que ficou de fora | Custo estimado |
|---|---|---|---|---|
| 1 | Índice `(crop, event_category)` em `Policy` | `api/app/models.py` | O relatório por cultura faz varredura completa de 1,5 M linhas em ~1,0 s (os outros dois usam índice de cobertura: 8 ms e 89 ms). É aceitável na demo; o índice é aditivo e resolveria, mas mexer no esquema depois da D1 aprovada custa mais do que o 1 s incomoda | baixo |
| 2 | Varredura completa das 31 constantes importadas pelos testes | `api/tests/` | O revisor mutou as 5 mais críticas e as âncoras foram pedidas na W13. Faltam as de `test_dataset.py`, `test_open_meteo_client.py` e `test_audit.py` — mesmo padrão, risco menor porque não são valores de documento | baixo |
| 3 | Faixas do `terrain_score` em função do `L_ref` | `document/regras-de-risco.md` §8 | Hoje 8° e 15° são fixas. Com `L_ref ≠ 15°` o score muda de sentido sem mudar de fórmula. A ressalva já está documentada; mudar a fórmula é decisão de **calibração**, junto com os pesos, e depende da base de sinistros da Sompo | alto (depende de dado externo) |
| 4 | Calibrar os pesos do `terrain_score` | `api/app/services/underwriting.py` | Os pesos da v1 são arbitrários e a API já declara isso em `weights_version` / `calibration_note`. Calibrar exige a base de sinistros da Sompo com o relevo pareado — é o primeiro item do roadmap de ML | alto (depende de dado externo) |
| 6 | Varredura das 26 constantes restantes | `api/tests/` | O revisor mutou 5 de 31 e as âncoras entraram na W13. As de `test_open_meteo_client.py` e `test_audit.py` são as próximas candidatas — mesmo padrão, risco menor porque não são valores de `regras-de-risco.md` | baixo |
| 5 | Rota que liste as UFs com dados | `api/` + `front-web/views/reports.py:33` | O seletor do relatório por região traz as 27 UFs fixas no front, então oferece recortes vazios. Resolver exige rota nova na API a três dias do congelamento; oferecer uma UF sem dados mostra "sem dados", não quebra | médio |
| 7 | Revisar testes cujo docstring promete mais que as asserções verificam | `api/tests/`, `front-web/tests/` | Apareceu quatro vezes em 20/09 (guarda de LGPD sem o CSV, link de CSV sem os filtros, `proposal_id` em fixture que nunca o continha, motor do replay comparado só a conjuntos de valores). Os quatro foram corrigidos; a varredura sistemática não cabe antes do congelamento | médio |

## Como usar esta lista

- Antes de abrir uma issue, confira se o item ainda existe: alguns caem sozinhos quando uma feature vizinha muda.
- Ao promover um item a issue, registre o número dela na linha e não apague a linha — o histórico da decisão vale para o pitch.

# front-web/ — Front-end Streamlit

Interface web do AgriShield. **Só exibe dados**: toda regra de negócio fica na `api/`.

## Estrutura

```
front-web/
├── app.py                 # entrada: registra as páginas (st.navigation)
├── config.py              # AGRISHIELD_API_URL
├── views/                 # uma página por arquivo
│   ├── home.py            # início + status da API + seletor de fazenda
│   ├── risk_map.py        # W1 · W2 (aba Relevo) · W3 (aba Previsão de risco)
│   ├── equipment.py       # W4 (limite) · W5 (ao vivo) · W11 (passaporte, prévia)
│   ├── underwriting.py    # W8 (perfil do terreno + carteira)
│   ├── reports.py         # W12 (equipamento · região · cultura, com CSV)
│   └── replay.py          # W9 (replay de acidentes reais)
├── components/            # pedaços de tela reutilizados
│   ├── farm_picker.py     # seletor de fazenda (barra lateral, W1)
│   ├── device_picker.py   # seletor de equipamento (W5 e W12)
│   ├── cell_map.py        # mapa pydeck das células (abas Relevo e Risco)
│   ├── formatting.py      # rótulos, cores dos níveis, datas e horários
│   └── state.py           # chaves de sessão compartilhadas (dia e cenário)
├── services/api_client.py # única porta de acesso à API
├── tests/                 # pytest (cliente + smoke test das páginas)
├── .streamlit/config.toml # tema
├── pyproject.toml / uv.lock
└── requirements*.txt      # gerados do uv.lock (Streamlit Community Cloud usa requirements.txt)
```

## Rodando

A API precisa estar no ar (ver [api/README.md](../api/README.md)).

### Com uv (recomendado)

```bash
cd front-web
uv sync
uv run streamlit run app.py          # http://localhost:8501
uv run pytest
```

### Com pip

```bash
cd front-web
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
streamlit run app.py
pytest
```

Para apontar para outra API: `export AGRISHIELD_API_URL=https://minha-api.onrender.com`.

## O que já está na tela

| Página | Estado |
|---|---|
| **Início** | Status da API e seletor de fazenda. |
| **Mapa de risco** | Seletor de fazenda (W1) e aba **Relevo** (W2): mapa `pydeck` da grade 10 × 10 colorido por **inclinação** (escala sequencial) ou por **classe de terreno**, tooltip com elevação, inclinação, orientação e classe, KPIs de amplitude, inclinação máxima e % da área com 15° ou mais, e legenda. A escala de cor da inclinação é relativa à própria fazenda (com piso de 5° para o terreno quase plano não virar ruído); a legenda sempre mostra a **inclinação máxima real** vinda da API. |
| **Mapa de risco → Previsão de risco** | W3: faixa dos 7 dias (pior nível, chuva e estado do solo) com clique para escolher o dia, mapa das células coloridas por nível (🟢 `#2E7D32` · 🟡 `#F9A825` · 🔴 `#C62828`) com os motivos no tooltip, gráfico de chuva do dia e em 72 h com as linhas de 10 e 30 mm, painel com o limite do dia, a % da área por nível e os principais motivos, e o toggle **"Cenário: chuva forte (simulado)"**. Com um cenário aplicado a tela mostra a faixa "⚠️ Cenário simulado — não é a previsão real". W7: filtro **Perigos** (`st.multiselect`, padrão todos) com os ícones 🚜 capotamento · 🟫 atolamento · ⚡ raio · 💨 vento · 🔥 incêndio, e a linha de clima do dia (temperatura, UR mínima, vento, rajada, CAPE e tempestade) que mostra de onde vieram o raio e o incêndio. W6: card **O que fazer**, com abas Hoje e Amanhã, janelas seguras como chips (`07h–12h`) e as orientações em lista. W13: cartão **Probabilidade de sinistro (modelo)** no pé do painel do dia — menor que o cabeçalho do nível por regras de propósito, e com "menor que 0,01%" no lugar de um "0,00%" que pareceria defeito, com a versão do artefato, a comparação AUC-PR modelo × baseline, a ressalva que a **API** monta a partir do artefato e os fatores rotulados como importância global ("o que mais pesa no modelo, em geral"). Sem modelo, ou com a probabilidade nula, o cartão simplesmente não aparece. |
| **Equipamento ao vivo** | W4: card **Limite de hoje** com o número grande, a comparação com o limite em solo seco, o motivo, o seletor de dia, o toggle de cenário e o botão **"Enviar ao equipamento"** (com "Último envio: HH:MM — X°"). W5: bloco **ao vivo** em `@st.fragment(run_every="2s")` com faixa de status, banner de nível (SEGURO / ATENÇÃO / PERIGO / 🚨 CAPOTAMENTO), métricas de roll, pitch, limite e clima, gráfico de 10 min com a linha do limite, lista de eventos e, num capotamento de menos de 5 min, alerta fixo no topo com o gráfico dos 30 s de contexto. W11: seção **Passaporte (prévia)** abaixo do ao vivo, com o acumulado do período (horas operando, inclinação máxima, % do tempo acima do limite, leituras, alertas, capotamentos, ocorrências e limites aplicados), a linha do tempo usando a **mesma** linha de evento do painel ao vivo, e o `roadmap_note` da API dizendo o que o histórico ainda **não** é. |
| **Subscrição** | W8: selo grande da classe (A/B/C) com o score, indicadores do relevo, gráfico da distribuição de inclinação, "o que mais pesa" (drivers com os pontos) e a tabela da **carteira** ordenada pelo score. A nota de calibração (`weights_version` + `calibration_note`) aparece em destaque, logo abaixo do selo — os pesos são v1 e ainda não calibrados. |
| **Relatórios** | W12: abas **Equipamento** (horas operando, % acima do limite, alertas e tendência diária), **Região** (sinistros reais do PSR por UF e município, causas e as fazendas de demonstração da UF) e **Cultura** (taxa de sinistro por cultura e causa). Cada aba mostra o `purpose` que a API manda e tem o botão de **baixar CSV**, que aponta para a rota `.csv` da própria API. |
| **Replay de acidentes** | W9: **placar** dos casos (avaliados · alertariam no ponto · alertariam na grade) com a ressalva da API colada ao número, lista caso a caso com link da notícia, e, para o caso escolhido: resumo curado, veredito grande com as **duas** leituras — no ponto e na grade de vizinhança, que significam coisas diferentes —, a faixa "O que este replay não prova" com o `limitations` inteiro (nunca em tooltip ou expander), a precisão da coordenada sempre à vista, o **mapa das células da vizinhança** (polígonos de `terrain`, cores dos níveis de `day.cells`, join por `(row, col)`) com 📍 no ponto do acidente, e o clima do dia. Tem também a aba de **ponto e data manuais**. |

O filtro de perigos é **exibição**: a cor de cada célula passa a ser o pior nível **entre os
perigos marcados**, a legenda do mapa encerra com "Mostrando só: …" quando há filtro ativo (para a
captura de tela se explicar sozinha) e a tela avisa que as porcentagens e o nível do dia continuam
vindo da API, com todos os perigos. O aviso da W6 — "as áreas em vermelho seguem proibidas mesmo dentro das
janelas" — sai da lista e aparece como faixa em destaque.

No replay, o placar e a contagem por caso vêm prontos de `GET /replay/summary` — o front não soma
vereditos — e a ressalva que acompanha o número é exibida junto dele, nunca escondida. Um
"não teria alertado" é apresentado como informação, não como falha:
incêndio por falha mecânica e capotamento em terreno plano estão fora do que o motor promete
detectar, e a tela diz isso com todas as letras.

A fazenda (`farm_id`), o **dia** (`selected_day_date`) e o **cenário** (`risk_scenario_heavy_rain`)
ficam no `st.session_state`, em chaves próprias (`components/state.py`) — **nunca** na chave de um
widget, porque o Streamlit limpa o estado com escopo de página na própria troca de página (mesmo
que a página seguinte desenhe o mesmo widget) — e **valem para todas as páginas**: escolher o dia chuvoso e o cenário no mapa de risco e ir ao
equipamento envia o limite daquele dia, com o aviso de cenário simulado junto.

## Como as páginas falam com a API

- Todo acesso passa por `services/api_client.py`: `list_farms()` e `get_farm(id)` (cache de 10 min),
  `get_terrain(id)` (cache de 24 h), `get_risk(id, days, scenario)` e `get_device_limit(...)`
  (cache de 1 h) e as leituras do painel ao vivo — `get_device_status`, `get_latest_telemetry`,
  `get_telemetry_series` e `get_device_events` — com cache de **2 s**, o ritmo do fragmento.
  `get_recommendations(id, days, scenario)` também tem cache de 1 h, `get_underwriting(id)` de 24 h,
  `get_region_report(...)`/`get_crop_report(...)` de 24 h (são 1,5 milhão de apólices por consulta)
  e `get_equipment_report(...)` de 1 min, porque a telemetria continua chegando.
  `report_csv_url(...)` **não** é requisição: só monta o link de download da rota `.csv` da API —
  o front nunca gera CSV.
  `publish_device_limit(...)` é escrita e **nunca** entra em cache.

### Chave de API nas escritas (I5)

A API exige `X-API-Key` nas rotas de escrita — hoje só `POST /devices/{id}/limit/publish`, que é o
botão "Enviar ao equipamento". O front lê a chave de **`AGRISHIELD_API_KEY`** (singular; a API tem a
lista `AGRISHIELD_API_KEYS`) e a envia **apenas** nessa requisição: leitura nunca leva segredo.
Sem a variável, ou com uma chave que a API recusa, a tela diz exatamente o que configurar em vez de
mostrar um erro genérico.

Copie `.env.example` para **`front-web/.env`** e preencha: o `config.py` carrega esse arquivo com
`python-dotenv`, como a API já faz com `pydantic-settings`. O `.env` lido é sempre o que fica ao
lado do `config.py`, então `streamlit run app.py` dentro de `front-web/` e
`streamlit run front-web/app.py` a partir da raiz se comportam igual. Variável já exportada no
ambiente **vence** o `.env`.

`config.py` carrega `front-web/.env` no import (`python-dotenv`), o arquivo **ao lado dele** — então
`streamlit run app.py` de dentro de `front-web/` e `streamlit run front-web/app.py` da raiz enxergam a
mesma configuração. Variável já exportada no ambiente **vence** o arquivo, que é do que o
`scripts/run-demo.sh` depende para repassar a chave aos dois processos.
- Falhas viram `ApiError` (ou `NotFoundError`, no 404) com mensagem em português; a página mostra
  `st.error` com o que fazer — **nunca** um traceback.
- **Zero regra de negócio aqui**: inclinação, classes de terreno e estatísticas vêm de
  `GET /api/v1/farms/{id}/terrain`; nível de cada célula, estado do solo, limite do dia, % por nível
  e os motivos já escritos em português vêm de `GET /api/v1/farms/{id}/risk`. As cores, os rótulos e
  as linhas de referência do gráfico (10 e 30 mm) são só apresentação. No painel do equipamento, o
  limite, o nível do alerta e o estado online/offline também vêm prontos das rotas `/devices/...`.
- Sem telemetria ainda **não é erro**: a tela mostra "Aguardando o equipamento conectar…". Quando o
  envio do limite falha por MQTT fora do ar, a mensagem diz que o limite **não foi enviado** e que
  nada se perdeu — nunca "falhou".

## Testes

`uv run pytest` cobre o cliente da API (com `httpx.MockTransport`) e cada página (com `AppTest`),
sempre **sem rede**: os dublês entram por `api_online()` / `api_offline()` em `tests/test_pages.py`.

Dois hábitos que os testes daqui seguem, porque foram os buracos encontrados em revisão:

- **Pareamento rótulo ↔ valor:** afirme `("Rolagem (roll)", "12,4°") in pares`, não só que "12,4°"
  aparece em algum lugar — senão trocar dois números na tela não quebra nada.
- **Proveniência:** quando o texto vem pronto da API (`purpose`, `note`, `calibration_note`,
  `limitations`), varie a fixture e afirme também a **ausência** do valor antigo. É o que
  distingue "mostra o que a API mandou" de "tem uma cópia congelada no código".

`test_control_mutation_the_suite_really_sees_the_page_change` é uma **mutação de controle**: ela
apaga de propósito a chamada que desenha o cartão do modelo numa cópia da view e exige que o
cartão suma. Se esse teste falhar durante uma varredura de mutações, o arnês está rodando código
antigo (o clássico: copiar o projeto com `shutil.copytree`, que leva junto o `__pycache__`) e
nenhuma conclusão daquela rodada vale.

## Dependências

Igual à API: `uv add pacote` e depois `../scripts/sync-requirements.sh`. Nunca edite `requirements*.txt` à mão.

`altair`, `pandas` e `pydeck` vêm junto do Streamlit, mas o código os importa direto (gráficos,
tabelas e mapas), então estão **declarados explicitamente** no `pyproject.toml` — um clone limpo
não pode depender do que o Streamlit escolhe trazer. O piso do Streamlit é 1.63, versão em que
existem `st.fragment`, `st.toast`, `st.column_config`, `st.link_button` e `width="stretch"`.

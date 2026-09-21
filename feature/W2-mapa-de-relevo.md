# W2 — Mapa de relevo (inclinação e classes de terreno)

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api, front-web |
| Depende de | I1, W1 |
| Janela | 16/09 |
| Responsável | Dev |
| Status | ✅ API + aba Relevo no front |

## Objetivo

Transformar a elevação da fazenda num **mapa de inclinação** e em **classes de terreno** (baixada,
encosta, topo exposto, plano). É o diferencial que a Sompo apontou: ninguém está olhando o terreno.

## História de usuário

> Como **produtor ou analista**, quero **ver onde a fazenda é íngreme, onde acumula água e onde é exposta**, para **entender o risco físico de cada área**.

## Escopo

**Inclui**
- Grade 10×10, com inclinação, orientação (aspect) e classe por célula: [regras-de-risco §1](../document/regras-de-risco.md#1-relevo-w2).
- Estatísticas: elevação mín/máx, amplitude, inclinação máx/média, % de células por faixa.
- Polígono de cada célula já calculado pela API (o front só desenha).
- Cache de 24 h por fazenda.

**Não inclui**
- DEM de maior resolução (30 m ou 10 m): roadmap.
- Rede de drenagem ou acúmulo de fluxo.

## Regras e lógica

Seguir exatamente [regras-de-risco §1](../document/regras-de-risco.md#1-relevo-w2). Pontos de atenção:
- As linhas vão de **norte → sul**, então `dz_dnorte = -gradient_eixo0`.
- `dx` depende de `cos(lat)`.
- Fazenda com amplitude < 5 m → tudo `flat`.

## Implementação

### API (`api/`)
- Dependência: `uv add numpy`.
- `app/services/terrain.py`:
  - `build_grid(bbox, n=10) -> tuple[list[float], list[float]]` (lats de N→S, lons de O→L)
  - `cell_size_m(bbox, n) -> tuple[float, float]`
  - `compute_slope_aspect(elev: np.ndarray, dx_m, dy_m) -> tuple[np.ndarray, np.ndarray]`
  - `classify_cells(elev: np.ndarray) -> np.ndarray[str]`
  - `aspect_label(deg) -> str` (N, NE, L, SE, S, SO, O, NO)
  - `get_terrain(farm) -> Terrain` (usa o I1 e fica em cache)
- `app/schemas/terrain.py`: `TerrainCell(row, col, lat, lon, polygon: list[[lon, lat]], elevation_m, slope_deg, aspect_deg, aspect_label, terrain_class)` e `TerrainResponse(farm_id, grid_size, cell_size_m, stats, cells)`.
- `GET /api/v1/farms/{farm_id}/terrain`
- **Herdado do I1:** registrar em `app/main.py` o tratador global que converte
  `WeatherUnavailableError` (de `app/clients/open_meteo.py`) em
  **503 "Serviço de clima indisponível"** — `app.add_exception_handler(WeatherUnavailableError, …)`.
  É a primeira feature com rota que usa a Open-Meteo, então a pendência vence aqui.

### Front-web (`front-web/`)
- Página **Mapa de risco**, aba **Relevo**:
  - `pydeck` (já vem com o Streamlit) com `PolygonLayer` das células, colorido por inclinação (escala sequencial) ou por classe de terreno (alternados por um `st.radio`).
  - Tooltip com a elevação, a inclinação, a orientação e a classe.
  - KPIs (`st.metric`): amplitude (m), inclinação máxima (°) e % da área acima de 15°.
  - Legenda das cores.

## Critérios de aceite

- [x] A fazenda de Carmo de Minas mostra relevo variado (inclinação máx ≥ 8°). A fazenda plana tem quase tudo `flat`.
- [x] Teste sintético: um plano que sobe 10 m a cada 100 m para leste → `slope ≈ 5,71°` e `aspect ≈ 270° (O)`.
- [x] Teste sintético: um plano que sobe para o **norte** → `aspect ≈ 180° (S)` (valida a inversão de sinal).
- [x] Um vale sintético → células do fundo `lowland` e das bordas altas `exposed`.
- [x] Resposta em < 3 s sem cache e < 200 ms com cache.
- [x] Com a Open-Meteo fora do ar e sem cache, a rota responde **503 "Serviço de clima indisponível"** (tratador global de `WeatherUnavailableError`, pendência do I1).

## Testes

- `tests/test_terrain.py`: os três sintéticos acima, `aspect_label` nas fronteiras (22,5°, 67,5°…) e a regra do `flat`.
- `tests/test_terrain_route.py`: rota com a Open-Meteo mockada (sucesso, 404 de fazenda inexistente e 503 quando o cliente levanta `WeatherUnavailableError`).

## Riscos e plano B

| Risco | Plano B |
|---|---|
| DEM de 90 m "achata" as encostas | Mostrar como limitação no pitch. Escolher uma fazenda com relevo marcante |
| O mapa pydeck dá trabalho | Plano B: `st.map` com pontos coloridos por nível (menos bonito, funciona) |

## Tarefas

- [x] `uv add numpy` + sincronizar os requirements
- [x] Funções puras + testes sintéticos
- [x] `get_terrain` com cache + rota
- [x] `exception_handler` global de `WeatherUnavailableError` → 503 (pendência do I1)
- [x] Aba Relevo no front
- [x] Atualizar `document/arquitetura.md` (✅ no endpoint) e os READMEs

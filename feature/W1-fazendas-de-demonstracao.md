# W1 — Fazendas de demonstração

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api, front-web |
| Depende de | — |
| Janela | 15/09 |
| Responsável | Dev (dados validados pelo time, T2) |
| Status | ⬜ A fazer |

## Objetivo

Ter fazendas reais pré-cadastradas, com relevos contrastantes, para a demo não depender de desenhar
áreas no mapa. É o ponto de partida de todas as telas.

## História de usuário

> Como **analista da Sompo**, quero **escolher uma fazenda de exemplo**, para **ver o risco dela sem precisar cadastrar nada**.

## Escopo

**Inclui**
- 3 fazendas num arquivo JSON versionado:
  1. **Café em Carmo de Minas (MG)**: morros, colheita mecanizada. Centro `-22.12, -45.13`, bbox ±0,005° (elevação verificada entre 900 e 1000 m) → **fazenda principal da demo**.
  2. **Grãos numa área plana (ex.: Sorriso, MT)**: contraste. Deve sair quase toda 🟢 e classe `flat`.
  3. **Uva ou café em outra encosta** (ex.: Serra Gaúcha, RS), para mostrar um segundo relevo.
- Cada fazenda tem pelo menos 1 equipamento (`tractor-01` na fazenda de café).
- Seletor de fazenda no front, compartilhado entre as páginas.

**Não inclui**
- Cadastro, edição ou desenho de áreas (roadmap).

## Implementação

### API (`api/`)
- `app/data/farms.json`:
  ```json
  [
    {
      "id": "cafe-carmo-de-minas",
      "name": "Sítio Café da Serra (exemplo)",
      "municipality": "Carmo de Minas", "state": "MG", "crop": "café",
      "center": { "lat": -22.12, "lon": -45.13 },
      "bbox": { "north": -22.115, "south": -22.125, "west": -45.135, "east": -45.125 },
      "reference_tilt_limit_deg": 15.0,
      "devices": [
        { "device_id": "tractor-01", "name": "Trator cafeeiro 01", "type": "tractor", "base_tilt_limit_deg": 15.0 }
      ]
    }
  ]
  ```
- `app/schemas/farm.py`: `BBox`, `LatLon`, `Device`, `Farm`, `FarmSummary`.
- `app/services/farms.py`: `load_farms()` (lido **uma vez** e validado com Pydantic; JSON inválido → erro claro ao subir), `get_farm(farm_id)` e `find_device(device_id)`.
- `app/api/v1/routes/farms.py`:
  - `GET /api/v1/farms` → `list[FarmSummary]` (id, name, municipality, state, crop)
  - `GET /api/v1/farms/{farm_id}` → `Farm`, ou **404 "Fazenda não encontrada"**

### Front-web (`front-web/`)
- `services/api_client.py`: `list_farms()` e `get_farm(id)` (cache de 10 min).
- `components/farm_picker.py`: `farm_picker()`, um `st.sidebar.selectbox` que guarda em `st.session_state["farm_id"]` e devolve a fazenda.
- Na página **Mapa de risco**: um mini-mapa (`st.map`) com o centro da fazenda e os dados básicos.

## Critérios de aceite

- [ ] `GET /farms` lista 3 fazendas. `GET /farms/xyz` → 404 com a mensagem em português.
- [ ] `farms.json` com um campo faltando → a API **não sobe** e mostra qual campo está errado.
- [ ] Trocar de página no front mantém a fazenda selecionada.
- [ ] O time (T2) conferiu no Google Maps (satélite) que as bboxes caem em áreas agrícolas reais.

## Testes

- `tests/test_farms.py`: listagem, detalhe, 404, validação do JSON de exemplo.
- Front: smoke test da página continua passando.

## Tarefas

- [ ] Coordenadas das 3 fazendas (com o T2)
- [ ] `farms.json` + schemas + service
- [ ] Rotas + testes
- [ ] Seletor no front
- [ ] Marcar os endpoints como ✅ em `docs/arquitetura.md` e `api/README.md`

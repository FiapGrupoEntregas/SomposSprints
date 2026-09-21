# I3 — Persistência em SQLite

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api |
| Depende de | I2 |
| Janela | 20/09 |
| Responsável | Dev |
| Status | ✅ Pronto |

## Objetivo

Guardar a telemetria, os eventos, o status e os limites publicados, para que o painel (W5), o
histórico (W11) e a demo sobrevivam a um reinício da API.

## Escopo

**Inclui**
- SQLite via **SQLModel** (do mesmo autor do FastAPI).
- Tabelas: `telemetry`, `device_event`, `device_status` e `published_config`.
- Retenção: apagar a telemetria com mais de 7 dias quando a API sobe.

**Não inclui**
- Migrações (Alembic): as tabelas são criadas com `create_all`. Se o esquema mudar, apague o arquivo `.db` local.
- Banco em nuvem.

## Implementação

### API (`api/`)
- Dependência: `uv add sqlmodel`.
- Setting: `database_url: str = "sqlite:///./agrishield.db"` (o `*.db` já está no `.gitignore`).
- `app/db.py`: engine, `create_db()` (chamado no lifespan) e a dependência `get_session()`.
- `app/models.py`:
  - `Telemetry(id, device_id, ts, received_at, seq, roll_deg, pitch_deg, accel_g, temp_c, humidity_pct, tilt_limit_deg, alert_level)`, com índice em `(device_id, received_at)`.
  - `DeviceEvent(id, event_id UNIQUE, device_id, type, ts, received_at, payload_json)`
  - `DeviceStatus(device_id PK, state, updated_at)`
  - `PublishedConfig(id, device_id, published_at, payload_json)`
- `app/repositories/devices.py`: `save_telemetry`, `save_event` (ignora `event_id` duplicado), `set_status`, `latest_telemetry`, `telemetry_since`, `list_events` e `save_published_config`.
- Os callbacks da ponte (I2) chamam o repositório. A thread do paho abre a **própria sessão** em cada mensagem.

## Critérios de aceite

- [x] Com a API reiniciada, os dados continuam lá.
- [x] `event_id` duplicado não cria uma linha nova (constraint UNIQUE + tratamento da exceção).
- [x] `telemetry_since(device, 10 min)` devolve em ordem cronológica.
- [x] Os testes usam `sqlite://` em memória (`StaticPool`) e não deixam arquivo para trás.

## Testes

- `tests/test_repositories.py`: salvar e ler cada tabela, deduplicação, retenção.

## Riscos e plano B

| Risco | Plano B |
|---|---|
| Acesso concorrente (thread do MQTT + requisições) | `check_same_thread=False` e uma sessão por operação |
| Deploy gratuito apaga o disco | Aceito para a demo (ADR-009). A demo roda local |

## Tarefas

- [x] `uv add sqlmodel` + sincronizar os requirements (já tinha vindo com a D1)
- [x] Modelos e `create_db` no lifespan
- [x] Repositório + testes
- [x] Ligar os callbacks do I2
- [x] Atualizar `.env.example` e os READMEs

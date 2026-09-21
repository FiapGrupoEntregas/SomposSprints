# CLAUDE.md

Instruções para o Claude Code neste repositório. **Responda sempre em português (pt-BR).**

## ⚠️ Regra obrigatória: manter o README.md atualizado

Sempre que uma tarefa alterar o projeto, **atualize o `README.md` da raiz na mesma tarefa**, antes de
dizer que terminou. Conta como alteração:

| Mudou… | Atualize no README.md |
|---|---|
| Pastas ou arquivos relevantes (criados, movidos, renomeados, removidos) | "📁 Estrutura de pastas" |
| Como instalar, rodar ou testar (comandos, dependências, variáveis de ambiente, portas) | "🔧 Como executar" |
| Feature implementada, removida ou com status novo | "🧭 Funcionalidades" (e `feature/README.md`) |
| Nova versão, tag ou marco | "🗃 Histórico de lançamentos" |
| Arquitetura, contrato MQTT ou endpoints | resumo no README + o documento em `document/` |

- Atualize também o README do subprojeto afetado: `api/README.md`, `front-web/README.md` ou `iot/README.md`.
- Se concluir que o README **não** precisa mudar, diga isso explicitamente na resposta final, com o motivo.

## Contexto

Projeto acadêmico (FIAP × Sompo) com entrega em **27/09/2026** e código congelado em **25/09/2026**.
O produto previne acidentes com máquinas agrícolas **cruzando relevo × clima**: mapa de risco por
talhão e por dia, e **limite de inclinação dinâmico** enviado a um ESP32 (simulado no Wokwi) na máquina.
Só uma pessoa do time programa, então prefira soluções simples, com poucas partes móveis.

## Mapa do repositório

| Pasta | O quê |
|---|---|
| `api/` | FastAPI: motor de risco, clientes Open-Meteo, ponte MQTT, SQLite |
| `front-web/` | Streamlit: só exibe dados vindos da API |
| `iot/` | Firmware ESP32 (PlatformIO + Wokwi), arquivo único `src/main.ino` |
| `feature/` | Especificação de cada feature (I, W, E, T), com critérios de aceite |
| `document/` | Práticas e decisões: arquitetura, regras de risco, contrato MQTT, fluxo git, padrões, DoD, fluxo de agentes |
| `scripts/` | `sync-requirements.sh`, `create-labels.sh` |
| `data/` | Dados: `sample/` versionado, `raw/` fora do Git (ver `document/dados-e-modelo.md`) |
| `.claude/` | Agentes (`agents/`) e skills (`skills/`) do Claude Code usados no projeto |

## Comandos

```bash
# API
cd api && uv sync && uv run fastapi dev app/main.py
cd api && uv run ruff check . && uv run ruff format --check . && uv run pytest

# Front-web
cd front-web && uv sync && uv run streamlit run app.py
cd front-web && uv run ruff check . && uv run ruff format --check . && uv run pytest

# IoT
cd iot && pio run

# Depois de mudar dependências (uv add/remove)
./scripts/sync-requirements.sh
```

## Regras de implementação

- **Antes de implementar uma feature, leia `feature/<ID>-*.md`.** Atenda aos critérios de aceite e atualize o status em `feature/README.md`.
- **Regra de negócio só na `api/`** (em `app/services/`, com funções puras e testadas). O front só exibe e o firmware só aplica o limite recebido.
- **Limiares e fórmulas vêm de `document/regras-de-risco.md`.** Não invente valores. Se precisar mudar, atualize o documento no mesmo trabalho.
- **Contrato MQTT** (`document/contrato-mqtt.md`): mudou lá, muda o firmware e a API juntos.
- **Dados e modelo:** fontes, pipeline, features e métricas vêm de `document/dados-e-modelo.md`. **Dado real primeiro**; dado simulado precisa estar rotulado como tal no código, na API e na tela.
- **LGPD:** nenhum dado pessoal entra no banco ou no repositório (o CSV do PSR traz nome e documento: descarte na leitura).
- **Dependências Python:** `uv add` e depois `scripts/sync-requirements.sh`. Nunca edite `requirements*.txt` à mão. uv e pip precisam continuar funcionando.
- **Firmware:** um único `.ino` compatível com o Wokwi web, sem `delay()` no `loop()`. Pinos iguais no `diagram.json`, no `main.ino` e no `iot/README.md`. Bibliotecas no `platformio.ini` **e** no `libraries.txt`.
- **Idioma:** identificadores em inglês. Comentários, docs, textos de UI e commits em português.
- Os testes não acessam a internet: mocke a Open-Meteo e o MQTT.
- Commits no padrão Conventional Commits (`feat(api): …`). **Não faça commit nem push sem o usuário pedir.**

## Agentes e dinâmica de geração de código

Para implementar features, use a dinâmica descrita em `document/fluxo-agentes.md`:

| Agente | Área |
|---|---|
| `dev-api` | `api/` |
| `dev-front` | `front-web/` |
| `dev-iot` | `iot/` |
| `dev-dados` | dados e modelo preditivo (pipelines, banco, treino, métricas) |
| `qa-integracao` | testes ponta a ponta, simulador de dispositivo e evidências |
| `doc-entrega` | documentação, README final e conformidade com o enunciado |
| `revisor` | avalia qualquer área e pede alterações (não edita) |

- Para uma feature inteira, use a skill `/implementar-feature <ID>`. Você é o **orquestrador**: delega aos devs, manda cada entrega para o `revisor` e repete no máximo 3 rodadas por camada.
- Nas correções, **continue o mesmo agente dev** (SendMessage), para que ele mantenha o contexto.
- Pedidos "fora do escopo" do revisor viram sugestão de issue, não entram na feature atual.

## Checklist final de toda tarefa

- [ ] `README.md` (e o README do subprojeto) atualizado, ou uma justificativa explícita
- [ ] Status da feature em `feature/README.md`
- [ ] `document/` atualizado se mudou regra, contrato ou endpoint
- [ ] `ruff check`, `ruff format --check` e `pytest` (api, front-web) / `pio run` (iot) passando
- [ ] `scripts/sync-requirements.sh` rodado se as dependências mudaram

# I5 — Segurança, controle de acesso e rastreabilidade

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Camadas | api |
| Depende de | I3 |
| Janela | 21/09 |
| Responsável | dev-api |
| Status | ✅ Pronto |

## Objetivo

Atender ao requisito de **segurança e rastreabilidade** do enunciado: proteger os dados, controlar
quem escreve, e registrar entradas, saídas e decisões de forma auditável. Sem isso, o score não é
defensável perante a seguradora.

## Escopo

**Inclui**
- **Controle de acesso**: chave de API (cabeçalho `X-API-Key`) obrigatória nos endpoints de escrita e de publicação de limite. Leitura permanece aberta na demo (documentado como decisão).
- **Proteção de dados**: dados pessoais do PSR descartados na ingestão (D1); nada de segredo no repositório; `.env` fora do Git; validação estrita de toda entrada (Pydantic) e tamanho máximo de payload (64 KB → 413).
- **Log estruturado** (JSON) com `request_id`, rota, status e duração. O `request_id` volta no cabeçalho da resposta.
- **Trilha de auditoria** em banco: tabela `decision_log` com o que entrou, o que saiu e qual versão de regra/modelo decidiu.
- **Integridade**: hash do payload dos eventos MQTT gravado junto, para detectar alteração.
- Endpoint `GET /api/v1/audit?entity=&limit=` (protegido por chave) para consultar a trilha.

**Não inclui**
- Login de usuário, perfis e OAuth: fora do escopo de um MVP de 2 semanas (registrar como decisão).
- HTTPS: responsabilidade do ambiente de deploy.

## Proteção de dados: o que guardamos e qual é o resíduo

Não é correto afirmar "nenhum dado pessoal versionado" sem qualificar, e esta feature é o lugar de
registrar isso. O levantamento veio da revisão da D1.

**O que fica fora**, por lista branca de colunas na leitura (`read_psr`): `NM_SEGURADO` e
`NR_DOCUMENTO_SEGURADO` não chegam nem a virar `DataFrame`, e um teste falha se um nome ou
documento aparecer em qualquer campo da tabela `policy`.

**O resíduo:** o banco e a amostra versionada guardam o `proposal_id` (`ID_PROPOSTA`), que é
**chave de junção de volta ao CSV público do PSR — e esse CSV traz o nome do segurado**.

- **Por que guardamos:** é a única chave que permite deduplicar a carga e refazer a ingestão de
  forma reproduzível. Sem ela, recarregar o CSV duplicaria 1,5 milhão de linhas.
- **Por que o risco é baixo:** a fonte é dado aberto (Mapa, CC-BY) e **já está publicada com o
  nome**. O `proposal_id` não dá acesso a nada que não seja público — mas **facilita a junção**,
  e essa é a parte honesta que precisa estar escrita.
- **Como eliminar, se um dia valer a pena:** trocar o `proposal_id` por um hash com sal guardado
  fora do repositório. Está no roadmap, **não** está feito.

Isso está repetido em `api/README.md`, para quem ler só o código encontrar o mesmo aviso.

## Regras e lógica

- `decision_log(id, created_at, request_id, decision_type, entity_id, inputs_json, output_json, rule_version, model_version, source)`.
  `decision_type`: `risk_score` · `tilt_limit` · `alert` · `replay`.
- Toda decisão que vira alerta, limite ou score **gera uma linha**. Sem exceção.
- O log nunca registra chave de API, documento ou nome de pessoa. Chaves aparecem mascaradas (`****1234`).
- Chave ausente ou inválida → **401**, com a tentativa registrada (sem a chave).

## Implementação

### API (`api/`)
- `app/core/security.py`: dependência `require_api_key`, com as chaves em `AGRISHIELD_API_KEYS` (lista separada por vírgula) e comparação em tempo constante (`secrets.compare_digest`).
- `app/core/logging.py`: configuração do log JSON + middleware de `request_id`.
- `app/models.py`: tabela `decision_log`. `app/repositories/audit.py`: `record_decision(...)`.
- `app/api/v1/routes/audit.py`: consulta da trilha.
- `.env.example`: `AGRISHIELD_API_KEYS=troque-esta-chave`.

### Front-web (`front-web/`)

O front passou a enviar `X-API-Key` **só nas escritas protegidas** (hoje, o botão "Enviar ao
equipamento"): a chave vem de `AGRISHIELD_API_KEY` (no singular — a API aceita a lista
`AGRISHIELD_API_KEYS`) e nunca aparece em tela nem em log. O 401 tem duas mensagens distintas: "falta
configurar a chave" e "a API recusou a chave configurada", e as duas dizem que **o limite não foi
enviado**. Leitura continua sem cabeçalho nenhum, com teste afirmando isso.

## Critérios de aceite

- [x] `POST /devices/{id}/limit/publish` sem chave → 401. Com chave válida → 200.
- [x] Toda resposta traz `X-Request-ID`, e o mesmo id aparece no log da requisição.
- [x] Calcular um risco e publicar um limite geram linhas em `decision_log` com entrada, saída e versões. O alerta vindo do equipamento também. **O `replay` fica para a W9**, que ainda não existe: o tipo está no enum e sem gravador.
- [x] Teste automático garante que nenhuma chave, nome ou documento aparece no log.
- [x] `GET /audit` exige chave e devolve os registros mais recentes primeiro.

## Tarefas

- [x] Chave de API + testes (401/200)
- [x] Log estruturado + `request_id`
- [x] Tabela e repositório de auditoria (+ `event_integrity`, o hash dos eventos)
- [x] Registro nas decisões: risco, limite e alerta. O `replay` sai junto com a W9
- [x] Endpoint de consulta + documentação em `document/arquitetura.md` e nos READMEs

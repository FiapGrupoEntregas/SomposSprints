# Validação geral do projeto — 25/09/2026

## Escopo e resultado

Validação local da API, do front-web e do firmware, seguida de smoke test com a API e o Streamlit
em execução. Os testes unitários não acessaram a internet. O MQTT foi desativado no smoke test da
API.

| Área | Verificações | Resultado |
|---|---|---|
| API | `uv run ruff check .`, `uv run ruff format --check .` e `uv run pytest` | Lint e formatação aprovados; **845 testes passaram**, 15 foram desmarcados e houve 12 avisos |
| Front-web | `uv run ruff check .`, `uv run ruff format --check .` e `uv run pytest` | Lint e formatação aprovados; **152 testes passaram** |
| Firmware | `pio run` | Compilação aprovada; 845.769 bytes de flash e 46.952 bytes de RAM |
| API em execução | `GET /api/v1/health` | HTTP 200; status `ok`, versão `0.1.0`, ambiente `dev` |
| Streamlit em execução | `/_stcore/health` e navegação pelo menu | Health `ok`; início e página **Mapa de risco** carregaram, com a fazenda de exemplo e o mapa |

Os 15 testes desmarcados são os testes de ponta a ponta marcados `e2e`; eles precisam de rede e
broker e **não** foram executados nesta validação. O caminho `/risk_map` retornou 404 quando
digitado diretamente antes da navegação do Streamlit completar; ao abrir a página pelo item do
menu, ela funcionou. Portanto, o 404 isolado não foi reproduzido como falha da navegação normal.

## Inconsistências documentais corrigidas

- A especificação E2 ainda dizia que a telemetria imediata era `TODO(E4)`, apesar de E4 já estar
  implementada. O texto agora descreve a publicação imediata na mudança de nível.
- O roteiro do vídeo afirmava que a base não tinha nenhum dado pessoal. A redação agora esclarece
  que nome e documento são descartados, mas `proposal_id` pode ser ligado ao CSV público que contém
  o nome, conforme a ressalva da I5.
- Foi removida uma repetição consecutiva da mesma observação sobre o teste de `X-Request-ID` no
  README da API.

## Avisos e pontos de atenção não bloqueantes

- O navegador registrou repetidamente `Invalid color passed for textColor in theme.sidebar: ""`.
  A configuração atual define a cor principal, mas não uma cor de texto da barra lateral. A
  aplicação continuou funcionando; o aviso ficou registrado sem uma alteração especulativa no
  tema.
- Os testes da API registraram avisos de depreciação no código do projeto: `datetime.utcnow()` em
  `api/app/services/history.py` e `api/app/services/reports.py`, e
  `HTTP_422_UNPROCESSABLE_ENTITY` em `api/app/api/v1/routes/replay.py`. Também houve avisos de
  depreciação originados por dependências de teste (Starlette/httpx e AnyIO). São itens para uma
  rodada futura de manutenção, não falhas desta execução.
- O PlatformIO compilou, mas informou que o Core 6.1.19 está obsoleto e que há uma versão 6.2.0
  detectada. Convém planejar a atualização do ambiente, sem alterar a versão durante esta validação.
- O smoke test verificou API e front localmente, mas não substitui o teste ponta a ponta
  dispositivo → broker MQTT → API → front. Nenhum resultado de integração com broker foi inferido
  deste teste.

## Revisão contra o enunciado

Após a validação acima, a mensagem `purpose` do relatório por cultura foi ajustada para informar
também na interface que o recorte vem da carteira agrícola PSR/SISSER e não representa operação ou
risco de acidente de máquina. O critério agora tem teste automatizado. Validação focada posterior:
`uv run pytest tests/test_reports.py -q` (**35 passaram**), `ruff check` aprovado e
`ruff format --check` aprovado nos dois arquivos Python alterados.

## Limpeza

O Streamlit e a API foram encerrados; um worker órfão da API foi identificado pelo processo
`multiprocessing` e terminado pelo PID específico. As portas 8765 e 8766 ficaram livres. O arquivo
SQLite criado no startup para esta execução foi removido. A API usada no teste tinha MQTT
desativado e estava vinculada a loopback; não foi feita publicação em broker.

# Histórico da reconstrução do projeto

Este documento distingue o que já existia na cópia local preservada do que foi reconstruído após a
perda da cópia editada. A versão perdida não está disponível nesta pasta e não foi recuperada do
GitHub; portanto, este registro é baseado nos arquivos existentes e no resumo/instruções fornecidos
pelo usuário, não em um diff exato entre as duas versões.

## Já existia na cópia preservada

- O produto AgriShield com API FastAPI, front Streamlit, regras de risco relevo × clima, SQLite,
  documentação de features e firmware ESP32.
- O controle de acesso da API por `X-API-Key`, comparação em tempo constante, falha fechada,
  mascaramento da chave em logs e auditoria. Isso já estava em
  [`api/app/core/security.py`](../api/app/core/security.py); não foi criado durante a reconstrução.
- O modelo oficial por regras/modelo preditivo e a arquitetura do firmware e circuito da pasta
  [`iot/`](../iot/).
- A maior parte das funcionalidades descritas em [`feature/README.md`](../feature/README.md).

## Reconstruído ou complementado nesta cópia

- **MLP experimental e opcional:** finalização do treino opt-in, artefato separado, metadados de
  reprodutibilidade e documentação de métricas/limitações. O modelo neural permanece fora da
  seleção do modelo oficial e não determina limites nem alertas.
- **API e front para a MLP:** parâmetro `include_experimental_mlp` desligado por padrão, retorno
  experimental separado, erros do artefato não ocultados, registro de auditoria e controle
  individual por sessão na tela de previsão de risco.
- **Teste do ID de requisição longo no Windows:** o caso passou a testar diretamente o filtro do
  `X-Request-ID`, sem tentar transportar um cabeçalho de 100.000 caracteres no cliente HTTP.
- **Integrantes:** nomes e RMs foram registrados no README raiz conforme os dados enviados pelo
  usuário.
- **Wokwi:** projeto público criado a partir dos arquivos locais de firmware, circuito e
  bibliotecas; link e evidências manuais adicionados à documentação.
- **Status de validação do ESP32:** E1 teve leituras manuais registradas; E3 e E4 receberam
  evidências parciais de MQTT/retained/NTP/telemetria. O histórico deixa explícito o que não foi
  comprovado (recebimento pela API/painel, alertas físicos, evento `rollover` com contexto e demais
  critérios ainda pendentes).
- **Backlog de nuvem:** foram explicitadas evoluções como segurança do transporte MQTT, controles
  de acesso de produção e observabilidade. Essas sugestões não significam que o projeto foi
  implantado ou certificado para nuvem.

## Validações registradas

Os resultados abaixo vêm das execuções feitas durante a reconstrução; não significam que todos os
testes foram repetidos após cada atualização documental:

- API: 838 testes aprovados e 15 testes E2E desmarcados na execução completa registrada.
- Front-web: 152 testes aprovados, com Ruff e verificação de formatação aprovados na execução
  registrada.
- Firmware: compilação PlatformIO aprovada.
- Integração externa: uma execução normal observou 6/6 telemetrias na API; uma repetição do teste de
  rajada observou 100/100 mensagens sem perda/duplicação. O broker público também apresentou
  timeouts em outras tentativas; a suíte E2E inteira não foi aprovada de forma conclusiva.
- Wokwi: a evidência manual mais recente está em
  [`evidencias/2026-09-25-inclinometro-wokwi.md`](evidencias/2026-09-25-inclinometro-wokwi.md).

## Estado do versionamento

A pasta usada nesta reconstrução não contém metadados `.git`. Os arquivos foram atualizados na
cópia local, mas não foi criado commit nem enviado qualquer alteração ao GitHub.

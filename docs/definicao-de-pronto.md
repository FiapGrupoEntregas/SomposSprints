# Definição de Pronto (DoR e DoD)

## Definition of Ready: quando uma task pode começar

- [ ] Aponta para uma feature de `feature/` (ou está justificada na issue)
- [ ] Tem **critérios de aceite** verificáveis
- [ ] Cabe em **até 1 dia** de trabalho (se não couber, quebre em mais tasks)
- [ ] As dependências (outras features, dados, contratos) já estão prontas ou têm um mock combinado
- [ ] Tem um responsável e um prazo

## Definition of Done: quando uma task ou feature está pronta

### Código
- [ ] Todos os critérios de aceite da feature foram atendidos
- [ ] Lint e formatação sem erros (`ruff check`, `ruff format --check`) ou `pio run` compilando
- [ ] Testes adicionados ou atualizados e passando (ver [padroes-de-codigo.md](padroes-de-codigo.md#testes-mínimos-por-tipo-de-mudança))
- [ ] Sem segredo commitado. Variáveis novas estão no `.env.example`
- [ ] Se mudou dependências: `scripts/sync-requirements.sh` rodado

### Documentação
- [ ] **`README.md` atualizado** (estrutura de pastas, como executar, funcionalidades e histórico de lançamentos), ou uma justificativa no PR explicando por que não precisou
- [ ] O status da feature foi atualizado em `feature/README.md`
- [ ] Se mudou regra, contrato ou endpoint: `docs/regras-de-risco.md`, `docs/contrato-mqtt.md` ou `docs/arquitetura.md` atualizados

### Revisão
- [ ] PR aprovado por 1 pessoa, que **testou seguindo o "Como testar"**
- [ ] A CI está verde
- [ ] Merge feito na `main` e a demo continua funcionando de ponta a ponta

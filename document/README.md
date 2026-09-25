# 📚 Documentação técnica — Sompo AgriShield

Aqui ficam as práticas e decisões que **todo o time segue**. Na dúvida, vale o que está escrito aqui.

> **Onde fica cada coisa**
> - `document/`: documentação técnica e práticas do projeto (esta pasta)
> - `feature/`: detalhamento de cada feature (o que fazer e como aceitar)
> - `.claude/`: agentes e skills do Claude Code (ver [fluxo-agentes.md](fluxo-agentes.md))

| Documento | Para que serve | Quem precisa ler |
|---|---|---|
| [plano-de-implementacao.md](plano-de-implementacao.md) | Tudo que será implementado, em ordem, e o mapa para os entregáveis | Todos |
| [arquitetura.md](arquitetura.md) | Componentes, fluxos de dados e endpoints da API | Todos |
| [regras-de-risco.md](regras-de-risco.md) | O modelo de risco relevo × clima: fórmulas e limiares | Dev + pesquisa |
| [contrato-mqtt.md](contrato-mqtt.md) | Tópicos e payloads entre o ESP32 e a API | Dev |
| [fluxo-git.md](fluxo-git.md) | Branches, commits, PRs, issues, labels e versões | Todos |
| [padroes-de-codigo.md](padroes-de-codigo.md) | Convenções de Python, FastAPI, Streamlit e Arduino | Dev |
| [definicao-de-pronto.md](definicao-de-pronto.md) | Quando uma task pode começar e quando está pronta | Todos |
| [ambiente-de-desenvolvimento.md](ambiente-de-desenvolvimento.md) | Como instalar e rodar tudo (uv, pip, PlatformIO, Wokwi) | Dev + quem for testar |
| [decisoes.md](decisoes.md) | Registro das decisões de arquitetura e de escopo (ADRs) | Todos, e serve de material para o pitch |
| [demo.md](demo.md) | Roteiro da apresentação **ao vivo**, checklist e plano B | Todos |
| [roteiro-video.md](roteiro-video.md) | Roteiro do **vídeo de entrega** (até 5 min): blocos, tempos, quem narra e o que aparece na tela | Todos |
| [fluxo-agentes.md](fluxo-agentes.md) | Dinâmica de geração de código com os agentes (devs por área + revisor) | Dev |
| [dados-e-modelo.md](dados-e-modelo.md) | Fontes de dados (reais), pipeline, features, métricas e limitações | Dev + pesquisa |
| [user-stories.md](user-stories.md) | Histórias por perfil e matriz de rastreabilidade | Todos |
| [entregaveis.md](entregaveis.md) | Checklist dos entregáveis do enunciado da Sprint 4 | Todos |
| [backlog-pos-entrega.md](backlog-pos-entrega.md) | Sugestões do revisor que ficaram **fora** do escopo de cada feature, com o motivo | Dev |
| [historico-reconstrucao.md](historico-reconstrucao.md) | O que já existia na cópia preservada, o que foi reconstruído nesta sessão e o que não pôde ser comparado à cópia perdida | Todos |
| [evidencias/2026-09-25-validacao-geral.md](evidencias/2026-09-25-validacao-geral.md) | Resultados de lint, testes, build e smoke test local, com avisos e limitações | Todos |
| [evidencias/](evidencias/README.md) | Relatórios de validação e prints usados na entrega | Todos |

## Regras de ouro

1. **Nada entra na `main` sem PR.** A `main` precisa estar sempre pronta para a demo.
2. **Mudou o projeto, atualizou o `README.md`**, no mesmo PR.
3. **Regra de risco só existe em um lugar:** na `api/`, seguindo [regras-de-risco.md](regras-de-risco.md). O front e o firmware só exibem ou aplicam o que a API calcula.
4. **Contrato mudou, os dois lados mudam juntos.** Se um tópico ou payload MQTT mudar, atualize `contrato-mqtt.md`, o firmware e a API no mesmo PR.
5. **O código congela em 25/09.** Depois disso, só entram correções de bug que afetem a demo.

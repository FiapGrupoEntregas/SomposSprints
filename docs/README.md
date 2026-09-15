# 📚 Documentação técnica — Sompo AgriShield

Aqui ficam as práticas e decisões que **todo o time segue**. Na dúvida, vale o que está escrito aqui.

> **Onde fica cada coisa**
> - `docs/`: documentação técnica e práticas do projeto (esta pasta)
> - `feature/`: detalhamento de cada feature (o que fazer e como aceitar)
> - `document/`: entregáveis acadêmicos da FIAP

| Documento | Para que serve | Quem precisa ler |
|---|---|---|
| [arquitetura.md](arquitetura.md) | Componentes, fluxos de dados e endpoints da API | Todos |
| [regras-de-risco.md](regras-de-risco.md) | O modelo de risco relevo × clima: fórmulas e limiares | Dev + pesquisa |
| [contrato-mqtt.md](contrato-mqtt.md) | Tópicos e payloads entre o ESP32 e a API | Dev |
| [fluxo-git.md](fluxo-git.md) | Branches, commits, PRs, issues, labels e versões | Todos |
| [padroes-de-codigo.md](padroes-de-codigo.md) | Convenções de Python, FastAPI, Streamlit e Arduino | Dev |
| [definicao-de-pronto.md](definicao-de-pronto.md) | Quando uma task pode começar e quando está pronta | Todos |
| [ambiente-de-desenvolvimento.md](ambiente-de-desenvolvimento.md) | Como instalar e rodar tudo (uv, pip, PlatformIO, Wokwi) | Dev + quem for testar |
| [decisoes.md](decisoes.md) | Registro das decisões de arquitetura e de escopo (ADRs) | Todos, e serve de material para o pitch |
| [demo.md](demo.md) | Roteiro da apresentação, checklist e plano B | Todos |

## Regras de ouro

1. **Nada entra na `main` sem PR.** A `main` precisa estar sempre pronta para a demo.
2. **Mudou o projeto, atualizou o `README.md`**, no mesmo PR.
3. **Regra de risco só existe em um lugar:** na `api/`, seguindo [regras-de-risco.md](regras-de-risco.md). O front e o firmware só exibem ou aplicam o que a API calcula.
4. **Contrato mudou, os dois lados mudam juntos.** Se um tópico ou payload MQTT mudar, atualize `contrato-mqtt.md`, o firmware e a API no mesmo PR.
5. **O código congela em 23/09.** Depois disso, só entram correções de bug que afetem a demo.

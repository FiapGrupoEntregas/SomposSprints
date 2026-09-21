# W13 — Score híbrido e explicabilidade na tela

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Camadas | api, front-web |
| Depende de | D3, W3 |
| Janela | 22/09 |
| Responsável | dev-api + dev-front |
| Status | 🟦 Em revisão (API e front implementados; 1 correção da API pendente) |

## Objetivo

Mostrar, lado a lado, o **nível por regras** (que explica o alerta ao operador) e a **probabilidade do
modelo** (que interessa à seguradora), deixando claro o que cada um significa e no que se baseia.

## Escopo

**Inclui**
- O endpoint de risco (W3) passa a devolver, por dia: `model_probability` (0–1), `model_version` e `model_drivers` (as 3 variáveis que mais pesaram).
- Sem modelo carregado, os campos vêm `null` e a tela esconde o bloco. Nada quebra.
- No front: um cartão "Probabilidade de sinistro (modelo)" ao lado do nível por regras, com a versão do modelo, a métrica de teste e um aviso: *"modelo treinado com sinistros reais do PSR; as regras continuam sendo a base do alerta ao operador"*.
- Registro da decisão na trilha de auditoria (I5), com a versão da regra e do modelo.

**Não inclui**
- Substituir as regras pelo modelo. O alerta ao operador continua vindo das regras (explicável).

## Critérios de aceite

- [x] O endpoint devolve probabilidade e versão do modelo quando há artefato, e `null` quando não há.
- [x] A tela mostra as duas leituras sem confundir: nível (regras) em destaque e a probabilidade como cartão secundário, com a ressalva da API na íntegra. *(Front.)*
- [x] Os 3 fatores exibidos batem com a importância das variáveis do D3 (importâncias de permutação, lidas do artefato).
- [x] Cada consulta com modelo gera uma linha em `decision_log`, com a versão da regra **e** a do modelo.

### Decisões registradas na W13

- **O pipeline manda.** `forecast.model`, os três campos por dia e o `model_version` da trilha só
  aparecem se o `.joblib` carregou. As duas metades do modelo vêm de arquivos diferentes, e uma
  escrita interrompida num retreino deixa `.json` novo com `.joblib` pela metade: anunciar
  "modelo v1, AUC-PR 0,073" sem nenhuma probabilidade seria o bloco que existe para dizer a
  verdade descrevendo um modelo que não pontuou nada.
- **Nada de métrica no código.** Versão, métricas, importâncias e até a frase "não superou o
  baseline" saem do artefato em tempo de execução; um retreino que mude o resultado inverte a
  frase sozinho.
- **Campo novo `model` no nível da resposta**, além dos três por dia: carrega a ressalva, as
  métricas do teste e o tamanho da amostra. Fica fora do dia para não repetir o bloco 7 vezes.
- **A ressalva da janela.** O modelo foi treinado com uma linha por apólice/safra e aqui é
  aplicado a um dia. O valor serve para **comparar dias e fazendas**, não como probabilidade
  calibrada — e o texto diz isso.

## Tarefas

- [x] Integrar o modelo no serviço de risco (`app/services/model_scoring.py`)
- [x] Ajustar schema e endpoint
- [x] Cartão no front + textos explicativos
- [x] Atualizar `document/arquitetura.md` e os READMEs

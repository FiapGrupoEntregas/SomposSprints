# Roteiro do vídeo de entrega (até 5 min)

Vídeo obrigatório do enunciado da Sprint 4: **até 5 minutos, narração humana** (voz de gente, não
sintetizada), publicado no YouTube como **"não listado"**, com o link no `README.md` da raiz.

> **Regra deste roteiro:** só entra na tela o que **existe em código**. Conferido contra o
> repositório em **20/09/2026**, depois das entregas de W8, W9, W12, W13, D3 e I4. Situação de
> cada item: [entregaveis.md](entregaveis.md).
>
> Este roteiro é para o **vídeo**. O roteiro da apresentação ao vivo, com checklist e plano B, é o
> [demo.md](demo.md).

## Quem narra

O enunciado pede narração humana e contribuição visível de todos. Sugestão de divisão — preencha os
nomes antes de gravar:

| Voz | Integrante | Blocos |
|---|---|---|
| A | _(preencher)_ | 1, 10 — problema e fechamento |
| B | _(preencher)_ | 2, 3, 4 — solução, mapa de risco e score híbrido |
| C | _(preencher)_ | 5, 6 — limite dinâmico e dispositivo |
| D | _(preencher)_ | 7, 8, 9 — seguradora, replay, dados e rastreabilidade |

Se o grupo preferir uma voz só, tudo bem: o enunciado exige humana, não coral. Mas registre no
README quem fez o quê.

## Blocos

Total planejado: **4 min 45 s**. Sobra de 15 s para respiro entre cortes. São dez blocos curtos —
cronometre cada um, porque o estouro costuma vir dos blocos 3 e 6.

| # | Tempo | Quem | O que aparece na tela | O que se fala (ideia, não decoreba) |
|---|---|---|---|---|
| 1 | 0:00–0:22 | A | Foto/vídeo curto de trator em encosta (banco de imagens livre) + título "Sompo AgriShield" | O seguro de máquina agrícola é reativo: paga depois do acidente. E o fator que mais pesa, o **relevo**, quase não é considerado. Uma encosta de 12° é tranquila com solo seco; depois de 40 mm de chuva, a mesma encosta capota trator. |
| 2 | 0:22–0:45 | B | Diagrama de arquitetura de [arquitetura.md](arquitetura.md#diagrama-final-entrada--banco--modelo--saída), com zoom lento | Cruzamos **relevo por satélite** com **previsão do tempo**, calculamos risco por talhão e por dia, e mandamos o **limite de inclinação do dia** para um dispositivo na própria máquina. Entrada, banco, modelo e saída — tudo com regras explicáveis: todo alerta mostra o motivo, em números. |
| 3 | 0:45–1:25 | B | Front, página **Mapa de risco**. Aba **Relevo**: grade 10×10 do Sítio Café da Serra (Carmo de Minas/MG), KPIs. Depois aba **Previsão de risco**: faixa dos 7 dias, clicar no dia vermelho, mapa das células. Em seguida trocar a fazenda para Fazenda Planalto de Grãos (Sorriso/MT) | Este é o relevo **real**, do DEM de satélite. Agora o clima: no café em Carmo de Minas, a chuva prevista deixa **_(leia o número da tela)_** das células em vermelho. Na fazenda de soja em Sorriso, com terreno plano, os sete dias ficam verdes. **Mesma chuva, resultados opostos** — porque o que muda é o relevo. |
| 4 | 1:25–1:48 | B | Mesma página, rolar até o **cartão "🤖 Probabilidade de sinistro (modelo)"**, ao lado do nível por regras. Mostrar a linha da AUC-PR e a ressalva que o cartão exibe por inteiro | Ao lado da regra, um **modelo treinado com sinistros reais** do seguro rural dá uma segunda leitura para a seguradora. No teste de 2024 ele **supera** o baseline — **por pouco**, com **26 sinistros**, e porque o conjunto de teste dobrou, não porque o modelo melhorou. Por isso o alerta ao operador continua vindo da **regra explicável**. |
| 5 | 1:48–2:15 | C | Página **Equipamento ao vivo**: card do limite do dia (ex.: 15° seco → 10° encharcado, com o motivo), clique no botão **Enviar ao equipamento**, confirmação na tela | O limite seguro não é fixo: ele cai quando o solo encharca. A plataforma calcula o limite do dia e **publica para a máquina** — por MQTT, com mensagem retida, então o equipamento recebe o último limite assim que liga. |
| 6 | 2:15–3:00 | C | Wokwi lado a lado com o painel ao vivo. (a) Serial com `[mqtt] conectado` e o limite recebido; (b) MPU6050 em ~12° → LED amarelo/vermelho + buzzer + OLED; (c) MPU6050 em ~60° → capotamento: LED vermelho travado e evento no painel com o gráfico dos 30 s anteriores | O ESP32 mede a inclinação com o MPU6050 e alerta o operador **na hora, sem depender da internet**: o limite fica gravado na memória do dispositivo. Se o pior acontecer, ele detecta o capotamento e manda o evento com **30 segundos de contexto** — a seguradora sabe o que estava acontecendo. |
| 7 | 3:00–3:30 | D | Página **Subscrição**: selo da classe A/B/C com o score, os indicadores do terreno e a carteira ordenada. Depois página **Relatórios**: abas **Equipamento**, **Região** e **Cultura**, com o gráfico de tendência e o botão de CSV | Para a seguradora, o mesmo relevo vira duas coisas. Na **cotação**, um perfil objetivo do terreno — classe A, B ou C — sem questionário e sem hardware. E, na carteira, **tendências por equipamento, por região e por cultura**, calculadas sobre 1,5 milhão de apólices reais, com exportação em CSV. |
| 8 | 3:30–3:55 | D | Página **Replay de acidentes**: abrir o caso de Imbituva, mostrar o veredito, a fonte da notícia e a faixa "O que este replay não prova". Depois o placar do topo | Rodamos o motor na data e no local de **cinco acidentes reais noticiados**. O placar está na tela e ele **não** é de cinco em cinco: teria alertado em **dois de cinco no ponto** e três de cinco na vizinhança. Em Imbituva, o motor apontou risco de raio no dia em que um raio matou. E os três "não" ficam à vista, com o motivo — incêndio por falha mecânica não é o que este motor promete detectar. |
| 9 | 3:55–4:22 | D | (a) Terminal com a contagem da tabela `policy`; (b) resposta do `GET /api/v1/audit` no `/docs`, com uma linha de `decision_log`; (c) saída do `pytest` | Do lado dos dados, carregamos **1.525.473 apólices reais** do seguro rural (PSR/SISSER, dados abertos) — e **sem nenhum dado pessoal**: nome e documento são descartados na leitura. **Toda decisão** fica registrada com entrada, saída e a versão da regra e do modelo que decidiram, e a escrita exige chave de API. São **_(recontar na véspera)_** testes automatizados, todos offline. |
| 10 | 4:22–4:45 | A | Slide simples: o que está pronto · o que é próximo passo · integrantes | Está no ar: mapa de risco, limite dinâmico na máquina, telemetria e capotamento, perfil de subscrição, relatórios de tendência, replay de casos reais, base real de sinistros e trilha de auditoria. O próximo passo é **calibrar**: o rótulo que temos hoje é sinistro de **seguro agrícola**, não de máquina — é por isso que o modelo vence num alvo que não é o perigo que o alerta trata. Calibrar com a base de sinistros da Sompo é exatamente o que a parceria destrava. |

## O que **não** entra no vídeo

- **Features que não foram implementadas:** alerta pelo Telegram (**W10**, decidido **fora do
  escopo** por exigir token de bot). Se for citado, só como roadmap, e nunca com tela.
  > O **histórico do equipamento (W11)** saiu desta lista em 21/09: foi entregue e aprovado, tem
  > rota, testes e a seção **Passaporte (prévia)** na página Equipamento ao vivo. Pode aparecer na
  > tela — dizendo que é a **prévia** do Passaporte Digital, não o produto completo.
- **SoilGrids.** Aparece como fonte opcional em documento antigo, mas **não está implementado**.
  Não cite como fonte de dados do sistema.
- **Métrica de cabeça.** O número do modelo muda a cada retreino. Se for citar AUC-PR, ela precisa
  estar **na tela no mesmo instante** — o cartão do front lê tudo do artefato em tempo de execução.
- **Qualquer número sem origem.** Se for citar valor, ele aparece na tela junto com a fala.
- **Vender o resultado do modelo redondo.** Desde o retreino de 21/09 ele **supera** o baseline —
  e dizer só isso é tão errado quanto a versão antiga. Não diga "o modelo venceu", "é melhor que a
  regra" nem "está validado". A frase falada é a do bloco 4, que cabe nos 23 s; **a versão
  completa, para responder à banca, são as três ressalvas mais o alvo:**
  1. **26 positivos** no teste, abaixo do mínimo de 30 que o próprio `train_model.py` exige para
     conclusão firme;
  2. o **IC 95% da diferença quase toca o zero** — extremo inferior **+0,003**, ou seja, "o ponto
     estimado é positivo e o intervalo, por pouco, não contém o zero", não "demonstrado com folga";
  3. **a virada veio da troca do conjunto de teste, não de o modelo ter melhorado** — auditado: o
     artefato de 20/09, sem retreino nenhum, já venceria no teste novo, e a fatia antiga (8
     sinistros em 155) é que era atípica.

  E o pano de fundo: o baseline **nunca discriminou** "qualquer indenização", que no PSR é dominada
  por seca e geada; no alvo que as regras tratam (chuva e tempestade) quem discrimina é o baseline.
  A resposta longa está em [dados-e-modelo.md](dados-e-modelo.md#resultados-do-modelo-d3).

## Números citados no vídeo — de onde vêm

| Número | Fonte | Cuidado |
|---|---|---|
| 1.525.473 apólices | tabela `policy` depois de `scripts/load_psr.py` (D1) | confira com `SELECT count(*)` antes de gravar |
| Total de testes | `uv run pytest --collect-only` na API e no front-web | **não está fixado neste roteiro de propósito:** o número cresce a cada feature. Recontar na véspera e falar o do dia |
| AUC-PR do modelo × baseline | cartão do modelo no front, que lê `api/app/data/model/risk_model_v1.json` em tempo de execução | **o modelo foi retreinado em 21/09 e o resultado inverteu** (0,144 × 0,074 no teste de 2024, contra 0,073 × 0,091 em 20/09). Não decore: leia o que estiver no cartão na hora da gravação. A frase de comparação sai do artefato — se houver novo retreino, o cartão inverte a frase sozinho e a narração tem de acompanhar |
| **2 de 5 no ponto · 3 de 5 na grade** (replay) | placar de `GET /api/v1/replay/summary`, exibido no topo da página Replay | roda contra a Open-Meteo na hora; confira o placar na tela antes de gravar |
| % de células vermelhas em Carmo de Minas e os 7 dias verdes em Sorriso | leitura do front no dia | ⚠️ **a previsão muda todo dia.** Reabra as duas fazendas na hora de gravar e **fale o número que estiver na tela**. Se o contraste sumir, troque a fala para "o mesmo volume de chuva gera alerta na encosta e nada no plano" e, se precisar, use o cenário `chuva forte (simulado)` **dizendo em voz alta que é simulado** |
| 15° seco → 10° encharcado | [regras-de-risco.md](regras-de-risco.md) (`regras-de-risco/v1`) | o card na tela mostra o motivo; leia o da tela |
| 30 s de contexto no capotamento | `iot/src/main.ino` (E5) e [contrato-mqtt.md](contrato-mqtt.md) | — |
| 1,5 milhão de apólices nos relatórios | os mesmos dados da tabela `policy`, agregados por `GET /api/v1/reports/region` e `/crop` | é a mesma base do primeiro item, dita de forma arredondada |

## Checklist de gravação

- [ ] Recontar os números da tabela acima **no dia da gravação**
- [ ] **O modelo foi retreinado em 21/09.** Reabrir o cartão do modelo e conferir a métrica e a
      frase de comparação com o baseline **antes** de gravar o bloco 4 — e repetir a conferência a
      cada novo retreino
- [ ] Aquecer o cache abrindo as fazendas no front **na véspera e 30 min antes** (a Open-Meteo já
      devolveu `429` em 19/09 com muitas verificações no mesmo dia — ver [demo.md](demo.md))
- [ ] API e front no ar (`./scripts/run-demo.sh --aquecer`); Wokwi com `[mqtt] conectado` no Serial
- [ ] Conferir que as duas chaves batem: sem isso o botão "Enviar ao equipamento" (bloco 5)
      devolve 401. O `run-demo.sh` confere e avisa antes de subir
- [ ] Abrir as páginas **Subscrição**, **Relatórios** e **Replay** uma vez antes de gravar: a
      primeira carga de cada uma é a mais lenta
- [ ] Gravar em **1080p**, 30 fps, com o navegador em tela cheia e as notificações desligadas
- [ ] Zoom do navegador em 110–125%: texto pequeno não se lê no YouTube
- [ ] **Narração humana**, sem TTS. Sem música por cima da voz
- [ ] Cronometrar: **máximo 5:00**. Se passar, corte no bloco 9, não no 4 nem no 8 — os cortes têm
      de preservar o que o enunciado cobra (interface, relatórios e o resultado do modelo)
- [ ] Legenda ou cartela com os nomes dos integrantes
- [ ] Subir no YouTube como **"Não listado"** e testar o link numa janela anônima
- [ ] Colar o link no `README.md` da raiz e neste documento

**Link do vídeo:** _(preencher após publicar)_

# 🧩 Features — Sompo AgriShield

Cada feature tem um arquivo próprio explicando **o que resolve, o que entra e o que não entra, como
implementar em cada camada, os critérios de aceite e as tarefas**. Antes de começar uma feature, leia o
arquivo dela. Ao terminar, atualize o **status** nesta tabela.

**Prioridade:** **P0** = obrigatório para a entrega · **P1** = importante · **P2** = se sobrar tempo
**Status:** ⬜ A fazer · 🟨 Em andamento · 🟦 Em revisão · ✅ Pronto · ⛔ Bloqueado

## Infraestrutura (I)

| ID | Feature | Prioridade | Camadas | Depende de | Janela | Agente | Status |
|---|---|---|---|---|---|---|---|
| [I1](I1-cliente-open-meteo.md) | Cliente Open-Meteo e agregação diária | P0 | api | — | 19/09 | dev-api | ✅ |
| [I2](I2-integracao-mqtt.md) | Ponte MQTT na API | P0 | api | contrato MQTT | 21/09 | dev-api | ✅ |
| [I3](I3-persistencia.md) | Persistência em SQLite | P0 | api | I2 | 20/09 | dev-api | ✅ |
| [I4](I4-ambiente-de-demo.md) | Ambiente de demo e deploy | P0 | todas | tudo P0 | 24/09 | dev-api | 🟨 (código aprovado; faltam ensaio no Wokwi e teste no macOS) |
| [I5](I5-seguranca-rastreabilidade.md) | Segurança, controle de acesso e rastreabilidade | P0 | api | I3 | 22/09 | dev-api | ✅ |
| [I6](I6-simulador-e2e.md) | Simulador de dispositivo e testes ponta a ponta | P0 | api, testes | I2, I3 | 22/09 | qa-integracao | ✅ |

## Dados e modelo (D)

| ID | Feature | Prioridade | Camadas | Depende de | Janela | Agente | Status |
|---|---|---|---|---|---|---|---|
| [D1](D1-ingestao-psr.md) | Ingestão de dados reais de sinistro (PSR/SISSER) | P0 | api | — | 19/09 | dev-dados | ✅ |
| [D2](D2-dataset-relevo-clima.md) | Dataset de treino: relevo × clima × sinistro | P0 | api | D1, I1, W2 | 20/09 | dev-dados | 🟨 (1.184 de 2.500 linhas — cota da Open-Meteo) |
| [D3](D3-modelo-preditivo.md) | Modelo preditivo de risco e métricas | P0 | api | D2 | 21/09 | dev-dados | ✅ |

## Plataforma: API + front-web (W)

| ID | Feature | Prioridade | Camadas | Depende de | Janela | Agente | Status |
|---|---|---|---|---|---|---|---|
| [W1](W1-fazendas-de-demonstracao.md) | Fazendas de demonstração | P0 | api, front | — | 19/09 | dev-api + dev-front | ✅ |
| [W2](W2-mapa-de-relevo.md) | Mapa de relevo (inclinação e classes) | P0 | api, front | I1, W1 | 20/09 | dev-api + dev-front | ✅ |
| [W3](W3-previsao-de-risco.md) | Previsão de risco em 7 dias (capotamento + atolamento) | P0 | api, front | I1, W2 | 21/09 | dev-api + dev-front | ✅ |
| [W4](W4-limite-dinamico.md) | Limite dinâmico e envio ao ESP32 | P0 | api, front | W3, I2 | 22/09 | dev-api + dev-front | ✅ |
| [W5](W5-painel-ao-vivo.md) | Painel do equipamento ao vivo | P0 | api, front | I2, I3, E4 | 22/09 | dev-api + dev-front | ✅ |
| [W8](W8-perfil-de-subscricao.md) | Perfil de risco do terreno para subscrição | P1 | api, front | W2 | 23/09 | dev-api + dev-front | ✅ |
| [W12](W12-relatorios-tendencias.md) | Relatórios e tendências de risco | P1 | api, front | I3, D1, W8 | 22/09 | dev-api + dev-front | ✅ |
| [W13](W13-score-hibrido.md) | Score híbrido e explicabilidade na tela | P1 | api, front | D3, W3 | 22/09 | dev-api + dev-front | 🟦 (em revisão) |
| [W6](W6-recomendacoes.md) | Recomendações e janela segura | P1 | api, front | W3 | 23/09 | dev-api + dev-front | ✅ |
| [W7](W7-riscos-extras.md) | Riscos extras: raio, vento, incêndio | P1 | api, front | W3 | 23/09 | dev-api + dev-front | ✅ |
| [W9](W9-modo-replay.md) | Replay de acidentes reais | P1 | api, front | W3, ~~T2~~ (casos prontos) | 23/09 | dev-api + dev-front | ✅ |
| [W10](W10-alerta-telegram.md) | Alerta pelo Telegram | P2 | api | I2, E5 | 24/09 | dev-api | ⛔ (fora do escopo: exige token de bot) |
| [W11](W11-historico-do-equipamento.md) | Histórico do equipamento (prévia do Passaporte) | P2 | api, front | I3 | 24/09 | dev-api + dev-front | 🟦 (em revisão; api e front prontos) |

## Dispositivo: ESP32 no Wokwi (E)

| ID | Feature | Prioridade | Componente | Depende de | Janela | Agente | Status |
|---|---|---|---|---|---|---|---|
| [E1](E1-inclinometro.md) | Inclinômetro (roll/pitch) | P0 | MPU6050 | — | 19/09 | dev-iot | 🟦 em revisão (falta evidência no Wokwi) |
| [E2](E2-alerta-local.md) | Alerta local (LEDs + buzzer) | P0 | LEDs, buzzer | E1 | 19/09 | dev-iot | 🟦 em revisão (falta evidência no Wokwi) |
| [E3](E3-limite-via-mqtt.md) | Receber o limite do dia via MQTT | P0 | Wi-Fi | contrato MQTT | 20/09 | dev-iot | 🟦 em revisão (falta evidência no Wokwi) |
| [E4](E4-telemetria.md) | Telemetria via MQTT | P0 | Wi-Fi | E1 | 20/09 | dev-iot | 🟦 em revisão (falta evidência no Wokwi) |
| [E5](E5-deteccao-de-capotamento.md) | Detecção de capotamento | P1 | MPU6050 | E1, E4 | 21/09 | dev-iot | 🟦 em revisão (falta evidência no Wokwi) |
| [E6](E6-sensor-ambiente.md) | Temperatura/umidade e regra dos 30 local | P1 | DHT22 | E3, E4 | 22/09 | dev-iot | 🟦 em revisão (falta evidência no Wokwi) |
| [E7](E7-display-oled.md) | Display OLED | P2 | SSD1306 | E2, E3 | 23/09 | dev-iot | 🟦 em revisão (falta evidência no Wokwi) |
| [E8](E8-botao-de-ocorrencia.md) | Botão de ocorrência | P2 | botão | E5 | 23/09 | dev-iot | 🟦 em revisão (falta evidência no Wokwi) |

## Documentação e entrega (DOC)

| ID | Feature | Prioridade | Depende de | Janela | Agente | Status |
|---|---|---|---|---|---|---|
| [DOC1](DOC1-user-stories.md) | User Stories e rastreabilidade | P0 | — | 20/09 | doc-entrega | 🟨 (escrito; falta revisar com o time e atualizar 4 histórias) |
| [DOC2](DOC2-entrega-final.md) | Entrega final (README, diagrama, evidências, vídeo) | P0 | todas | 24–26/09 | doc-entrega | ⬜ |

## Trilha do time: não-código (T)

Pesquisa, casos reais, pitch e testes: ver [T-trilha-do-time.md](T-trilha-do-time.md).

## Cronograma (19 a 27/09)

Quatro frentes rodando **em paralelo**, cada uma com o seu agente. O revisor avalia cada entrega.

| Dia | dev-api + dev-front | dev-dados | dev-iot | qa + documentação |
|---|---|---|---|---|
| Sáb 19/09 | I1, W1 | D1 (dados reais do PSR) | E1, E2 | DOC1 (com o time) |
| Dom 20/09 | W2, I3 | D2 (dataset) | E3, E4 | DOC1 entregue · T1, T2 |
| Seg 21/09 | W3, I2 | D3 (modelo + métricas) | E5 | T3 |
| Ter 22/09 | W4, W5, I5, W12, W13 | apoio ao W13 | E6 | I6 (simulador + e2e) |
| Qua 23/09 | W6, W7, W8, W9 | — | E7, E8 | rodada 2 de validação · T4 (pitch) |
| Qui 24/09 | W10, W11, I4 | — | ajustes | evidências e prints |
| Sex 25/09 | **congelamento do código** | — | — | DOC2 · entregáveis |
| Sáb 26/09 | só correções de bug da demo | — | — | vídeo (T5) e ensaios |
| Dom 27/09 | **entrega** | | | convite ao `fiap-tutoria` |

**Ordem de risco:** nada foi cortado, mas a fila é esta. Se algum dia atrasar, quem escorrega são os
últimos da lista (W10, W11, E7, E8 e, depois, W6, W7, W9). **Os P0 não escorregam**: sem eles não há
entrega, porque são exatamente os itens que o enunciado cobra (sistema integrado, banco e modelo com
métricas, validação da integração, segurança e rastreabilidade, relatórios e documentação final).

## Como escrever uma feature nova

Copie [_TEMPLATE.md](_TEMPLATE.md), use o próximo ID da categoria (I, D, W, E ou DOC) e adicione uma linha na tabela certa.

# 🧩 Features — Sompo AgriShield

Cada feature tem um arquivo próprio explicando **o que resolve, o que entra e o que não entra, como
implementar em cada camada, os critérios de aceite e as tarefas**. Antes de começar uma feature, leia o
arquivo dela. Ao terminar, atualize o **status** nesta tabela.

**Prioridade:** **P0** = obrigatório para a demo · **P1** = importante · **P2** = só se sobrar tempo
**Status:** ⬜ A fazer · 🟨 Em andamento · 🟦 Em revisão · ✅ Pronto · ⛔ Bloqueado

## Infraestrutura (I)

| ID | Feature | Prioridade | Camadas | Depende de | Janela | Status |
|---|---|---|---|---|---|---|
| [I1](I1-cliente-open-meteo.md) | Cliente Open-Meteo e agregação diária | P0 | api | — | 15/09 | ⬜ |
| [I2](I2-integracao-mqtt.md) | Ponte MQTT na API | P0 | api | contrato MQTT | 18/09 | ⬜ |
| [I3](I3-persistencia.md) | Persistência em SQLite | P0 | api | I2 | 20/09 | ⬜ |
| [I4](I4-ambiente-de-demo.md) | Ambiente de demo e deploy | P0 | todas | tudo P0 | 23/09 | ⬜ |

## Plataforma: API + front-web (W)

| ID | Feature | Prioridade | Camadas | Depende de | Janela | Status |
|---|---|---|---|---|---|---|
| [W1](W1-fazendas-de-demonstracao.md) | Fazendas de demonstração | P0 | api, front | — | 15/09 | ⬜ |
| [W2](W2-mapa-de-relevo.md) | Mapa de relevo (inclinação e classes) | P0 | api, front | I1, W1 | 16/09 | ⬜ |
| [W3](W3-previsao-de-risco.md) | Previsão de risco em 7 dias (capotamento + atolamento) | P0 | api, front | I1, W2 | 17/09 | ⬜ |
| [W4](W4-limite-dinamico.md) | Limite dinâmico e envio ao ESP32 | P0 | api, front | W3, I2 | 18/09 | ⬜ |
| [W5](W5-painel-ao-vivo.md) | Painel do equipamento ao vivo | P0 | api, front | I2, I3, E4 | 20/09 | ⬜ |
| [W6](W6-recomendacoes.md) | Recomendações e janela segura | P1 | api, front | W3 | 22/09 | ⬜ |
| [W7](W7-riscos-extras.md) | Riscos extras: raio, vento, incêndio | P1 | api, front | W3 | 22/09 | ⬜ |
| [W8](W8-perfil-de-subscricao.md) | Perfil de risco do terreno para subscrição | P1 | api, front | W2 | 21/09 | ⬜ |
| [W9](W9-modo-replay.md) | Replay de acidentes reais | P1 | api, front | W3, T2 | 22/09 | ⬜ |
| [W10](W10-alerta-telegram.md) | Alerta pelo Telegram | P2 | api | I2, E5 | — | ⬜ |
| [W11](W11-historico-do-equipamento.md) | Histórico do equipamento (prévia do Passaporte) | P2 | api, front | I3 | — | ⬜ |

## Dispositivo: ESP32 no Wokwi (E)

| ID | Feature | Prioridade | Componente | Depende de | Janela | Status |
|---|---|---|---|---|---|---|
| [E1](E1-inclinometro.md) | Inclinômetro (roll/pitch) | P0 | MPU6050 | — | 19/09 | 🟨 leitura básica pronta |
| [E2](E2-alerta-local.md) | Alerta local (LEDs + buzzer) | P0 | LEDs, buzzer | E1 | 19/09 | ⬜ |
| [E3](E3-limite-via-mqtt.md) | Receber o limite do dia via MQTT | P0 | Wi-Fi | contrato MQTT | 19/09 | 🟨 conexão e assinatura prontas |
| [E4](E4-telemetria.md) | Telemetria via MQTT | P0 | Wi-Fi | E1 | 19/09 | ⬜ |
| [E5](E5-deteccao-de-capotamento.md) | Detecção de capotamento | P1 | MPU6050 | E1, E4 | 21/09 | ⬜ |
| [E6](E6-sensor-ambiente.md) | Temperatura/umidade e regra dos 30 local | P1 | DHT22 | E3, E4 | 22/09 | ⬜ |
| [E7](E7-display-oled.md) | Display OLED | P2 | SSD1306 | E2, E3 | — | ⬜ |
| [E8](E8-botao-de-ocorrencia.md) | Botão de ocorrência | P2 | botão | E5 | — | ⬜ |

## Trilha do time: não-código (T)

Pesquisa, casos reais, pitch e testes: ver [T-trilha-do-time.md](T-trilha-do-time.md).

## Cronograma

| Dia | Dev | Time |
|---|---|---|
| Ter 15/09 | I1, W1 | T6 (validar o circuito no Wokwi), início de T1 e T2 |
| Qua 16/09 | W2 | T1, T2 |
| Qui 17/09 | W3 (com cenários simulados) | T1 entregue, T3 |
| Sex 18/09 | W4, I2 | T2 entregue, T3 |
| Sáb 19/09 | E1–E4 | revisar e testar os PRs |
| Dom 20/09 | W5, I3. **Ponta a ponta funcionando** | T4 (pitch) |
| Seg 21/09 | E5, W8 | T4 |
| Ter 22/09 | W9, W6, W7, E6 (P1 restantes, nesta ordem) | T4 entregue |
| Qua 23/09 | I4. **Congelamento do código** | ensaio 1 |
| 24–26/09 | só correções | T5: vídeo de backup, ensaios 2 e 3 |
| Dom 27/09 | **Entrega** | |

**Se atrasar:** os P1 saem na ordem inversa da lista acima (primeiro E6, depois W7, W6…). Os P0 nunca saem.

## Como escrever uma feature nova

Copie [_TEMPLATE.md](_TEMPLATE.md), use o próximo ID da categoria e adicione uma linha nesta tabela.

# Demo — roteiro, checklist e plano B

> Este é o roteiro da **apresentação ao vivo**. O roteiro do **vídeo de entrega** (até 5 min,
> narração humana, YouTube não listado) está em [roteiro-video.md](roteiro-video.md), e usa só
> o que já existe em código.

## Roteiro (≈ 3 min)

| # | Tela | O que mostrar | Fala-chave |
|---|---|---|---|
| 1 | Mapa de risco | Fazenda de café em Carmo de Minas (MG), mapa de inclinação | "Este é o relevo real da fazenda, calculado com dados de satélite." |
| 2 | Mapa de risco | Linha do tempo: dia de chuva forte → a encosta fica 🔴 | "A mesma encosta é segura na terça e perigosa na quinta, porque a chuva mudou o solo." |
| 3 | Equipamento ao vivo | Limite do dia caindo de 15° para 10° e o botão **Enviar ao equipamento** | "O limite seguro muda com o clima e vai direto para a máquina." |
| 4 | Wokwi | MPU6050 em 12° → LED 🔴 + buzzer. O painel web mostra o alerta ao vivo | "O operador é avisado na hora, mesmo se a internet cair." |
| 5 | Wokwi | MPU6050 em 60° → **capotamento** → evento de emergência no painel | "Se o pior acontecer, a Sompo sabe na hora e com o contexto dos últimos 30 segundos." |
| 6 | Subscrição | Perfil do terreno (classe A/B/C) | "Na cotação, sem questionário: o terreno fala por si." |
| 7 | Replay | Acidente real noticiado → "o sistema teria alertado: 🔴" | "Testamos com casos reais." |

Valores do MPU6050 para cada ângulo: ver a tabela em [iot/README.md](../iot/README.md#simulando-inclinação).

> ✅ **Situação no fim de 20/09/2026: os sete passos rodam.** Subscrição (W8) e Replay (W9)
> deixaram de ser placeholder e estão implementados, com testes. O que ainda **não** foi feito é
> o ensaio completo com o Wokwi (passos 4 e 5), que depende de pessoa — ver
> [entregaveis.md](entregaveis.md).
>
> Se sobrar tempo no ao vivo, vale um oitavo passo: a página **Relatórios** (W12), com as
> tendências por equipamento, região e cultura sobre 1,5 milhão de apólices reais, e o **cartão
> do modelo** no Mapa de risco (W13) — que mostra a probabilidade ao lado do nível por regras e
> exibe a ressalva do artefato na íntegra.
>
> **Desde 21/09 o resultado é outro: com a D2 fechada em 2.256 linhas e o modelo retreinado, ele
> passou a superar o baseline por regras** no teste de 2024 (AUC-PR 0,144 × 0,074). **Diga as
> ressalvas junto:** são 26 sinistros no teste (o próprio `train_model.py` exige 30 para conclusão
> firme), o IC 95% da diferença quase toca o zero ([+0,003, +0,169]) e a virada veio da **troca do
> conjunto de teste**, não de o modelo ter ficado melhor — o modelo de 20/09, sem retreino, já
> venceria no teste novo. Assumir o resultado com as ressalvas costuma render mais crédito com a
> banca do que vendê-lo redondo. Leia do cartão, que sai do artefato; a leitura completa está em
> [dados-e-modelo.md](dados-e-modelo.md#resultados-do-modelo-d3).

> **Semana seca?** Setembro costuma ser seco em MG. Se a previsão real não tiver chuva, ligue o
> **cenário "chuva forte (simulado)"** nos passos 2 e 3 e diga isso em voz alta: "este é um cenário
> simulado; no replay mostramos um dia chuvoso real". A tela exibe o aviso automaticamente.

## Subindo tudo (I4)

```bash
cp api/.env.example api/.env                 # escolha a chave em AGRISHIELD_API_KEYS
cp front-web/.env.example front-web/.env     # AGRISHIELD_API_KEY = uma das chaves acima
./scripts/run-demo.sh --aquecer
```

O script sobe API e front juntos, confere o `/health`, **testa a chave de verdade** (um `GET
/audit` autenticado) e aquece o cache das 3 fazendas, imprimindo quanto cada uma levou. Um Ctrl+C
derruba os dois. Use `--conferir` na véspera para checar o ambiente sem subir nada, e `--sem-front`
quando estiver só testando o Wokwi. Medido em 20/09/2026, do "clone" à demo no ar: **14 s** nesta
máquina (7 s de `uv sync` com cache frio + 7 s de boot) — o critério da I4 é 10 min.

No **Windows**, o script não roda: use o WSL ou os dois terminais do PowerShell descritos no
[README](../README.md#0-demo-completa-com-um-comando-linux-e-macos).

## Checklist (30 min antes)

- [ ] Notebook carregado e na tomada. Notificações desligadas
- [ ] Internet principal testada **e** o hotspot do celular pronto como reserva
- [ ] **Cota da Open-Meteo:** aquecer o cache **na véspera e de novo 30 min antes**, com
      `./scripts/run-demo.sh --aquecer` (uma chamada de relevo e uma de previsão por fazenda, com o
      tempo de cada uma na tela) ou abrindo as 3 fazendas no front. O relevo vale 24 h e a previsão,
      1 h. Em 19/09/2026 a API passou a devolver **429 Too Many Requests** depois de muitas
      verificações no mesmo dia (cada chamada de relevo pede 100 pontos). Se acontecer na hora, o
      sistema responde 503 "Serviço de clima indisponível" e o painel ao vivo continua funcionando —
      mas o mapa não carrega, então **não deixe para aquecer na hora**.
      ⚠️ O cache é um `TTLCache` **em memória**, então ele vive dentro do processo da API: aquecer
      na véspera só adianta se **aquela mesma instância** continuar de pé. Reiniciou a API (ou
      rodou o `run-demo.sh` de novo)? O cache voltou a zero — aqueça outra vez, e é por isso que o
      aquecimento de 30 min antes é o que realmente conta.
      Aquecer `/risk` cobre mais do que parece: a API tem **um único** ponto de chamada da previsão
      horária, então a mesma resposta serve ao mapa de risco (W3), às recomendações (W6) e ao
      limite do equipamento (W4). O `--aquecer` ainda chama o `GET /replay/summary` (W9), que a
      frio é o mais caro do projeto — 5 elevações + 5 históricos numa requisição só. Medido em
      20/09/2026: **3 s a frio e ~6 ms depois**, com o aquecimento completo em 5 s.
      🔴 **Em 21/09/2026 a cota diária foi esgotada de verdade, e deu para medir o estrago**
      ([evidência](evidencias/2026-09-21-degradacao-sem-cota.md)): sem clima caem **relevo,
      previsão de risco e limite do dia** — e, com o limite, cai a **publicação do `config` no
      MQTT**, ou seja, **o ESP32 não recebe limite nenhum**. Não é só o mapa. Continuam de pé:
      fazendas, os três relatórios, o replay, o painel ao vivo e a auditoria — dá para improvisar,
      mas não é a demo.
      Duas consequências: **(1)** aquecer o cache deixou de ser recomendação e é **pré-requisito**;
      **(2)** **não gere dataset no dia da apresentação** — uma execução do `build_dataset.py`
      consome a cota inteira, porque cada requisição de clima pesa ~21 chamadas (o teto prático é
      de ~465 apólices por dia). Ver [dados-e-modelo.md](dados-e-modelo.md).
- [ ] API no ar: http://localhost:8000/api/v1/health retorna `ok`
- [ ] **As duas chaves, e compatíveis entre si** (I5). `AGRISHIELD_API_KEYS` no `api/.env` **e**
      `AGRISHIELD_API_KEY` no ambiente do front, com um valor que esteja na lista da API. Faltando
      qualquer uma das duas, o botão "Enviar ao equipamento" responde **401** — é a falha fechada
      funcionando, mas surpreende no palco. O `run-demo.sh --conferir` compara as duas e avisa; à
      mão, o teste que não gasta cota nem toca o broker é:
      `curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Key: SUA_CHAVE" http://localhost:8000/api/v1/audit?limit=1`
      (200 = vai funcionar; 401 = não vai).
      A chave do front pode vir de `front-web/.env` (o front carrega o arquivo) ou do ambiente — o
      que estiver exportado no terminal vence o arquivo, e é assim que o `run-demo.sh` repassa.
- [ ] Front no ar, com a fazenda da demo já aberta (isso aquece o cache do relevo e da previsão)
- [ ] Simulação do Wokwi rodando, com `[mqtt] conectado` no Serial
- [ ] O ESP32 recebeu o `config` (o Serial mostra o limite atual)
- [ ] O painel ao vivo mostra a telemetria chegando. Lembre que ele vira **offline** depois de 20 s
      sem telemetria: se o Wokwi estiver pausado, a faixa fica cinza — é o comportamento certo
- [ ] O caso de replay escolhido abre sem erro
- [ ] O vídeo de backup está no notebook **e** num pendrive
- [ ] As abas do navegador estão na ordem do roteiro

## Plano B

| Falha | O que fazer |
|---|---|
| Sem internet | Hotspot do celular. Se não der, passar o **vídeo de backup** |
| Broker HiveMQ fora | Trocar para `test.mosquitto.org` nos **dois** lados (ver abaixo) |
| Open-Meteo lenta ou fora | A API serve o último cache. Aquecer antes com `run-demo.sh --aquecer` |
| **Cota da Open-Meteo esgotada** (429 com `Daily API request limit exceeded`) | Não passa até as 00:00 UTC, e **nenhum recuo resolve**. Caem mapa, relevo, limite do dia e o `config` para o ESP32. Vá para os relatórios, o replay e o painel ao vivo, e use o vídeo de backup no trecho do limite. Confirme a causa com `curl` olhando o **corpo** da resposta, não o código |
| Botão "Enviar ao equipamento" dá 401 | Chave faltando ou diferente entre API e front. Pare, rode `./scripts/run-demo.sh --conferir`, corrija e suba de novo |
| Painel "offline" com o Wokwi rodando | São os 20 s sem telemetria (W5). Confira `[mqtt] conectado` no Serial e o **prefixo** igual nos dois lados |
| Broker cai no meio da demo | A API reconecta sozinha; a detecção leva de 1× a 2× o keepalive (15 s ⇒ ~15–30 s, medido na I6). Continue falando e espere |
| Porta 8000 ou 8501 ocupada | Sobrou processo de um ensaio. `./scripts/run-demo.sh --porta-api 8010 --porta-front 8510`, ou mate o processo antigo |
| Wokwi não conecta | Mostrar o painel com o histórico já gravado e o vídeo do trecho IoT |
| Tudo falhou | Vídeo de backup com narração ao vivo |

### Trocar de broker (plano B do MQTT)

São **dois** valores, um de cada lado, e eles precisam mudar juntos:

| Onde | O quê |
|---|---|
| `api/.env` | `AGRISHIELD_MQTT_HOST=test.mosquitto.org` (porta segue 1883) |
| `iot/src/main.ino`, linha 37 | `const char* MQTT_HOST = "test.mosquitto.org";` — recompilar e reiniciar a simulação |

O `TOPIC_PREFIX`/`AGRISHIELD_MQTT_TOPIC_PREFIX` **não muda**. Reinicie a API depois de editar o
`.env`. Validado em 20/09/2026 **do lado da API**: com `test.mosquitto.org`, a ponte assinou os três
tópicos e gravou telemetria e status normalmente
([evidência](evidencias/2026-09-20-ambiente-de-demo.md)). O lado do firmware é uma troca de
constante, mas **ainda não foi testado no Wokwi** — se der, teste antes do dia.

## Vídeo de backup

Gravar em 26/09, com o fluxo completo do roteiro, em 1080p e com até 4 min. Guardar no drive do time e em um pendrive.

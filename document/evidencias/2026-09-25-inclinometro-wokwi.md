# Validação manual do inclinômetro no Wokwi — 25/09/2026

## Projeto

[AgriShield ESP32 no Wokwi](https://wokwi.com/projects/476164477397501953) — projeto público
com `sketch.ino`, `diagram.json` e `libraries.txt` do firmware em `iot/`.

## Procedimento e resultados

Com X = 0, ajustamos Y e Z no MPU6050 e conferimos o `roll`, o `tilt` e o nível no Serial Monitor.
O log é emitido a 1 Hz e a leitura passa pela média móvel de cinco amostras.

| Ângulo de referência | Y (g) | Z (g) | Leitura no Serial | Erro (pela leitura exibida) | Observação |
|---:|---:|---:|---:|---:|---|
| 0° | 0,00 | 1,00 | 0,0° | 0,0° | nível `green` |
| 10° | 0,20 | 1,10 | 10,3° | 0,3° | nível `green` |
| 15° | 0,30 | 1,10 | 15,2° | 0,2° | nível `red` com limite padrão de 15° |
| 60° | 0,85 | 0,50 | 59,5° | 0,5° | após cerca de 3 s acima de 45°, nível `rollover` |

As quatro leituras exibidas atendem à tolerância de 0,5° do critério E1. A apresentação do Serial
arredonda para uma casa decimal. O ensaio de 60° também observou a transição para `rollover`, mas
não substitui a validação completa da feature E5.

## Limitações desta execução

- A compilação web e a leitura local do sensor funcionaram. Em uma primeira execução, a conexão
  falhou; após recarregar o projeto salvo e iniciar outra simulação, Wi-Fi conectou, o relógio
  sincronizou via NTP e o broker MQTT conectou após uma tentativa inicial malsucedida.
- Na segunda execução, o ESP32 recebeu pelo tópico retained a configuração de 10° (com motivo de
  solo encharcado), registrou `limit_applied` e confirmou os três envios do evento. O log também
  confirmou a publicação de telemetrias sequenciais (seq 1–5), com `ts` NTP não nulo e intervalos
  de 5 s. A configuração retida estava fora da validade; o firmware registrou isso e continuou com
  o limite recebido. Essa execução confirma o replay retained ao iniciar a simulação, mas não inclui
  um teste manual de publicação por `mosquitto_pub`.
- O broker é público e a API não estava conectada como parte deste ensaio. Portanto, as mensagens
  publicadas não comprovam persistência na API nem atualização do painel.
- Os estados textuais `red` e `rollover` foram observados no Serial durante o teste dos ângulos;
  essa execução ainda não estava conectada ao MQTT. O broker event `rollover` com contexto não
  foi comprovado. LEDs e buzzer também não foram conferidos visualmente.
- Esta evidência confirma os ângulos de E1 e registra observações parciais de E3–E5. Não aprova
  todos os critérios de E2–E8 nem o fluxo ponta a ponta com API e painel.

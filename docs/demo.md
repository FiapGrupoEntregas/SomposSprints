# Demo — roteiro, checklist e plano B

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

> **Semana seca?** Setembro costuma ser seco em MG. Se a previsão real não tiver chuva, ligue o
> **cenário "chuva forte (simulado)"** nos passos 2 e 3 e diga isso em voz alta: "este é um cenário
> simulado; no replay mostramos um dia chuvoso real". A tela exibe o aviso automaticamente.

## Checklist (30 min antes)

- [ ] Notebook carregado e na tomada. Notificações desligadas
- [ ] Internet principal testada **e** o hotspot do celular pronto como reserva
- [ ] API no ar: http://localhost:8000/api/v1/health retorna `ok`
- [ ] Front no ar, com a fazenda da demo já aberta (isso aquece o cache do relevo e da previsão)
- [ ] Simulação do Wokwi rodando, com `[mqtt] conectado` no Serial
- [ ] O ESP32 recebeu o `config` (o Serial mostra o limite atual)
- [ ] O painel ao vivo mostra a telemetria chegando
- [ ] O caso de replay escolhido abre sem erro
- [ ] O vídeo de backup está no notebook **e** num pendrive
- [ ] As abas do navegador estão na ordem do roteiro

## Plano B

| Falha | O que fazer |
|---|---|
| Sem internet | Hotspot do celular. Se não der, passar o **vídeo de backup** |
| Broker HiveMQ fora | Trocar para `test.mosquitto.org` (host no `.env` da API e em `MQTT_HOST` no firmware) |
| Open-Meteo lenta ou fora | A API serve o último cache. Deixar a fazenda da demo aberta antes, para aquecer o cache |
| Wokwi não conecta | Mostrar o painel com o histórico já gravado e o vídeo do trecho IoT |
| Tudo falhou | Vídeo de backup com narração ao vivo |

## Vídeo de backup

Gravar em 24/09, com o fluxo completo do roteiro, em 1080p e com até 4 min. Guardar no drive do time e em um pendrive.

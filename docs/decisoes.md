# Registro de decisões (ADRs)

Cada decisão importante fica registrada aqui: o contexto, o que decidimos e as consequências. Isso
evita reabrir discussões e **serve de material para o pitch** ("por que fizemos assim").
Para uma decisão nova, adicione ao fim da lista, com o próximo número.

---

### ADR-001: Foco em relevo × clima
- **Contexto:** o MVP original (Passaporte Digital) era amplo demais para 2 semanas. Na última call, a Sompo comentou que **nenhum projeto estava abordando problemas de terreno**.
- **Decisão:** o produto passa a prevenir acidentes com máquinas **cruzando relevo (inclinação, baixadas, topos) com clima (chuva, solo, vento, raio, calor)**, por talhão e por dia.
- **Consequências:** o problema é diferente dos concorrentes, a demo é visual (um mapa que muda com a previsão) e dá para atender qualquer segurado sem hardware. O Passaporte Digital vira visão de futuro.

### ADR-002: Cortar app mobile, BLE, ML, fraude e revenda
- **Contexto:** a equipe tem uma pessoa desenvolvendo e 2 semanas.
- **Decisão:** esses itens ficam fora do escopo e aparecem só no roadmap do pitch.
- **Consequências:** o escopo cabe no prazo. Os modelos de ML dependem da base de sinistros da Sompo, que não temos.

### ADR-003: ESP32 simulado no Wokwi, com Wi-Fi + MQTT
- **Contexto:** queremos algo embarcado na máquina. O Wokwi não simula BLE e o ESP32 simulado não enxerga o `localhost`.
- **Decisão:** um ESP32 básico no Wokwi (MPU6050, DHT22, LEDs, buzzer) falando **MQTT** com a API.
- **Consequências:** não precisa de hardware físico e funciona em qualquer rede. Dependemos do broker público (ver ADR-008).

### ADR-004: A regra de negócio fica só na API
- **Contexto:** o risco é exibido em três lugares (mapa, painel e dispositivo).
- **Decisão:** a `api/` é a única que calcula. O front só exibe e o ESP32 só aplica o limite recebido.
- **Consequências:** existe uma única fonte da verdade ([regras-de-risco.md](regras-de-risco.md)), testável com pytest.

### ADR-005: Open-Meteo como fonte única de clima e relevo
- **Contexto:** o plano original usava INMET, ANA, SRTM e IBGE, cada um com formato e acesso próprios.
- **Decisão:** usar só a Open-Meteo (elevação Copernicus 90 m, previsão, previsão histórica e ERA5), que é gratuita, sem chave e tem uma API JSON uniforme.
- **Consequências:** a integração fica simples. Aceitamos a resolução de 90 m e a previsão pontual (ver as limitações em regras-de-risco).

### ADR-006: Score baseado em regras, e não em ML
- **Contexto:** não há dados de sinistro para treinar um modelo.
- **Decisão:** usar regras explícitas, com limiares documentados, e mostrar o motivo de cada alerta.
- **Consequências:** o score é explicável para a banca e para o operador. A calibração com dados reais vira o primeiro passo do roadmap de ML.

### ADR-007: uv e pip suportados ao mesmo tempo
- **Contexto:** o dev usa uv. Parte do time e as plataformas de deploy usam pip e `requirements.txt`.
- **Decisão:** `pyproject.toml` + `uv.lock` são a fonte da verdade, e os `requirements*.txt` são gerados por script e verificados na CI.
- **Consequências:** qualquer um dos dois funciona e não há divergência de versões.

### ADR-008: Broker MQTT público (HiveMQ)
- **Contexto:** subir e manter um broker próprio custa tempo.
- **Decisão:** usar `broker.hivemq.com:1883`, com um prefixo de tópico próprio.
- **Consequências:** zero configuração. Não trafegamos dado sensível e a API valida os payloads. Plano B: `test.mosquitto.org`.

### ADR-009: Persistência em SQLite
- **Contexto:** precisamos guardar telemetria e eventos por alguns dias, e só para a demo.
- **Decisão:** usar SQLite (arquivo local) na API.
- **Consequências:** nada para instalar. Se a API for publicada num serviço gratuito, os dados podem ser apagados em cada novo deploy, o que é aceitável para a demo.

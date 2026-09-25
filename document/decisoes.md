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
- **Contexto:** subir e manter um broker próprio custa tempo na demonstração, mas um broker compartilhado não autentica publicadores nem protege mensagens.
- **Decisão:** `broker.hivemq.com:1883` sem TLS permanece exclusivamente para demo com dados sintéticos. Produção deve usar broker privado, TLS com validação de certificado, autenticação individual e ACL por dispositivo; o serviço deve falhar fechado se a configuração de produção não satisfizer os requisitos.
- **Consequências:** a demo continua simples, mas qualquer pessoa pode publicar e ler tópicos públicos. Esse modo não é adequado a equipamento real; TLS/credenciais sem ACL no broker não bastam para autorizar tópicos.

### ADR-009: Persistência em SQLite
- **Contexto:** precisamos guardar telemetria e eventos por alguns dias, e só para a demo.
- **Decisão:** usar SQLite (arquivo local) na API.
- **Consequências:** nada para instalar. Se a API for publicada num serviço gratuito, os dados podem ser apagados em cada novo deploy, o que é aceitável para a demo.

### ADR-010: Modelo preditivo treinado com dados reais do PSR (revisa a ADR-006)
- **Contexto:** a ADR-006 dizia que não havia base de sinistros para treinar. Em 19/09/2026 encontramos os dados abertos do **PSR/SISSER** (Mapa, CC-BY): uma linha por apólice, de 2006 a 2025, com coordenada da propriedade, cultura, vigência, valor indenizado e evento preponderante.
- **Decisão:** treinar um modelo simples com esses dados (D1, D2, D3) e usá-lo **ao lado** das regras, não no lugar delas (W13).
- **Consequências:** atende à exigência de modelo e métricas do enunciado, com dado **real**. O rótulo é de seguro agrícola, não de máquinas: essa limitação precisa ser declarada em toda apresentação.

### ADR-011: Nenhum dado pessoal no repositório (LGPD)
- **Contexto:** o CSV do PSR traz nome do segurado e documento parcial.
- **Decisão:** descartar essas colunas na leitura (D1), nunca gravá-las no banco nem versioná-las. O `data/raw/` fica fora do Git e só uma amostra anonimizada é versionada.
- **Consequências:** conformidade com a LGPD e com o requisito de proteção de dados, sem perder nada de útil para o modelo.

### ADR-012: Simulador de dispositivo para os testes de integração
- **Contexto:** o Wokwi não roda em CI nem em teste automatizado, mas o enunciado cobra validação da integração.
- **Decisão:** criar `scripts/simulate_device.py` (I6), que publica no mesmo contrato MQTT, e testes `tests/e2e/` fora da CI padrão. O Wokwi continua sendo o dispositivo da demo.
- **Consequências:** dá para provar confiabilidade da coleta e consistência dos dados com evidências reproduzíveis.

### ADR-013: Controle de acesso por chave de API, sem login de usuário
- **Contexto:** o enunciado pede controle de acesso, mas login com perfis não cabe no prazo.
- **Decisão:** chave de API (`X-API-Key`) nos endpoints de escrita/publicação e auditoria; leitura aberta somente no ambiente local de desenvolvimento/demo, protegida por chave quando implantada em produção.
- **Consequências:** atende ao MVP e evita expor telemetria por acidente em implantação. Uma chave de API compartilhada não substitui identidade de usuário, perfis e autorização por organização/dispositivo; esses controles continuam necessários em produção real.

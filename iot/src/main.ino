/*
 * AgriShield — firmware do dispositivo embarcado (ESP32)
 *
 * Inclinômetro com limite dinâmico: mede a inclinação da máquina (MPU6050),
 * recebe da API o limite seguro do dia via MQTT e alerta localmente.
 *
 * Este arquivo único roda nos dois ambientes:
 *   - PlatformIO: iot/src/main.ino  (pio run + extensão Wokwi no VS Code)
 *   - Wokwi web:  cole como sketch.ino, junto com diagram.json e libraries.txt
 *
 * Features (detalhes em feature/): E1 inclinômetro · E2 alerta local ·
 * E3 limite via MQTT · E4 telemetria · E5 capotamento · E6 ambiente ·
 * E7 display · E8 botão de ocorrência.
 * Contrato MQTT: document/contrato-mqtt.md
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <float.h>
#include <math.h>
#include <string.h>
#include <time.h>

#if defined(AGRISHIELD_MQTT_TLS)
#include <WiFiClientSecure.h>
#include "mqtt_tls_config.h"
#ifndef AGRISHIELD_WIFI_SSID
#error "Defina AGRISHIELD_WIFI_SSID no arquivo local mqtt_tls_config.h"
#endif
#ifndef AGRISHIELD_WIFI_PASSWORD
#error "Defina AGRISHIELD_WIFI_PASSWORD no arquivo local mqtt_tls_config.h"
#endif
#ifndef AGRISHIELD_MQTT_TLS_HOST
#error "Defina AGRISHIELD_MQTT_TLS_HOST no arquivo local mqtt_tls_config.h"
#endif
#ifndef AGRISHIELD_MQTT_TLS_PORT
#error "Defina AGRISHIELD_MQTT_TLS_PORT no arquivo local mqtt_tls_config.h"
#endif
#ifndef AGRISHIELD_MQTT_TLS_USERNAME
#error "Defina AGRISHIELD_MQTT_TLS_USERNAME no arquivo local mqtt_tls_config.h"
#endif
#ifndef AGRISHIELD_MQTT_TLS_PASSWORD
#error "Defina AGRISHIELD_MQTT_TLS_PASSWORD no arquivo local mqtt_tls_config.h"
#endif
#ifndef AGRISHIELD_MQTT_TLS_CA_CERT
#error "Defina AGRISHIELD_MQTT_TLS_CA_CERT no arquivo local mqtt_tls_config.h"
#endif
#endif

// ============================ Configuração ============================

#if defined(AGRISHIELD_MQTT_TLS)
static_assert(sizeof(AGRISHIELD_WIFI_SSID) > 1, "o SSID Wi-Fi nao pode ser vazio");
static_assert(sizeof(AGRISHIELD_WIFI_PASSWORD) > 1, "a senha Wi-Fi nao pode ser vazia");
const char* WIFI_SSID = AGRISHIELD_WIFI_SSID;
const char* WIFI_PASSWORD = AGRISHIELD_WIFI_PASSWORD;
#else
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
#endif
#if defined(AGRISHIELD_MQTT_TLS)
const int WIFI_CHANNEL = 0;  // hardware: varre os canais; nao presume o canal do Wokwi
#else
const int WIFI_CHANNEL = 6;  // canal fixo acelera a conexão no Wokwi
#endif

#if defined(AGRISHIELD_MQTT_TLS)
static_assert(sizeof(AGRISHIELD_MQTT_TLS_HOST) > 1, "o host MQTT TLS nao pode ser vazio");
static_assert(sizeof(AGRISHIELD_MQTT_TLS_USERNAME) > 1,
              "o usuario MQTT TLS nao pode ser vazio");
static_assert(sizeof(AGRISHIELD_MQTT_TLS_PASSWORD) > 1,
              "a senha MQTT TLS nao pode ser vazia");
static_assert(sizeof(AGRISHIELD_MQTT_TLS_CA_CERT) > 1,
              "o certificado CA MQTT TLS nao pode ser vazio");
static_assert(AGRISHIELD_MQTT_TLS_PORT > 0 && AGRISHIELD_MQTT_TLS_PORT <= 65535,
              "a porta MQTT TLS precisa estar entre 1 e 65535");
const char* MQTT_HOST = AGRISHIELD_MQTT_TLS_HOST;
constexpr uint16_t MQTT_PORT = AGRISHIELD_MQTT_TLS_PORT;
#else
const char* MQTT_HOST = "broker.hivemq.com";
constexpr uint16_t MQTT_PORT = 1883;
#endif
// Precisa ser igual a AGRISHIELD_MQTT_TOPIC_PREFIX da API.
const char* TOPIC_PREFIX = "agrishield/fiap-sompo-2026";
const char* DEVICE_ID = "tractor-01";

// NTP (E4): o contrato manda ts em epoch de segundos (UTC). Antes de sincronizar, vai ts = 0 e a
// API usa a hora em que recebeu a mensagem.
const char* NTP_SERVER = "pool.ntp.org";
const uint32_t NTP_MIN_VALID_EPOCH = 1704067200;  // 2024-01-01T00:00:00Z

// Pinos — iguais ao diagram.json. I2C do MPU6050: SDA = 21, SCL = 22.
const uint8_t PIN_LED_GREEN = 25;
const uint8_t PIN_LED_YELLOW = 26;
const uint8_t PIN_LED_RED = 27;
const uint8_t PIN_BUZZER = 14;
const uint8_t PIN_DHT = 4;
const uint8_t PIN_BUTTON = 33;  // botão de ocorrência (E8), INPUT_PULLUP contra o GND

const float DEFAULT_TILT_LIMIT_DEG = 15.0;  // usado até chegar o limite da API (E3)
const float DEFAULT_WARN_RATIO = 0.8;       // fração do limite que acende o amarelo (E2)
constexpr float ALERT_HYSTERESIS_DEG = 1.0f;  // evita o LED piscar em volta do limite (E2)

// Faixas aceitas no config recebido da API (E3). Fora delas o valor é ignorado e o atual continua.
constexpr float MIN_TILT_LIMIT_DEG = 3.0f;
constexpr float MAX_TILT_LIMIT_DEG = 45.0f;
constexpr float MIN_WARN_RATIO = 0.5f;
constexpr float MAX_WARN_RATIO = 0.95f;

// Por que existe um piso para o limite: computeLevel() só sai do 🔴 abaixo de (limite − histerese) e
// só sai do 🟡 abaixo de (warn_ratio × limite − histerese). Se qualquer um desses valores chegasse a
// zero, o nível travaria para sempre no 🔴 ou no 🟡. Os asserts abaixo travam o build se alguém
// afrouxar as faixas sem perceber isso.
static_assert(MIN_TILT_LIMIT_DEG > ALERT_HYSTERESIS_DEG,
              "limite minimo precisa ser maior que a histerese, senao o nivel trava no vermelho");
static_assert(MIN_WARN_RATIO * MIN_TILT_LIMIT_DEG > ALERT_HYSTERESIS_DEG,
              "warn_ratio minimo x limite minimo precisa ser maior que a histerese");

// Regra dos 30 (E6): três condições que, juntas, descrevem o risco de incêndio na máquina.
// O vento não é medido (não há anemômetro): vem do config, em configWindMaxKmh (E3).
constexpr float FIRE_TEMP_C = 30.0f;
constexpr float FIRE_HUMIDITY_PCT = 30.0f;
constexpr float FIRE_WIND_KMH = 30.0f;
// DHT22 falho (NaN): o último valor válido vale por mais 10 s; depois disso a telemetria vai null.
const unsigned long ENV_HOLD_MS = 10000;

const unsigned long IMU_INTERVAL_MS = 100;         // 10 Hz (E1)
const unsigned long DHT_INTERVAL_MS = 2000;        // intervalo mínimo do DHT22 (E6)
const unsigned long LOG_INTERVAL_MS = 1000;        // 1 Hz no Serial (E1)
const unsigned long TELEMETRY_INTERVAL_MS = 5000;  // contrato MQTT: telemetria a cada 5 s (E4)
const unsigned long MQTT_RETRY_INTERVAL_MS = 5000;
const unsigned long WIFI_RETRY_INTERVAL_MS = 5000;
// Só o setup() pode bloquear, e mesmo lá com limite: sem rede o alerta local ainda tem que subir.
const unsigned long WIFI_SETUP_TIMEOUT_MS = 20000;
// mqtt.connect() é síncrono: sem este teto o loop() pode ficar até 15 s parado com o broker lento.
const uint16_t MQTT_SOCKET_TIMEOUT_S = 2;
const int FILTER_WINDOW = 5;  // média móvel do IMU (E1)

/*
 * Display SSD1306 128x64 (E7), no mesmo barramento I2C do MPU6050 (0x3C e 0x68, sem conflito).
 * Uma atualização completa move 1 KB pelo I2C: ~23 ms a 400 kHz (seriam ~92 ms a 100 kHz), e isso
 * é tempo em que o loop() não anda. Por isso duas decisões:
 *   - o barramento inteiro vai a 400 kHz (o MPU6050 aceita, e as leituras dele também ficam mais
 *     rápidas): é o clkDuring/clkAfter do construtor;
 *   - drawScreen() só é chamada quando o conteúdo muda (conferido a 4 Hz por screenChanged), então
 *     a tela parada custa zero e o pior caso é 23 ms a cada 250 ms — ~9% do tempo, longe de
 *     atrapalhar o IMU a 10 Hz ou o LED de 5 Hz do capotamento.
 */
const uint8_t SCREEN_WIDTH = 128;
const uint8_t SCREEN_HEIGHT = 64;
const uint8_t SCREEN_I2C_ADDRESS = 0x3C;
const int8_t SCREEN_RESET_PIN = -1;  // o módulo do Wokwi não tem pino de reset
const uint32_t I2C_CLOCK_HZ = 400000;
const unsigned long SCREEN_INTERVAL_MS = 250;  // no máximo 4 atualizações por segundo

// Botão de ocorrência (E8).
const unsigned long BUTTON_DEBOUNCE_MS = 50;
const uint8_t BUTTON_CONFIRM_BEEPS = 3;      // confirmação ao operador: 3 bipes curtos
const unsigned long BUTTON_BEEP_ON_MS = 80;
const unsigned long BUTTON_BEEP_OFF_MS = 120;

// Buzzer (E2): bipe curto ao entrar no amarelo, intermitente a 2 Hz no vermelho.
const unsigned int BUZZER_FREQ_HZ = 2000;
const unsigned long BUZZER_YELLOW_BEEP_MS = 150;
const unsigned long BUZZER_RED_HALF_PERIOD_MS = 250;

// Capotamento (E5). Limiares e tempos vêm de feature/E5-deteccao-de-capotamento.md.
constexpr float ROLLOVER_TILT_DEG = 45.0f;          // inclinação que caracteriza capotamento
const unsigned long ROLLOVER_SUSTAIN_MS = 2000;     // tempo acima de 45°; pico mais curto não conta
constexpr float ROLLOVER_IMPACT_G = 2.5f;           // módulo da aceleração que dispara por impacto
constexpr float ROLLOVER_RECOVER_TILT_DEG = 20.0f;  // abaixo disso a situação começa a normalizar
const unsigned long ROLLOVER_RECOVER_MS = 10000;    // tempo abaixo de 20° para destravar o estado
const unsigned long ROLLOVER_BLINK_HALF_PERIOD_MS = 100;  // LED vermelho a 5 Hz (100 ms on/off)

// A saída precisa ser mais baixa que a entrada, senão o estado travado oscilaria na mesma leitura.
static_assert(ROLLOVER_RECOVER_TILT_DEG < ROLLOVER_TILT_DEG,
              "o limiar de saida do capotamento precisa ser menor que o de entrada");
// Acima de 45° o E2 já estaria no vermelho: é isso que permite adiar o tilt_alert sem perdê-lo.
static_assert(MAX_TILT_LIMIT_DEG <= ROLLOVER_TILT_DEG,
              "o limite do E2 nao pode passar do limiar de capotamento");

// Janela de contexto dos eventos rollover (E5) e incident_report (E8): 30 s a 1 Hz.
const uint8_t CONTEXT_SAMPLES = 30;
const unsigned long CONTEXT_INTERVAL_MS = 1000;

// Eventos (contrato MQTT): QoS 0, então cada evento vai 3 vezes com o mesmo event_id.
const uint8_t EVENT_SLOTS = 3;
const uint8_t EVENT_REPEATS = 3;
const unsigned long EVENT_REPEAT_INTERVAL_MS = 500;
const unsigned long EVENT_MAX_AGE_MS = 30000;  // evento velho demais não vale mais a pena publicar
// Buffer do PubSubClient: o evento rollover com 30 linhas de contexto dá ~1 KB (E5).
const uint16_t MQTT_BUFFER_SIZE = 2048;
const uint16_t MQTT_OVERHEAD_BYTES = 128;  // cabeçalho MQTT + tópico, descontados do buffer
const uint16_t MQTT_MAX_CONFIG_PAYLOAD_BYTES = MQTT_BUFFER_SIZE - MQTT_OVERHEAD_BYTES;
const size_t CONFIG_REASON_MAX_BYTES = 128;

// Preferences/NVS (E3). As chaves têm no máximo 15 caracteres.
const char* NVS_NAMESPACE = "agrishield";
const char* NVS_KEY_TILT_LIMIT = "tilt_limit";
const char* NVS_KEY_WARN_RATIO = "warn_ratio";

// ============================== Estado ================================

#if defined(AGRISHIELD_MQTT_TLS)
WiFiClientSecure wifiClient;
#else
WiFiClient wifiClient;
#endif
PubSubClient mqtt(wifiClient);
Adafruit_MPU6050 mpu;
// clkDuring e clkAfter iguais: o barramento fica em 400 kHz o tempo todo (o padrão da biblioteca
// devolveria 100 kHz depois de cada desenho, e quem pagaria isso seria o IMU).
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, SCREEN_RESET_PIN, I2C_CLOCK_HZ,
                         I2C_CLOCK_HZ);
DHT dht(PIN_DHT, DHT22);
Preferences prefs;

String topicTelemetry;
String topicEvents;
String topicConfig;
String topicStatus;
String statusOnline;
String statusOffline;

// Vem da API pelo tópico config (E3); até lá valem os padrões (ou o que estiver na NVS).
float tiltLimitDeg = DEFAULT_TILT_LIMIT_DEG;
float warnRatio = DEFAULT_WARN_RATIO;

// Campos do config que o firmware ainda não usa, guardados para as features seguintes (E3).
float configWindMaxKmh = NAN;  // E6, regra dos 30 local
String configSoilState;        // E7, display
String configRiskLevel;        // E7, display
String configReason;           // E7, display
uint32_t configValidUntil = 0;
bool configExpiredLogged = false;

enum AlertLevel { LEVEL_GREEN, LEVEL_YELLOW, LEVEL_RED, LEVEL_ROLLOVER };

struct ImuReading {
  float rollDeg;
  float pitchDeg;
  float accelG;
};

struct EnvReading {
  float tempC;
  float humidityPct;
};

/*
 * O que a tela mostra (E7). Guardar o modelo em vez de comparar a tela desenhada deixa a decisão
 * de redesenhar em uma função pura (screenChanged) e evita o custo de I2C quando nada mudou.
 * Os valores vêm em décimos, inteiros: comparar float com == é pedir para redesenhar à toa.
 */
struct ScreenModel {
  int16_t tiltDeci;
  int16_t limitDeci;
  uint8_t level;
  bool wifiOk;
  bool mqttOk;
  const char* soil;
};

// Botão de ocorrência (E8), com debounce na função pura debounceButton().
struct ButtonState {
  bool stableLow;            // nível já confirmado (true = pressionado)
  bool lastRawLow;           // última leitura crua, ainda sem confirmação
  unsigned long lastChangeMs;
};

struct ButtonDecision {
  ButtonState state;
  bool pressed;  // borda de descida confirmada — vale 1 evento
};

// Um canal do DHT22 (temperatura ou umidade) com a janela de tolerância a falhas (E6).
struct EnvHold {
  float value;              // último valor válido, ou NaN quando a tolerância expirou
  unsigned long updatedMs;  // millis() dessa última leitura válida
  bool valid;
};

/*
 * Amostra da janela de contexto (E5/E8). Guardamos o millis() da coleta em vez do t_s do contrato
 * porque t_s é relativo ao evento: só dá para calculá-lo na hora de montar o payload. A subtração
 * de unsigned long continua correta quando o millis() dá a volta.
 */
struct ContextSample {
  unsigned long capturedMs;
  float rollDeg;
  float pitchDeg;
  float accelG;
};

// Estado do detector de capotamento (E5), atualizado pela função pura evaluateRollover().
struct RolloverState {
  bool latched;                 // estado travado: LEVEL_ROLLOVER até a situação normalizar
  bool aboveTilt;               // acumulando tempo acima de ROLLOVER_TILT_DEG
  unsigned long aboveSinceMs;
  bool belowTilt;               // acumulando tempo abaixo de ROLLOVER_RECOVER_TILT_DEG
  unsigned long belowSinceMs;
};

// Saída de evaluateRollover(): o estado seguinte mais as transições desta amostra.
struct RolloverDecision {
  RolloverState state;
  bool entered;   // travou agora — vale 1 evento rollover
  bool exited;    // destravou agora — volta ao cálculo normal do E2
  bool byImpact;  // travou pelo impacto (accel_g), e não pela inclinação sustentada
};

// Evento aguardando as 3 publicações do contrato, sem bloquear o loop.
struct PendingEvent {
  bool active;
  uint8_t remaining;
  unsigned long nextAttemptMs;
  unsigned long createdMs;
  String eventId;
  String payload;
};

ImuReading imuSamples[FILTER_WINDOW];  // buffer circular da média móvel (E1)
uint8_t imuSampleIndex = 0;
uint8_t imuSampleCount = 0;

// envReading é o que vai para a telemetria e para o log: o último valor válido enquanto a
// tolerância de ENV_HOLD_MS durar, NaN (→ null) depois disso.
EnvReading envReading = {NAN, NAN};
EnvHold tempHold = {NAN, 0, false};
EnvHold humidityHold = {NAN, 0, false};
bool envFailLogged = false;
bool envEverValid = false;  // separa "ainda não leu" (normal no boot) de "parou de ler"

// Impacto (E5) é transitório: a média móvel de 0,5 s do E1 o diluiria, então a detecção usa a
// amostra crua, e a janela de contexto guarda o pico de cada segundo.
float lastRawAccelG = NAN;
float contextPeakAccelG = NAN;

AlertLevel alertLevel = LEVEL_GREEN;
AlertLevel appliedLevel = LEVEL_ROLLOVER;  // força applyOutputs a escrever na primeira chamada
bool buzzerOn = false;
unsigned long buzzerToggleMs = 0;
unsigned long buzzerBeepEndMs = 0;
bool ledRedOn = false;            // espelha o pino no piscar de 5 Hz do capotamento (E5)
unsigned long ledToggleMs = 0;

// Confirmação do botão de ocorrência (E8): enquanto dura, ela manda no buzzer e o som do nível
// fica suspenso; ao terminar, buzzerResyncPending faz applyOutputs restabelecer o som do nível.
bool confirmActive = false;
bool confirmBeepOn = false;
uint8_t confirmBeepsLeft = 0;
unsigned long confirmNextMs = 0;
bool buzzerResyncPending = false;

bool displayReady = false;  // E7: sem display o firmware segue normalmente
ScreenModel lastScreen = {-32768, -32768, 255, false, false, ""};
ButtonState buttonState = {false, false, 0};

// Janela de contexto (E5/E8): buffer circular de 30 s a 1 Hz.
ContextSample contextBuffer[CONTEXT_SAMPLES];
uint8_t contextWriteIndex = 0;  // próxima posição a escrever
uint8_t contextCount = 0;

RolloverState rolloverState = {false, false, 0, false, 0};
/*
 * tilt_alert (E2) segurado enquanto a inclinação está na faixa de capotamento (≥ 45°). Em até 2 s a
 * dúvida se resolve: ou o E5 trava o estado, e aí o evento rollover substitui o alerta de
 * inclinação (nada de dois eventos pela mesma inclinação), ou a inclinação cai sem capotar e o
 * tilt_alert daquela entrada no 🔴 é publicado normalmente.
 */
bool tiltAlertDeferred = false;

PendingEvent eventQueue[EVENT_SLOTS];
uint32_t eventCounter = 0;

uint32_t telemetrySeq = 0;  // contador do contrato; reinicia no boot e conta só o que foi publicado

bool wifiWasConnected = false;
bool ntpStarted = false;
bool ntpSynced = false;
// Config novo pede recálculo do nível, mas fora do callback do MQTT (ver onMqttMessage).
bool alertRecheckPending = false;

unsigned long lastImuReadMs = 0;
unsigned long lastDhtReadMs = 0;
unsigned long lastLogMs = 0;
unsigned long lastTelemetryMs = 0;
unsigned long lastContextSampleMs = 0;
unsigned long lastScreenMs = 0;
unsigned long lastMqttAttemptMs = 0;
unsigned long lastWifiAttemptMs = 0;

// ====================== Declarações antecipadas =======================
// O callback do MQTT fica na seção de conectividade, mas o tratamento do config depende de funções
// definidas mais abaixo (ordem de seções de document/padroes-de-codigo.md).

void applyConfig(const byte* payload, unsigned int length);
// readEnv() (sensores) usa a tolerância a falhas do DHT22, que é lógica pura e fica mais abaixo.
EnvHold updateEnvHold(const EnvHold& current, float reading, unsigned long nowMs);
uint32_t currentEpochSeconds();

// ============================ Conectividade ============================

void buildTopics() {
  String base = String(TOPIC_PREFIX) + "/devices/" + DEVICE_ID;
  topicTelemetry = base + "/telemetry";
  topicEvents = base + "/events";
  topicConfig = base + "/config";
  topicStatus = base + "/status";
  statusOnline = String("{\"device_id\":\"") + DEVICE_ID + "\",\"state\":\"online\"}";
  statusOffline = String("{\"device_id\":\"") + DEVICE_ID + "\",\"state\":\"offline\"}";
}

// Dispara a conexão e volta na hora: quem confirma é WiFi.status(), depois.
void startWifi() {
  Serial.printf("[wifi] conectando em %s...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, WIFI_CHANNEL);
  lastWifiAttemptMs = millis();
}

// SNTP em background (E4): configTime() só agenda a sincronização e volta na hora, sem bloquear.
void startNtp() {
  if (ntpStarted) {
    return;
  }
  ntpStarted = true;
  configTime(0, 0, NTP_SERVER);
  Serial.printf("[wifi] NTP %s iniciado; ate sincronizar a telemetria vai com ts=0\n", NTP_SERVER);
}

// Espera a primeira conexão. Só pode ser chamada pelo setup(), e desiste no timeout:
// sem rede o dispositivo continua o boot e o alerta local (E2) funciona mesmo assim.
void waitWifiOnBoot() {
  unsigned long startedMs = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startedMs < WIFI_SETUP_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    wifiWasConnected = true;
    Serial.printf("\n[wifi] conectado, IP %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[wifi] sem conexao no boot; o alerta local segue e o loop tenta de novo");
  }
}

// Reconexão dentro do loop(): temporizada com millis(), sem while e sem delay.
void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    if (!wifiWasConnected) {
      wifiWasConnected = true;
      Serial.printf("[wifi] reconectado, IP %s\n", WiFi.localIP().toString().c_str());
    }
    startNtp();  // no-op depois da primeira vez
    return;
  }
  if (wifiWasConnected) {
    wifiWasConnected = false;
    Serial.println("[wifi] conexao caiu; LEDs e buzzer continuam funcionando");
  }
  if (millis() - lastWifiAttemptMs >= WIFI_RETRY_INTERVAL_MS) {
    lastWifiAttemptMs = millis();
    Serial.println("[wifi] tentando reconectar...");
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD, WIFI_CHANNEL);
  }
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  if (topicConfig.equals(topic)) {
    applyConfig(payload, length);
    return;
  }
  Serial.printf("[mqtt] topico sem tratamento, ignorado: %s\n", topic);
}

void connectMqtt() {
#if defined(AGRISHIELD_MQTT_TLS)
  if (currentEpochSeconds() == 0) {
    Serial.println("[mqtt] TLS aguardando sincronizacao NTP para validar o certificado CA");
    return;
  }
#endif
  String clientId = String("agrishield-") + DEVICE_ID + "-" + String((uint32_t)esp_random(), HEX);
  Serial.printf("[mqtt] conectando em %s:%u...\n", MQTT_HOST, MQTT_PORT);

  // LWT: se o ESP32 cair, o broker publica "offline" no tópico de status.
#if defined(AGRISHIELD_MQTT_TLS)
  bool connected = mqtt.connect(clientId.c_str(), AGRISHIELD_MQTT_TLS_USERNAME,
                                AGRISHIELD_MQTT_TLS_PASSWORD, topicStatus.c_str(), 1, true,
                                statusOffline.c_str());
#else
  bool connected = mqtt.connect(clientId.c_str(), nullptr, nullptr, topicStatus.c_str(), 1, true,
                                statusOffline.c_str());
#endif
  if (!connected) {
    Serial.printf("[mqtt] falhou (state=%d), nova tentativa em %lus\n", mqtt.state(),
                  MQTT_RETRY_INTERVAL_MS / 1000);
    return;
  }

  mqtt.publish(topicStatus.c_str(), statusOnline.c_str(), true);
  // O config é retained: o limite do dia chega logo depois deste subscribe, mesmo após um reboot.
  mqtt.subscribe(topicConfig.c_str(), 1);
  Serial.printf("[mqtt] conectado; ouvindo %s\n", topicConfig.c_str());
}

void ensureConnected() {
  ensureWifi();
  if (WiFi.status() != WL_CONNECTED) {
    return;  // sem Wi-Fi não adianta tentar o broker
  }
  if (!mqtt.connected() && millis() - lastMqttAttemptMs >= MQTT_RETRY_INTERVAL_MS) {
    lastMqttAttemptMs = millis();
    connectMqtt();
  }
}

// =============================== Sensores ===============================

// Lê o acelerômetro e guarda a amostra no buffer da média móvel (E1).
void readImu() {
  sensors_event_t accel, gyro, temp;
  mpu.getEvent(&accel, &gyro, &temp);

  float ax = accel.acceleration.x / SENSORS_GRAVITY_STANDARD;
  float ay = accel.acceleration.y / SENSORS_GRAVITY_STANDARD;
  float az = accel.acceleration.z / SENSORS_GRAVITY_STANDARD;

  ImuReading sample;
  sample.rollDeg = atan2(ay, az) * RAD_TO_DEG;
  sample.pitchDeg = atan2(-ax, sqrt(ay * ay + az * az)) * RAD_TO_DEG;
  sample.accelG = sqrt(ax * ax + ay * ay + az * az);

  lastRawAccelG = sample.accelG;
  if (isnan(contextPeakAccelG) || sample.accelG > contextPeakAccelG) {
    contextPeakAccelG = sample.accelG;  // pico do segundo, para a janela de contexto (E5)
  }

  imuSamples[imuSampleIndex] = sample;
  imuSampleIndex = (imuSampleIndex + 1) % FILTER_WINDOW;
  if (imuSampleCount < FILTER_WINDOW) {
    imuSampleCount++;
  }
}

/*
 * DHT22 (E6), com temporização própria de 2 s no loop() — o mínimo do sensor, e sem atravessar o
 * IMU a 10 Hz. Cada canal passa pela tolerância do updateEnvHold(): uma falha isolada não apaga a
 * telemetria, mas depois de ENV_HOLD_MS sem leitura boa o campo vira null, como manda o contrato.
 */
void readEnv() {
  unsigned long now = millis();
  tempHold = updateEnvHold(tempHold, dht.readTemperature(), now);
  humidityHold = updateEnvHold(humidityHold, dht.readHumidity(), now);
  envReading.tempC = tempHold.value;
  envReading.humidityPct = humidityHold.value;

  bool missing = isnan(envReading.tempC) || isnan(envReading.humidityPct);
  if (missing && !envFailLogged) {
    envFailLogged = true;
    if (envEverValid) {
      Serial.printf("[env] DHT22 sem leitura ha mais de %lus; publicando null\n",
                    ENV_HOLD_MS / 1000);
    } else {
      // O DHT22 leva 1 a 2 s para responder depois do boot: aqui ainda não é falha.
      Serial.println("[env] DHT22 ainda sem leitura valida; publicando null");
    }
  } else if (!missing) {
    if (envFailLogged) {
      Serial.println(envEverValid ? "[env] DHT22 voltou a responder" : "[env] DHT22 respondendo");
    }
    envFailLogged = false;
    envEverValid = true;
  }
}

// Epoch em segundos (UTC), ou 0 enquanto o NTP não sincronizou — nesse caso o contrato manda o
// dispositivo enviar ts = 0 e a API usa a hora em que recebeu (E4).
uint32_t currentEpochSeconds() {
  time_t now = time(nullptr);
  return (now >= (time_t)NTP_MIN_VALID_EPOCH) ? (uint32_t)now : 0;
}

// ================================ Lógica ================================
// Funções puras (entrada → saída, sem I/O), para deixar a decisão revisável.

// Média das amostras do buffer circular; a ordem não importa para a média (E1).
ImuReading averageImu(const ImuReading* samples, uint8_t count) {
  ImuReading average = {0.0, 0.0, 0.0};
  if (count == 0) {
    return average;
  }
  for (uint8_t i = 0; i < count; i++) {
    average.rollDeg += samples[i].rollDeg;
    average.pitchDeg += samples[i].pitchDeg;
    average.accelG += samples[i].accelG;
  }
  average.rollDeg /= count;
  average.pitchDeg /= count;
  average.accelG /= count;
  return average;
}

// Grandeza comparada com o limite do dia (E1).
float tiltFromImu(const ImuReading& reading) {
  return fmaxf(fabsf(reading.rollDeg), fabsf(reading.pitchDeg));
}

// Arredondamento do payload (E4): 1 casa em graus, 2 em g. NaN continua NaN (vira null no JSON).
float roundTo(float value, uint8_t decimals) {
  if (isnan(value)) {
    return NAN;
  }
  float factor = powf(10.0f, (float)decimals);
  return roundf(value * factor) / factor;
}

// Tolerância a falhas do DHT22 (E6), função pura: leitura boa renova o valor; NaN mantém o último
// por até ENV_HOLD_MS e, passado esse tempo, apaga o valor (a telemetria publica null).
EnvHold updateEnvHold(const EnvHold& current, float reading, unsigned long nowMs) {
  if (!isnan(reading)) {
    EnvHold fresh = {reading, nowMs, true};
    return fresh;
  }
  if (current.valid && (nowMs - current.updatedMs) < ENV_HOLD_MS) {
    return current;  // falha isolada: o último valor válido continua valendo
  }
  EnvHold expired = {NAN, current.updatedMs, false};
  return expired;
}

/*
 * Regra dos 30 (E6), função pura: quantas das três condições de incêndio estão ativas.
 *   temp_c > 30 · humidity_pct < 30 · wind_max_kmh > 30
 * Um valor desconhecido (NaN) não conta como condição ativa — na dúvida, não inventa risco. O vento
 * vem do config da API (E3), porque a máquina não tem anemômetro.
 */
int fireConditions(float tempC, float humidityPct, float windMaxKmh) {
  int count = 0;
  if (!isnan(tempC) && tempC > FIRE_TEMP_C) {
    count++;
  }
  if (!isnan(humidityPct) && humidityPct < FIRE_HUMIDITY_PCT) {
    count++;
  }
  if (!isnan(windMaxKmh) && windMaxKmh > FIRE_WIND_KMH) {
    count++;
  }
  return count;
}

// Validação do config recebido da API (E3). Ver document/contrato-mqtt.md#config-w4--e3-retained.
bool isValidTiltLimit(float value) {
  return isfinite(value) && value >= MIN_TILT_LIMIT_DEG && value <= MAX_TILT_LIMIT_DEG;
}

bool isValidWarnRatio(float value) {
  return isfinite(value) && value >= MIN_WARN_RATIO && value <= MAX_WARN_RATIO;
}

bool isSafeConfigText(JsonVariant field, size_t maxBytes) {
  if (!field.is<const char*>()) {
    return false;
  }
  JsonString text = field.as<JsonString>();
  if (text.size() > maxBytes) {
    return false;
  }
  for (size_t i = 0; i < text.size(); i++) {
    uint8_t character = (uint8_t)text.c_str()[i];
    if (character < 0x20 || character == 0x7F) {
      return false;
    }
  }
  return true;
}

/*
 * Nível de alerta com histerese (E2). Com L = limitDeg, w = ratio e H = ALERT_HYSTERESIS_DEG:
 *   🔴 entra em tilt ≥ L      e só sai abaixo de L − H
 *   🟡 entra em tilt ≥ w·L    e só sai abaixo de w·L − H
 * As duas histereses se encadeiam: ao sair do 🔴 o nível cai para 🟡 enquanto tilt ≥ w·L − H, e só
 * vai direto para 🟢 abaixo disso (com L = 10 e w = 0,8: 8,9° → 🟡 e 6,9° → 🟢).
 * LEVEL_ROLLOVER (E5) tem prioridade e não é revertido por esta função.
 * Espera limitDeg > H e w·limitDeg > H: quem garante isso é isValidTiltLimit/isValidWarnRatio (E3),
 * junto com os static_assert das faixas lá em cima.
 */
AlertLevel computeLevel(float tiltDeg, AlertLevel current, float limitDeg, float ratio) {
  if (current == LEVEL_ROLLOVER) {
    return LEVEL_ROLLOVER;
  }
  float redEnter = limitDeg;
  float redExit = limitDeg - ALERT_HYSTERESIS_DEG;
  float warnEnter = ratio * limitDeg;
  float warnExit = warnEnter - ALERT_HYSTERESIS_DEG;

  if (tiltDeg >= redEnter) {
    return LEVEL_RED;
  }
  if (current == LEVEL_RED) {
    if (tiltDeg >= redExit) {
      return LEVEL_RED;  // ainda dentro da faixa de histerese do vermelho
    }
    return (tiltDeg >= warnExit) ? LEVEL_YELLOW : LEVEL_GREEN;
  }
  if (tiltDeg >= warnEnter) {
    return LEVEL_YELLOW;
  }
  if (current == LEVEL_YELLOW) {
    return (tiltDeg >= warnExit) ? LEVEL_YELLOW : LEVEL_GREEN;
  }
  return LEVEL_GREEN;
}

/*
 * Detector de capotamento (E5), função pura: recebe o estado atual, a amostra e o instante, e
 * devolve o estado seguinte com as transições. Regras de feature/E5-deteccao-de-capotamento.md:
 *   - trava com tiltDeg ≥ 45° por ≥ 2 s seguidos (um pico mais curto zera o cronômetro) OU com
 *     accel_g ≥ 2,5 (impacto, sem tempo mínimo);
 *   - destrava com tiltDeg < 20° por 10 s seguidos.
 * Toda comparação de tempo é feita por subtração de unsigned long, que continua correta no wrap do
 * millis(). Os cronômetros usam um flag em vez de "0 = parado": 0 é um valor legítimo de millis().
 */
RolloverDecision evaluateRollover(const RolloverState& current, float tiltDeg, float accelG,
                                  unsigned long nowMs) {
  RolloverDecision decision = {current, false, false, false};

  if (!current.latched) {
    if (tiltDeg >= ROLLOVER_TILT_DEG) {
      if (!decision.state.aboveTilt) {
        decision.state.aboveTilt = true;
        decision.state.aboveSinceMs = nowMs;
      }
    } else {
      decision.state.aboveTilt = false;  // pico curto: o cronômetro dos 2 s recomeça do zero
    }

    bool sustained =
        decision.state.aboveTilt && (nowMs - decision.state.aboveSinceMs) >= ROLLOVER_SUSTAIN_MS;
    bool impact = !isnan(accelG) && accelG >= ROLLOVER_IMPACT_G;
    if (sustained || impact) {
      decision.state.latched = true;
      decision.state.aboveTilt = false;
      decision.state.belowTilt = false;
      decision.entered = true;
      decision.byImpact = impact && !sustained;
    }
    return decision;
  }

  if (tiltDeg < ROLLOVER_RECOVER_TILT_DEG) {
    if (!decision.state.belowTilt) {
      decision.state.belowTilt = true;
      decision.state.belowSinceMs = nowMs;
    } else if ((nowMs - decision.state.belowSinceMs) >= ROLLOVER_RECOVER_MS) {
      decision.state.latched = false;
      decision.state.belowTilt = false;
      decision.exited = true;
    }
  } else {
    decision.state.belowTilt = false;  // voltou a subir: os 10 s de normalização recomeçam
  }
  return decision;
}

// t_s do contrato: tempo da amostra em relação ao evento, em segundos e sempre ≤ 0 (E5/E8).
// Subtração de unsigned long, correta no wrap do millis().
int32_t contextRelativeSeconds(unsigned long sampleMs, unsigned long eventMs) {
  return -(int32_t)((eventMs - sampleMs) / 1000UL);
}

/*
 * Debounce do botão de ocorrência (E8), função pura. O pino é INPUT_PULLUP contra o GND, então
 * pressionado = LOW. Uma mudança crua reinicia a contagem; só depois de BUTTON_DEBOUNCE_MS estável
 * o nível é confirmado, e apenas a borda de descida vale um evento — segurar o botão confirma uma
 * vez só, porque o nível estável já é "pressionado" nas voltas seguintes.
 * Subtração de unsigned long: correta no wrap do millis().
 */
ButtonDecision debounceButton(const ButtonState& current, bool rawLow, unsigned long nowMs) {
  ButtonDecision decision = {current, false};

  if (rawLow != current.lastRawLow) {
    decision.state.lastRawLow = rawLow;
    decision.state.lastChangeMs = nowMs;  // ainda tremendo: a contagem recomeça
    return decision;
  }
  if (rawLow == current.stableLow) {
    return decision;  // nada de novo desde a última confirmação
  }
  if ((nowMs - current.lastChangeMs) < BUTTON_DEBOUNCE_MS) {
    return decision;  // estável, mas ainda não por tempo suficiente
  }
  decision.state.stableLow = rawLow;
  decision.pressed = rawLow;  // só o apertar gera evento; o soltar apenas rearma
  return decision;
}

// Texto do estado do solo no display (E7). Sem acento: a fonte padrão da Adafruit GFX não tem.
const char* soilLabel(const char* soilState) {
  if (soilState == nullptr) {
    return "SOLO --";
  }
  if (strcmp(soilState, "dry") == 0) {
    return "SOLO SECO";
  }
  if (strcmp(soilState, "moist") == 0) {
    return "SOLO UMIDO";
  }
  if (strcmp(soilState, "saturated") == 0) {
    return "SOLO ENCHARCADO";
  }
  return "SOLO --";  // ausente ou desconhecido: melhor admitir do que chutar
}

// Palavra do nível no display (E7), sem acento. Diferente de levelName(), que é o contrato MQTT.
const char* levelLabel(AlertLevel level) {
  switch (level) {
    case LEVEL_GREEN:
      return "OK";
    case LEVEL_YELLOW:
      return "ATENCAO";
    case LEVEL_RED:
      return "PERIGO";
    case LEVEL_ROLLOVER:
      return "CAPOTAMENTO";
  }
  return "OK";
}

// Só redesenha o que mudou (E7): cada desenho custa ~23 ms de I2C. Função pura.
bool screenChanged(const ScreenModel& a, const ScreenModel& b) {
  return a.tiltDeci != b.tiltDeci || a.limitDeci != b.limitDeci || a.level != b.level ||
         a.wifiOk != b.wifiOk || a.mqttOk != b.mqttOk || strcmp(a.soil, b.soil) != 0;
}

// Nome do nível no contrato MQTT (campo alert_level) e nos logs.
const char* levelName(AlertLevel level) {
  switch (level) {
    case LEVEL_GREEN:
      return "green";
    case LEVEL_YELLOW:
      return "yellow";
    case LEVEL_RED:
      return "red";
    case LEVEL_ROLLOVER:
      return "rollover";
  }
  return "green";
}

// ============================ Saídas locais =============================

/*
 * LEDs (exatamente um aceso) e buzzer, temporizados com millis() — sem bloquear (E2, E5).
 *   🟢 LED verde, sem som
 *   🟡 LED amarelo + bipe curto de entrada
 *   🔴 LED vermelho fixo + buzzer intermitente a 2 Hz
 *   capotamento (E5): LED vermelho piscando a 5 Hz + buzzer contínuo — tem prioridade sobre o 🔴
 */
void applyOutputs(AlertLevel level) {
  unsigned long now = millis();
  bool levelChanged = (level != appliedLevel);

  if (levelChanged) {
    appliedLevel = level;
    bool red = (level == LEVEL_RED || level == LEVEL_ROLLOVER);
    digitalWrite(PIN_LED_GREEN, level == LEVEL_GREEN ? HIGH : LOW);
    digitalWrite(PIN_LED_YELLOW, level == LEVEL_YELLOW ? HIGH : LOW);
    digitalWrite(PIN_LED_RED, red ? HIGH : LOW);
    ledRedOn = red;
    ledToggleMs = now;
  }

  // O LED não é disputado por ninguém: o piscar do capotamento segue mesmo durante os bipes do E8.
  if (level == LEVEL_ROLLOVER && now - ledToggleMs >= ROLLOVER_BLINK_HALF_PERIOD_MS) {
    ledToggleMs = now;
    ledRedOn = !ledRedOn;
    digitalWrite(PIN_LED_RED, ledRedOn ? HIGH : LOW);
  }

  // Buzzer: um dono por vez. Enquanto a confirmação do botão (E8) toca, o som do nível fica
  // suspenso; quando ela acaba, buzzerResyncPending manda restabelecê-lo aqui.
  if (confirmActive) {
    return;
  }

  if (levelChanged || buzzerResyncPending) {
    bool resuming = !levelChanged;  // voltando dos bipes: nada de repetir o bipe de entrada do 🟡
    buzzerResyncPending = false;
    if (level == LEVEL_ROLLOVER) {
      tone(PIN_BUZZER, BUZZER_FREQ_HZ);  // contínuo enquanto o estado estiver travado
      buzzerOn = true;
    } else if (level == LEVEL_RED) {
      tone(PIN_BUZZER, BUZZER_FREQ_HZ);
      buzzerOn = true;
      buzzerToggleMs = now;
    } else if (level == LEVEL_YELLOW && !resuming) {
      tone(PIN_BUZZER, BUZZER_FREQ_HZ);  // bipe curto de entrada
      buzzerOn = true;
      buzzerBeepEndMs = now + BUZZER_YELLOW_BEEP_MS;
    } else {
      noTone(PIN_BUZZER);
      buzzerOn = false;
    }
  }

  if (level == LEVEL_RED) {
    // Intermitente a 2 Hz: 250 ms ligado, 250 ms desligado.
    if (now - buzzerToggleMs >= BUZZER_RED_HALF_PERIOD_MS) {
      buzzerToggleMs = now;
      buzzerOn = !buzzerOn;
      if (buzzerOn) {
        tone(PIN_BUZZER, BUZZER_FREQ_HZ);
      } else {
        noTone(PIN_BUZZER);
      }
    }
  } else if (level == LEVEL_YELLOW && buzzerOn && (long)(now - buzzerBeepEndMs) >= 0) {
    noTone(PIN_BUZZER);
    buzzerOn = false;
  }
}

// Inicia a confirmação do botão de ocorrência (E8): 3 bipes curtos, tocados por pumpConfirmBeep().
void startConfirmBeeps() {
  confirmActive = true;
  confirmBeepOn = false;
  confirmBeepsLeft = BUTTON_CONFIRM_BEEPS;
  confirmNextMs = millis();  // o primeiro bipe começa na volta seguinte do loop
}

// Toca os bipes da confirmação sem bloquear; chamada a cada volta do loop (E8).
void pumpConfirmBeep() {
  if (!confirmActive || (long)(millis() - confirmNextMs) < 0) {
    return;
  }
  unsigned long now = millis();
  if (confirmBeepOn) {
    noTone(PIN_BUZZER);
    confirmBeepOn = false;
    confirmBeepsLeft--;
    if (confirmBeepsLeft == 0) {
      confirmActive = false;
      buzzerResyncPending = true;  // applyOutputs devolve o buzzer ao nível atual
      return;
    }
    confirmNextMs = now + BUTTON_BEEP_OFF_MS;
    return;
  }
  tone(PIN_BUZZER, BUZZER_FREQ_HZ);
  confirmBeepOn = true;
  confirmNextMs = now + BUTTON_BEEP_ON_MS;
}

// ============================= Display (E7) =============================

// Liga o SSD1306. Sem display o firmware continua igual: LEDs, buzzer e MQTT não dependem dele.
void initDisplay() {
  displayReady = display.begin(SSD1306_SWITCHCAPVCC, SCREEN_I2C_ADDRESS);
  if (!displayReady) {
    Serial.printf("[oled] SSD1306 nao encontrado em 0x%02X; seguindo sem display\n",
                  SCREEN_I2C_ADDRESS);
    return;
  }
  display.cp437(true);  // sem isso o 0xF8 do grau vira outro glifo
  display.clearDisplay();
  display.display();
  Serial.printf("[oled] SSD1306 %ux%u em 0x%02X\n", SCREEN_WIDTH, SCREEN_HEIGHT,
                SCREEN_I2C_ADDRESS);
}

// Lê o estado atual e monta o que a tela deveria mostrar (E7).
ScreenModel buildScreenModel(const ImuReading& imu) {
  ScreenModel model;
  model.tiltDeci = (int16_t)lroundf(tiltFromImu(imu) * 10.0f);
  model.limitDeci = (int16_t)lroundf(tiltLimitDeg * 10.0f);
  model.level = (uint8_t)alertLevel;
  model.wifiOk = (WiFi.status() == WL_CONNECTED);
  model.mqttOk = mqtt.connected();
  model.soil = soilLabel(configSoilState.c_str());
  return model;
}

/*
 * Desenha a tela única do E7. Sem acentos (a fonte padrão da GFX não tem) e sem delay: o custo é o
 * display.display() no fim, ~23 ms de I2C a 400 kHz, e por isso só é chamada quando algo muda.
 *
 *   AgriShield                W M   ← W = Wi-Fi, M = MQTT ("-" quando fora)
 *   tractor-01
 *   ------------------------------
 *   12.4°                           ← inclinação atual, fonte grande
 *   Limite 10.0°
 *   SOLO ENCHARCADO
 *   PERIGO                          ← invertido no vermelho e no capotamento
 */
void drawScreen(const ScreenModel& model) {
  if (!displayReady) {
    return;
  }
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("AgriShield");
  display.setCursor(104, 0);
  display.print(model.wifiOk ? "W" : "-");
  display.setCursor(116, 0);
  display.print(model.mqttOk ? "M" : "-");
  display.setCursor(0, 10);
  display.print(DEVICE_ID);
  display.drawFastHLine(0, 20, SCREEN_WIDTH, SSD1306_WHITE);

  display.setTextSize(2);
  display.setCursor(0, 22);
  display.printf("%.1f", model.tiltDeci / 10.0f);
  display.write(0xF8);  // ° em cp437

  display.setTextSize(1);
  display.setCursor(0, 40);
  display.printf("Limite %.1f", model.limitDeci / 10.0f);
  display.write(0xF8);
  display.setCursor(0, 48);
  display.print(model.soil);

  const char* label = levelLabel((AlertLevel)model.level);
  bool inverted = (model.level == LEVEL_RED || model.level == LEVEL_ROLLOVER);
  if (inverted) {
    display.fillRect(0, 55, (int16_t)(6 * strlen(label) + 2), 9, SSD1306_WHITE);
    display.setTextColor(SSD1306_BLACK);
  }
  display.setCursor(1, 56);
  display.print(label);
  display.setTextColor(SSD1306_WHITE);

  display.display();
}

// Redesenha no máximo 4 vezes por segundo, e só quando o conteúdo muda (E7).
void updateScreen(const ImuReading& imu) {
  ScreenModel model = buildScreenModel(imu);
  if (!screenChanged(model, lastScreen)) {
    return;
  }
  lastScreen = model;
  drawScreen(model);
}

// =============================== Eventos ================================

/*
 * Guarda mais uma amostra da janela de contexto (E5/E8). Chamada a 1 Hz pelo loop; quando o buffer
 * enche, a posição mais antiga é sobrescrita — é um buffer circular de 30 s. roll e pitch vêm da
 * leitura filtrada (mesma base da telemetria); accel_g vem do pico do segundo, porque é o valor que
 * descreve um impacto no laudo do sinistro — uma média esconderia justamente o pico.
 */
void pushContextSample(const ImuReading& imu, unsigned long capturedMs) {
  contextBuffer[contextWriteIndex].capturedMs = capturedMs;
  contextBuffer[contextWriteIndex].rollDeg = imu.rollDeg;
  contextBuffer[contextWriteIndex].pitchDeg = imu.pitchDeg;
  contextBuffer[contextWriteIndex].accelG = imu.accelG;
  contextWriteIndex = (contextWriteIndex + 1) % CONTEXT_SAMPLES;
  if (contextCount < CONTEXT_SAMPLES) {
    contextCount++;
  }
}

// event_id = {device_id}-{ts ou millis}-{contador}, conforme o contrato MQTT.
String buildEventId(uint32_t ts) {
  uint32_t stamp = (ts != 0) ? ts : (uint32_t)(millis() / 1000);
  eventCounter++;
  return String(DEVICE_ID) + "-" + String(stamp) + "-" + String(eventCounter);
}

void clearEventSlot(PendingEvent& slot) {
  slot.active = false;
  slot.remaining = 0;
  slot.eventId = String();
  slot.payload = String();
}

// Coloca o evento na fila; pumpEvents() publica as 3 cópias sem travar o loop.
// Fila cheia: sai o mais antigo. Para o operador, o alerta recente é o que importa.
void enqueueEvent(const String& eventId, const String& payload) {
  uint8_t slot = EVENT_SLOTS;
  for (uint8_t i = 0; i < EVENT_SLOTS; i++) {
    if (!eventQueue[i].active) {
      slot = i;
      break;
    }
  }
  if (slot == EVENT_SLOTS) {
    slot = 0;
    for (uint8_t i = 1; i < EVENT_SLOTS; i++) {
      // Diferença com sinal: continua correta quando o millis() dá a volta.
      if ((long)(eventQueue[i].createdMs - eventQueue[slot].createdMs) < 0) {
        slot = i;
      }
    }
    Serial.printf("[event] fila cheia: %s substituido pelo mais recente %s\n",
                  eventQueue[slot].eventId.c_str(), eventId.c_str());
  }

  eventQueue[slot].active = true;
  eventQueue[slot].remaining = EVENT_REPEATS;
  eventQueue[slot].nextAttemptMs = millis();
  eventQueue[slot].createdMs = millis();
  eventQueue[slot].eventId = eventId;
  eventQueue[slot].payload = payload;
}

// Campos comuns a todo evento do contrato MQTT (sem context, que é só do E5/E8).
void fillEventBase(JsonDocument& doc, const char* type, const String& eventId, uint32_t ts,
                   const ImuReading& imu) {
  doc["device_id"] = DEVICE_ID;
  doc["event_id"] = eventId;
  doc["ts"] = ts;
  doc["type"] = type;
  doc["roll_deg"] = roundTo(imu.rollDeg, 1);
  doc["pitch_deg"] = roundTo(imu.pitchDeg, 1);
  doc["accel_g"] = roundTo(imu.accelG, 2);
  doc["tilt_limit_deg"] = roundTo(tiltLimitDeg, 1);
}

// Evento tilt_alert (E2): 1 por entrada no vermelho, sem context (contrato MQTT).
void queueTiltAlert(const ImuReading& imu) {
  uint32_t ts = currentEpochSeconds();
  String eventId = buildEventId(ts);

  JsonDocument doc;
  fillEventBase(doc, "tilt_alert", eventId, ts, imu);

  String payload;
  serializeJson(doc, payload);
  Serial.printf("[event] tilt_alert %s: %s\n", eventId.c_str(), payload.c_str());
  enqueueEvent(eventId, payload);
}

// Evento limit_applied (E3): o dispositivo confirma à API o limite que passou a valer.
void queueLimitApplied(const ImuReading& imu) {
  uint32_t ts = currentEpochSeconds();
  String eventId = buildEventId(ts);

  JsonDocument doc;
  fillEventBase(doc, "limit_applied", eventId, ts, imu);

  String payload;
  serializeJson(doc, payload);
  Serial.printf("[event] limit_applied %s: %s\n", eventId.c_str(), payload.c_str());
  enqueueEvent(eventId, payload);
}

/*
 * Monta o bloco context do contrato MQTT (E5/E8): fields fixo e rows do mais antigo para o mais
 * novo, com t_s relativo ao evento (≤ 0). A última linha é o próprio instante do evento (t_s = 0);
 * amostras com menos de 1 s são puladas, porque essa linha já as representa. Com o buffer cheio
 * saem 30 linhas (de -29 a 0). Devolve quantas linhas entraram.
 */
uint8_t fillEventContext(JsonDocument& doc, const ImuReading& imu, unsigned long eventMs) {
  JsonObject context = doc["context"].to<JsonObject>();
  JsonArray fields = context["fields"].to<JsonArray>();
  fields.add("t_s");
  fields.add("roll_deg");
  fields.add("pitch_deg");
  fields.add("accel_g");

  JsonArray rows = context["rows"].to<JsonArray>();
  uint8_t oldest = (contextWriteIndex + CONTEXT_SAMPLES - contextCount) % CONTEXT_SAMPLES;
  for (uint8_t i = 0; i < contextCount; i++) {
    const ContextSample& sample = contextBuffer[(oldest + i) % CONTEXT_SAMPLES];
    int32_t tS = contextRelativeSeconds(sample.capturedMs, eventMs);
    if (tS >= 0 || tS <= -(int32_t)CONTEXT_SAMPLES) {
      continue;  // já coberta pela linha do evento, ou fora da janela de 30 s
    }
    JsonArray row = rows.add<JsonArray>();
    row.add(tS);
    row.add(roundTo(sample.rollDeg, 1));
    row.add(roundTo(sample.pitchDeg, 1));
    row.add(roundTo(sample.accelG, 2));
  }

  JsonArray last = rows.add<JsonArray>();
  last.add(0);
  last.add(roundTo(imu.rollDeg, 1));
  last.add(roundTo(imu.pitchDeg, 1));
  last.add(roundTo(imu.accelG, 2));
  return rows.size();
}

/*
 * Enfileira um evento com janela de contexto: rollover (E5) e incident_report (E8) só diferem no
 * type e no texto do log. detail entra no log entre parênteses, ou "" quando não há o que dizer.
 */
void queueContextEvent(const char* type, const ImuReading& imu, const char* detail) {
  uint32_t ts = currentEpochSeconds();
  String eventId = buildEventId(ts);

  JsonDocument doc;
  fillEventBase(doc, type, eventId, ts, imu);
  uint8_t rows = fillEventContext(doc, imu, millis());

  String payload;
  serializeJson(doc, payload);
  Serial.printf("[event] %s %s%s: %u linhas de contexto, %u bytes\n", type, eventId.c_str(),
                detail, rows, payload.length());
  if (payload.length() + MQTT_OVERHEAD_BYTES > MQTT_BUFFER_SIZE) {
    // Não deve acontecer (o pior caso com 30 linhas fica bem abaixo), mas o PubSubClient descarta
    // em silêncio o que não cabe no buffer: melhor o Serial denunciar.
    Serial.printf("[event] ATENCAO: payload de %u bytes perto do buffer MQTT de %u\n",
                  payload.length(), MQTT_BUFFER_SIZE);
  }
  enqueueEvent(eventId, payload);
  // O payload inteiro é a evidência do contexto na demo. Vai por último, depois de o evento já
  // estar na fila: escrever ~1 KB no Serial a 115200 leva ~90 ms, e nada crítico espera por isso.
  Serial.printf("[event] %s\n", payload.c_str());
}

// Evento rollover (E5): 1 por entrada no estado travado, com os 30 s de contexto (contrato MQTT).
void queueRolloverEvent(const ImuReading& imu, bool byImpact) {
  queueContextEvent("rollover", imu, byImpact ? " por impacto" : " por inclinacao sustentada");
}

// Evento incident_report (E8): o operador registrou uma ocorrência, com o mesmo contexto de 30 s.
void queueIncidentReport(const ImuReading& imu) {
  queueContextEvent("incident_report", imu, " (botao do operador)");
}

// Publica as cópias pendentes quando chega a hora; chamada a cada volta do loop.
void pumpEvents() {
  unsigned long now = millis();
  for (uint8_t i = 0; i < EVENT_SLOTS; i++) {
    PendingEvent& pending = eventQueue[i];
    if (!pending.active || (long)(now - pending.nextAttemptMs) < 0) {
      continue;
    }
    if (now - pending.createdMs >= EVENT_MAX_AGE_MS) {
      // Depois de uma queda longa do MQTT, alerta velho não ajuda mais o operador.
      Serial.printf("[event] %s descartado: mais de %lus na fila\n", pending.eventId.c_str(),
                    EVENT_MAX_AGE_MS / 1000);
      clearEventSlot(pending);
      continue;
    }
    if (!mqtt.connected()) {
      continue;  // a fila espera a reconexão, respeitando o limite de idade acima
    }
    bool ok = mqtt.publish(topicEvents.c_str(), pending.payload.c_str());
    uint8_t attempt = EVENT_REPEATS - pending.remaining + 1;
    Serial.printf("[mqtt] evento %s enviado %u/%u %s\n", pending.eventId.c_str(), attempt,
                  EVENT_REPEATS, ok ? "ok" : "falhou");
    pending.remaining--;
    if (pending.remaining == 0) {
      clearEventSlot(pending);
    } else {
      pending.nextAttemptMs = now + EVENT_REPEAT_INTERVAL_MS;
    }
  }
}

// ====================== Config vindo da API (E3) ========================

// Grava o limite em vigor. Só é chamada quando algo muda, para não desgastar a flash com o config
// que a API reenvia de hora em hora (W4).
void persistConfig() {
  prefs.putFloat(NVS_KEY_TILT_LIMIT, tiltLimitDeg);
  prefs.putFloat(NVS_KEY_WARN_RATIO, warnRatio);
  Serial.printf("[config] salvo na NVS: limite %.1f°, warn_ratio %.2f\n", tiltLimitDeg, warnRatio);
}

// Recupera o último limite aplicado. No Wokwi a flash costuma zerar a cada simulação; lá quem
// garante o limite depois do reinício é a mensagem retained do config. A NVS vale no hardware real.
void loadConfigFromNvs() {
  prefs.begin(NVS_NAMESPACE, false);
  float storedLimit = prefs.getFloat(NVS_KEY_TILT_LIMIT, NAN);
  float storedRatio = prefs.getFloat(NVS_KEY_WARN_RATIO, NAN);

  if (isValidTiltLimit(storedLimit)) {
    tiltLimitDeg = storedLimit;
  }
  if (isValidWarnRatio(storedRatio)) {
    warnRatio = storedRatio;
  }
  if (isValidTiltLimit(storedLimit)) {
    Serial.printf("[config] restaurado da NVS: limite %.1f°, warn_ratio %.2f\n", tiltLimitDeg,
                  warnRatio);
  } else {
    Serial.printf("[config] nada na NVS; padrao limite %.1f°, warn_ratio %.2f (esperando o config "
                  "retained)\n",
                  tiltLimitDeg, warnRatio);
  }
}

// valid_until vencido não invalida o limite (contrato): o dispositivo segue com o último e avisa.
// Uma vez só por config recebido, para não poluir o Serial a cada segundo.
void checkConfigExpiry() {
  if (configValidUntil == 0 || configExpiredLogged) {
    return;
  }
  uint32_t now = currentEpochSeconds();
  if (now == 0 || now <= configValidUntil) {
    return;  // sem NTP sincronizado não dá para julgar o vencimento
  }
  configExpiredLogged = true;
  Serial.printf("[config] limite vencido (valid_until %lu); seguindo com %.1f°\n",
                (unsigned long)configValidUntil, tiltLimitDeg);
}

/*
 * Aplica o config recebido no tópico retained (E3).
 * - tilt_limit_deg é obrigatório e precisa estar em [3, 45]; caso contrário a mensagem inteira é
 *   ignorada.
 * - warn_ratio fora de [0,5, 0,95], ausente ou não numérico: mantém o atual.
 * - campos desconhecidos são ignorados; wind_max_kmh (E6) e soil_state/risk_level/reason (E7) ficam
 *   guardados para quando essas features chegarem.
 * - textos conhecidos e datas são validados antes de serem guardados; cada campo opcional inválido
 *   mantém o valor anterior.
 * Roda dentro do callback do PubSubClient, então não publica nada aqui: o evento limit_applied vai
 * para a fila e o recálculo do nível fica marcado em alertRecheckPending, ambos tratados no loop().
 */
void applyConfig(const byte* payload, unsigned int length) {
  if (length == 0) {
    // Payload vazio é como o broker limpa um retained; não é um config, então nada muda.
    Serial.printf("[config] retained limpo no broker; seguindo com %.1f°\n", tiltLimitDeg);
    return;
  }
  if (length > MQTT_MAX_CONFIG_PAYLOAD_BYTES) {
    Serial.printf("[config] payload excede %u bytes; mensagem ignorada\n",
                  MQTT_MAX_CONFIG_PAYLOAD_BYTES);
    return;
  }

  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, payload, length);
  if (error) {
    Serial.printf("[config] JSON invalido (%s); mensagem ignorada\n", error.c_str());
    return;
  }
  if (!doc.is<JsonObject>()) {
    Serial.println("[config] payload nao e um objeto JSON; mensagem ignorada");
    return;
  }

  JsonVariant limitField = doc["tilt_limit_deg"];
  if (!limitField.is<double>()) {
    Serial.println("[config] tilt_limit_deg obrigatorio e numerico; mensagem ignorada");
    return;
  }
  double parsedLimit = limitField.as<double>();
  if (!isfinite(parsedLimit) || parsedLimit < MIN_TILT_LIMIT_DEG ||
      parsedLimit > MAX_TILT_LIMIT_DEG) {
    Serial.printf("[config] limite invalido; esperado [%.1f°, %.1f°]; mensagem ignorada\n",
                  MIN_TILT_LIMIT_DEG, MAX_TILT_LIMIT_DEG);
    return;
  }

  float previousLimit = tiltLimitDeg;
  float previousRatio = warnRatio;
  tiltLimitDeg = (float)parsedLimit;

  JsonVariant ratioField = doc["warn_ratio"];
  if (!ratioField.isNull()) {
    double parsedRatio = ratioField.is<double>() ? ratioField.as<double>() : NAN;
    if (isfinite(parsedRatio) && isValidWarnRatio((float)parsedRatio)) {
      warnRatio = (float)parsedRatio;
    } else {
      Serial.printf("[config] warn_ratio invalido; mantendo %.2f\n", warnRatio);
    }
  }

  // Guardados para as features seguintes (ver tabela do contrato MQTT).
  JsonVariant windField = doc["wind_max_kmh"];
  if (!windField.isNull()) {
    double parsedWind = windField.is<double>() ? windField.as<double>() : NAN;
    if (isfinite(parsedWind) && parsedWind >= 0.0 && parsedWind <= FLT_MAX) {
      configWindMaxKmh = (float)parsedWind;
    } else {
      Serial.println("[config] wind_max_kmh invalido; mantendo o valor atual");
    }
  }
  JsonVariant soilField = doc["soil_state"];
  if (!soilField.isNull()) {
    if (soilField.is<const char*>() &&
        (soilField == "dry" || soilField == "moist" || soilField == "saturated")) {
      configSoilState = soilField.as<const char*>();
    } else {
      Serial.println("[config] soil_state invalido; mantendo o valor atual");
    }
  }
  JsonVariant riskField = doc["risk_level"];
  if (!riskField.isNull()) {
    if (riskField.is<const char*>() &&
        (riskField == "green" || riskField == "yellow" || riskField == "red")) {
      configRiskLevel = riskField.as<const char*>();
    } else {
      Serial.println("[config] risk_level invalido; mantendo o valor atual");
    }
  }
  JsonVariant reasonField = doc["reason"];
  if (!reasonField.isNull()) {
    if (isSafeConfigText(reasonField, CONFIG_REASON_MAX_BYTES)) {
      configReason = reasonField.as<const char*>();
    } else {
      Serial.println("[config] reason invalido ou maior que 128 bytes; mantendo o valor atual");
    }
  }
  // is<double>() aceita tanto 1789550000 quanto 1789550000.0, e o double guarda o epoch inteiro sem
  // perder precisão (um float perderia até ~2 min). Config sem valid_until não herda o vencimento
  // do config anterior: seria colar uma validade velha num limite novo.
  JsonVariant validUntilField = doc["valid_until"];
  if (validUntilField.isNull()) {
    configValidUntil = 0;
    configExpiredLogged = false;
  } else if (validUntilField.is<double>()) {
    double validUntil = validUntilField.as<double>();
    if (isfinite(validUntil) && validUntil >= 0.0 && validUntil <= 4294967295.0 &&
        floor(validUntil) == validUntil) {
      configValidUntil = (uint32_t)validUntil;
      configExpiredLogged = false;
    } else {
      Serial.println("[config] valid_until invalido; mantendo o valor atual");
    }
  } else {
    Serial.println("[config] valid_until invalido; mantendo o valor atual");
  }

  bool changed =
      fabsf(tiltLimitDeg - previousLimit) >= 0.05f || fabsf(warnRatio - previousRatio) >= 0.005f;
  if (!changed) {
    Serial.printf("[config] recebido sem mudanca (limite %.1f°, warn_ratio %.2f)\n", tiltLimitDeg,
                  warnRatio);
    checkConfigExpiry();
    return;
  }

  if (configReason.length() > 0) {
    Serial.printf("[config] limite %.1f° → %.1f° · warn_ratio %.2f (%s)\n", previousLimit,
                  tiltLimitDeg, warnRatio, configReason.c_str());
  } else {
    Serial.printf("[config] limite %.1f° → %.1f° · warn_ratio %.2f\n", previousLimit, tiltLimitDeg,
                  warnRatio);
  }
  persistConfig();
  queueLimitApplied(averageImu(imuSamples, imuSampleCount));
  alertRecheckPending = true;
  checkConfigExpiry();
}

// ============================ Telemetria (E4) ===========================

/*
 * Publica o estado atual no tópico telemetry (QoS 0, sem retained), no formato do contrato.
 * Sem buffer offline: com o MQTT fora a leitura se perde e só o próximo envio aparece — os alertas
 * locais (E2) continuam funcionando do mesmo jeito. seq conta apenas o que saiu, para a API
 * conseguir detectar perda de mensagem pela lacuna na sequência.
 */
void publishTelemetry(const ImuReading& imu) {
  lastTelemetryMs = millis();  // agenda o próximo envio mesmo quando não dá para publicar agora
  if (!mqtt.connected()) {
    return;
  }

  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  doc["ts"] = currentEpochSeconds();
  doc["seq"] = ++telemetrySeq;
  doc["roll_deg"] = roundTo(imu.rollDeg, 1);
  doc["pitch_deg"] = roundTo(imu.pitchDeg, 1);
  doc["accel_g"] = roundTo(imu.accelG, 2);
  if (isnan(envReading.tempC)) {
    doc["temp_c"] = nullptr;  // DHT22 falhou: o contrato pede null, não 0
  } else {
    doc["temp_c"] = roundTo(envReading.tempC, 1);
  }
  if (isnan(envReading.humidityPct)) {
    doc["humidity_pct"] = nullptr;
  } else {
    doc["humidity_pct"] = roundTo(envReading.humidityPct, 1);
  }
  // fire_conditions é opcional no contrato: sai quando dá para julgar pelo menos uma das três
  // condições. Sem DHT e sem vento no config não há o que afirmar, e o campo é omitido — 0 diria
  // "nenhuma condição ativa", que é uma afirmação diferente de "não sei".
  if (!isnan(envReading.tempC) || !isnan(envReading.humidityPct) || !isnan(configWindMaxKmh)) {
    doc["fire_conditions"] =
        fireConditions(envReading.tempC, envReading.humidityPct, configWindMaxKmh);
  }
  doc["tilt_limit_deg"] = roundTo(tiltLimitDeg, 1);
  doc["alert_level"] = levelName(alertLevel);

  String payload;
  serializeJson(doc, payload);
  bool ok = mqtt.publish(topicTelemetry.c_str(), payload.c_str());
  Serial.printf("[mqtt] telemetria seq=%lu (%u bytes) %s: %s\n", (unsigned long)telemetrySeq,
                payload.length(), ok ? "ok" : "falhou", payload.c_str());
}

// ========================= Alerta (E1 + E2 + E5) ========================

// Recalcula o nível a cada amostra do IMU e reage à mudança.
void updateAlert(const ImuReading& imu) {
  float tiltDeg = tiltFromImu(imu);
  AlertLevel next = computeLevel(tiltDeg, alertLevel, tiltLimitDeg, warnRatio);
  if (next == alertLevel) {
    return;
  }

  Serial.printf("[alert] %s → %s (tilt %.1f° / limite %.1f°)\n", levelName(alertLevel),
                levelName(next), tiltDeg, tiltLimitDeg);
  bool enteringRed = (next == LEVEL_RED);
  alertLevel = next;

  // E4: o painel ao vivo (W5) vê a mudança na hora, sem esperar a janela de 5 s.
  publishTelemetry(imu);

  if (!enteringRed) {
    return;
  }
  if (tiltDeg >= ROLLOVER_TILT_DEG) {
    // Já na faixa de capotamento: o tilt_alert espera o veredito do E5 (ver tiltAlertDeferred).
    tiltAlertDeferred = true;
    Serial.printf("[event] tilt_alert adiado: tilt %.1f° ja na faixa de capotamento\n", tiltDeg);
    return;
  }
  queueTiltAlert(imu);
}

/*
 * Detecção de capotamento (E5). Roda logo depois do updateAlert(), com a mesma leitura filtrada:
 *   - ao travar, o nível vira LEVEL_ROLLOVER (prioridade sobre o E2), sai telemetria na hora e o
 *     evento rollover com contexto vai para a fila das 3 publicações;
 *   - ao destravar, o nível é recalculado a partir do 🔴 (a hipótese conservadora) sem emitir um
 *     tilt_alert: o operador acabou de sair do alerta máximo, não faz sentido repetir o alarme. A
 *     próxima entrada no 🔴 vinda do 🟡/🟢 volta a publicar normalmente.
 * Enquanto o estado está travado, computeLevel() devolve LEVEL_ROLLOVER e o updateAlert() não
 * consegue mudar o nível — é o que garante 1 rollover por entrada e nenhum tilt_alert em paralelo.
 */
void updateRollover(const ImuReading& imu) {
  float tiltDeg = tiltFromImu(imu);
  // Inclinação vem da leitura filtrada (estável); impacto, da amostra crua (transitório).
  float impactG = isnan(lastRawAccelG) ? imu.accelG : lastRawAccelG;
  RolloverDecision decision = evaluateRollover(rolloverState, tiltDeg, impactG, millis());
  rolloverState = decision.state;

  if (decision.entered) {
    Serial.printf("[alert] CAPOTAMENTO por %s (tilt %.1f°, |a| %.2fg)\n",
                  decision.byImpact ? "impacto" : "inclinacao sustentada", tiltDeg, impactG);
    // No evento por impacto, accel_g leva o pico que disparou, não a média — é o dado do laudo.
    ImuReading eventReading = imu;
    if (decision.byImpact) {
      eventReading.accelG = impactG;
    }
    if (tiltAlertDeferred) {
      tiltAlertDeferred = false;
      Serial.println("[event] tilt_alert adiado descartado: o rollover cobre esta inclinacao");
    }
    alertLevel = LEVEL_ROLLOVER;
    applyOutputs(alertLevel);  // LED vermelho a 5 Hz + buzzer contínuo já nesta volta do loop
    publishTelemetry(imu);     // alert_level = "rollover" para o painel (W5)
    queueRolloverEvent(eventReading, decision.byImpact);
    return;
  }

  if (decision.exited) {
    AlertLevel next = computeLevel(tiltDeg, LEVEL_RED, tiltLimitDeg, warnRatio);
    Serial.printf("[alert] rollover → %s: tilt %.1f° abaixo de %.0f° por %lus\n", levelName(next),
                  tiltDeg, ROLLOVER_RECOVER_TILT_DEG, ROLLOVER_RECOVER_MS / 1000);
    alertLevel = next;
    applyOutputs(alertLevel);
    publishTelemetry(imu);
    return;
  }

  if (tiltAlertDeferred && !rolloverState.latched && tiltDeg < ROLLOVER_TILT_DEG) {
    // Saiu da faixa dos 45° sem capotar: o alerta do E2 daquela entrada no 🔴 continua valendo.
    tiltAlertDeferred = false;
    Serial.println("[event] tilt_alert adiado liberado: nao houve capotamento");
    queueTiltAlert(imu);
  }
}

/*
 * Botão de ocorrência (E8). Lido a cada volta do loop (digitalRead é barato), com o debounce na
 * função pura debounceButton(). Um toque = um incident_report com os 30 s de contexto do E5, mais
 * os 3 bipes de confirmação. Segurar não repete: só a borda de descida conta.
 */
void updateButton() {
  bool rawLow = (digitalRead(PIN_BUTTON) == LOW);
  ButtonDecision decision = debounceButton(buttonState, rawLow, millis());
  buttonState = decision.state;
  if (!decision.pressed) {
    return;
  }
  Serial.println("[event] botao de ocorrencia pressionado");
  queueIncidentReport(averageImu(imuSamples, imuSampleCount));
  startConfirmBeeps();
}

void printStatus(const ImuReading& imu) {
  float tiltDeg = tiltFromImu(imu);
  Serial.printf("[imu] roll=%.1f° pitch=%.1f° tilt=%.1f° |a|=%.2fg | nivel=%s limite=%.1f°\n",
                imu.rollDeg, imu.pitchDeg, tiltDeg, imu.accelG, levelName(alertLevel),
                tiltLimitDeg);
  // [env] 32.1°C 25% vento prev. 35 km/h → 3/3 condicoes (E6). Campo sem leitura sai como "--".
  char tempText[12];
  char humidityText[12];
  char windText[12];
  if (isnan(envReading.tempC)) {
    snprintf(tempText, sizeof(tempText), "--");
  } else {
    snprintf(tempText, sizeof(tempText), "%.1f°C", envReading.tempC);
  }
  if (isnan(envReading.humidityPct)) {
    snprintf(humidityText, sizeof(humidityText), "--");
  } else {
    snprintf(humidityText, sizeof(humidityText), "%.0f%%", envReading.humidityPct);
  }
  if (isnan(configWindMaxKmh)) {
    snprintf(windText, sizeof(windText), "--");
  } else {
    snprintf(windText, sizeof(windText), "%.0f km/h", configWindMaxKmh);
  }
  Serial.printf("[env] %s %s vento prev. %s → %d/3 condicoes\n", tempText, humidityText, windText,
                fireConditions(envReading.tempC, envReading.humidityPct, configWindMaxKmh));
}

// Avisa uma vez quando o relógio fica válido: a partir daí a telemetria sai com ts real (E4).
void reportNtpSync() {
  if (ntpSynced) {
    return;
  }
  uint32_t now = currentEpochSeconds();
  if (now == 0) {
    return;
  }
  ntpSynced = true;
  Serial.printf("[wifi] relogio sincronizado pelo NTP: ts=%lu\n", (unsigned long)now);
}

// ============================ Setup e loop =============================

void setup() {
  Serial.begin(115200);
  Serial.println("\n[boot] AgriShield — dispositivo " + String(DEVICE_ID));

  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);  // E8: o botão liga o pino ao GND quando pressionado

  if (!mpu.begin()) {
    Serial.println("[imu] MPU6050 não encontrado — verifique a fiação (SDA 21, SCL 22)");
    while (true) {
      delay(1000);
    }
  }
  // A biblioteca inicializa o acelerômetro em ±2 g, e nessa faixa a leitura satura em 2 g: o
  // impacto de 2,5 g do E5 nunca apareceria. Com ±8 g sobra margem para o impacto e a resolução
  // continua sobrando para a inclinação (~0,014° perto do plano).
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  dht.begin();
  initDisplay();          // E7: mesmo barramento I2C do MPU6050 (0x3C e 0x68)
  Wire.setClock(I2C_CLOCK_HZ);  // depois dos begin(), que podem reajustar o clock

  /*
   * Estado inicial do botão vindo do próprio pino (E8): se ele estiver pressionado no boot — preso,
   * ou alguém segurando durante a partida —, isso não vira ocorrência. Só um toque novo, depois de
   * soltar, gera evento. No boot não há nem contexto para anexar.
   */
  buttonState.lastRawLow = (digitalRead(PIN_BUTTON) == LOW);
  buttonState.stableLow = buttonState.lastRawLow;
  buttonState.lastChangeMs = millis();
  if (buttonState.stableLow) {
    Serial.println("[event] botao pressionado no boot; ignorado ate soltar");
  }

  loadConfigFromNvs();  // E3: limite salvo, ou o padrão até chegar o config retained
  buildTopics();        // antes dos sensores: o boot em 🔴 já pode enfileirar um evento

  // Sensores e saídas antes da rede: sem Wi-Fi o boot pode levar até WIFI_SETUP_TIMEOUT_MS, e o
  // operador precisa ver o LED do nível aceso nesse período, não três LEDs apagados.
  readImu();
  readEnv();
  ImuReading firstReading = averageImu(imuSamples, imuSampleCount);
  pushContextSample(firstReading, millis());  // primeira linha da janela de contexto (E5)
  contextPeakAccelG = NAN;
  alertLevel = computeLevel(tiltFromImu(firstReading), alertLevel, tiltLimitDeg, warnRatio);
  applyOutputs(alertLevel);
  Serial.printf("[alert] nivel inicial %s (tilt %.1f°, limite %.1f°, warn_ratio %.2f)\n",
                levelName(alertLevel), tiltFromImu(firstReading), tiltLimitDeg, warnRatio);

  if (alertLevel == LEVEL_RED) {
    // Máquina ligada já acima do limite. Como não houve transição, updateAlert() nunca dispararia o
    // evento dessa entrada no 🔴, e o contrato pede 1 tilt_alert por entrada. A fila segura o evento
    // até o MQTT conectar, dentro do limite de idade de EVENT_MAX_AGE_MS.
    if (tiltFromImu(firstReading) >= ROLLOVER_TILT_DEG) {
      /*
       * Máquina que liga já capotada (E5). O estado travado NÃO é assumido aqui: a regra é
       * temporal e o setup() não tem histórico para afirmar os 2 s. O primeiro updateRollover()
       * do loop() começa a contar na hora (ou trava imediatamente, se o impacto ≥ 2,5 g estiver
       * presente), então o rollover sai ~2 s depois do boot, com as poucas linhas de contexto que
       * existirem. Até lá o tilt_alert fica adiado, para não sair um evento por inclinação e outro
       * por capotamento pela mesma situação.
       */
      tiltAlertDeferred = true;
      Serial.println("[alert] boot na faixa de capotamento; tilt_alert adiado ate o veredito do E5");
    } else {
      Serial.println("[alert] boot ja acima do limite; enfileirando o tilt_alert da entrada");
      queueTiltAlert(firstReading);
    }
  }

#if defined(AGRISHIELD_MQTT_TLS)
  wifiClient.setCACert(AGRISHIELD_MQTT_TLS_CA_CERT);
  Serial.println("[mqtt] modo TLS: certificado CA validado e autenticacao habilitada");
#else
  Serial.println("[mqtt] modo DEMO: MQTT em texto claro, somente para desenvolvimento/Wokwi");
#endif
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(MQTT_BUFFER_SIZE);         // eventos com janela de contexto (E5/E8)
  mqtt.setSocketTimeout(MQTT_SOCKET_TIMEOUT_S); // teto do mqtt.connect(), que é síncrono
  mqtt.setCallback(onMqttMessage);

  startWifi();
  waitWifiOnBoot();
  if (WiFi.status() == WL_CONNECTED) {
    startNtp();
    connectMqtt();
  }

  unsigned long bootedMs = millis();
  lastMqttAttemptMs = bootedMs;
  lastTelemetryMs = bootedMs;
  lastImuReadMs = bootedMs;
  lastDhtReadMs = bootedMs;
  lastLogMs = bootedMs;
  lastContextSampleMs = bootedMs;
  lastScreenMs = bootedMs;
  updateScreen(averageImu(imuSamples, imuSampleCount));  // E7: a tela já sobe preenchida
}

void loop() {
  ensureConnected();
  mqtt.loop();
  pumpEvents();
  updateButton();     // E8: debounce por millis(), sem interrupção
  pumpConfirmBeep();  // E8: os 3 bipes de confirmação, sem bloquear

  if (alertRecheckPending) {
    // Limite novo do E3: recalcula o nível fora do callback do MQTT, na volta seguinte do loop.
    alertRecheckPending = false;
    updateAlert(averageImu(imuSamples, imuSampleCount));
  }

  if (millis() - lastImuReadMs >= IMU_INTERVAL_MS) {
    lastImuReadMs = millis();
    readImu();
    ImuReading reading = averageImu(imuSamples, imuSampleCount);
    updateAlert(reading);     // E1 + E2, com o nível travado enquanto o E5 estiver ativo
    updateRollover(reading);  // E5: inclinação sustentada, impacto e saída do estado
  }

  if (millis() - lastContextSampleMs >= CONTEXT_INTERVAL_MS) {
    // Janela de contexto do E5/E8: 1 Hz, sempre — inclusive durante o estado travado.
    lastContextSampleMs = millis();
    ImuReading contextSample = averageImu(imuSamples, imuSampleCount);
    if (!isnan(contextPeakAccelG)) {
      contextSample.accelG = contextPeakAccelG;  // pico do segundo; ver pushContextSample()
    }
    contextPeakAccelG = NAN;
    pushContextSample(contextSample, lastContextSampleMs);
  }

  if (millis() - lastDhtReadMs >= DHT_INTERVAL_MS) {
    lastDhtReadMs = millis();
    readEnv();
  }

  if (millis() - lastTelemetryMs >= TELEMETRY_INTERVAL_MS) {
    publishTelemetry(averageImu(imuSamples, imuSampleCount));  // reagenda lastTelemetryMs
  }

  if (millis() - lastLogMs >= LOG_INTERVAL_MS) {
    lastLogMs = millis();
    printStatus(averageImu(imuSamples, imuSampleCount));
    reportNtpSync();
    checkConfigExpiry();  // o vencimento só pode ser julgado depois que o NTP sincroniza
  }

  applyOutputs(alertLevel);  // buzzer intermitente precisa de atenção a cada volta

  if (millis() - lastScreenMs >= SCREEN_INTERVAL_MS) {
    // E7: por último, depois das saídas locais — é a tarefa mais cara do loop (~23 ms de I2C) e a
    // menos urgente. updateScreen só desenha quando o conteúdo muda.
    lastScreenMs = millis();
    updateScreen(averageImu(imuSamples, imuSampleCount));
  }
}

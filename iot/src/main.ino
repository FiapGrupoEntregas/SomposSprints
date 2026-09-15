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
 * Contrato MQTT: docs/contrato-mqtt.md
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <math.h>

// ============================ Configuração ============================

const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
const int WIFI_CHANNEL = 6;  // canal fixo acelera a conexão no Wokwi

const char* MQTT_HOST = "broker.hivemq.com";
const uint16_t MQTT_PORT = 1883;
// Precisa ser igual a AGRISHIELD_MQTT_TOPIC_PREFIX da API.
const char* TOPIC_PREFIX = "agrishield/fiap-sompo-2026";
const char* DEVICE_ID = "tractor-01";

// Pinos — iguais ao diagram.json. I2C do MPU6050: SDA = 21, SCL = 22.
const uint8_t PIN_LED_GREEN = 25;
const uint8_t PIN_LED_YELLOW = 26;
const uint8_t PIN_LED_RED = 27;
const uint8_t PIN_BUZZER = 14;
const uint8_t PIN_DHT = 4;

const float DEFAULT_TILT_LIMIT_DEG = 15.0;  // usado até chegar o limite da API (E3)
const unsigned long SENSOR_INTERVAL_MS = 1000;
const unsigned long MQTT_RETRY_INTERVAL_MS = 5000;

// ============================== Estado ================================

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
Adafruit_MPU6050 mpu;
DHT dht(PIN_DHT, DHT22);

String topicTelemetry;
String topicEvents;
String topicConfig;
String topicStatus;
String statusOnline;
String statusOffline;

float tiltLimitDeg = DEFAULT_TILT_LIMIT_DEG;
unsigned long lastSensorReadMs = 0;
unsigned long lastMqttAttemptMs = 0;

struct Reading {
  float rollDeg;
  float pitchDeg;
  float accelG;
  float tempC;
  float humidityPct;
};

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

void connectWifi() {
  Serial.printf("[wifi] conectando em %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, WIFI_CHANNEL);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.printf("\n[wifi] conectado, IP %s\n", WiFi.localIP().toString().c_str());
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  String message;
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.printf("[mqtt] recebido em %s: %s\n", topic, message.c_str());

  // TODO(E3): se topic == topicConfig, ler "tilt_limit_deg" com ArduinoJson,
  // atualizar tiltLimitDeg e salvar em Preferences (NVS).
}

void connectMqtt() {
  String clientId = String("agrishield-") + DEVICE_ID + "-" + String((uint32_t)esp_random(), HEX);
  Serial.printf("[mqtt] conectando em %s:%u...\n", MQTT_HOST, MQTT_PORT);

  // LWT: se o ESP32 cair, o broker publica "offline" no tópico de status.
  bool connected = mqtt.connect(clientId.c_str(), nullptr, nullptr, topicStatus.c_str(), 1, true,
                                statusOffline.c_str());
  if (!connected) {
    Serial.printf("[mqtt] falhou (state=%d), nova tentativa em %lus\n", mqtt.state(),
                  MQTT_RETRY_INTERVAL_MS / 1000);
    return;
  }

  mqtt.publish(topicStatus.c_str(), statusOnline.c_str(), true);
  mqtt.subscribe(topicConfig.c_str(), 1);
  Serial.printf("[mqtt] conectado; ouvindo %s\n", topicConfig.c_str());
}

void ensureConnected() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  if (!mqtt.connected() && millis() - lastMqttAttemptMs >= MQTT_RETRY_INTERVAL_MS) {
    lastMqttAttemptMs = millis();
    connectMqtt();
  }
}

// =============================== Sensores ===============================

Reading readSensors() {
  sensors_event_t accel, gyro, temp;
  mpu.getEvent(&accel, &gyro, &temp);

  float ax = accel.acceleration.x / SENSORS_GRAVITY_STANDARD;
  float ay = accel.acceleration.y / SENSORS_GRAVITY_STANDARD;
  float az = accel.acceleration.z / SENSORS_GRAVITY_STANDARD;

  Reading reading;
  reading.rollDeg = atan2(ay, az) * RAD_TO_DEG;
  reading.pitchDeg = atan2(-ax, sqrt(ay * ay + az * az)) * RAD_TO_DEG;
  reading.accelG = sqrt(ax * ax + ay * ay + az * az);
  reading.tempC = dht.readTemperature();
  reading.humidityPct = dht.readHumidity();
  return reading;
}

void printReading(const Reading& r) {
  Serial.printf("[imu] roll=%.1f° pitch=%.1f° |a|=%.2fg | limite=%.1f° | temp=%.1f°C umid=%.0f%%\n",
                r.rollDeg, r.pitchDeg, r.accelG, tiltLimitDeg, r.tempC, r.humidityPct);
}

// ============================ Setup e loop =============================

void setup() {
  Serial.begin(115200);
  Serial.println("\n[boot] AgriShield — dispositivo " + String(DEVICE_ID));

  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  if (!mpu.begin()) {
    Serial.println("[imu] MPU6050 não encontrado — verifique a fiação (SDA 21, SCL 22)");
    while (true) {
      delay(1000);
    }
  }
  dht.begin();

  buildTopics();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(2048);  // eventos com janela de contexto (E5/E8)
  mqtt.setCallback(onMqttMessage);

  connectWifi();
  connectMqtt();

  digitalWrite(PIN_LED_GREEN, HIGH);  // sistema no ar; E2 substitui pela lógica de alerta
}

void loop() {
  ensureConnected();
  mqtt.loop();

  if (millis() - lastSensorReadMs >= SENSOR_INTERVAL_MS) {
    lastSensorReadMs = millis();
    Reading reading = readSensors();
    printReading(reading);

    // TODO(E2): comparar max(|roll|, |pitch|) com tiltLimitDeg e acionar LEDs/buzzer.
    // TODO(E4): publicar telemetria em topicTelemetry a cada 5 s.
    // TODO(E5): detectar capotamento e publicar evento em topicEvents.
  }
}

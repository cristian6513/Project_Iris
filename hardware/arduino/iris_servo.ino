/*
 * IRIS Servo Controller — Arduino (Full Implementation)
 *
 * Protocol: JSON over Serial (9600 baud)
 *
 * Incoming commands:
 *   {"cmd":"servo","id":0,"angle":90}
 *   {"cmd":"servo","id":1,"angle":45,"speed":5}
 *   {"cmd":"led","pin":13,"state":1}
 *   {"cmd":"rgb","r":212,"g":160,"b":23}
 *   {"cmd":"ping"}
 *   {"cmd":"reset"}
 *
 * Responses:
 *   {"ok":true,"cmd":"servo","id":0,"angle":90}
 *   {"ok":true,"cmd":"ping","uptime":12345}
 *   {"error":"unknown_cmd"}
 *
 * Smooth servo movement with configurable speed.
 */

#include <Servo.h>

// ── Config ──
#define NUM_SERVOS     4
#define SERIAL_BAUD    9600
#define BUFFER_SIZE    256
#define LED_STATUS     13

// Servo definitions: pin, min_angle, max_angle, default_angle
struct ServoConfig {
    int pin;
    int minAngle;
    int maxAngle;
    int defaultAngle;
};

const ServoConfig SERVO_CFG[NUM_SERVOS] = {
    { 9,  0, 180, 90 },   // 0: Head pan
    { 10, 60, 120, 90 },   // 1: Head tilt
    { 11, 0, 180, 90 },   // 2: Left arm (future)
    { 6,  0, 180, 90 },   // 3: Right arm (future)
};

// ── State ──
Servo servos[NUM_SERVOS];
int   currentAngle[NUM_SERVOS];
int   targetAngle[NUM_SERVOS];
int   servoSpeed[NUM_SERVOS]; // degrees per update cycle

char  inputBuffer[BUFFER_SIZE];
int   bufferPos = 0;
unsigned long lastUpdate = 0;
const int UPDATE_INTERVAL_MS = 20; // 50 Hz servo update

// ── Setup ──
void setup() {
    Serial.begin(SERIAL_BAUD);
    pinMode(LED_STATUS, OUTPUT);

    for (int i = 0; i < NUM_SERVOS; i++) {
        servos[i].attach(SERVO_CFG[i].pin);
        servos[i].write(SERVO_CFG[i].defaultAngle);
        currentAngle[i] = SERVO_CFG[i].defaultAngle;
        targetAngle[i]  = SERVO_CFG[i].defaultAngle;
        servoSpeed[i]   = 3; // default speed
    }

    // Ready blink
    for (int i = 0; i < 3; i++) {
        digitalWrite(LED_STATUS, HIGH); delay(150);
        digitalWrite(LED_STATUS, LOW);  delay(150);
    }

    Serial.println("{\"status\":\"ready\",\"device\":\"iris_servo\",\"servos\":4}");
}

// ── Main Loop ──
void loop() {
    // Read serial
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (bufferPos > 0) {
                inputBuffer[bufferPos] = '\0';
                processCommand(inputBuffer);
                bufferPos = 0;
            }
        } else if (bufferPos < BUFFER_SIZE - 1) {
            inputBuffer[bufferPos++] = c;
        }
    }

    // Smooth servo update
    unsigned long now = millis();
    if (now - lastUpdate >= UPDATE_INTERVAL_MS) {
        lastUpdate = now;
        updateServos();
    }
}

// ── Smooth Servo Update ──
void updateServos() {
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (currentAngle[i] != targetAngle[i]) {
            int diff = targetAngle[i] - currentAngle[i];
            int step = servoSpeed[i];
            if (abs(diff) < step) {
                currentAngle[i] = targetAngle[i];
            } else if (diff > 0) {
                currentAngle[i] += step;
            } else {
                currentAngle[i] -= step;
            }
            servos[i].write(currentAngle[i]);
        }
    }
}

// ── Command Parser (minimal JSON) ──
void processCommand(const char* json) {
    // Extract "cmd" value
    char cmd[16] = "";
    extractString(json, "cmd", cmd, sizeof(cmd));

    if (strcmp(cmd, "servo") == 0) {
        int id    = extractInt(json, "id", -1);
        int angle = extractInt(json, "angle", -1);
        int speed = extractInt(json, "speed", -1);

        if (id < 0 || id >= NUM_SERVOS) {
            Serial.println("{\"error\":\"invalid_servo_id\"}");
            return;
        }
        if (angle < SERVO_CFG[id].minAngle || angle > SERVO_CFG[id].maxAngle) {
            angle = constrain(angle, SERVO_CFG[id].minAngle, SERVO_CFG[id].maxAngle);
        }

        targetAngle[id] = angle;
        if (speed > 0) servoSpeed[id] = speed;

        Serial.print("{\"ok\":true,\"cmd\":\"servo\",\"id\":");
        Serial.print(id);
        Serial.print(",\"angle\":");
        Serial.print(angle);
        Serial.println("}");

    } else if (strcmp(cmd, "led") == 0) {
        int pin   = extractInt(json, "pin", LED_STATUS);
        int state = extractInt(json, "state", 0);
        pinMode(pin, OUTPUT);
        digitalWrite(pin, state ? HIGH : LOW);
        Serial.println("{\"ok\":true,\"cmd\":\"led\"}");

    } else if (strcmp(cmd, "rgb") == 0) {
        int r = extractInt(json, "r", 0);
        int g = extractInt(json, "g", 0);
        int b = extractInt(json, "b", 0);
        // Assumes RGB LED on pins 3, 5, 6
        analogWrite(3, r);
        analogWrite(5, g);
        analogWrite(6, b);
        Serial.println("{\"ok\":true,\"cmd\":\"rgb\"}");

    } else if (strcmp(cmd, "ping") == 0) {
        Serial.print("{\"ok\":true,\"cmd\":\"ping\",\"uptime\":");
        Serial.print(millis());
        Serial.println("}");

    } else if (strcmp(cmd, "reset") == 0) {
        for (int i = 0; i < NUM_SERVOS; i++) {
            targetAngle[i] = SERVO_CFG[i].defaultAngle;
        }
        Serial.println("{\"ok\":true,\"cmd\":\"reset\"}");

    } else {
        Serial.print("{\"error\":\"unknown_cmd\",\"received\":\"");
        Serial.print(cmd);
        Serial.println("\"}");
    }
}

// ── Minimal JSON Helpers (no ArduinoJson dependency) ──

void extractString(const char* json, const char* key, char* out, int maxLen) {
    char search[32];
    snprintf(search, sizeof(search), "\"%s\":\"", key);
    const char* start = strstr(json, search);
    if (!start) return;
    start += strlen(search);
    const char* end = strchr(start, '"');
    if (!end) return;
    int len = min((int)(end - start), maxLen - 1);
    strncpy(out, start, len);
    out[len] = '\0';
}

int extractInt(const char* json, const char* key, int defaultVal) {
    char search[32];
    snprintf(search, sizeof(search), "\"%s\":", key);
    const char* start = strstr(json, search);
    if (!start) return defaultVal;
    start += strlen(search);
    // Skip whitespace
    while (*start == ' ') start++;
    return atoi(start);
}

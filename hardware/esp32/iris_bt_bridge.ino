/*
 * IRIS BT Bridge — ESP32 Bluetooth Sketch
 * 
 * Bridges Bluetooth Low Energy to Serial for wireless
 * communication between IRIS Core and hardware.
 * 
 * Status: Template — Future implementation (v0.5)
 */

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <ArduinoJson.h>

// BLE UUIDs
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"

BLEServer* pServer = NULL;
BLECharacteristic* pCharacteristic = NULL;
bool deviceConnected = false;

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
        deviceConnected = true;
        Serial.println("{\"ble\": \"connected\"}");
    }

    void onDisconnect(BLEServer* pServer) {
        deviceConnected = false;
        Serial.println("{\"ble\": \"disconnected\"}");
        pServer->startAdvertising();
    }
};

class CharCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic* pCharacteristic) {
        String value = pCharacteristic->getValue().c_str();
        if (value.length() > 0) {
            Serial.println(value);
        }
    }
};

void setup() {
    Serial.begin(115200);
    
    BLEDevice::init("IRIS-Hardware");
    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());
    
    BLEService* pService = pServer->createService(SERVICE_UUID);
    
    pCharacteristic = pService->createCharacteristic(
        CHARACTERISTIC_UUID,
        BLECharacteristic::PROPERTY_READ |
        BLECharacteristic::PROPERTY_WRITE |
        BLECharacteristic::PROPERTY_NOTIFY
    );
    
    pCharacteristic->addDescriptor(new BLE2902());
    pCharacteristic->setCallbacks(new CharCallbacks());
    
    pService->start();
    
    BLEAdvertising* pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(SERVICE_UUID);
    pAdvertising->start();
    
    Serial.println("{\"status\": \"ready\", \"device\": \"esp32_bt\"}");
}

void loop() {
    // Forward serial data to BLE
    if (Serial.available() && deviceConnected) {
        String data = Serial.readStringUntil('\n');
        pCharacteristic->setValue(data.c_str());
        pCharacteristic->notify();
    }
    
    delay(10);
}

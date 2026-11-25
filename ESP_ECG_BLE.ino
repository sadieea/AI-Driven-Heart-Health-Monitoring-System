#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

#define ECG_PIN 36

#define SERVICE_UUID        "12345678-1234-1234-1234-1234567890ab"
#define CHARACTERISTIC_UUID "abcd1234-5678-1234-5678-1234567890ab"

BLECharacteristic *pCharacteristic;
BLEServer *pServer = NULL; 

bool deviceConnected = false;
bool oldDeviceConnected = false;

#define SAMPLES_PER_PACKET 12
uint8_t buffer[SAMPLES_PER_PACKET * 2];
int indexBuffer = 0;

unsigned long lastSampleTime = 0;
const int sampleInterval = 4000; 

class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
      Serial.println(">> Client connected");
    };

    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
      Serial.println(">> Client disconnected");
    }
};

void setup() {
  Serial.begin(115200);

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  BLEDevice::init("ESP32_ECG_Monitor");
  BLEDevice::setMTU(100);

  pServer = BLEDevice::createServer(); 
  pServer->setCallbacks(new MyServerCallbacks());

  BLEService *pService = pServer->createService(SERVICE_UUID);
  pCharacteristic = pService->createCharacteristic(
                      CHARACTERISTIC_UUID,
                      BLECharacteristic::PROPERTY_NOTIFY
                    );
  pCharacteristic->addDescriptor(new BLE2902());

  pService->start();
  pServer->getAdvertising()->start();

  Serial.println("BLE Active. Waiting for connection...");
}

void loop() {
  if (!deviceConnected && oldDeviceConnected) {
      delay(200);
      pServer->getAdvertising()->start();
      Serial.println("Restarting advertising...");
      oldDeviceConnected = deviceConnected;
  }

  if (deviceConnected && !oldDeviceConnected) {
      oldDeviceConnected = deviceConnected;
  }

  if (micros() - lastSampleTime >= sampleInterval) {
    lastSampleTime = micros();
    uint16_t ecgValue = analogRead(ECG_PIN);
    

    Serial.println(ecgValue); 

    buffer[indexBuffer++] = ecgValue & 0xFF;
    buffer[indexBuffer++] = (ecgValue >> 8) & 0xFF;

    if (indexBuffer >= SAMPLES_PER_PACKET * 2) {
      if (deviceConnected) {
        pCharacteristic->setValue(buffer, sizeof(buffer));
        pCharacteristic->notify();
      }
      indexBuffer = 0;
    }
  }
}

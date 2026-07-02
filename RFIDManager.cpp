/*
 * RFIDManager.cpp
 */

#include "RFIDManager.h"

bool RFIDManager::begin() {
  // IMPORTANT: PN532 V3 module must be physically configured for SPI mode
  // via its onboard DIP switches before this will work. See README wiring
  // section. If SS/SCK/MISO/MOSI are wired correctly but the switches are
  // still set to I2C or HSU, getFirmwareVersion() below will return 0.
  SPI.begin(PN532_SCK, PN532_MISO, PN532_MOSI, PN532_SS);

  nfc_.begin();

  uint32_t versiondata = nfc_.getFirmwareVersion();
  if (!versiondata) {
    Serial.println("[RFID] PN532 not found. Check SPI wiring and DIP switches.");
    ready_ = false;
    return false;
  }

  Serial.print("[RFID] Found PN5");
  Serial.print((versiondata >> 24) & 0xFF, HEX);
  Serial.print(" firmware v");
  Serial.print((versiondata >> 16) & 0xFF, DEC);
  Serial.print('.');
  Serial.println((versiondata >> 8) & 0xFF, DEC);

  // Configure the max retries to 0xFF (infinite) internally is not desired;
  // we want SAMConfig for normal passive reads.
  nfc_.SAMConfig();

  ready_ = true;
  return true;
}

String RFIDManager::uidBytesToHex_(const uint8_t* uid, uint8_t len) {
  String out;
  out.reserve(len * 2);
  const char* hexChars = "0123456789ABCDEF";
  for (uint8_t i = 0; i < len; i++) {
    out += hexChars[(uid[i] >> 4) & 0x0F];
    out += hexChars[uid[i] & 0x0F];
  }
  return out;
}

bool RFIDManager::poll(String& uidOut) {
  if (!ready_) return false;

  uint8_t uid[7];
  uint8_t uidLength = 0;

  bool found = nfc_.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLength,
                                         RFID_POLL_TIMEOUT_MS);
  if (!found || uidLength == 0) return false;

  String hex = uidBytesToHex_(uid, uidLength);
  uint32_t now = millis();

  // Debounce: the same physical card held on the reader will be detected
  // repeatedly every poll cycle. Suppress repeats within CARD_COOLDOWN_MS.
  if (hex == lastUid_ && (now - lastDetectMs_) < CARD_COOLDOWN_MS) {
    return false;
  }

  lastUid_ = hex;
  lastDetectMs_ = now;
  uidOut = hex;
  return true;
}

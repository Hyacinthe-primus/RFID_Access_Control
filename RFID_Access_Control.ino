/*
 * RFID_Access_Control.ino
 *
 * ESP32-S3 standalone RFID access controller.
 * PN532 (hardware SPI) + 16x2 I2C LCD + LittleFS user database.
 * Serial link to a Python CLI for admin operations (add/remove/rename/
 * list/scan) -- the ESP32 works fully standalone with no PC attached.
 *
 * Board:   ESP32-S3 Dev Module (QFN56, 8MB Octal PSRAM, 40MHz XTAL)
 * Arduino IDE settings that matter for THIS project:
 *   Tools > USB CDC On Boot        : Disabled   (required for Serial JSON link)
 *   Tools > Flash Size             : 16MB
 *   Tools > Partition Scheme       : Custom  (uses partitions.csv next to this .ino)
 *   Tools > PSRAM                  : OPI PSRAM  (verify against your module)
 *
 * See README.md for full wiring + setup instructions.
 * Note: The amount of flash used is limited to 12 MB by the partition scheme
 */

#include "SystemController.h"

SystemController controller;

void setup() {
  controller.begin();
}

void loop() {
  controller.update();
  // Intentionally no delay() here. RFID_POLL_TIMEOUT_MS inside RFIDManager
  // already caps how long a single loop() iteration can take, which keeps
  // the serial link responsive.
}

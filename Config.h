#pragma once
/*
 * Config.h
 * Central hardware pin map and system constants.
 *
 * Board tested against: ESP32-S3 (QFN56, rev v0.2), 8MB Octal PSRAM, 40MHz XTAL.
 * If you are on a different S3 module, re-check PSRAM mode in
 * Tools > PSRAM before assuming these pins are safe -- some dev boards
 * route PSRAM over pins that overlap with GPIO 33-37 / 26-32. The pins
 * below (8,9,10,11,12,13) are free on essentially every S3 devkit variant,
 * but "essentially every" is not "every."
 */

#include <Arduino.h>

// Using ESP32-S3's default FSPI pins.
#define PN532_SCK   12
#define PN532_MISO  13
#define PN532_MOSI  11
#define PN532_SS    10

// I2C (16x2 LCD)
#define LCD_SDA     8
#define LCD_SCL     9
#define LCD_I2C_ADDR 0x27      // Common default; 0x3F is the other common one.
#define LCD_COLS    16
#define LCD_ROWS    2

// Filesystem
#define USERS_DB_PATH      "/users.json"
#define USERS_DB_TMP_PATH  "/users.tmp"
#define DB_JSON_CAPACITY   16384   // bytes reserved for the ArduinoJson document

// Timing (all non-blocking, millis()-based) 
#define BOOT_SCREEN_MIN_MS     2500
#define RESULT_DISPLAY_MS      2000
#define UID_DISPLAY_MS         2000
#define CARD_COOLDOWN_MS       2500
#define RFID_POLL_TIMEOUT_MS   50
#define SERIAL_BAUD             115200

// Validation
#define MAX_NAME_LEN   48
#define MIN_UID_HEX_LEN 8      // 4 bytes minimum (MIFARE Classic UID)
#define MAX_UID_HEX_LEN 20     // 10 bytes maximum (double UID)

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

// Timing (all non-blocking, millis()-based) 
#define BOOT_SCREEN_MIN_MS     2500
#define RESULT_DISPLAY_MS      2000
#define UID_DISPLAY_MS         2000
#define CARD_COOLDOWN_MS       2500
#define RFID_POLL_TIMEOUT_MS   50
#define SERIAL_BAUD             921600

// Validation
#define MAX_NAME_LEN   48
#define MAX_USERS      10000   // PSRAM-backed; SRAM-only limit was ~2000
#define MIN_UID_HEX_LEN 8      // 4 bytes minimum (MIFARE Classic UID)
#define MAX_UID_HEX_LEN 20     // 10 bytes maximum (double UID)
#define REGISTERED_DATE_LEN 10 // strlen("YYYY-MM-DD")

// Wi-Fi / NTP time sync
// Credentials are never hardcoded here -- they are provisioned at runtime
// via the 'configure_wifi' serial command and persisted in NVS (Preferences).
#define WIFI_CONNECT_TIMEOUT_MS   10000   // how long begin()/configure will block waiting to associate
#define NTP_SERVER_1              "pool.ntp.org"
#define NTP_SERVER_2              "time.nist.gov"
// The Python CLI stamps 'registered' using the LOCAL calendar date of the
// machine running the CLI. Set these two offsets to that same timezone so
// the firmware's expiration math (Config.h-adjacent, see
// SystemController::isUserExpired_) lines up with it. Default is UTC+0
// change to your timezone, e.g. UTC+2 => NTP_GMT_OFFSET_SEC = 7200.
#define NTP_GMT_OFFSET_SEC        0   // UTC+0
#define NTP_DAYLIGHT_OFFSET_SEC   0
#define NTP_SYNC_TIMEOUT_MS       8000    // how long to wait for time() to become sane
#define NTP_RESYNC_INTERVAL_MS    (6UL * 60UL * 60UL * 1000UL)  // re-sync every 6h to correct drift

// Buzzer (passive, driven via ledc/tone -- never a delay()-blocking tone)
#define BUZZER_PIN   6

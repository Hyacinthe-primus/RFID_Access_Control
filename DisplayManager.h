#pragma once
/*
 * DisplayManager.h
 * Owns the LiquidCrystal_I2C instance. Single responsibility: rendering
 * named screen states. Nothing here decides *when* to show a screen --
 * that's SystemController's job.
 */

#include <Arduino.h>
#include <LiquidCrystal_I2C.h>
#include "Config.h"

class DisplayManager {
public:
  void begin();

  void showBoot();
  void showIdle();
  void showAccessGranted(const String& name);
  void showAccessDenied(const String& reason = "Unknown Card");
  void showUid(const String& uid);
  void showDatabaseUpdating();
  void showSavingDatabase();
  void showLoadingDatabase();
  void showScanMode();
  void showSerialConnected();
  void showSerialDisconnected();
  void showWifiConnecting();
  void showWifiResult(bool connected);
  void showError(const String& message);
  void showRenewingTag();

private:
  LiquidCrystal_I2C lcd_{LCD_I2C_ADDR, LCD_COLS, LCD_ROWS};
  void printTwoLines_(const String& line1, const String& line2);
  static String truncate_(const String& s, uint8_t maxLen);
};

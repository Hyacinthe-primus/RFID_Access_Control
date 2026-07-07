#pragma once
// Class renamed WifiTimeManager to avoid collision with
// ESP32 Core v3.x Network library's own NetworkManager class.

#include <Arduino.h>
#include <time.h>

class WifiTimeManager {
public:
  void begin();
  void update();
  bool configure(const String& ssid, const String& password);
  bool isWifiConnected() const;
  bool isTimeSynced() const { return timeSynced_; }
  time_t nowUtc() const;
  bool resyncTime();

  // --- Timezone (persisted in NVS, defaults to Config.h's
  // NTP_GMT_OFFSET_SEC/NTP_DAYLIGHT_OFFSET_SEC on first boot) ---
  // Sets the offsets, persists them, and re-applies them immediately via
  // configTime() + a resync so the change takes effect without a reboot.
  // Returns false (offsets NOT persisted) if the resync fails, so a typo
  // that breaks NTP doesn't get silently saved.
  bool setTimezone(long gmtOffsetSec, int daylightOffsetSec);
  long gmtOffsetSec() const { return gmtOffsetSec_; }
  int daylightOffsetSec() const { return daylightOffsetSec_; }

  // Network status getters, used by the 'net_status' serial command.
  // All of these are read-only snapshots of the current WiFi state; they
  // never block or initiate a reconnect. SSID/IP return empty strings when
  // not connected, and RSSI returns 0 when not connected (WiFi.RSSI()
  // itself returns 0 in that case on most ESP32 cores).
  String currentSsid() const;
  String currentIp() const;
  int32_t currentRssi() const;

private:
  bool timeSynced_ = false;
  uint32_t lastSyncAttemptMs_ = 0;
  long gmtOffsetSec_ = 0;
  int daylightOffsetSec_ = 0;
  bool connectBlocking_(const String& ssid, const String& password, uint32_t timeoutMs);
  bool syncTimeBlocking_(uint32_t timeoutMs);
  void loadCredentials_(String& ssidOut, String& passOut);
  void saveCredentials_(const String& ssid, const String& password);
  void loadTimezone_();  // NVS -> gmtOffsetSec_/daylightOffsetSec_, defaults from Config.h if unset
  void saveTimezone_(long gmtOffsetSec, int daylightOffsetSec);
};

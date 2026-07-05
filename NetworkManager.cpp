#include "NetworkManager.h"
#include "Config.h"
#include <WiFi.h>
#include <Preferences.h>

namespace {
  const char* kPrefsNamespace = "wifi";
  const char* kKeySsid = "ssid";
  const char* kKeyPass = "pass";
}

void WifiTimeManager::loadCredentials_(String& ssidOut, String& passOut) {
  Preferences prefs;
  prefs.begin(kPrefsNamespace, true);
  ssidOut = prefs.getString(kKeySsid, "");
  passOut = prefs.getString(kKeyPass, "");
  prefs.end();
}

void WifiTimeManager::saveCredentials_(const String& ssid, const String& password) {
  Preferences prefs;
  prefs.begin(kPrefsNamespace, false);
  prefs.putString(kKeySsid, ssid);
  prefs.putString(kKeyPass, password);
  prefs.end();
}

bool WifiTimeManager::connectBlocking_(const String& ssid, const String& password, uint32_t timeoutMs) {
  if (ssid.length() == 0) return false;
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid.c_str(), password.c_str());
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - start) < timeoutMs) {
    delay(100);
  }
  return WiFi.status() == WL_CONNECTED;
}

bool WifiTimeManager::syncTimeBlocking_(uint32_t timeoutMs) {
  configTime(NTP_GMT_OFFSET_SEC, NTP_DAYLIGHT_OFFSET_SEC, NTP_SERVER_1, NTP_SERVER_2);
  uint32_t start = millis();
  time_t now = time(nullptr);
  while (now < 1600000000 && (millis() - start) < timeoutMs) {
    delay(100);
    now = time(nullptr);
  }
  lastSyncAttemptMs_ = millis();
  timeSynced_ = (now >= 1600000000);
  return timeSynced_;
}

void WifiTimeManager::begin() {
  String ssid, password;
  loadCredentials_(ssid, password);
  if (ssid.length() == 0) {
    return;
  }
  if (connectBlocking_(ssid, password, WIFI_CONNECT_TIMEOUT_MS)) {
    syncTimeBlocking_(NTP_SYNC_TIMEOUT_MS);
  }
}

bool WifiTimeManager::configure(const String& ssid, const String& password) {
  saveCredentials_(ssid, password);
  bool connected = connectBlocking_(ssid, password, WIFI_CONNECT_TIMEOUT_MS);
  if (connected) { syncTimeBlocking_(NTP_SYNC_TIMEOUT_MS); }
  return connected;
}

void WifiTimeManager::update() {
  if (!isWifiConnected()) return;
  if (millis() - lastSyncAttemptMs_ < NTP_RESYNC_INTERVAL_MS) return;
  syncTimeBlocking_(NTP_SYNC_TIMEOUT_MS);
}

bool WifiTimeManager::isWifiConnected() const {
  return WiFi.status() == WL_CONNECTED;
}

time_t WifiTimeManager::nowUtc() const {
  if (!timeSynced_) return 0;
  return time(nullptr);
}

String WifiTimeManager::currentSsid() const {
  // WiFi.SSID() returns "" if not connected. Cheap, non-blocking.
  if (!isWifiConnected()) return String("");
  return WiFi.SSID();
}

String WifiTimeManager::currentIp() const {
  if (!isWifiConnected()) return String("");
  return WiFi.localIP().toString();
}

int32_t WifiTimeManager::currentRssi() const {
  if (!isWifiConnected()) return 0;
  return WiFi.RSSI();
}

bool WifiTimeManager::resyncTime() {
  if (!isWifiConnected()) return false;
  return syncTimeBlocking_(NTP_SYNC_TIMEOUT_MS);
}
/*
 * SerialProtocol.cpp
 */

#include "SerialProtocol.h"
#include "DatabaseManager.h"
#include "NetworkManager.h"
#include "Config.h"

void SerialProtocol::begin(SerialMessageHandler handler) {
  handler_ = handler;
  lineLen_ = 0;
}

void SerialProtocol::poll() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n') {
      lineBuf_[lineLen_] = '\0';
      if (lineLen_ > 0) {
        handleLine_(lineBuf_);
      }
      lineLen_ = 0;
      continue;
    }
    if (c == '\r') continue; // tolerate CRLF

    if (lineLen_ < kLineBufCapacity - 1) {
      lineBuf_[lineLen_++] = c;
    } else {
      // Line too long -- drop it to avoid a stuck buffer / memory issue.
      lineLen_ = 0;
      sendError("Message too long");
    }
  }
}

void SerialProtocol::handleLine_(const char* line) {
  // Matches kLineBufCapacity -- see the comment there ('remove_all_except'
  // is the message that needs the extra headroom).
  DynamicJsonDocument doc(kLineBufCapacity);
  DeserializationError err = deserializeJson(doc, line);

  if (err) {
    sendError("Malformed JSON");
    return;
  }
  if (!doc.containsKey("type")) {
    sendError("Missing 'type' field");
    return;
  }

  if (handler_) handler_(doc);
}

void SerialProtocol::sendOk() {
  Serial.println("{\"status\":\"ok\"}");
}

void SerialProtocol::sendError(const String& message) {
  DynamicJsonDocument doc(256);
  doc["status"] = "error";
  doc["message"] = message;
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendUidDetected(const String& uid) {
  DynamicJsonDocument doc(256);
  doc["status"] = "ok";
  doc["type"] = "uid_detected";
  doc["uid"] = uid;
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendRemovedCount(size_t removedCount) {
  DynamicJsonDocument doc(256);
  doc["status"] = "ok";
  doc["type"] = "remove_all_except";
  doc["removed_count"] = removedCount;
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendWifiResult(bool connected, const String& message) {
  DynamicJsonDocument doc(256);
  doc["status"] = connected ? "ok" : "error";
  doc["type"] = "wifi_status";
  doc["connected"] = connected;
  doc["message"] = message;
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendNtpSyncResult(bool synced, const String& message) {
  DynamicJsonDocument doc(256);
  doc["status"] = synced ? "ok" : "error";
  doc["type"] = "ntp_sync";
  doc["synced"] = synced;
  doc["message"] = message;
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendStatus(DatabaseManager& db) {
  DynamicJsonDocument doc(512);
  doc["status"] = "ok";
  doc["type"] = "status";
  doc["db_path"] = db.dbPath();
  size_t total = db.fsTotalBytes();
  size_t used = db.fsUsedBytes();
  doc["fs_total_bytes"] = total;
  doc["fs_used_bytes"] = used;
  doc["fs_free_bytes"] = (total >= used) ? (total - used) : 0;
  doc["user_count"] = db.userCount();
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendUserList(DatabaseManager& db) {
  // Stream the response directly to Serial, one user at a time.
  // Avoids a single huge DynamicJsonDocument (~500KB for 4500 users).
  Serial.print("{\"status\":\"ok\",\"users\":[");
  for (size_t i = 0; i < db.userCount(); i++) {
    if (i > 0) Serial.print(',');
    const auto& u = db.userAt(i);
    Serial.print("{\"uid\":\"");
    Serial.print(u.uid); // hex-only, never needs escaping
    Serial.print("\",\"name\":\"");
    Serial.print(DatabaseManager::jsonEscape(u.name));
    Serial.print("\",\"registered\":\"");
    Serial.print(u.registered);
    Serial.print("\",\"valid_days\":");
    Serial.print(u.validDays);
    Serial.print('}');
  }
  Serial.println("]}");
}

void SerialProtocol::sendNetStatus(WifiTimeManager& net) {
  DynamicJsonDocument doc(512);
  doc["status"] = "ok";
  doc["type"] = "net_status";
  bool connected = net.isWifiConnected();
  doc["connected"] = connected;
  doc["ssid"] = net.currentSsid();
  doc["ip"] = net.currentIp();
  doc["rssi"] = net.currentRssi();
  doc["time_synced"] = net.isTimeSynced();
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendImportResult(size_t added, size_t errors) {
  DynamicJsonDocument doc(256);
  doc["status"] = (errors == 0) ? "ok" : "error";
  doc["type"] = "import_result";
  doc["added"] = added;
  doc["errors"] = errors;
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendRenewalResult(const String& uid, const String& name,
                                       const String& registered, double validDays) {
  DynamicJsonDocument doc(256);
  doc["status"] = "ok";
  doc["type"] = "renewal_result";
  doc["uid"] = uid;
  doc["name"] = name;
  doc["registered"] = registered;
  doc["valid_days"] = validDays;
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendTime(time_t epoch, const String& formatted) {
  DynamicJsonDocument doc(256);
  doc["status"] = "ok";
  doc["type"] = "time";
  doc["epoch"] = (double)epoch;
  doc["formatted"] = formatted;
  serializeJson(doc, Serial);
  Serial.println();
}

void SerialProtocol::sendTimezoneResult(bool applied, long gmtOffsetSec, int daylightOffsetSec,
                                        const String& message) {
  DynamicJsonDocument doc(256);
  doc["status"] = applied ? "ok" : "error";
  doc["type"] = "timezone";
  doc["applied"] = applied;
  doc["gmt_offset_sec"] = gmtOffsetSec;
  doc["daylight_offset_sec"] = daylightOffsetSec;
  doc["message"] = message;
  serializeJson(doc, Serial);
  Serial.println();
}

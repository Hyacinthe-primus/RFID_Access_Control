#pragma once
/*
 * SerialProtocol.h
 * Single responsibility: frame/deframe newline-terminated JSON messages
 * over Serial. Does NOT know what "add" or "scan" mean -- it hands parsed
 * JsonDocuments to a handler callback owned by SystemController.
 */

#include <Arduino.h>
#include <ArduinoJson.h>
#include <functional>

using SerialMessageHandler = std::function<void(JsonDocument&)>;

class SerialProtocol {
public:
  void begin(SerialMessageHandler handler);

  // Call every loop() iteration. Non-blocking: reads whatever bytes are
  // currently available and only parses once a full '\n'-terminated line
  // has arrived.
  void poll();

  void sendOk();
  void sendError(const String& message);
  void sendUidDetected(const String& uid);

  // Sends {"status":"ok","type":"remove_all_except","removed_count":N}
  void sendRemovedCount(size_t removedCount);

  // Sends {"status":"ok"|"error","type":"wifi_status","connected":bool,"message":"..."}
  void sendWifiResult(bool connected, const String& message);

  // Sends {"status":"ok"|"error","type":"ntp_sync","synced":bool,"message":"..."}
  void sendNtpSyncResult(bool synced, const String& message);

  // Sends {"status":"ok","users":[{"uid":"...","name":"...",
  //        "registered":"YYYY-MM-DD","valid_days":N}, ...]}
  void sendUserList(class DatabaseManager& db);

  // Sends {"status":"ok","type":"status","db_path":"...",
  //        "fs_total_bytes":N,"fs_used_bytes":N,"fs_free_bytes":N,
  //        "user_count":N}
  void sendStatus(class DatabaseManager& db);

  // Sends {"status":"ok","type":"net_status","connected":bool,
  //        "ssid":"...","ip":"...","rssi":N,"time_synced":bool}
  void sendNetStatus(class WifiTimeManager& net);

  // Sends {"status":"ok"|"error","type":"import_result","added":N,"errors":N}
  void sendImportResult(size_t added, size_t errors);

  // Sends {"status":"ok","type":"time","epoch":N,"formatted":"YYYY-MM-DD HH:MM:SS"}
  void sendTime(time_t epoch, const String& formatted);

private:
  // 4096 (not 1024) because 'remove_all_except' can carry an array of many
  // UIDs in one line -- a few dozen UIDs plus JSON punctuation comfortably
  // clears the old 1024-byte ceiling. ESP32-S3 has plenty of SRAM to spare.
  static const size_t kLineBufCapacity = 4096;
  char lineBuf_[kLineBufCapacity];
  size_t lineLen_ = 0;
  SerialMessageHandler handler_;

  void handleLine_(const char* line);
};

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

  // Sends {"status":"ok","users":[{"uid":"...","name":"..."}, ...]}
  void sendUserList(class DatabaseManager& db);

  // Sends {"status":"ok","type":"status","db_path":"...",
  //        "fs_total_bytes":N,"fs_used_bytes":N,"fs_free_bytes":N,
  //        "user_count":N}
  void sendStatus(class DatabaseManager& db);

private:
  static const size_t kLineBufCapacity = 1024;
  char lineBuf_[kLineBufCapacity];
  size_t lineLen_ = 0;
  SerialMessageHandler handler_;

  void handleLine_(const char* line);
};

/*
 * SerialProtocol.cpp
 */

#include "SerialProtocol.h"
#include "DatabaseManager.h"
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
  DynamicJsonDocument doc(1024);
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
  DynamicJsonDocument doc(DB_JSON_CAPACITY);
  doc["status"] = "ok";
  JsonArray arr = doc.createNestedArray("users");
  for (size_t i = 0; i < db.userCount(); i++) {
    JsonObject o = arr.createNestedObject();
    o["uid"] = db.userAt(i).uid;
    o["name"] = db.userAt(i).name;
  }
  serializeJson(doc, Serial);
  Serial.println();
}

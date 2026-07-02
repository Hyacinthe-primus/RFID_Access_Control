/*
 * DatabaseManager.cpp
 */

#include "DatabaseManager.h"
#include "Config.h"
#include <LittleFS.h>
#include <ArduinoJson.h>

bool DatabaseManager::begin() {
  // false = do not format on mount failure yet; we want to try recovery first.
  if (!LittleFS.begin(false)) {
    Serial.println("[DB] LittleFS mount failed, formatting...");
    if (!LittleFS.format()) {
      Serial.println("[DB] FATAL: LittleFS format failed.");
      return false;
    }
    if (!LittleFS.begin(false)) {
      Serial.println("[DB] FATAL: LittleFS mount failed after format.");
      return false;
    }
  }

  if (!load()) {
    Serial.println("[DB] users.json missing or corrupted -- recreating empty DB.");
    recreateEmpty_();
    save();
  }
  return true;
}

void DatabaseManager::recreateEmpty_() {
  users_.clear();
}

bool DatabaseManager::load() {
  if (!LittleFS.exists(USERS_DB_PATH)) {
    recreateEmpty_();
    return false; // triggers save() of an empty DB by caller
  }

  File f = LittleFS.open(USERS_DB_PATH, "r");
  if (!f) {
    recreateEmpty_();
    return false;
  }

  DynamicJsonDocument doc(DB_JSON_CAPACITY);
  DeserializationError err = deserializeJson(doc, f);
  f.close();

  if (err || !doc.is<JsonArray>()) {
    recreateEmpty_();
    return false;
  }

  std::vector<UserRecord> loaded;
  for (JsonObject obj : doc.as<JsonArray>()) {
    if (!obj.containsKey("uid") || !obj.containsKey("name")) continue;
    UserRecord u;
    u.uid = normalizeUid(String((const char*)obj["uid"]));
    u.name = String((const char*)obj["name"]);
    if (!isValidUidFormat(u.uid) || !isValidName(u.name)) continue;
    loaded.push_back(u);
  }

  users_ = loaded;
  return true;
}

bool DatabaseManager::save() {
  DynamicJsonDocument doc(DB_JSON_CAPACITY);
  JsonArray arr = doc.to<JsonArray>();
  for (const auto& u : users_) {
    JsonObject obj = arr.createNestedObject();
    obj["uid"] = u.uid;
    obj["name"] = u.name;
  }

  // Write to a temp file first, then replace -- avoids a half-written
  // users.json if power is lost mid-write.
  File f = LittleFS.open(USERS_DB_TMP_PATH, "w");
  if (!f) return false;

  size_t written = serializeJson(doc, f);
  f.close();

  if (written == 0 && users_.size() > 0) {
    LittleFS.remove(USERS_DB_TMP_PATH);
    return false;
  }

  LittleFS.remove(USERS_DB_PATH);
  bool ok = LittleFS.rename(USERS_DB_TMP_PATH, USERS_DB_PATH);
  return ok;
}

int DatabaseManager::indexOfUid_(const String& uid) const {
  String norm = normalizeUid(uid);
  for (size_t i = 0; i < users_.size(); i++) {
    if (users_[i].uid == norm) return (int)i;
  }
  return -1;
}

bool DatabaseManager::addUser(const String& uid, const String& name, String& errorOut) {
  String norm = normalizeUid(uid);
  if (!isValidUidFormat(norm)) { errorOut = "Invalid UID format"; return false; }
  if (!isValidName(name)) { errorOut = "Invalid or empty name"; return false; }
  if (indexOfUid_(norm) >= 0) { errorOut = "Duplicate UID"; return false; }

  UserRecord u;
  u.uid = norm;
  u.name = name;
  users_.push_back(u);

  if (!save()) { errorOut = "Failed to persist database"; users_.pop_back(); return false; }
  return true;
}

bool DatabaseManager::removeUser(const String& uid, String& errorOut) {
  int idx = indexOfUid_(uid);
  if (idx < 0) { errorOut = "UID not found"; return false; }

  UserRecord backup = users_[idx];
  users_.erase(users_.begin() + idx);

  if (!save()) {
    errorOut = "Failed to persist database";
    users_.insert(users_.begin() + idx, backup);
    return false;
  }
  return true;
}

bool DatabaseManager::renameUser(const String& uid, const String& newName, String& errorOut) {
  if (!isValidName(newName)) { errorOut = "Invalid or empty name"; return false; }
  int idx = indexOfUid_(uid);
  if (idx < 0) { errorOut = "UID not found"; return false; }

  String oldName = users_[idx].name;
  users_[idx].name = newName;

  if (!save()) {
    errorOut = "Failed to persist database";
    users_[idx].name = oldName;
    return false;
  }
  return true;
}

bool DatabaseManager::findUser(const String& uid, String& nameOut) const {
  int idx = indexOfUid_(uid);
  if (idx < 0) return false;
  nameOut = users_[idx].name;
  return true;
}

String DatabaseManager::normalizeUid(const String& rawUid) {
  String out;
  out.reserve(rawUid.length());
  for (size_t i = 0; i < rawUid.length(); i++) {
    char c = rawUid[i];
    if (isxdigit((unsigned char)c)) out += (char)toupper(c);
  }
  return out;
}

bool DatabaseManager::isValidUidFormat(const String& uid) {
  if (uid.length() < MIN_UID_HEX_LEN || uid.length() > MAX_UID_HEX_LEN) return false;
  if (uid.length() % 2 != 0) return false; // must be whole bytes
  for (size_t i = 0; i < uid.length(); i++) {
    if (!isxdigit((unsigned char)uid[i])) return false;
  }
  return true;
}

const char* DatabaseManager::dbPath() const {
  return USERS_DB_PATH;
}

size_t DatabaseManager::fsTotalBytes() const {
  return LittleFS.totalBytes();
}

size_t DatabaseManager::fsUsedBytes() const {
  return LittleFS.usedBytes();
}

bool DatabaseManager::isValidName(const String& name) {
  if (name.length() == 0 || name.length() > MAX_NAME_LEN) return false;
  // Reject names that are pure whitespace.
  for (size_t i = 0; i < name.length(); i++) {
    if (!isspace((unsigned char)name[i])) return true;
  }
  return false;
}

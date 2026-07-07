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
    if (!LittleFS.format()) {
      return false;
    }
    if (!LittleFS.begin(false)) {
      return false;
    }
  }

  if (!load()) {
    recreateEmpty_();
    save();
  }
  return true;
}

void DatabaseManager::recreateEmpty_() {
  users_.clear();
  uidIndex_.clear();
}

void DatabaseManager::rebuildIndex_() {
  uidIndex_.clear();
  for (size_t i = 0; i < users_.size(); i++) uidIndex_[users_[i].uid] = i;
}

bool DatabaseManager::load() {
  if (!LittleFS.exists(USERS_DB_PATH)) {
    recreateEmpty_();
    return false;
  }

  File f = LittleFS.open(USERS_DB_PATH, "r");
  if (!f) {
    recreateEmpty_();
    return false;
  }

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, f);
  f.close();

  if (err || !doc.is<JsonArray>()) {
    recreateEmpty_();
    return false;
  }

  std::vector<UserRecord, PsramAllocator<UserRecord>> loaded;
  for (JsonObject obj : doc.as<JsonArray>()) {
    if (!obj.containsKey("uid") || !obj.containsKey("name")) continue;
    UserRecord u;
    u.uid = normalizeUid(String((const char*)obj["uid"]));
    u.name = String((const char*)obj["name"]);
    // registered/valid_days are new fields -- default gracefully if an old
    // (pre-expiration) users.json is ever loaded, rather than dropping the user.
    // For admin badges the JSON will contain "" and -1 respectively (see
    // DatabaseManager.h's ADMIN_* sentinels); both are accepted by the
    // validators below as admin sentinels.
    u.registered = obj.containsKey("registered") ? String((const char*)obj["registered"]) : String("1970-01-01");
    u.validDays = obj.containsKey("valid_days") ? obj["valid_days"].as<double>() : 0.0;
    if (!isValidUidFormat(u.uid) || !isValidName(u.name)) continue;
    if (!isValidRegisteredDate(u.registered)) continue;
    if (!isValidValidDays(u.validDays)) continue;
    loaded.push_back(u);
  }

  users_ = loaded;
  rebuildIndex_();
  return true;
}

bool DatabaseManager::save() {
  // Stream the JSON directly to a temp file, one user at a time.
  // This avoids allocating a single huge DynamicJsonDocument (~500KB for
  // 4500 users) which would overflow the ESP32's SRAM heap.
  File f = LittleFS.open(USERS_DB_TMP_PATH, "w");
  if (!f) return false;

  f.print('[');
  for (size_t i = 0; i < users_.size(); i++) {
    if (i > 0) f.print(',');
    const auto& u = users_[i];
    // Manual JSON encoding per object -- avoids ArduinoJson entirely.
    f.print("{\"uid\":\"");
    f.print(u.uid); // uid is normalizeUid()'d to [0-9A-F] only -- never needs escaping
    f.print("\",\"name\":\"");
    f.print(jsonEscape(u.name));
    f.print("\",\"registered\":\"");
    f.print(u.registered);
    f.print("\",\"valid_days\":");
    f.print(u.validDays);
    f.print('}');
  }
  f.print(']');
  f.close();

  LittleFS.remove(USERS_DB_PATH);
  bool ok = LittleFS.rename(USERS_DB_TMP_PATH, USERS_DB_PATH);
  return ok;
}

int DatabaseManager::indexOfUid_(const String& uid) const {
  String norm = normalizeUid(uid);
  auto it = uidIndex_.find(norm);
  if (it == uidIndex_.end()) return -1;
  return (int)it->second;
}

bool DatabaseManager::addUser(const String& uid, const String& name, const String& registered,
                               double validDays, String& errorOut) {
  String norm = normalizeUid(uid);
  if (!isValidUidFormat(norm)) { errorOut = "Invalid UID format"; return false; }
  if (!isValidName(name)) { errorOut = "Invalid or empty name"; return false; }
  if (!isValidRegisteredDate(registered)) { errorOut = "Invalid 'registered' date (expected YYYY-MM-DD)"; return false; }
  if (!isValidValidDays(validDays)) { errorOut = "Invalid 'valid_days' (must be a non-negative number)"; return false; }
  if (users_.size() >= MAX_USERS) { errorOut = "Database full (max 10000 users)"; return false; }
  if (uidIndex_.count(norm)) { errorOut = "Duplicate UID"; return false; }

  UserRecord u;
  u.uid = norm;
  u.name = name;
  u.registered = registered;
  u.validDays = validDays;
  users_.push_back(u);
  uidIndex_[norm] = users_.size() - 1;

  if (!importMode_) {
    if (!save()) { errorOut = "Failed to persist database"; users_.pop_back(); uidIndex_.erase(norm); return false; }
  }
  return true;
}

bool DatabaseManager::removeUser(const String& uid, String& errorOut) {
  int idx = indexOfUid_(uid);
  if (idx < 0) { errorOut = "UID not found"; return false; }

  UserRecord backup = users_[idx];
  users_.erase(users_.begin() + idx);
  // Every user after idx just shifted down by one position -- a plain
  // uidIndex_.erase(backup.uid) would leave all of their stored indices
  // stale (off by one), silently breaking future lookups for them.
  rebuildIndex_();

  if (!save()) {
    errorOut = "Failed to persist database";
    users_.insert(users_.begin() + idx, backup);
    rebuildIndex_();
    return false;
  }
  return true;
}

bool DatabaseManager::removeAllExcept(const std::vector<String>& keepUids, size_t& removedCountOut,
                                       String& errorOut) {
  removedCountOut = 0;

  if (keepUids.empty()) {
    errorOut = "Keep list is empty -- use clear_all to wipe every user";
    return false;
  }

  // Normalize + validate every keep UID up front. Fail closed on the first
  // bad entry: a malformed UID here is almost certainly a typo, and
  // silently dropping it would mean the caller keeps fewer users than they
  // asked for (i.e. we'd delete someone they meant to protect).
  std::vector<String> keepNorm;
  keepNorm.reserve(keepUids.size());
  for (const auto& raw : keepUids) {
    String norm = normalizeUid(raw);
    if (!isValidUidFormat(norm)) {
      errorOut = "Invalid UID format in keep list: " + raw;
      return false;
    }
    keepNorm.push_back(norm);
  }

  std::vector<UserRecord, PsramAllocator<UserRecord>> backup = users_;

  std::vector<UserRecord, PsramAllocator<UserRecord>> kept;
  kept.reserve(users_.size());
  for (const auto& u : users_) {
    bool keep = false;
    for (const auto& k : keepNorm) {
      if (u.uid == k) { keep = true; break; }
    }
    if (keep) kept.push_back(u);
  }

  removedCountOut = users_.size() - kept.size();
  users_ = kept;
  rebuildIndex_();

  if (!save()) {
    errorOut = "Failed to persist database";
    users_ = backup;
    rebuildIndex_();
    removedCountOut = 0;
    return false;
  }
  return true;
}

bool DatabaseManager::clearAll(String& errorOut) {
  // Snapshot in RAM so we can roll back atomically if the save fails --
  // same shape as removeUser/renameUser, just at the whole-database scale.
  std::vector<UserRecord, PsramAllocator<UserRecord>> backup;
  backup.swap(users_);
  std::map<String, size_t, std::less<String>,
           PsramAllocator<std::pair<const String, size_t>>> backupIndex;
  backupIndex.swap(uidIndex_);

  if (!save()) {
    errorOut = "Failed to persist database";
    backup.swap(users_);  // restore
    backupIndex.swap(uidIndex_);
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

bool DatabaseManager::findUser(const String& uid, UserRecord& outUser) const {
  int idx = indexOfUid_(uid);
  if (idx < 0) return false;
  outUser = users_[idx];
  return true;
}

bool DatabaseManager::renewUser(const String& uid, const String& today,
                                double validDays, String& errorOut) {
  String norm = normalizeUid(uid);
  int idx = indexOfUid_(norm);
  if (idx < 0) { errorOut = "UID not found"; return false; }

  String oldRegistered = users_[idx].registered;
  double oldValidDays = users_[idx].validDays;
  users_[idx].registered = today;
  users_[idx].validDays = validDays;

  if (!save()) {
    errorOut = "Failed to persist database";
    users_[idx].registered = oldRegistered;
    users_[idx].validDays = oldValidDays;
    return false;
  }
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
  bool sawNonSpace = false;
  for (size_t i = 0; i < name.length(); i++) {
    unsigned char c = (unsigned char)name[i];
    // Control characters (incl. \n, \r, \t) are rejected outright: even
    // though jsonEscape() would now encode them safely, they'd still
    // garble the 16x2 LCD line. '"' and '\' ARE allowed here -- they're
    // valid in a display name and are made safe by jsonEscape() at every
    // write site (save() and sendUserList()).
    if (c < 0x20) return false;
    if (!isspace(c)) sawNonSpace = true;
  }
  // Reject names that are pure whitespace.
  return sawNonSpace;
}

String DatabaseManager::jsonEscape(const String& in) {
  String out;
  out.reserve(in.length() + 8);
  for (size_t i = 0; i < in.length(); i++) {
    char c = in[i];
    switch (c) {
      case '"':  out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if ((unsigned char)c < 0x20) {
          char buf[7];
          snprintf(buf, sizeof(buf), "\\u%04x", (unsigned char)c);
          out += buf;
        } else {
          out += c;
        }
    }
  }
  return out;
}

bool DatabaseManager::isValidRegisteredDate(const String& registered) {
  // Admin sentinel: an empty string means "no registration date". The
  // Python CLI emits this when `add` is called without --valid-days
  // (admin badge shortcut).
  if (registered.length() == 0) return true;

  // Strict "YYYY-MM-DD" shape check. Not a full calendar validator (e.g.
  // won't catch Feb 30), but that's an acceptable tradeoff for firmware --
  // the Python CLI is the only writer and always emits date.today().isoformat().
  if (registered.length() != REGISTERED_DATE_LEN) return false;
  for (int i = 0; i < REGISTERED_DATE_LEN; i++) {
    char c = registered[i];
    if (i == 4 || i == 7) {
      if (c != '-') return false;
    } else {
      if (!isdigit((unsigned char)c)) return false;
    }
  }
  int month = registered.substring(5, 7).toInt();
  int day = registered.substring(8, 10).toInt();
  if (month < 1 || month > 12) return false;
  if (day < 1 || day > 31) return false;
  return true;
}

bool DatabaseManager::isValidValidDays(double validDays) {
  if (isnan(validDays) || isinf(validDays)) return false;
  // Admin sentinel: -1.0 means "never expires". Any other negative value
  // is still rejected.
  if (validDays == ADMIN_VALID_DAYS) return true;
  return validDays >= 0.0;
}

void DatabaseManager::setImportMode(bool on) {
  importMode_ = on;
}

bool DatabaseManager::addUserNoSave(const String& uid, const String& name,
                                    const String& registered, double validDays,
                                    String& errorOut) {
  String norm = normalizeUid(uid);
  if (!isValidUidFormat(norm)) { errorOut = "Invalid UID format"; return false; }
  if (!isValidName(name)) { errorOut = "Invalid or empty name"; return false; }
  if (!isValidRegisteredDate(registered)) { errorOut = "Invalid 'registered' date (expected YYYY-MM-DD)"; return false; }
  if (!isValidValidDays(validDays)) { errorOut = "Invalid 'valid_days' (must be a non-negative number)"; return false; }
  if (users_.size() >= MAX_USERS) { errorOut = "Database full (max 10000 users)"; return false; }
  if (uidIndex_.count(norm)) { errorOut = "Duplicate UID"; return false; }

  UserRecord u;
  u.uid = norm;
  u.name = name;
  u.registered = registered;
  u.validDays = validDays;
  users_.push_back(u);
  uidIndex_[norm] = users_.size() - 1;
  return true;
}

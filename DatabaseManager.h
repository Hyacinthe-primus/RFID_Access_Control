#pragma once
/*
 * DatabaseManager.h
 * Owns users.json on LittleFS. Single responsibility: persistence + lookups.
 * Does NOT know about RFID or the LCD. The caller (SystemController) is
 * responsible for pausing RFID scanning around write operations.
 */

#include <Arduino.h>
#include <vector>

struct UserRecord {
  String uid;   // stored upper-case, no separators
  String name;
};

class DatabaseManager {
public:
  bool begin();                       // mounts LittleFS, loads DB (or recreates it)
  bool load();                        // (re)loads users_ from USERS_DB_PATH
  bool save();                        // atomically persists users_ to USERS_DB_PATH

  bool addUser(const String& uid, const String& name, String& errorOut);
  bool removeUser(const String& uid, String& errorOut);
  bool renameUser(const String& uid, const String& newName, String& errorOut);

  bool findUser(const String& uid, String& nameOut) const;

  size_t userCount() const { return users_.size(); }
  const UserRecord& userAt(size_t i) const { return users_[i]; }

  const char* dbPath() const;         // path of USERS_DB_PATH on LittleFS
  size_t fsTotalBytes() const;        // total LittleFS partition size, in bytes
  size_t fsUsedBytes() const;         // bytes currently used on LittleFS

  static String normalizeUid(const String& rawUid);
  static bool isValidUidFormat(const String& uid);
  static bool isValidName(const String& name);

private:
  std::vector<UserRecord> users_;
  void recreateEmpty_();
  int indexOfUid_(const String& uid) const;
};

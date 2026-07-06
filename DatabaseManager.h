#pragma once
/*
 * DatabaseManager.h
 * Owns users.json on LittleFS. Single responsibility: persistence + lookups.
 * Does NOT know about RFID or the LCD. The caller (SystemController) is
 * responsible for pausing RFID scanning around write operations.
 *
 * Schema (per user):
 *   { "uid": "A43FE5S4", "name": "Azrael",
 *     "registered": "2024-04-06", "valid_days": 30 }
 * 'registered' is an ISO-8601 date (YYYY-MM-DD) stamped by the Python CLI
 * at creation time -- the firmware never generates or edits it, it only
 * validates the format and stores it verbatim. 'valid_days' may be
 * fractional (e.g. 0.01) so short-lived badges can be tested quickly.
 *
 * --- Admin badges ---
 * An "admin" NFC card has no expiration and no registration date -- it is
 * always granted regardless of NTP sync state. Admins are stored with the
 * sentinel values below (empty registered, valid_days = -1). Old user
 * databases (created before the admin feature) always have a real date and
 * a non-negative valid_days, so the admin detection is fully backward
 * compatible -- no migration step is needed.
 */

#include <Arduino.h>
#include <vector>
#include <set>
#include <esp_heap_caps.h>

// PSRAM allocator for STL containers.  Routes malloc/free to the 8MB
// external PSRAM so large containers don't overflow the ~300KB SRAM heap.
template <typename T>
struct PsramAllocator {
  using value_type = T;
  PsramAllocator() = default;
  template <typename U> struct rebind { using other = PsramAllocator<U>; };
  template <typename U>
  PsramAllocator(const PsramAllocator<U>&) {}
  T* allocate(std::size_t n) {
    return (T*)heap_caps_malloc(n * sizeof(T), MALLOC_CAP_SPIRAM);
  }
  void deallocate(T* p, std::size_t) {
    heap_caps_free(p);
  }
  bool operator==(const PsramAllocator&) const { return true; }
  bool operator!=(const PsramAllocator&) const { return false; }
};

// Sentinels used to mark an admin badge inside the JSON database.
//   - ADMIN_REGISTERED is the empty string (the normal validator accepts
//     it specifically as a sentinel; any non-empty value must still be
//     a real YYYY-MM-DD date).
//   - ADMIN_VALID_DAYS is -1.0 (the normal validator accepts it as a
//     sentinel; any other value must be >= 0).
#define ADMIN_REGISTERED       ""
#define ADMIN_VALID_DAYS       (-1.0)

struct UserRecord {
  String uid;         // stored upper-case, no separators
  String name;
  String registered;  // ISO-8601 date "YYYY-MM-DD", or "" for admin badges
  double validDays = 0.0;  // >= 0 for normal badges, -1 for admin badges

  bool isAdmin() const {
    // Treat either sentinel as admin. Both are set together when an admin
    // is created, but checking both makes load() resilient against a
    // partially-corrupted record (e.g. if a future CLI writes only one).
    return registered.length() == 0 || validDays == ADMIN_VALID_DAYS;
  }
};

class DatabaseManager {
public:
  bool begin();                       // mounts LittleFS, loads DB (or recreates it)
  bool load();                        // (re)loads users_ from USERS_DB_PATH
  bool save();                        // atomically persists users_ to USERS_DB_PATH

  bool addUser(const String& uid, const String& name, const String& registered,
               double validDays, String& errorOut);
  bool removeUser(const String& uid, String& errorOut);
  bool renameUser(const String& uid, const String& newName, String& errorOut);
  bool clearAll(String& errorOut);    // wipes every user, persists immediately

  // Deletes every user whose UID is NOT in keepUids, persists immediately.
  // keepUids must be non-empty (use clearAll() to wipe everything) and every
  // entry must be a well-formed UID -- a typo in the keep list must never
  // silently turn into "delete everyone", so this fails closed instead of
  // guessing. UIDs in keepUids that aren't actually in the database are
  // simply ignored (not an error). On success, removedCountOut is set to
  // the number of users deleted.
  bool removeAllExcept(const std::vector<String>& keepUids, size_t& removedCountOut,
                        String& errorOut);

  // Batch import mode: when active, addUser() skips the per-call save().
  // Caller must call setImportMode(true) before the batch and
  // setImportMode(false) + save() after the last addUserNoSave().
  void setImportMode(bool on);
  bool isImportMode() const { return importMode_; }

  // Inserts a user into RAM only (no persistence). Returns false on
  // validation error -- the caller should stop the import and report it.
  bool addUserNoSave(const String& uid, const String& name,
                     const String& registered, double validDays,
                     String& errorOut);

  // Renews a user: sets registered = today, validDays = new value.
  // Returns false if UID not found.
  bool renewUser(const String& uid, const String& today, double validDays,
                 String& errorOut);

  bool findUser(const String& uid, UserRecord& outUser) const;

  size_t userCount() const { return users_.size(); }
  const UserRecord& userAt(size_t i) const { return users_[i]; }

  const char* dbPath() const;         // path of USERS_DB_PATH on LittleFS
  size_t fsTotalBytes() const;        // total LittleFS partition size, in bytes
  size_t fsUsedBytes() const;         // bytes currently used on LittleFS

  static String normalizeUid(const String& rawUid);
  static bool isValidUidFormat(const String& uid);
  static bool isValidName(const String& name);
  static bool isValidRegisteredDate(const String& registered);
  static bool isValidValidDays(double validDays);

private:
  std::vector<UserRecord, PsramAllocator<UserRecord>> users_;
  std::set<String, std::less<String>, PsramAllocator<String>> uidIndex_;
  bool importMode_ = false;
  void recreateEmpty_();
  void rebuildIndex_();         // rebuilds uidIndex_ from users_ (call after bulk replace)
  int indexOfUid_(const String& uid) const;
};

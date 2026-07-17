#pragma once
/*
 * DatabaseManager.h
 * Owns users.bin on LittleFS. Persistence + lookups only (no RFID, no LCD).
 *
 * Binary format (v1): header ("RUD1" + version + recordSize uint16 LE)
 * + N fixed-width records. Record count derived from file size. Per-record
 * CRC32 -- a corrupt record is skipped, not the whole database. Sorted by
 * UID; add/remove use suffix rewrite.
 *
 * Legacy users.json is auto-converted on first boot and kept as .bak.
 * Admin badges: empty registered + validDays -1.
 */

#include <Arduino.h>
#include <vector>
#include <set>
#include <algorithm>
#include <cstring>
#include <esp_heap_caps.h>
#include "Config.h"

// STL allocator backed by external PSRAM.
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

#define ADMIN_REGISTERED       ""
#define ADMIN_VALID_DAYS       (-1.0)

static const char     DB_MAGIC[4]     = {'R', 'U', 'D', '1'};
static const uint8_t  DB_VERSION      = 1;
static const size_t   DB_HEADER_SIZE  = 4 /*magic*/ + 1 /*version*/ + 2 /*recordSize*/;
static const size_t   DB_UID_BYTES    = MAX_UID_HEX_LEN / 2;  // 10
static const size_t   DB_RECORD_SIZE  =
    1                    /* uidLen     */ +
    DB_UID_BYTES         /* uidBytes   */ +
    1                    /* nameLen    */ +
    MAX_NAME_LEN          /* nameBytes  */ +
    2                    /* regDays    */ +
    sizeof(double)       /* validDays  */ +
    4                    /* crc32      */;
static const uint16_t DB_ADMIN_DAYS_SENTINEL = 0xFFFF;

// Fixed-capacity inline string; avoids String heap allocations.
template <size_t N>
struct FixedStr {
  char buf[N + 1];
  FixedStr() { buf[0] = '\0'; }
  FixedStr(const String& s) { *this = s; }
  FixedStr& operator=(const String& s) {
    size_t len = s.length();
    if (len > N) len = N;
    memcpy(buf, s.c_str(), len);
    buf[len] = '\0';
    return *this;
  }
  operator String() const { return String(buf); }
  const char* c_str() const { return buf; }
  size_t length() const { return strlen(buf); }
  bool operator<(const FixedStr& other) const { return strcmp(buf, other.buf) < 0; }
  bool operator==(const FixedStr& other) const { return strcmp(buf, other.buf) == 0; }
  bool operator==(const String& other) const { return other == buf; }
};

struct UserRecord {
  FixedStr<MAX_UID_HEX_LEN> uid;     // upper-case, no separators
  FixedStr<MAX_NAME_LEN> name;
  FixedStr<10> registered;           // ISO-8601, or "" for admin badges
  double validDays = 0.0;            // -1 for admin badges

  // Checks both sentinels so a partially-corrupted record still resolves.
  bool isAdmin() const {
    return registered.length() == 0 || validDays == ADMIN_VALID_DAYS;
  }
};

class DatabaseManager {
public:
  bool begin();
  bool load();
  bool save();

  bool addUser(const String& uid, const String& name, const String& registered,
               double validDays, String& errorOut);
  bool removeUser(const String& uid, String& errorOut);
  bool renameUser(const String& uid, const String& newName, String& errorOut);
  bool clearAll(String& errorOut);

  // Deletes every user NOT in keepUids. Fails closed if keepUids is empty.
  bool removeAllExcept(const std::vector<String>& keepUids, size_t& removedCountOut,
                        String& errorOut);

  // While active, addUser() skips its per-call save(). Caller must pair
  // setImportMode(true)/addUserNoSave() with setImportMode(false)+save().
  void setImportMode(bool on);
  bool isImportMode() const { return importMode_; }

  // Pre-allocates PSRAM to avoid the ~2x transient spike from repeated
  // push_back() during import.
  void reserveForImport(size_t additionalUsers);

  bool addUserNoSave(const String& uid, const String& name,
                     const String& registered, double validDays,
                     String& errorOut);

  // Sets registered = today, validDays = new value.
  bool renewUser(const String& uid, const String& today, double validDays,
                 String& errorOut);

  bool findUser(const String& uid, UserRecord& outUser) const;

  size_t userCount() const { return users_.size(); }
  const UserRecord& userAt(size_t i) const { return users_[i]; }

  uint32_t computeCrc32() const;
  static size_t recordSize();
  void encodeUserAt(size_t i, uint8_t* outRec) const;

  // Same validation as addUserNoSave(). False on corrupt/duplicate/invalid.
  bool addUserFromRawRecord(const uint8_t* rec, String& errorOut);

  // ---- sync support ----
  // Manifest entry = uidLen(1) + uidBytes(DB_UID_BYTES) + per-record CRC32(4).
  // Reuses the same trailing CRC32 already stored in each record (see
  // encodeRecord_) -- no separate "manifest checksum" needed.
  static size_t manifestEntrySize();
  void encodeManifestEntryAt(size_t i, uint8_t* outEntry) const;

  // Wire size of a bare UID entry (uidLen + uidBytes, no name/dates) --
  // the "remove" list's per-entry size in the sync protocol.
  static size_t uidEntrySize();

  // Sync mutators. No save() -- the "sync" command in SystemController
  // persists once after all ops are applied.
  //
  // Batch remove: mark-then-compact instead of one erase() per call.
  // erase() is an O(n) memmove, so N removes from an N-user db is
  // O(n^2) -- slow enough on PSRAM to blow past the task watchdog on a
  // large sync diff. beginRemoveBatch() snapshots the current size and
  // clears the tombstone bitmap; removeUserRawNoSave() then just flips a
  // bit (O(log n) lookup, O(1) mark) instead of shifting the array;
  // endRemoveBatch() does a single O(n) compaction pass and returns the
  // db to its normal (untombstoned) state. Caller must pair
  // beginRemoveBatch() with exactly one endRemoveBatch() -- calling
  // removeUserRawNoSave() outside an active batch falls back to the old
  // immediate erase() so other callers keep working unchanged.
  void beginRemoveBatch();
  size_t endRemoveBatch();

  // removeUserRawNoSave() keeps users_ sorted after every call (marks
  // for removal inside a begin/endRemoveBatch() pair; immediate erase()
  // otherwise). ADD no longer has its own mutator here: sync_apply's add
  // phase reuses addUserFromRawRecord() + setImportMode() instead --
  // append unsorted, single O(n log n) resort at the end -- the same
  // strategy import_bin uses, rather than a second sorted-insert path.
  bool removeUserRawNoSave(const uint8_t* uidEntry, String& errorOut);
  // uid in `rec` must already exist; overwrites the whole record in place
  // (position is unchanged since a replace never changes uid). Requires
  // users_ to be sorted (binary search via indexOfUid_) -- callers must
  // not be inside an unresolved setImportMode(true) window.
  bool replaceUserFromRawRecord(const uint8_t* rec, String& errorOut);

  const char* dbPath() const;
  size_t fsTotalBytes() const;
  size_t fsUsedBytes() const;

  static String normalizeUid(const String& rawUid);
  static bool isValidUidFormat(const String& uid);
  static bool isValidName(const String& name);
  static bool isValidRegisteredDate(const String& registered);
  static bool isValidValidDays(double validDays);

  // Escapes '"', '\', and control chars for hand-built JSON.
  static String jsonEscape(const String& in);

private:
  // Always sorted ascending by uid; enables binary search + suffix rewrite.
  std::vector<UserRecord, PsramAllocator<UserRecord>> users_;

  using UidKey = FixedStr<MAX_UID_HEX_LEN>;

  // Import: records appended unsorted, re-sorted once at import_end.
  // Binary search only valid over [0, importBaselineCount_); duplicate
  // checks against the batch itself use importSeen_.
  std::set<UidKey, std::less<UidKey>, PsramAllocator<UidKey>> importSeen_;
  size_t importBaselineCount_ = 0;
  bool existsInBaseline_(const UidKey& norm) const;

  bool importMode_ = false;
  void recreateEmpty_();

  // Remove-batch state (see beginRemoveBatch()/endRemoveBatch()). Sized
  // to users_.size() at beginRemoveBatch() time; index i true means
  // users_[i] is pending removal at endRemoveBatch().
  std::vector<bool> removeTombstones_;
  bool removeBatchActive_ = false;

  int indexOfUid_(const String& uid) const;
  size_t lowerBound_(const String& normUid) const;

  bool loadBinary_();
  bool loadLegacyJsonAndMigrate_();

  static void encodeRecord_(const UserRecord& u, uint8_t* outRec);
  static bool decodeRecord_(const uint8_t* rec, UserRecord& out);

  bool saveSingleRecord_(size_t idx);

  // Add = suffix-only write. Remove falls back to full save() (no
  // truncate() available on this LittleFS).
  bool saveSuffixFrom_(size_t startIdx);

  // Proleptic Gregorian day count (Hinnant's civil_from_days/days_from_civil).
  static uint16_t dateToDays_(const char* registered);
  static String   daysToDate_(uint16_t days);

  static uint32_t crc32_(const uint8_t* data, size_t len, uint32_t crc = 0);
};
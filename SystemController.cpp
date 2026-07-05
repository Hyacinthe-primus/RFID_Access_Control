/*
 * SystemController.cpp
 */

#include "SystemController.h"
#include "Config.h"
#include <cstring>
#include <time.h>

namespace {
  // Formats a time_t (epoch seconds) into "YYYY-MM-DD HH:MM:SS" in local
  // time.  ESP32's newlib does NOT provide strftime(), so we use localtime()
  // and manual formatting instead.
  String formatLocalTime(time_t epoch) {
    struct tm* t = localtime(&epoch);
    if (!t) return String("??");
    char buf[20];
    snprintf(buf, sizeof(buf), "%04d-%02d-%02d %02d:%02d:%02d",
             t->tm_year + 1900, t->tm_mon + 1, t->tm_mday,
             t->tm_hour, t->tm_min, t->tm_sec);
    return String(buf);
  }

  // Portable conversion of a calendar date to UTC epoch seconds.
  // ESP32's newlib does NOT provide timegm(), so we use Howard Hinnant's
  // civil-calendar-to-days algorithm instead -- no libc time conversion
  // needed, works identically on AVR, ESP32, ARM, etc.
  time_t civilDateToEpoch(int year, int month, int day) {
    // Shift March-based year so that the leap-day rule is simplified
    if (month <= 2) { year--; month += 12; }
    // Days from 1970-01-01 using the Gregorian calendar algorithm
    int era = (year >= 0 ? year : year - 399) / 400;
    unsigned yoe = static_cast<unsigned>(year - era * 400);       // year within era  [0, 399]
    unsigned doy = (153u * (static_cast<unsigned>(month) - 3u) + 2u) / 5u + static_cast<unsigned>(day) - 1u;
    unsigned doe = yoe * 365u + yoe / 4u - yoe / 100u + doy;     // day-of-era       [0, 146096]
    int days = static_cast<int>(era * 146097) + static_cast<int>(doe) - 719468;
    return static_cast<time_t>(days) * 86400LL;
  }

  // Parses a "YYYY-MM-DD" string (already format-validated by
  // DatabaseManager::isValidRegisteredDate) into UTC midnight epoch seconds.
  time_t parseRegisteredDateUtc(const String& registered) {
    int year  = registered.substring(0, 4).toInt();
    int month = registered.substring(5, 7).toInt();
    int day   = registered.substring(8, 10).toInt();
    return civilDateToEpoch(year, month, day);
  }
}

void SystemController::begin() {
  Serial.begin(SERIAL_BAUD);
  uint32_t waitStart = millis();
  while (!Serial && (millis() - waitStart) < 3000) { /* wait briefly for native USB CDC */ }

  display_.begin();
  enterState_(SystemState::BOOT);
  display_.showBoot();

  if (!db_.begin()) {
    display_.showError("DB INIT FAIL");
    // Keep going -- an empty in-RAM DB still lets ACCESS DENIED work,
    // and the operator can retry 'add' once they notice the error.
  }

  bool rfidOk = rfid_.begin();
  if (!rfidOk) {
    display_.showError("PN532 NOT FOUND");
  }

  buzzer_.begin();

  // Blocking (bounded by WIFI_CONNECT_TIMEOUT_MS/NTP_SYNC_TIMEOUT_MS), same
  // one-off pattern as the rest of begin(). If no credentials are stored
  // yet, or the connect/sync fails, we keep going: badges will simply be
  // reported as expired (fail safe) until 'configure_wifi' succeeds.
  network_.begin();

  serial_.begin([this](JsonDocument& doc) { handleSerialMessage_(doc); });

  serialWasConnected_ = (bool)Serial;

  uint32_t bootElapsed = millis() - stateEnteredMs_;
  if (bootElapsed < BOOT_SCREEN_MIN_MS) {
    delay(BOOT_SCREEN_MIN_MS - bootElapsed);
  }

  enterState_(SystemState::IDLE);
  display_.showIdle();
}

void SystemController::enterState_(SystemState s) {
  state_ = s;
  stateEnteredMs_ = millis();
}

void SystemController::restoreIdleOrScanScreen_() {
  if (scanModeActive_) {
    state_ = SystemState::SCAN_MODE;
    display_.showScanMode();
  } else {
    state_ = SystemState::IDLE;
    display_.showIdle();
  }
}

void SystemController::update() {
  // 1. Always service the serial link, regardless of state, so 'add'/
  //    'remove'/'rename'/'list'/'scan' commands are never dropped even
  //    while a result screen is being shown.
  serial_.poll();
  handleSerialConnectionChange_();
  buzzer_.update();
  network_.update(); // non-blocking check; only re-syncs every NTP_RESYNC_INTERVAL_MS

  uint32_t now = millis();

  switch (state_) {
    case SystemState::BOOT:
      // begin() moves us out of BOOT synchronously; nothing to do here.
      break;

    case SystemState::IDLE:
    case SystemState::SCAN_MODE: {
      String uid;
      if (rfid_.poll(uid)) {
        handleCardDetected_(uid);
      }
      break;
    }

    case SystemState::RESULT_DISPLAY:
      if (now - stateEnteredMs_ >= RESULT_DISPLAY_MS) {
        display_.showUid(pendingUid_);
        enterState_(SystemState::UID_DISPLAY);
      }
      break;

    case SystemState::UID_DISPLAY:
      if (now - stateEnteredMs_ >= UID_DISPLAY_MS) {
        restoreIdleOrScanScreen_();
        enterState_(state_); // refresh timer, harmless in IDLE/SCAN_MODE
      }
      break;

    case SystemState::DB_BUSY:
      // Only reached transiently inside withDbBusyScreen_; update() should
      // never observe this state because that helper is synchronous.
      break;
  }
}

bool SystemController::isUserExpired_(const UserRecord& user, bool& timeAvailableOut) const {
  timeAvailableOut = network_.isTimeSynced();

  // Admin badges never expire, regardless of NTP state. This is the whole
  // point of the admin shortcut: an admin card always grants access even
  // if the device's clock has not been synced yet (which would otherwise
  // fail safe to "denied" for normal badges).
  if (user.isAdmin()) {
    return false;
  }

  if (!timeAvailableOut) {
    // Fail safe: without a trustworthy clock we cannot evaluate
    // "current_date_time <= expiration_date", so treat as expired.
    return true;
  }

  // 'registered' is a bare calendar date with no timezone info -- it's
  // interpreted in the same local timezone as NTP_GMT_OFFSET_SEC/
  // NTP_DAYLIGHT_OFFSET_SEC (see Config.h), which must match the timezone
  // of the machine running the Python CLI for this to line up correctly.
  time_t registeredEpoch = parseRegisteredDateUtc(user.registered);
  double expirationEpoch = (double)registeredEpoch + user.validDays * 86400.0;
  double currentLocalEpoch = (double)network_.nowUtc() + NTP_GMT_OFFSET_SEC + NTP_DAYLIGHT_OFFSET_SEC;
  // nowUtc() returns raw UTC; configTime() only affects localtime(), not
  // time(NULL), so the manual addition here is correct and NOT a double count.

  return currentLocalEpoch > expirationEpoch; // expired if current > expiration
}

void SystemController::handleCardDetected_(const String& uid) {
  if (scanModeActive_) {
    // Scan mode: report the UID, do NOT check it against the database,
    // then automatically fall back to normal operation.
    serial_.sendUidDetected(uid);
    scanModeActive_ = false;
    enterState_(SystemState::IDLE);
    display_.showIdle();
    return;
  }

  UserRecord user;
  bool found = db_.findUser(uid, user);

  bool timeAvailable = true;
  bool expired = found ? isUserExpired_(user, timeAvailable) : false;
  bool granted = found && !expired;

  pendingAccessGranted_ = granted;
  pendingUid_ = uid;
  pendingName_ = granted ? user.name : String("");

  if (granted) {
    display_.showAccessGranted(pendingName_);
    buzzer_.playGranted();
  } else if (found && expired && !timeAvailable) {
    display_.showAccessDenied("No Time Sync");
    buzzer_.playDenied();
  } else if (found && expired) {
    display_.showAccessDenied("Expired");
    buzzer_.playDenied();
  } else {
    display_.showAccessDenied();
    buzzer_.playDenied();
  }
  enterState_(SystemState::RESULT_DISPLAY);
}

void SystemController::handleSerialMessage_(JsonDocument& doc) {
  const char* type = doc["type"] | "";

  if (strcmp(type, "add") == 0) {
    if (!doc.containsKey("uid") || !doc.containsKey("name")) {
      serial_.sendError("Missing 'uid' or 'name'");
      return;
    }
    String uid = String((const char*)doc["uid"]);
    String name = String((const char*)doc["name"]);

    // --- Admin vs normal badge detection --------------------------------
    // The Python CLI sends an ADMIN add as `{"type":"add","uid":...,"name":...}`
    // with NO 'registered' and NO 'valid_days' fields. When either field is
    // missing we substitute the admin sentinels (empty registered, -1 days),
    // which the DatabaseManager validators accept and which `UserRecord::isAdmin()`
    // later recognises. A normal badge still sends both fields as before.
    // ---------------------------------------------------------------------
    String registered;
    double validDays;
    bool adminBadge = !doc.containsKey("registered") || !doc.containsKey("valid_days");
    if (adminBadge) {
      registered = String(ADMIN_REGISTERED);   // ""
      validDays = ADMIN_VALID_DAYS;             // -1.0
    } else {
      registered = String((const char*)doc["registered"]);
      validDays = doc["valid_days"].as<double>();
    }

    String err;
    bool ok;
    if (db_.isImportMode()) {
      // Batch import: the "DATABASE UPDATING" screen is shown once by
      // import_begin and restored once by import_end. Do NOT churn the
      // I2C LCD on every single add -- at ~1500 users that's 1500x2
      // full-screen refreshes for nothing, and I2C writes are slow
      // (multiple ms per transaction).
      ok = db_.addUserNoSave(uid, name, registered, validDays, err);
    } else {
      ok = withDbBusyScreen_([&]() {
        return db_.addUser(uid, name, registered, validDays, err);
      });
    }
    if (ok) serial_.sendOk(); else serial_.sendError(err);
    return;
  }

  if (strcmp(type, "remove") == 0) {
    if (!doc.containsKey("uid")) {
      serial_.sendError("Missing 'uid'");
      return;
    }
    String uid = String((const char*)doc["uid"]);
    String err;
    bool ok = withDbBusyScreen_([&]() { return db_.removeUser(uid, err); });
    if (ok) serial_.sendOk(); else serial_.sendError(err);
    return;
  }

  if (strcmp(type, "clear_all") == 0) {
    // Wipes every user from the device database. The CLI's `remove --force`
    // command sends this. No UID required -- the entire users_ vector is
    // emptied and persisted atomically (DatabaseManager::clearAll rolls
    // back in RAM if the LittleFS write fails, so a power loss mid-write
    // never leaves a half-empty DB on disk).
    String err;
    bool ok = withDbBusyScreen_([&]() { return db_.clearAll(err); });
    if (ok) serial_.sendOk(); else serial_.sendError(err);
    return;
  }

  if (strcmp(type, "remove_all_except") == 0) {
    // Deletes every user NOT in the given 'uids' array. The CLI's
    // `remove --except UID1,UID2,...` command sends this. We require a
    // JSON array with at least one entry -- an empty/missing list is
    // rejected here (and again in DatabaseManager) rather than treated as
    // "keep nothing", since that would just be clear_all wearing a
    // different name and callers should say that explicitly instead.
    if (!doc.containsKey("uids") || !doc["uids"].is<JsonArray>()) {
      serial_.sendError("Missing or invalid 'uids' array");
      return;
    }
    JsonArray arr = doc["uids"].as<JsonArray>();
    if (arr.size() == 0) {
      serial_.sendError("'uids' array is empty -- use clear_all to wipe every user");
      return;
    }
    std::vector<String> keepUids;
    keepUids.reserve(arr.size());
    for (JsonVariant v : arr) {
      keepUids.push_back(String((const char*)v));
    }

    size_t removedCount = 0;
    String err;
    bool ok = withDbBusyScreen_([&]() { return db_.removeAllExcept(keepUids, removedCount, err); });
    if (ok) serial_.sendRemovedCount(removedCount); else serial_.sendError(err);
    return;
  }

  if (strcmp(type, "rename") == 0) {
    if (!doc.containsKey("uid") || !doc.containsKey("name")) {
      serial_.sendError("Missing 'uid' or 'name'");
      return;
    }
    String uid = String((const char*)doc["uid"]);
    String newName = String((const char*)doc["name"]);
    String err;
    bool ok = withDbBusyScreen_([&]() { return db_.renameUser(uid, newName, err); });
    if (ok) serial_.sendOk(); else serial_.sendError(err);
    return;
  }

  if (strcmp(type, "list") == 0) {
    // Read-only, in-RAM -- no need to pause RFID scanning for this one.
    serial_.sendUserList(db_);
    return;
  }

  if (strcmp(type, "status") == 0) {
    // Read-only, in-RAM + a couple of LittleFS syscalls -- no need to
    // pause RFID scanning for this one.
    serial_.sendStatus(db_);
    return;
  }

  if (strcmp(type, "net_status") == 0) {
    // Read-only snapshot of the current Wi-Fi state. Non-blocking: the
    // NetworkManager getters just read WiFi.status()/WiFi.SSID()/etc,
    // they never initiate a reconnect or NTP sync.
    serial_.sendNetStatus(network_);
    return;
  }

  if (strcmp(type, "get_time") == 0) {
    if (!network_.isTimeSynced()) {
      serial_.sendError("NTP time not synced yet");
      return;
    }
    // configTime() already applied NTP_GMT_OFFSET_SEC so localtime()
    // returns local time -- do NOT add the offset again.
    time_t now = network_.nowUtc();
    String formatted = formatLocalTime(now);
    serial_.sendTime(now, formatted);
    return;
  }

  if (strcmp(type, "ntp_sync") == 0) {
    if (!network_.isWifiConnected()) {
      serial_.sendNtpSyncResult(false, "Wi-Fi not connected");
      return;
    }
    bool synced = network_.resyncTime();
    if (synced) {
      time_t now = network_.nowUtc();
      String formatted = formatLocalTime(now);
      serial_.sendNtpSyncResult(true, formatted);
    } else {
      serial_.sendNtpSyncResult(false, "NTP sync failed (server unreachable?)");
    }
    return;
  }

  if (strcmp(type, "import_begin") == 0) {
    // Enters batch-import mode: subsequent "add" calls insert users into
    // RAM only, without persisting to flash.  The import is finalized by
    // "import_end", which writes once.
    //
    // The busy screen is shown ONCE here and held for the whole batch --
    // NOT via withDbBusyScreen_, which would show-then-immediately-restore
    // it on this single call and leave every subsequent "add" with no
    // screen state to hold onto.
    state_ = SystemState::DB_BUSY;
    display_.showDatabaseUpdating();
    db_.setImportMode(true);
    serial_.sendOk();
    return;
  }

  if (strcmp(type, "import_end") == 0) {
    // Finalizes a batch import: disables import mode, persists the
    // in-memory database to flash once, and reports how many users were
    // added and how many failed validation.
    size_t added = 0;
    size_t errors = 0;
    String err;
    bool ok = withDbBusyScreen_([&]() {
      added = db_.userCount();
      db_.setImportMode(false);
      bool persisted = db_.save();
      if (!persisted) {
        err = "Failed to persist database";
        return false;
      }
      return true;
    });
    if (ok) {
      serial_.sendImportResult(added, errors);
    } else {
      serial_.sendError(err);
    }
    return;
  }

  if (strcmp(type, "enter_scan_mode") == 0) {
    if (state_ == SystemState::RESULT_DISPLAY || state_ == SystemState::UID_DISPLAY) {
      // Don't interrupt a result that's still being shown to whoever
      // just badged in -- scan mode will engage on the next update().
    }
    scanModeActive_ = true;
    enterState_(SystemState::SCAN_MODE);
    display_.showScanMode();
    serial_.sendOk();
    return;
  }

  if (strcmp(type, "configure_wifi") == 0) {
    if (!doc.containsKey("ssid") || !doc.containsKey("password")) {
      serial_.sendError("Missing 'ssid' or 'password'");
      return;
    }
    String ssid = String((const char*)doc["ssid"]);
    String password = String((const char*)doc["password"]);

    SystemState previous = state_;
    state_ = SystemState::DB_BUSY; // reuses the "block the idle/scan screen" state
    display_.showWifiConnecting();

    bool connected = network_.configure(ssid, password);

    display_.showWifiResult(connected);
    delay(1000); // brief, bounded -- lets the operator read the result, same as the boot-screen wait

    state_ = previous;
    restoreIdleOrScanScreen_();

    serial_.sendWifiResult(connected,
      connected ? "Wi-Fi connected and time synced" : "Failed to connect with the given credentials");
    return;
  }

  serial_.sendError("Unknown command type");
}

void SystemController::handleSerialConnectionChange_() {
  bool connected = (bool)Serial;

  if (connected != serialWasConnected_) {
    serialWasConnected_ = connected;

    // Only interrupt the screen if we're in an idle-ish state -- never
    // stomp on a result the user is currently reading.
    if (state_ == SystemState::IDLE || state_ == SystemState::SCAN_MODE) {
      if (connected) display_.showSerialConnected();
      else display_.showSerialDisconnected();
      serialNoticeActive_ = true;
      serialNoticeStartMs_ = millis();
    }
  }

  if (serialNoticeActive_ && (millis() - serialNoticeStartMs_ >= 1200)) {
    serialNoticeActive_ = false;
    if (state_ == SystemState::IDLE || state_ == SystemState::SCAN_MODE) {
      restoreIdleOrScanScreen_();
    }
  }
}
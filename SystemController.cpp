/*
 * SystemController.cpp
 */

#include "SystemController.h"
#include "Config.h"
#include <cstring>

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

  String name;
  bool granted = db_.findUser(uid, name);

  pendingAccessGranted_ = granted;
  pendingUid_ = uid;
  pendingName_ = granted ? name : String("");

  if (granted) {
    display_.showAccessGranted(pendingName_);
  } else {
    display_.showAccessDenied();
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
    String err;
    bool ok = withDbBusyScreen_([&]() { return db_.addUser(uid, name, err); });
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

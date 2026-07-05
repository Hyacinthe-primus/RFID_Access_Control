#pragma once
/*
 * SystemController.h
 * Orchestrates DatabaseManager + DisplayManager + RFIDManager + SerialProtocol.
 * This is the only class that knows the full state machine. Everything
 * else is a dumb-ish component with one job.
 *
 * All timing is millis()-based and the RFID/serial/buzzer loop never
 * blocks. The only blocking waits left in the firmware are bounded,
 * one-off admin/setup operations that already had this shape before Wi-Fi
 * was added: the boot-screen minimum-duration wait, and now Wi-Fi
 * connect + NTP sync (at boot, and when 'configure_wifi' is received).
 * Those are intentionally synchronous so the operator/CLI gets an
 * immediate success/failure result instead of a fire-and-forget one.
 */

#include <Arduino.h>
#include "DatabaseManager.h"
#include "DisplayManager.h"
#include "RFIDManager.h"
#include "SerialProtocol.h"
#include "NetworkManager.h"
#include "BuzzerManager.h"

enum class SystemState {
  BOOT,
  IDLE,
  SCAN_MODE,
  RESULT_DISPLAY,   // showing ACCESS GRANTED / DENIED
  UID_DISPLAY,       // showing the UID: xxxx screen that follows a result
  DB_BUSY
};

class SystemController {
public:
  void begin();
  void update(); // call every loop()

private:
  DatabaseManager db_;
  DisplayManager display_;
  RFIDManager rfid_;
  SerialProtocol serial_;
  WifiTimeManager network_;
  BuzzerManager buzzer_;

  SystemState state_ = SystemState::BOOT;
  uint32_t stateEnteredMs_ = 0;

  // Pending result data, set when a card is read, consumed by the
  // RESULT_DISPLAY -> UID_DISPLAY transition.
  bool pendingAccessGranted_ = false;
  String pendingName_;
  String pendingUid_;

  bool scanModeActive_ = false;

  // Serial connection tracking (native USB CDC on ESP32-S3 reflects host DTR)
  bool serialWasConnected_ = false;
  bool serialNoticeActive_ = false;
  uint32_t serialNoticeStartMs_ = 0;

  void enterState_(SystemState s);
  void handleCardDetected_(const String& uid);
  void handleSerialMessage_(JsonDocument& doc);
  void handleSerialConnectionChange_();

  // Expiration check per the spec:
  //   expiration_date = registered + valid_days
  //   granted if current_date_time <= expiration_date
  // Fails safe (treated as expired) if time hasn't been NTP-synced yet.
  bool isUserExpired_(const UserRecord& user, bool& timeAvailableOut) const;

  // Wraps any filesystem-mutating DB call: pauses RFID, shows the
  // "DATABASE UPDATING" screen, runs the operation, restores the screen.
  template <typename Fn>
  bool withDbBusyScreen_(Fn&& operation) {
    SystemState previous = state_;
    state_ = SystemState::DB_BUSY;
    display_.showDatabaseUpdating();

    bool result = operation();

    state_ = previous;
    restoreIdleOrScanScreen_();
    return result;
  }

  void restoreIdleOrScanScreen_();
};
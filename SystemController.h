#pragma once
/*
 * SystemController.h
 * Orchestrates DatabaseManager + DisplayManager + RFIDManager + SerialProtocol.
 * This is the only class that knows the full state machine. Everything
 * else is a dumb-ish component with one job.
 *
 * All timing is millis()-based. There is exactly one delay() in this whole
 * firmware (see .cpp) and it is 0 -- kept out entirely on purpose so the
 * serial link never stalls while a card result is being displayed.
 */

#include <Arduino.h>
#include "DatabaseManager.h"
#include "DisplayManager.h"
#include "RFIDManager.h"
#include "SerialProtocol.h"

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

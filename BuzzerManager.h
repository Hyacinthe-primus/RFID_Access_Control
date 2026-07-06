#pragma once
/*
 * BuzzerManager.h
 * Drives a passive buzzer on BUZZER_PIN via tone()/noTone(). Single
 * responsibility: play the granted/denied tone sequences. Entirely
 * non-blocking -- update() advances a millis()-based note sequencer so
 * the ~1.5s tone never stalls the serial link or the RFID poll loop
 * (mirrors the project's "no delay() outside one-off setup" convention).
 */

#include <Arduino.h>

class BuzzerManager {
public:
  void begin();
  void update();           // call every loop()

  void playGranted();      // pleasant ascending sequence, ~1.5s total
  void playDenied();       // descending/error sequence, ~1.5s total

private:
  struct Note {
    uint16_t freqHz;   // 0 = silent gap
    uint16_t durationMs;
  };

  static const Note kGrantedSeq_[];
  static const uint8_t kGrantedLen_;
  static const Note kDeniedSeq_[];
  static const uint8_t kDeniedLen_;

  const Note* seq_ = nullptr;
  uint8_t seqLen_ = 0;
  uint8_t index_ = 0;
  uint32_t noteStartMs_ = 0;
  bool playing_ = false;

  void startSequence_(const Note* seq, uint8_t len);
  void startNote_(uint8_t idx);
};

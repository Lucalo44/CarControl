// Input scanning shared by both firmware versions: debouncing, joystick auto-repeat, switch edges.
// Events are written as text lines to any Print (Serial, or a buffer the UNO Q forwards).
#pragma once
#include <Arduino.h>

// ---- pin map (change to suit your wiring) ----
const uint8_t PIN_JOY_UP = 2;
const uint8_t PIN_JOY_DOWN = 3;
const uint8_t PIN_JOY_LEFT = 4;
const uint8_t PIN_JOY_RIGHT = 5;
const uint8_t PIN_JOY_PRESS = 6;
const uint8_t PIN_AC_BUTTON = 7;
const uint8_t PIN_SWITCH[3] = {8, 9, 10};  // 1 = driver seat, 2 = passenger seat, 3 = steering wheel

const unsigned long DEBOUNCE_MS = 20;
const unsigned long REPEAT_DELAY_MS = 400;
const unsigned long REPEAT_EVERY_MS = 150;

struct Input {
  uint8_t pin;
  const char* event;   // line to send on press, or the switch number for switches
  bool repeats;        // joystick directions repeat while held
  bool isSwitch;
  bool stable;         // debounced state, true = closed (LOW)
  bool raw;
  unsigned long changedAt;
  unsigned long nextRepeat;
};

static Input inputs[] = {
  {PIN_JOY_UP, "JOY UP", true, false},
  {PIN_JOY_DOWN, "JOY DOWN", true, false},
  {PIN_JOY_LEFT, "JOY LEFT", true, false},
  {PIN_JOY_RIGHT, "JOY RIGHT", true, false},
  {PIN_JOY_PRESS, "JOY PRESS", false, false},
  {PIN_AC_BUTTON, "BTN AC", false, false},
  {PIN_SWITCH[0], "1", false, true},
  {PIN_SWITCH[1], "2", false, true},
  {PIN_SWITCH[2], "3", false, true},
};
const size_t N_INPUTS = sizeof(inputs) / sizeof(inputs[0]);

inline void inputsBegin() {
  for (size_t i = 0; i < N_INPUTS; i++) {
    pinMode(inputs[i].pin, INPUT_PULLUP);
    inputs[i].raw = inputs[i].stable = (digitalRead(inputs[i].pin) == LOW);
    inputs[i].changedAt = millis();
  }
}

inline void sendSwitch(Print& out, const Input& in) {
  out.print("SW ");
  out.print(in.event);
  out.println(in.stable ? " ON" : " OFF");
}

inline void sendHello(Print& out) {
  out.println("HELLO CarControl 1");
  for (size_t i = 0; i < N_INPUTS; i++)
    if (inputs[i].isSwitch) sendSwitch(out, inputs[i]);
}

inline void pollInputs(Print& out) {
  unsigned long now = millis();
  for (size_t i = 0; i < N_INPUTS; i++) {
    Input& in = inputs[i];
    bool closed = (digitalRead(in.pin) == LOW);
    if (closed != in.raw) {  // contact moved: restart the debounce timer
      in.raw = closed;
      in.changedAt = now;
    }
    if (in.raw != in.stable && now - in.changedAt >= DEBOUNCE_MS) {
      in.stable = in.raw;
      if (in.isSwitch) {
        sendSwitch(out, in);
      } else if (in.stable) {  // buttons and joystick fire on press, not release
        out.println(in.event);
        in.nextRepeat = now + REPEAT_DELAY_MS;
      }
    } else if (in.repeats && in.stable && (long)(now - in.nextRepeat) >= 0) {
      out.println(in.event);
      in.nextRepeat = now + REPEAT_EVERY_MS;
    }
  }
}

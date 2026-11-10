// Input scanning shared by both firmware versions: debouncing, joystick auto-repeat, switch edges.
// Events are written as text lines to any Print (Serial, or a buffer the UNO Q forwards).
//
// The joystick is an analog thumb stick (KY-023 style: GND, +5V, VRx, VRy, SW). Each axis is a
// potentiometer, so pushing it past a threshold counts as a press of that direction. From there on
// it's treated exactly like a button: debounced, and repeating while held.
#pragma once
#include <Arduino.h>

// ---- pin map (change to suit your wiring) ----
const uint8_t PIN_JOY_X = A0;      // VRx
const uint8_t PIN_JOY_Y = A1;      // VRy
const uint8_t PIN_JOY_PRESS = 2;   // SW (closes to GND when the stick is clicked)
const uint8_t PIN_SWITCH[3] = {3, 4, 5};  // 1 = driver seat, 2 = passenger seat, 3 = steering wheel

// ---- joystick orientation: flip these if a direction comes out backwards ----
const bool JOY_SWAP_XY = false;   // true if left/right and up/down are swapped
const bool JOY_INVERT_X = false;  // true if left and right are reversed
const bool JOY_INVERT_Y = true;   // true if up and down are reversed (true for my stick as mounted)

const int ADC_MAX = 1023;         // 10-bit analogRead, the Arduino default
const int JOY_ON_PCT = 50;        // push this far (% of travel from centre) to count as a press...
const int JOY_OFF_PCT = 30;       // ...and come back inside this to count as released

const unsigned long DEBOUNCE_MS = 20;
const unsigned long REPEAT_DELAY_MS = 400;
const unsigned long REPEAT_EVERY_MS = 150;

enum Axis : int8_t { DIGITAL = -1, AXIS_X = 0, AXIS_Y = 1 };

struct Input {
  uint8_t pin;         // digital pin (unused for joystick directions)
  const char* event;   // line to send on press, or the switch number for switches
  int8_t axis;         // DIGITAL, or the joystick axis this direction reads
  int8_t sign;         // +1 or -1: which way along the axis
  bool repeats;        // joystick directions repeat while held
  bool isSwitch;
  bool stable;         // debounced state, true = pressed / closed
  bool raw;
  unsigned long changedAt;
  unsigned long nextRepeat;
};

static Input inputs[] = {
  {0, "JOY UP", AXIS_Y, +1, true, false},
  {0, "JOY DOWN", AXIS_Y, -1, true, false},
  {0, "JOY LEFT", AXIS_X, -1, true, false},
  {0, "JOY RIGHT", AXIS_X, +1, true, false},
  {PIN_JOY_PRESS, "JOY PRESS", DIGITAL, 0, false, false},
  {PIN_SWITCH[0], "1", DIGITAL, 0, false, true},
  {PIN_SWITCH[1], "2", DIGITAL, 0, false, true},
  {PIN_SWITCH[2], "3", DIGITAL, 0, false, true},
};
const size_t N_INPUTS = sizeof(inputs) / sizeof(inputs[0]);

static int joyCentre[2] = {ADC_MAX / 2, ADC_MAX / 2};
static int joyPct[2] = {0, 0};  // current deflection per axis, -100..100

// Deflection from centre as a percentage of the travel available on that side.
inline int axisPct(uint8_t pin, int centre) {
  int d = analogRead(pin) - centre;
  int span = d > 0 ? ADC_MAX - centre : centre;
  return span > 0 ? (int)((long)d * 100 / span) : 0;
}

inline void readJoystick() {
  int x = axisPct(PIN_JOY_X, joyCentre[0]);
  int y = axisPct(PIN_JOY_Y, joyCentre[1]);
  if (JOY_SWAP_XY) { int t = x; x = y; y = t; }
  joyPct[AXIS_X] = JOY_INVERT_X ? -x : x;
  joyPct[AXIS_Y] = JOY_INVERT_Y ? -y : y;
}

inline bool readInput(const Input& in) {
  if (in.axis == DIGITAL) return digitalRead(in.pin) == LOW;
  int own = joyPct[in.axis] * in.sign;
  int other = abs(joyPct[1 - in.axis]);
  // only the axis pushed furthest counts, so a diagonal doesn't change temperature and fan at once
  return own >= (in.raw ? JOY_OFF_PCT : JOY_ON_PCT) && own >= other;
}

// Reads where the stick rests, so leave it untouched while the board starts up.
inline void calibrateJoystick() {
  long sx = 0, sy = 0;
  for (int i = 0; i < 16; i++) {
    sx += analogRead(PIN_JOY_X);
    sy += analogRead(PIN_JOY_Y);
    delay(2);
  }
  joyCentre[0] = sx / 16;
  joyCentre[1] = sy / 16;
}

inline void inputsBegin() {
  for (size_t i = 0; i < N_INPUTS; i++)
    if (inputs[i].axis == DIGITAL) pinMode(inputs[i].pin, INPUT_PULLUP);
  calibrateJoystick();
  readJoystick();
  for (size_t i = 0; i < N_INPUTS; i++) {
    inputs[i].raw = false;
    inputs[i].raw = inputs[i].stable = readInput(inputs[i]);
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
  readJoystick();
  for (size_t i = 0; i < N_INPUTS; i++) {
    Input& in = inputs[i];
    bool closed = readInput(in);
    if (closed != in.raw) {  // contact moved: restart the debounce timer
      in.raw = closed;
      in.changedAt = now;
    }
    if (in.raw != in.stable && now - in.changedAt >= DEBOUNCE_MS) {
      in.stable = in.raw;
      if (in.isSwitch) {
        sendSwitch(out, in);
      } else if (in.stable) {  // the joystick fires on press, not release
        out.println(in.event);
        in.nextRepeat = now + REPEAT_DELAY_MS;
      }
    } else if (in.repeats && in.stable && (long)(now - in.nextRepeat) >= 0) {
      out.println(in.event);
      in.nextRepeat = now + REPEAT_EVERY_MS;
    }
  }
}

// CarControl controller firmware, USB serial version.
//
// For boards whose USB port talks straight to the microcontroller (UNO R3/R4, Nano, Leonardo...).
// Reads an analog thumb joystick and three toggle switches, and sends one event per line
// at 115200 baud (protocol in carcontrol/protocol.py):
//
//   HELLO CarControl 1     at boot, and in reply to "?" from the host
//   JOY UP|DOWN|LEFT|RIGHT joystick pushed; repeats every 150 ms after a 400 ms hold
//   JOY PRESS              joystick clicked (opens and closes the climate menu)
//   SW 1|2|3 ON|OFF        switch changed (all three are also sent after every HELLO)
//
// Wiring (pin map in carcontrol_inputs.h):
//   joystick GND -> GND, +5V -> the board's logic voltage (3.3V on 3.3V boards), VRx -> A0, VRy -> A1, SW -> D2
//   switches 1, 2, 3 between D3, D4, D5 and GND
// The digital pins use the internal pull-ups, so no resistors are needed and a closed contact reads LOW.
// Leave the stick centred while the board starts: it measures its resting position then.

#include "carcontrol_inputs.h"

void setup() {
  Serial.begin(115200);
  inputsBegin();
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 1500) {}  // native-USB boards: give the host a moment to open the port
  sendHello(Serial);
}

void loop() {
  while (Serial.available()) {
    if (Serial.read() == '?') sendHello(Serial);  // host connected late and wants the current state
  }
  pollInputs(Serial);
}

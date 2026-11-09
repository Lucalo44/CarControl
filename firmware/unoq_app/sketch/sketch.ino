// CarControl controller firmware, Arduino UNO Q version (microcontroller side).
//
// On the UNO Q the USB-C port belongs to the Linux processor, not this microcontroller, so events
// can't go straight out over USB serial. Instead each event line is handed to python/main.py on the
// Linux side through the Bridge, and that script serves them to the host over TCP (port 5555).
//
// Same wiring and protocol as firmware/carcontrol_serial. carcontrol_inputs.h is a copy of the one
// there; the test suite checks the two stay identical.

#include <Arduino_RouterBridge.h>
#include "carcontrol_inputs.h"

// A Print that collects characters and forwards each finished line to Linux.
class BridgeLines : public Print {
 public:
  size_t write(uint8_t c) override {
    if (c == '\n') {
      Bridge.notify("board_event", line);
      line = "";
    } else if (c != '\r') {
      line += (char)c;
    }
    return 1;
  }

 private:
  String line;
};

BridgeLines out;

void sendHelloNow() { sendHello(out); }

void setup() {
  Bridge.begin();
  Bridge.provide("send_hello", sendHelloNow);  // Linux side asks for the current state when it starts
  inputsBegin();
  sendHello(out);
}

void loop() {
  pollInputs(out);
}

"""Line protocol between the controller board and the host.

The board sends one ASCII event per line, newline-terminated, at 115200 baud:

    HELLO CarControl 1      board booted (protocol version 1)
    JOY UP|DOWN|LEFT|RIGHT  joystick pushed (repeats while held)
    JOY PRESS               joystick clicked (opens and closes the climate menu)
    SW 1|2|3 ON|OFF         switch position (sent on change, and once at boot)

Anything else (blank lines, "# comments", unknown words) is ignored, so the
board can print debug text without confusing the host.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

PROTOCOL_VERSION = 1


class Kind(Enum):
    HELLO = "HELLO"
    JOY = "JOY"
    SWITCH = "SW"


JOY_DIRECTIONS = ("UP", "DOWN", "LEFT", "RIGHT", "PRESS")


@dataclass(frozen=True)
class Event:
    kind: Kind
    name: str = ""  # a joystick direction, or a switch number as text
    on: bool | None = None  # switches only

    def to_line(self) -> str:
        if self.kind is Kind.HELLO:
            return f"HELLO CarControl {PROTOCOL_VERSION}"
        if self.kind is Kind.SWITCH:
            return f"SW {self.name} {'ON' if self.on else 'OFF'}"
        return f"{self.kind.value} {self.name}"


def parse_line(line: str) -> Event | None:
    """Parse one line from the board. Returns None for anything that isn't a valid event."""
    parts = line.strip().upper().split()
    if not parts or parts[0].startswith("#"):
        return None
    head, args = parts[0], parts[1:]
    if head == "HELLO":
        return Event(Kind.HELLO)
    if head == "JOY" and len(args) == 1 and args[0] in JOY_DIRECTIONS:
        return Event(Kind.JOY, args[0])
    if head == "SW" and len(args) == 2 and args[0] in ("1", "2", "3") and args[1] in ("ON", "OFF"):
        return Event(Kind.SWITCH, args[0], args[1] == "ON")
    return None


class LineBuffer:
    """Turns an arbitrary stream of bytes into complete lines (serial reads split lines anywhere)."""

    def __init__(self, max_line: int = 256):
        self._buf = bytearray()
        self._max = max_line

    def feed(self, data: bytes) -> list[str]:
        self._buf.extend(data)
        lines = []
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                break
            raw = bytes(self._buf[:i])
            del self._buf[: i + 1]
            lines.append(raw.decode("ascii", errors="replace").rstrip("\r"))
        if len(self._buf) > self._max:  # garbage with no newline (wrong baud rate, noise): drop it
            self._buf.clear()
        return lines

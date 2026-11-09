"""Where board events come from: a USB serial port or a TCP socket.

Both are polled (non-blocking), so the GUI can call `poll()` from a timer without threads.
"""

from __future__ import annotations

import socket

from .protocol import Event, LineBuffer, parse_line

BAUD = 115200


class Source:
    name = "source"

    def poll(self) -> list[tuple[str, Event | None]]:
        """Return (raw line, parsed event or None) for every complete line received so far."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class SerialSource(Source):
    """A board on a serial port, e.g. /dev/cu.usbmodem1101 on a Mac or COM4 on Windows.

    Also accepts pyserial URLs such as loop:// (used by the tests).
    """

    def __init__(self, port: str, baud: int = BAUD):
        import serial  # imported here so the simulator runs without pyserial installed

        self.name = port
        self._ser = serial.serial_for_url(port, baudrate=baud, timeout=0)
        self._lines = LineBuffer()

    def poll(self):
        n = self._ser.in_waiting
        data = self._ser.read(n) if n else b""
        return [(ln, parse_line(ln)) for ln in self._lines.feed(data)]

    def write_line(self, line: str) -> None:
        self._ser.write((line + "\n").encode("ascii"))

    def close(self):
        self._ser.close()


class TcpSource(Source):
    """A board that forwards its events over the network (the UNO Q's Linux side does this)."""

    def __init__(self, host: str, port: int, timeout: float = 3.0):
        self.name = f"{host}:{port}"
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.setblocking(False)
        self._lines = LineBuffer()
        self.closed = False

    def poll(self):
        out = []
        while True:
            try:
                data = self._sock.recv(4096)
            except BlockingIOError:
                break
            if not data:  # peer closed
                self.closed = True
                break
            out += [(ln, parse_line(ln)) for ln in self._lines.feed(data)]
        return out

    def close(self):
        self._sock.close()


def list_serial_ports() -> list[tuple[str, str]]:
    """(device, description) for each serial port, with likely Arduino boards first."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = [(p.device, p.description or "") for p in list_ports.comports() if "Bluetooth" not in p.device]
    if any(d.startswith("/dev/cu.") for d, _ in ports):  # macOS lists each port twice; cu.* is the one to open
        ports = [p for p in ports if not p[0].startswith("/dev/tty.")]
    return sorted(ports, key=lambda p: ("usbmodem" not in p[0] and "arduino" not in p[1].lower(), p[0]))

"""A stand-in for the controller board, for testing the host without hardware.

It speaks the same line protocol as the firmware, over either a virtual serial port (macOS/Linux)
or TCP (like the UNO Q), and either plays a scripted demo or takes commands you type.

    python tools/fake_board.py                 # virtual serial port, scripted demo
    python tools/fake_board.py --interactive   # type: up, down, left, right, press, sw1 on, ...
    python tools/fake_board.py --tcp 5555      # serve over TCP instead (connect with --tcp localhost:5555)
"""

from __future__ import annotations

import argparse
import os
import select
import socket
import sys
import threading
import time

DEMO = [  # (seconds to wait first, line)
    (1.0, "JOY PRESS"),  # open the climate menu
    *[(0.35, "JOY DOWN")] * 6,  # cool it down from 70 to 64 °F
    *[(0.35, "JOY RIGHT")] * 3,  # fan 2 -> 5
    (1.2, "JOY PRESS"),  # close it
    (1.2, "SW 1 ON"),
    (1.0, "SW 3 ON"),
    (1.5, "JOY UP"),  # ignored: menu is closed
    (1.0, "JOY PRESS"),
    *[(0.35, "JOY LEFT")] * 2,
    *[(0.35, "JOY UP")] * 4,
    (1.2, "JOY PRESS"),
    (1.5, "SW 1 OFF"),
    (0.8, "SW 2 ON"),
]

ALIASES = {"up": "JOY UP", "down": "JOY DOWN", "left": "JOY LEFT", "right": "JOY RIGHT",
           "press": "JOY PRESS", "click": "JOY PRESS"}


class Board:
    def __init__(self):
        self.switches = {"1": False, "2": False, "3": False}

    def hello_lines(self):
        return ["HELLO CarControl 1"] + [f"SW {n} {'ON' if on else 'OFF'}" for n, on in self.switches.items()]

    def translate(self, cmd: str) -> str | None:
        cmd = cmd.strip().lower()
        if cmd in ALIASES:
            return ALIASES[cmd]
        parts = cmd.replace("sw", "sw ").split()
        if len(parts) == 3 and parts[0] == "sw" and parts[1] in self.switches and parts[2] in ("on", "off"):
            return f"SW {parts[1]} {parts[2].upper()}"
        return None

    def note(self, line: str):
        p = line.split()
        if p[0] == "SW":
            self.switches[p[1]] = p[2] == "ON"


class PtyLink:
    def __init__(self):
        import tty

        self.master, slave = os.openpty()
        tty.setraw(slave)
        self.name = os.ttyname(slave)
        self._slave = slave  # keep open so the port stays alive

    def send(self, line: str):
        os.write(self.master, (line + "\n").encode())

    def requests(self) -> bool:
        """True if the host sent '?' (asking for HELLO and switch states)."""
        r, _, _ = select.select([self.master], [], [], 0)
        return bool(r) and b"?" in os.read(self.master, 1024)


class TcpLink:
    def __init__(self, port: int, board: Board):
        self.name = f"localhost:{port}"
        self.clients: list[socket.socket] = []
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen()

        def accept():
            while True:
                c, _ = srv.accept()
                c.sendall(("\n".join(board.hello_lines()) + "\n").encode())
                self.clients.append(c)

        threading.Thread(target=accept, daemon=True).start()

    def send(self, line: str):
        for c in self.clients[:]:
            try:
                c.sendall((line + "\n").encode())
            except OSError:
                self.clients.remove(c)

    def requests(self) -> bool:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tcp", type=int, metavar="PORT", help="serve over TCP instead of a virtual serial port")
    ap.add_argument("--interactive", action="store_true", help="type commands instead of playing the demo")
    ap.add_argument("--loop", action="store_true", help="repeat the demo forever")
    args = ap.parse_args()

    board = Board()
    if args.tcp:
        link = TcpLink(args.tcp, board)
        print(f"Fake board serving on TCP {link.name}.  Connect with:  python -m carcontrol --tcp {link.name}")
    else:
        if os.name == "nt":
            sys.exit("Virtual serial ports need macOS or Linux; use --tcp on Windows.")
        link = PtyLink()
        print(f"Fake board on {link.name}.  Connect with:  python -m carcontrol --serial {link.name}")

    def send(line):
        board.note(line)
        link.send(line)
        print(f"  sent {line}")

    def answer_requests():
        if link.requests():
            for ln in board.hello_lines():
                link.send(ln)

    if args.interactive:
        print("Commands: up, down, left, right, press, sw1 on, sw2 off, ...  (Ctrl+D to quit)")
        for raw in sys.stdin:
            answer_requests()
            line = board.translate(raw)
            if line:
                send(line)
            elif raw.strip():
                print(f"  ? {raw.strip()}")
        return 0

    print("Waiting 5 s for the host to connect, then playing the demo. Ctrl+C to stop.")
    t_end = time.monotonic() + 5
    while time.monotonic() < t_end:
        answer_requests()
        time.sleep(0.05)
    try:
        while True:
            for wait, line in DEMO:
                t_end = time.monotonic() + wait
                while time.monotonic() < t_end:
                    answer_requests()
                    time.sleep(0.02)
                send(line)
            if not args.loop:
                print("Demo finished. Ctrl+C to quit (the port closes when this exits).")
                while True:
                    answer_requests()
                    time.sleep(0.1)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

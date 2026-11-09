"""CarControl, Arduino UNO Q version (Linux side).

Receives event lines from the microcontroller sketch over the Bridge and serves them to the host
over TCP, one line per event, using the same protocol as the USB serial firmware. Connect from the
host with:

    python -m carcontrol --tcp <board-ip>:5555
    # or over the USB cable:  adb forward tcp:5555 tcp:5555  then  --tcp localhost:5555

A newly connected host gets HELLO plus the current switch positions straight away.
"""

import socket
import threading

from arduino.app_utils import App, Bridge

PORT = 5555
clients: list[socket.socket] = []
switches: dict[str, str] = {}  # "1" -> "SW 1 ON"
lock = threading.Lock()


def broadcast(line: str) -> None:
    data = (line + "\n").encode("ascii", errors="replace")
    with lock:
        for c in clients[:]:
            try:
                c.sendall(data)
            except OSError:
                clients.remove(c)
                c.close()


def on_board_event(line: str) -> None:
    parts = line.split()
    if len(parts) == 3 and parts[0] == "SW":
        switches[parts[1]] = line
    broadcast(line)


def serve() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen()
    print(f"CarControl: serving controller events on TCP port {PORT}")
    while True:
        conn, addr = srv.accept()
        print(f"CarControl: host connected from {addr[0]}")
        hello = ["HELLO CarControl 1"] + [switches[k] for k in sorted(switches)]
        try:
            conn.sendall(("\n".join(hello) + "\n").encode("ascii"))
        except OSError:
            conn.close()
            continue
        with lock:
            clients.append(conn)


Bridge.provide("board_event", on_board_event)
threading.Thread(target=serve, daemon=True).start()
try:
    Bridge.call("send_hello")  # learn the switch positions if the sketch booted before this script
except Exception as e:  # noqa: BLE001
    print(f"CarControl: could not ask the sketch for its state yet ({e})")
App.run()

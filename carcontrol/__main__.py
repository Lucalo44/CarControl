"""python -m carcontrol [--serial PORT | --tcp HOST:PORT] [--headless] [--speed N]"""

from __future__ import annotations

import argparse
import sys
import time


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="carcontrol", description="Car climate controller host and simulator.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--serial", metavar="PORT", help="board on a serial port, e.g. /dev/cu.usbmodem1101 or COM4")
    src.add_argument("--tcp", metavar="HOST:PORT", help="board forwarding events over the network (UNO Q)")
    ap.add_argument("--headless", action="store_true", help="no window: print what each event does")
    ap.add_argument("--speed", type=float, default=10.0, help="cabin simulation speed-up (default 10x)")
    args = ap.parse_args(argv)

    from .sources import SerialSource, TcpSource

    source = None
    if args.serial:
        source = SerialSource(args.serial)
    elif args.tcp:
        host, port = args.tcp.rsplit(":", 1)
        source = TcpSource(host, int(port))

    if not args.headless:
        from .gui import run

        return run(source, args.speed)

    if source is None:
        ap.error("--headless needs --serial or --tcp")
    from .controller import Controller
    from .vehicle import SimulatedVehicle

    vehicle = SimulatedVehicle()
    ctrl = Controller(vehicle)
    if isinstance(source, SerialSource):
        source.write_line("?")  # ask a board that's already running for its switch positions
    print(f"Listening on {source.name}. Ctrl+C to stop.")
    try:
        while True:
            for raw, ev in source.poll():
                if ev is not None:
                    s = vehicle.state()
                    print(f"{raw:<16} → {ctrl.handle(ev).text:<40} [{s.target_temp:.0f} °F, fan {s.fan}, "
                          f"seats {s.seat_heat['driver']}/{s.seat_heat['passenger']}, wheel {s.wheel_heat}]")
            if getattr(source, "closed", False):
                print("Connection closed.")
                return 0
            time.sleep(0.02)
    except KeyboardInterrupt:
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    sys.exit(main())

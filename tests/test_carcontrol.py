from pathlib import Path

import pytest

from carcontrol.controller import Controller
from carcontrol.protocol import Event, Kind, LineBuffer, parse_line
from carcontrol.sources import SerialSource
from carcontrol.vehicle import TEMP_MAX, TEMP_MIN, ClimateState, SimulatedVehicle

ROOT = Path(__file__).resolve().parents[1]


def make():
    v = SimulatedVehicle(ClimateState(target_temp=70.0, fan=2))
    return v, Controller(v)


def feed(ctrl, *lines):
    return [ctrl.handle(parse_line(ln)) for ln in lines]


# ---- protocol ----
@pytest.mark.parametrize("line,expected", [
    ("HELLO CarControl 1", Event(Kind.HELLO)),
    ("joy up\r", Event(Kind.JOY, "UP")),
    ("JOY PRESS", Event(Kind.JOY, "PRESS")),
    ("SW 2 ON", Event(Kind.SWITCH, "2", True)),
    ("SW 3 OFF", Event(Kind.SWITCH, "3", False)),
])
def test_parse_valid(line, expected):
    assert parse_line(line) == expected


@pytest.mark.parametrize("line", ["", "   ", "# debug text", "JOY", "JOY SIDEWAYS", "SW 4 ON", "SW 1 MAYBE", "BTN AC"])
def test_parse_ignores_junk(line):
    assert parse_line(line) is None


def test_round_trip_lines():
    for line in ("JOY PRESS", "JOY LEFT", "SW 1 ON", "SW 3 OFF"):
        assert parse_line(line).to_line() == line


def test_line_buffer_handles_split_reads():
    b = LineBuffer()
    assert b.feed(b"JOY U") == []
    assert b.feed(b"P\r\nJOY PRESS\nSW 1") == ["JOY UP", "JOY PRESS"]
    assert b.feed(b" ON\n") == ["SW 1 ON"]


def test_line_buffer_drops_runaway_garbage():
    b = LineBuffer(max_line=16)
    b.feed(b"\xff" * 100)
    assert b.feed(b"JOY PRESS\n") == ["JOY PRESS"]


# ---- controller ----
def test_joystick_ignored_until_menu_open():
    v, c = make()
    acts = feed(c, "JOY UP", "JOY RIGHT")
    assert not any(a.changed for a in acts)
    assert v.state().target_temp == 70.0 and v.state().fan == 2


def test_menu_controls_temperature_and_fan():
    v, c = make()
    feed(c, "JOY PRESS", "JOY UP", "JOY UP", "JOY DOWN", "JOY RIGHT", "JOY RIGHT", "JOY LEFT")
    assert c.menu_open
    assert v.state().target_temp == 71.0
    assert v.state().fan == 3


def test_joystick_click_toggles_menu():
    _, c = make()
    feed(c, "JOY PRESS")
    assert c.menu_open
    feed(c, "JOY PRESS")
    assert not c.menu_open


def test_joystick_ignored_after_click_closes_menu():
    v, c = make()
    feed(c, "JOY PRESS", "JOY PRESS", "JOY UP")
    assert not c.menu_open and v.state().target_temp == 70.0


def test_limits_hold():
    v, c = make()
    feed(c, "JOY PRESS", *["JOY UP"] * 30, *["JOY RIGHT"] * 10)
    assert v.state().target_temp == TEMP_MAX and v.state().fan == 5
    acts = feed(c, "JOY UP", "JOY RIGHT")
    assert not any(a.changed for a in acts)
    feed(c, *["JOY DOWN"] * 30, *["JOY LEFT"] * 10)
    assert v.state().target_temp == TEMP_MIN and v.state().fan == 0


def test_switches_set_heaters_fully_on_and_off():
    v, c = make()
    feed(c, "SW 1 ON", "SW 2 ON", "SW 3 ON")
    s = v.state()
    assert s.seat_heat == {"driver": 3, "passenger": 3} and s.wheel_heat == 3
    feed(c, "SW 2 OFF", "SW 3 OFF")
    assert s.seat_heat == {"driver": 3, "passenger": 0} and s.wheel_heat == 0


def test_switches_work_with_menu_closed_or_open():
    v, c = make()
    feed(c, "JOY PRESS", "SW 1 ON")
    assert v.state().seat_heat["driver"] == 3 and c.menu_open


def test_hello_marks_board_connected():
    _, c = make()
    feed(c, "HELLO CarControl 1")
    assert c.board_connected


# ---- simulated cabin ----
def test_cabin_approaches_target_faster_with_more_fan():
    def after(fan):
        v = SimulatedVehicle(ClimateState(target_temp=20.0, fan=fan, cabin_temp=30.0))
        for _ in range(60):
            v.step(1.0)
        return v.state().cabin_temp

    assert 20.0 < after(5) < after(1) < 30.0


def test_cabin_drifts_to_outside_with_fan_off():
    v = SimulatedVehicle(ClimateState(fan=0, cabin_temp=20.0), outside_temp=30.0)
    for _ in range(600):
        v.step(1.0)
    assert 24.0 < v.state().cabin_temp < 26.0  # 20 + 10·(1 − e^(−600/900)) ≈ 24.9


# ---- serial transport, end to end ----
def test_serial_source_end_to_end():
    src = SerialSource("loop://")  # pyserial loopback: what we write comes back as if from a board
    v = SimulatedVehicle(ClimateState(target_temp=70.0, fan=2))
    c = Controller(v)
    for line in ("HELLO CarControl 1", "JOY PRESS", "JOY UP", "JOY RIGHT", "JOY PRESS", "SW 3 ON", "# debug"):
        src.write_line(line)
    got = src.poll()
    assert [raw for raw, _ in got][-1] == "# debug" and got[-1][1] is None
    for _, ev in got:
        if ev:
            c.handle(ev)
    s = v.state()
    assert (s.target_temp, s.fan, s.wheel_heat, c.menu_open, c.board_connected) == (71.0, 3, 3, False, True)
    src.close()


# ---- firmware ----
def test_firmware_input_headers_match():
    a = (ROOT / "firmware/carcontrol_serial/carcontrol_inputs.h").read_text()
    b = (ROOT / "firmware/unoq_app/sketch/carcontrol_inputs.h").read_text()
    assert a == b, "keep the two copies of carcontrol_inputs.h identical"

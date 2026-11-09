"""The control logic: board events in, vehicle commands out.

    joystick click   open the climate menu (click again to close it)
    joystick up/down raise/lower the target temperature, 1 °F per step (menu open only)
    joystick l/r     lower/raise the fan speed (menu open only)
    switch 1 / 2     driver / passenger seat heater: fully on when the switch is on, off when off
    switch 3         steering wheel heater: fully on / off

Joystick moves while the menu is closed are ignored, so a bumped stick can't change
the climate; only a deliberate click opens the menu. The switches work at any time, because they're physical on/off positions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import Event, Kind
from .vehicle import FAN_MAX, HEAT_MAX, TEMP_STEP, Vehicle

SWITCH_TARGETS = {"1": ("seat", "driver"), "2": ("seat", "passenger"), "3": ("wheel", None)}


@dataclass
class Action:
    """What the controller did in response to an event, for the log and the display."""
    text: str
    changed: bool = True


class Controller:
    def __init__(self, vehicle: Vehicle):
        self.vehicle = vehicle
        self.menu_open = False
        self.board_connected = False

    def handle(self, event: Event) -> Action:
        k = event.kind
        if k is Kind.HELLO:
            self.board_connected = True
            return Action("Controller board connected", changed=False)
        if k is Kind.SWITCH:
            return self._switch(event.name, bool(event.on))
        if k is Kind.JOY:
            return self._joystick(event.name)
        return Action(f"Ignored {event}", changed=False)

    def _joystick(self, direction: str) -> Action:
        if direction == "PRESS":
            self.menu_open = not self.menu_open
            return Action("Climate menu opened" if self.menu_open else "Climate menu closed")
        if not self.menu_open:
            return Action(f"Joystick {direction.lower()} ignored (climate menu closed)", changed=False)
        s = self.vehicle.state()
        if direction in ("UP", "DOWN"):
            before = s.target_temp
            self.vehicle.set_target_temp(before + (TEMP_STEP if direction == "UP" else -TEMP_STEP))
            after = self.vehicle.state().target_temp
            if after == before:
                return Action(f"Temperature already at {'max' if direction == 'UP' else 'min'} ({after:.0f} °F)", False)
            return Action(f"Temperature {after:.0f} °F")
        if direction in ("LEFT", "RIGHT"):
            before = s.fan
            self.vehicle.set_fan(before + (1 if direction == "RIGHT" else -1))
            after = self.vehicle.state().fan
            if after == before:
                return Action(f"Fan already at {'max' if after == FAN_MAX else 'off'}", changed=False)
            return Action(f"Fan {after}" if after else "Fan off")
        return Action(f"Unknown joystick direction {direction}", changed=False)

    def _switch(self, number: str, on: bool) -> Action:
        target, seat = SWITCH_TARGETS[number]
        level = HEAT_MAX if on else 0
        if target == "seat":
            self.vehicle.set_seat_heat(seat, level)
            return Action(f"{seat.capitalize()} seat heater {'on (max)' if on else 'off'}")
        self.vehicle.set_wheel_heat(level)
        return Action(f"Steering wheel heater {'on (max)' if on else 'off'}")

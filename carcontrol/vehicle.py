"""Vehicle backends: where climate commands end up.

`Vehicle` is the interface the controller talks to. `SimulatedVehicle` is a stand-in
for the car, with a simple cabin-temperature model so the demo responds like a real
cabin would. A real backend would implement the same five methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

TEMP_MIN, TEMP_MAX, TEMP_STEP = 17.0, 27.0, 0.5  # °C
FAN_MAX = 5
HEAT_MAX = 3  # seat and steering wheel heaters: 0 = off, 3 = highest

SEATS = ("driver", "passenger")


@dataclass
class ClimateState:
    target_temp: float = 21.0
    fan: int = 2
    seat_heat: dict | None = None
    wheel_heat: int = 0
    cabin_temp: float = 28.0  # a car that's been parked in the sun

    def __post_init__(self):
        if self.seat_heat is None:
            self.seat_heat = {s: 0 for s in SEATS}


class Vehicle(ABC):
    @abstractmethod
    def state(self) -> ClimateState: ...

    @abstractmethod
    def set_target_temp(self, celsius: float) -> None: ...

    @abstractmethod
    def set_fan(self, level: int) -> None: ...

    @abstractmethod
    def set_seat_heat(self, seat: str, level: int) -> None: ...

    @abstractmethod
    def set_wheel_heat(self, level: int) -> None: ...


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class SimulatedVehicle(Vehicle):
    """Applies commands instantly and moves the cabin temperature toward the target over time."""

    def __init__(self, state: ClimateState | None = None, outside_temp: float = 28.0):
        self._s = state or ClimateState()
        self.outside_temp = outside_temp

    def state(self) -> ClimateState:
        return self._s

    def set_target_temp(self, celsius: float) -> None:
        self._s.target_temp = clamp(round(celsius / TEMP_STEP) * TEMP_STEP, TEMP_MIN, TEMP_MAX)

    def set_fan(self, level: int) -> None:
        self._s.fan = int(clamp(level, 0, FAN_MAX))

    def set_seat_heat(self, seat: str, level: int) -> None:
        if seat not in SEATS:
            raise ValueError(f"unknown seat {seat!r}")
        self._s.seat_heat[seat] = int(clamp(level, 0, HEAT_MAX))

    def set_wheel_heat(self, level: int) -> None:
        self._s.wheel_heat = int(clamp(level, 0, HEAT_MAX))

    def step(self, dt: float) -> None:
        """Advance the cabin model by dt seconds.

        With the fan running, the cabin closes on the target with a time constant of about
        4 min at fan 1 down to about 50 s at fan 5. With the fan off it drifts toward the
        outside temperature over roughly 15 min.
        """
        s = self._s
        if s.fan > 0:
            tau, goal = 240.0 / s.fan, s.target_temp
        else:
            tau, goal = 900.0, self.outside_temp
        s.cabin_temp += (goal - s.cabin_temp) * min(dt / tau, 1.0)

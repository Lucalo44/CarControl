"""Simulator window: a virtual controller on the left, a left-hand-drive Volvo dashboard on the right.

The virtual controller and a real board feed the same Controller, so the simulator doubles as
the host app for hardware: connect a board and its events drive the display exactly like the
on-screen buttons do.
"""

from __future__ import annotations

import shutil
import subprocess
import time

from PySide6 import QtCore, QtGui, QtWidgets

from .controller import Controller
from .protocol import Event, Kind
from .sources import Source, SerialSource, TcpSource, list_serial_ports
from .vehicle import FAN_MAX, HEAT_MAX, TEMP_MAX, TEMP_MIN, SimulatedVehicle

# Colours after Volvo Car UX's dark theme: near-black glass, grey tiles, white type, blue for
# active states and orange for heat.
DASH, GLASS, SHEET, TILE = (QtGui.QColor(c) for c in ("#0d0f12", "#050506", "#1c1d20", "#2a2c30"))
INK, MUTED, DIM = QtGui.QColor("#f4f5f6"), QtGui.QColor("#9a9ea4"), QtGui.QColor("#4a4e55")
ACCENT, WARM, OFF = QtGui.QColor("#5b9cf5"), QtGui.QColor("#ff7a1f"), QtGui.QColor("#3a3d43")
MAP_BG, MAP_ROAD, MAP_PARK, MAP_WATER = (QtGui.QColor(c) for c in ("#1b1e22", "#30353c", "#1f2a22", "#16222f"))
FLASH_S = 0.6
REF_W, REF_H = 768, 1024  # the V60's 9-inch portrait centre display; everything is laid out in these units


# --------------------------------------------------------------------------- #
# Car display
# --------------------------------------------------------------------------- #
class ClimateDisplay(QtWidgets.QWidget):
    """The dashboard of a left-hand-drive Volvo: steering wheel on the left, portrait centre display.

    The centre display follows Volvo Car UX (the Google built-in interface on the V60): a status line,
    a map filling the home screen, a fixed climate bar along the bottom with the driver's side on the
    left, and a climate sheet that slides up over the map when the joystick is clicked.
    """

    def __init__(self, ctrl: Controller, vehicle: SimulatedVehicle):
        super().__init__()
        self.ctrl, self.vehicle = ctrl, vehicle
        self.flash: dict[str, float] = {}  # field -> time it last changed, for a brief highlight
        self.sheet = 0.0  # climate sheet position, 0 = hidden, 1 = fully open
        self.setMinimumSize(640, 520)

    def mark(self, field: str):
        self.flash[field] = time.monotonic()

    def _hot(self, field: str) -> bool:
        return time.monotonic() - self.flash.get(field, -9) < FLASH_S

    def animate(self, dt: float):
        goal = 1.0 if self.ctrl.menu_open else 0.0
        self.sheet += (goal - self.sheet) * min(dt / 0.08, 1.0)
        if abs(goal - self.sheet) < 0.01:
            self.sheet = goal

    # ---- drawing helpers (all sizes in centre-display units, scaled by self.k) ----
    def _font(self, px: float, weight=QtGui.QFont.Normal) -> QtGui.QFont:
        f = QtGui.QFont()
        f.setPixelSize(max(int(px * self.k), 1))
        f.setWeight(weight)
        return f

    def R(self, x, y, w, h) -> QtCore.QRectF:
        """A rect in centre-display units, mapped onto the widget."""
        k = self.k
        return QtCore.QRectF(self.ox + x * k, self.oy + y * k, w * k, h * k)

    def P(self, x, y) -> QtCore.QPointF:
        return QtCore.QPointF(self.ox + x * self.k, self.oy + y * self.k)

    def _text(self, p, rect, text, px, color=INK, align=QtCore.Qt.AlignCenter, weight=QtGui.QFont.Normal):
        p.setFont(self._font(px, weight))
        p.setPen(color)
        p.drawText(rect, align, text)

    def _pen(self, color, width):
        pen = QtGui.QPen(color, max(width * self.k, 1.0))
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        return pen

    def _seat(self, p, cx, cy, size, level, facing_right=True):
        """A seat in profile, with heat waves rising from the cushion when it's on."""
        col = WARM if level else MUTED
        s = -1 if facing_right else 1  # the backrest sits behind the occupant
        p.setPen(self._pen(col, size * 0.09))
        p.setBrush(QtCore.Qt.NoBrush)
        path = QtGui.QPainterPath()
        path.moveTo(self.P(cx + s * size * 0.30, cy - size * 0.45))
        path.lineTo(self.P(cx + s * size * 0.18, cy + size * 0.18))
        path.lineTo(self.P(cx - s * size * 0.30, cy + size * 0.18))
        path.lineTo(self.P(cx - s * size * 0.36, cy + size * 0.40))
        p.drawPath(path)
        self._waves(p, cx - s * size * 0.06, cy - size * 0.05, size * 0.42, level)

    def _wheel(self, p, cx, cy, size, level):
        col = WARM if level else MUTED
        p.setPen(self._pen(col, size * 0.08))
        p.setBrush(QtCore.Qt.NoBrush)
        r = size * 0.42
        p.drawEllipse(self.P(cx, cy), r * self.k, r * self.k)
        p.drawLine(self.P(cx - r, cy), self.P(cx + r, cy))
        p.drawLine(self.P(cx, cy), self.P(cx, cy + r))
        p.setBrush(col)
        p.drawEllipse(self.P(cx, cy), r * 0.22 * self.k, r * 0.22 * self.k)

    def _waves(self, p, cx, bottom, h, level):
        """Three small heat waves centred on cx, lit up to `level`."""
        for i in range(HEAT_MAX):
            if i >= level:
                continue
            x = cx + (i - 1) * h * 0.38
            p.setPen(self._pen(WARM, h * 0.13))
            path = QtGui.QPainterPath(self.P(x, bottom))
            for j in range(3):
                path.quadTo(self.P(x + (h * 0.14 if j % 2 == 0 else -h * 0.14), bottom - (j + 0.5) * h / 3),
                            self.P(x, bottom - (j + 1) * h / 3))
            p.drawPath(path)

    def _fan_icon(self, p, cx, cy, size, on):
        col = INK if on else MUTED
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        for ang in (90, 210, 330):
            p.save()
            p.translate(self.P(cx, cy))
            p.rotate(-ang)
            p.drawEllipse(QtCore.QRectF(size * 0.06 * self.k, -size * 0.11 * self.k, size * 0.40 * self.k, size * 0.22 * self.k))
            p.restore()
        p.setBrush(SHEET)
        p.setPen(self._pen(col, size * 0.05))
        p.drawEllipse(self.P(cx, cy), size * 0.08 * self.k, size * 0.08 * self.k)

    def _pips(self, p, cx, y, level, n=HEAT_MAX, w=18, gap=6, color=WARM):
        x0 = cx - (n * w + (n - 1) * gap) / 2
        p.setPen(QtCore.Qt.NoPen)
        for i in range(n):
            p.setBrush(color if i < level else OFF)
            p.drawRoundedRect(self.R(x0 + i * (w + gap), y, w, 5), 2 * self.k, 2 * self.k)

    # ---- dashboard ----
    def _steering_wheel(self, p, scr: QtCore.QRectF, level):
        """The driver's wheel left of the centre display, with the driver display seen through it."""
        r = min(scr.height() * 0.30, scr.left() * 0.40)
        c = QtCore.QPointF(scr.left() * 0.48, scr.top() + scr.height() * 0.58)
        # driver display, framed by the top half of the rim
        dd = QtCore.QRectF(c.x() - r * 0.60, c.y() - r * 0.84, r * 1.20, r * 0.52)
        p.setPen(QtGui.QPen(QtGui.QColor("#22252a"), 2))
        p.setBrush(GLASS)
        p.drawRoundedRect(dd, 8, 8)
        f = QtGui.QFont()
        f.setPixelSize(max(int(dd.height() * 0.46), 1))
        f.setWeight(QtGui.QFont.Light)
        p.setFont(f)
        p.setPen(INK)
        p.drawText(dd.adjusted(0, 0, 0, -dd.height() * 0.18), QtCore.Qt.AlignCenter, "0")
        f.setPixelSize(max(int(dd.height() * 0.16), 1))
        p.setFont(f)
        p.setPen(MUTED)
        p.drawText(dd.adjusted(0, 0, 0, -dd.height() * 0.08), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom, "mph")
        if level:
            glow = QtGui.QRadialGradient(c, r * 1.25)
            glow.setColorAt(0.72, QtGui.QColor(255, 122, 31, 0))
            glow.setColorAt(0.82, QtGui.QColor(255, 122, 31, 90))
            glow.setColorAt(0.95, QtGui.QColor(255, 122, 31, 0))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, r * 1.25, r * 1.25)
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(QtGui.QPen(QtGui.QColor("#5a3a22") if level else QtGui.QColor("#2a2d33"), r * 0.13))
        p.drawEllipse(c, r, r)
        p.setPen(QtGui.QPen(QtGui.QColor("#24272c"), r * 0.11))
        for ang in (180, 0, 270):
            v = QtCore.QLineF.fromPolar(r, ang)
            p.drawLine(c, c + QtCore.QPointF(v.dx(), v.dy()))  # Qt's y runs down, so 270° is the bottom spoke
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#24272c"))
        p.drawEllipse(c, r * 0.28, r * 0.28)
        f.setPixelSize(max(int(r * 0.11), 1))
        f.setWeight(QtGui.QFont.Normal)
        p.setFont(f)
        p.setPen(DIM)
        p.drawText(QtCore.QRectF(c.x() - r, c.y() + r * 1.12, 2 * r, r * 0.3), QtCore.Qt.AlignCenter,
                   "Driver (left-hand drive)")

    # ---- centre display ----
    def _map(self, p):
        p.fillRect(self.R(0, 48, REF_W, REF_H - 48), MAP_BG)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(MAP_WATER)
        water = QtGui.QPainterPath(self.P(0, 640))
        water.cubicTo(self.P(180, 600), self.P(260, 760), self.P(470, 720))
        water.cubicTo(self.P(620, 690), self.P(700, 800), self.P(768, 780))
        water.lineTo(self.P(768, 900))
        water.lineTo(self.P(0, 900))
        p.drawPath(water)
        p.setBrush(MAP_PARK)
        p.drawRoundedRect(self.R(470, 160, 190, 150), 20 * self.k, 20 * self.k)
        p.setBrush(QtCore.Qt.NoBrush)
        for (x1, y1, x2, y2, w) in ((0, 210, 768, 150, 14), (0, 470, 768, 520, 18), (140, 48, 220, 700, 12),
                                    (420, 48, 380, 690, 16), (600, 48, 690, 650, 10), (0, 340, 420, 330, 9),
                                    (380, 400, 768, 360, 9), (60, 48, 40, 640, 7), (220, 600, 768, 600, 7)):
            p.setPen(self._pen(MAP_ROAD, w))
            p.drawLine(self.P(x1, y1), self.P(x2, y2))
        route = QtGui.QPainterPath(self.P(384, 560))
        route.lineTo(self.P(399, 498))
        route.lineTo(self.P(720, 364))
        route.lineTo(self.P(712, 200))
        p.setPen(self._pen(ACCENT, 11))
        p.drawPath(route)
        # position chevron
        p.setPen(self._pen(INK, 4))
        p.setBrush(ACCENT)
        chev = QtGui.QPolygonF([self.P(384, 536), self.P(404, 584), self.P(384, 572), self.P(364, 584)])
        p.drawPolygon(chev)
        # next-turn card
        card = self.R(20, 64, 330, 92)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(28, 29, 32, 235))
        p.drawRoundedRect(card, 16 * self.k, 16 * self.k)
        self._text(p, self.R(40, 70, 70, 80), "↗", 46, INK)
        self._text(p, self.R(112, 74, 230, 40), "0.2 mi", 28, INK, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   QtGui.QFont.DemiBold)
        self._text(p, self.R(112, 110, 230, 34), "Turn right onto Main St", 18, MUTED,
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

    def _status(self, p, s):
        p.fillRect(self.R(0, 0, REF_W, 48), GLASS)
        mid = QtCore.Qt.AlignVCenter
        self._text(p, self.R(24, 0, 300, 48), time.strftime("%H:%M"), 20, INK, QtCore.Qt.AlignLeft | mid,
                   QtGui.QFont.DemiBold)
        self._text(p, self.R(0, 0, REF_W, 48), f"Cabin {s.cabin_temp:.0f}°F", 18, MUTED)
        self._text(p, self.R(REF_W - 324, 0, 300, 48), f"{self.vehicle.outside_temp:.0f}°F", 20, INK,
                   QtCore.Qt.AlignRight | mid)

    def _climate_bar(self, p, s):
        top = REF_H - 112
        p.fillRect(self.R(0, top, REF_W, 112), GLASS)
        p.fillRect(self.R(0, top, REF_W, 1), TILE)
        cy = top + 56
        hot_t = self._hot("temp")
        tcol = WARM if hot_t else INK
        # driver's side on the left (left-hand drive), passenger on the right
        self._text(p, self.R(12, top, 150, 112), f"{s.target_temp:.0f}°", 44, tcol, weight=QtGui.QFont.Light)
        self._seat(p, 192, cy, 54, s.seat_heat["driver"], facing_right=True)
        self._wheel(p, 280, cy, 50, s.wheel_heat)
        # climate button in the middle: fan icon plus the current level
        btn = self.R(REF_W / 2 - 56, top + 16, 112, 80)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(ACCENT if self.ctrl.menu_open else TILE)
        p.drawRoundedRect(btn, 18 * self.k, 18 * self.k)
        self._fan_icon(p, REF_W / 2 - 16, cy, 50, s.fan > 0)
        self._text(p, self.R(REF_W / 2 + 4, top + 16, 44, 80), str(s.fan) if s.fan else "Off", 24 if s.fan else 18,
                   INK, weight=QtGui.QFont.DemiBold)
        self._seat(p, 578, cy, 54, s.seat_heat["passenger"], facing_right=False)
        self._text(p, self.R(REF_W - 162, top, 150, 112), f"{s.target_temp:.0f}°", 44, tcol, weight=QtGui.QFont.Light)

    def _climate_sheet(self, p, s):
        h = 600
        top = REF_H - 112 - h * self.sheet
        p.save()
        p.setClipRect(self.R(0, 48, REF_W, REF_H - 160))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(SHEET)
        p.drawRoundedRect(self.R(0, top, REF_W, h + 40), 28 * self.k, 28 * self.k)
        p.setBrush(DIM)
        p.drawRoundedRect(self.R(REF_W / 2 - 30, top + 10, 60, 5), 3 * self.k, 3 * self.k)
        left = QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
        self._text(p, self.R(32, top + 26, 300, 44), "Climate", 30, INK, left, QtGui.QFont.DemiBold)
        self._text(p, self.R(REF_W - 332, top + 26, 300, 44), "Click to close", 17, MUTED,
                   QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        # temperature: one setpoint shown for both zones, like Volvo's synced mode
        hot_t = self._hot("temp")
        y = top + 92
        for x, label, main in ((32, "Driver", True), (REF_W / 2 + 12, "Passenger", False)):
            box = self.R(x, y, REF_W / 2 - 44, 200)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(TILE)
            p.drawRoundedRect(box, 20 * self.k, 20 * self.k)
            self._text(p, self.R(x + 20, y + 14, 200, 30), label, 17, MUTED, left)
            if not main:
                self._text(p, self.R(x + REF_W / 2 - 160, y + 14, 96, 30), "Synced", 15, ACCENT,
                           QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            col = WARM if hot_t else (INK if main else MUTED)
            self._text(p, self.R(x, y + 40, REF_W / 2 - 44, 120), f"{s.target_temp:.0f}°", 80, col,
                       weight=QtGui.QFont.Light)
            if main:
                self._text(p, self.R(x + REF_W / 2 - 104, y + 48, 40, 40), "▲", 24, MUTED)
                self._text(p, self.R(x + REF_W / 2 - 104, y + 112, 40, 40), "▼", 24, MUTED)
            self._text(p, self.R(x, y + 158, REF_W / 2 - 44, 30), f"{TEMP_MIN:.0f}–{TEMP_MAX:.0f} °F", 15, DIM)

        # fan: a segmented slider across the sheet
        y = top + 316
        hot_f = self._hot("fan")
        self._text(p, self.R(32, y, 200, 30), "Fan", 17, MUTED, left)
        self._text(p, self.R(REF_W - 232, y, 200, 30), f"{s.fan}" if s.fan else "Off", 20,
                   ACCENT if hot_f else INK, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, QtGui.QFont.DemiBold)
        self._text(p, self.R(32, y + 40, 36, 56), "◀", 24, MUTED)
        self._text(p, self.R(REF_W - 68, y + 40, 36, 56), "▶", 24, MUTED)
        n, gap = FAN_MAX, 10
        x0, w = 80, (REF_W - 160 - gap * (FAN_MAX - 1)) / FAN_MAX
        p.setPen(QtCore.Qt.NoPen)
        for i in range(n):
            lit = i < s.fan
            p.setBrush((ACCENT.lighter(120) if hot_f else ACCENT) if lit else TILE)
            p.drawRoundedRect(self.R(x0 + i * (w + gap), y + 50, w, 36), 10 * self.k, 10 * self.k)

        # heaters, in the order they sit in a left-hand-drive car
        y = top + 434
        tiles = (("Driver seat", "Switch 1", s.seat_heat["driver"], "seatL"),
                 ("Steering wheel", "Switch 3", s.wheel_heat, "wheel"),
                 ("Passenger seat", "Switch 2", s.seat_heat["passenger"], "seatR"))
        tw = (REF_W - 64 - 2 * 16) / 3
        for i, (label, sw, level, icon) in enumerate(tiles):
            x = 32 + i * (tw + 16)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#3a2a1f") if level else TILE)
            p.drawRoundedRect(self.R(x, y, tw, 140), 20 * self.k, 20 * self.k)
            cx = x + tw / 2
            if icon == "wheel":
                self._wheel(p, cx, y + 46, 52, level)
            else:
                self._seat(p, cx, y + 46, 52, level, facing_right=(icon == "seatL"))
            self._pips(p, cx, y + 88, level)
            self._text(p, self.R(x, y + 98, tw, 24), label, 16, INK if level else MUTED)
            self._text(p, self.R(x, y + 118, tw, 20), sw, 13, DIM)
        p.restore()

    # ---- paint ----
    def paintEvent(self, _):
        s = self.vehicle.state()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), DASH)

        # portrait centre display, a little right of middle so the wheel fits on the driver's side
        margin = 16
        sh = H - 2 * margin
        sw = sh * REF_W / REF_H
        if sw > W * 0.62:
            sw = W * 0.62
            sh = sw * REF_H / REF_W
        scr = QtCore.QRectF(W * 0.60 - sw / 2, (H - sh) / 2, sw, sh)
        self.k, self.ox, self.oy = sw / REF_W, scr.left(), scr.top()

        self._steering_wheel(p, scr, s.wheel_heat)
        p.setPen(QtGui.QPen(QtGui.QColor("#2a2d33"), 6))
        p.setBrush(GLASS)
        p.drawRoundedRect(scr.adjusted(-6, -6, 6, 6), 14, 14)
        p.setFont(self._font(14))
        p.setPen(DIM)
        label = "Board connected" if self.ctrl.board_connected else "Simulated controller"
        p.drawText(QtCore.QRectF(scr.right() + 14, scr.top(), W - scr.right() - 20, 40),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap, label)

        p.save()
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(scr, 10, 10)
        p.setClipPath(clip)
        self._map(p)
        if self.sheet < 1.0:
            hint = self.R(REF_W / 2 - 200, REF_H - 112 - 70, 400, 50)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(28, 29, 32, 235))
            p.drawRoundedRect(hint, 25 * self.k, 25 * self.k)
            self._text(p, hint, "Click the joystick for climate", 17, MUTED)
        if self.sheet > 0.0:
            self._climate_sheet(p, s)
        self._status(p, s)
        self._climate_bar(p, s)
        p.restore()
        p.end()


# --------------------------------------------------------------------------- #
# Virtual controller
# --------------------------------------------------------------------------- #
class ControllerPanel(QtWidgets.QWidget):
    event = QtCore.Signal(object)  # Event

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)

        box = QtWidgets.QGroupBox("Joystick (click = climate)")
        grid = QtWidgets.QGridLayout(box)
        self.joy = {}
        for name, glyph, r, c in (("UP", "▲", 0, 1), ("LEFT", "◀", 1, 0), ("PRESS", "●", 1, 1),
                                  ("RIGHT", "▶", 1, 2), ("DOWN", "▼", 2, 1)):
            b = QtWidgets.QToolButton()
            b.setText(glyph)
            b.setFixedSize(56, 56)
            b.setAutoRepeat(name != "PRESS")  # holding a direction repeats, like the firmware does
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(150)
            b.setToolTip("Click: open or close the climate menu" if name == "PRESS" else f"Joystick {name.lower()}")
            b.clicked.connect(lambda _=False, n=name: self.event.emit(Event(Kind.JOY, n)))
            grid.addWidget(b, r, c)
            self.joy[name] = b
        lay.addWidget(box)

        box = QtWidgets.QGroupBox("Switches")
        sl = QtWidgets.QVBoxLayout(box)
        self.switches = {}
        for num, label in (("1", "Driver seat heat"), ("2", "Passenger seat heat"), ("3", "Steering wheel heat")):
            cb = QtWidgets.QCheckBox(f"{num}  {label}")
            cb.toggled.connect(lambda on, n=num: self.event.emit(Event(Kind.SWITCH, n, on)))
            sl.addWidget(cb)
            self.switches[num] = cb
        lay.addWidget(box)

        keys = QtWidgets.QLabel("Keys: arrows = joystick,\nReturn/Space = click, 1/2/3 = switches")
        keys.setStyleSheet("color:#8b939c")
        lay.addWidget(keys)
        lay.addStretch()

    def show_switch(self, num: str, on: bool):
        """Mirror a real switch's position without re-emitting it."""
        cb = self.switches[num]
        cb.blockSignals(True)
        cb.setChecked(on)
        cb.blockSignals(False)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QtWidgets.QMainWindow):
    RETRY_S = 2.0

    def __init__(self, source: Source | None = None, speed: float = 10.0, tcp: str | None = None):
        super().__init__()
        self.setWindowTitle("CarControl — climate simulator (left-hand-drive Volvo)")
        self.vehicle = SimulatedVehicle()
        self.ctrl = Controller(self.vehicle)
        self.source: Source | None = None
        self.retry: tuple[str, int] | None = None  # TCP board to keep reconnecting to
        self.next_try = 0.0

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        left = QtWidgets.QVBoxLayout()
        self.panel = ControllerPanel()
        self.panel.event.connect(self.on_event)
        left.addWidget(self.panel)

        conn = QtWidgets.QGroupBox("Hardware")
        cl = QtWidgets.QVBoxLayout(conn)
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setEditable(True)  # type a port, or host:port for a UNO Q over Wi-Fi
        self.port_combo.setToolTip("Serial port (e.g. /dev/cu.usbmodem1101, COM4) or host:port for TCP")
        cl.addWidget(self.port_combo)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("Refresh")
        b.clicked.connect(self.refresh_ports)
        row.addWidget(b)
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connection)
        row.addWidget(self.connect_btn)
        cl.addLayout(row)
        self.conn_label = QtWidgets.QLabel("Not connected (using the on-screen controller)")
        self.conn_label.setWordWrap(True)
        self.conn_label.setStyleSheet("color:#8b939c")
        cl.addWidget(self.conn_label)
        left.addWidget(conn)

        sim = QtWidgets.QGroupBox("Simulation")
        fl = QtWidgets.QFormLayout(sim)
        self.speed = QtWidgets.QComboBox()
        for x in (1, 10, 60):
            self.speed.addItem(f"{x}×", x)
        self.speed.setCurrentIndex([1, 10, 60].index(speed) if speed in (1, 10, 60) else 1)
        fl.addRow("Cabin time:", self.speed)
        self.outside = QtWidgets.QDoubleSpinBox()
        self.outside.setRange(-20, 120)
        self.outside.setDecimals(0)
        self.outside.setValue(self.vehicle.outside_temp)
        self.outside.setSuffix(" °F")
        self.outside.valueChanged.connect(lambda v: setattr(self.vehicle, "outside_temp", v))
        fl.addRow("Outside:", self.outside)
        left.addWidget(sim)

        lw = QtWidgets.QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(280)
        root.addWidget(lw)

        right = QtWidgets.QVBoxLayout()
        self.display = ClimateDisplay(self.ctrl, self.vehicle)
        right.addWidget(self.display, 1)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setFixedHeight(130)
        self.log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        right.addWidget(self.log)
        root.addLayout(right, 1)

        self._shortcuts()
        self.refresh_ports()
        self._last = time.monotonic()
        self.timer = QtCore.QTimer(self, interval=50)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        if source is not None:
            self._attach(source)
        elif tcp:
            self.port_combo.setEditText(tcp)
            self._start_tcp(tcp)
        self.resize(1180, 820)

    # ---- input ----
    def _shortcuts(self):
        keys = {QtCore.Qt.Key_Up: "UP", QtCore.Qt.Key_Down: "DOWN", QtCore.Qt.Key_Left: "LEFT",
                QtCore.Qt.Key_Right: "RIGHT", QtCore.Qt.Key_Return: "PRESS", QtCore.Qt.Key_Enter: "PRESS",
                QtCore.Qt.Key_Space: "PRESS"}
        for key, name in keys.items():
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            sc.setContext(QtCore.Qt.ApplicationShortcut)
            sc.activated.connect(lambda e=Event(Kind.JOY, name): self.on_event(e))
        for num in ("1", "2", "3"):
            sc = QtGui.QShortcut(QtGui.QKeySequence(num), self)
            sc.activated.connect(lambda n=num: self.panel.switches[n].toggle())

    def on_event(self, ev: Event, raw: str | None = None):
        before = self.vehicle.state()
        temp, fan = before.target_temp, before.fan
        act = self.ctrl.handle(ev)
        s = self.vehicle.state()
        if s.target_temp != temp:
            self.display.mark("temp")
        if s.fan != fan:
            self.display.mark("fan")
        if ev.kind is Kind.SWITCH and raw is not None:
            self.panel.show_switch(ev.name, bool(ev.on))
        src = f"board  {raw}" if raw is not None else f"screen {ev.to_line()}"
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {src:<22} → {act.text}")
        self.display.update()

    # ---- hardware connection ----
    def refresh_ports(self):
        cur = self.port_combo.currentText()
        self.port_combo.clear()
        for dev, desc in list_serial_ports():
            self.port_combo.addItem(f"{dev}", dev)
            self.port_combo.setItemData(self.port_combo.count() - 1, desc, QtCore.Qt.ToolTipRole)
        if cur:
            self.port_combo.setEditText(cur)

    def toggle_connection(self):
        if self.source or self.retry:
            self.retry = None
            self._detach("Disconnected")
            return
        target = self.port_combo.currentText().strip()
        if not target:
            self.conn_label.setText("Pick or type a serial port first.")
            return
        if ":" in target and not target.startswith(("/", "COM", "loop:", "socket:", "rfc2217:")):
            self._start_tcp(target)
            return
        try:
            src = SerialSource(target)
        except Exception as e:  # noqa: BLE001
            self.conn_label.setText(f"Could not open {target}: {e}")
            return
        self._attach(src)

    def _start_tcp(self, target: str):
        """Connect to a board over TCP, and keep retrying until it answers (and after it drops)."""
        host, port = target.rsplit(":", 1)
        self.retry = (host, int(port))
        self.next_try = 0.0
        self.connect_btn.setText("Stop")
        self._try_tcp()

    def _try_tcp(self):
        host, port = self.retry
        self.next_try = time.monotonic() + self.RETRY_S
        if host in ("localhost", "127.0.0.1") and shutil.which("adb"):
            # a UNO Q over USB: the adb port forward disappears when the board reboots, so renew it
            subprocess.run(["adb", "forward", f"tcp:{port}", f"tcp:{port}"], capture_output=True, timeout=3)
        try:
            src = TcpSource(host, port, timeout=1.0)
        except OSError:
            self.conn_label.setText(f"Waiting for the board on {host}:{port}…")
            return
        self._attach(src)

    def _attach(self, src: Source):
        self.source = src
        if isinstance(src, SerialSource):
            src.write_line("?")  # a board that booted earlier resends HELLO and its switch positions
        self.connect_btn.setText("Disconnect")
        self.conn_label.setText(f"Connected to {src.name}. Waiting for the board…")

    def _detach(self, why: str):
        if self.source:
            self.source.close()
        self.source = None
        self.ctrl.board_connected = False
        self.connect_btn.setText("Stop" if self.retry else "Connect")
        self.conn_label.setText(why + (" Retrying…" if self.retry else ""))
        self.display.update()

    # ---- loop ----
    def tick(self):
        now = time.monotonic()
        self.vehicle.step((now - self._last) * self.speed.currentData())
        self.display.animate(now - self._last)
        self._last = now
        if self.source:
            try:
                for raw, ev in self.source.poll():
                    if ev is None:
                        if raw.strip() and not raw.lstrip().startswith("#"):
                            self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  board  {raw:<15} → (not understood)")
                        continue
                    self.on_event(ev, raw)
                    if ev.kind is Kind.HELLO:
                        self.conn_label.setText(f"Board connected on {self.source.name}")
                if getattr(self.source, "closed", False):
                    self._detach("The board closed the connection.")
            except Exception as e:  # noqa: BLE001  (unplugged mid-session, etc.)
                self._detach(f"Connection lost: {e}")
        elif self.retry and now >= self.next_try:
            self._try_tcp()
        self.display.update()

    def closeEvent(self, ev):
        if self.source:
            self.source.close()
        super().closeEvent(ev)


def run(source: Source | None = None, speed: float = 10.0, tcp: str | None = None) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("CarControl")
    w = MainWindow(source, speed, tcp)
    w.show()
    return app.exec()

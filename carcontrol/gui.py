"""Simulator window: a virtual controller on the left, a stand-in car display on the right.

The virtual controller and a real board feed the same Controller, so the simulator doubles as
the host app for hardware: connect a board and its events drive the display exactly like the
on-screen buttons do.
"""

from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from .controller import Controller
from .protocol import Event, Kind
from .sources import Source, SerialSource, TcpSource, list_serial_ports
from .vehicle import FAN_MAX, HEAT_MAX, TEMP_MAX, TEMP_MIN, SimulatedVehicle

BG, PANEL, INK, MUTED = QtGui.QColor("#101317"), QtGui.QColor("#1b2027"), QtGui.QColor("#eef0f2"), QtGui.QColor("#8b939c")
COOL, WARM, OFF = QtGui.QColor("#4ea1ff"), QtGui.QColor("#ff8a3d"), QtGui.QColor("#3a4149")
FLASH_S = 0.6


# --------------------------------------------------------------------------- #
# Car display
# --------------------------------------------------------------------------- #
class ClimateDisplay(QtWidgets.QWidget):
    """Paints a generic centre display: a status line, the AC menu overlay, and the climate bar."""

    def __init__(self, ctrl: Controller, vehicle: SimulatedVehicle):
        super().__init__()
        self.ctrl, self.vehicle = ctrl, vehicle
        self.flash: dict[str, float] = {}  # field -> time it last changed, for a brief highlight
        self.setMinimumSize(720, 470)

    def mark(self, field: str):
        self.flash[field] = time.monotonic()

    def _hot(self, field: str) -> bool:
        return time.monotonic() - self.flash.get(field, -9) < FLASH_S

    # ---- drawing helpers ----
    @staticmethod
    def _font(px: float, weight=QtGui.QFont.Normal) -> QtGui.QFont:
        f = QtGui.QFont()
        f.setPixelSize(max(int(px), 1))
        f.setWeight(weight)
        return f

    def _text(self, p, rect, text, px, color=INK, align=QtCore.Qt.AlignCenter, weight=QtGui.QFont.Normal):
        p.setFont(self._font(px, weight))
        p.setPen(color)
        p.drawText(rect, align, text)

    def _heat_waves(self, p, x, y, h, level):
        """Three wavy lines, lit up to `level`."""
        for i in range(HEAT_MAX):
            col = WARM if i < level else OFF
            pen = QtGui.QPen(col, max(h * 0.09, 2))
            pen.setCapStyle(QtCore.Qt.RoundCap)
            p.setPen(pen)
            path = QtGui.QPainterPath()
            cx = x + i * h * 0.32
            path.moveTo(cx, y + h)
            for k in range(4):
                path.quadTo(cx + (h * 0.12 if k % 2 == 0 else -h * 0.12), y + h - (k + 0.5) * h / 4, cx, y + h - (k + 1) * h / 4)
            p.drawPath(path)

    def _seat(self, p, x, y, h, level, label):
        col = WARM if level else MUTED
        p.setPen(QtGui.QPen(col, max(h * 0.07, 2)))
        p.setBrush(QtCore.Qt.NoBrush)
        back = QtGui.QPainterPath()
        back.moveTo(x + h * 0.15, y)
        back.lineTo(x + h * 0.25, y + h * 0.62)
        back.lineTo(x + h * 0.75, y + h * 0.62)
        back.lineTo(x + h * 0.8, y + h * 0.8)
        p.drawPath(back)
        self._heat_waves(p, x + h * 0.95, y + h * 0.05, h * 0.75, level)
        self._text(p, QtCore.QRectF(x - h * 0.2, y + h * 0.85, h * 1.9, h * 0.4), label, h * 0.22, MUTED)

    def _wheel(self, p, x, y, h, level):
        col = WARM if level else MUTED
        p.setPen(QtGui.QPen(col, max(h * 0.07, 2)))
        p.setBrush(QtCore.Qt.NoBrush)
        r = h * 0.38
        c = QtCore.QPointF(x + r, y + h * 0.42)
        p.drawEllipse(c, r, r)
        p.drawEllipse(c, r * 0.25, r * 0.25)
        for ang in (180, 0, 270):
            v = QtCore.QLineF.fromPolar(r, ang)
            p.drawLine(c + QtCore.QPointF(v.dx(), -v.dy()) * 0.25, c + QtCore.QPointF(v.dx(), -v.dy()))
        self._heat_waves(p, x + h * 0.95, y + h * 0.05, h * 0.75, level)
        self._text(p, QtCore.QRectF(x - h * 0.2, y + h * 0.85, h * 1.9, h * 0.4), "Wheel", h * 0.22, MUTED)

    def _fan_bars(self, p, rect: QtCore.QRectF, level, hot=False):
        n, gap = FAN_MAX, rect.width() * 0.04
        w = (rect.width() - gap * (n - 1)) / n
        for i in range(n):
            bh = rect.height() * (0.35 + 0.65 * (i + 1) / n)
            r = QtCore.QRectF(rect.left() + i * (w + gap), rect.bottom() - bh, w, bh)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(COOL.lighter(125) if (i < level and hot) else COOL if i < level else OFF)
            p.drawRoundedRect(r, 3, 3)

    # ---- paint ----
    def paintEvent(self, _):
        s = self.vehicle.state()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), BG)
        u = min(W / 900, H / 560)  # layout unit, so everything scales with the window

        # status line
        self._text(p, QtCore.QRectF(24 * u, 12 * u, W - 48 * u, 30 * u),
                   f"Cabin {s.cabin_temp:.1f} °C   ·   Outside {self.vehicle.outside_temp:.0f} °C",
                   16 * u, MUTED, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._text(p, QtCore.QRectF(24 * u, 12 * u, W - 48 * u, 30 * u),
                   "Board connected" if self.ctrl.board_connected else "Simulated controller",
                   16 * u, MUTED, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        bar_h = 120 * u
        bar = QtCore.QRectF(0, H - bar_h, W, bar_h)

        # AC menu overlay, or a resting home screen
        body = QtCore.QRectF(40 * u, 56 * u, W - 80 * u, H - bar_h - 76 * u)
        if self.ctrl.menu_open:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(PANEL)
            p.drawRoundedRect(body, 18 * u, 18 * u)
            self._text(p, QtCore.QRectF(body.left() + 28 * u, body.top() + 16 * u, 400 * u, 40 * u), "Climate",
                       26 * u, INK, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, QtGui.QFont.DemiBold)
            half = body.width() / 2
            # temperature column
            tcol = QtCore.QRectF(body.left(), body.top() + 60 * u, half, body.height() - 110 * u)
            self._text(p, QtCore.QRectF(tcol.left(), tcol.top(), tcol.width(), 34 * u), "▲", 26 * u, MUTED)
            hot = self._hot("temp")
            self._text(p, QtCore.QRectF(tcol.left(), tcol.top() + 30 * u, tcol.width(), tcol.height() - 90 * u),
                       f"{s.target_temp:.1f}°", 92 * u, WARM if hot else INK, weight=QtGui.QFont.Light)
            self._text(p, QtCore.QRectF(tcol.left(), tcol.bottom() - 58 * u, tcol.width(), 34 * u), "▼", 26 * u, MUTED)
            self._text(p, QtCore.QRectF(tcol.left(), tcol.bottom() - 24 * u, tcol.width(), 24 * u),
                       f"Temperature  ({TEMP_MIN:.0f}–{TEMP_MAX:.0f} °C)", 15 * u, MUTED)
            # fan column
            fcol = QtCore.QRectF(body.left() + half, body.top() + 60 * u, half, body.height() - 110 * u)
            bars = QtCore.QRectF(fcol.center().x() - 120 * u, fcol.center().y() - 55 * u, 240 * u, 100 * u)
            self._text(p, QtCore.QRectF(bars.left() - 60 * u, bars.top(), 50 * u, bars.height()), "◀", 26 * u, MUTED)
            self._text(p, QtCore.QRectF(bars.right() + 10 * u, bars.top(), 50 * u, bars.height()), "▶", 26 * u, MUTED)
            self._fan_bars(p, bars, s.fan, self._hot("fan"))
            self._text(p, QtCore.QRectF(fcol.left(), bars.bottom() + 8 * u, fcol.width(), 30 * u),
                       f"Fan {s.fan}" if s.fan else "Fan off", 20 * u, COOL if self._hot("fan") else INK)
            self._text(p, QtCore.QRectF(fcol.left(), tcol.bottom() - 24 * u, fcol.width(), 24 * u),
                       "Fan speed", 15 * u, MUTED)
            self._text(p, QtCore.QRectF(body.left(), body.bottom() - 40 * u, body.width(), 30 * u),
                       "Joystick: ▲▼ temperature   ·   ◀▶ fan   ·   click to close", 15 * u, MUTED)
        else:
            self._text(p, body, "Press the AC button to open climate controls", 20 * u, OFF)

        # climate bar
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(PANEL)
        p.drawRect(bar)
        ih = 64 * u
        iy = bar.top() + (bar_h - ih) / 2 - 8 * u
        self._seat(p, 40 * u, iy, ih, s.seat_heat["driver"], "Driver")
        self._text(p, QtCore.QRectF(W * 0.22, bar.top(), W * 0.2, bar_h), f"{s.target_temp:.1f}°", 44 * u,
                   WARM if self._hot("temp") else INK, weight=QtGui.QFont.Light)
        self._fan_bars(p, QtCore.QRectF(W * 0.46, iy + 8 * u, 110 * u, ih * 0.75), s.fan, self._hot("fan"))
        self._text(p, QtCore.QRectF(W * 0.46, iy + ih * 0.85, 110 * u, ih * 0.4), "Fan", 14 * u, MUTED)
        self._seat(p, W - 260 * u, iy, ih, s.seat_heat["passenger"], "Passenger")
        self._wheel(p, W - 130 * u, iy, ih, s.wheel_heat)
        p.end()


# --------------------------------------------------------------------------- #
# Virtual controller
# --------------------------------------------------------------------------- #
class ControllerPanel(QtWidgets.QWidget):
    event = QtCore.Signal(object)  # Event

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)

        box = QtWidgets.QGroupBox("Joystick (5-way)")
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
            b.setToolTip(f"Joystick {name.lower()}")
            b.clicked.connect(lambda _=False, n=name: self.event.emit(Event(Kind.JOY, n)))
            grid.addWidget(b, r, c)
            self.joy[name] = b
        lay.addWidget(box)

        box = QtWidgets.QGroupBox("Button")
        bl = QtWidgets.QVBoxLayout(box)
        self.ac = QtWidgets.QPushButton("AC")
        self.ac.setMinimumHeight(40)
        self.ac.clicked.connect(lambda: self.event.emit(Event(Kind.BUTTON, "AC")))
        bl.addWidget(self.ac)
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

        keys = QtWidgets.QLabel("Keys: arrows = joystick, Return = click,\nA = AC button, 1/2/3 = switches")
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
    def __init__(self, source: Source | None = None, speed: float = 10.0):
        super().__init__()
        self.setWindowTitle("CarControl — climate simulator")
        self.vehicle = SimulatedVehicle()
        self.ctrl = Controller(self.vehicle)
        self.source: Source | None = None

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
        self.outside.setRange(-30, 50)
        self.outside.setValue(self.vehicle.outside_temp)
        self.outside.setSuffix(" °C")
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
        self.resize(1180, 700)

    # ---- input ----
    def _shortcuts(self):
        keys = {QtCore.Qt.Key_Up: ("JOY", "UP"), QtCore.Qt.Key_Down: ("JOY", "DOWN"),
                QtCore.Qt.Key_Left: ("JOY", "LEFT"), QtCore.Qt.Key_Right: ("JOY", "RIGHT"),
                QtCore.Qt.Key_Return: ("JOY", "PRESS"), QtCore.Qt.Key_Enter: ("JOY", "PRESS"),
                QtCore.Qt.Key_A: ("BTN", "AC")}
        for key, (kind, name) in keys.items():
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            sc.setContext(QtCore.Qt.ApplicationShortcut)
            ev = Event(Kind.JOY if kind == "JOY" else Kind.BUTTON, name)
            sc.activated.connect(lambda e=ev: self.on_event(e))
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
        if self.source:
            self._detach("Disconnected")
            return
        target = self.port_combo.currentText().strip()
        if not target:
            self.conn_label.setText("Pick or type a serial port first.")
            return
        try:
            if ":" in target and not target.startswith(("/", "COM", "loop:", "socket:", "rfc2217:")):
                host, port = target.rsplit(":", 1)
                src = TcpSource(host, int(port))
            else:
                src = SerialSource(target)
        except Exception as e:  # noqa: BLE001
            self.conn_label.setText(f"Could not open {target}: {e}")
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
        self.connect_btn.setText("Connect")
        self.conn_label.setText(why)
        self.display.update()

    # ---- loop ----
    def tick(self):
        now = time.monotonic()
        self.vehicle.step((now - self._last) * self.speed.currentData())
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
        self.display.update()

    def closeEvent(self, ev):
        if self.source:
            self.source.close()
        super().closeEvent(ev)


def run(source: Source | None = None, speed: float = 10.0) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("CarControl")
    w = MainWindow(source, speed)
    w.show()
    return app.exec()

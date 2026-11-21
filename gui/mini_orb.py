"""
VYUK — Floating mini orb (v2.1 Presence pack; Aura-only, Premium Wave 3).

A small always-on-top orb that lives on the desktop while the main
window is hidden: it mirrors VYUK's live state and audio, drags
anywhere, restores its position between runs, and offers Open /
Connect / Hide from a right-click menu. Clicking it brings the main
window back.

Painting — Aura only, drawn locally (v5.45 F7, Aura-only since
2026-11-01 by owner decision). The mini canvas paints the
Aura-language canvas: a deep glass sphere with internal
currents in the palette hues, a bright living core and a fresnel
rim, mirroring gui/visualizer.py's Aura painter at mini scale.
Only the theme
palette is shared with the visualizer (the same theme source the
mini orb has always used); the canvas below is a separate,
lightweight widget with one 33 ms timer and no other machinery.

State is shown the Aura way — by light and motion, not by labels:
idle breathes slowly, listening pulses with the voice, speaking
ripples brighter, thinking spins the ring faster and warms violet,
connecting runs eager, muted dims behind a small slash badge, error
warms red, and disconnected greys out (plus the window's own 0.55
opacity, exactly as before). Mouse events are taken straight on this
window: the canvas is made transparent to the mouse so presses land
here, not on the child.
"""

import math
import random

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from .motion import (
    SPRING_ENERGY_ATTACK,
    SPRING_ENERGY_RELEASE,
)
from .orb_sphere import SphereState, paint_particle_sphere
from .visualizer_glass import (
    GlassState,
    glass_contour_arc,
    glass_contour_path,
    paint_glass_layers,
)
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set_noted

try:  # The palette only — shared theme source, no visualizer widget.
    from .visualizer import THEMES
except Exception:  # noqa: BLE001 — palette copy, kept in sync with visualizer
    THEMES = {
        "Cyan": ("#E6FBFF", "#35D6FF", "#3D8BFF"),
        "Sunset": ("#FFE8D6", "#FF9A7A", "#FF7EB6"),
        "Violet": ("#EFE9FF", "#A78BFA", "#7C8CFF"),
        "Mono": ("#FFFFFF", "#C9CED6", "#8A919C"),
        "Amber": ("#FFF1D6", "#FFB020", "#FF6A00"),
        "Crimson": ("#FFE3E6", "#FF5468", "#FD465A"),
    }

SIZE = 92
_DRAG_THRESHOLD = 4  # px of movement before a press becomes a drag


def _rgb(hex_color: str) -> tuple[float, float, float]:
    c = QColor(hex_color)
    return float(c.red()), float(c.green()), float(c.blue())


class _AuraMiniCanvas(QWidget):
    """Lightweight Aura-language canvas for the 92 px mini orb.

    Same public surface the mini orb used on the old child widget —
    set_state / set_theme / set_orb_style / update_audio_data /
    accent_color — so MiniOrb's forwarding code is unchanged in shape.
    set_custom_theme (v5.17) mirrors the main visualizer: an arbitrary
    palette triple that wins over the named theme until set_theme()
    clears it, so chrome-only Settings themes recolour the mini too.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "DISCONNECTED"
        self.theme_name = "Cyan"
        self._custom_theme: tuple[str, str, str] | None = None
        self.orb_style = "Aura"
        self._energy = 0.0        # smoothed audio energy (fast attack)
        self._target_energy = 0.0
        self._breathe = 0.0       # breathing phase (radians)
        # B-R1-5: the LerSent liquid-glass state — eased in _tick
        # from the SAME state + smoothed energy this canvas already
        # has; the paint pass only reads it.
        self._glass = GlassState()
        # R7-2: the voice-reactive particle sphere, eased in _tick
        # from the same smoothed energy this canvas already has.
        self._sphere = SphereState()
        self._orbit = 0.0         # ring / travelling-dot angle (degrees)
        # v5.45 F7: Aura currents — the visualizer's blob
        # recipe ([angle deg, orbit radius fraction, deg/tick,
        # blob radius fraction, twinkle phase], same seed family).
        _aurng = random.Random(23)
        self._aur_blobs = [
            [_aurng.uniform(0, 360), _aurng.uniform(0.26, 0.50),
             _aurng.uniform(0.08, 0.22) * (-1.0 if i % 2 else 1.0),
             _aurng.uniform(0.36, 0.50), _aurng.uniform(0, 6.283)]
            for i in range(4)
        ]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # ------------------------------------------------------------------ API
    def set_state(self, state: str):
        self.state = str(state or "").upper()

    def set_theme(self, theme_name: str):
        if theme_name in THEMES:
            self.theme_name = theme_name
            # A named palette stays exact: choosing one retires any
            # custom override (the main visualizer's rule).
            self._custom_theme = None

    def set_custom_theme(self, hi: str, mid: str, edge: str):
        """Install a custom palette triple (highlight, mid, edge) —
        the main visualizer's contract at mini scale: wins over the
        named theme in _palette(); any value QColor rejects makes
        the call a no-op; cleared by the next set_theme()."""
        triple = []
        for value in (hi, mid, edge):
            if not isinstance(value, (str, QColor)):
                return
            col = QColor(value)
            if not col.isValid():
                return
            triple.append(col.name())
        self._custom_theme = (triple[0], triple[1], triple[2])

    def canvas_family(self) -> str:
        """Always 'aura': Aura is the only orb style (owner decision
        2026-11-01). Kept as a method so callers need no change."""
        return "aura"

    def set_orb_style(self, style: str):
        # Aura-only (owner decision 2026-11-01): any requested style
        # resolves to Aura, so a saved Apex/Reactor/unknown value can
        # never re-select a deleted style at runtime.
        self.orb_style = "Aura"
        self.update()

    def update_audio_data(self, energy: float, bands=None):
        try:
            self._target_energy = max(0.0, min(1.0, float(energy)))
        except Exception:  # noqa: BLE001
            self._target_energy = 0.0

    def accent_color(self) -> QColor:
        _, mid, _ = self._palette()
        return mid

    # ------------------------------------------------------------- internal
    def _is(self, *names) -> bool:
        return any(n in self.state for n in names)

    def _palette(self):
        """(highlight, mid, edge) for the current state — the visualizer's
        palette rules, at mini scale: error is red, disconnected is grey,
        thinking warms the mid toward violet, everything else is theme."""
        if self._is("ERROR"):
            return QColor("#FFBEBE"), QColor("#F06E6E"), QColor("#AA3746")
        if self._is("DISCONNECTED"):
            return QColor("#BEC6D0"), QColor("#6E7682"), QColor("#464C56")
        hi_s, mid_s, edge_s = self._custom_theme or THEMES.get(
            self.theme_name, THEMES["Cyan"])
        hi, mid, edge = QColor(hi_s), QColor(mid_s), QColor(edge_s)
        if self._is("THINKING"):
            violet = QColor("#A78BFA")
            mid = QColor((mid.red() + violet.red()) // 2,
                         (mid.green() + violet.green()) // 2,
                         (mid.blue() + violet.blue()) // 2)
        if self._is("MUTED"):
            mid = QColor(int(mid.red() * 0.55 + 90), int(mid.green() * 0.55 + 90),
                         int(mid.blue() * 0.55 + 95))
            edge = QColor(int(edge.red() * 0.5 + 80), int(edge.green() * 0.5 + 80),
                          int(edge.blue() * 0.5 + 85))
        return hi, mid, edge

    def _tick(self):
        # Fast attack, graceful release — the main orb's analyser feel.
        k = (SPRING_ENERGY_ATTACK if self._target_energy > self._energy
             else SPRING_ENERGY_RELEASE)  # v5.51 F-3: motion vocabulary
        self._energy += (self._target_energy - self._energy) * k
        if self._is("DISCONNECTED"):
            self._breathe += 0.008
            speed = 0.25
        elif self._is("THINKING"):
            self._breathe += 0.030
            speed = 2.4
        elif self._is("CONNECTING"):
            self._breathe += 0.028
            speed = 1.7
        elif self._is("LISTENING"):
            self._breathe += 0.055
            speed = 1.0
        elif self._is("SPEAKING"):
            self._breathe += 0.045
            speed = 1.4
        elif self._is("ERROR"):
            self._breathe += 0.016
            speed = 0.4
        else:
            self._breathe += 0.022
            speed = 0.6
        self._orbit = (self._orbit + 0.9 * speed) % 360.0
        # Aura currents orbit at their own rates, hurried by the
        # same state speed that spins the mini orb's orbit ring.
        for blob in self._aur_blobs:
            blob[0] = (blob[0] + blob[2] * (0.35 + 0.65 * speed)) % 360.0
        # B-R1-5: the LerSent glass eases toward the same state's
        # targets from the same smoothed energy — s=2.0 (the 33 ms
        # tick is ~2 legacy 60 fps frames). The phase advances with
        # the tick; the paint pass only reads the snapshot.
        self._glass.advance(self.state, self._energy, 2.0)
        # R7-2: the sphere turns and eases its swell toward the
        # same smoothed energy (33 ms tick).
        self._sphere.advance(0.033, self._energy)
        self.update()

    def _paint_muted_badge(self, painter, cx, cy, disc_r, half):
        """The shared muted badge — drawn by BOTH canvases (F7):
        a small dark disc at the body's lower right carrying a
        pale slash and centre dot."""
        bx, by, br = cx + disc_r * 0.62, cy + disc_r * 0.62, half * 0.16
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1.0))
        painter.setBrush(QColor(16, 19, 26, 235))
        painter.drawEllipse(QRectF(bx - br, by - br, br * 2, br * 2))
        slash = QPen(QColor(200, 206, 216, 220), max(1.4, half * 0.035))
        slash.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(slash)
        painter.drawLine(QPointF(bx - br * 0.55, by + br * 0.55),
                         QPointF(bx + br * 0.55, by - br * 0.55))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(200, 206, 216, 220))
        painter.drawEllipse(QRectF(bx - br * 0.18, by - br * 0.18,
                                   br * 0.36, br * 0.36))

    def _paint_aura(self, painter, cx, cy, half, highlight, mid, edge,
                    energy, dimf):
        """Aura-language canvas (v5.45 F7) — the visualizer's
        Aura painter at 92 px: deep-colour atmosphere, a glass
        sphere in deep edge tones, internal currents in the
        palette hues clipped inside the glass, a bright living
        core, and a fresnel rim (dim full contour, bright crown,
        whisper of bounce below). No ticks, no rings, no gauge.
        B-R1-5 LerSent liquid glass: the silhouette is the wobbled
        glass contour, and over the rim sit the glass wall, the
        top-light specular and the dispersion fringe — the same
        treatment as the home orb, at mini scale.
        DISCONNECTED greys through the shared palette + dimf;
        the muted badge is the shared one, drawn last."""
        # B-R1-5 LerSent glass: the liquid silhouette — the body +
        # currents clip to the wobbled contour (one surface, not a
        # matte disc in a perfect circle), from the same state +
        # energy this canvas already has.
        # R7-2 prerequisite fix: body_r was used on the line above
        # its assignment (UnboundLocalError on every paint — the
        # mini orb painted nothing); it is defined first now.
        body_r = half * 0.80
        _snap = self._glass.snapshot()
        _phase = self._glass.phase
        contour = glass_contour_path(cx, cy, body_r, _snap, _phase)
        # -- atmosphere -------------------------------------------------
        atm_r = half * 0.99
        atm = QRadialGradient(cx, cy, atm_r)
        a = int((52.0 + energy * 30.0) * dimf)
        atm.setColorAt(0.0, QColor(edge.red(), edge.green(), edge.blue(), a))
        atm.setColorAt(0.55, QColor(edge.red(), edge.green(), edge.blue(),
                                    int(a * 0.45)))
        atm.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(atm))
        painter.drawEllipse(QRectF(cx - atm_r, cy - atm_r, atm_r * 2, atm_r * 2))
        # -- sphere body: deep edge tones, light pulled high ------------
        body = QRadialGradient(QPointF(cx, cy), body_r * 1.05,
                               QPointF(cx, cy - body_r * 0.26))
        body.setColorAt(0.0, QColor(int(edge.red() * 0.50),
                                    int(edge.green() * 0.50),
                                    int(edge.blue() * 0.54), 238))
        body.setColorAt(0.55, QColor(int(edge.red() * 0.30),
                                     int(edge.green() * 0.30),
                                     int(edge.blue() * 0.33), 244))
        body.setColorAt(0.85, QColor(int(edge.red() * 0.14),
                                     int(edge.green() * 0.14),
                                     int(edge.blue() * 0.16), 248))
        body.setColorAt(1.0, QColor(6, 8, 12, 250))
        painter.setBrush(QBrush(body))
        painter.drawPath(contour)
        # -- internal currents -------------------------------------------
        clip = contour
        painter.save()
        painter.setClipPath(clip)
        blob_cols = (mid, edge, highlight, edge)
        for i, (ang, orad, _spd, brad, twk) in enumerate(self._aur_blobs):
            col = blob_cols[i % len(blob_cols)]
            rad = math.radians(ang)
            bx = cx + math.cos(rad) * body_r * orad
            by = cy + math.sin(rad) * body_r * orad
            br = body_r * brad
            twinkle = 0.75 + 0.25 * math.sin(self._breathe * 2.0 + twk)
            a_b = int(105.0 * twinkle * dimf)
            blob = QRadialGradient(bx, by, br)
            blob.setColorAt(0.0, QColor(col.red(), col.green(), col.blue(), a_b))
            blob.setColorAt(0.6, QColor(col.red(), col.green(), col.blue(),
                                        int(a_b * 0.45)))
            blob.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
            painter.setBrush(QBrush(blob))
            painter.drawEllipse(QRectF(bx - br, by - br, br * 2, br * 2))
        painter.restore()
        # -- R7-2: voice-reactive particle sphere -------------------
        # The jarvis-mark-i pattern as own code, at mini scale
        # (lod 2: fewer particles): the same slowly turning shell,
        # swelling with the same smoothed voice energy, inside the
        # glass clip, under the living core.
        painter.save()
        painter.setClipPath(clip)
        paint_particle_sphere(painter, cx, cy, body_r, self._sphere,
                              energy, highlight, mid, edge, dimf, 2)
        painter.restore()
        # -- living core ---------------------------------------------------
        core_r = body_r * (0.34 + 0.02 * math.sin(self._breathe)
                           + 0.13 * energy)
        core = QRadialGradient(cx, cy, core_r)
        core_a = int(235 * dimf)
        core.setColorAt(0.0, QColor(255, 255, 255, core_a))
        core.setColorAt(0.28, QColor(highlight.red(), highlight.green(),
                                     highlight.blue(), int(core_a * 0.82)))
        core.setColorAt(0.62, QColor(mid.red(), mid.green(), mid.blue(),
                                     int(core_a * 0.42)))
        core.setColorAt(1.0, QColor(mid.red(), mid.green(), mid.blue(), 0))
        painter.setBrush(QBrush(core))
        painter.drawEllipse(QRectF(cx - core_r, cy - core_r,
                                   core_r * 2, core_r * 2))
        # -- fresnel rim ----------------------------------------------------
        # B-R1-5: the rim follows the same wobbled contour as the
        # body (glass_contour_arc) — a rim on the perfect circle
        # would visibly detach from the liquid edge at full deform.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(highlight.red(), highlight.green(),
                                   highlight.blue(), int(58 * dimf)), 1.0))
        painter.drawPath(contour)
        painter.setPen(QPen(QColor(255, 255, 255, int(92 * dimf)), 1.2))
        painter.drawPath(glass_contour_arc(
            cx, cy, body_r, _snap, _phase, -145.0, 110.0))
        painter.setPen(QPen(QColor(edge.red(), edge.green(), edge.blue(),
                                   int(44 * dimf)), 1.0))
        painter.drawPath(glass_contour_arc(
            cx, cy, body_r, _snap, _phase, 35.0, 110.0))
        # -- B-R1-5 LerSent glass: shell + specular + fringe ---------
        # Drawn last (over the rim), from the same snapshot + phase
        # as the contour above — the mini orb mirrors the home orb.
        paint_glass_layers(painter, cx, cy, body_r, highlight, mid,
                           edge, _snap, _phase, dimf)
        if self._is("MUTED"):
            self._paint_muted_badge(painter, cx, cy, body_r, half)

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        half = min(w, h) / 2.0
        highlight, mid, edge = self._palette()
        energy = self._energy
        dim = self._is("DISCONNECTED")
        dimf = 0.55 if dim else 1.0
        self._paint_aura(painter, cx, cy, half, highlight, mid,
                        edge, energy, dimf)

class MiniOrb(QWidget):
    """Frameless, always-on-top floating orb. See module docstring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar entry
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(SIZE, SIZE)
        self.setObjectName("miniOrb")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.visualizer = _AuraMiniCanvas(self)
        # The canvas must not fight the mini's fixed 92px.
        self.visualizer.setMinimumSize(1, 1)
        self.visualizer.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.visualizer)

        # Callbacks (plain attributes, wired by the main window):
        #   on_click()         — left click (not a drag)
        #   on_open_main()     — "Open VYUK" menu item
        #   on_toggle_connect()— "Connect/Disconnect" menu item
        #   on_hide()          — "Hide mini orb" menu item (parent persists)
        self.on_click = None
        self.on_open_main = None
        self.on_toggle_connect = None
        self.on_hide = None

        self._state = "DISCONNECTED"
        self._press_global: QPoint | None = None
        self._press_window_pos: QPoint | None = None
        self._dragging = False

        self._restore_position()
        self.set_state("DISCONNECTED")

    # ------------------------------------------------------------------ API
    def set_state(self, status: str):
        """Mirror a session status: forward to the visualizer, and dim
        the whole orb while VYUK is disconnected."""
        status = str(status or "")
        self._state = status.upper()
        # SENSES v5.16 (E31): the tooltip names the state in words.
        try:
            pretty = (self._state.replace("_", " ").title()
                      if self._state else "Unknown")
            self.setToolTip(f"VYUK — {pretty}")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.visualizer.set_state(status)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.setWindowOpacity(
                0.55 if "DISCONNECTED" in self._state else 1.0)
        except Exception:  # noqa: BLE001
            pass

    def set_audio(self, level, bands=None):
        try:
            self.visualizer.update_audio_data(level, bands)
        except Exception:  # noqa: BLE001
            pass

    def set_theme(self, name: str):
        try:
            self.visualizer.set_theme(name)
        except Exception:  # noqa: BLE001
            pass

    def set_custom_theme(self, hi: str, mid: str, edge: str):
        try:
            self.visualizer.set_custom_theme(hi, mid, edge)
        except Exception:  # noqa: BLE001
            pass

    def set_orb_style(self, name: str):
        # Aura-only: the canvas normalises any name to Aura.
        try:
            self.visualizer.set_orb_style(name)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- position
    def _restore_position(self):
        pos = qsettings_get("mini_orb/pos")
        if isinstance(pos, QPoint) and self._on_screen(pos):
            self.move(pos)
            return
        # Default: bottom-right of the primary screen's usable area.
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            self.move(
                avail.right() - SIZE - 24,
                avail.bottom() - SIZE - 24,
            )

    def _on_screen(self, pos: QPoint) -> bool:
        """True if a 92px orb at pos is meaningfully on some screen."""
        area = SIZE * SIZE
        visible = 0
        for screen in QGuiApplication.screens():
            geo = screen.availableGeometry()
            inter = geo.intersected(QRect(pos.x(), pos.y(), SIZE, SIZE))
            visible += inter.width() * inter.height()
        return visible >= area // 2

    # ---------------------------------------------------------------- input
    def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_window_pos = self.pos()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 (Qt naming)
        if self._press_global is not None:
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() > _DRAG_THRESHOLD:
                self._dragging = True
            if self._dragging and self._press_window_pos is not None:
                self.move(self._press_window_pos + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            was_drag = self._dragging
            self._press_global = None
            self._press_window_pos = None
            self._dragging = False
            event.accept()
            if was_drag:
                # Persist where the user left it (move end).
                # Round-3 item 14: a position that never persists
                # jumps the orb back to its default spot at the
                # next launch. Noted on failure, never swallowed.
                qsettings_set_noted("mini_orb/pos", self.pos())
            elif self.on_click is not None:
                self.on_click()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):  # noqa: N802 (Qt naming)
        menu = QMenu(self)
        act_open = menu.addAction("Open VYUK")
        if self.on_open_main is not None:
            act_open.triggered.connect(lambda: self.on_open_main())
        act_conn = menu.addAction("Connect/Disconnect")
        if self.on_toggle_connect is not None:
            act_conn.triggered.connect(lambda: self.on_toggle_connect())
        menu.addSeparator()
        act_hide = menu.addAction("Hide mini orb")
        if self.on_hide is not None:
            act_hide.triggered.connect(lambda: self.on_hide())
        menu.exec(event.globalPos())
        event.accept()

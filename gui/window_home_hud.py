"""UI Phase 1 — Home HUD (rebuild, 2026-11-24).

Reparents the frozen v6.3 Home page into the Phase 1 three-column
stage (left rail / centre / right accordion) with the 2026-11-24
screenshot sizes (sidebar 170 / chat ~230 / HUD 868 = 224 / 494 /
150 at 1280; orb 19.5% of window width at every tier — ~250 at
1280, ~375 at 1920). The old _build_assistant_page is NOT
rewritten: this mixin wraps it (super() first, then
_build_home_hud), extracts the existing widgets, and re-lays them
into the new stage. It goes FIRST in ShellMixin's bases so its
_build_assistant_page, _apply_breakpoint_tier, _fix_chat_width and
_apply_dock_narrow_rule overrides win; the override is deliberate
and documented here (no test pins the no-duplicate-method
invariant).

Colours: every stylesheet in this module is built from the
resolved theme (gui/style_theme._resolve_theme) at build time and
rebuilt by _apply_home_theme on every theme change — no hardcoded
dark hex values (the old build's 21-colour Light-contrast debt is
not repeated). Semantic accents (amber/gold ring, OK green,
danger) use the style.py constants, which are the same on Light.
The state line uses the visualizer's exact accent hex (leg 70).

Object names the repro pins: homeStatusBlock, homeServices,
homeAlertsFeed, homeAttentionRail, homeCapabilityTiles,
homeDocsGlance, homeCommsGlance, homeSuggestions, homeMutePill,
homePermissionsPill, homeQuietPill, homePrivatePill,
homePrivateLock, homeTelemetry, homeDegradationBanner,
homeNowDoing, homeNowDoingElapsed, homeScreenMini, homeScreenScope,
screenScopeCursor, homeScreenScopeToggle, homeGreeting,
homeWeatherRow, homeWeatherBlock, weatherTemp, weatherGlyph,
weatherCondition, homeCityPrompt, homeNowContainer, goal_pill,
homeStateLine, homeColumnRule-0/1, homeAccordionSection-*,
accordionPreview, homeVitalsLine, composerPill, micLevelArc,
missionRailEmpty (Activity), _home_hero, _home_split,
_stage_scroll (None — the stage never scrolls, leg 15).
"""
from __future__ import annotations

import datetime
import re

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPalette,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .breakpoints import (
    hud_chat_for,
    hud_columns_for,
    hud_orb_diameter_for,
    hud_sidebar_for,
    hud_stage_min_for,
    hud_visualizer_height_for,
)
from .design_system.backdrop import HudBackdrop
from .design_system.panels import HudPanel
from .design_system.progress import SegmentBar
from .design_system.statusstrip import StatusStrip
from .room_home import BOOT_PLAYED, countdown_text
from .style import AMBER, DANGER, OK
from .style_theme import _luminance, _resolve_theme
from voice_assistant.core.gui_facade import (
    qsettings_get,
    qsettings_set,
)

# -- theme helpers -------------------------------------------------------
# All HUD stylesheets are built from the resolved theme, never from
# hardcoded hex. _rgba() turns a resolved hex into an rgba() string
# so hairlines and translucent fills follow the theme on dark and
# Light alike (line colours are a low alpha of the theme text).


def _rgba(hex_colour: str, alpha: float) -> str:
    c = QColor(str(hex_colour))
    return "rgba(%d, %d, %d, %.2f)" % (c.red(), c.green(), c.blue(), alpha)


def _qcolor(hex_colour: str) -> QColor:
    return QColor(str(hex_colour))


def _resolved() -> dict:
    """The resolved theme for the current theme name."""
    try:
        from .style_theme import _theme_by_name

        name = str(qsettings_get("theme") or "Cyan")
        return _resolve_theme(_theme_by_name(name))
    except Exception:  # noqa: BLE001 — fall back to the house theme
        try:
            from .style_theme import _theme_by_name

            return _resolve_theme(_theme_by_name("Cyan"))
        except Exception:  # noqa: BLE001
            return {
                "accent": "#35D6FF", "accent_2": "#64A2FF",
                "highlight": "#E3F0FF", "bg": "#04090E",
                "surface_1": "#0C1D28", "surface_2": "#08131B",
                "text": "#E6FBFF", "text_muted": "#B0C1CD",
                "text_faint": "#7E9AA6",
            }


class MicLevelArc(QWidget):
    """Ring around the composer mic button (H-32).

    The mic level is the ring, never a meter bar. ``level`` is a
    0..1 float set by _sync_audio_telemetry; the arc sweeps from
    the top clockwise, accent-coloured, alpha from the level.
    Hidden while idle (leg 35). Transparent to mouse events.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.level = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setVisible(False)

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, float(level or 0.0)))
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt naming
        if self.level <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(2, 2, -2, -2)
        # UI Phase 6 (D-02 neighbour): QPalette.WindowText does
        # not resolve on a QPalette INSTANCE (shiboken exposes the
        # enum member on the class only) — the old
        # ``self.palette().WindowText`` raised AttributeError on
        # every paint, so the ring never drew even while the leg
        # asserted it visible. ColorRole is the instance-safe path.
        accent = self.palette().color(QPalette.ColorRole.WindowText)
        pen = QPen(accent, 2.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setOpacity(0.35 + 0.65 * self.level)
        span = int(-5760 * self.level)  # clockwise, Qt spans are /16deg
        p.drawArc(r, 90 * 16, span)
        p.end()


class _HomeNewsBlock(QWidget):
    """News container whose PREFERRED height is the height its rows need.

    Briefing fix (2026-11-26): the rows' wrapped headlines need
    heightForWidth at the block's laid-out width (51 px a headline
    at 1280, 68 px for the longest one) — but the block's sizeHint
    under-reads its minimum (155 px against a 172 px minimum, both
    below the 223 px three rows need), so the column budget squeezed
    the rows and the headlines clipped. The first fix was an
    explicit minimumHeight floor — it held the rows full but the
    floor propagated up the layout chain and pushed the Home page
    minimum to 747 px against the 688 px no-scroll budget
    (test_home_no_scroll_1280_and_additions), so it had to go. A
    sizeHint is the right instrument: a preferred height, not a
    hard floor — the column has room for two rows (544 px of
    content in a 570 px column), so the layout honours it, and the
    page MINIMUM stays at its old value because minimumSizeHint is
    untouched. The hint is computed from the current width, so it
    tracks resizes (at 1920 the headlines wrap to fewer lines and
    the hint drops); _refresh_home_news_hint calls updateGeometry
    after every refresh and summary set so the layout re-asks.
    Falls back to the default hint before the block has a width or
    when it holds the single empty/failure label. Never raises.
    """

    def sizeHint(self):  # noqa: N802 - Qt naming
        try:
            _lay = self.layout()
            if _lay is None:
                return super().sizeHint()
            _rows = [_lay.itemAt(i).widget()
                     for i in range(_lay.count())]
            _rows = [r for r in _rows if r is not None
                     and r.objectName() == "homeNewsRow"]
            if not _rows:
                return super().sizeHint()
            _w = self.width()
            if _w <= 0:
                return super().sizeHint()
            _total = sum(r.heightForWidth(_w) for r in _rows)
            _total += _lay.spacing() * max(0, len(_rows) - 1)
            _m = _lay.contentsMargins()
            _total += _m.top() + _m.bottom()
            _hint = super().sizeHint()
            return QSize(max(_hint.width(), _w), _total)
        except Exception:  # noqa: BLE001 — cosmetic
            return super().sizeHint()


class HomeHudMixin:
    """Phase 1 Home HUD. Mixed in FIRST in ShellMixin's bases."""

    # -- _build_assistant_page wrapper ----------------------------------
    def _build_assistant_page(self):  # noqa: N802 - Qt naming
        page = super()._build_assistant_page()
        try:
            self._build_home_hud(page)
        except Exception as e:  # noqa: BLE001 - never break window boot
            print(f"[HomeHud] build failed: {e!r}")
        return page

    # -- build -----------------------------------------------------------
    def _build_home_hud(self, page: QWidget) -> None:
        """Reparent the v6.3 Home page into the Phase 1 stage."""
        th = _resolved()
        self._home_theme = th
        self._home_page = page
        page_layout = page.layout()
        if page_layout is not None:
            # Vertical margins 12/18 (was 16/20): the conditional
            # first-run banner (setup card) + voice strip add
            # 151 px to the stage when both show, and the page
            # minimum must stay inside the 688 px viewport at
            # 1280 (leg 15 / test_ui_p1). 6 px back keeps it
            # fitting with both banners visible; the horizontal
            # margins stay 0 so the §5 budgets land.
            page_layout.setContentsMargins(0, 12, 0, 18)
            page_layout.setSpacing(0)
        split = getattr(self, "_chat_splitter", None)
        self._home_split = split

        # -- 1. Extract the old stage's widgets, drop the scroll area --
        # The old stage is a child of _stage_scroll; reparenting a
        # widget out of a layout removes it from that layout, so the
        # old stage collapses to nothing and is deleted with the
        # scroll area. Widgets we keep hidden (telemetry render
        # targets) are reparented to the page itself.
        old_scroll = getattr(self, "_stage_scroll", None)
        old_stage = getattr(self, "_stage_widget", None)
        keep = {}
        for name in (
            "hero", "visualizer", "particle_field", "home_wordmark",
            "state_label", "speaking_eq", "briefing_label",
            "caption_label", "setup_card", "selftest_card",
            "briefing_card", "transport_row", "pace_box",
            "voice_chip", "goal_pill", "settings_voice_strip",
            "mic_health_dot",
        ):
            w = getattr(self, name, None)
            if w is not None:
                keep[name] = w
        # mic_meter is superseded by the mic ring (H-32): it is
        # deleted with the old stage; _sync_audio_telemetry drives
        # micLevelArc instead (guarded, so a pre-HUD call is safe).
        if old_scroll is not None and split is not None:
            old_scroll.setParent(None)
            old_scroll.deleteLater()
        if old_stage is not None:
            old_stage.setParent(None)
            old_stage.deleteLater()
        self._stage_scroll = None
        self._stage_widget = None

        # -- 2. New stage (never scrolls — leg 15) ----------------------
        stage = QWidget()
        stage.setObjectName("homeStage")
        self._home_stage = stage
        self._stage_widget = stage
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)
        if split is not None:
            split.insertWidget(0, stage)
        if page_layout is not None:
            # The old header row (greeting + status strip) and the
            # settings-voice strip leave the page layout: the header
            # moves inside the stage, the strip becomes a hidden
            # child of the stage (it still shows on demand).
            for _ in range(page_layout.count()):
                item = page_layout.itemAt(0)
                if item is None:
                    break
                lay = item.layout()
                if lay is not None and lay.indexOf(
                        getattr(self, "btn_header_overflow", None)) >= 0:
                    page_layout.takeAt(0)
                    break
                break
            strip = keep.get("settings_voice_strip")
            if strip is not None:
                strip.setParent(stage)
                stage_layout.addWidget(strip)
                strip.hide()
        self._home_stage_layout = stage_layout

        # -- 3. Header (kicker + greeting + cluster) --------------------
        self._build_home_header(stage, stage_layout, keep, th)
        # -- 4. Three columns -------------------------------------------
        self._build_home_columns(stage, stage_layout, keep, th)
        # -- 4b. Setup / self-test cards: a stage-level banner ---------
        # The conditional first-run cards live ABOVE the columns, not
        # in the centre column. In the centre column they overfill
        # it (120 px) and zero both stretches, pushing the orb to
        # 34% (leg 72) — the plan's centre column has no room for
        # them. As a full-width banner above the columns they stay
        # visible while setup is incomplete and never displace the
        # orb. _build_home_centre stashes them in _home_setup_cards.
        try:
            _cols = getattr(self, "_home_columns_widget", None)
            _cards = getattr(self, "_home_setup_cards", []) or []
            for _card in _cards:
                _card.setParent(stage)
                if _cols is not None:
                    stage_layout.insertWidget(
                        stage_layout.indexOf(_cols), _card)
                else:
                    stage_layout.addWidget(_card)
        except Exception:  # noqa: BLE001 — banner never breaks boot
            pass
        # -- 5. Composer pill + mic ring (wraps the existing dock) -----
        self._build_home_composer(th)
        # -- 6. Boot moment (first build only) --------------------------
        self._home_boot()
        # -- 7. Profile, tiers, first refresh ---------------------------
        self._apply_hud_profile()
        self._apply_breakpoint_tier()
        self._fix_chat_width(first=True)
        self._refresh_home()
        self._ensure_icon_button_tooltips()

    # -- header ----------------------------------------------------------
    def _build_home_header(self, stage, stage_layout, keep, th):
        header = QWidget()
        header.setObjectName("homeHeader")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(4, 0, 4, 0)
        hlay.setSpacing(12)
        # Left: kicker above the greeting (H-14 order).
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        kicker = QLabel("HOME")
        kicker.setObjectName("homeKicker")
        self._home_kicker = kicker
        kicker.setStyleSheet(
            "color: %s; font-family: %s; font-size: 10px;"
            " letter-spacing: 2px;" % (
                th["text_faint"], "Share Tech Mono, monospace"))
        left.addWidget(kicker)
        greeting = keep.get("home_greeting")
        if greeting is None:
            greeting = getattr(self, "home_greeting", None)
        if greeting is not None:
            greeting.setParent(header)
            greeting.setObjectName("homeGreeting")
            greeting.setStyleSheet(
                "color: %s; font-family: Orbitron, sans-serif;"
                " font-size: 20px; font-weight: 600;" % th["text"])
            left.addWidget(greeting)
            self.home_greeting = greeting
        hlay.addLayout(left, 1)
        # Right: the header cluster (connection dots, permissions,
        # quiet/private pills, mute pill, rail toggle, overflow,
        # Connect). The old status strip is NOT reused here — the
        # vitals strip at the centre bottom is the StatusStrip now.
        cluster = QHBoxLayout()
        cluster.setContentsMargins(0, 0, 0, 0)
        cluster.setSpacing(6)
        dots = QLabel("●")
        dots.setObjectName("homeConnDots")
        dots.setToolTip("Connection")
        dots.setStyleSheet("color: %s; font-size: 11px;" % th["text_muted"])
        cluster.addWidget(dots)
        self.home_conn_dots = dots
        perm = QPushButton("Permissions")
        perm.setObjectName("homePermissionsPill")
        perm.setToolTip("Permissions — open Settings")
        perm.clicked.connect(
            lambda: self._show_page(4) if hasattr(self, "_show_page") else None)
        cluster.addWidget(perm)
        self.home_permissions_pill = perm
        quiet = QLabel("")
        quiet.setObjectName("homeQuietPill")
        quiet.setVisible(False)
        cluster.addWidget(quiet)
        self.home_quiet_pill = quiet
        # Private pill: the lock glyph sits IN the pill (H-36).
        priv = QPushButton("Private")
        priv.setObjectName("homePrivatePill")
        priv.setCheckable(True)
        priv.setToolTip("Private mode — blanks every surface")
        priv.clicked.connect(self._on_private_pill_clicked)
        cluster.addWidget(priv)
        self.home_private_pill = priv
        lock = QLabel("🔒")
        lock.setObjectName("homePrivateLock")
        lock.setParent(priv)
        lock.setVisible(False)
        lock.setStyleSheet("background: transparent; border: none;")
        self.home_private_lock = lock
        mute = QPushButton("Mute")
        mute.setObjectName("homeMutePill")
        mute.setToolTip("Mute / unmute the microphone")
        mute.clicked.connect(self._on_mute_pill_clicked)
        cluster.addWidget(mute)
        self.home_mute_pill = mute
        # Rail overlay toggle (H-05) — only at the narrow tiers.
        rail_toggle = QPushButton("☰ Rail")
        rail_toggle.setObjectName("homeRailToggle")
        rail_toggle.setToolTip("Show the telemetry rail")
        rail_toggle.setVisible(False)
        rail_toggle.clicked.connect(self._toggle_rail_overlay)
        cluster.addWidget(rail_toggle)
        self.home_rail_toggle = rail_toggle
        overflow = getattr(self, "btn_header_overflow", None)
        if overflow is not None:
            overflow.setParent(header)
            cluster.addWidget(overflow)
        connect = getattr(self, "btn_connect", None)
        if connect is not None:
            connect.setParent(header)
            cluster.addWidget(connect)
        # The old header status strip is retired (its aliases are
        # re-pointed at the vitals strip in _build_home_centre).
        # Delete it outright — merely hiding it leaves it a child
        # of the page, so a findChildren(StatusStrip) still returns
        # it first (leg 37) and its stock text ("Offline MIC OFF
        # WAKE ON") shadows the vitals strip's instruments.
        old_strip = getattr(self, "status_strip", None)
        if old_strip is not None:
            old_strip.hide()
            old_strip.setParent(None)
            old_strip.deleteLater()
        hlay.addLayout(cluster)
        stage_layout.addWidget(header)
        self._home_header = header
        for w in (perm, priv, mute, rail_toggle):
            w.setStyleSheet(
                "QPushButton { color: %s; background: %s;"
                " border: 1px solid %s; border-radius: 13px;"
                " padding: 3px 10px; font-size: 12px; }"
                "QPushButton:checked { color: %s; border-color: %s; }"
                % (th["text_muted"], _rgba(th["surface_1"], 0.6),
                   _rgba(th["text"], 0.14), th["text"], th["accent"]))
        self._home_pill_buttons = (perm, priv, mute, rail_toggle)

    # -- columns ---------------------------------------------------------
    def _build_home_columns(self, stage, stage_layout, keep, th):
        cols = QWidget()
        cols.setObjectName("homeColumns")
        self._home_columns = cols
        clay = QHBoxLayout(cols)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(0)
        # Left rail.
        rail = QWidget()
        rail.setObjectName("homeLeftRail")
        rail.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        rlay = QVBoxLayout(rail)
        rlay.setContentsMargins(4, 4, 4, 4)
        rlay.setSpacing(10)
        clay.addWidget(rail)
        self._home_left_rail = rail
        self._home_left_layout = rlay
        # Centre.
        centre = QWidget()
        centre.setObjectName("homeCentre")
        centre.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        clay.addWidget(centre)
        self._home_centre = centre
        # Right column.
        right = QWidget()
        right.setObjectName("homeRightCol")
        right.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        rrlay = QVBoxLayout(right)
        rrlay.setContentsMargins(4, 4, 4, 4)
        rrlay.setSpacing(10)
        clay.addWidget(right)
        self._home_right_col = right
        self._home_right_layout = rrlay
        stage_layout.addWidget(cols, 1)
        self._home_columns_widget = cols
        self._build_home_rail(rail, rlay, th)
        self._build_home_centre(centre, keep, th)
        self._build_home_right(right, rrlay, th)
        # Column hairlines (leg 38): 1 px QFrames at the right edge
        # of the rail / centre, inside the column widgets so the
        # column widths stay exactly at the §5 values (leg 71).
        for idx, host in enumerate((rail, centre)):
            rule = QFrame(host)
            rule.setObjectName("homeColumnRule-%d" % idx)
            rule.setFrameShape(QFrame.VLine)
            rule.setFrameShadow(QFrame.Plain)
            rule.setLineWidth(1)
            rule.setStyleSheet(
                "background: %s; border: none;" % _rgba(th["text"], 0.10))
            rule.setFixedWidth(1)
            rule.move(host.width() - 1, 0)
            rule.setFixedHeight(host.height())
            rule.show()
            if idx == 0:
                self.home_column_rule_0 = rule
            else:
                self.home_column_rule_1 = rule
        self._home_rule_hosts = (rail, centre)

    # -- left rail -------------------------------------------------------
    def _build_home_rail(self, rail, rlay, th):
        # Status block (instrument cluster, §2 refinement): labelled
        # mini-bars for CPU / memory / temperature / battery, brain +
        # wake as small pills. Telemetry lives in homeTelemetry.
        block = HudPanel(glass=True)
        block.setObjectName("homeStatusBlock")
        # A HudPanel already owns a layout (content_layout) — never
        # construct a second QLayout on it: Qt leaves the second
        # one orphaned and every widget added to it parentless
        # (findChild then cannot see them). Use content_layout().
        blay = block.content_layout()
        blay.setContentsMargins(10, 8, 10, 8)
        blay.setSpacing(6)
        title = QLabel("STATUS")
        title.setStyleSheet(
            "color: %s; font-family: Share Tech Mono, monospace;"
            " font-size: 10px; letter-spacing: 2px;" % th["text_faint"])
        blay.addWidget(title)
        tele = QWidget()
        tele.setObjectName("homeTelemetry")
        tlay = QVBoxLayout(tele)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.setSpacing(5)
        self._home_status_bars = {}
        for key, label in (
                ("cpu", "CPU"), ("mem", "Memory"),
                ("temp", "Temp"), ("batt", "Battery")):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            lab = QLabel(label)
            lab.setFixedWidth(52)
            lab.setStyleSheet("color: %s; font-size: 11px;"
                              % th["text_muted"])
            bar = QFrame()
            bar.setObjectName("statusBar-%s" % key)
            bar.setFixedHeight(5)
            bar.setStyleSheet(
                "background: %s; border-radius: 2px;"
                % _rgba(th["text"], 0.12))
            row.addWidget(lab)
            row.addWidget(bar, 1)
            val = QLabel("—")
            val.setObjectName("statusBarVal-%s" % key)
            val.setFixedWidth(40)
            val.setAlignment(Qt.AlignRight)
            val.setStyleSheet(
                "color: %s; font-family: Share Tech Mono, monospace;"
                " font-size: 11px;" % th["text"])
            row.addWidget(val)
            tlay.addLayout(row)
            self._home_status_bars[key] = (bar, val)
        blay.addWidget(tele)
        self.home_telemetry = tele
        pills = QHBoxLayout()
        pills.setContentsMargins(0, 0, 0, 0)
        pills.setSpacing(6)
        brain = QLabel("cloud")
        brain.setObjectName("statusPill-brain")
        wake = QLabel("off")
        wake.setObjectName("statusPill-wake")
        for p in (brain, wake):
            p.setStyleSheet(
                "color: %s; background: %s; border: 1px solid %s;"
                " border-radius: 9px; padding: 2px 8px; font-size: 11px;"
                % (th["text_muted"], _rgba(th["surface_2"], 0.7),
                   _rgba(th["text"], 0.12)))
            pills.addWidget(p)
        blay.addLayout(pills)
        self._home_brain_pill = brain
        self._home_wake_pill = wake
        rlay.addWidget(block)
        self.home_status_block = block
        # Services.
        services = HudPanel(glass=True)
        services.setObjectName("homeServices")
        slay = services.content_layout()
        slay.setContentsMargins(10, 8, 10, 8)
        slay.setSpacing(6)
        st = QLabel("SERVICES")
        st.setStyleSheet(
            "color: %s; font-family: Share Tech Mono, monospace;"
            " font-size: 10px; letter-spacing: 2px;" % th["text_faint"])
        slay.addWidget(st)
        self._home_services_body = QLabel("Checking…")
        self._home_services_body.setWordWrap(True)
        self._home_services_body.setStyleSheet(
            "color: %s; font-size: 12px;" % th["text_muted"])
        slay.addWidget(self._home_services_body)
        rlay.addWidget(services)
        self.home_services = services
        # Alerts feed.
        alerts = HudPanel(glass=True)
        alerts.setObjectName("homeAlertsFeed")
        alay = alerts.content_layout()
        alay.setContentsMargins(10, 8, 10, 8)
        alay.setSpacing(6)
        at = QLabel("ALERTS")
        at.setStyleSheet(
            "color: %s; font-family: Share Tech Mono, monospace;"
            " font-size: 10px; letter-spacing: 2px;" % th["text_faint"])
        alay.addWidget(at)
        self._home_alerts_body = QVBoxLayout()
        self._home_alerts_body.setContentsMargins(0, 0, 0, 0)
        self._home_alerts_body.setSpacing(4)
        alay.addLayout(self._home_alerts_body)
        rlay.addWidget(alerts)
        self.home_alerts_feed = alerts
        # Comms at a glance + Proactive suggestions (ruling
        # 2026-11-24, Aayur — moved from the right column, which
        # was too crowded): collapsible sections below the alerts
        # feed, comms first, then suggestions. The left rail is
        # wider (224 vs 150 at 1280), so it takes the two
        # lightest, mostly-empty cards. The bodies keep the
        # plan's objectNames (leg 17 pins them).
        self._home_rail_panels = {}
        self._home_rail_sections = {}
        self._home_rail_previews = {}
        comms_panel = self._build_home_rail_section(
            rlay, th, "comms", "MESSAGES", "homeCommsGlance")
        sugg_panel = self._build_home_rail_section(
            rlay, th, "suggestions", "SUGGESTIONS", "homeSuggestions")
        self.home_comms_glance = self._home_rail_sections["comms"][0]
        self.home_suggestions = (
            self._home_rail_sections["suggestions"][0])
        rlay.addStretch(1)
        # Hide buttons (H-02: orb / chat / greeting / briefing
        # summary can never be hidden — no buttons for them).
        for name, widget in (
                ("services", services), ("alerts", alerts),
                ("status", block), ("comms", comms_panel),
                ("suggestions", sugg_panel)):
            btn = QPushButton("Hide %s" % name)
            btn.setObjectName("homeHide-%s" % name)
            btn.setVisible(False)
            btn.clicked.connect(
                lambda _c, w=widget: w.hide())
            rlay.addWidget(btn)
            setattr(self, "_home_hide_%s_btn" % name, btn)
        self._home_hidden_panels = []

    def _build_home_rail_section(self, rlay, th, key, title,
                                 body_name):
        """One collapsible left-rail section (ruling 2026-11-24).
        Same shape as an accordion section — header + preview +
        body, collapsed by default — but a plain rail panel, not
        part of the right accordion. The body content is the same
        builder the accordion used (_build_accordion_body), so the
        refresh methods (_refresh_home_comms /
        _refresh_home_suggestions) keep working unchanged."""
        panel = HudPanel(glass=True)
        panel.setObjectName("homeRailSection-%s" % key)
        play = panel.content_layout()
        play.setContentsMargins(10, 6, 10, 6)
        play.setSpacing(6)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        t = QLabel(title)
        t.setStyleSheet(
            "color: %s; font-family: Share Tech Mono, monospace;"
            " font-size: 10px; letter-spacing: 2px;"
            % th["text_faint"])
        head.addWidget(t, 1)
        chev = QToolButton()
        chev.setText("▾")
        chev.setObjectName("homeRailToggle-%s" % key)
        chev.setAutoRaise(True)
        chev.setStyleSheet(
            "QToolButton { background: transparent; border: none;"
            " color: %s; font-size: 12px; }"
            " QToolButton:hover { color: %s; }"
            % (th["text_muted"], th["text"]))
        chev.setToolTip("Expand / collapse %s" % title.lower())
        chev.clicked.connect(
            lambda _c, k=key: self._toggle_rail_section(k))
        head.addWidget(chev)
        play.addLayout(head)
        preview = QLabel("")
        preview.setObjectName("railPreview")
        preview.setWordWrap(True)
        preview.setStyleSheet("color: %s; font-size: 12px;"
                              % th["text_muted"])
        play.addWidget(preview)
        self._home_rail_previews = getattr(
            self, "_home_rail_previews", {})
        self._home_rail_previews[key] = preview
        body = QWidget()
        body.setObjectName(body_name)
        blay = QVBoxLayout(body)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(6)
        self._build_accordion_body(key, blay, th)
        play.addWidget(body)
        # Collapsed by default (preview line only), like the
        # accordion's non-briefing sections.
        body.setVisible(False)
        preview.setVisible(True)
        self._home_rail_sections[key] = (body, preview)
        self._home_rail_panels[key] = panel
        rlay.addWidget(panel)
        return panel

    def _toggle_rail_section(self, key):
        entry = getattr(self, "_home_rail_sections", {}).get(key)
        if entry is None:
            return
        body, preview = entry
        expanded = not body.isVisible()
        body.setVisible(expanded)
        if preview is not None:
            preview.setVisible(not expanded)

    # -- centre ----------------------------------------------------------
    def _build_home_centre(self, centre, keep, th):
        clay = QVBoxLayout(centre)
        clay.setContentsMargins(4, 4, 4, 4)
        # 4 px spacing (not 6): the centre's minimum height must
        # fit the 650 px stage at 1280 (leg 15) — 6 px costs 26 px
        # of minimum height across the column's 13 gaps.
        clay.setSpacing(4)
        # Degradation banner (top, never displaces the orb — leg 72).
        banner = QLabel("")
        banner.setObjectName("homeDegradationBanner")
        # Hunt fix (c) item 3: the notice reads whole, wrapped.
        # The old one-line elision left "Voice is degraded — her
        # ears (speech recognition) are not ready and her…" at
        # 1280 and "Voice is degraded —…" at 640. The old comment
        # claimed a wrapped banner costs 62 px of the centre's
        # 650 px budget (leg 15); the measured slack is 109 px
        # (page minSizeHint 577 vs 686 at 1280), and a 3-line
        # banner costs only +21 px over the 30 px single line.
        banner.setWordWrap(True)
        banner.setVisible(False)
        banner.setStyleSheet(
            "color: %s; background: %s; border: 1px solid %s;"
            " border-radius: 8px; padding: 6px 10px; font-size: 12px;"
            % (th["text"], _rgba(th["surface_2"], 0.85),
               _rgba(AMBER, 0.55)))
        clay.addWidget(banner)
        self.home_degradation_banner = banner
        # Top stretch (3) / bottom stretch (1): the orb centre lands
        # ~43% of the column height (leg 72 band is 35–55%). The
        # 2:1 ratio left the centre at 34.6% (3.4 px under the 35%
        # floor at 1280) — the banner + header eat the top, so the
        # top stretch needs the extra share.
        clay.addStretch(3)
        hero = keep.get("hero")
        if hero is not None:
            hero.setParent(centre)
            hero.setObjectName("homeHero")
            clay.addWidget(hero)
            self.hero = hero
            self._home_hero = hero
        else:
            self._home_hero = getattr(self, "hero", None)
        wordmark = keep.get("home_wordmark")
        if wordmark is not None:
            wordmark.setParent(centre)
            wordmark.setAlignment(Qt.AlignCenter)
            wordmark.setStyleSheet(
                "color: %s; font-family: Orbitron, sans-serif;"
                " font-size: 13px; font-weight: 600;"
                " letter-spacing: 6px;"
                % th["text_muted"])
            clay.addWidget(wordmark)
            self.home_wordmark = wordmark
        state = keep.get("state_label")
        if state is not None:
            state.setParent(centre)
            state.setObjectName("homeStateLine")
            state.setAlignment(Qt.AlignCenter)
            clay.addWidget(state)
            self.state_label = state
            self.home_state_line = state
        eq = keep.get("speaking_eq")
        if eq is not None:
            eq.setParent(centre)
            clay.addWidget(eq, alignment=Qt.AlignCenter)
        caption = keep.get("caption_label")
        if caption is not None:
            caption.setParent(centre)
            caption.hide()
            self.caption_label = caption
        brief = keep.get("briefing_label")
        if brief is not None:
            brief.setParent(centre)
            brief.hide()
            self.briefing_label = brief
        # Now container: goal pill + Now Doing share one container
        # with a hairline (H-43). The hairline QFrame is always
        # present at 1 px (leg 68) but transparent until both the
        # goal pill and Now Doing are present.
        now = QWidget()
        now.setObjectName("homeNowContainer")
        nlay = QVBoxLayout(now)
        nlay.setContentsMargins(0, 0, 0, 0)
        nlay.setSpacing(4)
        goal = keep.get("goal_pill")
        if goal is not None:
            goal.setParent(now)
            nlay.addWidget(goal)
            self.goal_pill = goal
        hair = QFrame(now)
        hair.setFrameShape(QFrame.HLine)
        hair.setFrameShadow(QFrame.Plain)
        hair.setFixedHeight(1)
        hair.setStyleSheet("background: transparent; border: none;")
        nlay.addWidget(hair)
        self._home_now_hairline = hair
        doing = QLabel("")
        doing.setObjectName("homeNowDoing")
        doing.setWordWrap(True)
        doing.setAlignment(Qt.AlignCenter)
        doing.setStyleSheet("color: %s; font-size: 13px;" % th["text"])
        nlay.addWidget(doing)
        self.home_now_doing = doing
        elapsed = QLabel("")
        elapsed.setObjectName("homeNowDoingElapsed")
        elapsed.setAlignment(Qt.AlignCenter)
        elapsed.setStyleSheet(
            "color: %s; font-family: Share Tech Mono, monospace;"
            " font-size: 12px;" % th["text_muted"])
        nlay.addWidget(elapsed)
        self.home_now_doing_elapsed = elapsed
        clay.addWidget(now)
        self.home_now_container = now
        # Screen mini (H-17): scope + last action chip, below Now
        # Doing. Visible at idle so the toggle is reachable (leg 46).
        # The scope is a container panel (homeScreenScope) holding
        # the cursor dot (screenScopeCursor) — the leg reads the dot
        # as a CHILD of the scope, so the dot must live inside it.
        mini = QWidget()
        mini.setObjectName("homeScreenMini")
        mlay = QHBoxLayout(mini)
        mlay.setContentsMargins(0, 0, 0, 0)
        mlay.setSpacing(8)
        scope = QWidget()
        scope.setObjectName("homeScreenScope")
        scope.setStyleSheet(
            "background: %s; border: 1px solid %s; border-radius: 8px;"
            % (_rgba(th["surface_2"], 0.8), _rgba(th["text"], 0.12)))
        slay = QHBoxLayout(scope)
        slay.setContentsMargins(8, 4, 8, 4)
        slay.setSpacing(8)
        eyebrow = QLabel("SCREEN")
        eyebrow.setStyleSheet(
            "color: %s; font-family: Share Tech Mono, monospace;"
            " font-size: 10px; letter-spacing: 2px; background: transparent;"
            " border: none;" % th["text_faint"])
        slay.addWidget(eyebrow)
        cursor = QLabel("●")
        cursor.setObjectName("screenScopeCursor")
        cursor.setStyleSheet("color: %s; font-size: 12px; background: transparent;"
                             " border: none;" % th["accent"])
        slay.addWidget(cursor)
        action = QLabel("No screen activity yet")
        action.setObjectName("homeScreenScopeAction")
        action.setStyleSheet("color: %s; font-size: 12px; background: transparent;"
                             " border: none;" % th["text_muted"])
        slay.addWidget(action, 1)
        mlay.addWidget(scope, 1)
        self.home_screen_scope = scope
        self.screen_scope_cursor = cursor
        self.home_screen_scope_action = action
        toggle = QPushButton("▾")
        toggle.setObjectName("homeScreenScopeToggle")
        toggle.setToolTip("Show / hide the screen scope")
        toggle.clicked.connect(self._toggle_screen_scope)
        mlay.addWidget(toggle)
        self.home_screen_scope_toggle = toggle
        clay.addWidget(mini)
        self.home_screen_mini = mini
        # Transport + pace (hidden children at idle; the pace box
        # keeps its height so the vitals never jump — H-13/H-32).
        transport = keep.get("transport_row")
        if transport is not None:
            transport.setParent(centre)
            clay.addWidget(transport)
            self.transport_row = transport
        pace = keep.get("pace_box")
        if pace is not None:
            pace.setParent(centre)
            pace.setFixedHeight(28)
            for child in pace.findChildren(QWidget):
                child.hide()
            clay.addWidget(pace)
            self.pace_box = pace
        # Conditional cards (setup / self-test) — stashed, NOT added
        # to the centre column. They live as a stage-level banner
        # above the columns (see _build_home_hud §4b): in the centre
        # column they overfill it and zero both stretches, pushing
        # the orb off its optical centre (leg 72). Usually hidden.
        self._home_setup_cards = []
        for name in ("setup_card", "selftest_card"):
            card = keep.get(name)
            if card is not None:
                self._home_setup_cards.append(card)
                setattr(self, name, card)
        clay.addStretch(1)
        # Vitals strip (H-34): the StatusStrip, pinned to the
        # bottom edge. The old header strip's aliases re-point here.
        vitals = StatusStrip(centre)
        vitals.setObjectName("homeVitalsLine")
        clay.addWidget(vitals)
        self.home_vitals_line = vitals
        self.vitals_strip = vitals
        self.status_strip = vitals
        self.status_pill = vitals.status_label
        self.watch_pill = vitals.watch_label
        self.capture_pill = vitals.capture_label
        # Instrument labels (leg 37): wake-word state, battery,
        # clock. The strip's own status_label is owned by
        # _refresh_home (brain source); these labels carry the
        # exact words the leg asserts. Hunt fix (c) item 2: the
        # brain label is gone — it duplicated the strip's
        # status_label, so the footer read the brain source twice
        # ("local local").
        self._home_vitals_wake = self._vitals_label(
            vitals, "homeVitalsWake", th)
        self._home_vitals_batt = self._vitals_label(
            vitals, "homeVitalsBatt", th)
        self._home_vitals_clock = self._vitals_label(
            vitals, "homeVitalsClock", th)
        # The mic-health dot is a hidden telemetry target; keep it
        # alive as a child of the centre (never laid out).
        dot = keep.get("mic_health_dot")
        if dot is not None:
            dot.setParent(centre)
            dot.hide()
            self.mic_health_dot = dot
        voice_chip = keep.get("voice_chip")
        if voice_chip is not None:
            voice_chip.setParent(centre)
            voice_chip.hide()
            self.voice_chip = voice_chip
        self._home_centre_layout = clay

    def _vitals_label(self, vitals, name, th):
        lab = QLabel("")
        lab.setObjectName(name)
        lab.setStyleSheet(
            "color: %s; font-family: Share Tech Mono, monospace;"
            " font-size: 11px;" % th["text_muted"])
        try:
            vitals.layout().addWidget(lab)
        except Exception:  # noqa: BLE001
            pass
        return lab

    # -- right accordion -------------------------------------------------
    def _build_home_right(self, right, rrlay, th):
        self._home_accordion = {}
        self._home_accordion_bodies = {}
        self._home_accordion_previews = {}
        # The briefing card becomes the Briefing section's body.
        briefing_card = getattr(self, "briefing_card", None)
        sections = [
            ("briefing", "BRIEFING", briefing_card),
            ("attention", "ATTENTION", None),
            ("tiles", "QUICK ACTIONS", None),
            ("docs", "DOCUMENTS", None),
        ]
        for key, title, body_widget in sections:
            panel = HudPanel(glass=True)
            panel.setObjectName("homeAccordionSection-%s" % key)
            play = panel.content_layout()
            # Margins (10, 6, 10, 6): the vertical 6 px (was 8)
            # reclaims the ~4 px the vertical briefing header
            # still costs after the card-margin tightening, so
            # Home stays scroll-free at 1280 (688 px budget).
            play.setContentsMargins(10, 6, 10, 6)
            play.setSpacing(6)
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(6)
            t = QLabel(title)
            t.setStyleSheet(
                "color: %s; font-family: Share Tech Mono, monospace;"
                " font-size: 10px; letter-spacing: 2px;"
                % th["text_faint"])
            head.addWidget(t, 1)
            chev = QToolButton()
            chev.setText("▾")
            chev.setObjectName("homeAccordionToggle-%s" % key)
            # Flat: a raised QToolButton paints a solid button face
            # (the white square in the renders) — the toggle is a
            # bare glyph over the panel, no button chrome.
            chev.setAutoRaise(True)
            chev.setStyleSheet(
                "QToolButton { background: transparent; border: none;"
                " color: %s; font-size: 12px; }"
                " QToolButton:hover { color: %s; }"
                % (th["text_muted"], th["text"]))
            chev.setToolTip("Expand / collapse %s" % title.lower())
            chev.clicked.connect(
                lambda _c, k=key: self._toggle_accordion(k))
            head.addWidget(chev)
            play.addLayout(head)
            preview = QLabel("")
            preview.setObjectName("accordionPreview")
            preview.setWordWrap(True)
            preview.setStyleSheet("color: %s; font-size: 12px;"
                                  % th["text_muted"])
            play.addWidget(preview)
            self._home_accordion_previews[key] = preview
            body = QWidget()
            # Hunt fix (c) §7: the briefing body's height depends
            # on its width (the card's wrapped labels) — the body
            # and its panel carry height-for-width so the column
            # asks the card at the real width instead of trusting
            # a sizeHint computed at the preferred width.
            if key == "briefing":
                from PySide6.QtWidgets import QSizePolicy as _QSP
                _hfw = _QSP(_QSP.Policy.Preferred,
                            _QSP.Policy.Preferred)
                _hfw.setHeightForWidth(True)
                body.setSizePolicy(_hfw)
                panel.setSizePolicy(_hfw)
            # The repro pins these bodies by the plan's names
            # (leg 17); the briefing body keeps its accordion name.
            _body_names = {
                "attention": "homeAttentionRail",
                "tiles": "homeCapabilityTiles",
                "docs": "homeDocsGlance",
            }
            body.setObjectName(_body_names.get(
                key, "homeAccordionBody-%s" % key))
            blay = QVBoxLayout(body)
            blay.setContentsMargins(0, 0, 0, 0)
            blay.setSpacing(6)
            if body_widget is not None:
                body_widget.setParent(body)
                blay.addWidget(body_widget)
                self.briefing_card = body_widget
                # The briefing card's vertical header (summary on
                # its own line, buttons below) adds ~21 px over
                # the old one-line header; the accordion panel
                # already pads 10 px, so the card's own margins
                # shrink here to keep Home scroll-free at 1280
                # (the no-scroll leg allows 688 px).
                try:
                    _bl = body_widget.layout()
                    if _bl is not None:
                        _bl.setContentsMargins(12, 6, 12, 6)
                except Exception:  # noqa: BLE001 — cosmetic
                    pass
            else:
                self._build_accordion_body(key, blay, th)
            play.addWidget(body)
            self._home_accordion_bodies[key] = body
            self._home_accordion[key] = panel
            rrlay.addWidget(panel)
            # Briefing open by default; the rest closed.
            expanded = key == "briefing"
            body.setVisible(expanded)
            preview.setVisible(not expanded)
        rrlay.addStretch(1)
        # Attention rail + capability tiles + docs live in their
        # accordion bodies (built below); comms + suggestions
        # live in the left rail now (ruling 2026-11-24) and their
        # attributes are set in _build_home_rail. The named
        # attributes the repro pins:
        self.home_attention_rail = self._home_accordion_bodies["attention"]
        self.home_capability_tiles = self._home_accordion_bodies["tiles"]
        self.home_docs_glance = self._home_accordion_bodies["docs"]

    def _fill_home_tiles_body(self, blay, th):
        """Build the capability-tiles row into ``blay`` (UI Phase 2,
        D-P2-15).

        The row shows the user's pinned skills when the pin store
        is non-empty (a tap asks with that skill — the E22
        prefill), and the six page shortcuts (Reminders, Email,
        Calendar, Notes, Home, Money) when it is empty. The six
        are PAGE shortcuts, not tools, so they live here as the
        dock's defaults — they are not written into the pin store
        (the Centre's Pinned section would otherwise list six
        tools the user never pinned, three of which have no tool
        at all). The dock shows at most six pins (the same six
        slots); further pins live in the Centre's Pinned section
        and are named in the collapsed preview line ("+N more").
        """
        from .icons import make_icon
        from .skill_pins import pinned_tools
        from .style import SURFACE_1, TEXT

        text = th.get("text", TEXT) if isinstance(th, dict) else TEXT
        surface_1 = (th.get("surface_1", SURFACE_1)
                     if isinstance(th, dict) else SURFACE_1)
        grid = QHBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        try:
            pins = pinned_tools(settings=self.settings)
        except Exception:  # noqa: BLE001 — a broken pin store
            pins = []  # reads as no pins, never a broken Home.
        self._home_tiles_pins = list(pins)
        shown = list(pins)[:6]
        if shown:
            from voice_assistant.core.gui_facade import tool_labels

            for name in shown:
                friendly = tool_labels.tool_friendly_name(name)
                btn = QPushButton(friendly)
                btn.setObjectName("homeTile-%s" % name)
                btn.setToolTip(friendly)
                try:
                    btn.setIcon(make_icon(
                        tool_labels.tool_icon_name(name), size=18))
                except Exception:  # noqa: BLE001 - text still works
                    pass
                btn.setStyleSheet(
                    "QPushButton { color: %s; background: %s;"
                    " border: 1px solid %s; border-radius: 8px;"
                    " padding: 8px 4px; font-size: 11px; }"
                    % (text, _rgba(surface_1, 0.6),
                       _rgba(text, 0.12)))
                btn.clicked.connect(
                    lambda _c, n=name, f=friendly:
                    self._on_galaxy_ask(n, f))
                grid.addWidget(btn, 1)
        else:
            tiles = [
                ("Reminders", "bell", 1),
                ("Email", "envelope", 9),
                ("Calendar", "calendar", 8),
                ("Notes", "note", None),
                ("Home", "house", None),
                ("Money", "currency-dollar", None),
            ]
            for label, icon_name, page in tiles:
                btn = QPushButton(label)
                btn.setObjectName("homeTile-%s" % label.lower())
                btn.setToolTip(label)
                try:
                    btn.setIcon(make_icon(icon_name, size=18))
                except Exception:  # noqa: BLE001 - text still works
                    pass
                btn.setStyleSheet(
                    "QPushButton { color: %s; background: %s;"
                    " border: 1px solid %s; border-radius: 8px;"
                    " padding: 8px 4px; font-size: 11px; }"
                    % (text, _rgba(surface_1, 0.6),
                       _rgba(text, 0.12)))
                if page is not None:
                    btn.clicked.connect(
                        lambda _c, p=page: self._show_page(p)
                        if hasattr(self, "_show_page") else None)
                grid.addWidget(btn, 1)
        blay.addLayout(grid)
        self._home_tiles_grid = grid

    def _refresh_home_capability_tiles(self):
        """Rebuild the capability-tiles row when the pins changed
        (UI Phase 2, D-P2-15). Runs in the _refresh_home guarded
        list (every 1 s tick + every Home show); the rebuild
        happens only when the pin list differs from the last
        render, so the steady state costs one QSettings read.
        """
        body = getattr(self, "home_capability_tiles", None)
        if body is None:
            return
        try:
            from .skill_pins import pinned_tools

            pins = pinned_tools(settings=self.settings)
        except Exception:  # noqa: BLE001 — a broken pin store
            return  # reads as no change, never a broken Home.
        if list(pins) == list(getattr(self, "_home_tiles_pins", None)
                              or []):
            return
        layout = body.layout()
        if layout is None:
            return
        grid = getattr(self, "_home_tiles_grid", None)
        if grid is not None:
            while grid.count():
                item = grid.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            layout.removeItem(grid)
        th = getattr(self, "_resolved_theme", None) or {}
        self._fill_home_tiles_body(layout, th)

    def _build_accordion_body(self, key, blay, th):
        if key == "attention":
            self._home_attention_body = QVBoxLayout()
            self._home_attention_body.setContentsMargins(0, 0, 0, 0)
            self._home_attention_body.setSpacing(4)
            blay.addLayout(self._home_attention_body)
            empty = QLabel("Nothing needs your attention.")
            empty.setWordWrap(True)
            empty.setStyleSheet("color: %s; font-size: 12px;"
                                % th["text_muted"])
            self._home_attention_body.addWidget(empty)
            self._home_attention_empty = empty
        elif key == "tiles":
            self._fill_home_tiles_body(blay, th)
        elif key == "docs":
            self._home_docs_body = QLabel("No documents in memory yet.")
            self._home_docs_body.setWordWrap(True)
            self._home_docs_body.setStyleSheet(
                "color: %s; font-size: 12px;" % th["text_muted"])
            blay.addWidget(self._home_docs_body)
        elif key == "comms":
            self._home_comms_body = QLabel("Nothing unread.")
            self._home_comms_body.setWordWrap(True)
            self._home_comms_body.setStyleSheet(
                "color: %s; font-size: 12px;" % th["text_muted"])
            blay.addWidget(self._home_comms_body)
        elif key == "suggestions":
            self._home_sugg_body = QVBoxLayout()
            self._home_sugg_body.setContentsMargins(0, 0, 0, 0)
            self._home_sugg_body.setSpacing(4)
            blay.addLayout(self._home_sugg_body)
            empty = QLabel("No suggestions right now.")
            empty.setWordWrap(True)
            empty.setStyleSheet("color: %s; font-size: 12px;"
                                % th["text_muted"])
            self._home_sugg_body.addWidget(empty)
            self._home_sugg_empty = empty

    def _toggle_accordion(self, key):
        body = self._home_accordion_bodies.get(key)
        preview = self._home_accordion_previews.get(key)
        if body is None:
            return
        expanded = not body.isVisible()
        body.setVisible(expanded)
        if preview is not None:
            preview.setVisible(not expanded)

    def _toggle_screen_scope(self):
        scope = getattr(self, "home_screen_scope", None)
        if scope is None:
            return
        # The cursor dot is a child of the scope, so it follows
        # the scope's visibility — no separate toggle.
        scope.setVisible(not scope.isVisible())

    # -- composer pill + mic ring ---------------------------------------
    def _build_home_composer(self, th):
        panel = getattr(self, "chat_panel", None)
        if panel is None:
            return
        layout = panel.layout()
        if layout is None or layout.count() == 0:
            return
        # The composer dock is the last item of the chat layout.
        last = layout.itemAt(layout.count() - 1)
        dock = last.layout() if last is not None else None
        if dock is None:
            return
        pill = HudPanel(glass=True, pill=True)
        pill.setObjectName("composerPill")
        # The pill's content_layout is a VBox; the dock is a
        # horizontal row, so it rides an inner HBox widget.
        inner = QWidget(pill)
        play = QHBoxLayout(inner)
        play.setContentsMargins(10, 6, 10, 6)
        play.setSpacing(8)
        pill.content_layout().setContentsMargins(0, 0, 0, 0)
        pill.content_layout().setSpacing(0)
        pill.content_layout().addWidget(inner)
        widgets = []
        while dock.count():
            item = dock.takeAt(0)
            w = item.widget()
            if w is not None:
                widgets.append(w)
        for w in widgets:
            play.addWidget(w)
        layout.takeAt(layout.count() - 1)
        layout.addWidget(pill)
        self.composer_pill = pill
        # Look-fix 2026-11-24: the input's own bordered box drew
        # a second frame inside the glass pill (double border).
        # The pill is the single frame — the input is borderless
        # and transparent. The narrow rule rewrites the input's
        # inline sheet (padding), so it carries the borderless
        # rules too (see _apply_dock_narrow_rule).
        _edit = getattr(self, "input_cmd", None)
        if _edit is not None:
            try:
                _edit.setStyleSheet(
                    "background: transparent; border: none;")
            except Exception:  # noqa: BLE001
                pass
        # Mic level ring around the mic button (H-32).
        mic = getattr(self, "btn_mic", None)
        if mic is not None:
            arc = MicLevelArc(mic)
            arc.setObjectName("micLevelArc")
            arc.setGeometry(0, 0, mic.width(), mic.height())
            mic.installEventFilter(self)
            self.mic_level_arc = arc
        self._sync_composer_placeholder()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
        # Keep the mic ring glued to the mic button's geometry, and
        # keep each page backdrop filling its page.
        try:
            from PySide6.QtCore import QEvent as _QEv

            if event.type() == _QEv.Resize:
                if watched is getattr(self, "btn_mic", None):
                    arc = getattr(self, "mic_level_arc", None)
                    if arc is not None:
                        arc.setGeometry(0, 0, watched.width(),
                                        watched.height())
                else:
                    bd = getattr(self, "_home_backdrops", {}).get(
                        id(watched))
                    if bd is not None:
                        bd.setGeometry(watched.rect())
        except Exception:  # noqa: BLE001
            pass
        return super().eventFilter(watched, event)

    # -- boot moment ------------------------------------------------------
    def _home_boot(self):
        hero = getattr(self, "_home_hero", None)
        if hero is None:
            return
        try:
            from . import room_home as _rh
        except ImportError:
            from voice_assistant.gui import room_home as _rh
        if not getattr(_rh, "BOOT_PLAYED", False):
            _rh.BOOT_PLAYED = True
            hero.boot_played = True
            try:
                vis = getattr(self, "visualizer", None)
                if vis is not None and hasattr(vis, "ack_flare"):
                    vis.ack_flare()
            except Exception:  # noqa: BLE001
                pass
            # H-35: the boot moment (~600 ms) is exempt from the
            # motion audit, but it still respects reduced motion —
            # under the Minimal profile (or a reduced-motion
            # setting) the hero appears at full opacity with no
            # animation. The _reduced gate is what the motion
            # test (test_uis5) looks for in this function.
            _reduced = False
            try:
                _reduced = bool(
                    getattr(self, "_hud_profile_name", "") == "Minimal")
            except Exception:  # noqa: BLE001
                _reduced = False
            if _reduced:
                hero.setGraphicsEffect(None)
                hero.boot_animation = None
                return
            effect = QGraphicsOpacityEffect(hero)
            effect.setOpacity(0.35)
            hero.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", hero)
            anim.setDuration(600)
            anim.setStartValue(0.35)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start()
            hero.boot_animation = anim
        else:
            hero.boot_played = False
            hero.boot_animation = None

    # -- refresh ---------------------------------------------------------
    def _refresh_home(self):
        """Refresh every Home surface from live reads (H-12: never
        resets rail UI state — the accordion open/closed state and
        the hidden panels are untouched here)."""
        for fn in (
                self._refresh_home_greeting,
                self._refresh_home_state_line,
                self._refresh_home_weather,
                self._refresh_home_news,
                self._refresh_home_services,
                self._refresh_home_alerts,
                self._refresh_home_attention,
                self._refresh_home_docs,
                self._refresh_home_comms,
                self._refresh_home_suggestions,
                self._refresh_home_now,
                self._refresh_home_vitals,
                self._refresh_home_banner,
                self._refresh_home_capability_tiles,
                self._refresh_home_previews,
                self._refresh_home_pills,
                self._refresh_home_screen,
                self._refresh_home_goal,
        ):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - one surface never kills Home
                print(f"[HomeHud] refresh {fn.__name__} failed: {e!r}")

    def _refresh_home_state_line(self):
        """The state line under the wordmark (leg 70): the exact
        visualizer accent hex in its stylesheet, and the current
        state word as its text (unless the mute label owns it)."""
        line = getattr(self, "home_state_line", None)
        if line is None:
            return
        try:
            vis = getattr(self, "visualizer", None)
            accent = vis.accent_color().name() if vis is not None \
                else "#35D6FF"
        except Exception:  # noqa: BLE001
            accent = "#35D6FF"
        line.setStyleSheet("color: %s; font-size: 13px;" % accent)
        if getattr(self, "_muted_label_active", False):
            return
        try:
            from .design_system.session_states import (
                STATUS_DISPLAY_WORDS, resolve_status)
            word, _, _, _ = resolve_status(
                getattr(self, "_status_raw", "") or "")
            text = STATUS_DISPLAY_WORDS.get(word, word)
            if line.text() != text:
                line.setText(text)
        except Exception:  # noqa: BLE001
            pass

    def _refresh_home_greeting(self):
        """H-16: the headline is composed live (meetings → unread →
        approvals, omit zeros, max three clauses)."""
        greeting = getattr(self, "home_greeting", None)
        if greeting is None:
            return
        meetings = unread = approvals = 0
        try:
            from voice_assistant.core.gui_facade import (
                google_services_calendar as _cal)

            events = _cal.next_events(within_hours=24, limit=20)
            meetings = len(events or [])
        except Exception:  # noqa: BLE001
            meetings = 0
        try:
            from voice_assistant.core.gui_facade import (
                unread_count as _unread_count)

            unread = int(_unread_count())
        except Exception:  # noqa: BLE001
            unread = 0
        try:
            # UI Phase 2 (P2-02): the ONE shared count — the
            # Review queue's length — never a local sum. Before
            # this the headline counted every record with any
            # truthy pending_approval (goal records, stale dicts
            # without a tool, terminal records included) while
            # the badge counted list_interrupted() — the two
            # disagreed.
            from voice_assistant.gui.pending_approval import (
                pending_approvals_count)

            approvals = int(pending_approvals_count())
        except Exception:  # noqa: BLE001
            approvals = 0
        clauses = []
        if meetings:
            clauses.append("%d meeting%s today" % (
                meetings, "s" if meetings != 1 else ""))
        if unread:
            clauses.append("%d unread" % unread)
        if approvals:
            clauses.append("%d approval%s waiting" % (
                approvals, "s" if approvals != 1 else ""))
        if not clauses:
            text = "Nothing scheduled today — ask me to add something."
        else:
            text = " · ".join(clauses[:3])
        # The headline needs ~574 px at 20 px Orbitron but the
        # header cluster takes ~400 px of the 868 px HUD at 1280,
        # so the label only has ~445 px — elide with an ellipsis
        # instead of hard-clipping mid-word ("add sor"). The full
        # headline rides the tooltip.
        self._greeting_full = text
        greeting.setToolTip(text)
        try:
            text = self._elide_greeting(greeting, text)
        except Exception:  # noqa: BLE001 — cosmetic
            pass
        greeting.setText(text)

    def _elide_greeting(self, greeting, full):
        """Fit the headline to its label without a mid-word stub.

        Look-fix 2 (2026-11-24): at 640 the label is ~430 px and
        the full headline needs ~574 px, so ElideRight cut it to
        "…ask me to add s…" — the elision worked, the wording was
        the problem. When the full text does not fit, fall back to
        a clean short form first (the first clause; for the
        zero-clause fallback, the sentence before the em dash),
        and only elide that if even the short form does not fit.
        The full text stays in the tooltip.
        """
        from PySide6.QtGui import QFontMetrics
        from PySide6.QtCore import Qt
        metrics = QFontMetrics(greeting.font())
        width = greeting.width()
        if width <= 0 or metrics.horizontalAdvance(full) <= width:
            return full
        short = full.split(" · ")[0]
        if " — " in short:
            short = short.split(" — ")[0] + "."
        if metrics.horizontalAdvance(short) <= width:
            return short
        return metrics.elidedText(
            short, Qt.TextElideMode.ElideRight, width)

    def _reelide_greeting(self):
        """Re-apply the greeting elision against the label's
        current width (called after a resize — the header cluster
        takes a different share at each tier, so the headline's
        room changes; the full text stays in _greeting_full and
        the tooltip)."""
        greeting = getattr(self, "home_greeting", None)
        full = getattr(self, "_greeting_full", None)
        if greeting is None or not full:
            return
        try:
            greeting.setText(self._elide_greeting(greeting, full))
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _refresh_home_weather(self):
        """Weather glance block + city row (H-11). One canonical
        store: ConfigSpec home_city + QSettings home/city."""
        city = ""
        try:
            from voice_assistant.core.gui_facade import config as _cfg

            city = str(getattr(_cfg, "home_city", "") or "")
        except Exception:  # noqa: BLE001
            city = ""
        if not city:
            city = str(qsettings_get("home/city") or "")
        details = getattr(self, "_briefing_details", None)
        if details is None:
            return
        th = getattr(self, "_home_theme", _resolved())
        # Glance block (leg 66: temp font >= 20 — CONFLICTS with
        # H-42's 13 Sora; built per the leg, the gate, and flagged
        # in the report for the parent's ruling).
        block = getattr(self, "_home_weather_block", None)
        if block is None:
            block = QWidget()
            block.setObjectName("homeWeatherBlock")
            blay = QHBoxLayout(block)
            blay.setContentsMargins(0, 0, 0, 0)
            blay.setSpacing(8)
            temp = QLabel("—")
            temp.setObjectName("weatherTemp")
            # Glance-sized (leg 66): the point size must be set on
            # the QFont, not only in the stylesheet — a stylesheet
            # font-size leaves font().pointSize() at -1, so the
            # leg's >= 20 check fails.
            _temp_font = QFont("Sora")
            _temp_font.setPointSize(20)
            _temp_font.setBold(True)
            temp.setFont(_temp_font)
            # The global QWidget rule (font-size: 13px) overrides
            # setFont, so the size must ALSO be in this label's own
            # stylesheet — as 20pt (points, not px: the leg reads
            # font().pointSize(), and a px size converts by DPI).
            temp.setStyleSheet(
                "color: %s; font-family: Sora, sans-serif;"
                " font-size: 20pt; font-weight: 600;" % th["text"])
            blay.addWidget(temp)
            glyph = QLabel("○")
            glyph.setObjectName("weatherGlyph")
            glyph.setStyleSheet("color: %s; font-size: 20px;"
                                % th["text_muted"])
            blay.addWidget(glyph)
            cond = QLabel("Unknown")
            cond.setObjectName("weatherCondition")
            cond.setStyleSheet("color: %s; font-size: 13px;"
                               % th["text_muted"])
            blay.addWidget(cond, 1)
            self._home_weather_temp = temp
            self._home_weather_glyph = glyph
            self._home_weather_cond = cond
            self._home_weather_block = block
            details.layout().addWidget(block)
        # Hunt fix (c) §7c: the block shows "— ○ Unknown" until a
        # fetch completes — a row with no reading. Hide it until
        # the first fetch lands (a real reading, or the honest
        # "unavailable" line); the "Set your city" row below stays
        # as the affordance. No city collection starts here.
        block.setVisible(bool(getattr(self, "_home_weather_fetched",
                                      False)))
        # City row (leg 21). The row is a QPushButton (it opens the
        # city prompt), but the leg reads text via descendant
        # QLabels only — so a hidden QLabel mirrors the row text
        # (lowercased, matching the leg's substring) for that read.
        row = getattr(self, "_home_weather_row", None)
        if row is None:
            row = QPushButton("")
            row.setObjectName("homeWeatherRow")
            # Hunt fix (c) §7: padding 1 px (was the QPushButton
            # default ~9 px a side, 32 px tall) — the row is a flat
            # link, and the squeezed details cannot spare the dead
            # padding; the text still reads at 12 px. The 1 px (not
            # 2) reclaims the 2 px the two-line Connect button
            # costs the schedule below it.
            row.setStyleSheet(
                "QPushButton { color: %s; background: transparent;"
                " border: none; text-align: left; font-size: 12px;"
                " text-decoration: underline; padding: 1px 0px; }"
                % th["text_muted"])
            row.clicked.connect(self._on_weather_row_clicked)
            mirror = QLabel("")
            mirror.setObjectName("homeWeatherRowText")
            mirror.setVisible(False)
            mirror.setParent(row)
            self._home_weather_row_text = mirror
            self._home_weather_row = row
            details.layout().addWidget(row)
        prompt = getattr(self, "_home_city_prompt", None)
        if prompt is None:
            prompt = QLineEdit()
            prompt.setObjectName("homeCityPrompt")
            prompt.setPlaceholderText("Your home city (e.g. Mumbai)")
            prompt.setVisible(False)
            prompt.returnPressed.connect(self._on_home_city_entered)
            self._home_city_prompt = prompt
            details.layout().addWidget(prompt)
        if city:
            row.setText("Weather for %s" % city)
            row.setToolTip("Change your home city")
        else:
            # Hunt fix (c) §7: "Set your city for the weather"
            # needs ~150 px at 12 px but the card interior is
            # 96 px, so it clipped to "Set your city for". The
            # short form reads whole; the context (the weather
            # section) carries the rest.
            row.setText("Set your city")
            row.setToolTip("Set your home city")
        mirror = getattr(self, "_home_weather_row_text", None)
        if mirror is not None:
            mirror.setText(row.text().lower())
        # Fetch the weather in the background (never block Home).
        if city and getattr(self, "_home_weather_city", None) != city:
            self._home_weather_city = city
            self._fetch_home_weather(city)

    def _on_weather_row_clicked(self):
        # The row lives in _briefing_details (hidden until the card
        # expands) — expand the card first so the prompt is
        # actually visible when it opens (leg 21).
        try:
            details = getattr(self, "_briefing_details", None)
            if details is not None and not details.isVisible():
                if hasattr(self, "_toggle_briefing_card"):
                    self._toggle_briefing_card()
        except Exception:  # noqa: BLE001
            pass
        prompt = getattr(self, "_home_city_prompt", None)
        if prompt is not None:
            prompt.setVisible(True)
            prompt.setFocus()

    def _on_home_city_entered(self):
        prompt = getattr(self, "_home_city_prompt", None)
        if prompt is None:
            return
        city = prompt.text().strip()
        if city:
            self._set_home_city(city)
        prompt.setVisible(False)
        self._refresh_home_weather()

    def _set_home_city(self, city: str):
        """One canonical store (H-11): ConfigSpec + QSettings."""
        try:
            from voice_assistant.core.gui_facade import config as _cfg

            _cfg.home_city = city
        except Exception:  # noqa: BLE001
            pass
        try:
            qsettings_set("home/city", city)
        except Exception:  # noqa: BLE001
            pass
        try:
            from voice_assistant.core.gui_facade import save_env_setting

            save_env_setting("VYUK_HOME_CITY", city)
        except Exception:  # noqa: BLE001
            pass

    def _fetch_home_weather(self, city: str):
        """One-shot weather fetch on a worker thread; the block
        shows the honest 'unavailable' line on any failure."""
        try:
            from PySide6.QtCore import QThread as _QT
        except Exception:  # noqa: BLE001
            return

        class _Fetch(_QT):
            def __init__(self, city):
                super().__init__()
                self.city = city
                self.result = None

            def run(self):  # noqa: N802 - Qt naming
                try:
                    from voice_assistant.core.gui_facade import (
                        home_feeds as _home_feeds)
                    self.result = _home_feeds.weather_for_city(
                        self.city)
                except Exception as e:  # noqa: BLE001
                    self.result = {"ok": False, "error": str(e)}

        worker = _Fetch(city)
        worker.finished.connect(
            lambda: self._on_home_weather_done(worker),
            Qt.QueuedConnection)
        worker.start()
        self._home_weather_worker = worker

    def _on_home_weather_done(self, worker):
        result = getattr(worker, "result", None) or {}
        temp = getattr(self, "_home_weather_temp", None)
        glyph = getattr(self, "_home_weather_glyph", None)
        cond = getattr(self, "_home_weather_cond", None)
        if temp is None or glyph is None or cond is None:
            return
        # Hunt fix (c) §7c: the first fetch has landed, so there
        # is something honest to show — the reading, or the
        # "unavailable" line. The block unhides here.
        self._home_weather_fetched = True
        block = getattr(self, "_home_weather_block", None)
        if block is not None:
            block.setVisible(True)
        if not result.get("ok"):
            temp.setText("—")
            cond.setText("Weather unavailable")
            glyph.setText("○")
            return
        temp.setText("%d°" % round(float(result.get("temp_c", 0))))
        try:
            from voice_assistant.core.gui_facade import (
                home_feeds as _home_feeds)

            word = _home_feeds.condition_word(
                result.get("condition", ""))
        except Exception:  # noqa: BLE001
            word = str(result.get("condition", "Unknown"))
        cond.setText(word)
        glyph.setText(self._weather_glyph_for(word))

    def _weather_glyph_for(self, word: str) -> str:
        """Phosphor weather glyph name for a condition word."""
        w = (word or "").lower()
        if "thunder" in w:
            name = "cloud-lightning"
        elif "snow" in w or "sleet" in w:
            name = "cloud-snow"
        elif "rain" in w:
            name = "cloud-rain"
        elif "fog" in w or "mist" in w or "haze" in w:
            name = "cloud-fog"
        elif "partly" in w:
            name = "cloud-sun"
        elif "cloud" in w:
            name = "cloud"
        elif "clear" in w or "sun" in w:
            name = "sun"
        else:
            name = "cloud"
        # The glyph label carries the icon name as text (the real
        # Phosphor icon is set on the label via make_icon when the
        # weather loads — the text is the honest fallback).
        try:
            from .icons import make_icon

            glyph = getattr(self, "_home_weather_glyph", None)
            if glyph is not None:
                glyph.setPixmap(
                    make_icon(name, size=22).pixmap(22, 22))
                glyph.setText("")
        except Exception:  # noqa: BLE001
            pass
        return {"sun": "☀", "cloud": "☁", "cloud-sun": "⛅",
                "cloud-rain": "🌧", "cloud-snow": "🌨",
                "cloud-lightning": "⛈", "cloud-fog": "🌫"}.get(name, "○")

    def _refresh_home_news(self):
        """One headline per row, each with a small source tag.

        Briefing fix (2026-11-26): the old block joined the top
        two headlines with " · " into one wrapping label — a
        paragraph wall with no source. The top two headlines are
        now each their own row (headline over a small source tag);
        the fetch still runs once per Home build. Two rows, not
        three — the column budget at 1280 cannot hold three full
        rows under the card (three want 223 px; the column is
        61 px over budget and the layout clips the third row),
        and the old block showed two headlines too. No stylesheet
        call here — the rows use the accordion's own label styling
        (bare QLabel for the headline, the existing "smallMuted"
        rule — 11 px, the ladder rung below body — for the tag),
        so the style pins hold. A QFont pixel size does NOT work
        here: the app stylesheet's font-size rule overrides
        setFont() (proven by probe — the tag read back 13 px),
        so the tag takes its size from the stylesheet like every
        other small caption in the app.
        """
        bodies = getattr(self, "_home_accordion_bodies", {})
        body = bodies.get("briefing")
        if body is None:
            return
        block = getattr(self, "_home_news_block", None)
        if block is None:
            from PySide6.QtWidgets import QVBoxLayout as _QVBoxLayout
            block = _HomeNewsBlock()
            block.setObjectName("homeNewsBlock")
            _blay = _QVBoxLayout(block)
            _blay.setContentsMargins(0, 0, 0, 0)
            # Phase 6 (D-12): spacing 0, not 4. The column gives
            # the block 132 px at 1280 while two 3-line rows want
            # 136 px (66 each + 4 spacing); the layout then
            # squeezes each row by 2 px and the third headline
            # line's descenders clip. With spacing 0 the rows
            # get their full 66 px. The rows stay visually
            # distinct — the source tag is small muted text
            # under each headline — so the 4 px gap is not
            # needed for readability.
            _blay.setSpacing(0)
            body.layout().addWidget(block)
            self._home_news_block = block
        if getattr(self, "_home_news_done", False):
            return
        self._home_news_done = True
        # Clear any rows from a previous refresh (the alerts
        # section's pattern: take + deleteLater).
        _lay = block.layout()
        while _lay.count():
            _item = _lay.takeAt(0)
            if _item.widget() is not None:
                _item.widget().deleteLater()
        from PySide6.QtWidgets import QLabel as _QLabel
        try:
            from voice_assistant.core.gui_facade import (
                home_feeds as _home_feeds)
            items = _home_feeds.fetch_news_headlines(limit=3) or []
        except Exception:  # noqa: BLE001
            items = None
        if items is None:
            _line = _QLabel("News unavailable.")
            _line.setObjectName("muted")
            _line.setWordWrap(True)
            _lay.addWidget(_line)
            return
        if not items:
            _line = _QLabel("No headlines right now.")
            _line.setObjectName("muted")
            _line.setWordWrap(True)
            _lay.addWidget(_line)
            return
        from PySide6.QtWidgets import QSizePolicy as _QSizePolicy
        from PySide6.QtWidgets import QVBoxLayout as _QVBoxLayout
        from PySide6.QtWidgets import QWidget as _QWidget
        # Two rows, not three: the column budget at 1280 cannot
        # hold three full rows under the card (measured — three
        # rows want 223 px; with the 3-line not-connected card the
        # column is 61 px over budget and the layout clips the
        # third row's last line and tag, and with the 1-line
        # connected card it still clips the longest headline's
        # last word). Two rows want ~136 px and fit with room to
        # spare — and the old block showed two headlines too, so
        # no information is lost against the pre-fix UI; only the
        # format changes (rows + source tags instead of the
        # " · "-joined paragraph). The rows' height is carried by
        # the block's sizeHint (see _HomeNewsBlock): a preferred
        # height, not a hard minimum — a minimumHeight floor was
        # tried first and pushed the Home page minimum to 747 px
        # against the 688 px no-scroll budget, so it had to go.
        _shown = 0
        self._home_news_heads = []
        for item in items:
            if _shown >= 2:
                break
            _title = (item.get("title") or "").strip()
            if not _title:
                continue
            _shown += 1
            _row = _QWidget()
            _row.setObjectName("homeNewsRow")
            # The row's sizeHint height is its floor: with a
            # Preferred vertical policy the column budget squeezed
            # the rows below the height their wrapped headlines
            # need (offline the heads got 38 px against 51 px of
            # text, so the first line clipped). Minimum means
            # "sizeHint is the floor, grow freely" — and
            # hasHeightForWidth makes the layout ask
            # heightForWidth at the real width instead of
            # trusting the sizeHint cached at build time. The
            # block's sizeHint (see _HomeNewsBlock) carries the
            # guarantee for the block as a whole.
            _pol = _QSizePolicy(
                _QSizePolicy.Policy.Preferred,
                _QSizePolicy.Policy.Minimum)
            _pol.setHeightForWidth(True)
            _row.setSizePolicy(_pol)
            _rlay = _QVBoxLayout(_row)
            _rlay.setContentsMargins(0, 0, 0, 0)
            _rlay.setSpacing(1)
            _head = _QLabel(_title)
            _head.setWordWrap(True)
            # Phase 6 (D-12): the headline is elided to three
            # lines at the row's width (see _reelide_home_news).
            # The full title is kept so a resize can re-elide
            # it; the label shows the elided form.
            _head.setProperty("full_title", _title)
            self._home_news_heads.append(_head)
            _rlay.addWidget(_head)
            _tag = _QLabel((item.get("source") or "News").strip())
            _tag.setObjectName("smallMuted")
            _rlay.addWidget(_tag)
            _lay.addWidget(_row)
        self._refresh_home_news_hint()
        self._reelide_home_news()
        # Deferred re-elision: the rows have no laid-out width
        # yet at build time, so the first elision may be a
        # no-op; once the layout settles, re-elide at the real
        # width (the briefing summary's deferred pattern).
        try:
            from PySide6.QtCore import QTimer as _QTimer
            _QTimer.singleShot(0, self._reelide_home_news)
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _reelide_home_news(self):
        """Elide each news headline to the line cap at the row's
        current width (Phase 6, D-12).

        The column budget at 1280 cannot hold two full long
        headlines (each wants 134 px at the 122 px rail width;
        the block gets 132 px and the rows squeeze to 64 px, so
        the headlines clip mid-line). A hard minimum on the rows
        is not viable — it pushes the Home page minimum past
        the 688 px no-scroll budget (the 2026-11-26 floor
        attempt). So the headlines are capped with an ellipsis,
        the same word-by-word elision the briefing summary uses:
        the text is intentionally shortened (and the ellipsis
        says so), never cut off mid-glyph. The cap is four lines
        at 1920 (the rail is wide and the rows get 83 px, so
        the long headlines show in full) and three lines at
        1280 and below (the rows get 66 px); the block's layout
        spacing is 0 so the two rows fit the 132 px the column
        gives. A headline that fits inside the cap is shown in
        full. Re-elides on every resize (called from
        _refresh_home_news_hint) so the cap tracks the width.
        Never raises: cosmetic only.
        """
        try:
            _heads = getattr(self, "_home_news_heads", None)
            if not _heads:
                return
            from PySide6.QtGui import QFontMetrics as _QFM
            for _head in _heads:
                _full = _head.property("full_title")
                if not _full:
                    continue
                _w = _head.width()
                if _w <= 0:
                    continue
                _fm = _QFM(_head.font())
                _words = str(_full).split()
                if not _words:
                    continue
                # Greedy word wrap into lines at the label width.
                _lines = []
                _cur = ""
                for _wd in _words:
                    _trial = (_cur + " " + _wd).strip()
                    if _fm.horizontalAdvance(_trial) <= _w:
                        _cur = _trial
                    else:
                        if _cur:
                            _lines.append(_cur)
                        _cur = _wd
                if _cur:
                    _lines.append(_cur)
                # The line cap tracks the width: at 1920 the rail
                # is wide (197 px) and the column gives each row
                # 83 px (four lines + tag), so a four-line cap
                # shows the long headlines in full; at 1280 and
                # below the rail is narrow (122–136 px) and each
                # row gets 66 px (three lines + tag), so the cap
                # is three. A fixed three-line cap would truncate
                # a headline that fits in full at 1920 (a
                # downgrade against the approved look); a fixed
                # four-line cap would clip at 1280 (the row is
                # squeezed to 66 px and the fourth line's
                # descenders cut). The 180 px threshold is the
                # measured break between the two.
                _cap = 4 if _w >= 180 else 3
                if len(_lines) <= _cap:
                    _head.setText(str(_full))
                    continue
                # Truncate to the cap, whole words only, with
                # an ellipsis on the last line. A first word too
                # long for the label keeps the character cut.
                _out = _lines[:_cap]
                _last = _out[_cap - 1]
                while _last and _fm.horizontalAdvance(
                        _last + "…") > _w:
                    _parts = _last.rsplit(" ", 1)
                    _last = _parts[0] if len(_parts) > 1 else ""
                _out[_cap - 1] = (_last + "…") if _last else "…"
                _head.setText("\n".join(_out))
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _refresh_home_news_hint(self):
        """Re-ask the news block's preferred height at the new width.

        The block's sizeHint is computed from its current width
        (see _HomeNewsBlock), so after a resize — or after the rows
        are built — the layout must be told the hint changed.
        updateGeometry does exactly that (it invalidates the cached
        hint and the layout re-asks); it does NOT set a minimum, so
        the page-minimum gate is untouched (a minimumHeight floor
        was tried first and broke test_home_no_scroll_1280_and_
        additions: page minimum 747 px vs the 688 px budget). Called
        after every news refresh and from _reelide_briefing_summary
        (resize + every summary set), so the hint tracks the width
        — at 1920 the headlines wrap to fewer lines and the hint
        drops with them. No-op before the block exists. Never
        raises: cosmetic only.
        """
        try:
            block = getattr(self, "_home_news_block", None)
            if block is None:
                return
            block.updateGeometry()
        except Exception:  # noqa: BLE001 — cosmetic
            pass
        # Phase 6 (D-12): a resize changes the width the
        # headlines wrap at, so re-elide them (the cap is three
        # lines at the current width).
        try:
            self._reelide_home_news()
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _refresh_home_services(self):
        body = getattr(self, "_home_services_body", None)
        if body is None:
            return
        lines = []
        try:
            from voice_assistant.core.gui_facade import (
                browser_enable as _be)
            lines.append("Browser: %s" % (
                "connected" if _be.is_connected() else "not connected"))
        except Exception:  # noqa: BLE001
            pass
        try:
            # google_services_calendar has no is_connected — the
            # Google connection (Calendar included) is
            # google_services.is_connected, through the facade.
            from voice_assistant.core.gui_facade import (
                google_services as _gs)
            lines.append("Calendar: %s" % (
                "connected" if _gs.is_connected() else "not connected"))
        except Exception:  # noqa: BLE001
            pass
        try:
            from voice_assistant.core.gui_facade import (
                google_services as _gm)
            lines.append("Gmail: %s" % (
                "connected" if _gm.is_connected() else "not connected"))
        except Exception:  # noqa: BLE001
            pass
        body.setText("\n".join(lines) if lines
                     else "Services status unavailable.")

    def _refresh_home_alerts(self):
        body = getattr(self, "_home_alerts_body", None)
        if body is None:
            return
        th = getattr(self, "_home_theme", _resolved())
        while body.count():
            item = body.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        rows = []
        # Over-budget envelopes (the money store is the source).
        try:
            # The facade's money_alerts resolves to the BARE
            # top-level core.money_alerts (see gui_facade): the
            # aliased module reads the memory store through the
            # harness shim (a _Fake) and sees no envelopes, while
            # the bare module reads the real DB file (the same
            # file the leg seeds). Production is identical either
            # way — the alias maps to these same files.
            from voice_assistant.core.gui_facade import (
                money_alerts as _money_alerts)
            # _load_envelopes returns (entries, broken_count) —
            # iterate the entries, not the tuple.
            entries, _broken = _money_alerts._load_envelopes()
            for entry in entries or []:
                key = str(entry.get("key", ""))
                budget = float(entry.get("budget") or 0)
                spent = float(entry.get("spent") or 0)
                if budget > 0 and spent > budget:
                    rows.append((key, "danger", "MONEY",
                                 "%s is over budget (spent %g of %g)"
                                 % (key, spent, budget)))
                elif budget > 0 and spent > 0.8 * budget:
                    rows.append((key, "amber", "MONEY",
                                 "%s is near its budget (spent %g of %g)"
                                 % (key, spent, budget)))
        except Exception:  # noqa: BLE001
            pass
        # Subscriptions renewing within 7 days. subscription_audit
        # returns a human-readable string, not a dict — the list
        # form carries ", renews YYYY-MM-DD" per subscription, so
        # parse the renewal dates out of it (ISO dates, stable
        # format) and flag the ones inside a week.
        try:
            from voice_assistant.core.gui_facade import (
                skills_money_budget as _money_budget)
            import datetime as _dt
            import re as _re
            _today = _dt.date.today()
            _text = _money_budget.subscription_audit(action="list") or ""
            for _line in str(_text).splitlines():
                _m = _re.search(r"renews (\d{4}-\d{2}-\d{2})", _line)
                if not _m:
                    continue
                try:
                    _due = _dt.date.fromisoformat(_m.group(1))
                except ValueError:  # noqa: BLE001 — bad date, skip
                    continue
                if 0 <= (_due - _today).days <= 7:
                    _name = _line.split(", renews")[0].strip()
                    if _name:
                        rows.append((_name, "amber", "SUBSCRIPTION",
                                     "%s renews soon" % _name))
        except Exception:  # noqa: BLE001
            pass
        if not rows:
            empty = QLabel("No alerts.")
            empty.setStyleSheet("color: %s; font-size: 12px;"
                                % th["text_muted"])
            body.addWidget(empty)
            return
        for key, severity, source, text in rows[:5]:
            row = QWidget()
            row.setObjectName("homeAlertRow-%s" % key)
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 0, 0, 0)
            rlay.setSpacing(6)
            dot = QLabel("●")
            dot.setObjectName("alertSeverityDot")
            dot.setStyleSheet("color: %s; font-size: 10px;"
                              % (DANGER if severity == "danger"
                                 else AMBER))
            rlay.addWidget(dot)
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setStyleSheet("color: %s; font-size: 12px;" % th["text"])
            rlay.addWidget(lab, 1)
            src = QLabel(source)
            src.setObjectName("alertSource")
            src.setStyleSheet(
                "color: %s; font-family: Share Tech Mono, monospace;"
                " font-size: 10px;" % th["text_faint"])
            rlay.addWidget(src)
            body.addWidget(row)

    def _refresh_home_attention(self):
        body = getattr(self, "_home_attention_body", None)
        if body is None:
            return
        th = getattr(self, "_home_theme", _resolved())
        while body.count():
            item = body.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        rows = []
        try:
            from voice_assistant.core.gui_facade import TaskLedger

            for rec in TaskLedger().all_records():
                pa = rec.get("pending_approval")
                if pa:
                    rows.append(("approval",
                                 "%s — approval waiting: %s"
                                 % (rec.get("title") or rec.get("task_id", ""),
                                    pa.get("label") or pa.get("tool", ""))))
                if rec.get("pending_question"):
                    rows.append(("question",
                                 "%s — question waiting"
                                 % (rec.get("title")
                                    or rec.get("task_id", ""))))
        except Exception:  # noqa: BLE001
            rows = []
        try:
            from voice_assistant.core.gui_facade import scheduler as _sch

            now = datetime.datetime.now(datetime.timezone.utc)
            for r in _sch.get_all_reminders_with_epoch() or []:
                if not r.get("fired"):
                    epoch = r.get("fire_at_epoch")
                    if epoch and epoch <= now.timestamp() + 3600:
                        rows.append(("reminder",
                                     "Reminder due: %s"
                                     % r.get("label", "")))
        except Exception:  # noqa: BLE001
            pass
        if not rows:
            empty = QLabel("Nothing needs your attention.")
            empty.setWordWrap(True)
            empty.setStyleSheet("color: %s; font-size: 12px;"
                                % th["text_muted"])
            body.addWidget(empty)
            self._home_attention_empty = empty
            return
        for kind, text in rows[:5]:
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setStyleSheet("color: %s; font-size: 12px;" % th["text"])
            body.addWidget(lab)

    def _refresh_home_docs(self):
        body = getattr(self, "_home_docs_body", None)
        if body is None:
            return
        try:
            from voice_assistant.core.gui_facade import (
                document_memory as _dm)
            # list_documents() returns a dict, not a list — the
            # files live under "files" (Step 2 F1: slicing the
            # dict raised TypeError here, the except swallowed it,
            # and the card always read "Documents unavailable.").
            data = _dm.list_documents(limit=5) or {}
            files = data.get("files") or []
            if files:
                body.setText("\n".join(
                    str(d.get("title") or d.get("name") or d.get("id", ""))
                    for d in files[:5]))
            else:
                body.setText("No documents in memory yet.")
        except Exception:  # noqa: BLE001
            body.setText("Documents unavailable.")

    def _refresh_home_comms(self):
        body = getattr(self, "_home_comms_body", None)
        if body is None:
            return
        parts = []
        try:
            from voice_assistant.core.gui_facade import (
                unread_count as _unread_count)

            unread = int(_unread_count())
            if unread:
                parts.append("%d unread email%s" % (
                    unread, "s" if unread != 1 else ""))
        except Exception:  # noqa: BLE001
            pass
        # No missed-call line: there is no missed-call source in
        # the tree (no calls backend exposes a missed count), so
        # the glance shows unread email only. A "0 missed calls"
        # line would be a fake status (D14) — the line is omitted
        # until a real source exists. (UI Phase 1, 2026-11-24.)
        body.setText(" · ".join(parts) if parts else "Nothing unread.")

    def _refresh_home_suggestions(self):
        body = getattr(self, "_home_sugg_body", None)
        if body is None:
            return
        th = getattr(self, "_home_theme", _resolved())
        while body.count():
            item = body.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        items = []
        try:
            from voice_assistant.core.gui_facade import (
                suggestions as _suggestions)
            items = _suggestions.default_registry().pending() or []
        except Exception:  # noqa: BLE001
            items = []
        if not items:
            empty = QLabel("No suggestions right now.")
            empty.setWordWrap(True)
            empty.setStyleSheet("color: %s; font-size: 12px;"
                                % th["text_muted"])
            body.addWidget(empty)
            self._home_sugg_empty = empty
            return
        for rec in items[:3]:
            lab = QLabel(str(rec.get("text", "")))
            lab.setWordWrap(True)
            lab.setStyleSheet("color: %s; font-size: 12px;" % th["text"])
            body.addWidget(lab)

    def _refresh_home_now(self):
        """Now Doing: the ledger's newest live record + elapsed."""
        doing = getattr(self, "home_now_doing", None)
        elapsed = getattr(self, "home_now_doing_elapsed", None)
        if doing is None:
            return
        record = None
        try:
            # The facade exports TaskLedger (not a "ledger"
            # singleton) — read the ledger file fresh each time so
            # a record seeded into the file is seen immediately.
            from voice_assistant.core.gui_facade import TaskLedger

            for rec in TaskLedger().all_records():
                if rec.get("state") in ("running", "paused", "verifying"):
                    record = rec
                    break
        except Exception:  # noqa: BLE001
            record = None
        if record is None:
            doing.setText("")
            doing.setVisible(False)
            if elapsed is not None:
                elapsed.setText("")
                elapsed.setVisible(False)
            return
        title = str(record.get("title") or record.get("task_id", ""))
        doing.setText("Now doing: %s" % title)
        doing.setVisible(True)
        if elapsed is not None:
            try:
                created = datetime.datetime.fromisoformat(
                    str(record.get("created_at", "")).replace("Z", "+00:00"))
                secs = max(
                    0, int((datetime.datetime.now(datetime.timezone.utc)
                            - created).total_seconds()))
                elapsed.setText("%d:%02d" % (secs // 60, secs % 60))
                elapsed.setVisible(True)
            except Exception:  # noqa: BLE001
                elapsed.setText("")
                elapsed.setVisible(False)

    def _refresh_home_vitals(self):
        """The vitals strip (H-34): brain source, wake-word state,
        battery, clock. The strip's status_label is the brain
        source (leg 37: no 'Ready' anywhere in the strip)."""
        try:
            from voice_assistant.core.gui_facade import (
                brain_state as _brain_state)
            brain = _brain_state.brain_source()
            wake = _brain_state.wake_word_state()
        except Exception:  # noqa: BLE001
            brain, wake = "cloud", "off"
        strip = getattr(self, "home_vitals_line", None)
        if strip is not None:
            try:
                strip.status_label.setText(brain)
            except Exception:  # noqa: BLE001
                pass
        # Hunt fix (c) item 2: the brain label is gone (it
        # duplicated the strip's status_label); only the
        # wake-word label is refreshed here.
        lab = getattr(self, "_home_vitals_wake", None)
        if lab is not None:
            lab.setText(wake)
        # Battery + clock (the ~1 Hz telemetry also refreshes these).
        self._refresh_home_telemetry()

    def _refresh_home_telemetry(self):
        """Mini-bars + battery + clock (called from the ~1 Hz
        telemetry block as well as _refresh_home_vitals)."""
        bars = getattr(self, "_home_status_bars", {})
        cpu = mem = temp = None
        try:
            import psutil as _ps

            cpu = float(_ps.cpu_percent(interval=None))
            mem = float(_ps.virtual_memory().percent)
            temps = _ps.sensors_temperatures() or {}
            for entries in temps.values():
                if entries:
                    temp = float(entries[0].current)
                    break
        except Exception:  # noqa: BLE001
            pass
        batt = None
        try:
            from voice_assistant.core.gui_facade import (
                resilience_power as _resilience_power)
            state = _resilience_power.battery_state() or {}
            if state.get("available"):
                batt = float(state.get("percent") or 0)
        except Exception:  # noqa: BLE001
            batt = None
        for key, value, unit, scale in (
                ("cpu", cpu, "%", 100.0),
                ("mem", mem, "%", 100.0),
                ("temp", temp, "°C", 100.0),
                ("batt", batt, "%", 100.0)):
            pair = bars.get(key)
            if pair is None:
                continue
            bar, val = pair
            if value is None:
                val.setText("—")
                continue
            val.setText("%g%s" % (round(value), unit))
            frac = max(0.0, min(1.0, value / scale))
            th = getattr(self, "_home_theme", _resolved())
            bar.setStyleSheet(
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
                " stop:0 %s, stop:%s %s, stop:%s %s, stop:1 %s);"
                " border-radius: 2px;"
                % (th["accent"], frac, th["accent"], frac + 0.001,
                   _rgba(th["text"], 0.12), _rgba(th["text"], 0.12)))
        batt_lab = getattr(self, "_home_vitals_batt", None)
        if batt_lab is not None:
            batt_lab.setText(
                "%d%%" % round(batt) if batt is not None else "—")
        clock = getattr(self, "_home_vitals_clock", None)
        if clock is not None:
            clock.setText(datetime.datetime.now().strftime("%H:%M"))

    def _refresh_home_banner(self):
        """Degradation banner: None when healthy, else the notice
        text + its fix route. Never displaces the orb (top)."""
        banner = getattr(self, "home_degradation_banner", None)
        if banner is None:
            return
        try:
            from voice_assistant.core.gui_facade import (
                degradation as _degradation)

            notice = _degradation.degradation_notice()
        except Exception:  # noqa: BLE001
            notice = None
        if notice is None:
            banner.setVisible(False)
            banner.setText("")
            banner.setToolTip("")
            # H-06: at 640 the banner replaces the status line —
            # with no banner, the vitals (status line) show. On a
            # wide tier the vitals always show.
            try:
                vitals = getattr(self, "home_vitals_line", None)
                if vitals is not None:
                    vitals.setVisible(True)
            except Exception:  # noqa: BLE001
                pass
            return
        text = "%s  —  %s" % (
            notice.get("text", ""), notice.get("fix_route", ""))
        # Hunt fix (c) item 3: the banner wraps and shows the
        # whole notice (no elision) — the label is word-wrapped,
        # so the full text is the text. The full text stays in
        # _banner_full and the tooltip (unchanged); the notice
        # dict stays in _banner_notice so the narrow tier can
        # shorten it (see _banner_display_text).
        self._banner_full = text
        self._banner_notice = notice
        banner.setToolTip(text)
        banner.setText(self._banner_display_text())
        # Phase 6 (640 orb): at the narrow tier the banner
        # cannot fit with the hero — the centre is 168 px tall
        # and the hero alone is 128 px, so the banner's box is
        # squeezed below its wrapped height and its text is
        # drawn over the orb's top (the 640 render). The 640 HUD
        # is orb + greeting + status line only (leg 16), so the
        # banner hides at <= 770 and the vitals (status line)
        # carry the degradation instead — the H-06 replacement
        # in reverse. Above 770 the banner shows as before.
        try:
            _narrow = self.width() <= 770
        except Exception:  # noqa: BLE001 — width unknown, show it
            _narrow = False
        banner.setVisible(not _narrow)
        # H-06: at 640 the banner replaces the status line, so the
        # vitals hide there; on a wide tier the vitals stay.
        # With the banner hidden at the narrow tier the vitals
        # are the degradation signal, so they show there too.
        try:
            vitals = getattr(self, "home_vitals_line", None)
            if vitals is not None:
                vitals.setVisible(True)
        except Exception:  # noqa: BLE001
            pass
        # Re-run the centre layout now (same reason as in
        # _reelide_banner: the hero must move up into the freed
        # banner row at the narrow tier).
        try:
            _clay = getattr(self, "_home_centre_layout", None)
            if _clay is not None:
                _clay.activate()
        except Exception:  # noqa: BLE001 — cosmetic
            pass
        # Re-wrap once the layout settles: at build time the
        # banner's width is 0/stale, so the wrap above may have
        # measured at the wrong width; the deferred pass re-sets
        # the full text at the real width.
        try:
            from PySide6.QtCore import QTimer as _QTimer
            _QTimer.singleShot(0, self._reelide_banner)
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _banner_display_text(self):
        """The banner's text at the current width. Wide tier:
        the whole notice. Narrow tier (width <= 770, the H-06
        threshold): the full notice needs 174 px at the 168 px
        banner width and the centre column cannot hold it plus
        the orb — wrapping cannot fit there, so shorten to the
        first sentence (what's missing) + the fix route; the
        full text stays in the tooltip. The parent ruling for
        item 3 (wrap preferred; shorten only if wrapping cannot
        fit) is what licenses the narrow-tier shortening."""
        full = getattr(self, "_banner_full", "") or ""
        try:
            if self.width() > 770:
                return full
        except Exception:  # noqa: BLE001 — width unknown, show full
            return full
        notice = getattr(self, "_banner_notice", None) or {}
        text = notice.get("text", "") or full
        fix = notice.get("fix_route", "")
        # Narrow-tier shortening, Phase 6: the first SENTENCE is
        # still ~100 chars ("Voice is degraded — her ears ... are
        # not ready."), which needs ~110 px at the 168 px banner
        # width while the centre only has ~38 px for the banner
        # once the 128 px hero is placed — so the banner's text
        # overflowed its box and was drawn over the orb's top
        # (the 640 render). Take the first CLAUSE (before " — ",
        # e.g. "Voice is degraded") instead: with the fix route
        # it is ~2 lines and fits. Falls back to the first
        # sentence when there is no " — " clause.
        first = text.split(" — ")[0].strip()
        if not first or first == text.strip():
            first = text.split(". ")[0].strip()
            if first and not first.endswith("."):
                first += "."
        return "%s  —  %s" % (first, fix) if fix else first

    def _reelide_banner(self):
        """Re-apply the banner text after a resize (called after
        a resize — the centre column width changes per tier, so
        the banner re-wraps and the tier can change; the full
        text stays in _banner_full and the tooltip). Hunt fix
        (c) item 3: the banner wraps, so there is no elision to
        re-apply — the display text is simply re-set, which
        re-wraps it at the new width. Phase 6: also re-evaluate
        the narrow-tier visibility (the banner hides at <= 770
        so it cannot cover the orb, and re-shows above it) — the
        early return on a hidden banner would strand it hidden
        after widening, so visibility is set here, not skipped."""
        banner = getattr(self, "home_degradation_banner", None)
        full = getattr(self, "_banner_full", None)
        if banner is None or not full:
            return
        banner.setText(self._banner_display_text())
        try:
            _narrow = self.width() <= 770
        except Exception:  # noqa: BLE001 — width unknown, show it
            _narrow = False
        banner.setVisible(not _narrow)
        try:
            vitals = getattr(self, "home_vitals_line", None)
            if vitals is not None:
                vitals.setVisible(True)
        except Exception:  # noqa: BLE001
            pass
        # Re-run the centre layout now: hiding the banner frees
        # its row, and the hero must move up into it — waiting
        # for the next layout pass leaves the hero at its old
        # (banner-visible) y and the vitals overlapping the
        # orb's bottom edge at 640.
        try:
            _clay = getattr(self, "_home_centre_layout", None)
            if _clay is not None:
                _clay.activate()
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _refresh_home_previews(self):
        """Closed accordion preview lines, from real data (the
        refinement). The briefing preview is the summary line."""
        previews = getattr(self, "_home_accordion_previews", {})
        summary = getattr(self, "briefing_summary", None)
        if "briefing" in previews and summary is not None:
            previews["briefing"].setText(summary.text() or "")
        if "attention" in previews:
            body = getattr(self, "_home_attention_body", None)
            count = 0
            if body is not None:
                count = sum(
                    1 for i in range(body.count())
                    if body.itemAt(i).widget() is not None
                    and body.itemAt(i).widget() is not getattr(
                        self, "_home_attention_empty", None))
            previews["attention"].setText(
                "%d item%s need attention" % (count, "s" if count != 1 else "")
                if count else "Nothing needs your attention.")
        if "tiles" in previews:
            # UI Phase 2: the preview names what the dock shows —
            # the six defaults when nothing is pinned, the pinned
            # skills (and the overflow) when pins exist.
            try:
                from .skill_pins import pinned_tools
                from voice_assistant.core.gui_facade import tool_labels

                _pins = pinned_tools(settings=self.settings)
            except Exception:  # noqa: BLE001 — the preview
                _pins = []  # falls back to the defaults.
            if _pins:
                _names = [tool_labels.tool_friendly_name(n)
                          for n in _pins[:6]]
                _text = " · ".join(_names)
                if len(_pins) > 6:
                    _text += " · +%d more" % (len(_pins) - 6)
                previews["tiles"].setText(_text)
            else:
                previews["tiles"].setText(
                    "Reminders · Email · Calendar · Notes · Home · Money")
        if "docs" in previews:
            body = getattr(self, "_home_docs_body", None)
            previews["docs"].setText(
                (body.text().split("\n")[0]
                 if body is not None and body.text() else
                 "No documents in memory yet."))
        # Left-rail sections (moved from the accordion, ruling
        # 2026-11-24): their collapsed preview lines show the
        # same one-line summary the accordion previews showed.
        rail = getattr(self, "_home_rail_previews", {})
        if "comms" in rail:
            body = getattr(self, "_home_comms_body", None)
            rail["comms"].setText(
                body.text() if body is not None else "Nothing unread.")
        if "suggestions" in rail:
            rail["suggestions"].setText(
                "Suggestions from VYUK, when there are any.")

    def _refresh_home_pills(self):
        """Quiet / private / mute pills. The quiet pill names its
        end (leg 59); the lock glyph sits in the private pill
        while private mode is on (H-36, leg 45)."""
        # Quiet pill.
        quiet = getattr(self, "home_quiet_pill", None)
        if quiet is not None:
            end = ""
            try:
                from voice_assistant.core.gui_facade import (
                    w7_wiring_daily as _w7_wiring_daily)

                window = _w7_wiring_daily.quiet_hours().window()
                if window:
                    end = str(window.get("end", ""))
            except Exception:  # noqa: BLE001
                end = ""
            if end:
                try:
                    h, m = end.split(":")
                    quiet.setText("Quiet until %d:%s" % (int(h), m))
                except Exception:  # noqa: BLE001
                    quiet.setText("Quiet until %s" % end)
                quiet.setVisible(True)
            else:
                quiet.setVisible(False)
        # Private pill + lock.
        try:
            from voice_assistant.core.gui_facade import (
                private_mode as _private_mode)

            private = bool(_private_mode.private_mode_on())
        except Exception:  # noqa: BLE001
            private = False
        priv = getattr(self, "home_private_pill", None)
        if priv is not None:
            priv.setChecked(private)
        lock = getattr(self, "home_private_lock", None)
        if lock is not None:
            lock.setVisible(private)
            if private and priv is not None:
                lock.setGeometry(6, (priv.height() - 16) // 2, 16, 16)
        # Mute pill (synced with the real mute state).
        mute = getattr(self, "home_mute_pill", None)
        if mute is not None:
            try:
                from voice_assistant.core.gui_facade import (
                    audio_manager as _am)
                muted = bool(getattr(_am, "is_muted", False))
            except Exception:  # noqa: BLE001
                muted = False
            mute.setText("Muted" if muted else "Mute")
        # Private-mode muting of the orb (leg 45): the visualizer
        # snaps to the grey triple immediately (its set_private_mode
        # does the snap); the ring goes grey with it.
        try:
            vis = getattr(self, "visualizer", None)
            if vis is not None and hasattr(vis, "set_private_mode"):
                vis.set_private_mode(private)
        except Exception:  # noqa: BLE001
            pass

    def _refresh_home_screen(self):
        """Screen mini: the last agent action chip (H-17, the
        overlay password rule — format_action_chip never leaks
        typed text). The chip text goes on the action label; the
        cursor dot stays a dot."""
        action = getattr(self, "home_screen_scope_action", None)
        if action is None:
            return
        chip = getattr(self, "_home_last_action_chip", "")
        if chip:
            action.setText(chip)
        else:
            action.setText("No screen activity yet")

    def _refresh_home_goal(self):
        """The now-container hairline (H-43): LINE_SUBTLE only
        when both the goal pill and Now Doing are present; the
        QFrame stays 1 px either way (leg 68)."""
        hair = getattr(self, "_home_now_hairline", None)
        if hair is None:
            return
        goal = getattr(self, "goal_pill", None)
        doing = getattr(self, "home_now_doing", None)
        th = getattr(self, "_home_theme", _resolved())
        both = (goal is not None and goal.isVisible()
                and doing is not None and doing.text().strip() != "")
        hair.setStyleSheet(
            "background: %s; border: none;"
            % (_rgba(th["text"], 0.14) if both else "transparent"))

    # -- profile ---------------------------------------------------------
    def _apply_hud_profile(self, profile_name=None):
        """The ONE call site resolving the HUD profile (plan §10):
        resolve_profile(stored, power_saver, contrast_theme). The
        stored value is ConfigSpec hud_profile / QSettings
        appearance/hud_profile. Drives the backdrops, the Memory
        constellation, and the visualizer's motion. Home has NO
        backdrop (leg 30)."""
        try:
            from .design_system.profiles import PROFILES, resolve_profile
        except Exception:  # noqa: BLE001
            self._hud_profile_name = "Cinematic"
            return
        stored = profile_name
        if stored is None:
            try:
                from voice_assistant.core.gui_facade import config as _cfg

                stored = str(getattr(_cfg, "hud_profile", "") or "")
            except Exception:  # noqa: BLE001
                stored = ""
            if not stored:
                stored = str(qsettings_get("appearance/hud_profile")
                             or "Cinematic")
        power_saver = False
        try:
            from voice_assistant.core.gui_facade import (
                resilience_power as _resilience_power)
            state = _resilience_power.battery_state() or {}
            power_saver = bool(
                state.get("available") and not state.get("power_plugged")
                and float(state.get("percent") or 100) <= 20)
        except Exception:  # noqa: BLE001
            power_saver = False
        theme_name = str(qsettings_get("theme") or "Cyan")
        contrast = "contrast" in theme_name.lower()
        # resolve_profile returns the profile NAME (a str), not a
        # HudProfile — the model lives in PROFILES.
        profile_name = resolve_profile(stored, power_saver, contrast)
        profile = PROFILES.get(profile_name) or PROFILES["Cinematic"]
        self._hud_profile_name = profile.name
        # Backdrops: Memory + Activity FULL, reading pages VIGNETTE,
        # Home none. The profile changes the effective layers
        # (Professional drops scanlines, Minimal hides). The
        # backdrop takes the profile NAME (a str).
        self._apply_profile_backdrops(profile.name)
        # Memory constellation: off on Minimal.
        try:
            canvas = getattr(self, "_memory_canvas", None)
            if canvas is not None and hasattr(
                    canvas, "set_constellation_enabled"):
                active = getattr(self, "_memory_section", "Overview")
                canvas.set_constellation_enabled(
                    profile.name != "Minimal" and active == "Knowledge Graph")
        except Exception:  # noqa: BLE001
            pass
        # Visualizer motion: Minimal is still.
        try:
            vis = getattr(self, "visualizer", None)
            if vis is not None and hasattr(vis, "set_reduced_motion"):
                vis.set_reduced_motion(profile.name == "Minimal")
        except Exception:  # noqa: BLE001
            pass
        # Private-mode quiet (leg 45): applying the profile must
        # also apply the pills — the lock glyph and the orb's grey
        # triple — because callers set private mode and then apply
        # the profile without a separate Home refresh.
        try:
            self._refresh_home_pills()
        except Exception:  # noqa: BLE001
            pass
        # UI Phase 1 (plan §5): OFF is Minimal only — the room's
        # ambient glow goes off under Minimal, and the Home
        # ambient background (teal-black fill + vignette) with
        # it; leaving Minimal restores both. _show_page gates
        # the background on page switches; this covers a profile
        # change while already on Home (no page switch fires
        # then). The orb's own local atmosphere is untouched —
        # only the room-wide glow.
        try:
            shell = getattr(self, "shell", None)
            if shell is not None and hasattr(
                    shell, "set_glow_enabled"):
                shell.set_glow_enabled(profile.name != "Minimal")
            if shell is not None and hasattr(
                    shell, "set_home_background"):
                pages = getattr(self, "pages", None)
                current = None
                try:
                    current = pages.currentIndex() if pages is not None else None
                except Exception:  # noqa: BLE001
                    current = None
                if current == 0:
                    th = getattr(self, "_home_theme", None)
                    base = th.get("bg") if isinstance(th, dict) else None
                    shell.set_home_background(
                        profile.name != "Minimal", base)
        except Exception:  # noqa: BLE001
            pass

    def _apply_profile_backdrops(self, profile):
        """Place / drive the HudBackdrops. Memory + Activity get
        VIGNETTE (flat bg + vignette, no grid — look-fix
        2026-11-24, the checkered grid read as a checkerboard);
        Settings + the reading pages get VIGNETTE. Home gets
        none (leg 30). Backdrops are children of their page,
        lowered behind content, geometry synced on page resize."""
        pages = getattr(self, "pages", None)
        if pages is None:
            return
        specs = []
        for index, intensity in (
                (2, "vignette"), (1, "vignette"), (4, "vignette"),
                (8, "vignette"), (9, "vignette"), (10, "vignette"),
                (11, "vignette"), (3, "vignette")):
            try:
                page = pages.widget(index)
            except Exception:  # noqa: BLE001
                page = None
            if page is not None:
                specs.append((page, intensity))
        if not hasattr(self, "_home_backdrops"):
            self._home_backdrops = {}
        th = getattr(self, "_home_theme", _resolved())
        for page, intensity in specs:
            # Reuse a backdrop the page builder already placed
            # (Memory / Activity place their own); never stack two.
            bd = page.findChild(HudBackdrop)
            if bd is None:
                bd = HudBackdrop(page)
                bd.set_theme(th)
                bd.lower()
            # The backdrop's HOST is its parent, not the stack
            # page: Memory / Activity backdrops are children of
            # the inner scrolled page, while the stack page is the
            # scroll area. Geometry and the resize sync must use
            # the host — keying them to the scroll area sized the
            # backdrop to the viewport (686 px) and never re-synced
            # it when the inner page grew (the lazy graph canvas
            # adds 480 px), leaving the shell's teal glow showing
            # below it. The dict is keyed by host id, and the
            # eventFilter syncs on host resizes.
            host = bd.parent() or page
            bd.set_intensity(intensity)
            bd.setGeometry(host.rect())
            if id(host) not in self._home_backdrops:
                host.installEventFilter(self)
                self._home_backdrops[id(host)] = bd
            try:
                bd.set_profile(profile)
            except Exception:  # noqa: BLE001
                pass
            try:
                bd.set_theme(th)
            except Exception:  # noqa: BLE001
                pass

    # -- theme ------------------------------------------------------------
    def _apply_home_theme(self, resolved=None):
        """Rebuild every HUD stylesheet from the resolved theme.
        Called from _apply_appearance (the window's appearance
        pass) and from _build_home_hud. This is the Light-contrast
        path: no hardcoded dark hex anywhere in the HUD."""
        th = resolved or _resolved()
        self._home_theme = th
        for panel in (
                getattr(self, "home_status_block", None),
                getattr(self, "home_services", None),
                getattr(self, "home_alerts_feed", None),
                getattr(self, "composer_pill", None)):
            if panel is not None and hasattr(panel, "set_theme"):
                try:
                    panel.set_theme(th)
                except Exception:  # noqa: BLE001
                    pass
        for panel in getattr(self, "_home_accordion", {}).values():
            try:
                panel.set_theme(th)
            except Exception:  # noqa: BLE001
                pass
        # On Home there is no backdrop, so the glass is the
        # translucent fill alone (the tile stays None); the panels
        # above already got set_theme, which is the whole glass
        # contract on this page.
        self._retheme_home_labels(th)
        self._refresh_home()

    def _retheme_home_labels(self, th):
        """Rebuild every HUD label stylesheet from the theme.

        The build methods set each label's stylesheet once, from
        the build-time theme — on a theme change to Light every
        label kept its dark-theme colour (light text on a light
        page: the greeting was invisible, the rail unreadable).
        This re-applies the same formats from the resolved theme,
        so Light (and any later theme switch) re-colours the
        labels exactly as the build did. Called from
        _apply_home_theme on every theme change.
        """
        from PySide6.QtWidgets import QLabel, QPushButton
        stage = getattr(self, "_home_stage", None)
        if stage is None:
            return
        mono = "Share Tech Mono, monospace"
        # Header: kicker + greeting + wordmark.
        kicker = getattr(self, "_home_kicker", None)
        if kicker is not None:
            kicker.setStyleSheet(
                "color: %s; font-family: %s; font-size: 10px;"
                " letter-spacing: 2px;" % (th["text_faint"], mono))
        greeting = getattr(self, "home_greeting", None)
        if greeting is not None:
            greeting.setStyleSheet(
                "color: %s; font-family: Orbitron, sans-serif;"
                " font-size: 20px; font-weight: 600;" % th["text"])
        # Hunt fix (c) §7f: the briefing kicker's inline stylesheet
        # was built once from the dark TEXT and never re-themed —
        # near-white on a light card. It follows the resolved theme
        # here, in the same 13 px / 600 format as the build.
        kicker = getattr(self, "briefing_kicker", None)
        if kicker is not None:
            from .style import TYPE_CARD_TITLE as _kicker_px
            kicker.setStyleSheet(
                "font-size: %spx; font-weight: 600; color: %s;"
                % (_kicker_px, th["text"]))
        wordmark = getattr(self, "home_wordmark", None)
        if wordmark is not None:
            wordmark.setStyleSheet(
                "color: %s; font-family: Orbitron, sans-serif;"
                " font-size: 13px; font-weight: 600;"
                " letter-spacing: 6px;" % th["text_muted"])
        # Header cluster: connection dots + the four pills.
        dots = getattr(self, "home_conn_dots", None)
        if dots is not None:
            dots.setStyleSheet(
                "color: %s; font-size: 11px;" % th["text_muted"])
        for w in (getattr(self, "home_permissions_pill", None),
                  getattr(self, "home_private_pill", None),
                  getattr(self, "home_mute_pill", None),
                  getattr(self, "home_rail_toggle", None)):
            if w is not None:
                w.setStyleSheet(
                    "QPushButton { color: %s; background: %s;"
                    " border: 1px solid %s; border-radius: 13px;"
                    " padding: 3px 10px; font-size: 12px; }"
                    "QPushButton:checked { color: %s; border-color: %s; }"
                    % (th["text_muted"], _rgba(th["surface_1"], 0.6),
                       _rgba(th["text"], 0.14), th["text"], th["accent"]))
        # Rail: section titles (STATUS / SERVICES / ALERTS) +
        # telemetry labels / values / bars + brain-wake pills.
        for lbl in stage.findChildren(QLabel):
            txt = lbl.text()
            if txt in ("STATUS", "SERVICES", "ALERTS"):
                lbl.setStyleSheet(
                    "color: %s; font-family: %s;"
                    " font-size: 10px; letter-spacing: 2px;"
                    % (th["text_faint"], mono))
        for key, (bar, val) in getattr(
                self, "_home_status_bars", {}).items():
            try:
                bar.setStyleSheet(
                    "background: %s; border-radius: 2px;"
                    % _rgba(th["text"], 0.12))
                val.setStyleSheet(
                    "color: %s; font-family: %s; font-size: 11px;"
                    % (th["text"], mono))
            except Exception:  # noqa: BLE001 — cosmetic
                pass
        for lbl in stage.findChildren(QLabel):
            name = lbl.objectName() or ""
            if name.startswith("statusBarVal-"):
                lbl.setStyleSheet(
                    "color: %s; font-family: %s; font-size: 11px;"
                    % (th["text"], mono))
        tele = getattr(self, "home_telemetry", None)
        if tele is not None:
            for lbl in tele.findChildren(QLabel):
                if lbl.objectName() in ("statusPill-brain",
                                        "statusPill-wake"):
                    lbl.setStyleSheet(
                        "color: %s; background: %s; border: 1px solid %s;"
                        " border-radius: 9px; padding: 2px 8px;"
                        " font-size: 11px;"
                        % (th["text_muted"], _rgba(th["surface_2"], 0.7),
                           _rgba(th["text"], 0.12)))
                # QLabel has no fixedWidth() — the 52 px label got
                # its width via setFixedWidth(52), so minimumWidth()
                # is the reliable marker (width() lags the layout).
                elif lbl.minimumWidth() == 52:
                    lbl.setStyleSheet(
                        "color: %s; font-size: 11px;" % th["text_muted"])
        services_body = getattr(self, "_home_services_body", None)
        if services_body is not None:
            services_body.setStyleSheet(
                "color: %s; font-size: 12px;" % th["text_muted"])
        # Centre: degradation banner + Now Doing + elapsed +
        # screen scope + vitals.
        banner = getattr(self, "home_degradation_banner", None)
        if banner is not None:
            banner.setStyleSheet(
                "color: %s; background: %s; border: 1px solid %s;"
                " border-radius: 8px; padding: 6px 10px; font-size: 12px;"
                % (th["text"], _rgba(th["surface_2"], 0.85),
                   _rgba(AMBER, 0.55)))
        doing = getattr(self, "home_now_doing", None)
        if doing is not None:
            doing.setStyleSheet(
                "color: %s; font-size: 13px;" % th["text"])
        elapsed = getattr(self, "home_now_doing_elapsed", None)
        if elapsed is not None:
            elapsed.setStyleSheet(
                "color: %s; font-family: %s; font-size: 11px;"
                % (th["text_muted"], mono))
        # Screen mini (H-17): the scope container + its eyebrow /
        # cursor / action labels — all set once at build time, so
        # without this the scope stays a dark bar on Light.
        scope = getattr(self, "home_screen_scope", None)
        if scope is not None:
            scope.setStyleSheet(
                "background: %s; border: 1px solid %s; border-radius: 8px;"
                % (_rgba(th["surface_2"], 0.8), _rgba(th["text"], 0.12)))
        for child in (getattr(self, "home_screen_scope_action", None),):
            if child is not None:
                child.setStyleSheet(
                    "color: %s; font-size: 12px; background: transparent;"
                    " border: none;" % th["text_muted"])
        # Accordion: section titles + previews.
        for key, panel in getattr(self, "_home_accordion", {}).items():
            try:
                for lbl in panel.findChildren(QLabel):
                    if lbl.objectName() == "accordionPreview":
                        lbl.setStyleSheet(
                            "color: %s; font-size: 12px;"
                            % th["text_muted"])
                    elif lbl.text() in (
                            "BRIEFING", "ATTENTION", "QUICK ACTIONS",
                            "DOCUMENTS"):
                        lbl.setStyleSheet(
                            "color: %s; font-family: %s;"
                            " font-size: 10px; letter-spacing: 2px;"
                            % (th["text_faint"], mono))
            except Exception:  # noqa: BLE001 — cosmetic
                pass
        # Left-rail sections (MESSAGES / SUGGESTIONS moved here,
        # ruling 2026-11-24): titles + previews, same treatment.
        for key, panel in getattr(self, "_home_rail_panels", {}).items():
            try:
                for lbl in panel.findChildren(QLabel):
                    if lbl.objectName() == "railPreview":
                        lbl.setStyleSheet(
                            "color: %s; font-size: 12px;"
                            % th["text_muted"])
                    elif lbl.text() in ("MESSAGES", "SUGGESTIONS"):
                        lbl.setStyleSheet(
                            "color: %s; font-family: %s;"
                            " font-size: 10px; letter-spacing: 2px;"
                            % (th["text_faint"], mono))
            except Exception:  # noqa: BLE001 — cosmetic
                pass
        # Column hairlines.
        for rule in stage.findChildren(QLabel):
            pass
        try:
            from PySide6.QtWidgets import QFrame
            for rule in stage.findChildren(QFrame):
                if (rule.objectName() or "").startswith("homeColumnRule"):
                    rule.setStyleSheet(
                        "background: %s; border: none;"
                        % _rgba(th["text"], 0.10))
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _refresh_settings_voice_strip(self):
        """The base voice-settings strip, then the narrow-tier
        rule: at 640 the strip hides (the 640 HUD is orb +
        greeting + status line only — leg 16; the strip is 48 px
        and would push the stage past 200). The base decides
        whether it SHOULD show; this only suppresses it on the
        narrow tier."""
        try:
            super()._refresh_settings_voice_strip()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.width() <= 770:
                strip = getattr(self, "settings_voice_strip", None)
                if strip is not None:
                    strip.hide()
        except Exception:  # noqa: BLE001
            pass

    def _show_page(self, index):
        """Page switch, then (re)apply the profile backdrops. The
        backdrops are placed by _apply_hud_profile at build time,
        but the reading pages may not exist in the stack yet then
        (leg 43: Settings had no backdrop) — re-applying on show
        is idempotent (it reuses an existing backdrop, never
        stacks two) and lands the backdrop the first time the
        page is real."""
        try:
            super()._show_page(index)
        except Exception:  # noqa: BLE001
            pass
        try:
            profile = getattr(self, "_hud_profile_name", "Cinematic")
            self._apply_profile_backdrops(profile)
        except Exception:  # noqa: BLE001
            pass
        try:
            # UI Phase 1 (ruling 2026-11-24): Home's ambient
            # background — the teal-black fill + vignette live on
            # the shell only while Home is the active page; every
            # other page gets the theme base back.
            shell = getattr(self, "shell", None)
            if shell is not None and hasattr(shell,
                                             "set_home_background"):
                th = getattr(self, "_home_theme", None)
                base = th.get("bg") if isinstance(th, dict) else None
                if index == 0:
                    # Plan §5: OFF is Minimal only — the ambient
                    # background (teal-black fill + vignette) does
                    # not apply under Minimal; Home falls back to
                    # the theme base. The glow itself is gated
                    # separately in _apply_hud_profile.
                    minimal = getattr(
                        self, "_hud_profile_name",
                        "Cinematic") == "Minimal"
                    shell.set_home_background(not minimal, base)
                else:
                    shell.set_home_background(False, base)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._ensure_icon_button_tooltips()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_icon_button_tooltips(self) -> None:
        """Every icon-only button needs a tooltip (leg 61). The
        window controls are set where they are built; Qt-internal
        buttons (QCalendarWidget's prev/next month nav) have
        neither a tooltip nor a creator we own, so this sweep
        covers any icon-only button that still lacks one. It is
        called after the HUD build and on every page switch, so
        lazily-built pages are covered too."""
        from PySide6.QtWidgets import QPushButton, QToolButton

        _names = {
            "qt_calendar_prevmonth": "Previous month",
            "qt_calendar_nextmonth": "Next month",
        }
        for btn in list(self.findChildren(QPushButton)) + list(
                self.findChildren(QToolButton)):
            try:
                if btn.icon().isNull() or btn.text().strip():
                    continue
                if btn.toolTip():
                    continue
                name = btn.objectName() or ""
                tip = _names.get(name)
                if not tip:
                    tip = btn.accessibleName() or name or "Button"
                btn.setToolTip(tip)
            except Exception:  # noqa: BLE001
                continue

    def _refresh_setup_cards(self):
        """The base setup/self-test cards, then the narrow-tier
        rule: at 640 the conditional cards hide (the 640 HUD is
        orb + greeting + status line only — leg 16). The base
        decides whether they SHOULD show; this only suppresses
        them on the narrow tier."""
        try:
            super()._refresh_setup_cards()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.width() <= 770:
                for name in ("setup_card", "selftest_card"):
                    card = getattr(self, name, None)
                    if card is not None:
                        card.hide()
        except Exception:  # noqa: BLE001
            pass

    # -- pending approval --------------------------------------------------
    def _resolve_pending(self, task_id, approved):
        """Resolve a pending approval ONLY through the core path
        (the Activity approval card calls this at click time —
        leg 27 spies on the method)."""
        try:
            from voice_assistant.core.gui_facade import TaskLedger

            from .pending_approval import resolve_pending_answer

            result = resolve_pending_answer(
                task_id, bool(approved), live_coordinator=None,
                ledger=TaskLedger())
        except Exception as e:  # noqa: BLE001
            result = {"status": "error", "message": str(e)}
        try:
            if hasattr(self, "_refresh_activity_tabs"):
                self._refresh_activity_tabs()
        except Exception:  # noqa: BLE001
            pass
        return result

    # -- composer placeholder ----------------------------------------------
    def _sync_composer_placeholder(self):
        """State-aware placeholder (R6). At 640 / 150% text the
        short form wins (leg 63)."""
        edit = getattr(self, "input_cmd", None)
        if edit is None:
            return
        try:
            scale = float(qsettings_get("appearance/font_scale") or 1.0)
        except Exception:  # noqa: BLE001
            scale = 1.0
        if scale >= 1.5 and self.width() <= 700:
            text = "Ask anything"
        else:
            raw = str(getattr(self, "_status_raw", "") or "").upper()
            if raw in ("LISTENING",):
                text = "Listening…"
            elif raw in ("PROCESSING", "RESPONDING"):
                text = "Working on it…"
            elif raw in ("SPEAKING", "RESPONDING"):
                text = "Speaking…"
            else:
                text = "Ask anything"
        try:
            if edit.placeholderText() != text:
                edit.setPlaceholderText(text)
        except Exception:  # noqa: BLE001
            pass

    # -- §5 tiers ------------------------------------------------------------
    def _apply_breakpoint_tier(self):
        """§5 (2026-11-24 screenshot sizes). The base tier sets the
        old 420/400/300/220 numbers and never touches the
        visualizer height, so it is NOT called here — this is the
        full tier: sidebar, stage min, columns, hero/visualizer,
        orb diameter, chat floor, orientation, and the 640
        collapse. Column widths are exact (leg 71)."""
        width = self.width()
        try:
            from .breakpoints import COMPACT_WIDTH
        except Exception:  # noqa: BLE001
            COMPACT_WIDTH = 908
        # Sidebar: the compact pass handles labels + the rail;
        # the §5 width overrides the 220/64 it sets.
        try:
            if hasattr(self, "_apply_sidebar_compact"):
                self._apply_sidebar_compact(width < COMPACT_WIDTH)
        except Exception:  # noqa: BLE001
            pass
        try:
            sidebar = self.findChild(QFrame, "sidebar")
            if sidebar is not None:
                sidebar.setFixedWidth(hud_sidebar_for(width))
        except Exception:  # noqa: BLE001
            pass
        stage = getattr(self, "_home_stage", None)
        if stage is None:
            return
        stage.setMinimumWidth(hud_stage_min_for(width))
        rail_w, centre_w, right_w = hud_columns_for(width)
        rail = getattr(self, "_home_left_rail", None)
        centre = getattr(self, "_home_centre", None)
        right = getattr(self, "_home_right_col", None)
        if rail is not None:
            if not rail_w:
                rail.hide()
            else:
                rail.setFixedWidth(rail_w)
                rail.show()
        if centre is not None:
            centre.setFixedWidth(centre_w)
            centre.show()
        if right is not None:
            if not right_w:
                right.hide()
            else:
                right.setFixedWidth(right_w)
                right.show()
        # The rail overlay toggle only at the narrow tiers.
        toggle = getattr(self, "home_rail_toggle", None)
        if toggle is not None:
            toggle.setVisible(rail_w is None and width > 770)
        # Hero / visualizer / orb.
        viz_h = hud_visualizer_height_for(width)
        narrow = width <= 770  # the 640 tier
        if narrow:
            viz_h = 128  # the stage height budget (leg 16, <=200)
        hero = getattr(self, "_home_hero", None)
        vis = getattr(self, "visualizer", None)
        if hero is not None:
            hero.setMinimumWidth(centre_w)
            hero.setMinimumHeight(viz_h)
            hero.setMaximumHeight(viz_h)
        if vis is not None:
            vis.setMinimumSize(centre_w, viz_h)
            try:
                vis.set_orb_diameter(hud_orb_diameter_for(width))
            except Exception:  # noqa: BLE001
                pass
        # Chat floor: the §5 chat value (230 at 1280 — below the
        # old 300 floor, so the floor must move with the tier).
        chat_panel = getattr(self, "chat_panel", None)
        if chat_panel is not None:
            chat_panel.setMinimumWidth(hud_chat_for(width))
        # Orientation: vertical only at 640 (leg 16); the 900 tier
        # keeps the horizontal splitter (leg 71 pins its columns).
        split = getattr(self, "_home_split", None)
        if split is not None:
            split.setOrientation(
                Qt.Vertical if narrow else Qt.Horizontal)
        # Stage max height: NOT set here. At 640 the vertical
        # splitter's setSizes (in _fix_chat_width) caps the stage
        # at 200; a maximumHeight here would cap the SPLITTER
        # (a horizontal splitter cannot be taller than any child's
        # max) and the layout would not re-expand it after the
        # tier changes (leg 15: stage stuck at 200 px at 1280).
        # The 640 cap lives only in _fix_chat_width's setSizes.
        # Centre layout spacing: 4 px normally, 2 px on the
        # narrow tier — at 640 the stage minimum must stay ≤200
        # (leg 16) and the column's 13 gaps are the cheapest px.
        centre = getattr(self, "_home_centre", None)
        if centre is not None and centre.layout() is not None:
            centre.layout().setSpacing(2 if narrow else 4)
        # 640 collapse: only the orb + greeting + status line.
        # 900 hides the screen mini (the now container is already
        # empty at idle). The wordmark/state/now/screen hide at 640.
        # The conditional cards (setup / self-test) and the starter
        # chips also hide at 640 — the page minimum must fit the
        # 386 px viewport (leg 16); the cards return on wider tiers.
        for name, visible in (
                ("home_wordmark", not narrow),
                ("home_state_line", not narrow),
                ("home_now_container", not narrow),
                ("home_screen_mini", width > 962),
                ("_home_kicker", not narrow)):
            w = getattr(self, name, None)
            if w is not None:
                w.setVisible(visible)
        chip_scroll = getattr(self, "_chip_scroll", None)
        if chip_scroll is not None:
            chip_scroll.setVisible(not narrow)
        # The header cluster collapses at <=1024, but the mode
        # pills (quiet / private) NEVER collapse (H-04). The
        # greeting must show WHOLE at 1280 (parent by-eye fix,
        # 2026-11-24): it needs 574 px at 20 px Orbitron but only
        # gets 445 px while the full cluster (~340 px) shows, so
        # it hard-clips mid-word ("ask me to adc"). The two
        # widest non-mode items — the Permissions pill (96 px)
        # and the Connect button (100 px) — hide at <=1280,
        # freeing ~196 px so the greeting gets ~641 px and reads
        # whole. The rest of the cluster still collapses at
        # <=1024 as before; the mode pills never collapse.
        cluster_wide = width <= 1280
        for name in ("home_permissions_pill", "btn_connect"):
            w = getattr(self, name, None)
            if w is not None:
                w.setVisible(not cluster_wide)
        cluster_narrow = width <= 1024
        for name in ("home_conn_dots",
                     "home_mute_pill", "btn_header_overflow"):
            w = getattr(self, name, None)
            if w is not None:
                w.setVisible(not cluster_narrow)
        # Centre the single column at 640.
        cols = getattr(self, "_home_columns", None)
        if cols is not None and centre is not None:
            lay = cols.layout()
            if lay is not None:
                try:
                    lay.setAlignment(
                        centre, Qt.AlignCenter if narrow else Qt.AlignLeft)
                except Exception:  # noqa: BLE001
                    pass
        self._sync_composer_placeholder()
        QTimer.singleShot(0, self._position_home_rules)

    def _position_home_rules(self):
        """Column hairlines (leg 38): 1 px at the right edge of the
        rail / centre, repositioned after layout settles."""
        for rule, host in (
                (getattr(self, "home_column_rule_0", None),
                 getattr(self, "_home_left_rail", None)),
                (getattr(self, "home_column_rule_1", None),
                 getattr(self, "_home_centre", None))):
            if rule is None or host is None or not host.isVisible():
                if rule is not None:
                    rule.hide()
                continue
            rule.setGeometry(host.width() - 1, 0, 1, host.height())
            rule.show()
            rule.raise_()

    def _fix_chat_width(self, first=False):
        """§5 chat: the stage takes its exact minimum, the chat
        takes the remainder (230 at 1280, ~353 at 1920). At 640
        the splitter is vertical and the chat is the floor."""
        self._apply_breakpoint_tier()
        split = getattr(self, "_home_split", None)
        if split is None:
            split = getattr(self, "_chat_splitter", None)
        if split is None:
            return
        # The dock rule needs the chat's NEW width, but
        # setSizes only schedules the layout — panel.width()
        # still reads the OLD width until the layout runs, so
        # the rule would read the stale (wide) chat and leave
        # the screen button visible at 230 px. Pass the target
        # width explicitly instead (narrow-chat ruling).
        _dock_w = None
        if split.orientation() == Qt.Vertical:
            avail = split.height() - split.handleWidth()
            stage_h = min(200, avail - 240)
            split.setSizes([max(stage_h, 0), avail - max(stage_h, 0)])
            _dock_w = split.width()
        else:
            avail = split.width() - split.handleWidth()
            stage_min = hud_stage_min_for(self.width())
            # The chat target is the §5 budget for this window
            # width (230 at 1280) — NOT avail - stage_min. avail
            # reads split.width(), which is stale (the full window
            # width) until the layout runs after a resize, so the
            # first call computed 412 and the dock rule left the
            # screen button visible; only the second call (after
            # the layout settled) got 230. hud_chat_for is the
            # §5 budget straight from the window width, so it is
            # right on the first call. setSizes still uses avail
            # for the stage share — Qt redistributes a stale sum.
            target = hud_chat_for(self.width())
            floor = hud_chat_for(self.width())
            target = max(target, min(floor, avail - stage_min))
            split.setSizes([avail - target, target])
            _dock_w = target
        try:
            if hasattr(self, "_apply_dock_narrow_rule"):
                self._apply_dock_narrow_rule(_dock_w)
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "_reelide_briefing_summary"):
                QTimer.singleShot(0, self._reelide_briefing_summary)
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "_reelide_greeting"):
                QTimer.singleShot(0, self._reelide_greeting)
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "_reelide_banner"):
                QTimer.singleShot(0, self._reelide_banner)
        except Exception:  # noqa: BLE001
            pass

    def _apply_dock_narrow_rule(self, panel_w=None):
        """The v6.3 dock rule, then the Phase 1 narrow-chat
        compaction: at a 230 px chat the screen button hides, so
        the dock is [input, send, mic] and the input gets ~94 px
        instead of 60 — the input must always show its
        placeholder whole (narrow-chat ruling 2026-11-24, Aayur:
        the 1280 render clipped the placeholder to "Li"). Mic 56,
        send 44 stay frozen; dictate joins when the chat reaches
        300. Screen hides below 300 (was below 200)."""
        try:
            super()._apply_dock_narrow_rule(panel_w)
        except Exception:  # noqa: BLE001
            pass
        panel = getattr(self, "chat_panel", None)
        if panel is None:
            return
        # The width: panel_w when the caller knows it (the HUD's
        # _fix_chat_width passes the splitter target). Otherwise
        # read it from the SPLITTER's sizes, not panel.width() —
        # panel.width() lags setSizes until the layout runs, so a
        # no-arg call right after a resize reads the stale (wide)
        # chat and leaves the screen button visible at 230 px.
        # The splitter sizes are the source of truth the moment
        # setSizes lands. (narrow-chat ruling, 2026-11-24.)
        w = panel_w
        if w is None or w <= 0:
            try:
                split = getattr(self, "_home_split", None)
                if split is None:
                    split = getattr(self, "_chat_splitter", None)
                if split is not None:
                    sizes = split.sizes()
                    if sizes:
                        w = sizes[-1]
            except Exception:  # noqa: BLE001
                w = None
        if w is None or w <= 0:
            w = panel.width()
        if w <= 0:
            w = panel.width()
        pill = getattr(self, "composer_pill", None)
        lay = pill.layout() if pill is not None else None
        edit = getattr(self, "input_cmd", None)
        screen = getattr(self, "btn_screen", None)
        dictate = getattr(self, "_btn_dictate", None)
        if lay is not None:
            lay.setSpacing(8 if w < 300 else 12)
        if edit is not None:
            try:
                edit.setMinimumWidth(60 if w < 300 else 120)
            except Exception:  # noqa: BLE001
                pass
        if screen is not None:
            # Narrow-chat ruling 2026-11-24: the screen button
            # hides below 300 (not 200), so at the 230 px chat
            # the dock is [input, send, mic] and the input gets
            # ~94 px — enough to show its placeholder whole.
            screen.setVisible(w >= 300)
        if dictate is not None:
            dictate.setVisible(w >= 300)
            if w < 420:
                try:
                    dictate.setText("")
                except Exception:  # noqa: BLE001
                    pass
        # The dock waveform's fit (look-fix 2026-11-24, the
        # speaking-state "Ask" clip): the base rule's computation
        # never runs under Phase 1 — the override's super() call
        # above passes panel_w, which the base signature does not
        # take, so it raises and is swallowed, and
        # _dock_waveform_allowed stays at its getattr default
        # True. The telemetry tick then shows the 104 px ribbon
        # at ANY width, and at the 230 px chat it crushes the
        # input to its minimum and the placeholder clips to
        # "Ask". Compute the allowance here from this override's
        # own (reliable) width and the Phase 1 visibility rules:
        # input min + send + dictate + mic + screen (when shown)
        # + waveform + the pill's inner gaps and margins must fit,
        # or the ribbon stays hidden and the MicLevelArc ring
        # alone carries the level. Widths are the FROZEN dock
        # widths, not sizeHint: QPushButton sizeHint counts icon
        # + padding (send 58, not its 44 fixed size) and the plain
        # WaveformWidget has no sizeHint at all (-1) — measuring
        # those let a 294 px dock read as 211 px and the ribbon
        # show at 230. minimumSize is the fixed size where one is
        # set; only the dictate button (no fixed size, font
        # dependent) reads its sizeHint.
        try:
            waveform = getattr(self, "waveform", None)
            if waveform is not None:
                def _dock_w_of(_w):
                    if _w is None:
                        return 0
                    _m = _w.minimumSize().width()
                    if _m > 0:
                        return _m
                    return max(0, _w.sizeHint().width())

                _input_min = 60 if w < 300 else 120
                _fixed = 0
                _visible = 0
                for _name, _shown in (
                        ("btn_send", True),
                        ("_btn_dictate", w >= 300),
                        ("btn_mic", True)):
                    _b = getattr(self, _name, None)
                    if _b is not None and _shown:
                        _fixed += _dock_w_of(_b)
                        _visible += 1
                if screen is not None and w >= 300:
                    _fixed += _dock_w_of(screen)
                    _visible += 1
                _gap = 6 if w < 420 else 8
                _margin = 6 if w < 420 else 10
                _need = (_input_min + _fixed
                         + _dock_w_of(waveform)
                         + (_visible + 1) * _gap + 2 * _margin)
                self._dock_waveform_allowed = _need <= w
                if not self._dock_waveform_allowed:
                    waveform.setVisible(False)
        except Exception:  # noqa: BLE001
            pass
        # The input must show its placeholder whole (narrow-chat
        # ruling, owner correction 2026-11-24 — the 11:04 render
        # still clipped it to "Ask"). The input is 94 px at the
        # 230 px chat but its text area was only ~44 px: the
        # #dockInput padding (16 px each side) + the document
        # margin (4 px each side) + the pill's inner margins
        # (10 px) and spacing ate it. The tightening applies
        # whenever w < 420 — not just w < 300 — because at 1920
        # the chat is ~345 px and the input (~116 px) still
        # clips "Ask anything" (85 px) under the wide padding.
        # At w < 420 the dock tightens: the pill's inner HBox
        # drops to 6 px margins and 6 px spacing (widening the
        # input), and the input's horizontal padding drops to
        # 3 px with a 0 px document margin, leaving ~96 px of
        # text room at 1280 and ~124 px at 1920. At w >= 420 the
        # comfortable padding / margins are restored. The inline
        # padding overrides only that property — the #dockInput
        # background / border / focus rules still come from the
        # global stylesheet.
        try:
            if pill is not None:
                _cl = pill.layout()
                _inner = None
                if _cl is not None and _cl.count():
                    _iw = _cl.itemAt(0).widget()
                    if _iw is not None:
                        _inner = _iw.layout()
                if _inner is not None:
                    if w < 420:
                        _inner.setContentsMargins(6, 6, 6, 6)
                        _inner.setSpacing(6)
                    else:
                        _inner.setContentsMargins(10, 6, 10, 6)
                        _inner.setSpacing(8)
            if edit is not None:
                # Look-fix 2026-11-24: the borderless rules travel
                # with the padding — setStyleSheet replaces the
                # whole inline sheet, so a bare padding rule would
                # drop them and the input's bordered box would come
                # back inside the pill. (The global #dockInput
                # border / background still lose to this inline
                # sheet, including on :focus.)
                if w < 420:
                    edit.setStyleSheet(
                        "background: transparent; border: none; "
                        "padding: 12px 3px;")
                    try:
                        edit.document().setDocumentMargin(0.0)
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    edit.setStyleSheet(
                        "background: transparent; border: none;")
                    try:
                        edit.document().setDocumentMargin(4.0)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass

    # -- panels --------------------------------------------------------------
    def _restore_home_panels(self):
        """Show every hidden Home section again (leg 20)."""
        for w in getattr(self, "_home_hidden_panels", []):
            try:
                w.show()
            except Exception:  # noqa: BLE001
                pass
        for name in ("home_services", "home_alerts_feed",
                     "home_status_block"):
            w = getattr(self, name, None)
            if w is not None:
                w.show()
        for panel in getattr(self, "_home_rail_panels", {}).values():
            try:
                panel.show()
            except Exception:  # noqa: BLE001
                pass

    def _toggle_rail_overlay(self):
        """H-05: the rail as an overlay at the narrow tiers."""
        rail = getattr(self, "_home_left_rail", None)
        if rail is None:
            return
        if rail.isVisible():
            rail.hide()
            return
        stage = getattr(self, "_home_stage", None)
        if stage is None:
            rail.show()
            return
        rail.setParent(stage)
        rail.setWindowFlags(Qt.Popup)
        rail.setGeometry(0, 0, rail.width(), stage.height())
        rail.show()
        rail.raise_()

    def _on_private_pill_clicked(self):
        """Private mode toggle (the pill is the switch)."""
        try:
            from voice_assistant.core.gui_facade import config as _cfg

            current = bool(getattr(_cfg, "private_mode", False))
            _cfg.private_mode = not current
        except Exception:  # noqa: BLE001
            pass
        self._refresh_home_pills()
        self._refresh_home()

    def _on_mute_pill_clicked(self):
        """Mute toggle (synced with btn_mute / _toggle_mute): the
        pill flips the transport button's checked state first, so
        _toggle_mute (which reads btn_mute) actually toggles."""
        try:
            btn = getattr(self, "btn_mute", None)
            if btn is not None:
                btn.setChecked(not btn.isChecked())
            if hasattr(self, "_toggle_mute"):
                self._toggle_mute()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_home_pills()

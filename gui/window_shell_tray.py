# Refactor Phase 1 step 5: ShellMixin panel mixin, moved verbatim from gui/window_shell.py.
from .style import (
    ACCENT,
    AMBER,
    BG,
    DANGER,
    LINE_SUBTLE,
    PREMIUM_STYLE,
    SURFACE_2,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
    TYPE_CARD_TITLE,
    TYPE_TITLE,
    RADIUS_BTN,
    load_bundled_fonts,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import (
    QEvent,
    QRectF,
    QSettings,
    QSize,
    QThread,
    Qt,
    QTimer,
    Signal,
)
from .window_widgets import (
    _ASSETS,
    BridgeSignals,
    BackendWorker,
    AppShell,
    TitleBar,
    WaveformWidget,
    MiniEq,
    ThinkingDots,
    _ActivitySparkline,
    _ComposerEdit,
    ChatBubble,
    CommandPalette,
    SplashIgnition,
)
from voice_assistant.core.gui_facade import config
from .icons import make_icon
from voice_assistant.core.gui_facade import qsettings_get


class ShellTrayMixin:


    def _setup_tray(self):
        icon_path = _ASSETS / "vyuk_icon.png"
        if icon_path.exists():
            self._app_icon = QIcon(str(icon_path))
        else:
            self._app_icon = make_icon("spark", ACCENT, 22)
        self.setWindowIcon(self._app_icon)
        self.tray = None
        try:
            if QSystemTrayIcon.isSystemTrayAvailable():
                tray = QSystemTrayIcon(self._app_icon, self)
                tray.setToolTip(config.assistant_name)
                menu = QMenu()
                act_open = menu.addAction(f"Open {config.assistant_name}")
                act_open.triggered.connect(self._tray_open)
                # v5.31: quick actions — connect and mute without
                # opening the window. Labels/states sync from
                # _update_tray_state and _toggle_mute.
                self.tray_connect_action = menu.addAction("Connect")
                self.tray_connect_action.triggered.connect(
                    self._toggle_connection)
                self.tray_mute_action = menu.addAction("Mute")
                self.tray_mute_action.setCheckable(True)
                self.tray_mute_action.triggered.connect(
                    self._tray_toggle_mute)
                self.tray_mini_action = menu.addAction("Show mini orb")
                self.tray_mini_action.setCheckable(True)
                self.tray_mini_action.setChecked(
                    bool(qsettings_get("presence/mini_orb", settings=self.settings)))
                self.tray_mini_action.triggered.connect(
                    lambda checked=False: self._set_mini_orb_visible(bool(checked)))
                # R8-6 (yasb port): the status strip toggle, beside the
                # mini orb — same shape, but opt-in and off by default.
                self.tray_strip_action = menu.addAction("Show status strip")
                self.tray_strip_action.setCheckable(True)
                self.tray_strip_action.setChecked(
                    bool(qsettings_get("presence/status_strip", settings=self.settings)))
                self.tray_strip_action.triggered.connect(
                    lambda checked=False: self._set_status_strip_visible(bool(checked)))
                # Hardening (v4.3): the failsafe watchdog's human
                # re-arm. Disabled while her hands are healthy; the
                # trip slot enables it.
                self.tray_rearm_action = menu.addAction("Re-arm VYUK's hands (failsafe)")
                self.tray_rearm_action.setEnabled(False)
                self.tray_rearm_action.triggered.connect(self._rearm_failsafe)
                # v5.57 (redo): a real menu — brain choice, Doctor,
                # and Settings, without opening the window first.
                brain_menu = menu.addMenu("Brain")
                act_brain_cloud = brain_menu.addAction("Gemini Live")
                act_brain_cloud.triggered.connect(
                    lambda: self._on_engine_segment_clicked("gemini"))
                act_brain_pc = brain_menu.addAction("On this PC")
                act_brain_pc.triggered.connect(
                    lambda: self._on_engine_segment_clicked("local"))
                act_doctor = menu.addAction("Doctor")
                act_doctor.triggered.connect(lambda: self._show_page(7))
                act_settings = menu.addAction("Settings")
                act_settings.triggered.connect(lambda: self._show_page(4))
                act_quit = menu.addAction("Quit")
                act_quit.triggered.connect(self.close)
                tray.setContextMenu(menu)
                tray.show()
                self.tray = tray
        except Exception:  # noqa: BLE001
            self.tray = None

    _TRAY_STATE_COLORS = {
        "LISTENING": ACCENT, "SPEAKING": ACCENT,
        "THINKING": TEXT, "CONNECTING": AMBER,
        "ERROR": DANGER, "DISCONNECTED": TEXT_MUTED,
    }


    def _tray_state_icon(self, color_hex: str):
        cache = getattr(self, "_tray_icon_cache", None)
        if cache is None:
            cache = self._tray_icon_cache = {}
        if color_hex in cache:
            return cache[color_hex]
        base = self._app_icon.pixmap(32, 32)
        if base.isNull():
            base = QPixmap(32, 32)
            base.fill(QColor(SURFACE_2))
        pm = QPixmap(base)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(BG), 2))
        painter.setBrush(QColor(color_hex))
        painter.drawEllipse(QRectF(19, 19, 11, 11))
        painter.end()
        icon = QIcon(pm)
        cache[color_hex] = icon
        return icon


    def _update_tray_state(self, upper: str):
        tray = getattr(self, "tray", None)
        if tray is None:
            return
        color = ACCENT
        label = "Ready"
        for key, hexv in self._TRAY_STATE_COLORS.items():
            if key in upper:
                color = hexv
                label = key.capitalize()
                break
        tray.setIcon(self._tray_state_icon(color))
        tray.setToolTip(f"{config.assistant_name} — {label}")
        connect_action = getattr(self, "tray_connect_action", None)
        if connect_action is not None:
            live = any(k in upper for k in
                       ("LISTENING", "SPEAKING", "THINKING", "CONNECTING"))
            connect_action.setText("Disconnect" if live else "Connect")


    def _tray_open(self):
        self.show()
        self.raise_()
        self.activateWindow()

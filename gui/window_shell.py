"""MainWindow shell mixin: construction, titlebar/tray,
onboarding/greeting, the UI build (banner, sidebar, assistant
page) and the page-shell helper's owner slice.

v5.50 (programme item 43): split out of gui/main_window.py,
which remains as the re-export shim. Bodies moved verbatim;
behaviour is unchanged.
"""
import datetime
import threading
from pathlib import Path
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
from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
from voice_assistant.core.gui_facade import scheduler
from voice_assistant.core.gui_facade import VERSION
from .breakpoints import (
    WINDOW_MINIMUM,
    chat_target,
    default_window_size,
    hero_min_width_for,
    stage_floor_for,
)
from .icons import make_icon
from .mini_orb import MiniOrb
from .onboarding import OnboardingDialog
from .sounds import Earcons
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
from .design_system.palette import parse_color, with_alpha
from .visualizer import (
    OrbVisualizer,
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

from .window_shell_assistant_widgets import _ChipRow  # refactor P1 step 5 (moved verbatim)
from .window_shell_assistant_widgets import _HeroWidget  # refactor P1 step 5 (moved verbatim)
from .window_shell_assistant_widgets import _DotsSplitterHandle  # refactor P1 step 5 (moved verbatim)
from .window_shell_assistant_widgets import _DotsSplitter  # refactor P1 step 5 (moved verbatim)
from .window_shell_briefing import _CalendarWorker  # refactor P1 step 5 (moved verbatim)
from .window_shell_assistant_widgets import _CHIP_PAD_X  # refactor P1 step 5 (moved verbatim)
from .window_shell_assistant_widgets import _CHIP_BORDER  # refactor P1 step 5 (moved verbatim)
from .window_shell_frame import _DOCK_NARROW_WIDTH  # refactor P1 step 5 (moved verbatim)

from .window_home_hud import HomeHudMixin
from .window_shell_frame import ShellFrameMixin
from .window_shell_tray import ShellTrayMixin
from .window_shell_settingsio import ShellSettingsIOMixin
from .window_shell_memoryio import ShellMemoryIOMixin
from .window_shell_activity import ShellActivityMixin
from .window_shell_onboarding import ShellOnboardingMixin
from .window_shell_sidebar import ShellSidebarMixin
from .window_shell_assistant import ShellAssistantMixin
from .window_shell_briefing import ShellBriefingMixin


class ShellMixin(HomeHudMixin, ShellFrameMixin, ShellTrayMixin, ShellSettingsIOMixin, ShellMemoryIOMixin, ShellActivityMixin, ShellOnboardingMixin, ShellSidebarMixin, ShellAssistantMixin, ShellBriefingMixin):
    # Refactor Phase 1 step 5: panel methods moved verbatim to window_shell_* mixins.
    # UI Phase 1: HomeHudMixin is FIRST so its _build_assistant_page,
    # _apply_breakpoint_tier, _fix_chat_width and
    # _apply_dock_narrow_rule overrides win. The override is
    # deliberate (no test pins the no-duplicate-method invariant).
    pass

"""
VYUK — Premium Main Window (P0 shell + Phase 2).

Phase 2 adds, on top of the P0 shell (sidebar + orb + chat + dock):
- Frameless window with custom title bar, rounded shell, ambient glow
  that follows the orb's colour + a whisper of film grain (A)
- Motion: page fades, bubble entrances, palette crossfades in the orb,
  animated connecting dots (B)
- Voice life: live caption of your last utterance, waveform in the dock,
  equaliser bars inside VYUK's bubble while she speaks, designed
  earcons — wake / sent / done / error (C)
- One SVG icon family everywhere, tray icon, app icon (D)
- Home strip (date · next reminder · briefing), tool results as cards,
  accent themes + orb styles, persisted with QSettings (E)
- First-run onboarding and Settings rebuilt as cards (F)

All backend wiring is unchanged from P0/original.
"""

# ------------------------------------------------------------------
# v5.50 (programme item 43): this module is now the re-export SHIM
# for the main window (the P-3 pattern). The implementation split
# by page/feature into sibling modules — gui/window_widgets.py
# (widgets, marks, helpers), gui/window_shell.py,
# gui/window_pages.py, gui/window_settings.py, gui/window_chat.py,
# gui/window_services.py, gui/window_session.py,
# gui/window_brain.py and gui/window_memory.py (the MainWindow
# feature mixins, composed below) — and every name the old module
# exposed is re-exported here, bound to the very same object, so
# every existing import and attribute access keeps working.
# ------------------------------------------------------------------

import asyncio
import datetime
import json
import math
import os
import random
import re
import threading
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPauseAnimation,
    QPropertyAnimation,
    QRectF,
    QSequentialAnimationGroup,
    QSettings,
    QSize,
    Qt,
    QTime,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSlider,
    QSpinBox,
    QStackedLayout,
    QStackedWidget,
    QSystemTrayIcon,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import clipboard_monitor  # Phase 3 D3: moved to core/desktop/
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import (
    LANGUAGES,
    TRANSCRIPTION_PROFILES,
    set_preferred_language,
)
from voice_assistant.core.gui_facade import confirm_gate, failsafe
from voice_assistant.core.gui_facade import hotkey_daemon  # Phase 3 D3: moved to core/desktop/
from voice_assistant.core.gui_facade import live_session  # Phase 3 B2h: brain hub moved to core/brain/brain.py
from voice_assistant.core.gui_facade import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
from voice_assistant.core.gui_facade import discover_plugins
from voice_assistant.core.gui_facade import scheduler
from voice_assistant.core.gui_facade import (
    generate_session_summary,
    save_session_summary,
)
from voice_assistant.core.gui_facade import vision_manager
from voice_assistant.core.gui_facade import wake_word_detector  # Phase 3 V1a-2: wake_word moved to core/voice/
from voice_assistant.core.gui_facade import VERSION

from .empty_art import GenerativeArt
from .icons import make_icon
from .memory_web import MemoryWebWidget, category_color, category_label
from .mini_orb import MiniOrb
from .onboarding import OnboardingDialog
from .sounds import Earcons
from .style import (
    PREMIUM_STYLE,
    build_stylesheet,
    derive_theme,
    load_bundled_fonts,
)
from .style import THEMES as STYLE_THEMES
from .visualizer import THEMES, OrbVisualizer

from .window_widgets import (
    _ASSETS,
    BridgeSignals,
    BackendWorker,
    AppShell,
    TitleBar,
    WaveformWidget,
    MiniEq,
    ThinkingDots,
    _TOOL_ICONS,
    _tool_icon_name,
    _TOOL_FRIENDLY_NAMES,
    _tool_friendly_name,
    _MISSING_KEY_NOTICE,
    _WATCHER_KIND_TO_SETTING,
    _CALL_APP_NAMES,
    _OWN_PROCESS_NAMES,
    _AMBIENT_MATERIAL_KINDS,
    _normalise_process_name,
    _foreground_process_name,
    _RECONNECT_DELAYS,
    _is_fatal_error,
    _friendly_error,
    _friendly_fallback,
    _friendly_setup,
    _md_lite,
    _rendered_plain,
    _title_case,
    _ActivitySparkline,
    _split_segments,
    _CodeBlock,
    _ComposerEdit,
    _AURA_CHIP_SIZE,
    _aura_chip_pixmap_cache,
    _aura_chip_pixmap,
    ChatBubble,
    CommandPalette,
    _SlideOffset,
    _SplashOrbMark,
    _WordmarkMark,
    SplashIgnition,
    _KOKORO_VOICE_ROSTER,
)
from .window_shell import ShellMixin as _ShellMixin
from .window_pages import PagesMixin as _PagesMixin
from .window_settings import SettingsMixin as _SettingsMixin
from .window_chat import ChatMixin as _ChatMixin
from .window_services import ServicesMixin as _ServicesMixin
from .window_session import SessionMixin as _SessionMixin
from .window_brain import BrainMixin as _BrainMixin
from .window_memory import MemoryMixin as _MemoryMixin
from .window_turns import TurnSurfacesMixin as _TurnSurfacesMixin


class MainWindow(_ShellMixin, _PagesMixin, _SettingsMixin,
                 _ChatMixin, _ServicesMixin, _SessionMixin,
                 _BrainMixin, _MemoryMixin, _TurnSurfacesMixin, QMainWindow):
    """The VYUK main window (v5.50, programme item 43).

    Behaviour-identical composition: every method that used to
    live in this class's body now lives, verbatim, on one of the
    feature mixins above (shell / pages / settings / chat /
    services / session / brain / memory), in the same order the
    methods appeared in the pre-split class. No method name is
    defined by more than one mixin, so the MRO resolves each
    attribute to exactly the body the old class resolved it to.
    """

# v5.58 gate: the turns mixin alias is used only by the bases line
# above, so drop it from the shim namespace — the v5.50 shim
# invariant (test_v550_p43 §A) is that main_window exposes exactly
# the census names, and nothing imports this alias from the shim
# (gui/window_turns.py is the canonical home).
del _TurnSurfacesMixin

# ------------------------------------------------------------------
# v5.50 (programme item 43), revised GAPS-28 (G-V6): no forwarding.
#
# Battery suites patch names on the module that OWNS them (e.g.
# ``window_widgets.live_session = fake``) — never on this shim.
# The module-class swap that used to forward assignments from
# this shim into the split modules is deleted: it existed for a
# patch-on-the-shim habit no test has (no test patches
# ``main_window.*`` and no test file references the forwarding
# class), and it served the one runtime-rebound name
# (``_aura_chip_pixmap_cache``) from a stale binding — the
# snapshot ``window_widgets`` imported at load time, never the
# value ``window_widgets_chatbits`` rebinds at render time.
#
# One name is still served live, by plain PEP 562 ``__getattr__``:
# ``_aura_chip_pixmap_cache`` is rebound at runtime (via
# ``global``) by its owner, ``window_widgets_chatbits`` — a
# snapshot import would freeze the pre-render value, so reads
# go to the owning module's dict on every access. Everything
# else is a real module attribute; assignment to this module
# binds here and nowhere else.
# ------------------------------------------------------------------
def __getattr__(name):
    # PEP 562: the single runtime-rebound name, served live from
    # its owner. Everything else is a real module attribute.
    if name == "_aura_chip_pixmap_cache":
        from . import window_widgets_chatbits as _owner

        return _owner.__dict__.get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | {"_aura_chip_pixmap_cache"})


del _aura_chip_pixmap_cache  # rebound at runtime; served via __getattr__
del _ShellMixin, _PagesMixin, _SettingsMixin, _ChatMixin
del _ServicesMixin, _SessionMixin, _BrainMixin, _MemoryMixin

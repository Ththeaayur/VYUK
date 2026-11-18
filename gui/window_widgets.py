"""Standalone widgets, marks and module-level helpers of the
main window: signals bridge, backend worker, shell/titlebar,
waveform/EQ/dots, result-card helpers, chat bubble, command
palette, splash marks and the Kokoro voice roster.

v5.50 (programme item 43): split out of gui/main_window.py,
which remains as the re-export shim. Bodies moved verbatim;
behaviour is unchanged.
"""


# Refactor Phase 1 step 5: this module is now the same-path
# re-export for its split families — every name below is the
# very same object as in its defining module, so every
# existing import and attribute access keeps working.

from .window_widgets_shell import (BridgeSignals, BackendWorker, AppShell, TitleBar)  # refactor P1 step 5 (moved verbatim)
from .window_widgets_chatbits import (_ASSETS, WaveformWidget, MiniEq, ThinkingDots, _ActivitySparkline, _CodeBlock, _ComposerEdit, _AURA_CHIP_SIZE, _aura_chip_pixmap_cache, _aura_chip_pixmap)  # refactor P1 step 5 (moved verbatim)
from .window_widgets_bubble import (_ACTION_PAD_X, _ACTION_PAD_Y, _ACTION_BORDER, _ACTIONS_TOP_MARGIN, _ACTIONS_WHO_GAP, _ACTIONS_RIGHT_INSET, _ACTIONS_BOTTOM_INSET, _ACTIONS_MIN_TOP, _DIMMED_OPACITY, ChatBubble)  # refactor P1 step 5 (moved verbatim)
from .window_widgets_toolnames import (_TOOL_ICONS, _tool_icon_name, _TOOL_FRIENDLY_NAMES, _tool_friendly_name)  # refactor P1 step 5 (moved verbatim)
from .window_widgets_helpers import (_MISSING_KEY_NOTICE, _WATCHER_KIND_TO_SETTING, _CALL_APP_NAMES, _OWN_PROCESS_NAMES, _AMBIENT_MATERIAL_KINDS, _normalise_process_name, _foreground_process_name, _RECONNECT_DELAYS, _is_fatal_error, _friendly_error, _friendly_fallback, _friendly_setup, _md_lite, _rendered_plain, _title_case, _split_segments, _summarise_tool, _humanise, _KOKORO_VOICE_ROSTER)  # refactor P1 step 5 (moved verbatim)
from .window_widgets_palette import (SHORTCUT_ROWS, palette_list_height_for, CommandPalette, ShortcutsSheet)  # refactor P1 step 5 (moved verbatim)
from .window_widgets_splash import (_SlideOffset, _SplashOrbMark, _WordmarkMark, SplashIgnition)  # refactor P1 step 5 (moved verbatim)

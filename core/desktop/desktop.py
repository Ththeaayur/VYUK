"""Wave 4 "Hands & Web" desktop track (v5.59, desktop worker W3).

Staged desktop verbs for VYUK, written to the Wave 4 brief's hard
constraint: NO new registry tools and NO fitted-declaration growth
(registry stays 480, fitted stays 229,785 B). That shapes where
each verb lives:

* Verbs that fit inside an EXISTING tool's schema are wired into
  that tool's handler: the HA-1 ladder's new rungs (HA-2 scan-code
  keystrokes, HA-6 clipboard paste) run inside core/desktop/uia.py's
  uia_type_text, HA-4's effect wait runs inside the expect path of
  uia_click_control / uia_type_text, and the HA-12/EY-A1
  easy-uiauto fallback runs inside uia_find_control. Those verbs
  ARE reachable today through the Wave 1 use_toolbox path, because
  use_toolbox call executes the existing tool through the same
  funnel (core/funnel/toolbox.py) — no new declaration is needed for a
  behaviour change inside an existing tool.

* Verbs that need a NEW action word on an enum-gated dispatcher
  (HA-5 virtual desktops on window_control, PC-C cursor pilots on
  cursor_control) are implemented HERE, behind plain Python
  functions, and are reached through core/desktop/control.py's
  window_control dispatcher (same handler, same funnel) — but
  their action words are NOT in the shipped window_control /
  cursor_control enum schemas, so the funnel's argument validator
  will refuse them until the coordinator adds the words to those
  enums in a declaration-growing wave. That one-line-per-tool
  declaration update is the ONLY remaining step for those verbs;
  it is deliberately NOT done here, because this wave forbids
  declaration growth. Nothing here registers a tool, and nothing
  here is imported by any shipped default path: every third-party
  package below is imported lazily inside the function that needs
  it, so this module imports cleanly on any OS, including
  headless Linux, where every verb reports itself unavailable
  instead of crashing.

Licences (verified 2026-10-25 from the packages' own distributed
sdists, NOT from search snippets):
* pyvda 0.6.0 — MIT. LICENSE.txt in the sdist is the MIT text
  (Copyright (c) 2015, 2016 Jari Pennanen, 2020 Mike Roberts),
  and PKG-INFO carries "License :: OSI Approved :: MIT License".
  Dependency: comtypes (its own licence is comtypes' business;
  pyvda is the only package this module imports).
* PyDirectInput 1.0.4 — MIT by classifier: setup.py declares
  "License :: OSI Approved :: MIT License". Caveat recorded
  honestly: the sdist ships NO LICENSE.txt file, so the MIT text
  itself was read only second-hand (the upstream repo's
  LICENSE.txt, via its rendered copy). Consequence: this module
  NEVER requires PyDirectInput — the HA-2 rung's primary engine
  is VYUK's own ctypes scan-code SendInput (core/desktop/uia.py), and
  PyDirectInput is tried only as an optional engine when the
  user already installed it. Nothing of it is bundled, and
  nothing is added to requirements.txt.
* easy-uiauto 0.9.1 — MIT. LICENSE in the sdist is the MIT text
  (Copyright (c) 2026 Poggi-Tang), and pyproject.toml declares
  "License :: OSI Approved :: MIT License".

NOT shipped here, by design:
* HE-9 Everything SDK — PILOT HELD (app terms owed). No code,
  no import, no probe. pilot_status() reports it as held.
* HA-11 — HELD, unidentified: the wave-10 rows that name HA-11's
  package are not in this working tree, so no package is guessed
  and no import is attempted. Its route is fixed (the HA-5 route:
  lazy optional import behind an existing dispatcher, licence
  verified from the primary source first) for the wave that names
  it. Inventing a package here would break the licence law.
* num2words — DELETED in v5.58 as LGPL-barred. Not reintroduced,
  not referenced as a dependency anywhere in this module.

Telemetry: none. Nothing here phones home, logs keystrokes, or
writes to data/ — the clipboard snapshot lives in memory only
and is dropped (never written to disk) the moment it is restored
or the paste attempt ends, whichever comes first.
"""


import sys  # INTENTIONAL (desktop split B7): the old module's stdlib import, re-exported so the facade namespace matches the old one.
import time  # INTENTIONAL (desktop split B7): the old module's stdlib import, re-exported so the facade namespace matches the old one.

# INTENTIONAL (desktop split B7): every name the old module defined
# is re-exported from the impl module that now owns it, so existing
# module-attribute importers (control_window, uia_discover, uia_type,
# w4_wiring, test_desktopreach) work unchanged and with identical
# function objects, and the test_desktopinput patch of
# core.desktop.desktop.clipboard_paste lands on the facade attribute
# that uia_type resolves through the module object.
# NOTE: the _clipboard_backend / _pyvda_override / _easy_uiauto_override
# re-exports below are value snapshots (None at import); the LIVE
# bindings live in desktop_clipboard / desktop_pilots, driven via
# set_clipboard_backend and the owning modules' readers.
from .desktop_pilots import (
    LICENCES,
    PILOT_STATUS,
    pilot_status,
    _is_windows,
    _pyvda_override,
    _pyvda,
    _pyvda_unavailable,
    _parse_desktop_target,
    _desk_name,
    _desk_label,
    virtual_desktop_list,
    virtual_desktop_switch,
    virtual_desktop_move,
    virtual_desktop_pin,
    _split_move_target,
    _match_window,
    desktop_action,
    _easy_uiauto_override,
    easy_uiauto_status,
    easy_uiauto_find,
)
from .desktop_clipboard import (
    _CLIPBOARD_MAX_BYTES,
    _CF_UNICODETEXT,
    _clipboard_backend,
    set_clipboard_backend,
    ClipboardSnapshot,
    _win32_clipboard,
    save_clipboard,
    _restore_snapshot,
    _set_text_excluding_history,
    clipboard_paste,
    ole_drag_drop,
)
from .desktop_glide import (
    glide_path_metrics,
    cursor_waypoint_glide,
    cursor_drag_with_hover_pause,
)

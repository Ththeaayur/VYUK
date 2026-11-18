from __future__ import annotations

# GAPS-15 (2026-11-13): moved from core/ to gui/ — this module
# builds Qt widgets / stylesheets / panels, which is GUI work
# that lived in core (core -> GUI imports). Code unchanged;
# only the imports changed: GUI imports are now relative,
# core imports go through the GUI facade (core/gui_facade.py).

# ------------------------------------------------------------------ #
# 1. Summon geometry / floating summon window
# ------------------------------------------------------------------ #

def build_summon_model(tools=None, recents=None):
    """SummonModel from live tool names (or injected lists).

    Tool items carry their declaration name as action_id; this window
    never resolves an action_id to a call — dispatch is gated by the
    caller (see handle_summon_dispatch) and never executes here.
    Names come from the raw declarations (INV-1 re-scope):
    the summon needs names only, so it no longer touches the
    fitted list — the name set is identical, zero behaviour
    change.
    """
    from . import summon_window as _summon

    if tools is None:
        try:
            from voice_assistant.core.gui_facade import tools as _tools  # GAPS-15: core import via the GUI facade

            decls = _tools.get_declarations()
            tools = [str(d.get("name", "")) for d in decls if d.get("name")]
        except Exception:  # noqa: BLE001 — empty tools, not a crash
            tools = []
    model = _summon.SummonModel(
        tools=[_summon.SummonItem(kind="tool", title=name, action_id=name)
               for name in (tools or [])],
        recents=[_summon.SummonItem(kind="recent", title=t, action_id=t)
                 for t in (recents or [])],
    )
    return model


def handle_summon_dispatch(window, item) -> bool:
    """Gate a summon dispatch request. NEVER executes anything.

    Refuses when the failsafe watchdog is tripped; otherwise records
    the request on the window for the caller. Execution, if any,
    belongs to a live path that gates through core/failsafe itself
    — this window is not an acting surface.
    """
    from voice_assistant.core.gui_facade import failsafe as _failsafe
    from . import summon_window as _summon

    gate = _summon.dispatch_allowed(_failsafe.tripped())
    if not gate["allowed"]:
        return False
    window._w9_last_summon_dispatch = item
    return True


def show_summon_window(window):
    """Create (once) and place the floating summon window at the
    cursor's monitor work area, re-validated on every summon."""
    from . import summon_window as _summon

    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QApplication

    existing = getattr(window, "_w9_summon_window", None)
    if existing is None:
        existing = _summon.SummonWindow(build_summon_model(), window)
        existing.dispatch_requested.connect(
            lambda item: handle_summon_dispatch(window, item))
        window._w9_summon_window = existing
    cursor = QCursor.pos()
    screens = _summon.screens_from_qt(QApplication.screens())
    result = existing.summon_at((cursor.x(), cursor.y()), screens)
    window._w9_last_summon_geometry = result
    return existing

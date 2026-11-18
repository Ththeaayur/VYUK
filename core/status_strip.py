"""Status strip tool (R8-6, yasb port): show / hide / report VYUK's
always-on status strip — a thin bar with live CPU / MEM / DISK / NET /
BAT, the five System Pulse vitals.

PORT, not adopt: YASB (amnweb/yasb, MIT — LICENSE read from ``main``)
is a full standalone Windows status-bar app. What is ported is the
pattern only — a small set of live widgets in a bar — built from
VYUK's own pieces (the Pulse sampler seam, VYUK's own Qt surfaces).
No YASB code is vendored, no YAML config surface, no CSS theme port.
The strip shows only what the tree can honestly read: the five Pulse
vitals. No media (the tree has no now-playing source), no workspaces
(the tree has no virtual-desktop concept), no pomodoro (``focus_session``
already exists), no per-app polling (the well.py sensing-free
boundary). The strip is opt-in and off by default.

SINK SEAM (the frame-sink precedent, v5.42 "Truth", E1): this module
is GUI-free on purpose — it cannot know whether a window is running.
So the window — the one place that owns the strip — registers a sink
here at startup (``gui/window_shell_frame.py``) and clears it on
close. The sink is a plain synchronous callable::

    fn(action: str) -> dict

with ``action`` one of ``"show"`` / ``"hide"`` / ``"status"`` and the
returned dict carrying ``ok`` (bool), ``visible`` (bool), ``text``
(the strip's current one-line text, "" when hidden) and ``error``
(str or None). The window's implementation hops to the GUI thread
itself and VERIFIES the outcome — a show that did not make the strip
visible is reported as a failure, never claimed. No sink registered
(headless runs, tests) is the honest third case: the tool result says
exactly that the strip isn't running, and points at the tray menu /
Settings toggle instead of inventing a strip.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from core.funnel.tools_registry_core import _funnel_only_fn


_ACTIONS = ("status", "show", "hide")

# The registered strip sink (None = no window is running).
_strip_sink: Callable[[str], dict] | None = None


def set_status_strip_sink(fn: Callable[[str], dict] | None) -> None:
    """Registers (or, with None, clears) the strip sink. Called by
    the GUI window, which owns the strip; this module itself never
    imports the GUI."""
    global _strip_sink
    _strip_sink = fn


def get_status_strip_sink() -> Callable[[str], dict] | None:
    """The currently registered strip sink (None when no window)."""
    return _strip_sink


def status_strip(action: str = "status") -> str:
    """Show / hide / report the status strip, in plain words.

    ``"show"`` makes the always-on strip appear (opt-in, off by
    default); ``"hide"`` tucks it away (sampling stops with it);
    ``"status"`` reports whether it is visible and what it reads
    right now. Every result says what actually happened — a show is
    only claimed when the strip IS visible afterwards.
    """
    if action not in _ACTIONS:
        return (
            "Give 'show', 'hide' or 'status' for the status strip — "
            f"'{action}' isn't one of them."
        )
    sink = _strip_sink
    if sink is None:
        return (
            "The status strip isn't running — VYUK's main window "
            "isn't open right now, so there is no strip to show. "
            "Open VYUK and use the tray menu or Settings to show it."
        )
    try:
        result = sink(action)
    except Exception as exc:  # noqa: BLE001 — a broken sink reads honest
        return f"The status strip didn't respond ({exc})."
    if not isinstance(result, dict):
        return "The status strip didn't answer clearly — try again."
    if not result.get("ok", False):
        return result.get("error") or "The status strip didn't do that."
    visible = bool(result.get("visible", False))
    text = str(result.get("text") or "")
    if action == "show":
        if not visible:
            return "The status strip didn't appear — try the tray menu instead."
        return f"Status strip is now visible — {text}" if text else (
            "Status strip is now visible."
        )
    if action == "hide":
        if visible:
            return "The status strip is still showing — try the tray menu instead."
        return "Status strip is now hidden."
    # status
    if not visible:
        return "Status strip is hidden."
    return f"Status strip is visible — {text}" if text else (
        "Status strip is visible."
    )


@_funnel_only_fn
def _tool_status_strip(action: str = "status") -> str:
    """Funnel handler: the status strip, in plain words."""
    return status_strip(action=action)


def register_all(registry) -> None:
    """Register the status-strip pack (R8-6, yasb port)."""
    registry.register(
        name="status_strip",
        description="Show, hide or report VYUK's status strip — a thin always-on bar at the top of the screen with live CPU, memory, disk, network and battery readings (the same five vitals as the System Pulse page). 'show' makes it appear, 'hide' tucks it away (it stops sampling while hidden), 'status' reports whether it is visible and what it reads right now. The strip is opt-in and off by default; it shows system-wide readings only, never per-app data, and never media or workspace state. Use when the user wants the live vitals on screen without opening VYUK, or asks what the strip shows.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "What to do: 'show', 'hide' "
                           "or 'status' (default 'status')"},
            },
            "required": [],
        },
        handler=_tool_status_strip,
    )

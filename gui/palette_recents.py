"""Command palette recents (UI Phase 2, D-P2-08).

The palette remembers the last 8 actions the user actually
ran, most recent first, so a repeated action is one tap
away. Only the action_id and the label are stored — never
parameters, never the confirmation ticket, never the tool
arguments (a recent must never replay a privileged action
on its own; tapping one re-runs the row through the
palette's normal path, confirmations included).

Storage: QSettings ``palette/recent`` — a list of
{"action_id": ..., "label": ...} dicts. QSettings may hand
a lone dict back for a one-item list, and a hand-edited conf
file may hold anything; ``_normalise`` turns every stored
form into a clean list. Recents are hidden in private mode
(the palette shows no recents at all there) and a record
failure (bad store, bad shape) means no recents — never a
crash, never a stale list.
"""

from __future__ import annotations

from voice_assistant.core.gui_facade import (
    qsettings_get,
    qsettings_set,
)

_KEY = "palette/recent"
MAX_RECENTS = 8


def _normalise(raw):
    """Any stored form -> a clean list of {action_id, label}."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action_id = str(item.get("action_id") or "").strip()
        label = str(item.get("label") or "").strip()
        if action_id and label and action_id not in [r["action_id"] for r in out]:
            out.append({"action_id": action_id, "label": label})
    return out[:MAX_RECENTS]


def load_recents(settings=None):
    """The stored recents, most recent first (never raises —
    a bad store reads as no recents)."""
    try:
        return _normalise(qsettings_get(_KEY, settings=settings))
    except Exception:  # noqa: BLE001 - recents are a convenience
        return []


def record_recent(action_id, label, settings=None):
    """Record one completed action: move it to the front,
    de-duplicate, cap at 8. Returns the new list (never
    raises — a failed record means no recents)."""
    action_id = str(action_id or "").strip()
    label = str(label or "").strip()
    if not action_id or not label:
        return load_recents(settings=settings)
    current = load_recents(settings=settings)
    current = ([{"action_id": action_id, "label": label}]
               + [r for r in current if r["action_id"] != action_id])
    current = current[:MAX_RECENTS]
    try:
        qsettings_set(_KEY, current, settings=settings)
    except Exception:  # noqa: BLE001 - recents are a convenience
        pass
    return current

"""Wave 4 CONTINUITY + WORKSPACES + PERF — session resume, RAM tiers.

Stdlib only, no GUI, no capture. Workspaces are named records of
caller-supplied state (a dict the caller owns); this module saves
and restores that record, it never decides what the state means.

Rules, enforced in code:
  * A workspace round-trip must be exact: restore returns exactly
    what was saved, or a failure — never a silently altered state.
  * Continuity records are resume notes: what was in progress, and
    what checkpoint it reached. A resume note never re-executes
    anything; the caller decides what to do with it.
  * RAM tiers are narrated, never silent: tier_for_ram returns the
    tier AND a narration string, so a downgrade is always visible
    to the user. Tiers: full (16 GB+), standard (8 GB target),
    reduced (4 GB+), core-only (below 4 GB). A negative or
    non-numeric RAM figure is invalid, never coerced.
"""

from __future__ import annotations

import copy


class WorkspaceStore:
    """Named workspace state records, exact round-trip only."""

    def __init__(self):
        self._store = {}

    def save(self, name: str, state: dict) -> dict:
        if not name:
            return {"ok": False, "reason": "workspace name is empty"}
        if not isinstance(state, dict):
            return {"ok": False, "reason": "workspace state must be a dict"}
        self._store[name] = copy.deepcopy(state)
        return {"ok": True, "name": name}

    def restore(self, name: str) -> dict:
        if name not in self._store:
            return {"ok": False, "reason": f"no workspace '{name}'"}
        return {"ok": True, "name": name,
                "state": copy.deepcopy(self._store[name])}

    def list_workspaces(self) -> list:
        return sorted(self._store)

    def delete(self, name: str) -> bool:
        return self._store.pop(name, None) is not None


def resume_note(mission: str, checkpoint: str, pending: list) -> dict:
    """Build a continuity resume note. It re-executes nothing."""
    if not mission:
        return {"ok": False, "reason": "mission name is empty"}
    return {"ok": True, "mission": mission, "checkpoint": checkpoint,
            "pending": list(pending),
            "note": "resume note only: nothing was re-executed"}


def tier_for_ram(ram_mb) -> dict:
    """RAM tier + narration. A downgrade is narrated, never silent."""
    if isinstance(ram_mb, bool) or not isinstance(ram_mb, (int, float)):
        return {"ok": False, "reason": "RAM figure must be a number"}
    if ram_mb < 0:
        return {"ok": False, "reason": "RAM figure cannot be negative"}
    if ram_mb >= 16384:
        tier, narration = "full", "Full experience: all layers and effects on."
    elif ram_mb >= 8192:
        tier, narration = "standard", (
            "Standard experience on the 8 GB target: full features, "
            "effects budgeted.")
    elif ram_mb >= 4096:
        tier, narration = "reduced", (
            "Reduced experience: fewer layers and effects, so the "
            "assistant stays smooth on this machine.")
    else:
        tier, narration = "core-only", (
            "Core-only experience: voice and basic control only, so "
            "the assistant stays usable on this machine.")
    return {"ok": True, "tier": tier, "narration": narration}

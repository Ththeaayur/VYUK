"""Wiring Build 3 — the shared Resilience trip gate and undo action.

This is the coordination point the audit (§1.4, §4 item 3, §5
Build 3) found missing: ``core/resilience/resilience_wiring.py`` had real
fences but no live caller, ``gui/resilience_status.py`` had a
renderer no page used, and ``core/undo_store.py`` had records no
button ever undid. This module binds them into one Qt-free
surface that both the Doctor page and ``planner.run_goal`` use,
so the status the GUI renders and the trip the loop halts on are
the SAME trip, not two copies that can drift apart.

Trip truth, in order:
  * the registry latch set by :func:`trip_resilience` (a trip
    recorded without a wiring instance, e.g. in tests);
  * the shared :class:`ResilienceWiring` instance's own latch
    (its ``trip()``) or its failsafe predicate;
  * the app failsafe (``core.failsafe.tripped``) itself — a
    failsafe trip pauses control whether or not any wiring
    instance was ever configured, and re-arming the wiring latch
    alone does NOT clear it (the re-arm report says so plainly).

C-14 / approve-first, enforced here, not just in the dialog:
  * :func:`rearm_resilience` refuses unless ``approved=True``
    (the Doctor button supplies that only on a Yes answer to a
    HudDialog confirm whose default is No) AND ``human=True``
    (the wiring's own rule: timers, beats and recovered hangs
    never re-arm). A refused re-arm changes nothing.
  * :func:`reset_failsafe` likewise refuses unless ``approved=True``
    AND ``human=True``, and it resets only through
    ``core.failsafe.reset()`` — never by clearing a flag directly.
    Its report names the failsafe and wiring sources separately.
  * :func:`undo_last` likewise refuses unless ``approved=True``.
    Every undo through the button is therefore confirm-gated,
    including undo of a change that was itself consequential —
    stricter than the C-14 minimum, deliberately.
  * Neither function executes, approves, or bypasses anything
    else. No wake, capture, or voiceprint is enabled, touched,
    or auto-started anywhere here.

Undo honesty:
  * Undo runs through the existing ``UndoStore`` mechanism and
    a caller-registered restorer for the change's class. A
    record is a before-state, not a restorer: if no restorer was
    registered for its class, :func:`undo_last` reports that
    plainly, preserves the record, and changes nothing — it
    never pretends an unrestorable change was undone.
  * A record on the cannot-undo list is skipped, not forced:
    ``undo_last`` undoes the last *undoable* action and lists
    the non-undoable records it skipped, with their reasons, so
    the button label can say both halves of the truth.
  * A tripped failsafe refuses the undo through the store's own
    fence, exactly as ``undo_store`` specifies.

Configuration and lifecycle:
  * The app configures this module once, at Doctor-page build
    (``gui/doctor_page.py``), with the app data directory:
    wiring stores under ``<DATA_DIR>/resilience_w3`` (own files,
    never ``assistant.db``) and the undo store at
    ``<DATA_DIR>/undo_w3.json``. Configuring twice with the same
    paths returns the existing instance — a status refresh must
    never silently replace (and un-trip) the wiring latch.
  * With nothing configured, :func:`is_tripped` still answers
    from the registry latch and the failsafe, and the status
    payload says ``configured: False`` so the Doctor page can
    say "not yet configured" instead of inventing a status.
  * :func:`reset_for_tests` exists for test isolation only.
    Production code must never call it: it would un-trip a live
    fence, which is exactly what the fences forbid.

Stdlib only, no GUI, no network, no new schema or cap, no
globe file, no version or data change. Field proof (owner-run
kill drill, scoped undo on his machine, his verdict) is still
owed — this module never claims it.
"""

from __future__ import annotations

import os

_WIRING = None
_WIRING_BASE = None
_UNDO_STORE = None
_UNDO_PATH = None
_REGISTRY_TRIPPED = False
_REGISTRY_REASON = ""
_RESTORERS = {}


def _failsafe_tripped() -> bool:
    try:
        from . import failsafe as _failsafe
    except Exception:  # noqa: BLE001 — top-level import fallback
        try:
            from core import failsafe as _failsafe
        except Exception:  # noqa: BLE001 — no failsafe, no stop state
            return False
    try:
        return bool(_failsafe.tripped())
    except Exception:  # noqa: BLE001 — a broken probe never un-trips
        return False


def _any_failsafe_tripped() -> bool:
    """Real failsafe OR the shared wiring's injected predicate.

    The wiring may be configured with an injected predicate (in
    tests, or by a caller that owns the trip signal); reading
    only the real failsafe would then report "resumed" while
    the wiring's own predicate still holds control paused.
    """
    if _failsafe_tripped():
        return True
    if _WIRING is not None:
        predicate = getattr(_WIRING, "_failsafe_tripped", None)
        if callable(predicate):
            try:
                return bool(predicate())
            except Exception:  # noqa: BLE001 — broken probe, not a trip
                return False
    return False


def _failsafe_reason() -> str:
    try:
        from . import failsafe as _failsafe
    except Exception:  # noqa: BLE001
        try:
            from core import failsafe as _failsafe
        except Exception:  # noqa: BLE001
            return ""
    try:
        return str(_failsafe.trip_reason() or "")
    except Exception:  # noqa: BLE001
        return ""


def _load_wiring_class():
    try:
        from . import resilience_wiring as _mod
    except Exception:  # noqa: BLE001
        from core.resilience import resilience_wiring as _mod  # Phase 3 R1: moved to core/resilience/
    return _mod.ResilienceWiring, list(_mod.RL_MODULES)


def _load_undo_store_class():
    try:
        from . import undo_store as _mod
    except Exception:  # noqa: BLE001
        from core import undo_store as _mod
    return _mod.UndoStore


def _load_status_renderer():
    for _path in ("gui", "voice_assistant.gui"):
        try:
            import importlib as _il

            _mod = _il.import_module(_path + ".resilience_status")
            return _mod.render_resilience_status
        except Exception:  # noqa: BLE001 — try the next import path
            continue
    return None


# -- configuration ----------------------------------------------------


def configure_resilience(base_dir, *, failsafe_tripped=None):
    """Create (or return, idempotently) the shared wiring instance.

    ``base_dir`` is where the wiring's own stores live. The
    failsafe predicate defaults to the app failsafe, so a
    failsafe trip makes the shared wiring trip too.
    """
    global _WIRING, _WIRING_BASE
    base_dir = str(base_dir)
    if _WIRING is not None and _WIRING_BASE == base_dir:
        return _WIRING
    wiring_class, _ = _load_wiring_class()
    predicate = failsafe_tripped
    if predicate is None:
        predicate = _failsafe_tripped
    _WIRING = wiring_class(base_dir, failsafe_tripped=predicate)
    _WIRING_BASE = base_dir
    return _WIRING


def shared_wiring():
    """The shared wiring instance, or None if never configured."""
    return _WIRING


def configure_undo(store_path):
    """Create (or return, idempotently) the shared undo store."""
    global _UNDO_STORE, _UNDO_PATH
    store_path = str(store_path)
    if _UNDO_STORE is not None and _UNDO_PATH == store_path:
        return _UNDO_STORE
    store_class = _load_undo_store_class()
    _UNDO_STORE = store_class(store_path)
    _UNDO_PATH = store_path
    return _UNDO_STORE


def shared_undo_store():
    """The shared undo store, or None if never configured."""
    return _UNDO_STORE


def reset_for_tests():
    """Clear all shared state. Tests only — never production."""
    global _WIRING, _WIRING_BASE, _UNDO_STORE, _UNDO_PATH
    global _REGISTRY_TRIPPED, _REGISTRY_REASON, _RESTORERS
    _WIRING = None
    _WIRING_BASE = None
    _UNDO_STORE = None
    _UNDO_PATH = None
    _REGISTRY_TRIPPED = False
    _REGISTRY_REASON = ""
    _RESTORERS = {}


# -- trip gate --------------------------------------------------------


def trip_resilience(reason: str) -> dict:
    """Latch a trip on the shared wiring (if configured) and on
    the registry latch, so the loop gate and the GUI agree."""
    global _REGISTRY_TRIPPED, _REGISTRY_REASON
    _REGISTRY_TRIPPED = True
    _REGISTRY_REASON = str(reason)
    if _WIRING is not None:
        _WIRING.trip(str(reason))
    return {"tripped": True, "reason": str(reason)}


def is_tripped() -> bool:
    """The single trip signal: registry latch, shared wiring
    (its latch or its failsafe predicate), or the app failsafe."""
    if _REGISTRY_TRIPPED:
        return True
    if _WIRING is not None:
        try:
            if _WIRING.is_tripped():
                return True
        except Exception:  # noqa: BLE001 — a broken probe never un-trips
            pass
    return _failsafe_tripped()


def trip_reason() -> str:
    """Why control is paused, in plain words, or '' if healthy."""
    if _REGISTRY_TRIPPED and _REGISTRY_REASON:
        return _REGISTRY_REASON
    if _WIRING is not None:
        try:
            status = _WIRING.status()
        except Exception:  # noqa: BLE001
            status = {}
        if status.get("trip_reason"):
            return str(status["trip_reason"])
    return _failsafe_reason()


def resilience_status() -> dict:
    """Status payload for the existing renderer, plus a
    ``configured`` flag so an unconfigured session never
    masquerades as healthy wiring."""
    if _WIRING is None:
        _, libraries = _load_wiring_class()
        return {
            "configured": False,
            "libraries": libraries,
            "tripped": is_tripped(),
            "trip_reason": trip_reason(),
            "armed": not is_tripped(),
            "safe_mode": False,
            "owed_reads": {},
            "fences": [
                "tripped failsafe = zero dispatches, including mid-replay",
                "no executor engine, no scheduler, no retry loop here",
                "no auto-resume without explicit human re-arm",
                "durability outside the executor, never in assistant.db",
            ],
            "field_proof": "NOT claimed: a kill drill on the owner's "
                           "machine, his run, his verdict, is still owed",
        }
    status = _WIRING.status()
    status["configured"] = True
    return status


def render_status_lines() -> list:
    """The existing renderer, over the shared payload — or the
    honest not-configured line when nothing was configured."""
    status = resilience_status()
    if not status.get("configured"):
        lines = ["Resilience wiring: not yet configured in this session."]
        if status.get("tripped"):
            lines.append("Control is paused: "
                         + (status.get("trip_reason") or "failsafe tripped"))
        return lines
    renderer = _load_status_renderer()
    if renderer is None:
        return ["Resilience wiring: status renderer unavailable."]
    return list(renderer(status))


def rearm_resilience(*, approved: bool, human: bool = True) -> dict:
    """Human, confirmed re-arm only. Refusals change nothing.

    ``approved`` comes from the Doctor button's confirm dialog
    (Yes only); ``human`` is the wiring's own rule. After a
    successful wiring re-arm, ``failsafe_still_tripped`` says
    plainly whether control is actually resumed or still paused
    by the separate failsafe latch — re-arming the wiring never
    claims to clear the failsafe.
    """
    if not human:
        return {"rearmed": False, "armed": False,
                "failsafe_still_tripped": _any_failsafe_tripped(),
                "reason": "re-arm requires an explicit human action"}
    if not approved:
        return {"rearmed": False, "armed": False,
                "failsafe_still_tripped": _any_failsafe_tripped(),
                "reason": "re-arm needs confirmation (Yes on the dialog)"}
    global _REGISTRY_TRIPPED, _REGISTRY_REASON
    # A wiring refusal clears nothing — the registry latch stays
    # set, so a refused re-arm can never silently un-trip.
    if _WIRING is not None:
        wiring_result = _WIRING.rearm(human=True)
        if not wiring_result.get("rearmed"):
            return {"rearmed": False, "armed": False,
                    "failsafe_still_tripped": _any_failsafe_tripped(),
                    "reason": wiring_result.get("reason")
                    or "wiring refused re-arm"}
    _REGISTRY_TRIPPED = False
    _REGISTRY_REASON = ""
    # After both latches are cleared, anything still tripped is
    # a failsafe source (real failsafe or injected predicate).
    failsafe_left = is_tripped()
    return {"rearmed": True, "armed": not failsafe_left,
            "failsafe_still_tripped": failsafe_left,
            "reason": ("re-armed, but the failsafe is still tripped — "
                       "control stays paused until the failsafe is reset")
            if failsafe_left else ""}


# -- failsafe reset ---------------------------------------------------


def _wiring_tripped() -> bool:
    """Registry latch or the shared wiring's own trip (its latch
    or its failsafe predicate) — the failsafe itself excluded,
    so a reset report can name each source separately."""
    if _REGISTRY_TRIPPED:
        return True
    if _WIRING is not None:
        try:
            return bool(_WIRING.is_tripped())
        except Exception:  # noqa: BLE001 — a broken probe never un-trips
            pass
    return False


def reset_failsafe(*, approved: bool, human: bool = True) -> dict:
    """Human, confirmed failsafe reset, through the failsafe
    module's own reset path — never by clearing a flag directly.

    Code-Perfection Build 1 closes the Wiring Build 3 gap: re-arm
    cleared the wiring latch but control stayed paused until the
    failsafe was reset, and no reset action existed. The report
    names each source separately, so one action never claims a
    full resume it did not achieve: a reset that leaves the
    wiring latch tripped still pauses control, and re-arm plus
    reset together, both confirmed, is what actually resumes it.
    A refused reset changes nothing. A stop flag still present
    makes the failsafe's own reset() refuse (its H23c rule) and
    the report carries that warning verbatim.
    """
    if not human:
        return {"reset": False, "armed": False,
                "failsafe_still_tripped": _failsafe_tripped(),
                "wiring_still_tripped": _wiring_tripped(),
                "reason": "reset requires an explicit human action"}
    if not approved:
        return {"reset": False, "armed": False,
                "failsafe_still_tripped": _failsafe_tripped(),
                "wiring_still_tripped": _wiring_tripped(),
                "reason": "reset needs confirmation (Yes on the dialog)"}
    try:
        from . import failsafe as _failsafe
    except Exception:  # noqa: BLE001
        try:
            from core import failsafe as _failsafe
        except Exception:  # noqa: BLE001
            return {"reset": False, "armed": False,
                    "failsafe_still_tripped": _failsafe_tripped(),
                    "wiring_still_tripped": _wiring_tripped(),
                    "reason": "failsafe module unavailable"}
    cleared = bool(_failsafe.reset())
    if not cleared:
        warning = ""
        try:
            warning = str(_failsafe.rearm_warning() or "")
        except Exception:  # noqa: BLE001
            warning = ""
        return {"reset": False, "armed": False,
                "failsafe_still_tripped": True,
                "wiring_still_tripped": _wiring_tripped(),
                "reason": warning or "stop flag still set — remove it "
                                     "to re-arm"}
    wiring_left = _wiring_tripped()
    return {"reset": True, "armed": not wiring_left,
            "failsafe_still_tripped": False,
            "wiring_still_tripped": wiring_left,
            "reason": ("failsafe reset, but the wiring latch is still "
                       "tripped — control stays paused until re-arm")
            if wiring_left else ""}


# -- undo action ------------------------------------------------------


def register_undo_restorer(change_class: str, restorer) -> dict:
    """Register how one change class is restored. The restorer is
    caller-supplied (it knows that class); this module never
    guesses a restoration."""
    if not change_class:
        return {"ok": False, "reason": "change class name is empty"}
    if not callable(restorer):
        return {"ok": False, "reason": "restorer must be callable"}
    _RESTORERS[str(change_class)] = restorer
    return {"ok": True, "change_class": str(change_class)}


def record_undo_change(change_id: str, *, change_class: str,
                       before, after) -> dict:
    """Record a change in the shared undo store, if configured."""
    if _UNDO_STORE is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    entry = _UNDO_STORE.record(str(change_id), change_class=change_class,
                               before=before, after=after)
    return {"ok": True, "record": entry}


def undo_last(*, approved: bool) -> dict:
    """Undo the last undoable recorded change, confirm-gated.

    Skips (and lists) newer non-undoable records instead of
    forcing them, and refuses plainly when there is no undoable
    change, no restorer, no store, no approval, or a tripped
    failsafe. Nothing is reported as undone unless the restorer
    verified the restoration.
    """
    if not approved:
        return {"undone": False,
                "reason": "undo needs confirmation (Yes on the dialog)"}
    if _UNDO_STORE is None:
        return {"undone": False,
                "reason": "no undo store configured in this session"}
    summary = _UNDO_STORE.summary()
    skipped = []
    target = None
    for entry in reversed(summary):
        if entry.get("taken_back"):
            continue
        if entry.get("can_be_taken_back"):
            target = entry
            break
        skipped.append({"id": entry.get("id"),
                        "class": entry.get("class"),
                        "reason": entry.get("reason") or "cannot be taken back"})
    if target is None:
        if skipped:
            first = skipped[0]
            return {"undone": False, "skipped_non_undoable": skipped,
                    "reason": ("no undoable change recorded; the latest "
                               f"change '{first['id']}' ({first['class']}) "
                               f"cannot be taken back: {first['reason']}")}
        return {"undone": False, "skipped_non_undoable": skipped,
                "reason": "no undoable change recorded"}
    restorer = _RESTORERS.get(target.get("class"))
    if restorer is None:
        return {"undone": False, "skipped_non_undoable": skipped,
                "reason": (f"no restorer registered for class "
                           f"'{target.get('class')}' — the record is "
                           "preserved and nothing was changed")}
    result = _UNDO_STORE.undo(target["id"], restorer,
                              failsafe_tripped=_failsafe_tripped)
    result = dict(result)
    result["id"] = target["id"]
    result["class"] = target.get("class")
    result["skipped_non_undoable"] = skipped
    return result

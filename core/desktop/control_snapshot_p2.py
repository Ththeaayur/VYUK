"""Code-Perfection Build 2 — real control-list snapshot source (stdlib only).

Closes the Wiring Build 2b gap honestly: until now no
control-list snapshot source existed in the tree, so client
goals passed control_snapshot=None and planner's control
revalidation skipped with a note. This module supplies REAL
control records — never grounding marks, never window titles
passed off as controls (that refusal stands: marks are
click-targets from a scan, not control records, and a title
is a window, not a control).

Source: the app's existing Windows UI Automation path
(core/desktop/uia.py): the foreground window's descendant walk
(_descendants, bounded by _SCAN_CAP) plus _element_facts,
with the automation id read from each element's
element_info where the element exposes one. The module is
import-guarded throughout: on non-Windows hosts, without
pywinauto, with no foreground window, or on a failed scan,
it returns None (no snapshot) instead of fabricating one —
planner then falls back to its honest skip, exactly as
before. A snapshot is a list of dicts in the shape
core/desktop/control_hardening.revalidate expects:

    {"control_id": ..., "fingerprint": ...,
     "role": ..., "name": ..., "automation_id": ...}

* control_id: the element's automation id when it exposes
  one (stable across runs for a well-built app); otherwise
  "control_type|name|rect" — a composite that is unique
  within one window scan and fails closed (a moved control
  gets a new id, so it is absent, not silently matched).
* fingerprint: sha256 of name|control_type|class_name|rect|
  automation_id, truncated. A control whose observable state
  changed since resolution is stale by fingerprint, exactly
  as the Wave 2 gate rules.
* Password controls are skipped: a snapshot must never
  become a path to revalidating (and then acting on) a
  password field. Nameless controls are skipped too — a
  control with no name cannot be addressed by a locator
  anyway, and including it could only collide.

Presence-only fingerprint rule (planner side, see
core/brain/planner.py _control_gate): an action whose args carry
no fingerprint revalidates on control_id presence alone.
That is deliberate, not a hole: an empty fingerprint is
the absence of evidence, and absence of evidence cannot
prove staleness — only absence of the control can. An
action that DOES carry a fingerprint revalidates on
presence + fingerprint match, as before.

Cost note: a snapshot is a full foreground-window
descendant scan (bounded by the tree's _SCAN_CAP, 2000).
Clients pass this function as planner's control_snapshot
callable, so it runs once per step; on a typical window
the walk is far below the cap, and on a host with no
automation it returns None immediately without a scan.
"""

from __future__ import annotations

import hashlib

_SNAPSHOT_CAP_FALLBACK = 2000


def _fingerprint(name: str, control_type: str, class_name: str,
                 rect: str, automation_id: str) -> str:
    blob = "|".join([name, control_type, class_name, rect, automation_id])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _record_from_facts(facts: dict, automation_id: str) -> dict | None:
    name = str(facts.get("name") or "").strip()
    if not name:
        return None
    control_type = str(facts.get("control_type") or "").strip()
    class_name = str(facts.get("class_name") or "").strip()
    rect = str(facts.get("rect") or "")
    automation_id = str(automation_id or "").strip()
    if automation_id:
        control_id = automation_id
    else:
        control_id = f"{control_type}|{name}|{rect}"
    return {
        "control_id": control_id,
        "fingerprint": _fingerprint(name, control_type, class_name,
                                     rect, automation_id),
        "role": control_type,
        "name": name,
        "automation_id": automation_id,
    }


def snapshot_controls(*, _window_fn=None, _facts_fn=None,
                      _is_password_fn=None) -> list | None:
    """A real control snapshot of the foreground window, or None.

    None means "no snapshot source here" (non-Windows host, no
    pywinauto, no foreground window, scan failure) — never an
    empty fabrication. An empty list means a successful scan
    that found no addressable controls. Injection seams
    (_window_fn, _facts_fn, _is_password_fn) exist for tests;
    production calls pass none of them.
    """
    try:
        from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; D2b interim closed
    except Exception:  # noqa: BLE001 — no uia module, no snapshot
        return None
    try:
        if _window_fn is not None:
            win, _desc = _window_fn()
        else:
            win, _desc = _uia._uia_window()
    except Exception:  # noqa: BLE001 — no window, no snapshot
        return None
    try:
        elements, failure = _uia._descendants(win)
    except Exception:  # noqa: BLE001 — scan failure is not a snapshot
        return None
    if failure is not None or elements is None:
        return None
    cap = int(getattr(_uia, "_SCAN_CAP", _SNAPSHOT_CAP_FALLBACK))
    records: list[dict] = []
    seen: set[str] = set()
    for el in elements[:cap]:
        try:
            facts = _facts_fn(el) if _facts_fn is not None else _uia._element_facts(el)
        except Exception:  # noqa: BLE001 — a dead element is skipped, not fatal
            continue
        if not isinstance(facts, dict):
            continue
        try:
            if _is_password_fn is not None:
                is_password = _is_password_fn(el)
            else:
                is_password = _uia._is_password_control(el)
        except Exception:  # noqa: BLE001 — unreadable password state: skip, never assume safe
            is_password = None
        if is_password is True:
            continue
        automation_id = ""
        try:
            automation_id = str(getattr(el.element_info, "automation_id", "") or "")
        except Exception:  # noqa: BLE001 — no automation id is fine, composite id follows
            automation_id = ""
        record = _record_from_facts(facts, automation_id)
        if record is None or record["control_id"] in seen:
            continue
        seen.add(record["control_id"])
        records.append(record)
    return records


def snapshot_status() -> dict:
    """{available, reason, count} for the current snapshot source.

    available=False with a reason is the honest state on
    hosts without automation; it is not an error.
    """
    snap = snapshot_controls()
    if snap is None:
        return {"available": False,
                "reason": "no UI Automation snapshot source on this host "
                          "(non-Windows, pywinauto missing, or no "
                          "foreground window)",
                "count": 0}
    return {"available": True, "reason": "", "count": len(snap)}


def current_control_snapshot() -> list | None:
    """The snapshot callable clients pass to planner.run_goal.

    Never raises; returns None when no snapshot source exists,
    in which case planner's honest-skip note applies.
    """
    try:
        return snapshot_controls()
    except Exception:  # noqa: BLE001 — a failing source is no source
        return None

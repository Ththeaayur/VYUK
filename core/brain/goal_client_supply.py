"""Wiring Build 2b — what the text-loop clients supply to
core/planner.run_goal's Build 2 wiring kwargs (stdlib only).

Wiring Build 2 added optional kwargs to planner.run_goal
(goal_context, playbook, interruption_check, control_snapshot)
but no caller passed them, so the wiring point was dormant.
This module is the single supply point both text-loop clients
(cloud_text_client.run_goal, local_client.run_goal) use, so the
two brains supply identical wiring instead of drifting apart.
The voice (Live) brain reaches the same supply indirectly: its
bridge hands a free-form goal to the injected text brain's
run_goal, it never calls planner.run_goal itself.

Supplied, in the task's order:

* goal_context — derived from the client's current observation
  at goal start: core.desktop.uia.desktop_snapshot()'s foreground window
  and focused control, in the shape planner's J1 merge expects
  ({"active_window": {"label": ...}, "focused_control":
  {"label": ...}}). Keys that were not observed are omitted, never
  guessed. When the probe has no eyes at goal start (snapshot
  None), the client passes an explicit empty dict: J1 then
  reports "unresolved" honestly instead of resolving against a
  guessed target, and the live per-step observation still feeds
  the loop as before.

* playbook — the PlaybookStore instance the planner looks up.
  Code-Perfection Build 2: the store is now PERSISTENT.
  shared_playbook() returns a PlaybookStore
  (core/playbook_store.py) backed by data/playbooks.json:
  a proven entry added in one session is reusable after a
  restart, an unproven entry is still refused by reuse, a
  deletion step is still refused at add time (and again on
  load), and a corrupted/missing file degrades to an empty
  playbook with a note, never a guessed entry. Q10 SINGLE
  STORE (owner ruling, supersedes the old J9-only decision):
  the loop's single playbook store holds BOTH the J9 goal
  entries the planner looks up AND the fl_playbook finance
  bullets the finance tools use — one file, two namespaces.
  The planner's lookup still pulls goal entries only, never
  finance bullets, so one goal cannot pull two playbooks'
  steps; the finance tools still use bullets only.

* interruption_check — the app's EXISTING stop signal, reused,
  not a new UI: core.failsafe.tripped(), which the goal pill's
  Stop button (gui/window_session._on_goal_stop_clicked, existing
  control, no new page, no renumbering) already trips together
  with the session-epoch bump. When tripped, the check returns
  the J7-shaped request {"reason": <trip reason or "stopped">,
  "resume": False}: a stop is never a resume request, so the J7
  machine abandons with the held-back list. When not tripped it
  returns None and the loop runs untouched.
  ORDERING, stated plainly: planner.run_goal checks the failsafe
  itself (_stop_checks) at the top of every step iteration, before
  it consults interruption_check. So in a real run a GUI stop
  halts via that failsafe halt at the next step boundary, not via
  a J7 pause report — the J7 check supplied here is the same
  signal, tested end-to-end below, and it surfaces pause/abandon
  reports on any path where the failsafe check is bypassed or
  faked (tests do exactly that). No GUI file is edited by this
  build: the stop control already trips the signal this check
  reads, so there is nothing new to wire into the GUI.

* control_snapshot — Code-Perfection Build 2: now supplied
  as the callable core/desktop/control_snapshot_p2.
  current_control_snapshot, which scans the foreground
  window's real UI Automation descendants into
  {control_id, fingerprint} records. Grounding marks are
  still never passed as control records (that refusal
  stands: marks are click-targets, not control records).
  Where no automation source exists (non-Windows host,
  pywinauto missing, no foreground window) the callable
  returns None and planner's honest-skip behaviour applies
  (a note is emitted, the existing registry gate stack
  including C-14 still applies). Presence-only rule: an
  action with no fingerprint revalidates on control_id
  presence alone; a fingerprinted action revalidates on
  presence + fingerprint (see planner._control_gate).

Guardrails: nothing here executes, approves, or bypasses
anything. Budgets, strikes, and the C-14 gate stack are planner's
and are unchanged; this module only supplies inputs to them.
"""

from __future__ import annotations

_PLAYBOOK = None


def shared_playbook(path=None):
    """The session's persistent single-store PlaybookStore
    (goal entries + finance bullets, two namespaces).

    On first creation the playbook loads data/playbooks.json
    (or the given path, for tests); a missing/corrupt file
    degrades to an empty playbook with a note in
    last_load_notes, never a crash. Goal adds and proof-setting
    save atomically; reuse never writes.
    """
    global _PLAYBOOK
    if _PLAYBOOK is None:
        from ..memory import playbook_store as _store  # Phase 3 B1b: moved to core/brain/

        _PLAYBOOK = _store.PlaybookStore(path)
        _PLAYBOOK.load(path)
    return _PLAYBOOK


def reset_shared_playbook(path=None):
    """Drop the shared singleton (tests / session reset only).

    The next shared_playbook() call reloads from file. This
    is a test helper, not a user action: it never deletes
    the store file.
    """
    global _PLAYBOOK
    _PLAYBOOK = None
    return shared_playbook(path)


def current_control_snapshot():
    """The control snapshot callable's value for one step.

    Thin wrapper over core/control_snapshot_p2 so clients
    import from one supply point. Returns a list of real
    control records, or None where no automation source
    exists (planner then honestly skips revalidation).
    """
    try:
        from ..desktop import control_snapshot_p2 as _snap  # Phase 3 B1b: moved to core/brain/ (D2b: control_snapshot_p2 now in core/desktop/)

        return _snap.current_control_snapshot()
    except Exception:  # noqa: BLE001 — no source, no snapshot, no crash
        return None


def goal_context_from_snapshot(snapshot) -> dict:
    """Convert a uia desktop snapshot to a J1 goal context.

    Only observed keys are included; an unobserved or empty
    snapshot becomes an explicit empty dict, never a guess.
    """
    if not isinstance(snapshot, dict):
        return {}
    context: dict = {}
    fg = snapshot.get("fg")
    if fg:
        context["active_window"] = {"label": str(fg)}
    focus = snapshot.get("focus")
    if focus:
        context["focused_control"] = {"label": str(focus)}
    return context


def current_goal_context() -> dict:
    """The client's current observation as a goal context.

    A probe failure degrades to an explicit empty dict (the
    probe's own contract: it never raises, it returns None when
    it has no eyes here).
    """
    try:
        from ..desktop import uia as _uia  # Phase 3 B1b: moved to core/brain/; Phase 3 D4b: uia hub moved to core/desktop/

        snapshot = _uia.desktop_snapshot()
    except Exception:  # noqa: BLE001 — no observation, no guess
        snapshot = None
    return goal_context_from_snapshot(snapshot)


def stop_interruption_check():
    """J7-shaped interruption request from the existing stop signal.

    Returns {"reason": ..., "resume": False} when the failsafe is
    tripped (the goal pill's Stop, or any other trip_now caller),
    else None. A stop is never a resume request.
    """
    try:
        from .. import failsafe as _failsafe  # Phase 3 B1b: moved to core/brain/

        if _failsafe.tripped():
            reason = ""
            try:
                reason = str(_failsafe.trip_reason() or "")
            except Exception:  # noqa: BLE001 — reason is a nicety
                reason = ""
            return {"reason": reason or "stopped",
                    "resume": False}
    except Exception:  # noqa: BLE001 — no signal, no interruption
        pass
    return None

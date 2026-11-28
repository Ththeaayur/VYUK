"""GUI presentation for coordinator pending approvals (§7 consumers batch 7).

Batch 6 gave the coordinator a re-issue-after-approval protocol
(``pending_approval`` / ``resolve_pending``) but no GUI code read it,
so a paused task's approval request was invisible. This module is
that GUI layer, and it reuses the EXISTING confirmation popup
(``gui/window_services.ServicesMixin._build_confirm_dialog`` —
headline, about line, collapsed Details, "Don't allow" /
"Allow once", safe button default + initial focus so Enter
denies) instead of inventing a second dialog. No new dialog
contract, no new wording family, no Home change.

Boundaries, stated plainly:

* The dialog is the presentation; ``resolve_pending`` is the
  decision. Showing the dialog approves nothing — only the
  explicit Allow click resolves, and only through the
  coordinator's ``resolve_pending``.
* Silence is denial: the dialog carries a 60 s timer
  (``core.confirm_gate.DEFAULT_TIMEOUT_S``, the gate's own
  source) that rejects on expiry. HudDialog itself has no
  countdown (see gui/design_system/DECISIONS.md — the 60 s
  default-deny lives in confirm_gate + the handler thread
  wait, not in the dialog); the timer here is the pending
  path's equivalent, owned by the presenter, not the dialog.
* Voice never resolves: no voice path into
  ``resolve_pending`` exists anywhere in this module, and the
  suite pins that the voice hook (window_services'
  ``_maybe_confirm_reply``) only ever resolves confirm-gate
  requests. A spoken "yes" is not an approval (C-14).
* The §7 tools are confirm-gate tools, hence never
  allowlistable, so the "Always allow this kind" third button
  never appears on a pending-approval dialog.
* Cancel is not deny: the Review flow offers a "Cancel task"
  button beside the dialog's own buttons (presenter chrome
  on the built dialog — the shared S-1 builder is untouched,
  so every other confirm dialog is byte-identical). Deny
  refuses this step and the task stays alive (it fails the
  step honestly, or skips on on_fail=skip); Cancel ends the
  whole task through coordinator.cancel_task, clears the
  pending record, and writes the ledger state "cancelled"
  with no refused outcome appended — the Activity record
  then renders the cancelled state (Partial with at least
  one successful step, Failed otherwise, per
  gui/task_state's standing contract), never "Needs
  approval" again, and the task drops out of future Review
  queues (list_interrupted covers running/paused/verifying
  only). A stale offer cancels nothing: if the live task no
  longer carries the pending record, or the ledger record
  moved on, the cancel path returns None and the task is
  left exactly as it is — a cancel must never land on a
  task the user already approved elsewhere.
* Voice never cancels: no voice path into cancel_task for a
  pending record exists anywhere in this module, and the
  suite pins that the voice hook (window_services'
  _maybe_confirm_reply) only ever resolves confirm-gate
  requests and never routes into the coordinator pending
  path. The one pre-existing voice-adjacent cancel caller,
  core/brain/voice_goal.cancel_voice_goal, cancels the
  voice goal's OWN task on interruption — it never answers
  or dismisses a Review offer and never resolves an
  approval, so it is out of this path, not a second
  spelling of it.
* Dialog first, resolve second: the Review flow presents the
  dialog from the ledger summary (GUI thread), then resolves
  on a worker thread. The worker may run task steps and the
  funnel gate may pop again on re-issue (two-layer protocol,
  standing rule: every execute call gates independently) —
  the GUI thread is free throughout, so no deadlock.
"""

from __future__ import annotations


def pending_approval_summary(pending):
    """Map a coordinator pending record to (tool_name, args_summary).

    Pure, stdlib-only: the tool id, the step label, the engine
    sentence verbatim, and the confirm-flag name. Nothing else
    is invented — no risk/data-scope prose is synthesized here;
    the dialog's own headline/about/note chrome frames it, and
    the Details section carries this summary verbatim (the
    S-1 builder shows "Action: {friendly} ({tool})" + summary).
    Missing fields degrade to the tool id / empty lines, never
    to a fabricated label.
    """
    pending = pending if isinstance(pending, dict) else {}
    tool = str(pending.get("tool") or "")
    label = str(pending.get("label") or "") or tool
    sentence = str(pending.get("sentence") or "")
    confirm_arg = str(pending.get("confirm_arg") or "")
    lines = [f"Step: {label}"]
    if sentence:
        lines.append(sentence)
    if confirm_arg:
        lines.append(f"Confirm flag: {confirm_arg}")
    return tool, "\n".join(lines)


def interrupted_pending_approvals(summaries):
    """Pending-approval offers inside ``list_interrupted`` output.

    Pure, stdlib-only. Returns [(task_id, pending-copy)] for
    every summary whose ``pending_approval`` is a dict carrying
    a tool. Summaries without one, malformed entries, and
    entries without a task id are dropped, never offered.
    """
    out = []
    for summary in summaries or []:
        if not isinstance(summary, dict):
            continue
        pending = summary.get("pending_approval")
        if not isinstance(pending, dict) or not pending.get("tool"):
            continue
        task_id = summary.get("task_id")
        if not task_id:
            continue
        out.append((str(task_id), dict(pending)))
    return out


def pending_approvals_count(ledger=None):
    """The ONE pending-approvals count (UI Phase 2, D-P2-02).

    The Activity badge, the Activity page's "Review pending
    approvals (N)" button, the Home headline's "N approvals
    waiting", and the Review queue itself all read this
    function — never their own count — so the badge, the page,
    and the queue can never disagree again.

    The count is the Review queue's length:
    ``interrupted_pending_approvals(list_interrupted())`` —
    interrupted (running / paused / verifying), non-goal
    records whose pending_approval is a dict carrying a tool.
    Before Phase 2 the three surfaces each counted
    differently: the page counted paused live records from
    all_records() (which INCLUDED paused goal records and
    EXCLUDED verifying records), the Home headline counted
    every record with any truthy pending_approval (goals,
    stale dicts without a tool, terminal records included),
    and only the Review queue used this formula. A verifying
    record with a pending approval counted in the queue but
    showed no card anywhere — the badge and the page
    disagreed in both directions. Never raises: a broken
    ledger reads as zero (the badge hides; the page shows
    its own unavailable row) — a count must never break a
    page.
    """
    try:
        from voice_assistant.core.gui_facade import TaskLedger

        if ledger is None:
            ledger = TaskLedger()
        return len(interrupted_pending_approvals(
            ledger.list_interrupted()))
    except Exception:  # noqa: BLE001 — a count never breaks a page.
        return 0


def present_pending_dialog(window, pending, *, timeout_s=None):
    """Show the EXISTING confirm dialog for one pending record.

    GUI thread only. Returns True for an explicit "Allow once"
    click, False for "Don't allow", window close, the silence
    timeout, or any dialog failure. A broken popup denies —
    it never waves an approval through (the same fail-closed
    rule as ``_on_confirm_requested``: designed builder first,
    plain builder on failure, deny if both fail).

    This function presents ONLY — it never calls
    ``resolve_pending``. The caller resolves the returned bool
    (on a worker thread, via :func:`resolve_pending_answer`).
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    if timeout_s is None:
        try:
            from voice_assistant.core.gui_facade import confirm_gate
            timeout_s = float(confirm_gate.DEFAULT_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — one timeout source
            timeout_s = 60.0
    tool, summary = pending_approval_summary(pending)
    crop = pending.get("stored_crop")
    # C7 dialog crop: pass the crop through ONLY when the record
    # carries one. The plain (tool, summary) call is the
    # long-standing builder contract — every record without a
    # crop (all existing callers and stubs) keeps it exactly,
    # so a builder that predates the crop slot still works.
    crop_kw = {"stored_crop": crop} if crop is not None else {}
    try:
        dlg = window._build_confirm_dialog(tool, summary, **crop_kw)
    except Exception:  # noqa: BLE001 — presentation must never
        try:  # deny the human their say: the plain dialog
            dlg = window._build_plain_confirm_dialog(
                tool, summary, **crop_kw)
        except Exception:  # noqa: BLE001 — a broken popup
            return False  # denies, never allows.
    timer = QTimer(dlg)
    timer.setSingleShot(True)
    timer.timeout.connect(dlg.reject)
    try:
        timer.start(int(float(timeout_s) * 1000))
    except Exception:  # noqa: BLE001 — a timer failure must
        pass  # not break the dialog itself.
    try:
        accepted = (dlg.exec() == QDialog.DialogCode.Accepted)
    except Exception:  # noqa: BLE001 — a broken popup denies.
        accepted = False
    finally:
        try:
            timer.stop()
        except Exception:  # noqa: BLE001 — teardown never raises.
            pass
    return bool(accepted)


_REVIEW_CANCEL_CODE = 2  # QDialog custom result code for
# "Cancel task": distinct from Accepted (1, Allow) and
# Rejected (0, Don't allow / close / silence timeout), so
# the Review flow can never mistake a cancel for a deny.


def present_pending_dialog_with_cancel(window, pending, *,
                                       timeout_s=None):
    """Show the Review dialog WITH a "Cancel task" button.

    GUI thread only. Returns "allow" for an explicit "Allow
    once" click, "deny" for "Don't allow", window close, the
    silence timeout, or any dialog failure, and "cancel"
    ONLY for an explicit "Cancel task" click. The dialog is
    the SAME shared S-1 builder as present_pending_dialog
    (designed builder first, plain builder on failure) with
    presenter chrome appended to the built instance: one
    muted line stating cancel ends the whole task (it is not
    the same as Don't allow) plus the button itself. The
    builder is never modified, so every other confirm dialog
    is unchanged. A broken popup denies — it never waves an
    approval through, and it never invents a cancel either:
    if the cancel row cannot be attached to the built
    dialog, this raises instead of presenting a Review
    dialog that silently lacks the affordance (the queue
    treats that as "no decision", never as a deny).

    This function presents ONLY — it never calls
    resolve_pending or cancel_task. The caller resolves or
    cancels the returned outcome (on a worker thread, via
    resolve_pending_answer / cancel_pending_answer).
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (QHBoxLayout, QLabel,
                                   QPushButton, QDialog)
    from .style import TEXT_MUTED

    if timeout_s is None:
        try:
            from voice_assistant.core.gui_facade import confirm_gate
            timeout_s = float(confirm_gate.DEFAULT_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — one timeout source
            timeout_s = 60.0
    tool, summary = pending_approval_summary(pending)
    crop = pending.get("stored_crop")
    # C7 dialog crop: same conditional threading as
    # present_pending_dialog — the kwarg passes only when the
    # record carries a crop, so the plain (tool, summary) call
    # stays byte-identical for every record without one.
    crop_kw = {"stored_crop": crop} if crop is not None else {}
    try:
        dlg = window._build_confirm_dialog(tool, summary, **crop_kw)
    except Exception:  # noqa: BLE001 — presentation must never
        try:  # deny the human their say: the plain dialog
            dlg = window._build_plain_confirm_dialog(
                tool, summary, **crop_kw)
        except Exception:  # noqa: BLE001 — a broken popup
            return "deny"  # denies, never allows.
    # Presenter chrome on the built instance (never a builder
    # change): the cancel affordance lives here and only here,
    # so the journal reconcile flow — which reuses
    # present_pending_dialog with a synthetic record and no
    # task to cancel — never offers it.
    try:
        note = QLabel(
            "Cancel task ends the whole task — it is not the "
            "same as Don't allow, which only refuses this step.")
        note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        note.setWordWrap(True)
        dlg.content_layout().addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Cancel task")
        btn_cancel.clicked.connect(
            lambda: dlg.done(_REVIEW_CANCEL_CODE))
        row.addWidget(btn_cancel)
        dlg.content_layout().addLayout(row)
    except Exception:  # noqa: BLE001 — a Review dialog
        raise RuntimeError(  # without its cancel affordance
            "Review dialog cannot carry the cancel button")
    timer = QTimer(dlg)
    timer.setSingleShot(True)
    timer.timeout.connect(dlg.reject)
    try:
        timer.start(int(float(timeout_s) * 1000))
    except Exception:  # noqa: BLE001 — a timer failure must
        pass  # not break the dialog itself.
    try:
        result = dlg.exec()
    except Exception:  # noqa: BLE001 — a broken popup denies.
        result = QDialog.DialogCode.Rejected
    finally:
        try:
            timer.stop()
        except Exception:  # noqa: BLE001 — teardown never raises.
            pass
    if result == _REVIEW_CANCEL_CODE:
        return "cancel"
    if result == QDialog.DialogCode.Accepted:
        return "allow"
    return "deny"


def cancel_pending_answer(task_id, *, live_coordinator=None,
                          ledger=None):
    """Cancel one pending-approval task, worker-safe (no Qt).

    Live first, mirroring resolve_pending_answer: when the
    live coordinator holds the task, it is the truth — a
    live-held task with no pending record means a stale
    offer, which cancels nothing (None) rather than killing
    a task the user already approved elsewhere.

    Otherwise the rebuild path: a fresh coordinator bound
    to the ledger resumes the interrupted record (a rebuilt
    pending task re-stops at its pending step — C-14
    re-gating holds), then the cancel lands on it. When the
    resume itself settles the task (funnel-gate denial, or
    the step no longer pending), the task already moved on
    and the cancel returns None instead of killing a live
    task. None only when nothing was found, the offer was
    stale, or the task moved on; True when the task was
    cancelled. The cancel writes the ledger state
    "cancelled" with no refused outcome appended — that is
    what distinguishes it from a deny in the record.
    """
    task_id = str(task_id)
    if live_coordinator is not None:
        try:
            held = live_coordinator.get_task(task_id)
        except Exception:  # noqa: BLE001 — a broken live lookup
            return None  # is not a licence to rebuild over it.
        if held is not None:
            if live_coordinator.pending_approval(task_id) is None:
                return None
            return bool(live_coordinator.cancel_task(task_id))
    if ledger is None:
        return None
    record = ledger.get(task_id)
    if not isinstance(record, dict):
        return None
    if record.get("state") != "paused":
        return None
    pend = record.get("pending_approval")
    if not isinstance(pend, dict) or not pend.get("tool"):
        return None
    from voice_assistant.core.gui_facade import TurnCoordinator  # Phase 3 B1a-2: moved to core/brain/

    coordinator = TurnCoordinator(ledger=ledger)
    receipt = coordinator.resume_ledger_task(record, ledger=ledger)
    if receipt is None:
        return None
    if coordinator.pending_approval(task_id) is None:
        return None
    return bool(coordinator.cancel_task(task_id))


def cancel_held_task(task_id, *, live_coordinator=None):
    """Cancel a task the live coordinator holds (presenter-only cancel path).

    The single place outside ``cancel_pending_answer`` that may
    call ``cancel_task``: recovery offers are not pending
    approvals, so they cannot go through ``cancel_pending_answer``
    (it gates on a pending record and would refuse them), but
    the GUI boundary still requires every ``cancel_task`` call
    to live in this presenter module. The caller keeps the
    ``get_task`` check (only a held task is cancelled); this
    helper only performs the cancel itself. True when the task
    was cancelled, False when the coordinator refused it or the
    cancel failed — never raises, so a failed cancel abandons
    nothing.
    """
    if live_coordinator is None:
        return False
    try:
        return bool(live_coordinator.cancel_task(task_id))
    except Exception:  # noqa: BLE001 — a failed cancel abandons nothing.
        return False


def resolve_pending_answer(task_id, approved, *, live_coordinator=None,
                           ledger=None):
    """Resolve one pending approval, worker-safe (no Qt).

    Live first: when the live coordinator holds the task, it is
    the truth — a pending record resolves through it, and a
    live-held task with no pending record means a stale answer,
    which resolves nothing (None) rather than rebuilding a
    live task from the ledger behind its back.

    Otherwise the rebuild path: a fresh coordinator bound to
    the ledger resumes the interrupted record (a rebuilt
    pending task re-stops at its pending step — C-14 re-gating
    holds), then the dialog answer resolves it. When the resume
    itself settles the task (funnel-gate denial, or the step
    no longer pending), the resume receipt is returned instead
    — the task did change state, and the caller refreshes from
    it. None only when nothing was found or resumable.
    """
    task_id = str(task_id)
    if live_coordinator is not None:
        try:
            held = live_coordinator.get_task(task_id)
        except Exception:  # noqa: BLE001 — a broken live lookup
            return None  # is not a licence to rebuild over it.
        if held is not None:
            if live_coordinator.pending_approval(task_id) is None:
                return None
            return live_coordinator.resolve_pending(
                task_id, approved=bool(approved))
    if ledger is None:
        return None
    record = ledger.get(task_id)
    if not isinstance(record, dict):
        return None
    from voice_assistant.core.gui_facade import TurnCoordinator  # Phase 3 B1a-2: moved to core/brain/

    coordinator = TurnCoordinator(ledger=ledger)
    receipt = coordinator.resume_ledger_task(record, ledger=ledger)
    if receipt is None:
        return None
    if coordinator.pending_approval(task_id) is None:
        return receipt
    return coordinator.resolve_pending(task_id, approved=bool(approved))


def review_pending_queue(window, queue, *, live_coordinator=None,
                         ledger=None, refresh=None, poll_ms=500):
    """Offer each pending approval in turn, then refresh.

    GUI thread only for the dialog half: each queue head gets
    :func:`present_pending_dialog_with_cancel` (modal, so the
    user answers before anything else runs), then its outcome
    lands on a worker thread: "allow"/"deny" resolve via
    :func:`resolve_pending_answer` (which may run task steps
    and re-pop the funnel gate — the GUI thread is free
    throughout, so no deadlock), "cancel" cancels via
    :func:`cancel_pending_answer` (the whole task ends, the
    ledger records "cancelled" with no refused outcome).
    A poll timer advances the queue only when the worker
    lands; when the queue empties, ``refresh`` (the Activity
    refresh) runs. A dialog dismissal counts as denial,
    never approval and never cancel; a stale offer resolves
    or cancels nothing and the refresh simply shows the new
    state; a dialog that cannot carry its cancel affordance
    decides nothing at all (the offer stays live for the
    next Review pass instead of being misread as a deny).
    ``window`` need only carry the confirm-dialog builders
    (the MainWindow does, via ServicesMixin).
    """
    import threading

    from PySide6.QtCore import QTimer

    if not queue:
        if callable(refresh):
            try:
                refresh()
            except Exception:  # noqa: BLE001 — refresh never
                pass  # breaks the review flow.
        return
    task_id, pending = queue[0]
    try:
        outcome = present_pending_dialog_with_cancel(window, pending)
    except Exception:  # noqa: BLE001 — a broken Review dialog
        outcome = "error"  # decides nothing: neither deny
        # nor cancel is invented for an offer never shown.
    done = threading.Event()

    def _work():
        try:
            if outcome == "cancel":
                cancel_pending_answer(
                    task_id,
                    live_coordinator=live_coordinator, ledger=ledger)
            elif outcome in ("allow", "deny"):
                resolve_pending_answer(
                    task_id, outcome == "allow",
                    live_coordinator=live_coordinator, ledger=ledger)
        except Exception:  # noqa: BLE001 — the worker never
            pass  # breaks the GUI; the refresh shows truth.
        finally:
            done.set()

    threading.Thread(target=_work, daemon=True).start()
    timer = QTimer(window)
    timer.setSingleShot(False)

    def _tick():
        if done.is_set():
            try:
                timer.stop()
            except Exception:  # noqa: BLE001 — teardown
                pass  # never raises.
            review_pending_queue(
                window, queue[1:], live_coordinator=live_coordinator,
                ledger=ledger, refresh=refresh, poll_ms=poll_ms)

    timer.timeout.connect(_tick)
    timer.start(int(poll_ms))

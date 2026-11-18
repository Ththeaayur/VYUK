"""GUI presentation for coordinator crash-reconcile decisions (B21).

Batch 21 gave the coordinator a reconcile pause
(``pending_reconcile`` / ``resolve_reconcile``) but no GUI code
read it, so a paused task's decision was invisible. This module
is that GUI layer, mirroring gui/pending_question.py (B15): a
dialog that asks "Did step '<label>' apply before the crash?"
and reports the decision through the coordinator's
``resolve_reconcile`` (the only way to decide — never voice,
C-14), a summary for the Activity page's pending queue, and
a queue review that offers the oldest interrupted task first.

Boundaries, stated plainly:

* The dialog is the presentation; ``resolve_reconcile`` is
  the decision. Showing the dialog decides nothing — only
  an explicit decision ("It applied" or "It didn't apply")
  resolves, and only through the coordinator's
  ``resolve_reconcile``.
* Silence is no decision: the dialog carries a 60 s timer
  (``core.confirm_gate.DEFAULT_TIMEOUT_S``, the gate's own
  source) that closes it on expiry with outcome "timeout".
  A timeout records nothing — the task stays paused with
  the reconcile record still pending, and the next Review
  pass offers it again. An undecided step is never guessed,
  never defaulted, never auto-answered.
* Neither answer is a default and neither button has focus.
  A decision to re-run or skip an effect is a human decision
  about what happened — no button is pre-selected, and no
  keypress answers it by accident.
* A decision is information, never approval: deciding a
  step applied never approves a step — the step still gates
  normally on resume (every execute call gates
  independently, the two-layer protocol). The reconcile
  dialog carries no allow/deny buttons and no "Always
  allow" affordance.
* Voice never decides: no voice path into
  ``resolve_reconcile`` exists anywhere in this module.
  C-14 keeps the decision channel GUI-only — a spoken
  decision is the same channel problem as a spoken
  approval, so the decision comes through this dialog,
  never through the voice loop.
* Cancel is not a decision: closing the dialog (or its
  timeout) leaves the task paused — it never fails the
  step and never cancels the task. The task is cancelled
  from Activity as usual, never from this dialog.
* Dialog first, resolve second: the Review flow presents
  the dialog from the ledger summary (GUI thread), then
  resolves on a worker thread. The worker may run task
  steps and the funnel gate may pop again on re-run —
  the GUI thread is free throughout, so no deadlock.
"""

from __future__ import annotations


def pending_reconcile_summary(pending):
    """Map a coordinator pending-reconcile record to (tool, text).

    Pure, stdlib-only: the step label, the question verbatim,
    and the reason the world could not be re-read. Nothing
    else is invented — no decision is synthesized here; the
    dialog frames the question, and the human supplies the
    decision. Missing fields degrade to the tool id / empty
    lines, never to a fabricated question.
    """
    pending = pending if isinstance(pending, dict) else {}
    tool = str(pending.get("tool") or "")
    label = str(pending.get("label") or "") or tool
    question = str(pending.get("question") or "")
    reason = str(pending.get("reason") or "")
    lines = [f"Step: {label}"]
    if question:
        lines.append(question)
    if reason:
        lines.append(reason)
    return tool, "\n".join(lines)


def interrupted_pending_reconciles(summaries):
    """Pending-reconcile offers inside ``list_interrupted`` output.

    Pure, stdlib-only. Returns [(task_id, pending-copy)] for
    every summary whose ``pending_reconcile`` is a dict
    carrying a question. Summaries without one, malformed
    entries, and entries without a task id are dropped, never
    offered.
    """
    out = []
    for summary in summaries or []:
        if not isinstance(summary, dict):
            continue
        pending = summary.get("pending_reconcile")
        if not isinstance(pending, dict) or not pending.get("question"):
            continue
        task_id = summary.get("task_id")
        if not task_id:
            continue
        out.append((str(task_id), dict(pending)))
    return out


def build_reconcile_dialog(window, pending):
    """Build the reconcile-decision dialog (no exec — testable).

    ``pending`` is a coordinator pending-reconcile record:
    {"step_index", "tool", "label", "question", "reason"}.
    Returns the dialog; the caller execs it (see
    present_reconcile_dialog) or drives its buttons directly
    in tests. The dialog carries _reconcile_btn_applied,
    _reconcile_btn_not_applied, and _reconcile_decision
    (True/False once a button is clicked, None before) for
    tests. Raises on a build failure (the caller treats that
    as "error", never as a decision).

    Behaviour: "It applied" accepts with decision True;
    "It didn't apply" accepts with decision False; closing
    the dialog rejects with no decision. Neither button is a
    default and neither has focus — no keypress answers it
    by accident. The safe default is no decision at all.
    """
    from PySide6.QtWidgets import (
        QHBoxLayout, QLabel, QPushButton,
    )

    from .design_system.dialogs import HudDialog
    from .style import TEXT_MUTED

    pending = pending if isinstance(pending, dict) else {}
    question = str(pending.get("question") or "").strip()
    reason = str(pending.get("reason") or "").strip()
    label = str(pending.get("label") or pending.get("tool") or "")
    tool = str(pending.get("tool") or "")

    dlg = HudDialog(window, title="VYUK — did that step apply?")
    layout = dlg.content_layout()

    headline = QLabel(
        question or "Did that step apply before the crash?")
    headline.setStyleSheet("font-size: 16px; font-weight: 600;")
    headline.setWordWrap(True)
    layout.addWidget(headline)
    about = QLabel(
        f"Step: {label}"
        + (f" ({tool})" if tool and tool != label else "")
        + " — the task is paused until you decide. "
        + (reason + " " if reason else "")
        + "“It applied” records the step as done and the task "
        "continues from the next step. “It didn't apply” runs "
        "the step again from scratch. Deciding never approves "
        "a step — it still asks for approval on its own when "
        "it needs one.")
    about.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
    about.setWordWrap(True)
    layout.addWidget(about)

    row = QHBoxLayout()
    row.addStretch(1)
    btn_applied = QPushButton("It applied")
    btn_not_applied = QPushButton("It didn't apply")
    # Neither button is a default and neither has focus: a
    # decision to re-run or skip an effect is never made by
    # an accidental keypress.

    def _on_applied():
        dlg._reconcile_decision = True
        dlg.accept()

    def _on_not_applied():
        dlg._reconcile_decision = False
        dlg.accept()

    btn_applied.clicked.connect(_on_applied)
    btn_not_applied.clicked.connect(_on_not_applied)
    row.addWidget(btn_applied)
    row.addWidget(btn_not_applied)
    layout.addLayout(row)

    dlg._reconcile_btn_applied = btn_applied
    dlg._reconcile_btn_not_applied = btn_not_applied
    dlg._reconcile_decision = None
    dlg._reconcile_timed_out = False
    return dlg


def present_reconcile_dialog(window, pending, *, timeout_s=None):
    """Show the reconcile dialog for one pending record.

    GUI thread only. Returns {"applied": True|False|None,
    "outcome": "decided" | "timeout" | "dismissed" | "error"}.
    "decided" only for an explicit decision ("It applied"
    or "It didn't apply"); "timeout" when the 60 s silence
    timer fires; "dismissed" for a window close or an
    accepted-but-undecided dialog; "error" for any dialog
    failure. Anything but "decided" records nothing — the
    task stays paused with the reconcile record still
    pending, and the next Review pass offers it again.

    This function presents ONLY — it never calls
    ``resolve_reconcile``. The caller resolves the returned
    decision (on a worker thread, via
    :func:`resolve_reconcile_answer`).
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    if timeout_s is None:
        try:
            from voice_assistant.core.gui_facade import confirm_gate
            timeout_s = float(confirm_gate.DEFAULT_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — one timeout source
            timeout_s = 60.0
    try:
        dlg = build_reconcile_dialog(window, pending)
    except Exception:  # noqa: BLE001 — a broken popup
        return {"applied": None, "outcome": "error"}  # decides nothing.

    def _on_timeout():
        # The timer firing means the human did not decide at
        # all — recorded as "timeout", never as a dismissal
        # and never as a decision, so the caller can tell the
        # two apart (the task stays paused either way).
        dlg._reconcile_timed_out = True
        dlg.reject()

    timer = QTimer(dlg)
    timer.setSingleShot(True)
    timer.timeout.connect(_on_timeout)
    try:
        timer.start(int(float(timeout_s) * 1000))
    except Exception:  # noqa: BLE001 — a timer failure must
        pass  # not break the dialog itself.
    try:
        accepted = (dlg.exec() == QDialog.DialogCode.Accepted)
    except Exception:  # noqa: BLE001 — a broken popup
        accepted = False  # decides nothing.
    finally:
        try:
            timer.stop()
        except Exception:  # noqa: BLE001 — teardown never raises.
            pass
    if getattr(dlg, "_reconcile_timed_out", False):
        return {"applied": None, "outcome": "timeout"}
    if not accepted:
        return {"applied": None, "outcome": "dismissed"}
    decision = getattr(dlg, "_reconcile_decision", None)
    if not isinstance(decision, bool):
        return {"applied": None, "outcome": "dismissed"}
    return {"applied": decision, "outcome": "decided"}


def resolve_reconcile_answer(task_id, applied, *, live_coordinator=None,
                             ledger=None):
    """Resolve one pending reconcile decision, worker-safe (no Qt).

    Live first: when the live coordinator holds the task, it is
    the truth — a pending reconcile resolves through it, and a
    live-held task with no pending reconcile means a stale
    decision, which resolves nothing (None) rather than
    rebuilding a live task from the ledger behind its back.
    A non-bool decision resolves nothing anywhere — it
    returns None without touching the task.

    Otherwise the rebuild path: a fresh coordinator bound to
    the ledger resumes the interrupted record (a rebuilt
    paused task re-stops at its pending reconcile — the
    resume re-reconciles the same way, so a rebuilt task
    whose world is now readable settles on its own), then
    the decision resolves it. When the resume itself settles
    the task (the step no longer pending), the resume receipt
    is returned instead — the task did change state, and the
    caller refreshes from it. None only when nothing was
    found, nothing was pending, or the decision was not a
    bool.
    """
    task_id = str(task_id)
    if not isinstance(applied, bool):
        return None
    if live_coordinator is not None:
        try:
            held = live_coordinator.get_task(task_id)
        except Exception:  # noqa: BLE001 — a broken live lookup
            return None  # is not a licence to rebuild over it.
        if held is not None:
            if live_coordinator.pending_reconcile(task_id) is None:
                return None
            return live_coordinator.resolve_reconcile(task_id, applied)
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
    if coordinator.pending_reconcile(task_id) is None:
        return receipt
    return coordinator.resolve_reconcile(task_id, applied)


def review_reconcile_queue(window, queue, *, live_coordinator=None,
                           ledger=None, refresh=None, poll_ms=500):
    """Offer each pending reconcile decision in turn, then refresh.

    GUI thread only for the dialog half: each queue head gets
    :func:`present_reconcile_dialog` (modal, so the user
    decides before anything else runs), then its outcome
    lands on a worker thread: "decided" resolves via
    :func:`resolve_reconcile_answer` (which may run task steps
    and re-pop the funnel gate — the GUI thread is free
    throughout, so no deadlock); "timeout" / "dismissed" /
    "error" resolve nothing — the offer stays live for the
    next Review pass instead of being misread as a decision.
    A poll timer advances the queue only when the worker
    lands; when the queue empties, ``refresh`` (the Activity
    refresh) runs. A stale offer resolves nothing and the
    refresh simply shows the new state.
    ``window`` need only carry the HudDialog parent (the
    MainWindow does, via its dialog builders).
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
        outcome = present_reconcile_dialog(window, pending)
    except Exception:  # noqa: BLE001 — a broken Review dialog
        outcome = {"applied": None, "outcome": "error"}  # decides nothing.
    done = threading.Event()

    def _work():
        try:
            if outcome.get("outcome") == "decided":
                resolve_reconcile_answer(
                    task_id, outcome.get("applied"),
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
            review_reconcile_queue(
                window, queue[1:], live_coordinator=live_coordinator,
                ledger=ledger, refresh=refresh, poll_ms=poll_ms)

    timer.timeout.connect(_tick)
    timer.start(int(poll_ms))

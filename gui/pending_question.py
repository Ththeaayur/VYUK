"""GUI presentation for coordinator clarifying questions (B15).

Batch 15 gave the coordinator a question pause
(``pending_question`` / ``resolve_question``) but no GUI code
read it, so a paused task's question was invisible. This module
is that GUI layer. It builds its own dialog (a question with
option buttons + a free-text field is a different shape from
the S-1 confirm dialog, so the S-1 builder cannot carry it),
reusing the HudDialog base (chamfer + role line + own sheet)
like the plan-approval dialog. No new dialog contract, no new
wording family, no Home change.

Boundaries, stated plainly:

* The dialog is the presentation; ``resolve_question`` is the
  decision. Showing the dialog answers nothing — only an
  explicit answer (an option click, or the Answer button with
  non-empty text) resolves, and only through the
  coordinator's ``resolve_question``.
* Silence is no answer: the dialog carries a 60 s timer
  (``core.confirm_gate.DEFAULT_TIMEOUT_S``, the gate's own
  source) that closes it on expiry with outcome "timeout".
  A timeout records nothing — the task stays paused with
  the question still pending, and the next Review pass
  offers it again. An unanswered question is never guessed,
  never defaulted, never auto-answered.
* An answer is information, never approval: answering a
  question never approves a step — the step still gates
  normally on resume (every execute call gates
  independently, the two-layer protocol). The question
  dialog carries no allow/deny buttons and no "Always
  allow" affordance.
* Voice never answers: no voice path into
  ``resolve_question`` exists anywhere in this module.
  C-14 keeps the answer channel GUI-only — a spoken
  answer to a clarifying question is the same channel
  problem as a spoken approval, so the answer comes
  through this dialog (or the typed chat surface that
  calls ``resolve_question`` directly), never through
  the voice loop.
* Cancel is not an answer: closing the dialog (or its
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


def pending_question_summary(pending):
    """Map a coordinator pending-question record to (tool, text).

    Pure, stdlib-only: the step label, the question verbatim,
    and the declared options. Nothing else is invented — no
    answer is synthesized here; the dialog frames the
    question, and the human supplies the answer.
    Missing fields degrade to the tool id / empty lines, never
    to a fabricated question.
    """
    pending = pending if isinstance(pending, dict) else {}
    tool = str(pending.get("tool") or "")
    label = str(pending.get("label") or "") or tool
    question = str(pending.get("question") or "")
    options = [str(o or "") for o in (pending.get("options") or [])
               if str(o or "").strip()]
    lines = [f"Step: {label}"]
    if question:
        lines.append(question)
    if options:
        lines.append("Options: " + ", ".join(options))
    return tool, "\n".join(lines)


def interrupted_pending_questions(summaries):
    """Pending-question offers inside ``list_interrupted`` output.

    Pure, stdlib-only. Returns [(task_id, pending-copy)] for
    every summary whose ``pending_question`` is a dict carrying
    a question. Summaries without one, malformed entries, and
    entries without a task id are dropped, never offered.
    """
    out = []
    for summary in summaries or []:
        if not isinstance(summary, dict):
            continue
        pending = summary.get("pending_question")
        if not isinstance(pending, dict) or not pending.get("question"):
            continue
        task_id = summary.get("task_id")
        if not task_id:
            continue
        out.append((str(task_id), dict(pending)))
    return out


def build_question_dialog(window, pending):
    """Build the clarifying-question dialog (no exec — testable).

    ``pending`` is a coordinator pending-question record:
    {"step_index", "tool", "label", "question", "options",
    "answer_key"}. Returns the dialog; the caller execs it
    (see present_question_dialog) or drives its buttons
    directly in tests. The dialog carries _question_input
    (the free-text field), _question_option_buttons (one per
    declared option) and _question_btn_answer for tests.
    Raises on a build failure (the caller treats that as
    "error", never as an answer).

    Behaviour: an option button fills the input with that
    option and accepts; the Answer button accepts only with
    non-empty input (an empty click does nothing — the
    dialog stays open, so Enter on an empty field cannot
    invent an answer); closing the dialog rejects with no
    answer. The safe default is no answer at all.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    )

    from .design_system.dialogs import HudDialog
    from .style import TEXT_MUTED

    pending = pending if isinstance(pending, dict) else {}
    question = str(pending.get("question") or "").strip()
    label = str(pending.get("label") or pending.get("tool") or "")
    tool = str(pending.get("tool") or "")
    options = [str(o or "") for o in (pending.get("options") or [])
               if str(o or "").strip()]

    dlg = HudDialog(window, title="VYUK — a question before this step")
    layout = dlg.content_layout()

    headline = QLabel(question or "A step needs an answer before it runs.")
    headline.setStyleSheet("font-size: 16px; font-weight: 600;")
    headline.setWordWrap(True)
    layout.addWidget(headline)
    about = QLabel(
        f"Step: {label}"
        + (f" ({tool})" if tool and tool != label else "")
        + " — the task is paused until you answer. An answer "
        "is information, never approval: it never approves a "
        "step, and the step still asks for approval on its own "
        "when it needs one.")
    about.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
    about.setWordWrap(True)
    layout.addWidget(about)

    edit = QLineEdit()
    edit.setPlaceholderText("Type your answer…")
    layout.addWidget(edit)

    option_buttons = []
    if options:
        opt_label = QLabel("Or pick one:")
        opt_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(opt_label)
        opt_layout = QVBoxLayout()
        opt_layout.setContentsMargins(0, 0, 0, 0)
        opt_layout.setSpacing(6)
        for option in options:
            btn = QPushButton(option)
            btn.clicked.connect(
                lambda _checked=False, _opt=option: (
                    edit.setText(_opt), dlg.accept()))
            opt_layout.addWidget(btn)
            option_buttons.append(btn)
        layout.addLayout(opt_layout)

    row = QHBoxLayout()
    row.addStretch(1)
    btn_answer = QPushButton("Answer")
    btn_answer.setDefault(True)

    def _on_answer():
        # An empty answer resolves nothing — the dialog stays
        # open, so Enter on an empty field cannot invent one.
        if edit.text().strip():
            dlg.accept()

    btn_answer.clicked.connect(_on_answer)
    row.addWidget(btn_answer)
    layout.addLayout(row)

    dlg._question_input = edit
    dlg._question_option_buttons = option_buttons
    dlg._question_btn_answer = btn_answer
    dlg._question_timed_out = False
    return dlg


def present_question_dialog(window, pending, *, timeout_s=None):
    """Show the question dialog for one pending record.

    GUI thread only. Returns {"answer": str|None, "outcome":
    "answered" | "timeout" | "dismissed" | "error"}.
    "answered" only for an explicit answer (an option click,
    or the Answer button with non-empty text); "timeout"
    when the 60 s silence timer fires; "dismissed" for a
    window close or an accepted-but-empty answer; "error"
    for any dialog failure. Anything but "answered" records
    nothing — the task stays paused with the question still
    pending, and the next Review pass offers it again.

    This function presents ONLY — it never calls
    ``resolve_question``. The caller resolves the returned
    answer (on a worker thread, via
    :func:`resolve_question_answer`).
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
        dlg = build_question_dialog(window, pending)
    except Exception:  # noqa: BLE001 — a broken popup
        return {"answer": None, "outcome": "error"}  # answers nothing.

    def _on_timeout():
        # The timer firing means the human did not answer at
        # all — recorded as "timeout", never as a dismissal
        # and never as an answer, so the caller can tell the
        # two apart (the task stays paused either way).
        dlg._question_timed_out = True
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
        accepted = False  # answers nothing.
    finally:
        try:
            timer.stop()
        except Exception:  # noqa: BLE001 — teardown never raises.
            pass
    if getattr(dlg, "_question_timed_out", False):
        return {"answer": None, "outcome": "timeout"}
    if not accepted:
        return {"answer": None, "outcome": "dismissed"}
    answer = dlg._question_input.text().strip()
    if not answer:
        return {"answer": None, "outcome": "dismissed"}
    return {"answer": answer, "outcome": "answered"}


def resolve_question_answer(task_id, answer, *, live_coordinator=None,
                            ledger=None):
    """Resolve one pending question, worker-safe (no Qt).

    Live first: when the live coordinator holds the task, it is
    the truth — a pending question resolves through it, and a
    live-held task with no pending question means a stale
    answer, which resolves nothing (None) rather than
    rebuilding a live task from the ledger behind its back.
    An empty answer resolves nothing anywhere — it returns
    None without touching the task.

    Otherwise the rebuild path: a fresh coordinator bound to
    the ledger resumes the interrupted record (a rebuilt
    pending task re-stops at its pending step — the recorded
    answer, if any, is honoured, so a rebuilt answered task
    never re-asks), then the answer resolves it. When the
    resume itself settles the task (the step no longer
    pending), the resume receipt is returned instead — the
    task did change state, and the caller refreshes from it.
    None only when nothing was found, nothing was pending,
    or the answer was empty.
    """
    task_id = str(task_id)
    if not isinstance(answer, str) or not answer.strip():
        return None
    if live_coordinator is not None:
        try:
            held = live_coordinator.get_task(task_id)
        except Exception:  # noqa: BLE001 — a broken live lookup
            return None  # is not a licence to rebuild over it.
        if held is not None:
            if live_coordinator.pending_question(task_id) is None:
                return None
            return live_coordinator.resolve_question(
                task_id, answer)
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
    if coordinator.pending_question(task_id) is None:
        return receipt
    return coordinator.resolve_question(task_id, answer)


def review_question_queue(window, queue, *, live_coordinator=None,
                          ledger=None, refresh=None, poll_ms=500):
    """Offer each pending question in turn, then refresh.

    GUI thread only for the dialog half: each queue head gets
    :func:`present_question_dialog` (modal, so the user
    answers before anything else runs), then its outcome
    lands on a worker thread: "answered" resolves via
    :func:`resolve_question_answer` (which may run task steps
    and re-pop the funnel gate — the GUI thread is free
    throughout, so no deadlock); "timeout" / "dismissed" /
    "error" resolve nothing — the offer stays live for the
    next Review pass instead of being misread as an answer.
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
        outcome = present_question_dialog(window, pending)
    except Exception:  # noqa: BLE001 — a broken Review dialog
        outcome = {"answer": None, "outcome": "error"}  # decides nothing.
    done = threading.Event()

    def _work():
        try:
            if outcome.get("outcome") == "answered":
                resolve_question_answer(
                    task_id, outcome.get("answer"),
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
            review_question_queue(
                window, queue[1:], live_coordinator=live_coordinator,
                ledger=ledger, refresh=refresh, poll_ms=poll_ms)

    timer.timeout.connect(_tick)
    timer.start(int(poll_ms))

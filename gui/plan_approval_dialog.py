"""GUI presentation for approve-plan-once (Fix 8, 2026-11-06).

The coordinator's plan hook (core/plan_approval.preview) splits
a task's steps into the ones a plan approval would cover and
the ones that will still ask one by one. This module is that
dialog: one plain-language list of the covered steps, one
muted list of the still-individual steps with their reasons,
and two buttons. It reuses the HudDialog base (chamfer + role
line + own sheet) like the S-1 confirm dialog, but it is a
different shape — a plan list, not a single action — so it
builds its own content instead of reusing the S-1 builder.

Boundaries, stated plainly:

* The dialog is the presentation; core/plan_approval.
  approve_plan is the decision, and it re-validates every
  index (an ineligible index smuggled in by any caller is
  refused, never recorded). Showing the dialog approves
  nothing.
* Safe default: "Ask me each time" is the default button
  and holds initial focus, so Enter denies the plan (mirrors
  the S-1 rule: Enter denies, never allows). Close reads as
  "ask each time"; the silence timer reads as a TIMEOUT, not
  as "ask each time" — the timer firing means the human did
  not answer at all, and core/plan_approval tells the two
  apart so the coordinator can pause the task instead of
  running step-by-step asks into an empty room. A plan
  approval is never the result of doing nothing.
* The approvable steps carry checkboxes, all checked by
  default: the human may uncheck a step they would rather
  be asked about individually. Approving returns only the
  checked indices.
* Voice never approves a plan: no voice path into this
  module exists, and the hook is called only from the typed
  turn's GUI-thread path (gui/window_session_typed).
* The dialog shows the masked args summary the confirm
  popup uses (core/confirm_gate.summarize_args via the
  preview) — no raw args reach this layer, so nothing
  unmasked can leak into the list.
"""

from __future__ import annotations


def build_plan_approval_dialog(window, task_title, preview):
    """Build the plan-approval dialog (no exec — testable).

    ``preview`` is core/plan_approval.preview(task): dict
    with "approvable" ([{index, tool, label, summary}]),
    "still_individual" ([{index, tool, label, summary,
    reason}]) and "no_ask" (count). Returns the dialog;
    the caller execs it (see present_plan_approval_dialog)
    or drives its buttons directly in tests. The dialog
    carries _plan_list (the checkable approvable list),
    _plan_btn_approve and _plan_btn_ask_each for tests.
    Raises on a build failure (the caller treats that as
    "ask each time", never as an approval).
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
        QPushButton,
    )

    from .design_system.dialogs import HudDialog
    from .style import TEXT_MUTED

    approvable = list((preview or {}).get("approvable") or [])
    still = list((preview or {}).get("still_individual") or [])
    no_ask = int((preview or {}).get("no_ask") or 0)
    title = str(task_title or "task").strip() or "task"

    dlg = HudDialog(
        window, title="VYUK — approve this plan once?")
    layout = dlg.content_layout()

    headline = QLabel(
        f"Approve the safe steps of “{title}” once?")
    headline.setStyleSheet("font-size: 16px; font-weight: 600;")
    headline.setWordWrap(True)
    layout.addWidget(headline)
    about = QLabel(
        "The steps below run without asking again once you "
        "approve them here. The rest still ask one by one, "
        "and deleting, sending and purchases always ask, "
        "every time.")
    about.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
    about.setWordWrap(True)
    layout.addWidget(about)

    if approvable:
        run_label = QLabel("Runs without asking again:")
        run_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(run_label)
        plan_list = QListWidget()
        plan_list.setMaximumHeight(160)
        for entry in approvable:
            label = str(entry.get("label") or entry.get("tool") or "")
            summary = str(entry.get("summary") or "")
            text = label
            if summary:
                text = f"{label} — {summary}"
            item = QListWidgetItem(text)
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole,
                         int(entry.get("index")))
            plan_list.addItem(item)
        layout.addWidget(plan_list)
    else:
        plan_list = None
        none_label = QLabel(
            "No step of this plan can be approved once — "
            "every gated step asks individually.")
        none_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        none_label.setWordWrap(True)
        layout.addWidget(none_label)

    if still:
        still_label = QLabel("Still asks one by one:")
        still_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(still_label)
        for entry in still:
            label = str(entry.get("label") or entry.get("tool") or "")
            reason = str(entry.get("reason") or "asks individually")
            line = QLabel(f"{label} — {reason}")
            line.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
            line.setWordWrap(True)
            layout.addWidget(line)
    if no_ask:
        more = QLabel(
            f"{no_ask} more step(s) never ask at all.")
        more.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        more.setWordWrap(True)
        layout.addWidget(more)

    note = QLabel(
        "This approval covers only this task, only these "
        "exact steps, and only until the task ends. It is not "
        "a standing permission — a new task asks again.")
    note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
    note.setWordWrap(True)
    layout.addWidget(note)

    row = QHBoxLayout()
    row.addStretch(1)
    btn_ask = QPushButton("Ask me each time")
    btn_approve = QPushButton("Approve these steps")
    btn_approve.setObjectName("primaryBtn")
    row.addWidget(btn_ask)
    row.addWidget(btn_approve)
    layout.addLayout(row)
    # Safe default, mirroring S-1: the "ask each time" button
    # is the default and holds initial focus — Enter asks
    # each time, never approves.
    btn_ask.setDefault(True)
    btn_ask.setFocus()
    btn_ask.clicked.connect(dlg.reject)
    btn_approve.clicked.connect(dlg.accept)

    dlg._plan_list = plan_list
    dlg._plan_btn_approve = btn_approve
    dlg._plan_btn_ask_each = btn_ask
    return dlg


def approved_indices(dlg):
    """The checked step indices on an approved dialog (the
    approvable entries the human left checked). Empty when
    the dialog has no list (nothing was approvable)."""
    from PySide6.QtCore import Qt

    plan_list = getattr(dlg, "_plan_list", None)
    if plan_list is None:
        return []
    out = []
    for row in range(plan_list.count()):
        item = plan_list.item(row)
        if item is None:
            continue
        if item.checkState() == Qt.CheckState.Checked:
            try:
                out.append(int(item.data(Qt.ItemDataRole.UserRole)))
            except (TypeError, ValueError):
                continue
    return out


def present_plan_approval_dialog(window, task_title, preview,
                                 timeout_s=None):
    """Show the plan-approval dialog, GUI thread only.

    Returns the list of approved step indices (possibly
    empty), or [] for "Ask me each time", window close, the
    silence timeout, or any dialog failure. A broken dialog
    never approves — it reads as "ask each time", exactly
    like a denial. This function presents ONLY — the caller
    records the returned indices through
    core/plan_approval.approve_plan (which re-validates
    them); presenting never approves by itself.

    The [] contract is kept for the existing callers and
    pins; callers that must tell a timeout from an explicit
    refusal use present_plan_approval_dialog_ex instead.
    """
    indices, _outcome = present_plan_approval_dialog_ex(
        window, task_title, preview, timeout_s=timeout_s)
    return indices


def present_plan_approval_dialog_ex(window, task_title, preview,
                                    timeout_s=None):
    """Show the plan-approval dialog, GUI thread only — and
    say WHY it ended.

    Returns (indices, outcome). ``indices`` is the approved
    step list, exactly as present_plan_approval_dialog
    returns it; ``outcome`` is one of:

    * "approved" — the human pressed "Approve these steps"
      with at least one step checked;
    * "ask_each_time" — the human pressed "Ask me each
      time", closed the dialog, or pressed approve with
      nothing checked;
    * "timeout" — the silence timer fired before any
      answer. This is NOT "ask each time": nobody was
      there, and the caller must not run step-by-step asks
      into an empty room on the strength of it;
    * "error" — the dialog could not be built or run.

    A timeout never approves — it reads as ([], "timeout"),
    exactly like a denial reads as ([], "ask_each_time").
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
        dlg = build_plan_approval_dialog(window, task_title, preview)
    except Exception:  # noqa: BLE001 — a broken popup asks per step
        return [], "error"
    timed_out = {"fired": False}

    def _on_timeout():
        # The silence timer is the ONLY path that reads as a
        # timeout: a human press rejects through the buttons,
        # never through here.
        timed_out["fired"] = True
        dlg.reject()

    timer = QTimer(dlg)
    timer.setSingleShot(True)
    timer.timeout.connect(_on_timeout)
    try:
        timer.start(int(float(timeout_s) * 1000))
    except Exception:  # noqa: BLE001 — a timer failure must not
        pass  # break the dialog itself.
    try:
        accepted = (dlg.exec() == QDialog.DialogCode.Accepted)
    except Exception:  # noqa: BLE001 — a broken popup asks per step
        accepted = False
    finally:
        try:
            timer.stop()
        except Exception:  # noqa: BLE001 — teardown never raises
            pass
    if timed_out["fired"]:
        return [], "timeout"
    if not accepted:
        return [], "ask_each_time"
    try:
        indices = approved_indices(dlg)
    except Exception:  # noqa: BLE001 — undecidable: ask per step
        return [], "ask_each_time"
    if not indices:
        return [], "ask_each_time"
    return indices, "approved"

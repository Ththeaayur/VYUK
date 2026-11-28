"""Single task-state component (UI Slice 3, complexity doc CX-05).

Every persistent task surface renders through this module — the
Activity page's In progress / History rows are the surfaces, and
they call :func:`render_task` + :func:`add_task_row`; no tab keeps
its own state text. Two boundaries, stated plainly:

* Chat receipt text (``core/brain/task_coordinator.format_receipt``) is
  transient coordinator-owned text, not a persistent task surface;
  it is NOT rewired here (the coordinator is a read-only input).
  It already reports the coordinator's terminal verdict with
  counts, steps, remaining work and reason — it never claims a
  goal is done from a single handler return.
* Saved-plan rows (``_plan_row``) are a different entity (plans,
  done/total) and are untouched; this component renders
  coordinator ledger tasks only.

PURE MAPPING, LAB-TESTABLE: this module imports only the standard
library at top level, so the mapping executes for real in the lab.
The Qt row widget is built by :func:`build_task_row_widget`, which
imports PySide6 lazily inside the function; the widget itself is
verified by AST (the lab cannot drive real widgets).

STATE SEMANTICS (rendered, never redefined — the coordinator and
the ledger own the truth; this module only maps it):

* ``Done`` ⟺ the stored ledger state is ``"completed"`` — and
  NOTHING else. A single successful tool call, a latest outcome
  of ``success``, or a handler return never renders Done. The
  coordinator sets ``completed`` only when every declared step
  finished with status ``success`` or ``skipped`` AND the
  verification phase (when the task carries expectations)
  judged no expectation ``"not-met"``; a not-met expectation
  fails the task instead, so Done never hides a failed
  verification. (Honest limit, updated: Done means "all
  declared steps completed per the coordinator, with every
  declared expectation either met, unverifiable, or — for a
  skipped step — never checkable"; the verification verdicts
  themselves display in the details text. A task with no
  expectations runs no phase, and Done for it means exactly
  what it meant before.)
* ``Partial`` vs ``Failed`` are distinct: a cancelled task with
  at least one successful step is Partial (work stands, the goal
  was stopped early); a cancelled task with no successful step,
  and any failed task, is Failed.
* ``Needs approval`` ⟺ EITHER the task is running AND its next
  uncompleted step is the tool of a LIVE pending confirmation
  (``core/confirm_gate.pending_request()``) — the pending
  registry is the source of truth that a dialog is actually
  open; the positional match attributes it to the task — OR
  the task is paused AND its stored record carries a
  coordinator pending approval (§7 consumers batch 7: an
  engine confirm stop paused the task; the stored record is
  the source of truth, no live request needed). With neither,
  no row claims Needs approval — a task merely positioned at
  a gated step still shows Running. The approve/deny path
  itself is the existing confirm dialog (see
  gui/pending_approval.py); this component only renders its
  state, never rebuilds it.
* ``Verifying`` ⟺ the stored ledger state is ``"verifying"``.
  This state exists because the coordinator now HAS a
  verification phase (wiring item 3, v6.0.0): the phase writes
  ``"verifying"`` to the ledger while it checks the task's
  expectations, so the state is shown only when verification
  is actually running — the phase itself is the real verifier
  producer the D9 producer-or-hide rule requires. The legacy
  ``verifying: True`` boolean marker is STILL ignored (it
  never displays): only the stored state counts, so a record
  cannot claim Verifying without the phase having run. The
  detail text shows skipped steps so a completed-with-gaps
  task never hides its gaps, and verification verdicts when
  the record carries them.
* ``paused`` is a hold inside Running, not a separate state:
  it renders as Running with an explicit "paused" qualifier.
* A record whose stored state is not one of the coordinator's
  six states renders an honest "unknown state" row, never a
  forced state. A live-state record carrying a failure outcome
  (a crashed write: the coordinator halts on such outcomes, so
  the task cannot proceed) renders Failed.

Progress arithmetic mirrors ``core/brain/task_ledger._advance_index``:
outcomes are walked in order, advancing past ``success`` /
``skipped`` only; any other outcome stops the walk. Counts
mirror ``Task.receipt``: completed = success outcomes, failed =
non-success non-skipped outcomes, skipped = skipped outcomes.
"""

from __future__ import annotations

import json

DISPLAY_STATES = (
    "Queued",
    "Running",
    "Verifying",
    "Needs approval",
    "Done",
    "Partial",
    "Failed",
)

_MARKS = {"success": "✓", "failure": "✗", "refused": "⊘", "skipped": "⊘"}
_DONE_OK = ("success", "skipped")


def _as_str(value, default="") -> str:
    if value is None:
        return default
    return str(value)


def _advance(outcomes, n_steps: int) -> int:
    """Mirror of core/brain/task_ledger._advance_index (see docstring)."""
    index = 0
    for outcome in outcomes or []:
        if index >= n_steps:
            break
        if _as_str(outcome.get("status", "")) in _DONE_OK:
            index += 1
        else:
            break
    return index


def _counts(outcomes):
    """(success, failed, skipped) counts, mirroring Task.receipt."""
    success = sum(1 for o in outcomes
                  if _as_str(o.get("status", "")) == "success")
    skipped = sum(1 for o in outcomes
                  if _as_str(o.get("status", "")) == "skipped")
    failed = sum(1 for o in outcomes
                 if _as_str(o.get("status", "")) not in _DONE_OK)
    return success, failed, skipped


def mission_state_for_record(record, live_count=1):
    """One ledger record -> the MissionRail's state (UI Phase 1).

    Live only: a running / paused / verifying record maps to
    {"goal", "step", "steps_total", "phase", "permission",
    "live_count"}; a finished record (completed / failed /
    cancelled) maps to None — the rail shows its honest idle
    state instead. ``step`` is the advance index (the count of
    leading success outcomes, capped at the step total) — the
    same number the rail's SegmentBar labels "Step n of m".
    ``permission`` is "required" while the record carries a
    pending approval, "none" otherwise. ``live_count`` is the
    number of live missions in the same read: when it is above
    one, the rail shows the newest record and names the count
    ("2 missions running — showing the newest"); the mapper
    still returns this record.
    """
    if not isinstance(record, dict):
        return None
    state = _as_str(record.get("state", "")).strip().lower()
    if state not in ("running", "paused", "verifying"):
        return None
    steps = record.get("steps") or []
    outcomes = record.get("outcomes") or []
    total = len(steps)
    step = _advance(outcomes, total)
    pending = record.get("pending_approval")
    return {
        "goal": _as_str(record.get("title")
                        or record.get("task_id") or "Mission"),
        "step": step,
        "steps_total": total,
        "phase": state,
        "permission": "required" if pending else "none",
        "live_count": int(live_count or 0),
    }


def _args_summary(args) -> str:
    """One short line for a step's (already sanitized) args."""
    if not isinstance(args, dict) or not args:
        return ""
    if args.get("_truncated"):
        return "(arguments truncated in the stored record)"
    try:
        text = json.dumps(args, default=str)
    except Exception:  # noqa: BLE001 — display only, never breaks
        return ""
    if len(text) > 300:
        text = text[:297] + "…"
    return text


def _step_text(step, outcome=None) -> str:
    """One detail line for a step + its outcome (or lack of one)."""
    label = _as_str(step.get("label", "")) or _as_str(step.get("tool", ""))
    tool = _as_str(step.get("tool", ""))
    if outcome is None:
        return f"  ○ {label} — not run"
    status = _as_str(outcome.get("status", ""))
    mark = _MARKS.get(status, "○")
    line = f"  {mark} {label} — {status}"
    kind = _as_str(outcome.get("kind", ""))
    if kind:
        line += f" ({kind})"
    message = _as_str(outcome.get("message", ""))
    if message:
        line += f": {message}"
    if tool and tool != label:
        line += f" [{tool}]"
    return line


def latest_second_opinion_off_line(records) -> str:
    """The OFF line of the most recent coordinator task record.

    B24 (second-opinion OFF surfacing): the home briefing
    carries the same OFF line as the receipt/ledger/Activity
    — the most recent coordinator task's line. A latest task
    with no OFF line supersedes an older one (its check ran,
    or it never needed one), so the briefing never shows a
    stale OFF line after the check is back on. Goal records
    (kind "goal" — run_goal goals, which have no coordinator
    check) never count: the latest coordinator task decides.
    "" when no coordinator task record exists or the latest
    one carries no line. Pure — no Qt, no ledger read — so
    the briefing and the tests share this one decision.
    """
    if not isinstance(records, list):
        return ""
    latest = None
    latest_key = None
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("kind", "") or "") == "goal":
            continue
        key = str(record.get("updated_at", "") or "")
        if latest is None or key >= latest_key:
            latest = record
            latest_key = key
    if latest is None:
        return ""
    off = latest.get("second_opinion_off")
    if not isinstance(off, dict):
        return ""
    line = off.get("line", "")
    return line if isinstance(line, str) and line.strip() else ""


def render_task(record, *, pending_tool=None, pending_summary=None):
    """Map one ledger record (v1 shape) to its display rendering.

    Returns a dict with: state (one of DISPLAY_STATES, or None
    for an unrecognized stored state), title, action, rationale,
    progress, details_text, updated_at, task_id, next_tool,
    paused. ``pending_tool``/``pending_summary`` come from the
    live confirm-gate pending request (or None when there is
    none); see the module docstring for the Needs approval rule.
    """
    record = record if isinstance(record, dict) else {}
    title = (_as_str(record.get("title", ""))
             or _as_str(record.get("classification", ""))
             or _as_str(record.get("task_id", ""))
             or "Untitled task")
    state_raw = _as_str(record.get("state", ""))
    steps = record.get("steps") or []
    if not isinstance(steps, list):
        steps = []
    outcomes = record.get("outcomes") or []
    if not isinstance(outcomes, list):
        outcomes = []
    outcomes = [o for o in outcomes if isinstance(o, dict)]
    total = len(steps)
    index = _advance(outcomes, total)
    success, failed, skipped = _counts(outcomes)
    failed_outcome = next(
        (o for o in outcomes
         if _as_str(o.get("status", "")) not in _DONE_OK), None)
    reason = _as_str(record.get("reason", ""))
    error_kind = _as_str(record.get("error_kind", ""))
    turn_text = _as_str(record.get("turn_text", ""))
    updated_at = _as_str(record.get("updated_at", ""))
    task_id = _as_str(record.get("task_id", ""))
    next_step = steps[index] if index < total else None
    next_tool = _as_str(next_step.get("tool", "")) if next_step else None
    next_label = (_as_str(next_step.get("label", "")) if next_step else "")
    if not next_label:
        next_label = next_tool or ""
    progress = f"{index}/{total} steps done" if total else "no steps defined"

    # -- state decision, in documented precedence order ------------
    state = None
    paused = False
    # §7 consumers batch 7: a paused task carrying a coordinator
    # pending approval renders Needs approval from its stored
    # record alone (the pause wrote it to the ledger) — no live
    # gate request needed, and no positional match: the record
    # names its own pending step. UI Phase 2 (P2-02): a
    # VERIFYING record carries one too (the task resumed after
    # a crash and died mid-verification with its pending step
    # still open) — the Review queue offers it, so the row
    # must render Needs approval as well, or the badge and the
    # page disagree. A running record is excluded on purpose:
    # its approval surface is the live gate hint, not the
    # stored record.
    coord_pending = None
    if state_raw in ("paused", "verifying"):
        _pend = record.get("pending_approval")
        if isinstance(_pend, dict) and _as_str(_pend.get("tool", "")):
            coord_pending = _pend
    # B15: a paused task carrying a coordinator pending
    # question renders Needs an answer from its stored
    # record alone (the pause wrote it to the ledger) — no
    # live gate request needed, and no positional match:
    # the record names its own pending step. A question is
    # not an approval: the state names the answer it waits
    # for, never "Needs approval".
    coord_question = None
    if state_raw == "paused":
        _pq = record.get("pending_question")
        if isinstance(_pq, dict) and _as_str(_pq.get("question", "")):
            coord_question = _pq
    # B21 (crash-safe re-run): a paused task carrying a
    # coordinator pending reconcile renders Needs a decision
    # from its stored record alone (the pause wrote it to
    # the ledger) — no live gate request needed, and no
    # positional match: the record names its own pending
    # step. A decision is not an answer and not an approval:
    # the state names the decision it waits for.
    coord_reconcile = None
    if state_raw == "paused":
        _pr = record.get("pending_reconcile")
        if isinstance(_pr, dict) and _as_str(_pr.get("question", "")):
            coord_reconcile = _pr
    if state_raw == "completed":
        # The ONLY path to Done: the coordinator's terminal verdict.
        state = "Done"
    elif state_raw == "failed":
        state = "Failed"
    elif state_raw == "cancelled":
        state = "Partial" if success >= 1 else "Failed"
    elif failed_outcome is not None:
        # Crash-inconsistent live record: the coordinator halts on
        # such outcomes, so this task cannot proceed.
        state = "Failed"
    elif state_raw == "pending":
        state = "Queued"
    elif state_raw == "paused":
        if coord_question is not None:
            state = "Needs an answer"
        elif coord_reconcile is not None:
            state = "Needs a decision"
        elif coord_pending is not None:
            state = "Needs approval"
        else:
            state = "Running"
        paused = True
    elif state_raw == "verifying":
        # Wiring item 3: the coordinator verification phase
        # writes this stored state while it runs, so Verifying
        # displays — the phase is the real producer D9 requires.
        # (The legacy boolean marker below is still ignored.)
        # UI Phase 2 (P2-02): a verifying record carrying a
        # coordinator pending approval is waiting on the human,
        # not on the verification — it renders Needs approval
        # like a paused one, so the row, the card, the Review
        # queue, and the badge all name the same thing.
        if coord_pending is not None:
            state = "Needs approval"
        else:
            state = "Verifying"
    elif state_raw == "running":
        # Phase D: the legacy verifying marker is ignored — a
        # record cannot claim Verifying from the marker alone,
        # only from the stored "verifying" state above. A marked
        # record falls through to the approval/running logic.
        if (pending_tool is not None and next_tool
                and next_tool == pending_tool):
            state = "Needs approval"
        else:
            state = "Running"
    else:
        state = None  # unrecognized stored state: honest unknown row

    # -- action / rationale per state ------------------------------
    if state == "Done":
        action = "Done"
        rationale = f"All {total} step(s) complete"
        if skipped:
            rationale += f" ({skipped} skipped — see details)"
        if reason:
            rationale += f" · {reason}"
    elif state == "Failed":
        action = "Failed"
        if failed_outcome is not None:
            flabel = _as_str(failed_outcome.get("label", ""))
            fstatus = _as_str(failed_outcome.get("status", ""))
            fkind = _as_str(failed_outcome.get("kind", ""))
            fmsg = _as_str(failed_outcome.get("message", ""))
            rationale = f"Step {flabel!r} {fstatus}"
            if fkind:
                rationale += f" ({fkind})"
            if fmsg:
                rationale += f": {fmsg}"
        elif reason:
            rationale = reason
        else:
            rationale = "The task stopped before finishing."
        rationale += f" · {progress}"
    elif state == "Partial":
        action = "Partially done"
        rationale = (f"{success} of {total} step(s) done — "
                     "stopped before finishing")
        if reason:
            rationale += f" · {reason}"
    elif state == "Needs approval":
        action = f"Waiting on approval: {next_label or next_tool}"
        if coord_pending is not None:
            _sentence = _as_str(coord_pending.get("sentence", ""))
            rationale = (_sentence if _sentence else
                         f"Approve or deny '{next_tool}' in the "
                         "confirmation dialog.")
        elif pending_summary:
            rationale = str(pending_summary)
        else:
            rationale = (f"Approve or deny '{next_tool}' in the "
                         "confirmation dialog.")
        rationale += f" · {progress}"
    elif state == "Needs a decision":
        # B21: the action names the decision the human must
        # make; the rationale is the pending record's own
        # question, verbatim.
        action = "Decide what happened"
        if coord_reconcile is not None:
            _question = _as_str(coord_reconcile.get("question", ""))
            rationale = (_question if _question else
                         "Decide whether the interrupted step "
                         "applied before the crash.")
        else:
            rationale = ("Decide whether the interrupted step "
                         "applied before the crash.")
        rationale += f" · {progress}"
    elif state == "Verifying":
        action = "Verifying"
        rationale = ("Checking the task's expected outcomes before "
                     f"reporting · {progress}")
    elif state == "Running" and paused:
        action = f"Paused: {next_label}" if next_label else "Paused"
        rationale = ("Paused — resume to continue from the next "
                     f"step · {progress}")
    elif state == "Running":
        if next_step is not None:
            action = next_label or next_tool or "Working"
            if next_label and next_label != next_tool:
                rationale = f"{next_label} ({next_tool})"
            elif next_tool:
                args_line = _args_summary(next_step.get("args"))
                rationale = next_tool + (f" {args_line}" if args_line else "")
            else:
                rationale = "Working"
            rationale += f" · step {index + 1} of {total}"
        else:
            action = "Finishing up"
            rationale = f"All steps attempted · {progress}"
    elif state == "Queued":
        action = "Not started yet"
        rationale = (f"{total} step(s) queued" if total
                     else "No steps defined")
    else:  # unknown stored state
        action = "Task state unknown"
        rationale = (f"Stored state {state_raw!r} is not a known task "
                     "state — see details.")

    # -- details text (existing data only, no new storage) --------
    lines = [f"{title}",
             f"State: {state if state is not None else 'unknown'}"
             f" (stored: {state_raw or 'missing'})"]
    if _as_str(record.get("classification", "")):
        lines.append(f"Kind: {record.get('classification')}")
    if turn_text:
        lines.append(f"Goal: {turn_text[:500]}")
    lines.append(f"Progress: {success} done, {skipped} skipped, "
                 f"{failed} failed, of {total} steps")
    for i, step in enumerate(steps):
        outcome = outcomes[i] if i < len(outcomes) else None
        lines.append(_step_text(step, outcome))
    verification = record.get("verification")
    if isinstance(verification, list):
        for verdict in verification:
            if not isinstance(verdict, dict):
                continue
            vlabel = _as_str(verdict.get("label", "")) or _as_str(
                verdict.get("tool", ""))
            vtext = f"Verification: {vlabel} — " \
                    f"{_as_str(verdict.get('verdict', ''))}"
            vreason = _as_str(verdict.get("reason", ""))
            if vreason:
                vtext += f": {vreason}"
            lines.append(vtext)
    # Retry-on-not-met history (retry batch): the ledger record
    # carries it under "retries" and the receipt prints it — the
    # Activity detail shows it here, in the receipt's wording
    # (minus the receipt's two-space list indent, which the
    # Verification lines above already drop). No header, no
    # section: like the receipt, the lines appear only when the
    # history is non-empty, so an unretried task reads exactly
    # as before.
    retries = record.get("retries")
    if isinstance(retries, list):
        for event in retries:
            if not isinstance(event, dict):
                continue
            rlabel = _as_str(event.get("label", ""))
            rverdict = _as_str(event.get("verdict", ""))
            try:
                rstrike = int(event.get("strike", 0) or 0)
            except (TypeError, ValueError):
                rstrike = 0
            rtext = (f"Retry: {rlabel} — {rverdict} "
                     f"(strike {rstrike})")
            rtext += (" — retried" if event.get("retried")
                      else " — halted")
            lines.append(rtext)
    # Second-opinion auto-invoke history (second-opinion batch):
    # the ledger record carries it under "second_opinions" and
    # the receipt prints it — the Activity detail shows it here,
    # in the receipt's wording (minus the receipt's two-space
    # list indent, which the Verification lines above already
    # drop), after the Retry lines, matching the receipt's
    # order (Verification, Retry, Second opinion). No header,
    # no section: like the receipt, the lines appear only when
    # the history is non-empty, so a task with no second-opinion
    # history reads exactly as before.
    second_opinions = record.get("second_opinions")
    if isinstance(second_opinions, list):
        for event in second_opinions:
            if not isinstance(event, dict):
                continue
            slabel = _as_str(event.get("label", ""))
            sverdict = _as_str(event.get("verdict", ""))
            saction = _as_str(event.get("action", ""))
            lines.append(f"Second opinion: {slabel} — {sverdict} "
                         f"({saction})")
    # B24: the second-opinion OFF line (second-opinion batch):
    # the ledger record carries it under "second_opinion_off"
    # and the receipt prints it — the Activity detail shows it
    # here, in the receipt's wording (minus the receipt's
    # two-space list indent), right after the Second opinion
    # events, matching the receipt's order. No header, no
    # section: like the receipt, the line appears only when
    # the record carries one, so a task whose check ran reads
    # exactly as before.
    _off = record.get("second_opinion_off")
    if isinstance(_off, dict):
        _off_line = _as_str(_off.get("line", ""))
        if _off_line:
            lines.append(_off_line)
    remaining = [ _as_str(s.get("label", "")) or _as_str(s.get("tool", ""))
                  for s in steps[index:] ] if total else []
    if remaining:
        lines.append("Remaining: " + ", ".join(remaining))
    if coord_pending is not None:
        _ptool = _as_str(coord_pending.get("tool", ""))
        _psent = _as_str(coord_pending.get("sentence", ""))
        lines.append("Pending approval: " + _ptool +
                     (f" — {_psent}" if _psent else ""))
    if reason:
        lines.append(f"Reason: {reason}")
    if error_kind:
        lines.append(f"Error kind: {error_kind}")
    if updated_at:
        lines.append(f"Updated: {updated_at}")
    if task_id:
        lines.append(f"Task id: {task_id}")
    details_text = "\n".join(lines)

    return {
        "state": state,
        "title": title,
        "action": action,
        "rationale": rationale,
        "progress": progress,
        "details_text": details_text,
        "updated_at": updated_at,
        "task_id": task_id,
        "next_tool": next_tool,
        "paused": paused,
    }


def instrument_row(parent=None):
    """Shared one-line row shell (Screen 3 Activity): a QWidget
    that re-elides its title label on resize. The caller sets
    ``row._title_lbl`` to the reading label and ``row._title_full``
    to its full text; the row keeps the reading to one line with
    an ellipsis instead of a mid-word clip. Qt is imported lazily
    so this module stays importable (and its pure mapping
    executable) without a Qt runtime.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QWidget

    class _Row(QWidget):
        """One-line instrument row; re-elides the title on resize."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._title_lbl = None
            self._title_full = ""

        def resizeEvent(self, event):
            super().resizeEvent(event)
            label = self._title_lbl
            if label is None or label.width() <= 0:
                return
            elided = QFontMetrics(label.font()).elidedText(
                self._title_full, Qt.TextElideMode.ElideRight,
                label.width())
            if elided != label.text():
                label.setText(elided)

    return _Row(parent)


# QListWidget::item carries 8px top + 8px bottom padding
# (gui/style_sheet.py), and an item widget is laid out inside
# the padded area — so a list row's item needs the padding on
# top of the widget's own height, or the widget is squeezed
# and its labels clip (caught by the Screen 3 render check:
# a 38px row rendered with 5px-tall labels). List-row widgets
# therefore take no vertical margins of their own — the item
# padding IS the row's breathing room.
_LIST_ITEM_VERTICAL_PADDING = 16


def instrument_row_title(row, label, full_text):
    """Wire a row's reading label: the row re-elides it on
    resize, and its horizontal sizeHint is ignored — a long
    reading must elide to the row's width, never widen the row
    past the viewport. Without this, a long journal error made
    the corrupt row's item wider than the list, put a
    horizontal scrollbar under it, and pushed its Details
    button off-screen (caught by the Screen 3 render check)."""
    from PySide6.QtWidgets import QSizePolicy

    row._title_lbl = label
    row._title_full = full_text
    label.setSizePolicy(QSizePolicy.Policy.Ignored,
                        QSizePolicy.Policy.Preferred)


def list_item_size_hint(widget):
    """Item sizeHint for a row widget: the widget's own hint
    plus the QListWidget::item vertical padding."""
    hint = widget.sizeHint()
    hint.setHeight(hint.height() + _LIST_ITEM_VERTICAL_PADDING)
    return hint


def build_task_row_widget(rendered, parent=None):
    """One Activity row widget for a :func:`render_task` result.

    Screen 3 Activity: the row is an instrument — one line: the
    mapped state word as a small coloured label, the title as the
    one reading, the progress fraction right-aligned in the mono
    face, and a quiet Details text action. The hairline is the
    row widget's own bottom border (stylesheet). The action,
    rationale, and updated-at used to sit on the face; they live
    in the Details dialog now — the details text already carries
    progress, steps, verification, reason, updated-at, and task
    id. The state label keeps the mapped state verbatim (the
    cancelled-row suite pins the exact string); its label role
    is size, weight, and colour, not case. Qt is imported
    lazily so this module stays importable (and its pure mapping
    executable) without a Qt runtime.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QPushButton,
    )

    from .room_activity import progress_frac, state_property

    state = rendered.get("state") or "Unknown"
    title = rendered.get("title", "")
    frac = progress_frac(rendered.get("progress", ""))

    wrap = instrument_row(parent)
    wrap.setObjectName("activityRow")
    layout = QHBoxLayout(wrap)
    # No vertical margins of its own: the QListWidget::item
    # padding (8px top + 8px bottom) is the row's breathing
    # room — own margins would be squeezed out of the labels
    # (see list_item_size_hint).
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    state_lbl = QLabel(state)
    state_lbl.setObjectName("activityRowLabel")
    state_lbl.setProperty("rowstate", state_property(state))
    layout.addWidget(state_lbl)
    title_lbl = QLabel(title)
    title_lbl.setObjectName("activityRowValue")
    title_lbl.setWordWrap(False)
    layout.addWidget(title_lbl, stretch=1)
    instrument_row_title(wrap, title_lbl, title)
    if frac:
        num_lbl = QLabel(frac)
        num_lbl.setObjectName("activityRowNumber")
        num_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(num_lbl)
    details_btn = QPushButton("Details")
    details_btn.setObjectName("activityTextAction")
    details_text = rendered.get("details_text", "")
    details_btn.clicked.connect(
        lambda _=False, t=title, s=state, d=details_text, p=parent: (
            _show_details(p, t, s, d)))
    layout.addWidget(details_btn)
    return wrap


def _show_details(parent, title, state, details_text) -> None:
    """Details dialog carrying the full step/receipt detail."""
    try:
        from gui.design_system.dialogs import HudDialog
    except ImportError:
        from voice_assistant.gui.design_system.dialogs import HudDialog  # type: ignore

    HudDialog.details(
        parent, f"Task details — {title}", f"{title}\n{state}", details_text)


def add_task_row(list_widget, rendered, parent=None):
    """Append one component row to a QListWidget (S3 wiring seam).

    Plain-text rows (empty states, unavailable rows) keep using
    addItem directly; every real task row goes through here so
    there is exactly one row renderer.
    """
    from PySide6.QtWidgets import QListWidgetItem

    item = QListWidgetItem(list_widget)
    widget = build_task_row_widget(rendered, parent=parent)
    item.setSizeHint(list_item_size_hint(widget))
    list_widget.addItem(item)
    list_widget.setItemWidget(item, widget)

"""Picker history rendering for the ``run_goal`` pick paths (B18).

The gap (audit L12): the goal loop's picker was handed only the tail
of the history — both text brains rendered ``list(history)[-8:]``
(``core/voice/cloud_text_goal.py`` and
``core/voice/local_client_plangoal.py``) — so past step 8 the picker
could not see what it did at the start of a long task: it re-picked
actions already tried, re-tried a tool that already failed, and could
not see that it was looping, because the loop was outside its window.

ONE design ("deciding blind" closed): a summarised full history.
The picker receives a compact summary of the WHOLE history — one
line per step (tool, outcome, verdict), compressed to what a pick
needs — plus the full last 8 records verbatim, exactly as today.
The summary is built by this ONE helper, in this ONE place, from the
ledger-backed history (B11) — never a second history store — and is
rebuilt from the history on every pick, so a resumed goal's pre-stop
steps are in it.

Honesty rules (never re-open):

* The summary never invents. It compresses only what the history
  actually records. A step with no outcome recorded stays
  "no outcome recorded" — never "succeeded".
* Failures are the most important part of what the picker must see.
  A summary that hides failures is the exact failure, so when the
  block has to shrink, only plain completed lines are dropped, and
  only the oldest first; every failed / halted / interrupted /
  unknown line is kept, and the drop is named in the output.
* The B17 lesson is visible as the FIRST line whenever a playbook
  match is in the history (a lesson steers the first pick — the
  summary must not hide it). No playbook event, no lesson line;
  one is never invented.

Bound (B14): the summary block is capped at ``MAX_SUMMARY_CHARS``.
The verbatim tail is unchanged (8 records x 400 chars), so a pick's
history text is bounded at roughly ``MAX_SUMMARY_CHARS + 3200``
chars plus headings — the prompt cost cannot grow without bound on
a long goal.

Rejected (do not re-open):

* Handing the picker the full raw history — a 25-step history is
  too long for the pick; the pick would drown in detail and cost.
  The summary is the point.
* Raising the window from 8 to 25 — a bigger window is still a
  window, and still drops the first steps on a long goal.
* A second summariser in the coordinator — the coordinator runs
  planned steps, it has no picker; the two text brains share this
  ONE helper.
* Summarising by dropping failures — see the honesty rules above.
"""

from __future__ import annotations

import json

# Compressed-block cap (chars). The verbatim tail adds at most
# 8 x 400 chars, so a pick's history text stays bounded.
MAX_SUMMARY_CHARS = 2000
# One step line is capped so a single long reason cannot eat the block.
_MAX_STEP_LINE_CHARS = 160
# The verbatim tail, unchanged from the pre-B18 rendering.
_TAIL_RECORDS = 8
_TAIL_RECORD_CHARS = 400


def _render_verbatim(records) -> str:
    """The pre-B18 rendering: each record JSON-dumped, capped."""
    lines = []
    for record in records:
        try:
            lines.append(json.dumps(record, default=str)[:_TAIL_RECORD_CHARS])
        except (TypeError, ValueError):
            lines.append(str(record)[:_TAIL_RECORD_CHARS])
    return "\n".join(lines)


def _lesson_line(history) -> str | None:
    """First line of a long history: the B17 lesson, if one matched.

    The playbook match appends a ``{"event": "playbook", ...}`` record
    to the history at goal start (planner_rungoal.py); a lesson that
    steers the first pick must stay visible past step 8. No playbook
    event, no line — one is never invented.
    """
    for record in history:
        if isinstance(record, dict) and record.get("event") == "playbook":
            steps = record.get("steps")
            proof = record.get("proof_job_id") or "unrecorded"
            return (f"Lesson (proven playbook, proof job {proof}): "
                    f"{steps} proven step(s) run first.")
    return None


def _step_lines(history) -> list[str]:
    """One compressed line per step number, in first-seen order.

    Outcome precedence is the LAST outcome-bearing event for the
    step (``completed`` / ``step_failed`` / ``halted``): a retry is
    followed by a re-execution whose outcome is a later record, so
    only the last one is the step's outcome. A step with no
    outcome-bearing event — only a retry, a resolve, a resume, or
    nothing at all — is "no outcome recorded", never "succeeded".
    """
    by_step: dict[int, dict] = {}
    order: list[int] = []
    for record in history:
        if not isinstance(record, dict):
            continue
        step = record.get("step")
        if not isinstance(step, int):
            continue
        if step not in by_step:
            by_step[step] = {"tool": None, "outcome": None, "retried": None,
                             "interrupted": None}
            order.append(step)
        slot = by_step[step]
        event = record.get("event")
        if record.get("tool") and slot["tool"] is None:
            slot["tool"] = str(record.get("tool"))
        if event == "retry":
            slot["retried"] = str(record.get("kind") or "transient")
        elif event == "completed":
            verdict = record.get("expect_verdict")
            slot["outcome"] = ("completed" +
                               (f" [expect {verdict}]" if verdict else ""))
        elif event == "step_failed":
            slot["outcome"] = (f"failed ({record.get('reason') or 'no reason'}"
                               f"; strikes {record.get('strikes')})")
        elif event == "halted":
            slot["outcome"] = f"halted ({record.get('reason') or 'no reason'})"
        elif event == "interrupted":
            slot["interrupted"] = str(record.get("report") or "interrupted")
    lines = []
    for step in sorted(order):
        slot = by_step[step]
        tool = slot["tool"] or "unknown tool"
        if slot["outcome"] is not None:
            outcome = slot["outcome"]
            if slot["retried"] and outcome.startswith("completed"):
                outcome = f"retried ({slot['retried']}) then {outcome}"
        elif slot["interrupted"] is not None:
            outcome = f"interrupted ({slot['interrupted']})"
        else:
            outcome = "no outcome recorded"
        lines.append(f"step {step}: {tool} — {outcome}"[:_MAX_STEP_LINE_CHARS])
    return lines


def summarise_history(history) -> str:
    """The compressed block: one line per step for the WHOLE history.

    Empty for a history with no step records. Bounded at
    ``MAX_SUMMARY_CHARS``: when the block has to shrink, only the
    oldest plain-completed lines are dropped (failures, halts,
    interruptions and unknowns are always kept), and the drop is
    named in the output so the picker is never misled about what
    it is not seeing.
    """
    lines = _step_lines(history)
    if not lines:
        return ""
    block = "\n".join(lines)
    if len(block) <= MAX_SUMMARY_CHARS:
        return block
    dropped = 0
    # Drop the oldest plain-completed lines first; a line that
    # mentions a retry, a failure, a halt, an interruption, or an
    # unknown outcome is never dropped.
    keep = list(lines)
    while len("\n".join(keep)) > MAX_SUMMARY_CHARS:
        victim = None
        for i, line in enumerate(keep):
            if " — completed" in line and "retried" not in line:
                victim = i
                break
        if victim is None:
            break
        del keep[victim]
        dropped += 1
    block = "\n".join(keep)
    if dropped:
        note = (f"({dropped} earliest completed step(s) omitted from "
                f"this summary to stay within {MAX_SUMMARY_CHARS} chars; "
                f"the latest {_TAIL_RECORDS} records below are verbatim.)")
        block = f"{block}\n{note}"
    return block


def render_picker_history(history) -> str:
    """What the picker sees on a pick: summary + verbatim tail.

    * No history: "(no actions taken yet)" — exactly as before.
    * 8 or fewer records: the pre-B18 rendering, byte-identical —
      a short goal behaves exactly as today, no summary needed.
    * More than 8: the B17 lesson line first (when a playbook
      matched), then the summary of the WHOLE history, then the
      full last 8 records verbatim as today. The summary is rebuilt
      from the history passed in on every call, so a resumed goal's
      pre-stop steps are in it.
    """
    if not history:
        return "(no actions taken yet)"
    records = list(history)
    if len(records) <= _TAIL_RECORDS:
        return _render_verbatim(records)
    parts = []
    lesson = _lesson_line(records)
    if lesson is not None:
        parts.append(lesson)
    summary = summarise_history(records)
    parts.append(f"Earlier steps (summary of all {len(records)} "
                 f"records):\n{summary}" if summary else
                 "Earlier steps: no step outcomes recorded.")
    parts.append(f"Latest {_TAIL_RECORDS} (verbatim):\n"
                 f"{_render_verbatim(records[-_TAIL_RECORDS:])}")
    return "\n".join(parts)

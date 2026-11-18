"""Screen 3 Activity — the density test, as a Qt-free model.

Mirrors :mod:`gui.room_home`: the visual system as data, so the
density-test rules can be asserted headlessly and the Qt layer
(``gui/window_pages_activity.py``, ``gui/task_state.py``,
``gui/window_memory_activity.py``, ``gui/window_memory_learned.py``)
only renders them.

The density test (deep doc section 5, Screen 3) asks five things
of the Activity page as it exists — four or five jobs on one
scroll, about twenty-five controls:

1. Five-second headline check — one headline LINE per section,
   the count joined to its label at 20 Sora semibold ("In
   progress — 4 · 1 waiting on you"); everything else smaller.
   (UI rethink Build 2, 2026-11-16: the giant 28 mono figure
   is gone — it was the biggest thing on the page and it meant
   the least. The count joins its label on one line.)
2. Four weights, no fifth — headline line; section headings;
   primary values inside rows; metadata and labels. A fifth
   weight is a failure; if a row needs one to be understood,
   the row is two rows. (Build 2: the heading and the row value
   share the 16 rung — weight, not size, separates a heading
   from a row title — so the page shows three sizes plus the
   caption half of the metadata level.)
3. Rows are instruments — a small label, one reading in the
   display face, one hairline rule. Detail lives one tap away,
   never on the row's face.
4. The pending row is one primary action — the first visible
   queue owns the primary button; the other three are quiet
   text actions. Four buttons at the same weight is four
   primary actions, which is none. (Build 2: all visible
   queues are quiet text actions, the first queue first —
   they are the same kind of thing, so none shouts.)
5. A real morning stays dense and calm — add a reminder, check
   a routine, approve a pending thing, and the page does not
   shout, flicker, or re-layout under the work.

Rules the model pins
-------------------
* The type ladder is the Home ladder (ratio 1.2 from the body
  size): headline line 20, heading 16, row value 16, metadata 13
  with an 11 caption half. The 20 is 1.28 off the 18.72 rung and
  the 16 is 0.4 off the 15.6 rung, both inside the Home build's
  1.5 px tolerance, and the 13 and 11 are ONE weight level
  (small / caption), not two — so the count stays four.
* The row's label is the mapped state word, in its mapped
  case, at 11 semibold, coloured per state. It is NOT
  uppercased: the cancelled-row suite pins the exact mapped
  string in the widget's labels, and a second hidden label
  carrying the mapped case would be dishonest markup. The
  label role is carried by size, weight, and colour, not case.
* Numbers are right-aligned, one decimal rule, monospace face.
  The progress number is the ``N/M`` fraction parsed from the
  mapped progress string — the mapping itself is not touched.
* Error lines are one sentence: what failed, one cause, one
  recovery, in body type, with a red dot beside it and
  nothing else. The orb never reacts. Where the surface is a
  plain-text list item (the ledger-unavailable rows, pinned by
  the Activity addItem allowlist), the dot is a glyph in body
  colour — the line shape, cause, and recovery still hold, and
  the gap is recorded, not hidden. Where the surface is a
  widget (the corrupt journal row), the dot is real red.
* No colour literal lives in this file (guard-clean: token
  names only, never values). Readability pairs are named by
  token; the test resolves them through ``gui.style_tokens``
  and the contrast math reused from :mod:`gui.room_home`.
* Contextual actions never hide behind a dimmed button: an
  action that appears only on selection (journal Reconcile /
  Compensate, Scheduled Remove selected) does not exist on
  the page while nothing is selected, and the section says so
  in words (one hint line) while there is something to act
  on. A button that can never be pressed is clutter, not a
  control.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .task_state import render_task

# --- The four weights -------------------------------------------------
# Headline line, section heading, row value, metadata. The 13 and
# 11 are one level (small / caption). Count them on the render and
# record the count: four. The heading and the row value share the
# 16 rung by design (Build 2): weight 600 separates a heading
# from a weight-400 row title, not size.
TYPE_HEADLINE = 20
TYPE_HEADING = 16
TYPE_ROW = 16
TYPE_METADATA = 13
TYPE_CAPTION = 11.5  # the tree's caption token; the doc rounds it to 11
WEIGHTS = (TYPE_HEADLINE, TYPE_HEADING, TYPE_ROW, TYPE_METADATA)

# Ladder check (ratio 1.2 from the body size): 13 x 1.2 = 15.6, so
# the row/heading rung 16 is 0.4 off; 13 x 1.2^2 = 18.72, so the
# headline rung 20 is 1.28 off. Both inside the Home build's
# 1.5 px tolerance, and both are named rungs, not new weights.
LADDER_TOLERANCE_PX = 1.5

# --- Headline lines -----------------------------------------------------
# One headline LINE per section. The label and the count join on
# one line at 20 Sora semibold — "In progress — 4 · 1 waiting on
# you" — with one true context part after it, or nothing. The
# line is the page's one large element; there is no giant figure
# on its own line anymore (Build 2). An empty section says so
# in words ("Nothing in progress") instead of showing a 0.

def headline_counts(records: list) -> dict:
    """Count what the four headlines need, from raw ledger records.

    Callers pass the records newest-first, exactly as the refresh
    already orders them. Live work is a raw state of running or
    paused; waiting is live work whose mapped state says it needs
    the user (approval, an answer, or a decision); finished is
    the History tab's own filter; attention is the mission log's
    rendered Failed + Partial across every record.
    """
    live = [r for r in records if str(r.get("state", "")) in ("running", "paused")]
    waiting = 0
    for record in live:
        if render_task(record)["state"] in (
            "Needs approval",
            "Needs an answer",
            "Needs a decision",
        ):
            waiting += 1
    finished = [
        r
        for r in records
        if str(r.get("state", "")) in ("completed", "failed", "cancelled")
    ]
    attention = 0
    for record in records:
        if render_task(record)["state"] in ("Failed", "Partial"):
            attention += 1
    latest_finished = finished[0].get("title", "") if finished else ""
    return {
        "live": len(live),
        "waiting": waiting,
        "finished": len(finished),
        "latest_finished": str(latest_finished),
        "attention": attention,
        "total": len(records),
    }


def headline_line(section: str, counts: dict) -> str:
    """The one-line headline for a ledger section.

    "In progress — 4 · 1 waiting on you": the label, the count,
    and one true context part, joined on one line. A paused task
    awaiting approval is still in progress — it is the work that
    needs the user, and the line names how much of it waits on
    them. An empty section says so in words instead of showing
    a 0.
    """
    if section == "in_progress":
        live = int(counts.get("live", 0))
        if not live:
            return "Nothing in progress"
        waiting = int(counts.get("waiting", 0))
        line = f"In progress — {live}"
        if waiting:
            line += f" · {waiting} waiting on you"
        return line
    if section == "history":
        finished = int(counts.get("finished", 0))
        if not finished:
            return "Nothing finished yet"
        latest = str(counts.get("latest_finished", "") or "")
        if len(latest) > 40:
            latest = latest[:39] + "…"
        line = f"Finished — {finished}"
        if latest:
            line += f" · latest: {latest}"
        return line
    if section == "mission_log":
        attention = int(counts.get("attention", 0))
        if not attention:
            return "Nothing needs attention"
        total = int(counts.get("total", 0))
        line = f"Need attention — {attention}"
        if total:
            line += f" · of {total} missions"
        return line
    raise ValueError(f"unknown headline section: {section!r}")


def due_today(reminders: list, today: date) -> tuple[int, int]:
    """(due today, upcoming) from reminder dicts.

    A reminder is due today when it has not fired and its
    ``fire_at`` date is today; upcoming is every unfired
    reminder. An unparseable ``fire_at`` counts as neither —
    it is never silently promoted into today.
    """
    due = 0
    upcoming = 0
    for reminder in reminders:
        if reminder.get("fired"):
            continue
        fire_at = str(reminder.get("fire_at", "") or "")
        try:
            fire_date = date.fromisoformat(fire_at[:10])
        except ValueError:
            continue
        upcoming += 1
        if fire_date == today:
            due += 1
    return due, upcoming


def scheduled_line(reminders: list, today: date) -> str:
    """The one-line Scheduled headline.

    "2 due today · next 09:00 · 1 already spoken": the count due
    today, the next fire time, and how many have already been
    spoken — one line, each part shown only when true. The
    explainer sentence ("VYUK will speak these out loud…")
    lives behind the "?" beside the heading, not on the page.
    """
    due, _upcoming = due_today(reminders, today)
    spoken = sum(1 for r in reminders if r.get("fired"))
    parts = [f"{due} due today" if due else "Nothing due today"]
    nxt = _next_fire_text(reminders, today)
    if nxt:
        parts.append(f"next {nxt}")
    if spoken:
        parts.append(f"{spoken} already spoken")
    return " · ".join(parts)


def _next_fire_text(reminders: list, today: date) -> str:
    """"09:00" for the earliest unfired reminder (today),
    "tomorrow 08:00" for tomorrow, "17 Nov 08:00" later on —
    or "" when nothing is upcoming. An unparseable fire_at is
    never silently promoted into the next slot."""
    best = None
    for reminder in reminders:
        if reminder.get("fired"):
            continue
        fire_at = str(reminder.get("fire_at", "") or "")
        try:
            when = datetime.fromisoformat(fire_at)
        except ValueError:
            continue
        if best is None or when < best:
            best = when
    if best is None:
        return ""
    clock = best.strftime("%H:%M")
    if best.date() == today:
        return clock
    if best.date() == today + timedelta(days=1):
        return f"tomorrow {clock}"
    return best.strftime("%d %b %H:%M")


# --- Rows ---------------------------------------------------------------
# A row is an instrument: a small label, one reading, one hairline.
# The state word colours the label; the property value is the
# token name lowercased, so the stylesheet keys on it without a
# colour literal ever reaching the Qt layer.

STATE_TOKEN = {
    "Done": "OK",
    "Failed": "DANGER",
    "Partial": "AMBER",
    "Needs approval": "AMBER",
    "Needs an answer": "AMBER",
    "Needs a decision": "AMBER",
    "Running": "TEXT_MUTED",
    "Verifying": "TEXT_MUTED",
    "Queued": "TEXT_MUTED",
    "Unknown": "TEXT_MUTED",
}

ROW_STATE_PROPERTY = "rowstate"


def state_property(state: str) -> str:
    """Stylesheet property value for a mapped state."""
    return STATE_TOKEN.get(str(state), "TEXT_MUTED").lower()


_FRACTION = re.compile(r"^(\d+/\d+)")


def progress_frac(progress: str) -> str:
    """The ``N/M`` fraction of a mapped progress string, or "".

    The mapping is not touched: the fraction is parsed from the
    string it already produces ("2/5 steps done"). "no steps
    defined" has no fraction, so the number stays hidden.
    """
    match = _FRACTION.match(str(progress or "").strip())
    return match.group(1) if match else ""


# --- Error lines ----------------------------------------------------------
# One sentence: what failed, one cause, one recovery, in body
# type. The dot is a view concern (a red dot widget where the
# surface is a widget; a glyph where it is plain text). When the
# app does not know why, it says so — never "something went
# wrong".

UNKNOWN_WHY = "I don't know why this failed"


def error_line(what: str, why: str | None, recovery: str) -> str:
    """Build one error sentence from its three parts."""
    what = str(what or "").strip().rstrip(".")
    why = str(why or "").strip().rstrip(".")
    recovery = str(recovery or "").strip().rstrip(".")
    if why:
        return f"{what} — {why}. {recovery}."
    return f"{what} — {UNKNOWN_WHY}. {recovery}."


# Matches an absolute filesystem path (Unix or Windows) so a
# user-facing error line can name the file without leaking
# where the user keeps it. A lone "/" never matches: every
# segment needs at least one path character.
_ABS_PATH_RE = re.compile(
    r"(?:/[A-Za-z0-9_.\-]+)+/?"
    r"|[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*")


def strip_absolute_paths(text: str) -> str:
    """Replace every absolute path in ``text`` with its file
    name. Field-check fix #8 (U5 #32/#33): the store errors
    carry the full path ("task ledger unreadable at
    /home/.../task_ledger.json: ...") and the Activity rows
    used to print it verbatim. The file name keeps the cause
    identifiable ("task_ledger.json") without saying where
    the user keeps their data. A relative name passes through
    unchanged; text with no path is returned as-is."""
    def _basename(match):
        path = match.group(0)
        base = path.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return base or path
    return _ABS_PATH_RE.sub(_basename, str(text or ""))


# --- What changed, and the way back --------------------------------------
# Demotions, in the Home build's discipline: every removal names
# its way back. Restyles (same information, quieter weight) are
# listed too, so the report can tell them apart.

CHANGES = (
    {
        "element": "Pending row, buttons 2–4",
        "was": "four chip buttons at the same weight",
        "now": "one primary button (first visible queue); the rest quiet text",
        "way_back": "the primary assignment is automatic — first visible queue in approval, question, reconcile, recovery order — and every queue stays tappable",
    },
    {
        "element": "Task row meta line",
        "was": "action, rationale, updated-at on the row's face",
        "now": "one line: state label, title, progress fraction, Details",
        "way_back": "Details opens the same details text — progress, steps, verification, reason, updated-at, task id",
    },
    {
        "element": "Plan row meta line",
        "was": "steps done, next step, interrupted flag on the face",
        "now": "one line: label, goal, done/total, Resume / Run / Details",
        "way_back": "Details opens the plan's steps with their status, plus the cursor",
    },
    {
        "element": "Routine schedule editor",
        "was": "always visible under every routine row",
        "now": "behind a quiet Schedule toggle on the row",
        "way_back": "the toggle on the row; a refused save opens the editor itself so the refusal is visible",
    },
    {
        "element": "Routine Delete",
        "was": "a chip button on the row",
        "now": "a quiet text action on the row",
        "way_back": "still on the row, one tap — the behaviour (no confirm) is unchanged, only the weight",
    },
    {
        "element": "Journal corrupt row",
        "was": "one loud plain-text row",
        "now": "red dot, one line, quiet Details beside it",
        "way_back": "Details opens the repair dialog with the manual repair path; the row's stored text and repair steps are unchanged",
    },
    {
        "element": "Headline figure (Build 2)",
        "was": "label over a 28 mono figure, comparison beside it",
        "now": "one line at 20 Sora semibold: label — count · context",
        "way_back": "the count is in the line; the ledger still holds the rows behind it",
    },
    {
        "element": "Section headings (Build 2)",
        "was": "23 Orbitron",
        "now": "16 Sora semibold",
        "way_back": "weight, not size, separates a heading from a 16 row title",
    },
    {
        "element": "Pending row primary button (Build 2)",
        "was": "first visible queue owned the primary button",
        "now": "all visible queues are quiet text actions, first queue first",
        "way_back": "every queue stays tappable in the same order; nothing is hidden",
    },
    {
        "element": "Reconcile / Compensate buttons (Build 2)",
        "was": "always visible, dimmed when nothing selected",
        "now": "appear only when a journal entry is selected; a hint line names them otherwise",
        "way_back": "the hint line 'Select entries to reconcile or compensate' when entries exist; Details on any row",
    },
    {
        "element": "Scheduled explainer + summary (Build 2)",
        "was": "three lines saying the same count",
        "now": "one headline line; the explainer lives behind the '?' beside the heading",
        "way_back": "the '?' beside the Scheduled headline",
    },
    {
        "element": "Refresh / Remove selected (Build 2)",
        "was": "inside the add-reminder input row",
        "now": "Refresh is a quiet text button at the Reminders section head; Remove selected appears only on selection",
        "way_back": "Del still removes the selected reminder; Refresh is at the section head",
    },
    {
        "element": "Tab pane border (Build 2)",
        "was": "the bordered pane ran to the bottom of the window",
        "now": "the pane hugs the current tab's content; below it is plain background",
        "way_back": "the lists scroll inside their cap when there are more rows than fit",
    },
    {
        "element": "Fire-at time fields (Build 2)",
        "was": "'14 : 0', unpadded",
        "now": "14:00, zero-padded",
        "way_back": "the same two spin boxes; only the display pads",
    },
)

# --- Readability ----------------------------------------------------------
# The pairs the page actually shows, named by token. Floors are
# the deep doc's: 4.5:1 body, 3:1 large. The test resolves the
# tokens through gui.style_tokens and measures with the contrast
# math reused from room_home.

READABILITY_PAIRS = (
    ("headline line, TEXT on BG", "TEXT", "BG", 3.0),
    ("section heading, TEXT on BG", "TEXT", "BG", 4.5),
    ("row value, TEXT on BG", "TEXT", "BG", 4.5),
    ("metadata, TEXT_MUTED on BG", "TEXT_MUTED", "BG", 4.5),
    ("fired row, TEXT_FAINT on BG", "TEXT_FAINT", "BG", 4.5),
    ("failed state, DANGER on BG", "DANGER", "BG", 4.5),
    ("attention state, AMBER on BG", "AMBER", "BG", 4.5),
    ("done state, OK on BG", "OK", "BG", 4.5),
    ("mission detail, TEXT on SURFACE_2", "TEXT", "SURFACE_2", 4.5),
    ("mission detail muted, TEXT_MUTED on SURFACE_2", "TEXT_MUTED", "SURFACE_2", 4.5),
)

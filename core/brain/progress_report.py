"""U9 — Progress report (tutor programme, 2026-11-08).

Lab leg only. The real-Windows field leg — a real MA 105 progress
report on his machine, showing what he knows, what is due, and what
his misconceptions are, with the clear action deleting exactly the
tutor records — is OWED, not this batch.

Gap proved first (audit T9): the tutor keeps a record (U1) and a
schedule (U8) and a diagnosis library (U4), but the owner cannot SEE
any of it. ``StudentModel.mastery_summary()`` (``student_model.py``)
and ``ReviewScheduler.schedule()`` (``spaced_repetition.py``) are
reader surfaces built for U9 — but nothing assembles them, nothing
renders them, and there is no clear action across the three tutor
categories. The only ``progress_report`` in the tree is
``_tool_progress_report`` (``core/voice/local_client_guards.py``) —
a tool-call progress summary for the voice client, not a tutor
report. The Activity page (``gui/window_pages_activity.py``) is an
agent-activity log — activity tabs, mission-log handlers, egress
ledger, undo — not a tutor progress report. No report module, no
report view, no report tool exists anywhere in ``core/`` or ``gui/``.

LOPHOLE FAMILY — "a report that lies by omission or by flattery":
a report that shows only what he got right flatters him and hides
the weak skills; a report that shows a wrong answer from a
low-confidence hearing as a failure lies about him; a report that
shows a hinted correct answer as a clean transfer inflates mastery;
a report that cannot be cleared traps his data. ONE design closes
the class: the report is a READ-ONLY assembly of the records that
already exist — U1's mastery, U8's schedule, U4's unclassified
entries — with the honesty rules enforced in the assembly, and a
single explicit clear that deletes only tutor records and reports
exactly what it deleted.

The honesty rules, enforced where the data is assembled (not in the
rendering, so no rendering can re-introduce them):
- Low-confidence hearings are NEVER shown as wrong: they were never
  recorded (U1's rule — confidence < 0.6 records nothing), and the
  assembly reads only recorded evidence, so they cannot appear.
- Hinted correct answers are NEVER shown as clean transfers: the
  transfers count is U1's, which already excludes hinted answers
  (``record_graded`` increments transfers only for correct + novel
  + not hinted). The assembly reads that count as-is.
- Unclassified diagnoses are shown as "not yet named", never forced
  into a category. The library's named entries are listed when a
  library is supplied, but WITHOUT counts — see the named residual
  below.
- The record is clearable by ONE explicit action
  (``clear_tutor_records``): it deletes only the three tutor
  categories and returns exactly what it deleted, per category.
  It is never automatic and never silent.

NAMED RESIDUAL — classified diagnosis counts are not recorded
anywhere, so the report does not invent them. U4 persists ONLY
unclassified errors (``error_diagnosis.py`` has exactly one
``save_fact`` call, for unclassified records); a classified
diagnosis is returned to the caller and never stored, in memory or
on disk. The report therefore lists the named library entries as
"the tutor knows these by name" with an explicit "counts not
recorded", and lists the unclassified entries with their counts.
Closing this needs U4 (or the session driver) to persist classified
outcomes — a change to U4, out of scope for U9. The report never
shows a zero count for a named entry, because zero would be a lie
about a count that was never taken.

Learning speed is computed from the records' own timestamps — the
student record's ``updated_at``, the FSRS card's ``last_review``,
and the unclassified record's ``recorded_at`` — over the span from
the earliest to the latest (calendar days, at least 1). It is
evidence/day and transfers/day over that span, rounded to 2
decimals. When there is no record, the span and the rates are None
("no record yet"), never 0. The span is what the records can prove;
a skill's first evidence is not stored separately, so the report
never claims a longer history than the timestamps show.

Source attribution (D-T2): a bite carries no stored source field
(``lesson_planner.Bite`` has none), so the assembly takes an
optional ``sources`` map (skill id -> source string, supplied by
the driver from the lesson plan). A skill with no supplied source
is shown as "not recorded" — the report never invents a source.

The split, precisely: this module is the ONLY place that reads the
tutor records for display. ``assemble_report`` reads U1/U8/U4
records and returns plain data; ``render_text`` formats that data
and computes nothing new; the GUI panel
(``gui/progress_report_panel.py``) renders the same assembled dict
and holds no store, no model, and no write handle. The assembly
grades nothing, writes nothing, calls ``record_graded`` never,
calls ``observe`` never. The clear is the one writer, and it only
deletes.

REJECTED alternatives (recorded, not carried forward):
- A report that re-computes mastery from raw logs (two sources of
  truth drift — the report reads U1's record, it does not
  re-derive it).
- A report that shows only strengths (flattery is a lie by
  omission — weak skills and misconceptions are the report's
  point; the weakest skills are listed first).
- A report that counts hinted answers as transfers (U1's rule
  already forbids it — the report must not re-introduce it).
- A silent or automatic clear (D-T1 says one EXPLICIT action —
  the clear is a deliberate, confirmed, reported action, never a
  background job).
- A report that ships data anywhere (local-only — the report is
  rendered from the local store, it sends nothing, it has no
  network path).
- Inventing classified diagnosis counts (see the named residual —
  a count that was never taken cannot be shown, not even as zero).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .error_diagnosis import UNCATEGORISED_CATEGORY
from .spaced_repetition import CATEGORY as SR_CATEGORY
from .spaced_repetition import ReviewScheduler
from .student_model import CATEGORY as SM_CATEGORY
from .student_model import MASTERY_TRANSFERS
from .student_model import StudentModel

# The three tutor record categories. The clear deletes ONLY these.
TUTOR_CATEGORIES = (SM_CATEGORY, SR_CATEGORY, UNCATEGORISED_CATEGORY)

# A skill below this mastery is weak enough to list first.
WEAK_P = 0.6

# "Due today" is the next 24 hours from the report's now. The label
# says so, because a calendar day would need a timezone the report
# does not claim.
DUE_TODAY = timedelta(hours=24)


def _parse_time(text):
    """Parse a record timestamp to an aware UTC datetime, or None.

    Accepts ISO strings (with or without an offset, with or without
    a trailing Z) and the SQLite CURRENT_TIMESTAMP form
    "YYYY-MM-DD HH:MM:SS". Naive values are read as UTC. Anything
    else is unparseable, never a crash.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    value = text.strip()
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _span_days(stamps):
    """Calendar-day span from the earliest to the latest timestamp.

    At least 1 day: a single day of evidence is one day of record,
    not zero. Returns (start_date_iso, end_date_iso, days) or
    (None, None, None) when no timestamp parses.
    """
    parsed = [t for t in (_parse_time(s) for s in stamps) if t is not None]
    if not parsed:
        return None, None, None
    start = min(parsed).date()
    end = max(parsed).date()
    days = (end - start).days + 1
    return start.isoformat(), end.isoformat(), max(1, days)


def _rows_of(store):
    """All facts rows, or [] when the store cannot list them."""
    try:
        rows = store.get_all_facts()
    except Exception:  # noqa: BLE001 — a store that cannot list
        # facts is a report with nothing to read, never a crash.
        return []
    return list(rows) if isinstance(rows, list) else []


def _unclassified_groups(store):
    """Persisted unclassified errors, grouped by skill.

    Returns (groups, unreadable): groups is a list of
    {"skill", "count", "latest_recorded_at", "latest_answer"} sorted
    by count desc then skill; unreadable counts rows whose JSON
    would not parse (skipped, never fatal).
    """
    by_skill = {}
    unreadable = 0
    for row in _rows_of(store):
        if row.get("category") != UNCATEGORISED_CATEGORY:
            continue
        try:
            record = json.loads(row.get("value") or "")
        except (ValueError, TypeError):
            unreadable += 1
            continue
        if not isinstance(record, dict):
            unreadable += 1
            continue
        skill = str(record.get("skill") or "unknown").strip() or "unknown"
        group = by_skill.setdefault(skill, {
            "skill": skill, "count": 0,
            "latest_recorded_at": None, "latest_answer": "",
        })
        group["count"] += 1
        recorded_at = _parse_time(record.get("recorded_at"))
        current = _parse_time(group["latest_recorded_at"])
        if recorded_at is not None and (current is None
                                        or recorded_at >= current):
            group["latest_recorded_at"] = record.get("recorded_at")
            group["latest_answer"] = str(record.get("answer") or "")
    groups = sorted(by_skill.values(),
                    key=lambda g: (-g["count"], g["skill"]))
    return groups, unreadable


def assemble_report(store, *, now=None, sources=None, library=None):
    """Assemble the progress report from the tutor records.

    Read-only: it grades nothing, writes nothing, calls
    ``record_graded`` never and ``observe`` never. ``store`` is the
    injected MemoryStore (duck-typed: ``get_all_facts``). ``now`` is
    an aware datetime (default: UTC now); ``sources`` is an optional
    {skill_id: source} map; ``library`` is an optional
    ``MisconceptionLibrary`` whose named entries are listed without
    counts (see the named residual in the module docstring).

    Returns plain data (JSON-able). On a store that cannot be read,
    returns {"ok": False, "reason": ...} with empty sections.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    sources = sources or {}
    empty = {
        "ok": False, "reason": "", "generated_at": now.isoformat(),
        "skills": [], "totals": {"skills": 0, "mastered": 0,
                                 "evidence": 0, "correct": 0,
                                 "wrong": 0, "transfers": 0},
        "due_now": [], "due_24h": [], "weakest": [],
        "misconceptions": {"named": [], "named_counts_recorded": False,
                           "unclassified": [], "unclassified_total": 0,
                           "unreadable": 0},
        "learning_speed": {"span_start": None, "span_end": None,
                           "span_days": None, "evidence_per_day": None,
                           "transfers_per_day": None},
    }
    try:
        model = StudentModel(store)
        scheduler = ReviewScheduler(store)
        mastery_records = model.all_skills()
        schedule_views = scheduler.schedule(now=now)
        due_now_views = scheduler.due_queue(now=now)
    except Exception as exc:  # noqa: BLE001 — a store that cannot
        # be read is an unavailable report, never a crash.
        empty["reason"] = f"cannot read tutor records: {exc}"
        return empty

    views_by_skill = {v["skill_id"]: v for v in schedule_views}
    skills = []
    stamps = []
    total_evidence = 0
    total_correct = 0
    total_wrong = 0
    total_transfers = 0
    for record in mastery_records:
        skill_id = record["skill_id"]
        view = views_by_skill.get(skill_id) or {}
        transfers = int(record.get("transfers", 0))
        total_evidence += int(record.get("evidence", 0))
        total_correct += int(record.get("correct", 0))
        total_wrong += int(record.get("wrong", 0))
        total_transfers += transfers
        stamps.append(record.get("updated_at"))
        skills.append({
            "skill_id": skill_id,
            "p": round(float(record.get("p", 0.30)), 4),
            "evidence": int(record.get("evidence", 0)),
            "correct": int(record.get("correct", 0)),
            "wrong": int(record.get("wrong", 0)),
            "transfers": transfers,
            "mastered": transfers >= MASTERY_TRANSFERS,
            "stability": view.get("stability"),
            "difficulty": view.get("difficulty"),
            "retrievability": (
                round(float(view["retrievability"]), 4)
                if view.get("retrievability") is not None else None),
            "due": view.get("due"),
            "state_name": view.get("state_name"),
            "reviews": view.get("reviews"),
            "lapses": view.get("lapses"),
            "last_review": view.get("last_review"),
            "source": str(sources.get(skill_id, "") or ""),
        })
    # A skill with a schedule but no mastery record yet: show it
    # with no mastery, never hide it.
    known = {s["skill_id"] for s in skills}
    for view in schedule_views:
        skill_id = view["skill_id"]
        if skill_id in known:
            continue
        skills.append({
            "skill_id": skill_id,
            "p": None, "evidence": 0, "correct": 0, "wrong": 0,
            "transfers": 0, "mastered": False,
            "stability": view.get("stability"),
            "difficulty": view.get("difficulty"),
            "retrievability": (
                round(float(view["retrievability"]), 4)
                if view.get("retrievability") is not None else None),
            "due": view.get("due"),
            "state_name": view.get("state_name"),
            "reviews": view.get("reviews"),
            "lapses": view.get("lapses"),
            "last_review": view.get("last_review"),
            "source": str(sources.get(skill_id, "") or ""),
        })
    skills.sort(key=lambda s: s["skill_id"])
    for view in schedule_views:
        stamps.append(view.get("last_review"))

    due_now = [v["skill_id"] for v in due_now_views]
    horizon = now + DUE_TODAY
    due_24h = [v["skill_id"] for v in schedule_views
               if v.get("due") and _parse_time(v["due"]) is not None
               and _parse_time(v["due"]) <= horizon]
    weakest = [{"skill_id": s["skill_id"], "p": s["p"]}
               for s in skills
               if not s["mastered"] and s["p"] is not None
               and s["p"] < WEAK_P]
    weakest.sort(key=lambda w: (w["p"], w["skill_id"]))

    groups, unreadable = _unclassified_groups(store)
    for group in groups:
        stamps.append(group["latest_recorded_at"])
    named = []
    if library is not None:
        try:
            entries = library.entries()
        except Exception:  # noqa: BLE001 — a library that cannot
            # list is no named entries, never a crash.
            entries = []
        for entry in entries:
            named.append({
                "id": entry.get("id", ""),
                "name": entry.get("name", ""),
                "skills": list(entry.get("skills", []) or []),
            })
    span_start, span_end, span_days = _span_days(stamps)
    if span_days is None:
        speed = {"span_start": None, "span_end": None, "span_days": None,
                 "evidence_per_day": None, "transfers_per_day": None}
    else:
        speed = {
            "span_start": span_start, "span_end": span_end,
            "span_days": span_days,
            "evidence_per_day": round(total_evidence / span_days, 2),
            "transfers_per_day": round(total_transfers / span_days, 2),
        }

    return {
        "ok": True, "reason": "",
        "generated_at": now.isoformat(),
        "skills": skills,
        "totals": {"skills": len(skills),
                   "mastered": sum(1 for s in skills if s["mastered"]),
                   "evidence": total_evidence,
                   "correct": total_correct,
                   "wrong": total_wrong,
                   "transfers": total_transfers},
        "due_now": due_now,
        "due_24h": due_24h,
        "weakest": weakest,
        "misconceptions": {
            "named": named,
            "named_counts_recorded": False,
            "unclassified": groups,
            "unclassified_total": sum(g["count"] for g in groups),
            "unreadable": unreadable,
        },
        "learning_speed": speed,
    }


def _fmt_p(value):
    return f"{value:.2f}" if isinstance(value, (int, float)) else "—"


def _fmt_due(value):
    parsed = _parse_time(value)
    return parsed.date().isoformat() if parsed is not None else "—"


def render_text(report):
    """Render an assembled report as plain text.

    Formats only — it computes nothing new, so it cannot disagree
    with the assembly. The GUI panel renders the same dict.
    """
    if not isinstance(report, dict) or not report.get("ok"):
        reason = ""
        if isinstance(report, dict):
            reason = str(report.get("reason") or "")
        return ("Tutor progress report unavailable."
                + (f" {reason}" if reason else ""))
    lines = ["Tutor progress report",
             f"Generated: {report.get('generated_at', '')}", ""]
    totals = report["totals"]
    lines.append(
        f"Overall: {totals['skills']} skills, "
        f"{totals['mastered']} mastered, "
        f"{totals['evidence']} evidence "
        f"({totals['correct']} right, {totals['wrong']} wrong), "
        f"{totals['transfers']} clean transfers.")
    speed = report["learning_speed"]
    if speed["span_days"] is None:
        lines.append("Learning speed: no record yet.")
    else:
        lines.append(
            f"Learning speed: {speed['evidence_per_day']} evidence/day, "
            f"{speed['transfers_per_day']} transfers/day over "
            f"{speed['span_days']} day(s) "
            f"({speed['span_start']} to {speed['span_end']}).")
    lines.append("")
    lines.append("Skills:")
    if not report["skills"]:
        lines.append("  (no skills recorded yet)")
    for skill in report["skills"]:
        mastery = ("mastered" if skill["mastered"]
                   else f"p {_fmt_p(skill['p'])}")
        due = _fmt_due(skill.get("due"))
        retrievability = _fmt_p(skill.get("retrievability"))
        source = skill.get("source") or "not recorded"
        lines.append(
            f"  {skill['skill_id']} — {mastery}, "
            f"{skill['evidence']} evidence "
            f"({skill['correct']} right, {skill['wrong']} wrong), "
            f"{skill['transfers']} clean transfers, "
            f"retrievability {retrievability}, due {due}, "
            f"source: {source}.")
    lines.append("")
    lines.append(f"Due now: {len(report['due_now'])} skill(s).")
    for skill_id in report["due_now"]:
        lines.append(f"  {skill_id}")
    lines.append("Due in the next 24 hours: "
                 f"{len(report['due_24h'])} skill(s).")
    for skill_id in report["due_24h"]:
        lines.append(f"  {skill_id}")
    lines.append("")
    lines.append("Weakest skills (lowest mastery first):")
    if not report["weakest"]:
        lines.append("  (none — every recorded skill is mastered "
                     "or at 0.60 or above)")
    for weak in report["weakest"]:
        lines.append(f"  {weak['skill_id']} — p {_fmt_p(weak['p'])}")
    lines.append("")
    lines.append("Misconceptions:")
    misconceptions = report["misconceptions"]
    if misconceptions["named"]:
        lines.append("  Known by name (counts not recorded):")
        for entry in misconceptions["named"]:
            skills = ", ".join(entry["skills"]) or "—"
            lines.append(f"    {entry['name']} [{entry['id']}] — "
                         f"skills: {skills}")
    else:
        lines.append("  Known by name: (none listed)")
    if misconceptions["unclassified"]:
        lines.append("  Not yet named:")
        for group in misconceptions["unclassified"]:
            lines.append(
                f"    {group['skill']}: {group['count']} error(s) "
                f"not yet named"
                + (f" (latest: {group['latest_recorded_at']})"
                   if group["latest_recorded_at"] else ""))
    else:
        lines.append("  Not yet named: (none)")
    if misconceptions["unreadable"]:
        lines.append(f"  ({misconceptions['unreadable']} unclassified "
                     "record(s) could not be read)")
    return "\n".join(lines)


def clear_tutor_records(store):
    """Delete ONLY the tutor records. ONE explicit action.

    Deletes every fact in the three tutor categories
    (student_model, spaced_repetition, diagnosis_unclassified) and
    nothing else. Returns {"ok": True, "deleted": {category: count},
    "total": n}. A delete that fails on one row keeps clearing the
    rest; only successful deletes are counted. Never automatic,
    never silent — the caller confirms, the result reports.
    On a store that cannot list facts, returns
    {"ok": False, "reason": ...}.
    """
    try:
        rows = store.get_all_facts()
    except Exception as exc:  # noqa: BLE001 — a store that cannot
        # list facts cannot be cleared, never a crash.
        return {"ok": False, "reason": f"cannot list facts: {exc}",
                "deleted": {}, "total": 0}
    if not isinstance(rows, list):
        return {"ok": False, "reason": "cannot list facts",
                "deleted": {}, "total": 0}
    deleted = {category: 0 for category in TUTOR_CATEGORIES}
    for row in rows:
        category = row.get("category")
        if category not in TUTOR_CATEGORIES:
            continue
        try:
            if store.delete_fact(row["id"]):
                deleted[category] += 1
        except Exception:  # noqa: BLE001 — keep clearing the rest
            # even if one row fails.
            continue
    return {"ok": True, "reason": "", "deleted": deleted,
            "total": sum(deleted.values())}

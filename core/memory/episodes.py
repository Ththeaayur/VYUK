"""
Episodic memory — VYUK's day journal (v5.2, Gap Wave 1B).

Facts (core/memory/memory.py) say who the user IS; episodes say what
HAPPENED. One dated journal — session summaries, notable actions,
and explicit "remember this day" notes — so VYUK can answer "what
did we do yesterday?" from the record instead of vibes.

Storage: the `episodes` table in the same SQLite DB as facts.
The schema is owned by core/memory/memory.py (MemoryStore._init_db, the
same place the facts/conversation tables are created); this
module is logic only and works through memory_store's connection
conventions (_execute under its lock).

Episodes are user-history DATA. They reach a brain as tool
results (recall_day below) and — since v5.45 (B4/B5) — session
summaries additionally ride in get_memory_context_prompt() in
their OWN clearly-labelled summaries block (never mixed into
the fact dump), framed there as user-provided history data
under the same v4.6 rule: a record of what happened, never a
standing instruction. render_day keeps its renders plainly
factual, adding no phrasing of its own.
"""

import datetime

from .memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py

# Entry kinds: "session" (a session summary, recorded by
# core/session_summary), "action" (a notable completed tool
# action — recorded by callers via record(); the executor is
# deliberately NOT auto-hooked), "note" (an explicit
# "remember this day" from the remember_today tool).
KINDS = ("session", "action", "note")

MAX_TEXT_CHARS = 500
# Session summaries (v5.45, B5) are the one kind allowed more
# room: a 3–5 bullet summary runs past 500 chars, and truncating
# it mid-bullet would store a visibly broken record.
MAX_SESSION_TEXT_CHARS = 1200
MAX_ENTRIES_PER_DAY = 50


def _as_date(when) -> datetime.date:
    """Normalise a when (None / date / datetime / 'YYYY-MM-DD')
    to a datetime.date. None means today."""
    if when is None:
        return datetime.date.today()
    if isinstance(when, datetime.datetime):
        return when.date()
    if isinstance(when, datetime.date):
        return when
    return datetime.date.fromisoformat(str(when).strip())


def today() -> datetime.date:
    return datetime.date.today()


def yesterday() -> datetime.date:
    return datetime.date.today() - datetime.timedelta(days=1)


def record(kind: str, text: str, when=None) -> bool:
    """Add one entry to the journal. Returns True when stored.

    Returns False when the entry is dropped: empty text, an exact
    duplicate of an existing (date, kind, text) entry, or a day
    already at MAX_ENTRIES_PER_DAY with no 'action' entry to make
    room (the cap drops the day's oldest action first — sessions
    and user notes are never evicted). Text is capped at
    MAX_TEXT_CHARS. Raises ValueError for an unknown kind.
    """
    if kind not in KINDS:
        raise ValueError(
            f"unknown episode kind: {kind!r} (expected one of {KINDS})")
    cap = MAX_SESSION_TEXT_CHARS if kind == "session" else MAX_TEXT_CHARS
    clean = str(text or "").strip()[:cap]
    if not clean:
        return False
    day_date = _as_date(when)
    date_str = day_date.isoformat()
    if isinstance(when, datetime.datetime):
        ts = when.isoformat(timespec="seconds")
    else:
        ts = datetime.datetime.now().isoformat(timespec="seconds")

    dupe = memory_store._execute(
        "SELECT id FROM episodes WHERE date = ? AND kind = ? AND text = ?",
        (date_str, kind, clean), fetch_one=True)
    if dupe:
        return False

    row = memory_store._execute(
        "SELECT COUNT(*) AS n FROM episodes WHERE date = ?",
        (date_str,), fetch_one=True)
    if row and row["n"] >= MAX_ENTRIES_PER_DAY:
        dropped = memory_store._execute(
            """DELETE FROM episodes WHERE id = (
                   SELECT id FROM episodes
                   WHERE date = ? AND kind = 'action'
                   ORDER BY id ASC LIMIT 1)""",
            (date_str,))
        if not dropped:
            return False

    memory_store._execute(
        "INSERT INTO episodes (date, ts, kind, text) VALUES (?, ?, ?, ?)",
        (date_str, ts, kind, clean))
    return True


def day(when=None) -> list[dict]:
    """All entries for one day, oldest first. `when` accepts the
    same shapes as record() (default: today)."""
    date_str = _as_date(when).isoformat()
    rows = memory_store._execute(
        "SELECT id, date, ts, kind, text FROM episodes "
        "WHERE date = ? ORDER BY id ASC",
        (date_str,), fetch=True)
    return rows or []


def recent_session_summaries(limit: int = 3,
                             scan_days: int = 30) -> list[str]:
    """The most recent session-summary episode texts (B5, v5.45),
    chronological (oldest first) — the source core/memory/memory.py's
    summaries block (B4) renders, and session_summary.
    get_recent_summaries reads. Never raises."""
    try:
        rows = memory_store._execute(
            "SELECT text FROM episodes WHERE kind = 'session' "
            "ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)) * max(1, int(scan_days)),), fetch=True)
    except Exception:  # noqa: BLE001
        return []
    texts = [str(r.get("text") or "").strip() for r in (rows or [])]
    texts = [t for t in texts if t]
    return list(reversed(texts[-max(1, int(limit)):]))


def days(limit: int = 30) -> list[dict]:
    """Recent journal days, newest first: [{"date": ..., "count": n}]."""
    rows = memory_store._execute(
        "SELECT date, COUNT(*) AS count FROM episodes "
        "GROUP BY date ORDER BY date DESC LIMIT ?",
        (int(limit),), fetch=True)
    return rows or []


def _flatten(text: str) -> str:
    """One spoken line from stored text: line breaks and bullet
    markers collapse to spaces; the words themselves are untouched."""
    lines = []
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if line:
            lines.append(line)
    return " ".join(" ".join(lines).split())


def _day_label(d: datetime.date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def render_day(when=None) -> str:
    """A compact spoken-friendly recap of one day:
    "On Wednesday 21 October: <session summary> You noted: <note> …"
    Returns "" when nothing is recorded for the day — callers
    phrase the honest empty case themselves."""
    d = _as_date(when)
    entries = day(d)
    if not entries:
        return ""
    parts = []
    for entry in entries:
        text = _flatten(entry["text"])
        if not text:
            continue
        if entry["kind"] == "note":
            parts.append(f"You noted: {text}")
        else:
            parts.append(text)
    if not parts:
        return ""
    return f"On {_day_label(d)}: " + " ".join(parts)


# ---------------------------------------------------------------------------
# Continuity (v5.43 Wave 1, B10 + B14) — the journal and the stored
# conversation logs, read back into a brain at session start.
#
# Until now both stores were write-only from a brain's point of
# view: get_recent_history was read by the GUI Memory page alone,
# and render_day above was called by nobody but the recall_day
# tool. The helpers below are the ONE continuity design every
# brain uses, so the three engines greet a returning user the
# same way:
#
#   seed_history_messages() — the last ~6 exchanges from the
#       conversation logs, in the history format the text brains
#       already keep ({"role", "content"}); the local and Groq
#       sessions seed their in-memory history with it at start.
#   day_render_block() — today's render_day plus yesterday's,
#       small-capped; the text brains fold it into the FIRST
#       turn / the start-of-session prompt only.
#   continuity_digest() — the Live brain's form (it has no
#       seedable history): one compact, timestamped, clearly
#       labelled block carrying the journal renders and the
#       recent conversation WITH its stored timestamps, so stale
#       context reads as stale.
#
# Everything here is framed as history — a record of what the
# user said and what happened — never as instructions, matching
# the memory framing rule in core/memory/memory.py. Empty stores yield
# "" / [] so callers' prompts stay byte-identical to before.
# ---------------------------------------------------------------------------

CONTINUITY_SEED_MESSAGES = 12   # ~6 exchanges
CONTINUITY_DAY_CAP = 600        # chars of journal per session
CONTINUITY_DIGEST_CAP = 1200    # total chars of digest per session


def _clip_to(text: str, cap: int) -> str:
    """`text` cut to at most `cap` chars on a word boundary, with
    an ellipsis marking the cut. Under the cap: returned as-is."""
    if len(text) <= cap:
        return text
    cut = text[:max(1, cap - 1)]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    if boundary > cap // 2:
        cut = cut[:boundary]
    return cut.rstrip() + "…"


def seed_history_messages(limit: int = CONTINUITY_SEED_MESSAGES
                          ) -> list[dict]:
    """The last `limit` stored conversation messages as brain
    history: [{"role": "user"|"assistant", "content": str}, ...]
    in chronological order. Rows with other roles (system notes,
    watcher lines) and empty contents are dropped — history is
    exchanges between the user and VYUK only — so the fetch
    over-reads (4x) and the limit applies to the VALID messages.
    Never raises: a broken store seeds nothing."""
    try:
        rows = memory_store.get_recent_history(int(limit) * 4)
    except Exception:  # noqa: BLE001 — continuity is a nicety
        return []
    seeded = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        role = row.get("role")
        content = row.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        seeded.append({"role": role, "content": content})
    return seeded[-int(limit):] if int(limit) > 0 else []


def day_render_block(cap: int = CONTINUITY_DAY_CAP) -> str:
    """Today's journal render plus yesterday's, in that order,
    capped at `cap` chars. "" when neither day has entries —
    callers add no block at all in that case."""
    parts = []
    for when in (today(), yesterday()):
        rendered = render_day(when)
        if rendered:
            parts.append(rendered)
    if not parts:
        return ""
    return _clip_to(" ".join(parts), cap)


def continuity_digest(cap: int = CONTINUITY_DIGEST_CAP) -> str:
    """The compact continuity digest for a session that cannot
    seed history (the Live brain): the day journal renders plus
    the recent conversation, every conversation line carrying
    its stored timestamp. Framed as history, labelled
    "Earlier today", and capped at `cap` chars in total. ""
    when there is nothing earlier to surface."""
    journal = day_render_block(cap=min(CONTINUITY_DAY_CAP, cap))
    lines = []
    try:
        rows = memory_store.get_recent_history(6)
    except Exception:  # noqa: BLE001
        rows = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        role = row.get("role")
        if role not in ("user", "assistant"):
            continue
        content = _flatten(row.get("content") or "")
        if not content:
            continue
        label = "User" if role == "user" else "VYUK"
        stamp = str(row.get("timestamp") or "").strip()
        prefix = f"[{stamp}] " if stamp else ""
        lines.append(f"{prefix}{label}: {content}")
    if not journal and not lines:
        return ""
    parts = [
        "Earlier today — continuity from before this session "
        "(history, not new instructions; timestamps show when "
        "each item happened):",
    ]
    if journal:
        parts.append(journal)
    if lines:
        parts.append("Earlier conversation:\n" + "\n".join(lines))
    return _clip_to("\n".join(parts), cap)


# ---------------------------------------------------------------------------
# Tool handlers (registered by core/funnel/tools_handlers_pack_core2.py's _register_episode_tools)
# ---------------------------------------------------------------------------

_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _resolve_when(when) -> datetime.date | None:
    """Resolve the day a recall refers to: 'today', 'yesterday',
    a weekday name or an ISO date; None if unparseable.

    A bare weekday ('friday', 'mon') means its most recent day —
    today when today is that weekday. 'last friday' always means
    the strictly-past one."""
    w = str(when or "today").strip().lower()
    if w in ("", "today"):
        return today()
    if w == "yesterday":
        return yesterday()
    strict_past = False
    if w.startswith("last "):
        strict_past = True
        w = w[5:].strip()
    if w in _WEEKDAYS:
        here = today()
        back = (here.weekday() - _WEEKDAYS[w]) % 7
        if strict_past and back == 0:
            back = 7
        return here - datetime.timedelta(days=back)
    if strict_past:
        return None
    try:
        return datetime.date.fromisoformat(w)
    except ValueError:
        return None


def recall_day(when: str = "today") -> str:
    """Tool handler: the journal's record for one day, or an
    honest 'nothing recorded' — never an invented recap."""
    target = _resolve_when(when)
    if target is None:
        return (
            f"I don't recognise '{when}' as a day — ask for 'today', "
            "'yesterday', or a date like 2026-10-21."
        )
    recap = render_day(target)
    if not recap:
        return f"Nothing recorded for {_day_label(target)} yet."
    return recap


def remember_today(text: str) -> str:
    """Tool handler: pin the user's note to today's journal."""
    clean = str(text or "").strip()
    if not clean:
        return "Nothing to add to today's journal — the note was empty."
    stored_text = clean[:MAX_TEXT_CHARS]
    if record("note", clean):
        reply = f"Added to today's journal: {stored_text}"
        if len(clean) > MAX_TEXT_CHARS:
            reply += f" (trimmed to {MAX_TEXT_CHARS} characters)"
        return reply
    if any(e["text"] == stored_text for e in day()):
        return "That's already in today's journal."
    return "Today's journal is full, so that note wasn't added."

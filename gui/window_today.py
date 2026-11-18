"""Today safety surface (RET-3).

One panel that shows, for today: the actions VYUK took (from the
shared record), what they cost, what it asked before doing, and
what can be undone — and makes the undo buttons honest. Safety
is the brand, not fine print.

READ SOURCES (named on the surface — the prover checks the
coverage statement matches the readers exactly):
  - shared chain, via the Activity dialog's activity_feed()
    (core/record_family.read_activity, kind "activity");
  - cost ledger, via CostLedger methods only (spend_day,
    spend_by_brain, spend_month — never raw SQL);
  - activity log, for the funnel's confirmation decisions
    (confirm_gate writes one line per decision to
    data/activity.log — see core/confirm_gate._log_decision);
  - kill-chain state, via kill_chain.status().
The day is Asia/Kolkata local, the same day the digest uses
(the machine's localtime is UTC, so the Kolkata date is
computed explicitly and passed to the ledger).

KNOWN GAP, stated on the surface, not silently omitted:
actions recorded ONLY in a per-tool ledger are invisible here
(the Activity feed reads only the shared chain — review M-V4).
That gap is owned by the gaps arc (M-V1/M-V4).

RENDERING (the window_trust.py pattern: pure functions first,
dialog builder at the bottom; no Qt at the top):
  - feed entries render through the Activity dialog's existing
    render path (gui/window_activity.render_feed_text), after
    redaction;
  - spend / confirmations / kill-chain state render through the
    pure functions in this module;
  - funnel decisions render as tool + verdict + reason ONLY —
    never args, never values;
  - file paths render as basenames; no secret values, no
    credential-like values, no full paths, no prompt text
    anywhere on the surface. Every rendered string passes
    through redact_text() first. The surface never reads any
    prompt/response text store (the cost ledger has no text
    columns; the chain carries human action lines only).
  - the confirmations section appears ONLY when a source
    carries confirmation records for today; otherwise it is
    omitted and the surface says so. It never shows a wrong
    count or an empty section presented as complete.
  - Undo is offered ONLY for ids in pending_undo_ids() — never
    for other feed entries, never a spent undo, and never a
    single "undo routine" button (a routine run's steps each
    carry their own undo id, or none).

What this module never does: it never writes the audit chain,
never records a turn, never changes a cap, never changes an
undo executor's verdict, never changes the PF-10 bundle, never
changes the funnel's decision log, and never makes an
un-undoable action look undoable. No timer, no second
briefing, no charging logic (ruling 34 is open).
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from zoneinfo import ZoneInfo

__all__ = [
    "today_date",
    "redact_text",
    "read_confirmations_today",
    "today_view",
    "render_today_text",
    "undo_offer",
    "build_today_dialog",
]

TIMEZONE_NAME = "Asia/Kolkata"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

# The sources the surface reads, in the order the coverage
# statement names them. The prover checks this list against
# the readers in today_view().
COVERAGE_SOURCES = (
    "shared chain (activity feed)",
    "cost ledger",
    "activity log (confirmations)",
    "kill-chain state",
)

COVERAGE_STATEMENT = (
    "Today, from the shared record — sources: "
    + "; ".join(COVERAGE_SOURCES)
    + "."
)

KNOWN_GAP_TEXT = (
    "Not shown here: actions recorded only in a per-tool ledger. "
    "This surface reads the shared chain only (known gap, owned "
    "by the gaps arc M-V1/M-V4)."
)

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# A path token: a drive/UNC/POSIX root followed by at least one
# more segment, preceded by the start of the string or a
# delimiter (space, quote, bracket, =, comma, semicolon). "5/7"
# has no leading slash and is untouched; a URL is untouched
# too — its slashes are preceded by ":" or letters, never by a
# delimiter, and a leading "//" is excluded by the (?!/) guard.
_PATH_RE = re.compile(
    r"(^|[\s\"'\(\[{=,;])"
    r"((?:[A-Za-z]:\\|\\\\|/(?!/))(?:[^\s/\\]+[/\\])+[^\s/\\]*)")

# credential-like key=value / key: value pairs, and bare tokens.
# The key may be quoted (password': '...', "token": "...").
_CRED_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|client[_-]?secret|"
    r"password|passwd|secret|token)\s*['\"]?\s*[:=]\s*['\"]?"
    r"[^\s,;\)\]]+")
_BEARER_RE = re.compile(r"(?i)Bearer\s+\S+")
_SK_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


def redact_text(text) -> str:
    """A display string with paths and secrets removed.

    Full paths collapse to their basename; credential-like
    values become [redacted]; bare sk- tokens and Bearer tokens
    are scrubbed. Plain prose (including counts like "5/7") is
    untouched. Never raises."""
    if text is None:
        return ""
    try:
        out = str(text)
    except Exception:  # noqa: BLE001 — an unprintable value renders empty
        return ""
    try:
        out = _PATH_RE.sub(
            lambda m: m.group(1) + (
                Path(m.group(2).replace("\\", "/")).name or "[path]"),
            out)
        out = _CRED_RE.sub(lambda m: m.group(1) + "=[redacted]", out)
        out = _BEARER_RE.sub("Bearer [redacted]", out)
        out = _SK_RE.sub("[redacted]", out)
        return out
    except Exception:  # noqa: BLE001 — redaction never breaks a render
        return ""


# ---------------------------------------------------------------------------
# Dates (Asia/Kolkata, the digest's day)
# ---------------------------------------------------------------------------


def today_date(now=None) -> datetime.date:
    """Today's date in Asia/Kolkata (naive `now` is machine-local
    wall time; aware `now` is converted)."""
    moment = now if now is not None else datetime.datetime.now()
    if moment.tzinfo is not None:
        return moment.astimezone(TIMEZONE).date()
    # Naive: machine-local wall time — astimezone() assumes the
    # system local timezone (UTC on this machine) before
    # converting to Asia/Kolkata.
    return moment.astimezone(TIMEZONE).date()


def _to_kolkata_date(value):
    """The Asia/Kolkata date of an ISO timestamp string or a
    datetime, or None when unparseable. Naive timestamps are
    machine-local wall time (the machine is UTC — chain `ts`
    is UTC ISO, activity.log lines are naive local)."""
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.datetime.fromisoformat(
                value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(TIMEZONE).date()


# ---------------------------------------------------------------------------
# Confirmations (the funnel's persisted decision log)
# ---------------------------------------------------------------------------

# _log_decision (core/confirm_gate.py) writes one line per
# decision to data/activity.log:
#   2026-11-15T06:00:00 | confirm_gate | tool_name: APPROVED [req abc] (reason)
# The line carries tool + verdict + reason only — never args.
_CONFIRM_RE = re.compile(
    r"^(?P<ts>\S+)\s*\|\s*confirm_gate\s*\|\s*"
    r"(?P<tool>[^:]+):\s*(?P<verdict>APPROVED|DENIED)"
    r"(?:\s*\[req\s+(?P<req>[^\]]+)\])?\s*\((?P<reason>.*)\)\s*$")


def read_confirmations_today(activity_path=None, day=None,
                             now=None) -> dict:
    """Confirmation decisions for one Kolkata day, read from the
    persisted funnel decision log (data/activity.log).

    Returns {"available", "entries", "reason"}: entries are
    {tool, verdict, reason, when, request_id} (reason redacted
    — a reason is free text from the funnel and could carry a
    path or a secret). available False means the log itself
    could not be read; entries empty means no source carries
    confirmation records for that day (the section is omitted,
    not shown empty). Never raises."""
    if day is None:
        day = today_date(now)
    path = (Path(activity_path) if activity_path is not None
            else _default_activity_path())
    try:
        lines = path.read_text(
            encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return {"available": False, "entries": [],
                "reason": "activity log not found"}
    entries = []
    for line in lines:
        match = _CONFIRM_RE.match(line.strip())
        if not match:
            continue
        when = _to_kolkata_date(match.group("ts"))
        if when != day:
            continue
        entries.append({
            "tool": redact_text(match.group("tool").strip()),
            "verdict": match.group("verdict"),
            "reason": redact_text(match.group("reason").strip()),
            "when": match.group("ts"),
            "request_id": match.group("req"),
        })
    return {"available": True, "entries": entries,
            "reason": None if entries else
            "no confirmations recorded today"}


def _default_activity_path() -> Path:
    """data/activity.log under the tree root (via the facade's
    DATA_DIR when reachable, else relative to this file)."""
    try:
        from voice_assistant.core.gui_facade import DATA_DIR

        return Path(DATA_DIR) / "activity.log"
    except Exception:  # noqa: BLE001
        return (Path(__file__).resolve().parent.parent
                / "data" / "activity.log")


# ---------------------------------------------------------------------------
# The view (pure data — no Qt)
# ---------------------------------------------------------------------------


def _facade(name):
    """One facade export, or None. GUI files never import core
    directly."""
    try:
        import voice_assistant.core.gui_facade as _facade_mod

        return getattr(_facade_mod, name, None)
    except Exception:  # noqa: BLE001
        return None


def _today_actions(day, limit=200) -> dict:
    """Today's shared-chain actions, redacted, newest-first.
    Reads through the Activity dialog's activity_feed() (the
    same read the Activity dialog renders) and filters to the
    Kolkata day. A per-tool-ledger action never appears here —
    the feed reads the shared chain only (the named known gap).
    Never raises."""
    try:
        from voice_assistant.gui import window_activity as _act

        feed = _act.activity_feed(int(limit))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "entries": [], "error": str(exc)}
    if not isinstance(feed, dict) or not feed.get("available"):
        return {"available": False, "entries": [],
                "error": str((feed or {}).get("error")
                             or "activity feed not available yet")}
    entries = []
    for entry in feed.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if _to_kolkata_date(entry.get("when") or "") != day:
            continue
        entries.append({
            "action_id": entry.get("action_id"),
            "title": redact_text(entry.get("title", "")),
            "when": str(entry.get("when") or ""),
            "undoable": bool(entry.get("undoable")),
            "reversible": entry.get("reversible"),
        })
    return {"available": True, "entries": entries, "error": None}


def _today_spend(day, db_path=None) -> dict:
    """Today's spend through CostLedger methods only (never raw
    SQL). The headline total is spend_day(Kolkata day); the
    per-brain rows come from spend_by_brain(Kolkata day) with
    the same filter, so the rows always sum to the total. The
    ledger's summary() db_path (a full path) is never read and
    never rendered. Never raises."""
    mod = _facade("cost_ledger")
    if mod is None:
        return {"available": False, "today_usd": 0.0,
                "month_usd": 0.0, "by_brain": [],
                "error": "cost ledger not available yet"}
    try:
        ledger = mod.CostLedger(db_path=db_path) \
            if db_path is not None else mod.CostLedger()
        try:
            day_s = day.isoformat()
            today = float(ledger.spend_day(day_s))
            by_brain = ledger.spend_by_brain(day_s)
            month = float(ledger.spend_month(day_s[:7]))
        finally:
            ledger.close()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "today_usd": 0.0,
                "month_usd": 0.0, "by_brain": [], "error": str(exc)}
    if not isinstance(by_brain, list):
        by_brain = []
    return {"available": True, "today_usd": today,
            "month_usd": month, "by_brain": by_brain, "error": None}


def _kill_chain_state() -> dict:
    """The kill-chain rung state (kill_chain.status()), or
    available False. Never raises."""
    mod = _facade("kill_chain")
    if mod is None:
        return {"available": False,
                "error": "kill chain not available yet"}
    try:
        state = mod.status()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}
    if not isinstance(state, dict):
        return {"available": False,
                "error": "kill chain returned no state"}
    return {"available": True, "rung": int(state.get("rung", 0)),
            "name": redact_text(state.get("name", "")),
            "reason": redact_text(state.get("reason", "")),
            "stopped": bool(state.get("stopped")),
            "tools_frozen": bool(state.get("tools_frozen")),
            "egress_frozen": bool(state.get("egress_frozen")),
            "brain_frozen": bool(state.get("brain_frozen")),
            "safe_mode": bool(state.get("safe_mode")),
            "error": None}


def today_view(now=None, activity_path=None, db_path=None) -> dict:
    """The whole Today surface as plain data. Never raises.

    Keys: date, coverage, known_gap, actions {available,
    entries, error}, spend {available, today_usd, month_usd,
    by_brain, error}, confirmations {available, entries,
    reason}, kill_chain {available, rung, name, reason,
    stopped, tools_frozen, egress_frozen, brain_frozen,
    safe_mode, error}, undo_ids (exactly pending_undo_ids()),
    chain_verified (True/False/None)."""
    day = today_date(now)
    from voice_assistant.gui import window_activity as _act

    try:
        chain = _act.chain_status()
        verified = chain.get("verified")
    except Exception:  # noqa: BLE001
        verified = None
    try:
        undo_ids = [str(i) for i in _act.pending_undo_ids()]
    except Exception:  # noqa: BLE001
        undo_ids = []
    return {
        "date": day.isoformat(),
        "coverage": COVERAGE_STATEMENT,
        "sources": list(COVERAGE_SOURCES),
        "known_gap": KNOWN_GAP_TEXT,
        "actions": _today_actions(day),
        "spend": _today_spend(day, db_path=db_path),
        "confirmations": read_confirmations_today(
            activity_path=activity_path, day=day),
        "kill_chain": _kill_chain_state(),
        "undo_ids": undo_ids,
        "chain_verified": verified,
    }


def undo_offer(view) -> list:
    """The undo buttons the surface offers: exactly the live
    pending undo ids — never a spent undo, never a feed entry
    id, and never a single "undo routine" button. A routine
    run's steps each carry their own undo id, or none; there
    is no routine-level undo to offer."""
    ids = (view or {}).get("undo_ids") or []
    return [str(i) for i in ids]


# ---------------------------------------------------------------------------
# Rendering (pure text — no Qt)
# ---------------------------------------------------------------------------


def render_today_text(view) -> str:
    """The Today view as plain display text. Never raises.

    The confirmations section appears ONLY when the source
    carries confirmation records for today; otherwise one line
    says the section is omitted and why — never an empty
    section presented as complete."""
    try:
        if not isinstance(view, dict):
            return "Today is not available right now."
        lines = [f"Today — {view.get('date', '')} (Asia/Kolkata)",
                 view.get("coverage", ""),
                 view.get("known_gap", ""),
                 ""]
        # Actions, through the Activity dialog's render path.
        actions = view.get("actions") or {}
        if not actions.get("available"):
            lines.append("Actions today: not yet available — "
                         + str(actions.get("error") or ""))
        else:
            entries = actions.get("entries") or []
            if not entries:
                lines.append("Actions today: nothing recorded yet.")
            else:
                from voice_assistant.gui.window_activity import (
                    render_feed_text)

                lines.append(f"Actions today ({len(entries)}):")
                lines.append(render_feed_text(
                    {"available": True, "entries": entries}))
        lines.append("")
        # Spend: totals + per-brain rows only (never prompt text —
        # the ledger has no text columns, and db_path is never read).
        spend = view.get("spend") or {}
        if not spend.get("available"):
            lines.append("Spend today: not yet available — "
                         + str(spend.get("error") or ""))
        else:
            lines.append(
                f"Spend today: ${spend.get('today_usd', 0):.4f} "
                f"(this month: ${spend.get('month_usd', 0):.4f})")
            for row in spend.get("by_brain") or []:
                lines.append(
                    f"  {row.get('brain', '')} · "
                    f"{row.get('model', '')}: "
                    f"{row.get('turns', 0)} turn(s), "
                    f"${row.get('cost_usd') or 0:.4f}")
        lines.append("")
        # Confirmations: present only with records.
        confs = view.get("confirmations") or {}
        entries = confs.get("entries") or []
        if entries:
            lines.append(f"Confirmations asked today ({len(entries)}):")
            for entry in entries:
                lines.append(
                    f"  {entry.get('tool', '')} — "
                    f"{entry.get('verdict', '')}"
                    + (f" ({entry.get('reason', '')})"
                       if entry.get("reason") else ""))
        else:
            lines.append(
                "Confirmations: section omitted — "
                + str(confs.get("reason")
                      or "no confirmation records for today") + ".")
        lines.append("")
        # Kill chain.
        kill = view.get("kill_chain") or {}
        if not kill.get("available"):
            lines.append("Kill chain: not yet available — "
                         + str(kill.get("error") or ""))
        else:
            frozen = []
            if kill.get("tools_frozen"):
                frozen.append("tools frozen")
            if kill.get("egress_frozen"):
                frozen.append("egress frozen")
            if kill.get("brain_frozen"):
                frozen.append("brain frozen")
            state = (f"rung {kill.get('rung', 0)} "
                     f"({kill.get('name', '')})")
            if frozen:
                state += " — " + ", ".join(frozen)
            if kill.get("reason"):
                state += f" — {kill.get('reason')}"
            lines.append(f"Kill chain: {state}")
        lines.append("")
        # Undo: exactly the pending ids.
        ids = undo_offer(view)
        if ids:
            lines.append(
                f"Undoable right now ({len(ids)}): "
                + ", ".join(ids))
        else:
            lines.append("Undoable right now: nothing pending.")
        verified = view.get("chain_verified")
        if verified is True:
            lines.append("Record chain: verified.")
        elif verified is False:
            lines.append(
                "Record chain: CHAIN MISMATCH — records may be "
                "tampered with.")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return "Today is not available right now."


# ---------------------------------------------------------------------------
# Dialog builder — Qt imported lazily here (see window_activity
# for the pattern). Returns (dialog, widgets) without exec()ing.
# ---------------------------------------------------------------------------


def build_today_dialog(parent=None, db_path=None):
    """The Today dialog: the rendered view plus one Undo button
    per PENDING undo id (from pending_undo_ids() — never a
    spent undo, never a feed entry id, never a single "undo
    routine" button). Read-only apart from the undo buttons,
    whose verdicts come from the executors themselves."""
    from PySide6.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
    from voice_assistant.gui.style import DIALOG_STYLE
    from voice_assistant.gui.window_activity import perform_undo

    dlg = QDialog(parent)
    dlg.setWindowTitle("Today")
    dlg.setStyleSheet(DIALOG_STYLE)
    dlg.resize(720, 560)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 20, 20, 16)
    lay.setSpacing(10)
    status = QLabel("")
    status.setObjectName("muted")
    status.setWordWrap(True)
    lay.addWidget(status)
    view = QListWidget()
    lay.addWidget(view, stretch=1)
    undo_label = QLabel("Undoable actions:")
    undo_box = QWidget()
    undo_layout = QVBoxLayout(undo_box)
    undo_layout.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(undo_label)
    lay.addWidget(undo_box)
    btn_row = QHBoxLayout()
    btn_row.addStretch(1)
    btn_refresh = QPushButton("Refresh")
    btn_close = QPushButton("Close")
    btn_row.addWidget(btn_refresh)
    btn_row.addWidget(btn_close)
    btn_close.setDefault(True)
    lay.addLayout(btn_row)

    def _fill():
        result = today_view(db_path=db_path)
        status.setText(
            f"Today — {result.get('date', '')} (Asia/Kolkata). "
            f"{result.get('coverage', '')}")
        view.clear()
        for line in render_today_text(result).splitlines():
            view.addItem(line)
        while undo_layout.count():
            item = undo_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        ids = undo_offer(result)
        undo_label.setVisible(bool(ids))
        undo_box.setVisible(bool(ids))
        for action_id in ids:
            btn = QPushButton(f"Undo {action_id}")
            btn.clicked.connect(
                lambda _=False, aid=action_id: _do_undo(aid))
            undo_layout.addWidget(btn)
        _fill.buttons = undo_layout.count()
        _fill.latest = result

    def _do_undo(action_id):
        result = perform_undo(action_id)
        _fill()
        return result

    btn_refresh.clicked.connect(_fill)
    btn_close.clicked.connect(dlg.accept)
    _fill.latest = None
    _fill.buttons = 0
    _fill()
    widgets = {"status": status, "view": view,
               "refresh": btn_refresh, "close": btn_close,
               "fill": _fill, "undo_label": undo_label,
               "undo_box": undo_box, "do_undo": _do_undo}
    return dlg, widgets

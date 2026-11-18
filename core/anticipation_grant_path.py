"""Wave 8 Build 1 — Anticipation-grant PATH, suggestion cards only.

This module builds the PATH to anticipation, not the freedom.
The grant itself is NOT granted here: it waits on the owner's
explicit opt-in (v6 section 4, anticipation grant — OPEN), and
no caller in the shipped tree today holds a live grant. A test
grant fixture is data for a test, never a live grant, and this
module never creates, persists, or upgrades a grant on its own.

WHAT THIS MODULE IS
-------------------
* A suggestion card: plain text (``text``), its ``source`` (always
  "caller-supplied context"), the ``reason`` it was suggested, the
  plain-language ``action`` it WOULD take (a description, never
  executed by this module), and an ``expiry`` the caller supplied.
  A card is a suggestion, never an act: this module executes
  nothing, and a card's action is never run by any function here.
* A grant record: ``granted`` (False by default), ``grantor``,
  ``scope``, and a caller-supplied ``timestamp`` string. A missing
  grant record is a refusal, never a default. Only the exact scope
  ``"suggestion-cards"`` opens the path; any other scope — capture,
  pre-warming, watch-and-learn, screen memory, clipboard history,
  or anything else — is refused whole.
* A gate: ``gate_cards()`` returns cards ONLY when the grant
  record says granted=True for exactly the suggestion-cards
  scope, with a non-empty grantor and timestamp. Every other
  case returns zero cards with a stated reason, never an empty
  pretence and never a degraded card.

WHAT THIS MODULE IS NOT — ABSENCE OF CAPTURE, VERIFIED
------------------------------------------------------
* It performs NO capture: it imports no watcher, screen, or
  clipboard module, it starts no background watcher, it schedules
  no job, and it pre-warms nothing. There is no thread, no timer,
  and no listener object anywhere in this module — proven by the
  AST test in tests/test_wave8_antic.py, which bans those imports
  and names, and by the runtime test, which shows no watcher or
  scheduler object exists after the path runs.
* It does NOT reuse the v5.62 anticipation capture session
  (``core/anticipation.py``): that session turns caller-
  supplied foreground observations into events, which is a
  capture surface. This path takes caller-supplied CONTEXT only
  (topics the caller lists, in plain text) and turns it into
  suggestion cards. The two are deliberately different shapes,
  and this module never imports the v5.62 module.
* Capture-derived input is refused whole: a context item that
  claims a capture source (screen, clipboard, window title,
  screenshot, keystroke, watcher, continuous monitoring) or that
  carries a ``captured`` flag is refused, and no card is produced
  for any item in that call — a tainted call never yields a
  partial card set.
* It does NOT grant anything: ``build_grant_record()`` only
  validates a record the caller hands in. A record with
  granted=True is still only data until a live caller — the
  owner, explicitly, outside this module — supplies it through
  the production path. Nothing in this module calls itself a
  grant, and no function here can turn a False record True.

FAIL-CLOSED, WHOLE-CALL
-----------------------
Any refused part refuses the whole call: a missing grant, a
False grant, a wrong-scope grant, an incomplete grant (granted
but grantor or timestamp empty), a capture-derived item, an
empty topic, an oversize call (>50 context items, >500 chars
per text field), or a malformed item all return zero cards
with a stated reason. A refused call never yields cards for
the "good" items alongside the refused ones.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only (``dataclasses``). No new
dependency, no telemetry write, no file write, no database
access, no network access. Nothing here reads or writes the
install tree's ``data/`` directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "SUGGESTION_CARDS_SCOPE",
    "MAX_CONTEXT_ITEMS",
    "MAX_FIELD_CHARS",
    "CAPTURE_SOURCES",
    "GrantRecord",
    "SuggestionCard",
    "build_grant_record",
    "inspect_grant",
    "gate_cards",
    "produce_cards",
]

#: The one and only scope that opens this path. Any other scope
#: is refused whole — including scopes that sound close, like
#: "suggestion-cards-and-capture" or "anticipation".
SUGGESTION_CARDS_SCOPE = "suggestion-cards"

#: Call-size caps: a suggestion call is small, caller-supplied
#: context. Anything larger is refused, not truncated — truncation
#: would silently drop the caller's context.
MAX_CONTEXT_ITEMS = 50
MAX_FIELD_CHARS = 500

#: Source labels that mark an input as capture-derived. An item
#: naming any of these as its source is refused whole, as is an
#: item carrying a truthy "captured" flag.
CAPTURE_SOURCES = frozenset({
    "screen", "screenshot", "clipboard", "window-title",
    "window title", "keystroke", "keylogging", "watcher",
    "continuous", "continuous-monitoring", "screen-memory",
    "clipboard-history", "foreground", "activity-stream",
    "captured",
})


@dataclass(frozen=True)
class GrantRecord:
    """An anticipation grant record, as handed in by the caller.

    ``granted`` defaults to False. A record is only usable by
    the gate when granted=True AND scope is exactly
    SUGGESTION_CARDS_SCOPE AND grantor and timestamp are both
    non-empty. This class validates shape; it does not confer
    authority — authority comes from the live caller (the owner,
    explicitly) supplying the record, never from this module.
    """
    granted: bool = False
    grantor: str = ""
    scope: str = ""
    timestamp: str = ""

    def __post_init__(self):
        object.__setattr__(self, "granted", bool(self.granted))
        object.__setattr__(self, "grantor", str(self.grantor or "").strip())
        object.__setattr__(self, "scope", str(self.scope or "").strip())
        object.__setattr__(self, "timestamp", str(self.timestamp or "").strip())

    def usable_reason(self) -> str | None:
        """None when this record opens the path, else the reason
        it does not. A missing or False record is a refusal."""
        if not self.granted:
            return ("refused — no anticipation grant: the grant is "
                    "not granted (capture stays OFF, no cards)")
        if self.scope != SUGGESTION_CARDS_SCOPE:
            return (f"refused — grant scope {self.scope!r} is not "
                    f"{SUGGESTION_CARDS_SCOPE!r}; only suggestion "
                    "cards are in scope, capture and pre-warming "
                    "are not granted")
        if not self.grantor:
            return "refused — grant record names no grantor"
        if not self.timestamp:
            return "refused — grant record carries no timestamp"
        return None


@dataclass(frozen=True)
class SuggestionCard:
    """One suggestion card: a suggestion, never an act.

    ``action`` is a plain-language description of what the card
    WOULD take if the user accepts it. No function in this
    module runs it, and no card here has ever been executed.
    ``expiry`` is caller-supplied text (or "" when the caller
    gave none); cards do not auto-expire by a clock here, since
    this module runs no clock, timer, or scheduler.
    """
    text: str
    source: str = "caller-supplied context"
    reason: str = ""
    action: str = ""
    expiry: str = ""
    kind: str = "SUGGESTION_CARD"


def build_grant_record(granted=False, grantor="", scope="",
                       timestamp=""):
    """Validate a caller-supplied grant record (data in, record
    out). This never grants anything: it only checks shape, and
    a record with granted=True is still just data until a live
    caller supplies it. Raises ValueError for a non-string
    grantor/scope/timestamp, and for an oversize field."""
    for name, value in (("grantor", grantor), ("scope", scope),
                        ("timestamp", timestamp)):
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        if len(value) > MAX_FIELD_CHARS:
            raise ValueError(f"{name} exceeds {MAX_FIELD_CHARS} chars")
    return GrantRecord(granted=bool(granted), grantor=grantor,
                       scope=scope, timestamp=timestamp)


def inspect_grant(grant) -> dict:
    """Describe a grant record without granting anything.

    ``grant`` may be None (no record at all) or a GrantRecord.
    Anything else is reported as malformed, never treated as a
    grant. The result states plainly whether the path is open
    and why, and names the one scope that opens it."""
    if grant is None:
        return {
            "ok": True,
            "has_record": False,
            "granted": False,
            "scope": None,
            "path_open": False,
            "reason": ("no anticipation grant record; the grant "
                       "itself waits on the owner's explicit "
                       "opt-in, capture stays OFF, no cards"),
            "allowed_scope": SUGGESTION_CARDS_SCOPE,
        }
    if not isinstance(grant, GrantRecord):
        return {
            "ok": False,
            "has_record": True,
            "granted": False,
            "scope": None,
            "path_open": False,
            "reason": "malformed grant record — not a GrantRecord",
            "allowed_scope": SUGGESTION_CARDS_SCOPE,
        }
    reason = grant.usable_reason()
    return {
        "ok": True,
        "has_record": True,
        "granted": bool(grant.granted),
        "grantor": grant.grantor,
        "scope": grant.scope,
        "timestamp": grant.timestamp,
        "path_open": reason is None,
        "reason": reason or "grant opens the suggestion-cards path only",
        "allowed_scope": SUGGESTION_CARDS_SCOPE,
    }


def _clean_field(value, name) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    text = value.strip()
    if len(text) > MAX_FIELD_CHARS:
        raise ValueError(f"{name} exceeds {MAX_FIELD_CHARS} chars")
    return text


def _validate_context(context) -> list:
    """Validate caller-supplied context into clean items, or
    raise ValueError. Capture-derived items refuse the whole
    call: a tainted call never yields a partial card set."""
    if context is None:
        return []
    if not isinstance(context, (list, tuple)):
        raise ValueError("context must be a list of items")
    if len(context) > MAX_CONTEXT_ITEMS:
        raise ValueError(
            f"context exceeds {MAX_CONTEXT_ITEMS} items")
    cleaned = []
    for index, item in enumerate(context):
        if not isinstance(item, dict):
            raise ValueError(f"context item {index} must be a dict")
        if item.get("captured"):
            raise ValueError(
                f"context item {index} is capture-derived "
                "(captured flag set); refused whole")
        source = str(item.get("source", "") or "").strip().lower()
        if source in CAPTURE_SOURCES:
            raise ValueError(
                f"context item {index} is capture-derived "
                f"(source {source!r}); refused whole")
        topic = _clean_field(item.get("topic", ""), f"topic {index}")
        if not topic:
            raise ValueError(f"context item {index} has an empty topic")
        reason = _clean_field(item.get("reason", ""), f"reason {index}")
        action = _clean_field(item.get("action", ""), f"action {index}")
        expiry = _clean_field(item.get("expiry", ""), f"expiry {index}")
        cleaned.append({
            "topic": topic,
            "reason": reason or "caller listed this as current work",
            "action": action,
            "expiry": expiry,
        })
    return cleaned


def produce_cards(context, grant) -> dict:
    """Produce suggestion cards from caller-supplied context,
    gated on the grant record. Returns a dict with ``ok``,
    ``cards`` (list of card dicts, possibly empty), and
    ``reason``. Zero cards + a stated reason is the honest
    answer whenever the gate is closed — never an empty
    pretence, never a degraded card.

    Cards are produced ONLY from the caller's context items:
    one card per item, in order. No capture, no watcher, no
    pre-warming, no scheduled job is involved at any point.
    """
    inspection = inspect_grant(grant)
    if not inspection["ok"]:
        return {"ok": False, "cards": [], "reason": inspection["reason"]}
    if not inspection["path_open"]:
        return {"ok": False, "cards": [], "reason": inspection["reason"]}
    try:
        items = _validate_context(context)
    except ValueError as exc:
        return {"ok": False, "cards": [], "reason": f"refused — {exc}"}
    cards = [
        {
            "kind": "SUGGESTION_CARD",
            "text": f"Consider: {item['topic']}",
            "source": "caller-supplied context",
            "reason": item["reason"],
            "action": item["action"],
            "expiry": item["expiry"],
            "executed": False,
        }
        for item in items
    ]
    return {
        "ok": True,
        "cards": cards,
        "reason": ("suggestion cards produced from caller-supplied "
                   "context under a suggestion-cards grant; no "
                   "capture, no pre-warming, nothing executed"),
    }


def gate_cards(context, grant) -> dict:
    """The gate: the only production entry point. Identical to
    produce_cards — the gate IS the producer, so there is no
    ungated path to cards anywhere in this module."""
    return produce_cards(context, grant)

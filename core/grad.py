"""Wave 9 Build 10: queued graduations 1 — TENSION
graduation step 1 (continuous-display gate enforcement,
NO capture) plus a graduation ledger.

Per build_flow_v6.md Wave 9, queued graduations re-enter
only by their route, and the graduation rule holds every
time: reason-clearance evidence PLUS a buildable form,
both, never a graduation on a claim. This build is the
first TENSION graduation step, and its evidence is named
here, not assumed:

* The owner ruling of 2026-10-26 ~21:59 IST: continuous
  forms become a first-open opt-in OPTION, default OFF,
  local-only, asked in plain language as soon as the
  person opens the app, remaining as an option in
  Settings all the way, reversible at any time. Building
  the option is not a grant; each user's own choice is
  the grant.
* The first-open opt-in build (Wave 9, before this one):
  the choice mechanism plus the gate ONLY — no capture
  code — with the 7-day retention figure chosen by the
  builder, NOT ruled by the owner, and overrulable by
  him at any time.

WHAT THIS MODULE IS
-------------------
* A continuous-display gate ENFORCEMENT descriptor: the
  check any future capture build must call BEFORE any
  capture. It reuses the first-open module's own gate
  (core/firstopen_optin.py — reused, not rebuilt):
  enabled ONLY when the caller-supplied choice record
  says allowed=True for exactly scope
  "continuous-display", with a non-empty chooser,
  timestamp, retention rule, and deletion rule. The
  result states the recorded retention rule and the
  indicator requirement in plain words, so a future
  capture build cannot claim it checked the gate
  without also carrying the rule and the indicator.
  Any other scope, a missing record, a False record,
  or a missing rule returns disabled with a stated
  reason — never defaulted on, never partially on.
* A retention-rule descriptor: caller-supplied
  retention days in, a plain-language rule record out.
  With no days supplied, the record states the current
  7-day figure AND states plainly that it is the
  builder-chosen default, NOT ruled by the owner, who
  may change it at any time. A caller-supplied figure
  is recorded as supplied, with the same note: the
  owner may change it at any time. This module never
  attributes any retention figure to the owner.
* An indicator-requirement descriptor: states, in plain
  words, that a small sign on screen must show while
  continuous display is on, that switching it off stops
  it watching anything new, and that switching off does
  NOT by itself delete what was kept — the per-feature
  delete path is named (the same place in Settings,
  the per-feature delete built with the opt-in), and
  the global delete-everywhere rule (PF-3) is stated
  as still OPEN, never claimed.
* A graduation-ledger descriptor: caller-supplied item
  id plus gate-evidence reference plus buildable-form
  description plus still-owed list in, a ledger record
  out. The record states evidence plus form plus what
  is still owed — and it is never a claim the item is
  built, shipped, or field-proven: the record carries
  built False, shipped False, field_proven False, and
  a note saying so. Still-owed items are echoed as
  caller-supplied, not verified by this module.
* A re-validation rule for recording a ledger entry:
  the entry is re-built from its own fields and must
  match exactly, or it is refused, never written on
  trust.

WHAT THIS MODULE IS NOT — NO CAPTURE, VERIFIED
----------------------------------------------
* It performs NO continuous capture: it imports no
  watcher, screen, clipboard, keystroke, or sensor
  code, starts no watcher, schedules no job, and
  pre-warms nothing. It reuses only the first-open
  choice module, which itself captures nothing — proven
  by the AST test in tests/test_grad_w9.py.
* It grants NOTHING and chooses NOTHING for any user:
  the gate only checks a record the caller hands in.
  A record with allowed=True is still only data until
  a live user supplies it explicitly through the
  first-open prompt or the Settings entry. No function
  here can turn a False record True, and a test
  fixture choice is data for a test, never a live
  choice.
* It does NOT decide PF-3: the global
  delete-everywhere rule is still OPEN, and every
  output that mentions deletion says so. The only
  delete named here is the per-feature delete already
  built with the opt-in.
* It does NOT build any actual capture feature: screen
  memory, clipboard history, and every other
  continuous form are still separate Wave 9 TENSION
  builds, and each must call this gate and honour the
  recorded rule before capturing anything.

FAIL-CLOSED, WHOLE-CALL
-----------------------
Any refused part refuses the whole call: an action other
than "describe"; an unknown descriptor kind; an
unexpected field for the kind; a capture-shaped input
(capture, watcher, screenshot, clipboard, keystroke,
pre-warm, listen, screen-memory); a deletion rule
claiming everything everywhere (that is PF-3, still
open); a ledger form or evidence claiming the item is
built, shipped, or field-proven, or claiming PF-3 is
decided; a missing or empty required field; a
retention figure that is not a positive whole number
of days, or over 10 years (not a plausible kept-notes
rule — refused, not silently capped).

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only (``re``). No new
dependency, no telemetry write, no database access, no
network access. Nothing here reads or writes the
install tree's ``data/`` directory.
"""

from __future__ import annotations

import re

from . import firstopen_optin as firstopen

__all__ = [
    "CONTINUOUS_DISPLAY_SCOPE",
    "DEFAULT_RETENTION_DAYS",
    "MAX_RETENTION_DAYS",
    "describe_gate",
    "describe_retention",
    "describe_indicator",
    "build_ledger_entry",
    "describe",
    "validate_recordable_graduation",
]

#: The one and only scope that opens continuous display,
#: inherited from the first-open module — never redefined
#: here, so the two can never drift apart.
CONTINUOUS_DISPLAY_SCOPE = firstopen.CONTINUOUS_DISPLAY_SCOPE

#: The current retention default, in days. Builder-chosen,
#: NOT ruled by the owner — every output that states it
#: says so, and the owner may change it at any time.
DEFAULT_RETENTION_DAYS = 7

#: A kept-notes rule over 10 years is refused, not
#: silently capped — capping would change the caller's
#: stated rule without saying so.
MAX_RETENTION_DAYS = 3650

#: Indicator wording, shared with the first-open prompt so
#: the requirement can never drift from the promise.
INDICATOR_TEXT = "a small sign on screen that tells you it is on"

#: Capture-shaped words: a caller field carrying one as a
#: whole word is a capture action, not a description, and
#: refuses the whole call. "watch"/"watching" alone is NOT
#: in this list: the first-open prompt itself calls the
#: form "watching what you do", so the word alone does
#: not make a field a capture action.
_CAPTURE_WORDS = frozenset({
    "capture", "captures", "captured", "capturing",
    "watcher", "watchers", "screenshot", "screenshots",
    "clipboard", "keystroke", "keystrokes",
    "prewarm", "pre-warm", "prewarming",
    "listen", "listens", "listening",
    "screen-memory", "screenmemory", "spy",
})

#: Completion claims a ledger form or evidence must never
#: carry: the ledger records evidence and form, never a
#: claim the item is built, shipped, or field-proven,
#: and never a claim that PF-3 is decided.
_CLAIM_PHRASES = (
    "is built", "has been built", "already built",
    "is shipped", "has shipped", "already shipped",
    "field-proven", "field proven",
    "pf-3 decided", "pf-3 closed", "pf-3 granted",
    "global delete enabled", "delete everywhere enabled",
)


def _refuse(reason):
    return {"ok": False, "reason": reason}


def _words(text):
    return set(re.findall(r"[a-z][a-z\-]*", text.lower()))


def _check_text(text, field):
    """Refuse whole on a capture-shaped word or a
    completion claim anywhere in one free-text field."""
    if not isinstance(text, str):
        return f"{field} must be a string"
    lowered = text.lower()
    for phrase in _CLAIM_PHRASES:
        if phrase in lowered:
            return f"{field} carries a refused claim: {phrase}"
    if _words(text) & _CAPTURE_WORDS:
        return f"{field} is shaped as a capture action"
    return None


def _check_action(action):
    if action != "describe":
        return f"only 'describe' is performed, not {action!r}"
    return None


def _check_fields(fields, allowed, kind):
    if set(fields) - set(allowed):
        return (f"{kind} descriptor carries unexpected fields: "
                f"{sorted(set(fields) - set(allowed))}")
    return None


def describe_gate(allowed=False, chooser="", scope="",
                  timestamp="", retention_rule="",
                  deletion_rule="", form="",
                  action="describe"):
    """Gate enforcement descriptor: check a
    caller-supplied choice record through the first-open
    gate, and state the recorded rule and the indicator
    requirement with the answer. This grants nothing and
    captures nothing — it only reports what the record
    the caller handed in does and does not open."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    for field, value in (("chooser", chooser), ("scope", scope),
                         ("timestamp", timestamp),
                         ("retention_rule", retention_rule),
                         ("deletion_rule", deletion_rule),
                         ("form", form)):
        err = _check_text(value, field)
        if err:
            return _refuse(err)
    if deletion_rule and "everywhere" in deletion_rule.lower():
        return _refuse(
            "deletion rule must be per-feature; deleting "
            "everything everywhere is the PF-3 global rule, "
            "which is still open")
    try:
        record = firstopen.build_choice_record(
            allowed=allowed, chooser=chooser, scope=scope,
            timestamp=timestamp, retention_rule=retention_rule,
            deletion_rule=deletion_rule, form=form)
    except ValueError as exc:
        return _refuse(f"choice record does not validate: {exc}")
    gate = firstopen.gate_continuous(record)
    return {
        "ok": True,
        "kind": "CONTINUOUS_GATE_CHECK",
        "enabled": bool(gate["enabled"]),
        "reason": gate["reason"],
        "allowed_scope": CONTINUOUS_DISPLAY_SCOPE,
        "retention_rule": record.retention_rule,
        "deletion_rule": record.deletion_rule,
        "indicator": INDICATOR_TEXT,
        "indicator_required": True,
        "local_only": True,
        "default_off": True,
        "captured": False,
        "granted": False,
        "note": ("The gate a future capture build must call "
                 "before capturing anything. Enabled only "
                 "on the user's own explicit choice for "
                 "exactly continuous-display, with a named "
                 "retention rule and deletion rule. While "
                 "on, a small sign on screen must show; "
                 "switching off stops new watching but does "
                 "not by itself delete what was kept — "
                 "deleting what was kept is the per-feature "
                 "delete in the same place in Settings, "
                 "and the global delete-everywhere rule "
                 "(PF-3) is still open."),
    }


def describe_retention(retention_days=None, action="describe"):
    """Retention-rule descriptor: caller-supplied days in,
    a plain-language rule record out. With no days, the
    record states the current 7-day default AND states
    plainly that it is builder-chosen, not ruled by the
    owner, who may change it at any time."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if retention_days is None:
        days = DEFAULT_RETENTION_DAYS
        supplied = False
    else:
        if isinstance(retention_days, bool) \
                or not isinstance(retention_days, int):
            return _refuse(
                "retention_days must be a whole number of days")
        if retention_days <= 0:
            return _refuse(
                "retention_days must be a positive number of days")
        if retention_days > MAX_RETENTION_DAYS:
            return _refuse(
                "retention_days over 10 years is not a "
                "plausible kept-notes rule")
        days = retention_days
        supplied = True
    if days == 1:
        rule = "kept notes are thrown away after 1 day"
    else:
        rule = f"kept notes are thrown away after {days} days"
    if supplied:
        note = ("A retention rule as supplied by the caller. "
                "The owner may change it at any time; this "
                "figure was never claimed as his ruling.")
    else:
        note = ("The current default: kept notes are thrown "
                "away after 7 days. This figure was chosen "
                "by the builder, NOT ruled by the owner, "
                "and the owner may change it at any time.")
    return {
        "ok": True,
        "kind": "RETENTION_RULE",
        "retention_days": days,
        "rule": rule,
        "supplied_by_caller": supplied,
        "owner_ruled": False,
        "note": note,
    }


def describe_indicator(action="describe"):
    """Indicator-requirement descriptor: states, in plain
    words, the sign that must show while continuous
    display is on, what switching off does and does not
    do, and that the global delete rule is still open."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    return {
        "ok": True,
        "kind": "INDICATOR_REQUIREMENT",
        "indicator": INDICATOR_TEXT,
        "indicator_required": True,
        "turn_off_stops_new_watching": True,
        "turn_off_deletes_kept_data": False,
        "delete_path": ("the same place in Settings — the "
                         "per-feature delete for continuous "
                         "data"),
        "global_delete_rule": "PF-3, still open",
        "note": ("While continuous display is on, a small "
                 "sign on screen must show that tells you "
                 "it is on. Switching it off stops it "
                 "watching anything new, but it does not "
                 "by itself delete what was kept — deleting "
                 "what was kept is the per-feature delete "
                 "in the same place in Settings. Deleting "
                 "everything everywhere is the PF-3 global "
                 "rule, which is still open and is not "
                 "claimed here."),
    }


def build_ledger_entry(item_id, evidence, form, still_owed,
                       action="describe"):
    """Graduation-ledger descriptor: item id plus
    gate-evidence reference plus buildable-form
    description plus still-owed list in, a ledger record
    out. The record states evidence plus form plus what
    is still owed — it never claims the item is built,
    shipped, or field-proven, and still-owed items are
    echoed as caller-supplied, not verified here."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    for field, value in (("item_id", item_id),
                         ("evidence", evidence),
                         ("form", form)):
        if not isinstance(value, str) or not value.strip():
            return _refuse(
                f"{field} must be a non-empty string")
        err = _check_text(value, field)
        if err:
            return _refuse(err)
    if not isinstance(still_owed, list):
        return _refuse("still_owed must be a list")
    for entry in still_owed:
        if not isinstance(entry, str) or not entry.strip():
            return _refuse(
                "still_owed entries must be non-empty strings")
    return {
        "ok": True,
        "kind": "GRADUATION_LEDGER_ENTRY",
        "item_id": item_id.strip(),
        "evidence": evidence.strip(),
        "form": form.strip(),
        "still_owed": list(still_owed),
        "built": False,
        "shipped": False,
        "field_proven": False,
        "note": ("A graduation ledger entry: the evidence "
                 "that cleared the item's gate, the "
                 "buildable form it graduated in, and "
                 "what is still owed. This entry is not a "
                 "claim the item is built, shipped, or "
                 "field-proven, and the still-owed items "
                 "are as supplied by the caller, not "
                 "verified by this module."),
    }


_KIND_FIELDS = {
    "gate": ("allowed", "chooser", "scope", "timestamp",
             "retention_rule", "deletion_rule", "form"),
    "retention": ("retention_days",),
    "indicator": (),
    "ledger": ("item_id", "evidence", "form", "still_owed"),
}


def describe(kind, action="describe", **fields):
    """Dispatch to one descriptor kind. Unknown kinds and
    unexpected fields for the kind refuse whole — a
    descriptor never silently ignores an input it was
    handed."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if kind not in _KIND_FIELDS:
        return _refuse(
            "kind must be one of gate, retention, indicator, ledger")
    err = _check_fields(fields, _KIND_FIELDS[kind], kind)
    if err:
        return _refuse(err)
    if kind == "gate":
        return describe_gate(action=action, **fields)
    if kind == "retention":
        return describe_retention(action=action, **fields)
    if kind == "indicator":
        return describe_indicator(action=action)
    return build_ledger_entry(action=action, **fields)


def validate_recordable_graduation(fields):
    """Re-run the ledger builder for a graduation record
    and return the rebuilt entry. A record is never
    written on trust: a caller-supplied JSON could claim
    anything, so the entry is rebuilt from its own fields
    and the rebuilt form is what gets recorded."""
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    allowed = {"item_id", "evidence", "form", "still_owed"}
    if set(fields) - allowed:
        raise ValueError("record carries unexpected fields")
    rebuilt = build_ledger_entry(
        fields.get("item_id"), fields.get("evidence"),
        fields.get("form"), fields.get("still_owed"))
    if not rebuilt.get("ok"):
        raise ValueError(rebuilt.get("reason", "rebuild refused"))
    # The rebuilt entry is what gets recorded, never the
    # presented fields: a tampered presentation that does
    # not rebuild is refused above.
    return rebuilt

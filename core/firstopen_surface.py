"""First-open prompt surface — pure flag + choice logic.

This module owns the FIRST-OPEN PROMPT's persisted state and
nothing else. It builds no window, starts no capture, and
watches nothing. The GUI dialog (gui/firstopen_prompt.py)
renders the descriptor from core/firstopen_optin.py; the
startup call site (MainWindow._run_first_open_prompt) and
the Settings card (Privacy & permissions) both read and
write through here, so the first-open choice and the
Settings choice are ONE choice, never two keys that drift.

STATE — exactly two QSettings keys, both under "privacy/":
* ``privacy/firstopen_prompt_seen`` (bool): the prompt has
  been shown (or attempted — a failed prompt counts as
  asked-with-default-No, see below) on a first open. Once
  True, the prompt is never shown again. Existing installs
  never enter the first-run path that checks this flag, so
  they are never re-asked.
* ``privacy/continuous_display_choice`` (JSON string): the
  user's ChoiceRecord, exactly the schema
  core/firstopen_optin.build_choice_record validates
  (allowed, chooser, scope, timestamp, retention_rule,
  deletion_rule, form). Absent, empty, or malformed means
  NO record, and the gate stays disabled — fail-closed.
  This is deliberately a DIFFERENT key from anything the
  session-scoped Screen watching card uses: screen
  watching is off at every launch by design and is never
  persisted, while this choice IS persisted and reversible
  in Settings at any time.

DEFAULT OFF, WHOLE-CALL:
* No key, a False record, a wrong-scope record, or an
  incomplete record all gate to disabled with a stated
  reason. Nothing here can turn a False record True except
  an explicit user choice passed in as ``allowed=True``.
* Closing the prompt dialog without choosing is No: the
  call site passes allowed=False, and the seen flag is
  still set — closing is a choice to leave it off, not a
  deferral that re-prompts every launch.
* A prompt that fails to display is treated the same way
  (seen, allowed=False) by the call site: failing open to
  normal startup, never a startup crash, never a nag loop.
  The Settings card remains available all the way, so the
  choice is never lost to a failed dialog.

GATE WIRING:
* ``gate_from_stored()`` is the production entry point for
  the continuous-enabled question: it rebuilds the stored
  record (or None) and answers through
  firstopen_optin.gate_continuous. No continuous feature
  calls it yet — capture is explicitly out of scope — but
  when one does, it must call this, never its own reading
  of the keys, so the gate can never drift from the choice.

Own code, standard library only. The functions here take a
plain mapping of stored values (a dict in tests, a dict
built from typed QSettings reads in production) so the
logic executes for real without Qt.
"""

from __future__ import annotations

import json

from . import firstopen_optin as optin_mod

__all__ = [
    "PROMPT_SEEN_KEY",
    "CHOICE_KEY",
    "prompt_seen",
    "should_show_prompt",
    "choice_record_dict",
    "encode_choice",
    "choice_from_stored",
    "apply_choice",
    "gate_from_stored",
]

#: QSettings key: the first-open prompt has been shown (or
#: attempted) on a first open. Once True, never shown again.
PROMPT_SEEN_KEY = "privacy/firstopen_prompt_seen"

#: QSettings key: the user's continuous-display choice, as a
#: JSON string of the ChoiceRecord fields. Absent or
#: malformed means no record — gate disabled, fail-closed.
CHOICE_KEY = "privacy/continuous_display_choice"

#: The chooser recorded for a choice made in the GUI (the
#: first-open dialog or the Settings card): the user, in
#: person, never a tool, a default, or a fixture.
_GUI_CHOOSER = "user"


def prompt_seen(stored) -> bool:
    """True only when the stored seen flag is exactly True.

    Callers read the QSettings key with type=bool, so a real
    stored value is a bool; anything else (absent, a stray
    string, a number) reads as not-seen, and the call site
    — which only runs on a first open — still shows once.
    Existing installs never reach the call site at all."""
    try:
        return stored.get(PROMPT_SEEN_KEY) is True
    except Exception:  # noqa: BLE001 — a broken mapping reads as unseen
        return False


def should_show_prompt(stored) -> bool:
    """Whether the first-open prompt should be shown.

    This answers ONLY the once-ness question: not shown
    before means show. Whether this launch IS a first open
    is the GUI call site's job (it runs only at the end of
    a full first-run onboarding) — this helper never claims
    a launch is a first open by itself."""
    return not prompt_seen(stored)


def choice_record_dict(allowed, now_iso) -> dict:
    """The ChoiceRecord fields for an explicit GUI choice.

    ``allowed`` is the user's own Yes/No; ``now_iso`` is the
    caller's timestamp string. The record is validated by
    build_choice_record itself (oversize fields raise, never
    silently truncated), and its retention/deletion rules
    are exactly the rules the prompt states — the same
    rules a future continuous feature must honour."""
    record = optin_mod.build_choice_record(
        allowed=bool(allowed),
        chooser=_GUI_CHOOSER,
        scope=optin_mod.CONTINUOUS_DISPLAY_SCOPE,
        timestamp=str(now_iso or ""),
        retention_rule=optin_mod.DEFAULT_RETENTION_RULE,
        deletion_rule=optin_mod.DEFAULT_DELETION_RULE,
        form=optin_mod.WATCHES_TEXT,
    )
    return {
        "allowed": bool(record.allowed),
        "chooser": record.chooser,
        "scope": record.scope,
        "timestamp": record.timestamp,
        "retention_rule": record.retention_rule,
        "deletion_rule": record.deletion_rule,
        "form": record.form,
    }


def encode_choice(record_dict) -> str:
    """Serialize a choice record dict to the stored JSON."""
    return json.dumps(record_dict)


def choice_from_stored(stored):
    """Rebuild the stored ChoiceRecord, or None.

    None covers every non-choice: absent key, empty string,
    invalid JSON, a non-object, or a record that fails
    validation. None is a refusal, never a default — the
    gate then reports disabled, exactly as if the user had
    never chosen."""
    try:
        raw = stored.get(CHOICE_KEY)
    except Exception:  # noqa: BLE001
        return None
    if raw is None or str(raw).strip() == "":
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return optin_mod.build_choice_record(
            allowed=data.get("allowed", False),
            chooser=data.get("chooser", ""),
            scope=data.get("scope", ""),
            timestamp=data.get("timestamp", ""),
            retention_rule=data.get("retention_rule", ""),
            deletion_rule=data.get("deletion_rule", ""),
            form=data.get("form", ""),
        )
    except (ValueError, TypeError):
        return None


def apply_choice(stored, allowed, now_iso) -> dict:
    """A new stored mapping with this choice applied.

    The seen flag is set True on EVERY application — Yes,
    No, dialog closed without choosing, or prompt failed to
    display (the call site passes allowed=False then): the
    prompt is asked once, and closing or failing is a
    default-No, not a deferral. The Settings card stays
    available all the way for a later change of mind."""
    updated = dict(stored) if stored is not None else {}
    updated[PROMPT_SEEN_KEY] = True
    updated[CHOICE_KEY] = encode_choice(
        choice_record_dict(allowed, now_iso))
    return updated


def gate_from_stored(stored) -> dict:
    """The production gate answer for the stored choice.

    Rebuilds the record (or None) and answers through the
    Wave 9 gate: enabled ONLY on a valid allowed record
    for exactly continuous-display with chooser, timestamp,
    retention rule, and deletion rule all present. This is
    the one entry point future continuous features must
    call; no caller exists yet, and none is built here."""
    return optin_mod.gate_continuous(choice_from_stored(stored))

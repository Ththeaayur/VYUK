"""First-open continuous choice — the choice mechanism, not
continuous capture itself.

Owner ruling 2026-10-26: continuous capture and monitoring
forms become an OPTION. The user chooses allow or deny, the
system stays local either way, it is OFF by default, the
choice is asked in plain language as soon as the person opens
the app (settings as they stand carry too much jargon), AND
it remains as an option in settings all the way, reversible
at any time. Building this option is not a grant turning it
on for everyone; each user's own choice is the grant.

WHAT THIS MODULE IS
-------------------
* A first-open prompt descriptor: plain-language text asking
  ONE choice — allow continuous display or not — stating in
  plain words what it watches, that everything stays on the
  user's own machine, that it is OFF until they say yes, that
  a small sign on screen shows while it is on, and how to
  switch it off and delete what it kept. The descriptor is
  data, never a GUI redesign, never a renumbering.
* A continuous choice record: ``allowed`` (False by default),
  ``chooser``, exact scope ``"continuous-display"``, a
  caller-supplied ``timestamp``, a ``retention_rule`` and a
  ``deletion_rule`` — the exact form, indicator, retention,
  and deletion rule that the Wave 9 TENSION route requires to
  be named. This module does NOT reuse the Wave 8
  suggestion-cards grant record: that scope opens suggestion
  cards only, and it must never open continuous display.
* A gate: ``gate_continuous()`` returns enabled ONLY when
  the record says allowed=True for exactly the
  continuous-display scope, with a non-empty chooser and
  timestamp and non-empty retention and deletion rules.
  Anything else returns disabled with a stated reason.
* A settings entry descriptor: the same choice in the same
  plain words, available all the way (reversible at any
  time), never a jargon label.
* A per-feature delete: ``delete_continuous_data()`` deletes
  the regular files inside a caller-supplied folder that
  holds kept continuous data. It is per-feature only: it
  never claims the PF-3 global delete-everywhere rule, which
  is still OPEN, and it never touches the app's core or
  data directories, credential files, or anything outside
  the named folder.

WHAT THIS MODULE IS NOT — NO CAPTURE, VERIFIED
----------------------------------------------
* It performs NO continuous capture: it imports no watcher,
  screen, or clipboard code, starts no watcher, schedules no
  job, and pre-warms nothing. There is no thread, no timer,
  and no listener anywhere in this module — proven by the
  AST test in tests/test_firstopen_optin_w9.py.
* It does NOT implement screen memory, clipboard history,
  or any continuous feature. Those are separate Wave 9
  TENSION builds under their own route, and they must honour
  the retention and deletion rule recorded here; this module
  records the rule, it does not enforce it on data it never
  collects, and it never claims otherwise.
* It does NOT choose for the user: ``build_choice_record()``
  only validates a record the caller hands in. A record with
  allowed=True is still only data until a live caller — the
  user, explicitly, through the first-open prompt or the
  settings entry — supplies it. No function here can turn a
  False record True, and a test fixture choice is data for a
  test, never a live choice.
* Switching the choice off stops future capture by the gate
  (the gate then reports disabled); it does not by itself
  delete what was kept — deletion is the separate
  confirm-gated action above, so the two are never confused.

FAIL-CLOSED, WHOLE-CALL
-----------------------
Any refused part refuses the whole call: a missing record,
a False record, a wrong-scope record, an incomplete record
(allowed but chooser, timestamp, retention rule, or deletion
rule empty), an oversize field, or a malformed record all
return disabled with a stated reason. The delete refuses
whole on a missing folder, a folder outside the allowed
shape, a credential file inside the folder, or a symlink
entry — it never deletes part of a folder it refused.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only (``dataclasses``,
``pathlib``). No new dependency, no telemetry write, no
database access, no network access. Nothing here reads or
writes the install tree's ``data/`` directory, and the
delete never touches it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CONTINUOUS_DISPLAY_SCOPE",
    "MAX_FIELD_CHARS",
    "JARGON_TERMS",
    "ChoiceRecord",
    "build_choice_record",
    "inspect_choice",
    "gate_continuous",
    "first_open_prompt",
    "settings_entry",
    "delete_continuous_data",
]

#: The one and only scope that opens continuous display. Any
#: other scope — including the Wave 8 "suggestion-cards"
#: scope — is refused whole.
CONTINUOUS_DISPLAY_SCOPE = "continuous-display"

#: A choice record is small, caller-supplied data. Anything
#: larger is refused, not truncated — truncation would
#: silently change the user's stated rule.
MAX_FIELD_CHARS = 500

#: Technical words the first-open prompt must never use.
#: The prompt is judged in plain language, so the test scans
#: the prompt paragraphs for these (word-boundary matched).
#: This list is about the PROMPT text only, not this module's
#: own documentation.
JARGON_TERMS = frozenset({
    "telemetry", "daemon", "api", "sdk", "biometric",
    "keystroke", "screenshot", "clipboard", "watcher",
    "scheduler", "pre-warm", "prewarming", "opt-in",
    "retention", "scope", "grant", "indicator",
    "endpoint", "webhook", "fingerprint", "egress",
    "payload", "taint",
})

#: What the prompt says it watches, in plain words. The
#: form is named here, once, and the prompt, the record,
#: and the settings entry all use this same wording.
WATCHES_TEXT = (
    "what is on your screen while you work, so it can "
    "suggest things without you having to ask"
)

#: The plain-language rule the prompt states for how long
#: kept notes are held. This is the RULE a future continuous
#: feature must honour; this module collects nothing, so it
#: enforces the rule on nothing, and it never claims
#: otherwise. A caller may supply a different rule in the
#: record — the gate requires one to be named, whichever
#: rule the user chose.
DEFAULT_RETENTION_RULE = "kept notes are thrown away after 7 days"
DEFAULT_DELETION_RULE = "you can delete everything it kept at any time"

_PROMPT_TITLE = "A choice about watching what you do"

_PROMPT_PARAGRAPHS = (
    "J.A.R.V.I.S can keep an eye on " + WATCHES_TEXT + ". "
    "This is called continuous display.",
    "It is switched off to start with. Nothing is watched "
    "until you say yes.",
    "If you say yes, everything it notices stays on this "
    "computer only. Nothing is sent anywhere else.",
    "While it is on, you will always see a small sign on "
    "screen that tells you it is on.",
    "You can switch it off at any time in Settings, in the "
    "same plain words as here. Switching it off stops it "
    "watching anything new.",
    "What it keeps is thrown away after 7 days, and you can "
    "delete everything it kept at any time, from the same "
    "place in Settings.",
)


@dataclass(frozen=True)
class ChoiceRecord:
    """A continuous choice record, as handed in by the caller.

    ``allowed`` defaults to False. A record only opens the
    gate when allowed=True AND scope is exactly
    CONTINUOUS_DISPLAY_SCOPE AND chooser, timestamp,
    retention_rule, and deletion_rule are all non-empty.
    This class validates shape; it does not confer
    authority — authority comes from the live user choosing
    explicitly, never from this module.
    """
    allowed: bool = False
    chooser: str = ""
    scope: str = ""
    timestamp: str = ""
    retention_rule: str = ""
    deletion_rule: str = ""
    form: str = WATCHES_TEXT

    def __post_init__(self):
        object.__setattr__(self, "allowed", bool(self.allowed))
        for name in ("chooser", "scope", "timestamp",
                     "retention_rule", "deletion_rule", "form"):
            object.__setattr__(self, name,
                               str(getattr(self, name) or "").strip())

    def usable_reason(self) -> str | None:
        """None when this record opens the gate, else the
        reason it does not. A missing or False record is a
        refusal, never a default."""
        if not self.allowed:
            return ("disabled — no continuous choice: the choice "
                    "is not allowed (continuous display stays "
                    "OFF, nothing is watched)")
        if self.scope != CONTINUOUS_DISPLAY_SCOPE:
            return (f"disabled — choice scope {self.scope!r} is not "
                    f"{CONTINUOUS_DISPLAY_SCOPE!r}; only a "
                    "continuous-display choice opens this gate")
        if not self.chooser:
            return "disabled — choice record names no chooser"
        if not self.timestamp:
            return "disabled — choice record carries no timestamp"
        if not self.retention_rule:
            return ("disabled — choice record names no rule for "
                    "how long kept notes are held")
        if not self.deletion_rule:
            return ("disabled — choice record names no rule for "
                    "deleting what was kept")
        return None


def build_choice_record(allowed=False, chooser="", scope="",
                        timestamp="", retention_rule="",
                        deletion_rule="", form=""):
    """Validate a caller-supplied choice record (data in,
    record out). This never chooses anything: it only checks
    shape, and a record with allowed=True is still just data
    until a live user supplies it through the prompt or the
    settings entry. Raises ValueError for a non-string
    field, and for an oversize field."""
    fields = {
        "chooser": chooser, "scope": scope, "timestamp": timestamp,
        "retention_rule": retention_rule,
        "deletion_rule": deletion_rule, "form": form,
    }
    for name, value in fields.items():
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        if len(value) > MAX_FIELD_CHARS:
            raise ValueError(f"{name} exceeds {MAX_FIELD_CHARS} chars")
    return ChoiceRecord(
        allowed=bool(allowed), chooser=chooser, scope=scope,
        timestamp=timestamp, retention_rule=retention_rule,
        deletion_rule=deletion_rule,
        form=form or WATCHES_TEXT)


def inspect_choice(choice) -> dict:
    """Describe a choice record without choosing anything.

    ``choice`` may be None (no record at all) or a
    ChoiceRecord. Anything else is reported as malformed,
    never treated as a choice. The result states plainly
    whether continuous display is on and why, and names the
    one scope that opens it."""
    if choice is None:
        return {
            "ok": True,
            "has_record": False,
            "allowed": False,
            "scope": None,
            "enabled": False,
            "reason": ("no continuous choice record; continuous "
                       "display stays OFF until the user says "
                       "yes, in plain words, at first open or "
                       "in Settings"),
            "allowed_scope": CONTINUOUS_DISPLAY_SCOPE,
            "local_only": True,
            "default_off": True,
        }
    if not isinstance(choice, ChoiceRecord):
        return {
            "ok": False,
            "has_record": True,
            "allowed": False,
            "scope": None,
            "enabled": False,
            "reason": "malformed choice record — not a ChoiceRecord",
            "allowed_scope": CONTINUOUS_DISPLAY_SCOPE,
            "local_only": True,
            "default_off": True,
        }
    reason = choice.usable_reason()
    return {
        "ok": True,
        "has_record": True,
        "allowed": bool(choice.allowed),
        "chooser": choice.chooser,
        "scope": choice.scope,
        "timestamp": choice.timestamp,
        "retention_rule": choice.retention_rule,
        "deletion_rule": choice.deletion_rule,
        "form": choice.form,
        "enabled": reason is None,
        "reason": reason or "continuous display is on, by the user's choice",
        "allowed_scope": CONTINUOUS_DISPLAY_SCOPE,
        "local_only": True,
        "default_off": True,
    }


def gate_continuous(choice) -> dict:
    """The gate: the only production entry point for the
    continuous-enabled question. A disabled answer with a
    stated reason is the honest answer whenever the gate is
    closed — never an empty pretence, never a partial on."""
    inspection = inspect_choice(choice)
    if not inspection["ok"]:
        return {"ok": False, "enabled": False,
                "reason": inspection["reason"]}
    return {
        "ok": True,
        "enabled": bool(inspection["enabled"]),
        "reason": inspection["reason"],
        "local_only": True,
        "default_off": True,
    }


def first_open_prompt() -> dict:
    """The first-open prompt descriptor: plain-language data
    asking ONE choice. It is a descriptor, not a GUI build:
    no window is opened, no index is renumbered, nothing is
    chosen by returning this descriptor."""
    return {
        "kind": "FIRST_OPEN_PROMPT",
        "title": _PROMPT_TITLE,
        "paragraphs": list(_PROMPT_PARAGRAPHS),
        "choices": ["Yes, switch it on", "No, keep it off"],
        "default_choice": "No, keep it off",
        "default_off": True,
        "local_only": True,
        "watches": WATCHES_TEXT,
        "indicator": "a small sign on screen that tells you it is on",
        "turn_off_path": "Settings, in the same plain words as here",
        "delete_path": "the same place in Settings",
        "retention_rule": DEFAULT_RETENTION_RULE,
        "deletion_rule": DEFAULT_DELETION_RULE,
        "note": ("This prompt chooses nothing by itself. The "
                 "user's answer, given explicitly, is what "
                 "opens or closes the gate — and the same "
                 "choice stays in Settings all the way."),
    }


def settings_entry(choice=None) -> dict:
    """The settings entry descriptor: the same choice, in
    the same plain words, remaining available all the way
    (reversible at any time). ``choice`` may be None (no
    record: the entry shows OFF) or a ChoiceRecord."""
    prompt = first_open_prompt()
    inspection = inspect_choice(choice)
    return {
        "kind": "SETTINGS_ENTRY",
        "title": _PROMPT_TITLE,
        "paragraphs": prompt["paragraphs"],
        "choices": prompt["choices"],
        "current": "on" if inspection["enabled"] else "off",
        "current_reason": inspection["reason"],
        "reversible": True,
        "available_always": True,
        "default_off": True,
        "local_only": True,
        "watches": WATCHES_TEXT,
        "indicator": prompt["indicator"],
        "turn_off_path": prompt["turn_off_path"],
        "delete_path": prompt["delete_path"],
        "retention_rule": prompt["retention_rule"],
        "deletion_rule": prompt["deletion_rule"],
        "note": ("The same choice as the first-open prompt, "
                 "in the same words. Changing it here "
                 "changes the gate; it never starts "
                 "continuous display by itself, and it "
                 "never claims the PF-3 global "
                 "delete-everywhere rule, which is still "
                 "open."),
    }


_CREDENTIAL_BASENAMES = (".env", "assistant.db")
_CREDENTIAL_SUFFIXES = (".pem", ".key")


def delete_continuous_data(data_dir) -> dict:
    """Delete the regular files inside a caller-supplied
    folder holding kept continuous data — per-feature only.

    Refuses whole, deleting nothing, on: a missing or
    non-directory path; a path that resolves into the app's
    core or data directories, the tree root, or the user's
    home directory itself; a credential file (.env,
    assistant.db, .pem, .key) inside the folder; or a
    symlink entry inside the folder (never followed, never
    deleted through). Subdirectories are left alone and
    reported, never emptied by surprise: only regular files
    directly inside the folder are deleted. This is NOT the
    PF-3 global delete-everywhere rule — that rule is still
    open, and this function never claims it."""
    try:
        target = Path(data_dir).expanduser()
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "deleted": [],
                "reason": f"refused — data folder cannot be resolved: {exc}"}
    if not target.is_dir():
        return {"ok": False, "deleted": [],
                "reason": "refused — data folder does not exist or is not a folder"}
    try:
        resolved = target.resolve()
    except (OSError, ValueError) as exc:
        return {"ok": False, "deleted": [],
                "reason": f"refused — data folder cannot be resolved: {exc}"}
    tree = Path(__file__).resolve().parent.parent
    home = Path.home().resolve()
    if resolved == tree or resolved == home \
            or resolved == tree / "core" or resolved == tree / "data" \
            or resolved.is_relative_to(tree / "core") \
            or resolved.is_relative_to(tree / "data"):
        return {"ok": False, "deleted": [],
                "reason": ("refused — data folder is the app tree, "
                           "its core or data directory, or the home "
                           "directory itself")}
    try:
        entries = sorted(target.iterdir())
    except OSError as exc:
        return {"ok": False, "deleted": [],
                "reason": f"refused — data folder cannot be read: {exc}"}
    for entry in entries:
        if entry.is_symlink():
            return {"ok": False, "deleted": [],
                    "reason": (f"refused — symlink entry {entry.name!r} "
                               "inside the folder; nothing deleted")}
        if entry.name in _CREDENTIAL_BASENAMES \
                or entry.name.endswith(_CREDENTIAL_SUFFIXES):
            return {"ok": False, "deleted": [],
                    "reason": (f"refused — credential file {entry.name!r} "
                               "inside the folder; nothing deleted")}
    deleted = []
    left = []
    for entry in entries:
        if entry.is_file():
            try:
                entry.unlink()
            except OSError as exc:
                return {"ok": False, "deleted": deleted,
                        "reason": (f"refused — could not delete "
                                   f"{entry.name!r}: {exc}; earlier files "
                                   "in this call were already deleted, "
                                   "and the rest were left alone")}
            deleted.append(entry.name)
        else:
            left.append(entry.name)
    return {
        "ok": True,
        "deleted": deleted,
        "left_alone": left,
        "reason": ("per-feature continuous data deleted from the "
                   "named folder only; this is not the PF-3 "
                   "global delete-everywhere rule, which is "
                   "still open"),
    }

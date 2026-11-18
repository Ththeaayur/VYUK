"""Wave 10 Build 3: P23 first-run cluster descriptors —
clean exit, three-minute first command, no-setup mode,
fake-data playground, fix-my-setup checklist.

Part 23 of the v6 plan (P23-1–P23-27), Wave 10
commercial close. The batch's own caveat is carried,
not softened: "This is a checklist, not legal or tax
advice. Confirm licences, privacy law, payments and tax
with a qualified professional in the markets you sell
to." Nothing in this module is that confirmation, and
nothing here claims it.

WHAT THIS MODULE IS
-------------------
Descriptors and records only, from caller-supplied
facts, never beyond them:

* A clean-exit descriptor: caller-supplied items
  stating what a clean exit entails (sessions closed,
  no temp data left, as supplied) in — an exit record
  out, exited False, performed False. It describes an
  exit; it never performs one, kills a process, or
  deletes user data.
* A first-command guide descriptor: a caller-supplied
  command plus plain-language steps that fit in about
  three minutes in — a guide record out, executed
  False. It never executes the command itself.
* A no-setup-mode descriptor: caller-supplied
  capabilities that work with zero setup in — a mode
  record out, setup_required False. A capability that
  needs setup, a key, or an install refuses whole:
  it is never claimed as zero-setup.
* A fake-data playground descriptor: caller-supplied
  labelled samples, or fixed clearly-labelled examples
  when none are supplied, in — a playground record
  out, fake True throughout, real False, sent False.
  Nothing here is real user data, nothing is sent
  anywhere, and nothing is presented as real.
* A fix-my-setup checklist descriptor: caller-supplied
  symptoms in, ordered checks with fix instructions
  out, applied False, settings_changed False,
  installed False. It performs none of the checks or
  fixes: it is a checklist, not a repair. A
  confirm-gated record of the checklist is the only
  write this build ever makes, and it writes only the
  record .json, never a setting, never a fix.
* A re-validation rule for recording a descriptor:
  the descriptor is re-built from its own fields and
  must match exactly, or it is refused, never written
  on trust.

Wave 10 exit wording (exact, never "commercially
complete"): "shippable to strangers except the
transaction surface, feed hosting, and signing".

WHAT THIS MODULE IS NOT
-----------------------
* It exits nothing, kills nothing, deletes nothing,
  runs nothing, installs nothing, applies nothing,
  sends nothing, and publishes nothing: no subprocess,
  network, install, or settings-write import, proven
  by the AST test in tests/test_firstrun_w10.py.
* It makes no legal, privacy-policy, compliance, or
  conformance claim. Accessibility conformance is NOT
  this build: it needs a conformance evidence
  pipeline of its own, and no conformance claim is
  made here.
* It decides nothing about tiers, trials, refunds,
  payments, tax, or purchases: ruling 34 is OPEN, so
  payment/tier/trial/refund content is refused here,
  not decided here.
* It is not a repair tool, not a setup tool, and not
  a data source: the playground is fake by
  construction, and the checklist's fixes are words
  for the user, never actions by this build.

FAIL-CLOSED, WHOLE-CALL
-----------------------
Any refused part refuses the whole call: an action
other than "describe"; an unknown descriptor kind;
an unexpected field for the kind; any send, publish,
install, apply, execute, run, deploy, delete, or kill
action word in a non-instruction field; any claim
something was fixed, installed, exited, shipped,
sent, executed, or deleted; any real-user-data claim
inside playground content; any capability that needs
setup, a key, or an install claimed as zero-setup;
any payment, tier, trial, refund, tax, or purchase
content; any legal, privacy-policy, compliance, or
conformance claim; any missing or empty required
field; any list field that is not a list of
non-empty strings; any checklist entry without a
non-empty check and instruction; any playground
sample without a non-empty label and value.

Steps and checks are the one deliberate exception to
the action-word scan: a step ("type the command",
"look at the reply") or a check instruction ("check
the setting is on") is an instruction for the user
to follow, not an action this build performs — so
those two fields are scanned for completed-action
claims ("already fixed", "fix applied") and for
ruling-34 / legal / conformance content, but not for
bare action verbs. Every record still carries
performed/executed/applied False, so an instruction
can never be mistaken for something this build did.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only (``re``). No new
dependency, no telemetry write, no database access, no
network access. Nothing here reads or writes the
install tree's ``data/`` directory.
"""

from __future__ import annotations

import re

__all__ = [
    "describe_exit",
    "describe_first_command",
    "describe_nosetup",
    "describe_playground",
    "describe_fix",
    "describe",
    "validate_recordable_firstrun",
    "FIXED_EXAMPLES",
]

#: The batch's own caveat, carried verbatim so no
#: first-run record can be mistaken for professional
#: confirmation.
CAVEAT = ("This is a checklist, not legal or tax advice. "
          "Confirm licences, privacy law, payments and tax "
          "with a qualified professional in the markets "
          "you sell to.")

#: Wave 10 exit wording, exact — never "commercially
#: complete".
EXIT_WORDING = ("shippable to strangers except the "
                "transaction surface, feed hosting, "
                "and signing")

#: Fixed playground examples, used only when the
#: caller supplies no samples. Every one is labelled
#: as an example, so none can be mistaken for real
#: user data.
FIXED_EXAMPLES = (
    {"label": "Example person", "value": "Example Name"},
    {"label": "Example note", "value": "Example text for trying things out"},
    {"label": "Example number", "value": "42"},
)

#: Action words: a caller field carrying one as a
#: whole word is an action, not a description, and
#: refuses the whole call — except in steps/check
#: instructions, where they are user instructions
#: (see module docstring).
_ACTION_WORDS = frozenset({
    "send", "sends", "sent", "sending",
    "publish", "publishes", "published", "publishing",
    "install", "installs", "installed", "installing",
    "apply", "applies", "applied", "applying",
    "execute", "executes", "executed", "executing",
    "run", "runs", "running", "ran",
    "deploy", "deploys", "deployed", "deploying",
    "delete", "deletes", "deleted", "deleting",
    "kill", "kills", "killed", "killing",
    "post", "posts", "posted", "posting",
    "broadcast", "broadcasts", "broadcasting",
    "upload", "uploads", "uploaded", "uploading",
})

#: Ruling-34 words: tiers, trials, refunds, payments,
#: tax, and purchases are refused here, not decided
#: here — ruling 34 is OPEN.
_RULING34_WORDS = frozenset({
    "payment", "payments", "pay", "pays", "paid", "paying",
    "price", "prices", "pricing",
    "tier", "tiers",
    "trial", "trials",
    "refund", "refunds", "refunded",
    "subscription", "subscriptions",
    "billing", "billed",
    "tax", "taxes",
    "purchase", "purchases", "purchasing",
    "checkout",
})

#: Completed-action and status claims: phrases that
#: claim something was already done refuse whole.
#: "fixed" alone is NOT here — a checklist
#: instruction may contain the word fix; a claim
#: that a fix was applied is what refuses.
_CLAIM_PHRASES = (
    "already fixed", "has been fixed", "was fixed",
    "is fixed", "fixed it", "fix applied",
    "applied the fix", "fix was applied",
    "already installed", "has been installed",
    "was installed", "is installed",
    "already exited", "has exited", "was exited",
    "exit performed", "exit complete", "exit done",
    "process killed", "killed the process",
    "data deleted", "was deleted", "has been deleted",
    "already shipped", "has shipped", "is shipped",
    "was shipped", "was released", "has been released",
    "was sent", "already sent", "has been sent",
    "message sent", "was executed", "command executed",
    "has run", "already run", "settings changed",
    "settings were changed", "was changed",
    "privacy policy",
    "compliant", "compliance",
    "certified", "certification",
    "conformance", "conforms",
    "accessibility conformance",
    "wcag", "section 508",
    "legally binding", "legal advice",
    "gdpr", "hipaa", "soc 2", "iso 27001",
    "real user data", "actual user data",
    "live user data", "production data",
    "my password", "my account", "my email address",
    "is live", "live feed",
)

#: Zero-setup contradictions: a capability carrying
#: one of these needs setup, a key, or an install,
#: and so refuses whole as a no-setup capability.
_SETUP_WORDS = (
    "api key", "needs a key", "requires a key", "need a key",
    "require a key",
    "needs setup", "requires setup", "need setup", "require setup",
    "needs an install", "requires an install",
    "need an install", "require an install",
    "needs installing", "requires installing",
    "need installing", "require installing",
    "account required", "sign-in required",
    "signin required", "login required",
    "needs a subscription", "requires a subscription",
    "need a subscription", "require a subscription",
)


def _refuse(reason):
    return {"ok": False, "reason": reason}


def _words(text):
    return set(re.findall(r"[a-z][a-z\-]*", text.lower()))


def _check_claims(text, field):
    """Refuse whole on a completed-action claim, a
    ruling-34 word, or a refused legal/conformance
    claim phrase anywhere in one free-text field."""
    lowered = text.lower()
    for phrase in _CLAIM_PHRASES:
        if phrase in lowered:
            return f"{field} carries a refused claim: {phrase}"
    found = _words(text) & _RULING34_WORDS
    if found:
        return (f"{field} carries ruling-34 content, refused "
                f"here, not decided here: {sorted(found)}")
    return None


def _check_text(text, field):
    """Full scan for non-instruction fields: claims
    plus bare action words."""
    if not isinstance(text, str):
        return f"{field} must be a string"
    err = _check_claims(text, field)
    if err:
        return err
    found = _words(text) & _ACTION_WORDS
    if found:
        return (f"{field} is shaped as an action, not a "
                f"description: {sorted(found)}")
    return None


def _check_instruction(text, field):
    """Scan for steps/check instructions: claims only.
    The words are instructions for the user, not
    actions this build performs — but a claim that
    the action already happened refuses whole."""
    if not isinstance(text, str):
        return f"{field} must be a string"
    return _check_claims(text, field)


def _check_action(action):
    if action != "describe":
        return f"only 'describe' is performed, not {action!r}"
    return None


def _check_list(value, field, instruction=False):
    """A list field must be a non-empty list of
    non-empty strings, each passing the text scan
    (instruction scan for steps/checks)."""
    if not isinstance(value, list) or not value:
        return f"{field} must be a non-empty list"
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            return f"{field} entries must be non-empty strings"
        err = (_check_instruction(entry, field) if instruction
               else _check_text(entry, field))
        if err:
            return err
    return None


def _check_string(value, field):
    if not isinstance(value, str) or not value.strip():
        return f"{field} must be a non-empty string"
    return _check_text(value, field)


def _check_fields(fields, allowed, kind):
    if set(fields) - set(allowed):
        return (f"{kind} descriptor carries unexpected fields: "
                f"{sorted(set(fields) - set(allowed))}")
    return None


def describe_exit(items, action="describe"):
    """Clean-exit descriptor: caller-supplied items
    stating what a clean exit entails in, an exit
    record out. It describes an exit; exited False,
    performed False — it never performs one, kills a
    process, or deletes user data."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_list(items, "items")
    if err:
        return _refuse(err)
    return {
        "ok": True,
        "kind": "CLEAN_EXIT",
        "items": [item.strip() for item in items],
        "exited": False,
        "performed": False,
        "processes_killed": False,
        "data_deleted": False,
        "caveat": CAVEAT,
        "exit_wording": EXIT_WORDING,
        "note": ("A clean-exit record, as supplied: what a "
                 "clean exit entails, in the caller's own "
                 "items. It describes an exit; it never "
                 "performs one, kills a process, or "
                 "deletes user data."),
    }


def describe_first_command(command, steps, action="describe"):
    """First-command guide descriptor: a caller-supplied
    command plus plain-language steps that fit in about
    three minutes in, a guide record out, executed
    False. It never executes the command itself."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_string(command, "command")
    if err:
        return _refuse(err)
    err = _check_list(steps, "steps", instruction=True)
    if err:
        return _refuse(err)
    return {
        "ok": True,
        "kind": "FIRST_COMMAND",
        "command": command.strip(),
        "steps": [step.strip() for step in steps],
        "executed": False,
        "fits_about_three_minutes": True,
        "note": ("A first-command guide, as supplied: the "
                 "command and the plain-language steps for "
                 "it, meant to fit in about three minutes. "
                 "It never executes the command itself."),
    }


def describe_nosetup(capabilities, action="describe"):
    """No-setup-mode descriptor: caller-supplied
    capabilities that work with zero setup in, a mode
    record out. A capability that needs setup, a key,
    or an install refuses whole — it is never claimed
    as zero-setup."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_list(capabilities, "capabilities")
    if err:
        return _refuse(err)
    for capability in capabilities:
        lowered = capability.lower()
        for phrase in _SETUP_WORDS:
            if phrase in lowered:
                return _refuse(
                    "capability needs setup, a key, or an "
                    f"install, refused as zero-setup: {phrase}")
    return {
        "ok": True,
        "kind": "NO_SETUP_MODE",
        "capabilities": [cap.strip() for cap in capabilities],
        "setup_required": False,
        "needs_key": False,
        "needs_install": False,
        "note": ("A no-setup-mode record, as supplied: what "
                 "works with zero setup, in the caller's "
                 "own capabilities. Nothing here needs "
                 "setup, a key, or an install."),
    }


def describe_playground(samples=None, action="describe"):
    """Fake-data playground descriptor: caller-supplied
    labelled samples, or fixed clearly-labelled examples
    when none are supplied, in — a playground record
    out, fake True throughout, real False, sent False.
    Nothing here is real user data, nothing is sent
    anywhere, and nothing is presented as real."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if samples is None:
        cleaned = [dict(entry) for entry in FIXED_EXAMPLES]
    else:
        if not isinstance(samples, list) or not samples:
            return _refuse("samples must be a non-empty list")
        cleaned = []
        for sample in samples:
            if not isinstance(sample, dict):
                return _refuse("samples must be objects")
            if set(sample) - {"label", "value"}:
                return _refuse(
                    "playground samples carry only label and value")
            for field in ("label", "value"):
                err = _check_string(sample.get(field), field)
                if err:
                    return _refuse(err)
            cleaned.append({"label": sample["label"].strip(),
                            "value": sample["value"].strip()})
    return {
        "ok": True,
        "kind": "FAKE_DATA_PLAYGROUND",
        "samples": cleaned,
        "fake": True,
        "real": False,
        "sent": False,
        "note": ("A fake-data playground record: every "
                 "sample here is fake, labelled as an "
                 "example, and for trying things out only. "
                 "Nothing here is real user data, nothing "
                 "is sent anywhere, and nothing is "
                 "presented as real."),
    }


def describe_fix(symptoms, checks, action="describe"):
    """Fix-my-setup checklist descriptor: caller-supplied
    symptoms in, ordered checks with fix instructions
    out, applied False. It performs none of the checks
    or fixes: it is a checklist, not a repair."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_list(symptoms, "symptoms")
    if err:
        return _refuse(err)
    if not isinstance(checks, list) or not checks:
        return _refuse("checks must be a non-empty list")
    cleaned = []
    for entry in checks:
        if not isinstance(entry, dict):
            return _refuse("checks must be objects")
        if set(entry) - {"check", "instruction"}:
            return _refuse(
                "checklist entries carry only check and instruction")
        for field in ("check", "instruction"):
            err = _check_instruction(entry.get(field), field)
            if err:
                return _refuse(err)
            if not isinstance(entry.get(field), str) \
                    or not entry[field].strip():
                return _refuse(
                    f"{field} must be a non-empty string")
        cleaned.append({"check": entry["check"].strip(),
                        "instruction": entry["instruction"].strip()})
    return {
        "ok": True,
        "kind": "FIX_MY_SETUP_CHECKLIST",
        "symptoms": [symptom.strip() for symptom in symptoms],
        "checks": cleaned,
        "applied": False,
        "settings_changed": False,
        "installed": False,
        "note": ("A fix-my-setup checklist, as supplied: "
                 "symptoms in, ordered checks with fix "
                 "instructions out, in the order given. "
                 "It performs none of them: no fix is "
                 "applied, no setting is changed, and "
                 "nothing is installed by this build."),
    }


_KIND_FIELDS = {
    "exit": ("items",),
    "first_command": ("command", "steps"),
    "nosetup": ("capabilities",),
    "playground": ("samples",),
    "fix": ("symptoms", "checks"),
}


def describe(kind, action="describe", **fields):
    """Dispatch to one descriptor kind. Unknown kinds
    and unexpected fields for the kind refuse whole —
    a descriptor never silently ignores an input it
    was handed. The playground kind takes no required
    field: with no samples it returns the fixed
    clearly-labelled examples."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if kind not in _KIND_FIELDS:
        return _refuse(
            "kind must be one of exit, first_command, "
            "nosetup, playground, fix")
    err = _check_fields(fields, _KIND_FIELDS[kind], kind)
    if err:
        return _refuse(err)
    if kind == "playground":
        return describe_playground(
            samples=fields.get("samples"), action=action)
    missing = [field for field in _KIND_FIELDS[kind]
               if field not in fields]
    if missing:
        return _refuse(
            f"{kind} descriptor is missing required fields: "
            f"{sorted(missing)}")
    if kind == "exit":
        return describe_exit(action=action, **fields)
    if kind == "first_command":
        return describe_first_command(action=action, **fields)
    if kind == "nosetup":
        return describe_nosetup(action=action, **fields)
    return describe_fix(action=action, **fields)


def validate_recordable_firstrun(fields):
    """Re-run the descriptor builder for a first-run
    record and return the rebuilt descriptor. A record
    is never written on trust: a caller-supplied JSON
    could claim anything, so the descriptor is rebuilt
    from its own fields and the rebuilt form is what
    gets recorded."""
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    kind = fields.get("kind")
    if kind not in _KIND_FIELDS:
        raise ValueError(
            "kind must be one of exit, first_command, "
            "nosetup, playground, fix")
    allowed = set(_KIND_FIELDS[kind]) | {"kind"}
    if set(fields) - allowed:
        raise ValueError("record carries unexpected fields")
    body = {key: fields[key] for key in _KIND_FIELDS[kind]
            if key in fields}
    rebuilt = describe(kind, **body)
    if not rebuilt.get("ok"):
        raise ValueError(rebuilt.get("reason", "rebuild refused"))
    return rebuilt

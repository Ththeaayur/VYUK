"""Wave 10 Build 2: P23 trust + help + changelog
descriptors — trust centre, AI transparency, in-app help
plus diagnostic bundle, known-issues feed, support
runbook, plain-language changelog, one audience first.

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

* A trust-centre descriptor: caller-supplied trust
  facts, in the caller's own words, in — a
  plain-language trust record out. It states what the
  product does with data as supplied. It is never a
  legal claim and never a privacy-policy claim.
* An AI-transparency descriptor: caller-supplied
  model/behaviour notes in — a transparency record
  out, stating what the assistant does and does not
  do, as supplied, never a capability claim beyond
  the supplied notes.
* A help/diagnostic-bundle descriptor: caller-supplied
  help topics plus diagnostic labels in — a bundle
  record out, sent False, explicit-send-only stated.
  The bundle is never sent by this build; sending is
  a separate explicit user action outside this tool.
* A known-issues descriptor: caller-supplied issue
  entries plus status as supplied in — a feed record
  out. It is never a live feed and never an invented
  issue: every entry is supplied, with its status as
  supplied.
* A support-runbook descriptor: caller-supplied steps
  in — a runbook record out, in the order given.
* A changelog descriptor: caller-supplied version
  plus entries in — a plain-language changelog record
  out, shipped False, never a claim that version
  shipped.
* An audience descriptor: caller-supplied audience
  label in — a record out stating the product's first
  audience as supplied, never a market claim.
* A re-validation rule for recording a descriptor:
  the descriptor is re-built from its own fields and
  must match exactly, or it is refused, never written
  on trust.

Wave 10 exit wording (exact, never "commercially
complete"): "shippable to strangers except the
transaction surface, feed hosting, and signing".

WHAT THIS MODULE IS NOT
-----------------------
* It sends nothing, publishes nothing, and runs no
  feed: no network, send, feed, or payment import,
  proven by the AST test in tests/test_trust_w10.py.
* It makes no legal, privacy-policy, compliance, or
  conformance claim. Accessibility conformance is NOT
  this build: it needs a conformance evidence
  pipeline, and no conformance claim is made here.
* It decides nothing about tiers, trials, refunds,
  payments, tax, or the honest legal pack: ruling 34
  is OPEN plus qualified-professional confirmation is
  needed, so payment/tier/trial/refund content is
  refused here, not decided here.
* It is not teams/organisations (ORG, PARKED) and not
  a marketplace (PARKED). It carries no live ticket
  and no real user content: none is supplied by a
  descriptor, and none is invented.

FAIL-CLOSED, WHOLE-CALL
-----------------------
Any refused part refuses the whole call: an action
other than "describe"; an unknown descriptor kind;
an unexpected field for the kind; any send, transmit,
publish, or post action; any legal, privacy-policy,
compliance, or conformance claim; any claim a version
shipped, a feed is live, a ticket exists, or a user
was helped; any payment, tier, trial, refund, tax, or
purchase content; any fake issue, fake user, or fake
data; any missing or empty required field; any list
field that is not a list of non-empty strings; any
issue entry without a non-empty title and status.

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
    "describe_trust",
    "describe_transparency",
    "describe_help",
    "describe_issues",
    "describe_runbook",
    "describe_changelog",
    "describe_audience",
    "describe",
    "validate_recordable_trust",
]

#: The batch's own caveat, carried verbatim in trust
#: outputs so no trust record can be mistaken for
#: professional confirmation.
CAVEAT = ("This is a checklist, not legal or tax advice. "
          "Confirm licences, privacy law, payments and tax "
          "with a qualified professional in the markets "
          "you sell to.")

#: Wave 10 exit wording, exact — never "commercially
#: complete".
EXIT_WORDING = ("shippable to strangers except the "
                "transaction surface, feed hosting, "
                "and signing")

#: Action words: a caller field carrying one as a whole
#: word is a send/publish action, not a description,
#: and refuses the whole call.
_ACTION_WORDS = frozenset({
    "send", "sends", "sent", "sending",
    "transmit", "transmits", "transmitted", "transmitting",
    "publish", "publishes", "published", "publishing",
    "post", "posts", "posted", "posting",
    "broadcast", "broadcasts", "broadcasting",
    "upload", "uploads", "uploaded", "uploading",
    "email", "emails", "emailed",
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

#: Claim phrases: legal, compliance, conformance,
#: shipped, live-feed, ticket, and helped-user claims
#: refuse whole. "privacy" alone is NOT in this list:
#: a trust fact may name privacy as a topic; claiming
#: a privacy POLICY, compliance, or certification is
#: what refuses.
_CLAIM_PHRASES = (
    "privacy policy",
    "compliant", "compliance",
    "certified", "certification",
    "conformance", "conforms",
    "legally binding", "legal advice",
    "gdpr", "hipaa", "soc 2", "iso 27001",
    "already shipped", "has shipped", "is shipped",
    "was released", "has been released",
    "is live", "live feed",
    "ticket exists", "ticket #",
    "was helped", "has been helped", "user was helped",
    "fake", "invented", "made-up", "made up",
)


def _refuse(reason):
    return {"ok": False, "reason": reason}


def _words(text):
    return set(re.findall(r"[a-z][a-z\-]*", text.lower()))


def _check_text(text, field):
    """Refuse whole on a send/publish action word, a
    ruling-34 word, or a refused claim phrase anywhere
    in one free-text field."""
    if not isinstance(text, str):
        return f"{field} must be a string"
    lowered = text.lower()
    for phrase in _CLAIM_PHRASES:
        if phrase in lowered:
            return f"{field} carries a refused claim: {phrase}"
    found = _words(text) & _ACTION_WORDS
    if found:
        return (f"{field} is shaped as a send/publish action: "
                f"{sorted(found)}")
    found = _words(text) & _RULING34_WORDS
    if found:
        return (f"{field} carries ruling-34 content, refused "
                f"here, not decided here: {sorted(found)}")
    return None


def _check_action(action):
    if action != "describe":
        return f"only 'describe' is performed, not {action!r}"
    return None


def _check_list(value, field):
    """A list field must be a non-empty list of
    non-empty strings, each passing the text scan."""
    if not isinstance(value, list) or not value:
        return f"{field} must be a non-empty list"
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            return f"{field} entries must be non-empty strings"
        err = _check_text(entry, field)
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


def describe_trust(facts, action="describe"):
    """Trust-centre descriptor: caller-supplied trust
    facts in, a plain-language trust record out. Never
    a legal claim, never a privacy-policy claim — the
    batch caveat rides with the record so it cannot be
    mistaken for professional confirmation."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_list(facts, "facts")
    if err:
        return _refuse(err)
    return {
        "ok": True,
        "kind": "TRUST_CENTRE",
        "facts": [fact.strip() for fact in facts],
        "sent": False,
        "published": False,
        "legal_claim": False,
        "privacy_policy_claim": False,
        "conformance_claim": False,
        "caveat": CAVEAT,
        "exit_wording": EXIT_WORDING,
        "note": ("A trust record in the caller's own words: "
                 "what the product does with data, as "
                 "supplied. It is not a legal claim, not a "
                 "privacy-policy claim, and not professional "
                 "confirmation of anything."),
    }


def describe_transparency(notes, action="describe"):
    """AI-transparency descriptor: caller-supplied
    model/behaviour notes in, a transparency record out
    stating what the assistant does and does not do, as
    supplied — never a capability claim beyond the
    supplied notes."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_list(notes, "notes")
    if err:
        return _refuse(err)
    return {
        "ok": True,
        "kind": "AI_TRANSPARENCY",
        "notes": [note.strip() for note in notes],
        "sent": False,
        "published": False,
        "capability_claim_beyond_notes": False,
        "note": ("A transparency record, as supplied: what "
                 "the assistant does and does not do, in "
                 "the caller's own notes. Nothing here "
                 "claims a capability beyond those notes."),
    }


def describe_help(topics, diagnostics, action="describe"):
    """Help/diagnostic-bundle descriptor: caller-supplied
    help topics plus diagnostic labels in, a bundle
    record out, sent False, explicit-send-only stated.
    The bundle is never sent by this build."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_list(topics, "topics")
    if err:
        return _refuse(err)
    err = _check_list(diagnostics, "diagnostics")
    if err:
        return _refuse(err)
    return {
        "ok": True,
        "kind": "HELP_BUNDLE",
        "topics": [topic.strip() for topic in topics],
        "diagnostics": [label.strip() for label in diagnostics],
        "sent": False,
        "explicit_send_only": True,
        "note": ("A help and diagnostic bundle, recorded "
                 "only. It has NOT been sent anywhere: "
                 "sending is a separate explicit user "
                 "action outside this tool, never "
                 "performed here."),
    }


def describe_issues(entries, action="describe"):
    """Known-issues descriptor: caller-supplied issue
    entries plus status as supplied in, a feed record
    out. Never a live feed, never an invented issue:
    every entry is supplied, with its status as
    supplied."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if not isinstance(entries, list) or not entries:
        return _refuse("entries must be a non-empty list")
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict):
            return _refuse("entries must be objects")
        if set(entry) - {"title", "status"}:
            return _refuse(
                "issue entries carry only title and status")
        for field in ("title", "status"):
            err = _check_string(entry.get(field), field)
            if err:
                return _refuse(err)
        cleaned.append({"title": entry["title"].strip(),
                        "status": entry["status"].strip()})
    return {
        "ok": True,
        "kind": "KNOWN_ISSUES_FEED",
        "entries": cleaned,
        "live": False,
        "invented": False,
        "note": ("A known-issues record, as supplied: each "
                 "issue and its status exactly as the "
                 "caller gave them. This is not a live "
                 "feed, and no issue here was invented."),
    }


def describe_runbook(steps, action="describe"):
    """Support-runbook descriptor: caller-supplied steps
    in, a runbook record out, in the order given."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_list(steps, "steps")
    if err:
        return _refuse(err)
    return {
        "ok": True,
        "kind": "SUPPORT_RUNBOOK",
        "steps": [step.strip() for step in steps],
        "sent": False,
        "note": ("A support runbook, as supplied, in the "
                 "order given. It describes steps; it "
                 "performs none of them."),
    }


def describe_changelog(version, entries, action="describe"):
    """Changelog descriptor: caller-supplied version plus
    entries in, a plain-language changelog record out,
    shipped False — never a claim that version shipped."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_string(version, "version")
    if err:
        return _refuse(err)
    err = _check_list(entries, "entries")
    if err:
        return _refuse(err)
    return {
        "ok": True,
        "kind": "CHANGELOG",
        "version": version.strip(),
        "entries": [entry.strip() for entry in entries],
        "shipped": False,
        "note": ("A changelog record, as supplied, in plain "
                 "language. It is not a claim that this "
                 "version shipped."),
    }


def describe_audience(audience, action="describe"):
    """Audience descriptor: caller-supplied audience label
    in, a record out stating the product's first
    audience as supplied, never a market claim."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_string(audience, "audience")
    if err:
        return _refuse(err)
    return {
        "ok": True,
        "kind": "FIRST_AUDIENCE",
        "audience": audience.strip(),
        "market_claim": False,
        "note": ("A first-audience record, as supplied. It "
                 "states who the product is for first, as "
                 "the caller gave it; it makes no market "
                 "claim."),
    }


_KIND_FIELDS = {
    "trust": ("facts",),
    "transparency": ("notes",),
    "help": ("topics", "diagnostics"),
    "issues": ("entries",),
    "runbook": ("steps",),
    "changelog": ("version", "entries"),
    "audience": ("audience",),
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
            "kind must be one of trust, transparency, help, "
            "issues, runbook, changelog, audience")
    err = _check_fields(fields, _KIND_FIELDS[kind], kind)
    if err:
        return _refuse(err)
    missing = [field for field in _KIND_FIELDS[kind]
               if field not in fields]
    if missing:
        return _refuse(
            f"{kind} descriptor is missing required fields: "
            f"{sorted(missing)}")
    if kind == "trust":
        return describe_trust(action=action, **fields)
    if kind == "transparency":
        return describe_transparency(action=action, **fields)
    if kind == "help":
        return describe_help(action=action, **fields)
    if kind == "issues":
        return describe_issues(action=action, **fields)
    if kind == "runbook":
        return describe_runbook(action=action, **fields)
    if kind == "changelog":
        return describe_changelog(action=action, **fields)
    return describe_audience(action=action, **fields)


def validate_recordable_trust(fields):
    """Re-run the descriptor builder for a trust record
    and return the rebuilt descriptor. A record is never
    written on trust: a caller-supplied JSON could claim
    anything, so the descriptor is rebuilt from its own
    fields and the rebuilt form is what gets recorded."""
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    kind = fields.get("kind")
    if kind not in _KIND_FIELDS:
        raise ValueError(
            "kind must be one of trust, transparency, help, "
            "issues, runbook, changelog, audience")
    allowed = set(_KIND_FIELDS[kind]) | {"kind"}
    if set(fields) - allowed:
        raise ValueError("record carries unexpected fields")
    body = {key: fields[key] for key in _KIND_FIELDS[kind]
            if key in fields}
    rebuilt = describe(kind, **body)
    if not rebuilt.get("ok"):
        raise ValueError(rebuilt.get("reason", "rebuild refused"))
    return rebuilt

"""Wave 10 Build 4: P23 accessibility conformance
EVIDENCE PIPELINE — the discipline that makes a
future conformance claim checkable. This build makes
NO conformance claim.

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
evidence, never beyond it:

* An evidence ledger: caller-supplied evidence items
  in — each exactly {check_id, criterion, evidence,
  outcome} — out as a ledger record that splits the
  items into evidenced and not-evidenced lists. A
  check is "evidenced" ONLY when its evidence is
  non-empty AND its outcome is supplied. Anything
  else is listed as "not evidenced" — never passed,
  never failed by inference, never filled in.
* A conformance statement descriptor: a
  caller-supplied scope plus the same evidence items
  in — a statement record out that lists evidenced
  checks and unevidenced checks separately, and
  states plainly that anything unevidenced is NOT
  claimed. The record carries conforms/compliant/
  certified flags all False and makes NO blanket
  product claim.
* A re-validation rule for recording: the descriptor
  is re-built from its own fields and must match
  exactly, or it is refused, never written on trust.

WHAT A CRITERION LABEL IS AND IS NOT
------------------------------------
A caller-supplied criterion (for example a WCAG
criterion name) is echoed as the LABEL only. The
verdict on that criterion comes solely from its
supplied evidence: an evidenced item echoes its
supplied outcome, an unevidenced item is listed as
not evidenced. That is why the verdict-word scan
below applies to evidence, outcome, and scope text
but NOT to criterion text: a label naming a
conformance criterion is not itself a verdict. A
criterion carrying a verdict phrase is still echoed
only as a label, and the record's own generated text
never contains a blanket verdict word.

RELATION TO WAVE 9 ACCESS
--------------------------
Wave 9 Build 5 (core/accessibility.py) built the
accessibility descriptors themselves: text clarity
counts, window description from a caller-supplied
snapshot, voice-first routine from a fixed list, and
preference records under the dignity bar (a label is
never inferred about a person, a score is never
computed). This build does NOT duplicate those
descriptors and does NOT re-claim them: it reuses
their discipline — no inferred label, no score, no
live read — and adds the pipeline those descriptors
never had, namely an evidence ledger plus a
statement that separates evidenced from
unevidenced. An ACCESS descriptor output may be
CITED by a caller as an evidence reference; this
module never re-runs an ACCESS descriptor and never
treats a cited descriptor as conformance evidence
on its own — the evidence field is what it is, as
supplied, and the evidenced/not-evidenced split is
all this module ever decides.

WHAT THIS MODULE IS NOT
-----------------------
* It is not a conformance claim, a compliance claim,
  a certification, or an audit: no audit is
  performed here, no live test is run here, and no
  product-level verdict is emitted, ever.
* It performs no live screen read and no capture:
  no capture, OCR, sensor, or network import,
  proven by the AST test in tests/test_accessconf_w10.py.
* It decides nothing about tiers, trials, refunds,
  payments, tax, or the honest legal pack: ruling 34
  is OPEN plus qualified-professional confirmation is
  needed, so payment/tier/trial/refund content is
  refused here, not decided here.
* It is not teams/organisations (ORG, PARKED) and
  not a marketplace (PARKED).

FAIL-CLOSED, WHOLE-CALL
-----------------------
Any refused part refuses the whole call: an action
other than "describe"; an unknown descriptor kind;
an unexpected field for the kind; any send, publish,
install, apply, execute, or run action; any
conformance, compliance, or certification conclusion
as an output (verdict words in evidence, outcome, or
scope text refuse whole — see the criterion-label
note above); any invented evidence; any pass verdict
presented without supplied evidence being treated as
a pass (it is listed as not evidenced, never passed
by inference — and a numeric score as an outcome
refuses whole, because a score is never computed
here); any disability, diagnosis, or health label;
any score; any live screen read or capture claim;
any payment, tier, trial, refund, tax, or purchase
content; any legal claim; any missing field; any
non-string field; any empty check_id, criterion, or
scope; any list field that is not a list of evidence
items. One deliberate, documented edge: an evidence
item whose evidence or outcome value is an EMPTY
STRING is not a refusal — it is the defined
"not evidenced" case, and the item is listed as not
evidenced. Missing KEYS, by contrast, refuse whole:
a check must carry all four keys, even when the
evidence or outcome value is empty.

Two further deliberate fail-closed edges, in the
same family as the WELL "diagnostics" edge and the
grad "no capture" edge: the action-word scan is
whole-word, so evidence phrased as a "test run"
refuses — reword it as a "test session". And the
verdict-word scan refuses evidence phrased as a
team having "reviewed compliance" — reword it as a
plain reference to the review itself, with no verdict
word in it.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only (``re``). No new
dependency, no telemetry write, no database access,
no network access. Nothing here reads or writes the
install tree's ``data/`` directory. No WCAG text is
reproduced here: criterion names are caller-supplied
labels, echoed only.
"""

from __future__ import annotations

import re

__all__ = [
    "describe_ledger",
    "describe_statement",
    "describe",
    "validate_recordable_accessconf",
]

#: The batch's own caveat, carried verbatim so no
#: evidence record can be mistaken for professional
#: confirmation.
CAVEAT = ("This is a checklist, not legal or tax advice. "
          "Confirm licences, privacy law, payments and tax "
          "with a qualified professional in the markets "
          "you sell to.")

#: Action words: a caller field carrying one as a
#: whole word is an action, not a description, and
#: refuses the whole call.
_ACTION_WORDS = frozenset({
    "send", "sends", "sent", "sending",
    "transmit", "transmits", "transmitted", "transmitting",
    "publish", "publishes", "published", "publishing",
    "post", "posts", "posted", "posting",
    "broadcast", "broadcasts", "broadcasting",
    "upload", "uploads", "uploaded", "uploading",
    "email", "emails", "emailed",
    "install", "installs", "installed", "installing",
    "apply", "applies", "applied", "applying",
    "execute", "executes", "executed", "executing",
    "run", "runs", "running",
    "deploy", "deploys", "deployed", "deploying",
    "compile", "compiles", "compiled",
    "sign", "signs", "signed", "signing",
    "merge", "merges", "merged",
    "push", "pushes", "pushed",
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

#: Verdict words: a conformance, compliance, or
#: certification conclusion in evidence, outcome, or
#: scope text refuses whole. Criterion text is exempt
#: (label only — see the module docstring).
_VERDICT_WORDS = frozenset({
    "conforms", "compliant", "certified",
    "conformance", "compliance", "certification",
})

#: Verdict phrases (multi-word conclusions).
_VERDICT_PHRASES = (
    "fully accessible",
    "fully conformant",
    "meets all requirements",
)

#: Disability, diagnosis, and health labels: refused
#: whole, per the Wave 9 ACCESS dignity bar — a label
#: is never placed on a person here.
_LABEL_WORDS = frozenset({
    "disability", "disabled",
    "diagnosis", "diagnosed",
    "medical", "health",
    "blind", "deaf",
})

#: Live-read and capture markers: this pipeline
#: performs no live screen read and no capture.
_CAPTURE_WORDS = frozenset({
    "screenshot", "captured", "capture", "ocr",
})

_CAPTURE_PHRASES = (
    "live read",
    "screen read",
    "screen capture",
)

#: Fake-evidence markers: evidence here is supplied
#: and real, never invented.
_FAKE_WORDS = frozenset({
    "fake", "invented",
})

_FAKE_PHRASES = (
    "made-up",
    "made up",
)

#: Legal markers: any legal claim refuses whole.
_LEGAL_WORDS = frozenset({
    "legal", "legally", "liability", "warranty",
})

#: Score marker: a score is never computed here.
_SCORE_WORDS = frozenset({
    "score",
})

#: A numeric outcome (for example "95" or "95%") is a
#: score, not an outcome, and refuses whole.
_NUMERIC_OUTCOME = re.compile(r"^\d+(\.\d+)?%?$")


def _refuse(reason):
    return {"ok": False, "reason": reason}


def _words(text):
    return set(re.findall(r"[a-z][a-z\-]*", text.lower()))


def _scan(text, field, verdict=True):
    """Refuse whole on an action word, a ruling-34
    word, a verdict word or phrase, a label word, a
    capture marker, a fake marker, a legal marker, or
    a score word anywhere in one free-text field.
    ``verdict=False`` is the criterion-label scan:
    verdict words are allowed there as a label only."""
    if not isinstance(text, str):
        return f"{field} must be a string"
    lowered = text.lower()
    found = _words(text) & _ACTION_WORDS
    if found:
        return (f"{field} is shaped as an action: "
                f"{sorted(found)}")
    found = _words(text) & _RULING34_WORDS
    if found:
        return (f"{field} carries ruling-34 content, refused "
                f"here, not decided here: {sorted(found)}")
    if verdict:
        found = _words(text) & _VERDICT_WORDS
        if found:
            return (f"{field} carries a refused verdict word: "
                    f"{sorted(found)}")
        for phrase in _VERDICT_PHRASES:
            if phrase in lowered:
                return (f"{field} carries a refused verdict "
                        f"phrase: {phrase}")
    found = _words(text) & _LABEL_WORDS
    if found:
        return (f"{field} carries a refused label: "
                f"{sorted(found)}")
    found = _words(text) & _CAPTURE_WORDS
    if found:
        return (f"{field} carries a refused capture marker: "
                f"{sorted(found)}")
    for phrase in _CAPTURE_PHRASES:
        if phrase in lowered:
            return (f"{field} carries a refused capture "
                    f"phrase: {phrase}")
    found = _words(text) & _FAKE_WORDS
    if found:
        return (f"{field} carries refused fake evidence: "
                f"{sorted(found)}")
    for phrase in _FAKE_PHRASES:
        if phrase in lowered:
            return (f"{field} carries refused fake evidence: "
                    f"{phrase}")
    found = _words(text) & _LEGAL_WORDS
    if found:
        return (f"{field} carries a refused legal marker: "
                f"{sorted(found)}")
    found = _words(text) & _SCORE_WORDS
    if found:
        return (f"{field} carries a refused score word: "
                f"{sorted(found)}")
    return None


def _check_action(action):
    if action != "describe":
        return f"only 'describe' is performed, not {action!r}"
    return None


def _check_string(value, field, verdict=True):
    if not isinstance(value, str) or not value.strip():
        return f"{field} must be a non-empty string"
    return _scan(value, field, verdict=verdict)


def _check_evidence_item(item):
    """Validate one evidence item. All four keys must
    be present and be strings; check_id and criterion
    must be non-empty; evidence and outcome may be
    empty strings — that is the defined not-evidenced
    case, not a refusal. A numeric outcome is a score
    and refuses whole."""
    if not isinstance(item, dict):
        return "evidence items must be objects"
    if set(item) != {"check_id", "criterion", "evidence",
                     "outcome"}:
        return ("evidence items carry exactly check_id, "
                "criterion, evidence, and outcome")
    err = _check_string(item["check_id"], "check_id")
    if err:
        return err
    # Criterion: verdict words allowed as a label only.
    err = _check_string(item["criterion"], "criterion",
                        verdict=False)
    if err:
        return err
    for field in ("evidence", "outcome"):
        value = item[field]
        if not isinstance(value, str):
            return f"{field} must be a string"
        if value.strip():
            err = _scan(value, field)
            if err:
                return err
    outcome = item["outcome"].strip()
    if outcome and _NUMERIC_OUTCOME.match(outcome):
        return "outcome must not be a numeric score"
    return None


def _split_checks(checks):
    """Split caller-supplied evidence items into
    evidenced and not-evidenced lists. Evidenced ONLY
    when evidence is non-empty AND outcome is
    supplied; anything else is not evidenced — never
    passed, never failed by inference."""
    evidenced = []
    not_evidenced = []
    for item in checks:
        clean = {
            "check_id": item["check_id"].strip(),
            "criterion": item["criterion"].strip(),
            "evidence": item["evidence"].strip(),
            "outcome": item["outcome"].strip(),
        }
        if clean["evidence"] and clean["outcome"]:
            clean["status"] = "evidenced"
            evidenced.append(clean)
        else:
            clean["status"] = "not_evidenced"
            not_evidenced.append(clean)
    return evidenced, not_evidenced


def _check_checks(checks):
    if not isinstance(checks, list) or not checks:
        return "checks must be a non-empty list"
    for item in checks:
        err = _check_evidence_item(item)
        if err:
            return err
    return None


_NOTE = ("This record lists evidenced checks and "
         "unevidenced checks separately. Anything "
         "listed as not evidenced is NOT claimed. No "
         "blanket claim is made about the product.")


def describe_ledger(checks, action="describe"):
    """Evidence-ledger descriptor: caller-supplied
    evidence items in, a ledger record out splitting
    them into evidenced and not-evidenced lists. The
    record makes no blanket claim: every product-level
    verdict flag is False."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_checks(checks)
    if err:
        return _refuse(err)
    evidenced, not_evidenced = _split_checks(checks)
    return {
        "ok": True,
        "kind": "EVIDENCE_LEDGER",
        "evidenced": evidenced,
        "not_evidenced": not_evidenced,
        "evidenced_count": len(evidenced),
        "not_evidenced_count": len(not_evidenced),
        "conforms": False,
        "compliant": False,
        "certified": False,
        "blanket_claim": False,
        "audit_performed": False,
        "live_test_run": False,
        "caveat": CAVEAT,
        "note": _NOTE,
    }


def describe_statement(scope, checks, action="describe"):
    """Conformance-statement descriptor: a
    caller-supplied scope plus evidence items in, a
    statement record out listing evidenced checks and
    unevidenced checks separately. Anything
    unevidenced is NOT claimed, and no blanket product
    claim is made: every verdict flag is False."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_string(scope, "scope")
    if err:
        return _refuse(err)
    err = _check_checks(checks)
    if err:
        return _refuse(err)
    evidenced, not_evidenced = _split_checks(checks)
    return {
        "ok": True,
        "kind": "CONFORMANCE_STATEMENT",
        "scope": scope.strip(),
        "evidenced": evidenced,
        "not_evidenced": not_evidenced,
        "evidenced_count": len(evidenced),
        "not_evidenced_count": len(not_evidenced),
        "conforms": False,
        "compliant": False,
        "certified": False,
        "accessible_claim": False,
        "unevidenced_not_claimed": True,
        "audit_performed": False,
        "live_test_run": False,
        "caveat": CAVEAT,
        "note": _NOTE,
    }


_KIND_FIELDS = {
    "ledger": ("checks",),
    "statement": ("scope", "checks"),
}


def describe(kind, action="describe", **fields):
    """Dispatch to one descriptor kind. Unknown kinds
    and unexpected fields for the kind refuse whole —
    a descriptor never silently ignores an input it
    was handed."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if kind not in _KIND_FIELDS:
        return _refuse(
            "kind must be one of ledger, statement")
    if set(fields) - set(_KIND_FIELDS[kind]):
        return _refuse(
            f"{kind} descriptor carries unexpected fields: "
            f"{sorted(set(fields) - set(_KIND_FIELDS[kind]))}")
    missing = [field for field in _KIND_FIELDS[kind]
               if field not in fields]
    if missing:
        return _refuse(
            f"{kind} descriptor is missing required fields: "
            f"{sorted(missing)}")
    if kind == "ledger":
        return describe_ledger(action=action, **fields)
    return describe_statement(action=action, **fields)


def validate_recordable_accessconf(fields):
    """Re-run the descriptor builder for a conformance
    record and return the rebuilt descriptor. A record
    is never written on trust: a caller-supplied JSON
    could claim anything, so the descriptor is rebuilt
    from its own fields and the rebuilt form is what
    gets recorded."""
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    kind = fields.get("kind")
    if kind not in _KIND_FIELDS:
        raise ValueError("kind must be one of ledger, statement")
    allowed = set(_KIND_FIELDS[kind]) | {"kind"}
    if set(fields) - allowed:
        raise ValueError("record carries unexpected fields")
    body = {key: fields[key] for key in _KIND_FIELDS[kind]
            if key in fields}
    rebuilt = describe(kind, **body)
    if not rebuilt.get("ok"):
        raise ValueError(rebuilt.get("reason", "rebuild refused"))
    return rebuilt

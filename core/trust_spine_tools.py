"""Wave 10 Build 2 tools: P23 trust + help + changelog
descriptors, as live tools.

Triage Wire pattern, as in core/release_spine_tools.py:
this coordinator imports the trust module, registers
tools via register_all(registry), each handler takes
the named schema fields, forwards to the module, and
returns the module result encoded as a JSON string
verbatim; refusal dicts flow the same way, and module
raises become an error JSON — handlers never raise.

TOOLBOX INDIRECTION (why two tools, not eight)
----------------------------------------------
The fitted Gemini declaration budget is a HARD cap
(240,000 B) and stood at 239,624 B on stage after
Wave 9 with only about 376 B of headroom — and after
Wave 10 Build 1 the fitted total is OVER the cap
(240,286 B), so the fitted full list was already over
it; the domain-router build routed around it (offered
sets never flew it), and the owner later retired it
(DECL-01, 2026-11-01) under the overflow protocol
(~/workspace/code-lab/overflow_queue.md). Eight
separate tools, each with its own parameter schema,
would only add to that breach, and no schema is
gutted here to chase bytes: nothing is dropped and
nothing is queued — every descriptor the brief names
is built, wired, grouped, and tested, sharing ONE
read-only descriptor tool by kind plus ONE
confirm-gated record tool. Parameter schemas here
carry no per-property descriptions, for the same
fitted-cap reason: at the 55-char cap the fitter
would truncate them anyway. Tool descriptions lead
with their safety clause, so the fitted copy still
carries it where the fitter still runs.

Wiring (Wave 10 Build 2, P23 trust + help +
changelog):
  * trust_describe — read-only. kind "trust" returns
    the trust-centre record from caller-supplied
    trust facts (never a legal or privacy-policy
    claim; the batch caveat rides with it); kind
    "transparency" returns the AI-transparency record
    from caller-supplied notes (never a capability
    claim beyond them); kind "help" returns the
    help/diagnostic-bundle record (sent False,
    explicit-send-only stated, never sent here); kind
    "issues" returns the known-issues record (never
    a live feed, never an invented issue); kind
    "runbook" returns the support-runbook record in
    the order given; kind "changelog" returns the
    plain-language changelog record (shipped False,
    never a claim that version shipped); kind
    "audience" returns the first-audience record as
    supplied (never a market claim). Nothing is
    sent, published, fed, or decided by any kind.
  * trust_record — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the
    trust descriptor itself (it never trusts a
    caller-supplied trust JSON, which could claim
    anything) and writes only the trust record to a
    caller-chosen .json file in an already-existing
    folder — no folder creation, no overwrite of a
    core/data path, no .env/.pem/.key target. The
    funnel refuses it without a human confirm handler,
    before the handler runs. Recording sends
    nothing, publishes nothing, claims no compliance,
    and ships no version — ruling 34 stays open.

This coordinator is NOT a send tool, a publish tool,
a feed tool, or a legal tool: it imports no network,
send, feed, or payment code, sends nothing, publishes
nothing, and never claims compliance, conformance, or
a shipped version.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import trust_spine as trust_mod


def _error(reason: str) -> str:
    return json.dumps({"ok": False, "reason": reason})


def _handler(fn, *args, **kwargs):
    try:
        return json.dumps(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")


def _parse_json(text, field):
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, f"{field} is not valid JSON: {exc}"
    return value, None


_TRUST_KINDS = ("trust", "transparency", "help", "issues",
                "runbook", "changelog", "audience")


def trust_describe_handler(kind, action="describe",
                           record_json=None):
    """Dispatch one descriptor kind. The record_json
    object must carry only the kind's own fields —
    the module refuses any field belonging to another
    kind whole, before describing anything — and it
    must not carry its own action: only 'describe'
    is performed."""
    if kind not in _TRUST_KINDS:
        return _error(
            "kind must be one of trust, transparency, help, "
            "issues, runbook, changelog, audience")
    if record_json is None:
        return _handler(trust_mod.describe, kind,
                        action=action)
    fields, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("record_json must be a JSON object")
    if "action" in fields:
        return _error(
            "record_json must not carry its own action; "
            "only 'describe' is performed")
    return _handler(trust_mod.describe, kind,
                    action=action, **fields)


def _check_output_path(output_path):
    try:
        target = Path(output_path).expanduser()
        if target.suffix != ".json":
            return None, "output_path must end in .json"
        if not target.parent.is_dir():
            return None, "output_path parent folder must already exist"
        if target.name in (".env", "assistant.db") \
                or target.name.endswith((".pem", ".key")):
            return None, "credential target refused"
        tree = Path(__file__).resolve().parent.parent
        try:
            resolved = target.resolve()
            if resolved.is_relative_to(tree / "core") \
                    or resolved.is_relative_to(tree / "data"):
                return None, "app core/data paths refused"
        except (OSError, ValueError):
            return None, "output_path cannot be resolved"
    except Exception as exc:  # noqa: BLE001
        return None, f"output_path cannot be resolved: {exc}"
    return target, None


def trust_record_handler(record_kind, record_json,
                         output_path):
    fields, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("record_json must be a JSON object")
    if "action" in fields:
        return _error(
            "record_json must not carry its own action; "
            "only 'describe' is performed")
    if record_kind not in _TRUST_KINDS:
        return _error(
            "record_kind must be one of trust, transparency, "
            "help, issues, runbook, changelog, audience")
    try:
        rebuilt = trust_mod.validate_recordable_trust(
            {"kind": record_kind, **fields})
    except (ValueError, TypeError) as exc:
        return _error(
            f"trust descriptor does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "TRUST_RECORD",
        "record_kind": record_kind,
        "trust": rebuilt,
        "sent": False, "published": False,
        "shipped": False, "conformance_claim": False,
        "exit": trust_mod.EXIT_WORDING,
        "caveat": trust_mod.CAVEAT,
        "note": ("A trust/help/changelog descriptor the user "
                 "chose, recorded only. Recording sends "
                 "nothing, publishes nothing, claims no "
                 "compliance or conformance, and ships no "
                 "version — ruling 34 stays open, and the "
                 "exit is shippable to strangers except "
                 "the transaction surface, feed hosting, "
                 "and signing."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "TRUST_RECORD",
                       "sent": False, "published": False,
                       "shipped": False,
                       "report_path": str(target),
                       "note": "Trust descriptor recorded; nothing "
                               "was sent, published, or shipped."})


def register_all(registry) -> None:
    """Register the 2 trust tools (toolbox
    indirection: trust_describe carries the trust,
    transparency, help, issues, runbook, changelog,
    and audience descriptors by kind; trust_record
    carries the trust record)."""
    registry.register(
        name="trust_describe",
        description="DESCRIBE ONLY, NOTHING SENT OR PUBLISHED: describe the trust centre, AI transparency, help/diagnostic bundle, known-issues feed, support runbook, plain-language changelog, or first audience, from caller-supplied fields only. kind 'trust' states data handling as supplied (never a legal or privacy-policy claim; batch caveat rides with it); kind 'transparency' states what the assistant does and does not do, as supplied; kind 'help' returns the bundle record, sent False, explicit-send-only; kind 'issues' returns supplied issues with status as supplied, never a live feed, never invented; kind 'runbook' returns steps in order; kind 'changelog' returns the version record, shipped False; kind 'audience' states the first audience as supplied, never a market claim. Ruling 34 stays open.",
        parameters={"type": "object", "properties": {
            "kind": {"description": "Which descriptor kind to describe","type": "string",
                     "enum": ["trust", "transparency",
                              "help", "issues", "runbook",
                              "changelog", "audience"]},
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "action": {"description": "Only 'describe' is performed", "type": "string"},
        }, "required": ["kind"]},
        handler=trust_describe_handler,
    )
    registry.register(
        name="trust_record",
        description="RECORD ONLY, NOTHING SENT OR PUBLISHED: re-validate a caller-supplied trust/help/changelog descriptor (trust, transparency, help, issues, runbook, changelog, or audience) by re-running the builder that made it, and write only the trust record to a caller-chosen .json file in an already-existing folder. A descriptor that does not re-build is refused, never written on trust. The record asks for confirmation first, and it is not a send, a publication, a compliance claim, or a shipped version; ruling 34 stays open.",
        parameters={"type": "object", "properties": {
            "record_kind": {"description": "Which descriptor kind the record is","type": "string",
                            "enum": ["trust", "transparency",
                                     "help", "issues", "runbook",
                                     "changelog", "audience"]},
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "output_path": {"description": "Caller-chosen .json file in an already-existing folder", "type": "string"},
        }, "required": ["record_kind", "record_json",
                        "output_path"]},
        handler=trust_record_handler,
    )

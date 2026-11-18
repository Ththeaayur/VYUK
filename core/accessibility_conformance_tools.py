"""Wave 10 Build 4 tools: P23 accessibility
conformance evidence pipeline, as live tools.

Triage Wire pattern, as in core/trust_spine_tools.py:
this coordinator imports the evidence module,
registers tools via register_all(registry), each
handler takes the named schema fields, forwards to
the module, and returns the module result encoded as
a JSON string verbatim; refusal dicts flow the same
way, and module raises become an error JSON —
handlers never raise.

TOOLBOX INDIRECTION (why two tools, not more)
----------------------------------------------
The fitted Gemini declaration budget is a HARD cap
(240,000 B) and is already OVER it after Wave 10
Build 1 (240,286 B, grown by Build 2 to 241,046 B and
by Build 3 to 241,768 B), so the fitted full list was
already over the cap; the domain-router build routed
around it (offered sets never flew it), and the owner
later retired it (DECL-01, 2026-11-01) under the
overflow protocol
protocol (~/workspace/code-lab/overflow_queue.md).
Separate tools, each with its own parameter schema,
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

Wiring (Wave 10 Build 4, P23 conformance evidence):
  * accessconf_describe — read-only. kind "ledger"
    returns the evidence-ledger record from
    caller-supplied evidence items, split into
    evidenced and not-evidenced lists (evidenced
    ONLY when evidence is non-empty AND outcome is
    supplied; anything else is not evidenced, never
    passed or failed by inference); kind "statement"
    returns the conformance-statement record from a
    caller-supplied scope plus the same items, with
    evidenced and unevidenced checks listed
    separately and anything unevidenced explicitly
    NOT claimed. No blanket product claim is made by
    either kind: every verdict flag is False, no
    audit is performed, no live test is run.
  * accessconf_record — confirm-gated, output_path
    in _WRITE_DESTINATION_ARGS. It re-validates the
    evidence descriptor itself (it never trusts a
    caller-supplied JSON, which could claim anything)
    and writes only the evidence record to a
    caller-chosen .json file in an already-existing
    folder — no folder creation, no overwrite of a
    core/data path, no .env/.pem/.key target. The
    funnel refuses it without a human confirm handler,
    before the handler runs. Recording claims no
    conformance, performs no audit, and runs no live
    test — ruling 34 stays open.

This coordinator is NOT a conformance tool, an
audit tool, a live-test tool, a capture tool, or a
legal tool: it imports no capture, OCR, sensor,
network, or payment code, claims nothing, audits
nothing, reads no live screen, and never emits a
blanket product verdict.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import accessibility_conformance as accessconf_mod


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


_ACCESSCONF_KINDS = ("ledger", "statement")


def accessconf_describe_handler(kind, action="describe",
                                record_json=None):
    """Dispatch one descriptor kind. The record_json
    object must carry only the kind's own fields —
    the module refuses any field belonging to another
    kind whole, before describing anything — and it
    must not carry its own action: only 'describe'
    is performed."""
    if kind not in _ACCESSCONF_KINDS:
        return _error("kind must be one of ledger, statement")
    if record_json is None:
        return _handler(accessconf_mod.describe, kind,
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
    return _handler(accessconf_mod.describe, kind,
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


def accessconf_record_handler(record_kind, record_json,
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
    if record_kind not in _ACCESSCONF_KINDS:
        return _error("record_kind must be one of ledger, statement")
    try:
        rebuilt = accessconf_mod.validate_recordable_accessconf(
            {"kind": record_kind, **fields})
    except (ValueError, TypeError) as exc:
        return _error(
            f"evidence descriptor does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "ACCESSCONF_RECORD",
        "record_kind": record_kind,
        "evidence": rebuilt,
        "conforms": False, "compliant": False,
        "certified": False, "blanket_claim": False,
        "audit_performed": False, "live_test_run": False,
        "caveat": accessconf_mod.CAVEAT,
        "note": ("An evidence/statement descriptor the user "
                 "chose, recorded only. Recording claims "
                 "no conformance, performs no audit, runs "
                 "no live test, and reads no live screen — "
                 "ruling 34 stays open, and anything "
                 "listed as not evidenced is NOT claimed."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "ACCESSCONF_RECORD",
                       "conforms": False, "compliant": False,
                       "certified": False,
                       "report_path": str(target),
                       "note": "Evidence descriptor recorded; no "
                               "conformance was claimed, no audit "
                               "performed, no live test run."})


def register_all(registry) -> None:
    """Register the 2 conformance-evidence tools
    (toolbox indirection: accessconf_describe carries
    the evidence-ledger and conformance-statement
    descriptors by kind; accessconf_record carries the
    evidence record)."""
    registry.register(
        name="accessconf_describe",
        description="DESCRIBE ONLY, NO CONFORMANCE CLAIM: describe the accessibility evidence ledger or the conformance statement, from caller-supplied evidence items only. kind 'ledger' splits items into evidenced and not-evidenced lists (evidenced ONLY when evidence is non-empty AND outcome is supplied; anything else is not evidenced, never passed or failed by inference); kind 'statement' lists evidenced and unevidenced checks separately for a caller-supplied scope, with anything unevidenced explicitly NOT claimed. No blanket product claim is made by either kind, no audit is performed, no live test is run, no live screen is read; ruling 34 stays open.",
        parameters={"type": "object", "properties": {
            "kind": {"description": "Which descriptor kind to describe","type": "string",
                     "enum": ["ledger", "statement"]},
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "action": {"description": "Only 'describe' is performed", "type": "string"},
        }, "required": ["kind"]},
        handler=accessconf_describe_handler,
    )
    registry.register(
        name="accessconf_record",
        description="RECORD ONLY, NO CONFORMANCE CLAIM: re-validate a caller-supplied evidence-ledger or conformance-statement descriptor (ledger or statement) by re-running the builder that made it, and write only the evidence record to a caller-chosen .json file in an already-existing folder. A descriptor that does not re-build is refused, never written on trust. The record asks for confirmation first, and it claims no conformance, performs no audit, runs no live test, and reads no live screen; ruling 34 stays open.",
        parameters={"type": "object", "properties": {
            "record_kind": {"description": "Which descriptor kind the record is","type": "string",
                            "enum": ["ledger", "statement"]},
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "output_path": {"description": "Caller-chosen .json file in an already-existing folder", "type": "string"},
        }, "required": ["record_kind", "record_json",
                        "output_path"]},
        handler=accessconf_record_handler,
    )

"""Wave 10 Build 3 tools: P23 first-run cluster
descriptors, as live tools.

Triage Wire pattern, as in core/trust_spine_tools.py:
this coordinator imports the first-run module, registers
tools via register_all(registry), each handler takes
the named schema fields, forwards to the module, and
returns the module result encoded as a JSON string
verbatim; refusal dicts flow the same way, and module
raises become an error JSON — handlers never raise.

TOOLBOX INDIRECTION (why two tools, not five)
----------------------------------------------
The fitted Gemini declaration budget is a HARD cap
(240,000 B) and is already OVER it after Wave 10
Build 1 (240,286 B), grown by Build 2 to 241,046 B,
so the fitted full list was already over the cap; the
domain-router build routed around it (offered sets
never flew it), and the owner later retired it
(DECL-01, 2026-11-01) under the overflow protocol
(~/workspace/code-lab/overflow_queue.md). Five
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

Wiring (Wave 10 Build 3, P23 first-run cluster):
  * firstrun_describe — read-only. kind "exit"
    returns the clean-exit record from caller-supplied
    items (exited False, performed False — it never
    performs an exit, kills a process, or deletes user
    data); kind "first_command" returns the
    first-command guide record from a caller-supplied
    command plus plain-language steps meant to fit in
    about three minutes (executed False — it never
    executes the command itself); kind "nosetup"
    returns the no-setup-mode record from
    caller-supplied capabilities only (a capability
    that needs setup, a key, or an install refuses
    whole, never claimed as zero-setup); kind
    "playground" returns the fake-data playground
    record from caller-supplied labelled samples, or
    the fixed clearly-labelled examples when none are
    supplied (fake True throughout, real False, sent
    False — never real user data, never sent anywhere,
    never presented as real); kind "fix" returns the
    fix-my-setup checklist record from caller-supplied
    symptoms plus ordered checks with fix instructions
    (applied False — it performs none of them, changes
    no setting, installs nothing). Nothing is exited,
    run, fixed, installed, applied, sent, or published
    by any kind.
  * firstrun_record — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the
    first-run descriptor itself (it never trusts a
    caller-supplied first-run JSON, which could claim
    anything) and writes only the first-run record to
    a caller-chosen .json file in an already-existing
    folder — no folder creation, no overwrite of a
    core/data path, no .env/.pem/.key target. The
    funnel refuses it without a human confirm handler,
    before the handler runs. Recording exits nothing,
    runs nothing, fixes nothing, installs nothing, and
    applies nothing — ruling 34 stays open, and
    accessibility conformance is NOT claimed here.

This coordinator is NOT an exit tool, a run tool, a
repair tool, a setup tool, or a data tool: it imports
no subprocess, network, install, or settings-write
code, and it performs no exit, execution, fix,
install, or setting change.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import firstrun as firstrun_mod


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


_FIRSTRUN_KINDS = ("exit", "first_command", "nosetup",
                   "playground", "fix")


def firstrun_describe_handler(kind, action="describe",
                              record_json=None):
    """Dispatch one descriptor kind. The record_json
    object must carry only the kind's own fields —
    the module refuses any field belonging to another
    kind whole, before describing anything — and it
    must not carry its own action: only 'describe'
    is performed. The playground kind needs no fields:
    with no record_json it returns the fixed
    clearly-labelled examples."""
    if kind not in _FIRSTRUN_KINDS:
        return _error(
            "kind must be one of exit, first_command, "
            "nosetup, playground, fix")
    if record_json is None:
        return _handler(firstrun_mod.describe, kind,
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
    return _handler(firstrun_mod.describe, kind,
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


def firstrun_record_handler(record_kind, record_json,
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
    if record_kind not in _FIRSTRUN_KINDS:
        return _error(
            "record_kind must be one of exit, first_command, "
            "nosetup, playground, fix")
    try:
        rebuilt = firstrun_mod.validate_recordable_firstrun(
            {"kind": record_kind, **fields})
    except (ValueError, TypeError) as exc:
        return _error(
            f"first-run descriptor does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "FIRSTRUN_RECORD",
        "record_kind": record_kind,
        "firstrun": rebuilt,
        "exited": False, "executed": False,
        "applied": False, "installed": False,
        "settings_changed": False, "sent": False,
        "conformance_claim": False,
        "exit": firstrun_mod.EXIT_WORDING,
        "caveat": firstrun_mod.CAVEAT,
        "note": ("A first-run descriptor the user chose, "
                 "recorded only. Recording exits nothing, "
                 "runs nothing, fixes nothing, installs "
                 "nothing, applies nothing, and claims no "
                 "conformance — ruling 34 stays open, and "
                 "the exit is shippable to strangers except "
                 "the transaction surface, feed hosting, "
                 "and signing."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "FIRSTRUN_RECORD",
                       "exited": False, "executed": False,
                       "applied": False, "installed": False,
                       "sent": False,
                       "report_path": str(target),
                       "note": "First-run descriptor recorded; "
                               "nothing was exited, run, fixed, "
                               "installed, or sent."})


def register_all(registry) -> None:
    """Register the 2 first-run tools (toolbox
    indirection: firstrun_describe carries the exit,
    first_command, nosetup, playground, and fix
    descriptors by kind; firstrun_record carries the
    first-run record)."""
    registry.register(
        name="firstrun_describe",
        description="DESCRIBE ONLY, NOTHING EXITED, RUN, FIXED, INSTALLED, OR SENT: describe the clean exit, three-minute first command, no-setup mode, fake-data playground, or fix-my-setup checklist, from caller-supplied fields only. kind 'exit' states what a clean exit entails, as supplied (exited False, never performed here, no process killed, no data deleted); kind 'first_command' returns the guide for a caller-supplied command plus plain-language steps meant to fit in about three minutes (executed False, never executed here); kind 'nosetup' states what works with zero setup, as supplied (a capability needing setup, a key, or an install refuses whole); kind 'playground' returns labelled samples or fixed clearly-labelled examples (fake True throughout, never real user data, never sent, never presented as real); kind 'fix' returns ordered checks with fix instructions from caller-supplied symptoms (applied False, performs none of them). Ruling 34 stays open; no conformance claim is made.",
        parameters={"type": "object", "properties": {
            "kind": {"description": "Which descriptor kind to describe","type": "string",
                     "enum": ["exit", "first_command",
                              "nosetup", "playground", "fix"]},
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "action": {"description": "Only 'describe' is performed", "type": "string"},
        }, "required": ["kind"]},
        handler=firstrun_describe_handler,
    )
    registry.register(
        name="firstrun_record",
        description="RECORD ONLY, NOTHING EXITED, RUN, FIXED, INSTALLED, OR SENT: re-validate a caller-supplied first-run descriptor (exit, first_command, nosetup, playground, or fix) by re-running the builder that made it, and write only the first-run record to a caller-chosen .json file in an already-existing folder. A descriptor that does not re-build is refused, never written on trust. The record asks for confirmation first, and it is not an exit, an execution, a fix, an install, or a setting change; ruling 34 stays open and no conformance is claimed.",
        parameters={"type": "object", "properties": {
            "record_kind": {"description": "Which descriptor kind the record is","type": "string",
                            "enum": ["exit", "first_command",
                                     "nosetup", "playground",
                                     "fix"]},
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "output_path": {"description": "Caller-chosen .json file in an already-existing folder", "type": "string"},
        }, "required": ["record_kind", "record_json",
                        "output_path"]},
        handler=firstrun_record_handler,
    )

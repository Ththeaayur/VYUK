"""First-open continuous choice as live tools.

Triage Wire pattern, as in core/anticipation_grant_tools.py: this
coordinator imports the choice module, registers tools via
register_all(registry), each handler takes the named schema
fields, forwards to the module, and returns the module result
encoded as a JSON string verbatim; refusal dicts flow the
same way, and module raises become an error JSON — handlers
never raise.

Wiring (first-open continuous opt-in, choice mechanism
only):
  * continuous_first_open — read-only. It returns the
    first-open prompt descriptor in plain language. It
    chooses nothing, watches nothing, and opens no window.
  * continuous_choice_status — read-only. It inspects a
    caller-supplied choice record (or its absence) and
    states plainly whether continuous display is on and
    why. It chooses nothing and upgrades nothing.
  * continuous_record_choice — confirm-gated, output_path
    in _WRITE_DESTINATION_ARGS. It re-validates the choice
    itself (it never trusts a caller-supplied choice JSON,
    which could claim anything) and writes only the choice
    record to a caller-chosen .json file whose parent
    folder must already exist — no folder creation, no
    overwrite of a core/data path, no .env/.pem/.key
    target. The funnel refuses it without a human confirm
    handler, before the handler runs. Writing the record
    is not starting continuous display, and the record is
    the user's own choice, never a choice made for them.
  * continuous_delete_data — confirm-gated, data_dir in
    _WRITE_DESTINATION_ARGS. It deletes only the regular
    files inside a caller-supplied folder holding kept
    continuous data, under the module's own refusals
    (missing folder, app core/data, tree root, home
    directory, credential file inside, symlink entry).
    The funnel refuses it without a human confirm handler,
    before the handler runs. This is per-feature delete
    only — it never claims the PF-3 global
    delete-everywhere rule, which is still open.

This coordinator is NOT a capture implementation: it
imports no watcher, screen, or clipboard code, starts no
watcher, schedules no job, and pre-warms nothing. No
continuous capture, screen memory, or clipboard history
is built or wired here; those are separate Wave 9
TENSION builds under their own route, and they must
honour the retention and deletion rule recorded here.
A test fixture choice is data for a test, never a live
choice, and no tool here chooses for the user.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import firstopen_optin as optin_mod


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


def _parse_choice(choice_json):
    """Parse a choice record JSON into a ChoiceRecord, or
    None when the caller supplied no record (an absent
    record is a refusal, never a default). A malformed
    record is an error, never treated as a choice."""
    if choice_json is None or str(choice_json).strip() == "":
        return None, None
    record, err = _parse_json(choice_json, "choice_json")
    if err:
        return None, err
    if not isinstance(record, dict):
        return None, "choice_json must be a JSON object"
    try:
        choice = optin_mod.build_choice_record(
            allowed=record.get("allowed", False),
            chooser=record.get("chooser", ""),
            scope=record.get("scope", ""),
            timestamp=record.get("timestamp", ""),
            retention_rule=record.get("retention_rule", ""),
            deletion_rule=record.get("deletion_rule", ""),
            form=record.get("form", ""),
        )
    except (ValueError, TypeError) as exc:
        return None, f"choice_json invalid: {exc}"
    return choice, None


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


def continuous_first_open_handler():
    return _handler(optin_mod.first_open_prompt)


def continuous_choice_status_handler(choice_json=""):
    choice, err = _parse_choice(choice_json)
    if err:
        return _error(err)
    return _handler(optin_mod.inspect_choice, choice)


def continuous_record_choice_handler(choice_json, output_path):
    choice, err = _parse_choice(choice_json)
    if err:
        return _error(err)
    if choice is None:
        return _error("choice_json must name a choice record")
    inspection = optin_mod.inspect_choice(choice)
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "CONTINUOUS_CHOICE",
        "allowed": bool(choice.allowed),
        "chooser": choice.chooser,
        "scope": choice.scope,
        "timestamp": choice.timestamp,
        "retention_rule": choice.retention_rule,
        "deletion_rule": choice.deletion_rule,
        "form": choice.form,
        "enabled": bool(inspection["enabled"]),
        "reason": inspection["reason"],
        "local_only": True,
        "default_off": True,
        "note": ("Choice record written; this tool chooses "
                 "nothing for the user, starts no continuous "
                 "display, and watches nothing. The record "
                 "is the user's own choice, given "
                 "explicitly."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "CONTINUOUS_CHOICE",
                       "executed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "enabled": bool(inspection["enabled"]),
                       "reason": inspection["reason"],
                       "note": "Choice record written; nothing "
                               "was watched, captured, or started."})


def continuous_delete_data_handler(data_dir):
    return _handler(optin_mod.delete_continuous_data, data_dir)


def register_all(registry) -> None:
    """Register the 4 first-open continuous choice tools."""
    registry.register(
        name="continuous_first_open",
        description="FIRST-OPEN PROMPT ONLY, NOTHING CHOSEN, NOTHING WATCHED: return the plain-language first-open prompt descriptor asking one choice — allow continuous display or not — stating in plain words what it watches, that everything stays on the user's own machine, that it is OFF until they say yes, that a small sign on screen shows while it is on, and how to switch it off and delete what it kept. Returning the descriptor chooses nothing and starts nothing; settings carry the same choice in the same words all the way.",
        parameters={"type": "object", "properties": {}},
        handler=continuous_first_open_handler,
    )
    registry.register(
        name="continuous_choice_status",
        description="CHOICE INSPECTION ONLY, CHOOSES NOTHING: inspect a caller-supplied continuous choice record (or its absence) and state plainly whether continuous display is on and why. Only allowed + scope continuous-display, with a chooser, timestamp, retention rule, and deletion rule, opens the gate. This tool never chooses, upgrades, or persists a choice.",
        parameters={"type": "object", "properties": {
            "choice_json": {"type": "string",
                            "description": "Optional JSON choice record {allowed, chooser, scope, timestamp, retention_rule, deletion_rule, form}; absent means no choice record"},
        }, "required": []},
        handler=continuous_choice_status_handler,
    )
    registry.register(
        name="continuous_record_choice",
        description="CHOICE RECORD ONLY, NOTHING STARTED, NOTHING WATCHED: re-validate a caller-supplied continuous choice and write only the choice record to a caller-chosen .json file in an already-existing folder. This tool chooses nothing for the user, starts no continuous display, and watches nothing — the record is the user's own explicit choice. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "choice_json": {"type": "string",
                            "description": "JSON choice record {allowed, chooser, scope, timestamp, retention_rule, deletion_rule, form}"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["choice_json", "output_path"]},
        handler=continuous_record_choice_handler,
    )
    registry.register(
        name="continuous_delete_data",
        description="PER-FEATURE DELETE ONLY, NEVER THE GLOBAL RULE: delete only the regular files inside a caller-chosen folder holding kept continuous data, under strict refusals (missing folder, app core/data, tree root, home directory, credential file inside, symlink entry). This is not the PF-3 global delete-everywhere rule, which is still open. The delete asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "data_dir": {"type": "string",
                         "description": "Caller-chosen folder holding kept continuous data; only regular files directly inside it are deleted"},
        }, "required": ["data_dir"]},
        handler=continuous_delete_data_handler,
    )

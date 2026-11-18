"""Accessibility deltas as live tools — descriptors and
records only, nothing read live, nothing inferred.

Triage Wire pattern, as in core/comms_tools.py:
this coordinator imports the accessibility module,
registers tools via register_all(registry), each handler
takes the named schema fields, forwards to the module,
and returns the module result encoded as a JSON string
verbatim; refusal dicts flow the same way, and module
raises become an error JSON — handlers never raise.

Wiring (Wave 9 Build 5, ACCESS — accessibility deltas
beyond the shipped Accessibility edition, under the
dignity bar and S-6 where capture is involved):
  * access_text_clarity — read-only. It returns a
    plain-language note about caller-supplied text —
    sentence count, word count, long-sentence count,
    and fixed-list jargon words found. It says nothing
    about the reader and never produces a score.
  * access_window_describe — read-only. It describes a
    caller-supplied accessibility-tree snapshot — control
    names and roles as supplied, in the order supplied.
    Nothing is read from a live screen; a snapshot
    flagged live or captured refuses whole.
  * access_voice_first_routine — read-only. It returns a
    routine of caller-chosen steps, each from the fixed
    list only (voice command, larger target, keyboard
    alternative, pause). An unknown step refuses whole.
  * access_record_preference — confirm-gated,
    output_path in _WRITE_DESTINATION_ARGS. It
    re-validates the preference itself (plain-language
    label only, no disability or health label, no score,
    no inferred flag) and writes only the preference
    record to a caller-chosen .json file in an
    already-existing folder — no folder creation, no
    overwrite of a core/data path, no .env/.pem/.key
    target. The funnel refuses it without a human
    confirm handler, before the handler runs.

This coordinator is NOT a capture tool: it imports no
screen-capture, OCR, or sensor code, reads no live
screen, infers nothing about the user, and labels no
one. Any disability, health, score, inference, live
capture, or cure claim refuses whole in the module.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import accessibility as access_mod


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


def access_text_clarity_handler(text, want_score=None):
    return _handler(access_mod.build_text_clarity, text, want_score)


def access_window_describe_handler(snapshot_json):
    snapshot, err = _parse_json(snapshot_json, "snapshot_json")
    if err:
        return _error(err)
    return _handler(access_mod.describe_window, snapshot)


def access_voice_first_routine_handler(steps_json):
    steps, err = _parse_json(steps_json, "steps_json")
    if err:
        return _error(err)
    return _handler(access_mod.build_routine, steps)


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


def access_record_preference_handler(preference_json, output_path):
    fields, err = _parse_json(preference_json, "preference_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("preference_json must be a JSON object")
    try:
        rebuilt = access_mod.validate_recordable_preference(fields)
    except (ValueError, TypeError) as exc:
        return _error(f"preference does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "ACCESS_PREFERENCE_RECORD",
        "preference": rebuilt,
        "note": ("An accessibility preference, recorded only. "
                 "It labels a setting, never a person, and "
                 "it changes nothing by itself."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "ACCESS_PREFERENCE_RECORD",
                       "executed": False,
                       "report_path": str(target),
                       "note": "Preference recorded; nothing was changed."})


def register_all(registry) -> None:
    """Register the 4 accessibility tools."""
    registry.register(
        name="access_text_clarity",
        description="NOTE ABOUT TEXT ONLY, NEVER ABOUT THE READER: return a plain-language note about caller-supplied text — sentence count, word count, long-sentence count, and fixed-list jargon words found. It says nothing about the reader and never produces a reading-level, capability, or comprehension score; a caller asking for a score refuses whole.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string",
                     "description": "Caller-supplied text to note, as given"},
            "want_score": {"type": "boolean",
                           "description": "Optional; any true value refuses whole — no score exists here"},
        }, "required": ["text"]},
        handler=access_text_clarity_handler,
    )
    registry.register(
        name="access_window_describe",
        description="SNAPSHOT ONLY, NOTHING READ LIVE: describe a caller-supplied accessibility-tree snapshot — control names and roles as supplied, in the order supplied — as an eyes-free layout description. Nothing is read from a live screen; a snapshot flagged live or captured, or naming a live source, refuses whole.",
        parameters={"type": "object", "properties": {
            "snapshot_json": {"type": "string",
                              "description": "JSON object with a caller-supplied controls list of {name, role} entries"},
        }, "required": ["snapshot_json"]},
        handler=access_window_describe_handler,
    )
    registry.register(
        name="access_voice_first_routine",
        description="CHOSEN STEPS ONLY, FIXED LIST: return a routine of caller-chosen steps, each from the fixed list only (voice command, larger target, keyboard alternative, pause), in the order given. An unknown step refuses whole, never swapped for a near neighbour; duplicates are kept once and reported.",
        parameters={"type": "object", "properties": {
            "steps_json": {"type": "string",
                           "description": "JSON list of caller-chosen step names from the fixed list"},
        }, "required": ["steps_json"]},
        handler=access_voice_first_routine_handler,
    )
    registry.register(
        name="access_record_preference",
        description="RECORD ONLY, A SETTING NEVER A PERSON: re-validate a caller-supplied accessibility preference by re-running the builder that made it, and write only the preference record to a caller-chosen .json file in an already-existing folder. A preference carrying a disability or health label, a score, an inferred flag, or any unexpected field is refused, never written on trust. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "preference_json": {"type": "string",
                                "description": "JSON object with a plain-language label and enabled"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["preference_json", "output_path"]},
        handler=access_record_preference_handler,
    )

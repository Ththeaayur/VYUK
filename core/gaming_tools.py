"""Gaming deltas as live tools.

Triage Wire pattern, as in core/net_tools.py:
this coordinator imports the gaming module, registers
tools via register_all(registry), each handler takes the
named schema fields, forwards to the module, and returns
the module result encoded as a JSON string verbatim;
refusal dicts flow the same way, and module raises become
an error JSON — handlers never raise.

Wiring (Wave 9 Build 7, P14 — gaming batch, under the
shipped v5.78 Gaming wall):
  * game_session_describe — read-only. It returns a
    game-session descriptor from a caller-supplied game
    title, a mode of exactly "single-player" or
    "co-op", and session notes. No game is read,
    controlled, automated, or observed live.
  * game_macro_describe — read-only. It returns a
    macro descriptor from caller-supplied steps and a
    mode of exactly "single-player" or "co-op". A
    competitive mode, a banned gaming-wall phrase, or
    an automation-shaped step refuses the whole macro,
    never downgraded. The macro is never executed:
    describing steps does not perform them.
  * game_media_note — read-only. It returns a
    drive-only media note for a caller-supplied tool
    name and licence label: the tool is NOT bundled,
    integrated, or shipped with Nova, and Nova cannot
    run it — the user drives it themselves, under its
    own licence. This is the only form here for the
    excluded-for-bundling GPL items (OBS GPL-2.0,
    FFmpeg GPL builds with libx264, LosslessCut
    GPL-2.0-only).
  * game_record_gaming — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the gaming
    descriptor itself (it never trusts a caller-supplied
    gaming JSON, which could claim anything) and writes
    only the gaming record to a caller-chosen .json file
    in an already-existing folder — no folder creation,
    no overwrite of a core/data path, no .env/.pem/.key
    target. The funnel refuses it without a human confirm
    handler, before the handler runs. Recording a session,
    macro, or media note reads no game, controls no game,
    automates no input, and bundles no GPL tool.

This coordinator is NOT a game tool: it imports no
game-memory, injection, input-automation, or
screen-capture code, reads no game, controls no game,
automates no input, and bundles no GPL tool. No
memory-read, injection, cheat, anti-cheat-evasion, or
competitive-automation action is built or wired here;
any such input refuses whole in the module.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import gaming as gaming_mod


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


def game_session_describe_handler(game_title, mode, notes=None,
                                  action="describe"):
    return _handler(gaming_mod.build_game_session,
                    game_title, mode, notes, action)


def game_macro_describe_handler(mode, steps, action="describe"):
    return _handler(gaming_mod.build_game_macro,
                    mode, steps, action)


def game_media_note_handler(tool_name, licence_label=None,
                            action="describe", bundled=None,
                            integrated=None, shipped=None):
    return _handler(gaming_mod.build_media_note,
                    tool_name, licence_label, action,
                    bundled, integrated, shipped)


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


def game_record_gaming_handler(record_kind, record_json, output_path):
    fields, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("record_json must be a JSON object")
    try:
        rebuilt = gaming_mod.validate_recordable_gaming(record_kind,
                                                        fields)
    except (ValueError, TypeError) as exc:
        return _error(f"gaming descriptor does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "GAMING_RECORD",
        "record_kind": record_kind.strip(),
        "gaming": rebuilt,
        "read_live": False,
        "controlled": False,
        "automated": False,
        "bundled": False,
        "note": ("A gaming descriptor the user chose, "
                 "recorded only. Recording reads no game, "
                 "controls no game, automates no input, "
                 "and bundles no tool."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "GAMING_RECORD",
                       "executed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "read_live": False, "controlled": False,
                       "automated": False, "bundled": False,
                       "note": "Gaming descriptor recorded; no game "
                               "was read, controlled, or automated, "
                               "and no tool was bundled."})


def register_all(registry) -> None:
    """Register the 4 gaming tools."""
    registry.register(
        name="game_session_describe",
        description="DESCRIPTOR ONLY, NO GAME READ OR TOUCHED: return a game-session descriptor from a caller-supplied game title, a mode of exactly 'single-player' or 'co-op', and session notes — never a live game read. Any competitive, ranked, or multiplayer mode, any memory-read, injection, cheat, or anti-cheat-evasion input, and any claim a game was read, controlled, or automated refuse the whole call.",
        parameters={"type": "object", "properties": {
            "game_title": {"type": "string",
                           "description": "Caller-supplied game title"},
            "mode": {"type": "string",
                     "description": "Exactly 'single-player' or 'co-op'"},
            "notes": {"type": "string",
                      "description": "Caller-supplied session notes"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["game_title", "mode"]},
        handler=game_session_describe_handler,
    )
    registry.register(
        name="game_macro_describe",
        description="DESCRIPTOR ONLY, NEVER EXECUTED: return a macro descriptor from caller-supplied step labels and a mode of exactly 'single-player' or 'co-op'. A competitive mode, a banned gaming-wall phrase, or a step shaped as an automated input sequence (key press, click, mouse move, keystroke) refuses the whole macro, never downgraded. Describing steps does not perform them.",
        parameters={"type": "object", "properties": {
            "mode": {"type": "string",
                     "description": "Exactly 'single-player' or 'co-op'"},
            "steps": {"type": "array", "items": {"type": "string"},
                      "description": "Caller-supplied step labels, in order"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["mode", "steps"]},
        handler=game_macro_describe_handler,
    )
    registry.register(
        name="game_media_note",
        description="DRIVE-ONLY NOTE ONLY, NOTHING BUNDLED: return a note for a caller-supplied tool name and licence label stating the tool is NOT bundled, integrated, or shipped with Nova and must be driven by the user themselves, under its own licence. This is the only form here for OBS (GPL-2.0), FFmpeg GPL builds with libx264, and LosslessCut (GPL-2.0-only). Any bundled, integrated, or shipped claim refuses the whole call.",
        parameters={"type": "object", "properties": {
            "tool_name": {"type": "string",
                          "description": "Caller-supplied tool name"},
            "licence_label": {"type": "string",
                              "description": "Caller-supplied licence label; omitted only for a plan-recorded GPL tool"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["tool_name"]},
        handler=game_media_note_handler,
    )
    registry.register(
        name="game_record_gaming",
        description="RECORD ONLY, NO GAME TOUCHED, NO TOOL BUNDLED: re-validate a caller-supplied gaming descriptor (session, macro, or media-note) by re-running the builder that made it, and write only the gaming record to a caller-chosen .json file in an already-existing folder. A descriptor that does not re-build is refused, never written on trust. Recording reads no game, controls no game, automates no input, and bundles no tool. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "record_kind": {"type": "string",
                            "description": "One of session, macro, media-note"},
            "record_json": {"type": "string",
                            "description": "JSON object of the gaming fields to re-validate"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["record_kind", "record_json", "output_path"]},
        handler=game_record_gaming_handler,
    )

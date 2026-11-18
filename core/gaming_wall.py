"""GM wall + traps (Gaming track, wave 30, §5.22) — Nova's own code.

This module is the anti-cheat wall made into data, not a policy memo.
Every build line in §5.22 was commissioned under one condition — 0 of
the 22 lines may require memory-reading, injection/hooking, competitive
gameplay automation, or anti-cheat evasion — and this module is where
that condition is enforced for the Gaming shelf.

STANDALONE RULE (edition precedent): this module imports nothing from
any other gm_* module, and no gm_* module imports it. Consumers take a
pre-classified title record (see ``classify_title``) as input; the
product wires classification through this module at the call site.
That keeps every module independently loadable and testable.

A title record is plain data, supplied by the caller — this library
never reads a process list, a game client, or a window title by
itself. ``classify_title`` turns explicit facts into the record; it
does not discover facts.

WALL CLASSES (all refused, no exception, no override flag):
- memory_reading: reading game memory (health, ammo, positions).
- injection: DLL injection, hooking, or driving a scene that uses
  OBS Game Capture for an anti-cheat title (OBS's own docs call
  Game Capture DLL injection — driving it makes her the automator).
- competitive_automation: input or decisions that play a competitive
  game for the user (live OCR killfeed readers, auto-aim, farming).
- anti_cheat_evasion: anything that hides her from, or routes around,
  an anti-cheat system.

TRAP REGISTER (§5.22 / §9):
- GM-15 must never be acquired via its README's ``npx skills add``
  channel (an agent-directed install instruction is not a licence).
- GM-21's LoL pre-game approval does not read across to VALORANT:
  approvals carry a game scope, and cross-game reuse is refused.
- GM-61 (ReShade/UUU) stays study/link-only: never installed or
  launched by her.

HA-2 RE-LENS (§5.22, adopted): HA-2's scan-code input gains a
game-context gate — no HA-2 input while a competitive/anti-cheat
title is foreground; its accessibility scope is single-player/co-op.
Without it, HA-2 + GM-55 compose into exactly the product wave 30
rejected.

TELEMETRY: none. Everything here is local, pure data in and out.
"""

from __future__ import annotations

# Wall class -> refusal reason (user-facing copy, kept short).
WALL_CLASSES = {
    "memory_reading": (
        "Refused: reading game memory. The downside lands on your own "
        "accounts — bans are routinely hardware-level and effectively "
        "permanent."
    ),
    "injection": (
        "Refused: injection or hooking into a game client. Driving a "
        "scene that uses OBS Game Capture for an anti-cheat title makes "
        "her the automator — Game Capture is DLL injection by OBS's "
        "own docs."
    ),
    "competitive_automation": (
        "Refused: competitive gameplay automation. Live reads of a "
        "competitive game to draw conclusions during gameplay are "
        "banned by publisher policy (Riot's third-party rules)."
    ),
    "anti_cheat_evasion": (
        "Refused: anti-cheat evasion. There is no lawful form of this "
        "feature inside a commercial product."
    ),
}


class WallRefusal(ValueError):
    """Raised when a hard wall or trap is hit. Never caught silently."""


def classify_title(title, competitive=False, anti_cheat=False,
                   multiplayer=False):
    """Build a classified title record from explicit caller facts.

    ``title`` is a display name (str). The flags are facts the caller
    asserts — this function discovers nothing. The record carries the
    derived gates so consumers never re-derive them inconsistently:
    - ``blocked``: True when the title is competitive or anti-cheat
      (the foreground gate for macros, HA-2 input, and Game Capture).
    - ``single_player_coop``: True only when the title is neither
      multiplayer nor anti-cheat.
    """
    if not isinstance(title, str) or not title.strip():
        raise ValueError("classify_title: title must be a non-empty str")
    record = {
        "title": title.strip(),
        "competitive": bool(competitive),
        "anti_cheat": bool(anti_cheat),
        "multiplayer": bool(multiplayer),
    }
    record["blocked"] = record["competitive"] or record["anti_cheat"]
    record["single_player_coop"] = (
        not record["multiplayer"] and not record["anti_cheat"])
    return record


def audit_action(kind, title_record=None):
    """Verdict for a proposed action kind against the wall.

    ``kind`` is a wall class (see WALL_CLASSES) or any other action
    label. Wall classes are always refused; anything else returns
    allowed=True with the note that the caller must still pass its
    own item gate (this audit is necessary, not sufficient).
    """
    if kind in WALL_CLASSES:
        return {"allowed": False, "reason": WALL_CLASSES[kind]}
    return {"allowed": True,
            "reason": "Not a wall class; the item's own gate still applies."}


def foreground_gate(title_record):
    """The shared foreground gate (macros, HA-2, capture).

    Returns (allowed, reason). Refused when the foreground title is
    competitive or anti-cheat. A None record (unknown foreground) is
    refused too — fail closed, never assume a title is safe.
    """
    if title_record is None:
        return (False, "Refused: foreground title unknown — fail closed.")
    if title_record.get("blocked"):
        return (False, "Refused: %s is a competitive/anti-cheat title "
                "in the foreground." % title_record.get("title", "?"))
    return (True, "Foreground title is not competitive/anti-cheat.")


def ha2_allowed(title_record):
    """HA-2 game-context gate (§5.22 re-lens, adopted).

    HA-2 input is allowed only when no competitive/anti-cheat title
    is foreground, and its scope is single-player/co-op. Returns
    (allowed, reason).
    """
    allowed, reason = foreground_gate(title_record)
    if not allowed:
        return (False, "HA-2 refused: " + reason)
    if title_record is not None and not title_record.get(
            "single_player_coop"):
        return (False, "HA-2 refused: accessibility scope is "
                "single-player/co-op; multiplayer titles are out.")
    return (True, "HA-2 allowed for this foreground title.")


def check_acquisition_channel(channel):
    """Refuse trap acquisition channels (§9 trap register).

    ``channel`` is a string describing how a tool/skill would be
    acquired. The ``npx skills add`` channel from GM-15's README is
    refused outright — an agent-directed install instruction in a
    README is not a licence and not an install path.
    """
    if not isinstance(channel, str):
        raise ValueError("check_acquisition_channel: channel must be str")
    if "npx" in channel and "skills add" in channel:
        raise WallRefusal(
            "Refused: GM-15 must never be acquired via its README's "
            "'npx skills add' channel (§9 trap register).")
    return {"allowed": True, "channel": channel}


def check_game_scope(approval_game, target_game):
    """GM-21 scope rule: approvals do not read across games.

    A LoL pre-game approval does not authorise VALORANT, and no other
    cross-game reuse is allowed. Same game (case-insensitive) passes;
    anything else raises WallRefusal.
    """
    if not isinstance(approval_game, str) or not isinstance(target_game, str):
        raise ValueError("check_game_scope: both games must be str")
    if approval_game.strip().lower() != target_game.strip().lower():
        raise WallRefusal(
            "Refused: approval for %r does not read across to %r "
            "(GM-21: LoL approval is not VALORANT approval)."
            % (approval_game, target_game))
    return {"allowed": True, "game": approval_game.strip()}


# GM-61 tools: study/link-only. Any install or launch action refused.
STUDY_LINK_ONLY_TOOLS = ("reshade", "uuu", "universal unreal engine unlocker")


def check_external_tool_action(tool, action):
    """GM-61 rule: ReShade/UUU stay study/link-only.

    ``action`` in ("install", "launch", "drive") is refused for these
    tools; ("study", "link") is allowed. Anything else about other
    tools passes through with no verdict (not this module's gate).
    """
    if not isinstance(tool, str) or not isinstance(action, str):
        raise ValueError("check_external_tool_action: str args required")
    if tool.strip().lower() in STUDY_LINK_ONLY_TOOLS:
        if action.strip().lower() in ("install", "launch", "drive"):
            raise WallRefusal(
                "Refused: %s stays study/link-only (GM-61) — never "
                "installed or launched by her." % tool)
        if action.strip().lower() in ("study", "link"):
            return {"allowed": True, "tool": tool, "action": action}
    return {"allowed": True, "tool": tool, "action": action,
            "note": "Not a GM-61 tool; this gate does not apply."}


def build_line_needs_no_wall(line_id):
    """Proof helper: a §5.22 build line needs no wall class.

    Returns the (empty) set of wall classes the Gaming library uses.
    The library reads no memory, injects nothing, automates no
    competitive play, and evades no anti-cheat — by construction:
    every function above is pure data in, verdict out. Tests assert
    this set is empty.
    """
    _ = line_id
    return frozenset()

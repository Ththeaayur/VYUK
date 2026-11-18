"""Gaming deltas, Wave 9 Build 7 (P14) — descriptors and
records only, under the shipped v5.78 Gaming wall.

Per build_flow_v6.md: "Part 14, P14-1–P14-20 (gamers)
— BUILD, Wave 9, under the shipped v5.78 Gaming wall,
which this batch restates and extends without changing
shipped code: no memory-reading, no injection, no
competitive automation, no anti-cheat evasion; macros
single-player and co-op only. Excluded-for-bundling
items from the Gaming track (OBS GPL-2.0, FFmpeg GPL
builds with libx264, LosslessCut GPL-2.0-only) stay
drive-only forms only."

WHAT THIS MODULE IS
-------------------
* A game-session descriptor: a caller-supplied game
  title, a mode label of exactly "single-player" or
  "co-op", and session notes in, a plain-language
  session record out. It is never a live game read:
  the game is not touched, read, or observed here.
  Descriptor forms built here, by P14 item: P14-1 game
  mode scene (the scene record only — the "closes the
  apps he names" action is NOT built), P14-3 what
  should we play (a caller-supplied pick record, never
  a recommendation from play history), P14-9
  second-screen companion (a companion note), P14-10
  spoiler-safe lore buddy (a lore question record —
  the answer is caller-supplied, never generated lore),
  P14-14 achievement and backlog coach (a
  caller-supplied progress record), P14-18 squad
  scheduler (a caller-supplied schedule note, never a
  calendar read or invite), P14-20 break and eyes (a
  break note only — it schedules nothing; the WELL
  break plan is the scheduling-free sibling, and no
  reminder is set here).
* A macro descriptor: caller-supplied macro steps in,
  a plain-language macro record out, with mode exactly
  "single-player" or "co-op". Descriptor forms built
  here, by P14 item: P14-6 voice that stays out of
  the fight (voice-command labels), P14-15 voice
  commands for single-player games (command labels),
  P14-17 one-handed and low-effort modes (layout
  suggestion labels). A macro record is never
  executed: describing steps does not perform them.
* A drive-only media note descriptor: a
  caller-supplied tool name and licence label in, a
  note out stating the tool is NOT bundled and must
  be driven by the user themselves. This is the form
  for the excluded-for-bundling GPL items — OBS
  (GPL-2.0), FFmpeg GPL builds with libx264, and
  LosslessCut (GPL-2.0-only) — and for any other
  caller-named tool: the note never claims the tool
  is shipped, integrated, or runnable by Nova.
* A re-validation rule for recording a gaming
  descriptor: the descriptor is re-built from its own
  fields and must match exactly, or it is refused,
  never written on trust.

WHAT THIS MODULE IS NOT — HARD REFUSALS
---------------------------------------
Any call carrying a refused kind of input refuses WHOLE,
never with a partial answer:
* any memory-reading, injection, cheat, anti-cheat
  evasion, or competitive-automation input — a title,
  note, step, or action naming one refuses the whole
  call;
* any competitive, multiplayer-ranked, or
  online-competitive mode — the mode must be exactly
  "single-player" or "co-op" (after normalising
  "single player" / "co op" spellings); any other mode
  refuses the whole macro or session, never
  downgraded to a safe mode;
* any claim a game was read, controlled, automated,
  or observed live, or that a GPL tool is bundled,
  integrated, shipped, or runnable by Nova — such a
  claim refuses the whole call, and the module's own
  outputs always carry read_live False, controlled
  False, automated False, and, for media notes,
  bundled False, integrated False, shipped False;
* any macro step shaped as an automated input
  sequence to a live game — a step naming a key
  press, click, mouse move, keystroke, send-input, or
  other automated input refuses the whole macro.
The module imports no game-memory, injection,
input-automation, or screen-capture code — proven by
the AST test in tests/test_gaming_w9.py. It reads no
game, controls no game, automates no input, and
bundles no GPL tool: there is nothing here to do any
of that with, by construction. Descriptors only,
records only.

P14 SLICES NOT BUILT HERE, NAMED NOT DROPPED
---------------------------------------------
* P14-2 pre-flight check as a live check (driver and
  disk reads) — not built; a caller-supplied checklist
  may ride in session notes, but nothing is checked.
* P14-4 save-game guardian (automatic backups before
  updates) — not built; copying saves is a file action
  needing its own build and its own undo story.
* P14-5 footprint pledge (dropping to near-zero use
  while a game runs) — not built; it needs live
  process control.
* P14-7 smart audio as an action (per-app volume,
  ducking music) — not built; volume control is a
  live audio action, not a descriptor.
* P14-8 reacting lighting (room and keyboard lights
  responding to a game) — not built; it needs
  hardware control plus a live game signal.
* P14-11 why did it stutter (frame-time dip report)
  — not built; it needs live performance measurement.
* P14-12 network doctor (continuous ping, jitter,
  packet-loss tracking) — not built; it is live
  network measurement, and the NET wall refuses it
  too.
* P14-13 revert timer for settings (timed revert) —
  not built; a timed action is a scheduled action.
* P14-16 mod butler (safe install with backup and
  conflict handling) — not built; installing mods is
  a file-and-install action needing its own build.
* P14-1's "closes the apps he names" action, P14-15's
  execution of a voice command, and P14-19's scene
  switching — actions, not descriptors, not built.
* P14-19 streamer helper beyond a note: scene
  switching and chat reading are not built; the
  drive-only media note is the only streamer form
  here, and OBS stays drive-only.

FAIL-CLOSED, WHOLE-CALL
-----------------------
A refused mode, a banned phrase, a live-game claim, a
bundled-tool claim, an automation-shaped step, or a
missing field refuses the whole call. Nothing is
relabeled, downgraded, executed, or quietly bundled:
a game Nova did not read is worse than no session
record.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only — in fact, no imports
beyond ``__future__`` annotations. No new dependency,
no telemetry write, no database access, no game
access. Nothing here reads or writes the install
tree's ``data/`` directory. No GPL tool is bundled,
vendored, linked, or shipped by this module: OBS,
FFmpeg GPL builds with libx264, and LosslessCut are
named only, in drive-only notes, with their licence
labels as supplied or as recorded in the plan.
"""

from __future__ import annotations

__all__ = [
    "MAX_TITLE_CHARS",
    "MAX_NOTES_CHARS",
    "MAX_STEPS",
    "MAX_STEP_CHARS",
    "ALLOWED_MODES",
    "COMPETITIVE_MODES",
    "BANNED_PHRASES",
    "AUTOMATION_STEP_WORDS",
    "GAME_CLAIM_TOKENS",
    "DRIVE_ONLY_TOOLS",
    "build_game_session",
    "build_game_macro",
    "build_media_note",
    "validate_recordable_gaming",
]

#: Caller-supplied bounds. A longer field refuses whole,
#: never truncated into a different gaming record.
MAX_TITLE_CHARS = 200
MAX_NOTES_CHARS = 1000
MAX_STEPS = 50
MAX_STEP_CHARS = 200

#: The only modes this module accepts, after spelling
#: normalisation ("single player" -> "single-player",
#: "co op" -> "co-op").
ALLOWED_MODES = frozenset({"single-player", "co-op"})

#: Modes that refuse a call whole. "multiplayer" on its
#: own is refused too: only single-player and co-op
#: are described here, and a co-op game is named as
#: co-op, never as multiplayer.
COMPETITIVE_MODES = frozenset({
    "competitive", "ranked", "multiplayer",
    "online-competitive", "online", "pvp", "esports",
    "tournament", "ladder", "multiplayer-ranked",
    "ranked-multiplayer", "battle-royale",
})

#: Phrases that refuse a call whole wherever they
#: appear in free text. Substring scan on lowered text:
#: "inject" also catches "injection", "cheat" also
#: catches "cheat code" — fail-closed is the wall.
BANNED_PHRASES = (
    "memory read", "read memory", "read process",
    "process memory", "inject", "aimbot", "wallhack",
    "cheat", "anti-cheat", "anticheat", "bypass",
    "evade anti", "trainer", "dll hook", "hook dll",
    "read game", "game memory", "read the game",
    "game was read", "game was controlled",
    "game was automated", "game was observed",
    "control the game", "automate the game",
)

#: Whole words that make a macro step an automated
#: input sequence to a live game. A step carrying one
#: refuses the whole macro: macro records are labels,
#: never input sequences.
AUTOMATION_STEP_WORDS = frozenset({
    "press", "click", "keystroke", "key", "mouse",
    "automate", "bot", "sendinput", "typewrite",
})

#: Words claiming a live game interaction. A note or
#: step carrying one as a claim about this call
#: refuses whole; the module's own outputs never
#: carry them as True.
GAME_CLAIM_TOKENS = frozenset({
    "read", "controlled", "automated", "observed",
    "injected", "hooked",
})

#: Excluded-for-bundling GPL tools, named only, in
#: drive-only form. Licence labels are as recorded in
#: the plan; a caller-supplied label is echoed as
#: supplied, never upgraded to a clearance.
DRIVE_ONLY_TOOLS = {
    "obs": "GPL-2.0",
    "ffmpeg": "GPL build with libx264",
    "losslesscut": "GPL-2.0-only",
}


def _tokens_in(text):
    """Lower-cased, word-ish tokens of a free-text input,
    for refusal scanning."""
    lowered = str(text).lower()
    current = []
    for char in lowered:
        if char.isalpha() or char == "-":
            current.append(char)
        else:
            if current:
                yield "".join(current)
                current = []
    if current:
        yield "".join(current)


def _scan_banned(field, value):
    lowered = str(value).lower()
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            raise ValueError(
                f"{field} carries refused gaming-wall input "
                f"({phrase!r}): no memory-reading, injection, "
                f"cheating, or anti-cheat evasion is built here")


def _check_text(field, value, maximum, required=True):
    if value is None:
        if required:
            raise ValueError(f"{field} must not be empty")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} chars")
    stripped = value.strip()
    if required and not stripped:
        raise ValueError(f"{field} must not be empty")
    _scan_banned(field, stripped)
    return stripped


def _check_mode(mode):
    if not isinstance(mode, str):
        raise ValueError("mode must be a string")
    cleaned = mode.strip().lower().replace(" ", "-")
    if cleaned in ALLOWED_MODES:
        return cleaned
    if cleaned in COMPETITIVE_MODES:
        raise ValueError(
            f"mode {mode!r} refused: macros and sessions "
            f"are single-player or co-op only, never "
            f"competitive, ranked, or multiplayer")
    raise ValueError(
        f"mode {mode!r} refused: only 'single-player' or "
        f"'co-op' are accepted here")


def _check_claim_flags(field, value):
    if value is True:
        raise ValueError(
            f"{field} claims a live game interaction or a "
            f"bundled tool: this module describes only, "
            f"reads nothing, controls nothing, bundles "
            f"nothing")
    return value


def build_game_session(game_title, mode, notes=None, action="describe"):
    """A game-session descriptor from caller-supplied
    fields only.

    The title, mode, and notes are as supplied, never
    read from a live game. The mode must be exactly
    "single-player" or "co-op". Returns the descriptor
    as data, with read_live, controlled, and automated
    all False and a note saying so."""
    if action is not None:
        if not isinstance(action, str):
            raise ValueError("action must be a string")
        cleaned_action = action.strip().lower()
        if cleaned_action != "describe":
            raise ValueError(
                f"action {cleaned_action!r} refused: only "
                f"'describe' is performed here")
    title = _check_text("game_title", game_title, MAX_TITLE_CHARS)
    cleaned_mode = _check_mode(mode)
    note_text = _check_text("notes", notes, MAX_NOTES_CHARS,
                            required=False)
    for token in _tokens_in(note_text):
        if token in GAME_CLAIM_TOKENS and token != "read":
            raise ValueError(
                f"notes claim a live game interaction "
                f"({token!r}): this module reads nothing, "
                f"controls nothing, automates nothing")
    return {
        "kind": "GAME_SESSION",
        "game_title": title,
        "mode": cleaned_mode,
        "notes": note_text,
        "read_live": False,
        "controlled": False,
        "automated": False,
        "note": ("A session descriptor only, from the title, "
                 "mode, and notes you supplied. No game was "
                 "read, controlled, automated, or observed "
                 "live — this module touches no game."),
    }


def build_game_macro(mode, steps, action="describe"):
    """A macro descriptor from caller-supplied steps only.

    Steps are labels (voice-command labels, layout
    suggestion labels), in the order given, duplicates
    kept once and reported. The mode must be exactly
    "single-player" or "co-op". A competitive mode, a
    banned phrase, or an automation-shaped step refuses
    the whole macro. The macro is never executed:
    describing steps does not perform them."""
    if action is not None:
        if not isinstance(action, str):
            raise ValueError("action must be a string")
        cleaned_action = action.strip().lower()
        if cleaned_action != "describe":
            raise ValueError(
                f"action {cleaned_action!r} refused: only "
                f"'describe' is performed here")
    cleaned_mode = _check_mode(mode)
    if not isinstance(steps, (list, tuple)):
        raise ValueError("steps must be a list of step labels")
    if not steps:
        raise ValueError("steps must not be empty")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"steps exceeds {MAX_STEPS} entries")
    cleaned_steps = []
    seen = set()
    duplicates = []
    for step in steps:
        text = _check_text("step", step, MAX_STEP_CHARS)
        for token in _tokens_in(text):
            if token in AUTOMATION_STEP_WORDS:
                raise ValueError(
                    f"step {text!r} is shaped as an automated "
                    f"input sequence ({token!r}): macro "
                    f"records are labels, never input "
                    f"sequences to a live game")
        if text in seen:
            duplicates.append(text)
            continue
        seen.add(text)
        cleaned_steps.append(text)
    return {
        "kind": "GAME_MACRO",
        "mode": cleaned_mode,
        "steps": cleaned_steps,
        "duplicates_removed": duplicates,
        "executed": False,
        "read_live": False,
        "controlled": False,
        "automated": False,
        "note": ("A macro descriptor only, from the steps you "
                 "supplied. Nothing was executed, no game was "
                 "read or controlled, and no input was sent."),
    }


def build_media_note(tool_name, licence_label=None, action="describe",
                     bundled=None, integrated=None, shipped=None):
    """A drive-only media note descriptor for a
    caller-supplied tool.

    The tool is NOT bundled and must be driven by the
    user themselves. Any bundled, integrated, or shipped
    claim refuses the whole call, and the note itself
    always carries bundled False, integrated False, and
    shipped False. For the known excluded-for-bundling
    GPL tools (OBS, FFmpeg GPL builds with libx264,
    LosslessCut) the plan-recorded licence label is
    used when the caller supplies none; any other tool
    keeps its caller-supplied label, echoed as supplied,
    never upgraded to a clearance."""
    if action is not None:
        if not isinstance(action, str):
            raise ValueError("action must be a string")
        cleaned_action = action.strip().lower()
        if cleaned_action != "describe":
            raise ValueError(
                f"action {cleaned_action!r} refused: only "
                f"'describe' is performed here")
    _check_claim_flags("bundled", bundled)
    _check_claim_flags("integrated", integrated)
    _check_claim_flags("shipped", shipped)
    name = _check_text("tool_name", tool_name, MAX_TITLE_CHARS)
    key = name.strip().lower().replace(" ", "")
    if licence_label is None:
        label = DRIVE_ONLY_TOOLS.get(key)
        if label is None:
            raise ValueError(
                "licence_label must be supplied for a tool "
                "without a plan-recorded label")
    else:
        label = _check_text("licence_label", licence_label,
                            MAX_TITLE_CHARS)
        if key in DRIVE_ONLY_TOOLS and label != DRIVE_ONLY_TOOLS[key]:
            raise ValueError(
                f"licence_label for {name!r} does not match "
                f"the plan-recorded label "
                f"{DRIVE_ONLY_TOOLS[key]!r}: a GPL tool's "
                f"label is never softened here")
    return {
        "kind": "MEDIA_NOTE",
        "tool_name": name,
        "licence_label": label,
        "bundled": False,
        "integrated": False,
        "shipped": False,
        "drive_only": True,
        "note": ("A drive-only note only. This tool is NOT "
                 "bundled, integrated, or shipped with Nova, "
                 "and Nova cannot run it for you: drive it "
                 "yourself, under its own licence."),
    }


def validate_recordable_gaming(kind, fields):
    """Re-validate a gaming descriptor a caller wants
    recorded, by re-running the builder that made it and
    requiring the core fields to match exactly. A record
    that does not re-build is refused, never written on
    trust. Returns the rebuilt descriptor dict, or raises
    ValueError."""
    if not isinstance(kind, str) or not isinstance(fields, dict):
        raise ValueError("kind must be a string and fields an object")
    kind = kind.strip()
    allowed_fields = {
        "session": {"game_title", "mode", "notes"},
        "macro": {"mode", "steps"},
        "media-note": {"tool_name", "licence_label"},
    }
    if kind not in allowed_fields:
        raise ValueError(f"unknown recordable gaming kind {kind!r}")
    unexpected = sorted(set(fields) - allowed_fields[kind])
    if unexpected:
        raise ValueError(
            f"gaming descriptor carries unexpected fields "
            f"{unexpected}: a record that does not re-build "
            f"exactly is refused, never written on trust")
    if kind == "session":
        rebuilt = build_game_session(
            fields.get("game_title"), fields.get("mode"),
            fields.get("notes"))
        if fields.get("game_title", "").strip() \
                != rebuilt["game_title"] \
                or fields.get("mode", "").strip().lower() \
                    .replace(" ", "-") != rebuilt["mode"] \
                or (fields.get("notes") or "").strip() \
                != rebuilt["notes"]:
            raise ValueError("game session fields do not re-build")
        return rebuilt
    if kind == "macro":
        rebuilt = build_game_macro(fields.get("mode"),
                                   fields.get("steps"))
        if fields.get("mode", "").strip().lower() \
                .replace(" ", "-") != rebuilt["mode"]:
            raise ValueError("game macro mode does not re-build")
        steps = fields.get("steps")
        if not isinstance(steps, (list, tuple)):
            raise ValueError("game macro steps must be a list")
        deduped = []
        for step in steps:
            text = step.strip() if isinstance(step, str) else ""
            if text not in deduped:
                deduped.append(text)
        if deduped != rebuilt["steps"]:
            raise ValueError("game macro steps do not re-build")
        return rebuilt
    if kind == "media-note":
        rebuilt = build_media_note(fields.get("tool_name"),
                                   fields.get("licence_label"))
        if fields.get("tool_name", "").strip() \
                != rebuilt["tool_name"]:
            raise ValueError("media note tool does not re-build")
        supplied = fields.get("licence_label")
        if supplied is None:
            key = rebuilt["tool_name"].strip().lower().replace(" ", "")
            if key not in DRIVE_ONLY_TOOLS:
                raise ValueError("media note licence does not "
                                 "re-build")
        elif supplied.strip() != rebuilt["licence_label"]:
            raise ValueError("media note licence does not re-build")
        return rebuilt
    raise ValueError(f"unknown recordable gaming kind {kind!r}")

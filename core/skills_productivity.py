"""
Productivity pack for VYUK (v5.11) — text snippets: named pieces of
text the user saves once ("my address", "bank details block", "email
signature") and VYUK pastes into whatever app is focused, on command.

Storage follows the plans/routines discipline (core/brain/planner.py):
data/snippets.json holds {name: {"text": ..., "saved_at": iso}}.
Loads are hardened at the boundary — a corrupt FILE is reported and
treated as empty, invalid ENTRIES are dropped and the drop count is
disclosed to the caller, and saves rewrite the file atomically
(temp file + os.replace), so dropped entries fall out on the next
save and a crash can never half-write the store. Mutations are
serialised in-process (a lock spans read through commit) and the
temp file is unique per write (F-01, 2026-10-30) — see the store
section below for the design and its stated cross-process residual.

Two honesty rules are load-bearing here:

- Snippet text is NEVER echoed in a result. Snippets exist to hold
  things like addresses and bank details; results carry the name and
  the character count only — including the list tool, which shows
  names, counts and dates, never contents.
- paste_snippet puts UNSEEN text into another app, so it reuses the
  established hands rather than growing its own: the clipboard write
  is core/desktop/control.py's write_clipboard (paste-back verified), the
  paste chord is control.press_hotkey (which checks the failsafe),
  and the failsafe is consulted BEFORE anything is touched — when it
  has tripped, its refusal is returned verbatim with zero dispatches.
  The parent additionally gates this tool statically in tools_gate_confirm.py (_CONFIRM_GATE_TOOLS),
  exactly like paste_text.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

_MAX_NAME_CHARS = 40
_MAX_TEXT_CHARS = 4000


# ---------------------------------------------------------------------------
# The store — data/snippets.json, planner-style load discipline
# ---------------------------------------------------------------------------
#
# F-01 (Final Systemic Audit, 2026-10-30): the store used to be an
# unlocked read-modify-write with a FIXED temp name (snippets.json.tmp),
# so two concurrent saves could lose one update (stale snapshot,
# last-writer-wins) and interleave in the shared temp file. Both
# mechanisms are closed here: _STORE_LOCK spans READ THROUGH COMMIT
# in every mutating entry point (save_snippet, delete_snippet), and
# _save_store writes a mkstemp-unique temp file, never a fixed name.
# Reads (list, paste) need no lock: os.replace is atomic, so a read
# sees the old file or the new file, never a torn one. Residual,
# stated plainly: the lock is in-process only — two separate
# processes writing the same file can still lose one update
# (last-writer-wins); the unique temp names at least guarantee
# neither process can corrupt the file or see a torn write.
# A cross-process file lock is future work (it needs Windows
# verification this lab cannot provide).


_STORE_LOCK = threading.RLock()


def _store_path() -> Path:
    """Where snippets live: the app's data dir (core/config.py's
    DATA_DIR — the same helper google_services uses), resolved lazily
    so tests can point it at a temp dir."""
    from .config import DATA_DIR

    return DATA_DIR / "snippets.json"


def _valid_entry(value) -> dict | None:
    """Normalise one stored entry, or None when it isn't usable.

    An entry is usable when it's a dict whose "text" is a non-empty
    string (save_snippet could never have written anything else).
    A missing/invalid "saved_at" doesn't condemn the entry — it's
    normalised to "" and listings say the date is unknown."""
    if not isinstance(value, dict):
        return None
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    saved_at = value.get("saved_at")
    return {"text": text,
            "saved_at": saved_at if isinstance(saved_at, str) else ""}


def _load_store() -> tuple[dict, int, bool]:
    """(store, dropped_count, file_corrupt).

    Missing file -> empty store, nothing to disclose. Corrupt file
    (unreadable, invalid JSON, or not a JSON object) -> empty store
    with file_corrupt=True so callers can say so. Invalid entries in
    a readable file are dropped and counted."""
    path = _store_path()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        return {}, 0, False
    except Exception:  # noqa: BLE001 — corrupt state must never crash a tool
        return {}, 0, True
    if not isinstance(data, dict):
        return {}, 0, True
    store: dict = {}
    dropped = 0
    for name, value in data.items():
        entry = _valid_entry(value) if isinstance(name, str) and name else None
        if entry is None:
            dropped += 1
        else:
            store[name] = entry
    return store, dropped, False


def _save_store(store: dict) -> bool:
    """Write the whole store atomically (unique temp file +
    os.replace), the planner/routine_scheduler way. False when the
    write failed. The temp file is mkstemp-unique (F-01): a fixed
    temp name let concurrent writers interleave in one file."""
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(store, indent=2,
                                        ensure_ascii=False))
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception:  # noqa: BLE001
        return False


def _find_key(store: dict, name: str) -> str | None:
    """The stored key matching `name` case-insensitively, or None."""
    wanted = name.strip().lower()
    for key in store:
        if key.lower() == wanted:
            return key
    return None


def _names_line(store: dict) -> str:
    """'a' (120 characters), 'b' (45 characters) — names and counts
    only, never contents. Used by the unknown-name results."""
    parts = [f"'{key}' ({len(entry['text'])} character(s))"
             for key, entry in
             sorted(store.items(), key=lambda kv: kv[0].lower())]
    return ", ".join(parts)


def _damaged_note(dropped: int, corrupt: bool) -> str:
    """The load-boundary disclosure, appended to reading results."""
    notes = []
    if corrupt:
        notes.append("The snippets file (data/snippets.json) couldn't "
                     "be read — it looks damaged, so I'm treating it "
                     "as empty.")
    if dropped:
        notes.append(f"Skipped {dropped} invalid saved "
                     f"entr{'y' if dropped == 1 else 'ies'} that "
                     "couldn't be read.")
    return " ".join(notes)


# ---------------------------------------------------------------------------
# SAVE — upsert, case-insensitive, replace disclosed
# ---------------------------------------------------------------------------


def save_snippet(name: str, text: str) -> str:
    """Save (or replace) a named text snippet for later pasting."""
    name = str(name or "").strip()
    if not name:
        return ("A snippet needs a name — e.g. save_snippet with the "
                "name 'address'.")
    if len(name) > _MAX_NAME_CHARS:
        return (f"That name is {len(name)} characters — snippet names "
                f"are capped at {_MAX_NAME_CHARS}. Shorten it and I'll "
                "save it.")
    text = "" if text is None else str(text)
    if not text.strip():
        return ("The snippet text is empty — there's nothing worth "
                "saving under that name.")
    if len(text) > _MAX_TEXT_CHARS:
        return (f"That text is {len(text)} characters — a snippet is "
                f"capped at {_MAX_TEXT_CHARS}. Split it into smaller "
                "snippets and I'll save each one.")
    # F-01: the whole read-modify-write runs under _STORE_LOCK, so
    # two concurrent saves cannot load the same stale snapshot and
    # lose one update. (Validation above needs no lock.)
    with _STORE_LOCK:
        store, _dropped, corrupt = _load_store()
        old_key = _find_key(store, name)
        if old_key is not None and old_key != name:
            del store[old_key]
        store[name] = {"text": text,
                       "saved_at": datetime.now().isoformat(timespec="seconds")}
        if not _save_store(store):
            return (f"Couldn't save snippet '{name}' — writing the "
                    "snippets file failed.")
    result = f"Saved snippet '{name}' ({len(text)} character(s))."
    if old_key is not None:
        result += (f" It replaced the old '{old_key}'."
                   if old_key != name else
                   " It replaced the previous version.")
    if corrupt:
        result += (" The previous snippets file was damaged and "
                   "unreadable, so this is a fresh start.")
    return result


# ---------------------------------------------------------------------------
# LIST — names, counts, dates. Never contents. (READ-ONLY)
# ---------------------------------------------------------------------------


def list_snippets(filter: str = "") -> str:
    """List the saved snippets: names, lengths, save dates.

    With `filter` (a word or part of a name), only snippets whose
    name contains it (case-insensitive) are listed and the header
    says how many of the whole set matched; no filter keeps the
    original full listing byte for byte."""
    store, dropped, corrupt = _load_store()
    note = _damaged_note(dropped, corrupt)
    if not store:
        base = ("No snippets saved yet. Save one with save_snippet — "
                "e.g. the name 'address' with your address as the text.")
        return f"{base} {note}".strip() if note else base
    needle = str(filter or "").strip().lower()
    keys = sorted(store, key=str.lower)
    if needle:
        keys = [k for k in keys if needle in k.lower()]
        if not keys:
            base = (f"No snippet has '{filter}' in its name — "
                    f"{len(store)} snippet(s) are saved under other "
                    "names. List them all with list_snippets and no "
                    "filter.")
            return f"{base} {note}".strip() if note else base
        lines = [f"Snippets matching '{filter}' "
                 f"({len(keys)} of {len(store)}):"]
    else:
        lines = [f"Snippets ({len(store)}):"]
    for key in keys:
        entry = store[key]
        saved = entry["saved_at"]
        when = f"saved {saved[:10]}" if len(saved) >= 10 else "save date unknown"
        lines.append(f"• '{key}' — {len(entry['text'])} character(s), {when}")
    if note:
        lines.append(note)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def delete_snippet(name: str) -> str:
    """Delete a saved snippet by name (case-insensitive)."""
    name = str(name or "").strip()
    if not name:
        return "Deleting a snippet needs its name."
    # F-01: same read-through-commit lock as save_snippet.
    with _STORE_LOCK:
        store, _dropped, _corrupt = _load_store()
        key = _find_key(store, name)
        if key is None:
            extra = (f" Saved snippets: {_names_line(store)}."
                     if store else " No snippets are saved yet.")
            # F-F501: nothing was deleted, so the verdict is failed,
            # not a plain string the receipts would read
            # "succeeded".
            from . import tool_outcome as _outcome  # R1b: seam wiring
            return _outcome.failed(
                f"There's no snippet called '{name}'.{extra}",
                "snippet missing")
        del store[key]
        if not _save_store(store):
            return (f"Couldn't delete snippet '{key}' — writing the "
                    "snippets file failed.")
    return f"Deleted snippet '{key}'."


# ---------------------------------------------------------------------------
# PASTE — clipboard + Ctrl+V through control.py's hands, never echoed
# ---------------------------------------------------------------------------


def paste_snippet(name: str) -> str:
    """Paste a saved snippet into the currently focused app.

    The text goes through control.write_clipboard (paste-back
    verified) and control.press_hotkey('ctrl+v'); the failsafe is
    consulted first and its refusal returned verbatim. The result
    names the snippet and its length — never the text."""
    name = str(name or "").strip()
    if not name:
        return "Pasting a snippet needs its name."
    store, _dropped, _corrupt = _load_store()
    key = _find_key(store, name)
    if key is None:
        extra = (f" Saved snippets: {_names_line(store)}."
                 if store else " No snippets are saved yet.")
        return f"You don't have a snippet called '{name}'.{extra}"
    text = store[key]["text"]
    try:
        from .desktop import control  # Phase 3 D4a: control hub moved to core/desktop/
    except Exception as e:  # noqa: BLE001
        return f"Couldn't paste snippet '{key}': the input tools failed to load ({e})."
    blocked = control._input_blocked()
    if blocked:
        return blocked
    # H20 (v5.45): the shared password rule — a paste is typing, so
    # core/input_guard decides before any keystroke is injected.
    try:
        from . import input_guard

        _pw_blocked = input_guard.typing_blocked_reason()
    except Exception:  # noqa: BLE001
        _pw_blocked = None
    if _pw_blocked:
        return _pw_blocked
    clip = control.write_clipboard(text)
    if not clip.startswith("Copied "):
        return clip  # write_clipboard's own honest failure wording
    try:
        chord = control.press_hotkey("ctrl+v")
    except Exception as e:  # noqa: BLE001
        return (f"Snippet '{key}' is on the clipboard, but pasting "
                f"failed: {e} — press Ctrl+V yourself to use it.")
    # R1b: press_hotkey may now return an Outcome — read it through
    # str(...) for the substring checks.
    chord_text = str(chord)
    if "failsafe has tripped" in chord_text:
        return chord
    if "Could not" in chord_text or "requires" in chord_text:
        return (f"Snippet '{key}' is on the clipboard, but the paste "
                f"keystroke failed: {chord_text} Press Ctrl+V yourself to "
                "use it.")
    # R1b: the clipboard write is verified (R1a paste-back), but the
    # ctrl+v landing cannot be read back — mark it honestly
    # unverified.
    # B13: the vision landing check asks the B5 backend whether the
    # snippet's text is on screen (Granite read). Where vision
    # cannot read, the honestly-unverified return stands exactly as
    # before (same message, same kind).
    from . import tool_outcome as _outcome  # R1b: seam wiring

    return _outcome.hands_landing_check(
        "paste", text,
        f"Pasted snippet '{key}' ({len(text)} character(s)) into "
        "the focused window. The text stays on the clipboard, so "
        "you can paste it again. (Sent as a paste keystroke; "
        "whether it landed was not confirmed.)")


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Productivity pack tool on a ToolRegistry-like object."""
    registry.register(
        name="save_snippet",
        description="Save a named piece of text (an address, an email signature, a bank-details block, a standard reply…) so it can be pasted later with paste_snippet. Names are case-insensitive and up to 40 characters: saving under an existing name replaces it, and the result says so. Text is capped at 4000 characters. Stored in VYUK's data folder (data/snippets.json).",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The snippet's name, e.g. 'address' or 'email signature'"},
                "text": {"type": "string", "description": "The exact text to save and paste later"},
            },
            "required": ["name", "text"],
        },
        handler=save_snippet,
    )
    registry.register(
        name="paste_snippet",
        description="Paste a saved snippet into the currently focused app: its text is put on the clipboard and Ctrl+V is sent. The user approves it in VYUK's confirmation popup first (it types text into another app). The result reports only the snippet's name and length — never its contents — and the text stays on the clipboard afterwards so it can be pasted again.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The snippet's name (case-insensitive), e.g. 'address'"},
            },
            "required": ["name"],
        },
        handler=paste_snippet,
    )
    registry.register(
        name="list_snippets",
        description="List the saved text snippets: their names, character counts and save dates. Contents are never shown — snippets often hold private details. Use to answer 'what snippets do I have?' or to find the right name before pasting. Pass filter (part of a name, case-insensitive) to list only the snippets whose name contains it — the result says how many of the whole set matched.",
        parameters={
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional: only list snippets whose name contains this text (case-insensitive)"},
            },
            "required": [],
        },
        handler=list_snippets,
    )
    registry.register(
        name="delete_snippet",
        description="Delete a saved text snippet by name (case-insensitive). The result confirms the name only; snippet contents are never shown. See what's saved with list_snippets; add with save_snippet.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The snippet's name (case-insensitive)"},
            },
            "required": ["name"],
        },
        handler=delete_snippet,
    )

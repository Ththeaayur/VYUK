"""Storage + load-boundary validation for core/brain/planner.py.

Split out in skills-split batch B40 (part 1 of 2). Contents: the plans /
routines file paths and limits, the atomic-ish JSON list store, the
load validators, and the small normalisation helpers the validators
depend on (_conditional_fields_valid -> _normalise_on_fail /
_normalise_when -> _json_args — the closure travels with the family so
this module's out-edges to the planner facade are ZERO).

_DATA_DIR derivation is identical by construction: this file sits in
core/brain/ exactly like planner.py, so parent.parent.parent / "data"
resolves to the same directory (B24 updater pattern).
"""

import datetime
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"  # Phase 3 B2-remainder: one level deeper in core/brain/
_PLANS_PATH = _DATA_DIR / "plans.json"
_ROUTINES_PATH = _DATA_DIR / "routines.json"

MAX_ROUTINE_ACTIONS = 20
MAX_PLAN_STEPS = 30


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _load_list(path: Path) -> list:
    """Read a JSON list from disk. Missing/wrong-type -> [].

    A corrupt FILE (unparseable JSON, unreadable) also reads [] —
    but unlike a missing file it is DISCLOSED: _note_corrupt
    records it (log line + sticky state, see store_corruption),
    so a caller can say so instead of reporting "none saved"."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        _note_corrupt(path, "not a JSON list")
        return []
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001 — corrupt state never crashes
        _note_corrupt(path, f"not valid JSON ({exc})")
        return []


def _save_list(path: Path, data: list) -> bool:
    """Write a JSON list atomically-ish (temp file + os.replace).

    F-01 audit: the temp file is mkstemp-unique, not the old fixed
    "<name>.tmp" — two concurrent saves could otherwise interleave
    in one shared temp file and promote a torn store. NOTE (stated,
    not hidden): this closes the torn-temp interleave only. Planner
    save paths (create/delete plan, save/delete routine, step-status
    and follow-on updates) are still unlocked read-modify-writes, so
    an in-process lost update across two threads saving plans at
    once remains possible; closing that needs a module lock spanning
    every save path, which is its own batch (these paths run during
    plan execution, so lock placement needs care) — it is NOT
    claimed fixed here."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Round-3 C3-F3: a corrupt file on disk must never be
        # silently replaced by a fresh write — quarantine it
        # first (original bytes kept alongside), or refuse.
        if path.exists() and _file_problem(path) is not None:
            if _quarantine_file(path) is None:
                return False
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(data, indent=2,
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


def _damaged_note(count: int) -> str:
    """Disclosure line for records skipped at load (see _load_records)."""
    if count == 1:
        return "Note: 1 damaged record was ignored."
    return f"Note: {count} damaged records were ignored."


# ---------------------------------------------------------------------------
# Corrupt-FILE disclosure + quarantine (round-3 C3-F3)
#
# _load_records counts bad ENTRIES, not a bad FILE — a wholly corrupt
# file used to load as zero records ("No routines saved yet") with no
# disclosure anywhere, and the next save silently destroyed it for
# good. The shape is the C3-F2 memory-DB precedent, adapted to a
# JSON list store: disclose (log line + sticky state + a note in
# the tool results), quarantine before any re-write, never delete
# the corrupt bytes. A genuinely MISSING file is still a healthy
# empty store (missing != corrupt) and the happy path is untouched.
# ---------------------------------------------------------------------------
_CORRUPT: dict[str, dict] = {}
_CORRUPT_LOGGED: set[str] = set()


def _file_problem(path: Path) -> str | None:
    """Why the file on disk is not a usable store, or None.

    None means healthy-or-missing: a missing file is a healthy
    empty store, a valid JSON list is a healthy store. A JSON
    parse failure, a non-list top level, or an unreadable file
    is a problem string. Read-only: never writes, never notes."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"unreadable ({exc})"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return f"not valid JSON ({exc})"
    if not isinstance(data, list):
        return "not a JSON list"
    return None


def _note_corrupt(path: Path, problem: str) -> None:
    """Record a corrupt file (first detection only per process).

    The log line names the file and the problem; the sticky
    record is what the tool results and the repair surface read.
    A later healthy read does not clear it — the file on disk
    is still corrupt until it is quarantined or replaced."""
    key = str(path)
    if key not in _CORRUPT:
        _CORRUPT[key] = {
            "path": key,
            "error": problem,
            "detected_at": _now(),
            "quarantine_path": None,
        }
    if key not in _CORRUPT_LOGGED:
        _CORRUPT_LOGGED.add(key)
        log.warning("planner store %s looks damaged (%s); reads as "
                    "empty until repaired", path, problem)


def store_corruption(path: Path) -> dict | None:
    """A copy of the corruption record for `path`, or None.

    Probes the file when nothing is recorded yet, so a caller
    that never loaded the store still gets a live answer. A
    missing or healthy file reads None (healthy empty)."""
    key = str(path)
    record = _CORRUPT.get(key)
    if record is None:
        problem = _file_problem(path)
        if problem is not None:
            _note_corrupt(path, problem)
            record = _CORRUPT.get(key)
    return dict(record) if record is not None else None


def corruption_notice(path: Path) -> str:
    """Plain-words notice for a corrupt store file, or "".

    Names the file and the quarantine that will happen on the
    next save; the caller decides the surface (tool result,
    dialog). A healthy or missing file has no notice."""
    record = store_corruption(path)
    if record is None:
        return ""
    name = Path(record["path"]).name
    return (f"Your {name} file looks damaged and could not be read "
            f"({record['error']}), so it is showing as empty. Nothing "
            f"was deleted: the file is kept as-is, and the next save "
            f"will set it aside first.")


def corrupt_note(path: Path) -> str:
    """One-line disclosure for a corrupt store file, or "".

    The tool-result shape (cf. _damaged_note): list/save
    results append this when the file they read is corrupt."""
    record = store_corruption(path)
    if record is None:
        return ""
    name = Path(record["path"]).name
    return (f"Note: {name} could not be read — the file looks damaged "
            f"({record['error']}). Nothing was deleted: the file is "
            f"kept as-is, and the next save will set it aside first.")


def reset_corruption_state() -> None:
    """Clear the sticky corruption records + once-per-process log
    flags. Tests only: production never forgets a corrupt file."""
    _CORRUPT.clear()
    _CORRUPT_LOGGED.clear()
    _LAST_QUARANTINE.clear()


# The last quarantine per store path, so a save that set a damaged
# file aside can say so in its result. Consume-once (see
# take_quarantine_notice): the saving function is the only reader.
_LAST_QUARANTINE: dict[str, str] = {}


def take_quarantine_notice(path: Path) -> str:
    """The set-aside note for a save that quarantined `path`, or "".

    Consumes the record: one save, one note. The name is the
    quarantine file's name (the bytes live next to the store)."""
    kept = _LAST_QUARANTINE.pop(str(path), None)
    if kept is None:
        return ""
    return (f"Note: the damaged {path.name} was set aside as "
            f"{Path(kept).name} — nothing was deleted.")


def _quarantine_file(path: Path) -> Path | None:
    """Move a corrupt store file aside, keeping its exact bytes.

    The name is `<name>.corrupt-<YYYYMMDD-HHMMSS>` next to the
    file (counter suffix on a same-second repeat), the C3-F2
    shape. The corruption record is cleared — the file on disk
    is gone, so the store reads as a healthy empty one
    afterwards — and the quarantine path is left for the saving
    function to report (see take_quarantine_notice). None (never
    raising) when the rename itself fails; the caller must then
    refuse the write."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    n = 2
    while target.exists():
        target = path.with_name(f"{path.name}.corrupt-{stamp}-{n}")
        n += 1
    try:
        os.replace(path, target)
    except OSError as exc:
        log.warning("could not set aside damaged planner store %s "
                    "(%s); refusing to overwrite it", path, exc)
        return None
    _CORRUPT.pop(str(path), None)
    _CORRUPT_LOGGED.discard(str(path))
    _LAST_QUARANTINE[str(path)] = str(target)
    log.warning("damaged planner store %s set aside as %s",
                path, target)
    return target


def _valid_plan(entry) -> bool:
    """A stored plan is usable iff it is a dict whose steps (when
    present) are a list of dicts. Anything else — a bare string, a
    number, steps saved as a string — is a damaged record: the kind
    a hand-edit typo or a truncated write produces."""
    if not isinstance(entry, dict):
        return False
    steps = entry.get("steps", [])
    if not (isinstance(steps, list) and all(isinstance(s, dict) for s in steps)):
        return False
    # v5.49 (programme item 32): when/on_fail conditionals ride on
    # steps — a record whose conditional fields are malformed is
    # damaged, same rule as a malformed steps list.
    return all(_conditional_fields_valid(s, len(steps)) for s in steps)


def _valid_routine(entry) -> bool:
    """A stored routine is usable iff it is a dict whose actions
    (when present) are a list of dicts, each with dict (or absent)
    args. A routine with list args would otherwise raise inside
    run_routine at its dict(args) call; one with string actions
    would make list_routines report the string's length as a
    phantom action count."""
    if not isinstance(entry, dict):
        return False
    actions = entry.get("actions", [])
    if not isinstance(actions, list):
        return False
    for action in actions:
        if not isinstance(action, dict):
            return False
        if not isinstance(action.get("args", {}), dict):
            return False
        # v5.49 (programme item 32): when/on_fail conditionals —
        # malformed conditional fields make the record damaged.
        if not _conditional_fields_valid(action, len(actions)):
            return False
    return True


def _load_records(path: Path, validator) -> tuple[list, int]:
    """Load a plans/routines file as (valid_records, damaged_count).

    Entry-level hardening: _load_list tolerates a corrupt FILE, and
    this tolerates corrupt ENTRIES — damaged records are skipped
    and counted (callers disclose the count), never raised on and
    never allowed to take list/get/create down with them. Skipped
    records drop out of the file on the next successful save. A
    corrupt FILE reads as zero records with damaged 0 — the file
    itself is disclosed separately (corrupt_note / store_corruption)
    and a save quarantines it first (see _save_list)."""
    raw = _load_list(path)
    good = [e for e in raw if validator(e)]
    return good, len(raw) - len(good)


def _short(text: str, limit: int = 140) -> str:
    """Collapse a tool result to one short line for summaries."""
    one_line = " ".join(str(text).split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1] + "…"


def _json_args(args):
    """Plan-step args may arrive as JSON text (the Gemini-safe schema
    declares free-form arg objects as strings — v5.27); returns the
    parsed dict, or None when the text is not a JSON object."""
    if isinstance(args, str) and args.strip().startswith("{"):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            return None
    return None


def _normalise_on_fail(raw) -> tuple[object, str | None]:
    """Normalise an `on_fail` value (v5.49, programme item 32).
    Returns (value, error): value is None (absent), "skip", "halt"
    or {"goto": int}; anything else is an error string."""
    if raw is None:
        return None, None
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in ("skip", "halt"):
            return value, None
        return None, (f"invalid 'on_fail' {raw!r} — use \"skip\", \"halt\" "
                      "or {\"goto\": <step number>}")
    if isinstance(raw, dict):
        if set(raw.keys()) == {"goto"}:
            goto = raw.get("goto")
            if isinstance(goto, bool) or not isinstance(goto, int) or goto < 1:
                return None, (f"invalid 'on_fail' {raw!r} — \"goto\" must be "
                              "a positive step number")
            return {"goto": goto}, None
        return None, (f"invalid 'on_fail' {raw!r} — the only object form "
                      "is {\"goto\": <step number>}")
    return None, (f"invalid 'on_fail' {raw!r} — use \"skip\", \"halt\" "
                  "or {\"goto\": <step number>}")


def _normalise_when(raw) -> tuple[dict | None, str | None]:
    """Normalise a `when` probe (v5.49, programme item 32).
    Returns (when_dict_or_None, error)."""
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, ("invalid 'when' — it must be an object like "
                      "{\"tool\": \"...\", \"args\": {...}, "
                      "\"expect_contains\": \"...\"}")
    tool = raw.get("tool")
    if not tool or not isinstance(tool, str):
        return None, "invalid 'when' — it needs a 'tool' name"
    args = raw.get("args", {})
    if args is None:
        args = {}
    if isinstance(args, str):
        args = _json_args(args)
    if not isinstance(args, dict):
        return None, f"invalid 'when' for '{tool}' — 'args' must be an object"
    when: dict = {"tool": tool.strip(), "args": args}
    contains = raw.get("expect_contains") or raw.get("contains")
    if isinstance(contains, str) and contains:
        when["expect_contains"] = contains
    status = raw.get("expect_status") or raw.get("status")
    if isinstance(status, str) and status:
        when["expect_status"] = status.strip().lower()
    return when, None


def _conditional_fields_valid(item: dict, total: int | None = None) -> bool:
    """Load-boundary validation for when/on_fail (v5.49, item 32):
    present-but-malformed conditional fields (including a goto
    past the end of the step list) mark the record damaged."""
    if item.get("when") is not None:
        _value, error = _normalise_when(item.get("when"))
        if error:
            return False
    if item.get("on_fail") is not None:
        value, error = _normalise_on_fail(item.get("on_fail"))
        if error:
            return False
        if isinstance(value, dict) and total is not None and value["goto"] > total:
            return False
    return True


# INTENTIONAL (skills split B41): _load_plans/_load_routines moved here
# from the facade (they belong with _load_records/_valid_plan). They
# resolve _PLANS_PATH/_ROUTINES_PATH through the facade module object at
# call time (B23 live-read pattern) so direct-assignment path patches on
# the facade keep landing — a snapshot import would go stale.
def _load_plans() -> tuple[list, int]:
    from . import planner as _facade  # local: facade fully loaded by call time

    return _load_records(_facade._PLANS_PATH, _valid_plan)


def _load_routines() -> tuple[list, int]:
    from . import planner as _facade  # local: facade fully loaded by call time

    return _load_records(_facade._ROUTINES_PATH, _valid_routine)

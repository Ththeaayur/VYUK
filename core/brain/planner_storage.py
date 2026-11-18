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
import os
import tempfile
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"  # Phase 3 B2-remainder: one level deeper in core/brain/
_PLANS_PATH = _DATA_DIR / "plans.json"
_ROUTINES_PATH = _DATA_DIR / "routines.json"

MAX_ROUTINE_ACTIONS = 20
MAX_PLAN_STEPS = 30


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _load_list(path: Path) -> list:
    """Read a JSON list from disk. Missing/corrupt/wrong-type -> []."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 — corrupt state must never crash a tool
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
    records drop out of the file on the next successful save."""
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

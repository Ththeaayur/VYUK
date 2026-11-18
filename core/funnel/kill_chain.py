"""Graduated kill-chain (v5.56, Wave 2, SE-6.9).

Five rungs over the EXISTING flag plumbing — there is no
separate "flag module" in core/ (plan item DS-2 is the python-tuf
update-feed study, not a flag system; the grep for a flag module
finds only core/failsafe.py). The plumbing reused here is
failsafe's: a persistent state file under the app's ``data/``
dir, a tripped/stopped predicate that funnel code consults
lazily, and manual user re-arm — no second trip mechanism is
invented. Rung 5 (full stop) is enforced in the funnel hook
alongside, never through, the panic stop: this module never
imports failsafe's trip internals, never calls a trip, and
never modifies failsafe.py. The panic stop stays R5's floor,
unchanged and independent; ``is_stopped()`` additionally
consults the failsafe (display-only: unavailable reads as not
tripped for status rendering; enforcement uses the tri-state
consult) so a panic trip and a kill-chain full stop agree that
nothing runs.
(A2: the funnel's consults are tri-state now — an unconsultable
failsafe or kill-chain gate is UNAVAILABLE and refuses
non-read/egress calls, never "not tripped" / "no rung". The
display helpers below still read unavailable as not-tripped,
for status rendering only; they never authorize.)

RUNGS (escalate only upward; every rung user-reversible)
--------------------------------------------------------
    0  none         normal operation
    1  freeze-tools "Tools are paused — VYUK can still look and
                    talk, but cannot change anything on your
                    computer until you unfreeze tools."
    2  freeze-egress "Network is paused — nothing leaves this
                    computer until you allow network again."
    3  freeze-brain "The cloud brain is paused — only on-device
                    brains may answer until you resume it."
    4  safe mode    "Safe mode — only looking things up and
                    talking work. All changes, all network and
                    the cloud brain are paused."
    5  full stop    "Full stop — VYUK is stopped completely
                    until you start it again by hand."

Rungs are cumulative: rung N enforces every lower rung's gate.
Every transition is logged into the hash-chained audit log
(kind ``"kill"``) via a lazy, guarded record_family append, and
carries its plain-language reason string, so the chain — not
just this module's state file — is the record of who stopped
what and why.

REVERSIBILITY: lower(rung) / clear() are user actions (the
PR-3 summon-bar surface that will expose them ships in Wave 9;
this module is the mechanism, not that surface). Clearing never
requires a vendor, a server or an update — and see the refusal
below. State persists in ``data/kill_chain.json`` so a restart
does not silently drop a freeze the user set.

DESIGNED-IN REFUSAL (doctrine, recorded here as code comment
and in the funnel-hook spec): no vendor remote-kill ever ships.
There is no remote endpoint, no vendor key and no flag in this
module that anyone but the local user (or code running as the
user on this machine, which is the threat the rungs exist to
contain) can set. The signed update channel must never double
as a kill switch: core/health/updater.py is deliberately NOT consulted
here, and no update-feed field is read as a rung.

Stdlib only. No new dependency.
"""

import json
import os
from .guard_state import FailsafeState
import threading
import time
from pathlib import Path

STATE_FILENAME = "kill_chain.json"

# Rung numbers, in escalation order.
RUNG_NONE = 0
RUNG_FREEZE_TOOLS = 1
RUNG_FREEZE_EGRESS = 2
RUNG_FREEZE_BRAIN = 3
RUNG_SAFE_MODE = 4
RUNG_FULL_STOP = 5

RUNG_NAMES = {
    0: "none",
    1: "freeze-tools",
    2: "freeze-egress",
    3: "freeze-brain",
    4: "safe mode",
    5: "full stop",
}

# Plain-language explanation per rung — what the user is told,
# verbatim in substance, whenever a rung is set or blocks a call.
RUNG_REASONS = {
    0: "Normal operation — nothing is frozen.",
    1: ("Tools are paused — VYUK can still look and talk, but "
        "cannot change anything on your computer until you "
        "unfreeze tools."),
    2: ("Network is paused — nothing leaves this computer "
        "until you allow network again."),
    3: ("The cloud brain is paused — only on-device brains may "
        "answer until you resume it."),
    4: ("Safe mode — only looking things up and talking work. "
        "All changes, all network and the cloud brain are "
        "paused."),
    5: ("Full stop — VYUK is stopped completely until you "
        "start it again by hand."),
}

_lock = threading.Lock()
_dir_override: Path | None = None

# GAPS-24 (K-V6): the state-file cache. gate_tool() runs on every
# tool call, and the old code read + parsed kill_chain.json from
# disk on each one. The cache is keyed on the file's identity —
# (path, device, inode, mtime_ns, size) from one stat() per
# consult — so ANY writer (this module's tmp+replace, the GUI,
# a test writing in place) invalidates it: a replaced file has
# a new inode, an in-place rewrite has a new mtime/size. A stat
# is one cheap syscall; the read+parse happens only when the key
# moved. The cache NEVER weakens the chain: a corrupt file is
# cached as an ERROR (strict readers still raise), and a missing
# file is cached only under the "missing" key, so creating the
# file later is picked up on the next stat. Writers invalidate
# explicitly (see _write_state / configure /
# reset_configuration); tests that write the file in place can
# also call reset_cache_for_tests().
_cache: dict = {"key": None, "state": None, "strict_error": None}


def _cache_invalidate_locked() -> None:
    """Drop the cached state. Callers hold _lock."""
    _cache["key"] = None
    _cache["state"] = None
    _cache["strict_error"] = None


def reset_cache_for_tests() -> None:
    """Drop the cached state (tests that write the file in place
    without going through _write_state)."""
    with _lock:
        _cache_invalidate_locked()


def _cache_key_locked():
    """The cache key for the state file, from one stat().

    (path, dev, ino, mtime_ns, size); ("missing", path) when the
    file does not exist; None when the file cannot be statted
    at all (never serve from cache then — always re-read)."""
    path = state_path()
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return ("missing", str(path))
    except OSError:
        return None
    return (str(path), st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def _cached_state_locked():
    """(lenient state dict, strict error or None). Callers hold
    _lock. The lenient state is rung 0 for a missing OR corrupt
    file (the old _read_state behaviour); the strict error is
    set for a corrupt/unreadable file so _strict_rung can still
    raise instead of silently reading as rung 0."""
    key = _cache_key_locked()
    if key is not None and key == _cache["key"]:
        return dict(_cache["state"]), _cache["strict_error"]
    state = {"rung": 0, "reason": "", "set_at": ""}
    err = None
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("rung"), int):
            state = {"rung": max(0, min(5, raw["rung"])),
                     "reason": str(raw.get("reason") or ""),
                     "set_at": str(raw.get("set_at") or "")}
        else:
            err = "kill-chain state malformed: rung is not an int"
    except FileNotFoundError:
        pass  # missing file reads as rung 0 (fresh install)
    except Exception as exc:  # noqa: BLE001 — corrupt state
        err = (f"kill-chain state unreadable: "
               f"{type(exc).__name__}")
    if key is not None:
        _cache["key"] = key
        _cache["state"] = dict(state)
        _cache["strict_error"] = err
    return dict(state), err


def _default_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data"  # Phase 3 F2b: file now in core/funnel/ — one more level up to the package root


def state_dir() -> Path:
    return _dir_override if _dir_override is not None else _default_dir()


def state_path() -> Path:
    return state_dir() / STATE_FILENAME


def configure(data_dir=None) -> None:
    """Redirect the state file (suites). None keeps the setting."""
    global _dir_override
    with _lock:
        if data_dir is not None:
            _dir_override = Path(data_dir)
        _cache_invalidate_locked()


def reset_configuration() -> None:
    global _dir_override
    with _lock:
        _dir_override = None
        _cache_invalidate_locked()


def _read_state() -> dict:
    # GAPS-24 (K-V6): the cached reader — one stat per consult,
    # one read only when the file's identity moved.
    with _lock:
        return _cached_state_locked()[0]


def _write_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    # GAPS-24 (K-V6): the file was replaced (new inode) — drop
    # the cache so the next consult re-reads. Called under _lock.
    _cache_invalidate_locked()


def _log_transition(old: int, new: int, reason: str, action: str) -> None:
    """Log a rung transition into the hash-chained audit log.

    Lazy + guarded: a broken record store must never break a
    kill-chain transition, and this module must not import
    record_family at top level (record_family is the facade;
    the facade never imports kill_chain, so the lazy import
    here cannot cycle in practice — but laziness also keeps
    this module importable with stdlib alone).
    """
    try:
        from .. import record_family as _rf  # Phase 3 F2b: file now in core/funnel/; flat sibling climbs one more level

        _rf.append_event("kill", {
            "action": action, "from_rung": old, "to_rung": new,
            "from_name": RUNG_NAMES.get(old, "?"),
            "to_name": RUNG_NAMES.get(new, "?"),
            "reason": reason,
        })
    except Exception:  # noqa: BLE001 — logging never breaks a rung change
        pass


def current_rung() -> int:
    """The currently set rung (0 when nothing is frozen)."""
    with _lock:
        # GAPS-24 (K-V6): the locked reader — _read_state()
        # would re-take _lock and deadlock here.
        return _cached_state_locked()[0]["rung"]


def status() -> dict:
    """{rung, name, reason, set_at, stopped, tools_frozen,
    egress_frozen, brain_frozen, safe_mode} — the whole rung
    state in one dict for surfaces (Wave 9) to render."""
    st = _read_state()
    rung = st["rung"]
    return {
        "rung": rung,
        "name": RUNG_NAMES[rung],
        "reason": st["reason"] or RUNG_REASONS[rung],
        "set_at": st["set_at"],
        "stopped": is_stopped(),
        "tools_frozen": tools_frozen(),
        "egress_frozen": egress_frozen(),
        "brain_frozen": brain_frozen(),
        "safe_mode": rung >= RUNG_SAFE_MODE,
    }


def reason_for(rung: int) -> str:
    """The plain-language explanation for a rung."""
    return RUNG_REASONS.get(rung, "Unknown rung.")


def escalate(rung: int, reason: str = "") -> dict:
    """Raise the kill-chain to ``rung`` (1–5). Escalation only:
    asking for a rung at or below the current rung leaves the
    state unchanged and reports ``changed: False``. Returns the
    new status(). ``reason`` overrides the rung's default
    explanation when given; the stored reason is always a
    human sentence, never a code.
    """
    if rung < 1 or rung > 5:
        raise ValueError(f"rung must be 1–5, got {rung!r}")
    with _lock:
        st = _cached_state_locked()[0]
        old = st["rung"]
        if rung <= old:
            return {**status_locked(st), "changed": False}
        new_reason = reason or RUNG_REASONS[rung]
        new = {"rung": rung, "reason": new_reason,
               "set_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                       time.gmtime())}
        _write_state(new)
    _log_transition(old, rung, new_reason, "escalate")
    return {**status(), "changed": True}


def lower(rung: int, reason: str = "") -> dict:
    """Lower the kill-chain to ``rung`` (0–5) — the user
    reversal path. Lowering to 0 clears the chain. Returns the
    new status() plus ``changed``. A rung above the current one
    is not an escalation here; use escalate() for that."""
    if rung < 0 or rung > 5:
        raise ValueError(f"rung must be 0–5, got {rung!r}")
    with _lock:
        st = _cached_state_locked()[0]
        old = st["rung"]
        if rung >= old:
            return {**status_locked(st), "changed": False}
        new_reason = reason or (RUNG_REASONS[rung] if rung else
                                "Kill-chain cleared by the user — "
                                "normal operation resumes.")
        new = {"rung": rung, "reason": new_reason,
               "set_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                       time.gmtime())}
        _write_state(new)
    _log_transition(old, rung, new_reason, "lower")
    return {**status(), "changed": True}


def clear(reason: str = "") -> dict:
    """Clear the kill-chain entirely (rung 0). User reversal."""
    return lower(0, reason or "Kill-chain cleared by the user — "
                 "normal operation resumes.")


def status_locked(st: dict) -> dict:
    """status() for a state dict already read under the lock
    (avoids re-reading the file mid-transition)."""
    rung = st["rung"]
    return {
        "rung": rung,
        "name": RUNG_NAMES[rung],
        "reason": st["reason"] or RUNG_REASONS[rung],
        "set_at": st["set_at"],
        "stopped": rung >= RUNG_FULL_STOP or _failsafe_tripped(),
        "tools_frozen": rung >= RUNG_FREEZE_TOOLS,
        "egress_frozen": rung >= RUNG_FREEZE_EGRESS,
        "brain_frozen": rung >= RUNG_FREEZE_BRAIN,
        "safe_mode": rung >= RUNG_SAFE_MODE,
    }


def _failsafe_state():
    """Tri-state failsafe consult (audit fix A2, P1-1):
    (FailsafeState, reason). CLEAR / TRIPPED as tripped()
    reports; UNAVAILABLE when the consult itself fails.
    Enforcement uses this; _failsafe_tripped() below is
    display-only."""
    try:
        from .. import failsafe as _fs  # Phase 3 F2b: file now in core/funnel/; flat sibling climbs one more level

        if bool(_fs.tripped()):
            try:
                reason = str(_fs.trip_reason() or "")
            except Exception:
                reason = ""
            return FailsafeState.TRIPPED, reason
        return FailsafeState.CLEAR, ""
    except Exception as exc:  # noqa: BLE001 — consult failure
        return FailsafeState.UNAVAILABLE, type(exc).__name__


def _failsafe_tripped() -> bool:
    """DISPLAY ONLY — True when the failsafe is tripped. An
    unconsultable failsafe reads as NOT tripped HERE, for
    status displays (status_locked, is_stopped) that must
    never break rendering. This function NEVER authorizes:
    enforcement consults _failsafe_state() and refuses
    non-read tools on UNAVAILABLE. (Pre-A2 this helper was
    the funnel's consult too, and its fail-open shape was
    the P1-1 defect; the funnel now uses the tri-state
    consult in core/funnel/tools_registry.)"""
    return _failsafe_state()[0] is FailsafeState.TRIPPED


# --- funnel enforcement predicates (coordinator wires these
# --- into core/funnel/tools_registry.py's execute() — see the funnel-
# --- hook spec in core/record_family.py's module docstring) ---

def tools_frozen() -> bool:
    """True when non-read tools must be refused (rung >= 1)."""
    return current_rung() >= RUNG_FREEZE_TOOLS


def egress_frozen() -> bool:
    """True when egress tools must be refused (rung >= 2)."""
    return current_rung() >= RUNG_FREEZE_EGRESS


def brain_frozen() -> bool:
    """True when cloud-brain calls must be refused (rung >= 3)."""
    return current_rung() >= RUNG_FREEZE_BRAIN


def is_stopped() -> bool:
    """True at rung 5 (full stop) OR while the panic-stop
    failsafe is tripped — the two stops agree, neither drives
    the other."""
    return current_rung() >= RUNG_FULL_STOP or _failsafe_tripped()


class _KillChainUnavailable(Exception):
    """Raised when the kill-chain rung state cannot be verified
    (A2, P1-1): a present-but-corrupt state file must never
    silently read as rung 0, which would clear every stop.
    A missing file still reads as 0 (nothing frozen yet —
    the legitimate fresh-install default). The funnel treats
    this as UNAVAILABLE and refuses non-read/egress calls."""


def _strict_rung() -> int:
    """Rung from the state file, strictly (see above).

    GAPS-24 (K-V6): reads through the same identity-keyed cache
    as the lenient reader — a corrupt file raises here exactly
    as before (the cache stores the error, it never downgrades
    it to rung 0), and a file fixed externally is picked up on
    the next stat."""
    with _lock:
        state, err = _cached_state_locked()
    if err is not None:
        raise _KillChainUnavailable(err)
    return state["rung"]


def gate_tool(name: str, is_read_class: bool,
              is_egress_class: bool) -> str | None:
    """The funnel's kill-chain verdict for one tool call.

    Returns None when the call may proceed, else a refusal
    message in plain language (the funnel returns it as kind
    "refused" without running the handler). Order matters:
    full stop outranks everything (even reads stop at rung 5,
    unlike the panic stop, under which reads still run);
    then tools, then egress. Brain gating is not a tool-call
    verdict — see brain_frozen(), wired at the brain dispatch
    (core/voice/live_client.py / local-brain selection), not here.

    v5.56 integration fix: the full-stop branch tests the RUNG
    only, never is_stopped()'s failsafe leg. The funnel already
    refused non-read tools at its own failsafe stage (which
    stays first); if this branch also fired on a tripped
    failsafe, it would refuse READ tools the panic floor
    deliberately lets through (R5: reads run while tripped so
    a human triaging keeps her eyes) — the v5.55 funnel suite
    caught exactly that. is_stopped() keeps its combined
    meaning for STATUS displays; the funnel verdict's ground
    is the rung alone, so the two stops refuse independently.
    """
    # A2 (P1-1): the rung comes from the STRICT reader, so a
    # corrupt state file raises _KillChainUnavailable (the
    # funnel treats it as UNAVAILABLE) instead of silently
    # reading as rung 0 and clearing every stop. Verdict
    # strings are byte-identical to the pre-A2 wording for
    # valid states.
    rung = _strict_rung()
    if rung >= RUNG_FULL_STOP:
        return ("VYUK is fully stopped (kill-chain rung 5) — no "
                "tool runs until a human lowers or clears the "
                "stop. Nothing was run.")
    if rung >= RUNG_FREEZE_TOOLS and not is_read_class:
        return ("VYUK's tools are frozen (kill-chain rung "
                f"{rung}: {RUNG_NAMES[rung]}) — "
                "this change was not run. Unfreeze tools to "
                "continue.")
    if rung >= RUNG_FREEZE_EGRESS and is_egress_class:
        return ("VYUK's network is frozen (kill-chain rung "
                f"{rung}) — this network call was not "
                "made. Allow network again to continue.")
    return None

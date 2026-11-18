"""One record family — facade (v5.56, Wave 2: SE-6.10, SE-6.9,
PF-2, PF-15, PF-23).

NOT four silos: the hash-chained audit log (SE-6.10), the human
activity feed (PF-2), the egress ledger (PF-23) and the kill
chain's transition log (SE-6.9) share ONE store, ONE chain and
ONE rotation policy (PF-15) in core/audit_chain.py. Delete /
retention / chain-integrity therefore cannot disagree between
them — there is only one record to rotate, and rotation writes
a checkpoint entry chaining the archived segment (see
audit_chain.rotate()), so verify_chain() covers checkpoints.

WHAT EACH PIECE IS
------------------
* Audit (safety-side): append_event("audit", {...}) — the
  machine record: funnel audit events, NO-33 health-rule output
  and kill transitions. Verifiable by hash; not written for
  charm.
* Activity (human-form): log_activity_human(text) — the SAME
  record in human form, beside the audit, not instead of it.
  read_activity() is what a user-facing feed renders.
* Egress ledger: log_egress(when, where, why) — every network
  call, in plain language. This is the FL-5-1 rig's proof made
  human-readable: the rig proves telemetry-off in testing,
  this ledger is that proof as a feature. Entries are record-
  family events (same store, same rotation, same chain).
* Undo (PF-2): register_undo() / execute_undo() for REVERSIBLE
  actions only. The undoable set is defined conservatively
  below; undo execution goes through the tools funnel (see the
  funnel-hook spec) — this module never runs a handler itself.

UNDOABLE SET (conservative, enforced in code)
--------------------------------------------
An action may register an undo ONLY if its effect is fully
reversible by one inverse step AND its kind is not in
UNDO_FORBIDDEN. Forbidden kinds (never undoable, never
registered — register_undo raises ValueError): anything that
sends, deletes, purchases, installs, shuts down or otherwise
leaves the machine or spends money — "delete", "send",
"purchase", "uninstall", "install", "power_control",
"empty_recycle_bin", "run_command", "email", "payment".
Allowed examples: a settings toggle (inverse = set it back),
a mute/unmute, a renamed-back label, a created scratch file
removed via the trash. The kind is the action_id prefix before
the first ":" (e.g. "setting:voice_volume"); an action_id
with no recognised kind is treated as NOT undoable unless the
caller passes kind= explicitly in the allowed set. Undo is
ONE-SHOT: executing it removes the registration, and the
execution itself is appended to the audit chain.

Undo registrations are PROCESS-LOCAL (a callable cannot be
serialised into the chain): a restart drops pending undos,
and that is deliberate — a stale inverse resurrected after a
restart is how an undo does the wrong thing. The audit chain
still records that the undo was REGISTERED, so the intent is
never lost even when the callable is gone.

DATA LOCATION: the app's ``data/`` dir (audit_chain.store_dir),
local only. NOTHING in this module egresses, ever.

FUNNEL-HOOK SPEC — for the coordinator to wire (this module
does NOT edit core/funnel/tools_registry.py; the insertion points are
in ToolRegistry.execute(), next to the existing v5.49 audit
hook and the v5.46 failsafe check):
----------------------------------------------------------------
(a) Funnel audit events into the chain. Inside execute()'s
    _audited() closure, beside the existing
    audit.write_record(...) call, add NOTHING more: the audit
    record IS the chain entry (kind "audit") — GAPS-27 (M-V2)
    removed the funnel's old second write, a subset
    _rf.append_event("audit", {...}) beside write_record, which
    wrote the same call twice with two different payloads.
    Callers that already have NO-33 health-rule output append
    it the same way with kind "audit" and
    data {"source": "health_rule", ...}. Funnel args MUST pass
    through audit.redact_args first — record_family's own
    shallow redaction (below) is a backstop, not the funnel's
    redaction path.

(b) Kill-chain enforcement checks. Immediately AFTER the v5.46
    failsafe-tripped refusal and BEFORE the protected-path
    check, add (guarded, fail-closed since A2: a gate_tool
    exception is UNAVAILABLE and refuses non-read/egress
    calls, exactly like the failsafe consult)::

        from .funnel import kill_chain as _kc
        _verdict = _kc.gate_tool(
            name,
            is_read_class=_is_read_class(name, args),
            is_egress_class=_is_egress_read(name))
        if _verdict is not None:
            _audit_gate["value"] = "refused:kill-chain"
            return _audited({"status": "error", "kind": "refused",
                             "message": _verdict})

    Rung-to-stage mapping: rung 1 gates non-read handlers;
    rung 2 gates egress-class calls (_is_egress_read is the
    egress test — a read that can put bytes on the wire;
    the Egress batch corrected the old negated call, which
    had refused local reads as "network" while letting
    network reads through); rung 3 gates cloud-brain dispatch, NOT the
    tool funnel (wire kill_chain.brain_frozen() at the brain
    selection/dispatch point, refusing cloud brains while
    on-device brains still answer); rung 4 is the union of
    1–3 for the funnel; rung 5 refuses ALL tools including
    reads (unlike the panic stop, under which reads run).
    The panic-stop check stays FIRST and untouched: a tripped
    failsafe and a rung-5 stop both refuse, independently.

(c) Undo execution through the funnel. execute_undo() below
    is the ONLY undo entry point surfaces should call. It
    resolves the registered inverse to a (tool_name, args)
    pair when the inverse was registered as one (see
    register_undo's funnel form), and runs it through
    tools.execute(name, args) — never by calling a handler
    directly — so the inverse inherits validation, the
    confirmation gate, the failsafe check, the kill-chain
    check in (b) and the audit hook in (a). Inverses
    registered as plain callables run directly (they are
    trusted local code, not brain-chosen tool calls) and are
    still audit-logged before and after.

Stdlib only. No new dependency.
"""

import threading
from . import audit_chain as _chain

# Kinds that may NEVER carry an undo (see module docstring).
UNDO_FORBIDDEN = frozenset({
    "delete", "send", "purchase", "uninstall", "install",
    "power_control", "empty_recycle_bin", "run_command",
    "email", "payment", "close_application", "process_control",
})

# Kinds that may carry an undo when registered explicitly.
UNDO_ALLOWED = frozenset({
    "setting", "mute", "volume", "label", "rename", "scratch",
    "toggle", "preference",
})

_SENSITIVE_SUBSTRINGS = ("password", "passwd", "token", "secret",
                         "api_key", "apikey", "auth", "credential",
                         "private_key")
_MASK = "•••"

_lock = threading.Lock()
_undo_registry: dict[str, dict] = {}


def _redact(data: dict) -> dict:
    """Shallow backstop redaction: mask values under sensitive-
    looking keys. The funnel MUST redact with audit.redact_args
    first (see the hook spec); this only catches direct
    append_event callers that forgot."""
    out = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if any(s in lowered for s in _SENSITIVE_SUBSTRINGS):
            out[key] = _MASK
        elif isinstance(value, dict):
            out[key] = _redact(value)
        else:
            out[key] = value
    return out


def configure(data_dir=None, max_bytes=None, max_age_days=None) -> None:
    """Redirect the shared store / rotation bounds (suites)."""
    _chain.configure(data_dir=data_dir, max_bytes=max_bytes,
                     max_age_days=max_age_days)


def reset_configuration() -> None:
    """Restore the shared store defaults."""
    _chain.reset_configuration()


# --- the contract API (Stream F surfaces code against these) ---

def append_event(kind: str, data: dict) -> str:
    """Append one record-family event to the hash chain.
    Returns the event id ("" if the write failed)."""
    if not isinstance(data, dict):
        return ""
    return _chain.append(kind, _redact(data))


def read_events(kind: str, limit: int = 50) -> list:
    """Read events of one kind, newest-first."""
    return _chain.read(kind=kind, limit=limit)


def verify_chain() -> bool:
    """True when the chain (live + present archives) is intact."""
    return _chain.verify_chain()


def verify_chain_detail() -> dict:
    """verify_chain() plus {checked, first_break, warnings} —
    see audit_chain.verify_chain_detail()."""
    return _chain.verify_chain_detail()


def rotate() -> str:
    """Rotate the shared store if a bound is crossed (PF-15);
    returns the checkpoint hash, or the current last hash when
    rotation was not needed, or "" when there was nothing to
    rotate. See audit_chain.rotate()."""
    return _chain.rotate()


def rotate_now() -> str:
    """Forced rotation (same checkpoint semantics)."""
    return _chain.rotate_now()


def rotation_needed() -> bool:
    """True when the shared store crosses its size/age bound."""
    return _chain.rotation_needed()


def export_chain(dest_path) -> dict:
    """User-held anchor export {entries, sha256, path}."""
    return _chain.export_chain(dest_path)


def log_egress(when: str, where: str, why: str) -> str:
    """Ledger one network call, in plain language (PF-23).
    ``when`` is an ISO timestamp string, ``where`` the
    destination in human terms (host/service, never a secret),
    ``why`` the plain-language reason the call was made.
    Returns the event id."""
    return append_event("egress", {"when": when, "where": where,
                                   "why": why})


def read_ledger(limit: int = 50) -> list:
    """Read egress-ledger entries, newest-first. Each entry's
    data carries {when, where, why} in plain language."""
    return read_events("egress", limit=limit)


def log_activity_human(text: str, reversible=None,
                       undo_action_id=None) -> str:
    """Append one human-form activity line (PF-2). Returns the
    event id. This is the same record as the audit, in human
    form — not a second store. ``reversible`` marks the PF-2
    criterion's irreversible line: False means the producer
    knows this action cannot be undone (the funnel's tool-call
    lines carry False — a tool call already executed is not a
    PF-2 undo object, and the surface marks it irreversible so
    no fake undo is ever implied); True + ``undo_action_id``
    marks a registered undoable action; None (default) means
    reversibility is unknown and the surface marks nothing."""
    data: dict = {"text": text}
    if reversible is not None:
        data["reversible"] = bool(reversible)
    if undo_action_id:
        data["undo_action_id"] = str(undo_action_id)
    return append_event("activity", data)


def read_activity(limit: int = 50) -> list:
    """Read human-form activity entries, newest-first."""
    return read_events("activity", limit=limit)


def register_undo(action_id: str, undo_callable, kind: str = "") -> None:
    """Register a one-shot undo for a REVERSIBLE action.

    ``undo_callable`` is either a plain callable (run directly
    by execute_undo) or a funnel-form tuple
    ``("tool", tool_name, args_dict)`` (run through
    tools.execute — see the hook spec (c)). The action's kind
    is the action_id prefix before ":" unless ``kind`` is
    passed explicitly. Raises ValueError for forbidden kinds
    and for kinds outside UNDO_ALLOWED (unknown kinds are
    refused: an undo must be affirmatively reversible, not
    optimistically assumed so). The registration is also
    appended to the audit chain (kind "undo", action
    "registered") — the callable itself is process-local and
    is never serialised.
    """
    action_kind = kind or (action_id.split(":", 1)[0] if ":" in action_id
                           else "")
    if action_kind in UNDO_FORBIDDEN:
        raise ValueError(
            f"action '{action_id}' is irreversible and can never "
            f"carry an undo (kind '{action_kind}')")
    if action_kind not in UNDO_ALLOWED:
        raise ValueError(
            f"action '{action_id}' has no recognised reversible "
            f"kind ('{action_kind}'); undo registration refused — "
            f"declare an allowed kind explicitly or not at all")
    with _lock:
        _undo_registry[action_id] = {"callable": undo_callable,
                                     "kind": action_kind}
    append_event("undo", {"action": "registered",
                          "action_id": action_id, "kind": action_kind})


def execute_undo(action_id: str) -> dict:
    """Execute a registered undo once, then drop it. Returns
    {ok, action_id, ...}. Funnel-form inverses run through
    tools.execute(); plain callables run directly. Both paths
    are audit-logged before and after; a missing registration
    returns ok=False (a restart drops pending undos by
    design — see the module docstring) rather than raising.
    """
    with _lock:
        entry = _undo_registry.pop(action_id, None)
    if entry is None:
        append_event("undo", {"action": "missing",
                              "action_id": action_id})
        return {"ok": False, "action_id": action_id,
                "message": "No undo registered for this action "
                           "(it may already have been used, or "
                           "the app restarted since it was set)."}
    append_event("undo", {"action": "executing",
                          "action_id": action_id})
    try:
        target = entry["callable"]
        if isinstance(target, tuple) and len(target) == 3 \
                and target[0] == "tool":
            from .funnel import tools_registry as _tr  # Phase 3 F3: moved to core/funnel/

            result = _tr.tools.execute(target[1], target[2] or {})
        else:
            result = target()
    except Exception as exc:  # noqa: BLE001 — undo failure is data
        append_event("undo", {"action": "failed",
                              "action_id": action_id,
                              "error": str(exc)})
        return {"ok": False, "action_id": action_id,
                "message": f"Undo failed: {exc}"}
    append_event("undo", {"action": "executed",
                          "action_id": action_id})
    return {"ok": True, "action_id": action_id, "result": result}


def pending_undos() -> list:
    """Action ids with a live (process-local) undo registered."""
    with _lock:
        return sorted(_undo_registry)

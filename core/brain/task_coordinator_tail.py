"""Split of task_coordinator: tail funcs (default resolvers, typed turns)."""

from __future__ import annotations

# INTENTIONAL (skills split B43): handle_typed_turn formats receipts
# with format_receipt from the steps module.
from .task_coordinator_steps import format_receipt


def default_plan_names() -> list:
    """J9 playbook names from the persistent store, or [] on any failure."""
    try:
        from ..memory.playbook_store import load_playbook
        _warnings, playbook = load_playbook()
        return list(playbook.names())
    except Exception:  # noqa: BLE001 — resolver failure must not break sending
        return []


def default_routine_names() -> list:
    """Saved routine names from planner, or [] on any failure."""
    try:
        from .planner import get_routines  # Phase 3 B2-remainder: planner now in core/brain/; B1a-2 interim closed
        return [str(r.get("name", "")) for r in get_routines()
                if str(r.get("name", "")).strip()]
    except Exception:  # noqa: BLE001 — resolver failure must not break sending
        return []


def default_plan_steps(name: str):
    """Proven playbook steps for a name, or None."""
    try:
        from ..memory.playbook_store import load_playbook
        _warnings, playbook = load_playbook()
        lookup = playbook.reuse(name)
        if not lookup.get("ok"):
            return None
        return list(lookup.get("steps") or [])
    except Exception:  # noqa: BLE001 — resolver failure becomes a task failure
        return None


def default_routine_steps(name: str):
    """Routine actions for a name, or None."""
    try:
        from .planner import get_routines  # Phase 3 B2-remainder: planner now in core/brain/; B1a-2 interim closed
        wanted = str(name).strip().lower()
        for routine in get_routines():
            if str(routine.get("name", "")).strip().lower() == wanted:
                return list(routine.get("actions", []) or [])
        return None
    except Exception:  # noqa: BLE001 — resolver failure becomes a task failure
        return None


def _typed_turn_brain_id(session) -> str | None:
    """Brain id for a typed turn's live brain, or None when unknown.

    Reads the GUI session's engine (gui/window_brain.py
    BrainMixin._brain_engine: gemini/local) through the capability
    contract's engine map. ANY failure — no session, no engine method,
    unknown engine — yields None, never a guessed brain: an unknown
    brain claims no capability (see brain_capabilities). This lookup
    must never break the turn, so it is fully guarded here as well as
    by the GUI hook's own try/except.
    """
    try:
        engine = session._brain_engine()
    except Exception:  # noqa: BLE001 — engine lookup failure is unknown
        return None
    try:
        from .. import brain_capabilities as _bc
        contract = _bc.contract_for_engine(engine)
    except Exception:  # noqa: BLE001 — contract failure is unknown
        return None
    return contract.brain_id if contract is not None else None


def _default_ledger():
    """Production ledger (app data dir), or None when it cannot be built.

    Ledger failure must never break a turn, so any failure here
    yields None and the coordinator runs in-memory-only.
    """
    try:
        from .task_ledger import TaskLedger
        return TaskLedger()
    except Exception:  # noqa: BLE001 — ledger failure is in-memory-only
        return None


def list_interrupted_tasks(session=None, ledger=None) -> list:
    """Interrupted tasks as summaries, for startup recovery.

    The consumer path the GUI (or any session caller) uses on
    startup: interrupted = stored state running or paused, with
    what completed, what remains, and whether the remainder is
    all pure reads (the only case that MAY auto-resume).
    Resolution: explicit ledger > the session coordinator's
    ledger > the default production ledger. ANY failure yields
    [] — a recovery query never breaks startup. No GUI page is
    built here (shell surfaces wait for the owner's own UI
    changes); this callable path + its test are the deliverable.
    """
    resolved = ledger
    if resolved is None and session is not None:
        coord = getattr(session, "_task_coordinator", None)
        resolved = getattr(coord, "_ledger", None) if coord is not None else None
    if resolved is None:
        resolved = _default_ledger()
    if resolved is None:
        return []
    try:
        return resolved.list_interrupted()
    except Exception:  # noqa: BLE001 — recovery query failure is an empty list
        return []


def _companion_second_opinion_brains():
    """(brain_a, brain_b) callables from the companion pair, else None.

    Companion sessions (2026-11-05): the construction-site reader
    for the live session holder's pair. The holder's pair is two
    SESSIONS; the coordinator takes two CALLABLES, so the pair is
    converted here through the EXISTING factory
    (second_opinion_from_sessions) — no factory change. Lazy
    imports (B12 pattern) and guarded — any failure reads as no
    pair and the check stays OFF exactly as before. The callables
    are supplied at coordinator construction; a stashed
    coordinator whose brains are None is refreshed by
    _fill_second_opinion_brains.
    """
    try:
        from . import companion_sessions as _cs
        pair = _cs.companion_pair()
        if pair is None:
            return None
        from ..w8_wiring_opinion import second_opinion_from_sessions
        return second_opinion_from_sessions(pair[0], pair[1])
    except Exception:  # noqa: BLE001 — no pair, check stays OFF
        return None


def _default_playbook():
    """The session's shared playbook store, or None on any failure.

    B17 (learning across tasks): the production coordinator
    writes its lessons into the shared store (the same store
    run_goal's callers already pass in). Lazy import, guarded
    — any failure reads as no playbook, and the completion
    path writes no lesson, exactly as before.
    """
    try:
        from . import goal_client_supply as _supply
        return _supply.shared_playbook()
    except Exception:  # noqa: BLE001 — no playbook, no lesson write
        return None


def _default_coordinator(*, ledger=None, executor=None):
    """The ONE production TurnCoordinator construction site.

    Companion sessions (2026-11-05): default ledger (durable —
    a drop leaves a resumable record) + the companion pair
    when both legs are real (else None — the second-opinion
    check stays OFF exactly as before). Both the typed path
    (handle_typed_turn below) and the voice path
    (planner.run_routine_as_task, Fix 9) build their
    coordinator HERE, so the holder's pair is converted to
    brains in exactly one place. The plumbing-blocker pin
    (tests/test_second_opinion_plumbing_blocker.py) counts
    one supplying TurnCoordinator( call, in this module —
    a second construction site passing
    second_opinion_brains anywhere else in core/brain or
    gui fails it. An explicit ledger keeps its meaning (a
    caller-supplied ledger is adopted; None means the
    default production ledger); an explicit executor is
    for tests only — production callers pass none and get
    the funnel.
    """
    # INTENTIONAL (skills split B43): the composed
    # TurnCoordinator is resolved through the facade module
    # object at call time (B12 pattern) so no impl->facade
    # module-level import is needed and the facade stays
    # importable first.
    from . import task_coordinator as _tc_mod
    return _tc_mod.TurnCoordinator(
        executor=executor,
        ledger=ledger if ledger is not None else _default_ledger(),
        second_opinion_brains=_companion_second_opinion_brains(),
        # B17: the shared playbook — the completion path writes
        # its lessons here (same store run_goal's callers pass).
        playbook=_default_playbook())


def _fill_second_opinion_brains(coord) -> bool:
    """Refresh a stashed coordinator whose brains are None.

    Companion sessions (2026-11-05): closes the first-turn race —
    a coordinator stashed before the local preload finished would
    otherwise leave the check OFF for the whole session. Validates
    the pair shape (tuple, length 2, both callable) before
    assigning, and never overwrites brains the coordinator already
    has (the caller checks that). Returns True when filled.
    """
    try:
        brains = _companion_second_opinion_brains()
        if brains is None:
            return False
        if not isinstance(brains, tuple) or len(brains) != 2:
            return False
        if not callable(brains[0]) or not callable(brains[1]):
            return False
        coord._second_opinion_brains = brains
        return True
    except Exception:  # noqa: BLE001 — refresh failure leaves OFF
        return False


def handle_typed_turn(session, text: str, *, coordinator=None,
                      plan_names=None, routine_names=None,
                      plan_steps_for=None, routine_steps_for=None,
                      ledger=None, plan_hook=None) -> bool:
    """Session-handler-level entry: True when the coordinator handled the turn.

    The GUI calls this from ``_send_typed_message`` after the
    submit ack and before the live send. A coordinator-handled
    turn posts its receipt through the session's system-message
    path and the caller must NOT also send it live. Any other
    turn returns False and the existing path runs unchanged. The
    coordinator is stashed on the session so pause/resume/cancel
    address the same task registry across turns.
    """
    coord = coordinator
    if coord is None:
        coord = getattr(session, "_task_coordinator", None)
        if coord is None:
            # Fix 9: the coordinator comes from the one
            # shared construction site (_default_coordinator
            # above) — default ledger (guarded: ledger
            # failure runs in-memory-only) + the companion
            # pair when both legs are real (else None — the
            # check stays OFF exactly as before, default-on
            # preserved per the owner ruling when the pair
            # exists). An explicitly passed coordinator keeps
            # its own ledger (or none).
            coord = _default_coordinator(ledger=ledger)
            try:
                session._task_coordinator = coord
            except Exception:  # noqa: BLE001 — stash failure is harmless
                pass
        elif getattr(coord, "_second_opinion_brains", None) is None:
            # Companion sessions (2026-11-05): this coordinator was
            # stashed before the pair was ready (the local preload
            # takes seconds). Refresh it once the pair exists —
            # never overwrite brains it already has, and never
            # touch an explicitly passed coordinator.
            _fill_second_opinion_brains(coord)
    if plan_names is None:
        plan_names = default_plan_names()
    if routine_names is None:
        routine_names = default_routine_names()
    if plan_steps_for is None:
        plan_steps_for = default_plan_steps
    if routine_steps_for is None:
        routine_steps_for = default_routine_steps
    result = coord.handle_turn(
        text, plan_names=plan_names, routine_names=routine_names,
        plan_steps_for=plan_steps_for, routine_steps_for=routine_steps_for,
        brain_id=_typed_turn_brain_id(session), plan_hook=plan_hook)
    if not result["handled"]:
        return False
    receipt = result["receipt"]
    post = getattr(session, "_add_system", None)
    if post is not None:
        try:
            if receipt is not None:
                post(format_receipt(receipt))
            elif result["error"]:
                post(f"Couldn't run {result['classification'].kind} "
                     f"{result['classification'].name!r}: {result['error']}")
        except Exception:  # noqa: BLE001 — posting never breaks the turn
            pass
    return True

# w4_wiring_replay.py — split from core/w4_wiring.py (Phase B B12): replay programmes + status pins.
# Move only; the facade in core/w4_wiring.py re-exports every name.

from __future__ import annotations  # keep def-time annotation semantics identical to the old module

import json  # runtime use: replay_programme_json calls json.loads



# ---------------------------------------------------------------------------
# Item 9 — Wave 4 TC-2 replay programmes through the funnel
# ---------------------------------------------------------------------------

def _live_executor() -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """The shipped-path executor: ToolRegistry.execute,
    imported lazily so this module never drags the registry
    in at import time."""
    from .funnel.tools_registry import tools as _tools  # Phase 3 F3: moved to core/funnel/

    return _tools.execute




def replay_programme(
    program: Any,
    executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Replay a Wave 4 programme through the funnel.

    Validation FIRST: validate_program gates before the
    executor is touched, so an invalid programme — empty,
    over the 50-step cap, non-JSON args, or carrying a
    password/captcha argument — is refused and NEVER
    executed (the suite proves the executor is not called
    for invalid programmes). replay() itself re-validates
    and halts at the first non-success step; later steps
    never run. This is core/replay.py (Wave 4 TC-2), NOT
    Wave 8 teach replay (core/brain/teach.py).
    """
    from . import replay as _replay

    error = _replay.validate_program(program)
    if error is not None:
        return {"status": "error", "kind": "validation",
                "message": error, "steps_run": 0, "results": []}
    exec_fn = executor if executor is not None else _live_executor()
    return _replay.replay(program, exec_fn)




def replay_programme_json(
    text: str,
    executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Replay a programme given as JSON text (the
    registration handler's shape). Unparseable text is a
    validation refusal, never an execution."""
    try:
        program = json.loads(text)
    except (TypeError, ValueError) as exc:
        return {"status": "error", "kind": "validation",
                "message": f"Replay programme is not valid JSON: {exc}",
                "steps_run": 0, "results": []}
    return replay_programme(program, executor)




def compile_replay_trace(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Compile a recorded trace into a programme (only
    successes compile — a failed call is refused here,
    never replayed as a success). Raises ValueError on a
    bad trace, exactly as the staged module does."""
    from . import replay as _replay

    return _replay.compile_trace(trace)




def replay_programme_text(program: Any) -> str:
    """Serialise a programme for storage (JSON only — a
    programme is data, never code). Raises ValueError on an
    invalid programme."""
    from . import replay as _replay

    return _replay.programme_json(program)




def replay_programme_tool(
    args: dict[str, Any],
    executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Tool-shaped handler for the READY replay_programme
    declaration (coordinator dispatch target)."""
    if not isinstance(args, dict) or not isinstance(
            args.get("programme"), str):
        return {"status": "error", "kind": "validation",
                "message": "replay_programme needs 'programme' as "
                           "JSON text.",
                "steps_run": 0, "results": []}
    return replay_programme_json(args["programme"], executor)




# ---------------------------------------------------------------------------
# Item 10 — status pin (verification, never a wire)
# ---------------------------------------------------------------------------
# (GAPS-1, 2026-11-12: the item-1 status pin is deleted with the
# patched engine it reported on; only the BR-5 pin remains.)




def br5_status() -> dict[str, Any]:
    """BR-5 honest playback state as data (already live).
    The chain is browser.py page_playback_votes ->
    music_player.py triangulate_playback ->
    _browser_playback_confirmed; the suite proves the
    2-of-3 rule on the pure function."""
    from . import music_player as _mp

    return {
        "live": True,
        "chain": (
            "core/browser.py:1913 page_playback_votes -> "
            "core/music_player.py:177 triangulate_playback -> "
            ":261 _browser_playback_confirmed"
        ),
        "rule": (
            "playing claimed only on 2-of-3 votes; one signal "
            "never claims it alone; player error is death "
            "regardless of other votes"
        ),
        "triangulate_example": _mp.triangulate_playback(
            True, True, False),
    }

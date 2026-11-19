"""Executor limits for the funnel (skills split B48, part 1 of 2).

Moved verbatim from core/funnel/tools_handlers.py: the deadline class
table, the post-timeout cool-down registry, and the cross-brain call
budget. The ToolHandlersMixin class stays in the facade; its methods and
the live clients resolve these names through the facade re-exports.
"""

# INTENTIONAL (skills split B48): imports exactly the names the moved code
# resolves by bare name (prover-verified; see prove_tools_handlers_p1.py).
# threading/time serve the module-level lock creations and clock seams.
import sys
import threading
import time
from typing import Any  # INTENTIONAL (skills split B48): annotation in moved code



# ------------------------------------------------------------------
# Executor limits (v5.48, programme item 28 — D-C4 + B-T3.17 +
# A12-residual): deadlines, a post-timeout cool-down, and a
# cross-brain safety budget, all enforced at this one funnel.
#
# Before v5.48, execute() ran handlers synchronously with NO
# deadline: a wedged handler (a hung subprocess wrapper, a stuck
# network read, an input call waiting on a dead app) blocked its
# brain's tool path forever, and the Live dispatch awaited the
# executor with no wait_for either. Now every call carries a
# deadline from the class table below; execute() enforces it on a
# daemon worker thread, and core/voice/live_client.py mirrors it with
# asyncio.wait_for. A deadline is DETECTION + REPORTING +
# COOL-DOWN, never thread murder: Python cannot kill a running
# thread, so a timed-out handler is honestly reported as
# ABANDONED — it may still finish (or wedge) in the background —
# and the cool-down below stops an immediate retry piling a
# second copy of the same call on top of it.
#
# Threading note: production execute() already runs on
# client/executor threads (Live's executor pool, the text brains'
# turn threads) and core handlers are Qt-free, so running the
# handler on one more daemon worker thread changes no threading
# contract. The confirm ASK is untouched by all of this: the gate
# precedes the handler (and already ran the ask on its own daemon
# thread before v5.48), so a human thinking over a popup is never
# charged against a tool deadline — deadlines cover the handler
# run only, exactly like the budget below counts only calls that
# reach dispatch.
_TOOL_DEADLINE_CLASS: dict[str, str] = {
    # "input" (30 s default) — direct input at the user's
    # direction: typing, keys, mouse/cursor movement and clicks,
    # UIA interaction, and the browser input verbs. These act on
    # the focused app and either land in well under a second or
    # are wedged; 30 s is already generous.
    "type_text": "input",
    "paste_text": "input",
    "paste_snippet": "input",
    "type_clipboard": "input",
    "press_hotkey": "input",
    "mouse_control": "input",
    "cursor_control": "input",
    "click_element": "input",
    "click_text": "input",
    "click_and_drag_path": "input",
    "drag_mouse": "input",
    "drag_file_onto": "input",
    "uia_click_control": "input",
    "click_by_mark": "input",  # v5.49 (programme item 30)
    "uia_type_text": "input",
    "scroll_until_found": "input",
    "browser_click": "input",
    "browser_control": "input",
    "browser_press": "input",
    "browser_select": "input",
    "browser_type": "input",
    # "long" (900 s default) — foreground work that legitimately
    # runs minutes, audited against the handlers (not the names):
    # winget_install / winget_upgrade_all run winget itself with
    # a 900 s internal timeout; create_restore_point /
    # restore_point_before_changes / windows_update_status wait
    # on 180 s system operations (Checkpoint-Computer, the
    # Windows Update search) that already exceed "standard";
    # list_drivers' signed-driver CIM query alone carries a
    # 120 s internal timeout; run_command's caller-supplied
    # timeout / job-wait may legitimately run minutes;
    # download_file streams up to the 25 MB cap over links of
    # any speed; sync_folder / zip_control / batch_rename /
    # batch_watermark / folder_watch_cook do bulk file work
    # whose duration scales with the tree they are handed;
    # run_routine and use_toolbox are dispatchers whose every
    # inner call re-enters execute() and is deadline'd on its
    # OWN class — the outer call must outlive the sum of its
    # steps, so it takes the long class too.
    "winget_install": "long",
    "winget_upgrade_all": "long",
    "create_restore_point": "long",
    "restore_point_before_changes": "long",
    "windows_update_status": "long",
    "list_drivers": "long",
    "run_command": "long",
    "download_file": "long",
    "sync_folder": "long",
    "sync_folders_two_way": "long",  # R9-4 mutagen port: walks and copies two whole folders
    "organise_photos": "long",  # R9-8 elodie port: walks and moves a whole camera dump
    "zip_control": "long",
    "batch_rename": "long",
    "batch_watermark": "long",
    "folder_watch_cook": "long",
    "run_routine": "long",
    # Field-check fix #32 (round-2 N5): run_anticipation_routine
    # is a dispatcher whose every inner call re-enters execute()
    # and is deadline'd on its OWN class — the outer call must
    # outlive the sum of its steps, same shape as run_routine /
    # run_workflow above.
    "run_anticipation_routine": "long",
    # Round-3 fix #4 (T12): run_plan is a dispatcher whose
    # every inner call re-enters execute() and is deadline'd on
    # its OWN class — the outer call must outlive the sum of
    # its steps, same shape as run_routine /
    # run_anticipation_routine above.
    "run_plan": "long",
    "use_toolbox": "long",
    # R7-4 meetily: stop_meeting transcribes the whole meeting and
    # transcribe_audio transcribes the recording it is handed — both
    # durations scale with the recording's length, so both take long.
    "stop_meeting": "long",
    "transcribe_audio": "long",
    # R7-7 taskflow: run_workflow is a dispatcher whose every inner
    # call re-enters execute() and is deadline'd on its OWN class —
    # the outer call must outlive the sum of its steps (plus retry
    # backoff), so it takes the long class too (the run_routine /
    # use_toolbox reasoning, same shape).
    "run_workflow": "long",
    # R11-1 ollama port: ollama_pull downloads a whole model (gigabytes)
    # from the model library via the local server — legitimately minutes,
    # so it takes the long class like download_file and winget_install.
    "ollama_pull": "long",
    # "none" (no deadline) — ONLY tools whose foreground duration
    # IS the user's request: record_audio blocks by design for
    # exactly the requested 1–120 s of recording (its handler
    # says so), and voice_note is the same shape (a caller-chosen
    # 3–120 s memo recorded in the foreground). Any deadline here
    # would cut a recording the user explicitly asked to last
    # that long; nothing else earns this class.
    "record_audio": "none",   # duration is the request (see above)
    "voice_note": "none",     # duration is the request (see above)
}
_TOOL_DEADLINE_DEFAULT_CLASS = "standard"
_TOOL_DEADLINE_CONFIG_FIELDS = {
    "input": "tool_deadline_input_s",
    "standard": "tool_deadline_standard_s",
    "long": "tool_deadline_long_s",
}


def _limits_config_value(field_name: str, default):
    """One executor-limits config value, read dynamically from
    the AppConfig singleton (the _session_provenance_lock_gated
    pattern): an unreadable config yields the default rather
    than breaking the funnel."""
    try:
        from ..config import config as _app_config  # Phase 3 F3: file now in core/funnel/; import climbs one more level

        value = getattr(_app_config, field_name, default)
    except Exception:  # noqa: BLE001 — limits must never break
        return default  # a tool call
    return default if value is None else value


def deadline_for(name: str, args: dict[str, Any] | None = None) -> float | None:
    """The deadline in seconds for one tool call (v5.48,
    programme item 28), or None when the call carries NO
    deadline — its class is "none" (see _TOOL_DEADLINE_CLASS),
    or its class's configured seconds are 0 or below (deadlines
    disabled for that class). `args` is accepted for future
    per-call refinement; the class today is a property of the
    tool alone. Never raises."""
    cls = _TOOL_DEADLINE_CLASS.get(name, _TOOL_DEADLINE_DEFAULT_CLASS)
    if cls == "none":
        return None
    field_name = _TOOL_DEADLINE_CONFIG_FIELDS[cls]
    default = {"input": 30.0, "standard": 120.0, "long": 900.0}[cls]
    try:
        seconds = float(_limits_config_value(field_name, default))
    except (TypeError, ValueError):
        seconds = default
    if seconds <= 0:
        return None
    return seconds


# --- Post-timeout cool-down (v5.48, programme item 28) --------
# Shared by execute() below and by the Live dispatch
# (core/voice/live_client.py imports note_tool_timeout /
# timeout_cooldown_active from here): one registry, so a timeout
# on any brain cools the tool+target down for every brain.
_timeout_cooldowns: dict[tuple[str, str], float] = {}
_timeout_cooldown_lock = threading.Lock()
# Injectable clock seam (suites replace it to travel in time).
_cooldown_now = time.monotonic


def _timeout_target_key(args: dict[str, Any] | None) -> str:
    """The cheap identity of WHAT a call aimed at: the first of
    the usual path/app/url/name-ish argument values, else "".
    Deliberately shallow — this keys a cool-down, not a cache.
    Audit fix Batch 3 (REL-02): the raw target is normalised
    with the receipt guard's own normalisers (path / URL
    spellings), so a retried call that re-spells its target
    still cools down instead of sailing past the 30-second
    window on a trailing slash or a case change. Names,
    titles, queries and commands are NOT case-folded — they
    are not paths or URLs, and folding them could merge
    genuinely different targets."""
    if not isinstance(args, dict):
        return ""
    try:
        from ..tool_receipts import (
            _PATH_KEYS as _CD_PATH_KEYS,
            _URL_KEYS as _CD_URL_KEYS,
            normalize_path_value,
            normalize_url_value,
        )  # Phase 3 F3: file now in core/funnel/; import climbs one more level
    except Exception:  # noqa: BLE001 — normalisers never break
        _CD_PATH_KEYS = frozenset()  # a cool-down
        _CD_URL_KEYS = frozenset()
        normalize_path_value = lambda v: str(v).strip()  # noqa: E731
        normalize_url_value = lambda v: str(v).strip()  # noqa: E731
    for key in ("path", "filepath", "destination", "save_path",
                "app_name", "app", "url", "name", "tool", "title",
                "query", "command"):
        value = args.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if key in _CD_PATH_KEYS:
            text = normalize_path_value(text)
        elif key in _CD_URL_KEYS or "://" in text:
            text = normalize_url_value(text)
        if text:
            return text[:256]
    return ""


def _timeout_cooldown_seconds() -> float:
    try:
        return float(_limits_config_value("tool_timeout_cooldown_s", 30.0))
    except (TypeError, ValueError):
        return 30.0


def note_tool_timeout(name: str, args: dict[str, Any] | None = None) -> None:
    """Record that `name` (against this call's target) just
    timed out, starting its cool-down (v5.48, programme item 28).
    Called by execute() on a sync deadline and by the Live
    dispatch on a wait_for timeout. Never raises."""
    try:
        key = (str(name), _timeout_target_key(args))
        with _timeout_cooldown_lock:
            _timeout_cooldowns[key] = _cooldown_now()
    except Exception:  # noqa: BLE001 — bookkeeping never breaks
        pass  # a tool path


def timeout_cooldown_active(name: str,
                            args: dict[str, Any] | None = None) -> str | None:
    """The refusal message when `name`+target is still cooling
    down after a recent timeout, else None (v5.48, programme
    item 28). A configured cool-down of 0 or below disables the
    registry. Expired entries are pruned on sight. Never raises."""
    try:
        window = _timeout_cooldown_seconds()
        if window <= 0:
            return None
        key = (str(name), _timeout_target_key(args))
        now = _cooldown_now()
        with _timeout_cooldown_lock:
            stamp = _timeout_cooldowns.get(key)
            if stamp is not None and now - stamp >= window:
                del _timeout_cooldowns[key]
                stamp = None
        if stamp is None:
            return None
        remaining = max(1, int(window - (now - stamp)) + 1)
        target = key[1]
        aimed = f" for '{target}'" if target else ""
        return (f"'{name}'{aimed} timed out a moment ago and is "
                f"cooling down — the abandoned call could not be "
                f"killed and may still be running, so a retry "
                f"right now could pile a second copy on top of "
                f"it. Try again in about {remaining} second(s).")
    except Exception:  # noqa: BLE001 — a broken registry must not
        return None  # refuse calls on its own say-so


# --- Safety budget (v5.48, programme item 28) -----------------
# A sliding 60-second window of the timestamps of calls that
# REACHED DISPATCH (post-gate, handler about to run — gated
# refusals, validation failures and cool-down refusals never
# count, because they never reach this code). This is a
# CROSS-BRAIN RUNAWAY BRAKE at the executor: it exists for the
# loop no single brain can see (or a single brain looping
# through different tools/brains in turn). It is explicitly NOT
# a per-turn round cap — the per-turn caps live on the brains
# (cloud_tool_rounds / local_tool_rounds, and since round-3 fix
# #2 the live loop's live_tool_rounds in _run_tool_batch);
# nothing HERE counts turns or rounds.
_budget_timestamps: list[float] = []
_budget_lock = threading.Lock()
# Injectable clock seam (suites replace it to travel in time).
_budget_now = time.monotonic
_BUDGET_WINDOW_S = 60.0


def _tool_call_budget() -> int:
    try:
        return int(_limits_config_value("tool_call_budget_per_minute", 120))
    except (TypeError, ValueError):
        return 120


# INTENTIONAL (skills split B48): the injectable clock seam, B23 pattern.
# Suites patch tools_handlers._budget_now through the FACADE module object
# (see tests/test_testiso.py:12 — "the existing injectable clock seam").
# This module's own _budget_now binding is an import-time snapshot that goes
# stale on rebind, so the budget check must read the clock through the
# facade at call time. Fallback is the local binding (direct-module import).
def _facade_budget_now():
    """Current budget clock: the facade's live binding, else local."""
    for _mod_name in (
        "voice_assistant.core.funnel.tools_handlers",
        "core.funnel.tools_handlers",
    ):
        _mod = sys.modules.get(_mod_name)
        if _mod is not None:
            _now = getattr(_mod, "_budget_now", None)
            if _now is not None:
                return _now()
    return _budget_now()


def _budget_check(name: str, is_read: bool) -> str | None:
    """Budget verdict with an explicit read/mutating split (A2, P1-1).

    Returns a refusal message, or None when the call may proceed.
    Cases, in order:
    * budget <= 0 (configured per-minute limit of 0): the brake
      is EXPLICITLY DISABLED by declared policy — proceed,
      record nothing. This is the only disabled state in the
      guard family, and it comes from configuration, never an
      error.
    * window full: the tripped message (mutating and read alike
      — a tripped brake stops everything).
    * internal error (clock/config/lock): MUTATING calls are
      refused with an "unavailable" message (the brake could
      not be verified, so the runaway risk is unmeasured);
      READ calls proceed WITHOUT recording, with a printed
      limiter-unavailable note (a read cannot run away, and
      refusing all reads on a broken clock would brick looking
      things up). The note is printed, like the tripped line,
      so the degradation is observable, never silent.
    """
    try:
        budget = _tool_call_budget()
        if budget <= 0:
            return None
        # INTENTIONAL (skills split B48): live facade read, see _facade_budget_now.
        now = _facade_budget_now()
        with _budget_lock:
            cutoff = now - _BUDGET_WINDOW_S
            _budget_timestamps[:] = [
                t for t in _budget_timestamps if t > cutoff]
            if len(_budget_timestamps) >= budget:
                return (f"Tool-call safety budget tripped: more "
                        f"than {budget} tool calls reached the "
                        f"executor in the last 60 seconds, which "
                        f"looks like a runaway loop — so '{name}' "
                        f"was not run. The brake resets as the "
                        f"window slides (within 60 seconds); "
                        f"nothing was executed for this call.")
            _budget_timestamps.append(now)
            return None
    except Exception as exc:  # noqa: BLE001 — A2 fail-closed
        if is_read:
            print(f"[Tools] BUDGET LIMITER UNAVAILABLE at '{name}': "
                  f"the safety budget could not be consulted "
                  f"({type(exc).__name__}) — this read proceeds "
                  "without recording; mutating calls refuse until "
                  "the brake can be verified.")
            return None
        return (f"Tool-call safety budget unavailable "
                f"({type(exc).__name__}): the runaway brake could "
                f"not be consulted, so '{name}' was not run. "
                "Nothing was executed for this call.")


def _budget_refusal(name: str) -> str | None:
    """Record one call reaching dispatch, or — when the sliding
    window is already full — return the budget refusal message
    WITHOUT recording it (a refused call must not extend its own
    lockout; the window drains as real dispatches age out).
    Returns None when the call may proceed. Never raises.
    Kept for callers that cannot prove read class; the
    mutating default is the conservative choice (A2: an
    unprovable call is refused on brake failure, never waved
    through). Funnel call sites pass their read class to
    _budget_check directly."""
    return _budget_check(name, is_read=False)


def reset_tool_budget() -> None:
    """Test-harness support (test-isolation batch): clear the
    process-global tool-call budget window.

    The production budget (120 calls / 60 s, _tool_call_budget)
    is UNCHANGED — this only empties the recorded timestamps, so
    a test session starts from a clean window. NO production path
    calls this: the brake must never be resettable by the code it
    guards. RULE (documented for every suite): any suite that
    executes tools through the funnel must call this in
    setUpModule (and tearDownModule, so it leaves no window
    behind for the next suite) — otherwise its tests inherit the
    previous suites' dispatches and trip on full-run ordering
    instead of on their own behavior. The budget clock seam is
    _budget_now (suites replace it to travel in time); the
    scheduler's equivalent seam is Scheduler._now_fn.
    """
    with _budget_lock:
        _budget_timestamps.clear()

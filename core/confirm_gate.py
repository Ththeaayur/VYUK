"""
Executor-level human-confirmation gate for VYUK (v4.3 hardening).

Today, destructive tools are gated by a ``confirm`` argument that
the *model itself* supplies — the brain can talk itself past its own
safety check. This module is the hook for a real human gate at the
executor level: before a critical tool runs, the executor asks
request_confirmation(); a registered handler (the GUI, showing a
native confirmation popup) asks the actual human; anything other
than an explicit yes is a no.

DEFAULT-DENY, always:
- no handler registered            → denied
- handler raises                    → denied
- handler doesn't answer in time    → denied (thread-based timeout)
- handler returns anything but the literal bool True → denied
  (A1, P1-6: "no", "false", None, 1 and other truthy non-bools
  are denials, logged as invalid responses — never coerced)

The handler receives (tool_name, args_summary) — a short, masked
summary string, never the raw args: values of sensitive-looking
keys (passwords, tokens, API keys…) are replaced with •••, because
the summary is shown on screen and written to the activity log.

HOW THE PARENT BUILD SHOULD WIRE THIS (nothing here is wired yet):
- GUI (gui/main_window.py): at startup, register a handler that
  marshals to the Qt thread and shows a confirmation dialog naming
  the tool and the summary, returning the user's choice:
  ``confirm_gate.register_handler(show_confirm_popup)``.
- Executor (core/funnel/tools_registry.py ToolRegistry.execute): for tools on the
  critical list, call
  ``confirm_gate.request_confirmation(name, args)`` before invoking
  the handler; on False, return a clear "cancelled by the user /
  not confirmed" error result. The handler runs on a worker thread
  with a 60 s default timeout, so a popup nobody answers denies the
  action instead of hanging the executor forever.
- Speech path: the handler may additionally speak the prompt — that
  is the GUI's choice; this module stays UI-free (no Qt imports).

Decisions are appended to data/activity.log in the codebase's
``timestamp | name | status`` convention (the same file
ToolRegistry._log_activity writes; that helper is a registry
method, not importable without side effects, so the convention is
replicated here) and also sent to the module logger. Logging never
raises and never changes the decision.
"""

import datetime
import hashlib
import inspect
import json
import logging
import math
import os
import pathlib
import re
import tempfile
import threading

log = logging.getLogger("nova.confirm_gate")

DEFAULT_TIMEOUT_S = 60.0

# v5.48 (programme item 19): the budget a handler should self-limit
# against — the gate gives up at DEFAULT_TIMEOUT_S, so a handler
# that waits for a human must stop asking sooner and return its
# denial while the gate is still listening (the GUI handler's 55 s
# wait vs the gate's 60 s is this relationship, pinned here).
HANDLER_BUDGET_S = DEFAULT_TIMEOUT_S - 5

_handler = None
_handler_arity = 2
_lock = threading.Lock()

# v5.48 (programme item 19): request correlation. Every
# request_confirmation() call draws the next ID from a process-wide
# monotonic sequence; the ID rides every decision log line for that
# request, is passed to 3-arg handlers, and keys the pending
# registry below so voice/notification surfaces can resolve a
# request the GUI handler hasn't answered (resolve_pending).
_seq_lock = threading.Lock()
_request_seq = 0

_pending_lock = threading.Lock()
_pending: dict = {}

# v5.49 (programme item 38): the decision-record join for the
# audit log (core/audit.py). _log_decision records the most recent
# decided request here — thread-locally (request_confirmation runs
# its decision logging on its caller's thread, which is the thread
# core/funnel/tools_registry.py's ToolRegistry.execute() then reads it back on, so concurrent
# executors never cross-attribute an ID) and in a process-wide
# fallback for readers on other threads. Additive only: the
# activity.log lines _log_decision writes are byte-unchanged.
_decision_local = threading.local()
_last_decision_lock = threading.Lock()
_last_decision: dict | None = None


def last_decision() -> dict | None:
    """The most recent confirmation decision record, or None.

    Returns a copy of {"request_id", "tool_name", "approved",
    "reason", "authority"} for the latest request _log_decision recorded —
    this thread's latest when it decided one, else the process's
    latest. core/funnel/tools_registry.py's ToolRegistry.execute() (v5.49, programme item 38) reads
    this immediately after request_confirmation returns and joins
    the request_id into its audit record; a caller whose ask was
    mocked or never reached a decision sees a record that did not
    change across its ask, and joins nothing."""
    local = getattr(_decision_local, "decision", None)
    if local is not None:
        return dict(local)
    with _last_decision_lock:
        return dict(_last_decision) if _last_decision else None


def _next_request_id() -> int:
    """The next confirmation-request ID (monotonic, process-wide)."""
    global _request_seq
    with _seq_lock:
        _request_seq += 1
        return _request_seq





# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------


def _handler_arity_for(fn) -> int:
    """3 when fn accepts (tool, summary, request_id), else 2.

    Inspected ONCE here, at registration (A1, P1-7) — never inferred
    from a TypeError raised by executing the handler. The old
    retry-on-TypeError fallback called a handler twice when it
    raised TypeError after doing work; arity-at-registration
    guarantees exactly one invocation per ask.
    """
    if fn is None:
        return 2
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return 2
    for param in params:
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            return 3
    positional = sum(
        1 for param in params
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY,
                          inspect.Parameter.POSITIONAL_OR_KEYWORD))
    return 3 if positional >= 3 else 2


def register_handler(fn) -> None:
    """Register the human-confirmation handler.

    fn(tool_name: str, args_summary: str) -> bool — return True only
    when the human explicitly approved. Registering replaces any
    previous handler; pass None to unregister.

    v5.48 (programme item 19): handlers MAY take a third argument,
    fn(tool_name, args_summary, request_id). A1 (P1-7): the arity is
    adapted HERE, once, by signature inspection — 3-arg handlers
    are called with the request ID, 2-arg handlers without it, each
    exactly once. A TypeError raised by the handler itself is a
    handler error (denial), never a cue to retry with fewer args.
    """
    global _handler, _handler_arity
    with _lock:
        _handler = fn
        _handler_arity = _handler_arity_for(fn)
    log.debug("confirm_gate handler %s", "registered" if fn else "cleared")


def clear_handler() -> None:
    """Unregister the handler (gate returns to deny-everything)."""
    register_handler(None)


def has_handler() -> bool:
    """True when a confirmation handler is currently registered."""
    with _lock:
        return _handler is not None


# ---------------------------------------------------------------------------
# Pending-request registry (v5.48, programme item 19)
#
# While request_confirmation() waits, its request sits in _pending
# keyed by request ID, with a threading.Event the waiter blocks on.
# The FIRST answer wins, whichever surface it arrives on: the
# registered handler thread, or resolve_pending() called by a
# voice/notification surface that showed the same prompt. A late
# answer for a finished/expired ID is dropped and logged.
# ---------------------------------------------------------------------------


def _strict_approval(value, tool_name: str, request_id=None) -> bool:
    """True ONLY for the literal bool True (A1, P1-6).

    Any other type or value — "no", "false", None, 1, 0 — is a
    denial, logged as an invalid confirmation response. bool()
    coercion is never applied to a human answer: bool("no") is
    True in Python, and a "no" must never approve. Only the
    response TYPE is logged, never the value, so a stray payload
    cannot leak through the log.
    """
    if type(value) is bool and value is True:
        return True
    log.info("confirm_gate invalid confirmation response for %s "
             "[req %s]: got %s, treated as denial",
             tool_name, request_id, type(value).__name__)
    return False


def _try_resolve(entry: dict, approved=None, error=None,
                 via=None) -> bool:
    """Claim `entry`'s resolution; True for the first answer only."""
    with _pending_lock:
        if entry.get("resolved"):
            return False
        entry["resolved"] = True
        entry["approved"] = approved
        entry["error"] = error
        if via is not None:
            entry["via"] = via
        entry["event"].set()
        return True


def pending_folder(request_id):
    """The folder a pending confirmation touches (UI rethink
    Build 4 §4.5: the per-call files ask's "Always allow
    this folder" button reads it by request id), or None
    when the request names no folder or is unknown. The
    folder comes from the call's raw args, never from the
    masked summary — a clipped summary must never decide
    which folder gets remembered."""
    try:
        rid = int(request_id)
    except (TypeError, ValueError):
        return None
    with _pending_lock:
        entry = _pending.get(rid)
    if entry is None:
        return None
    return entry.get("folder")


def pending_request():
    """The oldest live pending request, or None.

    Returns (request_id, tool_name, summary, expires_at) with
    expires_at as epoch seconds (time.time() based). Expired
    entries are pruned on the way past.
    """
    import time as _time

    now = _time.time()
    with _pending_lock:
        for rid in list(_pending):
            if _pending[rid]["expires_at"] <= now \
                    and _pending[rid].get("resolved"):
                _pending.pop(rid, None)
        for rid, entry in _pending.items():
            if not entry.get("resolved"):
                return (rid, entry["tool_name"], entry["summary"],
                        entry["expires_at"])
    return None


def resolve_pending(request_id, approved, via: str = "voice") -> bool:
    """Resolve a pending confirmation from another surface.

    Returns True only when `request_id` names a live, unresolved,
    unexpired request — that request's waiter then completes with
    `approved` as its decision. An unknown, finished or expired ID
    returns False and changes nothing.
    """
    import time as _time

    try:
        rid = int(request_id)
    except (TypeError, ValueError):
        return False
    with _pending_lock:
        entry = _pending.get(rid)
    if entry is None:
        log.info("confirm_gate resolve for unknown req %s dropped "
                 "(via %s)", rid, via)
        return False
    if entry["expires_at"] <= _time.time():
        log.info("confirm_gate resolve for expired req %s dropped "
                 "(via %s)", rid, via)
        return False
    if type(approved) is not bool:
        # A1 (P1-6): a non-bool resolve answer ("false", "no",
        # None, 1) resolves the request as a DENIAL, logged as an
        # invalid response — it never coerces to approval.
        log.info("confirm_gate invalid resolve response for req %s "
                 "(via %s): got %s, treated as denial",
                 rid, via, type(approved).__name__)
        if not _try_resolve(entry, approved=False, via=str(via)):
            log.info("confirm_gate late resolve for req %s dropped — "
                     "already answered (via %s)", rid, via)
            return False
        return True
    if not _try_resolve(entry, approved=approved, via=str(via)):
        log.info("confirm_gate late resolve for req %s dropped — "
                 "already answered (via %s)", rid, via)
        return False
    return True





# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Taint-mode batching (v5.45, H22c; re-keyed A1, P0-1)
#
# Under injection taint a burst of near-identical actions can queue
# a popup per call. Repeats within a short window collapse to the
# ONE decision already made, with a count; the popup summary itself
# leads with the taint source, so the human sees WHY they are
# being asked. Batching is taint-only: untainted confirmations
# always ask afresh. Fail-closed is untouched — a batched decision
# replays a real human decision, never invents one.
#
# A1 (P0-1): the batch key is NEVER the display summary. The old
# (tool, summary) key shared a key between two different emails
# whose subject/body differed only after the summary's 30-char
# clip, so the second sent with no second approval. The key is now
# a canonical digest of the FULL validated arguments + tool +
# handler identity, and sensitive operations never batch at all.
# ---------------------------------------------------------------------------

_BATCH_WINDOW_S = 10.0



_batch_lock = threading.Lock()
_batch_last: dict = {"key": None, "decision": False, "at": 0.0,
                     "count": 0, "origin_id": None}
# INTENTIONAL (2026-11-03 product fix, batch-key id-reuse trap):
# _batch_handler holds a STRONG reference to the handler object
# that made the batched decision, OUTSIDE the serialisable
# _batch_last record (never inside the key tuple). CPython reuses
# id() values once an object is freed, so a key of
# (tool, digest, id(handler)) alone is NOT handler identity: after
# clear_handler() freed handler C, a newly created handler J could
# receive the SAME id and replay C's approval inside the 10 s
# window without ever being consulted (proven in dc1, where C's
# id and J's False-lambda id printed identical). Holding C here
# keeps C alive while its record lives, so its id cannot be
# recycled at all; _batch_lookup additionally verifies identity
# (stored handler IS the current handler), so even a hypothetical
# key collision cannot replay across handlers. The key itself is
# unchanged — handler stays IN the key, so dropping it (which
# would make cross-handler replay permanent by design) is not
# an option. The 10 s window, digest and exemption set are
# untouched.
_batch_handler = None


def _taint_context():
    """(tainted, source) from core.injection, lazily.

    A1 fail-closed (advisory): when the taint state cannot be
    consulted, the ask is treated as TAINTED with source
    "unknown" — the popup warns and the ask never batches —
    rather than as clean. The source is honestly "unknown",
    never claimed as a real source, and the gate still never
    depends on the screen (it degrades to a warning, not a
    crash). Callers use this only for warning text + batch
    exemption; the human still decides.
    """
    try:
        from . import injection

        if injection.tainted():
            sources = []
            try:
                sources = injection.taint_sources()
            except Exception:  # noqa: BLE001
                sources = []
            return True, (injection.taint_source(), sources)
    except Exception:  # noqa: BLE001 — A1 fail-closed: unconsultable
        # reads TAINTED with an honestly-unknown source.
        return True, ("unknown", [])
    return False, (None, [])


def last_batch_count() -> int:
    """How many identical asks the current batched decision has
    covered (1 = no batching happened)."""
    with _batch_lock:
        return int(_batch_last.get("count") or 0)


def _batch_lookup(key, handler=None) -> bool | None:
    """The batched decision for `key` inside the window, else None.
    A hit increments the collapse count.

    INTENTIONAL (2026-11-03 product fix): a key match alone is
    NOT enough — the stored handler must BE the current handler
    by identity (`is`). A recycled id() (see _batch_handler)
    therefore never replays, and a miss here increments nothing.
    """
    import time as _time

    with _batch_lock:
        if _batch_last.get("key") != key:
            return None
        if _time.monotonic() - float(_batch_last.get("at") or 0) \
                > _BATCH_WINDOW_S:
            return None
        # Identity, not id(): a different handler object with a
        # recycled id must be asked afresh, never replayed to.
        if _batch_handler is not handler:
            return None
        _batch_last["count"] = int(_batch_last.get("count") or 1) + 1
        return bool(_batch_last.get("decision"))


def _batch_origin_id():
    """The request ID that produced the current batched decision
    (v5.48, programme item 19 — a replay logs BOTH its own ID and
    the ID it replayed from)."""
    with _batch_lock:
        return _batch_last.get("origin_id")


def _batch_store(key, decision: bool, origin_id=None,
                 handler=None) -> None:
    import time as _time

    global _batch_handler
    with _batch_lock:
        # v5.48 (programme item 19): remember WHICH request made
        # this decision, so a later replay can name its origin.
        _batch_last.update({"key": key, "decision": bool(decision),
                            "at": _time.monotonic(), "count": 1,
                            "origin_id": origin_id})
        # INTENTIONAL (2026-11-03 product fix): hold the handler
        # object itself, not just its id, so its id cannot be
        # recycled while this record lives (see _batch_handler).
        _batch_handler = handler


def request_confirmation(tool_name: str, args: dict | None = None,
                         timeout_s: float = DEFAULT_TIMEOUT_S,
                         verbatim_keys=frozenset()) -> bool:
    """Ask the registered human handler; deny on anything but a clear yes.

    Runs the handler on a daemon thread and waits at most
    `timeout_s` seconds (default 60) so an unanswered popup can
    never hang the executor. `verbatim_keys` is forwarded to
    summarize_args (see its docstring — run_command's command is
    shown in full).

    H22c: under injection taint the summary leads with the taint
    source(s), and an identical non-sensitive repeat inside
    _BATCH_WINDOW_S replays the decision already given instead of
    popping up again. A1 (P0-1): the repeat key is a canonical
    digest of the FULL arguments (never the clipped display
    summary), and sensitive operations never batch — they always
    prompt afresh.

    v5.48 (programme item 19): every call draws a monotonic
    request ID (see _next_request_id) that keys the pending
    registry, is passed to 3-arg handlers, and rides every
    decision log line for this request. The wait completes on the
    FIRST answer — the handler thread's, or resolve_pending()
    from a voice/notification surface.
    """
    import time as _time

    request_id = _next_request_id()
    summary = summarize_args(args, verbatim_keys=verbatim_keys)
    # MN-5.14 never-automate money guard (v5.66, Money track
    # leaf 2) — FIRST, before the allowlist shortcut, before
    # any batched-decision replay, and before the handler is
    # ever invoked: a refused call is never asked about, so no
    # human yes, remembered kind, or replayed decision can
    # approve it. The refusal names its rule (the message
    # carries no user value). A2 (P0-2): a guard ERROR is
    # UNAVAILABLE, never "no refusal" and never "disabled"
    # (no explicit disabled state exists for the money guard
    # — only a declared policy could disable it, and an
    # exception is not a declaration). An ask that cannot
    # verify its money guard is denied outright, with a
    # machine-readable reason, before the handler is ever
    # invoked. Availability cost is deliberate: the guard
    # module is leaf-stable (the leaf suite pins it
    # importable), so this path should never trigger in
    # practice — but if it does, it must fail shut.
    try:
        from .funnel import money_guard as _mg  # Phase 3 F2b: money_guard moved to core/funnel/

        _mg_refusal = _mg.check(
            tool_name, args if isinstance(args, dict) else {})
    except Exception as exc:  # noqa: BLE001 — A2 fail-closed
        _log_decision(
            tool_name, False,
            f"money guard unavailable ({type(exc).__name__}) — "
            "denied: the money guard could not be consulted",
            request_id=request_id, authority="money-guard")
        return False
    if _mg_refusal is not None:
        _log_decision(
            tool_name, False,
            f"money guard refused: {_mg_refusal.message}",
            request_id=request_id, authority="money-guard")
        return False
    tainted_now, (tsource, tsources) = _taint_context()
    with _lock:
        handler = _handler
        arity = _handler_arity
    batch_key = None
    if tainted_now:
        named = ", ".join(tsources) if tsources else str(tsource)
        summary = (f"⚠ Taint source: {named} — untrusted content "
                   f"prompted this. Action: {tool_name}. {summary}")
        # A1 (P0-1): sensitive asks NEVER batch — they always prompt
        # afresh, even if an identical ask was just approved. For the
        # remaining batchable repeats the key is a canonical digest
        # of the FULL validated arguments + tool + handler identity
        # — never the display summary, which clips non-critical
        # fields at 30 chars and drops over budget (two different
        # emails sharing a clipped prefix shared a key before).
        # The batch key carries the HANDLER's identity: a decision
        # replays only for the same human-facing handler that made
        # it — a freshly registered handler is always asked afresh.
        # 2026-11-03 product fix: id(handler) in the key is NOT
        # sufficient identity (CPython recycles ids of freed
        # objects), so _batch_store also keeps the handler object
        # itself alive and _batch_lookup checks `is` identity.
        batched = None
        if _is_batch_exempt_tool(tool_name, args):
            batch_key = None
        else:
            batch_key = (tool_name, _canonical_args_digest(args),
                         id(handler))
            # 2026-11-03 product fix: pass the handler object for
            # the identity check in _batch_lookup — the id() in
            # the key alone is not handler identity (see
            # _batch_handler).
            batched = _batch_lookup(batch_key, handler)
        if batched is not None:
            # v5.48 (programme item 19): the replay logs THIS
            # request's ID and names the request it replayed from.
            _log_decision(
                tool_name, batched,
                f"batched repeat #{last_batch_count()} of an "
                f"identical tainted ask — decision replayed from "
                f"req {_batch_origin_id()}",
                request_id=request_id, authority="batched")
            return batched
    # v5.54.1: the per-kind allowlist is checked BEFORE
    # prompting — but only for allowlistable kinds, and never
    # under hard taint (any taint for browser_type), a shell
    # foreground for type kinds, or the provenance lock
    # (see _allowlist_shortcut_applies).
    # The decision is still logged with its request ID, so the
    # audit join reads an allowlisted approval exactly like a
    # human one — the trail never has a gap where a popup
    # used to be.
    if _allowlist_shortcut_applies(tool_name, args):
        _log_decision(
            tool_name, True,
            "allowlisted kind — no popup (remembered in "
            "Settings, revocable there)",
            request_id=request_id, authority="allowlist")
        return True
    if handler is None:
        _log_decision(tool_name, False,
                      "no confirmation handler registered",
                      request_id=request_id, authority="no-handler")
        return False

    timeout = max(0.1, float(timeout_s))
    # v5.48 (programme item 19): register the pending entry BEFORE
    # the handler thread starts, so a resolve_pending() racing the
    # popup still finds its request.
    # UI rethink Build 4 (§4.5): the per-call files ask's
    # folder — the folder this call touches, for the
    # dialog's "Always allow this folder" button. Computed
    # here, from the raw args (the dialog only ever sees
    # the masked summary, and a clipped summary must never
    # decide which folder gets remembered). None when the
    # call names no folder. Stored on the pending entry so
    # the dialog can read it by request id; never raises.
    confirm_folder = None
    try:
        from .funnel import tools_gate_paths as _paths

        confirm_folder = _paths.first_call_folder(
            tool_name, args if isinstance(args, dict) else {})
    except Exception:  # noqa: BLE001 — no folder, no button
        confirm_folder = None
    entry: dict = {
        "tool_name": tool_name, "summary": summary,
        "expires_at": _time.time() + timeout,
        "event": threading.Event(), "resolved": False,
        "approved": None, "error": None, "via": None,
        "folder": confirm_folder,
    }
    with _pending_lock:
        _pending[request_id] = entry

    def _ask() -> None:
        try:
            # A1 (P1-7): arity was adapted at registration time —
            # the handler is called exactly once, with the arity it
            # declared. A TypeError raised by the handler itself is
            # a handler error (denial), never a cue to retry with
            # fewer arguments. A1 (P1-6): only the literal bool
            # True approves; any other return value is a denial.
            if arity == 3:
                answered = handler(tool_name, summary, request_id)
            else:
                answered = handler(tool_name, summary)
            if not _try_resolve(
                    entry,
                    approved=_strict_approval(
                        answered, tool_name, request_id)):
                log.info("confirm_gate late handler answer for req "
                         "%s dropped — request already finished",
                         request_id)
        except Exception as e:  # noqa: BLE001 — a broken handler denies
            if not _try_resolve(entry, error=e):
                log.info("confirm_gate late handler error for req "
                         "%s dropped — request already finished",
                         request_id)

    thread = threading.Thread(
        target=_ask, name="nova-confirm-gate", daemon=True
    )
    try:
        thread.start()
        if not entry["event"].wait(timeout):
            # Timed out: claim the resolution so the handler's late
            # answer (and any late resolve) is dropped, not used.
            if _try_resolve(entry, approved=False):
                _log_decision(
                    tool_name, False,
                    f"timed out after {timeout_s:.0f}s — no answer",
                    request_id=request_id, authority="timeout")
                return False
            # Someone answered in the boundary instant — fall
            # through and honour that answer instead.
        if entry.get("error") is not None:
            _log_decision(
                tool_name, False,
                f"handler error: {entry['error']}",
                request_id=request_id, authority="popup")
            return False
        approved = bool(entry.get("approved"))
        via = entry.get("via")
        if via:
            reason = (f"approved by user (via {via})" if approved
                      else f"declined by user (via {via})")
        else:
            reason = ("approved by user" if approved
                      else "declined by user")
        _log_decision(tool_name, approved, reason,
                      request_id=request_id, authority="popup")
        if batch_key is not None:
            _batch_store(batch_key, approved, origin_id=request_id,
                         handler=handler)
        return approved
    finally:
        # Unregistered in ALL exit paths (v5.48, programme item 19).
        with _pending_lock:
            _pending.pop(request_id, None)


# ---------------------------------------------------------------------------
# Logging — the codebase's activity.log convention; never raises
# ---------------------------------------------------------------------------


def _log_decision(tool_name: str, approved: bool, reason: str,
                  request_id=None, authority: str | None = None) -> None:
    decision = "APPROVED" if approved else "DENIED"
    # v5.48 (programme item 19): every decision line carries its
    # request ID, so a popup, a voice answer and the log can be
    # correlated after the fact. Callers without an ID (legacy
    # direct calls) log exactly as before.
    req = f" [req {request_id}]" if request_id is not None else ""
    # v5.49 (programme item 38): record the decision for the audit
    # log's join (see last_decision above) — only ID-carrying
    # decisions can join, so ID-less legacy calls record nothing.
    # GAPS-23 (F-V6): the record names its AUTHORITY — which of
    # the approval paths granted (or denied) it: "popup" (the
    # human answered the popup, directly or via voice /
    # notification), "allowlist" (a remembered kind, no popup),
    # "plan" (never logged here — plan approvals bypass the ask
    # and carry their own audit value), "batched" (a replayed
    # identical tainted ask), or a denial authority
    # ("money-guard" / "timeout" / "no-handler"). Before F-V6 an
    # allowlisted approval joined as "user-approved", the same
    # structured value as a popup approval — indistinguishable.
    if request_id is not None:
        global _last_decision
        entry = {"request_id": request_id, "tool_name": tool_name,
                 "approved": bool(approved), "reason": reason,
                 "authority": authority}
        _decision_local.decision = entry
        with _last_decision_lock:
            _last_decision = entry
    log.info("confirm_gate %s %s%s (%s)", decision, tool_name, req, reason)
    try:
        log_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "data" / "activity.log"
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} | confirm_gate | {tool_name}: {decision}{req} ({reason})\n")
    except Exception:  # noqa: BLE001 — logging must never change a decision
        pass

# INTENTIONAL (skills split B18): the scrub/summarize family moved to
# confirm_gate_scrub.py; the facade re-exports every moved name so
# all existing importers (incl. audit.py / clipboard_monitor.py /
# failure_script.py from-imports) resolve identically.
from .confirm_gate_scrub import (
    _SENSITIVE_KEYS, FULL_FIDELITY_KEYS, _NONCRITICAL_CLIP,
    _KNOWN_SECRET_PREFIXES, _B64_RUN, _HEX_RUN, _looks_like_secret,
    _is_sensitive, summarize_args,
)

# INTENTIONAL (skills split B18): the allowlist family moved to
# confirm_gate_allowlist.py; the facade re-exports every moved name.
# (test_f01f02's unique-tmp pin for _save_allowlist moves with it —
# see that test's INTENTIONAL comment.)
from .confirm_gate_allowlist import (
    _ALLOWLIST_FILE, _NEVER_ALLOWLIST_KINDS, _EXTRA_ALLOWLISTABLE_KINDS,
    _allowlist_path, _load_allowlist, _save_allowlist, is_allowlistable,
    is_allowlisted, allow_kind, revoke_kind, list_allowlisted,
    _allowlist_shortcut_applies,
)

# INTENTIONAL (skills split B18): the pure digest/exempt helpers moved
# to confirm_gate_helpers.py; the facade re-exports them for
# request_confirmation's bare-name resolution.
from .confirm_gate_helpers import (
    _canonical_args_digest, _is_batch_exempt_tool,
)

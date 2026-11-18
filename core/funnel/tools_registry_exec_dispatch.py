# tools_registry execute-dispatch mixin (batch 2 split):
# execute() stages: rule of two, executor limits, receipt-error
# dispatch-point enforcement, mark dispatched, deadline + record
# legs, and dispatch (bracket setup + dispatch Try as one stage
# method, so _bracket/_op_lock/_op_cell/_end_bracket_once/
# _handler_worker stay in one scope). Stage bodies moved
# verbatim apart from the one authorised plumbing change:
# cross-stage names read/write ctx.<name>.
import inspect
import threading
from typing import Any

from .. import injection  # Phase 3 F3: file now in core/funnel/; import climbs one more level
from .tools_registry_core import _funnel_dispatch_scope  # GAPS-23 (F-V7): capability boundary
from .tools_gate import (
    _UNTRUSTED_PRODUCERS,
    _confirmation_args,
    _handler_error_kind,
    _is_read_class,
    _wrap_producer_result,
    mcp_is_read,
)
from .tools_gate_taint import wrap_mcp_content
from .tools_handlers import (
    _budget_check,
    deadline_for,
    note_tool_timeout,
    timeout_cooldown_active,
)


class ToolRegistryExecDispatchMixin:
    def _exec_rule_of_two(self, ctx):
        # v5.55 (Wave 1, SE-3.1): RULE OF TWO — the session-level
        # budget over legs A/B/C (core/funnel/rule_of_two.py). It sits
        # AFTER the confirmation gate (so an approval the gate
        # just gave satisfies it — see _human_approved_this_call
        # above) and BEFORE budget/deadline/dispatch (so a
        # denied ask consumes no budget and runs nothing).
        # Untainted sessions can never trip (leg A absent), and
        # the ask names the rule: the human is deciding on the
        # COMBINATION (untrusted input + private data + a way
        # out), not just on this one tool.
        ctx._rot_legs: set[str] = set()
        try:
            from . import rule_of_two as _rot_mod  # Phase 3 F2b: rule_of_two moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))

            # GAPS-12: one Rule-of-Two stage. MCP calls check
            # the legs for the tool's MCP class; native calls
            # check the legs for the name+args.
            if ctx._mcp_entry is not None:
                ctx._rot_legs = _rot_mod.legs_for_mcp_call(ctx._mcp_class)
            else:
                ctx._rot_legs = _rot_mod.legs_for_native_call(ctx.name, ctx.args)
            if _rot_mod.completes_forbidden_union(ctx._rot_legs) \
                    and not ctx._human_approved_this_call:
                if ctx._mcp_entry is not None:
                    # MCP view: names the external server and
                    # the tool's own description, because the
                    # human is approving code VYUK did not
                    # write. Same text + verbatim keys as the
                    # old MCP branch's ask.
                    _rot_view = {
                        "tool": ctx.name,
                        "server": ctx._mcp_server,
                        "description": ctx._mcp_entry.get(
                            "description", ""),
                        "arguments": (ctx.args
                                      if isinstance(ctx.args, dict) else {}),
                        "rule_of_two": (
                            "Rule of Two: this session has already handled "
                            "untrusted content and touched private data, "
                            "and this call would add the third leg "
                            "(changing state or communicating externally) "
                            "— all three together, without oversight, is "
                            "the configuration VYUK refuses to hold. "
                            "Approving runs just this call; the budget "
                            "itself stays in place."),
                    }
                    _rot_ask = self._ask_human(
                        ctx.name, _rot_view, {"arguments", "rule_of_two"},
                        ctx._audited, ctx._audit_gate)
                else:
                    _rot_view = _confirmation_args(ctx.name, ctx.args)
                    _rot_view["rule_of_two"] = (
                        "Rule of Two: this session has already handled "
                        "untrusted content and touched private data, "
                        "and this call would add the third leg "
                        "(changing state or communicating externally) "
                        "— all three together, without oversight, is "
                        "the configuration VYUK refuses to hold. "
                        "Approving runs just this call; the budget "
                        "itself stays in place.")
                    _rot_ask = self._ask_human(
                        ctx.name, _rot_view, {"rule_of_two"},
                        ctx._audited, ctx._audit_gate)
                if _rot_ask is not True:
                    return _rot_ask
                if ctx._epoch_void():
                    self._log_activity(
                        ctx.name,
                        "error: session changed while awaiting "
                        "confirmation — approval void")
                    ctx._audit_gate["value"] = "refused:epoch-void"
                    return ctx._audited({
                        "status": "error", "kind": "refused",
                        "message": (
                            "Session changed while awaiting "
                            "confirmation — approval void, tool "
                            "not run.")})
        except Exception as _rot_exc:  # noqa: BLE001 — GAPS-12
            # (flaw #10b) FAIL CLOSED: the old code logged
            # "call proceeding without the budget this once"
            # and let the call run — a safety gate failing
            # OPEN. A failed second check now REFUSES the
            # action with an honest reason, and nothing runs.
            # (A _rot_ask that already returned an error dict
            # above returns through it — a return is not an
            # exception, so it is unaffected by this guard.)
            # Loud, not silent: a broken budget is a finding,
            # and the next call re-evaluates from scratch.
            try:
                import logging

                logging.getLogger("nova.tools").error(
                    "Rule-of-Two check failed (%s: %s) — call "
                    "refused",
                    type(_rot_exc).__name__, _rot_exc)
            except Exception:  # noqa: BLE001 — logging never breaks
                pass  # a tool call either
            self._log_activity(
                ctx.name,
                f"error: Rule-of-Two check failed "
                f"({type(_rot_exc).__name__}: {str(_rot_exc)[:200]}) "
                f"— call refused")
            ctx._audit_gate["value"] = "refused:rule-of-two-error"
            return ctx._audited({
                "status": "error", "kind": "refused",
                "message": (
                    "Rule-of-Two check failed — the second check "
                    "could not be completed, so nothing was run. "
                    f"({type(_rot_exc).__name__}: "
                    f"{str(_rot_exc)[:200]})")})

    def _exec_limits(self, ctx):
        # v5.48 (programme item 28): EXECUTOR LIMITS — cool-down,
        # then the safety budget, then the deadline, in that
        # order, all AFTER every gate above has passed and BEFORE
        # dispatch. A call still cooling down from a recent
        # timeout of the same tool+target never runs (its
        # abandoned predecessor may still be alive); a cooled-down
        # refusal is not a dispatch, so it consumes no budget.
        _cooling = timeout_cooldown_active(ctx.name, ctx.args)
        if _cooling is not None:
            self._log_activity(
                ctx.name, "error: timeout cool-down — not run")
            # v5.49 (programme item 38): audit gate disposition.
            ctx._audit_gate["value"] = "refused:timeout-cooldown"
            return ctx._audited({"status": "error", "kind": "timeout",
                             "message": _cooling})
        # The budget counts only calls that REACH DISPATCH (this
        # line): every refusal above — validation, failsafe,
        # protected paths, the confirmation gate, the cool-down —
        # returned before it and consumed nothing.
        # GAPS-12: the budget counts MCP calls by their MCP
        # read class, native calls by _is_read_class — one
        # limits stage, not two.
        _budget_msg = _budget_check(
            ctx.name,
            mcp_is_read(ctx._mcp_entry)
            if ctx._mcp_entry is not None
            else _is_read_class(ctx.name, ctx.args))
        if _budget_msg is not None:
            self._log_activity(
                ctx.name, "error: tool-call budget tripped — not run")
            print(f"[Tools] SAFETY BUDGET TRIPPED at '{ctx.name}': "
                  f"{_budget_msg}")
            ctx._audit_gate["value"] = "refused:budget"  # v5.49 (item 38)
            return ctx._audited({"status": "error", "kind": "budget",
                             "ok": False,
                             "message": _budget_msg})

    def _exec_receipt_check(self, ctx):
        # Audit fix Batch 3 (REL-01 dispatch-point mandate):
        # the attempt record failed at the receipt block above.
        # Every gate has now passed, so this is the last point
        # where refusing is still honest: a non-read call whose
        # attempt could not be evidenced does NOT dispatch.
        # Read calls, disabled receipts, and calls whose attempt
        # landed are unaffected; a refusal here confers no
        # Rule-of-Two legs and consumes no handler run.
        if ctx._receipt_error and not ctx._receipt_is_read:
            self._log_activity(
                ctx.name, "error: receipt attempt failed — not run")
            ctx._audit_gate["value"] = "refused:receipt-unavailable"
            ctx._obs_failures.append("receipts")
            return ctx._audited({
                "status": "refused", "kind": "refused",
                "message": (
                    f"Refused: the tool-receipt store could not "
                    f"record this call to '{ctx.name}' before dispatch, "
                    f"so its outcome could not be evidenced — "
                    f"nothing was run. Repair or rotate "
                    f"data/tool_receipts.jsonl and retry.")})

    def _exec_mark_dispatched(self, ctx):
        # Regression batch (R2): mark the attempt dispatched
        # here — after every gate (this mandate is the last one)
        # and BEFORE the taint bracket below, so a mark failure
        # refuses before anything runs and leaks no bracket.
        # Later refusals (e.g. dropped args) record outcomes and
        # so resolve their attempts; only a crash between the
        # attempt record and this point leaves an unmarked
        # attempt, and that call never ran, so the guard must
        # not block its retry. Mark failure fails closed
        # (REL-01 rule) for non-read calls only; reads never
        # mark (their attempts are never consulted).
        if not ctx._receipt_is_read and ctx._receipt_attempt is not None:
            try:
                from .. import tool_receipts as _mark_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                _marked = _mark_mod.try_mark_dispatched(
                    ctx._receipt_attempt)
            except Exception:
                _marked = False
            if not _marked:
                ctx._audit_gate["value"] = "refused:receipt-unavailable"
                return ctx._audited({
                    "status": "refused", "kind": "refused",
                    "message": (
                        f"Refused: the tool-receipt store could "
                        f"not mark this call to '{ctx.name}' as "
                        f"dispatched, so its outcome could not "
                        f"be evidenced — nothing was run. "
                        f"Repair or rotate "
                        f"data/tool_receipts.jsonl and retry.")})

    def _exec_deadline_legs(self, ctx):
        ctx._deadline = deadline_for(ctx.name, ctx.args)
        # v5.55 (Wave 1, SE-3.1): record the legs this call
        # exercises — at dispatch, after every gate passes, so
        # only calls that actually run count toward the session
        # budget. Every refusal above returned before this line
        # and conferred nothing.
        try:
            from . import rule_of_two as _rot_record  # Phase 3 F2b: rule_of_two moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))

            _rot_record.record_legs(ctx._rot_legs)
        except Exception:  # noqa: BLE001 — recording never breaks
            pass  # a dispatch (the check above already ran)

    def _exec_dispatch(self, ctx):
        if ctx._mcp_entry is not None:
            # GAPS-12: the MCP leg of the ONE dispatch stage.
            # The bus call runs here, after every gate above
            # (validation, failsafe, kill-chain, confirm,
            # Rule of Two, limits, receipt, deadline) has
            # passed for this call — the same order native
            # handlers get. The legs were already recorded by
            # _exec_deadline_legs, so this leg records none.
            return self._exec_dispatch_mcp(ctx)
        # v5.45 (H18): the handler run is bracketed in the taint
        # in-flight counter, so a clear_taint() landing mid-run is
        # queued until the funnel drains instead of outrunning a
        # tool still executing (core/injection.clear_taint).
        injection.begin_execution()
        # Audit A5 (P1-3 nuance): the bracket handoff between the
        # caller and the deadline worker is lock-protected
        # (core.injection.ExecutionBracket). The old boolean
        # handoff had a race: a handler finishing between join()
        # returning and the caller's timeout flag being set left
        # the bracket begun-but-never-ended (begin=1, end=0),
        # stalling every queued taint clear. The protocol ends
        # the bracket exactly once on every path — success,
        # failure, timeout, cancellation. The timeout operation
        # cell has its own lock: a late worker outcome is either
        # finalized by the worker (op already recorded) or
        # stashed for the caller to finalize the moment it
        # records the op — never lost, never twice (and
        # finalize_operation itself is exactly-once).
        _bracket = injection.ExecutionBracket()
        _op_lock = threading.Lock()
        _op_cell: dict[str, Any] = {"op_id": None, "late": None}
        # GAPS-24 (K-V3/K-V8): the live-call registration for this
        # dispatch (0 = none). Initialised here — the outer
        # finally below unregisters, so the names must exist even
        # when the token plumbing below fails before assigning.
        _live_handle: int = 0
        _live_worker: Any = None

        def _end_bracket_once() -> None:
            try:
                injection.end_execution()
            except Exception:  # noqa: BLE001 — never breaks a call
                pass

        try:
            handler = self._tools[ctx.name]["handler"]
            _call_args, _dropped = self._dispatch_args(
                ctx.name, handler, ctx.args)
            # Audit fix Batch 3 (REL-03 token plumbing): the
            # opt-in side channel. Only a handler that declares
            # a `cancel_token` parameter receives one — every
            # other handler's call args are byte-identical to
            # before, and validation (which judges `args`, not
            # _call_args) never sees the token.
            try:
                from . import tool_cancel as _cancel_mod  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)

                _call_token = (
                    ctx.cancel_token
                    if ctx.cancel_token is not None
                    else _cancel_mod.CancellationToken())
                _handler_params = inspect.signature(
                    handler).parameters
                if ("cancel_token" in _handler_params
                        and "cancel_token" not in _call_args):
                    _call_args = {**_call_args,
                                  "cancel_token": _call_token}
            except Exception:  # noqa: BLE001 — token plumbing
                _call_token = None  # never breaks a dispatch
            # GAPS-24 (K-V3/K-V8): register the call's token so a
            # failsafe trip can cancel this in-flight call (the
            # trip listener is installed on the first registration;
            # a caller-supplied token is registered too — a trip
            # means stop everything, and the task listener already
            # cancels task tokens the same way). Registration never
            # breaks a dispatch; a None token registers nothing.
            try:
                from . import tool_cancel as _cancel_live  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)

                _live_handle = _cancel_live.register_live_call(
                    _call_token, ctx.name)
            except Exception:  # noqa: BLE001 — see above
                _live_handle = 0
            if _dropped and not _is_read_class(ctx.name, ctx.args):
                # Audit fix Batch 2 (TOOL-02): an undeclared key
                # on an effectful tool must REFUSE, never run
                # with a qualifier silently stripped (a dropped
                # path/scope/account on a destructive tool is
                # the worst failure mode in this set). This is
                # belt-and-braces behind validation — closed
                # schemas refuse first — and fires for any tool
                # left open (OPEN_SCHEMA_TOOLS aside, which
                # takes **kwargs and so never drops).
                self._log_activity(
                    ctx.name, "error: refused — undeclared args "
                          f"dropped for effectful tool: {_dropped}")
                ctx._audit_gate["value"] = "refused:undeclared-args"
                return ctx._audited({
                    "status": "refused", "kind": "refused",
                    "message": (
                        f"Refused: '{ctx.name}' was called with "
                        f"undeclared argument(s) "
                        f"{_dropped} that its handler does not "
                        f"accept — the call was NOT run. Fix "
                        f"the arguments to match the tool's "
                        f"declared schema and call again.")})
            if ctx._deadline is None:
                # GAPS-23 (F-V7): the capability boundary — the
                # handler runs inside the dispatch scope only.
                with _funnel_dispatch_scope():
                    result = handler(**_call_args)
            else:
                _outcome: dict[str, Any] = {}
                # Settings redesign (2026-11-16, §5/§8): the
                # live-origin flag is a contextvar, and the
                # worker thread starts with fresh contextvars —
                # the caller's flag does not cross the boundary
                # (the funnel flag is re-set in the worker for
                # the same reason). Capture it here so the
                # worker can re-establish it: a handler that
                # keys on live origin (the app_settings voice
                # protocol, the session-grant gate) otherwise
                # never sees a live call through execute() —
                # every live set would silently direct-apply.
                try:
                    from . import live_origin as _live_origin_mod

                    _live_caller = bool(
                        _live_origin_mod.is_live_origin())
                except Exception:  # noqa: BLE001 — no flag, no grant
                    _live_origin_mod = None
                    _live_caller = False

                def _handler_worker() -> None:
                    try:
                        # GAPS-23 (F-V7): the worker thread starts
                        # with fresh contextvars, so the scope is
                        # set HERE, in the worker — the caller's
                        # flag does not cross the thread boundary.
                        with _funnel_dispatch_scope():
                            if _live_caller \
                                    and _live_origin_mod is not None:
                                _live_token = (
                                    _live_origin_mod.set_live_origin())
                                try:
                                    _outcome["result"] = handler(
                                        **_call_args)
                                finally:
                                    _live_origin_mod.reset_live_origin(
                                        _live_token)
                            else:
                                _outcome["result"] = handler(
                                    **_call_args)
                    except BaseException as exc:  # noqa: BLE001 —
                        _outcome["error"] = exc  # ferried to caller
                    finally:
                        _late: dict[str, Any] | None = None
                        if "error" in _outcome:
                            _err = _outcome["error"]
                            _late = {"status": "failed",
                                     "error": type(_err).__name__}
                        elif "result" in _outcome:
                            _late = {"status": "completed"}
                        _op_id: str | None = None
                        with _op_lock:
                            _op_id = _op_cell["op_id"]
                            if _op_id is None:
                                _op_cell["late"] = _late
                        if _bracket.worker_finished():
                            _end_bracket_once()
                        if _op_id is not None and _late is not None:
                            try:
                                from . import tool_operations as _ops  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)

                                _ops.finalize_operation(_op_id, _late)
                            except Exception:  # noqa: BLE001
                                pass
                        # GAPS-24 (K-V3/K-V8): this worker owns the
                        # live-call registration from here on — it
                        # unregisters when it lands, so the caller
                        # must NOT unregister an abandoned worker
                        # (its registration stays live so a later
                        # trip can still cancel it).
                        if _live_handle:
                            try:
                                from . import tool_cancel as _cancel_live_w  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)

                                _cancel_live_w.unregister_live_call(
                                    _live_handle)
                            except Exception:  # noqa: BLE001
                                pass

                _worker = threading.Thread(
                    target=_handler_worker, daemon=True,
                    name=f"vyuk-tool-{ctx.name}")
                _live_worker = _worker
                _worker.start()
                _worker.join(ctx._deadline)
                if _worker.is_alive():
                    # REL-03: abandonment stays abandonment, but
                    # a token-aware worker is now asked to stop
                    # at its next poll point instead of running
                    # to completion unasked. B20: the deadline
                    # cancels with reason "deadline" — a user
                    # stop ("stop") is never overwritten by it,
                    # so a routine sharing one token across
                    # sub-steps can tell a timeout from a stop.
                    if _call_token is not None:
                        try:
                            _call_token.cancel(reason="deadline")
                        except Exception:  # noqa: BLE001
                            pass  # cancelling never breaks a call
                    note_tool_timeout(ctx.name, ctx.args)
                    # Audit A5 (P1-3): the abandoned call gets a
                    # durable operation id and an outcome-unknown
                    # result — never a silent drop, never success
                    # or failure. The worker may still complete;
                    # its late outcome finalizes this op exactly
                    # once (see _handler_worker above).
                    try:
                        from . import tool_operations as _ops  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)
                        from .. import tool_receipts as _tr_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                        _op_id = _ops.record_timeout(
                            ctx.name, "funnel",
                            args_signature=_tr_mod.args_signature(
                                ctx.name, ctx.args
                                if isinstance(ctx.args, dict) else {}))
                    except Exception:  # noqa: BLE001
                        _op_id = None  # ledger failure: id below
                    if _op_id is None:
                        import uuid as _uuid

                        _op_id = _uuid.uuid4().hex
                    _late_outcome: dict[str, Any] | None = None
                    _caller_ends = False
                    with _op_lock:
                        _op_cell["op_id"] = _op_id
                        _late_outcome = _op_cell["late"]
                    # Race window: the handler finished just as
                    # the caller timed out — the worker recorded
                    # "handler_done" and did not end the bracket,
                    # so the caller ends it right here.
                    _caller_ends, _ = _bracket.caller_timed_out()
                    if _caller_ends:
                        _end_bracket_once()
                    if _late_outcome is not None:
                        try:
                            from . import tool_operations as _ops2  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)

                            _ops2.finalize_operation(
                                _op_id, _late_outcome)
                        except Exception:  # noqa: BLE001
                            pass
                    self._log_activity(
                        ctx.name,
                        f"error: timed out after {ctx._deadline:g} "
                        f"second(s) — abandoned, not killed")
                    print(f"[Tools] TIMEOUT: '{ctx.name}' exceeded "
                          f"its {ctx._deadline:g} s deadline and was "
                          f"abandoned (the call could not be "
                          f"killed).")
                    # v5.49 (programme item 38): the gate had
                    # already passed — _audit_gate keeps its
                    # passing value and kind carries the timeout.
                    return ctx._audited({
                        "status": "timeout", "kind": "timeout",
                        "outcome": "unknown",
                        "operation_id": _op_id,
                        "message": (
                            f"'{ctx.name}' timed out after "
                            f"{ctx._deadline:g} second(s) and did "
                            f"not finish. The underlying call "
                            f"could not be killed, so it has "
                            f"been abandoned — it may still "
                            f"complete (or stay wedged) in "
                            f"the background. Nothing is "
                            f"claimed about its result: the "
                            f"outcome is UNKNOWN under "
                            f"operation {_op_id}. Do not "
                            f"automatically retry this call "
                            f"until that operation is "
                            f"reconciled.")})
                if "error" in _outcome:
                    raise _outcome["error"]
                result = _outcome.get("result")
            # Fix 7 (tools loopholes): the outcome seam. A handler
            # may return a core.tool_outcome.Outcome instead of a
            # bare string. Unwrap it to its plain message HERE,
            # before any other handling, so the H18 producer wrap,
            # the dropped-args notes, and the A5 refusal-sentence
            # rules all see exactly the string they saw before —
            # the verdict itself rides in the three locals below to
            # the success return and the Activity line. Plain
            # strings pass through with verified=None and behave
            # exactly as before (no outcome keys appear for them).
            # B13: the kind rides too — the boolean alone
            # collapses a failed landing and an unreadable screen
            # both to False, and the coordinator must tell them
            # apart (a failed landing fails the step; an
            # unverified one stays an honest success).
            _outcome_verified = None
            _outcome_evidence = None
            _outcome_kind = None
            try:
                from .. import tool_outcome as _outcome_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level
                _outcome_kind = _outcome_mod.outcome_kind(result)
                result, _outcome_verified, _outcome_evidence = (
                    _outcome_mod.unwrap_outcome(result))
            except Exception:  # noqa: BLE001 — unwrap never breaks
                pass  # a call; result stays whatever it was
            # v5.45 (H18): untrusted producers are envelope-wrapped
            # + taint-noted at this one seam (see
            # _UNTRUSTED_PRODUCERS), before the context cap.
            result = _wrap_producer_result(ctx.name, result)
            if _dropped:
                # Audit fix Batch 2 (TOOL-02): a read tool that
                # ran with stripped keys surfaces the strip in
                # its result — never a silent success for a
                # call that did not do exactly what was asked.
                # (Effectful tools never reach here: they
                # refused above.) Dict results gain a
                # dropped_args field; string results gain a
                # trailing note the brain can see and repair.
                if isinstance(result, dict):
                    result = {**result, "dropped_args": _dropped}
                elif isinstance(result, str):
                    result = (result + "\n[Note: this call ran "
                              "without undeclared argument(s) "
                              f"{_dropped}, which its handler "
                              f"does not accept.]")
            # Audit A5 (P1-10): a failure payload must never leave
            # the funnel marked success. A handler returning a
            # funnel-shaped dict (status + kind/message from a
            # nested dispatch) is surfaced as that outcome, not
            # re-wrapped; a handler returning a refusal sentence
            # becomes a refused result carrying the sentence.
            # (Data dicts that merely carry a "status" key, and
            # ordinary prose, are untouched.) The refusal-sentence
            # rule NEVER applies to an untrusted producer's output:
            # a file/web/clipboard reader's string is external DATA
            # by definition, so a "Refused:" phrase inside it (or
            # inside its envelope) is content, not a verdict —
            # converting it would destroy the wrapped content the
            # H18 seam just produced (regression: a file whose
            # text begins "Refused: no" must still arrive wrapped).
            try:
                from . import tool_result as _tr  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)
            except Exception:  # noqa: BLE001
                _tr = None  # type: ignore[assignment]
            if _tr is not None and _tr.is_funnel_shaped(result):
                self._log_activity(
                    ctx.name,
                    f"error: surfaced inner "
                    f"{result.get('status')}")
                return ctx._audited(dict(result))
            if (_tr is not None
                    and ctx.name not in _UNTRUSTED_PRODUCERS
                    and _tr.is_refusal_text(result)):
                self._log_activity(ctx.name, "refused: handler refusal")
                _refused_text = str(result)
                if len(_refused_text) > 4000:
                    _refused_text = _refused_text[:4000] + (
                        f"… [truncated, {len(str(result))} chars "
                        f"total]")
                return ctx._audited({
                    "status": "refused", "kind": "refused",
                    "message": _refused_text})
            # Fix 7 (tools loopholes): the outcome verdict rides
            # here, not in the message — the message already
            # carries the honesty in plain words, and these keys
            # are the machine-readable record for receipts, tests,
            # and the Activity line. Plain-string handlers have
            # verified=None and get no keys at all, exactly as
            # before.
            if _outcome_verified is None:
                self._log_activity(ctx.name, "success")
                _success = {"status": "success", "result": result}
            elif _outcome_verified:
                self._log_activity(
                    ctx.name,
                    "success (verified: %s)" % _outcome_evidence)
                _success = {"status": "success", "result": result,
                            "outcome_verified": True,
                            "outcome_evidence": _outcome_evidence,
                            "outcome_kind": _outcome_kind}
            else:
                self._log_activity(
                    ctx.name,
                    "success (unverified: %s)" % _outcome_evidence)
                _success = {"status": "success", "result": result,
                            "outcome_verified": False,
                            "outcome_evidence": _outcome_evidence,
                            "outcome_kind": _outcome_kind}
            if isinstance(result, str) and len(result) > 4000:
                # Protect Gemini's context window: hand back the head of a
                # very long result and say how long it really was. (The
                # activity log above stays a one-line summary regardless.)
                result = result[:4000] + f"… [truncated, {len(result)} chars total]"
                _success["result"] = result
            return ctx._audited(_success)
        except Exception as e:  # noqa: BLE001
            # B20: a handler that stopped via raise_if_cancelled
            # (ToolCancelled) is a CANCELLATION, not a failure —
            # without this it would land below as kind
            # "handler_error", which B12 classifies TRANSIENT and
            # would RETRY — doing the very thing the user just
            # stopped. A cancelled step is never retried.
            try:
                from . import tool_cancel as _cancel_mod2  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)
                _is_cancelled_exc = isinstance(
                    e, _cancel_mod2.ToolCancelled)
            except Exception:  # noqa: BLE001 — the check never breaks a call
                _is_cancelled_exc = False
            if _is_cancelled_exc:
                self._log_activity(ctx.name, "cancelled")
                return ctx._audited({
                    "status": "cancelled", "kind": "cancelled",
                    "message": ("Cancelled at the user's request — "
                                "the tool stopped at its next safe "
                                "point.")})
            self._log_activity(ctx.name, f"error: {e!s}")
            # v5.43 (H8): the message text is byte-identical to the
            # pre-H8 funnel; the `kind` is what brains branch on now
            # instead of string-sniffing "Execution failed: …".
            # Audit A5: the taxonomy covers funnel dicts — a
            # handler RETURNING a refusal sentence no longer flows
            # as a success result (see the success path above);
            # only raised exceptions land here.
            return ctx._audited({"status": "error",
                             "kind": _handler_error_kind(e),
                             "message": f"Execution failed: {e!s}"})
        finally:
            # Audit A5: exactly one bracket end — the caller ends
            # unless the worker owns it (timeout path) or already
            # ended it in the race window.
            if _bracket.caller_finished():
                _end_bracket_once()
            # GAPS-24 (K-V3/K-V8): drop the live-call registration
            # — except on the deadline path while the worker is
            # still alive (abandoned): the worker owns it then
            # and unregisters in its own finally when it lands.
            # Unregister is idempotent, so the completed-worker
            # case (already unregistered above) is a no-op.
            if _live_handle:
                try:
                    if _live_worker is None \
                            or not _live_worker.is_alive():
                        from . import tool_cancel as _cancel_live_f  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)

                        _cancel_live_f.unregister_live_call(_live_handle)
                except Exception:  # noqa: BLE001 — never breaks
                    pass  # a call

    def _exec_dispatch_mcp(self, ctx):
        # GAPS-12: the MCP leg of the ONE dispatch stage — the
        # bus call, ported verbatim in result shape from the old
        # _execute_mcp dispatch tail. The mark-dispatched step
        # and the legs recording already ran as native stages
        # above (_exec_mark_dispatched, _exec_deadline_legs),
        # so this leg records neither; it only brackets the
        # call in the H18 taint counter and calls the bus
        # bounded by the deadline.
        injection.begin_execution()
        try:
            _ok, _text, _failure = ctx._mcp_bus.call_tool_sync(
                ctx._mcp_server, ctx._mcp_tool,
                ctx.args if isinstance(ctx.args, dict) else {},
                timeout=(ctx._deadline
                         if ctx._deadline is not None else 120.0))
        finally:
            injection.end_execution()
        if _ok:
            self._log_activity(ctx.name, "success")
            # Audit A3 (P0-6): MCP success text through the
            # single MCP output seam (same envelope + source
            # as the pre-A3 inline wrap — behaviour unchanged,
            # now routed, so no MCP output path can drift off).
            _text = wrap_mcp_content(ctx._mcp_server, _text)
            if isinstance(_text, str) and len(_text) > 4000:
                _text = _text[:4000] + (
                    f"… [truncated, {len(_text)} chars total]")
            return ctx._audited({"status": "success", "result": _text})
        self._log_activity(ctx.name, f"error: {_failure}: {_text}")
        if _failure == "timeout":
            note_tool_timeout(ctx.name, ctx.args)
            # Audit A5 (P1-3): same durable unknown outcome as the
            # native path. No late-completion channel exists for
            # a bus timeout (the call returned), so this operation
            # stays "unknown" until a human reconciles it — the
            # record, not a silent drop, is the point.
            try:
                from . import tool_operations as _ops  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)
                from .. import tool_receipts as _tr_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                _mcp_op = _ops.record_timeout(
                    ctx.name, "funnel-mcp",
                    args_signature=_tr_mod.args_signature(
                        ctx.name, ctx.args
                        if isinstance(ctx.args, dict) else {}))
            except Exception:  # noqa: BLE001
                import uuid as _uuid

                _mcp_op = _uuid.uuid4().hex
            _seconds = (ctx._deadline
                        if ctx._deadline is not None else 120.0)
            return ctx._audited({
                "status": "timeout", "kind": "timeout",
                "outcome": "unknown",
                "operation_id": _mcp_op,
                "message": (
                    f"'{ctx.name}' timed out after {_seconds:g} "
                    f"second(s) and did not finish. The request "
                    f"to the toolbox server was cancelled — the "
                    f"server may still complete it in the "
                    f"background. Nothing is claimed about its "
                    f"result: the outcome is UNKNOWN under "
                    f"operation {_mcp_op}. Do not automatically "
                    f"retry this call until that operation is "
                    f"reconciled.")})
        if _failure == "unavailable":
            return ctx._audited({"status": "error", "kind": "unavailable",
                             "message": _text})
        return ctx._audited({"status": "error", "kind": "handler_error",
                         "message": _text})

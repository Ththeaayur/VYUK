# tools_registry execute-setup mixin (batch 2 split):
# execute() stages: setup (epoch/audit/telemetry/obs/_audited),
# receipt, mark-dispatched callback, route, toolbox parse, and
# validation. Stage bodies moved verbatim apart from the one
# authorised plumbing change: cross-stage names read/write
# ctx.<name> (see _ExecuteContext). _audit_t0/_telemetry_span/
# _epoch_mod/_epoch_stamp stay setup-method locals: their only
# readers are the setup-defined closures, whose cells stay live.
import time
from typing import Any

from .failure_script import apply_failure_script
from .tools_gate import _FREE_BY_DESIGN, _is_read_class, mcp_tool_class
from .tools_registry_validators import (  # INTENTIONAL (GAPS-12): the MCP
    _validate_args_against_schema,  # validation branch resolves the
    _validate_schema,  # shared validators through this module's globals
)


class _ExecuteContext:
    """Per-call mutable state shared by execute() stage methods.

    Fields (set/mutate/read character proven by symtable walk):
    - name, cancel_token: set by the orchestrator, read-only after.
    - args: set by the orchestrator, rebound by the toolbox stage.
    - audit_gate: set in setup, subscript-mutated by later stages
      (never rebound).
    - audited: the _audited closure object itself, set in setup,
      read-only after (never rebuilt from copied values).
    - epoch_void: the _epoch_void closure object itself, set in
      setup, read-only after.
    - obs_failures: set in setup ([]), appended by receipt and
      receipt-check stages (never rebound).
    - obs_sink_failures: the FACADE's _OBS_SINK_FAILURES dict
      object, stored by the orchestrator (same object, so
      mutations stay globally visible; a module-level import
      here would be circular).
    - receipt_attempt, receipt_error, receipt_is_read: set in the
      receipt stage, read-only after.
    - receipt_dispatched: set False in the receipt stage, set True
      by the mark stage ONLY when try_mark_dispatched succeeds
      (F-F502, 2026-11-21) — it means exactly "a
      {"type": "dispatched"} record precedes this call's outcome
      in the file". _audited reads it when recording the outcome,
      so a never-dispatched outcome records dispatched False
      instead of inheriting the store's dispatched=True default.
      Reads never mark, so their outcomes read False too, matching
      their attempt rows (which already say dispatched False).
    - mark_dispatched_cb: set in the mark_cb stage, read-only after.
    - human_approved_this_call: set in the confirm stage, read in
      the rule-of-two stage.
    - rot_legs: set in the rule-of-two stage, read in the
      deadline-legs stage.
    - deadline: set in the deadline-legs stage, read in dispatch.
    - _mcp_entry, _mcp_server, _mcp_tool, _mcp_class, _mcp_bus:
      set in the route stage for mcp__ names (None otherwise);
      read by the validate / failsafe / kill-chain / confirm /
      rule-of-two / limits / dispatch stages. The MCP entry is
      resolved once, here — no stage re-resolves it.
    """


class ToolRegistryExecSetupMixin:
    def _exec_setup(self, ctx):
        # v5.48 (programme item 20): CANCEL-ON-DISCONNECT EPOCH —
        # stamp the session epoch at dispatch, before any
        # confirmation ask. If the epoch moves while this call
        # waits (the session disconnected / was swapped mid-ask),
        # the approval that lands afterwards belongs to a dead
        # session and is void: the checks after the gate and
        # immediately before the handler refuse the call instead
        # of running it. Lazy + guarded, like the failsafe consult
        # below. GAPS-13: an unimportable epoch module reads as
        # VOID, not as "no stamp" — a gate that cannot be
        # consulted refuses (all four _epoch_void() call sites
        # already refuse on True), never silently passes as
        # before. In a healthy tree the module is always
        # importable (stdlib only, no imports from core), so
        # this fires only on tree breakage.
        _epoch_mod = None
        _epoch_stamp = None
        try:
            from .. import session_epoch as _epoch_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _epoch_stamp = _epoch_mod.current_epoch()
        except Exception:  # noqa: BLE001 — see the note above
            _epoch_mod = None
            _epoch_stamp = None

        def _epoch_void() -> bool:
            # GAPS-13: unimportable module, missing stamp, or a
            # current_epoch() that raises all read as VOID — the
            # session cannot be proven unchanged, so the approval
            # is void. Never a silent pass.
            if _epoch_mod is None or _epoch_stamp is None:
                return True
            try:
                return _epoch_mod.current_epoch() != _epoch_stamp
            except Exception:  # noqa: BLE001 — see above
                return True
        ctx._epoch_void = _epoch_void

        # v5.49 (programme item 38): AUDIT LOG — exactly one call
        # site (the _audited closure below, through which every
        # return in this method passes) writes one redacted JSON
        # record per execute() call into the shared hash chain
        # (records.jsonl, kind "audit") via core/audit.write_record
        # (GAPS-27, M-V2: the chain is the one surviving audit
        # stream; the funnel's old second write — a subset "audit"
        # event appended beside it — is gone). The closure
        # returns its result dict UNCHANGED — the funnel's result
        # bytes are exactly as before — and audit.write_record
        # itself never raises; the extra guard here keeps even a
        # broken audit import from touching a tool call. The gate
        # state rides a small mutable dict the gate/refusal
        # branches below update as the call's disposition firms
        # up: it starts at the free classification, a confirm ask
        # overwrites it with the human's decision (and joins the
        # confirm_gate request ID), and an always-refuse or
        # executor-limit refusal that stops the handler running
        # overwrites it with refused:<reason-class>.
        _audit_t0 = time.monotonic()
        ctx._audit_gate: dict[str, Any] = {
            "value": (f"free-by-design:{_FREE_BY_DESIGN[ctx.name]}"
                      if ctx.name in _FREE_BY_DESIGN else "free"),
            "request_id": None,
        }

        # v5.56 (Wave 2, OB-1): tool-span start. The span is
        # finished inside _audited below, so MCP calls (which
        # return through _audited) are spanned too. Guarded,
        # fail-open: without the SDK the funnel behaves exactly
        # as before (start_tool_span returns None).
        _telemetry_span = None
        try:
            from .. import telemetry as _telemetry_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _telemetry_span = _telemetry_mod.start_tool_span(ctx.name)
        except Exception:  # noqa: BLE001 — telemetry never breaks a call
            _telemetry_span = None

        # Audit fix Batch 3 (OBS-01): per-call observability
        # sink failures. A broken sink never refuses the call,
        # but the result carries observability="degraded" plus
        # the failing sink names, and _OBS_SINK_FAILURES keeps
        # the sticky process-level count. Additive only, and
        # outside `message`, so brains parsing results are
        # unaffected; on a clean call the result is byte-identical
        # to before.
        ctx._obs_failures: list[str] = []

        def _note_obs_failure(sink: str) -> None:
            ctx._obs_failures.append(sink)
            try:
                ctx.obs_sink_failures[sink] = \
                    ctx.obs_sink_failures.get(sink, 0) + 1
            except Exception:  # noqa: BLE001 — bookkeeping never
                pass  # breaks a call

        def _audited(result):
            # Audit A5 (P1-10): the structured-result contract is
            # enforced HERE, at the one closure every return in
            # this method passes through. Legacy "error" results
            # normalize by kind (refusal kinds -> "refused",
            # failure kinds -> "failure"); results already in the
            # vocabulary (including genuine "timeout" results with
            # their operation_id) pass through untouched; successes
            # pass through untouched. Kind is never rewritten, so
            # every kind-branching consumer below keeps working.
            # Normalization runs FIRST so the failure script, the
            # milestone check, telemetry, and the audit log all see
            # the contract status.
            try:
                from . import tool_result as _tr  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)

                result = _tr.normalize_result(result)
            except Exception:  # noqa: BLE001 — see below
                _note_obs_failure("result_contract")
                pass
            # v5.51 (H-1): THE failure script hooks in here — the
            # one closure every return in this method passes
            # through, so exactly one formatter application reaches
            # every brain. apply_failure_script is identity for
            # successes, read-class failures, unknown tools and
            # the pinned gate/validation kinds (not_confirmed /
            # refused / validation keep their exact wording); an
            # action-tool failure gains result["failure"] =
            # {"tried", "next"} and a one-line human version
            # appended to its `message` (the field brains speak).
            # A broken formatter must never break a tool call, so
            # a failure here leaves the result exactly as it was.
            try:
                result = apply_failure_script(
                    ctx.name, ctx.args if isinstance(ctx.args, dict) else {},
                    result,
                    is_read=_is_read_class(
                        ctx.name, ctx.args if isinstance(ctx.args, dict) else None),
                    known=ctx.name in self._tools)
            except Exception:  # noqa: BLE001 — see the note above
                _note_obs_failure("failure_script")
                pass
            # v5.54 (Wave 0): the milestone log's first completed
            # task — the first execute() that returns a success
            # through this funnel (core/milestones, WF-55's
            # recording dependency). record_once keeps it to the
            # first one ever; the guard keeps a broken log from
            # ever touching a tool result.
            try:
                if isinstance(result, dict) \
                        and result.get("status") == "success":
                    from .. import milestones as _milestones  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                    _milestones.record_once(
                        "first_task_completed", tool=ctx.name)
            except Exception:  # noqa: BLE001 — see the note above
                _note_obs_failure("milestones")
                pass
            # v5.56 (Wave 2, OB-1 / NO-33 / SE-6.10 / PF-2): the
            # funnel's evidence fan-out, on the same result the
            # audit log records below. The telemetry span
            # finishes on delivery evidence (never intent); the
            # health monitor learns delivered vs refused; the
            # hash chain gains the funnel audit event; the human
            # activity feed gains one plain-language line per
            # tool call. Every step guarded: a broken evidence
            # path never touches the result. Funnel args pass
            # through audit.redact_args first (record_family's
            # own redaction is only a backstop); if the audit
            # module itself is unimportable the chain append is
            # skipped rather than writing unredacted args.
            # Activity lines are NOT PF-2 undo objects (a tool
            # call already executed cannot be un-executed from
            # the feed), so they carry reversible=False and the
            # surface marks them irreversible; PF-2 undo lives
            # on registered actions (record_family.register_undo),
            # whose buttons the surface drives separately.
            try:
                from .. import telemetry as _telemetry_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                _telemetry_mod.finish_tool_span(_telemetry_span, result)
                _telemetry_mod.feed_funnel_result(ctx.name, result)
            except Exception:  # noqa: BLE001 — see the note above
                _note_obs_failure("telemetry")
                pass
            try:
                from .. import record_family as _rf  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                _rf_kind = result.get("kind") \
                    if isinstance(result, dict) else None
                if _rf_kind is None:
                    _rf_kind = "success" \
                        if isinstance(result, dict) \
                        and result.get("status") == "success" else ""
                # GAPS-27 (M-V2): the funnel no longer appends its
                # own subset "audit" event to the chain here — that
                # was the second of two writes for one execute()
                # call (the subset payload: tool/gate/kind/ms/args).
                # The one surviving audit write is
                # audit.write_record(...) below, which routes the
                # FULL record into the same chain (kind "audit").
                _outcome_words = {
                    "refused": "refused",
                    "not_confirmed": "awaiting confirmation",
                    "validation": "rejected (invalid arguments)",
                    "timeout": "timed out",
                    "budget": "held by the safety budget",
                    "unavailable": "unavailable",
                    "handler_error": "failed",
                }
                _outcome = "completed" \
                    if _rf_kind == "success" \
                    else _outcome_words.get(_rf_kind, "failed")
                # PF-18 widget batch: a permission / folder-scope
                # refusal names the permission and the Settings
                # direction in the Activity line, so GUI, funnel,
                # ledger and Activity agree. Other refused kinds
                # keep the generic line. The label map is local:
                # the funnel cannot import the GUI's PERMISSION_DEFS.
                _activity_text = f"Tool {ctx.name}: {_outcome}"
                _gate_value = ""
                try:
                    _gate_value = ctx._audit_gate.get("value", "")
                except Exception:  # noqa: BLE001 — generic line stands
                    _gate_value = ""
                if _gate_value.startswith("refused:permission-"):
                    _pid = _gate_value.split(
                        "refused:permission-", 1)[1]
                    _label = {
                        "microphone": "Microphone",
                        "camera": "Camera",
                        "screen": "Screen",
                        "files": "Files",
                        "browser": "Browser",
                        "home_devices": "Home devices",
                    }.get(_pid, _pid)
                    # F-F301: the Activity line must carry the same
                    # true cause as the funnel message — a permission
                    # never decided is off by default, not switched
                    # off by the user. A failed consult falls back
                    # to the denied wording (this is an observability
                    # line; it must never break the audit path).
                    _never = False
                    try:
                        from .. import permission_grants as _pg2
                        _never = bool(
                            _pg2.permission_never_decided(_pid))
                    except Exception:  # noqa: BLE001 — denied wording
                        _never = False
                    if _never:
                        _activity_text = (
                            f"Tool {ctx.name}: refused — {_label} has "
                            f"never been granted, so nothing was run. "
                            f"Switch {_label.lower()} on in Settings "
                            f"to use it again.")
                    else:
                        _activity_text = (
                            f"Tool {ctx.name}: refused — {_label} is "
                            f"switched off in Settings, so nothing was "
                            f"run. Switch {_label.lower()} back on in "
                            f"Settings to use it again.")
                elif _gate_value == "refused:folder-scope":
                    _activity_text = (
                        f"Tool {ctx.name}: refused — outside the "
                        f"folders you allow in Settings > "
                        f"Permissions.")
                elif _gate_value == "refused:stale-view":
                    # Live staleness stage, P4 (2026-11-05): a
                    # stale-view refusal names the re-observe
                    # direction, so GUI, funnel, ledger and
                    # Activity agree — and the line never reads
                    # "failed" (nothing ran, nothing failed).
                    _activity_text = (
                        f"Tool {ctx.name}: refused — the screen "
                        f"changed since the last shared frame, "
                        f"so nothing was clicked. Re-observe "
                        f"and retry.")
                _rf.log_activity_human(
                    _activity_text, reversible=False)
            except Exception:  # noqa: BLE001 — see the note above
                _note_obs_failure("record_family")
                pass
            try:
                from .. import audit as _audit_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                _kind = result.get("kind") \
                    if isinstance(result, dict) else None
                if _kind is None:
                    _kind = "success" if isinstance(result, dict) \
                        and result.get("status") == "success" else ""
                _audit_mod.write_record(
                    ctx.name,
                    ctx.args if isinstance(ctx.args, dict) else {},
                    gate=ctx._audit_gate["value"], kind=_kind,
                    ms=(time.monotonic() - _audit_t0) * 1000.0,
                    epoch=_epoch_stamp,
                    request_id=ctx._audit_gate["request_id"])
            except Exception:  # noqa: BLE001 — audit never breaks
                _note_obs_failure("audit")
                pass  # a tool call (write_record already never raises)
            # Phase 2 (audit 4 C10): durable receipt outcome.
            # This closure is the one point every return in
            # execute() passes through, so the outcome recorded
            # here reflects the FINAL result (after
            # normalization, the failure script, and the audit
            # fan-out), never an intermediate. A receipt
            # failure never refuses the call — but Batch 3
            # (OBS-01) surfaces it on the result (below) instead
            # of swallowing it with a bare pass.
            try:
                from .. import tool_receipts as _receipts_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                if ctx._receipt_attempt is None and ctx._receipt_error:
                    # The attempt itself failed (guard/mandate
                    # refusals, or a read running under a broken
                    # store): no outcome could be recorded, and
                    # that IS an observability failure for this
                    # call — surface it, don't swallow it.
                    _note_obs_failure("receipts")
                if ctx._receipt_attempt is not None:
                    # C2-F2 (2026-11-20): the outcome row records
                    # the ACT's outcome, not the dispatch — a
                    # failed act reads "failed" with the cause
                    # named, never "succeeded" with an empty
                    # summary. The derivation reads the full
                    # result (verdict + result string), not just
                    # the funnel status.
                    _r_status, _r_summary = (
                        _receipts_mod.receipt_outcome_for_result(
                            result,
                            is_read=getattr(ctx, "_receipt_is_read",
                                            False)))
                    # F-F502 (2026-11-21): the flag is the funnel's
                    # own dispatch fact — True only when the mark
                    # stage wrote a {"type": "dispatched"} record
                    # for this call. The store's dispatched=True
                    # default must NOT leak onto outcomes that
                    # never reached dispatch (permission / scope /
                    # validation / confirm-deny / no-handler
                    # refusals, unknown tools, reads): their rows
                    # claimed a dispatch the file has no record of.
                    _outcome_ok = _receipts_mod.try_record_outcome(
                        ctx._receipt_attempt,
                        status=_r_status, summary=_r_summary,
                        dispatched=bool(getattr(
                            ctx, "_receipt_dispatched", False)))
                    if not _outcome_ok:
                        _note_obs_failure("receipts")
            except Exception:  # noqa: BLE001 — receipts never break
                _note_obs_failure("receipts")
                pass  # a tool call
            if ctx._obs_failures and isinstance(result, dict):
                result["observability"] = "degraded"
                result["observability_failures"] = sorted(
                    set(ctx._obs_failures))
            return result
        ctx._audited = _audited

    def _exec_receipt(self, ctx):

        # Phase 2 (audit 4 C10): durable receipt attempt +
        # unknown-outcome guard. Recorded here, after the
        # _audited closure exists and before ANY dispatch (the
        # MCP branch below also returns through _audited, so
        # MCP calls get receipts too). The guard runs FIRST:
        # for a NON-read-only tool, an identical call (same
        # tool + same args, or a normalised re-spelling of it)
        # whose prior attempt never resolved (interrupted,
        # timed out, crashed) is refused WITHOUT dispatching —
        # re-running an unknown-status side effect is exactly
        # what the receipts exist to prevent. Read-only tools
        # never trigger the guard (re-running a read is always
        # safe).
        # Audit fix Batch 3 (REL-01): the guard FAILS CLOSED.
        # try_find_unknown still returns None on a store
        # failure (it never raises), but the funnel now reads
        # the sticky guard failure counter around the call: a
        # failure DURING this lookup refuses a non-read call
        # with an explicit receipt-unavailable message instead
        # of silently disabling the guard for the process (the
        # corrupt-store case). A failure of the attempt record
        # itself is remembered in _receipt_error and enforced
        # at the dispatch point below (after every gate has
        # passed, so a call that would have been refused anyway
        # still gets its real refusal, not a receipt one) and
        # in the MCP branch. Receipts disabled for tests, or a
        # store that simply has no record, never trip either
        # refusal: only a failure counted during THIS call's
        # lookup / attempt write does.
        ctx._receipt_attempt = None
        ctx._receipt_error = False
        # F-F502 (2026-11-21): False until the mark stage proves a
        # dispatched record exists for this call; _audited reads
        # this flag when recording the outcome.
        ctx._receipt_dispatched = False
        ctx._receipt_is_read = _is_read_class(
            ctx.name, ctx.args if isinstance(ctx.args, dict) else None)
        try:
            from .. import tool_receipts as _receipts_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            if not ctx._receipt_is_read:
                _guard_before = _receipts_mod.guard_failure_count()
                _prior_unknown = _receipts_mod.try_find_unknown(
                    tool=ctx.name, args=ctx.args
                    if isinstance(ctx.args, dict) else {})
                if _prior_unknown is not None:
                    _prior_receipt = _prior_unknown.get(
                        "receipt_id", "?")
                    ctx._audit_gate["value"] = "refused:unknown-prior"
                    return ctx._audited({
                        "status": "refused", "kind": "refused",
                        "message": (
                            f"Refused: a previous identical call to "
                            f"'{ctx.name}' has an unknown outcome "
                            f"(receipt {_prior_receipt}; interrupted, "
                            f"timed out, or crashed) "
                            f"— verify the action's effect before "
                            f"retrying; the call was not "
                            f"re-executed.")})
                if _receipts_mod.guard_failure_count() > _guard_before:
                    ctx._audit_gate["value"] = "refused:receipt-unavailable"
                    ctx._obs_failures.append("receipts")
                    return ctx._audited({
                        "status": "refused", "kind": "refused",
                        "message": (
                            f"Refused: the tool-receipt store could "
                            f"not be read, so a previous "
                            f"unknown-outcome call to '{ctx.name}' "
                            f"cannot be ruled out — verify the "
                            f"receipt store (data/tool_receipts.jsonl) "
                            f"before retrying; the call was not "
                            f"re-executed.")})
            # §7 consumers batch 3: thread handle/stamp evidence
            # into the receipt attempt. Explicit args win; else
            # the task's threaded pair (evidence only — never a
            # handler arg, never authorization, never a
            # confirmation). Guarded: threading never breaks a
            # call, so a failure here degrades to no evidence.
            _thread_handle = None
            _thread_stamp = None
            try:
                from ..desktop import s7_threading as _s7t  # Phase 3 S7a-2: moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

                _thread_handle, _thread_stamp = \
                    _s7t.resolve_for_execute(
                        ctx.args if isinstance(ctx.args, dict) else {})
            except Exception:  # noqa: BLE001 — see above
                _thread_handle, _thread_stamp = None, None
            _attempt_before = _receipts_mod.attempt_failure_count()
            ctx._receipt_attempt = _receipts_mod.try_record_attempt(
                tool=ctx.name, args=ctx.args if isinstance(ctx.args, dict) else {},
                handle_id=_thread_handle, snapshot_stamp=_thread_stamp)
            ctx._receipt_error = (
                _receipts_mod.attempt_failure_count() > _attempt_before)
        except Exception:  # noqa: BLE001 — receipts never break
            ctx._receipt_attempt = None  # a tool call
            # Only an unexpected failure of the receipt machinery
            # itself (e.g. the module unimportable) reaches here
            # — the helpers never raise — and for a non-read
            # call that is itself a fail-closed condition.
            ctx._receipt_error = not ctx._receipt_is_read

    def _exec_mark_cb(self, ctx):

        # v5.55 (Wave 1, HE-1): MCP tools never enter self._tools
        # (INV-1: the fitted registry stays 480) — their names
        # are namespaced "mcp__<server>__<tool>" and run the
        # same native stages as every other call (resolved in
        # _exec_route below, dispatched through the bus in
        # _exec_dispatch) instead of a native handler.
        # Regression batch (R2): the MCP leg dispatches
        # through the bus, so it marks dispatched through this
        # callback instead of the native choke point below.
        # None for reads or when no attempt was recorded (the
        # MCP leg then skips marking, like the native path
        # skips it for reads).
        ctx._mark_dispatched_cb = None
        if not ctx._receipt_is_read and ctx._receipt_attempt is not None:
            def _mark_dispatched_cb(
                    _attempt=ctx._receipt_attempt) -> bool:
                try:
                    from .. import tool_receipts as _md_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                    return _md_mod.try_mark_dispatched(_attempt)
                except Exception:
                    return False
            ctx._mark_dispatched_cb = _mark_dispatched_cb

    def _exec_route(self, ctx):

        if ctx.name.startswith("mcp__"):
            # GAPS-12 (flaw #10): the MCP branch is ONE funnel
            # now, not a second one. This stage only RESOLVES
            # the MCP entry and stashes it on ctx — it does NOT
            # dispatch. The native stages below (validation,
            # failsafe, kill-chain, confirm, Rule of Two,
            # limits, receipt, deadline, dispatch) then run
            # for the MCP call exactly as for a native one,
            # each branching on ctx._mcp_entry where the stage
            # genuinely differs (MCP schema, MCP read class,
            # MCP confirm predicate, MCP legs, bus dispatch).
            # The old _execute_mcp (a ~430-line parallel copy
            # of the stage chain) is deleted; there is no
            # second funnel to drift.
            try:
                from .. import mcp_client as _mcp_mod  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                _bus = _mcp_mod.get_bus()
            except Exception:  # noqa: BLE001 — no bus, no MCP tools
                _bus = None
            _entry = _bus.resolve(ctx.name) if _bus is not None else None
            if _entry is None and _bus is not None and "__" in ctx.name[5:]:
                # Server never connected this process (or its tools
                # vanished when it died): one bounded connect
                # attempt, then re-resolve. A dead server answers
                # immediately from cached dead state — no retry
                # storm, and no silent second chance.
                _server = ctx.name[5:].split("__", 1)[0]
                _ok, _err = _bus.ensure_server(_server)
                if _ok:
                    _entry = _bus.resolve(ctx.name)
            if _entry is None:
                self._log_activity(ctx.name, "error: unknown MCP tool")
                ctx._audit_gate["value"] = "refused:unknown-tool"
                return ctx._audited({
                    "status": "error", "kind": "unavailable",
                    "message": (
                        f"Unknown toolbox tool: {ctx.name} — its server "
                        "isn't connected, or the tool doesn't exist. "
                        "Ask use_toolbox to find it again.")})
            ctx._mcp_bus = _bus
            ctx._mcp_entry = _entry
            ctx._mcp_server = _entry["server"]
            ctx._mcp_tool = _entry["tool"]
            ctx._mcp_class = mcp_tool_class(_entry)
            return None
        if ctx.name not in self._tools:
            self._log_activity(ctx.name, "error: unknown tool")
            # v5.43 (H8): every error dict this funnel returns carries
            # a machine-readable `kind` (see _handler_error_kind and
            # the returns below); `message` texts are unchanged.
            ctx._audit_gate["value"] = "refused:unknown-tool"  # v5.49 (item 38)
            return ctx._audited({"status": "error", "kind": "unavailable",
                             "message": f"Unknown tool: {ctx.name}"})

    def _exec_toolbox(self, ctx):
        # Dispatcher tolerance (v5.33): use_toolbox relays an
        # argument bundle for another tool, and Live models
        # persistently serialize that bundle as a JSON string
        # instead of an object (router proof run 4: the call
        # was rejected twice for exactly this, even after the
        # model diagnosed its own mistake). Parse a string
        # bundle back into an object BEFORE validation; an
        # unparseable string falls through and validation
        # rejects it with the usual exact error. Scoped to
        # the dispatcher — every real tool keeps its strict
        # schema.
        if ctx.name == "use_toolbox" and isinstance(ctx.args, dict):
            bundle = ctx.args.get("args")
            if isinstance(bundle, str):
                import json as _json

                try:
                    parsed = _json.loads(bundle)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    ctx.args = {**ctx.args, "args": parsed}

    def _exec_validate(self, ctx):
        if ctx._mcp_entry is not None:
            # GAPS-12: MCP validation runs in the SHARED validate
            # stage now (the old _execute_mcp copy is deleted).
            # The schema is the MCP tool's own remote inputSchema
            # — untrusted content by definition — so its shape is
            # judged FIRST (L2 LOG-04/LOG-05): a malformed remote
            # inputSchema is an invalid_schema refusal, never a
            # dispatch and never an exception escaping execute().
            # The guard below is the second line of defence: any
            # unexpected validation exception becomes the same
            # structured refusal.
            _entry = ctx._mcp_entry
            _schema_block = {
                "name": ctx.name,
                "description": _entry.get("description", ""),
                "parameters": _entry.get("parameters") or {},
            }
            try:
                _schema_problem = _validate_schema(
                    ctx.name, "", _entry.get("parameters") or {})
                _problem = (None if _schema_problem is not None
                            else _validate_args_against_schema(
                                ctx.name, ctx.args,
                                _entry.get("parameters") or {}))
            except Exception as e:  # noqa: BLE001 — guard, see above
                self._log_activity(
                    ctx.name, f"error: schema validation failed "
                          f"({type(e).__name__}: {str(e)[:200]})")
                ctx._audit_gate["value"] = "refused:invalid-schema"
                return ctx._audited({"status": "refused",
                                     "kind": "invalid_schema",
                                     "message": (
                                         f"Invalid schema for tool "
                                         f"'{ctx.name}': the tool's "
                                         "schema could not be "
                                         "validated — nothing was run."),
                                     "validation_error": True,
                                     "schema": _schema_block})
            if _schema_problem is not None:
                self._log_activity(ctx.name, f"error: {_schema_problem}")
                ctx._audit_gate["value"] = "refused:invalid-schema"
                return ctx._audited({"status": "refused",
                                     "kind": "invalid_schema",
                                     "message": _schema_problem,
                                     "validation_error": True,
                                     "schema": _schema_block})
            if _problem is not None:
                self._log_activity(ctx.name, f"error: {_problem}")
                ctx._audit_gate["value"] = "refused:validation"
                return ctx._audited({"status": "error", "kind": "validation",
                                     "message": _problem,
                                     "validation_error": True,
                                     "schema": _schema_block})
            return None
        problem = self.validate_args(ctx.name, ctx.args)
        if problem is not None:
            self._log_activity(ctx.name, f"error: {problem}")
            ctx._audit_gate["value"] = "refused:validation"  # v5.49 (item 38)
            # v5.50 (programme item 45): argument-validation
            # self-repair — the error also carries THIS tool's own
            # full declaration under "schema" (that one tool only,
            # so the result stays bounded), letting the calling
            # brain correct its arguments in one round instead of
            # spending a separate describe/fetch round. Validation
            # errors ONLY: a handler runtime error means the
            # arguments were fine, so no schema rides those (or
            # any other) error kinds. "message" stays the
            # validator's exact text, byte-for-byte.
            _tool = self._tools[ctx.name]
            return ctx._audited({"status": "error", "kind": "validation",
                             "message": problem,
                             "validation_error": True,
                             "schema": {
                                 "name": ctx.name,
                                 "description": _tool["description"],
                                 "parameters": _tool["parameters"],
                             }})

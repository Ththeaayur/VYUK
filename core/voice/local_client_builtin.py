# Batch 4 (plan §3.3): builtin-turn (_brain_turn_builtin, 774 lines — §5 Q2), moved verbatim
# from core/voice/local_client.py. INTENTIONAL: imports below are the
# subset of the old module's import block that this group's code uses.
import asyncio
from typing import Any
from ..config import config
from .. import config_spec
from ..funnel.tools_registry import tools
from ..memory.memory import memory_store
from ..brain import planner
from ..languages import reply_language_directive
from . import local_assets
from .local_client_guards import (HISTORY_CAP, HISTORY_WINDOW, MAX_TOOL_REPAIRS,
    TOOL_SHAPE_FALLBACK, _max_tool_rounds, _memory_block,
    _state_guard_for, _tool_call_signature, _tool_progress_report)
from .local_client_vlm import _executed_tool_succeeded, _feed_text, _spoken_fallback
from .local_client_toolparse import (_closest_tool_hints, _extract_tool_call,
    _extract_tool_calls, _invented_tool_name, _looks_like_tool_attempt, _strip_think)
from .local_client_budgets import (_DeliberationBudget, _RepairBudget,
    _est_tokens, _tool_line)
from .local_client_render import _render_tool_protocol
from .local_client_classifier import entry_profile, escalate_turn_profile


class LocalLiveSessionBuiltinMixin:

    async def _brain_turn_builtin(self, user_text: str):
        """Runs one built-in (llama.cpp) chat turn.

        The GGUF chat template is not tool-aware, so tool use follows
        the JSON-block protocol from _render_tool_protocol(): each
        reply is either a tool call (executed through the same
        tools.execute() as every other engine, result fed back as a
        user message — the chat-template-safe channel) or plain speech.
        A reply that *tries* to be a tool call but doesn't parse — or
        one whose arguments tools.execute() rejects on schema
        validation — earns a repair attempt: the exact error is fed
        back and the model re-emits the call. Repairs are capped at
        MAX_TOOL_REPAIRS per turn (one shared _RepairBudget); past
        the cap the turn ends with TOOL_SHAPE_FALLBACK instead of
        looping."""
        user_msg = {"role": "user", "content": user_text}
        self._history.append(user_msg)
        self._history = self._history[-HISTORY_CAP:]

        # Context budget (v5.19): everything in this turn — persona,
        # memories, protocol, history — must fit the model's window
        # minus the reply (512 tokens) and a safety margin (128).
        # See the section comment above _est_tokens for the overflow
        # this fixes (the legacy protocol alone was ~11,400 tokens).
        total_budget = local_assets.effective_n_ctx() - 512 - 128
        system = config.system_instruction
        # v5.41: non-English home directive ("" for English); it is
        # part of `system` before every budget measurement below.
        system += reply_language_directive()
        # Conversion Wave 8: prompt-cache-first stable prefix
        # note (persona + directive, SHA-256 key recorded on the
        # session) + PA-3 speculative flag consultation with the
        # measurement harness attached. The speculative gate is
        # consulted, never acted on blindly: flags are OFF by
        # default, no llama.cpp runtime exists here, and the
        # harness reports NO-DATA — generation proceeds
        # non-speculatively and the plan's 1.2-1.6x figure is
        # never quoted as measured. Neither call may break a turn.
        try:
            from .. import w8_wiring as _w8  # Phase 3 V1b: moved to core/voice/

            _cached = _w8.note_stable_prefix(
                [config.system_instruction,
                 reply_language_directive()])
            self._w8_cache_key = _cached.cache_key
        except Exception:  # noqa: BLE001 — cache key is advisory
            pass
        try:
            from . import local_assets as _assets  # Phase 3 V2: local_assets now in core/voice/; V1b interim closed
            from .. import w8_wiring as _w8spec  # Phase 3 V1b: moved to core/voice/

            _tier = str((_assets.effective_model() or {}).get("tier")
                        or "balanced")
            self._w8_speculative_gate = \
                _w8spec.speculative_generation_gate(_tier)
        except Exception:  # noqa: BLE001 — speculative gate is advisory
            pass
        memories = _memory_block(user_text)
        if memories and _est_tokens(memories, model=self._llama) > 0.45 * total_budget:
            # A memory-rich user must not crowd the brain out of
            # its own prompt: cap the block at ~45% of the budget,
            # cutting on a line boundary (head kept — identity
            # facts are stored first).
            cap_chars = int(0.45 * total_budget * 3.2)
            cut = memories.rfind("\n", 0, cap_chars)
            memories = memories[:cut if cut > 0 else cap_chars]
            memories += "\n- … (further memories trimmed for space)"
        if memories:
            system += f"\n\n{memories}"
        # Ambient context (v5.44, N4): the stashed packet folds in
        # at the memory block's place, one-shot (a refresh via
        # set_ambient_context re-stashes it for a later turn).
        ambient_block = self._take_ambient_block()
        if ambient_block:
            system += f"\n\n{ambient_block}"
        # Continuity (v5.43, B14): the episodes day-render rides
        # the FIRST turn's assembly only — taken here, before the
        # protocol budget is measured, so it pays its own way in
        # the budget like every other block. "" later / when empty.
        day_block = self._take_continuity_day_block()
        if day_block:
            system += f"\n\n{day_block}"
        # Turn-truthfulness (6th-B08): the previous turn's action
        # trace rides the system prompt, so the model answers a
        # later "what did you do?" from what actually ran.
        action_note = self._previous_turn_action_note()
        if action_note:
            system += f"\n\n{action_note}"
        system += (" You are running on a local model: be extra concise, "
                   "prefer one tool call at a time. Answer in one or two "
                   "short spoken sentences. After a tool runs, say what "
                   "happened in plain words — never read out raw output, "
                   "JSON, code, or long lists; summarise them. If you "
                   "cannot do something, say so plainly and offer the "
                   "closest thing you can do.")
        # The protocol renders to whatever budget the persona and
        # memories leave AFTER two reserves (v5.19 audit fix): the
        # history floor below (the last two exchanges + the current
        # message, ~192 tokens) is mandatory, so it is reserved
        # up front rather than discovered as an overrun; and a
        # working allowance (a quarter of the budget, capped at
        # 480) stays free for the turn's own traffic — tool-call
        # and tool-result messages appended as rounds execute.
        # The history window is then trimmed oldest-first until
        # the assembled messages fit the assembly target (total
        # minus the working allowance). The trim touches only
        # this turn's window copy (self._history keeps everything)
        # and always keeps the last two exchanges.
        turn_allowance = min(480, total_budget // 4)
        assembly_target = total_budget - turn_allowance
        protocol_budget = max(
            300, assembly_target - _est_tokens(system, model=self._llama) - 192)
        system += _render_tool_protocol(protocol_budget,
                                        query=user_text,
                                        model=self._llama)

        window = self._history[-(HISTORY_WINDOW + 1):-1]
        messages = (
            [{"role": "system", "content": system}]
            + window
            + [user_msg]
        )
        while (len(window) > 2
               and _est_tokens("".join(m["content"] for m in messages),
                               model=self._llama)
               > assembly_target):
            window.pop(0)
            messages.pop(1)

        loop = asyncio.get_running_loop()
        if self._llama is None:
            # The brain was idle-unloaded (v3.2) — reload it here,
            # inside the turn lock the caller holds, so speech never
            # meets a half-loaded model. A reload failure is reported
            # exactly like a failed first load.
            if self._llama_unloaded:
                self._notify_status("SETUP: Waking my brain back up…")
                self._llama_unloaded = False
            try:
                await loop.run_in_executor(None, self._ensure_llama)
            except Exception as e:  # noqa: BLE001 — no model, no turn
                print(f"[LocalBrain] Brain reload failed: {e}")
                if self._history and self._history[-1] is user_msg:
                    self._history.pop()
                self._notify_status(f"ERROR: {str(e)[:300]}")
                self._notify_status("LISTENING")
                return
        final_text = ""
        tools_used = False  # brain-guard evidence (v5.3): did ANY tool run?
        # Turn-truthfulness (6th-B06): attempted is not succeeded.
        # tools_used says a tool ran; tools_succeeded says one
        # returned a success result. State-question guards and the
        # "Done." fallback consult tools_succeeded, so a failed
        # tool can never stand in for fresh evidence.
        tools_succeeded = False
        # Turn-truthfulness (6th-B08): this turn's action records
        # (tool, status, outcome snippet), stored at turn end.
        turn_actions: list[dict[str, Any]] = []
        _delib_budget = _DeliberationBudget()  # Phase 2 B09:
        # the deliberation budget is INDEPENDENT of the tool
        # rounds and of _RepairBudget — a repair retry never
        # consumes a tool round, and tool rounds never starve
        # repairs.
        last_result_text: str | None = None
        last_result = None  # v5.51 (M-4): the dict behind the text
        # v5.55 (Wave 1, SC-1): grammar-shortlist CHECK mode,
        # opt-in ONLY via VYUK_GRAMMAR_SHORTLIST=1 (default OFF
        # — the default path is byte-unchanged). When on, this
        # turn's routed shortlist is compiled to a Guidance
        # program up front; any build failure falls back to
        # today's path for the whole turn, logged. The per-round
        # check itself lives beside the parse below: a
        # grammar-valid shortlist call is used directly, and
        # anything else falls back to today's parse, logged.
        # The check never rejects a call today's path would
        # have run — sampling THROUGH the grammar is the
        # remaining integration (see grammar_shortlist.py).
        _grammar_schema = None
        try:
            if config_spec.explicit("VYUK_GRAMMAR_SHORTLIST") == "1":
                from . import grammar_shortlist as _gs_setup

                _gs_names = _gs_setup.shortlist_for_turn(tools, user_text)
                _grammar_schema = _gs_setup.shortlist_schema(
                    tools, _gs_names)
                if _gs_setup.guidance_json_program(
                        _grammar_schema) is None:
                    print("[Grammar] Guidance unavailable — "
                          "today's path for this turn")
                    _grammar_schema = None
        except Exception as _gs_exc:  # noqa: BLE001 — the hook
            print(f"[Grammar] shortlist build failed "  # never breaks
                  f"({type(_gs_exc).__name__}) — today's path "  # a turn
                  "for this turn")
            _grammar_schema = None
        # No-progress breaker (v5.44, B7): the same tool+args
        # twice in a row ends the turn with a progress report.
        prev_signature: str | None = None
        accomplished: list[str] = []
        try:
            budget = _RepairBudget()
            for _round in range(_max_tool_rounds()):
                # Phase 2 (audit 6 B07): re-fit context before
                # EVERY model call, against the same total
                # budget the assembly used. Tool results and
                # repair prompts accumulate round after round;
                # without this, a long result plus several
                # repairs overflows the window the assembly
                # promised to respect. The system message and
                # the current instruction are never dropped;
                # tool-result messages drop whole (B04 group
                # rule), freshest last. A refit failure leaves
                # the messages as they were — never a broken
                # turn for a budgeting fault.
                try:
                    from .. import context_refit as _refit  # Phase 3 V1b: moved to core/voice/

                    messages, _refit_report = _refit.refit_messages(
                        messages, budget_tokens=total_budget,
                        estimate=lambda t: _est_tokens(
                            t, model=self._llama))
                except Exception:  # noqa: BLE001 — never break a turn
                    pass
                reply_text = await loop.run_in_executor(
                    None, self._llama_chat, messages
                )
                # v5.55 (Wave 1, SC-1): judge this reply against
                # the turn's shortlist grammar (check mode — see
                # the setup above). _grammar_call is the
                # grammar-valid call, or None; the lone-parse
                # line below prefers it and logs the fallback
                # when today's parse finds a call the grammar
                # would not have emitted.
                _grammar_call = None
                _grammar_checked = False
                if _grammar_schema is not None:
                    try:
                        from . import grammar_shortlist as _gs_check

                        _grammar_call = _gs_check.constrained_parse(
                            reply_text, _grammar_schema)
                        _grammar_checked = True
                    except Exception:  # noqa: BLE001 — check failure
                        _grammar_call = None  # is a fallback, not a crash
                # Batched calls (v5.44, B7): a reply carrying a
                # JSON ARRAY of independent calls runs them in
                # order and feeds ALL results back as ONE message.
                # The lone-object path below is untouched.
                batch_calls = _extract_tool_calls(reply_text)
                if batch_calls is not None:
                    self._notify_status("THINKING")
                    messages.append({"role": "assistant",
                                     "content": _strip_think(reply_text)})
                    feedbacks: list[str] = []
                    batch_repair = False
                    batch_done = False
                    # 7th-B01: the repair text is COLLECTED here and
                    # appended after the loop, below the completed
                    # outcomes — appending it immediately used to
                    # let `continue` skip the feedbacks message,
                    # so the next round never saw the earlier
                    # successes in this batch.
                    repair_text: str | None = None
                    for bname, bargs in batch_calls:
                        # Phase 4 (C04): this batch dispatches
                        # synchronously, so a cancel requested
                        # during an earlier item is invisible
                        # until the next await — without this
                        # check, tools AFTER the cancelled one
                        # would still dispatch in the same batch.
                        # Two synchronous signals stop the batch
                        # here (the turn dies via CancelledError,
                        # BaseException, never swallowed by the
                        # except-Exception tail below):
                        # - session stopped: stop() sets
                        #   _running False from ANY thread, so
                        #   this fires even while the loop thread
                        #   is parked inside a blocking tool
                        #   (a queued task.cancel() cannot run
                        #   until the loop regains control, so it
                        #   alone cannot stop a sync batch);
                        # - pending task cancellation, for the
                        #   cases where the flag is already set.
                        # getattr default True: harness sessions
                        # driven straight into this method never
                        # ran start(), so _running may be absent.
                        if not getattr(self, "_running", True):
                            raise asyncio.CancelledError()
                        _cur_task = asyncio.current_task()
                        if (_cur_task is not None
                                and _cur_task.cancelling() > 0):
                            raise asyncio.CancelledError()
                        bsig = _tool_call_signature(bname, bargs)
                        if bsig == prev_signature:
                            final_text = _tool_progress_report(
                                accomplished)
                            batch_done = True
                            break
                        # Phase 2 (audit 4 C07): the batch
                        # call goes through the canonical
                        # envelope; to_funnel() returns the
                        # raw funnel dict verbatim, so the
                        # repair branches below (which branch
                        # on that dict) stay byte-identical.
                        from ..funnel import tool_envelope as _envelope  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_envelope moved to core/funnel/

                        _binv = _envelope.from_local_call(bname, bargs)
                        bresult = _envelope.execute_envelope(
                            _binv, tools.execute).to_funnel()
                        # Audit A5: contract statuses, not just
                        # legacy "error".
                        from ..funnel.tool_result import (  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_result moved to core/funnel/
                            is_non_success as _is_non_success)

                        b_unknown = (
                            isinstance(bresult, dict)
                            and _is_non_success(bresult)
                            and not bresult.get("validation_error")
                            and _tool_line(bname) is None)
                        if isinstance(bresult, dict) and (
                                bresult.get("validation_error")
                                or b_unknown):
                            if budget.try_consume():  # 7th-B09: repair escalates
                                # First repair: the model emitted an
                                # unusable call (low_confidence);
                                # second and later: repeated_repair.
                                # Escalation never demotes.
                                (self._active_profile,
                                 self._last_profile_escalations) = \
                                    escalate_turn_profile(
                                        entry_profile(self),
                                        repair_rounds=budget.used,
                                        low_confidence=True)
                                repair_text = (
                                    "Tool call rejected — nothing "
                                    f"was run. "
                                    f"{bresult.get('message', '')} "
                                    "Reply with ONLY the corrected "
                                    "JSON tool call, or plain text. "
                                    f"(Repair attempt {budget.used} "
                                    f"of {MAX_TOOL_REPAIRS}.)")
                                turn_actions.append({
                                    "tool": bname, "status": "rejected",
                                    "outcome": str(
                                        bresult.get("message", ""))[:120]})
                                batch_repair = True
                            else:
                                final_text = TOOL_SHAPE_FALLBACK
                                batch_done = True
                            break
                        elif (isinstance(bresult, dict)
                                and _is_non_success(bresult)):
                            # 7th-B09: the tool RAN and failed — not a
                            # validation rejection. The turn is harder
                            # than its shape suggested, so the next
                            # model call reasons at think. Feedback
                            # below is unchanged.
                            (self._active_profile,
                             self._last_profile_escalations) = \
                                escalate_turn_profile(
                                    entry_profile(self),
                                    bad_tool_output=True)
                        if self.on_tool_call:
                            self.on_tool_call(bname, bargs, bresult)
                        # B12 (v5.45): plan auto-complete announcements.
                        _anns = planner.observe_tool_execution(bname, bresult)
                        for _ann in _anns:
                            if self.on_transcript:
                                self.on_transcript("assistant", _ann)
                        tools_used = True
                        prev_signature = bsig  # only EXECUTED calls count
                        btext = _feed_text(bresult)
                        if _anns:
                            btext += " " + " ".join(_anns)
                        last_result = bresult
                        last_result_text = btext
                        accomplished.append(f"{bname}: {btext}")
                        # Phase 2 (audit 6 B10): tool output
                        # travels back inside a delimited
                        # block with a provenance header, so
                        # the model cannot mistake it for an
                        # instruction. The "Tool results: "
                        # prefix and the one-message shape
                        # are unchanged.
                        from ..funnel import tool_delimiters as _delims  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_delimiters moved to core/funnel/

                        feedbacks.append(_delims.format_tool_result(
                            bname, btext))
                        # 6th-B06/B08: record the outcome's status,
                        # not just the attempt.
                        if _executed_tool_succeeded(bresult):
                            tools_succeeded = True
                        turn_actions.append({
                            "tool": bname,
                            "status": ("succeeded" if
                                       _executed_tool_succeeded(bresult)
                                       else "failed"),
                            "outcome": btext[:120]})
                    # 7th-B01: completed outcomes reach the next
                    # prompt FIRST, on every path out of the batch
                    # — including the repair path, which used to
                    # `continue` past them. The repair text follows
                    # the outcomes, never instead of them.
                    if feedbacks:
                        messages.append({
                            "role": "user",
                            "content": "Tool results: "
                                       + "; ".join(feedbacks),
                        })
                    if batch_done:
                        break
                    if batch_repair:
                        if repair_text is not None:
                            messages.append({
                                "role": "user",
                                "content": repair_text,
                            })
                        continue
                    # Normal path: outcomes already appended above.
                    continue
                # Extraction runs on the RAW reply (the brace matcher
                # finds a tool JSON after a think block just fine);
                # only the spoken/stored text gets _strip_think().
                # v5.55 (SC-1, check mode): prefer the
                # grammar-valid call; today's parse is the
                # fallback, logged when it finds a call the
                # grammar rejected (plain-text replies log
                # nothing — parsed is None there too).
                if _grammar_call is not None:
                    parsed = _grammar_call
                else:
                    parsed = _extract_tool_call(reply_text)
                    if _grammar_checked and parsed is not None:
                        print(f"[Grammar] fallback: '{parsed[0]}' was "
                              "not shortlist-valid — today's path")
                if parsed is None:
                    # v5.52 deliberation guard (items 4 + 9): a
                    # no-tool reply that narrates process is
                    # WITHHELD — one corrective retry, then the
                    # persona line. Raw deliberation never ships.
                    # v5.53 (F4): the same discipline AFTER tools
                    # ran — a post-tool process summary (the
                    # "Automation Hiccups" shape) is withheld
                    # for an outcome-only retry.
                    _plain = _strip_think(reply_text)
                    if _plain:
                        from .. import deliberation as _delib  # Phase 3 V1b: moved to core/voice/

                        _pre = (not tools_used
                                and _delib.looks_like_deliberation(
                                    _plain))
                        _post = (tools_used
                                 and _delib.looks_like_process_summary(
                                     _plain))
                        if _pre or _post:
                            # Phase 2 B09: the repair retry
                            # draws on the INDEPENDENT
                            # deliberation budget, never on
                            # the tool rounds — AND on the
                            # final round (or with the budget
                            # spent) there is no retry that
                            # could run, so the turn falls
                            # back immediately to the outcome
                            # summary (tools succeeded) or
                            # the persona line, instead of
                            # injecting a corrective prompt
                            # the loop can never answer.
                            _final_round = (
                                _round + 1 >= _max_tool_rounds())
                            if _final_round or not (
                                    _delib_budget.try_consume()):
                                if tools_succeeded:
                                    final_text = (
                                        _tool_progress_report(
                                            accomplished))
                                else:
                                    final_text = _delib.PERSONA_FAILURE
                                break
                            messages.append(
                                {"role": "assistant",
                                 "content": _plain})
                            messages.append(
                                {"role": "user",
                                 "content": (
                                     _delib.outcome_corrective_for(
                                         user_text, _plain)
                                     if _post else
                                     _delib.corrective_for(
                                         user_text, _plain))})
                            continue
                    if _looks_like_tool_attempt(reply_text):
                        if budget.try_consume():  # 7th-B09: repair escalates
                            # First repair: the model emitted an
                            # unusable call (low_confidence);
                            # second and later: repeated_repair.
                            # Escalation never demotes.
                            (self._active_profile,
                             self._last_profile_escalations) = \
                                escalate_turn_profile(
                                    entry_profile(self),
                                    repair_rounds=budget.used,
                                    low_confidence=True)
                            messages.append({"role": "assistant", "content": _strip_think(reply_text)})
                            # v5.29: if the reply named a tool that
                            # doesn't exist, say THAT — a generic
                            # "invalid JSON" repair leaves the model
                            # apologising for its punctuation while
                            # keeping the fantasy tool.
                            invented = _invented_tool_name(reply_text)
                            if invented is not None:
                                repair_text = (
                                    f"There is no tool called "
                                    f"'{invented}' — nothing was run. "
                                    f"Reply with ONLY valid JSON for "
                                    f"a real tool call, or plain text."
                                    f"{_closest_tool_hints(invented)} "
                                    f"(Repair attempt {budget.used} of "
                                    f"{MAX_TOOL_REPAIRS}.)")
                            else:
                                repair_text = (
                                    "That reply was not valid tool-call "
                                    "JSON. Reply with ONLY valid JSON "
                                    "for the tool call, or plain text. "
                                    f"(Repair attempt {budget.used} of "
                                    f"{MAX_TOOL_REPAIRS}.)")
                            messages.append({
                                "role": "user",
                                "content": repair_text,
                            })
                            continue
                        final_text = TOOL_SHAPE_FALLBACK
                    else:
                        final_text = _strip_think(reply_text)
                    break

                name, args = parsed
                # No-progress breaker (v5.44, B7): an identical
                # call to the one just executed runs NOTHING — the
                # turn ends with a progress report instead.
                signature = _tool_call_signature(name, args)
                if signature == prev_signature:
                    final_text = _tool_progress_report(accomplished)
                    break
                self._notify_status("THINKING")
                messages.append({"role": "assistant", "content": _strip_think(reply_text)})
                # Phase 2 (audit 4 C07): the lone call goes
                # through the canonical envelope; to_funnel()
                # returns the raw funnel dict verbatim, so
                # the repair branches below stay
                # byte-identical.
                from ..funnel import tool_envelope as _envelope  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_envelope moved to core/funnel/

                _linv = _envelope.from_local_call(name, args)
                result = _envelope.execute_envelope(
                    _linv, tools.execute).to_funnel()
                # v5.29: an INVENTED tool name used to fall into the
                # success path below — execute() reports it as a
                # plain error result, so the turn counted it as a
                # tool that ran and the model was free to claim the
                # action worked (brain proof, 2026-10-22: a
                # "remember…" request "called" a nonexistent
                # store_memory, stored nothing, and announced
                # success). An unknown name is a REPAIR, like a
                # schema rejection: nothing ran, and the model
                # retries — now with the closest real tools' exact
                # schemas in front of it (see schema_note below).
                # Audit A5: contract statuses, not just legacy
                # "error".
                from ..funnel.tool_result import (  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_result moved to core/funnel/
                    is_non_success as _is_non_success)

                unknown_tool = (
                    isinstance(result, dict)
                    and _is_non_success(result)
                    and not result.get("validation_error")
                    and _tool_line(name) is None
                )
                if isinstance(result, dict) and (
                        result.get("validation_error") or unknown_tool):
                    # The call parsed, but its arguments failed schema
                    # validation in tools.execute() — NOTHING ran.
                    # Feed the validator's exact error back so the
                    # model can repair its own call, drawing from the
                    # same repair budget as parse failures. Since
                    # v5.19 the tool's exact schema line rides along:
                    # a tool the model picked from the compact index
                    # has never SEEN its schema — the error alone
                    # would leave it guessing twice.
                    if budget.try_consume():  # 7th-B09: repair escalates
                        # First repair: the model emitted an
                        # unusable call (low_confidence); second
                        # and later: repeated_repair. Escalation
                        # never demotes.
                        (self._active_profile,
                         self._last_profile_escalations) = \
                            escalate_turn_profile(
                                entry_profile(self),
                                repair_rounds=budget.used,
                                low_confidence=True)
                        schema_note = ""
                        line = _tool_line(name)
                        if line:
                            schema_note = (f" The exact schema for "
                                           f"{name} is: {line}")
                        elif unknown_tool:
                            schema_note = _closest_tool_hints(name)
                        turn_actions.append({
                            "tool": name, "status": "rejected",
                            "outcome": str(
                                result.get("message", ""))[:120]})
                        messages.append({
                            "role": "user",
                            "content": f"Tool call rejected — nothing was run. "
                                       f"{result.get('message', '')} Reply with "
                                       f"ONLY the corrected JSON tool call, or "
                                       f"plain text.{schema_note} "
                                       f"(Repair attempt {budget.used} of "
                                       f"{MAX_TOOL_REPAIRS}.)",
                        })
                        continue
                    final_text = TOOL_SHAPE_FALLBACK
                    break
                elif (isinstance(result, dict)
                        and _is_non_success(result)):
                    # 7th-B09: the tool RAN and failed — not a
                    # validation rejection. The turn is harder
                    # than its shape suggested, so the next model
                    # call reasons at think. Feedback below is
                    # unchanged.
                    (self._active_profile,
                     self._last_profile_escalations) = \
                        escalate_turn_profile(
                            entry_profile(self),
                            bad_tool_output=True)
                if self.on_tool_call:
                    self.on_tool_call(name, args, result)
                # B12 (v5.45): plan auto-complete announcements ride
                # the fed-back result and the transcript.
                _anns = planner.observe_tool_execution(name, result)
                for _ann in _anns:
                    if self.on_transcript:
                        self.on_transcript("assistant", _ann)
                tools_used = True
                prev_signature = signature  # only EXECUTED calls count
                last_result = result
                last_result_text = _feed_text(result)
                if _anns:
                    last_result_text += " " + " ".join(_anns)
                accomplished.append(f"{name}: {last_result_text}")
                # 6th-B06/B08: status, not just the attempt.
                if _executed_tool_succeeded(result):
                    tools_succeeded = True
                turn_actions.append({
                    "tool": name,
                    "status": ("succeeded" if
                               _executed_tool_succeeded(result)
                               else "failed"),
                    "outcome": last_result_text[:120]})
                # Phase 2 (audit 6 B10): the lone result
                # travels back inside a delimited block with
                # a provenance header, so the model cannot
                # mistake tool output for an instruction. The
                # "Tool result for {name}: " prefix is
                # unchanged.
                from ..funnel import tool_delimiters as _delims2  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_delimiters moved to core/funnel/

                messages.append({
                    "role": "user",
                    "content": (f"Tool result for {name}: "
                                + _delims2.format_tool_result(
                                    name, last_result_text)),
                })
            else:
                # The rounds ran out with a tool result still
                # unspoken (v5.19): the old code apologised here and
                # the user never heard what the last tool found.
                # Give the model ONE final call to answer from the
                # results — no more tools — and fall back to the
                # last result itself when that call comes back as a
                # tool call anyway, or as nothing at all.
                if last_result_text is not None:
                    messages.append({
                        "role": "user",
                        "content": "Answer the user now from the tool "
                                   "results above. Do not call any "
                                   "more tools.",
                    })
                    try:
                        answer = await loop.run_in_executor(
                            None, self._llama_chat, messages
                        )
                    except Exception:  # noqa: BLE001 — fall back below
                        answer = ""
                    if answer and _extract_tool_call(answer) is None:
                        final_text = _strip_think(answer)
                    if not final_text:
                        final_text = _spoken_fallback(
                            last_result, last_result_text)
                else:
                    final_text = "Sorry — I got stuck in a loop there."
        except Exception as e:  # noqa: BLE001 — a dead turn must not kill the session
            print(f"[LocalBrain] Built-in chat error: {e}")
            if self._history and self._history[-1] is user_msg:
                self._history.pop()  # don't leave a dangling unanswered turn
            self._notify_status(f"ERROR: {str(e)[:300]}")
            self._notify_status("LISTENING")
            return

        # Empty-reply honesty (v5.19): a model that answers with
        # nothing (or only a think block) earns ONE retry with a
        # plain-text nudge before the turn gives up. The retry runs
        # BEFORE the state guard below on purpose, so a retried
        # fabrication is caught exactly like a first-attempt one.
        if not final_text and not tools_used:
            try:
                retry_text = await loop.run_in_executor(
                    None, self._llama_chat,
                    messages + [{
                        "role": "user",
                        "content": "Please answer the question in "
                                   "plain text.",
                    }],
                )
            except Exception:  # noqa: BLE001 — the fallback below speaks
                retry_text = ""
            if retry_text and _extract_tool_call(retry_text) is None:
                final_text = _strip_think(retry_text)

        # Brain-quality guard (v5.3; widened v5.43 B11): a guarded
        # state question answered WITHOUT any tool call is a
        # fabrication by construction — the model cannot know the
        # time, the battery, the network or the download state.
        # Replace it, before it is spoken/shown/stored, with one
        # forced repair through the real registry. See
        # _STATE_GUARD_RULES.
        # 6th-B06: the guard consults tools_succeeded, not
        # tools_used — a FAILED tool is no evidence, so the
        # forced repair still fires and the fabrication is
        # replaced by the repair's honest result.
        if (not tools_succeeded and final_text
                and _state_guard_for(user_text) is not None):
            repaired = self._guard_state_answer(user_text)
            if repaired is not None:
                final_text = repaired

        if not final_text:
            # "Done." is only honest when a tool actually
            # SUCCEEDED; a question that produced no answer at
            # all gets the truth instead of a fake completion
            # (v5.19). 6th-B06: an attempted-but-failed tool gets
            # the failure's honest rendering, never "Done."
            if tools_succeeded:
                final_text = _spoken_fallback(
                    last_result, last_result_text) or "Done."
            elif tools_used:
                final_text = _spoken_fallback(
                    last_result, last_result_text) or (
                        "I couldn't complete that step — "
                        "could you ask me again?")
            else:
                final_text = ("I didn't get an answer that time — "
                              "could you ask me again?")
        self._record_turn_actions(turn_actions)
        self._history.append({"role": "assistant", "content": final_text})
        self._history = self._history[-HISTORY_CAP:]
        if self.on_transcript:
            self.on_transcript("assistant", final_text)
        memory_store.log_message("assistant", final_text)
        await self._speak(final_text)

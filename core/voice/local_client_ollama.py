# Batch 4 (plan §3.3): ollama-llama (_brain_turn_ollama/_llama_chat/continuity), moved verbatim
# from core/voice/local_client.py. INTENTIONAL: imports below are the
# subset of the old module's import block that this group's code uses.
import asyncio
import json
from typing import Any
from ..config import config
from ..memory import episodes
from ..brain import planner
from ..languages import reply_language_directive
from ..memory.memory import memory_store
from ..funnel.tools_registry import tools
from .local_client_guards import (
    HISTORY_CAP, HISTORY_WINDOW, _is_lite_tier_model, _log_brain_guard,
    _max_tool_rounds, _memory_block, _state_guard_for,
    _tool_call_signature, _tool_progress_report, sampling_profile)
from .local_client_vlm import (
    _executed_tool_succeeded, _feed_text, _result_to_text, _spoken_fallback)
from .local_client_toolparse import _to_ollama_tools, _tool_match_score
from .local_client_budgets import _tool_line
from .local_client_cost import _cost_prompt_text, _cost_record_local


class LocalLiveSessionOllamaMixin:

    async def _brain_turn_ollama(self, user_text: str):
        """Runs one Ollama chat turn, executing tool calls as they come."""
        user_msg = {"role": "user", "content": user_text}
        self._history.append(user_msg)
        self._history = self._history[-HISTORY_CAP:]

        system = config.system_instruction
        # v5.41: a non-English home adds its reply-language
        # directive here ("" for English — byte-identical).
        system += reply_language_directive()
        # Conversion Wave 8: prompt-cache-first stable prefix
        # (persona + directive, byte-identical + SHA-256 key,
        # recorded for cache-hit checks) + ledger pre-flight of
        # the assembled system text happens in _memory_block for
        # the memory stream; the stable-prefix note lives here,
        # where the stable parts are assembled.
        try:
            from .. import w8_wiring as _w8  # Phase 3 V1b: moved to core/voice/

            _cached = _w8.note_stable_prefix(
                [config.system_instruction,
                 reply_language_directive()])
            self._w8_cache_key = _cached.cache_key
        except Exception:  # noqa: BLE001 — cache key is advisory
            pass
        memories = _memory_block(user_text)
        if memories:
            system += f"\n\n{memories}"
        # Ambient context (v5.44, N4): the stashed packet folds in
        # at the memory block's place, one-shot like the day block
        # below (a refresh re-stashes it for a later turn).
        ambient_block = self._take_ambient_block()
        if ambient_block:
            system += f"\n\n{ambient_block}"
        # Continuity (v5.43, B14): the episodes day-render rides
        # the FIRST turn's assembly only (one-shot — see
        # _seed_continuity_history); "" on every later turn and
        # whenever the journal is empty.
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

        messages = (
            [{"role": "system", "content": system}]
            + self._history[-(HISTORY_WINDOW + 1):-1]
            + [user_msg]
        )
        # v5.42 (B1): fitted to _OLLAMA_TOOLS_BUDGET, with this
        # turn's utterance driving query promotion — the same
        # input _brain_turn_builtin passes _render_tool_protocol.
        ollama_tools = _to_ollama_tools(tools.get_declarations(),
                                        query=user_text)

        loop = asyncio.get_running_loop()
        final_text = ""
        tools_executed = False  # brain-guard evidence (v5.19 parity)
        # Turn-truthfulness (6th-B06/B08): attempted is not
        # succeeded (see the builtin path's note); turn_actions
        # records this turn's tool outcomes for the trace.
        tools_succeeded = False
        turn_actions: list[dict[str, Any]] = []
        _delib_retried = False  # v5.52 deliberation guard (items 4+9)
        last_result_text: str | None = None
        last_result = None  # v5.51 (M-4): the dict behind the text
        # No-progress breaker (v5.44, B7): the same tool+args
        # twice in a row ends the turn with a progress report.
        prev_signature: str | None = None
        accomplished: list[str] = []
        progress_break = False
        try:
            for _round in range(_max_tool_rounds()):
                reply = await loop.run_in_executor(
                    None, self._ollama_chat, messages, ollama_tools
                )
                msg = reply.get("message") or {}
                tool_calls = msg.get("tool_calls") or []
                content = (msg.get("content") or "").strip()
                if not tool_calls:
                    # v5.52 deliberation guard (items 4 + 9): a
                    # no-tool reply that narrates process is
                    # WITHHELD — one corrective retry, then the
                    # persona line. Raw deliberation never ships.
                    # v5.53 (F4): the same discipline AFTER tools
                    # ran — a post-tool process summary (the
                    # "Automation Hiccups" shape) is withheld
                    # for an outcome-only retry.
                    if content:
                        from .. import deliberation as _delib  # Phase 3 V1b: moved to core/voice/

                        _pre = (not tools_executed
                                and _delib.looks_like_deliberation(
                                    content))
                        _post = (tools_executed
                                 and _delib.looks_like_process_summary(
                                     content))
                        if _pre or _post:
                            if not _delib_retried:
                                _delib_retried = True
                                messages.append(msg)
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        _delib.outcome_corrective_for(
                                            user_text, content)
                                        if _post else
                                        _delib.corrective_for(
                                            user_text, content))})
                                continue
                            content = _delib.PERSONA_FAILURE
                    final_text = content
                    break

                self._notify_status("THINKING")
                messages.append(msg)  # Ollama expects its own message echoed back
                for call in tool_calls:
                    fn = call.get("function") or {}
                    name = fn.get("name", "")
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    signature = _tool_call_signature(name, args)
                    if signature == prev_signature:
                        final_text = _tool_progress_report(accomplished)
                        progress_break = True
                        break
                    result = tools.execute(name, args)
                    prev_signature = signature  # only EXECUTED calls count
                    if self.on_tool_call:
                        self.on_tool_call(name, args, result)
                    last_result = result
                    last_result_text = _feed_text(result)
                    # B12 (v5.45): plan auto-complete announcements.
                    _anns = planner.observe_tool_execution(name, result, args)
                    for _ann in _anns:
                        if self.on_transcript:
                            self.on_transcript("assistant", _ann)
                    if _anns:
                        last_result_text += " " + " ".join(_anns)
                    if _tool_line(name) is None:
                        # v5.29 parity with the builtin path: an
                        # invented name ran NOTHING — say so in the
                        # fed-back result, name the closest real
                        # tools, and don't count it as an execution.
                        ranked = sorted(
                            tools.get_declarations(),
                            key=lambda d: -_tool_match_score(name, d))
                        close = [d["name"] for d in ranked[:2]
                                 if _tool_match_score(name, d) >= 0.8]
                        hint = (f" Closest real tools: "
                                f"{', '.join(close)}.") if close else ""
                        last_result_text += (
                            " (That tool does not exist — nothing "
                            f"was run.{hint})")
                        turn_actions.append({
                            "tool": name, "status": "rejected",
                            "outcome": last_result_text[:120]})
                    else:
                        tools_executed = True
                        # 6th-B06/B08: status, not just the attempt.
                        if _executed_tool_succeeded(result):
                            tools_succeeded = True
                        turn_actions.append({
                            "tool": name,
                            "status": ("succeeded" if
                                       _executed_tool_succeeded(result)
                                       else "failed"),
                            "outcome": last_result_text[:120]})
                    messages.append({"role": "tool", "content": last_result_text})
                    if _tool_line(name) is not None:
                        accomplished.append(f"{name}: {last_result_text}")
                if progress_break:
                    break
            else:
                # The rounds ran out with a tool result still
                # unspoken (v5.19, the same fix as the builtin
                # path): give the model ONE final call with no
                # tools on offer so it answers from the results —
                # and if even that comes back empty, speak the last
                # result itself rather than an apology.
                if last_result_text is not None:
                    messages.append({
                        "role": "user",
                        "content": "Answer the user now from the tool "
                                   "results above. Do not call any "
                                   "more tools.",
                    })
                    try:
                        reply = await loop.run_in_executor(
                            None, self._ollama_chat, messages, []
                        )
                        answer = ((reply.get("message") or {})
                                  .get("content") or "").strip()
                    except Exception:  # noqa: BLE001 — fall back below
                        answer = ""
                    final_text = answer or _spoken_fallback(
                        last_result, last_result_text)
                else:
                    final_text = content or "Sorry — I got stuck in a loop there."
        except Exception as e:  # noqa: BLE001 — a dead turn must not kill the session
            print(f"[LocalBrain] Chat error: {e}")
            if self._history and self._history[-1] is user_msg:
                self._history.pop()  # don't leave a dangling unanswered turn
            self._notify_status(f"ERROR: {str(e)[:300]}")
            self._notify_status("LISTENING")
            return

        # Brain-quality guard (v5.19 parity): the builtin path has
        # replaced fabricated time/date answers since v5.3 (see
        # _STATE_QUESTION_RE) — the Ollama path never got the guard.
        # Same rule: a state question answered with NO tool call is
        # a fabrication by construction and is replaced, before it
        # is spoken, shown, or stored, by a forced repair through
        # the real registry.
        # 6th-B06: succeeded, not merely executed — a failed tool
        # is no evidence, so the forced repair still fires.
        if (not tools_succeeded and final_text
                and _state_guard_for(user_text) is not None):
            repaired = self._guard_state_answer(user_text)
            if repaired is not None:
                final_text = repaired

        if not final_text:
            # "Done." is only honest when a tool actually
            # SUCCEEDED; a question that produced no answer at
            # all gets the truth instead of a fake completion
            # (v5.19). 6th-B06: attempted-but-failed gets the
            # failure's honest rendering, never "Done."
            if tools_succeeded:
                final_text = _spoken_fallback(
                    last_result, last_result_text) or "Done."
            elif tools_executed:
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

    def _llama_chat(self, messages: list[dict[str, Any]],
                    profile: str | None = None) -> str:
        """One blocking llama.cpp chat completion (executor only).

        create_chat_completion applies the GGUF's own chat template —
        the model sees its native format; only the tool protocol inside
        the system prompt is ours. Returns the reply text. The model
        reference is captured up front: the idle unload may drop the
        session's reference, never an in-flight call's.

        v5.45 (B13): sampling comes from the turn's adaptive
        profile — `profile` when given, else the session's
        _active_profile (set by the brain-turn methods from the
        utterance), else the pre-B13 fixed profile. On the Lite
        tier (Qwen3-1.7B), no-think discipline is enforced via
        core.lite_client.apply_no_think so reasoning tokens stop
        eating the reply cap."""
        llama = self._llama
        if llama is None:
            raise RuntimeError("local brain model is not loaded")
        sampling = sampling_profile(
            profile or getattr(self, "_active_profile", None))
        send_messages = messages
        if _is_lite_tier_model():
            try:
                from .lite_client import apply_no_think

                send_messages = apply_no_think(messages)
            except Exception:  # noqa: BLE001 — discipline is a nicety
                send_messages = messages
        reply = llama.create_chat_completion(
            messages=send_messages,
            max_tokens=sampling["max_tokens"],
            temperature=sampling["temperature"],
        )
        choices = reply.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        reply_text = (message.get("content") or "").strip()
        # MN-2.8: zero-cost ledger row, estimated from text
        # (this wrapper returns text only, no usage counts).
        _cost_record_local(None, str(getattr(config, "local_model", "")
                                     or "builtin"),
                           _cost_prompt_text(messages), reply_text)
        return reply_text

    def _guard_state_answer(self, user_text: str = "") -> str | None:
        """The brain guard's forced repair (v5.3; widened v5.43
        B11): ask the real registry for the live state the user
        asked about — the class->tool map of _state_guard_for,
        defaulting to the original time/date tool when no question
        text is supplied — and answer from ITS result. Returns the
        replacement reply text, or None when the tool itself
        produced nothing usable (the caller then keeps the model's
        reply — a flawed answer beats a crashed turn, and the miss
        is still logged). The forced call is surfaced through
        on_tool_call like any other execution: hiding it would make
        the transcript claim VYUK answered unaided."""
        name, args = (_state_guard_for(user_text)
                      or ("get_current_time_date", {}))
        try:
            result = tools.execute(name, args)
        except Exception as e:  # noqa: BLE001 — guard must never break a turn
            _log_brain_guard(f"forced repair failed: {e}")
            return None
        text = _result_to_text(result).strip()
        if not text:
            _log_brain_guard("forced repair produced no usable result")
            return None
        if self.on_tool_call:
            self.on_tool_call(name, args, result)
        _log_brain_guard("fabricated state answer replaced")
        # v5.51 (M-4): this text is spoken verbatim as the turn's
        # answer — an error result is spoken as the butler line,
        # never as the raw "Error: …" rendering.
        return _spoken_fallback(result, text)

    # ------------------------------------------------------------------ #
    # Continuity (v5.43, B10/B14): seed this session from the stored
    # conversation logs and hold the episodes day-render for the
    # FIRST turn's prompt assembly only. The helpers live in
    # core/episodes.py — the one continuity design every brain
    # uses. Seeding is in-memory only: nothing is re-logged, and a
    # session that already has history (a live conversation, a
    # suite's fixture) is never re-seeded.
    # ------------------------------------------------------------------ #
    def _seed_continuity_history(self):
        """Session-start seeding (see the section header). Never
        raises: continuity is a nicety, not a session blocker."""
        if self._history:
            return
        try:
            seeded = episodes.seed_history_messages()
        except Exception:  # noqa: BLE001
            seeded = []
        if seeded:
            self._history = seeded[-HISTORY_CAP:]
        try:
            block = episodes.day_render_block()
        except Exception:  # noqa: BLE001
            block = ""
        if block:
            self._continuity_day_block = block
            self._day_block_pending = True

    def _take_continuity_day_block(self) -> str:
        """The held day-render, exactly once: the first brain turn
        to assemble its prompt takes it, later turns get ""."""
        # getattr: a session shell built without __init__ (test
        # harnesses do this) has no held block — "" is the truth.
        if not getattr(self, "_day_block_pending", False):
            return ""
        self._day_block_pending = False
        block = getattr(self, "_continuity_day_block", "")
        self._continuity_day_block = ""
        return block

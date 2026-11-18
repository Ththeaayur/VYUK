"""GroqSession B mixin (B39 part 2 of 2) — moved verbatim
from core/voice/cloud_text_client.py; the composed class lives
in cloud_text_client.py. No behaviour change."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
import uuid

from ..groq_action_journal import GroqActionJournal  # Phase 3 V1b: moved to core/voice/
from ..memory.memory import memory_store  # Phase 3 V1b: moved to core/voice/
from ..funnel.tools_registry import tools  # Phase 3 V1b: moved to core/voice/; Phase 4 S1b INTENTIONAL: registry split to funnel

# INTENTIONAL (B39 part 2 of 2): methods moved verbatim
# from cloud_text_client.py resolve helper names
# through cloud_text_helpers (part 1's module).
from .cloud_text_helpers import (
    GROQ_CHAT_URL,
    HISTORY_CAP,
    HISTORY_WINDOW,
    LOOP_FALLBACK,
    NETWORK_ERROR_STATUS,
    _DeliberationBudget,
    _GroqTurnError,
    _call_signature,
    _classify_http_error,
    _cost_preflight_or_raise,
    _cost_prompt_text,
    _cost_record,
    _groq_api_key,
    _groq_model,
    _groq_temperature,
    _log_line,
    _max_tool_rounds,
    _post_json,
    _result_text,
    _state_guard_for,
    _validate_tool_exchanges,
    groq_offered_declarations,
    openai_tools,
)


class GroqSessionTurnMixin:

    def _chat_completion(self, messages: list[dict[str, Any]]) -> tuple[int, Any]:
        """One blocking chat completion (executor only) — the whole
        request goes through the _post_json seam."""
        payload: dict[str, Any] = {
            "model": _groq_model(),
            "messages": messages,
            "tool_choice": "auto",
            "temperature": _groq_temperature(),
            "stream": False,
        }
        declarations = groq_offered_declarations()
        if declarations:
            payload["tools"] = openai_tools(declarations)
        # MN-2.8: cap gate first, ledger row after a 200 reply.
        prompt_text = _cost_prompt_text(messages)
        _cost_preflight_or_raise(_groq_model(), prompt_text)
        status, parsed = _post_json(GROQ_CHAT_URL, _groq_api_key(), payload)
        if status == 200:
            _cost_record(parsed, _groq_model(), prompt_text)
        return status, parsed

    def _mark_groq_turn_failed(self) -> None:
        """Mark this turn's journal events as failed-turn events.
        The history rollback above still trims _history; the
        journal keeps what it dropped. Never breaks the turn:
        journal errors are logged, not raised."""
        try:
            journal = getattr(self, "_action_journal", None)
            turn_id = getattr(self, "_groq_turn_id", "")
            if journal is not None and turn_id:
                journal.mark_turn_failed(turn_id)
        except Exception as exc:  # noqa: BLE001 — never break a turn
            _log_line(f"Groq action journal turn-failure mark failed: {exc}")

    def _run_tool_call(self, call: dict[str, Any]) -> dict[str, Any]:
        """Executes one OpenAI tool_call through the REAL registry
        and returns its role:"tool" message. Malformed argument JSON
        is an honest error RESULT back to the model — nothing runs,
        nothing crashes, and the model can correct itself."""
        fn = call.get("function") or {}
        name = fn.get("name", "")
        raw_args = fn.get("arguments")
        args: Any = raw_args
        if isinstance(raw_args, str):
            if raw_args.strip():
                try:
                    args = json.loads(raw_args)
                except ValueError:
                    args = None
            else:
                args = {}
        if not isinstance(args, dict):
            # 6th-B06: nothing ran, so nothing succeeded.
            self._last_tool_success = False
            return {
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": (
                    f"Error: the arguments for {name or 'that tool'} "
                    "weren't valid JSON, so nothing was run. Send the "
                    "arguments as a JSON object."
                ),
            }
        # Phase 0 (C02): durable journal + replay guard. The journal
        # record is written BEFORE execution (invocation id, tool,
        # sanitized args, turn linkage) and its outcome AFTER; the
        # guard below runs before either. Journal failures never
        # break the call: they are logged and execution proceeds
        # (the gap is visible, not fatal — the ledger precedent).
        journal = getattr(self, "_action_journal", None)
        if journal is None:
            try:
                journal = self._action_journal = GroqActionJournal()
            except Exception as exc:  # noqa: BLE001 — never break a turn
                _log_line(f"Groq action journal unavailable: {exc}")
                journal = None
        turn_key = getattr(self, "_groq_turn_id", "") or ""
        signature = _call_signature(call)
        event = None
        if journal is not None:
            try:
                prior = journal.find_completed_failed_turn(
                    tool=name, args_signature=signature)
            except Exception as exc:  # noqa: BLE001 — guard unavailable
                _log_line(f"Groq action journal guard unavailable: {exc}")
                prior = None
            if prior is not None and (
                    prior.get("surfaced_in_turn") is None
                    or prior.get("surfaced_in_turn") == turn_key):
                # Blind replay blocked: surface the prior execution
                # instead of running. A re-issue in a LATER turn is
                # allowed (the surfaced mark proves the conversation
                # has seen it by then); a re-issue in THIS turn
                # stays blocked.
                try:
                    journal.mark_surfaced(prior["event_id"], turn_key)
                except Exception as exc:  # noqa: BLE001
                    _log_line(
                        f"Groq action journal surface mark failed: {exc}")
                try:
                    prior_when = time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(float(prior.get("created_at") or 0)))
                except (TypeError, ValueError, OverflowError):
                    prior_when = "an earlier turn"
                prior_summary = str(prior.get("outcome_summary") or "")[:300]
                # 6th-B06: the guard blocked execution — no success.
                self._last_tool_success = False
                return {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": (
                        f"Not run: '{name}' with these arguments "
                        f"already completed on {prior_when} with "
                        f"outcome: {prior_summary}. The turn it ran "
                        "in later failed, so this record was kept "
                        "deliberately. Verify the current state "
                        "before asking me to run it again — a fresh "
                        "instruction in a later turn, after "
                        "verification, will run it."
                    ),
                }
            try:
                event = journal.record_started(
                    tool=name, args_signature=signature, args=args,
                    turn_id=turn_key,
                    turn_label=getattr(self, "_groq_turn_label", ""))
            except Exception as exc:  # noqa: BLE001 — never break a turn
                _log_line(f"Groq action journal record failed: {exc}")
                event = None
        # Phase 2 (audit 4 C07): the OpenAI call shape is
        # converted to the canonical envelope and dispatched
        # through it — the funnel dict comes back verbatim
        # (to_funnel), so this wiring cannot change what the
        # turn does with the result; it only adds the stable
        # action id and the uniform outcome view.
        from ..funnel import tool_envelope as _envelope  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_envelope moved to core/funnel/

        invocation = _envelope.from_openai_call(call)
        outcome = _envelope.execute_envelope(invocation, tools.execute)
        result = outcome.to_funnel()
        if self.on_tool_call:
            self.on_tool_call(name, args, result)
        content = _result_text(result)
        # B12 (v5.45): plan auto-complete announcements ride the
        # tool result and the transcript. A routed use_toolbox
        # 'call' is observed under the INNER tool's name (the
        # dispatcher ran it through the same funnel; its outcome
        # is this result), so routed executions complete steps
        # exactly like direct ones.
        try:
            from ..brain import planner as _planner  # Phase 3 V1b: moved to core/voice/

            _observe_name = name
            if name == "use_toolbox" and isinstance(args, dict) \
                    and str(args.get("action", "")).lower() == "call":
                _observe_name = str(args.get("tool") or name)
                if "did not run:" in content or "Unknown tool" in content:
                    _observe_name = name  # inner call failed: no completion
            _anns = _planner.observe_tool_execution(_observe_name, result)
            for _ann in _anns:
                if self.on_transcript:
                    self.on_transcript("assistant", _ann)
            if _anns:
                content += " " + " ".join(_anns)
        except Exception:  # noqa: BLE001 — bookkeeping never breaks a turn
            pass
        # 6th-B06: one success verdict for this call, shared by the
        # journal record and the turn's attempted-vs-succeeded
        # flag — set even when the journal itself is unavailable.
        call_status = result.get("status", "unknown") \
            if isinstance(result, dict) else "unknown"
        if event is not None:
            # Phase 0 (C02): outcome lands after execution — a
            # "success" here is what the replay guard later matches.
            try:
                journal.record_outcome(
                    event["invocation_id"], status=call_status,
                    outcome_summary=content)
            except Exception as exc:  # noqa: BLE001 — never break a turn
                _log_line(f"Groq action journal outcome failed: {exc}")
        self._last_tool_success = (call_status == "success")
        return {
            "role": "tool",
            "tool_call_id": call.get("id", ""),
            "content": content,
        }

    def _guard_state_answer(self, user_text: str = "") -> str | None:
        """The brain guard's forced repair (mirrors
        local_client._guard_state_answer): ask the real registry
        for the live state the user asked about — the shared
        class->tool map (v5.43, B11: time/date, battery, network,
        download), defaulting to the time/date tool when no
        question text is supplied — and answer from ITS result.
        The forced call is surfaced through on_tool_call like any
        other execution — hiding it would make the transcript
        claim VYUK answered unaided."""
        name, args = (_state_guard_for(user_text)
                      or ("get_current_time_date", {}))
        try:
            result = tools.execute(name, args)
        except Exception as e:  # noqa: BLE001 — guard must never break a turn
            _log_line(f"State-guard forced repair failed: {e}")
            return None
        text = _result_text(result).strip()
        if not text:
            _log_line("State-guard forced repair produced no usable result")
            return None
        if self.on_tool_call:
            self.on_tool_call(name, args, result)
        _log_line("Fabricated state answer replaced (brain guard)")
        # v5.51 (M-4): this text becomes the turn's spoken answer
        # verbatim — an error result is spoken as the butler line
        # (core/voice/spoken_errors), never the raw "Error: …" text.
        # Audit A5: the contract statuses, not just legacy "error".
        from ..funnel.tool_result import is_non_success as _is_non_success  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_result moved to core/funnel/

        if isinstance(result, dict) and _is_non_success(result):
            try:
                from .spoken_errors import spoken_error_text  # Phase 3 V1b: moved to core/voice/

                line = spoken_error_text(result)
                if line:
                    return line
            except Exception:  # noqa: BLE001 — guard must not break
                pass
        return text

    def _cap_history(self):
        """Keeps the stored history at HISTORY_CAP messages without
        orphaning a tool round-trip: after trimming, any partial
        tool exchange is dropped whole — a leading tool message,
        or an assistant tool_calls group missing any response —
        validated by exact tool_call_id matching (6th-B04)."""
        while len(self._history) > HISTORY_CAP:
            self._history.pop(0)
        self._history = _validate_tool_exchanges(self._history)

    def _windowed_history(self) -> list[dict[str, Any]]:
        """The stored history as the request should see it: the
        recent window, never starting mid-tool-round — and never
        carrying a partial tool exchange anywhere (6th-B04)."""
        window = list(self._history[-(HISTORY_WINDOW + 1):])
        return _validate_tool_exchanges(window)

    def _groq_exhaustion_summary(self, accomplished: list[str]) -> str:
        """6th-B02: what a round-exhausted Groq turn reports instead
        of the generic LOOP_FALLBACK when actions already ran.

        The Phase 0 journal is the outcome source when it holds
        this turn's successful events; the turn's in-memory
        `accomplished` list is the fallback (journal unavailable
        or empty). With neither, the generic fallback stands —
        nothing completed, so there is nothing true to report.
        The wording always says partial: the rest is not done, so
        a partial turn can never read as a total success."""
        items: list[str] = []
        try:
            journal = getattr(self, "_action_journal", None)
            turn_id = getattr(self, "_groq_turn_id", "")
            if journal is not None and turn_id:
                for event in journal.events():
                    if (event.get("turn_id") == turn_id
                            and event.get("status") == "success"):
                        summary = str(
                            event.get("outcome_summary") or "")[:200]
                        name = str(event.get("tool", "?"))
                        items.append(f"{name}: {summary}"
                                     if summary else name)
        except Exception:  # noqa: BLE001 — journal is advisory here
            items = []
        if not items:
            items = [str(a)[:200] for a in accomplished]
        if not items:
            return LOOP_FALLBACK
        return (
            "I ran out of rounds before finishing, so this is only "
            "partway done. What actually completed: "
            + "; ".join(items)
            + ". The rest is not done yet.")

    def _refresh_turn_memory(self, user_text: str) -> str:
        """Phase 2 (audit 6 B03): per-turn memory refresh.

        Re-reads the memory context for THIS turn's utterance
        (relevance-ranked, like the local brain's per-turn
        block) and returns a refresh section to append to the
        turn's system message — or "" when there is nothing
        new. Fast path: a refresh identical to the start-time
        block is skipped, so the system prompt stays
        byte-identical. No start block recorded (a session
        built without _build_system_prompt, e.g. a test
        harness) also returns "": with no baseline there is
        no honest delta to report. Over-budget blocks are cut
        on a line boundary with the same disclosure the local
        brain's _memory_block uses, against the same hard
        memory budget. Any failure returns "" — memory must
        never break a turn."""
        if not hasattr(self, "_start_memory_block"):
            return ""
        try:
            fresh = memory_store.get_memory_context_prompt(
                utterance=user_text)
        except TypeError:
            try:
                fresh = memory_store.get_memory_context_prompt()
            except Exception:  # noqa: BLE001 — never break a turn
                return ""
        except Exception:  # noqa: BLE001 — never break a turn
            return ""
        if not fresh or fresh == self._start_memory_block:
            return ""
        try:
            from ..brain.context import HARD_BUDGETS_TOKENS  # Phase 3 V1b: moved to core/voice/

            budget_chars = int(HARD_BUDGETS_TOKENS["memory"] * 3.2)
        except Exception:  # noqa: BLE001 — fallback cap
            budget_chars = 6000
        if len(fresh) > budget_chars:
            cut = fresh.rfind("\n", 0, budget_chars)
            fresh = fresh[:cut if cut > 0 else budget_chars]
            fresh += ("\n- … (further memories trimmed for "
                      "the memory budget)")
        return (
            "\n\n[Memory refresh — relevant memories re-read "
            "for this turn; source: memory store; scope: this "
            "turn only]\n" + fresh)

    async def _brain_turn(self, user_text: str):
        """One Groq chat turn: completions + native tool rounds.

        Callers own the transcript/log echo of the user's line; this
        method owns history, the assistant transcript, and speech —
        the same division as local_client's brain turns."""
        # v5.52 (item 7): publish the turn's instruction for the
        # executor's echo guard (core/brain/turn_context).
        try:
            from ..brain import turn_context as _tc  # Phase 3 V1b: moved to core/voice/

            _tc.set_current_instruction(user_text)
        except Exception:  # noqa: BLE001 — never break a turn
            pass
        turn_start = len(self._history)
        # Phase 0 (C02): this turn's journal identity. _run_tool_call
        # records every execution under it, and the failure branches
        # below mark the turn failed — so a retry cannot blindly
        # replay its completed actions.
        self._groq_turn_id = uuid.uuid4().hex
        self._groq_turn_label = user_text
        user_msg = {"role": "user", "content": user_text}
        self._history.append(user_msg)

        # Ambient refresh (v5.44, N4): a packet stashed since the
        # last turn folds into THIS turn's system message only.
        system_content = self._system_prompt
        pending_ambient = getattr(self, "_ambient_context_pending", "")
        if pending_ambient:
            self._ambient_context_pending = ""
            self._ambient_packet = pending_ambient
            system_content += f"\n\n{pending_ambient}"
        # Phase 2 (audit 6 B03): per-turn memory refresh — a
        # changed memory block appends a provenance-headed
        # refresh section to THIS turn's system message only;
        # an unchanged block leaves system_content untouched.
        _refresh = self._refresh_turn_memory(user_text)
        if _refresh:
            system_content += _refresh
        messages = ([{"role": "system", "content": system_content}]
                    + self._windowed_history())

        loop = asyncio.get_running_loop()
        final_text = ""
        tools_used = False
        # 6th-B06: attempted is not succeeded — see the local
        # path's note. The state guard and the empty-final
        # fallback consult tools_succeeded.
        tools_succeeded = False
        _delib_budget = _DeliberationBudget()  # Phase 2 B09:
        # the deliberation budget is INDEPENDENT of the tool
        # rounds below — a repair retry never consumes a tool
        # round, and tool rounds never starve repairs.
        # No-progress breaker (v5.44, B7): the same tool+args
        # twice in a row ends the turn with a progress report.
        from .local_client import _tool_progress_report
        from ..async_executor import run_blocking  # Phase 3 V1b: moved to core/voice/

        prev_signature: str | None = None
        accomplished: list[str] = []
        progress_break = False
        try:
            # Phase 2 B09: the tool-round loop is a while with
            # an explicit counter, so ONLY tool rounds consume
            # it — the deliberation `continue` below never
            # increments, and the while...else keeps the
            # exhaustion-summary branch exactly where the
            # for...else had it.
            tool_rounds = 0
            while tool_rounds < _max_tool_rounds():
                status, parsed = await loop.run_in_executor(
                    None, self._chat_completion, messages)
                if status != 200:
                    raise _GroqTurnError(
                        _classify_http_error(status, parsed))
                choice = None
                if isinstance(parsed, dict):
                    choices = parsed.get("choices") or []
                    if choices:
                        choice = choices[0]
                msg = choice.get("message") if isinstance(choice, dict) else None
                if not isinstance(msg, dict):
                    raise _GroqTurnError(_classify_http_error(200, None))

                tool_calls = msg.get("tool_calls") or []
                content = (msg.get("content") or "").strip()
                if not tool_calls:
                    # v5.52 deliberation guard (items 4 + 9): a
                    # no-tool reply that narrates process is
                    # WITHHELD — one corrective retry, then the
                    # persona line. Raw deliberation never ships.
                    # v5.53 (F4): the same discipline AFTER tools
                    # ran — a closing reply whose substance is
                    # the automation's own process (the
                    # "Automation Hiccups" essay) is withheld
                    # for an outcome-only retry.
                    if content:
                        from .. import deliberation as _delib  # Phase 3 V1b: moved to core/voice/

                        _pre = (not tools_used
                                and _delib.looks_like_deliberation(
                                    content))
                        _post = (tools_used
                                 and _delib.looks_like_process_summary(
                                     content))
                        if _pre or _post:
                            # Phase 2 B09: the repair retry
                            # draws on the INDEPENDENT
                            # deliberation budget, never on
                            # tool_rounds — and when it is
                            # spent, the persona line ships
                            # instead of a retry that cannot
                            # run.
                            if _delib_budget.try_consume():
                                messages.append(
                                    {"role": "assistant",
                                     "content": content})
                                messages.append(
                                    {"role": "user",
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
                assistant_msg = {
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": tool_calls,
                }
                messages.append(assistant_msg)
                self._history.append(assistant_msg)
                for call_index, call in enumerate(tool_calls):
                    signature = _call_signature(call)
                    if signature == prev_signature:
                        # Breaker (B7): the repeated call does NOT
                        # run again. It and any unrun calls from
                        # this reply still get tool messages (a
                        # skipped note) so the history's
                        # call/result pairing stays valid, then
                        # the turn ends with the progress report.
                        for skipped in tool_calls[call_index:]:
                            skip_msg = {
                                "role": "tool",
                                "tool_call_id": skipped.get("id", ""),
                                "content": ("Not run: the turn was "
                                            "stopped for repeating "
                                            "the same call."),
                            }
                            messages.append(skip_msg)
                            self._history.append(skip_msg)
                        final_text = _tool_progress_report(accomplished)
                        progress_break = True
                        break
                    tool_msg = await run_blocking(
                        self._run_tool_call, call)
                    # 6th-B06: _run_tool_call stashes this call's
                    # success verdict on the session.
                    if getattr(self, "_last_tool_success", False):
                        tools_succeeded = True
                    prev_signature = signature
                    accomplished.append(
                        f"{(call.get('function') or {}).get('name', '')}: "
                        f"{tool_msg.get('content', '')}")
                    messages.append(tool_msg)
                    self._history.append(tool_msg)
                tools_used = True
                if progress_break:
                    break
                # Phase 2 B09: only a completed tool round
                # consumes the tool budget — the deliberation
                # `continue` above never reaches this line.
                tool_rounds += 1
            else:
                # 6th-B02: rounds exhausted AFTER useful actions
                # get a structured partial report, not the generic
                # apology — or the generic fallback when nothing
                # actually completed.
                final_text = self._groq_exhaustion_summary(accomplished)
        except _GroqTurnError as e:
            _log_line(f"Turn failed: {e.status_line}")
            self._mark_groq_turn_failed()
            del self._history[turn_start:]
            self._notify_status(e.status_line)
            self._notify_status("LISTENING")
            return
        except (urllib.error.URLError, OSError) as e:
            _log_line(f"Turn failed (network): {str(e)[:300]}")
            self._mark_groq_turn_failed()
            del self._history[turn_start:]
            self._notify_status(NETWORK_ERROR_STATUS)
            self._notify_status("LISTENING")
            return
        except Exception as e:  # noqa: BLE001 — a dead turn must not kill the session
            _log_line(f"Turn failed: {str(e)[:300]}")
            self._mark_groq_turn_failed()
            del self._history[turn_start:]
            self._notify_status(f"ERROR: {str(e)[:300]}")
            self._notify_status("LISTENING")
            return

        # Brain guard (v5.3's teatime lesson; widened v5.43 B11):
        # a guarded state question answered with NO tool call is
        # fabricated by construction — replace it before it is
        # spoken, shown, or stored.
        # 6th-B06: succeeded, not merely attempted — a failed tool
        # is no evidence, so the forced repair still fires.
        if (not tools_succeeded and final_text
                and _state_guard_for(user_text) is not None):
            repaired = self._guard_state_answer(user_text)
            if repaired is not None:
                final_text = repaired

        if not final_text:
            # 6th-B06: an attempted-but-failed tool never earns a
            # bare "Done." — the no-tool empty case keeps it.
            if tools_used and not tools_succeeded:
                final_text = ("I ran into a problem with that step — "
                              "could you ask me again?")
            else:
                final_text = "Done."
        self._history.append({"role": "assistant", "content": final_text})
        self._cap_history()
        if self.on_transcript:
            self.on_transcript("assistant", final_text)
        memory_store.log_message("assistant", final_text)
        await self._speak(final_text)
        await self._maybe_auto_summarize()

    async def _maybe_auto_summarize(self):
        """Turn-count summary trigger (v5.45, B5): every N turns
        the conversation is summarised through this session's own
        Groq chat path and stored as an episode. Best-effort."""
        try:
            from ..memory import session_summary as _ss  # Phase 3 V1b: moved to core/voice/

            trigger = getattr(self, "_summary_trigger", None)
            if trigger is None:
                trigger = _ss.TurnSummaryTrigger()
                self._summary_trigger = trigger
            if not trigger.note_turn():
                return
            trigger.mark_fired()
            transcript = [dict(m) for m in self._history
                          if m.get("role") in ("user", "assistant")
                          and isinstance(m.get("content"), str)]
            if len(transcript) < 4:
                return

            async def _async_summarise(prompt: str) -> str:
                loop = asyncio.get_running_loop()
                status, parsed = await loop.run_in_executor(
                    None, self._chat_completion,
                    [{"role": "user", "content": prompt}])
                if status == 200 and isinstance(parsed, dict):
                    choices = parsed.get("choices") or []
                    if choices:
                        msg = choices[0].get("message") or {}
                        return str(msg.get("content") or "")
                return ""

            await _ss.generate_session_summary(
                transcript, summariser=_async_summarise)
        except Exception:  # noqa: BLE001 — summaries are a nicety
            pass

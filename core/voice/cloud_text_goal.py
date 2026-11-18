"""GroqSession C mixin (B39 part 2 of 2) — moved verbatim
from core/voice/cloud_text_client.py; the composed class lives
in cloud_text_client.py. No behaviour change."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from .. import injection  # Phase 3 V1b: moved to core/voice/
from ..brain.picker_history import render_picker_history  # B18
from ..memory.memory import memory_store  # Phase 3 V1b: moved to core/voice/
from ..funnel.tools_registry import tools  # Phase 3 V1b: moved to core/voice/; Phase 4 S1b INTENTIONAL: registry split to funnel

# INTENTIONAL (B39 part 2 of 2): methods moved verbatim
# from cloud_text_client.py resolve helper names
# through cloud_text_helpers (part 1's module).
from .cloud_text_helpers import (
    GROQ_CHAT_URL,
    VISION_FALLBACK_NOTE,
    _classify_http_error,
    _cost_preflight_or_raise,
    _cost_record,
    _groq_api_key,
    _groq_vision_enabled,
    _groq_vision_model,
    _log_line,
    _parse_groq_goal_reply,
    _post_json,
    groq_vision_payload,
)


class GroqSessionGoalMixin:

    async def run_plan(self, plan_id: str) -> str:
        """B15 driver entry (v5.45): walk a plan through
        core.brain.planner.run_plan; undeclared steps get ONE Groq chat
        call each (a native tool_call reply is taken as the pick,
        else a JSON content reply). Progress goes to the
        transcript."""
        from ..brain import planner as _planner  # Phase 3 V1b: moved to core/voice/
        from ..funnel import toolbox as _toolbox  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/

        async def _pick(step, plan):
            ranked = _toolbox.rank_tools(tools, step.get("text", ""), limit=8)
            if not ranked:
                return None
            prompt = (
                "Pick the ONE tool that performs this plan step by "
                "calling it. "
                f"Step: {step.get('text')}\n"
                f"Candidate tools: {', '.join(ranked)}")
            loop = asyncio.get_running_loop()
            try:
                status, parsed = await loop.run_in_executor(
                    None, self._chat_completion,
                    [{"role": "user", "content": prompt}])
            except Exception:  # noqa: BLE001
                return None
            if status != 200 or not isinstance(parsed, dict):
                return None
            choices = parsed.get("choices") or []
            if not choices:
                return None
            msg = choices[0].get("message") or {}
            calls = msg.get("tool_calls") or []
            if calls:
                fn = calls[0].get("function") or {}
                name = fn.get("name", "")
                raw = fn.get("arguments")
                args = raw if isinstance(raw, dict) else {}
                if isinstance(raw, str):
                    try:
                        args = json.loads(raw) if raw.strip() else {}
                    except ValueError:
                        args = {}
                return (name, args if isinstance(args, dict) else {})
            content = str(msg.get("content") or "")
            try:
                data = json.loads(content[content.index("{"):
                                          content.rindex("}") + 1])
                name = data.get("tool") or data.get("name") or ""
                args = data.get("arguments") or data.get("args") or {}
                if name:
                    return (name, args if isinstance(args, dict) else {})
            except (ValueError, AttributeError):
                pass
            return None

        def _emit(line: str):
            if self.on_transcript:
                self.on_transcript("assistant", line)

        # v5.49 (programme item 31): see local_client.run_plan —
        # the expect verifier is passed for session plan runs too.
        from ..desktop import uia as _uia  # Phase 3 V1b: moved to core/voice/; Phase 3 D4b: uia hub moved to core/desktop/
        return await _planner.run_plan(
            plan_id, pick_tool=_pick, emit=_emit,
            verify_expect=_uia.verify_expectation)

    async def run_goal(self, goal_text: str, plan=None,
                       interruption_check=None, search=None) -> str:
        """Goal driver entry (v5.49, programme item 29): drive an
        open-ended goal through core.brain.planner.run_goal, with ONE
        Groq chat call per step (a native tool_call reply is
        taken as the pick, else a JSON content reply — the same
        parsing discipline as run_plan's picker). The picker
        receives the goal, the live UIA snapshot rendered as text
        (plus the grounding marks table when one is on record)
        and the attempt history (retry hints included).
        Expectations are verified by
        core.desktop.uia.verify_expectation (item 31). Progress lines go
        to the transcript.

        R7-3: ``search`` (None / True / GoalSearchConfig) is passed
        through to planner.run_goal — None (the default) keeps the
        greedy single pick; a config enables the bounded beam
        search over candidate next actions. The voice brain's
        bridged goals inherit it through this method.

        Voice-brain adoption: ``interruption_check`` is an optional
        caller check (the voice wrapper's coordinator-cancel /
        session-stop check). When supplied it combines with the
        supply's stop check — either firing halts the loop at the
        next step boundary. With none supplied, behaviour is
        byte-identical: the supply check alone, exactly as before."""
        from ..brain import planner as _planner  # Phase 3 V1b: moved to core/voice/
        from ..funnel import toolbox as _toolbox  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/
        from ..desktop import uia as _uia  # Phase 3 V1b: moved to core/voice/; Phase 3 D4b: uia hub moved to core/desktop/

        def _emit(line: str):
            if self.on_transcript:
                self.on_transcript("assistant", line)

        def _render_observation(observation) -> str:
            if isinstance(observation, dict):
                text = (f"Foreground window: "
                        f"{observation.get('fg') or 'unknown'}\n"
                        f"Focused control: "
                        f"{observation.get('focus') or 'unknown'}")
            elif observation is None:
                text = ("Desktop observation: unavailable (the UIA "
                        "probe has no eyes here).")
            else:
                text = f"Desktop observation: {observation}"
            try:
                from ..desktop.vision import marks_table_text  # Phase 3 V1b: moved to core/voice/ (D4c: vision hub moved to core/desktop/)

                table = marks_table_text(_uia.last_marks())
            except Exception:  # noqa: BLE001 — marks are a bonus
                table = ""
            return f"{text}\n{table}" if table else text

        def _render_history(history) -> str:
            # B18: the summarised full history — one shared helper
            # (core/brain/picker_history.py), not a second slice.
            # Short goals render exactly as before.
            return render_picker_history(history)

        async def _pick_action(goal, observation, history):
            obs_text = _render_observation(observation)
            try:
                ranked = _toolbox.rank_tools(
                    tools, f"{goal} {obs_text}", limit=8)
            except Exception:  # noqa: BLE001 — candidates are a hint
                ranked = []
            prompt = (
                "Work toward this goal, ONE action at a time, by "
                "calling the ONE tool that takes the next step.\n"
                f"Goal: {goal}\n\n"
                f"Current desktop:\n{obs_text}\n\n"
                f"History (summary of all steps so far, then the "
                f"latest 8 verbatim, latest last; a retry record "
                f"means the previous action changed nothing — "
                f"follow its hint and choose a different approach):\n"
                f"{_render_history(history)}\n\n"
                f"Candidate tools: {', '.join(ranked) or '(none ranked)'}\n"
                'If the goal is complete, reply with ONLY JSON: '
                '{"done": "<summary>"}. An action reply may also '
                'carry "expect": "<what should be visible '
                'afterwards>". Use a candidate or another '
                "registered tool name exactly; do not invent tools.")
            loop = asyncio.get_running_loop()
            try:
                status, parsed = await loop.run_in_executor(
                    None, self._chat_completion,
                    [{"role": "user", "content": prompt}])
            except Exception:  # noqa: BLE001
                return None
            if status != 200 or not isinstance(parsed, dict):
                return None
            choices = parsed.get("choices") or []
            if not choices:
                return None
            msg = choices[0].get("message") or {}
            calls = msg.get("tool_calls") or []
            if calls:
                fn = calls[0].get("function") or {}
                name = fn.get("name", "")
                raw = fn.get("arguments")
                args = raw if isinstance(raw, dict) else {}
                if isinstance(raw, str):
                    try:
                        args = json.loads(raw) if raw.strip() else {}
                    except ValueError:
                        args = {}
                if not name:
                    return None
                try:
                    valid = {d["name"] for d in tools.get_declarations()}
                except Exception:  # noqa: BLE001
                    valid = None
                if valid is not None and name not in valid:
                    return None  # unknown tool → None, per item 29
                return {"tool": name,
                        "args": args if isinstance(args, dict) else {}}
            return _parse_groq_goal_reply(str(msg.get("content") or ""))

        # Code-Perfection Build 2: supply the Build 2 wiring
        # kwargs from the client's current observation (goal
        # context), the shared PERSISTENT playbook, the existing
        # failsafe stop signal, and the real control snapshot
        # callable (per-step UIA scan; None on hosts without
        # automation, where the loop's honest-skip note applies
        # — see goal_client_supply).
        from ..brain import goal_client_supply as _supply  # Phase 3 V1b: moved to core/voice/

        # Voice-brain adoption: combine an optional caller
        # interruption check with the supply's stop check. Either
        # firing halts the loop at the next step boundary (J7
        # dict shape: {"reason": ..., "resume": False}).
        # With none supplied, _effective_check IS the supply check
        # (same object, same behaviour — no wrapper in the way).
        if interruption_check is None:
            _effective_check = _supply.stop_interruption_check
        else:
            _voice_check = interruption_check

            async def _effective_check():
                try:
                    _req = _voice_check()
                    import inspect as _inspect
                    if _inspect.isawaitable(_req):
                        _req = await _req
                except Exception:  # noqa: BLE001 — broken voice check falls through
                    _req = None
                if isinstance(_req, dict):
                    return _req
                return _supply.stop_interruption_check()


        # Wave 6 integration (AGENT caller, WAVE6_WIRING_SPEC §2.1):
        # an optional caller-supplied split plan runs through the
        # AGENT coordinator instead of a single planner.run_goal.
        # Without a plan, behaviour is EXACTLY today's: the
        # staged-plan holder is consulted only when no explicit
        # plan was passed, and only for the SAME goal text — a
        # staged plan never hijacks an unrelated goal run, and a
        # staged plan inside a running loop waits for the next
        # top-level goal run. The coordinator never supplies its
        # own picker (the session's pick_action/observe path is
        # bound below), never clears or re-arms a trip, and
        # never retries a sub-goal (zero coordinator retries).
        from .. import mission_jobs as _mission_jobs  # Phase 3 V1b: moved to core/voice/

        _effective_plan = plan
        if _effective_plan is None:
            _staged = _mission_jobs.consume_plan_for_goal(goal_text)
            if _staged is not None:
                _effective_plan = _staged.get("plan")
        if _effective_plan is not None:
            _plan_data = _effective_plan
            _plan_list = (_plan_data.get("plan")
                          if isinstance(_plan_data, dict) else _plan_data)
            try:
                import functools as _functools

                from .. import agent_coordination as _agent  # Phase 3 V1b: moved to core/voice/
                from ..resilience import resilience_gate as _resilience  # Phase 3 R1: moved to core/resilience/ (relative: the absolute voice_assistant form does not resolve under the PYTHONPATH=. test layout and the except below would silently drop the stop-check) (V1b: one level deeper in core/voice/)

                def _stop_check():
                    # The coordinator's stop_check takes a halt
                    # reason string ("" = healthy); the gate's
                    # is_tripped() is a bool, so the wrapper
                    # reports the gate's own reason text.
                    try:
                        if _resilience.is_tripped():
                            return (_resilience.trip_reason()
                                    or "resilience tripped")
                    except Exception:  # noqa: BLE001 — broken probe
                        return ""
                    return ""

                _bound = _functools.partial(
                    _planner.run_goal, pick_action=_pick_action,
                    emit=_emit, verify_expect=_uia.verify_expectation)
                _coordinator = _agent.AgentCoordinator(
                    run_goal=_bound, stop_check=_stop_check, emit=_emit)
                _funnel_kwargs = {
                    "playbook": _supply.shared_playbook(),
                    "interruption_check": _effective_check,
                    "control_snapshot": _supply.current_control_snapshot,
                    "goal_context": _supply.current_goal_context(),
                    "search": search,
                }
                _job = await _coordinator.run_job(
                    goal_text, _plan_list, funnel_kwargs=_funnel_kwargs)
                return _job.report
            except Exception as exc:  # noqa: BLE001 — honest report
                return (f"Multi-agent job refused: {exc} "
                        f"(no sub-goal was run on this refusal)")

        # UI rethink Build 4 (§4.5): plan approval for direct
        # multi-step requests — one extra model call asks for
        # the FULL plan up front, and when at least two steps
        # are approvable the owner approves the plan ONCE in
        # the dialog instead of one popup per step. Any
        # failure, decline or timeout returns None and the
        # goal runs exactly as today (picker alone, per-call
        # asks). The helper skips the model call entirely
        # when no GUI handler is registered.
        async def _ask_plan(goal):
            try:
                from .direct_plan import (
                    parse_plan_steps as _parse_steps,
                    plan_prompt as _plan_prompt)
            except Exception:  # noqa: BLE001 — no plan
                return []
            try:
                ranked = _toolbox.rank_tools(
                    tools, str(goal or ""), limit=8)
            except Exception:  # noqa: BLE001 — candidates are a hint
                ranked = []
            try:
                valid = {d["name"] for d in tools.get_declarations()}
            except Exception:  # noqa: BLE001 — no validation, no plan
                return []
            loop = asyncio.get_running_loop()
            try:
                status, parsed = await loop.run_in_executor(
                    None, self._chat_completion,
                    [{"role": "user",
                      "content": _plan_prompt(goal, ranked)}])
            except Exception:  # noqa: BLE001 — model failure: no plan
                return []
            if status != 200 or not isinstance(parsed, dict):
                return []
            choices = parsed.get("choices") or []
            if not choices:
                return []
            msg = choices[0].get("message") or {}
            return _parse_steps(str(msg.get("content") or ""), valid)

        _direct_pair = None
        try:
            from .direct_plan import plan_direct_goal as _plan_direct

            _direct_pair, _direct_outcome = await _plan_direct(
                goal_text, ask_plan=_ask_plan, emit=_emit)
        except Exception:  # noqa: BLE001 — no plan: today's behaviour
            _direct_pair = None
        return await _planner.run_goal(
            goal_text, pick_action=_pick_action, emit=_emit,
            verify_expect=_uia.verify_expectation,
            goal_context=_supply.current_goal_context(),
            playbook=_supply.shared_playbook(),
            interruption_check=_effective_check,
            control_snapshot=_supply.current_control_snapshot,
            plan_approval=_direct_pair,
            search=search)

    async def send_text(self, text: str):
        """Sends manual typed user text through the Groq brain.

        Phase 1 (F04): returns a delivery result instead of a
        silent no-op — {"status": "dropped", "reason": ...} when
        no session is live, {"status": "accepted"} once the turn
        ran. Callers that ignored the old None return are
        unaffected."""
        if not self.is_connected:
            return {"status": "dropped", "reason": "not connected"}
        # Genuine typed user text lifts the injection taint (v5.3);
        # bracketed VYUK-internal injections do not.
        if not (text or "").lstrip().startswith("["):
            injection.clear_taint()
        if self.on_transcript:
            self.on_transcript("user", text)
        memory_store.log_message("user", text)
        async with self._turn_lock:
            await self._brain_turn(text)
        return {"status": "accepted"}

    def _vision_completion(self, image_bytes: bytes, mime_type: str,
                           question: str) -> tuple[int, Any]:
        """One blocking VISION chat completion (executor only) —
        through the same _post_json seam as every Groq call."""
        payload = groq_vision_payload(
            image_bytes, mime_type, question,
            history=self._windowed_history(),
            system_prompt=self._system_prompt)
        # MN-2.8: same cap gate + ledger row as the text path.
        _cost_preflight_or_raise(_groq_vision_model(), question)
        status, parsed = _post_json(GROQ_CHAT_URL, _groq_api_key(), payload)
        if status == 200:
            _cost_record(parsed, _groq_vision_model(), question)
        return status, parsed

    def _last_user_question(self) -> str:
        """The context a frame turn rides on when the caller gave
        no explicit question: the most recent genuine user line
        in history, else the standing screen question."""
        for msg in reversed(self._history):
            if msg.get("role") == "user" \
                    and isinstance(msg.get("content"), str) \
                    and msg["content"].strip():
                return msg["content"].strip()
        return ("What is on my screen? Describe what you see and "
                "help with whatever I'm looking at.")

    async def _vision_turn(self, image_bytes: bytes, mime_type: str,
                           question: str) -> bool:
        """One vision-model turn about a frame. True when the
        vision answer was delivered (history, transcript, speech
        — the _brain_turn division of labour); False on ANY
        failure (vision disabled, no key, HTTP error, transport
        failure, an unreadable/empty reply) so the caller
        degrades to OCR."""
        if not _groq_vision_enabled() or not _groq_api_key():
            return False
        loop = asyncio.get_running_loop()
        try:
            status, parsed = await loop.run_in_executor(
                None, self._vision_completion,
                image_bytes, mime_type, question)
        except (urllib.error.URLError, OSError) as e:
            _log_line(f"Vision turn failed (network): {str(e)[:300]}")
            return False
        except Exception as e:  # noqa: BLE001 — degrade, never crash
            _log_line(f"Vision turn failed: {str(e)[:300]}")
            return False
        if status != 200:
            _log_line(f"Vision turn failed: "
                      f"{_classify_http_error(status, parsed)}")
            return False
        text = ""
        if isinstance(parsed, dict):
            choices = parsed.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                text = (msg.get("content") or "").strip()
        if not text:
            _log_line("Vision turn failed: unreadable/empty reply")
            return False
        # History carries a TEXT note of the frame turn — the
        # image part itself never enters the stored history (the
        # text model couldn't read it back anyway).
        self._history.append({
            "role": "user",
            "content": f"[The user shared their screen. {question}]",
        })
        self._history.append({"role": "assistant", "content": text})
        self._cap_history()
        if self.on_transcript:
            self.on_transcript("assistant", text)
        memory_store.log_message("assistant", text)
        await self._speak(text)
        return True

    async def send_image_frame(self, image_bytes: bytes,
                               mime_type: str = "image/jpeg",
                               question: str = ""):
        """A brain turn about a shared frame (v5.45, E5a): the
        frame is sent to the vision model (_groq_vision_model)
        as a real image turn, with the user's question/context
        riding along (`question` when the caller has one — the
        watch-my-screen framing, a tool's ask — else the most
        recent user line from history).

        On ANY vision failure the turn degrades to the pre-E5a
        OCR path (LocalLiveSession._describe_frame — the same
        read the local brain uses) with VISION_FALLBACK_NOTE
        prepended, so the reply SAYS vision fell back to OCR
        instead of silently pretending.

        Returns a delivery status (v5.53, F8): "delivered"
        when the vision model took the turn, "degraded" when
        the turn fell back to the OCR describe (the model
        answered from text — callers must NOT report the image
        as shown), "no-session" when not connected. Before F8
        every outcome returned None and the frame sink claimed
        vision for the degraded case too."""
        if not self.is_connected:
            _log_line(
                "Frame delivery skipped: not connected — "
                "the frame was not shown to the model.")
            return "no-session"
        from .local_client import LocalLiveSession

        async with self._turn_lock:
            asked = (question or "").strip() or self._last_user_question()
            if await self._vision_turn(image_bytes, mime_type, asked):
                _log_line("Frame delivered to the vision model.")
                return "delivered"
            loop = asyncio.get_running_loop()
            prompt = await loop.run_in_executor(
                None, LocalLiveSession._describe_frame, image_bytes)
            await self._brain_turn(f"[{VISION_FALLBACK_NOTE}] {prompt}")
            _log_line(
                "Frame vision turn failed; the turn degraded "
                "to the OCR describe path (the model answered "
                "from text, not the image).")
            return "degraded"

# Batch 4 (plan §3.3): plan-goal (run_plan/run_goal), moved verbatim
# from core/voice/local_client.py. INTENTIONAL: imports below are the
# subset of the old module's import block that this group's code uses.
import asyncio
from ..brain.picker_history import render_picker_history  # B18
from ..funnel.tools_registry import tools
from .local_client_toolparse import _extract_tool_call, _parse_goal_action_reply


class LocalLiveSessionPlanGoalMixin:

    async def run_plan(self, plan_id: str) -> str:
        """B15 driver entry (v5.45): walk a plan through
        core.brain.planner.run_plan, picking tools for undeclared steps
        with ONE chat call per step through this session's active
        backend. Progress lines go to the transcript.

        v5.49 (programme item 36): on the lite tier the first
        plan/goal attempt per session warns and requires an
        explicit yes first — see _lite_plan_gate."""
        from ..brain import planner as _planner  # Phase 3 V1b: moved to core/voice/
        from ..funnel import toolbox as _toolbox  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/

        async def _pick(step, plan):
            ranked = _toolbox.rank_tools(tools, step.get("text", ""), limit=8)
            if not ranked:
                return None
            prompt = (
                "Pick the ONE tool that performs this plan step. "
                f"Step: {step.get('text')}\n"
                f"Candidate tools: {', '.join(ranked)}\n"
                'Reply with ONLY JSON: {"tool": "<name>", '
                '"arguments": {}} using a candidate name exactly.')
            loop = asyncio.get_running_loop()
            try:
                if self._backend == "ollama":
                    reply = await loop.run_in_executor(
                        None, self._ollama_chat,
                        [{"role": "user", "content": prompt}], [])
                    text = str(((reply or {}).get("message") or {})
                               .get("content") or "")
                else:
                    text = await loop.run_in_executor(
                        None, self._llama_chat,
                        [{"role": "user", "content": prompt}])
            except Exception:  # noqa: BLE001
                return None
            parsed = _extract_tool_call(text)
            if parsed is None:
                return None
            name, args = parsed
            return (name, args) if name in ranked else (ranked[0], {})

        def _emit(line: str):
            if self.on_transcript:
                self.on_transcript("assistant", line)

        # v5.49 (programme item 36): lite-tier gate — a refusal
        # returns before the planner is touched, so nothing runs.
        gate = await self._lite_plan_gate(_emit)
        if gate is not None:
            return gate
        # v5.49 (programme item 31): session run_plan passes the
        # expect verifier too, not just run_goal — steps declaring
        # a free-text `expect` get verdicts recorded; steps without
        # one behave byte-identically to pre-v5.49.
        from ..desktop import uia as _uia  # Phase 3 V1b: moved to core/voice/; Phase 3 D4b: uia hub moved to core/desktop/
        return await _planner.run_plan(
            plan_id, pick_tool=_pick, emit=_emit,
            verify_expect=_uia.verify_expectation)

    async def run_goal(self, goal_text: str, plan=None,
                       interruption_check=None, search=None) -> str:
        """Goal driver entry (v5.49, programme item 29): drive an
        open-ended goal through core.brain.planner.run_goal, with ONE
        chat call per step through this session's active backend.
        The picker receives the goal, the live UIA snapshot
        rendered as text (plus the grounding marks table when one
        is on record) and the attempt history (retry hints
        included), and answers with one action / a done summary /
        nothing. Expectations are verified by
        core.desktop.uia.verify_expectation (item 31). Progress lines go
        to the transcript.

        R7-3: ``search`` (None / True / GoalSearchConfig) is passed
        through to planner.run_goal — None (the default) keeps the
        greedy single pick; a config enables the bounded beam
        search over candidate next actions.

        Voice-brain adoption: ``interruption_check`` is an optional
        caller check (the voice wrapper's coordinator-cancel /
        session-stop check). When supplied it combines with the
        supply's stop check — either firing halts the loop at the
        next step boundary. With none supplied, behaviour is
        byte-identical: the supply check alone, exactly as before.

        v5.49 (programme item 36): the lite-tier gate applies to
        goals exactly as to plans — see _lite_plan_gate."""
        from ..brain import planner as _planner  # Phase 3 V1b: moved to core/voice/
        from ..funnel import toolbox as _toolbox  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/
        from ..desktop import uia as _uia  # Phase 3 V1b: moved to core/voice/; Phase 3 D4b: uia hub moved to core/desktop/

        def _emit(line: str):
            if self.on_transcript:
                self.on_transcript("assistant", line)

        gate = await self._lite_plan_gate(_emit)
        if gate is not None:
            return gate

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
                "Work toward this goal, ONE action at a time.\n"
                f"Goal: {goal}\n\n"
                f"Current desktop:\n{obs_text}\n\n"
                f"History (summary of all steps so far, then the "
                f"latest 8 verbatim, latest last; a retry record "
                f"means the previous action changed nothing — "
                f"follow its hint and choose a different approach):\n"
                f"{_render_history(history)}\n\n"
                f"Candidate tools: {', '.join(ranked) or '(none ranked)'}\n"
                'Reply with ONLY one JSON object: '
                '{"tool": "<name>", "arguments": {...}} for the '
                'next action — optionally with "expect": "<what '
                'should be visible afterwards>" — or '
                '{"done": "<summary>"} when the goal is complete. '
                "Use a candidate or another registered tool name "
                "exactly; do not invent tools.")
            loop = asyncio.get_running_loop()
            try:
                if self._backend == "ollama":
                    reply = await loop.run_in_executor(
                        None, self._ollama_chat,
                        [{"role": "user", "content": prompt}], [])
                    text = str(((reply or {}).get("message") or {})
                               .get("content") or "")
                else:
                    text = await loop.run_in_executor(
                        None, self._llama_chat,
                        [{"role": "user", "content": prompt}])
            except Exception:  # noqa: BLE001
                return None
            return _parse_goal_action_reply(str(text or ""))

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
                if self._backend == "ollama":
                    reply = await loop.run_in_executor(
                        None, self._ollama_chat,
                        [{"role": "user",
                          "content": _plan_prompt(goal, ranked)}], [])
                    text = str(((reply or {}).get("message") or {})
                               .get("content") or "")
                else:
                    text = await loop.run_in_executor(
                        None, self._llama_chat,
                        [{"role": "user",
                          "content": _plan_prompt(goal, ranked)}])
            except Exception:  # noqa: BLE001 — model failure: no plan
                return []
            return _parse_steps(str(text or ""), valid)

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

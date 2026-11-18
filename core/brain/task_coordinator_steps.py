"""Split of task_coordinator: step/receipt dataclasses + format_receipt."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepDef:
    """One validated coordinator step.

    ``handle_id`` (§7 consumers batch 3): an optional item-1
    handle this step acts on. At dispatch it injects into the
    tool's handle arg (menu_action / file_dialog_action ->
    window_handle_id, system_surface_action -> target_handle_id;
    see core.desktop.s7_threading.STEP_HANDLE_ARG) when the step's args
    do not already carry that arg — explicit args always win.
    Tools outside that map (including drag_drop_action, whose
    two handle args one id cannot name without guessing) get
    no injection; a handle_id there is ignored, never guessed.

    Menu steps (§7 consumers batch 4): a step with tool
    "menu_action" names a menu path instead of spelling raw
    clicks. Build-time validation: action must be "list" or
    "invoke" (anything else is rejected, never dispatched), and
    an invoke step must carry a non-empty path (string or list
    — the engine's own parse rules apply again at dispatch).
    """

    tool: str
    args: dict = field(default_factory=dict)
    label: str = ""
    on_fail: str = "halt"
    # B4 (plan steps can jump): a goto target, or None. The
    # canonical planner shape is on_fail {"goto": n} (1-based
    # step number, planner_storage / planner_plans v5.49);
    # from_mapping stores it here 0-based and keeps on_fail
    # as the string "goto" (the ledger's string field). A
    # jump is a controlled re-entry into the action loop at
    # a named step — never a second engine, never silent:
    # every jump is recorded (Task._jumps), bounded twice
    # (per-step strikes at _RETRY_STRIKE_LIMIT, global budget
    # max_jumps), and refused outright when its target is a
    # D4/C-14 step (see plan_approval.is_jump_refused).
    goto: int | None = None
    when: dict | None = None  # {"tool": str, "args": dict} probe, or None
    expect: str = ""  # free-text expectation, or "" when none declared
    handle_id: str = ""  # optional item-1 handle for this step
    # Fix 9 (voice routines run as coordinator tasks): a B12 result
    # check, {"status": ..., "contains": ...} or None — the same
    # shape planner routines declare (core/brain/planner_plans.py
    # _normalise_check / check_result). A step that declares one
    # is only a success when its executor result meets it; the
    # evaluation lives in task_coordinator_execution._run_step
    # (mirroring check_result, parity-pinned in
    # tests/test_complex_task.py), so a check-bearing routine
    # keeps its legacy meaning instead of silently dropping it.
    check: dict | None = None
    # B15 (mid-task clarifying questions): a named question this
    # step needs answered before it can run, or None. Shape:
    # {"question": str, "options": [str, ...],
    # "answer_key": str}. The coordinator pauses with a
    # _pending_question record (fourth pause shape, beside
    # _pending_approval / _pending_monitor / the B14 budget
    # pause) instead of dispatching; the human answers through
    # the GUI pause surface and resolve_question records the
    # answer and re-runs the step with it injected into args
    # under answer_key. "options" may be empty (free-text
    # only); "answer_key" defaults to "answer". An answer is
    # information, never approval — it never approves a step,
    # and the step still gates normally on resume.
    ask: dict | None = None
    # B19 (parallel steps): the parallel-group label this
    # step belongs to, or "" when the step runs sequentially
    # (the default — a plan with no labels behaves exactly
    # as today). Consecutive steps sharing the same
    # non-empty label form ONE group: they run together
    # through the SAME _run_step, each with its own expect,
    # landing verdict, strike, and confirm stop. The label
    # is a DECLARATION, never an inference — the coordinator
    # never guesses that two steps are independent (a step
    # that reads a file another step writes is sequential
    # unless the plan says otherwise). Two runs with the
    # same label are two groups, never one: a group is a
    # maximal run of consecutive steps, so a sequential
    # step between two runs breaks the group. Group rules
    # (validate_parallel_groups, plan level): at least 2
    # members, at most MAX_PARALLEL_STEPS, and no member
    # consequential (the tree's own D4/C-14 set — a
    # parallel group must never be a way around the confirm
    # gate or the plan-approval digest).
    parallel: str = ""

    @classmethod
    def from_mapping(cls, mapping) -> "StepDef":
        """Validate a raw step mapping. Raises ValueError, never guesses."""
        if not isinstance(mapping, dict):
            raise ValueError("coordinator step is not a mapping")
        tool = str(mapping.get("tool", "")).strip()
        if not tool:
            raise ValueError("coordinator step has no tool")
        args = mapping.get("args", {}) or {}
        if not isinstance(args, dict):
            raise ValueError(f"coordinator step {tool!r} args are not a mapping")
        on_fail_raw = mapping.get("on_fail", "halt")
        goto = None
        if isinstance(on_fail_raw, dict):
            # B4: the planner's canonical jump shape
            # {"goto": n} (1-based step number). Exactly one
            # key, an int >= 1 — anything else is rejected
            # at build time, never guessed. Range (n inside
            # the plan) is checked at plan level by
            # validate_step_jumps, since one step cannot
            # know the plan's length.
            if set(on_fail_raw.keys()) != {"goto"}:
                raise ValueError(
                    f"coordinator step {tool!r} has unsupported "
                    f"on_fail {on_fail_raw!r} (only 'halt', 'skip' "
                    f"or {{'goto': <step number>}})")
            target = on_fail_raw.get("goto")
            if isinstance(target, bool) or not isinstance(target, int) \
                    or target < 1:
                raise ValueError(
                    f"coordinator step {tool!r} has invalid "
                    f"on_fail {on_fail_raw!r} — 'goto' must be "
                    f"a step number >= 1")
            on_fail = "goto"
            goto = target - 1
        else:
            on_fail = str(on_fail_raw or "halt").strip().lower()
            if on_fail not in ("halt", "skip"):
                raise ValueError(
                    f"coordinator step {tool!r} has unsupported on_fail "
                    f"{on_fail!r} (only 'halt', 'skip' or "
                    f"{{'goto': <step number>}})")
        when = mapping.get("when")
        if when is not None:
            if not isinstance(when, dict) or not str(when.get("tool", "")).strip():
                raise ValueError(
                    f"coordinator step {tool!r} has a malformed when probe")
            when_args = when.get("args", {}) or {}
            if not isinstance(when_args, dict):
                raise ValueError(
                    f"coordinator step {tool!r} when-probe args are not a mapping")
            when = {"tool": str(when["tool"]).strip(), "args": when_args}
        expect = mapping.get("expect", "")
        if expect is None:
            expect = ""
        if not isinstance(expect, str):
            # A dict expect is planner's B12 result check, not a
            # free-text expectation — never silently reinterpreted.
            raise ValueError(
                f"coordinator step {tool!r} has a non-string expect "
                f"{type(expect).__name__!r} (free-text strings only)")
        expect = expect.strip()
        label = str(mapping.get("label", "") or mapping.get("description", "") or tool)
        handle_id = mapping.get("handle_id", "")
        if handle_id is None:
            handle_id = ""
        if not isinstance(handle_id, str):
            raise ValueError(
                f"coordinator step {tool!r} has a non-string handle_id "
                f"{type(handle_id).__name__!r} (item-1 handle ids only)")
        handle_id = handle_id.strip()
        # Fix 9: a B12 result check, when present, must be a
        # mapping with string-valued "status"/"contains" — the
        # shape planner's _normalise_check produces. Anything
        # else is rejected at build time, never silently
        # reinterpreted (a dict check is not a free-text expect,
        # and a non-mapping check is not a check at all).
        check = mapping.get("check")
        if check is not None:
            if not isinstance(check, dict):
                raise ValueError(
                    f"coordinator step {tool!r} has a non-mapping "
                    f"check {type(check).__name__!r}")
            for _key in ("status", "contains"):
                if _key in check and check[_key] is not None \
                        and not isinstance(check[_key], str):
                    raise ValueError(
                        f"coordinator step {tool!r} check "
                        f"{_key!r} must be a string")
            check = {k: v for k, v in check.items()
                     if k in ("status", "contains") and v not in ("", None)}
        # B15: a clarifying question, when present, must be a
        # mapping with a non-empty "question" string; "options"
        # is an optional list of non-empty strings (empty means
        # free-text only); "answer_key" is an optional
        # non-empty string (default "answer") naming the arg
        # the recorded answer is injected into. Anything else
        # is rejected at build time, never silently
        # reinterpreted — a malformed question is a malformed
        # step, and a step that cannot say what it needs
        # cannot ask for it.
        ask = mapping.get("ask")
        if ask is not None:
            if not isinstance(ask, dict):
                raise ValueError(
                    f"coordinator step {tool!r} has a non-mapping "
                    f"ask {type(ask).__name__!r}")
            question = ask.get("question", "")
            if not isinstance(question, str) or not question.strip():
                raise ValueError(
                    f"coordinator step {tool!r} has an ask "
                    f"without a non-empty 'question'")
            options = ask.get("options", []) or []
            if not isinstance(options, list):
                raise ValueError(
                    f"coordinator step {tool!r} has a non-list "
                    f"ask 'options'")
            clean_options = []
            for _opt in options:
                if not isinstance(_opt, str) or not _opt.strip():
                    raise ValueError(
                        f"coordinator step {tool!r} has a "
                        f"non-string or empty ask option")
                _opt = _opt.strip()
                if _opt not in clean_options:
                    clean_options.append(_opt)
            answer_key = ask.get("answer_key", "") or "answer"
            if not isinstance(answer_key, str) \
                    or not answer_key.strip():
                raise ValueError(
                    f"coordinator step {tool!r} has a non-string "
                    f"or empty ask 'answer_key'")
            ask = {"question": question.strip(),
                   "options": clean_options,
                   "answer_key": answer_key.strip()}
        # B19: a parallel-group label, when present, must be
        # a string — anything else is rejected at build time,
        # never silently reinterpreted (a non-string label is
        # a malformed step, and a step that cannot say which
        # group it belongs to cannot run in one). "" / absent
        # / None means sequential, exactly like a step that
        # never had a label. Group-level rules (size,
        # consequential members) live in
        # validate_parallel_groups — one step cannot know its
        # neighbours, so from_mapping cannot check them.
        parallel = mapping.get("parallel", "")
        if parallel is None:
            parallel = ""
        if not isinstance(parallel, str):
            raise ValueError(
                f"coordinator step {tool!r} has a non-string "
                f"parallel label {type(parallel).__name__!r} "
                f"(group labels are strings only)")
        parallel = parallel.strip()
        if tool == "menu_action":
            # §7 consumers batch 4: a menu step names its path at
            # build time. An invoke step with no usable path is
            # rejected here, never dispatched to guess; a list
            # step needs no path. The engine re-validates at
            # dispatch (empty-path refusal), so this check only
            # fails fast on malformed steps.
            action = str(args.get("action", "") or "").strip().lower()
            if action not in ("list", "invoke"):
                raise ValueError(
                    f"coordinator step 'menu_action' has unsupported "
                    f"action {action!r} (list or invoke only)")
            if action == "invoke":
                path = args.get("path")
                if isinstance(path, str):
                    usable = bool(path.strip())
                elif isinstance(path, (list, tuple)):
                    usable = any(str(s or "").strip() for s in path)
                else:
                    usable = False
                if not usable:
                    raise ValueError(
                        "coordinator step 'menu_action' invoke needs "
                        "a non-empty path")
        return cls(tool=tool, args=args, label=label, on_fail=on_fail,
                   goto=goto, when=when, expect=expect,
                   handle_id=handle_id, check=check, ask=ask,
                   parallel=parallel)


def max_jumps(n_steps: int) -> int:
    """The global jump budget for a plan of n_steps steps.

    B4: the legacy loop's formula, one home
    (planner_routines.run_routine: max_jumps =
    len(actions) * 3 + 10) — the two loops' contracts stay
    identical, so a goto routine behaves the same whichever
    loop runs it. The per-step strike bound
    (_RETRY_STRIKE_LIMIT, 3) binds first for any loop that
    repeats one step; this budget binds for long chains
    whose steps keep succeeding between failures (a success
    resets a step's strikes, so strikes alone cannot bound
    them). Zero steps -> the +10 floor still applies.
    """
    return int(n_steps or 0) * 3 + 10


def validate_step_jumps(steps) -> None:
    """Plan-level jump validation: every goto target must
    land inside the plan. Raises ValueError, never guesses.

    B4: the range check the planner does at parse time
    (planner_plans.py:365, planner_routines.py:93) — a
    single step cannot know the plan's length, so
    StepDef.from_mapping cannot do this; callers that
    assemble a whole plan (new_task, handle_turn,
    ledger rebuild) call this once the list is built.
    A target step's D4/C-14 eligibility is NOT checked
    here — it is checked at jump time, when the target
    is known and the refusal can name the live reason
    (a plan whose recovery is refused still runs its
    earlier steps and halts honestly at the jump).
    """
    steps = list(steps or [])
    for index, step in enumerate(steps):
        target = getattr(step, "goto", None)
        if target is None:
            continue
        if not 1 <= target + 1 <= len(steps):
            raise ValueError(
                f"coordinator step {getattr(step, 'label', '') or index + 1!r} "
                f"has on_fail goto {target + 1}, outside a "
                f"{len(steps)}-step plan")


# B19 (parallel steps): the most steps that may run in one
# parallel group. Four is the bound, not a suggestion: each
# branch holds a funnel worker thread (and may hold a confirm
# dialog), so an unbounded group is unbounded resource use.
# A declared group larger than this is a malformed plan,
# rejected at build time — never silently split into waves
# (a wave would change the join semantics: a wave-2 branch
# pending while wave-1 outcomes are recorded is a second
# pause shape, not this one).
MAX_PARALLEL_STEPS = 4


def parallel_groups(steps) -> list:
    """The parallel groups in a plan, in order.

    B19: a group is a maximal run of consecutive steps
    sharing the same non-empty ``parallel`` label. Each
    group is a list of step indices (0-based). Steps with
    no label belong to no group. Two runs with the same
    label are two groups — a sequential step between two
    runs breaks the group, so the label never reaches
    across it. Pure: reads labels only, never infers
    independence from tool names, args, or order.
    """
    groups = []
    steps = list(steps or [])
    i = 0
    while i < len(steps):
        label = str(getattr(steps[i], "parallel", "") or "").strip()
        if not label:
            i += 1
            continue
        j = i + 1
        while j < len(steps) and str(
                getattr(steps[j], "parallel", "") or "").strip() == label:
            j += 1
        groups.append(list(range(i, j)))
        i = j
    return groups


def validate_parallel_groups(steps) -> None:
    """Plan-level parallel-group validation. Raises ValueError.

    B19: the group rules, checked once the whole list is
    built (one step cannot know its neighbours, so
    StepDef.from_mapping cannot check them). In order:
      1. Every group has at least 2 members — a group of
         one is a malformed declaration (the plan said
         "these run together" and named one step; either
         the planner forgot a member or the label is
         stray — either way it is rejected, never silently
         run sequentially).
      2. No group has more than MAX_PARALLEL_STEPS members
         — bounded threads, bounded dialogs.
      3. No member is consequential — the tree's own D4/C-14
         refusal set (plan_approval.is_jump_refused, read
         here, never copied): a parallel group must never
         be a way around the confirm gate or the
         plan-approval digest. A consequential step runs
         sequentially, always — a plan that declares it
         parallel is malformed and fails at build with the
         reason named, never runs. run_command is refused
         here by its own rule (below), not via
         is_jump_refused: D-19 removed it from
         _NEVER_PLAN_KINDS for plan approval, but the
         refusal here is about CONCURRENCY, not approval —
         a shell step running alongside other steps is a
         consequential step in a group, whatever its
         approval. Plan approval and jumps still admit
         run_command on exact digest (the dispatch check
         governs); only parallel membership refuses it.
    A step's ask / when-probe / check / handle_id / goto
    are NOT refused here — a group member that needs an
    answer, a probe, or a jump runs through the same
    _run_step as any step, and a pending branch is handled
    by the group's join (pending placeholder outcome),
    not by refusing the group.
    """
    steps = list(steps or [])
    for members in parallel_groups(steps):
        label = str(getattr(steps[members[0]], "parallel", "") or "")
        if len(members) < 2:
            step = steps[members[0]]
            raise ValueError(
                f"coordinator parallel group {label!r} has only "
                f"1 step ({getattr(step, 'label', '') or step.tool!r}) — "
                f"declare at least 2 members, or drop the label")
        if len(members) > MAX_PARALLEL_STEPS:
            raise ValueError(
                f"coordinator parallel group {label!r} has "
                f"{len(members)} steps — at most "
                f"{MAX_PARALLEL_STEPS} may run together")
        try:
            from .. import plan_approval as _pa
        except Exception:  # noqa: BLE001 — undecidable: refuse the group
            raise ValueError(
                f"coordinator parallel group {label!r} cannot be "
                f"checked for consequential members — group refused")
        for index in members:
            step = steps[index]
            # Click-anywhere Batch 0 (D-19): run_command left
            # _NEVER_PLAN_KINDS for plan approval, but a parallel
            # group must still never contain a shell step — the
            # refusal is about CONCURRENCY, not approval (see
            # rule 3 above). (Neighbour catch: the D-19 removal
            # let is_jump_refused admit run_command members — the
            # plan does not name this second-order effect; this
            # restores the pinned safety property without
            # touching D-19.)
            if str(getattr(step, "tool", "") or "") == "run_command":
                raise ValueError(
                    f"coordinator step "
                    f"{getattr(step, 'label', '') or step.tool!r} "
                    f"in parallel group {label!r} is a shell step "
                    f"(run_command) — shell steps run sequentially, "
                    f"never in parallel")
            refused, reason = _pa.is_jump_refused(
                step.tool, getattr(step, "args", {}) or {})
            if refused:
                raise ValueError(
                    f"coordinator step {getattr(step, 'label', '') or step.tool!r} "
                    f"in parallel group {label!r} is consequential "
                    f"({reason}) — consequential steps run "
                    f"sequentially, never in parallel")


@dataclass
class StepOutcome:
    """One executed (or skipped) step, as the receipt records it."""

    tool: str
    label: str
    status: str
    kind: str
    message: str


# B24 (second-opinion OFF surfacing): ONE honest OFF line from
# the missing-leg names the companion holder returns
# (CompanionSessionsHolder.missing_legs). The line is built in
# exactly one place, so the receipt, the ledger, the Activity
# line and the home briefing can never disagree on the wording.
# Rejected: turning the check ON without both legs (the pair is
# built only when both legs are real — the design, not a gap);
# inventing a Groq key or a local model (BYOK — never invented);
# a popup per task (the OFF line belongs in the receipt/ledger/
# Activity/briefing, not in his face); hiding the OFF state in a
# log file only (the log is not where he looks).
_SECOND_OPINION_OFF_REASONS = {
    "groq key": "no Groq key",
    "groq session": "no Groq session",
    "local model": "no local model",
    "local model loading": "local model still loading",
    "companion holder not started": "companion holder not started",
    "companion pair not attached": "companion pair not attached",
}


def second_opinion_off_line(missing) -> str:
    """One OFF line naming the missing second-opinion leg(s).

    ``missing`` is the companion holder's plain-word list
    (missing_legs). "groq key" + "local model" read exactly
    "Second opinion: OFF — no Groq key, no local model"; a
    single missing leg reads with just its reason. An empty
    or unrecognisable list is a caller bug — the pair is
    ready, so no OFF line is owed — and reads honestly as
    "missing leg unknown" rather than inventing a leg.
    """
    names = [str(m or "").strip() for m in (missing or [])]
    names = [n for n in names if n]
    if not names:
        return "Second opinion: OFF — missing leg unknown"
    reasons = [_SECOND_OPINION_OFF_REASONS.get(n, n) for n in names]
    return "Second opinion: OFF — " + ", ".join(reasons)


@dataclass
class TaskReceipt:
    """The terminal record of one coordinator task."""

    task_id: str
    classification: str
    title: str
    state: str
    steps: list
    attempted: int
    completed: int
    failed: int
    skipped: int
    remaining: list
    reason: str
    error_kind: str
    # Verification-phase verdicts, one per expected step that
    # reached the phase: {"tool", "label", "expect", "verdict",
    # "reason"}. Verdicts are "met" / "not-met" /
    # "unverifiable" (the engine's verdicts) or "skipped" (the
    # step itself did not run, so its expectation was never
    # checkable — not an engine verdict). Empty when the task
    # ran no phase. A retried step carries exactly ONE entry
    # (its final verdict); the intermediate misses live in
    # "retries" below, so the entry shape never changes.
    verification: list = field(default_factory=list)
    # Retry-on-not-met history, one event per not-met verdict
    # (plus a met-reset event when a retried step finally meets
    # its expectation): {"step_index", "tool", "label",
    # "verdict", "reason", "strike", "retried"}. Empty when no
    # step was ever retried.
    retries: list = field(default_factory=list)
    # B10: the goal check's verdict — ONE check of the goal
    # text against a fresh snapshot, run after the step
    # verification phase: {"verdict": "met" | "not-met" |
    # "unverifiable", "reason": str}. "met" means the goal
    # state was observed; "not-met" means the goal state was
    # observable and absent (the task fails, it does not
    # complete); "unverifiable" means the goal could not be
    # judged (no observable state, or the goal text names
    # nothing groundable) — never faked to "met". A task that
    # never ran the check carries the "no goal check
    # recorded" reason. See Task._goal_verdict and
    # TurnCoordinatorExecutionMixin._check_goal.
    goal_verdict: dict = field(default_factory=dict)
    # Second-opinion auto-invoke history, one event per checked
    # step: {"step_index", "tool", "label", "verdict", "action",
    # "reason", "answer_a", "answer_b", "question"}. Verdicts
    # are "agree" / "disagree" / "unavailable" (the
    # second_opinion module's) or "error" (the check itself
    # failed); action is "proceeded" (agreement on YES/YES
    # only) or "paused" (anything else — the human decides).
    # Empty when no step was ever checked.
    second_opinions: list = field(default_factory=list)
    # B24: the second-opinion OFF record — ONE line per task,
    # written at the first check that would have run when the
    # companion pair was None: {"missing": [...], "line": str}.
    # Empty when the check ran (or no step ever needed one):
    # a task whose check ran never carries an OFF line, and a
    # task with no high-stakes step never needed a check.
    second_opinion_off: dict = field(default_factory=dict)
    # B4: jump history, one event per jump decision (taken or
    # refused), in order: {"from_index", "from_tool",
    # "from_label", "reason", "to_index", "to_label",
    # "strike", "jumped"}. "reason" is the failure that
    # caused the jump (or the refusal reason); "strike" is
    # the from-step's consecutive jump-ending failure count
    # at the decision; "jumped" False means the jump was
    # refused (strike limit, budget spent, D4/C-14 target)
    # and the task halted instead. A failed step's outcome
    # lives here when a jump rewinds past it (the outcomes
    # list is truncated to the jump target, so it stays
    # positionally aligned) — the jump event is the only
    # record of that failure, and it is never dropped.
    jumps: list = field(default_factory=list)
    # B14: the task's spend — what it spent (elapsed wall
    # time, steps run, executions, cloud calls by leg, local
    # calls, and cost where measured, honest-unknown where
    # not). Shape: see task_budget.spend_snapshot. Empty
    # when the receipt was built without a task (a task that
    # ran always carries one).
    spend: dict = field(default_factory=dict)
    # B15: answered clarifying questions, one entry per
    # answered question, in answer order: {"step_index",
    # "tool", "label", "question", "answer", "option"}.
    # "option" is the chosen option when the answer matches
    # one of the step's declared options, else None (free
    # text). Empty when no question was ever asked. An
    # answer is information, never approval — it records
    # what the human said, never a permission.
    questions: list = field(default_factory=list)
    # B22: the initial decomposition, when the task's steps
    # came from the brain breaking a free-text goal into
    # sub-goals (never a declared plan): {"subgoals":
    # [names], "brain": "cloud" | "local", "steps": int}.
    # Empty when the task ran a declared plan — a declared
    # plan was never decomposed, and the receipt never
    # claims it was.
    decomposition: dict = field(default_factory=dict)
    # B22: re-plan history, one event per re-plan attempt
    # that produced a new plan, in order: {"attempt": int,
    # "tried": [step labels tried so far], "failed_reason":
    # str (the goal-check reason that triggered it),
    # "subgoals": [new sub-goal names], "steps": int (new
    # step count), "first_step_index": int (where the new
    # steps were appended), "brain": "cloud" | "local"}.
    # Empty when the task never re-planned. A failed
    # decomposition (no brain, malformed plan, invalid step)
    # is NOT an entry here — it ends the task, and the
    # receipt's reason names it instead.
    replans: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "classification": self.classification,
            "title": self.title,
            "state": self.state,
            "steps": [dict(s) for s in self.steps],
            "attempted": self.attempted,
            "completed": self.completed,
            "failed": self.failed,
            "skipped": self.skipped,
            "remaining": list(self.remaining),
            "reason": self.reason,
            "error_kind": self.error_kind,
            "verification": [dict(v) for v in self.verification],
            "goal_verdict": dict(self.goal_verdict),
            "retries": [dict(r) for r in self.retries],
            "second_opinions": [dict(e) for e in self.second_opinions],
            # B24: the receipt dict carries the second-opinion
            # OFF record alongside the histories — {} when the
            # check ran or never needed to run. (The neighbour
            # run caught this omission: the dataclass field and
            # format_receipt both had it, but as_dict() — the
            # serialized form — did not, so the pin test failed.)
            "second_opinion_off": dict(self.second_opinion_off),
            "jumps": [dict(j) for j in self.jumps],
            "questions": [dict(q) for q in self.questions],
            "decomposition": dict(self.decomposition),
            "replans": [dict(r) for r in self.replans],
            "spend": {
                "elapsed_s": self.spend.get("elapsed_s", 0.0),
                "steps_run": self.spend.get("steps_run", 0),
                "executions": self.spend.get("executions", 0),
                "cloud_calls": [dict(e) for e in
                                self.spend.get("cloud_calls", [])],
                "local_calls": self.spend.get("local_calls", 0),
                "local_seconds": self.spend.get("local_seconds"),
                "cost_usd": self.spend.get("cost_usd"),
                "cost_known": self.spend.get("cost_known", True),
                "cost_note": self.spend.get("cost_note", ""),
            },
        }


def format_receipt(receipt: TaskReceipt) -> str:
    """Plain-text receipt for the chat/system-message path."""
    lines = [
        f"Task {receipt.task_id[:8]} — {receipt.classification}: {receipt.title}",
        f"State: {receipt.state} "
        f"(attempted {receipt.attempted}, completed {receipt.completed}, "
        f"failed {receipt.failed}, skipped {receipt.skipped})",
    ]
    marks = {"success": "✓", "failure": "✗", "refused": "⊘", "skipped": "⊘"}
    for step in receipt.steps:
        mark = marks.get(step["status"], "○")
        lines.append(f"  {mark} {step['label']} — {step['status']}"
                     + (f" ({step['kind']})" if step["kind"] else ""))
    if receipt.remaining:
        lines.append("Remaining: " + ", ".join(receipt.remaining))
    if receipt.reason:
        lines.append(f"Reason: {receipt.reason}")
    for verdict in receipt.verification:
        lines.append(f"  Verification: {verdict['label']} — "
                     f"{verdict['verdict']}"
                     + (f": {verdict['reason']}"
                        if verdict.get("reason") else ""))
    if receipt.goal_verdict:
        lines.append(f"  Goal: {receipt.goal_verdict.get('verdict', '')}"
                     + (f" — {receipt.goal_verdict.get('reason', '')}"
                        if receipt.goal_verdict.get("reason") else ""))
    for event in receipt.retries:
        lines.append(f"  Retry: {event.get('label', '')} — "
                     f"{event.get('verdict', '')} "
                     f"(strike {event.get('strike', 0)})"
                     + (" — retried" if event.get("retried")
                        else " — halted"))
    for event in receipt.second_opinions:
        lines.append(f"  Second opinion: {event.get('label', '')} — "
                     f"{event.get('verdict', '')} "
                     f"({event.get('action', '')})")
    # B24: the second-opinion OFF line — one line per task,
    # written at the first check that would have run when the
    # companion pair was None. It appears only when the
    # record carries one, so a task whose check ran reads
    # exactly as before; it never claims the check ran.
    _off = getattr(receipt, "second_opinion_off", None) or {}
    _off_line = _off.get("line", "") if isinstance(_off, dict) else ""
    if _off_line:
        lines.append(f"  {_off_line}")
    for event in receipt.jumps:
        # B4: the legacy loop's ↪ mark — a jump (or a refused
        # jump) reads as a jump, never as a silent re-run.
        if event.get("jumped"):
            lines.append(f"  ↪ {event.get('from_label', '')} — "
                         f"{event.get('reason', '')}; jumping to "
                         f"step {int(event.get('to_index', 0) or 0) + 1} "
                         f"({event.get('to_label', '')})")
        else:
            lines.append(f"  ↪ {event.get('from_label', '')} — "
                         f"{event.get('reason', '')}; jump refused")
    # B15: answered questions — what the human was asked and
    # what they said, so the receipt itself shows the answer
    # that fed the step (never a guess: an unanswered
    # question has no entry here, it has a pause instead).
    for entry in receipt.questions:
        lines.append(f"  Question: {entry.get('label', '')} — "
                     f"{entry.get('question', '')} → "
                     f"{entry.get('answer', '')}")
    # B22: the decomposition and the re-plans — what the
    # brain broke the goal into, and what each re-plan tried
    # after the goal check said not-met. A task that ran a
    # declared plan carries neither, and the receipt never
    # claims it was decomposed.
    if receipt.decomposition:
        _subs = ", ".join(receipt.decomposition.get("subgoals", []))
        lines.append(f"  Decomposed into: {_subs}"
                     + (f" ({receipt.decomposition.get('brain', '')})"
                        if receipt.decomposition.get("brain") else ""))
    for event in receipt.replans:
        _subs = ", ".join(event.get("subgoals", []))
        lines.append(f"  Re-planned (attempt {event.get('attempt', 0)}): "
                     f"{event.get('failed_reason', '')} → {_subs}"
                     + (f" ({event.get('brain', '')})"
                        if event.get("brain") else ""))
    # B14: what the task spent — always present on a receipt
    # built from a task (empty on a receipt built bare). Cost
    # is a measured sum or "unknown", never a fabricated zero;
    # local calls are time, never dollars.
    if receipt.spend:
        _sp = receipt.spend
        _calls = sum(int(e.get("calls", 0) or 0)
                     for e in _sp.get("cloud_calls", []))
        _cost = _sp.get("cost_usd")
        _cost_s = (f"${_cost:.4f}"
                   if isinstance(_cost, (int, float))
                   else "unknown")
        lines.append(f"  Spent: {_sp.get('elapsed_s', 0):.1f}s wall, "
                     f"{_sp.get('steps_run', 0)} steps, "
                     f"{_sp.get('executions', 0)} executions, "
                     f"{_calls} cloud calls (cost {_cost_s}), "
                     f"{_sp.get('local_calls', 0)} local calls")
        if _sp.get("cost_note"):
            lines.append(f"  Cost note: {_sp['cost_note']}")
    return "\n".join(lines)

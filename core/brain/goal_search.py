# goal_search.py — R7-3: a bounded beam search over candidate next
# actions for run_goal (maitrix-org/llm-reasoners pattern, own code).
#
# Pattern source: maitrix-org/llm-reasoners (Apache-2.0, 2,348 stars)
# — its BeamSearch algorithm (beam nodes, reward lists, argmax
# sampling, cumulative reward aggregation) is re-implemented here
# as VYUK's own code. The library itself is NOT depended on, for
# two measured reasons (2026-11-10, from the 1.0.2 sdist + PyPI):
#   (1) TORCH HARD GATE. llm-reasoners 1.0.2's requires_dist pulls
#       torch + transformers + datasets + bitsandbytes + fairscale +
#       optimum + peft + ninja + sentencepiece + google-generativeai
#       + anthropic + openai + tarski, and reasoners/base.py imports
#       torch and transformers at TOP LEVEL — any `import reasoners`
#       requires torch. The tree is deliberately torch-free
#       (B-R4-7 / B-R5-5: torch ~280-490 MB, hard gate). The 70 KB
#       wheel figure is meaningless for the installed closure.
#   (2) UNIMPLEMENTABLE WORLD MODEL. BeamSearch calls
#       world.step(state, action) for EVERY candidate at EVERY depth
#       and expects the NEXT STATE back — i.e. the world model must
#       SIMULATE the action's effect. A desktop-control agent has no
#       simulator for arbitrary desktop state: applying an action
#       means EXECUTING a tool (side effects, irreversible,
#       trust-gated), which the search must never do. The library's
#       own DefaultWorldModel.step just appends the action to a list
#       (the state IS the action sequence) — honest for maths
#       word-problems, a fiction for a desktop. VYUK's world model
#       therefore never simulates: the state is (observation,
#       history), step() appends the candidate to the HYPOTHETICAL
#       history only, and the observation is unchanged.
#
# What the search IS: at each goal step, instead of taking the
# picker's single greedy action, the search asks the picker for up
# to beam_width candidates, scores each with the tree's OWN static
# signals (tool known, args valid against the declared schema,
# declares a verifiable expect, not a repeat of a just-failed
# action), and feeds the BEST into the EXISTING single pick. The
# picker contract does NOT change (still exactly one action per
# pick), the trust path does NOT change (verification, confirm
# gates, strikes, budgets all run as before), the search NEVER
# executes anything, NEVER bypasses a gate, NEVER writes code, and
# uses NO new LLM judge — the reward is the tree's own verification
# signal, never a model opinion. Depth defaults to 1 (score the
# next action only); depth 2 is the hard cap, and it only asks the
# picker to imagine the step after a hypothetical first action —
# the hypothetical is labelled in the history it is shown, and it
# NEVER enters the real history.
#
# OFF by default: run_goal's `search` kwarg is None unless the
# caller passes a GoalSearchConfig (or True for defaults). With
# none supplied, run_goal behaves byte-identically to before.

import json
from dataclasses import dataclass, field

# INTENTIONAL: _known_tools / _parse_goal_action live in the
# planner families; imported, never redefined (skills split B41).
from .planner_goalhelpers import _parse_goal_action
from .planner_plans import _known_tools

# Scoring weights (documented, not tuned — each component is a
# tree signal with a clear direction; the penalties dominate
# because avoiding a known-bad pick matters more than preferring
# a good one):
#   known tool +0.5 / unknown tool -1.0 / registry unreadable 0.0
#   args valid +0.5 / args invalid -1.0 / no schema or validator 0.0
#   declares a verifiable expect +0.5
#   repeats the most-recent retry tool -1.0
#   tool in any earlier retry/halted event -0.5
_W_KNOWN = 0.5
_W_UNKNOWN = -1.0
_W_ARGS_OK = 0.5
_W_ARGS_BAD = -1.0
_W_EXPECT = 0.5
_W_REPEAT_LAST = -1.0
_W_REPEAT_EARLIER = -0.5

MAX_DEPTH_HARD_CAP = 2
MAX_BEAM_WIDTH = 8


@dataclass(frozen=True)
class GoalSearchConfig:
    """Bounds for the goal search. All bounds are enforced, never
    advisory: beam_width caps the candidates per level, max_depth
    caps the lookahead (hard cap 2 — there is no simulator for a
    deeper world), max_picker_calls caps the picker calls per
    step (a step that would need more stops expanding and returns
    the best-so-far). reward_aggregator is 'cumulative' (sum of
    per-action scores along the path) — the only aggregator; the
    library's 'last'/'mean' options are not offered, because a
    path's value here is the sum of its steps' verifiability."""

    beam_width: int = 3
    max_depth: int = 1
    max_picker_calls: int = 12
    reward_aggregator: str = "cumulative"

    def __post_init__(self):
        if not 1 <= self.beam_width <= MAX_BEAM_WIDTH:
            raise ValueError(
                f"beam_width must be 1..{MAX_BEAM_WIDTH}, got "
                f"{self.beam_width}")
        if not 1 <= self.max_depth <= MAX_DEPTH_HARD_CAP:
            raise ValueError(
                f"max_depth must be 1..{MAX_DEPTH_HARD_CAP}, got "
                f"{self.max_depth}")
        if self.max_picker_calls < self.beam_width:
            raise ValueError(
                "max_picker_calls must be >= beam_width, got "
                f"{self.max_picker_calls} < {self.beam_width}")
        if self.reward_aggregator != "cumulative":
            raise ValueError(
                "reward_aggregator must be 'cumulative', got "
                f"{self.reward_aggregator!r}")


def normalise_search(value):
    """Normalise run_goal's `search` kwarg: None stays None (search
    OFF — the safe default), True means defaults, a
    GoalSearchConfig is used as-is (already validated), anything
    else is a caller error (TypeError, fail fast before any step
    runs — a bad search flag must never silently degrade to
    greedy)."""
    if value is None:
        return None
    if value is True:
        return GoalSearchConfig()
    if isinstance(value, GoalSearchConfig):
        return value
    raise TypeError(
        "search must be None, True, or a GoalSearchConfig, got "
        f"{type(value).__name__}")


@dataclass(frozen=True)
class GoalState:
    """The search state: the live observation (opaque — the search
    never interprets it), the history tuple, and the depth. The
    observation is NEVER changed by step(): there is no simulator,
    so a hypothetical state keeps the live observation and only
    gains a hypothetical history record."""

    observation: object
    history: tuple = ()
    depth: int = 0


class GoalWorld:
    """The WorldModel role (llm-reasoners' WorldModel, ported):
    init_state / step / is_terminal. step() appends the candidate
    to the HYPOTHETICAL history only — it never executes, never
    touches the observation, never writes to the real history.
    The hypothetical record is labelled in the record itself so
    the picker (and any human reading the history) cannot mistake
    it for a real step."""

    def __init__(self, max_depth: int):
        self.max_depth = max_depth

    def init_state(self, observation, history) -> GoalState:
        return GoalState(observation=observation,
                         history=tuple(history or ()), depth=0)

    def step(self, state: GoalState, action: dict):
        """Return (next_state, aux). The next state's observation
        is the SAME object (no simulation); the history gains one
        hypothetical record; the depth rises by one. aux carries
        the candidate for the reward's bookkeeping."""
        tool, _, _, _ = _parse_goal_action(action)
        hypo = {"event": "hypothetical",
                "step": state.depth + 1,
                "tool": tool,
                "args": action.get("args") if isinstance(action, dict)
                        else {},
                "note": ("search-only: this step was imagined, never "
                         "executed")}
        nxt = GoalState(observation=state.observation,
                        history=state.history + (hypo,),
                        depth=state.depth + 1)
        return nxt, {"action": action}

    def is_terminal(self, state: GoalState) -> bool:
        return state.depth >= self.max_depth


@dataclass
class ScoringContext:
    """What the scorer needs, read once per search call: the known
    tool names (None when the registry cannot be read — the signal
    is then skipped, never guessed) and the declared parameter
    schemas by tool name ({} when unreadable)."""

    known_tools: object = None  # set[str] | None
    schemas: dict = field(default_factory=dict)


def build_scoring_context() -> ScoringContext:
    """Read the registry once for this search call. Every failure
    degrades to no-signal (None / {}), never raises — the search
    must not break a goal because the registry was unreadable."""
    known = _known_tools()
    schemas = {}
    try:
        from ..funnel.tools_registry import tools as registry
        for decl in registry.get_declarations():
            name = decl.get("name")
            schema = decl.get("parameters")
            if name and isinstance(schema, dict):
                schemas[name] = schema
    except Exception:  # noqa: BLE001 — no schema signal, not a failure
        schemas = {}
    return ScoringContext(known_tools=known, schemas=schemas)


def _validate_args(tool: str, args, schemas: dict):
    """(error, available): error is None when the args pass the
    declared schema, a string when they fail, and available is
    False when there is no schema or no validator to judge by
    (the signal is then skipped). The validator is imported
    lazily — the funnel package is heavy and the brain must not
    pay for it at import time."""
    schema = schemas.get(tool) if isinstance(schemas, dict) else None
    if not isinstance(schema, dict) or not schema:
        return None, False
    try:
        from ..funnel.tools_registry_validators import (
            _validate_args_against_schema)
    except Exception:  # noqa: BLE001 — validator unavailable
        return None, False
    try:
        err = _validate_args_against_schema(tool, args, schema)
    except Exception:  # noqa: BLE001 — a validator bug is not a verdict
        return None, False
    return err, True


def _repeat_penalty(tool: str, history) -> float:
    """The retry-ladder memory, read off the history records the
    loop already writes: the most recent retry event's tool is
    penalised hardest (it just changed nothing — the ladder hint
    says choose a different approach); any earlier retry/halted
    tool is penalised half. History records carry no args, so
    the penalty is by tool name only — stated, not hidden."""
    if not tool:
        return 0.0
    penalty = 0.0
    last_retry_tool = None
    earlier = set()
    # Iterate NEWEST-first: the first retry met IS the most
    # recent retry (forward order would take the OLDEST retry
    # instead — the penalty would land on the wrong tool).
    for rec in reversed(list(history or ())):
        if not isinstance(rec, dict):
            continue
        event = rec.get("event")
        if event == "retry":
            if last_retry_tool is None:
                last_retry_tool = rec.get("tool")
            elif rec.get("tool"):
                earlier.add(rec.get("tool"))
        elif event == "halted" and rec.get("tool"):
            earlier.add(rec.get("tool"))
    if last_retry_tool is not None and tool == last_retry_tool:
        return _W_REPEAT_LAST
    if tool in earlier:
        return _W_REPEAT_EARLIER
    return penalty


@dataclass
class ScoredCandidate:
    """One candidate with its score and the per-component
    breakdown (the breakdown is the audit trail — a score is
    never a bare number)."""

    action: dict
    score: float
    breakdown: dict


def score_candidate(action, ctx: ScoringContext, history) -> object:
    """Score one candidate action with the tree's own static
    signals. Returns a ScoredCandidate, or None when the action
    is malformed (a malformed pick would halt the goal — the
    search drops it instead of ever picking it; if every
    candidate is malformed the search returns the first one
    unchanged so run_goal's existing malformed-action halt fires
    exactly as greedy would)."""
    tool, args, expect_text, _words = _parse_goal_action(action)
    if not tool:
        return None
    breakdown = {}
    score = 0.0
    known = ctx.known_tools if isinstance(ctx, ScoringContext) else None
    if known is None:
        breakdown["known_tool"] = 0.0
    elif tool in known:
        breakdown["known_tool"] = _W_KNOWN
        score += _W_KNOWN
    else:
        breakdown["known_tool"] = _W_UNKNOWN
        score += _W_UNKNOWN
    schemas = ctx.schemas if isinstance(ctx, ScoringContext) else {}
    err, available = _validate_args(tool, args, schemas)
    if not available:
        breakdown["args_valid"] = 0.0
    elif err is None:
        breakdown["args_valid"] = _W_ARGS_OK
        score += _W_ARGS_OK
    else:
        breakdown["args_valid"] = _W_ARGS_BAD
        score += _W_ARGS_BAD
    if expect_text:
        breakdown["declares_expect"] = _W_EXPECT
        score += _W_EXPECT
    else:
        breakdown["declares_expect"] = 0.0
    rep = _repeat_penalty(tool, history)
    breakdown["repeat_penalty"] = rep
    score += rep
    return ScoredCandidate(action=action, score=score,
                           breakdown=breakdown)


def _canonical(action) -> str:
    """Dedupe key for a candidate: tool + canonical JSON args.
    Two samples of the same action are one candidate — the beam
    holds distinct actions, not duplicate samples."""
    tool, args, _, _ = _parse_goal_action(action)
    try:
        return json.dumps({"tool": tool, "args": args},
                          sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 — unserialisable args: fall back
        return repr((tool, args))


def _dedupe_best(scored_list):
    """Dedupe scored candidates by canonical action, keeping the
    HIGHEST-scoring variant per key (two samples of the same
    (tool, args) — one with an expect, one without — are one
    candidate; the verifiable variant wins). First-seen order is
    preserved, so ties still resolve to the picker's own order."""
    by_key = {}
    for scored in scored_list:
        key = _canonical(scored.action)
        if key not in by_key or scored.score > by_key[key].score:
            by_key[key] = scored
    return list(by_key.values())


class BeamNode:
    """One beam node (llm-reasoners' BeamSearchNode, ported):
    the state, the action that reached it, its reward, and the
    parent link the trace walks back up."""

    def __init__(self, state, action, reward, parent=None):
        self.state = state
        self.action = action
        self.reward = reward
        self.parent = parent
        self.children = []

    def add_child(self, child):
        self.children.append(child)

    def get_trace(self):
        """The (action, state, reward) path from the root to here,
        root first."""
        node, path = self, []
        while node is not None:
            path.append((node.action, node.state, node.reward))
            node = node.parent
        return path[::-1]


class GoalSearch:
    """The SearchConfig role (llm-reasoners' SearchConfig, ported):
    get_actions asks the picker for candidates; reward scores one
    with the tree's static signals. get_actions is async because
    VYUK's picker is async — a deliberate, documented deviation
    from the library's sync get_actions. There is no fast_reward:
    the reward IS cheap (no LLM call), so the optimisation hook
    is not needed."""

    def __init__(self, pick_action, goal, config: GoalSearchConfig,
                 scoring_ctx: ScoringContext):
        self.pick_action = pick_action
        self.goal = goal
        self.config = config
        self.scoring_ctx = scoring_ctx
        self.picker_calls = 0
        self.picker_failed = False

    async def get_actions(self, state: GoalState, limit: int):
        """Ask the picker for up to `limit` distinct tool-action
        candidates at this state. A None reply is a failed sample
        (skipped, not a candidate); a {"done": ...} reply is NOT
        a tool action (the search scores tool actions only — a
        done claim is handled by the caller before the beam
        exists); malformed replies are dropped. Every picker call
        counts against the step budget."""
        out = []
        # Attempt cap: a picker that only ever returns the same
        # few actions must not burn the whole step budget trying
        # to fill the beam — allow `limit` distinct plus two
        # wasted calls (dups / Nones), then stop with what we
        # have. The step budget (max_picker_calls) still caps
        # the total. Duplicates are collected and _dedupe_best
        # keeps the highest-scoring variant of each action.
        attempts = 0
        while (len(_dedupe_best(out)) < limit and attempts < limit + 2
               and self.picker_calls < self.config.max_picker_calls):
            attempts += 1
            self.picker_calls += 1
            try:
                raw = self.pick_action(self.goal, state.observation,
                                       list(state.history))
            except Exception:
                # A picker failure is a picker failure: mark it so
                # the caller re-raises instead of falling back.
                self.picker_failed = True
                raise
            if hasattr(raw, "__await__"):
                try:
                    raw = await raw
                except Exception:
                    self.picker_failed = True
                    raise
            if raw is None:
                continue
            if isinstance(raw, dict) and "done" in raw:
                continue
            scored = score_candidate(raw, self.scoring_ctx,
                                     state.history)
            if scored is None:
                continue
            out.append(scored)
        return _dedupe_best(out)

    def reward(self, state: GoalState, action: dict):
        """(score, breakdown) for one action at this state — the
        tree's static signals, no execution, no LLM call."""
        scored = score_candidate(action, self.scoring_ctx,
                                 state.history)
        if scored is None:
            return 0.0, {"malformed": True}
        return scored.score, scored.breakdown


class BeamSearch:
    """The search algorithm (llm-reasoners' BeamSearch, ported):
    beam nodes, cumulative reward aggregation, argmax top-k
    sampling, action dedup. Deviations, all documented: the
    first level is SEEDED by the caller (the first pick is made
    before the beam exists so "done"/None are handled exactly as
    greedy handles them); get_actions is async (the picker is
    async); the reward aggregator is cumulative only; the world
    never simulates (GoalWorld.step appends a hypothetical
    record, nothing more)."""

    def __init__(self, config: GoalSearchConfig):
        self.config = config

    async def __call__(self, world: GoalWorld, search: GoalSearch,
                       seed, root_state: GoalState):
        """seed: the scored first-level candidates (list of
        ScoredCandidate) from the caller's first pick(s); root_state
        is the live initial state the seed nodes step from (its
        observation is the live one — a None observation here
        would poison the depth-2 expansion). Returns (best_action,
        report) — the best FIRST action of the best path, plus the
        audit report. The best action is always one the picker
        itself produced."""
        root = BeamNode(state=root_state, action=None, reward=0.0)
        # Level 1 is the seed: each scored candidate is a child
        # of the root, with its cumulative reward = its score.
        cur_beam = []
        for scored in seed:
            nxt, _aux = world.step(root.state, scored.action)
            node = BeamNode(state=nxt, action=scored.action,
                            reward=scored.score, parent=root)
            root.add_child(node)
            cur_beam.append((node, [scored.score], scored.score))
        depth_reached = 1
        # Deeper levels expand the surviving beam through the
        # hypothetical world — only while the depth cap and the
        # picker-call budget allow.
        while depth_reached < self.config.max_depth and cur_beam:
            if search.picker_calls >= self.config.max_picker_calls:
                break
            new_beam = []
            for node, reward_list, _cum in cur_beam:
                if world.is_terminal(node.state):
                    continue
                cands = await search.get_actions(
                    node.state, self.config.beam_width)
                for scored in cands:
                    nxt, _aux = world.step(node.state, scored.action)
                    child = BeamNode(state=nxt, action=scored.action,
                                     reward=scored.score, parent=node)
                    node.add_child(child)
                    new_list = reward_list + [scored.score]
                    new_beam.append((child, new_list, sum(new_list)))
            if not new_beam:
                break
            new_beam.sort(key=lambda x: x[2], reverse=True)
            cur_beam = new_beam[:self.config.beam_width]
            depth_reached += 1
        if not cur_beam:
            return None, {"depth_reached": depth_reached,
                          "candidates": 0}
        cur_beam.sort(key=lambda x: x[2], reverse=True)
        best_node, _lst, best_reward = cur_beam[0]
        trace = best_node.get_trace()
        # The action to execute is the FIRST action of the best
        # path (trace[0] is the root's None action).
        first_action = None
        for action, _state, _reward in trace:
            if action is not None:
                first_action = action
                break
        return first_action, {
            "depth_reached": depth_reached,
            "candidates": len(cur_beam),
            "best_score": best_reward,
            "best_tool": (_parse_goal_action(first_action)[0]
                          if first_action else None),
        }


async def search_pick(config: GoalSearchConfig, pick_action, goal,
                      observation, history):
    """The Reasoner entry: pick the next action for one goal step
    with the beam search. Returns (action, report). The action is
    ALWAYS one the picker produced (or None / {"done": ...} exactly
    as the picker returned them) — the search only CHOOSES among
    the picker's own candidates, never invents one.
    A search-internal error (anything that is not a picker
    failure) falls back to the first candidate — the greedy pick
    — with the fallback recorded in the report: the search is an
    optimisation, and a broken optimisation must degrade to the
    proven greedy path, never halt a goal the greedy pick would
    have completed. A picker failure propagates (run_goal halts
    as "picker failed", exactly as greedy would)."""
    report = {"candidates_generated": 0, "distinct": 0, "picker_calls": 0,
              "depth_reached": 0, "best_score": None, "best_tool": None,
              "fallback": None, "scores": []}
    ctx = build_scoring_context()
    search = GoalSearch(pick_action, goal, config, ctx)
    world = GoalWorld(config.max_depth)
    # The first pick, exactly as greedy makes it — "done" and None
    # are handled here, before the beam exists. A picker failure
    # here propagates (it is NOT inside the search try below).
    search.picker_calls += 1
    try:
        first = pick_action(goal, observation, list(history or ()))
    except Exception:
        search.picker_failed = True
        raise
    if hasattr(first, "__await__"):
        try:
            first = await first
        except Exception:
            search.picker_failed = True
            raise
    report["picker_calls"] = search.picker_calls
    if first is None:
        return None, report
    if isinstance(first, dict) and "done" in first:
        return first, report
    try:
        first_scored = score_candidate(first, ctx, history or ())
        if first_scored is None:
            # Malformed first pick: return it unchanged so
            # run_goal's existing malformed-action halt fires
            # exactly as greedy would.
            return first, report
        seed = [first_scored]
        # Fill the beam with further samples of the same step,
        # deduped against the first pick (best variant wins).
        more = await search.get_actions(
            world.init_state(observation, history),
            config.beam_width - 1)
        seed = _dedupe_best(seed + more)
        report["candidates_generated"] = len(seed)
        report["distinct"] = len(seed)
        report["picker_calls"] = search.picker_calls
        report["scores"] = [
            (_parse_goal_action(s.action)[0], s.score) for s in seed]
        if len(seed) == 1:
            # A deterministic picker: one distinct candidate.
            # The search is a no-op — return the greedy pick.
            report["depth_reached"] = 1
            report["best_score"] = seed[0].score
            report["best_tool"] = _parse_goal_action(seed[0].action)[0]
            return seed[0].action, report
        beam = BeamSearch(config)
        best, beam_report = await beam(
            world, search, seed, world.init_state(observation, history))
        report.update({k: v for k, v in beam_report.items()
                       if k not in ("candidates",)})
        report["picker_calls"] = search.picker_calls
        if best is None:
            return first, report
        return best, report
    except Exception as exc:  # noqa: BLE001 — see docstring
        # A picker failure (marked by GoalSearch) propagates;
        # anything else is a search-internal error and falls back
        # to the first candidate — the greedy pick.
        if getattr(search, "picker_failed", False):
            raise
        report["fallback"] = f"search error, greedy fallback: {exc}"
        return first, report


def report_line(report: dict) -> str:
    """One progress line for the goal transcript, in run_goal's
    voice: how many candidates the search weighed, which won, and
    whether it fell back to greedy."""
    if report.get("fallback"):
        return f"  🔍 search: {report['fallback']}"
    scores = report.get("scores") or []
    best = report.get("best_tool")
    if not scores:
        return "  🔍 search: no candidates (greedy pick kept)"
    shown = ", ".join(f"{t} ({s:+.2f})" for t, s in scores[:5])
    return (f"  🔍 search: {len(scores)} candidate(s), best "
            f"'{best}' — {shown}")

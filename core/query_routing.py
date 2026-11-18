"""GAPS-6 (BLOCKS #5, routing half) — per-query model routing.

Before this module, VYUK had NO per-query model/engine selection:
three parallel resolvers decided independently and none of them
looked at the query — ``model_manager.resolve_tier`` /
``resolve_model`` fall back on availability alone, ``local_assets``
``effective_model()`` picks by the user's explicit file, the
biggest tier already downloaded, or the machine's RAM, and
``config`` picks the engine (local/gemini, groq/ollama) once at
startup. Every query got the same model: a trivial question
burned the same model as a hard one. (The gap proof,
gap_routing.py, shows ``resolve_tier`` returning 'quality' and
``effective_model()`` returning 'balanced' for the SAME query —
the resolvers do not even agree with each other.)

This module is the ONE routing decision. It scores a query from
cheap, deterministic signals — length, shape, questions, code /
maths markers, multi-part markers — never a second model call
to decide which model to call — and picks ONE tier for it.
The shape signals are the turn classifier's own
(``core/voice/local_client_classifier.py``: the same measured-
features discipline — text shape is one input among several,
never a model judgment smuggled in).

How the decision is made, in order:
1. The user's explicit model pick wins. Routing never overrides
   a pick the user made (``LOCAL_MODEL_FILE`` / effective_model
   source "your pick").
2. Routing off (``enabled=False``) or an undecidable query
   (empty text) falls back to TODAY'S behaviour: the effective
   tier — the tier ``effective_model()`` already resolves. The
   fallback is stated in the decision, not silent.
3. Otherwise the score band picks the DESIRED tier — trivial
   queries want lite, routine ones balanced, hard ones quality —
   and availability is applied once, nearest first and cheaper
   first: a trivial query whose lite is missing gets balanced,
   never quality (the load path's ``resolve_tier`` falls back
   biggest-first, which would push a cheap query to the most
   expensive model — routing deliberately does not use it).
   A cheap query is therefore never routed to the most expensive
   model by default, and a hard query can reach the strongest
   available model. When the desired tier is unavailable the
   decision says so and names the tier it fell back to.
4. The decision is returned with its score, its signals, the
   rule that fired, and a plain-language reason — and recorded
   (``last_decision()`` / ``decision_history()``) so a wrong
   pick is diagnosable.

GAPS-22 (B-V11) — the loop half. Routing now also listens:
each turn records its outcome (``record_turn_outcome``) and
the next decision consults two things the score alone cannot
see — the cost ledger and the turn outcomes. The ledger
snapshot is pure data the caller builds (``CostLedger``
summary + preflight, best-effort); the router never imports
the ledger itself. Two loop rules, applied after the band
pick, in order:

* failure step-up: the desired tier's last three recorded
  turns all failed → step UP one tier (a tier that keeps
  failing is not serving the query; the stronger tier gets
  the next try). Quality has nowhere to step up to.
* cap step-down: the ledger says the daily cap is degraded
  (``degraded``) and the desired tier is quality → step DOWN
  to balanced (a hard query still gets a strong model, not
  the most expensive one, while the cap is hit). The cap
  applies last, so it wins when both rules fire.

Both adjustments are recorded in the decision's ``loop``
field (None when neither fired) and named in its reason, so
a stepped decision is diagnosable. The router still routes
tiers among the local models only — it never routes to a
different engine, and a degraded cap never sends a query to
the cloud; it steps the local tier down.

The resident-model boundary, stated on the tin: the built-in
local brain loads ONE GGUF per session, so a routed tier that
differs from the resident model takes effect at the next model
load, not mid-turn — the decision says exactly that when it
applies. Routing a turn to a different engine (local vs cloud)
is NOT done here: the engine is the user's own choice, and
silently sending a query to the cloud would break the
local-first stance. This module routes tiers among the local
models VYUK already has; it adds no model, no provider, and
no paid call.

Stdlib only. Pure: no config import (the caller passes
``enabled`` / ``effective_tier`` / ``availability`` in, exactly
like ``model_manager`` takes its inputs as arguments).
"""

from __future__ import annotations

import re

from . import model_manager as _mm
from .voice.local_client_classifier import (
    _QUICK_SHAPE_RE,
    _THINK_SHAPE_RE,
)

# Score bands: the score is 0..1; the band is the desired tier.
_TRIVIAL_BELOW = 0.35
_HARD_AT_OR_ABOVE = 0.65

# Band -> desired local tier. A cheap query wants lite, a hard
# one wants quality; availability may pull the answer down, but
# never pushes a cheap query up.
_BAND_TIER = {"trivial": "lite", "routine": "balanced", "hard": "quality"}

_CODE_RE = re.compile(
    r"(```|def |class |function |traceback|error:|import |"
    r"\[.*\]\(.*\)|SELECT |\{[^}]*\})",
    re.IGNORECASE)
_MATH_RE = re.compile(
    r"\b(integral|derivative|equation|calculate|compute|solve for|"
    r"probability|statistics|matrix|algebra|sqrt|logarithm)\b",
    re.IGNORECASE)
_MULTI_RE = re.compile(
    r"\b(first|then|next|finally|step \d|and also|as well as|"
    r"versus|vs\.?|on the other hand|in addition)\b",
    re.IGNORECASE)
_DETAIL_RE = re.compile(
    r"\b(step by step|in detail|thoroughly|in depth|"
    r"show your working|explain in detail)\b",
    re.IGNORECASE)

# The last routing decisions, most recent first (bounded ring).
# Module-level record in the failsafe _last_release_failures
# shape: inspectable in-process, no new store, no DB write.
_HISTORY_MAX = 20
_history: list[dict] = []

# The last turn outcomes, most recent first (bounded ring).
# GAPS-22 (B-V11): written by record_turn_outcome() after each
# turn; read by route_query() when it adjusts a band decision
# from what actually happened. Same in-process record shape as
# _history — no new store, no DB write.
_OUTCOMES_MAX = 20
_outcomes: list[dict] = []


def score_query(text: str | None, hints: dict | None = None) -> dict:
    """Score one query from cheap, deterministic signals.

    Returns {"score" (0..1), "band" ("trivial"|"routine"|"hard"),
    "signals" ({...})}. No model call, no network, no config.
    ``hints`` lets a caller add measured turn features the text
    alone cannot see (tool_calls, repair_rounds, prior failure);
    every hint is a plain number/bool, never a model judgment.
    """
    text = " ".join(str(text or "").split())
    if not text:
        # No text, no complexity: an empty query scores exactly 0.
        return {"score": 0.0, "band": "trivial",
                "signals": {"words": 0, "questions": 0,
                            "think_shape": False, "quick_shape": False,
                            "code": False, "maths": False,
                            "multi_part": False, "detail": False,
                            "tool_calls": 0, "repair_rounds": 0,
                            "prior_turn_failed": False}}
    words = text.split() if text else []
    hints = hints or {}
    signals: dict = {
        "words": len(words),
        "questions": text.count("?"),
        "think_shape": bool(_THINK_SHAPE_RE.search(text)),
        "quick_shape": bool(_QUICK_SHAPE_RE.search(text)),
        "code": bool(_CODE_RE.search(text)),
        "maths": bool(_MATH_RE.search(text)),
        "multi_part": bool(_MULTI_RE.search(text)),
        "detail": bool(_DETAIL_RE.search(text)),
        "tool_calls": int(hints.get("tool_calls") or 0),
        "repair_rounds": int(hints.get("repair_rounds") or 0),
        "prior_turn_failed": bool(hints.get("prior_turn_failed")),
    }
    score = 0.0
    # Length: longer asks carry more to do.
    if signals["words"] <= 6:
        score += 0.05
    elif signals["words"] <= 12:
        score += 0.15
    elif signals["words"] <= 25:
        score += 0.30
    elif signals["words"] <= 50:
        score += 0.45
    else:
        score += 0.55
    # Questions, reasoning shape, code/maths, multi-part, detail.
    score += min(0.20, 0.10 * signals["questions"])
    if signals["think_shape"]:
        score += 0.25
    if signals["code"] or signals["maths"]:
        score += 0.20
    if signals["multi_part"]:
        score += 0.15
    if signals["detail"]:
        score += 0.10
    # Measured turn pressure (hints only): real tool use, a repair
    # already spent, a failed prior turn — a turn that is already
    # hard work earns the stronger tier.
    if signals["tool_calls"] >= 2:
        score += 0.10
    if signals["repair_rounds"] >= 1:
        score += 0.10
    if signals["prior_turn_failed"]:
        score += 0.10
    # A short control command is cheap even when it is wordy
    # enough to score: quick shape without think shape pulls down.
    if signals["quick_shape"] and not signals["think_shape"] \
            and signals["words"] <= 12:
        score -= 0.15
    score = max(0.0, min(1.0, score))
    if score < _TRIVIAL_BELOW:
        band = "trivial"
    elif score < _HARD_AT_OR_ABOVE:
        band = "routine"
    else:
        band = "hard"
    return {"score": round(score, 3), "band": band, "signals": signals}


def _routing_fallback(availability: dict, desired: str) -> str | None:
    """The tier routing falls back to when the desired is missing.

    Nearest first, cheaper first: a trivial query that cannot
    have lite gets balanced (not quality), a hard query that
    cannot have quality gets balanced, a routine query that
    cannot have balanced gets lite. This is routing policy, and
    it deliberately differs from ``model_manager.resolve_tier``:
    the load path wants the BEST tier present (biggest first),
    while routing wants the CHEAPEST tier that serves the query
    — a cheap query must never be pushed to the most expensive
    model by an availability fallback. Availability itself still
    comes from the one shared source (the injected map or real
    discovery); only the fallback order is the router's own.
    """
    order = ["lite", "balanced", "quality"]
    idx = order.index(desired)
    cheaper = order[:idx][::-1]       # nearest cheaper first
    dearer = order[idx + 1:]          # nearest dearer first
    for tier in [desired] + cheaper + dearer:
        if availability.get(tier):
            return tier
    return None


def _tier_file(tier: str | None) -> str | None:
    """The GGUF filename for a tier, or None when there is none."""
    if tier is None:
        return None
    for name, tier_name in _mm.TIER_FILES.items():
        if tier_name == tier:
            return name
    return None


def _availability_for(availability: dict | None,
                      effective_tier: str | None,
                      data_dir: str | None) -> dict:
    """The availability map this decision may trust.

    An injected map wins (tests, callers that already scanned).
    Otherwise real discovery over ``data_dir`` when given. When
    neither is supplied, only the effective tier is known
    available — the resident model is present by definition, and
    no other tier is claimed available without evidence.
    """
    if availability is not None:
        return dict(availability)
    if data_dir is not None:
        discovered = _mm.discover_models(data_dir)
        return {m["tier"]: True for m in discovered.get("models", [])
                if m.get("kind") == "brain" and m.get("tier")}
    if effective_tier:
        return {effective_tier: True}
    return {}


def _record(decision: dict) -> dict:
    """Record one decision (bounded ring) and return it."""
    _history.insert(0, dict(decision))
    del _history[_HISTORY_MAX:]
    return decision


def last_decision() -> dict | None:
    """The most recent routing decision, or None when none ran."""
    return dict(_history[0]) if _history else None


def decision_history() -> list[dict]:
    """Routing decisions so far, most recent first (at most 20)."""
    return [dict(d) for d in _history]


def record_turn_outcome(decision: dict | None, *, ok: bool,
                        repairs: int = 0, tool_calls: int = 0,
                        latency_ms: float | None = None) -> dict:
    """Record what one routed turn actually did (GAPS-22, B-V11).

    ``decision`` is the routing decision the turn ran under (or
    None when the turn ran unrouted — the outcome is still
    recorded, with tier None). ``ok`` is False when the turn's
    recorded actions include a failure or rejection — the same
    evidence ``prior_turn_failed_from_trace`` reads, so the
    next turn's hints and this outcome can never disagree about
    what "failed" means. ``repairs`` counts the escalation /
    repair work the turn needed; ``tool_calls`` the tools it
    ran. Returns the recorded outcome (a copy). The ring holds
    the last 20; older outcomes are forgotten, so a stale
    failure cannot steer routing forever.
    """
    outcome = {"tier": (decision or {}).get("tier"),
               "rule": (decision or {}).get("rule"),
               "ok": bool(ok),
               "repairs": int(repairs or 0),
               "tool_calls": int(tool_calls or 0),
               "latency_ms": latency_ms}
    _outcomes.insert(0, outcome)
    del _outcomes[_OUTCOMES_MAX:]
    return dict(outcome)


def last_turn_outcome() -> dict | None:
    """The most recent turn outcome, or None when none ran."""
    return dict(_outcomes[0]) if _outcomes else None


def turn_outcomes() -> list[dict]:
    """Turn outcomes so far, most recent first (at most 20)."""
    return [dict(o) for o in _outcomes]


def _loop_adjustment(desired: str, ledger: dict | None) -> tuple[str, dict | None]:
    """The GAPS-22 loop rules, applied to the band's desired tier.

    Returns (tier, loop) — ``loop`` is None when neither rule
    fired, else {"rule", "reason"} naming the adjustment. The
    failure rule fires first, the cap rule last (the cap wins
    when both fire).
    """
    tier = desired
    loop = None
    # Failure step-up: the desired tier's last three recorded
    # turns all failed → the stronger tier gets the next try.
    # Only outcomes for THIS tier count; an unrouted outcome
    # (tier None) or a different tier's failure never steps a
    # tier up.
    recent = [o for o in _outcomes if o.get("tier") == desired][:3]
    if len(recent) == 3 and not any(o.get("ok") for o in recent):
        order = ["lite", "balanced", "quality"]
        idx = order.index(desired)
        if idx < len(order) - 1:
            tier = order[idx + 1]
            loop = {"rule": "failure-step-up",
                    "reason": (f"the last 3 {desired} turns all failed "
                               f"— stepped up to {tier} for the next try")}
    # Cap step-down: the ledger says the daily cap is degraded
    # and the (possibly stepped-up) tier is quality → balanced.
    if ledger is not None and bool(ledger.get("degraded")) \
            and tier == "quality":
        tier = "balanced"
        loop = {"rule": "cap-step-down",
                "reason": ("daily cost cap is degraded — stepped down "
                           "from quality to balanced")}
    return tier, loop


def route_query(text: str | None, *,
                availability: dict | None = None,
                effective_tier: str | None = None,
                enabled: bool = True,
                user_tier: str | None = None,
                data_dir: str | None = None,
                hints: dict | None = None,
                ledger: dict | None = None) -> dict:
    """Route ONE query to ONE local tier. The single decision path.

    Returns {"tier", "file", "score", "band", "signals", "rule",
    "reason", "routed" (the router chose, rather than fell back),
    "fallback" (today's behaviour was used instead), "loop"
    (the GAPS-22 loop adjustment, or None)}. The decision is
    also recorded (see ``last_decision()``).

    Rules, in order: "user-pick" (the user's explicit model pick
    wins over routing); "routing-disabled"; "undecided" (empty
    query — nothing to score); otherwise "score-band" with the
    availability fallback applied once via ``resolve_tier`` —
    and, before availability, the loop rules (see
    ``_loop_adjustment``): a failing tier steps up, a degraded
    cost cap steps quality down. ``ledger`` is pure data the
    caller builds (``{"spend_today_usd", "daily_cap_usd",
    "degraded"}`` from the cost ledger, best-effort) — the
    router never reads the ledger itself. When the routed tier
    differs from the effective (resident) tier, the reason says
    the resident model stays until the next model load.
    """
    scored = score_query(text, hints)
    base = {"score": scored["score"], "band": scored["band"],
            "signals": scored["signals"], "fallback": False,
            "routed": True, "loop": None}
    # 1. The user's explicit pick wins over routing.
    if user_tier:
        decision = dict(base, tier=user_tier,
                        file=_tier_file(user_tier),
                        rule="user-pick",
                        reason=("explicit model pick wins over routing: "
                                f"tier {user_tier}"),
                        routed=False)
        return _record(decision)
    # 2. Routing off: today's behaviour, stated.
    if not enabled:
        decision = dict(base, tier=effective_tier,
                        file=_tier_file(effective_tier),
                        rule="routing-disabled",
                        reason=("query routing is off — using the "
                                "effective tier (today's behaviour)"),
                        routed=False, fallback=True)
        return _record(decision)
    # 3. Nothing to score: undecided, today's behaviour.
    if not str(text or "").strip():
        decision = dict(base, tier=effective_tier,
                        file=_tier_file(effective_tier),
                        rule="undecided",
                        reason=("empty query — nothing to score; using "
                                "the effective tier (today's behaviour)"),
                        routed=False, fallback=True)
        return _record(decision)
    # 4. Score band -> desired tier -> loop rules -> availability, once.
    desired = _BAND_TIER[scored["band"]]
    desired, loop = _loop_adjustment(desired, ledger)
    avail = _availability_for(availability, effective_tier, data_dir)
    tier = _routing_fallback(avail, desired)
    if tier is None:
        # Nothing is available at all: say so, and fall back to
        # the effective tier rather than inventing a model.
        decision = dict(base, tier=effective_tier,
                        file=_tier_file(effective_tier),
                        rule="unavailable",
                        reason=("no tier is available from discovery; "
                                "using the effective tier "
                                "(today's behaviour)"),
                        routed=False, fallback=True, loop=loop)
        return _record(decision)
    if tier != desired:
        reason = (f"{scored['band']} query wants {desired}, but "
                  f"{desired} is not available — fell back to {tier}")
    else:
        reason = (f"{scored['band']} query (score "
                  f"{scored['score']}) routed to {tier}")
    if loop is not None:
        reason += f"; loop: {loop['reason']}"
    if effective_tier and tier != effective_tier:
        reason += (f"; resident model is {effective_tier} and stays "
                   "until the next model load")
    decision = dict(base, tier=tier, file=_tier_file(tier),
                    rule="score-band", reason=reason, loop=loop)
    return _record(decision)


def route_for_turn(text: str | None, *,
                   availability: dict | None = None,
                   effective_tier: str | None = None,
                   enabled: bool = True,
                   user_tier: str | None = None,
                   data_dir: str | None = None,
                   hints: dict | None = None,
                   ledger: dict | None = None) -> dict:
    """The turn's routing decision: ``route_query`` + the record.

    This is the one entry the local client's turn-start path
    calls, next to the profile classifier. It never raises on
    bad input — a routing failure falls back to the effective
    tier rather than breaking the turn.
    """
    try:
        return route_query(text, availability=availability,
                           effective_tier=effective_tier,
                           enabled=enabled, user_tier=user_tier,
                           data_dir=data_dir, hints=hints,
                           ledger=ledger)
    except Exception as exc:  # noqa: BLE001 — routing never breaks a turn
        decision = {"tier": effective_tier,
                    "file": _tier_file(effective_tier),
                    "score": 0.0, "band": "routine", "signals": {},
                    "rule": "routing-error",
                    "reason": (f"routing failed ({exc}) — using the "
                               "effective tier (today's behaviour)"),
                    "routed": False, "fallback": True, "loop": None}
        return _record(decision)

"""Think bake-off race harness (2026-11-05 batch) — ruled design, built.

RULED DESIGN (owner, 2026-11-05, elegant design approved):
"No winner without a race, no ship without a licence."
  1. No winner without a race — Nemotron-Nano-8B vs OpenThinker3-7B
     run the SAME real tasks on real hardware; numbers decide;
     until the race runs there is no winner and this harness says
     so (NO-DATA, winner None).
  2. Licence is a separate ship gate — a dev race is allowed, but
     shipping needs a verified clean licence; a dirty licence
     loses regardless of score. Licence status is tracked
     separately from scores and never mixed into them.
  3. Replacement, named — the winner takes ONE named resident
     slot (8 GB budget, INV-21 replacement-tier only); displaced
     duties are explicitly re-homed or dropped by decision (see
     REPLACEMENT_SLOT — nothing is silently dropped).
  4. Async always — submit/collect only, no sync entry ever,
     speech never stalls (see race_voice_path_entry_points).
  5. Honest until gated — truncation marked, continuation
     prompts never pose as answers, labelled harness not feature
     until all gates pass.

WHAT THIS MODULE IS AND IS NOT:
  * It EXTENDS core/think.py (imported, never edited here): the
    existing harness keeps its licence-gated production registry
    (register_engine_adapter still refuses OWED models — that
    gate is the SHIP path and is untouched). This module adds a
    separate DEV-RACE-ONLY registry (register_race_engine) for
    the race. Race adapters never feed production collect, never
    ship, and never bypass the ship gate: the ship decision in
    race_verdict re-checks licence, budget fit, and weights
    re-verification independently.
  * It holds no weights, downloads nothing, imports no inference
    library. Real engines arrive later through injection; until
    then every race run is a labelled fixture (live=False) and
    the verdict is NO-DATA.
  * LIVE marking rule (load-bearing): a race run is live=True
    ONLY when its adapter was registered with real=True (the real
    race runner, with real engines). Explicitly passed adapters
    and real=False registrations always produce live=False
    fixtures. Passing a fake with real=True in a test is a
    simulation, labelled as such in that test — never a race.
  * GRADING is exact-after-normalisation (lowercase, punctuation
    collapsed, whitespace collapsed). That is deliberately strict
    and comparative: both engines face the same strictness, and
    explanations around an answer fail. Open-ended quality is
    assessed separately and is NOT part of the race score.
  * BUDGET TENSION, stated not hidden: the three resident tier
    disk classes in think.RESIDENT_TIER_GB sum to 8.6 GB, above
    the 8 GB budget — disk inventory is not RAM residency. The
    fit check therefore uses MEASURED sizes at race time
    (candidate_gb + other loaded residents), never the stated
    classes, and a candidate that does not fit does not ship.
    Candidate sizes are currently unknown (None in MODELS), so
    the fit gate reports measurement-pending until race time.

Versioned strings: RACE_TASKS_VERSION = "1.0" (frozen task set;
changing tasks is a new version, never a silent edit).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .think import (
    LICENCE_VERDICTS,
    RESIDENT_BUDGET_GB,
    RESIDENT_TIER_GB,
    ThinkBudget,
    ThinkRun,
    bakeoff_verdict,
    enforce_budget,
    fits_as_replacement,
    slot_competition,
    split_thinking,
)

RACE_TASKS_VERSION = "1.0"

# --- Competitors ---------------------------------------------------------
RACE_COMPETITORS = ("nemotron-nano-8b", "openthinker3-7b")


# --- Frozen race tasks ----------------------------------------------------
# Real tasks the resident thinker would actually do, not toy prompts.
# Every task is exact-answer graded (see grade_answer): the prompt
# instructs the exact format, so strictness is fair to both engines.
RACE_TASKS = (
    {
        "id": "t-plan-deps",
        "language": "en",
        "category": "planning",
        "prompt": (
            "A user wants to prepare a monthly expense report. "
            "Steps available: (A) export bank transactions, "
            "(B) categorise transactions, (C) total each category, "
            "(D) write the summary, (E) send the summary. "
            "Constraints: categorising needs the export; totalling "
            "needs categorised transactions; writing needs totals; "
            "sending needs the written summary. Reply with only "
            "the letters, in a valid order, separated by spaces."
        ),
        "expected": "A B C D E",
    },
    {
        "id": "t-constraint-arith",
        "language": "en",
        "category": "reasoning",
        "prompt": (
            "A shop sells pens in packs of 6 and notebooks in "
            "packs of 4. A teacher buys exactly 3 packs in total "
            "and gets 16 items. How many pen packs did she buy? "
            "Reply with only the number."
        ),
        "expected": "2",
    },
    {
        "id": "t-logic",
        "language": "en",
        "category": "reasoning",
        "prompt": (
            "Three boxes are labelled Apples, Oranges, Mixed. "
            "Every label is wrong. You take one fruit from the "
            "box labelled Mixed and it is an apple. Which box "
            "actually contains oranges? Reply with only the "
            "box's label."
        ),
        "expected": "Apples",
    },
    {
        "id": "t-code-bug",
        "language": "en",
        "category": "code",
        "prompt": (
            "Which line contains the bug? Reply with only the "
            "line number.\n"
            "1: def total(prices):\n"
            "2:     s = 0\n"
            "3:     for i in range(1, len(prices)):\n"
            "4:         s += prices[i]\n"
            "5:     return s"
        ),
        "expected": "3",
    },
    {
        "id": "t-code-output",
        "language": "en",
        "category": "code",
        "prompt": (
            "What does this print? Reply with only the value.\n"
            "x = [1, 2, 3]\n"
            "y = x\n"
            "y.append(4)\n"
            "print(len(x))"
        ),
        "expected": "4",
    },
    {
        "id": "t-ambiguity",
        "language": "en",
        "category": "reasoning",
        "prompt": (
            "A user says 'Move the meeting to Friday.' There are "
            "two meetings this week: team sync and client call. "
            "Which fact must be confirmed first? Reply with only "
            "A or B. A: Which meeting to move. B: What time "
            "Friday is."
        ),
        "expected": "A",
    },
    {
        "id": "t-hi-arith",
        "language": "hi",
        "category": "reasoning",
        "prompt": (
            "एक दुकानदार के पास 5 डिब्बे हैं, हर डिब्बे में 8 पेन "
            "हैं। वह 13 पेन बेच देता है। उसके पास कितने पेन बचे? "
            "केवल संख्या में उत्तर दें।"
        ),
        "expected": "27",
    },
    {
        "id": "t-hi-logic",
        "language": "hi",
        "category": "reasoning",
        "prompt": (
            "राम, श्याम से लंबा है। श्याम, मोहन से लंबा है। "
            "सबसे छोटा कौन है? केवल नाम उत्तर दें।"
        ),
        "expected": "मोहन",
    },
)

RACE_TASK_IDS = tuple(t["id"] for t in RACE_TASKS)


def get_race_task(task_id: str) -> dict | None:
    """One frozen race task by id, or None if unknown."""
    for task in RACE_TASKS:
        if task["id"] == task_id:
            return task
    return None


def _normalise_answer(text: str) -> str:
    """Lowercase, punctuation collapsed, whitespace collapsed.

    Deliberately strict: an explanation around the answer fails,
    because the normalised text no longer equals the expected
    text. Both engines face the same strictness."""
    lowered = str(text or "").lower()
    cleaned = re.sub(r"[^\w]+", " ", lowered, flags=re.UNICODE)
    return " ".join(cleaned.split())


def grade_answer(task: dict, answer: str) -> bool:
    """True iff the answer matches the task's expected answer
    after normalisation. Unknown tasks never grade True."""
    if not isinstance(task, dict) or "expected" not in task:
        return False
    return _normalise_answer(answer) == _normalise_answer(
        task["expected"])


# --- Named replacement slot (INV-21 replacement-tier only) -----------------
# The quality tier IS the resident 8B slot: model_manager TIER_FILES
# maps Qwen3-8B-Instruct-2507-Q4_K_M.gguf to "quality", and
# think.slot_competition already lists "quality" as competing for
# the 8B slot. The winner replaces quality; it never co-resides.
REPLACEMENT_SLOT = {
    "slot": "quality",
    "slot_rule": "8B",
    "resident_model": "Qwen3-8B-Instruct-2507",
    "resident_gb_class": RESIDENT_TIER_GB["quality"],
    "budget_gb": RESIDENT_BUDGET_GB,
    "tier_rule": "INV-21 replacement-tier only",
    # Displaced duties, each explicitly dispositioned. NOTHING is
    # silently dropped: every quality-tier duty lands in exactly
    # one home below. Any future duty that fits neither home must
    # be dispositioned by decision first — never dropped quietly.
    "displaced_duties": (
        {
            "duty": "reasoning-heavy top-tier requests",
            "disposition": "re-homed",
            "home": "winner (new deep-think tier)",
        },
        {
            "duty": "latency-sensitive top-tier requests",
            "disposition": "re-homed",
            "home": "balanced (4B tier, faster)",
        },
        {
            "duty": "all other top-tier requests (default)",
            "disposition": "re-homed",
            "home": "winner (new top tier)",
        },
    ),
    "dropped": (),
    "fit_rule": (
        "fits_as_replacement(measured_candidate_gb, 5.0, "
        "measured_other_resident_gb) must hold at race time; "
        "stated classes are not measurements and disk inventory "
        "is not RAM residency."
    ),
}


def slot_fits(candidate_gb: float | None,
             other_resident_gb: float | None) -> dict:
    """The budget fit check for the replacement slot.

    Sizes are MEASURED at race time. Unknown sizes report
    measurement-pending (never a guessed fit); a measured miss
    refuses outright."""
    if candidate_gb is None or other_resident_gb is None:
        return {
            "fits": False,
            "reason": "budget-measurement-pending",
            "detail": ("Candidate and other-resident sizes are "
                       "measured at race time; neither is known "
                       "now, so no fit is claimed."),
        }
    fits = fits_as_replacement(candidate_gb,
                               REPLACEMENT_SLOT["resident_gb_class"],
                               other_resident_gb)
    return {
        "fits": fits,
        "reason": "fits" if fits else "budget-exceeded",
        "detail": (f"other {other_resident_gb} GB + candidate "
                   f"{candidate_gb} GB vs budget "
                   f"{RESIDENT_BUDGET_GB} GB."),
    }


# --- Dev-race-only engine registry -----------------------------------------
# SEPARATE from think._ENGINE_ADAPTERS (the licence-gated
# production registry). Race adapters never feed production
# collect, never ship, and never bypass the ship gate: the ship
# decision in race_verdict re-checks licence, budget, and weights
# independently. Dev race allowed; ship only on clean licence.
_RACE_ADAPTERS: dict[str, tuple[object, bool]] = {}


def _race_adapter_generate(adapter: object) -> object | None:
    """The generate callable of a race adapter, or None.

    Same contract as think's wiring seam: generate(prompt,
    budget) -> str, either as the adapter itself or as its
    .generate attribute."""
    if callable(adapter):
        return adapter
    generate = getattr(adapter, "generate", None)
    if callable(generate):
        return generate
    return None


def register_race_engine(model_key: str, adapter: object,
                         real: bool = False) -> None:
    """Register a DEV-RACE-ONLY engine adapter for one competitor.

    Allowed for both competitors regardless of licence verdict:
    a dev race is allowed, and licence gates SHIPPING, not racing.
    Unknown keys, non-competitors, and non-adapters are refused.
    ``real`` marks a genuine engine (real race runner only);
    anything else is a fixture and its runs stay live=False.
    This registry is never read by production code."""
    if model_key not in RACE_COMPETITORS:
        raise ValueError(
            f"race engines are only for {RACE_COMPETITORS!r}; "
            f"got {model_key!r}")
    if _race_adapter_generate(adapter) is None:
        raise ValueError(
            "race engine adapter must be callable as "
            "generate(prompt, budget) -> str, or hold such a "
            ".generate attribute")
    _RACE_ADAPTERS[model_key] = (adapter, bool(real))


def get_race_engine(model_key: str) -> object | None:
    """The registered dev-race adapter for a competitor, or None."""
    entry = _RACE_ADAPTERS.get(model_key)
    return entry[0] if entry is not None else None


def clear_race_engines() -> None:
    """Forget all registered race adapters (tests + race reset)."""
    _RACE_ADAPTERS.clear()


# --- Async-only race submission ---------------------------------------------
@dataclass
class RaceJob:
    """A queued race job. Queued, never executed here: the prompt
    comes from the frozen task, the result arrives only via
    collect_race_job, off the voice path. ``result`` stays None
    by construction until collected."""
    job_id: str
    task_id: str
    model_key: str
    budget: ThinkBudget = field(default_factory=ThinkBudget)
    result: object = None


def race_voice_path_entry_points() -> list[str]:
    """Synchronous voice-path entry points for the race: NONE.

    The race is submit/collect only, like the think harness. A
    test asserts this list is empty and that no sync-named
    callable exists in this module."""
    return []


def submit_race_job(job_id: str, task_id: str, model_key: str,
                    budget: ThinkBudget | None = None) -> RaceJob:
    """Queue a race job and return IMMEDIATELY.

    This function does not run a model, does not block, and does
    not return an answer. The task prompt is fixed by the frozen
    task set, so both competitors receive the identical prompt
    for a task. Unknown tasks and non-competitors are refused
    rather than queued under a guessed task or model."""
    if get_race_task(task_id) is None:
        raise ValueError(f"unknown race task: {task_id!r}")
    if model_key not in RACE_COMPETITORS:
        raise ValueError(
            f"race jobs are only for {RACE_COMPETITORS!r}; "
            f"got {model_key!r}")
    return RaceJob(job_id=job_id, task_id=task_id, model_key=model_key,
                   budget=budget or ThinkBudget())


def collect_race_job(job: RaceJob, adapter: object = None) -> dict:
    """Collect a queued race job's result, off the voice path.

    With an engine adapter — registered for the job's model, or
    passed explicitly — the adapter runs through the harness's
    own discipline: split_thinking + enforce_budget. A capped run
    is marked (truncated True, marker present), never silent; a
    short run reports needs-continuation with the continuation
    directive as prompt text, never a faked complete answer; an
    adapter failure or non-string output reports error, never a
    fabricated result. With no adapter, the result is NO-DATA.
    Licence does NOT gate execution here (dev race allowed) but
    the result CARRIES the licence verdict, and the ship decision
    in race_verdict applies it. Live marking follows the
    registration rule: live=True only via a registered real=True
    adapter; explicit adapters and real=False registrations yield
    live=False fixtures. This function never touches the
    production engine registry."""
    task = get_race_task(job.task_id)
    entry = _RACE_ADAPTERS.get(job.model_key)
    use = adapter if adapter is not None else (
        entry[0] if entry is not None else None)
    live = bool(entry is not None and entry[1]) and adapter is None
    verdict = LICENCE_VERDICTS.get(job.model_key)
    base = {
        "job_id": job.job_id,
        "task_id": job.task_id,
        "model_key": job.model_key,
        "licence": verdict.get("licence") if verdict else None,
        "licence_verdict": verdict.get("verdict") if verdict else None,
        "dev_only": True,
        "live": live,
        "run": None,
    }
    if task is None:
        return {**base, "status": "error",
                "reason": f"unknown race task: {job.task_id!r}",
                "thinking": None, "answer": None}
    if use is None:
        return {**base, "status": "NO-DATA",
                "reason": ("No race engine is registered for "
                           f"{job.model_key!r}: nothing was run, "
                           "nothing is claimed."),
                "thinking": None, "answer": None}
    generate = _race_adapter_generate(use)
    if generate is None:
        return {**base, "status": "error",
                "reason": ("The race engine adapter for "
                           f"{job.model_key!r} is not callable as "
                           "generate(prompt, budget) -> str."),
                "thinking": None, "answer": None}
    started = time.monotonic()
    try:
        raw = generate(task["prompt"], job.budget)
    except Exception as exc:  # noqa: BLE001 — adapter failure is data
        return {**base, "status": "error",
                "reason": f"race engine adapter failed: {exc}",
                "thinking": None, "answer": None}
    latency_ms = (time.monotonic() - started) * 1000.0
    if not isinstance(raw, str):
        return {**base, "status": "error",
                "reason": ("race engine adapter must return str "
                           f"(raw model output); got "
                           f"{type(raw).__name__}."),
                "thinking": None, "answer": None}
    thinking, answer = split_thinking(raw)
    enforced = enforce_budget(thinking, job.budget)
    correct = grade_answer(task, answer)
    run = ThinkRun(
        model_key=job.model_key,
        task_id=job.task_id,
        correct=correct,
        thinking_tokens=enforced["tokens_after"],
        latency_ms=latency_ms,
        language=task["language"],
        live=live,
    )
    return {
        **base,
        "status": ("completed" if enforced["action"] != "extend"
                   else "needs-continuation"),
        "thinking": enforced["thinking"],
        "answer": answer,
        "action": enforced["action"],
        "truncated": enforced["action"] == "capped",
        "tokens_before": enforced["tokens_before"],
        "tokens_after": enforced["tokens_after"],
        "continuation_directive": enforced["continuation_directive"],
        "run": run,
    }


# --- Race verdict + ship decision -------------------------------------------
def race_verdict(runs: list[ThinkRun],
                 candidate_gb: float | None = None,
                 other_resident_gb: float | None = None,
                 weights_reverified: bool = False) -> dict:
    """Verdict a race, then decide shipping — separately.

    Gates, in order: (1) coverage — every competitor must have a
    LIVE run on EVERY frozen task, else NO-DATA (partial data
    never crowns a winner, mirroring the RD bake-off rule);
    (2) score — think.bakeoff_verdict on live competitor runs
    only (highest accuracy, ties toward fewer tokens); (3) ship
    gates — the winner ships ONLY if its licence is
    CLEAR-for-harness AND the measured budget fit holds AND
    weights re-verification passed. A winner that fails any ship
    gate is reported as race winner but ship_pick None, with the
    blocker named. A dirty licence loses regardless of score.
    Non-competitor runs are ignored and reported, never scored."""
    live = [r for r in runs if r.live]
    scored = [r for r in live if r.model_key in RACE_COMPETITORS]
    ignored = sorted({r.model_key for r in live
                      if r.model_key not in RACE_COMPETITORS})
    missing: dict[str, list[str]] = {}
    for competitor in RACE_COMPETITORS:
        covered = {r.task_id for r in scored
                   if r.model_key == competitor}
        absent = [t for t in RACE_TASK_IDS if t not in covered]
        if absent:
            missing[competitor] = absent
    if missing:
        return {
            "winner": None,
            "verdict": "NO-DATA",
            "reason": ("Partial race coverage: every competitor "
                       "must have a live run on every frozen "
                       "task before any winner exists."),
            "missing": missing,
            "ignored_models": ignored,
            "ship_pick": None,
            "shippable": False,
            "ship_blockers": ["partial-coverage"],
            "closing_step": ("Run the missing tasks for the "
                             "missing competitors, then re-run "
                             "this verdict."),
        }
    base = bakeoff_verdict(scored)
    winner = base.get("winner")
    blockers: list[str] = []
    if base.get("verdict") != "LIVE" or winner is None:
        blockers.append("no-race-winner")
        return {
            **base,
            "ignored_models": ignored,
            "ship_pick": None,
            "shippable": False,
            "ship_blockers": blockers,
            "closing_step": base.get("closing_step"),
        }
    verdict = LICENCE_VERDICTS.get(winner)
    licence = verdict.get("verdict") if verdict else None
    if licence != "CLEAR-for-harness":
        blockers.append(f"licence-{licence}")
    fit = slot_fits(candidate_gb, other_resident_gb)
    if not fit["fits"]:
        blockers.append(fit["reason"])
    if not weights_reverified:
        blockers.append("weights-reverify-pending")
    return {
        **base,
        "ignored_models": ignored,
        "licence": {m: (LICENCE_VERDICTS.get(m) or {}).get("verdict")
                    for m in RACE_COMPETITORS},
        "budget_fit": fit,
        "ship_pick": winner if not blockers else None,
        "shippable": not blockers,
        "ship_blockers": blockers,
        "closing_step": (
            "All ship gates pass: winner ships into the quality "
            "slot." if not blockers else
            "Winner does not ship until every blocker clears; "
            "losers leave regardless."),
    }


def race_slot() -> dict:
    """The named replacement slot, and its consistency with the
    existing slot rule: think.slot_competition already lists
    "quality" as competing for the 8B slot, and this race's slot
    IS that quality tier's seat."""
    competition = slot_competition(
        ["nemotron-nano-8b", "openthinker3-7b", "quality"])
    return {
        "replacement_slot": REPLACEMENT_SLOT,
        "slot_rule_consistent": (
            competition["slot"] == REPLACEMENT_SLOT["slot_rule"]
            and "quality" in competition["competing"]
            and competition["pick"] is None),
    }

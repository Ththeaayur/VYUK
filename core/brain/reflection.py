"""Wave 8 (v5.63) — Reflexion + Self-Discover for run_goal (brief item 5,
BN-1-1/1-3).

Reflexion: ONE bounded reflection-retry. On a failed step, the loop
reflects once on the failure trace, screens the reflection for taint,
and retries at most once with the reflection attached. The retry cap
is enforced by a counter, not by convention: a second failure ends
the step as failed — no reflection loop, ever. Taint screening drops
a reflection that carries injection markers or secret-shaped content
instead of retrying on poisoned text.

Self-Discover: cached reasoning structures per task type. Each
structure is PINNED PROMPT TEXT — a fixed reasoning skeleton the
brain is instructed to follow for that task type. All structures live
in ONE clearly-marked table (:data:`REASONING_STRUCTURES`) so the
coordinator can re-pin fixtures deliberately (INV-24: cached
structures are new pinned prompt text — re-pin deliberately, never
silently). :func:`re_pin` replaces one entry only when called with
explicit new text and records the change; there is no silent
mutation path.

No new dependencies, no telemetry, stdlib only. Reflection text is
produced by an injected callable (the coordinator's brain call) —
this module never calls a brain itself and executes no tool.

RETIRED — B17 (learning across tasks, 2026-11-07). This module is
NOT wired into any live loop and must not be re-wired without
re-deciding. Reason: B12's retry ladder already provides exactly
one bounded retry per step (the same guarantee
``reflect_and_retry`` provides), so wiring a second retry would
give two retries per step and contradict B12; and the playbook
write (core/brain/task_learning.py) provides the learning from
failure without needing a brain-generated reflection — no brain
callable exists at either completion path to produce one, and
inventing a cloud call on every failure would be new capability,
not wiring. The functions stay callable (tests and the
checkpoint path import them) and the Self-Discover structures
below stay pinned and unchanged (re-pin deliberately, INV-24)
— retirement is a wiring decision, not a deletion. The audit's
"zero importers" claim was wrong in the letter (w8_wiring_steps
imports this module) but right in substance: no live loop ever
supplies a reflect_fn, so no reflection ever ran in production.

Versioned strings introduced by this module (pre-pinned to 5.63):
``WAVE_VERSION = "5.63"`` and ``STRUCTURES_VERSION = "5.63"``
(module markers only — NOT the product version; ``core/version.py``
stays 5.62.0 until coordinator assembly).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

WAVE_VERSION = "5.63"
STRUCTURES_VERSION = "5.63"

#: B17: this module is RETIRED (see the module docstring) — not
#: wired into any live loop. The reason is recorded here so a
#: test and the prover can pin it, and a future re-wire must
#: re-decide it explicitly instead of silently reviving it.
RETIRED_REASON = (
    "B12's retry ladder already provides exactly one bounded "
    "retry per step (the same guarantee reflect_and_retry "
    "provides); wiring a second retry would give two retries "
    "per step. No brain callable exists at either completion "
    "path to produce a reflection, and the playbook write "
    "(task_learning.record_task_lesson) provides the learning "
    "from failure without one."
)
RETIRED = True

#: Hard cap: exactly one reflection-retry per step, enforced by count.
MAX_REFLECTION_RETRIES = 1

#: Markers that taint a reflection (injection-shaped or secret-shaped).
_TAINT_MARKERS = (
    "ignore previous instructions", "ignore all previous",
    "disregard your instructions", "system prompt:",
    "api_key", "password:", "-----begin",
)


def screen_reflection(text: str) -> dict:
    """Taint-screen a reflection. Returns ``{"clean": bool, "hits": [...]}``.

    A tainted reflection is DISCARDED (the retry proceeds without it
    or not at all — see :func:`reflect_and_retry`); tainted text is
    never fed back into the loop.
    """
    lowered = text.lower()
    hits = [m for m in _TAINT_MARKERS if m in lowered]
    return {"clean": not hits, "hits": hits}


# --- Self-Discover: ONE clearly-marked table of pinned prompt text ----
# RE-PIN DELIBERATELY (INV-24): every entry here is pinned prompt
# text that fixtures may depend on. Change an entry ONLY through
# re_pin() with explicit new text, and re-run the fixtures after.
REASONING_STRUCTURES: dict[str, dict] = {
    "file_operation": {
        "structure": "locate -> verify-target -> act -> verify-result",
        "prompt": "First locate the exact target. Verify it exists "
                  "before acting. Act with the smallest reversible "
                  "operation. Verify the result matches the intent "
                  "before reporting success.",
    },
    "web_lookup": {
        "structure": "question -> sources -> corroborate -> answer",
        "prompt": "Restate the question. Gather at least two "
                  "independent sources. Corroborate the key claim "
                  "across them. Answer only what is corroborated; "
                  "mark the rest as unverified.",
    },
    "multi_step_control": {
        "structure": "goal -> steps -> gate-check -> execute -> confirm",
        "prompt": "Break the goal into steps. Check each step for "
                  "gate requirements before executing. Execute one "
                  "step at a time through the funnel. Confirm each "
                  "step's result before the next.",
    },
    "question_answering": {
        "structure": "question -> recall -> verify -> answer",
        "prompt": "Restate the question. Recall what is known. "
                  "Verify the key claim against available evidence. "
                  "Answer plainly, and say what is unknown.",
    },
    "diagnosis": {
        "structure": "symptom -> hypotheses -> test -> conclusion",
        "prompt": "State the symptom exactly. List the plausible "
                  "causes. Test the cheapest discriminating cause "
                  "first. Conclude only from a test result, never "
                  "from the symptom alone.",
    },
}

#: Audit trail of deliberate re-pins: (task_type, old_version, new_text).
_REPIN_LOG: list[dict] = []


def reasoning_structure(task_type: str) -> Optional[dict]:
    """The cached reasoning structure for a task type, or None."""
    entry = REASONING_STRUCTURES.get(task_type)
    return dict(entry) if entry else None


def re_pin(task_type: str, new_prompt: str, new_structure: str = "") -> dict:
    """Deliberately re-pin one reasoning structure (INV-24).

    Replaces the entry's prompt text ONLY on this explicit call and
    logs the change. Returns the new entry plus the log length so
    the coordinator can confirm fixtures were re-run after the pin.
    """
    if task_type not in REASONING_STRUCTURES:
        return {"pinned": False, "detail": f"unknown task type {task_type!r}"}
    old = REASONING_STRUCTURES[task_type]["prompt"]
    REASONING_STRUCTURES[task_type] = {
        "structure": new_structure or REASONING_STRUCTURES[task_type][
            "structure"],
        "prompt": new_prompt,
    }
    _REPIN_LOG.append({"task_type": task_type,
                       "old_prompt": old, "new_prompt": new_prompt})
    return {"pinned": True, "task_type": task_type,
            "repin_log_entries": len(_REPIN_LOG)}


@dataclass
class ReflectionAttempt:
    step: dict
    failure: str
    reflection: Optional[str] = None
    retries_used: int = 0
    tainted: bool = False


def reflect(failure_trace: str,
            reflect_fn: Callable[[str], str]) -> dict:
    """Produce one taint-screened reflection on a failure trace.

    ``reflect_fn`` is the injected reflector (the coordinator's brain
    call). A tainted reflection is discarded and reported as such —
    the caller retries without it or not at all.
    """
    raw = reflect_fn(failure_trace)
    screen = screen_reflection(raw)
    if not screen["clean"]:
        return {"reflection": None, "tainted": True,
                "hits": screen["hits"]}
    return {"reflection": raw, "tainted": False, "hits": []}


def reflect_and_retry(step: dict, executor: Callable[[dict], dict],
                      reflect_fn: Callable[[str], str],
                      task_type: str = "") -> dict:
    """Execute a step with ONE bounded reflection-retry.

    First attempt: plain execution through the injected executor (the
    funnel). On failure: reflect once (taint-screened), attach the
    clean reflection (and the cached reasoning structure for
    ``task_type``, when known) to a single retry. A second failure
    ends the step as failed — the cap is a counter, and this function
    never retries twice. Returns the final outcome plus a reflection
    report (retries used, tainted flag, structure used).
    """
    structure = reasoning_structure(task_type) if task_type else None
    first = executor(step)
    if first.get("ok"):
        return {"outcome": first, "retries_used": 0, "reflected": False,
                "tainted": False, "structure": structure}
    # Audit A5: an outcome-unknown first attempt (timeout) is NEVER
    # reflection-retried — the abandoned call may still complete,
    # so a retry risks double execution. Unknown outcomes end the
    # step for a human decision, exactly like the checkpoint loop's
    # crash-mid-tool rule.
    if first.get("unknown"):
        return {"outcome": first, "retries_used": 0, "reflected": False,
                "tainted": False, "structure": structure}
    attempt = ReflectionAttempt(step=step,
                                failure=str(first.get("error", "failed")))
    reflection = reflect(attempt.failure, reflect_fn)
    attempt.tainted = reflection["tainted"]
    attempt.reflection = reflection["reflection"]
    # Exactly one retry, with the clean reflection attached (if any).
    retry_step = dict(step)
    if attempt.reflection:
        retry_step["_reflection"] = attempt.reflection
    if structure:
        retry_step["_reasoning_structure"] = structure["prompt"]
    attempt.retries_used = 1
    second = executor(retry_step)
    return {"outcome": second, "retries_used": attempt.retries_used,
            "reflected": True, "tainted": attempt.tainted,
            "structure": structure,
            "reflection": attempt.reflection}

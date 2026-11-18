"""Wave 8 (v5.63) — deep-think tier harness (BN-7-2/7-3 + 7-8).

ONE harness for the two deep-think candidates, with deliberate
budget forcing. This module is a HARNESS, not an engine: it holds
no weights, downloads nothing, imports no inference library, and
never produces a model answer. Any verdict about which candidate
wins is NO-DATA until a live engine is injected (see
``bakeoff_verdict``) — nothing here fabricates model output.

SILENT THINKING (load-bearing, by design):
  * There is NO synchronous entry point in this module. No
    ``think_sync``, no ``generate_sync``, no blocking ``run`` that a
    voice path could call and wait on. The only submission path is
    ``submit_deep_think``, which queues a job and returns
    immediately; the result is collected later, off the voice path,
    via ``collect``. A voice path that waited on deep thinking
    would stall speech; this module makes that call unspellable.
  * ``voice_path_entry_points`` returns the (empty) list of
    synchronous voice-path entry points, so a test can prove the
    absence rather than trust this comment.

BUDGET FORCING:
  * ``enforce_budget`` caps thinking that runs past ``max_tokens``
    (truncation is marked, never silent) and, when thinking falls
    short of ``min_tokens``, returns a *continuation directive* —
    the literal prompt fragment an engine adapter would append to
    make the engine keep thinking. The directive is prompt text,
    not generated reasoning, and it is never presented as an
    answer.

INV-21 — REPLACEMENT-TIER ONLY (recorded rule, enforced in code):
  * The 8 GB resident budget (``RESIDENT_BUDGET_GB``) already holds
    the resident brains. A co-resident 5.5-5.8 GB deep-think model
    does NOT fit beside them, so the deep-think tier may only
    REPLACE an existing tier's slot — it never adds a fourth
    resident brain. ``fits_as_addition`` refuses every candidate
    in the 5.5-5.8 GB class, and ``fits_as_replacement`` checks a
    candidate only against the slot it replaces.
  * Nemotron-Nano-8B and OpenThinker3-7B are NOT co-residents and
    NOT second deep-think seats: they compete for the single 8B
    slot (see ``slot_competition``) or they do not ship. This
    module records that rule; it does not download or size either
    competitor. OpenThinker3-7B is licence-clear for a harness
    (publisher card, Apache-2.0); Nemotron-Nano-8B stays OWED in
    ``LICENCE_VERDICTS``.

LICENCES (primary sources; no weights bundled):
  * Qwen3-4B-Thinking-2507 — Apache-2.0, from the repo's own
    LICENSE file on Hugging Face
    (huggingface.co/Qwen/Qwen3-4B-Thinking-2507/blob/main/LICENSE,
    "Apache License Version 2.0", copyright 2024 Alibaba Cloud),
    read 2026-10-25, full text re-verified 2026-11-03.
  * DeepSeek-R1-Distill-Qwen-7B — MIT, from the repo's own
    LICENSE file on Hugging Face
    (huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B/blob/main/LICENSE,
    "MIT License", copyright (c) 2023 DeepSeek), full text
    re-verified 2026-11-03; the series README §7 (2026-10-25 read)
    adds that the Qwen-2.5 base is Apache-2.0. Licence-clear
    for a harness; weight bundling still re-verifies at ship gate.
  * OpenThinker3-7B — Apache-2.0, from the publisher's own model
    card (open-thoughts/OpenThinker3-7B, "Intended uses &
    limitations: Apache 2.0 License"), read 2026-11-03; base
    Qwen2.5-7B-Instruct Apache-2.0. Licence-clear for a harness;
    no LICENSE file text was retrieved from the repo, so weights
    still re-verify at ship gate.
  * Nemotron-Nano-8B — OWED: governing terms identified as NVIDIA
    Open Model License + Llama 3.1 Community License Agreement,
    but neither text was retrieved in full, so it may not be
    bundled or sized into a ship plan on this module's say-so.

Versioned strings introduced by this module (for the coordinator's
pre-pin): ``MODULE_VERSION = "5.63.0"``; the model ids in
``MODELS``; ``"deep-think"`` as a tier name (formerly shared with
the s1 auto-router, removed Phase 3 C06 — this harness never
routed anything and still runs no engine). This module does NOT
touch core/version.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MODULE_VERSION = "5.63.0"

# --- INV-21 budget -------------------------------------------------------
RESIDENT_BUDGET_GB = 8.0
# Resident tier sizes from the plan's INV-21 table (GB, Q4_K_M class).
RESIDENT_TIER_GB = {"lite": 1.1, "balanced": 2.5, "quality": 5.0}
# The class of model the brief names as breaking the budget when
# added beside the residents (GB range, inclusive).
CORESIDENT_BREAKING_GB = (5.5, 5.8)

INV21_REPLACEMENT_ONLY_RULE = (
    "INV-21 replacement-tier ONLY: the deep-think tier replaces an "
    "existing tier slot; it never co-resides beside the resident "
    "brains. A 5.5-5.8 GB model added co-resident breaks the 8 GB "
    "budget. Nemotron-Nano-8B / OpenThinker3-7B compete for the "
    "single 8B slot or do not ship."
)

# --- Candidates ----------------------------------------------------------
MODELS = {
    # PILOT: the newer thinking specialist.
    "qwen3-4b-thinking": {
        "repo": "Qwen/Qwen3-4B-Thinking-2507",
        "role": "PILOT",
        "licence": "Apache-2.0",
        "approx_gb_q4": 2.5,
    },
    # ADOPT baseline / fallback: the established distilled reasoner.
    "r1-distill-qwen-7b": {
        "repo": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        "role": "ADOPT-baseline/fallback",
        "licence": "MIT",
        "approx_gb_q4": 4.7,
    },
    # 8B-slot competitors: recorded, never sized in, never shipped
    # without a live bake-off pick (see slot_competition). Repo ids
    # corrected 2026-11-03: the publisher model ids are
    # nvidia/Llama-3.1-Nemotron-Nano-8B-v1 (a Llama-3.1 derivative)
    # and open-thoughts/OpenThinker3-7B; the previous ids named no
    # such publisher repos.
    "nemotron-nano-8b": {
        "repo": "nvidia/Llama-3.1-Nemotron-Nano-8B-v1",
        "role": "8B-slot-competitor",
        "licence": "OWED",
        "approx_gb_q4": None,
    },
    "openthinker3-7b": {
        "repo": "open-thoughts/OpenThinker3-7B",
        "role": "8B-slot-competitor",
        "licence": "Apache-2.0",
        "approx_gb_q4": None,
    },
}

LICENCE_VERDICTS = {
    "qwen3-4b-thinking": {
        "licence": "Apache-2.0",
        "verdict": "CLEAR-for-harness",
        "primary_source": (
            "HF repo LICENSE file: "
            "huggingface.co/Qwen/Qwen3-4B-Thinking-2507/blob/main/LICENSE "
            "(Apache License Version 2.0, Copyright 2024 Alibaba Cloud), "
            "read 2026-10-25, full text re-verified 2026-11-03. "
            "No weights bundled by this module."
        ),
        "ships_weights": False,
    },
    "r1-distill-qwen-7b": {
        "licence": "MIT",
        "verdict": "CLEAR-for-harness",
        "primary_source": (
            "HF repo LICENSE file: "
            "huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B/blob/main/LICENSE "
            "(MIT License, Copyright (c) 2023 DeepSeek), read 2026-10-25, "
            "full text re-verified 2026-11-03; series README §7 "
            "(2026-10-25 read): Qwen-2.5 base Apache-2.0. "
            "No weights bundled by this module."
        ),
        "ships_weights": False,
    },
    "nemotron-nano-8b": {
        "licence": "OWED",
        "verdict": "OWED",
        "primary_source": (
            "Governing terms identified from publisher model-card "
            "summaries (2026-11-03): NVIDIA Open Model License + "
            "Llama 3.1 Community License Agreement ('Built with "
            "Llama'; card states 'ready for commercial use'). "
            "NEITHER licence text was retrieved in full this "
            "session, and the Llama overlay (acceptable-use policy "
            "+ scale re-licensing clause) needs review before any "
            "commercial ship decision. Do not bundle, do not size "
            "into a ship plan, until both texts are read and "
            "reviewed. Repo id corrected 2026-11-03 (was "
            "nvidia/Nemotron-Nano-8B-v1; publisher model is "
            "nvidia/Llama-3.1-Nemotron-Nano-8B-v1)."
        ),
        "ships_weights": False,
    },
    "openthinker3-7b": {
        "licence": "Apache-2.0",
        "verdict": "CLEAR-for-harness",
        "primary_source": (
            "Publisher model card (open-thoughts/OpenThinker3-7B), "
            "'Intended uses & limitations: Apache 2.0 License', "
            "read 2026-11-03 via the original-card text; base "
            "Qwen2.5-7B-Instruct Apache-2.0. NOTE: no LICENSE file "
            "text retrieved from the repo — weights re-verify at "
            "ship gate. Repo id corrected 2026-11-03 (was "
            "OpenThinker/OpenThinker3-7B; publisher org is "
            "open-thoughts). No weights bundled by this module."
        ),
        "ships_weights": False,
    },
}


def licence_verdict(model_key: str) -> dict | None:
    """The licence verdict record for a candidate, or None if unknown."""
    return LICENCE_VERDICTS.get(model_key)


# --- INV-21 accounting ----------------------------------------------------
def fits_as_addition(candidate_gb: float, resident_total_gb: float) -> bool:
    """True only if adding the candidate beside the residents fits.

    Any candidate in the 5.5-5.8 GB class is refused outright when
    the residents already occupy the budget's bulk: addition is
    not a shape this tier is allowed to take (see the rule above).
    """
    lo, hi = CORESIDENT_BREAKING_GB
    if lo <= candidate_gb <= hi:
        return False
    return resident_total_gb + candidate_gb <= RESIDENT_BUDGET_GB


def fits_as_replacement(candidate_gb: float, replaced_slot_gb: float,
                        other_resident_gb: float) -> bool:
    """True only if the candidate fits in the slot it replaces."""
    return other_resident_gb + candidate_gb <= RESIDENT_BUDGET_GB


def slot_competition(candidates: list[str]) -> dict:
    """The single-8B-slot rule, as data.

    Returns which candidates are competing for the one 8B slot and
    the refusal that applies to shipping more than one of them.
    No candidate is picked here: picking needs licence clearance
    (OWED for both competitors) plus a live bake-off.
    """
    competing = [c for c in candidates if c in
                 ("nemotron-nano-8b", "openthinker3-7b", "quality")]
    return {
        "slot": "8B",
        "competing": competing,
        "rule": ("At most ONE 8B-slot model ships. The rest do not "
                 "ship — they are not second seats, fallbacks, or "
                 "co-residents."),
        "pick": None,
        "closing_step": ("Verify each competitor's licence from a "
                         "primary source, then run the live bake-off; "
                         "until then the pick stays None."),
    }


# --- Budget forcing --------------------------------------------------------
@dataclass(frozen=True)
class ThinkBudget:
    """Deliberate thinking budget for one deep-think job."""
    min_tokens: int = 256
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        if self.min_tokens < 0 or self.max_tokens < self.min_tokens:
            raise ValueError(
                f"invalid ThinkBudget min={self.min_tokens} "
                f"max={self.max_tokens}")


CONTINUATION_DIRECTIVE = (
    "Wait — think further before answering: check the reasoning "
    "above for a missed case, then continue thinking."
)
TRUNCATION_MARKER = "[thinking capped at budget]"


def count_tokens_approx(text: str) -> int:
    """Whitespace-token estimate. Deliberately crude and labelled:
    the engine's own tokenizer is authoritative; this estimate only
    drives the harness's cap/extend decisions in tests and in the
    no-engine path, never a billing or quality claim."""
    return len(str(text or "").split())


def split_thinking(raw_output: str) -> tuple[str, str]:
    """Split a thinking-model output into (thinking, answer).

    Qwen thinking models emit ``</think>`` as the thinking/answer
    boundary; an output without the marker is treated as answer
    only (thinking empty), never guessed into thinking text."""
    text = str(raw_output or "")
    if "</think>" in text:
        thinking, answer = text.split("</think>", 1)
        thinking = thinking.replace("<think>", "", 1)
        return thinking.strip(), answer.strip()
    return "", text.strip()


def enforce_budget(thinking: str, budget: ThinkBudget) -> dict:
    """Cap or extend thinking deliberately.

    Returns a dict with the (possibly adjusted) thinking text, the
    action taken (``kept`` / ``capped`` / ``extend``), and, for
    ``extend``, the continuation directive the engine adapter must
    append — prompt text, not reasoning. Nothing here generates
    reasoning content."""
    tokens = count_tokens_approx(thinking)
    if tokens > budget.max_tokens:
        words = str(thinking).split()[: budget.max_tokens]
        return {
            "thinking": " ".join(words) + " " + TRUNCATION_MARKER,
            "action": "capped",
            "tokens_before": tokens,
            "tokens_after": budget.max_tokens,
            "continuation_directive": None,
        }
    if tokens < budget.min_tokens:
        return {
            "thinking": thinking,
            "action": "extend",
            "tokens_before": tokens,
            "tokens_after": tokens,
            "continuation_directive": CONTINUATION_DIRECTIVE,
        }
    return {
        "thinking": thinking,
        "action": "kept",
        "tokens_before": tokens,
        "tokens_after": tokens,
        "continuation_directive": None,
    }


# --- Async-only submission (silent thinking) -------------------------------
@dataclass
class DeepThinkJob:
    """A queued deep-think job. Queued, never executed here: there
    is no engine in this module, so a job has no result until a
    coordinator-supplied engine adapter collects it off the voice
    path. ``result`` stays None by construction in this module."""
    job_id: str
    prompt: str
    model_key: str
    budget: ThinkBudget = field(default_factory=ThinkBudget)
    result: object = None


def voice_path_entry_points() -> list[str]:
    """Synchronous voice-path entry points: NONE, by design.

    The voice path never waits on deep thinking, and this module
    gives it no function to wait with. A test asserts this list is
    empty; adding a sync entry point must update this list and
    that test, deliberately, not silently."""
    return []


def submit_deep_think(job_id: str, prompt: str, model_key: str,
                      budget: ThinkBudget | None = None) -> DeepThinkJob:
    """Queue a deep-think job and return IMMEDIATELY.

    This function does not run a model, does not block, and does
    not return an answer. The caller collects the result later,
    off the voice path, once a real engine adapter exists. An
    unknown model key is refused rather than queued under a
    guessed model."""
    if model_key not in MODELS:
        raise ValueError(f"unknown deep-think model: {model_key!r}")
    if not str(prompt or "").strip():
        raise ValueError("deep-think prompt must not be empty")
    return DeepThinkJob(job_id=job_id, prompt=prompt, model_key=model_key,
                        budget=budget or ThinkBudget())


# --- Engine adapter registry (coordinator seam, wired 2026-11-03) ---
# LIVE-ENGINE GATE (stated, not hidden): the registry + collect path
# below are wired and licence-gated, but no live weights exist on
# this VM and no inference runtime is in the tree. A live adapter
# still needs: (1) weights for a CLEAR-for-harness model, obtained
# at ship time, never bundled in this module; (2) a runtime (not in
# tree); (3) the ship-gate licence re-check for weights; (4) field
# proof on Windows. Until then, bakeoff stays NO-DATA and collect
# without a registered adapter stays NO-DATA. Registration is NOT
# shipping: registering both bake-off candidates is allowed, and
# INV-21 still permits at most ONE to ship, decided by live runs.
_ENGINE_ADAPTERS: dict[str, object] = {}


def _adapter_generate(adapter: object) -> object:
    """The generate callable of an engine adapter, or None.

    The wiring_seam contract is generate(prompt, budget) -> str.
    An adapter may BE that callable, or HOLD it as a .generate
    attribute. Anything else is not an adapter."""
    if callable(adapter):
        return adapter
    generate = getattr(adapter, "generate", None)
    if callable(generate):
        return generate
    return None


def register_engine_adapter(model_key: str, adapter: object) -> None:
    """Register an engine adapter for one deep-think candidate.

    LICENCE GATE (enforced, not documented-only): the model's
    LICENCE_VERDICTS verdict must be CLEAR-for-harness. An OWED or
    unknown model cannot be registered even by accident — an OWED
    licence is a gate, not a warning. Unknown model keys and
    non-adapters are refused the same way. Re-registering a key
    replaces its adapter (bake-off swaps are legitimate)."""
    if model_key not in MODELS:
        raise ValueError(f"unknown deep-think model: {model_key!r}")
    verdict = LICENCE_VERDICTS.get(model_key)
    if verdict is None or verdict.get("verdict") != "CLEAR-for-harness":
        raise ValueError(
            f"cannot register an engine adapter for {model_key!r}: "
            f"licence verdict is "
            f"{verdict.get('verdict') if verdict else None!r}, "
            "not CLEAR-for-harness")
    if _adapter_generate(adapter) is None:
        raise ValueError(
            "engine adapter must be callable as generate(prompt, budget) "
            "-> str, or hold such a .generate attribute")
    _ENGINE_ADAPTERS[model_key] = adapter


def get_engine_adapter(model_key: str) -> object | None:
    """The registered engine adapter for a model, or None."""
    return _ENGINE_ADAPTERS.get(model_key)


def clear_engine_adapters() -> None:
    """Forget all registered adapters (tests + coordinator reset)."""
    _ENGINE_ADAPTERS.clear()


def collect(job: DeepThinkJob, adapter: object = None) -> dict:
    """Collect a queued job's result.

    With an engine adapter — registered for the job's model, or
    passed explicitly — the adapter runs through the harness's own
    discipline: its raw output is split by ``split_thinking`` and
    forced through ``enforce_budget``. A capped run is marked, never
    silent; a short run reports ``needs-continuation`` with the
    continuation directive, never a faked complete answer; an
    adapter failure, a non-string output, or an adapter for a model
    whose licence is not CLEAR-for-harness reports ``error``, never
    a fabricated result. With no adapter, the result is NO-DATA
    exactly as before: nothing was run, nothing is claimed. The
    queueing rule is untouched: submission stays async-only, and
    this function must still never be called from a voice path."""
    use = adapter if adapter is not None else _ENGINE_ADAPTERS.get(
        job.model_key)
    if use is None:
        return {
            "job_id": job.job_id,
            "model_key": job.model_key,
            "status": "NO-DATA",
            "reason": ("No deep-think engine is wired in: no weights "
                       "exist on this VM and no engine adapter is "
                       "registered. Nothing was run, nothing is claimed."),
            "thinking": None,
            "answer": None,
        }
    verdict = LICENCE_VERDICTS.get(job.model_key)
    if verdict is None or verdict.get("verdict") != "CLEAR-for-harness":
        return {
            "job_id": job.job_id,
            "model_key": job.model_key,
            "status": "error",
            "reason": ("engine adapter refused: licence verdict for "
                       f"{job.model_key!r} is "
                       f"{verdict.get('verdict') if verdict else None!r}, "
                       "not CLEAR-for-harness."),
            "thinking": None,
            "answer": None,
        }
    generate = _adapter_generate(use)
    if generate is None:
        return {
            "job_id": job.job_id,
            "model_key": job.model_key,
            "status": "error",
            "reason": ("The engine adapter for "
                       f"{job.model_key!r} is not callable as "
                       "generate(prompt, budget) -> str."),
            "thinking": None,
            "answer": None,
        }
    try:
        raw = generate(job.prompt, job.budget)
    except Exception as exc:  # noqa: BLE001 — adapter failure is data
        return {
            "job_id": job.job_id,
            "model_key": job.model_key,
            "status": "error",
            "reason": f"engine adapter failed: {exc}",
            "thinking": None,
            "answer": None,
        }
    if not isinstance(raw, str):
        return {
            "job_id": job.job_id,
            "model_key": job.model_key,
            "status": "error",
            "reason": ("engine adapter must return str (raw model "
                       f"output); got {type(raw).__name__}."),
            "thinking": None,
            "answer": None,
        }
    thinking, answer = split_thinking(raw)
    enforced = enforce_budget(thinking, job.budget)
    return {
        "job_id": job.job_id,
        "model_key": job.model_key,
        "status": ("completed" if enforced["action"] != "extend"
                   else "needs-continuation"),
        "thinking": enforced["thinking"],
        "answer": answer,
        "action": enforced["action"],
        "tokens_before": enforced["tokens_before"],
        "tokens_after": enforced["tokens_after"],
        "continuation_directive": enforced["continuation_directive"],
    }


# --- Bake-off harness --------------------------------------------------------
@dataclass(frozen=True)
class ThinkRun:
    """One bake-off run, supplied by a live engine or a labelled
    fixture. ``live=False`` runs never select a winner."""
    model_key: str
    task_id: str
    correct: bool
    thinking_tokens: int
    latency_ms: float | None = None
    language: str = "en"
    live: bool = False


def bakeoff_verdict(runs: list[ThinkRun]) -> dict:
    """Pick a winner from LIVE runs only, or return NO-DATA.

    Winner rule (documented, deterministic): highest accuracy;
    ties break toward fewer thinking tokens. A fixture-only or
    empty set yields NO-DATA — stub results never crown a model.
    Hindi runs are reported split (``hi_correct``) alongside the
    overall figure, never collapsed into one number."""
    live = [r for r in runs if r.live]
    if not live:
        return {
            "winner": None,
            "verdict": "NO-DATA",
            "reason": ("No live engine runs: no weights on this VM, "
                       "no engine adapter wired in."),
            "closing_step": ("Register live engine adapters (weights + "
                             "runtime) for both candidates, run the frozen "
                             "eval incl. Hindi items, then re-run this "
                             "verdict on live runs only."),
        }
    by_model: dict[str, list[ThinkRun]] = {}
    for run in live:
        by_model.setdefault(run.model_key, []).append(run)

    def _score(rs: list[ThinkRun]) -> tuple[float, float]:
        acc = sum(1 for r in rs if r.correct) / len(rs)
        avg_tokens = sum(r.thinking_tokens for r in rs) / len(rs)
        return acc, -avg_tokens

    ranked = sorted(by_model.items(),
                    key=lambda kv: _score(kv[1]), reverse=True)
    top, top_runs = ranked[0]
    top_acc = _score(top_runs)[0]
    tied = [m for m, rs in ranked if _score(rs)[0] == top_acc
            and _score(rs)[1] == _score(top_runs)[1]]
    hi = [r for r in live if r.language == "hi"]
    return {
        "winner": top if len(tied) == 1 else None,
        "verdict": "LIVE" if len(tied) == 1 else "TIE-NO-WINNER",
        "accuracy": {m: round(_score(rs)[0], 4)
                     for m, rs in by_model.items()},
        "hi_runs": len(hi),
        "hi_correct": sum(1 for r in hi if r.correct),
        "tied": tied,
        "closing_step": ("If TIE-NO-WINNER: extend the eval, do not "
                         "pick on a tie."),
    }


# --- Coordinator seam --------------------------------------------------------
def wiring_seam() -> dict:
    """What the coordinator wires, and what it must NOT wire.

    The coordinator supplies an engine adapter with
    ``generate(prompt, budget) -> raw_output`` and a collector that
    feeds ``split_thinking`` + ``enforce_budget``. It must NOT call
    any deep-think function from a voice-path thread, and must NOT
    register a second deep-think model as a co-resident."""
    return {
        "submit": "submit_deep_think (async-only queue; returns a job)",
        "collect": ("collect (NO-DATA until an engine adapter is "
                    "registered; a registered adapter runs through "
                    "split_thinking + enforce_budget)"),
        "engine_adapter_registry": ("register_engine_adapter "
                                    "(licence-gated: CLEAR-for-harness "
                                    "only) / get_engine_adapter / "
                                    "clear_engine_adapters"),
        "engine_adapter_contract": "generate(prompt, budget) -> str",
        "budget": "ThinkBudget via enforce_budget (cap/extend)",
        "must_not_wire": [
            "any synchronous voice-path call to deep thinking",
            "a co-resident second deep-think model (INV-21)",
            "weights before the ship-gate licence re-check",
        ],
        "disposition": "PILOT-harness (Qwen3-4B-Thinking-2507) vs "
                        "ADOPT-baseline (R1-Distill-Qwen-7B); "
                        "verdict NO-DATA until live runs exist",
    }

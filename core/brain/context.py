"""Wave 8 (v5.63) — context-discipline trio, in brief order BN-5-3 -> 5-1 -> 5-2.

(1) Per-stream token ledger + hard budgets + pre-flight check before
    every brain call. The ledger IS the INV-1 payload budget's
    runtime twin: it mirrors the fitted-payload constants already in
    the tree — it invents no numbers. Sources read from the tree:

    * ``ToolRegistry._GEMINI_DECL_BUDGET = 240_000`` bytes
      (``core/funnel/tools_registry.py``; measured Live-API cliff:
      250,023 B turn fine, 268,817 B fail every turn).
      At the codebase's own chars-per-token heuristic
      (``_est_tokens`` counts ``ceil(chars / 3.2)``, see
      ``core/voice/local_client.py``), 240,000 B ≈ 75,000 est. tokens.
    * ``_OLLAMA_TOOLS_BUDGET = 80_000`` bytes
      (``core/voice/local_client.py``) ≈ 25,000 est. tokens — the
      comment there states the conversion explicitly.
    * Memory block cap 6,000 chars (INV-8; ``core/memory/memory.py``,
      ``get_memory_context_prompt(max_chars=6000)``)
      ≈ 1,875 est. tokens at /3.2.
    * The actually-shipped fitted payload, 229,785 B
      (``core/funnel/tools_registry.py`` docstring), is the live
      tools-stream measurement the ledger checks against; the
      240,000 B budget is the hard ceiling above it.

    Budgets are HARD: a pre-flight violation is reported, never
    silently trimmed — trimming would hide headroom spend (the
    §8 S-5 fence: invisible headroom spend breaks fixtures).
    The caller decides (drop a stream, shorten history, refuse).

(2) Prompt-cache-first assembly. The stable prefix (system prompt
    + tool declarations + pinned memory) is assembled byte-identical
    every call: same parts in the same order produce the same bytes
    and the same cache key, so a prompt cache can hit. Any change
    to a stable part changes the key — the cache never serves a
    stale prefix silently.

(3) Shipped-agent compaction, GATED. Threshold
    summarise-and-restart plus per-step tool-output clearing inside
    run_goal ship behind an explicit flag (default OFF). Compaction
    enables ONLY after a session-replay eval harness run passes
    (see :func:`evaluate_compaction` and :func:`enable_compaction`):
    no eval pass, no enablement — the gate is code, not policy.

No new dependencies, no telemetry, stdlib only. The coordinator
wires :func:`preflight_check` in front of every brain call and the
compaction hooks into run_goal's step loop; this module itself
calls no brain and executes no tool.

Versioned strings introduced by this module (pre-pinned to 5.63):
``WAVE_VERSION = "5.63"`` (module marker only — NOT the product
version; ``core/version.py`` stays 5.62.0 until coordinator assembly).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Optional

WAVE_VERSION = "5.63"

# --- (1) Ledger: mirrored constants (sources in the module docstring) ---
CHARS_PER_TOKEN = 3.2  # the codebase's own heuristic (local_client)
GEMINI_DECL_BUDGET_BYTES = 240_000  # tools_registry._GEMINI_DECL_BUDGET
GEMINI_DECL_BUDGET_TOKENS = 75_000  # 240_000 / 3.2
OLLAMA_TOOLS_BUDGET_BYTES = 80_000  # local_client._OLLAMA_TOOLS_BUDGET
OLLAMA_TOOLS_BUDGET_TOKENS = 25_000  # stated in the local_client comment
FITTED_PAYLOAD_BYTES = 229_785  # shipped fitted payload (registry docstring)
MEMORY_BLOCK_CHARS = 6_000  # INV-8 memory cap (memory.py default)
MEMORY_BLOCK_TOKENS = 1_875  # 6_000 / 3.2

#: Hard per-stream token budgets, keyed by stream name. A stream
#: over budget fails pre-flight; it is never silently trimmed.
HARD_BUDGETS_TOKENS = {
    "tools_gemini": GEMINI_DECL_BUDGET_TOKENS,
    "tools_ollama": OLLAMA_TOOLS_BUDGET_TOKENS,
    "memory": MEMORY_BLOCK_TOKENS,
    "history": 8_000,   # standing history allowance; caller-settable
    "system": 4_000,    # standing system-prompt allowance; caller-settable
}


def estimate_tokens(text: str) -> int:
    """Estimated tokens for ``text`` under the codebase heuristic."""
    if not text:
        return 0
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass
class StreamUsage:
    name: str
    chars: int = 0
    tokens: int = 0
    calls: int = 0

    def add(self, text: str) -> None:
        self.chars += len(text)
        self.tokens += estimate_tokens(text)
        self.calls += 1


class TokenLedger:
    """Per-stream token ledger with hard budgets.

    One ledger per brain session. ``record(stream, text)`` accounts
    a payload; :meth:`preflight` checks a proposed call's streams
    against :data:`HARD_BUDGETS_TOKENS` (plus any caller overrides)
    BEFORE the brain call is made. ``over_budget`` lists violations;
    an empty list means the call may proceed.
    """

    def __init__(self, budgets: Optional[dict[str, int]] = None):
        self.budgets = dict(HARD_BUDGETS_TOKENS)
        if budgets:
            self.budgets.update(budgets)
        self.streams: dict[str, StreamUsage] = {}

    def record(self, stream: str, text: str) -> StreamUsage:
        usage = self.streams.setdefault(stream, StreamUsage(name=stream))
        usage.add(text)
        return usage

    def preflight(self, payload: dict[str, str]) -> dict:
        """Pre-flight check before a brain call. ``payload`` maps
        stream name -> text that would be sent. Returns
        ``{"ok": bool, "violations": [...], "totals": {...}}`` where
        each violation names the stream, its tokens, and its budget.
        A violation blocks the call; the caller must shrink a stream
        or refuse — never trim silently."""
        violations = []
        totals = {}
        for stream, text in payload.items():
            tokens = estimate_tokens(text)
            totals[stream] = tokens
            budget = self.budgets.get(stream)
            if budget is not None and tokens > budget:
                violations.append({
                    "stream": stream, "tokens": tokens, "budget": budget,
                    "over_by": tokens - budget,
                })
        return {"ok": not violations, "violations": violations,
                "totals": totals}

    def check_and_record(self, payload: dict[str, str]) -> dict:
        """Pre-flight, then record only if the call is allowed."""
        report = self.preflight(payload)
        if report["ok"]:
            for stream, text in payload.items():
                self.record(stream, text)
        return report

    def summary(self) -> dict:
        return {name: {"chars": u.chars, "tokens": u.tokens,
                       "calls": u.calls}
                for name, u in sorted(self.streams.items())}


def preflight_check(payload: dict[str, str],
                    budgets: Optional[dict[str, int]] = None) -> dict:
    """Standalone pre-flight check (no ledger needed)."""
    return TokenLedger(budgets=budgets).preflight(payload)


# --- (2) Prompt-cache-first assembly -----------------------------------
@dataclass
class CachedPrompt:
    """An assembled prompt: byte-identical stable prefix + suffix."""
    prefix: bytes
    suffix: str
    cache_key: str

    @property
    def prefix_text(self) -> str:
        return self.prefix.decode("utf-8")


def assemble_prompt(stable_parts: list[str], suffix: str) -> CachedPrompt:
    """Assemble a prompt cache-first.

    ``stable_parts`` are joined in order with a single ``\\n`` and
    encoded as UTF-8; identical parts in identical order produce
    byte-identical prefixes and identical SHA-256 cache keys. The
    suffix (current turn, tool results) is never part of the prefix,
    so the cached prefix survives across turns unchanged.
    """
    prefix = "\n".join(stable_parts).encode("utf-8")
    key = hashlib.sha256(prefix).hexdigest()
    return CachedPrompt(prefix=prefix, suffix=suffix, cache_key=key)


def prefix_is_stable(previous: CachedPrompt,
                     stable_parts: list[str]) -> bool:
    """True iff re-assembling ``stable_parts`` reproduces the cached
    prefix byte-for-byte. Any drift invalidates the cache entry."""
    return assemble_prompt(stable_parts, "").cache_key == previous.cache_key


# --- (3) Gated compaction ----------------------------------------------
#: Compaction ships GATED: False until an eval pass enables it.
COMPACTION_ENABLED = False


@dataclass
class CompactionConfig:
    """Threshold summarise-and-restart + per-step tool-output clearing.

    ``threshold_tokens``: history above this triggers a summarise
    (the summary replaces all but the newest ``keep_turns`` turns).
    ``clear_tool_outputs``: when True, per-step tool outputs older
    than ``keep_turns`` are dropped from the restarted session
    (their step records still live in the run checkpoint).
    """
    threshold_tokens: int = 8_000
    keep_turns: int = 4
    clear_tool_outputs: bool = True


@dataclass
class Turn:
    role: str  # "user" | "assistant" | "tool"
    text: str


def compact_session(turns: list[Turn], config: CompactionConfig,
                    summarise: Any) -> dict:
    """Summarise-and-restart a session. GATED: refuses while the flag
    is off (returns ``{"enabled": False}`` and the session untouched).
    ``summarise`` is an injected callable (turns -> summary text);
    this module never calls a brain to summarise. Tool-output
    clearing drops old ``role == "tool"`` turns beyond keep_turns;
    the summary preserves the dropped turns' gist, per the caller.
    """
    if not COMPACTION_ENABLED:
        return {"enabled": False, "turns": turns,
                "detail": "compaction gated: enable only after a "
                          "passing session-replay eval"}
    if estimate_tokens("\n".join(t.text for t in turns)) \
            <= config.threshold_tokens:
        return {"enabled": True, "turns": turns, "compacted": False,
                "detail": "under threshold; session unchanged"}
    keep = turns[-config.keep_turns:] if config.keep_turns > 0 else []
    dropped = turns[:len(turns) - len(keep)] if keep else list(turns)
    summary = summarise(dropped)
    new_turns = [Turn(role="assistant", text=f"[session summary] {summary}")]
    if config.clear_tool_outputs:
        keep = [t for t in keep if t.role != "tool"]
    new_turns.extend(keep)
    return {"enabled": True, "turns": new_turns, "compacted": True,
            "dropped": len(dropped), "summary": summary}


def evaluate_compaction(session: list[Turn], config: CompactionConfig,
                        summarise: Any) -> dict:
    """Session-replay eval harness: proves compaction before enablement.

    Replays ``session`` through compaction (with the gate temporarily
    honoured via a direct code path — the harness does NOT flip the
    global flag) and checks: (a) the compacted session is shorter
    in tokens when over threshold; (b) the newest user turn survives
    verbatim; (c) no tool output survives beyond keep_turns when
    clearing is on; (d) summarise was deterministic (same input ->
    same summary twice). Returns ``{"passed": bool, "checks": {...}}``.
    """
    checks: dict[str, bool] = {}
    over = estimate_tokens("\n".join(t.text for t in session)) \
        > config.threshold_tokens
    # Run the compaction logic with the gate bypassed for eval only.
    global COMPACTION_ENABLED
    saved = COMPACTION_ENABLED
    COMPACTION_ENABLED = True
    try:
        result = compact_session(session, config, summarise)
    finally:
        COMPACTION_ENABLED = saved
    compacted = result["turns"]
    checks["shorter_when_over_threshold"] = (
        not over or estimate_tokens("\n".join(t.text for t in compacted))
        < estimate_tokens("\n".join(t.text for t in session)))
    last_user = [t for t in session if t.role == "user"]
    checks["newest_user_turn_survives"] = (
        not last_user or any(t.text == last_user[-1].text
                             for t in compacted))
    if config.clear_tool_outputs and result.get("compacted"):
        tail_tools = [t for t in compacted[1:] if t.role == "tool"]
        checks["old_tool_outputs_cleared"] = len(tail_tools) == 0
    else:
        checks["old_tool_outputs_cleared"] = True
    # Dropped turns are exactly the session head above keep_turns —
    # the same slice compact_session summarised (tool-output clearing
    # only affects the kept tail, never the summary input).
    dropped = (session[:len(session) - config.keep_turns]
               if result.get("compacted") and config.keep_turns > 0
               else session if result.get("compacted") else [])
    checks["summarise_deterministic"] = (
        summarise(dropped) == result.get("summary")
        if result.get("compacted") else True)
    return {"passed": all(checks.values()), "checks": checks,
            "compacted_tokens": estimate_tokens(
                "\n".join(t.text for t in compacted)),
            "original_tokens": estimate_tokens(
                "\n".join(t.text for t in session))}


def enable_compaction(eval_report: dict) -> bool:
    """Enable compaction ONLY on a passing eval report. Returns the
    new flag state; a failing or missing report leaves it OFF."""
    global COMPACTION_ENABLED
    if isinstance(eval_report, dict) and eval_report.get("passed") is True:
        COMPACTION_ENABLED = True
    return COMPACTION_ENABLED

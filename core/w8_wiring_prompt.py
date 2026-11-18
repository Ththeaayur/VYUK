"""w8_wiring part module (skills split B14): w8_wiring_prompt."""
from __future__ import annotations

# Module-level imports travel verbatim with the units that use them.
from .brain import context  # Phase 3 B1a-1: moved to core/brain/
from . import learning_feed, sleeptime
from .memory.memory_files import list_facts
# INTENTIONAL (skills split B14): cross-part caps from gates (P3).
from .w8_wiring_gates import _MAX_LEARNING_LINES, _MAX_SUPPLEMENT_CHARS



# ---------------------------------------------------------------------
# Context ledger + budgets + pre-flight + prompt-cache-first (items 2, 3)
# ---------------------------------------------------------------------

_LAST_STABLE_PREFIX: dict[str, Any] = {"key": None, "parts": None}




def note_stable_prefix(stable_parts: list[str]):
    """Assemble the byte-identical stable prefix + SHA-256 key
    for a live prompt's stable parts (persona + language
    directive), via context.assemble_prompt. Records the
    key so a later assembly can prove stability
    (prefix_is_stable) instead of assuming it. Pure: it never
    mutates the caller's prompt text."""
    cached = context.assemble_prompt(list(stable_parts), "")
    _LAST_STABLE_PREFIX["key"] = cached.cache_key
    _LAST_STABLE_PREFIX["parts"] = list(stable_parts)
    return cached




def stable_prefix_key() -> Optional[str]:
    """The SHA-256 key of the most recent stable-prefix assembly."""
    return _LAST_STABLE_PREFIX.get("key")




def stable_prefix_is_stable(stable_parts: list[str]) -> bool:
    """True iff re-assembling ``stable_parts`` reproduces the
    last recorded prefix byte-for-byte (cache-hit test)."""
    previous_key = _LAST_STABLE_PREFIX.get("key")
    if previous_key is None:
        return False
    cached = context.assemble_prompt(list(stable_parts), "")
    return cached.cache_key == previous_key




def preflight_prompt(streams: dict[str, str]) -> dict:
    """Pre-flight a live prompt's streams against the hard
    budgets (context.TokenLedger). Returns the module's
    report: {"ok", "violations", "totals"}."""
    return context.TokenLedger().preflight(streams)




def enforce_prompt(prompt: str, memory_text: str,
                   supplements: str) -> tuple[str, dict]:
    """Enforce budgets on an assembled live prompt, fail-closed.

    If the full prompt (system + memory streams) passes
    pre-flight, it is returned unchanged with its report. If
    it fails, the optional supplement streams are dropped and
    the prompt re-checked; if it STILL fails, ValueError is
    raised (refuse) — an over-budget prompt is never sent and
    never silently trimmed. ``supplements`` must be the exact
    block prompt_supplements() appended, so dropping it is
    exact, not a fuzzy cut.
    """
    report = preflight_prompt({"system": prompt, "memory": memory_text})
    if report["ok"]:
        return prompt, report
    if supplements and supplements in prompt:
        stripped = prompt.replace(supplements, "", 1)
        report2 = preflight_prompt({"system": stripped,
                                    "memory": memory_text})
        if report2["ok"]:
            return stripped, dict(report2, supplements_dropped=True)
    raise ValueError(
        "prompt over hard budget (refusing fail-closed): "
        f"{report['violations']}")




# ---------------------------------------------------------------------
# PF-28 learning feed + sleeptime block -> prompt supplements (items 8, 9)
# ---------------------------------------------------------------------


def learning_lines(directory=None, limit: int = _MAX_LEARNING_LINES) -> list[str]:
    """fact_prompt_lines() from the live feed: opinions never
    included, expired inferred records excluded (the module
    honours both). Empty feed -> empty list -> prompt unchanged."""
    try:
        feed = learning_feed.LearningFeed(directory=directory)
        return feed.fact_prompt_lines()[:limit]
    except Exception:  # noqa: BLE001 — prompt assembly never breaks
        return []




def sleeptime_block(base=None, query: Optional[str] = None) -> str:
    """build_memory_block() over the FILE STORE only
    (memory_files.list_facts). The frozen DB is never
    opened here: sleeptime has no sqlite call path, and this
    helper reads files only. Empty store -> ""."""
    try:
        records, _skipped = list_facts(base=base)
    except Exception:  # noqa: BLE001 — prompt assembly never breaks
        return ""
    if not records:
        return ""
    try:
        block, _ranked = sleeptime.build_memory_block(
            records, query=query)
    except Exception:  # noqa: BLE001
        return ""
    return block




def prompt_supplements(feed_dir=None, memories_base=None) -> str:
    """The optional prompt supplement block: learning-feed fact
    lines + the sleeptime ranked file-store block, capped.
    Returns "" when both sources are empty, so a default
    install's prompts stay byte-identical. Learning lines and
    the sleeptime block are SEPARATE labelled sections — the
    sleeptime block ranks file-store facts for the capped
    memory budget; it does not replace the dual-read block."""
    parts: list[str] = []
    lines = learning_lines(directory=feed_dir)
    if lines:
        parts.append("What I have learned (learning feed):\n"
                     + "\n".join(lines))
    block = sleeptime_block(base=memories_base)
    if block:
        parts.append("Ranked memory (file store, sleeptime-ranked):\n"
                     + block)
    if not parts:
        return ""
    text = "\n\n".join(parts)
    if len(text) > _MAX_SUPPLEMENT_CHARS:
        text = text[:_MAX_SUPPLEMENT_CHARS].rsplit("\n", 1)[0]
    return "\n\n" + text




def record_learning_event(what: str, source: str,
                          kind: str = "fact", directory=None):
    """Append one learning event to the PF-28 feed. The live
    caller is core/learning.py::learn_from_text (a stored
    learned fact is a learning event). Never raises: feed
    bookkeeping must not break learning. Per-row expiry dates
    are OWED (see LEARNING_EXPIRY_OWED) — none is invented."""
    try:
        feed = learning_feed.LearningFeed(directory=directory)
        return feed.append(what=what, source=source, kind=kind)
    except Exception:  # noqa: BLE001
        return None



"""Wave 8 (v5.63) — Sleeptime consolidation trio (BN-8-1/8-2/8-3).

Wired via w8_wiring.run_sleeptime_pass / maybe_sleeptime_on_idle
and triggered by the watcher away-episode hook (core.watchers
WatcherEngine sleeptime_hook, wired in gui/window_memory.
_init_watchers); this module itself schedules nothing, and it
never touches the frozen Wave 6 DB. The ONLY writable surface
it uses is Wave 6's Markdown+frontmatter file store
(core/memory_files.write_fact / list_facts), reached through an
injectable ``writer`` / ``base`` so tests run against tmp dirs
and never against the tree's data/.

The three pieces:
1. BN-8-1 idle-time worker — idle_pass(): one list_facts walk,
   promote only heat-eligible candidates, otherwise zero writes.
2. BN-8-2 recall ranking — rank_records(): a PURE function over
   record dicts (no store, no I/O), deciding what fills the
   capped memory block.
3. BN-8-3 heat-based promotion — promotion_eligible() gates
   every long-term write on accumulated recall heat.

Budget discipline (INV-8): the ranker fills the SAME 6,000-char
block memory.py already caps (MEMORY_BLOCK_BUDGET_CHARS = 6000).
There is no second budget: the budget is a parameter defaulting
to 6000, and build_memory_block() never emits more than it.

Ethics carried from Wave 6 (CP-4.7, forgetting-by-design): kind
``opinion`` records NEVER rank into a block, and expired
``inferred`` records (past expires_at) NEVER rank in either.
The ranker drops both silently from the block — they stay
retrievable through list_facts(kind=...) exactly as before.

Version strings introduced by this module: none shipped in the
tree besides the module's own docstring "v5.63" label and the
mined_in-style marker is NOT used here; the only literal is
``SLEEPTIME_SCHEMA_VERSION = 1`` (an int, not a product version).
core/version.py is untouched and still reads 5.62.0 until the
coordinator assembles 5.63.0.
"""

import math
import os
import re
from datetime import date

from .memory.memory_files import (
    is_expired_inferred,
    list_facts,
    normalize_day,
    normalize_ts,
    write_fact,
)

MEMORY_BLOCK_BUDGET_CHARS = 6000
RECENCY_HALF_LIFE_DAYS = 30.0
PROMOTION_HEAT_THRESHOLD = 3
SLEEPTIME_SCHEMA_VERSION = 1

# Pinned categories/keys, copied from memory.py (B4, v5.45) so the
# ranker agrees with the prompt path it feeds: who the user IS
# and what they prefer must never be crowded out.
PINNED_CATEGORIES = frozenset({
    "identity", "identities", "profile", "personal",
    "preference", "preferences", "user", "about",
})
PINNED_KEYS = frozenset({
    "name", "user_name", "full_name", "my_name", "username",
})

_KIND_DEFAULT_IMPORTANCE = {
    "person": 0.8, "promise": 0.8, "fact": 0.5, "inferred": 0.4,
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text):
    return {t for t in _TOKEN_RE.findall(str(text or "").lower())
            if len(t) >= 3}


def _parse_day(value):
    try:
        return date.fromisoformat(normalize_day(value))
    except (ValueError, TypeError):
        return None


def _recency_factor(record, today):
    """0..1, halving every RECENCY_HALF_LIFE_DAYS. Unparseable or
    missing updated_at scores 0.25 (old, but not zero — a
    dateless fact the user asked to keep still outranks noise)."""
    stamp = _parse_day(record.get("updated_at", ""))
    if stamp is None:
        stamp = _parse_day(record.get("created_at", ""))
    if stamp is None:
        return 0.25
    age = max(0, (today - stamp).days)
    return 0.5 ** (age / RECENCY_HALF_LIFE_DAYS)


def _importance(record):
    raw = record.get("importance", None)
    if raw is not None:
        try:
            return min(1.0, max(0.0, float(raw)))
        except (TypeError, ValueError):
            pass
    return _KIND_DEFAULT_IMPORTANCE.get(
        str(record.get("kind", "")).lower(), 0.5)


def _relevance_factor(record, query_tokens):
    if not query_tokens:
        return 1.0
    hay = _tokens(" ".join(str(record.get(field, "")) for field in
                  ("category", "key", "value")))
    overlap = len(query_tokens & hay)
    # 0.5 floor: a merely-unrelated fact is down-ranked, never
    # annihilated — with no overlap at all the product still
    # orders by recency x importance.
    return 0.5 + 0.5 * (overlap / len(query_tokens))


def _is_pinned(record):
    cat = str(record.get("category", "")).strip().lower()
    key = str(record.get("key", "")).strip().lower()
    return cat in PINNED_CATEGORIES or key in PINNED_KEYS


def _render_line(record):
    flat = " ".join(str(record.get("value", "")).split())
    return f"{record.get('category', '')}: {record.get('key', '')} — {flat}"


def score_record(record, query=None, today=None):
    """Pure score: recency x importance x relevance, each 0..1
    (relevance 1.0 with no query). Returns None for records that
    must never enter a block (opinions; expired inferred)."""
    if str(record.get("kind", "")).lower() == "opinion":
        return None
    today = today or date.today()
    if is_expired_inferred(record, today=today.isoformat()):
        return None
    query_tokens = _tokens(query) if query else set()
    return (_recency_factor(record, today)
            * _importance(record)
            * _relevance_factor(record, query_tokens))


def rank_records(records, query=None, today=None,
                 budget=MEMORY_BLOCK_BUDGET_CHARS):
    """Pure ranker. Returns a dict: ``selected`` (records in fill
    order), ``scores`` ({(category, key): score}), ``dropped``
    (count that did not fit), ``chars`` (chars the selected lines
    occupy). Pinned records always lead (score order within each
    group); whole lines only — a line that would cross the budget
    is dropped, never truncated, so the block never exceeds it."""
    today = today or date.today()
    scored = []
    for record in records or []:
        score = score_record(record, query=query, today=today)
        if score is None:
            continue
        scored.append((record, score))
    pinned = sorted((item for item in scored if _is_pinned(item[0])),
                    key=lambda item: item[1], reverse=True)
    rest = sorted((item for item in scored if not _is_pinned(item[0])),
                  key=lambda item: item[1], reverse=True)
    ordered = pinned + rest
    selected, scores, chars = [], {}, 0
    for record, score in ordered:
        line = _render_line(record)
        cost = len(line) + 1  # line + newline
        if selected and chars + cost > budget:
            continue
        if not selected and cost > budget:
            continue  # a single over-budget line never fills alone
        selected.append(record)
        scores[(str(record.get("category", "")),
                str(record.get("key", "")))] = score
        chars += cost
    return {"selected": selected, "scores": scores,
            "dropped": len(ordered) - len(selected), "chars": chars}


def build_memory_block(records, query=None, today=None,
                       budget=MEMORY_BLOCK_BUDGET_CHARS):
    """Render the ranked block: selected lines, plus a disclosure
    line when facts were held back by the cap (mirrors memory.py's
    drop disclosure). Never exceeds ``budget`` + the disclosure
    line itself, and the disclosure is outside the ranked fill."""
    ranked = rank_records(records, query=query, today=today,
                          budget=budget)
    lines = [_render_line(record) for record in ranked["selected"]]
    if ranked["dropped"]:
        lines.append(f"… and {ranked['dropped']} more fact(s) held "
                     f"back by the {budget}-char cap.")
    return "\n".join(lines), ranked


# ---------------------------------------------------------------------------
# BN-8-3: heat-based promotion gating
# ---------------------------------------------------------------------------

def bump_heat(record):
    """Pure: return a copy of the record with heat + 1. Heat is
    caller-maintained (a recall hit, a re-mention); this module
    never invents heat from a clock."""
    copy = dict(record)
    try:
        copy["heat"] = int(record.get("heat", 0)) + 1
    except (TypeError, ValueError):
        copy["heat"] = 1
    return copy


def heat_of(record):
    try:
        return max(0, int(record.get("heat", 0)))
    except (TypeError, ValueError):
        return 0


def promotion_eligible(record, threshold=PROMOTION_HEAT_THRESHOLD,
                       today=None):
    """A candidate earns a long-term write only when: heat meets
    the threshold, it has a non-empty value, it is not an opinion,
    and (for inferred traits) it has not expired. Everything else
    stays out of the file store no matter how often it is seen
    below threshold."""
    if str(record.get("kind", "")).lower() == "opinion":
        return False
    if not str(record.get("value", "")).strip():
        return False
    if heat_of(record) < threshold:
        return False
    today = today or date.today()
    if is_expired_inferred(record, today=today.isoformat()):
        return False
    return True


def consolidate_candidates(candidates, today=None):
    """Pure dedupe for a sleeptime batch: per (category, key) keep
    the newest updated_at (ties: highest heat, then first seen).
    Returns (kept, merged_count). No I/O, no writes."""
    today = today or date.today()
    best = {}
    merged = 0
    for record in candidates or []:
        identity = (str(record.get("category", "")).strip().lower(),
                    str(record.get("key", "")).strip())
        stamp = normalize_ts(record.get("updated_at", ""))
        previous = best.get(identity)
        if previous is None:
            best[identity] = record
        else:
            merged += 1
            prev_stamp = normalize_ts(previous.get("updated_at", ""))
            if (stamp, heat_of(record)) > (prev_stamp,
                                           heat_of(previous)):
                best[identity] = record
    return list(best.values()), merged


# ---------------------------------------------------------------------------
# BN-8-1: idle-time worker (STAGED seam)
# ---------------------------------------------------------------------------

def idle_pass(base=None, candidates=(), writer=None, today=None):
    """One sleeptime pass. Reads the file store ONCE (list_facts),
    consolidates the caller's candidates, and writes ONLY
    heat-eligible records that are not already stored byte-equal.
    With no candidates and no eligible records it performs NO
    writes and NO expensive scans: no DB open, no second walk,
    no model call. Returns a report dict; ``writes`` is the count
    the coordinator can assert is 0 on quiet days.

    ``writer`` defaults to memory_files.write_fact (the
    Wave 6 writable seam); tests inject a counting fake. ``base``
    routes the store to a tmp dir in tests — production passes
    None (DATA_DIR/memories via VYUK_MEMORIES_DIR override).
    The frozen DB is never opened here: this module has no
    sqlite call path at all."""
    today = today or date.today()
    stored, _skipped = list_facts(base=base)
    stored_values = {
        (str(r.get("category", "")).strip().lower(),
         str(r.get("key", "")).strip()): str(r.get("value", ""))
        for r in stored
    }
    expired = sum(1 for r in stored
                  if is_expired_inferred(r, today=today.isoformat()))
    kept, merged = consolidate_candidates(candidates, today=today)
    eligible = []
    for record in kept:
        identity = (str(record.get("category", "")).strip().lower(),
                    str(record.get("key", "")).strip())
        if not promotion_eligible(record, today=today):
            continue
        if stored_values.get(identity) == str(record.get("value", "")):
            continue  # already stored byte-equal: a write is waste
        eligible.append(record)
    writes = 0
    written = []
    if eligible:
        write = writer or write_fact
        for record in eligible:
            write(record, base=base)
            writes += 1
            written.append(f"{record.get('category')}:"
                           f"{record.get('key')}")
    return {"scanned": len(stored), "expired_inferred_seen": expired,
            "candidates": len(candidates or ()),
            "merged_duplicates": merged,
            "eligible": len(eligible), "writes": writes,
            "written": written,
            "schema_version": SLEEPTIME_SCHEMA_VERSION}

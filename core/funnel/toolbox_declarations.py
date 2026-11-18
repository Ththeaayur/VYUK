"""Split out of core/funnel/toolbox.py (B37 part 2 of 2).

Find-declarations + activity-resident families, combined in one module:
router_declarations calls resident_declarations and resident_declarations
calls router_declarations, so separate modules would need a cycle or a
body deviation; combined, every bare name resolves locally.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

# B37 INTENTIONAL: cross-family names imported from the tokens module
# (same objects); find-decl/activity names resolve locally in this module.
from .toolbox_tokens import (CORE_TOOL_PRIORITY, NEGATIVE_PAIRS, _expand,
                             _negative_forbidden, _stemmed, _tokens,
                             core_tool_names)




# ---------------------------------------------------------------------------
# v5.50 (programme item 45): find-with-schema confidence.
#
# Pre-45, reaching a non-resident tool cost THREE rounds: find,
# describe, call. When the ranker is SURE of its top match, find
# now inlines that tool's full declaration, so the model calls
# it in round 2. "Sure" is judged on ranking_scores() — the
# exact values rank_tools() sorted by (the keyword score alone
# when no embedding model is provisioned, the keyword+cosine
# blend when one is):
#
#   CONFIDENT iff the query, stripped and lowercased, IS the top
#   scorer's exact tool name (the ranker awards an exact-name
#   query a +100 bonus in keyword_scores, so the named tool is
#   the top scorer or this clause does not fire); OR all of:
#     * the top score reaches FIND_SCHEMA_MIN_SCORE — 14, one
#       name-token hit (weight 12) plus at least one more token
#       of evidence at the description weight (2): a bare name
#       echo with nothing behind it is not evidence of intent;
#     * the top score beats the runner-up by at least
#       FIND_SCHEMA_MIN_MARGIN — 12, one full name-token weight:
#       the winner must own a whole ranking signal the runner-up
#       lacks, so near-ties stay ambiguous ("delete a file"
#       scores 46 vs 44, "email" 34 vs 32, a bare "image" ties
#       four tools at 14 — none of them inlines a schema); AND
#     * the top score is at least FIND_SCHEMA_MIN_RATIO (1.5x)
#       the runner-up's. A lone match has no runner-up and needs
#       only the floor; a dead heat at the top is ambiguous by
#       construction (margin 0).
#   Anything else is AMBIGUOUS: find returns the plain ranked
#   list, exactly as before this item — no schema is inlined,
#   so a guessed schema can never send the model calling the
#   wrong tool confidently.
#
# The inlined declaration is result TEXT only: no declaration
# joins any session's fitted or resident set to pay for this,
# so the 240,000-byte Gemini budget and the resident set are
# untouched (proven in test_v550_p45).
# ---------------------------------------------------------------------------

#: Confidence floor for find-with-schema: one name-token hit
#: (12) plus one description-token hit (2). See the header above.
FIND_SCHEMA_MIN_SCORE = 14.0
#: Minimum lead over the runner-up: one full name-token weight.
FIND_SCHEMA_MIN_MARGIN = 12.0
#: Minimum top/runner-up ratio.
FIND_SCHEMA_MIN_RATIO = 1.5

# ---------------------------------------------------------------------------
# Audit fix Batch 2 (ROUTE-01): the negative-pair table + the
# inlining gate + the unsupported-task abstention.
#
# The keyword scorer has no intent class, so a shared stem is
# enough to make a wrong tool confident (audit probes: "convert
# a PDF to Word" -> unit_convert, "translate this text" ->
# type_text, "record my screen" -> take_screenshot). The table
# below is DATA, not a scorer rewrite: each entry names the
# query signal (word-prefix groups, ALL groups must hit, plus
# optional raw phrases and exclusions) and the tools that
# signal must never select. keyword_scores() subtracts
# NEGATIVE_PAIR_PENALTY (larger than any keyword score plus
# any embedding blend of 100, so a forbidden tool lands at
# the bottom in keyword-only AND blended mode) for a fired
# entry. Entries also carry the unsupported-task message used
# by _find(): when a query matches one and no tool may be
# inlined, _find returns that abstention text instead of a
# ranked list that invites the model to call the least-bad
# tool. A signal that names a tool the catalogue DOES have
# (e.g. "translate to morse") is excluded, never abstracted.
# ---------------------------------------------------------------------------






def _unsupported_task_message(query: str) -> str | None:
    """The abstention message when this query asks for a task
    no catalogue tool does, or None when it does not. Only
    entries carrying an "unsupported" message qualify; the
    webpage-reading entry is a routing correction, not an
    unsupported task, so it returns None and _find still
    lists the ranked tools (browser_read above open_path).
    """
    raw = (query or "").lower()
    words = _tokens(query)
    for entry in NEGATIVE_PAIRS:
        message = entry.get("unsupported")
        if not message:
            continue
        if any(p in raw for p in entry.get("exclude_phrases", ())):
            continue
        groups = entry.get("groups", ())
        groups_hit = all(
            any(w.startswith(prefix) for w in words for prefix in group)
            for group in groups)
        phrase_hit = any(p in raw for p in entry.get("phrases", ()))
        if groups_hit or phrase_hit:
            return message
    return None


#: Inlining floor, higher than the ranking-confidence floor
#: (FIND_SCHEMA_MIN_SCORE = 14): a bare name-token echo plus
#: one description token must never hand the model a full
#: declaration. 30 keeps the proven confident rows (browser
#: 88, screenshot 68, battery 42) while the audit's
#: false-confidence rows fail on margin/ratio/name-token
#: grounds even before this floor is reached.
INLINE_MIN_SCORE = 30.0


def find_may_inline(query: str, scores: dict[str, float],
                    top_name: str) -> bool:
    """The inlining gate (Batch 2, ROUTE-01a): may _find inline
    top_name's full declaration? Stricter than
    find_is_confident, which judges ranking confidence only:

    * the query naming the tool exactly still inlines (the
      ranker's +100 exact-name bonus makes it the top scorer
      or this clause does not fire — same rule as before);
    * otherwise ALL of: find_is_confident holds, the top
      score clears INLINE_MIN_SCORE, the query shares a
      stemmed token with the tool's NAME (not just keyword/
      synonym/description overlap — "convert" alone must not
      inline unit_convert for a document query), and the top
      tool is not negative-pair-forbidden for this query.

    When this returns False, _find lists tools (or abstains
    for a known unsupported task) — it never hands the model
    a callable wrong tool with the system's confidence on it.
    """
    if not scores or top_name not in scores:
        return False
    ordered = sorted(scores.values(), reverse=True)
    top = scores[top_name]
    if top != ordered[0]:
        return False
    q = (query or "").strip().lower()
    if q and q == top_name and scores.get(q) == top:
        return True
    if not find_is_confident(query, scores):
        return False
    if top < INLINE_MIN_SCORE:
        return False
    if not (_stemmed(query) & _stemmed(top_name.replace("_", " "))):
        return False
    forbidden, _reasons = _negative_forbidden(query)
    if top_name in forbidden:
        return False
    return True


def find_is_confident(query: str, scores: dict[str, float]) -> bool:
    """The item-45 confidence rule (see the section header for
    the full derivation): True when find may inline the top
    match's full declaration. `scores` is ranking_scores()
    output. Only the top two VALUES are judged, so which tool
    wins a tie never matters — a tie is ambiguous."""
    if not scores:
        return False
    ordered = sorted(scores.values(), reverse=True)
    top = ordered[0]
    q = (query or "").strip().lower()
    if q and scores.get(q) == top:
        return True  # the query names the top-scoring tool exactly
    if top < FIND_SCHEMA_MIN_SCORE:
        return False
    if len(ordered) == 1:
        return True  # a lone match above the floor: no rival
    runner_up = ordered[1]
    if runner_up <= 0:
        return True
    return (top - runner_up >= FIND_SCHEMA_MIN_MARGIN
            and top >= FIND_SCHEMA_MIN_RATIO * runner_up)


#: Description cap preserved for the assistant resident set
#: (INV-1 re-scope): the fitted full list's stopping point
#: today is cap 150, so the assistant set built directly
#: with cap 150 is byte-identical to the fitted-filtered
#: set it replaces. HELD: the untrimmed flip (cap None)
#: waits for the owner's live A/B — set this to None only
#: then, never as part of the gate re-scope itself.
ASSISTANT_DESC_CAP = 150


def _cap_description_tree(node: Any, cap: int) -> None:
    """Local copy of the registry fitter's description cap
    (ToolRegistry._cap_descriptions): every description
    string in the tree longer than `cap` becomes
    desc[:cap].rstrip() + ".". Used only for registries
    without that seam (test doubles); the real registry's
    own cap function is used when present."""
    if isinstance(node, dict):
        desc = node.get("description")
        if isinstance(desc, str) and len(desc) > cap:
            node["description"] = desc[:cap].rstrip() + "."
        for value in node.values():
            _cap_description_tree(value, cap)
    elif isinstance(node, list):
        for item in node:
            _cap_description_tree(item, cap)


def gemini_decl_for_names(registry: Any, names: Any,
                          description_cap: int | None = None,
                          ) -> list[dict[str, Any]]:
    """ONE shared per-tool Gemini declaration builder
    (INV-1 re-scope): for each name, the registry entry's
    canonical description plus the Gemini-safe schema
    conversion — the registry's Gemini-safe rewrite, minus
    the budget fitter, so the result never depends on any
    fitted full list. description_cap=None builds FULL canonical
    descriptions (the desktop build); an int cap applies
    the fitter's description cap to the whole declaration
    tree (the assistant build keeps cap 150, byte-identical
    to the fitted-filtered set it replaces). Unknown names
    are skipped, as the old filter did. Registries without
    the conversion seam (test doubles) fall back to a deep
    copy of the canonical declaration, parameters untouched
    — the same fallback the old desktop builder had."""
    tools_map = getattr(registry, "_tools", None)
    safe_schema = getattr(registry, "_gemini_safe_schema", None)
    cap_fn = getattr(registry, "_cap_descriptions", None)
    canonical: dict[str, Any] = {}
    if tools_map is None or not callable(safe_schema):
        canonical = {d["name"]: d
                     for d in registry.get_declarations()}
    out: list[dict[str, Any]] = []
    for name in names:
        if tools_map is not None and callable(safe_schema):
            entry = tools_map.get(name)
            if entry is None:
                continue
            decl: dict[str, Any] = {
                "name": name,
                "description": entry["description"],
            }
            params = safe_schema(entry["parameters"])
            if isinstance(params, dict) and params.get("properties"):
                decl["parameters"] = params
        else:
            decl = canonical.get(name)
            if decl is None:
                continue
            decl = copy.deepcopy(decl)
        if description_cap is not None:
            if callable(cap_fn):
                cap_fn(decl, description_cap)
            else:
                _cap_description_tree(decl, description_cap)
        out.append(decl)
    return out


def router_declarations(registry: Any,
                        usage_counts: dict[str, int] | None = None,
                        ) -> list[dict[str, Any]]:
    """The Gemini declaration list for a routed session: the
    core tools in CORE_TOOL_PRIORITY order, then use_toolbox.
    Built per tool from the registry's own entries through
    gemini_decl_for_names (INV-1 re-scope) — never from the
    fitted full list — with the assistant description cap
    (ASSISTANT_DESC_CAP), so the schemas are the same ones
    the full-list path would send and the result is
    byte-identical to the old fitted-filtered set.

    With `usage_counts` (v5.45, HANDS H6 — see the adaptive
    resident-set section below) the resident names are the
    usage-adapted set from compute_resident_tool_names()
    instead; with None (every pre-H6 caller) the result is
    byte-for-byte the historical list."""
    if usage_counts:
        return resident_declarations(registry, usage_counts)
    return gemini_decl_for_names(
        registry, [*core_tool_names(), "use_toolbox"],
        description_cap=ASSISTANT_DESC_CAP)


# ---------------------------------------------------------------------------
# H6 (v5.45): adaptive resident set.
#
# The resident core set above is a fixed editorial pick. H6 makes
# it the user's own: data/activity.log already records every tool
# execution (tools.ToolRegistry._log_activity writes
# "<iso-timestamp> | <tool name> | <status>" per call), so at
# SESSION START (the Live connect path in core/voice/live_client.py —
# the only caller that reads the log) the most-used NON-core tools
# are promoted into the resident set, each displacing a least-used
# NON-essential core entry. Everything not resident stays one
# use_toolbox call away, exactly as before.
#
# Rules, all deterministic (same log -> same resident set):
#   * counts come from a BOUNDED tail read of the log (the last
#     ACTIVITY_LOG_TAIL_BYTES), parsed tolerantly — malformed,
#     bracketed ([Live]/[Groq]/…) and pseudo-source lines never
#     count, so format drift degrades to fewer counts, never to
#     a crash or a wrong promotion;
#   * a promotion must be used STRICTLY MORE than the entry it
#     displaces (frequency wins — a one-off outsider never evicts
#     a core tool the user actually relies on);
#   * PROTECTED_CORE_TOOLS are never displaced (below);
#   * among displaceable entries the least-used goes first, ties
#     broken toward the tail of CORE_TOOL_PRIORITY (the lowest-
#     priority entry) — among candidates, highest count first,
#     ties broken by registry order;
#   * the fitted 240,000-byte budget is the final guard: the
#     candidate declarations are MEASURED, and promotions that
#     do not fit are dropped (lowest-ranked first) rather than
#     trimmed into the set;
#   * a fresh user (no log / no counts) gets exactly today's set:
#     compute_resident_tool_names({}) == CORE_TOOL_PRIORITY and
#     resident_declarations(registry, {}) is byte-identical to
#     router_declarations(registry).
# ---------------------------------------------------------------------------

#: How much of the activity log's tail the usage counter reads.
#: Bounded on purpose: the log grows one line per tool call for
#: the life of the install, and session start must never pay for
#: all of it. 256 KB of tail is thousands of executions — far
#: more signal than the promotion ranking can use.
ACTIVITY_LOG_TAIL_BYTES = 262_144

#: Core tools that are NEVER displaced by an H6 promotion: the
#: confirm / run / input essentials. Losing any of these from
#: the resident set would put a safety-critical or basic-input
#: action behind a router round-trip — exactly the actions that
#: must stay one direct call away:
#:   run/open — launch_application, close_application (also
#:              confirm-gated), open_path;
#:   input    — type_text, press_hotkey, mouse_control,
#:              window_control, get_active_window;
#:   browser  — browser_control, browser_click, browser_type
#:              (the browser's input surface);
#:   clipboard— read_clipboard, write_clipboard;
#:   safety   — power_control (confirm-gated), lock_screen,
#:              file_control (confirm-gated; includes delete).
#: use_toolbox itself is listed for completeness: it is not a
#: CORE_TOOL_PRIORITY member (it is appended separately) and is
#: resident in every routed set by construction.
PROTECTED_CORE_TOOLS: frozenset = frozenset({
    "launch_application", "close_application", "open_path",
    "type_text", "press_hotkey", "mouse_control",
    "window_control", "get_active_window",
    "browser_control", "browser_click", "browser_type",
    "read_clipboard", "write_clipboard",
    "power_control", "lock_screen", "file_control",
    "use_toolbox",
})

#: Log sources that share the activity log's "a | b | c" line
#: shape but are not tool executions (confirm_gate logs gate
#: DECISIONS under its own name; the gated tool is named inside
#: its message field, not the name field).
_NON_TOOL_LOG_SOURCES = frozenset({"confirm_gate"})

_TOOL_NAME_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")


def activity_log_path() -> Path:
    """data/activity.log beside the project root — the same
    path tools.ToolRegistry._log_activity writes to."""
    return Path(__file__).resolve().parent.parent.parent / "data" / "activity.log"  # Phase 3 F2a: file now in core/funnel/ — one more level up to the package root


def parse_activity_counts(text: str) -> dict[str, int]:
    """Tool-execution counts from activity-log TEXT (pure).

    The canonical line is "<timestamp> | <name> | <status>";
    parsing is deliberately tolerant of drift: a line counts
    when its second "|"-field (or, for a pipe-less line, its
    second whitespace token) is a tool-shaped name. Bracketed
    lines ([Live] …, [Groq] …), blank lines, pseudo-sources
    (_NON_TOOL_LOG_SOURCES) and anything shapeless are skipped
    — never an error. use_toolbox executions are executions
    like any other and are counted (the promotion filter, not
    the parser, decides they are never promoted)."""
    counts: dict[str, int] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("["):
            continue
        name = ""
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                name = parts[1]
        else:
            # Pipe-less drift: only a line that still OPENS
            # with a date-shaped timestamp earns the fallback —
            # free prose ("random shapeless line") must not
            # mint counts out of its second word.
            tokens = line.split()
            if len(tokens) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}",
                                             tokens[0]):
                name = tokens[1]
        if not _TOOL_NAME_SHAPE.match(name):
            continue
        if name in _NON_TOOL_LOG_SOURCES:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def activity_usage_counts(log_path: Any = None,
                          max_bytes: int = ACTIVITY_LOG_TAIL_BYTES,
                          ) -> dict[str, int]:
    """Usage counts from the activity log's TAIL (bounded read).

    Reads at most the last `max_bytes` bytes; when the file is
    larger, the partial first line of the window is discarded
    (it may be cut mid-line) before parsing. A missing or
    unreadable log is {} — a fresh user — never an error."""
    try:
        path = Path(log_path) if log_path is not None \
            else activity_log_path()
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the possibly-partial first line
            data = fh.read()
        return parse_activity_counts(data.decode("utf-8", "ignore"))
    except Exception:  # noqa: BLE001 — usage is an enhancement
        return {}


def compute_resident_tool_names(
        usage_counts: dict[str, int] | None = None,
        *, known_names: Any = None) -> tuple[str, ...]:
    """The H6 resident set as a pure function of usage counts.

    `known_names` (optional) is the registry's name order: it
    filters candidates to tools that actually exist and breaks
    candidate ties by registry order. Without it, ties break by
    name — still fully deterministic. Survivors keep
    CORE_TOOL_PRIORITY order; promotions append in rank order
    (highest count first). Empty/None counts return exactly
    CORE_TOOL_PRIORITY. See the section header for the rules."""
    counts: dict[str, int] = {}
    for key, value in (usage_counts or {}).items():
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n > 0:
            counts[str(key)] = n
    core = list(CORE_TOOL_PRIORITY)
    # v5.49 (programme item 34): gimmick tools are never promoted
    # into the resident set (and, defensively, never survive in it).
    # core/funnel/tool_profiles.py imports THIS module at top level, so the
    # GIMMICK_TOOLS import is lazy (call time) to avoid a cycle.
    # Today's intersection with CORE_TOOL_PRIORITY is empty, so the
    # core filter is a no-op until a future gimmick lands there.
    try:
        from .tool_profiles import GIMMICK_TOOLS as _gimmicks
    except Exception:  # noqa: BLE001 — curation must never break the router
        _gimmicks = frozenset()
    if _gimmicks:
        core = [n for n in core if n not in _gimmicks]
        counts = {k: v for k, v in counts.items() if k not in _gimmicks}
    if not counts:
        return tuple(core)
    core_set = set(core)
    core_index = {name: i for i, name in enumerate(core)}

    # Candidates: used, non-core, not the dispatcher itself.
    candidates = [n for n in counts
                  if n not in core_set and n != "use_toolbox"]
    if known_names is not None:
        known_order = {str(n): i for i, n in enumerate(known_names)}
        candidates = [n for n in candidates if n in known_order]
        candidates.sort(key=lambda n: (-counts[n], known_order[n]))
    else:
        candidates.sort(key=lambda n: (-counts[n], n))
    if not candidates:
        return tuple(core)

    # Displaceable victims, least-used first; ties go to the
    # tail of the priority list (lowest priority first out).
    victims = sorted(
        (n for n in core if n not in PROTECTED_CORE_TOOLS),
        key=lambda n: (counts.get(n, 0), -core_index[n]))
    remaining = list(victims)
    displaced: set[str] = set()
    promotions: list[str] = []
    for candidate in candidates:
        # Frequency must WIN: only an entry used strictly less
        # than the candidate may be displaced by it.
        eligible = [v for v in remaining
                    if counts.get(v, 0) < counts[candidate]]
        if not eligible:
            continue
        victim = eligible[0]  # `remaining` is least-used-first
        remaining.remove(victim)
        displaced.add(victim)
        promotions.append(candidate)
    if not promotions:
        return tuple(core)
    survivors = [n for n in core if n not in displaced]
    return tuple(survivors + promotions)


def resident_tool_names(usage_counts: dict[str, int] | None = None,
                        *, log_path: Any = None,
                        known_names: Any = None) -> tuple[str, ...]:
    """The computed resident set, callable without a live
    session (the suite's seam): pass `usage_counts` directly,
    or a `log_path` to read counts from that log's tail, or
    neither for the log at activity_log_path()."""
    if usage_counts is None:
        usage_counts = activity_usage_counts(log_path)
    return compute_resident_tool_names(usage_counts,
                                       known_names=known_names)


def resident_declarations(registry: Any,
                          usage_counts: dict[str, int] | None = None,
                          ) -> list[dict[str, Any]]:
    """Gemini declarations for the H6 resident set: the
    computed resident names in set order, then use_toolbox —
    router_declarations() with usage adaptation, plus the
    budget guard: the result is measured against the registry's
    fitted budget (240,000 B) and promotions that do not fit
    are dropped, lowest-ranked first, rather than trimmed in.
    Built through gemini_decl_for_names (INV-1 re-scope),
    never from the fitted full list.
    With no effective promotions the result is built by
    router_declarations() itself, byte-for-byte."""
    try:
        known = [d["name"] for d in registry.get_declarations()]
    except Exception:  # noqa: BLE001 — fall back to name order
        known = None
    names = compute_resident_tool_names(usage_counts,
                                        known_names=known)
    core_set = set(CORE_TOOL_PRIORITY)
    promotions = [n for n in names if n not in core_set]
    if not promotions:
        return router_declarations(registry)
    built = {d["name"]: d for d in gemini_decl_for_names(
        registry, [*names, "use_toolbox"],
        description_cap=ASSISTANT_DESC_CAP)}

    def _build(resident: list[str]) -> list[dict[str, Any]]:
        out = [built[n] for n in resident if n in built]
        if "use_toolbox" in built:
            out.append(built["use_toolbox"])
        return out

    budget = int(getattr(registry, "_GEMINI_DECL_BUDGET", 240_000))
    size_of = getattr(registry, "gemini_payload_size", None)
    if not callable(size_of):
        size_of = lambda decls: len(json.dumps(decls))  # noqa: E731
    resident = list(names)
    decls = _build(resident)
    while promotions and size_of(decls) > budget:
        dropped = promotions.pop()  # lowest-ranked promotion
        resident.remove(dropped)
        decls = _build(resident)
    if size_of(decls) > budget:
        # Even the unpromoted shape busts the budget on this
        # registry — today's set is the honest floor.
        return router_declarations(registry)
    return decls


def _declaration(registry: Any, name: str) -> dict[str, Any] | None:
    """One tool's full declaration from the registry, or None
    when the name is unknown."""
    for decl in registry.get_declarations():
        if decl["name"] == name:
            return decl
    return None


def _mcp_declaration(entry: dict[str, Any]) -> dict[str, Any]:
    """One MCP table entry -> its find/describe declaration.
    The description carries the server provenance up front:
    the model must know this tool runs in an external server,
    not in VYUK, before it decides to call it. Find/describe
    TEXT only — never fitted (INV-1: the fitted registry
    stays 480, the fitted payload stays 229,785 B).

    Audit A3 (P0-6): the description is server-controlled
    prose, so it passes through the single MCP output seam
    (core.funnel.tools_gate.wrap_mcp_content) — envelope-wrapped +
    taint-screened — before any rendering. An empty
    description carries no server prose and stays a bare
    provenance prefix. The parameters dict stays raw here:
    schema prose is screened where the declaration is
    RENDERED (_find inline, _describe), which wraps the full
    declaration JSON through the same seam.
    """
    from . import tools_gate as _tg  # Phase 3 F1: tools_gate moved to core/funnel/; Phase 3 F2a: toolbox now in core/funnel/ (intra-package sibling)

    raw = entry.get("description", "") or ""
    prefix = f"[External toolbox server '{entry['server']}'] "
    desc = (_tg.wrap_mcp_content(entry["server"], prefix + raw)
            if raw.strip() else prefix)
    return {
        "name": entry["composed"],
        "description": desc,
        "parameters": entry.get("parameters") or {},
    }


def _mcp_scores(query: str, bus: Any) -> dict[str, float]:
    """Keyword scores over the MCP table, on the SAME weights
    as keyword_scores (name tokens 12, description tokens 2,
    exact-name query +100) — one ranking, two catalogues, so
    an MCP tool competes with a native tool on equal terms.
    Name tokens come from the server's raw tool name (the
    "mcp__server__" namespace prefix is addressing, not
    vocabulary, and is never scored). No synonyms: MCP tools
    have no TOOL_SYNONYMS entries, and inventing any would
    be vocabulary VYUK does not own."""
    q_raw = _tokens(query)
    if not q_raw:
        return {}
    q = _expand(_stemmed(query))
    scores: dict[str, float] = {}
    for entry in bus.list_tools():
        composed = entry["composed"]
        score = 0
        if (query or "").strip().lower() == composed:
            score += 100
        score += 12 * len(q & _stemmed(entry["tool"].replace("_", " ")))
        score += 2 * len(q & _stemmed(entry.get("description", "")))
        if score:
            scores[composed] = float(score)
    return scores

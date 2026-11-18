"""Command-palette logic, Qt-free (Sweep Build 4, Flow pattern only).

Nova already shipped a Qt command palette (gui/window_widgets.py,
``CommandPalette``: Ctrl+K, titlebar pill, filter-as-you-type). This
module is the part that was missing: the matching, ranking and
action-registry logic with no Qt import, so it can be tested without
a GUI, and the consequence classification the widget must honour.

PATTERN ONLY, NOT FLOW CODE: this module is Nova's own code. No
Flow Launcher code is copied here, no Flow binary is bundled, and
Nova's palette is not presented as Flow Launcher. The Flow MIT
sighting is recorded in gui/assets/FLOW_LICENCE_READ.md; because no
Flow code ships, no MIT notice is owed for this module. If Flow code
were ever copied, its MIT notice would be required — that is the
line this build does not cross.

Entries are plain dicts so the widget can stay the only Qt layer::

    {"label": str, "kind": str, "description": str,
     "action_id": str, "consequential": bool, "index": int}

``kind`` is PAGE, SETTING, TOOL or LOOP. TOOL entries never execute a tool:
in the widget they prefill the composer, so the brain's argument
filling and confirmation still run them. LOOP entries (Wiring
Build 6) are loop-action rows: they navigate to the existing GUI
surface where the action lives (Doctor page for resilience re-arm
and undo, Settings for the voice bridge), or — for the running
goal's Stop — invoke the existing
goal-pill Stop button. A LOOP row never invents a loop action: a
loop capability with no existing user-facing surface (pause/resume
as a standalone action, playbook lookup, J9 deletion) has no row,
and is listed in INTENTIONALLY_EXCLUDED, never as a row.
Consequential entries are
the ones whose widget action itself changes state in a way the user
must explicitly approve again:

- ``toggle_connection`` — connects/disconnects the live session
  (egress / session state change).
- ``toggle_wake`` — changes whether the microphone listens for a
  wake word (capture state change).

Page navigation, mute, theme and orb switches, and tool prefills are
not consequential in this sense. Anything not listed here defaults
to non-consequential only when its action_id is known to be safe;
unknown action_ids are treated as consequential by
``requires_confirmation`` — fail closed, never fail open.
"""

from __future__ import annotations

# R9-1 RapidFuzz PORT: the palette's typo lane reuses the tree's own
# plain Levenshtein (core/skill_gate_admit.py:131, stdlib, used only
# for typosquat screening) — no RapidFuzz adoption, no new dependency.
# skill_gate_admit's chain is stdlib-only and nothing in it imports
# this module, so there is no cycle.
from .skill_gate_admit import levenshtein

ROW_CAP = 12

# Action ids whose widget action changes connection or capture state.
CONSEQUENTIAL_ACTION_IDS = frozenset({"toggle_connection", "toggle_wake"})

# Action ids known to be safe to run from the palette without a second
# confirmation: page navigation, cosmetic switches, mute, and tool
# prefills (which never execute the tool).
SAFE_ACTION_PREFIXES = ("page_", "tool_")
SAFE_ACTION_IDS = frozenset({
    "toggle_mute",
    "switch_theme",
    "play_briefing",
    # Wiring Build 6 loop-navigation rows: navigating to a page is
    # not consequential — the action on that page (re-arm, undo)
    # still carries its own confirmation there.
    "loop_rearm_resilience",
    "loop_undo_last",
    "loop_voice_bridge",
})


def requires_confirmation(entry: dict) -> bool:
    """True when running this palette entry needs a second, explicit
    user confirmation in the widget (default-deny).

    An entry marked consequential needs confirmation. An entry with an
    unknown action_id also needs confirmation — fail closed. Only
    entries explicitly marked non-consequential with a known-safe
    action_id run without one.
    """
    if entry.get("consequential"):
        return True
    action_id = str(entry.get("action_id") or "")
    if action_id in SAFE_ACTION_IDS:
        return False
    if action_id.startswith(SAFE_ACTION_PREFIXES):
        return False
    return True


def is_subsequence(needle: str, hay: str) -> bool:
    """True when every character of needle appears in hay in order."""
    it = iter(hay)
    return all(ch in it for ch in needle)


def _words(text: str) -> list[str]:
    return [w for w in text.replace("-", " ").split() if w]


def score_match(needle: str, label: str, description: str = "") -> float | None:
    """Deterministic match score for one entry, or None for no match.

    Ranking, highest first: exact label, label prefix, word-boundary
    start, label substring, description substring, label subsequence
    (with a consecutive-run bonus and a shorter-label bonus), then
    description subsequence, then a label typo (Levenshtein distance
    at most 2 with similarity at least 0.6 over the label words).
    Scores are floats only to carry the
    bonuses; ordering is what matters, and ties keep original order
    via ``rank_entries``. Matching is case-insensitive and matches
    labels and descriptions, as the shipped widget did.
    """
    needle = (needle or "").strip().lower()
    label_low = (label or "").lower()
    desc_low = (description or "").lower()
    if not needle:
        return 0.0
    if needle == label_low:
        return 100.0
    if label_low.startswith(needle):
        return 90.0
    if any(w.startswith(needle) for w in _words(label_low)):
        return 80.0
    if needle in label_low:
        return 70.0
    if desc_low and needle in desc_low:
        return 60.0
    if is_subsequence(needle, label_low):
        # Consecutive-run bonus: a tight subsequence ("brf" in
        # "briefing") scores above a scattered one ("b...r...f").
        run = 0
        best = 0
        j = 0
        for ch in needle:
            found = False
            while j < len(label_low):
                if label_low[j] == ch:
                    run += 1
                    best = max(best, run)
                    j += 1
                    found = True
                    break
                run = 0
                j += 1
            if not found:
                break
        consecutive = best / max(1, len(needle))
        shorter = 1.0 / (1.0 + max(0, len(label_low) - len(needle)))
        return 40.0 + 10.0 * consecutive + 5.0 * shorter
    if desc_low and is_subsequence(needle, desc_low):
        return 20.0
    # Typo lane (R9-1 RapidFuzz PORT): a transposition or a wrong
    # letter is not a subsequence ("chorme" is not in "chrome" in
    # order), so it scored nothing at all. The tree already has a
    # plain Levenshtein (core/skill_gate_admit.py:131, stdlib) — reuse
    # it here as a similarity over the label words, instead of
    # adopting RapidFuzz's 1.7 MB C++ core for a job this size (tens
    # to hundreds of short rows; the pure-Python distance is fast
    # enough there, measured in tests/test_rapidfuzz.py).
    # A typo match scores below every clean lane: it only fires when
    # no earlier lane matched for this entry, and it ranks below a
    # clean label subsequence (40+) but above a description
    # subsequence (20) — a label with a typo is still a label hit.
    # Threshold: distance at most 2 (one substitution, insertion or
    # deletion is 1; one transposition is 2 in plain Levenshtein)
    # and similarity at least 0.6, so short needles cannot
    # typo-match ("ab" against "ax" is 0.5, no match).
    best_ratio = 0.0
    for candidate in [label_low] + _words(label_low):
        if not candidate:
            continue
        dist = levenshtein(needle, candidate)
        longest = max(len(needle), len(candidate))
        if longest == 0:
            continue
        ratio = 1.0 - dist / longest
        if dist <= 2 and ratio >= 0.6 and ratio > best_ratio:
            best_ratio = ratio
    if best_ratio:
        return 25.0 + 10.0 * best_ratio
    return None


def rank_entries(entries: list[dict], query: str) -> list[dict]:
    """Entries matching query, best first, stable on ties.

    Each returned entry is a shallow copy with its original ``index``
    preserved (entries without one get their position). Non-matching
    entries are dropped, never silently kept.
    """
    scored = []
    for pos, entry in enumerate(entries):
        score = score_match(
            query, str(entry.get("label") or ""),
            str(entry.get("description") or ""))
        if score is None:
            continue
        copy = dict(entry)
        copy.setdefault("index", pos)
        scored.append((-score, pos, copy))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [copy for _neg, _pos, copy in scored]


def filter_entries(entries: list[dict], query: str,
                   cap: int = ROW_CAP) -> tuple[list[dict], int]:
    """(shown, hidden_count) for a query, capped at ``cap`` rows.

    The hidden count is returned so the widget can show the honest
    "and N more — keep typing to narrow" line instead of silently
    hiding results, as the shipped widget already did.
    """
    ranked = rank_entries(entries, query)
    if cap <= 0:
        return [], len(ranked)
    return ranked[:cap], max(0, len(ranked) - cap)


def humanise_tool_name(name: str) -> str:
    """'get_stock_quote' -> 'Get Stock Quote' (own helper; the widget
    keeps its own friendly-name map and overrides this where it has
    one, so palette wording and Skills wording stay matched there)."""
    return " ".join(w.capitalize() for w in (name or "").split("_") if w)


# Wiring Build 6 — features deliberately given NO palette row, because
# they have no registered tool and no existing user-facing surface a
# row could honestly point at. A row for any of these would invent
# an action. (name, reason) — checked by tests, never rendered.
INTENTIONALLY_EXCLUDED = (
    ("voiceprint enrolment",
     "never built: session lock only, biometric risk outweighs benefit"),
    ("continuous capture / anticipation capture",
     "no grant given: suggestion cards only, capture OFF unless explicit opt-in"),
    ("J9 deletion",
     "never built as a deletion step: confirm-gated + undoable only, per decision"),
    ("M3 'there' resolution",
     "GATED: view-centre + explicit name confirmation only, never built"),
    ("payment / purchase / booking",
     "ruling 34: no payment surface until a real pricing/EULA/support pack exists"),
    ("literal vehicle driving",
     "never built: safety wall, not a palette action"),
    ("medical diagnosis / interpretation",
     "never built: safety wall, not a palette action"),
    ("signed update feed",
     "code only, no host: no_feed; nothing to open from the palette"),
    ("marketplace listing",
     "parked pending legal review; parked is a decision, not a stall"),
    ("goal pause / resume as a standalone action",
     "no GUI surface: interruption is client-supplied; Stop is the user action"),
    ("playbook lookup as a standalone action",
     "automatic inside run_goal when a caller supplies a playbook; no surface"),
)


def loop_action_entries() -> list[dict]:
    """LOOP palette entries for loop actions with an existing surface.

    Plain dicts, no callables — the widget maps action_id to the
    real navigation / button it already has (see gui/window_widgets
    CommandPalette._build_commands). ``target_page`` is the stack
    page for navigation rows, None for the Stop row. The Stop row
    is consequential (palette confirmation, default No, on top of
    the failsafe trip the button itself performs); navigation rows
    are not, because re-arm and undo still confirm on the Doctor
    page itself.
    """
    return [
        {"label": "Re-arm resilience", "kind": "LOOP",
         "description": "Open the Doctor page — re-arming itself still "
                        "needs confirmation there",
         "action_id": "loop_rearm_resilience", "consequential": False,
         "target_page": 7},
        {"label": "Undo last change", "kind": "LOOP",
         "description": "Open the Doctor page undo card — undo itself "
                        "still needs confirmation there",
         "action_id": "loop_undo_last", "consequential": False,
         "target_page": 7},
        {"label": "Voice bridge", "kind": "LOOP",
         "description": "Open Settings — the voice bridge toggle lives "
                        "there, session only, off by default",
         "action_id": "loop_voice_bridge", "consequential": False,
         "target_page": 4},
        {"label": "Stop running goal", "kind": "LOOP",
         "description": "Emergency stop — trips the failsafe and ends "
                        "the running goal or plan",
         "action_id": "loop_stop_goal", "consequential": True,
         "target_page": None},
    ]


def missing_tool_entries(declarations: list[dict],
                         existing_entries: list[dict]) -> list[dict]:
    """Tool entries for registered tools with no row yet.

    ``existing_entries`` is the palette's current row list (any
    kind); a declaration is covered when a row carries
    ``tool_<name>`` as its action_id. The widget already emits one
    TOOL row per declaration, so against the widget's own rows
    this returns [] — the point of the helper is that a future
    row builder cannot silently drop a tool: coverage is now
    computable and tested, not assumed from the widget's loop.
    """
    present = {str(e.get("action_id") or "") for e in (existing_entries or [])}
    missing = []
    for entry in build_tool_entries(declarations or []):
        if entry["action_id"] not in present:
            missing.append(entry)
    return missing


def coverage_report(declarations: list[dict],
                    existing_entries: list[dict]) -> dict:
    """Registry-vs-palette counts: registered, rows, missing names."""
    built = build_tool_entries(declarations or [])
    missing = missing_tool_entries(declarations or [], existing_entries or [])
    return {"registered": len(built),
            "rows": len([e for e in (existing_entries or [])
                         if str(e.get("action_id") or "").startswith("tool_")]),
            "missing": [e["action_id"][5:] for e in missing]}


def build_tool_entries(declarations: list[dict]) -> list[dict]:
    """TOOL palette entries from the existing tool registry's
    declarations. No new tools are invented here: a declaration
    without a name is skipped, and entries carry no callable — the
    widget maps them to its composer-prefill action, never to direct
    execution, so the C-14 confirm gate still runs through the brain.
    """
    entries = []
    for decl in declarations or []:
        name = (decl or {}).get("name") or ""
        if not name:
            continue
        description = str((decl or {}).get("description") or "")
        summary = description.split("\n", 1)[0].strip()
        if len(summary) > 120:
            summary = summary[:119].rstrip() + "…"
        entries.append({
            "label": humanise_tool_name(name),
            "kind": "TOOL",
            "description": summary,
            "action_id": "tool_" + name,
            "consequential": False,
        })
    return entries

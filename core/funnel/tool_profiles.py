"""Tool profiles (v5.45, HANDS H13): named resident tool sets.

A profile decides which tools a session offers as RESIDENT —
loaded directly, by name — while everything else in the
registry stays reachable through the use_toolbox router
(core/funnel/toolbox.py) AND through the domain routers
(core/brain/domain_routers.py: one synthetic declaration per
catalogue domain, carrying its tools' names + one-line
purposes, full schemas server-side). Profiles shape two surfaces:

  * the CLOUD Live session's connect-time declarations
    (core/voice/live_client.py builds them through
    profile_declarations() below), and
  * the local brain's resident render (core/voice/local_client.py
    claims full schema lines for the profile's resident names
    first, via resident_names_for_profile()).

The profiles:

  "assistant"  Today's behaviour, byte-for-byte: the resident
               core set (toolbox.CORE_TOOL_PRIORITY) plus
               use_toolbox, built by toolbox.router_declarations.
               With H6 usage counts (Live connect only) the
               resident names are the usage-adapted set from
               toolbox.compute_resident_tool_names.
  "desktop"    The computer-use set: DESKTOP_TOOL_PRIORITY
               (~40 tools — input, UIA, OCR/vision, windows,
               files, browser, clipboard, run/launch) plus
               use_toolbox, with FULL UNTRIMMED descriptions.
               Its declarations are built from the canonical
               registry entries through the Gemini-safe schema
               conversion ONLY (never the budget fitter's
               description caps): the whole set measures
               ~41 KB, far inside the 240,000-byte Live budget,
               so the fitter is a no-op on it by construction —
               profile_declarations runs the fit as the guard
               and it provably changes nothing.
  "full"       Complete coverage, not direct declarations for
               every tool (domain-router redefinition,
               2026-10-27): the resident core set with FULL
               untrimmed descriptions, plus the domain
               routers, plus use_toolbox. "Full" used to mean
               the unrouted fitted full list itself; that list
               busts the 240,000-byte budget at 640 tools and
               is now an informational number only, never
               flown by any profile. A gimmick filter still
               applies to the direct declarations (the router
               declarations pass it by name — see
               core/brain/domain_routers.py for why routed gimmicks
               stay reachable).

Selection: core/config.py's tool_profile field (env
TOOL_PROFILE, default "desktop" since v5.49, programme
item 34 completion — see DEFAULT_PROFILE below), edited in
Settings. Unknown values resolve to the default at every
read site, so a garbled .env can never invent a fourth
shape.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..brain.domain_routers import build_domain_routers  # Phase 3 B1b: moved to core/brain/; Phase 3 F2a: tool_profiles now in core/funnel/
from .. import config_spec
from .toolbox import (CORE_TOOL_PRIORITY, gemini_decl_for_names,
                      router_declarations)

#: Every profile name, in Settings display order.
PROFILE_NAMES: tuple[str, ...] = ("assistant", "desktop", "full")

#: The default (v5.49, programme item 34 completion): "desktop"
#: — VYUK's job is controlling this PC, so a FRESH install leads
#: with the computer-use set. This is only a dataclass/env
#: DEFAULT: an existing install's stored TOOL_PROFILE (in .env,
#: loaded into os.environ by core/config.load_env_file before
#: AppConfig's default_factory reads it) always wins over it —
#: the flip changes what a fresh config reads, never what a
#: stored config says. Pinned in test_v549_p34.py.
DEFAULT_PROFILE = "desktop"

# ---------------------------------------------------------------------------
# v5.49 (programme item 34): gimmick-tool curation.
#
# CURATION RULE — read this before registering a new tool. A
# tool belongs in GIMMICK_TOOLS when its SOLE purpose is play,
# novelty or a daily ritual — a game, a fortune / compliment /
# quote ritual, or a random-chance toy — and it yields no
# information, file, system state or decision support a user
# could act on outside the amusement itself. Tools that
# produce real artifacts or information at the user's
# direction are NOT gimmicks, however playful they feel: the
# creative packs' image / music / text generators, the study
# pack, and the calculators all stay curated-in.
#
# The membership below was taken from the tools' own
# declarations and pack docstrings, never guessed:
#   * skills_fun — its docstring calls it "the play pack";
#   * skills_fun_party — "a second play pack";
#   * skills_games — five playable games against VYUK;
#   * two chance toys registered outside the play packs:
#     roll_dice ("for games and random numbers") and
#     random_choice ("for coin-flip moments, not real
#     decisions"), both in skills_smart_chance.
#
# EFFECT: a gimmick is never RESIDENT. It is filtered out of
# resident_names_for_profile() (assistant + desktop), out of
# desktop_declarations(), and out of the "full" profile's
# direct declarations in profile_declarations() — "full" now
# offers the resident core + domain routers + use_toolbox
# (domain-router redefinition, 2026-10-27), so this filter
# is the only thing standing between a gimmick and a direct
# declaration there. resident_names_for_profile("full") still
# returns None (whole registry) by contract; the full-profile
# exclusion lives in profile_declarations(), the surface
# that materialises that profile's offered set.
#
# Gimmicks stay REGISTERED and router-reachable: use_toolbox
# find / describe / call still finds and runs every one of
# them. Curation shapes what a session LEADS with, never
# what VYUK can do. Nothing here deletes or unregisters.
# ---------------------------------------------------------------------------

#: The gimmick / novelty / entertainment-only tools (v5.49,
#: programme item 34 — see the curation rule above).
GIMMICK_TOOLS: frozenset = frozenset({
    # skills_fun — the play pack: guess-the-tune game,
    # co-written story game, daily word ritual, compliment
    # ritual, tabletop dice with advantage, the coin-flip
    # decision toy, generated novelty soundscapes, daily
    # quote ritual.
    "music_trivia", "story_chain", "word_of_the_day",
    "compliment_engine", "dice_roller_plus", "coin_decision",
    "ambient_scene", "quote_of_morning",
    # skills_fun_party — the second play pack: riddle game,
    # rock-paper-scissors, Mad Libs, fortune cookie.
    "riddle_time", "rock_paper_scissors", "mad_libs",
    "fortune_cookie",
    # skills_games — playable games against VYUK.
    "tic_tac_toe", "hangman", "number_guess", "trivia_quiz",
    "word_game",
    # skills_smart_chance — random-chance toys: dice for games,
    # random pick "for coin-flip moments, not real decisions".
    "roll_dice", "random_choice",
})


def _resident_without_gimmicks(names: Any) -> tuple[str, ...]:
    """`names` with every GIMMICK_TOOLS member removed, order
    preserved (v5.49, programme item 34)."""
    return tuple(n for n in names if n not in GIMMICK_TOOLS)


def _declarations_without_gimmicks(
        decls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Declaration list with every gimmick removed, order
    preserved (v5.49, programme item 34)."""
    return [d for d in decls if d.get("name") not in GIMMICK_TOOLS]

#: The desktop (computer-use) resident set, in offer order:
#: keyboard/mouse input, UIA + window-control inspection and
#: clicking, OCR/vision looks, window management, launch/run,
#: files, browser control, clipboard — plus app_settings
#: (v5.52, item 8) and play_music (v5.52, item 4). 59 tools;
#: every name is a registered tool (the H13 suite pins both
#: facts).
DESKTOP_TOOL_PRIORITY: tuple[str, ...] = (
    # input
    "type_text", "press_hotkey", "mouse_control", "drag_mouse",
    "cursor_control",
    # UIA / window inspection + activation
    "uia_find_control", "uia_click_control", "uia_type_text",
    "uia_read_tree", "get_window_controls", "click_element",
    "click_text",
    # v5.49 (programme item 34 completion): click_by_mark — the
    # mark-id clicker for the numbered-screenshot flow (item 30)
    # is a desktop-control essential, so it is resident here,
    # beside its UIA/click siblings.
    "click_by_mark",
    # OCR / vision
    "take_screenshot", "screen_look", "ocr_screen", "capture_window",
    # windows
    "get_active_window", "window_control", "window_snap",
    "window_place", "tile_windows", "virtual_desktop",
    # run / launch
    "launch_application", "close_application", "run_command",
    "process_control",
    # Field-check fix #32 (round-2 N5): run_anticipation_routine
    # must be resident here, not only in CORE_TOOL_PRIORITY —
    # the desktop profile is the default Live profile, and a
    # tool absent from it can only be reached via use_toolbox,
    # which the model does not route a by-name demand through
    # (field-check fix #10, V2 — the get_current_time_date
    # precedent, same shape).
    "run_anticipation_routine",
    # Round-3 fix #4 (T12): run_plan — the same shape as
    # run_anticipation_routine above. The desktop profile is
    # the default Live profile; without this, a spoken "run my
    # morning plan" is answered in words and the tool never
    # runs.
    "run_plan",
    # Round-3 fix Q5+Q7 (section B): the memory tools and the
    # routine pair — the same shape as run_anticipation_routine
    # and run_plan above. The desktop profile is the default
    # Live profile; without these, a spoken "remember that...",
    # "what do you remember about...", "what's 2+2" or "run my
    # morning routine" is answered in words (or routed to the
    # wrong runner) and the tool never runs — reachable only
    # via use_toolbox, which the model does not route a
    # by-name demand through.
    "remember_user_fact", "recall_memories", "quick_math",
    "run_routine", "save_routine",
    # files
    "file_control", "find_files", "open_path", "read_file",
    "write_file",
    # browser
    "browser_control", "browser_read", "browser_click",
    "browser_type", "browser_select", "browser_press", "open_url",
    # clipboard
    "read_clipboard", "write_clipboard", "clipboard_files",
    # v5.52 (item 8): her own settings — app-internal, resident
    # in every profile (here, in CORE_TOOL_PRIORITY for the
    # assistant profile, and in "full" via the whole registry).
    "app_settings",
    # v5.52 (item 4): play_music — a top-canonical command.
    "play_music",
    # field-check fix #10 (V2): get_current_time_date — the
    # most-asked read-only info tool ("what time is it"), and
    # the one the assistant profile already offers first
    # (CORE_TOOL_PRIORITY). The desktop profile is the
    # default Live profile, so without this a spoken
    # named-tool demand is answered in words and the tool
    # never runs — reachable only via use_toolbox, which the
    # model does not route a by-name demand through.
    "get_current_time_date",
    # Round-3 fix Q6 (B10): get_morning_briefing — the same
    # shape as get_current_time_date above. The section-B
    # morning flow asks for a briefing by name; without this
    # the model improvises the schedule from reminders instead
    # of calling the tool that reads the calendar.
    "get_morning_briefing",
    # Stage 0 features batch 2 (Group E): the mail and
    # calendar actions — the same shape as run_plan and
    # get_morning_briefing above. The desktop profile is the
    # default Live profile; without these, a spoken named-tool
    # demand is answered in words and the tool never runs.
    "find_free_slots", "snooze_email",
    "cancel_calendar_event", "reschedule_calendar_event",
    # Stage 0 features batch 3 (Group F, 2026-11-22): the money
    # actions — same shape as batch 2 above.
    "check_money_alerts", "portfolio_tracker",
)

#: Backwards-friendly alias (the set, not the ordering claim).
DESKTOP_TOOL_NAMES = DESKTOP_TOOL_PRIORITY


def normalize_profile(value: Any) -> str:
    """A profile name from any raw value: stripped, lowercased,
    and validated against PROFILE_NAMES — anything unknown (or
    empty, or not a string) resolves to the default, so callers
    never have to re-check."""
    name = str(value or "").strip().lower()
    return name if name in PROFILE_NAMES else DEFAULT_PROFILE


def current_profile_name(config_obj: Any = None) -> str:
    """The configured profile: the passed config object's
    tool_profile field when it carries a value, else the spec's
    live TOOL_PROFILE read, else the default. (The real AppConfig
    field already folds the env var in; the spec fallback serves
    stub configs in tests and tools.)"""
    raw = getattr(config_obj, "tool_profile", None) if config_obj \
        is not None else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = config_spec.env("TOOL_PROFILE")
    return normalize_profile(raw)


def resident_names_for_profile(profile: Any) -> tuple[str, ...] | None:
    """The profile's resident tool names, in priority order —
    or None for "full", whose resident set is the whole
    registry (the caller resolves that against its own
    registry view). This is the shape the LOCAL brain renders
    as resident; it carries no usage adaptation (H6 promotion
    is a Live-connect behaviour).

    v5.49 (programme item 34): the returned names pass through
    the GIMMICK_TOOLS filter — today neither curated list
    contains a gimmick, so the filter is a no-op on the shipped
    lists and a guard for future ones. "full" still returns
    None by contract; its gimmick exclusion is applied where
    the profile is materialised, in profile_declarations()."""
    name = normalize_profile(profile)
    if name == "desktop":
        return _resident_without_gimmicks(DESKTOP_TOOL_PRIORITY)
    if name == "full":
        return None
    return _resident_without_gimmicks(CORE_TOOL_PRIORITY)


def _payload_size(registry: Any, decls: list) -> int:
    size_of = getattr(registry, "gemini_payload_size", None)
    if callable(size_of):
        return int(size_of(decls))
    return len(json.dumps(decls))


def desktop_declarations(registry: Any,
                         *, include_toolbox: bool = True,
                         ) -> list[dict[str, Any]]:
    """The desktop profile's offered declarations: the desktop
    set in DESKTOP_TOOL_PRIORITY order, then the domain
    routers, then use_toolbox (omitted when include_toolbox
    is False — the router-off shape), all with full untrimmed
    descriptions — built through the shared builder
    (toolbox.gemini_decl_for_names, cap None), which is the
    old per-tool build moved, not a second copy.
    The registry's budget fitter runs over the result as the
    GUARD: at ~90 KB the set is far under the 240,000-byte
    budget, so the fit is a no-op and the output equals the
    untrimmed build exactly; if a future registry somehow
    pushed the set over budget, the fit (and only then)
    would trim, and a still-over list raises ValueError."""
    decls = gemini_decl_for_names(
        registry, (*_resident_without_gimmicks(
            DESKTOP_TOOL_PRIORITY),))
    decls = decls + build_domain_routers(registry)
    if include_toolbox:
        decls = decls + gemini_decl_for_names(registry, ["use_toolbox"])
    fit = getattr(registry, "_gemini_fit_budget", None)
    if callable(fit):
        fit(decls)
    budget = int(getattr(registry, "_GEMINI_DECL_BUDGET", 240_000))
    size = _payload_size(registry, decls)
    if size > budget:
        raise ValueError(
            f"Desktop tool payload is {size} bytes, over the "
            f"{budget}-byte Live API budget even after the "
            "guard fit. Shrink DESKTOP_TOOL_PRIORITY before "
            "shipping.")
    return decls


def _with_routers(base: list[dict[str, Any]],
                  routers: list[dict[str, Any]],
                  *, include_toolbox: bool = True,
                  ) -> list[dict[str, Any]]:
    """A resident declaration list with the domain routers
    inserted after the resident tools and use_toolbox (when
    wanted) moved to the end: resident + routers + toolbox.
    The use_toolbox declaration is split out by name so the
    router-off shape (include_toolbox=False) is the same list
    minus exactly that one declaration."""
    toolbox = [d for d in base if d.get("name") == "use_toolbox"]
    rest = [d for d in base if d.get("name") != "use_toolbox"]
    out = rest + routers
    if include_toolbox:
        out = out + toolbox
    return out


def profile_declarations(registry: Any, profile: Any = None,
                         *, usage_counts: dict[str, int] | None = None,
                         include_toolbox: bool = True,
                         ) -> list[dict[str, Any]]:
    """The declarations a CLOUD Live session offers for
    `profile` (None = the configured profile, read from
    core.config). This is the one builder the Live connect
    path calls. Every profile returns resident declarations +
    domain routers + use_toolbox (omitted when
    include_toolbox is False — the router-off shape):

      assistant -> toolbox.router_declarations(registry), or
                   the H6-adapted resident set when
                   `usage_counts` is given (Live passes the
                   session-start activity-log counts);
      desktop   -> desktop_declarations(registry) (usage
                   counts do not reshape the curated set);
      full      -> the resident core set with FULL untrimmed
                   descriptions + domain routers + use_toolbox.
                   "Full" means complete coverage, not direct
                   declarations for every tool: the retired
                   all-direct fitted list (DECL-01, owner ruling
                   2026-11-01) used to bust the budget at 640
                   tools; it no longer exists.

    v5.49 (programme item 34): EVERY direct declaration
    passes through the GIMMICK_TOOLS filter before it is
    returned. For assistant and desktop the filter is a
    no-op on today's curated lists; for "full" it is the
    curation of the direct core. Router declarations (named
    route_*) pass the filter by construction — routed
    gimmicks stay reachable, as they already are through
    use_toolbox (see core/brain/domain_routers.py). The filter
    also strips a gimmick that an H6 usage promotion
    (toolbox.resident_declarations) displaced into the
    assistant resident declarations, so no profile's
    offered set can carry a gimmick as a direct declaration
    whatever the usage log says.

    Every result fits the registry's 240,000-byte budget:
    the desktop builder measures against it at build time,
    and the assistant and full sets — small by construction
    (resident + routers, ~90 KB) — are measured by the ship
    gate and the doctor check (INV-1 re-scope: the gate
    measures offered sets, not a fitted full list). No
    offered-set builder in this module ever built a fitted
    full list; offered sets are measured directly against
    the budget, so no fitted list can break any profile's
    session."""
    if profile is None:
        try:
            from ..config import config as _config  # Phase 3 F2a: tool_profiles now in core/funnel/; config stays flat

            profile = current_profile_name(_config)
        except Exception:  # noqa: BLE001 — default is safe
            profile = DEFAULT_PROFILE
    name = normalize_profile(profile)
    routers = build_domain_routers(registry)
    if name == "full":
        core = gemini_decl_for_names(
            registry, _resident_without_gimmicks(CORE_TOOL_PRIORITY))
        out = core + routers
        if include_toolbox:
            out = out + gemini_decl_for_names(registry, ["use_toolbox"])
        return _declarations_without_gimmicks(out)
    if name == "desktop":
        return desktop_declarations(registry,
                                    include_toolbox=include_toolbox)
    if usage_counts:
        from .toolbox import resident_declarations

        return _declarations_without_gimmicks(
            _with_routers(resident_declarations(registry, usage_counts),
                          routers, include_toolbox=include_toolbox))
    return _declarations_without_gimmicks(
        _with_routers(router_declarations(registry),
                      routers, include_toolbox=include_toolbox))

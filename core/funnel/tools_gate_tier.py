"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

from typing import Any
from .tools_gate_shell import (_HOTKEY_SHELL_KEYS, _foreground_is_shell, _normalized_hotkey_tokens)
from .tools_gate_egress import (_is_egress_read, _plugin_gate_class)
from .tools_gate_readclass import (_is_read_action)
from .tools_gate_readonly import (_READ_ONLY_TOOLS)



# ------------------------------------------------------------------
# Provenance-tiered taint (v5.46, programme item 2, D-C1 + D-C2)
#
# HARD taint (a pattern hit in core/injection) keeps the original
# H2 design exactly: every state-changing tool gates, egress
# included, looks exempt. SOFT taint is new: content that merely
# ARRIVED from an external source (a benign web page, an image
# frame) creates caution without a pattern hit — and gating
# EVERY action on it made see-then-act work unusable (a popup
# per step; plan runs have no user message to clear it). So
# under soft taint the gate decision tiers the tool:
#
#   Tier 0 (observe)  — never taint-gated.
#   Tier 1 (navigate) — gates, UNLESS a screen scope is active
#                       (below): the human approved acting on
#                       what's on screen for a bounded time.
#   Tier 2 (high-impact) — gates under soft taint exactly as
#                       under hard; a scope NEVER frees it.
#
# The tier classification is consulted ONLY from the soft branch
# of _needs_confirmation (and execute()'s scope-ask / log
# labelling), so untainted and hard-taint decisions are
# byte-for-byte the pre-v5.46 ones.
_TYPE_FAMILY = frozenset({
    "type_text", "paste_text", "type_clipboard", "paste_snippet",
    "uia_type_text",
})
_TIER1_NAV_TOOLS = frozenset({
    "mouse_control", "cursor_control", "click_element",
    "uia_click_control",
    "click_by_mark",  # v5.49 (programme item 30)
})


def _hotkey_gate_classes_fire(args: dict[str, Any] | None) -> bool:
    """True when a press_hotkey call carries a combo class the
    press_hotkey branch of _needs_confirmation gates outright:
    a shell-escape key token (Win/Meta/Super/Cmd), Ctrl+Shift+Esc,
    Alt+F4, Ctrl+W, Shift+Delete, Ctrl+Alt+Delete, or ANY combo
    while a shell owns the foreground. This MIRRORS that branch
    token-for-token (same _normalized_hotkey_tokens
    normalisation) for _tool_tier's Tier-2 classification — the
    two must move together: a combo class added to the gate
    there belongs here too, or soft taint would scope-free a
    combo the untainted gate treats as high-impact."""
    tokens = _normalized_hotkey_tokens((args or {}).get("keys", ""))
    if tokens & _HOTKEY_SHELL_KEYS:
        return True
    has_ctrl = bool(tokens & {"ctrl", "control"})
    has_alt = "alt" in tokens
    has_shift = "shift" in tokens
    if has_ctrl and has_shift and bool(tokens & {"esc", "escape"}):
        return True  # Ctrl+Shift+Esc (Task Manager)
    if has_alt and "f4" in tokens:
        return True  # Alt+F4
    if has_ctrl and "w" in tokens:
        return True  # Ctrl+W
    if has_shift and "delete" in tokens:
        return True  # Shift+Delete (incl. Ctrl+Shift+Delete)
    if has_ctrl and has_alt and "delete" in tokens:
        return True  # Ctrl+Alt+Delete
    return _foreground_is_shell()


def _tool_tier(name: str, args: dict[str, Any] | None) -> int:
    """The soft-taint risk tier of one call (v5.46, item 2):
    0 = observe, 1 = navigate, 2 = high-impact. The DEFAULT for
    anything not read-only and not Tier 1 is Tier 2 — a new
    state-changing tool fails safe (gates under soft taint)
    until someone tiers it deliberately."""
    args = args or {}
    # Tier 0 (observe): the look tools (the H12 corollary — a
    # look is a read), read actions of action-dispatched tools,
    # plugin "read" class, and read-only tools that cannot put
    # bytes on the wire.
    if name == "screen_look":
        return 0
    if name == "browser_read":
        # Click-anywhere Batch 0 (Plan 9 §4, N-13): the DOM read
        # feeds the observation — Tier 0 under soft taint (it
        # passes; its content is wrapped + screened at the
        # producer). Deliberately NOT a _READ_ONLY_TOOLS member:
        # under HARD taint it still gates (the hard-taint branch
        # exempts only _READ_ONLY_TOOLS members), which is
        # correct — a pattern hit means the page tried to
        # instruct VYUK.
        return 0
    if name == "take_screenshot" and not str(
            args.get("save_path", "") or "").strip():
        return 0
    if _is_read_action(name, args):
        return 0
    _pclass = _plugin_gate_class(name)
    if _pclass == "read":
        return 0
    # Tier 2 (high-impact): egress reads (the item-4
    # _is_egress_read inversion is the egress test), plugin
    # egress/gated classes.
    if _is_egress_read(name):
        return 2
    if _pclass in ("egress", "gated"):
        return 2
    if name in _READ_ONLY_TOOLS:
        return 0  # read-only and non-egress (egress handled above)
    if name == "launch_application":
        # Tier 2 per the approved brief: a launch is the
        # see-then-act chain's code-exec step (item 3 / C1), so a
        # screen scope never frees it, even for ordinary apps.
        return 2
    if name == "window_control":
        action = str(args.get("action", "")).strip().lower()
        return 2 if action == "close" else 1
    if name == "personal_skills":
        # v5.67: admit is the state-changing step (Tier 2, never
        # freed by a screen scope); list/inspect are Tier 1 reads.
        # B8: run is code execution — Tier 2, like admit.
        action = str(args.get("action", "")).strip().lower()
        return 2 if action in ("admit", "run") else 1
    if name == "health":
        # v5.69: record is the state-changing step (Tier 2);
        # extract/summary/remind are read-class (Tier 0 via
        # _is_read_action, above) so this branch only fires for
        # record.
        action = str(args.get("action", "")).strip().lower()
        return 2 if action == "record" else 1
    if name == "press_hotkey":
        return 2 if _hotkey_gate_classes_fire(args) else 1
    if name in _TYPE_FAMILY:
        # Typing/pasting is navigation — until it lands in a
        # shell, where it is one Enter from run_command (H11).
        return 2 if _foreground_is_shell() else 1
    if name in _TIER1_NAV_TOOLS:
        return 1
    if name == "browser_control":
        # Click-anywhere Batch 0 (Plan 9 §4): navigation is
        # Tier 1 — gated unless a screen scope is active. Other
        # browser_control actions stay Tier 2 (fail-safe).
        action = str(args.get("action", "")).strip().lower()
        if action in ("goto", "new_tab", "back", "forward", "reload"):
            return 1
        return 2
    if name in ("browser_click", "browser_type", "browser_press"):
        # Click-anywhere Batch 0 (Plan 9 §4, N-13): Tier 1 for
        # navigation / read-only controls, keyed on the call's
        # action class — the picker's declared class, carried in
        # the call's args (a named field, not inferred at the
        # gate). Anything else, including an absent/unknown
        # class, is Tier 2 (fail-safe default).
        try:
            from .. import browser_scope as _scope
        except Exception:  # noqa: BLE001
            try:
                from core import browser_scope as _scope
            except Exception:  # noqa: BLE001 — unreadable: Tier 2
                return 2
        action_class = str(
            args.get("action_class", "") or "").strip().lower()
        if action_class in _scope.TIER1_ACTION_CLASSES:
            return 1
        return 2
    if name == "click_anywhere":
        # Click-anywhere Batch 1 (Plan 9 §4, D-20): tiered on the
        # call's action_class arg, the picker's declared class —
        # navigation / read-only -> Tier 1; consequential /
        # absent / unknown -> Tier 2 (fail-safe default). The
        # gate-side backstop (plan_approval.covering_call) still
        # refuses coverage on a D-18 word match in the element's
        # name/text whatever class was declared.
        action_class = str(
            args.get("action_class", "") or "").strip().lower()
        if action_class in ("navigation", "read-only"):
            return 1
        return 2
    return 2

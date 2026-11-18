"""Per-kind allowlist family of the confirm gate (split B18).

Pure w.r.t. module state: the store is data/confirm_allowlist.json,
resolved from __file__ exactly as before (same directory, same path).
"""

import json
import logging
import os
import pathlib
import tempfile

# INTENTIONAL (skills split B18): the old module's log is
# logging.getLogger("nova.confirm_gate"); getLogger is a registry
# lookup by name, so this binds the SAME logger object, and the
# _save_allowlist failure warning is identical.
log = logging.getLogger("nova.confirm_gate")



# ---------------------------------------------------------------------------
# Per-kind allowlist (v5.54.1, field fix — "Always allow this
# kind"). The confirm dialog offers the third button ONLY for
# allowlistable kinds (see is_allowlistable); the gate checks
# the allowlist before prompting (see request_confirmation).
# The store is data/confirm_allowlist.json, a plain list of
# tool names, written atomically like .env (tmp + os.replace)
# and created lazily — it never ships in the install tree, so
# the shipped data/ baseline is untouched. A kind the user
# remembered is revocable from Settings; deleting the file
# revokes everything.
#
# WHAT IS ALLOWLISTABLE — derived from the gate's own
# taxonomy in core/funnel/tools_gate.py, never copied by hand (the
# v5.32 lesson: a copied constant drifts from the product it
# guards, so the sets are read from tools_gate at call time):
#   allowlistable = (Tier-1 navigation kinds) MINUS (the
#   critical set) MINUS (kinds with a destructive action).
# Tier-1 navigation = _TIER1_NAV_TOOLS (mouse/cursor/click
# kinds) + _TYPE_FAMILY (typing kinds). The critical set
# (_CONFIRM_GATE_TOOLS) already removes paste_text,
# type_clipboard and paste_snippet (paste-into-terminal
# parity). The kind-level exclusions below remove every
# args-aware gated kind — as KINDS they each include a
# destructive action (close, shell combos, run/enable/
# disable, non-status system settings, persistent sets,
# traffic reroutes, power-policy sets, Wi-Fi changes), so no
# per-arg carve-out can make the kind safe — plus
# launch_application (Tier 2 by brief: a screen scope never
# frees it) and browser_click (browser.py's payment /
# destructive click guard). What is left is seven navigation
# kinds: mouse_control, cursor_control, click_element,
# uia_click_control, click_by_mark, type_text, uia_type_text —
# PLUS, by conversion-pass Phase 0 owner decision (2026-10-25),
# browser_type (Web typing): it is in neither Tier-1 set nor
# the critical set nor _NEVER_ALLOWLIST_KINDS, so it is added
# explicitly via _EXTRA_ALLOWLISTABLE_KINDS below rather than
# by widening a tools_gate set (browser_type stays
# args-gated in tools_gate; this only adds the Always button
# and the shortcut, with the any-taint carve-out below).
# Everything else — delete, send, purchase, shell, system
# settings, plugin trust, capture grants, multi-step task
# grants — never gets the button, by construction. There is
# NO blanket "allow everything" mode: the store holds kinds,
# and a kind not in the allowlistable set can never enter it
# (allow_kind refuses it).
#
# WHEN THE SHORTCUT APPLIES — even for an allowlisted kind,
# the ask still happens when: the session is HARD-tainted
# (a pattern hit is attack evidence; H2 outranks
# convenience), the kind is a type-family kind aimed at a
# shell foreground (H11: one Enter from run_command), or the
# session-provenance lock gates the call (a session the user
# did not start). Soft taint (provenance only) does NOT block
# the shortcut — it is the fatigue the button exists for,
# and the standing grant is narrower than the screen scope
# (one kind, not one app, and it never frees Tier-2 tools).
# EXCEPTION, browser_type ONLY (conversion-pass Phase 0 hard
# line): taint of ANY kind — hard OR soft — defeats the
# browser_type shortcut, always ask. The existing kinds keep
# hard-taint-only defeat. A content-prompted browser_type
# auto-approval would be exactly the injection hole the gate
# exists to close, so a stored browser_type entry shortcuts
# ONLY a direct, untainted user request.
# Any failure to decide (unreadable store, unimportable
# gate module, unknown taint state) means ASK, never skip.
#
# UI rethink Build 4 (§4.5): the derivation above IS the
# safe-shape test — passes_safe_shape() names each rule and
# reads it from the gate's own taxonomy, never a hand copy
# (the v5.32 lesson). The per-kind args-safety proof lives
# in kind_args_check(): every allowlistable kind either
# carries an args check the shortcut applies (the D-18
# backstop for the three click kinds — a click whose
# name/text matches a D-18 word still asks, whatever the
# picker declared — the H11 shell-foreground check for the
# type kinds, any-taint defeat for browser_type) or takes
# coordinates only (mouse_control / cursor_control: no name
# to check, the icon-only residual the plan accepts —
# covering_call documents the same). A kind that is safe in
# general and dangerous in one argument is not a safe kind:
# the three click kinds keep the button ONLY because the
# shortcut checks the argument.
# ---------------------------------------------------------------------------

_ALLOWLIST_FILE = "confirm_allowlist.json"

# Kinds with a destructive action, excluded as kinds even
# though some of their actions are benign (tools_gate.py's
# args-aware branches + the Tier-2-by-brief launch + the
# payment-click guard — see the header comment).
_NEVER_ALLOWLIST_KINDS = frozenset({
    "window_control", "press_hotkey", "task_scheduler_control",
    "firewall_control", "fast_startup_control",
    "hibernate_setting", "env_var", "vpn_control",
    "power_plans", "network_control", "launch_application",
    "browser_click",
})

# Conversion-pass Phase 0 (owner decision 2026-10-25):
# kinds allowlistable by explicit addition, not by tools_gate
# Tier-1 membership. browser_type is Web typing; it sits in
# neither _TIER1_NAV_TOOLS nor _TYPE_FAMILY and in neither
# _CONFIRM_GATE_TOOLS nor _NEVER_ALLOWLIST_KINDS (verified
# 2026-10-25), so the minimal change is this set, not a
# tools_gate edit — tools_gate's own gating of browser_type
# is untouched. Kept deliberately separate so a future
# tools_gate reshuffle cannot silently widen the button.
_EXTRA_ALLOWLISTABLE_KINDS = frozenset({
    "browser_type",
})


def _allowlist_path():
    return (pathlib.Path(__file__).resolve().parent.parent
            / "data" / _ALLOWLIST_FILE)


def _load_allowlist() -> set:
    """The stored kinds, or the empty set on any failure
    (missing file, bad JSON, wrong shape) — a broken store
    asks afresh, never skips."""
    try:
        data = json.loads(_allowlist_path().read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data
                    if isinstance(x, str) and x}
    except Exception:  # noqa: BLE001
        pass
    return set()


def _save_allowlist(kinds: set) -> bool:
    """Atomic store write. False (never raising) when the
    file cannot be written — the caller must say so, because
    a remembered choice that did not stick is exactly the
    silent-failure class v5.54.1 kills elsewhere."""
    try:
        path = _allowlist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # F-01 audit: mkstemp-unique temp file, not the old fixed
        # "<name>.tmp" — two concurrent allow/revoke writes could
        # otherwise interleave in one shared temp file. (The
        # read-modify-write in allow_kind/revoke_kind is still
        # unlocked; a lost update across two simultaneous
        # resolutions remains possible and is documented, not
        # claimed fixed — same residual class as planner.)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(sorted(kinds), indent=1))
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("confirm_gate allowlist save failed (%s: %s)",
                    type(e).__name__, e)
        return False


# Click kinds whose args carry the element's name/text —
# the D-18 backstop (Q-12) applies to them at shortcut
# time: a name/text match still asks. The same three kinds
# covering_call checks at plan-dispatch time.
_D18_CLICK_KINDS = frozenset({
    "click_element", "uia_click_control", "click_by_mark",
})


def _d18_defeats_shortcut(tool_name: str, args) -> bool:
    """True when this click's args carry a D-18 word in the
    element's name/text — the shortcut must NOT apply.
    args None means the click cannot be judged: fail closed
    (True). Any matcher failure is also True (ask)."""
    if tool_name not in _D18_CLICK_KINDS:
        return False
    if not isinstance(args, dict):
        return True
    try:
        from . import browser_scope as _scope
    except Exception:  # noqa: BLE001
        try:
            from core import browser_scope as _scope
        except Exception:  # noqa: BLE001 — undecidable: ask
            return True
    try:
        for key in ("name", "text"):
            if _scope.matches_d18_words(args.get(key)):
                return True
    except Exception:  # noqa: BLE001 — undecidable: ask
        return True
    return False


def _in_allowlistable_base(tool_name: str) -> bool:
    """The set derivation without the rule-6 args check:
    Tier-1 navigation + type family + the explicit extra
    kinds, minus the critical set and the kind-level
    exclusions. Shared by passes_safe_shape() and
    kind_args_check() so neither calls the other."""
    try:
        from .funnel import tools_gate as _gate  # Phase 3 F1

        tier1 = set(getattr(_gate, "_TIER1_NAV_TOOLS", ()))
        tier1 |= set(getattr(_gate, "_TYPE_FAMILY", ()))
        critical = set(getattr(_gate, "_CONFIRM_GATE_TOOLS", ()))
        if tool_name in critical or tool_name in _NEVER_ALLOWLIST_KINDS:
            return False
        if tool_name in tier1 or tool_name in _EXTRA_ALLOWLISTABLE_KINDS:
            return True
        return False
    except Exception:  # noqa: BLE001 — fail closed
        return False


def kind_args_check(tool_name: str) -> str:
    """The args check the shortcut applies for this kind
    (§4.5 per-kind args-safety proof), or "" when the kind
    needs none: "d18" (click kinds — name/text backstop),
    "shell-foreground" (type kinds — H11), "any-taint"
    (browser_type), "coordinates-only" (mouse/cursor —
    coordinates only, no name to check; the accepted
    icon-only residual). "" for a kind that is not
    allowlistable at all."""
    if not _in_allowlistable_base(tool_name):
        return ""
    if tool_name in _D18_CLICK_KINDS:
        return "d18"
    if tool_name == "browser_type":
        return "any-taint"
    try:
        from .funnel import tools_gate as _gate  # Phase 3 F1

        if tool_name in set(getattr(_gate, "_TYPE_FAMILY", ())):
            return "shell-foreground"
    except Exception:  # noqa: BLE001 — fail closed below
        return ""
    if tool_name in ("mouse_control", "cursor_control"):
        return "coordinates-only"
    return ""


def passes_safe_shape(tool_name: str) -> bool:
    """The §4.5 safe-shape test for a confirm-kind — every
    rule read from the gate's own taxonomy at call time,
    never a hand copy:
      1. not destructive — not in the critical set
         (_CONFIRM_GATE_TOOLS) and not in the kind-level
         exclusions (_NEVER_ALLOWLIST_KINDS, each of which
         includes a destructive action as a kind);
      2. moves no money — the money guard refuses money
         calls before the shortcut is ever consulted (see
         request_confirmation), and no allowlistable kind
         is a money tool by taxonomy (none is in the
         critical set, which holds every money-adjacent
         kind the gate knows);
      3. sends nothing — no send kind is in Tier-1 or the
         type family (send_email and friends are critical);
      4. runs no shell and changes no system setting —
         run_command, the system-setting kinds and the
         shell-escape kinds are all critical or
         never-allowlist kinds;
      5. grants no trust — plugin trust, permission and
         capture grants are not navigation kinds;
      6. args-safe as a KIND — the kind must carry an args
         check (kind_args_check() non-empty) or take
         coordinates only; a kind with neither is out;
      7. taint defeat — per-call, in the shortcut itself
         (hard taint defeats every kind, any taint defeats
         browser_type), not a property of the kind.
    False on any failure."""
    if not _in_allowlistable_base(tool_name):
        return False
    # Rule 6: the kind must carry its args check.
    if not kind_args_check(tool_name):
        return False
    return True


def is_allowlistable(tool_name: str) -> bool:
    """True only for the seven navigation kinds plus
    browser_type (see the header comment). Reads the sets
    from tools_gate at call time — never a hand copy — plus
    the explicit _EXTRA_ALLOWLISTABLE_KINDS set. False on
    any failure. Build 4: this IS the §4.5 safe-shape test
    (see passes_safe_shape) — the button appears only for
    kinds that pass it."""
    return passes_safe_shape(tool_name)


def is_allowlisted(tool_name: str) -> bool:
    """True when the kind is allowlistable AND stored."""
    if not is_allowlistable(tool_name):
        return False
    return tool_name in _load_allowlist()


def _record_grant_receipt(subject: str, action: str, how: str,
                           undo=None) -> None:
    """Best-effort §4.8 receipt: the grant event in the
    always-allow ledger plus the Activity line with its
    Undo action. Never raises, never blocks the grant —
    a failed receipt is a missing line, not a failed
    grant. (Lazy import: always_allow reads this module
    for its rows, so the import cannot sit at top level.)"""
    try:
        from . import always_allow as _aa

        _aa.record_event(subject=subject, action=action, how=how)
        _aa.log_grant_receipt(subject=subject, action=action,
                              how=how, undo=undo)
    except Exception:  # noqa: BLE001 — receipt never blocks
        pass


def allow_kind(tool_name: str, how: str = "confirm dialog") -> bool:
    """Remember a kind. Refuses (False) a kind that is not
    allowlistable — the store can never hold one — and
    reports a failed write as False instead of pretending
    it stuck. Records the §4.8 grant receipt (event +
    Activity line with Undo) — the receipt never blocks
    the grant. `how` names the tap that made it (the
    confirm dialog's third button, by default)."""
    if not is_allowlistable(tool_name):
        return False
    kinds = _load_allowlist()
    already = tool_name in kinds
    kinds.add(tool_name)
    if not _save_allowlist(kinds):
        return False
    if not already:
        _record_grant_receipt(
            f"kind:{tool_name}", "granted", how,
            undo=lambda: revoke_kind(tool_name, how="Activity"))
    return True


def revoke_kind(tool_name: str, how: str = "Settings") -> bool:
    """Forget one kind. True when the store now lacks it
    (including when it never had it); False only when the
    write itself failed. Records the §4.8 revocation
    receipt (event + Activity line with Undo = re-grant)
    when a stored kind is actually forgotten."""
    kinds = _load_allowlist()
    had = tool_name in kinds
    kinds.discard(tool_name)
    if not _save_allowlist(kinds):
        return False
    if had:
        _record_grant_receipt(
            f"kind:{tool_name}", "revoked", how,
            undo=lambda: allow_kind(tool_name, how="Activity"))
    return True


def list_allowlisted() -> list:
    """The stored kinds, sorted — the Settings revoke UI
    reads this. Only allowlistable kinds are ever listed
    (a hand-edited file with a destructive kind in it is
    read as if that entry were absent)."""
    return sorted(k for k in _load_allowlist()
                  if is_allowlistable(k))


def _allowlist_shortcut_applies(tool_name: str, args=None) -> bool:
    """The full shortcut predicate for request_confirmation:
    allowlistable + stored + no hard taint (any taint for
    browser_type) + no shell foreground for type kinds + no
    provenance lock + no D-18 word in a click kind's
    name/text args. Any undecidable input means False (ask).

    Build 4 (§4.5): `args` is the call's real arguments.
    For the three click kinds the D-18 backstop runs here,
    exactly as covering_call runs it at plan-dispatch time
    — a click on a "Send" / "Delete" / "Close" button still
    asks, whatever the picker declared. args None for a
    click kind means the click cannot be judged: ask."""
    if not is_allowlisted(tool_name):
        return False
    if _d18_defeats_shortcut(tool_name, args):
        return False
    try:
        from . import injection as _inj

        # Conversion-pass Phase 0 hard line, browser_type
        # ONLY: taint of ANY kind defeats the shortcut — a
        # content-prompted browser_type must always ask, even
        # under soft (provenance-only) taint. The existing
        # kinds keep hard-taint-only defeat below.
        if _inj.tainted() and tool_name == "browser_type":
            return False
        if _inj.tainted() and _inj.taint_kind() == "hard":
            return False
    except Exception:  # noqa: BLE001 — unknown taint state: ask
        return False
    try:
        from .funnel import tools_gate as _gate  # Phase 3 F1: tools_gate moved to core/funnel/

        if tool_name in set(getattr(_gate, "_TYPE_FAMILY", ())):
            if _gate._foreground_is_shell():
                return False
        if _gate._session_provenance_lock_gated(tool_name, None):
            return False
    except Exception:  # noqa: BLE001 — undecidable: ask
        return False
    return True

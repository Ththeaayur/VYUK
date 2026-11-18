"""
UI Automation pack for VYUK — the dedicated Windows UI Automation
(UIA) backend: find controls by name (with class, control type and
on-screen rectangle), click them by name, type into named edit
fields, and read a window's control tree.

Why a separate module when core/desktop/control_plus.py already clicks by
name? control_plus offers two quick conveniences (list a window's
named controls, click one by name). This pack is the deeper,
structured version the hardening build asked for: match *reports*
(name / class / control type / rectangle) so the brain can reason
about what is on screen before acting, typing into a named field
without ever echoing the typed text back, and a depth- and
count-bounded tree read for exploring an unfamiliar window.

Same rules as the other packs: plain functions returning
human-readable strings; pywinauto is imported lazily inside the
functions that need it and guarded with try/except, so this module
imports cleanly on any OS — including headless Linux, where every
tool simply returns a clear "isn't available" string instead of
crashing. The typed text of uia_type_text is never included in the
returned string and never logged (it may be a password).

Wiring: the parent build calls register_all(registry) from core/funnel/tools_handlers_pack_core2.py (_register_uia_tools) — this module never touches the registry itself.
"""

import sys

# How many descendants a single scan will walk before giving up —
# some windows (browsers, IDEs) expose thousands of elements.
from .uia_base import (_SCAN_CAP, _MATCH_CAP, _is_windows, _routed_backend, _truncate, _failsafe_block, _unavailable, _uia_window, _window_failure_text, _UIA_IS_PASSWORD_ID, _PASSWORD_NAME_RE, _password_name_re, _is_password_control, _control_usable, _TYPE_KEYS_SPECIALS, _escape_type_keys, _current_text, foreground_title, foreground_hwnd)  # refactor P1 step 4 (uia_base concern, moved verbatim)
# How many matches uia_find_control reports at most.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
















from .uia_discover import (_descendants, _element_facts, _find_matches, _format_facts, _point_element, describe_point, _focused_element, _focused_control_label, desktop_snapshot, _safe_snapshot, uia_find_control, uia_read_tree)  # refactor P1 step 4 (uia_discover concern, moved verbatim)
from .uia_engine import (ENGINE_CAPABILITIES, ENGINE_NAMES, electron_wall, engines_status, find_control, focus_label, get_engine, read_tree, set_engine_override, reset_engine_overrides, set_window_identity_override, reset_window_identity_override, wall_note)  # GAPS-20 (D-9): one UIA engine interface + Electron-wall detector








# ---------------------------------------------------------------------------
# Safety helpers (v4.4): password refusal, usability, key escaping
# ---------------------------------------------------------------------------

# UIA property id for IsPassword.

# Defence-in-depth companion to the IsPassword check: when the
# property can't be read at all, an Edit whose name says "password"
# is treated as one (audit U4). Word-bounded so "Shipping" and
# "spinner" don't trip the pin alternative.








# pywinauto's type_keys/send_keys parses its input: ^ Ctrl, % Alt,
# + Shift, ~ Enter, ( ) grouping, { } named keys. Literal prose full
# of these ("50% + tax", "Q3 ~final~") must be brace-escaped per
# character so it types literally (audit U5).






# ---------------------------------------------------------------------------
# POINT — identify what lives at a screen point (v5.8)
# ---------------------------------------------------------------------------
# The cursor gets a receipt: a coordinate click (cursor.py /
# control.py) asks what it is ABOUT to click, so its result can name
# the control instead of reporting bare pixels. Everything here is
# best-effort and NEVER raises — identification must never break,
# block or move a click.










# ---------------------------------------------------------------------------
# POST-ACTION PROBE (v5.44, H5) — report what an action CAUSED
# ---------------------------------------------------------------------------
# The browser tools have always reported what a click caused (a
# before/after page probe — core/browser.py's _page_probe /
# _change_sentence); the desktop hands only reported what they DID.
# This section is the desktop equivalent: snapshot the foreground
# window title + the focused control's identity BEFORE an action,
# snapshot again AFTER, and phrase the difference as one sentence
# appended to the action's own result ("The Settings window came
# to front." / "The 'Alerts' checkbox is now on."). A click target's
# toggle/selection state is read through _control_state for the
# same purpose. Where a visual check is the only honest check
# (raw-coordinate clicks have no named target), the before/after
# screen comparison REUSES core/skills_window_control.py's screen_diff
# machinery — its capture seam and its diff threshold/quiet
# constants — instead of a second differ.
#
# When a probed action changes nothing observable, the result says
# so and names the next rung of the bounded retry ladder — UIA by
# name, then OCR text (click_text), then raw coordinates — as
# guidance for the brain's NEXT call. This is result-text guidance
# only: nothing here ever re-clicks on its own.
#
# Failure discipline: every seam is guarded and the probe NEVER
# raises and NEVER blocks an action. When nothing could be
# observed at all (off-Windows, no pywinauto, every read failed —
# or a seam raised, which _safe_snapshot degrades to "unobserved"),
# the suffix is empty and the caller returns its pre-H5 result
# text byte-for-byte.










from .uia_probe import (_toggle_word, _control_state, _state_sentence, _snapshot_change_sentence, no_change_sentence, _screen_image, screen_change_fraction, probe_suffix, _probe_click_suffix, _probe_type_suffix, _effect_present, wait_for_effect)  # refactor P1 step 4 (uia_probe concern, moved verbatim)




















# ---------------------------------------------------------------------------
# EFFECT WAIT (v5.59, HA-4) — wait on the effect, never re-look
# ---------------------------------------------------------------------------
# The old expect path verified the expectation the instant the
# action returned — a dialog still animating in read as "not-met".
# HA-4 waits on the EFFECT instead: given the same expectation
# string verify_expectation understands ("a dialog titled Save As
# appears", "text Saved present"), wait_for_effect watches the
# H5 probe snapshots (foreground title / focus / the snapshot's
# windows/texts keys) until the effect is present, or the deadline
# passes. It NEVER re-searches for the acted-on control by name —
# act-and-re-look is exactly what this replaces: the evidence is
# the world changing, not the target being found again.
#
# A native COM AddAutomationEventHandler subscription was
# evaluated and NOT shipped: pywinauto exposes no stable
# subscription seam, a comtypes event sink needs a Windows STA run
# to prove, and this VM is Linux — shipping an unprovable COM sink
# would be the opposite of the programme's proof discipline. The
# wait below is a bounded poll of the effect (the same snapshots
# the H5 probe already reads); the semantic — wait for the effect,
# bounded, honestly timed out — is what HA-4 asked for, and the
# COM subscription stays a named hardening item for a Windows run.
# The click/type expect paths call wait_for_effect before
# verify_expectation; the verdict text is unchanged, so the wait
# changes WHEN the verdict is taken, never what it says.






# ---------------------------------------------------------------------------
# GROUNDING — set-of-marks (v5.49, programme item 30)
# ---------------------------------------------------------------------------
# The marks table lives HERE, not in core/vision.py: the marks are
# UIA facts first (name / control type / true-pixel rectangle from
# this module's own tree walk), with OCR word boxes merged in only
# for surfaces UIA can't see. vision.py draws the numbered boxes
# on the grounding frame and tools_handlers.py ships this table alongside
# the image; click_by_mark resolves an id back to a rectangle
# centre at click time. The table is process-global state (the
# LAST observation), stamped with the foreground title it was
# taken under so a stale table can be refused honestly instead of
# clicked blindly.

from .uia_marks import (_INTERACTIVE_TYPES, _UNNAMED_MARK_TYPES, _MARKS_CAP, _KEEP, _last_marks, _last_marks_fg, _last_marks_hwnd, _element_rect, _set_last_marks, last_marks, last_marks_foreground, last_marks_foreground_hwnd, _marks_stale_reason, clear_last_marks, _marks_capture_block, _word_rect, merge_ocr_marks, enumerate_marks, control_list_snapshot)  # refactor P1 step 4 (uia_marks concern, moved verbatim)

# Desktop programme, F-06 (layered detection): control types that
# may carry an UNNAMED mark — the interactive set plus Custom
# (custom controls are the "custom target" case). The NAMED layer
# above is unchanged: a named Custom control is still not a named
# mark. Only a nameless, usable, bounded control of these types
# earns an unnamed mark, always with its layer disclosed.





























def click_by_mark(mark_id, button: str = "left", expect: str = "",
                  monitor_backend=None) -> str:
    """Click the centre of one mark from the LAST grounding table.

    The id is resolved against the table the last grounding
    capture (take_screenshot / screen_look with marks, or
    enumerate_marks) recorded — never against a fresh walk, so the
    number the brain picked is the rectangle it saw. At click time
    the table is re-validated: when the foreground window has
    changed since the marks were drawn the table is STALE and the
    click is refused (re-observe first), and the mark's centre
    must still fall inside the current virtual-screen bounds. The
    click itself goes through core/desktop/cursor.py's click path (smooth
    glide, v5.8 receipt, focus note) with this module's H5 probe
    snapshots around it.

    v5.49 (programme item 31): the optional `expect` is verified
    the same way as on uia_click_control / uia_type_text.

    §7 consumers batch 10: after the handle re-proof, the mark
    centre is validated against a FRESH monitor table
    (core/desktop/cursor_monitor): a centre in the gap between staggered
    monitors, or on an unreadable-scale monitor, refuses instead
    of clicking into the void. `monitor_backend` injects the
    monitor-table backend (tests); None means the live backend,
    and an unreadable table skips this gate — the pinned checks
    above stand, and the gate never changes a success result."""
    blocked = _failsafe_block()
    if blocked:
        return blocked
    try:
        wanted = int(mark_id)
    except (TypeError, ValueError):
        return (f"click_by_mark needs a numeric mark id from the last "
                f"grounding table, got '{mark_id}'.")
    button_key = str(button or "left").strip().lower()
    if button_key not in ("left", "right", "middle"):
        return f"Unknown mouse button: '{button}'. Use left, right or middle."
    table = last_marks()
    if not table:
        return ("No grounding marks are on record — the last look "
                "produced none (or none has happened yet). Take a "
                "screenshot or use screen_look first so controls "
                "are numbered, then click_by_mark with an id.")
    mark = next((m for m in table if int(m.get("id") or 0) == wanted), None)
    if mark is None:
        ids = ", ".join(str(m.get("id")) for m in table[:20])
        return (f"Mark {wanted} is not in the last grounding table "
                f"(ids on record: {ids}). Re-observe with a "
                "screenshot or screen_look if the screen changed.")
    # Staleness (F-07): a different foreground window than the one
    # the marks were drawn under means the rectangles describe a
    # window that is no longer in front — refuse honestly. The
    # check is handle-first (a same-title replacement still
    # refuses) with the title rule as fallback when either
    # handle is unknown.
    stale = _marks_stale_reason(wanted)
    if stale is not None:
        return stale
    rect = mark.get("rect")
    try:
        left, top, right, bottom = (int(v) for v in rect)
    except (TypeError, ValueError):
        return f"Mark {wanted} has no usable rectangle on record — re-observe."
    cx, cy = (left + right) // 2, (top + bottom) // 2
    # On-screen re-validation against the CURRENT virtual bounds.
    try:
        from . import cursor as _cursor  # Phase 3 D4d: cursor hub now in core/desktop/; D4b interim closed

        bounds = _cursor._virtual_bounds()
    except Exception:  # noqa: BLE001 — unknown bounds can't refuse
        bounds = None
    if bounds is not None and not (bounds[0] <= cx <= bounds[2]
                                   and bounds[1] <= cy <= bounds[3]):
        return (f"Not clicking mark {wanted} ('{mark.get('name')}'): its "
                f"centre ({cx}, {cy}) is no longer on-screen (virtual "
                f"bounds {bounds}). The screen changed since the "
                "marks were drawn — re-observe and pick a fresh id.")
    # §7 item 1 — stable target handles: the validated mark becomes
    # a session-scoped handle, resolved through the handle engine
    # before dispatch. The pinned staleness + bounds checks above
    # still run first and keep their exact messages; the engine
    # re-proves the same stamp + bounds (agreement is pinned by
    # test_s7_target_handles) and the click result names the
    # handle id, so the dispatch is auditable against the
    # consented target. A handle refusal here is fail-closed.
    try:
        from . import target_handles as _th  # Phase 3 S7a: target_handles moved to core/desktop/; Phase 3 D4b: hub now in core/desktop/

        _handle = _th.mint_mark(
            mark, {"hwnd": last_marks_foreground_hwnd(),
                   "title": last_marks_foreground()})
        _resolved = _th.resolve(_handle["id"], {
            "foreground": {"hwnd": foreground_hwnd(),
                           "title": foreground_title()},
            "bounds": bounds})
    except Exception:  # noqa: BLE001 — a broken handle layer must
        _resolved = {"ok": True}  # never block an already-validated
        _handle = {"id": "unavailable"}  # click; the pinned checks
    if not _resolved.get("ok"):  # above already refused stale marks
        refusal = (_resolved.get("refusal") or {}).get("reason") or \
            "target-handle resolution refused the click."
        return refusal
    handle_id = _handle["id"]
    # §7 item-6 monitor validation (consumers batch 10): the mark
    # centre must sit on a monitor with a readable scale in a
    # FRESH table — a point inside the virtual union but in the
    # gap between staggered monitors, or on an unreadable-scale
    # monitor, refuses here instead of clicking into the void.
    # An unreadable table skips this gate (the pinned checks
    # above stand); the gate never changes a success result.
    try:
        from . import cursor_monitor as _cm  # Phase 3 S7a-2: moved to core/desktop/; Phase 3 D4b: hub now in core/desktop/

        _mon, _status, _note = _cm.validate_point(
            cx, cy, backend=monitor_backend)
    except Exception:  # noqa: BLE001 — a broken monitor layer must
        _status, _note = "unvalidated", ""  # never block an
        # already-validated click when no table can be read.
    if _status == "refused":
        return (f"Not clicking mark {wanted} ('{mark.get('name')}'): "
                f"{_note}")
    from . import cursor as _cursor  # Phase 3 D4d: cursor hub now in core/desktop/; D4b interim closed

    snap_before = _safe_snapshot()
    result = _cursor.click_cursor(button=button_key, count=1, x=cx, y=cy)
    lowered = result.lower()
    if lowered.startswith(("could not", "move aborted", "cursor control unavailable")) \
            or "failsafe has tripped" in lowered:
        return (f"click_by_mark {wanted} ('{mark.get('name')}') did not "
                f"land: {result}")
    # Command Deck Phase F: the numeric grounding rectangle is the
    # overlay's target lock, and the mark's name is the chip. This
    # fires AFTER the cursor path's generic chip, so the named chip
    # wins. Never raises, never affects the click result.
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D4b: file now in core/desktop/; state_bus_producers stays flat in core/

        _prod.emit_target_rect(left, top, right - left, bottom - top,
                               role=str(mark.get("control_type") or ""),
                               name=str(mark.get("name") or ""))
        _prod.emit_agent_action("click", label=str(mark.get("name") or ""),
                                x=cx, y=cy)
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
    suffix = probe_suffix(snap_before, _safe_snapshot(), None, None, "coords")
    if "Focus moved to" in result and "came to front" in suffix:
        suffix = ""  # the cursor receipt already told that story
    base = (f"Clicked mark {wanted} — '{mark.get('name')}' "
            f"({mark.get('control_type')}) at ({cx}, {cy}), the centre "
            f"of its grounding rectangle ({left},{top})\u2013({right},{bottom}). "
            f"[target handle {handle_id}] "
            + result + suffix)
    if str(expect or "").strip():
        verdict, reason = verify_expectation(
            str(expect), snap_before, _safe_snapshot(), base)
        base += f" Expectation '{expect}': {verdict} — {reason}"
    else:
        base = _with_dispatch_trailer(base)
    return base


# ---------------------------------------------------------------------------
# EXPECT ENGINE (v5.49, programme item 31) — verify_expectation
# ---------------------------------------------------------------------------
# The planner (item 31, builder A) drives multi-step goals and
# needs a grounded verdict on each step's declared expectation —
# "a dialog titled Save As appears", "text Saved present" — from
# the before/after desktop_snapshot() dicts plus the step's own
# result. This engine is that judge. Its discipline is the H5
# discipline turned around: a verdict of "met" requires positive
# evidence IN the after snapshot (or the result text, the OCR
# fallback); anything it cannot ground is "unverifiable", NEVER a
# guessed "met". Snapshots are read for the keys desktop_snapshot
# really carries ({"fg", "focus"}) and, opportunistically, the
# richer keys a caller may add ("windows", "texts", "tree_hash",
# "ocr_text") — absent keys are simply absent evidence.


from .uia_verify import (_expect_corpus, _corpus_contains, _parse_expectation, verify_expectation)  # refactor P1 step 4 (uia_verify concern, moved verbatim)








# ---------------------------------------------------------------------------
# FIND — report controls by name
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# CLICK — activate a control by name
# ---------------------------------------------------------------------------


from .uia_click import (_DISPATCH_TRAILER, _with_dispatch_trailer, _pick_click_target, uia_click_control, _emit_overlay_lock, _uia_click_control_impl)  # refactor P1 step 4 (uia_click concern, moved verbatim)












# ---------------------------------------------------------------------------
# TYPE — put text into a named edit control (text is never echoed back)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HA-1 TYPING LADDER (v5.54, Wave 0 — wave 10 HA-1 + plan §8 S-3)
# ---------------------------------------------------------------------------
# ui_type_text delivers text down a ladder, safest rung first:
#   1. FOCUS ASSERTION — the password refusals bind HERE (§8 S-3:
#      IsPassword/denylist at rung 1, before any rung runs), then
#      set_focus() and a positive check that keyboard focus is on
#      the target; a positively-identified different focused
#      element refuses the type (keystrokes would land elsewhere).
#   2. UIA VALUE — ValuePattern.SetValue via pywinauto's
#      set_edit_text (which itself prefers the TextPattern route
#      where a control offers one), then the raw pattern. No keys
#      are pressed, so no layout/dead-key can rewrite the text.
#   3. SENDINPUT — real keystrokes via Win32 SendInput with
#      KEYEVENTF_UNICODE (every character as UTF-16 code units).
#      Where SendInput does not exist (non-Windows hosts, test
#      rigs), pywinauto's type_keys remains the mechanics — same
#      rung, degraded engine, grammar-escaped as before (U5).
#   4. CLIPBOARD — BUILT (v5.59, HA-6). Runs ONLY when the keystroke
#      rungs landed nothing at all (read-back still equals the
#      pre-type value — the silent-drop signature): the clipboard
#      is saved in ALL byte-copyable formats, the text is placed
#      marked excluded from clipboard history, Ctrl+V is sent, and
#      the saved clipboard is restored and dropped in a finally
#      (core/desktop/desktop.py's clipboard_paste — the discipline IS
#      the item, and it never runs on a password field: rung 1
#      refused those before any rung ran).
#   5. MANDATORY READ-BACK — after any rung, the control's value is
#      re-read and compared in memory (typed text is NEVER echoed
#      into a result string — the standing rule). Value-rung
#      mismatch = the typing did not land, reported as failure.
#      Keystroke-rung mismatch is advisory (fields may legitimately
#      reformat): the result withholds word-for-word confirmation.
# The failsafe is consulted before EVERY rung (§8 S-3).

from .uia_type import (_UIA_HAS_KEYBOARD_FOCUS_PID, _same_element, _focus_assertion, _focus_assertion_once, _values_match, uia_type_text, _emit_overlay_type, _uia_type_text_impl)  # refactor P1 step 4 (uia_type concern, moved verbatim)










# ---------------------------------------------------------------------------
# D-04 — BATCHED TYPING REVALIDATION (desktop programme)
# ---------------------------------------------------------------------------
# Batched raw typing used to send every 200-event block blindly:
# a target window that changed mid-type received the remaining
# keystrokes. Both SendInput engines now send through
# _send_in_blocks, which revalidates the foreground window's
# identity (HWND + PID) before EACH block and aborts the rest on
# a change, reporting exactly how many characters were sent.
# An abort is terminal for the caller: it must NOT fall back to
# retyping the payload (that would type into the new window).
# D-05 (this group): the under-delivery fallback is no longer
# blind either — SendInput's accepted-event count is captured
# per block (_block_char_progress), and the caller reads back
# before any retry (_retry_plan), retyping only the missing
# remainder, never the full payload on top of a partial land.


from .uia_send import (_SendOutcome, _foreground_identity, _target_still_current, _chunk_events, _block_char_progress, _send_in_blocks, _partial_type_report, _retry_plan, _sendinput_type, _pydirectinput_typewrite_fn, _scancode_type)  # refactor P1 step 4 (uia_send concern, moved verbatim)


















# ---------------------------------------------------------------------------
# HA-2 (v5.59) — scan-code SendInput rung
# ---------------------------------------------------------------------------
# Some targets silently drop VK injection (notably games and
# DirectX apps): rung 3's SendInput "succeeds" and nothing lands.
# The rung below is the fallback for exactly that signature —
# the caller runs it ONLY when rung 3's read-back equals the
# pre-type value (nothing landed at all), never on top of a
# partial/reformatted land (that would double-type).
#
# Primary engine: VYUK's own ctypes scan-code SendInput — each
# character's VK comes from VkKeyScanW, its scan code from
# MapVirtualKeyW, and the event carries KEYEVENTF_SCANCODE, so
# the target sees a hardware scan code, not a virtual key.
# Optional engine: PyDirectInput (MIT by classifier, lazy, never
# required, never in requirements.txt) — tried first when already
# installed, because it exists precisely for this class of
# target; its absence changes nothing, the ctypes engine runs.
# A character VkKeyScanW cannot map fails the whole rung (False)
# rather than typing a wrong character. Failsafe is the caller's
# job (checked before this rung, like every rung). Never raises.











# ---------------------------------------------------------------------------
# TREE — read a window's control tree (depth- and count-bounded)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Registration — the parent build calls this from core/funnel/tools_handlers_pack_core2.py (_register_uia_tools; Phase 5 docs-only rename, wiring unchanged)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every UI Automation pack tool on a ToolRegistry-like object."""
    registry.register(
        name="uia_find_control",
        description="Find UI controls by name in the foreground window (or a window whose title contains window_title) using Windows UI Automation, and REPORT the matches — each with its name, class, control type and on-screen rectangle — without touching anything. Use this to see exactly what can be clicked or typed into before acting. Windows only, needs pywinauto.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Part of the control's visible label to search for, e.g. 'Save' or 'Search'"},
                "window_title": {"type": "string", "description": "Optional part of a window title to search in; empty means the foreground window"},
                "control_type": {"type": "string", "description": "Optional control type filter, e.g. 'Button', 'Edit', 'CheckBox'"},
            },
            "required": ["name"],
        },
        handler=uia_find_control,
    )
    registry.register(
        name="uia_click_control",
        description="Click a UI control by its visible name using Windows UI Automation — more reliable than pixel coordinates. Searches the foreground window (or a window whose title contains window_title), skips disabled or hidden matches, and clicks the first usable control whose name contains the given text. The result always reports how many controls matched, which one was clicked (with its rectangle), and any that were skipped; if no match is usable, nothing is clicked. Windows only, needs pywinauto.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Part of the control's visible label to click, e.g. 'Save'"},
                "window_title": {"type": "string", "description": "Optional part of a window title to search in; empty means the foreground window"},
                "control_type": {"type": "string", "description": "Optional control type filter, e.g. 'Button'"},
                "expect": {"type": "string", "description": "Optional expectation to verify after the click, e.g. 'a dialog titled Save As appears' or 'text Saved present' — the result appends a grounded verdict: met, not-met or unverifiable (v5.49)"},
            },
            "required": ["name"],
        },
        handler=uia_click_control,
    )
    registry.register(
        name="uia_type_text",
        description="Type text into a named text field (search box, form field, editor) using Windows UI Automation: finds the field by its label/name in the foreground window (or a window whose title contains window_title), focuses it and sets its text. Prefer this over raw keystrokes when a field has a name. The text is typed but never read back. HARD RULE: password fields are refused, always — passwords are the user's to type. clear_first=false appends instead of replacing, on every path. Windows only, needs pywinauto.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The field's label or name, e.g. 'Search' or 'File name'"},
                "text": {"type": "string", "description": "The text to put into the field"},
                "window_title": {"type": "string", "description": "Optional part of a window title to search in; empty means the foreground window"},
                "clear_first": {"type": "boolean", "description": "Replace existing text (true, default) rather than appending where the control supports it"},
                "expect": {"type": "string", "description": "Optional expectation to verify after typing, e.g. 'text Saved present' — the result appends a grounded verdict: met, not-met or unverifiable (v5.49)"},
            },
            "required": ["name", "text"],
        },
        handler=uia_type_text,
    )
    registry.register(
        name="uia_read_tree",
        description="Read the UI Automation control tree of the foreground window (or a window whose title contains window_title): an indented outline of controls with names and types, bounded by max_depth (default 3) and max_nodes (default 80). Use to explore an unfamiliar window before acting on it. Windows only, needs pywinauto.",
        parameters={
            "type": "object",
            "properties": {
                "window_title": {"type": "string", "description": "Optional part of a window title; empty means the foreground window"},
                "max_depth": {"type": "integer", "description": "How many levels deep to read (default 3, max 8)"},
                "max_nodes": {"type": "integer", "description": "Maximum controls to list (default 80, max 300)"},
            },
            "required": [],
        },
        handler=uia_read_tree,
    )

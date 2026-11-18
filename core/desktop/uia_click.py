"""uia_click concern extracted from core/desktop/uia.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/desktop/uia."""

from .uia_base import (_SCAN_CAP, _MATCH_CAP, _is_windows, _routed_backend, _truncate, _failsafe_block, _unavailable, _uia_window, _window_failure_text, _UIA_IS_PASSWORD_ID, _PASSWORD_NAME_RE, _password_name_re, _is_password_control, _control_usable, _TYPE_KEYS_SPECIALS, _escape_type_keys, _current_text, foreground_title, foreground_hwnd)
from .uia_discover import (_descendants, _element_facts, _find_matches, _format_facts, _point_element, describe_point, _focused_element, _focused_control_label, desktop_snapshot, _safe_snapshot, uia_find_control, uia_read_tree)
from .uia_probe import (_toggle_word, _control_state, _state_sentence, _snapshot_change_sentence, no_change_sentence, _screen_image, screen_change_fraction, probe_suffix, _probe_click_suffix, _probe_type_suffix, _effect_present, wait_for_effect)
from .uia_marks import (_INTERACTIVE_TYPES, _UNNAMED_MARK_TYPES, _MARKS_CAP, _KEEP, _last_marks, _last_marks_fg, _last_marks_hwnd, _element_rect, _set_last_marks, last_marks, last_marks_foreground, last_marks_foreground_hwnd, _marks_stale_reason, clear_last_marks, _marks_capture_block, _word_rect, merge_ocr_marks, enumerate_marks, control_list_snapshot)
from .uia_verify import (_expect_corpus, _corpus_contains, _parse_expectation, verify_expectation)


_DISPATCH_TRAILER = (" (Click dispatched; landing not verified — "
                     "no expectation was given.)")


def _with_dispatch_trailer(result: str) -> str:
    """F-08: dispatch ≠ verified outcome. A click result that
    reports a dispatch ("Clicked …" / "Invoked …") with NO
    expectation attached gains an explicit trailer saying the
    landing was not verified — it must never read as a verified
    success. Refusals and failures ("Could not …", "No control
    …", "Found … but none could be clicked") dispatch nothing
    and gain nothing. The expect path never calls this: its
    verdict trailer (met / not-met / unverifiable) IS the
    outcome state."""
    text = str(result or "")
    if text.startswith(("Clicked ", "Invoked ")):
        return text + _DISPATCH_TRAILER
    return text


def _pick_click_target(matches, name: str, desc: str,
                       truncated: bool = False):
    """D-01 unified click policy, shared by uia_click_control and
    control_plus.click_element so the same name resolves the same
    way regardless of which tool the model calls.

    Returns ((el, facts), note) or (None, refusal):
    - exact-name matches win outright over substring matches;
    - the winning tier with several usable candidates REFUSES
      with a candidate listing (never guesses);
    - the winning tier with no usable candidate refuses with the
      skipped reasons (nothing is clicked);
    - a single usable candidate clicks with a note naming the
      tier, the unclicked substring matches, the skipped
      unusable ones, and — when the scan was cut short — the
      fact that more matches may exist deeper.
    `matches` are (wrapper, facts) substring pairs as
    _find_matches returns them; the tier split is by full-name
    equality, case-insensitive."""
    needle = str(name).strip().lower()
    exact = [(el, f) for el, f in matches
             if str(f.get("name") or "").strip().lower() == needle]
    substr = [(el, f) for el, f in matches
              if str(f.get("name") or "").strip().lower() != needle]
    tier, tier_kind = (exact, "exact") if exact else (substr, "partial")
    usable, skipped = [], []
    for el, facts in tier:
        ok, reason = _control_usable(el, facts)
        if ok:
            usable.append((el, facts))
        else:
            skipped.append(f"'{facts['name']}' ({reason})")
    if not usable:
        return None, (
            f"Found {len(tier)} control(s) matching '{name}' in {desc}, "
            "but none could be clicked: " + "; ".join(skipped)
            + ". Nothing was clicked."
        )
    if len(usable) > 1:
        listing = "\n".join(
            f"• '{f['name']}' ({f['control_type']}) at {f['rect']}"
            for _el, f in usable)
        return None, (
            f"Found {len(usable)} controls in {desc} that could be "
            f"'{name}' — I won't guess which one to click. Candidates:\n"
            f"{listing}\n"
            "Tell me which one, e.g. by a more specific name."
        )
    el, facts = usable[0]
    note_parts = []
    if tier_kind == "exact":
        note_parts.append("exact name match")
        if substr:
            others = ", ".join(f"'{f['name']}'" for _e, f in substr[:3])
            extra = (f" (+{len(substr) - 3} more)"
                     if len(substr) > 3 else "")
            note_parts.append(
                f"{len(substr)} other control(s) merely contain "
                f"'{name}' and were not clicked: {others}{extra}")
    else:
        note_parts.append(
            f"the only match was this partial-name one — no control "
            f"is named exactly '{name}'")
    if skipped:
        note_parts.append(
            f"skipped {len(skipped)} unusable: {'; '.join(skipped)}")
    if truncated:
        note_parts.append(
            "the scan stopped early, so more matches may exist "
            "deeper in the window")
    note = f" ({'; '.join(note_parts)})" if note_parts else ""
    return (el, facts), note


def uia_click_control(name: str, window_title: str = "", control_type: str = "",
                      expect: str = "") -> str:
    """Click a named control; optionally verify an expectation after.

    v5.49 (programme item 31): the optional `expect` threads an
    expectation string ("a dialog titled Save As appears", "text
    Saved present") through the action: the world is snapshotted
    before and after via the H5 probe seam and verify_expectation()
    appends its verdict (met / not-met / unverifiable) to the
    result. With no expect the result is byte-identical to before.
    The legacy contract this extends: uia_click_control's result
    previously carried only the H5 probe sentence."""
    if not str(expect or "").strip():
        return _with_dispatch_trailer(
            _uia_click_control_impl(name, window_title, control_type))
    before = _safe_snapshot()
    result = _uia_click_control_impl(name, window_title, control_type)
    # HA-4 (v5.59): wait on the EFFECT before judging it — the
    # verdict text below is unchanged, only its timing moves.
    wait_for_effect(str(expect))
    verdict, reason = verify_expectation(
        str(expect), before, _safe_snapshot(), result)
    return result + f" Expectation '{expect}': {verdict} — {reason}"


def _emit_overlay_lock(el, facts) -> None:
    """Command Deck Phase F: TargetRect + named chip for a UIA click.

    The rectangle comes from ``_element_rect`` (numeric, true
    pixels); when it cannot be read, the named chip still fires
    and no lock is claimed. Never raises.
    """
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D2c: file now in core/desktop/

        rect = _element_rect(el)
        if rect is not None:
            left, top, right, bottom = rect
            _prod.emit_target_rect(left, top, right - left, bottom - top,
                                   role=str(facts.get("control_type") or ""),
                                   name=str(facts.get("name") or ""))
        _prod.emit_agent_action("click", label=str(facts.get("name") or ""))
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass


def _uia_click_control_impl(name: str, window_title: str = "", control_type: str = "") -> str:
    """Click a UI control whose name contains `name`, via UIA.

    Disclosure (v4.4, audit U3 — mirrors browser_click): the result
    always says how many controls matched, which one was clicked
    (name + rectangle), and which matches were skipped as unusable.
    Resolution follows the unified D-01 policy (see
    _pick_click_target): an exact name beats substring matches,
    and an ambiguous winning tier refuses rather than guessing.
    Disabled or hidden/zero-size matches are skipped with
    disclosure; if none is usable, nothing is clicked.
    """
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.uia_click_control(
            name, window_title=window_title, control_type=control_type)
    if not name or not str(name).strip():
        return "No control name given. Tell me the label of the control to click."
    blocked = _failsafe_block()
    if blocked:
        return blocked
    unavailable = _unavailable()
    if unavailable:
        return unavailable
    try:
        win, desc = _uia_window(str(window_title or ""))
    except Exception as e:  # noqa: BLE001
        return f"Could not connect to the target window: {e}"
    matches, _scanned, truncated, failure = _find_matches(win, name, control_type)
    if failure is not None:
        return f"Could not search {desc} for '{name}': {failure}"
    if not matches:
        return f"No control with a name containing '{name}' was found in {desc}."
    chosen, note_or_refusal = _pick_click_target(
        matches, name, desc, truncated=truncated)
    if chosen is None:
        return note_or_refusal
    el, facts = chosen
    note = note_or_refusal
    # H5 probe (v5.44): snapshot the world BEFORE the click lands —
    # the target's state plus foreground/focus — so the result can
    # report what the click CAUSED, not just what was dispatched.
    before_state = _control_state(el)
    snap_before = _safe_snapshot()
    try:
        el.click_input()
        base = (
            f"Clicked '{facts['name']}' ({facts['control_type']}) at "
            f"{facts['rect']} in {desc}.{note}"
        )
        _emit_overlay_lock(el, facts)
        return base + _probe_click_suffix(
            snap_before, _safe_snapshot(), before_state,
            _control_state(el), facts, "uia")
    except Exception:  # noqa: BLE001 — fall back to the UIA Invoke pattern
        pass
    try:
        el.iface_invoke.Invoke()
        base = (
            f"Invoked '{facts['name']}' ({facts['control_type']}) at "
            f"{facts['rect']} in {desc}.{note}"
        )
        _emit_overlay_lock(el, facts)
        return base + _probe_click_suffix(
            snap_before, _safe_snapshot(), before_state,
            _control_state(el), facts, "uia")
    except Exception as e:  # noqa: BLE001
        return f"Found '{facts['name']}' in {desc}, but could not click it: {e}"

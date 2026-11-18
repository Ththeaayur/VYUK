"""uia_type concern extracted from core/desktop/uia.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/desktop/uia."""

from .uia_base import (_SCAN_CAP, _MATCH_CAP, _is_windows, _routed_backend, _truncate, _failsafe_block, _unavailable, _uia_window, _window_failure_text, _UIA_IS_PASSWORD_ID, _PASSWORD_NAME_RE, _password_name_re, _is_password_control, _control_usable, _TYPE_KEYS_SPECIALS, _escape_type_keys, _current_text, foreground_title, foreground_hwnd)
from .uia_discover import (_descendants, _element_facts, _find_matches, _format_facts, _point_element, describe_point, _focused_element, _focused_control_label, desktop_snapshot, _safe_snapshot, uia_find_control, uia_read_tree)
from .uia_probe import (_toggle_word, _control_state, _state_sentence, _snapshot_change_sentence, no_change_sentence, _screen_image, screen_change_fraction, probe_suffix, _probe_click_suffix, _probe_type_suffix, _effect_present, wait_for_effect)
from .uia_verify import (_expect_corpus, _corpus_contains, _parse_expectation, verify_expectation)
from .uia_send import (_SendOutcome, _foreground_identity, _target_still_current, _chunk_events, _block_char_progress, _send_in_blocks, _partial_type_report, _retry_plan, _sendinput_type, _pydirectinput_typewrite_fn, _scancode_type)


_UIA_HAS_KEYBOARD_FOCUS_PID = 30008  # UIA_HasKeyboardFocusPropertyId


def _same_element(a, b):
    """Identity compare for two UIA wrappers: True / False / None
    (None = no basis available). Runtime id first, then the
    (name, control type, rectangle) facts tuple. Never raises."""
    for el in (a, b):
        if el is None:
            return None
    try:
        ra = a.element_info.element.GetRuntimeId()
        rb = b.element_info.element.GetRuntimeId()
        if ra is not None and rb is not None:
            return list(ra) == list(rb)
    except Exception:  # noqa: BLE001 — fall through to facts
        pass
    try:
        fa, fb = _element_facts(a), _element_facts(b)
        ka = (fa["name"], fa["control_type"], fa["rect"])
        kb = (fb["name"], fb["control_type"], fb["rect"])
        if ka == ("", "", "") and kb == ("", "", ""):
            return None
        return ka == kb
    except Exception:  # noqa: BLE001
        return None


def _focus_assertion(el):
    """Rung 1's check: (state, focused_label) where state is
    'confirmed' (focus positively on the target), 'elsewhere'
    (focus positively on a DIFFERENT element) or 'unknown'
    (cannot be determined — the read-back rung then carries the
    verification). One settle-and-recheck when the first verdict
    is 'elsewhere': focus events can lag a set_focus() call.
    Never raises."""
    for attempt in (0, 1):
        state, label = _focus_assertion_once(el)
        if state != "elsewhere" or attempt == 1:
            return state, label
        try:
            import time

            time.sleep(0.06)
        except Exception:  # noqa: BLE001
            return state, label
    return state, label


def _focus_assertion_once(el):
    try:
        has = el.element_info.element.GetCurrentPropertyValue(
            _UIA_HAS_KEYBOARD_FOCUS_PID)
    except Exception:  # noqa: BLE001
        has = None
    focused = _focused_element()
    if focused is not None:
        same = _same_element(el, focused)
        if same is True:
            return "confirmed", ""
        if same is False:
            ff = _element_facts(focused)
            label = (f"'{ff['name']}' ({ff['control_type']})"
                     if ff["name"] else
                     f"a {ff['control_type'] or 'different'} control")
            return "elsewhere", label
    if has is True:
        return "confirmed", ""
    return "unknown", ""


def _values_match(readback: str, payload: str) -> bool:
    """The read-back compare (rung 5): exact, tolerating only
    trailing whitespace a control may add or trim."""
    if readback == payload:
        return True
    return readback.rstrip(" \t\r\n") == payload.rstrip(" \t\r\n")


def uia_type_text(name: str, text: str, window_title: str = "", clear_first: bool = True,
                  expect: str = "") -> str:
    """Type into a named field; optionally verify an expectation
    after (v5.49, programme item 31 — same threading as
    uia_click_control: before/after probe snapshots plus
    verify_expectation's verdict appended to the result; with no
    expect the result is byte-identical to before)."""
    if not str(expect or "").strip():
        result = _uia_type_text_impl(name, text, window_title, clear_first)
        _emit_overlay_type(result, text)
        return result
    before = _safe_snapshot()
    result = _uia_type_text_impl(name, text, window_title, clear_first)
    _emit_overlay_type(result, text)
    # HA-4 (v5.59): wait on the EFFECT before judging it — the
    # verdict text below is unchanged, only its timing moves.
    wait_for_effect(str(expect))
    verdict, reason = verify_expectation(
        str(expect), before, _safe_snapshot(), result)
    return result + f" Expectation '{expect}': {verdict} — {reason}"


def _emit_overlay_type(result, text) -> None:
    """Command Deck Phase F: type chip for a successful UIA type.

    Only results starting with "Typed " count as success — every
    refusal, partial, and failure return starts differently, so no
    chip is claimed for text that did not land. The chip carries
    a character COUNT only, never the text. Password fields refuse
    before typing (see the impl), so a password flag never arises
    here; it stays False rather than guessed.
    """
    try:
        if not str(result or "").startswith("Typed "):
            return
        from .. import state_bus_producers as _prod  # Phase 3 D2c: file now in core/desktop/

        _prod.emit_agent_action("type", chars=len(str(text or "")),
                                password=False)
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass


def _uia_type_text_impl(name: str, text: str, window_title: str = "", clear_first: bool = True) -> str:
    """Type text into the named edit control, via Windows UI Automation.

    Delivery is the HA-1 ladder (v5.54 — see the ladder block
    above, extended v5.59): focus assertion (password refusals
    bound at rung 1) → UIA Value → SendInput keystrokes →
    scan-code keystrokes (HA-2) → clipboard paste (HA-6) →
    mandatory read-back before any success claim. The fallback
    rungs run only when nothing landed at all, so nothing is
    typed twice. Prefers an Edit/Document control when several
    controls share the name. The text itself is never included in
    the result string — it may be sensitive (a password, a
    private note).
    """
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.uia_type_text(
            name, text, window_title=window_title,
            clear_first=clear_first)
    if not name or not str(name).strip():
        return "No control name given. Tell me which field to type into."
    if text is None:
        text = ""
    text = str(text)
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
    matches, _scanned, truncated, failure = _find_matches(win, name)
    if failure is not None:
        return f"Could not search {desc} for '{name}': {failure}"
    # D-02: the scan-truncation flag is NOT discarded here. A
    # truncated scan means the window holds controls that were
    # never examined — the field found (or not found) may not be
    # the one meant, and keystrokes into a wrong field silently
    # corrupt data. Refuse with an actionable path instead of
    # typing into a possibly-wrong control.
    if truncated:
        if matches:
            return (
                f"I stopped scanning {desc} early — the window holds "
                f"more controls than I examined — so I can't be sure "
                f"the field I found is the one you mean by '{name}'. "
                f"I didn't type. Name the field more specifically, or "
                f"narrow the window with window_title, and try again."
            )
        return (
            f"I stopped scanning {desc} early — the window holds "
            f"more controls than I examined — so a field named "
            f"'{name}' may exist deeper in the window that I never "
            f"saw. I didn't type. Name the field more specifically, "
            f"or narrow the window with window_title, and try again."
        )
    if not matches:
        return f"No control with a name containing '{name}' was found in {desc}."
    # Prefer a text-entry control when the name matches several things.
    chosen = None
    for el, facts in matches:
        if facts["control_type"] in ("Edit", "Document", "ComboBox"):
            chosen = (el, facts)
            break
    if chosen is None:
        # Never spray keystrokes at a non-text control (audit U6):
        # after a best-effort focus they would land in whatever
        # actually has focus, while the result claimed this control.
        first = matches[0][1]
        return (
            f"'{first['name']}' is a {first['control_type'] or 'non-text'} "
            "control, not a text field — I won't type into it. "
            "Name the field you mean, or use uia_find_control to see "
            "the text fields in the window."
        )
    el, facts = chosen
    # HARD RULE (v4.4, audit U4 — the same rule the browser pack
    # enforces): never type into a password field. Refuse outright
    # when UIA says the control is a password control; when the
    # property can't be determined, an Edit NAMED like a password
    # field is refused too, worded honestly.
    # v5.45 (H20): the IsPassword verdict comes through the ONE
    # shared helper in core/input_guard.py (element_is_password,
    # which wraps _is_password_control above) — same seam every
    # typing path now uses; the target-element check here stays
    # the strongest form (it judges the field being typed INTO,
    # not merely the focused one).
    try:
        from .. import input_guard  # Phase 3 D2c: file now in core/desktop/

        _verdict = input_guard.element_is_password(el)
        password_state = (True if _verdict is True else
                          False if _verdict is False else None)
    except Exception:  # noqa: BLE001 — fall back to the local seam
        password_state = _is_password_control(el)
    if password_state is True:
        return (
            "That's a password field — passwords are yours to type, "
            "I won't. Everything else in the window I can fill for you."
        )
    if (
        password_state is None
        and facts["control_type"] == "Edit"
        and _password_name_re().search(facts["name"])
    ):
        return (
            f"'{facts['name']}' looks like a password field and I can't "
            "verify that it isn't — I won't type into it. Passwords are "
            "yours to type."
        )
    count = len(text)
    # H5 probe (v5.44): the field's current value (read for the
    # probe only — never echoed) and the foreground/focus snapshot,
    # taken BEFORE typing so the result can report the effect.
    value_before = _current_text(el)
    snap_before = _safe_snapshot()
    # ── HA-1 LADDER, rung 1: FOCUS ASSERTION (v5.54) ────────────
    # The password refusals above already bound at this rung (§8
    # S-3). Focus the target, then positively verify where
    # keyboard focus sits: a positively-identified DIFFERENT
    # focused element refuses the type outright — keystrokes
    # would land in the wrong field while the result claimed
    # this one. 'unknown' proceeds; rung 5's read-back carries
    # the verification there.
    try:
        el.set_focus()
        # v5.48 (programme item 22): a landed UIA focus is a focus
        # decision — record the focus expectation (whatever the
        # foreground actually is now) for later type_text /
        # press_hotkey calls, like the other focus paths.
        try:
            from .control import note_expected_foreground  # Phase 3 D4a: control hub now in core/desktop/; D2c interim closed

            note_expected_foreground()
        except Exception:  # noqa: BLE001 — best effort
            pass
    except Exception:  # noqa: BLE001 — focusing is best-effort
        pass
    focus_state, focus_label = _focus_assertion(el)
    if focus_state == "elsewhere":
        return (
            f"I tried to focus '{facts['name']}' in {desc}, but "
            f"keyboard focus is on {focus_label} — typing now "
            "would land in the wrong field, so I didn't type."
        )
    # clear_first is honoured on EVERY path (audit U6): the value
    # paths replace the control's whole content, so for append mode
    # the existing content is read first and the new text added.
    payload = text
    if not clear_first:
        existing = _current_text(el)
        if existing:
            payload = existing + text
    base = (
        f"Typed {count} character(s) into '{facts['name']}' "
        f"({facts['control_type']}) in {desc}."
    )
    # ── Rung 2: UIA VALUE (set_edit_text, then raw ValuePattern) ─
    for setter in (
        lambda: el.set_edit_text(payload),
        lambda: el.iface_value.SetValue(payload),
    ):
        blocked = _failsafe_block()
        if blocked:
            return blocked
        try:
            setter()
        except Exception:  # noqa: BLE001 — next rung
            continue
        # ── Rung 5: MANDATORY READ-BACK (in-memory compare only;
        # the typed text is never echoed into a result). ────────
        readback = _current_text(el)
        if _values_match(readback, payload):
            return base + _probe_type_suffix(
                snap_before, _safe_snapshot(), value_before,
                readback, facts, verified=True)
        return (
            f"I set the value of '{facts['name']}' in {desc}, but "
            "the read-back does not match the text I sent — the "
            "typing did not land as written, and I won't claim "
            "it did."
        )
    # ── Rung 3: SENDINPUT real keystrokes (KEYEVENTF_UNICODE);
    # pywinauto type_keys is the fallback engine where SendInput
    # does not exist. ─────────────────────────────────────────────
    blocked = _failsafe_block()
    if blocked:
        return blocked
    identity_before = _foreground_identity()
    outcome = _sendinput_type(text, clear_first=clear_first)
    if outcome.aborted:
        # D-04: the target changed mid-send. The remaining text is
        # NOT retried here — retyping it would land in the new
        # window. The partial report is the honest outcome.
        return _partial_type_report(facts, desc, outcome.chars_sent, count)
    if not outcome.completed:
        # D-05: a partial or failed SendInput must NEVER fall
        # through to a blind full-payload retype — in append
        # mode that duplicates the landed prefix. First,
        # revalidate the target (the D-04 identity, applied to
        # the fallback too): a changed target means no retry
        # at all. Then read back BEFORE any retry and let the
        # field's actual content choose: landed → verify and
        # stop; clean prefix → retype only the remainder;
        # still pre-type → the pre-existing full fallback is
        # safe; anything else → report honestly, no guess.
        if not _target_still_current(identity_before):
            return (
                f"Found '{facts['name']}' in {desc}, but the target "
                f"changed while typing — keystrokes reported "
                f"{outcome.chars_sent} of {count} character(s) sent, "
                f"and I did not retype the rest into the new "
                f"window. Check the field before continuing."
            )
        readback_pre = _current_text(el)
        plan_kind, plan_arg = _retry_plan(
            readback_pre, value_before, payload)
        if plan_kind == "landed":
            return (
                f"Typed {count} character(s) into '{facts['name']}' "
                f"({facts['control_type']}) in {desc} (as keystrokes)."
                + _probe_type_suffix(
                    snap_before, _safe_snapshot(), value_before,
                    readback_pre, facts, verified=True)
            )
        if plan_kind == "remainder":
            try:
                # Append at the caret, which sits after the
                # landed prefix; no clear — clearing would wipe
                # the prefix this retry is preserving.
                el.type_keys(_escape_type_keys(plan_arg),
                             with_spaces=True, set_focus=False)
            except Exception:  # noqa: BLE001
                return _partial_type_report(
                    facts, desc, outcome.chars_sent, count)
        elif plan_kind == "full":
            try:
                if clear_first:
                    el.type_keys("^a{BACKSPACE}", set_focus=True)
                # Escape pywinauto's key grammar so literal prose
                # types literally (audit U5); the reported count
                # stays the number of characters of the user's
                # text — which is exactly what the escaped input
                # delivers.
                el.type_keys(_escape_type_keys(text), with_spaces=True,
                             set_focus=False)
            except Exception as e:  # noqa: BLE001
                return (f"Found '{facts['name']}' in {desc}, but could "
                        f"not type into it: {e}")
        else:  # "report" — unexpected field content, never guess
            return (
                f"Keystrokes reported {outcome.chars_sent} of {count} "
                f"character(s) sent into '{facts['name']}' in {desc}, "
                f"but the field does not hold the text I sent (it "
                f"may have been reformatted as it was typed), so I "
                f"stopped rather than retyping blindly and risking "
                f"duplicated text. Check the field before "
                f"continuing."
            )
    base = (
        f"Typed {count} character(s) into '{facts['name']}' "
        f"({facts['control_type']}) in {desc} (as keystrokes)."
    )
    # ── Rung 5 for keystrokes: a match VERIFIES; a mismatch is
    # advisory (a field may legitimately reformat what was typed)
    # — the result then withholds word-for-word confirmation
    # instead of claiming the text landed exactly as written. ────
    readback = _current_text(el)
    if _values_match(readback, payload):
        return base + _probe_type_suffix(
            snap_before, _safe_snapshot(), value_before,
            readback, facts, verified=True)
    if readback != value_before:
        out = base + _probe_type_suffix(
            snap_before, _safe_snapshot(), value_before,
            readback, facts, verified=False)
        out += (
            " Read-back note: the field does not hold exactly the "
            "text I sent (it may have been reformatted as it was "
            "typed), so I can't confirm it landed word-for-word."
        )
        return out
    # Nothing landed at all (read-back == pre-type value) — the
    # silent-drop signature. Only NOW do the fallback rungs run,
    # in order, each with its own failsafe check, each followed
    # by rung 5's read-back; a rung that lands anything (even
    # reformatted) stops the ladder, so nothing is ever typed
    # twice. An empty text has nothing to fall back to.
    if not text:
        return base + _probe_type_suffix(
            snap_before, _safe_snapshot(), value_before,
            readback, facts, verified=False)
    # ── Rung 3b: SCAN-CODE keystrokes (v5.59, HA-2) ─────────────
    blocked = _failsafe_block()
    if blocked:
        return blocked
    identity_before_sc = _foreground_identity()
    outcome = _scancode_type(text, clear_first=clear_first)
    if outcome.aborted:
        # D-04: same terminal partial report as rung 3 — never
        # retype the remainder into the new window.
        return _partial_type_report(facts, desc, outcome.chars_sent, count)
    scan_base = (
        f"Typed {count} character(s) into '{facts['name']}' "
        f"({facts['control_type']}) in {desc} (as scan-code "
        "keystrokes — the target dropped virtual-key input)."
    )
    paste_text = text
    paste_base = None
    paste_keys_remainder = False
    if outcome.completed:
        readback = _current_text(el)
        if _values_match(readback, payload):
            return scan_base + _probe_type_suffix(
                snap_before, _safe_snapshot(), value_before,
                readback, facts, verified=True)
        if readback != value_before:
            out = scan_base + _probe_type_suffix(
                snap_before, _safe_snapshot(), value_before,
                readback, facts, verified=False)
            out += (
                " Read-back note: the field does not hold exactly the "
                "text I sent (it may have been reformatted as it was "
                "typed), so I can't confirm it landed word-for-word."
            )
            return out
        # Read-back == pre-type value: the silent-drop signature
        # persists — fall through to the full paste below.
    else:
        # D-05: the same read-back-before-retry as rung 3,
        # applied to the paste fallback — a partial scan-code
        # land must not be followed by a full-text paste on top
        # of it (same duplication, same append-mode risk).
        if not _target_still_current(identity_before_sc):
            return (
                f"Found '{facts['name']}' in {desc}, but the target "
                f"changed while typing — scan-code keystrokes "
                f"reported {outcome.chars_sent} of {count} "
                f"character(s) sent, and I did not paste the rest "
                f"into the new window. Check the field before "
                f"continuing."
            )
        readback_pre = _current_text(el)
        plan_kind, plan_arg = _retry_plan(
            readback_pre, value_before, payload)
        if plan_kind == "landed":
            return scan_base + _probe_type_suffix(
                snap_before, _safe_snapshot(), value_before,
                readback_pre, facts, verified=True)
        if plan_kind == "remainder":
            # Paste only the remainder, at the caret, with no
            # select-all — clearing would wipe the landed prefix.
            paste_text = plan_arg
            paste_keys_remainder = True
            paste_base = (
                f"Typed {count} character(s) into '{facts['name']}' "
                f"({facts['control_type']}) in {desc} (scan-code "
                f"keystrokes partially landed; remainder via "
                f"clipboard paste)."
            )
        elif plan_kind == "full":
            pass  # nothing landed — the full paste below is safe
        else:  # "report" — unexpected content, never guess
            return (
                f"Scan-code keystrokes reported {outcome.chars_sent} "
                f"of {count} character(s) sent into '{facts['name']}' "
                f"in {desc}, but the field does not hold the text I "
                f"sent (it may have been reformatted as it was "
                f"typed), so I stopped rather than pasting blindly "
                f"and risking duplicated text. Check the field "
                f"before continuing."
            )
    # ── Rung 4: CLIPBOARD paste (v5.59, HA-6) ───────────────────
    # Password fields never reach this rung: rung 1 refused them
    # before any rung ran, and the clipboard channel itself never
    # echoes or retains the text (see desktop.py).
    blocked = _failsafe_block()
    if blocked:
        return blocked
    try:
        from . import desktop as _desktop_v559  # Phase 3 D2c: file now in core/desktop/ — . import binds the same desktop module (desktop_action lives on the module)

        def _paste_keys():
            if paste_keys_remainder:
                # Remainder only: no select-all — clearing would
                # wipe the landed prefix this paste completes.
                el.type_keys("^v", set_focus=False)
            else:
                if clear_first:
                    el.type_keys("^a", set_focus=False)
                el.type_keys("^v", set_focus=False)

        pasted, clip_note = _desktop_v559.clipboard_paste(
            paste_text, _paste_keys)
    except Exception:  # noqa: BLE001 — clipboard rung unavailable
        pasted, clip_note = False, "clipboard paste unavailable."
    if pasted:
        clip_base = paste_base if paste_base is not None else (
            f"Typed {count} character(s) into '{facts['name']}' "
            f"({facts['control_type']}) in {desc} (via clipboard "
            "paste — keystroke input was dropped by the target)."
        )
        readback = _current_text(el)
        if _values_match(readback, payload):
            return clip_base + _probe_type_suffix(
                snap_before, _safe_snapshot(), value_before,
                readback, facts, verified=True) + f" {clip_note}"
        if readback != value_before:
            out = clip_base + _probe_type_suffix(
                snap_before, _safe_snapshot(), value_before,
                readback, facts, verified=False)
            out += (
                " Read-back note: the field does not hold exactly the "
                "text I sent (it may have been reformatted as it was "
                "typed), so I can't confirm it landed word-for-word."
            )
            return out + f" {clip_note}"
    return (
        f"Found '{facts['name']}' in {desc}, but the text did not "
        "land: UIA value, keystrokes, scan-code keystrokes and "
        "clipboard paste were all tried and the field still holds "
        "its previous text. Nothing was claimed as typed."
    )

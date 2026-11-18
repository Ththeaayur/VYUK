"""control_input concern extracted from core/control.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/control."""

from .control_base import (_DATA_DIR, _is_windows, _routed_backend, _truncate, _input_blocked)
from .cursor_input import (_VK_MAP, _VK_CHAR_MAP, _NAME_BY_VK,
                           _sendinput_key, _sendinput_scancode,
                           _vk_scan_for_char, _scan_layout_available)
from .control_gesture import (_GESTURE_TAKEOVER_PX, _GESTURE_POLL_S, _GESTURE_REASON, _gesture_lock, _gesture_depth, _gesture_expected, _gesture_watcher, _gesture_position_reader, _gesture_trip_fn, _read_gesture_mouse_pos, _trip_for_gesture, gesture_active, _note_synthetic_mouse_pos, check_gesture_takeover, _gesture_watch_loop, _begin_assistant_gesture, _end_assistant_gesture, assistant_gesture, _foreground_title, _foreground_elevated, _foreground_is_shell, _note_input_pressed, _note_input_released, _expected_lock, _expected_foreground, _foreground_identity_fn, _foreground_identity, note_expected_foreground, get_expected_foreground, clear_expected_foreground, set_expected_foreground, _foreground_label, _foreground_matches, _expectation_refusal, _last_input_tick, _tick_is_newer)


def _password_block() -> str | None:
    """The ONE shared password rule (v5.45, H20 — core/input_guard):
    the refusal text when the focused control is a password field
    (or UIA answered UNKNOWN), else None. A broken guard never
    blocks ordinary typing wholesale: input_guard itself fails
    open only on the no-UIA-at-all path, by design."""
    try:
        from .. import input_guard  # Phase 3 D2b: input_guard stays flat in core/

        return input_guard.typing_blocked_reason()
    except Exception:  # noqa: BLE001
        return None


_TYPE_BATCH_CHARS = 200


def type_text(text: str) -> str:
    """Type text into the currently focused application."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.type_text(text)
    if text is None:
        return "Nothing to type: no text was given."
    text = str(text)
    if not text:
        return "Nothing to type: text was empty."
    blocked = _input_blocked()
    if blocked:
        return blocked
    # H20: the shared password rule runs BEFORE any keystroke is
    # injected — this path used to have no password check at all.
    password_block = _password_block()
    if password_block:
        return password_block
    # v5.48 (programme item 22): focus discipline — when a
    # focus entry point recorded an expectation, the foreground
    # must STILL be that window before the first keystroke;
    # on a mismatch nothing is typed and the refusal names the
    # window that is focused now. No expectation -> unchanged.
    expectation_block = _expectation_refusal()
    if expectation_block:
        return expectation_block
    # Typing is dispatched with the native Windows SendInput API
    # (core/desktop/uia_send.py's unicode engine — the same
    # keystrokes the UIA typing ladder sends). Off Windows there
    # is no typing engine in this build: the platform backend
    # above already owns non-Windows hosts, so reaching here off
    # Windows means the backend seam failed, and the honest
    # answer is unavailable, not a silent no-op.
    if not _is_windows():
        return ("Typing unavailable: this build of VYUK types with the "
                "native Windows SendInput API, which is not available "
                "on this system.")
    try:
        from .uia_send import _sendinput_type
    except Exception as e:  # noqa: BLE001 — the typing engine is broken
        return f"Could not type text (the typing engine could not be loaded: {e})."
    try:
        _begin_assistant_gesture()
        # Command Deck Phase F: input held for the typing, plus the
        # overlay chip — a character COUNT only, never the text.
        # (Typing into a password field is refused above by the
        # shared password rule, so a type chip never describes
        # password input.) Released in the finally below.
        try:
            from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

            _prod.emit_input_acquired(source="control_type")
            _prod.emit_agent_action("type", chars=len(text))
        except Exception:  # noqa: BLE001 — cosmetic channel, never break
            pass
        # H13: name the destination — captured at dispatch time, so
        # if focus is not where the user thinks it is, the receipt
        # says where the text actually went.
        destination = _foreground_title()
        # v5.46 (programme item 7, A5/D-C3): type in batches and
        # re-check BEFORE EACH batch — (i) the failsafe
        # (_input_blocked) and (ii) the foreground-shell probe.
        # The executor gate decided from the foreground at
        # execute() time, but focus can change before or during
        # typing; if the window that now owns the keyboard has
        # become a terminal/shell, the remaining text must not
        # land in it. The stop is reported honestly, with the
        # number of characters that were actually typed.
        typed_count = 0
        # v5.48 (programme item 22): user-input abort. The
        # last-input tick (core/input_guard.last_input_tick)
        # counts VYUK's OWN synthetic keys too, so the sampling
        # discipline is exact: sample the tick at typing start,
        # then sample it again IMMEDIATELY AFTER each of her
        # batches — that post-batch sample absorbs every tick
        # her own batch produced. Before each SUBSEQUENT batch,
        # only a tick NEWER than the post-batch sample can be
        # the user's own input landing after her typing began,
        # and that stops the type. A None probe (off Windows)
        # skips the check entirely. (SendInput typing itself is
        # transient per-character injection with no hold, so it
        # is not registered with the failsafe — v5.48 item 26.)
        input_baseline = _last_input_tick()
        while typed_count < len(text):
            blocked_now = _input_blocked()
            if blocked_now:
                if typed_count:
                    return (
                        f"{blocked_now} Typing stopped part-way: "
                        f"{typed_count} of {len(text)} character(s) "
                        "had been typed before the stop; the rest "
                        "was not typed."
                    )
                return blocked_now
            if _foreground_is_shell():
                return (
                    "Typing stopped part-way because the focused "
                    "window became a terminal/shell: "
                    f"{typed_count} of {len(text)} character(s) "
                    "were typed before the stop; the rest was "
                    "not typed."
                )
            if typed_count:
                now_tick = _last_input_tick()
                if _tick_is_newer(now_tick, input_baseline):
                    return (
                        "Typing stopped part-way: "
                        f"{typed_count} of {len(text)} character(s) "
                        "had been typed before the stop; the rest "
                        "was not typed — you took over the "
                        "keyboard (new input arrived that wasn't "
                        "VYUK's), so she stopped typing."
                    )
            batch = text[typed_count:typed_count + _TYPE_BATCH_CHARS]
            # SendInput's own return is the accepted-event count:
            # chars_sent counts only fully-sent characters, so a
            # short or aborted batch reports exactly how much
            # landed — the loop never claims the whole batch.
            outcome = _sendinput_type(batch)
            typed_count += outcome.chars_sent
            if outcome.aborted:
                return (
                    "Typing stopped part-way because the focused "
                    "window changed: "
                    f"{typed_count} of {len(text)} character(s) "
                    "had been typed before the stop; the rest "
                    "was not typed."
                )
            if not outcome.completed:
                if typed_count == 0:
                    return ("Could not type text (SendInput did not "
                            "accept the keystrokes on this system).")
                return (
                    "Typing stopped part-way: "
                    f"{typed_count} of {len(text)} character(s) "
                    "had been typed before the stop; the rest "
                    "was not typed."
                )
            post_batch_tick = _last_input_tick()
            if post_batch_tick is not None:
                input_baseline = post_batch_tick
        # H23a: an elevated foreground window silently DROPS
        # synthetic input from a non-elevated process — claiming
        # "typed" would be a lie, so the receipt says the input
        # could not be delivered instead.
        if _foreground_elevated() is True:
            where = f" ('{destination}')" if destination else ""
            return (
                f"Input could not be delivered to the elevated "
                f"window{where}: Windows drops synthetic input sent "
                "to an elevated process from a non-elevated app, "
                "so the text was not typed there. Run VYUK as "
                "administrator, or type it yourself."
            )
        # R1b: the keystrokes were sent, but the focused app's text
        # cannot be read back — there is no field handle to verify
        # against. The receipt names where the text went (the
        # destination discipline, H13) and says plainly that the
        # landing was not confirmed.
        # B13: the vision landing check asks the B5 backend whether
        # the typed text is on screen (Granite read). Where vision
        # cannot read, the honestly-unverified return stands
        # exactly as before (same message, same kind).
        from .. import tool_outcome as _outcome  # R1b: seam wiring

        result = f"Typed {len(text)} character(s) into the focused app."
        if destination:
            result += f" It went to: {destination}."
        return _outcome.hands_landing_check(
            "type", text,
            result + " (Sent as keystrokes; whether the text landed "
            "was not confirmed.)")
    except Exception as e:  # noqa: BLE001
        return f"Could not type text (no display / input access on this system?): {e}"
    finally:
        _end_assistant_gesture()
        try:
            from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

            _prod.emit_input_released(source="control_type")
        except Exception:  # noqa: BLE001 — cosmetic channel, never break
            pass


# Canonical key-name aliases (the gate's normalisation map too —
# core/funnel/tools_gate_shell.py imports this as alias_map):
# "windows" / "super" are the Win key, "return" is Enter, and so
# on, so one spelling never slips past a rule written for another.
_KEY_ALIASES = {
    "enter": "enter", "return": "enter",
    "esc": "esc", "escape": "esc",
    "tab": "tab", "space": "space",
    "backspace": "backspace", "delete": "delete", "del": "delete",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end",
    "page_up": "page_up", "pageup": "page_up", "pgup": "page_up",
    "page_down": "page_down", "pagedown": "page_down", "pgdn": "page_down",
    "insert": "insert", "caps_lock": "caps_lock", "capslock": "caps_lock",
    "shift": "shift", "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "win": "cmd", "windows": "cmd", "cmd": "cmd", "super": "cmd",
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4", "f5": "f5", "f6": "f6",
    "f7": "f7", "f8": "f8", "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
}

# The virtual-key tables live in cursor_input.py now (the ctypes
# hub owns VK resolution); _VK_MAP / _VK_CHAR_MAP are imported
# above and stay re-exportable from here for core/control.


def _key_name(key) -> str:
    """Best-effort display name for a key (for D6 honesty): a
    virtual-key code resolves to its canonical name, a name
    string passes through, a key object reads .char / .name."""
    try:
        if isinstance(key, int):
            return _NAME_BY_VK.get(key, "VK 0x%02X" % key)
        if isinstance(key, str):
            return key
        return str(getattr(key, "char", None) or getattr(key, "name", None)
                   or key)
    except Exception:  # noqa: BLE001 — naming never breaks a call
        return "key"


def _release_failure_note(failed, failsafe: bool) -> str:
    """Honest tail for a hotkey result whose release(s) failed.

    Fix 7 (D6, fail-open audit follow-up): a key whose release raises
    stays physically down while the press itself succeeded, so the
    result must name it. The message keeps the "Pressed hotkey:"
    prefix the control_plus_desktop receipt keys off, and appends
    this note. ``failsafe`` names the rescue: on the failsafe-
    tracked path the failsafe has the key tracked and releases it
    on the next trip; on paths without failsafe registration the
    user taps the key.
    """
    names = ", ".join(failed)
    if failsafe:
        return (" — but the release of %s failed, so %s may still be "
                "physically down. The failsafe has it tracked and will "
                "release it on the next trip (takeover gesture, "
                "heartbeat starvation, or stop)."
                % (names, names))
    return (" — but the release of %s failed, so %s may still be "
            "physically down. Tap the key yourself to release it — "
            "there is no automatic rescue on this path."
            % (names, names))


def press_hotkey(keys: str) -> str:
    """Press a single key or a '+'-separated combo, e.g. 'ctrl+c', 'alt+tab', 'enter'.

    D6 reliability note (fail-open audit; Fix 7 CLOSED it): "Pressed
    hotkey" means the keys were SENT (pressed). A release that raises
    is no longer swallowed: the message names the stuck key(s) and
    the rescue (failsafe-tracked here — the keys are registered
    with the failsafe as they go down; tap-the-key on paths
    without failsafe registration). The control_plus_desktop
    "Sent" receipt keys off this message's prefix and inherits the
    same caveat (sent, not released) — the stuck-key note travels
    with it because the prefix is unchanged.
    """
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.press_hotkey(keys)
    if not keys or not str(keys).strip():
        return "No keys given. Example: 'ctrl+c', 'alt+tab' or 'enter'."
    parts = [p.strip().lower() for p in str(keys).split("+") if p.strip()]
    if not parts:
        return "No keys given. Example: 'ctrl+c', 'alt+tab' or 'enter'."
    blocked = _input_blocked()
    if blocked:
        return blocked
    # v5.48 (programme item 22): focus discipline, same gate as
    # type_text — a recorded expectation must still match the
    # foreground before the first key goes down.
    expectation_block = _expectation_refusal()
    if expectation_block:
        return expectation_block

    # The one path: native Windows SendInput with virtual-key
    # codes (core/desktop/cursor_input.py). Every key that goes
    # down is released in a finally (v4.4): a mid-combo failure
    # used to leave modifiers physically held — the user's
    # keyboard then "misbehaves" until they tap the stuck key
    # themselves (audit_hands MAJOR). Off Windows there is no
    # hotkey engine in this build (the platform backend above
    # already owns non-Windows hosts).
    if not _is_windows():
        return ("Hotkey control unavailable: this build of VYUK sends "
                "hotkeys with the native Windows SendInput API, which "
                "is not available on this system.")
    # GAPS-5 (BLOCKS #8): resolution — every part becomes a press
    # spec BEFORE any key goes down (a resolution failure refuses
    # the whole combo with nothing sent, as before). A spec is
    # (events, name): events is a list of (kind, code, vk, name)
    # in key-down order — kind "vk" or "scan", code the VK or
    # scan code, vk the VK registered with the failsafe, name the
    # display name for a release-failure note. The release order
    # is the reverse of the down order.
    #
    # The scan-code rung: a single punctuation character is
    # resolved through the CURRENT keyboard layout (VkKeyScanW
    # -> VK + shift, MapVirtualKeyW -> scan code) and sent as
    # KEYEVENTF_SCANCODE events. The VK path cannot express
    # these characters layout-independently: _VK_CHAR_MAP
    # hard-codes US-layout VKs, so e.g. '-' sent as
    # VK_OEM_MINUS produces 'ß' on a German layout. Named keys
    # and letters/digits stay on the VK path (a hotkey names a
    # key by character, and the VK for a letter IS its code —
    # the scan-code physical key for 'a' is 'q' on AZERTY, so
    # letters must never take this rung). A character the layout
    # cannot produce falls back to the VK table when the layout
    # API is unavailable (the resolver cannot tell that from
    # "unmappable", and the VK path is today's behaviour there);
    # when the layout API answers and the character is genuinely
    # not on it, the refusal says so instead of mistyping.
    # A scan-pressed key is registered with the failsafe under
    # its resolved VK: the OS key state is VK-keyed, so a trip
    # releases it with the existing VK key-up path.
    presses = []
    for part in parts:
        if part in _VK_MAP:
            vk = _VK_MAP[part]
            presses.append(( (("vk", vk, vk, _key_name(vk)),), part))
        elif len(part) == 1 and part.isalnum():
            # Letters/digits: ord(upper) IS the VK code.
            vk = ord(part.upper())
            presses.append(( (("vk", vk, vk, _key_name(vk)),), part))
        elif len(part) == 1:
            spec = _vk_scan_for_char(part)
            if spec is not None:
                scan, shift_scan, vk = spec
                if shift_scan:
                    presses.append((
                        (("scan", shift_scan, 0x10, "shift"),
                         ("scan", scan, vk, part)), part))
                else:
                    presses.append((
                        (("scan", scan, vk, part),), part))
            elif part in _VK_CHAR_MAP and not _scan_layout_available():
                # Layout API unavailable: the VK path, today's
                # behaviour — the best effort when the layout
                # cannot be consulted.
                vk = _VK_CHAR_MAP[part]
                presses.append(( (("vk", vk, vk, _key_name(vk)),), part))
            elif part in _VK_CHAR_MAP:
                return (f"Cannot press '{part}': this keyboard "
                        "layout has no key for it.")
            else:
                return f"Unknown key name: '{part}'. Use names like ctrl, alt, shift, enter, tab, esc, f1-f12, or a single character."
        else:
            return f"Unknown key name: '{part}'. Use names like ctrl, alt, shift, enter, tab, esc, f1-f12, or a single character."
    sent = []  # (kind, code, vk, name) per key-down that landed, in order
    _release_failed = []  # Fix 7 (D6): release failures are
    # collected, never swallowed — a key whose release fails
    # stays physically down, and the message must say so.
    _press_error = None
    _begin_assistant_gesture()
    # Command Deck Phase F: input held for the combo, plus the
    # overlay chip (the combo itself — a combo is not text).
    # Released after _end_assistant_gesture below.
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

        _prod.emit_input_acquired(source="control_hotkey")
        _prod.emit_agent_action("hotkey", label="+".join(parts))
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
    try:
        for events, _spec_name in presses:
            for kind, code, vk, name in events:
                if kind == "scan":
                    ok = _sendinput_scancode(code)
                else:
                    ok = _sendinput_key(code)
                if not ok:
                    raise RuntimeError(
                        "SendInput did not accept the key-down event")
                sent.append((kind, code, vk, name))
                # v5.48 (programme item 26): register the hold
                # with the failsafe — a trip now releases exactly
                # the keys still down, through the same SendInput
                # key-up the finally below uses. Unregistered in
                # the finally as each release lands; a FAILED
                # release leaves the entry tracked, which is
                # precisely the stuck-key case the failsafe
                # exists for. A scan-pressed key registers under
                # its resolved VK (GAPS-5): the OS key state is
                # VK-keyed, so a trip releases it with the
                # existing VK key-up path.
                _note_input_pressed("key", vk)
    except Exception as e:  # noqa: BLE001 — recorded, not
        # returned yet: the finally below still has to release
        # whatever went down, and a release failure there is
        # part of the same answer.
        _press_error = e
    finally:
        for kind, code, vk, name in reversed(sent):
            try:
                if kind == "scan":
                    ok = _sendinput_scancode(code, key_up=True)
                else:
                    ok = _sendinput_key(code, key_up=True)
                if not ok:
                    raise RuntimeError(
                        "SendInput did not accept the key-up event")
            except Exception:  # noqa: BLE001 — Fix 7 (D6):
                # the release failed: leave the failsafe entry
                # tracked (it is the rescue) AND name the key.
                _release_failed.append(name)
            else:
                _note_input_released("key", vk)
        _end_assistant_gesture()
        try:
            from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

            _prod.emit_input_released(source="control_hotkey")
        except Exception:  # noqa: BLE001 — cosmetic channel, never break
            pass
    if _press_error is not None:
        _msg = (f"Could not press hotkey '{keys}' (SendInput did not "
                f"accept the key event: {_press_error})")
        if _release_failed:
            _msg += _release_failure_note(_release_failed,
                                          failsafe=True)
        return _msg
    if _release_failed:
        return ("Pressed hotkey: %s%s"
                % ("+".join(parts),
                   _release_failure_note(_release_failed,
                                         failsafe=True)))
    # R1b: the key event was sent, but no OS read-back exists
    # for whether the app acted on it — mark it honestly
    # unverified. (The release-failed returns above stay plain:
    # they already name the failure, D6.)
    from .. import tool_outcome as _outcome  # R1b: seam wiring

    return _outcome.unverified(
        "Pressed hotkey: %s (sent — whether the app acted on it "
        "was not confirmed.)" % "+".join(parts),
        "no OS read-back exists for a sent key event")


def mouse_control(action: str, x: int = None, y: int = None, amount: int = 3) -> str:
    """Move/click/scroll the mouse. action: move/click/double_click/right_click/scroll."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.mouse_control(action, x=x, y=y, amount=amount)
    action = (action or "").strip().lower()
    valid = ("move", "click", "double_click", "right_click", "scroll")
    if action not in valid:
        return f"Unknown mouse action: '{action}'. Use one of: {', '.join(valid)}."
    blocked = _input_blocked()
    if blocked:
        return blocked
    # v4.4: exactly one coordinate is a caller error, never a silent
    # click wherever the pointer happens to be (audit_core MINOR:
    # mouse_control("click", x=500) clicked at the current position
    # while the brain believed it clicked at x=500).
    if (x is None) != (y is None):
        return (
            "Mouse actions need both x and y coordinates (or neither) "
            f"— got only {'x' if x is not None else 'y'}, so nothing was done."
        )
    # The mouse is driven with the native Windows SendInput API
    # through the cursor hub (core/desktop/cursor_input.py) — the
    # same handle the cursor tools use. Off Windows there is no
    # mouse engine in this build (the platform backend above
    # already owns non-Windows hosts).
    try:
        from .cursor_input import _mouse_controller
    except Exception as e:  # noqa: BLE001 — the cursor hub is broken
        return f"Mouse control unavailable (the cursor engine could not be loaded: {e})."
    mouse, Button, err = _mouse_controller()
    if mouse is None:
        return err
    try:
        _begin_assistant_gesture()
        clamped = False
        if x is not None and y is not None:
            # Same virtual-bounds fence the cursor pack applies
            # (imported, not duplicated — one fence for every hand).
            from .cursor import _fence_point  # Phase 3 D4d: cursor hub now in core/desktop/; D2b interim closed

            fx, fy = _fence_point(int(x), int(y))
            clamped = (fx, fy) != (int(x), int(y))
            # Note the target BEFORE dispatching, so the gesture
            # watcher never mistakes her own move for a takeover.
            _note_synthetic_mouse_pos((fx, fy))
            mouse.position = (fx, fy)
            x, y = fx, fy
        elif action == "move":
            return "Mouse 'move' needs both x and y coordinates."
        note = " (target clamped to the edge of the screen)" if clamped else ""
        if action == "move":
            # R1b: read the position back — setting mouse.position
            # is not proof it landed there. A matching read-back
            # keeps the v5.8 receipt byte-identical; a mismatch or
            # an unreadable position is reported honestly.
            try:
                pos = mouse.position
                landed = (int(pos[0]), int(pos[1]))
            except Exception:  # noqa: BLE001 — position unreadable
                landed = None
            if landed is not None and landed == (int(x), int(y)):
                return f"Moved mouse to ({int(x)}, {int(y)}){note}."
            if landed is not None:
                return (
                    f"Asked the mouse to move to ({int(x)}, {int(y)}), "
                    f"but it reads at ({landed[0]}, {landed[1]}) "
                    "instead — the move did not land where asked."
                )
            return (
                f"Asked the mouse to move to ({int(x)}, {int(y)}), but "
                "the position could not be read back to confirm it."
            )
        # v5.8: click-family actions get the cursor's receipt — the
        # result names the control under the landing point (after the
        # fence above) and notes a focus change. Shared with
        # cursor_control via core/desktop/cursor.py (imported, not
        # duplicated — one receipt for every hand). Move and scroll
        # are untouched. The failsafe already refused above, so this
        # never runs while tripped.
        id_note = ""
        fg_before = None
        if action in ("click", "double_click", "right_click"):
            from .cursor import _focus_change_note, _identify_click_point  # Phase 3 D4d: cursor hub now in core/desktop/; D2b interim closed

            if x is not None and y is not None:
                landing = (int(x), int(y))
            else:
                try:
                    pos = mouse.position
                    landing = (int(pos[0]), int(pos[1]))
                except Exception:  # noqa: BLE001 — position unreadable
                    landing = None
            if landing is not None:
                id_note, fg_before = _identify_click_point(*landing)

            def _receipt() -> str:
                return id_note + _focus_change_note(fg_before)

        if action in ("click", "double_click", "right_click"):
            # H23a: dispatch, then honesty about delivery — an
            # elevated foreground window drops synthetic input
            # from a non-elevated process, so the receipt must not
            # claim the click landed there.
            verb = {"click": ("Clicked (left button)", mouse.click,
                              (Button.left, 1)),
                    "double_click": ("Double-clicked (left button)",
                                     mouse.click, (Button.left, 2)),
                    "right_click": ("Right-clicked", mouse.click,
                                    (Button.right, 1))}[action]
            # v5.48 (programme item 26): mouse.click() presses and
            # releases inside one dispatch, so the registry entry lives
            # only for the call's duration — a trip (or a crash)
            # mid-click still finds the button tracked and
            # releases it; a completed click unregisters.
            _note_input_pressed("button", verb[2][0])
            # Command Deck Phase F: input held around the click,
            # plus the overlay chip (named control when the receipt
            # identified one, else the button). A failed click
            # releases the hold and shows no chip.
            try:
                from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

                _prod.emit_input_acquired(source="control_mouse")
            except Exception:  # noqa: BLE001 — cosmetic channel, never break
                pass
            try:
                verb[1](*verb[2])
            except Exception:
                # A failed click may have left the button down —
                # the entry STAYS tracked so a failsafe trip
                # releases it; the outer handler reports the error.
                try:
                    from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

                    _prod.emit_input_released(source="control_mouse")
                except Exception:  # noqa: BLE001 — cosmetic channel, never break
                    pass
                raise
            else:
                _note_input_released("button", verb[2][0])
                try:
                    from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

                    _prod.emit_input_released(source="control_mouse")
                    chip_label = {"click": "left",
                                  "double_click": "left",
                                  "right_click": "right"}[action]
                    if id_note.startswith(' That point is the "'):
                        rest = id_note[len(' That point is the "'):]
                        if '"' in rest:
                            chip_label = rest.split('"', 1)[0] or chip_label
                    _prod.emit_agent_action(
                        "click", label=chip_label,
                        x=int(landing[0]) if landing is not None else 0,
                        y=int(landing[1]) if landing is not None else 0)
                except Exception:  # noqa: BLE001 — cosmetic channel, never break
                    pass
            if _foreground_elevated() is True:
                return (
                    f"{verb[0]}{note}, but the input could not be "
                    "delivered to the elevated window in front: "
                    "Windows drops synthetic input sent to an "
                    "elevated process from a non-elevated app."
                )
            return f"{verb[0]}{note}." + _receipt()
        # scroll — clamped to a sane range (audit_hands leftover):
        # a raw pass-through let amount=100000 dispatch as-is, which
        # on backends where a scroll is repeated wheel clicks is a
        # long hang. Anything beyond ±50 steps is almost certainly a
        # mistake, so clamp it and SAY so.
        steps = int(amount) if amount is not None else 3
        clamped_steps = max(-50, min(50, steps))
        # Command Deck Phase F: input held around the scroll, plus
        # the overlay chip (direction only). A failed scroll
        # releases the hold and shows no chip.
        try:
            from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

            _prod.emit_input_acquired(source="control_mouse")
        except Exception:  # noqa: BLE001 — cosmetic channel, never break
            pass
        try:
            mouse.scroll(0, clamped_steps)
        except Exception:
            try:
                from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

                _prod.emit_input_released(source="control_mouse")
            except Exception:  # noqa: BLE001 — cosmetic channel, never break
                pass
            raise
        else:
            try:
                from .. import state_bus_producers as _prod  # Phase 3 D2b: state_bus_producers stays flat in core/

                _prod.emit_input_released(source="control_mouse")
                _prod.emit_agent_action(
                    "scroll", label="up" if clamped_steps > 0 else "down")
            except Exception:  # noqa: BLE001 — cosmetic channel, never break
                pass
        # R1b: a scroll has no position to read back — the wheel
        # event was dispatched, and whether the page moved cannot
        # be observed from here. Mark it honestly unverified.
        from .. import tool_outcome as _outcome  # R1b: seam wiring

        if clamped_steps != steps:
            return _outcome.unverified(
                f"Scrolled by {clamped_steps} step(s) "
                f"(asked for {steps} — clamped to the ±50 range). "
                "(Scroll dispatched; the page position was not re-read.)",
                "no position read-back after the scroll")
        return _outcome.unverified(
            f"Scrolled by {steps} step(s). "
            "(Scroll dispatched; the page position was not re-read.)",
            "no position read-back after the scroll")
    except Exception as e:  # noqa: BLE001
        return f"Could not control the mouse (no display / input access on this system?): {e}"
    finally:
        _end_assistant_gesture()

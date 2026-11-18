"""uia_send concern extracted from core/desktop/uia.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/desktop/uia."""

from .uia_base import (_SCAN_CAP, _MATCH_CAP, _is_windows, _routed_backend, _truncate, _failsafe_block, _unavailable, _uia_window, _window_failure_text, _UIA_IS_PASSWORD_ID, _PASSWORD_NAME_RE, _password_name_re, _is_password_control, _control_usable, _TYPE_KEYS_SPECIALS, _escape_type_keys, _current_text, foreground_title, foreground_hwnd)
from .cursor_input import _sendinput_keyboard  # GAPS-17: the shared input engine — this module no longer calls user32.SendInput directly


class _SendOutcome:
    """One batched-typing engine's result. `completed` means every
    event was accepted; `aborted` means the target changed
    mid-send and the remaining blocks were never sent (chars_sent
    is then exact — only fully-sent characters count); both False
    means the engine failed or under-delivered with nothing
    aborted (the caller's existing fallback path, unchanged)."""

    __slots__ = ("completed", "aborted", "chars_sent", "chars_total")

    def __init__(self, completed, aborted, chars_sent, chars_total):
        self.completed = bool(completed)
        self.aborted = bool(aborted)
        self.chars_sent = int(chars_sent)
        self.chars_total = int(chars_total)

    def __eq__(self, other):
        if not isinstance(other, _SendOutcome):
            return NotImplemented
        return (self.completed == other.completed
                and self.aborted == other.aborted
                and self.chars_sent == other.chars_sent
                and self.chars_total == other.chars_total)

    def __repr__(self):
        return (f"_SendOutcome(completed={self.completed}, "
                f"aborted={self.aborted}, chars_sent={self.chars_sent}, "
                f"chars_total={self.chars_total})")

    @property
    def delivery(self) -> str:
        """Tri-state delivery (D-05): 'aborted' when the target
        changed mid-send; 'complete' when every event was
        accepted; 'partial' when some characters were sent but
        not all; 'failed' when nothing was sent and nothing
        aborted. Callers branch on this, never on a bare
        Boolean."""
        if self.aborted:
            return "aborted"
        if self.completed:
            return "complete"
        if self.chars_sent > 0:
            return "partial"
        return "failed"


def _foreground_identity():
    """(hwnd, pid) of the foreground window, or None when unknown.
    Never raises. The D-04 revalidation anchor: a window's handle
    plus its owning process id, captured before the first block
    and re-checked before every later one."""
    if not _is_windows():
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return (int(hwnd), int(pid.value))
    except Exception:  # noqa: BLE001
        return None


def _target_still_current(identity) -> bool:
    """True when the typing target is still in front. An unknown
    starting identity cannot be validated, so it proceeds (today's
    behaviour, never a false abort); a positively different
    foreground — or no foreground at all when the start was known
    — means the target changed."""
    if identity is None:
        return True
    now = _foreground_identity()
    if now is None:
        return False
    return now == identity


def _chunk_events(groups, chunk_size: int = 200):
    """(blocks, chars_per_block) for (char_index|None, event)
    groups: blocks hold at most chunk_size events, and
    chars_per_block[i] counts the characters FULLY contained in
    blocks[0..i] — a character straddling a block boundary counts
    only once its last event is sent, so an abort report is exact.
    Clear-prefix events carry char_index None. Pure."""
    blocks, ends = [], []
    cur, in_block, total = [], 0, 0
    for _ci, ev in groups:
        cur.append(ev)
        in_block += 1
        total += 1
        if in_block >= chunk_size:
            blocks.append(cur)
            ends.append(total)
            cur, in_block = [], 0
    if cur:
        blocks.append(cur)
        ends.append(total)
    last_idx = {}
    for idx, (ci, _ev) in enumerate(groups):
        if ci is not None:
            last_idx[ci] = idx
    chars_per_block = [sum(1 for li in last_idx.values() if li < end)
                       for end in ends]
    return blocks, chars_per_block


def _block_char_progress(groups, block_sizes):
    """Per-block character-completion progress, for partial-block
    SendInput accounting (D-05). progress[i][k] = characters fully
    completed after the first k events of block i (k runs
    0..len(block i)) — a character counts once its LAST event is
    accepted, so a character straddling a block boundary counts
    in the block holding its last event, never earlier, and
    clear-prefix events (char_index None) never complete a
    character. progress[i][-1] summed over blocks 0..i equals
    _chunk_events' chars_per_block[i]. Pure."""
    last_idx = {}
    for idx, (ci, _ev) in enumerate(groups):
        if ci is not None:
            last_idx[ci] = idx
    completing = {}
    for li in last_idx.values():
        completing[li] = completing.get(li, 0) + 1
    progress = []
    pos = 0
    for size in block_sizes:
        row = [0]
        run = 0
        for j in range(size):
            run += completing.get(pos + j, 0)
            row.append(run)
        progress.append(row)
        pos += size
    return progress


def _send_in_blocks(blocks, chars_per_block, send_block, target_ok,
                    block_progress=None):
    """(completed, aborted, chars_sent): send each block in order,
    revalidating the target before each one. An abort stops the
    remaining blocks unsent; an under-delivering block stops the
    send without abort (the caller's D-05 fallback path).
    send_block may return a Boolean (legacy: True = the whole
    block accepted, False = none of it) or an int event count —
    Win32 SendInput's own return, which is how a short block is
    measured instead of collapsed to False. For a short block the
    sent count adds the characters completed within the accepted
    prefix (block_progress[i][accepted]); without progress data
    a short block conservatively contributes nothing beyond the
    fully-sent prior blocks. Pure — tests drive it directly
    with fake blocks and senders."""
    sent = 0
    for i, block in enumerate(blocks):
        if not target_ok():
            return False, True, sent
        result = send_block(block)
        if isinstance(result, bool):
            accepted = len(block) if result else 0
        else:
            try:
                accepted = int(result)
            except (TypeError, ValueError):
                accepted = len(block) if result else 0
            accepted = max(0, min(len(block), accepted))
        if accepted == 0:
            return False, False, sent
        if accepted < len(block):
            base = chars_per_block[i - 1] if i > 0 else 0
            within = 0
            if block_progress is not None and i < len(block_progress):
                row = block_progress[i]
                within = row[accepted] if accepted < len(row) else row[-1]
            return False, False, base + within
        sent = chars_per_block[i]
    return True, False, sent


def _partial_type_report(facts, desc, chars_sent, chars_total) -> str:
    """The D-04 abort report: exactly what was and was not typed,
    with no success claim for the remainder."""
    remaining = max(0, chars_total - chars_sent)
    return (
        f"Typed {chars_sent} of {chars_total} character(s) into "
        f"'{facts['name']}' ({facts['control_type']}) in {desc}, then "
        f"stopped — the target window changed mid-typing, so the "
        f"remaining {remaining} character(s) were NOT typed. Check "
        f"the field before continuing."
    )


def _retry_plan(readback, value_before, payload):
    """Read-back-before-retry decision (D-05). Pure: given the
    field's current content (read back AFTER a partial/failed
    SendInput, never echoed), its pre-type content, and the
    intended full payload, decide what a retry may do:

    - ("landed", None): the field already holds the payload —
      nothing more to type, whatever the engine reported.
    - ("remainder", rest): the field holds a strict prefix of
      the payload beyond its pre-type content — only `rest`
      may be typed, appended at the caret, never the full
      payload on top of the landed prefix. (A field cleared
      but empty also lands here with rest == payload, since
      nothing needs clearing again.)
    - ("full", None): the field still holds exactly its
      pre-type content — nothing landed, so a full retry
      (clear + full text, the pre-existing fallback) is safe.
    - ("report", None): the field holds something else
      (reformatted, or changed by another hand) — no retry
      may guess; the caller reports the partial honestly.

    The caller, not this helper, owns the target-revalidation
    check that must precede any retry."""
    readback = readback or ""
    value_before = value_before or ""
    payload = payload or ""
    if readback == payload:
        return ("landed", None)
    if (readback != value_before
            and len(readback) < len(payload)
            and readback == payload[:len(readback)]):
        return ("remainder", payload[len(readback):])
    if readback == value_before:
        return ("full", None)
    return ("report", None)


def _sendinput_type(text: str, clear_first: bool = False) -> _SendOutcome:
    """Rung 3's engine: real keystrokes via Win32 SendInput with
    KEYEVENTF_UNICODE — each character is delivered as UTF-16 code
    unit key events, immune to keyboard layout and dead keys.
    clear_first sends Ctrl+A then Backspace first (select-all +
    delete, the keystroke equivalent of replacing the content).
    Returns a _SendOutcome: completed only when every event was
    accepted; aborted when the target window changed mid-send
    (D-04 — remaining blocks never sent); neither when SendInput
    does not exist or under-delivers (the caller then falls back
    to pywinauto's type_keys, as before). Never raises.

    GAPS-17: the events are (vk, scan, flags) tuples sent through
    the shared hub (core/desktop/cursor_input._sendinput_keyboard)
    — this function no longer defines its own INPUT structs or
    calls user32.SendInput directly. Same wire bytes, same
    chunking, same D-04/D-05 accounting."""
    if not _is_windows():
        return _SendOutcome(False, False, 0, len(text))
    try:
        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_UNICODE = 0x0004

        def _key_event(vk: int, scan: int, flags: int):
            # (vk, scan, flags) — the hub builds the INPUT struct.
            return (vk, scan, flags)

        events: list = []
        if clear_first:
            VK_CONTROL, VK_A, VK_BACK = 0x11, 0x41, 0x08
            events += [
                (None, _key_event(VK_CONTROL, 0, 0)),
                (None, _key_event(VK_A, 0, 0)),
                (None, _key_event(VK_A, 0, KEYEVENTF_KEYUP)),
                (None, _key_event(VK_CONTROL, 0, KEYEVENTF_KEYUP)),
                (None, _key_event(VK_BACK, 0, 0)),
                (None, _key_event(VK_BACK, 0, KEYEVENTF_KEYUP)),
            ]
        # Characters, not UTF-16 units, are the counting unit: a
        # non-BMP character encodes as two units but is one
        # character of the user's text, and the abort report must
        # count it once.
        for ci, ch in enumerate(text):
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):
                unit = data[i] | (data[i + 1] << 8)
                events.append((ci, _key_event(0, unit, KEYEVENTF_UNICODE)))
                events.append((ci, _key_event(0, unit,
                                              KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)))
        # D-04: chunk by events, revalidating the target window
        # before each block; a mid-send target change aborts the
        # rest (the caller reports the partial send, never
        # retypes it into the new window).
        # D-05: SendInput's return is the accepted EVENT count,
        # not a Boolean — capture it, so a short block reports
        # exactly which characters completed (a character counts
        # once its last event is accepted, per-block progress).
        blocks, chars_per_block = _chunk_events(events)
        progress = _block_char_progress(
            events, [len(b) for b in blocks])

        def _send_block(block):
            return _sendinput_keyboard(block)

        identity = _foreground_identity()
        completed, aborted, chars_sent = _send_in_blocks(
            blocks, chars_per_block, _send_block,
            lambda: _target_still_current(identity),
            block_progress=progress)
        return _SendOutcome(completed, aborted, chars_sent, len(text))
    except Exception:  # noqa: BLE001
        return _SendOutcome(False, False, 0, len(text))


_pydirectinput_typewrite_fn = None  # test seam


def _scancode_type(text: str, clear_first: bool = False) -> _SendOutcome:
    """Scan-code keystrokes for `text`. Completed only when every
    character was sent (or the text was empty after the clear);
    aborted when the target window changed mid-send (D-04);
    neither where SendInput does not exist, a character is
    unmappable, or delivery under-reports. The PyDirectInput
    path is a single third-party call, not a block batch, so it
    cannot revalidate per batch — it reports completed/failed
    only. Never raises."""
    if not _is_windows():
        return _SendOutcome(False, False, 0, len(text))
    if _pydirectinput_typewrite_fn is not None:
        try:
            _pydirectinput_typewrite_fn(text)
            return _SendOutcome(True, False, len(text), len(text))
        except Exception:  # noqa: BLE001
            return _SendOutcome(False, False, 0, len(text))
    try:
        import pydirectinput  # noqa: PLC0415 — lazy, optional
    except Exception:  # noqa: BLE001
        pydirectinput = None
    if pydirectinput is not None:
        try:
            if clear_first:
                pydirectinput.hotkey("ctrl", "a")
                pydirectinput.press("backspace")
            pydirectinput.typewrite(text)
            return _SendOutcome(True, False, len(text), len(text))
        except Exception:  # noqa: BLE001 — fall through to ctypes
            pass
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        # GAPS-17: was a bare `return False` here — a return-type
        # violation (callers read .completed off a _SendOutcome).
        # user32 is still needed for the layout queries below
        # (MapVirtualKeyW / VkKeyScanW); only the SendInput call
        # moved to the hub.
        return _SendOutcome(False, False, 0, len(text))
    try:
        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_SCANCODE = 0x0008

        def _event(scan: int, flags: int):
            # (vk, scan, flags) — the hub builds the INPUT struct.
            return (0, scan, flags)

        def _vk_scan(vk: int) -> int:
            # MAPVK_VK_TO_VSC = 0.
            return int(user32.MapVirtualKeyW(vk, 0))

        events: list = []
        if clear_first:
            ctrl_scan = _vk_scan(0x11)
            a_scan = _vk_scan(0x41)
            back_scan = _vk_scan(0x08)
            if not (ctrl_scan and a_scan and back_scan):
                return _SendOutcome(False, False, 0, len(text))
            events += [
                (None, _event(ctrl_scan, KEYEVENTF_SCANCODE)),
                (None, _event(a_scan, KEYEVENTF_SCANCODE)),
                (None, _event(a_scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)),
                (None, _event(ctrl_scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)),
                (None, _event(back_scan, KEYEVENTF_SCANCODE)),
                (None, _event(back_scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)),
            ]
        VK_SHIFT = 0x10
        for ci, ch in enumerate(text):
            # VkKeyScanW: low byte VK, high byte shift state
            # (bit 0 = Shift). -1 = unmappable: fail, don't guess.
            packed = user32.VkKeyScanW(ord(ch))
            if packed == -1:
                return _SendOutcome(False, False, 0, len(text))
            vk = packed & 0xFF
            shift = bool(packed & 0x0100)
            scan = _vk_scan(vk)
            if not scan:
                return _SendOutcome(False, False, 0, len(text))
            shift_scan = _vk_scan(VK_SHIFT) if shift else 0
            if shift and not shift_scan:
                return _SendOutcome(False, False, 0, len(text))
            if shift:
                events.append((ci, _event(shift_scan, KEYEVENTF_SCANCODE)))
            events.append((ci, _event(scan, KEYEVENTF_SCANCODE)))
            events.append((ci, _event(scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)))
            if shift:
                events.append((ci, _event(shift_scan,
                                          KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)))
        # D-04: same per-block revalidation as the unicode engine.
        # D-05: same accepted-count capture as the unicode engine.
        blocks, chars_per_block = _chunk_events(events)
        progress = _block_char_progress(
            events, [len(b) for b in blocks])

        def _send_block_sc(block):
            # GAPS-17: through the shared hub — no direct SendInput.
            return _sendinput_keyboard(block)

        identity = _foreground_identity()
        completed, aborted, chars_sent = _send_in_blocks(
            blocks, chars_per_block, _send_block_sc,
            lambda: _target_still_current(identity),
            block_progress=progress)
        return _SendOutcome(completed, aborted, chars_sent, len(text))
    except Exception:  # noqa: BLE001
        return _SendOutcome(False, False, 0, len(text))

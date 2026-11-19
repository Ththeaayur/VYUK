"""
Fail-safe watchdog for VYUK's input control (v4.3 hardening).

VYUK moves the real mouse and keyboard. If the app hangs, crashes
mid-gesture, or otherwise stops responding while a button or key is
held down, the user's PC must never be left with stuck input. This
module is the dead-man's switch: while control is healthy, something
must keep calling beat(); if the beats stop for longer than the
configured timeout — or if the user drops the emergency stop-flag
file — the watchdog TRIPS: it releases the mouse buttons and keys
the pressed-input registry says VYUK is actually holding (v5.48,
programme item 26 — previously a blanket sweep of every key and
button), and sets a flag that input code polls before acting.

HOW THE PARENT BUILD SHOULD WIRE THIS (nothing here is wired yet):

- GUI (gui/main_window.py): call failsafe.start() once the main
  window is up, call failsafe.beat() from an existing periodic
  QTimer tick (e.g. the 1-second presence/vitals tick — a live GUI
  keeps the watchdog fed; a frozen GUI starves it and trips it),
  and failsafe.stop() from closeEvent.
- Input tools (core/desktop/cursor.py gestures, core/desktop/control.py
  mouse/keyboard tools, core/desktop/uia.py clicks): check
  failsafe.tripped() before starting a gesture or click; when it is
  True, refuse with a clear message until a human re-arms control.
- Re-arming is deliberately manual: a human calls failsafe.reset()
  (e.g. from a Settings button or a tray action). beat() alone never
  un-trips — a recovered hang must not silently resume control.
- User escape hatch: creating the file ``data/nova_stop.flag`` next
  to the app (data/nova_stop.flag relative to the package root)
  trips the watchdog within one poll cycle, no UI needed. Delete
  the file and call reset() to re-arm.

Everything platform-specific (the native SendInput release
path) is imported lazily and guarded, so this module imports
and runs on headless Linux — the tests drive it with a fake
clock and fake input controllers via set_input_backend().
"""

import logging
import threading
import time
from pathlib import Path

from .state_bus_producers import emit_failsafe

log = logging.getLogger("nova.failsafe")

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_POLL_S = 0.25
STOP_FLAG_NAME = "nova_stop.flag"

# --- tunables / injectable seams (tests monkeypatch these) ------------------
# _clock is a module global on purpose: tests substitute a fake clock
# and advance time by hand instead of sleeping.
_clock = time.monotonic
_timeout_s = DEFAULT_TIMEOUT_S
_poll_s = DEFAULT_POLL_S
_stop_flag_path: Path | None = None  # None → the default data/ path

# --- state -------------------------------------------------------------------
_lock = threading.Lock()
_last_beat: float | None = None
_tripped = False
_trip_reason = ""
_trip_callback = None
_trip_listeners: list = []
_mouse = None      # injected mouse releaser or a test fake
_keyboard = None   # injected keyboard releaser or a test fake
_last_release_failures: list = []  # idents the last trip could NOT release
_thread: threading.Thread | None = None
_stop_event = threading.Event()
# Voice-control batch (2026-11-16, W3): the monitor
# generation — stop() retires the old generation, so a
# start() that lands while stop()'s 2 s join is still waiting
# can never resurrect a stuck old monitor alongside the new
# one (the same clear-then-spawn race as the mic watchdog).
_gen = 0


def _default_stop_flag_path() -> Path:
    """data/nova_stop.flag, resolved like the other data/ files."""
    return Path(__file__).resolve().parent.parent / "data" / STOP_FLAG_NAME


def stop_flag_path() -> Path:
    """The stop-flag file path (override-aware)."""
    return _stop_flag_path or _default_stop_flag_path()


def stop_flag_present() -> bool:
    """True when the user's stop-flag file exists right now.

    GAPS-24 (K-V3): the headless escape hatch — the funnel's
    consult calls this directly, because the GUI monitor thread
    is the only thing that would otherwise notice the file and
    headless there is no monitor thread at all. Never raises —
    an unreadable path reads as absent."""
    try:
        return stop_flag_path().exists()
    except Exception:  # noqa: BLE001 — see above
        return False


def _now() -> float:
    return _clock()


# ---------------------------------------------------------------------------
# Configuration / injection
# ---------------------------------------------------------------------------


def configure(timeout_s: float | None = None, poll_s: float | None = None,
              stop_flag_path=None) -> None:
    """Set the trip timeout, poll interval and/or stop-flag location."""
    global _timeout_s, _poll_s, _stop_flag_path
    with _lock:
        if timeout_s is not None:
            _timeout_s = max(0.05, float(timeout_s))
        if poll_s is not None:
            _poll_s = max(0.01, float(poll_s))
        if stop_flag_path is not None:
            _stop_flag_path = Path(stop_flag_path)


def set_input_backend(mouse=None, keyboard=None) -> None:
    """Inject the input releasers used to release held buttons/keys.

    Production code passes nothing: the native SendInput release
    path (core/desktop/cursor_input.py) is used on a trip. Tests
    pass fakes that record release() calls.
    """
    global _mouse, _keyboard
    with _lock:
        if mouse is not None:
            _mouse = mouse
        if keyboard is not None:
            _keyboard = keyboard


def set_trip_callback(fn) -> None:
    """Register a callable invoked once with the reason string on trip."""
    global _trip_callback
    with _lock:
        _trip_callback = fn


def add_trip_listener(fn) -> None:
    """Register an ADDITIONAL trip listener (v5.46, programme
    item 5, A3): unlike set_trip_callback — the single GUI-owned
    slot above, which a later registration REPLACES — listeners
    accumulate, so independent subsystems (core/desktop/control's
    live-process kill, below) can react to a trip without
    evicting the window's callback or each other. Each listener
    is invoked with the reason string on trip, individually
    guarded in _trip: a broken listener never blocks the trip
    or the listeners after it. Registering the same callable
    twice is a no-op."""
    if fn is None:
        return
    with _lock:
        if fn not in _trip_listeners:
            _trip_listeners.append(fn)


# ---------------------------------------------------------------------------
# Heartbeat API
# ---------------------------------------------------------------------------


def start(timeout_s: float | None = None, poll_s: float | None = None) -> None:
    """Start the watchdog thread (idempotent). The heartbeat clock is
    deliberately NOT armed here: start() runs during window
    construction (service host, MainWindow.__init__), when the event
    loop isn't pumping and the 2 s beat timer cannot fire yet — arming
    the clock there trips a healthy but slow build ("no heartbeat ...
    the app may be hung"), posts the trip line into the chat, and (in
    the test suite) lands a system node on the live thread mid-test.
    The clock arms on the first beat() instead (<=2 s after the window
    is up); a hang after that trips exactly as before. R3F3."""
    global _thread, _last_beat, _gen
    configure(timeout_s=timeout_s, poll_s=poll_s)
    with _lock:
        _last_beat = None
        if _thread is not None and _thread.is_alive():
            return
        _gen += 1
        _g = _gen
        _stop_event.clear()
        _thread = threading.Thread(
            target=_monitor_loop, name="nova-failsafe", daemon=True,
            args=(_g,),
        )
        _thread.start()
    log.debug("failsafe watchdog started (timeout %.2fs)", _timeout_s)


def stop() -> None:
    """Stop the watchdog thread. Does not clear a trip."""
    global _thread, _gen
    _gen += 1
    _stop_event.set()
    with _lock:
        thread, _thread = _thread, None
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)


def beat() -> None:
    """Feed the watchdog. Called by the GUI timer while the app is healthy.

    Deliberately does NOT clear a trip — re-arming is a human
    decision via reset().
    """
    global _last_beat
    with _lock:
        _last_beat = _now()


def tripped() -> bool:
    """True once the watchdog has tripped, until reset() is called."""
    with _lock:
        return _tripped


def trip_reason() -> str:
    """Why the watchdog tripped ('' when it hasn't)."""
    with _lock:
        return _trip_reason


_rearm_warning = ""

REARM_FLAG_WARNING = "stop flag still set — remove it to re-arm"


def rearm_warning() -> str:
    """The standing re-arm warning ('' when none) — set by reset()
    when the stop flag is still present (H23c)."""
    with _lock:
        return _rearm_warning


def reset() -> bool:
    """Re-arm after a trip: clears the flag and restarts the beat clock.

    H23c: while data/nova_stop.flag is still present, re-arming is
    REFUSED with a warning instead of succeeding and instantly
    re-tripping on the next poll — the trip stays in force and
    rearm_warning()/status() carry "stop flag still set — remove
    it to re-arm" until the file is deleted and reset() is called
    again. Returns True when re-armed, False when the flag blocked
    it (callers that ignore the return keep working)."""
    global _tripped, _trip_reason, _last_beat, _rearm_warning
    try:
        flag_path = _stop_flag_path or _default_stop_flag_path()
        flag_present = flag_path.exists()
    except Exception:  # noqa: BLE001 — an unreadable path reads as absent
        flag_present = False
    if flag_present:
        with _lock:
            _rearm_warning = REARM_FLAG_WARNING
        log.warning("failsafe re-arm refused: %s", REARM_FLAG_WARNING)
        return False
    with _lock:
        _tripped = False
        _trip_reason = ""
        _rearm_warning = ""
        _last_beat = _now()
    log.info("failsafe re-armed by reset()")
    return True


def status() -> dict:
    """Snapshot for doctor/Settings display."""
    with _lock:
        return {
            "running": _thread is not None and _thread.is_alive(),
            "tripped": _tripped,
            "trip_reason": _trip_reason,
            "timeout_s": _timeout_s,
            "seconds_since_beat": (
                None if _last_beat is None else round(_now() - _last_beat, 2)
            ),
            "stop_flag_path": str(_stop_flag_path or _default_stop_flag_path()),
            "rearm_warning": _rearm_warning,
            # Inputs the last trip could NOT release (empty when the
            # last release was clean or nothing was held): a release
            # that cannot happen fails honestly here instead of
            # vanishing — see _release_inputs().
            "release_failures": list(_last_release_failures),
        }


def last_release_failures() -> list:
    """Idents the last trip could NOT release (a copy; [] when the
    last release was clean or nothing was held). Never raises."""
    with _lock:
        return list(_last_release_failures)


# ---------------------------------------------------------------------------
# Tripping
# ---------------------------------------------------------------------------


# Lock keys (v5.45, H23b): CapsLock / NumLock / ScrollLock are
# STATE TOGGLES, not held keys. Sending them a key-up (or any
# synthetic event) during the release sweep can FLIP the user's
# lock state — the watchdog must never change user state while
# rescuing it — so they are excluded from the release list.
_LOCK_KEY_NAMES = frozenset({"caps_lock", "num_lock", "scroll_lock"})


def _is_lock_key(key) -> bool:
    # A held key arrives as a virtual-key int (the ctypes hotkey
    # path registers VK codes) or as a name string / key object.
    if isinstance(key, int):
        try:
            from .desktop import cursor_input as _ci
        except Exception:  # noqa: BLE001 — a broken import reads as "not a lock"
            return key in (0x14, 0x90, 0x91)
        return key in _ci._LOCK_VKS
    name = getattr(key, "name", None) or str(key)
    return str(name).strip().lower().replace(" ", "_") in _LOCK_KEY_NAMES


# ---------------------------------------------------------------------------
# Pressed-input registry (v5.48, programme item 26 — A18)
#
# LEGACY CONTRACT CHANGE, with reason: before v5.48,
# _release_inputs() released EVERY known key plus all three
# mouse buttons on a trip — a blanket sweep that sent key-ups
# for inputs VYUK had never pressed (harmless for most keys,
# but pure noise, and the reason the H23b lock-key carve-out
# had to exist at all). v5.48 (programme item 26) replaces the
# sweep with precision: press sites REGISTER what they hold
# (note_input_pressed) and unregister on their normal release
# (note_input_released); a trip releases ONLY the tracked
# entries. An empty registry releases NOTHING — that is the
# item's point: if VYUK is not holding anything, a trip has
# nothing to rescue. (Suites written against the blanket sweep
# — test_v545_h23 section B, test_v542_h14 section C — encode
# the OLD contract and fail against this one by design.)
#
# Ident scheme: each entry is keyed by (kind, ident) where kind
# is "key" or "button" and ident is a STABLE STRING from
# input_ident() — a virtual-key code's canonical name ("ctrl"),
# a button name ("left"), a key object's .name / .char, else
# str(obj). The registry stores the original object alongside
# the ident and _release_inputs passes THAT object to the release
# path, so a held key's VK code / a button's name round-trips
# exactly; fakes that record release() arguments see the very
# object that was pressed.
#
# keyboard.type() is deliberately NOT tracked: it is transient
# per-character injection with no hold interval — at any trip
# instant there is no held key to release, and registering each
# character would re-create the blanket sweep under a new name.
# ---------------------------------------------------------------------------

_pressed_lock = threading.Lock()
_pressed: dict = {}  # (kind, ident) -> (kind, ident, obj)


def input_ident(obj) -> str:
    """The stable registry ident for a key/button object: a
    virtual-key code's canonical name ("ctrl" for 0x11), a button
    name ("left"), a key object's .name / .char, else str(obj).
    Never raises."""
    try:
        if isinstance(obj, int):
            try:
                from .desktop import cursor_input as _ci

                return str(_ci._NAME_BY_VK.get(
                    obj, "VK 0x%02X" % obj))
            except Exception:  # noqa: BLE001
                return "VK 0x%02X" % obj
        name = getattr(obj, "name", None)
        if name:
            return str(name)
        char = getattr(obj, "char", None)
        if char:
            return str(char)
    except Exception:  # noqa: BLE001 — an ident must never break a press
        pass
    return str(obj)


def note_input_pressed(kind, ident, obj=None) -> None:
    """Register one input as currently held by VYUK (v5.48,
    programme item 26). Called by press sites right after a
    successful press. Never raises."""
    try:
        key = (str(kind), str(ident))
        with _pressed_lock:
            _pressed[key] = (key[0], key[1],
                             obj if obj is not None else ident)
    except Exception:  # noqa: BLE001 — tracking must never break input
        pass


def note_input_released(kind, ident) -> None:
    """Unregister one input after its normal release (v5.48,
    programme item 26): a completed press is never re-released
    by a later trip. Never raises."""
    try:
        with _pressed_lock:
            _pressed.pop((str(kind), str(ident)), None)
    except Exception:  # noqa: BLE001
        pass


def tracked_pressed() -> list:
    """Snapshot of the registry as a sorted list of
    (kind, ident) pairs — the test surface for item 26."""
    with _pressed_lock:
        return sorted(_pressed.keys())


def clear_pressed_registry() -> None:
    """Drop every tracked entry WITHOUT releasing anything
    (test isolation / re-arm hygiene). Never raises."""
    try:
        with _pressed_lock:
            _pressed.clear()
    except Exception:  # noqa: BLE001
        pass


def _release_inputs() -> list:
    """Release ONLY the inputs the registry says VYUK is holding
    (v5.48, programme item 26 — see the registry section above
    for the legacy-contract-change reason). Returns the list of
    idents that could NOT be released (empty when the release was
    clean). Never raises. Lock keys are NEVER released (H23b): if
    one is somehow tracked, it is skipped, exactly as the old
    sweep skipped it. The registry is cleared either way — after
    a trip nothing is considered held. An empty registry releases
    nothing.

    The release goes through the native SendInput path
    (core/desktop/cursor_input.py): a key's virtual-key code gets
    a key-up, a button's name gets a button-up. When an injected
    backend is present (tests), its release() is used instead.
    A release that cannot happen FAILS HONESTLY: the ident is
    recorded in _last_release_failures (surfaced by status() and
    last_release_failures()) and named in a warning — it never
    vanishes into a silent skip.
    """
    global _mouse, _keyboard, _last_release_failures
    with _pressed_lock:
        entries = list(_pressed.values())
        _pressed.clear()
    if not entries:
        with _lock:
            _last_release_failures = []
        return []
    with _lock:
        mouse, keyboard = _mouse, _keyboard
    failures: list = []
    if mouse is not None or keyboard is not None:
        # Injected backend (tests): release through it, one entry
        # at a time so one failure never abandons the rest.
        for kind, ident, obj in entries:
            if kind == "key" and _is_lock_key(obj):
                continue  # H23b: releasing a toggle flips user state
            target = keyboard if kind == "key" else mouse
            if target is None:
                failures.append(ident)
                continue
            try:
                target.release(obj)
            except Exception:  # noqa: BLE001 — a failed release is recorded, not raised
                failures.append(ident)
    else:
        # Production path: native SendInput key-up / button-up.
        try:
            from .desktop import cursor_input as _ci
        except Exception as exc:  # noqa: BLE001 — no release path at all
            failures = [ident for _kind, ident, _obj in entries
                        if not (_kind == "key"
                                and _is_lock_key(_obj))]
            log.warning(
                "failsafe: could not release held input — no release "
                "path available (%s); unreleased: %s",
                exc, ", ".join(failures) or "none")
            with _lock:
                _last_release_failures = list(failures)
            return failures
        for kind, ident, obj in entries:
            if kind == "key" and _is_lock_key(obj):
                continue  # H23b: releasing a toggle flips user state
            try:
                if kind == "key":
                    vk = _resolve_release_vk(
                        obj, _ci._VK_MAP, _ci._VK_CHAR_MAP)
                    ok = (vk is not None
                          and _ci._sendinput_key(vk, key_up=True))
                elif kind == "button":
                    name = _resolve_release_button(obj)
                    ok = (name is not None
                          and _ci._sendinput_button_up(name))
                else:
                    ok = False
            except Exception:  # noqa: BLE001 — a failed release is recorded, not raised
                ok = False
            if not ok:
                failures.append(ident)
    if failures:
        log.warning(
            "failsafe: could not release held input: %s — %s may "
            "still be physically down; release it by hand",
            ", ".join(failures), ", ".join(failures))
    with _lock:
        _last_release_failures = list(failures)
    return failures


def _resolve_release_vk(obj, vk_map, vk_char_map):
    """Virtual-key code for a tracked key entry, or None when the
    entry cannot be resolved to a key. Ints are VK codes already;
    names resolve through the VK table; key objects resolve via
    their .name / .char. Never raises."""
    try:
        if isinstance(obj, int):
            return obj
        if isinstance(obj, str):
            name = obj.strip().lower().replace(" ", "_")
            if name in vk_map:
                return vk_map[name]
            if len(name) == 1 and name.isalnum():
                return ord(name.upper())
            if len(name) == 1 and name in vk_char_map:
                return vk_char_map[name]
            return None
        name = getattr(obj, "name", None) or getattr(obj, "char", None)
        if name:
            return _resolve_release_vk(str(name), vk_map, vk_char_map)
    except Exception:  # noqa: BLE001 — unresolvable reads as None
        pass
    return None


def _resolve_release_button(obj):
    """Button name ("left" / "right" / "middle") for a tracked
    button entry, or None when it cannot be resolved. Never
    raises."""
    try:
        if isinstance(obj, str):
            name = obj.strip().lower()
            return name if name in ("left", "right", "middle") else None
        name = getattr(obj, "name", None)
        if name:
            return _resolve_release_button(str(name))
    except Exception:  # noqa: BLE001 — unresolvable reads as None
        pass
    return None


def _trip(reason: str) -> None:
    """Trip the watchdog: set the flag, release input, notify. Idempotent."""
    global _tripped, _trip_reason
    with _lock:
        if _tripped:
            return
        _tripped = True
        _trip_reason = reason
        callback = _trip_callback
        listeners = list(_trip_listeners)
    log.warning("failsafe TRIPPED: %s — releasing held input", reason)
    _release_inputs()
    if callback is not None:
        try:
            callback(reason)
        except Exception:  # noqa: BLE001 — a broken callback must not block the trip
            pass
    # v5.46 (programme item 5, A3): additive listeners (see
    # add_trip_listener) — each guarded individually, exactly
    # like the callback above, so one broken listener can
    # neither block the trip nor starve the rest.
    for listener in listeners:
        try:
            listener(reason)
        except Exception:  # noqa: BLE001 — a broken listener must not block the trip
            log.debug("failsafe trip listener failed", exc_info=True)
    # Command Deck Phase D: the trip is a real bus event (the
    # overlay's CONTROL RETURNED flash consumes it in Phase F).
    # The emit sits last and never raises, so it cannot disturb
    # the trip itself; _trip stays idempotent (a second trip is
    # a no-op above, so no duplicate event).
    emit_failsafe(reason)


def trip_now(reason: str = "manual trip") -> None:
    """Trip immediately (used by the stop-flag check and available to callers)."""
    _trip(reason)


# ---------------------------------------------------------------------------
# Bare cancel-all utterances (v5.42, H14)
#
# A tiny, deliberately TIGHT predicate shared by the local and lite
# sessions' pre-model checks: it matches only an utterance that is
# NOTHING BUT a stop phrase — "stop", "stop everything", "cancel
# everything", "enough" — optionally prefixed by her name ("vyuk
# stop"). Anything carrying an object or extra words ("stop the
# music", "please stop the download", "stopwatch") is a real command
# for the brain / intents and must NEVER match: swallowing one would
# be worse than missing a cancel. Normalisation is case, surrounding
# whitespace, and trailing sentence punctuation only.
# ---------------------------------------------------------------------------

_CANCEL_ALL_BASE = frozenset({
    "stop",
    "stop everything",
    "cancel everything",
    "enough",
})

_CANCEL_ALL_FORMS = _CANCEL_ALL_BASE | {
    f"vyuk {phrase}" for phrase in _CANCEL_ALL_BASE
}


def is_cancel_all_utterance(text) -> bool:
    """True when `text` is ONLY a bare cancel-all phrase (see above)."""
    try:
        normalized = " ".join(str(text or "").lower().split())
    except Exception:  # noqa: BLE001 — a predicate must never raise
        return False
    normalized = normalized.strip(" .,!?")
    # A comma after her name ("vyuk, stop") is punctuation, not words.
    normalized = normalized.replace(",", " ")
    normalized = " ".join(normalized.split()).strip(" .,!?")
    return normalized in _CANCEL_ALL_FORMS


def _monitor_loop(gen=None) -> None:
    # Voice-control batch (2026-11-16, W3): the loop exits
    # when its generation is retired, not merely when the stop
    # event is set — a cleared event can never resurrect a
    # sleeping old monitor.
    while not _stop_event.is_set() and (gen is None or gen == _gen):
        try:
            flag_path = _stop_flag_path or _default_stop_flag_path()
            if flag_path.exists():
                _trip(f"stop flag file present ({flag_path})")
            else:
                with _lock:
                    last = _last_beat
                    timeout = _timeout_s
                if last is not None and (_now() - last) > timeout:
                    _trip(f"no heartbeat for over {timeout:.1f}s — the app may be hung")
        except Exception:  # noqa: BLE001 — the watchdog must never die quietly
            log.debug("failsafe monitor iteration failed", exc_info=True)
        _stop_event.wait(_poll_s)

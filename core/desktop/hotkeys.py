"""
Global Hotkey Daemon for Voice AI Assistant.
Registers system-wide hotkeys so VYUK can be triggered from any application.
"""

import sys
import threading
from collections.abc import Callable

# Combo tables (v5.42, H14): one definition per action, shared by the
# combo dispatch (key-name sets) and the Windows RegisterHotKey
# backend (modifier mask + virtual-key code), so the two can
# never drift apart. The panic combo is Ctrl+Alt+Break
# (VK_PAUSE 0x13 to Win32).
_PTT_KEYS = {"ctrl", "shift", "n"}
_SCREEN_KEYS = {"ctrl", "shift", "s"}
_MUTE_KEYS = {"ctrl", "shift", "m"}
_SUMMON_KEYS = {"ctrl", "shift", "space"}
_PANIC_KEYS = {"ctrl", "alt", "pause"}

_MOD_CONTROL = 0x0002
_MOD_ALT = 0x0001
_MOD_SHIFT = 0x0004
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012

# Win32 hotkey id -> action name (RegisterHotKey backend).
_WIN32_HOTKEYS = {
    1: ("ptt", _MOD_CONTROL | _MOD_SHIFT | _MOD_NOREPEAT, 0x4E),      # N
    2: ("screen", _MOD_CONTROL | _MOD_SHIFT | _MOD_NOREPEAT, 0x53),   # S
    3: ("mute", _MOD_CONTROL | _MOD_SHIFT | _MOD_NOREPEAT, 0x4D),     # M
    4: ("summon", _MOD_CONTROL | _MOD_SHIFT | _MOD_NOREPEAT, 0x20),   # Space
    5: ("panic", _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT, 0x13),      # Pause/Break
}


class HotkeyDaemon:
    """
    Registers global hotkeys that work even when the window is not focused.
    Hotkeys:
        Ctrl+Shift+N  -> toggle push-to-talk
        Ctrl+Shift+S  -> capture screen and send to assistant
        Ctrl+Shift+M  -> toggle mute
        Ctrl+Shift+Space -> summon / hide the main window (v4.1)
        Ctrl+Alt+Break (Pause) -> panic stop (v5.42, H14)

    Backends (v5.42): on Windows the combos go through the Win32
    RegisterHotKey API, so the OS CONSUMES them — they no longer also
    reach the foreground app (a raw key listener never suppressed,
    so Ctrl+Shift+N used to open a Chrome incognito window too).
    RegisterHotKey is per-combo, unlike a listener's all-or-nothing
    `suppress`. RegisterHotKey owns Windows hotkeys outright: when
    registration fails there is no fallback listener — global
    hotkeys are honestly unavailable, never silently half-working.
    Off Windows there is no hotkey backend in this build.
    The daemon stays policy-free for every action EXCEPT panic
    (v5.46, programme item 5, A3): _fire trips the failsafe
    itself, on this daemon's own thread, BEFORE invoking the
    window's panic callback — a frozen GUI must not be able to
    delay the stop by queueing it behind a stuck event loop.
    """

    def __init__(self):
        self._running = False
        self._pressed = set()
        self._ptt_active = False
        self._summon_active = False
        self._panic_active = False
        # Which backend is live: "win32" or None. Tests read this
        # and can force a backend by patching _use_win32_backend /
        # _start_win32_backend on the instance.
        self._backend: str | None = None
        self._win32_thread_id: int | None = None

        # Callbacks — wired up by the main window
        self.on_ptt_start: Callable[[], None] | None = None
        self.on_ptt_stop: Callable[[], None] | None = None
        self.on_screen_capture: Callable[[], None] | None = None
        self.on_toggle_mute: Callable[[], None] | None = None
        self.on_summon: Callable[[], None] | None = None
        self.on_panic: Callable[[], None] | None = None

    # -- backend selection (fakeable seams for the suite) ------------

    def _use_win32_backend(self) -> bool:
        """True on Windows, where RegisterHotKey should own the combos."""
        return sys.platform.startswith("win")

    def start(self):
        """Starts the global hotkey listener in a background thread.

        Windows only, via RegisterHotKey. Anywhere else — or when
        registration fails — global hotkeys are honestly
        unavailable: there is no fallback listener.
        """
        self._running = True
        self._ptt_active = False
        self._summon_active = False
        self._panic_active = False
        if self._use_win32_backend():
            try:
                if self._start_win32_backend():
                    self._backend = "win32"
                    print("[Hotkeys] Global hotkeys registered via "
                          "RegisterHotKey (consumed by the OS): "
                          "Ctrl+Shift+N (PTT), Ctrl+Shift+S (Screen), "
                          "Ctrl+Shift+M (Mute), Ctrl+Shift+Space "
                          "(Summon), Ctrl+Alt+Break (Panic)")
                    return
            except Exception as e:  # noqa: BLE001 — unavailable, never die
                print(f"[Hotkeys] RegisterHotKey backend failed ({e}) "
                      "— global hotkeys are unavailable on this system.")
            self._backend = None
            return
        print("[Hotkeys] Global hotkeys are not available on this "
              "system (RegisterHotKey is a Windows API).")

    # -- shared dispatch (the Win32 backend lands here) --------------

    def _fire(self, action: str):
        """Invoke the callback registered for `action`, if any.
        A broken callback must never kill the listener thread.

        PANIC (v5.46, programme item 5, A3): both panic paths —
        the RegisterHotKey backend's _dispatch_win32 and the
        combo dispatch's _handle_press — funnel through here,
        so when the action is "panic" the failsafe is tripped
        FIRST, on the hotkey daemon's thread (guarded lazy
        import, in the hands' precedent: a failsafe that can't
        be consulted must not break the callback path), and only
        then is the window's on_panic callback invoked as the
        follow-up (mute / interrupt / caption)."""
        if action == "panic":
            try:
                from .. import failsafe  # Phase 3 D3: file now in core/desktop/; failsafe stays flat in core/

                failsafe.trip_now("panic hotkey")
            except Exception:  # noqa: BLE001 — the callback still fires
                pass
        callback = {
            "ptt_start": self.on_ptt_start,
            "screen": self.on_screen_capture,
            "mute": self.on_toggle_mute,
            "summon": self.on_summon,
            "panic": self.on_panic,
        }.get(action)
        if callback is not None:
            try:
                callback()
            except Exception:  # noqa: BLE001
                pass

    def _handle_press(self, key_n: str):
        """One normalized key press (the combo dispatch)."""
        if key_n == " ":
            key_n = "space"
        self._pressed.add(key_n)

        if _PTT_KEYS <= self._pressed and not self._ptt_active:
            self._ptt_active = True
            self._fire("ptt_start")
        elif _PANIC_KEYS <= self._pressed and not self._panic_active:
            # Edge-triggered like summon: holding Break must not
            # machine-gun the panic path; it re-arms on release.
            self._panic_active = True
            self._fire("panic")
        elif _SUMMON_KEYS <= self._pressed and not self._summon_active:
            # Edge-triggered: holding the combo must not machine-gun
            # the window toggle; it re-arms when a key is released.
            self._summon_active = True
            self._fire("summon")
        elif _SCREEN_KEYS <= self._pressed:
            self._fire("screen")
        elif _MUTE_KEYS <= self._pressed:
            self._fire("mute")

    def _handle_release(self, key_n: str):
        """One normalized key release (the combo dispatch)."""
        if key_n == " ":
            key_n = "space"
        self._pressed.discard(key_n)

        # Only stop PTT if one of the active PTT keys was released
        if self._ptt_active and (key_n in _PTT_KEYS):
            self._ptt_active = False
            if self.on_ptt_stop:
                try:
                    self.on_ptt_stop()
                except Exception:  # noqa: BLE001
                    pass

        if self._summon_active and (key_n in _SUMMON_KEYS):
            self._summon_active = False
        if self._panic_active and (key_n in _PANIC_KEYS):
            self._panic_active = False

    # -- Windows RegisterHotKey backend -------------------------------
    # The OS consumes a registered combo, so it never reaches the
    # foreground app. Registration and the message pump must live on
    # the SAME thread (the hotkeys are thread-scoped); stop() posts
    # WM_QUIT to that thread and the loop unregisters on the way out.
    # RegisterHotKey reports key-DOWN only: PTT's release is recovered
    # by polling GetAsyncKeyState until the combo lets go.

    def _dispatch_win32(self, hotkey_id: int):
        """One WM_HOTKEY from the Win32 backend (also the suite seam)."""
        entry = _WIN32_HOTKEYS.get(hotkey_id)
        if entry is None:
            return
        action = entry[0]
        if action == "ptt":
            if not self._ptt_active:
                self._ptt_active = True
                self._fire("ptt_start")
                watcher = threading.Thread(
                    target=self._poll_ptt_release_win32, daemon=True,
                    name="HotkeyPTTRelease")
                watcher.start()
        elif action == "panic":
            if not self._panic_active:
                self._panic_active = True
                self._fire("panic")
                self._panic_active = False
        else:
            self._fire(action)

    def _poll_ptt_release_win32(self):
        """Fire on_ptt_stop once Ctrl/Shift/N are no longer all held.

        Voice-control batch (2026-11-16, P1/P2): two races.
        P1 — the loop also exits when stop() clears _running, and
        the old code then fired on_ptt_stop into shutdown (a stop
        for a session that no longer exists). A stop that exits
        because the daemon is stopping fires nothing. P2 — a
        re-press that lands between the last "not held" read and
        the stop was swallowed (WM_HOTKEY saw _ptt_active still
        True and ignored it), so the user held the keys with the
        mic muted until they released and pressed again. The
        not-held read is re-checked once, immediately, before
        the break — keys held again mean the same hold continues
        and the poller keeps watching. The poll is 20 ms, not
        50, so the stale-read window is smaller.
        """
        def _held():
            try:
                import ctypes

                user32 = ctypes.windll.user32
                vks = (0x11, 0x10, 0x4E)  # VK_CONTROL, VK_SHIFT, 'N'
                return all(user32.GetAsyncKeyState(vk) & 0x8000
                           for vk in vks)
            except Exception:  # noqa: BLE001
                return False

        try:
            while self._running and self._ptt_active:
                threading.Event().wait(0.02)
                if not _held():
                    # Re-check at once: a re-press inside the
                    # poll window must continue the hold, not
                    # lose its start to a stop fired a blink
                    # later (P2).
                    if _held():
                        continue
                    break
        except Exception:  # noqa: BLE001 — release polling is best-effort
            pass
        if not self._running:
            return  # P1: stopping — no stop event into shutdown.
        if self._ptt_active:
            self._ptt_active = False
            if self.on_ptt_stop:
                try:
                    self.on_ptt_stop()
                except Exception:  # noqa: BLE001
                    pass

    def _start_win32_backend(self) -> bool:
        """Register all combos + pump messages on a daemon thread.

        Returns False when ctypes or any single registration
        fails; partial registrations are undone first so no combo
        is ever owned twice."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        ready = threading.Event()
        outcome: dict = {}

        class _MSG(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND),
                        ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM),
                        ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD),
                        ("pt", wintypes.POINT)]

        def _run():
            self._win32_thread_id = kernel32.GetCurrentThreadId()
            registered = []
            try:
                for hotkey_id, (_action, mods, vk) in _WIN32_HOTKEYS.items():
                    if not user32.RegisterHotKey(None, hotkey_id, mods, vk):
                        raise OSError(
                            f"RegisterHotKey failed for id {hotkey_id}")
                    registered.append(hotkey_id)
            except Exception as e:  # noqa: BLE001 — reported to start()
                for hotkey_id in registered:
                    try:
                        user32.UnregisterHotKey(None, hotkey_id)
                    except Exception:  # noqa: BLE001
                        pass
                outcome["error"] = e
                ready.set()
                return
            outcome["ok"] = True
            ready.set()
            msg = _MSG()
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):  # WM_QUIT or an error: stop pumping
                    break
                if msg.message == _WM_HOTKEY:
                    self._dispatch_win32(int(msg.wParam))
            for hotkey_id in registered:
                try:
                    user32.UnregisterHotKey(None, hotkey_id)
                except Exception:  # noqa: BLE001
                    pass

        t = threading.Thread(target=_run, daemon=True,
                             name="HotkeyDaemonWin32")
        t.start()
        if not ready.wait(timeout=2.0):
            return False
        return bool(outcome.get("ok"))

    def stop(self):
        self._running = False
        # Voice-control batch (2026-11-16, P1): the edge flags are
        # session state, not daemon state — a stop that leaves
        # _ptt_active set lets the release poller fire on_ptt_stop
        # into shutdown, and leaves summon/panic armed for the
        # next start. Clear all three here; the poller's own
        # _running check is the second half of the same fix.
        self._ptt_active = False
        self._summon_active = False
        self._panic_active = False
        if self._backend == "win32" and self._win32_thread_id:
            try:
                import ctypes

                ctypes.windll.user32.PostThreadMessageW(
                    self._win32_thread_id, _WM_QUIT, 0, 0)
            except Exception:  # noqa: BLE001
                pass
            self._win32_thread_id = None
        self._backend = None


hotkey_daemon = HotkeyDaemon()

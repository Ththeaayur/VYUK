"""GAPS-2: the failsafe input release's ctypes fallback.

The failsafe release used to depend on a third-party input
library that is not installed in either lab interpreter and is
no longer shipped at all. The release now goes through the
native Windows SendInput path (core/desktop/cursor_input.py):
a key's virtual-key code gets a key-up, a button's name gets a
button-up. This suite proves, with a fake user32 (the lab has
no real SendInput):

* a trip sends the SendInput key-up / button-up for exactly
  what the registry says is held — never a silent skip;
* a release that cannot happen fails honestly: the ident is
  recorded (last_release_failures / status) and named, the trip
  itself still lands, the registry still clears;
* lock keys are never released (H23b);
* the hotkey listener backend is gone — RegisterHotKey owns
  Windows hotkeys, and off Windows hotkeys are honestly
  unavailable;
* the duck-typed mouse handle (_WinMouse) dispatches through
  the same SendInput seam;
* zero references to the removed library remain in any .py
  file, requirements.txt, requirements.lock or
  THIRD_PARTY_LICENSES.md, and it is not importable;
* the census is unchanged (751 tools, 2053 params, 750
  closed, 749 grouped).

The removed library's name never appears literally in this
file — it is built as "pyn" + "put" — so the zero-reference
check can scan this file too.
"""

import ctypes
import os
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest import mock

from core import failsafe
from core.desktop import cursor_input as ci
from core.desktop import hotkeys as hk

_TREEROOT = Path(__file__).resolve().parent.parent
_REMOVED = "pyn" + "put"  # the removed third-party input library


# --- fake Windows input ----------------------------------------------------
# Mirrors production's _INPUT with the same wintypes types, so the
# field offsets match on any platform (on Linux wintypes.DWORD is
# 8 bytes, so the lab layout is 56 bytes, not the 40 of the
# Windows ABI; what the lab proves is what production ASKS
# SendInput to do, never the Windows ABI itself).

class _FakePoint(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _FakeKeybd(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _FakeMouseIn(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _FakeUnion(ctypes.Union):
    _fields_ = [("mi", _FakeMouseIn), ("ki", _FakeKeybd),
                ("_pad", ctypes.c_byte * 32)]


class _FakeInput(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _FakeUnion)]


class _FakeUser32:
    """Records SendInput calls as ("key", vk, key_up) and
    ("mouse", dwFlags, mouseData) tuples, and answers
    GetCursorPos from a scripted position. Key-ups for VKs in
    fail_key_up_vks are refused (accepted 0); when refuse_all
    is set, every SendInput is refused."""

    def __init__(self, pos=(123, 456), fail_key_up_vks=()):
        self.events = []
        self.pos = pos
        self.fail_key_up_vks = set(fail_key_up_vks)
        self.refuse_all = False

    def SendInput(self, n, inputs, size):
        if isinstance(inputs, ctypes.Array):
            # GAPS-17: keyboard batches arrive as arrays too (the
            # hub's _sendinput_keyboard). Decode each element by
            # its type — keyboard elements as ("key", vk, key_up)
            # with the same refusal logic as the single-struct
            # path, mouse elements as before — and return the
            # accepted prefix, the way real SendInput stops at
            # the first refused event. Every attempted event is
            # recorded, exactly as the single-struct path does.
            arr = ctypes.cast(inputs, ctypes.POINTER(_FakeInput))
            accepted = n
            for i in range(n):
                ev = arr[i]
                if ev.type == 1:
                    key_up = bool(ev.u.ki.dwFlags & 0x0002)
                    self.events.append(("key", ev.u.ki.wVk, key_up))
                    if accepted == n and (self.refuse_all
                                          or (key_up and ev.u.ki.wVk
                                              in self.fail_key_up_vks)):
                        accepted = i
                else:
                    self.events.append(("mouse", ev.u.mi.dwFlags,
                                        ev.u.mi.mouseData))
                    if accepted == n and self.refuse_all:
                        accepted = i
            return 0 if self.refuse_all else accepted
        ev = ctypes.cast(inputs, ctypes.POINTER(_FakeInput)).contents
        key_up = bool(ev.u.ki.dwFlags & 0x0002)
        self.events.append(("key", ev.u.ki.wVk, key_up))
        if self.refuse_all:
            return 0
        if key_up and ev.u.ki.wVk in self.fail_key_up_vks:
            return 0
        return n

    def GetCursorPos(self, pt):
        p = ctypes.cast(pt, ctypes.POINTER(_FakePoint)).contents
        p.x, p.y = self.pos
        return 1


class _FakeWindll:
    def __init__(self, user32):
        self.user32 = user32


class _fake_windows:
    """Context manager: install the fake windll and patch
    _is_windows True on the cursor hub, then restore both and
    clean the failsafe registry."""

    def __init__(self, fake):
        self.fake = fake
        self._had_windll = hasattr(ctypes, "windll")
        self._old_windll = getattr(ctypes, "windll", None)

    def __enter__(self):
        ctypes.windll = _FakeWindll(self.fake)
        self._patcher = mock.patch.object(ci, "_is_windows",
                                          return_value=True)
        self._patcher.start()
        return self.fake

    def __exit__(self, *exc):
        self._patcher.stop()
        if self._had_windll:
            ctypes.windll = self._old_windll
        else:
            try:
                del ctypes.windll
            except AttributeError:
                pass
        failsafe.reset()
        failsafe.clear_pressed_registry()
        return False


class _RecordingBackend:
    """Injected failsafe backend (tests only): records the raw
    objects it is asked to release."""

    def __init__(self):
        self.released = []

    def release(self, obj):
        self.released.append(obj)


def _register_key(vk):
    failsafe.note_input_pressed("key", failsafe.input_ident(vk), vk)


def _register_button(name):
    failsafe.note_input_pressed("button", failsafe.input_ident(name),
                                name)


class FailsafeWiringTests(unittest.TestCase):
    def setUp(self):
        failsafe._mouse = None
        failsafe._keyboard = None
        failsafe.reset()
        failsafe.clear_pressed_registry()

    def tearDown(self):
        failsafe._mouse = None
        failsafe._keyboard = None
        failsafe.reset()
        failsafe.clear_pressed_registry()

    def test_trip_sends_key_up_and_button_up(self):
        fake = _FakeUser32()
        with _fake_windows(fake):
            _register_key(0x11)  # ctrl
            _register_button("left")
            failsafe.trip_now("suite ctypes release")
            self.assertIn(("key", 0x11, True), fake.events)
            self.assertIn(("mouse", 0x0004, 0), fake.events)  # LEFTUP
            self.assertEqual(failsafe.last_release_failures(), [])
            self.assertEqual(failsafe.tracked_pressed(), [])

    def test_release_failure_is_recorded_not_skipped(self):
        # No fake windll: the lab really cannot release — the
        # failure must be recorded and named, never swallowed.
        _register_key(0x11)
        _register_button("left")
        failsafe.trip_now("suite honest failure")
        self.assertEqual(failsafe.last_release_failures(),
                         ["ctrl", "left"])
        self.assertEqual(failsafe.status().get("release_failures"),
                         ["ctrl", "left"])
        self.assertTrue(failsafe.tripped())
        self.assertEqual(failsafe.tracked_pressed(), [])

    def test_partial_failure_records_only_the_failed_key(self):
        fake = _FakeUser32(fail_key_up_vks={0x11})
        with _fake_windows(fake):
            _register_key(0x11)
            _register_button("left")
            failsafe.trip_now("suite partial failure")
            self.assertEqual(failsafe.last_release_failures(), ["ctrl"])
            # The button-up still went out despite the failed key-up.
            self.assertIn(("mouse", 0x0004, 0), fake.events)
            self.assertEqual(failsafe.tracked_pressed(), [])

    def test_lock_keys_are_never_released(self):
        fake = _FakeUser32()
        with _fake_windows(fake):
            _register_key(0x14)  # caps_lock — a toggle, H23b
            _register_key(0x11)  # ctrl — a normal held key
            failsafe.trip_now("suite lock keys")
            key_ups = [e for e in fake.events if e[0] == "key" and e[2]]
            self.assertEqual(key_ups, [("key", 0x11, True)])
            self.assertEqual(failsafe.last_release_failures(), [])
            self.assertEqual(failsafe.tracked_pressed(), [])

    def test_empty_registry_releases_nothing(self):
        fake = _FakeUser32()
        with _fake_windows(fake):
            failsafe.trip_now("suite empty registry")
            self.assertEqual(fake.events, [])
            self.assertEqual(failsafe.last_release_failures(), [])

    def test_injected_backend_receives_the_raw_vk(self):
        backend = _RecordingBackend()
        failsafe.set_input_backend(keyboard=backend)
        _register_key(0x11)
        failsafe.trip_now("suite injected backend")
        self.assertIn(0x11, backend.released)
        self.assertEqual(failsafe.tracked_pressed(), [])


class ListenerPinTests(unittest.TestCase):
    def test_no_listener_backend_in_hotkeys(self):
        src = (_TREEROOT / "core" / "desktop" / "hotkeys.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn(_REMOVED, src)
        self.assertNotIn("Listener", src)
        daemon = hk.HotkeyDaemon()
        self.assertFalse(hasattr(daemon, "_start_" + _REMOVED
                                 + "_backend"))
        self.assertFalse(hasattr(daemon, "_normalize_key"))

    def test_start_off_windows_leaves_no_backend(self):
        daemon = hk.HotkeyDaemon()
        daemon.start()
        try:
            self.assertIsNone(daemon._backend)
        finally:
            daemon.stop()

    def test_register_hotkey_owns_windows_hotkeys(self):
        src = (_TREEROOT / "core" / "desktop" / "hotkeys.py"
               ).read_text(encoding="utf-8")
        self.assertIn("RegisterHotKey", src)


class WinMouseDuckTypeTests(unittest.TestCase):
    def test_mouse_controller_honest_error_off_windows(self):
        mouse, button, err = ci._mouse_controller()
        self.assertIsNone(mouse)
        self.assertIsNone(button)
        self.assertIsInstance(err, str)
        self.assertIn("unavailable", err.lower())

    def test_read_cursor_position_none_off_windows(self):
        self.assertIsNone(ci._read_cursor_position())

    def test_sendinput_key_false_off_windows(self):
        self.assertFalse(ci._sendinput_key(0x11))
        self.assertFalse(ci._sendinput_key(0x11, key_up=True))

    def test_sendinput_button_up_false_off_windows(self):
        self.assertFalse(ci._sendinput_button_up("left"))
        self.assertFalse(ci._sendinput_button_up("no-such-button"))

    def test_winmouse_dispatches_through_sendinput(self):
        fake = _FakeUser32(pos=(123, 456))
        with _fake_windows(fake):
            with mock.patch.object(ci, "_virtual_bounds",
                                   return_value=(0, 0, 1920, 1080)):
                mouse, button, err = ci._mouse_controller()
                self.assertIsNone(err)
                self.assertEqual(button.left, "left")
                self.assertEqual(button.right, "right")
                self.assertEqual(button.middle, "middle")
                self.assertEqual(mouse.position, (123, 456))
                mouse.position = (100, 200)
                # MOVE | ABSOLUTE | VIRTUALDESK
                self.assertIn(("mouse", 0xC001, 0), fake.events)
                mouse.click("left", 1)
                self.assertIn(("mouse", 0x0002, 0), fake.events)  # LEFTDOWN
                self.assertIn(("mouse", 0x0004, 0), fake.events)  # LEFTUP
                mouse.press("right")
                mouse.release("right")
                self.assertIn(("mouse", 0x0008, 0), fake.events)  # RIGHTDOWN
                self.assertIn(("mouse", 0x0010, 0), fake.events)  # RIGHTUP
                mouse.scroll(0, 2)
                self.assertIn(("mouse", 0x0800, 240), fake.events)  # WHEEL x2

    def test_winmouse_refusal_raises(self):
        fake = _FakeUser32()
        fake.refuse_all = True
        with _fake_windows(fake):
            mouse, _button, _err = ci._mouse_controller()
            with self.assertRaises(RuntimeError):
                mouse.click("left", 1)


class ZeroReferencesTests(unittest.TestCase):
    def test_no_removed_library_string_in_any_py_file(self):
        hits = []
        for root, _dirs, files in os.walk(_TREEROOT):
            if "__pycache__" in root:
                continue
            for fn in files:
                if fn.endswith(".py"):
                    path = os.path.join(root, fn)
                    with open(path, encoding="utf-8",
                              errors="replace") as handle:
                        if _REMOVED in handle.read():
                            hits.append(os.path.relpath(path, _TREEROOT))
        self.assertEqual(hits, [])

    def test_no_removed_library_in_declarations(self):
        for rel in ("requirements.txt", "requirements.lock",
                    "THIRD_PARTY_LICENSES.md"):
            text = (_TREEROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn(_REMOVED, text, rel)

    def test_removed_library_not_importable(self):
        try:
            __import__(_REMOVED)
        except ImportError:
            return
        self.fail("the removed library imported successfully")

    def test_census_unchanged(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        from core.funnel.tools_registry import tools
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 756)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        self.assertEqual(len({d["name"] for d in decls}), 756)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        params = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(params, 2078)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 755)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 753)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine


if __name__ == "__main__":
    unittest.main()

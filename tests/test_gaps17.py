"""GAPS-17: one input engine (review flaw D-1 remainder + D-2
second half).

Every input dispatch in the tree goes through the shared hub
(core/desktop/cursor_input.py): the hub owns the only INPUT
struct and the only user32.SendInput call sites; uia_send,
media_control, the funnel volume keys and the virtual-desktop
chord all send through it. The deprecated keybd_event is gone
from core/.
"""
import ast
import ctypes
import pathlib
import re
import unittest

import core.desktop.cursor_input as hub
import core.desktop.uia_send as uia_send

TREE = pathlib.Path(__file__).resolve().parent.parent
CORE = TREE / "core"


class _FakeUser32:
    def __init__(self):
        self.calls = []
        self.accept = None
        self.raise_on_send = False

    def SendInput(self, n, arr, size):
        if self.raise_on_send:
            raise OSError("fake SendInput failure")
        self.calls.append((n, size))
        return n if self.accept is None else self.accept


class _FakeWindll:
    def __init__(self):
        self.user32 = _FakeUser32()


class _HubFake:
    """Installs a fake user32 + faked Windows on the hub."""

    def __init__(self, test):
        self.test = test
        self.fake = _FakeWindll()
        self._had_windll = hasattr(ctypes, "windll")
        self._old_windll = getattr(ctypes, "windll", None)
        self._old_is_win = hub._is_windows

    def __enter__(self):
        ctypes.windll = self.fake
        hub._is_windows = lambda: True
        return self.fake.user32

    def __exit__(self, *exc):
        hub._is_windows = self._old_is_win
        if self._had_windll:
            ctypes.windll = self._old_windll
        else:
            del ctypes.windll
        return False


class TestBatchSender(unittest.TestCase):
    def test_count_passthrough(self):
        with _HubFake(self) as user32:
            n = hub._sendinput_keyboard(
                [(0x41, 0, 0), (0x41, 0, 2), (0, 0x1E, 8)])
        self.assertEqual(n, 3)
        self.assertEqual(len(user32.calls), 1)
        self.assertEqual(user32.calls[0][0], 3)

    def test_short_count(self):
        with _HubFake(self) as user32:
            user32.accept = 1
            n = hub._sendinput_keyboard([(0x41, 0, 0), (0x41, 0, 2)])
        self.assertEqual(n, 1)

    def test_empty_list_sends_nothing(self):
        with _HubFake(self) as user32:
            n = hub._sendinput_keyboard([])
        self.assertEqual(n, 0)
        self.assertEqual(user32.calls, [])

    def test_non_windows_returns_zero(self):
        old = hub._is_windows
        hub._is_windows = lambda: False
        try:
            self.assertEqual(hub._sendinput_keyboard([(0x41, 0, 0)]), 0)
        finally:
            hub._is_windows = old

    def test_send_failure_never_raises(self):
        with _HubFake(self) as user32:
            user32.raise_on_send = True
            n = hub._sendinput_keyboard([(0x41, 0, 0)])
        self.assertEqual(n, 0)

    def test_wrappers_keep_bool_contract(self):
        with _HubFake(self):
            self.assertIs(hub._sendinput_key(0x41), True)
            self.assertIs(hub._sendinput_key(0x41, key_up=True), True)
            self.assertIs(hub._sendinput_scancode(0x1E), True)
        old = hub._is_windows
        hub._is_windows = lambda: False
        try:
            self.assertIs(hub._sendinput_key(0x41), False)
            self.assertIs(hub._sendinput_scancode(0x1E), False)
            self.assertIs(hub._sendinput_button_up("left"), False)
        finally:
            hub._is_windows = old


class TestRouting(unittest.TestCase):
    def setUp(self):
        self._old_key = hub._sendinput_key
        self.key_calls = []
        hub._sendinput_key = self._spy_key

    def tearDown(self):
        hub._sendinput_key = self._old_key

    def _spy_key(self, vk, key_up=False):
        self.key_calls.append((vk, key_up))
        return True

    def test_uia_send_routes_through_hub(self):
        calls = []
        real_kb = hub._sendinput_keyboard

        def spy(events):
            calls.append(list(events))
            return real_kb(events)

        old_kb = uia_send._sendinput_keyboard
        old_win = uia_send._is_windows
        old_ident = uia_send._foreground_identity
        old_current = uia_send._target_still_current
        uia_send._sendinput_keyboard = spy
        uia_send._is_windows = lambda: True
        uia_send._foreground_identity = lambda: ("fake", 1)
        uia_send._target_still_current = lambda ident: True
        try:
            with _HubFake(self):
                out = uia_send._sendinput_type("A")
        finally:
            uia_send._sendinput_keyboard = old_kb
            uia_send._is_windows = old_win
            uia_send._foreground_identity = old_ident
            uia_send._target_still_current = old_current
        self.assertEqual(calls, [[(0, 0x41, 4), (0, 0x41, 6)]])
        self.assertTrue(out.completed)
        self.assertEqual(out.chars_sent, 1)

    def test_media_control_routes(self):
        import core.desktop.control_system as csys
        old_win = csys._is_windows
        old_blocked = csys._input_blocked
        csys._is_windows = lambda: True
        csys._input_blocked = lambda: None
        try:
            csys.media_control("play_pause")
        finally:
            csys._is_windows = old_win
            csys._input_blocked = old_blocked
        self.assertEqual(self.key_calls, [(0xB3, False), (0xB3, True)])

    def test_funnel_volume_key_routes(self):
        from core.funnel.tools_gate import _send_key_event
        _send_key_event(0xAF)  # VK_VOLUME_MUTE
        self.assertEqual(self.key_calls, [(0xAF, False), (0xAF, True)])

    def test_virtual_desktop_chord_routes(self):
        import core.skills_desktop_prefs as prefs
        prefs._send_desktop_chord(0x27)  # VK_RIGHT
        self.assertEqual(self.key_calls,
                         [(0x5B, False), (0x11, False), (0x27, False),
                          (0x27, True), (0x11, True), (0x5B, True)])

    def test_scancode_user32_failure_returns_outcome_not_false(self):
        class _RaisingWindll:
            @property
            def user32(self):
                raise AttributeError("no user32")

        old = getattr(ctypes, "windll", None)
        had = hasattr(ctypes, "windll")
        old_win = uia_send._is_windows
        old_hook = uia_send._pydirectinput_typewrite_fn
        ctypes.windll = _RaisingWindll()
        uia_send._is_windows = lambda: True
        uia_send._pydirectinput_typewrite_fn = None
        try:
            res = uia_send._scancode_type("a")
        finally:
            if had:
                ctypes.windll = old
            else:
                del ctypes.windll
            uia_send._is_windows = old_win
            uia_send._pydirectinput_typewrite_fn = old_hook
        self.assertIsInstance(res, uia_send._SendOutcome)
        self.assertFalse(res.completed)


class TestSingleDefinition(unittest.TestCase):
    def test_exactly_one_input_struct_in_core(self):
        defs = []
        for p in CORE.rglob("*.py"):
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in ("_INPUT", "INPUT"):
                    defs.append(str(p.relative_to(TREE)))
        self.assertEqual(defs, ["core/desktop/cursor_input.py"])

    def test_no_sendinput_call_outside_hub(self):
        sites = []
        for p in CORE.rglob("*.py"):
            for i, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if re.search(r"user32\.SendInput\s*\(", line):
                    sites.append(f"{p.relative_to(TREE)}:{i}")
        self.assertEqual(len(sites), 2)
        self.assertTrue(all("cursor_input" in s for s in sites))

    def test_no_keybd_event_calls_in_core(self):
        sites = []
        for p in CORE.rglob("*.py"):
            for i, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if re.search(r"keybd_event\s*\(", line):
                    sites.append(f"{p.relative_to(TREE)}:{i}")
        self.assertEqual(sites, [])

    def test_hub_keyboard_bytes_equal_legacy_uia_send_bytes(self):
        from ctypes import wintypes

        def legacy_bytes(vk, scan, flags):
            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                            ("dwExtraInfo", ctypes.c_void_p)]

            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("u", _U)]

            ev = INPUT()
            ev.type = 1
            ev.u.ki.wVk = vk
            ev.u.ki.wScan = scan
            ev.u.ki.dwFlags = flags
            ev.u.ki.time = 0
            ev.u.ki.dwExtraInfo = None
            return bytes(ev)

        def hub_bytes(vk, scan, flags):
            ev = hub._INPUT()
            ev.type = 1
            ev.u.ki.wVk = vk
            ev.u.ki.wScan = scan
            ev.u.ki.dwFlags = flags
            ev.u.ki.time = 0
            ev.u.ki.dwExtraInfo = 0
            return bytes(ev)

        for (vk, scan, flags) in [(0x41, 0, 0), (0, 0x1E, 0x0008),
                                  (0, 0x1E, 0x000A), (0x11, 0, 0),
                                  (0, 0x41, 0x0004), (0, 0x41, 0x0006)]:
            self.assertEqual(hub_bytes(vk, scan, flags)[:24],
                             legacy_bytes(vk, scan, flags)[:24],
                             f"bytes differ for {(vk, scan, flags)}")


if __name__ == "__main__":
    unittest.main()

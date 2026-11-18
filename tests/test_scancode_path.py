"""GAPS-5: the scan-code rung on the generic press_hotkey path
(BLOCKS #8, narrowed).

A single punctuation character is resolved through the CURRENT
keyboard layout (VkKeyScanW -> VK + shift, MapVirtualKeyW ->
scan code) and sent as KEYEVENTF_SCANCODE events, because the VK
path cannot express these characters layout-independently
(_VK_CHAR_MAP hard-codes US-layout VKs). Named keys and
letters/digits stay on the VK path. Driven here with a fake
windll (the lab has no real SendInput) and a SYNTHETIC non-US
layout fixture — not a claim about any real layout's mapping:
'-' lives on the key with scan code 0x35 (VK 0xBF, Shift
required), '/' on scan 0x33 (VK 0xBC, no Shift), backtick is not
on the layout, and the Euro sign needs Ctrl+Alt (AltGr).
"""

import ctypes
import unittest
from ctypes import wintypes
from unittest import mock

from core import failsafe
from core.desktop import control_input
from core.desktop import cursor_input


# --- fake Windows input (same pattern as test_d6_hotkey_stuck) ------
# Mirrors production's _INPUT with the same wintypes types, so the
# field offsets match on any platform (on Linux wintypes.DWORD is
# 8 bytes, so the lab layout is 56 bytes, not the 40 of the
# Windows ABI; what the lab proves is what production ASKS
# SendInput to do, never the Windows ABI itself).

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


_FIXTURE_VKSCAN = {
    ord("-"): 0xBF | 0x0100,
    ord("/"): 0xBC,
    ord("?"): 0xBF | 0x0100,
    ord("`"): -1,
    ord("\u20ac"): 0x45 | 0x0600,
    ord("a"): 0x41,
}
_FIXTURE_SCAN = {
    0xBF: 0x35, 0xBC: 0x33, 0x10: 0x2A,
    0xBD: 0x0C, 0x41: 0x1E, 0x11: 0x1D, 0x43: 0x2E,
}

SCANCODE = 0x0008
KEYUP = 0x0002


class _FakeUser32:
    """Records keyboard SendInput calls as (vk, scan, flags).
    Scan-code key-ups for scans in fail_scan_ups are refused."""

    def __init__(self, fail_scan_ups=()):
        self.events = []
        self.fail_scan_ups = set(fail_scan_ups)

    def SendInput(self, n, inputs, size):
        if isinstance(inputs, ctypes.Array):
            # GAPS-17: keyboard batches arrive as arrays too (the
            # hub's _sendinput_keyboard). Decode each element by
            # its type — keyboard elements as ki tuples, mouse
            # elements as before — and return the accepted prefix,
            # the way real SendInput stops at the first refused
            # event (a refused scan-code key-up accepts nothing
            # after it, exactly as the single-struct path did).
            arr = ctypes.cast(inputs, ctypes.POINTER(_FakeInput))
            for i in range(n):
                ev = arr[i]
                if ev.type == 1:
                    rec = (ev.u.ki.wVk, ev.u.ki.wScan, ev.u.ki.dwFlags)
                    self.events.append(rec)
                    if (rec[2] & SCANCODE and rec[2] & KEYUP
                            and rec[1] in self.fail_scan_ups):
                        return i
                else:
                    self.events.append(("mouse", ev.u.mi.dwFlags))
            return n
        ev = ctypes.cast(inputs, ctypes.POINTER(_FakeInput)).contents
        rec = (ev.u.ki.wVk, ev.u.ki.wScan, ev.u.ki.dwFlags)
        self.events.append(rec)
        if (rec[2] & SCANCODE and rec[2] & KEYUP
                and rec[1] in self.fail_scan_ups):
            return 0
        return n

    def VkKeyScanW(self, ch):
        return _FIXTURE_VKSCAN.get(ch, -1)

    def MapVirtualKeyW(self, vk, _map_type):
        return _FIXTURE_SCAN.get(vk, 0)


class _BareUser32:
    """SendInput only — no layout APIs at all."""

    def __init__(self):
        self.events = []

    def SendInput(self, n, inputs, size):
        ev = ctypes.cast(inputs, ctypes.POINTER(_FakeInput)).contents
        self.events.append((ev.u.ki.wVk, ev.u.ki.wScan,
                            ev.u.ki.dwFlags))
        return n


class _FakeWindll:
    def __init__(self, user32):
        self.user32 = user32


def _text(out):
    return str(getattr(out, "message", out))


class ScancodePathTests(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeUser32()
        self._had_windll = hasattr(ctypes, "windll")
        self._old_windll = getattr(ctypes, "windll", None)
        ctypes.windll = _FakeWindll(self.fake)
        self._patchers = [
            mock.patch.object(control_input, "_routed_backend",
                              return_value=None),
            mock.patch.object(control_input, "_input_blocked",
                              return_value=None),
            mock.patch.object(control_input, "_expectation_refusal",
                              return_value=None),
            mock.patch.object(control_input, "_is_windows",
                              return_value=True),
            mock.patch.object(cursor_input, "_is_windows",
                              return_value=True),
        ]
        for patcher in self._patchers:
            patcher.start()
        failsafe.reset()
        failsafe.clear_pressed_registry()

    def tearDown(self):
        for patcher in reversed(self._patchers):
            patcher.stop()
        if self._had_windll:
            ctypes.windll = self._old_windll
        else:
            try:
                del ctypes.windll
            except AttributeError:
                pass
        failsafe.reset()
        failsafe.clear_pressed_registry()

    def _press(self, keys):
        self.fake.events.clear()
        out = control_input.press_hotkey(keys)
        return _text(out), list(self.fake.events)

    # -- the hub primitives -------------------------------------------
    def test_sendinput_scancode_down(self):
        self.assertTrue(cursor_input._sendinput_scancode(0x35))
        self.assertEqual(self.fake.events[-1], (0, 0x35, SCANCODE))

    def test_sendinput_scancode_up(self):
        self.assertTrue(cursor_input._sendinput_scancode(0x35,
                                                         key_up=True))
        self.assertEqual(self.fake.events[-1],
                         (0, 0x35, SCANCODE | KEYUP))

    def test_resolver_maps_shifted_char(self):
        self.assertEqual(cursor_input._vk_scan_for_char("-"),
                         (0x35, 0x2A, 0xBF))

    def test_resolver_maps_plain_char(self):
        self.assertEqual(cursor_input._vk_scan_for_char("/"),
                         (0x33, 0, 0xBC))

    def test_resolver_none_unmappable(self):
        self.assertIsNone(cursor_input._vk_scan_for_char("`"))

    def test_resolver_none_altgr_char(self):
        self.assertIsNone(cursor_input._vk_scan_for_char("\u20ac"))

    def test_resolver_none_multichar(self):
        self.assertIsNone(cursor_input._vk_scan_for_char("ab"))

    def test_layout_available_with_full_fake(self):
        self.assertTrue(cursor_input._scan_layout_available())

    # -- press_hotkey through the rung ----------------------------------
    def test_press_dash_sends_scan_code_sequence(self):
        msg, ev = self._press("-")
        self.assertEqual(ev, [(0, 0x2A, SCANCODE),
                              (0, 0x35, SCANCODE),
                              (0, 0x35, SCANCODE | KEYUP),
                              (0, 0x2A, SCANCODE | KEYUP)])
        self.assertIn("Pressed hotkey: -", msg)

    def test_press_dash_sends_no_us_vk(self):
        _msg, ev = self._press("-")
        self.assertFalse(any(vk == 0xBD for vk, _s, _f in ev))

    def test_press_slash_needs_no_shift(self):
        msg, ev = self._press("/")
        self.assertEqual(ev, [(0, 0x33, SCANCODE),
                              (0, 0x33, SCANCODE | KEYUP)])
        self.assertIn("Pressed hotkey: /", msg)

    def test_press_ctrl_c_stays_vk(self):
        msg, ev = self._press("ctrl+c")
        self.assertEqual(ev, [(0x11, 0, 0), (0x43, 0, 0),
                              (0x43, 0, KEYUP), (0x11, 0, KEYUP)])
        self.assertIn("Pressed hotkey: ctrl+c", msg)

    def test_press_ctrl_dash_wraps_scan_in_vk_modifiers(self):
        msg, ev = self._press("ctrl+-")
        self.assertEqual(ev, [(0x11, 0, 0),
                              (0, 0x2A, SCANCODE),
                              (0, 0x35, SCANCODE),
                              (0, 0x35, SCANCODE | KEYUP),
                              (0, 0x2A, SCANCODE | KEYUP),
                              (0x11, 0, KEYUP)])
        self.assertIn("Pressed hotkey: ctrl+-", msg)

    def test_press_question_mark_now_presses(self):
        msg, ev = self._press("?")
        self.assertIn("Pressed hotkey: ?", msg)
        self.assertTrue(any(f & SCANCODE for _v, _s, f in ev))

    def test_press_backtick_refuses_and_sends_nothing(self):
        msg, ev = self._press("`")
        self.assertIn("Cannot press '`': this keyboard layout has "
                      "no key for it.", msg)
        self.assertEqual(ev, [])

    def test_press_euro_stays_unknown_and_sends_nothing(self):
        msg, ev = self._press("\u20ac")
        self.assertIn("Unknown key name", msg)
        self.assertEqual(ev, [])

    def test_registry_empty_after_clean_presses(self):
        self._press("-")
        self._press("ctrl+c")
        self.assertEqual(failsafe.tracked_pressed(), [])

    # -- the bare fake: VK fallback --------------------------------------
    def test_bare_fake_falls_back_to_vk(self):
        ctypes.windll = _FakeWindll(_BareUser32())
        self.assertFalse(cursor_input._scan_layout_available())
        self.assertIsNone(cursor_input._vk_scan_for_char("-"))
        bare = ctypes.windll.user32
        bare.events.clear()
        out = control_input.press_hotkey("-")
        self.assertIn("Pressed hotkey: -", _text(out))
        self.assertEqual(bare.events, [(0xBD, 0, 0),
                                       (0xBD, 0, KEYUP)])

    # -- the failsafe rescue for a scan-pressed key ----------------------
    def test_failed_scan_release_stays_tracked_and_trip_releases(self):
        self.fake.fail_scan_ups = {0x35}
        msg, _ev = self._press("-")
        self.assertIn("the release of - failed", msg)
        self.assertIn(("key", "VK 0xBF"), failsafe.tracked_pressed())
        self.fake.events.clear()
        failsafe.trip_now("scancode test rescue")
        self.assertTrue(any(vk == 0xBF and (fl & KEYUP)
                            and not (fl & SCANCODE)
                            for vk, _s, fl in self.fake.events))
        self.assertEqual(failsafe.tracked_pressed(), [])


if __name__ == "__main__":
    unittest.main()

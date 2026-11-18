"""D6 stuck-keys reliability note, pinned (fail-open audit item D6).

INTENTIONAL (D6 batch, 2026-11-05): these tests pin the CURRENT,
documented behaviour — they do not fix it. A failed hotkey release
is named in the message (the "Pressed hotkey:" prefix is kept) and
the key stays tracked in the failsafe registry so a trip can
release it. Changing the message or the tracking is a conscious
decision, not silent drift: update these tests with an INTENTIONAL
comment if that decision is ever taken.

GAPS-2: the release path is the native Windows SendInput path
(core/desktop/cursor_input.py), driven here with a fake windll
(the lab has no real SendInput); the injected-backend trip test
pins the failsafe's rescue seam. The linux/macos backends have no
input engine and answer honestly unavailable — they are NOT
covered here.
"""

import ctypes
import unittest
from ctypes import wintypes
from unittest import mock

from core import failsafe
from core import tool_outcome as outcome_mod
from core.desktop import control_input
from core.desktop import cursor_input


# --- fake Windows input (same pattern as test_tools_loopholes) --------
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


class _FakeUser32:
    """Records SendInput calls as ("key", vk, key_up) tuples.
    Key-ups for VKs in fail_key_up_vks are refused (accepted 0)
    — the stuck-key case — while everything else is accepted."""

    def __init__(self):
        self.events = []
        self.fail_key_up_vks = set()

    def SendInput(self, n, inputs, size):
        # The product ALWAYS passes an array of INPUT structs
        # (type=1, keyboard) — _sendinput_keyboard batches them in
        # one call. Decode every element as a key event (the old
        # array branch recorded only the first element as a mouse
        # event, so the fake never saw the key-ups the product
        # sends — the stuck-key tests could never observe them).
        if isinstance(inputs, ctypes.Array):
            for i in range(n):
                ev = ctypes.cast(
                    inputs, ctypes.POINTER(_FakeInput))[i]
                key_up = bool(ev.u.ki.dwFlags & 0x0002)
                self.events.append(("key", ev.u.ki.wVk, key_up))
                if key_up and ev.u.ki.wVk in self.fail_key_up_vks:
                    return 0
            return n
        ev = ctypes.cast(inputs, ctypes.POINTER(_FakeInput)).contents
        key_up = bool(ev.u.ki.dwFlags & 0x0002)
        self.events.append(("key", ev.u.ki.wVk, key_up))
        if key_up and ev.u.ki.wVk in self.fail_key_up_vks:
            return 0
        return n


class _FakeWindll:
    def __init__(self, user32):
        self.user32 = user32


class _RecordingController:
    """Injected failsafe backend (tests only): records the raw
    objects it is asked to release — virtual-key ints for keys."""

    released = []

    def release(self, key):
        type(self).released.append(key)


class D6HotkeyStuckTests(unittest.TestCase):
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
        self._old_keyboard_backend = failsafe._keyboard
        _RecordingController.released.clear()
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
        failsafe._keyboard = self._old_keyboard_backend
        failsafe.reset()
        failsafe.clear_pressed_registry()

    def test_release_failure_still_reports_success_and_stays_tracked(self):
        """D6, ctypes path: ctrl key-up refused -> message still
        reports success (same "Pressed hotkey:" prefix) AND names
        the stuck key, ctrl stays tracked (the trip rescue can
        reach it), c released normally and untracked.

        INTENTIONAL (Fix 7 closes D6): the exact message changed
        from bare "Pressed hotkey: ctrl+c" to the honest form that
        names the failed release; the success report and the
        tracking behaviour are unchanged.
        """
        self.fake.fail_key_up_vks = {0x11}
        msg = control_input.press_hotkey("ctrl+c")
        self.assertEqual(
            msg,
            "Pressed hotkey: ctrl+c — but the release of ctrl failed,"
            " so ctrl may still be physically down. The failsafe has"
            " it tracked and will release it on the next trip"
            " (takeover gesture, heartbeat starvation, or stop).")
        tracked = failsafe.tracked_pressed()
        self.assertIn(("key", "ctrl"), tracked)
        self.assertNotIn(("key", "c"), tracked)
        self.assertIn(("key", 0x11, True), self.fake.events)
        self.assertIn(("key", 0x43, True), self.fake.events)

    def test_normal_press_release_leaves_nothing_tracked(self):
        """Negative control: with no release failure, nothing stays
        tracked — tracking is specific to the failure, not the norm.

        INTENTIONAL (R1b): the clean-success message now carries the
        honest caveat (sent, not confirmed) and returns an unverified
        Outcome; the tracking behaviour is unchanged.
        """
        msg = control_input.press_hotkey("ctrl+c")
        self.assertTrue(outcome_mod.is_outcome(msg))
        self.assertFalse(msg.verified)
        self.assertEqual(
            str(msg),
            "Pressed hotkey: ctrl+c (sent — whether the app acted on it "
            "was not confirmed.)")
        self.assertEqual(failsafe.tracked_pressed(), [])
        self.assertEqual(self.fake.events,
                         [("key", 0x11, False), ("key", 0x43, False),
                          ("key", 0x43, True), ("key", 0x11, True)])

    def test_trip_releases_stuck_key(self):
        """D6 rescue, injected-backend seam: after a stuck ctrl, a
        failsafe trip releases it via the injected backend and
        clears the registry. The backend receives the raw
        virtual-key int (0x11) — the failsafe stores idents, the
        release path stores the releasable object."""
        self.fake.fail_key_up_vks = {0x11}
        control_input.press_hotkey("ctrl+c")
        self.assertIn(("key", "ctrl"), failsafe.tracked_pressed())
        failsafe.set_input_backend(keyboard=_RecordingController())
        failsafe.trip_now("d6 test")
        self.assertIn(0x11, _RecordingController.released)
        self.assertEqual(failsafe.tracked_pressed(), [])

    def test_trip_releases_stuck_key_via_ctypes(self):
        """D6 rescue, production path: after a stuck ctrl, a
        failsafe trip sends the SendInput key-up for VK 0x11 and
        clears the registry — no release is silently skipped."""
        self.fake.fail_key_up_vks = {0x11}
        control_input.press_hotkey("ctrl+c")
        self.assertIn(("key", "ctrl"), failsafe.tracked_pressed())
        self.fake.fail_key_up_vks = set()
        self.fake.events.clear()
        failsafe.trip_now("d6 test")
        self.assertIn(("key", 0x11, True), self.fake.events)
        self.assertEqual(failsafe.last_release_failures(), [])
        self.assertEqual(failsafe.tracked_pressed(), [])


if __name__ == "__main__":
    unittest.main()

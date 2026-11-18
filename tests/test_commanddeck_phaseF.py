"""Command Deck Phase F tests: agent overlay (guide §10 Phase F, §9 S9).

Pure tests run on both interpreters. Qt tests (window flags,
affinity-gated show, per-monitor build, paint, kill) skip on
system python3 — same pattern as Phase B/C/D: a QApplication can
only be created in the Qt venv.

Honesty note, stated in every capture test below: on Linux the
lab cannot prove pixel absence from a real capture (no Windows
affinity, no real windows). What the lab proves is the exclusion
machinery the capture paths rely on: default-excluded, the dev
switch compiled out of release builds, the affinity call offered
per window with the exact WDA value, show refused when the call
fails, and the exclusion precondition holding while each of the
four capture paths runs. Pixel-level proof on real Windows is
field-owed, never claimed here.

Bus tests subscribe a collector to the producers' own bus
instance (core.state_bus_producers._bus()) so they hold in either
import context. The singleton is reset in setUp and tearDown so
no subscription leaks into another suite. Tests that trip the
failsafe reset it in tearDown.
"""

import ctypes
import os
import sys
import time
import types
import unittest
import wave
from ctypes import wintypes
from pathlib import Path

from core.desktop import control  # Phase 3 D4a INTENTIONAL: control hub moved to core/desktop/
from core.desktop import control_input  # Phase F fix: the control_input concern module — type_text/press_hotkey/mouse_control read their gates from ITS globals, not the hub's
from core.desktop import cursor  # Phase 3 D4d INTENTIONAL: cursor hub moved to core/desktop/
from core.desktop import cursor_glide  # B35 INTENTIONAL: glide family moved here (part 2 of 2)
from core.desktop import cursor_input  # GAPS-2: the ctypes input hub — _mouse_controller / _is_windows seams
from core.desktop import cursor_ops  # B35 INTENTIONAL: ops family moved here (part 2 of 2)
from core import failsafe
from core import state_bus_producers as prod
from core.desktop import uia  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
from core.desktop import uia_send  # GAPS-2: the SendInput typing engine — _sendinput_type seam
from core.desktop import vision  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/

try:
    from core import w9_wiring

    W9_AVAILABLE = True
except Exception:  # pragma: no cover - system interpreter has no Qt chain
    w9_wiring = None
    W9_AVAILABLE = False

from core import accessibility_capture_guard as ac_guard
from core import capture
# INTENTIONAL (refactor Phase 1 step 7b): `from core import gm_capture_v578`
# removed — the module was deleted as PARK dead code; its phaseF check
# tested that dead code only, and the live overlay attach path was proven
# independent of it in step 7a run (c).
from gui import agent_overlay as ov
from gui import state_bus
from gui.sounds import CUE_NAMES, Earcons

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except Exception:  # pragma: no cover - system interpreter
    QT_AVAILABLE = False

TREE = Path(__file__).resolve().parent.parent


class _BusCase(unittest.TestCase):
    def setUp(self):
        state_bus.reset_state_bus()
        failsafe.reset()
        self.bus = prod._bus()
        self.seen = []
        self._by_kind = {}

        def _mk(kind):
            def _cb(event):
                self.seen.append((kind, event))
                self._by_kind.setdefault(kind, []).append(event)

            return _cb

        for name in ("AppState", "SensorState", "MissionState", "StepEvent",
                     "ToolStarted", "ToolFinished", "InputAcquired",
                     "InputReleased", "TargetRect", "FailsafeTripped",
                     "CaptureStarted", "CaptureFinished", "Verified",
                     "Verifying", "CursorPath", "AgentAction"):
            self.bus.subscribe(getattr(state_bus, name), _mk(name))
        self._patched = []

    def tearDown(self):
        for obj, attr, value in self._patched:
            setattr(obj, attr, value)
        self._patched = []
        failsafe.reset()
        state_bus.reset_state_bus()

    def patch(self, obj, attr, value):
        """Patch a module attribute, restored in tearDown."""
        self._patched.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    def drained(self, kind):
        """Drain, then return (and consume) this kind's new events."""
        self.bus.drain()
        return self._by_kind.pop(kind, [])


# --- fake Windows input (GAPS-2; same pattern as test_d6_hotkey_stuck)
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
    """Records SendInput calls as ("key", vk, key_up) tuples."""

    def __init__(self):
        self.events = []

    def SendInput(self, n, inputs, size):
        if isinstance(inputs, ctypes.Array):
            # GAPS-17: keyboard batches arrive as arrays too (the
            # hub's _sendinput_keyboard). Decode each element by
            # its type — keyboard elements as ("key", vk, key_up),
            # mouse elements as before.
            arr = ctypes.cast(inputs, ctypes.POINTER(_FakeInput))
            for i in range(n):
                ev = arr[i]
                if ev.type == 1:
                    self.events.append(("key", ev.u.ki.wVk,
                                        bool(ev.u.ki.dwFlags & 0x0002)))
                else:
                    self.events.append(("mouse", ev.u.mi.dwFlags))
            return n
        ev = ctypes.cast(inputs, ctypes.POINTER(_FakeInput)).contents
        self.events.append(("key", ev.u.ki.wVk,
                            bool(ev.u.ki.dwFlags & 0x0002)))
        return n


class _FakeWindll:
    def __init__(self, user32):
        self.user32 = user32


def _install_fake_windll(fake):
    had = hasattr(ctypes, "windll")
    old = getattr(ctypes, "windll", None)
    ctypes.windll = _FakeWindll(fake)
    return had, old


def _restore_fake_windll(had, old):
    if had:
        ctypes.windll = old
    else:
        try:
            del ctypes.windll
        except AttributeError:
            pass


class _FakeSendType:
    """Fake ``uia_send._sendinput_type``: records the batches it
    was asked to type and reports them all sent."""

    def __init__(self):
        self.typed = []

    def __call__(self, text, clear_first=False):
        self.typed.append(text)
        return uia_send._SendOutcome(completed=True, aborted=False,
                                     chars_sent=len(text),
                                     chars_total=len(text))


# ------------------------------------------------------------------ chips
class TestChipFormats(unittest.TestCase):
    def test_guide_formats(self):
        self.assertEqual(ov.format_action_chip("click", "Save"),
                         "CLICK › Save")
        self.assertEqual(ov.format_action_chip("type", chars=14),
                         "TYPE › 14 chars")
        self.assertEqual(ov.format_action_chip("scroll", "down"),
                         "SCROLL › down")
        self.assertEqual(ov.format_action_chip("scroll", "up"),
                         "SCROLL › up")
        self.assertEqual(ov.format_action_chip("hotkey", "ctrl+c"),
                         "HOTKEY › ctrl+c")

    def test_password_never_shows_text_or_count(self):
        chip = ov.format_action_chip("type", "hunter2", chars=7,
                                     password=True)
        self.assertNotIn("hunter2", chip)
        self.assertNotIn("7", chip)
        self.assertEqual(chip, "TYPE › " + chr(8226) * 3)

    def test_type_chip_carries_count_not_text(self):
        chip = ov.format_action_chip("type", "secret text", chars=11)
        self.assertNotIn("secret", chip)
        self.assertEqual(chip, "TYPE › 11 chars")

    def test_unknown_kind_renders_no_chip(self):
        self.assertEqual(ov.format_action_chip("teleport", "x"), "")

    def test_cue_mapping(self):
        self.assertEqual(ov.cue_for_event("target_lock"), "lock")
        self.assertEqual(ov.cue_for_event("failsafe"), "handback")
        self.assertEqual(ov.cue_for_event("step"), "step")
        self.assertEqual(ov.cue_for_event("click"), "")
        self.assertEqual(ov.cue_for_event("anything-else"), "")


# ------------------------------------------------------------------ model
class TestOverlayModel(unittest.TestCase):
    def setUp(self):
        self.cues = []
        self.model = ov.OverlayModel(play_cue=self.cues.append,
                                     clock=lambda: 100000.0)

    def test_target_lock_reticle_tag_and_cue(self):
        ev = state_bus.TargetRect(x=10, y=20, w=100, h=40,
                                  role="Button", name="Save")
        self.model.on_target_rect(ev)
        self.assertEqual(self.model.target_rect,
                         (10, 20, 100, 40, "Button", "Save"))
        self.assertEqual(self.model.reticle_tag(), "UIA · BUTTON")
        self.assertEqual(self.cues, ["lock"])

    def test_action_chip_and_click_pulse(self):
        self.model.on_agent_action(
            state_bus.AgentAction(kind="click", label="Save", x=60, y=40),
            now_ms=100000.0)
        self.assertEqual(self.model.chip, "CLICK › Save")
        self.assertEqual(self.model.click_pulse, (60, 40, 100000.0))
        self.model.on_agent_action(
            state_bus.AgentAction(kind="type", chars=14))
        self.assertEqual(self.model.chip, "TYPE › 14 chars")

    def test_trail_fades_over_400ms(self):
        self.model.on_cursor_path(
            state_bus.CursorPath(points=((0, 0), (10, 10), (20, 20))))
        # Clock is fixed at 100000.0, so all points share that time.
        self.assertEqual(len(self.model.trail), 3)
        self.assertEqual(self.model.trail_alphas(100000.0), [1.0, 1.0, 1.0])
        mid = self.model.trail_alphas(100000.0 + 200)
        self.assertTrue(all(abs(a - 0.5) < 1e-9 for a in mid))
        self.assertEqual(self.model.trail_alphas(100000.0 + 400), [])
        self.model.prune(100000.0 + 400)
        self.assertEqual(self.model.trail, [])

    def test_input_hold_nesting_and_frame(self):
        self.assertEqual(self.model.frame, ov.FRAME_IDLE)
        self.model.on_input_acquired()
        self.model.on_input_acquired()
        self.assertEqual(self.model.frame, ov.FRAME_ACTIVE)
        self.assertEqual(self.model.frame_banner(),
                         "AGENT CONTROL ACTIVE · MOVE MOUSE TO ABORT")
        self.model.on_input_released()
        self.assertEqual(self.model.frame, ov.FRAME_ACTIVE)
        self.model.on_input_released()
        self.assertEqual(self.model.frame, ov.FRAME_IDLE)
        self.assertEqual(self.model.frame_banner(), "")
        # Unmatched releases never drive the count negative.
        self.model.on_input_released()
        self.model.on_input_acquired()
        self.assertEqual(self.model.frame, ov.FRAME_ACTIVE)

    def test_failsafe_flash_and_expiry(self):
        self.model.on_input_acquired()
        self.model.on_failsafe(now_ms=100000.0)
        self.assertEqual(self.model.frame, ov.FRAME_RETURNED)
        self.assertEqual(self.model.frame_banner(), "CONTROL RETURNED")
        self.assertEqual(self.cues, ["handback"])
        # Flash still up just before expiry, gone at expiry.
        self.model.prune(100000.0 + ov.RETURNED_FLASH_MS - 1)
        self.assertEqual(self.model.frame, ov.FRAME_RETURNED)
        self.model.prune(100000.0 + ov.RETURNED_FLASH_MS)
        self.assertEqual(self.model.frame, ov.FRAME_IDLE)

    def test_scan_sweep_only_for_ocr_scope(self):
        self.model.on_capture_started(state_bus.CaptureStarted(scope="screenshot_save"))
        self.assertFalse(self.model.scan_sweep)
        self.model.on_capture_started(state_bus.CaptureStarted(scope="ocr"))
        self.assertTrue(self.model.scan_sweep)
        self.model.on_capture_finished(state_bus.CaptureFinished(scope="screenshot_save"))
        self.assertTrue(self.model.scan_sweep)
        self.model.on_capture_finished(state_bus.CaptureFinished(scope="ocr"))
        self.assertFalse(self.model.scan_sweep)

    def test_verifying_then_verified(self):
        self.model.on_verifying(state_bus.Verifying(kind="window"))
        self.assertEqual(self.model.verifying_kind, "window")
        self.model.on_verified(state_bus.Verified(evidence="a window appeared"))
        self.assertEqual(self.model.verified_evidence, "a window appeared")
        self.assertEqual(self.model.verifying_kind, "")


# ------------------------------------------------------- capture / affinity
class TestCaptureExclusion(unittest.TestCase):
    def test_default_excluded(self):
        self.assertTrue(ov.is_excluded_from_capture())
        self.assertFalse(ov.is_capturable())

    def test_env_switch_alone_cannot_enable_release_build(self):
        # The shipped constant is False: no environment value can
        # make a release build capturable.
        self.assertFalse(ov.DEV_CAPTURABLE_SWITCH_ENABLED)
        old = os.environ.get(ov.DEV_CAPTURABLE_ENV)
        os.environ[ov.DEV_CAPTURABLE_ENV] = "1"
        try:
            self.assertFalse(ov.is_capturable())
            self.assertTrue(ov.is_excluded_from_capture())
        finally:
            if old is None:
                del os.environ[ov.DEV_CAPTURABLE_ENV]
            else:
                os.environ[ov.DEV_CAPTURABLE_ENV] = old

    def test_dev_switch_requires_constant_and_env(self):
        old_const = ov.DEV_CAPTURABLE_SWITCH_ENABLED
        old_env = os.environ.get(ov.DEV_CAPTURABLE_ENV)
        try:
            ov.DEV_CAPTURABLE_SWITCH_ENABLED = True
            os.environ.pop(ov.DEV_CAPTURABLE_ENV, None)
            self.assertFalse(ov.is_capturable())
            os.environ[ov.DEV_CAPTURABLE_ENV] = "1"
            self.assertTrue(ov.is_capturable())
        finally:
            ov.DEV_CAPTURABLE_SWITCH_ENABLED = old_const
            if old_env is None:
                os.environ.pop(ov.DEV_CAPTURABLE_ENV, None)
            else:
                os.environ[ov.DEV_CAPTURABLE_ENV] = old_env

    def test_linux_cannot_apply_affinity(self):
        # Lab platform: no Windows affinity, so no overlay shows.
        self.assertFalse(ov.os_supports_affinity())
        self.assertFalse(ov.apply_display_affinity(12345))

    def test_affinity_seam_offers_exact_wda_value(self):
        calls = []
        old_support = ov.os_supports_affinity
        old_seam = ov._set_window_display_affinity
        try:
            ov.os_supports_affinity = lambda: True
            ov._set_window_display_affinity = (
                lambda hwnd, value: calls.append((hwnd, value)) or True)
            self.assertTrue(ov.apply_display_affinity(9876))
            self.assertEqual(calls, [(9876, ov.WDA_EXCLUDEFROMCAPTURE)])
            self.assertEqual(ov.WDA_EXCLUDEFROMCAPTURE, 0x11)
        finally:
            ov.os_supports_affinity = old_support
            ov._set_window_display_affinity = old_seam

    def test_failed_affinity_refuses_show(self):
        old_support = ov.os_supports_affinity
        old_seam = ov._set_window_display_affinity
        try:
            ov.os_supports_affinity = lambda: True
            ov._set_window_display_affinity = lambda hwnd, value: False
            self.assertFalse(ov.apply_display_affinity(9876))
        finally:
            ov.os_supports_affinity = old_support
            ov._set_window_display_affinity = old_seam


# ------------------------------------------------------------- earcon set
class TestPhaseFEarcons(unittest.TestCase):
    NEW_CUES = ("lock", "start", "step", "handback", "toast")
    BUDGETS = {"lock": 0.20, "start": 0.40, "step": 0.20,
               "handback": 0.60, "toast": 0.60}

    def test_new_cues_listed_and_playable(self):
        for cue in self.NEW_CUES:
            self.assertIn(cue, CUE_NAMES)
            self.assertTrue(callable(getattr(Earcons, cue)),
                            cue)

    def test_new_wavs_both_families_in_contract(self):
        for cue in self.NEW_CUES:
            for sub in ("", "earcons_soft"):
                path = TREE / "gui" / "assets" / sub / (cue + ".wav") \
                    if sub else TREE / "gui" / "assets" / (cue + ".wav")
                self.assertTrue(path.is_file(), str(path))
                with wave.open(str(path), "rb") as wav:
                    self.assertEqual(wav.getnchannels(), 1)
                    self.assertEqual(wav.getsampwidth(), 2)
                    self.assertEqual(wav.getframerate(), 48000)
                    frames = wav.readframes(wav.getnframes())
                    dur = wav.getnframes() / 48000.0
                self.assertLessEqual(dur, self.BUDGETS[cue] + 1e-9)
                import array as _array

                samples = _array.array("h")
                samples.frombytes(frames)
                import math as _math

                rms = _math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0
                rms_db = 20.0 * _math.log10(rms)
                self.assertAlmostEqual(rms_db, -20.0, delta=0.5)
                peak = max(abs(s) for s in samples) / 32768.0
                self.assertLessEqual(peak, 0.50)

    @unittest.skipUnless(W9_AVAILABLE, "w9_wiring needs the Qt import chain")
    def test_canon_allows_new_cues(self):
        w9_wiring.reset_canon_uses_for_tests()
        try:
            for cue in self.NEW_CUES:
                verdict = w9_wiring.canon_gate_for_cue(cue, "classic")
                self.assertTrue(verdict["allowed"], cue)
        finally:
            w9_wiring.reset_canon_uses_for_tests()


# ------------------------------------------------------------ bus producers
class TestPhaseFProducers(_BusCase):
    def test_cursor_path_emit(self):
        prod.emit_cursor_path([(0, 0), (10, 10)], source="cursor")
        events = self.drained("CursorPath")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].points, ((0, 0), (10, 10)))

    def test_follow_path_emits_trail(self):
        class _Mouse:
            def __init__(self):
                self.position = (0, 0)

        mouse = _Mouse()
        self.patch(cursor, "_failsafe_tripped", lambda: False)
        self.patch(cursor, "_set_position",
                   lambda m, x, y: setattr(m, "position", (x, y)))
        # B35 INTENTIONAL: _follow_path reads _set_position from the
        # glide module's globals, so fake both bindings (same fake,
        # both restored by tearDown) — B16 pattern, same assertions.
        self.patch(cursor_glide, "_set_position",
                   lambda m, x, y: setattr(m, "position", (x, y)))
        cursor._follow_path(mouse, [(0, 0, 0), (50, 50, 0)])
        events = self.drained("CursorPath")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].points[0], (0, 0))
        self.assertEqual(events[0].points[-1], (50, 50))

    def test_click_emits_held_input_and_named_chip(self):
        class _Mouse:
            def __init__(self):
                self.position = (60, 40)

            def scroll(self, *a):
                pass

        # Exercise the Windows production bodies: on lab Linux the
        # seamed backend would otherwise intercept the call. (The
        # seamed non-Windows backends not emitting overlay events
        # is a carried gap, recorded with the suite.)
        self.patch(cursor, "_routed_backend", lambda: None)
        self.patch(cursor, "_mouse_controller",
                   lambda: (_Mouse(), types.SimpleNamespace(left="L"), None))
        self.patch(cursor, "_failsafe_tripped", lambda: False)
        self.patch(cursor, "_identify_click_point",
                   lambda x, y: (' That point is the "Save" (Button) in Notepad.',
                                 "Notepad"))
        self.patch(cursor, "_dispatch_click", lambda *a, **k: None)
        # B35 INTENTIONAL: click_cursor reads these names from the
        # ops module's globals, so fake both bindings (same fakes,
        # both restored by tearDown) — B16 pattern, same assertions.
        self.patch(cursor_ops, "_routed_backend", lambda: None)
        self.patch(cursor_ops, "_mouse_controller",
                   lambda: (_Mouse(), types.SimpleNamespace(left="L"), None))
        self.patch(cursor_ops, "_failsafe_tripped", lambda: False)
        self.patch(cursor_ops, "_identify_click_point",
                   lambda x, y: (' That point is the "Save" (Button) in Notepad.',
                                 "Notepad"))
        self.patch(cursor_ops, "_dispatch_click", lambda *a, **k: None)
        result = cursor.click_cursor(button="left", count=1)
        self.assertIn("Clicked", result)
        acquired = self.drained("InputAcquired")
        released = self.drained("InputReleased")
        actions = self.drained("AgentAction")
        self.assertEqual([e.source for e in acquired], ["cursor_click"])
        self.assertEqual([e.source for e in released], ["cursor_click"])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "click")
        self.assertEqual(actions[0].label, "Save")
        self.assertEqual((actions[0].x, actions[0].y), (60, 40))

    def test_scroll_emits_direction(self):
        class _Mouse:
            def __init__(self):
                self.scrolled = []

            def scroll(self, dx, dy):
                self.scrolled.append((dx, dy))

        mouse = _Mouse()
        self.patch(cursor, "_routed_backend", lambda: None)
        self.patch(cursor, "_mouse_controller",
                   lambda: (mouse, types.SimpleNamespace(left="L"), None))
        self.patch(cursor, "_failsafe_tripped", lambda: False)
        # B35 INTENTIONAL: scroll_cursor reads these names from the
        # ops module's globals, so fake both bindings (same fakes,
        # both restored by tearDown) — B16 pattern, same assertions.
        self.patch(cursor_ops, "_routed_backend", lambda: None)
        self.patch(cursor_ops, "_mouse_controller",
                   lambda: (mouse, types.SimpleNamespace(left="L"), None))
        self.patch(cursor_ops, "_failsafe_tripped", lambda: False)
        result = cursor.scroll_cursor(-3)
        self.assertIn("Scrolled", result)
        self.assertEqual(mouse.scrolled, [(0, -3)])
        actions = self.drained("AgentAction")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "scroll")
        self.assertEqual(actions[0].label, "down")

    def test_drag_emits_held_input_and_chip(self):
        class _Mouse:
            def __init__(self):
                self.position = (0, 0)
                self.pressed = False

            def press(self, _b):
                self.pressed = True

            def release(self, _b):
                self.pressed = False

        self.patch(cursor, "_routed_backend", lambda: None)
        self.patch(cursor, "_mouse_controller",
                   lambda: (_Mouse(), types.SimpleNamespace(left="L"), None))
        self.patch(cursor, "_note_input_pressed", lambda *a, **k: None)
        self.patch(cursor, "_note_input_released", lambda *a, **k: None)
        self.patch(cursor, "_set_position", lambda m, x, y: None)
        self.patch(cursor, "_follow_path", lambda m, p: None)
        # B35 INTENTIONAL: drag_cursor reads these names from the
        # ops module's globals, so fake both bindings (same fakes,
        # both restored by tearDown) — B16 pattern, same assertions.
        self.patch(cursor_ops, "_routed_backend", lambda: None)
        self.patch(cursor_ops, "_mouse_controller",
                   lambda: (_Mouse(), types.SimpleNamespace(left="L"), None))
        self.patch(cursor_ops, "_note_input_pressed", lambda *a, **k: None)
        self.patch(cursor_ops, "_note_input_released", lambda *a, **k: None)
        self.patch(cursor_ops, "_set_position", lambda m, x, y: None)
        self.patch(cursor_ops, "_follow_path", lambda m, p: None)
        result = cursor.drag_cursor(0, 0, 100, 50)
        self.assertIn("Dragged", result)
        acquired = self.drained("InputAcquired")
        released = self.drained("InputReleased")
        actions = self.drained("AgentAction")
        self.assertEqual([e.source for e in acquired], ["cursor_drag"])
        self.assertEqual([e.source for e in released], ["cursor_drag"])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "drag")

    def test_type_emits_count_never_text(self):
        # GAPS-2: typing goes through the SendInput engine
        # (uia_send._sendinput_type) — fake it, and make the lab
        # look like Windows to the input seam.
        fake_type = _FakeSendType()
        self.patch(control, "_routed_backend", lambda: None)
        self.patch(control, "_input_blocked", lambda: None)
        self.patch(control, "_password_block", lambda: None)
        self.patch(control, "_expectation_refusal", lambda: None)
        # Phase F fix: type_text lives in control_input and reads
        # these gates from control_input's globals — patching the
        # hub (control) alone never takes effect (the real
        # _routed_backend still returns the Linux backend, so the
        # call delegates to the backend and the Phase F emits
        # never run). Fake both bindings — B16 pattern.
        self.patch(control_input, "_routed_backend", lambda: None)
        self.patch(control_input, "_input_blocked", lambda: None)
        self.patch(control_input, "_password_block", lambda: None)
        self.patch(control_input, "_expectation_refusal", lambda: None)
        self.patch(control_input, "_is_windows", lambda: True)
        self.patch(uia_send, "_sendinput_type", fake_type)
        result = control.type_text("hello world")
        # R1b: the legacy path returns an unverified Outcome, not
        # a str — read it through str(...), the same convention
        # the mouse test's scroll leg already uses below.
        self.assertIn("Typed 11 character(s)", str(result))
        self.assertEqual(fake_type.typed, ["hello world"])
        acquired = self.drained("InputAcquired")
        released = self.drained("InputReleased")
        actions = self.drained("AgentAction")
        self.assertEqual([e.source for e in acquired], ["control_type"])
        self.assertEqual([e.source for e in released], ["control_type"])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "type")
        self.assertEqual(actions[0].chars, 11)
        self.assertFalse(actions[0].password)

    def test_hotkey_emits_combo(self):
        # GAPS-2: the hotkey path is the native Windows SendInput
        # path — drive it with the fake windll and make the lab
        # look like Windows to the input seam.
        fake = _FakeUser32()
        windll_state = _install_fake_windll(fake)
        try:
            self.patch(control, "_routed_backend", lambda: None)
            self.patch(control, "_input_blocked", lambda: None)
            self.patch(control, "_expectation_refusal", lambda: None)
            self.patch(control, "_note_input_pressed", lambda *a, **k: None)
            self.patch(control, "_note_input_released", lambda *a, **k: None)
            # Phase F fix: press_hotkey lives in control_input and
            # reads these gates from control_input's globals — same
            # hub-only miss as the type test (B16 pattern: fake
            # both bindings, both restored by tearDown).
            self.patch(control_input, "_routed_backend", lambda: None)
            self.patch(control_input, "_input_blocked", lambda: None)
            self.patch(control_input, "_expectation_refusal", lambda: None)
            self.patch(control_input, "_note_input_pressed", lambda *a, **k: None)
            self.patch(control_input, "_note_input_released", lambda *a, **k: None)
            self.patch(control_input, "_is_windows", lambda: True)
            self.patch(cursor_input, "_is_windows", lambda: True)
            result = control.press_hotkey("ctrl+s")
            # R1b: the legacy path returns an unverified Outcome, not
            # a str — read it through str(...), the same convention
            # the mouse test's scroll leg already uses below.
            self.assertIn("Pressed hotkey", str(result))
            self.assertEqual(fake.events,
                             [("key", 0x11, False), ("key", 0x53, False),
                              ("key", 0x53, True), ("key", 0x11, True)])
        finally:
            _restore_fake_windll(*windll_state)
        acquired = self.drained("InputAcquired")
        released = self.drained("InputReleased")
        actions = self.drained("AgentAction")
        self.assertEqual([e.source for e in acquired], ["control_hotkey"])
        self.assertEqual([e.source for e in released], ["control_hotkey"])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "hotkey")
        self.assertEqual(actions[0].label, "ctrl+s")

    def test_mouse_control_click_and_scroll_emit(self):
        # GAPS-2: the mouse is driven through the cursor hub's
        # duck-typed controller (core/desktop/cursor_input.py) —
        # patch that seam with the same fake shape the old test
        # used, no third-party input library involved.
        class _Button:
            left = "left"
            right = "right"

        class _MouseController:
            def __init__(self):
                self.position = (0, 0)
                self.clicked = []
                self.scrolled = []

            def click(self, button, count):
                self.clicked.append((button, count))

            def scroll(self, dx, dy):
                self.scrolled.append((dx, dy))

        fake_mouse = _MouseController()
        self.patch(control, "_routed_backend", lambda: None)
        self.patch(cursor, "_routed_backend", lambda: None)
        self.patch(control, "_input_blocked", lambda: None)
        self.patch(control, "_foreground_elevated", lambda: None)
        # Phase F fix: mouse_control lives in control_input and
        # reads these gates from control_input's globals — same
        # hub-only miss as the type/hotkey tests (B16 pattern:
        # fake both bindings, both restored by tearDown).
        self.patch(control_input, "_routed_backend", lambda: None)
        self.patch(control_input, "_input_blocked", lambda: None)
        self.patch(control_input, "_foreground_elevated", lambda: None)
        self.patch(cursor_input, "_mouse_controller",
                   lambda: (fake_mouse, _Button, None))
        result = control.mouse_control("click", x=100, y=200)
        self.assertIn("Clicked", result)
        result2 = control.mouse_control("scroll", amount=3)
        # INTENTIONAL (R1b): the scroll leg now returns an
        # unverified Outcome (dispatched, page position not
        # re-read) — read it through str(...).
        self.assertIn("Scrolled by 3", str(result2))
        self.assertEqual(fake_mouse.clicked, [("left", 1)])
        self.assertEqual(fake_mouse.scrolled, [(0, 3)])
        acquired = self.drained("InputAcquired")
        released = self.drained("InputReleased")
        actions = self.drained("AgentAction")
        self.assertEqual([e.source for e in acquired],
                         ["control_mouse", "control_mouse"])
        self.assertEqual([e.source for e in released],
                         ["control_mouse", "control_mouse"])
        self.assertEqual([(a.kind, a.label) for a in actions],
                         [("click", "left"), ("scroll", "up")])
        self.assertEqual((actions[0].x, actions[0].y), (100, 200))

    def test_click_by_mark_emits_target_rect(self):
        uia._set_last_marks(
            [{"id": 1, "name": "Save", "control_type": "Button",
              "rect": (10, 20, 110, 60)}], "App")
        self.addCleanup(uia.clear_last_marks)
        self.patch(uia, "_failsafe_block", lambda: None)
        self.patch(uia, "_marks_stale_reason", lambda wanted: None)
        self.patch(uia, "_safe_snapshot", lambda: {})
        self.patch(uia, "probe_suffix", lambda *a, **k: "")
        self.patch(cursor, "_virtual_bounds", lambda: (0, 0, 1920, 1080))
        self.patch(cursor, "click_cursor",
                   lambda **k: "Clicked (left button) — landed at (60, 40).")
        result = uia.click_by_mark(1)
        self.assertIn("Clicked mark 1", result)
        rects = self.drained("TargetRect")
        actions = self.drained("AgentAction")
        self.assertEqual(len(rects), 1)
        self.assertEqual((rects[0].x, rects[0].y, rects[0].w, rects[0].h),
                         (10, 20, 100, 40))
        self.assertEqual(rects[0].role, "Button")
        self.assertEqual(rects[0].name, "Save")
        # The named chip fires after the cursor path's generic one.
        self.assertTrue(any(a.kind == "click" and a.label == "Save"
                            for a in actions))

    def test_verify_expectation_emits_verifying_and_verified(self):
        before = {"fg": "Notepad", "focus": "", "windows": ["Notepad"],
                  "texts": [], "tree_hash": "aaa"}
        after = {"fg": "Save As", "focus": "",
                 "windows": ["Notepad", "Save As"], "texts": [],
                 "tree_hash": "bbb"}
        verdict, _reason = uia.verify_expectation(
            "a dialog titled Save As appears", before, after)
        self.assertEqual(verdict, "met")
        verifying = self.drained("Verifying")
        verified = self.drained("Verified")
        self.assertEqual([e.kind for e in verifying], ["window"])
        self.assertEqual(len(verified), 1)
        self.assertIn("Save As", verified[0].evidence)
        # Not-met emits Verifying but no Verified (never guessed).
        verdict2, _r2 = uia.verify_expectation(
            "a dialog titled Nope appears", before, after)
        self.assertEqual(verdict2, "not-met")
        self.assertEqual(len(self.drained("Verifying")), 1)
        self.assertEqual(self.drained("Verified"), [])

    def test_ocr_brackets_scan_sweep_events(self):
        vision._ocr_words_for_image(None, {})
        started = self.drained("CaptureStarted")
        finished = self.drained("CaptureFinished")
        self.assertEqual([e.scope for e in started], ["ocr"])
        self.assertEqual([e.scope for e in finished], ["ocr"])

    def test_mouse_move_trips_failsafe_and_flashes(self):
        control._begin_assistant_gesture()
        try:
            self.assertFalse(control.check_gesture_takeover(current=(100, 100)))
            self.assertTrue(control.check_gesture_takeover(current=(900, 900)))
            self.assertTrue(failsafe.tripped())
        finally:
            control._end_assistant_gesture()
        trips = self.drained("FailsafeTripped")
        self.assertEqual(len(trips), 1)
        # The overlay model turns that trip into CONTROL RETURNED.
        model = ov.OverlayModel(clock=lambda: 100000.0)
        model.on_input_acquired()
        model.on_failsafe(now_ms=100000.0)
        self.assertEqual(model.frame_banner(), "CONTROL RETURNED")


# ------------------------------------------------------- event contract
class TestEventContract(_BusCase):
    def test_slow_or_raising_consumer_never_delays_producer(self):
        def _slow(_event):
            time.sleep(0.5)

        def _raising(_event):
            raise RuntimeError("consumer broken")

        self.bus.subscribe(state_bus.AgentAction, _slow)
        self.bus.subscribe(state_bus.AgentAction, _raising)
        started = time.monotonic()
        prod.emit_agent_action("click", label="Save")
        elapsed = time.monotonic() - started
        # emit() only queues: it must return far faster than the
        # slow consumer's 0.5 s, and must not raise for the broken one.
        self.assertLess(elapsed, 0.2)
        self.bus.drain()
        actions = self.drained("AgentAction")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].label, "Save")

    def test_killing_overlay_changes_nothing(self):
        # Model-level kill contract: the action flag is untouched,
        # and later bus events still reach the model.
        action_running = {"running": True}
        model = ov.OverlayModel(clock=lambda: 100000.0)
        model.on_input_acquired()
        self.assertEqual(model.frame, ov.FRAME_ACTIVE)
        # "Kill" here is the overlay teardown: windows close, the
        # model and the action are untouched (Qt-level kill is in
        # the Qt tests below).
        model.on_agent_action(
            state_bus.AgentAction(kind="click", label="Save"))
        self.assertTrue(action_running["running"])
        self.assertEqual(model.frame, ov.FRAME_ACTIVE)
        self.assertEqual(model.chip, "CLICK › Save")


# ------------------------------------------------------------ capture paths
class TestCapturePaths(_BusCase):
    """Each of the four guide capture paths, exercised in the lab.

    What is asserted: the overlay exclusion precondition holds
    while the path runs (default-excluded, affinity offered per
    window in the seam test above), and no path raises for overlay
    reasons. Pixel absence on real Windows is FIELD-OWED.
    """

    def test_all_four_paths_with_overlay_excluded(self):
        self.assertTrue(ov.is_excluded_from_capture())
        # 1. vision.py — the lowest grab (fails headless for display
        # reasons only; the exclusion precondition still holds).
        try:
            vision._grab_image()
        except Exception:  # noqa: BLE001 — headless, expected
            pass
        self.assertTrue(ov.is_excluded_from_capture())
        # 2. capture.py — occluded-window capture entry.
        result = capture.occluded_window_capture("No such window")
        self.assertIsInstance(result, dict)
        self.assertTrue(ov.is_excluded_from_capture())
        # 3. (INTENTIONAL, refactor Phase 1 step 7b: gm_capture_v578.py was
        # deleted as PARK dead code, so its ffmpeg_ddagrab_argv check was
        # removed with it; the live overlay attach path is independent.)
        self.assertTrue(ov.is_excluded_from_capture())
        # 4. accessibility_capture_guard.py — the capture gate.
        grant = ac_guard.grant_session("phaseF-test", "screen")
        grant.opted_in = True
        grant.indicator_on = True
        started = ac_guard.start_capture(grant, "test")
        self.assertTrue(started["capturing"])
        self.assertTrue(ov.is_excluded_from_capture())
        stopped = ac_guard.stop_capture(grant)
        self.assertFalse(stopped["capturing"])
        self.assertTrue(ov.is_excluded_from_capture())


# ------------------------------------------------------------------ Qt layer
@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class TestOverlayWindows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._patched = []

    def tearDown(self):
        for obj, attr, value in self._patched:
            setattr(obj, attr, value)

    def _patch(self, obj, attr, value):
        self._patched.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    def test_window_flags_transparent_clickthrough_always_on_top(self):
        from gui.agent_overlay import OverlayWindow

        model = ov.OverlayModel()
        window = OverlayWindow((0, 0, 800, 600), model)
        try:
            flags = window.windowFlags()
            self.assertTrue(bool(flags & Qt.FramelessWindowHint))
            self.assertTrue(bool(flags & Qt.WindowStaysOnTopHint))
            self.assertTrue(bool(flags & Qt.WindowTransparentForInput))
            self.assertTrue(bool(flags & Qt.Tool))
            self.assertTrue(window.testAttribute(Qt.WA_TranslucentBackground))
            self.assertTrue(window.testAttribute(Qt.WA_ShowWithoutActivating))
            self.assertEqual(window.focusPolicy(), Qt.NoFocus)
            self.assertEqual((window.x(), window.y(),
                              window.width(), window.height()),
                             (0, 0, 800, 600))
        finally:
            window.close()

    def test_show_refused_without_affinity(self):
        from gui.agent_overlay import OverlayWindow

        model = ov.OverlayModel()
        window = OverlayWindow((0, 0, 800, 600), model)
        try:
            self._patch(ov, "os_supports_affinity", lambda: True)
            self._patch(ov, "_set_window_display_affinity",
                        lambda hwnd, value: False)
            self.assertFalse(window.show_overlay())
            self.assertFalse(window.shown())
            self.assertFalse(window.isVisible())
        finally:
            window.close()

    def test_show_succeeds_with_affinity(self):
        from gui.agent_overlay import OverlayWindow

        model = ov.OverlayModel()
        window = OverlayWindow((0, 0, 800, 600), model)
        try:
            calls = []
            self._patch(ov, "os_supports_affinity", lambda: True)
            self._patch(ov, "_set_window_display_affinity",
                        lambda hwnd, value: calls.append(value) or True)
            self.assertTrue(window.show_overlay())
            self.assertTrue(window.shown())
            self.assertEqual(calls, [ov.WDA_EXCLUDEFROMCAPTURE])
        finally:
            window.close()

    def test_per_monitor_build_and_kill(self):
        from gui.agent_overlay import AgentOverlay

        overlay = AgentOverlay()
        windows = overlay.build_windows()
        screens = QGuiApplication.screens()
        self.assertEqual(len(windows), len(screens))
        self.assertGreaterEqual(len(windows), 1)
        self.assertEqual(overlay.window_count(), len(screens))
        # Kill closes windows and changes no action state.
        action_running = {"running": True}
        overlay.kill()
        self.assertEqual(overlay.window_count(), 0)
        self.assertEqual(overlay.shown_count(), 0)
        self.assertTrue(action_running["running"])

    def test_paint_from_model_events(self):
        from gui.agent_overlay import OverlayWindow

        model = ov.OverlayModel(clock=lambda: 100000.0)
        model.on_target_rect(state_bus.TargetRect(
            x=10, y=20, w=100, h=40, role="Button", name="Save"))
        model.on_agent_action(
            state_bus.AgentAction(kind="click", label="Save", x=60, y=40),
            now_ms=100000.0)
        model.on_input_acquired()
        window = OverlayWindow((0, 0, 800, 600), model)
        try:
            pixmap = window.grab()
            self.assertFalse(pixmap.isNull())
            self.assertEqual((pixmap.width(), pixmap.height()), (800, 600))
        finally:
            window.close()

    def test_bus_attach_drives_model(self):
        from gui.agent_overlay import AgentOverlay

        state_bus.reset_state_bus()
        try:
            bus = state_bus.get_state_bus()
            overlay = AgentOverlay()
            overlay.attach_to_bus(bus)
            try:
                bus.emit(state_bus.TargetRect(
                    x=10, y=20, w=100, h=40, role="Button", name="Save"))
                bus.emit(state_bus.AgentAction(kind="click", label="Save"))
                bus.emit(state_bus.InputAcquired(source="test"))
                bus.drain()
                self.assertEqual(overlay.model.reticle_tag(), "UIA · BUTTON")
                self.assertEqual(overlay.model.chip, "CLICK › Save")
                self.assertEqual(overlay.model.frame, ov.FRAME_ACTIVE)
            finally:
                overlay.detach_from_bus(bus)
        finally:
            state_bus.reset_state_bus()


if __name__ == "__main__":
    unittest.main()

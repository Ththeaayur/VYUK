"""Fix 7 — tools loopholes: the outcome-verification seam.

Covers: Outcome semantics + unwrap; file_written / print_job_queued
helpers (never raise, never guess); dispatch recognition of Outcome
(verified/unverified keys + Activity line; plain strings unchanged);
write_file / create_art / generate_melody / generate_beat verified
re-reads through the funnel and directly; print_file queue probe;
speak_text engine-start confirmation; press_hotkey release-failure
honesty (ctypes SendInput path + linux/macOS backends); sound-sidecar D4
indicator honesty; adjust_volume read-back hedge; audit-table
counts add to 655; C4 still zero wireable paths; webengine_proxy and
_web_rung honestly marked.

R1a (seam pass 2, part 1): clipboard_equals / clipboard_files_equal
/ file_copied / hash_file / file_matches_hash / file_absent /
process_running / registry_dword_equals helpers; clipboard writes,
file_control copy/move/delete/create_folder, replace_in_file,
shred_file, sync_folder, fractal_art, palette_image, gradient_css
preview, make_wallpaper, the _launch_evidence process leg,
set_focus_assist and display_control dark/set_wallpaper legs wired
through the seam; close_application pinned as already verified
in-handler.

R1b (seam pass 2, part 2): the sent-not-confirmed class wired through
the seam (wifi/eject/window/wallpaper/browser-type/file-writer legs),
the record-write families pinned as already verified in-handler with
file/line evidence, 30 tools marked honestly unverifiable with reasons,
pool closed to 0 (36 seam-wired + 243 already-verified + 30 unverifiable
= 309; 346 + 309 = 655).

Stdlib unittest only. Funnel tests use a fresh ToolRegistry with
uniquely-named probe tools (the A5 pattern), so they do not disturb
the full run's shared budget.
"""

import ctypes
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from core import tool_outcome as outcome_mod
from core.funnel import tool_operations as ops_mod
from core.funnel import tools_registry as registry_mod
from core.funnel import tools_registry_exec_dispatch as dispatch_mod

Outcome = outcome_mod.Outcome

_TREEROOT = Path(__file__).resolve().parent.parent


def _fresh_registry():
    reg = registry_mod.ToolRegistry()
    return reg


class TestOutcomeSemantics(unittest.TestCase):
    def test_verified_unverified_failed_fields(self):
        v = outcome_mod.verified("done", "3 bytes on disk")
        self.assertTrue(v.verified)
        self.assertEqual(v.kind, "verified")
        self.assertEqual(v.evidence, "3 bytes on disk")
        self.assertEqual(str(v), "done")
        u = outcome_mod.unverified("tried", "no read-back")
        self.assertFalse(u.verified)
        self.assertEqual(u.kind, "unverified")
        f = outcome_mod.failed("broke", "disk full")
        self.assertFalse(f.verified)
        self.assertEqual(f.kind, "failed")

    def test_outcome_compares_and_formats_as_its_message(self):
        # Direct (non-funnel) callers keep working with plain strings.
        v = outcome_mod.verified("File written: /x (4 chars)", "ev")
        self.assertEqual(v, "File written: /x (4 chars)")
        self.assertEqual("prefix: %s" % v, "prefix: File written: /x (4 chars)")
        self.assertTrue(str(v).startswith("File written"))

    def test_unwrap_outcome(self):
        msg, ver, ev = outcome_mod.unwrap_outcome(
            outcome_mod.verified("m", "e"))
        self.assertEqual((msg, ver, ev), ("m", True, "e"))
        msg, ver, ev = outcome_mod.unwrap_outcome("plain string")
        self.assertEqual((msg, ver, ev), ("plain string", None, None))
        self.assertFalse(outcome_mod.is_outcome("plain string"))
        self.assertTrue(outcome_mod.is_outcome(
            outcome_mod.unverified("m", "r")))


class TestFileWritten(unittest.TestCase):
    def test_round_trip_bytes_match(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_bytes(b"hello")
            ok, ev = outcome_mod.file_written(p, b"hello")
            self.assertTrue(ok)
            self.assertIn("5 bytes", ev)

    def test_missing_file_is_false_not_raise(self):
        ok, ev = outcome_mod.file_written("/nonexistent_dir_xyz/f.txt")
        self.assertFalse(ok)
        self.assertIn("no file", ev)

    def test_wrong_bytes_is_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_bytes(b"hello")
            ok, ev = outcome_mod.file_written(p, b"other!")
            self.assertFalse(ok)
            self.assertIn("not the 6 written", ev)


class TestPrintJobQueued(unittest.TestCase):
    def test_no_win32print_reads_unavailable_not_queued(self):
        with mock.patch.dict(sys.modules):
            sys.modules.pop("win32print", None)
            ok, ev = outcome_mod.print_job_queued("report.pdf")
            self.assertFalse(ok)
            self.assertIn("no print-job read-back", ev)

    def test_empty_doc_name_is_false(self):
        ok, ev = outcome_mod.print_job_queued("")
        self.assertFalse(ok)

    def test_fake_win32print_job_found_and_missing(self):
        fake = types.ModuleType("win32print")
        fake.GetDefaultPrinter = lambda: "Printer1"
        fake.OpenPrinter = lambda name: ("handle", name)
        fake.EnumJobs = lambda handle, first, count, level: [
            {"JobId": 7, "Document": "report.pdf"},
            {"JobId": 8, "Document": "other.docx"},
        ]
        with mock.patch.dict(sys.modules, {"win32print": fake}):
            with mock.patch.object(sys, "platform", "win32"):
                ok, ev = outcome_mod.print_job_queued("report.pdf")
                self.assertTrue(ok)
                self.assertIn("job 7", ev)
                ok, ev = outcome_mod.print_job_queued(
                    "absent.pdf", wait_s=0.05)
                self.assertFalse(ok)
                self.assertIn("no job", ev)

    def test_fake_win32print_queue_unreadable_is_false(self):
        fake = types.ModuleType("win32print")
        fake.GetDefaultPrinter = lambda: "Printer1"
        fake.OpenPrinter = lambda name: (_ for _ in ()).throw(
            RuntimeError("spooler down"))
        with mock.patch.dict(sys.modules, {"win32print": fake}):
            with mock.patch.object(sys, "platform", "win32"):
                ok, ev = outcome_mod.print_job_queued("x.pdf")
                self.assertFalse(ok)


class TestDispatchOutcome(unittest.TestCase):
    def setUp(self):
        ops_mod.set_store({})
        self._bp = mock.patch.object(
            registry_mod, "_budget_check", lambda name, is_read: None)
        self._bp2 = mock.patch.object(
            dispatch_mod, "_budget_check", lambda name, is_read: None)
        self._bp.start()
        self._bp2.start()

    def tearDown(self):
        self._bp2.stop()
        self._bp.stop()
        ops_mod.reset_store()

    def test_verified_outcome_carries_keys(self):
        reg = _fresh_registry()
        reg.register("f7_probe_verified", "probe",
                     {"type": "object", "properties": {}},
                     lambda **kw: outcome_mod.verified(
                         "did it", "3 bytes on disk"))
        result = reg.execute("f7_probe_verified", {})
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("result"), "did it")
        self.assertTrue(result.get("outcome_verified"))
        self.assertEqual(result.get("outcome_evidence"), "3 bytes on disk")

    def test_unverified_outcome_carries_false_key(self):
        reg = _fresh_registry()
        reg.register("f7_probe_unverified", "probe",
                     {"type": "object", "properties": {}},
                     lambda **kw: outcome_mod.unverified(
                         "tried, unconfirmed", "no read-back"))
        result = reg.execute("f7_probe_unverified", {})
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("result"), "tried, unconfirmed")
        self.assertFalse(result.get("outcome_verified"))
        self.assertEqual(result.get("outcome_evidence"), "no read-back")

    def test_failed_outcome_is_a_failure_sentence_not_a_success_claim(self):
        reg = _fresh_registry()
        reg.register("f7_probe_failed", "probe",
                     {"type": "object", "properties": {}},
                     lambda **kw: outcome_mod.failed(
                         "Could not confirm the write: no file",
                         "no file"))
        result = reg.execute("f7_probe_failed", {})
        self.assertEqual(result.get("status"), "success")
        self.assertIn("Could not confirm", result.get("result", ""))
        self.assertFalse(result.get("outcome_verified"))

    def test_plain_string_handler_gets_no_outcome_keys(self):
        reg = _fresh_registry()
        reg.register("f7_probe_plain", "probe",
                     {"type": "object", "properties": {}},
                     lambda **kw: "plain old answer")
        result = reg.execute("f7_probe_plain", {})
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("result"), "plain old answer")
        self.assertNotIn("outcome_verified", result)
        self.assertNotIn("outcome_evidence", result)


class TestWriteFileVerified(unittest.TestCase):
    def test_write_then_re_read_matches(self):
        from core.funnel.tools_handlers_data_services import (
            write_file_bytes)
        # GAPS-23 (F-V7): the write+verify logic lives in the
        # module-level core function — same code the funnel
        # delegates to; the test pins its Outcome shapes.
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "sub" / "note.txt")
            out = write_file_bytes(p, "hello world")
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertIn("File written", str(out))
            self.assertEqual(Path(p).read_text(encoding="utf-8"),
                             "hello world")

    def test_overwrite_disclosed_and_verified(self):
        from core.funnel.tools_handlers_data_services import (
            write_file_bytes)
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "note.txt")
            Path(p).write_text("old", encoding="utf-8")
            out = write_file_bytes(p, "new content")
            self.assertTrue(out.verified)
            self.assertIn("OVERWROTE", str(out))

    def test_append_verified(self):
        from core.funnel.tools_handlers_data_services import (
            write_file_bytes)
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "note.txt")
            Path(p).write_text("ab", encoding="utf-8")
            out = write_file_bytes(p, "cd", append=True)
            self.assertTrue(out.verified)
            self.assertEqual(Path(p).read_text(encoding="utf-8"), "abcd")

    def test_write_to_bad_location_is_failed_outcome(self):
        from core.funnel.tools_handlers_data_services import (
            write_file_bytes)
        with tempfile.TemporaryDirectory() as d:
            # A FILE as the parent: mkdir fails, the write cannot land.
            blocker = Path(d) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            out = write_file_bytes(str(blocker / "f.txt"), "x")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)


class TestGenerativeSavesVerified(unittest.TestCase):
    def test_create_art_verified_on_disk(self):
        from core import skills_creative_visual as art
        with tempfile.TemporaryDirectory() as d:
            out = art.create_art(style="circles", seed=1, size=256,
                                 output=str(Path(d) / "art.png"))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertIn("Created circles art", str(out))

    def test_generate_melody_verified_on_disk(self):
        from core import skills_creative_sound_words as snd
        with tempfile.TemporaryDirectory() as d:
            out = snd.generate_melody(
                mood="calm", seed=1, seconds=4,
                output=str(Path(d) / "m.wav"))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertIn("melody", str(out))

    def test_generate_beat_verified_on_disk(self):
        from core import skills_creative2_art_music as mus
        with tempfile.TemporaryDirectory() as d:
            out = mus.generate_beat(
                style="lofi", bpm=92, seconds=2,
                output=str(Path(d) / "b.wav"))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertIn("beat", str(out))


class TestPrintFileProbe(unittest.TestCase):
    def _run_print(self):
        from core import skills_plus_apps_files as apps
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "report.pdf"
            p.write_bytes(b"%PDF-1.4 fake")
            return apps.print_file(str(p))

    def test_no_readback_is_unverified_not_claimed(self):
        from core import skills_plus_apps_files as apps
        with mock.patch.object(apps, "_is_windows", return_value=True):
            with mock.patch.object(os, "startfile", create=True):
                with mock.patch.dict(sys.modules):
                    sys.modules.pop("win32print", None)
                    out = self._run_print()
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertIn("couldn't confirm", str(out).lower())

    def test_queued_job_is_verified(self):
        from core import skills_plus_apps_files as apps
        fake = types.ModuleType("win32print")
        fake.GetDefaultPrinter = lambda: "Printer1"
        fake.OpenPrinter = lambda name: ("handle", name)
        fake.EnumJobs = lambda handle, first, count, level: [
            {"JobId": 3, "Document": "report.pdf"}]
        with mock.patch.object(apps, "_is_windows", return_value=True):
            with mock.patch.object(os, "startfile", create=True):
                with mock.patch.dict(sys.modules, {"win32print": fake}):
                    with mock.patch.object(sys, "platform", "win32"):
                        out = self._run_print()
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("job 3", str(out))

    def test_no_job_appearing_is_unverified(self):
        from core import skills_plus_apps_files as apps
        fake = types.ModuleType("win32print")
        fake.GetDefaultPrinter = lambda: "Printer1"
        fake.OpenPrinter = lambda name: ("handle", name)
        fake.EnumJobs = lambda handle, first, count, level: []
        with mock.patch.object(apps, "_is_windows", return_value=True):
            with mock.patch.object(os, "startfile", create=True):
                with mock.patch.dict(sys.modules, {"win32print": fake}):
                    with mock.patch.object(sys, "platform", "win32"):
                        _orig_probe = outcome_mod.print_job_queued
                        with mock.patch.object(
                                outcome_mod, "print_job_queued",
                                side_effect=lambda *a, **k:
                                _orig_probe(
                                    *a, **{**k, "wait_s": 0.05})):
                            out = self._run_print()
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertIn("no job", str(out).lower())


class TestSpeakTextConfirmation(unittest.TestCase):
    def _fake_pyttsx3(self, fail_init=False, slow_init=False):
        engine_mod = types.ModuleType("pyttsx3")

        class _Engine:
            def setProperty(self, *a):
                pass

            def say(self, *a):
                pass

            def runAndWait(self):
                pass

            def stop(self):
                pass

        def _init():
            if slow_init:
                import time as _t
                _t.sleep(0.4)
            if fail_init:
                raise RuntimeError("no voice installed")
            return _Engine()

        engine_mod.init = _init
        return engine_mod

    def test_engine_started_is_verified(self):
        from core import skills_plus_voice_focus as vf
        with mock.patch.dict(sys.modules,
                             {"pyttsx3": self._fake_pyttsx3()}):
            out = vf.speak_text("hello there")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertTrue(str(out).startswith("Reading aloud now"))

    def test_engine_failure_is_failed_not_claimed(self):
        from core import skills_plus_voice_focus as vf
        with mock.patch.dict(sys.modules,
                             {"pyttsx3": self._fake_pyttsx3(
                                 fail_init=True)}):
            out = vf.speak_text("hello there")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertIn("failed", str(out).lower())
        self.assertNotIn("Reading aloud now", str(out))

    def test_slow_engine_is_unverified_not_claimed(self):
        from core import skills_plus_voice_focus as vf
        with mock.patch.dict(sys.modules,
                             {"pyttsx3": self._fake_pyttsx3(
                                 slow_init=True)}):
            with mock.patch.object(vf, "_SPEAK_START_WAIT_S", 0.05):
                out = vf.speak_text("hello there")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertIn("couldn't confirm", str(out).lower())

# --- fake Windows input (lab: no real SendInput) ------------------------
# The fake user32 records the key/mouse events production asks
# SendInput to send, decoding them through a struct that mirrors
# production's _INPUT field-for-field with the same wintypes
# types — so the field offsets match on any platform (on Linux
# wintypes.DWORD is 8 bytes, so the lab layout is 56 bytes, not
# the 40 of the Windows ABI; what the lab proves is what
# production ASKS SendInput to do, never the Windows ABI itself).
from ctypes import wintypes as _wintypes


class _FakeKeybd(ctypes.Structure):
    _fields_ = [("wVk", _wintypes.WORD), ("wScan", _wintypes.WORD),
                ("dwFlags", _wintypes.DWORD), ("time", _wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _FakeMouseIn(ctypes.Structure):
    _fields_ = [("dx", _wintypes.LONG), ("dy", _wintypes.LONG),
                ("mouseData", _wintypes.DWORD),
                ("dwFlags", _wintypes.DWORD),
                ("time", _wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _FakeUnion(ctypes.Union):
    _fields_ = [("mi", _FakeMouseIn), ("ki", _FakeKeybd),
                ("_pad", ctypes.c_byte * 32)]


class _FakeInput(ctypes.Structure):
    _fields_ = [("type", _wintypes.DWORD), ("u", _FakeUnion)]


class _FakeUser32:
    """Records SendInput calls as ("key", vk, key_up) /
    ("mouse", dwFlags) tuples. Key-ups for VKs in
    fail_key_up_vks are refused (accepted 0) — the stuck-key
    case — while everything else is accepted."""

    def __init__(self, fail_key_up_vks=()):
        self.events = []
        self.fail_key_up_vks = set(fail_key_up_vks)

    def SendInput(self, n, inputs, size):
        if isinstance(inputs, ctypes.Array):
            # GAPS-17: keyboard batches arrive as arrays too (the
            # hub's _sendinput_keyboard). Decode each element by
            # its type — keyboard elements as ("key", vk, key_up)
            # with the same refusal logic as the single-struct
            # path, mouse elements as before — and return the
            # accepted prefix, the way real SendInput stops at
            # the first refused event.
            arr = ctypes.cast(inputs, ctypes.POINTER(_FakeInput))
            for i in range(n):
                ev = arr[i]
                if ev.type == 1:
                    key_up = bool(ev.u.ki.dwFlags & 0x0002)
                    self.events.append(("key", ev.u.ki.wVk, key_up))
                    if key_up and ev.u.ki.wVk in self.fail_key_up_vks:
                        return i
                else:
                    self.events.append(("mouse", ev.u.mi.dwFlags))
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


class _fake_windows_input:
    """Context manager: make the lab look like Windows to the
    input seam — patch ``_is_windows`` True on the module under
    test AND on the cursor hub (``_sendinput_key`` checks its own
    module's copy) and install the fake windll — and clean the
    failsafe registry afterwards (press_hotkey registers every
    hold)."""

    def __init__(self, module, fail_key_up_vks=()):
        self.module = module
        self.fake = _FakeUser32(fail_key_up_vks=fail_key_up_vks)
        self._had_windll = hasattr(ctypes, "windll")
        self._old_windll = getattr(ctypes, "windll", None)

    def __enter__(self):
        from core.desktop import cursor_input as _ci
        ctypes.windll = _FakeWindll(self.fake)
        self._patchers = [
            mock.patch.object(self.module, "_is_windows",
                              return_value=True),
            mock.patch.object(_ci, "_is_windows", return_value=True),
        ]
        for patcher in self._patchers:
            patcher.start()
        return self.fake

    def __exit__(self, *exc):
        for patcher in reversed(self._patchers):
            patcher.stop()
        if self._had_windll:
            ctypes.windll = self._old_windll
        else:
            try:
                del ctypes.windll
            except AttributeError:
                pass
        from core import failsafe as _fs
        _fs.clear_pressed_registry()
        _fs.reset()
        return False


class TestPressHotkeyReleaseHonesty(unittest.TestCase):
    def test_ctypes_release_failure_named(self):
        from core import failsafe as fs
        from core.desktop import control_input as ci
        with _fake_windows_input(ci, fail_key_up_vks={0x11}) as fake:
            with mock.patch.object(ci, "_routed_backend",
                                   return_value=None):
                with mock.patch.object(ci, "_input_blocked",
                                       return_value=None):
                    with mock.patch.object(ci, "_expectation_refusal",
                                           return_value=None):
                        result = ci.press_hotkey("ctrl+c")
            # The ctrl key-up was attempted and refused; the c
            # key-up still went out — and the stuck key stays
            # tracked with the failsafe, which is the rescue.
            self.assertIn(("key", 0x11, True), fake.events)
            self.assertIn(("key", 0x43, True), fake.events)
            self.assertIn(("key", "ctrl"), fs.tracked_pressed())
        self.assertTrue(result.startswith("Pressed hotkey:"))
        self.assertIn("ctrl", result)
        self.assertIn("may still be physically down", result)
        self.assertIn("failsafe", result)

    def test_ctypes_clean_release_unchanged(self):
        # INTENTIONAL (R1b): the clean-success message now carries the
        # honest caveat — the keys were SENT, whether the app acted on
        # them was not confirmed — so press_hotkey returns an
        # unverified Outcome instead of the bare "Pressed hotkey" line.
        from core import failsafe as fs
        from core.desktop import control_input as ci
        with _fake_windows_input(ci) as fake:
            with mock.patch.object(ci, "_routed_backend",
                                   return_value=None):
                with mock.patch.object(ci, "_input_blocked",
                                       return_value=None):
                    with mock.patch.object(ci, "_expectation_refusal",
                                           return_value=None):
                        result = ci.press_hotkey("ctrl+c")
            # Down ctrl, down c, up c, up ctrl — and every hold
            # unregistered from the failsafe once it landed.
            self.assertEqual(fake.events,
                             [("key", 0x11, False), ("key", 0x43, False),
                              ("key", 0x43, True), ("key", 0x11, True)])
            self.assertEqual(fs.tracked_pressed(), [])
        self.assertTrue(outcome_mod.is_outcome(result))
        self.assertFalse(result.verified)
        self.assertEqual(result.kind, "unverified")
        self.assertEqual(
            str(result),
            "Pressed hotkey: ctrl+c (sent — whether the app acted on it "
            "was not confirmed.)")

    def test_linux_backend_honest_unavailable(self):
        # GAPS-2: the Linux backend has no native input engine
        # wired (the third-party input library it used is gone),
        # so _os_input answers (False, None) throughout and the
        # caller phrases the honest unavailable sentence — it
        # never claims a press it cannot perform.
        from core.platform import linux as lin
        ok, msg = lin.LinuxBackend()._os_input("press_hotkey", "ctrl+c")
        self.assertFalse(ok)
        self.assertIsNone(msg)


class TestSoundSidecarD4(unittest.TestCase):
    def _sidecar(self):
        from core import sound_event_sidecar as sc
        opt_in = sc.SoundEventOptIn(
            mic_approval_fn=lambda kind: True)
        sidecar = sc.SoundEventSidecar(opt_in=opt_in)
        opt_in.grant()
        return sc, sidecar

    def _fake_stream(self):
        class _Stream:
            def start(self):
                pass

            def stop(self):
                pass

        return _Stream()

    def test_indicator_note_failure_named_not_claimed_live(self):
        sc, sidecar = self._sidecar()
        with mock.patch("core.capture_guard.note_capture_started",
                        side_effect=RuntimeError("guard down")):
            ok, msg = sidecar.start(
                stream_factory=lambda cb: self._fake_stream())
        self.assertTrue(ok)
        self.assertNotIn("indicator live", msg)
        self.assertIn("indicator note failed", msg)

    def test_indicator_note_ok_claims_live(self):
        sc, sidecar = self._sidecar()
        with mock.patch("core.capture_guard.note_capture_started",
                        return_value=None):
            ok, msg = sidecar.start(
                stream_factory=lambda cb: self._fake_stream())
        self.assertTrue(ok)
        self.assertIn("indicator live", msg)


class TestAdjustVolumeHedge(unittest.TestCase):
    def test_readback_exception_gets_honest_hedge(self):
        from core.desktop import control_plus_volume_network as volnet
        from core.funnel import tools_handlers_command_url as cmd
        # GAPS-23 (F-V7): through the funnel — the stubs steer the
        # handler identically; the string lands in ["result"].
        with mock.patch.object(sys, "platform", "win32"):
            # _send_key_event is ctypes.windll (Windows-only): stub
            # the key press itself so the test reaches the read-back.
            with mock.patch.object(cmd, "_send_key_event",
                                   return_value=None):
                # Patch the seam on its canonical defining module
                # (Fix 1 precedent: a string patch target on the
                # facade re-export trips the guards suite and would
                # not intercept the handler's read anyway).
                with mock.patch.object(
                        volnet, "_pycaw_volume",
                        side_effect=RuntimeError("pycaw broken")):
                    out = registry_mod.tools.execute(
                        "adjust_volume",
                        {"action": "up", "steps": 1})["result"]
        self.assertIn("Increased volume by 1 step", out)
        self.assertIn("couldn't be read back", out)


class TestAuditTable(unittest.TestCase):
    def test_counts_add_to_677_and_match_snapshot(self):
        # INTENTIONAL (R7-4 meetily, 2026-11-10): 657 -> 661 —
        # start_meeting, stop_meeting, meeting_status,
        # transcribe_audio added (media_audio 31 -> 35, effectful
        # 311 -> 313, read 346 -> 348).
        # INTENTIONAL (R7-5 kovrel, 2026-11-10): 661 -> 665 —
        # start_companion, stop_companion, companion_status,
        # companion_pairing added (system_apps 81 -> 85, effectful
        # 313 -> 315, read 348 -> 350).
        # INTENTIONAL (R7-6 micro-wake-word, 2026-11-10): 665 -> 669 —
        # train_custom_wake_word, enable_custom_wake_model,
        # list_custom_wake_models, custom_wake_model_status added
        # (media_audio 35 -> 39, effectful 315 -> 317,
        # read 350 -> 352).
        import re
        doc = (_TREEROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        m = re.search(r"<!-- AUDIT-COUNTS: (\{.*\}) -->", doc)
        self.assertIsNotNone(m, "audit counts block missing")
        counts = json.loads(m.group(1))
        self.assertEqual(sum(counts.values()), 756)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        snap = json.loads(
            (_TREEROOT / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(len(snap), 756)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        t = re.search(r"<!-- AUDIT-TOTALS: (\{.*\}) -->", doc)
        totals = json.loads(t.group(1))
        self.assertEqual(totals["total"], 756)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        self.assertEqual(totals["read"] + totals["effectful"], 756)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        # INTENTIONAL (R1b): the pool is closed — the honestly-
        # unverifiable bucket joins the sum; next_pass_pool is 0.
        self.assertEqual(totals["seam_wired"]
                         + totals["already_verified_in_handler"]
                         + totals["honestly_unverifiable"]
                         + totals["next_pass_pool"],
                         totals["effectful"])

    def test_c4_still_zero_wireable_paths(self):
        doc = (_TREEROOT / "tests" / "test_c4_enumeration.py").read_text(
            encoding="utf-8")
        self.assertIn("WIRED verify_before_confirm call sites: NONE", doc)

    def test_webengine_proxy_honestly_marked(self):
        doc = (_TREEROOT / "core" / "webengine_proxy.py").read_text(
            encoding="utf-8")
        self.assertIn("no consumer", doc)
        self.assertIn("stay as the seam", doc)

    # INTENTIONAL (R3): the web rung was REVIVED through his
    # attached browser — the v5.70 probe is deleted, not
    # marked. The pin now proves the probe is gone and the
    # rung's lawful basis is stated in code (no cookie-store
    # reading; the login wall is observed at the click).
    def test_web_rung_revived_no_session_probe(self):
        for path in ("core/music_player_spotify.py",
                     "core/music_player.py",
                     "core/music_player_rungs.py"):
            doc = (_TREEROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("_spotify_web_session", doc)
        rungs = (_TREEROOT / "core" / "music_player_rungs.py").read_text(
            encoding="utf-8")
        self.assertIn("REVIVED R3", rungs)
        self.assertIn("no cookie", rungs.lower())


# =====================================================================
# R1a — seam pass 2, part 1 (clipboard / files / image writers +
# process_running + registry_dword_equals).
#
# Every newly wired tool reports success only when the world
# confirms it, and failure with the cause when it does not. The
# helpers verify in the world and never re-run the action.
# =====================================================================


def _install_fake_pyperclip(store):
    """Fake pyperclip backed by a plain dict (no real clipboard)."""
    fake = types.ModuleType("pyperclip")
    fake.paste = lambda: store.get("text")
    fake.copy = lambda text: store.__setitem__("text", text)
    return mock.patch.dict(sys.modules, {"pyperclip": fake})


class _FakeRegKey:
    def __init__(self, winreg, path):
        self._winreg = winreg
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    """Minimal winreg: a dict store keyed by (key path, value name)."""

    HKEY_CURRENT_USER = "HKCU"
    REG_DWORD = 4
    REG_SZ = 1
    KEY_SET_VALUE = 2
    KEY_QUERY_VALUE = 1

    def __init__(self, store=None):
        self.store = dict(store or {})

    def OpenKey(self, root, path, reserved=0, access=0):
        return _FakeRegKey(self, path)

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        return _FakeRegKey(self, path)

    def SetValueEx(self, key, name, reserved, vtype, value):
        self.store[(key.path, name)] = (value, vtype)

    def QueryValueEx(self, key, name):
        if (key.path, name) not in self.store:
            raise FileNotFoundError("no such value")
        return self.store[(key.path, name)]


def _install_fake_winreg(store=None):
    """Fake winreg + win32 platform (lab is Linux)."""
    fake = _FakeWinreg(store)
    stack = mock.patch.dict(sys.modules, {"winreg": fake})
    stack2 = mock.patch.object(sys, "platform", "win32")
    return fake, stack, stack2


class TestClipboardEquals(unittest.TestCase):
    def test_equal_text_is_true(self):
        with _install_fake_pyperclip({"text": "hello"}):
            ok, ev = outcome_mod.clipboard_equals("hello")
            self.assertTrue(ok)
            self.assertIn("match", ev)

    def test_different_text_is_false_with_reason(self):
        with _install_fake_pyperclip({"text": "stale"}):
            ok, ev = outcome_mod.clipboard_equals("hello")
            self.assertFalse(ok)
            self.assertIn("not the 5 written", ev)

    def test_non_text_clipboard_is_false(self):
        with _install_fake_pyperclip({"text": None}):
            ok, ev = outcome_mod.clipboard_equals("hello")
            self.assertFalse(ok)
            self.assertIn("isn't text", ev)

    def test_paste_raising_is_false_not_success(self):
        fake = types.ModuleType("pyperclip")
        fake.paste = lambda: (_ for _ in ()).throw(RuntimeError("busy"))
        with mock.patch.dict(sys.modules, {"pyperclip": fake}):
            ok, ev = outcome_mod.clipboard_equals("hello")
            self.assertFalse(ok)
            self.assertIn("could not read", ev)

    def test_missing_pyperclip_is_false(self):
        with mock.patch.dict(sys.modules, {"pyperclip": None}):
            ok, ev = outcome_mod.clipboard_equals("hello")
            self.assertFalse(ok)
            self.assertIn("no clipboard read-back", ev)


class TestClipboardFilesEqual(unittest.TestCase):
    def _fake_run(self, uri_stdout, returncode=0):
        def _run(cmd, **kw):
            class Done:
                pass
            done = Done()
            done.returncode = returncode
            done.stdout = uri_stdout if "-o" in cmd else ""
            done.stderr = ""
            return done
        return _run

    def test_xclip_uri_list_match_is_true(self):
        import subprocess
        with mock.patch("shutil.which", return_value="/usr/bin/xclip"):
            with mock.patch.object(subprocess, "run",
                                  self._fake_run("file:///tmp/a.txt\n")):
                ok, ev = outcome_mod.clipboard_files_equal("/tmp/a.txt")
                self.assertTrue(ok)
                self.assertIn("a.txt", ev)

    def test_xclip_empty_list_is_false(self):
        import subprocess
        with mock.patch("shutil.which", return_value="/usr/bin/xclip"):
            with mock.patch.object(subprocess, "run",
                                  self._fake_run("")):
                ok, ev = outcome_mod.clipboard_files_equal("/tmp/a.txt")
                self.assertFalse(ok)
                self.assertIn("no files", ev)

    def test_no_xclip_is_false(self):
        with mock.patch("shutil.which", return_value=None):
            ok, ev = outcome_mod.clipboard_files_equal("/tmp/a.txt")
            self.assertFalse(ok)
            self.assertIn("no file-list read-back", ev)


class TestFileCopiedHashAbsent(unittest.TestCase):
    def test_file_copied_equal_and_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "a.bin"
            b = Path(d) / "b.bin"
            a.write_bytes(b"abc123")
            b.write_bytes(b"abc123")
            ok, ev = outcome_mod.file_copied(a, b)
            self.assertTrue(ok)
            b.write_bytes(b"abc124")
            ok, ev = outcome_mod.file_copied(a, b)
            self.assertFalse(ok)
            self.assertIn("differ", ev)

    def test_file_copied_missing_destination_is_false(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "a.bin"
            a.write_bytes(b"x")
            ok, ev = outcome_mod.file_copied(a, Path(d) / "nope.bin")
            self.assertFalse(ok)

    def test_hash_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.txt"
            p.write_bytes(b"hello world")
            digest, ev = outcome_mod.hash_file(p)
            self.assertIsNotNone(digest)
            ok, ev2 = outcome_mod.file_matches_hash(p, digest)
            self.assertTrue(ok)
            ok, ev2 = outcome_mod.file_matches_hash(p, "0" * 64)
            self.assertFalse(ok)
            self.assertIn("differ", ev2)

    def test_file_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "gone.txt"
            ok, _ = outcome_mod.file_absent(p)
            self.assertTrue(ok)
            p.write_text("here", encoding="utf-8")
            ok, _ = outcome_mod.file_absent(p)
            self.assertFalse(ok)


class TestProcessRunning(unittest.TestCase):
    def test_current_pid_exists(self):
        ok, ev = outcome_mod.process_running(pid=os.getpid())
        self.assertTrue(ok)

    def test_absent_pid_is_false(self):
        ok, ev = outcome_mod.process_running(pid=99999999)
        self.assertFalse(ok)

    def test_current_process_name_found(self):
        name = Path("/proc/self/comm").read_text(
            encoding="utf-8").strip()
        ok, ev = outcome_mod.process_running(name=name)
        self.assertTrue(ok, ev)

    def test_future_created_after_is_false(self):
        import time
        ok, ev = outcome_mod.process_running(
            pid=os.getpid(), created_after=time.time() + 3600)
        self.assertFalse(ok)

    def test_past_created_after_is_true(self):
        import time
        ok, ev = outcome_mod.process_running(
            pid=os.getpid(), created_after=time.time() - 3600)
        self.assertTrue(ok, ev)

    def test_no_name_or_pid_is_false(self):
        ok, ev = outcome_mod.process_running()
        self.assertFalse(ok)


class TestRegistryDwordEquals(unittest.TestCase):
    def test_equal_dword_is_true(self):
        key = r"Software\Fake\Path"
        fake, s1, s2 = _install_fake_winreg({(key, "Val"): (0, 4)})
        with s1, s2:
            ok, ev = outcome_mod.registry_dword_equals(key, "Val", 0)
            self.assertTrue(ok)
            self.assertIn("reads 0 as expected", ev)

    def test_wrong_dword_is_false(self):
        key = r"Software\Fake\Path"
        fake, s1, s2 = _install_fake_winreg({(key, "Val"): (1, 4)})
        with s1, s2:
            ok, ev = outcome_mod.registry_dword_equals(key, "Val", 0)
            self.assertFalse(ok)
            self.assertIn("reads 1, not 0", ev)

    def test_missing_value_is_false(self):
        fake, s1, s2 = _install_fake_winreg({})
        with s1, s2:
            ok, ev = outcome_mod.registry_dword_equals(
                r"Software\Fake\Path", "Val", 0)
            self.assertFalse(ok)
            self.assertIn("is not set", ev)

    def test_non_dword_is_false(self):
        key = r"Software\Fake\Path"
        fake, s1, s2 = _install_fake_winreg({(key, "Val"): ("x", 1)})
        with s1, s2:
            ok, ev = outcome_mod.registry_dword_equals(key, "Val", 0)
            self.assertFalse(ok)
            self.assertIn("not a DWORD", ev)

    def test_non_windows_is_false(self):
        with mock.patch.dict(sys.modules, {"winreg": None}):
            ok, ev = outcome_mod.registry_dword_equals(
                r"Software\Fake\Path", "Val", 0)
            self.assertFalse(ok)
            self.assertIn("no registry read-back", ev)


class TestHelpersNeverRerun(unittest.TestCase):
    def test_file_written_does_not_change_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.txt"
            p.write_bytes(b"data")
            before = p.read_bytes()
            mtime = p.stat().st_mtime_ns
            ok, _ = outcome_mod.file_written(p, b"other")
            self.assertFalse(ok)
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(p.stat().st_mtime_ns, mtime)

    def test_clipboard_equals_never_writes(self):
        store = {"text": "hello"}
        fake = types.ModuleType("pyperclip")
        fake.paste = lambda: store["text"]
        calls = []
        fake.copy = lambda text: calls.append(text)
        with mock.patch.dict(sys.modules, {"pyperclip": fake}):
            ok, _ = outcome_mod.clipboard_equals("hello")
            self.assertTrue(ok)
            self.assertEqual(calls, [])

class TestWriteClipboardWired(unittest.TestCase):
    # GAPS-23 (F-V7): the write+paste-back-verify logic lives in the
    # module-level write_clipboard_core() — same code the funnel
    # delegates to; the tests pin its Outcome shapes.

    def test_plain_write_verified_when_paste_back_matches(self):
        from core.desktop import control
        from core.funnel.tools_handlers_command_url import (
            write_clipboard_core)
        store = {}

        def _fake_write(text):
            store["text"] = "" if text is None else str(text)
            return "Copied %d characters to the clipboard." % len(store["text"])

        with _install_fake_pyperclip(store):
            with mock.patch.object(control, "write_clipboard", _fake_write):
                out = write_clipboard_core("hello")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Copied", str(out))

    def test_plain_write_failed_when_paste_back_differs(self):
        from core.desktop import control
        from core.funnel.tools_handlers_command_url import (
            write_clipboard_core)
        store = {"text": "stale"}

        def _fake_write(text):
            # The write claims success but the clipboard still
            # holds the old text (another app overwrote it).
            return "Copied %d characters to the clipboard." % len(str(text))

        with _install_fake_pyperclip(store):
            with mock.patch.object(control, "write_clipboard", _fake_write):
                out = write_clipboard_core("hello")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "failed")
        self.assertIn("did not stick", str(out))

    def test_paste_back_unreadable_is_unverified_never_success(self):
        # The old hole: a paste-back exception was silently treated
        # as success. Now it is unverified, never verified.
        from core.desktop import control
        from core.funnel.tools_handlers_command_url import (
            write_clipboard_core)
        fake = types.ModuleType("pyperclip")
        fake.paste = lambda: (_ for _ in ()).throw(RuntimeError("busy"))
        fake.copy = lambda text: None
        with mock.patch.dict(sys.modules, {"pyperclip": fake}):
            with mock.patch.object(
                    control, "write_clipboard",
                    lambda text: "Copied 5 characters to the clipboard."):
                out = write_clipboard_core("hello")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")

    def test_validation_refusal_stays_plain_string(self):
        from core.desktop import control
        from core.funnel.tools_handlers_command_url import (
            write_clipboard_core)
        with mock.patch.object(
                control, "write_clipboard",
                lambda text: "Nothing to copy: the text was empty."):
            out = write_clipboard_core("")
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)
        self.assertIn("Nothing to copy", out)

    def test_append_verified(self):
        from core.desktop import control
        from core.funnel.tools_handlers_command_url import (
            write_clipboard_core)
        store = {"text": "first"}

        def _fake_write(text):
            store["text"] = "" if text is None else str(text)
            return "Copied %d characters to the clipboard." % len(store["text"])

        with _install_fake_pyperclip(store):
            with mock.patch.object(control, "write_clipboard", _fake_write):
                out = write_clipboard_core(
                    "second", append=True)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertEqual(store["text"], "first\nsecond")


class TestClearClipboardWired(unittest.TestCase):
    def _fake_user32(self, remaining=0):
        class _User32:
            def OpenClipboard(self, hwnd):
                return True

            def EmptyClipboard(self):
                return True

            def CountClipboardFormats(self):
                return remaining

            def CloseClipboard(self):
                return True
        return _User32()

    def test_clear_verified_when_zero_formats_remain(self):
        from core import skills_desktop_prefs as prefs
        fake_windll = types.SimpleNamespace(
            user32=self._fake_user32(remaining=0))
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(ctypes, "windll", fake_windll,
                                  create=True):
                out = prefs.clear_clipboard()
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)

    def test_clear_failed_when_formats_reappear(self):
        from core import skills_desktop_prefs as prefs
        fake_windll = types.SimpleNamespace(
            user32=self._fake_user32(remaining=2))
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(ctypes, "windll", fake_windll,
                                  create=True):
                out = prefs.clear_clipboard()
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "failed")

    def test_clear_refusal_on_linux_stays_plain(self):
        from core import skills_desktop_prefs as prefs
        out = prefs.clear_clipboard()
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)


class TestCopyFileToClipboardWired(unittest.TestCase):
    def _fake_run(self, uri_stdout, returncode=0):
        def _run(cmd, **kw):
            class Done:
                pass
            done = Done()
            done.returncode = returncode
            done.stdout = uri_stdout if "-o" in cmd else ""
            done.stderr = ""
            return done
        return _run

    def test_xclip_copy_verified_by_read_back(self):
        import subprocess
        from core import skills_files2_pdf_files as files2
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_text("x", encoding="utf-8")
            uri = "file://" + str(p) + "\n"
            with mock.patch("shutil.which",
                           return_value="/usr/bin/xclip"):
                with mock.patch.object(subprocess, "run",
                                      self._fake_run(uri)):
                    out = files2.copy_file_to_clipboard(str(p))
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)

    def test_xclip_copy_unverified_when_read_back_fails(self):
        import subprocess
        from core import skills_files2_pdf_files as files2

        def _run(cmd, **kw):
            class Done:
                pass
            done = Done()
            # The SET call succeeds; only the read-back fails.
            done.returncode = 1 if "-o" in cmd else 0
            done.stdout = ""
            done.stderr = ""
            return done

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_text("x", encoding="utf-8")
            with mock.patch("shutil.which",
                           return_value="/usr/bin/xclip"):
                with mock.patch.object(subprocess, "run", _run):
                    out = files2.copy_file_to_clipboard(str(p))
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")

    def test_missing_file_refusal_stays_plain(self):
        from core import skills_files2_pdf_files as files2
        out = files2.copy_file_to_clipboard("/tmp/does-not-exist-r1a.txt")
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)


class TestFileControlVerified(unittest.TestCase):
    def test_copy_verified_by_destination_bytes(self):
        from core.desktop import control_files
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "a.txt"
            src.write_bytes(b"payload-123")
            dst = Path(d) / "b.txt"
            out = control_files.file_control(
                "copy", str(src), destination=str(dst))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertEqual(dst.read_bytes(), b"payload-123")

    def test_move_verified_and_source_gone(self):
        from core.desktop import control_files
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "a.txt"
            src.write_bytes(b"move-me")
            dst = Path(d) / "b.txt"
            out = control_files.file_control(
                "move", str(src), destination=str(dst))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertFalse(src.exists())
            self.assertEqual(dst.read_bytes(), b"move-me")

    def test_create_folder_verified(self):
        from core.desktop import control_files
        with tempfile.TemporaryDirectory() as d:
            new = Path(d) / "sub" / "deep"
            out = control_files.file_control("create_folder", str(new))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertTrue(new.is_dir())

    def test_delete_verified_by_absence(self):
        from core.desktop import control_files
        with tempfile.TemporaryDirectory() as d:
            trash = Path(d) / "trash"
            victim = Path(d) / "v.txt"
            victim.write_text("gone", encoding="utf-8")
            with mock.patch.dict(os.environ,
                                {"VYUK_TRASH_DIR": str(trash)}):
                out = control_files.file_control(
                    "delete", str(victim), confirm=True)
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertFalse(victim.exists())

    def test_list_stays_plain_string(self):
        from core.desktop import control_files
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("x", encoding="utf-8")
            out = control_files.file_control("list", str(d))
            self.assertIsInstance(out, str)
            self.assertNotIsInstance(out, outcome_mod.Outcome)
            self.assertIn("a.txt", out)


class TestReplaceShredSyncVerified(unittest.TestCase):
    def test_replace_verified_by_re_read(self):
        from core.skills_text import replace_in_file
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.txt"
            p.write_text("foo bar foo", encoding="utf-8")
            out = replace_in_file(str(p), "foo", "baz")
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertEqual(p.read_text(encoding="utf-8"), "baz bar baz")

    def test_replace_no_match_stays_plain(self):
        from core.skills_text import replace_in_file
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.txt"
            p.write_text("nothing here", encoding="utf-8")
            out = replace_in_file(str(p), "zzz", "baz")
            self.assertIsInstance(out, str)
            self.assertNotIsInstance(out, outcome_mod.Outcome)

    def test_shred_verified_by_absence(self):
        from core.skills_files2_pdf_files import shred_file
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "y.txt"
            p.write_text("shred me", encoding="utf-8")
            out = shred_file(str(p))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertFalse(p.exists())

    def test_sync_verified_by_destination_bytes(self):
        from core.skills_files2_pdf_files import sync_folder
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            dst = Path(d) / "dst"
            src.mkdir()
            (src / "a.txt").write_bytes(b"sync-payload")
            out = sync_folder(str(src), str(dst))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertEqual((dst / "a.txt").read_bytes(), b"sync-payload")

    def test_sync_dry_run_stays_plain_string(self):
        from core.skills_files2_pdf_files import sync_folder
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            dst = Path(d) / "dst"
            src.mkdir()
            (src / "a.txt").write_bytes(b"sync-payload")
            out = sync_folder(str(src), str(dst), dry_run=True)
            self.assertIsInstance(out, str)
            self.assertNotIsInstance(out, outcome_mod.Outcome)
            self.assertFalse((dst / "a.txt").exists())


class TestCreativeImageVerified(unittest.TestCase):
    def test_fractal_art_verified(self):
        from core.skills_creative2_art_music import fractal_art
        with tempfile.TemporaryDirectory() as d:
            out = fractal_art(kind="mandelbrot", seed=3, size=256,
                              output=str(Path(d) / "f.png"))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertTrue((Path(d) / "f.png").is_file())

    def test_palette_image_verified(self):
        from core.skills_studio import palette_image
        with tempfile.TemporaryDirectory() as d:
            out = palette_image(output=str(Path(d) / "p.png"))
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertTrue((Path(d) / "p.png").is_file())

    def test_gradient_css_preview_verified(self):
        from core.skills_creative_play import gradient_css
        home = tempfile.mkdtemp(prefix="r1a-home-")
        with mock.patch.dict(os.environ, {"HOME": home}):
            out = gradient_css("#3B82F6", "#F59E0B", save_preview=True)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Preview saved to", str(out))

    def test_gradient_css_no_preview_stays_plain(self):
        from core.skills_creative_play import gradient_css
        out = gradient_css("#3B82F6", "#F59E0B", save_preview=False)
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)

    def test_make_wallpaper_verified_without_setting(self):
        from core.skills_studio import make_wallpaper
        home = tempfile.mkdtemp(prefix="r1a-home-")
        with mock.patch.dict(os.environ, {"HOME": home}):
            out = make_wallpaper(style="gradient", seed=7, set_it=False)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Wallpaper picture made", str(out))


class TestLaunchEvidenceProcessLeg(unittest.TestCase):
    def test_running_process_is_evidence(self):
        import time
        from core.funnel import tools_handlers_frame_launch as launch_mod
        name = Path("/proc/self/comm").read_text(
            encoding="utf-8").strip()
        ev = launch_mod._launch_evidence(name, time.time() - 3600)
        self.assertEqual(ev, "process")

    def test_absent_process_is_no_evidence(self):
        import time
        from core.funnel import tools_handlers_frame_launch as launch_mod
        ev = launch_mod._launch_evidence(
            "definitely-not-a-process-xyz", time.time() - 3600)
        self.assertIsNone(ev)

    def test_future_start_is_no_process_evidence(self):
        import time
        from core.funnel import tools_handlers_frame_launch as launch_mod
        name = Path("/proc/self/comm").read_text(
            encoding="utf-8").strip()
        ev = launch_mod._launch_evidence(name, time.time() + 3600)
        # The process leg fails (started before the launch window);
        # the window leg cannot confirm on headless Linux either.
        self.assertIsNone(ev)


class TestFocusAssistWired(unittest.TestCase):
    def test_focus_on_verified_by_registry_read_back(self):
        from core import skills_plus_voice_focus as focus
        key = focus._FOCUS_KEY
        fake, s1, s2 = _install_fake_winreg({})
        with s1, s2:
            out = focus.set_focus_assist(True)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Focus Assist is on", str(out))
        self.assertEqual(fake.store[(key, focus._FOCUS_VALUE)], (0, 4))

    def test_focus_off_verified_by_registry_read_back(self):
        from core import skills_plus_voice_focus as focus
        fake, s1, s2 = _install_fake_winreg({})
        with s1, s2:
            out = focus.set_focus_assist(False)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Focus Assist is off", str(out))

    def test_focus_change_not_kept_is_failed(self):
        from core import skills_plus_voice_focus as focus
        key = focus._FOCUS_KEY
        # A policy flips the value back the moment it is written:
        # SetValueEx stores 1 while the caller asked for 0.
        store = {(key, focus._FOCUS_VALUE): (1, 4)}
        fake = _FakeWinreg(store)
        orig_set = fake.SetValueEx

        def _sabotage(key_obj, name, reserved, vtype, value):
            orig_set(key_obj, name, reserved, vtype, 1)

        fake.SetValueEx = _sabotage
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            with mock.patch.object(sys, "platform", "win32"):
                out = focus.set_focus_assist(True)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "failed")

    def test_focus_call_sites_accept_outcome(self):
        # skills_desktop_tiling.py reads set_focus_assist through
        # str(...) — the Outcome's message still carries the prefix
        # the call sites test for.
        v = outcome_mod.verified("Focus Assist is on — muted", "ev")
        self.assertTrue(str(v).startswith("Focus Assist is on"))
        v = outcome_mod.verified("Focus Assist is off — showing", "ev")
        self.assertTrue(str(v).startswith("Focus Assist is off"))


class TestDisplayControlDarkLegs(unittest.TestCase):
    def test_dark_on_verified_by_both_dwords(self):
        from core.desktop import control_plus_desktop as desk
        key = (r"Software\Microsoft\Windows\CurrentVersion\Themes"
               r"\Personalize")
        fake, s1, s2 = _install_fake_winreg({})
        with s1, s2:
            out = desk.display_control("dark_on")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Dark mode is now on", str(out))
        self.assertEqual(fake.store[(key, "AppsUseLightTheme")], (0, 4))
        self.assertEqual(fake.store[(key, "SystemUsesLightTheme")], (0, 4))

    def test_dark_off_verified(self):
        from core.desktop import control_plus_desktop as desk
        fake, s1, s2 = _install_fake_winreg({})
        with s1, s2:
            out = desk.display_control("dark_off")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Light mode is now on", str(out))

    def test_dark_change_not_kept_is_failed(self):
        from core.desktop import control_plus_desktop as desk
        key = (r"Software\Microsoft\Windows\CurrentVersion\Themes"
               r"\Personalize")
        fake = _FakeWinreg({})
        orig_set = fake.SetValueEx

        def _sabotage(key_obj, name, reserved, vtype, value):
            orig_set(key_obj, name, reserved, vtype, 1)

        fake.SetValueEx = _sabotage
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            with mock.patch.object(sys, "platform", "win32"):
                out = desk.display_control("dark_on")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "failed")

    def test_unknown_action_stays_plain(self):
        from core.desktop import control_plus_desktop as desk
        out = desk.display_control("bogus")
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)


class TestCloseApplicationAlreadyVerified(unittest.TestCase):
    def test_close_application_verifies_exits_in_handler(self):
        # Pin (R1a triage): close_application was already verified
        # in-handler via _termination_outcome (H16) — it joins the
        # already-verified list, it is not newly seam-wired.
        src = (_TREEROOT / "core" / "desktop"
               / "control_system.py").read_text(encoding="utf-8")
        self.assertIn("_termination_outcome", src)
        seg = src[src.index("def close_application"):]
        seg = seg[:seg.index("\ndef ", 1)]
        self.assertIn("_termination_outcome", seg)


# NOTE (R1b): the R1a totals test (19/6/284) was superseded by
# TestAuditTotalsR1b in the R1b block below, which asserts the closed
# counts 36/243/30/pool 0. The old test is removed, not loosened.

# ---------------------------------------------------------------------------
# R1b (seam pass 2, part 2): sent-not-confirmed class wired, record-write
# families pinned, honestly-unverifiable remainder marked, pool closed.
# ---------------------------------------------------------------------------

try:
    import pypdf as _pypdf_mod
    _HAS_PYPDF = True
except Exception:  # noqa: BLE001 — pypdf is optional; sys lacks it
    _pypdf_mod = None
    _HAS_PYPDF = False


class _FakeSendType:
    """Fake ``uia_send._sendinput_type``: records the batches it
    was asked to type and reports them all sent."""

    def __init__(self):
        from core.desktop.uia_send import _SendOutcome
        self._outcome_cls = _SendOutcome
        self.typed = []

    def __call__(self, text, clear_first=False):
        self.typed.append(text)
        return self._outcome_cls(completed=True, aborted=False,
                                 chars_sent=len(text),
                                 chars_total=len(text))


class TestNetworkControlR1b(unittest.TestCase):
    def _run(self, action, ssid="", adapter_state=(True, "Admin state: Enabled"),
             connected=None):
        from core.desktop import control_plus_volume_network as netmod
        proc = mock.Mock(returncode=0, stdout="", stderr="")
        patches = [
            mock.patch.object(netmod, "_is_windows", return_value=True),
            mock.patch("subprocess.run", return_value=proc),
            mock.patch.object(outcome_mod, "wifi_adapter_state",
                              return_value=adapter_state),
            mock.patch.object(outcome_mod, "wifi_connected_ssid",
                              return_value=(connected, "State: connected")),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        return netmod.network_control(action, ssid=ssid)

    def test_wifi_on_verified_by_adapter_read_back(self):
        out = self._run("wifi_on")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Wi-Fi adapter enabled", str(out))

    def test_wifi_on_still_disabled_is_failed(self):
        out = self._run("wifi_on", adapter_state=(False, "Admin state: Disabled"))
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "failed")
        self.assertIn("still disabled", str(out))

    def test_wifi_on_unreadable_is_unverified(self):
        out = self._run("wifi_on", adapter_state=(None, "netsh output unreadable"))
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")

    def test_wifi_off_verified_by_adapter_read_back(self):
        out = self._run("wifi_off", adapter_state=(False, "Admin state: Disabled"))
        self.assertTrue(out.verified)
        self.assertIn("Wi-Fi adapter disabled", str(out))

    def test_wifi_connect_verified_by_ssid_read_back(self):
        out = self._run("wifi_connect", ssid="HomeNet", connected="HomeNet")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Connected to Wi-Fi network 'HomeNet' (verified)", str(out))

    def test_wifi_connect_wrong_ssid_is_unverified(self):
        out = self._run("wifi_connect", ssid="HomeNet", connected="CafeWiFi")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("CafeWiFi", str(out))


class TestEjectDriveR1b(unittest.TestCase):
    def _run(self, drive_present):
        from core import skills_plus_devices as mod
        proc = mock.Mock(returncode=0, stdout="", stderr="")

        class _ExistingPath:
            def __init__(self, p):
                self._p = p

            def exists(self):
                return True

        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "Path", _ExistingPath),
            mock.patch.object(outcome_mod, "drive_present",
                              return_value=drive_present),
            mock.patch("subprocess.run", return_value=proc),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        return mod.eject_drive("E")

    def test_eject_verified_when_drive_gone(self):
        out = self._run(False)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Ejected drive E: safely", str(out))

    def test_eject_still_present_is_unverified(self):
        out = self._run(True)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("still present", str(out))


class TestForgetWifiR1b(unittest.TestCase):
    def _run(self, listed):
        from core import skills_network_wifi_net as mod
        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "_run", return_value=(0, "")),
            mock.patch.object(outcome_mod, "wifi_profile_exists",
                              return_value=(listed, "profile list")),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        return mod.forget_wifi("HomeNet", confirm=True)

    def test_forget_verified_when_profile_gone(self):
        out = self._run(False)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Forgot 'HomeNet'", str(out))

    def test_forget_still_listed_is_failed(self):
        out = self._run(True)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "failed")

    def test_forget_unreadable_list_is_unverified(self):
        out = self._run(None)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertEqual(out.kind, "unverified")


class TestWindowLayeringR1b(unittest.TestCase):
    def _fake_user32(self, style=0):
        user32 = mock.Mock()
        user32.SetWindowPos.return_value = 1
        user32.SetLayeredWindowAttributes.return_value = 1
        user32.SetWindowLongW.return_value = 1
        user32.GetWindowLongW.return_value = style
        return user32

    def test_always_on_top_verified_by_style_read_back(self):
        from core import skills_network_display_input as mod
        fake = self._fake_user32()
        windll = types.SimpleNamespace(user32=fake)
        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "_pick_window",
                              return_value=((12345, "Test Window"), None)),
            mock.patch.object(ctypes, "windll", windll, create=True),
            mock.patch.object(outcome_mod, "window_topmost",
                              return_value=(True, "WS_EX_TOPMOST set")),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.window_always_on_top("Test", on=True)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("pinned on top of", str(out))

    def test_always_on_top_mismatch_is_plain_failure(self):
        from core import skills_network_display_input as mod
        fake = self._fake_user32()
        windll = types.SimpleNamespace(user32=fake)
        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "_pick_window",
                              return_value=((12345, "Test Window"), None)),
            mock.patch.object(ctypes, "windll", windll, create=True),
            mock.patch.object(outcome_mod, "window_topmost",
                              return_value=(False, "WS_EX_TOPMOST clear")),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.window_always_on_top("Test", on=True)
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)
        self.assertIn("did not keep the layering", out)

    def test_always_on_top_unreadable_is_unverified(self):
        from core import skills_network_display_input as mod
        fake = self._fake_user32()
        windll = types.SimpleNamespace(user32=fake)
        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "_pick_window",
                              return_value=((12345, "Test Window"), None)),
            mock.patch.object(ctypes, "windll", windll, create=True),
            mock.patch.object(outcome_mod, "window_topmost",
                              return_value=(None, "style unreadable")),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.window_always_on_top("Test", on=True)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertEqual(out.kind, "unverified")

    def test_opacity_restore_verified_by_style_read_back(self):
        from core import skills_network_display_input as mod
        fake = self._fake_user32(style=0)  # no WS_EX_LAYERED flag
        windll = types.SimpleNamespace(user32=fake)
        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "_pick_window",
                              return_value=((12345, "Test Window"), None)),
            mock.patch.object(ctypes, "windll", windll, create=True),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.window_opacity("Test", 100)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("fully opaque", str(out))

    def test_opacity_set_verified_by_alpha_read_back(self):
        from core import skills_network_display_input as mod
        fake = self._fake_user32(style=0)
        windll = types.SimpleNamespace(user32=fake)
        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "_pick_window",
                              return_value=((12345, "Test Window"), None)),
            mock.patch.object(ctypes, "windll", windll, create=True),
            mock.patch.object(outcome_mod, "window_opacity_value",
                              return_value=(50, "alpha 128")),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.window_opacity("Test", 50)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("50% opacity", str(out))

    def test_opacity_mismatch_is_plain_failure(self):
        from core import skills_network_display_input as mod
        fake = self._fake_user32(style=0)
        windll = types.SimpleNamespace(user32=fake)
        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "_pick_window",
                              return_value=((12345, "Test Window"), None)),
            mock.patch.object(ctypes, "windll", windll, create=True),
            mock.patch.object(outcome_mod, "window_opacity_value",
                              return_value=(80, "alpha 204")),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.window_opacity("Test", 50)
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)
        self.assertIn("reads at 80%", out)


class _FakeLocator:
    def __init__(self, value="typed text", raise_value=False):
        self.value = value
        self.raise_value = raise_value
        self.filled = None

    def get_attribute(self, name):
        return "text"

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def fill(self, text, timeout=None):
        self.filled = text

    def input_value(self, timeout=None):
        if self.raise_value:
            raise RuntimeError("unreadable field")
        return self.value


class _FakeBase:
    def __init__(self, loc):
        self._loc = loc

    def count(self):
        return 1

    @property
    def first(self):
        return self._loc

    def nth(self, i):
        return self._loc


class TestBrowserTypeR1b(unittest.TestCase):
    def _run(self, value="typed text", raise_value=False):
        from core.desktop import browser_interact as bi
        loc = _FakeLocator(value=value, raise_value=raise_value)
        base = _FakeBase(loc)
        page = types.SimpleNamespace(
            keyboard=types.SimpleNamespace(press=lambda k: None))
        patches = [
            mock.patch.object(bi, "_need_connection", return_value=None),
            mock.patch.object(bi, "_active_page", return_value=page),
            mock.patch.object(bi, "_login_wall_result", return_value=None),
            mock.patch.object(bi, "_scoped_locator",
                              return_value=(base, False)),
            mock.patch.object(bi, "_is_youtube_page", return_value=False),
            mock.patch.object(bi, "_element_identity", return_value="input#q"),
            mock.patch.object(bi, "_page_label", return_value="Example"),
            mock.patch.object(bi, "_settle", return_value=None),
            mock.patch.object(bi, "_submit_gate", return_value=None),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        return bi._impl_type("q", "typed text", "label", False), loc

    def test_fill_verified_when_field_reads_back(self):
        out, loc = self._run(value="typed text")
        self.assertEqual(loc.filled, "typed text")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Filled 'q'", str(out))

    def test_fill_mismatch_is_plain_failure(self):
        out, loc = self._run(value="other text")
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)
        self.assertIn("reads back as 'other text'", out)

    def test_fill_unreadable_is_unverified(self):
        out, loc = self._run(raise_value=True)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertEqual(out.kind, "unverified")
        self.assertIn("Filled 'q'", str(out))


class TestBrowserPressR1b(unittest.TestCase):
    def test_press_is_unverified_not_claimed(self):
        from core.desktop import browser_interact as bi
        page = types.SimpleNamespace(
            keyboard=types.SimpleNamespace(press=lambda k: None))
        patches = [
            mock.patch.object(bi, "_need_connection", return_value=None),
            mock.patch.object(bi, "_active_page", return_value=page),
            mock.patch.object(bi, "_login_wall_result", return_value=None),
            mock.patch.object(bi, "_press_delivers_text", return_value=False),
            mock.patch.object(bi, "_is_submit_key", return_value=False),
            mock.patch.object(bi, "_settle", return_value=None),
            mock.patch.object(bi, "_page_label", return_value="Example"),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = bi._impl_press("Tab")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("Pressed Tab", str(out))
        self.assertIn("whether it acted", str(out))


class TestSentKeysUnverifiedR1b(unittest.TestCase):
    def test_press_hotkey_clean_press_is_unverified(self):
        from core.desktop import control_input as ci
        with _fake_windows_input(ci) as fake:
            with mock.patch.object(ci, "_routed_backend", return_value=None):
                with mock.patch.object(ci, "_input_blocked", return_value=None):
                    with mock.patch.object(ci, "_expectation_refusal",
                                           return_value=None):
                        out = ci.press_hotkey("ctrl+c")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertEqual(
            str(out),
            "Pressed hotkey: ctrl+c (sent — whether the app acted on it "
            "was not confirmed.)")
        self.assertEqual(fake.events,
                         [("key", 0x11, False), ("key", 0x43, False),
                          ("key", 0x43, True), ("key", 0x11, True)])

    def test_type_text_full_success_is_unverified(self):
        from core.desktop import control_input as ci
        from core.desktop import uia_send
        fake_type = _FakeSendType()
        with _fake_windows_input(ci):
            with mock.patch.object(uia_send, "_sendinput_type",
                                   fake_type):
                with mock.patch.object(ci, "_routed_backend", return_value=None):
                    with mock.patch.object(ci, "_input_blocked", return_value=None):
                        with mock.patch.object(ci, "_password_block",
                                               return_value=None):
                            with mock.patch.object(ci, "_expectation_refusal",
                                                   return_value=None):
                                with mock.patch.object(ci, "_foreground_title",
                                                       return_value="Notepad"):
                                    with mock.patch.object(
                                            ci, "_foreground_is_shell",
                                            return_value=False):
                                        with mock.patch.object(
                                                ci, "_last_input_tick",
                                                return_value=None):
                                            out = ci.type_text("hi")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("Typed 2 character(s)", str(out))
        self.assertIn("Notepad", str(out))
        self.assertEqual(fake_type.typed, ["hi"])

    def test_mouse_scroll_is_unverified(self):
        from core.desktop import control_input as ci
        from core.desktop import cursor_input as cur_in

        class _Button:
            left = "left"
            right = "right"

        class _MouseController:
            def __init__(self):
                self.position = (0, 0)
                self.scrolled = []

            def click(self, button, count):
                return None

            def scroll(self, dx, dy):
                self.scrolled.append((dx, dy))

        fake_mouse = _MouseController()
        with mock.patch.object(cur_in, "_mouse_controller",
                               return_value=(fake_mouse, _Button, None)):
            with mock.patch.object(ci, "_routed_backend", return_value=None):
                with mock.patch.object(ci, "_input_blocked", return_value=None):
                    with mock.patch.object(ci, "_foreground_elevated",
                                           return_value=None):
                        out = ci.mouse_control("scroll", amount=3)
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("Scrolled by 3", str(out))
        self.assertEqual(fake_mouse.scrolled, [(0, 3)])

    def test_paste_text_is_unverified(self):
        from core import skills_network_display_input as mod
        from core.desktop import control as hub
        patches = [
            mock.patch.dict(sys.modules, {
                "pyperclip": types.SimpleNamespace(copy=lambda t: None)}),
            mock.patch.object(hub, "press_hotkey",
                              return_value="Pressed hotkey: ctrl+v"),
            mock.patch("core.input_guard.typing_blocked_reason",
                       return_value=None),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.paste_text("hello")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("Pasted", str(out))

    def test_type_clipboard_is_unverified(self):
        from core import skills_hands3_capture_scroll as mod
        from core.desktop import control as hub
        patches = [
            mock.patch.dict(sys.modules, {
                "pyperclip": types.SimpleNamespace(paste=lambda: "hello")}),
            mock.patch.object(hub, "type_text",
                              return_value="Typed 5 character(s) into the "
                                           "focused app. It went to: Notepad."),
            mock.patch.object(mod, "_input_blocked", return_value=None),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.type_clipboard()
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")

    def test_paste_snippet_is_unverified(self):
        from core import skills_productivity as mod
        from core.desktop import control as hub
        patches = [
            mock.patch.object(mod, "_load_store",
                              return_value=({"greet": {"text": "hello"}},
                                            False, False)),
            mock.patch.object(hub, "_input_blocked", return_value=None),
            mock.patch.object(hub, "write_clipboard",
                              return_value="Copied 5 character(s) to the "
                                           "clipboard."),
            mock.patch.object(hub, "press_hotkey",
                              return_value="Pressed hotkey: ctrl+v"),
            mock.patch("core.input_guard.typing_blocked_reason",
                       return_value=None),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.paste_snippet("greet")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("Pasted snippet 'greet'", str(out))


class TestCursorControlUnverifiedActR1b(unittest.TestCase):
    def test_click_scroll_drag_wrap_as_unverified(self):
        from core.desktop import cursor_control as cc
        for kind, msg in (("click", "Clicked at (100, 200)."),
                          ("scroll", "Scrolled by 3."),
                          ("drag", "Dragged from (1, 1) to (2, 2).")):
            out = cc._unverified_act(msg, kind)
            self.assertTrue(outcome_mod.is_outcome(out), kind)
            self.assertFalse(out.verified, kind)
            self.assertEqual(out.kind, "unverified", kind)
            self.assertIn(msg, str(out))

    def test_failures_and_moves_pass_through_plain(self):
        from core.desktop import cursor_control as cc
        for kind, msg in (("click", "Could not click: no window."),
                          ("scroll", "Nothing to scroll."),
                          ("move", "Moved the cursor to (1, 2).")):
            out = cc._unverified_act(msg, kind)
            self.assertIsInstance(out, str, kind)
            self.assertNotIsInstance(out, outcome_mod.Outcome, kind)
            self.assertEqual(out, msg, kind)

    def test_branches_call_the_wrapper(self):
        src = (_TREEROOT / "core" / "desktop"
               / "cursor_control.py").read_text(encoding="utf-8")
        self.assertGreater(src.count("return _unverified_act("), 2)


class TestPlayMusicMarkerR1b(unittest.TestCase):
    def _run(self, canned):
        from core import music_player as mp
        from core.funnel.tools_handlers_command_url import (
            play_music_wrapped)
        # GAPS-23 (F-V7): the handler's R1b marker wrap lives in
        # the module-level core function — same code the funnel
        # delegates to; the test pins the wrap's Outcome shapes.
        with mock.patch.object(mp, "play_music", return_value=canned):
            return play_music_wrapped("test query")

    def test_confirmed_playback_is_verified(self):
        out = self._run("Now playing X (audio confirmed)")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)

    def test_unconfirmed_playback_is_unverified(self):
        out = self._run("Playing X (couldn't confirm the audio)")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")

    def test_failure_line_passes_through_plain(self):
        out = self._run("Couldn't play that — no source worked.")
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)
        self.assertIn("Couldn't play", out)


class TestWallpaperApplyR1b(unittest.TestCase):
    def _run(self, current):
        # current is either a (value, evidence) tuple, or "MATCH"
        # (the registry reads back exactly this run's candidate).
        from core import wallpaper as wall
        from core.funnel.tools_handlers_pack_libwire import (
            wallpaper_apply_core)
        # GAPS-23 (F-V7): the apply+verify logic lives in the
        # module-level core function — same code the funnel
        # delegates to; the test pins its Outcome/dict shapes.
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "dawn.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n")
            state = wall.state_for_hour(10)
            candidate = str(png.resolve())
            if current == "MATCH":
                current = (candidate, "HKCU Wallpaper")
            patches = [
                mock.patch.object(wall, "pilot_status",
                                  return_value={"disposition": "PILOT"}),
                mock.patch.object(wall, "apply_wallpaper",
                                  return_value={"applied": True}),
                mock.patch.object(outcome_mod, "wallpaper_current",
                                  return_value=current),
            ]
            for p in patches:
                p.start()
            try:
                return wallpaper_apply_core(
                    opt_in=True, hour=10,
                    entries=[{"state": state, "art_path": str(png)}]), candidate
            finally:
                for pp in reversed(patches):
                    pp.stop()

    def test_apply_verified_when_registry_matches(self):
        out, _ = self._run("MATCH")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertIn("Wallpaper applied", str(out))

    def test_apply_mismatch_is_plain_failure(self):
        out, _ = self._run(("C:\\other.png", "HKCU Wallpaper"))
        # B1: set failures are failure dicts (tool_result.failure_result);
        # message text unchanged, so the "Windows still shows" wording pins.
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "failure")
        self.assertEqual(out.get("kind"), "wallpaper_not_set")
        self.assertIn("Windows still shows", out.get("message", ""))

    def test_apply_unreadable_is_unverified(self):
        out, _ = self._run((None, "registry unreadable"))
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertEqual(out.kind, "unverified")


class TestFileWritersR1b(unittest.TestCase):
    def test_env_backup_verified_by_re_read(self):
        from core import skills_system_bench as bench
        with tempfile.TemporaryDirectory() as tmp:
            out = bench.env_backup(folder=tmp)
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            made = list(Path(tmp).glob("visor_env_backup_*.json"))
            self.assertEqual(len(made), 1)
            self.assertGreater(made[0].stat().st_size, 0)

    def test_env_backup_unreadable_re_read_is_not_verified(self):
        from core import skills_system_bench as bench
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(outcome_mod, "file_written", return_value=(False, "re-read failed")):
                out = bench.env_backup(folder=tmp)
        self.assertFalse(outcome_mod.is_outcome(out)
                         and out.verified)
        self.assertIn("couldn't confirm", str(out).lower())

    def test_metronome_verified_by_re_read(self):
        from core import skills_creative_play as play
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                out = play.metronome(bpm=120, beats=4)
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            made = list((Path(tmp) / "Music" / "Nova").glob("*.wav"))
            self.assertEqual(len(made), 1)

    def test_compose_song_verified_by_re_read(self):
        from core import skills_creative2_art_music as art
        with tempfile.TemporaryDirectory() as tmp:
            dest = str(Path(tmp) / "song.wav")
            out = art.compose_song(mood="calm", seed=1, seconds=8,
                                   output=dest)
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertTrue(Path(dest).exists())
            self.assertGreater(Path(dest).stat().st_size, 0)

    @unittest.skipUnless(_HAS_PYPDF, "pypdf only in the Qt venv")
    def test_split_pdf_verified_by_part_re_read(self):
        from core import skills_media_convert as conv
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.pdf"
            writer = _pypdf_mod.PdfWriter()
            writer.add_blank_page(width=72, height=72)
            writer.add_blank_page(width=72, height=72)
            with open(src, "wb") as fh:
                writer.write(fh)
            out = conv.split_pdf(str(src), "1", output_dir=tmp)
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            parts = [p for p in Path(tmp).glob("*.pdf")
                     if p.name != "in.pdf"]
            self.assertEqual(len(parts), 1)

    @unittest.skipUnless(_HAS_PYPDF, "pypdf only in the Qt venv")
    def test_split_pdf_failed_re_read_is_not_verified(self):
        from core import skills_media_convert as conv
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.pdf"
            writer = _pypdf_mod.PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with open(src, "wb") as fh:
                writer.write(fh)
            with mock.patch.object(outcome_mod, "file_written",
                                       return_value=(False, "re-read failed")):
                out = conv.split_pdf(str(src), "1", output_dir=tmp)
            self.assertFalse(outcome_mod.is_outcome(out)
                             and out.verified)
            self.assertIn("could not be confirmed", str(out))
            self.assertIn("could not be confirmed", str(out))

    def test_download_attachment_verified_by_byte_re_read(self):
        import base64
        from core import google_services_gmail as gmail

        class _Exec:
            def __init__(self, payload):
                self._payload = payload

            def execute(self):
                return self._payload

        class _Attachments:
            def get(self, userId=None, messageId=None, id=None):
                return _Exec({"data": base64.urlsafe_b64encode(
                    b"hello").decode()})

        class _Messages:
            def get(self, userId=None, id=None, format=None):
                return _Exec({"payload": {"parts": [
                    {"filename": "a.txt", "mimeType": "text/plain",
                     "body": {"attachmentId": "att1", "size": 5}}]}})

            def attachments(self):
                return _Attachments()

        class _Users:
            def messages(self):
                return _Messages()

        class _Svc:
            def users(self):
                return _Users()

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(gmail, "_service_or_failure",
                                   return_value=(_Svc(), None)):
                out = gmail.download_attachment(message_id="m1",
                                                save_dir=tmp)
            self.assertTrue(outcome_mod.is_outcome(out))
            self.assertTrue(out.verified)
            self.assertEqual((Path(tmp) / "a.txt").read_bytes(), b"hello")

    def test_download_attachment_failed_re_read_is_not_verified(self):
        import base64
        from core import google_services_gmail as gmail

        class _Exec:
            def __init__(self, payload):
                self._payload = payload

            def execute(self):
                return self._payload

        class _Attachments:
            def get(self, userId=None, messageId=None, id=None):
                return _Exec({"data": base64.urlsafe_b64encode(
                    b"hello").decode()})

        class _Messages:
            def get(self, userId=None, id=None, format=None):
                return _Exec({"payload": {"parts": [
                    {"filename": "a.txt", "mimeType": "text/plain",
                     "body": {"attachmentId": "att1", "size": 5}}]}})

            def attachments(self):
                return _Attachments()

        class _Users:
            def messages(self):
                return _Messages()

        class _Svc:
            def users(self):
                return _Users()

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(gmail, "_service_or_failure",
                                   return_value=(_Svc(), None)):
                with mock.patch.object(outcome_mod, "file_written",
                                       return_value=(False, "re-read failed")):
                    out = gmail.download_attachment(message_id="m1",
                                                    save_dir=tmp)
            self.assertFalse(outcome_mod.is_outcome(out)
                             and out.verified)
            self.assertIn("couldn't confirm", str(out).lower())


class TestAppSettingsConfirmR1b(unittest.TestCase):
    def test_confirm_set_verified_on_match(self):
        from core import app_settings as app
        with mock.patch.object(app, "_current", return_value="dark"):
            out = app._confirm_set("theme", "dark", "Theme set to dark.")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)

    def test_confirm_set_mismatch_is_plain_failure(self):
        from core import app_settings as app
        with mock.patch.object(app, "_current", return_value="light"):
            out = app._confirm_set("theme", "dark", "Theme set to dark.")
        self.assertIsInstance(out, str)
        self.assertNotIsInstance(out, outcome_mod.Outcome)
        self.assertIn("still reads light", out)

    def test_confirm_set_unreadable_is_unverified(self):
        from core import app_settings as app
        with mock.patch.object(app, "_current",
                               side_effect=RuntimeError("no config")):
            out = app._confirm_set("theme", "dark", "Theme set to dark.")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertEqual(out.kind, "unverified")

    def test_every_set_leg_calls_confirm_set(self):
        src = (_TREEROOT / "core" / "app_settings.py").read_text(
            encoding="utf-8")
        self.assertGreaterEqual(src.count("_confirm_set("), 6)


class TestAlreadyVerifiedPinsR1b(unittest.TestCase):
    def test_connect_wifi_already_verified_by_ssid_poll(self):
        from core import skills_network_wifi_net as mod
        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "_run",
                              return_value=(0, "Connection request completed.")),
            mock.patch.object(mod, "_current_wifi_ssid",
                              return_value="HomeNet"),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.connect_wifi("HomeNet", confirm=True)
        self.assertIn("verified", out)

    def test_safe_eject_all_already_verifies_each_drive_left(self):
        from core import skills_system6_devices as mod
        patches = [
            mock.patch.object(mod, "_is_windows", return_value=True),
            mock.patch.object(mod, "_removable_drives", side_effect=[
                [{"letter": "E", "label": "USB"}],
                []]),
            mock.patch.object(mod, "_eject_drive", return_value=None),
            mock.patch("time.sleep", return_value=None),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.safe_eject_all()
        self.assertIn("ejected safely", out)

    def test_save_snippet_store_failure_is_honest(self):
        from core import skills_productivity as mod
        patches = [
            mock.patch.object(mod, "_load_store",
                              return_value=({}, False, False)),
            mock.patch.object(mod, "_save_store", return_value=False),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.save_snippet("addr", "123 Main St")
        self.assertIn("Couldn't save snippet", out)
        self.assertNotIn("Saved snippet", out)

    def test_descriptor_write_failure_is_honest_error(self):
        from core import trust_spine_tools as trust
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "trust.json")
            with mock.patch.object(trust.trust_mod,
                                   "validate_recordable_trust",
                                   side_effect=lambda rec: rec):
                with mock.patch.object(Path, "write_text",
                                       side_effect=OSError("disk full")):
                    out = trust.trust_record_handler(
                        "trust", "{}", target)
        result = json.loads(out)
        self.assertFalse(result["ok"])
        self.assertIn("disk full", result["reason"])

    def test_descriptor_write_success_reports_path(self):
        from core import trust_spine_tools as trust
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "trust.json")
            with mock.patch.object(trust.trust_mod,
                                   "validate_recordable_trust",
                                   side_effect=lambda rec: rec):
                out = trust.trust_record_handler("trust", "{}", target)
            result = json.loads(out)
            self.assertTrue(result["ok"])
            self.assertTrue(Path(target).exists())

    def test_send_email_boundary_is_pinned(self):
        doc = (_TREEROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        section = doc[doc.index("## 2c."):]
        self.assertIn("send_email", section)
        self.assertIn("SMTP acceptance", section)
        self.assertIn("delivered", section)

    def test_capture_vision_binding_is_pinned_unverifiable(self):
        doc = (_TREEROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        section = doc[doc.index("## 2c."):]
        for tool in ("capture_window", "screenshot_region",
                     "screenshot_to_text_note", "take_screenshot",
                     "webcam_snapshot"):
            self.assertIn(tool, section)
        self.assertIn("single-slot ledger", section)


class TestAuditTotalsR1b(unittest.TestCase):
    def test_totals_are_44_245_22_pool_0(self):
        # INTENTIONAL (B13): 7 hands tools moved from
        # honestly-unverifiable to seam-wired (vision landing leg
        # via core/tool_outcome.hands_landing_check), so the totals
        # moved 37/29 -> 44/22. The 7 are wired, not verified by
        # default: the verdict rides the seam Outcome only when the
        # B5 vision backend can read the screen; without weights
        # they return honestly `unverified` exactly as before.
        # INTENTIONAL (R7-4 meetily, 2026-11-10): already-verified
        # 245 -> 247 — start_meeting (A9-measure) + stop_meeting
        # (A4-filestat, see §2c); total 657 -> 661, effectful
        # 311 -> 313, read 346 -> 348.
        # INTENTIONAL (R7-5 kovrel, 2026-11-10): already-verified
        # 247 -> 249 — start_companion (A9-measure) + stop_companion
        # (A4-filestat, see §2c); total 661 -> 665, effectful
        # 313 -> 315, read 348 -> 350.
        # INTENTIONAL (R7-6 micro-wake-word, 2026-11-10):
        # already-verified 249 -> 251 — train_custom_wake_word
        # (A9-measure) + enable_custom_wake_model (A4-filestat,
        # see §2c); total 665 -> 669, effectful 315 -> 317,
        # read 350 -> 352.
        # INTENTIONAL (R7-8 proactive-agent, 2026-11-10):
        # already-verified 253 -> 255 — score_intervention
        # (A4-filestat) + run_proactivity_bench (A9-measure,
        # see §2c); total 673 -> 677, effectful 319 -> 321,
        # read 354 -> 356.
        # INTENTIONAL (R8-1 fastembed port, 2026-11-10):
        # already-verified 255 -> 256 — embed_knowledge
        # (A4-filestat, see §2c); total 677 -> 680,
        # effectful 321 -> 322, read 356 -> 358.
        # INTENTIONAL (R8-3 sumy port, 2026-11-10):
        # summarise_text is read-only (pure computation), so
        # effectful stays 322 and §2c does not move; total
        # 680 -> 681, read 358 -> 359, utilities 109 -> 110.
        import re
        doc = (_TREEROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        t = re.search(r"<!-- AUDIT-TOTALS: (\{.*\}) -->", doc)
        totals = json.loads(t.group(1))
        self.assertEqual(totals["seam_wired"], 44)
        self.assertEqual(totals["already_verified_in_handler"], 308)  # INTENTIONAL — 255 -> 256: embed_knowledge (A4-filestat, see §2c) (R8-1 fastembed port, 2026-11-10).; R8-4 screen-break port (2026-11-10): 256 -> 258 — eye_rest_timer (A1-store, see §2c) + log_glass (A4-filestat, see §2c); R8-5 magic-wormhole port (2026-11-10): 258 -> 260 — wormhole_offer (A1-store, see §2c) + wormhole_receive (A9-measure, see §2c); R8-6 yasb port (2026-11-10): 260 -> 261 — status_strip (A1-store, see \u00a72c); R8-7 basic-pitch port (2026-11-10): 261 -> 262 — audio_to_midi (A4-filestat, see \u00a72c); R8-9 pypresence adoption (2026-11-10): 262 -> 264 — discord_presence (A5-sysreadback, see \u00a72c) + set_discord_client_id (A4-filestat, see \u00a72c); R9-2 rustic adoption (2026-11-10): 264 -> 267 — init_backup_repo (A5-sysreadback, see \u00a72c) + backup_files (A5-sysreadback, see \u00a72c) + restore_backup (A4-filestat, see \u00a72c); R9-3 whoosh port (2026-11-10): 267 -> 268 — index_files (A4-filestat, see \u00a72c); R9-4 mutagen port (2026-11-10): 268 -> 269 — sync_folders_two_way (A4-filestat, see §2c); R9-8 elodie port (2026-11-10): 269 -> 270 — organise_photos (A4-filestat, see §2c); R11-1 ollama port (2026-11-10): 270 -> 272 — ollama_pull + ollama_delete (A5-sysreadback, see §2c); R16-8 event-planning pack (2026-11-11): 272 -> 277 — five writing tools (A1-store, see §2c); R16-9 attic pack (2026-11-11): 277 -> 278 — home_inventory (A1-store, see §2c); R16-10 GreaterWMS pack (2026-11-11): 278 -> 279 — warehouse_stock (A1-store, see §2c); R17-1 bookrail pack (2026-11-11): 279 -> 280 — bookings (SQLite-trigger + re-read, see §2c); R17-3 go2rtc pack (2026-11-11): 280 -> 281 — ip_camera added (capture-gate + re-read/filestat, see §2c); R17-4 MCSManager pack (2026-11-11): 281 -> 282 — game_server added (A1-store + A5-sysreadback + process-leg, see §2c); R17-5 komga pack (2026-11-11): 282 -> 283 — ebook_library added (A1-store + A4-filestat, see §2c); R17-6 openstays pack (2026-11-11): 283 -> 284 — lodging added (SQLite-trigger + re-read, see §2c); R17-7 qlcplus pack (2026-11-12): 284 -> 285 — stage_lighting added (A1-store + send-return, see §2c); R17-9 wardrobe pack (2026-11-12): 285 -> 286 — wardrobe added (A1-store, see §2c); R18-1 stripe pack (2026-11-12): 286 -> 287 — billing added (A1-store, see §2c); R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine
        self.assertEqual(totals["honestly_unverifiable"], 22)
        self.assertEqual(totals["next_pass_pool"], 0)
        self.assertEqual(
            totals["seam_wired"]
            + totals["already_verified_in_handler"]
            + totals["honestly_unverifiable"],
            totals["effectful"])
        self.assertEqual(totals["read"] + totals["effectful"],
                         totals["total"])
        self.assertEqual(totals["total"], 756)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_section_2c_covers_every_effectful_tool_once(self):
        import re
        doc = (_TREEROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        section = doc[doc.index("## 2c."):doc.index("## 3.")]
        rows = re.findall(r"^\| `([a-z0-9_]+)` \|", section, re.M)
        snap = json.loads(
            (_TREEROOT / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        effectful = {name for name, entry in snap.items()
                     if not entry["read"]}
        # INTENTIONAL (R7-4 meetily, 2026-11-10): 311 -> 313 —
        # start_meeting added to §2c as A9-measure, stop_meeting
        # as A4-filestat.
        # INTENTIONAL (R7-5 kovrel, 2026-11-10): 313 -> 315 —
        # start_companion added to §2c as A9-measure, stop_companion
        # as A4-filestat.
        # INTENTIONAL (R7-7 taskflow, 2026-11-10): 317 -> 319 —
        # run_workflow added to §2c as A9-measure,
        # define_workflow as A4-filestat.
        # INTENTIONAL (R7-8 proactive-agent, 2026-11-10): 319 -> 321 —
        # run_proactivity_bench added to §2c as A9-measure,
        # score_intervention as A4-filestat.
        # INTENTIONAL (R8-1 fastembed port, 2026-11-10): 321 -> 322 —
        # embed_knowledge added to §2c as A4-filestat.
        # INTENTIONAL (R8-4 screen-break port, 2026-11-10): 322 -> 324 —
        # eye_rest_timer added to §2c as A1-store,
        # log_glass as A4-filestat.
        self.assertEqual(len(effectful), 374)  # INTENTIONAL — R8-4 screen-break port (2026-11-10): 322 -> 324 — eye_rest_timer (A1-store) + log_glass (A4-filestat) added to §2c; R8-5 magic-wormhole port (2026-11-10): 324 -> 326 — wormhole_offer (A1-store) + wormhole_receive (A9-measure) added to §2c; R8-6 yasb port (2026-11-10): 326 -> 327 — status_strip (A1-store) added to \u00a72c; R8-7 basic-pitch port (2026-11-10): 327 -> 328 — audio_to_midi (A4-filestat) added to \u00a72c; R8-9 pypresence adoption (2026-11-10): 328 -> 330 — discord_presence (A5-sysreadback) + set_discord_client_id (A4-filestat) added to \u00a72c; R9-2 rustic adoption (2026-11-10): 330 -> 333 — init_backup_repo (A5-sysreadback) + backup_files (A5-sysreadback) + restore_backup (A4-filestat) added to \u00a72c; R9-3 whoosh port (2026-11-10): 333 -> 334 — index_files (A4-filestat) added to \u00a72c; R9-4 mutagen port (2026-11-10): 334 -> 335 — sync_folders_two_way (A4-filestat) added to §2c; R9-8 elodie port (2026-11-10): 335 -> 336 — organise_photos added (effectful); R11-1 ollama port (2026-11-10): 336 -> 338 — ollama_pull + ollama_delete (A5-sysreadback) added to §2c; R16-8 event-planning pack (2026-11-11): 338 -> 343 — five writing tools (A1-store) added to §2c; R16-9 attic pack (2026-11-11): 343 -> 344 — home_inventory (A1-store) added to §2c; R16-10 GreaterWMS pack (2026-11-11): 344 -> 345 — warehouse_stock (A1-store) added to §2c; R17-1 bookrail pack (2026-11-11): 345 -> 346 — bookings added (effectful); R17-3 go2rtc pack (2026-11-11): 346 -> 347 — ip_camera added (effectful); R17-4 MCSManager pack (2026-11-11): 347 -> 348 — game_server added (effectful); R17-5 komga pack (2026-11-11): 348 -> 349 — ebook_library added (effectful); R17-6 openstays pack (2026-11-11): 349 -> 350 — lodging added (effectful); R17-7 qlcplus pack (2026-11-12): 350 -> 351 — stage_lighting added (effectful); R17-9 wardrobe pack (2026-11-12): 351 -> 352 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 352 -> 353 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine
        self.assertEqual(len(rows), 374)  # INTENTIONAL — R8-4 screen-break port (2026-11-10): 322 -> 324 — eye_rest_timer (A1-store) + log_glass (A4-filestat) added to §2c; R8-5 magic-wormhole port (2026-11-10): 324 -> 326 — wormhole_offer (A1-store) + wormhole_receive (A9-measure) added to §2c; R8-6 yasb port (2026-11-10): 326 -> 327 — status_strip (A1-store) added to \u00a72c; R8-7 basic-pitch port (2026-11-10): 327 -> 328 — audio_to_midi (A4-filestat) added to \u00a72c; R8-9 pypresence adoption (2026-11-10): 328 -> 330 — discord_presence (A5-sysreadback) + set_discord_client_id (A4-filestat) added to \u00a72c; R9-2 rustic adoption (2026-11-10): 330 -> 333 — init_backup_repo (A5-sysreadback) + backup_files (A5-sysreadback) + restore_backup (A4-filestat) added to \u00a72c; R9-3 whoosh port (2026-11-10): 333 -> 334 — index_files (A4-filestat) added to \u00a72c; R9-4 mutagen port (2026-11-10): 334 -> 335 — sync_folders_two_way (A4-filestat) added to §2c; R9-8 elodie port (2026-11-10): 335 -> 336 — organise_photos added (effectful); R11-1 ollama port (2026-11-10): 336 -> 338 — ollama_pull + ollama_delete (A5-sysreadback) added to §2c; R16-8 event-planning pack (2026-11-11): 338 -> 343 — five writing tools (A1-store) added to §2c; R16-9 attic pack (2026-11-11): 343 -> 344 — home_inventory (A1-store) added to §2c; R16-10 GreaterWMS pack (2026-11-11): 344 -> 345 — warehouse_stock (A1-store) added to §2c; R17-1 bookrail pack (2026-11-11): 345 -> 346 — bookings added (effectful); R17-3 go2rtc pack (2026-11-11): 346 -> 347 — ip_camera added (effectful); R17-4 MCSManager pack (2026-11-11): 347 -> 348 — game_server added (effectful); R17-5 komga pack (2026-11-11): 348 -> 349 — ebook_library added (effectful); R17-6 openstays pack (2026-11-11): 349 -> 350 — lodging added (effectful); R17-7 qlcplus pack (2026-11-12): 350 -> 351 — stage_lighting added (effectful); R17-9 wardrobe pack (2026-11-12): 351 -> 352 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 352 -> 353 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine
        self.assertEqual(set(rows), effectful)
        self.assertEqual(len(set(rows)), len(rows))


if __name__ == "__main__":
    unittest.main()

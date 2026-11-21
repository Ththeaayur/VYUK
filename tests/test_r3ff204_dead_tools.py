"""Round-3 section F fix queue item 2 (F-F204): 4 gated tools dead in
the live-session funnel — and 21 more sharing the shape.

The bug (section F, parent-confirmed from the live code): the
`@_funnel_only_fn` guard on 25 module-level handlers was imported
via the absolute ``from core.funnel…`` import, so the guard closed
over the BARE ``core.*`` ``_FUNNEL_DISPATCH`` contextvar while the
live session dispatches through ``voice_assistant.core.*``, which
sets only the VA flag. Each approved call asked once, was approved,
then died with ``handler_error`` ("called outside the funnel"). The
4 confirm-gated tools were filed as F-F204 (``audio_to_midi``,
``backup_files``, ``init_backup_repo``, ``restore_backup``); the
scope probe (``scope_probe.py`` in the work dir) found the same
shape on 21 further tools, all fixed by this item.

The fix: ``core/funnel/tools_registry_core.py`` gains
``_funnel_dispatch_active()`` — the guard consults the local flag
AND the sibling identity's flag (via ``sys.modules``). A dispatch
IS a funnel dispatch either way, so the F-V7 capability boundary
holds exactly: the flag is set only by ``_funnel_dispatch_scope``,
in either identity, and only around a real dispatch. Direct calls
outside any dispatch still refuse (pinned below).

Harness: the F3 shape — a bare ``GeminiLiveSession`` driven through
``_execute_tool_sync`` (the live session's real tool-call path)
with the funnel loaded through the ``voice_assistant.core.*``
alias (the production identity) and the confirm handler registered
on the VA identity, as production does it. The D-O2 repro harness
shape is deliberately NOT used: its module graph resolves the
funnel to the bare ``core.*`` identity, which masks this bug
(F-F503). Bare-funnel controls use the bare registry directly.

This suite dirties data/ (audit records + activity lines, like
every suite that drives execute()) — data/ is snapshotted in
setUpModule and restored in tearDownModule, and receipt rows go
to a temp store per test (the C2-F2 idiom). The rustic binary is
not placed in the lab and the Basic Pitch model is not placed, so
the backup/audio legs assert the handler RAN and returned its
honest not-placed message — not a completed backup or a MIDI file.
"""
import math
import pathlib
import shutil
import struct
import sys
import tempfile
import types
import unittest
import wave
from types import SimpleNamespace

TREE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE))

# Synthesise the voice_assistant package alias (test_voicecontrol /
# F3 precedent) BEFORE any voice_assistant.* import.
if "voice_assistant" not in sys.modules:
    _pkg = types.ModuleType("voice_assistant")
    _pkg.__path__ = [str(TREE)]
    sys.modules["voice_assistant"] = _pkg
if "voice_assistant.core" not in sys.modules:
    _core = types.ModuleType("voice_assistant.core")
    _core.__path__ = [str(TREE / "core")]
    sys.modules["voice_assistant.core"] = _core
if "voice_assistant.gui" not in sys.modules:
    _gui = types.ModuleType("voice_assistant.gui")
    _gui.__path__ = [str(TREE / "gui")]
    sys.modules["voice_assistant.gui"] = _gui

try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001
    _fake_sd = types.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

from voice_assistant.core import confirm_gate as va_gate  # noqa: E402
from voice_assistant.core import permission_grants as va_pg  # noqa: E402
from voice_assistant.core import tool_receipts as va_receipts  # noqa: E402
from voice_assistant.core.funnel import tools_registry_core as va_core  # noqa: E402
from voice_assistant.core.voice.live_client import GeminiLiveSession  # noqa: E402
import core.confirm_gate as bare_gate  # noqa: E402
import core.funnel.tools_registry as bare_reg  # noqa: E402
import core.funnel.tools_registry_core as bare_core  # noqa: E402
from core import injection as _inj  # noqa: E402
from core.funnel import rule_of_two as _rot  # noqa: E402


def _fresh_state():
    """Clear session taint + Rule-of-Two legs between calls (C2-F2 idiom)."""
    _inj.clear_taint(force=True)
    _rot.reset_for_tests()


def _handler_ran(result):
    """True when the handler executed instead of dying at the F-V7 guard."""
    if not isinstance(result, dict):
        return False
    if result.get("status") == "failure" and result.get("kind") == "handler_error":
        return False
    msg = str(result.get("result") or result.get("message") or "")
    return "called outside the funnel" not in msg


class _FF204Base(unittest.TestCase):
    """VA live-session harness (F3 shape) + temp receipts store."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="r3ff204_")
        tmp = pathlib.Path(cls._tmp.name)
        cls.out = tmp / "out"
        cls.out.mkdir()
        cls.perm_store = tmp / "perm_store.json"
        # Data snapshot: restored in tearDownClass (belt and braces —
        # the neighbour runner restores data/ per leg as well).
        cls._data_snap = tmp / "data_snap"
        shutil.copytree(TREE / "data", cls._data_snap)
        # A real 2 s sine WAV (22050 Hz mono) for audio_to_midi.
        cls.wav = cls.out / "f4_tone.wav"
        with wave.open(str(cls.wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            frames = b"".join(
                struct.pack("<h", int(12000 * math.sin(
                    2 * math.pi * 440 * i / 22050)))
                for i in range(22050 * 2))
            w.writeframes(frames)
        cls.repo = str(cls.out / "f4_repo")
        va_pg.set_path_override(cls.perm_store)
        va_pg.add_folder(str(cls.out), via="settings")
        for pid in va_pg.PERMISSION_IDS:
            va_pg.set_permission_state(pid, "granted", via="settings")
        cls.session = object.__new__(GeminiLiveSession)
        cls.session._cancelled_call_ids = set()
        cls.session._running = True
        cls.decision = True
        cls.asked = []

        def _handler(name, summary=None, *a, **k):
            cls.asked.append(name)
            return cls.decision

        cls._handler = _handler
        va_gate.register_handler(_handler)
        bare_gate.register_handler(_handler)

    @classmethod
    def tearDownClass(cls):
        va_gate.clear_handler()
        bare_gate.clear_handler()
        va_receipts.reset_default()
        data = TREE / "data"
        shutil.rmtree(data, ignore_errors=True)
        shutil.copytree(cls._data_snap, data)
        cls._tmp.cleanup()

    def setUp(self):
        self._rtmp = tempfile.TemporaryDirectory(prefix="r3ff204_rx_")
        self._receipts = pathlib.Path(self._rtmp.name) / "tool_receipts.jsonl"
        va_receipts.install_store(self._receipts)
        va_receipts.reset_receipt_degraded()
        type(self).decision = True
        type(self).asked.clear()
        _fresh_state()

    def tearDown(self):
        va_receipts.reset_default()
        va_receipts.reset_receipt_degraded()
        self._rtmp.cleanup()
        _fresh_state()

    def _run(self, name, args, decision=True):
        type(self).decision = decision
        type(self).asked.clear()
        fc = SimpleNamespace(name=name, args=dict(args), id="ff204-" + name)
        return self.session._execute_tool_sync(fc)

    def _assert_ran(self, result, label):
        self.assertTrue(_handler_ran(result),
                        f"{label}: handler died at the F-V7 guard: {result!r:.300}")
        self.assertEqual(result.get("status"), "success",
                         f"{label}: expected success, got {result!r:.300}")

    def _assert_ran_or_honest_refusal(self, result, label):
        """For start_meeting: the handler runs, but the lab has no
        microphone, so the business logic honestly refuses to start
        the meeting ("Could not open the microphone…"). The guard
        passing is the assertion; the refusal is the honest lab
        outcome, not a guard failure."""
        self.assertTrue(_handler_ran(result),
                        f"{label}: handler died at the F-V7 guard: {result!r:.300}")
        if result.get("status") == "refused":
            self.assertIn("microphone", str(result.get("message") or ""),
                          f"{label}: refusal names no honest cause: {result!r:.300}")
        else:
            self.assertEqual(result.get("status"), "success",
                             f"{label}: expected success, got {result!r:.300}")


# Tools whose honest lab outcome is a refusal, not a success — the
# guard passing is the assertion for them.
_RAN_ONLY = {"start_meeting"}


# The 25 tools whose guards close over the bare _FUNNEL_DISPATCH
# (scope_probe.py): the 4 filed gated tools + 21 same-shape tools.
# Benign args per tool: reads/status shapes where they exist, so no
# leg starts a server, a recording, or a real backup.
SWEEP_ARGS = {
    "audio_to_midi": lambda s: {"audio_path": str(s.wav),
                                "output": str(s.out / "f4_out.mid")},
    "audio_to_midi_status": lambda s: {},
    "backup_files": lambda s: {"sources": [str(s.wav)], "repo_path": s.repo},
    "backup_status": lambda s: {"repo_path": s.repo},
    "breathing_guide": lambda s: {"pattern": "box", "cycles": 1},
    "companion_pairing": lambda s: {},
    "companion_status": lambda s: {},
    "discord_presence": lambda s: {"action": "status"},
    "eye_rest_timer": lambda s: {"action": "status"},
    "init_backup_repo": lambda s: {"repo_path": s.repo},
    "list_backup_snapshots": lambda s: {"repo_path": s.repo},
    "log_glass": lambda s: {"action": "status"},
    "meeting_status": lambda s: {},
    "restore_backup": lambda s: {"snapshot": "latest",
                                 "destination": str(s.out / "f4_restored"),
                                 "repo_path": s.repo},
    "set_discord_client_id": lambda s: {"client_id": ""},
    "start_companion": lambda s: {"port": 0, "files_root": ""},
    "start_meeting": lambda s: {"title": ""},
    "status_strip": lambda s: {"action": "status"},
    "stop_companion": lambda s: {},
    "stop_meeting": lambda s: {},
    "summarise_text": lambda s: {"text": "One two three. Four five six.",
                                 "max_sentences": 1},
    "transcribe_audio": lambda s: {"filename": ""},
    "wormhole_offer": lambda s: {"action": "status"},
    "wormhole_receive": lambda s: {"phrase": "", "sender": "",
                                  "destination": ""},
    "wormhole_status": lambda s: {},
}
# start_companion / start_meeting need a stop after the leg.
_STOP_AFTER = {"start_companion": "stop_companion",
               "start_meeting": "stop_meeting"}


class TestFF204DeadTools(_FF204Base):
    """F-F204: the four filed tools + the wider shape, through the
    live-session (VA) funnel."""

    def test_filed_four_run_through_va_session(self):
        """The 4 filed gated tools: ask once, approve, handler runs."""
        for name in ("audio_to_midi", "init_backup_repo",
                     "backup_files", "restore_backup"):
            with self.subTest(tool=name):
                r = self._run(name, SWEEP_ARGS[name](self))
                self._assert_ran(r, name)
                self.assertIn(name, type(self).asked,
                              f"{name}: confirm ask never fired")

    def test_same_shape_reads_run(self):
        """Same-shape read tools from the two filed packs."""
        for name in ("audio_to_midi_status", "list_backup_snapshots",
                     "backup_status"):
            with self.subTest(tool=name):
                r = self._run(name, SWEEP_ARGS[name](self))
                self._assert_ran(r, name)

    def test_same_shape_other_packs_run(self):
        """The remaining bare-guarded tools (other packs), read/status
        shapes — no server, no recording, no real backup started."""
        for name in ("summarise_text", "meeting_status", "wormhole_status",
                     "status_strip", "eye_rest_timer", "breathing_guide",
                     "log_glass", "wormhole_offer", "discord_presence",
                     "companion_status", "companion_pairing",
                     "stop_companion", "stop_meeting", "transcribe_audio",
                     "wormhole_receive", "set_discord_client_id"):
            with self.subTest(tool=name):
                r = self._run(name, SWEEP_ARGS[name](self))
                self._assert_ran(r, name)

    def test_start_tools_run_and_stop(self):
        """start_companion / start_meeting run (no handler_error) and
        are stopped again in the same test."""
        for name in ("start_companion", "start_meeting"):
            with self.subTest(tool=name):
                try:
                    r = self._run(name, SWEEP_ARGS[name](self))
                    if name in _RAN_ONLY:
                        self._assert_ran_or_honest_refusal(r, name)
                    else:
                        self._assert_ran(r, name)
                finally:
                    self._run(_STOP_AFTER[name], {})

    def test_all_25_bare_guarded_tools_run(self):
        """Whole-class pin: every one of the 25 tools whose guard
        closes over the bare flag runs through the VA session funnel."""
        self.assertEqual(len(SWEEP_ARGS), 25)
        for name in sorted(SWEEP_ARGS):
            with self.subTest(tool=name):
                try:
                    r = self._run(name, SWEEP_ARGS[name](self))
                    if name in _RAN_ONLY:
                        self._assert_ran_or_honest_refusal(r, name)
                    else:
                        self._assert_ran(r, name)
                finally:
                    if name in _STOP_AFTER:
                        self._run(_STOP_AFTER[name], {})

    def test_deny_still_refuses(self):
        """A denied confirm still refuses honestly — nothing runs."""
        r = self._run("init_backup_repo", {"repo_path": self.repo + "_deny"},
                      decision=False)
        self.assertEqual(r.get("status"), "refused")
        self.assertEqual(r.get("kind"), "not_confirmed")
        rows = []
        if self._receipts.exists():
            import json as _json
            rows = [_json.loads(ln) for ln in
                    self._receipts.read_text().splitlines() if ln.strip()]
        outcomes = [x for x in rows if x.get("type") == "outcome"]
        self.assertTrue(outcomes, "no outcome row recorded for the denial")
        self.assertEqual(outcomes[-1].get("status"), "refused")

    def test_bare_funnel_control_still_green(self):
        """The bare funnel still runs the same handlers (both dispatch
        directions work after the fix)."""
        for name, args in (
                ("init_backup_repo", {"repo_path": str(self.out / "f4_repo_bare")}),
                ("audio_to_midi", {"audio_path": str(self.wav),
                                   "output": str(self.out / "f4_out_bare.mid")})):
            with self.subTest(tool=name):
                r = bare_reg.tools.execute(name, args)
                self._assert_ran(r, "bare " + name)

    def test_direct_call_still_refused(self):
        """The F-V7 boundary still holds: a direct call outside any
        dispatch refuses — the fix opens the guard to the sibling
        identity's dispatch, not to direct calls."""
        from voice_assistant.core.basic_pitch import (  # noqa: E402
            _tool_audio_to_midi_status as va_status)
        from core.basic_pitch import (  # noqa: E402
            _tool_audio_to_midi_status as bare_status)
        for fn in (va_status, bare_status):
            with self.subTest(fn=fn.__module__):
                with self.assertRaises(RuntimeError) as ctx:
                    fn()
                self.assertIn("called outside the funnel", str(ctx.exception))

    def test_guard_reads_both_identities(self):
        """Mechanism pin: a guard created by one identity passes
        inside the OTHER identity's dispatch scope, and refuses
        outside any scope."""
        self.assertFalse(va_core._funnel_dispatch_active())
        self.assertFalse(bare_core._funnel_dispatch_active())
        va_ok = va_core._funnel_only_fn(lambda: "va-ok")
        bare_ok = bare_core._funnel_only_fn(lambda: "bare-ok")
        with self.assertRaises(RuntimeError):
            va_ok()
        with self.assertRaises(RuntimeError):
            bare_ok()
        with va_core._funnel_dispatch_scope():
            self.assertEqual(va_ok(), "va-ok")
            self.assertEqual(bare_ok(), "bare-ok")
        with bare_core._funnel_dispatch_scope():
            self.assertEqual(va_ok(), "va-ok")
            self.assertEqual(bare_ok(), "bare-ok")
        self.assertFalse(va_core._funnel_dispatch_active())
        self.assertFalse(bare_core._funnel_dispatch_active())


if __name__ == "__main__":
    unittest.main()

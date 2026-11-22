"""Loophole batch 4 — speaker identity: #1, #20, L-27, L-28, #19.

The five items, one build (2026-11-22, user-approved: close all
32 Stage 0 loopholes). The design, stated honestly:

* There is NO voiceprint here. The on-device voiceprint module
  (core/voice_lock.py, PF-16) has no cleared engine and no
  cleared weights — SpeechBrain/Resemblyzer would be new heavy
  dependencies whose pre-trained weights have no
  commercial-redistribution clearance — so verify() returns
  "unavailable" and must never fall back to a weaker substitute.
  What is built instead is owner proof: a session is PROVEN
  only when the owner proved it (started from the app UI, or
  unlocked on screen / with the owner-set voice PIN). A
  voiceprint that cannot tell the owner from a visitor is
  worse than no voiceprint; a session lock that can is the
  honest foundation until a cleared engine exists.
* L-27: the speaker lock defaults ON (the field default AND the
  env-overlay fallback — the old overlay fell back to OFF, so
  the field default alone would not have turned it on), and it
  applies to every voice session however it started: a session
  is unproven until the owner proves it.
* #1/#20/#19: a spoken yes/no resolves a pending confirmation
  ONLY in an owner-proven session (or with the lock off). In an
  unproven session the voice answer is refused in plain words
  and resolves NOTHING — the popup stays up for the owner to
  answer on screen. The voice PIN ("voice PIN 1234") is the
  voice-only unlock, opt-in, session-scoped, stored protected
  (secrets_store, data/voice_pin.bin, 0600, never echoed).
* L-28: permission grants are NEVER by voice
  (grant_session_by_voice refuses and names Settings →
  Permissions). The store-level grant (made on screen) is what
  the funnel and the capture guard consult; revoking by voice
  stays allowed (it removes access).

The suite runs under both interpreters (sys + Qt). The hook
legs and the Settings card legs are Qt-only (the mixin module
imports PySide6); the config, PIN-store, owner-proof and grant
legs run on both. The hook legs drive the REAL
_maybe_confirm_reply on a fake window against the REAL pending
registry the GUI reads (the voice_assistant.core.confirm_gate
identity), with the pending request created by the REAL
request_confirmation; the funnel legs go through the REAL
funnel (tools.execute). The suite leaves data/ as it found it
(import-time snapshot + tearDownModule restore, the batch-3
pattern — importing core.memory migrates assistant.db).
"""

import os
import pathlib
import shutil
import sys
import tempfile
import threading
import types as _types_mod
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Snapshot the live data/ dir HERE — at module import, BEFORE any
# core.* import below (the batch-3 pattern: importing core.memory
# migrates the live assistant.db, so a later snapshot could never
# restore data/ to the state it found).
import shutil as _shutil

_DATA_SNAPSHOT = tempfile.TemporaryDirectory(prefix="b4_test_data_")
if (ROOT / "data").is_dir():
    _shutil.copytree(ROOT / "data", _DATA_SNAPSHOT.name, dirs_exist_ok=True)

import core.config_spec as cs  # noqa: E402
import core.confirm_gate as flat_gate  # noqa: E402
import core.permission_grants as pga  # noqa: E402
import core.settings_voice as sv  # noqa: E402
import core.voice_pin as voice_pin  # noqa: E402
from core.funnel import live_origin  # noqa: E402
from core.funnel import tools_gate as flat_tools_gate  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication as _QA  # noqa: E402
    from PySide6.QtWidgets import QLabel as _QLabel  # noqa: E402
    from PySide6.QtWidgets import QVBoxLayout as _QVBoxLayout  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = _QLabel = _QVBoxLayout = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
ServicesConfirmMixin = None
SettingsGroupsMixin = None
va_gate = None
va_tools_gate = None
va_config = None
va_voice_pin = None
va_data_dir = None


def setUpModule():
    """Snapshot the voice_assistant.* modules; synthesise the
    alias (the test_settings_redesign shape) and import the
    aliased gate/config/PIN modules the GUI reads, plus the GUI
    mixins under Qt. The live data/ snapshot is taken at module
    import (see _DATA_SNAPSHOT above)."""
    global _APP, _VA_SNAPSHOT, ServicesConfirmMixin
    global SettingsGroupsMixin, va_gate, va_tools_gate, va_config
    global va_voice_pin, va_data_dir
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    try:
        import voice_assistant.core.confirm_gate as _vg  # noqa: E402
        import voice_assistant.core.config as _vc  # noqa: E402
        import voice_assistant.core.funnel.tools_gate as _vtg  # noqa: E402
        import voice_assistant.core.voice_pin as _vvp  # noqa: E402
        va_gate = _vg
        va_config = _vc
        va_tools_gate = _vtg
        va_voice_pin = _vvp
        va_data_dir = str(_vc.DATA_DIR)
    except Exception:  # noqa: BLE001 — alias import failed
        va_gate = va_tools_gate = va_config = None
        va_voice_pin = None
        va_data_dir = str(ROOT / "data")
    if not _QT:
        return
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    try:
        from gui.window_services_confirm import (  # noqa: E402
            ServicesConfirmMixin as _SCM)
        ServicesConfirmMixin = _SCM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        ServicesConfirmMixin = None
    try:
        from gui.window_settings_groups import (  # noqa: E402
            SettingsGroupsMixin as _SGM)
        SettingsGroupsMixin = _SGM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        SettingsGroupsMixin = None


def tearDownModule():
    """Restore the voice_assistant.* modules and the live data/
    dir (from the import-time snapshot), so the suite leaves the
    tree as it found it."""
    _live_data = ROOT / "data"
    if _live_data.is_dir():
        shutil.rmtree(_live_data)
    _live_data.mkdir(parents=True)
    shutil.copytree(_DATA_SNAPSHOT.name, _live_data, dirs_exist_ok=True)
    _DATA_SNAPSHOT.cleanup()
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        if key not in _VA_SNAPSHOT:
            del sys.modules[key]
    for key, mod in _VA_SNAPSHOT.items():
        sys.modules[key] = mod


def _set_provenance_both(value):
    """The exact call the window makes at session start, on BOTH
    module identities (one identity in production)."""
    flat_tools_gate.set_session_provenance(value)
    if va_tools_gate is not None:
        va_tools_gate.set_session_provenance(value)


def _register_handler(fn):
    """Register a confirm handler on every confirm_gate module
    identity present (the flat core.* one the funnel reads, and
    the voice_assistant.* one the GUI synthesises)."""
    flat_gate.register_handler(fn)
    if va_gate is not None and va_gate is not flat_gate:
        va_gate.register_handler(fn)


def _clear_handler():
    for mod in (flat_gate, va_gate):
        if mod is None:
            continue
        try:
            mod.clear_handler()
        except Exception:
            pass


# ------------------------------------------------------- L-27: the default
class LockDefaultTests(unittest.TestCase):
    """The lock defaults ON — the field default AND the
    env-overlay fallback (the old overlay fell back to OFF, so
    the field default alone would not have turned it on)."""

    def test_field_default_is_on(self):
        self.assertIs(cs.ConfigSpec().session_provenance_lock, True)

    def test_build_spec_no_env_is_on(self):
        self.assertIs(cs.build_spec({}).session_provenance_lock, True)

    def test_garbled_env_falls_back_to_on(self):
        spec = cs.build_spec({"SESSION_PROVENANCE_LOCK": "maybe"})
        self.assertIs(spec.session_provenance_lock, True)

    def test_explicit_off_is_respected(self):
        spec = cs.build_spec({"SESSION_PROVENANCE_LOCK": "false"})
        self.assertIs(spec.session_provenance_lock, False)

    def test_explicit_on_is_respected(self):
        spec = cs.build_spec({"SESSION_PROVENANCE_LOCK": "true"})
        self.assertIs(spec.session_provenance_lock, True)

    def test_legacy_alias_off_is_respected(self):
        spec = cs.build_spec({"SPEAKER_LOCK": "false"})
        self.assertIs(spec.session_provenance_lock, False)

    def test_legacy_alias_on_is_respected(self):
        spec = cs.build_spec({"SPEAKER_LOCK": "true"})
        self.assertIs(spec.session_provenance_lock, True)

    def test_env_read_with_no_env_is_on(self):
        self.assertIs(cs.env("SESSION_PROVENANCE_LOCK", {}), True)


class LockReadActionsTests(unittest.TestCase):
    """The lock's read exemption, completed by loophole batch 4.

    Turning the lock ON by default exposed a gap in
    _is_read_action: it had no branch for scheduled_agents,
    context_menu_action, journal_action, menu_action,
    system_surface_action or run_command's job actions, so a
    wake session's list/status/digest ASKED for confirmation
    despite _gate_action naming them free reads. The branches
    added for them mirror _gate_action's own free-read lists.
    These legs pin the shape with the lock ON and provenance
    "unknown" (a wake session): reads stay free, acting
    actions still gate."""

    def setUp(self):
        super().setUp()
        from core import injection as _inj  # noqa: E402
        from core.config import config as _app_config  # noqa: E402
        from core.funnel.tools_gate import (  # noqa: E402
            _is_read_action as _ira,
            _needs_confirmation as _nc)
        from core.funnel.tool_classification import (  # noqa: E402
            classify_call as _cc)
        self._ira = _ira
        self._nc = _nc
        self._cc = _cc
        self._inj = _inj
        self._inj.clear_taint(force=True)
        # Lock ON + unproven session, whatever an earlier test
        # left behind; the flat config is what the flat gate
        # reads (the aliased config is the GUI's copy).
        self._lock_patch = mock.patch.object(
            _app_config, "session_provenance_lock", True)
        self._lock_patch.start()
        _set_provenance_both("unknown")

    def tearDown(self):
        self._lock_patch.stop()
        self._inj.clear_taint(force=True)
        _set_provenance_both("unknown")
        super().tearDown()

    def _assert_free_read(self, name, args):
        self.assertTrue(self._ira(name, args), (name, args))
        self.assertFalse(self._nc(name, args), (name, args))

    def _assert_gates(self, name, args):
        self.assertFalse(self._ira(name, args), (name, args))
        self.assertTrue(self._nc(name, args), (name, args))

    def test_scheduled_agents_reads_stay_free(self):
        for action in ("list", "status", "digest"):
            self._assert_free_read(
                "scheduled_agents", {"action": action})

    def test_scheduled_agents_acting_actions_still_gate(self):
        for action in ("setup_digest", "cancel", "run_now",
                       "save_routine", "run_routine"):
            self._assert_gates(
                "scheduled_agents", {"action": action})

    def test_menu_and_context_menu_list_stay_free(self):
        self._assert_free_read("menu_action", {"action": "list"})
        self._assert_free_read(
            "context_menu_action", {"action": "list"})
        self._assert_gates("menu_action", {"action": "invoke"})
        self._assert_gates(
            "context_menu_action", {"action": "invoke"})

    def test_journal_reads_stay_free(self):
        for action in ("list", "plan"):
            self._assert_free_read("journal_action", {"action": action})
        self._assert_gates("journal_action", {"action": "reconcile"})
        self._assert_gates("journal_action", {"action": "compensate"})

    def test_system_surface_reads_stay_free(self):
        self._assert_free_read(
            "system_surface_action",
            {"surface": "tray", "action": "list"})
        self._assert_free_read(
            "system_surface_action",
            {"surface": "start", "action": "search"})
        self._assert_gates(
            "system_surface_action",
            {"surface": "start", "action": "list"})
        self._assert_gates(
            "system_surface_action",
            {"surface": "tray", "action": "invoke"})

    def test_run_command_job_reads_stay_free(self):
        for action in ("poll", "status", "wait", "list"):
            self._assert_free_read("run_command", {"action": action})
        # Cancelling a job is an effect, not a read; a bare
        # launch is a new command — both still gate.
        self._assert_gates("run_command", {"action": "cancel"})
        self._assert_gates("run_command", {})

    def test_classify_call_agrees_with_the_read_split(self):
        for name, args in (
                ("scheduled_agents", {"action": "list"}),
                ("menu_action", {"action": "list"}),
                ("journal_action", {"action": "plan"}),
                ("run_command", {"action": "poll"})):
            self.assertTrue(
                self._cc(name, args).is_read, (name, args))
        self.assertFalse(
            self._cc("scheduled_agents",
                     {"action": "setup_digest"}).is_read)


# ------------------------------------------------------- the voice PIN store
class VoicePinTests(unittest.TestCase):
    """The PIN phrase parses only well-formed 4–8 digit tokens,
    and the store is protected (never plaintext, never partial)."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="b4_pin_")
        self.data_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def test_parse_voice_pin_phrase(self):
        self.assertEqual(
            voice_pin.parse_voice_pin_phrase("voice PIN 1234"), "1234")
        self.assertEqual(
            voice_pin.parse_voice_pin_phrase("unlock voice 1234"),
            "1234")
        self.assertEqual(
            voice_pin.parse_voice_pin_phrase("my voice PIN is 1234"),
            "1234")
        self.assertEqual(
            voice_pin.parse_voice_pin_phrase("Voice pin 1234."),
            "1234")

    def test_parse_rejects_malformed_phrases(self):
        self.assertIsNone(
            voice_pin.parse_voice_pin_phrase("voice PIN 123456789"))
        self.assertIsNone(
            voice_pin.parse_voice_pin_phrase("voice PIN 123"))
        self.assertIsNone(
            voice_pin.parse_voice_pin_phrase("voice PIN 12ab"))
        self.assertIsNone(
            voice_pin.parse_voice_pin_phrase("voice PIN"))
        self.assertIsNone(voice_pin.parse_voice_pin_phrase("yes"))
        self.assertIsNone(voice_pin.parse_voice_pin_phrase(""))

    def test_set_check_has_clear(self):
        self.assertFalse(voice_pin.has_voice_pin(self.data_dir))
        self.assertFalse(voice_pin.check_voice_pin(self.data_dir, "1234"))
        voice_pin.set_voice_pin(self.data_dir, "1234")
        self.assertTrue(voice_pin.has_voice_pin(self.data_dir))
        self.assertTrue(voice_pin.check_voice_pin(self.data_dir, "1234"))
        self.assertFalse(voice_pin.check_voice_pin(self.data_dir, "9999"))
        voice_pin.set_voice_pin(self.data_dir, "")
        self.assertFalse(voice_pin.has_voice_pin(self.data_dir))

    def test_bad_pin_raises_and_stores_nothing(self):
        for bad in ("123", "123456789", "12ab", "abcd"):
            with self.assertRaises(ValueError):
                voice_pin.set_voice_pin(self.data_dir, bad)
        self.assertFalse(voice_pin.has_voice_pin(self.data_dir))

    def test_stored_pin_is_not_plaintext(self):
        voice_pin.set_voice_pin(self.data_dir, "1234")
        raw = pathlib.Path(self.data_dir, "voice_pin.bin").read_bytes()
        self.assertNotIn(b"1234", raw)

    def test_corrupt_store_reads_as_unset(self):
        pathlib.Path(self.data_dir, "voice_pin.bin").write_bytes(b"junk")
        self.assertFalse(voice_pin.has_voice_pin(self.data_dir))
        self.assertFalse(voice_pin.check_voice_pin(self.data_dir, "1234"))


# ------------------------------------------------------- owner proof state
class OwnerProofTests(unittest.TestCase):
    """The proof state follows the session start, and the voice
    answer is allowed only when proven (or the lock is off)."""

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(va_tools_gate,
                             "aliased tools_gate did not import")
        _set_provenance_both("unknown")

    def tearDown(self):
        _set_provenance_both("unknown")
        super().tearDown()

    def test_wake_start_is_unproven(self):
        va_tools_gate.set_session_provenance("auto")
        self.assertFalse(va_tools_gate.session_owner_proven())
        self.assertFalse(va_tools_gate.voice_confirm_allowed())

    def test_user_start_is_proven(self):
        va_tools_gate.set_session_provenance("user")
        self.assertTrue(va_tools_gate.session_owner_proven())
        self.assertTrue(va_tools_gate.voice_confirm_allowed())
        self.assertEqual(
            va_tools_gate.session_owner_proof_reason(), "user-started")

    def test_unknown_is_unproven(self):
        va_tools_gate.set_session_provenance("unknown")
        self.assertFalse(va_tools_gate.session_owner_proven())
        self.assertFalse(va_tools_gate.voice_confirm_allowed())

    def test_screen_unlock_proves_then_unprove_clears(self):
        va_tools_gate.set_session_provenance("auto")
        va_tools_gate.mark_session_owner_proven("screen")
        self.assertTrue(va_tools_gate.session_owner_proven())
        self.assertEqual(
            va_tools_gate.session_owner_proof_reason(), "screen")
        self.assertTrue(va_tools_gate.voice_confirm_allowed())
        va_tools_gate.mark_session_owner_unproven()
        self.assertFalse(va_tools_gate.session_owner_proven())
        self.assertIsNone(
            va_tools_gate.session_owner_proof_reason())
        self.assertFalse(va_tools_gate.voice_confirm_allowed())

    def test_status_dict_shape(self):
        va_tools_gate.set_session_provenance("auto")
        status = va_tools_gate.session_owner_proof_status()
        self.assertEqual(status["proven"], False)
        self.assertEqual(status["lock_on"], True)
        self.assertEqual(status["provenance"], "auto")

    def test_lock_off_allows_an_unproven_voice_answer(self):
        va_tools_gate.set_session_provenance("auto")
        with mock.patch.object(va_config.config,
                               "session_provenance_lock", False):
            self.assertTrue(va_tools_gate.voice_confirm_allowed())

    def test_broken_config_fails_closed(self):
        va_tools_gate.set_session_provenance("auto")

        class _Broken:
            @property
            def session_provenance_lock(self):
                raise RuntimeError("config unreadable")

        with mock.patch.object(va_config, "config", _Broken()):
            self.assertFalse(va_tools_gate.voice_confirm_allowed())
            status = va_tools_gate.session_owner_proof_status()
            self.assertTrue(status["lock_on"])

    def test_flat_and_aliased_proof_agree(self):
        # The two lab identities are kept in sync by
        # _set_provenance_both (one identity in production).
        _set_provenance_both("auto")
        self.assertFalse(flat_tools_gate.session_owner_proven())
        self.assertFalse(va_tools_gate.session_owner_proven())
        _set_provenance_both("user")
        self.assertTrue(flat_tools_gate.session_owner_proven())
        self.assertTrue(va_tools_gate.session_owner_proven())


# ------------------------------------------------------- the voice answer
@unittest.skipUnless(_QT, "Qt-only: the voice-confirm hook needs PySide6")
class VoiceAnswerTests(unittest.TestCase):
    """The real _maybe_confirm_reply, on a fake window, against
    the real pending registry the GUI reads."""

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(ServicesConfirmMixin,
                             "confirm mixin did not import under Qt")
        self.assertIsNotNone(va_gate, "aliased gate did not import")
        self.assertIsNotNone(va_tools_gate,
                             "aliased tools_gate did not import")
        _set_provenance_both("unknown")
        self._win = _FakeWindow()

    def tearDown(self):
        _set_provenance_both("unknown")
        _clear_handler()
        super().tearDown()

    def _pending(self, timeout_s=2.0):
        """A real pending request on the ALIASED gate, with a
        handler that blocks until released. Returns
        (thread, release, result); the caller drives the hook,
        then releases + joins."""
        release = threading.Event()
        result = {}

        def _blocking(name, summary=None, *a, **k):
            release.wait()
            return False

        va_gate.register_handler(_blocking)

        def _waiter():
            try:
                result["ok"] = va_gate.request_confirmation(
                    "run_command", {"command": "echo b4-marker"},
                    timeout_s=timeout_s)
            except Exception as exc:  # noqa: BLE001 — recorded
                result["error"] = str(exc)

        thread = threading.Thread(target=_waiter, daemon=True)
        thread.start()
        return thread, release, result

    def _wait_pending(self, deadline_s=5.0):
        import time as _time

        end = _time.monotonic() + deadline_s
        while _time.monotonic() < end:
            pending = va_gate.pending_request()
            if pending is not None:
                return pending
            _time.sleep(0.05)
        return None

    def _finish(self, thread, release):
        release.set()
        thread.join(timeout=6.0)

    def _answer(self, text):
        return ServicesConfirmMixin._maybe_confirm_reply(
            self._win, text)

    def test_unproven_yes_is_refused_and_resolves_nothing(self):
        _set_provenance_both("auto")
        thread, release, result = self._pending()
        try:
            pending = self._wait_pending()
            self.assertIsNotNone(pending, "no pending request appeared")
            rid = pending[0]
            self.assertTrue(self._answer("yes"))
            still = va_gate.pending_request()
            self.assertIsNotNone(still, "the yes resolved the request")
            self.assertEqual(still[0], rid)
            self.assertTrue(any("unlock" in c or "on screen" in c
                                for c in self._win.captions))
        finally:
            self._finish(thread, release)

    def test_unproven_no_is_refused_too(self):
        _set_provenance_both("auto")
        thread, release, result = self._pending()
        try:
            pending = self._wait_pending()
            self.assertIsNotNone(pending, "no pending request appeared")
            rid = pending[0]
            self.assertTrue(self._answer("no"))
            still = va_gate.pending_request()
            self.assertIsNotNone(still, "the no resolved the request")
            self.assertEqual(still[0], rid)
        finally:
            self._finish(thread, release)

    def test_proven_owner_yes_resolves(self):
        _set_provenance_both("user")
        thread, release, result = self._pending()
        try:
            self.assertIsNotNone(self._wait_pending(),
                                 "no pending request appeared")
            self.assertTrue(self._answer("yes"))
            thread.join(timeout=5.0)
            self.assertIs(result.get("ok"), True)
        finally:
            self._finish(thread, release)

    def test_screen_unlock_then_yes_resolves(self):
        _set_provenance_both("auto")
        va_tools_gate.mark_session_owner_proven("screen")
        thread, release, result = self._pending()
        try:
            self.assertIsNotNone(self._wait_pending(),
                                 "no pending request appeared")
            self.assertTrue(self._answer("yes"))
            thread.join(timeout=5.0)
            self.assertIs(result.get("ok"), True)
        finally:
            self._finish(thread, release)

    def test_voice_pin_unlock_then_yes_resolves(self):
        _set_provenance_both("auto")
        va_voice_pin.set_voice_pin(va_data_dir, "1234")
        try:
            self.assertTrue(self._answer("voice PIN 1234"))
            self.assertTrue(va_tools_gate.session_owner_proven())
            self.assertNotIn("1234", " ".join(self._win.captions))
            thread, release, result = self._pending()
            try:
                self.assertIsNotNone(self._wait_pending(),
                                     "no pending request appeared")
                self.assertTrue(self._answer("yes"))
                thread.join(timeout=5.0)
                self.assertIs(result.get("ok"), True)
            finally:
                self._finish(thread, release)
        finally:
            va_voice_pin.set_voice_pin(va_data_dir, "")

    def test_wrong_pin_does_not_unlock(self):
        _set_provenance_both("auto")
        va_voice_pin.set_voice_pin(va_data_dir, "1234")
        try:
            self.assertTrue(self._answer("voice PIN 9999"))
            self.assertFalse(va_tools_gate.session_owner_proven())
            thread, release, result = self._pending()
            try:
                pending = self._wait_pending()
                self.assertIsNotNone(pending,
                                     "no pending request appeared")
                self.assertTrue(self._answer("yes"))
                self.assertIsNotNone(va_gate.pending_request())
            finally:
                self._finish(thread, release)
        finally:
            va_voice_pin.set_voice_pin(va_data_dir, "")

    def test_no_pin_set_no_voice_unlock(self):
        _set_provenance_both("auto")
        va_voice_pin.set_voice_pin(va_data_dir, "")
        self.assertTrue(self._answer("voice PIN 1234"))
        self.assertFalse(va_tools_gate.session_owner_proven())

    def test_lock_off_lets_unproven_voice_answer(self):
        _set_provenance_both("auto")
        with mock.patch.object(va_config.config,
                               "session_provenance_lock", False):
            thread, release, result = self._pending()
            try:
                self.assertIsNotNone(self._wait_pending(),
                                     "no pending request appeared")
                self.assertTrue(self._answer("yes"))
                thread.join(timeout=5.0)
                self.assertIs(result.get("ok"), True)
            finally:
                self._finish(thread, release)

    def test_always_allow_utterance_still_refused(self):
        # Regression (UI rethink Build 4 §4.7, D8): a spoken
        # "always allow" is consumed and resolves nothing, in a
        # PROVEN session too.
        _set_provenance_both("user")
        thread, release, result = self._pending()
        try:
            pending = self._wait_pending()
            self.assertIsNotNone(pending, "no pending request appeared")
            rid = pending[0]
            self.assertTrue(self._answer("always allow"))
            still = va_gate.pending_request()
            self.assertIsNotNone(still)
            self.assertEqual(still[0], rid)
        finally:
            self._finish(thread, release)


class _FakeWindow:
    """The smallest object the real _maybe_confirm_reply needs:
    the yes/no sets off the real mixin class, the self-sent echo
    list, and a caption recorder."""

    def __init__(self):
        self._self_sent_texts = []
        self._VOICE_CONFIRM_YES = (
            ServicesConfirmMixin._VOICE_CONFIRM_YES)
        self._VOICE_CONFIRM_NO = (
            ServicesConfirmMixin._VOICE_CONFIRM_NO)
        self.captions = []

    def _add_system(self, text):
        self.captions.append(str(text))


# ------------------------------------------------------- L-28: the grants
class VoiceGrantTests(unittest.TestCase):
    """Permission grants are NEVER by voice; the store-level
    grant (made on screen) is what the gates consult."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="b4_grants_")
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        pga.set_path_override(self._store)
        _set_provenance_both("user")

    def tearDown(self):
        try:
            for pid in pga.PERMISSION_IDS:
                pga.revoke_session_grant(pid)
        except Exception:
            pass
        pga.clear_path_override()
        self._tmp.cleanup()
        _set_provenance_both("unknown")
        _clear_handler()
        super().tearDown()

    def test_voice_grant_refused_without_a_live_session(self):
        got = sv.grant_session_by_voice("camera")
        self.assertIn("can't grant", got)
        self.assertIn("Settings", got)
        self.assertIn("Permissions", got)
        self.assertEqual(sv.active_session_grants(), [])

    def test_voice_grant_refused_inside_a_live_session(self):
        token = live_origin.set_live_origin()
        try:
            got = sv.grant_session_by_voice("camera")
        finally:
            live_origin.reset_live_origin(token)
        self.assertIn("can't grant", got)
        self.assertEqual(sv.active_session_grants(), [])
        self.assertEqual(pga.check_tool("webcam_snapshot"),
                         (False, "camera"))

    def test_voice_grant_unknown_id_still_names_it(self):
        got = sv.grant_session_by_voice("flibberty")
        self.assertIn("no permission called", got)

    def test_router_grant_direction_refused(self):
        routed = sv.route_voice_settings_request("grant camera", "")
        self.assertIn("can't grant", routed["message"])
        self.assertEqual(sv.active_session_grants(), [])

    def test_funnel_grant_refused_in_a_live_session(self):
        token = live_origin.set_live_origin()
        try:
            result = tools.execute("app_settings", {
                "action": "grant", "setting": "camera"})
        finally:
            live_origin.reset_live_origin(token)
        text = str(result.get("result", result))
        self.assertIn("can't grant", text)
        self.assertIn("Permissions", text)
        self.assertEqual(sv.active_session_grants(), [])

    def test_revoke_by_voice_still_works(self):
        pga.grant_session_only("camera")
        token = live_origin.set_live_origin()
        try:
            self.assertEqual(pga.check_tool("webcam_snapshot"),
                             (True, None))
            self.assertIn("off", sv.revoke_session_by_voice("camera"))
            self.assertEqual(pga.check_tool("webcam_snapshot"),
                             (False, "camera"))
        finally:
            live_origin.reset_live_origin(token)

    def test_settings_grant_still_opens_the_gate(self):
        pga.set_permission_state("camera", "granted", via="settings")
        self.assertEqual(pga.check_tool("webcam_snapshot"),
                         (True, None))


# ------------------------------------------------------- the Privacy card
@unittest.skipUnless(_QT, "Qt-only: the Settings card needs PySide6")
class PrivacyCardTests(unittest.TestCase):
    """The Privacy & security card carries the lock, the
    on-screen unlock and the voice-PIN controls (Qt only)."""

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(SettingsGroupsMixin,
                             "settings groups mixin did not import")
        self.assertIsNotNone(va_tools_gate,
                             "aliased tools_gate did not import")
        self.mixin = SettingsGroupsMixin()
        self.mixin._settings_card = (
            lambda _layout, _t, _s: _QVBoxLayout())
        self.mixin._on_session_provenance_lock_toggled = (
            lambda *a: None)
        self.mixin.show_settings_card = lambda *a: None
        self.mixin._build_privacy_cards(_QVBoxLayout())
        _set_provenance_both("unknown")

    def tearDown(self):
        _set_provenance_both("unknown")
        super().tearDown()

    def test_card_carries_the_unlock_and_pin_controls(self):
        self.assertEqual(
            self.mixin.btn_unlock_voice_session.text(),
            "Unlock voice for this session")
        self.assertEqual(self.mixin.btn_set_voice_pin.text(),
                         "Set voice PIN")
        self.assertEqual(self.mixin.btn_clear_voice_pin.text(),
                         "Clear voice PIN")

    def test_lock_checkbox_reflects_the_config(self):
        self.assertIs(
            self.mixin.chk_session_provenance_lock.isChecked(),
            bool(va_config.config.session_provenance_lock))

    def test_refresh_shows_locked_for_an_unproven_session(self):
        _set_provenance_both("auto")
        self.mixin._refresh_voice_proof_status()
        self.assertIn("locked",
                      self.mixin.voice_proof_label.text())

    def test_refresh_shows_unlocked_after_a_screen_unlock(self):
        _set_provenance_both("auto")
        va_tools_gate.mark_session_owner_proven("screen")
        self.mixin._refresh_voice_proof_status()
        self.assertIn("unlocked",
                      self.mixin.voice_proof_label.text())
        self.assertIn("on screen",
                      self.mixin.voice_proof_label.text())

    def test_unlock_button_proves_the_session(self):
        _set_provenance_both("auto")
        self.mixin._unlock_voice_for_session()
        self.assertTrue(va_tools_gate.session_owner_proven())
        self.assertIn("unlocked",
                      self.mixin.voice_proof_label.text())

    def test_refresh_pin_line_set_and_unset(self):
        with mock.patch.object(
                va_voice_pin, "has_voice_pin",
                lambda data_dir: True):
            self.mixin._refresh_voice_proof_status()
            self.assertIn("is set",
                          self.mixin.voice_pin_label.text())
        with mock.patch.object(
                va_voice_pin, "has_voice_pin",
                lambda data_dir: False):
            self.mixin._refresh_voice_proof_status()
            self.assertIn("No voice PIN",
                          self.mixin.voice_pin_label.text())

    def test_clear_pin_button_clears_the_store(self):
        seen = []
        with mock.patch.object(
                va_voice_pin, "set_voice_pin",
                lambda data_dir, pin: seen.append((data_dir, pin))):
            self.mixin._clear_voice_pin()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], "")


if __name__ == "__main__":
    unittest.main()

"""Settings UI redesign (2026-11-16, §4a/§4b/§4c/§5/§8).

Covers, in order:
  * §4b plain-words search (core/settings_search.match_cards,
    TOP_TASKS, card_voice_line) — runs on both interpreters.
  * §4a key-test verdict wording (gui/connect_dialog.
    key_test_verdict) — Qt only (the module imports PySide6).
  * §5 voice protocol (core/settings_voice): propose leaves
    the setting untouched, confirm applies + undo restores,
    deny applies nothing, undo expires, blanket-on refused,
    deny-all works, routing classes A/C/D, one pending
    proposal at a time.
  * §8/R1 session grants: live-origin only, never for the
    microphone, never over a stored denial (D7/D5), and the
    funnel (check_tool) + capture guard consult them.
  * The funnel handler dispatch (live set proposes, chat set
    applies, permission names get the directed refusal).
  * GUI structure: Connect dialog, onboarding key/mic/voice
    pages, Settings card value + voice lines, plain-words
    Settings search on a bare mixin, the Home strip's modes.
  * Static guards: no colour literals in the new dialog, the
    wizard/strip never write the durable grants store (D8).

Qt tests need PySide6 (the .venv-qt interpreter); on an
interpreter without it they skip, following the programme's
standing optional-dependency skips. The grants store is
pointed at a temp dir per test; the record-family store is
guarded by _DataGuard (consumers-batch pattern) so Activity
lines written during a test are restored after it. Theme
changes are undone in tearDown (confirm -> undo restores
the starting theme; the tearDown re-sets it if a test died
mid-flow).
"""

import os
import pathlib
import re
import sys
import tempfile
import time
import types as _types_mod
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Lab has no PortAudio: stub sounddevice (mirrors
# test_mic_first_use_ask.py / test_micfix.py).
try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd


class _FakeStream:
    """Stand-in for sd.InputStream/OutputStream."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        _FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class _FakeDefault:
    device = (0, 0)


class _FakeSD:
    InputStream = _FakeStream
    OutputStream = _FakeStream
    default = _FakeDefault()

    @staticmethod
    def query_devices(kind=None):
        devs = [{"name": "Fake Mic", "max_input_channels": 2},
                {"name": "Fake Speaker", "max_input_channels": 0}]
        if kind == "input":
            return {"name": "Fake Mic"}
        return devs


if "sounddevice" in sys.modules and not hasattr(
        sys.modules["sounddevice"], "InputStream"):
    _sdmod = sys.modules["sounddevice"]
    _sdmod.InputStream = _FakeStream
    _sdmod.OutputStream = _FakeStream
    _sdmod.query_devices = _FakeSD.query_devices
    _sdmod.default = _FakeDefault()

from core import settings_search as search  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication as _QA  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
ConnectDialog = None
key_test_verdict = None
OnboardingDialog = None
SettingsMixin = None
ShellAssistantMixin = None
registry_mod = None
dispatch_mod = None
sv = None
pga = None
live_origin = None
capture_guard = None
confirm_gate_mod = None
app_settings_mod = None
config_mod = None


def setUpModule():
    """Mirror test_c7_dialog_crop's voice_assistant.* alias.

    gui/* imports voice_assistant.core.* (pre-split paths); the
    tree layout is top-level core/ + gui/, so the alias maps
    voice_assistant -> tree root. The GUI imports happen here,
    after the alias — module-level imports would run before
    it. Snapshot discipline: restore prior entries on teardown
    so a co-run suite's cached modules survive.
    """
    global _APP, _VA_SNAPSHOT, _QT
    global ConnectDialog, key_test_verdict, OnboardingDialog
    global SettingsMixin, ShellAssistantMixin
    global registry_mod, dispatch_mod
    global sv, pga, live_origin, capture_guard
    global app_settings_mod, config_mod, confirm_gate_mod
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    try:
        from gui.connect_dialog import (  # noqa: E402
            ConnectDialog as _CD, key_test_verdict as _ktv)
        from gui.onboarding import (  # noqa: E402
            OnboardingDialog as _OD)
        from gui.window_settings import (  # noqa: E402
            SettingsMixin as _SM)
        from gui.window_shell_assistant import (  # noqa: E402
            ShellAssistantMixin as _SA)
        from voice_assistant.core.funnel import (  # noqa: E402
            tools_registry as _reg,
            tools_registry_exec_dispatch as _disp)
        from voice_assistant.core import (  # noqa: E402
            app_settings as _as, capture_guard as _cg,
            confirm_gate as _conf,
            permission_grants as _pg, settings_voice as _sv)
        from voice_assistant.core import config as _cfg  # noqa: E402
        from voice_assistant.core.funnel import (  # noqa: E402
            live_origin as _lo)
        ConnectDialog = _CD
        key_test_verdict = _ktv
        OnboardingDialog = _OD
        SettingsMixin = _SM
        ShellAssistantMixin = _SA
        registry_mod = _reg
        dispatch_mod = _disp
        sv = _sv
        pga = _pg
        live_origin = _lo
        capture_guard = _cg
        confirm_gate_mod = _conf
        app_settings_mod = _as
        config_mod = _cfg
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        _QT = False


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


class _DataGuard:
    """Restore data/ after a test (consumers-batch pattern)."""

    def _scan(self):
        found = {}
        for path in sorted((ROOT / "data").rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(ROOT / "data")] = \
                        path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    ((ROOT / "data") / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = (ROOT / "data") / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class _StoreMixin(unittest.TestCase):
    # LAB NAMESPACE NOTE (mirrors test_onboarding_mic.py): the
    # GUI code reads permission_grants / settings_voice through
    # the `voice_assistant.core` alias, while the flat test
    # world imports `core.*` — two module instances with
    # separate path overrides (the dual-import trap). These
    # tests therefore drive the ALIASED modules — the ones the
    # GUI code actually reads — and assert on them.
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        pga.set_path_override(self._store)
        self._guard = _DataGuard()
        self._guard.__enter__()
        sv.reset_for_tests()
        # The starting theme, and a different valid theme to
        # propose in tests (the starting theme may be Sunset,
        # so the proposal target is picked, not assumed).
        self._theme_before = None
        self._test_theme = None
        try:
            themes = list(app_settings_mod._themes())
            current = str(app_settings_mod.app_settings(
                "get", "theme"))
            for name in themes:
                if name not in current:
                    self._test_theme = name
                    break
            self._theme_before = next(
                (name for name in themes if name in current), None)
        except Exception:  # noqa: BLE001 — tests fail loudly below
            pass
        sv.reset_for_tests()

    def tearDown(self):
        try:
            if self._theme_before:
                current = app_settings_mod.app_settings(
                    "get", "theme")
                if self._theme_before not in str(current):
                    app_settings_mod.app_settings(
                        "set", "theme", self._theme_before)
        except Exception:  # noqa: BLE001 — the suite still tears down
            pass
        # Session grants are in-memory in permission_grants and
        # survive across tests in one process — a grant left by
        # one test would widen the next test's gate. Clear them.
        try:
            for pid in pga.PERMISSION_IDS:
                pga.revoke_session_grant(pid)
        except Exception:  # noqa: BLE001 — the suite still tears down
            pass
        sv.reset_for_tests()
        self._guard.__exit__()
        pga.clear_path_override()
        self._tmp.cleanup()
        super().tearDown()

    def _live(self):
        """A live-origin context (set_live_origin token)."""
        return live_origin.set_live_origin()

    def _deny_stored(self, pid):
        self.assertTrue(pga.set_permission_state(
            pid, "denied", via="settings"))


# ------------------------------------------------------------------ §4b search
class SearchTests(unittest.TestCase):
    """Plain-words search: the box matches what the user means,
    not the setting's name."""

    _TITLES = None

    @classmethod
    def setUpClass(cls):
        cls._TITLES = list(search.CARD_SYNONYMS)

    def test_quieter_maps_to_volume_cards(self):
        got = search.match_cards("make her quieter", self._TITLES)
        self.assertIn("Sounds", got)
        self.assertIn("Whisper hours", got)
        self.assertIn("Briefing voice", got)
        self.assertNotIn("Microphone", got)

    def test_stop_listening_maps_to_microphone(self):
        got = search.match_cards("stop listening", self._TITLES)
        self.assertIn("Microphone", got)

    def test_connect_maps_to_think_card(self):
        got = search.match_cards("connect to the cloud", self._TITLES)
        self.assertIn("How VYUK thinks", got)

    def test_theme_word_maps_to_appearance(self):
        got = search.match_cards("change the theme", self._TITLES)
        self.assertIn("Appearance", got)

    def test_unknown_matches_nothing(self):
        self.assertEqual(
            search.match_cards("flibberty gibbet", self._TITLES), [])

    def test_top_tasks_shape(self):
        self.assertEqual(
            [label for label, _ in search.TOP_TASKS],
            ["Mic", "Voice", "Connect", "Theme", "Language"])
        self.assertEqual(
            [card for _, card in search.TOP_TASKS],
            ["Microphone", "Voice", "How VYUK thinks",
             "Appearance", "Local brain"])

    def test_every_top_task_card_has_a_voice_line(self):
        for _label, card in search.TOP_TASKS:
            line = search.card_voice_line(card)
            self.assertTrue(line)
            self.assertIn(search.card_class(card), ("A", "B", "C", "D"))

    def test_class_a_line_says_voice_cannot_flip(self):
        self.assertEqual(search.card_class("Microphone"), "A")
        self.assertIn("voice can't flip",
                      search.card_voice_line("Microphone"))

    def test_class_b_line_says_you_can_also_say(self):
        self.assertEqual(search.card_class("Voice"), "B")
        self.assertTrue(search.card_voice_line("Voice").startswith(
            "You can also say:"))

    def test_class_c_line_names_the_connect_screen(self):
        self.assertEqual(search.card_class("How VYUK thinks"), "C")
        self.assertIn("key goes in by typing",
                      search.card_voice_line("How VYUK thinks"))


# ------------------------------------------------------------------ §4a verdicts
@unittest.skipUnless(_QT, "Qt unavailable")
class VerdictTests(unittest.TestCase):
    """One honest sentence per key-test verdict kind."""

    def test_ok_passes_the_detail_through(self):
        msg = "Key works ✓ — REST passed, Live passed for 'gemini-2.0-flash'."
        self.assertEqual(key_test_verdict("ok", msg), msg)

    def test_auth_names_the_fix(self):
        got = key_test_verdict("auth", "")
        self.assertIn("rejected by Google", got)
        self.assertIn("paste it again", got)

    def test_quota_names_the_limit(self):
        got = key_test_verdict("quota", "")
        self.assertIn("quota", got.lower())
        self.assertIn("Live usage limit", got)

    def test_network_names_the_pc(self):
        self.assertIn("Couldn't reach Google",
                      key_test_verdict("network", ""))

    def test_model_bidi_names_live_voice(self):
        got = key_test_verdict(
            "model", "REST ok, Live failed: bidiGenerateContent "
            "not supported for this key")
        self.assertIn("live voice", got)

    def test_model_wrong_name_names_the_model(self):
        got = key_test_verdict("model", "REST ok, Live failed: 404")
        self.assertIn("voice model name is wrong", got)

    def test_unknown_with_message_keeps_it(self):
        got = key_test_verdict("unknown", "boom")
        self.assertIn("boom", got)


# ------------------------------------------------------------------ §5 protocol
@unittest.skipUnless(_QT, "Qt unavailable")
class ProtocolTests(_StoreMixin):
    """Propose -> confirm -> announce + undo -> receipt."""

    def _theme_now(self):
        return str(app_settings_mod.app_settings("get", "theme"))

    def _propose_test_theme(self):
        proposal, message = sv.propose_setting_change(
            "theme", self._test_theme)
        self.assertIsNotNone(proposal)
        self.assertIn("Proposal #", message)
        return proposal, message

    def _other_theme(self):
        """A second valid theme, unlike the starting theme and
        unlike the proposal target."""
        for name in app_settings_mod._themes():
            if name != self._theme_before \
                    and name != self._test_theme:
                return name
        self.fail("no second theme to propose")

    def test_propose_leaves_setting_untouched(self):
        before = self._theme_now()
        proposal, message = self._propose_test_theme()
        self.assertEqual(self._theme_now(), before)
        self.assertIn(self._theme_before, message)
        self.assertIn(self._test_theme, message)
        self.assertEqual(sv.pending_proposal(), proposal)

    def test_confirm_applies_and_undo_restores(self):
        proposal, _ = self._propose_test_theme()
        announcement = sv.confirm_proposal(proposal["id"])
        self.assertIn(self._test_theme, self._theme_now())
        self.assertIn("undo", announcement.lower())
        self.assertIsNone(sv.pending_proposal())
        change = sv.undo_available()
        self.assertIsNotNone(change)
        self.assertLessEqual(change["seconds_left"], 60)
        self.assertGreater(change["seconds_left"], 0)
        undone = sv.undo_last_change()
        self.assertIn(self._theme_before, self._theme_now())
        self.assertIn("Undone", undone)
        self.assertIsNone(sv.undo_available())

    def test_confirm_with_stale_id_applies_nothing(self):
        proposal, _ = self._propose_test_theme()
        self.assertIn("no change waiting",
                      sv.confirm_proposal("not-a-real-id"))
        self.assertIn(self._theme_before, self._theme_now())
        self.assertIsNotNone(sv.pending_proposal())

    def test_deny_leaves_unchanged(self):
        proposal, _ = self._propose_test_theme()
        self.assertIn("Left as it is", sv.deny_proposal(proposal["id"]))
        self.assertIn(self._theme_before, self._theme_now())
        self.assertIsNone(sv.pending_proposal())

    def test_undo_expires_after_its_minute(self):
        proposal, _ = self._propose_test_theme()
        sv.confirm_proposal(proposal["id"])
        self.assertIn(self._test_theme, self._theme_now())
        sv._LAST_CHANGE["expires_at"] = time.monotonic() - 1.0
        self.assertIsNone(sv.undo_available())
        self.assertIn("Nothing to undo", sv.undo_last_change())
        self.assertIn(self._test_theme, self._theme_now())

    def test_second_proposal_replaces_the_first(self):
        first, _ = self._propose_test_theme()
        second, _ = sv.propose_setting_change(
            "theme", self._other_theme())
        self.assertIsNotNone(second)
        self.assertIn("no change waiting",
                      sv.confirm_proposal(first["id"]))
        self.assertIn(self._theme_before, self._theme_now())
        sv.confirm_proposal(second["id"])
        self.assertIn(self._other_theme(), self._theme_now())

    def test_blanket_on_is_refused_always(self):
        before = self._theme_now()
        routed = sv.route_voice_settings_request("everything", "on")
        self.assertEqual(routed["kind"], "blanket")
        self.assertIn("can't turn everything on", routed["message"])
        self.assertEqual(self._theme_now(), before)
        self.assertIsNone(sv.pending_proposal())

    def test_deny_all_revokes_session_grants(self):
        token = self._live()
        try:
            self.assertIn("session", sv.grant_session_by_voice("camera"))
            self.assertEqual(sv.active_session_grants(), ["camera"])
            routed = sv.route_voice_settings_request(
                "everything", "off")
            self.assertEqual(routed["kind"], "deny_all")
            self.assertEqual(sv.active_session_grants(), [])
        finally:
            live_origin.reset_live_origin(token)

    def test_class_c_queues_the_connect_screen(self):
        routed = sv.route_voice_settings_request("api key", "")
        self.assertEqual(routed["kind"], "class_c")
        self.assertEqual(routed["action"], "open_connect")
        self.assertEqual(sv.pending_gui_action(),
                         {"action": "open_connect"})
        self.assertIn("can't take a key by voice", routed["message"])

    def test_class_d_is_settings_only(self):
        routed = sv.route_voice_settings_request("factory reset", "")
        self.assertEqual(routed["kind"], "class_d")
        self.assertIn("Settings only", routed["message"])

    def test_class_a_without_direction_queues_settings(self):
        routed = sv.route_voice_settings_request("camera", "")
        self.assertEqual(routed["kind"], "class_a")
        self.assertEqual(routed["action"], "open_settings")
        self.assertEqual(routed["card"], "Permissions")
        self.assertEqual(routed["permission"], "camera")
        self.assertEqual(
            sv.pending_gui_action()["action"], "open_settings")

    def test_unknown_setting_is_not_a_proposal(self):
        routed = sv.route_voice_settings_request("flibberty", "x")
        self.assertEqual(routed["kind"], "class_b")
        self.assertNotIn("proposal", routed)
        self.assertIsNone(sv.pending_proposal())


# ------------------------------------------------------------------ §8/R1 grants
@unittest.skipUnless(_QT, "Qt unavailable")
class GrantGateTests(_StoreMixin):
    """Session grants: live-origin only, never the mic, never
    over a stored denial — and the gates consult them."""

    def test_camera_grant_opens_check_tool_only_when_live(self):
        token = self._live()
        try:
            self.assertIn("session",
                          sv.grant_session_by_voice("camera"))
            self.assertEqual(pga.check_tool("webcam_snapshot"),
                             (True, None))
        finally:
            live_origin.reset_live_origin(token)
        # The grant dies with the live origin: the same call
        # outside a live session refuses again.
        self.assertEqual(pga.check_tool("webcam_snapshot"),
                         (False, "camera"))

    def test_grant_without_live_origin_refused(self):
        self.assertIn("live session",
                      sv.grant_session_by_voice("camera"))
        self.assertEqual(sv.active_session_grants(), [])
        self.assertEqual(pga.check_tool("webcam_snapshot"),
                         (False, "camera"))

    def test_mic_session_grant_never_opens_the_funnel(self):
        token = self._live()
        try:
            pga.grant_session_only("microphone")
            self.assertTrue(pga.has_session_grant("microphone"))
            self.assertEqual(pga.check_tool("mic_check"),
                             (False, "microphone"))
            self.assertEqual(pga.check_tool("record_audio"),
                             (False, "microphone"))
        finally:
            live_origin.reset_live_origin(token)

    def test_stored_denial_blocks_the_grant(self):
        self._deny_stored("camera")
        token = self._live()
        try:
            got = sv.grant_session_by_voice("camera")
            self.assertIn("switched off in Settings", got)
            self.assertEqual(sv.active_session_grants(), [])
            self.assertEqual(pga.check_tool("webcam_snapshot"),
                             (False, "camera"))
        finally:
            live_origin.reset_live_origin(token)

    def test_revoke_session_grant_closes_the_gate(self):
        token = self._live()
        try:
            sv.grant_session_by_voice("camera")
            self.assertEqual(pga.check_tool("webcam_snapshot"),
                             (True, None))
            self.assertIn("off", sv.revoke_session_by_voice("camera"))
            self.assertEqual(pga.check_tool("webcam_snapshot"),
                             (False, "camera"))
        finally:
            live_origin.reset_live_origin(token)

    def test_capture_guard_consults_the_session_grant(self):
        # The guard's ask flow is patched to approve: the test
        # proves the PRE-CHECK consults the grant, not the ask.
        token = self._live()
        try:
            sv.grant_session_by_voice("camera")
            with mock.patch.object(
                    confirm_gate_mod, "request_confirmation",
                    return_value=True):
                allowed, _msg = capture_guard.ensure_capture_allowed(
                    "camera")
                self.assertTrue(allowed)
        finally:
            live_origin.reset_live_origin(token)
        with mock.patch.object(
                confirm_gate_mod, "request_confirmation",
                return_value=True):
            allowed, _msg = capture_guard.ensure_capture_allowed(
                "camera")
            self.assertFalse(allowed)

    def test_capture_guard_still_refuses_a_stored_denial(self):
        self._deny_stored("camera")
        token = self._live()
        try:
            sv.grant_session_by_voice("camera")
            with mock.patch.object(
                    confirm_gate_mod, "request_confirmation",
                    return_value=True):
                allowed, _msg = capture_guard.ensure_capture_allowed(
                    "camera")
                self.assertFalse(allowed)
        finally:
            live_origin.reset_live_origin(token)


# ------------------------------------------------------------------ funnel dispatch
@unittest.skipUnless(_QT, "Qt unavailable")
class FunnelDispatchTests(_StoreMixin):
    """The app_settings tool, through the real registry: a live
    set proposes, a chat set applies, a permission name gets
    the directed refusal. (GAPS-23 F-V7: handlers refuse a
    direct call, so the tests go through tools.execute —
    the test_tools_loopholes pattern. Fix 4 budgets are
    patched out: the routing, not the budget, is under test.)"""

    def setUp(self):
        super().setUp()
        self._bp = mock.patch.object(
            registry_mod, "_budget_check", lambda name, is_read: None)
        self._bp2 = mock.patch.object(
            dispatch_mod, "_budget_check", lambda name, is_read: None)
        self._bp.start()
        self._bp2.start()

    def tearDown(self):
        self._bp2.stop()
        self._bp.stop()
        super().tearDown()

    def _execute(self, args):
        return registry_mod.tools.execute(
            "app_settings", args)["result"]

    def test_live_set_proposes_instead_of_applying(self):
        before = str(app_settings_mod.app_settings("get", "theme"))
        token = self._live()
        try:
            got = self._execute({"action": "set", "setting": "theme",
                                 "value": self._test_theme})
        finally:
            live_origin.reset_live_origin(token)
        self.assertIn("Proposal #", str(got))
        self.assertEqual(
            str(app_settings_mod.app_settings("get", "theme")), before)

    def test_chat_set_still_applies_directly(self):
        got = self._execute({"action": "set", "setting": "theme",
                             "value": self._test_theme})
        self.assertIn(self._test_theme, str(got))
        self.assertIn(self._test_theme,
                      str(app_settings_mod.app_settings("get", "theme")))

    def test_chat_set_permission_name_gets_the_refusal(self):
        got = self._execute({"action": "set", "setting": "camera",
                             "value": "on"})
        self.assertIn("Settings", str(got))
        self.assertIn("Permissions", str(got))
        self.assertIn("can't flip", str(got))

    def test_confirm_action_dispatches(self):
        proposal, _ = sv.propose_setting_change(
            "theme", self._test_theme)
        got = self._execute({"action": "confirm",
                             "value": proposal["id"]})
        self.assertIn(self._test_theme, str(got))
        self.assertIn(self._test_theme,
                      str(app_settings_mod.app_settings("get", "theme")))

    def test_grant_action_needs_a_live_session(self):
        got = self._execute({"action": "grant", "setting": "camera"})
        self.assertIn("live session", str(got))
        self.assertEqual(sv.active_session_grants(), [])


# ------------------------------------------------------------------ §4a dialog
@unittest.skipUnless(_QT, "Qt unavailable")
class ConnectDialogTests(unittest.TestCase):
    """The Connect screen: test before trust, save only what
    was pasted, repair in one tap."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def _dialog(self):
        dlg = ConnectDialog()
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_builds_with_test_disabled_for_an_empty_key(self):
        dlg = self._dialog()
        self.assertFalse(dlg.btn_test_key.isEnabled())
        self.assertEqual(dlg.combo_provider.count(), 2)
        self.assertEqual(dlg.btn_get_key.text(), "Get a free key")
        self.assertTrue(dlg.input_key.echoMode()
                        == dlg.input_key.EchoMode.Password)

    def test_typing_a_key_enables_test(self):
        dlg = self._dialog()
        dlg.input_key.setText("AIzaFakeKeyForTest")
        self.assertTrue(dlg.btn_test_key.isEnabled())

    def test_save_with_no_key_says_so(self):
        dlg = self._dialog()
        dlg.btn_save_key.click()
        self.assertIn("Paste a key first", dlg.key_status.text())

    def test_verdict_slot_writes_plain_words(self):
        dlg = self._dialog()
        dlg._on_key_test_completed("auth", "")
        self.assertIn("rejected by Google", dlg.key_status.text())
        dlg._on_key_test_completed("network", "")
        self.assertIn("Couldn't reach Google", dlg.key_status.text())

    def test_open_settings_button_emits(self):
        dlg = self._dialog()
        seen = []
        dlg.open_settings_requested.connect(lambda: seen.append(True))
        dlg.btn_open_settings.click()
        self.assertEqual(seen, [True])

    def test_groq_provider_changes_the_link_label(self):
        dlg = self._dialog()
        dlg.combo_provider.setCurrentIndex(1)
        self.assertEqual(dlg.combo_provider.currentData(), "groq")
        self.assertIn("Groq", dlg.btn_get_key.text())


# ------------------------------------------------------------------ §4a wizard
@unittest.skipUnless(_QT, "Qt unavailable")
class OnboardingPageTests(_StoreMixin):
    """The wizard's three screens carry their new controls."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def _dialog(self):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_key_page_has_test_button_link_and_result(self):
        from PySide6.QtWidgets import QLabel

        dlg = self._dialog()
        self.assertIsNotNone(dlg.ob_btn_test_key)
        self.assertEqual(dlg.ob_btn_test_key.text(), "Test key")
        self.assertIsNotNone(dlg.ob_key_test_label)
        links = [w for w in dlg._key_page.findChildren(QLabel)
                 if w.accessibleName()
                 == "Get a free key from Google AI Studio"]
        self.assertEqual(len(links), 1)
        self.assertIn("aistudio.google.com", links[0].text())

    def test_key_test_with_empty_key_says_so(self):
        dlg = self._dialog()
        dlg.ob_api_key.setText("")
        dlg.ob_btn_test_key.click()
        self.assertIn("Paste a key first",
                      dlg.ob_key_test_label.text())

    def test_mic_page_has_hearback_button(self):
        dlg = self._dialog()
        self.assertIsNotNone(dlg._hearback_btn)
        self.assertEqual(dlg._hearback_btn.text(), "Hear yourself back")
        dlg._hearback_btn.click()
        self.assertIn("Nothing recorded yet",
                      dlg._hearback_status.text())

    def test_voice_page_has_sample_button(self):
        dlg = self._dialog()
        self.assertIsNotNone(dlg._voice_sample_btn)
        self.assertEqual(dlg._voice_sample_btn.text(), "Hear a sample")
        dlg._voice_sample_btn.click()
        text = dlg._voice_sample_status.text()
        self.assertTrue(text)
        self.assertTrue("PC's voice" in text
                        or "Couldn't play" in text)


# ------------------------------------------------------------------ §4b cards
@unittest.skipUnless(_QT, "Qt unavailable")
class SettingsCardTests(unittest.TestCase):
    """Every card gets the same shape: value in words + the
    voice line. Built on a bare mixin — no MainWindow."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def _bare(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget

        obj = object.__new__(SettingsMixin)
        obj._settings_card_value_labels = {}
        obj._settings_card_voice_labels = {}
        return obj

    def test_card_registers_value_and_voice_labels(self):
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

        obj = self._bare()
        host = QWidget()
        layout = QVBoxLayout(host)
        returned = SettingsMixin._settings_card(
            obj, layout, "Voice", "Pick the voice she speaks with.")
        labels = returned.parentWidget().findChildren(QLabel)
        self.assertIn("Voice",
                      [w.text() for w in labels])
        self.assertIn("Voice", obj._settings_card_value_labels)
        self.assertIn("Voice", obj._settings_card_voice_labels)

    def test_refresh_fills_value_and_voice_lines(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget

        obj = self._bare()
        host = QWidget()
        layout = QVBoxLayout(host)
        SettingsMixin._settings_card(obj, layout, "Voice", "hint")
        SettingsMixin._settings_card(obj, layout, "About", "hint")
        SettingsMixin._refresh_settings_card_values(obj)
        voice_value = obj._settings_card_value_labels["Voice"]
        # isHidden, not isVisible: the host widget was never
        # shown, so isVisible is False for every child whatever
        # the label's own state is.
        self.assertFalse(voice_value.isHidden())
        self.assertTrue(voice_value.text().startswith("Currently: "))
        voice_line = obj._settings_card_voice_labels["Voice"]
        self.assertFalse(voice_line.isHidden())
        self.assertTrue(voice_line.text().startswith(
            "You can also say:"))
        about_value = obj._settings_card_value_labels["About"]
        self.assertIn("6.3.0", about_value.text())

    def test_value_reader_sounds_off(self):
        from PySide6.QtWidgets import QCheckBox

        obj = self._bare()
        chk = QCheckBox()
        chk.setChecked(False)
        obj.chk_sounds = chk
        self.assertEqual(
            SettingsMixin._settings_card_value(obj, "Sounds"), "Off")

    def test_value_reader_unknown_card_has_no_value(self):
        obj = self._bare()
        self.assertIsNone(
            SettingsMixin._settings_card_value(obj, "Doctor"))

    def test_plain_words_search_on_the_bare_mixin(self):
        from PySide6.QtWidgets import (
            QLabel, QScrollArea, QVBoxLayout, QWidget)

        obj = self._bare()
        obj.more_container = QWidget()
        obj._more_expanded = False
        obj._settings_group_headers = {}
        scroll = QScrollArea()
        scroll.setWidget(QWidget())
        obj.settings_scroll = scroll
        cards = {}
        for title, hay in (
                ("Voice", "voice pick the voice she speaks with"),
                ("Sounds", "sounds volume interface sounds"),
                ("Whisper hours",
                 "whisper hours she speaks softer at night"),
                ("Briefing voice",
                 "briefing voice morning briefing pace"),
                ("Microphone", "microphone which microphone")):
            card = QWidget()
            lay = QVBoxLayout(card)
            lbl = QLabel(title)
            lbl.setObjectName("sectionTitle")
            lay.addWidget(lbl)
            cards[card] = hay
        obj._settings_card_texts = cards
        SettingsMixin._on_settings_search(obj, "make her quieter")
        visible = {c.findChild(QLabel, "sectionTitle").text()
                   for c in cards if not c.isHidden()}
        # The registry maps "quieter" to the volume cards, not
        # to Voice or Microphone (see SearchTests).
        self.assertEqual(
            visible, {"Sounds", "Whisper hours", "Briefing voice"})
        SettingsMixin._on_settings_search(obj, "")
        self.assertEqual(
            {c.findChild(QLabel, "sectionTitle").text()
             for c in cards if not c.isHidden()},
            {"Voice", "Sounds", "Whisper hours", "Briefing voice",
             "Microphone"})


# ------------------------------------------------------------------ §4c strip
@unittest.skipUnless(_QT, "Qt unavailable")
class StripTests(_StoreMixin):
    """The Home strip shows one mode at a time, in priority
    order. Built on a namespace — no MainWindow."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def _window(self):
        from PySide6.QtWidgets import (
            QFrame, QHBoxLayout, QLabel, QPushButton)
        import types as _t

        ns = _t.SimpleNamespace()
        ns.settings_voice_strip = QFrame()
        lay = QHBoxLayout(ns.settings_voice_strip)
        ns.settings_voice_strip_label = QLabel("")
        lay.addWidget(ns.settings_voice_strip_label)
        for name in ("yes", "no", "undo", "keep", "off", "fix"):
            btn = QPushButton(name)
            setattr(ns, f"btn_settings_voice_{name}", btn)
            lay.addWidget(btn)
        ns._brain_fallback_active = False
        ns._connect_calls = []
        ns._card_calls = []
        ns._open_connect_dialog = lambda: ns._connect_calls.append(True)
        ns.show_settings_card = lambda t: ns._card_calls.append(t)
        ns._speak_strip_announcement = lambda text: None
        # The refresh calls the mixin's button helper and its
        # class-level grant-words map on self — bind both to
        # this namespace the same way.
        ns._show_strip_buttons = (
            lambda names: ShellAssistantMixin._show_strip_buttons(
                ns, names))
        ns._SESSION_GRANT_WORDS = (
            ShellAssistantMixin._SESSION_GRANT_WORDS)
        ns._refresh_settings_voice_strip = (
            lambda: ShellAssistantMixin._refresh_settings_voice_strip(
                ns))
        return ns

    def _refresh(self, ns):
        ShellAssistantMixin._refresh_settings_voice_strip(ns)

    def _visible_buttons(self, ns):
        return {name for name in
                ("yes", "no", "undo", "keep", "off", "fix")
                if getattr(ns, f"btn_settings_voice_{name}").isVisible()}

    def test_nothing_to_show_hides_the_strip(self):
        ns = self._window()
        self._refresh(ns)
        self.assertFalse(ns.settings_voice_strip.isVisible())

    def test_pending_proposal_shows_yes_no(self):
        ns = self._window()
        proposal, _ = sv.propose_setting_change("theme", self._test_theme)
        self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        self.assertEqual(self._visible_buttons(ns), {"yes", "no"})
        self.assertIn("Proposal #",
                      ns.settings_voice_strip_label.text())

    def test_confirmed_change_shows_undo_with_seconds(self):
        ns = self._window()
        proposal, _ = sv.propose_setting_change("theme", self._test_theme)
        sv.confirm_proposal(proposal["id"])
        self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        self.assertEqual(self._visible_buttons(ns), {"undo"})
        self.assertRegex(ns.btn_settings_voice_undo.text(),
                         r"Undo \(\d+s\)")

    def test_live_grant_shows_keep_and_off(self):
        ns = self._window()
        token = self._live()
        try:
            sv.grant_session_by_voice("camera")
            self._refresh(ns)
        finally:
            live_origin.reset_live_origin(token)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        self.assertEqual(self._visible_buttons(ns), {"keep", "off"})
        self.assertIn("Camera", ns.settings_voice_strip_label.text())
        self.assertIn("session only",
                      ns.settings_voice_strip_label.text())

    def test_fallback_shows_fix_the_connection(self):
        ns = self._window()
        ns._brain_fallback_active = True
        self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        self.assertEqual(self._visible_buttons(ns), {"fix"})
        self.assertIn("cloud brain",
                      ns.settings_voice_strip_label.text())

    def test_gui_action_open_connect_consumed_once(self):
        ns = self._window()
        sv.request_gui_action("open_connect")
        self._refresh(ns)
        self.assertEqual(ns._connect_calls, [True])
        self.assertIsNone(sv.pending_gui_action())
        self._refresh(ns)
        self.assertEqual(ns._connect_calls, [True])

    def test_gui_action_open_settings_consumed_once(self):
        ns = self._window()
        sv.request_gui_action("open_settings", card="Permissions")
        self._refresh(ns)
        self.assertEqual(ns._card_calls, ["Permissions"])
        self.assertIsNone(sv.pending_gui_action())

    def test_yes_button_confirms_and_speaks_nothing_crashing(self):
        ns = self._window()
        proposal, _ = sv.propose_setting_change("theme", self._test_theme)
        self._refresh(ns)
        ShellAssistantMixin._on_settings_voice_yes(ns)
        self.assertIn(self._test_theme, str(
            app_settings_mod.app_settings("get", "theme")))
        self._refresh(ns)
        self.assertEqual(self._visible_buttons(ns), {"undo"})


# ------------------------------------------------------------------ static guards
class StaticGuardTests(unittest.TestCase):
    """The new GUI code carries no colour literals, and the
    wizard/strip never write the durable grants store (D8)."""

    def test_no_colour_literals_in_the_connect_dialog(self):
        text = (ROOT / "gui" / "connect_dialog.py").read_text()
        self.assertNotRegex(text, r"#[0-9A-Fa-f]{6}\b")
        self.assertNotIn("rgb(", text)

    def test_settings_voice_never_writes_the_durable_store(self):
        text = (ROOT / "core" / "settings_voice.py").read_text()
        # The docstring names set_permission_state as the thing
        # never called — the check is for an actual CALL
        # (".set_permission_state("), not the word.
        self.assertNotIn(".set_permission_state(", text)
        self.assertNotIn("set_state(", text)

    def test_wizard_and_strip_never_flip_a_standing_switch(self):
        for name in ("onboarding_pages.py", "onboarding.py",
                     "connect_dialog.py", "window_shell_assistant.py",
                     "window_settings.py"):
            text = (ROOT / "gui" / name).read_text()
            self.assertNotIn("set_permission_state", text,
                             msg=name)
            self.assertNotIn("grant_session_only", text, msg=name)


if __name__ == "__main__":
    unittest.main()

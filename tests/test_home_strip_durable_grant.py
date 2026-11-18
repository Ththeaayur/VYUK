"""Home strip vs durable mic grant (round-2 N4 FAIL 4, fix #30).

Bug: apply_mic_first_use_choice("always") writes the durable
grant AND holds a session grant (so the live mic can open), and
the Home settings-voice strip showed "Microphone is on for this
session only — the switch in Settings hasn't moved." whenever a
session grant was held — without ever checking the durable
state. The switch HAS moved; the label was false.

Fix: core/settings_voice.session_only_grants() — active session
grants whose durable state is NOT granted — and the strip reads
that for its session-only row. active_session_grants() itself is
unchanged (the voice deny-all path still clears every held
grant), and no grant is ever removed by the fix: after
"always" the session grant is still held, only the false label
is gone.

Qt tests need PySide6 (the .venv-qt interpreter); on an
interpreter without it they skip, following the programme's
standing optional-dependency skips. The pure helper tests run
on both interpreters (flat core.* imports, no Qt).
"""

import os
import pathlib
import sys
import tempfile
import types as _types_mod
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Lab has no PortAudio: stub sounddevice (mirrors
# test_settings_redesign.py / test_mic_first_use_ask.py).
try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

from core import permission_grants as flat_pg  # noqa: E402
from core import settings_voice as flat_sv  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication as _QA  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
ShellAssistantMixin = None
sv = None
pga = None
apply_mic_first_use_choice = None
apply_permission_changes = None


def setUpModule():
    """Mirror test_settings_redesign.setUpModule's
    voice_assistant.* alias. The GUI code reads
    permission_grants / settings_voice through the alias,
    while the flat test world imports core.* — two module
    instances (the dual-import trap). The Qt tests therefore
    drive the ALIASED modules — the ones the GUI code
    actually reads. Snapshot discipline: restore prior
    entries on teardown so a co-run suite's cached modules
    survive.
    """
    global _APP, _VA_SNAPSHOT, _QT
    global ShellAssistantMixin, sv, pga
    global apply_mic_first_use_choice, apply_permission_changes
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
        from gui.window_shell_assistant import (  # noqa: E402
            ShellAssistantMixin as _SA)
        from gui.pf_permissions import (  # noqa: E402
            apply_mic_first_use_choice as _choice,
            apply_permission_changes as _apply)
        from voice_assistant.core import (  # noqa: E402
            permission_grants as _pg, settings_voice as _sv)
        ShellAssistantMixin = _SA
        sv = _sv
        pga = _pg
        apply_mic_first_use_choice = _choice
        apply_permission_changes = _apply
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
    """Restore data/ after a test (consumers-batch pattern,
    mirrors test_settings_redesign.py)."""

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


class SessionOnlyGrantsTests(unittest.TestCase):
    """The helper, pure: no Qt, both interpreters. Flat
    core.* world throughout (one module identity)."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        flat_pg.set_path_override(self._store)
        self._guard = _DataGuard()
        self._guard.__enter__()
        flat_sv.reset_for_tests()

    def tearDown(self):
        try:
            for pid in flat_pg.PERMISSION_IDS:
                flat_pg.revoke_session_grant(pid)
        except Exception:  # noqa: BLE001 — teardown never fails
            pass
        flat_sv.reset_for_tests()
        self._guard.__exit__()
        flat_pg.clear_path_override()
        self._tmp.cleanup()
        super().tearDown()

    def test_session_grant_without_durable_is_session_only(self):
        flat_pg.grant_session_only("microphone")
        self.assertEqual(flat_sv.active_session_grants(),
                         ["microphone"])
        self.assertEqual(flat_sv.session_only_grants(),
                         ["microphone"])

    def test_session_grant_with_durable_is_not_session_only(self):
        self.assertTrue(flat_pg.set_permission_state(
            "microphone", "granted", via="settings"))
        flat_pg.grant_session_only("microphone")
        # The grant is still held — the fix never removes it —
        # only the session-only claim about it is gone.
        self.assertEqual(flat_sv.active_session_grants(),
                         ["microphone"])
        self.assertEqual(flat_sv.session_only_grants(), [])

    def test_denied_durable_session_grant_still_session_only(self):
        # Only "granted" suppresses: a stored denial leaves the
        # label true (the switch hasn't moved, it's off).
        self.assertTrue(flat_pg.set_permission_state(
            "microphone", "denied", via="settings"))
        flat_pg.grant_session_only("microphone")
        self.assertEqual(flat_sv.session_only_grants(),
                         ["microphone"])

    def test_no_session_grant_is_empty(self):
        self.assertTrue(flat_pg.set_permission_state(
            "microphone", "granted", via="settings"))
        self.assertEqual(flat_sv.session_only_grants(), [])
        self.assertEqual(flat_sv.active_session_grants(), [])


@unittest.skipUnless(_QT, "Qt unavailable")
class HomeStripDurableGrantTests(unittest.TestCase):
    """The strip itself, on a namespace — no MainWindow
    (mirrors test_settings_redesign.StripTests)."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        pga.set_path_override(self._store)
        self._guard = _DataGuard()
        self._guard.__enter__()
        sv.reset_for_tests()

    def tearDown(self):
        try:
            for pid in pga.PERMISSION_IDS:
                pga.revoke_session_grant(pid)
        except Exception:  # noqa: BLE001 — teardown never fails
            pass
        sv.reset_for_tests()
        self._guard.__exit__()
        pga.clear_path_override()
        self._tmp.cleanup()
        super().tearDown()

    def _window(self):
        from PySide6.QtWidgets import (
            QFrame, QHBoxLayout, QLabel, QPushButton)
        import types as _t

        ns = _t.SimpleNamespace()
        ns.settings_voice_strip = QFrame()
        lay = QHBoxLayout(ns.settings_voice_strip)
        ns.settings_voice_strip_label = QLabel("")
        lay.addWidget(ns.settings_voice_strip_label)
        for name in ("yes", "no", "undo", "keep", "off", "fix",
                     "browser"):
            btn = QPushButton(name)
            setattr(ns, f"btn_settings_voice_{name}", btn)
            lay.addWidget(btn)
        ns._brain_fallback_active = False
        ns._open_connect_dialog = lambda: None
        ns.show_settings_card = lambda t: None
        ns._speak_strip_announcement = lambda text: None
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

    def test_always_choice_hides_the_session_only_row(self):
        # The exact round-2 probe: choice "always".
        ns = self._window()
        self.assertEqual(apply_mic_first_use_choice("always"),
                         "always")
        self.assertEqual(pga.get_state("microphone"), "granted")
        self.assertEqual(sv.active_session_grants(), ["microphone"])
        self._refresh(ns)
        self.assertFalse(ns.settings_voice_strip.isVisible())
        self.assertNotIn("session only",
                         ns.settings_voice_strip_label.text())

    def test_session_only_mic_still_shows_keep_and_off(self):
        ns = self._window()
        pga.grant_session_only("microphone")
        self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        self.assertIn("Microphone",
                      ns.settings_voice_strip_label.text())
        self.assertIn("session only",
                      ns.settings_voice_strip_label.text())
        self.assertTrue(ns.btn_settings_voice_keep.isVisible())
        self.assertTrue(ns.btn_settings_voice_off.isVisible())

    def test_durable_only_mic_hides_strip(self):
        ns = self._window()
        apply_permission_changes([], {"microphone": "granted"})
        self.assertEqual(pga.get_state("microphone"), "granted")
        self.assertEqual(sv.active_session_grants(), [])
        self._refresh(ns)
        self.assertFalse(ns.settings_voice_strip.isVisible())

    def test_denied_mic_hides_strip(self):
        ns = self._window()
        apply_permission_changes([], {"microphone": "denied"})
        self.assertEqual(pga.get_state("microphone"), "denied")
        self._refresh(ns)
        self.assertFalse(ns.settings_voice_strip.isVisible())

    def test_turn_off_clears_session_grant_and_hides(self):
        # The buttons go with the label — and "Turn off" still
        # works for a genuine session-only grant.
        ns = self._window()
        pga.grant_session_only("microphone")
        self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        ShellAssistantMixin._on_settings_voice_off(ns)
        self.assertEqual(sv.active_session_grants(), [])
        self.assertFalse(ns.settings_voice_strip.isVisible())

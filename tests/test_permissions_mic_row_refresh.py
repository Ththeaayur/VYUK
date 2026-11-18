"""Round-2 N4 FAIL 3: the permissions-page mic row is stale within
the same walk.

The wizard builds all pages once at construction and the mic row
(`_mic_state_block()`) read the live state only at build time, so
a grant made on the Microphone page still read as off when the
Permissions page was shown later. The fix refreshes the row in
`_sync_nav` whenever the permissions page becomes current — the
same seam that already refreshes the self-test, setup, done, key
and mic pages on show.

Qt tests need PySide6 (the .venv-qt interpreter); on an
interpreter without it they skip, following the programme's
standing optional-dependency skips. The grants store is pointed
at a temp dir per test; the record-family store is guarded by
_DataGuard (consumers-batch pattern) so Activity lines written
during a test are restored after it.
"""

import os
import pathlib
import sys
import tempfile
import types as _types_mod
import unittest
from unittest import mock  # noqa: F401  (kept for parity with the suite)

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

try:
    from PySide6.QtWidgets import QApplication as _QA
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
OnboardingDialog = None
QApplication = None


def setUpModule():
    """Mirror test_onboarding_mic's voice_assistant.* alias.

    gui/onboarding*.py imports voice_assistant.core.* (pre-split
    paths); the tree layout is top-level core/ + gui/, so the
    alias maps voice_assistant -> tree root. The GUI imports
    happen here, after the alias — module-level imports would
    run before it. Snapshot discipline: restore prior entries
    on teardown so a co-run suite's cached modules survive.
    """
    global _APP, _VA_SNAPSHOT, _QT
    global OnboardingDialog, QApplication
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
        from gui.onboarding import (  # noqa: E402
            OnboardingDialog as _OD)
        OnboardingDialog = _OD
        QApplication = _QA
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
    # GUI code reads permission_grants through the
    # `voice_assistant.core` alias, while the flat test world
    # imports `core.permission_grants` — two module instances
    # with separate path overrides (the dual-import trap). These
    # tests therefore drive the ALIASED module — the one the GUI
    # code actually reads — and assert on it.
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        from voice_assistant.core import (  # noqa: E402
            permission_grants as _pga)
        self.pga = _pga
        self.pga.set_path_override(self._store)
        self._guard = _DataGuard()
        self._guard.__enter__()
        self._saved_hooks = list(self.pga._mic_teardown_hooks)

    def tearDown(self):
        self._guard.__exit__()
        self.pga.clear_path_override()
        self.pga._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()
        super().tearDown()

    def _grant(self, state):
        self.assertTrue(self.pga.set_permission_state(
            "microphone", state, via="settings"))


@unittest.skipUnless(_QT, "Qt unavailable")
class MicRowRefreshTests(_StoreMixin):
    """The permissions-page mic row follows the live state on
    every show — forward, back, and repeated."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        return dlg

    def _show_permissions(self, dlg):
        # The wizard's own navigation: switch + _sync_nav,
        # the same pair _next/_back use (mirrors
        # test_ask_pages_never_gate_continue).
        dlg.pages.setCurrentWidget(dlg._permissions_page)
        dlg._sync_nav()

    def _mic_labels(self, dlg):
        return (dlg._ob_mic_vyuk.text(),
                dlg._ob_mic_os.text(),
                dlg._ob_mic_diag.text())

    def test_granted_before_show_reads_on(self):
        self._grant("granted")
        dlg = self._dialog()
        self._show_permissions(dlg)
        vyuk, _os_line, _diag = self._mic_labels(dlg)
        self.assertEqual(vyuk, "On — VYUK can use this right now.")

    def test_denied_before_show_reads_off(self):
        self._grant("denied")
        dlg = self._dialog()
        self._show_permissions(dlg)
        vyuk, _os_line, diag = self._mic_labels(dlg)
        self.assertEqual(vyuk, "Off — VYUK cannot use this.")
        self.assertIn("you switched VYUK's microphone off", diag)

    def test_grant_between_shows_followed_on_second_show(self):
        # The round-2 sequence, through the real button: fresh
        # install, permissions page says off; mic page, "Always
        # allow"; permissions page again — the row must say on.
        dlg = self._dialog()
        self._show_permissions(dlg)
        self.assertEqual(self._mic_labels(dlg)[0],
                         "Off — VYUK cannot use this.")
        dlg.pages.setCurrentWidget(dlg._mic_page)
        dlg._sync_nav()
        dlg._mic_ask_buttons["always"].click()
        self.assertEqual(self.pga.get_state("microphone"),
                         "granted")
        self._show_permissions(dlg)
        vyuk, _os_line, diag = self._mic_labels(dlg)
        self.assertEqual(vyuk, "On — VYUK can use this right now.")
        self.assertNotIn("you switched VYUK's microphone off", diag)

    def test_deny_between_shows_followed_on_second_show(self):
        self._grant("granted")
        dlg = self._dialog()
        self._show_permissions(dlg)
        self.assertEqual(self._mic_labels(dlg)[0],
                         "On — VYUK can use this right now.")
        self._grant("denied")
        self._show_permissions(dlg)
        vyuk, _os_line, diag = self._mic_labels(dlg)
        self.assertEqual(vyuk, "Off — VYUK cannot use this.")
        self.assertIn("you switched VYUK's microphone off", diag)

    def test_back_navigation_also_refreshes(self):
        # The other entry route: forward to the self-test page,
        # then Back lands on the permissions page — the row must
        # still follow a grant made while it was hidden.
        dlg = self._dialog()
        self._show_permissions(dlg)
        self.assertEqual(self._mic_labels(dlg)[0],
                         "Off — VYUK cannot use this.")
        self._grant("granted")
        dlg.pages.setCurrentWidget(dlg._selftest_page)
        dlg._sync_nav()
        dlg._back()
        self.assertIs(dlg.pages.currentWidget(),
                      dlg._permissions_page)
        self.assertEqual(self._mic_labels(dlg)[0],
                         "On — VYUK can use this right now.")

    def test_show_never_writes_the_store(self):
        # Display-only: showing the page re-reads, never writes.
        self._grant("granted")
        before = self._store.read_bytes()
        dlg = self._dialog()
        self._show_permissions(dlg)
        self._show_permissions(dlg)
        self.assertEqual(self._store.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

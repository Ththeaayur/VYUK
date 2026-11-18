"""B6: onboarding surfaces the live microphone state.

The permissions page's Microphone row reads the SAME state the
Permissions page reads (permissions_model with no overrides:
durable grants store + B3 platform probe + device reader) and
shows VYUK-side and Windows-side separately, plus the
three-way diagnosis. It never writes the store, never pops the
first-use ask (the ask fires on the wizard's Microphone page —
D8 — and at a human Connect), never flips anything (D8).

Qt tests need PySide6 (the .venv-qt interpreter); on an
interpreter without it they skip, following the programme's
standing optional-dependency skips. The grants store is
pointed at a temp dir per test; the record-family store is
guarded by _DataGuard (consumers-batch pattern) so Activity
lines written during a test are restored after it.
"""

import os
import pathlib
import sys
import tempfile
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

from core import permission_grants as pg  # noqa: E402
from core.platform_permission_probe import (  # noqa: E402
    PlatformPermissionState,
)

try:
    from PySide6.QtCore import Qt as _Qt  # noqa: F401
    from PySide6.QtWidgets import QApplication as _QA
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
onboarding_mic_state = None
permissions_model = None
OnboardingDialog = None
QApplication = None


def setUpModule():
    """Mirror test_c7_dialog_crop's voice_assistant.* alias.

    gui/onboarding*.py imports voice_assistant.core.* (pre-split
    paths); the tree layout is top-level core/ + gui/, so the
    alias maps voice_assistant -> tree root. The GUI imports
    happen here, after the alias — module-level imports would
    run before it. Snapshot discipline: restore prior entries
    on teardown so a co-run suite's cached modules survive.
    """
    global _APP, _VA_SNAPSHOT, _QT
    global onboarding_mic_state, permissions_model
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
        from gui.onboarding_pages import (  # noqa: E402
            onboarding_mic_state as _oms)
        from gui.pf_permissions import (  # noqa: E402
            permissions_model as _pm)
        from gui.onboarding import (  # noqa: E402
            OnboardingDialog as _OD)
        onboarding_mic_state = _oms
        permissions_model = _pm
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


def _probe(mic_state="allowed", mic_master="allow",
           mic_desktop="allow", cam_state="allowed"):
    """A platform_probe fake: {pid: PlatformPermissionState}."""
    def _one(pid, state, master, desktop):
        return PlatformPermissionState(
            pid, state, master, desktop,
            reason=("Windows allows desktop apps to use the %s."
                    % pid) if state == "allowed" else
            ("Windows has the %s off." % pid),
            source="windows-consent-store")
    return lambda: {
        "microphone": _one("microphone", mic_state,
                           mic_master, mic_desktop),
        "camera": _one("camera", cam_state, "allow", "allow"),
    }


def _device_present():
    return {"device": "present",
            "detail": "Default microphone is active."}


def _device_absent():
    return {"device": "absent",
            "detail": "Default microphone is DISABLED."}


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
    # LAB NAMESPACE NOTE (mirrors test_mic_first_use_ask.py's
    # ConnectTriggerTests): the GUI code reads permission_grants
    # through the `voice_assistant.core` alias, while the flat
    # test world imports `core.permission_grants` — two module
    # instances with separate path overrides (the dual-import
    # trap). These tests therefore drive the ALIASED module —
    # the one the GUI code actually reads — and assert on it.
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
class HelperTests(_StoreMixin):
    """The helper reads the one source and never writes it."""

    def test_ready_when_granted_allowed_present(self):
        self._grant("granted")
        got = onboarding_mic_state(
            platform_probe=_probe(), device_reader=_device_present)
        self.assertEqual(got["state"], "granted")
        self.assertEqual(got["os_state"], "allowed")
        self.assertEqual(got["vyuk_line"],
                         "On — VYUK can use this right now.")
        self.assertTrue(got["os_line"].startswith("Windows: on"))
        self.assertIn("should work", got["diagnosis"])

    def test_denied_by_you_distinct_from_windows(self):
        self._grant("denied")
        got = onboarding_mic_state(
            platform_probe=_probe(), device_reader=_device_present)
        self.assertEqual(got["vyuk_line"],
                         "Off — VYUK cannot use this.")
        self.assertIn("you switched VYUK's microphone off",
                      got["diagnosis"])
        self.assertNotIn("Windows has the microphone off",
                         got["diagnosis"])

    def test_denied_by_windows_master(self):
        self._grant("granted")
        got = onboarding_mic_state(
            platform_probe=_probe(mic_state="denied",
                                  mic_master="deny"),
            device_reader=_device_present)
        self.assertEqual(got["os_state"], "denied")
        self.assertIn("Windows has the microphone off for all apps",
                      got["diagnosis"])

    def test_denied_by_windows_desktop_toggle(self):
        self._grant("granted")
        got = onboarding_mic_state(
            platform_probe=_probe(mic_state="denied",
                                  mic_desktop="deny"),
            device_reader=_device_present)
        self.assertEqual(got["os_state"], "denied")
        self.assertIn("desktop apps", got["diagnosis"])

    def test_no_device_is_honest_never_ready(self):
        self._grant("granted")
        got = onboarding_mic_state(
            platform_probe=_probe(), device_reader=_device_absent)
        self.assertNotIn("should work", got["diagnosis"])
        self.assertIn("no microphone is usable", got["diagnosis"])
        self.assertIn("-9996", got["diagnosis"])

    def test_probe_failure_is_unknown_never_a_verdict(self):
        self._grant("granted")
        def _boom():
            raise RuntimeError("no registry here")
        got = onboarding_mic_state(
            platform_probe=_boom, device_reader=_device_present)
        self.assertEqual(got["os_state"], "unknown")
        self.assertIn("couldn't be read", got["os_line"])
        self.assertNotIn("Windows has the microphone off",
                         got["diagnosis"])

    def test_model_failure_returns_none(self):
        def _boom(**kwargs):
            raise RuntimeError("model broken")
        self.assertIsNone(
            onboarding_mic_state(permissions_model_fn=_boom))

    def test_same_source_as_permissions_page(self):
        # One flow, one source: with the same fakes, the helper's
        # lines are the Permissions page entry's own lines.
        self._grant("granted")
        probe, device = _probe(), _device_present
        got = onboarding_mic_state(
            platform_probe=probe, device_reader=device)
        entry = next(
            e for e in permissions_model(
                platform_probe=probe, device_reader=device)
            if e.permission_id == "microphone")
        self.assertEqual(got["vyuk_line"], entry.state_line())
        self.assertEqual(got["os_line"], entry.os_state_line())
        self.assertEqual(got["diagnosis"], entry.diagnosis)

    def test_helper_never_writes_the_store(self):
        self._grant("granted")
        before = self._store.read_bytes()
        onboarding_mic_state(
            platform_probe=_probe(), device_reader=_device_present)
        self.assertEqual(self._store.read_bytes(), before)

    def test_row_never_pops_the_ask_or_writes(self):
        # Static proof: the onboarding row's code paths never
        # call the first-use ask, the session grant, or the
        # store writer — they only read permissions_model.
        for name in ("onboarding_pages.py", "onboarding.py",
                     "window_shell_onboarding.py"):
            text = (ROOT / "gui" / name).read_text()
            self.assertNotIn("_maybe_mic_first_use_ask", text)
            self.assertNotIn("grant_session_only", text)
            self.assertNotIn("set_permission_state", text)


@unittest.skipUnless(_QT, "Qt unavailable")
class DialogTests(_StoreMixin):
    """The permissions page shows the live row in the dialog."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        return dlg

    def _mic_labels(self, dlg):
        return (dlg._ob_mic_vyuk.text(),
                dlg._ob_mic_os.text(),
                dlg._ob_mic_diag.text())

    def test_row_shows_live_state_at_construction(self):
        self._grant("granted")
        dlg = self._dialog()
        vyuk, os_line, _diag = self._mic_labels(dlg)
        # Lab is Linux: the Windows probe honestly reads unknown.
        self.assertEqual(vyuk, "On — VYUK can use this right now.")
        self.assertIn("couldn't be read", os_line)

    def test_refresh_re_reads_after_a_flip(self):
        self._grant("granted")
        dlg = self._dialog()
        self.assertEqual(self._mic_labels(dlg)[0],
                         "On — VYUK can use this right now.")
        self._grant("denied")
        dlg._ob_mic_refresh.click()
        self.assertEqual(self._mic_labels(dlg)[0],
                         "Off — VYUK cannot use this.")

    def test_settings_button_signals_and_closes_without_writing(self):
        self._grant("granted")
        before = self._store.read_bytes()
        dlg = self._dialog()
        seen = []
        dlg.open_permissions_requested.connect(
            lambda: seen.append(True))
        before_widgets = set(
            QApplication.topLevelWidgets())
        dlg._ob_mic_settings.click()
        self.assertEqual(seen, [True])
        # The dialog accepted (closed) so the page is visible.
        self.assertEqual(dlg.result(), 1)
        # No grant flip, no ask dialog popped.
        self.assertEqual(self._store.read_bytes(), before)
        new_dialogs = [w for w in QApplication.topLevelWidgets()
                       if w not in before_widgets and w is not dlg]
        self.assertEqual(new_dialogs, [])

    def test_fresh_install_reads_denied_not_ready(self):
        # No store file at all: fail-closed mic reads denied —
        # the row must never claim "ready" on a fresh install.
        self.assertFalse(self._store.exists())
        dlg = self._dialog()
        self.assertEqual(self._mic_labels(dlg)[0],
                         "Off — VYUK cannot use this.")


if __name__ == "__main__":
    unittest.main()

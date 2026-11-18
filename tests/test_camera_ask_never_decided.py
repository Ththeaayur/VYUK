"""Field-check fix #21 (round-2 N4 FAIL 1): the wizard camera ask
is unanswerable on a fresh install.

On pristine data there is no grants file, so get_state("camera")
fail-closes to "denied" — and _page_ask treated "denied" as
"already decided" and hid Yes/Not now, although the user never
decided anything. The fix gives _page_ask the mic page's
distinction: "decided" means an explicit record
(permission_never_decided), so the ask shows while the camera
record is absent and still hides on an explicit denial.

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

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


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
    sys.modules["sounddevice"].__dict__.update(_FakeSD.__dict__)

# Flat core import (no Qt, no alias needed): the never-decided
# helper is pure store logic.
from core import permission_grants as flat_pg  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication as _QA
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
OnboardingDialog = None
permission_grants = None
settings_voice = None


def setUpModule():
    """Mirror test_c7_dialog_crop's voice_assistant.* alias.

    gui/* imports voice_assistant.core.* (pre-split paths); the
    tree layout is top-level core/ + gui/, so the alias maps
    voice_assistant -> tree root. The GUI imports happen here,
    after the alias — module-level imports would run before it.
    Snapshot discipline: restore prior entries on teardown so a
    co-run suite's cached modules survive.
    """
    global _APP, _VA_SNAPSHOT, _QT
    global OnboardingDialog, permission_grants, settings_voice
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
        from voice_assistant.core.gui_facade import (  # noqa: E402
            permission_grants as _pg,
            settings_voice as _sv)
        OnboardingDialog = _OD
        permission_grants = _pg
        settings_voice = _sv
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
    """Temp-dir grants store + data/ guard + settings_voice reset.

    The store override lands on the ALIASED module (the GUI
    classes read it) when Qt set it up, and on the flat module
    on the sys interpreter (the core-only classes).
    """

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        pg = permission_grants
        if pg is None:
            pg = flat_pg
        self._pg = pg
        pg.set_path_override(self._store)
        pg.clear_session_grants()
        self._guard = _DataGuard()
        self._guard.__enter__()
        if settings_voice is not None:
            settings_voice.reset_for_tests()

    def tearDown(self):
        self._guard.__exit__()
        self._pg.clear_path_override()
        self._pg.clear_session_grants()
        self._tmp.cleanup()
        super().tearDown()

    def _dialog(self):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        return dlg


class NeverDecidedTests(_StoreMixin):
    """permission_never_decided: the mic distinction, generalised.

    Runs on both interpreters (pure store logic, no Qt needed).
    The assertions go through self._pg — the mixin-selected
    module (aliased under Qt, flat on sys) — so the temp-store
    override lands on the module being read.
    """

    def test_pristine_is_never_decided(self):
        self.assertFalse(self._store.exists())
        self.assertTrue(self._pg.permission_never_decided("camera"))
        self.assertTrue(self._pg.permission_never_decided("microphone"))
        self.assertTrue(self._pg.permission_never_decided("screen"))

    def test_store_without_the_record_is_never_decided(self):
        self._pg.set_permission_state(
            "microphone", "granted", via="settings", how="Settings")
        self.assertTrue(self._pg.permission_never_decided("camera"))
        # ... but the recorded id itself is decided.
        self.assertFalse(
            self._pg.permission_never_decided("microphone"))

    def test_explicit_record_is_decided(self):
        for state in ("granted", "denied", "unknown"):
            self._pg.set_permission_state(
                "camera", state, via="settings", how="Settings")
            self.assertFalse(
                self._pg.permission_never_decided("camera"),
                msg="explicit %s must count as decided" % state)

    def test_corrupt_store_is_not_never_decided(self):
        self._store.write_text("{not json", encoding="utf-8")
        self.assertFalse(self._pg.permission_never_decided("camera"))

    def test_unknown_id_is_not_never_decided(self):
        self.assertFalse(self._pg.permission_never_decided("nope"))

    def test_mic_helper_still_agrees(self):
        # _mic_never_decided now delegates to the general helper:
        # the mic behaviour the mic suites pin is unchanged.
        self.assertTrue(self._pg._mic_never_decided())
        self._pg.set_permission_state(
            "microphone", "denied", via="settings", how="Settings")
        self.assertFalse(self._pg._mic_never_decided())


@unittest.skipUnless(_QT, "Qt unavailable")
class CameraAskTests(_StoreMixin):
    """The camera ask shows while the user never decided."""

    def test_pristine_camera_ask_shows(self):
        dlg = self._dialog()
        cam = dlg._camera_page
        self.assertFalse(cam._yes_button.isHidden())
        self.assertFalse(cam._skip_button.isHidden())
        self.assertTrue(cam._confirm_label.isHidden())

    def test_store_without_camera_record_ask_shows(self):
        # The second shape of the bug: the store exists (a mic
        # answer created it) but holds no camera record — the
        # fail-closed "denied" is still not a decision.
        permission_grants.set_permission_state(
            "microphone", "granted", via="settings", how="Settings")
        self.assertEqual(
            permission_grants.get_state("camera"), "denied")
        dlg = self._dialog()
        self.assertFalse(dlg._camera_page._yes_button.isHidden())
        self.assertFalse(dlg._camera_page._skip_button.isHidden())

    def test_sibling_asks_show_on_pristine(self):
        # Scope pin: screen/files were never fail-closed, so their
        # asks already showed — the fix changes only camera.
        dlg = self._dialog()
        for page in (dlg._screen_page, dlg._files_page):
            self.assertFalse(page._yes_button.isHidden())
            self.assertFalse(page._skip_button.isHidden())

    def test_explicit_deny_still_hides(self):
        permission_grants.set_permission_state(
            "camera", "denied", via="settings", how="Settings")
        dlg = self._dialog()
        cam = dlg._camera_page
        self.assertTrue(cam._yes_button.isHidden())
        self.assertTrue(cam._skip_button.isHidden())
        self.assertFalse(cam._confirm_label.isHidden())
        self.assertIn("switched off", cam._confirm_label.text())

    def test_explicit_grant_still_hides(self):
        permission_grants.set_permission_state(
            "camera", "granted", via="settings", how="Settings")
        dlg = self._dialog()
        cam = dlg._camera_page
        self.assertTrue(cam._yes_button.isHidden())
        self.assertTrue(cam._skip_button.isHidden())
        self.assertFalse(cam._confirm_label.isHidden())
        self.assertIn("already on", cam._confirm_label.text())

    def test_corrupt_store_still_hides(self):
        # D2 no-ask-again: an unreadable store never re-asks.
        self._store.write_text("{not json", encoding="utf-8")
        dlg = self._dialog()
        self.assertTrue(dlg._camera_page._yes_button.isHidden())
        self.assertTrue(dlg._camera_page._skip_button.isHidden())

    def test_yes_grants_on_pristine(self):
        dlg = self._dialog()
        cam = dlg._camera_page
        cam._yes_button.click()
        self.assertEqual(cam.ask_result, "granted")
        self.assertEqual(
            permission_grants.get_state("camera"), "granted")
        self.assertIn("the camera is on.",
                      cam._confirm_label.text())

    def test_skip_writes_nothing(self):
        dlg = self._dialog()
        cam = dlg._camera_page
        cam._skip_button.click()
        self.assertEqual(cam.ask_result, "skipped")
        self.assertFalse(self._store.exists())
        # get_state() itself is unchanged — fail-closed default.
        self.assertEqual(
            permission_grants.get_state("camera"), "denied")


if __name__ == "__main__":
    unittest.main()

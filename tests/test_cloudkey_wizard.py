"""Wizard cloud-brain key page (round-2 N4 FAIL 2).

The first-run wizard used to let an empty Gemini key through
Continue and then claim on the done page that the cloud brain
was ready ("She'll think in the cloud with your Gemini key").
What this suite proves:

* Continue on the key page validates: an empty field with no
  stored key warns once and stays; the second, deliberate press
  advances. A pasted key is saved on Continue (never silently
  dropped because Save was not pressed). A short key stays with
  the "doesn't look like a full key" line.
* The warning re-arms: editing the field after a warning, or
  leaving the page and coming back, warns again — the old
  warning never rides through to an advance.
* The done page is honest: with no key stored it never claims
  "your Gemini key" and names Settings as the way back; with a
  key stored it claims the cloud brain. A key stored earlier
  pre-fills the field and Continues at once; local never visits
  the key page and its done line is unchanged; the choice page's
  honest wording is untouched.

Discipline (mirrors test_first_run_onboarding.py): the GUI
reads permission_grants / config through the
``voice_assistant.core`` alias, while the flat test world
imports ``core.*`` — two module instances (the dual-import
trap). These tests therefore drive the ALIASED modules — the
ones the GUI code actually reads — and assert on them. The
grants store is pointed at a temp dir per test; the record
store is guarded by _DataGuard (consumers-batch pattern).
First-run progress (QSettings firstrun/stage/*) is snapshotted
and reset per test — walking marks stages done, so a second
dialog would otherwise resume mid-flow — and restored after.
The .env file the pasted-key check writes (a fake key) is
snapshotted and restored per test, with config.api_key and
GEMINI_API_KEY restored too. Qt tests need PySide6 (the
.venv-qt interpreter); on an interpreter without it they skip,
following the programme's standing optional-dependency skips.
"""

import os
import pathlib
import sys
import tempfile
import time
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
    sys.modules["sounddevice"].__dict__.update(_FakeSD.__dict__)

try:
    from PySide6.QtWidgets import QApplication as _QA
    from PySide6.QtWidgets import QLabel as _QLabel
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = None
    _QLabel = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
OnboardingDialog = None
config = None
permission_grants = None
settings_voice = None

FAKE_KEY = "AIzaSy" + "F" * 33  # 39 chars, single-line, clearly fake
STAGES = ("interaction", "brain", "permissions", "first_mission")


def setUpModule():
    """Mirror test_first_run_onboarding's voice_assistant.* alias."""
    global _APP, _VA_SNAPSHOT, _QT
    global OnboardingDialog, config
    global permission_grants, settings_voice
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
            config as _cfg,
            permission_grants as _pg,
            settings_voice as _sv)
        OnboardingDialog = _OD
        config = _cfg
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


class _ProgressGuard:
    """Snapshot + reset the first-run progress (QSettings
    firstrun/stage/*): walking marks stages done, so a second
    dialog in this process would otherwise resume mid-flow."""

    def __init__(self):
        self._saved = {}

    def reset(self):
        try:
            from PySide6.QtCore import QSettings
        except ImportError:
            return  # no Qt, no dialog, nothing to reset
        s = QSettings("NovaAI", "Nova")
        for stage in STAGES:
            key = f"firstrun/stage/{stage}"
            self._saved[key] = s.value(key)
            s.remove(key)
        s.sync()

    def restore(self):
        try:
            from PySide6.QtCore import QSettings
        except ImportError:
            return  # no Qt, no dialog, nothing to restore
        s = QSettings("NovaAI", "Nova")
        for key, value in self._saved.items():
            if value is None:
                s.remove(key)
            else:
                s.setValue(key, value)
        s.sync()


class _EnvGuard:
    """Snapshot + restore .env, config.api_key and the
    GEMINI_API_KEY env var (the pasted-key check writes a fake
    key through the real save path)."""

    def __enter__(self):
        self._env_path = ROOT / ".env"
        self._had = self._env_path.exists()
        self._saved = (self._env_path.read_bytes()
                       if self._had else None)
        self._orig_api_key = (str(getattr(config, "api_key", "") or "")
                              if config is not None else "")
        self._orig_env = os.environ.get("GEMINI_API_KEY")
        if config is not None:
            config.api_key = ""
        os.environ.pop("GEMINI_API_KEY", None)
        return self

    def __exit__(self, *exc):
        if self._had:
            self._env_path.write_bytes(self._saved)
        else:
            try:
                self._env_path.unlink()
            except OSError:
                pass
            try:
                (ROOT / ".env.tmp").unlink()
            except OSError:
                pass
        if config is not None:
            config.api_key = self._orig_api_key
        if self._orig_env is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = self._orig_env
        return False


class _StoreMixin(unittest.TestCase):
    """Temp-dir grants store + data/ guard + settings_voice
    reset + progress reset + .env guard (mirrors
    test_first_run_onboarding's _StoreMixin, plus the
    progress/env guards this suite needs)."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        pg = permission_grants
        if pg is None:
            from core import permission_grants as _flat_pg
            pg = _flat_pg
        self._pg = pg
        pg.set_path_override(self._store)
        pg.clear_session_grants()
        self._guard = _DataGuard()
        self._guard.__enter__()
        if settings_voice is not None:
            settings_voice.reset_for_tests()
        self._progress = _ProgressGuard()
        self._progress.reset()
        self._env = _EnvGuard()
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__()
        self._progress.restore()
        self._guard.__exit__()
        self._pg.clear_path_override()
        self._pg.clear_session_grants()
        self._tmp.cleanup()
        super().tearDown()

    # -- wizard driving -------------------------------------------
    def _pump(self, seconds=0.1):
        end = time.time() + seconds
        while time.time() < end:
            _QA.instance().processEvents()
            time.sleep(0.02)

    def _dialog(self):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        dlg.show()
        self._pump()
        return dlg

    def _cur(self, dlg):
        return dlg.pages.currentWidget()

    def _go_next(self, dlg):
        dlg.btn_next.click()
        self._pump()

    def _walk_to_key(self, dlg, choice="cloud"):
        self.assertIs(self._cur(dlg), dlg._welcome_page)
        self._go_next(dlg)  # welcome -> mic
        self._go_next(dlg)  # mic -> choice
        dlg._select_choice(choice)
        self._pump()
        self._go_next(dlg)  # choice -> key (cloud/both)
        return self._cur(dlg) is dlg._key_page

    def _walk_to_done(self, dlg, max_clicks=16):
        for _ in range(max_clicks):
            if self._cur(dlg) is dlg._done_page:
                return True
            if self._cur(dlg) is dlg._key_page:
                self._go_next(dlg)
                if self._cur(dlg) is dlg._key_page:
                    self._go_next(dlg)  # second, deliberate press
                continue
            if self._cur(dlg) is dlg._selftest_page:
                self._pump(0.6)  # self-test auto-runs on show
            self._go_next(dlg)
        return self._cur(dlg) is dlg._done_page


@unittest.skipUnless(_QT, "Qt unavailable")
class CloudKeyWizardTests(_StoreMixin):
    """Key-page validation + honest done page (round-2 N4 FAIL 2)."""

    def test_empty_key_first_continue_stays_and_warns(self):
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "cloud"))
        self.assertEqual(dlg.ob_api_key.text(), "")
        self._go_next(dlg)
        self.assertIs(self._cur(dlg), dlg._key_page,
                      "first Continue with an empty key must stay")
        status = dlg.ob_key_status.text()
        self.assertIn("No key saved yet", status)
        self.assertIn("Settings", status)

    def test_empty_key_second_continue_advances(self):
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "cloud"))
        self._go_next(dlg)  # warns, stays
        self.assertIs(self._cur(dlg), dlg._key_page)
        self._go_next(dlg)  # deliberate second press
        self.assertIs(self._cur(dlg), dlg._browser_page)

    def test_pasted_key_continues_and_saves(self):
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "cloud"))
        dlg.ob_api_key.setText(FAKE_KEY)
        self._pump()
        self._go_next(dlg)
        self.assertIs(self._cur(dlg), dlg._browser_page,
                      "a pasted key must Continue")
        self.assertEqual(config.api_key, FAKE_KEY)
        self.assertEqual(os.environ.get("GEMINI_API_KEY"), FAKE_KEY)
        self.assertTrue((ROOT / ".env").exists(),
                        "the pasted key must reach .env")

    def test_short_key_continue_stays(self):
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "cloud"))
        dlg.ob_api_key.setText("short")
        self._pump()
        self._go_next(dlg)
        self.assertIs(self._cur(dlg), dlg._key_page)
        self.assertIn("doesn't look like a full key",
                      dlg.ob_key_status.text())

    def test_edit_after_warning_rewarns(self):
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "cloud"))
        self._go_next(dlg)  # warns, stays
        self.assertIs(self._cur(dlg), dlg._key_page)
        dlg.ob_api_key.setText("abc")
        self._pump()
        dlg.ob_api_key.setText("")
        self._pump()
        self._go_next(dlg)
        self.assertIs(self._cur(dlg), dlg._key_page,
                      "an edited-then-cleared field must warn again")
        self.assertIn("No key saved yet", dlg.ob_key_status.text())

    def test_back_and_forth_rewarns(self):
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "cloud"))
        self._go_next(dlg)  # warns, stays
        self.assertIs(self._cur(dlg), dlg._key_page)
        dlg.btn_back.click()
        self._pump()
        self.assertIs(self._cur(dlg), dlg._choice_page)
        self._go_next(dlg)  # choice -> key again (warning re-armed)
        self.assertIs(self._cur(dlg), dlg._key_page)
        self._go_next(dlg)
        self.assertIs(self._cur(dlg), dlg._key_page,
                      "re-entering the key page must warn again")
        self.assertIn("No key saved yet", dlg.ob_key_status.text())

    def test_done_page_no_key_honest(self):
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "cloud"))
        self.assertTrue(self._walk_to_done(dlg))
        line = dlg.done_brain_line.text()
        self.assertNotIn("your Gemini key", line)
        self.assertIn("no Gemini key is saved yet", line)
        self.assertIn("Settings", line)

    def test_done_page_with_key_claims_key(self):
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "cloud"))
        dlg.ob_api_key.setText(FAKE_KEY)
        self._pump()
        self.assertTrue(self._walk_to_done(dlg))
        self.assertIn("your Gemini key", dlg.done_brain_line.text())

    def test_done_page_both_no_key_honest(self):
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "both"))
        self.assertTrue(self._walk_to_done(dlg))
        line = dlg.done_brain_line.text()
        self.assertIn("think on this PC for now", line)
        self.assertIn("Settings", line)
        self.assertFalse(
            line.startswith("She'll think in the cloud"),
            "without a key the cloud-first claim must not lead")

    def test_stored_key_prefilled_and_continues(self):
        config.api_key = FAKE_KEY
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        dlg = self._dialog()
        self.assertTrue(self._walk_to_key(dlg, "cloud"))
        self.assertEqual(dlg.ob_api_key.text(), FAKE_KEY,
                         "the field must start filled with the key")
        self._go_next(dlg)
        self.assertIs(self._cur(dlg), dlg._browser_page,
                      "a stored key must Continue at once")

    def test_local_choice_skips_key_page(self):
        dlg = self._dialog()
        self._go_next(dlg)  # welcome -> mic
        self._go_next(dlg)  # mic -> choice
        dlg._select_choice("local")
        self._pump()
        self._go_next(dlg)  # choice -> onward (never the key page)
        self.assertIsNot(self._cur(dlg), dlg._key_page)
        self.assertTrue(self._walk_to_done(dlg))
        self.assertIn("think on this PC",
                      dlg.done_brain_line.text())

    def test_choice_page_wording_unchanged(self):
        dlg = self._dialog()
        self._go_next(dlg)  # welcome -> mic
        self._go_next(dlg)  # mic -> choice
        labels = [lbl.text() for lbl in
                  dlg._choice_page.findChildren(_QLabel)]
        self.assertTrue(
            any("Needs the free key pasted in on the next step" in t
                for t in labels),
            "the cloud card must still name the next step")


if __name__ == "__main__":
    unittest.main()

"""Wizard key-page warning wrap (round-3 section E finding E-F303).

The key page's amber warning label (``ob_key_status``) had no
``setWordWrap(True)``, unlike every neighbour on the page — so its
unwrapped ~200-character line made the key page's minimum width
~1,193 px, blowing out the shared QStackedWidget, and every later
wizard page rendered cut off at the dialog's right edge (setup
"Waiting" labels, permissions Details buttons and state words,
self-test result labels, the done page's proof card, and the
"Full" profile card all sat past the visible width).

What this suite proves, on the real dialog offscreen:

* The status label wraps (the one-line fix), and the warning
  text itself is unchanged word-for-word.
* After the warning, the key page and the stacked widget fit
  inside the dialog's visible width — and the setup, profile,
  permissions, self-test and done pages' controls all sit
  inside it.
* The same walk WITHOUT the warning (a stored key skips it)
  fits too — the control that proves the warning, not the
  pages, was the cause.

Discipline (mirrors tests/test_cloudkey_wizard.py): the GUI reads
permission_grants / config through the ``voice_assistant.core``
alias, while the flat test world imports ``core.*`` — two module
instances (the dual-import trap). These tests therefore drive the
ALIASED modules — the ones the GUI code actually reads — and
assert on them. The grants store is pointed at a temp dir per
test; the record store is guarded by _DataGuard
(consumers-batch pattern). First-run progress (QSettings
firstrun/stage/*) is snapshotted and reset per test — walking
marks stages done, so a second dialog would otherwise resume
mid-flow — and restored after. The .env file is snapshotted and
restored per test, with config.api_key and GEMINI_API_KEY
restored too. Qt tests need PySide6 (the .venv-qt interpreter);
on an interpreter without it they skip, following the
programme's standing optional-dependency skips.
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

try:
    from PySide6.QtCore import QPoint as _QPoint
    from PySide6.QtWidgets import QApplication as _QA
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = None
    _QPoint = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
OnboardingDialog = None
config = None
permission_grants = None
settings_voice = None

FAKE_KEY = "AIzaSy" + "F" * 33  # 39 chars, single-line, clearly fake
STAGES = ("interaction", "brain", "permissions", "first_mission")
WARNING_TEXT = (
    "No key saved yet — you picked the cloud brain, "
    "so she'll think on this PC until you add your "
    "Gemini key in Settings. Press Continue again "
    "to go on without a key, or paste your key "
    "above.")


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
        self._saved["sounds_enabled"] = s.value("sounds_enabled")
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
    GEMINI_API_KEY env var."""

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
    test_cloudkey_wizard's _StoreMixin)."""

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
        dlg.resize(780, 560)
        self._pump()
        return dlg

    def _cur(self, dlg):
        return dlg.pages.currentWidget()

    def _go_next(self, dlg):
        dlg.btn_next.click()
        self._pump()

    def _walk_to_key(self, dlg, choice="both"):
        self.assertIs(self._cur(dlg), dlg._welcome_page)
        self._go_next(dlg)  # welcome -> mic
        self._go_next(dlg)  # mic -> choice
        dlg._select_choice(choice)
        self._pump()
        self._go_next(dlg)  # choice -> key (cloud/both)
        self.assertIs(self._cur(dlg), dlg._key_page)

    def _warn(self, dlg):
        """First Continue on the empty key page: warns, stays."""
        self._go_next(dlg)
        self.assertIs(self._cur(dlg), dlg._key_page,
                      "first Continue with an empty key must stay")
        self.assertIn("No key saved yet", dlg.ob_key_status.text())

    def _viewport(self, dlg):
        return dlg._pages_scroll.viewport().width()

    def _right_edge(self, widget, page):
        return widget.mapTo(page, _QPoint(widget.width(), 0)).x()

    def _walk_to_permissions(self, dlg):
        for _ in range(8):
            if self._cur(dlg) is dlg._permissions_page:
                return
            self._go_next(dlg)
        self.assertIs(self._cur(dlg), dlg._permissions_page)


@unittest.skipUnless(_QT, "Qt unavailable")
class WizardWrapTests(_StoreMixin):
    """E-F303: the key-page warning must not blow out the dialog."""

    def test_key_status_label_wraps(self):
        dlg = self._dialog()
        self.assertTrue(dlg.ob_key_status.wordWrap(),
                        "ob_key_status must wrap like its neighbours")

    def test_warning_text_unchanged(self):
        dlg = self._dialog()
        self._walk_to_key(dlg)
        self._warn(dlg)
        self.assertEqual(dlg.ob_key_status.text(), WARNING_TEXT,
                         "the warning wording must not change")

    def test_key_page_minimum_width_fits_after_warning(self):
        dlg = self._dialog()
        self._walk_to_key(dlg)
        self._warn(dlg)
        vw = self._viewport(dlg)
        self.assertLessEqual(
            dlg._key_page.minimumSizeHint().width(), vw,
            "the key page must fit the dialog once warned")

    def test_stacked_minimum_width_fits_after_warning(self):
        dlg = self._dialog()
        self._walk_to_key(dlg)
        self._warn(dlg)
        vw = self._viewport(dlg)
        self.assertLessEqual(
            dlg.pages.minimumSizeHint().width(), vw,
            "no page may blow out the shared stacked widget")

    def test_setup_waiting_labels_visible_after_warning(self):
        dlg = self._dialog()
        self._walk_to_key(dlg)
        self._warn(dlg)
        self._go_next(dlg)  # second, deliberate press -> setup
        if dlg._setup_page is None:
            self.skipTest("pack complete, no setup page")
        self.assertIs(self._cur(dlg), dlg._setup_page)
        self._pump(0.3)
        vw = self._viewport(dlg)
        for stage, lbl in dlg._setup_rows.items():
            self.assertLessEqual(
                self._right_edge(lbl, dlg._setup_page), vw,
                f"setup {stage} state label is cut off")

    def test_profile_full_card_visible_after_warning(self):
        dlg = self._dialog()
        self._walk_to_key(dlg)
        self._warn(dlg)
        self._go_next(dlg)  # second, deliberate press -> setup
        if dlg._setup_page is None:
            self.skipTest("pack complete, no setup page")
        self.assertIs(self._cur(dlg), dlg._setup_page)
        dlg._toggle_advanced()
        self._pump()
        self._go_next(dlg)  # voice -> language
        self._go_next(dlg)  # language -> profile
        self.assertIs(self._cur(dlg), dlg._profile_page)
        vw = self._viewport(dlg)
        full = dlg.profile_cards.get("full")
        self.assertIsNotNone(full, "the Full profile card must exist")
        self.assertLessEqual(
            self._right_edge(full, dlg._profile_page), vw,
            "the Full profile card is cut off after the warning")

    def test_permissions_details_and_state_visible_after_warning(self):
        dlg = self._dialog()
        self._walk_to_key(dlg)
        self._warn(dlg)
        self._go_next(dlg)  # second, deliberate press -> setup
        self._walk_to_permissions(dlg)
        self._pump(0.3)
        vw = self._viewport(dlg)
        for pid, row in dlg._perm_rows.items():
            self.assertLessEqual(
                self._right_edge(row["action"], dlg._permissions_page),
                vw, f"{pid} Details button is cut off")
            self.assertLessEqual(
                self._right_edge(row["state"], dlg._permissions_page),
                vw, f"{pid} state word is cut off")

    def test_selftest_result_labels_visible_after_warning(self):
        dlg = self._dialog()
        self._walk_to_key(dlg)
        self._warn(dlg)
        self._go_next(dlg)  # second, deliberate press -> setup
        self._walk_to_permissions(dlg)
        self._go_next(dlg)  # permissions -> self-test
        self.assertIs(self._cur(dlg), dlg._selftest_page)
        self._pump(1.0)  # self-test auto-runs on show
        vw = self._viewport(dlg)
        for name, lbl in dlg._selftest_page._rows.items():
            self.assertLessEqual(
                self._right_edge(lbl, dlg._selftest_page), vw,
                f"self-test {name} result label is cut off")

    def test_done_proof_fits_after_warning(self):
        dlg = self._dialog()
        self._walk_to_key(dlg)
        self._warn(dlg)
        self._go_next(dlg)  # second, deliberate press -> setup
        self._walk_to_permissions(dlg)
        self._go_next(dlg)  # permissions -> self-test
        self.assertIs(self._cur(dlg), dlg._selftest_page)
        self._pump(1.0)
        self._go_next(dlg)  # self-test -> done
        self.assertIs(self._cur(dlg), dlg._done_page)
        self._pump(0.3)
        vw = self._viewport(dlg)
        proof = getattr(dlg, "_w9_proof_widget", None)
        self.assertIsNotNone(proof, "the done page proof card "
                             "must exist")
        self.assertLessEqual(
            self._right_edge(proof, dlg._done_page), vw,
            "the first-run proof card is cut off")

    def test_no_warning_control_fits(self):
        """A stored key skips the warning: the same pages must
        fit — the control that proves the warning, not the
        pages, was the cause."""
        config.api_key = FAKE_KEY
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        dlg = self._dialog()
        self._walk_to_key(dlg)
        self.assertEqual(dlg.ob_key_status.text(), "")
        self._go_next(dlg)  # advances at once, no warning
        self.assertIsNot(self._cur(dlg), dlg._key_page)
        vw = self._viewport(dlg)
        self.assertLessEqual(
            dlg._key_page.minimumSizeHint().width(), vw,
            "the key page must fit without a warning")
        self._walk_to_permissions(dlg)
        self._pump(0.3)
        vw = self._viewport(dlg)
        for pid, row in dlg._perm_rows.items():
            self.assertLessEqual(
                self._right_edge(row["action"], dlg._permissions_page),
                vw, f"{pid} Details button is cut off (control)")


if __name__ == "__main__":
    unittest.main()

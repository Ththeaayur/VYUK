"""Wizard welcome title (round-3 section E finding E-F301).

The welcome title "Meet VYUK" was sliced mid-glyph at the scroll
viewport's bottom edge at the default 780x560 dialog — and
invisible entirely at 760 wide (an 800-wide window), where the nav
hint wraps taller and the viewport shrinks to 319. The welcome orb
took the shared sizeHint 320x320 (the Home hero's hint,
gui/visualizer_paint.py), so the title landed at y=326 in a 336
viewport (780) and a 319 viewport (760).

What this suite proves, on the real dialog offscreen:

* The welcome orb is capped at 240x240 (and stays >= 170, so it
  is still the page's hero) — the one-line fix.
* The title reads "Meet VYUK" unchanged, and the title and its
  body sit fully inside the scroll viewport at both default dialog
  sizes (780x560 and 760x560).
* The dialog size itself is unchanged.

Discipline (mirrors tests/test_r3oauth_build.py): the GUI reads
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
    from PySide6.QtWidgets import QLabel as _QLabel
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = None
    _QPoint = None
    _QLabel = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
OnboardingDialog = None
OrbVisualizer = None
config = None
permission_grants = None
settings_voice = None

STAGES = ("interaction", "brain", "permissions", "first_mission")
TITLE_TEXT = "Meet VYUK"
BODY_START = "A calm voice assistant"


def setUpModule():
    """Mirror test_first_run_onboarding's voice_assistant.* alias."""
    global _APP, _VA_SNAPSHOT, _QT
    global OnboardingDialog, OrbVisualizer, config
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
        from gui.visualizer import (  # noqa: E402
            OrbVisualizer as _Orb)
        from voice_assistant.core.gui_facade import (  # noqa: E402
            config as _cfg,
            permission_grants as _pg,
            settings_voice as _sv)
        OnboardingDialog = _OD
        OrbVisualizer = _Orb
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
    test_r3oauth_build's _StoreMixin)."""

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

    def _dialog(self, w, h):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        dlg.show()
        self._pump()
        dlg.resize(w, h)
        self._pump()
        return dlg

    def _welcome_parts(self, dlg):
        """(orb, title, body, viewport) on the welcome page."""
        page = dlg._welcome_page
        if dlg.pages.currentWidget() is not page:
            # Round-3 wizard expansion: the dialog opens on the
            # language page now, so walk one step to welcome.
            dlg._next()
            self._pump()
        self.assertIs(dlg.pages.currentWidget(), page,
                      "the walk must land on the welcome page")
        vp = dlg._pages_scroll.viewport()
        orb = None
        title = None
        body = None
        for w in page.findChildren(_QLabel):
            if w.text().startswith("Meet "):
                title = w
            elif w.text().startswith(BODY_START):
                body = w
        for w in page.findChildren(OrbVisualizer):
            orb = w
        self.assertIsNotNone(orb, "the welcome orb must exist")
        self.assertIsNotNone(title, "the welcome title must exist")
        self.assertIsNotNone(body, "the welcome body must exist")
        return orb, title, body, vp

    def _label_fits(self, dlg, widget, vp):
        top = widget.mapTo(vp, _QPoint(0, 0)).y()
        bot = widget.mapTo(vp, _QPoint(0, widget.height())).y()
        return top, bot, top >= 0 and bot <= vp.height()


@unittest.skipUnless(_QT, "Qt unavailable")
class WizardTitleTests(_StoreMixin):
    """E-F301: the welcome title must sit above the fold."""

    def test_welcome_orb_is_capped(self):
        dlg = self._dialog(780, 560)
        orb, _, _, _ = self._welcome_parts(dlg)
        self.assertLessEqual(orb.width(), 240,
                             "the welcome orb must not take the "
                             "320 Home-hero hint")
        self.assertLessEqual(orb.height(), 240,
                             "the welcome orb must not take the "
                             "320 Home-hero hint")

    def test_welcome_orb_still_prominent(self):
        dlg = self._dialog(780, 560)
        orb, _, _, _ = self._welcome_parts(dlg)
        self.assertGreaterEqual(orb.width(), 170,
                                "the orb must stay the page's hero")
        self.assertGreaterEqual(orb.height(), 170,
                                "the orb must stay the page's hero")

    def test_welcome_title_text_unchanged(self):
        dlg = self._dialog(780, 560)
        _, title, _, _ = self._welcome_parts(dlg)
        self.assertEqual(title.text(), TITLE_TEXT,
                         "the title wording must not change")

    def test_title_fully_visible_at_780(self):
        dlg = self._dialog(780, 560)
        _, title, _, vp = self._welcome_parts(dlg)
        top, bot, ok = self._label_fits(dlg, title, vp)
        self.assertTrue(ok, f"title must sit inside the viewport "
                            f"(top={top} bot={bot} vp_h={vp.height()})")

    def test_body_fully_visible_at_780(self):
        dlg = self._dialog(780, 560)
        _, _, body, vp = self._welcome_parts(dlg)
        top, bot, ok = self._label_fits(dlg, body, vp)
        self.assertTrue(ok, f"body must sit inside the viewport "
                            f"(top={top} bot={bot} vp_h={vp.height()})")

    def test_title_fully_visible_at_760(self):
        dlg = self._dialog(760, 560)
        _, title, _, vp = self._welcome_parts(dlg)
        top, bot, ok = self._label_fits(dlg, title, vp)
        self.assertTrue(ok, f"title must sit inside the viewport "
                            f"at 760 wide (top={top} bot={bot} "
                            f"vp_h={vp.height()})")

    def test_body_fully_visible_at_760(self):
        dlg = self._dialog(760, 560)
        _, _, body, vp = self._welcome_parts(dlg)
        top, bot, ok = self._label_fits(dlg, body, vp)
        self.assertTrue(ok, f"body must sit inside the viewport "
                            f"at 760 wide (top={top} bot={bot} "
                            f"vp_h={vp.height()})")

    def test_dialog_size_unchanged(self):
        dlg = self._dialog(780, 560)
        self.assertEqual((dlg.width(), dlg.height()), (780, 560),
                         "the dialog size must not change")
        dlg2 = self._dialog(760, 560)
        self.assertEqual((dlg2.width(), dlg2.height()), (760, 560),
                         "the dialog size must not change")


if __name__ == "__main__":
    unittest.main()

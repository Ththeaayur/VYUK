"""Wizard switch note (round-3 section E finding E-F302, option D).

The user ruled (2026-11-20): the choice and profile card pages
scroll by design — the item closes as by-design — but the "You
can switch any time in Settings — nothing here is locked in."
note must move out of the scrolling pages into the dialog chrome,
so it is always visible. Before the move it sat at the bottom of
the choice/profile pages, below the fold (the choice page is 550
px tall against a 336 px viewport at 780), so the reassurance was
invisible exactly where it matters.

What this suite proves, on the real dialog offscreen:

* The note is a chrome label — a direct child of the dialog,
  outside the scroll area, inside the dialog bounds — visible on
  the choice and profile pages at both default dialog sizes
  (780x560 and 760x560), and in the choice-only dialog.
* The note is gone from inside the choice/profile pages.
* The note hides on the other pages (welcome, key).
* The note text is word-for-word, and the dialog size is
  unchanged.

The card pages themselves still scroll by design — this suite
does NOT assert they fit (see the E-F302 investigation report:
full fit is geometrically impossible at 780x560).

Discipline (mirrors tests/test_r3oauth_build.py and
tests/test_r3ef301_wizard_title.py): the GUI reads
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
config = None
permission_grants = None
settings_voice = None

STAGES = ("interaction", "brain", "permissions", "first_mission")
NOTE_TEXT = ("You can switch any time in Settings — "
             "nothing here is locked in.")


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
        self._orig_api_key = (config.api_key
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

    def _dialog(self, w, h, choice_only=False):
        dlg = OnboardingDialog(choice_only=choice_only)
        self.addCleanup(dlg.deleteLater)
        dlg.show()
        self._pump()
        dlg.resize(w, h)
        self._pump()
        return dlg

    def _goto_choice(self, dlg):
        # Round-3 wizard expansion: language is first and name +
        # voice joined the main flow, so the walk to choice is
        # five steps now (was welcome -> mic -> choice).
        dlg._next()  # language -> welcome
        self._pump()
        dlg._next()  # welcome -> mic
        self._pump()
        dlg._next()  # mic -> name
        self._pump()
        dlg._next()  # name -> voice
        self._pump()
        dlg._next()  # voice -> choice
        self._pump()
        self.assertIs(dlg.pages.currentWidget(), dlg._choice_page,
                      "the walk must land on the choice page")

    def _goto_profile(self, dlg):
        self._goto_choice(dlg)
        # Round-3 wizard expansion: the profile page is main
        # flow now, right after choice — no detour toggle. The
        # choice gate still applies, so a choice must be picked
        # before Continue leaves the choice page (the old detour
        # bypassed the gate, which is why no pick was needed).
        dlg._select_choice("local")
        self._pump()
        dlg._next()  # choice -> profile
        self._pump()
        self.assertIs(dlg.pages.currentWidget(), dlg._profile_page,
                      "the walk must land on the profile page")

    def _assert_note_in_chrome(self, dlg):
        """The chrome note: a visible dialog-child label with the
        exact note text, outside the scroll area, inside the
        dialog bounds."""
        note = getattr(dlg, "_switch_note", None)
        self.assertIsNotNone(note, "the dialog must carry a "
                                   "_switch_note chrome label")
        self.assertIsInstance(note, _QLabel,
                              "the chrome note must be a QLabel")
        self.assertEqual(note.text(), NOTE_TEXT,
                         "the note wording must not change")
        self.assertFalse(dlg._pages_scroll.isAncestorOf(note),
                         "the note must live outside the scroll area")
        self.assertTrue(note.isVisible(),
                        "the note must be visible on this page")
        top = note.mapTo(dlg, _QPoint(0, 0)).y()
        bottom = note.mapTo(dlg, _QPoint(0, note.height())).y()
        scroll_bottom = dlg._pages_scroll.mapTo(
            dlg, _QPoint(0, dlg._pages_scroll.height())).y()
        self.assertGreaterEqual(
            top, scroll_bottom - 1,
            f"the note must sit below the scroll area "
            f"(top={top} scroll_bottom={scroll_bottom})")
        self.assertLessEqual(
            bottom, dlg.height(),
            f"the note must sit inside the dialog "
            f"(bottom={bottom} dialog_h={dlg.height()})")

    def _assert_note_hidden(self, dlg):
        note = getattr(dlg, "_switch_note", None)
        self.assertIsNotNone(note, "the dialog must carry a "
                                   "_switch_note chrome label")
        self.assertFalse(note.isVisible(),
                         "the note must hide off the card pages")

    def _assert_note_not_in_page(self, page):
        for w in page.findChildren(_QLabel):
            self.assertFalse(
                w.text().startswith("You can switch any time"),
                "the switch note must not live inside the page")


@unittest.skipUnless(_QT, "Qt unavailable")
class WizardNoteTests(_StoreMixin):
    """E-F302 option D: the switch note lives in the chrome."""

    def test_note_in_chrome_on_choice_780(self):
        dlg = self._dialog(780, 560)
        self._goto_choice(dlg)
        self._assert_note_in_chrome(dlg)

    def test_note_gone_from_choice_page(self):
        dlg = self._dialog(780, 560)
        self._goto_choice(dlg)
        self._assert_note_not_in_page(dlg._choice_page)

    def test_note_in_chrome_on_profile_780(self):
        dlg = self._dialog(780, 560)
        self._goto_profile(dlg)
        self._assert_note_in_chrome(dlg)

    def test_note_gone_from_profile_page(self):
        dlg = self._dialog(780, 560)
        self._goto_profile(dlg)
        self._assert_note_not_in_page(dlg._profile_page)

    def test_note_in_chrome_at_760(self):
        dlg = self._dialog(760, 560)
        self._goto_choice(dlg)
        self._assert_note_in_chrome(dlg)
        self._goto_profile(dlg)
        self._assert_note_in_chrome(dlg)

    def test_note_hidden_on_welcome_and_signin(self):
        dlg = self._dialog(780, 560)
        # Round-3 wizard expansion: the dialog opens on the
        # language page now — walk one step to welcome.
        dlg._next()  # language -> welcome
        self._pump()
        self.assertIs(dlg.pages.currentWidget(), dlg._welcome_page,
                      "the walk must land on the welcome page")
        self._assert_note_hidden(dlg)
        self._goto_choice(dlg)
        dlg._select_choice("cloud")
        self._pump()
        dlg._next()  # choice -> profile
        self._pump()
        dlg._next()  # profile -> sign-in (no download for cloud)
        self._pump()
        self.assertIs(dlg.pages.currentWidget(), dlg._signin_page,
                      "the walk must land on the sign-in page")
        self._assert_note_hidden(dlg)

    def test_note_in_chrome_choice_only(self):
        dlg = self._dialog(780, 560, choice_only=True)
        self.assertIs(dlg.pages.currentWidget(), dlg._choice_page,
                      "the choice-only dialog opens on the choice")
        self._assert_note_in_chrome(dlg)
        self._assert_note_not_in_page(dlg._choice_page)

    def test_dialog_size_unchanged(self):
        dlg = self._dialog(780, 560)
        self.assertEqual((dlg.width(), dlg.height()), (780, 560),
                         "the dialog size must not change")
        dlg2 = self._dialog(760, 560)
        self.assertEqual((dlg2.width(), dlg2.height()), (760, 560),
                         "the dialog size must not change")


class NoteSourceTests(unittest.TestCase):
    """The note text lives once, in the chrome — not in the pages."""

    def test_note_text_defined_once_in_chrome(self):
        chrome = (ROOT / "gui" / "onboarding.py").read_text(
            encoding="utf-8")
        self.assertIn("You can switch any time in Settings",
                      chrome, "the chrome must carry the note text")
        self.assertIn("_switch_note", chrome,
                      "the chrome label must be named _switch_note")
        pages = (ROOT / "gui" / "onboarding_pages.py").read_text(
            encoding="utf-8")
        self.assertNotIn("You can switch any time", pages,
                         "no switch note may remain inside the pages")
        flow = (ROOT / "gui" / "onboarding_flow.py").read_text(
            encoding="utf-8")
        self.assertIn("_switch_note", flow,
                      "_sync_nav must toggle the chrome note")


if __name__ == "__main__":
    unittest.main()

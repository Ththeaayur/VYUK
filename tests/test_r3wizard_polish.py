"""Wizard polish (round-3 section E follow-up, user-chosen
2026-11-21: "polish the look").

The wizard was correct (E-F301/302/303 fixed) but not premium.
What this suite proves, on the real dialog offscreen:

* Choice + profile cards: every card's tagline sits at the same y
  within its card — the RECOMMENDED pill row exists on every card
  (blank on the unrecommended ones), so the recommended card's
  tagline no longer sits a pill-row lower than the rest. The blank
  pill is the same height as the real pill by construction.
* Card tightening roughly offsets the pill rows: the choice page
  is no taller than 530 px at 780 (baseline 523) and the profile
  page no taller than 425 (baseline 425).
* Permissions page: a one-line body under the title (the page
  used to jump straight into the rows), and every row action
  button the same width — including "Take back" — so the state
  column lines up.
* Done page: the brain recap line is emphasised (full-text
  colour, semibold) instead of reading as a third muted line.
* Guards: the welcome title/body still sit fully inside the
  viewport at 780 and 760 (E-F301), the dialog size is unchanged,
  the key page is untouched, the brain-choice gate still holds
  Continue.

Discipline (mirrors tests/test_r3ef301_wizard_title.py): the GUI
reads permission_grants / config through the
``voice_assistant.core`` alias, while the flat test world imports
``core.*`` — two module instances (the dual-import trap). These
tests therefore drive the ALIASED modules — the ones the GUI code
actually reads — and assert on them. The grants store is pointed
at a temp dir per test; the record store is guarded by _DataGuard
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
PERM_BODY = ("One row per permission — what's on, what's off, and "
             "what you've never decided.")
PERM_TITLE = "Permissions — your call"


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
        self._pump(0.2)
        return dlg

    def _show_page(self, dlg, page):
        dlg.pages.setCurrentWidget(page)
        dlg._sync_nav()
        self._pump()

    def _tagline_spread(self, cards):
        """Max-min tagline y within its card, over the cards."""
        ys = {}
        for key, card in cards.items():
            labels = [w for w in card.findChildren(_QLabel)
                      if w.objectName() == "muted" and w.wordWrap()]
            self.assertTrue(labels, f"card {key} must have a tagline")
            ys[key] = labels[0].mapTo(card, _QPoint(0, 0)).y()
        return max(ys.values()) - min(ys.values()), ys

    def _perm_button_widths(self, dlg):
        widths = [w["action"].width()
                  for w in dlg._perm_rows.values()]
        widths.append(dlg._sounds_action.width())
        return widths


@unittest.skipUnless(_QT, "Qt unavailable")
class WizardPolishTests(_StoreMixin):
    """The polish contract, pinned structurally (no pixel pins)."""

    def test_choice_taglines_aligned_780(self):
        dlg = self._dialog(780, 560)
        dlg._select_choice("both")
        self._show_page(dlg, dlg._choice_page)
        spread, ys = self._tagline_spread(dlg.choice_cards)
        self.assertLessEqual(
            spread, 1, f"choice taglines must start at the same y "
                       f"(spread={spread} ys={ys})")

    def test_profile_taglines_aligned_780(self):
        dlg = self._dialog(780, 560)
        if not dlg._detour:
            dlg._toggle_advanced()
        self._show_page(dlg, dlg._profile_page)
        spread, ys = self._tagline_spread(dlg.profile_cards)
        self.assertLessEqual(
            spread, 1, f"profile taglines must start at the same y "
                       f"(spread={spread} ys={ys})")

    def test_choice_taglines_aligned_1280(self):
        dlg = self._dialog(1280, 560)
        dlg._select_choice("both")
        self._show_page(dlg, dlg._choice_page)
        spread, ys = self._tagline_spread(dlg.choice_cards)
        self.assertLessEqual(
            spread, 1, f"choice taglines must start at the same y "
                       f"at 1280 (spread={spread} ys={ys})")

    def test_profile_taglines_aligned_1280(self):
        dlg = self._dialog(1280, 560)
        if not dlg._detour:
            dlg._toggle_advanced()
        self._show_page(dlg, dlg._profile_page)
        spread, ys = self._tagline_spread(dlg.profile_cards)
        self.assertLessEqual(
            spread, 1, f"profile taglines must start at the same y "
                       f"at 1280 (spread={spread} ys={ys})")

    def test_blank_pill_same_height_as_real_pill(self):
        """The alignment mechanism: the blank pill on the
        unrecommended cards is the same height as the RECOMMENDED
        pill, so the rows below start together. It carries no
        text and draws nothing (transparent fill + transparent
        2px border — QLabel is a QFrame subclass, so the card's
        QFrame border rule would otherwise draw an empty box)."""
        dlg = self._dialog(780, 560)
        dlg._select_choice("both")
        self._show_page(dlg, dlg._choice_page)
        real = None
        blanks = []
        for key, card in dlg.choice_cards.items():
            for w in card.findChildren(_QLabel):
                if w.text() == "RECOMMENDED":
                    real = w
                elif w.text() == "":
                    blanks.append(w)
        self.assertIsNotNone(real, "the recommended pill must exist")
        self.assertEqual(len(blanks), 2,
                         "the two unrecommended cards must each carry "
                         "one blank pill")
        for blank in blanks:
            self.assertEqual(blank.height(), real.height(),
                             "the blank pill must be the same height "
                             "as the real pill")
            self.assertIn("transparent", blank.styleSheet(),
                          "the blank pill must draw nothing")

    def test_choice_page_not_taller(self):
        dlg = self._dialog(780, 560)
        dlg._select_choice("both")
        self._show_page(dlg, dlg._choice_page)
        self.assertLessEqual(
            dlg._choice_page.sizeHint().height(), 530,
            "the pill rows must be offset by the tightening "
            "(baseline 523)")

    def test_profile_page_not_taller(self):
        dlg = self._dialog(780, 560)
        if not dlg._detour:
            dlg._toggle_advanced()
        self._show_page(dlg, dlg._profile_page)
        self.assertLessEqual(
            dlg._profile_page.sizeHint().height(), 425,
            "the profile page must not grow (baseline 425)")

    def test_perm_body_present_and_title_unchanged(self):
        dlg = self._dialog(780, 560)
        self._show_page(dlg, dlg._permissions_page)
        texts = [w.text() for w in
                 dlg._permissions_page.findChildren(_QLabel)]
        self.assertIn(PERM_BODY, texts,
                      "the permissions page needs its one-line body")
        self.assertIn(PERM_TITLE, texts,
                      "the permissions title text must not change")

    def test_perm_buttons_equal_width(self):
        dlg = self._dialog(780, 560)
        self._show_page(dlg, dlg._permissions_page)
        widths = self._perm_button_widths(dlg)
        self.assertEqual(len(set(widths)), 1,
                         f"every row action must be the same width "
                         f"(widths={widths})")

    def test_perm_takeback_same_width(self):
        dlg = self._dialog(780, 560)
        self._show_page(dlg, dlg._permissions_page)
        dlg._record_walk_grant("microphone", "durable")
        dlg._refresh_permissions_page()
        self._pump()
        mic_btn = dlg._perm_rows["microphone"]["action"]
        self.assertEqual(mic_btn.text(), "Take back")
        widths = self._perm_button_widths(dlg)
        self.assertEqual(len(set(widths)), 1,
                         f"Take back must match the other actions "
                         f"(widths={widths})")

    def test_done_brain_line_emphasised(self):
        dlg = self._dialog(780, 560)
        dlg._select_choice("both")
        self._show_page(dlg, dlg._done_page)
        style = dlg.done_brain_line.styleSheet() or ""
        self.assertIn("#E6FBFF", style,
                      "the brain recap must read in the full text "
                      "colour, not muted")
        self.assertIn("600", style,
                      "the brain recap must be semibold")
        self.assertIn("She'll think on this PC for now",
                      dlg.done_brain_line.text(),
                      "the recap text itself must not change")
        self.assertEqual(dlg.done_mission_line.objectName(), "muted",
                         "the mission line stays muted — only the "
                         "recap is emphasised")

    def test_welcome_title_body_visible_780(self):
        dlg = self._dialog(780, 560)
        # Round-3 wizard expansion: the dialog opens on the
        # language page now — show welcome before measuring it.
        self._show_page(dlg, dlg._welcome_page)
        page = dlg._welcome_page
        self.assertIs(dlg.pages.currentWidget(), page)
        vp = dlg._pages_scroll.viewport()
        title = body = None
        for w in page.findChildren(_QLabel):
            if w.text().startswith("Meet "):
                title = w
            elif w.text().startswith("A calm voice"):
                body = w
        self.assertIsNotNone(title)
        self.assertIsNotNone(body)
        tb = title.mapTo(vp, _QPoint(0, title.height())).y()
        bb = body.mapTo(vp, _QPoint(0, body.height())).y()
        self.assertLessEqual(tb, vp.height(),
                             "the welcome title must sit in the viewport")
        self.assertLessEqual(bb, vp.height(),
                             "the welcome body must sit in the viewport")

    def test_welcome_title_body_visible_760(self):
        dlg = self._dialog(760, 560)
        # Round-3 wizard expansion: the dialog opens on the
        # language page now — show welcome before measuring it
        # (mapping a hidden page's labels reads off-viewport).
        self._show_page(dlg, dlg._welcome_page)
        page = dlg._welcome_page
        vp = dlg._pages_scroll.viewport()
        title = body = None
        for w in page.findChildren(_QLabel):
            if w.text().startswith("Meet "):
                title = w
            elif w.text().startswith("A calm voice"):
                body = w
        tb = title.mapTo(vp, _QPoint(0, title.height())).y()
        bb = body.mapTo(vp, _QPoint(0, body.height())).y()
        self.assertLessEqual(tb, vp.height(),
                             "the welcome title must sit in the viewport "
                             "at 760 wide")
        self.assertLessEqual(bb, vp.height(),
                             "the welcome body must sit in the viewport "
                             "at 760 wide")

    def test_choice_gate_intact(self):
        dlg = self._dialog(780, 560)
        self._show_page(dlg, dlg._choice_page)
        self.assertFalse(dlg.btn_next.isEnabled(),
                         "Continue must stay disabled until a card is "
                         "picked")

    def test_signin_page_present(self):
        # Round-3 OAuth build: the key page is gone — the wizard
        # offers "Continue with Google" instead. The polish pass
        # never touched the auth page; pin the sign-in page and
        # its primary button copy in its place.
        dlg = self._dialog(780, 560)
        self.assertIsNotNone(dlg._signin_page)
        self.assertIsNotNone(getattr(dlg, "ob_btn_signin", None))
        self.assertEqual(dlg.ob_btn_signin.text(),
                         "Continue with Google")

    def test_dialog_size_unchanged(self):
        dlg = self._dialog(780, 560)
        self.assertEqual((dlg.width(), dlg.height()), (780, 560),
                         "the dialog size must not change")
        dlg2 = self._dialog(760, 560)
        self.assertEqual((dlg2.width(), dlg2.height()), (760, 560),
                         "the dialog size must not change")


if __name__ == "__main__":
    unittest.main()

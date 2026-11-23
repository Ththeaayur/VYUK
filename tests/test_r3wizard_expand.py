"""First-run wizard expansion (round-3, post-OAuth).

The wizard opens on the language page and the rest of the flow
follows the pick; voice + tool-profile + name + theme + Gmail/
Calendar + home-devices + guided first try join the main flow;
skip-everything leaves sensible defaults plus a "finish setup"
card on Home; a self-test failure pins a fix-it card to Home.
What this suite proves:

* Wizard chrome: wizard_strings NAV + SAMPLE_SENTENCE cover all
  9 home languages, every key present, English fallback on any
  miss (the non-English strings are first-pass copy, not
  native-verified — see the report).
* Language first: the first applicable page is the language
  page; picking Hindi sets preferred_language + kokoro_voice
  (hm_omega) in-session, localizes the nav, and the sample
  sentence follows. Voice + profile apply without the detour;
  Advanced setup is hidden.
* Name + theme: the name page stages the name until Finish,
  which persists USER_NAME + config.user_name; the Done greeting
  reads it in the picked language. The theme picker shows all
  11 built-in themes; a Sunset pick persists the "theme"
  QSettings key at Finish. Skip records neither.
* Home devices: the ask copy exists, the page is in the main
  flow, and Yes grants through the Settings-only path.
* Gmail/Calendar: the page is in the main flow; Skip is clean
  (no token, honest status); Connect with no client codes
  returns _NO_CODES at once — no browser opens.
* Guided first try: setting a reminder persists it through the
  scheduler and the Done page names what was just set.
* Chrome toggle: the toggle block shows up front when the
  machine's path is toggle_or_restart, and stays hidden for
  attached/direct.
* Sign-in page: the middle carries the honest "What this
  unlocks" list.
* Self-test: the page's on_done hook receives the full results;
  the wizard records failing check names to
  firstrun/selftest_faults and clears them on an all-pass run;
  the continue-anyway line shows only while a run has faults.
* setup_incomplete: True only for onboarded-without-a-brain-
  choice (dict seam + live QSettings).
* Choice-only: the brain-choice dialog skips every new page.
* Home cards (real MainWindow): the finish-setup card shows
  only while setup is incomplete; the fix-it card shows only
  while faults are pinned, names them, and its Check-again
  finish clears the pin on a pass. The Home greeting uses the
  name when set, plain otherwise.
* Morning briefing: the header greets by name when set.
* Pins: ConfigSpec carries user_name (USER_NAME); the
  QSettings table documents firstrun/selftest_faults.

Discipline (mirrors test_r3oauth_build): the GUI reads
permission_grants / config through the ``voice_assistant.core``
alias, while the flat test world imports ``core.*`` — wizard
tests therefore drive the ALIASED modules and re-register the
alias before each test (WindowBase teardown wipes it). The
grants + scheduler stores live in data/ (restored per test by
_DataGuard); first-run progress, the brain choice, the theme
and the self-test pin are snapshotted per test by _WizardGuard;
.env + the staged config fields by _EnvGuard. Qt tests skip
gracefully on an interpreter without PySide6.
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

try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

try:
    from PySide6.QtWidgets import QApplication as _QA
    from PySide6.QtCore import QSettings as _QSettings
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = None
    _QSettings = None
    _QT = False

try:
    from tests.test_chat_ui import WindowBase as _WindowBase
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _WindowBase = None

_APP = None
_VA_SNAPSHOT = None
_VA_PKG = None
_VA_CORE = None
_VA_MODS = {}
OnboardingDialog = None
config = None
permission_grants = None
scheduler = None
wizard_strings = None
first_run_asks = None


def _ensure_alias():
    """Re-register the voice_assistant alias in sys.modules.

    WindowBase._install_shim (test_chat_ui) replaces the alias
    packages and its _teardown_shim pops them — and unittest
    loads test classes alphabetically, so a WindowBase class
    running before a dialog class wipes the alias setUpModule
    installed. Re-registering the ORIGINAL package/module
    objects (same identities, not fresh ones) before each
    wizard test restores the alias exactly. No-op when intact.
    Every captured submodule is restored, not just the
    packages: a wiped submodule would otherwise re-import
    FRESH on next access — and a stateful one (the scheduler
    singleton) would come back empty, so a reminder the dialog
    just set would be invisible to the test reading it."""
    if _VA_PKG is not None and "voice_assistant" not in sys.modules:
        sys.modules["voice_assistant"] = _VA_PKG
    if _VA_CORE is not None and "voice_assistant.core" not in sys.modules:
        sys.modules["voice_assistant.core"] = _VA_CORE
    for name, mod in _VA_MODS.items():
        if mod is not None and name not in sys.modules:
            sys.modules[name] = mod


STAGES = ("interaction", "brain", "permissions", "first_mission")
LANGUAGES = ("en-us", "en-gb", "hi", "ja", "cmn", "es", "fr-fr",
             "it", "pt-br")
CHROME_KEYS = ("continue", "back", "skip", "finish", "not_now",
               "advanced", "stage_interaction", "stage_brain",
               "stage_permissions", "stage_first_mission", "hello",
               "sample")


def setUpModule():
    """Mirror test_r3oauth_build's voice_assistant.* alias."""
    global _APP, _VA_SNAPSHOT, _QT
    global _VA_PKG, _VA_CORE, _VA_MODS
    global OnboardingDialog, config
    global permission_grants, scheduler, wizard_strings, first_run_asks
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
    _VA_PKG = pkg
    _VA_CORE = core
    try:
        from gui.onboarding import (  # noqa: E402
            OnboardingDialog as _OD)
        from voice_assistant.core.gui_facade import (  # noqa: E402
            config as _cfg,
            permission_grants as _pg,
            scheduler as _sched,
            wizard_strings as _ws,
            first_run_asks as _fra)
        OnboardingDialog = _OD
        config = _cfg
        permission_grants = _pg
        scheduler = _sched
        wizard_strings = _ws
        first_run_asks = _fra
        # Capture every aliased submodule the dialog's lazy
        # imports may touch, so _ensure_alias restores the same
        # identities (see its docstring on the scheduler trap).
        _VA_MODS = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
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


def _pump_app(app, seconds=0.2):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


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
        self._dirs = {p for p in (ROOT / "data").rglob("*")
                      if p.is_dir()}
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
        for d in sorted(self._dirs, key=lambda p: len(p.parts)):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        for dirpath in sorted((ROOT / "data").rglob("*"), reverse=True):
            if dirpath.is_dir() and dirpath not in self._dirs:
                try:
                    dirpath.rmdir()
                except OSError:
                    pass
        return False


class _WizardGuard:
    """Snapshot + reset the wizard's QSettings keys and the
    staged config fields, per test. A dialog that walks marks
    stages done and stages picks in-process, so a second dialog
    in this process would otherwise resume mid-flow with the
    first test's picks."""

    _QKEYS = ("theme", "onboarded", "setup/brain_choice",
              "firstrun/selftest_faults")
    _CFG_ATTRS = ("preferred_language", "kokoro_voice", "user_name",
                  "tool_profile")

    def __init__(self):
        self._saved_q = {}
        self._saved_cfg = {}

    def __enter__(self):
        s = _QSettings("NovaAI", "Nova")
        for key in self._QKEYS:
            self._saved_q[key] = s.value(key)
            s.remove(key)
        for stage in STAGES:
            key = f"firstrun/stage/{stage}"
            self._saved_q[key] = s.value(key)
            s.remove(key)
        s.sync()
        for attr in self._CFG_ATTRS:
            self._saved_cfg[attr] = getattr(config, attr, None)
        return self

    def __exit__(self, *exc):
        s = _QSettings("NovaAI", "Nova")
        for key, value in self._saved_q.items():
            if value is None:
                s.remove(key)
            else:
                s.setValue(key, value)
        s.sync()
        for attr, value in self._saved_cfg.items():
            try:
                setattr(config, attr, value)
            except Exception:  # noqa: BLE001 — best-effort restore
                pass
        return False


class _EnvGuard:
    """Snapshot + restore .env (the Finish path writes it)."""

    def __enter__(self):
        self._path = ROOT / ".env"
        self._had = self._path.exists()
        self._saved = self._path.read_bytes() if self._had else None
        self._tmp = ROOT / ".env.tmp"
        self._had_tmp = self._tmp.exists()
        self._saved_tmp = (self._tmp.read_bytes()
                           if self._had_tmp else None)
        return self

    def __exit__(self, *exc):
        if self._had:
            self._path.write_bytes(self._saved)
        else:
            try:
                self._path.unlink()
            except OSError:
                pass
        if self._had_tmp:
            self._tmp.write_bytes(self._saved_tmp)
        else:
            try:
                self._tmp.unlink()
            except OSError:
                pass
        return False


def _needs_qt(test_fn):
    """Skip a Qt test on an interpreter without PySide6."""
    import functools

    @functools.wraps(test_fn)
    def wrapper(self, *args, **kwargs):
        if not _QT or OnboardingDialog is None:
            self.skipTest("no Qt in this interpreter")
        return test_fn(self, *args, **kwargs)

    return wrapper


class _DialogMixin:
    """One fresh dialog per test, with all guards in place."""

    def _make_dialog(self, choice_only=False, initial_choice=None):
        _ensure_alias()
        self._data = _DataGuard()
        self._data.__enter__()
        self._wiz = _WizardGuard()
        self._wiz.__enter__()
        self._env = _EnvGuard()
        self._env.__enter__()
        kwargs = {"choice_only": choice_only}
        if initial_choice is not None:
            kwargs["initial_choice"] = initial_choice
        dlg = OnboardingDialog(**kwargs)
        dlg.show()
        _pump_app(_QA.instance(), 0.2)
        self.addCleanup(self._close_dialog, dlg)
        return dlg

    def _close_dialog(self, dlg):
        try:
            dlg.close()
        except Exception:  # noqa: BLE001 — close is best-effort
            pass
        try:
            self._env.__exit__()
            self._wiz.__exit__()
            self._data.__exit__()
        except Exception:  # noqa: BLE001 — guards restore best-effort
            pass

    def _finish(self, dlg, brain="local"):
        """Walk the dialog to Done and press Finish."""
        dlg.brain_choice = brain
        dlg._select_choice(brain)
        dlg.pages.setCurrentWidget(dlg._done_page)
        dlg._sync_nav()
        dlg._next()
        _pump_app(_QA.instance(), 0.2)


# ---------------------------------------------------------------- Qt-free
class WizardStringsTests(unittest.TestCase):
    """wizard_strings covers all 9 home languages, English fallback."""

    def test_nav_covers_all_languages(self):
        import core.wizard_strings as ws

        for code in LANGUAGES:
            self.assertIn(code, ws.NAV, code)
            for key in CHROME_KEYS:
                self.assertIn(key, ws.NAV[code], (code, key))
                self.assertTrue(str(ws.NAV[code][key]).strip(),
                                (code, key))

    def test_sample_sentence_covers_all_languages(self):
        import core.wizard_strings as ws

        for code in LANGUAGES:
            self.assertIn(code, ws.SAMPLE_SENTENCE, code)
            self.assertIn("VYUK", ws.SAMPLE_SENTENCE[code], code)
        self.assertNotEqual(ws.SAMPLE_SENTENCE["hi"],
                            ws.SAMPLE_SENTENCE["en-gb"])

    def test_chrome_falls_back_to_english(self):
        import core.wizard_strings as ws

        self.assertEqual(ws.chrome("xx", "continue"), "Continue")
        self.assertEqual(ws.chrome(None, "hello"), "Hello")
        self.assertEqual(ws.chrome("hi", "no_such_key"), "no_such_key")
        self.assertEqual(ws.hello("hi"), "नमस्ते")
        self.assertEqual(ws.sample_sentence("xx"),
                         ws.sample_sentence("en-gb"))


class ConfigPinsTests(unittest.TestCase):
    """The new ConfigSpec field and QSettings table entry."""

    def test_user_name_field_is_env_backed(self):
        import dataclasses

        import core.config_spec as cs

        fields = {f.name: f for f in dataclasses.fields(cs.ConfigSpec)}
        self.assertIn("user_name", fields)
        self.assertEqual(fields["user_name"].metadata.get("env"),
                         "USER_NAME")
        self.assertEqual(fields["user_name"].default, "")

    def test_selftest_faults_documented(self):
        import core.config_spec as cs

        row = next((e for e in cs.QSETTINGS_FIELDS
                    if e[1] == "firstrun/selftest_faults"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "firstrun_selftest_faults")
        self.assertIs(row[2], str)
        self.assertEqual(row[3], "")

    def test_morning_briefing_greets_by_name(self):
        import datetime

        import core.morning_briefing as mb

        now = datetime.datetime.now().astimezone()
        with_name = mb.render_morning_briefing(now, [], {},
                                                name="Aayur")
        self.assertIn("Aayur", with_name)
        without = mb.render_morning_briefing(now, [], {})
        self.assertNotIn("Aayur", without)


# ---------------------------------------------------------------- Qt
class LanguageFirstTests(_DialogMixin, unittest.TestCase):
    """Language first, and the rest of the wizard follows it."""

    @_needs_qt
    def test_language_is_first_applicable_page(self):
        dlg = self._make_dialog()
        self.assertIs(dlg._applicable_pages()[0], dlg._language_page)

    @_needs_qt
    def test_language_pick_drives_voice_session_only(self):
        dlg = self._make_dialog()
        hi = [rb for rb in dlg.ob_language_buttons
              if rb.property("language_code") == "hi"]
        self.assertEqual(len(hi), 1)
        hi[0].click()
        _pump_app(_QA.instance(), 0.2)
        self.assertEqual(config.preferred_language, "hi")
        self.assertEqual(config.kokoro_voice, "hm_omega")

    @_needs_qt
    def test_nav_follows_the_pick(self):
        dlg = self._make_dialog()
        hi = [rb for rb in dlg.ob_language_buttons
              if rb.property("language_code") == "hi"]
        hi[0].click()
        _pump_app(_QA.instance(), 0.2)
        self.assertEqual(dlg.btn_next.text(),
                         wizard_strings.NAV["hi"]["continue"])
        self.assertEqual(dlg.btn_back.text(),
                         wizard_strings.NAV["hi"]["back"])

    @_needs_qt
    def test_voice_and_profile_are_main_flow(self):
        dlg = self._make_dialog()
        self.assertTrue(dlg._page_applies(dlg._voice_page))
        self.assertTrue(dlg._page_applies(dlg._profile_page))
        self.assertFalse(dlg.btn_advanced.isVisible())

    @_needs_qt
    def test_sample_sentence_follows_the_pick(self):
        dlg = self._make_dialog()
        hi = [rb for rb in dlg.ob_language_buttons
              if rb.property("language_code") == "hi"]
        hi[0].click()
        _pump_app(_QA.instance(), 0.2)
        sentence = wizard_strings.sample_sentence(
            config.preferred_language)
        self.assertIn("VYUK", sentence)
        self.assertNotEqual(
            sentence,
            wizard_strings.sample_sentence("en-gb"))


class NameThemeFirstTryTests(_DialogMixin, unittest.TestCase):
    """Name, theme, guided first try — staged, then persisted."""

    @_needs_qt
    def test_name_page_in_main_flow(self):
        dlg = self._make_dialog()
        self.assertTrue(hasattr(dlg, "_name_page"))
        self.assertTrue(dlg._page_applies(dlg._name_page))

    @_needs_qt
    def test_finish_persists_name(self):
        dlg = self._make_dialog()
        dlg._name_edit.setText("Aayur")
        _pump_app(_QA.instance(), 0.2)
        self.assertEqual(dlg._pending_name, "Aayur")
        self._finish(dlg)
        self.assertEqual(config.user_name, "Aayur")
        self.assertEqual(
            _QSettings("NovaAI", "Nova").value("setup/brain_choice"),
            "local")

    @_needs_qt
    def test_blank_name_persists_nothing(self):
        dlg = self._make_dialog()
        self.assertEqual(dlg._pending_name, "")
        self._finish(dlg)
        self.assertEqual(config.user_name, "")

    @_needs_qt
    def test_theme_picker_has_all_twelve(self):
        dlg = self._make_dialog()
        from gui.style import THEMES

        self.assertEqual(len(dlg.ob_theme_buttons), len(THEMES))
        # Stage 0 features batch 5: 11 -> 12 (Light added).
        self.assertEqual(len(THEMES), 12)
        names = {str(b.property("theme_name"))
                 for b in dlg.ob_theme_buttons}
        self.assertEqual(names, set(THEMES))

    @_needs_qt
    def test_theme_pick_persists_at_finish(self):
        dlg = self._make_dialog()
        sunset = [b for b in dlg.ob_theme_buttons
                  if str(b.property("theme_name")) == "Sunset"]
        self.assertEqual(len(sunset), 1)
        sunset[0].click()
        _pump_app(_QA.instance(), 0.2)
        self.assertEqual(dlg.theme_choice, "Sunset")
        self._finish(dlg)
        self.assertEqual(
            _QSettings("NovaAI", "Nova").value("theme"), "Sunset")

    @_needs_qt
    def test_first_try_sets_a_real_reminder(self):
        dlg = self._make_dialog()
        page = dlg._first_try_page
        page._first_try_edit.setText("water the plants")
        page._first_try_when.setCurrentIndex(0)
        page._first_try_set_btn.click()
        _pump_app(_QA.instance(), 0.2)
        found = [r for r in scheduler.get_all_reminders()
                 if "water the plants" in str(r.get("label", ""))]
        self.assertTrue(found)
        for r in found:
            try:
                scheduler.cancel_reminder(r["label"])
            except Exception:  # noqa: BLE001 — cleanup best-effort
                pass

    @_needs_qt
    def test_done_names_the_first_try_and_greets(self):
        dlg = self._make_dialog()
        dlg._name_edit.setText("Aayur")
        hi = [rb for rb in dlg.ob_language_buttons
              if rb.property("language_code") == "hi"]
        hi[0].click()
        page = dlg._first_try_page
        page._first_try_edit.setText("water the plants")
        page._first_try_when.setCurrentIndex(0)
        page._first_try_set_btn.click()
        dlg.pages.setCurrentWidget(dlg._done_page)
        dlg._sync_nav()
        _pump_app(_QA.instance(), 0.2)
        self.assertIn("water the plants", dlg.done_mission_line.text())
        self.assertIn("नमस्ते", dlg.done_greeting_line.text())
        self.assertIn("Aayur", dlg.done_greeting_line.text())

    @_needs_qt
    def test_skip_records_no_name_or_theme(self):
        dlg = self._make_dialog()
        dlg._name_edit.setText("Aayur")
        sunset = [b for b in dlg.ob_theme_buttons
                  if str(b.property("theme_name")) == "Sunset"]
        sunset[0].click()
        dlg.reject()
        _pump_app(_QA.instance(), 0.2)
        self.assertEqual(config.user_name, "")
        self.assertIsNone(_QSettings("NovaAI", "Nova").value("theme"))
        self.assertIsNone(
            _QSettings("NovaAI", "Nova").value("setup/brain_choice"))


class PermissionsGoogleTests(_DialogMixin, unittest.TestCase):
    """Home-devices ask, Gmail/Calendar offer, Chrome toggle."""

    @_needs_qt
    def test_home_devices_copy_and_page(self):
        dlg = self._make_dialog()
        self.assertIn("home_devices", first_run_asks.ASK_COPY)
        self.assertTrue(hasattr(dlg, "_home_devices_page"))
        self.assertTrue(dlg._page_applies(dlg._home_devices_page))

    @_needs_qt
    def test_home_devices_yes_grants(self):
        dlg = self._make_dialog()
        page = dlg._home_devices_page
        page._yes_button.click()
        _pump_app(_QA.instance(), 0.2)
        self.assertEqual(
            permission_grants.get_state("home_devices"), "granted")

    @_needs_qt
    def test_google_page_in_main_flow(self):
        dlg = self._make_dialog()
        self.assertTrue(hasattr(dlg, "_google_page"))
        self.assertTrue(dlg._page_applies(dlg._google_page))

    @_needs_qt
    def test_google_skip_is_clean(self):
        dlg = self._make_dialog()
        from voice_assistant.core import (  # noqa: E402
            google_services_token as _gst)

        page = dlg._google_page
        page._google_skip_btn.click()
        _pump_app(_QA.instance(), 0.2)
        self.assertIn("Skipped", page._google_status.text())
        self.assertFalse(_gst.is_connected())

    @_needs_qt
    def test_google_connect_with_no_codes_is_honest(self):
        dlg = self._make_dialog()
        page = dlg._google_page
        page._google_connect_btn.click()
        for _ in range(10):
            _pump_app(_QA.instance(), 0.3)
            if "client" in page._google_status.text().lower():
                break
        self.assertIn("client", page._google_status.text().lower())
        self.assertIn("Settings", page._google_status.text())

    @_needs_qt
    def test_chrome_toggle_upfront_on_toggle_path(self):
        dlg = self._make_dialog()
        from voice_assistant.core.gui_facade import (  # noqa: E402
            browser_enable as _be)

        orig = _be.browser_connect_path
        _be.browser_connect_path = lambda: {
            "path": "toggle_or_restart",
            "line": "Chrome is open without the switch."}
        try:
            page = dlg._browser_page
            dlg.pages.setCurrentWidget(page)
            _pump_app(_QA.instance(), 0.3)
            self.assertFalse(page.widget.toggle_block.isHidden())
        finally:
            _be.browser_connect_path = orig

    @_needs_qt
    def test_chrome_toggle_hidden_when_attached(self):
        dlg = self._make_dialog()
        from voice_assistant.core.gui_facade import (  # noqa: E402
            browser_enable as _be)

        orig = _be.browser_connect_path
        _be.browser_connect_path = lambda: {
            "path": "attached", "line": "Attached to your Chrome."}
        try:
            page = dlg._browser_page
            dlg.pages.setCurrentWidget(page)
            _pump_app(_QA.instance(), 0.3)
            self.assertTrue(page.widget.toggle_block.isHidden())
        finally:
            _be.browser_connect_path = orig

    @_needs_qt
    def test_signin_page_middle_is_filled(self):
        dlg = self._make_dialog()
        from PySide6.QtWidgets import QLabel

        labels = dlg._signin_page.findChildren(QLabel)
        texts = " ".join(lbl.text() for lbl in labels)
        self.assertIn("What this unlocks", texts)


class SelfTestHomeTests(_DialogMixin, unittest.TestCase):
    """Self-test faults pin to Home; the predicate is exact."""

    @_needs_qt
    def test_selftest_page_reports_full_results(self):
        from PySide6.QtWidgets import QLabel
        from gui.selftest_page import _SelfTestPage

        seen = {}
        page = _SelfTestPage(
            QLabel("Self-test"),
            check_fn=lambda: [("Mic", False, "no mic found")],
            on_done=lambda results: seen.update({"results": results}))
        page.run_selftest()
        for _ in range(50):
            _pump_app(_QA.instance(), 0.1)
            if "results" in seen:
                break
        self.assertEqual(seen.get("results"),
                         [("Mic", False, "no mic found")])

    @_needs_qt
    def test_selftest_faults_recorded_and_cleared(self):
        dlg = self._make_dialog()
        settings = _QSettings("NovaAI", "Nova")
        dlg._on_selftest_done([("Mic", False, "no mic found"),
                               ("Disk", True, "")])
        self.assertEqual(settings.value("firstrun/selftest_faults"),
                         "Mic")
        dlg._on_selftest_done([("Mic", True, ""), ("Disk", True, "")])
        self.assertEqual(settings.value("firstrun/selftest_faults"), "")

    @_needs_qt
    def test_continue_anyway_line_shows_only_on_faults(self):
        from gui.selftest_page import _SelfTestPage

        dlg = self._make_dialog()
        page = _SelfTestPage(
            dlg._title_block("Self-test", "x"),
            check_fn=lambda: [("Mic", False, "no mic found")])
        page.show()
        page.run_selftest()
        for _ in range(50):
            _pump_app(_QA.instance(), 0.1)
            if not page._continue_note.isHidden():
                break
        self.assertFalse(page._continue_note.isHidden())
        page._paint([("Mic", True, "")])
        self.assertTrue(page._continue_note.isHidden())

    @_needs_qt
    def test_setup_incomplete_predicate(self):
        from gui.firstrun_g import setup_incomplete

        self.assertTrue(setup_incomplete({"onboarded": True}))
        self.assertFalse(setup_incomplete(
            {"onboarded": True, "setup/brain_choice": "local"}))
        self.assertFalse(setup_incomplete({}))
        self.assertFalse(setup_incomplete({"onboarded": False}))

    @_needs_qt
    def test_choice_card_click_emits(self):
        from gui.onboarding_widgets import _ChoiceCard

        card = _ChoiceCard()
        seen = []
        card.clicked.connect(lambda: seen.append(True))
        card.click()
        self.assertEqual(seen, [True])


class ChoiceOnlyTests(_DialogMixin, unittest.TestCase):
    """The brain-choice dialog skips every new first-run page."""

    @_needs_qt
    def test_new_pages_skip_in_choice_only(self):
        dlg = self._make_dialog(choice_only=True,
                                initial_choice="local")
        for attr in ("_name_page", "_theme_page", "_google_page",
                     "_home_devices_page", "_first_try_page",
                     "_voice_page", "_language_page", "_profile_page",
                     "_browser_page", "_camera_page", "_screen_page",
                     "_files_page", "_permissions_page",
                     "_selftest_page"):
            page = getattr(dlg, attr, None)
            self.assertIsNotNone(page, attr)
            self.assertFalse(dlg._page_applies(page), attr)
        self.assertTrue(dlg._page_applies(dlg._choice_page))


class HomeCardsTests(unittest.TestCase):
    """The two conditional Home cards on the real MainWindow."""

    def setUp(self):
        if _WindowBase is None or not _QT:
            self.skipTest("no Qt in this interpreter")
        _ensure_alias()
        import types as _types

        _pkg = _types.ModuleType("voice_assistant")
        _pkg.__path__ = [str(ROOT)]
        sys.modules["voice_assistant"] = _pkg
        _core_pkg = _types.ModuleType("voice_assistant.core")
        _core_pkg.__path__ = [str(ROOT / "core")]
        sys.modules["voice_assistant.core"] = _core_pkg
        from voice_assistant.core.gui_facade import w9_wiring  # noqa: F401

        self._data = _DataGuard()
        self._data.__enter__()
        self._env = _EnvGuard()
        self._env.__enter__()
        self._wiz = _WizardGuard()
        self._wiz.__enter__()
        self._base = _WindowBase()
        self._base.setUp()
        self.window = self._base.window
        self.app = self._base.app
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — overlay best-effort
            pass
        _pump_app(self.app, 0.4)
        self.window.resize(1280, 750)
        self.window.show()
        try:
            self.window._show_page(0)
        except Exception:  # noqa: BLE001 — page switch best-effort
            pass
        _pump_app(self.app, 0.6)

    def tearDown(self):
        try:
            self._base.tearDown()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass
        try:
            self._wiz.__exit__()
            self._env.__exit__()
            self._data.__exit__()
        except Exception:  # noqa: BLE001 — guards restore best-effort
            pass

    def _refresh(self):
        self.window._refresh_setup_cards()
        _pump_app(self.app, 0.2)

    def test_setup_card_shows_when_incomplete(self):
        s = _QSettings("NovaAI", "Nova")
        s.setValue("onboarded", True)
        s.remove("setup/brain_choice")
        s.sync()
        self._refresh()
        self.assertFalse(self.window.setup_card.isHidden())
        self.assertTrue(self.window.setup_card.isVisible())

    def test_setup_card_hidden_when_complete(self):
        # Loophole batch 7 (#18): "complete" now means a brain
        # choice stored AND no piece still open — declining every
        # piece resolves the card, exactly as finishing every
        # piece would. A brain choice alone no longer hides it.
        s = _QSettings("NovaAI", "Nova")
        s.setValue("onboarded", True)
        s.setValue("setup/brain_choice", "local")
        s.setValue("firstrun/pieces_declined",
                   "google;gmail;calendar;browser;mic")
        s.setValue("firstrun/setup_card_snoozed_until", "")
        s.sync()
        self._refresh()
        self.assertTrue(self.window.setup_card.isHidden())

    def test_setup_card_shows_when_brain_chosen_but_pieces_open(self):
        # Loophole batch 7 (#18): the fix itself — a brain choice
        # stored with pieces still open keeps the card up (the
        # old binary rule hid it at Finish even with pieces
        # skipped, killing the only path back to them).
        s = _QSettings("NovaAI", "Nova")
        s.setValue("onboarded", True)
        s.setValue("setup/brain_choice", "local")
        s.setValue("firstrun/pieces_declined", "")
        s.setValue("firstrun/setup_card_snoozed_until", "")
        s.sync()
        self._refresh()
        self.assertFalse(self.window.setup_card.isHidden())
        self.assertTrue(self.window.setup_card.isVisible())

    def test_selftest_card_shows_faults_and_names_them(self):
        s = _QSettings("NovaAI", "Nova")
        s.setValue("firstrun/selftest_faults", "Mic")
        s.sync()
        self._refresh()
        self.assertFalse(self.window.selftest_card.isHidden())
        self.assertIn("Mic",
                      self.window._selftest_faults_label.text())

    def test_selftest_card_hidden_when_clear(self):
        s = _QSettings("NovaAI", "Nova")
        s.setValue("firstrun/selftest_faults", "")
        s.sync()
        self._refresh()
        self.assertTrue(self.window.selftest_card.isHidden())

    def test_check_again_finish_clears_the_pin(self):
        s = _QSettings("NovaAI", "Nova")
        s.setValue("firstrun/selftest_faults", "Mic")
        s.sync()
        self._refresh()
        self.assertFalse(self.window.selftest_card.isHidden())
        self.window._home_selftest_failed = []
        self.window._finish_home_selftest()
        _pump_app(self.app, 0.2)
        self.assertEqual(s.value("firstrun/selftest_faults"), "")
        self.assertTrue(self.window.selftest_card.isHidden())

    def test_greeting_uses_the_name_when_set(self):
        from gui.window_shell_onboarding import ShellOnboardingMixin

        config.user_name = "Aayur"
        try:
            greeted = ShellOnboardingMixin._greeting(None)
        finally:
            config.user_name = ""
        self.assertIn("Aayur", greeted)
        plain = ShellOnboardingMixin._greeting(None)
        self.assertNotIn(",", plain)
        self.assertIn("Good", plain)


if __name__ == "__main__":
    unittest.main()

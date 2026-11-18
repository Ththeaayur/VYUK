"""UI settings feedback + scroll standardisation slice (2026-10-30) —
the LAST UI slice (after breakpoints, a11y, interaction, polish).

FEEDBACK AUDIT (every settings control, classified from CODE, never
from assumption — the table the slice implements):

- Immediate (live effect now / next utterance / next turn / next
  listen / next event / next transcription): theme, orb style, font
  scale, density, motion, glass, particles (live apply); sounds,
  volume, earcon family, cue toggles (earcons live); mic gain/gate/
  barge/sustain/wake-sens (config + audio_manager live, wake sens
  via set_sensitivity); whisper hours/times/factor (read per speech
  enqueue, core/voice/local_client.py); Kokoro voice/blend/ratio/speed,
  local TTS voice (read per synthesis); language (persisted by
  set_preferred_language, read per turn by reply_language_directive);
  transcription profile (resolved per transcribe call); listen
  timeout (read in the turn loop); PTT mode (read per PTT press);
  media pause (read at the next media event); watch screen (session
  flag + chat announcement); continuous display (gate read live —
  wording never claims capture starts); suggestions (live via
  configure_suggestions); watchers master + kinds (kinds read live
  at each watcher event); presence (auto-reconnect read per drop,
  mini orb visible live, summon hotkey daemon hook live); browser
  real-Chrome + port (read live on every attach attempt); voice
  bridge (label sync); pronunciations, mic test/calibrate, key
  saves, audit/allowlist, theme packs, voice preview, updates,
  export/import, w9 reset, w10 models (own labels/views/buttons).
- Next connect (saved; the live session is unaffected until you
  reconnect): brain engine (card hint says so), cloud provider,
  Gemini/Groq keys ("ready to Connect"), local backend, Ollama
  model (read at Ollama session creation), whisper model (read at
  local session build), brain size ("downloads on next local
  connect"), tool profile ("next connect / brain turn"), Voice
  combo (live session reads config.voice_name at creation).
- Next launch (saved; governs future launches, no restart needed
  now): connect_on_launch — _init_presence reads the key and
  schedules _connect_on_launch_once 800 ms after start (guarded:
  already connected / worker alive), which calls
  _start_connection() with auto provenance. Its status line
  already reports the effect, so it needs no new line.
- Restart-required — EXACTLY ONE by code evidence: the extra wake
  phrase. WakeWordDetector is a module-level singleton
  (core/voice/wake_word.py) that reads the alt phrase once, in __init__.
  It gets a PERSISTENT label (the placeholder disappears while
  typing) plus a save-confirmation line.
- WIRED in the PF-18 widget batch (INTENTIONAL transition, S4
  pin retired — see test_permissions_apply_is_wired):
  PermissionsWidget Apply now writes real grants through the
  Settings-only store API and reports per-switch results; the
  "not wired yet" copy is gone from the widget.
  CORRECTION, owned: this slice's first draft wrongly called
  connect_on_launch not-wired (its _init_presence reader was
  missed); the reader was found by the suite's own scan, the
  false claim was reverted, and the wiring above is now pinned.
- Placebo FIXED in this slice (separately pinned): the Settings
  Voice pick was session-only (the only .env write path for
  VOICE_NAME was the voice-command path) — it now persists like
  the other voice picks. The autostart toggle was connected
  TWICE (settings build + _init_presence_card), firing the
  SettingsMixin handler twice per toggle — the build-time connect
  is removed; _init_presence_card's states-first wiring stays.
- Undetermined at audit time, RESOLVED by reading further (not
  guessed): the Morning briefing toggle/time ARE wired — the
  toggle reports via the presence status label and _check_briefing
  (timer + startup singleShot) reads both keys live per check, so
  the toggle is immediate and the time change is next-check. The
  scheduler's retired add_daily_briefing was a SEPARATE home-button path,
  not the Settings toggle's mechanism; GAPS-26 merged them — the
  scheduler-side briefing is retired (its records are migrated to
  these presence keys at startup), so the toggle/time here ARE the
  single briefing mechanism now. Watcher kinds ARE wired (read live per watcher event).

SCROLL STANDARDISATION: the breakpoints slice's claim HOLDS for
the page structure — one outer QScrollArea (widgetResizable,
frameless Doctor-pattern), Advanced's more_container inside the
scroll content, search scrolls to top. What did NOT hold: TWO
nested scrollers lived inside the page content — HelpWidget's
results scroll and PrivacyDashboardWidget's facts scroll
(gui/pf_surfaces.py). Both are removed; their rows now flow into the
page scroll. Fixed-height QListWidget viewports (audit/allowlist
lists) are list viewports, not page scrollers, and stay. The
wheel filter for combos/sliders/spinboxes/edits/lists stays.

REGRESSION ARBITERS (this suite pins structure; the neighbours
prove behaviour): S4 structure via test_uis4, polish contrast +
tokens via test_uipolish, breakpoints via test_uibreakpoints.

Lab limits (field-owed): feedback + restart indicators seen by
his eye on real Windows; scrolling felt at narrow widths on his
machine; offscreen pins MECHANISM, never pixel renders (lab fonts
differ from Segoe UI).
"""

import ast
import os
import sys
import types
import unittest
from pathlib import Path

from core.config_spec import qsettings_set

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "gui"


def _source(name):
    return (GUI / name).read_text(encoding="utf-8")


def _func_source(path, func_name):
    """Source segment of one function def from a module file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == func_name:
            return ast.get_source_segment(
                path.read_text(encoding="utf-8"), node)
    raise AssertionError(f"{func_name} not found in {path.name}")


def _exec_func(path, func_name, globs, self_obj, *args):
    """Exec one real function body on a stub self (established
    pattern: no module import, no sys.modules pollution)."""
    ns = dict(globs)
    exec(compile(_func_source(path, func_name), path.name, "exec"), ns)
    return ns[func_name](self_obj, *args)


class _LabelStub:
    def __init__(self):
        self.text = None
        self.visible = False

    def setText(self, text):
        self.text = text

    def setVisible(self, visible):
        self.visible = visible


class _SettingsStub:
    def __init__(self):
        self.values = {}

    def setValue(self, key, value):
        self.values[key] = value

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def sync(self):
        pass

    def remove(self, key):
        self.values.pop(key, None)


class _Shell:
    """Minimal self for handler bodies: records feedback notes,
    persist calls, and status lines instead of touching Qt."""

    def __init__(self):
        self.settings = _SettingsStub()
        self.settings_feedback_label = _LabelStub()
        self.notes = []
        self.persisted = []
        self.status = None

    def _note_settings_feedback(self, text):
        self.notes.append(text)
        self.settings_feedback_label.setText(text)
        self.settings_feedback_label.setVisible(True)

    def _persist_config_setting(self, key, value):
        self.persisted.append((key, value))
        return True

    def _set_presence_status(self, text):
        self.status = text


class TestFeedbackMechanism(unittest.TestCase):
    def test_feedback_label_and_helper_exist(self):
        build = _func_source(GUI / "window_settings.py",
                             "_build_settings_page")
        self.assertIn("settings_feedback_label", build)
        self.assertIn("setVisible(False)", build)
        helper = _func_source(GUI / "window_settings.py",
                              "_note_settings_feedback")
        self.assertIn("settings_feedback_label", helper)
        self.assertIn("setText(text)", helper)

    def test_helper_sets_label_and_never_breaks(self):
        shell = _Shell()
        _exec_func(GUI / "window_settings.py",
                   "_note_settings_feedback", {}, shell, "hello")
        self.assertEqual(shell.settings_feedback_label.text, "hello")
        self.assertTrue(shell.settings_feedback_label.visible)
        # Missing label: guarded, never raises.
        bare = types.SimpleNamespace()
        _exec_func(GUI / "window_settings.py",
                   "_note_settings_feedback", {}, bare, "hello")

    def test_volume_readout_is_the_feedback(self):
        # INTENTIONAL (refactor P1 step 5): the voice section moved
        # verbatim to window_settings_voice.py as _build_voice_cards.
        build = _func_source(GUI / "window_settings_voice.py",
                             "_build_voice_cards")
        self.assertIn("lbl_volume", build)
        handler = _func_source(GUI / "window_settings_voice.py",
                               "_on_volume_changed")
        self.assertIn("lbl_volume", handler)
        self.assertIn('f"{value}%"', handler)


class TestAutostartDoubleConnect(unittest.TestCase):
    def test_exactly_one_autostart_connect(self):
        count = 0
        # GUI split #12 batch 1 INTENTIONAL: chk_autostart wiring lives in
        # _init_presence_card, moved verbatim to window_memory_presence.py.
        for name in ("window_settings.py", "window_memory_presence.py"):
            count += _source(name).count(
                "chk_autostart.toggled.connect")
        self.assertEqual(count, 1,
                         "the build-time duplicate connect is gone; "
                         "only _init_presence_card's wiring remains")

    def test_presence_card_states_first(self):
        """setChecked precedes connect in _init_presence_card, so
        building Settings never fires the handler (in particular,
        never rewrites the Windows autostart entry on open)."""
        fn = _func_source(GUI / "window_memory_presence.py",  # GUI split #12 batch 1 INTENTIONAL: _init_presence_card moved verbatim here.
                          "_init_presence_card")
        self.assertLess(fn.index("chk_autostart.setChecked"),
                        fn.index("chk_autostart.toggled.connect"))


class TestVoicePersistFix(unittest.TestCase):
    def test_voice_changed_persists_and_notes(self):
        fn = _func_source(GUI / "window_memory_web.py",  # GUI split #12 batch 1 INTENTIONAL: _on_voice_changed moved verbatim here.
                          "_on_voice_changed")
        self.assertIn('_persist_config_setting("VOICE_NAME"',
                      fn)
        self.assertIn("_note_settings_feedback", fn)
        config = types.SimpleNamespace(voice_name=None)
        shell = _Shell()
        _exec_func(GUI / "window_memory_web.py", "_on_voice_changed",  # GUI split #12 batch 1 INTENTIONAL: _on_voice_changed moved verbatim here.
                   {"config": config}, shell, "George")
        self.assertEqual(config.voice_name, "George")
        self.assertIn(("VOICE_NAME", "George"), shell.persisted)
        self.assertTrue(any("next time you connect" in n
                            for n in shell.notes))


class TestRestartIndicator(unittest.TestCase):
    def test_wake_phrase_persistent_restart_label(self):
        # INTENTIONAL (refactor P1 step 5): the voice section moved
        # verbatim to window_settings_voice.py as _build_voice_cards.
        build = _func_source(GUI / "window_settings_voice.py",
                             "_build_voice_cards")
        self.assertIn("Takes effect when VYUK next starts", build)
        handler = _func_source(GUI / "window_services_audio.py",  # GUI split #10 batch 2 INTENTIONAL: _on_wake_phrase_changed moved verbatim here.
                               "_on_wake_phrase_changed")
        self.assertIn("_note_settings_feedback", handler)
        self.assertIn("next starts", handler)

    def test_no_other_restart_claims(self):
        """The audit found exactly one restart-required setting.
        No other handler may claim a restart (a restart note
        anywhere else would be an unverified claim)."""
        for name, func in (
                ("window_services_audio.py", "_on_media_pause_toggled"),  # GUI split #10 batch 2 INTENTIONAL: handler moved verbatim here.
                # GUI split #4 INTENTIONAL: handler moved verbatim to
                # window_brain_voice.py. Assertion unchanged.
                ("window_brain_voice.py", "_on_tool_profile_picked"),
                ("window_memory_presence.py", "_on_briefing_time_changed")):  # GUI split #12 batch 1 INTENTIONAL: _on_briefing_time_changed moved verbatim here.
            fn = _func_source(GUI / name, func)
            self.assertNotIn("restart", fn.lower())


class TestConnectLaunchWired(unittest.TestCase):
    """CORRECTED audit finding (the slice's first draft wrongly
    called this not-wired — the reader was missed): _init_presence
    reads presence/connect_on_launch and, when true, schedules
    _connect_on_launch_once 800 ms after start; that method
    guards (already connected / worker alive) and calls
    _start_connection() directly with auto provenance. So this
    is NEXT-LAUNCH wired, and its status line ("will connect by
    itself at launch") is a true claim that already reports —
    it needs no new feedback line."""

    def test_launch_reader_schedules_auto_connect(self):
        fn = _func_source(GUI / "window_memory_presence.py",  # GUI split #12 batch 1 INTENTIONAL: _init_presence moved verbatim here.
                          "_init_presence")
        self.assertIn('qsettings_get("presence/connect_on_launch"',
                      fn)
        self.assertIn(
            "QTimer.singleShot(800, self._connect_on_launch_once)",
            fn)

    def test_auto_connect_starts_the_session(self):
        fn = _func_source(GUI / "window_memory_presence.py",  # GUI split #12 batch 1 INTENTIONAL: _connect_on_launch_once moved verbatim here.
                          "_connect_on_launch_once")
        self.assertIn("live_session.is_connected", fn)
        self.assertIn("self._start_connection()", fn)

    def test_toggle_reports_the_launch_effect(self):
        fn = _func_source(GUI / "window_memory_presence.py",  # GUI split #12 batch 1 INTENTIONAL: _on_connect_launch_toggled moved verbatim here.
                          "_on_connect_launch_toggled")
        self.assertIn("will connect by itself at launch", fn)
        shell = _Shell()
        _exec_func(GUI / "window_memory_presence.py",  # GUI split #12 batch 1 INTENTIONAL: _on_connect_launch_toggled moved verbatim here.
                   "_on_connect_launch_toggled",
                   {"qsettings_set": qsettings_set}, shell, True)
        self.assertIn("will connect by itself at launch",
                      shell.status)

    def test_permissions_apply_is_wired(self):
        """PF-18 widget batch (INTENTIONAL transition, design
        §8 item 7): the S4 "not wired yet" pin is REPLACED,
        not deleted — Apply is wired now. The old copy must
        be nowhere in the widget source, and the wired Apply
        path must be present. Behaviour pins (Apply writes a
        grant, failure surfaces, flip-back works) live in
        tests/test_pf18_widget.py, which drives the widget;
        this suite stays source-level by design."""
        source = _source("pf_permissions.py")
        self.assertNotIn("Revoke is not wired yet", source)
        self.assertNotIn("Record choices for the wiring step", source)
        self.assertNotIn("Recorded for the wiring step", source)
        self.assertIn("apply_permission_changes", source)
        self.assertIn('QPushButton("Apply")', source)


class TestFeedbackCalls(unittest.TestCase):
    # (module, handler): every handler that previously changed a
    # setting with NO visible feedback now reports one line.
    NOTED = [
        ("window_services_audio.py", "_on_media_pause_toggled"),  # GUI split #10 batch 2 INTENTIONAL: handlers moved verbatim to the audio/speech modules.
        ("window_services_speech.py", "_on_whisper_hours_toggled"),
        ("window_services_speech.py", "_on_whisper_times_changed"),
        ("window_services_speech.py", "_on_cue_toggled"),
        ("window_services_audio.py", "_on_wake_phrase_changed"),
        # INTENTIONAL (refactor P1 step 5): these two handlers moved
        # verbatim to window_settings_voice.py. Assertions unchanged.
        ("window_settings_voice.py", "_on_sounds_toggled"),
        ("window_settings_voice.py", "_on_continuous_display_toggled"),
        # GUI split #4 INTENTIONAL: handlers moved verbatim out of
        # window_brain.py (voice/local/engine/services per name).
        # Assertions unchanged.
        ("window_brain_voice.py", "_on_whisper_model_changed"),
        ("window_brain_voice.py", "_on_transcription_profile_picked"),
        ("window_brain_voice.py", "_on_tool_profile_picked"),
        ("window_brain_engine.py", "_on_local_backend_changed"),
        ("window_brain_services.py", "_on_browser_real_chrome_toggled"),
        ("window_brain_services.py", "_on_browser_debug_port_changed"),
        ("window_brain_local.py", "_save_ollama_model"),
        ("window_brain_voice.py", "_save_local_tts_voice"),
        # GUI split #11 batch 1 INTENTIONAL: suggestion handlers moved
        # verbatim to window_session_suggestions.py. Assertions unchanged.
        ("window_session_suggestions.py", "_on_suggestions_enabled_toggled"),
        ("window_session_suggestions.py", "_on_suggestions_auto_act_toggled"),
        ("window_session_suggestions.py", "_on_suggestion_class_toggled"),
        ("window_memory_presence.py", "_on_watcher_kind_toggled"),  # GUI split #12 batch 1 INTENTIONAL: _on_watcher_kind_toggled moved verbatim here.
        ("window_memory_presence.py", "_on_briefing_time_changed"),  # GUI split #12 batch 1 INTENTIONAL: _on_briefing_time_changed moved verbatim here.
        ("window_memory_presence.py", "_on_summon_hotkey_pref_toggled"),  # GUI split #12 batch 1 INTENTIONAL: _on_summon_hotkey_pref_toggled moved verbatim here.
        ("window_memory_web.py", "_on_voice_changed"),  # GUI split #12 batch 1 INTENTIONAL: _on_voice_changed moved verbatim here.
    ]

    def test_each_no_feedback_handler_notes(self):
        for module, func in self.NOTED:
            with self.subTest(handler=f"{module}:{func}"):
                fn = _func_source(GUI / module, func)
                self.assertIn("_note_settings_feedback", fn)

    def test_next_connect_wording_names_the_timing(self):
        """Next-connect handlers must say so in the NOTE THE USER
        SEES (not a bare 'applied', which would imply the live
        session changed). Asserted on stub-exec output, not on
        source: production f-strings split the phrase across
        literals/lines, which is fine at runtime."""
        config = types.SimpleNamespace()

        class _Combo:
            def __init__(self, data):
                self._data = data

            def itemData(self, index):
                return self._data

        class _LineEdit:
            def __init__(self, text):
                self._text = text

            def text(self):
                return self._text

        cases = [
            # GUI split #4 INTENTIONAL: handlers moved verbatim out of
            # window_brain.py (voice/engine/local per name).
            # Assertions unchanged.
            ("window_brain_voice.py", "_on_whisper_model_changed",
             ("tiny",), {}),
            ("window_brain_engine.py", "_on_local_backend_changed",
             (0,), {"combo_local_backend": _Combo("ollama")}),
            ("window_brain_local.py", "_save_ollama_model",
             (), {"input_ollama_model": _LineEdit("qwen")}),
            ("window_memory_web.py", "_on_voice_changed",  # GUI split #12 batch 1 INTENTIONAL: _on_voice_changed moved verbatim here.
             ("George",), {}),
        ]
        for module, func, args, attrs in cases:
            with self.subTest(handler=func):
                shell = _Shell()
                for key, value in attrs.items():
                    setattr(shell, key, value)
                shell._update_backend_visibility = lambda: None
                _exec_func(GUI / module, func, {"config": config},
                           shell, *args)
                self.assertTrue(
                    any("next time you connect" in n
                        for n in shell.notes),
                    f"{func} notes: {shell.notes}")

    def test_briefing_time_and_watcher_kind_stub_exec(self):
        shell = _Shell()

        class _QTime:
            def toString(self, fmt):
                return "07:30"

        # The handler annotates qtime: QTime, evaluated at def
        # time during exec, so the stub class must be a global.
        _exec_func(GUI / "window_memory_presence.py",  # GUI split #12 batch 1 INTENTIONAL: _on_briefing_time_changed moved verbatim here.
                   "_on_briefing_time_changed",
                   {"QTime": _QTime, "qsettings_set": qsettings_set},
                   shell, _QTime())
        self.assertEqual(
            shell.settings.values["presence/briefing_time"], "07:30")
        self.assertTrue(any("07:30" in n for n in shell.notes))
        _exec_func(GUI / "window_memory_presence.py",  # GUI split #12 batch 1 INTENTIONAL: _on_watcher_kind_toggled moved verbatim here.
                   "_on_watcher_kind_toggled",
                   {"qsettings_set": qsettings_set}, shell, "battery",
                   False)
        self.assertFalse(shell.settings.values["watchers/battery"])
        self.assertTrue(any("battery" in n and "next notice" in n
                            for n in shell.notes))


class TestScrollStandardisation(unittest.TestCase):
    def test_page_has_exactly_one_scroll_area(self):
        build = _func_source(GUI / "window_settings.py",
                             "_build_settings_page")
        self.assertEqual(build.count("QScrollArea("), 1)
        self.assertIn("scroll.setWidgetResizable(True)", build)
        self.assertIn("self.settings_scroll = scroll", build)

    def test_advanced_lives_inside_the_page_scroll(self):
        # INTENTIONAL (refactor P1 step 5): the suggestions section
        # (which adds more_container) moved verbatim to
        # window_settings_groups.py as _build_suggestions_cards.
        build = _func_source(GUI / "window_settings_groups.py",
                             "_build_suggestions_cards")
        self.assertIn("c_layout.addWidget(self.more_container)", build)

    def test_no_nested_scrollers_in_settings_widgets(self):
        # GUI split #3 INTENTIONAL: HelpWidget moved to pf_settings_help.py
        # and PrivacyDashboardWidget to pf_privacy.py; the check must read
        # both widget modules, or it would pass vacuously on the facade.
        src = _source("pf_settings_help.py") + _source("pf_privacy.py")
        self.assertNotIn("QScrollArea(", src,
                         "HelpWidget and PrivacyDashboardWidget "
                         "must not nest a scroll area inside the "
                         "settings page scroll")

    def test_search_still_scrolls_to_top(self):
        fn = _func_source(GUI / "window_settings.py",
                          "_on_settings_search")
        self.assertIn("verticalScrollBar().setValue(0)", fn)

    def test_s4_groups_still_present(self):
        build = _func_source(GUI / "window_settings.py",
                             "_build_settings_page")
        for group in ("Voice & language", "Privacy & permissions",
                      "Appearance", "Notifications", "Connections"):
            self.assertIn(group, build)
        self.assertIn("setVisible(False)", build)  # Advanced collapsed


class TestScrollWidgetsOffscreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            cls.app = QApplication.instance() or QApplication([])
        except ImportError:
            raise unittest.SkipTest("PySide6 unavailable")

    @classmethod
    def tearDownClass(cls):
        # Unregister the spec-loaded module (dataclasses needs it
        # in sys.modules during exec; leaving it would pollute
        # later suites in the same process).
        sys.modules.pop("gui.pf_surfaces", None)

    def _load_pf(self):
        import importlib.util
        # GUI split #3 INTENTIONAL: the facade + section modules use
        # relative imports, so the module must load under its gui package
        # name (parent package registered first); the old arbitrary loader
        # key no longer works — a packageless load cannot resolve them.
        import gui  # noqa: F401  (registers the parent package)
        spec = importlib.util.spec_from_file_location(
            "gui.pf_surfaces", GUI / "pf_surfaces.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gui.pf_surfaces"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_help_widget_has_no_scroll_area(self):
        from PySide6.QtWidgets import QScrollArea
        mod = self._load_pf()
        widget = mod.HelpWidget()
        self.assertEqual(widget.findChildren(QScrollArea), [])
        self.assertIsNotNone(widget.results_layout)
        widget.search.setText("refuse")
        self.assertGreaterEqual(widget.results_layout.count(), 0)

    def test_privacy_dashboard_has_no_scroll_area(self):
        from PySide6.QtWidgets import QScrollArea
        mod = self._load_pf()
        api = {"list": lambda: [], "delete": lambda c, k: {},
               "wipe": lambda c: {}, "export": lambda: {}}
        widget = mod.PrivacyDashboardWidget(api=api)
        self.assertEqual(widget.findChildren(QScrollArea), [])
        self.assertIsNotNone(widget.facts_layout)


if __name__ == "__main__":
    unittest.main()

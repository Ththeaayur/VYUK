"""Round-3 section C fix queue item 14: qsettings per-site handling audit.

C3-F4 made qsettings_set/remove LOUD (RuntimeError on a genuine write
failure), but ~50 call sites sat inside broad `except Exception: pass`
guards, so the loud failure was swallowed one layer up and the setting
still silently lost. The fix: a shared noted pair
(qsettings_set_noted / qsettings_remove_noted in core/config_spec.py)
that logs the key + cause and records a sticky per-key degraded state
(the Activity feed renders it as a "Settings degraded" marker), and
39 call sites re-pointed at it. 14 sites were classified HARMLESS and
left alone (idempotent migration/healing writes whose failure re-runs
with the same outcome, cosmetic preferences, or sites whose failure
already surfaces — the portability import's skipped list, the undo
restorer's read-back verification); the full table with the one-line
reason per site is in r3r4_qsettings_audit_report.md.

Read-only HOME in this lab: the process runs as root, so chmod 0555
does NOT block writes; /sys IS mounted read-only and even root cannot
create /sys/NovaAI — so an explicit-path store at /sys/NovaAI/Nova.conf
is the root-proof read-only store (the C3-F4 trick, reused here).
"""
import contextlib
import importlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import config_spec as cs

# The voice-identity twin of config_spec: GUI modules reach the
# noted setter through voice_assistant.core.gui_facade, so they
# record into THIS module object's sticky dict, while the bare
# `core.config_spec` above has its own. In production the app
# runs as the voice_assistant package, so there is only one
# identity; in this harness both exist, and the per-site checks
# must read (and reset) both, or GUI-site failures look swallowed.
# (The feed-marker tests below deliberately stay bare: they note
# and read through the same bare identity, which is what the
# feed wiring itself uses.)
import importlib as _importlib


def _voice_cs():
    """The voice-identity twin of config_spec, resolved lazily
    (after _register_packages() has planted the shim)."""
    return _importlib.import_module(
        "voice_assistant.core.config_spec")


def _degraded_state():
    """The sticky record merged across both module identities."""
    merged = {}
    for _mod in (cs, _voice_cs()):
        merged.update(_mod.qsettings_write_degraded_state())
    return merged


def _reset_degraded():
    for _mod in (cs, _voice_cs()):
        _mod.reset_qsettings_write_degraded()

ROOT = Path(__file__).resolve().parent.parent


def _register_packages():
    """Mirror test_wiring_triage_v600: make voice_assistant.*
    importable from this folder whatever it is called, so the lazy
    w9/gui imports resolve to real modules."""
    if "voice_assistant" not in sys.modules:
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [str(ROOT)]
        sys.modules["voice_assistant"] = pkg
    if "voice_assistant.core" not in sys.modules:
        core = types.ModuleType("voice_assistant.core")
        core.__path__ = [str(ROOT / "core")]
        sys.modules["voice_assistant.core"] = core


_register_packages()

try:
    from core import w9_wiring_chat as w9c
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    w9c = None
    _W9_ERROR = exc

try:
    from PySide6.QtCore import QSettings as _QSettings
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False

READONLY_CONF = "/sys/NovaAI/Nova.conf"

PACK_TEXT = json.dumps({
    "schema_version": 1, "name": "Audit",
    "tokens": {"surface": "#0F141A", "surface_variant": "#1E242B",
               "on_surface": "#F2F4F7", "primary": "#0B5FFF",
               "on_primary": "#FFFFFF", "accent": "#00C8FF"}})


class _FailingStore:
    """Dict-backed store whose sync never persists and whose status()
    reports AccessError — the no-Qt double for a read-only HOME
    (copied from test_r3c4_qsettings). fileName() points at a real
    temp conf holding the OLD value, so the read-back has bytes to
    judge."""

    def __init__(self, conf: Path, values=None):
        self._conf = conf
        self.data = dict(values or {})
        self.status_name = "AccessError"

    def fileName(self):
        return str(self._conf)

    def status(self):
        class _Status:
            name = "AccessError"
        return _Status()

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, val):
        self.data[key] = val

    def contains(self, key):
        return key in self.data

    def remove(self, key):
        self.data.pop(key, None)

    def sync(self):
        pass


class _HealthyFakeStore(_FailingStore):
    """The same double, but the sync succeeds: status NoError, so no
    read-back runs and no Qt is needed."""

    def __init__(self, conf: Path, values=None):
        super().__init__(conf, values)
        self.status_name = "NoError"

    def status(self):
        class _Status:
            name = "NoError"
        return _Status()


class _DegradedReset(unittest.TestCase):
    def setUp(self):
        super().setUp()
        _reset_degraded()
        self.addCleanup(_reset_degraded)

    def _conf(self, content="theme=Cyan\n"):
        tmp = tempfile.TemporaryDirectory(prefix="r3r4_")
        self.addCleanup(tmp.cleanup)
        conf = Path(tmp.name) / "Nova.conf"
        conf.write_text(content, encoding="utf-8")
        return conf


class TestNotedHelpersFakeStore(_DegradedReset):
    """The noted pair on the no-Qt doubles — both interpreters."""

    def test_healthy_set_noted_true_and_persists(self):
        conf = self._conf()
        store = _HealthyFakeStore(conf)
        self.assertTrue(cs.qsettings_set_noted("theme", "Solar",
                                                settings=store))
        self.assertEqual(store.data["theme"], "Solar")
        self.assertEqual(_degraded_state(), {})

    def test_failing_set_noted_false_logs_and_records(self):
        conf = self._conf()
        store = _FailingStore(conf, {"theme": "Cyan"})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertFalse(cs.qsettings_set_noted("theme", "Solar",
                                                     settings=store))
        self.assertIn("[settings] could not save 'theme'", buf.getvalue())
        state = _degraded_state()
        self.assertEqual(set(state), {"theme"})
        self.assertEqual(state["theme"]["count"], 1)
        self.assertIn("AccessError", state["theme"]["last_error"])
        # The failed write rolls the store back: get still reads Cyan.
        self.assertEqual(store.data["theme"], "Cyan")

    def test_failing_set_noted_counts_accumulate(self):
        conf = self._conf()
        store = _FailingStore(conf, {"theme": "Cyan"})
        cs.qsettings_set_noted("theme", "Solar", settings=store)
        cs.qsettings_set_noted("theme", "Amber", settings=store)
        self.assertEqual(
            _degraded_state()["theme"]["count"], 2)

    def test_failing_remove_noted_false_and_recorded(self):
        conf = self._conf("theme=Cyan\nsounds_enabled=false\n")
        store = _FailingStore(conf, {"theme": "Cyan"})
        self.assertFalse(cs.qsettings_remove_noted("theme",
                                                    settings=store))
        state = _degraded_state()
        self.assertEqual(set(state), {"theme"})
        self.assertEqual(state["theme"]["count"], 1)

    def test_healthy_remove_noted_true(self):
        conf = self._conf()
        store = _HealthyFakeStore(conf, {"theme": "Cyan"})
        self.assertTrue(cs.qsettings_remove_noted("theme",
                                                  settings=store))
        self.assertNotIn("theme", store.data)
        self.assertEqual(_degraded_state(), {})

    def test_unknown_key_still_raises_keyerror(self):
        conf = self._conf()
        store = _HealthyFakeStore(conf)
        with self.assertRaises(KeyError):
            cs.qsettings_set_noted("no_such_key_xyz", "v",
                                   settings=store)
        with self.assertRaises(KeyError):
            cs.qsettings_remove_noted("no_such_key_xyz",
                                      settings=store)
        # A programmer error is loud, not degraded.
        self.assertEqual(_degraded_state(), {})

    def test_degraded_state_is_a_copy(self):
        conf = self._conf()
        store = _FailingStore(conf, {"theme": "Cyan"})
        cs.qsettings_set_noted("theme", "Solar", settings=store)
        state = _degraded_state()
        state["theme"]["count"] = 99
        self.assertEqual(
            _degraded_state()["theme"]["count"], 1)

    def test_note_never_raises(self):
        class _BadStr(RuntimeError):
            def __str__(self):
                raise ValueError("boom")

        cs.note_qsettings_write_failure("theme", _BadStr())
        self.assertEqual(
            _degraded_state()["theme"]["count"], 1)


class TestFeedMarker(_DegradedReset):
    """The Activity feed renders the sticky record (both
    interpreters — read_unified needs no Qt)."""

    def test_marker_names_key_and_cause(self):
        from core.activity_feed import read_unified

        cs.note_qsettings_write_failure(
            "theme", RuntimeError("the settings file could not be "
                                  "saved (QSettings status AccessError)"))
        with tempfile.TemporaryDirectory() as tmp:
            entries = read_unified(limit=50, data_dir=Path(tmp))
        markers = [e for e in entries
                   if e.get("source") == "observability"]
        settings_markers = [m for m in markers
                            if m["title"].startswith("Settings degraded")]
        self.assertEqual(len(settings_markers), 1)
        title = settings_markers[0]["title"]
        self.assertIn("1 write(s) to 1 setting(s)", title)
        self.assertIn("theme", title)
        self.assertIn("AccessError", title)
        self.assertIn("may not survive a restart", title)
        self.assertEqual(settings_markers[0]["kind"], "degraded")

    def test_marker_aggregates_keys(self):
        from core.activity_feed import read_unified

        cs.note_qsettings_write_failure("theme", RuntimeError("x"))
        cs.note_qsettings_write_failure("sounds_enabled",
                                        RuntimeError("y"))
        cs.note_qsettings_write_failure("theme", RuntimeError("x"))
        with tempfile.TemporaryDirectory() as tmp:
            entries = read_unified(limit=50, data_dir=Path(tmp))
        titles = [e["title"] for e in entries
                  if e.get("source") == "observability"
                  and e["title"].startswith("Settings degraded")]
        self.assertEqual(len(titles), 1)
        self.assertIn("3 write(s) to 2 setting(s)", titles[0])

    def test_no_marker_when_healthy(self):
        from core.activity_feed import read_unified

        with tempfile.TemporaryDirectory() as tmp:
            entries = read_unified(limit=50, data_dir=Path(tmp))
        titles = [e["title"] for e in entries
                  if e.get("source") == "observability"
                  and e["title"].startswith("Settings degraded")]
        self.assertEqual(titles, [])


@unittest.skipUnless(_HAVE_QT, "read-only QSettings legs need Qt")
class TestNotedHelpersRealQt(_DegradedReset):
    """The noted pair against real Qt stores — the /sys read-only
    store and a healthy temp store."""

    def test_readonly_set_noted_false_and_recorded(self):
        store = _QSettings(READONLY_CONF, _QSettings.IniFormat)
        self.assertFalse(cs.qsettings_set_noted("theme", "Solar",
                                                 settings=store))
        state = _degraded_state()
        self.assertEqual(set(state), {"theme"})
        self.assertIn("AccessError", state["theme"]["last_error"])

    def test_healthy_set_noted_true_and_persists(self):
        conf = self._conf()
        store = _QSettings(str(conf), _QSettings.IniFormat)
        self.assertTrue(cs.qsettings_set_noted("theme", "Solar",
                                                settings=store))
        self.assertEqual(_degraded_state(), {})
        fresh = _QSettings(str(conf), _QSettings.IniFormat)
        self.assertEqual(fresh.value("theme"), "Solar")

    def test_readonly_remove_absent_key_is_vacuous(self):
        # /sys/NovaAI/Nova.conf does not exist (the mount is
        # read-only, so it never comes into being): removing a key
        # that is not there needs no write, so the noted remove
        # correctly returns True and records nothing — no loss, no
        # noise. A genuine remove failure (key present in a
        # read-only file) is covered by the fake-store leg below,
        # where the conf bytes do carry the key.
        store = _QSettings(READONLY_CONF, _QSettings.IniFormat)
        self.assertTrue(cs.qsettings_remove_noted("theme",
                                                  settings=store))
        self.assertEqual(_degraded_state(), {})


class TestPerSiteFakeStore(_DegradedReset):
    """Fixed core sites on the no-Qt doubles. The stores are fake,
    but w9_wiring_chat itself imports PySide6, so these legs run
    on the Qt interpreter and skip on sys."""

    def _need_w9(self):
        if w9c is None:
            self.skipTest(f"w9 deps unavailable: {_W9_ERROR}")

    def test_theme_pack_validates_but_notes_failure(self):
        self._need_w9()
        conf = self._conf()
        store = _FailingStore(conf, {"theme": "Cyan"})
        applied = w9c.apply_theme_pack_text(PACK_TEXT, settings=store)
        self.assertEqual(applied["name"], "Audit")
        self.assertEqual(applied["accent"], "#00C8FF")
        # The pack still validates (that is the return), but every
        # one of its four writes is logged + recorded.
        state = _degraded_state()
        self.assertEqual(set(state), {"theme_pack/name",
                                      "theme_pack/tokens", "theme",
                                      "appearance/custom_hex"})
        self.assertTrue(all(r["count"] == 1 for r in state.values()))

    def test_theme_pack_invalid_still_raises(self):
        self._need_w9()
        from core.theme_packs import ThemePackError

        conf = self._conf()
        store = _HealthyFakeStore(conf)
        with self.assertRaises(ThemePackError):
            w9c.apply_theme_pack_text("not json", settings=store)
        self.assertEqual(_degraded_state(), {})

    def test_reset_row_notes_failed_legacy_remove(self):
        self._need_w9()
        # The conf BYTES must carry the legacy key in the form
        # QSettings itself writes it — a "[appearance]" section,
        # not a bare "appearance/density=compact" line (Qt parses
        # the bare line as a general-group key, so contains()
        # misses it and the C3-F4 read-back calls the remove a
        # success). The read-back judges a remove by the file, so
        # a key that is only in the fake's dict reads as already
        # removed.
        conf = self._conf("[appearance]\ndensity=compact\n")
        # Canonical "density" key absent (its reset is vacuous, no
        # raise); legacy "appearance/density" present, so its
        # removal is a real write — and it fails on this store.
        store = _FailingStore(conf, {"appearance/density": "compact"})
        wrapped = w9c.settings_store(store)
        w9c.reset_settings_row(wrapped, "density")
        state = _degraded_state()
        self.assertEqual(set(state), {"appearance/density"})

    def test_migrate_settings_store_unchanged_harmless(self):
        # Class A control: the migration is an idempotent copy and
        # stays on the plain setter — a failing store migrates
        # nothing and records nothing (the failure re-runs next
        # boot with the same outcome).
        self._need_w9()
        conf = self._conf()
        store = _FailingStore(conf, {"appearance/density": "compact"})
        wrapped = w9c.settings_store(store)
        self.assertEqual(w9c.migrate_settings_store(wrapped), [])
        self.assertEqual(_degraded_state(), {})


@unittest.skipUnless(_HAVE_QT, "GUI site legs need Qt")
class TestPerSiteQt(_DegradedReset):
    """Fixed GUI sites against the real read-only /sys store, driven
    as unbound mixin methods on stub windows — the real method
    code, the real QSettings path, no network, no main window."""

    def _readonly_store(self):
        return _QSettings(READONLY_CONF, _QSettings.IniFormat)

    def _need_gui(self, modname):
        try:
            return importlib.import_module(f"gui.{modname}")
        except Exception as exc:  # noqa: BLE001 — skip, not error
            self.skipTest(f"gui.{modname} unavailable: {exc}")

    def test_composer_draft_noted(self):
        mod = self._need_gui("window_chat_thread")
        stub = SimpleNamespace(
            settings=self._readonly_store(),
            input_cmd=SimpleNamespace(text=lambda: "half-typed"),
        )
        mod.ChatThreadMixin._chat_save_composer_draft(stub)
        state = _degraded_state()
        self.assertEqual(set(state), {"chat/composer_draft"})

    def test_last_seen_marks_noted(self):
        mod = self._need_gui("window_chat_thread")
        leaf = SimpleNamespace(id=42, store_id=41)
        stub = SimpleNamespace(
            settings=self._readonly_store(),
            _chat_thread=lambda: SimpleNamespace(leaf=lambda: leaf),
        )
        mod.ChatThreadMixin._chat_mark_last_seen(stub)
        state = _degraded_state()
        self.assertEqual(set(state), {"chat/last_seen_id",
                                      "chat/last_seen_store_id"})

    def _readonly_default_store(self):
        """Patch qsettings_store() on BOTH config_spec identities
        to return the read-only /sys store, so default-store call
        sites (which pass no settings=) fail in-process. The GUI
        modules reach the noted setter through the voice identity,
        so patching only the bare one would leave the real path
        writing to the writable home store."""
        store = self._readonly_store()
        return (mock.patch.object(cs, "qsettings_store",
                                  return_value=store),
                mock.patch.object(_voice_cs(), "qsettings_store",
                                  return_value=store))

    def test_onboarding_brain_choice_noted(self):
        mod = self._need_gui("onboarding_flow")
        # The method mutates the config singleton the module itself
        # imported (voice_assistant.core.gui_facade's `config`) —
        # restore that exact object, not the bare-core twin.
        old_engine = mod.config.brain_engine
        self.addCleanup(setattr, mod.config, "brain_engine", old_engine)
        stub = SimpleNamespace(
            settings=self._readonly_store(),
            brain_choice="local",
        )
        # _persist_brain_choice writes through the DEFAULT store
        # (no settings=) — make that store the read-only one.
        patch_bare, patch_voice = self._readonly_default_store()
        with patch_bare, patch_voice, mock.patch(
                "voice_assistant.core.gui_facade.save_env_setting"):
            mod.FlowMixin._persist_brain_choice(stub)
        state = _degraded_state()
        self.assertEqual(set(state), {"setup/brain_choice",
                                      "setup/local_pack_wanted"})

    def test_onboarding_sound_choice_noted(self):
        mod = self._need_gui("onboarding_flow")
        stub = SimpleNamespace(
            settings=self._readonly_store(),
            _sound_checkbox=SimpleNamespace(isChecked=lambda: True),
        )
        patch_bare, patch_voice = self._readonly_default_store()
        with patch_bare, patch_voice:
            mod.FlowMixin._persist_sound_choice(stub)
        state = _degraded_state()
        self.assertEqual(set(state), {"sounds_enabled"})

    def test_output_device_pick_noted(self):
        mod = self._need_gui("window_services_audio")
        from voice_assistant.core.gui_facade import audio_manager

        old = (audio_manager.output_device,
               audio_manager.output_device_name)
        self.addCleanup(setattr, audio_manager, "output_device", old[0])
        self.addCleanup(setattr, audio_manager, "output_device_name",
                        old[1])
        stub = SimpleNamespace(
            settings=self._readonly_store(),
            combo_out_dev=SimpleNamespace(
                currentData=lambda: 2, currentText=lambda: "Speakers"),
            _refresh_output_device_label=lambda: None,
        )
        mod.ServicesAudioMixin._on_output_device_changed(stub, 0)
        state = _degraded_state()
        self.assertEqual(set(state), {"audio/output_device",
                                      "audio/output_device_name"})

    def test_input_device_pick_noted(self):
        mod = self._need_gui("window_services_audio")
        from voice_assistant.core.gui_facade import audio_manager

        old = (audio_manager.input_mode, audio_manager.input_device,
               audio_manager.input_device_name)
        self.addCleanup(setattr, audio_manager, "input_mode", old[0])
        self.addCleanup(setattr, audio_manager, "input_device", old[1])
        self.addCleanup(setattr, audio_manager, "input_device_name",
                        old[2])
        stub = SimpleNamespace(
            settings=self._readonly_store(),
            combo_in_dev=SimpleNamespace(
                currentData=lambda: 3, currentText=lambda: "Mic"),
            _refresh_input_device_label=lambda: None,
        )
        mod.ServicesAudioMixin._on_input_device_changed(stub, 0)
        state = _degraded_state()
        self.assertEqual(set(state), {"audio/input_mode",
                                      "audio/input_device",
                                      "audio/input_device_name"})

    def test_motion_choice_noted(self):
        mod = self._need_gui("window_settings_appearance")
        stub = SimpleNamespace(
            settings=self._readonly_store(),
            MOTION_LEVELS=mod.SettingsAppearanceMixin.MOTION_LEVELS,
            _sync_motion_controls=lambda level: None,
            _apply_ambience=lambda: None,
        )
        mod.SettingsAppearanceMixin._on_motion_selected(stub, "subtle")
        state = _degraded_state()
        self.assertEqual(set(state), {"motion/level", "motion/reduced"})

    def test_glass_toggle_noted(self):
        mod = self._need_gui("window_settings_appearance")
        stub = SimpleNamespace(
            settings=self._readonly_store(),
            _apply_saved_glass=lambda: None,
        )
        mod.SettingsAppearanceMixin._on_glass_toggled(stub, True)
        state = _degraded_state()
        self.assertEqual(set(state), {"appearance/glass"})

    def test_first_open_prompt_choice_noted(self):
        mod = self._need_gui("window_shell_onboarding")
        stub = SimpleNamespace(settings=self._readonly_store())
        with mock.patch(
                "gui.window_shell_onboarding.FirstOpenPromptDialog",
                create=True) as _dlg_cls:
            _dlg_cls.return_value = SimpleNamespace(choice_yes=True)
            mod.ShellOnboardingMixin._run_first_open_prompt(stub)
        state = _degraded_state()
        # Exactly the two keys apply_choice writes — the seen flag
        # and the choice record. Both are noted, so the prompt
        # repeats at the next launch with a trace of why.
        self.assertEqual(set(state), {"privacy/firstopen_prompt_seen",
                                      "privacy/continuous_display_choice"})

    def test_coach_flag_lambda_noted(self):
        mod = self._need_gui("window_shell_onboarding")
        import gui.firstrun_g as _fg

        store = self._readonly_store()
        setter = (lambda k, v: cs.qsettings_set_noted(
            k, v, settings=store))
        # The no-targets terminal path writes the flag through the
        # setter the shell passes in — a failure is noted, not
        # swallowed.
        with mock.patch.object(_fg, "COACH_MARK_STEPS", []):
            _fg.run_coach_sequence(
                SimpleNamespace(),
                lambda k, d=False: False,
                setter)
        state = _degraded_state()
        self.assertEqual(set(state), {_fg.COACH_SHOWN_KEY})

    def test_browser_offer_noted_via_default_store(self):
        # note_browser_offer_shown uses the DEFAULT store, so the
        # read-only store is installed by patching the factory —
        # the product code under test is untouched.
        from core import settings_voice as _sv

        old_active = _sv._BROWSER_OFFER_ACTIVE
        self.addCleanup(setattr, _sv, "_BROWSER_OFFER_ACTIVE", old_active)
        store = self._readonly_store()
        with mock.patch.object(cs, "qsettings_store",
                               return_value=store):
            _sv.note_browser_offer_shown()
        state = _degraded_state()
        self.assertEqual(set(state), {"browser/connect_offer_shown"})


if __name__ == "__main__":
    unittest.main()

"""GAPS-21: the section 6.1 C-series remainder (configuration).

C-V1 — save_env_setting is one write now: file + os.environ +
the running singleton (was file + env only, the singleton sync
a hand-set line at every call site — and w10_wiring's settings
import had no such line, so an imported bundle never took effect
in the running session).
C-V4 — the key-mapping tables are derived from the spec now:
settings_schema.FIELD_SPECS defaults (was hand-copied literals)
and w10_wiring's CONFIG_BACKED_KEYS / QSETTINGS_KEYS /
_CONFIG_ATTR_TO_ENV (was hand-copied literals, prover-checked
nowhere).
C-V6 — the .env persistence half lives in core/config_env.py
now; core/config.py keeps AppConfig, the singleton, the save
entry points and the migration runner, re-exporting the moved
names.
C-V8 — diagnostics exist now: build_spec()/env() record
unparseable values for spec keys, load_env_file() records
unknown keys in .env. Nothing is refused — a diagnostic is not
a gate.
C-V2 / C-V3 / C-V5 — already closed (GAPS-10/11/15 and the
env-read collapse); pinned here so the closure cannot regress
silently.
C-V9 — design-only (adjudicated, no build): VYUK is a
single-entry-point desktop app, main.py IS its composition
point, and the Recipe half is done (ConfigSpec); pinned here.
"""

import os
import pathlib
import re
import tempfile
import unittest

from core import config as _config_mod
from core import config_env as _env_mod
from core import config_spec as cs
from core import settings_schema as _schema
from core import w10_wiring as _w10
from core.config import AppConfig, config as _live_config

TREE = pathlib.Path(__file__).resolve().parent.parent

_ENV_KEYS = ("BRAIN_ENGINE", "VOICE_NAME", "KOKORO_VOICE",
             "TTS_LOUDNESS_ENABLED", "SAMPLE_RATE_IN",
             "SAMPLE_RATE_OUT", "SOME_UNKNOWN_KEY", "GEMINI_API_KEY",
             "SPEAKER_LOCK", "SESSION_PROVENANCE_LOCK",
             "VYUK_CONFIG_VERSION")
_FIELDS = ("brain_engine", "voice_name", "kokoro_voice",
           "tts_loudness_enabled", "sample_rate_in",
           "sample_rate_out", "session_provenance_lock", "api_key")


def _code_text(text):
    """Text with triple-quoted strings and # comments removed."""
    text = re.sub(r'"""[\s\S]*?"""', '""', text)
    text = re.sub(r"'''[\s\S]*?'''", "''", text)
    return "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())


class _TempEnvTests(unittest.TestCase):
    """Tests that write a .env file: PROJECT_ROOT is a temp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmp.name)
        self._saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
        self._saved_fields = {f: getattr(_live_config, f)
                              for f in _FIELDS}
        # The seam is core.config.PROJECT_ROOT (only it is patched:
        # the persistence layer resolves the root through it, so
        # this exercises the production path exactly as the
        # pre-existing tests do).
        self._saved_root = _config_mod.PROJECT_ROOT
        _config_mod.PROJECT_ROOT = self._root
        for k in _ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        _config_mod.PROJECT_ROOT = self._saved_root
        for f, v in self._saved_fields.items():
            setattr(_live_config, f, v)
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _env_file(self):
        return (self._root / ".env").read_text(encoding="utf-8")


# ---------------------------------------------------------------- C-V1
class SaveSyncTests(_TempEnvTests):
    def test_save_brain_engine_syncs_all_three(self):
        _config_mod.save_env_setting("BRAIN_ENGINE", "gemini")
        self.assertEqual(_live_config.brain_engine, "gemini")
        self.assertEqual(os.environ["BRAIN_ENGINE"], "gemini")
        self.assertIn("BRAIN_ENGINE=gemini", self._env_file())

    def test_save_coerces_by_field_type(self):
        _config_mod.save_env_setting("VOICE_NAME", "Aoede")
        _config_mod.save_env_setting("KOKORO_VOICE", "af_heart")
        _config_mod.save_env_setting("TTS_LOUDNESS_ENABLED", "false")
        _config_mod.save_env_setting("SAMPLE_RATE_IN", "22050")
        self.assertEqual(_live_config.voice_name, "Aoede")
        self.assertEqual(_live_config.kokoro_voice, "af_heart")
        self.assertIs(_live_config.tts_loudness_enabled, False)
        self.assertEqual(_live_config.sample_rate_in, 22050)

    def test_save_unknown_key_is_harmless(self):
        _config_mod.save_env_setting("SOME_UNKNOWN_KEY", "x")
        self.assertEqual(os.environ["SOME_UNKNOWN_KEY"], "x")
        self.assertIn("SOME_UNKNOWN_KEY=x", self._env_file())
        self.assertEqual(_live_config.brain_engine,
                         self._saved_fields["brain_engine"])

    def test_save_garbage_int_takes_default_and_diagnoses(self):
        _config_mod.save_env_setting("SAMPLE_RATE_IN", "abc")
        self.assertEqual(_live_config.sample_rate_in, 16000)
        diags = cs.spec_diagnostics()
        self.assertTrue(any(
            d["env_key"] == "SAMPLE_RATE_IN" and d["raw"] == "abc"
            for d in diags), str(diags))

    def test_save_newline_rejected_and_nothing_lands(self):
        with self.assertRaises(ValueError):
            _config_mod.save_env_setting("BRAIN_ENGINE", "bad\nvalue")
        self.assertEqual(
            _live_config.brain_engine, self._saved_fields["brain_engine"])
        self.assertNotIn("BRAIN_ENGINE", os.environ)
        self.assertFalse((self._root / ".env").exists())

    def test_save_alias_syncs_the_field(self):
        _config_mod.save_env_setting("SPEAKER_LOCK", "true")
        self.assertIs(_live_config.session_provenance_lock, True)

    def test_save_respects_config_project_root_patch(self):
        # Neighbour run 1 caught this: two pre-existing tests patch
        # ONLY core.config.PROJECT_ROOT (test_uis6's engine
        # persistence, test_config_spec's migration runner) and the
        # split made the write follow config_env's own root instead
        # — the save landed in the real tree (a stray .env). The
        # .env location's one seam is core.config.PROJECT_ROOT.
        import tempfile as _tf

        with _tf.TemporaryDirectory() as tmp:
            old_root = _config_mod.PROJECT_ROOT
            old_env = os.environ.get("BRAIN_ENGINE")
            try:
                _config_mod.PROJECT_ROOT = pathlib.Path(tmp)
                _config_mod.save_env_setting("BRAIN_ENGINE", "local")
                text = (pathlib.Path(tmp) / ".env").read_text(
                    encoding="utf-8")
                self.assertIn("BRAIN_ENGINE=local", text)
                self.assertEqual(_live_config.brain_engine, "local")
            finally:
                _config_mod.PROJECT_ROOT = old_root
                if old_env is None:
                    os.environ.pop("BRAIN_ENGINE", None)
                else:
                    os.environ["BRAIN_ENGINE"] = old_env

    def test_portability_import_now_lands_on_the_singleton(self):
        out = _w10.apply_portability_settings(
            {"BRAIN_ENGINE": "local", "theme": "Cyan", "NOPE": "1"},
            config_save_fn=_config_mod.save_env_setting,
            qsettings_set=lambda k, v: None)
        self.assertEqual(out["applied"], ["BRAIN_ENGINE", "theme"])
        self.assertEqual(out["skipped"], ["NOPE"])
        self.assertEqual(_live_config.brain_engine, "local")

    def test_save_api_key_still_syncs_self(self):
        # The secrets key dir resolves via the module file, NOT the
        # patched PROJECT_ROOT — point it at the temp dir so the
        # test creates no machine-key file in the real tree.
        from core import secrets_store
        secrets_store.set_key_dir(self._root)
        try:
            _live_config.save_api_key("sk-test-key")
        finally:
            secrets_store.set_key_dir(None)
        self.assertEqual(_live_config.api_key, "sk-test-key")
        self.assertEqual(os.environ["GEMINI_API_KEY"], "sk-test-key")


# ---------------------------------------------------------------- C-V4
class DerivedTablesTests(unittest.TestCase):
    def test_field_specs_equal_the_recorded_literals(self):
        expected = {
            "theme": ("Theme", "Cyan", "choice", "theme", "qsettings"),
            "orb_style": ("Orb style", "Aura", "choice", "orb_style",
                          "qsettings"),
            "sounds_enabled": ("Earcons (sounds)", True, "bool",
                               "sounds_enabled", "qsettings"),
            "suggestions_enabled": ("Suggestions", True, "bool",
                                    "suggestions/enabled", "qsettings"),
            "density": ("Density", "comfortable", "choice", "density",
                        "qsettings"),
            "live_voice": ("Live voice", "Charon", "choice", None,
                           "config/.env via app_settings"),
            "local_voice": ("Local voice", "bm_george", "choice", None,
                            "config/.env via app_settings"),
            "language": ("Language", "en-gb", "choice", None,
                         "config via app_settings"),
            "tts_loudness_enabled": ("TTS loudness", True, "bool", None,
                                     "config via app_settings"),
            "voice_auto_switch": ("Voice auto-switch", True, "bool", None,
                                  "config via app_settings"),
            "telemetry_enabled": ("Telemetry (dev-only, restart required)",
                                  False, "bool", "telemetry/enabled",
                                  "qsettings"),
        }
        self.assertEqual(_schema.FIELD_SPECS, expected)

    def test_nova_settings_data_defaults_come_from_field_specs(self):
        dump = _schema.NovaSettingsData().model_dump()
        self.assertEqual(
            dump, {n: spec[1] for n, spec in _schema.FIELD_SPECS.items()})

    def test_w10_tables_equal_the_recorded_literals(self):
        self.assertEqual(_w10.CONFIG_BACKED_KEYS, (
            "VOICE_NAME", "BRAIN_ENGINE", "PREFERRED_LANGUAGE",
            "VOICE_AUTO_SWITCH", "TTS_LOUDNESS_ENABLED",
            "TTS_LOUDNESS_TARGET_DBFS", "KOKORO_VOICE", "LOCAL_BACKEND"))
        self.assertEqual(_w10.QSETTINGS_KEYS, (
            "theme", "orb_style", "sounds/family", "watchers/enabled"))
        self.assertEqual(_w10._CONFIG_ATTR_TO_ENV, {
            "voice_name": "VOICE_NAME",
            "brain_engine": "BRAIN_ENGINE",
            "preferred_language": "PREFERRED_LANGUAGE",
            "voice_auto_switch": "VOICE_AUTO_SWITCH",
            "tts_loudness_enabled": "TTS_LOUDNESS_ENABLED",
            "tts_loudness_target_dbfs": "TTS_LOUDNESS_TARGET_DBFS",
            "kokoro_voice": "KOKORO_VOICE",
            "local_backend": "LOCAL_BACKEND"})

    def test_w10_tables_are_members_of_the_spec_tables(self):
        for key in _w10.CONFIG_BACKED_KEYS:
            self.assertIn(key, cs.FIELD_FOR_ENV_KEY, key)
        _by_key = {k for _, k, _, _ in cs.QSETTINGS_FIELDS}
        for key in _w10.QSETTINGS_KEYS:
            self.assertIn(key, _by_key, key)

    def test_w10_derivation_uses_the_spec_maps(self):
        self.assertEqual(
            _w10.CONFIG_BACKED_KEYS,
            tuple(cs.ENV_KEY_FOR_FIELD[f]
                  for f in _w10._PORTABILITY_CONFIG_FIELDS))
        self.assertEqual(
            _w10.QSETTINGS_KEYS,
            tuple(cs.QSETTINGS_KEY_FOR_NAME[f]
                  for f in _w10._PORTABILITY_QSETTINGS_FIELDS))


# ---------------------------------------------------------------- C-V6
class PersistenceSplitTests(_TempEnvTests):
    def test_config_reexports_are_the_config_env_objects(self):
        for name in ("load_env_file", "_write_env",
                     "_stored_env_file_value", "_read_env_file_dict",
                     "_flush_secret_migration"):
            self.assertIs(getattr(_config_mod, name),
                          getattr(_env_mod, name), name)

    def test_config_still_exposes_its_own_names(self):
        for name in ("PROJECT_ROOT", "DATA_DIR", "DB_PATH", "config",
                     "AppConfig", "save_env_setting"):
            self.assertTrue(hasattr(_config_mod, name), name)
        self.assertIsInstance(_live_config, AppConfig)

    def test_write_env_byte_behaviour(self):
        (self._root / ".env").write_text(
            "KEEP=1\nBRAIN_ENGINE=local\nBRAIN_ENGINE=local\nTAIL=2\n",
            encoding="utf-8")
        _env_mod._write_env({"BRAIN_ENGINE": "gemini", "NEW_KEY": "v"})
        self.assertEqual(self._env_file(),
                         "KEEP=1\nBRAIN_ENGINE=gemini\nTAIL=2\n"
                         "NEW_KEY=v\n")
        self.assertFalse((self._root / ".env.tmp").exists())

    def test_load_env_file_merge_real_env_wins(self):
        (self._root / ".env").write_text(
            "# comment\n\nBRAIN_ENGINE=gemini\nVOICE_NAME=Aoede\n"
            "NOEQUALS\n=noname\n",
            encoding="utf-8")
        os.environ["BRAIN_ENGINE"] = "local"
        _env_mod.load_env_file()
        self.assertEqual(os.environ["BRAIN_ENGINE"], "local")
        self.assertEqual(os.environ["VOICE_NAME"], "Aoede")
        self.assertNotIn("NOEQUALS", os.environ)

    def test_stored_value_and_dict_readers(self):
        (self._root / ".env").write_text(
            'A=1\nB="quoted"\n', encoding="utf-8")
        self.assertEqual(_env_mod._stored_env_file_value("B"), "quoted")
        self.assertIsNone(_env_mod._stored_env_file_value("ZZZ"))
        self.assertEqual(_env_mod._read_env_file_dict(),
                         {"A": "1", "B": "quoted"})


# ---------------------------------------------------------------- C-V8
class SpecDiagnosticsTests(unittest.TestCase):
    def test_garbage_values_fall_back_and_are_diagnosed(self):
        spec = cs.build_spec({
            "SAMPLE_RATE_IN": "abc", "KOKORO_SPEED": "fast",
            "VOICE_AUTO_SWITCH": "maybe", "BRAIN_ENGINE": "local"})
        self.assertEqual(spec.sample_rate_in, 16000)
        self.assertEqual(spec.kokoro_speed, 0.95)
        self.assertIs(spec.voice_auto_switch, True)
        diags = {(d["env_key"], d["field"]): d
                 for d in cs.spec_diagnostics()}
        self.assertEqual(set(diags), {
            ("SAMPLE_RATE_IN", "sample_rate_in"),
            ("KOKORO_SPEED", "kokoro_speed"),
            ("VOICE_AUTO_SWITCH", "voice_auto_switch")})
        self.assertEqual(diags[("SAMPLE_RATE_IN", "sample_rate_in")]["raw"],
                         "abc")
        self.assertIn("integer",
                      diags[("SAMPLE_RATE_IN", "sample_rate_in")]["reason"])
        self.assertIn("true/false",
                      diags[("VOICE_AUTO_SWITCH",
                             "voice_auto_switch")]["reason"])

    def test_clean_build_has_no_diagnostics(self):
        cs.build_spec({"BRAIN_ENGINE": "local", "SAMPLE_RATE_IN": "8000"})
        self.assertEqual(cs.spec_diagnostics(), [])

    def test_env_live_read_diagnoses_and_dedupes(self):
        self.assertEqual(cs.env("SAMPLE_RATE_IN",
                                {"SAMPLE_RATE_IN": "zzz"}), 16000)
        self.assertEqual(cs.env("SAMPLE_RATE_IN",
                                {"SAMPLE_RATE_IN": "zzz"}), 16000)
        hits = [d for d in cs.spec_diagnostics()
                if d["env_key"] == "SAMPLE_RATE_IN"
                and d["raw"] == "zzz"]
        self.assertEqual(len(hits), 1)

    def test_provenance_garbage_names_the_fallback(self):
        spec = cs.build_spec({"SESSION_PROVENANCE_LOCK": "maybe"})
        self.assertIs(spec.session_provenance_lock, False)
        diags = [d for d in cs.spec_diagnostics()
                 if d["env_key"] == "SESSION_PROVENANCE_LOCK"]
        self.assertEqual(len(diags), 1)
        self.assertIn("SPEAKER_LOCK", diags[0]["reason"])

    def test_provenance_garbage_falls_back_to_legacy(self):
        spec = cs.build_spec({"SESSION_PROVENANCE_LOCK": "maybe",
                              "SPEAKER_LOCK": "true"})
        self.assertIs(spec.session_provenance_lock, True)

    def test_version_garbage_is_diagnosed(self):
        spec = cs.build_spec({"VYUK_CONFIG_VERSION": "xx"})
        self.assertEqual(spec.version, cs.CONFIG_VERSION)
        self.assertTrue(any(d["env_key"] == "VYUK_CONFIG_VERSION"
                            for d in cs.spec_diagnostics()))

    def test_diagnostics_are_copies(self):
        cs.build_spec({"SAMPLE_RATE_IN": "abc"})
        first = cs.spec_diagnostics()
        first.clear()
        self.assertTrue(cs.spec_diagnostics())

    def test_coercion_failure_predicate(self):
        self.assertIsNone(cs._coercion_failure(None, int))
        self.assertIsNone(cs._coercion_failure("", int))
        self.assertIsNone(cs._coercion_failure("42", int))
        self.assertIsNone(cs._coercion_failure("4.5", float))
        self.assertIsNone(cs._coercion_failure("yes", bool))
        self.assertIsNone(cs._coercion_failure("OFF", bool))
        self.assertIsNone(cs._coercion_failure("x", str))
        self.assertIsNotNone(cs._coercion_failure("abc", int))
        self.assertIsNotNone(cs._coercion_failure("4.5", int))
        self.assertIsNotNone(cs._coercion_failure("maybe", bool))


class EnvFileDiagnosticsTests(_TempEnvTests):
    def test_unknown_keys_recorded_keys_only(self):
        (self._root / ".env").write_text(
            "BRAIN_ENGINE=local\nSOME_UNKNOWN_KEY=hunter2\n"
            "CHANNELS=1\nVYUK_SAFE_MODE=1\n",
            encoding="utf-8")
        _env_mod.load_env_file()
        diags = _env_mod.env_file_diagnostics()
        self.assertEqual(
            [d["env_key"] for d in diags], ["SOME_UNKNOWN_KEY"])
        self.assertEqual(diags[0]["kind"], "unknown-key")
        self.assertNotIn("hunter2", str(diags))
        # A diagnostic is not a gate: the key still loads.
        self.assertEqual(os.environ["SOME_UNKNOWN_KEY"], "hunter2")

    def test_known_keys_are_not_flagged(self):
        (self._root / ".env").write_text(
            "BRAIN_ENGINE=local\nCHANNELS=1\nCHUNK_SIZE=1024\n"
            "NOVA_START_MINIMIZED=1\nVYUK_SAFE_MODE=1\n"
            "SPEAKER_LOCK=false\nVYUK_CONFIG_VERSION=1\n",
            encoding="utf-8")
        _env_mod.load_env_file()
        self.assertEqual(_env_mod.env_file_diagnostics(), [])

    def test_env_file_diagnostics_are_copies(self):
        (self._root / ".env").write_text("NOPE_KEY=1\n",
                                         encoding="utf-8")
        _env_mod.load_env_file()
        first = _env_mod.env_file_diagnostics()
        first.clear()
        self.assertTrue(_env_mod.env_file_diagnostics())


# ------------------------------------------- C-V2 / C-V3 / C-V5 / C-V9
class PinnedClosuresTests(unittest.TestCase):
    def test_c_v2_garbage_sample_rates_do_not_crash_import(self):
        spec = cs.build_spec({"SAMPLE_RATE_IN": "abc",
                              "SAMPLE_RATE_OUT": "xyz"})
        self.assertEqual((spec.sample_rate_in, spec.sample_rate_out),
                         (16000, 24000))

    def test_c_v3_no_direct_qsettings_in_appearance_page(self):
        text = (TREE / "gui" / "window_settings_appearance.py"
                ).read_text(encoding="utf-8")
        self.assertNotIn("QSettings(", text)
        self.assertIn("qsettings_get(", text)
        app = (TREE / "core" / "app_settings.py").read_text(
            encoding="utf-8")
        self.assertIn("from .config_spec import qsettings_get, "
                      "qsettings_set", app)

    def test_c_v5_cited_files_have_no_env_reads(self):
        for rel in ("core/voice/local_assets_model.py",
                    "core/funnel/tool_profiles.py",
                    "core/voice/cloud_text_helpers.py"):
            code = _code_text((TREE / rel).read_text(encoding="utf-8"))
            self.assertNotIn("os.getenv", code, rel)
            self.assertNotIn("os.environ", code, rel)

    def test_c_v9_design_adjudication_pinned(self):
        self.assertFalse((TREE / "core" / "compose.py").exists())
        self.assertFalse((TREE / "core" / "system_builder.py").exists())
        callers = []
        for path in [TREE / "main.py", TREE / "doctor.py"] + \
                list((TREE / "core").rglob("*.py")) + \
                list((TREE / "gui").rglob("*.py")):
            if "MainWindow(" in path.read_text(errors="replace") \
                    and path.name != "main_window.py":
                callers.append(str(path.relative_to(TREE)))
        self.assertEqual(callers, ["main.py"])
        # The Recipe half is done: one typed, versioned spec.
        self.assertEqual(cs.CONFIG_VERSION, 1)
        self.assertIn(0, cs.MIGRATIONS)


if __name__ == "__main__":
    unittest.main()

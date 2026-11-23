"""GAPS-10: the versioned config spec (flaw #2).

Before this batch the configuration was three stores plus env
reads scattered through the code, with no single place that said
what the configuration IS: no typed spec, no version field, no
migration path. `core/config_spec.py` is that place now — one
typed, versioned spec (CONFIG_VERSION == 1) that the whole app
reads through, with every default in exactly one place and a
registered migration path for old shapes. The central config
(`core/config.py`, AppConfig) is built from the spec, and the
scattered reads are migrated onto `config_spec.env()` (a live,
coerced read) or `config_spec.explicit()` (a raw probe for
"did the user explicitly set this?"). Behaviour is preserved:
every default that exists today comes out of the spec unchanged,
and the one deliberate change is pinned here — a garbled
SAMPLE_RATE_IN used to crash the import and now falls back to
16000 (a typo in .env must never take the assistant down).
"""

import dataclasses
import os
import pathlib
import tempfile
import unittest

from core import config_spec as cs
from core.config import AppConfig, config as _live_config
from core.settings_schema import FIELD_SPECS

TREE = pathlib.Path(__file__).resolve().parent.parent


def _mapping(**pairs):
    return dict(pairs)


class SpecShapeTests(unittest.TestCase):
    def test_version_is_1(self):
        self.assertEqual(cs.CONFIG_VERSION, 1)

    def test_field_counts(self):
        fields = dataclasses.fields(cs.ConfigSpec)
        # Round-3 fix #7 (A-UI4): 107 -> 106 fields, 102 -> 101
        # env-backed — the dead VYUK_-prefixed debug-port field
        # is gone (its env var could never take effect; the
        # working override is BROWSER_DEBUG_PORT via
        # browser_debug_port).
        # OAuth build: 106 -> 108 fields, 101 -> 103 env-backed —
        # gemini_oauth_client_id / gemini_oauth_client_secret
        # (GEMINI_OAUTH_CLIENT_ID / GEMINI_OAUTH_CLIENT_SECRET).
        # Round-3 wizard expansion: 108 -> 109 fields, 103 -> 104
        # env-backed — user_name (USER_NAME, the wizard's name ask).
        # Loophole batch 5: 109 -> 110 fields, 104 -> 105
        # env-backed — private_mode (PRIVATE_MODE). The pin was
        # NOT updated then, so it read stale (109/104 against a
        # live 110/105) until loophole batch 7 fixed it here.
        # Loophole batch 7: 110 -> 111 fields, 105 -> 106
        # env-backed — feedback_reporting_enabled
        # (FEEDBACK_REPORTING_ENABLED).
        # Stage 0 features batch 5: 111 -> 112 fields, 106 -> 107
        # env-backed — reply_verbosity (VYUK_REPLY_VERBOSITY).
        # Stage 0 features batch 6: 112 -> 113 fields, 107 -> 108
        # env-backed — calendar_quiet_enabled
        # (VYUK_CALENDAR_QUIET_ENABLED).
        self.assertEqual(len(fields), 113)
        env_backed = [f for f in fields if "env" in f.metadata]
        self.assertEqual(len(env_backed), 108)
        names = {f.name for f in fields}
        self.assertIn("version", names)
        self.assertIn("browser_allow_history_read", names)
        self.assertIn("vyuk_camoufox_lane", names)
        self.assertIn("vyuk_cost_daily_cap_usd", names)

    def test_secrets_are_repr_false(self):
        fields = {f.name: f for f in dataclasses.fields(cs.ConfigSpec)}
        for name in ("api_key", "groq_api_key", "google_client_secret",
                     "rustic_password", "gemini_oauth_client_secret"):
            self.assertTrue(fields[name].metadata.get("secret"), name)
            self.assertFalse(fields[name].repr, name)
        spec = cs.build_spec({})
        spec.api_key = "sk-test-secret"
        spec.groq_api_key = "gsk-test-secret"
        spec.gemini_oauth_client_secret = "oauth-test-secret"
        self.assertNotIn("sk-test-secret", repr(spec))
        self.assertNotIn("gsk-test-secret", repr(spec))
        self.assertNotIn("oauth-test-secret", repr(spec))

    def test_env_keys_are_unique(self):
        keys = [f.metadata["env"] for f in dataclasses.fields(cs.ConfigSpec)
                if "env" in f.metadata]
        self.assertEqual(len(keys), len(set(keys)))

    def test_aliases_are_three(self):
        self.assertEqual(cs.ENV_ALIAS, {
            "SPEAKER_LOCK": "SESSION_PROVENANCE_LOCK",
            "VYUK_ALLOW_REAL_CHROME_ATTACH":
                "BROWSER_ALLOW_REAL_CHROME_ATTACH",
            "VYUK_BROWSER_DEBUG_PROFILE_DIR":
                "BROWSER_DEBUG_PROFILE_DIR"})


class EnvExplicitTests(unittest.TestCase):
    def test_env_coerces_by_declared_type(self):
        m = _mapping(TOOL_ROUTER="0", GROQ_TEMPERATURE="0.5",
                     VOICE_NAME="Puck",
                     VYUK_COST_DAILY_CAP_USD="5.5",
                     VYUK_CAMOUFOX_LANE="1")
        self.assertIs(cs.env("TOOL_ROUTER", m), False)
        self.assertEqual(cs.env("GROQ_TEMPERATURE", m), 0.5)
        self.assertEqual(cs.env("VOICE_NAME", m), "Puck")
        self.assertEqual(cs.env("VYUK_COST_DAILY_CAP_USD", m), 5.5)
        self.assertEqual(cs.env("VYUK_CAMOUFOX_LANE", m), "1")

    def test_env_bool_spellings(self):
        for raw, want in (("1", True), ("true", True), ("yes", True),
                          ("on", True), ("0", False), ("false", False),
                          ("no", False), ("off", False),
                          ("garbage", True), ("", True)):
            self.assertIs(cs.env("TOOL_ROUTER", {"TOOL_ROUTER": raw}),
                          want, raw)

    def test_env_falls_back_on_unset_and_garbled(self):
        self.assertEqual(cs.env("SAMPLE_RATE_IN",
                                {"SAMPLE_RATE_IN": "garbage"}), 16000)
        self.assertEqual(cs.env("GROQ_MODEL", {}),
                         "llama-3.3-70b-versatile")
        self.assertIsNone(cs.env("VYUK_COST_MONTHLY_CAP_USD", {}))
        self.assertEqual(cs.env("VYUK_CAMOUFOX_LANE", {}), "")

    def test_env_raises_on_unknown_keys(self):
        with self.assertRaises(KeyError):
            cs.env("NOPE_KEY", {})
        with self.assertRaises(KeyError):
            cs.explicit("NOPE_KEY", {})

    def test_env_refuses_aliases_naming_the_canonical_key(self):
        with self.assertRaises(KeyError) as cm:
            cs.env("VYUK_ALLOW_REAL_CHROME_ATTACH", {})
        self.assertIn("BROWSER_ALLOW_REAL_CHROME_ATTACH", str(cm.exception))

    def test_explicit_returns_raw_or_none(self):
        self.assertEqual(cs.explicit("VOICE_NAME",
                                     {"VOICE_NAME": "Puck"}), "Puck")
        self.assertIsNone(cs.explicit("GROQ_MODEL", {}))
        self.assertEqual(cs.explicit("SAMPLE_RATE_IN",
                                     {"SAMPLE_RATE_IN": "garbage"}),
                         "garbage")

    def test_explicit_reads_an_alias_raw(self):
        self.assertEqual(
            cs.explicit("VYUK_ALLOW_REAL_CHROME_ATTACH",
                        {"VYUK_ALLOW_REAL_CHROME_ATTACH": "1"}), "1")
        self.assertIsNone(
            cs.explicit("VYUK_ALLOW_REAL_CHROME_ATTACH", {}))
        self.assertEqual(
            cs.explicit("SPEAKER_LOCK", {"SPEAKER_LOCK": "1"}), "1")


class ProvenanceAliasTests(unittest.TestCase):
    def test_legacy_speaker_lock_decides_when_primary_unset(self):
        self.assertIs(cs.env("SESSION_PROVENANCE_LOCK",
                             {"SPEAKER_LOCK": "1"}), True)
        self.assertIs(cs.env("SESSION_PROVENANCE_LOCK",
                             {"SPEAKER_LOCK": "0"}), False)

    def test_garbled_primary_falls_through_to_legacy(self):
        self.assertIs(cs.env(
            "SESSION_PROVENANCE_LOCK",
            {"SESSION_PROVENANCE_LOCK": "garbage",
             "SPEAKER_LOCK": "1"}), True)

    def test_recognisable_primary_wins_over_legacy(self):
        self.assertIs(cs.env(
            "SESSION_PROVENANCE_LOCK",
            {"SESSION_PROVENANCE_LOCK": "0",
             "SPEAKER_LOCK": "1"}), False)


class BuildSpecTests(unittest.TestCase):
    def test_build_spec_defaults(self):
        spec = cs.build_spec({})
        self.assertEqual(spec.voice_name, "Charon")
        self.assertIs(spec.tool_router, True)
        self.assertEqual(spec.version, 1)
        self.assertEqual(spec.tool_profile, "desktop")
        self.assertEqual(spec.groq_temperature, 0.6)

    def test_build_spec_overlays_the_mapping(self):
        spec = cs.build_spec({"VOICE_NAME": "Puck", "TOOL_ROUTER": "0"})
        self.assertEqual(spec.voice_name, "Puck")
        self.assertIs(spec.tool_router, False)

    def test_app_config_is_a_spec(self):
        self.assertIsInstance(_live_config, cs.ConfigSpec)
        fresh = AppConfig()
        self.assertEqual(fresh.voice_name, "Charon")
        fresh.tool_router = False
        self.assertIs(fresh.tool_router, False)


class MigrationTests(unittest.TestCase):
    def test_aoede_moves_to_charon(self):
        migrated, version = cs.migrate_stored(
            {"VOICE_NAME": "Aoede"}, 0, set())
        self.assertEqual(migrated["VOICE_NAME"], "Charon")
        self.assertEqual(version, 1)

    def test_a_deliberate_stored_voice_is_respected(self):
        migrated, _v = cs.migrate_stored({"VOICE_NAME": "Puck"}, 0, set())
        self.assertEqual(migrated["VOICE_NAME"], "Puck")

    def test_a_real_env_override_wins_over_the_file(self):
        migrated, _v = cs.migrate_stored(
            {"VOICE_NAME": "Aoede"}, 0, {"VOICE_NAME"})
        self.assertEqual(migrated["VOICE_NAME"], "Aoede")

    def test_runner_migrates_a_temp_env_file(self):
        from pathlib import Path
        from core import config as cfg_mod
        tmp = Path(tempfile.mkdtemp(prefix="config_spec_test_"))
        (tmp / ".env").write_text(
            "VOICE_NAME=Aoede\nGROQ_MODEL=llama-3.3-70b-versatile\n",
            encoding="utf-8")
        old_root = cfg_mod.PROJECT_ROOT
        old_voice = os.environ.get("VOICE_NAME")
        old_version = os.environ.get(cs.CONFIG_VERSION_KEY)
        try:
            cfg_mod.PROJECT_ROOT = tmp
            cfg_mod._run_config_migrations()
            after = (tmp / ".env").read_text(encoding="utf-8")
            self.assertIn("VOICE_NAME=Charon", after)
            self.assertIn("VYUK_CONFIG_VERSION=1", after)
            self.assertEqual(os.environ.get("VOICE_NAME"), "Charon")
            cfg_mod._run_config_migrations()
            self.assertEqual((tmp / ".env").read_text(encoding="utf-8"),
                             after)
        finally:
            cfg_mod.PROJECT_ROOT = old_root
            if old_voice is None:
                os.environ.pop("VOICE_NAME", None)
            else:
                os.environ["VOICE_NAME"] = old_voice
            if old_version is None:
                os.environ.pop(cs.CONFIG_VERSION_KEY, None)
            else:
                os.environ[cs.CONFIG_VERSION_KEY] = old_version

    def test_no_env_file_means_nothing_written(self):
        from pathlib import Path
        from core import config as cfg_mod
        tmp = Path(tempfile.mkdtemp(prefix="config_spec_test_"))
        old_root = cfg_mod.PROJECT_ROOT
        try:
            cfg_mod.PROJECT_ROOT = tmp
            cfg_mod._run_config_migrations()
            self.assertFalse((tmp / ".env").exists())
        finally:
            cfg_mod.PROJECT_ROOT = old_root


class QSettingsTableTests(unittest.TestCase):
    def test_table_matches_settings_schema(self):
        # GAPS-11: the table grew from 5 to the full QSettings
        # surface (90 entries). The five fields shared with
        # settings_schema.FIELD_SPECS must still agree on key +
        # default; the rest are spec-only keys.
        names = [n for n, _, _, _ in cs.QSETTINGS_FIELDS]
        keys = [k for _, k, _, _ in cs.QSETTINGS_FIELDS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(keys), len(set(keys)))
        for name, spec in FIELD_SPECS.items():
            if spec[3] is None:
                continue
            entry = next(e for e in cs.QSETTINGS_FIELDS if e[0] == name)
            self.assertEqual(entry[1], spec[3], name)
            self.assertEqual(entry[3], spec[1], name)
        defaults = cs.qsettings_defaults()
        self.assertEqual(len(defaults), len(cs.QSETTINGS_FIELDS))
        self.assertEqual(defaults["theme"], "Cyan")
        self.assertEqual(defaults["orb_style"], "Aura")
        self.assertEqual(defaults["sounds_enabled"], True)
        self.assertEqual(defaults["suggestions/enabled"], True)
        self.assertEqual(defaults["density"], "comfortable")
        for _name, _key, typ, _default in cs.QSETTINGS_FIELDS:
            self.assertIn(typ, (str, bool, int, float, list, object))


class ConsumerMigrationTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_camoufox_lane_is_a_live_spec_read(self):
        from core.desktop import browser_camoufox_lane as lane
        os.environ["VYUK_CAMOUFOX_LANE"] = "1"
        self.assertIs(lane.lane_enabled(), True)
        os.environ["VYUK_CAMOUFOX_LANE"] = "true"
        self.assertIs(lane.lane_enabled(), False)
        os.environ.pop("VYUK_CAMOUFOX_LANE", None)
        self.assertIs(lane.lane_enabled(), False)

    def test_history_opt_in_is_a_live_spec_read(self):
        from core.desktop import browser_history as bh
        os.environ["VYUK_ALLOW_BROWSER_HISTORY"] = "1"
        self.assertIs(bh._browser_history_allowed(), True)
        os.environ["VYUK_ALLOW_BROWSER_HISTORY"] = "garbage"
        self.assertIs(bh._browser_history_allowed(), False)
        os.environ.pop("VYUK_ALLOW_BROWSER_HISTORY", None)
        self.assertIs(bh._browser_history_allowed(), False)

    def test_cost_cap_reads_through_the_spec(self):
        from core.cost_ledger import _env_cap
        os.environ["VYUK_COST_DAILY_CAP_USD"] = "5.5"
        self.assertEqual(_env_cap("VYUK_COST_DAILY_CAP_USD"), 5.5)
        os.environ["VYUK_COST_DAILY_CAP_USD"] = "garbage"
        self.assertIsNone(_env_cap("VYUK_COST_DAILY_CAP_USD"))
        os.environ["VYUK_COST_DAILY_CAP_USD"] = "-3"
        self.assertIsNone(_env_cap("VYUK_COST_DAILY_CAP_USD"))
        os.environ.pop("VYUK_COST_DAILY_CAP_USD", None)
        self.assertIsNone(_env_cap("VYUK_COST_DAILY_CAP_USD"))

    def test_injection_second_pass_reads_through_the_spec(self):
        from core import injection_secondpass as isp
        os.environ["VYUK_INJECTION_SECOND_PASS"] = "0"
        self.assertIs(isp._env_second_pass_disabled(), True)
        os.environ["VYUK_INJECTION_SECOND_PASS"] = "1"
        self.assertIs(isp._env_second_pass_disabled(), False)
        os.environ.pop("VYUK_INJECTION_SECOND_PASS", None)
        self.assertIs(isp._env_second_pass_disabled(), False)

    def test_groq_temperature_reads_through_the_spec(self):
        from core.voice import cloud_text_helpers as cth
        os.environ["GROQ_TEMPERATURE"] = "0.25"
        self.assertEqual(cth._groq_temperature(), 0.25)
        os.environ["GROQ_TEMPERATURE"] = "garbage"
        self.assertEqual(cth._groq_temperature(),
                         cth.DEFAULT_TEMPERATURE)
        os.environ.pop("GROQ_TEMPERATURE", None)
        self.assertEqual(cth._groq_temperature(),
                         cth.DEFAULT_TEMPERATURE)

    def test_groq_model_falls_back_to_the_downstream_default(self):
        from core.voice import cloud_text_helpers as cth
        os.environ.pop("GROQ_MODEL", None)
        self.assertEqual(cth._groq_model(), cth.DEFAULT_GROQ_MODEL)
        os.environ["GROQ_MODEL"] = "llama-3.1-8b-instant"
        self.assertEqual(cth._groq_model(), "llama-3.1-8b-instant")


if __name__ == "__main__":
    unittest.main()

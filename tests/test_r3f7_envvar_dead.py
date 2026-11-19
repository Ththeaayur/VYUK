"""Round-3 fix #7 (A-UI4): VYUK_BROWSER_DEBUG_PORT is dead — pin its removal.

The VYUK_-prefixed env var could never take effect: `_cdp_port()`
read the config field `browser_debug_port` (default 9222, working
env `BROWSER_DEBUG_PORT`) first and only consulted
`config_spec.explicit("VYUK_BROWSER_DEBUG_PORT")` when that was
None, which never happens in production — and the ConfigSpec
field `vyuk_browser_debug_port` was read by nothing at all.
GAPS-11 removed the identical dead-leg shape for
VYUK_ALLOW_REAL_CHROME_ATTACH; this fix does the same for the
debug port. The working override is BROWSER_DEBUG_PORT, via the
field — and the Settings UI persists exactly that name.

Pre-fix: test_stubbed_config_ignores_vyuk_var,
test_no_vyuk_field_in_configspec, test_no_vyuk_env_key_in_configspec
and test_no_vyuk_reference_in_tree FAIL (the dead leg fires under
a stubbed config; the field, the env key and the code references
exist). The remaining tests pass by design — they pin the
surviving behaviour the removal must not break.
Post-fix: all 9 pass on both interpreters.
"""
import dataclasses
import os
import pathlib
import unittest
from unittest import mock

from core import config as cfg_mod
from core import config_spec as cs
from core.desktop import browser_cdp as cdp

ROOT = pathlib.Path(__file__).resolve().parents[1]
THIS_FILE = "tests/test_r3f7_envvar_dead.py"


class EnvVarDeadTests(unittest.TestCase):
    def test_stubbed_config_ignores_vyuk_var(self):
        # The only shape in which the dead leg could fire (the
        # GAPS-11 shape): the config read stubbed to None. Even
        # then the VYUK var must not take effect.
        with mock.patch.dict(os.environ, {"VYUK_BROWSER_DEBUG_PORT": "9333"}):
            with mock.patch.object(cdp, "_config_value", return_value=None):
                self.assertEqual(cdp._cdp_port(), 9222)

    def test_working_override_still_works(self):
        # The surviving route: the AppConfig field honours the port.
        with mock.patch.object(cfg_mod.config, "browser_debug_port", 9333):
            self.assertEqual(cdp._cdp_port(), 9333)

    def test_default_is_9222(self):
        with mock.patch.object(cfg_mod.config, "browser_debug_port", 9222):
            self.assertEqual(cdp._cdp_port(), 9222)

    def test_garbage_port_falls_back_to_default(self):
        with mock.patch.object(cdp, "_config_value", return_value="garbage"):
            self.assertEqual(cdp._cdp_port(), 9222)

    def test_out_of_range_port_falls_back_to_default(self):
        with mock.patch.object(cdp, "_config_value", return_value=80):
            self.assertEqual(cdp._cdp_port(), 9222)

    def test_no_vyuk_field_in_configspec(self):
        names = {f.name for f in dataclasses.fields(cs.ConfigSpec)}
        self.assertNotIn("vyuk_browser_debug_port", names)

    def test_no_vyuk_env_key_in_configspec(self):
        keys = {f.metadata["env"] for f in dataclasses.fields(cs.ConfigSpec)
                if "env" in f.metadata}
        self.assertNotIn("VYUK_BROWSER_DEBUG_PORT", keys)

    def test_browser_debug_port_field_still_exists(self):
        # Guard against over-removal: the working field stays, with
        # the working env name.
        fields = {f.name: f for f in dataclasses.fields(cs.ConfigSpec)}
        self.assertIn("browser_debug_port", fields)
        self.assertEqual(fields["browser_debug_port"].metadata.get("env"),
                         "BROWSER_DEBUG_PORT")

    def test_no_vyuk_reference_in_tree(self):
        # The strings may appear nowhere in the tree except this
        # suite's own explanation (its docstring + this test).
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if str(path.relative_to(ROOT)) == THIS_FILE:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if ("VYUK_BROWSER_DEBUG_PORT" in text
                    or "vyuk_browser_debug_port" in text):
                hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()

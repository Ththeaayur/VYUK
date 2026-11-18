"""Plugin loader: a user-added plugin with a module-level @dataclass
must load (field fix #24 follow-up).

The loader used to exec_module the plugin file without registering
it in sys.modules. A @dataclass whose annotations are strings —
the normal shape with `from __future__ import annotations` — is
resolved through sys.modules[__name__].__dict__ by dataclasses,
so the import died with "'NoneType' object has no attribute
'__dict__'" and the plugin was silently skipped. The fix registers
the module in sys.modules before exec_module (the importlib
recipe, same one line as core/personal_skills.py in fix #24).

Harness mirrors tests/test_gateC.py: a temp plugins dir + temp
data dir, an empty (unsigned) manifest, PLUGINS_DIR/DATA_DIR
patched, an approve-all trust-prompt handler. Nothing touches the
real plugins/ or data/.
"""
import sys
import tempfile
import unittest
from pathlib import Path

from core import plugin_loader as plugin_mod
from core.plugin_loader import discover_plugins

DC_FUTURE_PLUGIN = '''# vyuk-plugin: capabilities=read
from __future__ import annotations

from dataclasses import dataclass

@dataclass
class Config:
    label: str = "default"
    retries: int = 3

PLUGIN = {"name": "dc_tool", "description": "fixture with a module-level dataclass"}

def run(**kw):
    return f"ok {Config().label}"
'''

DC_PLAIN_PLUGIN = '''# vyuk-plugin: capabilities=read
from dataclasses import dataclass

@dataclass
class Config:
    label: str = "default"
    retries: int = 3

PLUGIN = {"name": "dc_plain_tool", "description": "dataclass with real-type annotations"}

def run(**kw):
    return f"ok {Config().label}"
'''

PLAIN_PLUGIN = '''# vyuk-plugin: capabilities=read
PLUGIN = {"name": "plain_tool", "description": "control fixture, no dataclass"}

def run(**kw):
    return "ok plain"
'''

DC_A_PLUGIN = '''# vyuk-plugin: capabilities=read
from __future__ import annotations

from dataclasses import dataclass

@dataclass
class Config:
    name: str = "a"

PLUGIN = {"name": "dc_a_tool", "description": "first dataclass plugin"}

def run(**kw):
    return f"a {Config().name}"
'''

DC_B_PLUGIN = '''# vyuk-plugin: capabilities=read
from __future__ import annotations

from dataclasses import dataclass

@dataclass
class Config:
    name: str = "b"

PLUGIN = {"name": "dc_b_tool", "description": "second dataclass plugin"}

def run(**kw):
    return f"b {Config().name}"
'''

STEMS = ("dc_plugin", "dc_plain_plugin", "plain_plugin",
         "dc_a_plugin", "dc_b_plugin")


class PluginLoaderDataclassTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.plugdir = self.dir / "plugins"
        self.plugdir.mkdir()
        self.datadir = self.dir / "data"
        self.datadir.mkdir()
        (self.plugdir / "plugins_hashes.json").write_text(
            "{}\n", encoding="utf-8")
        self._old_plugdir = plugin_mod.PLUGINS_DIR
        self._old_datadir = plugin_mod.DATA_DIR
        plugin_mod.PLUGINS_DIR = self.plugdir
        plugin_mod.DATA_DIR = self.datadir
        self.addCleanup(setattr, plugin_mod, "PLUGINS_DIR",
                        self._old_plugdir)
        self.addCleanup(setattr, plugin_mod, "DATA_DIR",
                        self._old_datadir)
        plugin_mod.set_trust_prompt_handler(lambda *a: True)
        self.addCleanup(plugin_mod.clear_trust_prompt_handler)
        self.addCleanup(plugin_mod.clear_plugin_cache)
        for stem in STEMS:
            sys.modules.pop(stem, None)
        self.addCleanup(self._drop_stems)

    def _drop_stems(self):
        for stem in STEMS:
            sys.modules.pop(stem, None)

    def _write(self, filename, content):
        (self.plugdir / filename).write_text(content, encoding="utf-8")

    def _discover(self):
        return discover_plugins(existing_names=set(), refresh=True)

    def test_dataclass_plugin_with_future_annotations_loads(self):
        """The pin: a module-level @dataclass under `from __future__
        import annotations` (string annotations) used to crash the
        load with 'NoneType' object has no attribute '__dict__'."""
        self._write("dc_plugin.py", DC_FUTURE_PLUGIN)
        found = self._discover()
        self.assertEqual([p["name"] for p in found], ["dc_tool"])

    def test_dataclass_is_usable_after_load(self):
        self._write("dc_plugin.py", DC_FUTURE_PLUGIN)
        self._discover()
        mod = sys.modules["dc_plugin"]
        cfg = mod.Config()
        self.assertEqual((cfg.label, cfg.retries), ("default", 3))
        cfg2 = mod.Config(label="hi", retries=2)
        self.assertEqual((cfg2.label, cfg2.retries), ("hi", 2))
        self.assertEqual(mod.run(), "ok default")

    def test_loaded_module_registered_in_sys_modules(self):
        self._write("dc_plugin.py", DC_FUTURE_PLUGIN)
        found = self._discover()
        self.assertEqual([p["name"] for p in found], ["dc_tool"])
        mod = sys.modules.get("dc_plugin")
        self.assertIsNotNone(mod, "loader did not register the module")
        self.assertTrue(hasattr(mod, "Config"))
        self.assertEqual(str(getattr(mod, "__file__", "")),
                         str(self.plugdir / "dc_plugin.py"))

    def test_dataclass_without_future_annotations_loads(self):
        """Real-type annotations never hit the sys.modules lookup,
        so the plugin is DISCOVERED even pre-fix — but the module
        is only reachable through sys.modules post-fix, which is
        what the .run() line pins."""
        self._write("dc_plain_plugin.py", DC_PLAIN_PLUGIN)
        found = self._discover()
        self.assertEqual([p["name"] for p in found], ["dc_plain_tool"])
        self.assertEqual(sys.modules["dc_plain_plugin"].run(),
                         "ok default")

    def test_plain_plugin_without_dataclass_still_loads(self):
        self._write("plain_plugin.py", PLAIN_PLUGIN)
        found = self._discover()
        self.assertEqual([p["name"] for p in found], ["plain_tool"])

    def test_two_dataclass_plugins_both_load(self):
        """Registration is per module stem: two dataclass plugins
        in one scan must both load and keep their own Config."""
        self._write("dc_a_plugin.py", DC_A_PLUGIN)
        self._write("dc_b_plugin.py", DC_B_PLUGIN)
        found = self._discover()
        self.assertEqual([p["name"] for p in found],
                         ["dc_a_tool", "dc_b_tool"])
        self.assertEqual(sys.modules["dc_a_plugin"].Config().name, "a")
        self.assertEqual(sys.modules["dc_b_plugin"].Config().name, "b")


if __name__ == "__main__":
    unittest.main()

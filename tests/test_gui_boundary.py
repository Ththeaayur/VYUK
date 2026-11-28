"""GAPS-15 suite: the GUI boundary (architecture-review flaw #6).

The boundary: a gui/*.py file may import core ONLY through
core.gui_facade (either spelling); a core/*.py file may not import
gui in any form; no core file imports the facade (core code imports
its source modules directly). The facade is lazy (PEP 562), so
importing it can never pull Qt/GUI in, and every exported name must
be the IDENTICAL object as the direct import in the same spelling
family (or raise the same ModuleNotFoundError on an interpreter
that lacks the dependency — sounddevice / PySide6 on system
python3).

Runs standalone: python3 -m unittest tests.test_gui_boundary
from the tree root, on BOTH interpreters (system python3 and the
Qt venv with QT_QPA_PLATFORM=offscreen).
"""
import ast
import importlib
import inspect
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

CORE_MOD = re.compile(r"^(core|voice_assistant\.core)(\..+)?$")
GUI_MOD = re.compile(r"^(gui|voice_assistant\.gui)(\..+)?$")
LAZY_CORE = re.compile(r"""import_module\(\s*["'](?:voice_assistant\.)?core""")
LAZY_GUI = re.compile(r"""import_module\(\s*["'](?:voice_assistant\.)?gui""")
FROM_VA_CORE = re.compile(r"^\s*from\s+voice_assistant\s+import\s+.*\bcore\b")
FROM_VA_GUI = re.compile(r"^\s*from\s+voice_assistant\s+import\s+.*\bgui\b")


def _is_core_import(node):
    """A core import other than the facade itself (relatives count)."""
    if isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        if not mod or mod.endswith("gui_facade"):
            return False
        return bool(CORE_MOD.match(mod))
    if isinstance(node, ast.Import):
        return any(a.name == "core" or a.name.startswith("core.")
                   or a.name.startswith("voice_assistant.core")
                   for a in node.names
                   if not a.name.endswith("gui_facade"))
    return False


def _is_gui_import(node):
    if isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        if not mod:
            return False
        return bool(GUI_MOD.match(mod))
    if isinstance(node, ast.Import):
        return any(a.name == "gui" or a.name.startswith("gui.")
                   or a.name.startswith("voice_assistant.gui")
                   for a in node.names)
    return False


def _is_facade_import(node):
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").endswith("gui_facade")
    if isinstance(node, ast.Import):
        return any(a.name.endswith("gui_facade") for a in node.names)
    return False


def _va_import_lines(text, pkg):
    pat = FROM_VA_CORE if pkg == "core" else FROM_VA_GUI
    return [i + 1 for i, ln in enumerate(text.splitlines())
            if pat.search(ln)]


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8", errors="replace"))


class GuiBoundaryTests(unittest.TestCase):
    def test_00_facade_import_pulls_no_gui(self):
        """Importing the facade must not load any gui module."""
        import core.gui_facade  # noqa: F401  (the import IS the test)
        loaded = [m for m in sys.modules
                  if m == "gui" or m.startswith("gui.")
                  or m == "voice_assistant.gui"
                  or m.startswith("voice_assistant.gui.")]
        self.assertEqual(loaded, [],
                         f"facade import pulled GUI in: {loaded}")

    def test_01_no_gui_imports_core_directly(self):
        """Every gui/*.py file reaches core only via the facade."""
        viol = []
        for f in sorted((ROOT / "gui").rglob("*.py")):
            text = f.read_text(encoding="utf-8", errors="replace")
            tree = _parse(f)
            for node in ast.walk(tree):
                if _is_core_import(node):
                    viol.append((str(f.relative_to(ROOT)), node.lineno,
                                 getattr(node, "module", "?")))
            for i, ln in enumerate(text.splitlines()):
                if LAZY_CORE.search(ln):
                    viol.append((str(f.relative_to(ROOT)), i + 1, "lazy"))
            for ln in _va_import_lines(text, "core"):
                viol.append((str(f.relative_to(ROOT)), ln,
                             "from-voice_assistant"))
        self.assertEqual(viol, [], f"direct core imports: {viol[:5]}")

    def test_02_no_core_imports_gui(self):
        """No core/*.py file imports gui in any form."""
        viol = []
        for f in sorted((ROOT / "core").rglob("*.py")):
            text = f.read_text(encoding="utf-8", errors="replace")
            tree = _parse(f)
            for node in ast.walk(tree):
                if _is_gui_import(node):
                    viol.append((str(f.relative_to(ROOT)), node.lineno,
                                 getattr(node, "module", "?")))
            for i, ln in enumerate(text.splitlines()):
                if LAZY_GUI.search(ln):
                    viol.append((str(f.relative_to(ROOT)), i + 1, "lazy"))
            for ln in _va_import_lines(text, "gui"):
                viol.append((str(f.relative_to(ROOT)), ln,
                             "from-voice_assistant"))
        self.assertEqual(viol, [], f"core->GUI imports: {viol[:5]}")

    def test_03_no_core_imports_facade(self):
        """Core code imports its source modules directly, never the
        facade (the facade is the GUI-facing surface only)."""
        viol = []
        for f in sorted((ROOT / "core").rglob("*.py")):
            for node in ast.walk(_parse(f)):
                if _is_facade_import(node):
                    viol.append((str(f.relative_to(ROOT)), node.lineno))
        self.assertEqual(viol, [], f"core imports facade: {viol[:5]}")

    def test_04_facade_exports_everything_gui_imports(self):
        """Every name GUI imports from the facade exists in it."""
        import core.gui_facade as fac
        exported = set(fac.__all__)
        used = set()
        for f in sorted((ROOT / "gui").rglob("*.py")):
            for node in ast.walk(_parse(f)):
                if isinstance(node, ast.ImportFrom) and \
                        (node.module or "").endswith("gui_facade"):
                    used.update(a.name for a in node.names)
        self.assertTrue(used, "no GUI file imports the facade?")
        self.assertEqual(sorted(used - exported), [],
                         f"unknown facade names: {sorted(used - exported)}")

    def test_05_collision_resolutions(self):
        """The two renamed collisions resolve to the right objects:
        `config` is the AppConfig instance, `config_module` the
        core.config module; `live_session` the brain session,
        `voice_live_session` the voice session."""
        import core.gui_facade as fac
        from core.config import config as direct_config
        import core.config as config_module
        from core.brain.brain import live_session as brain_session
        from core.voice.live_client import live_session as voice_session
        self.assertIs(fac.config, direct_config)
        self.assertIs(fac.config_module, config_module)
        self.assertIs(fac.live_session, brain_session)
        self.assertIs(fac.voice_live_session, voice_session)
        self.assertIsNot(fac.config, fac.config_module)
        self.assertIsNot(fac.live_session, fac.voice_live_session)

    def test_06_icon_manifest_reexports(self):
        """gui/icon_manifest.py re-exports the core resolver objects
        (moved to core/icon_manifest.py, GAPS-15)."""
        import core.gui_facade as fac
        import core.icon_manifest as core_icons
        import gui.icon_manifest as gui_icons
        for name in ("IconRef", "ManifestProblem", "validate_icon_name",
                     "resolve", "build_manifest", "validate_manifest",
                     "PRIMARY_SYSTEM", "FLUENT_WINDOWS_ACTIONS"):
            self.assertIs(getattr(gui_icons, name),
                          getattr(core_icons, name), name)
            self.assertIs(getattr(fac, name),
                          getattr(core_icons, name), name)

    def test_07_roster_lives_in_core(self):
        """The Kokoro voice roster is voice data: it lives in
        core/languages.py; gui/window_widgets_helpers.py only
        re-exports it from the facade."""
        import core.languages as lang
        import gui.window_widgets_helpers as helpers
        self.assertEqual(len(lang._KOKORO_VOICE_ROSTER), 12)
        self.assertEqual(lang._KOKORO_VOICE_ROSTER[0],
                         ("af_heart", "Heart — warm"))
        self.assertIs(helpers._KOKORO_VOICE_ROSTER,
                      lang._KOKORO_VOICE_ROSTER)
        text = (ROOT / "gui" / "window_widgets_helpers.py").read_text(
            encoding="utf-8")
        self.assertNotIn("_KOKORO_VOICE_ROSTER: tuple", text)

    def test_08_themes_come_from_core(self):
        """core/app_settings._themes() reads the DTCG source in core
        (core.tokens.theme_names), not gui/style.py — same 12 names,
        same order (Stage 0 features batch 5 adds Light, last)."""
        from core.app_settings import _themes
        from core.tokens import theme_names
        self.assertEqual(_themes(), list(theme_names()))
        self.assertEqual(_themes(), ["Cyan", "Sunset", "Violet",
                                     "Graphite", "Aurora", "Ember",
                                     "Mono", "Rose", "Contrast",
                                     "Amber", "Crimson", "Light"])
        text = (ROOT / "core" / "app_settings.py").read_text(
            encoding="utf-8")
        self.assertNotIn("from ..gui", text)

    def test_09_tokens_outputs_takes_shipped_style(self):
        """The DTCG comparison takes the shipped style as an explicit
        argument (injection, not a core->GUI import)."""
        from core import tokens_outputs as to
        sig = inspect.signature(to.generate_stylesheet)
        self.assertEqual(list(sig.parameters), ["theme_name", "shipped"])
        sig = inspect.signature(to.prove_byte_identity)
        self.assertEqual(list(sig.parameters),
                         ["shipped", "max_diff_lines"])
        self.assertEqual(sig.parameters["max_diff_lines"].default, 60)
        sig = inspect.signature(to.proof_table_text)
        self.assertIsNone(sig.parameters["proof"].default)
        self.assertIsNone(sig.parameters["shipped"].default)
        for node in ast.walk(_parse(ROOT / "core" / "tokens_outputs.py")):
            self.assertFalse(_is_gui_import(node),
                             "tokens_outputs imports GUI")

    def test_10_wiring_submodules_moved(self):
        """The three Qt-building wiring submodules live in gui/ now,
        not core/; w9_wiring.py no longer re-exports them, and its
        icon imports are core-internal."""
        for name in ("w9_wiring_theme", "w9_wiring_summon",
                     "w9_wiring_proof"):
            self.assertTrue((ROOT / "gui" / f"{name}.py").exists(), name)
            self.assertFalse((ROOT / "core" / f"{name}.py").exists(),
                             name)
        tree = _parse(ROOT / "core" / "w9_wiring.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                self.assertNotIn("w9_wiring_theme", mod)
                self.assertNotIn("w9_wiring_summon", mod)
                self.assertNotIn("w9_wiring_proof", mod)
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotIn("w9_wiring_theme", a.name)
                    self.assertNotIn("w9_wiring_summon", a.name)
                    self.assertNotIn("w9_wiring_proof", a.name)
        for node in ast.walk(_parse(ROOT / "core" / "w9_wiring.py")):
            self.assertFalse(_is_gui_import(node),
                             "w9_wiring imports GUI")

    def test_11_facade_names_match_direct_imports(self):
        """Every facade name is the IDENTICAL object as the direct
        import (same spelling family), or raises the same
        ModuleNotFoundError the direct import raises on this
        interpreter (sounddevice / PySide6 absent on system
        python3)."""
        import core.gui_facade as fac
        resolved = same_error = 0
        problems = []
        for name, (rel, attr) in sorted(fac._EXPORTS.items()):
            # Most entries are relative (".module" -> "core.module").
            # A few are absolute top-level imports ("core.module")
            # — the money/review-inbox entries resolve through the
            # top-level core package so they read the real memory
            # store under the test harness (the aliased
            # voice_assistant.core.memory.memory is stubbed).
            # Those are already absolute; do not prefix them.
            modname = rel if not rel.startswith(".") else "core" + rel
            try:
                got = getattr(fac, name)
            except ModuleNotFoundError as e_fac:
                try:
                    mod = importlib.import_module(modname)
                    direct = getattr(mod, attr) if attr else mod
                except ModuleNotFoundError as e_dir:
                    if type(e_fac) is type(e_dir) and \
                            str(e_fac) == str(e_dir):
                        same_error += 1
                        continue
                    problems.append((name, "different error",
                                     str(e_fac), str(e_dir)))
                    continue
                problems.append((name, "facade raised, direct did not",
                                 str(e_fac)))
                continue
            try:
                mod = importlib.import_module(modname)
                direct = getattr(mod, attr) if attr else mod
            except ModuleNotFoundError as e_dir:
                problems.append((name, "direct raised, facade did not",
                                 str(e_dir)))
                continue
            if got is not direct:
                problems.append((name, "different object"))
            else:
                resolved += 1
        self.assertEqual(problems, [], f"mismatches: {problems[:5]}")
        self.assertEqual(resolved + same_error, len(fac._EXPORTS))


if __name__ == "__main__":
    unittest.main()

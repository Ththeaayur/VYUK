"""Packaging batch — wiring audit WIR-01, WIR-02, WIR-12 + duplicate
overrides. Working-copy only; no frozen build is claimed (spec-side
only, Windows compile validation still owed).

Budget discipline (testiso guest rule): this suite performs NO
funnel executes, so it cannot trip the process-global tool-call
budget; the registry build at import time registers tools but
executes none. Module-global state touched here
(PACK_REGISTRATION_FAILURES, domain_routers._GROUPS_CACHE) is
snapshotted and restored byte-for-byte.
"""

import ast
import sys
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent


class _FakeRegistry:
    """Minimal registry surface for pack register_all calls."""

    def __init__(self):
        self._tools = {}

    def register(self, name, description, parameters, handler):
        self._tools[name] = {"handler": handler, "description": description}


def _spec_text():
    return (TREE / "installer" / "vyuk.spec").read_text(encoding="utf-8")


def _spec_hiddenimports():
    parsed = ast.parse(_spec_text())
    for node in ast.walk(parsed):
        if isinstance(node, ast.Call):
            fname = node.func.id if isinstance(node.func, ast.Name) else ""
            if fname == "Analysis":
                for kw in node.keywords:
                    if kw.arg == "hiddenimports" and isinstance(kw.value, ast.List):
                        return {e.value for e in kw.value.elts
                                if isinstance(e, ast.Constant)}
    raise AssertionError("hiddenimports= not found in installer/vyuk.spec")


def _pack_modules():
    """tools_handlers __import__ loop tuples (the WIR-01 core).

    Refactor Phase 1 Step 1 moved most pack loops out of
    tools_handlers.py into the core/tools_handlers_pack_*.py and core/funnel/tools_handlers_pack_*.py
    modules; the derivation follows them there, so the assertions
    below keep checking the FULL loop set (unchanged guard and
    unchanged missing-set assertion), not just the loops left
    behind in tools_handlers.py."""
    paths = [TREE / "core" / "funnel" / "tools_handlers.py"] + sorted(
        (TREE / "core").glob("tools_handlers_pack_*.py")) + sorted(
        (TREE / "core" / "funnel").glob("tools_handlers_pack_*.py"))  # Phase 3 F3 INTENTIONAL: pack enumeration re-pointed — 9 pack files moved to core/funnel/; hands/waves stay flat
    mods = []
    for path in paths:
        parsed = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(parsed):
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
                try:
                    vals = [ast.literal_eval(e) for e in node.iter.elts]
                except Exception:
                    continue
                if vals and all(isinstance(v, str) for v in vals) \
                        and "__import__" in ast.dump(node):
                    mods.extend(vals)
    return mods


def _live_groups():
    # GAPS-23 (F-V7): the tables moved to core/catalogue_groups_data.py
    # (pure data, no Qt) — imported directly, no AST re-parse.
    from core.catalogue_groups_data import GROUP_MEMBERS

    return {k: tuple(v) for k, v in GROUP_MEMBERS.items()}


class SpecTests(unittest.TestCase):
    def test_spec_covers_all_pack_modules(self):
        """WIR-01 (core): every dynamically-imported pack module is
        a spec hiddenimport in frozen namespace form."""
        hidden = _spec_hiddenimports()
        mods = _pack_modules()
        self.assertGreater(len(mods), 40, "pack module derivation collapsed")
        missing = {f"voice_assistant.core.{m}" for m in mods} - hidden
        self.assertEqual(missing, set())

    def test_spec_carries_catalogue_datas(self):
        """WIR-02 (spec half): the catalogue data module rides in the
        bundle via import tracing (it is imported by the GUI shim and
        by core/brain/domain_routers), NOT as a datas entry —
        GAPS-23 (F-V7) removed the gui/skills_catalog_data.py datas
        entry, because nothing reads that file as data anymore."""
        text = _spec_text()
        # No datas ENTRY for the file (the name still appears in the
        # comment that records the removal — that is the point).
        self.assertNotIn(
            'os.path.join(TREE_DIR, "gui", "skills_catalog_data.py")',
            text)
        from core.catalogue_groups_data import GROUP_MEMBERS

        self.assertGreater(len(GROUP_MEMBERS), 0)

    def test_checker_covers_all_dynamic_imports(self):
        """WIR-01 (checker half): the code-lab checker derives the
        full dynamic set and the spec covers it. Skipped when the
        lab layout is absent (the checker itself is verified by
        direct runs in the lab, incl. its negative test)."""
        code_lab = TREE.parent.parent.parent
        checker = code_lab / "check_packaging.py"
        if not checker.exists():
            self.skipTest("code-lab checker not present in this layout")
        sys.path.insert(0, str(code_lab))
        try:
            import check_packaging
        finally:
            sys.path.remove(str(code_lab))
        missing, fresh = check_packaging.check()
        self.assertEqual(missing, set())
        self.assertTrue(fresh)

    def test_checker_honours_explicit_tree_without_global_repoint(self):
        """tree_root half-wiring, completed 2026-11-03: check()
        against a non-live tree via explicit tree_root/spec_path,
        with the module-global TREE untouched afterwards.
        Previously this died in _package_for with ValueError (it
        resolved paths against the global, not the passed root),
        so proving anything about another tree required
        repointing the global. Skipped when the lab layout is
        absent, like the checker-half test above. The fixture is
        a synthetic temp tree, not a backup dir, so the test
        stays hermetic: it exercises the exact divergence point
        (package-prefix resolution under a foreign root)."""
        import tempfile
        code_lab = TREE.parent.parent.parent
        checker = code_lab / "check_packaging.py"
        if not checker.exists():
            self.skipTest("code-lab checker not present in this layout")
        sys.path.insert(0, str(code_lab))
        try:
            import check_packaging
        finally:
            sys.path.remove(str(code_lab))
        before = check_packaging.TREE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "core").mkdir()
            (root / "installer").mkdir()
            (root / "main.py").write_text(
                "import importlib\n"
                "mod = importlib.import_module(\"core.alpha\")\n",
                encoding="utf-8")
            (root / "doctor.py").write_text(
                "_MODULES = [\"core.beta\"]\n", encoding="utf-8")
            # GAPS-23 (F-V7): the checker reads GROUP_MEMBERS from
            # core/catalogue_groups_data.py, where the tables live now.
            (root / "core" / "catalogue_groups_data.py").write_text(
                "GROUP_MEMBERS = {\"Group\": (\"tool_a\",)}\n",
                encoding="utf-8")
            (root / "core" / "catalogue_snapshot.py").write_text(
                "GROUP_MEMBERS = {\"Group\": (\"tool_a\",)}\n",
                encoding="utf-8")
            hidden = sorted(check_packaging.OPTIONAL_THIRD_PARTY
                            | {"voice_assistant.core.alpha"})
            (root / "installer" / "vyuk.spec").write_text(
                "a = Analysis([\"main.py\"], hiddenimports=["
                + ", ".join(repr(h) for h in hidden) + "])\n",
                encoding="utf-8")
            missing, fresh = check_packaging.check(
                root, root / "installer" / "vyuk.spec")
            self.assertEqual(missing, {"voice_assistant.core.beta"})
            self.assertTrue(fresh)
        self.assertEqual(check_packaging.TREE, before)


class CatalogueFallbackTests(unittest.TestCase):
    def setUp(self):
        from core.brain import domain_routers as rm  # Phase 3 B1b INTENTIONAL: domain_routers moved to core/brain/
        self.rm = rm
        self._cache = rm._GROUPS_CACHE
        self._source_fn = rm._catalogue_source
        self._snap_fn = rm._snapshot_groups

    def tearDown(self):
        self.rm._GROUPS_CACHE = self._cache
        self.rm._catalogue_source = self._source_fn
        self.rm._snapshot_groups = self._snap_fn

    def test_snapshot_matches_live_catalogue(self):
        """WIR-02: the embedded snapshot is not a stale copy."""
        from core import catalogue_snapshot
        self.assertEqual(
            {k: tuple(v) for k, v in catalogue_snapshot.GROUP_MEMBERS.items()},
            _live_groups())

    def test_missing_source_uses_snapshot_once(self):
        """WIR-02: an unimportable catalogue source degrades to the
        snapshot (same groups), never re-imports, and never guesses
        a grouping."""
        self.rm._GROUPS_CACHE = None
        self.rm._catalogue_source = lambda: (_ for _ in ()).throw(
            ImportError("core.catalogue_groups_data gone"))
        groups = self.rm.catalogue_groups()
        self.assertEqual(groups, _live_groups())
        # Second call serves the cache, not a second import.
        self.assertIs(groups, self.rm.catalogue_groups())

    def test_missing_source_and_snapshot_raises_naming_source(self):
        """WIR-02: when BOTH source and snapshot are gone, the
        failure is loud and names the catalogue source module."""
        self.rm._GROUPS_CACHE = None
        self.rm._catalogue_source = lambda: (_ for _ in ()).throw(
            ImportError("core.catalogue_groups_data gone"))
        self.rm._snapshot_groups = lambda: (_ for _ in ()).throw(
            RuntimeError("snapshot gone"))
        with self.assertRaises(ValueError) as ctx:
            self.rm.catalogue_groups()
        self.assertIn("core/catalogue_groups_data", str(ctx.exception))


class PackFailureTests(unittest.TestCase):
    def setUp(self):
        from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        self.th = th
        self._failures = list(th.PACK_REGISTRATION_FAILURES)

    def tearDown(self):
        self.th.PACK_REGISTRATION_FAILURES[:] = self._failures

    def test_loop_pack_failure_recorded_and_rest_load(self):
        """WIR-12: a broken loop-pack module is recorded (pack +
        module + error) while its sibling modules still load."""
        from core import skills_system
        real = skills_system.register_all
        skills_system.register_all = lambda registry: (_ for _ in ()).throw(
            RuntimeError("boom"))
        try:
            fake = _FakeRegistry()
            self.th.ToolHandlersMixin._register_mega_tools(fake)
        finally:
            skills_system.register_all = real
        new = self.th.PACK_REGISTRATION_FAILURES[len(self._failures):]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["pack"], "_register_mega_tools")
        self.assertEqual(new[0]["module"], "skills_system")
        self.assertIn("boom", new[0]["error"])
        # Siblings registered despite the failure: skills_media
        # follows skills_system in the mega loop, so its tools
        # (e.g. convert_image) must be present on the fake.
        self.assertIn("convert_image", fake._tools)

    def test_single_pack_failure_recorded(self):
        """WIR-12: a broken single-import pack is recorded too."""
        from core.funnel import toolbox  # Phase 3 F2a INTENTIONAL: toolbox moved to core/funnel/
        real = toolbox.register_all
        toolbox.register_all = lambda registry: (_ for _ in ()).throw(
            RuntimeError("kaboom"))
        try:
            fake = _FakeRegistry()
            self.th.ToolHandlersMixin._register_toolbox_tools(fake)
        finally:
            toolbox.register_all = real
        new = self.th.PACK_REGISTRATION_FAILURES[len(self._failures):]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["module"], "toolbox")

    def test_doctor_surfaces_failures(self):
        """WIR-12: the doctor reports PASS when clean and WARN
        naming the pack when a failure is recorded.

        Namespace note: the doctor reads the failure list
        through the voice_assistant.* namespace (its production
        namespace), while this suite imports core.* — the same
        file under two module keys, so the test appends to
        the doctor's own module object, exactly as the app's
        voice_assistant-namespace registration would.
        """
        import importlib
        if "voice_assistant" not in sys.modules:
            pkg = types.ModuleType("voice_assistant")
            pkg.__path__ = [str(TREE)]
            sys.modules["voice_assistant"] = pkg
        import doctor as doctor_mod
        vah = importlib.import_module("voice_assistant.core.funnel.tools_handlers")  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        vah_saved = list(vah.PACK_REGISTRATION_FAILURES)
        rows = []
        real_report = doctor_mod.report
        doctor_mod.report = lambda status, label, detail="", fix="": rows.append(
            (status, label, detail))
        try:
            doctor_mod.check_pack_registrations()
            pack_rows = [r for r in rows if r[1] == "Pack registrations"]
            self.assertEqual(len(pack_rows), 1)
            self.assertEqual(pack_rows[0][0], "PASS")
            rows.clear()
            vah.PACK_REGISTRATION_FAILURES.append({
                "pack": "_register_mega_tools", "module": "skills_system",
                "item": "skills_system", "error": "RuntimeError: boom"})
            doctor_mod.check_pack_registrations()
            pack_rows = [r for r in rows if r[1] == "Pack registrations"]
            self.assertEqual(pack_rows[0][0], "WARN")
            self.assertIn("skills_system", pack_rows[0][2])
        finally:
            doctor_mod.report = real_report
            vah.PACK_REGISTRATION_FAILURES[:] = vah_saved


class OverrideTests(unittest.TestCase):
    def test_later_pack_wins_for_all_three(self):
        """Duplicate-registration explicitness: the hands4 packs
        deliberately override the mega pack for all three names,
        and the registered handler is the later pack's."""
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        winners = {
            "resize_image": "skills_media_convert",  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
            "convert_image": "skills_media_convert",  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
            "screenshot_region": "skills_screen_capture",  # Phase C C4 INTENTIONAL: eyes2 renamed to screen_capture
        }
        for name, pack in winners.items():
            handler = tools._tools[name]["handler"]
            self.assertTrue(
                handler.__module__.endswith("core." + pack),
                f"{name}: winner is {handler.__module__}, expected *.{pack}")

    def test_override_comments_present(self):
        """The three second registrations carry their INTENTIONAL
        OVERRIDE comment (no silent duplicate survives)."""
        media_convert = (TREE / "core" / "skills_media_convert.py").read_text(encoding="utf-8")  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        screen_capture = (TREE / "core" / "skills_screen_capture.py").read_text(encoding="utf-8")  # Phase C C4 INTENTIONAL: eyes2 renamed to screen_capture
        self.assertEqual(media_convert.count("INTENTIONAL OVERRIDE"), 2)
        self.assertEqual(screen_capture.count("INTENTIONAL OVERRIDE"), 1)

    def test_override_comments_are_name_bound(self):
        """Audit fix Batch 3 (REG-01): the old count test could
        pass while a comment drifted to another tool. Each
        INTENTIONAL OVERRIDE comment must sit directly above
        THAT name's second register call (within the 15 lines
        before it), naming the loser pack it replaces."""
        from core.funnel.tools_registry import OVERRIDE_ALLOWLIST  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        sources = {
            "skills_media_convert.py": (TREE / "core" / "skills_media_convert.py").read_text(encoding="utf-8").splitlines(),  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
            "skills_screen_capture.py": (TREE / "core" / "skills_screen_capture.py").read_text(encoding="utf-8").splitlines(),  # Phase C C4 INTENTIONAL: eyes2 renamed to screen_capture
        }
        for name, entry in OVERRIDE_ALLOWLIST.items():
            winner_file = ("skills_media_convert.py" if entry["to"] == "skills_media_convert"  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
                           else "skills_screen_capture.py")
            lines = sources[winner_file]
            reg_idx = [i for i, line in enumerate(lines)
                       if f'name="{name}"' in line]
            self.assertEqual(len(reg_idx), 1, f"{name}: expected exactly one register site in {winner_file}")
            window = "\n".join(lines[max(0, reg_idx[0] - 15):reg_idx[0]])
            self.assertIn("INTENTIONAL OVERRIDE", window,
                          f"{name}: no override comment above its register call")
            self.assertIn(entry["from"], window,
                          f"{name}: comment does not name loser {entry['from']}")

    def test_winner_schema_description_and_gate_identity(self):
        """The winner is not just the right module: its schema
        carries the winner-only fields and its gate class is
        the winner's (an override must not silently change a
        safety class or drop the capability it was kept for)."""
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
        winner_fields = {
            "resize_image": "scale_percent",
            "convert_image": "quality",
            "screenshot_region": "path",
        }
        for name, field in winner_fields.items():
            params = tools._tools[name]["parameters"]
            self.assertIn(field, params.get("properties", {}),
                          f"{name}: winner schema lost {field}")
            self.assertTrue(tools._tools[name]["description"],
                            f"{name}: winner description empty")
        # None of the three is read-class (they write files);
        # the override did not smuggle any of them into the
        # read-only set.
        for name in winner_fields:
            self.assertFalse(gate._is_read_class(name, {}), name)

    def test_allow_list_decides_winner_in_both_orders(self):
        """Audit fix Batch 3 (REG-01) order variation: the
        allow-list, not pack order, decides the winner. A loser
        arriving after its winner is refused (loudly) and the
        winner survives; a winner arriving after its loser
        replaces it and is logged."""
        from core.funnel.tools_registry import ToolRegistry  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

        def _stub(module):
            def _handler(**kw):
                return {"ok": True}
            _handler.__module__ = "core." + module
            return _handler

        reg = ToolRegistry()
        reg.register("resize_image", "loser", {"type": "object", "properties": {}},
                     _stub("skills_media"))
        reg.register("resize_image", "winner", {"type": "object", "properties": {}},
                     _stub("skills_media_convert"), override=True)  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertTrue(reg._tools["resize_image"]["handler"].__module__.endswith("skills_media_convert"))  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertEqual([e["name"] for e in reg.override_log()], ["resize_image"])

        reg2 = ToolRegistry()
        reg2.register("resize_image", "winner", {"type": "object", "properties": {}},
                      _stub("skills_media_convert"))  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        with self.assertRaises(ValueError):
            reg2.register("resize_image", "loser", {"type": "object", "properties": {}},
                          _stub("skills_media"), override=True)
        self.assertTrue(reg2._tools["resize_image"]["handler"].__module__.endswith("skills_media_convert"))  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertEqual(reg2.override_log(), [])


if __name__ == "__main__":
    unittest.main()

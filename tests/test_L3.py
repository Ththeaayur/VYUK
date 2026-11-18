"""L3 test debt (logic audit §3): budget-isolation standardisation,
the two stale status assertions, and the order-sensitive suites.

Pins, in order:
1. The shared helper (tests/_budget_isolation.py) implements the
   test-isolation guest rule exactly: snapshot timestamps + clock
   + confirm handler, open a clean window, restore byte-for-byte.
2. The disease and the cure, mechanistically: a saturated budget
   window makes a gated writer fail exactly as the full run showed
   (kind "budget", no file written); the same writer under the
   helper's clean window succeeds; restoring brings the saturated
   ambient window back untouched.
3. The A5 status contract the two fixed stale assertions rely on
   (core/funnel/tool_result.py): legacy "error" normalizes by kind.
4. The uia11y snapshot/restore fix: entries present before
   setUpModule survive tearDownModule byte-for-byte, and entries
   installed by the suite are removed — the old delete-all wiped
   chatfix's cached real w9_wiring when uia11y ran first.
5. Adoption: every shared-funnel writer suite carries the guest
   rule (setUpModule/tearDownModule via the shared helper).

This suite itself uses the guest rule, so it neither inherits
nor leaves a budget window. Stdlib unittest only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest

try:
    from _budget_isolation import (
        reset as _reset_budget,
    )
    from _budget_isolation import (
        restore_budget,
        snapshot_budget,
    )
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import reset as _reset_budget  # type: ignore
    from tests._budget_isolation import (  # type: ignore
        restore_budget,
        snapshot_budget,
    )

try:
    from core import confirm_gate
    from core.funnel import tool_result as _tr  # Phase 3 F2a INTENTIONAL: tool_result moved to core/funnel/
    from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
    from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"core tools unavailable: {exc}")

try:
    from test_triage1_study import _materials as _triage_materials
except ImportError:  # run as a package module instead of top-level
    try:
        from tests.test_triage1_study import (  # type: ignore
            _materials as _triage_materials,
        )
    except ImportError:
        _triage_materials = None

_BUDGET_SNAPSHOT = None


def setUpModule():
    global _BUDGET_SNAPSHOT
    _BUDGET_SNAPSHOT = snapshot_budget()


def tearDownModule():
    global _BUDGET_SNAPSHOT
    if _BUDGET_SNAPSHOT is not None:
        restore_budget(_BUDGET_SNAPSHOT)
        _BUDGET_SNAPSHOT = None


def _saturate():
    """Fill the budget window past its limit, as late in a full
    run: 130 recent dispatches against the 120 / 60 s budget."""
    import time

    now = time.time()
    th._budget_timestamps.extend([now] * 130)


class HelperContractTests(unittest.TestCase):
    def tearDown(self):
        _reset_budget()
        confirm_gate.clear_handler()

    def test_snapshot_copies_timestamps_restores_list_identity(self):
        _saturate()
        original_list = th._budget_timestamps
        saved = snapshot_budget()
        # Snapshot is a copy, not an alias: the reset that follows
        # must not drain the saved copy.
        self.assertEqual(len(saved["budget"]), 130)
        self.assertEqual(len(th._budget_timestamps), 0)
        self.assertIsNot(th._budget_timestamps, saved["budget"])
        # Dispatches under the clean window, then restore.
        th._budget_timestamps.append(1.0)
        restore_budget(saved)
        self.assertIs(th._budget_timestamps, original_list)
        self.assertEqual(len(th._budget_timestamps), 130)

    def test_snapshot_restores_clock_and_handler(self):
        sentinel_now = object()
        old_now = th._budget_now
        confirm_gate.register_handler(lambda name, summary: True)
        try:
            saved = snapshot_budget()
            self.assertIs(saved["now"], old_now)
            self.assertIsNotNone(saved["handler"])
            th._budget_now = sentinel_now
            confirm_gate.clear_handler()
            restore_budget(saved)
            self.assertIs(th._budget_now, old_now)
            self.assertIsNotNone(confirm_gate._handler)
        finally:
            th._budget_now = old_now
            confirm_gate.clear_handler()

    def test_restore_clears_handler_registered_after_snapshot(self):
        saved = snapshot_budget()
        self.assertIsNone(saved["handler"])
        confirm_gate.register_handler(lambda name, summary: True)
        restore_budget(saved)
        self.assertIsNone(confirm_gate._handler)


class DiseaseAndCureTests(unittest.TestCase):
    def tearDown(self):
        _reset_budget()
        confirm_gate.clear_handler()

    def _write_deck(self, tmp):
        out = os.path.join(tmp, "bb101.apkg")
        materials = (
            _triage_materials()
            if _triage_materials is not None
            else [{"q": "a", "a": "b"}]
        )
        # _materials() already returns a JSON string (the triage
        # test passes it verbatim); do not double-encode it.
        if not isinstance(materials, str):
            materials = json.dumps(materials)
        result = tools.execute("study_export_deck", {
            "materials_json": materials,
            "deck_name": "BB101 Test", "output_path": out})
        return out, result

    def test_saturated_budget_fails_writer_exactly_like_full_run(self):
        # The disease, reproduced on demand: handler registered
        # (so the refusal cannot be a confirm-gate refusal), yet
        # the writer fails with kind "budget" and no file — the
        # exact Phase 4 full-run shape for both triage tests.
        _saturate()
        confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            out, result = self._write_deck(tmp)
            self.assertEqual(result.get("kind"), "budget")
            self.assertEqual(result.get("status"), "refused")
            self.assertFalse(os.path.exists(out))

    def test_helper_clean_window_cures_writer_restores_ambient(self):
        _saturate()
        confirm_gate.register_handler(lambda name, summary: True)
        saved = snapshot_budget()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out, result = self._write_deck(tmp)
                self.assertTrue(os.path.exists(out))
                payload = json.loads(result["result"])
                self.assertEqual(payload["status"], "ok")
        finally:
            restore_budget(saved)
        # Ambient saturated window back untouched: the cure
        # neither inherits nor erases the run's window.
        self.assertEqual(len(th._budget_timestamps), 130)


class StatusContractTests(unittest.TestCase):
    def test_legacy_error_normalizes_by_kind(self):
        # The contract the two fixed stale assertions rely on
        # (audit A5, core/funnel/tool_result.py). Refusal kinds map to
        # "refused"; failure kinds map to "failure"; never "error".
        cases = {
            "timeout": "refused",
            "not_confirmed": "refused",
            "validation": "refused",
            "budget": "refused",
            "unavailable": "failure",
            "handler_error": "failure",
        }
        for kind, status in cases.items():
            out = _tr.normalize_result(
                {"status": "error", "kind": kind, "message": "m"})
            self.assertEqual(out["status"], status)
            self.assertEqual(out["kind"], kind)

    def test_vocabulary_results_pass_through(self):
        for status in ("success", "failure", "refused", "timeout"):
            out = _tr.normalize_result(
                {"status": status, "kind": "k", "message": "m"})
            self.assertEqual(out["status"], status)


class Uia11yRestoreTests(unittest.TestCase):
    def test_teardown_restores_preexisting_entries(self):
        try:
            import test_uia11y as uia
        except ImportError:
            from tests import test_uia11y as uia  # type: ignore
        planted_pkg = types.ModuleType("voice_assistant")
        planted_pkg.__path__ = []
        planted_w9 = types.ModuleType("voice_assistant.core.w9_wiring")
        planted_w9.note_chat_message = lambda *a, **k: "planted"
        pre = {k: v for k, v in sys.modules.items()
               if k == "voice_assistant"
               or k.startswith("voice_assistant.")}
        sys.modules["voice_assistant"] = planted_pkg
        sys.modules["voice_assistant.core.w9_wiring"] = planted_w9
        try:
            uia.setUpModule()
            # Simulate the suite's install: overwrite the cached
            # module and add a new stub entry.
            stub_w9 = types.ModuleType("voice_assistant.core.w9_wiring")
            stub_w9.note_chat_message = lambda *a, **k: None
            sys.modules["voice_assistant.core.w9_wiring"] = stub_w9
            sys.modules["voice_assistant.core.audio"] = types.ModuleType(
                "voice_assistant.core.audio")
            uia.tearDownModule()
            # Planted entries back by identity; suite entries gone.
            self.assertIs(sys.modules["voice_assistant"], planted_pkg)
            self.assertIs(
                sys.modules["voice_assistant.core.w9_wiring"], planted_w9)
            self.assertNotIn("voice_assistant.core.audio", sys.modules)
        finally:
            for key in list(sys.modules):
                if key == "voice_assistant" or key.startswith(
                        "voice_assistant."):
                    del sys.modules[key]
            sys.modules.update(pre)
            uia._VA_SNAPSHOT = None


class Batch5RestoreTests(unittest.TestCase):
    """Audit fix Batch 5 (TEST-03): the audit2s1 and
    uiinteraction legs get the same snapshot/restore pin as
    uia11y (Uia11yRestoreTests above). Each module's
    tearDownModule must restore pre-existing
    voice_assistant.* entries by identity and remove only
    entries the suite itself installed — including on the
    no-Qt path, where setUpModule early-returns without
    installing anything (the audit-env shape: the old
    unconditional delete-all still ran and wiped chatfix's
    cached real w9_wiring)."""

    def _check_module(self, module_name):
        try:
            mod = __import__(module_name)
        except ImportError:
            mod = __import__("tests." + module_name, fromlist=["x"])
        planted_pkg = types.ModuleType("voice_assistant")
        planted_pkg.__path__ = []
        planted_w9 = types.ModuleType("voice_assistant.core.w9_wiring")
        planted_w9.note_chat_message = lambda *a, **k: "planted"
        pre = {k: v for k, v in sys.modules.items()
               if k == "voice_assistant"
               or k.startswith("voice_assistant.")}
        sys.modules["voice_assistant"] = planted_pkg
        sys.modules["voice_assistant.core.w9_wiring"] = planted_w9
        try:
            mod.setUpModule()
            # Simulate the suite's install: overwrite the
            # cached module and add a new stub entry.
            stub_w9 = types.ModuleType("voice_assistant.core.w9_wiring")
            stub_w9.note_chat_message = lambda *a, **k: None
            sys.modules["voice_assistant.core.w9_wiring"] = stub_w9
            sys.modules["voice_assistant.core.audio"] = types.ModuleType(
                "voice_assistant.core.audio")
            mod.tearDownModule()
            # Planted entries back by identity; suite entries gone.
            self.assertIs(sys.modules["voice_assistant"], planted_pkg)
            self.assertIs(
                sys.modules["voice_assistant.core.w9_wiring"], planted_w9)
            self.assertNotIn("voice_assistant.core.audio", sys.modules)
        finally:
            for key in list(sys.modules):
                if key == "voice_assistant" or key.startswith(
                        "voice_assistant."):
                    del sys.modules[key]
            sys.modules.update(pre)
            mod._VA_SNAPSHOT = None

    def test_audit2s1_teardown_restores_preexisting_entries(self):
        self._check_module("test_audit2s1")

    def test_uiinteraction_teardown_restores_preexisting_entries(self):
        self._check_module("test_uiinteraction")


class NoDeleteAllTeardownTests(unittest.TestCase):
    """Audit fix Batch 5 (TEST-03) lint: BANNED — a
    tearDownModule that deletes voice_assistant.* entries
    without restoring a snapshot. Snapshot/restore only:
    any test module whose tearDownModule deletes
    voice_assistant.* entries must restore exactly the
    pre-existing entries in the same function (the uia11y /
    audit2s1 / uiinteraction pattern, pinned above by
    identity). A bare delete-all is how chatfix's cached
    real w9_wiring got wiped by a suite that had installed
    nothing. This lint scans tearDownModule functions only
    (AST), so ordinary test-body cleanup is untouched."""

    def test_no_unconditional_voice_assistant_delete_all(self):
        import ast
        import pathlib

        here = pathlib.Path(__file__).resolve().parent
        offenders = []
        for path in sorted(here.glob("test_*.py")):
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in tree.body:
                if not (isinstance(node, ast.FunctionDef)
                        and node.name == "tearDownModule"):
                    continue
                segment = ast.get_source_segment(src, node) or ""
                if ("voice_assistant" in segment
                        and "del sys.modules" in segment
                        and "sys.modules.update(" not in segment):
                    offenders.append(path.name)
        self.assertEqual(offenders, [], offenders)


class Batch5BudgetAdoptionTests(unittest.TestCase):
    """Audit fix Batch 5 (TEST-02): the three suites the L3
    programme deliberately left un-isolated now carry the
    guest rule — same snapshot/restore discipline as the
    11 pinned suites (budget timestamps + clock + confirm
    handler via production reset_tool_budget())."""

    def test_batch5_suites_carry_guest_rule(self):
        import pathlib

        here = pathlib.Path(__file__).resolve().parent
        for name in ("test_release_w10.py",
                     "test_tools_execute_schema.py",
                     "test_wave7_undo.py"):
            src = (here / name).read_text(encoding="utf-8")
            self.assertIn("def setUpModule():", src, name)
            self.assertIn("def tearDownModule():", src, name)
            self.assertIn("snapshot_budget()", src, name)
            self.assertIn("restore_budget(", src, name)


class AdoptionTests(unittest.TestCase):
    def test_writer_suites_carry_guest_rule(self):
        # The standardisation itself, pinned: every shared-funnel
        # writer suite must carry setUpModule/tearDownModule via
        # the shared helper, so no suite can silently drop it.
        import pathlib

        suites = (
            "test_travel_w9.py", "test_trust_w10.py",
            "test_triage1_study.py",
            "test_triage2_creative_dev_social.py",
            "test_wave8_antic.py", "test_wave8_feel.py",
            "test_wave8_learnlast.py", "test_wave8_phase5.py",
            "test_wave8_trackl.py", "test_well_w9.py",
            "test_wave6_wiring.py",
        )
        here = pathlib.Path(__file__).resolve().parent
        for name in suites:
            src = (here / name).read_text()
            self.assertIn("def setUpModule():", src, name)
            self.assertIn("def tearDownModule():", src, name)
            self.assertIn("snapshot_budget()", src, name)
            self.assertIn("restore_budget(", src, name)


if __name__ == "__main__":
    unittest.main()

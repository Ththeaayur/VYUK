"""Shipped unit tests (wiring item 6, v6.0.0 post-ship): staged-module
triage + control-list snapshot.

The wiring inventory (wiring_inventory_v600.md) left 26 staged v5.63/v5.64
modules PARTIAL — reachable through the live w8/w9 wiring modules, with
end-to-end invocation never traced. This suite pins the traced chains
at caller level (no product-code change beyond the two wires below):

- w8 chains (core-only): stable prefix -> context, preflight ->
  TokenLedger, prompt supplements -> learning_feed +
  sleeptime, plan verification -> plan_verify, reflective
  execution -> reflection, checkpointed runs ->
  checkpoint, speculative gate -> speculative, trace
  mining -> trace_mining (miner + live-log reader).
- w9 chains (headless-safe wrappers; Qt-window chains stay
  caller-evidenced, see the triage note): stylesheet -> tokens,
  Follow-Windows -> theme_sync, settings store/schema ->
  settings_schema, theme packs -> theme_packs, canon gate
  -> sonic_canon, icon resolver -> icon_manifest, on-this-day ->
  on_this_day, overview lines -> overview_panel, summon model
  -> summon_window, turn checks/ladder -> conversation.
- NEW WIRE 1: the Doctor failure-clusters check (doctor.py) mines
  live trace clusters and reports them; cases are never
  auto-committed (commit needs a human-supplied expected behaviour).
- NEW WIRE 2: the control-list snapshot source (core/desktop/uia.py
  control_list_snapshot), built on enumerate_marks, never raising.

Qt-window chains (show_summon_window, build_overview_panel,
apply_window_backdrop, refresh_first_run_proof, searchable_combo,
sync_status_glyph) are caller-evidenced by live calls in
gui/window_shell.py, gui/window_pages.py and gui/window_settings.py
and are NOT pinned here — a full MainWindow harness is out of scope
for a triage suite; the triage note records the evidence.

Store isolation: every test that touches a store uses a temp dir or a
stub; the real data/ tree is never written. Stdlib unittest only.
Run via tests/run_tests.py.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _register_packages():
    """Mirror doctor._register_package / test_chatfix: make
    voice_assistant.* importable from this folder whatever it is
    called, so the lazy w9_wiring imports resolve to real modules."""
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
    from core import w8_wiring as w8
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    w8 = None
    _W8_ERROR = exc

try:
    from core import w9_wiring as w9
except Exception as exc:  # noqa: BLE001 — Qt or dep missing: skip
    w9 = None
    _W9_ERROR = exc

# T-2 (debug pass): the w9 theme/proof/summon functions moved to gui/
# (GAPS-15 layering: core cannot import Qt). Attach them to the w9
# module object so the call sites below keep working — the tests
# exercise the functions, not the module they live in.
if w9 is not None:
    try:
        from gui import w9_wiring_theme as _w9_theme
        from gui import w9_wiring_proof as _w9_proof
        from gui import w9_wiring_summon as _w9_summon
        for _name in ("stylesheet_for", "follow_windows_theme",
                      "status_glyph_for_text", "glyph_pixmap"):
            if not hasattr(w9, _name):
                setattr(w9, _name, getattr(_w9_theme, _name))
        for _name in ("build_overview_panel", "on_this_day_lines",
                      "overview_from_activity_lines",
                      "update_briefing_on_this_day"):
            if not hasattr(w9, _name):
                setattr(w9, _name, getattr(_w9_proof, _name))
        if not hasattr(w9, "build_summon_model"):
            w9.build_summon_model = _w9_summon.build_summon_model
    except Exception:  # noqa: BLE001 — Qt-only shim; sys skips below
        pass

try:
    from core.desktop import uia as uia_mod  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    uia_mod = None
    _UIA_ERROR = exc

try:
    import doctor as doctor_mod
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    doctor_mod = None
    _DOCTOR_ERROR = exc

try:
    import PySide6  # noqa: F401
    _HAS_QT = True
except Exception:  # noqa: BLE001
    _HAS_QT = False


def _need_w8(test):
    if w8 is None:
        test.skipTest(f"w8 deps unavailable: {_W8_ERROR}")


def _need_w9(test):
    if w9 is None:
        test.skipTest(f"w9 deps unavailable: {_W9_ERROR}")


def _need_uia(test):
    if uia_mod is None:
        test.skipTest(f"uia deps unavailable: {_UIA_ERROR}")


def _need_doctor(test):
    if doctor_mod is None:
        test.skipTest(f"doctor deps unavailable: {_DOCTOR_ERROR}")
    _need_w8(test)


class _StubSettings:
    """Dict-backed QSettings stand-in (value/setValue/sync/contains).

    T-9 (debug pass): the migration calls store.contains(key) via
    qsettings_contains, so the stub must implement it — a real
    QSettings has it; the stub previously did not."""

    def __init__(self, values=None):
        self._v = dict(values or {})

    def value(self, key, default=None):
        return self._v.get(key, default)

    def setValue(self, key, val):
        self._v[key] = val

    def contains(self, key):
        return key in self._v

    def sync(self):
        pass


class _StubLabel:
    def __init__(self):
        self.texts = []
        self.visible = []

    def setText(self, text):
        self.texts.append(text)

    def setVisible(self, visible):
        self.visible.append(visible)


class TestW8Chains(unittest.TestCase):
    def test_stable_prefix_roundtrip(self):
        _need_w8(self)
        parts = ["You are VYUK.", "Reply in English."]
        cached = w8.note_stable_prefix(parts)
        expect = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        self.assertEqual(cached.cache_key, expect)
        self.assertTrue(w8.stable_prefix_is_stable(parts))
        self.assertFalse(w8.stable_prefix_is_stable(["different"]))

    def test_preflight_ok_and_violation(self):
        _need_w8(self)
        ok = w8.preflight_prompt({"system": "hello"})
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["violations"], [])
        over = w8.preflight_prompt({"tools_gemini": "x" * 400_000})
        self.assertFalse(over["ok"])
        self.assertEqual(over["violations"][0]["stream"], "tools_gemini")

    def test_prompt_supplements_learning_and_sleeptime(self):
        _need_w8(self)
        with tempfile.TemporaryDirectory() as tmp:
            feed_dir = str(Path(tmp) / "feed")
            mem_dir = str(Path(tmp) / "memories")
            # Empty stores -> empty supplements (default prompts stay
            # byte-identical) and an empty sleeptime block.
            self.assertEqual(w8.prompt_supplements(feed_dir=feed_dir,
                                                   memories_base=mem_dir), "")
            self.assertEqual(w8.sleeptime_block(base=mem_dir), "")
            # One learned fact surfaces as a labelled section.
            w8.record_learning_event("The user prefers dark themes",
                                     "triage-test", directory=feed_dir)
            text = w8.prompt_supplements(feed_dir=feed_dir,
                                         memories_base=mem_dir)
            self.assertIn("What I have learned (learning feed):", text)
            self.assertIn("dark themes", text)
            # One file-store fact surfaces through the sleeptime block.
            from core.memory.memory_files import write_fact
            write_fact({"category": "preference", "key": "theme",
                        "value": "dark", "heat": 5}, base=mem_dir)
            block = w8.sleeptime_block(base=mem_dir)
            self.assertIn("dark", block)

    def test_verify_steps_known_and_unknown(self):
        _need_w8(self)
        ok = w8.verify_steps_against_live_tools(
            [{"tool": "get_current_time_date", "args": {}}])
        self.assertTrue(ok["ok"])
        bad = w8.verify_steps_against_live_tools(
            [{"tool": "no-such-tool-xyz", "args": {}}])
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["checks"][0]["tool"], "no-such-tool-xyz")
        self.assertFalse(bad["checks"][0]["all_valid"])

    def test_execute_step_plain(self):
        _need_w8(self)
        out = w8.execute_step_with_reflection(
            {"tool": "x", "args": {}},
            executor=lambda step: {"ok": True, "result": "done"})
        self.assertEqual(out["outcome"], {"ok": True, "result": "done"})
        self.assertFalse(out["reflected"])
        self.assertEqual(out["retries_used"], 0)

    def test_execute_step_reflection_retry(self):
        _need_w8(self)
        calls = []

        def _executor(step):
            calls.append(step)
            if len(calls) == 1:
                return {"ok": False, "error": "boom"}
            return {"ok": True, "result": "recovered"}

        out = w8.execute_step_with_reflection(
            {"tool": "x", "args": {}},
            executor=_executor, reflect_fn=lambda f: "retry with care")
        self.assertTrue(out["outcome"]["ok"])
        self.assertTrue(out["reflected"])
        self.assertEqual(out["retries_used"], 1)
        self.assertFalse(out["tainted"])
        self.assertIn("_reflection", calls[1])
        self.assertTrue(calls[1]["_reflection"])

    def test_checkpointed_run_completes(self):
        _need_w8(self)
        with tempfile.TemporaryDirectory() as tmp:
            out = w8.run_steps_checkpointed(
                [{"tool": "get_current_time_date", "args": {}}],
                "triage-run", store_dir=tmp,
                executor=lambda step: {"ok": True, "result": "done"})
            self.assertEqual(out["state"], "complete")
            self.assertEqual(out["steps"], 1)
            self.assertTrue(out["verification"]["ok"])

    def test_speculative_gate_off_by_design(self):
        _need_w8(self)
        gate = w8.speculative_generation_gate("balanced")
        self.assertFalse(gate["enabled"])
        self.assertFalse(gate["runtime_available"])
        self.assertIsNone(gate["measured_gain"])
        self.assertEqual(gate["claimed_gain"]["status"],
                         "PLAN-CLAIM-NOT-MEASURED")

    def test_live_failure_records_from_audit_chain(self):
        _need_w8(self)
        from core import audit_chain
        entries = [{"kind": "tool", "ts": "2026-11-01T00:00:00",
                    "data": {"tool": "get_weather", "error": "timed out",
                             "status": "failed"}}]
        with mock.patch.object(audit_chain, "read",
                               return_value=entries):
            records = w8.live_failure_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool"], "get_weather")
        self.assertEqual(records[0]["error"], "timed out")

    def test_mine_live_trace_clusters(self):
        _need_w8(self)
        records = [{"tool": "get_weather", "error": "timed out",
                    "kind": "failure", "ts": "2026-11-01T00:00:00"}]
        with mock.patch.object(w8, "live_failure_records",
                               return_value=records):
            mined = w8.mine_live_trace_clusters()
        self.assertEqual(len(mined["clusters"]), 1)
        self.assertEqual(mined["clusters"][0]["tool"], "get_weather")
        self.assertEqual(mined["clusters"][0]["count"], 1)
        self.assertEqual(mined["total_failures"], 1)


class TestW9Chains(unittest.TestCase):
    def test_stylesheet_for(self):
        _need_w9(self)
        sheet, source = w9.stylesheet_for("Cyan")
        self.assertIsInstance(sheet, str)
        self.assertTrue(sheet)
        self.assertIn(source, ("tokens", "shipped"))

    def test_follow_windows_theme_off(self):
        _need_w9(self)
        theme, report = w9.follow_windows_theme("Cyan", False)
        self.assertEqual(theme, "Cyan")
        self.assertFalse(report["follow_enabled"])
        self.assertFalse(report["applied"])

    def test_settings_migration(self):
        _need_w9(self)
        stub = _StubSettings({"appearance/density": "compact"})
        store = w9.settings_store(stub)
        migrated = w9.migrate_settings_store(store)
        self.assertEqual(migrated, ["density"])
        self.assertEqual(stub.value("density"), "compact")
        # Already migrated: never overwritten, nothing to do.
        self.assertEqual(w9.migrate_settings_store(store), [])

    def test_settings_schema_rows(self):
        _need_w9(self)
        store = w9.settings_store(_StubSettings())
        rows = w9.settings_schema_rows(store)
        self.assertTrue(len(rows) > 0)
        self.assertTrue(all(isinstance(r.name, str) for r in rows))
        self.assertEqual(w9.settings_schema_rows(store, "zzzz-no-match"), [])

    def test_theme_pack_apply_and_reject(self):
        _need_w9(self)
        from core.theme_packs import ThemePackError
        pack = json.dumps({
            "schema_version": 1, "name": "Triage",
            "tokens": {"surface": "#0F141A", "surface_variant": "#1E242B",
                       "on_surface": "#F2F4F7", "primary": "#0B5FFF",
                       "on_primary": "#FFFFFF", "accent": "#00C8FF"}})
        applied = w9.apply_theme_pack_text(pack)
        self.assertEqual(applied["name"], "Triage")
        self.assertEqual(applied["accent"], "#00C8FF")
        self.assertEqual(applied["tokens"]["accent"], "#00C8FF")
        with self.assertRaises(ThemePackError):
            w9.apply_theme_pack_text("not json")

    def test_canon_gate(self):
        _need_w9(self)
        w9.reset_canon_uses_for_tests()
        self.assertTrue(w9.gate_sound_emission("ack"))
        self.assertFalse(w9.gate_sound_emission("no-such-cue-xyz"))

    def test_resolve_icon_missing_honest(self):
        _need_w9(self)
        ref = w9.resolve_icon("definitely-not-an-icon")
        self.assertFalse(ref.available)
        self.assertEqual(ref.status, "missing")

    def test_on_this_day_lines_list(self):
        _need_w9(self)
        lines = w9.on_this_day_lines()
        self.assertIsInstance(lines, list)

    def test_overview_from_activity_lines(self):
        _need_w9(self)
        series, events = w9.overview_from_activity_lines(
            ["2026-11-01 10:00 | get_current_time_date | ok", "garbage"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "get_current_time_date")
        self.assertEqual(series[0].ys, [1.0])

    def test_build_summon_model_injected(self):
        _need_w9(self)
        model = w9.build_summon_model(tools=["get_current_time_date"],
                                      recents=["morning"])
        self.assertEqual(model.tools[0].title, "get_current_time_date")
        self.assertEqual(model.tools[0].action_id, "get_current_time_date")
        self.assertEqual(model.recents[0].title, "morning")

    def test_note_turn_error_ladder(self):
        _need_w9(self)
        window = SimpleNamespace()
        first = w9.note_turn_error(window)
        self.assertEqual(first.consecutive_errors, 1)
        second = w9.note_turn_error(window)
        self.assertEqual(second.consecutive_errors, 2)

    def test_diagnose_turn_records(self):
        _need_w9(self)
        window = SimpleNamespace()
        ok_turn = w9.diagnose_turn(window, "hello")
        self.assertTrue(ok_turn.ok)
        self.assertIs(window._w9_last_turn_check, ok_turn)
        bad_turn = w9.diagnose_turn(window, "What? When? Why?")
        self.assertFalse(bad_turn.ok)

    def test_update_briefing_no_label(self):
        _need_w9(self)
        self.assertEqual(w9.update_briefing_on_this_day(SimpleNamespace()),
                         [])

    def test_update_briefing_label_consistent(self):
        _need_w9(self)
        label = _StubLabel()
        window = SimpleNamespace(briefing_on_this_day=label)
        lines = w9.update_briefing_on_this_day(window)
        if lines:
            self.assertTrue(label.texts)
        else:
            self.assertEqual(label.visible, [False])

    @unittest.skipUnless(_HAS_QT, "Qt unavailable")
    def test_note_chat_message(self):
        _need_w9(self)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        window = SimpleNamespace()
        doc = w9.note_chat_message(window, "user", "hello **world**")
        self.assertIsNotNone(doc)
        self.assertIn("user-0", window._w9_chat_renderer.message_ids())

    @unittest.skipUnless(_HAS_QT, "Qt unavailable")
    def test_glyph_pixmap(self):
        _need_w9(self)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from voice_assistant.gui import status_glyphs
        name = sorted(status_glyphs._GLYPHS)[0]
        pixmap = w9.glyph_pixmap(name, 20)
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())

    @unittest.skipUnless(_HAS_QT, "Qt unavailable")
    def test_build_overview_panel(self):
        _need_w9(self)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text(
                "2026-11-01 10:00 | get_current_time_date | ok\n"
                "2026-11-01 10:01 | get_weather | ok\n",
                encoding="utf-8")
            panel = w9.build_overview_panel(parent=None,
                                            activity_path=str(log))
            self.assertEqual(len(panel._w9_activity_events), 2)


class TestDoctorFailureClusters(unittest.TestCase):
    def _run_check(self, mined=None, error=None):
        _need_doctor(self)
        w8_va = importlib.import_module("voice_assistant.core.w8_wiring")
        seen = []

        def _report(status, label, detail="", fix=""):
            seen.append((status, label, detail))

        if error is not None:
            mine = mock.Mock(side_effect=error)
        else:
            mine = mock.Mock(return_value=mined)
        with mock.patch.object(doctor_mod, "report", _report), \
                mock.patch.object(w8_va, "mine_live_trace_clusters", mine):
            doctor_mod.check_failure_clusters()
        return seen

    def test_no_clusters_pass(self):
        seen = self._run_check(mined={"clusters": [], "total_failures": 0})
        self.assertEqual(seen[0][0], "PASS")
        self.assertEqual(seen[0][1], "Failure clusters")

    def test_clusters_warn_with_top(self):
        mined = {"clusters": [{"tool": "get_weather", "signature": "t",
                               "count": 5, "examples": ["timed out"],
                               "first_seen": "x", "last_seen": "y"}],
                 "total_failures": 5}
        seen = self._run_check(mined=mined)
        self.assertEqual(seen[0][0], "WARN")
        self.assertIn("1 failure cluster", seen[0][2])
        self.assertIn("get_weather", seen[0][2])

    def test_unreadable_log_warns_not_crashes(self):
        seen = self._run_check(error=RuntimeError("store gone"))
        self.assertEqual(seen[0][0], "WARN")
        self.assertEqual(seen[0][1], "Failure clusters")


class TestControlListSnapshot(unittest.TestCase):
    def test_off_windows_honest_empty(self):
        _need_uia(self)
        snap = uia_mod.control_list_snapshot("anything")
        self.assertEqual(snap["controls"], [])
        self.assertIsInstance(snap["note"], str)
        self.assertTrue(snap["note"])

    def test_stubbed_walk(self):
        _need_uia(self)
        fake_el = object()
        # Refactor P1 step 4 INTENTIONAL seam migration: core/desktop/uia.py
        # split into uia_base/verify/discover/probe/marks/click/send/
        # type. control_list_snapshot looks its helpers up in the
        # marks module's namespace, so patch every module that binds
        # each name (patching only core.desktop.uia no longer reaches it).
        _seam_mods = [uia_mod] + [
            importlib.import_module(f"core.desktop.uia_{s}")  # Phase 3 D4b INTENTIONAL: D2c miss — family modules moved to core/desktop/ in D2c, but this constructed import kept the flat path; proven failing identically on the pre-D4b backup tree
            for s in ("base", "verify", "discover", "probe", "marks",
                      "click", "send", "type")]
        _specs = [
            ("_marks_capture_block", {"return_value": None}),
            ("_unavailable", {"return_value": None}),
            ("_uia_window", {"return_value": (object(), "desc")}),
            ("_descendants", {"return_value": ([fake_el], None)}),
            ("_element_facts", {"return_value": {"name": "Save",
                                                 "control_type": "Button"}}),
            ("_element_rect", {"return_value": (10, 10, 100, 40)}),
            ("_control_usable", {"return_value": (True, "")}),
        ]
        patches = []
        for _mod in _seam_mods:
            for _name, _kw in _specs:
                if hasattr(_mod, _name):
                    patches.append(
                        mock.patch.object(_mod, _name, **_kw))
        for patch in patches:
            patch.start()
        try:
            snap = uia_mod.control_list_snapshot("")
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual(snap["controls"], [
            {"id": 1, "name": "Save", "control_type": "Button",
             "rect": (10, 10, 100, 40), "source": "uia"}])
        self.assertEqual(snap["note"], "")


if __name__ == "__main__":
    unittest.main()

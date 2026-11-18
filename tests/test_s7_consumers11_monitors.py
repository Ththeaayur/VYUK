"""§7 consumers batch 11 — funnel plumbing for monitor context.

Corrects a batch-10 premise with evidence, then finishes the
genuinely unplumbed half:

- click_by_mark needed NO new argument. The funnel registers
  _uia.click_by_mark directly, and with monitor_backend=None
  the engine gate runs against the LIVE backend
  (core/desktop/cursor_monitor.validate_point -> monitors.read_table(None)
  -> _live_enumerate). The gate was never skipped — only an
  unreadable table skips it, by design. The funnel tests below
  prove the gap refusal through the real funnel, not just at
  the engine seam.
- cursor_control's monitor branch WAS genuinely unplumbed:
  batch 10 added it Python-only ("NOT a funnel tool argument")
  and the closed schema refused an undeclared `monitor` key.
  This batch declares the optional monitor argument on the
  funnel tool, so voice-driven cursor work resolves through
  the item-6 engine against a fresh table. Omitted, behaviour
  is byte-identical (proven by the pass-through tests).
- Mark-listing annotation rides with the marks suffix:
  flagged ids only, never dropped, never altering a mark
  line, and byte-identical whenever the table is unreadable
  or no mark is flagged.

Lab honesty: every rule here is drivable headless with fake
monitor tables (mon.read_table patched, routed backend
stubbed). Live multi-monitor / mixed-DPI behaviour stays
field work, and the norm1000 capture-freshness investigation
stays open.
"""

import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent

from core.desktop import cursor  # Phase 3 D4d INTENTIONAL: cursor hub moved to core/desktop/
from core.desktop import monitors as mon  # INTENTIONAL (Phase 3 S7a): monitors moved to core/desktop/
from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel import tools_handlers as th_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.desktop import uia  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
from core.desktop import vision as vision_mod  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/
from core.config import config as app_config
from core import confirm_gate, injection


def _patch(test, target, new):
    patcher = mock.patch(target, new)
    patched = patcher.start()
    test.addCleanup(patcher.stop)
    return patched


def _two_monitor_table():
    """Primary 1920x1080 @0,0 scale 1.0; secondary 1920x1080
    @1920,0 scale 1.5. Virtual bounds 0,0..3839,1079."""
    return [
        {"monitor_id": "\\\\.\\DISPLAY1", "device": "\\\\.\\DISPLAY1",
         "left": 0, "top": 0, "width": 1920, "height": 1080,
         "primary": True, "scale_x": 1.0, "scale_y": 1.0},
        {"monitor_id": "\\\\.\\DISPLAY2", "device": "\\\\.\\DISPLAY2",
         "left": 1920, "top": 0, "width": 1920, "height": 1080,
         "primary": False, "scale_x": 1.5, "scale_y": 1.5},
    ]


def _staggered_table():
    """DISPLAY2 sits lower: the strip y<300 above it is a gap."""
    table = _two_monitor_table()
    table[1] = {"monitor_id": "\\\\.\\DISPLAY2",
                "device": "\\\\.\\DISPLAY2",
                "left": 1920, "top": 300, "width": 1280,
                "height": 1024, "primary": False,
                "scale_x": 1.0, "scale_y": 1.0}
    return table


def _unreadable_table():
    table = _two_monitor_table()
    table[1]["scale_x"] = None
    table[1]["scale_y"] = None
    return table


class _FakeMonitorBackend:
    def __init__(self, entries):
        self._entries = entries

    def enumerate(self):
        return [dict(e) for e in self._entries]


class _DataGuard:
    """Restore data/ after the run (funnel tests dirty it)."""

    def _scan(self):
        found = {}
        for path in sorted((ROOT / "data").rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(ROOT / "data")] = \
                        path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    ((ROOT / "data") / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = (ROOT / "data") / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class _FunnelBase(unittest.TestCase):
    def setUp(self):
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False
        self._orig_read_table = mon.read_table
        self.addCleanup(setattr, mon, "read_table",
                        self._orig_read_table)
        self.backend_calls = []

        class _RecordingBackend:
            def cursor_control(inner_self, *args, **kwargs):
                self.backend_calls.append((args, kwargs))
                return "stubbed dispatch"

        _patch(self, "core.desktop.cursor._routed_backend",
               lambda: _RecordingBackend())  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        # B35 INTENTIONAL: cursor_control reads _routed_backend from
        # the control module globals, so fake both bindings (same
        # fake form, both restored by addCleanup) — B16 pattern.
        _patch(self, "core.desktop.cursor_control._routed_backend",
               lambda: _RecordingBackend())

    def tearDown(self):
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()

    def _set_table(self, table):
        mon.read_table = lambda backend=None: (  # noqa: ARG005
            mon.canonical_sort([dict(e) for e in table]), "")

    def call(self, name, args):
        return tools.execute(name, dict(args))


class DeclarationTests(unittest.TestCase):
    def _cursor_decl(self):
        for decl in tools.get_declarations():
            if decl.get("name") == "cursor_control":
                return decl
        self.fail("cursor_control not in funnel declarations")

    def test_monitor_argument_declared_with_loud_grid_contract(self):
        decl = self._cursor_decl()
        props = decl["parameters"]["properties"]
        self.assertIn("monitor", props)
        spec = props["monitor"]
        self.assertEqual(spec.get("type"), "string")
        desc = spec.get("description") or ""
        self.assertIn("VIRTUAL-DESKTOP", desc)
        self.assertIn("NEVER screenshot-crop", desc)
        self.assertIn("FRESH monitor table", desc)
        self.assertIn("omitted means today's behaviour exactly", desc)

    def test_tool_description_mentions_monitor_target(self):
        decl = self._cursor_decl()
        self.assertIn("monitor", decl.get("description") or "")

    def test_monitor_is_optional(self):
        decl = self._cursor_decl()
        self.assertNotIn("monitor", decl["parameters"].get("required", []))


class FunnelCursorControlTests(_FunnelBase):
    def test_monitor_norm1000_resolves_through_funnel(self):
        self._set_table(_two_monitor_table())
        out = self.call("cursor_control", {
            "action": "click", "x": 651, "y": 500,
            "coords": "norm1000", "monitor": "DISPLAY2"})
        self.assertEqual(out["status"], "success", out)
        self.assertEqual(len(self.backend_calls), 1)
        kwargs = self.backend_calls[0][1]
        self.assertEqual((kwargs["x"], kwargs["y"]), (2499, 540))
        self.assertEqual(kwargs["coords"], "pixels")

    def test_monitor_pixels_validated_through_funnel(self):
        self._set_table(_two_monitor_table())
        out = self.call("cursor_control", {
            "action": "move", "x": 2500, "y": 500,
            "coords": "pixels", "monitor": "DISPLAY2"})
        self.assertEqual(out["status"], "success", out)
        kwargs = self.backend_calls[0][1]
        self.assertEqual((kwargs["x"], kwargs["y"]), (2500, 500))

    def test_monitor_refusals_never_dispatch_through_funnel(self):
        self._set_table(_two_monitor_table())
        cases = [
            ({"action": "click", "x": 651, "y": 500,
              "coords": "norm1000", "monitor": "DISPLAY9"},
             "Unknown monitor reference"),
            ({"action": "click", "x": 100, "y": 500,
              "coords": "norm1000", "monitor": "DISPLAY2"},
             "not on monitor"),
            ({"action": "position", "monitor": "DISPLAY1"},
             "applies only to absolute-target"),
            ({"action": "click", "x": 651,
              "coords": "norm1000", "monitor": "DISPLAY2"},
             "needs both x and y"),
        ]
        for args, substring in cases:
            with self.subTest(args=args):
                out = self.call("cursor_control", args)
                self.assertEqual(out["status"], "success", out)
                self.assertIn(substring, out["result"])
        self.assertEqual(self.backend_calls, [])

    def test_monitor_unreadable_scale_refuses_through_funnel(self):
        self._set_table(_unreadable_table())
        out = self.call("cursor_control", {
            "action": "click", "x": 651, "y": 500,
            "coords": "norm1000", "monitor": "DISPLAY2"})
        self.assertIn("unreadable scale", out["result"])
        self.assertEqual(self.backend_calls, [])

    def test_no_monitor_passes_through_unchanged(self):
        self._set_table(_two_monitor_table())
        out = self.call("cursor_control", {
            "action": "click", "x": 651, "y": 500,
            "coords": "norm1000"})
        self.assertEqual(out["status"], "success", out)
        kwargs = self.backend_calls[0][1]
        self.assertEqual((kwargs["x"], kwargs["y"]), (651, 500))
        self.assertEqual(kwargs["coords"], "norm1000")


class FunnelClickByMarkTests(_FunnelBase):
    def _patch_click(self, virtual_bounds):
        _patch(self, "core.desktop.uia._failsafe_block", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._safe_snapshot", lambda: {})  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.probe_suffix", lambda *a, **k: "")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_title", lambda: "App")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.cursor._virtual_bounds",
               lambda: virtual_bounds)  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        calls = []
        _patch(self, "core.desktop.cursor.click_cursor",
               lambda **k: calls.append(k) or
               "Clicked (left button) — landed at (60, 40).")  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        return calls

    def _stamp(self, rect, title="App", hwnd=None):
        uia._set_last_marks(
            [{"id": 1, "name": "Save", "control_type": "Button",
              "rect": rect}], title, hwnd)

    def tearDown(self):
        super().tearDown()
        try:
            from core.desktop import uia_marks as _um  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
            _um.clear_last_marks()
        except Exception:
            pass

    def test_gap_centre_refuses_through_funnel(self):
        # The batch-10 premise correction: no monitor argument
        # is passed, yet the gate runs — backend=None means the
        # LIVE backend, and the patched read_table is that live
        # path in this test. Centre (2500, 50) is in the virtual
        # union but in the staggered gap.
        self._set_table(_staggered_table())
        self._stamp((2450, 10, 2550, 90))
        calls = self._patch_click((0, 0, 3199, 1079))
        out = self.call("click_by_mark", {"mark_id": 1})
        self.assertEqual(out["status"], "success", out)
        self.assertIn("Not clicking mark 1", out["result"])
        self.assertIn("not on any monitor", out["result"])
        self.assertNotIn("Clicked mark", out["result"])
        self.assertEqual(calls, [])

    def test_on_monitor_centre_proceeds_through_funnel(self):
        self._set_table(_two_monitor_table())
        self._stamp((10, 20, 110, 60))
        calls = self._patch_click((0, 0, 3839, 1079))
        out = self.call("click_by_mark", {"mark_id": 1})
        self.assertEqual(out["status"], "success", out)
        self.assertIn("Clicked mark 1", out["result"])
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["x"], calls[0]["y"]), (60, 40))


class MonitorFlagNoteTests(unittest.TestCase):
    def _marks(self, rects):
        return [{"id": i + 1, "name": f"M{i + 1}",
                 "control_type": "Button", "rect": r}
                for i, r in enumerate(rects)]

    def test_gap_mark_flagged_with_reason(self):
        note = th_mod._monitor_flag_note(
            self._marks([(2450, 10, 2550, 90)]),
            backend=_FakeMonitorBackend(_staggered_table()))
        self.assertIn("[1]", note)
        self.assertIn("not on any monitor", note)

    def test_on_monitor_marks_produce_no_note(self):
        note = th_mod._monitor_flag_note(
            self._marks([(10, 20, 110, 60)]),
            backend=_FakeMonitorBackend(_two_monitor_table()))
        self.assertEqual(note, "")

    def test_unreadable_table_produces_no_note(self):
        class _RaisingBackend:
            def enumerate(self):
                raise RuntimeError("no table")

        note = th_mod._monitor_flag_note(
            self._marks([(2450, 10, 2550, 90)]),
            backend=_RaisingBackend())
        self.assertEqual(note, "")

    def test_flagged_marks_are_never_dropped_from_note(self):
        marks = self._marks([(10, 20, 110, 60),
                             (2450, 10, 2550, 90)])
        note = th_mod._monitor_flag_note(
            marks, backend=_FakeMonitorBackend(_staggered_table()))
        self.assertIn("[2]", note)
        self.assertNotIn("[1]", note)

    def test_suffix_appends_flags_only_when_flagged(self):
        marks = self._marks([(2450, 10, 2550, 90)])
        text = ("Grounding marks on this frame — click one with "
                "click_by_mark using its id: [1] 'Save' (Button) "
                "at (2450,10)–(2550,90)")
        with mock.patch.object(vision_mod, "marks_table_text",
                               return_value=text):
            with mock.patch.object(vision_mod.vision_manager,
                                   "last_marks", return_value=marks):
                with mock.patch.object(mon, "read_table",
                                       return_value=(
                                           mon.canonical_sort(
                                               _staggered_table()),
                                           "")):
                    suffix = th_mod._marks_suffix()
        self.assertIn(text, suffix)
        self.assertIn("Monitor flags", suffix)
        self.assertIn("[1]", suffix)
        self.assertIn("not on any monitor", suffix)

    def test_suffix_byte_identical_when_unvalidated(self):
        marks = self._marks([(2450, 10, 2550, 90)])
        text = ("Grounding marks on this frame — click one with "
                "click_by_mark using its id: [1] 'Save' (Button) "
                "at (2450,10)–(2550,90)")
        with mock.patch.object(vision_mod, "marks_table_text",
                               return_value=text):
            with mock.patch.object(vision_mod.vision_manager,
                                   "last_marks", return_value=marks):
                with mock.patch.object(mon, "read_table",
                                       return_value=([], "no table")):
                    suffix = th_mod._marks_suffix()
        self.assertEqual(suffix, f" {text}")


if __name__ == "__main__":
    unittest.main()

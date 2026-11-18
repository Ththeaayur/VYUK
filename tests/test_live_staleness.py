"""Live staleness stage tests (P1–P4, 2026-11-05).

P1: the delivery ledger records ONLY delivered frames (pixels
from the bytes actually sent), never a capture that merely
happened, never a failed send. P2: the live-origin marker is
set for exactly one _execute_tool_sync call and survives nested
calls on the same thread. P3: the helper resizes with PIL (never
normalise_grid), compares with its own named thresholds, and
passes through labelled on every unverifiable input. P4: the
funnel stage refuses live stale point actions BEFORE confirm,
passes through labelled otherwise, and never runs for non-live
callers or out-of-scope actions.

Headless by construction: synthetic frames (PIL), fake grabs,
fake sessions, fake registries — no screen, no live session, no
real mouse movement. The two real-registry tests refuse BEFORE
dispatch (stale) or before validation (out-of-scope action), so
no handler ever runs in them.
"""

import asyncio
import ast
import io
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from core.desktop import live_staleness as ls
from core.funnel import live_origin as lo

_TREE = Path(__file__).resolve().parent.parent


def _jpeg_bytes(width, height, rgb):
    """JPEG bytes for a solid-colour frame."""
    img = Image.new("RGB", (width, height), rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _solid_frame(width, height, gray_value):
    """A ledger-shaped frame tuple (width, height, gray bytes)."""
    return (width, height, bytes([gray_value]) * (width * height))


def _fake_ctx(name, args):
    """Minimal funnel context for stage-unit tests."""
    audited_calls = []
    gate = {"value": "free", "request_id": None}

    def _audited(result):
        audited_calls.append(result)
        return result

    ctx = SimpleNamespace(name=name, args=args, _audited=_audited,
                          _audit_gate=gate)
    return ctx, audited_calls, gate


class LedgerTests(unittest.TestCase):
    def setUp(self):
        ls.clear_delivery_ledger()

    def tearDown(self):
        ls.clear_delivery_ledger()

    def test_record_delivery_stores_decoded_pixels(self):
        # INTENTIONAL (P1): pixels come from the bytes actually
        # sent — the decoded JPEG size and bytes are the record.
        self.assertTrue(ls.record_delivery(
            _jpeg_bytes(120, 60, (10, 20, 30)),
            capture_record={"capture_id": 7, "image_width": 120,
                            "image_height": 60}))
        frame = ls.last_delivered_frame()
        self.assertIsNotNone(frame)
        self.assertEqual((frame["width"], frame["height"]),
                         (120, 60))
        self.assertEqual(frame["capture_id"], 7)
        self.assertEqual(len(frame["gray"]), 120 * 60)

    def test_record_delivery_rejects_undecodable_bytes(self):
        # INTENTIONAL (P1): undecodable bytes record nothing —
        # never a fake frame in the slot.
        self.assertFalse(ls.record_delivery(b"not an image"))
        self.assertIsNone(ls.last_delivered_frame())

    def test_second_delivery_overwrites_first(self):
        # INTENTIONAL (P1): single slot, no history — the second
        # delivery replaces the first, and the old id then serves
        # nothing (the slot holds the LAST delivered frame only).
        ls.record_delivery(_jpeg_bytes(100, 50, (1, 2, 3)),
                           capture_record={"capture_id": 1})
        ls.record_delivery(_jpeg_bytes(80, 40, (9, 9, 9)),
                           capture_record={"capture_id": 2})
        frame = ls.last_delivered_frame()
        self.assertEqual(frame["capture_id"], 2)
        self.assertEqual((frame["width"], frame["height"]),
                         (80, 40))

    def test_geometry_copy_cannot_mutate_slot(self):
        # INTENTIONAL (P1): last_delivered_frame copies the
        # geometry dict, so a caller cannot mutate the slot.
        ls.record_delivery(_jpeg_bytes(60, 30, (5, 5, 5)),
                           capture_record={"capture_id": 3})
        frame = ls.last_delivered_frame()
        frame["geometry"]["capture_id"] = 999
        self.assertEqual(ls.last_delivered_frame()["capture_id"], 3)


class SendLevelLedgerTests(unittest.TestCase):
    """P1 placement: send_image_frame records ONLY on delivered."""

    def setUp(self):
        ls.clear_delivery_ledger()
        # System python has no google-genai (types=None in the
        # module) while the Qt venv has it — a fake Blob keeps
        # the send-level tests exercising the REAL method on
        # both interpreters. The Blob's content is irrelevant to
        # the ledger (it decodes image_bytes directly).
        from core.voice import live_client_send as _lcs
        self._lcs = _lcs
        self._old_types = _lcs.types

        class _FakeBlob:
            def __init__(self, data=None, mime_type=None):
                self.data = data
                self.mime_type = mime_type

        _lcs.types = SimpleNamespace(Blob=_FakeBlob)

    def tearDown(self):
        ls.clear_delivery_ledger()
        self._lcs.types = self._old_types

    def _mixin(self, session, connected):
        from core.voice.live_client_send import (
            GeminiLiveSessionSendMixin)
        mixin = object.__new__(GeminiLiveSessionSendMixin)
        mixin._session = session
        mixin.is_connected = connected
        return mixin

    def test_delivered_records_ledger(self):
        # INTENTIONAL (P1): a successful send records the frame
        # it actually sent — the ledger baseline is the delivered
        # frame, not the capture slot.
        class _Session:
            async def send_realtime_input(self, **kwargs):
                return None

        mixin = self._mixin(_Session(), True)
        status = asyncio.run(mixin.send_image_frame(
            _jpeg_bytes(120, 60, (10, 20, 30))))
        self.assertEqual(status, "delivered")
        frame = ls.last_delivered_frame()
        self.assertIsNotNone(frame)
        self.assertEqual((frame["width"], frame["height"]),
                         (120, 60))

    def test_no_session_records_nothing(self):
        # INTENTIONAL (P1): a frame that never delivered (no
        # session) must NOT become the baseline — the slot would
        # otherwise claim the model saw a frame it never got.
        mixin = self._mixin(None, False)
        status = asyncio.run(mixin.send_image_frame(
            _jpeg_bytes(120, 60, (10, 20, 30))))
        self.assertEqual(status, "no-session")
        self.assertIsNone(ls.last_delivered_frame())

    def test_failed_send_records_nothing(self):
        # INTENTIONAL (P1): a failed send records nothing, even
        # though the bytes existed — delivery, not existence,
        # is what the ledger tracks.
        class _FailingSession:
            async def send_realtime_input(self, **kwargs):
                raise RuntimeError("boom")

        mixin = self._mixin(_FailingSession(), True)
        status = asyncio.run(mixin.send_image_frame(
            _jpeg_bytes(120, 60, (10, 20, 30))))
        self.assertEqual(status, "error")
        self.assertIsNone(ls.last_delivered_frame())


class MarkerTests(unittest.TestCase):
    def test_marker_set_reset_cycle(self):
        # INTENTIONAL (P2): default False; set True; reset
        # restores False — the marker cannot leak to the next call.
        self.assertFalse(lo.is_live_origin())
        token = lo.set_live_origin()
        try:
            self.assertTrue(lo.is_live_origin())
        finally:
            lo.reset_live_origin(token)
        self.assertFalse(lo.is_live_origin())

    def test_marker_survives_nested_call(self):
        # INTENTIONAL (P2): route_/toolbox re-entry is a nested
        # call on the same thread — the marker must still read
        # True inside it, which is what the funnel stage keys on.
        token = lo.set_live_origin()
        try:
            seen = []

            def _nested():
                seen.append(lo.is_live_origin())

            _nested()
            self.assertEqual(seen, [True])
        finally:
            lo.reset_live_origin(token)
        self.assertFalse(lo.is_live_origin())

    def test_execute_tool_sync_marks_and_clears(self):
        # INTENTIONAL (P2): _execute_tool_sync marks the context
        # for the duration of the call and clears it in finally
        # — proven with a recording fake registry (no real funnel
        # run, no handler).
        from core.voice import live_client_receive as lr
        seen = []

        class _FakeTools:
            def execute(self, tool, call_args):
                seen.append((tool, call_args, lo.is_live_origin()))
                return {"status": "success", "kind": "ok",
                        "message": "ok", "result": "ok"}

        old_tools = lr.tools
        lr.tools = _FakeTools()
        try:
            self.assertFalse(lo.is_live_origin())
            result = lr.GeminiLiveSessionReceiveMixin._execute_tool_sync(
                SimpleNamespace(name="cursor_control",
                                args={"action": "click", "x": 500,
                                      "y": 500}))
            self.assertEqual(
                seen, [("cursor_control",
                        {"action": "click", "x": 500, "y": 500},
                        True)])
            self.assertFalse(lo.is_live_origin())
            self.assertEqual(result.get("status"), "success")
        finally:
            lr.tools = old_tools
            # Defensive: never leak the marker into other tests.
            if lo.is_live_origin():
                lo._LIVE_ORIGIN.set(False)


class HelperTests(unittest.TestCase):
    def test_resize_produces_fixed_small_grid(self):
        # INTENTIONAL (P3): both frames land on the same small
        # grid (160x90) regardless of input size — the compare
        # is consistent even when geometry changed.
        rows = ls._resize_to_grid(200, 100, bytes([128]) * 20000)
        self.assertEqual(len(rows), 90)
        self.assertEqual(len(rows[0]), 160)
        self.assertTrue(all(v == 128 for row in rows for v in row))

    def test_resize_rejects_unusable(self):
        # INTENTIONAL (P3): length mismatch / empty input is
        # unusable (None), never a fabricated grid.
        self.assertIsNone(ls._resize_to_grid(10, 10, b"short"))
        self.assertIsNone(ls._resize_to_grid(0, 0, b""))
        self.assertIsNone(ls._resize_to_grid(10, 10, b""))

    def test_compare_identical_matches(self):
        # INTENTIONAL (P3): identical grids match with zero diff.
        rows = [[100] * 4 for _ in range(3)]
        out = ls._compare_grids(rows, rows, mean_max=12.0,
                                large_diff=48, frac_large_max=0.10)
        self.assertTrue(out["match"])
        self.assertEqual(out["mean_abs_diff"], 0.0)
        self.assertEqual(out["frac_large"], 0.0)

    def test_compare_changed_mismatches_with_numbers(self):
        # INTENTIONAL (P3): fully different grids mismatch with
        # measured numbers, not a bare boolean.
        a = [[0] * 4 for _ in range(3)]
        b = [[255] * 4 for _ in range(3)]
        out = ls._compare_grids(a, b, mean_max=12.0, large_diff=48,
                                frac_large_max=0.10)
        self.assertFalse(out["match"])
        self.assertEqual(out["mean_abs_diff"], 255.0)
        self.assertEqual(out["frac_large"], 1.0)

    def test_compare_thresholds_are_parameters(self):
        # INTENTIONAL (P3): thresholds are NAMED parameters, not
        # hardcoded C7 constants — P5 retunes them without
        # touching the compare code.
        a = [[0] * 4 for _ in range(3)]
        b = [[100] * 4 for _ in range(3)]
        strict = ls._compare_grids(a, b, mean_max=12.0, large_diff=48,
                                   frac_large_max=0.10)
        self.assertFalse(strict["match"])
        loose = ls._compare_grids(a, b, mean_max=1000.0,
                                  large_diff=48, frac_large_max=1.0)
        self.assertTrue(loose["match"])

    def test_no_verifier_import_no_normalise_grid(self):
        # INTENTIONAL (P3): the helper must not import the
        # verifier (the funnel's no-verifier-import pin must
        # keep passing transitively) and must not CALL
        # normalise_grid (pure-Python full-frame cost). AST
        # names, not substring — the docstring legitimately
        # NAMES normalise_grid while forbidding its use (the
        # gap test's own immunity-to-comments rule).
        src = Path(ls.__file__).read_text()
        tree = ast.parse(src)
        roots = set()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    roots.add(node.module.split(".")[0])
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        self.assertNotIn("control_existence", roots)
        self.assertNotIn("normalise_grid", names)
        self.assertIn("BILINEAR", src)


class CheckTests(unittest.TestCase):
    def setUp(self):
        ls.clear_delivery_ledger()

    def tearDown(self):
        ls.clear_delivery_ledger()

    def test_fresh_view_proceeds(self):
        # INTENTIONAL (P3): identical baseline + fresh grab is
        # fresh — the action proceeds.
        ls.record_delivery(_jpeg_bytes(64, 64, (77, 77, 77)))
        frame = ls.last_delivered_frame()
        out = ls.check_staleness(
            ledger_frame=frame,
            grab=lambda: (frame["width"], frame["height"],
                          frame["gray"]))
        self.assertEqual(out["verdict"], "fresh")
        self.assertEqual(out["mean_abs_diff"], 0.0)

    def test_changed_view_is_stale(self):
        # INTENTIONAL (P3): a fully different fresh grab is
        # stale with measured numbers — positive evidence only.
        ls.record_delivery(_jpeg_bytes(64, 64, (0, 0, 0)))
        frame = ls.last_delivered_frame()
        out = ls.check_staleness(
            ledger_frame=frame,
            grab=lambda: _solid_frame(64, 64, 255))
        self.assertEqual(out["verdict"], "stale")
        self.assertGreater(out["mean_abs_diff"], 0)

    def test_no_baseline_passes_through_without_grab(self):
        # INTENTIONAL (P3, positive-evidence-only): with no
        # delivered frame the check cannot judge — it passes
        # through WITHOUT even grabbing (nothing to compare
        # against), labelled, never a refusal.
        calls = []
        out = ls.check_staleness(
            ledger_frame=None, grab=lambda: calls.append(1) or
            _solid_frame(64, 64, 128))
        self.assertEqual(out["verdict"], "unchecked")
        self.assertIn("no-baseline", out["reason"])
        self.assertEqual(calls, [])

    def test_grab_failure_passes_through(self):
        # INTENTIONAL (P3): a failed grab must not brick live
        # voice — pass through labelled.
        ls.record_delivery(_jpeg_bytes(64, 64, (0, 0, 0)))
        frame = ls.last_delivered_frame()
        out = ls.check_staleness(ledger_frame=frame,
                                 grab=lambda: None)
        self.assertEqual(out["verdict"], "unchecked")
        self.assertIn("grab-failed", out["reason"])

    def test_over_budget_passes_through(self):
        # INTENTIONAL (P3, design §5.3): over budget is NEVER a
        # refusal — slowness is not evidence of staleness.
        ls.record_delivery(_jpeg_bytes(64, 64, (0, 0, 0)))
        frame = ls.last_delivered_frame()
        out = ls.check_staleness(
            ledger_frame=frame,
            grab=lambda: _solid_frame(64, 64, 255),
            budget_ms=0.0)
        self.assertEqual(out["verdict"], "unchecked")
        self.assertIn("slow-grab", out["reason"])

    def test_check_never_raises(self):
        # INTENTIONAL (P3): a raising grab and a malformed
        # baseline both read as unchecked, never an exception
        # escaping into the funnel.
        def _raising():
            raise RuntimeError("grab exploded")

        out = ls.check_staleness(ledger_frame=None, grab=_raising)
        self.assertEqual(out["verdict"], "unchecked")
        out = ls.check_staleness(ledger_frame={"width": 64},
                                 grab=_raising)
        self.assertEqual(out["verdict"], "unchecked")


class FilterTests(unittest.TestCase):
    def test_cursor_absolute_actions_in_scope(self):
        # INTENTIONAL (P4 scope, design §3.3): the seven
        # absolute-target cursor actions with coordinates are in.
        for action in ("move", "click", "double_click",
                       "right_click", "middle_click", "drag",
                       "beacon"):
            self.assertTrue(ls.is_live_point_action(
                "cursor_control",
                {"action": action, "x": 500, "y": 500}), action)
            self.assertTrue(ls.is_live_point_action(
                "cursor_control",
                {"action": action, "fx": 0.5, "fy": 0.5}), action)

    def test_cursor_relative_and_spotless_out_of_scope(self):
        # INTENTIONAL (P4 scope): relative actions, spotless
        # clicks (no absolute target), and park are OUT.
        for action in ("move_by", "nudge", "move_fraction",
                       "scroll", "park", "position"):
            self.assertFalse(ls.is_live_point_action(
                "cursor_control",
                {"action": action, "x": 500, "y": 500}), action)
        self.assertFalse(ls.is_live_point_action(
            "cursor_control", {"action": "click"}))
        self.assertFalse(ls.is_live_point_action(
            "cursor_control", {"action": "click", "x": 500}))

    def test_mouse_absolute_actions_in_scope(self):
        # INTENTIONAL (P4 scope): mouse absolute actions with
        # true-pixel x/y are in; scroll (delta) is out.
        for action in ("move", "click", "double_click",
                       "right_click"):
            self.assertTrue(ls.is_live_point_action(
                "mouse_control",
                {"action": action, "x": 100, "y": 200}), action)
        self.assertFalse(ls.is_live_point_action(
            "mouse_control", {"action": "scroll", "x": 100,
                              "y": 200}))
        self.assertFalse(ls.is_live_point_action(
            "mouse_control", {"action": "click"}))

    def test_filter_total_on_garbage(self):
        # INTENTIONAL (P4): malformed input never raises the
        # filter — it reads as out-of-scope, and the funnel's
        # validator refuses the garbage later.
        self.assertFalse(ls.is_live_point_action("cursor_control",
                                                 None))
        self.assertFalse(ls.is_live_point_action("cursor_control",
                                                 {"action": "click",
                                                  "x": "bogus",
                                                  "y": 500}))
        self.assertFalse(ls.is_live_point_action("not_a_tool",
                                                 {"action": "click",
                                                  "x": 1, "y": 2}))


class StageUnitTests(unittest.TestCase):
    """P4 stage behaviour through the mixin with a fake ctx."""

    def setUp(self):
        from core.funnel.tools_registry_exec_gates import (
            ToolRegistryExecGatesMixin)
        self.stage = object.__new__(ToolRegistryExecGatesMixin)
        self.activity = []
        self.stage._log_activity = lambda name, status: \
            self.activity.append((name, status))
        self._old_check = ls.check_staleness
        self.calls = []

    def tearDown(self):
        ls.check_staleness = self._old_check
        if lo.is_live_origin():
            lo._LIVE_ORIGIN.set(False)

    def _run(self, name, args, live, verdict):
        def _fake_check(*args_, **kwargs):
            self.calls.append(True)
            return verdict

        ls.check_staleness = _fake_check
        token = lo.set_live_origin() if live else None
        try:
            ctx, audited, gate = _fake_ctx(name, args)
            result = self.stage._exec_live_staleness(ctx)
            return result, audited, gate
        finally:
            if token is not None:
                lo.reset_live_origin(token)

    def test_live_stale_refuses_before_dispatch(self):
        # INTENTIONAL (P4): live stale point action refuses with
        # kind stale-view, audit gate refused:stale-view, and an
        # Activity line — the handler never runs.
        result, audited, gate = self._run(
            "cursor_control", {"action": "click", "x": 500,
                               "y": 500}, True,
            {"verdict": "stale", "reason": "view changed",
             "mean_abs_diff": 200.0, "frac_large": 0.9,
             "elapsed_ms": 5.0})
        self.assertIsNotNone(result)
        self.assertEqual(result["kind"], "stale-view")
        self.assertEqual(result["status"], "error")
        self.assertNotIn("result", result)
        self.assertIn("nothing was clicked", result["message"])
        self.assertIn("Re-observe", result["message"])
        self.assertEqual(gate["value"], "refused:stale-view")
        self.assertEqual(audited, [result])
        self.assertTrue(any("stale view" in s for _, s in
                            self.activity))

    def test_live_fresh_proceeds_silently(self):
        # INTENTIONAL (P4): a fresh view proceeds with NO
        # Activity line — nothing to report when the check passes.
        result, _, _ = self._run(
            "cursor_control", {"action": "click", "x": 500,
                               "y": 500}, True,
            {"verdict": "fresh", "reason": "matches",
             "mean_abs_diff": 0.0, "frac_large": 0.0,
             "elapsed_ms": 5.0})
        self.assertIsNone(result)
        self.assertEqual(self.activity, [])

    def test_live_unchecked_passes_through_labelled(self):
        # T-12 (debug pass): the INTENTIONAL (P4) comment above
        # predates the fail-closed design — an unchecked view now
        # REFUSES with kind stale-view (GAPS-13: an unverifiable
        # world refuses, never passes through), the same shape as
        # the stale refusal in the sibling test. The reason names
        # the grab failure, and the Activity line carries it.
        result, _, _ = self._run(
            "cursor_control", {"action": "click", "x": 500,
                               "y": 500}, True,
            {"verdict": "unchecked", "reason": "grab-failed: boom",
             "mean_abs_diff": None, "frac_large": None,
             "elapsed_ms": 5.0})
        self.assertIsNotNone(result)
        self.assertEqual(result["kind"], "stale-view")
        self.assertEqual(result["status"], "error")
        self.assertNotIn("result", result)
        self.assertIn("grab-failed: boom", result["message"])
        self.assertIn("nothing was clicked", result["message"])
        self.assertTrue(any("stale view: unchecked" in s and
                            "grab-failed" in s
                            for _, s in self.activity))

    def test_non_live_never_invokes_helper(self):
        # INTENTIONAL (P4): without the marker the helper is
        # never called — zero behaviour change outside live,
        # proven by a recording fake, not by absence of output.
        result, _, _ = self._run(
            "cursor_control", {"action": "click", "x": 500,
                               "y": 500}, False,
            {"verdict": "stale", "reason": "view changed",
             "mean_abs_diff": 200.0, "frac_large": 0.9,
             "elapsed_ms": 5.0})
        self.assertIsNone(result)
        self.assertEqual(self.calls, [])

    def test_live_out_of_scope_never_invokes_helper(self):
        # INTENTIONAL (P4 scope): live mark clicks, window
        # control, relative actions, and spotless clicks never
        # reach the helper even with the marker set.
        stale = {"verdict": "stale", "reason": "view changed",
                 "mean_abs_diff": 200.0, "frac_large": 0.9,
                 "elapsed_ms": 5.0}
        for name, args in (
                ("click_by_mark", {"mark_id": 3}),
                ("window_control", {"action": "focus",
                                    "title_substring": "x"}),
                ("cursor_control", {"action": "scroll", "x": 1,
                                    "y": 2}),
                ("cursor_control", {"action": "click"}),
                ("take_screenshot", {})):
            result, _, _ = self._run(name, args, True, stale)
            self.assertIsNone(result, (name, args))
        self.assertEqual(self.calls, [])

    def test_stage_position_between_toolbox_and_confirm(self):
        # INTENTIONAL (P4, design §3.4): the stage runs after
        # toolbox (true inner tool name) and before confirm (no
        # wasted approval) — pinned by the declared EXECUTE_STAGES
        # tuple (GAPS-23 F-V4), not by a run that could execute
        # a handler, and not by sequential call sites.
        from core.funnel.tools_registry import EXECUTE_STAGES
        stages = list(EXECUTE_STAGES)
        self.assertLess(stages.index("_exec_toolbox"),
                        stages.index("_exec_live_staleness"))
        self.assertLess(stages.index("_exec_live_staleness"),
                        stages.index("_exec_confirm"))


class RefusalVocabularyTests(unittest.TestCase):
    def test_stale_view_normalizes_to_refused(self):
        # INTENTIONAL (P4): stale-view is a REFUSAL kind — the
        # audited result must read status "refused" (call never
        # ran), never "failure" (which would claim the click ran
        # and failed).
        from core.funnel.tool_result import (
            REFUSAL_KINDS, normalize_result)
        self.assertIn("stale-view", REFUSAL_KINDS)
        out = normalize_result({"status": "error",
                                "kind": "stale-view",
                                "message": "x"})
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["kind"], "stale-view")


class RealRegistryTests(unittest.TestCase):
    """End-to-end through the real registry — refusal paths only,
    so no handler ever runs."""

    def setUp(self):
        self._old_check = ls.check_staleness

    def tearDown(self):
        ls.check_staleness = self._old_check
        if lo.is_live_origin():
            lo._LIVE_ORIGIN.set(False)

    def test_live_stale_refused_by_real_registry(self):
        # INTENTIONAL (P4 end-to-end): live marker + stale
        # baseline + valid click args → the real funnel refuses
        # stale-view before dispatch. The handler never runs.
        from core.funnel.tools_registry import tools
        ls.check_staleness = lambda **kwargs: {
            "verdict": "stale", "reason": "view changed",
            "mean_abs_diff": 200.0, "frac_large": 0.9,
            "elapsed_ms": 5.0}
        token = lo.set_live_origin()
        try:
            result = tools.execute(
                "cursor_control",
                {"action": "click", "x": 500, "y": 500})
        finally:
            lo.reset_live_origin(token)
        self.assertEqual(result.get("kind"), "stale-view")
        self.assertEqual(result.get("status"), "refused")
        self.assertNotIn("result", result)

    def test_non_live_stale_not_refused_by_stage(self):
        # INTENTIONAL (P4 gating, real registry): without the
        # marker, a would-be-stale call is NOT refused by the
        # stage — proven with an out-of-scope action (refused by
        # validation later, never by staleness, and no handler
        # runs for an unknown action).
        from core.funnel.tools_registry import tools
        calls = []
        ls.check_staleness = lambda **kwargs: calls.append(1) or {
            "verdict": "stale", "reason": "view changed",
            "mean_abs_diff": 200.0, "frac_large": 0.9,
            "elapsed_ms": 5.0}
        result = tools.execute(
            "cursor_control", {"action": "bogus_action_xyz",
                               "x": 500, "y": 500})
        self.assertEqual(calls, [])
        self.assertNotEqual(result.get("kind"), "stale-view")


if __name__ == "__main__":
    unittest.main()

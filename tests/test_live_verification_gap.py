"""Live-loop verifier coverage gap pins (2026-11-05, design batch).

GAP (derived from code reads 2026-11-05; the parent verifies it
from disk; full map + insertion design in
LIVE_VERIFIER_COVERAGE_DESIGN.md at the tree root):

The C7 verifier guards coordinator paths (C3 re-issue, pending
records, stored-crop points). The LIVE loop bypasses it entirely:
live_client_receive._execute_tool_sync turns a Gemini Live
function call (name + args ONLY) through tool_envelope into
tools.execute, with no StepDef, no observation, and no verifier
consulted. The funnel NEVER imports control_existence. So the
highest-volume coordinate path — live model clicks — runs on
the cursor_control geometry floor alone, with no content
verification of any kind.

WHAT IS ALREADY PROTECTED (pinned, not rebuilt):
- Geometry floor in-handler: cursor_control consults
  _capture_freshness_refusal (virtual bounds changed,
  foreground-crop window moved/resized/replaced). Grid moves
  only — in-window content change, same-rect swaps, and
  intervening captures with a different crop are NOT visible
  to it.
- Mark staleness in-handler: click_by_mark refuses when the
  foreground window changed since the marks were drawn
  (_marks_stale_reason) and validates the centre against
  current bounds + a fresh monitor table.
- Family-C tools are NOT in _CONFIRM_GATE_TOOLS: live point
  actions never pop a funnel dialog today.

WHY BINDING IS IMPOSSIBLE HERE (not a missing wire):
A live point carries no stored crop and no authored-frame
provenance, and the vision slot holds the LATEST CAPTURED
frame — not the frame the model read the point off (it fills
at capture time, not delivery time; send_image_frame can
return "no-session"/"error"; the model holds many frames,
the slot holds one). Stamping a live point with the slot's
id would make the check pass because the stamper just wrote
the id — the tautology the brain-stamp batch (2026-11-05)
refused. The honest insertion is a STALENESS check (fresh
grab vs last DELIVERED frame), designed in the doc above.
UPDATE (live staleness batch, 2026-11-05): P1 (delivery ledger),
P2 (live-origin marker) and P4 (funnel stage) are now BUILT —
see core/desktop/live_staleness.py, core/funnel/live_origin.py
and tests/test_live_staleness.py. The pins below still hold
exactly as written (the envelope still drops origin — the
marker travels via contextvar, a different channel; the vision
slot still carries no delivery field — the ledger is separate);
only the prose claim "neither exists today" is retired by this
update. Live BINDING verification remains impossible (1.4
below is unchanged) — the stage checks staleness, never binding.

Headless by construction: no test needs a screen, a live
session, or a UIA walk. Static pins use ast (immune to
comment/string false positives); behavioural pins use fake
backends / recording fakes, never the real registry.
"""

import ast
import unittest
from pathlib import Path

# INTENTIONAL (live-coverage design batch): these pins assert
# ABSENCE — the gap itself — so a future wiring batch must
# update this file (and the design doc) rather than assume
# coverage that was never there.
_TREE = Path(__file__).resolve().parent.parent
_FUNNEL_DIR = _TREE / "core" / "funnel"
_LIVE_RECEIVE = _TREE / "core" / "voice" / "live_client_receive.py"

_CONTENT_REFUSAL_CODES = frozenset({
    "region-changed", "region-unobserved", "capture-mismatch"})


def _imported_module_roots(path):
    """Module roots imported anywhere in one file (ast, all scopes)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
    return roots


def _function_source_segment(path, func_name):
    """Source text of one function def (ast segment, exact)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(path.read_text(), node) or ""
    return ""


class LiveGapAbsenceTests(unittest.TestCase):
    def test_funnel_never_imports_verifier(self):
        # INTENTIONAL: the funnel has no observation supplier
        # and no verifier import in ANY of its modules — a
        # funnel verification stage has nowhere to read from
        # today. A future stage must add the supplier first.
        offenders = []
        for path in sorted(_FUNNEL_DIR.glob("*.py")):
            if "control_existence" in _imported_module_roots(path):
                offenders.append(path.name)
        self.assertEqual(offenders, [], offenders)

    def test_live_receive_never_imports_verifier(self):
        # INTENTIONAL: the live receive loop — the path every
        # live model click travels — never imports the
        # verifier, in any scope.
        self.assertNotIn(
            "control_existence",
            _imported_module_roots(_LIVE_RECEIVE))

    def test_execute_tool_sync_carries_no_observation(self):
        # INTENTIONAL: _execute_tool_sync reads fc.name +
        # fc.args and nothing else — no verifier, no capture
        # id, no stored crop, no observation of any kind.
        # A live tool call arrives with name + args only.
        segment = _function_source_segment(
            _LIVE_RECEIVE, "_execute_tool_sync")
        self.assertTrue(segment, "function not found")
        tree = ast.parse(segment)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        banned = {"control_existence", "verify",
                  "verify_before_confirm", "last_sent_frame",
                  "last_capture_info", "stored_crop", "capture_id"}
        self.assertEqual(names & banned, set(), names & banned)

    def test_envelope_drops_live_origin(self):
        # INTENTIONAL (prerequisite P2): from_live_call marks
        # the invocation source="live", but execute_envelope
        # passes ONLY (tool, args) to the funnel — the origin
        # never crosses the ENVELOPE boundary. UPDATE (live
        # staleness batch, 2026-11-05): the funnel CAN now tell
        # a live call from any other call — via P2's contextvar
        # marker set in _execute_tool_sync, a separate channel
        # from the envelope. This pin still asserts exactly what
        # it always did (the envelope passes tool + args only);
        # it no longer implies the funnel is blind to origin.
        from core.funnel.tool_envelope import (
            execute_envelope, from_live_call)
        args = {"action": "click", "x": 500, "y": 500}
        inv = from_live_call("cursor_control", args)
        self.assertEqual(inv.source, "live")
        seen = []

        def _recording_execute(tool, call_args):
            seen.append((tool, call_args))
            return {"status": "success", "kind": "ok",
                    "message": "ok", "result": "ok"}

        outcome = execute_envelope(inv, _recording_execute)
        self.assertEqual(outcome.status, "success")
        self.assertEqual(seen, [("cursor_control", args)])
        # The funnel call carried no origin: two elements only,
        # tool + args — nothing a stage could key on.

    def test_verify_without_observation_cannot_content_refuse(self):
        # INTENTIONAL (C4 lesson, restated for live): calling
        # verify() on a bare live point with NO fresh
        # observation cannot refuse for content reasons — the
        # point adapter passes through geometry-unverified
        # (or a geometry code) when it has nothing to check
        # against. Wiring verify() into the funnel without an
        # observation supplier would be theatre, not
        # protection: region/capture refusals need a stored
        # crop / source id the live path does not carry.
        from core.desktop import control_existence as cev
        bare = {"kind": "point", "description": "live spot"}
        for fresh in ({}, {"capture_info": {"capture_id": 9}}):
            verdict = cev.verify(bare, fresh)
            refusal = verdict.get("refusal") or {}
            self.assertNotIn(refusal.get("code"),
                             _CONTENT_REFUSAL_CODES, verdict)

    def test_slot_carries_no_delivery_status(self):
        # INTENTIONAL (prerequisite P1): the vision slot fills
        # at CAPTURE time, not delivery time, and its record
        # carries no delivery field — so the slot cannot tell
        # "frame the model saw" from "frame merely captured".
        # A staleness baseline built on the slot would be the
        # dishonest baseline; the delivery ledger comes first.
        from PIL import Image
        from core.desktop import vision_manager as vm
        mgr = vm.VisionManager()
        frame = Image.new("RGB", (200, 100))
        frame.putdata([(x % 256, y % 256, (x + y) % 256)
                       for y in range(100) for x in range(200)])
        old_routed = vm._routed_backend
        old_raw = vm._backend_raw_full_frame
        vm._routed_backend = lambda: object()
        vm._backend_raw_full_frame = lambda backend: (frame.copy(), None)
        try:
            mgr.capture_screen(marks=False)
            slot = mgr.last_sent_frame()
            self.assertIsNotNone(slot)
            self.assertNotIn("delivered", slot)
            self.assertNotIn("delivery", slot)
            self.assertNotIn("delivered", slot.get("record") or {})
        finally:
            vm._routed_backend = old_routed
            vm._backend_raw_full_frame = old_raw


class LiveAlreadyProtectedTests(unittest.TestCase):
    def test_geometry_floor_present_in_cursor_control(self):
        # INTENTIONAL: the geometry floor already runs
        # in-handler for every funnel caller including live —
        # the staleness stage complements it (content moves),
        # it does not replace it (grid moves).
        src = (_TREE / "core" / "desktop" / "cursor_control.py"
               ).read_text()
        self.assertIn("_capture_freshness_refusal", src)

    def test_mark_staleness_present_in_click_by_mark(self):
        # INTENTIONAL: mark-based live clicks already refuse
        # on stale marks in-handler — no staleness stage
        # needed for click_by_mark.
        src = (_TREE / "core" / "desktop" / "uia.py").read_text()
        self.assertIn("_marks_stale_reason", src)

    def test_family_c_tools_have_no_funnel_dialog(self):
        # INTENTIONAL (mirrors the C4 pin): live point
        # actions never pop a funnel dialog today, so a
        # verification stage placed before confirm adds no
        # dialog — and a stale destructive action refuses
        # before any approval is wasted.
        from core.funnel import tools_gate_confirm as _gate
        for tool in ("cursor_control", "mouse_control",
                     "window_control"):
            self.assertNotIn(tool, _gate._CONFIRM_GATE_TOOLS)


class LiveReentryTests(unittest.TestCase):
    def test_toolbox_call_reenters_execute(self):
        # INTENTIONAL: use_toolbox action=call executes the
        # inner tool through registry.execute — so a future
        # funnel stage sees the TRUE inner tool name, not
        # "use_toolbox".
        segment = _function_source_segment(
            _TREE / "core" / "funnel" / "toolbox_findcall.py", "_call")
        self.assertIn("registry.execute(name, args)", segment)

    def test_route_dispatch_reenters_execute(self):
        # INTENTIONAL: domain-router calls execute the inner
        # tool through registry.execute — same funnel, same
        # stage visibility as a direct call.
        segment = _function_source_segment(
            _TREE / "core" / "brain" / "domain_routers.py",
            "dispatch_route")
        self.assertIn("registry.execute(tool_name, args)", segment)


if __name__ == "__main__":
    unittest.main()

"""§7 consumers batch 5 — coordinator refusal rewrite, generalized.

Batch 4 found the seam for menus: funnel handlers render engine
refusals as SUCCESS-carrying sentences, so a refused menu path
recorded as a successful coordinator step. Batch 4 rewrote the
two menu shapes; parent verification then confirmed the same
pattern for every other §7 tool. This batch generalizes the
rewrite to all of them (dialog, surface, adapter, drag,
monitor, journal) in core.brain.task_coordinator._rewrite_s7_refusal.

This suite proves, per tool:

- Every handler refusal-sentence shape (read off the handler
  code, never guessed) becomes a refused step failure with the
  tool's kind and the engine's sentence verbatim — including
  engine confirm stops (batch-4 precedent: a stop fails rather
  than silently succeeding; the re-issue-after-approval
  protocol is still owed, not invented here).
  INTENTIONAL (§7 consumers batch 6): batch 6 BUILT that
  protocol, so the five confirm-stop entries below moved from
  REFUSALS to CONFIRM_STOPS and now assert PAUSED with a
  pending-approval record instead of failed — the shapes are
  still covered, under their new outcome. Genuine refusals
  still fail exactly as before.
- Success lines never rewrite — including the dialog success
  line that shares the "File dialog " opener, the adapter
  generic-fallback line, and the journal "complete" / "no undo
  available" lines that share openers with refusal shapes.
- The skip rule still converts a refused step of a non-menu
  tool to skipped; funnel gate refusals (not_confirmed) are
  untouched (they already failed steps); non-§7 steps are
  untouched even when their result text looks like a refusal;
  non-string results and non-success statuses pass through.
- The menu rewrite is intact through the new dispatcher, and
  a dialog step survives the ledger round-trip.
- Through the REAL funnel (batch-4 setup: DataGuard, taint
  cleared, session provenance "user", confirm_gate toggled):
  dialog, surface, journal, drag, monitor, and adapter legs
  each refuse honestly in this lab and rewrite to their kind.

Stdlib unittest only. Fake executors mimic funnel shapes
(status/result strings), exactly as production delivers them.
"""

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain import task_ledger as tl  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/


def _step(tool, args, **kw):
    mapping = {"tool": tool, "args": args}
    mapping.update(kw)
    return tc.StepDef.from_mapping(mapping)


def _fake_executor(handler):
    """Executor recording calls, delegating to handler(tool, args)."""
    def execute(tool, args):
        execute.calls.append((tool, dict(args)))
        return handler(tool, args)
    execute.calls = []
    return execute


def _run(steps, handler, **coord_kw):
    executor = _fake_executor(handler)
    coord = tc.TurnCoordinator(executor=executor, **coord_kw)
    task = coord.new_task("s7 goal", "routine", steps)
    return coord.run_task(task), executor


def _failed_for(tool, args, result_text, **step_kw):
    def handler(t, a):
        return {"status": "success", "result": result_text}
    receipt, _ = _run([_step(tool, args, **step_kw)], handler)
    return receipt


# ---------------------------------------------------------------------------
# Per-tool refusal shapes → refused failures
# ---------------------------------------------------------------------------

# (tool, args, refusal sentence, expected kind)
REFUSALS = [
    ("file_dialog_action",
     {"action": "open", "filename": "nope.txt"},
     "File dialog open refused: no file named 'nope.txt' in this dialog",
     "dialog_refused"),
    ("file_dialog_action",
     {"action": "save", "filename": "a.txt"},
     "File dialog save refused: that dialog is a Save dialog, not Open",
     "dialog_refused"),
    ("file_dialog_action",
     {"action": "folder"},
     "File dialog folder refused: that dialog is not a folder picker",
     "dialog_refused"),
    ("system_surface_action",
     {"surface": "tray", "action": "open_menu", "target": "Volume"},
     "Surface refused (wall-refused): the lock screen refuses automation",
     "surface_refused"),
    ("system_surface_action",
     {"surface": "tray", "action": "invoke_path", "target": "Volume",
      "path": "Open"},
     "refused (tray-ambiguous): more than one icon is named 'Volume'",
     "surface_refused"),
    ("system_surface_action",
     {"surface": "notification", "action": "act",
      "notification_id": "n-1"},
     "refused (notification-expired): that notification has expired",
     "surface_refused"),
    ("adapter_action",
     {"phrase": "open history"},
     "Adapter mismatch: the foreground does not identify 'chrome' "
     "(no adapter matches this foreground) — nothing resolves under "
     "the wrong adapter.",
     "adapter_refused"),
    ("adapter_action",
     {"phrase": "frobnicate the zanzibar"},
     "Adapter resolution refused (unknown-phrase): no adapter phrase "
     "matches 'frobnicate the zanzibar'",
     "adapter_refused"),
    ("drag_drop_action",
     {"source_handle_id": "h-1", "target_handle_id": "h-2",
      "payload": {"kind": "text", "text": "hi"}},
     "Drag refused (handle-refused): the target handle did not re-prove",
     "drag_refused"),
    ("monitor_action",
     {"action": "set_resolution"},
     "Monitor action refused: display configuration changes "
     "(resolution, arrangement, primary, scale, rotation) are out of "
     "scope — this tool adapts to the arrangement, it never "
     "rearranges it.",
     "monitor_refused"),
    ("monitor_action",
     {"action": "resolve", "reference": "third monitor",
      "nx": 0.5, "ny": 0.5},
     "Monitor resolve refused: no monitor matches 'third monitor'",
     "monitor_refused"),
    ("monitor_action",
     {"action": "convert", "reference": "second monitor",
      "direction": "logical_to_physical", "x": 1, "y": 1},
     "Monitor convert refused: the scale for that monitor is unreadable",
     "monitor_refused"),
    ("monitor_action",
     {"action": "place", "rect": [0, 0, 100, 100],
      "from_reference": "first monitor", "to_reference": "second monitor"},
     "Monitor place refused: the rect does not fit on that monitor",
     "monitor_refused"),
    ("journal_action",
     {"action": "list"},
     "Journal unreadable: data/task_receipts.jsonl line 3 is corrupt\n"
     "Manual repair path (no automatic repair):\n"
     "- restore the file from backup",
     "journal_refused"),
    ("journal_action",
     {"action": "reconcile", "receipt_id": "no-such-entry",
      "happened": True, "confirmed": True, "confirmed_by": "user"},
     "Reconcile refused: reconcile refuses: no journal entry for "
     "receipt 'no-such-entry'",
     "journal_refused"),
    ("journal_action",
     {"action": "compensate", "receipt_id": "e-1",
      "confirmed": True, "confirmed_by": "user"},
     "Compensation refused: reconcile refuses: no journal entry for "
     "receipt 'e-1'",
     "journal_refused"),
    ("journal_action",
     {"action": "compensate", "receipt_id": "e-1",
      "confirmed": True, "confirmed_by": "user"},
     "Compensation stopped for entry e-1: the first compensation "
     "failed\nStopped at: file_control",
     "journal_refused"),
]


# INTENTIONAL (§7 consumers batch 6): confirm-stop entries moved
# out of REFUSALS — batch 6 built the re-issue-after-approval
# protocol, so these shapes now PAUSE with a pending-approval
# record instead of failing. (tool, args, sentence, confirm flag)
CONFIRM_STOPS = [
    ("file_dialog_action",
     {"action": "save", "filename": "a.txt"},
     "File dialog save stops for confirmation: saving would overwrite "
     "'a.txt' — confirm first.",
     "confirm_overwrite"),
    ("adapter_action",
     {"phrase": "close tab"},
     "Adapter resolution refused (confirm-required): 'close tab' is a "
     "confirm-gated phrase. Confirm it explicitly, then resolve again.",
     "confirmed"),
    ("drag_drop_action",
     {"source_handle_id": "h-1", "target_handle_id": "h-2",
      "payload": {"kind": "files", "files": ["a.txt"]}},
     "Drag refused (destructive-needs-confirmation): the target is a "
     "trash target. Confirm it explicitly, then drag again with "
     "confirm_destructive set.",
     "confirm_destructive"),
    ("journal_action",
     {"action": "reconcile", "receipt_id": "e-1"},
     "Reconciliation needs confirmation: reconcile is a "
     "user-confirmed action, so this call stops with "
     "needs_confirmation and writes nothing — confirm it "
     "explicitly, then reconcile again with confirmed set.",
     "confirmed"),
    ("journal_action",
     {"action": "compensate", "receipt_id": "e-1"},
     "Compensation needs confirmation: running declared "
     "compensations is effectful, so this call stops with "
     "needs_confirmation and runs nothing — confirm it "
     "explicitly, then compensate again with confirmed set.",
     "confirmed"),
]


class RefusalRewriteTests(unittest.TestCase):

    def test_every_tool_refusal_shape_fails_with_its_kind(self):
        for tool, args, sentence, kind in REFUSALS:
            with self.subTest(tool=tool, kind=kind):
                receipt = _failed_for(tool, args, sentence)
                self.assertEqual(receipt.state, "failed", sentence)
                self.assertEqual(receipt.error_kind, kind, sentence)
                self.assertIn(sentence, receipt.reason, sentence)

    def test_engine_confirm_stops_now_pause(self):
        # INTENTIONAL (§7 consumers batch 6): these shapes used
        # to fail under the batch-4/5 precedent; batch 6 built
        # the re-issue-after-approval protocol, so a stop now
        # pauses with a pending-approval record (full
        # stop→approve→resolve chains live in the batch-6
        # suite; here the pause itself is pinned per shape).
        for tool, args, sentence, flag in CONFIRM_STOPS:
            with self.subTest(tool=tool):
                def handler(t, a, s=sentence):
                    return {"status": "success", "result": s}
                executor = _fake_executor(handler)
                coord = tc.TurnCoordinator(executor=executor)
                task = coord.new_task("s7 goal", "routine",
                                      [_step(tool, args)])
                receipt = coord.run_task(task)
                self.assertEqual(receipt.state, "paused", sentence)
                pending = coord.pending_approval(task.id)
                self.assertEqual(pending["tool"], tool)
                self.assertEqual(pending["sentence"], sentence)
                self.assertEqual(pending["confirm_arg"], flag)

    def test_menu_rewrite_intact_through_new_dispatcher(self):
        receipt = _failed_for(
            "menu_action", {"action": "invoke", "path": "File → Delete"},
            "Menu path File → Delete refused: 'Delete' is disabled")
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "menu_refused")

    def test_refused_step_with_skip_on_fail_skips_non_menu_tool(self):
        receipt = _failed_for(
            "system_surface_action",
            {"surface": "tray", "action": "open_menu", "target": "Volume"},
            "Surface refused (tray-ambiguous): more than one icon is "
            "named 'Volume'",
            on_fail="skip")
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.steps[0]["status"], "skipped")

    def test_gate_refusal_untouched(self):
        def handler(tool, args):
            return {"status": "refused", "kind": "not_confirmed",
                    "message": "approval required"}
        receipt, _ = _run(
            [_step("file_dialog_action",
                   {"action": "open", "filename": "a.txt"})], handler)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "not_confirmed")

    def test_non_s7_step_untouched_even_with_refusal_text(self):
        # Per-tool scoping: a non-§7 step whose result text looks
        # like a surface refusal must NOT be rewritten.
        def handler(tool, args):
            return {"status": "success",
                    "result": "Surface refused (wall-refused): test"}
        receipt, _ = _run(
            [_step("file_control", {"path": "a.txt"})], handler)
        self.assertEqual(receipt.state, "completed")

    def test_non_string_result_untouched(self):
        def handler(tool, args):
            return {"status": "success", "result": {"ok": False}}
        receipt, _ = _run(
            [_step("drag_drop_action",
                   {"source_handle_id": "h-1", "target_handle_id": "h-2",
                    "payload": {"kind": "text", "text": "hi"}})],
            handler)
        self.assertEqual(receipt.state, "completed")

    def test_non_success_status_untouched(self):
        def handler(tool, args):
            return {"status": "failure", "kind": "backend-down",
                    "message": "the backend is unavailable"}
        receipt, _ = _run(
            [_step("monitor_action",
                   {"action": "resolve", "reference": "second monitor",
                    "nx": 0.5, "ny": 0.5})],
            handler)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "backend-down")


# ---------------------------------------------------------------------------
# Success lines never rewritten (per tool, incl. shared openers)
# ---------------------------------------------------------------------------

SUCCESS_LINES = [
    ("file_dialog_action",
     {"action": "open", "filename": "a.txt"},
     "File dialog open C:\\notes\\a.txt (file handle f-1) — verified"),
    ("file_dialog_action",
     {"action": "save", "filename": "b.txt"},
     "File dialog save C:\\notes\\b.txt — invoked-without-verification"),
    ("system_surface_action",
     {"surface": "tray", "action": "open_menu", "target": "Volume"},
     "tray: open_menu — Volume (target handle t-1 — pass it back as "
     "target_handle_id on the next call for this target.)"),
    ("system_surface_action",
     {"surface": "start", "action": "search", "query": "Notepad"},
     "Start results:\n- Notepad (app, id 7)"),
    ("system_surface_action",
     {"surface": "tray", "action": "list"},
     "Tray icons:\n- Volume (id 3)"),
    ("adapter_action",
     {"phrase": "open history"},
     "No profile for this app — using generic controls. "
     "No adapter matches this foreground."),
    ("adapter_action",
     {"phrase": "new tab"},
     "Resolved 'new tab' via chrome — menu path: File → New Tab"),
    ("drag_drop_action",
     {"source_handle_id": "h-1", "target_handle_id": "h-2",
      "payload": {"kind": "text", "text": "hi"}},
     "Dragged text (2 chars) onto folder via ole (effect: moved; "
     "verification: dropped-without-verification)."),
    ("monitor_action",
     {"action": "list"},
     "Monitors:\n- DISPLAY1 (1920x1080)"),
    ("monitor_action",
     {"action": "resolve", "reference": "second monitor",
      "nx": 0.5, "ny": 0.5},
     "Resolved DISPLAY2; bounds 1920,0 1920x1080."),
    ("monitor_action",
     {"action": "convert", "reference": "first monitor",
      "direction": "logical_to_physical", "x": 100, "y": 100},
     "Converted (100.0, 100.0) -> (125, 125) on DISPLAY1."),
    ("monitor_action",
     {"action": "place", "rect": [0, 0, 100, 100],
      "from_reference": "first monitor", "to_reference": "second monitor"},
     "Placed rect on DISPLAY2: (1920, 0, 2020, 100)."),
    ("journal_action",
     {"action": "list"},
     "Journal entries (newest first):\n- abc press_hotkey\n"
     "Counts: retryable 0, done 1, unknown 0."),
    ("journal_action",
     {"action": "plan"},
     "Resume plan (read-only — nothing re-runs):\n"
     "Retryable (never dispatched — safe to retry as new attempts): 0."),
    ("journal_action",
     {"action": "reconcile", "receipt_id": "e-1",
      "happened": False, "confirmed": True, "confirmed_by": "user"},
     "Reconciled entry e-1: was unknown, now failed. "
     "effect confirmed absent."),
    ("journal_action",
     {"action": "compensate", "receipt_id": "e-1",
      "confirmed": True, "confirmed_by": "user"},
     "Compensation complete for entry e-1: 2 step(s) verified."),
    ("journal_action",
     {"action": "compensate", "receipt_id": "e-1",
      "confirmed": True, "confirmed_by": "user"},
     "No undo available for entry e-1 — the executor is never called."),
    ("journal_action",
     {"action": "plan", "receipt_id": "no-such-entry"},
     "No journal entry for receipt 'no-such-entry'."),
    ("menu_action",
     {"action": "invoke", "path": "File → Save As"},
     "Menu path File → Save As invoked — invoked-without-verification"),
]


class SuccessLinesTests(unittest.TestCase):

    def test_success_lines_never_rewritten(self):
        for tool, args, text in SUCCESS_LINES:
            with self.subTest(tool=tool, text=text[:40]):
                def handler(t, a, text=text):
                    return {"status": "success", "result": text}
                receipt, _ = _run([_step(tool, args)], handler)
                self.assertEqual(receipt.state, "completed", text)


# ---------------------------------------------------------------------------
# Ledger round-trip unaffected (dialog step)
# ---------------------------------------------------------------------------

class LedgerTests(unittest.TestCase):

    def test_dialog_step_survives_ledger_rebuild(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            ledger = tl.TaskLedger(
                path=pathlib.Path(tmp.name) / "task_ledger.json")
            coord = tc.TurnCoordinator(
                executor=_fake_executor(
                    lambda t, a: {"status": "success", "result": "ok"}),
                ledger=ledger)
            step = _step("file_dialog_action",
                         {"action": "open", "filename": "a.txt"})
            task = coord.new_task("dialog goal", "routine", [step])
            coord.pause_task(task.id)
            coord.run_task(task)
            record = ledger.get(task.id)
            self.assertIsNotNone(record)
            rebuilt = tl.TaskLedger().rebuild_task(record)
            kept = rebuilt.steps[0]
            self.assertEqual(kept.tool, "file_dialog_action")
            self.assertEqual(kept.args["action"], "open")
            self.assertEqual(kept.args["filename"], "a.txt")
        finally:
            tmp.cleanup()


# ---------------------------------------------------------------------------
# Real funnel: gate + rewrite through tools.execute
# ---------------------------------------------------------------------------

class _DataGuard:
    """Restore data/ after funnel executes (batch-1 pattern)."""

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


class RealFunnelTests(unittest.TestCase):
    """Coordinator + real funnel: each §7 leg refuses honestly."""

    def setUp(self):
        from core import confirm_gate, injection
        from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
        from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
        from core.config import config as app_config
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._tools_mod = tools_mod
        self._tools = tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False

    def tearDown(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        self._tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()

    def _run_tool(self, tool, args, approve=False):
        from core import confirm_gate
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            coord = tc.TurnCoordinator(
                executor=self._tools.execute)
            task = coord.new_task(
                "s7 goal", "routine", [_step(tool, args)])
            return coord.run_task(task)
        finally:
            confirm_gate.clear_handler()

    def test_dialog_unapproved_refused_at_funnel_gate(self):
        receipt = self._run_tool(
            "file_dialog_action",
            {"action": "open", "filename": "a.txt"}, approve=False)
        self.assertEqual(receipt.state, "failed")
        self.assertIn(receipt.error_kind, ("not_confirmed", "refused"))

    def test_dialog_approved_engine_refusal_rewritten(self):
        # The lab has no dialog backend, so the approved call runs
        # the handler and the engine refuses honestly.
        receipt = self._run_tool(
            "file_dialog_action",
            {"action": "open", "folder": "C:\\notes",
             "filename": "a.txt"}, approve=True)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "dialog_refused")
        self.assertIn("refused", receipt.reason)

    def test_surface_approved_engine_refusal_rewritten(self):
        receipt = self._run_tool(
            "system_surface_action",
            {"surface": "tray", "action": "open_menu",
             "target": "Volume"}, approve=True)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "surface_refused")
        self.assertIn("refused", receipt.reason)

    def test_journal_reconcile_unknown_entry_rewritten(self):
        # Gate pops for reconcile (approve=True); the handler then
        # refuses the unknown receipt id honestly.
        receipt = self._run_tool(
            "journal_action",
            {"action": "reconcile", "receipt_id": "no-such-entry",
             "happened": False, "confirmed": True,
             "confirmed_by": "user",
             "verification": "user confirmed it did not happen"},
            approve=True)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "journal_refused")
        self.assertIn("Reconcile refused", receipt.reason)

    def test_drag_approved_engine_refusal_rewritten(self):
        receipt = self._run_tool(
            "drag_drop_action",
            {"source_handle_id": "h-nope",
             "target_handle_id": "h-nope2",
             "payload": {"kind": "text", "text": "hi"}},
            approve=True)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "drag_refused")
        self.assertIn("refused", receipt.reason)

    def test_monitor_resolve_unknown_reference_rewritten(self):
        # Monitor reads never gate — no approval needed.
        receipt = self._run_tool(
            "monitor_action",
            {"action": "resolve", "reference": "no such monitor",
             "nx": 0.5, "ny": 0.5})
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "monitor_refused")
        self.assertIn("refused", receipt.reason)

    def test_adapter_unknown_phrase_rewritten(self):
        # Explicit chrome foreground lets the gate resolve purely:
        # the unknown phrase needs no popup, and the handler
        # refuses it honestly. (A foreground matching NO adapter
        # would fall back to generic controls — a legitimate
        # success, not a refusal — so the foreground must match
        # an adapter for this leg.)
        receipt = self._run_tool(
            "adapter_action",
            {"phrase": "frobnicate the zanzibar",
             "foreground": {"proc": "chrome.exe",
                            "window_class": "Chrome_WidgetWin_1",
                            "title": "New Tab"}})
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "adapter_refused")
        self.assertIn("refused", receipt.reason)


if __name__ == "__main__":
    unittest.main()

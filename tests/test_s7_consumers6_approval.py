"""§7 consumers batch 6 — coordinator re-issue-after-approval protocol.

Batches 4/5 left engine confirm stops failing as refusals: a voice
goal that hit a confirm-gated path died instead of asking the user
and continuing. This batch changes ONLY that outcome: an engine
needs_confirmation stop pauses the task with a pending-approval
record (step, tool, label, engine sentence verbatim, confirm flag
name); the GUI reports its authorization-dialog outcome through
resolve_pending — approval re-issues exactly the pending step with
exactly its engine confirmed flag, denial fails honestly
(on_fail=skip still skips), and the dialog's silence-timeout
arrives as denial. Genuine refusals still fail exactly as
batches 4/5 left them; funnel gate refusals (not_confirmed)
never pause; a spoken "yes" never resolves; a model-set flag on
the step's args is never consulted; the one-shot re-issue flag is
in-memory only, so a prior approval never carries across a
restart (a rebuilt pending task re-stops on resume until it is
resolved again).

Stop shapes are read off the engine/handler renderings, never
guessed (menu C-14 reason in menu_paths.py invoke_path; dialog
describe() in file_dialogs.py; surface stop code
"needs-confirmation" via system_surfaces.describe(); adapter
confirmation-required in app_adapters.py resolve() + the pack
handler suffix; drag describe() + the pack handler suffix;
journal handler literals in pack_s7consumers2.py). Grounding
tests below call the real describe()/resolve() functions; the
real-funnel test drives a genuine chrome "close tab" stop
through the funnel.

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
    return coord.run_task(task), executor, coord, task


def _ok(result="done"):
    return {"status": "success", "result": result}


def _stop(sentence):
    return {"status": "success", "result": sentence}


# --- Real stop sentences (see module docstring for sources) --------

MENU_STOP = ("Menu path File → Delete refused: 'Delete' is an "
             "effectful menu action — confirm it first "
             "(C-14 approve-first) before it is invoked.")
MENU_REFUSAL = "Menu path File → Open refused: item is disabled"
DIALOG_STOP = ("File dialog save stops for confirmation: saving "
               "would overwrite 'a.txt' — confirm first.")
DIALOG_REFUSAL = ("File dialog open refused: no file named "
                  "'nope.txt' in this dialog")
SURFACE_STOP = ("refused (needs-confirmation): pinning 'Chrome' "
                "changes taskbar state, so it needs your "
                "confirmation first — confirm and retry.")
SURFACE_REFUSAL = ("refused (tray-ambiguous): more than one icon "
                   "is named 'Volume'")
ADAPTER_STOP = ("Adapter resolution refused "
                "(confirmation-required): adapter 'chrome' action "
                "'action:close tab' needs confirmation — confirm "
                "it explicitly, then resolve again. Confirm it "
                "explicitly, then resolve again.")
ADAPTER_OK_FLAGGED = ("Resolved 'close tab' via chrome — menu path: "
                      "File → Close Tab (confirmation still required)")
ADAPTER_REFUSAL = ("Adapter resolution refused (unknown-phrase): "
                   "no adapter phrase matches 'frobnicate'")
DRAG_STOP = ("Drag refused (destructive-target): the target is "
             "destructive. Confirm it explicitly, then drag again "
             "with confirm_destructive set.")
DRAG_REFUSAL = ("Drag refused (handle-refused): the target handle "
                "did not re-prove")
JOURNAL_STOP_R = ("Reconciliation needs confirmation: entry 'r-1' "
                  "has no verified outcome — confirm it explicitly, "
                  "then reconcile again.")
JOURNAL_STOP_C = ("Compensation needs confirmation: entry 'r-1' "
                  "needs a confirmer — confirm it explicitly, then "
                  "compensate again.")
JOURNAL_REFUSAL = "Reconcile refused: no journal entry 'nope'"

# (tool, args, stop sentence, confirm flag name)
STOPS = [
    ("menu_action",
     {"action": "invoke", "path": "File → Delete"},
     MENU_STOP, "confirmed"),
    ("file_dialog_action",
     {"action": "save", "filename": "a.txt"},
     DIALOG_STOP, "confirm_overwrite"),
    ("system_surface_action",
     {"surface": "taskbar", "action": "pin", "target": "Chrome"},
     SURFACE_STOP, "confirmed"),
    ("adapter_action",
     {"phrase": "close tab"},
     ADAPTER_STOP, "confirmed"),
    ("drag_drop_action",
     {"source_handle_id": "h-1", "target_handle_id": "h-2",
      "payload": {"kind": "text", "text": "hi"}},
     DRAG_STOP, "confirm_destructive"),
    ("journal_action",
     {"action": "reconcile", "receipt_id": "r-1"},
     JOURNAL_STOP_R, "confirmed"),
    ("journal_action",
     {"action": "compensate", "receipt_id": "r-1"},
     JOURNAL_STOP_C, "confirmed"),
]

# (tool, args, refusal sentence, batch-4/5 kind) — must still fail.
REFUSALS = [
    ("menu_action",
     {"action": "invoke", "path": "File → Open"},
     MENU_REFUSAL, "menu_refused"),
    ("file_dialog_action",
     {"action": "open", "filename": "nope.txt"},
     DIALOG_REFUSAL, "dialog_refused"),
    ("system_surface_action",
     {"surface": "tray", "action": "open_menu", "target": "Volume"},
     SURFACE_REFUSAL, "surface_refused"),
    ("adapter_action",
     {"phrase": "frobnicate the zanzibar"},
     ADAPTER_REFUSAL, "adapter_refused"),
    ("drag_drop_action",
     {"source_handle_id": "h-1", "target_handle_id": "h-2",
      "payload": {"kind": "text", "text": "hi"}},
     DRAG_REFUSAL, "drag_refused"),
    ("journal_action",
     {"action": "reconcile", "receipt_id": "nope"},
     JOURNAL_REFUSAL, "journal_refused"),
]


class DetectorTests(unittest.TestCase):
    """Stop detection: exact shapes pause, near-misses do not."""

    def test_every_stop_shape_detected_with_its_flag(self):
        for tool, _args, sentence, flag in STOPS:
            with self.subTest(tool=tool):
                found = tc._detect_s7_confirmation_stop(
                    tool, {"status": "success", "result": sentence})
                self.assertEqual(found, (sentence, flag))

    def test_every_refusal_shape_is_not_a_stop(self):
        for tool, _args, sentence, _kind in REFUSALS:
            with self.subTest(tool=tool):
                self.assertIsNone(tc._detect_s7_confirmation_stop(
                    tool, {"status": "success", "result": sentence}))

    def test_non_success_non_string_non_s7_never_stop(self):
        cases = [
            ("menu_action", {"status": "refused", "kind": "not_confirmed",
                             "message": "no approval"}),
            ("menu_action", {"status": "success", "result": None}),
            ("menu_action", {"status": "success", "result": 42}),
            ("get_current_time_date", {"status": "success",
                                       "result": MENU_STOP}),
            ("monitor_action", {"status": "success",
                                "result": "Monitor resolve refused: x"}),
        ]
        for tool, out in cases:
            with self.subTest(tool=tool, out=out):
                self.assertIsNone(tc._detect_s7_confirmation_stop(tool, out))

    def test_adapter_ok_flagged_line_is_success_not_stop(self):
        # The resolution happened; the flag travels to the next
        # step (the menu/dialog action it resolves for). Only a
        # refused resolution pauses.
        self.assertIsNone(tc._detect_s7_confirmation_stop(
            "adapter_action",
            {"status": "success", "result": ADAPTER_OK_FLAGGED}))

    def test_menu_stop_needs_the_c14_phrase(self):
        # A menu refusal whose reason merely mentions confirmation
        # in other words is a refusal, not a stop.
        self.assertIsNone(tc._detect_s7_confirmation_stop(
            "menu_action",
            {"status": "success",
             "result": "Menu path File → Save refused: please confirm "
                       "the file is not in use"}))


class GroundingTests(unittest.TestCase):
    """Canned sentences match the real engine renderings."""

    def test_menu_c14_describe_matches_detector(self):
        from core.desktop import menu_paths as mp  # INTENTIONAL (Phase 3 S7a): menu_paths moved to core/desktop/
        rendered = mp.describe({
            "ok": False, "path": ["File", "Delete"],
            "refusal": {"code": "confirm-required",
                        "reason": "'Delete' is an effectful menu action "
                                  "— confirm it first (C-14 "
                                  "approve-first) before it is "
                                  "invoked."}})
        self.assertEqual(rendered, MENU_STOP)
        self.assertIsNotNone(tc._detect_s7_confirmation_stop(
            "menu_action", {"status": "success", "result": rendered}))

    def test_dialog_describe_matches_detector(self):
        from core.desktop import file_dialogs as fd  # INTENTIONAL (Phase 3 S7a): file_dialogs moved to core/desktop/
        rendered = fd.describe({
            "ok": False, "action": "save", "needs_confirmation": True,
            "refusal": {"reason": "saving would overwrite 'a.txt'"}})
        self.assertIn("stops for confirmation", rendered)
        self.assertIsNotNone(tc._detect_s7_confirmation_stop(
            "file_dialog_action",
            {"status": "success", "result": rendered}))

    def test_surface_describe_matches_detector(self):
        from core.desktop import system_surfaces as ss  # INTENTIONAL (Phase 3 S7a): system_surfaces moved to core/desktop/
        rendered = ss.describe({
            "ok": False,
            "refusal": {"code": "needs-confirmation",
                        "reason": "needs your confirmation first"}})
        self.assertEqual(rendered,
                         "refused (needs-confirmation): needs your "
                         "confirmation first")
        self.assertIsNotNone(tc._detect_s7_confirmation_stop(
            "system_surface_action",
            {"status": "success", "result": rendered}))

    def test_drag_describe_plus_handler_suffix_matches_detector(self):
        from core.desktop import drag_drop as dd  # INTENTIONAL (Phase 3 S7a): drag_drop moved to core/desktop/
        rendered = dd.describe({
            "ok": False,
            "refusal": {"code": "destructive-target",
                        "reason": "the target is destructive"}})
        rendered += (" Confirm it explicitly, then drag again with "
                     "confirm_destructive set.")
        self.assertIsNotNone(tc._detect_s7_confirmation_stop(
            "drag_drop_action",
            {"status": "success", "result": rendered}))

    def test_adapter_engine_stop_fields(self):
        # The real resolution dict for a confirm-gated phrase:
        # refused with code confirmation-required and the
        # needs_confirmation marker the handler suffixes.
        # (Registration is duplicate-safe: re-registering an
        # already-registered adapter is a no-op.)
        from core.desktop import app_adapters as aa  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
        from core.desktop.chrome_adapter import register_chrome_adapter  # INTENTIONAL (Phase 3 S7b): moved to core/desktop/
        register_chrome_adapter()
        foreground = {"proc": "chrome.exe",
                      "window_class": "Chrome_WidgetWin_1",
                      "title": "New Tab"}
        resolution = aa.resolve("close tab", foreground=foreground,
                                confirmed=False)
        self.assertFalse(resolution.get("ok"))
        self.assertTrue(resolution.get("needs_confirmation"))
        self.assertEqual((resolution.get("refusal") or {}).get("code"),
                         "confirmation-required")
        self.assertIn("confirm it explicitly, then resolve again.",
                      (resolution.get("refusal") or {}).get("reason", ""))


class PauseTests(unittest.TestCase):
    """A stop pauses: no outcome, index held, record named."""

    def test_every_stop_pauses_with_full_record(self):
        for tool, args, sentence, flag in STOPS:
            with self.subTest(tool=tool):
                receipt, _ex, coord, task = _run(
                    [_step(tool, args, label="gated")],
                    lambda t, a: _stop(sentence))
                self.assertEqual(receipt.state, "paused")
                self.assertEqual(receipt.steps, [])
                self.assertEqual(receipt.attempted, 0)
                self.assertEqual(receipt.remaining, ["gated"])
                self.assertIn("awaiting approval", receipt.reason)
                pending = coord.pending_approval(task.id)
                self.assertEqual(pending, {
                    "step_index": 0, "tool": tool, "label": "gated",
                    "sentence": sentence, "confirm_arg": flag})
                # The record carries no timer: the coordinator
                # sets no timeout — silence-timeout is the
                # dialog's deny, delivered via resolve_pending.
                self.assertEqual(set(pending),
                                 {"step_index", "tool", "label",
                                  "sentence", "confirm_arg"})

    def test_refusal_still_fails_never_pauses(self):
        for tool, args, sentence, kind in REFUSALS:
            with self.subTest(tool=tool):
                receipt, _ex, coord, task = _run(
                    [_step(tool, args)],
                    lambda t, a: _stop(sentence))
                self.assertEqual(receipt.state, "failed")
                self.assertEqual(receipt.error_kind, kind)
                self.assertIsNone(coord.pending_approval(task.id))

    def test_funnel_gate_refusal_never_pauses(self):
        receipt, _ex, coord, task = _run(
            [_step("menu_action",
                   {"action": "invoke", "path": "File → Delete"})],
            lambda t, a: {"status": "refused", "kind": "not_confirmed",
                          "message": "no approval"})

        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "not_confirmed")
        self.assertIsNone(coord.pending_approval(task.id))

    def test_non_s7_stop_like_text_is_success(self):
        receipt, _ex, _c, _t = _run(
            [_step("get_current_time_date", {})],
            lambda t, a: _stop(DIALOG_STOP))
        self.assertEqual(receipt.state, "completed")

    def test_prior_steps_preserved_on_pause(self):
        calls = []

        def handler(t, a):
            calls.append(t)
            if t == "menu_action":
                return _stop(MENU_STOP)
            return _ok("ok")

        receipt, _ex, _c, task = _run(
            [_step("get_current_time_date", {}, label="first"),
             _step("menu_action",
                   {"action": "invoke", "path": "File → Delete"},
                   label="gated"),
             _step("get_current_time_date", {}, label="third")],
            handler)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual([s["label"] for s in receipt.steps], ["first"])
        self.assertEqual(receipt.remaining, ["gated", "third"])
        self.assertEqual(task._index, 1)
        self.assertEqual(calls, ["get_current_time_date", "menu_action"])


class ResolveTests(unittest.TestCase):
    """Approval re-issues with the flag; denial fails honestly."""

    def _stopping_handler(self, stop_sentence, ok_result="invoked"):
        """Engine-faithful stop: stops until the confirm flag is set.

        The handler succeeds only when the call carries a confirm
        flag (confirmed / confirm_destructive / confirm_overwrite)
        — so a re-issue without approval stops again, and only
        the approval path (which sets the flag) resolves.
        """
        def handler(tool, args):
            if args.get("confirmed") or args.get("confirm_destructive") \
                    or args.get("confirm_overwrite"):
                return _ok(ok_result)
            return _stop(stop_sentence)

        return handler

    def test_approve_reissues_with_flag_and_continues(self):
        def handler(t, a):
            if t == "menu_action":
                if a.get("confirmed"):
                    return _ok("invoked")
                return _stop(MENU_STOP)
            return _ok("ok")

        receipt, executor, coord, task = _run(
            [_step("get_current_time_date", {}, label="first"),
             _step("menu_action",
                   {"action": "invoke", "path": "File → Delete"},
                   label="gated"),
             _step("get_current_time_date", {}, label="third")],
            handler)
        self.assertEqual(receipt.state, "paused")
        # The original step args are never mutated by the pause.
        self.assertNotIn("confirmed", task.steps[1].args)
        receipt2 = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt2.state, "completed")
        self.assertEqual([s["label"] for s in receipt2.steps],
                         ["first", "gated", "third"])
        menu_calls = [c for t, c in executor.calls if t == "menu_action"]
        self.assertEqual(len(menu_calls), 2)
        self.assertNotIn("confirmed", menu_calls[0])
        self.assertTrue(menu_calls[1].get("confirmed"))
        self.assertIsNone(coord.pending_approval(task.id))

    def test_approve_per_tool_flag(self):
        cases = [
            ("file_dialog_action",
             {"action": "save", "filename": "a.txt"},
             DIALOG_STOP, "confirm_overwrite"),
            ("drag_drop_action",
             {"source_handle_id": "h-1", "target_handle_id": "h-2",
              "payload": {"kind": "text", "text": "hi"}},
             DRAG_STOP, "confirm_destructive"),
            ("journal_action",
             {"action": "reconcile", "receipt_id": "r-1"},
             JOURNAL_STOP_R, "confirmed"),
        ]
        for tool, args, sentence, flag in cases:
            with self.subTest(tool=tool):
                handler = self._stopping_handler(sentence)
                receipt, executor, coord, task = _run(
                    [_step(tool, args)], handler)
                self.assertEqual(receipt.state, "paused")
                receipt2 = coord.resolve_pending(task.id, approved=True)
                self.assertEqual(receipt2.state, "completed")
                tool_calls = [c for t, c in executor.calls if t == tool]
                self.assertEqual(len(tool_calls), 2)
                self.assertTrue(tool_calls[1].get(flag))

    def test_deny_fails_honestly(self):
        receipt, _ex, coord, task = _run(
            [_step("menu_action",
                   {"action": "invoke", "path": "File → Delete"})],
            lambda t, a: _stop(MENU_STOP))
        self.assertEqual(receipt.state, "paused")
        receipt2 = coord.resolve_pending(task.id, approved=False)
        self.assertEqual(receipt2.state, "failed")
        self.assertEqual(receipt2.error_kind, "approval_denied")
        self.assertIn(MENU_STOP, receipt2.reason)
        self.assertIsNone(coord.pending_approval(task.id))

    def test_silence_timeout_arrives_as_denial(self):
        # The dialog contract (S-1, confirmed 2026-11-03 under the
        # code-rulings delegation): no visible ring; 60 s silence = deny.
        # The coordinator sets no timer of its own; the GUI delivers
        # the dialog's silence outcome as denial.
        # INTENTIONAL (F1/F3 record-correction, 2026-11-03): comment
        # wording only; the test asserts behaviour, which is unchanged.
        receipt, _ex, coord, task = _run(
            [_step("journal_action",
                   {"action": "reconcile", "receipt_id": "r-1"})],
            lambda t, a: _stop(JOURNAL_STOP_R))
        receipt2 = coord.resolve_pending(task.id, approved=False)
        self.assertEqual(receipt2.state, "failed")
        self.assertEqual(receipt2.error_kind, "approval_denied")

    def test_deny_skip_continues(self):
        calls = []

        def handler(t, a):
            calls.append(t)
            if t == "menu_action":
                return _stop(MENU_STOP)
            return _ok("ok")

        receipt, _ex, coord, task = _run(
            [_step("menu_action",
                   {"action": "invoke", "path": "File → Delete"},
                   label="gated", on_fail="skip"),
             _step("get_current_time_date", {}, label="after")],
            handler)
        self.assertEqual(receipt.state, "paused")
        receipt2 = coord.resolve_pending(task.id, approved=False)
        self.assertEqual(receipt2.state, "completed")
        self.assertEqual([(s["label"], s["status"])
                          for s in receipt2.steps],
                         [("gated", "skipped"), ("after", "success")])

    def test_resume_without_resolve_restops(self):
        calls = []

        def handler(t, a):
            calls.append(dict(a))
            return _stop(MENU_STOP)

        receipt, _ex, coord, task = _run(
            [_step("menu_action",
                   {"action": "invoke", "path": "File → Delete"})],
            handler)
        self.assertEqual(receipt.state, "paused")
        receipt2 = coord.resume_task(task.id)
        self.assertEqual(receipt2.state, "paused")
        # No approval was given, so the re-run carries no flag
        # and stops again; the step still has no outcome.
        self.assertEqual(len(calls), 2)
        self.assertNotIn("confirmed", calls[1])
        self.assertEqual(receipt2.steps, [])
        self.assertIsNotNone(coord.pending_approval(task.id))

    def test_resolve_on_non_pending_returns_none(self):
        receipt, _ex, coord, task = _run(
            [_step("get_current_time_date", {})],
            lambda t, a: _ok("ok"))
        self.assertEqual(receipt.state, "completed")
        self.assertIsNone(coord.resolve_pending(task.id, approved=True))
        self.assertIsNone(coord.resolve_pending("no-such-id",
                                                approved=True))
        # A plain-paused task (no pending record) also resolves
        # to None, not to an invented approval.
        task2 = coord.new_task("p", "routine",
                               [_step("get_current_time_date", {})])
        coord.pause_task(task2.id)
        self.assertEqual(task2.state, "paused")
        self.assertIsNone(coord.resolve_pending(task2.id, approved=True))

    def test_preconfirmed_step_never_pends(self):
        # A step whose args already carry the flag proceeds
        # (the funnel popup, not the flag, is what gates it) —
        # no pending record is created for it.
        receipt, _ex, coord, task = _run(
            [_step("menu_action",
                   {"action": "invoke", "path": "File → Delete",
                    "confirmed": True})],
            lambda t, a: _ok("invoked"))
        self.assertEqual(receipt.state, "completed")
        self.assertIsNone(coord.pending_approval(task.id))

    def test_handle_carrying_step_chain(self):
        calls = []

        def handler(t, a):
            calls.append(dict(a))
            if a.get("confirmed"):
                return _ok("invoked")
            return _stop(MENU_STOP)

        receipt, _ex, coord, task = _run(
            [_step("menu_action",
                   {"action": "invoke", "path": "File → Delete"},
                   handle_id="h-window-1")],
            handler)
        self.assertEqual(receipt.state, "paused")
        receipt2 = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt2.state, "completed")
        # The re-issue carries BOTH the declared handle
        # (batch-3 injection) and the approval flag.
        self.assertEqual(calls[1].get("window_handle_id"), "h-window-1")
        self.assertTrue(calls[1].get("confirmed"))

    def test_cancel_clears_pending(self):
        receipt, _ex, coord, task = _run(
            [_step("menu_action",
                   {"action": "invoke", "path": "File → Delete"})],
            lambda t, a: _stop(MENU_STOP))
        self.assertEqual(receipt.state, "paused")
        self.assertTrue(coord.cancel_task(task.id))
        self.assertIsNone(coord.pending_approval(task.id))
        self.assertIsNone(coord.resolve_pending(task.id, approved=True))

    def test_out_of_range_pending_returns_none(self):
        task = tc.TurnCoordinator().new_task(
            "p", "routine", [_step("get_current_time_date", {})])
        task.state = "paused"
        task._pending_approval = {"step_index": 99, "tool": "menu_action",
                                  "label": "x", "sentence": "s",
                                  "confirm_arg": "confirmed"}
        coord = tc.TurnCoordinator()
        coord._tasks[task.id] = task
        self.assertIsNone(coord.resolve_pending(task.id, approved=True))


class LedgerRoundTripTests(unittest.TestCase):
    """Pending survives the ledger; approval never does."""

    def test_pending_round_trips_but_approval_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = tl.TaskLedger(path=f"{tmp}/ledger.json")
            calls = []

            def handler(t, a):
                calls.append(dict(a))
                if a.get("confirmed"):
                    return _ok("invoked")
                return _stop(MENU_STOP)

            executor = _fake_executor(handler)
            coord = tc.TurnCoordinator(executor=executor, ledger=ledger)
            task = coord.new_task(
                "s7 goal", "routine",
                [_step("menu_action",
                       {"action": "invoke", "path": "File → Delete"})])
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "paused")
            record = ledger.get(task.id)
            self.assertEqual(record["state"], "paused")
            pending = record["pending_approval"]
            self.assertEqual(pending["tool"], "menu_action")
            self.assertEqual(pending["sentence"], MENU_STOP)
            self.assertEqual(pending["confirm_arg"], "confirmed")
            summary = [s for s in ledger.list_interrupted()
                       if s["task_id"] == task.id][0]
            self.assertEqual(summary["pending_approval"]["tool"],
                             "menu_action")
            # Restart: rebuild + resume WITHOUT approval re-stops
            # (the approval never carried across the restart).
            coord2 = tc.TurnCoordinator(executor=executor, ledger=ledger)
            receipt2 = coord2.resume_ledger_task(record, ledger=ledger)
            self.assertEqual(receipt2.state, "paused")
            task2 = coord2.get_task(task.id)
            self.assertIsNotNone(coord2.pending_approval(task.id))
            # Now resolve: the re-issue carries the flag and the
            # task completes; the final record holds no pending.
            receipt3 = coord2.resolve_pending(task.id, approved=True)
            self.assertEqual(receipt3.state, "completed")
            self.assertIsNone(ledger.get(task.id)["pending_approval"])
            self.assertTrue(task2 is not None)
            menu_calls = [c for t, c in executor.calls
                          if t == "menu_action"]
            self.assertTrue(menu_calls[-1].get("confirmed"))


class RealFunnelTests(unittest.TestCase):
    """Genuine chrome 'close tab' stop through the real funnel."""

    def setUp(self):
        from core import confirm_gate, injection
        from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
        from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
        from core.config import config as app_config
        self._guard = _RealDataGuard()
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
        # The funnel gate stays approved across run + resolve,
        # so the re-issue passes the gate and the engine flag
        # decides it (the layer this protocol bridges).
        confirm_gate.register_handler(lambda n, s: True)

    def tearDown(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        self._tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()

    def test_chrome_close_tab_stop_pauses_then_approves(self):
        coord = tc.TurnCoordinator(
            executor=self._tools.execute)
        task = coord.new_task(
            "s7 goal", "routine",
            [_step("adapter_action",
                   {"phrase": "close tab",
                    "foreground": {"proc": "chrome.exe",
                                   "window_class": "Chrome_WidgetWin_1",
                                   "title": "New Tab"}})])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        pending = coord.pending_approval(task.id)
        self.assertEqual(pending["tool"], "adapter_action")
        self.assertEqual(pending["confirm_arg"], "confirmed")
        self.assertIn("confirmation-required", pending["sentence"])
        receipt2 = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt2.state, "completed")


class _RealDataGuard:
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


if __name__ == "__main__":
    unittest.main()

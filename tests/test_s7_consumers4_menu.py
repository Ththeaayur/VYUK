"""§7 consumers batch 4 — coordinator consumer for menus + consequence verifier.

Menus become usable inside voice goals, not just callable as
funnel tools. This suite proves:

- Build-time validation: a menu_action step names its path
  (invoke without a path, or with an unknown action, is
  rejected at build, never dispatched to guess).
- The consequence verifier (core/desktop/s7_menu_consequence.py): every
  shape it judges — generic window/text shapes still delegated
  to core.desktop.uia.verify_expectation, plus foreground-changed-to,
  foreground-present, window-closed, menu-opened (engine dict
  and funnel describe text), and dialog-opened (rewritten to
  the generic shape) — each in met / not-met / unverifiable,
  with the backend "verified" flag never auto-meting a named
  consequence and exact-name matching for opened menus.
- The refusal rewrite: engine refusal sentences arriving
  through the funnel as success ("Menu path … refused: …",
  "Menu list refused (…)") become refused step failures
  (kind menu_refused) instead of silent successes — for every
  refusal class and for list. INTENTIONAL (§7 consumers
  batch 6): the C-14 engine stop used to fail here too
  (batch-4 precedent); batch 6 built the
  re-issue-after-approval protocol, so a stop now pauses
  with a pending-approval record instead of failing — the
  stop tests below assert the pause, and the full
  stop→approve→resolve chains live in the batch-6 suite.
- The verification phase routes menu steps to the menu
  verifier (with the step's raw executor output as
  invoke-result evidence) and non-menu steps to the generic
  verifier; not-met menu consequences fail the task honestly,
  met / unverifiable complete it with the verdict recorded.
- Confirm stop-then-resolve through the coordinator, handle
  injection (declared handle + explicit-wins), stale-handle
  refusal, ledger round-trip of a menu step, and the same gate
  + rewrite through the REAL funnel (following the batch-1
  funnel-test setup: DataGuard, taint cleared, session
  provenance "user", confirm_gate toggled).

Stdlib unittest only. Fake executors mimic funnel shapes
(status/result strings), exactly as production delivers them;
the real-funnel tests use tools.execute with no menu backend
in this lab, so the engine refuses honestly.
"""

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.desktop import s7_menu_consequence as mc  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain import task_ledger as tl  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/


def _snap(fg, windows, tree="a"):
    return {"fg": fg, "windows": list(windows), "focus": "",
            "texts": [], "tree_hash": tree}


BEFORE = _snap("Notes", ["Notes"])
AFTER_SAME = _snap("Notes", ["Notes"])
AFTER_SWITCH = _snap("Notepad", ["Notes", "Notepad"], tree="b")
AFTER_SAVEAS = _snap("Save As", ["Notes", "Save As"], tree="b")
BEFORE_SAVEAS = _snap("Notes", ["Notes", "Save As"])
AFTER_CLOSED = _snap("Notes", ["Notes"], tree="b")


def _invoked(path, verification="invoked-without-verification"):
    return ("Menu path %s invoked — %s" % (path, verification))


def _opened(path):
    return ("Menu path %s opened — menu opened (no action invoked)"
            % path)


def _refused(path, reason):
    return "Menu path %s refused: %s" % (path, reason)


def _menu_step(path, expect="", **kw):
    mapping = {"tool": "menu_action",
               "args": {"action": "invoke", "path": path}}
    if expect:
        mapping["expect"] = expect
    mapping.update(kw)
    return tc.StepDef.from_mapping(mapping)


def _fake_executor(handler):
    """Executor recording calls, delegating to handler(tool, args)."""
    def execute(tool, args):
        execute.calls.append((tool, dict(args)))
        return handler(tool, args)
    execute.calls = []
    return execute


def _snapshots(*snaps):
    """Snapshot callable yielding the given snapshots in order."""
    queue = list(snaps)

    def take():
        return queue.pop(0) if queue else None
    return take


# ---------------------------------------------------------------------------
# Build-time validation
# ---------------------------------------------------------------------------

class ValidationTests(unittest.TestCase):

    def test_invoke_step_with_path_valid(self):
        step = _menu_step("File → Save As")
        self.assertEqual(step.tool, "menu_action")
        self.assertEqual(step.args["path"], "File → Save As")

    def test_list_step_needs_no_path(self):
        step = tc.StepDef.from_mapping(
            {"tool": "menu_action", "args": {"action": "list"}})
        self.assertEqual(step.args["action"], "list")

    def test_invoke_without_path_rejected(self):
        for args in ({"action": "invoke"},
                     {"action": "invoke", "path": ""},
                     {"action": "invoke", "path": "   "},
                     {"action": "invoke", "path": []},
                     {"action": "invoke", "path": ["", "  "]}):
            with self.assertRaises(ValueError, msg=str(args)):
                tc.StepDef.from_mapping({"tool": "menu_action",
                                         "args": args})

    def test_unknown_action_rejected(self):
        for args in ({"action": "walk"},
                     {"action": ""},
                     {}):
            with self.assertRaises(ValueError, msg=str(args)):
                tc.StepDef.from_mapping({"tool": "menu_action",
                                         "args": args})

    def test_non_menu_steps_unaffected(self):
        step = tc.StepDef.from_mapping(
            {"tool": "file_control", "args": {}})
        self.assertEqual(step.tool, "file_control")


# ---------------------------------------------------------------------------
# Consequence verifier units
# ---------------------------------------------------------------------------

class VerifierTests(unittest.TestCase):

    def test_generic_shapes_still_delegate(self):
        verdict, _ = mc.verify_menu_consequence(
            "a dialog titled Save As appears", BEFORE, AFTER_SAVEAS)
        self.assertEqual(verdict, "met")
        verdict, _ = mc.verify_menu_consequence(
            "a dialog titled Save As appears", BEFORE, AFTER_SAME)
        self.assertEqual(verdict, "not-met")
        verdict, _ = mc.verify_menu_consequence(
            "text Hello present", BEFORE, AFTER_SAME, "")
        self.assertEqual(verdict, "not-met")

    def test_foreground_change_met_notmet_unverifiable(self):
        verdict, _ = mc.verify_menu_consequence(
            "the foreground is now Notepad", BEFORE, AFTER_SWITCH)
        self.assertEqual(verdict, "met")
        verdict, _ = mc.verify_menu_consequence(
            "switched to Notepad", BEFORE, AFTER_SAME)
        self.assertEqual(verdict, "not-met")
        verdict, _ = mc.verify_menu_consequence(
            "the foreground is now Notepad", BEFORE, AFTER_SAME)
        self.assertEqual(verdict, "not-met")
        verdict, _ = mc.verify_menu_consequence(
            "the foreground is now Notepad", BEFORE, None)
        self.assertEqual(verdict, "unverifiable")

    def test_foreground_present_style(self):
        verdict, _ = mc.verify_menu_consequence(
            "foreground is Notepad", BEFORE, AFTER_SWITCH)
        self.assertEqual(verdict, "met")
        verdict, _ = mc.verify_menu_consequence(
            "foreground is Notepad", BEFORE, AFTER_SAME)
        self.assertEqual(verdict, "not-met")

    def test_closed_met_notmet_unverifiable(self):
        verdict, _ = mc.verify_menu_consequence(
            "dialog Save As closed", BEFORE_SAVEAS, AFTER_CLOSED)
        self.assertEqual(verdict, "met")
        verdict, _ = mc.verify_menu_consequence(
            "window Save As disappeared", BEFORE_SAVEAS, AFTER_SAVEAS)
        self.assertEqual(verdict, "not-met")
        # Never present before: closing cannot be confirmed.
        verdict, _ = mc.verify_menu_consequence(
            "dialog Save As closed", BEFORE, AFTER_CLOSED)
        self.assertEqual(verdict, "unverifiable")
        verdict, _ = mc.verify_menu_consequence(
            "dialog Save As closed", None, AFTER_CLOSED)
        self.assertEqual(verdict, "unverifiable")

    def test_menu_opened_from_engine_dict(self):
        result = {"action": "opened", "path": ["View", "Toolbars"],
                  "items": [{}, {}]}
        verdict, _ = mc.verify_menu_consequence(
            "the Toolbars menu opened", BEFORE, AFTER_SAME, result)
        self.assertEqual(verdict, "met")
        # Intermediate segment counts: the walk expanded it.
        verdict, _ = mc.verify_menu_consequence(
            "the View menu opened", BEFORE, AFTER_SAME, result)
        self.assertEqual(verdict, "met")
        verdict, _ = mc.verify_menu_consequence(
            "the Status menu opened", BEFORE, AFTER_SAME, result)
        self.assertEqual(verdict, "not-met")

    def test_menu_opened_exact_name_not_substring(self):
        result = {"action": "opened", "path": ["Overview"]}
        verdict, _ = mc.verify_menu_consequence(
            "the View menu opened", BEFORE, AFTER_SAME, result)
        self.assertEqual(verdict, "not-met")

    def test_menu_opened_from_funnel_text(self):
        funnel = {"status": "success", "result": _opened("View → Toolbars")}
        verdict, _ = mc.verify_menu_consequence(
            "the Toolbars menu opened", BEFORE, AFTER_SAME, funnel)
        self.assertEqual(verdict, "met")
        funnel = {"status": "success",
                  "result": _invoked("File → Save As")}
        verdict, _ = mc.verify_menu_consequence(
            "the Toolbars menu opened", BEFORE, AFTER_SAME, funnel)
        self.assertEqual(verdict, "not-met")
        verdict, _ = mc.verify_menu_consequence(
            "the Toolbars menu opened", BEFORE, AFTER_SAME, None)
        self.assertEqual(verdict, "unverifiable")

    def test_dialog_opened_rewrites_to_generic(self):
        verdict, _ = mc.verify_menu_consequence(
            "the Save As dialog opened", BEFORE, AFTER_SAVEAS)
        self.assertEqual(verdict, "met")
        verdict, _ = mc.verify_menu_consequence(
            "the Save As dialog opened", BEFORE, AFTER_SAME)
        self.assertEqual(verdict, "not-met")
        verdict, _ = mc.verify_menu_consequence(
            "dialog Save As opened", BEFORE, AFTER_SAVEAS)
        self.assertEqual(verdict, "met")

    def test_backend_verified_flag_never_auto_mets(self):
        # A backend "verified" flag with no snapshot evidence for
        # the named consequence is not-met, never a borrowed met.
        result = {"action": "invoked", "path": ["File", "Save As"],
                  "verification": "verified"}
        verdict, _ = mc.verify_menu_consequence(
            "a dialog titled Save As appears", BEFORE, AFTER_SAME, result)
        self.assertEqual(verdict, "not-met")

    def test_unparseable_is_unverifiable_never_guessed(self):
        verdict, reason = mc.verify_menu_consequence(
            "the thing does stuff", BEFORE, AFTER_SAME)
        self.assertEqual(verdict, "unverifiable")
        self.assertIn("nothing", reason)


# ---------------------------------------------------------------------------
# Coordinator consumer: fake executors
# ---------------------------------------------------------------------------

class ConsumerTests(unittest.TestCase):

    def _run(self, steps, handler, snapshot=None, **coord_kw):
        executor = _fake_executor(handler)
        coord = tc.TurnCoordinator(executor=executor, snapshot=snapshot,
                                   **coord_kw)
        task = coord.new_task("menu goal", "routine", steps)
        receipt = coord.run_task(task)
        return receipt, executor

    def test_menu_step_met_completes_with_verdict(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": _invoked("File → Save As")}
        receipt, _ = self._run(
            [_menu_step("File → Save As",
                        "the Save As dialog opened")],
            handler, snapshot=_snapshots(BEFORE, AFTER_SAVEAS))
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(receipt.verification), 1)
        self.assertEqual(receipt.verification[0]["verdict"], "met")

    def test_menu_step_notmet_fails_honestly(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": _invoked("File → Save As")}
        receipt, _ = self._run(
            [_menu_step("File → Save As",
                        "the Save As dialog opened")],
            handler, snapshot=_snapshots(BEFORE, AFTER_SAME))
        self.assertEqual(receipt.state, "failed")
        self.assertIn("Save As", receipt.reason)
        self.assertEqual(receipt.verification[0]["verdict"], "not-met")

    def test_menu_step_unverifiable_completes_recorded(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": _invoked("File → Save As")}
        receipt, _ = self._run(
            [_menu_step("File → Save As", "the thing does stuff")],
            handler, snapshot=_snapshots(BEFORE, AFTER_SAME))
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.verification[0]["verdict"], "unverifiable")

    def test_menu_opened_consequence_via_raw_output(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": _opened("View → Toolbars")}
        receipt, _ = self._run(
            [_menu_step("View → Toolbars", "the Toolbars menu opened")],
            handler, snapshot=_snapshots(BEFORE, AFTER_SAME))
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.verification[0]["verdict"], "met")

    def test_menu_opened_consequence_fails_when_command_ran(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": _invoked("File → Save As")}
        receipt, _ = self._run(
            [_menu_step("File → Save As", "the Toolbars menu opened")],
            handler, snapshot=_snapshots(BEFORE, AFTER_SAME))
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.verification[0]["verdict"], "not-met")

    def test_foreground_change_consequence(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": _invoked("Window → Notepad")}
        receipt, _ = self._run(
            [_menu_step("Window → Notepad",
                        "the foreground is now Notepad")],
            handler, snapshot=_snapshots(BEFORE, AFTER_SWITCH))
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.verification[0]["verdict"], "met")

    def test_no_expect_no_phase_but_honest_message(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": _invoked("File → Save As")}
        receipt, _ = self._run([_menu_step("File → Save As")], handler)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.verification, [])
        self.assertIn("invoked-without-verification",
                      receipt.steps[0]["message"])

    def test_menu_step_routes_to_menu_verifier_not_generic(self):
        menu_calls = []
        generic_calls = []

        def menu_stub(expect, before, after, invoke_result):
            menu_calls.append((expect, invoke_result))
            return ("met", "stub met")

        def generic_stub(expect, before, after, result_text):
            generic_calls.append(expect)
            return ("met", "generic met")
        coord = tc.TurnCoordinator(
            executor=_fake_executor(
                lambda t, a: {"status": "success",
                              "result": _invoked("File → Save As")}),
            snapshot=_snapshots(BEFORE, AFTER_SAVEAS),
            verifier=generic_stub, menu_verifier=menu_stub)
        task = coord.new_task(
            "menu goal", "routine",
            [_menu_step("File → Save As",
                        "the Save As dialog opened")])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(menu_calls), 1)
        self.assertEqual(menu_calls[0][0], "the Save As dialog opened")
        self.assertIn("Save As", str(menu_calls[0][1]))
        # The step's expectation never reached the generic
        # verifier — but the B10 goal check legitimately calls
        # it exactly once with the task title (pinned in
        # test_goal_verification.py: the goal text is judged
        # through the EXISTING verifier, step phase or not).
        # So the generic verifier saw only the goal check,
        # never the step's expectation.
        self.assertEqual(generic_calls, ["menu goal"])
        self.assertNotIn("the Save As dialog opened", generic_calls)
        self.assertEqual(receipt.goal_verdict["verdict"], "met")

    def test_non_menu_step_routes_to_generic_verifier(self):
        menu_calls = []

        def menu_stub(expect, before, after, invoke_result):
            menu_calls.append(expect)
            return ("met", "stub met")
        coord = tc.TurnCoordinator(
            executor=_fake_executor(
                lambda t, a: {"status": "success", "result": "done"}),
            snapshot=_snapshots(BEFORE, AFTER_SAME),
            menu_verifier=menu_stub)
        step = tc.StepDef.from_mapping(
            {"tool": "file_control", "args": {},
             "expect": "a dialog titled Save As appears"})
        task = coord.new_task("plain goal", "routine", [step])
        receipt = coord.run_task(task)
        self.assertEqual(menu_calls, [])
        self.assertEqual(receipt.verification[0]["verdict"], "not-met")

    def test_handle_injection_and_explicit_wins(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": _invoked("File → Save As")}
        step = _menu_step("File → Save As", handle_id="win-1")
        receipt, executor = self._run([step], handler)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(executor.calls[0][1]["window_handle_id"], "win-1")
        step2 = tc.StepDef.from_mapping(
            {"tool": "menu_action",
             "args": {"action": "invoke", "path": "File → Save As",
                      "window_handle_id": "win-9"},
             "handle_id": "win-1"})
        _, executor2 = self._run([step2], handler)
        self.assertEqual(executor2.calls[0][1]["window_handle_id"], "win-9")

    def test_stale_handle_refusal_fails_honestly(self):
        def handler(tool, args):
            if args.get("window_handle_id") != "win-live":
                return {"status": "failure", "kind": "handle-refused",
                        "message": "handle win-stale refused: window "
                                   "replaced under the same title"}
            return {"status": "success",
                    "result": _invoked("File → Save As")}
        step = _menu_step("File → Save As", handle_id="win-stale")
        receipt, _ = self._run([step], handler)
        self.assertEqual(receipt.state, "failed")
        self.assertIn("handle", receipt.reason)


# ---------------------------------------------------------------------------
# Refusal rewrite: every refusal class, list, engine stop, skip
# ---------------------------------------------------------------------------

class RefusalRewriteTests(unittest.TestCase):

    def _failed_for(self, result_text, **step_kw):
        def handler(tool, args):
            return {"status": "success", "result": result_text}
        receipt, _ = ConsumerTests._run(
            self, [_menu_step("File → Delete", **step_kw)], handler)
        return receipt

    def test_every_invoke_refusal_class_fails(self):
        reasons = {
            "item-disabled": "'Delete' is disabled",
            "item-ambiguous": "more than one item is named 'Delete'",
            "item-not-found": "'Delete' is not in this menu level",
            "menu-rebuilt": "the menu was rebuilt since it was listed",
            "menu-unexposed": "that menu isn't exposed to automation",
            "not-a-submenu": "'File' has no submenu",
            "invoke-failed": "invoking 'Delete' failed",
            "snapshot-stale": "observation is stale",
            "handle-refused": "handle resolution refused",
        }
        for code, reason in reasons.items():
            with self.subTest(code=code):
                receipt = self._failed_for(_refused("File → Delete", reason))
                self.assertEqual(receipt.state, "failed")
                self.assertEqual(receipt.error_kind, "menu_refused")
                self.assertIn(reason, receipt.reason)

    def test_list_refusal_fails(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": "Menu list refused (menu-unexposed): that "
                              "menu isn't exposed to automation"}
        step = tc.StepDef.from_mapping(
            {"tool": "menu_action", "args": {"action": "list"}})
        receipt, _ = ConsumerTests._run(self, [step], handler)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "menu_refused")

    def test_engine_confirm_stop_pauses_not_silent(self):
        # INTENTIONAL (§7 consumers batch 6): renamed from
        # test_engine_confirm_stop_fails_not_silent — batch 6
        # built the re-issue-after-approval protocol, so the
        # C-14 stop now pauses with a pending-approval record
        # instead of failing. The test's intent is unchanged
        # and strengthened: a stop is never a silent success.
        receipt = self._failed_for(_refused(
            "File → Delete",
            "'Delete' is an effectful menu action — confirm it first "
            "(C-14 approve-first) before it is invoked."))
        self.assertEqual(receipt.state, "paused")
        self.assertIn("awaiting approval", receipt.reason)

    def test_success_lines_never_rewritten(self):
        for text in (_invoked("File → Save As"),
                     _opened("View → Toolbars"),
                     "Menu roots:\n- File (enabled, submenu)"):
            def handler(tool, args, text=text):
                return {"status": "success", "result": text}
            step = tc.StepDef.from_mapping(
                {"tool": "menu_action",
                 "args": {"action": "invoke", "path": "File → Save As"}}) \
                if text.startswith("Menu path") else \
                tc.StepDef.from_mapping(
                    {"tool": "menu_action", "args": {"action": "list"}})
            receipt, _ = ConsumerTests._run(self, [step], handler)
            self.assertEqual(receipt.state, "completed", text)

    def test_refused_step_with_skip_on_fail_skips(self):
        def handler(tool, args):
            return {"status": "success",
                    "result": _refused("File → Delete",
                                      "'Delete' is disabled")}
        step = _menu_step("File → Delete", on_fail="skip")
        receipt, _ = ConsumerTests._run(self, [step], handler)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.steps[0]["status"], "skipped")

    def test_confirm_stop_then_resolve(self):
        # INTENTIONAL (§7 consumers batch 6): the first run
        # used to assert failed under the batch-4 precedent,
        # and resolution required a fresh task carrying
        # confirmed=True. Batch 6 built the
        # re-issue-after-approval protocol, so the stop now
        # pauses the SAME task, and resolve_pending
        # (approved=True) — the GUI's authorization outcome —
        # re-issues it with confirmed set.
        state = {"approved": False}

        def handler(tool, args):
            if not args.get("confirmed"):
                return {"status": "success",
                        "result": _refused(
                            "File → Delete",
                            "'Delete' is an effectful menu action — "
                            "confirm it first (C-14 approve-first) "
                            "before it is invoked.")}
            state["approved"] = True
            return {"status": "success",
                    "result": _invoked("File → Delete", "verified")}
        executor = _fake_executor(handler)
        coord = tc.TurnCoordinator(executor=executor)
        task = coord.new_task("menu goal", "routine",
                              [_menu_step("File → Delete")])
        stopped = coord.run_task(task)
        self.assertEqual(stopped.state, "paused")
        self.assertFalse(state["approved"])
        pending = coord.pending_approval(task.id)
        self.assertEqual(pending["tool"], "menu_action")
        self.assertEqual(pending["confirm_arg"], "confirmed")
        resolved = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(resolved.state, "completed")
        self.assertTrue(state["approved"])


# ---------------------------------------------------------------------------
# Ledger round-trip of a menu step
# ---------------------------------------------------------------------------

class LedgerTests(unittest.TestCase):

    def test_menu_step_survives_ledger_rebuild(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            ledger = tl.TaskLedger(
                path=pathlib.Path(tmp.name) / "task_ledger.json")
            coord = tc.TurnCoordinator(
                executor=_fake_executor(
                    lambda t, a: {"status": "success", "result": "ok"}),
                ledger=ledger)
            step = _menu_step("File → Save As",
                              "the Save As dialog opened",
                              handle_id="win-7")
            task = coord.new_task("menu goal", "routine", [step])
            # Pause before any step runs: the run loop stops at
            # the pause flag, persisting a paused record with no
            # outcomes for the rebuild to resume from.
            coord.pause_task(task.id)
            coord.run_task(task)
            record = ledger.get(task.id)
            self.assertIsNotNone(record)
            rebuilt = tl.TaskLedger().rebuild_task(record)
            kept = rebuilt.steps[0]
            self.assertEqual(kept.tool, "menu_action")
            self.assertEqual(kept.args["path"], "File → Save As")
            self.assertEqual(kept.args["action"], "invoke")
            self.assertEqual(kept.handle_id, "win-7")
            self.assertEqual(kept.expect, "the Save As dialog opened")
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
    """Coordinator + real funnel: gate refusal, then rewrite."""

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

    def _run_menu(self, approve=False):
        from core import confirm_gate
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            coord = tc.TurnCoordinator(
                executor=self._tools.execute)
            task = coord.new_task(
                "menu goal", "routine",
                [_menu_step("File → Save As")])
            return coord.run_task(task)
        finally:
            confirm_gate.clear_handler()

    def test_unapproved_invoke_refused_at_funnel_gate(self):
        receipt = self._run_menu(approve=False)
        self.assertEqual(receipt.state, "failed")
        self.assertIn(receipt.error_kind, ("not_confirmed", "refused"))

    def test_approved_invoke_engine_refusal_rewritten(self):
        # The lab has no menu backend, so the approved call runs
        # the handler and the engine refuses honestly — arriving
        # as success-with-sentence, which the coordinator must
        # rewrite to a refused step, never a silent success.
        receipt = self._run_menu(approve=True)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "menu_refused")
        self.assertIn("refused", receipt.reason)


if __name__ == "__main__":
    unittest.main()

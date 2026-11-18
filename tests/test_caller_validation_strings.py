"""Caller-validation strings — coordinator refusal rewrite, completed.

§7 consumers batch 5 deliberately left handler caller-validation
strings out of _S7_REFUSAL_SHAPES, on the table comment's claim
that "they are not engine refusals, and the coordinator's own
step validation owns them". This batch tests that claim against
the real funnel + coordinator and finds it FALSE for every
validation string that reaches the coordinator: the funnel
wraps a plain handler string as {"status": "success"}, the
coordinator's step validation (StepDef.from_mapping) checks
ONLY menu_action action/path, and the funnel schemas declare
action as a plain string (no enum) — so a bad action value or
a missing arg recorded as a SUCCESSFUL step that did nothing.
That is the batch-4/5 defect in a new family, and this batch
rewrites it in the same table, with per-tool *_validation
kinds (distinct from *_refused, so receipts still distinguish
a caller error from an engine refusal).

This suite proves, per string:

- Every validation sentence that reaches the coordinator as
  success now fails honestly with its *_validation kind and
  the handler's sentence verbatim (fake-executor table, one
  entry per new shape; dialog's unknown-action shape is a
  tight regex, because the invalid action word itself escapes
  the existing enum regex).
- The same legs through the REAL funnel + REAL coordinator
  (batch-5 setup: DataGuard, taint cleared, session
  provenance "user", confirm_gate toggled) fail with the
  validation kind end to end — including the two omission
  cases the schema cannot catch (journal reconcile with
  happened omitted; drag norm1000 with the wrong array
  length).
- Every string NOT rewritten is already honest, with its
  evidence pinned: menu action/path (coordinator build-time
  ValueError — never a runtime result); every mistype/null
  string (funnel schema refuses with kind=validation before
  the handler runs — real-funnel legs); the dead
  fallthroughs ("Unknown surface" — route_surface ok's only
  the five known surfaces; journal's trailing fallthrough —
  the top enum check + four branches cover every action, so
  the reachable producer for a bogus action is the shaped
  line); the confirm stops (batch 6 — they pause, and no new
  shape matches any stop sentence).
- Success lines never rewrite under the new shapes — the
  batch-5 pinned lines re-asserted here, including the
  closest pairs ("Reconciled entry" vs "Reconciling needs",
  "Compensation complete" vs "Compensating needs",
  "Dragged text" vs "Dragging needs", "Placed rect on" vs
  "Placing a window rect needs").
- Refusal semantics unchanged: the skip rule still converts
  a refused validation step to skipped; non-§7 steps are
  untouched even when their result text looks like a
  validation sentence; gate refusals and non-success
  statuses pass through.

Behaviour-rule note (stated so the parent can overrule):
the brief's "same outcome, same state, same receipt shape"
cannot be read literally for this batch — the recorded
success IS the defect, and batch 4/5's own precedent changed
recorded outcomes (success → refused). Adopted reading:
"outcome" = real-world effect (the validation failure
already meant nothing ran, and nothing new runs), "receipt
field" = field SET/shape (status/kind/message, unchanged).
The fix changes the recorded status VALUE from false-success
to refused, exactly as batch 4/5 did.

Stdlib unittest only. Fake executors mimic funnel shapes
(status/result strings), exactly as production delivers them.
"""

import pathlib
import re
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
# New validation shapes → refused failures with *_validation kinds
# ---------------------------------------------------------------------------

# (tool, args, validation sentence, expected kind)
# Sentences are the handlers' own renderings, read off the code
# and confirmed through the real funnel in the investigation.
VALIDATIONS = [
    # Dialog: the invalid action word escapes the enum regex.
    ("file_dialog_action",
     {"action": "bogus"},
     "File dialog bogus refused: unknown dialog action 'bogus' "
     "(open, save, folder). Re-observe (take_screenshot or "
     "screen_look) and pick a fresh target.",
     "dialog_refused"),
    # Surfaces: every "Unknown X action" / "X needs a Y" leg.
    ("system_surface_action",
     {"surface": "tray", "action": "open_menu"},
     "Opening a tray menu needs a tray icon name.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "tray", "action": "invoke_path", "path": "Open"},
     "Invoking a tray menu path needs an icon name.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "tray", "action": "invoke_path", "target": "Volume"},
     "Invoking a tray menu path needs a path.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "tray", "action": "bogus"},
     "Unknown tray action 'bogus' — use 'open_menu', "
     "'invoke_path', or 'list'.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "taskbar", "action": "switch"},
     "Switching needs a taskbar button name.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "taskbar", "action": "launch"},
     "Launching needs a taskbar button name.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "taskbar", "action": "pin"},
     "Pinning needs a taskbar button name.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "taskbar", "action": "bogus"},
     "Unknown taskbar action 'bogus' — use 'switch', 'launch', "
     "'pin', or 'list'.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "start", "action": "search"},
     "Searching Start needs search text.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "start", "action": "launch"},
     "Launching from Start needs a result name.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "start", "action": "bogus"},
     "Unknown Start action 'bogus' — use 'search', 'launch', "
     "or 'list'.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "notification", "action": "act"},
     "Acting on a notification needs its id — never a position.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "notification", "action": "bogus"},
     "Unknown notification action 'bogus' — use 'act', "
     "'dismiss', or 'list'.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "settings", "action": "open"},
     "Opening a settings page needs a page name.",
     "surface_validation"),
    ("system_surface_action",
     {"surface": "settings", "action": "bogus"},
     "Unknown settings action 'bogus' — use 'open', or 'list'.",
     "surface_validation"),
    # Adapter: the empty-phrase leg.
    ("adapter_action",
     {"phrase": ""},
     "Resolving an adapter phrase needs a phrase to resolve.",
     "adapter_validation"),
    # Drag: missing handles, and the wrong-length norm1000
    # array (the schema's array-of-numbers type lets it
    # through; the handler unpacks and fails). The schema
    # REQUIRES source_handle_id, target_handle_id, and
    # payload, so the handler legs are reached with empty
    # strings — an omitted id is schema-refused first (see
    # the schema legs below), which is itself honest.
    ("drag_drop_action",
     {"source_handle_id": "", "target_handle_id": "h-2",
      "payload": {"kind": "text", "text": "hi"}},
     "Dragging needs a source handle id.",
     "drag_validation"),
    ("drag_drop_action",
     {"source_handle_id": "h-1", "target_handle_id": "",
      "payload": {"kind": "text", "text": "hi"}},
     "Dragging needs a target handle id.",
     "drag_validation"),
    ("drag_drop_action",
     {"source_handle_id": "h-1", "target_handle_id": "h-2",
      "payload": {"kind": "text", "text": "hi"},
      "target_norm1000": [0.5, 0.5, 0.5]},
     "A monitor-targeted drag needs a norm1000 (x, y) target "
     "point to convert.",
     "drag_validation"),
    # Monitor: every "Unknown X action" / "X needs a Y" leg.
    ("monitor_action",
     {"action": "bogus"},
     "Unknown monitor action 'bogus' — use 'list', 'resolve', "
     "'convert', or 'place'.",
     "monitor_validation"),
    ("monitor_action",
     {"action": "resolve"},
     "Resolving a monitor needs a reference.",
     "monitor_validation"),
    ("monitor_action",
     {"action": "resolve", "reference": "1", "nx": None, "ny": 0.5},
     "Resolving a monitor point needs norm1000 numbers (nx, ny).",
     "monitor_validation"),
    ("monitor_action",
     {"action": "convert", "reference": "1", "direction": "bogus"},
     "Converting needs a direction: logical_to_physical or "
     "physical_to_logical.",
     "monitor_validation"),
    ("monitor_action",
     {"action": "convert", "direction": "logical_to_physical"},
     "Converting needs a monitor reference.",
     "monitor_validation"),
    ("monitor_action",
     {"action": "place", "from_reference": "1",
      "to_reference": "2"},
     "Placing a window rect needs a rect (left, top, right, "
     "bottom).",
     "monitor_validation"),
    ("monitor_action",
     {"action": "place", "rect": [0, 0, 100],
      "from_reference": "1", "to_reference": "2"},
     "Placing a window rect needs 4 numbers (left, top, right, "
     "bottom).",
     "monitor_validation"),
    ("monitor_action",
     {"action": "place", "rect": [0, 0, 100, 100],
      "to_reference": "2"},
     "Placing a window rect needs a source monitor.",
     "monitor_validation"),
    ("monitor_action",
     {"action": "place", "rect": [0, 0, 100, 100],
      "from_reference": "1"},
     "Placing a window rect needs a target monitor.",
     "monitor_validation"),
    # Journal: the enum leg and every "X needs a Y" leg,
    # including the happened-omitted case (happened defaults
    # to None, so omission passes the boolean schema).
    ("journal_action",
     {"action": "bogus"},
     "Unknown journal action 'bogus' — use 'list', 'reconcile', "
     "'compensate', or 'plan'.",
     "journal_validation"),
    ("journal_action",
     {"action": "list", "limit": 0},
     "Listing journal entries needs a limit of at least 1.",
     "journal_validation"),
    ("journal_action",
     {"action": "reconcile", "confirmed": True},
     "Reconciling needs a journal entry id.",
     "journal_validation"),
    ("journal_action",
     {"action": "reconcile", "receipt_id": "x", "confirmed": True},
     "Reconciling needs happened true or false — what the user "
     "confirmed actually happened (or did not). The tool cannot "
     "guess.",
     "journal_validation"),
    ("journal_action",
     {"action": "reconcile", "receipt_id": "x", "happened": True,
      "confirmed": True, "confirmed_by": ""},
     "Reconciling needs a confirmer name — reconcile is a "
     "user-confirmed action, never automatic.",
     "journal_validation"),
    ("journal_action",
     {"action": "compensate", "confirmed": True},
     "Compensating needs a journal entry id.",
     "journal_validation"),
    ("journal_action",
     {"action": "compensate", "receipt_id": "x",
      "confirmed": True, "confirmed_by": ""},
     "Compensating needs a confirmer name — effectful "
     "compensation asks first, never auto-runs.",
     "journal_validation"),
    # Context menu: the enum leg and the missing-path leg.
    ("context_menu_action",
     {"action": "bogus"},
     "Unknown context menu action 'bogus' — use 'trigger', "
     "'list', or 'invoke'.",
     "context_menu_validation"),
    ("context_menu_action",
     {"action": "invoke"},
     "Invoking a context menu path needs a path, for example "
     "'Delete'.",
     "context_menu_validation"),
]


class ValidationRewriteTests(unittest.TestCase):

    def test_every_validation_shape_fails_with_its_kind(self):
        for tool, args, sentence, kind in VALIDATIONS:
            with self.subTest(tool=tool, kind=kind):
                receipt = _failed_for(tool, args, sentence)
                self.assertEqual(receipt.state, "failed", sentence)
                self.assertEqual(receipt.error_kind, kind, sentence)
                self.assertIn(sentence, receipt.reason, sentence)

    def test_refused_validation_step_with_skip_on_fail_skips(self):
        receipt = _failed_for(
            "monitor_action", {"action": "bogus"},
            "Unknown monitor action 'bogus' — use 'list', "
            "'resolve', 'convert', or 'place'.",
            on_fail="skip")
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.steps[0]["status"], "skipped")

    def test_gate_refusal_untouched(self):
        def handler(tool, args):
            return {"status": "refused", "kind": "not_confirmed",
                    "message": "approval required"}
        receipt, _ = _run(
            [_step("monitor_action", {"action": "bogus"})], handler)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "not_confirmed")

    def test_non_s7_step_untouched_even_with_validation_text(self):
        # Per-tool scoping: a non-§7 step whose result text
        # looks like a monitor validation sentence must NOT
        # be rewritten.
        def handler(tool, args):
            return {"status": "success",
                    "result": "Unknown monitor action 'bogus' — "
                    "use 'list', 'resolve', 'convert', or 'place'."}
        receipt, _ = _run(
            [_step("cursor_control", {"action": "move"})], handler)
        self.assertEqual(receipt.state, "completed")

    def test_non_success_status_untouched(self):
        def handler(tool, args):
            return {"status": "failure", "kind": "backend-down",
                    "message": "the backend is unavailable"}
        receipt, _ = _run(
            [_step("monitor_action", {"action": "bogus"})], handler)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "backend-down")

    def test_menu_rewrite_intact_through_new_dispatcher(self):
        # _rewrite_menu_refusal untouched: the batch-4 shape
        # still rewrites, and menu validation strings still
        # never reach it (build blocks them — see below).
        receipt = _failed_for(
            "menu_action", {"action": "invoke", "path": "File → Delete"},
            "Menu path File → Delete refused: 'Delete' is disabled")
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "menu_refused")


# ---------------------------------------------------------------------------
# Already honest, NOT rewritten — with evidence
# ---------------------------------------------------------------------------

# The NEW validation patterns only (prefixes, plus the dialog
# unknown-action regex), against which the confirm stops are
# checked — the stops must match NONE of them.
_NEW_VALIDATION_PATTERNS = [
    "Opening a tray menu needs a tray icon name.",
    "Invoking a tray menu path needs an icon name.",
    "Invoking a tray menu path needs a path.",
    "Unknown tray action '",
    "Switching needs a taskbar button name.",
    "Launching needs a taskbar button name.",
    "Pinning needs a taskbar button name.",
    "Unknown taskbar action '",
    "Searching Start needs search text.",
    "Launching from Start needs a result name.",
    "Unknown Start action '",
    "Acting on a notification needs its id",
    "Unknown notification action '",
    "Opening a settings page needs a page name.",
    "Unknown settings action '",
    "Resolving an adapter phrase needs a phrase to resolve.",
    "Dragging needs a source handle id.",
    "Dragging needs a target handle id.",
    "A monitor-targeted drag needs a norm1000 (x, y) "
    "target point to convert.",
    "Unknown monitor action '",
    "Resolving a monitor needs a reference.",
    "Resolving a monitor point needs norm1000 numbers (nx, ny).",
    "Converting needs a direction: ",
    "Converting needs a monitor reference.",
    "Placing a window rect needs a rect (left, top, right, bottom).",
    "Placing a window rect needs 4 numbers (left, top, right, bottom).",
    "Placing a window rect needs a source monitor.",
    "Placing a window rect needs a target monitor.",
    "Unknown journal action '",
    "Listing journal entries needs a limit of at least 1.",
    "Reconciling needs a journal entry id.",
    "Reconciling needs happened true or false — ",
    "Reconciling needs a confirmer name — ",
    "Compensating needs a journal entry id.",
    "Compensating needs a confirmer name — ",
    "Unknown context menu action '",
    "Invoking a context menu path needs a path, ",
    re.compile(r"^File dialog .* refused: unknown dialog action "),
]

# Confirm stops (batch 6): these PAUSE, and must not be
# caught by any new validation shape. The journal stops are
# the closest pair ("Reconciliation needs confirmation" vs
# "Reconciling needs"; "Compensation needs confirmation"
# vs "Compensating needs").
CONFIRM_STOPS = [
    "File dialog save stops for confirmation: saving would "
    "overwrite 'a.txt' — confirm first.",
    "Adapter resolution refused (confirm-required): 'close tab' "
    "is a confirm-gated phrase. Confirm it explicitly, then "
    "resolve again.",
    "Drag refused (destructive-needs-confirmation): the target "
    "is a trash target. Confirm it explicitly, then drag again "
    "with confirm_destructive set.",
    "Reconciliation needs confirmation: reconcile is a "
    "user-confirmed action, so this call stops with "
    "needs_confirmation and writes nothing — confirm it "
    "explicitly, then reconcile again with confirmed set.",
    "Compensation needs confirmation: running declared "
    "compensations is effectful, so this call stops with "
    "needs_confirmation and runs nothing — confirm it "
    "explicitly, then compensate again with confirmed set.",
]


def _pattern_matches(pattern, text):
    if isinstance(pattern, re.Pattern):
        return pattern.search(text) is not None
    return text.startswith(pattern)


class AlreadyHonestTests(unittest.TestCase):

    def test_menu_validation_blocked_at_build(self):
        # Menu action/path strings never become runtime
        # results: StepDef.from_mapping rejects them first.
        with self.assertRaises(ValueError):
            _step("menu_action", {"action": "bogus"})
        with self.assertRaises(ValueError):
            _step("menu_action", {"action": "invoke", "path": ""})
        with self.assertRaises(ValueError):
            _step("menu_action", {"action": "invoke"})

    def test_confirm_stops_match_no_new_validation_shape(self):
        for stop in CONFIRM_STOPS:
            for pattern in _NEW_VALIDATION_PATTERNS:
                with self.subTest(stop=stop[:40], pattern=str(pattern)[:40]):
                    self.assertFalse(
                        _pattern_matches(pattern, stop),
                        f"new shape {pattern!r} would catch stop {stop!r}")

    def test_dead_fallthroughs_unreachable(self):
        # "Unknown surface": route_surface ok's only the five
        # known surfaces, so the handler's fallthrough never
        # produces it for a real call.
        from core.desktop import system_surfaces as ss
        self.assertFalse(ss.route_surface("bogus")["ok"])
        for surface in ("tray", "taskbar", "start",
                        "notification", "settings"):
            self.assertTrue(ss.route_surface(surface)["ok"], surface)
        # Journal's trailing fallthrough: the top enum check
        # + four branches cover every action, so the reachable
        # producer for a bogus action is the shaped line —
        # proven end to end in the real-funnel legs below
        # (the reason carries "— use 'list', ").


# ---------------------------------------------------------------------------
# Success lines never rewritten under the new shapes
# ---------------------------------------------------------------------------

# The batch-5 pinned lines, re-asserted: the new shapes must
# not catch any of them, including the closest pairs.
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
                self.assertEqual(receipt.steps[0]["status"], "success", text)


# ---------------------------------------------------------------------------
# Real funnel + real coordinator (batch-5 setup)
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
    """Through the real registry + real coordinator, with the
    batch-5 setup: taint cleared, session provenance "user",
    confirm_gate toggled so effectful tools run."""

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

    def _run_real(self, tool, args):
        from core import confirm_gate
        confirm_gate.clear_handler()
        confirm_gate.register_handler(lambda n, s: True)
        try:
            coord = tc.TurnCoordinator(
                executor=self._tools.execute)
            task = coord.new_task("s7 goal", "routine",
                                  [_step(tool, args)])
            return coord.run_task(task)
        finally:
            confirm_gate.clear_handler()

    def test_real_funnel_validation_legs_fail_with_validation_kind(self):
        # (tool, args, expected kind) — representative legs
        # covering every tool with a new shape, including the
        # two omission cases the schema cannot catch.
        legs = [
            ("file_dialog_action", {"action": "bogus"},
             "dialog_refused"),
            ("system_surface_action",
             {"surface": "tray", "action": "bogus"},
             "surface_validation"),
            ("system_surface_action",
             {"surface": "tray", "action": "open_menu"},
             "surface_validation"),
            ("system_surface_action",
             {"surface": "tray", "action": "invoke_path", "path": "Open"},
             "surface_validation"),
            ("adapter_action", {"phrase": ""}, "adapter_validation"),
            ("drag_drop_action",
             {"source_handle_id": "", "target_handle_id": "h-2",
              "payload": {"kind": "text", "text": "hi"}},
             "drag_validation"),
            ("drag_drop_action",
             {"source_handle_id": "h-1", "target_handle_id": "",
              "payload": {"kind": "text", "text": "hi"}},
             "drag_validation"),
            ("drag_drop_action",
             {"source_handle_id": "h-1", "target_handle_id": "h-2",
              "payload": {"kind": "text", "text": "hi"},
              "target_norm1000": [0.5, 0.5, 0.5]},
             "drag_validation"),
            ("monitor_action", {"action": "bogus"},
             "monitor_validation"),
            ("monitor_action",
             {"action": "place", "from_reference": "1",
              "to_reference": "2"},
             "monitor_validation"),
            ("journal_action", {"action": "bogus"},
             "journal_validation"),
            ("journal_action", {"action": "list", "limit": 0},
             "journal_validation"),
            ("journal_action",
             {"action": "reconcile", "confirmed": True},
             "journal_validation"),
            ("journal_action",
             {"action": "reconcile", "receipt_id": "x",
              "confirmed": True},
             "journal_validation"),
            ("context_menu_action", {"action": "bogus"},
             "context_menu_validation"),
            ("context_menu_action", {"action": "invoke"},
             "context_menu_validation"),
        ]
        for tool, args, kind in legs:
            with self.subTest(tool=tool, args=args):
                receipt = self._run_real(tool, args)
                self.assertEqual(receipt.state, "failed", tool)
                self.assertEqual(receipt.error_kind, kind, tool)

    def test_real_funnel_journal_bogus_uses_shaped_line(self):
        # The reachable producer for a bogus journal action is
        # the shaped line (top enum check), NOT the trailing
        # fallthrough — the reason carries the use-list suffix.
        receipt = self._run_real("journal_action", {"action": "bogus"})
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.error_kind, "journal_validation")
        self.assertIn("— use 'list', ", receipt.reason)

    def test_real_funnel_schema_refusals_are_already_honest(self):
        # Mistype/null cases never reach a handler: the funnel
        # schema refuses with kind=validation first. No
        # approval needed — the refusal precedes any gate ask.
        cases = [
            ("menu_action",
             {"action": "list", "snapshot": "not-a-dict"}),
            ("file_dialog_action", {"action": 42}),
            ("system_surface_action", {"surface": 42}),
            ("adapter_action", {"foreground": "not-a-dict"}),
            ("drag_drop_action", {"payload": "not-a-dict"}),
            ("drag_drop_action", {"target_handle_id": "h-2"}),
            ("drag_drop_action",
             {"source_handle_id": "h-1", "target_handle_id": "h-2",
              "source_observation": "not-a-dict"}),
            ("monitor_action", {"action": 42}),
            ("journal_action", {"action": "list", "limit": "bogus"}),
            ("context_menu_action", {"action": 42}),
        ]
        for tool, args in cases:
            with self.subTest(tool=tool):
                result = self._tools.execute(tool, args)
                self.assertEqual(result["status"], "refused", tool)
                self.assertEqual(result["kind"], "validation", tool)


if __name__ == "__main__":
    unittest.main()

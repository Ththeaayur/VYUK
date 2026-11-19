"""§7 consumers batch 3 — funnel auto-threading + coordinator step handles.

Within one coordinator task, the funnel remembers the most recent
handle id + snapshot stamp a §7 tool call produced, and threads
them into subsequent calls' receipt records. This suite proves:

- Threading is task-scoped and inert without a task (no leak
  across tasks, no threading for direct funnel calls that never
  began a task).
- A production threads into the NEXT call's receipt whole
  (handle + its own stamp, never mixed with the new call's
  snapshot stamp), and an explicit handle always wins.
- Staleness follows the item-2 rule: a stored snapshot judged
  stale against the consuming call's own snapshot (foreground
  hwnd/bounds — never stamp equality alone) is dropped and
  cleared, never threaded; a failed/denied snapshot never
  threads at all.
- Threading never waves anything through confirmation (gated
  paths still gate with threading active, zero invokes).
- Coordinator steps carry a handle id into the tool call under
  the tool's handle arg (drag gets no injection — two handle
  args, one id cannot name one), explicit step args win, the
  declared handle survives a ledger round-trip, run_task
  threads across its steps and clears on every exit (including
  failure), and journal lines stay append-only under threading.
- Registry: 655 tools after batch 8 (batch 3 itself added no
  funnel tool). Stale-pin sweep: INTENTIONAL — 653 -> 654: wallpaper_apply; wallpaper-revert batch (2026-11-05): INTENTIONAL — 654 -> 655: wallpaper_revert.

Technique (no product seam): the funnel's receipt store is
pointed at a tmp file per test (the module's _default_store is
swapped and restored), data/ is guarded and restored, menu /
dialog / drag engines run over the batch-1/2 fake backends,
and snapshots are built through the real perception adapter
over a fake perception backend.
"""

import json
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import confirm_gate
from core.desktop import drag_drop as dd  # INTENTIONAL (Phase 3 S7a): drag_drop moved to core/desktop/
from core.desktop import file_dialogs as fd  # INTENTIONAL (Phase 3 S7a): file_dialogs moved to core/desktop/
from core import injection
from core.desktop import menu_paths as mp  # INTENTIONAL (Phase 3 S7a): menu_paths moved to core/desktop/
from core.desktop import perception_adapter as pa  # INTENTIONAL (Phase 3 S7a): perception_adapter moved to core/desktop/
from core.desktop import s7_threading as s7t  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/
from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain import task_ledger as tl  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core import tool_receipts as tr_mod
from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.config import config as app_config

TOOLS = tools

FG = {"hwnd": 111, "pid": 42, "title": "Notes", "proc": "notes.exe"}
FG_OTHER = {"hwnd": 222, "pid": 43, "title": "Other",
            "proc": "other.exe"}
BOUNDS = (0, 0, 800, 600)


def _mnode(name, aid, children=None, enabled=True, checked=None,
           rect=(0, 0, 100, 20)):
    return {"name": name, "control_type": "MenuItem",
            "automation_id": aid, "enabled": enabled,
            "checked": checked, "children": list(children or []),
            "rect": rect}


def _mtree():
    return [
        _mnode("File", "file", [
            _mnode("New", "new"),
            _mnode("Open", "open"),
            _mnode("Save", "save"),
            _mnode("Save As...", "saveas"),
            _mnode("Delete", "delete"),
            _mnode("Exit", "exit"),
        ]),
        _mnode("Edit", "edit", [
            _mnode("Undo", "undo"),
            _mnode("Redo", "redo", enabled=False),
        ]),
        _mnode("Tools", "tools", [_mnode("Options", "options")],
               enabled=False),
        _mnode("View", "view", [
            _mnode("Zoom", "zoom1"),
            _mnode("Zoom", "zoom2"),
        ]),
        _mnode("Help", "help", [_mnode("About", "about")]),
    ]


class FakePerceptionBackend:
    def __init__(self, fg=None):
        self.fg = dict(fg) if fg is not None else dict(FG)

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def capture_block_reason(self):
        return None

    def capture(self):
        return {"fake_image": True}

    def marks(self, capture, fg, ocr_words):
        return [], ""

    def controls(self, capture, fg):
        return [], ""

    def ocr_words(self, capture):
        return [], ""

    def pixels(self, capture):
        return {}, ""


class FakeMenuBackend:
    def __init__(self, tree=None, fg=None):
        self.tree = tree if tree is not None else _mtree()
        self.fg = dict(fg) if fg is not None else dict(FG)
        self.stamp = 1
        self.invoked = []
        self.invoke_result = (True, False, "")

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def menu_tree(self):
        return (list(self.tree), self.stamp, "")

    def expand(self, node):
        return (list(node.get("children") or []), self.stamp, "")

    def invoke(self, node):
        self.invoked.append(node.get("name"))
        return self.invoke_result


class FakeDialogBackend:
    def __init__(self, kind="open", folder="/docs", items=None):
        self.kind = kind
        self.current_folder = folder
        self.items = list(items) if items is not None else []
        self.exposed = True
        self.password = False
        self.fg = dict(FG)
        self.closed = False
        self.invoke_calls = []
        self.invoke_result = (True, True, None, "")
        self.app_reported = None

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def dialog_state(self):
        if self.closed:
            return None
        return {"kind": self.kind, "title": "Open",
                "current_folder": self.current_folder,
                "items": list(self.items),
                "filename_field": True, "exposed": self.exposed,
                "has_password_field": self.password,
                "file_type_filter": None, "note": ""}

    def set_folder(self, folder):
        self.current_folder = folder
        return True, ""

    def set_filename(self, name):
        return True, ""

    def set_filter(self, file_type):
        return True, ""

    def invoke(self, button):
        self.invoke_calls.append(button)
        invoked, closed_flag, reported, note = self.invoke_result
        if closed_flag:
            self.closed = True
        if reported is None:
            reported = self.app_reported
        return invoked, closed_flag, reported, note


class FakeDragBackend:
    """Minimal drag protocol, copied from the batch-2 suite."""

    ole_available = True

    def __init__(self):
        self.do_calls = 0

    def advertised_formats(self, target):
        return ["CF_HDROP", "CF_UNICODETEXT",
                "UniformResourceLocator"]

    def current_target_profile(self, target):
        return None

    def build_data_object(self, payload, format_name):
        return {"payload": payload, "format": format_name}

    def do_drag_drop(self, data_object, source, target, point):
        self.do_calls += 1
        return {"effect": "copy"}

    def verify_drop(self, target, payload):
        return "file present"

    def gesture_proven(self, target):
        return False


def _snapshot(fg=None):
    return pa.observe(backend=FakePerceptionBackend(fg))


class _DataGuard:
    def __init__(self):
        self._saved = {}

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


class ThreadingBase(unittest.TestCase):
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
        th.reset_store()
        self._orig_mp_backend = mp.default_backend
        self.menu_backend = FakeMenuBackend()
        mp.default_backend = lambda: self.menu_backend
        self._orig_fd_backend = fd.default_backend
        self.dialog_backend = FakeDialogBackend(
            kind="open", items=["report.txt", "notes.txt"])
        fd.default_backend = lambda: self.dialog_backend
        self._tmp = tempfile.TemporaryDirectory()
        self._receipt_path = pathlib.Path(self._tmp.name) \
            / "tool_receipts.jsonl"
        self._old_store = tr_mod._default_store
        self._old_disabled = tr_mod._store_disabled
        tr_mod._store_disabled = False
        tr_mod._default_store = tr_mod.ReceiptStore(
            path=self._receipt_path)
        s7t.end_task()

    def tearDown(self):
        s7t.end_task()
        tr_mod._default_store = self._old_store
        tr_mod._store_disabled = self._old_disabled
        self._tmp.cleanup()
        fd.default_backend = self._orig_fd_backend
        mp.default_backend = self._orig_mp_backend
        th.reset_store()
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()

    def call(self, name, args, approve=False):
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()

    def attempts(self, tool=None):
        if not self._receipt_path.exists():
            return []
        out = []
        for line in self._receipt_path.read_text(
                encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("type") != "attempt":
                continue
            if tool is not None and rec.get("tool") != tool:
                continue
            out.append(rec)
        return out

    def last_attempt(self, tool):
        found = self.attempts(tool)
        self.assertTrue(found, "no %s attempt recorded" % tool)
        return found[-1]


# ----------------------------------------------------------------------
# Threading module: scope, pair semantics, staleness
# ----------------------------------------------------------------------

class TestThreadingModule(ThreadingBase):
    def test_inert_without_task(self):
        s7t.note_produced("h-nope", stamp="s-nope")
        self.assertEqual(s7t.threaded(), (None, None))
        self.assertIsNone(s7t.current_task())
        self.assertEqual(s7t.resolve_for_call(), (None, None))

    def test_begin_clears_previous_task(self):
        s7t.begin_task("task-a")
        s7t.note_produced("h-a", stamp="s-a")
        self.assertEqual(s7t.threaded(), ("h-a", "s-a"))
        s7t.begin_task("task-b")
        self.assertEqual(s7t.threaded(), (None, None))
        self.assertEqual(s7t.current_task(), "task-b")

    def test_end_clears_everything(self):
        s7t.begin_task("task-a")
        s7t.note_produced("h-a", stamp="s-a")
        s7t.end_task()
        self.assertEqual(s7t.threaded(), (None, None))
        self.assertIsNone(s7t.current_task())

    def test_same_handle_without_snapshot_keeps_stamp(self):
        s7t.begin_task("task-a")
        snap = _snapshot()
        s7t.note_produced("h-a", snapshot=snap)
        self.assertEqual(s7t.threaded(), ("h-a", snap["stamp"]))
        s7t.note_produced("h-a")
        self.assertEqual(s7t.threaded(), ("h-a", snap["stamp"]))

    def test_new_handle_replaces_pair(self):
        s7t.begin_task("task-a")
        snap = _snapshot()
        s7t.note_produced("h-a", snapshot=snap)
        s7t.note_produced("h-b")
        self.assertEqual(s7t.threaded(), ("h-b", None))

    def test_failed_and_denied_snapshots_never_thread(self):
        s7t.begin_task("task-a")
        for status in ("failed", "denied"):
            bad = {"stamp": "s-bad-%s" % status,
                   "foreground_identity": dict(FG),
                   "bounds": BOUNDS, "capture_status": status}
            s7t.note_produced("h-bad", snapshot=bad)
            self.assertEqual(s7t.resolve_for_call(), (None, None),
                             status)

    def test_explicit_handle_wins_whole(self):
        s7t.begin_task("task-a")
        snap = _snapshot()
        s7t.note_produced("h-a", snapshot=snap)
        self.assertEqual(
            s7t.resolve_for_call(explicit_handle="h-x",
                                 explicit_stamp="s-x"),
            ("h-x", "s-x"))

    def test_explicit_stamp_alone_without_handle(self):
        s7t.begin_task("task-a")
        self.assertEqual(
            s7t.resolve_for_call(explicit_stamp="s-only"),
            (None, "s-only"))

    def test_stale_pair_dropped_and_cleared_by_oracle(self):
        s7t.begin_task("task-a")
        snap1 = _snapshot()
        s7t.note_produced("h-a", snapshot=snap1)
        snap_other = _snapshot(fg=FG_OTHER)
        got = s7t.resolve_for_call(current_snapshot=snap_other)
        self.assertEqual(got, (None, None))
        self.assertEqual(s7t.threaded(), (None, None))

    def test_same_window_new_stamp_is_fresh(self):
        # Staleness is fg + bounds, never stamp equality: a second
        # observation of the unchanged screen keeps the pair even
        # though its stamp differs (counter-minted).
        s7t.begin_task("task-a")
        snap1 = _snapshot()
        s7t.note_produced("h-a", snapshot=snap1)
        snap2 = _snapshot()
        self.assertNotEqual(snap1["stamp"], snap2["stamp"])
        self.assertEqual(s7t.resolve_for_call(current_snapshot=snap2),
                         ("h-a", snap1["stamp"]))

    def test_step_handle_arg_map(self):
        self.assertEqual(s7t.step_handle_arg("menu_action"),
                         "window_handle_id")
        self.assertEqual(s7t.step_handle_arg("file_dialog_action"),
                         "window_handle_id")
        self.assertEqual(s7t.step_handle_arg("system_surface_action"),
                         "target_handle_id")
        self.assertIsNone(s7t.step_handle_arg("drag_drop_action"))
        self.assertIsNone(s7t.step_handle_arg("get_current_time_date"))


# ----------------------------------------------------------------------
# Funnel threading: receipts carry the pair, gates untouched
# ----------------------------------------------------------------------

class TestFunnelThreading(ThreadingBase):
    def _list(self, snap):
        listed = self.call("menu_action",
                           {"action": "list", "snapshot": snap})
        self.assertEqual(listed["status"], "success", listed)
        return listed

    def _handle_from_list(self, listed):
        text = listed.get("result") or listed.get("message") or ""
        match = re.search(r"window handle (\S+)", text)
        self.assertIsNotNone(match, listed)
        return match.group(1)

    def test_list_then_invoke_receipt_carries_threaded_pair(self):
        s7t.begin_task("task-a")
        snap1 = _snapshot()
        listed = self._list(snap1)
        handle = self._handle_from_list(listed)
        snap2 = _snapshot()
        invoked = self.call("menu_action",
                            {"action": "invoke", "path": "File → Save",
                             "snapshot": snap2, "confirmed": True},
                            approve=True)
        self.assertEqual(invoked["status"], "success", invoked)
        self.assertEqual(self.menu_backend.invoked, ["Save"])
        attempts = self.attempts("menu_action")
        self.assertEqual(len(attempts), 2)
        # First call: no threaded pair yet; its own stamp alone.
        self.assertIsNone(attempts[0]["handle_id"])
        self.assertEqual(attempts[0]["snapshot_stamp"], snap1["stamp"])
        # Second call: threaded whole — handle + ITS stamp, never
        # mixed with the second call's own snapshot stamp.
        self.assertEqual(attempts[1]["handle_id"], handle)
        self.assertEqual(attempts[1]["snapshot_stamp"], snap1["stamp"])

    def test_explicit_handle_overrides_threaded(self):
        s7t.begin_task("task-a")
        snap1 = _snapshot()
        self._list(snap1)
        snap2 = _snapshot()
        other = th.mint_window(pa.snapshot_window_identity(snap2))
        invoked = self.call("menu_action",
                            {"action": "invoke", "path": "File → Save",
                             "snapshot": snap2,
                             "window_handle_id": other["id"],
                             "confirmed": True},
                            approve=True)
        self.assertEqual(invoked["status"], "success", invoked)
        attempt = self.last_attempt("menu_action")
        self.assertEqual(attempt["handle_id"], other["id"])
        self.assertEqual(attempt["snapshot_stamp"], snap2["stamp"])

    def test_stale_pair_dropped_by_consuming_snapshot(self):
        s7t.begin_task("task-a")
        snap1 = _snapshot()
        self._list(snap1)
        snap_other = _snapshot(fg=FG_OTHER)
        invoked = self.call("menu_action",
                            {"action": "invoke", "path": "File → Save",
                             "snapshot": snap_other, "confirmed": True},
                            approve=True)
        # The invoke itself refuses (window changed) — but the
        # receipt proves the point: no threaded handle, and the
        # call's own snapshot stamp alone.
        attempt = self.last_attempt("menu_action")
        self.assertIsNone(attempt["handle_id"])
        self.assertEqual(attempt["snapshot_stamp"],
                         snap_other["stamp"])
        self.assertEqual(s7t.threaded(), (None, None))

    def test_cross_task_isolation(self):
        s7t.begin_task("task-a")
        snap1 = _snapshot()
        self._list(snap1)
        s7t.end_task()
        s7t.begin_task("task-b")
        snap2 = _snapshot()
        self.call("menu_action",
                  {"action": "invoke", "path": "File → Save",
                   "snapshot": snap2, "confirmed": True},
                  approve=True)
        attempt = self.last_attempt("menu_action")
        self.assertIsNone(attempt["handle_id"])

    def test_inert_without_task_for_direct_funnel_calls(self):
        snap1 = _snapshot()
        self._list(snap1)
        snap2 = _snapshot()
        self.call("menu_action",
                  {"action": "invoke", "path": "File → Save",
                   "snapshot": snap2, "confirmed": True},
                  approve=True)
        attempt = self.last_attempt("menu_action")
        self.assertIsNone(attempt["handle_id"])

    def test_dialog_production_threads_to_next_call(self):
        s7t.begin_task("task-a")
        self.dialog_backend.app_reported = "/docs/report.txt"
        # GAPS-23 (F-V7): through the funnel (gate approved).
        result = self.call("file_dialog_action",
                           {"action": "open", "filename": "report.txt",
                            "snapshot": _snapshot()},
                           approve=True)["result"]
        self.assertIn("File dialog open /docs/report.txt", result)
        handle, stamp = s7t.threaded()
        self.assertIsNotNone(handle)
        snap2 = _snapshot()
        self.call("menu_action",
                  {"action": "invoke", "path": "File → Save",
                   "snapshot": snap2, "confirmed": True},
                  approve=True)
        attempt = self.last_attempt("menu_action")
        self.assertEqual(attempt["handle_id"], handle)
        self.assertEqual(attempt["snapshot_stamp"], stamp)

    def test_surface_note_threads_handle_only(self):
        s7t.begin_task("task-a")
        rendered = TOOLS._describe_surface(
            {"ok": True, "surface": "tray", "action": "open_menu",
             "target": "Chat", "target_handle_id": "th-surf-1",
             "verification": None, "refusal": None})
        self.assertIn("th-surf-1", rendered)
        self.assertEqual(s7t.threaded(), ("th-surf-1", None))
        failed = TOOLS._describe_surface(
            {"ok": False, "surface": "tray", "action": "open_menu",
             "target": "Chat", "target_handle_id": "th-surf-2",
             "verification": None,
             "refusal": {"code": "x", "reason": "y"}})
        self.assertEqual(s7t.threaded(), ("th-surf-1", None))

    def test_drag_production_threads_target(self):
        s7t.begin_task("task-a")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "drag.txt"
            path.write_bytes(b"drag me")
            source = th.mint_file(str(path))
            target = th.mint_control(FG, name="Editor",
                                     control_type="edit")
            backend = FakeDragBackend()
            with mock.patch.object(dd, "default_backend",
                                   return_value=backend):
                # GAPS-23 (F-V7): through the funnel (gate approved).
                text = self.call(
                    "drag_drop_action",
                    {"source_handle_id": source["id"],
                     "target_handle_id": target["id"],
                     "payload": {"kind": "files", "files": [str(path)]},
                     "source_observation": {},
                     "target_observation": {"identity": dict(FG)},
                     "snapshot": {"stamp": "snap-drag"},
                     "target_kind": "editor"},
                    approve=True)["result"]
            self.assertIn("via ole", text)
            self.assertEqual(backend.do_calls, 1)
        handle, stamp = s7t.threaded()
        self.assertEqual(handle, target["id"])
        self.assertEqual(stamp, "snap-drag")

    def test_confirm_gate_unaffected_by_threading(self):
        # "Delete" is a C-14 leaf, so it proves all three steps;
        # "Save" deliberately is not (it would resolve without
        # confirmation and prove nothing here).
        s7t.begin_task("task-a")
        snap1 = _snapshot()
        self._list(snap1)
        snap2 = _snapshot()
        refused = self.call("menu_action",
                            {"action": "invoke", "path": "File → Delete",
                             "snapshot": snap2})
        self.assertEqual(refused["kind"], "not_confirmed", refused)
        self.assertEqual(self.menu_backend.invoked, [])
        stopped = self.call("menu_action",
                            {"action": "invoke", "path": "File → Delete",
                             "snapshot": snap2},
                            approve=True)
        # Engine stop through the funnel: status success carrying
        # the stop sentence in result (batch-1 funnel contract —
        # engine refusals are not funnel refusals), zero invokes.
        self.assertEqual(stopped["status"], "success", stopped)
        self.assertIn("confirm it first", stopped["result"])
        self.assertEqual(self.menu_backend.invoked, [])
        resolved = self.call("menu_action",
                             {"action": "invoke", "path": "File → Delete",
                              "snapshot": snap2, "confirmed": True},
                             approve=True)
        self.assertEqual(resolved["status"], "success", resolved)
        self.assertEqual(self.menu_backend.invoked, ["Delete"])

    def test_journal_append_only_under_threading(self):
        s7t.begin_task("task-a")
        snap1 = _snapshot()
        self._list(snap1)
        before = self._receipt_path.read_bytes()
        snap2 = _snapshot()
        self.call("menu_action",
                  {"action": "invoke", "path": "File → Save",
                   "snapshot": snap2, "confirmed": True},
                  approve=True)
        after = self._receipt_path.read_bytes()
        self.assertTrue(after.startswith(before),
                        "receipt store must stay append-only")
        for line in after[len(before):].decode("utf-8").splitlines():
            rec = json.loads(line)
            self.assertIn(rec["type"],
                          ("attempt", "outcome", "dispatched"))

    def test_registry_755_after_batch8(self):  # INTENTIONAL — Click-anywhere (2026-11-14): stale method-name label renamed
        names = [d["name"] for d in TOOLS.get_declarations()]
        # §7 consumers batch 8: INTENTIONAL — 1 tool added
        # (652 -> 653): context_menu_action. Batch 3 itself
        # added no funnel tool.
        # Stale-pin sweep: INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (count proof: registry 655, no duplicates); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        # B-R1-8 (2026-11-09): INTENTIONAL — 1 tool added (656 -> 657): watch_gestures.
        self.assertEqual(len(names), 757,  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan
                         "registry is 734 + 1 after R18-1")
        for name in ("menu_action", "file_dialog_action",
                     "system_surface_action", "adapter_action",
                     "drag_drop_action", "monitor_action",
                     "journal_action"):
            self.assertIn(name, names)


# ----------------------------------------------------------------------
# Coordinator step handles
# ----------------------------------------------------------------------

class TestCoordinatorHandles(ThreadingBase):
    def _fake_coord(self, seen):
        def fake(tool, args):
            seen.append((tool, dict(args)))
            return {"status": "success", "kind": "", "message": "ok"}
        return tc.TurnCoordinator(executor=fake)

    def test_step_handle_injected_per_tool(self):
        cases = (("menu_action", "window_handle_id"),
                 ("file_dialog_action", "window_handle_id"),
                 ("system_surface_action", "target_handle_id"))
        for tool, arg in cases:
            seen = []
            coord = self._fake_coord(seen)
            step = tc.StepDef.from_mapping(
                {"tool": tool, "args": {"action": "list"},
                 "handle_id": "h-declared"})
            task = tc.Task("t", "multi_step", [step])
            outcome = coord._run_step(task, step)
            self.assertEqual(outcome.status, "success")
            self.assertEqual(seen[0][1][arg], "h-declared")

    def test_step_handle_not_injected_for_drag(self):
        seen = []
        coord = self._fake_coord(seen)
        step = tc.StepDef.from_mapping(
            {"tool": "drag_drop_action",
             "args": {"payload": {"kind": "text", "text": "x"}},
             "handle_id": "h-drag"})
        task = tc.Task("t", "multi_step", [step])
        coord._run_step(task, step)
        for key in ("window_handle_id", "target_handle_id",
                    "source_handle_id", "handle_id"):
            self.assertNotIn(key, seen[0][1])

    def test_step_args_unchanged_and_explicit_wins(self):
        seen = []
        coord = self._fake_coord(seen)
        step = tc.StepDef.from_mapping(
            {"tool": "menu_action",
             "args": {"action": "list",
                      "window_handle_id": "h-explicit"},
             "handle_id": "h-declared"})
        task = tc.Task("t", "multi_step", [step])
        coord._run_step(task, step)
        self.assertEqual(seen[0][1]["window_handle_id"], "h-explicit")
        self.assertEqual(step.args["window_handle_id"], "h-explicit")

    def test_step_handle_validation(self):
        with self.assertRaises(ValueError):
            tc.StepDef.from_mapping(
                {"tool": "menu_action", "args": {}, "handle_id": 123})
        # INTENTIONAL (§7 consumers batch 4): menu steps now
        # require a valid action at build time, so these handle
        # vehicles carry "list" — the handle assertions below
        # are unchanged, only the vehicle became a valid step.
        step = tc.StepDef.from_mapping(
            {"tool": "menu_action", "args": {"action": "list"},
             "handle_id": None})
        self.assertEqual(step.handle_id, "")
        step = tc.StepDef.from_mapping(
            {"tool": "menu_action", "args": {"action": "list"}})
        self.assertEqual(step.handle_id, "")

    def test_declared_handle_survives_ledger_round_trip(self):
        step = tc.StepDef.from_mapping(
            {"tool": "menu_action", "args": {"action": "list"},
             "handle_id": "h-ledger"})
        task = tc.Task("t", "multi_step", [step])
        record = tl.task_to_record(task)
        self.assertEqual(record["steps"][0]["handle_id"], "h-ledger")
        record["state"] = "paused"
        record["task_id"] = task.id
        rebuilt = tl.TaskLedger().rebuild_task(record)
        self.assertEqual(rebuilt.steps[0].handle_id, "h-ledger")

    def test_run_task_threads_across_steps_and_clears(self):
        snap1 = _snapshot()
        snap2 = _snapshot()
        seen_threaded = []

        def on_step(task, outcome):
            if len(task.outcomes) == 1:
                seen_threaded.append(s7t.threaded())

        coord = tc.TurnCoordinator()
        steps = [
            tc.StepDef.from_mapping(
                {"tool": "menu_action",
                 "args": {"action": "list", "snapshot": snap1}}),
            tc.StepDef.from_mapping(
                {"tool": "menu_action",
                 "args": {"action": "invoke", "path": "File → Save",
                          "snapshot": snap2, "confirmed": True}}),
        ]
        task = tc.Task("chain", "multi_step", steps)
        confirm_gate.register_handler(lambda n, s: True)
        try:
            receipt = coord.run_task(task, on_step=on_step)
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(receipt.state, "completed", receipt)
        self.assertEqual(len(seen_threaded), 1)
        handle, stamp = seen_threaded[0]
        self.assertIsNotNone(handle)
        self.assertEqual(stamp, snap1["stamp"])
        attempts = self.attempts("menu_action")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[1]["handle_id"], handle)
        self.assertEqual(attempts[1]["snapshot_stamp"], snap1["stamp"])
        self.assertIsNone(s7t.current_task())
        # A later funnel call threads nothing from the dead task.
        snap3 = _snapshot()
        self.call("menu_action",
                  {"action": "invoke", "path": "File → Save",
                   "snapshot": snap3, "confirmed": True},
                  approve=True)
        self.assertIsNone(self.last_attempt("menu_action")["handle_id"])

    def test_failed_task_clears_threading(self):
        # A genuinely failed task: an unapproved gated step is
        # refused by the funnel gate (status refused), so the
        # coordinator fails the task and the finally clears
        # threading. (Engine refusal sentences alone do NOT fail
        # a task through the funnel — they arrive as success
        # carrying the sentence in result, per the batch-1
        # funnel contract — so the gate refusal is the honest
        # failure shape here.)
        snap1 = _snapshot()
        coord = tc.TurnCoordinator()
        steps = [
            tc.StepDef.from_mapping(
                {"tool": "menu_action",
                 "args": {"action": "invoke", "path": "File → Save",
                          "snapshot": snap1}}),
        ]
        task = tc.Task("fails", "multi_step", steps)
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed", receipt)
        self.assertEqual(self.menu_backend.invoked, [])
        self.assertIsNone(s7t.current_task())
        self.assertEqual(s7t.threaded(), (None, None))


if __name__ == "__main__":
    unittest.main()

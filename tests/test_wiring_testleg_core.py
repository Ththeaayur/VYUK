"""Wiring item 5, Part B — caller-path tests for the core tools.

Covers the 54 core tools that lack a verified real-funnel test
(see ~/workspace/code-lab/wiring_testleg_recount_v600.md for the
recount that corrected the inventory's 41 to 54: the inventory
counted stub/spy registries, gate predicates, ranking lists, and
faked-executor step-dicts as "calls" for 13 core tools).

Every test invokes its tool through the REAL tools.execute funnel
with the REAL registered declaration and handler — never a spy
registry, never a faked executor, never a bare registration
assert. What the test asserts is the tool's HONEST lab outcome:

- pure tools: the computed answer;
- Windows-only tools on Linux: the Windows-only sentence
  (success status — the tool ran and truthfully reported it
  cannot act here);
- store tools: round-trips against TEMP stores (planner paths,
  memory DB, scheduler store, focus record dir) — never the real
  working data/;
- gated tools: the refusal without approval AND the handler
  outcome with approval (no real power/close/launch action is
  ever approved);
- live-network tools: the measurement OR the honest failure
  sentence (branch documented per test — the funnel + handler
  proof holds in both branches; the branch depends on network
  availability, which is environmental, not behavioural).

Isolation per test: fresh temp dir; planner paths, memory store,
and scheduler store redirected to it; the whole working data/
tree snapshotted and restored; provenance forced to user-started
with the lock off;
taint cleared; confirm handler cleared; the desktop-backend
routing env (VYUK_PLATFORM) unset. Args dicts are never reused
across calls: the funnel annotates a caller's dict (it adds a
rule_of_two key — observed during probing, against the
no-mutation note in the registry docstring).

Known defects pinned (recorded in the item report, NOT fixed
here — product fixes are out of scope for this item):
- adjust_volume raises on Linux (ctypes.windll) instead of
  returning a Windows-only sentence like its siblings.
- open_url reports "Opened ..." without checking that a browser
  actually opened (webbrowser.open's result is ignored).

Stdlib unittest only. Run: PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1
python -m unittest tests.test_wiring_testleg_core -v
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from core import confirm_gate, injection
    from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.brain import planner  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
    from core.brain import planner_checkpoint, planner_plans, planner_routines  # INTENTIONAL (skills split B41): impl modules for dual-patching (see setUp notes below)
    from core.config import config as app_config
    from core.memory.memory import MemoryStore  # Phase 3 M2h INTENTIONAL: import moved with module
    from core.scheduler import ProactiveScheduler
    import core.config as cfg_mod
    import core.memory.episodes as episodes_mod
    import core.scheduler as scheduler_mod
    import core.funnel.tools_handlers as tools_handlers_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")

try:
    from core.platform import reset_backend_cache
except Exception:  # noqa: BLE001 — optional seam
    reset_backend_cache = None

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _PACKAGE_ROOT / "data"


def _body(out):
    if not isinstance(out, dict):
        return str(out)
    value = out.get("result")
    if value is None:
        value = out.get("message", "")
    return str(value)


class _DataGuard:
    """Snapshot/restore the ENTIRE working data/ tree per test.

    The funnel's side effects are not limited to the three log
    files: a suite run also created records.jsonl and
    milestones.jsonl. Snapshotting the whole tree (369 KB) is
    cheap and covers future side-effect files too. New files are
    deleted on restore; changed files get their bytes back."""

    def __init__(self):
        self._saved = {}

    def _scan(self):
        found = {}
        for path in sorted(_DATA_DIR.rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(_DATA_DIR)] = path.read_bytes()
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
                    (_DATA_DIR / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = _DATA_DIR / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class _Base(unittest.TestCase):
    """Common isolation: temp stores, clean funnel state, data guard."""

    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="wiring_testleg_"))
        self._log_guard = _DataGuard()
        self._log_guard.__enter__()
        try:
            self._setUp_inner()
        except Exception:
            self._log_guard.__exit__(None, None, None)
            shutil.rmtree(self._tmp, ignore_errors=True)
            raise

    def _setUp_inner(self):
        self._orig_env = os.environ.pop("VYUK_PLATFORM", None)
        if reset_backend_cache is not None:
            try:
                reset_backend_cache()
            except Exception:  # noqa: BLE001 — best effort only
                pass
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        tools_mod.revoke_screen_scope()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config, "session_provenance_lock", False)
        app_config.session_provenance_lock = False
        self._orig_plans = planner._PLANS_PATH
        self._orig_routines = planner._ROUTINES_PATH
        planner._PLANS_PATH = self._tmp / "plans.json"
        planner._ROUTINES_PATH = self._tmp / "routines.json"
        # INTENTIONAL (skills split B41): _PLANS_PATH/_ROUTINES_PATH are
        # bound in the impl modules too (snapshot imports); patch every
        # binding with the same value, or moved callers would miss it.
        planner_plans._PLANS_PATH = planner._PLANS_PATH
        planner_checkpoint._PLANS_PATH = planner._PLANS_PATH
        planner_routines._ROUTINES_PATH = planner._ROUTINES_PATH
        self._tmpstore = MemoryStore(db_path=str(self._tmp / "probe.db"))
        self._orig_th_mem = tools_handlers_mod.memory_store
        self._orig_ep_mem = episodes_mod.memory_store
        tools_handlers_mod.memory_store = self._tmpstore
        episodes_mod.memory_store = self._tmpstore
        # INTENTIONAL (skills split B49): _tool_remember_fact /
        # _tool_recall_memories moved to the command_url mixin and
        # resolve memory_store from the mixin module's globals, so the
        # facade patch alone no longer steers them — patch BOTH
        # bindings (B16/B41 pattern), same store, both restored.
        from core.funnel import tools_handlers_command_url as _th_m1
        self._orig_th_m1_mem = _th_m1.memory_store
        _th_m1.memory_store = self._tmpstore
        # Scheduler: only core.scheduler.scheduler is patched.
        # tools_handlers holds NO module-level scheduler attribute
        # (its handlers import it locally at call time), so the
        # singleton patch covers every scheduler touch.
        self._tmpsched = ProactiveScheduler(store_path=self._tmp / "sched.json")
        self._orig_sched_mod = scheduler_mod.scheduler
        scheduler_mod.scheduler = self._tmpsched

    def tearDown(self):
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.revoke_screen_scope()
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        planner._PLANS_PATH = self._orig_plans
        planner_plans._PLANS_PATH = self._orig_plans
        planner_checkpoint._PLANS_PATH = self._orig_plans
        planner._ROUTINES_PATH = self._orig_routines
        planner_routines._ROUTINES_PATH = self._orig_routines
        tools_handlers_mod.memory_store = self._orig_th_mem
        episodes_mod.memory_store = self._orig_ep_mem
        from core.funnel import tools_handlers_command_url as _th_m1
        _th_m1.memory_store = self._orig_th_m1_mem
        scheduler_mod.scheduler = self._orig_sched_mod
        if self._orig_env is None:
            os.environ.pop("VYUK_PLATFORM", None)
        else:
            os.environ["VYUK_PLATFORM"] = self._orig_env
        if reset_backend_cache is not None:
            try:
                reset_backend_cache()
            except Exception:  # noqa: BLE001 — best effort only
                pass
        self._log_guard.__exit__(None, None, None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def call(self, name, args, approve=False):
        """One funnel call with a FRESH args dict (never reused:
        the funnel annotates the caller's dict)."""
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()

    def assert_success(self, out):
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "success", str(out))


class _FocusBase(_Base):
    """Adds a temp DATA_DIR: focus_session resolves its record path
    from core.config.DATA_DIR fresh on every call, so the record
    (and any focus reminder) stays in temp. DATA_DIR is a Path —
    a str breaks Path joins elsewhere (probe1 artifact)."""

    def setUp(self):
        super().setUp()
        self._orig_data_dir = cfg_mod.DATA_DIR
        cfg_mod.DATA_DIR = self._tmp

    def tearDown(self):
        cfg_mod.DATA_DIR = self._orig_data_dir
        super().tearDown()


class PureToolsTests(_Base):
    """Pure/local tools: the computed answer, no side effects."""

    def test_world_clock_city_and_panel(self):
        out = self.call("world_clock", {"city": "Tokyo"})
        self.assert_success(out)
        self.assertIn("Tokyo", _body(out))
        self.assertIn("ahead of here", _body(out))
        out = self.call("world_clock", {})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("World clock:", body)
        self.assertIn("New York", body)
        self.assertIn("Tokyo", body)

    def test_quick_math(self):
        out = self.call("quick_math", {"expression": "2+3*4"})
        self.assert_success(out)
        self.assertIn("2+3*4 = 14", _body(out))

    def test_unit_convert(self):
        out = self.call("unit_convert", {"value": 5, "from_unit": "miles",
                                         "to_unit": "km"})
        self.assert_success(out)
        self.assertIn("5 mi = 8.04672 km", _body(out))

    def test_date_calculator(self):
        out = self.call("date_calculator", {"operation": "days_between",
                                            "date_a": "2026-01-01",
                                            "date_b": "2026-01-10"})
        self.assert_success(out)
        self.assertIn("= 9 days", _body(out))
        out = self.call("date_calculator", {"operation": "weekday",
                                            "date_a": "2026-11-01"})
        self.assert_success(out)
        self.assertIn("is a Sunday", _body(out))

    def test_get_system_info_branch(self):
        # Branch is environmental (psutil presence), both honest:
        # no psutil -> unavailable sentence; psutil -> the live
        # system status (values vary, so only the shape is pinned).
        out = self.call("get_system_info", {})
        self.assert_success(out)
        body = _body(out)
        self.assertTrue(
            "System status —" in body
            or "System info isn't available in this build" in body, body)

    def test_get_activity_log_reads_own_call(self):
        self.call("world_clock", {"city": "Tokyo"})
        out = self.call("get_activity_log", {"lines": 20,
                                             "contains": "world_clock"})
        self.assert_success(out)
        self.assertIn("world_clock", _body(out))

    def test_battery_info_branch(self):
        # Branch is environmental (psutil presence), both honest:
        # no psutil -> unavailable sentence; psutil + no battery ->
        # the no-battery sentence. The VM has no battery either way.
        out = self.call("battery_info", {})
        self.assert_success(out)
        body = _body(out)
        self.assertTrue(
            "No battery detected" in body
            or "Battery info isn't available in this build" in body, body)

    def test_wifi_info(self):
        out = self.call("wifi_info", {})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("local IP is", body)
        self.assertIn("netsh", body)


class WindowsOnlyOnLinuxTests(_Base):
    """Desktop/audio/display tools: on Linux they truthfully report
    the Windows-only limitation (success status — the tool ran)."""

    def test_set_volume_level(self):
        out = self.call("set_volume_level", {"percent": 50})
        self.assert_success(out)
        self.assertIn("needs Windows audio endpoints (pycaw)", _body(out))

    def test_media_control(self):
        out = self.call("media_control", {"action": "play_pause"})
        self.assert_success(out)
        self.assertIn("only available on Windows", _body(out))

    def test_set_brightness(self):
        out = self.call("set_brightness", {"level": 50})
        self.assert_success(out)
        self.assertIn("requires Windows (WMI", _body(out))

    def test_set_focus_assist(self):
        out = self.call("set_focus_assist", {"on": False})
        self.assert_success(out)
        self.assertIn("Focus Assist is a Windows feature", _body(out))

    def test_notify_user(self):
        out = self.call("notify_user", {"title": "probe",
                                        "message": "probe notification xyz"})
        self.assert_success(out)
        self.assertIn("Toast notifications need Windows", _body(out))

    def test_keep_awake(self):
        out = self.call("keep_awake", {"action": "status"})
        self.assert_success(out)
        self.assertIn("Keep-awake is off", _body(out))
        out = self.call("keep_awake", {"action": "start", "minutes": 1})
        self.assert_success(out)
        self.assertIn("Keep-awake uses a Windows power API", _body(out))

    def test_adjust_volume_windows_only_sentence(self):
        # FIXED (post-triage defect batch): adjust_volume now
        # returns the Windows-only sentence like its siblings
        # instead of raising AttributeError (ctypes.windll) on
        # Linux. INTENTIONAL — round-3 fix Q1 (2026-11-19): "I can't
        # change the volume ..." is a refusal sentence, so the
        # funnel now surfaces it as refused, not success carrying
        # the honest sentence — the same shape as the other
        # refusal sentences. The sentence itself is unchanged.
        out = self.call("adjust_volume", {"action": "up", "steps": 1})
        self.assertEqual(out.get("status"), "refused", str(out))
        self.assertIn("Windows", _body(out))
        self.assertIn("media keys", _body(out))


class DesktopSeamTests(_Base):
    """Desktop/input/browser seams on a headless Linux system:
    honest unavailable messages, nothing touched or launched."""

    def test_window_control_list(self):
        out = self.call("window_control", {"action": "list"})
        self.assert_success(out)
        self.assertIn("Window control isn't available on this Linux system yet",
                      _body(out))

    def test_get_active_window(self):
        out = self.call("get_active_window", {})
        self.assert_success(out)
        self.assertIn("No foreground window found.", _body(out))

    def test_mouse_control(self):
        out = self.call("mouse_control", {"action": "move", "x": 0, "y": 0})
        self.assert_success(out)
        self.assertIn("Input isn't available on this Linux system yet",
                      _body(out))

    def test_type_text_free_when_untainted(self):
        # type_text is NOT in _CONFIRM_GATE_TOOLS: _needs_confirmation
        # gates it only when the foreground is a shell, and the
        # suite's setUp clears taint — so on headless Linux the
        # free path runs and honestly reports no input seam.
        # (Probe1's earlier refusal was the TAINT gate: its session
        # had been tainted by prior live-web calls. That
        # session-state behaviour is not what this test pins.)
        out = self.call("type_text", {"text": "probe"})
        self.assert_success(out)
        self.assertIn("Input isn't available on this Linux system yet",
                      _body(out))
        out = self.call("type_text", {"text": "probe"}, approve=True)
        self.assert_success(out)
        self.assertIn("Input isn't available on this Linux system yet",
                      _body(out))

    def test_take_screenshot_headless(self):
        # INTENTIONAL — round-3 fix Q1 (2026-11-19): "Could not
        # capture a screenshot" is a refusal sentence, so the funnel
        # now surfaces it as refused, not success carrying the
        # sentence. The honest sentence is unchanged, only the
        # status: nothing was captured, so nothing is recorded done.
        out = self.call("take_screenshot", {})
        self.assertEqual(out.get("status"), "refused", str(out))
        body = _body(out)
        self.assertIn("Could not capture a screenshot", body)
        self.assertIn("headless", body)

    def test_browser_control_status(self):
        out = self.call("browser_control", {"action": "status"})
        self.assert_success(out)
        self.assertIn("Not connected to any browser.", _body(out))

    def test_browser_read_no_session(self):
        out = self.call("browser_read", {"action": "title_url"})
        self.assert_success(out)
        self.assertIn("No controllable browser session is active.",
                      _body(out))

    def test_browser_click_no_session(self):
        out = self.call("browser_click",
                        {"target": "definitely_not_present_xyz"})
        self.assert_success(out)
        self.assertIn("No controllable browser session is active.",
                      _body(out))

    def test_browser_type_no_session(self):
        out = self.call("browser_type",
                        {"target": "definitely_not_present_xyz",
                         "text": "hi"})
        self.assert_success(out)
        self.assertIn("No controllable browser session is active.",
                      _body(out))

    def test_launch_application_absent(self):
        # INTENTIONAL — round-3 fix Q1 (2026-11-19): "Could not find
        # an application named ..." is a refusal sentence, so the
        # funnel now surfaces it as refused, not success carrying
        # the sentence. Nothing was launched, so nothing is recorded
        # done.
        out = self.call("launch_application",
                        {"app_name": "definitely_not_installed_app_xyz"})
        self.assertEqual(out.get("status"), "refused", str(out))
        self.assertIn("Could not find an application named "
                      "'definitely_not_installed_app_xyz'", _body(out))

    def test_open_path_not_found(self):
        out = self.call("open_path",
                        {"path": "/tmp/definitely_not_present_xyz_123.txt"})
        self.assert_success(out)
        self.assertIn("Path not found: /tmp/definitely_not_present_xyz_123.txt",
                      _body(out))

    def test_open_url_open_claim_verified(self):
        # FIXED (post-triage defect batch): open_url now checks
        # webbrowser.open's result — a False result yields an
        # honest failure sentence instead of an "Opened ..."
        # claim. Both branches pinned with a stubbed opener so
        # the test is deterministic on any machine.
        with mock.patch("webbrowser.open", return_value=False):
            out = self.call("open_url", {"url": "not a url"})
            self.assert_success(out)
            self.assertIn("didn't open it", _body(out))
            self.assertNotIn("Opened https://not a url", _body(out))
        with mock.patch("webbrowser.open", return_value=True):
            out = self.call("open_url", {"url": "https://example.invalid"})
            self.assert_success(out)
            self.assertIn("Opened https://example.invalid", _body(out))

    def test_lock_screen_headless(self):
        out = self.call("lock_screen", {})
        self.assert_success(out)
        self.assertIn("I tried to lock this PC with xdg-screensaver, "
                      "but it failed", _body(out))

    def test_close_application(self):
        out = self.call("close_application",
                        {"name": "definitely_not_running_xyz"})
        self.assertEqual(out.get("status"), "refused", str(out))
        self.assertEqual(out.get("kind"), "not_confirmed", str(out))
        out = self.call("close_application",
                        {"name": "definitely_not_running_xyz",
                         "confirm": True}, approve=True)
        self.assert_success(out)
        self.assertIn("Window control isn't available on this Linux system yet",
                      _body(out))

    def test_power_control_never_acts(self):
        # No power action is ever approved: the refused call proves
        # the gate, and the approved call carries confirm=false,
        # so the handler reports the Windows-only limitation
        # without attempting sleep.
        out = self.call("power_control", {"action": "sleep",
                                          "confirm": False})
        self.assertEqual(out.get("status"), "refused", str(out))
        self.assertEqual(out.get("kind"), "not_confirmed", str(out))
        out = self.call("power_control", {"action": "sleep",
                                          "confirm": False}, approve=True)
        self.assert_success(out)
        self.assertIn("Sleep via SetSuspendState is only available on Windows",
                      _body(out))

    def test_network_control(self):
        # status is a READ (tools_gate.py: status / wifi_list only
        # report), so it runs free without approval; the mutating
        # actions (wifi_on / wifi_off / wifi_connect) are the gated
        # ones, and no real network mutation is approved in tests.
        out = self.call("network_control", {"action": "status"})
        self.assert_success(out)
        self.assertIn("Hostname:", _body(out))
        out = self.call("network_control", {"action": "status"}, approve=True)
        self.assert_success(out)
        self.assertIn("Hostname:", _body(out))


class StoreToolsTests(_Base):
    """Store tools: temp stores only, round-trips via the funnel."""

    def test_remember_and_recall_fact(self):
        out = self.call("remember_user_fact",
                        {"key": "probe_key_xyz", "value": "probe_value_xyz"})
        self.assert_success(out)
        self.assertIn("Saved to memory: 'probe_key_xyz' = 'probe_value_xyz'",
                      _body(out))
        out = self.call("recall_memories", {"query": "probe_value_xyz"})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("probe_key_xyz", body)
        self.assertIn("probe_value_xyz", body)

    def test_recall_day_empty_then_journal(self):
        out = self.call("recall_day", {"when": "today"})
        self.assert_success(out)
        self.assertIn("Nothing recorded for", _body(out))
        out = self.call("remember_today", {"text": "probe journal note xyz"})
        self.assert_success(out)
        self.assertIn("Added to today's journal: probe journal note xyz",
                      _body(out))
        out = self.call("recall_day", {"when": "today"})
        self.assert_success(out)
        self.assertIn("probe journal note xyz", _body(out))

    def test_set_reminder_temp_scheduler(self):
        out = self.call("set_reminder", {"message": "probe reminder xyz",
                                         "minutes": 1440})
        self.assert_success(out)
        self.assertIn("Reminder set: 'probe reminder xyz' in 1440 minute(s)",
                      _body(out))

    def test_read_clipboard_unavailable(self):
        # Both lab interpreters lack pyperclip; pinned + commented
        # so a future pyperclip install fails honestly here and
        # the test is updated, not silently re-purposed.
        out = self.call("read_clipboard", {})
        self.assert_success(out)
        self.assertIn("Clipboard reading isn't available in this build",
                      _body(out))

    def test_write_clipboard_unavailable(self):
        out = self.call("write_clipboard", {"text": "probe clipboard xyz"})
        self.assert_success(out)
        self.assertIn("Clipboard writing isn't available in this build",
                      _body(out))

    def test_app_settings_get_branch(self):
        # Branch is environmental (PySide6 presence), both honest:
        # no PySide6 -> unavailable failure; PySide6 -> success
        # with the settings shape.
        try:
            import PySide6  # noqa: F401
            has_qt = True
        except Exception:  # noqa: BLE001
            has_qt = False
        out = self.call("app_settings", {"action": "get"})
        if has_qt:
            self.assert_success(out)
            self.assertIn("Her settings right now:", _body(out))
        else:
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "failure", str(out))
            self.assertEqual(out.get("kind"), "unavailable", str(out))
            self.assertIn("No module named 'PySide6'", _body(out))

    def test_create_plan_and_list_plans(self):
        out = self.call("list_plans", {})
        self.assert_success(out)
        self.assertIn("No plans yet.", _body(out))
        out = self.call("create_plan",
                        {"goal": "probe goal xyz",
                         "steps": [{"text": "step one", "tool": "quick_math",
                                    "args": {"expression": "2+3"}}]})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("created: probe goal xyz", body)
        self.assertIn("step one [via quick_math]", body)
        out = self.call("list_plans", {})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("probe goal xyz", body)
        self.assertIn("steps done", body)

    def test_list_routines(self):
        out = self.call("list_routines", {})
        self.assert_success(out)
        self.assertIn("No routines saved yet.", _body(out))
        # Planner paths must be Path objects: a str path silently
        # makes the loader return [] (probe2 lesson).
        (self._tmp / "routines.json").write_text(json.dumps(
            [{"name": "probe_routine_xyz",
              "actions": [{"tool": "quick_math",
                           "args": {"expression": "2+3"}}]}]))
        out = self.call("list_routines", {})
        self.assert_success(out)
        self.assertIn("probe_routine_xyz: 1 action(s)", _body(out))

    def test_run_routine_end_to_end(self):
        (self._tmp / "routines.json").write_text(json.dumps(
            [{"name": "probe_routine_xyz",
              "actions": [{"tool": "quick_math",
                           "args": {"expression": "2+3"}}]}]))
        out = self.call("run_routine", {"name": "probe_routine_xyz"})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("Running routine 'probe_routine_xyz'", body)
        # T-13 (debug pass): the standalone run_routine routes
        # through run_routine_as_task -> the task coordinator ->
        # format_receipt, so the body is a task receipt ("✓
        # quick_math — success" + a State line), not the LEGACY
        # run_routine rendering ("quick_math — 2+3 = 5" /
        # "all 1 action(s) completed"). The receipt step record
        # is {label, status, kind} by design — it carries no
        # step result text. (Whether receipts SHOULD carry step
        # results is an owed product decision, not a test fix.)
        self.assertIn("✓ quick_math — success", body)
        self.assertIn(
            "State: completed (attempted 1, completed 1, "
            "failed 0, skipped 0)", body)

    def test_find_files_temp_root(self):
        root = self._tmp / "searchroot"
        root.mkdir()
        (root / "probe_file_xyz.txt").write_text("probe\n")
        out = self.call("find_files", {"root": str(root),
                                       "name_pattern": "probe_file_xyz"})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("Found 1 file(s)", body)
        self.assertIn("probe_file_xyz", body)
        out = self.call("find_files", {"root": str(root),
                                       "name_pattern": "definitely_not_present_xyz_123"})
        self.assert_success(out)
        self.assertIn("No files matching 'definitely_not_present_xyz_123'",
                      _body(out))


class GoogleToolsTests(_Base):
    """Google tools with no connected account: the honest
    not-connected sentence (success status — the tool ran and
    reported the account state)."""

    def test_get_todays_schedule(self):
        out = self.call("get_todays_schedule", {})
        self.assert_success(out)
        self.assertIn("Google isn't connected yet", _body(out))

    def test_get_upcoming_events(self):
        out = self.call("get_upcoming_events", {"days": 2, "limit": 5})
        self.assert_success(out)
        self.assertIn("Google isn't connected yet", _body(out))

    def test_get_unread_email(self):
        out = self.call("get_unread_email", {"limit": 3})
        self.assert_success(out)
        self.assertIn("Google isn't connected yet", _body(out))

    def test_search_email(self):
        out = self.call("search_email", {"query": "probe_xyz", "limit": 3})
        self.assert_success(out)
        self.assertIn("Google isn't connected yet", _body(out))


class FocusSessionTests(_FocusBase):
    """Focus lifecycle on headless Linux: start is honestly
    blocked by the Windows-only Focus Assist gate, so no session
    is ever created and stop is a clean no-op."""

    def test_focus_lifecycle_linux(self):
        out = self.call("focus_session", {"action": "status"})
        self.assert_success(out)
        self.assertIn("No focus session is active.", _body(out))
        out = self.call("focus_session", {"action": "start", "minutes": 25})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("Focus session not started", body)
        self.assertIn("Focus Assist couldn't be turned on", body)
        out = self.call("focus_session", {"action": "status"})
        self.assert_success(out)
        self.assertIn("No focus session is active.", _body(out))
        out = self.call("focus_session", {"action": "stop"})
        self.assert_success(out)
        self.assertIn("No focus session is active — nothing to stop",
                      _body(out))


class LiveNetworkTests(_Base):
    """Live-network tools: the measurement OR the honest failure
    sentence. Both branches prove funnel + handler; the branch
    depends on network availability, which is environmental."""

    def test_speed_test(self):
        out = self.call("speed_test", {})
        self.assert_success(out)
        body = _body(out)
        self.assertTrue("Download speed:" in body
                        or "could not run" in body, body)

    def test_public_ip(self):
        out = self.call("public_ip", {})
        self.assert_success(out)
        body = _body(out)
        self.assertTrue("Your public IP address is" in body
                        or "Could not look up" in body, body)

    def test_get_weather(self):
        out = self.call("get_weather", {"location": "London"})
        self.assert_success(out)
        body = _body(out)
        # Live-network contract (see the suite header): the
        # measurement OR the honest failure sentence. A provider
        # HTTP 429 lands in the failure branch ("Weather fetch
        # failed: HTTP Error 429: ...") — that is environmental,
        # not a product regression, so it passes here.
        self.assertTrue("Weather in London:" in body
                        or "Could not" in body
                        or "Weather fetch failed:" in body, body)

    def test_web_search(self):
        out = self.call("web_search",
                        {"query": "probe_xyz_unlikely_query"})
        self.assert_success(out)
        body = _body(out)
        self.assertTrue("no instant answer" in body
                        or "http" in body
                        or "Web search failed" in body, body)

    def test_speak_text_unavailable(self):
        # Both lab interpreters lack pyttsx3; pinned + commented
        # like the clipboard tests (future install updates it).
        out = self.call("speak_text", {"text": "probe"})
        self.assert_success(out)
        self.assertIn("Reading aloud isn't available in this build",
                      _body(out))


if __name__ == "__main__":
    unittest.main()

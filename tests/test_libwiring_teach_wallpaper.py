"""Libwiring sweep: teach + wallpaper funnel tools.

Teach mode (w8) and the wallpaper pilot (w9) were wiring helpers
with no user-action path. These tests prove the new funnel tools:

- record/list/replay through the REAL funnel (record + replay
  are confirm-gated, so the approving handler is installed per
  call exactly like _journey_base's call helper, and cleared
  after every call);
- replay semantics at the w8 level with the real funnel as the
  inner executor (the outer funnel gate would refuse an
  unapproved replay before any step ran, so the halt proof
  lives here: a gated inner step halts the replay and later
  steps never run);
- wallpaper resolution that never applies anything (the pilot's
  closing step — Windows validation + explicit owner decision —
  is still owed, so applied is always no).

Isolation mirrors the journey pattern: the whole data/ tree is
snapshotted per test and restored after (taught sequences are
test files, never real user data), taint is cleared, provenance
is user-started with the lock off, and the confirm handler is
cleared after every call.
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import confirm_gate, injection
from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel import tools_gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.config import config as app_config

try:
    from core import w9_wiring as _w9
    _W9_AVAILABLE = True
except Exception:  # noqa: BLE001 — PySide6 absent on this interpreter
    _w9 = None
    _W9_AVAILABLE = False

_DATA_DIR = ROOT / "data"

READ_STEP = {"tool": "get_current_time_date", "args": {},
             "verify": {"kind": "ok"}}


class DataGuard:
    """Snapshot/restore the ENTIRE working data/ tree per test."""

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


class FunnelBase(unittest.TestCase):
    def setUp(self):
        self._guard = DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False

    def tearDown(self):
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


class TestTeachFunnel(FunnelBase):
    def test_record_list_replay_through_real_funnel(self):
        recorded = self.call("teach_record_sequence",
                             {"name": "libwire-demo",
                              "steps": [dict(READ_STEP)]},
                             approve=True)
        self.assertEqual(recorded["status"], "success", recorded)
        self.assertIn("recorded", recorded["result"])
        self.assertIn("libwire-demo", recorded["result"])
        listed = self.call("teach_list_sequences", {})
        self.assertEqual(listed["status"], "success", listed)
        self.assertIn("libwire-demo", listed["result"])
        replayed = self.call("teach_replay_sequence",
                             {"name": "libwire-demo"},
                             approve=True)
        self.assertEqual(replayed["status"], "success", replayed)
        self.assertIn("completed", replayed["result"])

    def test_record_refused_without_approval_stores_nothing(self):
        refused = self.call("teach_record_sequence",
                            {"name": "libwire-nope",
                             "steps": [dict(READ_STEP)]})
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "not_confirmed")
        listed = self.call("teach_list_sequences", {})
        self.assertEqual(listed["status"], "success", listed)
        self.assertNotIn("libwire-nope", listed["result"])

    def test_replay_refused_without_approval(self):
        self.call("teach_record_sequence",
                  {"name": "libwire-gated", "steps": [dict(READ_STEP)]},
                  approve=True)
        refused = self.call("teach_replay_sequence",
                            {"name": "libwire-gated"})
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "not_confirmed")

    def test_schema_validation_rejects_bad_args_before_handler(self):
        # The funnel's contract: a schema violation is a refused /
        # validation result, never a handler run.
        bad = self.call("teach_record_sequence",
                        {"name": "libwire-bad", "steps": "not-a-list"},
                        approve=True)
        self.assertEqual(bad["status"], "refused", bad)
        self.assertEqual(bad["kind"], "validation")
        self.assertIn("'steps' must be an array", bad["message"])


class TestTeachReplaySemantics(FunnelBase):
    def test_mismatched_inputs_refused_no_step_ran(self):
        from core import w8_wiring as w8
        step = {"tool": "get_current_time_date",
                "args": {"city": "{city}"},
                "verify": {"kind": "ok"}}
        # A placeholder step with a pattern the inputs must match.
        w8.record_taught_sequence(
            "libwire-pattern", [step], pattern={"city": "[A-Z]+"})
        result = w8.replay_taught_sequence(
            "libwire-pattern", {"city": "mumbai"})
        self.assertEqual(result.status, "refused")
        self.assertEqual(result.steps_completed, 0)
        self.assertEqual(result.step_results, [])

    def test_gated_step_halts_later_steps_never_run(self):
        from core import w8_wiring as w8
        gated = {"tool": "teach_record_sequence",
                 "args": {"name": "libwire-inner",
                          "steps": [dict(READ_STEP)]},
                 "verify": {"kind": "ok"}}
        w8.record_taught_sequence(
            "libwire-halt", [gated, dict(READ_STEP)])
        # No approval handler: the gated inner step is refused by
        # the real funnel, so the replay must halt at step 1.
        result = w8.replay_taught_sequence("libwire-halt", {})
        self.assertEqual(result.status, "halted")
        self.assertEqual(result.steps_completed, 0)
        self.assertEqual(len(result.step_results), 1)
        self.assertEqual(result.step_results[0]["step"], 1)
        self.assertIn("no later step ran", result.reason)

    def test_unknown_sequence_is_an_honest_error(self):
        from core import w8_wiring as w8
        with self.assertRaises(ValueError) as ctx:
            w8.replay_taught_sequence("libwire-no-such", {})
        self.assertIn("no taught sequence stored as 'libwire-no-such'",
                      str(ctx.exception))
        # GAPS-23 (F-V7): through the funnel (gate approved) — the
        # handler's own honest error is what this pins.
        text = self.call("teach_replay_sequence",
                         {"name": "libwire-no-such"},
                         approve=True)["result"]
        self.assertIn("Couldn't replay", text)
        self.assertIn("no taught sequence stored as 'libwire-no-such'",
                      text)

    def test_malformed_steps_refused_nothing_persisted(self):
        # GAPS-23 (F-V7): through the funnel (gate approved) — the
        # handler's own refusals are what this pins.
        text = self.call(
            "teach_record_sequence",
            {"name": "libwire-malformed",
             "steps": [{"tool": "", "args": {}}]},
            approve=True)["result"]
        self.assertIn("Couldn't record", text)
        text = self.call("teach_record_sequence",
                         {"name": "", "steps": [dict(READ_STEP)]},
                         approve=True)["result"]
        self.assertIn("non-empty name", text)
        listed = self.call("teach_list_sequences", {})
        self.assertNotIn("libwire-malformed", listed["result"])


class TestLibwireClassifications(FunnelBase):
    def test_confirm_and_read_classes(self):
        self.assertTrue(
            tools_gate._needs_confirmation("teach_record_sequence", {}))
        self.assertTrue(
            tools_gate._needs_confirmation("teach_replay_sequence", {}))
        self.assertFalse(
            tools_gate._needs_confirmation("teach_list_sequences", {}))
        self.assertFalse(
            tools_gate._needs_confirmation("wallpaper_state", {}))
        self.assertTrue(
            tools_gate._is_read_class("teach_list_sequences", {}))
        self.assertTrue(
            tools_gate._is_read_class("wallpaper_state", {}))
        self.assertFalse(
            tools_gate._is_read_class("teach_record_sequence", {}))
        self.assertFalse(
            tools_gate._is_read_class("teach_replay_sequence", {}))


class TestWallpaperState(FunnelBase):
    def test_validation_errors_are_honest_without_qt(self):
        bad_hour = self.call("wallpaper_state", {"hour": 25})
        self.assertEqual(bad_hour["status"], "success", bad_hour)
        self.assertIn("0 to 23", bad_hour["result"])
        bad_state = self.call(
            "wallpaper_state",
            {"hour": 10,
             "entries": [{"state": "nope", "art_path": "/x/y.jpg"}]})
        self.assertEqual(bad_state["status"], "success", bad_state)
        self.assertIn("Unknown wallpaper state 'nope'", bad_state["result"])
        # A non-object entry never reaches the handler: the funnel
        # schema gate refuses it first (refused / validation).
        bad_entry = self.call(
            "wallpaper_state",
            {"hour": 10, "entries": ["not-a-mapping"]})
        self.assertEqual(bad_entry["status"], "refused", bad_entry)
        self.assertEqual(bad_entry["kind"], "validation")
        # The handler's own mapping check still stands for direct
        # callers, behind the schema gate — pinned here through the
        # module-level core function (GAPS-23 F-V7), the same code
        # the funnel delegates to.
        from core.funnel.tools_handlers_pack_libwire import wallpaper_state_core  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        text = wallpaper_state_core(
            hour=10, entries=["not-a-mapping"])
        self.assertIn("{state, art_path}", text)

    @unittest.skipUnless(_W9_AVAILABLE,
                         "w9_wiring needs PySide6 (Qt interpreter)")
    def test_resolves_state_never_applies(self):
        # Hour 10 is "day" on the default schedule (day 6-18).
        resolved = self.call("wallpaper_state",
                             {"opt_in": False, "hour": 10})
        self.assertEqual(resolved["status"], "success", resolved)
        self.assertIn("day", resolved["result"])
        self.assertIn("Applied: no", resolved["result"])
        # Even opted in, resolution never applies: applying is a
        # separate confirm-gated wallpaper_apply tool (decided under
        # the code-rulings delegation, built 2026-11-03); field
        # validation on real Windows is still owed, and the lab
        # host is not Windows.
        opted = self.call("wallpaper_state",
                          {"opt_in": True, "hour": 10,
                           "entries": [{"state": "day",
                                        "art_path": "/user/art/day.jpg"}]})
        self.assertEqual(opted["status"], "success", opted)
        self.assertIn("day", opted["result"])
        self.assertIn("/user/art/day.jpg", opted["result"])
        self.assertIn("Applied: no", opted["result"])


if __name__ == "__main__":
    unittest.main()

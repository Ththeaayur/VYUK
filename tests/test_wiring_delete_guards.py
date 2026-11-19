"""Shipped unit tests (wiring item 1, v6.0.0 post-ship): delete guards.

The four true delete tools — delete_plan, delete_routine, delete_snippet,
delete_window_layout — must be confirm-gated (standing C-14 approve-first
rule): each destroys a record that cannot be undone, with no trash path.
The wiring inventory (wiring_inventory_v600.md) found all four ungated.

Proves, at caller level through tools.execute:
- the gate decision is True for all four (untainted session);
- without a confirm handler the funnel refuses (kind not_confirmed)
  and the stored record survives;
- with a denying handler the funnel refuses and the record survives;
- with an approving handler the handler runs and the record is gone;
- previously confirm-gated tools are unaffected (set membership +
  gate decision for a sample, including the other deletes).

Store isolation: planner paths are patched to temp files; snippets and
window layouts resolve core.config.DATA_DIR lazily, so DATA_DIR is
patched to the temp dir for the whole test. The real data/ tree is
never touched by these tests.

Stdlib unittest only. Run via tests/run_tests.py.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from core import confirm_gate
    from core.brain import planner  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
    from core.brain import planner_checkpoint, planner_plans, planner_routines  # INTENTIONAL (skills split B41): impl modules for dual-patching (see setUp notes below)
    from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
    from core import config as config_mod
    from core.config import config as app_config
    from core import injection
    from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"delete-guard deps unavailable: {exc}")

DELETE_TOOLS = (
    ("delete_plan", {"plan_id": "plan-1"}),
    ("delete_routine", {"name": "morning"}),
    ("delete_snippet", {"name": "note"}),
    ("delete_window_layout", {"name": "Desk"}),
)

# Previously confirm-gated tools (incl. the other deletes): membership
# and gate decision must be unchanged by the wiring item.
PREVIOUSLY_GATED = (
    ("run_command", {"command": "echo hi"}),
    ("send_email", {}),
    ("write_file", {"filepath": "a", "content": "b"}),
    ("file_control", {}),
    ("empty_recycle_bin", {}),
    ("uninstall_app", {}),
    ("finance_playbook_delete", {}),
    ("social_entry_delete", {}),
    ("continuous_delete_data", {}),
    ("access_record_preference", {}),
)


def _message(envelope):
    """Best-effort handler message out of an execute() envelope."""
    if isinstance(envelope, dict):
        value = envelope.get("result", envelope)
        if isinstance(value, str):
            return value
        return json.dumps(value, default=str)
    return str(envelope)


class DeleteGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)
        # Seed one record per store.
        (self._dir / "plans.json").write_text(json.dumps([
            {"id": "plan-1", "goal": "Test goal", "steps": []},
        ]), encoding="utf-8")
        (self._dir / "routines.json").write_text(json.dumps([
            {"name": "morning", "actions": []},
        ]), encoding="utf-8")
        (self._dir / "snippets.json").write_text(json.dumps({
            "note": {"text": "keep me", "saved_at": "2026-01-01T00:00:00"},
        }), encoding="utf-8")
        (self._dir / "window_layouts.json").write_text(json.dumps({
            "Desk": {"saved_at": "2026-01-01T00:00:00", "windows": [
                {"title": "Notepad", "left": 0, "top": 0,
                 "width": 800, "height": 600, "minimized": False},
            ]},
        }), encoding="utf-8")
        # Patch the stores (planner paths are import-time constants;
        # snippets/layouts resolve DATA_DIR lazily).
        self._orig_plans = planner._PLANS_PATH
        self._orig_routines = planner._ROUTINES_PATH
        self._orig_data_dir = config_mod.DATA_DIR
        planner._PLANS_PATH = self._dir / "plans.json"
        planner._ROUTINES_PATH = self._dir / "routines.json"
        # INTENTIONAL (skills split B41): _PLANS_PATH/_ROUTINES_PATH are
        # bound in the impl modules too (snapshot imports); patch every
        # binding with the same value, or moved callers would miss it.
        planner_plans._PLANS_PATH = planner._PLANS_PATH
        planner_checkpoint._PLANS_PATH = planner._PLANS_PATH
        planner_routines._ROUTINES_PATH = planner._ROUTINES_PATH
        config_mod.DATA_DIR = self._dir
        # Untainted session, speaker lock off (mirror of
        # test_needs_confirmation's setup).
        injection.clear_taint()
        self._orig_lock = getattr(app_config, "speaker_lock", False)
        app_config.speaker_lock = False
        self._orig_prov = gate.session_provenance()
        gate.set_session_provenance("user")
        confirm_gate.register_handler(None)

    def tearDown(self):
        confirm_gate.register_handler(None)
        planner._PLANS_PATH = self._orig_plans
        planner_plans._PLANS_PATH = self._orig_plans
        planner_checkpoint._PLANS_PATH = self._orig_plans
        planner._ROUTINES_PATH = self._orig_routines
        planner_routines._ROUTINES_PATH = self._orig_routines
        config_mod.DATA_DIR = self._orig_data_dir
        app_config.speaker_lock = self._orig_lock
        gate.set_session_provenance(self._orig_prov)
        injection.clear_taint()
        self._tmp.cleanup()

    def _store_has(self, filename, needle):
        text = (self._dir / filename).read_text(encoding="utf-8")
        return needle in text

    # -- gate set ---------------------------------------------------- #
    def test_four_deletes_are_in_confirm_gate_set(self):
        for name, _args in DELETE_TOOLS:
            self.assertIn(name, gate._CONFIRM_GATE_TOOLS, name)
        # 128 before wiring item 1 + exactly these 4.
        # Libwiring sweep: INTENTIONAL — 2 teach tools added
        # (132 -> 134): teach_record_sequence, teach_replay_sequence.
        # INTENTIONAL UPDATE (Phase 3 B2-remainder, 2026-11-02):
        # 141, not 134. The gate set grew by exactly the 7
        # confirm-gated §7 consumer tools (batch 1: menu_action,
        # file_dialog_action, system_surface_action, adapter_action;
        # batch 2: drag_drop_action, journal_action; batch 8:
        # context_menu_action). Proven: 134 on the B2-remainder
        # backup's pin, 141 live with all 7 present; the four
        # deletes' membership assertions above are unchanged.
        # INTENTIONAL UPDATE (wallpaper-revert batch, 2026-11-05):
        # 164, not 143 — the confirm-gate set grew with the tool
        # census (755 tools; the census is 164 confirm-gated).
        # Trail: 128→132→134→141→142→143→164. (Debug pass,
        # 2026-11-15: the pin had not been swept since the
        # wallpaper-revert batch; the live set is 164 and the
        # census agrees.)
        self.assertEqual(len(gate._CONFIRM_GATE_TOOLS), 166)  # INTENTIONAL — 143 -> 164: confirm-gate set growth with the census (debug pass, 2026-11-15).; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan (confirm-gated); fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling

    def test_four_deletes_are_not_free_by_design(self):
        # They used to sit in the "app-state" free class; a tool
        # cannot be both human-gated and free by design.
        for name, _args in DELETE_TOOLS:
            self.assertNotIn(name, gate._FREE_BY_DESIGN, name)

    def test_gate_decision_true_for_all_four(self):
        for name, args in DELETE_TOOLS:
            self.assertTrue(gate._needs_confirmation(name, args), name)

    def test_previously_gated_tools_unaffected(self):
        for name, args in PREVIOUSLY_GATED:
            self.assertIn(name, gate._CONFIRM_GATE_TOOLS, name)
            self.assertTrue(gate._needs_confirmation(name, args), name)

    # -- denied without approval ------------------------------------- #
    def test_refused_without_confirm_handler(self):
        store_files = {
            "delete_plan": ("plans.json", "plan-1"),
            "delete_routine": ("routines.json", "morning"),
            "delete_snippet": ("snippets.json", "note"),
            "delete_window_layout": ("window_layouts.json", "Desk"),
        }
        for name, args in DELETE_TOOLS:
            result = tools.execute(name, dict(args))
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            filename, needle = store_files[name]
            self.assertTrue(self._store_has(filename, needle),
                            f"{name} must not delete without approval")

    def test_refused_with_denying_handler(self):
        confirm_gate.register_handler(lambda name, summary: False)
        result = tools.execute("delete_plan", {"plan_id": "plan-1"})
        self.assertEqual(result["status"], "refused", result)
        self.assertEqual(result["kind"], "not_confirmed", result)
        self.assertTrue(self._store_has("plans.json", "plan-1"))

    # -- approved path works ----------------------------------------- #
    def test_approved_delete_removes_record(self):
        confirm_gate.register_handler(lambda name, summary: True)
        cases = (
            ("delete_plan", {"plan_id": "plan-1"}, "plans.json",
             "plan-1", "deleted"),
            ("delete_routine", {"name": "morning"}, "routines.json",
             "morning", "deleted"),
            ("delete_snippet", {"name": "note"}, "snippets.json",
             "note", "Deleted snippet"),
            ("delete_window_layout", {"name": "Desk"},
             "window_layouts.json", "Desk", "Deleted layout"),
        )
        for name, args, filename, needle, msg in cases:
            result = tools.execute(name, dict(args))
            self.assertNotEqual(result.get("status"), "refused", result)
            self.assertIn(msg, _message(result), result)
            self.assertFalse(self._store_has(filename, needle),
                             f"{name} must delete once approved")


if __name__ == "__main__":
    unittest.main()

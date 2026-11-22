"""RET-4 (2026-11-15): deep-integrations restraint — a RULE plus a
per-integration hardening checklist, NOT an integration build.

The tests pin the same shapes as gap_ret4.py / prove_ret4.py: the
restraint note exists in TOOLS_LOOPHOLES_AUDIT.md where tool-adding
batches are governed and names the five-surface cap, the
single-surface-batch rule, and the FROZEN status of integrations
outside the pick; the hardening checklist exists at the tree root,
covers items (a)–(e), and each item states what proof looks like;
no tool is added and the census is unchanged.

No surface is picked here — D-RET-1 is the owner's pick, owed —
and no surface work is proven in this batch.
"""

import sys
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent

AUDIT = TREE / "TOOLS_LOOPHOLES_AUDIT.md"
CHECKLIST = TREE / "SURFACE_HARDENING_CHECKLIST.md"
NOTE_MARKER = "RET-4-STANDING-RESTRAINT"


def _note():
    text = AUDIT.read_text(encoding="utf-8")
    start = text.index(NOTE_MARKER)
    # Collapse whitespace: the note wraps lines, so a phrase such as
    # "no new work" can straddle a line break in the file.
    return " ".join(text[start:start + 4000].split())


def _checklist():
    return CHECKLIST.read_text(encoding="utf-8")


class Ret4NoteTests(unittest.TestCase):
    def test_restraint_note_present(self):
        self.assertIn(NOTE_MARKER, AUDIT.read_text(encoding="utf-8"))

    def test_restraint_note_names_five_surface_cap(self):
        self.assertIn("at most five", _note())

    def test_restraint_note_names_single_surface_batch_rule(self):
        self.assertIn("single-surface", _note().lower())

    def test_restraint_note_names_frozen_status(self):
        note = _note()
        self.assertIn("FROZEN", note)
        self.assertIn("not removed", note)
        self.assertIn("no new work", note)
        self.assertIn("no new tools", note)

    def test_restraint_note_names_candidate_set_as_candidates_not_a_pick(self):
        note = _note()
        for name in ("Gmail", "Calendar", "files", "browser"):
            self.assertIn(name, note)
        self.assertIn("candidate", note.lower())
        self.assertIn("D-RET-1", note)
        self.assertIn("owner", note.lower())

    def test_restraint_note_names_enforcement(self):
        note = _note()
        self.assertIn("census pins", note)
        self.assertIn("standing note", note)

    def test_restraint_note_sits_where_tool_adding_batches_are_governed(self):
        text = AUDIT.read_text(encoding="utf-8")
        self.assertGreater(text.index(NOTE_MARKER),
                           text.index("AUDIT-TOTALS:"))

    def test_restraint_note_forbids_charging_logic(self):
        note = _note()
        self.assertIn("No charging logic anywhere", note)
        self.assertNotIn("$", note)


class Ret4ChecklistTests(unittest.TestCase):
    def test_checklist_exists(self):
        self.assertTrue(CHECKLIST.is_file())

    def test_checklist_covers_read_write_proven_separately(self):
        text = _checklist().lower()
        self.assertIn("read path", text)
        self.assertIn("write path", text)
        self.assertIn("proven separately", text)

    def test_checklist_covers_honest_refusal(self):
        text = _checklist().lower()
        self.assertIn("honest refusal", text)
        self.assertIn("reason", text)
        self.assertIn("silent wrong result", text)

    def test_checklist_covers_confirm_gated_stay_gated(self):
        text = _checklist().lower()
        self.assertIn("confirm-gated", text)
        self.assertIn("stay gated", text)

    def test_checklist_covers_live_service_or_named_standin(self):
        text = _checklist().lower()
        self.assertIn("live service", text)
        self.assertIn("stand-in", text)
        self.assertIn("recorded real record", text)

    def test_checklist_covers_cost_ledger_visibility(self):
        text = _checklist().lower()
        self.assertIn("cost ledger", text)
        self.assertIn("ledger-recorded", text)

    def test_checklist_each_item_states_what_proof_looks_like(self):
        text = _checklist()
        self.assertGreaterEqual(text.count("What proof looks like"), 5)

    def test_checklist_sequencing_one_surface_at_a_time_owners_pick(self):
        text = _checklist().lower()
        self.assertIn("one surface at a time", text)
        self.assertIn("owner", text)
        self.assertIn("pick", text)
        self.assertIn("does not choose", text)

    def test_checklist_does_not_pick_a_surface(self):
        text = _checklist()
        self.assertIn("D-RET-1", text)
        self.assertNotIn("the first surface is", text.lower())

    def test_checklist_forbids_charging_logic(self):
        self.assertNotIn("$", _checklist())


class Ret4CensusTests(unittest.TestCase):
    def test_no_tool_added_registry_census(self):
        sys.path.insert(0, str(TREE))
        from core.funnel.tools_registry import tools as registry
        decls = registry.get_declarations()
        total = len(decls)
        params = sum(len(d.get("parameters", {}).get("properties", {}))
                     for d in decls)
        self.assertEqual(total, 769)  # INTENTIONAL; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169 — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(params, 2121)  #Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_audit_totals_pinned(self):
        text = AUDIT.read_text(encoding="utf-8")
        for fragment in ('"total": 769', '"read": 384', '"effectful": 385',  # INTENTIONAL — field-check fix #32 (2026-11-18): 755/373 -> 756/374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756/374 -> 757/375 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
                         '"confirm_gated": 169', '"seam_wired": 44',  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
                         '"already_verified_in_handler": 319',  # INTENTIONAL — field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
                         '"honestly_unverifiable": 22',
                         '"next_pass_pool": 0'):
            self.assertIn(fragment, text)


if __name__ == "__main__":
    unittest.main()

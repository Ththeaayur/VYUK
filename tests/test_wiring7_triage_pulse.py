"""Wiring Build 7 — edition-leaf triage + Pulse naming disambiguation.

Label tests read GUI source (no Qt import, so they run in the lab).
The triage-report test reads the report only if it is present at the
layout-relative path; in a shipped tree without the report it skips,
because the report lives outside the shipped tree and must never be a
ship blocker. Phase 2 renamed 234 modules (see
refactor_phase2_rename_plan.md), so this file's original "no module
was renamed" snapshot statement is superseded — EXCEPT for pulse_w5
itself, which Phase 2 HELD at its old name pending its own decision
(plan §9.11, triage-pulse-suite review 2026-11-02). What this file
still pins, unchanged: no pulse_w5 card was wired into the Pulse page.
"""

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
TREE = HERE.parent.parent
# Report lives in code-lab/: tests/ -> tree root -> imported/voice-ai-assistant
# -> imported/ -> code-lab/ (five .parent steps from the test file).
REPORT = HERE.parent.parent.parent.parent.parent / "wiring_triage.md"

# LEAVES enumerates the HISTORICAL triage-report rows (wiring_triage.md,
# 2026-10-26) that test_report_covers_all_64_leaves checks — it is NOT a
# list of current module files, so its entries are deliberately NOT renamed
# when a leaf module renames (Phase 2 B1a-string-only, 2026-11-02: renaming
# an entry here would fail the report test, because the historical report
# lists the old names and historical records are never rewritten).
LEAVES = (
    # Accessibility (10)
    "ac_caption_v579", "ac_capture_guard_v579", "ac_cognitive_v579",
    "ac_contrast_v579", "ac_dignity_v579", "ac_gaze_v579", "ac_nvda_v579",
    "ac_select_v579", "ac_speech_v579", "ac_start_v579",
    # Business (9)
    "bz19_entitlement_v572", "bz1_deploy_v572", "bz3_workplace_v572",
    "bz48_consent_v572", "bz6_counters_v572", "bz6_presidio_v572",
    "bz6_sqlcipher_v572", "bz6_trust_pack_v572", "bz76_identity_v572",
    # Creative (8)
    "cr_drive_v576", "cr_edit_v576", "cr_ledger_v576", "cr_marking_v576",
    "cr_music_v576", "cr_openverse_v576", "cr_render_v576", "cr_write_v576",
    # Developer (7)
    "dv_codebase_v577", "dv_debug_v577", "dv_doctor_v577", "dv_drive_v577",
    "dv_flake_v577", "dv_funnel_v577", "dv_serial_v577",
    # Finance/Life (5)
    "fl_consent_v574", "fl_donate_v574", "fl_maths_v574", "fl_playbook_v574",
    "fl_redact_v574",
    # Gaming (7)
    "gm_analysis_v578", "gm_capture_v578", "gm_deals_v578", "gm_detect_v578",
    "gm_guard_v578", "gm_macros_v578", "gm_mods_v578",
    # Social (13)
    "so_crisis_v573", "so_dates_v573", "so_drafting_v573", "so_graph_v573",
    "so_honesty_v573", "so_ledger_v573", "so_logistics_v573",
    "so_register_v573", "so_relational_v573", "so_rituals_v573",
    "so_telegram_v573", "so_tiers_v573", "so_vcard_v573",
    # Study (5)
    "st_cite", "st_deck", "st_practice", "st_schedule", "st_workspace",
)


def _read(rel):
    return (TREE / rel).read_text(encoding="utf-8")


class TestPulseLabels(unittest.TestCase):
    def test_sidebar_label_is_system_pulse(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        shell = _read("gui/window_shell_sidebar.py")
        # INTENTIONAL (S1, 2026-10-30): the old enumerate loop
        # is gone — S1 builds sidebar buttons from _nav_defs
        # (Home/Activity/Memory + Advanced + footer Settings).
        # The label assertion moves to that definition: the
        # button label is "System Pulse", never bare "Pulse".
        start = shell.index("_nav_defs = [")
        end = shell.index("]\n", start)
        nav_defs = shell[start:end]
        self.assertIn('"System Pulse"', nav_defs)
        self.assertNotIn('"Pulse",', nav_defs)

    def test_page_shell_title_disambiguates(self):
        # GUI split #8 INTENTIONAL: pulse builder moved verbatim to
        # window_pages_system.py. Assertion unchanged.
        pages = _read("gui/window_pages_system.py")
        self.assertIn('"System Pulse"', pages)
        self.assertIn("system probes only", pages)
        self.assertIn("not news or entity cards", pages)
        self.assertIn("System Pulse is unavailable on this machine.", pages)

    def test_pulse_page_footnote_disambiguates(self):
        pulse = _read("gui/pulse_page.py")
        self.assertIn("System probes only", pulse)
        self.assertIn("no news or entity cards here", pulse)

    def test_pulse_module_held_and_no_card_data_wired(self):
        # INTENTIONAL (§9.11 triage-pulse-suite review, 2026-11-02):
        # renamed from test_no_module_renamed_and_no_card_data_wired.
        # The old name-half ("no module was renamed") is SUPERSEDED —
        # Phase 2 renamed 234 modules, so this test no longer asserts
        # tree-wide name stability. What it pins, unchanged: pulse_w5
        # itself is HELD at its old name (file exists), and GUI still
        # does not import pulse_w5, so no entity card reaches the page.
        # The scan uses the CURRENT name because no rename happened;
        # if pulse_w5 is ever renamed, this scan must move to the new
        # name in the same move.
        self.assertTrue((TREE / "core/pulse_w5.py").exists())
        self.assertTrue((TREE / "gui/pulse_page.py").exists())
        # GUI split #8 INTENTIONAL: the pulse builder moved verbatim to
        # window_pages_system.py; the no-pulse_w5-import check follows
        # it. Assertion unchanged.
        for rel in ("gui/pulse_page.py", "gui/window_pages.py",
                    "gui/window_pages_system.py",
                    "gui/window_shell.py"):
            src = _read(rel)
            imports = [ln for ln in src.splitlines()
                       if "pulse_w5" in ln and ("import" in ln)]
            self.assertEqual(imports, [], f"{rel} must not import pulse_w5")


class TestTriageReport(unittest.TestCase):
    def test_report_covers_all_64_leaves(self):
        if not REPORT.exists():
            self.skipTest("triage report not present at layout path")
        text = REPORT.read_text(encoding="utf-8")
        rows = re.findall(
            r"\| `([a-z0-9_]+)` \| (WIRE|KEEP-AS-BENCH|PARK) \|", text)
        found = {name: cls for name, cls in rows}
        self.assertEqual(len(LEAVES), 64)
        for leaf in LEAVES:
            self.assertIn(leaf, found, f"{leaf} missing from triage")
        self.assertEqual(len(found), 64)
        counts = {c: list(found.values()).count(c) for c in
                  ("WIRE", "KEEP-AS-BENCH", "PARK")}
        self.assertEqual(counts["WIRE"], 18)
        self.assertEqual(counts["KEEP-AS-BENCH"], 16)
        self.assertEqual(counts["PARK"], 30)
        # Every row carries a reason (a non-empty third cell).
        for name in LEAVES:
            line = [ln for ln in text.splitlines()
                    if f"`{name}`" in ln and "|" in ln]
            self.assertTrue(line and len(line[0].split("|")) >= 4
                            and line[0].split("|")[3].strip(),
                            f"{name} has no reason")

    def test_no_leaf_wired_in_this_build_claim(self):
        # This build wires no leaf: the report must say so plainly,
        # and no WIRE leaf may have gained a GUI caller in this build.
        if not REPORT.exists():
            self.skipTest("triage report not present at layout path")
        text = REPORT.read_text(encoding="utf-8")
        self.assertIn("Wired in this build: NONE of the 64", text)


if __name__ == "__main__":
    unittest.main()

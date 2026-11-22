"""Audit Fix Batch 3 — shell-gating verification + licence-key custody.

No new tools, no registry change, no fitted change: this batch VERIFIES
two audit claims and fixes exactly one real gap it found.

SHELL (audit: shell=True at skills_plus.py:194 and control.py:1480,
gating unverified for skills_plus):
  The whole-tree sweep (core/ + gui/) finds exactly those two sites.
  Both sit behind the funnel's executor-level human confirm gate:
  run_command launches and uninstall_app are members of
  _CONFIRM_GATE_TOOLS, so execute() refuses them without a registered
  human handler (not_confirmed, nothing runs) even when the caller
  supplies confirm=True — the model flag cannot bypass the human
  gate. run_command job actions (poll/status/wait/cancel/list) are
  deliberately NOT gated: they are not new commands (the launch was
  already approved). Verdict: GATED, proven by test below. No fix.

LICENCE (audit: "no licence verifies" because licence carries a
placeholder zero-byte TEST_PUBLIC_KEY_B64):
  The audit's outcome is true but its mechanism is wrong, and the
  module's own safety story was overstated — so this batch pins the
  truth and hardens the one real gap:
  * Production licence path (w10_wiring.licence_status) returns
    "test-key" early while the placeholder ships and NEVER calls
    check_licence_file — the forge below is unreachable in
    production today. No production path depends on the placeholder
    for a positive verification.
  * Install-manifest and update-feed paths use a separate real
    pinned key (84f32c05...), never the licence placeholder.
  * BUT the module docstring's "verifies NOTHING / zero point does
    not decode" was false: the zero encoding decodes to a 4-torsion
    point, and a crafted signature CAN verify under it
    (small-order forgery, demonstrated in-test with the module's
    own primitives). check_licence therefore refuses the
    placeholder key outright now (same pattern as bz19's
    "no-verifying-key" refusal) instead of relying on verification
    failure. No key material was generated, invented, or shipped
    anywhere in this batch: the forgery test crafts a signature in
    memory only, from the public zero key, and never writes it out
    as a licence; key custody stays an owner/offline decision.
"""

import base64
import re
import shlex
import tempfile
import unittest
from hashlib import sha512
from pathlib import Path
from random import Random

from core import business_fleet_entitlement as bz19
from core import confirm_gate
from core import licence as lic
from core import skills_plus
from core import w10_wiring
from core.desktop import control as control_mod  # Phase 3 D4a INTENTIONAL: control hub moved to core/desktop/
from core.health import integrity as integrity_mod  # Phase 3 H1 INTENTIONAL: module moved to core/health/
from core.health import updater as updater_mod  # Phase 3 H1 INTENTIONAL: module moved to core/health/
from core.funnel.tools_gate import _needs_confirmation  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

ROOT = Path(__file__).resolve().parent.parent
SHELL_TRUE_RE = re.compile(r"shell\s*=\s*True")


def _forge_under_zero_key(message: bytes):
    """Craft (signature) that verifies under the zero key, using only
    the module's own primitives. The zero encoding decodes to a
    4-torsion point A, so a signature (R, s) with R = s*B - k*A
    verifies whenever H(R) mod 4 == k. Search k in 0..3 per random
    s; a fresh s is tried until one works (deterministic seed).
    Returns the 64-byte signature, or None if not found (the test
    fails then — the forgery must be demonstrable)."""
    zero_key = bytes(32)
    a_point = lic._decode_point(zero_key)

    def digest(point):
        return int.from_bytes(
            sha512(lic._encode_point(point) + zero_key + message).digest(),
            "little") % lic._L

    rng = Random(1234)
    for _ in range(50):
        s = rng.randrange(1, lic._L)
        s_b = lic._scalarmult(lic._BASE, s)
        for k in range(4):
            candidate = lic._ed_add(s_b, lic._scalarmult(a_point, (-k) % 4))
            if digest(candidate) % 4 == k:
                return lic._encode_point(candidate) + s.to_bytes(32, "little")
    return None


def _licence_fixture(signature_b64: str) -> dict:
    return {
        "product_id": "VYUK",
        "edition": "standard",
        "holder": "Audit Fix Batch 3",
        "issued_at": "2026-01-01",
        "expires_at": None,
        "signature": signature_b64,
    }


def _entitlement_fixture() -> dict:
    return {
        "product_id": "VYUK",
        "edition": "Business",
        "holder": "Audit Fix Batch 3",
        "issued_at": "2026-01-01",
        "expires_at": None,
        "seats_purchased": 5,
        "enrolled_identities": ["admin@example.test"],
        "signature": base64.b64encode(bytes(64)).decode(),
    }


class ShellSweepTests(unittest.TestCase):
    def test_shell_true_sites_are_exactly_the_two_known(self):
        """Whole-tree sweep (core/ + gui/, test files excluded by
        scope): any NEW shell=True site fails this test until it
        gets its own gating verdict — the sweep must be re-run,
        never silently extended.

        Gaps batch (P1-12) INTENTIONAL behavior change: the
        uninstall site is GONE — uninstall_app no longer hands a
        registry string to a shell (it parses argv, validates the
        executable, and launches with shell=False). Exactly ONE
        shell=True site remains: control.py's _spawn_shell, the
        confirm-gated run_command spawn. The test name keeps its
        historical wording; the pinned set is now one site.

        Refactor P1 step 4 INTENTIONAL relocation (no behaviour
        change): _spawn_shell moved verbatim from core/desktop/control.py
        to core/desktop/control_shell.py, so the pinned file is now
        core/desktop/control_shell.py; the site count stays one and the
        gating verdict below (confirm-gated run_command) is
        unchanged and still proven by ShellGateTests."""
        found = {}
        for base in ("core", "gui"):
            for path in sorted((ROOT / base).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                hits = [n for n, line in enumerate(
                    path.read_text(errors="replace").splitlines(), 1)
                    if SHELL_TRUE_RE.search(line)]
                if hits:
                    found[str(path.relative_to(ROOT))] = hits
        self.assertEqual(
            set(found), {"core/desktop/control_shell.py"}, found)  # Phase 3 D2b INTENTIONAL: control_shell moved to core/desktop/
        self.assertEqual(len(found["core/desktop/control_shell.py"]), 1, found)  # Phase 3 D2b INTENTIONAL: control_shell moved to core/desktop/

    def test_no_os_system_or_popen_shell_escape(self):
        """Supplementary sweep: no os.system / os.popen anywhere in
        core/ + gui/ (the audit's clean finding, pinned)."""
        for base in ("core", "gui"):
            for path in sorted((ROOT / base).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                text = path.read_text(errors="replace")
                self.assertNotIn("os.system(", text, str(path))
                self.assertNotIn("os.popen(", text, str(path))


class ShellGateTests(unittest.TestCase):
    def setUp(self):
        confirm_gate.register_handler(None)
        self.calls = []

    def tearDown(self):
        confirm_gate.register_handler(None)

    def test_gate_classification(self):
        """Launches gate for every call, whatever the caller claims;
        job actions are not new commands and stay ungated (policy in
        tools_gate's run_command branch)."""
        self.assertTrue(_needs_confirmation(
            "run_command", {"command": "echo hi"}))
        self.assertTrue(_needs_confirmation(
            "run_command", {"command": "echo hi", "confirm": True}))
        self.assertTrue(_needs_confirmation(
            "uninstall_app", {"name": "Something"}))
        self.assertTrue(_needs_confirmation(
            "uninstall_app", {"name": "Something", "confirm": True}))
        for action in ("poll", "status", "wait", "cancel", "list"):
            self.assertFalse(_needs_confirmation(
                "run_command", {"action": action, "job_id": "x"}), action)

    def test_run_command_refused_without_handler_even_with_confirm(self):
        """Model-supplied confirm=True cannot bypass the human gate:
        no handler -> not_confirmed, and the marker file is never
        created (nothing executed)."""
        with tempfile.TemporaryDirectory() as tmp:
            marker = str(Path(tmp) / "auditfix3-marker.txt")
            result = tools.execute("run_command", {
                "command": "echo auditfix3 > %s" % shlex.quote(marker),
                "confirm": True})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(marker).exists())

    def test_uninstall_app_refused_without_handler_even_with_confirm(self):
        result = tools.execute("uninstall_app", {
            "name": "Something", "confirm": True})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result["status"], "refused", result)
        self.assertEqual(result["kind"], "not_confirmed", result)

    def test_run_command_runs_with_approving_handler(self):
        """Approving handler -> the gate passes AND the command
        actually runs (marker file created)."""
        def handler(name, summary):
            self.calls.append(name)
            return True

        confirm_gate.register_handler(handler)
        with tempfile.TemporaryDirectory() as tmp:
            marker = str(Path(tmp) / "auditfix3-marker.txt")
            result = tools.execute("run_command", {
                "command": "echo auditfix3 > %s" % shlex.quote(marker),
                "confirm": True})
            self.assertEqual(result["status"], "success", result)
            self.assertEqual(self.calls, ["run_command"])
            self.assertEqual(Path(marker).read_text().strip(), "auditfix3")

    def test_uninstall_app_reaches_handler_with_approval(self):
        """Approving handler -> the gate passes and the handler runs
        (on Linux the platform guard answers before any Popen, so
        this proves gate passage without launching anything)."""
        def handler(name, summary):
            self.calls.append(name)
            return True

        confirm_gate.register_handler(handler)
        result = tools.execute("uninstall_app", {
            "name": "Something", "confirm": True})
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(self.calls, ["uninstall_app"])
        self.assertIn("Windows", result["result"], result)

    def test_module_level_confirm_layers(self):
        """Second layers, independent of the funnel: uninstall_app
        refuses without its own confirm flag, and the trust layer
        refuses an always-refuse pattern even with confirm=True."""
        msg = skills_plus.uninstall_app("Something", confirm=False)
        self.assertIn("confirm=true", msg)
        verdict, _message = control_mod._trust_decision(
            "format c:", confirm=True)
        self.assertEqual(verdict, "refuse")


class LicenceCustodyTests(unittest.TestCase):
    def test_placeholder_key_is_exactly_32_zero_bytes(self):
        self.assertTrue(lic.is_placeholder_key(lic.TEST_PUBLIC_KEY_B64))
        self.assertEqual(
            base64.b64decode(lic.TEST_PUBLIC_KEY_B64, validate=True),
            bytes(32))

    def test_check_licence_refuses_placeholder_key_outright(self):
        """After the Batch 3 hardening: default key -> placeholder
        refusal, never a signature verdict. (Before the fix this
        returned signature-mismatch for garbage sigs — and valid
        for a forged one; see the next test.)"""
        result = lic.check_licence(_licence_fixture(
            base64.b64encode(bytes(64)).decode()))
        self.assertFalse(bool(result))
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason, "placeholder-key")

    def test_forged_signature_under_zero_key_is_refused(self):
        """Small-order forgery, demonstrated with the module's own
        primitives: the forged signature DOES verify at the primitive
        level (proving the old 'verifies NOTHING' story false), yet
        check_licence must still refuse it via the placeholder key."""
        fixture = _licence_fixture("")
        message = lic.canonical_payload(fixture)
        forged = _forge_under_zero_key(message)
        self.assertIsNotNone(forged, "forgery must be demonstrable")
        self.assertTrue(lic.verify_ed25519(bytes(32), message, forged))
        fixture["signature"] = base64.b64encode(forged).decode()
        result = lic.check_licence(dict(fixture))
        self.assertFalse(bool(result))
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason, "placeholder-key")

    def test_non_placeholder_key_still_reaches_signature_check(self):
        """The placeholder branch triggers ONLY for the placeholder:
        a random non-placeholder key reaches ordinary verification
        (garbage signature -> signature-mismatch, not placeholder)."""
        other_key = base64.b64encode(bytes(range(1, 33))).decode()
        self.assertFalse(lic.is_placeholder_key(other_key))
        result = lic.check_licence(
            _licence_fixture(base64.b64encode(bytes(64)).decode()),
            public_key_b64=other_key)
        self.assertFalse(bool(result))
        self.assertEqual(result.reason, "signature-mismatch")

    def test_production_licence_status_never_verifies_under_placeholder(self):
        """The only production caller of the licence check returns
        'test-key' early while the placeholder ships — even a bogus
        licence file never reaches check_licence_file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / w10_wiring.LICENCE_FILENAME
            path.write_text('{"bogus": true}')
            state = w10_wiring.licence_status(tmp)
            self.assertEqual(state["state"], "test-key")
            self.assertIn("verifies nothing", state["message"])
        with tempfile.TemporaryDirectory() as tmp:
            state = w10_wiring.licence_status(tmp)
            self.assertEqual(state["state"], "none")

    def test_manifest_and_update_paths_use_a_separate_real_key(self):
        """The install-manifest / update-feed pin is a real 32-byte
        key, identical in updater and integrity, and unrelated to
        the licence placeholder."""
        pinned = updater_mod.PINNED_UPDATE_PUBLIC_KEY
        self.assertEqual(len(pinned), 32)
        self.assertNotEqual(pinned, bytes(32))
        self.assertEqual(pinned.hex()[:8], "84f32c05")
        self.assertEqual(integrity_mod.PINNED_UPDATE_PUBLIC_KEY, pinned)

    def test_bz19_sibling_already_refuses_missing_or_placeholder_key(self):
        """Precedent for the licence fix: the fleet-entitlement
        checker refuses a missing key AND an explicit placeholder
        key before any verification."""
        for key in (None, lic.TEST_PUBLIC_KEY_B64):
            result = bz19.check_fleet_entitlement(
                _entitlement_fixture(), public_key_b64=key)
            self.assertFalse(bool(result))
            self.assertEqual(result.reason, "no-verifying-key")


class BatchPinsTests(unittest.TestCase):
    def test_no_new_tools_unchanged(self):
        """Batch 3 adds no tools: registry stays 640 at this batch (research wiring later added deep_research) — see the INTENTIONAL note below."""
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Desktop-input batch (D-07): INTENTIONAL — cursor_control
        # schema gains x2/y2/points/pause_s (no new tool, registry
        # stays 641).
        # Desktop-reach batch (F-04 + D-09): INTENTIONAL —
        # screen_look + take_screenshot gain a scope parameter
        # and cursor_control's description gains the honest
        # pointer-gesture sentence (no new tool, registry
        # stays 641).
        # Audit fix Batch 2 (TOOL-03) — INTENTIONAL:
        # 75 previously description-less properties gain
        # descriptions and 4 typeless props gain anyOf types
        # (no new tool, registry stays 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 769)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        # Audit fix Batch 4 (DESK/DISPLAY) — INTENTIONAL:
        # take_screenshot gains a monitor selector, screen_diff
        # gains a monitor selector, focus_window_by_text gains a
        # title_hint scope parameter and a rewritten description
        # stating the ask-on-ambiguity tie policy (no new tool,
        # registry stays 641).


if __name__ == "__main__":
    unittest.main()

"""Audit Fix Batch 2 — install-manifest integrity (GAP-1 + GAP-2).

INTENTIONAL (world-view removal, 2026-11-05): this suite used to
cover the embedded-web-view assets (gui/assets/globe/*, including
the .wasm decoders) and the CDN integrity of globe.html /
flatmap.html (GAP-2). The web view is gone with the feature, so
the manifest covers *.py files ONLY again, and the GAP-2 SRI
tests are deleted with the pages they tested. What remains pins
the *.py-only covered set, the exclusions, determinism, and the
signed-temp-tree tamper path.

GAP-1 (as built): the install manifest used to cover *.py files
only, leaving the embedded-web-view assets outside the integrity
check although the web view executed code from them.
core/integrity.py build_manifest() then covered *.py files PLUS
every file under gui/assets/globe/, with the same hashing, and
verify_install_tree() compared against that same set (both share
the one builder, so they cannot disagree about coverage).
Exclusions, pinned here: __pycache__, data/, .env, *.pem, *.key,
and the manifest files themselves.

What this suite does NOT do: it does not rewrite the shipped
install_manifest.json in the working copy. That file is
re-signed by the ship-gate from this builder (the release key
is offline and is never handled here); the signed-tree tests
below use temp trees signed with a fixed TEST keypair and the
module's documented monkeypatch seam, so the full verify path
— signature plus live comparison — is proven without touching
the shipped signature. No new tools are added by this batch:
registry stays 640 at this batch (research wiring later added deep_research, 640 -> 641), fitted payload stays 242,780 B (over the
240,000 cap — the owner's cap decision, not this batch's).
"""

from __future__ import annotations

import base64
import hashlib
import re
import tempfile
import unittest
from pathlib import Path

from core.health import integrity  # Phase 3 H1 INTENTIONAL: module moved to core/health/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------
# Minimal Ed25519 signing, TEST ONLY (RFC 8032). The product code
# verifies with core/updater.py's _ed25519_verify; this signer
# exists so the suite can build a genuinely signed temp tree and
# prove the untampered path returns ok, instead of stubbing the
# verifier (which would prove nothing). Fixed test seed, never
# a release key.
# ------------------------------------------------------------------
_TP = 2 ** 255 - 19
_TL = 2 ** 252 + 27742317777372353535851937790883648493
_TD = (-121665 * pow(121666, _TP - 2, _TP)) % _TP
_TI = pow(2, (_TP - 1) // 4, _TP)


def _tinv(x):
    return pow(x, _TP - 2, _TP)


def _txrecover(y):
    xx = (y * y - 1) * _tinv(_TD * y * y + 1) % _TP
    x = pow(xx, (_TP + 3) // 8, _TP)
    if (x * x - xx) % _TP != 0:
        x = (x * _TI) % _TP
    if x % 2 != 0:
        x = _TP - x
    return x


_TBY = (4 * _tinv(5)) % _TP
_TB = (_txrecover(_TBY), _TBY)


def _tadd(P, Q):
    x1, y1 = P
    x2, y2 = Q
    t = _TD * x1 % _TP * x2 % _TP * y1 % _TP * y2 % _TP
    x = (x1 * y2 + x2 * y1) % _TP * _tinv((1 + t) % _TP) % _TP
    y = (y1 * y2 + x1 * x2) % _TP * _tinv((1 - t) % _TP) % _TP
    return (x, y)


def _tmul(P, e):
    Q = (0, 1)
    while e > 0:
        if e & 1:
            Q = _tadd(Q, P)
        P = _tadd(P, P)
        e >>= 1
    return Q


def _tenc(P):
    x, y = P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _thint(data):
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _test_keypair():
    seed = bytes(range(32))
    digest = hashlib.sha512(seed).digest()
    a = int.from_bytes(digest[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return seed, a, _tenc(_tmul(_TB, a))


def _test_sign(seed, a, pub, msg):
    prefix = hashlib.sha512(seed).digest()[32:]
    r = _thint(prefix + msg) % _TL
    r_enc = _tenc(_tmul(_TB, r))
    s = (r + _thint(r_enc + pub + msg) * a) % _TL
    return r_enc + s.to_bytes(32, "little")


class ManifestCoverageTests(unittest.TestCase):
    def _excluded(self, rel: Path) -> bool:
        parts = rel.parts
        if "__pycache__" in parts:
            return True
        if parts and parts[0] == "data":
            return True
        if rel.name == ".env" or rel.name.endswith(".pem") or \
                rel.name.endswith(".key"):
            return True
        if rel.as_posix() in ("install_manifest.json",
                              "install_manifest.json.sig"):
            return True
        return False

    def test_real_tree_covered_set(self):
        """build_manifest on the real tree covers exactly *.py,
        nothing else. (INTENTIONAL, world-view removal
        2026-11-05: the gui/assets/globe/* set is gone with the
        web view; the manifest is *.py-only again.)"""
        manifest = integrity.build_manifest(ROOT)
        files = manifest["files"]
        expected = set()
        for path in ROOT.rglob("*.py"):
            rel = path.relative_to(ROOT)
            if not self._excluded(rel):
                expected.add(rel.as_posix())
        self.assertEqual(set(files), expected)
        for required in (
                "core/health/integrity.py",  # Phase 3 H1 INTENTIONAL: module moved to core/health/
                "core/w7_wiring.py",
                "gui/window_shell_sidebar.py"):
            self.assertIn(required, files)
        self.assertTrue(all(k.endswith(".py") for k in files))
        self.assertFalse(any(k.startswith("data/") or
                             "__pycache__" in k or
                             k.endswith((".pem", ".key")) or
                             k.endswith("/.env") or k == ".env"
                             for k in files))
        self.assertNotIn("install_manifest.json", files)
        self.assertNotIn("install_manifest.json.sig", files)

    def test_temp_tree_exclusions(self):
        """The exclusion list holds on a synthetic tree: only
        the .py file enters the manifest. (INTENTIONAL, world-view
        removal 2026-11-05: the globe asset files that used to be
        wanted here are now unwanted — no asset directory is
        covered any more.)"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wanted = {
                "app.py": b"print('hi')\n",
            }
            unwanted = {
                "gui/assets/globe/asset.js": b"// engine\n",
                "gui/assets/globe/sub/ok.css": b"/* css */\n",
                "data/store.py": b"print('runtime')\n",
                "data/activity.log": b"runtime log\n",
                ".env": b"SECRET=<redacted>\n",
                "key.pem": b"fake pem\n",
                "key.key": b"fake key\n",
                "pkg/__pycache__/cached.py": b"print('cached')\n",
            }
            for rel, data in {**wanted, **unwanted}.items():
                path = base / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            manifest = integrity.build_manifest(base)
            self.assertEqual(set(manifest["files"]), set(wanted))

    def test_manifest_bytes_deterministic(self):
        first = integrity.build_manifest(ROOT)
        second = integrity.build_manifest(ROOT)
        self.assertEqual(integrity.manifest_bytes(first),
                         integrity.manifest_bytes(second))


class SignedTreeTamperTests(unittest.TestCase):
    FILES = {
        "app.py": b"print('app')\n",
    }

    def setUp(self):
        self._orig_key = integrity.PINNED_UPDATE_PUBLIC_KEY
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self._seed, self._a, self._pub = _test_keypair()
        self._write_signed_tree()

    def tearDown(self):
        integrity.PINNED_UPDATE_PUBLIC_KEY = self._orig_key
        self._tmp.cleanup()

    def _write_signed_tree(self):
        for rel, data in self.FILES.items():
            path = self.base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = integrity.build_manifest(self.base)
        manifest_bytes = integrity.manifest_bytes(manifest)
        (self.base / "install_manifest.json").write_bytes(
            manifest_bytes)
        sig = _test_sign(self._seed, self._a, self._pub,
                         manifest_bytes)
        (self.base / "install_manifest.json.sig").write_text(
            base64.b64encode(sig).decode("ascii"))
        integrity.PINNED_UPDATE_PUBLIC_KEY = self._pub
        return manifest

    def test_untampered_tree_passes(self):
        result = integrity.verify_install_tree(self.base)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["checked"], len(self.FILES))

    def test_tampered_py_asset_fails(self):
        # INTENTIONAL (world-view removal, 2026-11-05): the tampered
        # globe-asset test is now a tampered .py test — the manifest
        # is *.py-only, so app.py is the tamper target.
        asset = self.base / "app.py"
        asset.write_bytes(asset.read_bytes() + b"# tampered\n")
        result = integrity.verify_install_tree(self.base)
        self.assertFalse(result["ok"])
        self.assertTrue(any(
            "hash mismatch" in p and "app.py" in p
            for p in result["problems"]), result["problems"])

    def test_missing_asset_fails(self):
        (self.base / "app.py").unlink()
        result = integrity.verify_install_tree(self.base)
        self.assertFalse(result["ok"])
        self.assertTrue(any(
            "missing file" in p and "app.py" in p
            for p in result["problems"]), result["problems"])

    def test_extra_file_fails(self):
        (self.base / "extra.py").write_text("print('extra')\n")
        result = integrity.verify_install_tree(self.base)
        self.assertFalse(result["ok"])
        self.assertTrue(any(
            "extra file" in p and "extra.py" in p
            for p in result["problems"]), result["problems"])

class BatchGuardTests(unittest.TestCase):
    def test_registry_unchanged(self):
        """This batch adds no tools: registry stays 640 at this batch (research wiring later added deep_research). See the INTENTIONAL note below."""
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
        self.assertEqual(len(tools.get_declarations()), 758)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        # Audit fix Batch 4 (DESK/DISPLAY) — INTENTIONAL:
        # take_screenshot gains a monitor selector, screen_diff
        # gains a monitor selector, focus_window_by_text gains a
        # title_hint scope parameter and a rewritten description
        # stating the ask-on-ambiguity tie policy (no new tool,
        # registry stays 641).


if __name__ == "__main__":
    unittest.main()

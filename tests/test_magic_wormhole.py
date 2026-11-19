"""R8-5 magic-wormhole — code-phrase encrypted file transfer between two
machines on the same local network, as VYUK's own code (PORT, not adopt:
the upstream library pulls Twisted + autobahn + txtorcon + spake2 +
pynacl, a whole async framework the tree has never used; the crypto here
is the tree's own HMAC-CTR + encrypt-then-MAC construction from
core/secrets_store.py, stdlib only, no new dependency, requirements.txt
untouched). Pins: the phrase shape, the crypto round-trip + tamper
refusal, the offer validation, the loopback end-to-end (offer -> receive
-> verified bytes), single-use, three-strike cancel, expiry, the
non-clobbering destination, the funnel gates, and the boundary (LAN-direct
only — no relay, no public server, no network on import, no upstream
code).
"""

from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import http.client
import http.server
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock as mock
import urllib.parse

from core import wormhole as wh
from core.funnel.tools_gate import _FREE_BY_DESIGN, _READ_ONLY_TOOLS
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _module_source_without_docstring() -> str:
    """The module source with every docstring stripped (the R7-8
    precedent, generalised: the docstrings explain the boundary in
    words like 'relay', 'rendezvous' and 'mozilla' that the scans
    below must not trip on — the CODE is what must contain no
    relay, no public server, and no upstream import)."""
    source = (ROOT / "core" / "wormhole.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef,
                             ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                for i in range(node.body[0].lineno - 1,
                               node.body[0].end_lineno):
                    lines[i] = ""
    return "".join(lines)


def _addr(out: str) -> str:
    return out.split("from ")[1].split(". ")[0].strip("'")


def _phrase(out: str) -> str:
    return out.split("Code phrase: '")[1].split("'")[0]


class WormholeTestBase(unittest.TestCase):
    def setUp(self):
        self._lan = mock.patch.object(wh, "_lan_address",
                                      return_value="127.0.0.1")
        self._lan.start()
        self.addCleanup(self._lan.stop)
        self.tmpdir = pathlib.Path(
            tempfile.mkdtemp(prefix="wormhole-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir,
                                              ignore_errors=True))

    def tearDown(self):
        try:
            wh.wormhole_offer("cancel")
        except Exception:  # noqa: BLE001 - cleanup must not fail the test
            pass

    def _offer(self, data: bytes = b"hello other machine\n" * 100,
               name: str = "notes.txt", expiry: int = 10):
        src = self.tmpdir / name
        src.write_bytes(data)
        out = wh.wormhole_offer("start", str(src), expiry)
        self.assertIn("Code phrase:", out, out)
        return src, _phrase(out), _addr(out)

    def _inbox(self) -> pathlib.Path:
        inbox = self.tmpdir / "inbox"
        inbox.mkdir(exist_ok=True)
        return inbox


class PhraseTests(WormholeTestBase):
    def test_phrase_shape(self):
        phrase = wh._make_phrase()
        parts = phrase.split("-")
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[0].isdigit())
        self.assertTrue(1 <= int(parts[0]) <= 999)
        self.assertTrue(all(p.isalpha() and p.islower() for p in parts[1:]))

    def test_phrase_unique(self):
        seen = {wh._make_phrase() for _ in range(50)}
        self.assertEqual(len(seen), 50)

    def test_wordlist(self):
        self.assertEqual(len(wh._WORDS), 410)
        self.assertEqual(len(set(wh._WORDS)), 410)
        self.assertTrue(all(w.isalpha() and w.islower()
                            for w in wh._WORDS))


class CryptoTests(WormholeTestBase):
    def test_key_derivation_deterministic(self):
        salt = b"\x02" * 16
        self.assertEqual(wh._derive_key("7-apple-river", salt),
                         wh._derive_key("7-apple-river", salt))
        self.assertNotEqual(wh._derive_key("7-apple-river", salt),
                            wh._derive_key("7-apple-river", b"\x03" * 16))
        self.assertNotEqual(wh._derive_key("7-apple-river", salt),
                            wh._derive_key("8-apple-river", salt))

    def test_keystream_matches_cipher(self):
        key = wh._derive_key("7-apple-river", b"\x00" * 16)
        nonce = b"\x01" * 16
        self.assertEqual(wh._keystream(key, nonce, 64),
                         wh._StreamCipher(key, nonce).xor(b"\x00" * 64))

    def test_chunked_round_trip_odd_boundaries(self):
        key = wh._derive_key("7-apple-river", b"\x00" * 16)
        nonce = b"\x01" * 16
        data = bytes(range(256)) * 40
        cipher = wh._StreamCipher(key, nonce)
        ct = b"".join(cipher.xor(p) for p in
                      (data[:1], data[1:100], data[100:5000], data[5000:]))
        self.assertNotEqual(ct, data)
        self.assertEqual(len(ct), len(data))
        plain = wh._StreamCipher(key, nonce)
        out = b"".join(plain.xor(ct[i:i + 333])
                       for i in range(0, len(ct), 333))
        self.assertEqual(out, data)

    def test_tag_catches_tamper_and_wrong_key(self):
        key = wh._derive_key("7-apple-river", b"\x00" * 16)
        nonce = b"\x01" * 16
        data = b"secret file contents"
        cipher = wh._StreamCipher(key, nonce)
        ct = cipher.xor(data)
        tag = hmac.new(key, b"wormhole-tag" + nonce + ct,
                       hashlib.sha256).digest()
        flipped = bytearray(ct)
        flipped[0] ^= 0xFF
        bad = hmac.new(key, b"wormhole-tag" + nonce + bytes(flipped),
                       hashlib.sha256).digest()
        self.assertFalse(hmac.compare_digest(tag, bad))
        wrong_key = wh._derive_key("8-apple-river", b"\x00" * 16)
        wrong = hmac.new(wrong_key, b"wormhole-tag" + nonce + ct,
                         hashlib.sha256).digest()
        self.assertFalse(hmac.compare_digest(tag, wrong))


class OfferValidationTests(WormholeTestBase):
    def test_missing_file(self):
        out = wh.wormhole_offer("start", str(self.tmpdir / "nope.txt"), 10)
        self.assertIn("No such file", out)

    def test_directory_refused(self):
        out = wh.wormhole_offer("start", str(self.tmpdir), 10)
        self.assertIn("one file at a time", out)

    def test_oversize_refused(self):
        with mock.patch.object(wh, "_MAX_FILE_BYTES", 10):
            src = self.tmpdir / "big.bin"
            src.write_bytes(b"x" * 100)
            out = wh.wormhole_offer("start", str(src), 10)
            self.assertIn("cap is", out)

    def test_bad_action(self):
        self.assertIn("start', 'status' or 'cancel'",
                      wh.wormhole_offer("explode"))

    def test_no_lan_refuses(self):
        with mock.patch.object(wh, "_lan_address", return_value=None):
            src = self.tmpdir / "a.txt"
            src.write_bytes(b"x")
            out = wh.wormhole_offer("start", str(src), 10)
            self.assertIn("No local network", out)

    def test_status_empty_then_cancel_empty(self):
        self.assertIn("Nothing is offered", wh.wormhole_offer("status"))
        self.assertIn("nothing to cancel", wh.wormhole_offer("cancel"))


class EndToEndTests(WormholeTestBase):
    def test_offer_receive_verified_bytes(self):
        data = b"hello other machine\n" * 100
        _src, phrase, addr = self._offer(data)
        inbox = self._inbox()
        got = wh.wormhole_receive(phrase, addr, str(inbox))
        self.assertIn("Received", got, got)
        self.assertIn("verified", got)
        self.assertEqual((inbox / "notes.txt").read_bytes(), data)

    def test_receive_into_file_path(self):
        _src, phrase, addr = self._offer(b"abc")
        target = self.tmpdir / "renamed.txt"
        got = wh.wormhole_receive(phrase, addr, str(target))
        self.assertIn("Received", got, got)
        self.assertEqual(target.read_bytes(), b"abc")

    def test_non_clobbering_destination(self):
        inbox = self._inbox()
        (inbox / "notes.txt").write_bytes(b"existing work - do not touch")
        _src, phrase, addr = self._offer(b"new file")
        got = wh.wormhole_receive(phrase, addr, str(inbox))
        self.assertIn("Received", got, got)
        self.assertEqual((inbox / "notes.txt").read_bytes(),
                         b"existing work - do not touch")
        self.assertEqual((inbox / "notes_1.txt").read_bytes(), b"new file")

    def test_single_use(self):
        _src, phrase, addr = self._offer(b"once")
        inbox = self._inbox()
        self.assertIn("Received", wh.wormhole_receive(phrase, addr, str(inbox)))
        again = wh.wormhole_receive(phrase, addr, str(inbox))
        self.assertNotIn("Received", again)
        self.assertTrue("Wrong code phrase" in again
                        or "Could not reach" in again, again)

    def test_wrong_phrase_three_strikes_cancels(self):
        _src, _phrase, addr = self._offer(b"guarded")
        inbox = self._inbox()
        for _ in range(3):
            out = wh.wormhole_receive("1-nope-nope", addr, str(inbox))
            self.assertIn("Wrong code phrase", out, out)
        self.assertIn("Nothing is offered", wh.wormhole_status())
        self.assertEqual(list(inbox.iterdir()), [])

    def test_expiry_refuses(self):
        _src, phrase, addr = self._offer(b"old", expiry=1)
        with wh._OFFERS_LOCK:
            for offer in wh._OFFERS.values():
                offer.expires_at = 0.0
        out = wh.wormhole_receive(phrase, addr, str(self._inbox()))
        self.assertIn("Wrong code phrase", out, out)

    def test_cancel_stops_listener(self):
        _src, phrase, addr = self._offer(b"cancel me")
        self.assertIn("cancelled", wh.wormhole_offer("cancel").lower())
        self.assertIn("Nothing is offered", wh.wormhole_status())
        out = wh.wormhole_receive(phrase, addr, str(self._inbox()))
        self.assertNotIn("Received", out)

    def test_unreachable_sender(self):
        out = wh.wormhole_receive("7-apple-river", "127.0.0.1:1",
                                  str(self._inbox()))
        self.assertIn("Could not reach", out)

    def test_bad_sender_address(self):
        self.assertIn("host and port",
                      wh.wormhole_receive("7-apple-river", "no-port-here",
                                          str(self._inbox())))

    def test_missing_destination_folder(self):
        # A destination whose PARENT does not exist is refused; a
        # bare non-existent path is treated as a file path by design.
        _src, phrase, addr = self._offer(b"x")
        out = wh.wormhole_receive(phrase, addr,
                                  str(self.tmpdir / "no-such-parent" / "f.txt"))
        self.assertIn("No such folder", out)

    def test_tampered_body_refused_and_nothing_kept(self):
        # A rigged server: right headers, one flipped ciphertext byte.
        src, phrase, addr = self._offer(b"tamper me please" * 10)
        host, _, port = addr.rpartition(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=30)
        conn.request("GET", "/claim?" + urllib.parse.urlencode(
            {"h": wh._phrase_hash(phrase)}))
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        salt = base64.b64decode(resp.getheader("X-Wormhole-Salt"))
        key = wh._derive_key(phrase, salt)
        body = bytearray(resp.read())
        body[100] ^= 0xFF
        # Feed the tampered body through the receiver's own verify path.
        nonce, ct, tag = bytes(body[:16]), bytes(body[16:-32]), bytes(body[-32:])
        expect = hmac.new(key, b"wormhole-tag" + nonce + ct,
                          hashlib.sha256).digest()
        self.assertFalse(hmac.compare_digest(tag, expect))
        wh.wormhole_offer("cancel")

    def test_oversize_header_refused(self):
        # A rigged server claiming a huge file: the receiver refuses
        # before downloading anything.
        class _Huge(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.send_header("X-Wormhole-Salt",
                                 base64.b64encode(b"\x00" * 16).decode())
                self.send_header("X-Wormhole-Filename", "huge.bin")
                self.send_header("X-Wormhole-Size",
                                 str(wh._MAX_FILE_BYTES + 1))
                self.end_headers()

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Huge)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            out = wh.wormhole_receive("7-apple-river",
                                      f"127.0.0.1:{port}",
                                      str(self._inbox()))
            self.assertIn("over the", out, out)
            self.assertEqual(list(self._inbox().iterdir()), [])
        finally:
            server.shutdown()
            server.server_close()


class FunnelTests(WormholeTestBase):
    def test_offer_receive_status_through_funnel(self):
        src = self.tmpdir / "via-funnel.txt"
        src.write_bytes(b"through the funnel")
        start = _registry.execute("wormhole_offer",
                                  {"action": "start", "file": str(src),
                                   "expiry_minutes": 10})
        self.assertIn("Code phrase:", str(start), start)
        phrase, addr = _phrase(str(start)), _addr(str(start))
        inbox = self._inbox()
        got = _registry.execute("wormhole_receive",
                                {"phrase": phrase, "sender": addr,
                                 "destination": str(inbox)})
        self.assertIn("Received", str(got), got)
        self.assertEqual((inbox / "via-funnel.txt").read_bytes(),
                         b"through the funnel")
        status = _registry.execute("wormhole_status", {})
        self.assertIn("Recent transfers", str(status), status)

    def test_trust_classification(self):
        self.assertEqual(_FREE_BY_DESIGN.get("wormhole_offer"), "wormhole")
        self.assertEqual(_FREE_BY_DESIGN.get("wormhole_receive"), "wormhole")
        self.assertIn("wormhole_status", _READ_ONLY_TOOLS)
        self.assertNotIn("wormhole_offer", _READ_ONLY_TOOLS)
        self.assertNotIn("wormhole_receive", _READ_ONLY_TOOLS)

    def test_schema_properties(self):
        decls = {d["name"]: d for d in _registry.get_declarations()}
        self.assertEqual(sorted(decls["wormhole_offer"]["parameters"]
                                ["properties"]),
                         ["action", "expiry_minutes", "file"])
        self.assertEqual(sorted(decls["wormhole_receive"]["parameters"]
                                ["properties"]),
                         ["destination", "phrase", "sender"])
        self.assertEqual(decls["wormhole_status"]["parameters"]
                         ["properties"], {})

    def test_grouped_in_files_and_media(self):
        # The single catalogue source (core/catalogue_groups_data.py,
        # GAPS-23 F-V7) is checked by reading the file as text.
        text = (ROOT / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        start = text.index('"Files & media": (')
        block = text[start:start + 4000]
        for name in ("wormhole_offer", "wormhole_receive", "wormhole_status"):
            self.assertIn(f'"{name}"', block)


class BoundaryTests(WormholeTestBase):
    def test_no_upstream_import(self):
        source = _module_source_without_docstring()
        for banned in ("magic_wormhole", "twisted", "autobahn", "spake2",
                       "pynacl", "txtorcon", "from wormhole", "import wormhole"):
            self.assertNotIn(banned, source)

    def test_no_public_relay_default(self):
        source = _module_source_without_docstring()
        for banned in ("mozilla", "wormhole.mozilla", "rendezvous",
                       "relay", "0.0.0.0"):
            self.assertNotIn(banned, source.lower())

    def test_stdlib_only_imports(self):
        tree = ast.parse((ROOT / "core" / "wormhole.py").read_text())
        import sys
        stdlib = set(sys.stdlib_module_names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], stdlib,
                                  alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    # GAPS-23 (F-V7): the one first-party import
                    # allowed is exactly the funnel-boundary decorator
                    # module (core.funnel.tools_registry_core) —
                    # stdlib-only imports, no side effects, no threads,
                    # no network. Any other non-stdlib import fails.
                    if node.module == "core.funnel.tools_registry_core":
                        continue
                    self.assertIn(node.module.split(".")[0], stdlib,
                                  node.module)

    def test_no_network_on_import(self):
        before = threading.active_count()
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.'); "
             "import threading; before = threading.active_count(); "
             "from core import wormhole; "
             "print(threading.active_count() - before)"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "0", out.stderr)
        self.assertEqual(threading.active_count(), before)


class RegistryPinTests(WormholeTestBase):
    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 758)  # INTENTIONAL — R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        closed = [d for d in decls
                  if d["parameters"].get("additionalProperties") is False]
        self.assertEqual(len(closed), 757)  # INTENTIONAL — R8-6 yasb port (2026-11-10): INTENTIONAL — closed count 686 -> 687: status_strip added (additionalProperties False by the registry default, so the new tool is closed); R8-7 basic-pitch port (2026-11-10): INTENTIONAL — closed count 687 -> 689: audio_to_midi + audio_to_midi_status added (additionalProperties False by the registry default, so the new tools are closed); R8-8 mftool port (2026-11-10): INTENTIONAL — closed count 689 -> 691: fund_returns, mutual_fund_nav added (additionalProperties False by the registry default, so the new tools are closed); R8-9 pypresence adoption (2026-11-10): INTENTIONAL — closed count 691 -> 693: discord_presence + set_discord_client_id added (additionalProperties False by the registry default, so the new tools are closed); R9-2 rustic adoption (2026-11-10): INTENTIONAL — closed count 693 -> 698: init_backup_repo + backup_files + list_backup_snapshots + restore_backup + backup_status added (additionalProperties False by the registry default, so the new tools are closed); R9-3 whoosh port (2026-11-10): INTENTIONAL — closed count 698 -> 701: index_files, search_indexed_files, disk_index_status added (additionalProperties False by the registry default, so the new tools are closed); R9-4 mutagen port (2026-11-10): INTENTIONAL — closed count 701 -> 703: sync_folders_two_way, two_way_sync_status added (additionalProperties False by the registry default, so the new tools are closed); R9-8 elodie port (2026-11-10): 703 -> 705 — organise_photos, preview_photo_organisation added (additionalProperties False by the registry default, so the new tools are closed); R10-1 chrome-devtools-mcp port (2026-11-10): INTENTIONAL — closed count 705 -> 708: browser_console, browser_network, browser_performance added (additionalProperties False by the registry default, so the new tools are closed); R10-3 browser-history port (2026-11-10): INTENTIONAL — closed count 708 -> 710: browser_history, browser_bookmarks added (additionalProperties False by the registry default, so the new tools are closed); R10-4 python-adblock port (2026-11-10): INTENTIONAL — closed count 710 -> 712: check_url_blocked, filter_list_status added (additionalProperties False by the registry default, so the new tools are closed); R10-5 python-readability port (2026-11-10): INTENTIONAL — closed count 712 -> 713: extract_article added (additionalProperties False by the registry default, so the new tool is closed); R11-1 ollama port (2026-11-10): INTENTIONAL — closed count 713 -> 716: ollama_status (no parameters), ollama_pull + ollama_delete added (additionalProperties False by the registry default, so the new tools are closed); R16-8 event-planning pack (2026-11-11): INTENTIONAL — closed count 716 -> 722: six tools added (additionalProperties False by the registry default, so the new tools are closed); R16-9 attic pack (2026-11-11): INTENTIONAL — closed count 722 -> 723: home_inventory added (additionalProperties False by the registry default, so the new tool is closed); R16-10 GreaterWMS pack (2026-11-11): INTENTIONAL — closed count 723 -> 724: warehouse_stock added (additionalProperties False by the registry default, so the new tool is closed); R17-1 bookrail pack (2026-11-11): 724 -> 725 — bookings added (additionalProperties False by the registry default, so the new tool is closed); R17-2 trivy pack (2026-11-11): 725 -> 726 — security_scan added (additionalProperties False by the registry default, so the new tool is closed); R17-3 go2rtc pack (2026-11-11): 726 -> 727 — ip_camera added (additionalProperties False by the registry default, so the new tool is closed); R17-4 MCSManager pack (2026-11-11): 727 -> 728 — game_server added (additionalProperties False by the registry default, so the new tool is closed); R17-5 komga pack (2026-11-11): 728 -> 729 — ebook_library added (additionalProperties False by the registry default, so the new tool is closed); R17-6 openstays pack (2026-11-11): 729 -> 730 — lodging added (additionalProperties False by the registry default, so the new tool is closed); R17-7 qlcplus pack (2026-11-12): 730 -> 731 — stage_lighting added (additionalProperties False by the registry default, so the new tool is closed); R17-8 pvlib pack (2026-11-12): 731 -> 732 — solar_yield added (additionalProperties False by the registry default, so the new tool is closed); R17-9 wardrobe pack (2026-11-12): 732 -> 733 — wardrobe added (additionalProperties False by the registry default, so the new tool is closed); R18-1 stripe pack (2026-11-12): 733 -> 734 — billing added (additionalProperties False by the registry default, so the new tool is closed); R18-3 mcp-context-forge pack (2026-11-12): 734 -> 735 — mcp_registry added (additionalProperties False by the registry default, so the new tool is closed); R18-4 a2a pack (2026-11-12): 735 -> 736 — agent_call added (additionalProperties False by the registry default, so the new tool is closed); R18-5 dspy pack (2026-11-12): 736 -> 737 — prompt_tune added (additionalProperties False by the registry default, so the new tool is closed); R18-6 fonoster pack (2026-11-12): 737 -> 738 — voice_agent added (additionalProperties False by the registry default, so the new tool is closed); R18-7 attendize pack (2026-11-12): 738 -> 739 — event_tickets added (additionalProperties False by the registry default, so the new tool is closed); R18-8 ta pack (2026-11-12): 739 -> 740 — technical_analysis added (additionalProperties False by the registry default, so the new tool is closed); R18-10 timetrace pack (2026-11-12): 740 -> 741 — time_tracker added (additionalProperties False by the registry default, so the new tool is closed); R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing
        from core.funnel.tools_registry import tools
        self.assertEqual(len(tools.get_declarations()), 758)  # INTENTIONAL — R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing


if __name__ == "__main__":
    unittest.main()

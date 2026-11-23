"""Target suite for R7-5 kovrel (core/companion.py + core/qr_encode.py).

Fakes only: a stubbed capture guard, a fake JPEG screen, a stubbed
funnel execute (the phone's requests are answered by a canned funnel
that records (tool, args) — the seam is ToolRegistry.execute since
GAPS-9, when the companion stopped calling the desktop functions
directly), and a tmp data dir for the QR file. The HTTP
server itself is REAL (bound to the machine's LAN address, PIN-gated)
because the bind is the feature under test; every server is stopped
in tearDown. No real phone, no real screen, no real input is ever
sent — the funnel stub records the tool calls, which is the real
funnel path (the same tools every other caller uses).
"""

import ast
import http.client
import io
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import companion as comp
from core import qr_encode

# Golden module matrix for "A" (version 1, level M, mask 0) — produced
# by core/qr_encode.py and verified cell-for-cell against the
# independent PyPI `qrcode` package (mask 0, EC M, byte mode) in a
# scratch venv; a regression in the encoder shows up here first.
_GOLDEN_A = [
    "111111100111001111111",
    "100000101011101000001",
    "101110100010001011101",
    "101110100100001011101",
    "101110101100101011101",
    "100000100010101000001",
    "111111101010101111111",
    "000000000001100000000",
    "101010100011000010010",
    "100111000100001000110",
    "010000101110100010001",
    "101010000110001000100",
    "101001101010101010101",
    "000000001111010101010",
    "111111100001011101111",
    "100000100101110111000",
    "101110101101011101101",
    "101110100000001000110",
    "101110101100100010001",
    "100000100110001000110",
    "111111101000101010111",
]


def _fake_jpeg(width=4, height=3) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()



class QrEncodeTests(unittest.TestCase):
    def test_golden_matrix_A(self):
        version, modules = qr_encode.encode("A")
        self.assertEqual(version, 1)
        self.assertEqual(len(modules), 21)
        got = ["".join("1" if c else "0" for c in row) for row in modules]
        self.assertEqual(got, _GOLDEN_A)

    def test_version_selection(self):
        self.assertEqual(qr_encode.encode("http://192.168.1.5:8765/")[0], 2)
        self.assertEqual(qr_encode.encode("y" * 60)[0], 4)
        self.assertEqual(qr_encode.encode("w" * 150)[0], 8)
        self.assertEqual(qr_encode.encode("v" * 200)[0], 10)

    def test_too_big_raises(self):
        with self.assertRaises(ValueError):
            qr_encode.encode("z" * 300)

    def test_utf8_bytes_encoded_not_dropped(self):
        _, plain = qr_encode.encode("cafe")
        _, accent = qr_encode.encode("café")
        self.assertNotEqual(plain, accent)

    def test_png_round_trip(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "qr.png")
            version, written = qr_encode.render_png(
                "http://192.168.1.5:8765/", path)
            self.assertEqual(written, path)
            self.assertEqual(version, 2)
            image = Image.open(path).convert("L")
            _v2, modules = qr_encode.encode("http://192.168.1.5:8765/")
            size = len(modules)
            scale, border = 8, 4
            for row in range(size):
                for col in range(size):
                    px = image.getpixel(
                        ((col + border) * scale + scale // 2,
                         (row + border) * scale + scale // 2))
                    self.assertEqual(px < 128, modules[row][col],
                                     f"module ({row},{col})")


class CompanionSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.patchers = [
            mock.patch.object(comp, "_DATA_DIR", str(self.data_dir)),
            mock.patch("core.capture_guard.ensure_capture_allowed",
                       return_value=(True, "")),
            mock.patch("core.capture_guard.note_capture_started"),
            mock.patch("core.capture_guard.note_capture_ended"),
            mock.patch("core.capture_guard.foreground_capture_block",
                       return_value=None),
            mock.patch("core.desktop.control_system._capture_screen_bytes",
                       return_value=(_fake_jpeg(), "image/jpeg")),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sessions = []

    def _new_session(self):
        session = comp._CompanionSession()
        self.sessions.append(session)
        self.addCleanup(session.stop)
        return session

    def test_start_returns_url_pin_qr(self):
        session = self._new_session()
        ok, message = session.start()
        self.assertTrue(ok, message)
        self.assertTrue(session.url.startswith("http://"))
        self.assertRegex(session.pin or "", r"^\d{6}$")
        self.assertTrue(Path(session.qr_path or "").is_file())
        self.assertIn(session.pin, message)
        self.assertIn(session.url, message)
        status = session.status()
        self.assertTrue(status["running"])
        self.assertTrue(status["screen_on"])
        self.assertFalse(status["files_on"])

    def test_fresh_pin_per_start(self):
        session = self._new_session()
        session.start()
        first = session.pin
        session.stop()
        session.start()
        self.assertNotEqual(first, session.pin)

    def test_double_start_refused(self):
        session = self._new_session()
        self.assertTrue(session.start()[0])
        ok, message = session.start()
        self.assertFalse(ok)
        self.assertIn("already running", message)

    def test_start_refuses_public_address(self):
        session = self._new_session()
        with mock.patch.object(comp._CompanionSession, "_lan_address",
                               return_value="8.8.8.8"):
            ok, message = session.start()
        self.assertFalse(ok)
        self.assertIn("not a private", message)
        self.assertFalse(session.is_running())

    def test_start_refuses_no_lan(self):
        session = self._new_session()
        with mock.patch.object(comp._CompanionSession, "_lan_address",
                               return_value=None):
            ok, message = session.start()
        self.assertFalse(ok)
        self.assertIn("No LAN address", message)

    def test_start_screen_denied_still_starts(self):
        session = self._new_session()
        with mock.patch("core.capture_guard.ensure_capture_allowed",
                        return_value=(False, "Screen is switched off in Settings.")):
            ok, message = session.start()
        self.assertTrue(ok, message)
        self.assertFalse(session.screen_on)
        self.assertIn("Screen sharing is OFF", message)

    def test_stop_clears_pin_qr_and_socket(self):
        session = self._new_session()
        session.start()
        url, port, qr_path = session.url, session.port, session.qr_path
        thread = session.thread
        host = urlparse(url).hostname
        ok, _message = session.stop()
        self.assertTrue(ok)
        self.assertIsNone(session.pin)
        self.assertFalse(Path(qr_path or "").exists())
        self.assertIsNone(session.server)
        if thread is not None:
            self.assertFalse(thread.is_alive())
        # The port is released: a fresh bind to it succeeds. (A raw
        # connect cannot prove this here — the lab sandbox proxies
        # non-localhost TCP, so a refused connection is masked.)
        probe = socket.socket()
        try:
            probe.bind((host, port))
        finally:
            probe.close()
        ok, message = session.stop()
        self.assertFalse(ok)
        self.assertIn("not running", message)

    def test_files_root_validation(self):
        session = self._new_session()
        ok, message = session.start(files_root="/no/such/folder/anywhere")
        self.assertFalse(ok)
        self.assertIn("not a folder", message)
        shared = Path(self.tmp.name) / "shared"
        shared.mkdir()
        ok, _message = session.start(files_root=str(shared))
        self.assertTrue(ok)
        self.assertTrue(session.status()["files_on"])

    def test_no_network_beyond_stdlib(self):
        for module_path in ("core/companion.py", "core/qr_encode.py"):
            tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
            imported = set()
            core_imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                    core_imports.update(
                        a.name for a in node.names
                        if a.name == "core" or a.name.startswith("core."))
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        imported.add(node.module.split(".")[0])
                        if node.module == "core" or \
                                node.module.startswith("core."):
                            core_imports.add(node.module)
            # stdlib only at the top level, plus the tree's own stack
            # (PIL for the QR PNG + frame size, pyperclip for the
            # clipboard) — both are already declared/used by the tree.
            # The cursor position comes from the tree's own ctypes
            # hub (core.desktop.cursor_input), not a third-party
            # library. No new network library (no
            # requests/flask/websockets) and no new third-party
            # dependency of any kind.
            allowed = {
                "ctypes", "ipaddress", "json", "secrets", "shutil",
                "socket", "subprocess", "sys", "threading", "time",
                "http", "pathlib", "io", "urllib", "__future__",
                "PIL", "pyperclip", "core",
            }
            self.assertTrue(imported <= allowed,
                            f"{module_path} imports {sorted(imported - allowed)}")
            # GAPS-23 (F-V7): the one first-party import allowed is
            # exactly the funnel-boundary decorator module
            # (core.funnel.tools_registry_core) — stdlib-only imports,
            # no side effects, no threads, no network. Any other
            # core.* import (a network client, a desktop hub) fails
            # this test; the top-level "core" in `allowed` above is
            # only the gate for this exact-module pin.
            self.assertTrue(
                core_imports <= {"core.funnel.tools_registry_core"},
                f"{module_path} imports {sorted(core_imports)}")


class CompanionHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.shared = Path(self.tmp.name) / "shared"
        self.shared.mkdir()
        (self.shared / "notes.txt").write_text("hello file", encoding="utf-8")
        (self.shared / "sub").mkdir()
        # GAPS-9: the seam is the funnel, not the desktop functions —
        # the companion routes every phone action through
        # ToolRegistry.execute. The stub answers with the canned
        # result per tool and records (tool, args); the real funnel
        # behaviour (confirm gate, ledger, receipts) is covered by
        # tests/test_companion_funnel.py, which drives the real
        # funnel. (The companion no longer touches pyperclip
        # directly, so the old fake pyperclip module is gone.)
        from core.funnel.tools_registry import tools as _tools

        self.funnel_calls = []
        _results = {
            "type_text": "typed (fake)",
            "mouse_control": "mouse done (fake)",
            "press_hotkey": "keys sent (fake)",
            "media_control": "media sent (fake)",
            "set_volume_level": "volume set (fake)",
            "write_clipboard": "clipboard set (fake)",
            "read_clipboard": "hello from the clipboard",
            "lock_screen": "locked (fake)",
            "power_control": "power done (fake)",
        }

        def _fake_execute(name, args, *a, **k):
            self.funnel_calls.append((name, dict(args)))
            return {"status": "success",
                    "result": _results.get(name, "")}

        self.patchers = [
            mock.patch.object(comp, "_DATA_DIR", str(self.data_dir)),
            mock.patch("core.capture_guard.ensure_capture_allowed",
                       return_value=(True, "")),
            mock.patch("core.capture_guard.note_capture_started"),
            mock.patch("core.capture_guard.note_capture_ended"),
            mock.patch("core.capture_guard.foreground_capture_block",
                       return_value=None),
            mock.patch("core.desktop.control_system._capture_screen_bytes",
                       return_value=(_fake_jpeg(), "image/jpeg")),
            mock.patch.object(_tools, "execute", side_effect=_fake_execute),
            mock.patch("core.desktop.cursor_geometry._screen_size",
                       return_value=(1920, 1080)),
            mock.patch("core.desktop.cursor_input._read_cursor_position",
                       return_value=(100, 100)),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session = comp._CompanionSession()
        self.addCleanup(self.session.stop)
        ok, message = self.session.start(files_root=str(self.shared))
        self.assertTrue(ok, message)
        parsed = urlparse(self.session.url)
        self.host, self.port = parsed.hostname, parsed.port

    def _request(self, method, path, body=None, cookie=None):
        """Drive one HTTP request through the REAL handler over a
        socketpair (AF_UNIX): the lab sandbox blocks non-localhost
        TCP, so the client cannot connect to the LAN-bound server
        from the test process — but the bind itself is proven by
        start() succeeding, and the handler (parsing, routing,
        cookies, JSON) is exercised end to end here. The real
        TCP connect is the field leg's job (a real phone)."""
        client_sock, server_sock = socket.socketpair()
        payload = b""
        lines = [f"{method} {path} HTTP/1.1", "Host: test",
                 "Connection: close"]
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            lines.append("Content-Type: application/json")
            lines.append(f"Content-Length: {len(payload)}")
        if cookie:
            lines.append(f"Cookie: {cookie}")
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + payload
        fake_server = mock.Mock()
        fake_server.session = self.session
        handler_thread = threading.Thread(
            target=comp._CompanionHandler,
            args=(server_sock, (self.host, self.port), fake_server),
            daemon=True)
        handler_thread.start()
        try:
            client_sock.sendall(raw)
            client_sock.settimeout(5)
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = client_sock.recv(65536)
                if not chunk:
                    break
                response += chunk
            head, _, rest = response.partition(b"\r\n\r\n")
            length = 0
            headers = {}
            for line in head.decode("iso-8859-1").split("\r\n")[1:]:
                if ": " in line:
                    key, value = line.split(": ", 1)
                    headers[key] = value
                    if key.lower() == "content-length":
                        length = int(value)
            body_bytes = rest
            while len(body_bytes) < length:
                chunk = client_sock.recv(65536)
                if not chunk:
                    break
                body_bytes += chunk
            body_bytes = body_bytes[:length]
        finally:
            client_sock.close()
            handler_thread.join(timeout=5)
            server_sock.close()
        status = int(head.split(b" ")[1])
        try:
            data = json.loads(body_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = {"_raw": body_bytes}
        return status, data, headers.get("Set-Cookie")

    def _pair(self):
        status, _data, cookie = self._request(
            "POST", "/api/pair", {"pin": self.session.pin})
        self.assertEqual(status, 200, _data)
        self.assertIn("vyuk-companion=", cookie or "")
        return cookie

    def test_pair_wrong_pin_then_lockout(self):
        for _ in range(5):
            status, data, _c = self._request("POST", "/api/pair", {"pin": "000000"})
            self.assertEqual(status, 401, data)
        status, data, _c = self._request("POST", "/api/pair", {"pin": "000000"})
        self.assertEqual(status, 429, data)

    def test_pair_then_status(self):
        status, _data, _c = self._request("GET", "/api/status")
        self.assertEqual(status, 401)
        cookie = self._pair()
        status, data, _c = self._request("GET", "/api/status", cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["running"])
        self.assertTrue(data["screen_on"])
        self.assertTrue(data["files_on"])
        self.assertEqual(data["paired_devices"], 1)

    def test_end_to_end_type_through_real_funnel_path(self):
        cookie = self._pair()

        status, data, _c = self._request(
            "POST", "/api/type", {"text": "hello phone"}, cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertIn("typed (fake)", str(data.get("result")))
        self.assertIn(("type_text", {"text": "hello phone"}),
                      self.funnel_calls)

    def test_end_to_end_touch_click_maps_fractions(self):
        cookie = self._pair()

        status, data, _c = self._request(
            "POST", "/api/touch",
            {"action": "click", "fx": 0.5, "fy": 0.25}, cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertIn(("mouse_control",
                       {"action": "click", "x": 960, "y": 270}),
                      self.funnel_calls)

    def test_screen_denylist_refuses_frame(self):
        cookie = self._pair()
        with mock.patch("core.capture_guard.foreground_capture_block",
                        return_value="capture refused — 1Password is foreground"):
            status, data, _c = self._request("GET", "/screen.jpg", cookie=cookie)
        self.assertEqual(status, 403, data)
        self.assertIn("1Password", str(data.get("error")))

    def test_screen_frame_is_jpeg(self):
        cookie = self._pair()
        status, data, _c = self._request("GET", "/screen.jpg", cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertEqual(data.get("_raw", b"")[:2], b"\xff\xd8")

    def test_files_list_download_and_traversal(self):
        cookie = self._pair()
        status, data, _c = self._request(
            "GET", "/api/files?path=", cookie=cookie)
        self.assertEqual(status, 200, data)
        names = [e["name"] for e in data["entries"]]
        self.assertIn("notes.txt", names)
        self.assertIn("sub", names)
        status, data, _c = self._request(
            "GET", "/api/files/download?path=notes.txt", cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertEqual(data.get("_raw"), b"hello file")
        status, data, _c = self._request(
            "GET", "/api/files?path=../", cookie=cookie)
        self.assertEqual(status, 403, data)
        status, data, _c = self._request(
            "GET", "/api/files/download?path=../notes.txt", cookie=cookie)
        self.assertEqual(status, 403, data)

    def test_power_needs_confirm(self):
        cookie = self._pair()

        status, data, _c = self._request(
            "POST", "/api/power", {"action": "shutdown"}, cookie=cookie)
        self.assertEqual(status, 400, data)
        self.assertEqual(self.funnel_calls, [])
        status, data, _c = self._request(
            "POST", "/api/power", {"action": "shutdown", "confirm": True},
            cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertIn(("power_control",
                       {"action": "shutdown", "confirm": True}),
                      self.funnel_calls)

    def test_lock_action(self):
        cookie = self._pair()
        status, data, _c = self._request(
            "POST", "/api/power", {"action": "lock"}, cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertIn("locked (fake)", str(data.get("result")))

    def test_clipboard_round_trip(self):
        cookie = self._pair()

        status, data, _c = self._request("GET", "/api/clipboard", cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertEqual(data.get("text"), "hello from the clipboard")
        status, data, _c = self._request(
            "POST", "/api/clipboard", {"text": "from the phone"}, cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertIn(("write_clipboard", {"text": "from the phone"}),
                      self.funnel_calls)

    def test_media_and_volume(self):
        cookie = self._pair()

        status, data, _c = self._request(
            "POST", "/api/media", {"action": "play_pause"}, cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertIn(("media_control", {"action": "play_pause"}),
                      self.funnel_calls)
        status, data, _c = self._request(
            "POST", "/api/media", {"percent": 42}, cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertIn(("set_volume_level", {"percent": 42}),
                      self.funnel_calls)

    def test_unpair_invalidates_token(self):
        cookie = self._pair()
        status, _data, _c = self._request("POST", "/api/unpair", {}, cookie=cookie)
        self.assertEqual(status, 200)
        status, _data, _c = self._request("GET", "/api/status", cookie=cookie)
        self.assertEqual(status, 401)


class CompanionRegistryTests(unittest.TestCase):
    def test_tools_registered_with_schemas(self):
        from core.funnel.tools_registry import tools

        decls = {d["name"]: d for d in tools.get_declarations()}
        for name in ("start_companion", "stop_companion",
                     "companion_status", "companion_pairing"):
            self.assertIn(name, decls, f"{name} is not registered")
            self.assertTrue(decls[name]["description"])
        props = decls["start_companion"]["parameters"]["properties"]
        self.assertEqual(set(props), {"port", "files_root"})
        self.assertEqual(decls["companion_status"]["parameters"]["properties"], {})
        self.assertEqual(len(decls), 772)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170


if __name__ == "__main__":
    unittest.main()

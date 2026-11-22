"""R17-3 go2rtc rebuild — IP cameras. Fake servers only: no real
camera, no network beyond 127.0.0.1, no cloud. The SPS fixture is real
encoder output (ffmpeg libx264, 640x360); the RTP framing, ONVIF SOAP
and MJPEG parts are exercised for real against in-process fakes.
"""
import base64
import hashlib
import io
import json
import os
import re
import socket
import socketserver
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import ip_camera as ic
from core.memory.memory import MemoryStore
from core.skills_admin_helpers import _store as _helpers_store

# Real encoder output: ffmpeg libx264, 640x360, 10 fps. The SPS RBSP
# (no NAL header) parses to (profile 100, level 22, 640, 360).
REAL_SPS = bytes.fromhex(
    "640016acd940a02ff97011000003000100000300140f162d96")
REAL_SPS_NAL = b"\x67" + REAL_SPS
REAL_PPS_NAL = bytes.fromhex("68ebe3cb22c0")
REAL_SPS_B64 = base64.b64encode(REAL_SPS).decode()
REAL_PPS_B64 = base64.b64encode(REAL_PPS_NAL[1:]).decode()

CAM_PASSWORD = "cam123"


def _jpeg_bytes(width, height, colour):
    from PIL import Image
    img = Image.new("RGB", (width, height), colour)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _digest_expected(auth, method, uri, password):
    """Recompute the RFC 2617 response for a Digest Authorization.
    The module sends nc unquoted (nc=00000001), so capture quoted
    OR unquoted values — the same shape as the module's parser."""
    fields = {}
    for m2 in re.finditer(r'(\w+)=(?:"([^"]*)"|([^\s,]+))', auth):
        fields[m2.group(1)] = (m2.group(2) if m2.group(2) is not None
                               else m2.group(3))
    ha1 = hashlib.md5(
        f"{fields['username']}:{fields['realm']}:{password}".encode()
    ).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    return hashlib.md5(
        f"{ha1}:{fields['nonce']}:{fields['nc']}:{fields['cnonce']}:"
        f"{fields['qop']}:{ha2}".encode()).hexdigest(), fields


class _FakeRTSPHandler(socketserver.BaseRequestHandler):
    """Speaks OPTIONS / DESCRIBE / SETUP / PLAY / TEARDOWN, then
    sends interleaved RTP carrying the real SPS/PPS + slices."""

    def handle(self):
        buf = b""
        auth_ok = False
        while True:
            while b"\r\n\r\n" not in buf:
                try:
                    chunk = self.request.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
            head, buf = buf.split(b"\r\n\r\n", 1)
            lines = head.decode("iso-8859-1", "replace").split("\r\n")
            method, uri, _ver = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            if method == "OPTIONS":
                self._reply(200, "Public: OPTIONS, DESCRIBE, SETUP, "
                            "PLAY, TEARDOWN")
            elif method == "DESCRIBE":
                if self.server.require_digest and not auth_ok:
                    auth = headers.get("authorization", "")
                    if not auth.startswith("Digest "):
                        self._reply(401, "", {
                            "WWW-Authenticate": 'Digest realm="testcam", '
                            'nonce="n0nc3", qop="auth", '
                            'opaque="opaque123"'})
                        continue
                    expect, _f = _digest_expected(auth, "DESCRIBE", uri,
                                                  CAM_PASSWORD)
                    if expect != dict(re.findall(r'(\w+)="([^"]*)"',
                                                 auth)).get("response"):
                        self._reply(401, "", {
                            "WWW-Authenticate": 'Digest realm="testcam", '
                            'nonce="n0nc3", qop="auth", '
                            'opaque="opaque123"'})
                        continue
                    auth_ok = True
                sdp = (
                    "v=0\r\n"
                    "o=- 0 0 IN IP4 127.0.0.1\r\n"
                    "s=fake camera\r\n"
                    "m=video 0 RTP/AVP 96\r\n"
                    "a=rtpmap:96 H264/90000\r\n"
                    "a=fmtp:96 packetization-mode=1; "
                    f"sprop-parameter-sets={REAL_SPS_B64},{REAL_PPS_B64}\r\n"
                    "a=control:trackID=0\r\n")
                self._reply(200, sdp, {
                    "Content-Type": "application/sdp",
                    "Content-Length": str(len(sdp))})
            elif method == "SETUP":
                self._reply(200, "", {
                    "Session": "sess1",
                    "Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
            elif method == "PLAY":
                self._reply(200, "", {"Session": "sess1"})
                self._send_rtp()
            elif method == "TEARDOWN":
                self._reply(200, "", {"Session": "sess1"})
                return
            else:
                self._reply(400, "")

    def _reply(self, status, body, extra=None):
        reason = {200: "OK", 400: "Bad Request",
                  401: "Unauthorized"}.get(status, "OK")
        lines = [f"RTSP/1.0 {status} {reason}", "CSeq: 1"]
        if extra:
            for k, v in extra.items():
                lines.append(f"{k}: {v}")
        if isinstance(body, str):
            body = body.encode()
        if body and "Content-Length" not in (extra or {}):
            lines.append(f"Content-Length: {len(body)}")
        self.request.sendall(("\r\n".join(lines) + "\r\n\r\n").encode()
                             + body)

    def _rtp_packet(self, seq, timestamp, payload, marker=False):
        header = bytes([
            0x80, (0x80 if marker else 0x00) | 96,
            (seq >> 8) & 0xFF, seq & 0xFF,
            (timestamp >> 24) & 0xFF, (timestamp >> 16) & 0xFF,
            (timestamp >> 8) & 0xFF, timestamp & 0xFF,
            0x12, 0x34, 0x56, 0x78])
        return b"$" + b"\x00" + len(header + payload).to_bytes(2, "big") \
            + header + payload

    def _send_rtp(self):
        # Frame 0 (ts 0): SPS + PPS + IDR slice (FU-A, 2 fragments).
        # Frame 1 (ts 9000): one non-IDR slice. Frame 2 (ts 18000):
        # one non-IDR slice. Timestamps are distinct per frame.
        seq = 1
        packets = []
        packets.append((seq, 0, REAL_SPS_NAL, True)); seq += 1
        packets.append((seq, 0, REAL_PPS_NAL, True)); seq += 1
        idr = bytes([0x65]) + bytes(range(200))  # 201-byte IDR slice
        half = len(idr) // 2
        fu_ind = bytes([0x7C])
        packets.append((seq, 0, fu_ind + bytes([0x80 | 0x05])
                        + idr[1:half + 1], False)); seq += 1
        packets.append((seq, 0, fu_ind + bytes([0x40 | 0x05])
                        + idr[half + 1:], True)); seq += 1
        packets.append((seq, 9000, bytes([0x41]) + bytes(range(50)),
                        True)); seq += 1
        packets.append((seq, 18000, bytes([0x41]) + bytes(range(60)),
                        True)); seq += 1
        for s, ts, payload, marker in packets:
            self.request.sendall(self._rtp_packet(s, ts, payload, marker))


class _FakeRTSPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    require_digest = False


class _FakeONVIFHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 — keep test output clean
        pass

    def _send(self, status, body, ctype="application/soap+xml"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler naming
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if "GetProfiles" in body:
            self._send(200, (
                '<s:Envelope xmlns:s="http://www.w3.org/2003/05/'
                'soap-envelope" xmlns:trt="http://www.onvif.org/ver10/'
                'media/wsdl"><s:Body><trt:GetProfilesResponse>'
                '<trt:Profiles token="prof0"><trt:Name>main</trt:Name>'
                '</trt:Profiles></trt:GetProfilesResponse></s:Body>'
                '</s:Envelope>'))
        elif "GetStreamUri" in body:
            self._send(200, (
                '<s:Envelope xmlns:s="http://www.w3.org/2003/05/'
                'soap-envelope" xmlns:trt="http://www.onvif.org/ver10/'
                'media/wsdl"><s:Body><trt:GetStreamUriResponse>'
                f'<trt:MediaUri><trt:Uri>rtsp://127.0.0.1:'
                f'{self.server.rtsp_port}/stream</trt:Uri>'
                '</trt:MediaUri></trt:GetStreamUriResponse></s:Body>'
                '</s:Envelope>'))
        elif "GetSnapshotUri" in body:
            self._send(200, (
                '<s:Envelope xmlns:s="http://www.w3.org/2003/05/'
                'soap-envelope" xmlns:trt="http://www.onvif.org/ver10/'
                'media/wsdl"><s:Body><trt:GetSnapshotUriResponse>'
                f'<trt:MediaUri><trt:Uri>{base}/snapshot.jpg</trt:Uri>'
                '</trt:MediaUri></trt:GetSnapshotUriResponse></s:Body>'
                '</s:Envelope>'))
        else:
            self._send(400, "")

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler naming
        if self.path == "/snapshot.jpg":
            self._send(200, _jpeg_bytes(320, 240, (200, 30, 30)),
                       "image/jpeg")
        elif self.path == "/mjpeg":
            boundary = "FRAME"
            parts = b""
            for colour in ((200, 30, 30), (30, 200, 30), (30, 30, 200)):
                frame = _jpeg_bytes(160, 120, colour)
                parts += (f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                          f"Content-Length: {len(frame)}\r\n\r\n"
                          ).encode() + frame + b"\r\n"
            parts += f"--{boundary}--\r\n".encode()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                f"multipart/x-mixed-replace; boundary={boundary}")
            self.send_header("Content-Length", str(len(parts)))
            self.end_headers()
            self.wfile.write(parts)
        elif self.path == "/digest.jpg":
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Digest "):
                self.send_response(401)
                self.send_header(
                    "WWW-Authenticate",
                    'Digest realm="testcam", nonce="n0nc3", qop="auth", '
                    'opaque="opaque123"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            expect, _f = _digest_expected(auth, "GET", self.path,
                                          CAM_PASSWORD)
            fields = dict(re.findall(r'(\w+)="([^"]*)"', auth))
            if expect != fields.get("response"):
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send(200, _jpeg_bytes(320, 240, (30, 30, 200)),
                       "image/jpeg")
        else:
            self._send(404, "")


class _FakeDiscoveryResponder(threading.Thread):
    """UDP responder: answers one WS-Discovery Probe, then stops."""

    def __init__(self, port, xaddr):
        super().__init__(daemon=True)
        self.port, self.xaddr = port, xaddr
        self.stop = threading.Event()
        self.bound = threading.Event()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.bind(("127.0.0.1", self.port))
        self.bound.set()
        connected = False
        try:
            while not self.stop.is_set():
                try:
                    data, addr = sock.recvfrom(65535)
                except (socket.timeout, TimeoutError):
                    continue
                if b"Probe" not in data:
                    continue
                if not connected:
                    # This sandbox blocks UDP sendto; a connected
                    # socket may send. The probe is the only peer.
                    sock.connect(addr)
                    connected = True
                reply = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/'
                    'soap-envelope" xmlns:d="http://schemas.xmlsoap.org/'
                    'ws/2005/04/discovery"><s:Body><d:ProbeMatches>'
                    '<d:ProbeMatch><d:EndpointReference><wsa:Address'
                    ' xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/'
                    'addressing">urn:uuid:fake-camera</wsa:Address>'
                    '</d:EndpointReference>'
                    '<d:Types>tds:Device</d:Types>'
                    f'<d:XAddrs>{self.xaddr}</d:XAddrs>'
                    '<d:Scopes>onvif://www.onvif.org/name/FakeCam'
                    '</d:Scopes></d:ProbeMatch></d:ProbeMatches>'
                    '</s:Body></s:Envelope>')
                sock.send(reply.encode())
        finally:
            sock.close()


class Go2rtcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rtsp_server = _FakeRTSPServer(("127.0.0.1", 0),
                                          _FakeRTSPHandler)
        threading.Thread(target=cls.rtsp_server.serve_forever,
                         daemon=True).start()
        cls.rtsp_port = cls.rtsp_server.server_address[1]
        cls.http_server = ThreadingHTTPServer(("127.0.0.1", 0),
                                              _FakeONVIFHandler)
        cls.http_server.rtsp_port = cls.rtsp_port
        threading.Thread(target=cls.http_server.serve_forever,
                         daemon=True).start()
        cls.http_port = cls.http_server.server_address[1]
        cls.rtsp_url = f"rtsp://127.0.0.1:{cls.rtsp_port}/stream"
        cls.snap_url = f"http://127.0.0.1:{cls.http_port}/snapshot.jpg"
        cls.mjpeg_url = f"http://127.0.0.1:{cls.http_port}/mjpeg"
        cls.digest_url = f"http://127.0.0.1:{cls.http_port}/digest.jpg"

    @classmethod
    def tearDownClass(cls):
        cls.rtsp_server.shutdown()
        cls.rtsp_server.server_close()
        cls.http_server.shutdown()
        cls.http_server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(
            db_path=str(Path(self.tmp.name) / "facts.db"))
        self._orig_helpers_store = _helpers_store
        import core.skills_admin_helpers as helpers
        self._helpers = helpers
        helpers._store = lambda: self.store
        self._orig_ic_store = ic._store
        ic._store = lambda: self.store
        self._orig_gate = ic.ensure_capture_allowed
        ic.ensure_capture_allowed = lambda kind: (True, "")
        self.cam = "Front door"
        ic.ip_camera(action="add", camera=self.cam, url=self.rtsp_url,
                     snapshot_url=self.snap_url, kind="test")

    def tearDown(self):
        self._helpers._store = self._orig_helpers_store
        ic._store = self._orig_ic_store
        ic.ensure_capture_allowed = self._orig_gate
        self.tmp.cleanup()

    # -- unit-level: SPS / SDP / NAL reassembly ----------------------

    def test_sps_parse_real_fixture(self):
        self.assertEqual(ic._parse_sps(REAL_SPS), (100, 22, 640, 360))

    def test_sps_parse_rejects_garbage(self):
        self.assertIsNone(ic._parse_sps(b"\x00\x01"))
        self.assertIsNone(ic._parse_sps(b""))

    def test_sdp_parse(self):
        sdp = ("v=0\r\nm=video 0 RTP/AVP 96\r\n"
               "a=rtpmap:96 H264/90000\r\n"
               f"a=fmtp:96 packetization-mode=1; sprop-parameter-sets="
               f"{REAL_SPS_B64},{REAL_PPS_B64}\r\n"
               "a=control:trackID=0\r\n")
        out = ic._parse_sdp(sdp)
        self.assertEqual(out["codec"], "H264")
        self.assertEqual(out["payload_type"], 96)
        self.assertEqual(out["control"], "trackID=0")
        self.assertEqual(out["sps_b64"], REAL_SPS_B64)

    def test_digest_header_roundtrip(self):
        challenge = {"realm": "testcam", "nonce": "n0nc3",
                     "qop": "auth", "opaque": "opaque123"}
        header = ic._digest_auth("GET", "/digest.jpg", challenge,
                                 "admin", CAM_PASSWORD)
        expect, _f = _digest_expected(header, "GET", "/digest.jpg",
                                      CAM_PASSWORD)
        fields = dict(re.findall(r'(\w+)="([^"]*)"', header))
        self.assertEqual(expect, fields["response"])
        self.assertIn('username="admin"', header)

    def test_nal_reassembly_fu_a_and_stap_a(self):
        # FU-A: one IDR slice split across two RTP payloads.
        idr = bytes([0x65]) + bytes(range(200))
        half = len(idr) // 2
        frag1 = bytes([0x7C, 0x80 | 0x05]) + idr[1:half + 1]
        frag2 = bytes([0x7C, 0x40 | 0x05]) + idr[half + 1:]
        nals, _s, _p = ic._reassemble_h264([(0, frag1), (0, frag2)])
        self.assertEqual(nals, [idr])
        # STAP-A: two small NALs in one RTP payload.
        stap = bytes([0x18]) + len(REAL_SPS_NAL).to_bytes(2, "big") \
            + REAL_SPS_NAL + len(REAL_PPS_NAL).to_bytes(2, "big") \
            + REAL_PPS_NAL
        nals, sps, pps = ic._reassemble_h264([(0, stap)])
        self.assertEqual(nals, [REAL_SPS_NAL, REAL_PPS_NAL])
        self.assertEqual(sps, REAL_SPS)
        self.assertEqual(pps, REAL_PPS_NAL[1:])

    # -- RTSP probe + record against the fake server ------------------

    def test_rtsp_probe_fake_server(self):
        out = ic.ip_camera(action="rtsp", camera=self.cam,
                           duration_seconds=2)
        self.assertIn("H264", out)
        self.assertIn("640×360", out)
        self.assertIn("frame(s)", out)
        self.assertNotIn("could not reach", out)

    def test_rtsp_probe_digest_auth(self):
        self.server = _FakeRTSPServer(("127.0.0.1", 0), _FakeRTSPHandler)
        self.server.require_digest = True
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        try:
            url = (f"rtsp://127.0.0.1:"
                   f"{self.server.server_address[1]}/stream")
            ic.ip_camera(action="add", camera="Digest cam", url=url,
                         snapshot_url="")
            out = ic.ip_camera(action="rtsp", camera="Digest cam",
                               username="admin", password=CAM_PASSWORD,
                               duration_seconds=2)
            self.assertIn("640×360", out)
            bad = ic.ip_camera(action="rtsp", camera="Digest cam",
                               username="admin", password="wrong",
                               duration_seconds=2)
            self.assertIn("could not reach", bad)
            self.assertNotIn("640×360", bad)
        finally:
            self.server.shutdown()

    def test_record_writes_annexb(self):
        dest = str(Path(self.tmp.name) / "rec.h264")
        out = ic.ip_camera(action="record", camera=self.cam, path=dest,
                           duration_seconds=2)
        self.assertIn("Recorded", out)
        self.assertIn(".h264", out)
        data = Path(dest).read_bytes()
        self.assertGreater(len(data), 0)
        units = ic._split_annexb(data)
        self.assertEqual(units[0][0], 7)  # SPS first
        self.assertEqual(units[0][1], REAL_SPS_NAL)
        self.assertEqual(units[1][0], 8)  # PPS second
        self.assertIn("not an .mp4", out)

    # -- snapshot / MJPEG against the fake HTTP server -----------------

    def test_snapshot_fake_server(self):
        dest = str(Path(self.tmp.name) / "snap.jpg")
        out = ic.ip_camera(action="snapshot", camera=self.cam, path=dest)
        self.assertIn("Snapshot from", out)
        self.assertIn("320×240", out)
        from PIL import Image
        with Image.open(dest) as img:
            self.assertEqual(img.size, (320, 240))

    def test_snapshot_digest_auth(self):
        ic.ip_camera(action="add", camera="Digest snap",
                     url=self.rtsp_url, snapshot_url=self.digest_url)
        out = ic.ip_camera(action="snapshot", camera="Digest snap",
                           username="admin", password=CAM_PASSWORD,
                           path=str(Path(self.tmp.name) / "d.jpg"))
        self.assertIn("Snapshot from", out)
        bad = ic.ip_camera(action="snapshot", camera="Digest snap",
                           username="admin", password="wrong",
                           path=str(Path(self.tmp.name) / "d2.jpg"))
        self.assertIn("Snapshot failed", bad)

    def test_mjpeg_stream(self):
        ic.ip_camera(action="add", camera="MJPEG cam", url=self.mjpeg_url,
                     snapshot_url="")
        out = ic.ip_camera(action="mjpeg", camera="MJPEG cam",
                           path=str(Path(self.tmp.name) / "last.jpg"),
                           duration_seconds=3)
        self.assertIn("3 frame(s)", out)
        self.assertIn("160×120", out)
        from PIL import Image
        with Image.open(str(Path(self.tmp.name) / "last.jpg")) as img:
            self.assertEqual(img.size, (160, 120))

    # -- ONVIF discovery + stream URIs ---------------------------------

    def test_onvif_discover_fake(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        responder = _FakeDiscoveryResponder(
            port, f"http://127.0.0.1:{self.http_port}/onvif/device_service")
        responder.start()
        self.assertTrue(responder.bound.wait(timeout=5))
        try:
            out = ic.ip_camera(action="discover",
                               _discover_target=("127.0.0.1", port))
            self.assertIn("ONVIF cameras found", out)
            self.assertIn(f"http://127.0.0.1:{self.http_port}"
                          "/onvif/device_service", out)
        finally:
            responder.stop.set()
            responder.join(timeout=2)

    def test_onvif_stream_uris_fake(self):
        device = (f"http://127.0.0.1:{self.http_port}"
                  "/onvif/device_service")
        rtsp_uri, snap_uri = ic._onvif_stream_uris(device)
        self.assertEqual(rtsp_uri, self.rtsp_url)
        self.assertEqual(snap_uri, self.snap_url)

    # -- registry -------------------------------------------------------

    def test_add_list_remove_registry(self):
        out = ic.ip_camera(action="list")
        self.assertIn("Front door", out)
        self.assertIn(self.rtsp_url, out)
        removed = ic.ip_camera(action="remove", camera=self.cam)
        self.assertIn("removed", removed)
        self.assertNotIn("Front door",
                         ic.ip_camera(action="list"))

    def test_add_rejects_bad_urls(self):
        self.assertIn("rtsp://", ic.ip_camera(
            action="add", camera="Bad", url="ftp://example.com/x"))
        self.assertIn("http://", ic.ip_camera(
            action="add", camera="Bad2", url=self.rtsp_url,
            snapshot_url="ftp://example.com/x"))
        self.assertIn("name", ic.ip_camera(action="add", camera="",
                                           url=self.rtsp_url))

    def test_add_accepts_mjpeg_only_camera(self):
        out = ic.ip_camera(action="add", camera="MJPEG only",
                           url=self.mjpeg_url, snapshot_url="")
        self.assertIn("added", out)
        self.assertIn(self.mjpeg_url, out)

    def test_no_password_stored(self):
        facts = [f for f in self.store.get_all_facts()
                 if f.get("category") == "ip_camera"]
        self.assertEqual(len(facts), 1)
        payload = json.loads(facts[0]["value"])
        self.assertNotIn("password", payload)
        self.assertNotIn(CAM_PASSWORD, facts[0]["value"])

    def test_unknown_camera(self):
        out = ic.ip_camera(action="snapshot", camera="No such cam")
        self.assertIn("No camera named", out)
        self.assertIn("Front door", out)

    # -- privacy contract -------------------------------------------------

    def test_gate_refusal_touches_nothing(self):
        ic.ensure_capture_allowed = lambda kind: (False, "camera denied")
        for action in ("snapshot", "mjpeg", "rtsp", "record"):
            out = ic.ip_camera(action=action, camera=self.cam,
                               path=str(Path(self.tmp.name) / "x.bin"),
                               duration_seconds=1)
            self.assertIn("refused", out)
        self.assertEqual(list(Path(self.tmp.name).glob("x.bin")), [])

    def test_password_never_echoed(self):
        out = ic.ip_camera(action="snapshot", camera=self.cam,
                           username="admin", password="SECRET123",
                           path=str(Path(self.tmp.name) / "s.jpg"))
        self.assertNotIn("SECRET123", out)
        bad = ic.ip_camera(action="snapshot", camera="No such cam",
                           username="admin", password="SECRET123")
        self.assertNotIn("SECRET123", bad)

    def test_duration_bounds(self):
        self.assertIn("between 1 and 30", ic.ip_camera(
            action="rtsp", camera=self.cam, duration_seconds=0))
        self.assertIn("between 1 and 30", ic.ip_camera(
            action="rtsp", camera=self.cam, duration_seconds=31))
        self.assertIn("whole number", ic.ip_camera(
            action="rtsp", camera=self.cam, duration_seconds="soon"))

    # -- census pins (new totals after this build) ------------------------

    def test_census_pins(self):
        from core.funnel.tools_registry import tools
        from core.funnel import tools_registry as _tr
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 769)  # INTENTIONAL — R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2121)  # INTENTIONAL — R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        snap = json.load(open(os.path.join(
            os.path.dirname(__file__), "_classification_snapshot.json")))
        self.assertEqual(len(snap), 769)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(snap["ip_camera"],
                         {"confirm": False, "free": "capture",
                          "read": False})


if __name__ == "__main__":
    unittest.main()

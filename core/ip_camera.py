"""IP cameras: find them, add them, see them, check them, record them — own code.

Parent-ruled (licence, 2026-11-11): AlexxIT/go2rtc (MIT, LICENSE from
master, 21 lines, "MIT License / Copyright (c) 2022 Alexey Khit", read in
full 2026-11-11) is INSPIRATION ONLY. No code, binary, schema, text or
package is adopted from it; this module is written from scratch in stdlib
Python over the tree's own shapes (core/capture_guard.py's camera gate,
core/skills_admin_helpers.py's memory-facts registry, Pillow for JPEG
frames). Ideas are not copyrighted, so no MIT duty is triggered by a
from-scratch rebuild. The "go2rtc" name is not used here: the tool is the
generic phrase "ip_camera", and no package, fork, service or hosted
offering is named after the original. The Go binary is NOT adopted and no
Go runtime is added: there is no second app, only this module inside VYUK.

What this is, and what it is not — one verdict per go2rtc capability:
- RTSP ingest: BUILT, packet-level. RTSP DESCRIBE / SETUP / PLAY over
  TCP (stdlib socket), SDP parsed for codec and control URLs, RTP
  packets read interleaved, H.264 NAL units reassembled (single, STAP-A,
  FU-A), SPS parsed for resolution. No H.264 decode exists in the Python
  stdlib, so this half reports stream health (codec, resolution, fps,
  bitrate, packets) and records the raw bitstream — it does not show
  H.264 video, and the docstring says so rather than claiming otherwise.
- ONVIF discovery/control: BUILT. WS-Discovery Probe over UDP multicast
  (stdlib socket + xml.etree) finds cameras on the LAN; ONVIF
  GetProfiles / GetStreamUri / GetSnapshotUri over SOAP/HTTP (urllib)
  return the RTSP and snapshot URIs. No vendor-private protocol is
  built (no Tapo/Ring/Wyze/Nest cloud APIs — those are vendor clouds).
- MJPEG / HTTP snapshot: BUILT — the viewable half. An HTTP snapshot
  URL returns one JPEG (Pillow verifies it and reports its dimensions);
  an MJPEG stream returns JPEG frames one by one (Pillow verifies each).
  These are real pictures, not a claim: the frame is saved to a file
  the user names (or ~/Pictures by the webcam_snapshot precedent) and
  the tool reports the path, dimensions and byte count.
- Camera recording: BUILT, raw bitstream only. The RTSP ingest writes
  Annex B H.264 (SPS + PPS first, then reassembled NAL units) to a
  .h264 file. No MP4 muxer exists in stdlib, so the file is a raw
  bitstream — playable in VLC/ffplay, not an .mp4 — and the tool says
  so. The FFmpeg binary-provenance audit is owed and gates any
  ffmpeg-based mux; this half does not route around it.
- WebRTC restream: NOT BUILT. It needs aiortc + PyAV, which are
  declared in requirements.txt (aiortc>=1.16.0, parent-approved
  2026-11-09) but importable in NEITHER Linux interpreter, and a
  from-scratch WebRTC stack (DTLS-SRTP + ICE + SDP) is not a one-batch
  stdlib job. The ruling owed is whether to install the declared
  aiortc stack on the buyer's machine and verify the restream there.
- HLS: NOT BUILT. HLS ingest (playlist + segments) is stdlib-writable,
  but the segments are H.264 (no decoder) and the GUI has no HLS
  player (PySide6.QtMultimedia is NOT importable in the Qt venv), so
  a playlist parse alone would report segment URLs, not a viewable
  stream. Too thin to ship as a capability.

Privacy contract (standing, non-negotiable): an IP-camera feed IS a
camera. Before any action touches a feed (snapshot, mjpeg, rtsp,
record), the handler calls capture_guard.ensure_capture_allowed
("camera") — the durable camera denial refuses first, a consult
failure fails closed, and the refusal is returned, never worked
around. A feed runs only for the bounded duration the user asks for
(default 5 s, max 30 s) — never continuously, never in the background.
Frames never leave the machine: the snapshot / stream / record write
to files the user names (or the ~/Pictures / ~/Videos defaults), and
no frame is sent to any cloud service. Credentials (username /
password) are supplied per call, never stored (the registry payload
has no password field), never echoed in any returned string, never
logged. A URL that arrives with a username/password embedded
(rtsp://user:pass@host/...) has the credentials stripped before it
is stored or shown — give credentials per call instead. Discovery sends a user-initiated LAN probe and reads no
frames, so it is not gated — but it is a read, and it names what it
found, nothing more. Adding / listing / removing cameras touches only
VYUK's own registry, not a feed, so those are not gated either.

Classification: the tool is effectful (read=False), confirm=False,
free='capture' — the webcam_snapshot precedent (tools_gate_free.py):
the capture's product is files the user asked for, the registry
writes are to VYUK's own store, and the consent lives in the module
via ensure_capture_allowed, not the registry gate.
"""

import base64
import datetime
import hashlib
import json
import re
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .capture_guard import ensure_capture_allowed
from .skills_admin_helpers import _load, _nonempty, _save, _store

try:
    from PIL import Image as _PILImage
except Exception:  # noqa: BLE001 — Pillow is declared, not guaranteed
    _PILImage = None

_CAM_CATEGORY = "ip_camera"
_ACTIONS = ("discover", "add", "snapshot", "mjpeg", "rtsp", "record",
            "list", "remove")
_RTSP_PORT = 554
_WS_DISCOVERY_ADDR = "239.255.255.250"
_WS_DISCOVERY_PORT = 3702
_MAX_DURATION_S = 30
_DEFAULT_DURATION_S = 5
_MAX_FILE_BYTES = 5_000_000


# ------------------------------------------------------------------ auth ---

def _basic_auth(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode()
    return f"Basic {token}"


def _parse_www_authenticate(header: str):
    """(scheme, params) from a WWW-Authenticate header, or (None, {})."""
    if not header:
        return None, {}
    m = re.match(r"\s*(\w+)\s+(.*)", header.strip())
    if not m:
        return None, {}
    # findall would return 3-tuples here (quoted OR unquoted value),
    # which dict() rejects — so walk the matches explicitly.
    params = {}
    for m2 in re.finditer(r'(\w+)=(?:"([^"]*)"|([^\s,]+))', m.group(2)):
        params[m2.group(1)] = (m2.group(2) if m2.group(2) is not None
                               else m2.group(3))
    return m.group(1).lower(), params


def _digest_auth(method: str, uri: str, challenge: dict, user: str,
                 password: str, nc: int = 1) -> str:
    """RFC 2617 Digest, MD5, qop=auth. Returns the Authorization value."""
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    qop = challenge.get("qop", "")
    opaque = challenge.get("opaque", "")
    cnonce = secrets.token_hex(8)
    ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    if qop:
        nc_txt = "%08x" % nc
        resp = hashlib.md5(
            f"{ha1}:{nonce}:{nc_txt}:{cnonce}:auth:{ha2}".encode()
        ).hexdigest()
        out = (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
               f'uri="{uri}", response="{resp}", algorithm=MD5, '
               f'cnonce="{cnonce}", opaque="{opaque}", qop=auth, nc={nc_txt}')
    else:
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        out = (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
               f'uri="{uri}", response="{resp}"')
    return out


def _http_get(url: str, user: str = "", password: str = "",
              timeout: float = 10.0, headers: dict | None = None):
    """GET with Basic/Digest retry. Returns (status, headers, body).

    Raises urllib.error.URLError on a network failure and
    urllib.error.HTTPError on a non-401 HTTP failure. A 401 with no
    usable challenge raises HTTPError too — the caller reports it
    honestly, never as an empty result.
    """
    req_headers = dict(headers or {})
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code != 401 or not user:
            raise
        scheme, challenge = _parse_www_authenticate(
            exc.headers.get("WWW-Authenticate", ""))
        if scheme == "basic":
            req_headers["Authorization"] = _basic_auth(user, password)
        elif scheme == "digest":
            req_headers["Authorization"] = _digest_auth(
                "GET", urllib.parse.urlparse(url).path or "/", challenge,
                user, password)
        else:
            raise
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()


def _http_post_soap(url: str, action: str, body: str, user: str = "",
                    password: str = "", timeout: float = 10.0) -> bytes:
    """SOAP POST with Basic/Digest retry. Returns the response body."""
    data = body.encode("utf-8")
    headers = {"Content-Type": "application/soap+xml; charset=utf-8",
               "SOAPAction": action}
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code != 401 or not user:
            raise
        scheme, challenge = _parse_www_authenticate(
            exc.headers.get("WWW-Authenticate", ""))
        path = urllib.parse.urlparse(url).path or "/"
        if scheme == "basic":
            headers["Authorization"] = _basic_auth(user, password)
        elif scheme == "digest":
            headers["Authorization"] = _digest_auth(
                "POST", path, challenge, user, password)
        else:
            raise
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()


# ------------------------------------------------------------- bit reader ---

class _BitReader:
    """Bits over RBSP bytes (emulation-prevention bytes removed)."""

    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def bit(self) -> int:
        if self.pos >> 3 >= len(self.data):
            raise ValueError("SPS runs past its end")
        b = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return b

    def bits(self, n: int) -> int:
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v

    def ue(self) -> int:
        zeros = 0
        while self.bit() == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("Exp-Golomb code too long")
        return (1 << zeros) - 1 + self.bits(zeros)

    def se(self) -> int:
        code = self.ue()
        return (code + 1) // 2 if code % 2 == 1 else -(code // 2)


def _strip_emulation_prevention(raw: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(raw):
        if (raw[i:i + 3] == b"\x00\x00\x03" and i + 3 < len(raw)
                and raw[i + 3] <= 3):
            out += b"\x00\x00"
            i += 3
        else:
            out.append(raw[i])
            i += 1
    return bytes(out)


def _parse_sps(payload: bytes):
    """H.264 SPS RBSP (without the NAL header) -> (profile, level,
    width, height) or None when unparseable. Verified against a real
    encoder SPS (ffmpeg libx264, 640x360, crop bottom 4)."""
    try:
        r = _BitReader(_strip_emulation_prevention(payload))
        profile = r.bits(8)
        r.bits(8)  # constraint flags + reserved
        level = r.bits(8)
        r.ue()  # seq_parameter_set_id
        if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138,
                       139, 134, 135):
            chroma = r.ue()
            if chroma == 3:
                r.bit()  # separate_colour_plane_flag
            r.ue()  # bit_depth_luma_minus8
            r.ue()  # bit_depth_chroma_minus8
            r.bit()  # qpprime_y_zero_transform_bypass_flag
            if r.bit():  # seq_scaling_matrix_present_flag
                for i in range(8 if chroma != 3 else 12):
                    if not r.bit():
                        continue
                    size = 16 if i < 6 else 64
                    last = nxt = 8
                    for _ in range(size):
                        if nxt != 0:
                            delta = r.se()
                            nxt = (last + delta) & 0xFF
                        last = nxt if nxt else last
        r.ue()  # log2_max_frame_num_minus4
        poc = r.ue()  # pic_order_cnt_type
        if poc == 0:
            r.ue()  # log2_max_pic_order_cnt_lsb_minus4
        elif poc == 1:
            r.bit()  # delta_pic_order_always_zero_flag
            r.se()  # offset_for_non_ref_pic
            r.se()  # offset_for_top_to_bottom_field
            num_ref = r.ue()  # num_ref_frames_in_pic_order_cnt_cycle
            for _ in range(num_ref):
                r.se()  # offset_for_ref_frame
        r.ue()  # max_num_ref_frames
        r.bit()  # gaps_in_frame_num_value_allowed_flag
        w_mbs = r.ue()  # pic_width_in_mbs_minus1
        h_map = r.ue()  # pic_height_in_map_units_minus1
        frame_mbs_only = r.bit()
        if not frame_mbs_only:
            r.bit()  # mb_adaptive_frame_field_flag
        r.bit()  # direct_8x8_inference_flag
        crop = (0, 0, 0, 0)
        if r.bit():  # frame_cropping_flag
            crop = (r.ue(), r.ue(), r.ue(), r.ue())
        width = (w_mbs + 1) * 16 - (crop[0] + crop[1]) * 2
        height = ((2 - frame_mbs_only) * (h_map + 1) * 16
                  - (crop[2] + crop[3]) * 2)
        if width <= 0 or height <= 0 or width > 16384 or height > 16384:
            return None
        return profile, level, width, height
    except (ValueError, IndexError):
        return None


def _split_annexb(data: bytes):
    """Raw Annex B bytes -> [(nal_type, nal_bytes_without_header)]."""
    out = []
    start = None
    i = 0
    while i + 3 < len(data):
        if data[i:i + 3] == b"\x00\x00\x01" or data[i:i + 4] == b"\x00\x00\x00\x01":
            sc = 4 if data[i:i + 4] == b"\x00\x00\x00\x01" else 3
            if start is not None and i > start:
                nal = data[start:i]
                if nal:
                    out.append((nal[0] & 0x1F, nal))
            start = i + sc
            i += sc
        else:
            i += 1
    if start is not None and start < len(data):
        nal = data[start:]
        if nal:
            out.append((nal[0] & 0x1F, nal))
    return out


# ------------------------------------------------------------------ SDP ---

def _parse_sdp(text: str):
    """SDP -> {payload_type, codec, control, sps_b64, pps_b64} or {}."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("m=video"):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    out["payload_type"] = int(parts[3])
                except ValueError:
                    pass
        elif line.startswith("a=rtpmap:"):
            m = re.match(r"a=rtpmap:(\d+)\s+(\S+)/(\d+)", line)
            if m:
                out["codec"] = m.group(2).upper()
        elif line.startswith("a=fmtp:"):
            m = re.search(r"sprop-parameter-sets=([A-Za-z0-9+/=]+)"
                          r"(?:,([A-Za-z0-9+/=]+))?", line)
            if m:
                out["sps_b64"] = m.group(1)
                if m.group(2):
                    out["pps_b64"] = m.group(2)
        elif line.startswith("a=control:"):
            out["control"] = line[len("a=control:"):].strip()
    return out


# ----------------------------------------------------------------- RTSP ---

class _RTSPClient:
    """Minimal RTSP over TCP (interleaved RTP), stdlib only.

    Speaks OPTIONS / DESCRIBE / SETUP / PLAY / TEARDOWN with Basic /
    Digest auth. Reads interleaved RTP packets for a bounded time.
    It never decodes video: it reassembles H.264 NAL units and parses
    the SPS for resolution.
    """

    def __init__(self, host: str, port: int, user: str = "",
                 password: str = "", timeout: float = 10.0):
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.timeout = timeout
        self._sock = None
        self._buf = b""
        self._cseq = 0
        self._session = ""
        self._auth_tried = False

    def connect(self) -> None:
        self._sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("RTSP connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_line(self) -> bytes:
        while b"\r\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("RTSP connection closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\r\n", 1)
        return line

    def _request(self, method: str, uri: str,
                 extra_headers: dict | None = None):
        """One request -> (status, headers, body). Retries once with
        auth on a 401. Raises on a network failure or a non-401
        failure the caller must report honestly."""
        self._cseq += 1
        headers = {"CSeq": str(self._cseq),
                   "User-Agent": "VYUK-ip-camera/1.0"}
        if self._session:
            headers["Session"] = self._session
        headers.update(extra_headers or {})
        lines = [f"{method} {uri} RTSP/1.0"]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        self._sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        status, resp_headers, body = self._read_response()
        if status == 401 and not self._auth_tried and self.user:
            scheme, challenge = _parse_www_authenticate(
                resp_headers.get("www-authenticate", ""))
            if scheme in ("basic", "digest"):
                self._auth_tried = True
                if scheme == "basic":
                    auth = _basic_auth(self.user, self.password)
                else:
                    auth = _digest_auth(method, uri, challenge,
                                        self.user, self.password)
                return self._request_with_auth(method, uri, auth,
                                               extra_headers)
        return status, resp_headers, body

    def _request_with_auth(self, method: str, uri: str, auth: str,
                           extra_headers: dict | None = None):
        self._cseq += 1
        headers = {"CSeq": str(self._cseq),
                   "User-Agent": "VYUK-ip-camera/1.0",
                   "Authorization": auth}
        if self._session:
            headers["Session"] = self._session
        headers.update(extra_headers or {})
        lines = [f"{method} {uri} RTSP/1.0"]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        self._sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        return self._read_response()

    def _read_response(self):
        """Read one RTSP response, skipping any interleaved RTP
        packets that arrive first. Returns (status, headers, body).
        """
        while True:
            first = self._read_exact(1)
            if first == b"$":
                # Interleaved RTP: channel (1) + length (2), then data.
                self._read_exact(1)
                length = int.from_bytes(self._read_exact(2), "big")
                self._read_exact(length)
                continue
            line = first + self._read_line()
            text = line.decode("iso-8859-1", "replace")
            if not text.startswith("RTSP/"):
                raise ValueError(f"not an RTSP response: {text!r}")
            status = int(text.split()[1])
            headers = {}
            while True:
                hline = self._read_line().decode("iso-8859-1", "replace")
                if not hline:
                    break
                if ":" in hline:
                    k, v = hline.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            body = b""
            length = int(headers.get("content-length", "0") or 0)
            if length:
                body = self._read_exact(length)
            if "session" in headers:
                self._session = headers["session"].split(";")[0].strip()
            return status, headers, body

    def describe(self, url: str):
        status, _headers, body = self._request("DESCRIBE", url, {
            "Accept": "application/sdp"})
        if status != 200:
            raise ConnectionError(
                f"RTSP DESCRIBE failed with status {status}")
        return body.decode("utf-8", "replace")

    def setup_play(self, control_url: str):
        status, _headers, _body = self._request("SETUP", control_url, {
            "Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
        if status != 200:
            raise ConnectionError(
                f"RTSP SETUP failed with status {status}")
        status, _headers, _body = self._request("PLAY", control_url, {
            "Range": "npt=0.000-"})
        if status != 200:
            raise ConnectionError(f"RTSP PLAY failed with status {status}")

    def teardown(self) -> None:
        try:
            self._request("TEARDOWN", "*")
        except Exception:  # noqa: BLE001 — teardown is best-effort
            pass

    def read_rtp(self, duration_s: float):
        """Read interleaved RTP packets for up to duration_s.
        Returns [(timestamp, payload_bytes)] for the video channel.
        """
        packets = []
        deadline = time.monotonic() + duration_s
        self._sock.settimeout(0.5)
        while time.monotonic() < deadline:
            try:
                first = self._read_exact(1)
            except (socket.timeout, TimeoutError):
                continue
            except (OSError, ConnectionError):
                break
            if first != b"$":
                # A stray RTSP response line — drain it.
                try:
                    self._read_line()
                except Exception:  # noqa: BLE001
                    break
                continue
            try:
                channel = self._read_exact(1)[0]
                length = int.from_bytes(self._read_exact(2), "big")
                data = self._read_exact(length)
            except (OSError, ConnectionError):
                break
            if channel != 0 or len(data) < 12:
                continue
            timestamp = int.from_bytes(data[4:8], "big")
            packets.append((timestamp, data[12:]))
        return packets


def _reassemble_h264(packets):
    """RTP payloads -> (complete NAL units, sps, pps).

    Handles single NALs, STAP-A aggregation and FU-A fragmentation.
    Returns ([nal_bytes_without_header...], sps_payload or None,
    pps_payload or None). A NAL unit here is the full NAL including
    its header byte, so Annex B writing is a start code + the unit.
    """
    nals = []
    sps = pps = None
    frag = None  # (start_bit, buffer) for a FU-A in progress
    for _ts, payload in packets:
        if not payload:
            continue
        nal_type = payload[0] & 0x1F
        if nal_type == 24:  # STAP-A
            off = 1
            while off + 2 <= len(payload):
                size = int.from_bytes(payload[off:off + 2], "big")
                off += 2
                unit = payload[off:off + size]
                off += size
                if unit:
                    nals.append(unit)
        elif nal_type == 28:  # FU-A
            if len(payload) < 2:
                continue
            fu_header = payload[1]
            start = bool(fu_header & 0x80)
            end = bool(fu_header & 0x40)
            real_type = fu_header & 0x1F
            header = bytes([(payload[0] & 0xE0) | real_type])
            if start:
                frag = bytearray(header + payload[2:])
            elif frag is not None:
                frag += payload[2:]
                if end:
                    nals.append(bytes(frag))
                    frag = None
        elif 1 <= nal_type <= 23:
            nals.append(payload)
    for unit in nals:
        t = unit[0] & 0x1F
        if t == 7 and sps is None:
            sps = unit[1:]
        elif t == 8 and pps is None:
            pps = unit[1:]
    return nals, sps, pps


# ---------------------------------------------------------------- ONVIF ---

def _soap_envelope(body: str, action_ns: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
        ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
        ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
        '<s:Header>' + action_ns + '</s:Header>'
        '<s:Body>' + body + '</s:Body></s:Envelope>')


def _ws_discovery_probe(timeout: float = 3.0,
                        target: tuple | None = None):
    """ONVIF WS-Discovery Probe. Returns [{xaddrs, types, scopes}].

    `target` (host, port) overrides the multicast group — the tests
    point it at a local responder; production uses the multicast
    group 239.255.255.250:3702.
    """
    message_id = f"uuid:{uuid.uuid4()}"
    probe = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
        ' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
        ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
        '<s:Header><wsa:Action>'
        'http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe'
        '</wsa:Action><wsa:MessageID>' + message_id + '</wsa:MessageID>'
        '<wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery'
        '</wsa:To></s:Header><s:Body><d:Probe>'
        '<d:Types>tds:Device</d:Types>'
        '</d:Probe></s:Body></s:Envelope>')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    try:
        if target is None:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            dest = (_WS_DISCOVERY_ADDR, _WS_DISCOVERY_PORT)
            sock.sendto(probe.encode("utf-8"), dest)
        else:
            # Unicast target (tests point it at a local responder):
            # a connected socket. A connected socket would drop the
            # multicast replies (they come from each camera's own
            # address, not the group), so the multicast path above
            # stays unconnected with sendto.
            sock.connect(target)
            sock.send(probe.encode("utf-8"))
        found = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _addr = sock.recvfrom(65535)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                continue
            ns = {"d": "http://schemas.xmlsoap.org/ws/2005/04/discovery"}
            for match in root.findall(".//d:ProbeMatch", ns):
                xaddrs = (match.findtext("d:XAddrs", "", ns) or "").split()
                types = match.findtext("d:Types", "", ns) or ""
                scopes = match.findtext("d:Scopes", "", ns) or ""
                if xaddrs:
                    found.append({"xaddrs": xaddrs, "types": types,
                                  "scopes": scopes})
        return found
    finally:
        sock.close()


def _onvif_call(url: str, action: str, body: str, user: str = "",
                password: str = "") -> ET.Element:
    raw = _http_post_soap(url, action, body, user, password)
    return ET.fromstring(raw)


def _onvif_stream_uris(device_url: str, user: str = "",
                       password: str = ""):
    """(rtsp_uri, snapshot_uri) from an ONVIF device service URL."""
    profiles_body = _soap_envelope(
        '<trt:GetProfiles/>',
        '<wsa:Action>http://www.onvif.org/ver10/media/wsdl/GetProfiles'
        '</wsa:Action>')
    root = _onvif_call(device_url,
                       "http://www.onvif.org/ver10/media/wsdl/GetProfiles",
                       profiles_body, user, password)
    ns = {"trt": "http://www.onvif.org/ver10/media/wsdl"}
    profile = root.find(".//trt:Profiles", ns)
    token = (profile.get("token") if profile is not None else "") or ""
    if not token:
        raise ValueError("the camera returned no ONVIF profile")
    stream_body = _soap_envelope(
        '<trt:GetStreamUri><trt:StreamSetup>'
        '<trt:Stream>RTP-Unicast</trt:Stream>'
        '<trt:Transport><trt:Protocol>RTSP</trt:Protocol>'
        '</trt:Transport></trt:StreamSetup>'
        f'<trt:ProfileToken>{token}</trt:ProfileToken>'
        '</trt:GetStreamUri>',
        '<wsa:Action>http://www.onvif.org/ver10/media/wsdl/GetStreamUri'
        '</wsa:Action>')
    root = _onvif_call(
        device_url,
        "http://www.onvif.org/ver10/media/wsdl/GetStreamUri",
        stream_body, user, password)
    uri = root.findtext(".//trt:Uri", "", ns) or ""
    snap_body = _soap_envelope(
        '<trt:GetSnapshotUri>'
        f'<trt:ProfileToken>{token}</trt:ProfileToken>'
        '</trt:GetSnapshotUri>',
        '<wsa:Action>http://www.onvif.org/ver10/media/wsdl/GetSnapshotUri'
        '</wsa:Action>')
    snapshot = ""
    try:
        root = _onvif_call(
            device_url,
            "http://www.onvif.org/ver10/media/wsdl/GetSnapshotUri",
            snap_body, user, password)
        snapshot = root.findtext(".//trt:Uri", "", ns) or ""
    except Exception:  # noqa: BLE001 — a missing snapshot URI is not fatal
        snapshot = ""
    return uri, snapshot


# ---------------------------------------------------------------- MJPEG ---

def _jpeg_info(data: bytes):
    """(width, height) of JPEG bytes via Pillow, or None."""
    if _PILImage is None or not data:
        return None
    try:
        import io
        with _PILImage.open(io.BytesIO(data)) as img:
            img.verify()
        with _PILImage.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:  # noqa: BLE001 — not a readable JPEG
        return None


def _fetch_snapshot(url: str, user: str = "", password: str = ""):
    """One JPEG from an HTTP snapshot URL -> (bytes, (w, h))."""
    _status, _headers, body = _http_get(url, user, password, timeout=15.0)
    if len(body) > _MAX_FILE_BYTES:
        raise ValueError("snapshot too large to keep in memory")
    info = _jpeg_info(body)
    if info is None:
        raise ValueError("the snapshot URL did not return a JPEG image")
    return body, info


def _fetch_mjpeg(url: str, user: str = "", password: str = "",
                 duration_s: float = 5.0):
    """MJPEG stream -> (frames, last_jpeg_bytes, last_size, fps).

    Reads a multipart/x-mixed-replace stream for up to duration_s.
    Each part must be a JPEG (Pillow-verified); a part that is not a
    JPEG is skipped, never claimed as a frame.
    """
    req = urllib.request.Request(url, method="GET")
    if user:
        req.add_header("Authorization", _basic_auth(user, password))
    try:
        resp = urllib.request.urlopen(req, timeout=15.0)
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and user:
            scheme, challenge = _parse_www_authenticate(
                exc.headers.get("WWW-Authenticate", ""))
            if scheme == "digest":
                req.add_header("Authorization", _digest_auth(
                    "GET", urllib.parse.urlparse(url).path or "/",
                    challenge, user, password))
            elif scheme == "basic":
                req.add_header("Authorization",
                               _basic_auth(user, password))
            else:
                raise
            resp = urllib.request.urlopen(req, timeout=15.0)
        else:
            raise
    content_type = resp.headers.get("Content-Type", "")
    m = re.search(r"boundary=([^\s;]+)", content_type)
    if not m:
        # A plain JPEG body is one frame, not a stream.
        body = resp.read()
        info = _jpeg_info(body)
        resp.close()
        if info is None:
            raise ValueError("the MJPEG URL did not return JPEG frames")
        return 1, body, info, 0.0
    boundary = ("--" + m.group(1).strip('"')).encode()
    buf = b""
    frames = 0
    last = b""
    last_info = None
    started = time.monotonic()
    deadline = started + duration_s
    try:
        while time.monotonic() < deadline:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                idx = buf.find(boundary, 1)
                if idx < 0:
                    break
                part, buf = buf[:idx], buf[idx:]
                head, _, body = part.partition(b"\r\n\r\n")
                if not body:
                    continue
                body = body.rstrip(b"\r\n")
                info = _jpeg_info(body)
                if info is None:
                    continue
                frames += 1
                last, last_info = body, info
                if len(buf) > _MAX_FILE_BYTES * 2:
                    buf = buf[-1024:]
    finally:
        resp.close()
    elapsed = max(time.monotonic() - started, 0.001)
    return frames, last, last_info, (frames / elapsed if frames else 0.0)


# --------------------------------------------------------------- registry ---

def _load_cameras():
    """(entries, broken): {name, url, snapshot_url, kind} dicts."""
    entries, broken = _load(_CAM_CATEGORY,
                            {"name": _nonempty, "url": _nonempty})
    for entry in entries:
        entry["name"] = str(entry.get("name") or "")
        entry["url"] = str(entry.get("url") or "")
        entry["snapshot_url"] = str(entry.get("snapshot_url") or "")
        entry["kind"] = str(entry.get("kind") or "")
    return entries, broken


def _find_camera(entries, name: str):
    want = (name or "").strip().lower()
    for entry in entries:
        if entry["name"].strip().lower() == want:
            return entry
    return None


def _camera_key(name: str) -> str:
    return (name or "").strip().lower()


def _strip_url_credentials(url: str):
    """(clean_url, stripped): a URL that arrives with a username /
    password embedded (rtsp://user:pass@host/...) has the credentials
    removed before it is stored or shown — credentials are per-call
    only, never in the registry and never in a returned string."""
    if not url:
        return url, False
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return url, False
    if parsed.username is None and parsed.password is None:
        return url, False
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.port:
        netloc += f":{parsed.port}"
    clean = parsed._replace(netloc=netloc).geturl()
    return clean, True


# ------------------------------------------------------------ RTSP probe ---

def _rtsp_probe(url: str, user: str = "", password: str = "",
                duration_s: float = 5.0, record_path: str | None = None):
    """Probe an RTSP URL for duration_s. Returns a dict:
    {codec, width, height, frames, packets, bytes, fps, bitrate_kbps,
    recorded_path, recorded_bytes, error}. With record_path, the raw
    H.264 Annex B bitstream is written (SPS + PPS first)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "rtsp":
        return {"error": f"{url!r} is not an rtsp:// URL."}
    host = parsed.hostname or ""
    port = parsed.port or _RTSP_PORT
    if not host:
        return {"error": f"no host in {url!r}."}
    client = _RTSPClient(host, port, user, password)
    out = {"codec": "", "width": None, "height": None, "frames": 0,
           "packets": 0, "bytes": 0, "fps": 0.0, "bitrate_kbps": 0.0,
           "recorded_path": "", "recorded_bytes": 0, "error": ""}
    try:
        client.connect()
        sdp_text = client.describe(url)
        sdp = _parse_sdp(sdp_text)
        out["codec"] = sdp.get("codec", "")
        control = sdp.get("control", "")
        if not control:
            control = url
        elif not control.lower().startswith("rtsp://"):
            control = urllib.parse.urljoin(url, control)
        sps = pps = None
        if sdp.get("sps_b64"):
            try:
                sps = base64.b64decode(sdp["sps_b64"])
            except Exception:  # noqa: BLE001 — bad base64 is not fatal
                sps = None
        if sdp.get("pps_b64"):
            try:
                pps = base64.b64decode(sdp["pps_b64"])
            except Exception:  # noqa: BLE001
                pps = None
        client.setup_play(control)
        started = time.monotonic()
        packets = client.read_rtp(duration_s)
        elapsed = max(time.monotonic() - started, 0.001)
        client.teardown()
        nals, stream_sps, stream_pps = _reassemble_h264(packets)
        if sps is None:
            sps = stream_sps
        if pps is None:
            pps = stream_pps
        parsed_sps = _parse_sps(sps) if sps else None
        if parsed_sps:
            _profile, _level, out["width"], out["height"] = parsed_sps
        timestamps = {ts for ts, _ in packets}
        total_bytes = sum(len(p) for _ts, p in packets)
        out["frames"] = len(timestamps)
        out["packets"] = len(packets)
        out["bytes"] = total_bytes
        out["fps"] = len(timestamps) / elapsed
        out["bitrate_kbps"] = (total_bytes * 8 / elapsed) / 1000.0
        if record_path:
            dest = Path(record_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                if sps:
                    fh.write(b"\x00\x00\x00\x01" + b"\x67" + sps)
                if pps:
                    fh.write(b"\x00\x00\x00\x01" + b"\x68" + pps)
                for unit in nals:
                    fh.write(b"\x00\x00\x00\x01" + unit)
            out["recorded_path"] = str(dest)
            out["recorded_bytes"] = dest.stat().st_size
        return out
    except (OSError, ConnectionError, ValueError) as exc:
        out["error"] = f"could not reach the RTSP stream ({exc})"
        return out
    finally:
        client.close()


def _default_picture_path(name: str) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "camera").strip("_")
    return Path.home() / "Pictures" / f"nova_ipcam_{safe}_{stamp}.jpg"


def _default_video_path(name: str) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "camera").strip("_")
    return Path.home() / "Videos" / f"nova_ipcam_{safe}_{stamp}.h264"


def _usage() -> str:
    return ("ip_camera — your own IP cameras: find them on the LAN, "
            "add one, see a snapshot, watch its MJPEG frames, check "
            "its RTSP stream, or record it. Actions: 'discover' finds "
            "ONVIF cameras on the local network; 'add' registers a "
            "camera (name, url — the rtsp:// stream URL, snapshot_url "
            "— the http:// JPEG snapshot URL, kind); 'snapshot' saves "
            "one JPEG frame; 'mjpeg' watches the MJPEG stream for a "
            "few seconds and saves the last frame; 'rtsp' checks the "
            "RTSP stream (codec, resolution, fps, bitrate); 'record' "
            "saves the raw H.264 bitstream (.h264, not .mp4); 'list' "
            "shows registered cameras; 'remove' forgets one. Camera "
            "usernames and passwords are given per call and never "
            "stored. WebRTC restream and HLS are not built: no "
            "decoder and no player in this build.")


def ip_camera(action: str = "", camera: str = "", url: str = "",
              snapshot_url: str = "", kind: str = "", username: str = "",
              password: str = "", duration_seconds=None, path: str = "",
              limit=None, detail: str = "", _discover_target=None) -> str:
    """One tool, eight actions. See the module docstring. Credentials
    are per-call only: they are never stored, never echoed, never
    logged. `_discover_target` is test-only (a (host, port) pair for
    the WS-Discovery probe)."""
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return _usage()
    if duration_seconds in ("", None):
        duration = _DEFAULT_DURATION_S
    else:
        try:
            duration = int(duration_seconds)
        except (TypeError, ValueError):
            return ("duration_seconds needs to be a whole number of "
                    f"seconds — got {duration_seconds!r}.")
        if duration < 1 or duration > _MAX_DURATION_S:
            return (f"duration_seconds needs to be between 1 and "
                    f"{_MAX_DURATION_S} — got {duration}. A feed "
                    "never runs continuously.")
    lim = 10
    if limit not in ("", None):
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 10
        lim = max(lim, 1)

    if action == "discover":
        try:
            found = _ws_discovery_probe(timeout=3.0,
                                        target=_discover_target)
        except OSError as exc:
            return f"ONVIF discovery failed ({exc})."
        if not found:
            return ("No ONVIF cameras answered on the local network. "
                    "Add one by hand with action='add', its name and "
                    "its rtsp:// URL.")
        lines = [f"ONVIF cameras found ({len(found)}):"]
        for item in found[:lim]:
            lines.append("  " + "; ".join(item["xaddrs"])
                         + (f" — {item['types']}" if item["types"] else ""))
            if item["scopes"]:
                lines.append(f"    scopes: {item['scopes']}")
        return "\n".join(lines)

    if action == "add":
        name = (camera or "").strip()
        stream_url = (url or "").strip()
        if not name:
            return "Give the camera a name — e.g. camera='Front door'."
        if not stream_url:
            return ("Give the camera's feed URL — an rtsp:// stream URL "
                    "e.g. url='rtsp://192.168.1.10:554/stream', or an "
                    "http(s):// MJPEG stream URL for a camera that only "
                    "offers MJPEG.")
        if not stream_url.lower().startswith(
                ("rtsp://", "http://", "https://")):
            return ("The feed URL needs to start with rtsp:// or "
                    f"http(s):// — got {stream_url!r}.")
        snap = (snapshot_url or "").strip()
        if snap and not snap.lower().startswith(("http://", "https://")):
            return ("The snapshot URL needs to start with http:// or "
                    f"https:// — got {snap!r}.")
        stream_url, stripped_feed = _strip_url_credentials(stream_url)
        snap, stripped_snap = _strip_url_credentials(snap)
        payload = {"name": name, "url": stream_url,
                   "snapshot_url": snap, "kind": (kind or "").strip()}
        existed = _save(_CAM_CATEGORY, _camera_key(name), payload)
        again = _load_cameras()[0]
        if _find_camera(again, name) is None:
            return (f"Camera '{name}' was written but the re-read did "
                    "not find it — nothing is claimed. Check the "
                    "store before retrying.")
        verb = "updated" if existed else "added"
        stripped_note = (" A username/password embedded in the URL "
                         "was removed — give credentials per call."
                         if stripped_feed or stripped_snap else "")
        return (f"Camera '{name}' {verb}: {stream_url}"
                + (f" (snapshot {snap})" if snap else "")
                + ". The password is never stored — give it per call."
                + stripped_note)

    if action == "list":
        try:
            entries, broken = _load_cameras()
        except Exception as exc:  # noqa: BLE001
            return f"Couldn't read the camera list: {exc}"
        if not entries:
            base = ("No IP cameras registered yet. Add one with "
                    "action='add', its name and its rtsp:// URL, or "
                    "find them with action='discover'.")
            if broken:
                base += f" ({broken} stored camera record(s) unreadable.)"
            return base
        entries.sort(key=lambda e: e["name"].lower())
        lines = [f"IP cameras ({len(entries)}):"]
        for e in entries[:lim]:
            feed, _ = _strip_url_credentials(e['url'])
            snap, _ = _strip_url_credentials(e['snapshot_url'])
            lines.append(f"  • {e['name']} — {feed}"
                         + (f" (snapshot {snap})"
                            if snap else "")
                         + (f" [{e['kind']}]" if e["kind"] else ""))
        if broken:
            lines.append(f"({broken} stored camera record(s) unreadable.)")
        return "\n".join(lines)

    if action == "remove":
        name = (camera or "").strip()
        if not name:
            return "Give the camera's name to remove."
        try:
            entries, _broken = _load_cameras()
        except Exception as exc:  # noqa: BLE001
            return f"Couldn't read the camera list: {exc}"
        if _find_camera(entries, name) is None:
            return f"No camera named {name!r}."
        fact = _store().get_fact(_CAM_CATEGORY, _camera_key(name))
        if fact is None or not _store().delete_fact(fact["id"]):
            return (f"Camera '{name}' could not be removed — check "
                    "the store before retrying.")
        again = _load_cameras()[0]
        if _find_camera(again, name) is not None:
            return (f"Camera '{name}' was removed but the re-read "
                    "still finds it — nothing is claimed.")
        return f"Camera '{name}' removed."

    # The four feed actions all need a registered camera first.
    name = (camera or "").strip()
    if not name:
        return ("Give the camera's name — e.g. camera='Front door'. "
                "List them with action='list'.")
    try:
        entries, _broken = _load_cameras()
    except Exception as exc:  # noqa: BLE001
        return f"Couldn't read the camera list: {exc}"
    entry = _find_camera(entries, name)
    if entry is None:
        known = [e["name"] for e in entries]
        hint = (f" Known cameras: {', '.join(known)}."
                if known else " No cameras registered yet.")
        return f"No camera named {name!r}.{hint}"

    # Privacy contract: any feed contact goes through the camera gate.
    allowed, why = ensure_capture_allowed("camera")
    if not allowed:
        return f"Camera feed refused: {why}"

    if action == "snapshot":
        snap = entry["snapshot_url"]
        if not snap:
            return (f"Camera '{name}' has no snapshot URL registered. "
                    "Add one with action='add' and snapshot_url=..., "
                    "or use action='mjpeg' on its stream.")
        try:
            data, (w, h) = _fetch_snapshot(snap, username, password)
        except (urllib.error.URLError, urllib.error.HTTPError,
                ValueError, OSError) as exc:
            return f"Snapshot failed for '{name}' ({exc})."
        dest = Path(str(path)).expanduser() if path else \
            _default_picture_path(name)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            got = dest.stat()
        except OSError as exc:
            return f"Snapshot fetched but could not be saved ({exc})."
        if got.st_size != len(data) or got.st_size == 0:
            return ("Snapshot fetched but the saved file does not "
                    "match — nothing is claimed.")
        return (f"Snapshot from '{name}': {w}×{h}, {len(data)} bytes, "
                f"saved to {dest}.")

    if action == "mjpeg":
        stream = entry["url"]
        if not stream.lower().startswith(("http://", "https://")):
            return (f"MJPEG needs an http(s):// stream URL — '{name}' "
                    f"has {stream!r}. Add the camera's MJPEG URL as its "
                    "feed URL, or use action='rtsp' for the RTSP stream.")
        try:
            frames, last, info, fps = _fetch_mjpeg(
                stream, username, password, duration_s=duration)
        except (urllib.error.URLError, urllib.error.HTTPError,
                ValueError, OSError) as exc:
            return (f"MJPEG stream failed for '{name}' ({exc}). "
                    "Many cameras expose MJPEG only on a separate "
                    "http:// URL — register it as the snapshot URL "
                    "and use action='snapshot', or check the stream "
                    "with action='rtsp'.")
        if frames == 0 or last is None or info is None:
            return (f"No JPEG frames arrived from '{name}' in "
                    f"{duration} s — the stream may not be MJPEG.")
        dest = Path(str(path)).expanduser() if path else \
            _default_picture_path(name)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(last)
            got = dest.stat()
        except OSError as exc:
            return f"Frames arrived but the last one could not be saved ({exc})."
        if got.st_size != len(last) or got.st_size == 0:
            return ("Frames arrived but the saved file does not match "
                    "— nothing is claimed.")
        return (f"MJPEG from '{name}': {frames} frame(s) in "
                f"{duration} s ({fps:.1f} fps), last frame "
                f"{info[0]}×{info[1]}, saved to {dest}.")

    if action == "rtsp":
        result = _rtsp_probe(entry["url"], username, password,
                             duration_s=duration)
        if result["error"]:
            return f"RTSP check failed for '{name}': {result['error']}"
        res = (f"{result['width']}×{result['height']}"
               if result["width"] else "resolution unknown (no SPS seen)")
        return (f"RTSP stream '{name}': {result['codec'] or 'codec unknown'}, "
                f"{res}, {result['frames']} frame(s), "
                f"{result['packets']} packet(s), "
                f"{result['fps']:.1f} fps, "
                f"{result['bitrate_kbps']:.0f} kbps over {duration} s. "
                "H.264 video is not decoded in this build — the "
                "stream health above is packet-level.")

    # record
    dest = Path(str(path)).expanduser() if path else \
        _default_video_path(name)
    result = _rtsp_probe(entry["url"], username, password,
                         duration_s=duration, record_path=str(dest))
    if result["error"]:
        return f"Recording failed for '{name}': {result['error']}"
    if not result["recorded_path"] or result["recorded_bytes"] == 0:
        return (f"Recording ran for '{name}' but no stream was "
                "written — nothing is claimed.")
    return (f"Recorded '{name}': {result['frames']} frame(s), "
            f"{result['recorded_bytes']} bytes in {duration} s, saved to "
            f"{result['recorded_path']}. Raw H.264 bitstream — playable "
            "in VLC/ffplay, not an .mp4.")

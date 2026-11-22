"""Local voice-agent / call-flow engine + an operator-supplied SIP trunk client.

Parent-ruled (licence, 2026-11-12): fonoster/fonoster
(MIT, read in full. No code adopted, name not used.)

The finding (Fonoster, rebuilt from scratch — no code adopted):
an open-source platform for building, testing and running voice
agents on real phone lines. Its shape, verified from source (the
Voice SDK session flow and the Autopilot docs, not the README
alone): Fonoster opens one signed socket per call to your server;
commands go out, events come back, and Fonoster does the speech.
Your code works with TEXT, not audio — commands say / gather /
transfer / hangup; events call started / speech heard / key
pressed / speech finished / call ended. A voice agent is a prompt
plus tools plus turn-taking; a SIP agent is a softphone; a trunk
is the operator's own connection to the phone network.

What this module builds (the honest remainder):
* A local voice agent + a call-flow engine — the "programmable
  voice application". An agent is a name, a greeting, a flow and
  a transfer number. A flow is an ordered list of steps: say
  (speak text), gather (collect DTMF digits and/or a spoken
  reply), branch (jump on the gathered input), transfer (hand
  the caller to the agent's configured number), hangup.
* A SIP/VoIP client that connects the flow engine to the
  OPERATOR's own SIP trunk (own stdlib SIP + SDP + RTP + G.711
  mu-law + RFC 2833 DTMF — no library, no second app). The
  trunk host, port, username and password are operator-supplied
  PER CALL ONLY, never stored and never echoed (the billing /
  agent_call precedent). The password is never sent in the
  clear: SIP digest auth (MD5 challenge-response) only.
* A local `run` mode: execute a flow in a text session (the
  operator supplies the caller's replies) so the owner can test
  a receptionist without a phone line.
* A `serve` listener: register to the operator's trunk and
  answer inbound calls with the named agent's flow (one call at
  a time; a second inbound call while busy gets 486 Busy Here).

What is OUT (stated on the tin, not built):
* No PBX — VYUK does not run a phone exchange. No call
  bridging: a transfer is a SIP REFER to the trunk (the trunk
  moves the caller); if the trunk rejects it, the transfer
  fails honestly.
* No multi-tenancy, no RBAC, no OAuth2/JWT auth — single-user
  by design.
* No hosted service (Fonoster Cloud is out) and no trunk or
  phone number is ever bought or provisioned by VYUK — the
  BUYER owns the trunk/number.
* No S3, no Let's Encrypt endpoints, no cloud speech: the
  speech on a call is the tree's own Kokoro TTS (rendered to
  8 kHz mu-law RTP) and, for spoken replies, the product's own
  configured Gemini key (the prompt_tune precedent — key from
  config, never passed as an argument). DTMF is the primary
  gather: deterministic, testable, and it works with no key.
* No TLS/SRTP in v1: plain UDP SIP + plain RTP. The trunk is
  the operator's own (their LAN/VPN or their provider's SBC);
  the digest challenge means the password never crosses the
  wire in the clear, but the AUDIO is unencrypted — use a
  private trunk or a VPN. Stated here, not hidden.
* No call recording: the tool never stores call audio. The
  ledger records call metadata + the flow transcript, not
  audio.
* No transfer to an arbitrary number: a transfer goes only to
  the agent's configured transfer number, set by the operator.

Classification: effectful, confirm-gated for `call` and `serve`
(they dial a real number / start answering real calls); agent
and flow edits are local app-state (the wardrobe/lodging
precedent); `run`, `stop`, `status` and `events` and the reads
are free. A call never starts without the operator's approval
(C-14 approve-first).
"""

import hashlib
import json
import random
import re
import socket
import sqlite3
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .config import DATA_DIR, config
from . import store_base
from .version import VERSION

DEFAULT_DB_FILENAME = "voice_agent.sqlite3"

# --- Bounds (checked before any call is placed or any LM call spent) ---
_MAX_FLOW_STEPS = 50
_MAX_FLOW_JSON = 65536
_MAX_SAY_CHARS = 4000
_MAX_AGENT_NAME = 80
_MAX_NUMBER_CHARS = 32
_MAX_CALL_SECONDS = 300
_DEFAULT_CALL_SECONDS = 120
_MAX_GATHER_SECONDS = 60
_MAX_GATHER_DIGITS = 20
_MAX_CAPTURE_SECONDS = 30
_RTP_PAYLOAD_PCMU = 0
_RTP_PAYLOAD_DTMF = 101
_RTP_FRAME_BYTES = 160  # 20 ms of 8 kHz mu-law
_RTP_FRAME_MS = 20
_SIP_PORT = 5060
_SIP_TIMEOUT_S = 5.0
_GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/"
               "models/%s:generateContent?key=%s")
_GEMINI_TIMEOUT_S = 30
_TRANSCRIBE_PROMPT = ("Transcribe the caller's speech exactly. "
                      "Reply with only the transcript, no other words.")

# ---------------------------------------------------------------------------
# G.711 mu-law (pure Python; the canonical companding tables, no dependency)
# ---------------------------------------------------------------------------

_ULAW_BIAS = 0x84
_SEG_END = (0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF)


def linear_to_ulaw(sample: int) -> int:
    """One int16 sample -> one mu-law byte. Silence (0) -> 0xFF."""
    if sample > 32767:
        sample = 32767
    elif sample < -32768:
        sample = -32768
    sign = 0x80 if sample < 0 else 0x00
    if sample < 0:
        sample = -sample
    sample += _ULAW_BIAS
    if sample > 0x7FFF:
        sample = 0x7FFF
    seg = 7
    for i, end in enumerate(_SEG_END):
        if sample <= end:
            seg = i
            break
    mantissa = (sample >> (seg + 3)) & 0x0F
    return (~(sign | (seg << 4) | mantissa)) & 0xFF


def ulaw_to_linear(code: int) -> int:
    """One mu-law byte -> one int16 sample. 0xFF -> 0."""
    code = (~code) & 0xFF
    sign = code & 0x80
    seg = (code >> 4) & 0x07
    mantissa = code & 0x0F
    sample = ((mantissa << 3) + _ULAW_BIAS) << seg
    sample -= _ULAW_BIAS
    return -sample if sign else sample


def pcm16_to_ulaw(samples) -> bytes:
    """int16 samples (any iterable) -> mu-law bytes."""
    return bytes(linear_to_ulaw(int(s)) for s in samples)


def ulaw_to_pcm16(data: bytes):
    """mu-law bytes -> a list of int16 samples."""
    return [ulaw_to_linear(b) for b in data]


def resample_to_8k(samples, rate: int):
    """Resample int16/float samples to 8 kHz. Supports 8000 (direct)
    and 24000 (boxcar decimation by 3, the Kokoro native rate).
    Any other rate is refused honestly — never silently wrong."""
    if rate == 8000:
        return [int(s) for s in samples]
    if rate == 24000:
        out = []
        for i in range(0, len(samples) - 2, 3):
            out.append(int((float(samples[i]) + float(samples[i + 1])
                            + float(samples[i + 2])) / 3.0))
        return out
    raise ValueError("unsupported sample rate %r (need 8000 or 24000)"
                     % (rate,))


# ---------------------------------------------------------------------------
# SIP messages: parse + build (a minimal, strict-enough SIP/2.0 shape)
# ---------------------------------------------------------------------------

_SIP_VERSION = "SIP/2.0"


class SIPMessage:
    """One SIP request or response. Headers are case-insensitive
    (stored lower-cased); a repeated header keeps its first value
    except Via, which keeps the list."""

    def __init__(self, start_line, headers, body=b""):
        self.start_line = start_line
        self.headers = headers  # dict[str, str], keys lower-cased
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")

    @property
    def is_response(self):
        return self.start_line.startswith(_SIP_VERSION)

    @property
    def method(self):
        if self.is_response:
            return ""
        return self.start_line.split(" ", 1)[0].upper()

    @property
    def status(self):
        if not self.is_response:
            return 0
        try:
            return int(self.start_line.split(" ", 2)[1])
        except (IndexError, ValueError):
            return 0

    def header(self, name, default=""):
        return self.headers.get(name.lower(), default)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<SIP %s>" % (self.start_line,)


def parse_sip(data: bytes):
    """Parse raw UDP bytes into a SIPMessage, or None if not SIP."""
    try:
        text = data.decode("utf-8", "replace")
    except Exception:
        return None
    head, sep, body = text.partition("\r\n\r\n")
    if not sep:
        head, sep, body = text.partition("\n\n")
        if not sep:
            return None
    lines = head.splitlines()
    if not lines:
        return None
    start = lines[0].strip()
    if not (start.startswith(_SIP_VERSION)
            or " SIP/2.0" in start):
        return None
    headers = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        key = name.strip().lower()
        if key not in headers:
            headers[key] = value.strip()
    return SIPMessage(start, headers, body.encode("utf-8", "replace"))


def build_sip_request(method, uri, headers, body=b""):
    """Build one SIP request. `headers` is an ordered list of
    (name, value) pairs; Content-Length is added here."""
    lines = ["%s %s %s" % (method, uri, _SIP_VERSION)]
    for name, value in headers:
        lines.append("%s: %s" % (name, value))
    lines.append("Content-Length: %d" % len(body))
    lines.append("")
    lines.append("")
    raw = "\r\n".join(lines).encode("utf-8") + body
    return raw


def build_sip_response(status, reason, request, headers, body=b""):
    """Build one SIP response to `request` (copies Via/From/To/
    Call-ID/CSeq from it)."""
    lines = ["%s %d %s" % (_SIP_VERSION, status, reason)]
    for name in ("via", "from", "to", "call-id", "cseq"):
        value = request.header(name)
        if value:
            lines.append("%s: %s" % (name.title(), value))
    for name, value in headers:
        lines.append("%s: %s" % (name, value))
    lines.append("Content-Length: %d" % len(body))
    lines.append("")
    lines.append("")
    return "\r\n".join(lines).encode("utf-8") + body


# ---------------------------------------------------------------------------
# SIP digest auth (RFC 2617, MD5) — the password never crosses the wire
# ---------------------------------------------------------------------------

def _md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def parse_auth_challenge(header_value: str):
    """Parse a WWW-Authenticate / Proxy-Authenticate Digest header
    into a dict (realm, nonce, qop, opaque, algorithm)."""
    out = {}
    if not header_value:
        return out
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([\w.]+))', header_value):
        out[m.group(1).lower()] = m.group(2) if m.group(2) is not None \
            else m.group(3)
    return out


def digest_authorization(username, password, method, uri, challenge,
                         cnonce=None):
    """Build the Authorization header value for a Digest challenge.
    Returns "" when the challenge has no realm/nonce (no auth)."""
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    if not realm or not nonce:
        return ""
    if cnonce is None:
        cnonce = "%08x" % random.getrandbits(32)
    ha1 = _md5_hex("%s:%s:%s" % (username, realm, password))
    ha2 = _md5_hex("%s:%s" % (method, uri))
    qop = (challenge.get("qop") or "").split(",")[0].strip()
    if qop == "auth":
        nc = "00000001"
        response = _md5_hex("%s:%s:%s:%s:%s:%s"
                            % (ha1, nonce, nc, cnonce, qop, ha2))
        auth = ('Digest username="%s", realm="%s", nonce="%s", '
                'uri="%s", response="%s", algorithm=MD5, cnonce="%s", '
                'opaque="%s", qop=%s, nc=%s'
                % (username, realm, nonce, uri, response, cnonce,
                   challenge.get("opaque", ""), qop, nc))
    else:
        response = _md5_hex("%s:%s:%s" % (ha1, nonce, ha2))
        auth = ('Digest username="%s", realm="%s", nonce="%s", '
                'uri="%s", response="%s", algorithm=MD5'
                % (username, realm, nonce, uri, response))
    return auth


def parse_trunk(trunk_json):
    """Parse the operator-supplied trunk JSON into a dict
    {host, port, user, pass}. Refuses URL credentials, a host
    with a scheme, and a missing host/user/pass — the trunk is
    host + user + pass as separate fields, never a URL."""
    try:
        trunk = json.loads(trunk_json)
    except Exception:
        return None, "trunk must be JSON, e.g. {\"host\": \"...\", \"user\": \"...\", \"pass\": \"...\"}"
    if not isinstance(trunk, dict):
        return None, "trunk must be a JSON object"
    host = str(trunk.get("host", "")).strip()
    user = str(trunk.get("user", "")).strip()
    password = str(trunk.get("pass", ""))
    port = trunk.get("port", _SIP_PORT)
    if not host or "://" in host or "@" in host:
        return None, ("trunk host must be a bare hostname/IP, never a URL "
                      "and never with credentials embedded")
    if not user or not password:
        return None, "trunk needs a user and a pass (operator-supplied, per call only)"
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None, "trunk port must be a number"
    if not 1 <= port <= 65535:
        return None, "trunk port must be 1-65535"
    return {"host": host, "port": port, "user": user, "pass": password}, ""


def clean_number(number):
    """Validate a phone number: digits, +, spaces, dashes, parens
    only; 3-32 chars. Anything else is refused (a bare address
    that resolves to someone else is the first loophole)."""
    if number is None:
        return "", "a number is required"
    text = str(number).strip()
    if not 3 <= len(text) <= _MAX_NUMBER_CHARS:
        return "", "number must be 3-32 characters"
    if not re.fullmatch(r"\+?[0-9][0-9 ()\-]*", text):
        return "", ("number must be digits (and +, spaces, dashes, "
                    "parens) only")
    return re.sub(r"[ ()\-]", "", text), ""

# ---------------------------------------------------------------------------
# SDP: offer (PCMU + telephone-event) and answer parsing
# ---------------------------------------------------------------------------

def _local_ip_for(peer_host):
    """The local IP that reaches peer_host (the UDP connect trick);
    127.0.0.1 if it cannot be determined. Used for the SDP c= line
    — advertising 0.0.0.0 would leave the peer with no address to
    send RTP to."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((peer_host, _SIP_PORT))
            ip = probe.getsockname()[0]
        finally:
            probe.close()
        if ip and ip != "0.0.0.0":
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def build_sdp_offer(local_ip: str, rtp_port: int) -> str:
    """One SDP offer: G.711 mu-law (PCMU/8000) + RFC 2833 DTMF."""
    sess = random.getrandbits(31)
    return "\r\n".join([
        "v=0",
        "o=- %d %d IN IP4 %s" % (sess, sess, local_ip),
        "s=VYUK voice agent",
        "c=IN IP4 %s" % local_ip,
        "t=0 0",
        "m=audio %d RTP/AVP 0 101" % rtp_port,
        "a=rtpmap:0 PCMU/8000",
        "a=rtpmap:101 telephone-event/8000",
        "a=fmtp:101 0-15",
        "a=ptime:20",
        "",
    ])


def parse_sdp(body) -> dict:
    """Parse an SDP answer into {ip, port, payloads, dtmf_pt}.
    Returns {} when the body is not a usable audio offer/answer."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    ip = ""
    port = 0
    payloads = []
    dtmf_pt = None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("c=IN IP4 "):
            ip = line[len("c=IN IP4 "):].strip()
        elif line.startswith("m=audio "):
            parts = line.split()
            try:
                port = int(parts[1])
                payloads = [int(p) for p in parts[3:] if p.isdigit()]
            except (IndexError, ValueError):
                pass
        elif line.startswith("a=rtpmap:"):
            try:
                pt = int(line[len("a=rtpmap:"):].split()[0])
                if "telephone-event" in line:
                    dtmf_pt = pt
            except (IndexError, ValueError):
                pass
    if not ip or not port or _RTP_PAYLOAD_PCMU not in payloads:
        return {}
    return {"ip": ip, "port": port, "payloads": payloads,
            "dtmf_pt": dtmf_pt if dtmf_pt is not None else _RTP_PAYLOAD_DTMF}


# ---------------------------------------------------------------------------
# RTP: G.711 mu-law audio + RFC 2833 DTMF over UDP
# ---------------------------------------------------------------------------

_DTMF_EVENTS = "0123456789*#ABCD"


def rtp_pack(payload_type: int, seq: int, timestamp: int, ssrc: int,
             payload: bytes) -> bytes:
    """One 12-byte RTP header + payload."""
    return struct.pack(">BBHII", 0x80, payload_type & 0x7F, seq & 0xFFFF,
                       timestamp & 0xFFFFFFFF, ssrc & 0xFFFFFFFF) + payload


def rtp_unpack(data: bytes):
    """Parse one RTP packet -> (payload_type, seq, timestamp, ssrc,
    payload) or None when the packet is too short or not RTP v2."""
    if len(data) < 12 or (data[0] >> 6) != 2:
        return None
    _ver_pad_ext, pt, seq, ts, ssrc = struct.unpack(">BBHII", data[:12])
    marker = bool(pt & 0x80)
    return (pt & 0x7F, seq, ts, ssrc, data[12:], marker)


class RTPSession:
    """One RTP media leg: sends mu-law audio (paced 20 ms frames)
    and DTMF events, receives mu-law audio + DTMF into queues.
    The receive loop runs in a background thread; `close()` stops
    it and the socket. All socket work is UDP."""

    def __init__(self, sock=None):
        self.sock = sock or socket.socket(socket.AF_INET,
                                          socket.SOCK_DGRAM)
        self.sock.settimeout(0.2)
        self.sock.bind(("0.0.0.0", 0))
        self.local_port = self.sock.getsockname()[1]
        self.peer = None  # (ip, port) from the SDP answer
        self.seq = random.randint(0, 65535)
        self.timestamp = random.getrandbits(32)
        self.ssrc = random.getrandbits(32)
        self.audio = []      # list of (seq, pcmu bytes)
        self.dtmf = []       # list of digit chars, in order
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._seen_dtmf_end = set()
        self._connected_peer = None

    def _send(self, data: bytes):
        """Send one packet to the RTP peer over a CONNECTED socket.
        An unconnected sendto is refused by the sandbox (EPERM) —
        the qlcplus precedent: connect + send. Connects to the
        peer on first use (or when the peer changes)."""
        if self.peer is None:
            raise OSError("no RTP peer")
        if self._connected_peer != self.peer:
            self.sock.connect(self.peer)
            self._connected_peer = self.peer
        self.sock.send(data)

    @property
    def local_ip(self):
        # The socket binds to 0.0.0.0 (all interfaces) so the peer
        # can reach it — but 0.0.0.0 is not an address to advertise
        # in SDP. Once the peer is known, advertise the local IP
        # that reaches it; before that, loopback (the lab case).
        try:
            ip = self.sock.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        if ip and ip != "0.0.0.0":
            return ip
        if self.peer is not None:
            return _local_ip_for(self.peer[0])
        return "127.0.0.1"

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._recv_loop,
                                            daemon=True)
            self._thread.start()

    def _recv_loop(self):
        while not self._stop.is_set():
            try:
                data, _addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            parsed = rtp_unpack(data)
            if parsed is None:
                continue
            pt, _seq, _ts, _ssrc, payload, _marker = parsed
            if pt == _RTP_PAYLOAD_DTMF and len(payload) >= 4:
                event = payload[0]
                end = bool(payload[1] & 0x80)
                if event < len(_DTMF_EVENTS) and end:
                    digit = _DTMF_EVENTS[event]
                    with self._lock:
                        # One digit per event (the end packet is the
                        # reliable one; duplicates are dropped).
                        if (event, payload[2:4]) not in self._seen_dtmf_end:
                            self._seen_dtmf_end.add((event, payload[2:4]))
                            self.dtmf.append(digit)
            elif pt == _RTP_PAYLOAD_PCMU and payload:
                with self._lock:
                    self.audio.append(payload)

    def send_pcmu(self, pcmu: bytes, pace=True):
        """Send mu-law audio in 160-byte (20 ms) frames. `pace`
        sleeps 20 ms per frame so the peer hears real time; the
        suite passes pace=False to run fast."""
        if self.peer is None:
            return 0
        sent = 0
        for i in range(0, len(pcmu), _RTP_FRAME_BYTES):
            frame = pcmu[i:i + _RTP_FRAME_BYTES]
            if len(frame) < _RTP_FRAME_BYTES:
                frame = frame + b"\xff" * (_RTP_FRAME_BYTES - len(frame))
            pkt = rtp_pack(_RTP_PAYLOAD_PCMU, self.seq, self.timestamp,
                           self.ssrc, frame)
            try:
                self._send(pkt)
            except OSError:
                break
            self.seq = (self.seq + 1) & 0xFFFF
            self.timestamp = (self.timestamp + _RTP_FRAME_BYTES) & 0xFFFFFFFF
            sent += len(frame)
            if pace:
                time.sleep(_RTP_FRAME_MS / 1000.0)
        return sent

    def send_dtmf(self, digit: str, duration_ms: int = 100):
        """Send one DTMF digit as RFC 2833 telephone-events: a
        start packet, then an end packet (E bit set)."""
        if self.peer is None or digit not in _DTMF_EVENTS:
            return False
        event = _DTMF_EVENTS.index(digit)
        duration = int(duration_ms * 8)  # 8 kHz clock
        start = struct.pack(">BBH", event, 0x00, 0)
        end = struct.pack(">BBH", event, 0x80 | 0x0A, duration)
        try:
            self._send(rtp_pack(_RTP_PAYLOAD_DTMF, self.seq,
                                self.timestamp, self.ssrc, start))
            self.seq = (self.seq + 1) & 0xFFFF
            self._send(rtp_pack(_RTP_PAYLOAD_DTMF, self.seq,
                                self.timestamp, self.ssrc, end))
            self.seq = (self.seq + 1) & 0xFFFF
        except OSError:
            return False
        return True

    def drain_audio(self) -> bytes:
        """Take all received mu-law audio so far (and clear it)."""
        with self._lock:
            data = b"".join(self.audio)
            self.audio = []
        return data

    def drain_dtmf(self) -> str:
        """Take all received DTMF digits so far (and clear them)."""
        with self._lock:
            digits = "".join(self.dtmf)
            self.dtmf = []
        return digits

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        try:
            self.sock.close()
        except OSError:
            pass

# ---------------------------------------------------------------------------
# Speech: render (Kokoro TTS -> 8 kHz mu-law) and transcribe (Gemini)
# Both are injectable so the suite/prover never touch a real voice.
# ---------------------------------------------------------------------------

def _kokoro_render(text: str):
    """Render text through the tree's Kokoro TTS. Returns
    (rate, int16 PCM bytes) or (None, reason). Never raises: a
    missing Kokoro, a dead child, or a failed render is an honest
    failure, and the call says so instead of speaking silence."""
    try:
        from .desktop import kokoro_voice as _kv
    except Exception as e:  # noqa: BLE001 — Kokoro is optional here
        return None, "Kokoro TTS unavailable (%s)" % (e,)
    try:
        result = _kv.speak(text)
    except Exception as e:  # noqa: BLE001 — a render fault is a failure
        return None, "Kokoro render failed (%s)" % (e,)
    if not isinstance(result, dict) or not result.get("ok"):
        reason = result.get("reason", "unknown") \
            if isinstance(result, dict) else "no result"
        return None, "Kokoro render failed (%s)" % (reason,)
    audio = result.get("audio")
    rate = result.get("sample_rate") or 24000
    if audio is None:
        return None, "Kokoro returned no audio"
    if isinstance(audio, (bytes, bytearray)):
        pcm = bytes(audio)
    elif hasattr(audio, "tobytes"):
        pcm = audio.tobytes()
    else:
        return None, "Kokoro returned audio in an unknown shape"
    return int(rate), pcm


def render_speech_to_ulaw(text: str, _renderer=None) -> tuple:
    """Render `text` to mu-law bytes at 8 kHz for RTP. Returns
    (bytes, "") or (None, reason). `_renderer` is the injectable
    speech renderer (the suite/prover pass a fake that returns a
    known tone); the default is the tree's Kokoro TTS."""
    renderer = _renderer or _kokoro_render
    out = renderer(text)
    if out is None or out[0] is None:
        reason = out[1] if out and len(out) > 1 else "no audio"
        return None, str(reason)
    rate, pcm = out
    if rate not in (8000, 24000):
        return None, ("unsupported TTS rate %r (need 8000 or 24000)"
                      % (rate,))
    try:
        count = len(pcm) // 2
        samples = list(struct.unpack("<%dh" % count, pcm[:count * 2]))
        resampled = resample_to_8k(samples, rate)
        return pcm16_to_ulaw(resampled), ""
    except ValueError as e:
        return None, str(e)


def _gemini_key():
    """The product's own configured Gemini key, or None. Same key
    the brain session uses (the prompt_tune precedent); never
    passed as an argument, never echoed, never stored by this
    module."""
    try:
        key = str(getattr(config, "api_key", "") or "").strip()
    except Exception:
        key = ""
    return key or None


def _wav_bytes(pcm16_samples, rate: int = 8000) -> bytes:
    """int16 samples -> a minimal WAV file (44-byte header)."""
    data = struct.pack("<%dh" % len(pcm16_samples), *pcm16_samples)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 1, 1, rate,
        rate * 2, 2, 16, b"data", len(data))
    return header + data


def transcribe_audio(pcm16_samples, _opener=None):
    """Transcribe captured call audio through the product's own
    Gemini endpoint. Returns (text, "") or (None, reason). The
    key comes from config; the audio is sent as inline WAV data.
    Refuses up front when no key is configured — never spends a
    call that cannot work."""
    import base64
    key = _gemini_key()
    if not key:
        return None, ("no Gemini key configured — set one in Settings "
                      "(speech gather needs it; DTMF does not)")
    if not pcm16_samples:
        return None, "no audio captured to transcribe"
    wav = _wav_bytes(list(pcm16_samples)[:_MAX_CAPTURE_SECONDS * 8000])
    payload = {
        "contents": [{
            "parts": [
                {"text": _TRANSCRIBE_PROMPT},
                {"inline_data": {
                    "mime_type": "audio/wav",
                    "data": base64.b64encode(wav).decode("ascii")}},
            ],
        }],
        "generationConfig": {"temperature": 0.0},
    }
    model = "gemini-2.0-flash"
    url = _GEMINI_URL % (urllib.parse.quote(model, safe=""),
                         urllib.parse.quote(key, safe=""))
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    opener = _opener or urllib.request.urlopen
    try:
        with opener(req, timeout=_GEMINI_TIMEOUT_S) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return None, "Gemini refused the transcription (HTTP %d)" % (e.code,)
    except Exception as e:  # noqa: BLE001 — network faults are refusals
        return None, "Gemini transcription failed (%s)" % (e,)
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
        text = doc["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None, "Gemini returned an unreadable transcription"
    text = str(text or "").strip()
    if not text:
        return None, "Gemini returned an empty transcription"
    return text, ""


# ---------------------------------------------------------------------------
# Call flows: the "programmable voice application"
# ---------------------------------------------------------------------------

_FLOW_KINDS = ("say", "gather", "branch", "transfer", "hangup")


def validate_flow(steps_json):
    """Validate a flow (a JSON list of steps) -> (steps, "") or
    (None, reason). A step is say/gather/branch/transfer/hangup.
    Bounds: <= 50 steps, <= 64 KB JSON, say text <= 4000 chars,
    gather timeout 1-60 s, max digits 1-20, branch targets are
    valid step indexes."""
    try:
        steps = json.loads(steps_json)
    except Exception:
        return None, "steps must be JSON, e.g. [{\"kind\": \"say\", \"text\": \"...\"}]"
    if not isinstance(steps, list) or not steps:
        return None, "a flow needs at least one step"
    if len(steps) > _MAX_FLOW_STEPS:
        return None, "a flow has at most %d steps" % _MAX_FLOW_STEPS
    if len(steps_json) > _MAX_FLOW_JSON:
        return None, "a flow is at most 64 KB of JSON"
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return None, "step %d must be an object" % i
        kind = str(step.get("kind", "")).strip().lower()
        if kind not in _FLOW_KINDS:
            return None, ("step %d: kind must be say, gather, branch, "
                          "transfer or hangup" % i)
        if kind == "say":
            text = str(step.get("text", ""))
            if not text.strip():
                return None, "step %d: say needs text" % i
            if len(text) > _MAX_SAY_CHARS:
                return None, "step %d: say text is at most 4000 chars" % i
        elif kind == "gather":
            try:
                timeout = int(step.get("timeout_s", 10))
                max_digits = int(step.get("max_digits", 1))
            except (TypeError, ValueError):
                return None, "step %d: gather timeout/max_digits must be numbers" % i
            if not 1 <= timeout <= _MAX_GATHER_SECONDS:
                return None, "step %d: gather timeout must be 1-60 s" % i
            if not 1 <= max_digits <= _MAX_GATHER_DIGITS:
                return None, "step %d: gather max_digits must be 1-20" % i
            mode = str(step.get("mode", "dtmf")).strip().lower()
            if mode not in ("dtmf", "speech", "both"):
                return None, "step %d: gather mode must be dtmf, speech or both" % i
        elif kind == "branch":
            targets = step.get("on")
            if not isinstance(targets, dict) or not targets:
                return None, "step %d: branch needs an 'on' map" % i
            for _key, target in targets.items():
                if not isinstance(target, int) or not 0 <= target < len(steps):
                    return None, ("step %d: branch target must be a step "
                                  "index 0-%d" % (i, len(steps) - 1))
    return steps, ""


def run_flow(steps, session):
    """Run a validated flow against a session (say/gather/
    transfer/hangup). Returns the transcript: a list of
    {step, kind, detail}. The engine caps at 50 executed steps so
    a looping branch cannot run forever."""
    transcript = []
    last_input = ""
    pc = 0
    executed = 0
    while 0 <= pc < len(steps) and executed < _MAX_FLOW_STEPS:
        step = steps[pc]
        kind = str(step.get("kind", "")).strip().lower()
        executed += 1
        if kind == "say":
            text = str(step.get("text", ""))
            session.say(text)
            transcript.append({"step": pc, "kind": "say", "detail": text})
            pc += 1
        elif kind == "gather":
            prompt = str(step.get("prompt", ""))
            if prompt:
                session.say(prompt)
                transcript.append({"step": pc, "kind": "say",
                                   "detail": prompt})
            digits, speech = session.gather(
                int(step.get("timeout_s", 10)),
                int(step.get("max_digits", 1)),
                str(step.get("mode", "dtmf")).strip().lower())
            last_input = digits or speech or ""
            transcript.append({"step": pc, "kind": "gather",
                               "detail": last_input})
            pc += 1
        elif kind == "branch":
            targets = step.get("on") or {}
            nxt = targets.get(last_input, targets.get("default"))
            transcript.append({"step": pc, "kind": "branch",
                               "detail": "input %r -> step %s"
                                         % (last_input, nxt)})
            if isinstance(nxt, int) and 0 <= nxt < len(steps):
                pc = nxt
            else:
                pc += 1
        elif kind == "transfer":
            outcome = session.transfer()
            transcript.append({"step": pc, "kind": "transfer",
                               "detail": outcome})
            break
        elif kind == "hangup":
            session.hangup()
            transcript.append({"step": pc, "kind": "hangup",
                               "detail": "call ended"})
            break
        else:  # pragma: no cover - validate_flow refuses these
            pc += 1
    return transcript


class TextSession:
    """A local text session for `run`: the operator supplies the
    caller's replies; the flow runs against them. No SIP, no
    audio, no network — the receptionist, tested on paper."""

    def __init__(self, inputs, transfer_to=""):
        self.inputs = [str(x) for x in (inputs or [])]
        self.transfer_to = transfer_to
        self.said = []
        self.ended = False

    def say(self, text):
        self.said.append(text)

    def gather(self, timeout_s, max_digits, mode):
        if not self.inputs:
            return "", ""
        raw = str(self.inputs.pop(0)).strip()
        if re.fullmatch(r"[0-9*#ABCD]{1,20}", raw):
            return raw[:max_digits], ""
        return "", raw

    def transfer(self):
        if not self.transfer_to:
            return "no transfer number configured — transfer refused"
        return "transferred to %s" % self.transfer_to

    def hangup(self):
        self.ended = True

# ---------------------------------------------------------------------------
# SIP user agent: REGISTER / INVITE / ACK / BYE / REFER + the listener
# Own stdlib implementation (UDP sockets + MD5 digest auth). The socket
# is injectable so the suite/prover can drive a fake SIP peer.
# ---------------------------------------------------------------------------

def _new_tag():
    return "%08x" % random.getrandbits(32)


def _new_call_id(local_ip):
    return "%s@%s" % (uuid.uuid4().hex, local_ip)


class SIPUA:
    """One SIP user agent: a UDP socket, an RTP session, and the
    dialog state. `sock_factory` returns a bound UDP socket (the
    suite passes one bound to a known port so a fake peer can
    reach it); the default binds an ephemeral port."""

    def __init__(self, sock_factory=None, local_ip="127.0.0.1"):
        self.local_ip = local_ip
        if sock_factory is not None:
            self.sock = sock_factory()
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(("0.0.0.0", 0))
        # A factory may hand back an unbound socket; bind it so
        # local_port is real (the Via/Contact headers advertise it
        # — an unbound socket reads port 0, and no peer can dial
        # back to port 0).
        try:
            if self.sock.getsockname()[1] == 0:
                self.sock.bind(("0.0.0.0", 0))
        except OSError:
            pass
        self.sock.settimeout(_SIP_TIMEOUT_S)
        self.local_port = self.sock.getsockname()[1]
        self.rtp = RTPSession()
        self.cseq = 0
        self._connected_peer = None

    def _send(self, data: bytes, peer):
        """Send one SIP message to `peer` over a CONNECTED socket.
        An unconnected sendto is refused by the sandbox (EPERM) —
        the qlcplus precedent: connect + send. Connects to the
        peer on first use (or when the peer changes). A connected
        socket still receives (recvfrom) — only from that peer,
        which is exactly the trunk for this UA."""
        if self._connected_peer != peer:
            self.sock.connect(peer)
            self._connected_peer = peer
        self.sock.send(data)

    def close(self):
        self.rtp.close()
        try:
            self.sock.close()
        except OSError:
            pass

    def _next_cseq(self):
        self.cseq += 1
        return self.cseq

    def _base_headers(self, trunk, call_id, cseq, method):
        user = trunk["user"]
        host = trunk["host"]
        return [
            ("Via", "SIP/2.0/UDP %s:%d;branch=z9hG4bK%s"
                    % (self.local_ip, self.local_port, _new_tag())),
            ("Max-Forwards", "70"),
            ("From", "<sip:%s@%s>;tag=%s" % (user, host, _new_tag())),
            ("To", "<sip:%s@%s>" % (user, host)),
            ("Call-ID", call_id),
            ("CSeq", "%d %s" % (cseq, method)),
        ]

    def _send_and_wait(self, raw, peer, want_final=True):
        """Send one request and wait for a response. Provisional
        (1xx) responses are skipped when want_final. Returns
        (SIPMessage, addr) or (None, None) on timeout / garbage."""
        self._send(raw, peer)
        deadline = time.monotonic() + _SIP_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                self.sock.settimeout(max(0.1, deadline - time.monotonic()))
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                return None, None
            except OSError:
                return None, None
            msg = parse_sip(data)
            if msg is None or not msg.is_response:
                continue
            if want_final and 100 <= msg.status < 200:
                continue
            return msg, addr
        return None, None

    def _with_auth(self, method, uri, trunk, call_id, cseq, headers,
                   body, challenge_header):
        """Rebuild a request with a Digest Authorization header."""
        challenge = parse_auth_challenge(challenge_header)
        auth = digest_authorization(trunk["user"], trunk["pass"],
                                    method, uri, challenge)
        if not auth:
            return None
        headers = list(headers) + [("Authorization", auth)]
        return build_sip_request(method, uri, headers, body)

    def register(self, trunk):
        """REGISTER to the operator's trunk (digest auth when
        challenged). Returns (True, "") or (False, reason)."""
        host = trunk["host"]
        peer = (host, trunk["port"])
        uri = "sip:%s" % host
        call_id = _new_call_id(self.local_ip)
        cseq = self._next_cseq()
        headers = self._base_headers(trunk, call_id, cseq, "REGISTER") + [
            ("Contact", "<sip:%s@%s:%d>"
                        % (trunk["user"], self.local_ip, self.local_port)),
            ("Expires", "3600"),
        ]
        raw = build_sip_request("REGISTER", uri, headers)
        resp, _addr = self._send_and_wait(raw, peer)
        if resp is None:
            return False, "no answer from the trunk (timeout)"
        if resp.status in (401, 407):
            challenge = resp.header("www-authenticate") \
                or resp.header("proxy-authenticate")
            cseq = self._next_cseq()
            headers = self._base_headers(trunk, call_id, cseq,
                                         "REGISTER") + [
                ("Contact", "<sip:%s@%s:%d>"
                            % (trunk["user"], self.local_ip,
                               self.local_port)),
                ("Expires", "3600"),
            ]
            raw = self._with_auth("REGISTER", uri, trunk, call_id, cseq,
                                  headers, b"", challenge)
            if raw is None:
                return False, "the trunk challenged without a Digest realm/nonce"
            resp, _addr = self._send_and_wait(raw, peer)
            if resp is None:
                return False, "no answer from the trunk after auth (timeout)"
        if resp.status == 200:
            return True, ""
        return False, "trunk refused REGISTER (%d %s)" % (
            resp.status, resp.start_line.split(" ", 2)[-1])

    def invite(self, number, trunk, offer_sdp):
        """INVITE a number through the trunk. Returns (dialog, "")
        or (None, reason). On 200 OK the ACK is sent and the RTP
        peer is set from the SDP answer."""
        host = trunk["host"]
        peer = (host, trunk["port"])
        uri = "sip:%s@%s" % (number, host)
        call_id = _new_call_id(self.local_ip)
        cseq = self._next_cseq()
        local_tag = _new_tag()
        body = offer_sdp.encode("utf-8")
        headers = self._base_headers(trunk, call_id, cseq, "INVITE")
        headers[2] = ("From", "<sip:%s@%s>;tag=%s"
                      % (trunk["user"], host, local_tag))
        headers[3] = ("To", "<%s>" % uri)
        headers += [
            ("Contact", "<sip:%s@%s:%d>"
                        % (trunk["user"], self.local_ip, self.local_port)),
            ("Content-Type", "application/sdp"),
        ]
        raw = build_sip_request("INVITE", uri, headers, body)
        resp, _addr = self._send_and_wait(raw, peer)
        if resp is None:
            return None, "no answer from the trunk (timeout)"
        if resp.status in (401, 407):
            challenge = resp.header("www-authenticate") \
                or resp.header("proxy-authenticate")
            cseq = self._next_cseq()
            headers = self._base_headers(trunk, call_id, cseq, "INVITE")
            headers[2] = ("From", "<sip:%s@%s>;tag=%s"
                          % (trunk["user"], host, local_tag))
            headers[3] = ("To", "<%s>" % uri)
            headers += [
                ("Contact", "<sip:%s@%s:%d>"
                            % (trunk["user"], self.local_ip,
                               self.local_port)),
                ("Content-Type", "application/sdp"),
            ]
            raw = self._with_auth("INVITE", uri, trunk, call_id, cseq,
                                  headers, body, challenge)
            if raw is None:
                return None, "the trunk challenged without a Digest realm/nonce"
            resp, _addr = self._send_and_wait(raw, peer)
            if resp is None:
                return None, "no answer from the trunk after auth (timeout)"
        if resp.status != 200:
            return None, "the call was refused (%d %s)" % (
                resp.status, resp.start_line.split(" ", 2)[-1])
        answer = parse_sdp(resp.body)
        if not answer:
            return None, ("the trunk answered without a usable SDP "
                          "(no PCMU audio)")
        self.rtp.peer = (answer["ip"], answer["port"])
        self.rtp.start()
        # ACK (no response is expected for an ACK). The ACK carries
        # the INVITE's CSeq number (RFC 3261: the ACK for a 2xx
        # uses the INVITE's sequence number, method ACK).
        to_header = resp.header("to")
        ack_headers = self._base_headers(trunk, call_id,
                                         cseq, "ACK")
        ack_headers[2] = ("From", "<sip:%s@%s>;tag=%s"
                          % (trunk["user"], host, local_tag))
        ack_headers[3] = ("To", to_header)
        ack_headers += [
            ("Contact", "<sip:%s@%s:%d>"
                        % (trunk["user"], self.local_ip, self.local_port)),
        ]
        try:
            self._send(build_sip_request("ACK", uri, ack_headers), peer)
        except OSError:
            pass
        dialog = {
            "call_id": call_id, "local_tag": local_tag,
            "remote_tag": _tag_from_header(to_header),
            "cseq": cseq, "uri": uri, "peer": peer,
            "from": "<sip:%s@%s>;tag=%s" % (trunk["user"], host, local_tag),
            "to": to_header, "trunk": trunk,
        }
        return dialog, ""

    def bye(self, dialog):
        """BYE a dialog (best-effort: the call is over either way)."""
        cseq = self._next_cseq()
        headers = [
            ("Via", "SIP/2.0/UDP %s:%d;branch=z9hG4bK%s"
                    % (self.local_ip, self.local_port, _new_tag())),
            ("Max-Forwards", "70"),
            ("From", dialog["from"]),
            ("To", dialog["to"]),
            ("Call-ID", dialog["call_id"]),
            ("CSeq", "%d BYE" % cseq),
        ]
        try:
            self._send(
                build_sip_request("BYE", dialog["uri"], headers),
                dialog["peer"])
        except OSError:
            pass

    def refer(self, dialog, target):
        """REFER the peer to the transfer target (the trunk moves
        the caller). Returns (True, "") or (False, reason)."""
        cseq = self._next_cseq()
        headers = [
            ("Via", "SIP/2.0/UDP %s:%d;branch=z9hG4bK%s"
                    % (self.local_ip, self.local_port, _new_tag())),
            ("Max-Forwards", "70"),
            ("From", dialog["from"]),
            ("To", dialog["to"]),
            ("Call-ID", dialog["call_id"]),
            ("CSeq", "%d REFER" % cseq),
            ("Refer-To", "<sip:%s@%s>"
                         % (target, dialog["trunk"]["host"])),
            ("Referred-By", dialog["from"]),
        ]
        try:
            self._send(
                build_sip_request("REFER", dialog["uri"], headers),
                dialog["peer"])
        except OSError:
            return False, "could not send REFER"
        # The REFER response arrives on the same socket; wait for it.
        resp, _addr = self._wait_response(dialog)
        if resp is None:
            return False, "no answer to REFER (timeout)"
        if resp.status in (200, 202):
            return True, ""
        return False, "the trunk refused the transfer (%d)" % resp.status

    def _wait_response(self, dialog):
        deadline = time.monotonic() + _SIP_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                self.sock.settimeout(max(0.1, deadline - time.monotonic()))
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                return None, None
            except OSError:
                return None, None
            msg = parse_sip(data)
            if msg is not None and msg.is_response:
                return msg, addr
        return None, None


def _tag_from_header(header_value):
    """The tag= parameter of a From/To header, or ''."""
    m = re.search(r";tag=([^;\s]+)", header_value or "")
    return m.group(1) if m else ""


class SIPSession:
    """A flow-engine session over a live SIP call: `say` renders
    TTS to mu-law RTP, `gather` listens for DTMF digits and/or a
    spoken reply, `transfer` REFERs, `hangup` BYEs. `notes`
    collects honest failures (a render that failed, a gather that
    heard nothing) so the transcript says so."""

    def __init__(self, ua, dialog, transfer_to="", _renderer=None,
                 _opener=None, pace=True):
        self.ua = ua
        self.dialog = dialog
        self.transfer_to = transfer_to
        self._renderer = _renderer
        self._opener = _opener
        self.pace = pace
        self.notes = []
        self.ended = False
        self.deadline = None  # monotonic deadline for the whole call

    def say(self, text):
        if self.deadline is not None \
                and time.monotonic() > self.deadline:
            self.notes.append("say skipped — the call ran past its "
                              "time limit")
            return
        pcmu, reason = render_speech_to_ulaw(text, self._renderer)
        if pcmu is None:
            self.notes.append("say failed (%s) — the caller heard "
                              "silence for this step" % reason)
            return
        self.ua.rtp.send_pcmu(pcmu, pace=self.pace)

    def gather(self, timeout_s, max_digits, mode):
        digits = self.ua.rtp.drain_dtmf()
        deadline = time.monotonic() + timeout_s
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        audio = b""
        while time.monotonic() < deadline and len(digits) < max_digits:
            digits += self.ua.rtp.drain_dtmf()
            if digits:
                break
            audio += self.ua.rtp.drain_audio()
            time.sleep(0.05)
        digits = digits[:max_digits]
        if digits:
            return digits, ""
        if mode in ("speech", "both"):
            audio += self.ua.rtp.drain_audio()
            if audio:
                pcm = ulaw_to_pcm16(audio)
                text, reason = transcribe_audio(pcm, self._opener)
                if text:
                    return "", text
                self.notes.append("speech gather failed (%s)" % reason)
        return "", ""

    def transfer(self):
        if not self.transfer_to:
            return "no transfer number configured — transfer refused"
        ok, reason = self.ua.refer(self.dialog, self.transfer_to)
        if ok:
            return "transferred to %s" % self.transfer_to
        return "transfer refused (%s)" % reason

    def hangup(self):
        if not self.ended:
            self.ua.bye(self.dialog)
            self.ended = True

# ---------------------------------------------------------------------------
# Store: agents + flows + calls + the hash-chained ledger (A1-store:
# every write is re-read before it is claimed)
# ---------------------------------------------------------------------------

def _db_path():
    return Path(DATA_DIR) / DEFAULT_DB_FILENAME


def _locked(method):
    """Serialise store access across threads: the listener runs in
    its own thread while the handler runs on the caller's thread,
    and both use the one store. An RLock (not a Lock) so a method
    may call another locked method (remove_* re-reads via get_*)."""

    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class VoiceAgentStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = store_base.connect(str(self.path),
                                     check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS agents ("
            "id INTEGER PRIMARY KEY, name TEXT UNIQUE, greeting TEXT, "
            "flow_id INTEGER, transfer_to TEXT, voice TEXT, created REAL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS flows ("
            "id INTEGER PRIMARY KEY, name TEXT UNIQUE, steps_json TEXT, "
            "created REAL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS calls ("
            "id INTEGER PRIMARY KEY, direction TEXT, number TEXT, "
            "agent_id INTEGER, started REAL, ended REAL, outcome TEXT, "
            "transcript_json TEXT)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "id INTEGER PRIMARY KEY, seq INTEGER, ts REAL, kind TEXT, "
            "payload_json TEXT, prev_hash TEXT, hash TEXT)")
        self.conn.commit()

    @_locked
    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # -- agents ---------------------------------------------------------
    @_locked
    def add_agent(self, name, greeting, flow_id, transfer_to, voice):
        try:
            cur = self.conn.execute(
                "INSERT INTO agents (name, greeting, flow_id, "
                "transfer_to, voice, created) VALUES (?, ?, ?, ?, ?, ?)",
                (name, greeting, flow_id, transfer_to, voice,
                 time.time()))
        except sqlite3.IntegrityError:
            # A duplicate name: not stored (None), never an exception
            # leaking to the caller.
            self.conn.rollback()
            return None
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, name, greeting, flow_id, transfer_to, voice "
            "FROM agents WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(zip(("id", "name", "greeting", "flow_id",
                         "transfer_to", "voice"), row)) if row else None

    @_locked
    def get_agent(self, agent_id):
        row = self.conn.execute(
            "SELECT id, name, greeting, flow_id, transfer_to, voice "
            "FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return dict(zip(("id", "name", "greeting", "flow_id",
                         "transfer_to", "voice"), row)) if row else None

    @_locked
    def list_agents(self):
        rows = self.conn.execute(
            "SELECT id, name, greeting, flow_id, transfer_to, voice "
            "FROM agents ORDER BY id").fetchall()
        return [dict(zip(("id", "name", "greeting", "flow_id",
                          "transfer_to", "voice"), r)) for r in rows]

    @_locked
    def remove_agent(self, agent_id):
        self.conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        self.conn.commit()
        return self.get_agent(agent_id) is None

    # -- flows ----------------------------------------------------------
    @_locked
    def add_flow(self, name, steps_json):
        try:
            cur = self.conn.execute(
                "INSERT INTO flows (name, steps_json, created) "
                "VALUES (?, ?, ?)", (name, steps_json, time.time()))
        except sqlite3.IntegrityError:
            # A duplicate name: not stored (None), never an exception
            # leaking to the caller.
            self.conn.rollback()
            return None
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, name, steps_json FROM flows WHERE id = ?",
            (cur.lastrowid,)).fetchone()
        return dict(zip(("id", "name", "steps_json"), row)) if row else None

    @_locked
    def get_flow(self, flow_id):
        row = self.conn.execute(
            "SELECT id, name, steps_json FROM flows WHERE id = ?",
            (flow_id,)).fetchone()
        return dict(zip(("id", "name", "steps_json"), row)) if row else None

    @_locked
    def list_flows(self):
        rows = self.conn.execute(
            "SELECT id, name, steps_json FROM flows ORDER BY id").fetchall()
        return [dict(zip(("id", "name", "steps_json"), r)) for r in rows]

    @_locked
    def remove_flow(self, flow_id):
        self.conn.execute("DELETE FROM flows WHERE id = ?", (flow_id,))
        self.conn.commit()
        return self.get_flow(flow_id) is None

    # -- calls ----------------------------------------------------------
    @_locked
    def record_call(self, direction, number, agent_id, started, ended,
                    outcome, transcript):
        cur = self.conn.execute(
            "INSERT INTO calls (direction, number, agent_id, started, "
            "ended, outcome, transcript_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (direction, number, agent_id, started, ended, outcome,
             json.dumps(transcript)))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, direction, number, agent_id, started, ended, "
            "outcome FROM calls WHERE id = ?",
            (cur.lastrowid,)).fetchone()
        return dict(zip(("id", "direction", "number", "agent_id",
                         "started", "ended", "outcome"), row)) \
            if row else None

    @_locked
    def list_calls(self, limit=20):
        rows = self.conn.execute(
            "SELECT c.id, c.direction, c.number, c.agent_id, a.name, "
            "c.started, c.ended, c.outcome FROM calls c "
            "LEFT JOIN agents a ON a.id = c.agent_id "
            "ORDER BY c.id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(zip(("id", "direction", "number", "agent_id",
                          "agent", "started", "ended", "outcome"), r))
                for r in rows]

    # -- ledger ---------------------------------------------------------
    @_locked
    def append_event(self, kind, payload):
        row = self.conn.execute(
            "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_hash, seq = ("GENESIS", 0) if row is None else (row[1], row[0])
        seq = seq + 1
        ts = time.time()
        payload_json = json.dumps(payload, sort_keys=True)
        digest = store_base.chain_digest(
            "pipe_repr", prev_hash,
            {"seq": seq, "ts": ts, "kind": kind,
             "payload_json": payload_json})
        self.conn.execute(
            "INSERT INTO events (seq, ts, kind, payload_json, prev_hash, "
            "hash) VALUES (?, ?, ?, ?, ?, ?)",
            (seq, ts, kind, payload_json, prev_hash, digest))
        self.conn.commit()
        return seq

    @_locked
    def list_events(self, limit=20):
        rows = self.conn.execute(
            "SELECT seq, ts, kind, payload_json FROM events "
            "ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [{"seq": r[0], "ts": r[1], "kind": r[2],
                 "payload": json.loads(r[3])} for r in rows]

    @_locked
    def verify_chain(self):
        """Full-chain verification: every hash must recompute from
        its predecessor. Returns (True, "") or (False, reason)."""
        rows = self.conn.execute(
            "SELECT seq, ts, kind, payload_json, prev_hash, hash "
            "FROM events ORDER BY seq").fetchall()
        entries = [{"seq": seq, "ts": ts, "kind": kind,
                    "payload_json": payload_json,
                    "prev_hash": prev_hash, "hash": digest}
                   for seq, ts, kind, payload_json, prev_hash,
                   digest in rows]
        result = store_base.verify_entries(
            entries, "pipe_repr", store_base.LEGACY_GENESIS)
        if not result["ok"]:
            bad = rows[result["first_break"]["index"]]
            if result["first_break"]["reason"].startswith("link break"):
                return False, "event %d: prev_hash does not match" % bad[0]
            return False, "event %d: hash does not recompute" % bad[0]
        return True, ""

    @_locked
    def counts(self):
        out = {}
        for table in ("agents", "flows", "calls", "events"):
            out[table] = self.conn.execute(
                "SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        return out


# ---------------------------------------------------------------------------
# The listener: register to the trunk and answer inbound calls
# (one call at a time; a second inbound call gets 486 Busy Here)
# ---------------------------------------------------------------------------

_LISTENER = {"thread": None, "stop": None, "ua": None, "trunk": None,
             "agent_id": None, "busy": False, "started": None, "calls": 0}


def _serve_loop(stop_event, ua, trunk, agent_id, store,
                _renderer=None, _opener=None):
    """The listener thread: reads SIP messages off the UA socket.
    On INVITE, answers with the agent's flow (or 486 when busy)."""
    ua.sock.settimeout(0.5)
    while not stop_event.is_set():
        try:
            data, addr = ua.sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        msg = parse_sip(data)
        if msg is None or msg.is_response:
            continue
        if msg.method != "INVITE":
            # Anything else gets a polite refusal, not silence.
            if msg.method in ("BYE", "CANCEL", "OPTIONS"):
                try:
                    ua._send(
                        build_sip_response(200, "OK", msg, []), addr)
                except OSError:
                    pass
            continue
        if _LISTENER.get("busy"):
            try:
                ua._send(
                    build_sip_response(486, "Busy Here", msg, []), addr)
            except OSError:
                pass
            continue
        _LISTENER["busy"] = True
        try:
            _handle_inbound(ua, trunk, agent_id, msg, addr, store,
                            _renderer, _opener)
        finally:
            _LISTENER["busy"] = False
            _LISTENER["calls"] = _LISTENER.get("calls", 0) + 1


def _handle_inbound(ua, trunk, agent_id, request, addr, store,
                    _renderer=None, _opener=None):
    """Answer one inbound INVITE with the agent's flow, then BYE.
    Returns the call record (or None when the call never started)."""
    started = time.time()
    agent = store.get_agent(agent_id) if store is not None else None
    if agent is None:
        try:
            ua._send(
                build_sip_response(480, "Temporarily Unavailable",
                                   request, []), addr)
        except OSError:
            pass
        return None
    flow = store.get_flow(agent["flow_id"]) if agent.get("flow_id") else None
    if flow is None:
        try:
            ua._send(
                build_sip_response(480, "Temporarily Unavailable",
                                   request, []), addr)
        except OSError:
            pass
        return None
    steps, err = validate_flow(flow["steps_json"])
    if steps is None:
        try:
            ua._send(
                build_sip_response(480, "Temporarily Unavailable",
                                   request, []), addr)
        except OSError:
            pass
        return None
    offer = parse_sdp(request.body)
    if not offer:
        try:
            ua._send(
                build_sip_response(488, "Not Acceptable Here",
                                   request, []), addr)
        except OSError:
            pass
        return None
    # 100 Trying, then 200 OK with our SDP answer.
    try:
        ua._send(build_sip_response(100, "Trying", request, []), addr)
    except OSError:
        return None
    local_tag = _new_tag()
    # Advertise the local IP that reaches the caller, not the
    # socket's 0.0.0.0 bind address.
    answer_sdp = build_sdp_offer(
        _local_ip_for(offer.get("ip") or "127.0.0.1"),
        ua.rtp.local_port)
    to_header = request.header("to")
    if ";tag=" not in to_header:
        to_header = to_header + ";tag=%s" % local_tag
    headers = [
        ("Contact", "<sip:%s@%s:%d>"
                    % (trunk["user"], ua.local_ip, ua.local_port)),
        ("Content-Type", "application/sdp"),
    ]
    # 200 OK needs the tagged To header; build it by hand.
    lines = ["%s 200 OK" % _SIP_VERSION]
    for name in ("via", "from", "call-id", "cseq"):
        value = request.header(name)
        if value:
            lines.append("%s: %s" % (name.title(), value))
    lines.append("To: %s" % to_header)
    for name, value in headers:
        lines.append("%s: %s" % (name, value))
    body = answer_sdp.encode("utf-8")
    lines.append("Content-Length: %d" % len(body))
    lines.append("")
    lines.append("")
    try:
        ua._send("\r\n".join(lines).encode("utf-8") + body, addr)
    except OSError:
        return None
    # Wait briefly for the ACK, then start the media.
    ua.sock.settimeout(2.0)
    try:
        while True:
            data, _a = ua.sock.recvfrom(65535)
            ack = parse_sip(data)
            if ack is not None and not ack.is_response \
                    and ack.method == "ACK":
                break
    except (socket.timeout, OSError):
        pass
    finally:
        ua.sock.settimeout(0.5)
    ua.rtp.peer = (offer["ip"], offer["port"])
    ua.rtp.start()
    dialog = {
        "call_id": request.header("call-id"), "local_tag": local_tag,
        "remote_tag": _tag_from_header(request.header("from")),
        "cseq": 1, "uri": request.header("contact") or
        "sip:%s@%s" % (trunk["user"], trunk["host"]),
        "peer": addr,
        "from": request.header("to"),
        "to": request.header("from"),
        "trunk": trunk,
    }
    session = SIPSession(ua, dialog,
                         transfer_to=agent.get("transfer_to") or "",
                         _renderer=_renderer, _opener=_opener, pace=True)
    transcript = []
    if agent.get("greeting"):
        session.say(agent["greeting"])
        transcript.append({"step": -1, "kind": "say",
                           "detail": agent["greeting"]})
    transcript.extend(run_flow(steps, session))
    for note in session.notes:
        transcript.append({"step": -1, "kind": "note", "detail": note})
    if not session.ended:
        session.hangup()
    ended = time.time()
    number = _tag_from_header(request.header("from")) or "unknown"
    from_header = request.header("from") or ""
    m = re.search(r"sip:([^@>]+)@", from_header)
    if m:
        number = m.group(1)
    record = None
    if store is not None:
        record = store.record_call("in", number, agent_id, started, ended,
                                   "answered", transcript)
        store.append_event("call_in", {
            "number": number, "agent_id": agent_id,
            "duration_s": round(ended - started, 1),
            "steps": len(transcript)})
    return record


def start_listener(trunk, agent_id, store, _sock_factory=None,
                   _renderer=None, _opener=None):
    """Register to the trunk and start answering inbound calls.
    Returns (True, "") or (False, reason)."""
    if _LISTENER.get("thread") is not None \
            and _LISTENER["thread"].is_alive():
        return False, "the listener is already running"
    ua = SIPUA(sock_factory=_sock_factory)
    ok, reason = ua.register(trunk)
    if not ok:
        ua.close()
        return False, reason
    stop_event = threading.Event()
    _LISTENER.update({"thread": None, "stop": stop_event, "ua": ua,
                      "trunk": trunk, "agent_id": agent_id, "busy": False,
                      "started": time.time(), "calls": 0})
    thread = threading.Thread(
        target=_serve_loop,
        args=(stop_event, ua, trunk, agent_id, store, _renderer, _opener),
        daemon=True)
    _LISTENER["thread"] = thread
    thread.start()
    return True, ""


def stop_listener():
    """Stop the listener (and its SIP registration)."""
    thread = _LISTENER.get("thread")
    ua = _LISTENER.get("ua")
    if thread is None or not thread.is_alive():
        _LISTENER.update({"thread": None, "stop": None, "ua": None,
                          "trunk": None, "agent_id": None, "busy": False,
                          "started": None})
        return True, "the listener was not running"
    _LISTENER["stop"].set()
    thread.join(timeout=3.0)
    if ua is not None:
        ua.close()
    _LISTENER.update({"thread": None, "stop": None, "ua": None,
                      "trunk": None, "agent_id": None, "busy": False,
                      "started": None})
    return True, "the listener stopped"

# ---------------------------------------------------------------------------
# The funnel handler
# ---------------------------------------------------------------------------

_ACTIONS = ("agent", "flow", "run", "call", "serve", "stop", "status",
            "events")


def _int_arg(args, name, default=None):
    try:
        value = args.get(name)
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return None


def voice_agent(action="", mode="", agent_id=None, name="", greeting="",
                flow_id=None, steps="", inputs="", number="", trunk="",
                transfer_to="", timeout_s=None, limit=None, detail=None,
                _store=None, _renderer=None, _opener=None,
                _sock_factory=None):
    """voice_agent — a local voice-agent / call-flow engine plus an
    operator-supplied SIP trunk client. Actions: 'agent' (add/list/
    get/remove a voice agent), 'flow' (add/list/get/remove a call
    script), 'run' (execute a flow in a local text session — the
    operator supplies the caller's replies), 'call' (place a call
    through the operator's SIP trunk and run the agent's flow —
    asks first), 'serve' (register to the trunk and answer inbound
    calls — asks first), 'stop' (stop the listener), 'status'
    (counts + listener state + TTS/STT availability), 'events'
    (the hash-chained ledger). The parameters are the schema
    fields, all defaulted (the funnel dispatches handler(**args),
    and the registry-consistency guard requires every
    schema-required field to be a handler parameter)."""
    args = {"action": action, "mode": mode, "agent_id": agent_id,
            "name": name, "greeting": greeting, "flow_id": flow_id,
            "steps": steps, "inputs": inputs, "number": number,
            "trunk": trunk, "transfer_to": transfer_to,
            "timeout_s": timeout_s, "limit": limit, "detail": detail}
    action = str(action or "").strip().lower()
    if action not in _ACTIONS:
        return ("voice_agent refused: action must be 'agent', 'flow', "
                "'run', 'call', 'serve', 'stop', 'status' or 'events'")
    store = _store
    own_store = False
    if store is None:
        try:
            store = VoiceAgentStore()
            own_store = True
        except Exception as e:  # noqa: BLE001 — a dead store is a refusal
            return "voice_agent refused: the store could not open (%s)" % e
    try:
        return _voice_agent_inner(args, action, store, _renderer,
                                  _opener, _sock_factory)
    finally:
        if own_store:
            store.close()


def _voice_agent_inner(args, action, store, _renderer, _opener,
                       _sock_factory):
    mode = str(args.get("mode", "")).strip().lower()

    if action == "agent":
        if mode == "add":
            name = str(args.get("name", "")).strip()
            if not name or len(name) > _MAX_AGENT_NAME:
                return ("voice_agent refused: agent name is required "
                        "(at most 80 chars)")
            greeting = str(args.get("greeting", "")).strip()
            if len(greeting) > _MAX_SAY_CHARS:
                return ("voice_agent refused: greeting is at most "
                        "4000 chars")
            flow_id = _int_arg(args, "flow_id")
            if flow_id is not None and store.get_flow(flow_id) is None:
                return "voice_agent refused: no flow #%d" % flow_id
            transfer_to = str(args.get("transfer_to", "")).strip()
            if transfer_to:
                transfer_to, err = clean_number(transfer_to)
                if err:
                    return "voice_agent refused: transfer_to: %s" % err
            voice = str(args.get("voice", "")).strip()
            try:
                agent = store.add_agent(name, greeting, flow_id,
                                        transfer_to, voice)
            except sqlite3.IntegrityError:
                return ("voice_agent refused: an agent named %r already "
                        "exists" % name)
            if agent is None:
                return "voice_agent refused: the agent was not stored"
            store.append_event("agent_add", {"agent_id": agent["id"],
                                             "name": name})
            return ("Added voice agent #%d %r (flow #%s, transfer %s)."
                    % (agent["id"], name,
                       flow_id if flow_id is not None else "none",
                       transfer_to or "none"))
        if mode == "list":
            agents = store.list_agents()
            if not agents:
                return "No voice agents yet."
            lines = ["Voice agents:"]
            for a in agents:
                lines.append("#%d %s — flow #%s, transfer %s"
                             % (a["id"], a["name"],
                                a["flow_id"] if a["flow_id"] is not None
                                else "none",
                                a["transfer_to"] or "none"))
            return "\n".join(lines)
        if mode == "get":
            agent_id = _int_arg(args, "agent_id")
            if agent_id is None:
                return "voice_agent refused: agent_id is required"
            agent = store.get_agent(agent_id)
            if agent is None:
                return "voice_agent refused: no agent #%d" % agent_id
            return ("Agent #%d %r:\n  greeting: %s\n  flow: #%s\n"
                    "  transfer: %s\n  voice: %s"
                    % (agent["id"], agent["name"],
                       agent["greeting"] or "(none)",
                       agent["flow_id"] if agent["flow_id"] is not None
                       else "none",
                       agent["transfer_to"] or "none",
                       agent["voice"] or "(default)"))
        if mode == "remove":
            agent_id = _int_arg(args, "agent_id")
            if agent_id is None:
                return "voice_agent refused: agent_id is required"
            if store.get_agent(agent_id) is None:
                return "voice_agent refused: no agent #%d" % agent_id
            if not store.remove_agent(agent_id):
                return "voice_agent refused: the agent was not removed"
            store.append_event("agent_remove", {"agent_id": agent_id})
            return "Removed voice agent #%d." % agent_id
        return ("voice_agent refused: with 'agent', mode must be 'add', "
                "'list', 'get' or 'remove'")

    if action == "flow":
        if mode == "add":
            name = str(args.get("name", "")).strip()
            if not name or len(name) > _MAX_AGENT_NAME:
                return ("voice_agent refused: flow name is required "
                        "(at most 80 chars)")
            steps_json = str(args.get("steps", ""))
            steps, err = validate_flow(steps_json)
            if steps is None:
                return "voice_agent refused: %s" % err
            try:
                flow = store.add_flow(name, steps_json)
            except sqlite3.IntegrityError:
                return ("voice_agent refused: a flow named %r already "
                        "exists" % name)
            if flow is None:
                return "voice_agent refused: the flow was not stored"
            store.append_event("flow_add", {"flow_id": flow["id"],
                                            "name": name,
                                            "steps": len(steps)})
            return ("Added call flow #%d %r (%d steps)."
                    % (flow["id"], name, len(steps)))
        if mode == "list":
            flows = store.list_flows()
            if not flows:
                return "No call flows yet."
            lines = ["Call flows:"]
            for f in flows:
                try:
                    count = len(json.loads(f["steps_json"]))
                except Exception:
                    count = 0
                lines.append("#%d %s — %d steps"
                             % (f["id"], f["name"], count))
            return "\n".join(lines)
        if mode == "get":
            flow_id = _int_arg(args, "flow_id")
            if flow_id is None:
                return "voice_agent refused: flow_id is required"
            flow = store.get_flow(flow_id)
            if flow is None:
                return "voice_agent refused: no flow #%d" % flow_id
            return ("Flow #%d %r:\n%s"
                    % (flow["id"], flow["name"], flow["steps_json"]))
        if mode == "remove":
            flow_id = _int_arg(args, "flow_id")
            if flow_id is None:
                return "voice_agent refused: flow_id is required"
            if store.get_flow(flow_id) is None:
                return "voice_agent refused: no flow #%d" % flow_id
            if not store.remove_flow(flow_id):
                return "voice_agent refused: the flow was not removed"
            store.append_event("flow_remove", {"flow_id": flow_id})
            return "Removed call flow #%d." % flow_id
        return ("voice_agent refused: with 'flow', mode must be 'add', "
                "'list', 'get' or 'remove'")

    if action == "run":
        flow_id = _int_arg(args, "flow_id")
        if flow_id is None:
            return "voice_agent refused: flow_id is required"
        flow = store.get_flow(flow_id)
        if flow is None:
            return "voice_agent refused: no flow #%d" % flow_id
        steps, err = validate_flow(flow["steps_json"])
        if steps is None:
            return "voice_agent refused: %s" % err
        try:
            inputs = json.loads(str(args.get("inputs", "[]") or "[]"))
        except Exception:
            return "voice_agent refused: inputs must be JSON, e.g. [\"1\"]"
        if not isinstance(inputs, list):
            return "voice_agent refused: inputs must be a JSON list"
        transfer_to = str(args.get("transfer_to", "")).strip()
        session = TextSession(inputs, transfer_to=transfer_to)
        transcript = run_flow(steps, session)
        lines = ["Flow #%d %r — local run:" % (flow_id, flow["name"])]
        for entry in transcript:
            lines.append("  step %s %s: %s"
                         % (entry["step"], entry["kind"], entry["detail"]))
        return "\n".join(lines)

    if action == "call":
        agent_id = _int_arg(args, "agent_id")
        flow_id = _int_arg(args, "flow_id")
        agent = store.get_agent(agent_id) if agent_id is not None else None
        if agent_id is not None and agent is None:
            return "voice_agent refused: no agent #%d" % agent_id
        if agent is not None:
            flow_id = agent.get("flow_id")
            transfer_to = agent.get("transfer_to") or ""
            greeting = agent.get("greeting") or ""
        else:
            transfer_to = str(args.get("transfer_to", "")).strip()
            greeting = ""
            if transfer_to:
                transfer_to, err = clean_number(transfer_to)
                if err:
                    return "voice_agent refused: transfer_to: %s" % err
        if flow_id is None:
            return ("voice_agent refused: no flow — pass agent_id (with "
                    "a flow) or flow_id")
        flow = store.get_flow(flow_id)
        if flow is None:
            return "voice_agent refused: no flow #%d" % flow_id
        steps, err = validate_flow(flow["steps_json"])
        if steps is None:
            return "voice_agent refused: %s" % err
        number, err = clean_number(args.get("number"))
        if err:
            return "voice_agent refused: number: %s" % err
        trunk, err = parse_trunk(str(args.get("trunk", "") or ""))
        if trunk is None:
            return "voice_agent refused: trunk: %s" % err
        timeout_s = _int_arg(args, "timeout_s", _DEFAULT_CALL_SECONDS)
        if timeout_s is None or not 10 <= timeout_s <= _MAX_CALL_SECONDS:
            return ("voice_agent refused: timeout_s must be 10-%d"
                    % _MAX_CALL_SECONDS)
        return _place_call(store, agent_id, flow, steps, greeting,
                           transfer_to, number, trunk, timeout_s,
                           _renderer, _opener, _sock_factory)

    if action == "serve":
        agent_id = _int_arg(args, "agent_id")
        if agent_id is None:
            return "voice_agent refused: agent_id is required"
        agent = store.get_agent(agent_id)
        if agent is None:
            return "voice_agent refused: no agent #%d" % agent_id
        if agent.get("flow_id") is None:
            return ("voice_agent refused: agent #%d has no flow"
                    % agent_id)
        trunk, err = parse_trunk(str(args.get("trunk", "") or ""))
        if trunk is None:
            return "voice_agent refused: trunk: %s" % err
        ok, reason = start_listener(trunk, agent_id, store,
                                    _sock_factory=_sock_factory,
                                    _renderer=_renderer, _opener=_opener)
        if not ok:
            return "voice_agent refused: %s" % reason
        store.append_event("serve_start", {"agent_id": agent_id,
                                           "trunk_host": trunk["host"]})
        return ("Listening for calls on %s (agent #%d %r). Inbound "
                "calls run the agent's flow; stop with 'stop'."
                % (trunk["host"], agent_id, agent["name"]))

    if action == "stop":
        ok, reason = stop_listener()
        if ok:
            store.append_event("serve_stop", {})
        return "voice_agent: %s" % reason

    if action == "status":
        counts = store.counts()
        ok, chain_note = store.verify_chain()
        listener = _LISTENER
        running = listener.get("thread") is not None \
            and listener["thread"].is_alive()
        try:
            from .desktop import kokoro_voice as _kv  # noqa: F401
            tts = "Kokoro TTS available"
        except Exception:
            tts = "Kokoro TTS unavailable (speech on calls needs it)"
        key = _gemini_key()
        stt = ("Gemini key configured (speech gather available)"
               if key else "NO Gemini key — speech gather unavailable "
                        "(DTMF still works)")
        return ("Voice agent status:\n"
                "agents: %d\nflows: %d\ncalls: %d\nledger events: %d\n"
                "ledger chain: %s\n"
                "listener: %s%s\n"
                "TTS: %s\nSTT: %s"
                % (counts["agents"], counts["flows"], counts["calls"],
                   counts["events"],
                   "valid" if ok else "BROKEN (%s)" % chain_note,
                   "running" if running else "stopped",
                   (" (agent #%s, %d calls answered)"
                    % (listener.get("agent_id"), listener.get("calls", 0)))
                   if running else "",
                   tts, stt))

    if action == "events":
        limit = _int_arg(args, "limit", 20)
        if limit is None or not 1 <= limit <= 100:
            return "voice_agent refused: limit must be 1-100"
        ok, chain_note = store.verify_chain()
        events = store.list_events(limit)
        if not events:
            return "No voice-agent events yet."
        lines = ["Voice-agent events (chain %s):"
                 % ("valid" if ok else "BROKEN (%s)" % chain_note)]
        for e in events:
            lines.append("#%d %s: %s"
                         % (e["seq"], e["kind"],
                            json.dumps(e["payload"], sort_keys=True)))
        return "\n".join(lines)

    return "voice_agent refused: unknown action"  # pragma: no cover


def _place_call(store, agent_id, flow, steps, greeting, transfer_to,
                number, trunk, timeout_s, _renderer, _opener,
                _sock_factory):
    """Place one outbound call through the trunk and run the flow.
    The UA is closed on every path (the call is over either way)."""
    started = time.time()
    ua = SIPUA(sock_factory=_sock_factory)
    try:
        ok, reason = ua.register(trunk)
        if not ok:
            return "voice_agent refused: %s" % reason
        # Advertise the local IP that reaches the trunk, not the
        # socket's 0.0.0.0 bind address.
        offer = build_sdp_offer(_local_ip_for(trunk["host"]),
                                ua.rtp.local_port)
        dialog, reason = ua.invite(number, trunk, offer)
        if dialog is None:
            return "voice_agent refused: %s" % reason
        session = SIPSession(ua, dialog, transfer_to=transfer_to,
                             _renderer=_renderer, _opener=_opener,
                             pace=True)
        session.deadline = started + timeout_s
        transcript = []
        if greeting:
            session.say(greeting)
            transcript.append({"step": -1, "kind": "say",
                               "detail": greeting})
        transcript.extend(run_flow(steps, session))
        for note in session.notes:
            transcript.append({"step": -1, "kind": "note", "detail": note})
        if not session.ended:
            session.hangup()
        ended = time.time()
        record = store.record_call("out", number, agent_id, started,
                                   ended, "completed", transcript)
        store.append_event("call_out", {
            "number": number,
            "agent_id": agent_id if agent_id is not None else 0,
            "duration_s": round(ended - started, 1),
            "steps": len(transcript)})
        lines = ["Called %s (call #%d, %.1fs):"
                 % (number, record["id"] if record else 0,
                    ended - started)]
        for entry in transcript:
            lines.append("  step %s %s: %s"
                         % (entry["step"], entry["kind"], entry["detail"]))
        return "\n".join(lines)
    finally:
        ua.close()

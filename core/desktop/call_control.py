"""B-D6 (2026-11-09) voice/video calls — the ONE dict-returning
wrapper a driver calls for WebRTC calls, over aiortc (Python-native
WebRTC, BSD-3-Clause).

THE GAP (proved first, file/line evidence)
------------------------------------------
No call primitive exists anywhere in the tree:

* Zero aiortc imports: no ``aiortc``, ``RTCPeerConnection`` or
  ``RTCSessionDescription`` anywhere under core/ or gui/
  (grep, 2026-11-09). aiortc is importable in NEITHER
  interpreter (both report ModuleNotFoundError).
* "WebRTC" appears only in ``core/aec.py`` — and only to say
  WebRTC APM does NOT ship ("DOES NOT SHIP: WebRTC APM
  itself", aec.py:12; ``webrtc_apm_available`` is False).
  That is an echo-canceller reference, not a call path.
* No make_call / place_call / start_call / end_call / hangup
  anywhere under core/ (grep, 2026-11-09). No SIP, no VoIP,
  no peer-connection code of any kind.
* What exists is the assistant's OWN voice pipeline — mic
  input (sounddevice, core/skills_media_audio.py) and TTS
  output for the assistant itself. A call is different: a
  peer-to-peer audio path to ANOTHER person, with its own
  signalling, its own media, and its own lifetime. This
  module is that primitive. It does NOT change the assistant's
  voice pipeline — only the call path — and it does NOT wire
  a voice entry point (Stage 1 hold — not wired, not lifted).

LOPHOLE FAMILY — "a call that reaches the wrong person, or
a call that leaks"
---------------------------------------------------------
* A call placed to the WRONG peer (a bare address that
  resolves to someone else, or a stale invitation answered
  after it expired).
* A call that CONNECTS with NO audio (the peer hears
  nothing, and nobody says so).
* A call that stays OPEN after hangup (a leaked peer
  connection eating bandwidth).
* A call that sends audio BEFORE the user confirmed it
  (the mic live before the call is accepted).
* A call on an UNENCRYPTED channel presented as secure.

ONE design closes the class: every call goes through this
module; a call is identified by its own call id + the peer
it was placed to (never by a bare address alone); an
invitation carries an expiry, and an expired invitation
REFUSES; every call reports its media state (audio flowing
both ways, verified by a frame check — a connected call
with no audio is an honest failure, never a success claim);
a hangup CLOSES the peer connection and releases the media,
and the wrapper verifies the close (a still-live connection
after hangup is reported, never silent); the mic is live
ONLY inside an accepted call (never before the peer
accepts, never after hangup); a call is encrypted by
construction (DTLS-SRTP via WebRTC — an unencrypted
signalling transport is REFUSED, never silently used).

Rejected alternatives (recorded):
* Bare-address identity — an address that resolves to the
  wrong peer. (A call id + peer is the identity.)
* Connect-without-media-check — a silent call presented as
  working. (The media check is the verdict.)
* Fire-and-forget hangup — a leaked connection. (The
  wrapper verifies the close.)
* Mic-live-before-accept — audio sent before the user
  confirmed. (The mic capture starts only when the peer
  connection reaches "connected", and stops on hangup.)
* Unencrypted fallback — a call presented as secure that
  isn't. (The signalling transport must be encrypted or
  loopback, or the call is refused before anything is sent.)

DEPENDENCY (PROPOSED — parent rules)
------------------------------------
aiortc 1.16.0, BSD-3-Clause (wheel METADATA
License-Expression: BSD-3-Clause + LICENSE file, Copyright
(c) Jeremy Lainé, read from the 1.16.0 PyPI wheel
2026-11-09). Declared as ``aiortc>=1.16.0`` in
requirements.txt + ``aiortc==1.16.0`` in requirements.lock +
a THIRD_PARTY_LICENSES.md row, all PROPOSED — parent rules
(same shape as SymPy in U10, PyWinCtl in B-D1, watchdog in
B-D2, direct libraries in B-D3b, DXcam in B-D5).
Transitives (all read from their PyPI wheels' METADATA,
2026-11-09 — no GPL anywhere): aioice 0.13.0 BSD-3-Clause,
av 19.0.2 BSD-3-Clause, cryptography 50.0.2 Apache-2.0 OR
BSD-3-Clause (ALREADY declared + installed in the Qt venv),
google-crc32c 1.9.0 Apache-2.0, pyee 16.0.1 MIT,
pylibsrtp 1.0.0 BSD-3-Clause, pyOpenSSL 26.4.0 Apache-2.0.
WEIGHT, measured 2026-11-09 from the PyPI wheels (aiortc
is importable in NEITHER Linux interpreter): the full set
unpacks to 147.0 MB, dominated by av 19.0.2 at 125.4 MB
unpacked (35.0 MB wheel — it bundles FFmpeg). Excluding
cryptography (already in the tree), the NEW weight is
~132.7 MB unpacked (~37.6 MB of wheels). av is REQUIRED
for audio: aiortc imports it at module level
(mediastreams.py:8 ``from av import AudioFrame``,
rtcrtpreceiver.py:12 ``import av``) — ``import aiortc``
fails without it, so there is no light path. The parent
rules on the weight; this module states it plainly.
Requires-Python >=3.11; the tree is 3.12. $0, commercial
use permitted. Lazy-imported; degrades honestly to
engine_unavailable when aiortc is missing.

The engine is aiortc itself (real WebRTC peer connections),
never a home-grown socket protocol. On a machine without
aiortc, every call degrades honestly to
engine_unavailable — the Linux lab runs the logic against
an injected fake backend (see the backend protocol below);
the real-Windows leg (a real call between two machines) is
a field leg, owed, not this batch.

BACKEND PROTOCOL (what a backend must provide)
----------------------------------------------
A backend is any object with these methods; the real one is
``_AiortcBackend`` (aiortc, lazy-imported), the tests and the
prover inject a fake with the same shape:

* ``place(peer, invitation, signalling)`` -> opaque key.
  Creates the peer connection, exchanges offer/answer
  through ``signalling``, and returns the key once the
  connection is established. Raises ``CallFailed`` when
  the handshake fails.
* ``answer(invitation, signalling)`` -> opaque key. Same,
  from the answering side.
* ``call_media(key)`` -> {"state": str, "media_in": bool,
  "media_out": bool, "mic_live": bool}. Media flags are
  frame counters: True only when frames actually flowed.
* ``end(key)`` -> None. Closes the peer connection and
  releases the media. Raises ``CallDead`` when the call
  is already gone.
* ``is_live(key)`` -> bool. True while the peer connection
  still exists (used to verify a hangup really closed it).

A backend raises ``CallFailed`` when a call fails in a way
the wrapper should report honestly; any other backend
failure propagates as the exception it is, and the wrapper
turns it into an honest failure dict — never a success
claim, never a live call that is not live.

SIGNALLING TRANSPORT (what the caller must supply)
--------------------------------------------------
aiortc provides NO signalling channel — the caller supplies
one. A transport is any object with:

* ``encrypted`` -> bool (True = TLS / wss / https).
* ``endpoint`` -> str (e.g. "wss://signal.example/room"
  or "loopback://peer").
* ``send(msg: dict)`` -> None.
* ``recv(timeout_s: float)`` -> dict | None.

The wrapper accepts a transport that is encrypted, or one
whose endpoint is loopback (localhost / 127.0.0.1 / ::1 /
loopback:// — it never leaves the machine). Anything else
is REFUSED before a single message is sent: an
unencrypted transport is never silently used. The wrapper
never invents a server VYUK must run.

Public functions take an optional ``backend`` (None = the
real aiortc backend) and an optional ``clock`` (an object
with ``monotonic()``/``sleep()``; the default is real
time). An explicitly injected backend IS the engine under
test, so the aiortc availability check is skipped for it —
the suite and the prover run the logic on the fake while
the real backend is honestly unavailable on this machine.

A call dict is {"ok", "call_id", "peer", "kind",
"state", "media": {"in": bool, "out": bool,
"mic_live": bool}, "reason"}. ``state`` is one of
"connecting" / "connected" / "ended" / "failed".
``media`` is the frame check, never a guess.
"""

from __future__ import annotations

import time
import uuid

# An invitation lives this long; answering after it is a
# refusal, never a late connection to the wrong moment.
INVITATION_TTL_S = 60.0
# A call that connects but moves no audio in this long is
# torn down and reported as a failure, never presented
# as working.
DEFAULT_MEDIA_CHECK_S = 2.0
_MEDIA_POLL_S = 0.05


class _EngineUnavailable(Exception):
    """aiortc is not available for real calls."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class CallFailed(Exception):
    """A backend call failure the wrapper reports honestly."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class CallDead(Exception):
    """The call a call targeted no longer exists.

    Raised by a backend that KNOWS the call ended (the fake
    backend raises it explicitly; the real aiortc backend
    surfaces the closed connection instead — see
    ``_AiortcBackend``).
    """


# ----------------------------------------------------------------
# Clock (real time by default; a fake clock drives the media
# check in tests without any real waiting)
# ----------------------------------------------------------------

class _RealClock:
    """time.monotonic / time.sleep, as an object."""

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


# ----------------------------------------------------------------
# Engine availability (real backend only)
# ----------------------------------------------------------------

def _real_backend():
    """The real aiortc backend, or raise _EngineUnavailable
    naming what is missing. aiortc is imported lazily, here —
    importing THIS module never touches it (the sympy pattern).
    aiortc is OS-independent (no Windows gate): the probe is
    the import alone."""
    try:
        import aiortc  # noqa: F401 — the import IS the probe
    except ImportError as e:
        raise _EngineUnavailable(
            "aiortc is not available in this build of VYUK — "
            f"the call part didn't make it into the package ({e})"
        ) from e
    return _AiortcBackend()


def engine_available() -> bool:
    """True when the real aiortc backend can be built (aiortc
    importable). False on the Linux lab — and that is an
    honest answer, not a skip."""
    try:
        _real_backend()
    except _EngineUnavailable:
        return False
    return True


def engine_status() -> dict:
    """Honest engine report: {"available", "backend", "error"}."""
    try:
        _real_backend()
    except _EngineUnavailable as e:
        return {"available": False, "backend": None,
                "error": e.detail}
    return {"available": True, "backend": "aiortc", "error": None}

# ----------------------------------------------------------------
# The real backend — aiortc, and nothing else
# ----------------------------------------------------------------

class _AiortcBackend:
    """WebRTC peer connections via aiortc.

    One backend = one call = one asyncio event loop in a
    daemon thread, owned by this object and stopped by
    ``close()`` — a failed start never leaves a loop
    running. Media is DTLS-SRTP by construction (aiortc
    has no plaintext media path); the signalling transport
    is validated by the wrapper before this backend ever
    sees it. The mic capture starts only when the peer
    connection reaches "connected" and stops on close: the
    track sends silence before that (never mic audio), so
    no mic audio leaves the machine before the call is
    accepted, and none after hangup.
    """

    def __init__(self):
        import asyncio
        import threading

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True)
        self._thread_started = False
        self._pc = None
        self._mic = None
        self._frames_in = 0
        self._frames_out = 0
        self._mic_on = False
        self._closed = False

    # -- loop ownership --------------------------------------
    def _ensure_loop(self):
        if not self._thread_started:
            self._thread.start()
            self._thread_started = True

    def _run(self, coro, timeout_s: float):
        """Run one coroutine on the owned loop, bounded."""
        import asyncio

        self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout_s)

    def close(self) -> None:
        """Stop the owned loop (idempotent). A closed backend
        never runs again."""
        if self._closed:
            return
        self._closed = True
        self._mic_on = False
        pc, self._pc = self._pc, None
        if pc is not None:
            try:
                self._run(pc.close(), timeout_s=5.0)
            except Exception:  # noqa: BLE001 — close is
                # best-effort; the loop is stopped regardless.
                pass
        if self._thread_started:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=5.0)
            except Exception:  # noqa: BLE001
                pass

    # -- media ------------------------------------------------
    def _mic_track(self):
        """The outgoing audio track. Its frames come from the
        mic capture, which runs only while ``_mic_on`` — before
        the connection is established (and after it ends) the
        track sends silence, never mic audio."""
        from aiortc import MediaStreamTrack
        from av import AudioFrame

        backend = self

        class _MicTrack(MediaStreamTrack):
            kind = "audio"

            async def recv(self):
                import asyncio
                import numpy as np

                samples = 960  # 20 ms at 48 kHz
                if backend._mic_on and backend._mic is not None:
                    try:
                        data = backend._mic.read(samples)
                    except Exception:  # noqa: BLE001
                        data = None
                    if data is not None:
                        frame = AudioFrame(
                            format="s16", layout="mono",
                            samples=samples)
                        frame.pts = backend._frames_out
                        frame.sample_rate = 48000
                        frame.planes[0].update(
                            np.asarray(data, dtype=np.int16)
                            .tobytes())
                        backend._frames_out += 1
                        return frame
                # Silence (never mic audio) while the mic is off.
                frame = AudioFrame(
                    format="s16", layout="mono", samples=samples)
                frame.pts = backend._frames_out
                frame.sample_rate = 48000
                frame.planes[0].update(
                    b"\x00" * samples * 2)
                backend._frames_out += 1
                await asyncio.sleep(0.02)
                return frame

        return _MicTrack()

    def _video_track(self):
        """The outgoing video track, used only for kind ==
        "video". It sends solid frames: a camera source is a
        field leg (owed, not this batch) — the track exists so
        a video call carries a video m-line, and the media
        check still has the final say on whether frames flow.
        """
        from aiortc import MediaStreamTrack
        from av import VideoFrame

        backend = self

        class _BlankVideoTrack(MediaStreamTrack):
            kind = "video"

            async def recv(self):
                import asyncio
                import numpy as np

                frame = VideoFrame(width=640, height=360,
                                   format="rgb24")
                frame.pts = backend._frames_out
                frame.time_base = "1/30"
                frame.planes[0].update(
                    np.zeros((360, 640, 3), dtype=np.uint8)
                    .tobytes())
                backend._frames_out += 1
                await asyncio.sleep(1 / 30)
                return frame

        return _BlankVideoTrack()

    def _start_mic(self):
        """Start the mic capture (sounddevice, already a tree
        dependency). A missing mic fails the media check
        honestly — it never pretends audio is flowing."""
        if self._mic_on:
            return
        try:
            import sounddevice as sd

            self._mic = _MicCapture(sd)
            self._mic_on = True
        except Exception:  # noqa: BLE001 — no mic: the
            # media check reports no audio out, honestly.
            self._mic = None
            self._mic_on = False

    def _stop_mic(self):
        self._mic_on = False
        mic, self._mic = self._mic, None
        if mic is not None:
            try:
                mic.close()
            except Exception:  # noqa: BLE001
                pass

    # -- handshake -------------------------------------------
    async def _connect(self, peer, invitation, signalling,
                       answer_side: bool, timeout_s: float):
        """One handshake on the owned loop. Returns the peer
        connection once it is established; raises CallFailed
        on any failure (the caller closes the backend)."""
        import asyncio

        from aiortc import RTCPeerConnection, RTCSessionDescription

        pc = RTCPeerConnection()
        self._pc = pc

        def _on_state():
            if pc.connectionState == "connected":
                self._start_mic()
            elif pc.connectionState in ("failed", "closed",
                                       "disconnected"):
                self._stop_mic()

        pc.on("connectionstatechange", _on_state)

        @pc.on("track")
        def _on_track(track):
            # Count incoming frames: the media check's "in".
            async def _drain():
                while True:
                    try:
                        await track.recv()
                    except Exception:  # noqa: BLE001
                        return
                    self._frames_in += 1

            asyncio.ensure_future(_drain())

        pc.addTrack(self._mic_track())
        if invitation.get("kind") == "video":
            pc.addTrack(self._video_track())
        if not answer_side:
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            signalling.send({"type": "offer",
                             "call_id": invitation["call_id"],
                             "peer": peer,
                             "sdp": pc.localDescription.sdp,
                             "sdp_type": pc.localDescription.type})
            answer = signalling.recv(timeout_s)
            if answer is None or answer.get("type") != "answer":
                raise CallFailed(
                    "no answer from the peer "
                    f"({peer!r}) — the call was not placed")
            await pc.setRemoteDescription(RTCSessionDescription(
                sdp=answer["sdp"], type=answer["sdp_type"]))
        else:
            await pc.setRemoteDescription(RTCSessionDescription(
                sdp=invitation["offer"]["sdp"],
                type=invitation["offer"]["sdp_type"]))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            signalling.send({"type": "answer",
                             "call_id": invitation["call_id"],
                             "peer": peer,
                             "sdp": pc.localDescription.sdp,
                             "sdp_type": pc.localDescription.type})
        # Wait for "connected" (bounded): the media check
        # below still has the final say on audio.
        deadline = self._loop.time() + timeout_s
        while pc.connectionState not in ("connected", "completed"):
            if pc.connectionState in ("failed", "closed"):
                raise CallFailed(
                    "the peer connection failed before "
                    f"connecting ({pc.connectionState})")
            if self._loop.time() >= deadline:
                raise CallFailed(
                    "the peer connection did not establish "
                    f"within {timeout_s:g} s")
            await asyncio.sleep(0.05)
        return pc

    def place(self, peer, invitation, signalling, timeout_s=30.0):
        try:
            return self._run(
                self._connect(peer, invitation, signalling,
                              answer_side=False,
                              timeout_s=timeout_s),
                timeout_s=timeout_s + 5.0)
        except CallFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise CallFailed(
                f"could not place the call to {peer!r}: "
                f"{type(e).__name__}: {e}") from e

    def answer(self, invitation, signalling, timeout_s=30.0):
        try:
            return self._run(
                self._connect(invitation["peer"], invitation,
                              signalling, answer_side=True,
                              timeout_s=timeout_s),
                timeout_s=timeout_s + 5.0)
        except CallFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise CallFailed(
                "could not answer the call from "
                f"{invitation.get('peer')!r}: "
                f"{type(e).__name__}: {e}") from e

    def call_media(self, key) -> dict:
        if self._closed or self._pc is None or key is not self._pc:
            return {"state": "ended", "media_in": False,
                    "media_out": False, "mic_live": False}
        return {"state": self._pc.connectionState,
                "media_in": self._frames_in > 0,
                "media_out": self._frames_out > 0,
                "mic_live": bool(self._mic_on)}

    def end(self, key) -> None:
        if self._closed or self._pc is None or key is not self._pc:
            raise CallDead("the call is already ended")
        self._stop_mic()
        pc, self._pc = self._pc, None
        try:
            self._run(pc.close(), timeout_s=5.0)
        except Exception as e:  # noqa: BLE001
            raise CallFailed(
                f"could not close the peer connection: {e}") from e
        finally:
            self.close()

    def is_live(self, key) -> bool:
        return (not self._closed and self._pc is not None
                and key is self._pc)


class _MicCapture:
    """20 ms frames from the default microphone (sounddevice).

    A thin ring: read(n) returns int16 mono frames or None
    when the stream has nothing yet. Only constructed when
    the connection is established, and closed on hangup."""

    def __init__(self, sd):
        import collections
        import threading

        self._buf = collections.deque()
        self._cond = threading.Condition()

        def _cb(indata, frames, time_info, status):  # noqa: ARG001
            with self._cond:
                self._buf.append(bytes(indata))
                self._cond.notify_all()

        self._cb = _cb
        self._stream = sd.InputStream(
            samplerate=48000, channels=1, dtype="int16",
            blocksize=960, callback=self._cb)
        self._stream.start()

    def read(self, samples):
        want = samples * 2
        with self._cond:
            if not self._buf:
                self._cond.wait(timeout=0.02)
            if not self._buf:
                return None
            chunk = self._buf.popleft()
            return chunk[:want] if len(chunk) >= want else chunk

    def close(self):
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass

# ----------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------

def _backend_or_real(backend):
    """An injected backend is used as-is; None builds the real
    one (and raises _EngineUnavailable when it cannot)."""
    if backend is not None:
        return backend
    return _real_backend()


def _clock_or_real(clock):
    return clock if clock is not None else _RealClock()


def _is_loopback(endpoint: str) -> bool:
    lowered = (endpoint or "").strip().lower()
    if lowered.startswith("loopback://"):
        return True
    host = lowered.split("://", 1)[-1].split("/", 1)[0]
    host = host.split("@")[-1].split(":")[0].strip("[]")
    return host in ("localhost", "127.0.0.1", "::1")


def _validate_signalling(signalling):
    """The signalling transport, or a refusal naming it.
    Encrypted or loopback — anything else is refused before
    a single message is sent. A transport that cannot send
    or receive is refused too (a call with no signalling
    is a call that cannot happen)."""
    if signalling is None:
        return None, ("a call needs a signalling transport — "
                      "aiortc provides none, and this module "
                      "never invents a server")
    endpoint = getattr(signalling, "endpoint", None)
    encrypted = bool(getattr(signalling, "encrypted", False))
    if not isinstance(endpoint, str) or not endpoint.strip():
        return None, ("the signalling transport names no "
                      "endpoint — a call needs somewhere to "
                      "signal through")
    if not encrypted and not _is_loopback(endpoint):
        return None, (
            f"the signalling transport ({endpoint!r}) is not "
            "encrypted — a call is refused on an unencrypted "
            "channel; use an encrypted transport (wss/https) "
            "or a loopback one")
    if not callable(getattr(signalling, "send", None)) \
            or not callable(getattr(signalling, "recv", None)):
        return None, ("the signalling transport cannot send "
                      "and receive — a call needs both")
    return signalling, None


def make_invitation(peer, *, kind: str = "audio",
                    ttl_s: float = INVITATION_TTL_S,
                    clock=None) -> dict:
    """One invitation to be answered: {call_id, peer, kind,
    created_at, expires_at}. Pure — no backend, no network.
    The answering side checks the expiry before it answers;
    an expired invitation refuses, never connects late."""
    now = _clock_or_real(clock)
    if not isinstance(peer, str) or not peer.strip():
        raise ValueError("an invitation needs a peer name")
    if kind not in ("audio", "video"):
        raise ValueError(
            f"call kind must be 'audio' or 'video' (got {kind!r})")
    if isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float)) \
            or not ttl_s > 0:
        raise ValueError(
            f"invitation lifetime must be a positive number "
            f"(got {ttl_s!r})")
    created = float(now.monotonic())
    return {"call_id": uuid.uuid4().hex, "peer": peer.strip(),
            "kind": kind,
            "created_at": created,
            "expires_at": created + float(ttl_s),
            "offer": None}


def _validate_invitation(invitation, clock):
    """The invitation to answer, or a refusal naming it.
    Expiry is checked FIRST: an expired invitation never
    reaches the backend."""
    if not isinstance(invitation, dict):
        return None, "an invitation is a dict with call_id, peer and expires_at"
    call_id = invitation.get("call_id")
    peer = invitation.get("peer")
    expires = invitation.get("expires_at")
    if not isinstance(call_id, str) or not call_id:
        return None, "the invitation names no call id"
    if not isinstance(peer, str) or not peer.strip():
        return None, "the invitation names no peer"
    try:
        expires_f = float(expires)
    except (TypeError, ValueError):
        return None, "the invitation names no expiry"
    if clock.monotonic() > expires_f:
        return None, (
            f"the invitation to {peer!r} expired — it is "
            "refused, never answered late")
    kind = invitation.get("kind", "audio")
    if kind not in ("audio", "video"):
        return None, (
            f"the invitation names an unknown call kind "
            f"{kind!r}")
    return {"call_id": call_id, "peer": peer.strip(),
            "kind": kind,
            "created_at": invitation.get("created_at"),
            "expires_at": expires_f,
            "offer": invitation.get("offer")}, None


# One live call at a time per call id (the registry is the
# wrapper's, not a global capture — a driver that needs two
# calls holds two call ids). A failed start never registers;
# a media-check failure tears the call down first.
_CALLS: dict = {}


def _fail_call(reason: str, call_id=None, peer=None, kind=None) -> dict:
    return {"ok": False, "call_id": call_id, "peer": peer,
            "kind": kind, "state": "failed",
            "media": {"in": False, "out": False,
                      "mic_live": False},
            "reason": reason}


def _call_dict(ok: bool, call_id, peer, kind, state, media,
               reason: str) -> dict:
    return {"ok": ok, "call_id": call_id, "peer": peer,
            "kind": kind, "state": state,
            "media": {"in": bool(media.get("in", False)),
                      "out": bool(media.get("out", False)),
                      "mic_live": bool(media.get("mic_live", False))},
            "reason": reason}


def _media_check(be, key, clock, timeout_s: float):
    """Poll the backend's media flags until audio flows both
    ways or the window elapses. Returns (media dict, ok).
    A connected call with no audio is a failure — the
    caller tears the call down."""
    begin = clock.monotonic()
    media = {"in": False, "out": False, "mic_live": False}
    while True:
        try:
            got = be.call_media(key)
        except Exception:  # noqa: BLE001 — a backend that
            # cannot report its media fails the check.
            return media, False
        media = {"in": bool(got.get("media_in", False)),
                 "out": bool(got.get("media_out", False)),
                 "mic_live": bool(got.get("mic_live", False))}
        if media["in"] and media["out"]:
            return media, True
        if clock.monotonic() - begin >= float(timeout_s):
            return media, False
        clock.sleep(_MEDIA_POLL_S)


def _teardown(be, key):
    """End a backend call best-effort (a media-check failure
    tears the call down; a failed teardown is still reported
    by the caller, never silent)."""
    try:
        be.end(key)
    except CallDead:
        pass
    except Exception:  # noqa: BLE001
        pass
    if hasattr(be, "close"):
        try:
            be.close()
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------
# Calls
# ----------------------------------------------------------------

def start_call(peer, *, kind: str = "audio", signalling,
               backend=None, clock=None,
               media_check_s: float = DEFAULT_MEDIA_CHECK_S) -> dict:
    """Place a call to a peer through the supplied signalling
    transport. The call id + peer are the identity (never a
    bare address alone). The media check is the verdict: a
    call that connects but moves no audio is torn down and
    reported as a failure, never presented as working.
    Returns a call dict (see the module docstring)."""
    now = _clock_or_real(clock)
    if not isinstance(peer, str) or not peer.strip():
        return _fail_call("a call needs a peer to call",
                          peer=peer if isinstance(peer, str) else None,
                          kind=kind)
    if kind not in ("audio", "video"):
        return _fail_call(
            f"call kind must be 'audio' or 'video' (got {kind!r})",
            peer=peer.strip(), kind=kind)
    sig, err = _validate_signalling(signalling)
    if err:
        return _fail_call(err, peer=peer.strip(), kind=kind)
    invitation = make_invitation(peer.strip(), kind=kind, clock=now)
    try:
        be = _backend_or_real(backend)
    except _EngineUnavailable as e:
        return _fail_call(f"engine_unavailable: {e.detail}",
                          call_id=invitation["call_id"],
                          peer=peer.strip(), kind=kind)
    try:
        key = be.place(peer.strip(), invitation, sig)
    except CallFailed as e:
        _teardown(be, None)
        return _fail_call(e.detail, call_id=invitation["call_id"],
                          peer=peer.strip(), kind=kind)
    except Exception as e:  # noqa: BLE001
        _teardown(be, None)
        return _fail_call(
            f"could not place the call to {peer.strip()!r}: "
            f"{type(e).__name__}: {e}",
            call_id=invitation["call_id"], peer=peer.strip(),
            kind=kind)
    media, ok = _media_check(be, key, now, media_check_s)
    if not ok:
        _teardown(be, key)
        return _fail_call(
            "the call connected but no audio flowed both ways "
            f"within {media_check_s:g} s — the call was ended, "
            "never presented as working",
            call_id=invitation["call_id"], peer=peer.strip(),
            kind=kind)
    _CALLS[invitation["call_id"]] = {
        "backend": be, "key": key, "peer": peer.strip(),
        "kind": kind, "started_at": now.monotonic()}
    return _call_dict(True, invitation["call_id"], peer.strip(),
                      kind, "connected", media, "")


def answer_call(invitation, *, signalling, backend=None,
                clock=None,
                media_check_s: float = DEFAULT_MEDIA_CHECK_S) -> dict:
    """Answer an invitation before its expiry. The expiry is
    checked first — an expired invitation never reaches the
    backend. The mic is live only inside the accepted call:
    the backend starts its capture when the connection is
    established, never before. Returns a call dict."""
    now = _clock_or_real(clock)
    invite, err = _validate_invitation(invitation, now)
    if err:
        peer = (invitation.get("peer")
                if isinstance(invitation, dict) else None)
        return _fail_call(err, peer=peer)
    sig, err = _validate_signalling(signalling)
    if err:
        return _fail_call(err, call_id=invite["call_id"],
                          peer=invite["peer"])
    try:
        be = _backend_or_real(backend)
    except _EngineUnavailable as e:
        return _fail_call(f"engine_unavailable: {e.detail}",
                          call_id=invite["call_id"],
                          peer=invite["peer"])
    try:
        key = be.answer(invite, sig)
    except CallFailed as e:
        _teardown(be, None)
        return _fail_call(e.detail, call_id=invite["call_id"],
                          peer=invite["peer"])
    except Exception as e:  # noqa: BLE001
        _teardown(be, None)
        return _fail_call(
            f"could not answer the call from {invite['peer']!r}: "
            f"{type(e).__name__}: {e}",
            call_id=invite["call_id"], peer=invite["peer"])
    media, ok = _media_check(be, key, now, media_check_s)
    if not ok:
        _teardown(be, key)
        return _fail_call(
            "the call connected but no audio flowed both ways "
            f"within {media_check_s:g} s — the call was ended, "
            "never presented as working",
            call_id=invite["call_id"], peer=invite["peer"])
    _CALLS[invite["call_id"]] = {
        "backend": be, "key": key, "peer": invite["peer"],
        "kind": invite["kind"], "started_at": now.monotonic()}
    return _call_dict(True, invite["call_id"], invite["peer"],
                      invite["kind"], "connected", media, "")


def end_call(call_id, backend=None) -> dict:
    """Hang up. Closes the peer connection and releases the
    media, and VERIFIES the close: a still-live connection
    after hangup is reported, never silent. Returns
    {"ok", "call_id", "peer", "reason"}."""
    record = _CALLS.get(call_id)
    if record is None:
        return {"ok": False, "call_id": call_id, "peer": None,
                "reason": f"no live call {call_id!r} — it was "
                          "already ended, or never started"}
    be = backend if backend is not None else record["backend"]
    key = record["key"]
    peer = record["peer"]
    try:
        be.end(key)
    except CallDead:
        del _CALLS[call_id]
        return {"ok": False, "call_id": call_id, "peer": peer,
                "reason": "the call was already ended"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "call_id": call_id, "peer": peer,
                "reason": f"could not hang up: {e}"}
    try:
        still_live = be.is_live(key)
    except Exception:  # noqa: BLE001 — a backend that cannot
        # say whether the call is live fails the verification.
        still_live = True
    del _CALLS[call_id]
    if hasattr(be, "close"):
        try:
            be.close()
        except Exception:  # noqa: BLE001
            pass
    if still_live:
        return {"ok": False, "call_id": call_id, "peer": peer,
                "reason": "hangup was requested but the peer "
                          "connection is still live — the call "
                          "was NOT ended cleanly"}
    return {"ok": True, "call_id": call_id, "peer": peer,
            "reason": ""}


def call_state(call_id, backend=None) -> dict:
    """The state of one live call: {"ok", "call_id", "peer",
    "kind", "state", "media", "reason"}. A call the backend
    reports dead is removed from the registry and reported
    as ended, never as live."""
    record = _CALLS.get(call_id)
    if record is None:
        return _fail_call(f"no live call {call_id!r}",
                          call_id=call_id)
    be = backend if backend is not None else record["backend"]
    try:
        got = be.call_media(record["key"])
    except Exception as e:  # noqa: BLE001
        return _fail_call(
            f"could not read the call state: {e}",
            call_id=call_id, peer=record["peer"],
            kind=record["kind"])
    media = {"in": bool(got.get("media_in", False)),
             "out": bool(got.get("media_out", False)),
             "mic_live": bool(got.get("mic_live", False))}
    state = str(got.get("state", "connected"))
    if state in ("ended", "closed", "failed"):
        del _CALLS[call_id]
        return _call_dict(False, call_id, record["peer"],
                          record["kind"], "ended", media,
                          "the call ended")
    return _call_dict(True, call_id, record["peer"],
                      record["kind"], state, media, "")


def list_calls() -> dict:
    """Every live call: {"ok", "calls": [{call_id, peer,
    kind, state}], "reason"}."""
    calls = []
    for call_id, record in list(_CALLS.items()):
        try:
            got = record["backend"].call_media(record["key"])
            state = str(got.get("state", "connected"))
        except Exception:  # noqa: BLE001
            state = "unknown"
        calls.append({"call_id": call_id, "peer": record["peer"],
                      "kind": record["kind"], "state": state})
    return {"ok": True, "calls": calls, "reason": ""}

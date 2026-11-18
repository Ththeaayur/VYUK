"""Native-messaging host (click-anywhere Batch 1, Plan 9 §1.2, M-7/M-10/M-12/M-13).

Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).

Stdio, 4-byte length-prefixed JSON (the native-messaging protocol),
every message carrying ``"v": 1`` (v1 accepts v1 only). Stateless:
the extension holds only a cache rebuilt from the handshake on every
reconnect. State lives in VYUK's main process, reached over the
localhost HTTP channel (``browser_host_channel``).

Startup (win32 only) calls ``SetProcessDpiAwarenessContext(-4)`` and
records whether the per-monitor-v2 rung succeeded; the level travels
in the handshake REQUEST. On Linux the call is skipped and the level
is ``"unknown"`` — recorded honestly, never faked.

Chrome's appended argv (origin + ``--parent-window``) is accepted and
ignored except logging the origin. The host module takes no argv of
its own.

Spawn (see ``browser_extension/host_launcher.c``): the C stub spawns
``python.exe -m core.browser.bridge_host`` with working directory =
the VYUK install root, ``PYTHONPATH=<install root>`` and
``PYTHONUNBUFFERED=1``.

Token rotation (P-9, Q-2, Q-9): on 403 OR connection-refused/reset
on ``/events`` (or any channel call) the host re-reads the rendezvous
file and re-handshakes ONCE. On success the goal PAUSES and RE-GATES
on resume — exposed as the return/status value
``{"rotated": True, "pause_and_regate": True}`` the main process acts
on; the host itself never re-dispatches. If the re-read fails: file
absent -> "VYUK not running — start the app"; file present but the
re-handshake fails -> "VYUK restarted — re-handshaking".

Message budget (M-13): host->browser per-message cap 256 KB,
browser->host cap 1 MB, chunk 256 KB, per-element text cap 1 KB
(truncate with a ``"truncated"`` flag). A message over its cap is
never sent; snapshots over the cap are chunked with sequence numbers.

Convert commands (S-8) carry ``{v, kind: "convert",
observation_pass_id, latest_pass_id}`` — the host converts only when
the two pass ids are equal, else refuses "observation superseded —
re-observe". The actual viewport->screen math lives in
``core/browser/viewport_convert.py`` (a separate Batch 1 piece):
it is imported if present, else converts refuse with "conversion
module unavailable". The conversion is NOT reimplemented here.
"""

import ctypes
import http.client
import json
import os
import queue
import struct
import sys
import threading
import uuid
from pathlib import Path

# -- message budget (M-13) ---------------------------------------------
HOST_TO_BROWSER_CAP = 256 * 1024
BROWSER_TO_HOST_CAP = 1024 * 1024
CHUNK_SIZE = 256 * 1024
ELEMENT_TEXT_CAP = 1024

_TOKEN_HEADER = "X-VYUK-Browser-Token"
_RENDEZVOUS_NAME = "browser_host.json"
_LONG_POLL_CLIENT_TIMEOUT_S = 35.0
_NO_MESSAGE = object()

# SetProcessDpiAwarenessContext: DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
_DPI_AWARENESS_CONTEXT_V2 = -4


def _app_data_dir() -> Path:
    return Path(os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming"))


def rendezvous_path() -> Path:
    """Full path of the rendezvous file (app data)."""
    return _app_data_dir() / _RENDEZVOUS_NAME


def read_rendezvous() -> dict:
    """Read ``<app data>/browser_host.json`` (``{port, token, pid,
    started}``). Raises :class:`FileNotFoundError` when VYUK has not
    written one — the caller reports "VYUK not running — start the
    app", never a silent hang."""
    path = rendezvous_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(
            "VYUK not running — start the app (no browser rendezvous file "
            f"at {path})"
        ) from None


def detect_dpi_awareness() -> str:
    """The host's DPI-awareness level: ``"per-monitor-v2" |
    "per-monitor-v1" | "system" | "unaware" | "unknown"``.

    win32 only: calls ``SetProcessDpiAwarenessContext(-4)`` (the
    per-monitor-v2 rung, ``main.py:39``'s rung) and records whether it
    succeeded; on failure reads the current level via
    ``shcore.GetProcessDpiAwareness``. On Linux the call is skipped
    and the level is ``"unknown"`` — recorded honestly, never faked.
    """
    if sys.platform != "win32":
        return "unknown"
    try:
        user32 = ctypes.windll.user32
        ok = user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_V2)
        if ok:
            return "per-monitor-v2"
    except Exception:  # noqa: BLE001 — fall through to the read path
        pass
    try:
        shcore = ctypes.windll.shcore
        level = ctypes.c_int(0)
        result = shcore.GetProcessDpiAwareness(0, ctypes.byref(level))
        if result == 0:  # S_OK
            return {0: "unaware", 1: "system", 2: "per-monitor-v1"}.get(
                level.value, "unknown")
    except Exception:  # noqa: BLE001 — unknown is the honest answer
        pass
    return "unknown"


# -- native-messaging framing ------------------------------------------
def encode_message(message: dict) -> bytes:
    """4-byte little-endian length-prefixed JSON. Raises
    :class:`ValueError` when the payload is over the 256 KB
    host->browser cap — a message over its cap is never sent."""
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(payload) > HOST_TO_BROWSER_CAP:
        raise ValueError(
            f"message over the 256 KB host->browser cap ({len(payload)} bytes) — chunk it")
    return struct.pack("<I", len(payload)) + payload


def _read_exact(stream, count: int) -> bytes:
    chunks = []
    remaining = count
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("stdin closed mid-message")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream):
    """Read one length-prefixed message from a binary stream.
    Returns the decoded dict, or None on clean EOF before any byte.
    Raises :class:`ValueError` when the length exceeds the 1 MB
    browser->host cap."""
    header = stream.read(4)
    if not header:
        return None
    if len(header) < 4:
        raise EOFError("stdin closed mid-length-prefix")
    (length,) = struct.unpack("<I", header)
    if length > BROWSER_TO_HOST_CAP:
        raise ValueError(
            f"message over the 1 MB browser->host cap ({length} bytes)")
    payload = _read_exact(stream, length)
    data = json.loads(payload.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("native message must be a JSON object")
    return data


def chunk_payload(payload: bytes):
    """Split a payload into 256 KB chunks with sequence numbers.
    Returns a list of ``(seq, total, chunk_bytes)``."""
    total = max(1, (len(payload) + CHUNK_SIZE - 1) // CHUNK_SIZE)
    return [(i, total, payload[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE])
            for i in range(total)]


def truncate_element_text(text: str):
    """Per-element text cap (M-13): 1 KB of text; returns
    ``(text, truncated_flag)``."""
    if text is None:
        return "", False
    text = str(text)
    if len(text.encode("utf-8")) <= ELEMENT_TEXT_CAP:
        return text, False
    clipped = text.encode("utf-8")[:ELEMENT_TEXT_CAP].decode("utf-8", "ignore")
    return clipped, True


# -- channel client -----------------------------------------------------
class ChannelClient:
    """Minimal stdlib client for the localhost channel. Constructed
    from a rendezvous dict; rebuilt from a re-read rendezvous file
    on rotation."""

    def __init__(self, rendezvous: dict):
        self.port = int(rendezvous["port"])
        self.token = rendezvous["token"]

    def _request(self, method, path, body=None, timeout=_LONG_POLL_CLIENT_TIMEOUT_S):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            headers = {_TOKEN_HEADER: self.token,
                       "Content-Type": "application/json"}
            raw = json.dumps(body).encode("utf-8") if body is not None else None
            conn.request(method, path, body=raw, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            try:
                payload = json.loads(data.decode("utf-8", "replace")) if data else {}
            except ValueError:
                payload = {}
            return resp.status, payload
        finally:
            conn.close()

    def handshake(self, host_id, extension_id, dpi_awareness):
        return self._request("POST", "/handshake", {
            "v": 1, "host_id": host_id, "extension_id": extension_id,
            "dpi_awareness": dpi_awareness,
        }, timeout=10.0)

    def get_events(self):
        return self._request("GET", "/events")

    def post_observation(self, observation):
        return self._request("POST", "/observation", observation, timeout=10.0)

    def get_goal(self):
        return self._request("GET", "/goal", timeout=10.0)


# -- the host -----------------------------------------------------------
class BridgeHost:
    """The stateless native host: stdio on one side, the localhost
    channel on the other. ``run()`` returns a status dict the main
    process acts on; it never re-dispatches after a rotation."""

    def __init__(self, host_id=None, extension_id=None):
        self.host_id = host_id or uuid.uuid4().hex
        self.extension_id = extension_id
        self.dpi_awareness = detect_dpi_awareness()
        self.client = None
        self.latest_pass_id = None
        self._stop = threading.Event()

    # -- connection + rotation -----------------------------------------
    def connect(self):
        """Read the rendezvous file and handshake. Returns the
        handshake payload. Raises FileNotFoundError (no rendezvous
        file) or RuntimeError (handshake refused, with the reason)."""
        rendezvous = read_rendezvous()
        self.client = ChannelClient(rendezvous)
        status, payload = self.client.handshake(
            self.host_id, self.extension_id, self.dpi_awareness)
        if status != 200:
            raise RuntimeError(
                f"handshake refused ({status}): {payload.get('error', 'unknown reason')}")
        goal = payload.get("goal") or {}
        self.latest_pass_id = payload.get("latest_pass_id")
        return payload

    def rotate(self):
        """Re-read the rendezvous file and re-handshake ONCE. Returns
        ``{"rotated": True, "pause_and_regate": True}`` on success —
        the goal PAUSES and RE-GATES on resume (the approval died
        with the old process) — or ``{"rotated": False, "reason": ...}``
        with the reason by file presence."""
        try:
            rendezvous = read_rendezvous()
        except FileNotFoundError:
            return {"rotated": False, "reason": "VYUK not running — start the app"}
        try:
            client = ChannelClient(rendezvous)
            status, payload = client.handshake(
                self.host_id, self.extension_id, self.dpi_awareness)
        except (ConnectionError, OSError):
            return {"rotated": False, "reason": "VYUK restarted — re-handshaking"}
        if status != 200:
            # A DPI refusal is not a rotation: the new process refuses
            # this host outright; surface its reason, do not retry.
            if "dpi_awareness" in str(payload.get("error", "")):
                return {"rotated": False,
                        "reason": str(payload.get("error"))}
            return {"rotated": False, "reason": "VYUK restarted — re-handshaking"}
        self.client = client
        self.latest_pass_id = payload.get("latest_pass_id")
        return {"rotated": True, "pause_and_regate": True}

    @staticmethod
    def _is_rotation_trigger(exc) -> bool:
        """403s are handled by status, not exception; connection
        failures are the Q-9 trigger."""
        return isinstance(exc, (ConnectionError, OSError, http.client.HTTPException))

    # -- converts (S-8) -------------------------------------------------
    def handle_convert(self, cmd: dict):
        """Convert only when ``observation_pass_id == latest_pass_id``.
        Returns ``{"ok": True, "result": ...}`` or ``{"ok": False,
        "reason": ...}``."""
        obs_id = cmd.get("observation_pass_id")
        latest = cmd.get("latest_pass_id", self.latest_pass_id)
        if obs_id != latest:
            return {"ok": False, "reason": "observation superseded — re-observe"}
        try:
            from . import viewport_convert as _vc  # noqa: PLC0415 — owned by another Batch 1 piece
        except ImportError:
            return {"ok": False, "reason": "conversion module unavailable"}
        # The real entry point is convert_viewport_to_screen(observation);
        # the older names are kept as fallbacks for test stubs.
        convert = (getattr(_vc, "convert_viewport_to_screen", None)
                   or getattr(_vc, "convert", None)
                   or getattr(_vc, "viewport_to_screen", None)
                   or getattr(_vc, "convert_point", None))
        if not callable(convert):
            return {"ok": False, "reason": "conversion module unavailable"}
        try:
            # The command may embed the observation payload under
            # "observation"; otherwise the command itself carries the
            # conversion terms (pass ids + DOM/window terms).
            return {"ok": True, "result": convert(cmd.get("observation") or cmd)}
        except Exception as exc:  # noqa: BLE001 — a failed convert refuses, never crashes the host
            return {"ok": False, "reason": f"conversion failed: {exc}"}

    # -- native messages from the extension ------------------------------
    def handle_native_message(self, message: dict):
        """One message arrived over stdio from the extension. Returns
        a reply dict to write back, or None. The token never leaves
        the host: the extension asks the host, and the host asks the
        channel."""
        kind = message.get("kind")
        if message.get("v") != 1:
            return {"v": 1, "kind": "error", "error": "message v must be 1"}
        if kind == "observation":
            if self.client is None:
                return {"v": 1, "kind": "error", "error": "not connected — handshake first"}
            obs = dict(message)
            try:
                status, payload = self.client.post_observation(obs)
            except (ConnectionError, OSError, http.client.HTTPException) as exc:
                return {"v": 1, "kind": "rotation", "status": self._rotate_after(exc)}
            if status == 403:
                return {"v": 1, "kind": "rotation", "status": self.rotate()}
            if obs.get("observation_pass_id") is not None:
                self.latest_pass_id = obs["observation_pass_id"]
            return {"v": 1, "kind": "observation-ack",
                    "latest_pass_id": payload.get("latest_pass_id")}
        if kind == "goal-status":
            # The orphaned-alarm clearer (N-19, R-7) asks through here:
            # the host GETs /goal and applies alarm_clear_decision.
            if self.client is None:
                return {"v": 1, "kind": "error", "error": "not connected — handshake first"}
            try:
                from .browser_host_channel import alarm_clear_decision
                status, payload = self.client.get_goal()
            except (ConnectionError, OSError, http.client.HTTPException) as exc:
                return {"v": 1, "kind": "rotation", "status": self._rotate_after(exc)}
            if status == 403:
                return {"v": 1, "kind": "rotation", "status": self.rotate()}
            if status != 200:
                return {"v": 1, "kind": "error", "error": "GET /goal failed"}
            goal = payload.get("goal")
            self.latest_pass_id = payload.get("latest_pass_id")
            return {"v": 1, "kind": "goal-status", "goal": goal,
                    "alarm": alarm_clear_decision(goal),
                    "latest_pass_id": self.latest_pass_id}
        if kind == "hello":
            return {"v": 1, "kind": "hello", "host_id": self.host_id,
                    "dpi_awareness": self.dpi_awareness}
        return {"v": 1, "kind": "error", "error": f"unknown native message kind {kind!r}"}

    def _rotate_after(self, exc):
        if self._is_rotation_trigger(exc):
            return self.rotate()
        return {"rotated": False, "reason": str(exc)}

    # -- main loop --------------------------------------------------------
    def run(self, stdin=None, stdout=None):
        """Handshake, then bridge stdio <-> channel until stdin closes
        or a rotation pauses the goal. Returns a status dict:

        - ``{"ok": True, "reason": "stdin closed"}`` — clean end;
        - ``{"rotated": True, "pause_and_regate": True}`` — rotation
          succeeded; the goal pauses and re-gates, the host never
          re-dispatches;
        - ``{"rotated": False, "reason": ...}`` — rotation failed;
        - ``{"ok": False, "reason": ...}`` — could not start.
        """
        stdin = stdin if stdin is not None else sys.stdin.buffer
        stdout = stdout if stdout is not None else sys.stdout.buffer
        try:
            self.connect()
        except FileNotFoundError as exc:
            return {"ok": False, "reason": str(exc)}
        except RuntimeError as exc:
            return {"ok": False, "reason": str(exc)}

        inbound = queue.Queue()
        events_q = queue.Queue()

        def _stdin_reader():
            try:
                while not self._stop.is_set():
                    message = read_message(stdin)
                    if message is None:
                        break
                    inbound.put(message)
            except (EOFError, ValueError, OSError):
                pass
            finally:
                inbound.put(None)  # stdin closed

        def _events_poller():
            # Holds GET /events (30 s long-poll); re-issues immediately
            # on empty timeout, per the plan.
            while not self._stop.is_set():
                try:
                    status, payload = self.client.get_events()
                except (ConnectionError, OSError, http.client.HTTPException) as exc:
                    if self._is_rotation_trigger(exc):
                        events_q.put(("rotation", self.rotate()))
                        return
                    events_q.put(("error", {"ok": False, "reason": str(exc)}))
                    return
                if status == 403:
                    events_q.put(("rotation", self.rotate()))
                    return
                if status != 200:
                    events_q.put(("error", {"ok": False,
                                            "reason": f"GET /events failed ({status})"}))
                    return
                cmd = payload.get("command")
                if cmd is not None:
                    events_q.put(("command", cmd))
                # else: empty timeout — re-issue immediately.

        threading.Thread(target=_stdin_reader, name="vyuk-host-stdin", daemon=True).start()
        threading.Thread(target=_events_poller, name="vyuk-host-events", daemon=True).start()

        while not self._stop.is_set():
            try:
                message = inbound.get(timeout=0.05)
            except queue.Empty:
                message = _NO_MESSAGE
            if message is not _NO_MESSAGE:
                if message is None:
                    self._stop.set()
                    return {"ok": True, "reason": "stdin closed"}
                reply = self.handle_native_message(message)
                if reply is not None:
                    stdout.write(encode_message(reply))
                    stdout.flush()
            # Then drain channel events.
            try:
                while True:
                    tag, value = events_q.get_nowait()
                    if tag == "rotation":
                        self._stop.set()
                        return value
                    if tag == "error":
                        self._stop.set()
                        return value
                    if tag == "command":
                        self._dispatch_command(value)
            except queue.Empty:
                pass
        return {"ok": True, "reason": "stopped"}

    def _dispatch_command(self, cmd: dict):
        """A command arrived from the main process over /events."""
        if not isinstance(cmd, dict):
            return
        if cmd.get("kind") == "convert":
            result = self.handle_convert(cmd)
            # The result returns upstream over POST /observation — the
            # only upstream endpoint — tagged as a convert-result.
            try:
                self.client.post_observation({
                    "v": 1, "kind": "convert-result",
                    "observation_pass_id": cmd.get("observation_pass_id"),
                    "result": result,
                })
            except (ConnectionError, OSError, http.client.HTTPException):
                pass
            return
        if cmd.get("kind") == "clear-alarm":
            # The main process sends "clear-alarm" on the next /events
            # poll when a goal ends (secondary to the N-19 clearer).
            return
        # Other command kinds (observe, click, ...) are dispatched to
        # the extension over stdio by the main loop's caller; the host
        # itself only bridges — nothing to do here for unknown kinds.


def main(argv=None):
    """Entry point: ``python.exe -m core.browser.bridge_host``.
    Chrome's appended argv (origin + ``--parent-window``) is accepted
    and ignored except logging the origin; the host takes no argv of
    its own."""
    argv = list(sys.argv[1:] if argv is None else argv)
    origin = argv[0] if argv else None
    if origin:
        print(f"bridge_host: chrome origin {origin}", file=sys.stderr, flush=True)
    host = BridgeHost()
    status = host.run()
    if status.get("rotated") is True:
        print(f"bridge_host: rotated — goal pauses and re-gates: {status}",
              file=sys.stderr, flush=True)
        return 0
    if status.get("ok"):
        return 0
    print(f"bridge_host: {status.get('reason', status)}", file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

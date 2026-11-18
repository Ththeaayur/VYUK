"""B-R1-3r (2026-11-09) Kokoro subprocess boundary — the ONE
place the main process talks to the Kokoro engine.

THE GAP (proved first, file/line evidence)
------------------------------------------
Before this batch the Kokoro engine ran IN the product process:
``core/desktop/kokoro_voice.py:326`` imported ``kokoro_onnx``
in ``_load_engine()`` and ``_RealBackend.speak()`` built a
``Kokoro`` ONNX session in-process, and the session TTS path
(``core/voice/local_client_voices.py:186``) did the same. Every
synthesis therefore ran GPL-3.0 code in-process: kokoro-onnx
0.6.1 hard-requires phonemizer>=3.4.0 (unconditional
Requires-Dist) and ``kokoro_onnx/tokenizer.py:85`` calls
``phonemizer.phonemize()`` on every synthesis; phonemizer 3.4.0
is GPL-3.0 (parent ruling, B-R1-3) and the espeak-ng it drives
is GPL-3.0 too (THIRD_PARTY row 120). The shipped v6.2.0
carried both in-process in the Kokoro path — a HARD BLOCK on
shipping the Kokoro path as licence-clean.

This module is the fix. It follows the tree's own precedent,
``core/loopback.py`` + ``core/loopback_worker.py`` (the worker
is the only module allowed to import the isolated package, it
runs as a fresh subprocess, it reports back over stdout JSON,
and the test suite asserts the isolation by AST): the GPL
engine lives in a child process, the main process never
imports it, and the boundary is enforced by a test, not a
comment. One deliberate difference from the loopback shape:
loopback is a one-shot probe, Kokoro is a session — loading
the ~310 MB model on every utterance would add seconds to
every reply, so the child is PERSISTENT (spawned on first
speak, session cached in the child) with an explicit reset
(kill + respawn), as the brief allows.

LOPHOLE FAMILY — "GPL code leaking back into the main process"
---------------------------------------------------------------
* A forgotten import: some main-process module imports
  kokoro-onnx (or phonemizer, or espeak-ng) directly — a
  status call that builds a session, a helper that imports
  the engine for a presence check.
* A silent bypass: the child crashes or times out and the
  main process falls back to in-process synthesis — the
  isolation exists only when it works.
* Leaked state: the child holds a session across calls in a
  way the main process cannot see or reset.
* A zombie: the child outlives the main process.

ONE design closes the class: ONE subprocess boundary. The
main process NEVER imports kokoro-onnx, phonemizer, or
espeak-ng — enforced by a source-grep test
(``tests/test_kokoro_isolate.py``) that fails if any
main-process module imports them, with the child script
(``kokoro_child.py``, which imports kokoro-onnx only inside
``main()``) and the known residual
(``core/voice/local_client_voices.py`` — the session TTS path,
NOT rewired in this batch, separate parent decision) named
explicitly. All Kokoro synthesis goes through ONE child
process (stdlib subprocess, text in via a JSON line on stdin,
PCM out via a JSON line on stdout, base64 float32). A child
crash or timeout is an honest failure — never a silent
in-process fallback; there IS no in-process path left in the
wrapper to fall back to. The child is stateless per failure:
any failure kills it, so the next call spawns fresh; an
explicit ``reset()``/``close()`` kills it on demand. The
child dies with the main process three ways: closing stdin
is the polite death (the child reads stdin to EOF and exits —
that EOF is the parent-death watch, so a crashed parent
kills the child too), ``close()`` kills it on shutdown, and
an ``atexit`` hook closes it when the interpreter exits
normally. What crosses the boundary is text in, PCM out —
nothing else: no file writes from the child, no shared
memory, no session handle.

Rejected alternatives (recorded):
* In-process synthesis with a comment saying "isolated" (a
  comment is not a boundary).
* A child the main process can silently bypass on failure
  (the isolation exists only when it works).
* A shared-memory or file-based boundary that lets the child
  write arbitrary files (the boundary is text in, PCM out,
  nothing else).
* A one-shot child per utterance (stateless per call, but it
  reloads the ~310 MB model on every reply — seconds of
  latency on every spoken reply; the persistent child with
  kill-on-failure keeps the same isolation with one model
  load per session).
* A child that outlives the main process (a zombie).

DEPENDENCY
----------
No new pip dependency. Stdlib only (subprocess + json +
base64 + threading + queue). The child runs the EXISTING
kokoro-onnx 0.6.1 + phonemizer 3.4.0 (GPL-3.0) + espeak-ng
(GPL-3.0) set — ~90 MB installed in .venv-kokoro, 0 new
bytes — in a separate process. The GPL components stay
GPL-3.0 (licence texts + source pointer + written offer, as
the THIRD_PARTY rows already record); the isolation means
the main product process no longer links them.
"""

import atexit
import base64
import collections
import json
import os
from .. import config_spec
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

__all__ = [
    "KokoroChild",
    "probe_engine",
    "clear_probe_cache",
    "PROTOCOL_VERSION",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_SPAWN_TIMEOUT_S",
    "DEFAULT_PROBE_TIMEOUT_S",
]

PROTOCOL_VERSION = 1
# Per-render call: generous enough for a first-call model load
# (~310 MB ONNX) plus a long render, short enough that a wedged
# child never hangs the voice path.
DEFAULT_TIMEOUT_S = 120.0
# Spawn + ping round trip (interpreter start, no model load — a
# ping never builds a session).
DEFAULT_SPAWN_TIMEOUT_S = 60.0
# Throwaway `python -c "import kokoro_onnx"` probe.
DEFAULT_PROBE_TIMEOUT_S = 30.0
# A response line is one JSON object carrying base64 float32
# PCM. The wrapper caps text at 10,000 chars (~10-12 min of
# speech worst case, ~100 MB base64); 256 MB bounds a runaway
# child without ever cutting a legal render.
_MAX_LINE_BYTES = 256 * 1024 * 1024
# stderr is drained, never left to fill the pipe: the last lines
# ride in failure reasons so a dead child can explain itself.
_MAX_STDERR_LINES = 50
_STDERR_LINE_CAP = 65536

SPEED_MIN = 0.0  # exclusive
SPEED_MAX = 4.0  # inclusive

_OVERSIZE = object()  # reader sentinel: a response line over the cap


def _child_script_path() -> Path:
    return Path(__file__).resolve().with_name("kokoro_child.py")


def _default_python() -> str:
    # The shipped product runs the child in its own interpreter
    # (which has kokoro-onnx); VYUK_KOKORO_PYTHON lets a dev run
    # or a test point at another one (e.g. .venv-kokoro).
    return config_spec.explicit("VYUK_KOKORO_PYTHON") or sys.executable


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


# -- engine availability probe (subprocess, never in-process) -------
_PROBE_CACHE: dict[str, dict] = {}


def probe_engine(python=None, timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
                 *, refresh: bool = False) -> dict:
    """Ask a throwaway child interpreter whether kokoro-onnx
    imports. Returns {"available", "reason", "python"}. The
    import happens in the CHILD — this process never imports
    kokoro-onnx, phonemizer, or espeak-ng. The verdict is cached
    per interpreter (an import check costs ~0.3-1 s); pass
    refresh=True to re-probe, e.g. after an install."""
    key = str(python or _default_python())
    if not refresh and key in _PROBE_CACHE:
        return dict(_PROBE_CACHE[key])
    try:
        proc = subprocess.run(
            [key, "-c", "import kokoro_onnx"],
            capture_output=True, text=True, timeout=float(timeout_s))
        if proc.returncode == 0:
            result = {"available": True, "reason": None, "python": key}
        else:
            reason = _last_line(proc.stderr) or (
                f"kokoro-onnx import failed (exit {proc.returncode})")
            result = {"available": False, "reason": reason, "python": key}
    except Exception as e:  # noqa: BLE001 — the reason IS the report
        result = {"available": False,
                  "reason": f"{type(e).__name__}: {e}", "python": key}
    _PROBE_CACHE[key] = result
    return dict(result)


def clear_probe_cache() -> None:
    """Drop cached probe verdicts (e.g. after an engine install)."""
    _PROBE_CACHE.clear()


# -- reader threads -----------------------------------------------------
class _LineReader(threading.Thread):
    """Reads stdout lines into a queue. EOF puts None (the child
    died or closed stdout); an over-cap line puts _OVERSIZE and
    stops reading — a runaway child cannot OOM the parent."""

    def __init__(self, stream, out_queue: queue.Queue):
        super().__init__(daemon=True)
        self._stream = stream
        self._out = out_queue

    def run(self):
        try:
            while True:
                line = self._stream.readline(_MAX_LINE_BYTES)
                if line == "":
                    self._out.put(None)
                    return
                if len(line) >= _MAX_LINE_BYTES and not line.endswith("\n"):
                    self._out.put(_OVERSIZE)
                    return
                self._out.put(line)
        except Exception:  # noqa: BLE001 — a dead stream ends the reader
            try:
                self._out.put(None)
            except Exception:  # noqa: BLE001
                pass


class _StderrDrain(threading.Thread):
    """Drains stderr into a bounded deque so the child can never
    block on a full stderr pipe, and a dead child can explain
    itself in the failure reason."""

    def __init__(self, stream, lines: collections.deque):
        super().__init__(daemon=True)
        self._stream = stream
        self._lines = lines

    def run(self):
        try:
            while True:
                line = self._stream.readline(_STDERR_LINE_CAP)
                if line == "":
                    return
                self._lines.append(line.rstrip("\n"))
        except Exception:  # noqa: BLE001 — a dead stream ends the drain
            pass


class _ChildFailed(Exception):
    """The child could not be spawned, or did not answer its ping."""


# -- the boundary -------------------------------------------------------
class KokoroChild:
    """One persistent Kokoro child process.

    Spawned lazily on the first synthesize (a ping proves the
    process + protocol before any render). One JSON line in per
    request, exactly one JSON line out per response. The Kokoro
    session lives in the child and is cached across calls there;
    ANY failure (crash, timeout, malformed response, render
    error) kills the child, so the next call spawns fresh —
    there is no poisoned session and no silent in-process
    fallback (there is no in-process path left to fall back
    to). ``reset()``/``close()`` kill the child on demand.
    The child dies with the main process: closing stdin is the
    polite death (EOF is the parent-death watch), ``close()``
    kills it, and an atexit hook closes it at interpreter exit.
    """

    def __init__(self, *, python=None, child_script=None,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 spawn_timeout_s: float = DEFAULT_SPAWN_TIMEOUT_S,
                 extra_args=()):
        for name, value in (("timeout_s", timeout_s),
                            ("spawn_timeout_s", spawn_timeout_s)):
            if (isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value != value or value <= 0):
                raise ValueError(f"{name} must be a positive number")
        self._python = str(python or _default_python())
        self._script = str(child_script or _child_script_path())
        self._timeout_s = float(timeout_s)
        self._spawn_timeout_s = float(spawn_timeout_s)
        self._extra_args = tuple(extra_args)
        self._proc = None
        self._responses: queue.Queue = queue.Queue()
        self._stderr_lines: collections.deque = collections.deque(
            maxlen=_MAX_STDERR_LINES)
        self._atexit_registered = False

    # -- lifecycle ------------------------------------------------------
    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 — a kill is best-effort
            pass
        for handle in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if handle is not None:
                    handle.close()
            except Exception:  # noqa: BLE001
                pass

    def _spawn(self) -> None:
        if self.is_alive:
            return
        self._kill()  # clear a dead proc, if any
        # Fresh queues per spawn: a dead child's reader may still
        # post its EOF sentinel to the old queue — it must never be
        # read as this child's ping reply. (A stale None here made
        # the second spawn after a reset fail its ping.)
        self._responses = queue.Queue()
        self._stderr_lines = collections.deque(maxlen=_MAX_STDERR_LINES)
        kwargs: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
            "close_fds": True,
        }
        if os.name == "posix":
            # Own session: a signal to the parent's process group
            # (e.g. Ctrl-C) does not hit the child; the stdin-EOF
            # death watch still kills it when the parent dies.
            kwargs["start_new_session"] = True
        if os.name == "nt":
            # No console flash on Windows (the tree's CREATE_NO_WINDOW
            # idiom, e.g. core/funnel/tools_handlers_frame_launch.py).
            kwargs["creationflags"] = 0x08000000
        try:
            proc = subprocess.Popen(
                [self._python, self._script, *self._extra_args], **kwargs)
        except Exception as e:  # noqa: BLE001 — the reason IS the report
            raise _ChildFailed(
                f"could not spawn the Kokoro child ({self._python} "
                f"{self._script}): {e}") from e
        self._proc = proc
        _LineReader(proc.stdout, self._responses).start()
        _StderrDrain(proc.stderr, self._stderr_lines).start()
        if not self._atexit_registered:
            atexit.register(self.close)
            self._atexit_registered = True
        # Ping: prove the process + protocol before any render.
        try:
            proc.stdin.write(json.dumps(
                {"command": "ping",
                 "protocol": PROTOCOL_VERSION}) + "\n")
            proc.stdin.flush()
            line = self._responses.get(timeout=self._spawn_timeout_s)
        except Exception as e:  # noqa: BLE001
            self._kill()
            raise _ChildFailed(
                "the Kokoro child did not answer its ping "
                f"({type(e).__name__}: {e})") from e
        if line is None or line is _OVERSIZE:
            self._kill()
            raise _ChildFailed(
                "the Kokoro child died or misbehaved during its ping")
        try:
            reply = json.loads(line)
        except Exception as e:  # noqa: BLE001
            self._kill()
            raise _ChildFailed(
                f"the Kokoro child answered its ping with bad JSON: {e}"
            ) from e
        if not isinstance(reply, dict) or not reply.get("ok") \
                or reply.get("command") != "ping":
            self._kill()
            raise _ChildFailed(
                "the Kokoro child answered its ping with an unexpected "
                f"reply: {line.strip()[:200]}")

    def reset(self) -> None:
        """Kill the child (if any). The next synthesize spawns
        fresh — this IS the reset; there is no in-child state to
        clear any other way."""
        self._kill()

    def close(self) -> None:
        """Kill the child (if any), idempotent. Closing stdin is
        the polite death — the child reads EOF and exits — but a
        wedged child is killed, never waited on forever."""
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        self._kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- validation -------------------------------------------------------
    @staticmethod
    def _validate_request(request) -> str | None:
        """Refuse a bad request BEFORE any spawn — a non-text
        input never needs a child to be refused. Returns an error
        string, or None when the request is well formed."""
        if not isinstance(request, dict):
            return "request must be an object"
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            return "text must be a non-empty string"
        for field in ("voice", "lang", "model_path", "voices_path"):
            value = request.get(field)
            if not isinstance(value, str) or not value.strip():
                return f"{field} must be a non-empty string"
        speed = request.get("speed")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            return "speed must be a number"
        speed = float(speed)
        if speed != speed or speed <= SPEED_MIN or speed > SPEED_MAX:
            return (f"speed must be in ({SPEED_MIN}, {SPEED_MAX}] "
                    f"(got {speed})")
        return None

    # -- the call ---------------------------------------------------------
    def synthesize(self, request) -> dict:
        """Render one utterance through the child. Returns
        {"ok": True, "rate": 24000, "samples": np.float32 array}
        or {"ok": False, "reason": ...}. NEVER raises for a child
        fault, and NEVER falls back to in-process synthesis: a
        failure is an honest failure, and the child is killed so
        the next call spawns fresh."""
        err = self._validate_request(request)
        if err is not None:
            return {"ok": False, "reason": err}
        try:
            self._spawn()
        except _ChildFailed as e:
            return {"ok": False, "reason": str(e)}
        proc = self._proc
        if proc is None or proc.poll() is not None:
            self._kill()
            return {"ok": False,
                    "reason": "the Kokoro child died before the request "
                              f"was sent ({self._stderr_tail()})"}
        try:
            proc.stdin.write(json.dumps({
                "text": request["text"],
                "voice": request["voice"],
                "lang": request["lang"],
                "speed": float(request["speed"]),
                "model_path": request["model_path"],
                "voices_path": request["voices_path"],
                "protocol": PROTOCOL_VERSION}) + "\n")
            proc.stdin.flush()
        except Exception as e:  # noqa: BLE001 — a dead pipe is a dead child
            self._kill()
            return {"ok": False,
                    "reason": f"could not send to the Kokoro child "
                              f"({type(e).__name__}: {e}) — the child was "
                              "killed; no in-process fallback"}
        try:
            line = self._responses.get(timeout=self._timeout_s)
        except queue.Empty:
            self._kill()
            return {"ok": False,
                    "reason": (f"the Kokoro child did not answer within "
                               f"{self._timeout_s:.0f} s (killed; no "
                               "in-process fallback)")}
        if line is None:
            self._kill()
            return {"ok": False,
                    "reason": ("the Kokoro child died while rendering "
                               f"({self._stderr_tail()}) — killed; no "
                               "in-process fallback")}
        if line is _OVERSIZE:
            self._kill()
            return {"ok": False,
                    "reason": "the Kokoro child answered with an over-long "
                              "line (killed; no in-process fallback)"}
        try:
            reply = json.loads(line)
        except Exception as e:  # noqa: BLE001
            self._kill()
            return {"ok": False,
                    "reason": f"the Kokoro child answered with bad JSON "
                              f"({e}) — killed; no in-process fallback"}
        if not isinstance(reply, dict) or "ok" not in reply:
            self._kill()
            return {"ok": False,
                    "reason": "the Kokoro child answered with a malformed "
                              "reply — killed; no in-process fallback"}
        if not reply["ok"]:
            # A render error kills the child too: a poisoned session
            # never survives into the next call.
            reason = str(reply.get("reason") or "the Kokoro child refused "
                         "the render without a reason")
            self._kill()
            return {"ok": False, "reason": reason}
        try:
            import numpy as np  # noqa: PLC0415 — decode only, lazy

            rate = int(reply["rate"])
            count = int(reply["samples"])
            raw = base64.b64decode(str(reply["pcm_b64"]))
            if len(raw) != count * 4:
                raise ValueError(
                    f"PCM length {len(raw)} does not match {count} samples")
            samples = np.frombuffer(raw, dtype=np.float32).copy()
        except Exception as e:  # noqa: BLE001 — a bad payload is a failure
            self._kill()
            return {"ok": False,
                    "reason": f"the Kokoro child returned an unusable "
                              f"render ({e}) — killed; no in-process fallback"}
        return {"ok": True, "rate": rate, "samples": samples}

    def _stderr_tail(self) -> str:
        tail = " | ".join(self._stderr_lines)
        return f"child stderr: {tail[-500:]}" if tail else "no child stderr"

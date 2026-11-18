"""B-R1-3r (2026-11-09) Kokoro child process — the ONLY place in
the tree where kokoro-onnx (and, through it, phonemizer +
espeak-ng, both GPL-3.0) may run.

NEVER import this module from the main process. It is executed
only as a subprocess entry point by ``core.desktop.kokoro_isolate``
(``[sys.executable, kokoro_child.py]``); its kokoro-onnx import
lives INSIDE ``main()`` so even an accidental import of this
module cannot pull the GPL engine into the main process. The
child imports NO tree module at all (no ``core.*``, no relative
imports): the parent passes the model + voices paths in the
request, so the child needs only kokoro-onnx + numpy + stdlib.

PROTOCOL — JSON lines on stdin/stdout, one request, exactly one
response line. Diagnostics go to stderr only, never stdout.
* Request: ``{"text", "voice", "lang", "speed", "model_path",
  "voices_path"}`` or ``{"command": "ping"}``.
* Response (ok): ``{"ok": true, "protocol": 1, "rate": 24000,
  "samples": N, "pcm_b64": "<base64 float32 LE>"}``.
* Response (failure): ``{"ok": false, "protocol": 1, "reason":
  "..."}`` — every request gets exactly one response line, so
  the parent can always correlate; a render failure never
  leaves the parent waiting.
* stdin EOF ends the loop and the child exits 0. That EOF is
  the parent-death watch: when the main process dies (or closes
  the pipe on shutdown), the OS closes its end and the child
  sees EOF and exits — no zombie Kokoro process outlives the
  product.

The Kokoro session (ONNX session + tokenizer) is built lazily
on the first render request and cached across requests in this
child. The parent kills the child on ANY failure (crash,
timeout, render error), so a poisoned session never survives
into the next call — the next call spawns fresh. There is no
in-child reset command: parent-side kill + respawn IS the
reset.
"""

import base64
import json
import sys
import traceback
from pathlib import Path

PROTOCOL_VERSION = 1

SPEED_MIN = 0.0  # exclusive
SPEED_MAX = 4.0  # inclusive


def _fail(reason):
    sys.stdout.write(json.dumps(
        {"ok": False, "protocol": PROTOCOL_VERSION,
         "reason": str(reason)}) + "\n")
    sys.stdout.flush()


def _validate(req):
    """Strict request validation, in the child as well as the
    parent (defense in depth — the child is a trust boundary).
    Returns an error string, or None when the request is well
    formed."""
    if not isinstance(req, dict):
        return "request must be a JSON object"
    if req.get("command") == "ping":
        return None
    text = req.get("text")
    if not isinstance(text, str) or not text.strip():
        return "text must be a non-empty string"
    for field in ("voice", "lang", "model_path", "voices_path"):
        value = req.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"{field} must be a non-empty string"
    speed = req.get("speed")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        return "speed must be a number"
    speed = float(speed)
    if speed != speed or speed <= SPEED_MIN or speed > SPEED_MAX:
        return f"speed must be in ({SPEED_MIN}, {SPEED_MAX}] (got {speed})"
    return None


def main() -> int:
    kokoro = None  # built lazily on the first render request
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:  # noqa: BLE001 — a bad line gets a failure line
            _fail(f"request is not JSON: {e}")
            continue
        err = _validate(req)
        if err is not None:
            _fail(err)
            continue
        if req.get("command") == "ping":
            sys.stdout.write(json.dumps(
                {"ok": True, "protocol": PROTOCOL_VERSION,
                 "command": "ping"}) + "\n")
            sys.stdout.flush()
            continue
        model_path = Path(req["model_path"])
        voices_path = Path(req["voices_path"])
        if not model_path.is_file():
            _fail(f"Kokoro model not on disk: {model_path}")
            continue
        if not voices_path.is_file():
            _fail(f"Kokoro voices bin not on disk: {voices_path}")
            continue
        try:
            # The ONLY kokoro-onnx import in the tree that runs
            # inside this child process — never in the main
            # process. Imported here, inside main(), on the first
            # render request (a ping never pays the import cost).
            if kokoro is None:
                from kokoro_onnx import Kokoro  # noqa: PLC0415

                kokoro = Kokoro(str(model_path), str(voices_path))
            import numpy as np  # noqa: PLC0415 — child-only, with kokoro

            samples, rate = kokoro.create(
                req["text"], voice=req["voice"],
                speed=float(req["speed"]), lang=req["lang"])
            raw = np.asarray(samples, dtype=np.float32).tobytes()
            sys.stdout.write(json.dumps(
                {"ok": True, "protocol": PROTOCOL_VERSION,
                 "rate": int(rate),
                 "samples": int(len(raw) // 4),
                 "pcm_b64": base64.b64encode(raw).decode("ascii")}) + "\n")
            sys.stdout.flush()
        except Exception as e:  # noqa: BLE001 — the reason IS the report
            traceback.print_exc(file=sys.stderr)
            _fail(f"the Kokoro engine failed while rendering: {e}")
    # stdin EOF: the parent died or closed the pipe. Exit — a
    # Kokoro child never outlives the main process.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

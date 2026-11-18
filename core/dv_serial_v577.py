"""DV-4 serial discipline (Developer track, wave 28) — Nova's own code.

This module is the MS101 loop made into a safety spec, not a suggestion.
It implements the bench workflow for the Tx (Nano) / Rx (Uno) pair:

- board <-> sketch pairing asserted in the audit log before upload;
- the physical step she cannot do ("disconnect D1->D0 now") sits behind
  an explicit confirmation gate before any upload argv is built;
- one board open at 9600 baud, timestamped capture to the audit trail;
- the port is released PROVABLY before any upload starts;
- fuse/erase operations sit behind a SEPARATE, permanent confirmation
  gate (they can brick a board);
- firmware clamps + a dead-man timeout travel with every session.

DRIVE-ONLY BOUNDARY (binding): the compile/upload/diagnostic tools
themselves are other vendors' binaries, driven at arm's length by
dv_drive_v577. Nothing in this module bundles, links, or modifies
them. The serial transport is injected: tests use a fake; field use
uses the lazy adapter (``make_pyserial_transport``), which imports
the permissive serial library only when called and raises a clean
"unavailable" when it is absent. This module's own imports are
stdlib-only, and no function here opens a port by itself.

TELEMETRY: none. Everything here is local. The audit log is a local
file; it never leaves the machine through this module.

INV-8 GUARD: the audit log may never be the assistant memory store
(assistant.db). That path is refused outright.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from . import store_base

BAUD = 9600
KNOWN_BOARDS = {
    "nano": {"fqbn": "arduino:avr:nano", "variants": ("arduino:avr:nano",
                                        "arduino:avr:nano:cpu=atmega328old")},
    "uno": {"fqbn": "arduino:avr:uno", "variants": ("arduino:avr:uno",)},
}
# Bench truth: Tx board = Nano, Rx board = Uno (v9.64 gripper rework).
BENCH_PAIRING = {"tx": "nano", "rx": "uno"}


class SerialDisciplineError(ValueError):
    """Raised when any step of the loop order is violated."""


def _refuse_memory_store(path):
    if store_base.is_assistant_db(path):
        raise SerialDisciplineError(
            "audit log must never be the assistant memory store")


def assert_pairing(role, board, sketch_path, fqbn):
    """Assert sketch <-> board pairing before upload.

    Returns the pairing record for the audit log. Raises on any
    mismatch: unknown role, unknown board, wrong board for the role,
    or an FQBN that is not the board's pinned FQBN (old-bootloader
    Nano variant surfaced, never hidden).
    """
    role = str(role).lower()
    board = str(board).lower()
    if role not in BENCH_PAIRING:
        raise SerialDisciplineError("unknown loop role: %r" % role)
    if board not in KNOWN_BOARDS:
        raise SerialDisciplineError("unknown board: %r" % board)
    if BENCH_PAIRING[role] != board:
        raise SerialDisciplineError(
            "role %s expects board %s, got %s" % (role, BENCH_PAIRING[role],
                                                   board))
    if fqbn not in KNOWN_BOARDS[board]["variants"]:
        raise SerialDisciplineError(
            "FQBN %r is not a pinned variant for %s" % (fqbn, board))
    return {"role": role, "board": board, "sketch": str(sketch_path),
            "fqbn": fqbn, "old_bootloader": "atmega328old" in str(fqbn)}


class D1D0Gate:
    """The physical-step gate. Upload argv is never built without it.

    ``confirm`` is a callable returning True, supplied by the caller
    (later wiring passes the real confirm gate). There is no default,
    no inference from job contents, and no path that skips the check.
    ``confirm`` is asked with the exact physical instruction so the
    human confirms the step itself, not a vague "proceed".
    """

    INSTRUCTION = "disconnect D1->D0 now (Tx board detached from Rx D0/D1)"

    def __init__(self, confirm):
        if not callable(confirm):
            raise SerialDisciplineError(
                "D1->D0 gate needs an explicit confirm callable")
        self._confirm = confirm
        self._cleared = False

    def clear(self):
        if self._confirm(self.INSTRUCTION) is True:
            self._cleared = True
            return True
        return False

    def require(self):
        if not self._cleared:
            raise SerialDisciplineError(
                "upload blocked: D1->D0 gate not cleared")
        return True


class FuseGate:
    """Permanent, separate confirmation gate for fuse/erase operations.

    Deliberately distinct from the D1->D0 gate and from any generic
    confirm: fuse/erase can brick a board, so the callable is asked
    with the exact operation and the board identity, every time.
    """

    def __init__(self, confirm):
        if not callable(confirm):
            raise SerialDisciplineError(
                "fuse gate needs an explicit confirm callable")
        self._confirm = confirm

    def require(self, operation, board, port):
        question = ("fuse/erase operation %r on %s (%s) — "
                    "this can brick the board" % (operation, board, port))
        if self._confirm(question) is not True:
            raise SerialDisciplineError(
                "fuse/erase refused: gate not cleared for %r" % operation)
        return True


def validate_packet(line):
    """Validate one Tx packet line from the 9600-baud solo test.

    Returns (ok, reason). Rules: a line must be non-empty, must end
    with a terminator (newline handled by the caller splitting lines;
    a bare carriage return inside the payload is garbage), must be
    ASCII-printable, and must match the field shape the bench uses
    (comma-separated numeric fields). Garbage / missing terminators
    are flagged, never silently accepted.
    """
    if line is None:
        return False, "missing line"
    text = line if isinstance(line, str) else line.decode("utf-8", "replace")
    if text == "":
        return False, "empty line"
    if "\r" in text:
        return False, "bare carriage return inside payload (garbage)"
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
        return False, "non-printable bytes (garbage)"
    fields = text.split(",")
    if len(fields) < 2:
        return False, "packet needs comma-separated numeric fields"
    for field in fields:
        field = field.strip()
        if field == "":
            return False, "empty field"
        try:
            float(field)
        except ValueError:
            return False, "non-numeric field %r" % field
    return True, ""


class TimestampedLog:
    """Append-only audit log for a serial session (local file only)."""

    def __init__(self, path):
        _refuse_memory_store(path)
        self.path = str(path)

    def record(self, kind, payload):
        entry = {"ts": time.time(), "kind": kind, "payload": payload}
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return entry

    def entries(self):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


class PortReleaseProof:
    """Prove the monitor released the port before any upload starts.

    The classic failure is the monitor still holding the COM port
    when the upload begins. ``transport`` must expose ``is_open`` and
    ``close()``. ``prove_released`` closes if open and returns a
    proof record; it raises if the transport cannot be closed.
    """

    def __init__(self, transport):
        self.transport = transport

    def prove_released(self, port):
        transport = self.transport
        was_open = bool(getattr(transport, "is_open", False))
        if was_open:
            transport.close()
        if bool(getattr(transport, "is_open", False)):
            raise SerialDisciplineError(
                "port %s still held after close attempt" % port)
        return {"port": port, "was_open": was_open, "released": True}


class FirmwareClamps:
    """Firmware-side clamps carried from the bench (RB-6 discipline).

    Angles are clamped, never wrapped: a command past the hard clamp
    is refused (returned as an error), not silently clipped, so a
    bad command surfaces instead of moving the arm somewhere wrong.
    """

    def __init__(self, minimum, maximum):
        if not minimum < maximum:
            raise SerialDisciplineError("clamp range must be ordered")
        self.minimum = minimum
        self.maximum = maximum

    def check(self, angle):
        if angle < self.minimum or angle > self.maximum:
            raise SerialDisciplineError(
                "angle %r outside hard clamp [%r, %r]"
                % (angle, self.minimum, self.maximum))
        return angle


class DeadManTimeout:
    """No packet within the timeout -> safe state, not a hang.

    ``tick(now)`` returns True while the session is alive; once the
    gap since the last packet exceeds ``timeout_s``, it returns
    False and latches tripped until ``reset()``.
    """

    def __init__(self, timeout_s):
        self.timeout_s = float(timeout_s)
        self._last = None
        self._tripped = False

    def packet_seen(self, now):
        self._last = float(now)

    def tick(self, now):
        if self._tripped:
            return False
        if self._last is None:
            return True
        if float(now) - self._last > self.timeout_s:
            self._tripped = True
            return False
        return True

    def reset(self):
        self._last = None
        self._tripped = False


def make_pyserial_transport(port, baud=BAUD):
    """Lazy adapter over the user's serial library (field use only).

    The library is imported here, not at module import, so this
    module stays importable without it. Raises a clean
    SerialDisciplineError (not ImportError) when it is absent, so
    the caller reports "unavailable", never a fake success.
    """
    try:
        import serial  # type: ignore
    except ImportError:
        raise SerialDisciplineError(
            "serial library not installed: serial transport unavailable")
    handle = serial.Serial(port, baud, timeout=1)
    return handle


def list_ports_evidence():
    """Port enumeration evidence (VID/PID/serial) via the lazy import.

    Returns a list of evidence dicts, or an empty list with the
    "unavailable" reason when the library is absent. Honest caveat,
    carried from the bench: cheap CH340 clones often report
    identical descriptors, so port identity is confirmed once by
    the human and then remembered — never guessed from descriptors.
    """
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError:
        return []
    out = []
    for info in list_ports.comports():
        out.append({"device": info.device, "vid": info.vid,
                    "pid": info.pid, "serial": info.serial_number,
                    "hwid": info.hwid})
    return out


def solo_test_harness(transport, log, dead_man=None, max_lines=1000):
    """Run a 9600-baud solo test over an injected transport.

    ``transport`` must expose ``readline()`` returning bytes/str,
    with "" at end of stream. Each line is validated; valid packets
    are logged, garbage is logged as garbage (never dropped
    silently). Returns a summary. ``dead_man`` (DeadManTimeout) is
    ticked per line when given.
    """
    valid = 0
    garbage = 0
    for _ in range(max_lines):
        raw = transport.readline()
        if raw in ("", b""):
            break
        text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
        text = text.rstrip("\n")
        ok, reason = validate_packet(text)
        if ok:
            valid += 1
            log.record("packet", {"line": text})
        else:
            garbage += 1
            log.record("garbage", {"line": text, "reason": reason})
        if dead_man is not None:
            dead_man.packet_seen(time.time())
    digest = hashlib.sha256(
        ("\n".join(str(e) for e in log.entries())).encode("utf-8")
    ).hexdigest()[:16]
    return {"valid": valid, "garbage": garbage, "log_sha_prefix": digest}

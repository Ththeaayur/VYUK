"""The "write proof" routine (click-anywhere Batch 0, Plan 9 §7.2).

The shipped build's write-proof routine: the Doctor-page "Write
proof" entry (``doctor.py: check_write_proof``) and the voice
"run the write proof" (the ``write proof`` routine, run via the
``run_routine`` tool) are two triggers for this ONE routine. It
drives VYUK's ``write_file`` (the same handler the funnel calls),
reads the file back, and byte-compares in the detected encoding.

The named fixture (Q-6): 7 lines, mixed line endings + non-ASCII,
the seventh line with NO trailing newline. Lines 2 and 5 end CRLF;
lines 1, 3, 4, 6 end LF. 213 bytes in UTF-8 (28+27+26+49+28+24+31;
the non-ASCII line is 48 bytes of text + LF). The prover pins the
213-byte total; a fixture that drifts fails the pin instead of
silently changing what is proved.

The destination (Q-6): ``<app data>/write_proof.txt``, resolved by
the tree's app-data pattern (``APPDATA`` or ``~/AppData/Roaming``).
Retention (R-8, ONE rule): the file REMAINS afterwards (named in
the receipt) so the parent can inspect it.

Recording: the ledger ``write_proof`` receipt (tool receipts) + the
M-58 confirm popup (the Doctor page asks him to confirm the result
in the dialog — see gui/doctor_page.py _on_done). The pass
criterion is byte-identical on first attempt AND after an app
restart (the restart leg is the field gate, on his machine — the
lab runs the first leg only and records the restart leg as OWED).

Licence: own code — nothing adopted, no upstream, no third-party
licence text (Parent-ruled (licence, 2026-11-14)).
"""

from __future__ import annotations

import os
from pathlib import Path

# The named fixture (Plan 9 §7.2, Q-6). Built from explicit parts
# so the line endings are exact — never re-typed as one string.
_FIXTURE_PARTS = (
    ("Line one: plain ASCII text.", "\n"),
    ("Line two: ends with CRLF.", "\r\n"),
    ("Line three: ends with LF.", "\n"),
    ("Line four: café, naïve, façade — non-ASCII.", "\n"),
    ("Line five: ends with CRLF.", "\r\n"),
    ("Line six: ends with LF.", "\n"),
    ("Line seven: no trailing newline", ""),
)

WRITE_PROOF_FIXTURE_BYTES = b"".join(
    (text + ending).encode("utf-8") for text, ending in _FIXTURE_PARTS)
WRITE_PROOF_FIXTURE_TEXT = WRITE_PROOF_FIXTURE_BYTES.decode("utf-8")
# Pinned by the prover: 28+27+26+49+28+24+31 = 213.
WRITE_PROOF_FIXTURE_SIZE = len(WRITE_PROOF_FIXTURE_BYTES)
WRITE_PROOF_FILENAME = "write_proof.txt"
WRITE_PROOF_ROUTINE_NAME = "write proof"


def app_data_dir() -> Path:
    """The app-data dir, by the tree's own pattern
    (core/skills_media_capture.py:34, core/skills_media_files.py:50)."""
    return Path(os.environ.get("APPDATA")
                or str(Path.home() / "AppData" / "Roaming"))


def write_proof_path(app_data: str | Path | None = None) -> Path:
    """``<app data>/write_proof.txt`` (Q-6 — NAMED)."""
    base = Path(app_data) if app_data is not None else app_data_dir()
    return base / WRITE_PROOF_FILENAME


def write_proof_routine_actions(path: str | Path | None = None) -> list:
    """The voice routine's actions: one ``write_file`` call with
    the named fixture to the named path. The write's own verify
    is byte-exact (Batch 0), so the routine step fails — never
    silently passes — when the bytes on disk differ."""
    target = str(write_proof_path(path))
    return [{"tool": "write_file",
             "args": {"filepath": target,
                      "content": WRITE_PROOF_FIXTURE_TEXT}}]


def ensure_write_proof_routine() -> str:
    """Save the ``write proof`` voice routine when it does not
    exist yet (so "run the write proof" resolves). Best-effort
    and idempotent: an existing routine is never overwritten,
    and a save failure never fails the proof — it is reported
    in the verdict's note instead."""
    try:
        from .brain import planner_routines as _routines
    except Exception:  # noqa: BLE001
        try:
            from core.brain import planner_routines as _routines
        except Exception as exc:  # noqa: BLE001 — no routine home
            return "routine not saved: %s" % exc
    try:
        names = [str(r.get("name", "")).strip().lower()
                 for r in _routines.get_routines()]
        if WRITE_PROOF_ROUTINE_NAME in names:
            return "routine already saved"
        saved = _routines.save_routine(
            WRITE_PROOF_ROUTINE_NAME,
            actions=write_proof_routine_actions())
        if str(saved).startswith("Routine 'write proof'"):
            return "routine saved"
        return "routine not saved: %s" % saved
    except Exception as exc:  # noqa: BLE001 — never break the proof
        return "routine not saved: %s" % exc


def run_write_proof(*, app_data: str | Path | None = None,
                    record_receipt: bool = True,
                    save_routine: bool = True) -> dict:
    """Run the write proof. Drives VYUK's ``write_file`` with the
    named fixture, reads the file back, and byte-compares in the
    detected encoding. Returns a verdict dict:

      passed (bool), path (str), bytes_expected (213),
      bytes_on_disk (int | None), encoding (str),
      receipt_id (str | None), routine (str), detail (str).

    The file REMAINS afterwards (R-8), named in the receipt.
    The receipt is the tool-receipts ``write_proof`` triplet
    (attempt → dispatched → outcome). The routine-ensure step
    saves the voice routine when absent. Never raises: a
    failure to drive, read, or record is a failed verdict
    with the reason named, not an exception."""
    from .funnel.tools_handlers_data_services import (
        write_file_bytes as _write_file_bytes)
    try:
        from . import file_bytes as _fb
    except Exception:  # noqa: BLE001
        from core import file_bytes as _fb
    path = write_proof_path(app_data)
    verdict = {"passed": False, "path": str(path),
               "bytes_expected": WRITE_PROOF_FIXTURE_SIZE,
               "bytes_on_disk": None, "encoding": "utf-8",
               "receipt_id": None, "routine": "",
               "detail": ""}
    receipt = None
    if record_receipt:
        try:
            from . import tool_receipts as _receipts
        except Exception:  # noqa: BLE001
            try:
                from core import tool_receipts as _receipts
            except Exception:  # noqa: BLE001 — receipts unavailable
                _receipts = None
        if _receipts is not None:
            try:
                receipt = _receipts.try_record_attempt(
                    tool="write_proof",
                    args={"filepath": str(path),
                          "bytes": WRITE_PROOF_FIXTURE_SIZE,
                          "encoding": "utf-8"})
                if receipt is not None:
                    verdict["receipt_id"] = str(
                        receipt.get("receipt_id", "") or "")
                    _receipts.try_mark_dispatched(receipt)
            except Exception:  # noqa: BLE001 — receipts never break it
                receipt = None
    try:
        # GAPS-23 (F-V7): the proof writes through the same core
        # function the funnel's write_file handler delegates to —
        # never a bare-mixin handler call outside the funnel.
        outcome = _write_file_bytes(str(path), WRITE_PROOF_FIXTURE_TEXT)
        verified = bool(getattr(outcome, "verified", False))
        detail = str(getattr(outcome, "message", outcome))
    except Exception as exc:  # noqa: BLE001 — a failed drive is a fail
        verified = False
        detail = "write_file failed: %s" % exc
    try:
        on_disk = path.read_bytes()
        verdict["bytes_on_disk"] = len(on_disk)
        try:
            verdict["encoding"] = _fb.detect_encoding(on_disk)
        except Exception:  # noqa: BLE001 — encoding unreadable
            verdict["encoding"] = "unknown"
        byte_exact = on_disk == WRITE_PROOF_FIXTURE_BYTES
    except Exception as exc:  # noqa: BLE001 — unreadable is a fail
        byte_exact = False
        if not detail:
            detail = "could not read the file back: %s" % exc
    verdict["passed"] = bool(verified and byte_exact)
    if verdict["passed"]:
        verdict["detail"] = (
            "Write proof PASSED: %d bytes byte-identical at %s "
            "(encoding %s; the file remains for inspection)."
            % (WRITE_PROOF_FIXTURE_SIZE, path, verdict["encoding"]))
    else:
        verdict["detail"] = (
            "Write proof FAILED: %s — expected %d bytes %s, "
            "found %s bytes on disk at %s."
            % (detail, WRITE_PROOF_FIXTURE_SIZE,
               "byte-identical" if byte_exact else "NOT byte-identical",
               verdict["bytes_on_disk"], path))
    if save_routine:
        verdict["routine"] = ensure_write_proof_routine()
    if record_receipt and receipt is not None:
        try:
            _receipts.try_record_outcome(
                receipt,
                status="succeeded" if verdict["passed"] else "failed",
                summary=verdict["detail"])
        except Exception:  # noqa: BLE001 — receipts never break it
            pass
    return verdict

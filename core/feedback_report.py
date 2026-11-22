"""Feedback and crash reports that carry no content (loophole batch 7,
#6).

The fork in the audit: a report that carries the answer, the chat
history or the memory leaks private facts off the machine; a report
that carries nothing is useless. The close is a fixed report shape:

* an answer-feedback report carries ONLY the feedback label plus the
  SHA-256 hash of the turn — never the words;
* a crash report carries ONLY the crash signature (the exception
  type + the app code location) — never the exception message, which
  can itself carry user text (a file path, a pasted value).

Everything in this module is Qt-free and pure except the crash
hook itself. The opt-in (ConfigSpec ``feedback_reporting_enabled``,
default False) is read live at crash time: with the opt-in off, the
hook records nothing at all. The opt-in lives in Settings, separate
from the first-run sign-in — no first-run or sign-in module
references it (pinned by a suite scan).

The send shape is the PF-10 frozen bundle (gui/window_trust.py):
build_report_bundle() takes a validated feedback/crash report and
merges it under "feedback"/"crash" after report_carries_no_content;
the dialog previews exactly what ships and exports only with
consent. There is no endpoint and no upload — the export is a file
the user can attach, same as the problem bundle.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

#: The labels an answer-feedback report may carry. Small on
#: purpose: a label is a vote, not a transcript.
FEEDBACK_LABELS = ("helpful", "not_helpful", "wrong")

#: The ONLY keys a feedback/crash report may carry. No "content",
#: no "transcript", no "answer", no "history", no "memory" — a
#: report with any other key fails report_carries_no_content.
REPORT_KEYS = frozenset({
    "kind", "label", "signature", "turn_hash", "app", "version",
    "generated_at",
})

#: Crash signatures are kept locally, newest last, oldest-first
#: rotation (the batch-6 archive shape, at a smaller scale).
CRASH_SIGNATURES_LIMIT = 50

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_EXC_TYPE = re.compile(r"^[A-Za-z_][\w.]*$")
_LOCATION = re.compile(r"^[\w.]+:[\w.]+:\d+$")
_SIGNATURE = re.compile(
    r"^[A-Za-z_][\w.]* at [\w.]+:[\w.]+:\d+$")


def _version() -> str:
    """The app version for a report, or "unknown". Never raises."""
    try:
        from .version import VERSION

        return str(VERSION)
    except Exception:  # noqa: BLE001 — unknown is an honest version
        return "unknown"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def turn_hash(turn_text: str) -> str:
    """The SHA-256 of one turn's canonical text (stripped). A hash
    is one-way: it lets the team count identical turns, never read
    one. Empty text hashes to the hash of "" — still a hash, never
    content."""
    return hashlib.sha256(
        str(turn_text or "").strip().encode("utf-8")).hexdigest()


def build_feedback_report(label: str, turn_text: str,
                          *, version: str | None = None) -> dict:
    """One answer-feedback report: the label + the turn hash.
    Raises ValueError on an unknown label — a report with a
    free-text label could smuggle content in the label itself."""
    if label not in FEEDBACK_LABELS:
        raise ValueError(f"unknown feedback label: {label!r}")
    return {
        "kind": "feedback",
        "label": label,
        "turn_hash": turn_hash(turn_text),
        "app": "VYUK",
        "version": version if version is not None else _version(),
        "generated_at": _now_iso(),
    }


def build_crash_report(exc_type: str, location: str,
                       *, version: str | None = None) -> dict:
    """One crash report: the signature (type + app code location).
    The exception message is NEVER an input here — it cannot leak
    because there is nowhere to put it. Raises ValueError on a
    malformed type or location."""
    exc_type = str(exc_type or "").strip()
    location = str(location or "").strip()
    if not _EXC_TYPE.match(exc_type):
        raise ValueError(f"malformed exception type: {exc_type!r}")
    if not _LOCATION.match(location):
        raise ValueError(f"malformed crash location: {location!r}")
    return {
        "kind": "crash",
        "signature": f"{exc_type} at {location}",
        "app": "VYUK",
        "version": version if version is not None else _version(),
        "generated_at": _now_iso(),
    }


def report_carries_no_content(report: Any) -> bool:
    """The content-freedom validator: True only for a report whose
    keys are all in REPORT_KEYS, whose label (feedback) is in the
    allow-list, whose turn hash is a 64-hex digest, whose crash
    signature matches the type-at-location shape, and whose values
    are all single-line (content cannot hide in a multi-line
    value). Never raises; a non-dict is not a report."""
    try:
        if not isinstance(report, dict):
            return False
        if not set(report) <= REPORT_KEYS:
            return False
        kind = report.get("kind")
        if kind == "feedback":
            if report.get("label") not in FEEDBACK_LABELS:
                return False
            if not _HEX64.match(str(report.get("turn_hash") or "")):
                return False
        elif kind == "crash":
            if not _SIGNATURE.match(str(report.get("signature") or "")):
                return False
        else:
            return False
        for value in report.values():
            if isinstance(value, str) and "\n" in value:
                return False
        return True
    except Exception:  # noqa: BLE001 — an uncheckable report fails
        return False


def _default_path() -> Path:
    """The local crash-signature file (data/crash_signatures.jsonl).
    Resolved lazily so importing this module never touches data/."""
    from .config import DATA_DIR

    return DATA_DIR / "crash_signatures.jsonl"


def record_crash_signature(exc_type: str, location: str, *,
                           path: str | Path | None = None,
                           limit: int = CRASH_SIGNATURES_LIMIT) -> bool:
    """Append one crash signature to the local file, keeping the
    last ``limit`` lines (oldest-first rotation). The file is
    LOCAL ONLY — nothing here sends anything anywhere. Returns
    True on success, False on any failure (including a malformed
    signature — a bad signature is dropped, never stored raw).
    Never raises."""
    try:
        record = build_crash_report(exc_type, location)
        target = Path(path) if path is not None else _default_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if target.exists():
            try:
                lines = target.read_text(
                    encoding="utf-8").splitlines()
            except Exception:  # noqa: BLE001 — a broken file restarts
                lines = []
        lines.append(json.dumps(record, sort_keys=True))
        lines = lines[-int(limit):] if limit and limit > 0 else lines
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 — recording must never break
        return False


def crash_signatures(*, path: str | Path | None = None,
                     limit: int = CRASH_SIGNATURES_LIMIT) -> list[dict]:
    """The recorded crash signatures, newest last — validated ones
    only (a hand-edited line that fails the validator is skipped,
    never trusted). Never raises; [] on any failure."""
    try:
        target = Path(path) if path is not None else _default_path()
        if not target.exists():
            return []
        out = []
        for line in target.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except Exception:  # noqa: BLE001 — a bad line is skipped
                continue
            if report_carries_no_content(record):
                out.append(record)
        return out[-int(limit):] if limit and limit > 0 else out
    except Exception:  # noqa: BLE001 — a broken read is empty
        return []


def count_crash_signatures(*, path: str | Path | None = None) -> int:
    """How many crash signatures sit locally (the number the
    Settings feedback card's status line shows). Read-only —
    it never writes the file. Never raises; 0 on any failure."""
    try:
        return len(crash_signatures(path=path))
    except Exception:  # noqa: BLE001 — an unreadable store is empty
        return 0


def _app_frame_location(tb: Any) -> str:
    """The first traceback frame inside the install tree, as
    "module:func:line" (the module dotted from the tree root) — or
    "" when no app frame exists (a crash raised entirely inside a
    library names the type only). The exception message is never
    read here. Never raises."""
    try:
        from .config import PROJECT_ROOT

        root = str(PROJECT_ROOT)
    except Exception:  # noqa: BLE001 — no root, no location
        return ""
    frame = tb
    while frame is not None:
        try:
            filename = str(frame.tb_frame.f_code.co_filename or "")
            if filename.startswith(root):
                rel = filename[len(root):].lstrip("/\\")
                if rel.endswith(".py"):
                    rel = rel[:-3]
                module = rel.replace("/", ".").replace("\\", ".")
                if module.endswith(".__init__"):
                    module = module[: -len(".__init__")]
                return (f"{module}:{frame.tb_frame.f_code.co_name}:"
                        f"{frame.tb_lineno}")
        except Exception:  # noqa: BLE001 — a bad frame is skipped
            pass
        frame = frame.tb_next
    return ""


def _crash_hook(exc_type: Any, exc_value: Any, tb: Any,
                *, path: str | Path | None = None) -> None:
    """The excepthook body: record the signature ONLY when the
    opt-in is on (read live at crash time — flipping it off
    mid-session stops recording at once). Never raises, never
    records the message, never records when the opt-in is off."""
    try:
        from .config import config

        if not bool(getattr(config, "feedback_reporting_enabled",
                            False)):
            return
        name = getattr(exc_type, "__name__", None) or str(exc_type)
        location = _app_frame_location(tb)
        if not location:
            # No app frame (a crash raised entirely inside a
            # library, or a harness raising outside the tree):
            # the signature still records, with the location
            # honestly marked unknown — dropping it would hide
            # an opted-in crash entirely, and "unknown" is the
            # truth here, not a guess at a location.
            location = "unknown:unknown:0"
        record_crash_signature(name, location, path=path)
    except Exception:  # noqa: BLE001 — the hook must never break
        pass


def install_crash_hook(*, path: str | Path | None = None):
    """Install the crash-signature excepthook (main.py calls this
    at boot). Returns the previous hook so a test can restore it.
    The hook itself is gated on the opt-in — installing it records
    nothing until the user turns reporting on in Settings."""
    previous = sys.excepthook

    def _hook(exc_type, exc_value, tb):
        _crash_hook(exc_type, exc_value, tb, path=path)

    sys.excepthook = _hook
    return previous

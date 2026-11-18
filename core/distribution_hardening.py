"""Wave 3 distribution hardening (3b) — CODE and SUPPORT only.
Stdlib only, no network, no hosting, no signing, no paid CI.

What this module is:
  * Installer-validation SUPPORT: the Windows compile checklist,
    the clean-machine install checklist, and the uninstall
    checklist (R4-G), as data the owner or free-minutes CI works
    through. It does NOT compile a Windows installer here, and
    it does not claim one compiles.
  * Minimal-update-feed CODE without hosting (D-7): a feed
    manifest can be parsed and structurally validated from a
    local file, and a release entry can be checked for the fields
    a real feed needs. No feed is hosted, fetched, or served here;
    the bucket/host decision is deferred and stays deferred.
  * Crash-report path in EXPLICIT-SEND-ONLY shape: a crash dump
    is written locally, listed locally, and sent ONLY when the
    caller passes explicit_send=True AND injects a transport.
    With no transport, or without the explicit flag, sending
    refuses. Nothing here ever uploads silently.
  * Matrix completion support: a matrix row can be recorded only
    with its source (lab vs owner run) stated. A lab row never
    masquerades as an owner row, and a row with no source is
    refused.

GATED, named here and never asserted (R4-G, §3.3):
  * Sandbox / Hyper-V availability — UNKNOWN, because the owner's
    Windows edition is unknown.
  * The Zone.Identifier claim — TO-BE-CHECKED on his machine.
    Review 4's memory of it is not a verified fact.
Both appear in every R4-G checklist this module produces, marked
GATED, so a checklist can never be read as clearing them.

Deferred, never built here: EV/OV signing (builds stay unsigned
in fact), feed bucket/host, paid CI beyond free minutes, HSM,
and any Flow use (Hosted-Flow terms interaction still owed).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid

GATED_R4G = {
    "sandbox_hyperv_availability": (
        "GATED: UNKNOWN — the owner's Windows edition is unknown, "
        "so Sandbox / Hyper-V availability cannot be asserted"
    ),
    "zone_identifier_claim": (
        "GATED: TO-BE-CHECKED on his machine — Review 4's memory "
        "of the Zone.Identifier claim is not a verified fact"
    ),
}

INSTALLER_CHECKLIST = [
    "Compile the installer on Windows (owner's machine or free-minutes CI only)",
    "Install over the same existing folder, without wiping .env or models",
    "Confirm version and About line on the installed build",
    "Confirm no integrity warning on first launch",
    "Confirm the previous version's data still loads",
]

CLEAN_MACHINE_CHECKLIST = [
    "First run on a clean machine shows first-run guidance, not a crash",
    "No step assumes a file from a previous install",
    "Uninstall removes the installed files and leaves user data handling explicit",
]

FEED_REQUIRED_FIELDS = ("version", "url", "sha256", "notes")


def installer_validation_checklist() -> dict:
    """R4-G installer + clean-machine + uninstall checklists, with
    the two gated unknowns attached, never cleared."""
    return {
        "installer": list(INSTALLER_CHECKLIST),
        "clean_machine": list(CLEAN_MACHINE_CHECKLIST),
        "uninstall": [
            "Uninstall script removes installed files",
            "Uninstall states what happens to user data, explicitly",
            "Uninstall does not silently delete .env or models",
        ],
        "gated": dict(GATED_R4G),
        "signing": "DEFERRED: builds stay unsigned in fact until EV/OV signing is bought",
        "feed_hosting": "DEFERRED: no feed bucket/host; feed code here is local-only",
    }


def validate_feed_manifest(manifest: dict) -> dict:
    """Structural validation of a local update-feed manifest.

    Checks required fields and sha256 shape only. This is NOT a
    trust verdict on a remote feed: it proves nothing about a
    host, a signature, or a download, and it fetches nothing."""
    if not isinstance(manifest, dict):
        return {"ok": False, "reason": "manifest is not an object"}
    releases = manifest.get("releases")
    if not isinstance(releases, list):
        return {"ok": False, "reason": "manifest has no releases list"}
    problems = []
    for entry in releases:
        if not isinstance(entry, dict):
            problems.append("release entry is not an object")
            continue
        for field in FEED_REQUIRED_FIELDS:
            if not entry.get(field):
                problems.append(f"release missing field '{field}'")
        digest = str(entry.get("sha256", ""))
        if digest and (len(digest) != 64 or
                       any(c not in "0123456789abcdef" for c in digest.lower())):
            problems.append("release sha256 is not a 64-hex digest")
    return {"ok": not problems, "problems": problems,
            "releases": len(releases),
            "note": "structural only; no host, signature, or download is verified here"}


def feed_entry_for(version: str, url: str, file_bytes: bytes,
                   notes: str = "") -> dict:
    """Build one feed entry for a local file, with its real sha256.
    Building the entry does not publish, host, or serve it."""
    return {"version": version, "url": url,
            "sha256": hashlib.sha256(file_bytes).hexdigest(),
            "notes": notes}


class CrashReports:
    """Explicit-send-only crash reports, stored locally.

    Dumps live in the given directory as JSON files. ``send`` does
    nothing unless the caller passes explicit_send=True AND an
    injected ``transport`` callable; with no transport it reports
    'no-transport' and sends nothing. A dump is never uploaded by
    construction, by a timer, or by a crash handler here.

    Gaps batch (C-01/C-02) — two hardenings, both in this class:
    * Collision-proof filenames: the dump name carries a random
      suffix and the file is created with O_EXCL, so two crashes
      in the same second NEVER overwrite each other — the second
      gets its own file (the old second-resolution name + open
      "w" overwrote silently).
    * Allowlisted + recursively sanitized payloads: a dump
      carries exactly the keys error/context/at, every nesting
      level is scrubbed (sensitive key names at every level,
      secret-shaped text inside strings, including the error
      string itself), and ``preview_dump`` returns exactly the
      payload ``send`` transmits — the user sees the sanitized
      report before anything is sent. Over-redaction is
      deliberate: a crash report that drops a benign long token
      is safe; one that ships a secret is not.
    """

    def __init__(self, dump_dir: str):
        self.dump_dir = str(dump_dir)
        os.makedirs(self.dump_dir, exist_ok=True)

    def write_dump(self, *, error: str, context: dict | None = None) -> str:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        payload = sanitize_report(str(error), context, stamp=stamp)
        path, handle = _unique_dump_file(self.dump_dir, stamp)
        with handle:
            json.dump(payload, handle)
        return path

    def list_dumps(self) -> list:
        return sorted(
            os.path.join(self.dump_dir, name)
            for name in os.listdir(self.dump_dir)
            if name.startswith("crash-") and name.endswith(".json"))

    def preview_dump(self, path: str) -> dict:
        """The EXACT payload ``send`` would transmit for this dump.

        The stored file is re-sanitized on read, so a dump written
        before the gaps batch (possibly carrying secrets) is
        sanitized at preview/send time too, and preview == sent
        by construction — both go through this function.
        """
        if not os.path.isfile(path):
            raise FileNotFoundError(f"dump file not found: {path}")
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("dump file is not a JSON object")
        return sanitize_report(
            payload.get("error", ""), payload.get("context"),
            stamp=payload.get("at", ""))

    def send(self, path: str, *, explicit_send: bool,
             transport=None) -> dict:
        if not explicit_send:
            return {"sent": False,
                    "reason": "send requires explicit_send=True"}
        if transport is None:
            return {"sent": False, "reason": "no-transport: dump stays local"}
        if not os.path.isfile(path):
            return {"sent": False, "reason": "dump file not found"}
        try:
            payload = self.preview_dump(path)
        except Exception as e:  # noqa: BLE001 — never send unsanitized
            return {"sent": False,
                    "reason": f"dump could not be previewed: {e}"}
        transport(payload)
        return {"sent": True, "path": path}


# --- Gaps batch (C-01/C-02): crash-dump filename + sanitization ---

_SENSITIVE_KEY_WORDS = (
    "key", "token", "secret", "password", "passwd", "auth",
    "credential", "bearer", "private",
)

# Secret-shaped text inside otherwise ordinary strings. Each
# pattern replaces only the secret part, keeping the surrounding
# words so the report stays readable.
_CREDENTIAL_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret"
    r"|password|passwd|bearer)\b\s*[:=]\s*(\"[^\"]*\"|'[^']*'|\S+)")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.~+/=]+")
_URL_CREDENTIAL_RE = re.compile(r"(?i)(://[^:/@\s]+:)[^@\s]+@")
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{32,}")


def _scrub_text(text: str) -> str:
    """Remove secret-shaped values from one string, keeping prose."""
    text = _CREDENTIAL_ASSIGN_RE.sub(r"\1: [redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1[redacted]@", text)
    text = _LONG_TOKEN_RE.sub("[redacted]", text)
    return text


def _sanitize_value(value):
    """Recursively sanitize one value: sensitive key names at
    EVERY nesting level become [redacted]; strings are scrubbed
    of secret-shaped text; numbers/bools/None pass through."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(word in lowered for word in _SENSITIVE_KEY_WORDS):
                out[key] = "[redacted]"
            else:
                out[key] = _sanitize_value(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def sanitize_report(error: str, context: dict | None, *,
                    stamp: str = "") -> dict:
    """The allowlisted crash-report payload: exactly the keys
    error/context/at, fully sanitized. Anything else a caller
    tries to smuggle in is dropped, not carried."""
    return {
        "error": _scrub_text(str(error)),
        "context": _sanitize_value(context or {}),
        "at": str(stamp),
    }


def _unique_dump_file(dump_dir: str, stamp: str):
    """Create a new dump file with O_EXCL (C-01): the name carries
    a random suffix, and exclusive-create guarantees a second
    dump in the same second never overwrites the first — on a
    (near-impossible) suffix collision it retries with a fresh
    suffix, then falls back to a counter. Returns (path, handle).
    """
    for _ in range(100):
        path = os.path.join(
            dump_dir, f"crash-{stamp}-{uuid.uuid4().hex[:8]}.json")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            return path, os.fdopen(fd, "w", encoding="utf-8")
        except FileExistsError:
            continue
    counter = 0
    while True:
        counter += 1
        path = os.path.join(
            dump_dir, f"crash-{stamp}-c{counter}.json")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            return path, os.fdopen(fd, "w", encoding="utf-8")
        except FileExistsError:
            continue


class MatrixBook:
    """Matrix completion support. Every row carries its source.

    ``source`` must be 'owner' (a run on his machine, his verdict)
    or 'lab' (a run in this environment). Lab rows are stored
    marked lab and can never be re-labelled owner; a row with no
    source is refused outright."""

    def __init__(self, book_path: str):
        self.book_path = str(book_path)
        directory = os.path.dirname(os.path.abspath(book_path))
        if directory:
            os.makedirs(directory, exist_ok=True)

    def _load(self) -> list:
        try:
            with open(self.book_path, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def record(self, row_id: str, result: str, *, source: str,
               note: str = "") -> dict:
        if source not in ("owner", "lab"):
            return {"recorded": False,
                    "reason": "source must be 'owner' or 'lab'"}
        rows = self._load()
        entry = {"row": row_id, "result": result, "source": source,
                 "note": note, "at": time.time()}
        rows.append(entry)
        with open(self.book_path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle)
        return {"recorded": True, "entry": entry}

    def owner_rows(self) -> list:
        return [row for row in self._load() if row["source"] == "owner"]

    def summary(self) -> dict:
        rows = self._load()
        return {"total": len(rows),
                "owner": sum(1 for r in rows if r["source"] == "owner"),
                "lab": sum(1 for r in rows if r["source"] == "lab"),
                "note": "only owner rows count toward a wave's field exit"}

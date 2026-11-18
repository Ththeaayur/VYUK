"""Hash-chained record store (v5.56, Wave 2, SE-6.10 + PF-15).

ONE record family: audit events, human activity, egress-ledger
entries, kill-chain transitions and rotation checkpoints all live
in a single JSONL store under the app's ``data/`` dir, local only
— nothing here ever egresses. The safety-side audit chain and the
human-form activity feed are the same record read two ways (see
core/record_family.py); this module is only the chain mechanics.

CHAIN (SE-6.10)
---------------
Every line is one JSON object::

    {seq, id, ts, kind, data, prev_hash, hash}

``hash`` is SHA-256 over the canonical JSON (sorted keys,
compact separators) of ``(seq, id, ts, kind, data, prev_hash)``.
``prev_hash`` of the first entry is the GENESIS value below —
documented, constant, and NOT a secret: the chain's first link
has no predecessor, so it chains to a fixed, published genesis
constant instead of pretending otherwise. ``verify_chain()``
recomputes every hash and every link and reports the FIRST
break (index / id / reason); a tampered, deleted-middle or
reordered line is therefore detectable, not silently absorbed.

ROTATION (PF-15) — rotation NEVER breaks the chain:
--------------------------------------------------
When the store crosses its size bound (default 2 MiB, matching
core/audit.py's cap) or its age bound (default 90 days), rotate()
moves the current file to ``data/records_archive/`` and starts a
fresh file whose FIRST entry is a checkpoint entry
(kind ``"checkpoint"``) whose ``prev_hash`` is the archived
segment's LAST hash and whose data carries the archived segment's
file name, entry count, last hash and whole-file SHA-256. The new
chain therefore continues the old one by hash, and verify_chain()
verifies every archived segment still present, in order, before
the live file. Deleting an archived segment does not silently
pass: it is reported in the detail as ``archive_missing`` (a
warning, not a hash break — the live chain's hashes still verify;
the missing segment is simply no longer independently checkable).
User-held exports are the anchors (see export_chain()): there is
no server, no public log and no remote anchor, by design —
a vendor remote anchor would be a vendor remote-kill-adjacent
channel, and the plan's designed-in refusal forbids it.

FAILURE DISCIPLINE: a failing write never raises to the caller
(append returns "" on failure, mirroring audit.write_record's
never-break-the-call rule); verification and rotation are the
only operations that report problems, and they do so as data.

Configuration seams (suites / coordinator wiring): configure()
redirects the store dir and/or bounds; reset_configuration()
restores defaults. Importing this module creates no files.

Stdlib only. No new dependency.
"""

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from . import store_base

log_name = "nova.audit_chain"

# The genesis constant: the prev_hash of the very first entry.
# Published here on purpose — it is a fixed starting point, not
# a secret, and any verifier (including a user's own script)
# needs it to check the first link.
GENESIS_PREV_HASH = "0" * 64

DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB, same cap as core/audit.py
DEFAULT_MAX_AGE_DAYS = 90
ARCHIVE_DIRNAME = "records_archive"
STORE_FILENAME = "records.jsonl"

_lock = threading.Lock()
_dir_override: Path | None = None
_max_bytes_override: int | None = None
_max_age_days_override: float | None = None


def _default_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


def store_dir() -> Path:
    """Where the record store lives right now."""
    return _dir_override if _dir_override is not None else _default_dir()


def store_path() -> Path:
    return store_dir() / STORE_FILENAME


def archive_dir() -> Path:
    return store_dir() / ARCHIVE_DIRNAME


def configure(data_dir=None, max_bytes=None, max_age_days=None) -> None:
    """Redirect the store and/or resize the rotation bounds.

    Any argument left as None keeps its current setting.
    """
    global _dir_override, _max_bytes_override, _max_age_days_override
    with _lock:
        if data_dir is not None:
            _dir_override = Path(data_dir)
        if max_bytes is not None:
            _max_bytes_override = int(max_bytes)
        if max_age_days is not None:
            _max_age_days_override = float(max_age_days)


def reset_configuration() -> None:
    """Restore the default store dir and rotation bounds."""
    global _dir_override, _max_bytes_override, _max_age_days_override
    with _lock:
        _dir_override = None
        _max_bytes_override = None
        _max_age_days_override = None


def _max_bytes() -> int:
    return _max_bytes_override if _max_bytes_override is not None \
        else DEFAULT_MAX_BYTES


def _max_age_days() -> float:
    return _max_age_days_override if _max_age_days_override is not None \
        else DEFAULT_MAX_AGE_DAYS


def _canonical(entry: dict) -> str:
    # The canonical JSON encoding is owned by the shared store
    # base (GAPS-14) — this is its function, not a local copy.
    return store_base.canonical_json(entry)


def _entry_hash(seq: int, event_id: str, ts: str, kind: str,
                data: dict, prev_hash: str) -> str:
    # The canonical chain hash is owned by the shared store
    # base (GAPS-14) — this is its function, not a local copy.
    # The computation is identical (SHA-256 over canonical JSON
    # of (seq, id, ts, kind, data, prev_hash)), so every stored
    # hash still verifies.
    return store_base.canonical_hash(seq, event_id, ts, kind, data,
                                     prev_hash)


def _read_lines(path: Path) -> list[dict]:
    """Parse a store file, skipping a torn last line (crash
    mid-append) the way core/audit.py's reader does."""
    entries: list[dict] = []
    if not path.exists():
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # torn / partial line: skipped, never fatal
            if isinstance(obj, dict):
                entries.append(obj)
    return entries


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def append(kind: str, data: dict) -> str:
    """Append one event to the chain. Returns its id, or "" on
    failure (a failing write never raises to the caller)."""
    if not isinstance(data, dict):
        return ""
    # v5.56 (Wave 2, PF-15): rotation is AUTOMATIC — when the live
    # store already crosses a bound, it rotates BEFORE this entry
    # is written, so no manual cleanup is ever needed AND the
    # append→read round-trip always holds (the entry just
    # appended is always live-readable). Check-after-append was
    # rejected: it archives the entry that crossed the bound, so
    # a read immediately after an append can return empty. The
    # cost is bounded: the live file exceeds its bound by at
    # most one entry until the next append rotates it. _rotate
    # re-checks under its own lock, so a redundant rotate here
    # is a no-op, and this never raises to the caller.
    try:
        if rotation_needed():
            _rotate(force=False)
    except Exception:  # noqa: BLE001 — rotation never breaks a call
        pass
    try:
        path = store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            entries = _read_lines(path)
            seq = (entries[-1]["seq"] + 1) if entries else 0
            prev = entries[-1]["hash"] if entries else GENESIS_PREV_HASH
            event_id = uuid.uuid4().hex
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            h = _entry_hash(seq, event_id, ts, kind, data, prev)
            record = {"seq": seq, "id": event_id, "ts": ts, "kind": kind,
                      "data": data, "prev_hash": prev, "hash": h}
            with open(path, "a", encoding="utf-8") as f:
                f.write(_canonical(record) + "\n")
                f.flush()
                os.fsync(f.fileno())
        return event_id
    except Exception:  # noqa: BLE001 — record-keeping never breaks a call
        return ""


def read(kind: str | None = None, limit: int = 50) -> list[dict]:
    """Read events newest-first, optionally filtered by kind.

    ``limit`` <= 0 means no limit. Returned dicts are the stored
    records verbatim (id, ts, kind, data, hashes).
    """
    entries = _read_lines(store_path())
    if kind is not None:
        entries = [e for e in entries if e.get("kind") == kind]
    entries.reverse()
    if limit and limit > 0:
        entries = entries[:limit]
    return entries


def verify_chain_detail() -> dict:
    """Verify the live chain plus every archived segment still
    present, in order. Returns a dict::

        {ok, checked, first_break, warnings}

    ``first_break`` is None when intact, else
    {segment, index, id, reason} naming the FIRST bad link:
    a recomputed hash that does not match, a prev_hash that
    does not equal the previous entry's hash, a genesis entry
    whose prev_hash is not GENESIS_PREV_HASH, or a checkpoint
    whose recorded archived hash does not match the segment it
    names. ``warnings`` lists non-breaking notes (currently only
    ``archive_missing`` entries for checkpoint-named segments
    that are no longer on disk).
    """
    warnings: list[dict] = []
    checked = 0
    segments: list[tuple[str, list[dict]]] = []
    live = _read_lines(store_path())
    # Archived segments, oldest first (filename timestamp order).
    arch = archive_dir()
    archived: list[tuple[str, list[dict]]] = []
    if arch.is_dir():
        for p in sorted(arch.glob("*.jsonl")):
            archived.append((p.name, _read_lines(p)))
    # A checkpoint in the live file names its archived segment;
    # verify archived segments first, then the live file, so the
    # reported first break follows chronological order.
    for name, entries in archived:
        segments.append((f"archive:{name}", entries))
    segments.append(("live", live))

    expected_prev = GENESIS_PREV_HASH
    for segment_name, entries in segments:
        for i, e in enumerate(entries):
            checked += 1
            try:
                seq, eid = e["seq"], e["id"]
                recomputed = _entry_hash(seq, eid, e["ts"], e["kind"],
                                         e["data"], e["prev_hash"])
            except (KeyError, TypeError):
                return {"ok": False, "checked": checked,
                        "first_break": {"segment": segment_name,
                                        "index": i,
                                        "id": e.get("id"),
                                        "reason": "malformed entry"},
                        "warnings": warnings}
            if e.get("hash") != recomputed:
                return {"ok": False, "checked": checked,
                        "first_break": {"segment": segment_name,
                                        "index": i, "id": eid,
                                        "reason": "hash mismatch"},
                        "warnings": warnings}
            # Checkpoint re-anchor: a checkpoint's prev_hash is
            # the archived segment's last hash by construction.
            # When that segment is still on disk it was already
            # verified just before this file, so the generic link
            # check below passes. When it is GONE, the generic
            # check cannot pass (there is no previous hash left
            # to compare against) — instead the checkpoint must
            # be internally consistent (prev_hash == the
            # archived_last_hash it records) and is then treated
            # as a re-anchor point, with the loss recorded as a
            # warning. Without this, deleting an archive would
            # read as a chain break rather than as a lost
            # segment, which misdescribes the failure.
            _reanchored = False
            if e.get("kind") == "checkpoint":
                _cdata = e.get("data") or {}
                _seg = _cdata.get("archived_file")
                if _seg and not (arch / _seg).exists():
                    if e.get("prev_hash") != \
                            _cdata.get("archived_last_hash"):
                        return {"ok": False, "checked": checked,
                                "first_break": {
                                    "segment": segment_name,
                                    "index": i, "id": eid,
                                    "reason": "checkpoint "
                                    "prev_hash != recorded "
                                    "archived_last_hash"},
                                "warnings": warnings}
                    _reanchored = True
            if not _reanchored and e.get("prev_hash") != expected_prev:
                return {"ok": False, "checked": checked,
                        "first_break": {"segment": segment_name,
                                        "index": i, "id": eid,
                                        "reason": "link break: prev_hash "
                                        "!= previous hash"},
                        "warnings": warnings}
            expected_prev = e["hash"]
            if e.get("kind") == "checkpoint":
                data = e.get("data") or {}
                seg_name = data.get("archived_file")
                if seg_name:
                    seg_path = arch / seg_name
                    if not seg_path.exists():
                        warnings.append({"archive_missing": seg_name})
                    else:
                        seg_entries = _read_lines(seg_path)
                        if seg_entries:
                            last = seg_entries[-1]
                            if data.get("archived_last_hash") \
                                    and last.get("hash") != \
                                    data["archived_last_hash"]:
                                return {"ok": False, "checked": checked,
                                        "first_break": {
                                            "segment": segment_name,
                                            "index": i, "id": eid,
                                            "reason": "checkpoint "
                                            "archived_last_hash "
                                            "mismatch"},
                                        "warnings": warnings}
    return {"ok": True, "checked": checked, "first_break": None,
            "warnings": warnings}


def verify_chain() -> bool:
    """True when the chain (live + present archives) is intact."""
    return verify_chain_detail()["ok"]


def rotation_needed() -> bool:
    """True when the live store crosses its size or age bound."""
    path = store_path()
    if not path.exists():
        return False
    if path.stat().st_size >= _max_bytes():
        return True
    age_days = (time.time() - path.stat().st_mtime) / 86400.0
    return age_days >= _max_age_days()


def rotate() -> str:
    """Rotate the live store (PF-15) and return the checkpoint
    entry's hash ("" if there was nothing to rotate or rotation
    failed). The checkpoint entry is the first entry of the new
    file: its prev_hash is the archived segment's last hash, so
    the new chain continues the old one by hash, and its data
    carries the archived file name, entry count, last hash and
    whole-file SHA-256. If rotation is not needed, returns the
    current last hash (no-op) — callers that need a forced
    rotation pass force=True via rotate_now() below.
    """
    return _rotate(force=False)


def rotate_now() -> str:
    """Forced rotation (same checkpoint semantics as rotate())."""
    return _rotate(force=True)


def _rotate(force: bool) -> str:
    path = store_path()
    if not path.exists():
        return ""
    with _lock:
        entries = _read_lines(path)
        if not entries:
            return ""
        if not force and not rotation_needed():
            return entries[-1]["hash"]
        arch = archive_dir()
        arch.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        dest = arch / f"records_{stamp}.jsonl"
        n = 1
        while dest.exists():
            dest = arch / f"records_{stamp}_{n}.jsonl"
            n += 1
        file_hash = _file_sha256(path)
        last_hash = entries[-1]["hash"]
        count = len(entries)
        # Move the segment aside, then open the new live file
        # with the checkpoint as its first entry.
        os.replace(path, dest)
        checkpoint_data = {
            "archived_file": dest.name,
            "archived_entries": count,
            "archived_last_hash": last_hash,
            "archived_sha256": file_hash,
            "note": ("Rotation checkpoint: the archived segment "
                     "above continues into this entry by hash; "
                     "verify_chain() covers both."),
        }
        event_id = uuid.uuid4().hex
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        h = _entry_hash(0, event_id, ts, "checkpoint",
                        checkpoint_data, last_hash)
        record = {"seq": 0, "id": event_id, "ts": ts,
                  "kind": "checkpoint", "data": checkpoint_data,
                  "prev_hash": last_hash, "hash": h}
        with open(path, "a", encoding="utf-8") as f:
            f.write(_canonical(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return h


def export_chain(dest_path) -> dict:
    """Write a user-held anchor export: every live + archived
    entry, oldest-first, plus a SHA-256 over the whole export.

    Returns {entries, sha256, path}. Anchors are user-held
    exports by design — never a VYUK server, never a public
    transparency log. A later verify of the export is a plain
    sha256sum comparison plus re-running verify_chain() on a
    store restored from it.
    """
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    all_entries: list[dict] = []
    arch = archive_dir()
    if arch.is_dir():
        for p in sorted(arch.glob("*.jsonl")):
            all_entries.extend(_read_lines(p))
    all_entries.extend(_read_lines(store_path()))
    blob = "\n".join(_canonical(e) for e in all_entries)
    if blob:
        blob += "\n"
    dest.write_text(blob, encoding="utf-8")
    return {"entries": len(all_entries),
            "sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
            "path": str(dest)}

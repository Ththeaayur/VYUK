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
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from . import store_base

log_name = "nova.audit_chain"
_log = logging.getLogger(log_name)

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


def _read_lines_detailed(path: Path) -> list[tuple[int, dict | None]]:
    """Non-blank lines of a store file, in file order, as
    (0-based line number, parsed entry or None).

    A line is an entry only when it parses as JSON AND is a dict
    (a JSON array or bare string is garbage, not a record — the
    same notion _read_lines has always used). None marks an
    unparseable line: a torn write, or a destroyed file. Read
    paths keep skipping those lines; verify_chain_detail()
    treats any unparseable line as a break. append() refuses
    only on a destroyed chain (zero parseable entries — the fork
    case, round-3 C3-F1 rework); a partial tear still has valid
    entries to chain from, so the append continues from the last
    valid one while verify keeps naming the break."""
    lines: list[tuple[int, dict | None]] = []
    if not path.exists():
        return lines
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                lines.append((line_no, None))
                continue  # torn / partial line: skipped, never fatal
            if isinstance(obj, dict):
                lines.append((line_no, obj))
            else:
                lines.append((line_no, None))
    return lines


def _read_lines(path: Path) -> list[dict]:
    """Parse a store file, skipping a torn last line (crash
    mid-append) the way core/audit.py's reader does."""
    return [e for _, e in _read_lines_detailed(path)
            if e is not None]


def _corrupt_lines(path: Path) -> tuple[int, list[int]]:
    """(non-blank line count, line numbers of the unparseable
    ones) for a store file. (0, []) when the file is missing,
    empty, or blank-only — a genuinely empty ledger is healthy,
    only non-blank unparseable content is corruption."""
    detailed = _read_lines_detailed(path)
    bad = [n for n, e in detailed if e is None]
    return len(detailed), bad


# The last refused append (round-3 C3-F1): append() returns ""
# on failure by design, so the reason a corrupt file was
# refused has to live somewhere the caller — and a test — can
# read it. None when no refusal has happened in this process.
_last_refusal: dict | None = None


def last_append_refusal() -> dict | None:
    """The last append refusal on a corrupt ledger, if any:
    {path, reason, nonblank_lines, unparseable_lines,
    first_bad_line}. Never raises."""
    return dict(_last_refusal) if _last_refusal is not None else None


def _refuse_corrupt(path: Path, nonblank: int, bad: list[int]) -> str:
    """Record an honest refusal to append to a corrupt ledger
    (round-3 C3-F1) and return "" — the append's failure value.
    Names the file and the state; never deletes, overwrites or
    moves the corrupt bytes."""
    global _last_refusal
    reason = (
        f"refused: ledger file {path} is corrupt — "
        f"{len(bad)} of {nonblank} non-blank line(s) unparseable "
        f"(first bad line {bad[0]}); append refused so the "
        f"corrupt bytes are preserved, not forked into a new chain")
    _last_refusal = {"path": str(path), "reason": reason,
                     "nonblank_lines": nonblank,
                     "unparseable_lines": len(bad),
                     "first_bad_line": bad[0]}
    _log.error("audit_chain.append %s", reason)
    return ""


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def append(kind: str, data: dict) -> str:
    """Append one event to the chain. Returns its id, or "" on
    failure (a failing write never raises to the caller).

    Round-3 C3-F1: when the live ledger file is a DESTROYED
    chain (non-blank lines, zero parseable entries) the append
    is REFUSED ("" + a logged, readable refusal — see
    last_append_refusal()) instead of silently forking a fresh
    chain at seq 0 after the garbage. A fork would make the
    destroyed events unrecoverable AND invisible (verify would
    attest the fresh chain); a refusal leaves the corrupt bytes
    intact and the chain verdict not-ok, so the loss stays
    visible. Rework (2026-11-20): the refusal is narrowed to
    the destroyed chain only. On a PARTIAL tear (some valid
    entries plus torn lines) there is no fork — the append
    continues from the last valid entry (seq continues,
    prev_hash chains from its hash), the torn bytes stay in the
    file untouched, and verify still reports the break, so a
    crash mid-append can never leave the chain stuck. A missing
    / empty / blank-only file is a healthy empty chain and
    appends as before."""
    if not isinstance(data, dict):
        return ""
    # Corruption check BEFORE rotation: rotating a corrupt file
    # would archive the garbage behind a fresh checkpoint and
    # launder the loss the same way a fork would. Refuse ONLY a
    # destroyed chain (zero parseable entries — the fork case,
    # round-3 C3-F1 rework): a partial tear still has valid
    # entries to chain from, so the append continues below from
    # the last valid entry while verify keeps naming the break.
    try:
        _nonblank, _bad = _corrupt_lines(store_path())
        if _bad and _nonblank == len(_bad):
            return _refuse_corrupt(store_path(), _nonblank, _bad)
    except Exception:  # noqa: BLE001 — a failed corruption scan
        pass  # must never break the call; the in-lock check below
        # is the backstop and runs on the same data.
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
            _detailed = _read_lines_detailed(path)
            _bad = [n for n, e in _detailed if e is None]
            if _bad and len(_detailed) == len(_bad):
                # Backstop for a file corrupted between the
                # pre-rotation scan and this write — destroyed
                # chain only (round-3 C3-F1 rework); a partial
                # tear continues from the last valid entry below.
                return _refuse_corrupt(path, len(_detailed), _bad)
            entries = [e for _, e in _detailed if e is not None]
            seq = (entries[-1]["seq"] + 1) if entries else 0
            prev = entries[-1]["hash"] if entries else GENESIS_PREV_HASH
            event_id = uuid.uuid4().hex
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            h = _entry_hash(seq, event_id, ts, kind, data, prev)
            record = {"seq": seq, "id": event_id, "ts": ts, "kind": kind,
                      "data": data, "prev_hash": prev, "hash": h}
            # A crash mid-append can leave a torn LAST line with no
            # trailing newline. Writing the new record straight
            # after it would merge the two lines into one
            # unparseable line and swallow the new entry — the
            # chain would not actually continue. Terminate the
            # torn line first: the torn bytes themselves stay
            # untouched and still unparseable, so verify keeps
            # naming the break, and the new record lands on its
            # own line, chained from the last valid entry.
            _needs_terminator = False
            if path.exists() and path.stat().st_size > 0:
                with open(path, "rb") as _rf:
                    _rf.seek(-1, 2)
                    _needs_terminator = _rf.read(1) != b"\n"
            with open(path, "a", encoding="utf-8") as f:
                if _needs_terminator:
                    f.write("\n")
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


def _verified_prefix() -> tuple:
    """Walk live + archived segments in chronological order,
    validating every link exactly as verify_chain_detail() does,
    collecting entries until the FIRST break.

    Returns (entries, first_break, warnings, checked) — entries
    is the trusted prefix in file order (oldest-first), first_break
    is None when the whole chain verifies. This is the ONE walk
    verify and trusted reads share, so a read can never disagree
    with verify about where the chain breaks.

    Loophole batch 6 (#12): reads must stop at the first break
    instead of reading through it — a tampered line's values (a
    forged receipt, an edited amount) were named as a break by
    verify but still trusted by later reads. read_trusted() is
    built on this walk; the old read() keeps its tolerant shape
    for callers that already handle breaks themselves."""
    warnings: list[dict] = []
    checked = 0
    entries: list[dict] = []
    live = _read_lines_detailed(store_path())
    arch = archive_dir()
    archived: list[tuple[str, list[tuple[int, dict | None]]]] = []
    if arch.is_dir():
        for p in sorted(arch.glob("*.jsonl")):
            archived.append((p.name, _read_lines_detailed(p)))
    segments: list[tuple[str, list[tuple[int, dict | None]]]] = []
    for name, lines in archived:
        segments.append((f"archive:{name}", lines))
    segments.append(("live", live))

    expected_prev = GENESIS_PREV_HASH
    for segment_name, lines in segments:
        # Round-3 C3-F1: a file of pure garbage used to read as a
        # healthy empty chain (zero parseable entries, ok True).
        # A destroyed chain is a broken chain, not an empty one:
        # any non-blank unparseable line is a break, named by its
        # file line number, and a file with non-blank lines but
        # zero parseable entries says so in the reason. A missing
        # / empty / blank-only file still verifies ok with
        # checked 0. Lines are walked in file order, so the first
        # break — hash, link, malformed or unparseable — is the
        # chronologically first one.
        _parseable = sum(1 for _, e in lines if e is not None)
        i = -1  # entry index within this segment (parseable only)
        for line_no, e in lines:
            if e is None:
                if _parseable == 0:
                    reason = (f"unparseable line {line_no}: "
                              f"{len(lines)} non-blank line(s), "
                              f"none parseable — destroyed chain, "
                              f"not an empty one")
                else:
                    reason = f"unparseable line {line_no}"
                return entries, {"segment": segment_name,
                                 "index": line_no,
                                 "id": None,
                                 "reason": reason}, warnings, checked
            i += 1
            checked += 1
            try:
                seq, eid = e["seq"], e["id"]
                recomputed = _entry_hash(seq, eid, e["ts"], e["kind"],
                                         e["data"], e["prev_hash"])
            except (KeyError, TypeError):
                return entries, {"segment": segment_name,
                                 "index": i,
                                 "id": e.get("id"),
                                 "reason": "malformed entry"}, warnings, checked
            if e.get("hash") != recomputed:
                return entries, {"segment": segment_name,
                                 "index": i, "id": eid,
                                 "reason": "hash mismatch"}, warnings, checked
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
                        return entries, {
                            "segment": segment_name,
                            "index": i, "id": eid,
                            "reason": "checkpoint "
                            "prev_hash != recorded "
                            "archived_last_hash"}, warnings, checked
                    _reanchored = True
            if not _reanchored and e.get("prev_hash") != expected_prev:
                return entries, {"segment": segment_name,
                                 "index": i, "id": eid,
                                 "reason": "link break: prev_hash "
                                 "!= previous hash"}, warnings, checked
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
                                return entries, {
                                    "segment": segment_name,
                                    "index": i, "id": eid,
                                    "reason": "checkpoint "
                                    "archived_last_hash "
                                    "mismatch"}, warnings, checked
            expected_prev = e["hash"]
            entries.append(e)
    return entries, None, warnings, checked


def read_trusted(kind: str | None = None, limit: int = 50) -> dict:
    """Read only the VERIFIED prefix of the chain, newest-first.

    Returns {"entries", "ok", "first_break", "warnings"} — entries
    stop at the first break, so a tampered line (and everything
    after it) is never returned as a trusted value; first_break
    names the break the same way verify_chain_detail() does, so
    callers can show it instead of silently returning less.
    ``limit`` <= 0 means no limit. The old read() is unchanged:
    it still returns every parseable entry, for callers that
    already verify separately."""
    entries, first_break, warnings, _checked = _verified_prefix()
    if kind is not None:
        entries = [e for e in entries if e.get("kind") == kind]
    entries.reverse()
    if limit and limit > 0:
        entries = entries[:limit]
    return {"entries": entries, "ok": first_break is None,
            "first_break": first_break, "warnings": warnings}


def chain_break() -> dict | None:
    """The chain's first break, or None when it verifies intact."""
    _entries, first_break, _warnings, _checked = _verified_prefix()
    return first_break


def verify_chain_detail() -> dict:
    """Verify the live chain plus every archived segment still
    present, in order. Returns a dict::

        {ok, checked, first_break, warnings}

    ``first_break`` is None when intact, else
    {segment, index, id, reason} naming the FIRST bad link:
    a recomputed hash that does not match, a prev_hash that
    does not equal the previous entry's hash, a genesis entry
    whose prev_hash is not GENESIS_PREV_HASH, a checkpoint
    whose recorded archived hash does not match the segment it
    names, or a non-blank line that does not parse as a record
    (round-3 C3-F1 — a destroyed chain is a broken chain, not
    an empty one; a missing / empty / blank-only file still
    verifies ok with checked 0). ``warnings`` lists
    non-breaking notes (currently only ``archive_missing``
    entries for checkpoint-named segments that are no longer
    on disk).
    """
    _entries, first_break, warnings, checked = _verified_prefix()
    return {"ok": first_break is None, "checked": checked,
            "first_break": first_break, "warnings": warnings}


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
        _detailed = _read_lines_detailed(path)
        if any(e is None for _, e in _detailed):
            # Round-3 C3-F1: never rotate a corrupt file — moving
            # it to the archive behind a fresh checkpoint would
            # launder the corruption the same way a forked append
            # would. The caller (append) refuses first; this is
            # the backstop for a direct rotate() call.
            return ""
        entries = [e for _, e in _detailed if e is not None]
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

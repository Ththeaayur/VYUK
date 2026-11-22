"""Wave 10 (v5.65) Leaf 3 — PF-27 scheduled automatic backups (STAGED, unwired).

Scheduled snapshot engine: a ZIP bundle of settings + memory +
milestones, a scheduler record persisted in data/, a retention bound,
and a failure record that a status getter reports (never silent).
The schedule tick itself is a pure function (due? -> run -> record)
that the coordinator wires to the existing routine/watcher tick;
this module starts no thread and no background loop.

BUNDLE FORMAT — defined here, and deliberately CONVERGENT with
Leaf 2's portability module (core/portability.py), which was
read (not edited) while writing this module:

  settings.json          the settings snapshot (JSON object)
  memories/<relpath>.md  every .md file under data/memories/
  milestones.jsonl       the exported milestone lines, verbatim
  assistant.db           a consistent snapshot of the assistant
                         memory store (GAPS-27, M-V7 — see below)
  manifest.json          format, counts, and SHA-256 per entry

Manifest fields: ``format`` (``vyuk-portability-1`` — the same
format string Leaf 2 uses, so a PF-27 bundle verifies under
Leaf 2's ``verify_bundle`` and vice versa), ``created_at`` (UTC),
``hashes`` (bundle name -> SHA-256 hex), ``settings_keys``,
``milestone_lines``, ``milestones_skipped_malformed``, plus
backup-only keys ``backup_kind`` (``scheduled``) and
``app_version`` (``5.64.1``, the product version — this module
carries no product-version constant of its own beyond recording
the current one in the manifest).

CONVERGENCE POINT for the coordinator: the two modules share the
format but not code (leaf independence). If a single shared
reader/writer is wanted later, factor the manifest/hash helpers
out of these two modules into one home — do not let the two
format strings drift apart; the suite cross-checks a PF-27
bundle against Leaf 2's verifier.

Real formats (read from the tree, not invented):
- Settings: the QSettings-backed snapshot shape of
  ``core/settings_schema.py`` (``SettingsStore.snapshot()``).
  This module never touches QSettings itself — the caller passes
  a plain ``dict`` snapshot in and applies the restored dict back
  out — so it works with system python, with no Qt present.
- Memory: the Wave 6 file store (``core/memory_files.py``):
  Markdown + frontmatter files under ``data/memories/``.
  Files are copied byte-for-byte; this module never parses
  frontmatter, so it cannot mangle a record.
- Assistant memory (GAPS-27, M-V7): ``data/assistant.db`` —
  the facts/episodes store — IS in the bundle now, as the
  manifest entry ``assistant.db``: a consistent snapshot taken
  through the sqlite3 backup API (a live DB is never copied
  byte-for-byte — a mid-write copy would be corrupt). Before
  GAPS-27 the file was excluded by design ("the file store is
  the memory of record for portability"); the exclusion meant
  a restore could never bring the facts back, so the snapshot
  joins the manifest as a new entry — the layout of the other
  entries is unchanged. A bundle made before GAPS-27 simply
  has no such entry; restore reports it ``missing`` and applies
  the rest. When the file is absent or unsnapshotable at backup
  time it is omitted and the report says so explicitly
  (``assistant_db`` None + ``assistant_db_error``), never
  silently — the milestones-malformed pattern.
- Milestones: ``data/milestones.jsonl`` (``core/milestones.py``):
  append-only JSON Lines, one object per event with an ``event``
  field. Only well-formed lines are exported; malformed lines
  are counted in the report and the manifest, never silently
  dropped from the accounting.

Restore verifies every manifest hash BEFORE applying anything:
a single hash failure applies nothing. Entries present in the
ZIP but absent from the manifest are reported as ``extra`` and
never applied. Sections absent from the bundle are reported as
``missing``, never papered over.

Retention: ``prune_backups`` enforces max count AND max bytes,
oldest first, and NEVER prunes the newest bundle (at least one
bundle always survives, even if it alone exceeds max bytes).

Scheduler record (``data/backup_schedule_v565.json`` by default):
``interval_seconds``, ``last_run``, ``next_due``, ``last_status``
(``ok`` | ``failed`` | ``never``), ``last_bundle``,
``last_failure`` ({``at``, ``error``} or None) and ``total_runs``.
``schedule_tick`` is pure over (record, now, backup callable):
not due -> ``{"ran": False}``; due -> run, record, and on
exception record the failure and return it — never swallow it.
``get_status`` surfaces the last failure verbatim.

Stdlib only. Product VERSION is untouched (still 5.64.1).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone

BUNDLE_FORMAT = "vyuk-portability-1"
SETTINGS_NAME = "settings.json"
MILESTONES_NAME = "milestones.jsonl"
ASSISTANT_DB_NAME = "assistant.db"
MANIFEST_NAME = "manifest.json"
MEMORIES_PREFIX = "memories/"
SCHEDULE_FILENAME = "backup_schedule_v565.json"
DEFAULT_INTERVAL_SECONDS = 7 * 24 * 3600  # weekly
APP_VERSION = "5.64.1"  # recorded in manifests; not a version bump

# Loophole batch 3 (#10, L-23): the backup is ENCRYPTED.
# A bundle is `VYUKBK1` + salt + nonce + HMAC-CTR ciphertext +
# tag, wrapping the same ZIP the v1 format wrote (whose manifest
# now says "vyuk-backup-v2"). The construction mirrors
# core/secrets_store (PBKDF2-HMAC-SHA256 key derivation,
# HMAC-CTR keystream, encrypt-then-MAC tag) and is implemented
# here, locally, so this module stays stdlib-only. A bundle
# written before encryption (a plain .zip) is refused on verify
# and restore with a plain-words reason — it is never silently
# treated as an encrypted bundle, and never decrypted.
BUNDLE_FORMAT_ENCRYPTED = "vyuk-backup-v2"
BUNDLE_MAGIC = b"VYUKBK1"
BUNDLE_SUFFIX = ".vyukbak"
LEGACY_SUFFIX = ".zip"
_BACKUP_KDF_ITERATIONS = 200_000
_BACKUP_SALT_BYTES = 16
_BACKUP_NONCE_BYTES = 16
_BACKUP_TAG_BYTES = 32  # full HMAC-SHA256 output
PASSPHRASE_FILENAME = "backup_passphrase.bin"
_NO_PASSPHRASE_MSG = (
    "no backup passphrase is set — set one in Settings "
    "(Advanced → Backup passphrase) before backing up; "
    "a backup without a passphrase is refused, never written "
    "in plaintext")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-CTR keystream, mirroring core/secrets_store._keystream:
    block i = HMAC-SHA256(key, nonce || i as 8-byte big-endian),
    concatenated and truncated."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(
            key, nonce + counter.to_bytes(8, "big"),
            hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def _derive_backup_key(passphrase: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256, 200,000 iterations, 32-byte key —
    the same derivation strength as core/secrets_store."""
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt,
        _BACKUP_KDF_ITERATIONS, dklen=32)


def encrypt_backup_blob(plaintext: bytes, passphrase: str) -> bytes:
    """Wrap a ZIP in the encrypted bundle format:
    ``VYUKBK1`` + salt + nonce + ciphertext + tag. The tag is
    HMAC-SHA256(key, b"tag" + nonce + ct) — encrypt-then-MAC,
    mirroring core/secrets_store._machine_key_protect."""
    salt = os.urandom(_BACKUP_SALT_BYTES)
    nonce = os.urandom(_BACKUP_NONCE_BYTES)
    key = _derive_backup_key(passphrase, salt)
    stream = _backup_keystream(key, nonce, len(plaintext))
    ct = bytes(b ^ s for b, s in zip(plaintext, stream))
    tag = hmac.new(key, b"tag" + nonce + ct, hashlib.sha256).digest()
    return BUNDLE_MAGIC + salt + nonce + ct + tag


def decrypt_backup_blob(blob: bytes, passphrase: str) -> bytes:
    """Unwrap an encrypted bundle. The tag is verified FIRST
    (encrypt-then-MAC): no plaintext exists until it verifies.
    Raises ValueError for a wrong magic, a truncated bundle, or
    a tag mismatch (wrong passphrase or tampered bytes)."""
    head = len(BUNDLE_MAGIC) + _BACKUP_SALT_BYTES + _BACKUP_NONCE_BYTES
    if len(blob) < head + _BACKUP_TAG_BYTES:
        raise ValueError("backup bundle is truncated")
    if blob[:len(BUNDLE_MAGIC)] != BUNDLE_MAGIC:
        raise ValueError(
            "not an encrypted VYUK backup — this is a legacy "
            "plain backup, written before encryption; it cannot "
            "be verified or restored")
    salt = blob[len(BUNDLE_MAGIC):len(BUNDLE_MAGIC) + _BACKUP_SALT_BYTES]
    nonce = blob[len(BUNDLE_MAGIC) + _BACKUP_SALT_BYTES:head]
    tag = blob[-_BACKUP_TAG_BYTES:]
    ct = blob[head:-_BACKUP_TAG_BYTES]
    key = _derive_backup_key(passphrase, salt)
    expected = hmac.new(key, b"tag" + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError(
            "backup integrity check failed — wrong passphrase "
            "or tampered backup")
    stream = _backup_keystream(key, nonce, len(ct))
    return bytes(b ^ s for b, s in zip(ct, stream))


def _read_bundle_zip(bundle_path: str, passphrase: str | None
                     ) -> tuple[bytes | None, str | None]:
    """(inner ZIP bytes, error): decrypt an encrypted bundle, or
    refuse a legacy plain ZIP / a missing passphrase with a
    plain-words reason. The error is None on success."""
    try:
        with open(bundle_path, "rb") as fh:
            blob = fh.read()
    except OSError as exc:
        return None, f"bundle-unreadable: {exc}"
    if blob[:2] == b"PK" and blob[:len(BUNDLE_MAGIC)] != BUNDLE_MAGIC:
        return None, (
            "legacy-plain-backup: this backup was written before "
            "encryption and cannot be verified or restored; take "
            "a new encrypted backup instead")
    if not passphrase:
        return None, (
            "passphrase-required: " + _NO_PASSPHRASE_MSG)
    try:
        return decrypt_backup_blob(blob, passphrase), None
    except ValueError as exc:
        return None, str(exc)


def set_backup_passphrase(data_dir: str, passphrase: str) -> str:
    """Store the backup passphrase for this data dir, protected
    by the secrets store (never in plaintext, never inside a
    backup — the backup never collects this file). Returns the
    path written. An empty passphrase clears it instead."""
    from .secrets_store import protect_bytes

    path = os.path.join(data_dir, PASSPHRASE_FILENAME)
    if not passphrase:
        try:
            os.unlink(path)
        except OSError:
            pass
        return path
    os.makedirs(data_dir, exist_ok=True)
    blob = protect_bytes(passphrase.encode("utf-8"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, blob)
    finally:
        os.close(fd)
    return path


def get_backup_passphrase(data_dir: str) -> str | None:
    """The stored backup passphrase, or None when none is set.
    A corrupt/unreadable store reads as unset (never partial)."""
    from .secrets_store import unprotect_bytes

    path = os.path.join(data_dir, PASSPHRASE_FILENAME)
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
        return unprotect_bytes(blob).decode("utf-8")
    except Exception:  # noqa: BLE001 — unset, corrupt or unreadable
        return None


def has_backup_passphrase(data_dir: str) -> bool:
    """True when a backup passphrase is stored for this data dir."""
    return get_backup_passphrase(data_dir) is not None


def _safe_relpath(base: str, path: str) -> str:
    """Relative path of ``path`` under ``base``, or '' if it escapes."""
    base_abs = os.path.abspath(base)
    path_abs = os.path.abspath(path)
    rel = os.path.relpath(path_abs, base_abs)
    if rel == "." or rel.startswith(".." + os.sep) or rel == "..":
        return ""
    if os.path.isabs(rel):
        return ""
    return rel


def _collect_memories(data_dir: str) -> dict[str, bytes]:
    """Byte-for-byte map of bundle name -> content for data/memories/."""
    memories_dir = os.path.join(data_dir, "memories")
    out: dict[str, bytes] = {}
    if not os.path.isdir(memories_dir):
        return out
    for root, _dirs, files in os.walk(memories_dir):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            full = os.path.join(root, name)
            rel = _safe_relpath(memories_dir, full)
            if not rel:
                continue
            with open(full, "rb") as fh:
                out[MEMORIES_PREFIX + rel.replace(os.sep, "/")] = fh.read()
    return out


def _collect_milestones(data_dir: str) -> tuple[bytes, int]:
    """(verbatim valid lines, count of skipped malformed lines)."""
    path = os.path.join(data_dir, "milestones.jsonl")
    if not os.path.isfile(path):
        return b"", 0
    good: list[str] = []
    skipped = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except (ValueError, TypeError):
                skipped += 1
                continue
            if isinstance(item, dict) and item.get("event"):
                good.append(stripped)
            else:
                skipped += 1
    blob = ("\n".join(good) + "\n").encode("utf-8") if good else b""
    return blob, skipped


def _collect_assistant_db(data_dir: str
                          ) -> tuple[bytes | None, str | None, int]:
    """(snapshot bytes, error, transcripts stripped): a consistent
    snapshot of data/assistant.db via the sqlite3 backup API.
    Absent -> (None, "absent", 0); unsnapshotable ->
    (None, <reason>, 0). The
    caller omits the entry and records the reason — a snapshot
    failure never fails the whole bundle."""
    path = os.path.join(data_dir, ASSISTANT_DB_NAME)
    if not os.path.isfile(path):
        return None, "absent", 0
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="vyuk-assistant-db-",
                                   suffix=".sqlite3")
        os.close(fd)
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                              timeout=5.0)
        try:
            dst = sqlite3.connect(tmp, timeout=5.0)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        # Loophole batch 3 (L-23): the chat history never leaves
        # the machine in a backup — the transcripts are stripped
        # from the SNAPSHOT (the live database is untouched), the
        # freed pages vacuumed so no transcript bytes linger in
        # the file, and the stripped count is reported. Facts,
        # memories and milestones still back up; a restore brings
        # the facts back without the turns that taught them.
        stripped = 0
        conn = sqlite3.connect(tmp, timeout=5.0)
        try:
            try:
                stripped = conn.execute(
                    "SELECT COUNT(*) FROM conversation_logs"
                    ).fetchone()[0]
            except sqlite3.Error:
                stripped = 0
            else:
                if stripped:
                    conn.execute("DELETE FROM conversation_logs")
                    conn.commit()
                    conn.execute("VACUUM")
        finally:
            conn.close()
        with open(tmp, "rb") as fh:
            return fh.read(), None, int(stripped)
    except Exception as exc:  # noqa: BLE001 — the omission +
        return None, str(exc), 0  # reason below is the honest answer
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def create_backup(data_dir: str, settings: dict, out_path: str,
                  passphrase: str | None = None) -> dict:
    """Write one scheduled backup bundle; return a creation report.

    ``data_dir`` is read, never written (memories/milestones are
    read from it). ``settings`` must be a JSON-serialisable dict;
    a ValueError is raised rather than exporting a lossy bundle.
    The report carries ``bundle``, ``format``, ``settings_keys``,
    ``memory_files``, ``milestone_lines``,
    ``milestones_skipped_malformed`` and ``size_bytes`` — plus
    ``assistant_db`` (snapshot bytes, or None when the store was
    absent/unsnapshotable) and ``assistant_db_error`` (the reason
    when omitted, else None).

    Loophole batch 3 (#10, L-23): ``passphrase`` is REQUIRED —
    without it the backup refuses (ValueError, plain words) and
    nothing is written, never a plaintext bundle. With it, the
    bundle is encrypted (``VYUKBK1`` format, see the module
    constants) and the report carries ``encrypted: True`` plus
    ``transcripts_stripped`` (chat turns removed from the
    assistant.db snapshot). The ledger and the receipts are
    never collected — they are not in the entries map and never
    enter a bundle.
    """
    if not passphrase:
        raise ValueError(_NO_PASSPHRASE_MSG)
    try:
        settings_blob = json.dumps(
            settings, indent=2, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"settings are not JSON-serialisable: {exc}") from exc
    milestones_blob, milestones_skipped = _collect_milestones(data_dir)
    memories = _collect_memories(data_dir)
    assistant_blob, assistant_error, transcripts_stripped = (
        _collect_assistant_db(data_dir))
    entries: dict[str, bytes] = {SETTINGS_NAME: settings_blob}
    entries.update(memories)
    entries[MILESTONES_NAME] = milestones_blob
    if assistant_blob is not None:
        entries[ASSISTANT_DB_NAME] = assistant_blob
    manifest = {
        "format": BUNDLE_FORMAT_ENCRYPTED,
        "created_at": _utcnow(),
        "backup_kind": "scheduled",
        "app_version": APP_VERSION,
        "hashes": {name: _sha256(blob)
                   for name, blob in entries.items()},
        "settings_keys": sorted(settings.keys()),
        "milestone_lines": (
            len(milestones_blob.decode("utf-8").splitlines())
            if milestones_blob else 0),
        "milestones_skipped_malformed": milestones_skipped,
        "transcripts_stripped": transcripts_stripped,
    }
    manifest_blob = json.dumps(
        manifest, indent=2, sort_keys=True).encode("utf-8")
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, manifest_blob)
        for name in sorted(entries):
            zf.writestr(name, entries[name])
    with open(out_path, "wb") as fh:
        fh.write(encrypt_backup_blob(inner.getvalue(), passphrase))
    return {
        "bundle": out_path,
        "format": BUNDLE_FORMAT_ENCRYPTED,
        "encrypted": True,
        "transcripts_stripped": transcripts_stripped,
        "settings_keys": len(settings),
        "memory_files": len(memories),
        "milestone_lines": manifest["milestone_lines"],
        "milestones_skipped_malformed": milestones_skipped,
        "assistant_db": (len(assistant_blob)
                         if assistant_blob is not None else None),
        "assistant_db_error": assistant_error,
        "size_bytes": os.path.getsize(out_path),
    }


def verify_bundle(bundle_path: str, passphrase: str | None = None
                  ) -> dict:
    """Check a bundle's manifest hashes without applying anything.

    Returns {"ok", "manifest", "failed", "extra", "missing"}:
    ``failed`` lists hash mismatches and unreadable parts,
    ``extra`` lists ZIP entries absent from the manifest,
    ``missing`` lists manifest entries absent from the ZIP.

    Loophole batch 3 (#10): the bundle is decrypted first with
    ``passphrase`` — without one, or for a legacy plain ZIP, or
    when the integrity check fails (wrong passphrase or tampered
    bytes), verification fails and ``ok`` is False; nothing is
    treated as verified on a failed decrypt.
    """
    result: dict = {"ok": False, "manifest": None, "failed": [],
                    "extra": [], "missing": []}
    inner, error = _read_bundle_zip(bundle_path, passphrase)
    if error is not None:
        result["failed"] = [error]
        return result
    try:
        zf = zipfile.ZipFile(io.BytesIO(inner))
    except (zipfile.BadZipFile, OSError) as exc:
        result["failed"] = [f"bundle-unreadable: {exc}"]
        return result
    with zf:
        names = set(zf.namelist())
        if MANIFEST_NAME not in names:
            result["failed"] = ["manifest-missing"]
            return result
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            result["failed"] = [f"manifest-unreadable: {exc}"]
            return result
        result["manifest"] = manifest
        if manifest.get("format") != BUNDLE_FORMAT_ENCRYPTED:
            result["failed"].append(
                f"format-mismatch: {manifest.get('format')!r} "
                f"!= {BUNDLE_FORMAT_ENCRYPTED!r}")
        hashes = manifest.get("hashes") or {}
        if not isinstance(hashes, dict):
            result["failed"] = ["manifest-hashes-not-a-mapping"]
            return result
        for name in sorted(names):
            if name == MANIFEST_NAME:
                continue
            if name not in hashes:
                result["extra"].append(name)
        for name in sorted(hashes):
            if name not in names:
                result["missing"].append(name)
                continue
            try:
                actual = _sha256(zf.read(name))
            except (KeyError, OSError) as exc:
                result["failed"].append(f"{name}: unreadable: {exc}")
                continue
            if actual != hashes[name]:
                result["failed"].append(f"{name}: hash-mismatch")
    result["ok"] = not result["failed"]
    return result


def restore_bundle(bundle_path: str, target_data_dir: str,
                   settings_store=None,
                   passphrase: str | None = None) -> dict:
    """Restore a bundle into a data/ directory.

    Verifies hashes first; on ANY verification failure nothing is
    applied. On success: memory files are written under
    ``data/memories/``, milestones replace ``milestones.jsonl``
    (the previous file, if any, is kept as
    ``milestones.jsonl.pre-restore-<UTC timestamp>`` and named in
    the report), assistant.db replaces ``assistant.db`` (the
    previous file, if any, is kept as
    ``assistant.db.pre-restore-<UTC timestamp>`` and named in
    ``assistant_db_backup``), and settings are either returned in
    ``report["settings"]`` or applied to ``settings_store`` when
    one is supplied (a plain dict, or a QSettings-like object
    with ``setValue``/``sync``). Sections absent from the bundle
    are ``missing``, never papered over.

    Loophole batch 3 (#10): the bundle is decrypted with
    ``passphrase`` first — without it, or for a legacy plain
    ZIP, or when the integrity check fails, verification fails
    and nothing is applied.
    """
    report: dict = {"applied": [], "missing": [], "extra": [],
                    "failed": [], "settings": None,
                    "memory_files": 0, "milestone_lines": 0,
                    "milestones_skipped_malformed": 0,
                    "milestones_backup": None,
                    "assistant_db_backup": None}
    check = verify_bundle(bundle_path, passphrase=passphrase)
    manifest = check["manifest"]
    if manifest is None:
        report["failed"] = check["failed"]
        return report
    fatal = [f for f in check["failed"]]
    if fatal:
        report["failed"] = fatal
        report["extra"] = check["extra"]
        report["missing"] = check["missing"]
        return report
    report["extra"] = check["extra"]
    report["missing"] = check["missing"]
    inner, error = _read_bundle_zip(bundle_path, passphrase)
    if error is not None:
        report["failed"] = [error]
        return report
    with zipfile.ZipFile(io.BytesIO(inner)) as zf:
        names = set(zf.namelist())
        if SETTINGS_NAME in names:
            try:
                settings = json.loads(
                    zf.read(SETTINGS_NAME).decode("utf-8"))
                if not isinstance(settings, dict):
                    raise ValueError("settings.json is not a JSON object")
            except (ValueError, UnicodeDecodeError) as exc:
                report["failed"].append(f"{SETTINGS_NAME}: {exc}")
            else:
                report["settings"] = settings
                if settings_store is not None:
                    if isinstance(settings_store, dict):
                        for key in sorted(settings):
                            settings_store[key] = settings[key]
                    elif callable(getattr(settings_store, "setValue", None)):
                        for key in sorted(settings):
                            settings_store.setValue(key, settings[key])
                        sync = getattr(settings_store, "sync", None)
                        if callable(sync):
                            sync()
                    else:
                        report["failed"].append(
                            "settings_store: not a dict or QSettings-like")
                report["applied"].append(SETTINGS_NAME)
        elif SETTINGS_NAME not in report["missing"]:
            report["missing"].append(SETTINGS_NAME)
        memories_dir = os.path.join(target_data_dir, "memories")
        for name in sorted(names):
            if not name.startswith(MEMORIES_PREFIX):
                continue
            rel = name[len(MEMORIES_PREFIX):]
            if not rel or rel.startswith("/") or ".." in rel.split("/"):
                report["failed"].append(f"{name}: unsafe path, not applied")
                continue
            dest = os.path.join(memories_dir, *rel.split("/"))
            if _safe_relpath(memories_dir, dest) == "":
                report["failed"].append(f"{name}: escapes memories dir")
                continue
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(zf.read(name))
            except OSError as exc:
                report["failed"].append(f"{name}: {exc}")
            else:
                report["memory_files"] += 1
        if report["memory_files"]:
            report["applied"].append("memories/")
        if MILESTONES_NAME in names:
            try:
                blob = zf.read(MILESTONES_NAME)
                os.makedirs(target_data_dir, exist_ok=True)
                dest = os.path.join(target_data_dir, MILESTONES_NAME)
                if os.path.isfile(dest):
                    stamp = _utcnow().replace(":", "").replace("-", "")
                    backup = dest + ".pre-restore-" + stamp
                    with open(dest, "rb") as src, open(backup, "wb") as dst:
                        dst.write(src.read())
                    report["milestones_backup"] = backup
                with open(dest, "wb") as fh:
                    fh.write(blob)
                report["milestone_lines"] = (
                    len(blob.decode("utf-8").splitlines()) if blob else 0)
            except OSError as exc:
                report["failed"].append(f"{MILESTONES_NAME}: {exc}")
            else:
                report["applied"].append(MILESTONES_NAME)
        elif MILESTONES_NAME not in report["missing"]:
            report["missing"].append(MILESTONES_NAME)
        if ASSISTANT_DB_NAME in names:
            try:
                blob = zf.read(ASSISTANT_DB_NAME)
                os.makedirs(target_data_dir, exist_ok=True)
                dest = os.path.join(target_data_dir, ASSISTANT_DB_NAME)
                if os.path.isfile(dest):
                    stamp = _utcnow().replace(":", "").replace("-", "")
                    backup = dest + ".pre-restore-" + stamp
                    with open(dest, "rb") as src, open(backup, "wb") as dst:
                        dst.write(src.read())
                    report["assistant_db_backup"] = backup
                with open(dest, "wb") as fh:
                    fh.write(blob)
            except OSError as exc:
                report["failed"].append(f"{ASSISTANT_DB_NAME}: {exc}")
            else:
                report["applied"].append(ASSISTANT_DB_NAME)
        elif ASSISTANT_DB_NAME not in report["missing"]:
            report["missing"].append(ASSISTANT_DB_NAME)
    report["milestones_skipped_malformed"] = manifest.get(
        "milestones_skipped_malformed", 0)
    return report


# --- Scheduler record -------------------------------------------------

def default_schedule_record(interval_seconds: int = DEFAULT_INTERVAL_SECONDS
                            ) -> dict:
    """A fresh scheduler record: never run, due immediately."""
    return {
        "interval_seconds": int(interval_seconds),
        "last_run": None,
        "next_due": None,  # None = due immediately (first run)
        "last_status": "never",
        "last_bundle": None,
        "last_failure": None,
        "total_runs": 0,
    }


def load_schedule_record(data_dir: str) -> dict:
    """Read the persisted scheduler record, or a fresh one."""
    path = os.path.join(data_dir, SCHEDULE_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            record = json.load(fh)
        if not isinstance(record, dict):
            raise ValueError("record is not an object")
    except (OSError, ValueError):
        return default_schedule_record()
    merged = default_schedule_record(
        record.get("interval_seconds", DEFAULT_INTERVAL_SECONDS))
    merged.update({k: v for k, v in record.items() if k in merged})
    return merged


def save_schedule_record(data_dir: str, record: dict) -> str:
    """Persist the scheduler record; returns the path written."""
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, SCHEDULE_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
    return path


def is_due(record: dict, now: datetime) -> bool:
    """True when the schedule says a backup is due at ``now``.

    The record's ``next_due`` is always UTC-aware (``_parse_utc``
    stamps it), but the live RoutineScheduler calls this with
    its default ``now_fn`` — ``datetime.now()``, naive local
    time. Comparing naive >= aware raised TypeError on every
    tick once a ``next_due`` existed, so scheduled backups
    never ran after the first one (GAPS-30). A naive ``now``
    is read as local time and converted to UTC, which is what
    ``schedule_tick`` already does for its stamps.
    """
    nxt = _parse_utc(record.get("next_due"))
    if nxt is None:
        return True
    if now.tzinfo is None:
        now = now.astimezone(timezone.utc)
    return now >= nxt


def schedule_tick(record: dict, now: datetime, backup_fn) -> dict:
    """One pure schedule tick: due? -> run -> record.

    ``backup_fn`` is a zero-argument callable returning a creation
    report (see ``create_backup``) — the coordinator supplies the
    closure over its data dir, settings snapshot and bundle path.
    Not due -> ``{"ran": False, "record": record}`` with the record
    untouched. Due and successful -> the record advances
    (``last_run``/``next_due``/``last_bundle``/``last_status="ok"``,
    ``last_failure`` cleared). Due and failing -> the failure is
    RECORDED on the record (``last_status="failed"``,
    ``last_failure`` = {``at``, ``error``}) and returned — never
    swallowed, never raised past the tick.
    """
    if not is_due(record, now):
        return {"ran": False, "record": record}
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        report = backup_fn()
    except Exception as exc:  # noqa: BLE001 — failure is the record
        record["last_status"] = "failed"
        record["last_failure"] = {"at": stamp, "error": str(exc)}
        record["total_runs"] = int(record.get("total_runs", 0)) + 1
        return {"ran": True, "ok": False, "record": record,
                "error": str(exc)}
    interval = int(record.get("interval_seconds",
                              DEFAULT_INTERVAL_SECONDS))
    nxt = now.astimezone(timezone.utc).timestamp() + interval
    record["last_run"] = stamp
    record["next_due"] = datetime.fromtimestamp(
        nxt, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record["last_status"] = "ok"
    record["last_bundle"] = report.get("bundle") if isinstance(
        report, dict) else None
    record["last_failure"] = None
    record["total_runs"] = int(record.get("total_runs", 0)) + 1
    return {"ran": True, "ok": True, "record": record, "report": report}


def get_status(data_dir: str) -> dict:
    """The scheduler status a UI/doctor surface shows.

    Always includes ``last_failure`` (None when the last run
    succeeded) — a failed backup is reported here, never silent.
    """
    record = load_schedule_record(data_dir)
    return {
        "interval_seconds": record["interval_seconds"],
        "last_run": record["last_run"],
        "next_due": record["next_due"],
        "last_status": record["last_status"],
        "last_bundle": record["last_bundle"],
        "last_failure": record["last_failure"],
        "total_runs": record["total_runs"],
    }


# --- Retention ----------------------------------------------------------

def list_backups(backup_dir: str) -> list[dict]:
    """Bundles in ``backup_dir``, newest first, with sizes.

    Loophole batch 3 (#10): both ``.vyukbak`` (encrypted, the
    current format) and legacy ``.zip`` (plain, written before
    encryption) are listed; a legacy entry carries
    ``legacy: True`` and ``encrypted: False`` — it cannot be
    verified or restored, only replaced by a new encrypted
    backup. Encrypted entries carry ``legacy: False`` and
    ``encrypted: True``."""
    if not os.path.isdir(backup_dir):
        return []
    out = []
    for name in sorted(os.listdir(backup_dir)):
        if not (name.endswith(BUNDLE_SUFFIX)
                or name.endswith(LEGACY_SUFFIX)):
            continue
        path = os.path.join(backup_dir, name)
        if not os.path.isfile(path):
            continue
        legacy = name.endswith(LEGACY_SUFFIX)
        out.append({"path": path, "name": name,
                    "size_bytes": os.path.getsize(path),
                    "mtime": os.path.getmtime(path),
                    "legacy": legacy, "encrypted": not legacy})
    out.sort(key=lambda item: (item["mtime"], item["name"]), reverse=True)
    return out


def prune_backups(backup_dir: str, max_count: int, max_bytes: int) -> dict:
    """Prune oldest-first until count AND bytes bounds both hold.

    The newest bundle is NEVER pruned, even if it alone exceeds
    ``max_bytes`` — at least one bundle always survives. Returns
    {"pruned": [paths], "kept": [paths], "kept_bytes": int}.
    ``max_count`` < 1 is treated as 1 (the newest survives);
    ``max_bytes`` < 0 is treated as 0 (still keeps the newest).
    """
    bundles = list_backups(backup_dir)
    if not bundles:
        return {"pruned": [], "kept": [], "kept_bytes": 0}
    max_count = max(1, int(max_count))
    max_bytes = max(0, int(max_bytes))
    kept = bundles[:]  # newest first
    pruned: list[str] = []
    while len(kept) > max_count and len(kept) > 1:
        pruned.append(kept.pop()["path"])
    total = sum(item["size_bytes"] for item in kept)
    while total > max_bytes and len(kept) > 1:
        victim = kept.pop()
        total -= victim["size_bytes"]
        pruned.append(victim["path"])
    for path in pruned:
        try:
            os.remove(path)
        except OSError:
            pass  # a failed delete stays listed as kept below
    actually_pruned = [p for p in pruned if not os.path.exists(p)]
    kept_paths = [item["path"] for item in kept]
    kept_paths.extend(p for p in pruned if os.path.exists(p))
    return {"pruned": actually_pruned, "kept": kept_paths,
            "kept_bytes": sum(os.path.getsize(p) for p in kept_paths
                              if os.path.exists(p))}

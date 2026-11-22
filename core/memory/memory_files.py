"""Wave 6 (v5.61) — D-1 ruled memory format: Markdown + frontmatter files.

Delegated ruling (2026-10-25 01:11 IST, "whatever makes it close to
jarvis"): the PLAN-PROPOSED form (plan §6 C-1 / §8 S-8, shape (b)) —
her memories as Markdown+frontmatter files (Obsidian-compatible),
engines demoted to rebuildable indexes — WITH migration + dual-read
+ cap accounting. The unsafe shape — "replace and demote" WITHOUT
migration + dual-read + cap accounting — is forbidden and is NOT
implemented anywhere in this module.

S-8 discipline, as built here:
- Migration: every row of the frozen DB's `facts` table is copied
  to one file per (category, key). The DB is opened READ-ONLY
  (SQLite URI mode=ro) and is never written by this module, so
  rollback is exactly "the previous build reading the untouched
  DB". Migration is idempotent: re-runs skip files whose content
  is already identical, and a receipt file guards repeat work.
- Dual-read period: the prompt path reads BOTH the file store and
  the DB. Reconciliation rule (explicit, the one S-8 demands): per
  (category, key), the NEWEST updated_at wins; on a tie the FILE
  wins. DB-only and file-only records are both included.
- Cap accounting: file records compete inside the SAME 6,000-char
  budget as DB facts (the merge feeds memory.py's existing cap
  loop unchanged). There is no second budget.
- Schema-level ethics (CP-4.7, inside the C-1 ruling): every record
  carries a `kind`. Records of kind `opinion` NEVER render in the
  fact prompt — opinions are retrievable only through an explicit
  kind-filtered query. Records of kind `inferred` carry an
  `expires_at` (created + 90 days); expired inferred traits drop
  out of recall (forgetting-by-design) and are archived, not
  silently kept.

Frontmatter is a flat `key: value` block (own parser — PyYAML is
NOT a declared dependency, so nothing here imports yaml). The
record's value text is the Markdown body, so multi-line values
need no escaping. One file per (category, key), under
`data/memories/<category>/<slug(key)>.md`, mirroring the DB's
UNIQUE(category, key). The episodes journal is NOT migrated: it
is a journal, not a fact store, and keeps riding the summaries
block from the DB. Legacy `session_summary` facts are likewise
left DB-side (they already ride the summaries block).

This module imports stdlib only, plus core.config for DATA_DIR.
It never imports a brain, a tool, or an executor.
"""

import json
import os
import re
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone

from ..config import DATA_DIR
from .. import config_spec

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MEMORIES_DIRNAME = "memories"
MIGRATION_RECEIPT = "migration_receipt.json"
# Loophole batch 6 (#13): the marker for a migration that
# started but never finished — written before the first fact
# moves and removed only after the receipt lands. A later run
# that sees the marker with no receipt knows the previous run
# died mid-way (and re-runs from scratch — re-runs are
# idempotent); the marker itself never changes what reads see.
MIGRATION_IN_PROGRESS = "migration_in_progress.json"
EXPIRED_DIRNAME = "_expired"
INFERRED_TTL_DAYS = 90


def memories_dir(base=None):
    """The file-store root. Tests override it with the
    VYUK_MEMORIES_DIR env var; production uses DATA_DIR/memories.
    Reading never creates it (no side effects on the read path)."""
    override = config_spec.explicit("VYUK_MEMORIES_DIR")
    if override:
        return override
    if base is not None:
        return os.path.join(str(base), MEMORIES_DIRNAME)
    return os.path.join(str(DATA_DIR), MEMORIES_DIRNAME)


def _slug(text):
    slug = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return (slug or "item")[:64]


def fact_filename(category, key):
    return os.path.join(_slug(category), _slug(key) + ".md")


def fact_path(category, key, base=None):
    return os.path.join(memories_dir(base), fact_filename(category, key))


# ---------------------------------------------------------------------------
# Frontmatter (flat scalars only; body = value text)
# ---------------------------------------------------------------------------

# Canonical record fields. `value` lives in the body; everything
# else is frontmatter. `kind` is mandatory (CP-4.7).
FRONTMATTER_FIELDS = (
    "category", "key", "kind", "source", "provenance",
    "created_at", "updated_at", "valid_from", "valid_to",
    "recorded_at", "supersedes", "volatility", "due_date",
    "last_verified", "expires_at",
)

KINDS = ("fact", "opinion", "inferred", "person", "promise")


def normalize_ts(value):
    """Normalise a timestamp to 'YYYY-MM-DD HH:MM:SS' so DB rows
    (space-separated) and file rows (ISO 'T'-separated) compare on
    the same footing. Unparseable values sort as '' (oldest)."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("T", " ").replace("Z", "")
    text = re.split(r"[.+]", text)[0]
    return text[:19] if len(text) >= 19 else text


def normalize_day(value):
    """Normalise a date to 'YYYY-MM-DD' (or '' when absent)."""
    text = str(value or "").strip()[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return ""


def render_record(record):
    """Serialise a record dict to Markdown+frontmatter bytes.
    Deterministic: same record -> same bytes, so migration re-runs
    are byte-identical no-ops."""
    meta = {}
    for field in FRONTMATTER_FIELDS:
        value = record.get(field, "")
        if field in ("created_at", "updated_at", "valid_from",
                     "valid_to", "recorded_at"):
            value = normalize_ts(value)
        if field in ("due_date", "last_verified", "expires_at"):
            value = normalize_day(value)
        meta[field] = "" if value is None else str(value)
    lines = ["---"]
    for field in FRONTMATTER_FIELDS:
        lines.append(f"{field}: {meta[field]}")
    lines.append("---")
    lines.append("")
    lines.append(str(record.get("value", "")).rstrip("\n"))
    lines.append("")
    return ("\n".join(lines)).encode("utf-8")


def parse_record(text):
    """Parse Markdown+frontmatter back to a record dict. Returns
    None when the text is not a well-formed record (missing
    markers, missing category/key) — callers count skips, never
    crash on a bad file."""
    if not isinstance(text, str):
        return None
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None
    lines = text.splitlines()
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    meta = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    if not meta.get("category") or not meta.get("key"):
        return None
    body = "\n".join(lines[end + 1:]).strip("\n")
    record = {field: meta.get(field, "") for field in FRONTMATTER_FIELDS}
    record["value"] = body
    if record["kind"] not in KINDS:
        record["kind"] = "fact"
    return record


# ---------------------------------------------------------------------------
# Store primitives
# ---------------------------------------------------------------------------

def write_fact(record, base=None):
    """Write one record. Skips the write when the bytes are already
    identical (migration re-runs are no-ops, mtimes preserved).
    Returns (path, wrote_bool)."""
    path = fact_path(record.get("category", ""), record.get("key", ""),
                     base=base)
    data = render_record(record)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "rb") as handle:
            if handle.read() == data:
                return path, False
    except OSError:
        pass
    tmp = None
    # F-01 audit: mkstemp-unique temp file, not the old fixed
    # "<name>.tmp" — two concurrent writes of the same path could
    # otherwise interleave in one shared temp file.
    try:
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".",
                                   dir=os.path.dirname(path))
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    return path, True


def read_fact(category, key, base=None):
    try:
        with open(fact_path(category, key, base=base),
                  "r", encoding="utf-8") as handle:
            return parse_record(handle.read())
    except (OSError, UnicodeDecodeError):
        return None


def list_facts(base=None, kinds=None):
    """All well-formed records in the store. The _expired archive
    dir is never listed (expired traits are out of recall).
    Returns (records, skipped_count)."""
    root = memories_dir(base)
    records = []
    skipped = 0
    if not os.path.isdir(root):
        return records, skipped
    for dirpath, dirnames, filenames in os.walk(root):
        if EXPIRED_DIRNAME in dirnames:
            dirnames.remove(EXPIRED_DIRNAME)
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            try:
                with open(os.path.join(dirpath, name),
                          "r", encoding="utf-8") as handle:
                    record = parse_record(handle.read())
            except (OSError, UnicodeDecodeError):
                record = None
            if record is None:
                skipped += 1
                continue
            if kinds is not None and record["kind"] not in kinds:
                continue
            records.append(record)
    return records, skipped


def delete_fact(category, key, base=None):
    try:
        os.remove(fact_path(category, key, base=base))
        return True
    except OSError:
        return False


def file_store_has_records(base=None):
    root = memories_dir(base)
    if not os.path.isdir(root):
        return False
    for _dirpath, _dirnames, filenames in os.walk(root):
        if any(name.endswith(".md") for name in filenames):
            return True
    return False


# ---------------------------------------------------------------------------
# Migration (S-8): DB -> files, DB untouched, idempotent
# ---------------------------------------------------------------------------

def migration_receipt_path(base=None):
    return os.path.join(memories_dir(base), MIGRATION_RECEIPT)


def migration_receipt(base=None):
    try:
        with open(migration_receipt_path(base),
                  "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _in_progress_path(base=None):
    return os.path.join(memories_dir(base), MIGRATION_IN_PROGRESS)


def _write_in_progress(db_path, db_rows_seen, base=None):
    """Atomic marker write (tmp + os.replace): a crash mid-write
    leaves either the old marker or the new one, never a torn one."""
    root = memories_dir(base)
    os.makedirs(root, exist_ok=True)
    path = _in_progress_path(base)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"started_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
            "db_path": str(db_path), "db_rows_seen": db_rows_seen},
            handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def migrate_from_db(db_path, base=None):
    """Copy the frozen DB's facts table to files. The DB connection
    is READ-ONLY (SQLite URI mode=ro): this function cannot write
    to the DB even by accident, so rollback stays exactly "the
    previous build reading the untouched DB". Legacy
    `session_summary` facts are NOT migrated (they ride the
    summaries block from the DB, unchanged).

    Loophole batch 6 (#13) — atomicity, three parts:
    * An in-progress marker is written before the first fact
      moves and removed only after the receipt lands, so a
      crash mid-migration is visible (marker, no receipt) and
      the next run re-runs from scratch instead of trusting a
      half-migrated store.
    * A file record NEWER than its DB row is never regressed:
      when the file's updated_at is newer than the DB row's,
      the DB value is skipped (counted as skipped_stale) —
      the file wins, the same rule dual_read already applies.
    * The receipt is written only after a verification pass
      re-reads the file store and confirms every migrated row
      is present with the expected value. A failed verification
      raises (no receipt), so the next run retries instead of
      trusting the migration. Returns a receipt dict and writes
      it to the store root."""
    root = memories_dir(base)
    os.makedirs(root, exist_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, category, key, value, created_at, updated_at "
            "FROM facts ORDER BY id").fetchall()
    finally:
        conn.close()
    _write_in_progress(db_path, len(rows), base=base)
    try:
        migrated = 0
        skipped_existing = 0
        skipped_summary = 0
        skipped_stale = 0
        expected: dict[tuple[str, str], str] = {}
        for row in rows:
            category = str(row["category"] or "")
            key = str(row["key"] or "")
            if category.lower() == "session_summary":
                skipped_summary += 1
                continue
            identity = (category.strip().lower(), key.strip())
            existing = read_fact(category, key, base=base)
            if existing is not None and existing.get("value", "") == \
                    str(row["value"] or ""):
                skipped_existing += 1
                expected[identity] = str(row["value"] or "")
                continue
            if existing is not None:
                # Never regress a newer file record with a stale
                # DB value: the file wins, exactly as dual_read
                # already decides when both hold the same key.
                file_ts = normalize_ts(existing.get("updated_at", ""))
                db_ts = normalize_ts(row["updated_at"] or "")
                if file_ts and db_ts and file_ts > db_ts:
                    skipped_stale += 1
                    expected[identity] = str(
                        existing.get("value", ""))
                    continue
            record = {
                "category": category, "key": key,
                "kind": "fact", "source": "db_migration",
                "provenance": f"db facts id {row['id']}",
                "created_at": row["created_at"] or "",
                "updated_at": row["updated_at"] or "",
                "valid_from": "", "valid_to": "",
                "recorded_at": row["created_at"] or "",
                "supersedes": "", "volatility": "0.3",
                "due_date": "", "last_verified": "",
                "expires_at": "",
                "value": str(row["value"] or ""),
            }
            write_fact(record, base=base)
            migrated += 1
            expected[identity] = str(row["value"] or "")
        # Verification pass: every row the receipt will claim
        # must be present in the file store with the expected
        # value (the DB value, or the newer file value for
        # skipped-stale rows). A failed check raises — no
        # receipt — so the next run retries instead of trusting
        # a half-migrated store.
        verified_count = 0
        for identity, want in expected.items():
            got = read_fact(identity[0], identity[1], base=base)
            if got is None or str(got.get("value", "")) != want:
                raise RuntimeError(
                    "migration verification failed for "
                    f"{identity[0]}/{identity[1]}: expected value "
                    "not present in the file store")
            verified_count += 1
        receipt = {
            "migrated": migrated,
            "skipped_existing": skipped_existing,
            "skipped_session_summary": skipped_summary,
            "skipped_stale": skipped_stale,
            "db_rows_seen": len(rows),
            "verified": True,
            "verified_count": verified_count,
        }
        with open(migration_receipt_path(base), "w",
                  encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=1, sort_keys=True)
        try:
            os.unlink(_in_progress_path(base))
        except OSError:
            pass
        return receipt
    except Exception:
        # The in-progress marker stays: it names the interrupted
        # run for the next attempt (and for anyone reading the
        # store). No receipt is written, so the next run retries.
        raise


def migrate_if_needed(db_path, base=None):
    """Lazy-once migration for the prompt path: runs only when no
    receipt exists yet. Failure-safe — any exception is swallowed
    and None returned; the caller falls back to DB-only reading.

    Loophole batch 6 (#13): the failure-safety used to live only
    in the one caller (_dual_read_facts' try/except) while this
    docstring promised it here. The promise now holds at the
    seam: a raising migration can never break a caller that
    trusted this function."""
    try:
        if migration_receipt(base) is not None:
            return None
        if not os.path.exists(str(db_path)):
            return None
        return migrate_from_db(db_path, base=base)
    except Exception:  # noqa: BLE001 — failure-safe by contract
        return None


# ---------------------------------------------------------------------------
# Dual-read (S-8): union + reconciliation + ethics filters
# ---------------------------------------------------------------------------

def _file_as_db_shape(record):
    """A file record in the DB-fact dict shape memory.py's prompt
    code already consumes (id/category/key/value/updated_at)."""
    return {
        "id": 0,
        "category": record["category"],
        "key": record["key"],
        "value": record["value"],
        "updated_at": normalize_ts(record.get("updated_at", "")),
        "_from_file": True,
        "_kind": record["kind"],
    }


def is_expired_inferred(record, today=None):
    """Forgetting-by-design: an inferred record past its expires_at
    (created + 90 days) is out of recall."""
    if record.get("kind") != "inferred":
        return False
    expires = normalize_day(record.get("expires_at", ""))
    if not expires:
        return False
    today = today or date.today().isoformat()
    return expires < today


def dual_read(db_facts, base=None, today=None):
    """Merge DB facts with file records under the S-8
    reconciliation rule: per (category, key), newest updated_at
    wins; tie -> file wins. DB-only and file-only records are
    included. Opinions NEVER enter the merged prompt set (CP-4.7)
    and expired inferred traits NEVER enter it (forgetting-by-
    design) — both stay retrievable via list_facts(kind=...)."""
    file_records, _skipped = list_facts(base=base)
    merged = {}
    for fact in db_facts:
        identity = (str(fact.get("category") or "").strip().lower(),
                    str(fact.get("key") or "").strip())
        merged[identity] = (normalize_ts(fact.get("updated_at", "")),
                            False, dict(fact))
    for record in file_records:
        if record["kind"] == "opinion":
            continue
        if is_expired_inferred(record, today=today):
            continue
        identity = (record["category"].strip().lower(),
                    record["key"].strip())
        shaped = _file_as_db_shape(record)
        stamp = normalize_ts(record.get("updated_at", ""))
        previous = merged.get(identity)
        if previous is None or stamp >= previous[0]:
            merged[identity] = (stamp, True, shaped)
    return [entry[2] for entry in merged.values()]


def new_inferred_record(category, key, value, created=None):
    """Build an inferred-trait record with its 90-day expiry
    already set (forgetting-by-design is a write-time property,
    not a cleanup afterthought)."""
    created_day = (created or date.today()).isoformat() \
        if not isinstance(created, str) else created[:10]
    try:
        expires = (date.fromisoformat(created_day)
                   + timedelta(days=INFERRED_TTL_DAYS)).isoformat()
    except ValueError:
        expires = ""
    return {
        "category": category, "key": key, "kind": "inferred",
        "source": "inferred", "provenance": "",
        "created_at": created_day, "updated_at": created_day,
        "valid_from": "", "valid_to": "", "recorded_at": created_day,
        "supersedes": "", "volatility": "0.7",
        "due_date": "", "last_verified": "",
        "expires_at": expires, "value": value,
    }


def sweep_expired(base=None, today=None):
    """Archive expired inferred records out of recall: moved to
    the _expired dir (never listed, never prompted), NOT destroyed
    — PF-3 wipe is the destruction path, and it has its own
    confirm gate. Returns the archived filenames."""
    root = memories_dir(base)
    records, _skipped = list_facts(base=base)
    archived = []
    today = today or date.today().isoformat()
    for record in records:
        if not is_expired_inferred(record, today=today):
            continue
        src = fact_path(record["category"], record["key"], base=base)
        dest_dir = os.path.join(root, EXPIRED_DIRNAME)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(src))
        try:
            os.replace(src, dest)
            archived.append(os.path.basename(src))
        except OSError:
            continue
    return archived

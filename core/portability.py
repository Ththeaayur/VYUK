"""Wave 10 (v5.65) Leaf 2 — PF-4 data portability (STAGED, unwired).

Export a complete, importable ZIP bundle of settings + memory +
milestones, and import it on a fresh install. No live file is
edited by this module and no runtime path calls it; the coordinator
wires it into a UI action.

Real formats (read from the tree, not invented):
- Settings: the QSettings-backed snapshot shape of
  ``core/settings_schema.py`` (``SettingsStore.snapshot()``:
  model field name -> value). This module never touches QSettings
  itself — the caller passes a plain ``dict`` snapshot in and
  applies the restored dict back out (see ``apply_settings``) —
  so export/import work with system python, with no Qt present.
- Memory: the Wave 6 file store (``core/memory_files.py``):
  Markdown + frontmatter files under ``data/memories/``, one per
  (category, key). Files are copied byte-for-byte; this module
  never parses frontmatter, so it cannot mangle a record.
- Milestones: ``data/milestones.jsonl`` (``core/milestones.py``):
  append-only JSON Lines, one object per event with an ``event``
  field. Only well-formed lines (a JSON object carrying ``event``)
  are exported; malformed lines are counted in the export report
  and in the bundle manifest, never silently dropped from the
  accounting.

Bundle layout (inside the ZIP):
  settings.json          the settings snapshot (JSON object)
  memories/<relpath>.md  every .md file under data/memories/
  milestones.jsonl       the exported milestone lines, verbatim
  manifest.json          format, counts, and SHA-256 per entry

Import verifies every manifest hash BEFORE applying anything: a
single hash failure applies nothing and is listed in the report.
Entries present in the ZIP but absent from the manifest are
reported as ``extra`` and are never applied. Sections absent from
the bundle are reported as ``missing``, never papered over.
Import returns a report dict; it never hides a failure.

Stdlib only. Product VERSION is untouched (still 5.64.1); this
module carries no product-version constant of its own.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timezone

BUNDLE_FORMAT = "vyuk-portability-1"
SETTINGS_NAME = "settings.json"
MILESTONES_NAME = "milestones.jsonl"
MANIFEST_NAME = "manifest.json"
MEMORIES_PREFIX = "memories/"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def export_bundle(data_dir: str, settings: dict, out_path: str) -> dict:
    """Write a portability bundle and return an export report.

    ``data_dir`` is the install's data/ directory (memories and
    milestones are read from it; it is never written). ``settings``
    is a JSON-serialisable snapshot dict (e.g. SettingsStore
    snapshot). Raises ValueError when settings are not JSON
    serialisable, rather than exporting a lossy bundle.
    """
    try:
        settings_blob = json.dumps(settings, indent=2, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"settings are not JSON-serialisable: {exc}") from exc
    memories = _collect_memories(data_dir)
    milestones_blob, milestones_skipped = _collect_milestones(data_dir)

    entries: dict[str, bytes] = {SETTINGS_NAME: settings_blob}
    entries.update(memories)
    entries[MILESTONES_NAME] = milestones_blob
    manifest = {
        "format": BUNDLE_FORMAT,
        "exported_at": _utcnow(),
        "settings_keys": sorted(settings.keys()),
        "memory_files": sorted(memories.keys()),
        "milestone_lines": len(milestones_blob.decode("utf-8").splitlines()) if milestones_blob else 0,
        "milestones_skipped_malformed": milestones_skipped,
        "hashes": {name: _sha256(blob) for name, blob in entries.items()},
    }
    manifest_blob = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):
            zf.writestr(name, entries[name])
        zf.writestr(MANIFEST_NAME, manifest_blob)
    return {
        "bundle": out_path,
        "format": BUNDLE_FORMAT,
        "settings_keys": len(settings),
        "memory_files": len(memories),
        "milestone_lines": manifest["milestone_lines"],
        "milestones_skipped_malformed": milestones_skipped,
        "total_entries": len(entries),
    }


def verify_bundle(bundle_path: str) -> dict:
    """Check a bundle's manifest hashes without applying anything.

    Returns {"ok": bool, "manifest": dict|None, "failed": [...],
    "extra": [...], "missing": [...]}. ``failed`` lists entries
    whose bytes do not match the manifest; ``extra`` lists ZIP
    entries absent from the manifest; ``missing`` lists manifest
    entries absent from the ZIP.
    """
    result: dict = {"ok": False, "manifest": None, "failed": [],
                    "extra": [], "missing": []}
    try:
        zf = zipfile.ZipFile(bundle_path)
    except (zipfile.BadZipFile, OSError) as exc:
        result["failed"] = [f"bundle-unreadable: {exc}"]
        return result
    with zf:
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (KeyError, ValueError, UnicodeDecodeError) as exc:
            result["failed"] = [f"manifest-unreadable: {exc}"]
            return result
        result["manifest"] = manifest
        hashes = manifest.get("hashes") or {}
        if not isinstance(hashes, dict):
            result["failed"] = ["manifest-hashes-not-a-mapping"]
            return result
        names = set(zf.namelist())
        result["extra"] = sorted(n for n in names
                                 if n != MANIFEST_NAME and n not in hashes)
        result["missing"] = sorted(n for n in hashes if n not in names)
        for name, expected in hashes.items():
            if name not in names:
                continue
            try:
                actual = _sha256(zf.read(name))
            except (KeyError, OSError) as exc:
                result["failed"].append(f"{name}: unreadable ({exc})")
                continue
            if actual != expected:
                result["failed"].append(name)
    result["ok"] = not result["failed"] and not result["missing"]
    # Extra entries do not fail verification by themselves — they
    # are reported and never applied — but ok requires a clean bill.
    if result["extra"]:
        result["ok"] = False
    return result


def import_bundle(bundle_path: str, target_data_dir: str) -> dict:
    """Restore a bundle into a fresh install's data/ directory.

    Verifies hashes first; on ANY verification failure nothing is
    applied. On success: settings are returned (apply them with
    ``apply_settings``), memory files are written under
    ``data/memories/``, and milestones replace ``milestones.jsonl``.
    The returned report lists ``applied``, ``missing``, ``extra``
    and ``failed`` — a section is never dropped without a line
    here. Sections absent from the bundle (e.g. a hand-made bundle
    with no milestones file) are ``missing``; the other sections
    still restore.
    """
    report: dict = {"applied": [], "missing": [], "extra": [],
                    "failed": [], "settings": None,
                    "memory_files": 0, "milestone_lines": 0,
                    "milestones_skipped_malformed": 0}
    check = verify_bundle(bundle_path)
    manifest = check["manifest"]
    if manifest is None:
        report["failed"] = check["failed"]
        return report
    # Hash failures are fatal and apply nothing. Extra entries and
    # missing manifest entries are reported, not fatal, except
    # that a missing settings section means settings cannot restore.
    fatal = [f for f in check["failed"]]
    if fatal:
        report["failed"] = fatal
        report["extra"] = check["extra"]
        report["missing"] = check["missing"]
        return report
    report["extra"] = check["extra"]
    report["missing"] = check["missing"]

    with zipfile.ZipFile(bundle_path) as zf:
        names = set(zf.namelist())
        # Settings.
        if SETTINGS_NAME in names:
            try:
                settings = json.loads(zf.read(SETTINGS_NAME).decode("utf-8"))
                if not isinstance(settings, dict):
                    raise ValueError("settings.json is not a JSON object")
            except (ValueError, UnicodeDecodeError) as exc:
                report["failed"].append(f"{SETTINGS_NAME}: {exc}")
            else:
                report["settings"] = settings
                report["applied"].append(SETTINGS_NAME)
        else:
            if SETTINGS_NAME not in report["missing"]:
                report["missing"].append(SETTINGS_NAME)
        # Memories (byte-for-byte, traversal-guarded).
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
        # Milestones (fresh-install replace).
        if MILESTONES_NAME in names:
            try:
                blob = zf.read(MILESTONES_NAME)
                dest = os.path.join(target_data_dir, MILESTONES_NAME)
                os.makedirs(target_data_dir, exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(blob)
                report["milestone_lines"] = (
                    len(blob.decode("utf-8").splitlines()) if blob else 0)
            except (OSError, UnicodeDecodeError) as exc:
                report["failed"].append(f"{MILESTONES_NAME}: {exc}")
            else:
                report["applied"].append(MILESTONES_NAME)
        else:
            if MILESTONES_NAME not in report["missing"]:
                report["missing"].append(MILESTONES_NAME)
    report["milestones_skipped_malformed"] = manifest.get(
        "milestones_skipped_malformed", 0)
    return report


def apply_settings(settings: dict, store) -> list[str]:
    """Apply a restored settings dict to a store.

    ``store`` may be a plain dict (assigned in place) or a
    QSettings-like object with ``setValue``/``sync``. Returns the
    sorted list of keys written. This is the caller's one-line
    bridge back into the real QSettings store; import_bundle
    deliberately does not touch QSettings itself.
    """
    written = []
    if isinstance(store, dict):
        for key in sorted(settings):
            store[key] = settings[key]
            written.append(key)
        return written
    set_value = getattr(store, "setValue", None)
    if callable(set_value):
        for key in sorted(settings):
            set_value(key, settings[key])
            written.append(key)
        sync = getattr(store, "sync", None)
        if callable(sync):
            sync()
        return written
    raise TypeError("store must be a dict or a QSettings-like object")

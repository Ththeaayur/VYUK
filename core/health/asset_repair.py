"""Wave 10 (v5.65) — Asset self-repair, Doctor/installer leg only (EY-H).

Verifies installed tree files against ``install_manifest.json`` hashes
(SHA-256, ``{"files": {relative_path: hex_digest}}`` — the format the
tree already ships) and, when asked, repairs mismatched or missing
files from a pristine source supplied by the caller.

NEVER RUN AT STARTUP. This module has NO startup hook, NO import-time
side effect, and NO registration of any kind: it is a plain set of
functions that only Doctor/installer code paths may call, explicitly,
after the user (or the installer) has chosen repair. Importing this
module does nothing but define functions. It never touches the tree
on import, never schedules anything, and never phones home.

Repair sources — and only these:
* a pristine DIRECTORY supplied by the caller (the directory is read,
  never written), or
* a pristine ZIP path supplied by the caller (the canonical install
  ZIP, read, never modified).

Repair NEVER fabricates bytes and NEVER downloads anything. A file
whose pristine copy is absent, or whose pristine copy does not hash
to the manifest value, is reported as UNREPAIRABLE with its reason —
never silently skipped, never filled with invented content. Every
repaired file is re-hashed after writing and only counts as repaired
if it then matches the manifest.

Safety rules:
* Manifest paths must be relative and must stay inside the tree;
  absolute paths and ``..`` escapes are rejected outright.
* Repair writes ONLY files that verify reported as missing or
  mismatched, and only inside the tree. Files that already match are
  never touched.
* An existing symlink at a repair target is refused (repairing
  through a symlink could write outside the tree) and reported as
  unrepairable.
* The manifest itself (``install_manifest.json``) is verified like
  any other file but is never repaired from a pristine source by
  this module: a damaged manifest means the repair basis itself is
  untrustworthy, and the caller must re-acquire it. It is reported
  as unrepairable with reason ``manifest-itself``.

Stdlib only. No network, no new dependency.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Mismatch",
    "Unrepairable",
    "VerifyReport",
    "RepairReport",
    "load_manifest",
    "verify_tree",
    "repair_tree",
]

_MANIFEST_NAME = "install_manifest.json"


@dataclass(frozen=True)
class Mismatch:
    """One manifest file that is missing or differs from the tree."""

    file: str  # manifest-relative path, forward slashes
    expected: str  # manifest SHA-256 hex digest
    actual: str  # tree SHA-256 hex digest, or "missing"


@dataclass(frozen=True)
class Unrepairable:
    file: str
    reason: str  # e.g. not-in-pristine-source, pristine-hash-mismatch


@dataclass
class VerifyReport:
    checked: int
    ok: int
    mismatches: list = field(default_factory=list)

    @property
    def all_ok(self):
        return not self.mismatches


@dataclass
class RepairReport:
    repaired: list = field(default_factory=list)  # file paths
    unrepairable: list = field(default_factory=list)  # Unrepairable
    untouched_ok: int = 0


def _safe_relpath(raw):
    """Validate a manifest path. Returns a normalised forward-slash
    relative path, or None if it escapes the tree / is absolute."""
    if not isinstance(raw, str) or not raw:
        return None
    if raw.startswith("/") or raw.startswith("\\"):
        return None
    parts = raw.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 64), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(manifest):
    """Load a manifest from a path or an already-parsed dict. Returns
    ``{relative_path: sha256_hex}``. Raises ValueError on any malformed
    manifest — repair must never proceed on a basis it cannot trust."""
    if isinstance(manifest, (str, Path)):
        try:
            import json
            with open(manifest, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("unreadable-manifest: %s" % exc)
    else:
        data = manifest
    if not isinstance(data, dict):
        raise ValueError("manifest-not-an-object")
    files = data.get("files")
    if not isinstance(files, dict):
        raise ValueError("manifest-missing-files-map")
    out = {}
    for raw_path, digest in files.items():
        rel = _safe_relpath(raw_path)
        if rel is None:
            raise ValueError("manifest-unsafe-path:%s" % raw_path)
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("manifest-bad-digest:%s" % raw_path)
        try:
            int(digest, 16)
        except ValueError:
            raise ValueError("manifest-bad-digest:%s" % raw_path)
        out[rel] = digest.lower()
    return out


def verify_tree(tree_dir, manifest):
    """Compare every manifest file against the tree. Returns a
    VerifyReport listing each missing/mismatched file with its
    expected and actual hashes. Read-only: never writes anything."""
    tree = Path(tree_dir)
    entries = load_manifest(manifest)
    mismatches = []
    ok = 0
    for rel in sorted(entries):
        target = tree.joinpath(*rel.split("/"))
        if not target.is_file():
            mismatches.append(Mismatch(rel, entries[rel], "missing"))
        elif target.is_symlink():
            # A symlink is not the manifest file itself; report its
            # target's hash only if it resolves to a real file, else
            # missing. Repair refuses symlinks outright (see below).
            try:
                actual = _sha256_file(target)
            except OSError:
                actual = "missing"
            if actual == "missing":
                mismatches.append(Mismatch(rel, entries[rel], "missing"))
            elif actual != entries[rel]:
                mismatches.append(Mismatch(rel, entries[rel], actual))
            else:
                ok += 1
        else:
            try:
                actual = _sha256_file(target)
            except OSError:
                actual = "missing"
            if actual == entries[rel]:
                ok += 1
            else:
                mismatches.append(Mismatch(rel, entries[rel], actual))
    return VerifyReport(checked=len(entries), ok=ok, mismatches=mismatches)


def _pristine_bytes(pristine_source, rel):
    """Read one file's bytes from the caller-supplied pristine source.
    Returns bytes, or None when the source does not carry the file.
    Raises ValueError when the source itself is unusable."""
    source = Path(pristine_source)
    if source.is_dir():
        candidate = source.joinpath(*rel.split("/"))
        if not candidate.is_file() or candidate.is_symlink():
            return None
        try:
            return candidate.read_bytes()
        except OSError:
            return None
    if source.is_file() and source.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source, "r") as archive:
                try:
                    info = archive.getinfo(rel)
                except KeyError:
                    return None
                if info.is_dir():
                    return None
                return archive.read(info)
        except zipfile.BadZipFile as exc:
            raise ValueError("bad-pristine-zip: %s" % exc)
    raise ValueError("pristine-source-not-a-dir-or-zip: %s" % pristine_source)


def repair_tree(tree_dir, manifest, pristine_source):
    """Repair every missing/mismatched manifest file from the
    caller-supplied pristine source (directory or ZIP path). Returns a
    RepairReport: ``repaired`` lists files restored and re-verified,
    ``unrepairable`` lists every file that could not be restored with
    its reason. Never downloads, never fabricates bytes, never runs
    at startup — call only from Doctor/installer paths."""
    tree = Path(tree_dir)
    entries = load_manifest(manifest)
    report = verify_tree(tree, manifest)
    out = RepairReport(untouched_ok=report.ok)
    for mismatch in report.mismatches:
        rel = mismatch.file
        if rel == _MANIFEST_NAME:
            out.unrepairable.append(
                Unrepairable(rel, "manifest-itself"))
            continue
        target = tree.joinpath(*rel.split("/"))
        if target.is_symlink():
            out.unrepairable.append(
                Unrepairable(rel, "symlink-refused"))
            continue
        try:
            data = _pristine_bytes(pristine_source, rel)
        except ValueError as exc:
            out.unrepairable.append(Unrepairable(rel, str(exc)))
            continue
        if data is None:
            out.unrepairable.append(
                Unrepairable(rel, "not-in-pristine-source"))
            continue
        if hashlib.sha256(data).hexdigest() != entries[rel]:
            out.unrepairable.append(
                Unrepairable(rel, "pristine-hash-mismatch"))
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if _sha256_file(target) != entries[rel]:
                raise OSError("post-write hash mismatch")
        except OSError as exc:
            out.unrepairable.append(
                Unrepairable(rel, "write-failed:%s" % exc))
            continue
        out.repaired.append(rel)
    return out

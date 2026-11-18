"""Download + stage — split from updater.py (B24)."""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from ..version import VERSION, parse_version, version_newer
from .updater_paths import _DOWNLOAD_TIMEOUT_S, _log, _marker_path, _now_iso, _package_root, _sha256_of, _staging_dir  # INTENTIONAL: shared helpers/constants
from .updater_policy import _policy_refusal  # INTENTIONAL: shared helper
from .updater_protect import _is_protected_rel  # INTENTIONAL: shared helper
from .updater_transport import _opener_for, _transport_error  # INTENTIONAL: shared helpers


# ---------------------------------------------------------------------------
# download_and_stage()
# ---------------------------------------------------------------------------


def _download_resumable(url: str, dest: Path) -> str | None:
    """Streams `url` to `dest` via a sibling ".part" file, following
    local_assets' resume discipline. Returns None on success (dest
    promoted into place) or an error string; transport failures KEEP
    the .part so the next attempt resumes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in (0, 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": f"VYUK-Updater/{VERSION}"}
        if have > 0:
            headers["Range"] = f"bytes={have}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with _opener_for(url).open(
                    request, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
                status = getattr(resp, "status", 200)
                if have > 0 and status == 200:
                    have = 0  # server ignored the range: restart
                mode = "ab" if (have > 0 and status == 206) else "wb"
                length = resp.headers.get("Content-Length")
                expected = None
                if length is not None:
                    try:
                        expected = have + int(length) \
                            if mode == "ab" else int(length)
                    except ValueError:
                        expected = None
                with open(part, mode) as fh:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and attempt == 0 and part.exists():
                part.unlink()  # range unsatisfiable: start fresh once
                continue
            return f"download failed (HTTP {exc.code})"
        except Exception as exc:  # noqa: BLE001
            return f"download failed: {exc}"[:300]
        got = part.stat().st_size if part.exists() else 0
        if expected is not None and got != expected:
            return (f"download incomplete ({got} of {expected} "
                    f"bytes) — the partial file is kept and the "
                    f"next attempt resumes")
        os.replace(part, dest)
        return None
    return "download failed"


def _extract_staged(zip_path: Path, version: str) -> dict:
    """Extracts the release zip into the staging tree and builds the
    per-file inventory. Returns {"files": {relpath: sha256}} or
    {"error": message}. Zip-slip guarded; a data/ directory inside
    the zip is never staged (user data is not update payload)."""
    tree = _staging_dir() / version
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True, exist_ok=True)
    root_name = _package_root().name
    try:
        archive = zipfile.ZipFile(zip_path)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"update download is not a readable zip: {exc}"}
    with archive:
        names = [n for n in archive.namelist() if n and not n.endswith("/")]
        # Release zips wrap the tree in a single top folder named
        # after the package (voice_assistant/…); descend into it when
        # present so the staged tree IS the package tree.
        tops = {n.split("/")[0] for n in names}
        prefix = ""
        if len(tops) == 1 and tops.pop() == root_name:
            prefix = root_name + "/"
        # Gate B (P0-3): a payload carrying a credential path is
        # refused WHOLE, before anything is written to staging.
        # (data/ entries are skipped below, as before — user data
        # is never update payload, and skipping it is safe.)
        for name in names:
            rel = name[len(prefix):] if prefix else name
            if rel and _is_protected_rel(rel):
                return {"error": f"update zip contains a protected "
                                 f"credential path: {rel!r} — refusing "
                                 f"the update so your .env and tokens "
                                 f"can never be overwritten by it"}
        inventory: dict[str, str] = {}
        resolved_tree = tree.resolve()
        for name in names:
            rel = name[len(prefix):] if prefix else name
            if not rel or rel.split("/")[0] == "data":
                continue
            target = (tree / rel).resolve()
            if target != resolved_tree and \
                    resolved_tree not in target.parents:
                return {"error": f"update zip escapes its staging "
                                 f"directory: {name!r}"}
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(name) as src, \
                        open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            except Exception as exc:  # noqa: BLE001
                return {"error": f"couldn't extract {name!r}: {exc}"}
            inventory[Path(rel).as_posix()] = _sha256_of(target)
    if not inventory:
        return {"error": "update zip contained no package files"}
    return {"files": inventory}


def download_and_stage(info: dict) -> dict:
    """Downloads the update described by a check() "available"
    result, verifies it, and stages it for the next startup's
    apply_staged(). Never touches live files. Never raises.

    States: "staged", "integrity" (sha256 mismatch — the corrupt
    download is deleted, distinctly from a transport failure),
    "error" (anything else, message included).
    """
    try:
        return _download_and_stage(info)
    except Exception as exc:  # noqa: BLE001 — report, never raise
        return {"state": "error", "message": f"staging failed: {exc}"[:300]}


def _download_and_stage(info: dict) -> dict:
    if not isinstance(info, dict):
        return {"state": "error", "message": "no update info to stage"}
    # Gate C (P2-2): the policy gate sits BEFORE any download
    # — a paused download never opens a socket, regardless of
    # whether the update info itself is valid.
    policy_refusal = _policy_refusal("download")
    if policy_refusal is not None:
        return {"state": "error", "message": policy_refusal}
    version = info.get("version")
    if parse_version(version) is None:
        return {"state": "error",
                "message": f"update info has no usable version "
                           f"({version!r})"}
    if not version_newer(version, VERSION):
        return {"state": "error",
                "message": f"update {version} is not newer than the "
                           f"running build ({VERSION})"}
    min_version = info.get("min_version")
    if min_version is not None and \
            version_newer(min_version, VERSION):
        return {"state": "error",
                "message": f"this update needs VYUK {min_version} or "
                           f"newer first (running {VERSION})"}
    url = (info.get("url") or "").strip()
    refusal = _transport_error(url) if url else "update info names no URL"
    if refusal is not None:
        return {"state": "error", "message": refusal}
    expected_sha = (info.get("sha256") or "").strip().lower()
    if len(expected_sha) != 64:
        return {"state": "error",
                "message": "update info has no valid sha256 — "
                           "refusing to download an unverifiable file"}
    download_dir = _staging_dir() / "download"
    dest = download_dir / f"nova-update-{version}.zip"
    failure = _download_resumable(url, dest)
    if failure is not None:
        _log(f"update {version}: {failure}")
        return {"state": "error", "message": failure}
    actual_sha = _sha256_of(dest)
    if actual_sha != expected_sha:
        # The v4.2 rule: bytes that failed verification are DELETED,
        # never kept for resume — they can never become valid.
        dest.unlink(missing_ok=True)
        _log(f"update {version}: sha256 mismatch — download deleted")
        return {"state": "integrity",
                "message": "the downloaded update failed its SHA-256 "
                           "integrity check and was deleted; "
                           "nothing was staged"}
    extracted = _extract_staged(dest, version)
    if "error" in extracted:
        _log(f"update {version}: {extracted['error']}")
        return {"state": "error", "message": extracted["error"]}
    marker = {
        "version": version,
        "sha256": actual_sha,
        "notes": str(info.get("notes") or ""),
        "min_version": min_version,
        "staged_at": _now_iso(),
        "files": extracted["files"],
    }
    marker_path = _marker_path()
    tmp = marker_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    os.replace(tmp, marker_path)
    _log(f"update {version} staged "
         f"({len(extracted['files'])} files, sha256 verified)")
    return {"state": "staged", "version": version,
            "files": len(extracted["files"])}

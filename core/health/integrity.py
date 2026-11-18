"""
Install-tree integrity for VYUK (v5.47, programme item 10,
B-T1.4 + C5) — the startup half of the self-protection item.

The write-deny in core/funnel/tools_gate_paths.py stops the running app from
MODIFYING its own program folders at runtime. This module covers
the other direction: detecting, at startup, that the program
files on disk are not the ones that were shipped — edited by
something else between installs, corrupted, partially extracted,
or tampered with.

The mechanism is a signed manifest at the package root:

  * install_manifest.json maps every shipped *.py file
    (POSIX relative path -> SHA-256 hex), in the canonical
    serialisation produced by manifest_bytes() below. The
    embedded web view is gone (world-view feature removed
    2026-11-05), so no asset directory is covered: a manifest
    that covered only *.py is the whole shipped surface now.
  * install_manifest.json.sig is the detached Ed25519 signature
    (base64 of the 64-byte signature, the update-feed format) over
    the manifest file's EXACT bytes, made with the same offline
    release key that signs the update feed and the shipped plugin
    manifest. It is generated at packaging time by
    packaging/gen_install_manifest.py — never by the app, and
    never at runtime: a manifest the app wrote itself would prove
    nothing.
  * verify_install_tree() verifies that signature against the
    public key pinned in core/health/updater.py
    (PINNED_UPDATE_PUBLIC_KEY, imported the same acyclic way
    core/plugin_loader.py imports it — item 15's precedent), then
    re-hashes the live tree and compares it with the manifest.

Posture, deliberately: DETECT AND REPORT, never block. A failed
check appends one loud line per problem to data/activity.log and
prints to stderr (check_and_log), the doctor surfaces it as a
WARN, and VYUK starts anyway. The tree this check guards is also
legitimately edited by developers in a source checkout, and a
bricking failure here would punish exactly the people able to fix
a bad install. Detection is the contract; enforcement lives in
the runtime write-deny and in the signed update/plugin paths.

Nothing in this module raises across its public API: a broken
integrity check must never stop VYUK from starting (the main.py
hook is guarded too, but the module keeps the same never-raise
discipline as core/health/updater.py on its own).
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

MANIFEST_NAME = "install_manifest.json"
MANIFEST_SIG_NAME = MANIFEST_NAME + ".sig"
MANIFEST_FORMAT = 1

# ------------------------------------------------------------------
# Ed25519 verification (v5.47, programme item 10).
#
# The verifier is core/health/updater.py's pure-Python RFC 8032
# implementation — the same code that authenticates the update
# feed, with the same pinned public key: ONE offline release key
# signs the feed manifest, the shipped plugin manifest and this
# install manifest (packaging/sign_manifest.py +
# packaging/gen_install_manifest.py). The import is acyclic:
# updater imports only core/version, and nothing in updater
# imports integrity or tools. It is resolved relatively so the
# module works under every package name the app is imported by
# (voice_assistant.core.*, core.*) — core/plugin_loader.py's
# item-15 precedent, copied deliberately.
#
# PINNED_UPDATE_PUBLIC_KEY is copied into THIS module's namespace
# at import and read fresh on every verification, so a suite can
# monkeypatch integrity.PINNED_UPDATE_PUBLIC_KEY with a test
# keypair's public key (the updater documents the same seam on
# its own constant, and plugin_loader does the same). If the
# import ever fails, the verifier is None and verification FAILS
# CLOSED — an install whose manifest cannot be authenticated is
# reported, never silently trusted.
# ------------------------------------------------------------------
try:
    from .updater import _ed25519_verify
    from .updater import PINNED_UPDATE_PUBLIC_KEY
except Exception:  # noqa: BLE001 — no package context (bare script)
    try:
        from core.health.updater import _ed25519_verify  # app-root context  # Phase 3 H1: moved to core/health/
        from core.health.updater import PINNED_UPDATE_PUBLIC_KEY  # Phase 3 H1: moved to core/health/
    except Exception:  # noqa: BLE001 — fail closed, see above
        _ed25519_verify = None
        PINNED_UPDATE_PUBLIC_KEY = b""


def _package_root() -> Path:
    """The package root, derived from this file (core/health/ sits
    two levels under it), exactly how core/health/updater.py derives it."""
    return Path(__file__).resolve().parent.parent.parent  # Phase 3 H1: module moved one level deeper (core/health/); package root stays at tree root


def _sha256_of(path: Path) -> str:
    """Streaming SHA-256 hex digest — 1 MB chunks, constant
    memory (same pattern as core/health/updater.py)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_excluded(rel: Path) -> bool:
    """True when `rel` (package-relative) must never enter the
    manifest, whatever set matched it: bytecode caches
    (regenerated artifacts, not shipped source), the runtime
    data/ store (it changes legitimately on every run, so it
    can never be part of a shipped-tree manifest),
    credential-shaped files (.env, *.pem, *.key), and the
    manifest files themselves (a manifest can never list
    itself)."""
    parts = rel.parts
    if "__pycache__" in parts:
        return True
    if parts and parts[0] == "data":
        return True
    name = rel.name
    if name == ".env" or name.endswith(".pem") or \
            name.endswith(".key"):
        return True
    if rel.as_posix() in (MANIFEST_NAME, MANIFEST_SIG_NAME):
        return True
    return False


def build_manifest(root) -> dict:
    """The install manifest for the tree at `root`:
    {"format": 1, "files": {relpath_posix: sha256_hex}} covering
    every *.py file under `root`, with the exclusions in
    _manifest_excluded() applied. (The gui/assets/globe/ web-asset
    set from Audit Fix Batch 2, GAP-1, is gone with the world-view
    feature, removed 2026-11-05 — no web view executes asset code
    now.)

    Deterministic: files are inserted in sorted relpath order,
    and manifest_bytes() re-sorts on serialisation, so two builds
    of the same tree produce byte-identical manifests. The
    manifest files themselves are excluded by _manifest_excluded,
    so a manifest can never list itself. The shipped
    install_manifest.json is regenerated and re-signed by the
    ship-gate from this builder; the builder and the verifier
    below share this one function, so they can never disagree
    about the covered set."""
    root = Path(root)
    candidates = list(root.rglob("*.py"))
    files: dict[str, str] = {}
    for path in sorted(candidates,
                       key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root)
        if _manifest_excluded(rel):
            continue
        files[rel.as_posix()] = _sha256_of(path)
    return {"format": MANIFEST_FORMAT, "files": files}


def manifest_bytes(manifest: dict) -> bytes:
    """The canonical serialisation of `manifest` — THE bytes the
    Ed25519 signature covers, and the exact bytes
    packaging/gen_install_manifest.py writes to
    install_manifest.json.

    Canonical means: JSON with sorted keys, fixed separators
    (",", ":") — no incidental whitespace — encoded as UTF-8.
    Anyone re-serialising a parsed manifest for verification
    must reproduce these bytes exactly; verify_install_tree()
    avoids that trap by verifying the signature over the
    manifest file's bytes as read from disk, never over a
    re-serialisation."""
    return json.dumps(manifest, sort_keys=True,
                      separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _verify_manifest_signature(root: Path,
                               manifest_file_bytes: bytes,
                               problems: list[str]) -> None:
    """Appends a signature problem to `problems` unless the
    detached signature at <root>/install_manifest.json.sig
    verifies against PINNED_UPDATE_PUBLIC_KEY (this module's
    copy, read fresh — see the import note above) over
    `manifest_file_bytes`, the manifest's exact bytes."""
    sig_path = root / MANIFEST_SIG_NAME
    if not sig_path.is_file():
        problems.append(
            f"missing signature: {MANIFEST_SIG_NAME} not found — "
            f"the install manifest is unsigned")
        return
    try:
        sig_text = sig_path.read_text(encoding="utf-8").strip()
    except Exception as exc:  # noqa: BLE001 — report, never raise
        problems.append(
            f"invalid signature: {MANIFEST_SIG_NAME} could not be "
            f"read ({exc})")
        return
    try:
        signature = base64.b64decode(sig_text, validate=True)
    except Exception:  # noqa: BLE001 — not base64: not a signature
        problems.append(
            f"invalid signature: {MANIFEST_SIG_NAME} is not valid "
            f"base64")
        return
    if len(signature) != 64:
        problems.append(
            f"invalid signature: {MANIFEST_SIG_NAME} decodes to "
            f"{len(signature)} bytes, not the 64 of an Ed25519 "
            f"signature")
        return
    if _ed25519_verify is None:
        problems.append(
            "invalid signature: the Ed25519 verifier "
            "(core/health/updater.py) could not be imported")
        return
    if not _ed25519_verify(PINNED_UPDATE_PUBLIC_KEY,
                           manifest_file_bytes, signature):
        problems.append(
            "invalid signature: the signature in "
            f"{MANIFEST_SIG_NAME} does not verify against the "
            f"pinned release public key "
            f"(core/health/updater.py PINNED_UPDATE_PUBLIC_KEY)")


def verify_install_tree(root=None) -> dict:
    """Verifies the install tree at `root` (default: this file's
    package root) against its signed install manifest.

    Returns {"ok": bool, "checked": int, "problems": [str, ...]}:
    `checked` is the number of files the signed manifest lists
    (0 when there is no usable manifest), and `problems` names,
    in plain words, every defect found — missing manifest,
    missing/invalid signature, files the manifest lists that are
    missing on disk, extra files on disk that the manifest
    does not list, and hash mismatches. A live-tree comparison
    still runs when the signature is bad: the mismatches are
    useful diagnostics even though the manifest itself is
    untrusted in that case. NEVER raises."""
    root = Path(root) if root is not None else _package_root()
    problems: list[str] = []
    try:
        manifest_path = root / MANIFEST_NAME
        if not manifest_path.is_file():
            return {"ok": False, "checked": 0,
                    "problems": [
                        f"missing manifest: {MANIFEST_NAME} not "
                        f"found at {root} — the install tree's "
                        f"integrity cannot be verified"]}
        try:
            manifest_file_bytes = manifest_path.read_bytes()
        except Exception as exc:  # noqa: BLE001 — never raise
            return {"ok": False, "checked": 0,
                    "problems": [
                        f"invalid manifest: {MANIFEST_NAME} could "
                        f"not be read ({exc})"]}
        try:
            manifest = json.loads(
                manifest_file_bytes.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — never raise
            return {"ok": False, "checked": 0,
                    "problems": [
                        f"invalid manifest: {MANIFEST_NAME} is "
                        f"not valid JSON ({exc})"]}
        expected = manifest.get("files") \
            if isinstance(manifest, dict) else None
        if not isinstance(expected, dict) or \
                not all(isinstance(k, str) and isinstance(v, str)
                        for k, v in expected.items()):
            return {"ok": False, "checked": 0,
                    "problems": [
                        f"invalid manifest: {MANIFEST_NAME} does "
                        f"not carry a 'files' map of "
                        f"relpath -> sha256 hex"]}
        if manifest.get("format") != MANIFEST_FORMAT:
            problems.append(
                f"invalid manifest: format is "
                f"{manifest.get('format')!r}, expected "
                f"{MANIFEST_FORMAT}")
        # The signature covers the manifest's EXACT file bytes
        # (see manifest_bytes) — verified before the live-tree
        # comparison, whose problems are still reported after.
        _verify_manifest_signature(root, manifest_file_bytes,
                                   problems)
        live = build_manifest(root).get("files", {})
        for rel in sorted(expected):
            if rel not in live:
                problems.append(
                    f"missing file: {rel} is in the signed "
                    f"manifest but not on disk")
            elif live[rel] != expected[rel]:
                problems.append(
                    f"hash mismatch: {rel} has changed since "
                    f"the manifest was signed")
        for rel in sorted(live):
            if rel not in expected:
                problems.append(
                    f"extra file: {rel} is a file on disk "
                    f"that the signed manifest does not list")
        return {"ok": not problems, "checked": len(expected),
                "problems": problems}
    except Exception as exc:  # noqa: BLE001 — NEVER raises
        problems.append(
            f"integrity check itself failed: "
            f"{type(exc).__name__}: {exc}")
        return {"ok": False, "checked": 0, "problems": problems}


def _log_problem(root: Path, problem: str) -> None:
    """One loud line per problem in data/activity.log — the
    codebase's established line format (see core/health/updater.py's
    _log and core/funnel/tools_registry.py's _log_activity:
    "<iso timestamp> | <source> | <message>") — under the tree
    being checked. Never raises."""
    try:
        log_path = root / "data" / "activity.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{timestamp} | integrity | INSTALL TREE "
                     f"INTEGRITY PROBLEM: {problem}\n")
    except Exception:  # noqa: BLE001 — logging must never break
        pass


def check_and_log(root=None) -> dict:
    """Runs verify_install_tree() and reports any problems the
    loud way: one line per problem appended to the app log
    (data/activity.log, via _log_problem) AND printed to stderr.
    A clean tree is silent. Returns the verify result dict.
    Never raises — this runs at startup, beside the updater's
    apply_staged(), under the same never-stop-startup posture."""
    root = Path(root) if root is not None else _package_root()
    result = verify_install_tree(root)
    for problem in result.get("problems", []):
        print(f"[integrity] INSTALL TREE INTEGRITY PROBLEM: "
              f"{problem}", file=sys.stderr)
        _log_problem(root, problem)
    return result

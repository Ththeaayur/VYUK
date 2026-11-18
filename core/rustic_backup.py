"""User-file backup through the rustic binary (R9-2, rustic adoption).

rustic (rustic-rs/rustic, Apache-2.0) is a fast, encrypted,
deduplicated backup client in Rust, restic-repository-compatible.
It is a BINARY, not a library, so there is nothing to port: VYUK
adopts the released binary and drives it through a thin wrapper.

The binary is OPERATOR-SUPPLIED, never bundled in this batch
(distribution-gate decision for the parent): the operator places
rustic 0.11.4 at data/bin/rustic (Linux) or data/bin/rustic.exe
(Windows). The wrapper verifies the binary's sha256 against the
pinned release hashes BEFORE every run and refuses to execute a
binary that does not match. The Linux binary was exercised in the
lab (init / backup / snapshots / restore all run for real); the
Windows binary's hash was verified but it cannot run on this
Linux lab machine, so no Windows behaviour is claimed here.

Trust discipline (the product's own pattern, non-negotiable):

* The repository password is OPERATOR-SUPPLIED, never stored by
  this module. It is read from the RUSTIC_PASSWORD environment
  variable AT CALL TIME ONLY: it is never written to disk, never
  logged, never put in a tool argument (tool arguments can land in
  the activity log), and never passed as a --password flag (rustic
  itself warns that reveals it in the process list). If the
  variable is not set in VYUK's environment, every operation that
  needs it refuses plainly and says how to set it.
* The backup destination is operator-chosen; nothing here ever
  auto-starts a backup. Every write operation (init / backup /
  restore) is confirm-gated through the ONE funnel.
* Local repositories only: a repository path that looks like a
  remote backend URL (any URI scheme other than a Windows drive
  letter) is refused.
* A backup source that IS the repository, or lives inside it, is
  refused (a backup of its own repository would grow forever).
* Every write is verified by a read-back before the success line
  is claimed: init is followed by a snapshots probe that must
  open the new repository (A5-sysreadback); backup is followed by
  a snapshots probe that must contain the new snapshot id
  (A5-sysreadback); restore is followed by a listing of the
  destination that must be non-empty (A4-filestat). A write whose
  read-back fails is reported as a failure, never claimed.

What this does NOT do: no cloud, no remote backend, no
scheduling, no pruning/forget (deleting snapshots is a separate,
owner-ruled decision), no key management beyond the operator's
own password. The PF-27 ZIP (core/backup.py) keeps doing what it
does — VYUK's own settings/memories/milestones only. The restic
pilot (core/restic_pilot.py) stays evaluation-only and executes
nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from . import config_spec
import re
import subprocess
import sys
from pathlib import Path
from core.funnel.tools_registry_core import _funnel_only_fn


RUSTIC_VERSION = "0.11.4"

# Pinned release hashes (rustic v0.11.4, published 2026-08-18).
# Linux GNU tarball sha256 c20bc3682c6275de3cfbc9317101dca5c0800a2fb5c610d0f9ef2addfd27cf81
# (verified against the release .sha256 file); Windows MSVC tarball
# sha256 aa3586c2646da30965e099393c739a32526c27314cfaaf97af7f65aab1ec230a
# (same). These are the EXTRACTED BINARY hashes, verified by the
# builder from the downloaded release tarballs.
EXPECTED_BINARY_SHA256 = {
    "rustic": "3cf6e3d3e1e9de1528b07dcaee220d9882c5921f2a01e510360a1c6be8e9fc13",
    "rustic.exe": "3d4bc16c35b9813ff006576dce01e54d947111d16bdfdfe6742d90d0a2fddc5d",
}
RELEASE_URL = (
    "https://github.com/rustic-rs/rustic/releases/tag/v0.11.4"
)

_TREE_ROOT = Path(__file__).resolve().parent.parent
_BIN_DIR = _TREE_ROOT / "data" / "bin"
_REPO_FILE = _TREE_ROOT / "data" / "rustic_repo.json"

# A backup / restore can legitimately take a long time on a real
# folder; a listing or an init should not.
_TIMEOUT_SHORT_S = 300
_TIMEOUT_LONG_S = 3600

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_HEX_RE = re.compile(r"^[0-9a-f]{7,64}$")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class _RusticRefusal(Exception):
    """A plain-words refusal: nothing was run, nothing changed."""


def binary_name() -> str:
    """The binary file name for this platform."""
    return "rustic.exe" if sys.platform == "win32" else "rustic"


def binary_path() -> Path:
    """Where the operator places the rustic binary."""
    return _BIN_DIR / binary_name()


def _file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _verify_binary() -> Path:
    """Return the binary path, or raise if it is absent / unpinned.

    The hash is checked before EVERY run, not just once at import:
    a binary swapped after placement must never execute.
    """
    path = binary_path()
    if not path.is_file():
        raise _RusticRefusal(
            "The rustic backup binary is not placed yet. Put rustic "
            "%s at %s (from %s) and I can use it — VYUK does not "
            "bundle it. The binary's sha256 is checked before every "
            "run." % (RUSTIC_VERSION, path, RELEASE_URL)
        )
    expected = EXPECTED_BINARY_SHA256[binary_name()]
    actual = _file_sha256(path)
    if actual != expected:
        raise _RusticRefusal(
            "The rustic binary at %s does not match the pinned "
            "sha256 for rustic %s, so I refuse to run it. Replace it "
            "with the released binary from %s."
            % (path, RUSTIC_VERSION, RELEASE_URL)
        )
    return path


def _require_password() -> str:
    """The repository password, from the environment, at call time.

    Never stored, never logged, never a tool argument: the caller
    sets RUSTIC_PASSWORD in VYUK's environment (their shell, or
    their own secret store) before asking for a backup operation.
    """
    password = config_spec.explicit("RUSTIC_PASSWORD") or ""
    if not password:
        raise _RusticRefusal(
            "I need the repository password to do that, and it is not "
            "set in this session. Set RUSTIC_PASSWORD in VYUK's "
            "environment (your shell or your own secret store) and ask "
            "again — VYUK never stores the password itself."
        )
    return password


def _is_local_repo(repo: str) -> bool:
    """True only for a plain local filesystem path.

    Anything with a URI scheme (s3:, sftp:, rest:, ...) is a remote
    backend and is refused; a Windows drive letter is local.
    """
    if "://" in repo:
        return False
    if _SCHEME_RE.match(repo) and not _DRIVE_RE.match(repo):
        return False
    return True


def _is_within(path: str, parent: str) -> bool:
    """True if `path` is strictly inside `parent` (both as given)."""
    try:
        Path(path).relative_to(parent)
    except ValueError:
        return False
    return Path(path) != Path(parent)


def _validate_repo_path(repo: str) -> str:
    repo = (repo or "").strip()
    if not repo:
        raise _RusticRefusal(
            "Give a folder for the repository, or initialise one first "
            "with init_backup_repo — I have no repository to use."
        )
    if not _is_local_repo(repo):
        raise _RusticRefusal(
            "That repository is not a local folder — VYUK only backs "
            "up to local repositories, never to a remote backend."
        )
    if not Path(repo).is_absolute():
        raise _RusticRefusal(
            "Give the repository as a full path (for example "
            "C:\\backups\\vyuk on Windows), not a relative one."
        )
    return repo


def _read_default_repo() -> str | None:
    try:
        data = json.loads(_REPO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    repo = data.get("repo") if isinstance(data, dict) else None
    return repo if isinstance(repo, str) and repo else None


def _write_default_repo(repo: str) -> None:
    _REPO_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REPO_FILE.write_text(
        json.dumps({"repo": repo}, indent=2) + "\n", encoding="utf-8"
    )


def _resolve_repo(repo_path: str | None) -> str:
    """Explicit path, else the default set by init_backup_repo."""
    if repo_path and str(repo_path).strip():
        return _validate_repo_path(str(repo_path))
    default = _read_default_repo()
    if default:
        return _validate_repo_path(default)
    raise _RusticRefusal(
        "No backup repository is set yet. Initialise one first with "
        "init_backup_repo, or give a repository path to use."
    )


def _child_env(password: str) -> dict:
    """Environment for the child: the password, nothing else new.

    RUSTIC_REPOSITORY is dropped so the -r flag we always pass is
    the single source of truth; the password itself is never put in
    an argument or a message.
    """
    env = dict(os.environ)
    env["RUSTIC_PASSWORD"] = password
    env.pop("RUSTIC_REPOSITORY", None)
    return env


def _last_error(stderr: str) -> str:
    """The last `error:` line rustic printed, in plain words."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.lower().startswith("error:"):
            return ln[:200]
    if lines:
        return lines[-1][:200]
    return "rustic failed without saying why"


def _run_rustic(
    binary: Path,
    repo: str,
    password: str,
    args: list,
    timeout: int,
) -> tuple:
    """Run rustic with -r <repo>; return (returncode, stdout, stderr).

    Never raises for a rustic failure — the caller turns a non-zero
    exit into a plain-words refusal from rustic's own error line.
    """
    cmd = [str(binary), "-r", repo] + [str(a) for a in args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_child_env(password),
        )
    except FileNotFoundError:
        raise _RusticRefusal(
            "The rustic binary disappeared between the hash check and "
            "the run — nothing was executed."
        )
    except subprocess.TimeoutExpired:
        raise _RusticRefusal(
            "rustic did not finish in time (%d seconds) — the "
            "operation was stopped, and nothing is claimed."
            % timeout
        )
    except OSError as exc:
        raise _RusticRefusal(
            "I could not start rustic at all (%s) — nothing ran." % exc
        )
    return proc.returncode, proc.stdout, proc.stderr


def _list_snapshots(repo: str, password: str) -> list:
    """All snapshots in the repository, newest first (flat list)."""
    binary = _verify_binary()
    rc, out, err = _run_rustic(
        binary, repo, password, ["snapshots", "--json"],
        _TIMEOUT_SHORT_S,
    )
    if rc != 0:
        raise _RusticRefusal(
            "I could not open the repository at %s: %s"
            % (repo, _last_error(err))
        )
    try:
        groups = json.loads(out)
    except ValueError:
        raise _RusticRefusal(
            "rustic opened the repository but returned output I could "
            "not read — nothing is claimed."
        )
    snaps = []
    for group in groups if isinstance(groups, list) else []:
        for snap in group.get("snapshots", []):
            snaps.append(snap)
    snaps.sort(key=lambda s: str(s.get("time", "")), reverse=True)
    return snaps


def _snapshot_line(snap: dict) -> str:
    snap_id = str(snap.get("id", ""))
    when = str(snap.get("time", ""))[:19].replace("T", " ")
    paths = ", ".join(str(p) for p in snap.get("paths", []))
    summary = snap.get("summary") or {}
    files = summary.get("total_files_processed", "?")
    return "%s  %s  %s  (%s files)" % (
        snap_id[:8] or "?",
        when or "?",
        paths or "?",
        files,
    )


def init_backup_repo(repo_path: str = "") -> str:
    """Create a new encrypted repository at `repo_path`."""
    try:
        binary = _verify_binary()
        password = _require_password()
        repo = _validate_repo_path(repo_path)
        repo_dir = Path(repo)
        if repo_dir.is_file():
            raise _RusticRefusal(
                "That path is a file, not a folder — a repository "
                "needs an empty (or new) folder."
            )
        if (repo_dir / "config").is_file():
            raise _RusticRefusal(
                "That folder already holds a repository — use it with "
                "backup_files, or pick a different folder to initialise."
            )
        parent = repo_dir.parent
        if not parent.is_dir():
            raise _RusticRefusal(
                "The folder above %s does not exist — create it first, "
                "then initialise the repository." % repo
            )
        rc, _out, err = _run_rustic(
            binary, repo, password, ["init"], _TIMEOUT_SHORT_S
        )
        if rc != 0:
            raise _RusticRefusal(
                "The repository could not be created at %s: %s"
                % (repo, _last_error(err))
            )
        # A5-sysreadback: the new repository must open before the
        # creation is claimed (a fresh repository holds no snapshots).
        snaps = _list_snapshots(repo, password)
        if snaps:
            raise _RusticRefusal(
                "The repository at %s was created but already holds "
                "snapshots, which a fresh repository cannot — I am not "
                "claiming it." % repo
            )
        _write_default_repo(repo)
    except _RusticRefusal as exc:
        return str(exc)
    return (
        "Backup repository created at %s. It opens cleanly and holds "
        "no snapshots yet. It is now the default repository — "
        "backup_files will use it unless you name another."
        % repo
    )


def backup_files(
    sources=None,
    repo_path: str | None = None,
    label: str | None = None,
) -> str:
    """Back up `sources` (files/folders) into the repository."""
    try:
        binary = _verify_binary()
        password = _require_password()
        repo = _resolve_repo(repo_path)
        if label is not None and str(label).strip():
            label = str(label).strip()
            if not _LABEL_RE.match(label):
                raise _RusticRefusal(
                    "That label is not usable — use letters, numbers, "
                    "dashes and underscores only, up to 64 characters."
                )
        else:
            label = None
        if not sources:
            raise _RusticRefusal(
                "Give at least one file or folder to back up."
            )
        if isinstance(sources, str):
            sources = [sources]
        checked = []
        repo_resolved = Path(repo).resolve()
        for src in sources:
            if not isinstance(src, str) or not src.strip():
                raise _RusticRefusal(
                    "Every backup source must be a path to a file or "
                    "folder."
                )
            path = Path(src).expanduser()
            if not path.exists():
                raise _RusticRefusal(
                    "I cannot back up %s — it does not exist."
                    % src
                )
            resolved = path.resolve()
            if resolved == repo_resolved or repo_resolved in resolved.parents:
                raise _RusticRefusal(
                    "I will not back up the repository itself (%s) — "
                    "a backup of its own repository would grow "
                    "forever." % src
                )
            checked.append(str(resolved))
        args = ["backup", "--json"]
        if label:
            args += ["--label", label]
        args += checked
        rc, out, err = _run_rustic(
            binary, repo, password, args, _TIMEOUT_LONG_S
        )
        if rc != 0:
            raise _RusticRefusal(
                "The backup failed: %s" % _last_error(err)
            )
        try:
            result = json.loads(out)
            snap_id = str(result.get("id", ""))
            summary = result.get("summary") or {}
        except ValueError:
            raise _RusticRefusal(
                "rustic reported the backup done but returned output I "
                "could not read — nothing is claimed."
            )
        if not _HEX_RE.match(snap_id):
            raise _RusticRefusal(
                "rustic reported the backup done but gave no snapshot "
                "id — nothing is claimed."
            )
        # A5-sysreadback: the new snapshot must be in the repository's
        # own list before the backup is claimed.
        snaps = _list_snapshots(repo, password)
        if not any(str(s.get("id", "")) == snap_id for s in snaps):
            raise _RusticRefusal(
                "rustic reported a backup snapshot, but that snapshot "
                "is not in the repository's list — nothing is claimed."
            )
        files = summary.get("total_files_processed", "?")
        new_bytes = summary.get("data_added", "?")
    except _RusticRefusal as exc:
        return str(exc)
    return (
        "Backed up %s file(s) from %d source(s) into %s — %s bytes of "
        "new data. Snapshot %s, verified in the repository's snapshot "
        "list."
        % (files, len(checked), repo, new_bytes, snap_id[:8])
    )


def list_backup_snapshots(repo_path: str | None = None) -> str:
    """List the snapshots in the repository (pure read)."""
    try:
        _verify_binary()
        password = _require_password()
        repo = _resolve_repo(repo_path)
        snaps = _list_snapshots(repo, password)
    except _RusticRefusal as exc:
        return str(exc)
    if not snaps:
        return (
            "Repository %s holds no snapshots yet." % repo
        )
    lines = ["Repository %s holds %d snapshot(s):" % (repo, len(snaps))]
    lines += ["  " + _snapshot_line(s) for s in snaps]
    return "\n".join(lines)


def restore_backup(
    snapshot: str = "latest",
    destination: str = "",
    repo_path: str | None = None,
) -> str:
    """Restore `snapshot` (an id, or 'latest') into `destination`."""
    try:
        binary = _verify_binary()
        password = _require_password()
        repo = _resolve_repo(repo_path)
        snap = (snapshot or "").strip()
        if not snap:
            raise _RusticRefusal(
                "Give a snapshot to restore — 'latest', or a snapshot "
                "id from list_backup_snapshots."
            )
        snap_id, _, subpath = snap.partition(":")
        if snap_id != "latest" and not _HEX_RE.match(snap_id.lower()):
            raise _RusticRefusal(
                "That snapshot does not look like one — use 'latest' "
                "or a snapshot id from list_backup_snapshots."
            )
        if subpath and (".." in Path(subpath).parts):
            raise _RusticRefusal(
                "That path inside the snapshot is not usable — use a "
                "plain path without '..', or restore the whole snapshot."
            )
        if subpath:
            # The subpath must be exactly as the snapshot stored it:
            # a snapshot of absolute sources stores absolute paths, so
            # a relative subpath is not in the tree at all — rustic
            # answers that with a misleading "internal operations"
            # error, so the wrapper checks first and refuses plainly.
            snaps = _list_snapshots(repo, password)
            if snap_id == "latest":
                target = snaps[0] if snaps else None
            else:
                target = next(
                    (s for s in snaps
                     if str(s.get("id", "")).startswith(snap_id)),
                    None,
                )
            if target is None:
                raise _RusticRefusal(
                    "There is no snapshot %s in %s to restore from."
                    % (snap_id[:8], repo)
                )
            stored = [str(p) for p in target.get("paths", [])]
            inside = any(
                subpath == p or _is_within(subpath, p) for p in stored
            )
            if not inside:
                raise _RusticRefusal(
                    "That path is not in snapshot %s — the snapshot "
                    "holds %s. Give the path exactly as stored, or "
                    "restore the whole snapshot."
                    % (snap_id[:8], ", ".join(stored) or "nothing")
                )
        dest = (destination or "").strip()
        if not dest:
            raise _RusticRefusal(
                "Give a folder to restore into — nothing is restored "
                "without a destination you chose."
            )
        dest_path = Path(dest).expanduser()
        if not dest_path.is_absolute():
            raise _RusticRefusal(
                "Give the restore destination as a full path, not a "
                "relative one."
            )
        repo_resolved = Path(repo).resolve()
        if dest_path.resolve() == repo_resolved or repo_resolved in dest_path.resolve().parents:
            raise _RusticRefusal(
                "I will not restore into the repository itself — pick "
                "a different destination."
            )
        dest_path.mkdir(parents=True, exist_ok=True)
        rc, _out, err = _run_rustic(
            binary, repo, password,
            ["restore", snap, str(dest_path)], _TIMEOUT_LONG_S,
        )
        if rc != 0:
            raise _RusticRefusal(
                "The restore failed: %s" % _last_error(err)
            )
        # A4-filestat: the destination must hold restored files
        # before the restore is claimed.
        try:
            entries = list(dest_path.iterdir())
        except OSError:
            entries = []
        if not entries:
            raise _RusticRefusal(
                "rustic reported the restore done, but %s is empty — "
                "nothing is claimed." % dest_path
            )
    except _RusticRefusal as exc:
        return str(exc)
    return (
        "Restored snapshot %s into %s — %d top-level item(s) are there "
        "now. Check the files before using them."
        % (snap_id[:8] if snap_id != "latest" else "latest",
           dest_path, len(entries))
    )


def backup_status(repo_path: str | None = None) -> str:
    """Whether rustic is placed and verified, and what the repository holds.

    Pure read — nothing is started, stopped or changed. The snapshot
    count needs the repository password; without it, the status says
    so instead of guessing.
    """
    parts = []
    path = binary_path()
    if not path.is_file():
        return (
            "rustic is not placed yet — put rustic %s at %s (from %s) "
            "and set RUSTIC_PASSWORD in VYUK's environment to use "
            "backups. VYUK does not bundle the binary."
            % (RUSTIC_VERSION, path, RELEASE_URL)
        )
    expected = EXPECTED_BINARY_SHA256[binary_name()]
    actual = _file_sha256(path)
    if actual != expected:
        return (
            "The rustic binary at %s does not match the pinned sha256 "
            "for rustic %s — it will not be run. Replace it with the "
            "released binary from %s."
            % (path, RUSTIC_VERSION, RELEASE_URL)
        )
    parts.append(
        "rustic %s is placed at %s and its sha256 matches the pinned "
        "release hash." % (RUSTIC_VERSION, path)
    )
    default = _read_default_repo()
    repo = None
    if repo_path and str(repo_path).strip():
        try:
            repo = _validate_repo_path(str(repo_path))
        except _RusticRefusal as exc:
            return str(exc)
    elif default:
        repo = default
    if not repo:
        parts.append(
            "No backup repository is set yet — initialise one with "
            "init_backup_repo."
        )
        return " ".join(parts)
    parts.append("Default repository: %s." % repo)
    password = config_spec.explicit("RUSTIC_PASSWORD") or ""
    if not password:
        parts.append(
            "The repository password is not set in this session, so I "
            "cannot open the repository — set RUSTIC_PASSWORD in "
            "VYUK's environment to see its snapshots."
        )
        return " ".join(parts)
    try:
        snaps = _list_snapshots(repo, password)
    except _RusticRefusal as exc:
        parts.append(str(exc))
        return " ".join(parts)
    if not snaps:
        parts.append("It holds no snapshots yet.")
    else:
        latest = snaps[0]
        parts.append(
            "It holds %d snapshot(s); the newest is %s from %s."
            % (len(snaps), str(latest.get("id", ""))[:8],
               str(latest.get("time", ""))[:19].replace("T", " "))
        )
    return " ".join(parts)


@_funnel_only_fn
def _tool_init_backup_repo(repo_path: str = "") -> str:
    """Funnel handler: create a new encrypted backup repository."""
    return init_backup_repo(repo_path=repo_path)


@_funnel_only_fn
def _tool_backup_files(
    sources=None,
    repo_path: str | None = None,
    label: str | None = None,
) -> str:
    """Funnel handler: back up files/folders into the repository."""
    return backup_files(sources=sources, repo_path=repo_path, label=label)


@_funnel_only_fn
def _tool_list_backup_snapshots(repo_path: str | None = None) -> str:
    """Funnel handler: list snapshots (pure read)."""
    return list_backup_snapshots(repo_path=repo_path)


@_funnel_only_fn
def _tool_restore_backup(
    snapshot: str = "latest",
    destination: str = "",
    repo_path: str | None = None,
) -> str:
    """Funnel handler: restore a snapshot into a chosen folder."""
    return restore_backup(
        snapshot=snapshot, destination=destination, repo_path=repo_path
    )


@_funnel_only_fn
def _tool_backup_status(repo_path: str | None = None) -> str:
    """Funnel handler: rustic placement + repository status (read)."""
    return backup_status(repo_path=repo_path)


def register_all(registry) -> None:
    """Register the rustic backup pack (R9-2, rustic adoption)."""
    registry.register(
        name="init_backup_repo",
        description="Create a new encrypted backup repository in a folder you choose, using the rustic backup engine. The repository password comes from RUSTIC_PASSWORD in VYUK's environment — set it before you ask, VYUK never stores it. The new repository becomes the default, so later backups use it unless you name another. Local folders only, never a remote backend. Use when the user wants to set up backups for the first time.",
        parameters={
            "type": "object",
            "properties": {
                "repo_path": {"type": "string",
                             "description": "The full path of the folder to turn into a backup repository (for example C:\\backups\\vyuk)"},
            },
            "required": ["repo_path"],
        },
        handler=_tool_init_backup_repo,
    )
    registry.register(
        name="backup_files",
        description="Back up files or folders into the backup repository with the rustic engine: encrypted, deduplicated and compressed, kept as a named snapshot you can restore later. The repository password comes from RUSTIC_PASSWORD in VYUK's environment — set it before you ask, VYUK never stores it. The repository is the default one unless you name another; a source that is the repository itself is refused. Use when the user asks to back up their files.",
        parameters={
            "type": "object",
            "properties": {
                "sources": {"type": "array",
                            "items": {"type": "string"},
                            "description": "The files or folders to back up (full paths)"},
                "repo_path": {"type": "string",
                             "description": "The backup repository to use (optional; defaults to the one init_backup_repo created)"},
                "label": {"type": "string",
                          "description": "A short label for this snapshot (optional; letters, numbers, dashes and underscores only)"},
            },
            "required": ["sources"],
        },
        handler=_tool_backup_files,
    )
    registry.register(
        name="list_backup_snapshots",
        description="List the backup snapshots in the repository: when each was taken, what it holds, and its id for restoring. Pure read — nothing is started, stopped or changed. The repository password comes from RUSTIC_PASSWORD in VYUK's environment. Use when the user asks what backups exist or which snapshot to restore.",
        parameters={
            "type": "object",
            "properties": {
                "repo_path": {"type": "string",
                             "description": "The backup repository to list (optional; defaults to the one init_backup_repo created)"},
            },
            "required": [],
        },
        handler=_tool_list_backup_snapshots,
    )
    registry.register(
        name="restore_backup",
        description="Restore a backup snapshot into a folder you choose, using the rustic engine. Give 'latest' or a snapshot id from list_backup_snapshots. The repository password comes from RUSTIC_PASSWORD in VYUK's environment — set it before you ask, VYUK never stores it. Nothing is restored without a destination you chose, and never into the repository itself. Use when the user asks to get files back from a backup.",
        parameters={
            "type": "object",
            "properties": {
                "snapshot": {"type": "string",
                             "description": "The snapshot to restore: 'latest', or a snapshot id from list_backup_snapshots"},
                "destination": {"type": "string",
                                "description": "The full path of the folder to restore into"},
                "repo_path": {"type": "string",
                             "description": "The backup repository to restore from (optional; defaults to the one init_backup_repo created)"},
            },
            "required": ["snapshot", "destination"],
        },
        handler=_tool_restore_backup,
    )
    registry.register(
        name="backup_status",
        description="Whether the rustic backup engine is placed and verified, which repository is the default, and how many snapshots it holds. Pure read — nothing is started, stopped or changed. The snapshot count needs RUSTIC_PASSWORD in VYUK's environment; without it the status says so instead of guessing. Use when the user asks if backups are ready or why a backup refused.",
        parameters={
            "type": "object",
            "properties": {
                "repo_path": {"type": "string",
                             "description": "The backup repository to check (optional; defaults to the one init_backup_repo created)"},
            },
            "required": [],
        },
        handler=_tool_backup_status,
    )

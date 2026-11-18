"""Wave 7 Build 1 — SANDBOX-light completion (filesystem form).

This COMPLETES the Wave 4 sandbox (core/sandbox_light.py),
which was in-memory only: no real filesystem access, no tool
caller, no core-path boundary in code. This module adds the
real-filesystem form, and nothing else about the sandbox
shape changes:

  * A bounded execution environment for a test / dry-run
    action: file writes are confined to a caller-supplied
    sandbox directory, resolved through symlinks (the Wave 6
    harness precondition, applied here to every path).
  * Light-sandbox-first order, enforced in code: the light
    sandbox builds; full-VM universes (item 12) are NOT built
    here and stay QUEUED. This module contains no VM,
    hypervisor, guest-image, or code-execution machinery.
  * Core-modification boundary (item 1692), enforced in
    code, not prose: a sandbox action targeting a core path
    is refused outright, before any filesystem touch, and
    the sandbox root itself may not be the core directory
    or inside it, nor the app data directory or inside it.
  * Plan-approval gate intact, no self-deployment, ever:
    promotion is REQUEST-ONLY (one file, copy-only scope),
    exactly as in Wave 4. This module never copies a file
    outside the sandbox, never applies a proposal, never
    deploys anything. Promotion itself is the caller's
    separate C-14 action, outside this module.
  * C-14 / fail-closed (D-2): this module never approves
    anything itself. Consequential production calls go
    through the tool funnel (core/funnel/tools_gate.py), which
    refuses confirm-gated tools without a human confirm
    handler. Inside-sandbox file actions are the only
    actions this module performs, and every other action
    kind (send, network, purchase, execute, run, shell)
    is refused outright, never executed, never guessed
    into a file action.
  * No fake data, no guessed paths: a path that is not
    relative, not inside the sandbox after resolution, or
    not an existing file for a read/delete is refused
    with its reason, never created, never read, never
    written by guessing.

Actions are file operations ONLY (read / write / delete
inside the sandbox). This module never imports subprocess,
never calls eval/exec, never fetches a URL, never sends a
message, never touches the memory DB, the vault, .env
files, credentials, or core paths. A sandbox run returns
a report (attempted, allowed, refused with reason, files
changed inside only, sentinel files unchanged or not);
the report is data, never an instruction.

Own code, stdlib only. No third-party dependency is
introduced by this file (see gui/assets/
SANDBOX_LICENCE_READ_W7.md).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Action kinds this module will ever perform. Everything
# else is refused outright, never mapped onto a file op.
_ALLOWED_KINDS = frozenset({"read", "write", "delete"})

# Kinds named here so a caller cannot smuggle them in as
# "actions": they are refused with an explicit reason.
_REFUSED_KINDS = frozenset({
    "send", "network", "fetch", "purchase", "execute", "run",
    "shell", "subprocess", "install", "deploy", "promote",
})

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _tree_root() -> Path:
    """The shipped app tree root (parent of this file's package)."""
    return Path(__file__).resolve().parent.parent


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _hash_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return None


class FileSystemSandbox:
    """A bounded, real-filesystem light sandbox.

    ``root`` must already exist (the production tool
    creates it after the funnel's human confirm; the
    read-only list tool never creates it). The root may
    not be the app core directory or inside it, nor the
    app data directory or inside it, nor the tree root
    itself — those would make "inside the sandbox" mean
    "inside the app's own code or data".
    """

    def __init__(self, root, seed: dict | None = None):
        tree = _tree_root()
        self._tree_root = tree
        self._core_dir = tree / "core"
        self._data_dir = tree / "data"
        root_path = Path(root).expanduser()
        if not root_path.is_absolute():
            raise ValueError("sandbox root must be an absolute path")
        resolved = root_path.resolve()
        if not resolved.is_dir():
            raise ValueError(f"sandbox root does not exist: {root}")
        if (resolved == tree or _is_inside(resolved, self._core_dir)
                or resolved == self._core_dir
                or _is_inside(resolved, self._data_dir)
                or resolved == self._data_dir):
            raise ValueError(
                "sandbox root must not be the app tree, the core "
                "directory, or the data directory, or inside them")
        self._root = resolved
        self._promotions: list[str] = []
        self._last_report: dict | None = None
        if seed:
            if not isinstance(seed, dict):
                raise ValueError("seed must be a dict of rel path -> text")
            for rel, content in seed.items():
                result = self.write(rel, content if isinstance(content, str)
                                    else str(content))
                if not result["ok"]:
                    raise ValueError(
                        f"seed refused for {rel!r}: {result['reason']}")

    @property
    def root(self) -> Path:
        return self._root

    def resolve_inside(self, rel) -> dict:
        """Resolve a relative path, or refuse it with a reason.

        Refusals, all before any filesystem touch:
        non-string / empty / absolute / drive / NUL paths;
        any path resolving outside the sandbox root (``..``
        escapes and symlink escapes alike, because the
        resolved path is checked, not the lexical one);
        any path resolving into the core directory, the
        data directory, or the app's own .env file.
        """
        if not isinstance(rel, str) or not rel or "\x00" in rel:
            return {"ok": False, "reason": "path must be a non-empty string"}
        probe = Path(rel)
        if probe.is_absolute() or _DRIVE_RE.match(rel):
            return {"ok": False, "reason": "absolute paths refused"}
        try:
            resolved = (self._root / rel).resolve()
        except (OSError, ValueError, RuntimeError) as exc:
            return {"ok": False, "reason": f"path cannot be resolved: {exc}"}
        if not _is_inside(resolved, self._root):
            return {"ok": False, "reason": "path escapes the sandbox"}
        if _is_inside(resolved, self._core_dir) or resolved == self._core_dir:
            return {"ok": False,
                    "reason": "core-modification boundary: core paths refused"}
        if _is_inside(resolved, self._data_dir) or resolved == self._data_dir:
            return {"ok": False,
                    "reason": "app data paths refused"}
        if resolved == self._tree_root / ".env":
            return {"ok": False, "reason": "app .env refused"}
        return {"ok": True, "path": resolved,
                "rel": str(resolved.relative_to(self._root))}

    def read(self, rel) -> dict:
        resolved = self.resolve_inside(rel)
        if not resolved["ok"]:
            return resolved
        target = resolved["path"]
        if not target.is_file():
            return {"ok": False,
                    "reason": f"no such file in sandbox: {resolved['rel']}"}
        try:
            return {"ok": True, "path": resolved["rel"],
                    "content": target.read_text()}
        except OSError as exc:
            return {"ok": False, "reason": f"read failed: {exc}"}

    def write(self, rel, content) -> dict:
        resolved = self.resolve_inside(rel)
        if not resolved["ok"]:
            return resolved
        if not isinstance(content, str):
            return {"ok": False, "reason": "content must be a string"}
        target = resolved["path"]
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Re-check after creating parents: a parent that
            # was a symlink could only have been created by
            # an earlier allowed action inside the sandbox,
            # but the resolved target is re-verified anyway.
            rechecked = self.resolve_inside(rel)
            if not rechecked["ok"]:
                return rechecked
            target.write_text(content)
        except OSError as exc:
            return {"ok": False, "reason": f"write failed: {exc}"}
        return {"ok": True, "path": resolved["rel"],
                "note": "sandbox-only write; nothing outside changed"}

    def delete(self, rel) -> dict:
        resolved = self.resolve_inside(rel)
        if not resolved["ok"]:
            return resolved
        target = resolved["path"]
        if not target.is_file():
            return {"ok": False,
                    "reason": f"no such file in sandbox: {resolved['rel']}"}
        try:
            target.unlink()
        except OSError as exc:
            return {"ok": False, "reason": f"delete failed: {exc}"}
        return {"ok": True, "path": resolved["rel"],
                "note": "sandbox-only delete; nothing outside changed"}

    def list_files(self) -> list[str]:
        found = []
        for path in sorted(self._root.rglob("*")):
            if path.is_file():
                try:
                    found.append(str(path.relative_to(self._root)))
                except ValueError:
                    continue
        return found

    def request_promotion(self, rel) -> dict:
        """Record a copy-only promotion REQUEST; never perform it.

        One file only: directories and wildcards are refused.
        The file must exist inside the sandbox. Nothing is
        copied outside by this call — promotion itself is
        the caller's separate, C-14-approved action.
        """
        if not isinstance(rel, str) or not rel or rel.endswith("/") \
                or "*" in rel or "?" in rel:
            return {"ok": False,
                    "reason": "promotion is copy-only, one file: "
                              "directories and wildcards refused"}
        resolved = self.resolve_inside(rel)
        if not resolved["ok"]:
            return resolved
        if not resolved["path"].is_file():
            return {"ok": False,
                    "reason": f"no such file in sandbox: {resolved['rel']}"}
        self._promotions.append(resolved["rel"])
        return {"ok": True, "path": resolved["rel"],
                "note": "promotion REQUESTED only — caller must approve "
                        "before any real file changes"}

    def promotions(self) -> list[str]:
        return list(self._promotions)

    def run_actions(self, actions, sentinels=None) -> dict:
        """Run file actions inside the sandbox, report every one.

        ``actions`` is a list of dicts: {"kind": "read" |
        "write" | "delete", "path": rel, "content": str for
        writes}. Any other kind, any malformed action, and
        any path refusal is recorded as refused with its
        reason — never executed, never partially applied
        by guessing. ``sentinels`` is an optional list of
        file paths OUTSIDE the sandbox; they are hashed
        before and after, and the report states whether
        every sentinel is unchanged (a sentinel inside the
        sandbox is skipped, never treated as outside proof).
        """
        if not isinstance(actions, list):
            return {"ok": False, "reason": "actions must be a list",
                    "attempted": 0, "allowed": [], "refused": [],
                    "files_changed": [], "sentinel_unchanged": None}
        sentinel_paths: list[Path] = []
        if sentinels:
            if not isinstance(sentinels, list):
                return {"ok": False,
                        "reason": "sentinels must be a list",
                        "attempted": 0, "allowed": [], "refused": [],
                        "files_changed": [], "sentinel_unchanged": None}
            for item in sentinels:
                try:
                    candidate = Path(item).expanduser().resolve()
                except (OSError, ValueError, RuntimeError):
                    continue
                if _is_inside(candidate, self._root):
                    continue
                sentinel_paths.append(candidate)
        before = {str(p): (_hash_file(p), p.exists())
                  for p in sentinel_paths}
        allowed: list[dict] = []
        refused: list[dict] = []
        changed: list[str] = []
        for action in actions:
            if not isinstance(action, dict):
                refused.append({"action": action,
                                "reason": "action must be a dict"})
                continue
            kind = action.get("kind")
            rel = action.get("path")
            if kind in _REFUSED_KINDS or (
                    kind not in _ALLOWED_KINDS and kind is not None):
                refused.append({"action": action,
                                "reason": f"refused action kind: {kind}"})
                continue
            if kind not in _ALLOWED_KINDS:
                refused.append({"action": action,
                                "reason": "action kind missing or unknown"})
                continue
            if kind == "read":
                result = self.read(rel)
            elif kind == "write":
                if "content" not in action:
                    refused.append({"action": action,
                                    "reason": "write action needs content"})
                    continue
                result = self.write(rel, action["content"])
            else:  # delete
                result = self.delete(rel)
            if result["ok"]:
                allowed.append({"kind": kind, "path": result["path"]})
                if kind in ("write", "delete") \
                        and result["path"] not in changed:
                    changed.append(result["path"])
            else:
                refused.append({"action": action,
                                "reason": result["reason"]})
        after = {str(p): (_hash_file(p), p.exists())
                 for p in sentinel_paths}
        sentinel_unchanged = before == after if sentinel_paths else None
        report = {
            "ok": not refused,
            "sandbox_root": str(self._root),
            "attempted": len(actions),
            "allowed": allowed,
            "refused": refused,
            "files_changed": sorted(changed),
            "files_inside": self.list_files(),
            "promotions": self.promotions(),
            "sentinel_unchanged": sentinel_unchanged,
            "note": "sandbox-only run; no file outside the sandbox "
                    "was written by this run",
        }
        self._last_report = report
        return report

    def last_report(self) -> dict | None:
        return self._last_report

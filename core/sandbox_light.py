"""Wave 4 SANDBOX-light — light sandbox only, never full-VM.

Stdlib only, in-memory only, no network, no real filesystem access.
A light sandbox is a private copy of a caller-supplied file map:
actions inside it change only the copy, and the copy is discarded
unless the caller explicitly promotes a named file through the
approve-before-real-files gate (which this module records, but
does not itself perform — promotion is the caller's C-14 action).

Rules, enforced in code:
  * The sandbox starts as a copy of the caller's file map. Writing
    inside it never touches the caller's map: proven by comparing
    the caller's map before and after, which this module's tests do.
  * A test action inside the sandbox changes nothing outside it.
    There is no path from a sandbox write to a real file.
  * Full-VM universes (item 12) are NOT built here and stay QUEUED.
    This module's docstring and code contain no VM, hypervisor, or
    guest-image machinery.
  * Promotion is copy-only scope: a promotion request names one
    file; a request naming a directory or a wildcard is refused.
"""

from __future__ import annotations

import copy


class LightSandbox:
    """An in-memory, copy-only sandbox over a caller file map."""

    def __init__(self, file_map: dict):
        if not isinstance(file_map, dict):
            raise ValueError("file_map must be a dict")
        self._outside = file_map
        self._inside = copy.deepcopy(file_map)
        self._promotions = []

    def read(self, path: str) -> dict:
        if path not in self._inside:
            return {"ok": False, "reason": f"no such file in sandbox: {path}"}
        return {"ok": True, "content": self._inside[path]}

    def write(self, path: str, content: str) -> dict:
        """Write inside the sandbox only. The outside map is untouched."""
        self._inside[path] = content
        return {"ok": True, "path": path,
                "note": "sandbox-only write; nothing outside changed"}

    def delete(self, path: str) -> dict:
        if path not in self._inside:
            return {"ok": False, "reason": f"no such file in sandbox: {path}"}
        del self._inside[path]
        return {"ok": True, "path": path,
                "note": "sandbox-only delete; nothing outside changed"}

    def inside_files(self) -> list:
        return sorted(self._inside)

    def outside_unchanged(self, original_snapshot: dict) -> bool:
        """Prove the outside map still equals the caller's snapshot."""
        return self._outside == original_snapshot

    def request_promotion(self, path: str) -> dict:
        """Record a copy-only promotion request; do NOT perform it.

        Promotion itself is the caller's C-14 action, outside this
        module. A directory or wildcard request is refused outright.
        """
        if not path or path.endswith("/") or "*" in path:
            return {"ok": False,
                    "reason": "promotion is copy-only, one file: "
                              "directories and wildcards refused"}
        if path not in self._inside:
            return {"ok": False,
                    "reason": f"no such file in sandbox: {path}"}
        self._promotions.append(path)
        return {"ok": True, "path": path,
                "note": "promotion REQUESTED only — caller must approve "
                        "before any real file changes"}

    def promotions(self) -> list:
        return list(self._promotions)

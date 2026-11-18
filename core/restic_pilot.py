"""Wave 10 (v5.65) Leaf 3 — SE-6.11 restic PILOT (evaluation only).

This module evaluates restic as a possible future backup engine.
It is NOT the default engine (the PF-27 ZIP engine in
core/backup.py is), it bundles NO binary, it executes
NOTHING, and it imports nothing new — stdlib only, no
``subprocess``, no ``os.system``, no network. Everything here
builds command argument lists; running them is a human's or a
future, separately-gated step's decision.

LICENCE VERIFICATION (read 2026-10-25, recorded verbatim):
- Canonical repository metadata, GitHub API for restic/restic
  (https://api.github.com/repos/restic/restic — default branch
  ``master``, repo updated 2026-10-24): ``license`` =
  {"key": "bsd-2-clause", "name": 'BSD 2-Clause "Simplified"
  License', "spdx_id": "BSD-2-Clause"}. This CONFIRMS the plan's
  "BSD-2-Clause" claim for the canonical repo.
- Full licence text, as published for restic's LICENSE file via
  pkg.go.dev's licence rendering (restic v0.12.3 copy, and the
  v0.9.2 copy — identical text): "BSD 2-Clause License.
  Copyright (c) 2014, Alexander Neumann <alexander@bumpern.de>.
  All rights reserved. Redistribution and use in source and
  binary forms, with or without modification, are permitted
  provided that [the two BSD-2-Clause conditions] are met..."
  (binary redistribution must reproduce the copyright notice,
  conditions and disclaimer in the documentation and/or other
  materials provided with the distribution).
- GAP, stated plainly: no versioned canonical release tarball's
  LICENSE file itself was fetched in this sandbox (its URL was
  not returned verbatim by the sources consulted), so NO release
  version is asserted here. The pilot records
  ``licence_version_read`` as "canonical repo master metadata
  (2026-10-25) + LICENSE text as published for restic v0.12.3 /
  v0.9.2" and ``pinned_release`` as None. Before ANY bundling,
  the coordinator must pin one release, fetch that release's
  LICENSE, and register it in THIRD_PARTY_LICENSES.md (CC-1).

PILOT VERDICT (see ``pilot_verdict()``):
- Licence identity: BSD-2-Clause — permissive, allow-list
  compatible in principle.
- Bundling: BLOCKS. Not because the licence forbids it — it
  does not — but because bundling the restic binary has CC-1
  asset implications that are NOT yet discharged: no binary is
  bundled by this pilot, no pinned release version exists yet,
  and the BSD-2-Clause binary-redistribution clause (reproduce
  copyright notice + conditions + disclaimer in the
  distribution materials) has no entry in the shipped tree yet.
  Bundling clears ONLY once: (1) a release is pinned, (2) its
  LICENSE text ships in the distribution materials, (3) the
  asset is registered through the CC-1 asset step. Until then
  the verdict is PILOT, evaluation only, never the default.

Cloud backends are DISABLED always: the config object accepts
only ``local``; any attempt to select or enable a cloud backend
(s3, azure, gcs, b2, swift, rclone, or anything else) raises
ValueError at construction AND at ``set_backend`` time. Local
repos only (a directory path on this machine). No credentials
are ever stored by this module — a local repo path is the only
backend state it holds, and the command builder never adds a
password flag (restic reads its repository password from its
own environment/interactive prompt, outside this module).
"""

from __future__ import annotations

from dataclasses import dataclass, field

LICENCE_SPDX = "BSD-2-Clause"
LICENCE_NAME = 'BSD 2-Clause "Simplified" License'
LICENCE_COPYRIGHT = "Copyright (c) 2014, Alexander Neumann"
LICENCE_SOURCE_API = "https://api.github.com/repos/restic/restic"
LICENCE_SOURCE_TEXT = (
    "pkg.go.dev licence rendering of restic's LICENSE "
    "(v0.12.3 and v0.9.2 copies, identical text)")
LICENCE_VERSION_READ = (
    "canonical repo master metadata, read 2026-10-25, + LICENSE "
    "text as published for restic v0.12.3 / v0.9.2; "
    "no versioned release LICENSE fetched — pinned_release is None")
CLOUD_BACKENDS = frozenset({
    "s3", "azure", "gcs", "b2", "swift", "rclone", "sftp", "rest",
    "http", "gs", "azblob", "s3-compatible",
})
LOCAL_BACKEND = "local"


class CloudBackendRefused(ValueError):
    """Raised for any attempt to enable a non-local backend."""


def _refuse_backend(backend: str) -> None:
    if str(backend).strip().lower() != LOCAL_BACKEND:
        raise CloudBackendRefused(
            f"cloud/remote backend {backend!r} is disabled in the "
            "restic pilot: local repositories only")


@dataclass
class ResticConfig:
    """Pilot configuration. Local repos only, always.

    ``repo_path`` is a local directory path for the restic
    repository. ``binary`` is the command name used when
    building argument lists (default "restic"); it is NOT a
    bundled binary path — this pilot ships no binary, and
    ``binary`` must never be pointed at a shipped asset by
    this module (there is none to point at).
    """

    repo_path: str
    binary: str = "restic"
    backend: str = field(default=LOCAL_BACKEND, init=False)

    def __post_init__(self) -> None:
        if not str(self.repo_path or "").strip():
            raise ValueError("repo_path must be a non-empty local path")

    def set_backend(self, backend: str) -> None:
        """Select a backend — anything but "local" raises."""
        _refuse_backend(backend)
        self.backend = LOCAL_BACKEND


def build_snapshot_command(config: ResticConfig,
                           source_paths: list[str]) -> list[str]:
    """Argument list for ``restic -r <repo> backup <sources>``.

    Commands only — never executed by this module or by any
    shipped code path that imports it. Empty source lists and
    empty source entries are refused (a snapshot of nothing
    must not be silently built).
    """
    _refuse_backend(config.backend)
    sources = [str(p) for p in source_paths]
    if not sources or any(not p.strip() for p in sources):
        raise ValueError("snapshot needs at least one non-empty source")
    return [config.binary, "-r", config.repo_path, "backup", *sources]


def build_restore_command(config: ResticConfig, snapshot: str,
                          target: str) -> list[str]:
    """Argument list for ``restic -r <repo> restore <snap>``.

    Commands only — never executed. ``snapshot`` is a restic
    snapshot id (or "latest"); ``target`` is the local directory
    to restore into. Both must be non-empty.
    """
    _refuse_backend(config.backend)
    if not str(snapshot or "").strip():
        raise ValueError("restore needs a snapshot id")
    if not str(target or "").strip():
        raise ValueError("restore needs a target directory")
    return [config.binary, "-r", config.repo_path, "restore",
            snapshot, "--target", target]


def pilot_verdict() -> dict:
    """The pilot verdict: licence + bundling assessment.

    ``bundling`` is "BLOCKED" (see module docstring for the
    three discharge conditions); ``is_default_engine`` is
    False; ``executes_restic`` is False; ``bundles_binary`` is
    False. A coordinator must not promote this pilot on the
    strength of the licence alone.
    """
    return {
        "disposition": "PILOT",
        "is_default_engine": False,
        "executes_restic": False,
        "bundles_binary": False,
        "licence_spdx": LICENCE_SPDX,
        "licence_name": LICENCE_NAME,
        "licence_copyright": LICENCE_COPYRIGHT,
        "licence_source_api": LICENCE_SOURCE_API,
        "licence_source_text": LICENCE_SOURCE_TEXT,
        "licence_version_read": LICENCE_VERSION_READ,
        "pinned_release": None,
        "cloud_backends": "DISABLED_ALWAYS",
        "bundling": "BLOCKED",
        "bundling_blockers": [
            "no restic binary is bundled by this pilot",
            "no release version pinned yet (pinned_release is None)",
            "BSD-2-Clause binary-redistribution clause not yet "
            "discharged in shipped materials: copyright notice + "
            "conditions + disclaimer must be reproduced in the "
            "distribution materials",
            "CC-1 asset registration for the restic binary not done",
        ],
        "bundling_clears_when": [
            "a restic release is pinned and its LICENSE fetched",
            "the LICENSE text ships in the distribution materials",
            "the asset is registered through the CC-1 asset step",
        ],
    }

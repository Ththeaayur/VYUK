"""
Real auto-update core for VYUK (v5.2) — check, stage, apply.

The flow is deliberately staged so a bad update can never brick the
install:

1. `check()` reads a static JSON manifest from the configured feed
   (config.update_feed_url / UPDATE_FEED_URL) and reports whether a
   newer build exists. The manifest is only trusted after its
   detached Ed25519 signature — fetched from "<feed>.sig", base64 —
   verifies against the public key PINNED in this module
   (PINNED_UPDATE_PUBLIC_KEY) over the exact manifest bytes: the
   feed also carries the update zip's SHA-256, so a hash from an
   unsigned feed would prove nothing (a compromised feed host could
   serve a hostile zip and its matching hash together). Signature
   missing or invalid → state "untrusted", and no field of the
   manifest is ever parsed, let alone acted on. The signing key is
   held offline; packaging/sign_manifest.py does the signing.
2. `download_and_stage(info)` downloads the release zip with the
   codebase's resumable ".part" discipline, verifies its SHA-256
   BEFORE anything is promoted (the v4.2 rule: a hash-failed file is
   deleted, never kept — its bytes can never heal), and extracts it
   into data/update_staging/<version>/ with zip-slip guards plus a
   per-file inventory in the staging marker. Live files are never
   touched at this stage.
3. `apply_staged()` runs once at startup (main.py, before the main
   window constructs). It re-verifies the staging inventory, backs
   the current package tree up to data/update_backup/<old_version>/,
   then swaps the staged tree over the package tree — data/ is
   excluded on both sides, always: user data never moves. Any
   failure mid-swap restores from the backup and reports
   "rolled_back". Gate B additions: staged payloads naming a
   credential path (.env, .env.* other than the shipped
   .env.example, google_token.json, .vyuk_secret_key) are refused
   whole at extraction and again before apply, and rollback never
   deletes or clobbers the user's current credential files (the
   backup never contained them, and the restore never touches
   them); the staged signed manifest must verify before any live
   change; the version file is copied LAST, after all other files
   are in place and verified, so a crash mid-swap can never leave
   a mixed tree reporting the new version — a leftover apply
   journal triggers recovery on next startup (restore the
   pre-update tree, discard the staging) unless the journal says
   the swap had already completed; and files the previous
   manifest owned but the new manifest no longer lists are
   removed, except user-created files, user-modified files, and
   protected/data paths, which are preserved and reported.

Transport rules: HTTPS only. A plaintext http:// feed or zip is
refused — with ONE exception: loopback hosts (127.0.0.1, localhost,
::1) are allowed so developers can test against a local server; a
loopback address is by definition this machine. Downloads go
through the system proxy when one is configured (v3.3 behaviour),
except loopback, which is never proxied.

local_assets.download_file is NOT reused here: it is pack-coupled
(HF-mirror retry, the pack progress tracker, tier size floors).
This module carries its own small downloader + hasher that follow
the same rules (.part staging, Range resume, short reads keep the
.part, integrity failures delete it, promote with os.replace).

No Qt imports — pure stdlib, safe to run before QApplication exists.
Nothing in this module raises across its public API: every outcome
is a state dict, because an updater failure must never stop VYUK
from starting.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .. import power_policy  # Phase 3 H1: target stays flat in core/
from ..version import VERSION, parse_version, version_newer  # Phase 3 H1: target stays flat in core/

# Re-exports — split from this module (Phase B B24); importers unchanged.
from .updater_paths import _APPLIED_NAME, _BACKUP_DIRNAME, _CHECK_TIMEOUT_S, _DOWNLOAD_TIMEOUT_S, _JOURNAL_NAME, _LOOPBACK_HOSTS, _MANIFEST_NAME, _MANIFEST_SIG_NAME, _MARKER_NAME, _PROTECTED_BASENAMES, _STAGING_DIRNAME, _VERSION_REL, _data_dir, _log, _marker_path, _now_iso, _package_root, _sha256_of, _staging_dir  # noqa: F401
from .updater_transport import _configured_feed_url, _opener_for, _transport_error  # noqa: F401
from .updater_verify import PINNED_UPDATE_PUBLIC_KEY, UPDATE_FEED_PUBLIC_KEY_HEX, _ED_BASE, _ED_BASE_X, _ED_BASE_Y, _ED_D, _ED_I, _ED_IDENTITY, _ED_L, _ED_Q, _UNTRUSTED_MESSAGE, _ed25519_verify, _ed_add, _ed_decode_point, _ed_encode, _ed_mul, _ed_xrecover, _verify_feed_signature  # noqa: F401
from .updater_policy import _policy_refusal  # noqa: F401
from .updater_check import check  # noqa: F401
from .updater_stage import _download_and_stage, _download_resumable, _extract_staged, download_and_stage  # noqa: F401
from .updater_pending import _read_marker, pending_update  # noqa: F401
from .updater_protect import _is_protected_rel, _protected_in_inventory  # noqa: F401
from .updater_manifest import _read_manifest_map, _verify_staged_manifest  # noqa: F401
from .updater_apply import _apply_staged, _backup_ignore, _clear_staging, _journal_path, _read_journal, _recover_interrupted, _remove_journal, _restore_from_backup, _verify_staging, _writable_probe, apply_staged, _write_journal  # noqa: F401

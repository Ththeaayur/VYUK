"""Tool-receipt normalizers + signature helpers (skills split B17).

Moved verbatim from core/tool_receipts.py (constants, path/signature,
normalization, critical tier, _sanitize, validators). No behaviour change.
"""

from __future__ import annotations  # INTENTIONAL (skills split B17): old module carried it (B1 lesson).

import hashlib
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = 1
_DEFAULT_FILENAME = "tool_receipts.jsonl"
_MAX_LINES = 5000
_MAX_PREVIEW_CHARS = 500
_MAX_SUMMARY_CHARS = 300


def default_receipts_path() -> Path:
    """Production path: data/tool_receipts.jsonl."""
    from .config import DATA_DIR

    return Path(DATA_DIR) / _DEFAULT_FILENAME


def _canonical_args(args: Any) -> str:
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(args)


def args_signature(tool: str, args: Any) -> str:
    """Stable signature for a call: SHA-256 of tool + canonical
    args. The guard keys on this, so identical calls match and
    distinct calls never do.

    Loophole batch 3 (L-33): the signature is computed over the
    SCRUBBED args, never the raw ones — a hash of a password is
    still a verifier for that password (anyone who guesses it
    can test the guess), so the raw secret must never reach the
    hash. Two calls that differ only in a secret value now share
    one signature; that is the fail-closed direction (the guard
    may refuse a re-spelling, it never lets a duplicate through)."""
    blob = f"{tool}\0{_canonical_args(_sanitize(args))}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ----------------------------------------------------------------------
# Effect-identity normalisation (audit fix Batch 3, REL-02).
# Only named argument keys are normalised, and only where the
# normalisation is provably meaning-preserving for that key's
# kind. A guard false-positive (refusing a re-spelled duplicate)
# is the safe direction; a false-negative (missing a duplicate)
# is the risk, so anything ambiguous is left un-normalised.
# ----------------------------------------------------------------------

_PATH_KEYS = frozenset({
    "path", "filepath", "destination", "save_path", "output_path",
    "source_path",
})
_URL_KEYS = frozenset({"url", "link", "href", "website", "target_url"})
_RECIPIENT_KEYS = frozenset({
    "recipient", "to", "to_email", "recipient_email", "addressee",
    "cc", "bcc",
})
_HOST_KEYS = frozenset({"host", "hostname"})


def normalize_path_value(value: Any) -> str:
    """Platform path spelling: strip, ~ expansion, separator /
    trailing-slash collapse via normpath, and case-folding ONLY
    on case-insensitive platforms (Windows). POSIX paths keep
    their case (distinct files), and symlinks are NOT resolved
    (a symlink target can change under the guard)."""
    text = str(value).strip()
    if not text:
        return text
    try:
        expanded = os.path.expanduser(text)
        norm = os.path.normpath(expanded)
    except Exception:  # noqa: BLE001 — a weird path stays raw
        return text
    if os.name == "nt":
        return norm.lower()
    return norm


def normalize_url_value(value: Any) -> str:
    """URL spelling: scheme/host lowercased, default port
    stripped, trailing slashes stripped, fragment dropped (it
    is never sent to the server). Query order and
    percent-encoding are PRESERVED — both can change meaning."""
    text = str(value).strip()
    if "://" not in text:
        return text
    try:
        parts = urllib.parse.urlsplit(text)
        if not parts.scheme or not parts.netloc:
            return text
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            return text
        try:
            port = parts.port
        except ValueError:
            return text
        default_port = {"http": 80, "https": 443}.get(
            parts.scheme.lower())
        netloc = host
        if parts.username:
            netloc = parts.username + (
                f":{parts.password}" if parts.password else "") \
                + "@" + netloc
        if port is not None and port != default_port:
            netloc = f"{netloc}:{port}"
        path = parts.path.rstrip("/")
        return urllib.parse.urlunsplit(
            (parts.scheme.lower(), netloc, path, parts.query, ""))
    except Exception:  # noqa: BLE001 — an unparseable URL stays raw
        return text


def normalize_recipient_value(value: Any) -> str:
    """Recipient spelling: strip + lowercase. (An email local
    part is technically case-sensitive per RFC, but every real
    mail system treats it case-insensitively; for a duplicate
    guard the safe direction is to refuse the re-spelling.)"""
    return str(value).strip().lower()


def normalize_host_value(value: Any) -> str:
    """Host spelling: strip + lowercase + trailing-dot strip
    (a trailing dot is the DNS root label, same host)."""
    return str(value).strip().lower().rstrip(".")


def _normalized_value(key: str | None, value: Any) -> Any:
    if isinstance(value, str):
        if key in _PATH_KEYS:
            return normalize_path_value(value)
        if key in _URL_KEYS:
            return normalize_url_value(value)
        if key in _RECIPIENT_KEYS:
            return normalize_recipient_value(value)
        if key in _HOST_KEYS:
            return normalize_host_value(value)
        return value
    if isinstance(value, dict):
        return {k: _normalized_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalized_value(key, v) for v in value]
    return value


def normalized_args(args: Any) -> Any:
    """Args with every normalised key re-spelled canonically."""
    if isinstance(args, dict):
        return {k: _normalized_value(k, v) for k, v in args.items()}
    return _normalized_value(None, args)


def effect_signatures(tool: str, args: Any) -> list[str]:
    """Guard keys for one call: the exact-argument signature
    first, then the normalised-effect signature (deduped when
    they coincide). The guard matches on ANY of them."""
    exact = args_signature(tool, args)
    norm = args_signature(tool, normalized_args(args))
    return [exact] if norm == exact else [exact, norm]


def _attempt_signatures(attempt: dict) -> list[str]:
    """Signatures an attempt record guards under: its stored
    effect list when present, else its single exact signature
    (pre-Batch-3 receipts)."""
    stored = attempt.get("effect_signatures")
    if isinstance(stored, list) and stored:
        return [str(s) for s in stored]
    sig = attempt.get("args_signature")
    return [str(sig)] if sig else []


# ----------------------------------------------------------------------
# Critical tier (audit fix Batch 3, REL-04): the irreversible /
# leaves-the-machine subset of the confirm-gated tools. Only
# these tools' receipt records fsync; every other dispatch keeps
# the documented flush-only default. Maintained here (not
# imported from the gate) so this module stays stdlib-only; a
# tier name that is not a registered tool simply never matches.
# ----------------------------------------------------------------------

CRITICAL_RECEIPT_TIER = frozenset({
    "power_control",        # shutdown / restart / log off
    "empty_recycle_bin",    # irreversible delete
    "uninstall_app",        # removes software
    "winget_install",       # installs software
    "winget_upgrade_all",   # upgrades everything
    "close_application",    # can lose unsaved work
    "process_control",      # can kill processes
    "service_control",      # starts/stops system services
    "file_control",         # includes delete
    "run_command",          # arbitrary shell
    "clean_temp_files",     # deletes temp files in bulk
    "batch_rename",         # renames many files at once
    "send_email",           # mail leaves the machine
})


def _sanitize(value: Any) -> Any:
    try:
        from .distribution_hardening import _sanitize_value

        return _sanitize_value(value)
    except Exception:  # noqa: BLE001 — sanitizer never blocks receipts
        return repr(value)


def _validate_compensations(compensations: list | None) -> list:
    """§7 item 8: declared compensations, validated or refused.

    Each descriptor must be {"tool": <str>, "args": <dict>} with
    an optional "note" string. The runner executes them in
    REVERSE declaration order. Anything else fails loudly here
    — a half-formed compensation stored in the journal would be
    a guessed reversal waiting to happen. Returns a clean copy
    (never the caller's list)."""
    if compensations is None:
        return []
    if not isinstance(compensations, list):
        raise ValueError(
            "tool receipts: compensations must be a list of dicts")
    if len(compensations) > 32:
        raise ValueError(
            "tool receipts: at most 32 declared compensations")
    clean = []
    for item in compensations:
        if not isinstance(item, dict):
            raise ValueError(
                "tool receipts: each compensation must be a dict")
        comp_tool = item.get("tool")
        comp_args = item.get("args")
        note = item.get("note", "")
        if not isinstance(comp_tool, str) or not comp_tool:
            raise ValueError(
                "tool receipts: each compensation needs a tool name")
        if not isinstance(comp_args, dict):
            raise ValueError(
                "tool receipts: each compensation needs dict args")
        if not isinstance(note, str):
            raise ValueError(
                "tool receipts: compensation note must be a string")
        clean.append({"tool": comp_tool, "args": dict(comp_args),
                      "note": note})
    return clean


def _validate_manual_steps(manual_steps: list | None) -> list:
    """§7 item 8: human steps for the no-compensation case."""
    if manual_steps is None:
        return []
    if not isinstance(manual_steps, list):
        raise ValueError("tool receipts: manual_steps must be a list")
    if len(manual_steps) > 16:
        raise ValueError("tool receipts: at most 16 manual steps")
    for step in manual_steps:
        if not isinstance(step, str) or not step:
            raise ValueError(
                "tool receipts: each manual step must be a string")
    return list(manual_steps)

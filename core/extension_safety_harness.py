"""Wave 6 Build 1 — Extension safety harness, GATES FIRST (STAGING).

Status: STAGING ONLY. This file is NOT wired into the app, NOT
registered in any tool registry, and NOT shipped. It awaits the
parent's post-gate integration (see INTEGRATION_PLAN.md in this
directory). Nothing here loads, executes, or installs an extension;
it only audits a manifest, checks a path/action against a declared
policy, and records a human approval decision.

Design (reviewed against the existing gates, read-only, before
writing — no second gate owner invented):
- Approval: mirrors core/confirm_gate.py and
  core/resilience/resilience_gate.py — a consequential step runs only on
  an explicit human approval supplied by the CALLER. The harness
  never approves anything itself: if no confirm callable is
  supplied, the action is REFUSED (fail-closed, the H1 posture in
  core/funnel/tools_gate.py). At integration time the caller wires the
  confirm callable to core.confirm_gate.request_confirmation and
  the registry classification in core/funnel/tools_gate.py; until then
  the callable is a test seam, not a gate.
- Classification: mirrors core/funnel/tools_gate.py — every action is
  in exactly one class (free, approval-required, or refused), never
  free by omission. An action not in the manifest's declared
  permissions, or not in the harness's known action table, is
  refused.
- Stores: mirrors core/playbook_store.py — JSON, versioned,
  atomic save (temp file + os.replace), corrupt/missing loads to
  empty with a note and NEVER overwrites the file on a failed load.
- Undo/revoke: mirrors core/resilience/resilience_gate.py — revocation
  is terminal and re-arming a revoked extension is refused, not
  silently re-enabled.

Commercial law (standing, owner-vetoable only by the owner):
- Licence field is REQUIRED. A missing, empty, or unknown licence
  is REFUSED — never assumed cleared. This is the harness's
  allow-list; it does not clear any licence itself. A licence read
  filed by a human (or by a later licence-read build) is what puts
  a licence string in the allow-list, and only the exact strings
  in ALLOWED_LICENCES pass.
- Copyleft (GPL/LGPL/AGPL/MPL), non-commercial, revenue-cap, and
  territory-restricted material is REFUSED. Note: the app's
  THIRD_PARTY_LICENSES.md documents an MPL exception for bundled
  components; this harness is deliberately stricter for
  third-party EXTENSIONS (MPL-2.0 refused here) because an
  extension is unreviewed third-party code, not a reviewed bundle.
  If the owner wants MPL extensions admitted, that is his ruling
  to record, not a harness default.
- No payment surface (ruling 34): a purchase action is REFUSED
  even with an approving confirm callable. Approval cannot create
  a payment surface that does not exist.
- No fake data: no default permission, no guessed licence, no
  guessed directory, no guessed identity. Anything not declared
  in the manifest is refused.

Scope limits (standing, not waived by this build):
- No marketplace/store: this harness governs extensions; the
  store itself still needs the §14.20 counsel gate plus his
  approval of the store's rules, in that order, after the harness.
- No voiceprint, no continuous capture, no vehicle driving, no
  medical diagnosis: none of those is a grantable permission here
  (see KNOWN_PERMISSIONS and REFUSED_ACTIONS).
- No GUI: no sidebar/page/renumbering change. Frozen UI core.
- No network fetch, no code execution, no file write by this
  module itself. check_path_access / check_action are pure
  decisions; the caller performs (or refuses) the act.

Own code, stdlib only. No third-party dependency is introduced
by this file (see LICENCE_READ_W6.md).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HARNESS_VERSION = 1
STORE_VERSION = 1

# Exact licence strings the allow-list admits. Matching is
# normalised (case, spaces, hyphens) then EXACT — never substring,
# so "MIT-like" or "GPL-3.0 with MIT parts" never slips through.
ALLOWED_LICENCES = frozenset({
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "PSF-2.0",
    "CC0-1.0",
})

# Substrings that refuse a licence even if it also names an
# allowed licence (e.g. "MIT, non-commercial use only").
BLOCKED_LICENCE_MARKERS = (
    "gpl", "lgpl", "agpl", "mpl", "copyleft",
    "non-commercial", "noncommercial", "nc-",
    "revenue", "territory", "commercial use prohibited",
    "cc-by-nc", "cc-by-nd", "cc-by-sa",
)

# The ONLY permissions an extension may declare. Anything else
# in a manifest is refused at validation, not ignored.
KNOWN_PERMISSIONS = frozenset({
    "files.own.read",    # read inside its own declared directory
    "files.own.write",   # write inside its own declared directory
    "network.fetch",    # fetch a URL (policy decision only; the
                         # harness never fetches — the caller does,
                         # under the existing egress/confirm gates)
    "notify.user",      # show a notification to the user only
})

# Actions the harness classifies. "free" actions still require
# the matching declared permission; "approval" actions additionally
# require a caller-supplied human approval; "refused" actions are
# refused unconditionally (see module docstring for why purchase
# is refused even when approved).
ACTION_CLASS = {
    "read_own_file": "free",
    "write_own_file": "free",
    "notify_user": "free",
    "network_fetch": "free",
    "send_message": "approval",
    "delete_file": "approval",
    "purchase": "refused",
    "read_memory_db": "refused",
    "write_memory_db": "refused",
    "read_env": "refused",
    "read_credentials": "refused",
    "read_vault": "refused",
    "write_vault": "refused",
    "execute_code": "refused",
    "install_extension": "approval",
    "enable_extension": "approval",
}

# Action -> permission it requires (None = no permission can grant
# it; it is refused, or approval-gated, on its own class).
ACTION_PERMISSION = {
    "read_own_file": "files.own.read",
    "write_own_file": "files.own.write",
    "notify_user": "notify.user",
    "network_fetch": "network.fetch",
    "send_message": None,
    "delete_file": "files.own.write",
    "purchase": None,
    "read_memory_db": None,
    "write_memory_db": None,
    "read_env": None,
    "read_credentials": None,
    "read_vault": None,
    "write_vault": None,
    "execute_code": None,
    "install_extension": None,
    "enable_extension": None,
}

# Protected names: any path whose final component, or any
# component, matches one of these is refused regardless of the
# declared directory (a manifest cannot declare its way into
# them). Checked on path components, never by substring on the
# whole string, so "myassistant.db.backup" is not a false hit
# while "assistant.db" always is.
PROTECTED_NAMES = frozenset({
    ".env", "assistant.db", "credentials.json", "secrets.json",
})
PROTECTED_SUFFIXES = (".pem", ".key")

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _normalise_licence(text):
    """Normalise a licence string for exact allow-list lookup."""
    raw = str(text or "").strip()
    low = raw.lower().replace("_", "-").replace(" ", "-")
    low = re.sub(r"-+", "-", low)
    # Canonical spellings: "Apache 2.0" -> "apache-2.0" etc.
    if low in ("apache-2", "apache2"):
        low = "apache-2.0"
    return low


def _allowed_licence_lookup():
    return {_normalise_licence(lic): lic for lic in ALLOWED_LICENCES}


def audit_licence(licence):
    """Audit one licence string. Returns (ok, reason).

    Missing, empty, unknown, blocked-marker, or copyleft/NC/
    revenue-cap/territory-restricted licences all fail closed.
    This function never clears a licence; it only checks the
    allow-list a licence read has already populated.
    """
    if licence is None or not str(licence).strip():
        return False, "licence field is required, not supplied"
    raw = str(licence).strip()
    low = raw.lower()
    for marker in BLOCKED_LICENCE_MARKERS:
        if marker in low:
            return False, f"licence refused: blocked marker {marker!r} in {raw!r}"
    lookup = _allowed_licence_lookup()
    if _normalise_licence(raw) in lookup:
        return True, f"licence allowed: {lookup[_normalise_licence(raw)]}"
    return False, f"licence unknown, not in allow-list: {raw!r}"


def validate_manifest(manifest):
    """Validate an extension manifest dict. Returns a result dict.

    Required: name (non-empty str), version (X.Y.Z), licence
    (audited, see audit_licence), declared_permissions (list,
    subset of KNOWN_PERMISSIONS), declared_files (non-empty list
    of relative paths, no traversal, no absolute paths, no
    protected names). Optional: declared_directory (str; if
    absent, the caller must supply the directory at registration
    — never guessed here), source (recorded verbatim if given,
    never invented), description (recorded verbatim if given).
    """
    errors = []
    if not isinstance(manifest, dict):
        return {"ok": False, "errors": ["manifest is not a mapping"],
                "manifest": None}
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name is required and must be a non-empty string")
    version = manifest.get("version")
    if not isinstance(version, str) or not _VERSION_RE.match(version.strip()):
        errors.append("version is required and must be X.Y.Z")
    lic_ok, lic_reason = audit_licence(manifest.get("licence"))
    if not lic_ok:
        errors.append(lic_reason)
    perms = manifest.get("declared_permissions")
    if not isinstance(perms, list):
        errors.append("declared_permissions is required and must be a list")
        perms = []
    else:
        for perm in perms:
            if perm not in KNOWN_PERMISSIONS:
                errors.append(f"unknown permission refused: {perm!r}")
    files = manifest.get("declared_files")
    if not isinstance(files, list) or not files:
        errors.append("declared_files is required and must be a non-empty list")
        files = []
    else:
        for entry in files:
            if not isinstance(entry, str) or not entry.strip():
                errors.append("declared_files entries must be non-empty strings")
                continue
            if os.path.isabs(entry) or entry.startswith("~"):
                errors.append(f"declared file must be relative: {entry!r}")
                continue
            parts = Path(entry).parts
            if ".." in parts:
                errors.append(f"declared file escapes its directory: {entry!r}")
            if any(part in PROTECTED_NAMES for part in parts):
                errors.append(f"declared file is a protected name: {entry!r}")
            if any(part.lower().endswith(PROTECTED_SUFFIXES) for part in parts):
                errors.append(f"declared file is a protected suffix: {entry!r}")
    if errors:
        return {"ok": False, "errors": errors, "manifest": None}
    clean = {
        "name": name.strip(),
        "version": version.strip(),
        "licence": str(manifest.get("licence")).strip(),
        "declared_permissions": list(perms),
        "declared_files": list(files),
        "declared_directory": manifest.get("declared_directory"),
        "source": manifest.get("source"),
        "description": manifest.get("description"),
    }
    return {"ok": True, "errors": [], "manifest": clean}


def _is_protected_path(path_text):
    """True when any component of path_text is a protected name
    or carries a protected suffix."""
    parts = Path(str(path_text)).parts
    if any(part in PROTECTED_NAMES for part in parts):
        return True
    return any(part.lower().endswith(PROTECTED_SUFFIXES) for part in parts)


def check_path_access(extension, path_text, mode):
    """Pure sandbox decision: may this extension touch this path?

    extension: a validated manifest (from validate_manifest) plus
    a resolved "directory" key set at registration (the caller
    supplies the directory; the harness never guesses one).
    mode: "read" or "write". Anything else is refused.
    Rules, in order: protected name -> refused; no directory
    configured -> refused; path escapes the directory (including
    ".." traversal and symlink-style absolute paths) -> refused;
    mode needs its permission -> refused without it; else allowed.
    Wave 6 integration precondition (WAVE6_WIRING_SPEC §1.3):
    real paths ARE resolved here before the containment check,
    so a symlink or junction inside the declared directory
    pointing outside it is refused — lexical containment alone
    was staging-only and is not the live check.
    """
    if mode not in ("read", "write"):
        return {"ok": False, "reason": f"unknown access mode: {mode!r}"}
    if _is_protected_path(path_text):
        return {"ok": False, "reason": f"protected path refused: {path_text!r}"}
    directory = (extension or {}).get("directory")
    if not directory:
        return {"ok": False, "reason": "no declared directory configured for extension"}
    base = Path(str(directory))
    target = Path(str(path_text))
    if target.is_absolute():
        candidate = target
    else:
        candidate = base / target
    # Lexical containment only (no filesystem resolution here).
    try:
        candidate.resolve()
    except Exception:  # noqa: BLE001 — a gate never raises
        pass
    base_parts = Path(os.path.normpath(str(base))).parts
    cand_parts = Path(os.path.normpath(str(candidate))).parts
    if cand_parts[: len(base_parts)] != base_parts:
        return {"ok": False, "reason": f"path outside declared directory refused: {path_text!r}"}
    # Resolved-path re-check (integration precondition): a symlink
    # or junction inside the directory pointing outside it must
    # not pass on lexical containment alone. realpath resolves
    # symlinks in every existing path component, so it is the
    # right check even for targets that do not exist yet.
    real_base = Path(os.path.realpath(str(base)))
    real_candidate = Path(os.path.realpath(str(candidate)))
    if real_candidate.parts[: len(real_base.parts)] != real_base.parts:
        return {"ok": False, "reason": f"path outside declared directory refused (resolved path): {path_text!r}"}
    needed = "files.own.read" if mode == "read" else "files.own.write"
    if needed not in (extension or {}).get("declared_permissions", []):
        return {"ok": False, "reason": f"permission not declared: {needed}"}
    return {"ok": True, "reason": "inside declared directory with declared permission"}


def check_action(extension, action, *, confirm=None, reason=""):
    """Pure action decision for one extension action.

    extension: dict with keys "state" (see ExtensionHarness),
    "declared_permissions". confirm: a caller-supplied callable
    returning True only on an explicit human approval (the
    integration wires this to core.confirm_gate.request_
    confirmation). If confirm is None, approval-class actions are
    REFUSED — the harness never approves anything itself.
    "refused"-class actions are refused even when confirm would
    approve (purchase: no payment surface; memory/vault/.env/
    credentials: no permission can grant them).
    A revoked or non-enabled extension (except for the
    install/enable actions themselves) is refused.
    """
    state = (extension or {}).get("state")
    if action not in ACTION_CLASS:
        return {"ok": False, "reason": f"unknown action refused: {action!r}"}
    if action in ("install_extension", "enable_extension"):
        # These two are judged by the approval record, not by the
        # enabled state (an extension cannot be enabled before it
        # is approved). The caller must still pass confirm.
        if ACTION_CLASS[action] == "approval":
            if confirm is None:
                return {"ok": False, "reason": "approval required, no confirm handler supplied"}
            try:
                approved = bool(confirm(action, reason))
            except Exception:  # noqa: BLE001 — a gate failure refuses
                return {"ok": False, "reason": "confirm handler raised, refused"}
            if not approved:
                return {"ok": False, "reason": "human approval not given"}
            return {"ok": True, "reason": "human approval given"}
        return {"ok": False, "reason": f"action refused: {action!r}"}
    if state == "revoked":
        return {"ok": False, "reason": "extension is revoked, all actions refused"}
    if state != "enabled":
        return {"ok": False, "reason": f"extension is not enabled (state: {state}), action refused"}
    klass = ACTION_CLASS[action]
    if klass == "refused":
        return {"ok": False, "reason": f"action refused by harness policy: {action!r}"}
    needed = ACTION_PERMISSION.get(action)
    if needed is not None and needed not in (extension or {}).get("declared_permissions", []):
        return {"ok": False, "reason": f"permission not declared: {needed}"}
    if klass == "approval":
        if confirm is None:
            return {"ok": False, "reason": "approval required, no confirm handler supplied"}
        try:
            approved = bool(confirm(action, reason))
        except Exception:  # noqa: BLE001 — a gate failure refuses
            return {"ok": False, "reason": "confirm handler raised, refused"}
        if not approved:
            return {"ok": False, "reason": "human approval not given"}
        return {"ok": True, "reason": "human approval given"}
    return {"ok": True, "reason": "free action with declared permission"}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


class ExtensionHarness:
    """Approval record + pipeline for extensions (staging).

    States: registered -> approved -> enabled -> disabled, and
    any state -> revoked (terminal). Transitions require
    approved=True AND human=True, mirroring
    resilience_gate.rearm_resilience; a missing or false
    approval refuses and records nothing as approved.
    The store is the harness's own JSON file (never the memory
    DB, never the vault, never .env). Save is atomic; load
    failures yield an empty store with a note and never
    overwrite the file.
    """

    def __init__(self, store_path=None):
        self.store_path = str(store_path) if store_path else None
        self.entries = {}
        self.last_load_note = ""
        if self.store_path:
            self._load()

    def _load(self):
        try:
            text = Path(self.store_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            self.entries = {}
            self.last_load_note = "no store file, starting empty"
            return
        except OSError as exc:
            self.entries = {}
            self.last_load_note = f"store unreadable, starting empty: {exc}"
            return
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            self.entries = {}
            self.last_load_note = "store corrupt, starting empty, file untouched"
            return
        if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
            self.entries = {}
            self.last_load_note = "store version unsupported, starting empty, file untouched"
            return
        entries = data.get("entries")
        if not isinstance(entries, dict):
            self.entries = {}
            self.last_load_note = "store entries malformed, starting empty, file untouched"
            return
        self.entries = entries
        self.last_load_note = ""

    def _save(self):
        """Atomic save. Returns (ok, error-or-empty). Never raises."""
        if not self.store_path:
            return True, ""
        data = {"version": STORE_VERSION, "entries": self.entries}
        try:
            directory = os.path.dirname(os.path.abspath(self.store_path))
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ext_harness_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2, sort_keys=True)
                os.replace(tmp, self.store_path)
            except Exception:  # noqa: BLE001
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as exc:
            return False, str(exc)
        return True, ""

    def _log(self, name, event, reason):
        entry = self.entries.setdefault(name, {})
        log = entry.setdefault("log", [])
        log.append({"at": _utc_now(), "event": event, "reason": reason})

    def register(self, manifest, *, directory):
        """Register a validated manifest. Returns a result dict.

        directory is caller-supplied (the extension's own
        directory); a missing/empty directory refuses, never
        guessed. Registration does NOT approve or enable: the
        extension is inert until a human approves it.
        """
        validation = validate_manifest(manifest)
        if not validation["ok"]:
            return {"ok": False, "errors": validation["errors"]}
        if not directory or not str(directory).strip():
            return {"ok": False, "errors": ["declared directory is required, not supplied"]}
        clean = validation["manifest"]
        name = clean["name"]
        if name in self.entries and self.entries[name].get("state") == "revoked":
            return {"ok": False, "errors": ["extension is revoked, re-registration refused"]}
        self.entries[name] = {
            "manifest": clean,
            "directory": str(directory).strip(),
            "state": "registered",
            "log": [],
        }
        self._log(name, "registered", "manifest validated and licence audited")
        ok, err = self._save()
        if not ok:
            return {"ok": False, "errors": [f"store save failed: {err}"],
                    "persisted": False}
        return {"ok": True, "state": "registered", "persisted": True}

    def approve(self, name, *, approved, human=True, reason=""):
        """Human approve-first for install/enable. Both approved
        and human must be True, or the approval is refused and
        the state does not change."""
        entry = self.entries.get(name)
        if entry is None:
            return {"ok": False, "reason": "unknown extension"}
        if entry.get("state") == "revoked":
            return {"ok": False, "reason": "extension is revoked, approval refused"}
        if not approved or not human:
            return {"ok": False, "reason": "approval requires approved=True and human=True"}
        entry["state"] = "approved"
        self._log(name, "approved", reason or "human approval recorded")
        ok, err = self._save()
        return {"ok": ok, "state": "approved",
                "reason": "" if ok else f"store save failed: {err}"}

    def enable(self, name):
        """Enable only an approved extension. Revoked, disabled-
        without-approval, and merely-registered extensions refuse."""
        entry = self.entries.get(name)
        if entry is None:
            return {"ok": False, "reason": "unknown extension"}
        if entry.get("state") == "revoked":
            return {"ok": False, "reason": "extension is revoked, enable refused"}
        if entry.get("state") != "approved":
            return {"ok": False, "reason": f"extension is not approved (state: {entry.get('state')}), enable refused"}
        entry["state"] = "enabled"
        self._log(name, "enabled", "approved extension enabled")
        ok, err = self._save()
        return {"ok": ok, "state": "enabled",
                "reason": "" if ok else f"store save failed: {err}"}

    def disable(self, name, *, reason=""):
        entry = self.entries.get(name)
        if entry is None:
            return {"ok": False, "reason": "unknown extension"}
        if entry.get("state") == "revoked":
            return {"ok": False, "reason": "extension is already revoked"}
        entry["state"] = "disabled"
        self._log(name, "disabled", reason or "disabled")
        ok, err = self._save()
        return {"ok": ok, "state": "disabled",
                "reason": "" if ok else f"store save failed: {err}"}

    def revoke(self, name, *, reason=""):
        """Revocation is terminal: a revoked extension can never
        be re-approved, re-enabled, or re-registered (see
        register). This is deliberate — a revoked extension that
        could silently return would make the approval record
        meaningless."""
        entry = self.entries.get(name)
        if entry is None:
            return {"ok": False, "reason": "unknown extension"}
        entry["state"] = "revoked"
        self._log(name, "revoked", reason or "revoked")
        ok, err = self._save()
        return {"ok": ok, "state": "revoked",
                "reason": "" if ok else f"store save failed: {err}"}

    def extension_view(self, name):
        """The view check_action consumes: state + permissions +
        directory, nothing else (no manifest internals leak into
        the gate decision)."""
        entry = self.entries.get(name)
        if entry is None:
            return None
        manifest = entry.get("manifest", {})
        return {
            "state": entry.get("state"),
            "declared_permissions": manifest.get("declared_permissions", []),
            "directory": entry.get("directory"),
        }

    def status(self, name):
        entry = self.entries.get(name)
        if entry is None:
            return None
        return {"name": name, "state": entry.get("state"),
                "licence": entry.get("manifest", {}).get("licence"),
                "permissions": entry.get("manifest", {}).get("declared_permissions", []),
                "log": list(entry.get("log", []))}

"""Wave 7 Build 4 — Self-improvement PROPOSALS (Evolution Mode item 26 form).

A proposal is a proposed verified change with an exact diff, a
regression guard, and an evidence ledger — never a change applied
by itself. This is the proof of the no-self-deployment rule:
applying a proposal is a SEPARATE human C-14 action, outside
this module, and this module contains no code path that writes
a diff to a real file outside a throwaway dry-run copy.

Form, enforced in code (fail-closed whole: any refused part
refuses the WHOLE proposal, no partial proposal is returned):
  * Exact diff, file by file: each diff names a relative path,
    the old text and the new text, with SHA-256 hashes computed
    here, never trusted from the caller. A diff that changes
    nothing (old text == new text) is refused.
  * Regression guard: a non-empty list of NAMED tests that must
    pass before AND after any future human application. Guards
    are named, not invented: each guard must name a test
    (contain "test_"). This module does NOT run the guards and
    does NOT claim they pass — naming a guard is not passing
    it, and a proposal without a guard is refused.
  * Evidence ledger: a non-empty list of evidence entries, each
    with a kind, a checkable ref, and detail. A proposal without
    evidence is refused, never built on an assertion alone.
    Evidence is data, never an instruction, even when a line is
    phrased as one.

Core-modification boundary (item 1692), enforced in code, not
prose: a diff path whose first component is "core" or "data"
is a core-touching proposal. It is refused outright UNLESS it
carries a boundary check record naming exactly
"core-modification-boundary-1692" with a non-empty reason and
a non-empty checked_by. Even with that check, the result is
still only a proposal — the check permits proposing, never
applying. Credential targets (.env, assistant.db, .pem, .key)
are refused always, boundary check or not. Absolute paths,
drive paths, and ".." escapes are refused always.

Dry-run, reusing the SIM shape (Build 3): inspection applies
the diff to a throwaway TemporaryDirectory copy of the old
texts, verifies the resulting hashes equal the new hashes,
and discards the copy. Nothing the caller keeps is written,
nothing is promoted, nothing is recorded as done. Sentinel
files outside the copy are untouched by construction, because
the only writes this module performs are inside the
TemporaryDirectory.

M4-pattern reference only: the bounded-recovery pattern
(propose -> guard -> evidence -> human approval -> separate
action) is a pattern reference. M4 itself is NOT built here,
no auto-recovery, no self-healing execution, and M4 waits on
his green (Track M PROPOSED). No tool here plans, compiles,
promotes, or deploys anything — plan-approval gate intact,
no self-deployment, ever.

Own code, stdlib only. No third-party dependency is
introduced by this file (see gui/assets/
PROPOSALS_LICENCE_READ_W7.md).
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path

MAX_DIFFS = 50
MAX_GUARDS = 20
MAX_EVIDENCE = 50
MAX_TEXT_CHARS = 1_000_000
MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 2000

BOUNDARY_CHECK_NAME = "core-modification-boundary-1692"
_CORE_FIRST_COMPONENTS = {"core", "data"}
_FORBIDDEN_BASENAMES = {".env", "assistant.db"}
_FORBIDDEN_SUFFIXES = (".pem", ".key")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _fail(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def _path_reason(path: object) -> str | None:
    """Return a refusal reason for a bad path, else None."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return "diff path must be a non-empty string"
    if _DRIVE_RE.match(path):
        return f"drive path refused: {path}"
    probe = Path(path)
    if probe.is_absolute():
        return f"absolute path refused: {path}"
    if ".." in probe.parts:
        return f"path escape refused: {path}"
    if probe.name in _FORBIDDEN_BASENAMES:
        return f"credential target refused: {probe.name}"
    if probe.name.endswith(_FORBIDDEN_SUFFIXES):
        return f"credential target refused: {probe.name}"
    return None


def _is_core_path(path: str) -> bool:
    parts = Path(path).parts
    return bool(parts) and parts[0] in _CORE_FIRST_COMPONENTS


def _boundary_reason(boundary_check: object) -> str | None:
    """Validate a core-modification boundary check record."""
    if not isinstance(boundary_check, dict):
        return "core-touching proposal needs a boundary check record"
    if boundary_check.get("check") != BOUNDARY_CHECK_NAME:
        return (
            "core-touching proposal needs boundary check "
            f"'{BOUNDARY_CHECK_NAME}'"
        )
    if not isinstance(boundary_check.get("reason"), str) \
            or not boundary_check["reason"].strip():
        return "boundary check needs a non-empty reason"
    if not isinstance(boundary_check.get("checked_by"), str) \
            or not boundary_check["checked_by"].strip():
        return "boundary check needs a non-empty checked_by"
    return None


def _validate_diffs(diffs: object, boundary_check: object) -> tuple | tuple:
    """Validate diffs; return (diff_records, core_touching) or (None, reason)."""
    if not isinstance(diffs, list) or not diffs:
        return None, "diffs must be a non-empty list"
    if len(diffs) > MAX_DIFFS:
        return None, f"too many diffs (max {MAX_DIFFS})"
    seen: set[str] = set()
    records: list[dict] = []
    core_touching = False
    for item in diffs:
        if not isinstance(item, dict):
            return None, "each diff must be an object"
        path = item.get("path")
        reason = _path_reason(path)
        if reason:
            return None, reason
        if path in seen:
            return None, f"duplicate diff path refused: {path}"
        seen.add(path)
        old_text = item.get("old_text")
        new_text = item.get("new_text")
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            return None, f"diff {path}: old_text and new_text must be strings"
        if len(old_text) > MAX_TEXT_CHARS or len(new_text) > MAX_TEXT_CHARS:
            return None, f"diff {path}: text too large"
        if old_text == new_text:
            return None, f"diff {path}: old and new text are identical (no change)"
        if _is_core_path(path):
            core_touching = True
            breason = _boundary_reason(boundary_check)
            if breason:
                return None, breason
        records.append({
            "path": path,
            "old_text": old_text,
            "new_text": new_text,
            "old_sha256": _sha(old_text),
            "new_sha256": _sha(new_text),
            "core_path": _is_core_path(path),
        })
    return records, core_touching


def _validate_guards(guards: object) -> str | None:
    if not isinstance(guards, list) or not guards:
        return "regression_guards must be a non-empty list"
    if len(guards) > MAX_GUARDS:
        return f"too many regression guards (max {MAX_GUARDS})"
    seen: set[str] = set()
    for guard in guards:
        if not isinstance(guard, str) or not guard.strip():
            return "each regression guard must be a non-empty string"
        if "test_" not in guard:
            return f"regression guard must name a test: {guard!r}"
        if guard in seen:
            return f"duplicate regression guard refused: {guard}"
        seen.add(guard)
    return None


def _validate_evidence(evidence: object) -> str | None:
    if not isinstance(evidence, list) or not evidence:
        return "evidence must be a non-empty list"
    if len(evidence) > MAX_EVIDENCE:
        return f"too much evidence (max {MAX_EVIDENCE})"
    for entry in evidence:
        if not isinstance(entry, dict):
            return "each evidence entry must be an object"
        for field in ("kind", "ref", "detail"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                return f"evidence entry needs a non-empty {field}"
    return None


def build_proposal(title, summary, diffs, regression_guards, evidence,
                   boundary_check=None) -> dict:
    """Build (validate) a proposal. Returns it, or a refusal, whole.

    This function validates and records only. It writes no real
    file, promotes nothing, and records nothing as done. A
    returned proposal has deployed=False always.
    """
    if not isinstance(title, str) or not title.strip() \
            or len(title) > MAX_TITLE_CHARS:
        return _fail("title must be a non-empty string")
    if not isinstance(summary, str) or not summary.strip() \
            or len(summary) > MAX_SUMMARY_CHARS:
        return _fail("summary must be a non-empty string")
    diff_records, core_touching = _validate_diffs(diffs, boundary_check)
    if diff_records is None:
        return _fail(core_touching)  # type: ignore[arg-type]
    guard_reason = _validate_guards(regression_guards)
    if guard_reason:
        return _fail(guard_reason)
    evidence_reason = _validate_evidence(evidence)
    if evidence_reason:
        return _fail(evidence_reason)
    evidence_records = [
        {"kind": e["kind"], "ref": e["ref"], "detail": e["detail"]}
        for e in evidence
    ]
    canonical = json.dumps(
        {"title": title, "diffs": diff_records,
         "guards": list(regression_guards), "evidence": evidence_records},
        sort_keys=True)
    proposal = {
        "id": hashlib.sha256(canonical.encode()).hexdigest()[:16],
        "kind": "PROPOSAL",
        "status": "PROPOSED",
        "title": title,
        "summary": summary,
        "diffs": diff_records,
        "regression_guards": list(regression_guards),
        "evidence": evidence_records,
        "core_touching": core_touching,
        "boundary_check": (
            {"check": boundary_check["check"],
             "reason": boundary_check["reason"],
             "checked_by": boundary_check["checked_by"]}
            if core_touching and isinstance(boundary_check, dict) else None
        ),
        "deployed": False,
        "executes_nothing": True,
        "note": (
            "PROPOSAL ONLY, NOT APPLIED. Applying this proposal is a "
            "separate human C-14 action, outside this module. This "
            "module never applies, promotes, or deploys anything."
        ),
    }
    return {"ok": True, "proposal": proposal}


def _dry_run(proposal: dict) -> dict:
    """Apply the diff to a throwaway copy, verify hashes, discard it."""
    files: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="vyuk_proposal_") as tmp:
        root = Path(tmp)
        for diff in proposal["diffs"]:
            target = root / diff["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(diff["old_text"])
        for diff in proposal["diffs"]:
            target = root / diff["path"]
            target.write_text(diff["new_text"])
            actual = _sha(target.read_text())
            if actual != diff["new_sha256"]:
                return {"ok": False,
                        "reason": f"dry-run hash mismatch: {diff['path']}"}
            files.append({
                "path": diff["path"],
                "old_sha256": diff["old_sha256"],
                "new_sha256": diff["new_sha256"],
                "changed": True,
            })
    return {
        "ok": True,
        "kind": "DRY-RUN",
        "executed": False,
        "executes_nothing": True,
        "files": files,
        "temp_discarded": True,
        "note": "Dry-run copy discarded; no real file was written.",
    }


def inspect_proposal(proposal: object) -> dict:
    """Re-validate a proposal dict and dry-run it. Never applies it.

    A caller-supplied proposal dict is never trusted: it is
    re-built from its own diffs/guards/evidence through
    build_proposal, so a hand-edited proposal claiming hashes,
    guards, or evidence it does not have is refused or rebuilt
    honestly. Returns the rebuilt proposal plus the dry-run.
    """
    if not isinstance(proposal, dict):
        return _fail("proposal must be an object")
    rebuilt = build_proposal(
        proposal.get("title"), proposal.get("summary"),
        proposal.get("diffs"), proposal.get("regression_guards"),
        proposal.get("evidence"), proposal.get("boundary_check"))
    if not rebuilt.get("ok"):
        return rebuilt
    dry = _dry_run(rebuilt["proposal"])
    if not dry.get("ok"):
        return dry
    return {
        "ok": True,
        "proposal": rebuilt["proposal"],
        "dry_run": dry,
        "deployed": False,
        "executes_nothing": True,
    }


def proposal_from_json(text: object) -> dict:
    """Parse caller-supplied proposal JSON and inspect it (never trust it)."""
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        return _fail(f"proposal_json is not valid JSON: {exc}")
    return inspect_proposal(value)

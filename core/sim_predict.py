"""Wave 7 Build 3 — SIM graduation, ONE named, buildable slice.

Slice name: **plan-effect prediction** (a prediction before execution).

Study reason, in plain words: before a file plan is executed, show
the caller exactly what would change — which files would be created,
overwritten, or deleted — so a destructive plan can be refused
before it runs, not discovered after it. The prediction is made by
applying the caller-supplied plan to a COPY of a caller-supplied
snapshot inside a throwaway Wave 7 sandbox, diffing the copy, and
discarding the copy. Nothing the caller keeps is written, nothing
is promoted, nothing is recorded as done, and nothing is executed
outside the throwaway copy.

Why this slice clears its study reason, and the others do not:
  * Plan-effect prediction is deterministic and hand-checkable:
    apply the listed operations to the listed snapshot and diff.
    No probability, no invented model, no live state is needed.
  * Monte Carlo does NOT clear: the study material names no random
    variable and no distribution, so a Monte Carlo run would have
    to invent probabilities and present them as a prediction.
  * Counterfactual (P8) does NOT clear: it needs an alternative
    history with no ground truth, so it would present an invented
    past as a run, not a prediction before execution.
  * Digital twin does NOT clear: it needs a live mirrored system
    or device state that does not exist in this tree, so a twin
    without live state would be a fake.
  * Loan amortisation does NOT graduate here: it is already built
    as a calculation in core/skills_smart_chance.py, and it predicts no
    action plan — re-building it as SIM would duplicate it.
  * Mission dry-run (core/mission_contracts.py) is a precedent, not this
    slice: it checks contract preconditions and records nothing as
    done, but it predicts no file effects, and it is not wired as
    a tool here. This slice does not duplicate it: it predicts
    file effects of an already-supplied plan, it does not check
    a mission contract, and it does not plan, choose, or compile
    actions. Planning and compiling belong to P18 (Wave 7 Build 5),
    not to this slice.

Rules, enforced in code:
  * A prediction is a prediction: every result carries
    kind="PREDICTION", executed=False, executes_nothing=True, its
    inputs, its assumptions, and its uncertainty, never a claim
    that anything happened.
  * There is no executor argument anywhere in this module, and
    the module never imports an executor, a loop, or a promoter.
    A plan is proposed input, never an action taken.
  * Fail-closed whole: any refused operation (unknown kind,
    escaping path, forbidden target, oversize content, too many
    operations, delete of a file that is not in the snapshot)
    refuses the WHOLE prediction — no partial prediction is ever
    returned, so a caller can never act on a half-checked plan.
  * The copy is throwaway: it lives in a TemporaryDirectory, is
    diffed, and is deleted by the context manager. Sentinels are
    files OUTSIDE the copy, hashed before and after; a sentinel
    inside the copy is skipped, never treated as outside proof.
  * Forbidden targets, refused before any copy is made: absolute
    paths, ``..`` escapes, paths under a ``core/`` or ``data/``
    first component, basename ``.env`` or ``assistant.db``, and
    ``.pem`` / ``.key`` suffixes. The sandbox's own resolved-path
    checks (including symlink escapes) apply on top, because the
    copy is a real Wave 7 FileSystemSandbox.
  * Uncertainty is stated, never hidden: the prediction is only
    as current as the supplied snapshot — real files may have
    changed between snapshot and execution, and a prediction is
    not a guarantee about execution time.

Own code, stdlib only. No third-party dependency is introduced
by this file.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

try:
    from . import sandbox_filesystem as _sandbox_mod
except Exception:  # noqa: BLE001 — package-context fallback for tests
    from core import sandbox_filesystem as _sandbox_mod  # type: ignore

MAX_OPERATIONS = 100
MAX_SEED_FILES = 200
MAX_CONTENT_CHARS = 1_000_000
_FORBIDDEN_BASENAMES = {".env", "assistant.db"}
_FORBIDDEN_SUFFIXES = (".pem", ".key")
_FORBIDDEN_FIRST_COMPONENTS = {"core", "data"}
_ALLOWED_KINDS = ("write", "delete")

ASSUMPTIONS = (
    "The snapshot supplied by the caller is the before-state; "
    "files not in the snapshot are treated as not existing.",
    "Operations apply in list order; a later operation on the "
    "same path sees the result of the earlier one.",
    "The copy is a Wave 7 sandbox copy and is discarded after "
    "the diff; nothing is promoted, recorded, or executed.",
)
UNCERTAINTY = (
    "PREDICTION, not a fact: real files may differ from the "
    "snapshot by execution time, so re-predict from a fresh "
    "snapshot before acting on an old prediction.",
    "A prediction does not check whether the plan SHOULD run — "
    "only what it WOULD change if it ran unchanged.",
)


def _forbidden_reason(rel: object) -> str | None:
    """Return a refusal reason for a forbidden path, else None."""
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        return "path must be a non-empty string"
    probe = Path(rel)
    if probe.is_absolute():
        return "absolute paths refused"
    parts = probe.parts
    if parts and parts[0] in _FORBIDDEN_FIRST_COMPONENTS:
        return f"paths under {parts[0]}/ refused"
    if probe.name in _FORBIDDEN_BASENAMES:
        return f"forbidden target refused: {probe.name}"
    if probe.name.endswith(_FORBIDDEN_SUFFIXES):
        return "credential/key file refused"
    return None


def _refused(reason: str) -> dict:
    return {
        "ok": False,
        "reason": reason,
        "kind": "PREDICTION",
        "executed": False,
        "executes_nothing": True,
        "prediction": None,
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def predict_plan(seed, operations, sentinels=None):
    """Predict the file effects of ``operations`` on ``seed``.

    ``seed`` is a dict of relative path -> text content (the
    before-state). ``operations`` is a list of dicts:
    {"kind": "write", "path": rel, "content": str} or
    {"kind": "delete", "path": rel}. ``sentinels`` is an optional
    list of file paths OUTSIDE the copy, hashed before and
    after to prove they are unchanged. No executor is accepted:
    this function takes no executor argument and performs no
    real action — the copy it works on is discarded.
    """
    if not isinstance(seed, dict):
        return _refused("seed must be a dict of relative path -> text")
    if len(seed) > MAX_SEED_FILES:
        return _refused(f"seed has more than {MAX_SEED_FILES} files")
    if not isinstance(operations, list) or not operations:
        return _refused("operations must be a non-empty list")
    if len(operations) > MAX_OPERATIONS:
        return _refused(f"more than {MAX_OPERATIONS} operations refused")
    for rel, content in seed.items():
        reason = _forbidden_reason(rel)
        if reason:
            return _refused(f"seed path {rel!r}: {reason}")
        if not isinstance(content, str):
            return _refused(f"seed content for {rel!r} must be a string")
        if len(content) > MAX_CONTENT_CHARS:
            return _refused(f"seed content for {rel!r} exceeds the size cap")
    # Validate every operation before any copy is made.
    for action in operations:
        if not isinstance(action, dict):
            return _refused("operation must be a dict")
        kind = action.get("kind")
        if kind not in _ALLOWED_KINDS:
            return _refused(f"refused operation kind: {kind!r}")
        reason = _forbidden_reason(action.get("path"))
        if reason:
            return _refused(f"operation path {action.get('path')!r}: {reason}")
        if kind == "write":
            content = action.get("content")
            if not isinstance(content, str):
                return _refused("write operation needs string content")
            if len(content) > MAX_CONTENT_CHARS:
                return _refused("write content exceeds the size cap")

    sentinel_paths = []
    if sentinels:
        if not isinstance(sentinels, list):
            return _refused("sentinels must be a list")
        for item in sentinels:
            try:
                candidate = Path(item).expanduser().resolve()
            except (OSError, ValueError, RuntimeError):
                continue
            sentinel_paths.append(candidate)

    def _hash_file(path: Path):
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest(), True
        except OSError:
            return None, False

    before_sentinels = {str(p): _hash_file(p) for p in sentinel_paths}
    allowed = []
    refused = []
    try:
        with tempfile.TemporaryDirectory(prefix="vyuk_sim_") as tmp:
            sandbox = _sandbox_mod.FileSystemSandbox(tmp, seed=dict(seed))
            for action in operations:
                kind = action["kind"]
                rel = action["path"]
                if kind == "write":
                    result = sandbox.write(rel, action["content"])
                else:
                    result = sandbox.delete(rel)
                if result["ok"]:
                    allowed.append({"kind": kind, "path": result["path"]})
                else:
                    refused.append({"action": action,
                                    "reason": result["reason"]})
            if refused:
                # Fail-closed whole: no partial prediction.
                after = {str(p): _hash_file(p) for p in sentinel_paths}
                return {
                    "ok": False,
                    "reason": "prediction refused: "
                              + "; ".join(r["reason"] for r in refused),
                    "kind": "PREDICTION",
                    "executed": False,
                    "executes_nothing": True,
                    "prediction": None,
                    "refused": refused,
                    "sentinel_unchanged": before_sentinels == after
                    if sentinel_paths else None,
                }
            after_state = {}
            for rel in sandbox.list_files():
                read = sandbox.read(rel)
                if read["ok"]:
                    after_state[read["path"]] = read["content"]
    except Exception as exc:  # noqa: BLE001 — refusal, never a crash
        return _refused(f"{type(exc).__name__}: {exc}")

    after_sentinels = {str(p): _hash_file(p) for p in sentinel_paths}
    created, deleted, modified, unchanged = [], [], [], []
    for rel in sorted(set(seed) | set(after_state)):
        if rel not in seed:
            created.append({"path": rel,
                            "new_bytes": len(after_state[rel]),
                            "new_sha256": _sha(after_state[rel])})
        elif rel not in after_state:
            deleted.append({"path": rel,
                            "old_bytes": len(seed[rel]),
                            "old_sha256": _sha(seed[rel])})
        elif seed[rel] != after_state[rel]:
            modified.append({"path": rel,
                             "old_bytes": len(seed[rel]),
                             "new_bytes": len(after_state[rel]),
                             "old_sha256": _sha(seed[rel]),
                             "new_sha256": _sha(after_state[rel])})
        else:
            unchanged.append({"path": rel, "bytes": len(seed[rel])})
    prediction = {
        "kind": "PREDICTION",
        "executed": False,
        "executes_nothing": True,
        "label": "PREDICTION — NOT EXECUTED: nothing was written, "
                 "promoted, or recorded as done",
        "inputs": {
            "seed_files": sorted(seed),
            "operation_count": len(operations),
        },
        "assumptions": list(ASSUMPTIONS),
        "uncertainty": list(UNCERTAINTY),
        "allowed_operations": allowed,
        "effects": {
            "created": created,
            "deleted": deleted,
            "modified": modified,
            "unchanged": unchanged,
        },
        "summary": {
            "created": len(created),
            "deleted": len(deleted),
            "modified": len(modified),
            "unchanged": len(unchanged),
        },
        "sentinel_unchanged": before_sentinels == after_sentinels
        if sentinel_paths else None,
    }
    return {"ok": True, "kind": "PREDICTION", "executed": False,
            "executes_nothing": True, "prediction": prediction}

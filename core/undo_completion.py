"""Wave 7 Build 2 — UNDO completion (universal undo).

This COMPLETES the undo work, it does not replace it. The
shared undo store is still ``core/undo_store.py``'s ``UndoStore``,
reached through the shared gate ``core/resilience/resilience_gate.py``,
and the only producers before this build were
``core/undo_producers_p1.py``'s two: "setting changed" and
"file written". Nothing here creates a second undo store, a
second gate, or a second cannot-undo list — the disclosure
below EXTENDS the Wave 3 list, it does not fork it.

What this build adds, and nothing else:

  * Two new undoable change classes, each with a restorer
    that verifies by reading back, exactly as Perfection 1
    required for settings and files:
      - "sandbox file written" / "sandbox file deleted":
        a file inside a Wave 7 light sandbox, restored
        through ``FileSystemSandbox.resolve_inside`` — so a
        restore can never escape the sandbox, restore into
        core, or restore into the app data directory. The
        sandbox root must still exist at restore time; a
        vanished root is a failed restore, never a pretence.
      - "extension state changed": an extension harness
        transition (register / approve / enable / disable),
        restored by writing the recorded prior entry back
        into the harness's own store file and verifying by
        re-reading the state. The restorer refuses protected
        store paths (``assistant.db``, ``.env``) outright
        and never guesses a prior state — the prior entry
        is recorded at transition time, or the change is
        not recorded as undoable at all.
  * The upfront disclosure, extended: the Wave 3
    cannot-take-back list, plus two classes the app
    actually makes that cannot be taken back —
    "extension revoked" (terminal by harness design:
    re-approval and re-registration are refused) and
    "mission journal entry appended" (an audit record:
    removing it would falsify the audit trail). Both are
    recorded non-undoable explicitly, so ``undo_last``
    skips and lists them instead of pretending.
  * Production hooks, same pattern as the loop file-write
    hook: a change is recorded only when an undo store is
    configured in the session; when none is configured the
    change proceeds exactly as today, unrecorded, with no
    undo claim. Recording after a successful transition
    (extension, sandbox run) is deliberate: a failed
    transition must never leave an undo record for a
    change that did not happen. The strict settings/file
    producers keep their record-before pattern unchanged.

What is NOT undoable, and is never given a restorer:
  * Everything on the disclosure list (see
    :func:`cannot_undo_disclosure` — show it BEFORE a
    change, never after it).
  * A sandbox promotion performed outside the sandbox:
    promotion itself is request-only in the sandbox
    module, so there is no production promotion action
    for this build to undo; if a caller ever copies a
    file out, that copy is a real file write, undoable
    only if that write itself was recorded.
  * A change with no restorer, a vanished sandbox root,
    a corrupt harness store, or a tripped failsafe:
    ``undo_last`` reports the failure and preserves the
    record, never as undone.

Own code, stdlib only. No third-party dependency is
introduced by this file (see gui/assets/
UNDO_LICENCE_READ_W7.md).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

# Change classes this build makes undoable. The two
# Perfection 1 classes keep their names exactly, so the
# existing producers and the Doctor button keep working.
SANDBOX_FILE_WRITTEN = "sandbox file written"
SANDBOX_FILE_DELETED = "sandbox file deleted"
EXTENSION_STATE = "extension state changed"
EXTENSION_REVOKED = "extension revoked"
JOURNAL_APPENDED = "mission journal entry appended"

COVERED_CLASSES = (
    "setting changed",
    "file written",
    SANDBOX_FILE_WRITTEN,
    SANDBOX_FILE_DELETED,
    EXTENSION_STATE,
)

_CANNOT_REASONS = {
    "message sent to another person":
        "already left the machine; it cannot be recalled",
    "payment or purchase completed":
        "completed with a third party; it cannot be recalled",
    "file deleted with no backup and no trash copy":
        "the prior content is gone; there is nothing to restore",
    "message or post published externally":
        "already public outside the app; it cannot be unpublished",
    "system change that already restarted the machine":
        "the machine state already changed; it cannot be rewound",
    EXTENSION_REVOKED:
        "terminal by harness design: re-approval and "
        "re-registration of a revoked extension are refused",
    JOURNAL_APPENDED:
        "an audit record: removing it would falsify the audit trail",
}


def _gate():
    try:
        from .resilience import resilience_gate as gate  # Phase 3 R1: moved to core/resilience/
    except Exception:  # noqa: BLE001
        from core.resilience import resilience_gate as gate  # Phase 3 R1: moved to core/resilience/
    return gate


def _undo_w3():
    try:
        from . import undo_store as mod
    except Exception:  # noqa: BLE001
        from core import undo_store as mod
    return mod


def _new_change_id() -> str:
    return "undo-" + uuid.uuid4().hex[:12]


def cannot_undo_disclosure() -> dict:
    """The upfront cannot-take-back list — show BEFORE any
    change, never after it. The Wave 3 list, extended with
    the two classes this build adds. ``covered`` names the
    classes that CAN be taken back, with a verified restorer."""
    return {
        "cannot_undo": [
            {"class": name, "reason": _CANNOT_REASONS[name]}
            for name in list(_undo_w3().CANNOT_UNDO) + [
                EXTENSION_REVOKED, JOURNAL_APPENDED]
        ],
        "covered_undoable": [
            {"class": name,
             "note": "undoable with a verified restorer"}
            for name in COVERED_CLASSES
        ],
        "note": "Show this list before a change in one of these "
                "classes. A class on the cannot-undo list is "
                "never recorded as undoable, and undo skips "
                "and lists it instead of pretending.",
    }


def undo_status() -> dict:
    """Undo status: the store summary, the covered classes,
    and the cannot-undo list. Read-only — it changes nothing.
    When no undo store is configured, that is the honest
    answer, not a fabricated empty status."""
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    summary = gate.shared_undo_store().summary()
    last_undoable = None
    for entry in reversed(summary):
        if entry.get("can_be_taken_back") and not entry.get("taken_back"):
            last_undoable = entry
            break
    return {
        "ok": True,
        "records": summary,
        "last_undoable": last_undoable,
        "covered_undoable": list(COVERED_CLASSES),
        "cannot_undo": cannot_undo_disclosure()["cannot_undo"],
    }


# ---------------------------------------------------------------------------
# Restorers (registered in the shared gate, one per new class)
# ---------------------------------------------------------------------------


def _restore_sandbox(before) -> bool:
    """Restore a sandbox file, verified by reading back.

    ``before`` carries the sandbox root, the relative path,
    and either the prior content or the missing marker.
    Resolution goes through FileSystemSandbox, so a restore
    can never escape the sandbox or land in core/data.
    """
    try:
        if not isinstance(before, dict):
            return False
        root = before.get("sandbox_root")
        rel = before.get("rel")
        if not root or not rel:
            return False
        try:
            from . import sandbox_filesystem as sandbox_mod
        except Exception:  # noqa: BLE001
            from core import sandbox_filesystem as sandbox_mod
        sandbox = sandbox_mod.FileSystemSandbox(root)
        if before.get("missing"):
            result = sandbox.delete(rel)
            # Deleting a file that is already absent is the
            # restored state, not a failure: verify absence.
            if not result["ok"] and "no such file" not in result["reason"]:
                return False
            return sandbox.read(rel)["ok"] is False
        content = before.get("content")
        if not isinstance(content, str):
            return False
        if not sandbox.write(rel, content)["ok"]:
            return False
        read = sandbox.read(rel)
        return read["ok"] and read.get("content") == content
    except Exception:  # noqa: BLE001 — a restorer failure is a failure
        return False


_PROTECTED_STORE_NAMES = frozenset({"assistant.db", ".env"})


def _restore_extension(before) -> bool:
    """Restore an extension harness entry, verified by re-read.

    ``before`` carries the harness store path, the extension
    name, and the prior entry (or the missing marker when
    the extension did not exist). The prior entry is written
    back atomically, with an undo event appended to its log,
    and the state is verified by re-reading the store.

    One production trap, handled here and not left to the
    caller: the tools and the Doctor card share ONE harness
    instance holding entries in memory. Editing the store
    file alone would leave that instance stale, and its
    next save would overwrite the undo. So when the shared
    harness instance exists and its store path matches,
    the restorer mutates that instance and saves through
    it; the direct file edit is only the fallback for a
    harness that was never shared (tests, one-off loads).
    """
    try:
        if not isinstance(before, dict):
            return False
        store_path = before.get("store_path")
        name = before.get("name")
        if not store_path or not name:
            return False
        if os.path.basename(str(store_path)) in _PROTECTED_STORE_NAMES:
            return False
        shared = None
        try:
            from . import integration_harness_tools as _w6
        except Exception:  # noqa: BLE001
            try:
                from core import integration_harness_tools as _w6
            except Exception:  # noqa: BLE001
                _w6 = None
        if _w6 is not None:
            candidate = getattr(_w6, "_shared_harness", None)
            if candidate is not None and \
                    getattr(_w6, "_shared_harness_path", None) == str(store_path):
                shared = candidate
        if shared is not None:
            if before.get("missing"):
                shared.entries.pop(name, None)
                ok, _err = shared._save()
                return bool(ok) and shared.status(name) is None
            prior = before.get("prior_entry")
            if not isinstance(prior, dict) or "state" not in prior:
                return False
            entry = copy.deepcopy(prior)
            log = entry.setdefault("log", [])
            if not isinstance(log, list):
                entry["log"] = log = []
            log.append({"at": datetime.now(timezone.utc).isoformat(),
                        "event": "undo-restored",
                        "reason": "undo restored the prior entry"})
            shared.entries[name] = entry
            ok, _err = shared._save()
            if not ok:
                return False
            status = shared.status(name)
            return isinstance(status, dict) and \
                status.get("state") == prior["state"]
        with open(store_path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(
                data.get("entries"), dict):
            return False
        entries = data["entries"]
        if before.get("missing"):
            entries.pop(name, None)
            expected_state = None
        else:
            prior = before.get("prior_entry")
            if not isinstance(prior, dict) or "state" not in prior:
                return False
            entry = copy.deepcopy(prior)
            log = entry.setdefault("log", [])
            if not isinstance(log, list):
                entry["log"] = log = []
            log.append({"at": datetime.now(timezone.utc).isoformat(),
                        "event": "undo-restored",
                        "reason": "undo restored the prior entry"})
            entries[name] = entry
            expected_state = prior["state"]
        directory = os.path.dirname(os.path.abspath(store_path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ext_undo_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
            os.replace(tmp, store_path)
        except Exception:  # noqa: BLE001
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False
        with open(store_path, encoding="utf-8") as handle:
            check = json.load(handle)
        actual = check.get("entries", {}).get(name)
        if expected_state is None:
            return actual is None
        return isinstance(actual, dict) and actual.get("state") == expected_state
    except Exception:  # noqa: BLE001 — a restorer failure is a failure
        return False


def ensure_undo_completion_registered() -> dict:
    """Register every undo restorer in the shared gate
    (idempotent): the two Perfection 1 producers, plus the
    sandbox and extension restorers from this build."""
    try:
        from . import undo_producers_p1 as prod
    except Exception:  # noqa: BLE001
        from core import undo_producers_p1 as prod
    prod.ensure_producers_registered()
    gate = _gate()
    gate.register_undo_restorer(SANDBOX_FILE_WRITTEN, _restore_sandbox)
    gate.register_undo_restorer(SANDBOX_FILE_DELETED, _restore_sandbox)
    gate.register_undo_restorer(EXTENSION_STATE, _restore_extension)
    return {"ok": True,
            "classes": list(COVERED_CLASSES)}


def reset_undo_completion_for_tests() -> None:
    """No module-level state of its own to clear (the gate
    and the producers own theirs); kept for symmetry with
    the producers' test reset."""
    return None


# ---------------------------------------------------------------------------
# Producers — sandbox files
# ---------------------------------------------------------------------------


def snapshot_sandbox_priors(sandbox, actions) -> dict:
    """Snapshot prior content for write/delete actions, BEFORE
    a sandbox run. A prior is {"content": ...} or
    {"missing": True} — never a guess. Refused/malformed
    actions get no snapshot and are never recorded."""
    priors = {}
    if not isinstance(actions, list):
        return priors
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("kind") not in ("write", "delete"):
            continue
        rel = action.get("path")
        if not isinstance(rel, str) or rel in priors:
            continue
        read = sandbox.read(rel)
        if read["ok"]:
            priors[rel] = {"content": read["content"]}
        else:
            priors[rel] = {"missing": True}
    return priors


def record_sandbox_run(sandbox_root, report, priors) -> dict:
    """Record the ALLOWED write/delete actions of a completed
    sandbox run, one undo record each, with the snapshot
    priors. Refused actions are never recorded. When no undo
    store is configured, nothing is recorded and the run
    stands exactly as it ran — no undo claim is made."""
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False, "recorded": 0,
                "reason": "no undo store configured in this session"}
    if not isinstance(report, dict):
        return {"ok": False, "recorded": 0, "reason": "no run report"}
    recorded = 0
    for allowed in report.get("allowed", []):
        kind = allowed.get("kind")
        rel = allowed.get("path")
        if kind not in ("write", "delete") or rel not in priors:
            continue
        prior = priors[rel]
        before = {"sandbox_root": str(sandbox_root), "rel": rel}
        before.update(prior)
        change_class = (SANDBOX_FILE_WRITTEN if kind == "write"
                        else SANDBOX_FILE_DELETED)
        after = {"sandbox_root": str(sandbox_root), "rel": rel}
        result = gate.record_undo_change(
            _new_change_id(), change_class=change_class,
            before=before, after=after)
        if result.get("ok"):
            recorded += 1
    return {"ok": True, "recorded": recorded}


def write_sandbox_file_with_undo(sandbox, rel, content,
                                 change_id=None) -> dict:
    """Strict sandbox-write producer: snapshot, record, then
    write. When no undo store is configured, nothing is
    written — a write advertised as undoable must actually
    be undoable."""
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    priors = snapshot_sandbox_priors(
        sandbox, [{"kind": "write", "path": rel}])
    before = {"sandbox_root": str(sandbox.root), "rel": rel}
    before.update(priors.get(rel, {"missing": True}))
    result = gate.record_undo_change(
        change_id or _new_change_id(), change_class=SANDBOX_FILE_WRITTEN,
        before=before,
        after={"sandbox_root": str(sandbox.root), "rel": rel})
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason", "record failed")}
    written = sandbox.write(rel, content)
    if not written["ok"]:
        return {"ok": False, "reason": written["reason"],
                "change_id": result["record"]["id"]}
    return {"ok": True, "change_id": result["record"]["id"],
            "before": before}


def delete_sandbox_file_with_undo(sandbox, rel,
                                  change_id=None) -> dict:
    """Strict sandbox-delete producer: the prior content is
    the restoration, so a file that does not exist is
    refused, never recorded as a deletion of nothing."""
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    priors = snapshot_sandbox_priors(
        sandbox, [{"kind": "delete", "path": rel}])
    prior = priors.get(rel)
    if prior is None or prior.get("missing"):
        return {"ok": False,
                "reason": f"no such file in sandbox: {rel}"}
    before = {"sandbox_root": str(sandbox.root), "rel": rel}
    before.update(prior)
    result = gate.record_undo_change(
        change_id or _new_change_id(), change_class=SANDBOX_FILE_DELETED,
        before=before,
        after={"sandbox_root": str(sandbox.root), "rel": rel})
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason", "record failed")}
    deleted = sandbox.delete(rel)
    if not deleted["ok"]:
        return {"ok": False, "reason": deleted["reason"],
                "change_id": result["record"]["id"]}
    return {"ok": True, "change_id": result["record"]["id"],
            "before": before}


# ---------------------------------------------------------------------------
# Producers — extension transitions, and irreversible records
# ---------------------------------------------------------------------------


def record_extension_transition(store_path, name, prior_entry,
                                after_state, *, change_id=None) -> dict:
    """Record an extension transition AFTER it succeeds, with
    the prior entry captured before it. ``prior_entry`` is
    the full harness entry dict, or None when the extension
    did not exist. A failed transition must never be recorded
    — the caller records only on success, so this function
    refuses a non-dict prior that is not None, and refuses
    when no undo store is configured (the transition stands,
    unrecorded, exactly as today)."""
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    if prior_entry is not None and not isinstance(prior_entry, dict):
        return {"ok": False, "reason": "prior entry must be a dict or None"}
    before = {"store_path": str(store_path), "name": name}
    if prior_entry is None:
        before["missing"] = True
    else:
        before["prior_entry"] = copy.deepcopy(prior_entry)
    result = gate.record_undo_change(
        change_id or _new_change_id(), change_class=EXTENSION_STATE,
        before=before,
        after={"store_path": str(store_path), "name": name,
               "state": after_state})
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason", "record failed")}
    return {"ok": True, "change_id": result["record"]["id"],
            "before": before}


def note_irreversible_change(change_id, *, change_class) -> dict:
    """Record a change as explicitly non-undoable, so undo
    skips and lists it. Used for "extension revoked" and
    "mission journal entry appended": neither is on the
    Wave 3 forced list, so the explicit flag is what keeps
    them honest. No restorer is ever registered for them."""
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    store = gate.shared_undo_store()
    entry = store.record(str(change_id), change_class=change_class,
                         before=None, after=None, undoable=False)
    return {"ok": True, "record": entry, "undoable": False}


def undo_last_change() -> dict:
    """Undo the last undoable recorded change, through the
    shared gate (which confirm-gates at the tool funnel and
    refuses on a tripped failsafe). Callers that are not
    already confirm-gated must not call this directly."""
    return _gate().undo_last(approved=True)

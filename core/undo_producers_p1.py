"""Code-Perfection Build 1 — undo producers + the shared restorer set.

Wiring Build 3 left the Doctor "Undo last change" button wired to
a mechanism with no producers: no production caller recorded an
undo change or registered a restorer, so the button honestly
reported "no undoable change recorded" forever. This module is
the producer side, Qt-free (stdlib only), built on the shared
gate (``core/resilience/resilience_gate.py``) and its ``UndoStore``.

Two real reversible producers, and only two — no guessed
restorer for anything irreversible:

  * "setting changed" — a settings change made through the app.
    The producer works on a mapping-like store (a dict, or a
    GUI adapter whose ``__setitem__``/``__delitem__`` write the
    real backing store). The change is recorded BEFORE it is
    applied, with the previous value (or an explicit "missing"
    marker); the restorer puts that value back, or deletes the
    key when it did not exist, and verifies by reading back.
    The strict producer (``apply_setting_change``) refuses and
    changes nothing when no undo store is configured or the
    value is not JSON-serialisable — a change advertised as
    undoable must actually be undoable.
  * "file written" — a file write through the app's existing
    file-write path (the loop's ``write_file`` tool, see the
    hook in ``core/funnel/tools_handlers.py``). The restorer restores
    the previous file content, or deletes the file when it did
    not exist, and verifies by reading back. Append is restored
    by restoring the whole previous content, not by guessing
    how many bytes were appended.

Irreversible actions are never given a restorer and never
recorded as undoable: ``note_irreversible`` records them with
a class on the cannot-undo list (a sent message, a completed
purchase, a published post, …), so the undo button skips and
lists them instead of pretending. Anything with no restorer at
all is likewise never recorded as undoable by these producers.

One live-settings-store caveat, stated plainly: the settings
restorer finds its store through a registry keyed by a store id
carried in the record, because the gate's restorer receives
only the recorded before-state. The registry holds a strong
reference, so a registered store cannot be garbage-collected
and its id reused. The app has one settings backing store in
practice; tests register their own stores and clear the
registry with :func:`reset_producers_for_tests`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid

SETTING_CLASS = "setting changed"
FILE_CLASS = "file written"

# Batch 0 (click-anywhere, L-60): a file restore that fails its
# verification disables the file producer — the store never records
# writes it cannot restore (§6.2). The flag is set only by a genuine
# restore attempt that fails verification, never by an expiry
# refusal (an expired entry is normal lifecycle, not a proof
# failure). Re-enable via reenable_file_undo() (Doctor page) or
# reset_producers_for_tests() (tests).
_FILE_RESTORE_DISABLED = False
_FILE_UNDO_EXPIRY_S = 7 * 24 * 3600  # 7 days (§6.2)

SETTING_CLASS = "setting changed"
FILE_CLASS = "file written"

_MISSING = "__undo_missing__"

# store_id -> mapping-like store object (strong reference, see
# the module docstring). Keyed by a generated id, never by
# id(store), so reuse after collection cannot misroute a restore.
_SETTING_STORES: dict = {}
_SETTING_COUNTER = [0]


def _new_change_id() -> str:
    return "undo-" + uuid.uuid4().hex[:12]


def _gate():
    try:
        from .resilience import resilience_gate as gate  # Phase 3 R1: moved to core/resilience/
    except Exception:  # noqa: BLE001
        from core.resilience import resilience_gate as gate  # Phase 3 R1: moved to core/resilience/
    return gate


def _jsonable(value) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# Restorers (registered in the shared gate, one per change class)
# ---------------------------------------------------------------------------


def _restore_setting(before) -> bool:
    """Restore a settings value, verified by reading back."""
    try:
        if not isinstance(before, dict):
            return False
        store_id = before.get("store_id")
        key = before.get("key")
        store = _SETTING_STORES.get(store_id)
        if store is None or key is None:
            return False
        if before.get("missing"):
            if key in store:
                del store[key]
            return key not in store
        value = before.get("value")
        store[key] = value
        try:
            return store[key] == value
        except Exception:  # noqa: BLE001 — unreadable store, not verified
            return False
    except Exception:  # noqa: BLE001 — a restorer failure is a failure
        return False


def _restore_file(before) -> bool:
    """Restore a file's previous BYTES, verified by reading back.

    Batch 0 (L-60): the record carries base64 bytes, and the
    restore writes bytes (``"wb"``) — never text mode, so no
    platform newline translation can corrupt the restored
    content. A legacy text record (``"content"`` str, pre-Batch
    0) restores as its UTF-8 bytes. Expiry (§6.2): an entry
    older than 7 days, or whose file was written again since
    the change was recorded (current bytes no longer match the
    recorded after-state), is refused with a named error —
    never restored, never deleted. A restore that is attempted
    and fails verification disables the file producer (see
    _FILE_RESTORE_DISABLED)."""
    global _FILE_RESTORE_DISABLED
    try:
        if not isinstance(before, dict):
            return False
        path = before.get("path")
        if not path:
            return False
        # Expiry (§6.2) — checked before any restore is attempted,
        # so an expiry refusal never disables the producer.
        recorded_at = before.get("recorded_at")
        if isinstance(recorded_at, (int, float)):
            if time.time() - float(recorded_at) > _FILE_UNDO_EXPIRY_S:
                raise ValueError(
                    "undo entry expired: recorded more than 7 days ago")
        after_sha = before.get("after_sha256")
        current = None
        if os.path.exists(path):
            try:
                with open(path, "rb") as handle:
                    current = handle.read()
            except OSError:
                current = None
        if after_sha is not None and current is not None:
            if hashlib.sha256(current).hexdigest() != after_sha:
                raise ValueError(
                    "undo entry expired: the file was written again "
                    "since this change was recorded")
        if before.get("missing"):
            if os.path.exists(path):
                os.remove(path)
            ok = not os.path.exists(path)
        elif "content_b64" in before:
            prior = base64.b64decode(before["content_b64"])
            with open(path, "wb") as handle:
                handle.write(prior)
            with open(path, "rb") as handle:
                ok = handle.read() == prior
        else:
            content = before.get("content")
            if not isinstance(content, str):
                return False
            prior = content.encode("utf-8")
            with open(path, "wb") as handle:
                handle.write(prior)
            with open(path, "rb") as handle:
                ok = handle.read() == prior
        if not ok:
            # A restore that was attempted and failed its
            # verification disables the file producer (§6.2).
            _FILE_RESTORE_DISABLED = True
        return bool(ok)
    except ValueError:
        # Expiry refusals (above) are named, not proof failures.
        raise
    except Exception:  # noqa: BLE001 — a restorer failure is a failure
        _FILE_RESTORE_DISABLED = True
        return False


def file_undo_disabled() -> bool:
    """True when a failed file restore has disabled the producer."""
    return _FILE_RESTORE_DISABLED


def reenable_file_undo() -> None:
    """Re-enable the file producer after a restore-proof failure
    (Doctor page). Never called by production code on its own."""
    global _FILE_RESTORE_DISABLED
    _FILE_RESTORE_DISABLED = False


def ensure_producers_registered() -> dict:
    """Register both restorers in the shared gate (idempotent)."""
    gate = _gate()
    gate.register_undo_restorer(SETTING_CLASS, _restore_setting)
    gate.register_undo_restorer(FILE_CLASS, _restore_file)
    return {"ok": True, "classes": [SETTING_CLASS, FILE_CLASS]}


def reset_producers_for_tests() -> None:
    """Clear the settings-store registry (tests only)."""
    global _FILE_RESTORE_DISABLED
    _SETTING_STORES.clear()
    _SETTING_COUNTER[0] = 0
    _FILE_RESTORE_DISABLED = False


def _register_setting_store(store) -> str:
    _SETTING_COUNTER[0] += 1
    store_id = "settings-%d" % _SETTING_COUNTER[0]
    _SETTING_STORES[store_id] = store
    return store_id


# ---------------------------------------------------------------------------
# Producer 1 — settings change
# ---------------------------------------------------------------------------


def apply_setting_change(store, key, new_value, *, change_id=None) -> dict:
    """Record a settings change, then apply it. Strict: when no
    undo store is configured, or the value cannot be stored in
    the JSON undo record, nothing is changed and the reason
    says so — the change must not happen unrecorded."""
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    if not _jsonable(new_value):
        return {"ok": False,
                "reason": "settings value is not JSON-serialisable, "
                          "so it cannot be recorded for undo"}
    try:
        if key in store:
            before = {"key": key, "value": store[key]}
        else:
            before = {"key": key, "missing": True}
    except Exception as exc:  # noqa: BLE001 — unreadable store, no change
        return {"ok": False, "reason": f"settings store unreadable: {exc}"}
    if not _jsonable(before):
        return {"ok": False,
                "reason": "previous settings value is not "
                          "JSON-serialisable, so it cannot be recorded"}
    store_id = _register_setting_store(store)
    before = dict(before)
    before["store_id"] = store_id
    after = {"key": key, "value": new_value, "store_id": store_id}
    result = gate.record_undo_change(change_id or _new_change_id(),
                                     change_class=SETTING_CLASS,
                                     before=before, after=after)
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason", "record failed")}
    try:
        store[key] = new_value
    except Exception as exc:  # noqa: BLE001 — recorded, but not applied
        return {"ok": False, "reason": f"settings store write failed: {exc}",
                "change_id": result["record"]["id"]}
    return {"ok": True, "change_id": result["record"]["id"],
            "before": before, "after": after}


# ---------------------------------------------------------------------------
# Producer 2 — file write
# ---------------------------------------------------------------------------


def _read_prior_text(path: str):
    """Previous file content, or the missing marker (never a guess)."""
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                return {"content": handle.read()}
    except Exception:  # noqa: BLE001 — unreadable prior reads as absent
        pass
    return {"missing": True}


def _read_prior_bytes(path: str):
    """Previous file BYTES, or the missing marker (never a guess).

    Batch 0 (L-60): the undo record carries bytes, not text — a
    non-UTF-8 file is recorded as its bytes, not misread as
    missing (the old text reader swallowed decode errors and
    returned "missing", so a restore would have DELETED it)."""
    try:
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                return {"content_b64": base64.b64encode(
                    handle.read()).decode("ascii")}
    except Exception:  # noqa: BLE001 — unreadable prior reads as absent
        pass
    return {"missing": True}


def record_file_write_bytes(path: str, *, new_bytes: bytes,
                            encoding: str, change_id=None) -> dict:
    """Record a file write BEFORE it happens, in BYTES (Batch 0).

    The ``before`` carries the pre-write bytes (base64 — the JSON
    undo store holds text only) or ``{"missing": True}`` for a new
    file, whose undo DELETES the file; the ``after`` carries the
    exact bytes the write will lay down plus the encoding, the
    after-state hash, and the record time — the expiry inputs
    (§6.2: 7 days, or the file written again since). Returns ok
    False when no undo store is configured or the file producer
    is disabled after a failed restore — the caller then writes
    as today, with no record and no undo claim, rather than
    breaking the write."""
    global _FILE_RESTORE_DISABLED
    gate = _gate()
    if _FILE_RESTORE_DISABLED:
        return {"ok": False,
                "reason": "file undo disabled: a restore failed "
                          "verification — re-enable from the Doctor page"}
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    prior = _read_prior_bytes(path)
    before = {"path": str(path)}
    before.update(prior)
    before["after_sha256"] = hashlib.sha256(new_bytes).hexdigest()
    before["after_size"] = len(new_bytes)
    before["recorded_at"] = time.time()
    after = {"path": str(path),
             "content_b64": base64.b64encode(new_bytes).decode("ascii"),
             "encoding": encoding}
    result = gate.record_undo_change(change_id or _new_change_id(),
                                     change_class=FILE_CLASS,
                                     before=before, after=after)
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason", "record failed")}
    return {"ok": True, "change_id": result["record"]["id"],
            "before": before, "after": after}


def record_file_write(path: str, *, new_content: str,
                      change_id=None) -> dict:
    """Record a file write BEFORE it happens (the loop tool's
    hook). Returns ok False when no undo store is configured —
    the caller then writes as today, with no record and no
    undo claim, rather than breaking the write."""
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    prior = _read_prior_text(path)
    before = {"path": str(path)}
    before.update(prior)
    after = {"path": str(path), "content": new_content}
    result = gate.record_undo_change(change_id or _new_change_id(),
                                     change_class=FILE_CLASS,
                                     before=before, after=after)
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason", "record failed")}
    return {"ok": True, "change_id": result["record"]["id"],
            "before": before, "after": after}


def write_file_with_undo(path: str, content: str, *, append: bool = False,
                         change_id=None) -> dict:
    """Strict file producer: record first, then write. When no
    undo store is configured, nothing is written — a write
    advertised as undoable must actually be undoable. The
    after-state is the full resulting BYTES, so an append is
    restored by restoring the whole previous content. Batch 0
    (L-60): bytes throughout — the encoding is detected from
    the prior bytes, the write is bytes, the verify is bytes."""
    try:
        from . import file_bytes as _fb
    except Exception:  # noqa: BLE001
        from core import file_bytes as _fb
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    try:
        prior_bytes = None
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                prior_bytes = handle.read()
    except Exception:  # noqa: BLE001 — unreadable prior reads as absent
        prior_bytes = None
    try:
        encoding = _fb.detect_encoding(prior_bytes)
    except Exception as exc:  # noqa: BLE001 — undetectable: refuse
        return {"ok": False, "reason": str(exc)}
    if append and prior_bytes is not None:
        new_bytes = (prior_bytes
                     + _fb.encode_text(content, encoding,
                                       include_bom=False))
    else:
        new_bytes = _fb.encode_text(
            content, encoding,
            include_bom=(prior_bytes is not None
                         and _fb.has_bom(prior_bytes)))
    recorded = record_file_write_bytes(path, new_bytes=new_bytes,
                                       encoding=encoding,
                                       change_id=change_id)
    if not recorded.get("ok"):
        return recorded
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        # C3-F5 (2026-11-20): the write is atomic — temp file in
        # the same directory + os.replace, the scheduler /
        # skills_text pattern — so a failure mid-write (ENOSPC)
        # leaves the old file byte-identical (or no file at all
        # for a new path), never a truncated partial the undo
        # record would then disagree with. The temp file is
        # removed on any failure.
        import tempfile
        fd, tmp_name = tempfile.mkstemp(
            dir=parent, prefix=os.path.basename(path) + ".",
            suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(new_bytes)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        with open(path, "rb") as handle:
            if handle.read() != new_bytes:
                return {"ok": False,
                        "reason": "file write failed verification",
                        "change_id": recorded["change_id"]}
    except Exception as exc:  # noqa: BLE001 — recorded, but not written
        return {"ok": False, "reason": f"file write failed: {exc}",
                "change_id": recorded["change_id"]}
    return {"ok": True, "change_id": recorded["change_id"],
            "before": recorded["before"], "after": recorded["after"]}


# ---------------------------------------------------------------------------
# Irreversible actions — recorded, never undoable
# ---------------------------------------------------------------------------


def note_irreversible(change_id, *, change_class, before=None,
                      after=None) -> dict:
    """Record an irreversible action so the undo button can skip
    and list it. Classes on the cannot-undo list are forced
    non-undoable by the store itself; any other class given here
    is recorded non-undoable explicitly. No restorer is ever
    registered for these classes by this module."""
    gate = _gate()
    if gate.shared_undo_store() is None:
        return {"ok": False,
                "reason": "no undo store configured in this session"}
    store = gate.shared_undo_store()
    entry = store.record(str(change_id), change_class=change_class,
                         before=before, after=after, undoable=False)
    return {"ok": True, "record": entry, "undoable": False}

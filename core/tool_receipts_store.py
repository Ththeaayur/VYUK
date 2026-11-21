"""ReceiptStore class, whole (skills split B17).

Moved verbatim from core/tool_receipts.py. No behaviour change.
"""

from __future__ import annotations  # INTENTIONAL (skills split B17): old module carried it (B1 lesson).

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


# INTENTIONAL (skills split B17): names the moved ReceiptStore methods
# resolve by bare name, imported from the module that now owns them.
from .tool_receipts_normalize import (
    CRITICAL_RECEIPT_TIER,
    _MAX_LINES,
    _MAX_PREVIEW_CHARS,
    _MAX_SUMMARY_CHARS,
    _attempt_signatures,
    _canonical_args,
    _sanitize,
    _validate_compensations,
    _validate_manual_steps,
    default_receipts_path,
    effect_signatures,
)


# Round-3 section D fix queue item D-F2: prune-with-archive.
# _prune used to destroy every row past _MAX_LINES with no log
# line and no marker anywhere — the audit trail for effectful
# acts silently lost 95% of itself on a long-lived install. The
# dropped rows are now written to a dated archive file first
# (tool_receipts_archive/ next to the live file), and the event
# is kept in sticky per-process state so the Activity feed can
# say where the older rows went. If the archive write itself
# fails, the load still succeeds and the prune still runs; the
# failure is logged and recorded here too — the loss is
# announced either way, never silent.
_ARCHIVE_DIRNAME = "tool_receipts_archive"
_PRUNE_ARCHIVE: dict[str, dict] = {}


def _note_prune_archive(kind: str, *, count: int,
                        archive_path: str | None = None,
                        error: str | None = None) -> None:
    """Sticky record of one prune event ("archived" or
    "archive_failed"): row count + archive path (or error) +
    first/last time. Never raises."""
    try:
        now = time.time()
        key = archive_path if kind == "archived" and archive_path \
            else kind
        rec = _PRUNE_ARCHIVE.get(key)
        if rec is None:
            rec = {"count": 0, "archive_path": archive_path or "",
                   "last_error": "", "first_at": now, "last_at": now}
            _PRUNE_ARCHIVE[key] = rec
        rec["count"] = int(rec.get("count", 0)) + int(count)
        if archive_path:
            rec["archive_path"] = archive_path
        if error:
            rec["last_error"] = str(error)
        rec["last_at"] = now
    except Exception:  # noqa: BLE001 — bookkeeping never breaks
        pass


def prune_archive_state() -> dict:
    """Copies of the sticky prune-archive state: one record per
    archive event (keyed by archive path) plus one accumulated
    "archive_failed" record when an archive write has failed.
    Empty dict means no prune has archived or failed yet."""
    return {key: dict(rec) for key, rec in _PRUNE_ARCHIVE.items()}


def reset_prune_archive_state() -> None:
    """Clear the sticky prune-archive state. Tests only:
    production never forgets a prune."""
    _PRUNE_ARCHIVE.clear()


class ReceiptStore:
    """Append-only JSONL receipt store."""

    def __init__(self, path: Path | None = None,
                 max_lines: int = _MAX_LINES):
        if max_lines < 1:
            raise ValueError("max_lines must be at least 1")
        self.path = Path(path) if path is not None \
            else default_receipts_path()
        self._max_lines = max_lines
        self._loaded = False
        self._load_error = ""
        # receipt_id -> attempt record (dict); unknown COUNTS keyed
        # by (tool, args_signature) for the guard. Counts, not a
        # set: several attempts may share one signature, so one
        # attempt resolving must drop one count, never the pair
        # while another attempt still guards it (regression batch
        # R2 follow-up: a set discard unguarded a still-unknown
        # marked attempt when a same-signature call resolved).
        self._attempts: dict[str, dict] = {}
        self._unknown: dict[tuple[str, str], int] = {}

    # -- load ---------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:  # noqa: BLE001 — corrupt file is loud
            self._load_error = (
                f"tool receipts unreadable at {self.path}: {exc}")
            raise ValueError(self._load_error)
        records: list[dict] = []
        for lineno, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError as exc:
                self._load_error = (
                    f"tool receipts corrupt at {self.path}:{lineno}: {exc}")
                raise ValueError(self._load_error)
            if not isinstance(rec, dict) or rec.get("type") not in (
                    "attempt", "outcome", "dispatched"):
                self._load_error = (
                    f"tool receipts malformed at {self.path}:{lineno}: "
                    "expected an attempt/outcome/dispatched record")
                raise ValueError(self._load_error)
            records.append(rec)
        if len(lines) > self._max_lines:
            self._prune(lines)
            # The file now holds only the recent lines; the
            # in-memory state must agree, not outlive the file.
            records = records[-self._max_lines:]
        for rec in records:
            if rec["type"] == "attempt":
                self._attempts[str(rec.get("receipt_id", ""))] = rec
                for sig in _attempt_signatures(rec):
                    _key = (str(rec.get("tool", "")), sig)
                    self._unknown[_key] = self._unknown.get(_key, 0) + 1
            elif rec["type"] == "dispatched":
                _marked = self._attempts.get(
                    str(rec.get("receipt_id", "")))
                if _marked is not None:
                    _marked["dispatched"] = True
            else:
                rid = str(rec.get("receipt_id", ""))
                attempt = self._attempts.get(rid)
                if attempt is not None:
                    attempt["status"] = str(rec.get("status", ""))
                    attempt["outcome_summary"] = str(
                        rec.get("summary", ""))
                    # Old records have no "dispatched" field and
                    # read as dispatched=True (the safe side:
                    # a cancelled record of unknown provenance
                    # blocks re-execution like an unknown one).
                    attempt["dispatched"] = bool(
                        rec.get("dispatched", True))
                    status = rec.get("status")
                    if status != "unknown" and not (
                            status == "cancelled"
                            and rec.get("dispatched", True)):
                        for sig in _attempt_signatures(attempt):
                            _key = (str(attempt.get("tool", "")), sig)
                            if self._unknown.get(_key, 0) > 1:
                                self._unknown[_key] -= 1
                            elif _key in self._unknown:
                                del self._unknown[_key]

    def _prune(self, lines: list[str]) -> None:
        """Keep the most recent lines (atomic rewrite). Only
        runs on load, never in the dispatch hot path.

        D-F2: the dropped rows are archived first — written to
        a dated file under tool_receipts_archive/ next to the
        live file — so the audit trail survives the cap. The
        live cap itself is unchanged: the live file still holds
        only the newest _max_lines lines. If the archive write
        fails, the prune still runs and the load still succeeds;
        the failure is logged and recorded in the sticky
        prune-archive state so the loss is announced, never
        silent."""
        dropped = lines[:-self._max_lines:] \
            if len(lines) > self._max_lines else []
        if dropped:
            self._archive_dropped(dropped)
        keep = lines[-self._max_lines:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=self.path.name + ".",
                                        dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(keep))
                if keep:
                    handle.write("\n")
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _archive_dropped(self, dropped: list[str]) -> None:
        """Write the rows _prune is about to drop to a dated
        archive file (temp file + os.replace, the same atomic
        pattern as the prune itself). Never raises: a failure
        is logged + recorded in the sticky prune-archive state,
        and the caller still prunes."""
        try:
            archive_dir = self.path.parent / _ARCHIVE_DIRNAME
            archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
            stem = self.path.stem
            name = f"{stem}-{stamp}-{len(dropped)}.jsonl"
            target = archive_dir / name
            # Same-second second prune (two processes, or a
            # reloaded store): never overwrite an existing
            # archive — suffix until the name is free.
            suffix = 1
            while target.exists():
                suffix += 1
                target = archive_dir / (
                    f"{stem}-{stamp}-{len(dropped)}-{suffix}.jsonl")
            fd, tmp_name = tempfile.mkstemp(
                prefix=target.name + ".", dir=str(archive_dir))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(dropped))
                    if dropped:
                        handle.write("\n")
                os.replace(tmp_name, target)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            _note_prune_archive("archived", count=len(dropped),
                                archive_path=str(target))
            print(f"[Receipts] archived {len(dropped)} receipt "
                  f"rows pruned past the {self._max_lines}-line "
                  f"cap to {target}")
        except Exception as exc:  # noqa: BLE001 — an archive
            # failure must never break a load; the loss is
            # announced through the sticky state instead.
            _note_prune_archive("archive_failed",
                                count=len(dropped), error=str(exc))
            print(f"[Receipts] could not archive {len(dropped)} "
                  f"pruned receipt rows: {exc}")

    def _append(self, record: dict, *, fsync: bool = False) -> None:
        self._ensure_loaded()
        if self._load_error:
            raise ValueError(
                "refusing to append to unreadable tool receipts: "
                f"{self._load_error}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str))
            handle.write("\n")
            handle.flush()
            if fsync:
                # REL-04 critical tier only: pay the fsync so the
                # record is durable against power loss, not just
                # a process crash. Directory fsync on file
                # creation is deliberately not added here (the
                # file is created by the first append of any
                # tier, and a missing directory entry after a
                # power cut loses at most the newest records —
                # the residual the module docstring states).
                os.fsync(handle.fileno())

    # -- writes ---------------------------------------------------------

    def record_attempt(self, *, tool: str, args: Any,
                       handle_id: str | None = None,
                       snapshot_stamp: str | None = None,
                       compensations: list | None = None,
                       manual_steps: list | None = None) -> dict:
        """Pre-execution record. Returns the attempt (copy).

        The attempt starts with dispatched=False (regression batch
        R2): only the funnel's mark_dispatched() call, immediately
        before a handler/bus dispatch, flips it to True. Until
        then the attempt never ran, and find_unknown skips it.

        §7 item 8 (action journal): callers that resolved a
        target through the item-1 handles and observed through
        the item-2 adapter pass the handle id and snapshot stamp
        here, so the journal entry carries them as evidence.
        Both are evidence strings only — the journal never
        re-resolves a handle id (a handle from an earlier
        session may be evicted; resolution always goes through
        item-1 resolve on a fresh observation). Callers that
        declare how an effect can be undone pass compensations
        (a list of {"tool", "args"} descriptors, run in reverse
        by the journal's compensation runner) and manual_steps
        (human steps for the no-declared-compensation case).
        Bad descriptors fail LOUDLY here, never stored half-formed.
        """
        if handle_id is not None and not isinstance(handle_id, str):
            raise ValueError("tool receipts: handle_id must be a string")
        if snapshot_stamp is not None and not isinstance(
                snapshot_stamp, str):
            raise ValueError(
                "tool receipts: snapshot_stamp must be a string")
        clean_compensations = _validate_compensations(compensations)
        clean_manual = _validate_manual_steps(manual_steps)
        signatures = effect_signatures(tool, args)
        preview = _canonical_args(_sanitize(args))[:_MAX_PREVIEW_CHARS]
        attempt = {
            "type": "attempt",
            "receipt_id": uuid.uuid4().hex,
            "tool": str(tool),
            "args_signature": signatures[0],
            "effect_signatures": signatures,
            "args_preview": preview,
            "handle_id": handle_id,
            "snapshot_stamp": snapshot_stamp,
            "compensations": clean_compensations,
            "manual_steps": clean_manual,
            "created_at": time.time(),
            "status": "attempted",
            "dispatched": False,
            "outcome_summary": "",
        }
        self._append(
            attempt, fsync=str(tool) in CRITICAL_RECEIPT_TIER)
        self._attempts[attempt["receipt_id"]] = dict(attempt)
        for sig in signatures:
            _key = (str(tool), sig)
            self._unknown[_key] = self._unknown.get(_key, 0) + 1
        return dict(attempt)

    def mark_dispatched(self, receipt_id: str) -> bool:
        """Append-only dispatch mark for an attempt.

        Regression batch (R2): the funnel calls this immediately
        before a handler/bus dispatch. Until it lands, the attempt
        carries dispatched=False, and find_unknown skips it — a
        crash between the attempt record and dispatch start must
        not guard a retry for a call that never ran. Once marked,
        a crash during dispatch leaves a genuine unknown and the
        guard applies. Unknown id, an unreadable store, or an
        append failure returns False (the funnel fails closed);
        an already-resolved or already-marked attempt returns True
        with no new line.
        """
        try:
            self._ensure_loaded()
        except Exception:
            return False
        if self._load_error:
            return False
        attempt = self._attempts.get(str(receipt_id))
        if attempt is None:
            return False
        if attempt.get("status") not in ("attempted", "unknown"):
            return True
        if attempt.get("dispatched", True):
            return True
        try:
            self._append(
                {"type": "dispatched", "receipt_id": str(receipt_id)},
                fsync=str(attempt.get("tool", ""))
                in CRITICAL_RECEIPT_TIER)
        except Exception:
            return False
        attempt["dispatched"] = True
        return True

    def record_outcome(self, receipt_id: str, *, status: str,
                       summary: str = "", dispatched: bool = True,
                       ) -> dict:
        """Attach an outcome to an attempt. Unknown id is an
        error (loud) — outcomes must never silently vanish.
        `dispatched` matters only for "cancelled": True (the
        default) means the call may already have run, so the
        guard keeps blocking re-execution; False means the
        call was cancelled before dispatch, so it never ran
        and the guard releases it."""
        self._ensure_loaded()
        if self._load_error:
            raise ValueError(
                "refusing to append to unreadable tool receipts: "
                f"{self._load_error}")
        attempt = self._attempts.get(str(receipt_id))
        if attempt is None:
            raise ValueError(
                f"tool receipts: no attempt for receipt "
                f"{receipt_id!r}")
        if status not in ("succeeded", "failed", "refused", "unknown",
                          "cancelled"):
            raise ValueError(f"tool receipts: bad outcome status {status!r}")
        summary = summary if isinstance(summary, str) else str(summary)
        record = {
            "type": "outcome",
            "receipt_id": str(receipt_id),
            "status": status,
            "summary": summary[:_MAX_SUMMARY_CHARS],
            "dispatched": bool(dispatched),
            "created_at": time.time(),
        }
        self._append(
            record,
            fsync=str(attempt.get("tool", "")) in CRITICAL_RECEIPT_TIER)
        attempt["status"] = status
        attempt["outcome_summary"] = record["summary"]
        attempt["dispatched"] = bool(dispatched)
        if status != "unknown" and not (
                status == "cancelled" and dispatched):
            for sig in _attempt_signatures(attempt):
                _key = (str(attempt.get("tool", "")), sig)
                if self._unknown.get(_key, 0) > 1:
                    self._unknown[_key] -= 1
                elif _key in self._unknown:
                    del self._unknown[_key]
        return dict(attempt)

    def resolve_unknown(self, *, tool: str, args_signature: str,
                        summary: str) -> bool:
        """Human-verified release (REL-03 reconciliation): the
        newest still-unknown attempt for (tool, signature) is
        recorded as a FAILED outcome with the verification
        summary, so the guard stops blocking a re-run the human
        has verified never happened. Returns False when no such
        attempt exists. Only for the verified-absent case: a
        verified-happened effect must KEEP guarding (re-running
        it would duplicate it), so this method is never called
        for it. Loud on store failure (callers decide)."""
        self._ensure_loaded()
        if self._load_error:
            raise ValueError(self._load_error)
        target = None
        for attempt in reversed(list(self._attempts.values())):
            if (attempt.get("tool") == str(tool)
                    and str(args_signature) in _attempt_signatures(attempt)
                    and (attempt.get("status") in ("attempted", "unknown")
                         or (attempt.get("status") == "cancelled"
                             and attempt.get("dispatched", True)))):
                target = attempt
                break
        if target is None:
            return False
        self.record_outcome(
            str(target.get("receipt_id", "")), status="failed",
            summary=summary, dispatched=True)
        return True

    # -- reads ----------------------------------------------------------

    def find_unknown(self, *, tool: str, args: Any) -> dict | None:
        """The attempt for (tool, args) whose outcome is still
        unknown — an interrupted, timed-out, or crashed dispatch,
        or a call cancelled DURING dispatch (dispatched=True).
        A call cancelled BEFORE dispatch (dispatched=False)
        never ran and is NOT returned here. Likewise, an
        attempted/unknown record never marked dispatched (the call
        was interrupted before dispatch started) never ran and is
        NOT returned; only marked attempts guard. Records written
        before the dispatched field existed read as
        dispatched=True (the safe side). None when every matching
        attempt resolved. Matching is by effect signature: an
        exact match OR a normalised re-spelling (REL-02) counts,
        so pre-Batch-3 receipts guard exact matches and newer
        receipts guard re-spellings too."""
        self._ensure_loaded()
        if self._load_error:
            raise ValueError(self._load_error)
        query_sigs = set(effect_signatures(tool, args))
        if not any((str(tool), sig) in self._unknown
                   for sig in query_sigs):
            return None
        for attempt in reversed(list(self._attempts.values())):
            if (attempt.get("tool") == str(tool)
                    and query_sigs.intersection(
                        _attempt_signatures(attempt))
                    and ((attempt.get("status")
                          in ("attempted", "unknown")
                          and attempt.get("dispatched", True))
                         or (attempt.get("status") == "cancelled"
                             and attempt.get("dispatched", True)))):
                return dict(attempt)
        return None

    def attempts(self) -> list[dict]:
        """A copy of all attempts (diagnosis / tests)."""
        self._ensure_loaded()
        return [dict(a) for a in self._attempts.values()]

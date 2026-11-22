"""
Audit log for VYUK (v5.49, programme item 38).

Until v5.49 the executor left two unjoined trails: ToolRegistry's
activity log (``timestamp | name | status`` — no arguments, no
gate decision, no session context) and core/confirm_gate's own
decision log (per-request IDs, but no record of what actually ran
afterwards). Neither could answer the audit question — *what did
the tool do, with what arguments, under what taint, approved by
whom, in front of which window?* — because the answer lived half
in each.

This module is the joined record. ToolRegistry.execute() calls
write_record() exactly once per call (its one audit hook) and one
JSON record per call lands in the shared hash chain
(``data/records.jsonl``, kind "audit") via core/audit_chain
(GAPS-27, M-V2): the funnel used to write the SAME call twice —
once to the chain (a subset payload: tool/gate/kind/ms/args) and
once to this module's own ``data/audit.jsonl`` (the full record)
— and the two streams could never be reconciled by a reader.
The surviving shape is the chain only; ``audit.jsonl`` is never
written again. Its history is preserved, not deleted: reads
merge the legacy file (plus its single ``.1`` roll) with the
chain's "audit" entries, oldest-first by timestamp, so nothing
recorded before the merge is lost and nothing recorded after it
is duplicated. A chain entry written before the merge by the
funnel's subset append maps back to the audit shape with the
missing fields defaulted (taint unknown, epoch 0, fg ""), so
old subset entries stay readable too.

    ts          ISO timestamp of the call's completion
    tool        the tool name, as dispatched
    args        the arguments, REDACTED at write time (below)
    taint       {"state": "untainted"|"soft"|"hard"|"unknown",
                 "source": <first tainting source or None>,
                 "sources": [<every tainting source>]}
                read from core/injection's shipped accessors
                (tainted / taint_kind / taint_source /
                taint_sources)
    gate        the gate disposition that decided whether the
                handler ran — the v5.49 taxonomy:
                  "free"                    no confirmation was
                                            needed (read-only, a
                                            read action, an
                                            args-free gated tool,
                                            or an unclassified call)
                  "free-by-design:<class>"  no confirmation needed
                                            and the tool's freedom
                                            is a recorded decision
                                            in core/funnel/tools_gate_free.py's
                                            _FREE_BY_DESIGN (the
                                            class is that dict's
                                            rationale string key)
                  "user-approved"           confirm_gate asked and
                                            the human said yes
                  "user-denied"             confirm_gate asked and
                                            the answer was no
                                            (decline, timeout, or a
                                            replayed batched denial)
                  "scoped-approval"         the human approved a
                                            soft-taint scope-ask;
                                            the approval also grants
                                            the screen scope
                  "refused:<reason-class>"  the call never reached
                                            a user decision, or a
                                            later always-refuse /
                                            executor-limit check
                                            overrode one, so the
                                            handler did not run:
                                            unknown-tool, validation,
                                            failsafe, protected-path,
                                            protected-destination,
                                            install-tree, no-handler,
                                            gate-error, epoch-void,
                                            timeout-cooldown, budget
                A call that PASSED its gate and then failed or
                timed out in the handler keeps its passing gate
                value — `kind` carries that outcome instead.
    epoch       core.session_epoch.current_epoch() (the stamp
                execute() took at dispatch when it passes one)
    fg          foreground app/title as one string ("app — title",
                or whichever half is known), via the shipped
                capture seam core/capture_guard.foreground_info()
                (process from core/input_guard, title from
                core/uia.foreground_title). "" when unknown.
    kind        the H8 kind of the execute() result ("success"
                for a success dict, which carries no kind of its
                own; otherwise the result's own kind)
    ms          wall duration of the execute() call, in
                milliseconds (float, sub-millisecond precision)
    request_id  ONLY when a confirmation was involved: the
                confirm_gate request ID for this call, joining
                this record to that module's decision log line
                for the same request (see
                core/confirm_gate.last_decision).

REDACTION — at write time, before anything touches disk; the
file must never contain a raw secret:

  * Typed text is HASHED, never stored: a string under an exact
    "text" key (the typed/pasted/spoken text argument — what a
    user dictated could be anything, including a password read
    aloud) is replaced by "sha256:<first 16 hex of its sha256>".
  * Password-adjacent argument NAMES are MASKED with "•••",
    judged by exact-key matching in the shipped style — the
    detector is IMPORTED from core/confirm_gate (_is_sensitive:
    password / passwd / pin / otp / secret / token and the rest
    of its exact set), never copied here.
  * Secret-SHAPED values are MASKED wherever they appear, judged
    by the shipped detectors, imported not copied:
    core/confirm_gate._looks_like_secret (known prefixes, long
    base64/hex runs, high-entropy tokens) and the clipboard
    secret helper core/funnel/tools_handlers._clipboard_secret_shown (a single
    mixed letter+digit token of 12–200 chars), the latter imported
    lazily because core/funnel/tools_handlers imports this module's caller path
    and a top-level import would cycle.
  * PATHS ARE KEPT, in full: a value under a path key (path,
    filepath, file, target, destination, folder, … — the path
    subset of confirm_gate's FULL_FIDELITY_KEYS) is exempt from
    secret-shape masking, because the path IS the audit's value
    (which file was touched is the question the log exists to
    answer). Sensitive-key masking still outranks this exemption.
  * Long non-path strings are clipped at _MAX_VALUE_CHARS with
    an explicit truncation marker, so one giant content argument
    cannot bloat the log; the clip is visible, never silent.

ROTATION + FAILURE DISCIPLINE: the chain owns rotation
(2 MiB / 90-day, checkpoint entries — see core/audit_chain),
so this module keeps no size cap of its own: the ``max_bytes``
argument to configure() is still accepted and stored, but it
no longer caps writes. Writes are one chain append under a
lock. A failing audit write NEVER fails the tool call:
write_record swallows every error, returns False, and notes
the failure ONCE per process in the activity log beside the
audit file (the codebase's ``timestamp | name | status``
convention) plus the module logger.

READER CONTRACT (the GUI audit viewer is built against exactly
this): read_recent(limit=50) returns parsed records newest-first,
merging the legacy ``audit.jsonl`` history (plus its ``.1`` roll)
with the chain's "audit" entries by timestamp, tolerating a torn
last line (a crash mid-append leaves a partial line, which is
skipped, never fatal); export_to(path) copies all readable
records, oldest-first, to `path` as JSON lines and returns how
many were copied.

Configuration seams (suites / the viewer): configure(path=...,
max_bytes=...) redirects the LEGACY read path and, when a path
is given, the chain's data dir to that path's parent (so a
suite pointing audit at a temp dir also points the chain there,
and the two never disagree); passing None for a field leaves
it; reset_configuration() restores the defaults (including the
chain's data dir). set_foreground_probe(fn) replaces the
foreground probe (fn may return a string, an (app, title) pair,
a dict with process/title keys, or None).
"""

import datetime
import hashlib
import json
import logging
import pathlib
import threading

log = logging.getLogger("nova.audit")

# v5.49 (programme item 38): ~2 MB cap, one rolled file.
MAX_BYTES = 2 * 1024 * 1024
_MAX_VALUE_CHARS = 1000
_MASK = "•••"

_lock = threading.Lock()
_path_override: pathlib.Path | None = None
_max_bytes_override: int | None = None
_fg_probe = None
_failure_noted = False

# Path keys whose values are KEPT verbatim (see the module
# docstring): the path subset of confirm_gate.FULL_FIDELITY_KEYS.
_PATH_KEYS = frozenset({
    "path", "filepath", "file", "filename", "file_name",
    "target", "destination", "dest", "source", "folder",
    "directory", "dir",
})


# ---------------------------------------------------------------------------
# Configuration seams
# ---------------------------------------------------------------------------


def _default_path() -> pathlib.Path:
    return (pathlib.Path(__file__).resolve().parent.parent
            / "data" / "audit.jsonl")


def audit_path() -> pathlib.Path:
    """Where the LEGACY audit file is read from. Writes no longer
    go here (GAPS-27, M-V2): write_record routes into the shared
    hash chain; this path only locates pre-merge history for
    read_recent / export_to."""
    return _path_override if _path_override is not None \
        else _default_path()


def _max_bytes() -> int:
    return _max_bytes_override if _max_bytes_override is not None \
        else MAX_BYTES


def configure(path=None, max_bytes=None) -> None:
    """Redirect the legacy audit read path and/or resize the cap
    (v5.49, programme item 38). Either argument left as None keeps
    its current setting. GAPS-27 (M-V2): a path also redirects the
    shared chain's data dir to that path's parent, so a suite
    pointing audit at a temp dir writes its chain entries there
    too — the two never disagree. The cap is accepted and stored
    but no longer caps writes (the chain owns rotation)."""
    global _path_override, _max_bytes_override
    with _lock:
        if path is not None:
            _path_override = pathlib.Path(path)
            parent = _path_override.parent
            if str(parent) not in ("", "."):
                try:
                    from . import audit_chain

                    audit_chain.configure(data_dir=parent)
                except Exception:  # noqa: BLE001 — the legacy
                    pass  # redirect still stands; the chain keeps
                    # its own (default) data dir
        if max_bytes is not None:
            _max_bytes_override = int(max_bytes)


def reset_configuration() -> None:
    """Restore the default path, cap and foreground probe — and
    the chain's data dir (GAPS-27, M-V2: configure() above may
    have redirected it)."""
    global _path_override, _max_bytes_override, _fg_probe
    with _lock:
        _path_override = None
        _max_bytes_override = None
        _fg_probe = None
    try:
        from . import audit_chain

        audit_chain.reset_configuration()
    except Exception:  # noqa: BLE001 — the chain was never
        pass  # redirected; nothing to restore


def set_audit_path(path) -> None:
    """configure(path=...) under its plainer name."""
    configure(path=path)


def set_foreground_probe(fn) -> None:
    """Inject the foreground probe (see the module docstring for
    the accepted return shapes); None restores the shipped seam."""
    global _fg_probe
    _fg_probe = fn


# ---------------------------------------------------------------------------
# Redaction (v5.49, programme item 38) — detectors imported from
# core/confirm_gate (and lazily core/funnel/tools_handlers), never copied.
# ---------------------------------------------------------------------------


def _is_sensitive_key(key) -> bool:
    try:
        from .confirm_gate import _is_sensitive

        return bool(_is_sensitive(str(key)))
    except Exception:  # noqa: BLE001 — a broken detector must not
        return False  # leak by crashing redaction; shape checks stay


def _looks_like_secret(text: str) -> bool:
    try:
        from .confirm_gate import _looks_like_secret as _detect

        if _detect(text):
            return True
    except Exception:  # noqa: BLE001
        pass
    # The clipboard secret helper (core/funnel/tools_handlers._clipboard_secret_
    # shown): a single mixed letter+digit token reads as a secret
    # at lengths the entropy detector lets through. Lazily
    # imported — core/funnel/tools_handlers is heavy and sits above this module.
    try:
        from .funnel.tools_handlers import _clipboard_secret_shown  # Phase 4 S1b INTENTIONAL: handlers split to funnel

        return _clipboard_secret_shown(text) is not None
    except Exception:  # noqa: BLE001 — detector unavailable: the
        return False  # confirm_gate shape check above still ran


def _hash_text(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def _redact_value(key, value):
    """One argument value, redacted per the module docstring.
    `key` is the argument name the value sits under (for list
    items, the name of the list they sit in)."""
    if _is_sensitive_key(key):
        return _MASK
    if isinstance(value, dict):
        return {str(k): _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, str):
        normalised = str(key).lower() if key is not None else ""
        if normalised == "text":
            return _hash_text(value)
        if normalised not in _PATH_KEYS and _looks_like_secret(value):
            return _MASK
        if len(value) > _MAX_VALUE_CHARS:
            return (value[:_MAX_VALUE_CHARS]
                    + f"…[truncated, {len(value)} chars total]")
        return value
    return value


def redact_args(args) -> dict:
    """The redacted copy of a tool call's arguments that is safe
    to write to disk (v5.49, programme item 38). Non-dict input
    (execute() always passes a dict; direct callers may not)
    degrades to {} for None and a one-key record otherwise."""
    if args is None:
        return {}
    if not isinstance(args, dict):
        return {"value": _redact_value("value", args)}
    return {str(k): _redact_value(k, v) for k, v in args.items()}


# ---------------------------------------------------------------------------
# Context probes — best-effort, never raising
# ---------------------------------------------------------------------------


def _taint_snapshot(taint=None):
    """The record's taint field: the caller's snapshot when given,
    else core/injection's shipped accessors, read live."""
    if taint is not None:
        return taint
    try:
        from . import injection

        if not injection.tainted():
            return {"state": "untainted", "source": None,
                    "sources": []}
        sources = []
        try:
            sources = list(injection.taint_sources())
        except Exception:  # noqa: BLE001
            sources = []
        return {"state": injection.taint_kind() or "hard",
                "source": injection.taint_source(),
                "sources": sources}
    except Exception:  # noqa: BLE001 — unknown is the honest answer
        return {"state": "unknown", "source": None, "sources": []}


def _epoch_value(epoch=None) -> int:
    if epoch is not None:
        try:
            return int(epoch)
        except (TypeError, ValueError):
            pass
    try:
        from . import session_epoch

        return int(session_epoch.current_epoch())
    except Exception:  # noqa: BLE001
        return 0


def _foreground(fg=None) -> str:
    """The record's fg field: "app — title", one half, or ""."""
    if fg is not None:
        return str(fg)
    probe = _fg_probe
    try:
        if probe is not None:
            result = probe()
        else:
            from . import capture_guard

            result = capture_guard.foreground_info()
    except Exception:  # noqa: BLE001 — unknown foreground is normal
        return ""
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        app = result.get("process") or result.get("process_name") \
            or result.get("app") or ""
        title = result.get("title") or ""
    else:
        try:
            app, title = result
        except (TypeError, ValueError):
            return str(result).strip()
    parts = [str(p).strip() for p in (app, title) if p and str(p).strip()]
    return " — ".join(parts)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _note_failure_once(exc: BaseException) -> None:
    """One activity-log line per process for audit failures —
    the failure must be visible, but a broken disk must not turn
    every tool call into a log storm. Never raises."""
    global _failure_noted
    if _failure_noted:
        return
    _failure_noted = True
    log.warning("audit write failed (%s: %s) — tool calls continue "
                "unaudited", type(exc).__name__, exc)
    try:
        activity = audit_path().parent / "activity.log"
        activity.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(activity, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} | audit | error: audit write "
                    f"failed: {exc!s}\n")
    except Exception:  # noqa: BLE001 — the note is best-effort too
        pass


def write_record(tool, args=None, *, gate="free", kind="",
                 ms=0.0, taint=None, epoch=None, fg=None,
                 request_id=None, ts=None) -> bool:
    """Append ONE redacted JSON record for one execute() call
    (v5.49, programme item 38 — see the module docstring for the
    field contract). GAPS-27 (M-V2): the record lands in the
    shared hash chain (``data/records.jsonl``, kind "audit") via
    core/audit_chain — the one surviving audit stream — not in
    ``data/audit.jsonl``. Returns True when the record landed
    (a non-empty chain id), False when it did not; NEVER raises,
    so a failing audit can never fail the tool call it
    describes.

    Loophole batch 5 (#5): private mode suppresses the audit
    record — a private turn leaves no row in the chain, and the
    chain stays valid (the next record links to the last
    pre-private record). Returns False: no record landed.
    """
    try:
        from .private_mode import private_mode_on
    except Exception:  # noqa: BLE001 — unreadable: not private
        private_mode_on = None
    if private_mode_on is not None and private_mode_on():
        return False
    try:
        record = {
            "ts": ts or datetime.datetime.now().isoformat(
                timespec="milliseconds"),
            "tool": str(tool),
            "args": redact_args(args),
            "taint": _taint_snapshot(taint),
            "gate": str(gate),
            "epoch": _epoch_value(epoch),
            "fg": _foreground(fg),
            "kind": str(kind or ""),
            "ms": round(float(ms or 0.0), 3),
        }
        if request_id is not None:
            record["request_id"] = request_id
        from . import audit_chain

        entry_id = audit_chain.append("audit", record)
        return bool(entry_id)
    except Exception as exc:  # noqa: BLE001 — audit must never
        _note_failure_once(exc)  # break the call it describes
        return False


# ---------------------------------------------------------------------------
# Reading — the GUI viewer's contract
# ---------------------------------------------------------------------------


def _chain_record_to_audit(entry: dict) -> dict | None:
    """Map one chain kind="audit" entry back to the audit record
    shape. The full record is entry["data"] as written by
    write_record; a pre-merge funnel subset entry (tool/gate/
    kind/ms/args only) maps with the missing fields defaulted,
    so old subset entries stay readable."""
    data = entry.get("data")
    if not isinstance(data, dict):
        return None
    record = {
        "ts": str(data.get("ts") or entry.get("ts") or ""),
        "tool": str(data.get("tool", "")),
        "args": data.get("args") if isinstance(
            data.get("args"), dict) else {},
        "taint": data.get("taint") or {
            "state": "unknown", "source": None, "sources": []},
        "gate": str(data.get("gate", "")),
        "epoch": data.get("epoch", 0),
        "fg": str(data.get("fg", "")),
        "kind": str(data.get("kind", "")),
        "ms": data.get("ms", 0.0),
    }
    if data.get("request_id") is not None:
        record["request_id"] = data["request_id"]
    return record


def _read_legacy() -> list[dict]:
    """The pre-merge history: every readable record in the legacy
    ``audit.jsonl`` (+ its single ``.1`` roll), oldest-first.
    Unparsable lines — a torn last line from a crash mid-append
    above all — are skipped, never fatal."""
    records: list[dict] = []
    path = audit_path()
    for candidate in (path.with_name(path.name + ".1"), path):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue  # torn / corrupt line: skip it
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def _read_all() -> list[dict]:
    """Every readable record, oldest-first: the legacy file's
    history merged with the chain's "audit" entries, sorted by
    timestamp. A record exists in exactly one stream (a given
    execute() call wrote to one place — audit.jsonl before the
    GAPS-27 merge, the chain after it), so no dedup is needed;
    the merge only orders the two histories together.

    Loophole batch 6 (#12): the chain part reads the VERIFIED
    prefix only — entries at and after the chain's first break
    are never returned as trusted values. The legacy file
    predates the chain (no hashes to verify) and reads as before."""
    records = _read_legacy()
    try:
        from . import audit_chain

        for entry in audit_chain.read_trusted(
                kind="audit", limit=None)["entries"]:
            mapped = _chain_record_to_audit(entry)
            if mapped is not None:
                records.append(mapped)
    except Exception:  # noqa: BLE001 — the legacy history still
        pass  # stands when the chain is unreadable
    records.sort(key=lambda r: str(r.get("ts", "")))
    return records


def chain_break() -> dict | None:
    """The audit chain's first break, or None when it verifies
    intact (loophole batch 6, #12)."""
    try:
        from . import audit_chain

        return audit_chain.chain_break()
    except Exception:  # noqa: BLE001 — an unreadable chain
        return None  # reports no break rather than breaking callers


def read_recent(limit=50) -> list[dict]:
    """The newest `limit` audit records, newest-first (v5.49,
    programme item 38). Tolerant of a torn last line; [] when no
    audit file exists yet."""
    try:
        count = int(limit)
    except (TypeError, ValueError):
        count = 50
    if count <= 0:
        return []
    return list(reversed(_read_all()))[:count]


def export_to(path) -> int:
    """Copy every readable audit record to `path` as JSON lines,
    oldest-first, and return how many records were copied
    (v5.49, programme item 38). The destination's parent folder
    is created when needed."""
    records = _read_all()
    dest = pathlib.Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
    return len(records)

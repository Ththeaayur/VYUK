"""Unified activity read (GAPS-27, M-V4).

The Activity page used to read ONLY the shared hash chain
(core/record_family.read_activity) — the human lines. Everything
else a tool did lived in places the page never looked: the
per-tool hash-chained events tables (core/store_base.STORE_EVENTS),
the cost ledger (core/cost_ledger), and the unstructured
``data/activity.log``. This module is the one read that sees all
four, each entry source-tagged, newest-first.

WHAT IT IS NOT: the page's DEFAULT feed is unchanged — the
chain-only human lines (gui/window_activity.activity_feed).
This read is the opt-in unified view (the Activity dialog's
extra sources, via gui/window_activity.unified_feed). It never
writes, never creates a store file (read-only SQLite), and a
missing or corrupt source is skipped, never fatal — one broken
store must not blank the whole feed.

Chain "audit" entries (the funnel's per-call audit records) are
deliberately NOT included here: they are the audit viewer's
domain (core/audit.read_recent) and would duplicate the audit
log into the Activity page. The "activity" source below is the
chain's HUMAN lines only.

Entry shape (all sources):
    {"source": "activity" | "ledger" | "cost" | "log",
     "store": <tool store name, ledger entries only>,
     "ts": ISO timestamp ("" when the row carries none),
     "title": one human line,
     "kind": the event kind ("" when unknown),
     "detail": the short text the row carries ("" when none)}
"""

from __future__ import annotations

import datetime
import json
import sqlite3

from .config import DATA_DIR
from . import store_base

__all__ = ["read_unified"]

_MAX_TEXT_CHARS = 120

# JSON payload keys preferred (in order) when summarising a
# payload dict into one line; otherwise the first string value.
_TEXT_KEYS = ("text", "summary", "detail", "message", "name",
              "label", "title")


def _iso_from_epoch(value) -> str:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return ""
    try:
        return datetime.datetime.fromtimestamp(
            stamp, tz=datetime.timezone.utc).isoformat(
                timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


def _iso_from_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # SQLite datetime('now') gives "YYYY-MM-DD HH:MM:SS"; the
    # chain gives ISO with "T". Normalise to the "T" form so the
    # merged sort orders both the same way.
    return text.replace(" ", "T", 1) if "T" not in text else text


def _normalise_ts(value, ts_kind: str) -> str:
    if ts_kind == "epoch":
        return _iso_from_epoch(value)
    return _iso_from_text(value)


def _summarise_payload(payload) -> str:
    """One short line from a JSON payload value."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return payload[:_MAX_TEXT_CHARS]
    if isinstance(payload, dict):
        for key in _TEXT_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:_MAX_TEXT_CHARS]
        for value in payload.values():
            if isinstance(value, str) and value.strip():
                return value.strip()[:_MAX_TEXT_CHARS]
        return json.dumps(payload, ensure_ascii=False)[:_MAX_TEXT_CHARS]
    if payload is None:
        return ""
    return str(payload)[:_MAX_TEXT_CHARS]


def _unavailable_marker(source: str, title: str,
                        store: str | None = None) -> dict:
    """One feed row saying a source could not be read
    (field-check fix #16, T8 #24). A missing source is healthy
    — no marker — so this is only built when a source that
    should be readable failed: the feed shows the gap instead
    of silently omitting it. ts "" sorts last, never as a
    fabricated "now". No paths in the text (fix #8)."""
    entry = {"source": source, "ts": "", "title": title,
             "kind": "unavailable", "detail": title}
    if store is not None:
        entry["store"] = store
    return entry


def _is_marker_row(entry: dict) -> bool:
    """True for a feed row that reports a failure instead of
    content: fix #16's "unavailable" rows (a source that is
    present but unreadable) and fix #2's observability rows
    (source "observability", kind "degraded"). A marker is a
    failure notice the user must see even on a busy feed — it
    is never truncated away by the limit (field-check fix #26,
    round-2 T8 #24 PARTIAL). Anything else — including a real
    undated content row — is content and the limit applies."""
    return (entry.get("kind") == "unavailable"
            or entry.get("source") == "observability")


def _chain_file_unreadable() -> bool:
    """True when the chain file exists with content but no line
    parses as a record — the all-garbage case the tolerant reader
    skips silently (its torn-line tolerance is by design, so a file
    with SOME valid lines is readable, not broken). A line only
    counts as parseable when it parses as a JSON object: a JSON
    array or bare string is garbage, not a record (round-3 C3-F1
    — the section-C garbage includes "[1, 2, 3]" and a bare
    string, which used to count as parseable and silenced the
    marker). Missing or empty file is healthy: False. Never
    raises."""
    try:
        from . import audit_chain

        path = audit_chain.store_path()
        if not path.is_file():
            return False
        parseable = 0
        nonblank = 0
        for line in path.read_text(encoding="utf-8",
                                   errors="replace").splitlines():
            if not line.strip():
                continue
            nonblank += 1
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                parseable += 1
        return nonblank > 0 and parseable == 0
    except Exception:  # noqa: BLE001 — the check must never
        return False  # break the feed


def _read_chain(limit: int) -> list[dict]:
    entries: list[dict] = []
    try:
        from . import record_family

        for entry in record_family.read_activity(limit):
            data = entry.get("data") or {}
            text = data.get("text", "") if isinstance(data, dict) else ""
            entries.append({
                "source": "activity",
                "ts": _iso_from_text(entry.get("ts")),
                "title": str(text),
                "kind": "activity",
                "detail": str(text),
            })
    except Exception:  # noqa: BLE001 — a broken chain still
        # leaves the other three sources standing, but no longer
        # silently: the feed carries the gap as a marker row.
        return [_unavailable_marker(
            "activity",
            "Activity history is unavailable — its records "
            "couldn't be read, so older entries may be missing "
            "from this feed.")]
    if not entries and _chain_file_unreadable():
        # Garbage-only file: the tolerant reader skipped every
        # line and returned nothing — the same silence as a
        # raising read, so the same marker.
        return [_unavailable_marker(
            "activity",
            "Activity history is unavailable — its records "
            "couldn't be read, so older entries may be missing "
            "from this feed.")]
    return entries


def _read_observability() -> list[dict]:
    """One marker entry per degraded sticky state (field-check
    fix #2, T7 #19/#20). A receipt store or cost ledger that
    silently lost rows reports only `observability: degraded`
    to the model today; these markers put the same fact in
    front of the user, in the Activity feed, with the failure
    count and the last error. Empty when nothing is degraded
    — the feed is unchanged on a healthy system. The marker's
    ts is the last failure's own time, so it sorts among the
    entries by recency, never as a fabricated "now". Never
    raises, never writes."""
    markers: list[dict] = []
    try:
        from . import tool_receipts as _receipts_mod

        for kind, rec in _receipts_mod.receipt_degraded_state().items():
            count = rec.get("count", 0)
            last = str(rec.get("last_error") or "")[:80]
            text = (f"Receipts degraded: {count} {kind} receipt(s) "
                    "failed to record"
                    + (f" (last: {last})" if last else "")
                    + " — some tool runs may be missing from "
                    "this feed.")
            markers.append({
                "source": "observability",
                "ts": _iso_from_epoch(rec.get("last_at")),
                "title": text,
                "kind": "degraded",
                "detail": text,
            })
    except Exception:  # noqa: BLE001 - a broken receipts read
        pass  # must never break the feed
    try:
        from . import cost_ledger as _cost_mod

        for kind, rec in _cost_mod.live_record_degraded_state().items():
            count = rec.get("count", 0)
            last = str(rec.get("last_error") or "")[:80]
            label = {"live": "Live", "local": "Local",
                     "cloud_text": "Cloud text"}.get(kind, kind)
            text = (f"Cost ledger degraded: {count} {label} cost "
                    "row(s) lost"
                    + (f" (last: {last})" if last else "")
                    + " — the cost cap totals undercount.")
            markers.append({
                "source": "observability",
                "ts": _iso_from_epoch(rec.get("last_at")),
                "title": text,
                "kind": "degraded",
                "detail": text,
            })
    except Exception:  # noqa: BLE001 - a broken ledger read
        pass  # must never break the feed
    try:
        # Round-3 section C fix queue item 10: a cap check that
        # could not read the ledger is its own marker — a blocked
        # turn, not a lost row (the totals are intact; the spend
        # was refused, not undercounted).
        from . import cost_ledger as _cost_mod_pf

        for kind, rec in _cost_mod_pf.preflight_degraded_state().items():
            count = rec.get("count", 0)
            last = str(rec.get("last_error") or "")[:80]
            label = {"live": "Live", "local": "Local",
                     "cloud_text": "Cloud text"}.get(kind, kind)
            text = (f"Cost cap check unavailable: {count} {label} cap "
                    "check(s) failed"
                    + (f" (last: {last})" if last else "")
                    + " — cloud-text turns are paused until this "
                    "is fixed.")
            markers.append({
                "source": "observability",
                "ts": _iso_from_epoch(rec.get("last_at")),
                "title": text,
                "kind": "degraded",
                "detail": text,
            })
    except Exception:  # noqa: BLE001 - a broken ledger read
        pass  # must never break the feed
    try:
        # Round-3 section C fix queue item 14: a settings write
        # that could not persist is its own marker — the change
        # works for this session (the in-memory value stands) but
        # will not survive a restart, and the user should know
        # that in one place instead of discovering it key by key.
        from . import config_spec as _cs_mod

        state = _cs_mod.qsettings_write_degraded_state()
        if state:
            total = sum(int(rec.get("count", 0))
                        for rec in state.values())
            last_key = ""
            last_err = ""
            last_at = 0.0
            for _key, _rec in state.items():
                if float(_rec.get("last_at", 0) or 0) >= last_at:
                    last_at = float(_rec.get("last_at", 0) or 0)
                    last_key = _key
                    last_err = str(_rec.get("last_error") or "")[:80]
            text = (f"Settings degraded: {total} write(s) to "
                    f"{len(state)} setting(s) could not be saved"
                    + (f" (last: {last_key}: {last_err})"
                       if last_key else "")
                    + " — changes in this session may not survive "
                    "a restart.")
            markers.append({
                "source": "observability",
                "ts": _iso_from_epoch(last_at),
                "title": text,
                "kind": "degraded",
                "detail": text,
            })
    except Exception:  # noqa: BLE001 - a broken settings read
        pass  # must never break the feed
    try:
        # Round-3 section D fix queue item D-F2: a receipts
        # prune that archived rows is its own marker — the live
        # file keeps only the newest 5,000 rows, and the older
        # rows live in the named archive file instead of being
        # destroyed. A failed archive is a degraded marker
        # naming the lost rows instead.
        from . import tool_receipts as _receipts_mod_prune

        for _key, _rec in \
                _receipts_mod_prune.prune_archive_state().items():
            count = int(_rec.get("count", 0) or 0)
            path = str(_rec.get("archive_path") or "")
            err = str(_rec.get("last_error") or "")[:80]
            if _key == "archive_failed":
                text = (f"Receipts degraded: {count} older "
                        "receipt row(s) could not be archived"
                        + (f" (last: {err})" if err else "")
                        + " — they were dropped from the live file.")
                kind = "degraded"
            elif _key == "archive_rotated":
                # Loophole batch 6 (#15): the archive itself is
                # capped — old archive files are removed to keep
                # it under its size bound. The rows are gone, not
                # moved: say so, never render a rotation as an
                # archive.
                text = (f"Receipts archive rotated: {count} older "
                        "receipt row(s) removed to keep the archive "
                        "under its size cap — the live file keeps "
                        "the newest 5,000.")
                kind = "archived"
            else:
                text = (f"Receipts archived: {count} older receipt "
                        "row(s) moved to "
                        f"{path or 'the receipts archive'}"
                        " — the live file keeps the newest 5,000.")
                kind = "archived"
            markers.append({
                "source": "observability",
                "ts": _iso_from_epoch(_rec.get("last_at")),
                "title": text,
                "kind": kind,
                "detail": text,
            })
    except Exception:  # noqa: BLE001 - a broken receipts read
        pass  # must never break the feed
    try:
        # Loophole batch 6 (#12): the Activity feed reads the
        # VERIFIED prefix only — when the chain is broken, the
        # entries after the break are not shown. The gap itself
        # is a marker, so a shorter feed never reads as a quiet
        # day: it names where the record breaks and why.
        from . import record_family as _rf_mod

        _break = _rf_mod.chain_break()
        if _break is not None:
            _seg = str(_break.get("segment") or "")
            _idx = _break.get("index")
            _why = str(_break.get("reason") or "")
            text = (f"Activity record broken at {_seg} entry "
                    f"{_idx} ({_why}) — entries after that point "
                    "can't be trusted and are not shown.")
            markers.append({
                "source": "observability",
                "ts": "",
                "title": text,
                "kind": "degraded",
                "detail": text,
            })
    except Exception:  # noqa: BLE001 - a broken chain read
        pass  # must never break the feed
    try:
        # Round-3 section D fix queue item D-F3: a chain
        # rotation archives the whole live file behind a
        # checkpoint entry (kind "checkpoint", first entry of
        # the fresh live file), and the Activity feed then
        # shows only entries since the rotation — the older
        # entries are still on disk, but the user is never
        # told they moved. One marker per checkpoint in the
        # live file (in practice one: each rotation archives
        # the previous live file, checkpoint included, so the
        # live file holds only the latest rotation's
        # checkpoint). The marker names the archived file and
        # how many entries moved; when that archive is no
        # longer on disk the marker says so instead of
        # pointing at a missing file. The checkpoint lives in
        # the chain itself, so the marker survives restarts —
        # unlike the per-process sticky states above, which
        # only know about this session.
        from . import audit_chain as _chain_mod

        for _entry in _chain_mod.read(kind="checkpoint", limit=0):
            _cdata = _entry.get("data") or {}
            _arch_file = str(_cdata.get("archived_file") or "")
            _arch_count = _cdata.get("archived_entries")
            _arch_path = (str(_chain_mod.archive_dir() / _arch_file)
                          if _arch_file else "")
            _when = _iso_from_text(_entry.get("ts"))
            _count_txt = (f"{int(_arch_count)} entries"
                          if isinstance(_arch_count, int)
                          else "entries")
            if _arch_file and not (
                    _chain_mod.archive_dir() / _arch_file).is_file():
                text = (f"Older entries archived: {_count_txt} "
                        "were moved to "
                        f"{_arch_path or 'the records archive'}"
                        ", but that archive is no longer on disk"
                        " — those entries can no longer be "
                        "verified.")
                kind = "degraded"
            else:
                text = (f"Older entries archived: {_count_txt} "
                        "moved to "
                        f"{_arch_path or 'the records archive'}"
                        + (f" on {_when}" if _when else "")
                        + " — this feed shows entries since the "
                        "rotation.")
                kind = "archived"
            markers.append({
                "source": "observability",
                "ts": _when,
                "title": text,
                "kind": kind,
                "detail": text,
            })
    except Exception:  # noqa: BLE001 - a broken chain read
        pass  # must never break the feed
    return markers


def _read_store(store: str, spec, data_dir, limit: int) -> list[dict]:
    (filename, table, ts_col, ts_kind, kind_col,
     text_kind, text_cols) = spec
    path = data_dir / filename
    if not path.is_file():
        return []
    cols = [ts_col] + ([kind_col] if kind_col else []) + list(text_cols)
    query = (f"SELECT {', '.join(cols)} FROM {table} "
             f"ORDER BY {ts_col} DESC LIMIT ?")
    entries: list[dict] = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(query, (limit,)).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — a missing table or a
        # corrupt store is skipped, never fatal — but no longer
        # silently: the feed carries the gap as a marker row.
        return [_unavailable_marker(
            "ledger",
            f"Tool events are unavailable — the {store} record "
            "couldn't be read, so some tool runs may be missing "
            "from this feed.",
            store=store)]
    for row in rows:
        values = dict(zip(cols, row))
        ts = _normalise_ts(values.get(ts_col), ts_kind)
        if text_kind == "json":
            raw = values.get(text_cols[0])
            parsed = None
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = None
            title = _summarise_payload(
                parsed if parsed is not None else (raw or ""))
            kind = ""
            if kind_col:
                kind = str(values.get(kind_col) or "")
            elif isinstance(parsed, dict):
                kind = str(parsed.get("kind") or "")
            detail = title
        else:
            parts = [str(values.get(col) or "").strip()
                     for col in text_cols]
            title = " — ".join(p for p in parts if p)[:_MAX_TEXT_CHARS]
            kind = str(values.get(kind_col) or "") if kind_col else ""
            detail = title
        entries.append({
            "source": "ledger",
            "store": store,
            "ts": ts,
            "title": title,
            "kind": kind,
            "detail": detail,
        })
    return entries


def _read_cost(data_dir, limit: int) -> list[dict]:
    entries: list[dict] = []
    # Parent fix (2026-11-14): a read must never create a store
    # file — CostLedger._connect() creates cost_ledger.sqlite3
    # (schema only) when absent, so a missing ledger answers
    # empty here, before any CostLedger exists. The module's
    # "never creates a store file" promise holds for this source.
    if not (data_dir / "cost_ledger.sqlite3").is_file():
        return []
    try:
        from . import cost_ledger

        ledger = cost_ledger.CostLedger(
            db_path=data_dir / "cost_ledger.sqlite3")
        try:
            turns = ledger.recent_turns(limit)
        finally:
            ledger.close()
        for turn in turns:
            cost = turn.get("cost_usd")
            cost_text = (f"${cost:.4f}" if isinstance(cost, (int, float))
                         else "cost n/a")
            title = (f"{turn.get('brain', '')} · "
                     f"{turn.get('model', '')} · {cost_text}")
            entries.append({
                "source": "cost",
                "ts": _iso_from_epoch(turn.get("ts")),
                "title": title,
                "kind": "cost",
                "detail": title,
            })
    except Exception:  # noqa: BLE001 — no cost ledger yet, or a
        # broken one, still leaves the other sources — but a
        # present-but-broken ledger no longer reads as "no costs":
        # the feed carries the gap as a marker row.
        return [_unavailable_marker(
            "cost",
            "Cost history is unavailable — the cost ledger "
            "couldn't be read, so cost totals may be missing "
            "from this feed.")]
    return entries


def _read_log(data_dir, limit: int) -> list[dict]:
    path = data_dir / "activity.log"
    entries: list[dict] = []
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8",
                               errors="replace").splitlines()
    except OSError:  # noqa: BLE001 — a present-but-unreadable
        # log is a broken source, not an empty one: marker, not
        # silence. (A missing log is healthy — no marker.)
        return [_unavailable_marker(
            "log",
            "Activity log is unavailable — activity.log couldn't "
            "be read, so some entries may be missing from this "
            "feed.")]
    for line in reversed(lines[-limit:]):
        parts = [p.strip() for p in line.split(" | ")]
        if len(parts) < 3:
            continue
        ts, tool, outcome = parts[0], parts[1], " | ".join(parts[2:])
        entries.append({
            "source": "log",
            "ts": _iso_from_text(ts),
            "title": f"{tool}: {outcome}",
            "kind": outcome,
            "detail": line,
        })
    return entries


def read_unified(limit=50, data_dir=None, include=("activity",
                                                    "ledger", "cost",
                                                    "log")) -> list[dict]:
    """All four activity sources merged, newest-first, plus the
    observability markers (field-check fix #2, T7 #19/#20):
    when a receipt store or the Live cost ledger has lost rows,
    one marker per degraded state rides the "activity" branch,
    so the failure is visible in the feed instead of reaching
    only the model as `observability: degraded`. Field-check
    fix #16 (T8 #24) adds the same visibility to the sources
    themselves: a source that is present but unreadable (a
    garbage chain file, a corrupt store or ledger, an
    unreadable log) contributes one "unavailable" marker row
    instead of silently contributing nothing. A missing source
    is healthy and still contributes nothing.

    `include` selects sources by name ("activity" = the chain's
    human lines, "ledger" = the per-tool events tables, "cost" =
    the cost ledger's turns, "log" = data/activity.log). A
    missing source contributes nothing; an unreadable one
    contributes one "unavailable" marker row. The read never
    raises and never writes. Undated rows (ts "") sort
    last, so they never outrank a dated entry.

    The limit bounds dated content, never markers: the return
    is the newest `count` entries plus any marker row that fell
    below the cut, appended after them in sorted order (field-
    check fix #26, round-2 T8 #24 PARTIAL — a busy feed used to
    truncate the "unavailable" marker away at the default limit
    of 50, reproducing the exact round-1 FAIL signature). A
    marker already inside the cut stays where the sort put it;
    the first `count` rows are byte-identical to the old cut,
    so dated order and the limit's meaning for content are
    unchanged. On a healthy feed there are no markers and the
    return is exactly the old `entries[:limit]`."""
    try:
        count = int(limit)
    except (TypeError, ValueError):
        count = 50
    if count <= 0:
        return []
    base = data_dir if data_dir is not None else DATA_DIR
    wanted = set(include or ())
    entries: list[dict] = []
    if "activity" in wanted:
        entries.extend(_read_chain(count))
        entries.extend(_read_observability())
    if "ledger" in wanted:
        for store, spec in store_base.STORE_EVENTS.items():
            entries.extend(_read_store(store, spec, base, count))
    if "cost" in wanted:
        entries.extend(_read_cost(base, count))
    if "log" in wanted:
        entries.extend(_read_log(base, count))
    # Newest first; undated ("") last — "" is smaller than any
    # ISO timestamp, so a descending sort drops it to the end.
    # The sort is stable, so same-second entries keep their
    # per-source order.
    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
    # The limit bounds content, never markers: the newest
    # `count` entries, plus any marker row that fell below the
    # cut, appended after them in sorted order. A marker already
    # inside the cut stays where the sort put it.
    head = entries[:count]
    head.extend(e for e in entries[count:] if _is_marker_row(e))
    return head

"""Unified memory read (GAPS-27, M-V6).

Before this module, `core/memory/__init__.py` re-exported only
MemoryStore — facts and episodes had no shared read path, so
every consumer invented its own. read_memory() is the one read
that sees both, in the shape the "What I remember about you"
panel renders (gui/window_memory_panel).

SAFETY (the retention-loopholes memory privacy paradox): a
fact that looks like a credential (core/memory/memory.py's
looks_like_credential — warn-only there) is EXCLUDED here,
whole — key and value both, never rendered, never counted by
name. The read reports only HOW MANY were hidden, so the panel
can say so honestly without revealing them. Every returned
fact carries its source — WHY it is remembered ("user" /
"correction" / "learned" / "unknown") — and its id, so the
panel can offer per-item delete (MemoryStore.delete_fact).

The read never writes and never raises: a missing DB, a
missing table, or a corrupt row yields empty lists.
"""

from __future__ import annotations

import sqlite3

from ..config import DB_PATH
from .memory import SOURCE_LABELS, looks_like_credential

__all__ = ["read_memory"]


def _read_facts(db_path, limit: int) -> tuple[list[dict], int]:
    """(visible facts, hidden credential-like count)."""
    facts: list[dict] = []
    hidden = 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            try:
                rows = conn.execute(
                    "SELECT id, category, key, value, source,"
                    " updated_at FROM facts"
                    " ORDER BY category, key LIMIT ?",
                    (limit,)).fetchall()
                has_source = True
            except sqlite3.OperationalError:
                # Pre-migration table: no source column.
                rows = conn.execute(
                    "SELECT id, category, key, value,"
                    " updated_at FROM facts"
                    " ORDER BY category, key LIMIT ?",
                    (limit,)).fetchall()
                has_source = False
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — no DB / no table: the
        return [], 0  # read stands empty, never raises
    for row in rows:
        record = dict(row)
        key = str(record.get("key") or "")
        value = str(record.get("value") or "")
        if looks_like_credential(key, value):
            hidden += 1
            continue
        source = str(record.get("source") or "unknown") \
            if has_source else "unknown"
        facts.append({
            "id": record.get("id"),
            "category": str(record.get("category") or "general"),
            "key": key,
            "value": value,
            "source": source,
            "source_label": SOURCE_LABELS.get(
                source, SOURCE_LABELS["unknown"]),
            "updated_at": str(record.get("updated_at") or ""),
        })
    return facts, hidden


def _read_episodes(db_path, limit: int) -> list[dict]:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, date, ts, kind, text FROM episodes"
                " ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — no episodes table yet:
        return []  # the read stands without them
    return [{
        "id": row["id"],
        "date": str(row["date"] or ""),
        "ts": str(row["ts"] or ""),
        "kind": str(row["kind"] or ""),
        "text": str(row["text"] or ""),
    } for row in rows]


def read_memory(db_path=None, limit=100) -> dict:
    """{"facts", "episodes", "excluded"}: everything VYUK
    remembers, with each fact's source. `limit` caps facts and
    episodes each. `excluded` is the count of credential-like
    facts hidden from the read (never their keys or values)."""
    try:
        count = int(limit)
    except (TypeError, ValueError):
        count = 100
    if count <= 0:
        return {"facts": [], "episodes": [], "excluded": 0}
    path = str(db_path) if db_path is not None else str(DB_PATH)
    facts, hidden = _read_facts(path, count)
    episodes = _read_episodes(path, count)
    return {"facts": facts, "episodes": episodes, "excluded": hidden}

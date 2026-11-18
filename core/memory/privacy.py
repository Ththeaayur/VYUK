"""Wave 6 (v5.61) — PF-3 privacy dashboard (reads the D-1 format).

See the facts she stores about the user, delete one, wipe all,
export the milestone log. A first-party surface over the Wave 6
file store; NO new store (the dashboard reads the D-1 files +
the frozen DB through dual_read, exactly like the prompt path).

Ship shape, stated plainly: the dashboard LOGIC is live here
(list / delete / wipe / export, all tested). The GUI surface is
STAGED, not shipped in this wave: the sidebar is frozen at 7
buttons and the page indices are frozen (Doctor 7, Settings 4)
by the v5.57 owner override, and no new tool declarations are
allowed (registry 480, fitted 229,785 B). A GUI page would
shift indices or spend declarations — both forbidden this wave
— so surfacing lands in Wave 9 (Premium Surface), which owns
surface work. Calling this module's functions from a future
surface needs no store migration: the API is the surface's
backend.

Wipe is fail-closed: `wipe_all()` refuses unless called with
confirm="WIPE ALL" exactly. Anything else returns a refusal
record and deletes nothing. Milestone export reads
data/milestones.jsonl (append-only log, Wave 0) and returns
its lines; it never modifies the log.

This module imports stdlib + the Wave 6 file store only.
"""

import json
import os

from ..config import DATA_DIR
from .memory_files import (
    delete_fact, dual_read, list_facts, memories_dir,
)

WIPE_CONFIRM = "WIPE ALL"


def list_stored_facts(base=None, kinds=None):
    """Everything the dashboard shows: the dual-read union
    (files + DB), with kinds. Opinions are included here —
    the dashboard is the ONE place opinions are visible as
    themselves (labelled opinion), because the user must be
    able to see what is stored about them. The fact PROMPT
    still never renders opinions (CP-4.7)."""
    db_facts = []
    # DB facts are read through the singleton store when no base
    # override is given; tests pass base and use the file store
    # only (their DB is tmp — see the privacy suite).
    if base is None:
        try:
            from .memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
            db_facts = [fact for fact in
                        memory_store.get_all_facts()
                        if str(fact.get("category") or "").lower()
                        != "session_summary"]
        except Exception:  # noqa: BLE001
            db_facts = []
    merged = dual_read(db_facts, base=base)
    file_records, _skipped = list_facts(base=base)
    by_identity = {}
    for fact in merged:
        by_identity[(str(fact.get("category", "")).strip().lower(),
                     str(fact.get("key", "")).strip())] = {
            "category": fact.get("category", ""),
            "key": fact.get("key", ""),
            "value": fact.get("value", ""),
            "kind": fact.get("_kind", "fact"),
            "from_file": bool(fact.get("_from_file", False)),
        }
    for record in file_records:
        identity = (record["category"].strip().lower(),
                    record["key"].strip())
        if identity not in by_identity:
            by_identity[identity] = {
                "category": record["category"],
                "key": record["key"],
                "value": record["value"],
                "kind": record["kind"],
                "from_file": True,
            }
    visible = sorted(by_identity.values(),
                     key=lambda item: (item["category"], item["key"]))
    if kinds is not None:
        visible = [item for item in visible
                   if item["kind"] in kinds]
    return visible


def delete_one(category, key, base=None):
    """Delete one fact from the file store. DB rows are NOT
    deleted by the dashboard in this wave (the frozen DB is
    rollback material; a DB-side delete would break rollback) —
    the record states that plainly when the key is DB-only."""
    if delete_fact(category, key, base=base):
        return {"deleted": True, "category": category, "key": key,
                "note": "file record deleted"}
    return {"deleted": False, "category": category, "key": key,
            "note": "no file record for this key (DB rows are "
                    "not deleted by the dashboard — the frozen "
                    "DB is rollback material)"}


def wipe_all(confirm="", base=None):
    """Wipe ALL file-store facts. Fail-closed: anything other
    than confirm='WIPE ALL' refuses and deletes nothing."""
    if confirm != WIPE_CONFIRM:
        return {"wiped": 0, "refused": True,
                "reason": "wipe requires confirm='WIPE ALL' exactly"}
    root = memories_dir(base)
    wiped = 0
    if os.path.isdir(root):
        for dirpath, dirnames, filenames in os.walk(root):
            if "_expired" in dirnames:
                dirnames.remove("_expired")
            for name in filenames:
                if not name.endswith(".md"):
                    continue
                try:
                    os.remove(os.path.join(dirpath, name))
                    wiped += 1
                except OSError:
                    continue
    receipt = os.path.join(root, "wipe_receipt.json") \
        if os.path.isdir(root) else None
    if receipt:
        try:
            with open(receipt, "w", encoding="utf-8") as handle:
                json.dump({"wiped": wiped}, handle)
        except OSError:
            pass
    return {"wiped": wiped, "refused": False}


def export_milestones(base=None):
    """Export the milestone log (Wave 0 append-only
    data/milestones.jsonl). Read-only: the log is never
    modified. Returns the lines (parsed where possible)."""
    path = os.path.join(str(base or DATA_DIR), "milestones.jsonl")
    lines = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except ValueError:
                    lines.append({"raw": line})
    except OSError:
        return {"entries": [], "available": False,
                "reason": "milestone log absent"}
    return {"entries": lines, "available": True}

"""Review Inbox — the three real sources of things to review.

The inbox lists only what the store can actually show, from
three sources and no others:

1. Unseen learned facts — a fact whose source is learned /
   learned:voice / learned:text and whose id is not in
   ``learning/seen_ids``.
2. Inferred-kind records — a fact whose category is "inferred"
   (the KINDS vocabulary: an inference, not something the user
   said).
3. Duplicates / conflicts, on EXACT store fields only — the same
   key in two categories, the same (key, value) twice, or a
   learned fact whose key matches a hand-saved fact with a
   different value. NO fuzzy matching, NO similarity scores:
   the key either matches exactly or it is not a conflict.

Every row explains itself (its "why" line, in plain lowercase
words) and shows both values with their ages ("last updated")
and sources, so the user can decide from the row alone.

Actions:
* Approve — mark reviewed ONLY. The record keeps its kind and
  never returns to the inbox: the review is recorded in
  ``learning/seen_ids`` (for a learned fact) and in
  ``learning/reviewed_ids`` (for any fact — an inferred record
  is removed by the mark, not by changing its kind).
* Reject — forget the fact (the GUI's undo bar offers it back).
* Revise — edit the fact (the GUI's upsert; a rename is forget
  old + save new, one undo).
* A conflict row also offers "Keep the newer" (forget the
  older), "Keep the hand-saved one" (forget the learned one)
  and "Keep both" (forget nothing — mark the group reviewed).

Private mode: the inbox shows "Private mode is on" and the
count still shows (H-01 / M-01) — the GUI renders that line;
this module never blanks a row itself.
"""

from __future__ import annotations

import datetime

_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d",
)

_HAND_SAVED_SOURCES = ("user", "correction")


def fact_epoch(fact: dict | None) -> float | None:
    """A fact row's updated_at as a local-time epoch, or None
    when the row carries no parseable date (missing, empty,
    garbage). Undated facts are never hidden by a date rule."""
    raw = str((fact or {}).get("updated_at") or "").strip()
    if not raw:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.datetime.strptime(raw, fmt).timestamp()
        except ValueError:
            continue
    return None


def age_text(updated_at) -> str:
    """Short 'when' for a fact: today / yesterday / 12 Nov /
    12 Nov 2025. Empty when the fact carries no readable date."""
    epoch = None
    if isinstance(updated_at, (int, float)):
        epoch = float(updated_at)
    else:
        epoch = fact_epoch({"updated_at": updated_at})
    if epoch is None:
        return ""
    try:
        stamp = datetime.datetime.fromtimestamp(epoch)
    except (OSError, OverflowError, ValueError):
        return ""
    today = datetime.date.today()
    day = stamp.date()
    if day == today:
        return "today"
    if day == today - datetime.timedelta(days=1):
        return "yesterday"
    if stamp.year == today.year:
        return stamp.strftime("%d %b")
    return stamp.strftime("%d %b %Y")


def _is_learned(fact: dict) -> bool:
    return str(fact.get("source") or "").strip().lower().startswith("learned")


def _is_inferred(fact: dict) -> bool:
    return str(fact.get("category") or "").strip().lower() == "inferred"


def _learned_origin(fact: dict) -> str:
    """Where a learned fact came from, in the why line's words."""
    source = str(fact.get("source") or "").strip().lower()
    if source.startswith("learned:"):
        return "learned from chat"
    return "learned from use"


def collect_inbox_rows(store, seen_ids=None, reviewed_ids=None):
    """Every inbox row, from the live store.

    ``seen_ids`` / ``reviewed_ids`` are sets of fact ids (the
    QSettings lists, as strings). A row is a dict:
    {"kind", "why", "facts", "fact_ids", "title"}. Facts in a
    row carry their age ("last updated") and source, so the
    GUI can show both values of a conflict without a second
    read. Rows are ordered: conflicts first, then inferred,
    then unseen learned — the most decision-heavy first. A
    fact appears in at most one row.
    """
    seen = {str(i) for i in (seen_ids or [])}
    reviewed = {str(i) for i in (reviewed_ids or [])}
    try:
        facts = list(store.get_all_facts() or [])
    except Exception:  # noqa: BLE001 — an unreadable store
        return []  # shows the empty state, never a crash
    for fact in facts:
        fact["_age"] = age_text(fact.get("updated_at"))
    used: set[str] = set()
    rows: list[dict] = []

    # -- conflicts: one row per key stored more than once --------
    by_key: dict[str, list[dict]] = {}
    for fact in facts:
        key = str(fact.get("key") or "").strip()
        if key:
            by_key.setdefault(key, []).append(fact)
    for key, group in sorted(by_key.items()):
        if len(group) < 2:
            continue
        if any(str(f.get("id")) in reviewed for f in group):
            continue
        values = {str(f.get("value") or "") for f in group}
        if len(values) == 1:
            why = ("same field, two values — the same value stored "
                   "in two categories; keep one copy or both.")
        else:
            why = ("same field, two values — this key is stored in "
                   "two places with different values; keep the "
                   "newer, the hand-saved one, or both.")
        rows.append({
            "kind": "conflict",
            "why": why,
            "facts": sorted(group, key=lambda f: fact_epoch(f) or 0.0),
            "fact_ids": [f.get("id") for f in group],
            "title": key,
        })
        used.update(str(f.get("id")) for f in group)

    # -- inferred-kind records ------------------------------------
    for fact in facts:
        fid = str(fact.get("id"))
        if fid in used or fid in reviewed:
            continue
        if _is_inferred(fact):
            rows.append({
                "kind": "inferred",
                "why": ("inferred fact — VYUK inferred this; approve "
                        "if it is right, revise if it is not."),
                "facts": [fact],
                "fact_ids": [fact.get("id")],
                "title": str(fact.get("key") or ""),
            })
            used.add(fid)

    # -- unseen learned facts --------------------------------------
    for fact in facts:
        fid = str(fact.get("id"))
        if fid in used or fid in reviewed or fid in seen:
            continue
        if _is_learned(fact):
            rows.append({
                "kind": "unseen_learned",
                "why": (f"{_learned_origin(fact)}, unseen — VYUK "
                        "picked this up and you have not seen it "
                        "yet; approve to keep it, reject to forget it."),
                "facts": [fact],
                "fact_ids": [fact.get("id")],
                "title": str(fact.get("key") or ""),
            })
            used.add(fid)

    return rows


def mark_reviewed(row, seen_ids=None, reviewed_ids=None):
    """Approve: the ids to add to seen_ids / reviewed_ids.

    Takes the inbox row (its facts say which are learned).
    Returns (new_seen_ids, new_reviewed_ids) as sorted lists
    of strings — the GUI persists them to QSettings. Every
    approved id lands in reviewed_ids (that is what keeps it
    out of the inbox); a learned fact's id lands in seen_ids
    too, so the learned section stops flagging it as new.
    """
    seen = {str(i) for i in (seen_ids or [])}
    reviewed = {str(i) for i in (reviewed_ids or [])}
    for fact in ((row or {}).get("facts") or []):
        fid = str(fact.get("id"))
        reviewed.add(fid)
        if _is_learned(fact):
            seen.add(fid)
    return sorted(seen), sorted(reviewed)


def resolve_conflict(store, row, choice):
    """Resolve a conflict row; returns the forgotten fact dicts
    (for the GUI's undo bar — an empty list when nothing was
    forgotten).

    ``choice`` is "newer" (forget the older), "hand_saved"
    (forget the learned one — falling back to the older when
    neither is hand-saved) or "both" (forget nothing; the group
    is marked reviewed by the caller).
    """
    facts = list((row or {}).get("facts") or [])
    if len(facts) < 2 or choice not in ("newer", "hand_saved", "both"):
        return []
    if choice == "both":
        return []
    if choice == "newer":
        ordered = sorted(facts, key=lambda f: fact_epoch(f) or 0.0)
        loser = ordered[0]
    else:  # hand_saved: keep the hand-saved one, forget the learned one
        hand = [f for f in facts
                if str(f.get("source") or "").strip().lower()
                in _HAND_SAVED_SOURCES]
        learned = [f for f in facts if _is_learned(f)]
        if hand and learned:
            loser = learned[0]
        else:
            ordered = sorted(facts, key=lambda f: fact_epoch(f) or 0.0)
            loser = ordered[0]
    try:
        if store.delete_fact(loser.get("id")):
            return [loser]
    except Exception:  # noqa: BLE001 — a failed forget is a
        pass  # no-op, never a crash
    return []

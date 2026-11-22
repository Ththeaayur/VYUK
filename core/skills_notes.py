"""Notes, wishlist, watchlist and goal tracker — the owner's own
lists (Stage 0 features batch 4, Group G, 2026-11-22).

Four action-dispatched tools over memory facts (the reading_list
precedent: one fact per item, JSON value, `_load_*` with a broken
count, every write re-read before the claim — A1-store). All four
are effectful, FREE by design (app-state): their only writes are
the owner's own list facts, local and reversible, nothing leaves
the machine. Their list/read actions are owner-only (L-24): a
note, a wishlist, a watchlist or a goal is the owner's private
material, so list/read refuses in an unproven live-origin
session, the subscription_audit precedent.

- `notes`: take a note, list them (newest first, numbered),
  read one back by its number, remove one by its number.
- `wishlist`: things the user wants, with an optional price and
  note; add / list / remove by name.
- `watchlist`: films and shows to watch, with an optional kind
  (movie/show); add / list / mark_watched / remove by title.
- `goal_tracker`: the user's own goals with a target and
  progress (not the product's goal system — read a book, run a
  distance, save an amount); add / list / update / remove by
  name. Update SETS the current progress (absolute, idempotent
  — a repeated call cannot double-count); the list shows
  progress vs target with % and names a goal complete at or
  over its target.

Numbers are plain numbers, no currency assumed (the expense_log
precedent): a wishlist price or a goal target is the number the
user gave, with an optional unit word for goals.
"""

from __future__ import annotations

import datetime
import json

# INTENTIONAL: cross-part names — list functions resolve these bare
# names in their defining module's globals, so they are imported here,
# not relied on from the facade (bare-name rule, plan §2 P3).
from .skills_money_helpers import (
    _coerce_float,
    _facts,
    _store,
)

_MAX_TITLE_CHARS = 100
_MAX_TEXT_CHARS = 2000
_MAX_ITEM_CHARS = 200
_MAX_NOTE_CHARS = 500

_NOTES_CATEGORY = "notes"
_WISHLIST_CATEGORY = "wishlist"
_WATCHLIST_CATEGORY = "watchlist"
_GOALS_CATEGORY = "goals"


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _new_note_key(existing_keys) -> str:
    """A unique key for a new note: the timestamp, bumped with a
    counter while it collides with an existing key."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    key = stamp
    n = 2
    while key in existing_keys:
        key = f"{stamp}-{n}"
        n += 1
    return key


def _by_index(entries: list, index, what: str):
    """The entry at 1-based `index`, or (None, error-line). The
    list order is the store order, so an index from the last
    list is valid until the store changes."""
    try:
        n = int(float(index))
    except (TypeError, ValueError):
        return None, (f"That needs the {what}'s number from the "
                      f"list, e.g. 1.")
    if n < 1 or n > len(entries):
        if not entries:
            return None, f"There is nothing on the {what} list yet."
        return None, (f"That number is out of range — the list "
                      f"has {len(entries)} entr"
                      f"{'y' if len(entries) == 1 else 'ies'}, "
                      f"numbered 1 to {len(entries)}.")
    return entries[n - 1], ""


# ---------------------------------------------------------------------------
# NOTES — take a note, list, read one back, remove one
# ---------------------------------------------------------------------------


def _load_notes() -> tuple[list, int]:
    """(entries, broken_count): {id, key, title, text, added}
    dicts parsed from the notes facts; unparseable ones counted.
    Newest first (the key is the timestamp)."""
    entries = []
    broken = 0
    for fact in _facts(_NOTES_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            if not isinstance(stored, dict):
                raise ValueError("not a note")
            text = str(stored["text"])
            if not text.strip():
                raise ValueError("empty note")
            entries.append({
                "id": fact.get("id"),
                "key": str(fact.get("key") or ""),
                "title": str(stored.get("title") or ""),
                "text": text,
                "added": str(stored.get("added") or ""),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: e["key"], reverse=True)
    return entries, broken


def _note_line(entry: dict, n: int) -> str:
    head = entry["title"] or entry["text"].splitlines()[0][:60]
    line = f"{n}. {head} ({entry['added']})"
    return line


def notes(action: str = "", text: str = "", title: str = "",
          index=0) -> str:
    """Take a note, list your notes, read one back, or remove
    one. 'add' stores the text (title optional — without one the
    first line is the title); 'list' shows them newest first,
    numbered; 'read' reads one back by its number from the list;
    'remove' drops one by its number."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list", "read", "remove"):
        return ("Note actions are 'add', 'list', 'read' and "
                "'remove' — e.g. notes with action 'add' and "
                "the text of the note.")
    try:
        entries, broken = _load_notes()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the notes: {e}"
    broken_note = ""
    if broken:
        broken_note = (f" ({broken} stored note"
                       f"{'s' if broken != 1 else ''} couldn't "
                       "be read and were skipped.)")
    if action == "list":
        if not entries:
            return ("No notes yet. Take one with notes, action "
                    f"'add' and the text.{broken_note}")
        lines = [f"Notes ({len(entries)}):"]
        lines += [_note_line(e, n + 1)
                  for n, e in enumerate(entries)]
        if broken:
            lines.append(broken_note.strip())
        return "\n".join(lines)
    if action in ("read", "remove"):
        entry, err = _by_index(entries, index, "note")
        if entry is None:
            return err
        if action == "read":
            head = entry["title"]
            out = f"Note {index} ({entry['added']}):\n"
            if head:
                out += f"{head}\n"
            return out + entry["text"]
        try:
            _store().delete_fact(entry["id"])
        except Exception as e:  # noqa: BLE001
            return f"Couldn't remove the note: {e}"
        try:
            after, _b = _load_notes()
            gone = all(e["key"] != entry["key"] for e in after)
        except Exception:  # noqa: BLE001
            gone = False
        if not gone:
            return ("I tried to remove that note but it's still "
                    "there — nothing was claimed.")
        return (f"Removed note {index}"
                f" ('{entry['title'] or entry['text'][:40]}').")
    # action == "add"
    clean = " ".join(str(text or "").split())
    if not clean:
        return "That needs the note's text."
    if len(clean) > _MAX_TEXT_CHARS:
        return (f"That note is {len(clean)} characters — notes "
                f"are capped at {_MAX_TEXT_CHARS}.")
    head = " ".join(str(title or "").split())[:_MAX_TITLE_CHARS]
    if not head:
        head = clean.splitlines()[0][:60]
    try:
        key = _new_note_key({e["key"] for e in entries})
        _store().save_fact(
            _NOTES_CATEGORY, key,
            json.dumps({"title": head, "text": clean,
                        "added": _today_iso()}))
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the note: {e}"
    # A1-store: re-read before claiming the add.
    try:
        after, _b = _load_notes()
        saved = any(e["key"] == key for e in after)
    except Exception:  # noqa: BLE001
        saved = False
    if not saved:
        return ("I tried to take that note but couldn't confirm "
                "it stuck — nothing was claimed.")
    return f"Noted: '{head}'."


# ---------------------------------------------------------------------------
# WISHLIST — things the user wants, with an optional price
# ---------------------------------------------------------------------------


def _load_wishlist() -> tuple[list, int]:
    """(entries, broken_count): {id, key, item, price, note,
    added} dicts parsed from the wishlist facts; unparseable
    ones counted. Alphabetical by item."""
    entries = []
    broken = 0
    for fact in _facts(_WISHLIST_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            if not isinstance(stored, dict):
                raise ValueError("not a wishlist item")
            item = str(stored["item"])
            if not item.strip():
                raise ValueError("empty item")
            price = stored.get("price")
            price = float(price) if price is not None else None
            if price is not None and price <= 0:
                price = None
            entries.append({
                "id": fact.get("id"),
                "key": str(fact.get("key") or ""),
                "item": item,
                "price": price,
                "note": str(stored.get("note") or ""),
                "added": str(stored.get("added") or ""),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: e["item"].lower())
    return entries, broken


def _find_wishlist(entries: list, item: str):
    wanted = (item or "").strip().lower()
    for entry in entries:
        if entry["item"].strip().lower() == wanted:
            return entry
    return None


def _wishlist_line(entry: dict) -> str:
    line = f"• {entry['item']}"
    if entry.get("price") is not None:
        line += f" — {entry['price']:g}"
    if entry.get("note"):
        line += f" ({entry['note']})"
    return line


def wishlist(action: str = "", item: str = "", price=0,
             note: str = "") -> str:
    """Keep a wishlist: 'add' records something you want (price
    and a note are optional), 'list' shows it, 'remove' drops
    one by name. Prices are plain numbers, no currency assumed."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list", "remove"):
        return ("Wishlist actions are 'add', 'list' and "
                "'remove' — e.g. wishlist with action 'add' and "
                "the thing you want.")
    try:
        entries, broken = _load_wishlist()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the wishlist: {e}"
    broken_note = ""
    if broken:
        broken_note = (f" ({broken} stored entr"
                       f"{'ies' if broken != 1 else 'y'} couldn't "
                       "be read and were skipped.)")
    if action == "list":
        if not entries:
            return ("Your wishlist is empty. Add something with "
                    f"wishlist, action 'add' and the item."
                    f"{broken_note}")
        lines = [f"Wishlist ({len(entries)}):"]
        lines += [_wishlist_line(e) for e in entries]
        if broken:
            lines.append(broken_note.strip())
        return "\n".join(lines)
    clean = " ".join(str(item or "").split())
    if not clean:
        return "That needs the thing you want."
    if len(clean) > _MAX_ITEM_CHARS:
        return (f"That item is {len(clean)} characters — items "
                f"are capped at {_MAX_ITEM_CHARS}.")
    existing = _find_wishlist(entries, clean)
    if action == "add":
        if existing is not None:
            return (f"'{existing['item']}' is already on your "
                    "wishlist.")
        value = _coerce_float(price)
        if value is not None and value <= 0:
            value = None
        try:
            _store().save_fact(
                _WISHLIST_CATEGORY, clean.lower(),
                json.dumps({"item": clean, "price": value,
                            "note": " ".join(
                                str(note or "").split())
                            [:_MAX_NOTE_CHARS],
                            "added": _today_iso()}))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the wishlist item: {e}"
        try:
            after, _b = _load_wishlist()
            saved = _find_wishlist(after, clean) is not None
        except Exception:  # noqa: BLE001
            saved = False
        if not saved:
            return ("I tried to add that to your wishlist but "
                    "couldn't confirm it stuck — nothing was "
                    "claimed.")
        result = f"Added '{clean}' to your wishlist."
        if value is not None:
            result += f" Price noted: {value:g}."
        return result
    # action == "remove"
    if existing is None:
        known = (", ".join(e["item"] for e in entries)
                 if entries else "")
        extra = (f" On the list: {known}." if known else
                 " The list is empty.")
        return (f"'{clean}' isn't on your wishlist.{extra}")
    try:
        _store().delete_fact(existing["id"])
    except Exception as e:  # noqa: BLE001
        return f"Couldn't remove the wishlist item: {e}"
    try:
        after, _b = _load_wishlist()
        gone = _find_wishlist(after, clean) is None
    except Exception:  # noqa: BLE001
        gone = False
    if not gone:
        return ("I tried to remove that from your wishlist but "
                "it's still there — nothing was claimed.")
    return f"Removed '{existing['item']}' from your wishlist."


# ---------------------------------------------------------------------------
# WATCHLIST — films and shows to watch
# ---------------------------------------------------------------------------


def _load_watchlist() -> tuple[list, int]:
    """(entries, broken_count): {id, key, title, kind, status,
    note, added, watched} dicts parsed from the watchlist facts;
    unparseable ones counted. To-watch first, then watched,
    each alphabetical."""
    entries = []
    broken = 0
    for fact in _facts(_WATCHLIST_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            if not isinstance(stored, dict):
                raise ValueError("not a watchlist title")
            title = str(stored["title"])
            if not title.strip():
                raise ValueError("empty title")
            status = str(stored.get("status") or "to_watch")
            if status not in ("to_watch", "watched"):
                status = "to_watch"
            kind = str(stored.get("kind") or "")
            if kind not in ("movie", "show", ""):
                kind = ""
            entries.append({
                "id": fact.get("id"),
                "key": str(fact.get("key") or ""),
                "title": title,
                "kind": kind,
                "status": status,
                "note": str(stored.get("note") or ""),
                "added": str(stored.get("added") or ""),
                "watched": str(stored.get("watched") or ""),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: (e["status"] != "to_watch",
                                e["title"].lower()))
    return entries, broken


def _find_watch(entries: list, title: str):
    wanted = (title or "").strip().lower()
    for entry in entries:
        if entry["title"].strip().lower() == wanted:
            return entry
    return None


def _watch_line(entry: dict) -> str:
    line = f"• {entry['title']}"
    if entry.get("kind"):
        line += f" ({entry['kind']})"
    if entry.get("note"):
        line += f" — {entry['note']}"
    return line


def _save_watch(entry: dict) -> None:
    """Persist one watchlist entry (the stored shape)."""
    _store().save_fact(
        _WATCHLIST_CATEGORY, entry["title"].lower(),
        json.dumps({"title": entry["title"],
                    "kind": entry["kind"],
                    "status": entry["status"],
                    "note": entry["note"],
                    "added": entry["added"],
                    "watched": entry["watched"]}))


def watchlist(action: str = "", title: str = "", kind: str = "",
              note: str = "") -> str:
    """Keep a watchlist of films and shows: 'add' queues a title
    (kind 'movie' or 'show', optional), 'list' shows the
    to-watch shelf and the watched shelf separately,
    'mark_watched' moves a title to watched, 'remove' drops one
    by title."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list", "mark_watched", "remove"):
        return ("Watchlist actions are 'add', 'list', "
                "'mark_watched' and 'remove' — e.g. watchlist "
                "with action 'add' and the title.")
    try:
        entries, broken = _load_watchlist()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the watchlist: {e}"
    broken_note = ""
    if broken:
        broken_note = (f" ({broken} stored entr"
                       f"{'ies' if broken != 1 else 'y'} couldn't "
                       "be read and were skipped.)")
    if action == "list":
        if not entries:
            return ("Your watchlist is empty. Add a film or a "
                    f"show with watchlist, action 'add' and the "
                    f"title.{broken_note}")
        queued = [e for e in entries if e["status"] == "to_watch"]
        watched = [e for e in entries if e["status"] == "watched"]
        lines = [f"Watchlist ({len(entries)}):"]
        if queued:
            lines.append("To watch:")
            lines += [_watch_line(e) for e in queued]
        if watched:
            lines.append("Watched:")
            for entry in watched:
                line = _watch_line(entry)
                if entry["watched"]:
                    line += f" (watched {entry['watched']})"
                lines.append(line)
        if broken:
            lines.append(broken_note.strip())
        return "\n".join(lines)
    clean = " ".join(str(title or "").split())
    if not clean:
        return "That needs the title."
    if len(clean) > _MAX_TITLE_CHARS:
        return (f"That title is {len(clean)} characters — "
                f"titles are capped at {_MAX_TITLE_CHARS}.")
    existing = _find_watch(entries, clean)
    if action == "mark_watched":
        if existing is None:
            known = (", ".join(e["title"] for e in entries)
                     if entries else "")
            extra = (f" On the list: {known}." if known else
                     " The list is empty.")
            return (f"'{clean}' isn't on your watchlist.{extra}")
        if existing["status"] == "watched":
            return (f"'{existing['title']}' is already marked "
                    "as watched.")
        payload = dict(existing)
        payload["status"] = "watched"
        payload["watched"] = _today_iso()
        try:
            _save_watch(payload)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't update the watchlist: {e}"
        try:
            after, _b = _load_watchlist()
            saved = _find_watch(after, clean)
            ok = saved is not None and saved["status"] == "watched"
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            return ("I tried to mark that as watched but "
                    "couldn't confirm it stuck — nothing was "
                    "claimed.")
        return f"Marked '{existing['title']}' as watched."
    if action == "add":
        if existing is not None:
            if existing["status"] == "watched":
                return (f"'{existing['title']}' is already on "
                        "the list — and already marked watched.")
            return (f"'{existing['title']}' is already on your "
                    "watchlist.")
        key = (kind or "").strip().lower()
        if key not in ("movie", "show", ""):
            return ("The kind is 'movie' or 'show' — or leave "
                    "it out.")
        try:
            _store().save_fact(
                _WATCHLIST_CATEGORY, clean.lower(),
                json.dumps({"title": clean, "kind": key,
                            "status": "to_watch",
                            "note": " ".join(
                                str(note or "").split())
                            [:_MAX_NOTE_CHARS],
                            "added": _today_iso(),
                            "watched": ""}))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the watchlist title: {e}"
        try:
            after, _b = _load_watchlist()
            saved = _find_watch(after, clean) is not None
        except Exception:  # noqa: BLE001
            saved = False
        if not saved:
            return ("I tried to add that to your watchlist but "
                    "couldn't confirm it stuck — nothing was "
                    "claimed.")
        by = f" ({key})" if key else ""
        return f"Added '{clean}'{by} to your watchlist."
    # action == "remove"
    if existing is None:
        known = (", ".join(e["title"] for e in entries)
                 if entries else "")
        extra = (f" On the list: {known}." if known else
                 " The list is empty.")
        return (f"'{clean}' isn't on your watchlist.{extra}")
    try:
        _store().delete_fact(existing["id"])
    except Exception as e:  # noqa: BLE001
        return f"Couldn't remove the watchlist title: {e}"
    try:
        after, _b = _load_watchlist()
        gone = _find_watch(after, clean) is None
    except Exception:  # noqa: BLE001
        gone = False
    if not gone:
        return ("I tried to remove that from your watchlist but "
                "it's still there — nothing was claimed.")
    return f"Removed '{existing['title']}' from your watchlist."


# ---------------------------------------------------------------------------
# GOAL TRACKER — the user's own goals, with a target and progress
# ---------------------------------------------------------------------------


def _load_goals() -> tuple[list, int]:
    """(entries, broken_count): {id, key, name, target, unit,
    progress, added} dicts parsed from the goals facts;
    unparseable ones counted. Alphabetical by name."""
    entries = []
    broken = 0
    for fact in _facts(_GOALS_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            if not isinstance(stored, dict):
                raise ValueError("not a goal")
            name = str(stored["name"])
            if not name.strip():
                raise ValueError("empty name")
            target = float(stored["target"])
            if target <= 0:
                raise ValueError("target must be positive")
            progress = stored.get("progress")
            progress = float(progress) if progress is not None else 0.0
            if progress < 0:
                progress = 0.0
            entries.append({
                "id": fact.get("id"),
                "key": str(fact.get("key") or ""),
                "name": name,
                "target": target,
                "unit": str(stored.get("unit") or ""),
                "progress": progress,
                "added": str(stored.get("added") or ""),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: e["name"].lower())
    return entries, broken


def _find_goal(entries: list, name: str):
    wanted = (name or "").strip().lower()
    for entry in entries:
        if entry["name"].strip().lower() == wanted:
            return entry
    return None


def _goal_line(entry: dict) -> str:
    target = entry["target"]
    progress = entry["progress"]
    unit = f" {entry['unit']}" if entry["unit"] else ""
    pct = (progress / target * 100.0) if target > 0 else 0.0
    line = (f"• {entry['name']} — {progress:g}{unit} of "
            f"{target:g}{unit} ({pct:.0f}%)")
    if progress >= target:
        line += " — complete."
    return line


def _save_goal(entry: dict) -> None:
    """Persist one goal (the stored shape)."""
    _store().save_fact(
        _GOALS_CATEGORY, entry["name"].lower(),
        json.dumps({"name": entry["name"],
                    "target": entry["target"],
                    "unit": entry["unit"],
                    "progress": entry["progress"],
                    "added": entry["added"]}))


def goal_tracker(action: str = "", name: str = "", target=0,
                 unit: str = "", progress=0) -> str:
    """Track your own goals: 'add' records one with a target
    (unit optional, e.g. 'pages', 'km'), 'list' shows each with
    its progress against the target, 'update' sets the current
    progress, 'remove' drops one by name. Targets and progress
    are plain numbers, no currency assumed."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list", "update", "remove"):
        return ("Goal actions are 'add', 'list', 'update' and "
                "'remove' — e.g. goal_tracker with action 'add', "
                "a name and a target.")
    try:
        entries, broken = _load_goals()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the goals: {e}"
    broken_note = ""
    if broken:
        broken_note = (f" ({broken} stored goal"
                       f"{'s' if broken != 1 else ''} couldn't "
                       "be read and were skipped.)")
    if action == "list":
        if not entries:
            return ("No goals yet. Add one with goal_tracker, "
                    f"action 'add', a name and a target."
                    f"{broken_note}")
        lines = [f"Goals ({len(entries)}):"]
        lines += [_goal_line(e) for e in entries]
        if broken:
            lines.append(broken_note.strip())
        return "\n".join(lines)
    clean = " ".join(str(name or "").split())
    if not clean:
        return "That needs the goal's name."
    if len(clean) > _MAX_ITEM_CHARS:
        return (f"That name is {len(clean)} characters — names "
                f"are capped at {_MAX_ITEM_CHARS}.")
    existing = _find_goal(entries, clean)
    if action == "add":
        if existing is not None:
            return (f"'{existing['name']}' is already a goal — "
                    "update its progress with action 'update'.")
        value = _coerce_float(target)
        if value is None or value <= 0:
            return ("Adding a goal needs a target more than "
                    "zero.")
        payload = {"name": clean, "target": value,
                   "unit": " ".join(str(unit or "").split())
                   [:_MAX_TITLE_CHARS],
                   "progress": 0.0, "added": _today_iso()}
        try:
            _save_goal(payload)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the goal: {e}"
        try:
            after, _b = _load_goals()
            saved = _find_goal(after, clean) is not None
        except Exception:  # noqa: BLE001
            saved = False
        if not saved:
            return ("I tried to add that goal but couldn't "
                    "confirm it stuck — nothing was claimed.")
        unit_text = f" {payload['unit']}" if payload["unit"] else ""
        return (f"Added the goal '{clean}': target "
                f"{value:g}{unit_text}.")
    # action == "update" / "remove"
    if existing is None:
        known = (", ".join(e["name"] for e in entries)
                 if entries else "")
        extra = (f" Your goals: {known}." if known else
                 " You have no goals yet.")
        return (f"There's no goal called '{clean}'.{extra}")
    if action == "remove":
        try:
            _store().delete_fact(existing["id"])
        except Exception as e:  # noqa: BLE001
            return f"Couldn't remove the goal: {e}"
        try:
            after, _b = _load_goals()
            gone = _find_goal(after, clean) is None
        except Exception:  # noqa: BLE001
            gone = False
        if not gone:
            return ("I tried to remove that goal but it's still "
                    "there — nothing was claimed.")
        return f"Removed the goal '{existing['name']}'."
    # action == "update"
    value = _coerce_float(progress)
    if value is None or value < 0:
        return ("Updating a goal needs its current progress, a "
                "number zero or more.")
    payload = dict(existing)
    payload["progress"] = value
    try:
        _save_goal(payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't update the goal: {e}"
    try:
        after, _b = _load_goals()
        saved = _find_goal(after, clean)
        ok = saved is not None and saved["progress"] == value
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        return ("I tried to update that goal but couldn't "
                "confirm it stuck — nothing was claimed.")
    unit_text = f" {existing['unit']}" if existing["unit"] else ""
    pct = (value / existing["target"] * 100.0
           if existing["target"] > 0 else 0.0)
    result = (f"'{existing['name']}': {value:g}{unit_text} of "
              f"{existing['target']:g}{unit_text} ({pct:.0f}%)")
    if value >= existing["target"]:
        result += " — complete. Well done."
    return result + "."

"""Make group of the Words pack (moved verbatim from
core/skills_words.py): the quote vault, the pack's one write.
"""

import datetime
import json


# ---------------------------------------------------------------------------
# QUOTE VAULT — the pack's one write: quotes as memory facts
# ---------------------------------------------------------------------------

_QUOTES_CATEGORY = "quotes"
_MAX_QUOTE_CHARS = 1000
_MAX_AUTHOR_CHARS = 120
_MAX_TAG_CHARS = 60
_LIST_COUNT = 10


def _store():
    """The memory store singleton — this pack's only persistence
    seam. Tests substitute a temp MemoryStore by replacing this
    function; handlers never touch the singleton directly."""
    from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
    return memory_store


def _load_quotes() -> tuple[list, int]:
    """(entries, skipped): every parseable quote fact, each as
    {id, quote, author, tag, saved}. Corrupt payloads are
    counted, never fatal."""
    entries, skipped = [], 0
    for fact in _store().get_all_facts():
        if fact.get("category") != _QUOTES_CATEGORY:
            continue
        try:
            data = json.loads(fact.get("value") or "")
            quote = str(data["quote"])
        except (ValueError, TypeError, KeyError):
            skipped += 1
            continue
        entries.append({
            "id": fact.get("id") or 0,
            "quote": quote,
            "author": str(data.get("author") or ""),
            "tag": str(data.get("tag") or ""),
            "saved": str(data.get("saved") or ""),
        })
    entries.sort(key=lambda e: e["id"])
    return entries, skipped


def _quote_line(entry: dict) -> str:
    line = f"\"{entry['quote']}\""
    if entry["author"]:
        line += f" — {entry['author']}"
    if entry["tag"]:
        line += f" [{entry['tag']}]"
    return line


def _save_quote(quote: str, author: str, tag: str) -> str:
    quote = " ".join(str(quote or "").split())
    author = " ".join(str(author or "").split())
    tag = " ".join(str(tag or "").split())
    if not quote:
        return ("Tell me the quote to save — I need at least "
                "the words themselves.")
    for label, value, cap in (("quote", quote, _MAX_QUOTE_CHARS),
                              ("author", author, _MAX_AUTHOR_CHARS),
                              ("tag", tag, _MAX_TAG_CHARS)):
        if len(value) > cap:
            return (f"That {label} is {len(value)} characters — "
                    f"the vault caps a {label} at {cap}. "
                    "Shorten it and I'll save the quote.")
    today = datetime.date.today().isoformat()
    key = f"{author or 'unknown'} — {quote[:40]}"
    try:
        suffix = 1
        candidate = key
        while _store().get_fact(_QUOTES_CATEGORY,
                                candidate) is not None:
            suffix += 1
            candidate = f"{key} ({suffix})"
        _store().save_fact(
            _QUOTES_CATEGORY, candidate,
            json.dumps({"quote": quote, "author": author,
                        "tag": tag, "saved": today}))
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the quote: {e}"
    return ("Saved to the quote vault: "
            + _quote_line({"quote": quote, "author": author,
                           "tag": tag}))


def quote_vault(action: str = "list", quote: str = "",
                author: str = "", tag: str = "",
                query: str = "") -> str:
    """Save, find and list treasured quotes (memory-backed)."""
    action = (action or "list").strip().lower()
    if action not in ("save", "find", "list"):
        return ("Quote vault actions are 'save' (with quote, "
                "and optionally author and tag), 'find' (with a "
                "query) and 'list'.")
    if action == "save":
        return _save_quote(quote, author, tag)
    entries, skipped = _load_quotes()
    skip_note = (f" ({skipped} stored entr"
                 f"{'ies' if skipped != 1 else 'y'} couldn't be "
                 "read and were skipped.)" if skipped else "")
    if action == "find":
        needle = " ".join(str(query or "").split()).lower()
        if not needle:
            return ("What should I search the vault for? Give "
                    "me a query — it matches against the quote, "
                    "its author and its tag.")
        hits = [e for e in entries
                if needle in e["quote"].lower()
                or needle in e["author"].lower()
                or needle in e["tag"].lower()]
        if not hits:
            return (f"Nothing in the vault matches '{query}' — "
                    f"{len(entries)} quote(s) stored." + skip_note)
        shown = hits[-_LIST_COUNT:]
        lines = [f"{len(hits)} match(es) for '{query}':"]
        lines += [f"- {_quote_line(e)}" for e in shown]
        if len(hits) > len(shown):
            lines.append(f"(Showing the latest {len(shown)}.)")
        return "\n".join(lines) + ("\n" + skip_note if skip_note
                                   else "")
    # list
    if not entries:
        return ("The quote vault is empty — save one with "
                "action 'save' and the quote's words." + skip_note)
    shown = entries[-_LIST_COUNT:]
    lines = [f"Latest {len(shown)} of {len(entries)} quote(s) "
             "in the vault:"]
    lines += [f"- {_quote_line(e)}" for e in shown]
    return "\n".join(lines) + ("\n" + skip_note if skip_note
                               else "")

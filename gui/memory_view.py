"""Memory view data (GAPS-28, G-V1) — the view/model split for the
memory surfaces.

The two memory mixins (``window_memory_web`` and
``window_memory_learned``) used to query the stores directly and
render inline: ``memory_store.search_facts`` / ``get_all_facts``
in ``_refresh_memories``, ``scheduler.get_all_reminders`` in
``_refresh_reminders`` / ``_refresh_home``. The store query, the
filtering and the summary strings now live here as pure data
functions — no Qt, no widgets — and the mixins only render what
these return. Behaviour is identical: the same query, the same
category filter, the same "next" choice (first unfired in list
order, NOT the earliest — that is what ``_refresh_home`` showed)
and the same summary strings.

Defaults resolve lazily through the facade (GAPS-15 boundary)
so importing this module imports nothing from core; tests pass
an explicit store / scheduler.
"""

from __future__ import annotations


def _default_store():
    from voice_assistant.core.gui_facade import memory_store

    return memory_store


def _default_scheduler():
    from voice_assistant.core.gui_facade import scheduler

    return scheduler


#: The category-filter value that means "no filter" (the combo's
#: "All categories" entry, and the short form tests use).
ALL_CATEGORIES = "All categories"


def memory_facts_view(query: str = "", category: str = ALL_CATEGORIES,
                      store=None) -> dict:
    """The facts the Memory page shows, plus the category list.

    ``query`` searches the store (empty query lists everything);
    ``category`` filters to one category (``ALL_CATEGORIES`` shows
    all). Returns ``{"facts", "categories", "total", "shown"}``:
    the shown facts in store order, the sorted category list over
    ALL facts (not just the shown ones — the combo stays honest),
    the total fact count, and the shown count.
    """
    store = store if store is not None else _default_store()
    facts = (store.search_facts(query) if query
             else store.get_all_facts())
    all_facts = store.get_all_facts()
    total = len(all_facts)
    cats = sorted({str(f.get("category") or "general")
                   for f in all_facts})
    if category in (ALL_CATEGORIES, "all"):
        shown = list(facts)
    else:
        shown = [f for f in facts
                 if str(f.get("category") or "general") == category]
    return {"facts": shown, "categories": cats, "total": total,
            "shown": len(shown)}


def memory_header_text(shown: int, total: int) -> str:
    """The Memory header count line: the header counts UNIQUE
    facts — the list rows shown/total, nothing else (parent
    ruling, hunt fix (c) header round, 2026-11-26). The
    Learned card renders the same facts a second time (once in
    the card, once in the list) and is never filtered by the
    search/category controls, but its rows are the SAME facts,
    so they do not add to the count: with N stored user facts
    the header reads "N facts remembered", and the "X of Y"
    form counts unique facts on both sides.

    This supersedes the E-F204 rule (header == list rows + card
    rows). That rule was right while the card rendered a
    disjoint set, but the item-6 widening made the card render
    every user fact — the same facts already in the list — so
    the old rule counted every fact twice ("10 facts
    remembered" for 5 facts, "2" for 1). The `learned`
    parameter is gone with the rule: there is no second count
    to add, and no caller may reintroduce the doubling by
    passing one."""
    if shown == total:
        return (f"{total} fact{'s' if total != 1 else ''} "
                f"remembered")
    return f"{shown} of {total} facts shown"


def memory_web_header_text(fact_nodes: int) -> str:
    """The Memory header count line in Web view (E-F204 follow-up):
    the web shows one node per kept store fact (the graph caps each
    category at 40 nodes — MAX_PER_CATEGORY — so this is the fact
    nodes on the page, not the store total), and the Learned card
    and list are hidden, so their rows never count here. Always the
    plain form: the web's search only dims non-matches and the
    List "X of Y" form belongs to the List search/category filter,
    which does not apply in Web view."""
    return (f"{fact_nodes} fact{'s' if fact_nodes != 1 else ''} "
            f"remembered")


def reminders_view(scheduler=None) -> dict:
    """The reminders the Learned page shows, plus the home line.

    Returns ``{"reminders", "upcoming", "next", "summary"}``: all
    reminders in store order, the unfired ones, the next one for
    the home line (first unfired in list order — the same choice
    ``_refresh_home`` made), and the one-line summary string
    ``_refresh_reminders`` showed.
    """
    scheduler = scheduler if scheduler is not None else _default_scheduler()
    reminders = scheduler.get_all_reminders()
    upcoming = [r for r in reminders if not r["fired"]]
    nxt = upcoming[0] if upcoming else None
    if upcoming:
        next_fire = min(r["fire_at"] for r in upcoming)
        summary = (f"{len(upcoming)} upcoming · next fires "
                   f"{next_fire} · {len(reminders) - len(upcoming)} "
                   f"already spoken")
    elif reminders:
        summary = (f"Nothing upcoming · {len(reminders)} "
                   f"already spoken this session")
    else:
        summary = "Nothing scheduled this session."
    return {"reminders": reminders, "upcoming": upcoming, "next": nxt,
            "summary": summary}

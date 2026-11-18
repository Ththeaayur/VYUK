"""Article extraction — VYUK's own code (R10-5, python-readability PORT).

python-readability (buriy/python-readability, Apache-2.0) was
measured and NOT adopted: the 26 KB wheel drags in three new
declared dependencies — chardet (absent from the Qt venv),
cssselect (absent from BOTH lab interpreters) and
lxml-html-clean (absent from BOTH — ``lxml.html.clean`` is not
importable on either interpreter) — for an algorithm that is
~250 lines over lxml, which the tree ALREADY declares
(requirements.txt: ``lxml>=6.1.3,<7``, BSD-3; importable 5.2.1
on system python3, 6.1.3 in the Qt venv). The R8-3 sumy port
rejected the same shape (its closure pulled breadability +
lxml-html-clean + nltk for ~150 lines of stdlib). The algorithm
(Mozilla Readability / arc90 — score candidate containers by
text density plus class/id signals, drop boilerplate) is ported
here as own code; only xpath is used, never cssselect.

What the tree already had, and why the gap was still real:
the fetched-HTML path (core/skills_web_lookup.py:_page_text)
is a TAG-NAME strip — it keeps every text node outside
script/style/nav/header/footer/aside/form/svg/iframe, so a
sidebar, an ad block, a cookie banner or "related articles"
built from <div> + <p> all leak straight through; the live
browser path (core/desktop/browser_read.py:_page_text) prefers
``main, article, [role="main"]`` when one exists and falls back
to the whole body when it does not. Neither SCORES content:
no text-density ranking, no class/id signals, no decision
about which part of the page IS the article. That scoring is
what this module adds. The gap proof is in the suite: a page
whose sidebar <p> survives _page_text is dropped here.

Discipline (the R10-1 / R10-3 / R10-4 pattern):
* Read-only, local-only computation on HTML the caller already
  has — except the URL form, which fetches through the
  existing skills_web_lookup seams (_fetch, 512 KB cap, 10 s,
  status check), exactly as page_word_search does. No scripts
  are executed (lxml never runs JavaScript), no subresources
  are fetched, nothing is stored.
* The article text is untrusted web content: taint "web page"
  (tools_gate_taint.py), an egress read under taint when the
  URL form fetches (the page_word_search shape — NOT in
  _LOCAL_ONLY_READS, because the tool CAN fetch).
* Fail-closed: when no container scores above the bar, or the
  article text is under 140 characters, the tool refuses
  honestly — it NEVER falls back to the raw strip, which would
  hand back nav/ads/banners as if they were the article.
* The answer says which container won, its score, how many
  paragraphs were kept and how many boilerplate nodes were
  dropped, so the choice is inspectable, not magic.
"""

from __future__ import annotations

import re

from .skills_web_lookup import _PAGE_CAP, _fetch, _normalise_url, _status_of

# Same caps as the fetched-HTML path (skills_web_lookup.py).
_TEXT_CAP = 200_000  # characters of article text returned at most
_MIN_ARTICLE_CHARS = 140  # below this, there is no article to return
_MIN_NODE_CHARS = 25  # a text block shorter than this never scores
_MAX_SCORED_NODES = 2_000  # pathological pages stop scoring here

# Tag weights, in the shape of Mozilla Readability's getNodeWeight.
_TAG_WEIGHTS = {
    "div": 5,
    "pre": 3,
    "td": 3,
    "article": 3,
    "section": 5,
    "main": 10,
    "h1": -3,
    "h2": -3,
    "h3": -3,
    "h4": -3,
    "h5": -3,
    "h6": -3,
    "blockquote": 3,
    "form": -3,
    "address": -3,
    "ol": -3,
    "ul": -3,
    "dl": -3,
    "dd": -3,
    "dt": -3,
    "li": -3,
}

# Class/id signals, in the shape of Mozilla Readability's
# positive/negative regexes — but matched on TOKENS, not
# substrings: a class/id is split on non-alphanumerics and each
# token is matched whole. Substring matching would let
# "related-posts" inherit the "post" signal and "articleBody"
# inherit "article"; token matching keeps the signal honest.
# (camelCase names like "articleBody" do not match — a stated
# limitation; real sites use kebab-case.) A positive match
# outweighs a negative one on the same element.
_POSITIVE_TOKENS = frozenset({
    "article", "body", "content", "entry", "hentry", "main",
    "page", "pagination", "post", "text", "blog", "story",
})
_NEGATIVE_TOKENS = frozenset({
    "hidden", "banner", "combx", "comment", "contact", "foot",
    "footer", "footnote", "masthead", "media", "meta", "outbrain",
    "promo", "related", "scroll", "share", "shopp", "sidebar",
    "sponsor", "shopping", "tags", "tool", "widget", "ad", "ads",
    "advert", "cookie", "popup", "newsletter", "subscribe",
    "social", "recomm", "taboola",
})

# Subtrees removed before scoring (never content): scripts,
# styles, frames, embeds, forms, metadata. Comments go too.
_STRIP_TAGS = {
    "script", "style", "noscript", "iframe", "svg", "object",
    "embed", "form", "link", "meta",
}
# Block tags whose text is collected from the winning container.
_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "pre",
               "blockquote", "li"}
# Text blocks scored: paragraphs, preformatted text, headings.
_SCORE_TAGS = {"p", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}
# Never dropped as an unlikely candidate, whatever its class/id.
_NEVER_DROP = {"a", "body", "main", "article", "section", "html"}


def _clean(text: str) -> str:
    """Collapse whitespace the way the page-text path does."""
    return " ".join(str(text or "").split())


def _class_tokens(el) -> set:
    """An element's class + id as a set of lowercase tokens
    (split on anything that is not a letter or digit)."""
    raw = (el.get("class") or "") + " " + (el.get("id") or "")
    return set(re.split(r"[^a-z0-9]+", raw.lower())) - {""}


def _class_weight(el) -> int:
    """+25 / -25 from an element's class + id tokens, else 0. A
    positive token outweighs a negative one on the same element."""
    tokens = _class_tokens(el)
    if not tokens:
        return 0
    if tokens & _POSITIVE_TOKENS:
        return 25
    if tokens & _NEGATIVE_TOKENS:
        return -25
    return 0


def _node_weight(el) -> int:
    """Tag weight + class/id weight for one element."""
    return _TAG_WEIGHTS.get(el.tag, 0) + _class_weight(el)


def _is_unlikely(el) -> bool:
    """True when an element looks like boilerplate by class/id
    and nothing positive rescues it — the Mozilla
    unlikelyCandidates rule. Never drops body/main/article."""
    if el.tag in _NEVER_DROP:
        return False
    tokens = _class_tokens(el)
    if not tokens:
        return False
    return bool(tokens & _NEGATIVE_TOKENS
                and not tokens & _POSITIVE_TOKENS)


def _link_density(el) -> float:
    """Link text / total text of an element (0 when empty).
    A block that is mostly links is navigation, not article."""
    text = _clean("".join(el.itertext()))
    if not text:
        return 0.0
    link_text = "".join(
        "".join(a.itertext()) for a in el.iter("a"))
    return len(_clean(link_text)) / len(text)


def _describe(el) -> str:
    """A container as the answer names it: tag + class/id."""
    cls = el.get("class") or ""
    ident = el.get("id") or ""
    bits = [el.tag]
    if cls:
        bits.append(f'class="{cls}"')
    if ident:
        bits.append(f'id="{ident}"')
    return "<" + " ".join(bits) + ">"


def extract_article_html(html_text: str) -> dict:
    """Score a page's HTML and return its article body.

    Returns a dict: ok, title, text, winner, winner_score,
    paragraphs_kept, nodes_dropped, source. When ok is False,
    text is empty and reason says why — the caller must surface
    the refusal, never the raw strip. Pure: no network, no
    files, no state. lxml never executes scripts.
    """
    from lxml import html as _lxml_html

    if not isinstance(html_text, str) or not html_text.strip():
        return {"ok": False, "title": "", "text": "",
                "winner": "", "winner_score": 0.0,
                "paragraphs_kept": 0, "nodes_dropped": 0,
                "reason": "no HTML was supplied"}
    if len(html_text) > _PAGE_CAP:
        return {"ok": False, "title": "", "text": "",
                "winner": "", "winner_score": 0.0,
                "paragraphs_kept": 0, "nodes_dropped": 0,
                "reason": (f"the HTML is {len(html_text):,} "
                           f"characters, past the {_PAGE_CAP:,} "
                           "character cap")}
    try:
        doc = _lxml_html.fromstring(html_text)
    except Exception:  # noqa: BLE001 — unparseable HTML refuses
        return {"ok": False, "title": "", "text": "",
                "winner": "", "winner_score": 0.0,
                "paragraphs_kept": 0, "nodes_dropped": 0,
                "reason": "the HTML could not be parsed"}
    if doc is None:
        return {"ok": False, "title": "", "text": "",
                "winner": "", "winner_score": 0.0,
                "paragraphs_kept": 0, "nodes_dropped": 0,
                "reason": "the HTML could not be parsed"}

    title = ""
    titles = doc.xpath("//title")
    if titles:
        title = _clean("".join(titles[0].itertext()))

    # 1. Strip the never-content subtrees + comments + hidden nodes.
    dropped = 0
    for el in list(doc.iter()):
        if el.tag in _STRIP_TAGS:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
                dropped += 1
    for comment in doc.xpath("//comment()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
            dropped += 1
    for el in list(doc.iter()):
        style = (el.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
                dropped += 1

    # 2. Drop unlikely candidates (boilerplate by class/id).
    for el in list(doc.iter()):
        if _is_unlikely(el):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
                dropped += 1

    # 3. Score: each substantial text block adds its content
    # score to its parent (full) and grandparent (half). A
    # container's own tag + class weight is counted once, when
    # it is first created — the Mozilla shape.
    candidates: dict = {}

    def _candidate(el):
        if el is None or not isinstance(el.tag, str):
            return None
        key = id(el)
        if key not in candidates:
            candidates[key] = {"el": el, "score": float(_node_weight(el))}
        return candidates[key]

    scored = 0
    for el in doc.iter(*_SCORE_TAGS):
        if scored >= _MAX_SCORED_NODES:
            break
        text = _clean("".join(el.itertext()))
        if len(text) < _MIN_NODE_CHARS:
            continue
        scored += 1
        content = 1.0 + text.count(",") + min(len(text) / 100.0, 3.0)
        parent = el.getparent()
        if parent is not None:
            cand = _candidate(parent)
            if cand is not None:
                cand["score"] += content
            grand = parent.getparent()
            if grand is not None:
                cand = _candidate(grand)
                if cand is not None:
                    cand["score"] += content / 2.0

    if not candidates:
        return {"ok": False, "title": title, "text": "",
                "winner": "", "winner_score": 0.0,
                "paragraphs_kept": 0, "nodes_dropped": dropped,
                "reason": ("the page has no substantial text "
                           "blocks to score")}
    top = max(candidates.values(), key=lambda c: c["score"])
    if top["score"] <= 0:
        return {"ok": False, "title": title, "text": "",
                "winner": _describe(top["el"]),
                "winner_score": top["score"],
                "paragraphs_kept": 0, "nodes_dropped": dropped,
                "reason": ("the best-scoring container scored "
                           f"{top['score']:.1f} — nothing on the "
                           "page looks like an article body")}

    # 4. Gather the winner's siblings that score too (over a
    # fifth of the top score) or share its class.
    winner_el = top["el"]
    accepted = [winner_el]
    parent = winner_el.getparent()
    winner_class = winner_el.get("class") or ""
    if parent is not None:
        for sib in parent:
            if sib is winner_el or not isinstance(sib.tag, str):
                continue
            sib_cand = candidates.get(id(sib))
            sib_score = sib_cand["score"] if sib_cand else 0.0
            same_class = bool(winner_class) and sib.get("class") == winner_class
            if sib_score > 0.2 * top["score"] or same_class:
                accepted.append(sib)

    # 5. Collect headings + paragraphs from the accepted
    # containers, in document order, dropping boilerplate that
    # survived inside the winner (negative class/id, or a block
    # that is mostly links) and deduping repeats.
    paragraphs: list[str] = []
    seen: set[str] = set()
    for container in accepted:
        for block in container.iter(*_BLOCK_TAGS):
            if not isinstance(block.tag, str):
                continue
            # A block is dropped when it — or any ancestor up to
            # the accepted container — looks like boilerplate.
            node = block
            boilerplate = False
            while node is not None and node is not container:
                if _is_unlikely(node):
                    boilerplate = True
                    break
                node = node.getparent()
            if boilerplate:
                dropped += 1
                continue
            if _link_density(block) > 0.5:
                dropped += 1
                continue
            text = _clean("".join(block.itertext()))
            if len(text) < _MIN_NODE_CHARS or text in seen:
                continue
            seen.add(text)
            paragraphs.append(text)

    article = "\n\n".join(paragraphs)
    if len(article) < _MIN_ARTICLE_CHARS:
        return {"ok": False, "title": title, "text": "",
                "winner": _describe(winner_el),
                "winner_score": top["score"],
                "paragraphs_kept": len(paragraphs),
                "nodes_dropped": dropped,
                "reason": (f"the best container ({_describe(winner_el)}, "
                           f"score {top['score']:.1f}) holds only "
                           f"{len(article)} characters of text — "
                           "too short to be an article body")}
    if len(article) > _TEXT_CAP:
        article = article[:_TEXT_CAP]
    return {"ok": True, "title": title, "text": article,
            "winner": _describe(winner_el),
            "winner_score": top["score"],
            "paragraphs_kept": len(paragraphs),
            "nodes_dropped": dropped, "reason": ""}


def extract_article(url=None, html=None) -> str:
    """Extract the main article body from a web page.

    Give me either the page's address or its HTML — not both.
    The address form fetches through the existing web-lookup
    seams (512 KB cap, 10 s, status check); the HTML form takes
    HTML the caller already has. Read-only: nothing is stored,
    no scripts run, no subresources are fetched. The article
    text is untrusted web content (taint "web page").
    """
    if url and html:
        return ("Give me either the page's address or its HTML, "
                "not both.")
    if not url and not html:
        return ("Give me the address of the page to read, or "
                "paste its HTML.")
    source = ""
    if url:
        full, host = _normalise_url(url)
        if not host:
            return "Give me the address of the page to read."
        body, resp, err = _fetch(full, _PAGE_CAP)
        if body is None:
            return f"Couldn't fetch {full} — {err}."
        status = _status_of(resp)
        if status >= 400:
            return (f"{full} answered HTTP {status}, so there's "
                    "no page there to read.")
        html_text = body.decode("utf-8", errors="replace")
        source = full
        if len(body) >= _PAGE_CAP:
            source += (f" (the page reached the {_PAGE_CAP // 1024} "
                       "KB read limit, so only its first part was read)")
    else:
        html_text = html
        source = "caller-supplied HTML"
    result = extract_article_html(html_text)
    if not result["ok"]:
        where = f" on {source}" if source else ""
        return (f"Couldn't find an article body{where} — "
                f"{result['reason']}. I won't hand back the whole "
                "page as if it were the article; the unfiltered "
                "text is what page_word_search sees.")
    where = f"'{result['title']}' ({source})" if result["title"] else source
    return (
        f"Article: {where}\n\n{result['text']}\n\n"
        f"— how this was found: the best-scoring container was "
        f"{result['winner']} (score {result['winner_score']:.1f}); "
        f"{result['paragraphs_kept']} paragraphs kept, "
        f"{result['nodes_dropped']} boilerplate nodes dropped "
        f"(nav, ads, cookie banners, related links). I scored the "
        f"page's content — I did not strip tags by name, and I ran "
        f"no scripts and fetched no subresources. Pass this text "
        f"to summarise_text for a summary."
    )


def register_all(registry) -> None:
    """Register the python-readability pack (R10-5,
    python-readability port): extract_article — one read-only
    tool that scores a page's HTML and returns its main article
    body. Own code over the tree's already-declared lxml
    (BSD-3); nothing adopted, no chardet / cssselect /
    lxml-html-clean. The module owns its catalogue via
    register_all (same pattern as the other packs)."""
    registry.register(
        name="extract_article",
        description="Read the main article body from a web page, without the navigation, adverts, cookie banners and related-links boilerplate. Give me the page's address or its HTML: I'll score the page's content and return the article text with its title, and say which part of the page won and what was dropped. When no part of the page looks like an article body I'll say so honestly instead of handing back the whole page. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The page to read, e.g. 'https://example.com/article'"},
                "html": {"type": "string",
                         "description": "HTML you already have, e.g. from a page the browser is showing — give this or the URL, not both"},
            },
            "required": [],
        },
        handler=extract_article,
    )

"""Shared helpers and page-text parsing for the skills_web_watch pack (moved verbatim)."""

from html.parser import HTMLParser
from . import skills_web_research as _web5


_UA = _web5._UA
_TIMEOUT = 8                  # seconds, per probe (descriptions say so)
_PAGE_CAP = 512 * 1024        # page bytes read at most (as Web II)
_LINK_CAP = 15                # links checked per call
_READ_CAP = 3600              # article characters returned at most
_PRICE_FILE = "price_watch.json"
_SNAP_FILE = "page_summaries.json"


# ---------------------------------------------------------------------------
# Helpers — the Web II seams, re-exported so tests patch THIS module
# ---------------------------------------------------------------------------


def _fetch(url: str, cap: int):
    """skills_web_research._fetch behind a local name: (body, response,
    error_line) through the proxy-aware opener, ≤10 s."""
    return _web5._fetch(url, cap)


def _normalise_url(raw: str):
    """skills_web_research._normalise_url behind a local name."""
    return _web5._normalise_url(raw)


def _size_words(n: float) -> str:
    """A byte count in plain words: '812 KB', '3.4 MB'."""
    n = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return (f"{n:,.0f} {unit}" if unit == "bytes"
                    else f"{n:,.1f} {unit}")
        n /= 1024
    return f"{n:,.1f} GB"


# ---------------------------------------------------------------------------
# HTML → text: a small stdlib readability pass (shared by three tools)
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "nav", "header",
              "footer", "aside", "form", "svg", "iframe"}
_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li",
               "blockquote", "pre", "td"}


class _PageText(HTMLParser):
    """Collects a page's title and its block-level text, skipping
    the script/style/navigation subtrees."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.blocks: list[tuple[str, str]] = []
        self._skip = 0
        self._kind: str | None = None
        self._buf: list[str] = []
        self._in_title = False
        self._title_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif self._skip == 0 and tag == "title":
            self._in_title = True
        elif self._skip == 0 and tag in _BLOCK_TAGS and self._kind is None:
            self._kind = tag
            self._buf = []

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip > 0:
            self._skip -= 1
        elif tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._title_buf).split())
            self._title_buf = []
        elif self._skip == 0 and tag == self._kind:
            text = " ".join("".join(self._buf).split())
            if text:
                self.blocks.append((self._kind, text))
            self._kind = None
            self._buf = []

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self._title_buf.append(data)
        elif self._kind is not None:
            self._buf.append(data)


def _parse_page(html_text: str) -> _PageText:
    page = _PageText()
    try:
        page.feed(html_text)
        page.close()
    except Exception:  # noqa: BLE001 — partial parse still useful
        pass
    return page


def _visible_text(html_text: str) -> str:
    """All of a page's block text as one string (price hunting)."""
    page = _parse_page(html_text)
    return " ".join(text for _kind, text in page.blocks)


def _headline_lines(html_text: str) -> tuple[str, list[str]]:
    """(title, headline lines): the title, the headings, and the
    lead of each substantial paragraph — what a change summary
    can honestly compare. Deduped, order kept, capped."""
    page = _parse_page(html_text)
    lines: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            lines.append(text[:160])

    if page.title:
        add(page.title)
    for kind, text in page.blocks:
        if kind in ("h1", "h2", "h3"):
            add(text)
    for kind, text in page.blocks:
        if kind == "p" and len(text) >= 25:
            add(text)
    return page.title, lines[:60]

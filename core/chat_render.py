"""Chat rendering stack (Wave 9 / v5.64, Leaf 3).

Layers, in order:
  1. BASELINE: Qt-native GitHub-flavoured markdown. A fresh, empty
     per-message QTextDocument may be built with ``setMarkdown``
     once (see the trap below); after that the document is only ever
     appended to.
  2. CODE: Pygments inline-styled code blocks (lazy import; plain
     escaped <pre> fallback when Pygments is absent).
  3. FALLBACK RENDERER: markdown-it-py — the ONE fallback renderer
     shipped. The bake-off loser is NOT imported by the shipped path
     (see :func:`bakeoff` — it takes caller-supplied callables, so
     measuring a second engine never wires it into the product).

=================================================================
THE setMarkdown RESET TRAP — READ BEFORE TOUCHING THIS MODULE
=================================================================
``QTextDocument.setMarkdown()`` / ``setHtml()`` REPLACE the whole
document. Calling either per streamed chunk therefore ERASES every
earlier chunk of that message: the user watches the reply flicker
and lose its head on every token. That is the bug this module's API
exists to make unnatural:

* One QTextDocument PER MESSAGE (:class:`ChatMessageDocument`),
  never one document for the whole chat, never a rebuild per chunk.
* Streaming appends go through :meth:`ChatMessageDocument.append_text`,
  :meth:`ChatMessageDocument.append_html_fragment`, or
  :meth:`ChatMessageDocument.append_chunks` — all of which move a
  QTextCursor to the end and INSERT. They never call setMarkdown /
  setHtml on a non-empty document.
* :meth:`ChatMessageDocument.build_from_markdown` is allowed ONLY on
  an empty document (it asserts that) and exists for the first-build
  path only. Any call on a non-empty document raises — loudly, so a
  future streaming change fails in tests instead of flickering in
  front of the user.
* Batched appends are the natural path: pass a list of chunks to
  ``append_chunks`` and the cursor moves once per chunk, not once
  per character and never via a document reset.

Licences (verified from sdist primaries, not metadata/snippets):
  Pygments 2.21.0 — BSD-style licence (LICENSE: redistribution with
    copyright notice + disclaimer; the classic 2-clause BSD shape).
  markdown-it-py 4.2.0 — MIT (LICENSE, ExecutableBookProject).
  The bake-off loser (3.4.0) — BSD-style licence with a
    non-endorsement clause (LICENSE, Hsiaoming Yang). Recorded here
    for the record; it is NOT shipped, NOT imported by this module,
    and NOT in requirements.txt.
Neither shipped renderer is a hard dependency: both are lazy.

MODULE_VERSION = "5.64.0".
"""

from __future__ import annotations

import html
import importlib.util
import time
from dataclasses import dataclass

from PySide6.QtGui import QTextCursor, QTextDocument

MODULE_VERSION = "5.64.0"

# ---------------------------------------------------------------- probes


def _available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def probe_pygments() -> dict:
    if not _available("pygments"):
        return {"available": False, "version": None}
    import pygments  # noqa: PLC0415 — lazy by design

    return {"available": True, "version": getattr(pygments, "__version__", None)}


def probe_markdown_it() -> dict:
    if not _available("markdown_it"):
        return {"available": False, "version": None}
    import markdown_it  # noqa: PLC0415 — lazy by design

    return {"available": True,
            "version": getattr(markdown_it, "__version__", None)}


# ---------------------------------------------------------------- markdown


def render_markdown_fallback(text: str) -> str:
    """Render with markdown-it-py (table + strikethrough enabled).

    This is the ONE shipped fallback renderer. If markdown-it-py is
    absent, returns safely escaped plain text in a <p> — never an
    exception, never a second engine.
    """
    if not _available("markdown_it"):
        return f"<p>{html.escape(text)}</p>\n"
    from markdown_it import MarkdownIt  # noqa: PLC0415 — lazy by design

    parser = MarkdownIt().enable("table").enable("strikethrough")
    return parser.render(text)


def bakeoff(corpus: dict, renderers: dict) -> dict:
    """Measure caller-supplied renderers on a fixed corpus.

    ``renderers`` maps name -> callable(text) -> html. This module
    never imports a second engine itself; the suite supplies the
    callables from its own scratch environment. Returns per-document
    timings (ms, mean of 200 runs), output lengths, and a feature
    matrix (table / strikethrough / task-list / code block).
    """
    out = {}
    for name, text in corpus.items():
        row = {}
        for rname, fn in renderers.items():
            t0 = time.perf_counter()
            for _ in range(200):
                rendered = fn(text)
            ms = (time.perf_counter() - t0) / 200 * 1000
            row[rname] = {
                "ms": round(ms, 3),
                "len": len(rendered),
                "table": "<table" in rendered,
                "strike": ("<s>" in rendered or "<del>" in rendered),
                "task_checkbox": 'type="checkbox"' in rendered,
                "code_block": "<pre" in rendered,
            }
        out[name] = row
    return out


# Measured 2026-10-25 with markdown-it-py 4.2.0 vs the losing
# candidate 3.4.0 on the 12-document fixed corpus (suite file holds
# the corpus; numbers are mean ms over 200 renders per document).
# Full render-diff is in the Leaf 3 handoff; summary:
#  - Loser is faster on all 12 docs (~1.3x-2.4x) and renders task-list
#    checkboxes; markdown-it-py leaves "[x]/[ ]" as literal text.
#  - markdown-it-py passes raw inline HTML through (<b> stays <b>);
#    the loser escapes it (&lt;b&gt;).
#  - Identical output shape elsewhere: headings, lists, tables,
#    fenced code, blockquotes, links, hr, autolinks.
# Decision: ship markdown-it-py anyway — it is the designated
# fallback, CommonMark-conformant, and the task-list gap is a known
# limitation (closing step: render task lists in the Qt layer if a
# wave needs checkboxes; do NOT add a second engine to close it).
BAKEOFF_V564 = {
    "winner_shipped": "markdown-it-py 4.2.0",
    "loser_not_shipped": "mistune 3.4.0 (licence recorded, never imported)",
    "corpus_docs": 12,
    "loser_faster_on": "12/12 docs (approx 1.3x-2.4x)",
    "known_gap_shipped": "task-list checkboxes render as literal [x]/[ ] text",
    "known_diff_inline_html": "markdown-it-py passes raw HTML through; loser escapes it",
    "identical_elsewhere": [
        "headings", "lists", "tables", "fenced code", "blockquotes",
        "links", "hr", "autolinks", "emphasis", "strikethrough",
    ],
}

# The fixed bake-off corpus (also embedded in the suite for re-runs).
BAKEOFF_CORPUS = {
    "headings": "# Title\n## Section\n### Sub\n\nBody text.\n",
    "emphasis": "This is **bold**, *italic*, `code`, and ~~struck~~ text.\n",
    "lists": "- apple\n- banana\n  - nested\n\n1. first\n2. second\n",
    "task_list": "- [x] done\n- [ ] todo\n",
    "code_fence": "```python\ndef f(x):\n    return x + 1\n```\n",
    "table": "| A | B |\n|---|---|\n| 1 | 2 |\n",
    "link_image": "[Nova](https://example.com) and ![alt](https://example.com/i.png)\n",
    "blockquote": "> quoted\n> second line\n",
    "hr_rule": "Above\n\n---\n\nBelow\n",
    "inline_html": "Text with <b>raw html</b> inside.\n",
    "autolink": "Visit https://example.com for more.\n",
    "nested": "# Mix\n\n- **bold item** with `code`\n- second with [link](https://example.com)\n\n```\nplain block\n```\n",
}


# ---------------------------------------------------------------- code


def highlight_code(code: str, language: str = "text") -> str:
    """Pygments inline-styled HTML for one code block.

    Returns a fragment (spans with inline styles, wrapped in <pre>)
    suitable for :meth:`ChatMessageDocument.append_html_fragment`.
    Falls back to an escaped <pre> when Pygments is absent or the
    lexer is unknown — never raises for an unknown language.
    """
    if not _available("pygments"):
        return f"<pre>{html.escape(code)}</pre>"
    from pygments import highlight  # noqa: PLC0415 — lazy by design
    from pygments.formatters import HtmlFormatter  # noqa: PLC0415
    from pygments.lexers import TextLexer, get_lexer_by_name  # noqa: PLC0415

    try:
        lexer = get_lexer_by_name(language or "text")
    except Exception:  # noqa: BLE001 — unknown language -> plain text
        lexer = TextLexer()
    formatter = HtmlFormatter(nowrap=False, noclasses=True)
    return highlight(code, lexer, formatter)


# ---------------------------------------------------------------- documents


@dataclass
class ChatMessage:
    message_id: str
    role: str  # "user" | "assistant" | "system"


class ChatMessageDocument:
    """One message, one QTextDocument, append-only after first build.

    ``is_empty`` guards the only reset-style call
    (:meth:`build_from_markdown`); every other mutator inserts at
    the end cursor and preserves all earlier content.
    """

    def __init__(self, message: ChatMessage):
        self.message = message
        self.document = QTextDocument()
        self._chunks = 0

    @property
    def is_empty(self) -> bool:
        # PySide6 exposes QTextDocument.isEmpty as a property (Qt6),
        # while older bindings expose it as a method — accept both.
        value = self.document.isEmpty
        return value() if callable(value) else bool(value)

    @property
    def chunk_count(self) -> int:
        return self._chunks

    def build_from_markdown(self, text: str) -> None:
        """First-build only. Raises on a non-empty document (the trap)."""
        if not self.is_empty:
            raise RuntimeError(
                "build_from_markdown called on a non-empty document: "
                "setMarkdown would erase earlier chunks. Use "
                "append_text / append_html_fragment / append_chunks."
            )
        self.document.setMarkdown(text)
        self._chunks += 1

    def append_text(self, text: str) -> None:
        cursor = QTextCursor(self.document)
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self._chunks += 1

    def append_html_fragment(self, fragment: str) -> None:
        cursor = QTextCursor(self.document)
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(fragment)
        self._chunks += 1

    def append_chunks(self, chunks) -> None:
        """The natural streaming path: batched inserts, no resets."""
        for chunk in chunks:
            self.append_text(chunk)

    def append_code_block(self, code: str, language: str = "text") -> None:
        self.append_html_fragment(highlight_code(code, language))

    def append_markdown_chunk(self, markdown_chunk: str) -> None:
        """Append one markdown chunk WITHOUT resetting the document.

        The chunk alone is rendered via the markdown-it-py fallback
        and inserted as an HTML fragment. (Per-chunk rendering is
        why streaming markdown can split a construct across chunks;
        callers that need exact block rendering should buffer to a
        block boundary and then call this once per block.)
        """
        self.append_html_fragment(render_markdown_fallback(markdown_chunk))

    def plain_text(self) -> str:
        return self.document.toPlainText()

    def html(self) -> str:
        return self.document.toHtml()


class ChatRenderer:
    """Owns one document per message id. No whole-chat resets, ever."""

    def __init__(self):
        self._docs: dict[str, ChatMessageDocument] = {}

    def begin_message(self, message_id: str, role: str = "assistant") -> ChatMessageDocument:
        doc = ChatMessageDocument(ChatMessage(message_id, role))
        self._docs[message_id] = doc
        return doc

    def document_for(self, message_id: str) -> ChatMessageDocument | None:
        return self._docs.get(message_id)

    def append_chunks(self, message_id: str, chunks) -> ChatMessageDocument:
        doc = self._docs.get(message_id)
        if doc is None:
            doc = self.begin_message(message_id)
        doc.append_chunks(chunks)
        return doc

    def message_ids(self) -> list[str]:
        return list(self._docs.keys())

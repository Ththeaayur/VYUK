"""browser_read concern extracted from core/browser.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/browser."""

import re
from .. import injection  # Phase 3 D2a: injection stays flat in core/
from .browser_session import (_ELEMENT_TIMEOUT_MS, _active_page, _need_connection, _page_label, _short)  # GAPS-18: narrowed to the 5 used names
from .browser_elements import (_ELEMENTS_CAP, _ELEMENT_JS_HELPERS, _COLLECT_ELEMENTS_JS, _TAG_REFS_JS, _SINGLE_ELEMENT_JS, _element_row, _frame_marker, _KIND_FILTERS, _take_snapshot, _snapshot_from_candidates, _base_locator, _scoped_locator, _element_identity, _probe_int, _PAGE_PROBE_JS, _page_probe, _change_sentence)


_FIELDS_JS = r"""
() => {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return false;
    const cs = window.getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') {
      return false;
    }
    return true;
  };
  const all = document.querySelectorAll('button, input, select, textarea');
  const els = [];
  for (const el of all) {
    if (isVisible(el)) els.push(el);
  }
  const out = [];
  for (const el of els) {
    if (out.length >= 80) break;
    const tag = el.tagName.toLowerCase();
    const label = el.getAttribute('aria-label') || '';
    if (tag === 'input') {
      let d = `input[type=${el.getAttribute('type') || 'text'}]`;
      if (el.placeholder) d += ` placeholder='${el.placeholder}'`;
      if (el.name) d += ` name='${el.name}'`;
      if (label) d += ` label='${label}'`;
      out.push(d);
    } else if (tag === 'button') {
      const t = (el.innerText || el.value || label || '').trim().slice(0, 60);
      out.push(`button: '${t}'`);
    } else if (tag === 'select') {
      out.push(`select name='${el.name || ''}' label='${label}' options=${el.options.length}`);
    } else if (tag === 'textarea') {
      out.push(`textarea placeholder='${el.placeholder || ''}' name='${el.name || ''}'`);
    }
  }
  return {lines: out, total: els.length, capped: els.length > out.length};
}
"""


_LINKS_JS = r"""
() => {
  const all = document.querySelectorAll('a');
  const out = [];
  let textLinks = 0;
  for (const a of all) {
    const t = (a.innerText || '').trim();
    if (!t) continue;
    textLinks += 1;
    if (out.length < 200) out.push({t: t.slice(0, 80), h: a.href});
  }
  return {links: out, text_links: textLinks, capped: textLinks > out.length};
}
"""


_EXTRACT_LIMITS_NOTE = (
    "Note: I read the page's main content area where one exists; "
    "navigation, sidebars and footers, content inside embedded "
    "frames (iframes) or shadow DOM, and anything that only loads "
    "when scrolled to isn't in this text."
)


def _page_text_usage(page) -> str:
    """'main' when _page_text(page) would use the main-content
    region, else 'body'. Performs the same probe _page_text makes,
    so callers can word their results honestly (audit B3). Costs one
    extra short-timeout probe per read — acceptable for the honesty
    it buys."""
    try:
        text = page.locator(
            'main, article, [role="main"]'
        ).first.inner_text(timeout=1500) or ""
        if text.strip():
            return "main"
    except Exception:  # noqa: BLE001
        pass
    return "body"


def _page_text(page) -> str:
    # Prefer the page's main content region when one exists — it skips
    # nav bars, sidebars and footers. Short timeout: most pages have no
    # <main>, and there's no point waiting 8s to discover that.
    try:
        text = page.locator(
            'main, article, [role="main"]'
        ).first.inner_text(timeout=1500) or ""
        if text.strip():
            return text
    except Exception:  # noqa: BLE001 — no main region; use the body
        pass
    try:
        return page.inner_text("body", timeout=_ELEMENT_TIMEOUT_MS) or ""
    except Exception:  # noqa: BLE001 — fall back to JS extraction
        return page.evaluate(
            "() => document.body ? document.body.innerText : ''"
        ) or ""


def _fields_payload(page) -> tuple[list[str], int, bool]:
    """(lines, true_total, capped) for the page's form controls.

    Newer builds get the structured payload from _FIELDS_JS (with
    the page's true element count); a bare list (older shape) reads
    as its own length with capped unknown -> treated as complete
    only when shorter than the collector's 80 cap."""
    try:
        raw = page.evaluate(_FIELDS_JS)
    except Exception:  # noqa: BLE001
        return [], 0, False
    if isinstance(raw, dict):
        lines = [str(x) for x in (raw.get("lines") or [])]
        try:
            total = int(raw.get("total") or len(lines))
        except (TypeError, ValueError):
            total = len(lines)
        return lines, total, bool(raw.get("capped"))
    lines = [str(x) for x in (raw or [])]
    return lines, len(lines), len(lines) >= 80


def _field_lines(page, cap: int = 60) -> list[str]:
    lines, _total, _capped = _fields_payload(page)
    return lines[:cap]


def _impl_read_text() -> str:
    if (msg := _need_connection()) is not None:
        return msg
    page = _active_page()
    try:
        text = _page_text(page)
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't read the page ({_short(e)})."
    try:
        page_title = page.title() or ""
    except Exception:  # noqa: BLE001
        page_title = ""
    prefix = f"Title: {page_title}\n" if page_title else ""
    total = len(text)
    if not text.strip():
        return f"The page {_page_label(page)} has no readable text."
    # Honest accounting (audit B3): `total` is the length of the
    # text extracted from the page — NOT the page's full text. The
    # extraction may cover only the main content region, and never
    # crosses iframes / shadow DOM; say all of that instead of
    # presenting the extract's length as the page's total.
    region = (
        " (the page's main content area)" if _page_text_usage(page) == "main" else ""
    )
    # Injection defence (v5.3): the page's text is untrusted content —
    # it reaches the brain inside the envelope, and the FULL extract
    # is screened for the taint even though only the first 2,500
    # chars are shown. VYUK's own accounting trailer stays outside.
    injection.note_untrusted("web page", text)
    shown = text[:2500] if total > 2500 else text
    body = injection.wrap_untrusted("web page", prefix + shown)
    if total > 2500:
        return (
            body
            + f"\n… [truncated — showing the first 2,500 of {total:,} "
            f"characters I could read{region}. {_EXTRACT_LIMITS_NOTE}]"
        )
    return body + f"\n[{total:,} characters read{region}. {_EXTRACT_LIMITS_NOTE}]"


def _impl_read_find(query: str) -> str:
    if (msg := _need_connection()) is not None:
        return msg
    q = (query or "").strip()
    if not q:
        return "browser_read 'find' needs a query to look for."
    page = _active_page()
    try:
        text = _page_text(page)
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't read the page ({_short(e)})."
    hits = [m.start() for m in re.finditer(re.escape(q), text, re.IGNORECASE)]
    if not hits:
        # The search ran over the extracted text only — "not in what
        # I could read" is the honest claim (audit B3); the words may
        # still live in a region the extraction never reaches.
        return (
            f"'{q}' doesn't appear in the text I could read from "
            f"{_page_label(page)}. {_EXTRACT_LIMITS_NOTE}"
        )
    snippets = []
    for i in hits[:5]:
        start = max(0, i - 60)
        end = min(len(text), i + len(q) + 60)
        snippet = " ".join(text[start:end].split())
        snippets.append(f"…{snippet}…")
    return (
        f"'{q}' appears {len(hits)} time(s) on the page. First matches:\n"
        + injection.wrap_untrusted("web page", "\n".join(snippets))
    )


def _impl_read_links() -> str:
    if (msg := _need_connection()) is not None:
        return msg
    page = _active_page()
    try:
        raw = page.evaluate(_LINKS_JS) or []
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't list links ({_short(e)})."
    if isinstance(raw, dict):
        links = raw.get("links") or []
        try:
            text_total = int(raw.get("text_links") or len(links))
        except (TypeError, ValueError):
            text_total = len(links)
        capped = bool(raw.get("capped"))
    else:  # older payload shape: a bare list (already collector-capped)
        links = raw
        text_total = len(links)
        capped = len(links) >= 200
    if not links:
        return f"No links with visible text on {_page_label(page)}."
    lines = [f"{item.get('t', '')} → {item.get('h', '')}" for item in links[:60]]
    # Cap honesty (audit B4): a capped collection is a floor, not a
    # total — "at least", never "of {the capped number}".
    if capped:
        note = f" (showing 60 of at least {text_total} — the list was capped)"
    elif len(links) > 60:
        note = f" (showing 60 of {len(links)})"
    else:
        note = ""
    return f"Links on the page{note}:\n" + injection.wrap_untrusted(
        "web page", "\n".join(lines))


def _impl_read_fields() -> str:
    if (msg := _need_connection()) is not None:
        return msg
    page = _active_page()
    all_lines, total, js_capped = _fields_payload(page)
    lines = all_lines[:60]
    if not lines:
        return (
            f"No buttons, inputs or other form controls found on "
            f"{_page_label(page)}."
        )
    # Cap honesty (audit B4): when the collector or the display cut
    # the list, the count is a floor with the page's true total —
    # never the shown count presented as the whole story.
    if js_capped or len(all_lines) > len(lines):
        count_txt = f"{len(lines)} shown of at least {total} on the page"
    else:
        count_txt = f"{len(lines)} shown"
    return (
        f"Interactive elements on {_page_label(page)} "
        f"({count_txt}):\n" + injection.wrap_untrusted(
            "web page", "\n".join(lines))
    )


def _impl_read_elements(query: str = "", filter: str = "") -> str:
    if (msg := _need_connection()) is not None:
        return msg
    f = (filter or "").strip().lower()
    if f and f != "all" and f not in _KIND_FILTERS:
        return (f"Unknown elements filter: '{filter}'. "
                "Use all, links, buttons or inputs.")
    page = _active_page()
    q = (query or "").strip()
    try:
        snap = _take_snapshot(page, q, f)
    except Exception as e:  # noqa: BLE001
        return (
            "Browser action failed: couldn't list the page's "
            f"elements ({_short(e)})."
        )
    shown = snap["shown"]
    total_all = snap["total_all"]
    total_filtered = snap["total_filtered"]
    label = _page_label(page)
    if total_all == 0:
        return f"No visible interactive elements on {label}."
    if q and total_filtered == 0:
        return (
            f"No visible interactive elements on {label} match "
            f"'{q}' ({total_all} interactive on the page in total)."
        )
    if f and f != "all" and total_filtered == 0:
        return (
            f"No visible {f} on {label} "
            f"({total_all} interactive on the page in total)."
        )
    rows = [
        _element_row(e["ref"], e["info"], e["marker"]) for e in shown
    ]
    if q:
        if total_filtered > len(rows):
            count_txt = (
                f"{len(rows)} shown of {total_filtered} matching "
                f"'{q}' ({total_all} interactive on the page)"
            )
        else:
            count_txt = (
                f"{len(rows)} matching '{q}' "
                f"({total_all} interactive on the page)"
            )
    elif total_all > len(rows):
        count_txt = f"{len(rows)} shown of {total_all}"
    else:
        count_txt = f"{len(rows)}"
    if f and f != "all":
        count_txt = f"{count_txt} — {f} only"
    # Screen content, enveloped exactly like the sibling reads
    # (text/links/fields): VYUK's header and footer stay outside.
    return (
        f"Interactive elements on {label} ({count_txt}):\n"
        + injection.wrap_untrusted("web page", "\n".join(rows))
        + "\nTo click one exactly, use browser_click with by='ref' "
        "and its e-number as the target."
    )

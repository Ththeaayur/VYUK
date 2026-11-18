"""browser_elements concern extracted from core/browser.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/browser."""

from urllib.parse import urlparse
from .browser_session import (_page_label, _next_snapshot_seq, _store_snapshot)  # GAPS-18: narrowed to the 3 used names


_ELEMENTS_CAP = 80       # rows shown per snapshot listing


_ELEMENT_JS_HELPERS = r"""
  const ROLES = ['button', 'link', 'menuitem', 'tab', 'checkbox',
                 'radio', 'switch', 'option', 'listbox', 'combobox'];
  const classify = (el) => {
    const tag = el.tagName.toLowerCase();
    const roleAttr = (el.getAttribute('role') || '').toLowerCase();
    if (ROLES.indexOf(roleAttr) >= 0) return roleAttr;
    if (tag === 'a') return el.hasAttribute('href') ? 'link' : null;
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      const ty = (el.getAttribute('type') || 'text').toLowerCase();
      if (ty === 'hidden') return null;
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      if (ty === 'submit' || ty === 'button') return 'button';
      return 'input';
    }
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textarea';
    if (tag === 'summary') return 'summary';
    if (el.hasAttribute('onclick')) return 'element';
    if (el.isContentEditable) return 'editable';
    const ti = el.getAttribute('tabindex');
    if (ti !== null && parseInt(ti, 10) >= 0) return 'element';
    return null;
  };
  const nameOf = (el) => {
    const cands = [el.getAttribute('aria-label'), el.innerText, el.value,
                   el.getAttribute('placeholder'),
                   el.getAttribute('title'), el.getAttribute('alt')];
    for (const c of cands) {
      if (c === null || c === undefined) continue;
      const s = String(c).replace(/\s+/g, ' ').trim();
      if (s) return s.slice(0, 200);
    }
    return '';
  };
  // Deep walks (v5.7): document.querySelectorAll never descends
  // into shadow roots, so every "find elements" pass in this
  // module goes through these — they walk a root, then recurse
  // into every OPEN shadow root they meet (nested hosts too).
  // Closed shadow roots expose no shadowRoot at all: unreachable
  // by platform design, for every tool, not just VYUK
  const deepAll = (root, sel) => {
    const out = [];
    const walk = (r) => {
      r.querySelectorAll(sel).forEach(el => out.push(el));
      r.querySelectorAll('*').forEach(el => {
        if (el.shadowRoot) walk(el.shadowRoot);
      });
    };
    walk(root);
    return out;
  };
  const deepFind = (root, sel) => {
    const hit = root.querySelector(sel);
    if (hit) return hit;
    const all = root.querySelectorAll('*');
    for (const el of all) {
      if (el.shadowRoot) {
        const inner = deepFind(el.shadowRoot, sel);
        if (inner) return inner;
      }
    }
    return null;
  };
  const describe = (el, role, i) => {
    const tag = el.tagName.toLowerCase();
    let checked = null;
    if (typeof el.checked === 'boolean') checked = el.checked;
    else if (el.getAttribute('aria-checked') !== null) {
      checked = el.getAttribute('aria-checked') === 'true';
    }
    let selected = null;
    if (tag === 'option') selected = !!el.selected;
    else if (el.getAttribute('aria-selected') !== null) {
      selected = el.getAttribute('aria-selected') === 'true';
    }
    let expanded = null;
    if (el.getAttribute('aria-expanded') !== null) {
      expanded = el.getAttribute('aria-expanded') === 'true';
    }
    let value = '';
    if ((tag === 'input' || tag === 'textarea')
        && el.value !== undefined && el.value !== null) {
      value = String(el.value).slice(0, 60);
    }
    // Shadow residence (v5.7): an element whose root node is a
    // shadow root lives inside a web component; describe its host
    // (tag + short name/id) so rows can say where it lives. For
    // nested components the IMMEDIATE host is the useful one.
    let shadow = '';
    const rn = el.getRootNode ? el.getRootNode() : null;
    if (rn && rn.host) {
      const h = rn.host;
      let hn = h.getAttribute('aria-label') || '';
      hn = String(hn).replace(/\s+/g, ' ').trim();
      let hostDesc = '<' + h.tagName.toLowerCase();
      if (hn) hostDesc += ' "' + hn.slice(0, 30) + '"';
      else if (h.id) hostDesc += ' #' + String(h.id).slice(0, 30);
      hostDesc += '>';
      shadow = hostDesc.slice(0, 48);
    }
    return {
      i: i, tag: tag, role: role, name: nameOf(el), value: value,
      href: tag === 'a' ? (el.href || '') : '',
      disabled: !!el.disabled
        || el.getAttribute('aria-disabled') === 'true',
      checked: checked, selected: selected, expanded: expanded,
      shadow: shadow
    };
  };
"""


_COLLECT_ELEMENTS_JS = (
    "(tokPrefix) => {\n"
    + _ELEMENT_JS_HELPERS
    + r"""
  deepAll(document, '[data-nova-ref]')
    .forEach(el => el.removeAttribute('data-nova-ref'));
  deepAll(document, '[data-nova-tok]')
    .forEach(el => el.removeAttribute('data-nova-tok'));
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return false;
    const cs = window.getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') {
      return false;
    }
    return true;
  };
  const out = [];
  const els = deepAll(document,
    'a, button, input, select, textarea, summary, [role], [onclick],'
    + ' [tabindex], [contenteditable]');
  for (const el of els) {
    const role = classify(el);
    if (!role) continue;
    if (!isVisible(el)) continue;
    const i = out.length;
    el.setAttribute('data-nova-tok', tokPrefix + '-' + i);
    out.push(describe(el, role, i));
  }
  return out;
}
"""
)


_TAG_REFS_JS = (
    "(pairs) => {\n"
    + _ELEMENT_JS_HELPERS
    + r"""
  let done = 0;
  for (const pair of pairs) {
    const el = deepFind(document,
      '[data-nova-tok="' + pair[0] + '"]');
    if (el) {
      el.setAttribute('data-nova-ref', pair[1]);
      el.removeAttribute('data-nova-tok');
      done += 1;
    }
  }
  return done;
}
"""
)


_SINGLE_ELEMENT_JS = (
    "(el, ref) => {\n"
    "  el.setAttribute('data-nova-ref', ref);\n"
    + _ELEMENT_JS_HELPERS
    + r"""
  const role = classify(el) || 'element';
  return describe(el, role, 0);
}
"""
)


_PAGE_PROBE_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return false;
    const cs = window.getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden';
  };
  let overlays = 0;
  document.querySelectorAll(
    '[role="dialog"], [role="alertdialog"], [role="menu"], dialog[open]'
  ).forEach(el => { if (vis(el)) overlays += 1; });
  let interactive = 0;
  document.querySelectorAll(
    'a[href], button, input, textarea, select, [role="button"],'
    + ' [onclick], [tabindex]'
  ).forEach(el => { if (vis(el)) interactive += 1; });
  // Conversion pass, Phase 0 (browser-click field fix): the old
  // probe was blind to most SPA effects — a counter tick, a
  // checkbox toggle, a small reveal, a play/pause change all
  // read as "unchanged", and a dead click read exactly the
  // same. So the probe also fingerprints the visible text (a
  // single linear hash pass over innerText — no network, no
  // wait) and counts the toggleable states a click most often
  // flips. Every field is best-effort: _page_probe treats a
  // missing field as unknown, never as a change.
  const bodyText = document.body ? (document.body.innerText || '') : '';
  let textHash = 0;
  for (let i = 0; i < bodyText.length; i++) {
    textHash = ((textHash * 31) + bodyText.charCodeAt(i)) >>> 0;
  }
  let checked = 0;
  document.querySelectorAll(
    'input:checked, [aria-checked="true"]'
  ).forEach(() => { checked += 1; });
  let expanded = 0;
  document.querySelectorAll('[aria-expanded="true"]').forEach(
    () => { expanded += 1; });
  let pressed = 0;
  document.querySelectorAll('[aria-pressed="true"]').forEach(
    () => { pressed += 1; });
  let selected = 0;
  document.querySelectorAll('[aria-selected="true"]').forEach(
    () => { selected += 1; });
  let playing = 0;
  document.querySelectorAll('video, audio').forEach(
    (m) => { if (!m.paused) playing += 1; });
  return {overlays: overlays, interactive: interactive,
          textHash: textHash, textLen: bodyText.length,
          checked: checked, expanded: expanded,
          pressed: pressed, selected: selected,
          playing: playing};
}
"""


def _element_row(ref: str, info: dict, marker: str = "") -> str:
    """Render one snapshot row: 'e7 · button "Add to cart" [checked]'."""
    role = str(info.get("role") or "element")
    name = " ".join(str(info.get("name") or "").split())
    if len(name) > 80:
        name = name[:79] + "…"
    row = f"{ref} · {role}"
    if name:
        row += f' "{name}"'
    if role == "link":
        host = ""
        try:
            host = urlparse(str(info.get("href") or "")).netloc
        except Exception:  # noqa: BLE001
            host = ""
        if host:
            row += f" → {host}"
    states: list[str] = []
    if info.get("disabled"):
        states.append("disabled")
    if info.get("checked") is True:
        states.append("checked")
    if info.get("selected") is True:
        states.append("selected")
    if info.get("expanded") is True:
        states.append("expanded")
    elif info.get("expanded") is False:
        states.append("collapsed")
    value = " ".join(str(info.get("value") or "").split())
    if value and value != name:
        if len(value) > 30:
            value = value[:27] + "…"
        states.append(f'value: "{value}"')
    for s in states:
        row += f" [{s}]"
    shadow = " ".join(str(info.get("shadow") or "").split())
    if shadow:
        row += f" (in shadow: {shadow})"
    if marker:
        row += f" (in frame: {marker})"
    return row


def _frame_marker(frame) -> str:
    """How a non-main frame is named in snapshot rows: its frame
    name when it has one, else the host of its address."""
    name = ""
    try:
        name = (frame.name or "").strip()
    except Exception:  # noqa: BLE001
        name = ""
    if name:
        return name
    host = ""
    try:
        host = urlparse(frame.url or "").netloc
    except Exception:  # noqa: BLE001
        host = ""
    return host or "embedded frame"


_KIND_FILTERS = {
    "links": {"link"},
    "buttons": {"button"},
    "inputs": {"textbox", "checkbox", "radio", "combobox",
               "searchbox", "spinbutton", "slider"},
}


def _take_snapshot(page, query: str = "",
                   kind_filter: str = "") -> dict:
    """Collect the page's visible interactive elements (all frames),
    tag the shown ones with data-nova-ref, and store the snapshot.

    The query filter (case-insensitive, over name/role) and the
    kind filter (#27: links / buttons / inputs, by element role)
    are applied BEFORE the display cap, so a filtered view is
    complete whenever the matches fit under it. Returns {shown,
    total_all, total_filtered}; each shown entry carries
    ref/info/marker."""
    seq = _next_snapshot_seq()
    tok = f"nova{seq}"
    try:
        frames = list(page.frames)
    except Exception:  # noqa: BLE001 — a page that can't list frames
        frames = [page]
    main = None
    try:
        main = page.main_frame
    except Exception:  # noqa: BLE001
        main = frames[0] if frames else page
    raw: list[dict] = []
    for fi, frame in enumerate(frames):
        try:
            payload = frame.evaluate(_COLLECT_ELEMENTS_JS, f"{tok}-{fi}")
        except Exception:  # noqa: BLE001 — cross-origin/dead frame
            continue
        if not isinstance(payload, list):
            continue
        marker = "" if frame is main else _frame_marker(frame)
        for item in payload:
            if isinstance(item, dict):
                raw.append({
                    "frame": fi,
                    "marker": marker,
                    "info": item,
                    "tok": f"{tok}-{fi}-{item.get('i')}",
                })
    q = (query or "").strip().lower()
    if q:
        filtered = [
            e for e in raw
            if q in str(e["info"].get("name") or "").lower()
            or q in str(e["info"].get("role") or "").lower()
        ]
    else:
        filtered = raw
    roles = _KIND_FILTERS.get((kind_filter or "").strip().lower())
    if roles is not None:
        filtered = [
            e for e in filtered
            if str(e["info"].get("role") or "").lower() in roles
        ]
    shown = filtered[:_ELEMENTS_CAP]
    refs: dict[str, dict] = {}
    by_frame: dict[int, list] = {}
    for n, entry in enumerate(shown, start=1):
        ref = f"e{n}"
        entry["ref"] = ref
        info = entry["info"]
        refs[ref] = {
            "frame": entry["frame"],
            "name": str(info.get("name") or ""),
            "role": str(info.get("role") or ""),
            "shadow": str(info.get("shadow") or ""),
            "line": _element_row(ref, info, entry["marker"]),
        }
        by_frame.setdefault(entry["frame"], []).append([entry["tok"], ref])
    for fi, pairs in by_frame.items():
        try:
            frames[fi].evaluate(_TAG_REFS_JS, pairs)
        except Exception:  # noqa: BLE001 — tagging is best-effort; a
            pass  # ref that can't resolve is reported honestly later
    try:
        url = page.url
    except Exception:  # noqa: BLE001
        url = ""
    try:
        title = page.title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    _store_snapshot({"url": url, "title": title, "seq": seq,
                      "refs": refs})
    return {"shown": shown, "total_all": len(raw),
            "total_filtered": len(filtered)}


def _snapshot_from_candidates(page, locs, frame: int = 0) -> list[str]:
    """Snapshot a handful of already-resolved locators (an ambiguous
    click's visible matches) so the brain can re-click one by ref.
    Replaces the stored snapshot, like any listing. Returns rows.
    `frame` is the candidates' index in page.frames (0 unless the
    click's search scope fell back to an embedded frame, v5.10) —
    the stored refs must name it, or a later ref click would look
    for the tagged elements in the wrong document."""
    seq = _next_snapshot_seq()
    refs: dict[str, dict] = {}
    rows: list[str] = []
    for n, loc in enumerate(locs, start=1):
        ref = f"e{n}"
        info = None
        try:
            got = loc.evaluate(_SINGLE_ELEMENT_JS, ref)
            if isinstance(got, dict):
                info = got
        except Exception:  # noqa: BLE001 — fall back to identity text
            info = None
        if info is None:
            info = {
                "role": "element",
                "name": _element_identity(loc)[:80],
                "value": "", "href": "", "disabled": False,
                "checked": None, "selected": None, "expanded": None,
            }
        row = _element_row(ref, info, "")
        rows.append(row)
        refs[ref] = {
            "frame": int(frame),
            "name": str(info.get("name") or ""),
            "role": str(info.get("role") or ""),
            "shadow": str(info.get("shadow") or ""),
            "line": row,
        }
    try:
        url = page.url
    except Exception:  # noqa: BLE001
        url = ""
    _store_snapshot({"url": url, "title": "", "seq": seq,
                      "refs": refs})
    return rows


def _base_locator(page, by: str, target: str):
    """The (un-narrowed) locator for a by/target pair."""
    b = (by or "text").strip().lower()
    if b == "role_button":
        return page.get_by_role("button", name=target)
    if b == "label":
        return page.get_by_label(target)
    if b == "placeholder":
        return page.get_by_placeholder(target)
    if b == "selector":
        return page.locator(target)
    return page.get_by_text(target)


def _scoped_locator(page, by: str, target: str):
    """(locator, frame_index) — the search scope for a by/target pair.

    The main frame is the scope whenever it has at least one match:
    its behaviour is exactly what it was before frames were searched
    at all. Only on a main-frame MISS do we look inside the embedded
    frames, in page order — page-level Playwright getters never
    pierce iframes, while the v5.6 snapshot (and ref clicks) have
    always reached frame content, so the text/label/selector paths
    were blind to elements VYUK could list and click by ref (proven
    against a real page, v5.10). The first frame with a match
    becomes the scope; the caller's visibility narrowing, no-guess
    discipline and gate pipeline then run inside that scope,
    unchanged. frame_index is the scope's index in page.frames
    (0 = the page itself), so results can name the frame and
    refusal candidates can be snapshotted against the right frame.

    Guarded throughout: a main-frame search error hands the locator
    back so the caller's own error wording stays in charge, and a
    page whose frames cannot even be listed (older duck-typed
    doubles included) degrades to main-frame-only — never to an
    error of this helper's own."""
    base = _base_locator(page, by, target)
    try:
        if base.count() > 0:
            return base, 0
    except Exception:  # noqa: BLE001 — caller's search error rules
        return base, 0
    try:
        frames = list(page.frames or [])
    except Exception:  # noqa: BLE001 — treat as main-frame-only
        return base, 0
    for fi in range(1, len(frames)):
        try:
            candidate = _base_locator(frames[fi], by, target)
            if candidate.count() > 0:
                return candidate, fi
        except Exception:  # noqa: BLE001 — unsearchable frame: skip
            continue
    return base, 0


def _element_identity(loc) -> str:
    """Best-effort identity of the RESOLVED element: visible text plus
    the label-bearing attributes a dangerous label can hide behind
    (a submit input's value, an icon button's aria-label, a link's
    href…). The dangerous-click gate judges THIS, not the string the
    model asked for."""
    parts: list[str] = []
    try:
        text = (loc.inner_text(timeout=1500) or "").strip()
        if text:
            parts.append(text)
    except Exception:  # noqa: BLE001
        pass
    for attr in ("aria-label", "value", "title", "name", "id", "href", "type"):
        try:
            value = loc.get_attribute(attr)
        except Exception:  # noqa: BLE001
            value = None
        if value:
            parts.append(str(value))
    return " ".join(parts)[:300]


def _probe_int(raw: dict, key: str):
    """One probe field as an int, or None when it is missing or
    unparseable. Fields are parsed independently: one bad field
    must never wipe out the rest of the probe."""
    try:
        return int(raw.get(key))
    except (TypeError, ValueError):  # noqa: BLE001
        return None


def _page_probe(page) -> dict:
    """Before/after state for click reporting: url + title read
    directly, visible overlay + interactive counts plus the
    Phase 0 text/state fingerprint via _PAGE_PROBE_JS. A field
    is None when the probe cannot run or does not return it
    (older stub pages return only overlays/interactive) —
    comparisons then simply stay silent instead of inventing
    a change."""
    try:
        url = page.url
    except Exception:  # noqa: BLE001
        url = ""
    try:
        title = page.title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    fields = {"overlays": None, "interactive": None,
              "textHash": None, "textLen": None, "checked": None,
              "expanded": None, "pressed": None, "selected": None,
              "playing": None}
    try:
        raw = page.evaluate(_PAGE_PROBE_JS)
        if isinstance(raw, dict):
            for key in fields:
                fields[key] = _probe_int(raw, key)
    except Exception:  # noqa: BLE001
        pass
    return {"url": url, "title": title, **fields}


def _change_sentence(page, before: dict, after: dict) -> str:
    """One sentence on what a click actually changed. Any
    observed difference counts as an effect — overlay closes,
    text changes and toggle flips were invisible entirely
    before, and a small interactive delta counts when it is
    the sole change (see the inline note below). A field
    that is None on either side is unknown, never a change."""
    parts: list[str] = []
    if after["url"] != before["url"]:
        parts.append(f"Now on {_page_label(page)}")
    elif after["title"] != before["title"]:
        parts.append(f"the page title changed to '{after['title']}'")
    b_ov, a_ov = before["overlays"], after["overlays"]
    if b_ov is not None and a_ov is not None and a_ov > b_ov:
        parts.append("a dialog or menu opened")
    elif b_ov is not None and a_ov is not None and a_ov < b_ov:
        parts.append("a dialog or menu closed")
    # Interactive count, last: a LARGE delta (>=5, the old
    # threshold) is always worth naming, but a SMALL delta is
    # named only when it is the SOLE observed change — when
    # another signal already reports the effect (a dialog, a
    # text change, a navigation), a count sentence would only
    # pad the receipt, and it would break the v5.6 hover
    # receipt's pinned "A dialog or menu opened." sentence.
    # A small sole delta still counts as an effect (an
    # icon-only reveal changes no text and flips no toggle),
    # so it is reported, never swallowed.
    b_in, a_in = before["interactive"], after["interactive"]
    small_interactive = (
        b_in is not None and a_in is not None and a_in != b_in
        and abs(a_in - b_in) < 5)
    if b_in is not None and a_in is not None and abs(a_in - b_in) >= 5:
        parts.append(
            f"the page now has {a_in} clickable elements (was {b_in})")
    b_tx, a_tx = before.get("textHash"), after.get("textHash")
    b_tl, a_tl = before.get("textLen"), after.get("textLen")
    if ((b_tx is not None and a_tx is not None and a_tx != b_tx)
            or (b_tl is not None and a_tl is not None
                and a_tl != b_tl)):
        parts.append("the page text changed")
    for key, phrase in (
            ("checked", "a checkbox or toggle changed state"),
            ("expanded", "an expandable section opened or closed"),
            ("pressed", "a toggle button changed state"),
            ("selected", "a selected item changed"),
            ("playing", "media playback changed")):
        b_v, a_v = before.get(key), after.get(key)
        if b_v is not None and a_v is not None and a_v != b_v:
            parts.append(phrase)
    if small_interactive and not parts:
        parts.append(
            f"the page now has {a_in} clickable elements (was {b_in})")
    if not parts:
        return "The page looks unchanged."
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."

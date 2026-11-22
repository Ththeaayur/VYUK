"""browser_click concern extracted from core/browser.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/browser."""

from .browser_session import (_ELEMENT_TIMEOUT_MS, _active_page, _need_connection, _page_label, _settle, _short, _read_snapshot)  # GAPS-18: narrowed to the 7 used names
from .browser_gates import (_DANGEROUS_CLICK_RE, _MATCH_SCAN_CAP, _DESTRUCTIVE_SUBMIT_RE, _FORM_INTENT_JS, _FOCUSED_FORM_INTENT_JS, _is_submit_key, _dangerous_click_verdict, _dangerous_submit_verdict, _uninspectable_submit_verdict, _raw_point_verdict, _classify_submit_intent, _submit_gate, _loc_visible, _narrow_visible, _LOGIN_WALL_JS, _login_wall_result, _element_kind, _file_input_verdict)
from .browser_read import (_FIELDS_JS, _LINKS_JS, _EXTRACT_LIMITS_NOTE, _page_text_usage, _page_text, _fields_payload, _field_lines, _impl_read_text, _impl_read_find, _impl_read_links, _impl_read_fields, _impl_read_elements)
from .browser_elements import (_ELEMENTS_CAP, _ELEMENT_JS_HELPERS, _COLLECT_ELEMENTS_JS, _TAG_REFS_JS, _SINGLE_ELEMENT_JS, _element_row, _frame_marker, _KIND_FILTERS, _take_snapshot, _snapshot_from_candidates, _base_locator, _scoped_locator, _element_identity, _probe_int, _PAGE_PROBE_JS, _page_probe, _change_sentence)


_DESCRIBE_ELEMENT_JS = (
    "(el) => {\n"
    + _ELEMENT_JS_HELPERS
    + "  const role = classify(el) || 'element';\n"
    "  return describe(el, role, 0);\n"
    "}\n"
)


_FIND_REF_JS = (
    "(ref) => {\n"
    + _ELEMENT_JS_HELPERS
    + "  return deepFind(document,"
    " '[data-nova-ref=\"' + ref + '\"]');\n"
    "}\n"
)


_POINT_AT_JS = r"""
([x, y]) => {
  let el = document.elementFromPoint(x, y);
  if (!el) return null;
  while (el.shadowRoot) {
    const inner = el.shadowRoot.elementFromPoint(x, y);
    if (!inner || inner === el) break;
    el = inner;
  }
  return el;
}
"""


_CLIMB_INTERACTIVE_JS = r"""
(el) => {
  const SEL = 'a,button,input,select,textarea,summary,'
    + '[role="button"],[role="link"],[onclick],[tabindex]';
  let node = el;
  while (node) {
    if (node.closest) {
      const hit = node.closest(SEL);
      if (hit) return hit;
    }
    const root = node.getRootNode ? node.getRootNode() : null;
    if (root && root.host) {
      node = root.host;
      continue;
    }
    break;
  }
  return null;
}
"""


_CLICK_VERBS = {
    "click": "Clicked",
    "double": "Double-clicked",
    "right": "Right-clicked",
    "hover": "Hovered over",
}


_CLICK_NOUNS = {
    "click": "click",
    "double": "double-click",
    "right": "right-click",
    "hover": "hover over",
}


def _act_on_element(page, loc, *, display: str, ask: str, kind: str,
                    confirm: bool, note: str = "") -> str:
    """Act on ONE resolved element and report honestly.

    display: how the result names the element ("'Save'" for a text
    click, the snapshot row for a ref click). ask: the string the
    gate judges alongside the resolved identity (the requested
    target, or the snapshot's name for a ref click)."""
    identity = _element_identity(loc)
    if kind != "hover":
        # TRUST GATE (v4.4): judge the element that will actually be
        # clicked — its real text/attributes — not the requested
        # string. Selector targets get exactly the same scrutiny;
        # the requested string is still checked too, for when the
        # identity cannot be read. Hover is exempt: hovering cannot
        # pay, submit or delete anything — at worst it opens a menu,
        # and the click that follows is gated then.
        if (_DANGEROUS_CLICK_RE.search(identity)
                or _DANGEROUS_CLICK_RE.search(ask)):
            verdict = _dangerous_click_verdict(ask, identity or ask,
                                               confirm)
            if verdict is not None:
                return verdict
        # L-29 / batch 2: the regex reads only the element's words,
        # so it cannot see a file input (whose words are empty) or
        # a submit control whose own words are benign ("Next") in a
        # dangerous form. Probe the element's kind (one evaluate):
        # a file input is the attach step of an upload and asks
        # first; a submit control is judged by the form it submits
        # (dangerous form -> ask, uninspectable form -> ask, benign
        # form -> proceed). The probe runs only when the regex did
        # not already ask, so a dangerous click costs no probe.
        el_kind = _element_kind(loc)
        if el_kind.get("file"):
            verdict = _file_input_verdict(ask, identity or ask, confirm)
            if verdict is not None:
                return verdict
        elif el_kind.get("submit"):
            intent, _detail, _signals = _classify_submit_intent(
                page, loc=loc)
            if intent == "dangerous":
                verdict = _dangerous_submit_verdict(
                    "browser_click", ask, identity or ask, confirm)
                if verdict is not None:
                    return verdict
            elif intent == "uninspectable":
                verdict = _uninspectable_submit_verdict(
                    "browser_click", ask, confirm)
                if verdict is not None:
                    return verdict
    before = _page_probe(page)
    try:
        loc.scroll_into_view_if_needed(timeout=_ELEMENT_TIMEOUT_MS)
        if kind == "double":
            loc.dblclick(timeout=_ELEMENT_TIMEOUT_MS)
        elif kind == "right":
            loc.click(button="right", timeout=_ELEMENT_TIMEOUT_MS)
        elif kind == "hover":
            loc.hover(timeout=_ELEMENT_TIMEOUT_MS)
        else:
            loc.click(timeout=_ELEMENT_TIMEOUT_MS)
    except Exception as e:  # noqa: BLE001
        return (
            f"Browser action failed: couldn't {_CLICK_NOUNS[kind]} "
            f"{display} ({_short(e)})."
        )
    _settle(page)
    after = _page_probe(page)
    echo = ""
    ident = identity.strip()
    if (ident and ident.lower() != ask.strip().lower()
            and ask.strip().lower() not in ident.lower()):
        echo = f" (the element reads: {ident[:80]})"
    change = _change_sentence(page, before, after)
    if kind in ("click", "double") and change == "The page looks unchanged.":
        # Conversion pass, Phase 0 (browser-click field fix):
        # Playwright's click() succeeding only proves the event
        # was dispatched to the element — a SPA handler can
        # swallow it, or the element can be dead, and the old
        # receipt still read "Clicked 'X'." as a success. A
        # click with no observed effect must never read that
        # way: say the no-effect verdict out loud, in the same
        # breath as the action, and point at the ref path that
        # names the exact control. The "Clicked …" lead and the
        # trailing "The page looks unchanged." are kept
        # deliberately — existing suites pin both, and the
        # verdict between them is what turns a success-sounding
        # receipt into an honest one. Hover and right-click are
        # exempt: a hover often has no visible effect by design,
        # and a native context menu is invisible to this DOM
        # probe, so "unchanged" is not a failure for them. The
        # check is the existing settle only (_settle, at most
        # 4 s and immediate on an already-loaded page) — no new
        # wait is added on the action path, and an effect the
        # probe cannot see (a download, a clipboard copy, a
        # background request) is reported as "may not", never
        # as a certain failure.
        return (
            f"{_CLICK_VERBS[kind]} {display}{echo}{note}. "
            "No effect followed — no navigation, title change, "
            "dialog, or content change was observed after the "
            "click, so it may not have done anything; listing "
            "the elements (browser_read action 'elements') and "
            "clicking by ref names the exact control. "
            f"{change}"
        )
    return f"{_CLICK_VERBS[kind]} {display}{echo}{note}. {change}"


def _impl_click(target: str, by: str, confirm: bool,
                click: str = "click") -> str:
    kind = (click or "click").strip().lower()
    if kind not in _CLICK_VERBS:
        kind = "click"
    if (msg := _need_connection()) is not None:
        return msg
    t = (target or "").strip()
    if not t:
        return "browser_click needs a target (the text, label or selector to click)."
    page = _active_page()
    if (wall := _login_wall_result(page)) is not None:
        return wall  # v5.52 login-wall terminal state
    b = (by or "").strip().lower()
    if b == "ref":
        return _impl_click_ref(page, t, kind, confirm)
    if b == "point":
        return _impl_click_point(page, t, kind, confirm)
    base, scope_frame = _scoped_locator(page, by, t)
    frame_note = " (inside an embedded frame)" if scope_frame else ""
    try:
        count = base.count()
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't search for '{t}' ({_short(e)})."
    if count == 0:
        candidates = _field_lines(page, cap=8)
        extra = (
            " Controls I can see here: " + "; ".join(candidates)
            if candidates else ""
        )
        return (
            f"I couldn't find anything matching '{t}' (by={by}) on "
            f"{_page_label(page)}.{extra}"
        )
    # NO-GUESS DISCIPLINE (v5.6): narrow the matches to the VISIBLE
    # ones before acting. First-match clicking is gone: several
    # visible matches is a question, not a coin flip; zero visible
    # matches is a closed menu, not a misclick.
    visible, scanned, stopped_early = _narrow_visible(base, count)
    if not visible:
        if stopped_early:
            return (
                f"Found {count} elements matching '{t}' (by={by}) on "
                f"{_page_label(page)}{frame_note}, but none of the "
                f"first {scanned} "
                "I checked are visible — they may be inside a closed "
                "menu; hover or open it first, or use browser_read "
                "action 'elements' to see what's clickable right now."
            )
        return (
            f"Found {count} elements matching '{t}' (by={by}) on "
            f"{_page_label(page)}{frame_note}, but none are visible "
            "— they may be "
            "inside a closed menu; hover or open it first, or use "
            "browser_read action 'elements' to see what's clickable "
            "right now."
        )
    if len(visible) > 1:
        rows = _snapshot_from_candidates(page, visible,
                                         frame=scope_frame)
        if stopped_early:
            head = (
                f"'{t}' matches {count} elements; I checked the "
                f"first {scanned} and {len(visible)} of those are "
                "visible — I won't guess which one you mean:"
            )
        else:
            head = (
                f"'{t}' matches {count} elements and "
                f"{len(visible)} are visible — I won't guess which "
                "one you mean:"
            )
        if frame_note:
            head = head[:-1] + frame_note + ":"
        hidden = scanned - len(visible)
        hidden_note = (
            f"\n({hidden} matching element(s) I checked are hidden "
            "and were left out.)"
            if hidden > 0 else ""
        )
        return (
            head + "\n" + "\n".join(rows) + hidden_note
            + "\nTell me which one — browser_click with by='ref' and "
            "its e-number as the target — and I'll click exactly that."
        )
    note = f" (only visible match of {count})" if count > 1 else ""
    note += frame_note
    return _act_on_element(page, visible[0], display=f"'{t}'", ask=t,
                            kind=kind, confirm=confirm, note=note)


def _resolve_ref_handle(frame, ref: str):
    """ElementHandle for a shadow-resident snapshot ref, found by
    the in-page deep walk (_FIND_REF_JS); None when the tagged
    element is gone (or the frame can't be probed anymore)."""
    try:
        handle = frame.evaluate_handle(_FIND_REF_JS, ref)
    except Exception:  # noqa: BLE001
        return None
    if handle is None:
        return None
    try:
        return handle.as_element()
    except Exception:  # noqa: BLE001
        return None


def _impl_click_ref(page, ref: str, kind: str, confirm: bool) -> str:
    """Click a snapshot element by its e-number: resolve the
    data-nova-ref tag in the frame it was listed from, re-check that
    it is still there and still visible, then the same gate + act +
    report pipeline as any other click."""
    snap = _read_snapshot()
    if not isinstance(snap, dict) or ref not in (snap.get("refs") or {}):
        return (
            f"I don't have an element '{ref}' on file. Refs come "
            "from browser_read action 'elements' (or a list I gave "
            "you after an ambiguous click) — list the elements "
            "first, then click one by its e-number."
        )
    info = snap["refs"][ref]
    try:
        current_url = page.url
    except Exception:  # noqa: BLE001
        current_url = None
    if current_url is not None and snap.get("url") != current_url:
        return (
            "The page changed since that snapshot (the address is "
            "different now), so those e-numbers may point at the "
            "wrong elements. Ask me to list the elements again, "
            "then click by a fresh e-number."
        )
    try:
        frames = list(page.frames)
    except Exception:  # noqa: BLE001
        frames = [page]
    fi = info.get("frame", 0)
    if fi >= len(frames):
        return (
            f"{ref} lived in a frame that isn't on the page "
            "anymore. List the elements again and click by a fresh "
            "e-number."
        )
    if info.get("shadow"):
        # Shadow-resident (v5.7): resolve through the deep walk —
        # no locator can address an attribute inside a shadow tree.
        # From the handle on, the pipeline is the same as any
        # other click: visibility re-check, identity, gate, act,
        # report.
        el = _resolve_ref_handle(frames[fi], ref)
        if el is None:
            return (
                f"{ref} isn't on the page anymore — it may have been "
                "removed, or the page re-rendered since the listing. "
                "List the elements again and I'll use the fresh numbers."
            )
    else:
        loc = frames[fi].locator(f'[data-nova-ref="{ref}"]')
        try:
            n = loc.count()
        except Exception:  # noqa: BLE001
            n = 0
        if n == 0:
            return (
                f"{ref} isn't on the page anymore — it may have been "
                "removed, or the page re-rendered since the listing. "
                "List the elements again and I'll use the fresh numbers."
            )
        el = loc.first
    if not _loc_visible(el):
        return (
            f"{info.get('line') or ref} is hidden now — it was "
            "visible when listed, but something has closed or "
            "covered it since. I didn't click it."
        )
    return _act_on_element(
        page, el, display=info.get("line") or ref,
        ask=info.get("name") or ref, kind=kind, confirm=confirm)


def _viewport_size(page):
    """(width, height) of the page viewport in CSS pixels, or None
    when neither Playwright nor the page can say — bounds are then
    validated by the probe itself rather than a guessed size."""
    try:
        vs = page.viewport_size()
    except Exception:  # noqa: BLE001
        vs = None
    if isinstance(vs, dict) and vs.get("width") and vs.get("height"):
        return int(vs["width"]), int(vs["height"])
    try:
        raw = page.evaluate(
            "() => ({width: window.innerWidth,"
            " height: window.innerHeight})"
        )
        if isinstance(raw, dict) and raw.get("width") and raw.get("height"):
            return int(raw["width"]), int(raw["height"])
    except Exception:  # noqa: BLE001
        pass
    return None


def _probe_point(frame, x, y):
    """ElementHandle for whatever is painted at (x, y) in frame
    (shadow-descended — see _POINT_AT_JS); None when nothing is
    there or the frame can't be probed."""
    try:
        handle = frame.evaluate_handle(_POINT_AT_JS, [x, y])
    except Exception:  # noqa: BLE001
        return None
    if handle is None:
        return None
    try:
        return handle.as_element()
    except Exception:  # noqa: BLE001
        return None


def _handle_tag(handle) -> str:
    try:
        return str(
            handle.evaluate(
                "el => (el.tagName || '').toLowerCase()") or ""
        )
    except Exception:  # noqa: BLE001
        return ""


def _climb_handle(raw):
    """The interactive ancestor (or the element itself) for a
    point-probed element, as an ElementHandle; None when no
    interactive element lives in its ancestry."""
    try:
        handle = raw.evaluate_handle(_CLIMB_INTERACTIVE_JS)
    except Exception:  # noqa: BLE001
        return None
    if handle is None:
        return None
    try:
        return handle.as_element()
    except Exception:  # noqa: BLE001
        return None


_RAW_POINT_VERBS = {
    "click": "Clicked",
    "double": "Double-clicked",
    "right": "Right-clicked",
    "hover": "Hovered",
}


_RAW_POINT_NOUNS = {
    "click": "click",
    "double": "double-click",
    "right": "right-click",
    "hover": "move",
}


def _raw_point_click(page, x: int, y: int, kind: str,
                     reason: str, confirm: bool = False) -> str:
    """The raw-coordinate fallback, honestly labelled. Audit fix
    Batch 1 (WEB-01): the trust gate DOES judge this branch now
    — as unclassifiable. There is no element whose identity
    could be read, so for click/double/right the verdict runs
    confirm-first (never silent dispatch); hover stays exempt,
    mirroring _act_on_element, because a hover cannot pay,
    submit or delete. The result still says 'raw coordinate'
    out loud, and never names a semantic target it did not
    resolve."""
    if kind != "hover":
        verdict = _raw_point_verdict(
            f"raw coordinate ({x}, {y}) — {reason}", confirm)
        if verdict is not None:
            return verdict
    before = _page_probe(page)
    try:
        mouse = page.mouse
        if kind == "double":
            mouse.dblclick(x, y)
        elif kind == "right":
            mouse.click(x, y, button="right")
        elif kind == "hover":
            mouse.move(x, y)
        else:
            mouse.click(x, y)
    except Exception as e:  # noqa: BLE001
        return (
            f"Browser action failed: couldn't "
            f"{_RAW_POINT_NOUNS[kind]} at ({x}, {y}) ({_short(e)})."
        )
    _settle(page)
    after = _page_probe(page)
    change = _change_sentence(page, before, after)
    return (
        f"{_RAW_POINT_VERBS[kind]} at ({x}, {y}) — {reason}, so "
        f"this was a raw coordinate {_RAW_POINT_NOUNS[kind]}. "
        f"{change}"
    )


def _impl_click_point(page, target: str, kind: str,
                      confirm: bool) -> str:
    parts = [p.strip() for p in (target or "").split(",")]
    xy = None
    if len(parts) == 2:
        try:
            xy = (int(parts[0]), int(parts[1]))
        except ValueError:
            xy = None
    if xy is None:
        return (
            "browser_click with by='point' needs the target as "
            "'x,y' — two whole numbers in viewport pixels, e.g. "
            "'640,480'. Nothing was clicked."
        )
    x, y = xy
    vp = _viewport_size(page)
    if vp is not None:
        w, h = vp
        if not (0 <= x < w and 0 <= y < h):
            return (
                f"({x}, {y}) is outside the page's visible area "
                f"({w}×{h} viewport pixels, counted from the "
                "top-left corner) — nothing was clicked."
            )
    try:
        frame = page.main_frame
    except Exception:  # noqa: BLE001
        frame = page
    raw = _probe_point(frame, x, y)
    if raw is not None and _handle_tag(raw) in ("iframe", "frame"):
        # One level in: translate the point by the frame's box and
        # probe inside it. Deeper nesting stops here — the click
        # below still lands at the asked point (mouse coordinates
        # are viewport coordinates regardless of frame nesting),
        # but it is reported as the raw click it is.
        box = None
        child = None
        try:
            box = raw.bounding_box()
        except Exception:  # noqa: BLE001
            box = None
        try:
            child = raw.content_frame()
        except Exception:  # noqa: BLE001
            child = None
        if box is not None and child is not None:
            inner = _probe_point(child, x - box["x"], y - box["y"])
            if (inner is not None
                    and _handle_tag(inner) in ("iframe", "frame")):
                return _raw_point_click(
                    page, x, y, kind,
                    "that point sits inside nested embedded frames",
                    confirm=confirm)
            raw = inner
        else:
            return _raw_point_click(
                page, x, y, kind,
                "that point is an embedded frame I couldn't look "
                "inside", confirm=confirm)
    if raw is not None:
        resolved = _climb_handle(raw)
        if resolved is not None:
            info = {}
            try:
                got = resolved.evaluate(_DESCRIBE_ELEMENT_JS)
                if isinstance(got, dict):
                    info = got
            except Exception:  # noqa: BLE001
                info = {}
            role = str(info.get("role") or "element")
            name = " ".join(str(info.get("name") or "").split())
            if len(name) > 80:
                name = name[:79] + "…"
            desc = f'{role} "{name}"' if name else role
            shadow = str(info.get("shadow") or "")
            if shadow:
                desc += f" (in shadow: {shadow})"
            display = (
                f"at ({x}, {y}) — that point is the {desc}"
            )
            return _act_on_element(
                page, resolved, display=display,
                ask=name or role, kind=kind, confirm=confirm)
    return _raw_point_click(
        page, x, y, kind, "no element lives there (drawn content)",
        confirm=confirm)

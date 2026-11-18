"""browser_interact concern extracted from core/browser.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/browser."""

from urllib.parse import urlparse

from ..tool_outcome import Outcome
from .browser_session import (_ELEMENT_TIMEOUT_MS, _active_page, _need_connection, _page_label, _settle, _short)  # GAPS-18: narrowed to the 6 used names
from .browser_gates import (_DANGEROUS_CLICK_RE, _MATCH_SCAN_CAP, _DESTRUCTIVE_SUBMIT_RE, _FORM_INTENT_JS, _FOCUSED_FORM_INTENT_JS, _is_submit_key, _dangerous_click_verdict, _dangerous_submit_verdict, _uninspectable_submit_verdict, _raw_point_verdict, _classify_submit_intent, _submit_gate, _loc_visible, _narrow_visible, _LOGIN_WALL_JS, _login_wall_result)
from .browser_nav import (_looks_like_timeout, _goto_target, _scam_hash_warning, _impl_goto, _impl_history, _impl_new_tab, _impl_close_tab, _impl_switch_tab, _impl_list_tabs, _impl_scroll, _impl_wait, _impl_screenshot)
from .browser_elements import (_ELEMENTS_CAP, _ELEMENT_JS_HELPERS, _COLLECT_ELEMENTS_JS, _TAG_REFS_JS, _SINGLE_ELEMENT_JS, _element_row, _frame_marker, _KIND_FILTERS, _take_snapshot, _snapshot_from_candidates, _base_locator, _scoped_locator, _element_identity, _probe_int, _PAGE_PROBE_JS, _page_probe, _change_sentence)
from .browser_click import (_CLICK_VERBS, _CLICK_NOUNS, _act_on_element, _impl_click, _DESCRIBE_ELEMENT_JS, _FIND_REF_JS, _resolve_ref_handle, _impl_click_ref, _viewport_size, _POINT_AT_JS, _probe_point, _handle_tag, _CLIMB_INTERACTIVE_JS, _climb_handle, _RAW_POINT_VERBS, _RAW_POINT_NOUNS, _raw_point_click, _impl_click_point)


_YT_SEARCH_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com")


_YT_CONSENT_TEXTS = ("Reject all", "Accept all")


def _youtube_results_url(query: str) -> str:
    """The direct results URL for `query` (quote_plus encoding,
    the YTM rung's encoding for its search URL)."""
    from urllib.parse import quote_plus

    return ("https://www.youtube.com/results?search_query="
            + quote_plus((query or "").strip()))


def _is_youtube_page(page) -> bool:
    """True when *page* is currently on plain YouTube (not
    YouTube Music — that host already has its own rung)."""
    try:
        host = (urlparse(page.url or "").hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return host in _YT_SEARCH_HOSTS


def _dismiss_youtube_consent() -> None:
    """Best-effort consent-dialog dismissal on the results page,
    the YTM rung's semantics with _impl_click in place of the
    public browser_click (this runs on the worker thread, so it
    must not re-enter _submit): try Reject, then Accept; stop
    on a click that lands; any other outcome is ignored —
    absence is the common case, and a no-guess multi-match
    answer must never block the rung."""
    for text in _YT_CONSENT_TEXTS:
        try:
            clicked = _impl_click(text, by="text", confirm=False)
        except Exception:  # noqa: BLE001 — dismissal is best-effort
            return
        if isinstance(clicked, str) and clicked.startswith("Clicked"):
            return


def _youtube_results_search(query: str) -> str:
    """The direct-URL rung itself (worker thread only). Gotos
    the results URL for `query`, dismisses consent best-effort,
    and returns a plain-words success — or, when the navigation
    itself fails, a plain-words failure. Only an "Opened"
    navigation counts as success (stricter than the YTM rung,
    which can verify itself by clicking a result afterwards;
    this rung has no second verification, so a still-loading
    page must not read as a searched page). Never clicks or
    fills a search box, never fabricates a result, and never
    falls back to a click after a failed navigation."""
    q = (query or "").strip()
    if not q:
        return "No search text to search YouTube for."
    opened = _impl_goto(_youtube_results_url(q))
    if not isinstance(opened, str) or not opened.startswith("Opened"):
        return (
            f"Couldn't open the YouTube results page for '{q}' "
            f"directly ({opened})."
        )
    _dismiss_youtube_consent()
    page = _active_page()
    if (wall := _login_wall_result(page)) is not None:
        return wall
    return (
        f"Searched YouTube directly for '{q}' — opened the "
        "results page (the search box wasn't available, so I "
        f"didn't click or type into it). Now on {_page_label(page)}."
    )


def _impl_type(target: str, text: str, by: str, submit: bool,
               confirm: bool = False) -> str | Outcome:
    if (msg := _need_connection()) is not None:
        return msg
    t = (target or "").strip()
    if not t:
        return "browser_type needs a target (the field's label, placeholder or selector)."
    page = _active_page()
    if (wall := _login_wall_result(page)) is not None:
        return wall  # v5.52 login-wall terminal state
    base, scope_frame = _scoped_locator(page, by, t)
    frame_note = " (inside an embedded frame)" if scope_frame else ""
    try:
        count = base.count()
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't search for '{t}' ({_short(e)})."
    if count == 0:
        miss = (
            f"I couldn't find a field matching '{t}' (by={by}) on "
            f"{_page_label(page)}. Use browser_read action 'fields' to "
            "see what's actually on the page."
        )
        # Plain-YouTube direct-results rung: the type carries the
        # query the click step never had, so a missing search box
        # on a YouTube page falls back to the results URL instead
        # of dying on the miss. Any other page, field or empty
        # text keeps the miss exactly as before.
        if _is_youtube_page(page) and "search" in t.lower() \
                and (text or "").strip():
            rung = _youtube_results_search(text)
            if rung.startswith("Searched YouTube directly"):
                return rung
            return miss + " Direct-results attempt: " + rung
        return miss
    loc = base.first
    # HARD RULE: never type into a password field.
    # Audit fix Batch 1 (WEB-03): an inspection failure is NOT
    # "not a password field" — if the first field's type cannot
    # be determined, filling it would be a guess, so refuse.
    try:
        input_type = (loc.get_attribute("type") or "").strip().lower()
    except Exception as e:  # noqa: BLE001
        return (
            f"I couldn't determine whether '{t}' is a password "
            f"field ({_short(e)}) — I won't fill it on a guess. "
            f"Give me a more specific label."
        )
    if input_type == "password":
        return (
            "That's a password field — passwords are yours to type, "
            "I won't. Everything else on the form I can fill for you."
        )
    if count > 1:
        # Ambiguity handling (audit B7): the password rule judges
        # EVERY match, not just .first — if any match is a password
        # field, the label is ambiguous with a password field and
        # filling any of them on a guess is unsafe. And whatever is
        # filled gets disclosed (count + which field), the same
        # convention browser_click and uia_click_control use.
        # Audit fix Batch 1 (WEB-03): the old code inspected
        # only .first and nth(1) and degraded a get_attribute
        # exception to "" (fill anyway), so a password field at
        # index >= 2 — or an unreadable second match — was
        # filled. Now every match up to the click path's
        # _MATCH_SCAN_CAP is judged, an inspection failure on
        # ANY match refuses, and a count past the bound refuses
        # outright: the unchecked tail could hide a password
        # field, so there is no safe first field to fill.
        if count > _MATCH_SCAN_CAP:
            return (
                f"'{t}' matches {count} fields — more than the "
                f"{_MATCH_SCAN_CAP} I can check safely — I won't "
                f"fill any of them on a guess. Give me a more "
                f"specific label."
            )
        for _i in range(count):
            try:
                _ith_type = (base.nth(_i).get_attribute("type")
                             or "").strip().lower()
            except Exception as e:  # noqa: BLE001
                return (
                    f"'{t}' matches {count} fields and I couldn't "
                    f"determine the type of field {_i + 1} "
                    f"({_short(e)}) — I won't fill any of them on "
                    f"a guess. Give me a more specific label."
                )
            if _ith_type == "password":
                return (
                    f"'{t}' matches {count} fields and at least one of "
                    "them is a password field — I won't fill any of them "
                    "on a guess. Passwords are yours to type; for the "
                    "other field, give me a more specific label."
                )
    try:
        loc.scroll_into_view_if_needed(timeout=_ELEMENT_TIMEOUT_MS)
        loc.fill(text, timeout=_ELEMENT_TIMEOUT_MS)
    except Exception as e:  # noqa: BLE001
        miss = (
            f"Browser action failed: couldn't type into '{t}' "
            f"({_short(e)})."
        )
        # Same direct-results rung as the count==0 branch: a
        # field found but unfillable (e.g. under a consent
        # overlay) on a YouTube search box is the same failure
        # shape — the results URL does not need the box.
        if _is_youtube_page(page) and "search" in t.lower() \
                and (text or "").strip():
            rung = _youtube_results_search(text)
            if rung.startswith("Searched YouTube directly"):
                return rung
            return miss + " Direct-results attempt: " + rung
        return miss
    # R1b: read the field value back — loc.fill() succeeding does
    # not prove the text landed. A mismatch is reported at once
    # (nothing downstream may claim the fill); an unreadable
    # field marks every success line below honestly unverified.
    from .. import tool_outcome as _outcome  # R1b: seam wiring

    try:
        _back = loc.input_value(timeout=_ELEMENT_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001 — unreadable field
        _back = None
        _back_err = exc
    else:
        _back_err = None
    if _back is not None and _back != text:
        return (f"Filled '{t}' but it reads back as '{_back[:80]}', "
                "not the typed text — the fill did not land as typed.")
    _fill_evidence = ("field reads back the typed text"
                      if _back is not None else
                      "the field value could not be read back: %s"
                      % _back_err)

    def _mark(msg):
        if _back is not None:
            return _outcome.verified(msg, _fill_evidence)
        return _outcome.unverified(msg, _fill_evidence)

    which = _element_identity(loc)
    many = ""
    if count > 1:
        many = f" ({count} fields matched — I filled the first"
        if which:
            many += f": {which[:80]}"
        many += ")"
    if submit:
        # Audit fix Batch 1 (WEB-02): Enter is a submit, not a
        # harmless navigation key — the form's intent is gated
        # BEFORE dispatch (dangerous -> confirm-first, unknown
        # form -> confirm-first, unknown form + payment/
        # destructive page signals -> refuse). A refusal here
        # still reports the fill honestly: the text IS in the
        # field, it is only the submit that did not happen.
        gate = _submit_gate(page, "browser_type", f"submit '{t}'",
                            confirm, loc=loc, submit_requested=True)
        if gate is not None:
            return _mark(f"{gate} The text is filled in '{t}'{many}"
                         f"{frame_note} but was not submitted.")
        try:
            page.keyboard.press("Enter")
        except Exception as e:  # noqa: BLE001
            # Audit fix Batch 1 (WEB-04): never claim the press
            # on its failure path — report the fill and the
            # failed dispatch separately, in the same honest
            # shape _impl_press already uses one function away.
            return _mark(f"Filled '{t}'{many}{frame_note} but Enter "
                         f"did not go through ({_short(e)}) — the form "
                         "was NOT submitted; the text is in the field "
                         "and you can submit it yourself or ask again.")
        _settle(page)
        return _mark(f"Filled '{t}'{many}{frame_note} and pressed Enter. Now on {_page_label(page)}.")
    return _mark(f"Filled '{t}'{many}{frame_note}.")


def _select_options(loc) -> list[str]:
    """The visible option labels of a <select> element (best effort)."""
    try:
        opts = loc.evaluate(
            "el => Array.from(el.options || []).map(o => o.text)"
        )
        return [str(o) for o in (opts or [])]
    except Exception:  # noqa: BLE001
        return []


def _impl_select(target: str, value: str, by: str) -> str:
    if (msg := _need_connection()) is not None:
        return msg
    t = (target or "").strip()
    v = (value or "").strip()
    if not t or not v:
        return "browser_select needs both a target (the dropdown) and a value to choose."
    page = _active_page()
    if (wall := _login_wall_result(page)) is not None:
        return wall  # v5.52 login-wall terminal state
    base, scope_frame = _scoped_locator(page, by, t)
    frame_note = " (inside an embedded frame)" if scope_frame else ""
    try:
        if base.count() == 0:
            return (
                f"I couldn't find a dropdown matching '{t}' (by={by}) on "
                f"{_page_label(page)}."
            )
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't search for '{t}' ({_short(e)})."
    loc = base.first
    # Structural check FIRST (audit B8): "that element isn't a
    # dropdown" and "'v' isn't one of its options" are different
    # problems with different remedies — never let the option error
    # speak for a structural one. select_option on a non-<select>
    # raises, and the old code reported exactly that as a missing
    # option. When the tag can't be determined, fall through and
    # let the attempts (and their errors) speak.
    try:
        tag = str(loc.evaluate("el => (el.tagName || '').toLowerCase()") or "")
    except Exception:  # noqa: BLE001
        tag = ""
    if tag and tag != "select":
        kind = f"<{tag}>" if tag else "element"
        return (
            f"'{t}' isn't a native dropdown — the element it matched "
            f"is a {kind}, not a <select>. Custom dropdown widgets "
            "need browser_click on the control, then browser_click "
            "on the option."
        )
    chosen = None
    try:
        loc.select_option(value=v, timeout=_ELEMENT_TIMEOUT_MS)
        chosen = v
    except Exception:  # noqa: BLE001 — maybe they named the visible label
        try:
            loc.select_option(label=v, timeout=_ELEMENT_TIMEOUT_MS)
            chosen = v
        except Exception as e:  # noqa: BLE001
            options = _select_options(loc)
            if options:
                shown = ", ".join(options[:15])
                extra = (
                    f" … and {len(options) - 15} more"
                    if len(options) > 15 else ""
                )
                return (
                    f"Browser action failed: '{v}' isn't an option "
                    f"in '{t}'. The options are: {shown}{extra}."
                )
            return (
                f"Browser action failed: '{v}' isn't an option in '{t}' "
                f"({_short(e)})."
            )
    try:
        shown = loc.evaluate(
            "el => el.options && el.selectedIndex >= 0 "
            "? el.options[el.selectedIndex].text : ''"
        )
        if shown:
            chosen = shown
    except Exception:  # noqa: BLE001
        pass
    return f"Selected '{chosen}' in '{t}'{frame_note}."


_FOCUSED_PASSWORD_JS = (
    "() => { const el = document.activeElement; return !!el && "
    "el.tagName === 'INPUT' && "
    "(el.getAttribute('type') || '').toLowerCase() === 'password'; }"
)


def _press_delivers_text(key: str) -> bool:
    """True when a Playwright press would put text into the focused
    field: a single printable character, a Shift+character combo, the
    Space key, or Ctrl/Cmd+V (paste). Navigation and editing keys
    (Enter, Tab, arrows, Escape, Backspace…) never deliver text."""
    k = (key or "").strip()
    if len(k) == 1:
        return True
    parts = [p.strip() for p in k.split("+") if p.strip()]
    if not parts:
        return False
    base = parts[-1]
    mods = {p.lower() for p in parts[:-1]}
    if mods & {"control", "ctrl", "alt", "meta", "cmd", "command"}:
        return base.lower() == "v"
    if len(base) == 1:
        return True  # e.g. "Shift+a"
    return base.lower() == "space"


def _impl_press(key: str, confirm: bool = False) -> str | Outcome:
    if (msg := _need_connection()) is not None:
        return msg
    k = (key or "").strip()
    if not k:
        return "browser_press needs a key (e.g. Enter, Tab, Escape, ctrl+a)."
    page = _active_page()
    if (wall := _login_wall_result(page)) is not None:
        return wall  # v5.52 login-wall terminal state
    if _press_delivers_text(k):
        # HARD RULE, pack-wide (v4.4, audit B9): browser_type refuses
        # password fields; a press must not spell a password into a
        # focused one character by character either. Navigation keys
        # never reach this check.
        try:
            focused_password = bool(page.evaluate(_FOCUSED_PASSWORD_JS))
        except Exception:  # noqa: BLE001 — can't tell: fail closed for text keys
            return (
                "I couldn't check which field is focused, so I won't "
                "send a text-producing key blind. Click the field you "
                "mean first, then ask again."
            )
        if focused_password:
            return (
                "A password field is focused — passwords are yours to "
                "type, I won't send keystrokes into it. Navigation keys "
                "like Enter, Tab and Escape still work."
            )
    if _is_submit_key(k):
        # Audit fix Batch 1 (WEB-02): Enter is a submit, not a
        # harmless navigation key — the focused element's owning
        # form is gated BEFORE dispatch, on the same fork as
        # browser_type submit (dangerous -> confirm-first,
        # uninspectable -> confirm-first, uninspectable +
        # payment/destructive page signals -> refuse). Tab,
        # Escape, arrows and text keys never reach this gate.
        gate = _submit_gate(page, "browser_press", f"press '{k}'",
                            confirm, focused=True)
        if gate is not None:
            return gate
    try:
        page.keyboard.press(k)
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't press '{k}' ({_short(e)})."
    _settle(page)
    # R1b: the key was dispatched; the label below is the page
    # identity after the press, not proof the key acted in the
    # focused field — mark it honestly unverified.
    from .. import tool_outcome as _outcome  # R1b: seam wiring

    return _outcome.unverified(
        "Pressed %s. Now on %s. (Key dispatched; whether it acted "
        "in the focused field was not confirmed.)"
        % (k, _page_label(page)),
        "no field-level read-back exists for a dispatched browser key")

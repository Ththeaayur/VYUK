"""browser_nav concern extracted from core/browser.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/browser."""

import re
import time
from pathlib import Path
from .browser_session import (_GOTO_TIMEOUT_MS, _active_page, _need_connection, _page_label, _settle, _short, _tracked_page, _set_active_page, _context_pages, _new_context_page)  # GAPS-18: narrowed + state accessors
from .browser_gates import (_DANGEROUS_CLICK_RE, _MATCH_SCAN_CAP, _DESTRUCTIVE_SUBMIT_RE, _FORM_INTENT_JS, _FOCUSED_FORM_INTENT_JS, _is_submit_key, _dangerous_click_verdict, _dangerous_submit_verdict, _uninspectable_submit_verdict, _raw_point_verdict, _classify_submit_intent, _submit_gate, _loc_visible, _narrow_visible, _LOGIN_WALL_JS, _login_wall_result)


def _looks_like_timeout(exc: Exception) -> bool:
    """Playwright's navigation-budget expiry, narrowly (v5.53,
    F5): its TimeoutError class, whose message opens
    'Timeout Nms exceeded'. Network failures (net::ERR_…) are
    NOT timeouts here — those navigations genuinely failed and
    keep the failure wording."""
    if type(exc).__name__ == "TimeoutError":
        return True
    return str(exc).startswith("Timeout ")


def _goto_target(clean: str) -> tuple[str | None, str | None]:
    """Resolve a model-supplied goto URL to (url_to_open, refusal).

    Scheme allowlist (v5.46 programme item 8, C4): ONLY http and
    https may be opened. _impl_goto used to accept any
    ``scheme://`` and hand it straight to page.goto, so a
    ``file://`` URL turned the browser into a protected-path
    bypass — browser_read is read-only-classed and its text path
    never passes the file tools' protected-path check, yet it
    would happily read a local file the browser had loaded.
    Every other scheme (file, data, javascript, ftp, chrome,
    about, blob, …) is therefore refused here, BEFORE page.goto
    is called. This is the ONE guard for model-supplied URLs:
    _impl_new_tab delegates to _impl_goto when it is given a URL,
    so it inherits the refusal without a second copy of the check.

    browser_read is deliberately NOT guarded separately: it can
    only read what _impl_goto (or a page-initiated navigation)
    already loaded, so the allowlist here is what decides which
    schemes its reads can ever come from.

    A scheme is an explicit ``scheme:`` / ``scheme://`` prefix.
    A bare host:port (example.com:8080/…) is NOT a scheme — the
    colon there introduces a numeric port — so it keeps the
    historical behaviour of defaulting to https://, as does a
    bare host with no colon at all.
    """
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):(.*)$", clean,
                     re.DOTALL)
    scheme = None
    if match and not match.group(2)[:1].isdigit():
        scheme = match.group(1).lower()
    if scheme is None:
        return "https://" + clean, None
    if scheme not in ("http", "https"):
        return None, (
            "Refused: VYUK's browser only opens http:// and "
            "https:// pages. Local files are read with the file "
            "tools instead, which enforce the protected-path "
            "rules — nothing was opened."
        )
    return clean, None


def _scam_hash_warning(url: str) -> str | None:
    """v5.66 (MN-6): local-hash scam consult for a navigated URL.

    Consults core/scam_guard ONLY when the user has placed a
    local hash set at data/scam_hashes.txt (one SHA-256 hex digest
    per line; the file never ships). Absent file, missing module, or
    any consult error -> None, i.e. no change to the result. A
    match returns a warning string; navigation is NEVER blocked by
    this consult (warn-only by design — a block ruling would be a
    separate owner decision).
    """
    try:
        from .. import scam_guard as _sg  # Phase 3 D2a: scam_guard stays flat in core/
    except Exception:  # noqa: BLE001 — consult is best-effort
        return None
    path = Path(__file__).resolve().parent.parent.parent / "data" / "scam_hashes.txt"  # Phase 3 D2a: one level deeper in core/desktop/
    if not path.is_file():
        return None
    try:
        bad = _sg.load_local_hash_set(str(path))
        verdict = _sg.check_url(url, known_bad_hashes=bad)
    except Exception:  # noqa: BLE001 — consult is best-effort
        return None
    if verdict.get("verdict") == "match":
        return (
            "Warning: this URL matches a hash in your local scam "
            "list (data/scam_hashes.txt). The page was still opened — "
            "check it carefully before entering anything."
        )
    return None


def _impl_goto(url: str) -> str:
    if (msg := _need_connection()) is not None:
        return msg
    clean = (url or "").strip()
    if not clean:
        return "browser_control 'goto' needs a url."
    # v5.46 programme item 8 (C4): the scheme allowlist lives in
    # _goto_target — a refusal returns before _active_page /
    # page.goto, so a refused URL is never navigated to.
    clean, refusal = _goto_target(clean)
    if refusal is not None:
        return refusal
    page = _active_page()
    try:
        # v5.53 (F5): the page counts as opened at
        # DOMContentLoaded, not at the full load event — heavy
        # pages (YouTube-class) used to ride `load` all the way
        # to the 20 s cap and report a working navigation as
        # "didn't work". The 4 s settle below still applies.
        page.goto(clean, timeout=_GOTO_TIMEOUT_MS,
                  wait_until="domcontentloaded")
    except Exception as e:  # noqa: BLE001
        if _looks_like_timeout(e):
            # The budget expired with the navigation still
            # under way — Playwright does not cancel it, the
            # page goes on loading. A soft result, in the same
            # family as _submit's nav class: still loading,
            # connection alive, nothing reset.
            return (
                f"Still loading {clean} — the page didn't "
                f"finish within {_GOTO_TIMEOUT_MS // 1000}s, "
                "but the navigation is under way and the "
                "connection is fine. Ask me to wait, or to "
                "read the page."
            )
        return f"Browser action failed: couldn't open {clean} ({_short(e)})."
    _settle(page)
    # v5.66 (MN-6): local-hash scam consult, warn-only, default-off
    # (no data/scam_hashes.txt -> byte-identical result).
    _scam_note = _scam_hash_warning(clean)
    if _scam_note:
        return f"Opened {_page_label(page)}. {_scam_note}"
    return f"Opened {_page_label(page)}."


def _impl_history(action: str) -> str:
    if (msg := _need_connection()) is not None:
        return msg
    page = _active_page()
    try:
        # v5.53 (F5): DOMContentLoaded, as in _impl_goto — a
        # reload of a heavy page is a refresh once it commits,
        # not once every last resource lands.
        if action == "back":
            page.go_back(timeout=_GOTO_TIMEOUT_MS,
                         wait_until="domcontentloaded")
        elif action == "forward":
            page.go_forward(timeout=_GOTO_TIMEOUT_MS,
                            wait_until="domcontentloaded")
        else:
            page.reload(timeout=_GOTO_TIMEOUT_MS,
                        wait_until="domcontentloaded")
    except Exception as e:  # noqa: BLE001
        if _looks_like_timeout(e):
            return (
                f"Went {action} — the page is still loading "
                f"(it didn't finish within "
                f"{_GOTO_TIMEOUT_MS // 1000}s), and the "
                "connection is fine. Ask me to wait, or to "
                "read the page."
            )
        return f"Browser action failed: '{action}' didn't work ({_short(e)})."
    _settle(page)
    return f"Went {action}. Now on {_page_label(page)}."


def _impl_new_tab(url: str) -> str:
    if (msg := _need_connection()) is not None:
        return msg
    page = _new_context_page()
    if (url or "").strip():
        result = _impl_goto(url)
        return f"Opened a new tab. {result}"
    return "Opened a new blank tab."


def _impl_close_tab() -> str:
    if (msg := _need_connection()) is not None:
        return msg
    page = _active_page()
    label = _page_label(page)
    try:
        page.close()
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't close the tab ({_short(e)})."
    _set_active_page(None)
    remaining = [p for p in _context_pages() if not p.is_closed()]
    if remaining:
        _set_active_page(remaining[-1])
        return f"Closed tab {label}. Now on {_page_label(remaining[-1])}."
    new_page = _new_context_page()
    return f"Closed tab {label}. That was the last one, so I opened a blank tab."


def _impl_switch_tab(tab_index: int) -> str:
    if (msg := _need_connection()) is not None:
        return msg
    pages = _context_pages()
    if tab_index < 0 or tab_index >= len(pages):
        return (
            f"There's no tab {tab_index} — {len(pages)} tab(s) are open "
            f"(indexes 0..{len(pages) - 1}). Use action 'list_tabs' to see them."
        )
    page = pages[tab_index]
    _set_active_page(page)
    try:
        page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass
    return f"Switched to tab {tab_index}: {_page_label(page)}."


def _impl_list_tabs() -> str:
    if (msg := _need_connection()) is not None:
        return msg
    pages = _context_pages()
    active = _tracked_page()
    lines = [f"{len(pages)} tab(s) open:"]
    for i, p in enumerate(pages):
        marker = " <- active" if p is active else ""
        lines.append(f"{i}: {_page_label(p)}{marker}")
    return "\n".join(lines)


def _impl_scroll(direction: str, amount: int) -> str:
    # v5.69.1 field fix (owner report: YouTube Shorts "can't scroll"):
    # window.scrollBy silently does nothing inside a snap container
    # (Shorts traps scroll in its own feed), yet the old code reported
    # "Scrolled down Npx" unconditionally — a success claim for a
    # no-op. Now: measure scrollY before/after, report honestly when
    # nothing moved, and for up/down try the arrow-key fallback (the
    # key Shorts itself listens for), verified by a URL change.
    if (msg := _need_connection()) is not None:
        return msg
    page = _active_page()
    d = (direction or "down").strip().lower()
    px = max(50, min(int(amount or 600), 5000))
    try:
        before_y = page.evaluate("() => window.scrollY")
        before_url = page.url
        if d == "top":
            page.evaluate("() => window.scrollTo(0, 0)")
        elif d == "bottom":
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        elif d == "up":
            page.evaluate(f"() => window.scrollBy(0, {-px})")
        else:
            page.evaluate(f"() => window.scrollBy(0, {px})")
        after_y = page.evaluate("() => window.scrollY")
        if after_y != before_y:
            if d == "top":
                return "Scrolled to the top of the page."
            if d == "bottom":
                return "Scrolled to the bottom of the page."
            if d == "up":
                return f"Scrolled up {px}px."
            return f"Scrolled down {px}px."
        # Nothing moved: the page traps scroll (snap container).
        # Arrow-key fallback for up/down only — a navigation key,
        # never text, so the password-field rule cannot bite — and
        # only when no login wall is showing.
        if d in ("up", "down") and _login_wall_result(page) is None:
            page.keyboard.press("ArrowUp" if d == "up" else "ArrowDown")
            if page.url != before_url:
                which = "previous" if d == "up" else "next"
                return (
                    "Moved to the "
                    + which
                    + " item — this page handles scrolling itself "
                    "(like a short-form feed), so I used the arrow "
                    "key instead. Now on "
                    + _page_label(page)
                    + "."
                )
        return (
            "The page didn't move when I scrolled it, and the arrow "
            "key didn't move it either — this page traps scrolling "
            "inside its own container (short-form feeds like YouTube "
            "Shorts do this). I'm still on " + _page_label(page) + "."
        )
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't scroll ({_short(e)})."


def _impl_wait(seconds: float) -> str:
    if (msg := _need_connection()) is not None:
        return msg
    secs = max(0.0, min(float(seconds or 1.0), 10.0))
    page = _active_page()
    try:
        page.wait_for_timeout(int(secs * 1000))
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: wait interrupted ({_short(e)})."
    return f"Waited {secs:g}s. Still on {_page_label(page)}."


def _impl_screenshot(path: str) -> str:
    if (msg := _need_connection()) is not None:
        return msg
    page = _active_page()
    if (path or "").strip():
        dest = Path(path).expanduser()
    else:
        dest = (
            Path.home() / "Pictures"
            / f"nova_web_{time.strftime('%Y%m%d_%H%M%S')}.png"
        )
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(dest))
    except Exception as e:  # noqa: BLE001
        return f"Browser action failed: couldn't take a screenshot ({_short(e)})."
    return f"Screenshot of {_page_label(page)} saved to {dest}."

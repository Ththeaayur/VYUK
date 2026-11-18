"""B-D7 (2026-11-09) browser automation via Playwright — the ONE
dict-returning wrapper a driver calls for browser actions.

GAPS-1 (2026-11-12): the engine is stock Playwright in every live
path. The patched engine (B-D7/B-D7r, 2026-11-09) was removed per
the 2026-11-11 ruling: the v5.59 seam + A/B harness are deleted,
and this wrapper drives the same stock engine through
``_PlaywrightBackend``. There is no patched driver, no import
swap, and no second engine anywhere in the tree.

GAPS-18 (2026-11-13): the engine starts in exactly one place —
core/desktop/browser_engine.start_playwright, the single engine
start call in the tree. The worker session
(core/desktop/browser_session.py: _start_pw) and this backend
are its two callers; neither starts the engine itself any more.
The old claim that _start_pw was the single live engine start was
false while this backend started beside it — both starts now go
through the one interface.

THE GAP (proved first, file/line evidence)
------------------------------------------
Browser automation ALREADY exists in the tree — as scattered
human-string tool handlers on stock Playwright:

* The worker starts STOCK Playwright only:
  ``core/desktop/browser_session.py:280`` — ``_start_pw()`` does
  ``from playwright.sync_api import sync_playwright``. Nothing
  in ``core/browser.py`` or the worker imports any other engine.
  (Historical — GAPS-18: the start now lives in
  browser_engine.start_playwright; _start_pw is its caller.)
* The tool layer already navigates, clicks, types and reads —
  ``core/desktop/browser_nav.py`` (``_impl_goto`` :97,
  ``_impl_switch_tab`` :203, ``_impl_list_tabs`` :221),
  ``core/desktop/browser_click.py`` (``_act_on_element`` :79 —
  visible-narrowing, no-guess, before/after page probe,
  no-effect verdict), ``core/desktop/browser_interact.py``
  (``_impl_type`` :84 — password-field refusal on EVERY match,
  fill + ``input_value`` read-back), ``core/desktop/
  browser_read.py`` (text/find/links/fields/elements) — behind
  the registered tools browser_control / browser_read /
  browser_click / browser_type. What does NOT exist, and is
  the remainder this module builds:

1. NO single dict-returning wrapper a driver can call. The
   existing functions return human sentences; a driver cannot
   tell success from failure without parsing prose.
2. NO page identity carried across actions. Tabs are switched
   BY INDEX (``_impl_switch_tab``, browser_nav.py:203 — tabs
   reorder, so an index names the wrong tab after any open or
   close). Nothing binds an action to the page it was asked
   for.
3. NO load-state refusal. ``_settle`` (browser_session.py:522)
   waits for domcontentloaded but SWALLOWS the timeout — an
   action on a half-loaded page proceeds silently.
4. NO uniform read-back rule. Click and fill each verify in
   their own way at the tool layer; nothing enforces the
   verification for every action, and no result names the page
   it acted on in a machine-readable shape.

The existing tools are NOT changed by this batch (their
behaviour is theirs). This wrapper is the new driver surface;
the tools keep working as they are. Fix 3's attach
(``_impl_attach_cdp``, browser_session.py:429 — CDP connect
to the user's real Chrome, opt-in, default OFF) is NOT
changed: the attach is the CONNECTION for the existing tools;
this wrapper is the AUTOMATION on top of a Playwright
session of its own — it launches Playwright driving the
user's installed Chrome (``channel="chrome"``) on VYUK's own
managed profile (``data/browser_profile``), never the user's
real Chrome user-data dir, so it never fights the user's
running Chrome for a profile lock, and ``close()`` only ever
closes what ``launch()`` started — never the user's Chrome.

LOPHOLE FAMILY — "a browser action that acts on the wrong
page, or acts without the user knowing"
------------------------------------------------------------
* An action that runs on the wrong tab (asked for tab A, acted
  on tab B — tabs reorder, an index is stale the moment a tab
  opens or closes).
* A click that lands on the wrong element (the selector matched
  something else after the page changed).
* A form filled with the wrong data (the field's identity was
  assumed, never verified).
* An action that runs while the page is still loading (and
  silently does nothing, or acts on a half-loaded page).
* An automation that runs without the user knowing which page
  it is on (no page identity in the result).

ONE design closes the class: every browser action goes through
this module; a page is named by a PAGE IDENTITY —
``{"tab_id", "url", "title"}`` — where ``tab_id`` is a
wrapper-assigned stable handle (never an index, never reused
within a session); the wrapper verifies the live page's URL
matches the named identity BEFORE acting (a mismatch is an
honest ``wrong_page`` failure, never an action on the wrong
page); every action waits for the page to settle first and
REFUSES with ``still_loading`` when it does not (never acts
on a half-loaded page); every click resolves to exactly one
visible match (zero is ``not_found``, several visible is
``ambiguous`` — never a first-match guess) and reads the
element's identity back after the click, and a click with no
observed effect is an honest ``no_effect`` failure, never a
success claim; every fill refuses password fields (an
unreadable type refuses too — an inspection failure is NOT
"not a password field") and reads the field's value AND label
back after filling (a mismatch is an honest failure, an
unreadable value is ``fill_unverified``, never a success
claim); every result names the page it acted on (URL + title,
read back AFTER the action).

Rejected alternatives (recorded):
* Tab-by-index identity — tabs reorder; the wrong tab acts.
  (What ``_impl_switch_tab`` does today; this wrapper never
  takes an index.)
* Selector-only clicks — the selector matched something else
  after the page changed; no read-back means a wrong-element
  click reports success.
* Fill-without-verify — the field's identity assumed; a fill
  into the wrong field reports success.
* Act-during-load — ``_settle`` swallows the timeout, so a
  half-loaded page is silently acted on.
* Result-without-page-identity — the user never knows which
  page was acted on.
* A home-grown CDP client — the
  automation IS Playwright (the stock sync API starts the real
  driver); a hand-rolled client would re-implement the
  engine instead of using it.

DEPENDENCY (restored GAPS-1 — stock engine)
-------------------------------------------
playwright 1.64.0, Apache-2.0 — licence read from the
.venv-pw installed METADATA 2026-11-12 by this batch (not from
memory): ``playwright-1.64.0.dist-info/METADATA`` carries
``License-Expression: Apache-2.0`` + ``License-File: LICENSE``,
author "Microsoft Corporation", source
github.com/Microsoft/playwright-python. Transitives, from the
installed METADATA ``Requires-Dist``: pyee>=13,<15 (MIT — pyee
14.0.0 installed in .venv-pw, METADATA ``License: MIT``) and
greenlet>=3.1.1,<4.0.0 (MIT AND PSF-2.0 — greenlet 3.5.6
installed in .venv-pw, METADATA ``License-Expression: MIT AND
PSF-2.0``). No GPL anywhere in the set. WEIGHT, measured
2026-11-12 from .venv-pw: playwright 1.64.0 unpacks to 140 MB
(145,725,011 B — the Node driver binary dominates, as before).
The removed engine weighed the same order because it IS a patched
copy of the same engine; as a REPLACEMENT for it as the engine
it is net ~0. $0, commercial use permitted.
Requires-Python >=3.10; the tree is 3.12. Lazy-imported (the
sympy pattern); degrades honestly to ``engine_unavailable``
when playwright or Chrome is missing.

Install state, verified 2026-11-12 by pip show + import
(this batch, not from memory): playwright is importable in
.venv-pw (the Playwright venv) ONLY — playwright 1.64.0,
greenlet 3.5.6, pyee 14.0.0; system python3:
``ModuleNotFoundError``; Qt venv (.venv-qt):
``ModuleNotFoundError``. The patched engine is importable in NO
interpreter. The Linux lab runs the logic against an injected
fake backend (see below); the real-Windows leg (real Playwright
driving real installed Chrome on his machine) is a field leg,
owed, not this batch.

BY-DESIGN WALLS (same as Fix 3, never weakened here)
-----------------------------------------------------
* NEVER fills a password field — ``type="password"`` refuses,
  and a field whose type cannot be read refuses too.
* NEVER solves a CAPTCHA — no such function exists here.
* NEVER downloads a browser — ``channel="chrome"`` drives the
  user's INSTALLED Chrome; no ``playwright install`` call
  exists anywhere in this module, and the wrapper never calls
  one.
* NEVER touches the user's real Chrome profile — the default
  profile is VYUK's own ``data/browser_profile``.

BACKEND PROTOCOL (what a backend must provide)
----------------------------------------------
A backend is any object with these methods; the real one is
``_PlaywrightBackend`` (Playwright, lazy-imported), the tests
and the prover inject a fake with the same shape:

* ``start(profile_dir)`` -> None (raises EngineUnavailable /
  LaunchFailed).
* ``pages()`` -> list of {"handle": opaque, "url": str,
  "title": str} for the open tabs.
* ``load_state(handle)`` -> "loaded" | "loading" (raises
  PageGone when the page is gone).
* ``navigate(handle, url)`` -> None (raises TimeoutError when
  the navigation does not settle; raises PageGone).
* ``matches(handle, selector)`` -> list of {"handle": opaque,
  "visible": bool} — every match, visible or not.
* ``element_identity(el)`` -> {"tag": str, "text": str,
  "role": str} (raises PageGone when the element is gone).
* ``click_element(el)`` -> {"changed": bool, "detail": str} —
  whether anything was observed to change after the click.
* ``field_type(el)`` -> str | None; raises on an inspection
  failure (an unreadable type is a refusal, never a guess).
* ``field_label(el)`` -> str (aria-label / placeholder /
  name / selector, first non-empty).
* ``fill_element(el, text)`` -> None.
* ``field_value(el)`` -> str | None (None = unreadable).
* ``page_text(handle)`` -> str (capped by the wrapper).
* ``page_url(handle)`` / ``page_title(handle)`` -> str.
* ``close()`` -> None.

A backend raises ``PageGone`` when the page it is asked about
is closed; the wrapper turns that into an honest failure dict,
never a success claim and never a crash.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import browser_engine as _engine  # GAPS-18: the one engine interface

def _launch_persistent_context(chromium, profile_dir, *,
                               channel: str = "chrome",
                               headless: bool = False):
    """Launch the one persistent context on the worker's contract:
    channel "chrome", her persistent profile, headed. The channel
    is asserted, not defaulted away: a call asking for any other
    channel is a caller bug and raises instead of quietly launching
    a different browser. ``profile_dir`` must be given — the launch
    never guesses a profile, and in particular never defaults to a
    real-Chrome user-data dir. (Discipline carried over from the
    deleted v5.59 seam's launch_persistent_context, GAPS-1.)"""
    if channel != "chrome":
        raise ValueError(
            "browser automation launches channel='chrome' "
            f"only; got {channel!r}")
    if not profile_dir:
        raise ValueError(
            "browser automation requires an explicit profile_dir "
            "(her persistent profile); it never guesses one")
    return chromium.launch_persistent_context(
        str(profile_dir), headless=headless, channel=channel)

LOAD_WAIT_S = 10.0          # bounded settle wait before any action
GOTO_TIMEOUT_MS = 20_000    # navigation budget (mirrors _GOTO_TIMEOUT_MS)
ELEMENT_TIMEOUT_MS = 8_000  # element action budget (mirrors _ELEMENT_TIMEOUT_MS)
TEXT_CAP = 6_000            # page-text cap on read (mirrors read_pdf_text's shape)
ENGINE = _engine.ENGINE  # GAPS-18: single source in browser_engine


class EngineUnavailable(Exception):
    """Playwright (or Chrome) is not available on this machine."""


class LaunchFailed(Exception):
    """Playwright started but the browser could not be launched."""


class PageGone(Exception):
    """The page the caller named is closed."""


class ElementNotFound(Exception):
    """No element matches the selector."""


class ElementAmbiguous(Exception):
    """Several visible elements match; the wrapper never guesses."""


def _data_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data"


def default_profile_dir() -> str:
    """VYUK's own managed browser profile — never the user's
    real Chrome user-data dir (see the module docstring)."""
    return str(_data_dir() / "browser_profile")


def engine_available() -> bool:
    """True when the playwright package is importable. Probe
    only: this never imports playwright, so importing this
    module cannot pull the driver into a process."""
    return _engine.engine_available()


def engine_status() -> dict:
    """Honest availability report. Chrome presence is NOT
    knowable without launching, so it is reported as unknown
    until launch() — never guessed."""
    return _engine.engine_status()


class _RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class _PlaywrightBackend:
    """The REAL backend: Playwright driving the user's installed
    Chrome. Every method here is a thin call into the Playwright
    surface the engine starts; all policy (page identity, load
    wait, read-backs) lives in the wrapper above, never here."""

    def __init__(self):
        self._pw = None
        self._context = None

    def start(self, profile_dir: str) -> None:
        if not engine_available():
            raise EngineUnavailable(
                "playwright is not installed in this interpreter")
        try:
            # GAPS-18: the engine starts in exactly one place —
            # browser_engine.start_playwright. This backend is the
            # second of its two callers (the session is the other).
            self._pw = _engine.start_playwright()
            chromium = self._pw.chromium
            # channel="chrome" is the installed Chrome — the launch
            # helper asserts it, and no download is ever requested.
            self._context = _launch_persistent_context(
                chromium, profile_dir, channel="chrome", headless=False)
        except EngineUnavailable:
            raise
        except ImportError as exc:
            raise EngineUnavailable(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — launch failure
            self._stop_pw()
            raise LaunchFailed(
                f"could not launch installed Chrome ({exc})") from exc

    def _stop_pw(self) -> None:
        pw, self._pw = self._pw, None
        if pw is not None:
            try:
                pw.stop()
            except Exception:  # noqa: BLE001 — teardown never raises
                pass

    def _pages(self):
        if self._context is None:
            raise LaunchFailed("browser session is not started")
        return [p for p in self._context.pages if not p.is_closed()]

    def pages(self) -> list[dict]:
        out = []
        for page in self._pages():
            out.append({"handle": page,
                        "url": self.page_url(page),
                        "title": self.page_title(page)})
        return out

    def load_state(self, handle) -> str:
        try:
            handle.wait_for_load_state("domcontentloaded", timeout=250)
        except Exception as exc:  # noqa: BLE001
            if "closed" in str(exc).lower() or handle.is_closed():
                raise PageGone("the page is closed") from exc
            return "loading"
        return "loaded"

    def navigate(self, handle, url: str) -> None:
        try:
            handle.goto(url, timeout=GOTO_TIMEOUT_MS,
                        wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            if handle.is_closed():
                raise PageGone("the page is closed") from exc
            if "timeout" in str(exc).lower():
                raise TimeoutError(
                    f"navigation to {url} did not settle within "
                    f"{GOTO_TIMEOUT_MS // 1000}s") from exc
            raise

    def matches(self, handle, selector: str) -> list[dict]:
        try:
            loc = handle.locator(selector)
            count = loc.count()
        except Exception as exc:  # noqa: BLE001
            if handle.is_closed():
                raise PageGone("the page is closed") from exc
            raise ElementNotFound(
                f"could not search for '{selector}' ({exc})") from exc
        out = []
        for i in range(count):
            nth = loc.nth(i)
            try:
                visible = bool(nth.is_visible())
            except Exception:  # noqa: BLE001 — unreadable is hidden
                visible = False
            out.append({"handle": nth, "visible": visible})
        return out

    def element_identity(self, el) -> dict:
        try:
            info = el.evaluate(
                "(el) => ({tag: el.tagName || '', "
                "text: ((el.innerText || el.value || "
                "el.getAttribute('aria-label') || '')"
                ".trim().slice(0, 80)), "
                "role: el.getAttribute('role') || ''})")
        except Exception as exc:  # noqa: BLE001
            raise PageGone(
                "the element is gone — the page changed") from exc
        return {"tag": str(info.get("tag", "")),
                "text": str(info.get("text", "")),
                "role": str(info.get("role", ""))}

    def click_element(self, el) -> dict:
        page = el.page
        before = (self.page_url(page), self.page_title(page),
                  self._probe(page))
        try:
            el.click(timeout=ELEMENT_TIMEOUT_MS)
        except Exception as exc:  # noqa: BLE001
            if page.is_closed():
                raise PageGone("the page closed during the click") from exc
            raise
        after = (self.page_url(page), self.page_title(page),
                 self._probe(page))
        changed = before != after
        return {"changed": changed,
                "detail": ("page changed after the click"
                           if changed else
                           "no navigation, title change, or content "
                           "change was observed after the click")}

    def _probe(self, page) -> str:
        try:
            text = page.evaluate(
                "() => document.body ? "
                "document.body.innerText.slice(0, 500) : ''")
        except Exception:  # noqa: BLE001 — probe failure is "no signal"
            return ""
        return str(text)

    def field_type(self, el) -> str | None:
        # An inspection failure RAISES (never degrades to "") —
        # the wrapper refuses an unreadable field outright.
        return (el.get_attribute("type") or "").strip().lower() or None

    def field_label(self, el) -> str:
        for attr in ("aria-label", "placeholder", "name", "id"):
            try:
                value = (el.get_attribute(attr) or "").strip()
            except Exception:  # noqa: BLE001 — try the next attribute
                continue
            if value:
                return value
        return ""

    def fill_element(self, el, text: str) -> None:
        el.fill(text, timeout=ELEMENT_TIMEOUT_MS)

    def field_value(self, el) -> str | None:
        try:
            return el.input_value(timeout=ELEMENT_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 — unreadable field
            return None

    def page_text(self, handle) -> str:
        try:
            text = handle.evaluate(
                "() => document.body ? document.body.innerText : ''")
        except Exception as exc:  # noqa: BLE001
            if handle.is_closed():
                raise PageGone("the page is closed") from exc
            raise
        return str(text)

    def page_url(self, handle) -> str:
        try:
            return str(handle.url)
        except Exception:  # noqa: BLE001
            return "?"

    def page_title(self, handle) -> str:
        try:
            return str(handle.title())
        except Exception:  # noqa: BLE001
            return "?"

    def close(self) -> None:
        context, self._context = self._context, None
        if context is not None:
            try:
                context.close()
            except Exception:  # noqa: BLE001 — teardown never raises
                pass
        self._stop_pw()


def _fail(reason: str, detail: str, page: dict | None = None) -> dict:
    out = {"ok": False, "engine": ENGINE, "reason": reason,
           "detail": detail}
    if page is not None:
        out["page"] = dict(page)
    return out


class BrowserAutomation:
    """One Playwright session, one wrapper, every action verified.

    A page is named by its PAGE IDENTITY — {"tab_id", "url",
    "title"} — returned by every call that names a page. The
    caller passes the latest identity back in; the wrapper
    resolves tab_id to the live page and refuses (wrong_page)
    when the live URL is not the named URL. tab_id values are
    assigned here ("tab-1", "tab-2", …), never indices, never
    reused within a session."""

    def __init__(self, *, backend=None, clock=None):
        self._backend = backend
        self._clock = clock or _RealClock()
        self._launched = False
        self._tabs: dict[str, object] = {}   # tab_id -> backend handle
        self._tab_urls: dict[str, str] = {}  # tab_id -> last known url
        self._next_tab = 1

    # -- engine ----------------------------------------------------

    def engine_available(self) -> bool:
        if self._backend is not None:
            return True  # an injected backend IS the engine for tests
        return engine_available()

    def engine_status(self) -> dict:
        if self._backend is not None:
            return {"ok": True, "engine": ENGINE, "available": True,
                    "version": "fake backend (tests)",
                    "chrome": "fake backend (tests)", "reason": None}
        return engine_status()

    def _backend_or_real(self):
        if self._backend is not None:
            return self._backend
        return _PlaywrightBackend()

    # -- session ---------------------------------------------------

    def launch(self, *, profile_dir: str | None = None) -> dict:
        if self._launched:
            page = self._current_identity()
            return {"ok": True, "engine": ENGINE,
                    "detail": "browser session already launched",
                    "page": page}
        backend = self._backend_or_real()
        try:
            backend.start(profile_dir or default_profile_dir())
        except EngineUnavailable as exc:
            return _fail("engine_unavailable", str(exc))
        except LaunchFailed as exc:
            return _fail("engine_unavailable",
                         f"installed Chrome could not be launched: {exc}")
        except Exception as exc:  # noqa: BLE001 — launch never crashes
            return _fail("engine_unavailable",
                         f"installed Chrome could not be launched: {exc}")
        self._backend = backend
        self._launched = True
        self._refresh_tabs()
        page = self._current_identity()
        if page is None:
            return _fail("engine_unavailable",
                         "Chrome launched but no tab is open")
        return {"ok": True, "engine": ENGINE,
                "detail": "launched Playwright on installed Chrome",
                "page": page}

    def close(self) -> dict:
        if not self._launched:
            return _fail("not_launched",
                         "no browser session is launched, so there is "
                         "nothing to close")
        backend = self._backend
        self._tabs = {}
        self._tab_urls = {}
        self._launched = False
        try:
            backend.close()
        except Exception:  # noqa: BLE001 — close never crashes
            pass
        # Close only ever closes what launch() started: the wrapper
        # never adopts the user's running Chrome, so nothing the
        # user owns is touched here.
        return {"ok": True, "engine": ENGINE,
                "detail": "detached from the launched browser; any "
                          "Chrome the user owns is untouched"}

    # -- page identity ----------------------------------------------

    def _refresh_tabs(self) -> None:
        """Re-read the open tabs from the backend, keeping stable
        tab_ids for handles already known and assigning fresh ones
        to new tabs. Closed tabs drop out of the registry."""
        try:
            pages = self._backend.pages()
        except Exception:  # noqa: BLE001 — no tabs readable, keep state
            return
        handle_to_id = {id(h): tid for tid, h in self._tabs.items()}
        new_tabs: dict[str, object] = {}
        new_urls: dict[str, str] = {}
        for entry in pages:
            handle = entry["handle"]
            tid = handle_to_id.get(id(handle))
            if tid is None:
                tid = f"tab-{self._next_tab}"
                self._next_tab += 1
            new_tabs[tid] = handle
            new_urls[tid] = entry["url"]
        self._tabs = new_tabs
        self._tab_urls = new_urls

    def _identity(self, tab_id: str) -> dict:
        handle = self._tabs[tab_id]
        return {"tab_id": tab_id,
                "url": self._backend.page_url(handle),
                "title": self._backend.page_title(handle)}

    def _current_identity(self) -> dict | None:
        if not self._tabs:
            return None
        # The active tab is the most recently registered one.
        tab_id = sorted(self._tabs, key=lambda t: int(t.split("-")[1]))[-1]
        return self._identity(tab_id)

    def _verify_page(self, page: dict | None) -> tuple[object | None, dict | None]:
        """Resolve a caller-supplied page identity to a live
        handle, verifying the live page is the named page.
        Returns (handle, None) or (None, failure dict)."""
        if not self._launched:
            return None, _fail("not_launched",
                               "no browser session is launched — "
                               "launch() first")
        if not isinstance(page, dict) or not page.get("tab_id"):
            return None, _fail("wrong_page",
                               "no page identity was given — every "
                               "action names its page (tab_id + url)")
        tab_id = page["tab_id"]
        self._refresh_tabs()
        if tab_id not in self._tabs:
            return None, _fail(
                "wrong_page",
                f"tab {tab_id} is not open (it may have been closed) "
                f"— list_tabs() shows the open tabs",
                page)
        handle = self._tabs[tab_id]
        try:
            live_url = self._backend.page_url(handle)
        except PageGone:
            return None, _fail("wrong_page",
                               f"tab {tab_id} is closed", page)
        named_url = page.get("url")
        if named_url and live_url != named_url:
            return None, _fail(
                "wrong_page",
                f"tab {tab_id} is no longer on the named page: "
                f"it is on {live_url}, not {named_url} — refusing "
                f"to act on the wrong page",
                {"tab_id": tab_id, "url": live_url,
                 "title": self._backend.page_title(handle)})
        return handle, None

    def _wait_settled(self, handle) -> bool:
        """Bounded load-state wait. False means the page is still
        loading after the budget — the caller REFUSES, never acts
        on a half-loaded page."""
        deadline = self._clock.monotonic() + LOAD_WAIT_S
        while True:
            state = self._backend.load_state(handle)
            if state == "loaded":
                return True
            if self._clock.monotonic() >= deadline:
                return False
            self._clock.sleep(0.25)

    def _settle_or_fail(self, handle, page: dict) -> dict | None:
        try:
            settled = self._wait_settled(handle)
        except PageGone:
            return _fail("wrong_page", "the page closed while waiting "
                                       "for it to settle", page)
        if not settled:
            return _fail("still_loading",
                         "the page is still loading after "
                         f"{LOAD_WAIT_S:.0f}s — refusing to act on a "
                         "half-loaded page; ask again once it settles",
                         page)
        return None

    # -- tabs --------------------------------------------------------

    def list_tabs(self) -> dict:
        if not self._launched:
            return _fail("not_launched",
                         "no browser session is launched — launch() first")
        self._refresh_tabs()
        tabs = [self._identity(tid) for tid in sorted(
            self._tabs, key=lambda t: int(t.split("-")[1]))]
        return {"ok": True, "engine": ENGINE, "tabs": tabs,
                "detail": f"{len(tabs)} tab(s) open"}

    # -- actions -----------------------------------------------------

    def navigate(self, page: dict, url: str) -> dict:
        handle, failure = self._verify_page(page)
        if failure is not None:
            return failure
        clean = (url or "").strip()
        if not clean:
            return _fail("bad_input", "navigate needs a url", page)
        if not (clean.startswith("http://")
                or clean.startswith("https://")):
            return _fail("bad_input",
                         f"navigate refuses {clean!r} — only http(s) "
                         "URLs are navigated to", page)
        # Settle BEFORE navigating away: the current page may still
        # be loading, and navigating off a half-loaded page would
        # silently abandon its in-flight load.
        failure = self._settle_or_fail(handle, page)
        if failure is not None:
            return failure
        try:
            self._backend.navigate(handle, clean)
        except TimeoutError as exc:
            return _fail("still_loading", str(exc), page)
        except PageGone:
            return _fail("wrong_page", "the page closed during "
                                       "navigation", page)
        failure = self._settle_or_fail(handle, page)
        if failure is not None:
            return failure
        new_page = self._identity(page["tab_id"])
        self._tab_urls[page["tab_id"]] = new_page["url"]
        return {"ok": True, "engine": ENGINE,
                "detail": f"opened {new_page['url']}",
                "page": new_page}

    def _resolve_one(self, handle, selector: str, page: dict,
                     verb: str) -> tuple[dict | None, dict | None]:
        """Resolve a selector to exactly ONE visible element.
        Returns (match, None) or (None, failure dict)."""
        try:
            matches = self._backend.matches(handle, selector)
        except PageGone:
            return None, _fail("wrong_page",
                               "the page closed while searching", page)
        except ElementNotFound as exc:
            return None, _fail("not_found", str(exc), page)
        visible = [m for m in matches if m.get("visible")]
        if not matches:
            return None, _fail(
                "not_found",
                f"nothing matches '{selector}' on this page", page)
        if not visible:
            return None, _fail(
                "not_found",
                f"'{selector}' matches {len(matches)} element(s) but "
                "none is visible — it may be inside a closed menu",
                page)
        if len(visible) > 1:
            return None, _fail(
                "ambiguous",
                f"'{selector}' matches {len(visible)} visible "
                f"elements — I won't guess which one you mean; "
                f"give me a more specific selector",
                page)
        return visible[0], None

    def click(self, page: dict, selector: str) -> dict:
        handle, failure = self._verify_page(page)
        if failure is not None:
            return failure
        failure = self._settle_or_fail(handle, page)
        if failure is not None:
            return failure
        match, failure = self._resolve_one(handle, selector, page, "click")
        if failure is not None:
            return failure
        el = match["handle"]
        try:
            before = self._backend.element_identity(el)
        except PageGone:
            return _fail("wrong_page",
                         "the element is gone — the page changed", page)
        try:
            result = self._backend.click_element(el)
        except PageGone:
            return _fail("wrong_page",
                         "the page closed during the click", page)
        after_page = self._identity(page["tab_id"])
        self._tab_urls[page["tab_id"]] = after_page["url"]
        if not result.get("changed"):
            # No observed effect: Playwright's click() succeeding
            # only proves the event was dispatched — a handler can
            # swallow it, or the element can be dead. Never report
            # that as a success (mirrors _act_on_element).
            return _fail("no_effect",
                         "clicked the element but no navigation, title "
                         "change, or content change was observed after "
                         "the click, so it may not have done anything",
                         after_page)
        try:
            after = self._backend.element_identity(el)
        except PageGone:
            # The element is gone because the click navigated — the
            # page change above IS the effect; report it, naming
            # the new page.
            return {"ok": True, "engine": ENGINE,
                    "detail": "clicked the element; the page changed",
                    "element": before, "page": after_page}
        if after.get("tag") != before.get("tag") or \
                after.get("role") != before.get("role"):
            # Structural identity only: a button whose LABEL
            # changes after the click ("Play" -> "Pause") is a
            # real effect, not a mismatch — the tag + role are
            # what prove it is the same element.
            return _fail("click_mismatch",
                         "the element read back as a different kind "
                         "of control after the click — the click may "
                         "have landed on the wrong element",
                         after_page)
        return {"ok": True, "engine": ENGINE,
                "detail": "clicked the element",
                "element": after, "page": after_page}

    def fill(self, page: dict, selector: str, text: str) -> dict:
        handle, failure = self._verify_page(page)
        if failure is not None:
            return failure
        failure = self._settle_or_fail(handle, page)
        if failure is not None:
            return failure
        match, failure = self._resolve_one(handle, selector, page, "fill")
        if failure is not None:
            return failure
        el = match["handle"]
        # HARD RULE (mirrors _impl_type): never fill a password
        # field, and an inspection failure is NOT "not a password
        # field" — refuse on a guess.
        try:
            field_type = self._backend.field_type(el)
        except Exception as exc:  # noqa: BLE001 — unreadable type
            return _fail("refused_password",
                         "I couldn't determine whether this field is a "
                         f"password field ({exc}) — I won't fill it on "
                         "a guess", page)
        if (field_type or "").strip().lower() == "password":
            return _fail("refused_password",
                         "that's a password field — passwords are yours "
                         "to type, I won't", page)
        try:
            self._backend.fill_element(el, text)
        except PageGone:
            return _fail("wrong_page",
                         "the page closed during the fill", page)
        label = self._backend.field_label(el)
        back = self._backend.field_value(el)
        after_page = self._identity(page["tab_id"])
        self._tab_urls[page["tab_id"]] = after_page["url"]
        if back is None:
            return _fail("fill_unverified",
                         "the field value could not be read back after "
                         "filling — the fill is unverified, never "
                         "claimed as landed",
                         after_page)
        if back != text:
            return _fail("fill_mismatch",
                         f"filled the field but it reads back as "
                         f"'{back[:80]}', not the typed text — the "
                         f"fill did not land as typed",
                         after_page)
        return {"ok": True, "engine": ENGINE,
                "detail": "filled the field; it reads back the typed text",
                "field": {"selector": selector, "label": label,
                          "value": back},
                "page": after_page}

    def read(self, page: dict) -> dict:
        handle, failure = self._verify_page(page)
        if failure is not None:
            return failure
        failure = self._settle_or_fail(handle, page)
        if failure is not None:
            return failure
        try:
            text = self._backend.page_text(handle)
        except PageGone:
            return _fail("wrong_page", "the page closed while reading",
                         page)
        after_page = self._identity(page["tab_id"])
        return {"ok": True, "engine": ENGINE,
                "text": text[:TEXT_CAP], "page": after_page}


# -- module-level default (the driver calls these) -------------------

_default = BrowserAutomation()


def set_automation_backend(backend) -> None:
    """Test seam (clipboard idiom): inject a fake backend for the
    module-level functions. Production code never calls this."""
    global _default
    _default = BrowserAutomation(backend=backend)


def reset_automation_backend() -> None:
    """Restore the module-level default (real backend, lazy)."""
    global _default
    _default = BrowserAutomation()


def launch(*, profile_dir: str | None = None) -> dict:
    return _default.launch(profile_dir=profile_dir)


def close() -> dict:
    return _default.close()


def list_tabs() -> dict:
    return _default.list_tabs()


def navigate(page: dict, url: str) -> dict:
    return _default.navigate(page, url)


def click(page: dict, selector: str) -> dict:
    return _default.click(page, selector)


def fill(page: dict, selector: str, text: str) -> dict:
    return _default.fill(page, selector, text)


def read(page: dict) -> dict:
    return _default.read(page)

"""§7 item 9 — application adapter #1: GOOGLE CHROME (D6 pick #1).

Control profile for Google Chrome on Windows, on top of the
``core/desktop/app_adapters.py`` framework (items 1–4 engines do the
driving; this module only RESOLVES — it never invokes, clicks,
types, or dispatches anything).

Identity (positive evidence or nothing): the foreground must be
``chrome.exe`` (matched case-insensitively) in a top-level
``Chrome_WidgetWin_1`` window. That class string is sourced, not
guessed: Chromium's views widget base registers
``Chrome_WidgetWin_`` + a number, and the main browser window is
``Chrome_WidgetWin_1`` (flyout menus — the ⋮ menu itself,
extension popups — live in ``Chrome_WidgetWin_2`` windows; see
the ``flyout_windows`` quirk). No title substring is named:
Chrome window titles are page titles, and naming one would be
wrong for every other page. A signature mismatch disables the
adapter (generic fallback + honest note), never a forced fit.

Vocabulary: the 12 core Chrome jobs, as exact phrases. Menu
entries name Chrome's ⋮ (three-dot) menu items; action entries
cover jobs with no menu item (toolbar/tab-strip/address-bar
actions). Exact item text is FIELD-VERIFICATION OWED: Chrome
menu text varies by version, and the item-3 engine matches
exactly and refuses on mismatch (fail-closed), so a renamed
menu item degrades to an honest refusal, never a wrong click.
Menu text here is the current Chrome ⋮ menu as documented
(New tab, New window, Bookmarks and lists → Bookmark this tab,
History → History for the full history page, Downloads,
Settings).

Quirks: ``address_bar_focus`` gates "go to address" (typing
without omnibox focus goes to the page, not the address bar);
``tab_strip`` gates "close tab" (a tab closes, not the window);
``flyout_windows`` gates every menu entry (the ⋮ menu is a
flyout window, not the main window). ``profile_windows`` and
``download_shelf`` are documented adapter knowledge (several
windows/profiles share one chrome.exe process; downloads surface
as a bubble, not a page) and gate no entry in this vocabulary.
A quirk that stops working refuses its entries, never a pixel
workaround.

Safe/confirm: read-only navigation and bookmarking are safe.
"close tab" is confirm-gated (a tab may hold unsaved state).
Nothing in this vocabulary touches a password field, a CAPTCHA,
or the secure desktop — registration refuses any such entry,
and the vocabulary contains no search-query, scraping, or
account action.

G2 TERMS (read 2026-11-02, recorded via ``record_terms`` with
the citation in ``CHROME_TERMS_EVIDENCE``):
  (1) Google Terms of Service, policies.google.com/terms
  (effective 2026-07-30): the conduct rules prohibit abuse /
  impersonation / legal-rights violations, reverse-engineering
  to extract trade secrets, and "using automated means to
  access content from any of our services in violation of the
  machine-readable instructions on our web pages (for example,
  robots.txt files that disallow crawling, training, or other
  activities)". This adapter does none of those: it drives the
  user's own local Chrome UI at the user's spoken direction
  through the app's own menus and controls, does not scrape
  service content, does not violate robots.txt, does not
  reverse-engineer Chrome, bundles no vendor code, and holds
  no credentials.
  (2) Chrome DevTools Protocol (Google's own documented
  protocol for instrumenting Chrome): the vendor-published
  automation interface, and the channel the product's existing
  Chrome-136 ruling already uses (localhost:9222 + a
  non-standard user-data-dir). The adapter depends on no
  vendor SDK and adds no new channel.
  (3) Chromium window-class evidence (see Identity above).
  Scope conditions: local UI operation only, user-directed
  actions only, by-design walls stand, destructive leaves
  confirm-gated. VERDICT: CLEARED within that scope.
  Gap disclosed: Chrome's own terms page was not retrieved
  as a separate current primary text in this read (search
  surfaced only the superseded 2008 Chrome ToS); the verdict
  rests on the current Google ToS conduct rules plus CDP's
  published status. If a current Chrome-specific term
  restricting local UI automation surfaces, this verdict
  must be revisited before shipping.
  No real Chrome version is verified from lab evidence
  (the lab has Chromium, which is NOT Chrome — a Chromium
  version must never stand in for a Chrome version), so
  ``verified_app_versions`` is empty and ``shippability()``
  is honestly False until a field version pin lands. That is
  the gate state, not a failure: the adapter + tests land,
  and shipping waits on the field pin.
"""

from __future__ import annotations

APP_ID = "chrome"
ADAPTER_VERSION = "1.0"

CHROME_TERMS_EVIDENCE = (
    "G2 read 2026-11-02: (1) Google Terms of Service, "
    "policies.google.com/terms (effective 2026-07-30): conduct rules "
    "prohibit abuse/impersonation/legal-rights violations, "
    "reverse-engineering to extract trade secrets, and 'using automated "
    "means to access content from any of our services in violation of "
    "the machine-readable instructions on our web pages (for example, "
    "robots.txt files that disallow crawling, training, or other "
    "activities)' — none of which this adapter does: it drives the "
    "user's own local Chrome UI at the user's spoken direction through "
    "the app's own menus and controls, does not scrape service content, "
    "does not violate robots.txt, does not reverse-engineer Chrome, "
    "bundles no vendor code, and holds no credentials; no search-query, "
    "scraping, or account action is in the vocabulary. (2) Chrome "
    "DevTools Protocol (Google's documented protocol for instrumenting "
    "Chrome): the vendor-published automation interface and the channel "
    "the product's existing Chrome-136 ruling already uses "
    "(localhost:9222 + non-standard user-data-dir); the adapter depends "
    "on no vendor SDK and adds no new channel. (3) Chromium "
    "window-class evidence: top-level Chrome browser window class "
    "Chrome_WidgetWin_1 (Chromium views/widget base 'Chrome_WidgetWin_' "
    "plus flyout windows Chrome_WidgetWin_2). Scope: local UI operation "
    "only, user-directed actions only, by-design walls stand, destructive "
    "leaves confirm-gated. Verdict: CLEARED within that scope. Gap: "
    "Chrome's own terms page was not retrieved as a separate current "
    "primary text in this read (search surfaced only the superseded 2008 "
    "Chrome ToS); if a current Chrome-specific term restricting local UI "
    "automation surfaces, this verdict must be revisited before shipping."
)


def chrome_adapter_record():
    """A fresh Chrome adapter record (terms start unread, per the
    framework — callers record terms after registration, never
    assert them at it). Verified app versions are deliberately
    empty: no real Chrome version is pinned from lab evidence."""
    return {
        "app_id": APP_ID,
        "identity_signatures": [
            {"proc": "chrome.exe", "window_class": "Chrome_WidgetWin_1"},
        ],
        "vocabulary": {
            "new tab": {"kind": "menu", "path": ["New tab"],
                        "quirk": "flyout_windows"},
            "new window": {"kind": "menu", "path": ["New window"],
                           "quirk": "flyout_windows"},
            "bookmark this page": {
                "kind": "menu",
                "path": ["Bookmarks and lists", "Bookmark this tab"],
                "quirk": "flyout_windows"},
            "open history": {"kind": "menu",
                             "path": ["History", "History"],
                             "quirk": "flyout_windows"},
            "open downloads": {"kind": "menu", "path": ["Downloads"],
                               "quirk": "flyout_windows"},
            "open settings": {"kind": "menu", "path": ["Settings"],
                              "quirk": "flyout_windows"},
            "go to address": {"kind": "action", "name": "go to address",
                              "quirk": "address_bar_focus"},
            "reload": {"kind": "action", "name": "reload"},
            "go back": {"kind": "action", "name": "go back"},
            "go forward": {"kind": "action", "name": "go forward"},
            "find in page": {"kind": "action", "name": "find in page"},
            "close tab": {"kind": "action", "name": "close tab",
                          "quirk": "tab_strip"},
        },
        "quirks": {
            "address_bar_focus": {
                "description": "the omnibox must be focused before "
                "typing a URL; typing into page content does not "
                "navigate.",
                "working": True},
            "tab_strip": {
                "description": "tabs are per-tab targets on the tab "
                "strip, not the window; closing a tab must not "
                "close the window.",
                "working": True},
            "flyout_windows": {
                "description": "Chrome's flyout menus (the three-dot "
                "menu, extension popups) live in Chrome_WidgetWin_2 "
                "windows, not the main Chrome_WidgetWin_1 window; "
                "menu driving must target the flyout window handle.",
                "working": True},
            "profile_windows": {
                "description": "several Chrome windows, including "
                "different profiles, share one chrome.exe process; "
                "window identity must come from the window handle "
                "plus profile, never the process alone.",
                "working": True},
            "download_shelf": {
                "description": "downloads surface as a bubble/shelf, "
                "not a page; download completion is not a "
                "navigation.",
                "working": True},
        },
        "safe_actions": [
            "menu:New tab",
            "menu:New window",
            "menu:Bookmarks and lists → Bookmark this tab",
            "menu:History → History",
            "menu:Downloads",
            "menu:Settings",
            "action:go to address",
            "action:reload",
            "action:go back",
            "action:go forward",
            "action:find in page",
        ],
        "confirm_actions": [
            "action:close tab",
        ],
        "adapter_version": ADAPTER_VERSION,
        # Deliberately empty: no real Chrome version is verified
        # from lab evidence (Chromium is not Chrome). A field
        # version pin is owed before shipping.
        "verified_app_versions": [],
    }


def register_chrome_adapter():
    """Register the Chrome adapter and record its G2 terms read.
    Returns (register_result, terms_result). Registration starts
    unread per the framework; the terms call is what clears the
    terms half of the gate. Version pinning stays open (field
    work), so shippability() remains False after this call —
    honestly, by design."""
    from . import app_adapters as aa  # Phase 3 S7b: adapter moved into core/desktop/ (intra-package)

    registered = aa.register(chrome_adapter_record())
    if not registered.get("ok"):
        return registered, {"ok": False, "adapter_id": None,
                            "refusal": {"code": "not-registered",
                                        "reason": "registration failed; "
                                        "terms not recorded"}}
    termed = aa.record_terms(APP_ID, terms=CHROME_TERMS_EVIDENCE)
    return registered, termed

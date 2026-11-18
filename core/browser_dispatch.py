"""Browser dispatch layer — split out of core/browser.py (skills split B15).

Move only, no behaviour change: the 10 names DEFINED in browser.py
(the session-control set, the control/read/click/type/select/press
tool + dispatch functions, and register_all) live here verbatim.
browser.py keeps its desktop re-export lists and re-exports these
names, so every importer and dir() view is unchanged.

The dispatch functions resolve their helpers by bare name; those
helpers are imported below from the SAME desktop modules browser.py
re-exported them from, so every name is the identical object.
"""

from .desktop.browser_session import (
    _with_bootstrap,
    _submit,
    _impl_status,
    _impl_attach_cdp,
    _impl_close,
    _need_connection,
    _page_label,
    _active_page,
)
from .desktop.browser_nav import (
    _impl_goto,
    _impl_history,
    _impl_new_tab,
    _impl_close_tab,
    _impl_switch_tab,
    _impl_list_tabs,
    _impl_scroll,
    _impl_wait,
    _impl_screenshot,
)
from .desktop.browser_read import (
    _impl_read_text,
    _impl_read_find,
    _impl_read_links,
    _impl_read_fields,
    _impl_read_elements,
)
from .desktop.browser_click import _impl_click
from .desktop.browser_interact import _impl_type, _impl_select, _impl_press

_SESSION_CONTROL_ACTIONS = frozenset({
    "goto", "back", "forward", "reload", "new_tab", "close_tab",
    "switch_tab", "list_tabs", "scroll", "wait", "screenshot",
})


def browser_control(action: str, url: str = "", tab_index: int = -1,
                    direction: str = "", amount: int = 600, path: str = "",
                    seconds: float = 1.0) -> str:
    """Control the browser: connection, navigation, tabs, scroll, screenshots.

    v5.52 (item 5 extension): the session-requiring actions run
    behind _with_bootstrap — with no controllable session active
    the tool layer attaches to the user's real Chrome first, in
    the same call (v5.70: the own-browser bootstrap was deleted
    with the own-browser launch). Connection actions (status /
    launch / close) are not session-requiring and dispatch exactly
    as before."""
    act = (action or "").strip().lower()
    if act in _SESSION_CONTROL_ACTIONS:
        return _with_bootstrap(
            _browser_control_dispatch, action, url=url,
            tab_index=tab_index, direction=direction, amount=amount,
            path=path, seconds=seconds)
    return _browser_control_dispatch(
        action, url=url, tab_index=tab_index, direction=direction,
        amount=amount, path=path, seconds=seconds)


def _browser_control_dispatch(action: str, url: str = "", tab_index: int = -1,
                              direction: str = "", amount: int = 600,
                              path: str = "", seconds: float = 1.0) -> str:
    act = (action or "").strip().lower()
    if act == "status":
        return _submit(_impl_status)
    if act == "launch":
        return _submit(_impl_attach_cdp, kind="launch")
    if act == "close":
        return _submit(_impl_close)
    if act == "goto":
        return _submit(lambda: _impl_goto(url), kind="nav")
    if act in ("back", "forward", "reload"):
        return _submit(lambda: _impl_history(act), kind="nav")
    if act == "new_tab":
        return _submit(lambda: _impl_new_tab(url), kind="nav")
    if act == "close_tab":
        return _submit(_impl_close_tab)
    if act == "switch_tab":
        return _submit(lambda: _impl_switch_tab(int(tab_index)))
    if act == "list_tabs":
        return _submit(_impl_list_tabs)
    if act == "scroll":
        return _submit(lambda: _impl_scroll(direction, amount))
    if act == "wait":
        return _submit(lambda: _impl_wait(seconds))
    if act == "screenshot":
        return _submit(lambda: _impl_screenshot(path))
    return (
        f"Unknown browser_control action: '{action}'. Valid actions: "
        "status, launch, close, "
        "goto, back, forward, reload, new_tab, close_tab, switch_tab, "
        "list_tabs, scroll, wait, screenshot. (v5.70: your real Chrome "
        "is the only browser — the own-browser and dedicated-profile "
        "launches were deleted.)"
    )


def browser_read(action: str, query: str = "", filter: str = "") -> str:
    """Read the current page: its text, a search within it, links,
    controls, or the numbered element snapshot (filter narrows
    the elements listing to links / buttons / inputs).

    Every read action needs a controllable session, so reads run
    behind the v5.52 auto-bootstrap gate (see browser_control)."""
    act = (action or "").strip().lower()
    if act in ("text", "find", "links", "fields", "elements",
               "title_url"):
        return _with_bootstrap(
            _browser_read_dispatch, action, query=query, filter=filter)
    return _browser_read_dispatch(action, query=query, filter=filter)


def _browser_read_dispatch(action: str, query: str = "",
                           filter: str = "") -> str:
    act = (action or "").strip().lower()
    if act == "text":
        return _submit(_impl_read_text)
    if act == "find":
        return _submit(lambda: _impl_read_find(query))
    if act == "links":
        return _submit(_impl_read_links)
    if act == "fields":
        return _submit(_impl_read_fields)
    if act == "elements":
        return _submit(lambda: _impl_read_elements(query, filter))
    if act == "title_url":
        def _title_url():
            if (msg := _need_connection()) is not None:
                return msg
            return _page_label(_active_page())
        return _submit(_title_url)
    return (
        f"Unknown browser_read action: '{action}'. Valid actions: "
        "text, find, links, fields, elements, title_url."
    )


def browser_click(target: str, by: str = "text", confirm: bool = False,
                  click: str = "click") -> str:
    """Click an element by its visible text, button role+name, label,
    CSS selector, or snapshot ref (by='ref'); or identify-and-click
    whatever lives at a viewport point (by='point', target 'x,y');
    click picks the kind: click, double, right or hover."""
    return _with_bootstrap(
        lambda: _submit(
            lambda: _impl_click(target, by, bool(confirm), click)))


def browser_type(target: str, text: str, by: str = "label",
                 submit: bool = False, confirm: bool = False) -> str:
    """Fill a form field found by label, placeholder, or CSS selector.

    submit=true presses Enter afterwards — and that Enter is
    gated (audit fix Batch 1 WEB-02): a payment/destructive
    form, or a form that cannot be inspected, asks for
    confirmation first (confirm=true is the no-handler
    fallback; with a handler registered the human decides in
    the popup), and an uninspectable form on a
    payment/destructive page is refused outright."""
    return _with_bootstrap(
        lambda: _submit(
            lambda: _impl_type(target, text, by, bool(submit),
                               bool(confirm))))


def browser_select(target: str, value: str, by: str = "label") -> str:
    """Choose an option in a <select> dropdown found by label or selector."""
    return _with_bootstrap(
        lambda: _submit(lambda: _impl_select(target, value, by)))


def browser_press(key: str, confirm: bool = False) -> str:
    """Press a key (or combo like ctrl+a) on the current page.

    Enter (and Ctrl/Cmd+Enter) is a submit, not mere navigation
    (audit fix Batch 1 WEB-02): it runs the same form-intent
    gate as browser_type submit — dangerous or uninspectable
    forms confirm first, uninspectable forms on
    payment/destructive pages are refused. confirm=true is the
    no-handler fallback; with a handler registered the human
    decides in the popup. Other keys are unaffected."""
    return _with_bootstrap(
        lambda: _submit(lambda: _impl_press(key, bool(confirm))))


# ---------------------------------------------------------------------------
# Registration — called by core/tools_handlers_pack_hands.py (_register_browser_tools; Phase 5 docs-only rename, wiring unchanged)
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register the Web pack's 6 browser tools on a ToolRegistry-like object."""
    registry.register(
        name="browser_control",
        description=(
            "Drive a REAL browser (never to play music — that "
            "is play_music's job) — the user's real Chrome, the "
            "ONLY browser VYUK drives (v5.70): there is no "
            "own-browser window and no dedicated-profile Chrome any "
            "more. Search YouTube for a video, open websites, search "
            "within the page, click, type and read (YouTube, news, "
            "shops). If no browser session is open, tap 'Connect my browser' "
            "(or use Settings → Browser control → Enable browser control) "
            "to set it up in one tap — the "
            "navigation/read/click/type actions try to attach to your "
            "Chrome only once control is enabled and Chrome is running "
            "with the connection port open; it never works on a plain "
            "open_url window, which VYUK can't see or drive. Actions: status (connection "
            "mode, active tab, tab count); launch (attach to the user's "
            "REAL Chrome at the configured connection port (9222 by "
            "default) — EXPLICIT OPT-IN ONLY, off "
            "by default (browser_allow_real_chrome_attach); with it on, "
            "fails with instructions if Chrome wasn't started with "
            "--remote-debugging-port, and there is nothing else to "
            "fall back to); "
            "close "
            "(detach — your Chrome is never killed and your tabs are "
            "never closed; detaching only lets go of the controls); "
            "goto (url); back; "
            "forward; reload; new_tab (optional url); close_tab; "
            "switch_tab (tab_index); list_tabs; scroll (direction "
            "up/down/top/bottom, amount px); wait (seconds, max 10); "
            "screenshot (PNG to path, or ~/Pictures/nova_web_<time>.png). "
            "CAPTCHAs and bot checks will block VYUK — the user takes "
            "those clicks. VYUK never types passwords (see browser_type). "
            "Playing music or a song is play_music's job, never this "
            "tool's — do not drive YouTube or Spotify pages to start "
            "music."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status", "launch", "close", "goto", "back",
                        "forward", "reload", "new_tab", "close_tab",
                        "switch_tab", "list_tabs", "scroll", "wait",
                        "screenshot",
                    ],
                    "description": "What the browser should do",
                },
                "url": {"type": "string", "description": "For goto / new_tab: the address to open (https:// is added if missing)"},
                "tab_index": {"type": "integer", "description": "For switch_tab: which tab (see list_tabs)"},
                "direction": {"type": "string", "description": "For scroll: up, down, top or bottom"},
                "amount": {"type": "integer", "description": "For scroll: pixels (default 600)"},
                "path": {"type": "string", "description": "For screenshot: where to save the PNG (default ~/Pictures)"},
                "seconds": {"type": "number", "description": "For wait: seconds to pause (capped at 10)"},
            },
            "required": ["action"],
        },
        handler=browser_control,
    )

    registry.register(
        name="browser_read",
        description=(
            "Read the current web page structurally — this is how VYUK "
            "sees a page. Actions: text (the page's visible text, "
            "truncated — use it to understand what's on the page); find "
            "(count + context snippets for a query string); links (every "
            "link's text and destination); fields (every button, input, "
            "select and textarea with its label/placeholder/name); "
            "elements (THE way to see what can be clicked: every "
            "VISIBLE interactive element on the page — links, buttons, "
            "inputs, checkboxes, menus, elements inside iframes and "
            "inside OPEN shadow DOM (web components) too — numbered "
            "e1, e2, …, with its kind, name and state; pass query to "
            "filter by name or kind, or pass filter to list "
            "only links, only buttons or only inputs. Click "
            "one exactly with "
            "browser_click by='ref'. Elements inside CLOSED shadow "
            "roots are invisible to every tool, by the web "
            "platform's design — no snapshot can list them); "
            "title_url (current page title and address)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["text", "find", "links", "fields",
                             "elements", "title_url"],
                    "description": "What to read from the page",
                },
                "query": {"type": "string", "description": "For find: the text to search for on the page. For elements: only show elements whose name or kind contains this text"},
                "filter": {"type": "string", "enum": ["all", "links", "buttons", "inputs"], "description": "For elements: list only one kind of element (default all)"},
            },
            "required": ["action"],
        },
        handler=browser_read,
    )

    registry.register(
        name="browser_click",
        description=(
            "Click something on the current page BY MEANING, not pixels: "
            "by='text' (an element containing that visible text), "
            "by='role_button' (a button with that name), by='label' (the "
            "control with that label), by='selector' (raw CSS), or "
            "by='ref' (an e-number from browser_read action 'elements' — "
            "the exact element that was listed; prefer this whenever a "
            "listing exists). by='point' (target 'x,y' in viewport "
            "pixels) is the LAST resort, for drawn/canvas content the "
            "other modes can't name: VYUK identifies what lives at "
            "that point and, when an interactive element is there, "
            "clicks THAT element through the same gate and reporting; "
            "only when nothing interactive lives there does it make "
            "a raw coordinate click, and the result says so plainly — "
            "and a raw coordinate click is unclassifiable, so it "
            "asks for confirmation first (audit fix Batch 1 WEB-01), "
            "never silently clicking a point no element could be "
            "read at. "
            "click picks the kind: 'click' (default), "
            "'double', 'right' (context menu), 'hover' (reveal hover "
            "menus without clicking; hover is never gated). NO-GUESS "
            "RULES: matches are narrowed to the visible ones — if "
            "exactly one is visible it is clicked (and the result "
            "says so); if SEVERAL are visible, NOTHING is clicked and "
            "the visible candidates come back numbered so you can "
            "re-click by ref; if matches exist but none are visible "
            "(e.g. inside a closed menu), that is reported instead of "
            "clicking blind. The result always reports what was "
            "actually clicked and what changed (new page, dialog or "
            "menu opened, or nothing). TRUST GATE: the gate judges "
            "the RESOLVED element's real text and attributes (not "
            "just your target string — selectors and refs are "
            "scrutinised too): clicks that look like payment or "
            "destruction (pay, purchase, checkout, place your order, "
            "buy, order now, delete, remove, erase, send, confirm…) "
            "are REFUSED unless confirmed. When VYUK's confirmation "
            "popup is active, the USER approves or denies it there "
            "and confirm=true alone is not enough; otherwise only "
            "pass confirm=true when the user explicitly confirmed "
            "that exact click. CAPTCHAs will block VYUK; the user "
            "takes those clicks."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "The text, button name, label or CSS selector to click — the e-number (e.g. 'e7') when by='ref', or 'x,y' viewport pixels (e.g. '640,480') when by='point'"},
                "by": {
                    "type": "string",
                    "enum": ["text", "role_button", "label", "selector", "ref", "point"],
                    "description": "How to find it (default text)",
                },
                "confirm": {"type": "boolean", "description": "Only true when the user explicitly confirmed a payment/destructive click"},
                "click": {
                    "type": "string",
                    "enum": ["click", "double", "right", "hover"],
                    "description": "The kind of click (default click). Use hover to open hover menus, right for context menus, double for double-click targets",
                },
            },
            "required": ["target"],
        },
        handler=browser_click,
    )

    registry.register(
        name="browser_type",
        description=(
            "Type into a form field on the current page: find it "
            "by='label' (its label), by='placeholder' (its placeholder "
            "text) or by='selector' (raw CSS), clear it, and fill it "
            "with the given text. submit=true presses Enter afterwards — "
            "and that Enter is gated (audit fix Batch 1 WEB-02): a "
            "payment/destructive form, or a form that cannot be "
            "inspected, asks for confirmation first; an uninspectable "
            "form on a payment/destructive page is refused outright. "
            "HARD RULE: if the field is a password field, VYUK refuses — "
            "passwords, PINs and one-time codes are the user's to type, "
            "always."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "The field's label, placeholder or CSS selector"},
                "text": {"type": "string", "description": "What to type into the field"},
                "by": {
                    "type": "string",
                    "enum": ["label", "placeholder", "selector"],
                    "description": "How to find the field (default label)",
                },
                "submit": {"type": "boolean", "description": "Press Enter after filling (default false)"},
                "confirm": {"type": "boolean", "description": "Confirm a gated submit (payment/destructive or uninspectable form); with a confirmation popup registered, the human decides instead (default false)"},
            },
            "required": ["target", "text"],
        },
        handler=browser_type,
    )

    registry.register(
        name="browser_select",
        description=(
            "Choose an option in a <select> dropdown on the current "
            "page: find the dropdown by='label' or by='selector', then "
            "select the option whose value or visible text matches "
            "'value'. Reports what was actually chosen. Its browser "
            "family: read the page with browser_read, click with "
            "browser_click, type with browser_type, press keys with "
            "browser_press."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "The dropdown's label or CSS selector"},
                "value": {"type": "string", "description": "The option's value or visible text to choose"},
                "by": {
                    "type": "string",
                    "enum": ["label", "selector"],
                    "description": "How to find the dropdown (default label)",
                },
            },
            "required": ["target", "value"],
        },
        handler=browser_select,
    )

    registry.register(
        name="browser_press",
        description=(
            "Press a key on the current page: Enter, Tab, Escape, "
            "ArrowDown, PageDown, or a combo like ctrl+a / ctrl+l. "
            "Useful for submitting, dismissing dialogs, and keyboard-"
            "driven sites. Enter (and Ctrl/Cmd+Enter) is a submit, "
            "not mere navigation (audit fix Batch 1 WEB-02): it runs "
            "the same form-intent gate as browser_type submit — "
            "dangerous or uninspectable forms confirm first, "
            "uninspectable forms on payment/destructive pages are "
            "refused. HARD RULE (same as browser_type): if a "
            "password field is focused, text-producing presses "
            "(single characters, Shift+character, Space, Ctrl+V) are "
            "refused — passwords are the user's to type; navigation "
            "keys still work."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The key or combo to press (e.g. 'Enter', 'ctrl+a')"},
                "confirm": {"type": "boolean", "description": "Confirm a gated Enter submit (payment/destructive or uninspectable form); with a confirmation popup registered, the human decides instead (default false)"},
            },
            "required": ["key"],
        },
        handler=browser_press,
    )

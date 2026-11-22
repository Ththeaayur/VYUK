"""browser_gates concern extracted from core/browser.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/browser."""

import re


_DANGEROUS_CLICK_RE = re.compile(
    r"\bpay\b|purchase|checkout|check out|place (your )?order|order now|"
    r"\bbuy\b|send money|\bsend\b|donate|delete|remove|erase|"
    r"\bconfirm\b|unsubscribe|complete (your )?(purchase|order|payment)|"
    r"submit (payment|order)|\bupload\b|\battach\b|\bshare\b|\bsave\b|"
    r"\bsubscribe\b|cancel ((my|the|this|an?) )?(account|subscription|order|membership|booking)|"
    r"close (your )?account|\bdeactivate\b|\bagree\b|\baccept\b|\bbook\b|"
    r"\breserve\b|\bpublish\b|\btransfer\b|\bwithdraw\b|sign ?up|log ?in|sign ?in|"
    r"post (an? )?(comment|reply|message|update)",
    re.IGNORECASE,
)


_MATCH_SCAN_CAP = 20     # matches visibility-scanned per click


def _dangerous_click_verdict(target: str, identity: str,
                             model_confirmed: bool) -> str | None:
    """Gate a dangerous click. None = proceed; otherwise the refusal.

    When the GUI has registered a human confirmation handler
    (core/confirm_gate.py), the HUMAN decides — the model's
    confirm=true alone is no longer sufficient. With no handler registered (headless runs and tests ONLY — the
    shipped product always runs with the GUI handler present; headless
    is lab/dev only), the model's confirm flag remains the fallback as
    the documented headless-only rule (accepted 2026-11-03 under the
    code-rulings delegation). Note the funnel gate differs: it refuses
    not_confirmed without a handler; the old "exactly as the executor
    gate" comparison is stale post-refactor and is removed.
    """
    try:
        from .. import confirm_gate  # Phase 3 D2a: confirm_gate stays flat in core/
    except Exception:  # noqa: BLE001 — a broken import means no handler
        confirm_gate = None
    if confirm_gate is not None and confirm_gate.has_handler():
        approved = confirm_gate.request_confirmation(
            "browser_click",
            {"target": target, "element": identity[:120]},
        )
        if approved:
            return None
        return (
            "Refused: that click looks like a payment or destructive "
            "action, and it was not approved in VYUK's confirmation "
            "popup — so I didn't click it."
        )
    if model_confirmed:
        return None
    return (
        f"Refused: '{target}' looks like a payment or destructive action. "
        "I never click those on a guess — if the user really wants this "
        "exact click, they must confirm it (pass confirm=true)."
    )


_DESTRUCTIVE_SUBMIT_RE = re.compile(
    r"\bpay\b|purchase|checkout|check out|place (your )?order|order now|"
    r"\bbuy\b|send money|donate|delete|remove|erase|destroy|"
    r"unsubscribe|cancel ((my|the|this|an?) )?(account|subscription|order|membership|booking)|"
    r"submit (payment|order)|\bupload\b|\battach\b|\bshare\b|\bsave\b|"
    r"\bsubscribe\b|close (your )?account|\bdeactivate\b|\bagree\b|"
    r"\baccept\b|\bbook\b|\breserve\b|\bpublish\b|\btransfer\b|\bwithdraw\b|"
    r"sign ?up|log ?in|sign ?in|post (an? )?(comment|reply|message|update)",
    re.IGNORECASE,
)


_ELEMENT_KIND_JS = (
    "/* VYUK-ELEMENT-KIND */ el => { try { "
    "if (!el) return {file: false, submit: false}; "
    "const tag = (el.tagName || '').toUpperCase(); "
    "const type = (el.getAttribute('type') || '').toLowerCase(); "
    "let file = tag === 'INPUT' && type === 'file'; "
    "if (!file && tag === 'LABEL') { "
    "const forId = el.getAttribute('for') || ''; "
    "const target = forId ? document.getElementById(forId) : null; "
    "const wrapped = el.querySelector('input[type=file]'); "
    "file = !!(wrapped || (target && target.tagName === 'INPUT' "
    "&& (target.getAttribute('type') || '').toLowerCase() === 'file')); } "
    "const submit = (tag === 'BUTTON' && (type === 'submit' || type === '')) "
    "|| (tag === 'INPUT' && (type === 'submit' || type === 'button')); "
    "return {file: !!file, submit: !!submit}; } "
    "catch (e) { return {file: false, submit: false}; } }"
)


def _element_kind(loc) -> dict:
    """The resolved element's kind: {file, submit}.

    file = the element IS an input[type=file], or a label for one
    (``for`` pointing at one, or wrapping one) — the attach step
    of an upload (L-29). submit = the element is a submit control
    (a button with no type, which defaults to submit inside a
    form, a button[type=submit], or an input[type=submit|button]).

    A probe failure reads as neither: the probe only ADDS the
    file-input and submit-control shapes — the regex gate and
    the form-intent gate still judge the dangerous shapes, and
    the upload flow's commit step (the send click) is gated by
    the regex regardless, so a failed probe can never let an
    upload complete ungated.
    """
    try:
        kind = loc.evaluate(_ELEMENT_KIND_JS)
    except Exception:  # noqa: BLE001 — a failed probe is neither
        return {"file": False, "submit": False}
    if not isinstance(kind, dict):
        return {"file": False, "submit": False}
    return {"file": bool(kind.get("file")),
            "submit": bool(kind.get("submit"))}


def _file_input_verdict(target: str, identity: str,
                        model_confirmed: bool) -> str | None:
    """Gate a file-input click (L-29: the attach step of an
    upload — the file chooser hands a local file to the page).
    The _dangerous_click_verdict shape with upload wording: the
    HUMAN decides when a handler is present; the model's confirm
    flag is only the no-handler headless fallback.
    None = proceed; otherwise the refusal.
    """
    try:
        from .. import confirm_gate  # Phase 3 D2a: confirm_gate stays flat in core/
    except Exception:  # noqa: BLE001 — a broken import means no handler
        confirm_gate = None
    if confirm_gate is not None and confirm_gate.has_handler():
        approved = confirm_gate.request_confirmation(
            "browser_click",
            {"target": target, "element": identity[:120],
             "upload": True},
        )
        if approved:
            return None
        return (
            "Refused: that click attaches a file to the page, and it "
            "was not approved in VYUK's confirmation popup — so I "
            "didn't click it."
        )
    if model_confirmed:
        return None
    return (
        f"Refused: '{target}' attaches a file to the page. I never "
        "attach files on a guess — if the user really wants this "
        "exact click, they must confirm it (pass confirm=true)."
    )


_FORM_INTENT_JS = (
    "el => { try { const form = (el && el.form) ? el.form : "
    "(el ? el.closest('form') : null); "
    "if (!form) return {found: false, has_form: false}; "
    "const btns = Array.from(form.querySelectorAll('button, "
    "input[type=submit], input[type=button]')).map(b => "
    "(b.innerText || b.value || b.getAttribute('aria-label') "
    "|| '')).join(' '); "
    "return {found: true, has_form: true, "
    "action: form.getAttribute('action') || '', buttons: btns}; } "
    "catch (e) { return {found: false, has_form: null}; } }"
)


_FOCUSED_FORM_INTENT_JS = (
    "() => { try { const el = document.activeElement; "
    "if (!el) return {found: false, has_form: false}; "
    "const form = el.form ? el.form : el.closest('form'); "
    "const elText = (el.innerText || el.value || "
    "el.getAttribute('aria-label') || ''); "
    "if (!form) return {found: false, has_form: false, "
    "el_text: elText}; "
    "const btns = Array.from(form.querySelectorAll('button, "
    "input[type=submit], input[type=button]')).map(b => "
    "(b.innerText || b.value || b.getAttribute('aria-label') "
    "|| '')).join(' '); "
    "return {found: true, has_form: true, "
    "action: form.getAttribute('action') || '', buttons: btns, "
    "el_text: elText}; } "
    "catch (e) { return {found: false, has_form: null}; } }"
)


def _is_submit_key(key: str) -> bool:
    """True when a browser_press key can submit a form: Enter
    itself, or an Enter combo (Ctrl+Enter / Cmd+Enter, the
    chat/compose submit chord). Tab, Escape, arrows and text
    keys are never submit keys and keep their current paths."""
    parts = [p.strip().lower() for p in (key or "").split("+")
             if p.strip()]
    if not parts:
        return False
    return parts[-1] in ("enter", "return", "numpadenter")


def _dangerous_submit_verdict(tool_name: str, target: str,
                              identity: str,
                              model_confirmed: bool) -> str | None:
    """Gate a dangerous submit — the _dangerous_click_verdict
    shape, with submit wording and the calling tool's name.
    None = proceed; otherwise the refusal. The model's confirm flag alone
    is never approval: with a handler registered the HUMAN decides in
    the popup; the model's confirm flag is only the no-handler fallback
    (headless-only), exactly as for clicks."""
    try:
        from .. import confirm_gate  # Phase 3 D2a: confirm_gate stays flat in core/
    except Exception:  # noqa: BLE001 — a broken import means no handler
        confirm_gate = None
    if confirm_gate is not None and confirm_gate.has_handler():
        approved = confirm_gate.request_confirmation(
            tool_name,
            {"target": target, "element": identity[:120]},
        )
        if approved:
            return None
        return (
            "Refused: submitting that form looks like a payment or "
            "destructive action, and it was not approved in VYUK's "
            "confirmation popup — so I didn't submit it."
        )
    if model_confirmed:
        return None
    return (
        f"Refused: submitting '{target}' looks like a payment or "
        "destructive action. I never submit those on a guess — if "
        "the user really wants this exact submit, they must "
        "confirm it (pass confirm=true)."
    )


def _uninspectable_submit_verdict(tool_name: str, target: str,
                                  model_confirmed: bool) -> str | None:
    """Confirm-first for a submit whose form could not be
    inspected (the DECIDED WEB-02 fork: the human decides, so
    benign search submits on unreadable pages keep working).
    None = proceed; otherwise the refusal, which always says
    the form could not be inspected."""
    try:
        from .. import confirm_gate  # Phase 3 D2a: confirm_gate stays flat in core/
    except Exception:  # noqa: BLE001 — a broken import means no handler
        confirm_gate = None
    if confirm_gate is not None and confirm_gate.has_handler():
        approved = confirm_gate.request_confirmation(
            tool_name,
            {"target": target, "uninspectable": True},
        )
        if approved:
            return None
        return (
            "Refused: the form could not be inspected, and the "
            "submit was not approved in VYUK's confirmation popup "
            "— so I didn't submit it."
        )
    if model_confirmed:
        return None
    return (
        f"Refused: the form behind '{target}' could not be "
        "inspected, so I can't tell whether submitting it is "
        "safe — if the user really wants this exact submit, they "
        "must confirm it (pass confirm=true)."
    )


def _raw_point_verdict(target_desc: str,
                       model_confirmed: bool) -> str | None:
    """Gate a raw-coordinate click (WEB-01): there is no element
    whose identity could be read, so "no DOM element found" is
    never evidence of safety — the click is unclassifiable and
    goes confirm-first, in the same shape as the dangerous-click
    verdict (human popup when a handler is registered, the
    model's confirm flag only as the no-handler fallback (headless-only)).
    None = proceed; otherwise the refusal. Hover never reaches
    here (a hover cannot pay, submit or delete)."""
    try:
        from .. import confirm_gate  # Phase 3 D2a: confirm_gate stays flat in core/
    except Exception:  # noqa: BLE001 — a broken import means no handler
        confirm_gate = None
    if confirm_gate is not None and confirm_gate.has_handler():
        approved = confirm_gate.request_confirmation(
            "browser_click",
            {"target": target_desc, "uninspectable": True},
        )
        if approved:
            return None
        return (
            "Refused: that raw coordinate click could not be "
            "classified — no element could be read at the point — "
            "and it was not approved in VYUK's confirmation popup "
            "— so I didn't click it."
        )
    if model_confirmed:
        return None
    return (
        f"Refused: '{target_desc}' could not be classified — no "
        "element could be read at that point, so I can't tell "
        "whether it pays, submits or deletes something. I never "
        "click unclassifiable points on a guess — if the user "
        "really wants this exact click, they must confirm it "
        "(pass confirm=true)."
    )


def _classify_submit_intent(page, loc=None, focused: bool = False,
                            submit_requested: bool = False):
    """Classify an Enter submit before it is dispatched.

    Returns (kind, detail, page_signals) with kind one of:
    * "dangerous" — the owning form's submit-button text, form
      action, page URL or page title matches _DANGEROUS_CLICK_RE.
    * "benign" — a form was inspected and matches nothing
      dangerous, or (press only) the focused element owns no
      form, so there is nothing for Enter to submit.
    * "uninspectable" — the probe raised, returned nothing
      usable, or (type only) the field owns no form: an
      explicitly requested submit whose mechanism cannot be
      read is not benign, it is unknown.
    page_signals is "url title" for the destructive-signal
    refuse fork. Never raises: a probe failure IS the
    uninspectable outcome, not an error to propagate.
    """
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    try:
        title = page.title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    page_signals = f"{url} {title}".strip()
    try:
        if focused:
            raw = page.evaluate(_FOCUSED_FORM_INTENT_JS)
        elif loc is not None:
            raw = loc.evaluate(_FORM_INTENT_JS)
        else:
            raw = None
    except Exception:  # noqa: BLE001 — probe failure = uninspectable
        return "uninspectable", "the form probe failed", page_signals
    if not isinstance(raw, dict) or not raw.get("found"):
        if isinstance(raw, dict) and raw.get("has_form") is False \
                and not submit_requested:
            return "benign", "no owning form", page_signals
        return "uninspectable", "the form could not be read", page_signals
    haystack = (" ".join([str(raw.get("buttons") or ""),
                          str(raw.get("action") or ""),
                          str(raw.get("el_text") or ""),
                          page_signals]))
    if _DANGEROUS_CLICK_RE.search(haystack):
        detail = (str(raw.get("buttons") or "").strip()
                  or str(raw.get("action") or "").strip()
                  or page_signals)
        return "dangerous", detail[:120], page_signals
    return "benign", "inspected form", page_signals


def _submit_gate(page, tool_name: str, target_desc: str,
                 model_confirmed: bool, loc=None,
                 focused: bool = False,
                 submit_requested: bool = False) -> str | None:
    """The WEB-02 gate, shared by browser_type submit and
    browser_press Enter. None = dispatch the Enter; otherwise
    the refusal (and the caller must NOT dispatch).

    DECIDED fork (coordinator decision, owner-vetoable):
    dangerous intent -> confirm-first via the submit verdict;
    uninspectable intent -> confirm-first, EXCEPT uninspectable
    + payment/destructive page signals (_DESTRUCTIVE_SUBMIT_RE
    on URL/title) -> REFUSE outright, no popup, no flag
    override. Benign intent dispatches unchanged.
    """
    kind, detail, page_signals = _classify_submit_intent(
        page, loc=loc, focused=focused,
        submit_requested=submit_requested)
    if kind == "benign":
        return None
    if kind == "dangerous":
        return _dangerous_submit_verdict(
            tool_name, target_desc, detail, model_confirmed)
    if _DESTRUCTIVE_SUBMIT_RE.search(page_signals):
        return (
            "Refused: the form could not be inspected AND this "
            f"page carries payment/destructive signals "
            f"({page_signals[:120]}) — submitting blind here could "
            "pay, send or delete something, so I didn't submit it. "
            "Inspect the page first (browser_read) and confirm the "
            "exact submit you want."
        )
    return _uninspectable_submit_verdict(
        tool_name, target_desc, model_confirmed)


def _loc_visible(loc) -> bool:
    """Best-effort visibility probe for one resolved element.

    Playwright's is_visible() answers immediately (no waiting). Any
    failure — including a locator that cannot be probed at all —
    reads as VISIBLE: the pre-v5.6 behaviour was to act on the first
    match, and an element whose visibility cannot be proven must
    degrade to that, never to a silent skip."""
    try:
        return bool(loc.is_visible())
    except Exception:  # noqa: BLE001
        return True


def _narrow_visible(base, count: int):
    """Split a locator's matches into (visible locators, scanned,
    stopped_early). The scan is capped at _MATCH_SCAN_CAP matches:
    past that VYUK says it stopped early rather than guessing."""
    visible: list = []
    scanned = 0
    for i in range(min(count, _MATCH_SCAN_CAP)):
        try:
            loc = base.first if i == 0 else base.nth(i)
        except Exception:  # noqa: BLE001 — cannot enumerate further
            break
        scanned += 1
        if _loc_visible(loc):
            visible.append(loc)
    return visible, scanned, count > scanned


_LOGIN_WALL_JS = (
    "() => { const els = document.querySelectorAll("
    "'input[type=\"password\"]'); for (const e of els) { "
    "if (e.offsetParent !== null || e.getClientRects().length) "
    "return true; } return false; }"
)


def _login_wall_result(page) -> str | None:
    """The needs-user-login terminal result when *page* shows
    a login form, else None. Never raises; detection only."""
    try:
        walled = bool(page.evaluate(_LOGIN_WALL_JS))
    except Exception:  # noqa: BLE001 — no verdict, no wall claim
        return None
    if not walled:
        return None
    site = ""
    try:
        from urllib.parse import urlparse

        host = (urlparse(page.url or "").hostname or "")
        host = host.removeprefix("www.").removeprefix("open.")
        site = host.split(".")[0].capitalize() if host else ""
    except Exception:  # noqa: BLE001
        site = ""
    if not site:
        site = "This site"
    return (f"needs-user-login: {site} needs you to sign in "
            "once in my browser — after that I can drive it.")

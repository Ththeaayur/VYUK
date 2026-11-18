"""One registry tool for click-anywhere (Batch 1, Plan 9 §1.5-§1.7,
§2, §4, §6): `click_anywhere`.

One tool, one policy. The click ladder (§1.6) is DOM box > UIA
name > mark > OCR text > raw coordinates (confirm-first) > refuse.
Routes (§1.7): extension primary, CDP attach/launch fallback,
sight+click fallback; desktop apps use the desktop route (§1.5) as
their PRIMARY, not a fallback. A route death NEVER silently falls
through mid-goal — it pauses the goal (M-29) with the route-death
reason and what he must do.

The picker's declared action class is a NAMED field (`action_class`
in the args), never inferred at the gate. Classification is by
element role/action, not by rung.

Safety (§4): D-17 messaging guard (domain + composer-context test),
D-18 always-ask (consequential clicks), scope guard (allowed sites
+ denied roles, per-frame denylist, login-wall recogniser, captcha),
password rule (M-38), game deny-list (M-37), taint tiers (D-20 —
the tiering lives in core/funnel/tools_gate_tier.py).

Playbook opt-out (M-24): the registry record carries
``playbook_opt_out: True``; run_goal's playbook-match step checks
it before any replayed step runs, and the lesson filter
(:func:`lesson_step_excluded`, :func:`strip_lesson_args`) excludes
coordinate shapes, selector strings, and this tool's args entirely.

Scoping (M-25): :func:`click_anywhere_goal_flags` reports the
strict-completion / pause-on-denial flags a goal must run with
when its plan includes click_anywhere steps; run_goal latches
them from the plan and from the first picked click_anywhere step.

Own code throughout, nothing adopted. The browser conversion /
host / channel / extension / CDP bypass / denylist live in
``core/browser/`` (another worker owns them) and are only IMPORTED
from here, never reimplemented: the CDP leg calls the other
worker's ``_cdp_handshake_toggle`` in
``core/desktop/browser_cdp.py`` (takes the debug port); the
DevTools delta check delegates to the conversion module's
``check_devtools_delta`` (``core/browser/viewport_convert``) for
delta-pair inputs and compares other measured-chrome terms
generically; the per-frame denylist check is the other worker's
``origin_denied`` (``core/browser/denylist.py``).

Licence: own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).
"""

from __future__ import annotations

import re
import time

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

ACTIONS = ("observe", "click", "type", "press", "navigate", "read")
BY_VALUES = ("dom", "uia_name", "mark", "ocr", "point", "selector")
ACTION_CLASSES = ("navigation", "read-only", "consequential")
BROWSERS = ("chrome", "edge", "brave")
ROUTES = ("extension", "cdp", "sight", "desktop")

# The click ladder (§1.6), highest rung first. Classification is by
# element role/action, not by rung.
LADDER_RUNGS = ("dom_box", "uia_name", "mark", "ocr", "raw")

# Raw coordinates announce themselves out loud before asking (§1.6).
RAW_COORDINATE_ANNOUNCE = "raw coordinate"

# Route-death pause (M-29): the fallback initiator is the user.
ROUTE_DEATH_WHAT_TO_DO = (
    "re-enable the extension, restart the browser, or re-open the "
    "window, then say 'resume' — the goal waits paused, it never "
    "silently starts a new sight-based goal.")

# Tools that never replay from a playbook lesson (M-24). The
# registry record is the authority; this set is the fail-safe when
# the registry itself cannot be read.
_PLAYBOOK_OPT_OUT_TOOLS = frozenset({"click_anywhere"})

# The rename-symbol refusal, verbatim (Plan 9 §6.2, M-48).
RENAME_SYMBOL_REFUSAL = (
    "I can rename the text in this file, but a symbol rename touches "
    "every file that uses it and I can't verify that — ask me to "
    "rename the text in one file, or wait for rename-symbol support")

# Workspace-swap warning (N-22): carried in the plan-approval
# dialog's covered-steps list for `code -r` steps, before the
# first `code` call.
CODE_WORKSPACE_SWAP_WARNING = (
    "this will replace the current workspace in that window")

_TOOL_DESCRIPTION = (
    "Click, type, press, navigate, read, or observe anywhere — in a "
    "browser (extension, then CDP, then sight+click) or in a desktop "
    "app (UIA + sight+click as the primary route). The picker declares "
    "the action class (navigation, read-only, or consequential) as a "
    "named field; consequential clicks (submit, pay, send, delete, "
    "download, logout, and any state change such as Like, Follow, "
    "Save, or Star) always ask first. Clicks need the element's role "
    "and accessible name or text. A named route that dies pauses the "
    "goal — the tool never silently falls through to another route "
    "mid-goal; when no route is named, the first live route in the "
    "browser order above is used. "
    "Messaging sends are refused (D-17); passwords are never typed.")

_PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": ("observe: render the foreground window + "
                            "focus and return the observation. click / "
                            "type / press: act on the target through "
                            "the click ladder. navigate: go to the "
                            "target URL. read: read the target's text."),
        },
        "target": {
            "type": "string",
            "description": ("What to act on: an element name, a mark "
                            "number, OCR text, a URL for navigate, or "
                            "a point as 'x,y' for the raw-coordinates "
                            "rung."),
        },
        "by": {
            "type": "string",
            "enum": list(BY_VALUES),
            "description": ("How the target is located: dom (DOM box), "
                            "uia_name (UIA control name), mark (grounding "
                            "mark number), ocr (OCR text), point (raw "
                            "screen coordinates, confirm-first), selector "
                            "(CSS/DOM selector)."),
        },
        "action_class": {
            "type": "string",
            "enum": list(ACTION_CLASSES),
            "description": ("The picker's declared action class — a "
                            "named field, never inferred at the gate: "
                            "navigation, read-only, or consequential. "
                            "Absent or unknown tiers Tier 2 (fail-safe)."),
        },
        "role": {
            "type": "string",
            "description": ("The element's role (button, link, textbox, "
                            "...). Required for click actions, carried "
                            "for the record and for future checks."),
        },
        "name": {
            "type": "string",
            "description": ("The element's accessible name or text. "
                            "REQUIRED for click actions — the gate-side "
                            "backstop refuses coverage on a D-18 word "
                            "match in it regardless of the declared class."),
        },
        "text": {
            "type": "string",
            "description": ("Text to type (type action), or the key to "
                            "press (press action), or extra element text "
                            "for the backstop check."),
        },
        "browser": {
            "type": "string",
            "enum": list(BROWSERS),
            "description": "Which browser the browser routes act on.",
        },
        "route": {
            "type": "string",
            "enum": list(ROUTES),
            "description": ("Which route to use: extension (primary), "
                            "cdp (fallback), sight (fallback), desktop "
                            "(primary for desktop apps). Omit it to use "
                            "the first live route in that order — the "
                            "extension route is dead until the browser "
                            "add-on lands, so a named extension route "
                            "pauses while an omitted one falls back to "
                            "CDP, then sight."),
        },
        "confirm": {
            "type": "boolean",
            "description": ("Confirm a raw-coordinate click (by='point' "
                            "on a sight/desktop live route): the first "
                            "call returns the 'raw coordinate' ask; "
                            "re-issue the same call with confirm=true "
                            "and the visible click runs. Only the raw "
                            "rung reads it — every other rung ignores "
                            "it. Voice-control batch (2026-11-16, C4): "
                            "before this, the ask had no resolver on "
                            "Live — the identical re-issue asked again "
                            "forever."),
        },
    },
    "required": ["action"],
}

# Exactly ten properties (the parent pins this count).
PROPERTY_COUNT = len(_PARAMETERS["properties"])


def _ok(data: dict) -> dict:
    out = {"ok": True}
    out.update(data)
    return out


def _fail(reason: str, **extra) -> dict:
    out = {"ok": False, "reason": reason}
    out.update(extra)
    return out


def _paused(reason: str, **extra) -> dict:
    """A goal pause (M-23/M-29), never a silent fail: the reason
    names what died and what he must do, and the goal waits."""
    out = {"ok": False, "status": "paused", "reason": reason,
           "what_to_do": ROUTE_DEATH_WHAT_TO_DO}
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Registry record + playbook opt-out (M-24)
# ---------------------------------------------------------------------------

def get_tool_record(tool_name: str):
    """The live registry record for a tool, or None when the
    registry cannot be read. Reads the singleton's stored dict —
    the same record run_goal's playbook-match step reads."""
    try:
        from .funnel.tools_registry import tools as _tools
        record = _tools._tools.get(str(tool_name or ""))
        return dict(record) if isinstance(record, dict) else None
    except Exception:  # noqa: BLE001 — unreadable: no record
        return None


def is_playbook_opted_out(tool_name: str) -> bool:
    """True when the tool's registry record carries
    playbook_opt_out — or when the registry cannot be read and
    the tool is in the module's fail-safe opt-out set. A
    click_anywhere step is never replayed from a lesson."""
    record = get_tool_record(tool_name)
    if record is not None:
        return bool(record.get("playbook_opt_out"))
    return str(tool_name or "") in _PLAYBOOK_OPT_OUT_TOOLS


# Coordinate shapes, selector strings, and this tool's args are
# never lessoned (M-24): a replayed click from a stale lesson is
# the failure the opt-out exists to prevent. The strip is SCOPED,
# not global (neighbour r3, 2026-11-14): the first version stripped
# x/y from every step's args and broke the lesson contract — a
# completed goal's lesson must record what actually ran
# (test_task_learning pins a fake_tool step's {"x": 1} surviving).
# So a coordinate/selector arg is stripped only when it can carry
# a stale click: (a) the tool is a pointer tool whose declared
# coordinate args move the pointer or click, or (b) the tool is
# registered and does NOT declare that key — a foreign (leaked)
# coordinate, never legitimate input. A declared coordinate arg
# of a pure-read tool (screenshot_region x/y) is legitimate input
# and is kept, and an unregistered tool's args (test fakes) are
# kept verbatim — there is no schema to judge them against.
_POINTER_TOOLS = frozenset({
    "mouse_control", "cursor_control", "click_by_mark"})
_COORDINATE_ARG_KEYS = frozenset({
    "x", "y", "point", "coords", "coordinates", "mark", "mark_id",
    "mark_number", "screen_x", "screen_y", "client_x", "client_y"})
_SELECTOR_ARG_KEYS = frozenset({
    "selector", "css", "css_selector", "xpath", "dom_selector"})


def strip_lesson_args(args, tool=None) -> dict:
    """A copy of a step's args with the lesson-unsafe coordinate /
    selector args removed (see the M-24 note above the key sets).
    Pure; the caller's dict is never mutated. ``tool`` is the
    step's tool name; without it, or for an unregistered tool,
    the args are kept verbatim — there is no schema to judge
    them against."""
    if not isinstance(args, dict):
        return {}
    tool = str(tool or "").strip()
    if tool in _POINTER_TOOLS:
        # Pointer tools act on the screen: their coordinate args
        # are never lessoned, declared or not. This branch needs
        # no registry read, so it holds even when the registry
        # cannot be read.
        return {k: v for k, v in args.items()
                if k not in _COORDINATE_ARG_KEYS
                and k not in _SELECTOR_ARG_KEYS}
    record = get_tool_record(tool) if tool else None
    if record is None:
        # Unregistered tool (test fakes, future tools): no schema
        # to judge against — the lesson records what ran, verbatim.
        return dict(args)
    declared = set(((record.get("parameters") or {}).get("properties")
                    or {}))
    out = {}
    for key, value in args.items():
        if ((key in _COORDINATE_ARG_KEYS or key in _SELECTOR_ARG_KEYS)
                and key not in declared):
            continue
        out[key] = value
    return out


def lesson_step_excluded(step) -> bool:
    """True when a playbook step must never be lessoned or
    replayed: it is a click_anywhere step (its args are excluded
    ENTIRELY). Any other step is kept with its coordinate /
    selector args stripped by :func:`strip_lesson_args` — a
    browser_read that happens to carry an x arg is not a click
    and is not dropped whole."""
    if not isinstance(step, dict):
        return True
    tool = str(step.get("tool", "") or "").strip()
    if tool == "click_anywhere" or is_playbook_opted_out(tool):
        return True
    return False


def filter_lesson_steps(steps):
    """The lesson-safe subset of tried steps: click_anywhere steps
    dropped entirely, lesson-unsafe coordinate/selector args
    stripped from the rest by :func:`strip_lesson_args` (pointer
    tools always; foreign args on registered tools; verbatim for
    unregistered tools). Returns a new list; the caller's steps
    are untouched."""
    out = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        if lesson_step_excluded(step):
            continue
        kept = dict(step)
        kept["args"] = strip_lesson_args(step.get("args") or {},
                                         tool=step.get("tool"))
        out.append(kept)
    return out


def click_anywhere_goal_flags(plan_steps):
    """(strict_completion, pause_on_denial) for a goal whose plan
    is ``plan_steps`` (M-25): True/True when any declared step is
    a click_anywhere step, else False/False. run_goal latches the
    same flags from the first picked click_anywhere step, so a
    plan-less goal that picks one is scoped the same way."""
    for step in plan_steps or []:
        tool = ""
        if isinstance(step, dict):
            tool = str(step.get("tool", "") or "")
        else:
            tool = str(getattr(step, "tool", "") or "")
        if tool.strip() == "click_anywhere":
            return True, True
    return False, False


# ---------------------------------------------------------------------------
# Classification + guards (§4)
# ---------------------------------------------------------------------------

def classify_action_class(name="", text="", role="", declared=""):
    """The action class for a click target, from the element's
    role/action (never from the ladder rung). A D-18 word or a
    state-change word (Like/Follow/Save/Star) in the name/text
    makes it consequential whatever the picker declared; the
    declared class is trusted only when it does not contradict
    the words. Uncertain -> consequential (ask, never assume
    free)."""
    try:
        from . import browser_scope as _scope
    except Exception:  # noqa: BLE001
        try:
            from core import browser_scope as _scope
        except Exception:  # noqa: BLE001 — unreadable: consequential
            return "consequential"
    hay = " ".join(str(v or "") for v in (name, text))
    if _scope.matches_backstop_words(hay):
        return "consequential"
    declared = str(declared or "").strip().lower()
    if declared in ("navigation", "read-only"):
        return declared
    return "consequential"


def guard_click(*, name="", text="", role="", action_class="",
                host="", path="", dom_root=None):
    """The pre-click guard (§4). Returns (allowed, outcome):
    allowed False carries the refusal dict (rule named).

    Checks, in order: D-17 messaging domain; D-17 composer
    context (when a DOM root is supplied); D-18 mislabel — a
    consequential name/text declared navigation/read-only is
    the picker's failure and refuses (M-23 pause, not fail).
    A missing host/path/dom_root skips only the checks that need
    it; the mislabel check always runs."""
    try:
        from . import browser_scope as _scope
    except Exception:  # noqa: BLE001
        try:
            from core import browser_scope as _scope
        except Exception:  # noqa: BLE001 — unreadable: refuse
            return False, _fail(
                "cannot load the scope guard — refusing the click "
                "rather than clicking unguarded (D-17/D-18)",
                rule="scope-guard-unreadable")
    if host and _scope.is_messaging_domain(host, path or ""):
        return False, _fail(
            "refused: %s is a messaging app (D-17) — VYUK never "
            "clicks Send on a messaging app; use social_send, "
            "which prepares the message and lets you send it"
            % (host or "this site"),
            rule="D-17-messaging-domain")
    if dom_root is not None:
        verdict = _scope.composer_context_test(dom_root)
        if verdict.get("messaging_send"):
            return False, _fail(
                "refused: %s (D-17)" % verdict.get("reason", ""),
                rule="D-17-composer-context")
    declared = str(action_class or "").strip().lower()
    if declared in ("navigation", "read-only"):
        hay = " ".join(str(v or "") for v in (name, text))
        if _scope.matches_backstop_words(hay):
            return False, _paused(
                "a consequential control (%s) was declared %s — "
                "the picker mislabeled it, so the step pauses and "
                "asks instead of clicking under a wrong label "
                "(D-18 backstop)" % (hay.strip(), declared),
                rule="D-18-backstop")
    return True, {}


def check_session_marker(marker, *, observation_text="", login_wall=False):
    """The session-alive marker check (M-59, R-15). Returns
    (alive, outcome): alive False carries the pause dict.

    When the M-65 gate recorded a marker, a missing marker pauses
    with "session dead — sign in again". When NO marker was
    recorded, the marker check is SKIPPED and the login-wall
    recogniser is a NEGATIVE check only (wall present -> pause
    "session dead — sign in again"; no wall -> proceed)."""
    if marker is None or str(marker or "").strip() == "":
        if login_wall:
            return False, _paused(
                "session dead — sign in again (a login wall is "
                "present and no session marker was recorded)",
                rule="R-15-login-wall")
        return True, {}
    if str(marker) not in str(observation_text or ""):
        return False, _paused(
            "session dead — sign in again (the session marker %r "
            "is missing from this observation)" % str(marker),
            rule="M-59-marker-missing")
    return True, {}


def devtools_delta_check(baseline, current):
    """The DevTools delta check: the baseline recorded at session
    start against the current measurement; a mid-goal change
    refuses.

    When both sides are DevTools delta pairs (a {"height", "width"}
    dict or a (h, w) pair), the conversion module's helper is the
    authority — ``check_devtools_delta`` in
    ``core/browser/viewport_convert`` (the other worker's module,
    now present). Any other measured-chrome dict is compared
    generically here (key-by-key); a change in any term refuses.
    Returns (unchanged, detail)."""
    baseline = baseline or {}
    current = current or {}
    if _is_delta_pair(baseline) and _is_delta_pair(current):
        try:
            from core.browser.viewport_convert import (
                check_devtools_delta as _check)
            result = _check(baseline, current)
            if isinstance(result, dict) and not result.get("ok"):
                return False, {"changed": ["devtools-delta"],
                               "reason": str(result.get("reason")
                                             or result)}
            return True, {"changed": []}
        except Exception:  # noqa: BLE001 — helper unreadable: local check
            pass
    changed = sorted(k for k in set(baseline) | set(current)
                     if baseline.get(k) != current.get(k))
    if not changed:
        return True, {"changed": []}
    return False, {"changed": changed,
                   "reason": ("DevTools chrome changed mid-goal "
                              "(%s) — refusing rather than converting "
                              "against stale chrome" % ", ".join(changed))}


def _is_delta_pair(value):
    """True when the value is a DevTools delta pair: a dict with
    "height" + "width", or a 2-item (h, w) tuple/list."""
    if isinstance(value, dict):
        return "height" in value and "width" in value
    if isinstance(value, (list, tuple)):
        return len(value) == 2
    return False


def frame_origin_denied(origin):
    """Per-frame denylist (N-17): True when the frame's origin is
    denylisted — the other worker's ``origin_denied`` in
    ``core/browser/denylist.py``. A denied-origin frame's content
    is refused by name, never silently skipped. When the denylist
    module is unreadable, no origin is recognised as denied (the
    list is empty, never a silent pass on a listed origin)."""
    try:
        from core.browser.denylist import origin_denied as _denied
        return bool(_denied(origin))
    except Exception:  # noqa: BLE001 — unreadable: not denied
        return False


# ---------------------------------------------------------------------------
# Click ladder (§1.6)
# ---------------------------------------------------------------------------

# Ladder rung order: DOM box > UIA name > mark > OCR text > raw
# coordinates (confirm-first) > refuse.
_LADDER_ORDER = {"dom_box": 0, "uia_name": 1, "mark": 2, "ocr": 3,
                 "raw": 4}


def click_ladder_pick(candidates):
    """Pick the highest-rung confident candidate (§1.6).

    ``candidates`` are dicts with "rung" (dom_box / uia_name /
    mark / ocr / raw), "x", "y", "confidence" (0..1), "label".
    Returns {"status": "pick", "candidate": ...} for a confident
    pick; {"status": "ask", "announce": "raw coordinate ...",
    "candidate": ...} for the raw-coordinates rung — the rung
    announces "raw coordinate" out loud BEFORE it asks; and
    {"status": "refuse", "reason": ...} when no rung yields a
    confident coordinate (M-23 pause, not fail).

    A candidate is confident at >= 0.5, except the raw rung,
    which is never confident on its own — it always asks first.
    """
    usable = []
    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        rung = str(cand.get("rung") or "")
        if rung not in _LADDER_ORDER:
            continue
        try:
            conf = float(cand.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        usable.append(((_LADDER_ORDER[rung], -conf), cand, conf, rung))
    usable.sort(key=lambda item: item[0])
    for _key, cand, conf, rung in usable:
        if rung == "raw":
            continue  # raw never picks outright — it asks first
        if conf >= 0.5:
            return {"status": "pick", "rung": rung, "candidate": cand}
    for _key, cand, conf, rung in usable:
        if rung == "raw":
            return {"status": "ask", "rung": "raw", "candidate": cand,
                    "announce": (RAW_COORDINATE_ANNOUNCE + ": the "
                                 "only coordinate is a raw screen "
                                 "point at (%s, %s) — confirm before "
                                 "I click it"
                                 % (cand.get("x"), cand.get("y")))}
    return {"status": "refuse",
            "reason": ("no rung yields a confident coordinate — "
                       "the ladder is exhausted (DOM box, UIA name, "
                       "mark, OCR text, raw coordinates), so the "
                       "step pauses (M-23) instead of clicking on "
                       "a guess"),
            "rule": "M-23-ladder-failure"}


def re_ground_mark(mark, observation):
    """Re-ground a mark against the FRESH observation before the
    click (L-40 — marks never carried across observations).

    ``mark`` is {"number": n, "stamp": s, "x": x, "y": y};
    ``observation`` carries "stamp" and "foreground". Returns
    {"status": "ok", "point": (x, y)} or {"status": "re-observe",
    "reason": ...} — a stale mark re-observes, never clicks."""
    stamp = (observation or {}).get("stamp")
    mark_stamp = (mark or {}).get("stamp")
    if mark_stamp is not None and stamp is not None \
            and str(mark_stamp) != str(stamp):
        return {"status": "re-observe",
                "reason": ("mark %s is stale — it was minted under "
                           "observation %s, the fresh observation is "
                           "%s; re-observe, never click a stale mark"
                           % (mark.get("number"), mark_stamp, stamp))}
    stale = None
    try:
        from .desktop import uia_marks as _marks
        stale = _marks._marks_stale_reason(mark.get("number"))
    except Exception:  # noqa: BLE001 — no engine seam: stamp is the check
        stale = None
    if stale:
        return {"status": "re-observe", "reason": stale}
    point = (mark.get("x"), mark.get("y"))
    if point[0] is None or point[1] is None:
        return {"status": "re-observe",
                "reason": "mark %s carries no point — re-observe"
                          % mark.get("number")}
    return {"status": "ok", "point": point}


# ---------------------------------------------------------------------------
# Browser observe + browser picker (internal helpers, NOT registry tools)
# ---------------------------------------------------------------------------

# Snapshot exclusion (M-8): values are never carried into the
# observation — beyond inputs, also textarea / contenteditable /
# select values.
_SNAPSHOT_EXCLUDED_TAGS = ("input", "textarea", "select")
_SNAPSHOT_EXCLUDED_ATTRS = ("contenteditable",)

# The observe renders the foreground window + focus (not the whole
# desktop) with this size cap on the rendered text.
OBSERVE_TEXT_CAP = 8000


def _screen_snapshot_values(snapshot):
    """Strip excluded values from a DOM snapshot dict (M-8): any
    node whose tag is input/textarea/select, or which is
    contenteditable, loses its value/text. Returns a new dict."""
    if not isinstance(snapshot, dict):
        return snapshot
    tag = str(snapshot.get("tag") or "").strip().lower()
    attrs = snapshot.get("attrs") or {}
    attrs = attrs if isinstance(attrs, dict) else {}
    editable = str(attrs.get("contenteditable") or "").strip().lower() \
        in ("true", "")
    if tag in _SNAPSHOT_EXCLUDED_TAGS or editable:
        out = dict(snapshot)
        out.pop("value", None)
        out["text"] = ""
        out["children"] = [
            _screen_snapshot_values(c) for c in
            (snapshot.get("children") or [])]
        return out
    out = dict(snapshot)
    out["children"] = [_screen_snapshot_values(c) for c in
                       (snapshot.get("children") or [])]
    return out


def _wrap_observation_text(source, text):
    """Wrap untrusted observation text at the producer
    (wrap_untrusted, core/injection_envelope.py:32). Falls back
    to a plain [untrusted] marker when the envelope is
    unreadable — the text is never left unwrapped."""
    try:
        from .injection_envelope import wrap_untrusted as _wrap
        return _wrap(source, text)
    except Exception:  # noqa: BLE001 — envelope unreadable: mark it
        try:
            from core.injection_envelope import wrap_untrusted as _wrap
            return _wrap(source, text)
        except Exception:  # noqa: BLE001 — last resort: plain marker
            return "[untrusted %s]\n%s" % (source, text)


def browser_observe(*, foreground=None, focus=None, dom_snapshot=None,
                    routes=("extension", "cdp", "sight"),
                    profile="main", session_marker=None,
                    login_wall=False, conversion=None,
                    devtools_baseline=None, hwnd=None, pass_id=None,
                    frame_origins=None):
    """The browser observe (§2.1). Renders the foreground window +
    focus (not the whole desktop) with a size cap, and names the
    route list (extension/CDP/sight+click) AND the profile (main
    vs debug) alongside it (M-20). Re-measures the conversion
    terms per observation; the hwnd is re-resolved per observation
    (Q-5); the DOM box + window rect of one conversion carry the
    same observation pass id (R-10).

    ``dom_snapshot`` is the screened DOM dict (values already
    excluded per M-8, or excluded here). ``conversion`` is the
    freshly measured conversion terms for this observation.
    ``frame_origins`` are the origins of the page's frames — a
    denied-origin frame's content is refused by name (N-17),
    never silently skipped. Returns an observation dict with
    "status" ok/paused: the session-alive marker is checked on
    EVERY observation (M-59, R-15) and a missing marker pauses
    with "session dead — sign in again"."""
    stamp = pass_id or ("obs-%d" % int(time.time() * 1000))
    observation = {
        "kind": "browser",
        "pass_id": stamp,
        "foreground": foreground,
        "focus": focus,
        "routes": list(routes),
        "profile": profile,
        "hwnd": hwnd,
        "conversion": dict(conversion or {}),
        "dom": _screen_snapshot_values(dom_snapshot)
        if dom_snapshot is not None else None,
    }
    denied = [o for o in (frame_origins or [])
              if frame_origin_denied(o)]
    if denied:
        observation["status"] = "paused"
        observation["reason"] = (
            "denied-origin frame refused (N-17): %s — the frame's "
            "content must not reach the observation"
            % ", ".join(str(o) for o in denied))
        observation["rule"] = "N-17-denied-frame"
        return observation
    if devtools_baseline is not None:
        unchanged, detail = devtools_delta_check(
            devtools_baseline, conversion or {})
        if not unchanged:
            observation["status"] = "paused"
            observation["reason"] = detail.get("reason", "")
            observation["rule"] = "devtools-delta"
            return observation
    text = str(observation.get("dom") or "")[:OBSERVE_TEXT_CAP]
    observation["text"] = _wrap_observation_text("browser-observe", text)
    alive, outcome = check_session_marker(
        session_marker, observation_text=text, login_wall=login_wall)
    if not alive:
        observation["status"] = "paused"
        observation["reason"] = outcome.get("reason", "")
        observation["rule"] = outcome.get("rule", "")
        return observation
    observation["status"] = "ok"
    return observation


def pick_browser(observation, target, *, by="dom"):
    """The browser picker (§2.2): picks from the observation — the
    ranked candidates, with the click ladder's rung named per
    candidate. The local picker renders only fg/focus + a marks
    table. Returns {"status": "pick"/"ask"/"refuse", ...} — the
    "pick" carries the ladder rung, the candidate, and the
    declared action class."""
    if not isinstance(observation, dict) or not observation:
        return {"status": "refuse",
                "reason": "no observation to pick from — re-observe"}
    target_text = str(target or "").strip()
    if not target_text:
        return {"status": "refuse",
                "reason": "no target named — the picker cannot pick "
                          "without one"}
    candidates = []
    dom = observation.get("dom") or {}
    if isinstance(dom, dict):
        for node, _path in _walk_dom(dom):
            text = str(node.get("text") or "") + " " + str(
                (node.get("attrs") or {}).get("aria-label") or "")
            if target_text.lower() in text.lower():
                box = node.get("box") or {}
                if isinstance(box, dict) and box.get("x") is not None:
                    candidates.append({
                        "rung": "dom_box", "x": box.get("x"),
                        "y": box.get("y"), "confidence": 0.9,
                        "label": text.strip(),
                        "role": node.get("role") or "",
                        "name": text.strip()})
    for mark in observation.get("marks") or []:
        if not isinstance(mark, dict):
            continue
        label = str(mark.get("label") or "")
        if target_text.lower() in label.lower():
            candidates.append({"rung": "mark", "x": mark.get("x"),
                               "y": mark.get("y"), "confidence": 0.8,
                               "label": label,
                               "mark": mark.get("number")})
    for word in observation.get("ocr_words") or []:
        text = word.get("text") if isinstance(word, dict) else str(word)
        if str(text or "").strip().lower() == target_text.lower():
            box = word.get("box") if isinstance(word, dict) else None
            if isinstance(box, dict):
                candidates.append({
                    "rung": "ocr",
                    "x": (box.get("left", 0) + box.get("right", 0)) // 2,
                    "y": (box.get("top", 0) + box.get("bottom", 0)) // 2,
                    "confidence": 0.7, "label": str(text)})
    if by == "point":
        try:
            x_text, y_text = target_text.split(",", 1)
            candidates.append({"rung": "raw", "x": int(x_text),
                               "y": int(y_text), "confidence": 0.0,
                               "label": "raw point %s" % target_text})
        except (ValueError, TypeError):
            pass
    picked = click_ladder_pick(candidates)
    if picked["status"] == "pick":
        cand = picked["candidate"]
        return {"status": "pick", "rung": picked["rung"],
                "candidate": cand,
                "action_class": classify_action_class(
                    cand.get("name", ""), cand.get("label", ""),
                    cand.get("role", "")),
                "role": cand.get("role", ""),
                "name": cand.get("name") or cand.get("label", "")}
    return picked


def _walk_dom(node):
    """Yield (node, path) over a DOM-node dict tree."""
    if not isinstance(node, dict):
        return
    yield node, []
    for child in node.get("children") or []:
        for sub, subpath in _walk_dom(child):
            yield sub, [node] + subpath


# ---------------------------------------------------------------------------
# Desktop observe + desktop picker (§1.5, internal helpers)
# ---------------------------------------------------------------------------

def desktop_observe(*, scope="", snapshot_fn=None, base_fn=None):
    """The desktop observe (§2.1): the loop's default
    `core.desktop.uia.desktop_snapshot` PLUS
    `perception_adapter.observe()` — one capture, one stamp.
    The snapshot's `stamp` IS the desktop pass id; the capture is
    called EXACTLY ONCE (by the adapter itself) and OCR first,
    then marks, controls, pixels, all against the same capture.

    Records: foreground window + focus label + UIA tree (controls)
    + OCR words + marks + foreground identity + virtual bounds.
    NO session-alive marker, NO route list, NO profile.

    A denylisted foreground yields an empty snapshot with
    `denied` and the goal pauses with the reason named; a capture
    failure yields an empty snapshot with `failed` and the goal
    pauses (M-29). A game foreground (M-37) pauses the same way.
    """
    if base_fn is None:
        try:
            from .desktop.uia_discover import desktop_snapshot as _base
            base_fn = _base
        except Exception:  # noqa: BLE001
            try:
                from core.desktop.uia_discover import (
                    desktop_snapshot as _base)
                base_fn = _base
            except Exception:  # noqa: BLE001 — no base: empty
                base_fn = lambda: None  # noqa: E731
    if snapshot_fn is None:
        try:
            from .desktop.perception_adapter import observe as _obs
            snapshot_fn = _obs
        except Exception:  # noqa: BLE001
            try:
                from core.desktop.perception_adapter import (
                    observe as _obs)
                snapshot_fn = _obs
            except Exception:  # noqa: BLE001 — no adapter: failed
                snapshot_fn = None
    base = None
    try:
        base = base_fn()
    except Exception:  # noqa: BLE001 — base failure: still try adapter
        base = None
    if snapshot_fn is None:
        return {"kind": "desktop", "status": "paused",
                "capture_status": "failed",
                "reason": ("the desktop observe has no perception "
                           "adapter — the capture cannot run, so the "
                           "goal pauses (M-29)"),
                "rule": "M-29-observe-death",
                "foreground": (base or {}).get("fg") if base else None,
                "focus": (base or {}).get("focus") if base else None}
    try:
        snap = snapshot_fn(scope)
    except Exception as exc:  # noqa: BLE001 — capture failure: empty
        return {"kind": "desktop", "status": "paused",
                "capture_status": "failed",
                "reason": "capture failed (%s) — the goal pauses "
                          "(M-29)" % exc,
                "rule": "M-29-observe-death",
                "foreground": (base or {}).get("fg") if base else None,
                "focus": (base or {}).get("focus") if base else None}
    if not isinstance(snap, dict):
        return {"kind": "desktop", "status": "paused",
                "capture_status": "failed",
                "reason": "the perception adapter returned no "
                          "snapshot — the goal pauses (M-29)",
                "rule": "M-29-observe-death"}
    status = snap.get("capture_status") or "ok"
    if status == "denied":
        return {"kind": "desktop", "status": "paused",
                "capture_status": "denied",
                "reason": ("denylisted foreground: %s — the goal "
                           "pauses with the denylist reason named"
                           % (snap.get("capture_reason") or "denied")),
                "rule": "denylist-foreground",
                "foreground": (base or {}).get("fg") if base else None,
                "focus": (base or {}).get("focus") if base else None}
    if status == "failed":
        return {"kind": "desktop", "status": "paused",
                "capture_status": "failed",
                "reason": ("capture failed: %s — the goal pauses "
                           "(M-29)"
                           % (snap.get("capture_reason") or "failed")),
                "rule": "M-29-observe-death"}
    observation = {
        "kind": "desktop",
        "status": "ok",
        "pass_id": snap.get("stamp"),
        "stamp": snap.get("stamp"),
        "foreground": (base or {}).get("fg") if base else None,
        "focus": (base or {}).get("focus") if base else None,
        "foreground_identity": snap.get("foreground_identity"),
        "bounds": snap.get("bounds"),
        "controls": snap.get("controls") or [],
        "ocr_words": snap.get("ocr_words") or [],
        "marks": snap.get("marks") or [],
        "pixels": snap.get("pixels") or [],
        "notes": snap.get("notes") or {},
        "capture_status": status,
        "capture_reason": snap.get("capture_reason"),
    }
    game = None
    try:
        from . import browser_scope as _scope
        game = _scope.is_game_foreground(
            observation.get("foreground_identity") or
            {"title": observation.get("foreground")})
    except Exception:  # noqa: BLE001 — unreadable: no game recognised
        game = None
    if game:
        observation["status"] = "paused"
        observation["reason"] = (
            "the foreground is a denylisted game (%s) — the goal "
            "pauses (M-37)" % game)
        observation["rule"] = "M-37-game-denylist"
    return observation


def _control_usable(control):
    """A UIA control dict is usable when it is not disabled,
    hidden, or zero-size. Returns (usable, reason)."""
    if not isinstance(control, dict):
        return False, "not a control"
    if control.get("disabled"):
        return False, "disabled"
    if control.get("hidden") or control.get("visible") is False:
        return False, "hidden"
    rect = control.get("rect")
    if isinstance(rect, (list, tuple)) and len(rect) == 4:
        left, top, right, bottom = rect
        if right <= left or bottom <= top:
            return False, "zero-size"
    return True, ""


def _normalise_text(text):
    """Whole-word, case-insensitive, normalised text (§6.3
    folding without the confusable step: strip non-alphanumerics,
    fold case)."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"[^0-9a-z]+", " ", text.lower()).strip()


def pick_desktop(snapshot, target, *, adapter_vocabulary=None):
    """The desktop picker (§1.5/§2.2): picks from the desktop
    observation — UIA control / mark / OCR text candidates, with
    the click ladder's rung named per candidate and the action
    class from the adapter vocabulary (VS Code) or the control
    type + name (generic desktop, M-34 sight-path classification).

    UIA: exact name wins over substring; several usable candidates
    in the winning tier -> refuse with the candidate listing.
    Disabled/hidden/zero-size skipped with disclosure. Mark:
    re-grounded (stale -> re-observe, never click). OCR: whole-
    word, case-insensitive, normalised; several boxes -> refuse
    with candidates; one -> its centre. Electron wall
    (kind electron/chrome/chromium-unknown) -> fall back to
    mark/OCR rungs, never a full tree."""
    if not isinstance(snapshot, dict) or not snapshot:
        return {"status": "refuse",
                "reason": "no desktop observation to pick from — "
                          "re-observe"}
    target_text = str(target or "").strip()
    if not target_text:
        return {"status": "refuse",
                "reason": "no target named — the picker cannot pick "
                          "without one"}
    needle = _normalise_text(target_text)
    wall = (snapshot.get("electron_wall") or snapshot.get("wall")
            or {})
    wall_kind = str(wall.get("kind") or "").strip().lower()
    behind_wall = wall_kind in ("electron", "chrome",
                                "chromium-unknown")
    candidates = []
    skipped = []
    if not behind_wall:
        controls = snapshot.get("controls") or []
        exact, partial = [], []
        for control in controls:
            if not isinstance(control, dict):
                continue
            name = str(control.get("name") or "")
            if _normalise_text(name) == needle:
                exact.append(control)
            elif needle and needle in _normalise_text(name):
                partial.append(control)
        tier, tier_kind = ((exact, "exact") if exact
                           else (partial, "partial"))
        if len(tier) > 1:
            usable = [c for c in tier if _control_usable(c)[0]]
            if len(usable) > 1:
                return {"status": "refuse",
                        "reason": ("several %s matches for %r — "
                                   "refusing with the candidate "
                                   "listing, never guessing: %s"
                                   % (tier_kind, target_text,
                                      "; ".join(
                                          str(c.get("name")) for c in
                                          usable))) ,
                        "rule": "ambiguous-tier",
                        "candidates": [c.get("name") for c in usable]}
        for control in tier:
            ok, why = _control_usable(control)
            if not ok:
                skipped.append("%s (%s)" % (control.get("name"), why))
                continue
            rect = control.get("rect")
            if isinstance(rect, (list, tuple)) and len(rect) == 4:
                left, top, right, bottom = rect
                candidates.append({
                    "rung": "uia_name",
                    "x": (left + right) // 2, "y": (top + bottom) // 2,
                    "confidence": 0.95 if tier_kind == "exact" else 0.8,
                    "label": str(control.get("name") or ""),
                    "role": str(control.get("type") or ""),
                    "name": str(control.get("name") or ""),
                    "tier": tier_kind})
                break
    else:
        skipped.append("UIA tree is shallow behind the Electron wall "
                       "(%s) — falling back to mark/OCR rungs" % wall_kind)
    for mark in snapshot.get("marks") or []:
        if not isinstance(mark, dict):
            continue
        label = str(mark.get("label") or "")
        if needle and needle in _normalise_text(label):
            grounded = re_ground_mark(mark, snapshot)
            if grounded["status"] == "re-observe":
                return {"status": "re-observe",
                        "reason": grounded["reason"]}
            x, y = grounded["point"]
            candidates.append({"rung": "mark", "x": x, "y": y,
                               "confidence": 0.85, "label": label,
                               "mark": mark.get("number")})
    ocr_hits = []
    for word in snapshot.get("ocr_words") or []:
        text = word.get("text") if isinstance(word, dict) else str(word)
        if _normalise_text(str(text or "")) == needle and needle:
            ocr_hits.append(word)
    if len(ocr_hits) > 1:
        return {"status": "refuse",
                "reason": ("several OCR boxes match %r — refusing "
                           "with the candidates named: %s"
                           % (target_text, "; ".join(
                               str(w.get("text") if isinstance(w, dict)
                                   else w) for w in ocr_hits))),
                "rule": "ambiguous-ocr",
                "candidates": [str(w.get("text") if isinstance(w, dict)
                                   else w) for w in ocr_hits]}
    if len(ocr_hits) == 1:
        word = ocr_hits[0]
        box = word.get("box") if isinstance(word, dict) else None
        if isinstance(box, dict):
            candidates.append({
                "rung": "ocr",
                "x": (box.get("left", 0) + box.get("right", 0)) // 2,
                "y": (box.get("top", 0) + box.get("bottom", 0)) // 2,
                "confidence": 0.7,
                "label": str(word.get("text"))})
    picked = click_ladder_pick(candidates)
    if picked["status"] != "pick":
        if skipped:
            picked["skipped"] = skipped
        return picked
    cand = picked["candidate"]
    action_class = classify_desktop_action(
        cand.get("role", ""), cand.get("name", "") or cand.get("label", ""),
        adapter_vocabulary=adapter_vocabulary)
    out = {"status": "pick", "rung": picked["rung"],
           "candidate": cand, "action_class": action_class,
           "role": cand.get("role", ""),
           "name": cand.get("name") or cand.get("label", "")}
    if skipped:
        out["skipped"] = skipped
    if behind_wall:
        out["wall_note"] = ("Electron wall (%s): the UIA tree is "
                            "shallow — picked by the %s rung"
                            % (wall_kind, picked["rung"]))
    return out


def classify_desktop_action(control_type, name, *,
                            adapter_vocabulary=None):
    """The action class for a desktop control (M-34 sight-path
    classification): UIA control type + name, whole-word,
    case-insensitive, on the D-18 word list. For an app with an
    adapter (VS Code, 19 entries), the adapter vocabulary decides
    first; generic controls fall back to type + name. Uncertain
    -> consequential (ask)."""
    vocab = adapter_vocabulary or {}
    key = _normalise_text(str(name or ""))
    if isinstance(vocab, dict):
        for entry_name, entry in vocab.items():
            if _normalise_text(str(entry_name)) == key and key:
                if isinstance(entry, dict) and entry.get("confirm"):
                    return "consequential"
                return "navigation"
    try:
        from . import browser_scope as _scope
        hay = " ".join(str(v or "") for v in (control_type, name))
        if _scope.matches_backstop_words(hay):
            return "consequential"
    except Exception:  # noqa: BLE001 — unreadable: consequential
        return "consequential"
    return "navigation"


# ---------------------------------------------------------------------------
# Desktop point rule (§1.5/§2.3) + visible click (D-8)
# ---------------------------------------------------------------------------

def desktop_point_fence(x, y, *, bounds=None, window_rect=None):
    """The fence (L-6) for desktop points: the point must be inside
    the virtual desktop bounds AND inside the window's visible
    rect, else refuse with the numbers. Desktop points are already
    in screen coordinates — NO §1.1 conversion runs on them.
    Returns (inside, detail)."""
    try:
        x = int(x)
        y = int(y)
    except (TypeError, ValueError):
        return False, {"reason": "the point is not a coordinate pair"}
    if bounds is not None:
        left, top, right, bottom = bounds
        if not (left <= x < right and top <= y < bottom):
            return False, {
                "reason": ("point (%d, %d) is outside the virtual "
                           "desktop bounds (%d, %d, %d, %d)"
                           % (x, y, left, top, right, bottom)),
                "point": (x, y), "bounds": tuple(bounds)}
    if window_rect is not None:
        left, top, right, bottom = window_rect
        if not (left <= x < right and top <= y < bottom):
            return False, {
                "reason": ("point (%d, %d) is outside the window's "
                           "visible rect (%d, %d, %d, %d)"
                           % (x, y, left, top, right, bottom)),
                "point": (x, y), "window_rect": tuple(window_rect)}
    return True, {"point": (x, y)}


def virtual_bounds():
    """The virtual desktop bounds via virtual_screen_bounds
    (core/desktop/screen_capture.py:972), or None when they cannot
    be determined."""
    try:
        from .desktop.screen_capture import (
            virtual_screen_bounds as _bounds)
        return _bounds()
    except Exception:  # noqa: BLE001
        try:
            from core.desktop.screen_capture import (
                virtual_screen_bounds as _bounds)
            return _bounds()
        except Exception:  # noqa: BLE001 — unmeasurable: None
            return None


def visible_click(x, y):
    """The VISIBLE click (D-8): the physical cursor moves to the
    point via the SendInput hub (`_sendinput_move`,
    core/desktop/cursor_input.py:288) and the click goes through
    the hub — NOT via `el.click_input()`. Returns (ok, detail).
    Off Windows (or with no hub) the click is honestly
    unavailable, never silently faked."""
    try:
        from .desktop.cursor_input import (
            _sendinput_move as _move, _sendinput_click as _click)
    except Exception:  # noqa: BLE001
        try:
            from core.desktop.cursor_input import (
                _sendinput_move as _move,
                _sendinput_click as _click)
        except Exception:  # noqa: BLE001 — no hub here
            return False, {"reason": ("the SendInput hub is not "
                                      "available on this machine — "
                                      "no click was made")}
    try:
        if not _move(int(x), int(y)):
            return False, {"reason": ("the cursor could not be moved "
                                      "to (%d, %d)" % (int(x), int(y)))}
        if not _click("left", 1):
            return False, {"reason": ("the cursor moved to (%d, %d) "
                                      "but the click did not land"
                                      % (int(x), int(y)))}
    except Exception as exc:  # noqa: BLE001 — hub failure: no click
        return False, {"reason": "the visible click failed: %s" % exc}
    return True, {"point": (int(x), int(y)), "visible": True}


# ---------------------------------------------------------------------------
# Routes (§1.7) — extension primary, CDP fallback, sight fallback,
# desktop primary for desktop apps
# ---------------------------------------------------------------------------

# Route-availability probes. The browser worker owns core/browser/
# (conversion, host, channel, extension, CDP bypass, denylist); a
# probe for the extension route is registered by that worker via
# register_route_probe when its host lands — until then the
# extension route honestly reports itself unavailable instead of
# being guessed at.
_ROUTE_PROBES: dict = {}


def register_route_probe(route, fn):
    """Register an availability probe for a route (the browser
    worker's seam: its host registers the extension probe when
    core/browser/ lands). ``fn`` returns (available, reason)."""
    _ROUTE_PROBES[str(route)] = fn


def _extension_available():
    probe = _ROUTE_PROBES.get("extension")
    if probe is not None:
        try:
            return bool(probe()[0]), str(probe()[1] or "")
        except Exception as exc:  # noqa: BLE001 — probe failed: dead
            return False, "extension probe failed: %s" % exc
    return False, ("the extension route is unavailable — the "
                   "browser host in core/browser/ has not landed "
                   "yet (another worker owns it)")


def _cdp_available():
    """CDP attach/launch fallback — probe the NORMAL port first
    with `_cdp_handshake` in core/desktop/browser_cdp.py
    (takes the debug port; the default is 9222 via `_cdp_port`):
    a standard --remote-debugging-port server answers
    /json/version, and page targets in /json/list are enough —
    the browser socket lives in /json/version, and /json/list
    never carries a browser-type target on a standard Chrome
    (field check B2, 2026-11-17: HeadlessChrome 156 lists only
    page targets). The toggle probe (`_cdp_handshake_toggle`)
    is reserved for toggle servers, where /json/version 404s
    and the normal handshake returns None before ever trying
    /json/list. While both probes are absent, CDP refuses with
    "CDP bypass unavailable" rather than reimplementing the
    bypass here."""
    try:
        from .desktop.browser_cdp import (
            _cdp_handshake as _handshake,
            _cdp_handshake_toggle as _toggle,
            _cdp_handshake_toggle_reason as _reason_fn,
            _cdp_port as _port_fn)
    except Exception:  # noqa: BLE001
        try:
            from core.desktop.browser_cdp import (
                _cdp_handshake as _handshake,
                _cdp_handshake_toggle as _toggle,
                _cdp_handshake_toggle_reason as _reason_fn,
                _cdp_port as _port_fn)
        except Exception:  # noqa: BLE001 — not there yet
            return False, "CDP bypass unavailable"
    try:
        port = _port_fn()
    except Exception:  # noqa: BLE001 — unreadable: default port
        port = 9222
    # Normal port first: /json/version answering means a CDP
    # server is there (this is the probe enable_browser_control
    # already uses).
    try:
        if _handshake(port):
            return True, ""
    except Exception:  # noqa: BLE001 — fall through to toggle
        pass
    # Toggle server: /json/version 404s, so the normal handshake
    # returned None — try the toggle probe, which reads
    # /json/list directly and needs a browser-type target (the
    # N-9 premise, still owed from his machine).
    try:
        result = _toggle(port)
    except Exception as exc:  # noqa: BLE001 — toggle failed: dead
        return False, "CDP bypass unavailable (%s)" % exc
    if result:
        return True, ""
    try:
        reason = _reason_fn()
    except Exception:  # noqa: BLE001 — no reason: generic
        reason = None
    return False, "CDP bypass unavailable (%s)" % (
        reason or "no browser-type CDP target answered")


def _sight_available():
    probe = _ROUTE_PROBES.get("sight")
    if probe is not None:
        try:
            return bool(probe()[0]), str(probe()[1] or "")
        except Exception as exc:  # noqa: BLE001 — probe failed: dead
            return False, "sight+click probe failed: %s" % exc
    try:
        from .desktop import screen_capture as _sc  # noqa: F401
        return True, ""
    except Exception:  # noqa: BLE001 — no capture stack
        return False, ("sight+click is unavailable — no screen "
                       "capture stack on this machine")


def _route_available(route):
    if route == "extension":
        return _extension_available()
    if route == "cdp":
        return _cdp_available()
    if route == "sight":
        return _sight_available()
    if route == "desktop":
        return _sight_available()
    return False, "unknown route %r" % route


# First-run onboarding (2026-11-16): a dead browser route is
# the browser never having been connected as often as a route
# dying mid-goal — the pause names the one-tap way back, so a
# fresh install that skipped the browser ask is told the fix
# instead of only the route-death procedure.
_CONNECT_WAY_BACK = (
    " If your browser was never connected, tap 'Connect my "
    "browser' and I'll set it up — one tap, about a minute.")


def resolve_route(route, *, browser="chrome"):
    """Resolve the route for one act (§1.7). Desktop apps use the
    desktop route as their primary, not a fallback. Returns
    (route, outcome): outcome is None when the route is live,
    else the route-death pause dict — a route death pauses the
    goal (M-29), it never silently falls through to another
    route mid-goal. This is the EXPLICIT-route resolver: use
    resolve_default_route when no route was requested."""
    available, reason = _route_available(route)
    if available:
        return route, None
    pause_reason = (
        "the %s route died (%s) — the goal pauses; %s"
        % (route, reason, ROUTE_DEATH_WHAT_TO_DO))
    if route in ("extension", "cdp"):
        pause_reason += _CONNECT_WAY_BACK
    return route, _paused(
        pause_reason,
        rule="M-29-route-death", route=route, browser=browser)


def resolve_default_route(*, browser="chrome"):
    """Resolve the DEFAULT route — no route was requested (§1.7,
    hunt #2, 2026-11-16). Walks the documented order extension
    -> CDP -> sight and uses the first live route. This is route
    SELECTION, not a mid-goal fall-through: M-29 forbids silently
    switching routes after one was named and died; it does not
    forbid picking the first live route when none was named.
    Before this fix the default was the extension route, which
    is dead (no extension probe is registered anywhere), so
    every default click paused even with the browser connected.
    Returns (route, None) for the first live route, else
    (None, pause-dict) naming every dead route honestly."""
    tried = []
    for candidate in ("extension", "cdp", "sight"):
        available, reason = _route_available(candidate)
        if available:
            return candidate, None
        tried.append((candidate, reason))
    pause_reason = (
        "no browser route is live — " + "; ".join(
            "the %s route is unavailable (%s)" % (name, why)
            for name, why in tried)
        + " — the goal pauses; %s"
        % ROUTE_DEATH_WHAT_TO_DO)
    pause_reason += _CONNECT_WAY_BACK
    return None, _paused(
        pause_reason,
        rule="M-29-route-death", route="extension", browser=browser)


# ---------------------------------------------------------------------------
# The CDP act (hunt fix #2b, 2026-11-16)
# ---------------------------------------------------------------------------

def _cdp_click(target, by, confirm):
    """CDP act seam — the real Playwright click via browser_click
    (core/browser_dispatch.py:152). Lazy import: browser_dispatch
    is only pulled in when a CDP act actually runs, so importing
    this module never drags the browser stack in. Tests stub this
    seam, or the dispatch function itself."""
    from .browser_dispatch import browser_click
    return browser_click(target, by=by, confirm=bool(confirm))


def _cdp_type(target, text, by, confirm):
    """CDP act seam — the real Playwright fill via browser_type
    (core/browser_dispatch.py:163). Same lazy-import shape as
    _cdp_click."""
    from .browser_dispatch import browser_type
    return browser_type(target, text, by=by, confirm=bool(confirm))


def _cdp_press(key, confirm):
    """CDP act seam — the real Playwright key press via
    browser_press (core/browser_dispatch.py:191). Same
    lazy-import shape as _cdp_click."""
    from .browser_dispatch import browser_press
    return browser_press(key, confirm=bool(confirm))


def _cdp_navigate(url):
    """CDP act seam — the real Playwright navigation via
    browser_control("goto", url=...) (core/browser_dispatch.py:51).
    Same lazy-import shape as _cdp_click."""
    from .browser_dispatch import browser_control
    return browser_control("goto", url=url)


def _cdp_read(query):
    """CDP act seam — the real page read via browser_read
    (core/browser_dispatch.py:112). No query reads the whole
    current page ("text"); a query searches the page for that
    text ("find") and returns the matching passages."""
    from .browser_dispatch import browser_read
    if str(query or "").strip():
        return browser_read("find", query=query)
    return browser_read("text")


def _cdp_result_text(result):
    """The browser_* result as plain text: browser_click returns a
    string; browser_type / browser_press return a string or an
    Outcome, which formats as its message string."""
    if isinstance(result, str):
        return result
    try:
        return str(result)
    except Exception:  # noqa: BLE001 — unreadable: no claim
        return ""


def _cdp_click_outcome(result, *, action, target, by):
    """Translate a browser_click string into the click_anywhere
    dict. A landed click starts with 'Clicked' — the in-tree
    precedent (music_player_spotify.py judges the same prefix).
    Anything else is an honest failure carrying the browser's
    own words: a miss, a no-guess list, a login wall, or a lost
    connection never reads as a click."""
    text = _cdp_result_text(result)
    if text.startswith("Clicked"):
        return _ok({"action": action, "target": target, "by": by,
                    "route": "cdp", "status": "clicked",
                    "report": text})
    return _fail(text or "the browser click returned no result",
                 rule="cdp-click-failed")


def _cdp_type_outcome(result, *, action, target, by):
    """Translate a browser_type result. A landed fill starts with
    'Filled' — except the read-back mismatch, which starts the
    same way but says the fill did not land as typed."""
    text = _cdp_result_text(result)
    if text.startswith("Filled") and "did not land as typed" not in text:
        return _ok({"action": action, "target": target, "by": by,
                    "route": "cdp", "status": "typed",
                    "report": text})
    return _fail(text or "the browser type returned no result",
                 rule="cdp-type-failed")


def _cdp_press_outcome(result, *, action, target, by):
    """Translate a browser_press result. A dispatched key starts
    with 'Pressed'."""
    text = _cdp_result_text(result)
    if text.startswith("Pressed"):
        return _ok({"action": action, "target": target, "by": by,
                    "route": "cdp", "status": "pressed",
                    "report": text})
    return _fail(text or "the browser press returned no result",
                 rule="cdp-press-failed")


def _cdp_navigate_outcome(result, *, action, target, by):
    """Translate a browser_control("goto") string into the
    click_anywhere dict. A landed navigation starts with
    'Opened' — or 'Still loading', the soft timeout: the page
    is under way and the connection is fine, so the act landed
    and the browser's own words say to wait or read. Anything
    else is an honest failure carrying the browser's own words:
    a refused scheme, a failed open, or a dead session never
    reads as a navigation."""
    text = _cdp_result_text(result)
    if text.startswith("Opened ") or text.startswith("Still loading "):
        return _ok({"action": action, "target": target, "by": by,
                    "route": "cdp", "status": "navigated",
                    "report": text})
    return _fail(text or "the browser navigation returned no result",
                 rule="cdp-navigate-failed")


def _cdp_read_outcome(result, *, action, target, by):
    """Translate a browser_read string into the click_anywhere
    dict. A read has no single landed prefix — the page text
    itself is the result — so the translator fails on the
    known failure shapes and otherwise carries the browser's
    own words: a failed read and a dead session fail honestly;
    an empty page and a find with no hits are completed reads
    whose absence IS the answer."""
    text = _cdp_result_text(result)
    if text.startswith("Browser action failed") or text.startswith(
            "No controllable browser session is active."):
        return _fail(text or "the browser read returned no result",
                     rule="cdp-read-failed")
    return _ok({"action": action, "target": target, "by": by,
                "route": "cdp", "status": "read",
                "report": text})


def _cdp_act(action, target, by, role, name, text, confirm):
    """The CDP act (hunt fix #2b, extended by #2c, 2026-11-16):
    on a live CDP route, click/type/press/navigate/read actually
    act through browser_click / browser_type / browser_press /
    browser_control("goto") / browser_read instead of returning
    'routed' and doing nothing.

    Mapping (click_anywhere `by` -> browser_* `by`):
    - click selector -> selector (1:1).
    - click dom / uia_name -> role_button when the role is a
      button, else text: both name the element by its accessible
      name / visible text, which is what the DOM and UIA rungs
      identify.
    - click ocr -> text: OCR reads visible text; get_by_text
      matches it.
    - click/type/press point -> the C4 ask, mirrored from the
      sight/desktop branch: a raw point asks first; only a click
      re-issued with confirm=true acts (through browser_click
      by='point'). A type/press at a point keeps asking, exactly
      as on sight — it has no act of its own.
    - click mark -> NO mapping (a grounding mark is not a
      browser_read ref): a clear fail, never a guess.
    - type selector -> selector (1:1); type dom / uia_name ->
      label (the field's label is the browser-side name of a
      DOM/UIA field); type ocr / mark -> a clear fail, never a
      guessed fill.
    - press needs no locator: the key in `text` maps straight
      onto browser_press (any non-point `by`).
    - navigate -> browser_control("goto", url=target): the
      target IS the URL. An empty target is a clear fail, never
      a call with an empty URL. A landed navigation starts with
      'Opened' (or 'Still loading', the soft timeout — the page
      is under way, the connection is fine).
    - read -> browser_read: no target reads the whole current
      page ("text"); a target searches the page for that text
      ("find") and returns the matching passages. A find with
      no hits is a completed read, not a failure.

    Confirm: only the raw point rung reads click_anywhere's
    confirm (C4) — every other rung passes False, so the
    funnel's confirm gate stays the first ask and the
    browser_* trust gates stay the second. A browser result
    that did not land fails with the browser's own words."""
    by = str(by or "dom").strip().lower()
    confirm_yes = str(confirm).strip().lower() in ("true", "1", "yes", "on")
    if by == "point":
        if action == "click" and confirm_yes:
            return _cdp_click_outcome(
                _cdp_click(target, "point", True),
                action=action, target=target, by=by)
        return _ok({"action": action, "target": target,
                    "route": "cdp", "status": "ask",
                    "announce": RAW_COORDINATE_ANNOUNCE,
                    "report": (RAW_COORDINATE_ANNOUNCE + ": the "
                               "only coordinate is a raw screen "
                               "point — confirm before I click it "
                               "(re-issue this call with "
                               "confirm=true to proceed)")})
    if action == "click":
        if by == "mark":
            return _fail("a grounding mark needs a fresh observation "
                         "first — click by text, by the button's "
                         "name, or by selector on the CDP route",
                         rule="cdp-mark-needs-observation")
        element = (str(target or "").strip()
                   or str(name or "").strip()
                   or str(text or "").strip())
        if by == "selector":
            click_by = "selector"
        elif by == "ocr":
            click_by = "text"
        elif by in ("dom", "uia_name"):
            if str(role or "").strip().lower() == "button":
                click_by = "role_button"
            else:
                click_by = "text"
        else:
            return _fail("unknown locator %r for a CDP click" % by,
                         rule="cdp-unknown-by")
        return _cdp_click_outcome(
            _cdp_click(element, click_by, False),
            action=action, target=target, by=by)
    if action == "type":
        if by in ("dom", "uia_name"):
            type_by = "label"
        elif by == "selector":
            type_by = "selector"
        else:
            return _fail("a %s type has no clean mapping onto "
                         "browser_type (it types by label or "
                         "selector) — type by the field's label or "
                         "selector on the CDP route" % by,
                         rule="cdp-type-unmapped-by")
        field = (str(target or "").strip()
                 or str(name or "").strip())
        return _cdp_type_outcome(
            _cdp_type(field, text, type_by, False),
            action=action, target=target, by=by)
    if action == "press":
        return _cdp_press_outcome(
            _cdp_press(text, False),
            action=action, target=target, by=by)
    if action == "navigate":
        url = str(target or "").strip()
        if not url:
            return _fail("a navigate needs a URL to go to — "
                         "refusing without one",
                         rule="cdp-navigate-needs-url")
        return _cdp_navigate_outcome(
            _cdp_navigate(url),
            action=action, target=target, by=by)
    if action == "read":
        return _cdp_read_outcome(
            _cdp_read(target),
            action=action, target=target, by=by)
    return _fail("unknown action %r for the CDP route" % action,
                 rule="cdp-unknown-action")


# ---------------------------------------------------------------------------
# The tool handler
# ---------------------------------------------------------------------------

def click_anywhere(action, target="", by="dom", action_class="",
                   role="", name="", text="", browser="chrome",
                   route=None, confirm=False):
    """The `click_anywhere` handler. One tool, all routes.

    observe -> the browser or desktop observe (route "desktop"
    observes the desktop; any other route observes the browser).
    click/type/press/navigate/read -> the guards run first
    (D-17, D-18 backstop, password rule), then the route
    resolves. A NAMED route that is dead pauses the goal (M-29)
    with the route-death reason and what he must do — never a
    silent fall-through. When NO route is named, the first live
    route in the documented order (extension, then CDP, then
    sight) is used instead — hunt #2: the old default named the
    extension route, which is dead, so every default click
    paused even with the browser connected. A click also needs
    the element's name (the gate-side backstop's input); a
    missing name refuses.
    """
    action = str(action or "").strip().lower()
    if action not in ACTIONS:
        return _fail("unknown action %r (expected one of %s)"
                     % (action, ", ".join(ACTIONS)))
    by = str(by or "dom").strip().lower()
    browser = str(browser or "chrome").strip().lower()
    route_given = route is not None and str(route).strip() != ""
    route = str(route or "").strip().lower() or None
    if action == "observe":
        if route == "desktop":
            return _ok(desktop_observe())
        return _ok(browser_observe())
    if action == "click" and not str(name or "").strip() \
            and not str(text or "").strip():
        return _fail("a click needs the element's accessible name or "
                     "text (role + name/text are the gate-side "
                     "backstop's input) — refusing without it",
                     rule="Q-12-name-required")
    if action in ("click", "type", "press"):
        allowed, outcome = guard_click(
            name=name, text=text, role=role, action_class=action_class)
        if not allowed:
            return outcome
    if action == "type" and str(target or "").strip():
        # Password rule (M-38): the DOM type path checks
        # type=password via DOM before any DOM-set or type.
        # The funnel type dispatch routes through the same check
        # (core/desktop/uia_type.py:225,
        # core/desktop/control_input.py:42); here the target is
        # a DOM node dict when the picker supplies one.
        if isinstance(target, dict):
            try:
                from . import browser_scope as _scope
                if _scope.dom_input_is_password(target):
                    return _fail(
                        "that's a password field — passwords are "
                        "yours to type, I won't (M-38)",
                        rule="M-38-password")
            except Exception:  # noqa: BLE001 — unreadable: proceed
                pass
    if route_given:
        live_route, death = resolve_route(route, browser=browser)
    else:
        # Hunt #2: no route named — use the first live route in
        # the documented order instead of defaulting to the dead
        # extension route.
        live_route, death = resolve_default_route(browser=browser)
    if death is not None:
        return death
    if action in ("navigate", "read"):
        # Hunt fix #2c (2026-11-16): navigate/read ACT on a live
        # CDP route through browser_control("goto") / browser_read
        # instead of returning "routed" and doing nothing. Any
        # other live route has no navigate/read act wiring:
        # refuse honestly rather than returning a "routed" no-op
        # — sight observes the desktop, it cannot navigate a
        # browser, and the extension route has no act wiring
        # until the browser add-on host lands.
        if live_route == "cdp":
            return _cdp_act(action, target, by, role, name, text,
                            confirm)
        return _fail("the %s route is live but has no %s act wired "
                     "yet — nothing was done" % (live_route, action),
                     rule="route-act-unwired", route=live_route)
    # click / type / press on a live route: the act itself goes
    # through the visible click path (desktop) or the route's own
    # act (browser routes). The sight and desktop routes click
    # visibly; the CDP route acts at DOM level through
    # browser_click / browser_type / browser_press (hunt fix #2b);
    # the extension route has no act wiring until the browser
    # add-on host lands.
    if live_route in ("sight", "desktop"):
        if by == "point":
            try:
                x_text, y_text = str(target or "").split(",", 1)
                picked = {"status": "pick", "rung": "raw",
                          "candidate": {"x": int(x_text),
                                        "y": int(y_text)}}
            except (ValueError, TypeError):
                return _fail("the raw-coordinates rung needs a "
                             "target of the form 'x,y'",
                             rule="raw-point-shape")
            if action == "click" and str(confirm).strip().lower() \
                    in ("true", "1", "yes", "on"):
                # Voice-control batch (2026-11-16, C4): the
                # confirm-first ask finally has a resolver — the
                # model re-issues with confirm=true after the
                # ask, exactly like browser_click's confirm
                # parameter, and the visible click runs HERE
                # (visible_click is the sight/desktop act path;
                # falling through would return needs-observation
                # and click nothing). The funnel's own confirm
                # gate still asks first for Tier-2 calls, so this
                # is the second, raw-point-specific yes. Only
                # clicks resolve here: a type/press at a raw
                # point needs its own act wiring, so those keep
                # asking rather than half-acting.
                ok, detail = visible_click(int(x_text), int(y_text))
                if not ok:
                    return _fail(detail.get("reason", "the visible "
                            "click failed"), rule="visible-click-failed")
                return _ok({"action": action, "target": target,
                            "route": live_route, "status": "clicked",
                            "point": detail.get("point"),
                            "report": ("clicked the raw point (%s, %s) "
                                       "visibly via the %s route"
                                       % (x_text.strip(), y_text.strip(),
                                          live_route))})
            return _ok({"action": action, "target": target,
                        "route": live_route, "status": "ask",
                        "announce": RAW_COORDINATE_ANNOUNCE,
                        "report": (RAW_COORDINATE_ANNOUNCE + ": the "
                                   "only coordinate is a raw screen "
                                   "point — confirm before I click it "
                                   "(re-issue this call with "
                                   "confirm=true to proceed)")})
        # Field check B2 (2026-11-17): a needs-observation
        # return clicked nothing, so it must NOT read as success
        # — a goal runner reading ok:true treats it as progress
        # and moves on with nothing clicked. The pick guidance
        # stays in the reason and report so the model knows the
        # next step (pick_browser / pick_desktop first); the
        # status stays needs-observation so the caller can tell
        # "pick first" apart from a hard failure.
        return _fail("the %s route is live — pick from a fresh "
                     "observation first (pick_browser / pick_desktop), "
                     "then click the picked point visibly"
                     % live_route,
                     status="needs-observation", action=action,
                     target=target, by=by, route=live_route,
                     report=("the %s route is live — pick from a "
                             "fresh observation first (pick_browser "
                             "/ pick_desktop), then click the "
                             "picked point visibly" % live_route))
    if live_route == "cdp":
        return _cdp_act(action, target, by, role, name, text, confirm)
    # Any other live route (today only the extension, if a probe
    # is ever registered) has no act wiring: refuse honestly
    # rather than returning a "routed" no-op.
    return _fail("the %s route is live but has no click act wired "
                 "yet — the browser add-on host has not landed, so "
                 "nothing was clicked" % live_route,
                 rule="route-act-unwired")


# ---------------------------------------------------------------------------
# VS Code test case (§6)
# ---------------------------------------------------------------------------

def rename_symbol_refusal() -> str:
    """The rename-symbol refusal, verbatim (M-48). Never a partial
    text rename (it corrupts references)."""
    return RENAME_SYMBOL_REFUSAL


def code_workspace_swap_warning(step) -> str:
    """The workspace-swap warning (N-22) for a plan step, or "".

    A `code -r` step carries it into the plan-approval dialog's
    covered-steps list before the first `code` call: "this will
    replace the current workspace in that window". The dialog
    renders it from preview()'s entry ("warning" key); the
    integration point is gui/plan_approval_dialog.py's
    build_plan_approval_dialog, which renders the covered-steps
    list from preview()."""
    tool = ""
    args = {}
    if isinstance(step, dict):
        tool = str(step.get("tool", "") or "")
        args = step.get("args") or {}
    else:
        tool = str(getattr(step, "tool", "") or "")
        args = getattr(step, "args", {}) or {}
    if tool.strip() != "run_command":
        return ""
    command = ""
    if isinstance(args, dict):
        command = str(args.get("command", "") or "")
    if command.strip().startswith("code -r"):
        return CODE_WORKSPACE_SWAP_WARNING
    return ""


def probe_code_install(runner=None):
    """The `code` probe (M-44): `where code` + `code --version`
    with full paths. Prefers the PATH-resolved install, else the
    user-level (%LOCALAPPDATA%) install over system. Records the
    choice in the returned record. Refuses only when the goal
    named an install that is absent.

    ``runner`` is an injectable (cmd -> {"ok", "out"}) for tests;
    the default runs the commands on this machine (Windows-only
    `where`; elsewhere the probe reports itself unavailable)."""
    import os
    import shutil
    import subprocess

    def _default_runner(cmd):
        try:
            done = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=15)
            return {"ok": done.returncode == 0,
                    "out": (done.stdout or "") + (done.stderr or "")}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "out": str(exc)}

    run = runner or _default_runner
    found = []
    where = run(["where", "code"] if os.name == "nt" else ["which", "code"])
    if where.get("ok"):
        for line in str(where.get("out") or "").splitlines():
            path = line.strip()
            if path:
                found.append(path)
    if not found:
        resolved = shutil.which("code")
        if resolved:
            found.append(resolved)
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    def _rank(path):
        lowered = str(path).lower()
        if local_appdata and lowered.startswith(
                local_appdata.lower()):
            return 1
        return 0 if found and path == found[0] else 2
    found = sorted(set(found), key=_rank)
    if not found:
        return {"ok": False, "installed": False,
                "reason": ("no `code` install found — refuse only "
                           "when the goal named an install that is "
                           "absent; no install was named here, so "
                           "this is the honest unavailability, not "
                           "a refusal of a named install")}
    version = run([found[0], "--version"])
    return {"ok": True, "installed": True, "path": found[0],
            "candidates": found,
            "version": str(version.get("out") or "").splitlines()[:1],
            "choice": ("PATH-resolved" if found[0] == (shutil.which("code") or "")
                       else "user-level (%LOCALAPPDATA%)"
                       if local_appdata and found[0].lower().startswith(
                           local_appdata.lower())
                       else "first found")}


def vscode_title_matches(title, target) -> bool:
    """The title rule (Q-3): the OS title must end with
    " - Visual Studio Code" AND contain the target file/folder
    name as a substring (the OS title is "<file> - <folder> -
    Visual Studio Code"; exact equality would never match).
    An empty title never matches (R-25)."""
    if not isinstance(title, str) or not title.strip():
        return False
    if not title.endswith(" - Visual Studio Code"):
        return False
    if not isinstance(target, str) or not target.strip():
        return False
    return target.strip() in title


def discover_vscode_window(list_windows, target, *, settle_s=1.0,
                           retries=3, sleep=None):
    """Post-`code` window discovery (R-18): after a `code` call,
    wait a settle delay of 1 s, then retry the window-list match
    up to 3 times at 1 s intervals before refusing with "VS Code
    window not found". ``list_windows`` returns [{title, proc,
    hwnd}]; an empty title refuses with "tab title empty — cannot
    identify the window" (R-25); ambiguous (two windows with the
    same folder open) refuses + asks with the candidates named.
    ``sleep`` is injectable for tests (defaults to time.sleep)."""
    import time as _time
    _sleep = sleep or _time.sleep
    _sleep(settle_s)
    last_error = None
    for _attempt in range(max(1, retries)):
        try:
            windows = list_windows() or []
        except Exception as exc:  # noqa: BLE001
            windows = []
            last_error = str(exc)
        matches = []
        empty_title = False
        for window in windows:
            if not isinstance(window, dict):
                continue
            title = window.get("title")
            if not isinstance(title, str) or not title.strip():
                empty_title = True
                continue
            if str(window.get("proc") or "") not in ("Code.exe", "code"):
                if window.get("proc"):
                    continue
            if vscode_title_matches(title, target):
                matches.append(window)
        if len(matches) == 1:
            return {"ok": True, "window": matches[0],
                    "hwnd": matches[0].get("hwnd")}
        if len(matches) > 1:
            return {"ok": False, "status": "ask",
                    "reason": ("ambiguous: two VS Code windows match "
                               "%r — which one? %s"
                               % (target, "; ".join(
                                   str(w.get("title")) for w in matches))),
                    "candidates": matches}
        if empty_title:
            return {"ok": False, "status": "refused",
                    "reason": "tab title empty — cannot identify the "
                              "window"}
        last_error = "VS Code window not found"
        _sleep(1.0)
    return {"ok": False, "status": "refused",
            "reason": last_error or "VS Code window not found"}


def check_pinned_window(pinned_hwnd, current_hwnd):
    """Window pinning (M-42/M-43): before each `code` call the
    last-active window must still be the pinned one (the handle
    discovered after the `code` call). A mismatch refuses + asks."""
    if pinned_hwnd is None or current_hwnd is None:
        return {"ok": False, "status": "refused",
                "reason": "no pinned window to check against — "
                          "pin the window first"}
    if int(pinned_hwnd) != int(current_hwnd):
        return {"ok": False, "status": "ask",
                "reason": ("the active window (%s) is no longer the "
                           "pinned VS Code window (%s) — refuse + ask"
                           % (current_hwnd, pinned_hwnd))}
    return {"ok": True}


def vscode_action_requires_confirm(action_name) -> bool:
    """True when a VS Code adapter action is confirm-gated at
    dispatch (M-55): "delete file" and "close without saving"
    are confirm-gated in the adapter's own record AND enforced
    at dispatch by app_adapters.resolve() (confirm_actions;
    unconfirmed resolve stops with needs_confirmation=True).
    No "format document" or "rename symbol" exists in the
    vocabulary at all."""
    try:
        from .desktop.vscode_adapter import vscode_adapter_record as _rec
        record = _rec()
    except Exception:  # noqa: BLE001
        try:
            from core.desktop.vscode_adapter import (
                vscode_adapter_record as _rec)
            record = _rec()
        except Exception:  # noqa: BLE001 — unreadable: ask
            return True
    confirm = record.get("confirm_actions") or []
    vocab = record.get("vocabulary") or {}
    wanted = str(action_name or "").strip().lower()
    for entry in confirm:
        if str(entry).strip().lower().endswith(wanted):
            return True
    for key, value in vocab.items():
        if str(key).strip().lower() != wanted:
            continue
        if isinstance(value, dict):
            kind = str(value.get("kind") or "")
            name = str(value.get("name") or key)
            label = "%s:%s" % (kind, name)
            if label in [str(c) for c in confirm]:
                return True
    return False


# ---------------------------------------------------------------------------
# Two-sided verify (P-14) + tolerant read-back (M-47)
# ---------------------------------------------------------------------------

def tolerant_readback_equal(expected: bytes, actual: bytes) -> bool:
    """The post-hoc READ-BACK verify (M-47, tolerant): line
    endings normalised to LF, trailing whitespace stripped per
    line, at most one trailing-newline difference at EOF
    tolerated; anything else must match exactly."""
    def _norm(data: bytes):
        text = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        lines = [line.rstrip() for line in text.split(b"\n")]
        while lines and lines[-1] == b"":
            lines.pop()
        return lines
    return _norm(expected) == _norm(actual)


def verify_write_two_sided(path, expected_bytes):
    """The two-sided verify (P-14): the byte-exact write verify
    FIRST, then the tolerant read-back (M-47). A line-ending
    change from the write fails byte-exact before the tolerant
    check ever runs; the tolerant check only absorbs
    editor-induced normalisation between the write and the
    read-back (format-on-save). Returns {"byte_exact": bool,
    "tolerant": bool, "verdict": ...}."""
    import pathlib
    try:
        actual = pathlib.Path(path).read_bytes()
    except Exception as exc:  # noqa: BLE001 — unreadable: fail both
        return {"byte_exact": False, "tolerant": False,
                "verdict": "failed", "reason": "cannot read back %s: %s"
                           % (path, exc)}
    byte_exact = actual == expected_bytes
    if not byte_exact:
        return {"byte_exact": False,
                "tolerant": tolerant_readback_equal(expected_bytes, actual),
                "verdict": "failed",
                "reason": ("byte-exact write verify failed — a "
                           "line-ending change from the write fails "
                           "here, before the tolerant check runs")}
    return {"byte_exact": True,
            "tolerant": tolerant_readback_equal(expected_bytes, actual),
            "verdict": "passed"}


# ---------------------------------------------------------------------------
# Funnel registration (social_send pattern)
# ---------------------------------------------------------------------------

def register_all(registry):
    """Register click_anywhere with the funnel. The registry record
    carries ``playbook_opt_out: True`` (M-24): run_goal's
    playbook-match step checks the record before any replayed step
    runs, and the lesson filter excludes this tool's args entirely.
    """
    registry.register(
        name="click_anywhere",
        description=_TOOL_DESCRIPTION,
        parameters=_PARAMETERS,
        handler=click_anywhere,
    )
    try:
        record = registry._tools.get("click_anywhere")
        if isinstance(record, dict):
            record["playbook_opt_out"] = True
    except Exception:  # noqa: BLE001 — a registry that cannot
        # carry the flag still has the tool; is_playbook_opted_out
        # falls back to the module-level opt-out set.
        pass

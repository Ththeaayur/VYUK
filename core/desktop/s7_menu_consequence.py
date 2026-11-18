"""Menu consequence verifier — §7 consumers batch 4.

After a coordinator menu step invokes a path, the verification
phase must judge the step's declared consequence honestly. The
existing ``core.desktop.uia.verify_expectation`` engine already grounds
two shapes against before/after desktop snapshots — a window or
dialog titled X appearing, and text X present or appearing — and
this module reuses it first, never rebuilding it. It adds only
the consequence shapes that engine cannot ground, and only when
the generic engine returns "unverifiable" for shape (never to
override a grounded met / not-met verdict):

* foreground-changed-to — "the foreground is now X" / "switched
  to X": the foreground window changed to X. A bare "foreground
  is X" is the present-style variant (X is the foreground now,
  whether or not it changed).
* window-closed — "dialog X closed" / "window X disappeared" /
  "X is gone": X was present before the step and is absent
  after it.
* menu-opened — "the X menu opened" / "submenu X is open": the
  invoked path opened a submenu (the engine's own expansion of
  that submenu, with its children listed, IS the consequence —
  a desktop snapshot carries no menu-open state to check
  instead). Judged from the invoke result: an engine-shaped
  result (action "opened", path segments) or the funnel's
  rendered describe line ("Menu path … opened"). An invoke
  that ran a command instead of opening a menu is not-met for
  this shape, never a silent pass.
* dialog-opened — "the Save As dialog opened": rewritten to the
  generic engine's own "a dialog titled X appears" shape and
  delegated to it, never judged here (the generic parser needs
  the titled phrasing; this is the one menu-natural phrasing it
  cannot read).

Verdicts use the same vocabulary as the generic engine — "met"
/ "not-met" / "unverifiable" — and the same honesty rule: a
consequence that cannot be grounded is "unverifiable", never a
guessed "met". The backend's "verified" flag never auto-mets a
named consequence: window / text / foreground / closed shapes
still need snapshot evidence, and menu-opened needs the opened
path itself. When no expect is declared, this module is never
consulted — the step's outcome message already carries the
engine's own verification note ("verified" or
"invoked-without-verification") verbatim.

Snapshots here are the coordinator's desktop snapshots (fg,
focus, windows, texts, tree_hash — the same fields
``core.uia_verify`` reads). A None snapshot is unobserved
evidence, never an empty desktop.
"""

from __future__ import annotations

import re

__all__ = ["verify_menu_consequence", "parse_menu_consequence"]


def _clean(text) -> str:
    return " ".join(str(text or "").split()).strip()


def _fg_windows(snap):
    """(foreground, windows-list) from a desktop snapshot, or (None, [])."""
    if not isinstance(snap, dict):
        return None, []
    fg = snap.get("fg")
    fg = str(fg).strip() if fg is not None and str(fg).strip() else None
    windows = [str(w) for w in (snap.get("windows") or [])
               if str(w or "").strip()]
    return fg, windows


def _contains(haystack, target) -> bool:
    return target.lower() in str(haystack or "").lower()


def _present(target, fg, windows) -> bool:
    if fg is not None and _contains(fg, target):
        return True
    return any(_contains(w, target) for w in windows)


def _observed(fg, windows) -> bool:
    return fg is not None or bool(windows)


# ---------------------------------------------------------------------------
# Menu-specific consequence shapes
# ---------------------------------------------------------------------------

_FG_CHANGE = re.compile(
    r"foreground\s+(?:is\s+now|changed\s+to|switched\s+to)\s+(.+)$",
    re.IGNORECASE)
_FG_SWITCHED = re.compile(r"switched\s+to\s+(.+)$", re.IGNORECASE)
_FG_PRESENT = re.compile(r"foreground\s+is\s+(.+)$", re.IGNORECASE)
_CLOSED = re.compile(
    r"(?:dialog|window|popup|alert|message\s+box)\s+"
    r"(?:titled\s+|named\s+|called\s+)?['\"]?(.+?)['\"]?\s+"
    r"(?:closed|disappeared|is\s+gone|went\s+away|no\s+longer\s+present)"
    r"\s*$",
    re.IGNORECASE)
_CLOSED_BARE = re.compile(
    r"^['\"]?(.+?)['\"]?\s+"
    r"(?:closed|disappeared|is\s+gone|went\s+away|no\s+longer\s+present)"
    r"\s*$",
    re.IGNORECASE)
_OPENED = re.compile(
    r"(?:the\s+)?(.+?)\s+(?:menu|submenu)\s+"
    r"(?:opened|is\s+open|opens)\s*$",
    re.IGNORECASE)
_OPENED_BARE = re.compile(
    r"^(?:menu|submenu)\s+(.+?)\s+(?:opened|is\s+open|opens)\s*$",
    re.IGNORECASE)
# "the Save As dialog opened" / "dialog Save As opened" — the one
# dialog-opened phrasing the generic engine cannot parse (it needs
# "a dialog titled X appears"). Rewritten to that shape and
# delegated to the generic engine, never judged here.
_DIALOG_OPENED = re.compile(
    r"(?:the\s+)?(.+?)\s+"
    r"(?:dialog|window|popup|alert|message\s+box)\s+"
    r"(?:opened|is\s+open|opens)\s*$",
    re.IGNORECASE)
_DIALOG_OPENED_LEAD = re.compile(
    r"^(?:dialog|window|popup|alert|message\s+box)\s+"
    r"(?:titled\s+|named\s+|called\s+)?['\"]?(.+?)['\"]?\s+"
    r"(?:opened|is\s+open|opens)\s*$",
    re.IGNORECASE)

_GENERIC_WORDS = ("something", "anything", "it", "a window", "the window",
                  "a dialog", "the dialog", "a menu", "the menu")


def _target(raw):
    target = _clean(raw).strip("'\"").rstrip(".").strip()
    if not target or target.lower() in _GENERIC_WORDS:
        return None
    return target


def parse_menu_consequence(text):
    """(kind, target, style) for a menu-specific consequence, or None.

    kind is "foreground-change" (change-style), "foreground"
    (present-style), "closed", or "menu-opened". Returns None
    when the text names no menu-specific shape — the caller then
    falls back to the generic engine's own verdict, never a guess.
    """
    t = _clean(text).rstrip(".")
    if not t:
        return None
    m = _FG_CHANGE.search(t) or _FG_SWITCHED.search(t)
    if m:
        target = _target(m.group(1))
        return ("foreground-change", target, "change") if target else None
    m = _FG_PRESENT.search(t)
    if m:
        target = _target(m.group(1))
        return ("foreground", target, "present") if target else None
    m = _CLOSED.search(t) or _CLOSED_BARE.search(t)
    if m:
        target = _target(m.group(1))
        return ("closed", target, "gone") if target else None
    m = _OPENED.search(t) or _OPENED_BARE.search(t)
    if m:
        target = _target(m.group(1))
        return ("menu-opened", target, "opened") if target else None
    return None


def _opened_path(invoke_result):
    """(action, path-segments) from an invoke result, or (None, []).

    Accepts an engine-shaped dict (action + path list), a funnel
    dict whose result is either, or a rendered describe line
    ("Menu path A → B opened/invoked …"). Anything else yields
    no path — the caller reports unverifiable, never a guess.
    """
    result = invoke_result
    if isinstance(result, dict):
        inner = result.get("result", result)
        if isinstance(inner, dict):
            action = str(inner.get("action") or "").strip().lower()
            path = [str(s) for s in (inner.get("path") or [])
                    if str(s or "").strip()]
            if action in ("opened", "invoked"):
                return action, path
            if inner.get("opened"):
                return "opened", path
            if inner.get("invoked"):
                return "invoked", path
            return None, []
        result = inner
    if isinstance(result, str):
        m = re.search(r"Menu path\s+(.+?)\s+(opened|invoked)\b", result)
        if m:
            segments = [s.strip() for s in m.group(1).split("→")]
            segments = [s for s in segments if s]
            return m.group(2), segments
    return None, []


def _judge_menu_shape(kind, target, style, before, after, invoke_result):
    """Verdict for one parsed menu shape, or None when ungroundable."""
    before_fg, before_windows = _fg_windows(before)
    after_fg, after_windows = _fg_windows(after)
    if kind == "foreground-change":
        if not _observed(after_fg, after_windows):
            return ("unverifiable",
                    f"the foreground could not be observed after the "
                    f"action, so a switch to '{target}' was not checked.")
        if _present(target, after_fg, after_windows) and not _present(
                target, before_fg, before_windows):
            return ("met",
                    f"the foreground is now '{after_fg}' — it changed "
                    f"to '{target}'.")
        if _present(target, after_fg, after_windows):
            return ("not-met",
                    f"'{target}' was already the foreground before the "
                    f"action and nothing changed — it did not newly "
                    f"become the foreground.")
        return ("not-met",
                f"the foreground after the action is "
                f"'{after_fg}' — it did not change to '{target}'.")
    if kind == "foreground":
        if not _observed(after_fg, after_windows):
            return ("unverifiable",
                    f"the foreground could not be observed after the "
                    f"action, so '{target}' was not checked.")
        if _present(target, after_fg, after_windows):
            return ("met", f"the foreground is '{after_fg}'.")
        return ("not-met",
                f"the foreground after the action is '{after_fg}' — "
                f"'{target}' is not the foreground.")
    if kind == "closed":
        if not _observed(before_fg, before_windows):
            return ("unverifiable",
                    f"'{target}' was not observed before the action, "
                    f"so its closing cannot be confirmed.")
        if not _observed(after_fg, after_windows):
            return ("unverifiable",
                    f"the desktop could not be observed after the "
                    f"action, so the closing of '{target}' was not "
                    f"checked.")
        if not _present(target, before_fg, before_windows):
            return ("unverifiable",
                    f"'{target}' was not present before the action, "
                    f"so its closing cannot be confirmed.")
        if _present(target, after_fg, after_windows):
            return ("not-met",
                    f"'{target}' is still present after the action "
                    f"(foreground: '{after_fg}').")
        return ("met",
                f"'{target}' was present before the action and is "
                f"absent after it — it closed.")
    if kind == "menu-opened":
        action, path = _opened_path(invoke_result)
        if action is None:
            return ("unverifiable",
                    f"no invoke result names an opened menu, so "
                    f"'{target}' opening was not checked.")
        if action == "invoked":
            return ("not-met",
                    f"the menu path ran a command instead of opening "
                    f"a menu — '{target}' did not open.")
        # Exact-name match (case-insensitive, accelerator-stripped),
        # mirroring the engine: a segment is genuinely open only
        # when the walk expanded that exact name — substring
        # matches ("View" vs "Overview") never count.
        cleaned = [s.replace("&", "").strip().casefold() for s in path]
        if target.replace("&", "").strip().casefold() in cleaned:
            return ("met",
                    f"the menu path {' → '.join(path)} opened — "
                    f"'{target}' is open.")
        return ("not-met",
                f"the menu path that opened is "
                f"{' → '.join(path) if path else '(unnamed)'} — "
                f"'{target}' did not open.")
    return None


def verify_menu_consequence(expect_text, before, after, invoke_result=None):
    """Judge one menu step's declared consequence.

    Tries the generic ``core.desktop.uia.verify_expectation`` engine
    first (window / text shapes); a grounded met / not-met
    verdict stands. Only when the generic engine cannot ground
    the shape does this module try its menu-specific shapes
    (foreground change / present, window closed, menu opened).
    A consequence nothing can ground is "unverifiable" — never
    a guessed "met". ``invoke_result`` is the step's raw
    executor output (a funnel dict, an engine dict, or a
    rendered describe line); it grounds only the menu-opened
    shape, never the snapshot shapes.
    """
    result_text = ""
    if isinstance(invoke_result, dict):
        inner = invoke_result.get("result", "")
        if isinstance(inner, str):
            result_text = inner
    elif isinstance(invoke_result, str):
        result_text = invoke_result
    try:
        from . import uia_verify as _uv  # Phase 3 D2c: uia_verify now in core/desktop/; S7a-2 interim closed

        verdict, reason = _uv.verify_expectation(
            expect_text, before, after, result_text)
    except Exception as exc:  # noqa: BLE001 — generic failure is unverifiable
        return ("unverifiable",
                f"verification callback failed: {exc}")
    verdict = str(verdict or "").strip().lower()
    if verdict in ("met", "not-met"):
        return (verdict, str(reason or ""))
    # Dialog-opened phrasing the generic engine cannot parse:
    # rewrite to its own window-appears shape and delegate —
    # the generic engine's verdict stands, never re-judged here.
    m = _DIALOG_OPENED_LEAD.search(_clean(expect_text).rstrip(".")) \
        or _DIALOG_OPENED.search(_clean(expect_text).rstrip("."))
    if m:
        target = _target(m.group(1))
        if target:
            try:
                verdict2, reason2 = _uv.verify_expectation(
                    f"a dialog titled '{target}' appears",
                    before, after, result_text)
            except Exception as exc:  # noqa: BLE001
                return ("unverifiable",
                        f"verification callback failed: {exc}")
            verdict2 = str(verdict2 or "").strip().lower()
            if verdict2 in ("met", "not-met", "unverifiable"):
                return (verdict2, str(reason2 or ""))
    parsed = parse_menu_consequence(expect_text)
    if parsed is None:
        return ("unverifiable", str(reason or ""))
    judged = _judge_menu_shape(parsed[0], parsed[1], parsed[2],
                                before, after, invoke_result)
    if judged is None:
        return ("unverifiable", str(reason or ""))
    return judged

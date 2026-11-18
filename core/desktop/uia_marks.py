"""uia_marks concern extracted from core/desktop/uia.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/desktop/uia."""

from .uia_base import (_SCAN_CAP, _MATCH_CAP, _is_windows, _routed_backend, _truncate, _failsafe_block, _unavailable, _uia_window, _window_failure_text, _UIA_IS_PASSWORD_ID, _PASSWORD_NAME_RE, _password_name_re, _is_password_control, _control_usable, _TYPE_KEYS_SPECIALS, _escape_type_keys, _current_text, foreground_title, foreground_hwnd)
from .uia_discover import (_descendants, _element_facts, _find_matches, _format_facts, _point_element, describe_point, _focused_element, _focused_control_label, desktop_snapshot, _safe_snapshot, uia_find_control, uia_read_tree)


_INTERACTIVE_TYPES = frozenset({
    "Button", "CheckBox", "ComboBox", "DataItem", "Document",
    "Edit", "Hyperlink", "ListItem", "MenuItem", "RadioButton",
    "Slider", "Spinner", "SplitButton", "TabItem", "TreeItem",
})


_UNNAMED_MARK_TYPES = _INTERACTIVE_TYPES | {"Custom"}


_MARKS_CAP = 80


_last_marks: list = []


_last_marks_fg: str | None = None


_last_marks_hwnd: int | None = None


_KEEP = object()  # _set_last_marks sentinel: keep the stamped hwnd


def _element_rect(el):
    """(left, top, right, bottom) ints for one element, or None.

    Reads the rectangle object directly; falls back to parsing
    _element_facts' rect text ("(l,t)–(r,b)", en-dash) for
    wrappers that only surface the text form."""
    try:
        rect = el.element_info.rectangle
        if rect is not None:
            return (int(rect.left), int(rect.top),
                    int(rect.right), int(rect.bottom))
    except Exception:  # noqa: BLE001 — dead element mid-walk
        pass
    facts = _element_facts(el)
    if not facts:
        return None
    import re as _re

    m = _re.fullmatch(r"\((-?\d+),(-?\d+)\)\u2013\((-?\d+),(-?\d+)\)",
                      str(facts.get("rect") or ""))
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def _set_last_marks(marks, fg_title, fg_hwnd=_KEEP) -> None:
    """Record `marks` as the last grounding table (a copy), stamped
    with the foreground title they were observed under — and, since
    the desktop programme's F-07 fix, the foreground window's
    HANDLE too, so a same-title replacement window still reads as
    stale. Callers that re-stamp the same observation (vision's
    OCR-merge pass) omit fg_hwnd and keep the earlier stamp; a
    fresh observation passes foreground_hwnd() explicitly, and
    clearing passes None to wipe it."""
    global _last_marks, _last_marks_fg, _last_marks_hwnd
    _last_marks = [dict(m) for m in (marks or [])]
    _last_marks_fg = fg_title
    if fg_hwnd is not _KEEP:
        _last_marks_hwnd = fg_hwnd


def last_marks() -> list:
    """A copy of the last grounding marks table ([] when no
    grounding observation has happened yet)."""
    return [dict(m) for m in _last_marks]


def last_marks_foreground() -> str | None:
    """The foreground title the last marks table was taken under,
    or None when unknown / no table exists."""
    return _last_marks_fg


def last_marks_foreground_hwnd() -> int | None:
    """The foreground window handle the last marks table was taken
    under, or None when unknown (F-07 stamp)."""
    return _last_marks_hwnd


def _marks_stale_reason(wanted) -> str | None:
    """Why the last grounding table must not be clicked, or None
    when it is still fresh. Desktop programme, F-07: freshness is
    no longer title-only. A foreground window HANDLE that differs
    from the stamped handle refuses even when the title matches
    (a replaced window with the same title); when either handle
    is unknown the title rule is the fallback, exactly as before
    (a different title refuses, an unreadable title never counts
    as a change). Pure — tests drive it directly."""
    try:
        fg_now = foreground_title()
    except Exception:  # noqa: BLE001
        fg_now = None
    try:
        hwnd_now = foreground_hwnd()
    except Exception:  # noqa: BLE001
        hwnd_now = None
    if (_last_marks_hwnd is not None and hwnd_now is not None
            and hwnd_now != _last_marks_hwnd):
        return (f"Not clicking mark {wanted}: the grounding marks are "
                f"stale — they were drawn under a different window "
                f"than the one now in front (the foreground window "
                f"changed, even though its title is still "
                f"'{_last_marks_fg}'). Re-observe (take_screenshot "
                f"or screen_look) and pick a mark id from the fresh "
                f"table.")
    if _last_marks_fg and fg_now and fg_now != _last_marks_fg:
        return (f"Not clicking mark {wanted}: the grounding marks are "
                f"stale — they were drawn under '{_last_marks_fg}' but "
                f"the foreground window is now '{fg_now}'. "
                "Re-observe (take_screenshot or screen_look) and "
                "pick a mark id from the fresh table.")
    return None


def clear_last_marks() -> None:
    """Forget the last grounding table (a blocked or failed
    observation must never leave a stale table clickable)."""
    _set_last_marks([], None, None)


def _marks_capture_block() -> str | None:
    """The Phase-3 capture denylist (core/capture_guard) applied
    to grounding: a denylisted foreground yields NO marks, exactly
    as it yields no frame. Reuses foreground_capture_block() —
    never a parallel check. None when capture may proceed (an
    unknown foreground is not blocked, matching the guard)."""
    try:
        from .. import capture_guard  # Phase 3 D2c: file now in core/desktop/

        return capture_guard.foreground_capture_block()
    except Exception:  # noqa: BLE001 — a broken guard must not
        return None  # invent marks OR block honest ones


def _word_rect(word):
    """(left, top, right, bottom) from one OCR word dict — the
    core/desktop/control.py _ocr_image_words shape {text, left, top,
    right, bottom} (true screen pixels), or a {rect: (l,t,r,b)}
    shape. None when the word carries no usable box."""
    if not isinstance(word, dict):
        return None
    rect = word.get("rect")
    if rect is not None:
        try:
            vals = tuple(int(v) for v in rect)
            return vals if len(vals) == 4 else None
        except (TypeError, ValueError):
            return None
    try:
        return (int(word["left"]), int(word["top"]),
                int(word["right"]), int(word["bottom"]))
    except (KeyError, TypeError, ValueError):
        return None


def merge_ocr_marks(marks, ocr_words, cap: int = _MARKS_CAP):
    """Merge OCR word boxes into a marks table (pure).

    An OCR box whose CENTRE falls inside an existing mark's rect
    is dropped — UIA already grounds that spot, and the OCR word
    there is the control's own label. Survivors are appended with
    continuing ids, control_type "OCRText", source "ocr" (their
    name is the word's text as OCR read it — the pipeline's word
    dicts carry it lowercased). Returns a NEW list; ids of the
    incoming marks are preserved."""
    merged = [dict(m) for m in (marks or [])]
    rects = [m.get("rect") for m in merged]
    next_id = max((int(m.get("id") or 0) for m in merged), default=0) + 1
    for word in (ocr_words or []):
        if len(merged) >= cap:
            break
        rect = _word_rect(word)
        if rect is None or rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue
        text = str((word or {}).get("text") or "").strip()
        if not text:
            continue
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        if any(r is not None and r[0] <= cx <= r[2] and r[1] <= cy <= r[3]
               for r in rects):
            continue
        merged.append({"id": next_id, "name": text,
                       "control_type": "OCRText", "rect": rect,
                       "source": "ocr"})
        rects.append(rect)
        next_id += 1
    return merged


def enumerate_marks(window_title: str = "", ocr_words=None,
                    cap: int = _MARKS_CAP):
    """Enumerate the visible interactive controls of a window as a
    marks table: (marks, note).

    marks is a list of {"id", "name", "control_type", "rect",
    "source"} dicts — id 1-based in walk order, rect a true-pixel
    (left, top, right, bottom) tuple, source "uia" (or "ocr" for
    merged OCR boxes, see merge_ocr_marks). Desktop programme,
    F-06 layer 3: unnamed marks follow the named pass with
    continuing ids, source "uia_unnamed" and key "layer" =
    "unnamed" — a nameless, usable, bounded interactive-or-Custom
    control, named by position ("Unnamed Button at (x,y)").
    Non-interactive nameless elements earn no mark. Reuses this module's
    own descendant walk (_descendants / _element_facts /
    _control_usable): a control qualifies when it has a name, an
    interactive control type, a real on-screen rectangle, and is
    not positively disabled or hidden. The table (plus the current
    foreground title) is recorded as the last grounding table for
    click_by_mark. note is "" on a clean walk, else the honest
    reason the UIA half could not run (off-Windows, window gone) —
    OCR marks may still be returned alongside it.

    Phase-3 capture discipline: a denylisted foreground yields
    ([], block_reason) — NO marks at all, mirroring the frame
    rule in core/vision.py."""
    blocked = _marks_capture_block()
    if blocked is not None:
        clear_last_marks()
        return [], blocked
    marks: list = []
    note = ""
    unavailable = _unavailable()
    if unavailable:
        note = unavailable
    else:
        try:
            win, _desc = _uia_window(str(window_title or ""))
        except Exception as e:  # noqa: BLE001
            win, note = None, f"Could not connect to the target window: {e}"
        if win is not None:
            elements, failure = _descendants(win)
            if failure is not None:
                note = failure
            else:
                seen = set()
                for el in elements:
                    if len(marks) >= cap:
                        break
                    facts = _element_facts(el)
                    if facts is None or not facts["name"]:
                        continue
                    if facts["control_type"] not in _INTERACTIVE_TYPES:
                        continue
                    rect = _element_rect(el)
                    if rect is None or rect[2] <= rect[0] or rect[3] <= rect[1]:
                        continue
                    usable, _reason = _control_usable(el, facts)
                    if not usable:
                        continue
                    key = (facts["name"], facts["control_type"], rect)
                    if key in seen:
                        continue
                    seen.add(key)
                    marks.append({
                        "id": len(marks) + 1,
                        "name": facts["name"],
                        "control_type": facts["control_type"],
                        "rect": rect,
                        "source": "uia",
                    })
                # Desktop programme, F-06 layer 3 — unnamed
                # interactive controls: a second pass over the SAME
                # walk, so no new scan is needed. A control
                # qualifies only when it is nameless AND usable AND
                # bounded AND of an unnamed-mark type (interactive
                # + Custom); its rect must not already be covered
                # by a named mark, so one control never yields two
                # marks. Unnamed marks are appended AFTER the named
                # pass (named ids are untouched) and BEFORE the OCR
                # merge below, so an OCR word inside an unnamed
                # control's rect is dropped the same way words
                # inside named rects are — UIA grounds that spot.
                # Every unnamed mark discloses its layer: source
                # "uia_unnamed", key "layer" = "unnamed", and a
                # positional name ("Unnamed Button at (x,y)").
                # Non-interactive nameless elements (Pane, Text,
                # Window) earn nothing — and a surface with no UIA
                # children at all (a bare canvas, a game frame)
                # earns no mark from this layer either: that is
                # the honest limit, not a fabricated coordinate.
                named_rects = {m["rect"] for m in marks}
                unseen = set()
                for el in elements:
                    if len(marks) >= cap:
                        break
                    facts = _element_facts(el)
                    if facts is None or facts["name"]:
                        continue
                    if facts["control_type"] not in _UNNAMED_MARK_TYPES:
                        continue
                    rect = _element_rect(el)
                    if rect is None or rect[2] <= rect[0] or rect[3] <= rect[1]:
                        continue
                    if rect in named_rects:
                        continue
                    key = (facts["control_type"], rect)
                    if key in unseen:
                        continue
                    usable, _reason = _control_usable(el, facts)
                    if not usable:
                        continue
                    unseen.add(key)
                    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
                    marks.append({
                        "id": len(marks) + 1,
                        "name": (f"Unnamed {facts['control_type']} "
                                 f"at ({cx},{cy})"),
                        "control_type": facts["control_type"],
                        "rect": rect,
                        "source": "uia_unnamed",
                        "layer": "unnamed",
                    })
    if ocr_words:
        marks = merge_ocr_marks(marks, ocr_words, cap=cap)
    try:
        fg = foreground_title()
    except Exception:  # noqa: BLE001 — the stamp is best-effort
        fg = None
    try:
        fg_hwnd = foreground_hwnd()
    except Exception:  # noqa: BLE001 — the stamp is best-effort
        fg_hwnd = None
    _set_last_marks(marks, fg, fg_hwnd)
    return marks, note


def control_list_snapshot(window_title: str = "") -> dict:
    """The control-list snapshot source (wiring item 6, staged-module
    triage): the named snapshot of a window's visible interactive
    controls, built on enumerate_marks (the F-06 grounding walk).
    Returns {"controls": [...marks...], "note": ...} — controls are
    the marks table ({id, name, control_type, rect, source}), note
    is "" on a clean walk else the honest reason the walk could
    not run (off-Windows, window gone, denylisted foreground).
    Never raises: an unobservable window yields an empty list with
    the reason in note, never a fabricated control. Live consumers
    of control-list enumeration: vision grounding (click_by_mark)
    and the get_window_controls tool; control-existence checks in
    the verification phase are the named follow-up consumer (they
    need a verifier-contract change, not a second enumerator)."""
    try:
        marks, note = enumerate_marks(window_title)
    except Exception:  # noqa: BLE001 — snapshot never breaks a caller
        return {"controls": [], "note": "control enumeration unavailable"}
    return {"controls": marks, "note": note}

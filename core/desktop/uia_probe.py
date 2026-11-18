"""uia_probe concern extracted from core/desktop/uia.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/desktop/uia."""

from .uia_discover import (_descendants, _element_facts, _find_matches, _format_facts, _point_element, describe_point, _focused_element, _focused_control_label, desktop_snapshot, _safe_snapshot, uia_find_control, uia_read_tree)
from .uia_verify import (_expect_corpus, _corpus_contains, _parse_expectation, verify_expectation)


def _toggle_word(value) -> str | None:
    """Normalise a UIA ToggleState value to on/off/indeterminate."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (int, float)):
        return {0: "off", 1: "on", 2: "indeterminate"}.get(int(value))
    text = str(value or "").strip().lower()
    if not text:
        return None
    if "indeterminate" in text or text == "2":
        return "indeterminate"
    if text in ("on", "1", "true", "checked"):
        return "on"
    if text in ("off", "0", "false", "unchecked"):
        return "off"
    return None


def _control_state(el) -> str | None:
    """A control's observable state: 'on' / 'off' / 'indeterminate'
    from its Toggle pattern, else 'selected' / 'not selected' from
    its SelectionItem pattern, else None when the element exposes
    neither (or reading it failed). Never raises — an unreadable
    state is simply not evidence."""
    try:
        toggle = getattr(el, "iface_toggle", None)
        state = _toggle_word(getattr(toggle, "ToggleState", None))
        if state is not None:
            return state
    except Exception:  # noqa: BLE001
        pass
    try:
        getter = getattr(el, "get_toggle_state", None)
        if callable(getter):
            state = _toggle_word(getter())
            if state is not None:
                return state
    except Exception:  # noqa: BLE001
        pass
    try:
        sel = getattr(el, "iface_selection_item", None)
        flag = getattr(sel, "IsSelected", None)
        if isinstance(flag, bool):
            return "selected" if flag else "not selected"
    except Exception:  # noqa: BLE001
        pass
    return None


def _state_sentence(name: str, control_type: str, state: str) -> str:
    """The change sentence for a target whose state flipped."""
    if state in ("on", "off", "indeterminate"):
        kind = "checkbox" if control_type == "CheckBox" else "control"
        return f"The '{name}' {kind} is now {state}."
    if state == "selected":
        return f"The '{name}' item is now selected."
    return f"The '{name}' item is now deselected."


def _snapshot_change_sentence(before, after) -> str:
    """One sentence on what changed between two probe snapshots,
    or "" when nothing observably changed (or either snapshot is
    missing — an unreadable title is not a change)."""
    if not before or not after:
        return ""
    b_fg, a_fg = before.get("fg"), after.get("fg")
    if a_fg and b_fg and a_fg != b_fg:
        return f"The {a_fg} window came to front."
    b_focus, a_focus = before.get("focus"), after.get("focus")
    if a_focus and b_focus and a_focus != b_focus:
        return f"Focus moved to {a_focus}."
    return ""


def no_change_sentence(method: str = "uia") -> str:
    """The honest no-change sentence + the retry ladder's next
    rung (H5). Every variant names the rungs in ladder order —
    UIA by name, then OCR text (click_text), then raw
    coordinates — so the brain can follow the order from any
    starting point. `method` is the rung just tried ("uia",
    "ocr", "coords"); window actions aren't a ladder rung and
    get the plain observation sentence instead."""
    if method == "ocr":
        return (
            "No visible change. Retry ladder: UIA by name ran "
            "first and OCR text (click_text) was this step — the "
            "last rung left is raw coordinates (mouse_control or "
            "cursor_control)."
        )
    if method == "coords":
        return (
            "No visible change. The retry ladder is exhausted — "
            "UIA by name, then OCR text (click_text), then raw "
            "coordinates have all been tried; check what is "
            "actually on screen before trying again."
        )
    if method == "window":
        return (
            "No visible change in the foreground window, focus "
            "or screen was observed."
        )
    return (
        "No visible change. Retry ladder: this was the UIA-by-name "
        "step — next try OCR text (click_text), then raw "
        "coordinates (mouse_control or cursor_control)."
    )


def _screen_image(monitor_rect=None):
    """One full-screen PIL image via skills_window_control's capture seam
    (the screen_diff machinery), or None when capture is
    unavailable here. Never raises. DESK-01: monitor_rect (left,
    top, right, bottom in true screen pixels) scopes the probe
    frame to one monitor through the same seam screen_diff uses;
    None keeps today's primary-screen probe frame."""
    try:
        from ..skills_window_control import _grab_screen_image  # Phase 3 D2c: file now in core/desktop/

        image, err = _grab_screen_image(monitor_rect)
        return image if err is None else None
    except Exception:  # noqa: BLE001
        return None


def screen_change_fraction(before, after) -> float | None:
    """Fraction of pixels that changed between two screen images,
    computed exactly the way skills_window_control.screen_diff computes
    it (480-wide greyscale pair, its _DIFF_THRESHOLD), reusing
    that machinery rather than a new differ. None when either
    image is missing or the comparison can't run."""
    if before is None or after is None:
        return None
    try:
        from PIL import ImageChops, ImageStat

        from ..skills_window_control import _DIFF_THRESHOLD  # Phase 3 D2c: file now in core/desktop/

        base = before.convert("RGB")
        cur = after.convert("RGB")
        if cur.size != base.size:
            cur = cur.resize(base.size)
        scale = min(1.0, 480 / base.width)
        small = (max(3, int(base.width * scale)),
                 max(3, int(base.height * scale)))
        base_small = base.convert("L").resize(small)
        cur_small = cur.convert("L").resize(small)
        diff = ImageChops.difference(base_small, cur_small)
        mask = diff.point(lambda p: 255 if p > _DIFF_THRESHOLD else 0)
        return ImageStat.Stat(mask).mean[0] / 255.0
    except Exception:  # noqa: BLE001 — a visual check is best-effort
        return None


def probe_suffix(before_snap, after_snap, before_img, after_img,
                 method: str = "coords") -> str:
    """The H5 suffix for actions with no named UIA target
    (coordinate clicks, window actions): a leading space + one
    change sentence, the screen-changed sentence when only the
    pixels moved, or the no-change sentence (with the ladder for
    click methods) when a live probe channel saw nothing move.
    "" when no probe channel was live at all."""
    sentence = _snapshot_change_sentence(before_snap, after_snap)
    if sentence:
        return " " + sentence
    fraction = screen_change_fraction(before_img, after_img)
    if fraction is not None:
        try:
            from ..skills_window_control import _DIFF_QUIET, _pct  # Phase 3 D2c: file now in core/desktop/
        except Exception:  # noqa: BLE001
            _DIFF_QUIET, _pct = 0.005, None  # screen_diff's own values
        if fraction >= _DIFF_QUIET:
            pct = _pct(fraction) if _pct else f"{round(fraction * 100)}%"
            # DESK-02 (narration discipline): this sentence reports
            # pixels only. The old "The screen changed" wording
            # was read upstream as proof the requested action
            # succeeded; the new wording states exactly what was
            # observed and disclaims success in the same sentence.
            return (f" The pixels changed (about {pct} of pixels "
                    "differ) — a picture change only, not proof "
                    "the requested action succeeded.")
        return " " + no_change_sentence(method)
    if before_snap is not None and after_snap is not None:
        return " " + no_change_sentence(method)
    return ""


def _probe_click_suffix(before_snap, after_snap, before_state,
                        after_state, facts, method: str = "uia") -> str:
    """The H5 suffix for a successful named-control click: the
    target's new state when it flipped, else the snapshot change
    sentence, else the no-change sentence when at least one probe
    channel was live, else "" (nothing observable — stay silent
    and keep the pre-H5 wording)."""
    if before_state and after_state and before_state != after_state:
        return " " + _state_sentence(
            facts["name"], facts["control_type"], after_state)
    sentence = _snapshot_change_sentence(before_snap, after_snap)
    if sentence:
        return " " + sentence
    observed = (
        (before_state is not None and after_state is not None)
        or (before_snap is not None and after_snap is not None))
    if observed:
        return " " + no_change_sentence(method)
    return ""


def _probe_type_suffix(before_snap, after_snap, value_before,
                       value_after, facts, verified: bool) -> str:
    """The H5 suffix for a successful uia_type_text. `verified` is
    True only on the UIA Value paths, where the field's readable
    value is real evidence: a changed value is reported (never the
    text itself — it may be sensitive), an unchanged one is an
    honest no-change. The keystroke path can't read its result
    back, so it only ever gains a foreground/focus sentence."""
    if verified and value_after != value_before:
        return f" The '{facts['name']}' field now holds the new text."
    sentence = _snapshot_change_sentence(before_snap, after_snap)
    if sentence:
        return " " + sentence
    if verified:
        return " No visible change was observed in the field."
    return ""


def _effect_present(parsed, snapshot) -> bool:
    """True when `parsed` (_parse_expectation's triple) is present
    in one snapshot's evidence (foreground, focus, windows, texts,
    OCR text). No "appears"-vs-"present" distinction here: the
    wait only needs presence; verify_expectation still judges
    newness afterwards. Never raises."""
    if parsed is None or not isinstance(snapshot, dict):
        return False
    kind, target, _style = parsed
    corpus = _expect_corpus(snapshot)
    if kind == "window":
        if corpus["fg"] and _corpus_contains(corpus["fg"], target):
            return True
        return any(_corpus_contains(w, target) for w in corpus["windows"])
    return _corpus_contains(corpus["all"], target)


def wait_for_effect(expect_text, timeout_s: float = 5.0,
                    snapshot_fn=None) -> tuple[str, str]:
    """Wait (bounded) for an expectation's effect to appear.

    Returns (status, detail), status in {"met", "not-met",
    "unverifiable"}: "met" when the effect was observed before
    the deadline, "not-met" when the deadline passed with live
    snapshots that never showed it, "unverifiable" when the
    expectation names nothing groundable or no snapshot could
    ever be observed. `snapshot_fn` (default: _safe_snapshot) is
    the test seam. Never raises; a snapshot_fn that raises reads
    as "no observation this tick", never as a verdict."""
    parsed = _parse_expectation(expect_text)
    if parsed is None:
        return ("unverifiable",
                "the expectation names nothing this engine can ground.")
    try:
        timeout = max(0.0, float(timeout_s))
    except (TypeError, ValueError):
        timeout = 5.0
    import time as _time

    deadline = _time.monotonic() + timeout
    observed_any = False
    while True:
        try:
            snap = snapshot_fn() if snapshot_fn is not None \
                else _safe_snapshot()
        except Exception:  # noqa: BLE001
            snap = None
        if snap is not None:
            observed_any = True
            if _effect_present(parsed, snap):
                return ("met", "the expected effect was observed.")
        if _time.monotonic() >= deadline:
            break
        _time.sleep(0.25)
    if not observed_any:
        return ("unverifiable",
                "no desktop snapshot could be observed while waiting.")
    return ("not-met",
            "the deadline passed without the expected effect.")

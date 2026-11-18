"""Editor-truth read for the VS Code test case (click-anywhere
Batch 1, Plan 9 §6.3, Q-11/R-11/R-19).

After a direct file write, the editor must SHOW the new content —
disk verify alone cannot tell whether `code -r` opened the file in
a different window or failed silently. v1 uses OCR (a screenshot
of the editor region + Tesseract, the existing sight+click stack)
with a NORMALISED match on the distinctive new-content lines,
PLUS the tab-title check (the file's tab is the active tab, title
matches the file name).

Normalisation: strip everything that is not a letter or digit,
fold case, then fold confusables to one canonical form — `0` and
`O` -> 'O'; `1` and `l` -> 'l'. OCR misreads WITHIN the table are
absorbed; misreads outside it are not.

The editor region source: the editor pane rect from the adapter's
UIA tree (the editor pane control's rect via the adapter's
`read_tree`), with the whole VS Code window rect as fallback.

An inconclusive read — the OCR text does not match under
normalisation (with the folding) and the tab-title check does not
settle it — returns the PAUSE signal (never a silent pass, never
a silent fail).

The match logic is pure over synthetic OCR word lists — no real
screenshot is needed to test it. The UIA document-text leg is NOT
used in v1: VS Code's editor text is only exposed through UIA in
accessibility mode, which is off by default and the plan does not
turn it on.

Licence: own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).
"""

from __future__ import annotations

import re


def normalise_ocr_text(text: str) -> str:
    """Alphanumeric-only, case-insensitive, confusable-folded.

    Strips everything that is not a letter or digit, folds case,
    then folds confusables to one canonical form — `0` and `O`
    -> 'O'; `1` and `l` -> 'l' (brackets were already stripped
    as non-alphanumeric). The fold is per character so the
    canonical forms ('O', 'l') survive the case fold."""
    if not isinstance(text, str):
        return ""
    out = []
    for ch in text:
        if not ch.isalnum():
            continue
        if ch in "0Oo":
            out.append("O")
        elif ch in "1lL":
            out.append("l")
        elif ch.isalpha():
            out.append(ch.upper())
        else:
            out.append(ch)
    return "".join(out)


def _ocr_words_text(ocr_words) -> str:
    """The joined text of an OCR word list.

    Each word is a dict with a "text" key (the shape
    `core/desktop/vision_capture.py` produces) or a plain string.
    """
    parts = []
    for word in ocr_words or []:
        if isinstance(word, dict):
            parts.append(str(word.get("text") or ""))
        else:
            parts.append(str(word or ""))
    return " ".join(parts)


def editor_truth_check(*, expected_lines, ocr_words, tab_title="",
                       file_name=""):
    """Check the editor shows the new content.

    ``expected_lines`` are the distinctive new-content lines;
    ``ocr_words`` is the OCR word list for the editor region;
    ``tab_title`` is the active tab's title; ``file_name`` is the
    file's name. Returns a dict:

      {"verdict": "match"} — every distinctive line matches under
        normalisation (+ folding), or the tab-title check settles
        an otherwise thin OCR read;
      {"verdict": "pause", "reason": ...} — inconclusive: the OCR
        text does not match and the tab title does not settle it.
        The goal PAUSES and asks him to confirm visually (never
        a silent pass, never a silent fail).

    A line matches when its normalised form is a substring of the
    normalised OCR text. An empty distinctive line never counts
    (it would match everything); a line that normalises to empty
    is skipped. When no expected line survives normalisation the
    read is inconclusive unless the tab title settles it.
    """
    observed = normalise_ocr_text(_ocr_words_text(ocr_words))
    wanted = [normalise_ocr_text(line) for line in (expected_lines or [])]
    wanted = [w for w in wanted if w]
    matched = [w for w in wanted if w and w in observed]
    tab_settles = bool(file_name) and bool(tab_title) and (
        normalise_ocr_text(file_name) in normalise_ocr_text(tab_title))
    if wanted and len(matched) == len(wanted):
        return {"verdict": "match",
                "reason": ("all %d distinctive line(s) match under "
                           "normalisation" % len(wanted)),
                "matched": len(matched), "wanted": len(wanted),
                "tab_settles": tab_settles}
    if tab_settles and matched:
        return {"verdict": "match",
                "reason": ("tab title matches the file name and "
                           "%d/%d distinctive line(s) match"
                           % (len(matched), len(wanted))),
                "matched": len(matched), "wanted": len(wanted),
                "tab_settles": True}
    if tab_settles and not wanted:
        return {"verdict": "match",
                "reason": "tab title matches the file name (no "
                          "distinctive lines to match)",
                "matched": 0, "wanted": 0, "tab_settles": True}
    return {"verdict": "pause",
            "reason": ("inconclusive editor-truth read: %d/%d "
                       "distinctive line(s) match under normalisation "
                       "and the tab-title check does not settle it — "
                       "ask him to confirm visually"
                       % (len(matched), len(wanted))),
            "matched": len(matched), "wanted": len(wanted),
            "tab_settles": tab_settles}


def editor_region(*, tree=None, window_rect=None):
    """The editor region rect (left, top, right, bottom).

    The editor pane rect from the adapter's UIA tree (the editor
    pane control's rect via the adapter's `read_tree`), with the
    whole VS Code window rect as fallback when the pane rect is
    unavailable. A whole-window screenshot includes the sidebar
    and terminal, which can confuse the normalized match — the
    pane rect is the region, the window rect is the fallback.
    ``tree`` is a list of control dicts with "type"/"name"/
    "rect" keys; the pane is the control whose type or name
    names the editor pane ("editor", "document", "pane")."""
    for control in tree or []:
        if not isinstance(control, dict):
            continue
        hay = ((str(control.get("type") or "") + " "
                + str(control.get("name") or "")).strip().lower())
        rect = control.get("rect")
        if rect is None:
            continue
        if ("editor" in hay or "document" in hay) and "pane" in hay:
            return tuple(rect)
    for control in tree or []:
        if not isinstance(control, dict):
            continue
        hay = ((str(control.get("type") or "") + " "
                + str(control.get("name") or "")).strip().lower())
        rect = control.get("rect")
        if rect is None:
            continue
        if "editor" in hay:
            return tuple(rect)
    if window_rect is not None:
        return tuple(window_rect)
    return None

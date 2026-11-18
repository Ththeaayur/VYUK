"""Control Plus split (B26): control_plus_click.py -- moved verbatim from core/desktop/control_plus.py; see the facade for the split map."""

from .control_plus_helpers import _input_blocked  # INTENTIONAL (B26: cross-part bare name; defined in control_plus_helpers)
from .control_plus_helpers import _note_input_pressed  # INTENTIONAL (B26: cross-part bare name; defined in control_plus_helpers)
from .control_plus_helpers import _note_input_released  # INTENTIONAL (B26: cross-part bare name; defined in control_plus_helpers)

def click_text(text: str) -> str:
    """OCR the screen and click the visible word/phrase matching `text`.

    Match discipline (v5.9, audit H5): ALL matches are gathered
    first. Exactly one → it is clicked and the receipt identifies
    what was clicked (the v5.8 cursor receipt, shared via
    core/desktop/cursor.py). Several → nothing is clicked; the candidates
    are listed with their coordinates. The v4.3 failsafe is
    consulted before anything is captured or clicked (audit H3).
    """
    if not text or not str(text).strip():
        return "No text given. Tell me what word or label to look for on screen."
    blocked = _input_blocked()
    if blocked:
        return blocked
    target = str(text).strip().lower()
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError:
        return "Click-by-text isn't available in this build of VYUK — the OCR part didn't make it into the package."
    except Exception as e:  # noqa: BLE001
        return f"Click-by-text unavailable: the OCR library could not be loaded ({e})."
    from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/ (D2b: level .., file now in core/desktop/)

    if not local_assets.configure_pytesseract(pytesseract):
        return "Click-by-text isn't set up on this machine yet — the OCR engine that comes with VYUK isn't in place. It arrives with the installer; nothing for you to download."
    # v5.44 (E2e + E7): word boxes come from the native-resolution
    # OCR pipeline in core/control.py — a dedicated full-res frame
    # (the foreground window's rectangle by default), x2 upscale +
    # binarisation, explicit Tesseract config/lang — and the boxes
    # are already TRUE screen pixels. The pre-E2 path OCR'd the
    # 1024-px capture and scaled its boxes back up by ratio; that
    # ratio quantisation is gone. When the crop yields no match,
    # the search falls back to the whole screen (the yield — no
    # match — is what decides, here at the OCR seam).
    from . import control as _control  # Phase 3 D4a: control hub now in core/desktop/; D2b interim closed

    target_words = target.split()
    n = len(target_words)

    def _centres(word_list) -> list[tuple[int, int]]:
        found = []
        for i in range(len(word_list) - n + 1):
            span = word_list[i:i + n]
            if [w["text"] for w in span] != target_words:
                continue
            left = min(w["left"] for w in span)
            top = min(w["top"] for w in span)
            right = max(w["right"] for w in span)
            bottom = max(w["bottom"] for w in span)
            found.append(((left + right) // 2, (top + bottom) // 2))
        return found

    try:
        attempts = _control._ocr_captures()
        image, meta = next(attempts)
    except Exception as e:  # noqa: BLE001
        return (
            f"Could not capture the screen for click-by-text: {e}. "
            "Screen capture needs a display — it is unavailable on a headless system."
        )
    words: list = []
    centres: list = []
    while True:
        try:
            words, _note = _control._ocr_image_words(
                image, meta, pytesseract, Output)
        except Exception as e:  # noqa: BLE001
            return f"Click-by-text OCR failed: {e}"
        # Count EVERY match before acting (audit H5): clicking the
        # first of several identical labels is a guess, and a guess
        # is what the v5.6 discipline exists to refuse.
        centres = _centres(words)
        if centres or not (meta or {}).get("cropped"):
            break
        try:
            image, meta = next(attempts)
        except StopIteration:
            break
        except Exception as e:  # noqa: BLE001
            return (
                f"Could not capture the screen for click-by-text: {e}. "
                "Screen capture needs a display — it is unavailable on a headless system."
            )
    if not words:
        return "OCR ran, but no readable words were found on screen."
    if not centres:
        return f"Could not find '{text}' anywhere on screen (OCR saw {len(words)} word(s))."
    if len(centres) > 1:
        shown = centres[:10]
        listing = "; ".join(
            f"{i + 1}. at ({x}, {y})" for i, (x, y) in enumerate(shown))
        more = (f"; and {len(centres) - len(shown)} more"
                if len(centres) > len(shown) else "")
        return (
            f"Found {len(centres)} on-screen matches for '{text}' — "
            f"I won't guess which one to click. Matches: {listing}{more}. "
            "Tell me which by its coordinates (mouse_control can click "
            "an exact point)."
        )
    cx, cy = centres[0]
    # v5.8 receipt, shared with the cursor hands: identify what is
    # about to be clicked BEFORE the click fires (the identification
    # helper also captures the foreground title for the post-click
    # focus note). Identification never blocks the click.
    id_note = ""
    fg_before = None
    try:
        from .cursor import _identify_click_point  # Phase 3 D4d: cursor hub now in core/desktop/; D2b interim closed

        id_note, fg_before = _identify_click_point(cx, cy)
    except Exception:  # noqa: BLE001 — receipt is best-effort
        id_note, fg_before = "", None
    try:
        from .cursor_input import _mouse_controller

        mouse, Button, err = _mouse_controller()
        if mouse is None:
            raise RuntimeError(err)
        mouse.position = (cx, cy)
        # v5.48 (programme item 26): the click's button is
        # registered for the call's duration (mouse.click presses
        # and releases inside one dispatch) and unregistered on
        # success; a failed click leaves it tracked for a
        # failsafe trip.
        _note_input_pressed("button", Button.left)
        try:
            mouse.click(Button.left, 1)
        except Exception:
            raise  # stays tracked — a failsafe trip releases it
        _note_input_released("button", Button.left)
    except Exception as e:  # noqa: BLE001
        return (
            f"Found '{text}' at ({cx}, {cy}) but could not click it: "
            f"{e}"
        )
    focus_note = ""
    try:
        from .cursor import _focus_change_note  # Phase 3 D4d: cursor hub now in core/desktop/; D2b interim closed

        focus_note = _focus_change_note(fg_before)
    except Exception:  # noqa: BLE001 — receipt is best-effort
        focus_note = ""
    # R1b: the click was dispatched at an OCR point; the landing was
    # not verified (no expectation was given) — mark it honestly
    # unverified. The UIA expectation path (uia_click_control) is
    # the verified alternative.
    # B13: the vision landing check asks the B5 backend whether the
    # click landed, with the clicked text as the expectation. Where
    # vision cannot read, the honestly-unverified return stands
    # exactly as before (same message, same kind) — the check costs
    # nothing in the lab, where no weights exist.
    from .. import tool_outcome as _outcome  # R1b: seam wiring

    base = (f"Clicked '{text}' on screen at ({cx}, {cy})." + id_note +
            focus_note +
            " (Click dispatched; landing not confirmed — no expectation "
            "was given.)")
    return _outcome.hands_landing_check("click", text, base)

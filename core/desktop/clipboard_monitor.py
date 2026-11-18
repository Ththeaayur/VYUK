"""
Clipboard Monitor for Voice AI Assistant.
Watches the system clipboard for new text and notifies the assistant.

v5.48 (programme item 23, A15-residual): the notice path no longer
hands raw copied text to the display layer. clipboard_notice()
turns any copied text into a COUNT-ONLY notice — no substring of
the text ever appears in it — and secret-shaped text gets the
"(content hidden)" variant. The raw text still reaches
on_new_content (other consumers may need it); what the GUI prints
must be clipboard_notice(text), never text[:120].

Secret-shape detection REUSES core/confirm_gate._looks_like_secret:
confirm_gate is a leaf module (stdlib imports only; it imports
neither this module nor the funnel registry (core/funnel/tools_registry.py)), so importing it here creates
no cycle. The alternative candidate, core/funnel/tools_handlers._clipboard_secret_
shown, was rejected: tools_handlers.py is heavy and reaches the clipboard
surface itself, so importing it from this monitor WOULD risk a
cycle — confirm_gate is the non-cyclic option (verified by grep:
nothing in confirm_gate imports clipboard_monitor or tools).

History exclusion: on Windows an app can mark clipboard content
"CanIncludeInClipboardHistory" = 0 (the Win+V history exclusion
flag). clipboard_excluded_from_history() reads that format via
ctypes; when it says the current content is excluded, the watch
loop suppresses the callback entirely — content its owner asked
Windows to keep out of history is not announced either.
"""

import ctypes
import sys
import threading
import time
from collections.abc import Callable


def _looks_like_secret(text: str) -> bool:
    """Secret-shape check, delegated to core/confirm_gate (see the
    module docstring for why that detector, and why not tools_handlers.py's _clipboard_secret_shown).
    Any import/detection failure reads as NOT secret-shaped: the
    notice still leaks nothing, because it is count-only either
    way — the hidden variant is a courtesy label, not the wall."""
    try:
        from ..confirm_gate import _looks_like_secret as _detect  # Phase 3 D3: file now in core/desktop/; confirm_gate stays flat in core/

        return bool(_detect(text))
    except Exception:  # noqa: BLE001 — notice must never crash
        return False


def clipboard_notice(text: str) -> str:
    """The ONLY display string for copied text (v5.48, programme
    item 23): a count, never content. Ordinary text ->
    "Copied N characters to the clipboard."; secret-shaped text
    -> the same count plus "(content hidden — it looks like a
    secret)". N is len(text) exactly as passed in."""
    n = len(text or "")
    if _looks_like_secret(text or ""):
        return (
            f"Copied {n} characters to the clipboard "
            "(content hidden — it looks like a secret)."
        )
    return f"Copied {n} characters to the clipboard."


# ---------------------------------------------------------------------------
# Clipboard-history exclusion probe (Windows)
# ---------------------------------------------------------------------------

_history_probe: Callable[[], bool | None] | None = None


def set_history_probe(fn) -> None:
    """Inject the history-exclusion probe (suites use this seam;
    production passes nothing and gets the ctypes probe below).
    Pass None to restore the real probe."""
    global _history_probe
    _history_probe = fn


def clipboard_excluded_from_history() -> bool | None:
    """True when the current clipboard content is flagged out of
    the Windows clipboard history, False when it is flagged in (or
    carries no flag), None when unknown (off Windows, no clipboard
    access, or any failure). v5.48, programme item 23.

    The flag is the registered clipboard format
    "CanIncludeInClipboardHistory": a DWORD on the clipboard; 0
    means the content's owner excluded it from history."""
    if _history_probe is not None:
        try:
            result = _history_probe()
        except Exception:  # noqa: BLE001 — a broken probe is "unknown"
            return None
        return None if result is None else bool(result)
    if not sys.platform.startswith("win"):
        return None
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        fmt = user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")
        if not fmt:
            return None
        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(fmt)
            if not handle:
                return False  # no flag present -> not excluded
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                value = ctypes.c_uint32.from_address(ptr).value
            finally:
                kernel32.GlobalUnlock(handle)
            return value == 0
        finally:
            user32.CloseClipboard()
    except Exception:  # noqa: BLE001 — guarded: unknown, never a crash
        return None


class ClipboardMonitor:
    def __init__(self, min_length: int = 60, debounce_seconds: float = 1.5):
        self.min_length = min_length
        self.debounce_seconds = debounce_seconds
        self._running = False
        self._last_text = ""
        self.on_new_content: Callable[[str], None] | None = None

    def start(self):
        self._running = True
        t = threading.Thread(target=self._watch_loop, daemon=True, name="ClipboardMonitor")
        t.start()

    def stop(self):
        self._running = False

    def _watch_loop(self):
        # Lazy-import to avoid hard dependency
        try:
            import pyperclip
        except ImportError:
            print("[Clipboard] pyperclip not installed — clipboard monitoring disabled.")
            return

        while self._running:
            try:
                text = pyperclip.paste()
                if (
                    text
                    and isinstance(text, str)
                    and text != self._last_text
                    and len(text.strip()) >= self.min_length
                ):
                    # v5.48 (programme item 23): history-excluded
                    # content is NOT announced — but _last_text
                    # still advances to it. Choice, documented:
                    # if _last_text stayed behind, the same excluded
                    # text would be re-probed every poll, and a
                    # later poll whose probe degrades to None would
                    # announce stale excluded content; advancing
                    # means exclusion is decided once, at first
                    # sight. A genuinely NEW copy (different text)
                    # is probed and announced normally.
                    self._last_text = text
                    if clipboard_excluded_from_history() is True:
                        pass  # excluded: suppress the callback entirely
                    elif self.on_new_content:
                        self.on_new_content(text.strip())
            except Exception:  # noqa: BLE001, S110
                pass
            time.sleep(self.debounce_seconds)


clipboard_monitor = ClipboardMonitor()

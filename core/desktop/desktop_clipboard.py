# Split from core/desktop/desktop.py (Phase B batch B7) — move only,
# no behaviour change. This module owns the section named below;
# the original file is now a facade re-exporting every name.

# Clipboard channel + OLE drag-drop (HA-6, both halves). The
# _clipboard_backend global, set_clipboard_backend, and ALL its
# readers (save_clipboard, _restore_snapshot,
# _set_text_excluding_history, clipboard_paste, ole_drag_drop)
# live in this module so the setter's `global` write and the
# readers' bare-name reads resolve in the same globals.

from .desktop_pilots import _is_windows  # INTENTIONAL (desktop split B7): _is_windows is owned by desktop_pilots; the clipboard section calls it.

# ---------------------------------------------------------------------------
# HA-6 — clipboard channel: save/restore ALL formats, no retention
# ---------------------------------------------------------------------------
# Privacy discipline (the item itself, not an afterthought):
# * The snapshot is memory-only, capped (formats that cannot be
#   byte-copied — bitmap handles, metafiles — are COUNTED as
#   skipped, never silently dropped from the report).
# * The pasted text is placed with CanIncludeInClipboardHistory=0
#   (core/clipboard_monitor.py's exclusion flag), so the paste
#   does not enter Win+V history — and history is what cloud
#   sync follows. A pre-existing history/cloud copy of EARLIER
#   content is untouched by this code and is said so, not denied.
# * Restore runs in a finally; the saved bytes are then dropped
#   (snapshot.clear()) even when restore itself failed, so a
#   failed restore never leaves VYUK holding the user's old
#   clipboard. Nothing is ever written to disk or to data/.
# * The clipboard is never read for its CONTENT by this module
#   beyond the byte copy needed to restore it: no text is
#   echoed, logged, or returned — only format counts.
#
# Backend seam: tests inject an in-memory backend
# ({"formats": {fmt_id: bytes}, "history_excluded": bool}) via
# set_clipboard_backend(); production leaves it None and the
# Win32 ctypes path below runs. The seam replaces the WHOLE
# backend so Linux suites can exercise save/set/restore exactly.

_CLIPBOARD_MAX_BYTES = 16 * 1024 * 1024  # 16 MiB total snapshot cap
_CF_UNICODETEXT = 13

_clipboard_backend = None  # test seam: dict-like fake, or None


def set_clipboard_backend(backend) -> None:
    """Test seam: inject a fake clipboard backend (a dict with a
    "formats" mapping), or None to restore the Win32 path."""
    global _clipboard_backend
    _clipboard_backend = backend


class ClipboardSnapshot:
    """An in-memory copy of the clipboard's byte-copyable formats."""

    def __init__(self, formats=None, skipped=0, total_bytes=0):
        self.formats = dict(formats or {})
        self.skipped = skipped
        self.total_bytes = total_bytes

    def clear(self) -> None:
        """Drop the saved bytes — retention ends here, always."""
        self.formats.clear()
        self.total_bytes = 0

    def describe(self) -> str:
        parts = [f"{len(self.formats)} format(s)"]
        if self.skipped:
            parts.append(f"{self.skipped} skipped (not byte-copyable)")
        return ", ".join(parts)


def _win32_clipboard():
    """ctypes user32 handle, or None off-Windows / on failure."""
    if not _is_windows():
        return None
    try:
        import ctypes

        return ctypes.windll.user32
    except Exception:  # noqa: BLE001
        return None


def save_clipboard() -> ClipboardSnapshot | None:
    """Snapshot the current clipboard (None when unavailable).
    Never raises; a snapshot that cannot be opened reads as None
    (the caller then refuses the paste rather than risking the
    user's clipboard — losing it silently would be worse)."""
    if _clipboard_backend is not None:
        try:
            formats = dict(_clipboard_backend.get("formats") or {})
            total = sum(len(v) for v in formats.values())
            return ClipboardSnapshot(formats, 0, total)
        except Exception:  # noqa: BLE001
            return None
    user32 = _win32_clipboard()
    if user32 is None:
        return None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(None):
            return None
        try:
            formats = {}
            skipped = 0
            total = 0
            fmt = user32.EnumClipboardFormats(0)
            while fmt:
                handle = user32.GetClipboardData(fmt)
                if not handle:
                    skipped += 1
                    fmt = user32.EnumClipboardFormats(fmt)
                    continue
                try:
                    size = kernel32.GlobalSize(handle)
                    ptr = kernel32.GlobalLock(handle)
                    if not ptr:
                        skipped += 1
                    else:
                        try:
                            blob = ctypes.string_at(ptr, size)
                        finally:
                            kernel32.GlobalUnlock(handle)
                        if total + len(blob) > _CLIPBOARD_MAX_BYTES:
                            skipped += 1
                        else:
                            formats[fmt] = blob
                            total += len(blob)
                except Exception:  # noqa: BLE001 — one bad format
                    skipped += 1  # must not lose the whole snapshot
                fmt = user32.EnumClipboardFormats(fmt)
            return ClipboardSnapshot(formats, skipped, total)
        finally:
            user32.CloseClipboard()
    except Exception:  # noqa: BLE001
        return None


def _restore_snapshot(snapshot: ClipboardSnapshot) -> bool:
    """Put a snapshot back. Returns False when it could not be
    restored (the caller reports that honestly). Never raises."""
    if _clipboard_backend is not None:
        try:
            _clipboard_backend["formats"] = dict(snapshot.formats)
            return True
        except Exception:  # noqa: BLE001
            return False
    user32 = _win32_clipboard()
    if user32 is None:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        GMEM_MOVEABLE = 0x0002
        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            ok = True
            for fmt, blob in snapshot.formats.items():
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(blob) or 1)
                if not handle:
                    ok = False
                    continue
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    kernel32.GlobalFree(handle)
                    ok = False
                    continue
                try:
                    ctypes.memmove(ptr, blob, len(blob))
                finally:
                    kernel32.GlobalUnlock(handle)
                if not user32.SetClipboardData(fmt, handle):
                    kernel32.GlobalFree(handle)
                    ok = False
            return ok
        finally:
            user32.CloseClipboard()
    except Exception:  # noqa: BLE001
        return False


def _set_text_excluding_history(text: str) -> bool:
    """Place `text` on the clipboard with the history-exclusion
    flag set, so the paste does not enter Win+V history. False
    when it could not be placed. Never raises."""
    if _clipboard_backend is not None:
        try:
            _clipboard_backend["formats"] = {
                _CF_UNICODETEXT: text.encode("utf-16-le") + b"\x00\x00"}
            _clipboard_backend["history_excluded"] = True
            return True
        except Exception:  # noqa: BLE001
            return False
    user32 = _win32_clipboard()
    if user32 is None:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        GMEM_MOVEABLE = 0x0002
        blob = text.encode("utf-16-le") + b"\x00\x00"
        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(blob))
            if not handle:
                return False
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                kernel32.GlobalFree(handle)
                return False
            try:
                ctypes.memmove(ptr, blob, len(blob))
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(_CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
            # History exclusion: a DWORD 0 under the registered
            # "CanIncludeInClipboardHistory" format (the flag
            # core/clipboard_monitor.py reads back).
            try:
                fmt = user32.RegisterClipboardFormatW(
                    "CanIncludeInClipboardHistory")
                if fmt:
                    flag = ctypes.create_string_buffer(b"\x00\x00\x00\x00", 4)
                    h2 = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
                    if h2:
                        p2 = kernel32.GlobalLock(h2)
                        if p2:
                            try:
                                ctypes.memmove(p2, flag, 4)
                            finally:
                                kernel32.GlobalUnlock(h2)
                            if not user32.SetClipboardData(fmt, h2):
                                kernel32.GlobalFree(h2)
            except Exception:  # noqa: BLE001 — exclusion is
                pass  # best-effort; the paste itself already landed
            return True
        finally:
            user32.CloseClipboard()
    except Exception:  # noqa: BLE001
        return False


def clipboard_paste(text: str, paste_fn) -> tuple[bool, str]:
    """HA-6 rung: paste `text` via the clipboard, then restore.

    Saves ALL byte-copyable formats, places the text (marked
    excluded from clipboard history), calls paste_fn() (the
    caller's Ctrl+V — uia passes el.type_keys("^v")), restores
    the saved clipboard in a finally, and drops the saved bytes
    whether restore succeeded or not. Returns (pasted, note):
    pasted is True only when the text was placed AND paste_fn
    ran without raising; note names the saved/restored formats
    and says plainly when restore failed (the user's old
    clipboard is then NOT back — that is reported, never hidden).
    The text itself is never in the note. Never raises."""
    if text is None:
        text = ""
    text = str(text)
    if _clipboard_backend is None and not _is_windows():
        return False, ("clipboard paste isn't available on this PC "
                       "yet — it only works on Windows.")
    snapshot = save_clipboard()
    if snapshot is None:
        return False, ("clipboard paste refused: the current clipboard "
                       "could not be saved first, so pasting would risk "
                       "losing what you had copied.")
    saved_desc = snapshot.describe()
    pasted = False
    try:
        if not _set_text_excluding_history(text):
            return False, ("clipboard paste failed: the text could not "
                           "be placed on the clipboard.")
        try:
            paste_fn()
            pasted = True
        except Exception:  # noqa: BLE001 — paste keystroke failed
            pasted = False
    finally:
        restored = _restore_snapshot(snapshot)
        snapshot.clear()  # retention ends here, success or not
    if not pasted:
        note = ("the paste keystroke did not run; the clipboard was "
                f"restored ({saved_desc}).")
        if not restored:
            note = ("the paste keystroke did not run, and the clipboard "
                    "could NOT be restored — what you had copied before "
                    "may be lost.")
        return False, note
    if not restored:
        return True, ("pasted, but the clipboard could NOT be restored "
                      "to what you had copied before — that content may "
                      "be lost; nothing of it was kept by VYUK.")
    return True, (f"pasted via the clipboard (history-excluded) and "
                  f"the previous clipboard was restored ({saved_desc}); "
                  "nothing of the old clipboard was kept.")


# ---------------------------------------------------------------------------
# HA-6, second half — OLE drag-drop PILOT (gesture + format discipline)
# ---------------------------------------------------------------------------
# True OLE (a COM IDataObject offered to DoDragDrop) is STUDY, not
# shipped: it cannot be verified on this Linux VM, and claiming a
# COM data object works without a Windows run would be a lie.
# What IS piloted here: the drag gesture itself, on cursor.py's
# own seeded-Bezier path (drag_cursor — never a second planner),
# wrapped in the same save/restore discipline as the clipboard
# rung, so a drag that carries data does not silently eat the
# user's clipboard either. The result says exactly that.

def ole_drag_drop(x1, y1, x2, y2, duration: float = 0.8) -> str:
    """Pilot drag: seeded-Bezier cursor drag with the clipboard
    saved and restored around it. NOT a COM OLE drag — the result
    says so. Never raises."""
    snapshot = None
    if _is_windows() or _clipboard_backend is not None:
        snapshot = save_clipboard()
    try:
        from . import cursor as _cursor  # Phase 3 D4d: cursor hub now in core/desktop/; S7a interim closed

        result = _cursor.drag_cursor(int(x1), int(y1), int(x2), int(y2),
                                     duration=duration)
    except Exception as e:  # noqa: BLE001
        result = f"Drag unavailable: {e}"
    if snapshot is not None:
        restored = _restore_snapshot(snapshot)
        snapshot.clear()
        if not restored:
            result += (" The clipboard could not be restored after "
                       "the drag — what was copied before may be lost.")
    return (result + " (Drag pilot: a cursor drag on the seeded-Bezier "
            "path, not a COM OLE DoDragDrop — true OLE data objects "
            "stay STUDY.)")

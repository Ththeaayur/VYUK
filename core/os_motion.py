"""OS reduced-motion preference (UI Phase 6, D-P6-12).

The blueprint requires respecting the OS reduced-motion
preference, but Qt exposes no such preference (verified by probe:
``QStyleHints`` in this PySide6 build has no motion / animation /
reduce attribute at all — see
``ui_p6_work/logs/probe_d12_motion.log``). The reliable mechanism
on Windows is ``SystemParametersInfoW`` with
``SPI_GETCLIENTAREAANIMATION`` (0x1002): the "Show animations in
Windows" toggle (Settings → Accessibility → Visual effects →
Animation effects). It returns TRUE while animations are on,
FALSE while they are off.

This module is stdlib-only (``sys`` + ``ctypes``) and Qt-free, so
the setting default can be unit-tested without a window. The
result is cached per process: the OS toggle is a session-level
setting and a restart picks up a change; re-querying on every
``_motion_level()`` call would be a syscall per transition gate.

Returns ``None`` where no mechanism exists (non-Windows, or the
call fails) — the caller treats ``None`` as "no OS opinion" and
keeps the built-in default. Never raises.
"""
import ctypes
import sys

_SPI_GETCLIENTAREAANIMATION = 0x1002

_cache = None
_cache_set = False


def os_prefers_reduced_motion():
    """True if the OS says reduce motion, False if it says animate,
    None if the OS offers no readable preference on this platform."""
    global _cache, _cache_set
    if _cache_set:
        return _cache
    _cache = _os_prefers_reduced_motion_once()
    _cache_set = True
    return _cache


def _os_prefers_reduced_motion_once():
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        animations_on = ctypes.c_bool(True)
        ok = user32.SystemParametersInfoW(
            _SPI_GETCLIENTAREAANIMATION, 0,
            ctypes.byref(animations_on), 0)
        if not ok:
            return None
        return not animations_on.value
    except Exception:  # noqa: BLE001 — no OS opinion, never raise
        return None

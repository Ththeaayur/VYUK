"""
LinuxBackend — the EXPERIMENTAL Linux platform backend
(v5.50, programme item 42).

EXPERIMENTAL: this backend is contract-tested only against
faked OS seams (see core/platform/_seamed.py for the seam
protocol), on a headless VM with no X11/Wayland session, no
AT-SPI bus and no display. It has NOT been run against a real
Linux desktop. What is proven: the full PlatformBackend
surface, import-safety on every OS, and honest fail-soft
behaviour — including the Wayland limits below, which are
stated in the returned sentences, not hidden. What is NOT
proven is listed in the phase-5 notes (PORTING.md's Linux
section is the spec it was written against):

  * Accessibility — AT-SPI2 (pyatspi/dasbus): no real hook is
    wired (no seam → the honest unavailable sentence). The
    IsPassword convention IS implemented for element dicts:
    role "password text" → True; a readable editable text
    object → False; an unreadable role → None (UNKNOWN,
    never False).
  * X11 — no input hook is wired (input is honestly
    unavailable on this platform),
    windows via EWMH: no real window hook is wired.
  * Wayland limits (by design of the protocol, surfaced by
    _session_block whenever XDG_SESSION_TYPE=wayland and no
    seam fakes the OS): no global input injection without the
    RemoteDesktop portal / libei, no global window list
    without a compositor protocol, no cross-app capture
    without the XDG Screenshot/ScreenCast portal. On a stock
    GNOME Wayland session the input/window/screenshot areas
    therefore return the honest unavailable sentences.
  * Screenshots on X11 — the real-OS hook tries PIL ImageGrab
    (XGetImage underneath); unproven on a real X session.

Session detection branches on XDG_SESSION_TYPE at CALL time,
never import time (PORTING.md). All OS-specific imports are
lazy and guarded: this module imports cleanly on Windows.
"""

from __future__ import annotations

import os

from ._seamed import SeamedBackend

_WAYLAND_INPUT = (
    "Input isn't available on Wayland without the RemoteDesktop "
    "portal (libei) — global input injection is not permitted by "
    "the compositor, so nothing was sent.")
_WAYLAND_WINDOWS = (
    "Window control isn't available on Wayland without a "
    "compositor window protocol (foreign-toplevel on wlroots; "
    "stock GNOME exposes none), so no windows were touched.")
_WAYLAND_SCREENSHOT = (
    "Screen capture isn't available on Wayland without the XDG "
    "Screenshot portal and its consent prompt, so no frame was "
    "captured.")


def _session_type() -> str:
    return (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()


class LinuxBackend(SeamedBackend):
    """Linux backend (EXPERIMENTAL — see module docstring)."""

    name = "linux"

    _a11y_unavailable = (
        "Accessibility (AT-SPI) isn't available on this Linux "
        "system yet — no AT-SPI seam could be reached, so no "
        "controls were read.")
    _input_unavailable = (
        "Input isn't available on this Linux system yet — no "
        "input seam could run (no native input engine is wired, "
        "or no display), so nothing was sent.")
    _windows_unavailable = (
        "Window control isn't available on this Linux system yet "
        "— no EWMH/window seam could be reached, so no windows "
        "were touched.")

    def __init__(self, requested_platform: str = "linux",
                 seams: dict | None = None) -> None:
        super().__init__(requested_platform=requested_platform or "linux",
                         seams=seams)

    # -- Wayland honesty ----------------------------------------------

    def _session_block(self, area: str) -> str | None:
        # Seams fake the OS in tests; the Wayland limits gate only
        # the REAL-OS path, so a present seam bypasses the block.
        if self._seam(area if area != "screenshot" else "screenshot") \
                is not None:
            return None
        if _session_type() != "wayland":
            return None
        return {"input": _WAYLAND_INPUT,
                "windows": _WAYLAND_WINDOWS,
                "screenshot": _WAYLAND_SCREENSHOT}.get(area)

    # -- password verdict: AT-SPI "password text" role -----------------

    def _password_verdict(self, el):
        role = None
        if isinstance(el, dict):
            role = el.get("role") or el.get("control_type")
            verdict = el.get("is_password")
            if isinstance(verdict, bool):
                return verdict
        else:
            role = getattr(el, "role", None) or getattr(
                el, "control_type", None)
        if role is None:
            return None  # role unreadable → UNKNOWN, never False
        text = str(role).strip().lower()
        if text == "password text":
            return True
        if text in ("text", "edit", "editable text", "entry"):
            return False
        # A non-text element is positively not a password field.
        return False

    def is_password_control(self, el):
        if el is None:
            return None
        if isinstance(el, dict) and "role" not in el \
                and "is_password" not in el:
            verdict = el.get("is_password")
            return verdict if isinstance(verdict, bool) else None
        return self._password_verdict(el)

    # -- real-OS hooks (lazy, guarded; unproven on a real desktop) ----

    def _os_input(self, method: str, *args, **kw):
        """No native input engine is wired on this platform: the
        input hooks were driven by a third-party input library
        this build no longer ships. (False, None) throughout —
        the caller phrases the honest unavailable sentence, and
        nothing here ever claims delivery it cannot perform.
        On Wayland the caller has already been gated by
        _session_block; the answer is the same either way."""
        return False, None

    def _os_grab(self, full_screen: bool = False):
        if _session_type() == "wayland":
            return False, None  # portal-only; see _session_block
        try:
            from PIL import ImageGrab

            rect = None if full_screen else self.foreground_window_rect()
            if rect is not None:
                return True, ImageGrab.grab(bbox=rect)
            return True, ImageGrab.grab()
        except Exception:  # noqa: BLE001
            return False, None

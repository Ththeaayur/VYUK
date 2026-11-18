"""
MacBackend — the EXPERIMENTAL macOS platform backend
(v5.50, programme item 42).

EXPERIMENTAL: this backend is contract-tested only against
faked OS seams (see core/platform/_seamed.py for the seam
protocol). It has NOT been run against a real Mac. What is
proven: the full PlatformBackend surface, import-safety on
every OS, and honest fail-soft behaviour when no seam and no
real macOS API answers. What is NOT proven is listed in the
phase-5 notes (PORTING.md's macOS section is the spec it was
written against):

  * Input — no hook is wired: there is no input engine on
    this platform, so input is honestly unavailable. The
    Accessibility permission gate (AXIsProcessTrusted) cannot
    be probed without the real API, so an untrusted process's
    dropped input is UNPROVEN territory — nothing here claims
    delivery it cannot verify.
  * Windows — NSWorkspace / AXUIElement window lists: no real
    hook is wired (no seam → the honest unavailable sentence).
  * Accessibility — the AXUIElement tree: no real hook wired.
    The IsPassword equivalent IS specified: a secure text field
    has role AXSecureTextField (→ True); a readable non-secure
    element is False; an unreadable role is None (UNKNOWN,
    never False) — _password_verdict implements exactly that
    for element dicts / role-carrying objects.
  * Screenshot — the real-OS hook tries PIL ImageGrab (which
    uses the native grab on macOS); Screen Recording denial
    detection (CGPreflightScreenCaptureAccess) is NOT wired,
    so a wallpaper-only capture cannot yet be detected —
    UNPROVEN, stated here rather than hidden.

All OS-specific imports are lazy and guarded: this module
imports cleanly on Linux and Windows.
"""

from __future__ import annotations

from ._seamed import SeamedBackend


class MacBackend(SeamedBackend):
    """macOS backend (EXPERIMENTAL — see module docstring)."""

    name = "darwin"

    _a11y_unavailable = (
        "Accessibility (the macOS Accessibility API) isn't available "
        "on this system yet — no AXUIElement seam could be reached, "
        "so no controls were read.")
    _input_unavailable = (
        "Input isn't available on this macOS system yet — no input "
        "seam could run (no native input engine is wired, or the "
        "Accessibility permission is not granted), so nothing was sent.")
    _windows_unavailable = (
        "Window control isn't available on this macOS system yet — "
        "no NSWorkspace/Accessibility window seam could be reached, "
        "so no windows were touched.")

    def __init__(self, requested_platform: str = "darwin",
                 seams: dict | None = None) -> None:
        super().__init__(requested_platform=requested_platform or "darwin",
                         seams=seams)

    # -- password verdict: AXSecureTextField role --------------------

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
        if str(role) == "AXSecureTextField":
            return True
        return False

    def is_password_control(self, el):
        if el is None:
            return None
        if isinstance(el, dict) and "role" not in el \
                and "is_password" not in el:
            # A plain seam element dict without macOS role data:
            # the shared dict rule applies (bool or UNKNOWN).
            verdict = el.get("is_password")
            return verdict if isinstance(verdict, bool) else None
        return self._password_verdict(el)

    # -- real-OS hooks (lazy, guarded; unproven on real hardware) ---

    def _os_input(self, method: str, *args, **kw):
        """No native input engine is wired on this platform: the
        input hooks were driven by a third-party input library
        this build no longer ships. (False, None) throughout —
        the caller phrases the honest unavailable sentence, and
        nothing here ever claims delivery it cannot perform."""
        return False, None

    def _os_grab(self, full_screen: bool = False):
        try:
            from PIL import ImageGrab

            rect = None if full_screen else self.foreground_window_rect()
            if rect is not None:
                return True, ImageGrab.grab(bbox=rect)
            return True, ImageGrab.grab()
        except Exception:  # noqa: BLE001
            return False, None

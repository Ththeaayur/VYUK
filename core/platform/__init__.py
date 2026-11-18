"""
core.platform — the platform-backend seam (v5.45, P-3).

    from core.platform import get_backend

    backend = get_backend()          # cached, one per process
    backend.type_text("hello")       # input
    backend.window_control("list")   # windows
    backend.uia_find_control("OK")   # accessibility
    backend.capture_screen()         # screenshot

Selection: the platform is chosen from sys.platform, with the
environment variable VYUK_PLATFORM as an override (for tests
and porting work — it changes which platform the backend is
selected FOR, never what the host OS actually is). Values are
normalised: anything starting with "win" -> "windows",
"darwin"/"mac*" -> "darwin", anything starting with "linux" ->
"linux", anything else passes through lower-cased.

Backends registered (v5.50, programme items 41/42):

  * "windows" — WindowsBackend, the shipping backend: it
    delegates to the pre-seam modules, byte-for-byte as since
    v5.45. Windows behaviour is unchanged by this release.
  * "fake"    — FakeBackend (item 41): a fully scripted backend
    (windows / accessibility trees / deterministic screenshots
    are data; every input call is recorded in its input_log),
    selected with VYUK_PLATFORM=fake so tool code can be driven
    end-to-end on any machine.
  * "darwin"  — MacBackend, EXPERIMENTAL (item 42): contract-
    tested against faked Accessibility/Quartz seams only; not
    run against a real Mac.
  * "linux"   — LinuxBackend, EXPERIMENTAL (item 42): contract-
    tested against faked AT-SPI/X11 seams only; Wayland limits
    are stated in its own unavailable sentences.

CHANGE (v5.50, item 42): before this release every requested
platform fell back to WindowsBackend — that fallback is now
only for platforms with NO registered backend (e.g. "freebsd").
On a Linux host, get_backend() with no override now returns the
LinuxBackend (backend.name == "linux"); on macOS it returns the
MacBackend. `backend.requested_platform` still records what was
asked for and `backend.name` what serves it. The native
backends fail soft and honest where no seam / real OS answers,
exactly per PORTING.md — they never invent successes.
"""

from __future__ import annotations

import os
from .. import config_spec
import sys

from .base import ALL_METHODS, AREAS, PlatformBackend
from .fake import FakeBackend
from .linux import LinuxBackend
from .macos import MacBackend
from .windows import WindowsBackend

__all__ = [
    "AREAS",
    "ALL_METHODS",
    "PlatformBackend",
    "WindowsBackend",
    "FakeBackend",
    "MacBackend",
    "LinuxBackend",
    "get_backend",
    "reset_backend_cache",
]

# Normalised platform name -> backend class. "windows" is the
# shipping backend; "fake" is the scripted test backend (item
# 41); "darwin"/"linux" are the EXPERIMENTAL native backends
# (item 42). Any other requested platform falls back to
# WindowsBackend, whose delegated pre-seam code is import-safe
# and fail-soft off-Windows (see the module docstring).
_BACKENDS: dict[str, type[PlatformBackend]] = {
    "windows": WindowsBackend,
    "fake": FakeBackend,
    "darwin": MacBackend,
    "linux": LinuxBackend,
}

_BACKEND = None


def _normalise_platform(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value.startswith("win"):
        return "windows"
    if value == "darwin" or value.startswith("mac"):
        return "darwin"
    if value.startswith("linux"):
        return "linux"
    return value


def _requested_platform() -> str:
    """The normalised platform to select a backend FOR: the
    VYUK_PLATFORM env override when set (tests/porting), else
    sys.platform."""
    override = config_spec.env("VYUK_PLATFORM")
    return _normalise_platform(override if override.strip() else sys.platform)


def get_backend() -> PlatformBackend:
    """The process's platform backend (cached — repeated calls
    return the same object). Selected per _requested_platform():
    the registered backend for that platform, or WindowsBackend
    for an unregistered one, with requested_platform recording
    the selection it serves."""
    global _BACKEND
    if _BACKEND is None:
        requested = _requested_platform()
        backend_cls = _BACKENDS.get(requested, WindowsBackend)
        _BACKEND = backend_cls(requested_platform=requested)
    return _BACKEND


def reset_backend_cache() -> None:
    """Drop the cached backend (tests that change VYUK_PLATFORM
    or sys.platform use this; production never calls it)."""
    global _BACKEND
    _BACKEND = None

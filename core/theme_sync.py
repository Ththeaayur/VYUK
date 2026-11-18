"""Wave 9 (v5.64) — "Follow Windows" OS theming sync.

Detects the Windows light/dark preference and the Windows accent
colour, and applies the accent as ONE token override on the
*selected* theme — never as a tenth theme. The app's theme list is
unchanged by this module; accent only recolours the accent token
of whichever theme the user already picked.

Detection order on Windows:

1. ``darkdetect`` (BSD-3-Clause, verified from its 0.8.0 wheel
   primary: ``LICENSE`` + ``METADATA`` read locally — see the
   licence verdict in the Wave 9 Leaf 1 handoff) when installed;
2. the stdlib-only registry path (``winreg``) —
   ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\
   Personalize`` value ``AppsUseLightTheme`` (0 = dark, 1 = light).

No hard dependency: ``darkdetect`` is imported lazily and its
absence falls through to the registry path. On non-Windows hosts
every function reports honest unavailable — there is no Windows
theme to follow, and nothing is guessed from the Linux desktop.

Accent (Windows only, stdlib registry):
``HKCU\\Software\\Microsoft\\Windows\\DWM`` value
``ColorizationColor`` (a DWORD, ARGB). Only the RGB part is used.
An invalid or missing value yields ``accent=None``, never a
fallback colour invented by this module.
"""

from __future__ import annotations

import platform
import re

MODULE_VERSION = "5.64.0"

PERSONALIZE_KEY = (r"Software\Microsoft\Windows\CurrentVersion"
                   r"\Themes\Personalize")
DWM_KEY = r"Software\Microsoft\Windows\DWM"
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def is_windows() -> bool:
    return platform.system() == "Windows"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _detect_via_darkdetect() -> str | None:
    """'dark' / 'light' via darkdetect, or None when unknown/absent."""
    try:
        import darkdetect  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        mode = darkdetect.theme()
    except Exception:
        return None
    if mode in ("Dark", "Light"):
        return mode.lower()
    return None


def _detect_via_registry() -> str | None:
    """'dark' / 'light' via the stdlib registry path, else None."""
    try:
        import winreg
    except Exception:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            PERSONALIZE_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except Exception:
        return None
    if value == 0:
        return "dark"
    if value == 1:
        return "light"
    return None


def detect_os_theme() -> dict:
    """Detect the OS light/dark preference.

    On non-Windows hosts: ``{"available": False, "mode": None,
    "reason": "windows-only"}`` — honest unavailable, not a guess.
    """
    if not is_windows():
        return {"available": False, "mode": None, "source": None,
                "reason": "windows-only"}
    mode = _detect_via_darkdetect()
    if mode is not None:
        return {"available": True, "mode": mode, "source": "darkdetect",
                "reason": "ok"}
    mode = _detect_via_registry()
    if mode is not None:
        return {"available": True, "mode": mode, "source": "registry",
                "reason": "ok"}
    return {"available": False, "mode": None, "source": None,
            "reason": "windows-theme-unknown"}


def detect_windows_accent() -> dict:
    """Read the Windows accent colour as ``#RRGGBB`` or report why not."""
    if not is_windows():
        return {"available": False, "accent": None,
                "reason": "windows-only"}
    try:
        import winreg
    except Exception:
        return {"available": False, "accent": None,
                "reason": "registry-unavailable"}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DWM_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "ColorizationColor")
    except Exception:
        return {"available": False, "accent": None,
                "reason": "accent-not-set"}
    rgb = int(value) & 0xFFFFFF
    return {"available": True, "accent": f"#{rgb:06X}",
            "reason": "ok"}


# ---------------------------------------------------------------------------
# Accent as ONE token override (never a new theme)
# ---------------------------------------------------------------------------

def is_valid_accent(accent: object) -> bool:
    return isinstance(accent, str) and bool(_HEX_RE.match(accent))


def apply_accent(theme: dict, accent: str | None) -> dict:
    """Return ``theme`` with only its ``accent`` token overridden.

    ``accent=None`` returns the theme unchanged (a copy). An invalid
    accent raises ``ValueError`` rather than silently inventing a
    colour. No key besides ``"accent"`` is added, removed or changed,
    and no new theme name is created — accent is a token override
    on the selected theme, never a tenth theme.
    """
    out = dict(theme)
    if accent is None:
        return out
    if not is_valid_accent(accent):
        raise ValueError(f"invalid accent colour: {accent!r} "
                         "(expected #RRGGBB)")
    out["accent"] = accent
    return out


def follow_windows(theme: dict) -> dict:
    """Apply "Follow Windows": OS mode + accent token override.

    Returns ``{"theme": ..., "os_mode": ..., "accent": ...,
    "source": ..., "available": bool, "reason": ...}``. On
    non-Windows hosts the theme is returned unchanged with
    ``available=False``.
    """
    detected = detect_os_theme()
    accent_info = detect_windows_accent()
    accent = accent_info["accent"] if accent_info["available"] else None
    return {
        "theme": apply_accent(theme, accent),
        "os_mode": detected["mode"],
        "accent": accent,
        "source": detected["source"],
        "available": detected["available"],
        "reason": detected["reason"],
    }

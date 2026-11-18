"""Wave 9 (v5.64) — direct DWM backdrops, assigned by surface role.

PJ-22 route: call Desktop Window Manager directly through ``ctypes``,
no framework, no new dependency, no bundled code. This module is the
*only* place that talks to DWM; it never touches the shipped
stylesheet bytes, the theme dicts, or any other leaf's files — a
backdrop is a window attribute, not style bytes, and the two must not
be conflated.

Material assignment by surface role (the brief's PJ-58 material
rules; the full PJ-58 text was not found in the working tree, so the
rules implemented here are exactly the brief's role mapping and are
stated in full below — the coordinator should confirm them against
the canonical PJ-58 text if it lives elsewhere):

  * ``base`` — the long-lived main window: Mica. Mica is the opaque,
    wallpaper-tinted material designed for long-lived primary
    windows; it is cheap to composite and never fights text legibility
    the way a translucent material can.
  * ``tabbed_base`` — a long-lived secondary / tabbed window:
    Mica Alt (``DWM_SYSTEMBACKDROP_TYPE`` tabbed). Stronger tint than
    Mica, for windows that hold tabs.
  * ``transient`` — dialogs, popups, the command palette, flyouts:
    Acrylic. Translucent, for surfaces that appear briefly and go
    away; Acrylic is deliberately NOT used for the long-lived base,
    where its cost and its legibility risk would be paid forever.
  * ``none`` — explicit opt-out (plain backdrop, no material).

Corner + border follow the same role split: long-lived bases get
fully round corners, transient surfaces get small round corners, and
the border/caption/text colours are left at the system default unless
a caller passes an explicit border colour.

Windows-only, platform-guarded: on any non-Windows platform every
apply call is an honest no-op returning ``applied=False`` with the
resolved material still reported (so callers can log what *would*
have been applied). DWM cannot render on this Linux VM, so Windows
render proof is documented as OWED below and is never faked or
simulated here.

Re-application contract: callers re-apply on theme change and on DPI
change (:func:`on_theme_changed` / :func:`on_dpi_changed`). DWM
backdrops normally survive both, but re-applying is cheap and makes
a stale backdrop after a system-theme switch impossible by
construction. The material itself is role-driven, not theme-driven:
a theme change re-applies the same role material.

Known interaction, NOT solved here (Windows validation item): the
app shell is frameless with a translucent-background attribute and
paints an opaque QSS background over the window. Whether Mica shows
through therefore depends on the shell's paint path, and only a
Windows render check can settle it. This module sets window
attributes only; it does not change any paint or stylesheet bytes
to force the issue.

No telemetry, no network, stdlib only (``ctypes`` + ``sys``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

MODULE_VERSION = "5.64.0"

#: Windows render proof status. DWM cannot render on this Linux VM,
#: so no render is claimed anywhere in this module: the proof is a
#: Windows 11 screenshot pass (Mica on the main window, Acrylic on a
#: transient dialog, Mica Alt on a tabbed window) on real hardware.
RENDER_PROOF = (
    "OWED: Windows 11 render proof (screenshots of Mica / Acrylic / "
    "Mica Alt on real Win11 hardware). DWM cannot render on this "
    "Linux VM; no render is claimed or simulated."
)

# ------------------------------------------------------------- DWM constants
# DWMWINDOWATTRIBUTE values (dwmapi.h). Only the four this module
# sets are listed; nothing else is touched.
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_SYSTEMBACKDROP_TYPE = 38

#: DWMWA_COLOR_DEFAULT — "let the system decide" for colours.
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF

# DWM_SYSTEMBACKDROP_TYPE values (Windows 11).
BACKDROP_AUTO = 0
BACKDROP_NONE = 1
BACKDROP_MICA = 2
BACKDROP_ACRYLIC = 3
BACKDROP_MICA_ALT = 4

# DWM_WINDOW_CORNER_PREFERENCE values.
CORNER_DEFAULT = 0
CORNER_DO_NOT_ROUND = 1
CORNER_ROUND = 2
CORNER_ROUND_SMALL = 3

#: Canonical roles -> material name (what a caller / log line reads).
ROLE_MATERIAL: dict[str, str] = {
    "base": "mica",
    "tabbed_base": "mica_alt",
    "transient": "acrylic",
    "none": "none",
}

#: Aliases for caller convenience; they canonicalise to "transient".
ROLE_ALIASES: dict[str, str] = {
    "dialog": "transient",
    "popup": "transient",
    "palette": "transient",
    "flyout": "transient",
    "main": "base",
    "tabbed": "tabbed_base",
}

#: Material name -> DWM_SYSTEMBACKDROP_TYPE value.
MATERIAL_VALUE: dict[str, int] = {
    "auto": BACKDROP_AUTO,
    "none": BACKDROP_NONE,
    "mica": BACKDROP_MICA,
    "acrylic": BACKDROP_ACRYLIC,
    "mica_alt": BACKDROP_MICA_ALT,
}

#: Canonical role -> corner preference.
ROLE_CORNER: dict[str, int] = {
    "base": CORNER_ROUND,
    "tabbed_base": CORNER_ROUND,
    "transient": CORNER_ROUND_SMALL,
    "none": CORNER_DEFAULT,
}

#: Canonical role -> one-line rationale (for logs / the gate record).
ROLE_RATIONALE: dict[str, str] = {
    "base": "long-lived primary window: opaque Mica, cheap, legible",
    "tabbed_base": "long-lived tabbed window: Mica Alt, stronger tint",
    "transient": "brief surface: translucent Acrylic, never the base",
    "none": "explicit opt-out: no DWM material",
}


def canonical_role(role: str) -> str:
    """Canonicalise a role name (aliases resolved) or raise ValueError.

    Unknown roles raise rather than silently falling back to a
    material: a wrong backdrop on the wrong surface is exactly the
    failure this mapping exists to prevent.
    """
    if not isinstance(role, str):
        raise ValueError(f"backdrop role must be a string, got {role!r}")
    key = role.strip().lower()
    if key in ROLE_MATERIAL:
        return key
    if key in ROLE_ALIASES:
        return ROLE_ALIASES[key]
    raise ValueError(
        f"unknown backdrop role {role!r}; "
        f"choose from {sorted(set(ROLE_MATERIAL) | set(ROLE_ALIASES))}"
    )


def resolve_material(role: str) -> str:
    """Material name (``mica`` / ``mica_alt`` / ``acrylic`` / ``none``)."""
    return ROLE_MATERIAL[canonical_role(role)]


def resolve_corner(role: str) -> int:
    """DWM corner-preference value for the role."""
    return ROLE_CORNER[canonical_role(role)]


def describe_assignment(role: str) -> dict:
    """Full assignment for a role: material, DWM values, rationale."""
    canon = canonical_role(role)
    material = ROLE_MATERIAL[canon]
    return {
        "role": canon,
        "requested_role": role,
        "material": material,
        "backdrop_value": MATERIAL_VALUE[material],
        "corner": ROLE_CORNER[canon],
        "border_color": "system default",
        "rationale": ROLE_RATIONALE[canon],
        "render_proof": RENDER_PROOF,
    }


def is_supported() -> bool:
    """True only on Windows, where DWM exists.

    On every other platform this is False and every apply call is a
    no-op (see :func:`apply_backdrop`). The check is ``sys.platform``
    only — importing ``ctypes.windll`` on Linux raises, so it is
    never attempted off Windows.
    """
    return sys.platform == "win32"


def _dwmapi():
    """The dwmapi ctypes handle on Windows, else None (never raises)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return ctypes.windll.dwmapi
    except Exception:  # noqa: BLE001 - any load failure means unsupported
        return None


def _extract_hwnd(target) -> int:
    """Extract a native window handle from an int or a Qt widget.

    Accepts a raw HWND int, or any object with a callable ``winId()``
    (``QWidget.winId``). Anything else raises TypeError; a zero /
    negative handle raises ValueError — a widget with no native
    window yet (``winId() == 0``) must be shown first, and silently
    applying to handle 0 would be a lie about what got a backdrop.
    """
    if isinstance(target, bool):
        raise TypeError(f"window handle must be an int or widget, got {target!r}")
    if isinstance(target, int):
        hwnd = target
    else:
        win_id = getattr(target, "winId", None)
        if not callable(win_id):
            raise TypeError(
                "backdrop target must be an HWND int or an object "
                f"with winId(), got {type(target).__name__}"
            )
        hwnd = int(win_id())
    if hwnd <= 0:
        raise ValueError(
            f"invalid window handle {hwnd}: the window has no native "
            "handle yet (show it first, then apply the backdrop)"
        )
    return hwnd


@dataclass
class BackdropResult:
    """Outcome of one backdrop application (or honest no-op)."""

    applied: bool
    platform: str
    role: str
    material: str
    backdrop_value: int
    corner: int
    hwnd: int
    trigger: str = "initial"
    reason: str = ""
    render_proof: str = RENDER_PROOF
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "applied": self.applied,
            "platform": self.platform,
            "role": self.role,
            "material": self.material,
            "backdrop_value": self.backdrop_value,
            "corner": self.corner,
            "hwnd": self.hwnd,
            "trigger": self.trigger,
            "reason": self.reason,
            "render_proof": self.render_proof,
            "error": self.error,
        }


def apply_backdrop(target, role: str, *, border_color=None,
                   trigger: str = "initial") -> BackdropResult:
    """Apply the role's DWM backdrop to a window (or no-op honestly).

    ``target`` is an HWND int or a widget with ``winId()``.
    ``border_color`` is an optional COLORREF int (``0x00BBGGRR``);
    when omitted the system border colour is left untouched.
    Caption/text colours are always left at the system default —
    this module sets backdrop + corner (+ optional border) and
    nothing else.

    On non-Windows platforms this returns ``applied=False`` with
    ``reason`` stating the platform guard; the resolved material is
    still reported so logs show intent, not silence.
    """
    canon = canonical_role(role)
    material = ROLE_MATERIAL[canon]
    hwnd = _extract_hwnd(target)
    base = {
        "platform": sys.platform,
        "role": canon,
        "material": material,
        "backdrop_value": MATERIAL_VALUE[material],
        "corner": ROLE_CORNER[canon],
        "hwnd": hwnd,
        "trigger": trigger,
    }
    if not is_supported():
        return BackdropResult(
            applied=False,
            reason=(
                "platform-guarded no-op: DWM exists only on Windows; "
                f"running on {sys.platform}, so no backdrop was set. "
                "Windows render proof remains OWED (see render_proof)."
            ),
            **base,
        )
    dwmapi = _dwmapi()
    if dwmapi is None:
        return BackdropResult(
            applied=False,
            reason="dwmapi unavailable on this Windows install",
            error="ctypes.windll.dwmapi could not be loaded",
            **base,
        )
    try:
        import ctypes

        def _set(attribute: int, value: int) -> int:
            holder = ctypes.c_int(value)
            return int(dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_int(attribute),
                ctypes.byref(holder),
                ctypes.sizeof(holder),
            ))

        hr_backdrop = _set(DWMWA_SYSTEMBACKDROP_TYPE,
                           MATERIAL_VALUE[material])
        if hr_backdrop != 0:
            return BackdropResult(
                applied=False,
                reason="DwmSetWindowAttribute (backdrop) failed",
                error=f"HRESULT 0x{hr_backdrop & 0xFFFFFFFF:08X}",
                **base,
            )
        hr_corner = _set(DWMWA_WINDOW_CORNER_PREFERENCE,
                         ROLE_CORNER[canon])
        if hr_corner != 0:
            return BackdropResult(
                applied=False,
                reason="DwmSetWindowAttribute (corner) failed",
                error=f"HRESULT 0x{hr_corner & 0xFFFFFFFF:08X}",
                **base,
            )
        if border_color is not None:
            if not isinstance(border_color, int) or border_color < 0:
                raise ValueError(
                    f"border_color must be a COLORREF int, got {border_color!r}"
                )
            hr_border = _set(DWMWA_BORDER_COLOR, border_color)
            if hr_border != 0:
                return BackdropResult(
                    applied=False,
                    reason="DwmSetWindowAttribute (border) failed",
                    error=f"HRESULT 0x{hr_border & 0xFFFFFFFF:08X}",
                    **base,
                )
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed DWM call is a result
        return BackdropResult(
            applied=False,
            reason="DWM call raised instead of returning an HRESULT",
            error=f"{type(exc).__name__}: {exc}",
            **base,
        )
    return BackdropResult(
        applied=True,
        reason=f"DWM backdrop {material} + corner {ROLE_CORNER[canon]} set",
        **base,
    )


def apply_to_window(widget, role: str, **kwargs) -> BackdropResult:
    """Convenience wrapper: :func:`apply_backdrop` for a Qt widget."""
    return apply_backdrop(widget, role, **kwargs)


def on_theme_changed(target, role: str, theme_name=None) -> BackdropResult:
    """Re-apply the role's backdrop after a theme change.

    The material is role-driven, so the theme name is recorded in the
    reason only — it never changes the material. Re-application (not
    a material switch) is the contract.
    """
    result = apply_backdrop(target, role, trigger="theme")
    result.reason = (
        f"re-applied on theme change"
        + (f" to {theme_name!r}" if theme_name else "")
        + f"; {result.reason}"
    )
    return result


def on_dpi_changed(target, role: str, dpi=None) -> BackdropResult:
    """Re-apply the role's backdrop after a DPI change (same contract)."""
    result = apply_backdrop(target, role, trigger="dpi")
    result.reason = (
        "re-applied on DPI change"
        + (f" (dpi={dpi})" if dpi is not None else "")
        + f"; {result.reason}"
    )
    return result

"""
Shared helpers for the non-Windows platform backends (v5.50,
programme items 41/42).

Everything here is platform-neutral: the bounds fence, the
capture-geometry sentence, and small formatters for scripted /
seam-provided accessibility elements (plain dicts). The Windows
backend does NOT use this module — it delegates to the legacy
modules, byte-for-byte as before (v5.45, P-3). FakeBackend
(fake.py) and the seamed native backends (_seamed.py, behind
macos.py / linux.py) share these bodies so every non-Windows
backend speaks the same geometry contract.

Element dict shape (the seam/script vocabulary):
    {"name": str, "class_name": str, "control_type": str,
     "rect": (left, top, right, bottom),
     "enabled": bool, "visible": bool,
     "is_password": True | False | None,
     "value": str, "children": [element dict, ...]}
"""

from __future__ import annotations

from .windows import _clamp, _fit_within, _norm1000_to_pixels, _to_pixels

__all__ = [
    "_clamp", "_fit_within", "_norm1000_to_pixels", "_to_pixels",
    "fence_point", "capture_geometry_line", "rect_text",
    "format_facts", "element_label",
]


def fence_point(x, y, bounds=None, virtual_bounds=None):
    """The bounds fence (see PORTING.md): clamp (x, y) into the
    virtual-screen bounds; unknown bounds pass the point through;
    non-numeric input passes through untouched for the caller's
    own validation. `virtual_bounds` is a zero-arg callable used
    when `bounds` is None."""
    try:
        xi, yi = int(round(float(x))), int(round(float(y)))
    except (TypeError, ValueError):
        return x, y
    if bounds is None and virtual_bounds is not None:
        try:
            bounds = virtual_bounds()
        except Exception:  # noqa: BLE001 — unknown bounds pass through
            bounds = None
    if bounds is None:
        return xi, yi
    min_x, min_y, max_x, max_y = bounds
    return _clamp(xi, min_x, max_x), _clamp(yi, min_y, max_y)


def capture_geometry_line(info=None) -> str:
    """One sentence stating a capture's coordinate contract —
    the same wording core/desktop/vision.py's line uses (screen size,
    sent image size, scaled-vs-matching, crop origin/size, and
    the norm1000 cursor default), computed from `info`. "" when
    no capture geometry is known."""
    if not info:
        return ""
    try:
        sw, sh = int(info["screen_width"]), int(info["screen_height"])
        iw, ih = int(info["image_width"]), int(info["image_height"])
    except (KeyError, TypeError, ValueError):
        return ""
    if (sw, sh) == (iw, ih):
        line = (f"Screen is {sw}x{sh}; this image is {iw}x{ih} — "
                f"coordinates in the image match screen pixels.")
    else:
        line = (f"Screen is {sw}x{sh}; this image is {iw}x{ih} — "
                f"coordinates in the image are scaled.")
    if info.get("cropped"):
        try:
            cl, ct = int(info["crop_left"]), int(info["crop_top"])
            cw, ch = int(info["crop_width"]), int(info["crop_height"])
        except (KeyError, TypeError, ValueError):
            return line
        line += (f" The image shows the foreground window at "
                 f"({cl}, {ct}), {cw}x{ch}.")
    line += (" Coordinates for cursor_control default to norm1000 "
             "(0-1000 across the image); pass coords='pixels' for "
             "true screen pixels.")
    return line


def rect_text(rect) -> str:
    """'(l,t)–(r,b)' for a rect tuple, else 'unknown position' —
    the shape core/desktop/uia.py's _element_facts produces."""
    try:
        left, top, right, bottom = (int(v) for v in rect)
        return f"({left},{top})–({right},{bottom})"
    except (TypeError, ValueError):
        return "unknown position"


def format_facts(facts: dict) -> str:
    """'• Name (Type, class X) at (l,t)–(r,b)' — mirrors
    core/desktop/uia.py's _format_facts for an element dict."""
    cls = f", class {facts['class_name']}" if facts.get("class_name") else ""
    return (f"• {facts.get('name', '')} ({facts.get('control_type', '')}"
            f"{cls}) at {rect_text(facts.get('rect'))}")


def element_label(facts: dict | None) -> str | None:
    """'"Name" (Type)' for an element dict, or None when it has
    no name — the focused_control_label shape."""
    if not facts or not facts.get("name"):
        return None
    label = f'"{facts["name"][:60]}"'
    if facts.get("control_type"):
        label += f" ({facts['control_type']})"
    return label

"""Wave 9 (v5.64) — hand-authored SVG status glyphs.

Every glyph is an SVG string in this module. There are NO separate
asset files, no bundled images, and no external references — a
glyph renders from this string alone. Animation uses ONLY
transform, colour (fill) and opacity, via CSS ``@keyframes`` inside
each SVG's ``<style>`` block; there is no script, no SMIL event
scripting, and no layout-affecting animation (no width/height
changes, so a pulsing glyph never shifts surrounding layout).

Explicitly NOT the orb: these are small status glyphs (listening,
thinking, speaking, idle, error, muted, success, offline). The orb
lives in ``gui/visualizer.py`` and is untouched by this module.

Safety invariants, checked by the suite for every glyph:

* parses as XML;
* contains no ``<script>``, no ``on*`` handler, no ``href``/``url(``
  external reference;
* every ``@keyframes`` body animates only transform, opacity,
  fill or fill-opacity.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# Command Deck Phase E: glyph colours are the token palette (D2:
# listening and speaking are both cyan; the glyph shape, not the
# colour, tells them apart).
from .style import ACCENT, ACCENT_2, DANGER, OK, TEXT, TEXT_MUTED

MODULE_VERSION = "5.64.0"

_SIZE = 48
_STYLE = """<style>
@keyframes v564-pulse { 0%,100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.55; transform: scale(0.88); } }
@keyframes v564-spin { to { transform: rotate(360deg); } }
@keyframes v564-blink { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
.pulse { animation: v564-pulse 1.6s ease-in-out infinite;
  transform-origin: center; transform-box: fill-box; }
.spin { animation: v564-spin 1.2s linear infinite;
  transform-origin: center; transform-box: fill-box; }
.blink { animation: v564-blink 1.4s ease-in-out infinite; }
</style>"""


def _svg(inner: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_SIZE}" '
            f'height="{_SIZE}" viewBox="0 0 48 48">{_STYLE}{inner}</svg>')


_GLYPHS: dict[str, str] = {
    "idle": _svg(f'<circle cx="24" cy="24" r="14" fill="{TEXT_MUTED}" '
                 'fill-opacity="0.55"/>'),
    "listening": _svg(f'<g class="pulse"><circle cx="24" cy="24" r="14" '
                      f'fill="{ACCENT}"/><circle cx="24" cy="24" r="6" '
                      f'fill="{TEXT}"/></g>'),
    "thinking": _svg(f'<g class="spin"><circle cx="24" cy="24" r="14" '
                     f'fill="none" stroke="{ACCENT_2}" stroke-width="4" '
                     'stroke-dasharray="22 66" stroke-linecap="round"/>'
                     '</g>'),
    "speaking": _svg(f'<g class="blink"><rect x="12" y="18" width="5" '
                     f'height="12" rx="2" fill="{ACCENT}"/><rect x="21" '
                     f'y="12" width="5" height="24" rx="2" '
                     f'fill="{ACCENT}"/><rect x="30" y="18" width="5" '
                     f'height="12" rx="2" fill="{ACCENT}"/></g>'),
    "error": _svg(f'<circle cx="24" cy="24" r="14" fill="{DANGER}"/>'
                  f'<path d="M18 18 L30 30 M30 18 L18 30" stroke="{TEXT}" '
                  'stroke-width="4" stroke-linecap="round"/>'),
    "muted": _svg(f'<circle cx="24" cy="24" r="14" fill="{TEXT_MUTED}" '
                  f'fill-opacity="0.35"/><path d="M17 31 L31 17" '
                  f'stroke="{DANGER}" stroke-width="4" '
                  'stroke-linecap="round"/>'),
    "success": _svg(f'<circle cx="24" cy="24" r="14" fill="{OK}"/>'
                    f'<path d="M17 24 L22 29 L31 19" fill="none" '
                    f'stroke="{TEXT}" stroke-width="4" '
                    'stroke-linecap="round" stroke-linejoin="round"/>'),
    "offline": _svg(f'<circle cx="24" cy="24" r="14" fill="none" '
                    f'stroke="{TEXT_MUTED}" stroke-width="3" '
                    'stroke-dasharray="4 5"/>'),
}

_ALLOWED_KEYFRAME_PROPS = {"transform", "opacity", "fill",
                           "fill-opacity", "stroke-opacity"}


def glyph_names() -> list[str]:
    return sorted(_GLYPHS)


def glyph(name: str) -> str:
    """Return the SVG string for ``name``; ``KeyError`` when unknown."""
    return _GLYPHS[name]


def validate_glyph(name: str) -> dict:
    """Check the safety invariants for one glyph.

    Returns ``{"name": ..., "valid": bool, "reasons": [...]}``.
    A glyph is valid only when it parses as XML, has no script /
    handler / external reference, and every keyframe property is
    in the transform/colour/opacity allow-list.
    """
    reasons: list[str] = []
    svg = _GLYPHS.get(name)
    if svg is None:
        return {"name": name, "valid": False, "reasons": ["unknown-glyph"]}
    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        return {"name": name, "valid": False,
                "reasons": [f"xml-parse-error: {exc}"]}
    lowered = svg.lower()
    if "<script" in lowered:
        reasons.append("script-element")
    if re.search(r"\son\w+\s*=", lowered):
        reasons.append("event-handler")
    # url(#...) fragment references are internal; any other url(
    # is an external reference.
    external_urls = [u for u in re.findall(r"url\(([^)]*)\)", lowered)
                     if not u.strip().startswith("#")]
    if "href=" in lowered or external_urls:
        reasons.append("external-reference")
    for block in re.findall(r"@keyframes\s+\S+\s*\{(.*?)\}\s*$",
                            svg, flags=re.DOTALL):
        for decl in re.findall(r"([a-z-]+)\s*:", block):
            if decl not in _ALLOWED_KEYFRAME_PROPS:
                reasons.append(f"disallowed-keyframe-property: {decl}")
    return {"name": name, "valid": bool(not reasons), "reasons": reasons}


def validate_all_glyphs() -> dict:
    results = {name: validate_glyph(name) for name in _GLYPHS}
    return {"all_valid": all(r["valid"] for r in results.values()),
            "glyphs": results}

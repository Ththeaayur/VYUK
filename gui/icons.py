"""
Phosphor Duotone icon family for VYUK (B-R1-6, 2026-11-09).

Replaces the single-stroke line-icon language (Phase 2 inline
templates + the Lucide bundle, 2026-11-02): every icon is now a
Phosphor Duotone icon — two tones, a primary fill at full
opacity and a secondary fill at 20% — bundled in
``gui/assets/icons/phosphor/`` and resolved through the
manifest (``gui/icon_manifest.py`` + ``core/w9_wiring.py``)
before the inline templates. The templates stay as the honest
fallback for names the bundle does not cover (``senses`` —
no Phosphor stem exists for the radar/sonar glyph).

Icons are rendered to QPixmap at the exact size + colour
needed, so every icon shares one visual language. The
duotone needs no loader change: the Phosphor SVGs carry
``fill="currentColor"`` with the secondary path at
``opacity="0.2"``, so the requested colour lands in both
tones automatically.
"""

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_TEMPLATES: dict[str, str] = {
    # four-point sparkle — Assistant
    "spark": '<path d="M12 3c.7 4.8 2.2 6.9 7 8-4.8 1.1-6.3 3.2-7 8-.7-4.8-2.2-6.9-7-8 4.8-1.1 6.3-3.2 7-8Z"/>',
    # clock — Reminders
    "clock": '<circle cx="12" cy="12" r="8.2"/><path d="M12 7.5V12l3 2"/>',
    # database — Memory
    "database": '<ellipse cx="12" cy="5.8" rx="7.5" ry="2.9"/><path d="M4.5 5.8v12.4c0 1.6 3.4 2.9 7.5 2.9s7.5-1.3 7.5-2.9V5.8"/><path d="M4.5 12c0 1.6 3.4 2.9 7.5 2.9s7.5-1.3 7.5-2.9"/>',
    # grid — Skills
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.8"/><rect x="13" y="4" width="7" height="7" rx="1.8"/><rect x="4" y="13" width="7" height="7" rx="1.8"/><rect x="13" y="13" width="7" height="7" rx="1.8"/>',
    # sliders — Settings
    "sliders": '<path d="M5 7h6M15 7h4M5 12h2M11 12h8M5 17h10M19 17h0"/><circle cx="13" cy="7" r="2.1"/><circle cx="9" cy="12" r="2.1"/><circle cx="17" cy="17" r="2.1"/>',
    # microphone
    "send": '<path d="M20.5 3.8 10 14.3M20.5 3.8 14 20.2l-3.8-5.9L4.2 10.5Z"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11.5a6 6 0 0 0 12 0M12 17.5V21M9 21h6"/>',
    # pencil — Dictate (field-check fix #6, U3 #47): dictation
    # writes words down as a draft, so its button carries a
    # pencil, not a second mic — at the shipped 300 px panel
    # width the Dictate label collapses away and two mic glyphs
    # were all that was left to tell the controls apart.
    "pencil": '<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="M15 5l4 4"/>',
    # monitor / screen share
    "monitor": '<rect x="3" y="4.5" width="18" height="12" rx="2"/><path d="M12 16.5V20M8.5 20h7"/>',
    # volume on
    "volume": '<path d="M4 9.8v4.4h3L12 19V5l-5 4.8H4Z"/><path d="M15.5 9.5a4 4 0 0 1 0 5M18 7a7.5 7.5 0 0 1 0 10"/>',
    # volume off / mute
    "volume_off": '<path d="M4 9.8v4.4h3L12 19V5l-5 4.8H4Z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
    # window controls
    "minus": '<path d="M6 12h12"/>',
    "square": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    # play (briefing / previews)
    "play": '<path d="M8 5.5v13l11-6.5Z" fill="{color}" stroke="none"/>',
    # check
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    # bell
    "bell": '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.2 2.5H4.8L6 16Z"/><path d="M10 21a2.2 2.2 0 0 0 4 0"/>',
    # search / magnifier (Phase 3 command palette pill)
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M15.8 15.8 20.5 20.5"/>',
    # pulse / activity (v5.18 Pulse page nav)
    "pulse": '<path d="M2.5 12h4l2.5-6.5 4.5 13L16 12h5.5"/>',
    "senses": '<path d="M12 12h.01"/><path d="M15.5 8.5a5 5 0 1 0 0 7"/><path d="M18.4 5.6a9 9 0 1 0 0 12.8"/><path d="M12 12l6.5-6.5"/>',
    # globe (Wave 5 extension nav): circle with meridian + parallel.
    "globe": '<circle cx="12" cy="12" r="8.2"/><ellipse cx="12" cy="12" rx="3.6" ry="8.2"/><path d="M3.8 12h16.4M5.5 7.5h13M5.5 16.5h13"/>',
}

_cache: dict[tuple[str, str, int], QIcon] = {}


def _bundled_svg_path(name: str):
    """Resolve a bundled Phosphor SVG by reading the shipped
    manifest directly (no w9_wiring import).

    The bundle path in make_icon goes through w9_wiring, whose
    module import chain pulls in core.memory — and the test
    harness stubs that module, so the import fails and every
    bundled name falls through to the inline templates (which
    only carry the original few names). Reading the manifest
    JSON directly avoids the chain: the manifest is data, and
    its entries map name -> relative SVG path. Returns the
    absolute Path, or None when the name is not bundled."""
    try:
        from pathlib import Path as _Path
        import json as _json
        manifest = (_Path(__file__).resolve().parent / "assets"
                    / "icons" / "phosphor" / "manifest.json")
        if not manifest.is_file():
            return None
        data = _json.loads(manifest.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        wanted = name
        if "_" in wanted:
            wanted = wanted.replace("_", "-")
        for entry in entries:
            if entry.get("name") == wanted:
                rel = entry.get("path", "")
                if not rel:
                    return None
                return manifest.parent / rel
        return None
    except Exception:  # noqa: BLE001 — no bundle, templates below
        return None


def _render_bundled_svg(path, color: str, size: int) -> QIcon:
    """Render a bundled Phosphor duotone SVG to a QIcon."""
    from pathlib import Path as _Path
    from PySide6.QtCore import QByteArray as _BA
    from PySide6.QtGui import QPixmap as _PM
    from PySide6.QtSvg import QSvgRenderer as _SR
    from PySide6.QtGui import QPainter as _QP
    _svg = _Path(path).read_text(encoding="utf-8")
    _svg = _svg.replace("currentColor", color)
    _renderer = _SR(_BA(_svg.encode("utf-8")))
    # Look-fix 2026-11-24: the old `_pix.fill(0x00000000)` int
    # fill is read as a GlobalColor enum (color0 = opaque black),
    # so the pixmap never got an alpha channel and every bundled
    # icon rendered as a glyph on a solid black square tile
    # (sidebar, composer send/mic, window controls). Render
    # exactly like the template path below: transparent fill at
    # 2x with devicePixelRatio, so the glyph is transparent
    # outside its strokes.
    _pix = _PM(size * 2, size * 2)
    _pix.fill(Qt.GlobalColor.transparent)
    _painter = _QP(_pix)
    _renderer.render(_painter)
    _painter.end()
    _pix.setDevicePixelRatio(2)
    return QIcon(_pix)


def make_icon(name: str, color: str = None, size: int = 20) -> QIcon:
    """Render the named line icon in the given colour as a QIcon.

    Command Deck Phase E: the default icon colour is the muted
    text token; callers pass an explicit colour when they need one.
    """
    if color is None:
        from .style import TEXT_MUTED as _DEFAULT_ICON_COLOR
        color = _DEFAULT_ICON_COLOR
    # Conversion Wave 9: the v564 icon manifest is consulted first
    # (validated at load inside w9_wiring). With no bundling step
    # installed the manifest is empty and every name resolves to
    # missing, so loading falls through to the templates below —
    # the manifest FEEDS this loader, it never replaces it.
    try:
        from voice_assistant.core.gui_facade import w9_wiring as _w9
        _ref = _w9.resolve_icon(name)
        if _ref.available and _ref.path:
            return _render_bundled_svg(_ref.path, color, size)
    except Exception:  # noqa: BLE001 — direct manifest below
        pass
    # The w9_wiring import chain pulls in core.memory, which the
    # test harness stubs — so in the harness the bundle lookup
    # above always fails. Read the shipped manifest directly.
    try:
        _direct = _bundled_svg_path(name)
        if _direct is not None and _direct.is_file():
            return _render_bundled_svg(_direct, color, size)
    except Exception:  # noqa: BLE001 — templates are the fallback
        pass
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    body = _TEMPLATES[name].replace("{color}", color)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    scale = 2  # render at 2x for crispness on high-DPI displays
    pix = QPixmap(size * scale, size * scale)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    pix.setDevicePixelRatio(scale)
    icon = QIcon(pix)
    _cache[key] = icon
    return icon

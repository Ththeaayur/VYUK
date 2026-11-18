# Photo FINISH tools: watermark, border, collage.
# Split out of core/skills_photo.py (skills split Phase A batch A17).

import math
from pathlib import Path
# SKILLS SPLIT PHASE A BATCH A17 INTENTIONAL: cross-part imports —
# these names now live in skills_photo_helpers; the tools resolve
# them in THIS module's globals (plan SS2 P3), not the facade's.
from .skills_photo_helpers import (
    _as_float,
    _as_int,
    _has_alpha,
    _human_size,
    _load_oriented,
    _load_pil,
    _resolve_output,
    _result_line,
    _save_result,
)

# ---------------------------------------------------------------------------
# WATERMARK IMAGE
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "arial.ttf",
    "DejaVuSans.ttf",
)


_WATERMARK_POSITIONS = ("bottom_right", "bottom_left", "top_right",
                        "top_left", "center")


def _load_font(ImageFont, size: int):
    """A TrueType font at `size`, or (PIL's built-in font, False).

    Returns (font, is_truetype). Candidates are module-level so
    tests can force the fallback branch."""
    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size), True
        except Exception:  # noqa: BLE001 — try the next candidate
            continue
    return ImageFont.load_default(), False


def watermark_image(path: str, text: str, position: str = "bottom_right",
                    opacity: float = 0.5, output: str = "") -> str:
    """Stamp a text watermark onto one image, as a NEW file.

    The text is drawn on a transparent overlay and
    alpha-composited over the picture at the given opacity
    (0.1–1.0), so the photo underneath keeps its full quality.
    Positions: bottom_right, bottom_left, top_right, top_left,
    center. The font is a system TrueType at a size proportional
    to the picture; if none is found, Pillow's small built-in
    font is used and the result says so. The input is never
    overwritten: the default output is '<name>_watermarked.<ext>'
    beside it, and a taken output name gets a ' (2)'-style
    suffix. A JPEG output flattens transparency onto white, and
    the result says so.
    """
    text = str(text or "")
    if not text.strip():
        return "The watermark needs some text — an empty watermark stamps nothing."
    position = str(position or "").strip().lower()
    if position not in _WATERMARK_POSITIONS:
        return ("position must be one of: "
                + ", ".join(_WATERMARK_POSITIONS)
                + f" (got '{position}').")
    opacity_value = _as_float(opacity, "opacity")
    if isinstance(opacity_value, str):
        return opacity_value
    if not 0.1 <= opacity_value <= 1.0:
        return (f"opacity must be between 0.1 and 1.0 (got "
                f"{opacity_value:g}) — 0.5 is a typical watermark.")
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_size = image.size
    Image, err = _load_pil()
    if err:
        return err
    from PIL import ImageDraw, ImageFont

    width, height = image.size
    font_size = max(14, width // 28)
    font, is_truetype = _load_font(ImageFont, font_size)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    margin = max(10, width // 50)
    anchors = {
        "bottom_right": (width - margin - text_w, height - margin - text_h),
        "bottom_left": (margin, height - margin - text_h),
        "top_right": (width - margin - text_w, margin),
        "top_left": (margin, margin),
        "center": ((width - text_w) // 2, (height - text_h) // 2),
    }
    x, y = anchors[position]
    alpha = round(255 * opacity_value)
    draw.text((x - bbox[0], y - bbox[1]), text, font=font,
              fill=(255, 255, 255, alpha))
    result = Image.alpha_composite(image.convert("RGBA"), overlay)
    notes = [f"Watermark '{text}' stamped at {position} with opacity "
             f"{opacity_value:g}."]
    if not is_truetype:
        notes.append("No system TrueType font was found, so Pillow's "
                     "small built-in font was used — the text is "
                     "smaller than usual.")
    dest = _resolve_output(target, output,
                           f"{target.stem}_watermarked{target.suffix}")
    final, save_notes = _save_result(Image, result, dest, image.format)
    if final is None:
        return save_notes
    return _result_line("Watermarked", target, old_size, result.size,
                        final, notes + save_notes)


# ---------------------------------------------------------------------------
# ADD BORDER
# ---------------------------------------------------------------------------

_NAMED_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "blue": (0, 0, 255),
    "green": (0, 128, 0),
    "yellow": (255, 255, 0),
    "pink": (255, 192, 203),
    "purple": (128, 0, 128),
    "orange": (255, 165, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
}


def _parse_color(color: str):
    """(r, g, b) for '#RRGGBB' or a named colour, else an error string."""
    text = str(color or "").strip().lower()
    if text in _NAMED_COLORS:
        return _NAMED_COLORS[text]
    if text.startswith("#") and len(text) == 7:
        try:
            return (int(text[1:3], 16), int(text[3:5], 16),
                    int(text[5:7], 16))
        except ValueError:
            pass
    named = ", ".join(sorted({k for k in _NAMED_COLORS if k != "grey"}))
    return (f"That colour isn't understood: '{color}'. Use a hex "
            f"colour like '#3366cc', or one of: {named}.")


def add_border(path: str, width_px: int = 24, color: str = "#000000",
               output: str = "") -> str:
    """Frame one image with a solid border, as a NEW file.

    The border ADDS `width_px` pixels (1–500) on every side, so
    the picture grows by twice the width in each dimension. The
    colour is '#RRGGBB' hex or a named colour (black, white,
    red, blue, green, yellow, pink, purple, orange, gray). The
    input is never overwritten: the default output is
    '<name>_framed.<ext>' beside it, and a taken output name
    gets a ' (2)'-style suffix.
    """
    width_value = _as_int(width_px, "width_px")
    if isinstance(width_value, str):
        return width_value
    if not 1 <= width_value <= 500:
        return (f"The border width must be between 1 and 500 pixels "
                f"(got {width_value}).")
    rgb = _parse_color(color)
    if isinstance(rgb, str):
        return rgb
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_size = image.size
    Image, err = _load_pil()
    if err:
        return err
    from PIL import ImageOps

    work = image
    fill = rgb
    if _has_alpha(work):
        work = work.convert("RGBA")
        fill = rgb + (255,)
    elif work.mode == "P":
        work = work.convert("RGB")
    elif work.mode == "L":
        fill = rgb[0]
    result = ImageOps.expand(work, border=width_value, fill=fill)
    notes = [f"Added a {width_value}px border in "
             f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}."]
    dest = _resolve_output(target, output,
                           f"{target.stem}_framed{target.suffix}")
    final, save_notes = _save_result(Image, result, dest, image.format)
    if final is None:
        return save_notes
    return _result_line("Framed", target, old_size, result.size,
                        final, notes + save_notes)


# ---------------------------------------------------------------------------
# MAKE COLLAGE
# ---------------------------------------------------------------------------

def _crop_to_fill(Image, image, cell_w: int, cell_h: int):
    """Scale (up if needed) and centre-crop so `image` exactly
    fills a cell_w × cell_h cell."""
    from PIL import ImageOps

    fitted = ImageOps.fit(image, (cell_w, cell_h),
                          method=Image.LANCZOS, centering=(0.5, 0.5))
    return fitted


def make_collage(paths, columns: int = 0, output: str = "",
                 background: str = "") -> str:
    """Combine 2–9 images into one grid collage, as a NEW file.

    EVERY input is loaded and validated first — one missing or
    corrupt picture refuses the whole collage (named in the
    result) and nothing is written; there are no partial
    collages. `columns` 0 picks a near-square grid automatically.
    Each picture is centre-cropped to fill its cell exactly
    (crop-to-fill), on a `background` colour (black unless one is
    named — '#RRGGBB' or a colour name); the cell size is the
    median input size, capped at 1200px per side. The default
    output is '<first-stem>_collage.png' beside the first input
    — PNG, so the grid isn't recompressed — and a taken name
    gets a ' (2)'-style suffix.
    """
    if isinstance(paths, str) or not isinstance(paths, (list, tuple)):
        return ("paths must be a LIST of image file paths, e.g. "
                "['a.jpg', 'b.jpg'] — between 2 and 9 of them.")
    paths = [str(p) for p in paths]
    bg_rgb = (0, 0, 0)
    if background and str(background).strip():
        parsed_bg = _parse_color(background)
        if isinstance(parsed_bg, str):
            return parsed_bg
        bg_rgb = parsed_bg
    if len(paths) < 2:
        return ("A collage needs at least 2 pictures "
                f"(got {len(paths)}).")
    if len(paths) > 9:
        return ("A collage holds at most 9 pictures "
                f"(got {len(paths)}) — pick the best nine.")
    cols = _as_int(columns, "columns")
    if isinstance(cols, str):
        return cols
    if cols < 0:
        return f"columns can't be negative (got {cols}); use 0 for automatic."
    Image, err = _load_pil()
    if err:
        return err
    loaded = []
    for p in paths:
        image, target, load_err = _load_oriented(p)
        if load_err:
            return (f"Collage not made — one input is unusable and "
                    f"no partial collage was written. {load_err}")
        loaded.append((image, target))
    count = len(loaded)
    notes: list[str] = []
    if cols == 0:
        cols = math.ceil(math.sqrt(count))
        notes.append(f"Laid out automatically in {cols} column(s).")
    elif cols > count:
        notes.append(f"Only {count} pictures, so the grid uses "
                     f"{count} column(s), not {cols}.")
        cols = count
    rows = math.ceil(count / cols)
    widths = sorted(img.size[0] for img, _ in loaded)
    heights = sorted(img.size[1] for img, _ in loaded)
    mid = count // 2
    if count % 2:
        cell_w, cell_h = widths[mid], heights[mid]
    else:
        cell_w = (widths[mid - 1] + widths[mid]) // 2
        cell_h = (heights[mid - 1] + heights[mid]) // 2
    cell_w = max(1, min(cell_w, 1200))
    cell_h = max(1, min(cell_h, 1200))
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), bg_rgb)
    for index, (image, _target) in enumerate(loaded):
        cell = image.convert("RGBA")
        background = Image.new("RGBA", cell.size, bg_rgb + (255,))
        cell = Image.alpha_composite(background, cell).convert("RGB")
        cell = _crop_to_fill(Image, cell, cell_w, cell_h)
        x = (index % cols) * cell_w
        y = (index // cols) * cell_h
        canvas.paste(cell, (x, y))
    first_target = loaded[0][1]
    if output and str(output).strip():
        dest = Path(str(output)).expanduser()
    else:
        dest = first_target.with_name(f"{first_target.stem}_collage.png")
        notes.append("Saved as PNG so the grid isn't recompressed.")
    if bg_rgb != (0, 0, 0):
        notes.append(f"Background colour: "
                     f"#{bg_rgb[0]:02x}{bg_rgb[1]:02x}{bg_rgb[2]:02x}.")
    notes.append("Each picture was centre-cropped to fill its cell.")
    final, save_notes = _save_result(Image, canvas, dest, "PNG")
    if final is None:
        return save_notes
    lines = [
        f"Made a {cols}×{rows} collage from {count} pictures: {final} "
        f"({canvas.size[0]}×{canvas.size[1]} pixels, "
        f"{_human_size(final.stat().st_size)})."
    ]
    lines.extend(notes + save_notes)
    return " ".join(lines)

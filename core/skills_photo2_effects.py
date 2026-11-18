# Photo Effects implementations (kaleidoscope / duotone / pixelate / glitch).
# Split out of core/skills_photo_effects.py (skills split Phase A batch A6).
import math
import random
# SKILLS SPLIT PHASE A BATCH A6 INTENTIONAL: the effects functions
# resolve these house helpers from the helpers module (bare-name rule).
from .skills_photo2_helpers import (
    _as_int,
    _has_alpha,
    _join_alpha,
    _load_oriented,
    _load_pil,
    _resolve_output,
    _result_line,
    _rgb_ready,
    _save_result,
    _split_alpha,
)


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


# ---------------------------------------------------------------------------
# KALEIDOSCOPE IMAGE
# ---------------------------------------------------------------------------

_KALEIDO_MAX_SIDE = 1024  # the fold canvas is 3x this per side


def _tile3_mirror(Image, ImageOps, square):
    """The square mirrored into a 3×3 reflective tiling (exact flips).

    A kaleidoscope fold samples the source at the output's radius
    in the folded direction; a plain square has no pixels at the
    corners' radius for most angles, so the fold is built on this
    tiled canvas and the finished mandala is centre-cropped back
    out of it."""
    s = square.size[0]
    big = Image.new("RGBA", (3 * s, 3 * s))
    mirrored = ImageOps.mirror(square)
    flipped = ImageOps.flip(square)
    both = ImageOps.flip(mirrored)
    tiles = ((both, flipped, both),
             (mirrored, square, mirrored),
             (both, flipped, both))
    for ty in range(3):
        for tx in range(3):
            big.paste(tiles[ty][tx], (tx * s, ty * s))
    return big


def _wedge_mask(Image, ImageDraw, size: int, a0: float, a1: float):
    """L mask of the triangular wedge between angles a0..a1
    (radians, image coords: from the +x axis toward +y), apex at
    the canvas centre, far edge pushed far outside the canvas."""
    c = (size - 1) / 2.0
    radius = 2 * size
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(c, c),
                  (c + radius * math.cos(a0), c + radius * math.sin(a0)),
                  (c + radius * math.cos(a1), c + radius * math.sin(a1))],
                 fill=255)
    return mask


def _own_sector(Image, ImageChops, ImageDraw, piece, size: int,
                a0_deg: float, a1_deg: float):
    """Clip a rotated wedge piece to exactly its own sector.

    The wedge masks are angularly fattened (and dilated) so a
    rotated piece never leaves a transparent seam — but that
    fattening also spills a couple of degrees into the NEIGHBOUR's
    sector, where this piece's fold is the wrong one, and plain
    composite order would let the spill win. Intersecting the
    piece's alpha with its exact sector hands every output angle
    to the piece whose fold is correct there; the fattened alpha
    still guarantees full coverage inside the sector."""
    sector = _wedge_mask(Image, ImageDraw, size,
                         math.radians(a0_deg), math.radians(a1_deg))
    piece.putalpha(ImageChops.multiply(piece.split()[3], sector))
    return piece


def _rotate_piece(Image, piece, degrees_cw: float, size: int):
    """Rotate a canvas piece CLOCKWISE (image-angle positive) by
    `degrees_cw` around the canvas centre, NEAREST sampling so
    colours stay exact copies of source pixels. PIL's rotate()
    turns counter-clockwise, hence the negation."""
    centre = ((size - 1) / 2.0, (size - 1) / 2.0)
    return piece.rotate(-degrees_cw, resample=Image.NEAREST,
                        expand=False, center=centre)


def _assemble_quadrants(Image, ImageOps, quad):
    """Complete a kaleidoscope from its bottom-right quadrant:
    the quadrant and its mirror/flip copies are pasted through
    hard quadrant rectangles, so every output pixel comes from
    exactly one copy and both axis mirrors are exact pixel
    permutations of the quadrant."""
    size = quad.size[0]
    mid = size // 2
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    regions = ((quad, (mid, mid, size, size)),
               (ImageOps.mirror(quad), (0, mid, mid, size)),
               (ImageOps.flip(quad), (mid, 0, size, mid)),
               (ImageOps.flip(ImageOps.mirror(quad)), (0, 0, mid, mid)))
    for copy_img, (x0, y0, x1, y1) in regions:
        canvas.paste(copy_img.crop((x0, y0, x1, y1)), (x0, y0))
    return canvas


def _assemble_halves(Image, ImageOps, right):
    """Complete a kaleidoscope from its right half-plane: the
    mirrored copy is pasted through the left-half rectangle, so
    the vertical mirror is an exact pixel permutation."""
    size = right.size[0]
    mid = size // 2
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(right.crop((mid, 0, size, size)), (mid, 0))
    left = ImageOps.mirror(right)
    canvas.paste(left.crop((0, 0, mid, size)), (0, 0))
    return canvas


def _kaleidoscope_quadrants(Image, ImageChops, ImageDraw, ImageFilter,
                            ImageOps, big, segments: int):
    """Build the mandala for segments divisible by 4; returns the
    finished big-canvas image (caller centre-crops)."""
    size = big.size[0]
    centre = (size - 1) / 2.0
    wedge_deg = 360.0 / segments
    if segments == 4:
        mask = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.rectangle([centre, centre, size, size], fill=255)
        quad = big.copy()
        quad.putalpha(mask)
        return _assemble_quadrants(Image, ImageOps, quad)
    base = big.copy()
    if segments == 8:
        # Wedges at 90° multiples of the group complete EXACTLY:
        # the second wedge is the first one's diagonal transpose.
        # No angular fattening — this path is pixel-exact.
        base.putalpha(_wedge_mask(Image, ImageDraw, size,
                                  0.0, math.radians(wedge_deg)))
        quad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        quad.alpha_composite(base)
        quad.alpha_composite(base.transpose(Image.TRANSPOSE))
        return _assemble_quadrants(Image, ImageOps, quad)
    # 12 / 16: rotated wedges. Their masks are angularly fattened
    # and dilated — rotated copies of a hard triangle otherwise
    # leave 1px transparent seams along the wedge boundaries.
    eps = math.radians(2.0)
    mask = _wedge_mask(Image, ImageDraw, size, -eps,
                       math.radians(wedge_deg) + eps)
    mask = mask.filter(ImageFilter.MaxFilter(3))
    base.putalpha(mask)
    quad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    per_quadrant = segments // 4
    for k in range(per_quadrant):
        if k % 2 == 0:
            piece = _rotate_piece(Image, base, k * wedge_deg, size)
        else:
            piece = _rotate_piece(Image, ImageOps.flip(base),
                                  (k + 1) * wedge_deg, size)
        piece = _own_sector(Image, ImageChops, ImageDraw, piece, size,
                            k * wedge_deg, (k + 1) * wedge_deg)
        quad.alpha_composite(piece)
    return _assemble_quadrants(Image, ImageOps, quad)


def _kaleidoscope_halves(Image, ImageChops, ImageDraw, ImageFilter,
                         ImageOps, big, segments: int):
    """Build the mandala for segments ≡ 2 (mod 4) — 6, 10, 14 —
    whose wedge grid doesn't align with the canvas quadrants:
    pieces centred on the +x axis are rotated around the right
    half-plane, which is then mirrored exactly."""
    size = big.size[0]
    wedge_deg = 360.0 / segments
    wedge = math.radians(wedge_deg)
    eps = math.radians(2.0)
    mask = _wedge_mask(Image, ImageDraw, size,
                       -wedge / 2 - eps, wedge / 2 + eps)
    mask = mask.filter(ImageFilter.MaxFilter(3))
    base = big.copy()
    base.putalpha(mask)
    right = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    reach = segments // 4 + 1
    for k in range(-reach, reach + 1):
        if k % 2 == 0:
            piece = _rotate_piece(Image, base, k * wedge_deg, size)
        else:
            piece = _rotate_piece(Image, ImageOps.flip(base),
                                  k * wedge_deg, size)
        piece = _own_sector(Image, ImageChops, ImageDraw, piece, size,
                            k * wedge_deg - wedge_deg / 2,
                            k * wedge_deg + wedge_deg / 2)
        right.alpha_composite(piece)
    return _assemble_halves(Image, ImageOps, right)


def kaleidoscope_image(path: str, segments: int = 8,
                       output: str = "") -> str:
    """Turn one picture into a kaleidoscope mandala, as a NEW file.

    The centre square of the picture (side = its smaller
    dimension) is folded into `segments` mirror-alternating
    wedges around the centre. `segments` must be EVEN and
    between 4 and 16: the wedges alternate mirrored/unmirrored
    around the full turn, and an odd count cannot mirror back
    into itself. Very large pictures are scaled to a 1024-pixel
    square first (the fold works on a 3× canvas) and the result
    says so. The output is square PNG by default:
    '<name>_kaleidoscope.png' beside the input. The input is
    never overwritten, and a taken output name gets a
    ' (2)'-style suffix.
    """
    count = _as_int(segments, "segments")
    if isinstance(count, str):
        return count
    if count % 2 != 0:
        return (f"segments must be even (got {count}) — the wedges "
                "mirror alternately around the centre, and an odd "
                "count can't mirror back into itself after a full "
                "turn. Try 6, 8 or 12.")
    if not 4 <= count <= 16:
        return (f"segments must be between 4 and 16 (got {count}).")
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_size = image.size
    Image, err = _load_pil()
    if err:
        return err
    from PIL import ImageChops, ImageDraw, ImageFilter, ImageOps

    notes: list[str] = []
    width, height = image.size
    side = min(width, height)
    if side < 16:
        return (f"That picture is only {width}×{height} — too small "
                "to fold into a kaleidoscope (the centre square "
                "needs at least 16×16 pixels).")
    square = image.crop(((width - side) // 2, (height - side) // 2,
                         (width - side) // 2 + side,
                         (height - side) // 2 + side)).convert("RGBA")
    if (width, height) != (side, side):
        notes.append(f"The mandala is built from the centre "
                     f"{side}×{side} square of the picture.")
    if side > _KALEIDO_MAX_SIDE:
        square = square.resize((_KALEIDO_MAX_SIDE, _KALEIDO_MAX_SIDE),
                               Image.LANCZOS)
        notes.append(f"The picture was scaled to "
                     f"{_KALEIDO_MAX_SIDE}×{_KALEIDO_MAX_SIDE} for "
                     "the effect (the fold works on a triple-size "
                     "canvas).")
        side = _KALEIDO_MAX_SIDE
    big = _tile3_mirror(Image, ImageOps, square)
    if count % 4 == 0:
        folded = _kaleidoscope_quadrants(Image, ImageChops, ImageDraw,
                                         ImageFilter, ImageOps, big,
                                         count)
    else:
        folded = _kaleidoscope_halves(Image, ImageChops, ImageDraw,
                                      ImageFilter, ImageOps, big,
                                      count)
    result = folded.crop((side, side, 2 * side, 2 * side))
    if not _has_alpha(image):
        result = result.convert("RGB")
    notes.append(f"{count} mirror segments.")
    dest = _resolve_output(target, output,
                           f"{target.stem}_kaleidoscope.png")
    final, save_notes = _save_result(Image, result, dest, "PNG")
    if final is None:
        return save_notes
    return _result_line("Kaleidoscoped", target, old_size, result.size,
                        final, notes + save_notes)


# ---------------------------------------------------------------------------
# DUOTONE IMAGE
# ---------------------------------------------------------------------------


def _duotone_lut(shadow_rgb, highlight_rgb):
    """The 256×3 duotone lookup table — the pure core.

    Returns (lut_r, lut_g, lut_b), each a list of 256 ints:
    index i maps linearly from the shadow colour (i=0) to the
    highlight colour (i=255), per channel, rounded to nearest.
    """
    table = []
    for channel in range(3):
        lo = shadow_rgb[channel]
        hi = highlight_rgb[channel]
        table.append([int(lo + (hi - lo) * i / 255 + 0.5)
                      for i in range(256)])
    return tuple(table)


def duotone_image(path: str, shadow: str = "#1a1a40",
                  highlight: str = "#f5d76e", output: str = "") -> str:
    """Remap one picture to a two-colour duotone, as a NEW file.

    The picture is first reduced to grayscale, then every tone
    is mapped through a linear ramp between the `shadow` colour
    (for black) and the `highlight` colour (for white) — the
    classic two-ink print look. Colours are '#RRGGBB' hex or a
    named colour (black, white, red, blue, green, yellow, pink,
    purple, orange, gray). Transparency is preserved untouched.
    The default output is '<name>_duotone.<ext>' beside the
    input; a taken name gets a ' (2)'-style suffix. The input
    is never overwritten.
    """
    shadow_rgb = _parse_color(shadow)
    if isinstance(shadow_rgb, str):
        return f"For the shadow colour — {shadow_rgb}"
    highlight_rgb = _parse_color(highlight)
    if isinstance(highlight_rgb, str):
        return f"For the highlight colour — {highlight_rgb}"
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_size = image.size
    Image, err = _load_pil()
    if err:
        return err
    from PIL import ImageOps

    rgb, alpha = _split_alpha(Image, image)
    gray = ImageOps.grayscale(rgb)
    lut_r, lut_g, lut_b = _duotone_lut(shadow_rgb, highlight_rgb)
    result = Image.merge("RGB", (gray.point(lut_r), gray.point(lut_g),
                                 gray.point(lut_b)))
    result = _join_alpha(Image, result, alpha)
    notes = [f"Duotone from #{shadow_rgb[0]:02x}{shadow_rgb[1]:02x}"
             f"{shadow_rgb[2]:02x} (shadows) to "
             f"#{highlight_rgb[0]:02x}{highlight_rgb[1]:02x}"
             f"{highlight_rgb[2]:02x} (highlights)."]
    if alpha is not None:
        notes.append("Transparency was preserved untouched.")
    dest = _resolve_output(target, output,
                           f"{target.stem}_duotone{target.suffix}")
    final, save_notes = _save_result(Image, result, dest, image.format)
    if final is None:
        return save_notes
    return _result_line("Duotoned", target, old_size, result.size,
                        final, notes + save_notes)


# ---------------------------------------------------------------------------
# PIXELATE IMAGE
# ---------------------------------------------------------------------------


def pixelate_image(path: str, pixel_size: int = 12,
                   output: str = "") -> str:
    """Pixelate one picture into retro pixel art, as a NEW file.

    The picture is shrunk to one pixel per `pixel_size`×
    `pixel_size` block (box average) and scaled straight back
    up with nearest-neighbour, so the dimensions are UNCHANGED
    and every block is one flat colour. `pixel_size` runs
    2–100 and must be less than half the picture's smaller
    side — beyond that there would be almost no blocks left.
    The default output is '<name>_pixelated.<ext>' beside the
    input; a taken name gets a ' (2)'-style suffix. The input
    is never overwritten.
    """
    block = _as_int(pixel_size, "pixel_size")
    if isinstance(block, str):
        return block
    if not 2 <= block <= 100:
        return (f"pixel_size must be between 2 and 100 (got {block}).")
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_size = image.size
    width, height = image.size
    if block * 2 >= min(width, height):
        return (f"pixel_size {block} is too big for a {width}×{height} "
                f"picture — it must be less than half the smaller "
                f"side ({min(width, height)}), or there would be "
                "almost no blocks left. Nothing was written.")
    Image, err = _load_pil()
    if err:
        return err
    work = _rgb_ready(image)
    small = work.resize((max(1, width // block), max(1, height // block)),
                        Image.BOX)
    result = small.resize((width, height), Image.NEAREST)
    notes = [f"Pixelated into {block}×{block} blocks "
             f"({width // block}×{height // block} of them)."]
    dest = _resolve_output(target, output,
                           f"{target.stem}_pixelated{target.suffix}")
    final, save_notes = _save_result(Image, result, dest, image.format)
    if final is None:
        return save_notes
    return _result_line("Pixelated", target, old_size, result.size,
                        final, notes + save_notes)


# ---------------------------------------------------------------------------
# GLITCH IMAGE
# ---------------------------------------------------------------------------


def _offset_channels(Image, rgb, shift: int):
    """Split the RGB channels and offset R by (+shift, 0) and B
    by (−shift, 0), WRAPPING at the edges — the pure core of
    the glitch look (ImageChops.offset wraps by design)."""
    from PIL import ImageChops

    red, green, blue = rgb.split()
    if shift:
        red = ImageChops.offset(red, shift, 0)
        blue = ImageChops.offset(blue, -shift, 0)
    return Image.merge("RGB", (red, green, blue))


def _slice_shifts(rng: random.Random, width: int, height: int,
                  shift: int):
    """The seeded slice plan: 5–9 horizontal bands, each
    (y, band_height, dx). Factored out so the plan is exactly
    reproducible from the seed."""
    bands = []
    count = rng.randint(5, 9)
    max_dx = max(4, 2 * shift)
    for _ in range(count):
        y = rng.randint(0, height - 1)
        band_h = rng.randint(1, max(1, height // 8))
        dx = rng.randint(-max_dx, max_dx)
        bands.append((y, band_h, dx))
    return bands


def _apply_slices(Image, rgb, bands):
    """Displace horizontal bands sideways (wrapping)."""
    from PIL import ImageChops

    width, height = rgb.size
    result = rgb
    for y, band_h, dx in bands:
        if dx == 0:
            continue
        band = result.crop((0, y, width, min(height, y + band_h)))
        result.paste(ImageChops.offset(band, dx, 0), (0, y))
    return result


def glitch_image(path: str, shift: int = 12, seed: int = 0,
                 output: str = "") -> str:
    """Give one picture an RGB-split glitch-art look, as a NEW file.

    The red channel is shifted `shift` pixels right and the blue
    channel `shift` pixels left — both WRAP around the edges —
    and 5–9 seeded horizontal slices are displaced sideways on
    top of that. The whole effect is deterministic per `seed`:
    the same seed always produces the same file, so a look can
    be recreated or iterated. seed 0 picks a seed at random and
    the result reports it. Transparency is left completely
    untouched — the glitch runs on the colour channels and the
    original alpha is reattached. The default output is
    '<name>_glitch.<ext>' beside the input; a taken name gets a
    ' (2)'-style suffix. The input is never overwritten.
    """
    offset = _as_int(shift, "shift")
    if isinstance(offset, str):
        return offset
    if not 0 <= offset <= 200:
        return (f"shift must be between 0 and 200 pixels (got "
                f"{offset}).")
    seed_value = _as_int(seed, "seed")
    if isinstance(seed_value, str):
        return seed_value
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_size = image.size
    Image, err = _load_pil()
    if err:
        return err
    width, height = image.size
    if seed_value == 0:
        seed_value = random.SystemRandom().randrange(1, 1_000_000)
        seed_note = (f"Seed {seed_value} was chosen at random — "
                     "ask for the same seed to recreate this exact "
                     "glitch.")
    else:
        seed_note = f"Seed {seed_value}."
    rgb, alpha = _split_alpha(Image, image)
    result = _offset_channels(Image, rgb, offset)
    rng = random.Random(seed_value)
    bands = _slice_shifts(rng, width, height, offset)
    result = _apply_slices(Image, result, bands)
    result = _join_alpha(Image, result, alpha)
    notes = [f"Red channel shifted +{offset}px, blue −{offset}px "
             f"(both wrap at the edges), plus {len(bands)} "
             "displaced horizontal slices.", seed_note]
    if alpha is not None:
        notes.append("Transparency was reattached untouched.")
    if offset == 0:
        notes.append("shift was 0, so only the slices displace "
                     "the picture.")
    dest = _resolve_output(target, output,
                           f"{target.stem}_glitch{target.suffix}")
    final, save_notes = _save_result(Image, result, dest, image.format)
    if final is None:
        return save_notes
    return _result_line("Glitched", target, old_size, result.size,
                        final, notes + save_notes)

# Photo EDIT tools: crop, rotate, adjust, filter.
# Split out of core/skills_photo.py (skills split Phase A batch A17).

# SKILLS SPLIT PHASE A BATCH A17 INTENTIONAL: cross-part imports —
# these names now live in skills_photo_helpers; the tools resolve
# them in THIS module's globals (plan SS2 P3), not the facade's.
from .skills_photo_helpers import (
    _as_float,
    _as_int,
    _has_alpha,
    _load_oriented,
    _load_pil,
    _resolve_output,
    _result_line,
    _rgb_ready,
    _save_result,
)

# ---------------------------------------------------------------------------
# CROP IMAGE
# ---------------------------------------------------------------------------

def crop_image(path: str, left: int, top: int, right: int, bottom: int,
               output: str = "") -> str:
    """Crop one image to a pixel box, saving the result as a NEW file.

    Coordinates are pixels in the ORIENTED (displayed) picture,
    (left, top) the top-left corner and (right, bottom) the
    bottom-right corner (exclusive, Pillow-style). A box that
    reaches partly outside the picture is CLAMPED to the edges and
    the result says so; a box that is empty, inverted or lies
    entirely outside is refused. The input is never overwritten:
    the default output is '<name>_cropped.<ext>' beside it, and a
    taken output name gets a ' (2)'-style suffix.
    """
    coords = []
    for name, value in (("left", left), ("top", top),
                        ("right", right), ("bottom", bottom)):
        coerced = _as_int(value, name)
        if isinstance(coerced, str):
            return coerced
        coords.append(coerced)
    left, top, right, bottom = coords
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_w, old_h = image.size
    if right <= left or bottom <= top:
        return (f"That crop box is empty or inverted — left={left}, "
                f"top={top}, right={right}, bottom={bottom}. The right "
                "edge must be past the left, and the bottom past the top.")
    cl, ct = max(0, left), max(0, top)
    cr, cb = min(old_w, right), min(old_h, bottom)
    if cr <= cl or cb <= ct:
        return (f"That crop box (left={left}, top={top}, right={right}, "
                f"bottom={bottom}) lies entirely outside the picture, "
                f"which is {old_w}×{old_h} — nothing was written.")
    notes: list[str] = []
    if (cl, ct, cr, cb) != (left, top, right, bottom):
        notes.append(f"The box reached outside the {old_w}×{old_h} picture, "
                     f"so it was clamped to ({cl}, {ct})–({cr}, {cb}).")
    cropped = image.crop((cl, ct, cr, cb))
    Image, err = _load_pil()
    if err:
        return err
    dest = _resolve_output(target, output,
                           f"{target.stem}_cropped{target.suffix}")
    final, save_notes = _save_result(Image, cropped, dest, image.format)
    if final is None:
        return save_notes
    return _result_line("Cropped", target, (old_w, old_h), cropped.size,
                        final, notes + save_notes)


# ---------------------------------------------------------------------------
# ROTATE IMAGE (+ optional flip)
# ---------------------------------------------------------------------------

def rotate_image(path: str, degrees: float = 90, flip: str = "",
                 output: str = "") -> str:
    """Rotate one image, saving the result as a NEW file.

    Positive degrees turn the picture CLOCKWISE. A multiple of
    90° uses a lossless transpose — no pixel is resampled. Any
    other angle is resampled (bicubic) onto a canvas expanded to
    fit the turned picture, and the result says so. `flip` may be
    "horizontal" or "vertical" and is applied AFTER the rotation.
    The input is never overwritten: the default output is
    '<name>_rotated.<ext>' beside it, and a taken output name
    gets a ' (2)'-style suffix.
    """
    deg = _as_float(degrees, "degrees")
    if isinstance(deg, str):
        return deg
    flip = str(flip or "").strip().lower()
    if flip == "none":
        flip = ""
    if flip not in ("", "horizontal", "vertical"):
        return ("flip must be 'horizontal', 'vertical' or 'none' "
                f"(got '{flip}').")
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_size = image.size
    deg_cw = deg % 360.0
    if deg_cw == 0.0 and not flip:
        return (f"A rotation of {deg:g}° is a full turn — the picture "
                "would come out identical, so nothing was written. "
                "Give an angle that isn't a multiple of 360, or a flip.")
    Image, err = _load_pil()
    if err:
        return err
    from PIL import ImageOps

    notes: list[str] = []
    result = image
    if deg_cw == 0.0:
        notes.append(f"The {deg:g}° rotation is a full turn, so only "
                     "the flip was applied.")
    elif deg_cw % 90 == 0:
        # Clockwise-positive: 90° CW is Pillow's ROTATE_270 (CCW maths).
        transpose_for = {90: Image.Transpose.ROTATE_270,
                         180: Image.Transpose.ROTATE_180,
                         270: Image.Transpose.ROTATE_90}
        result = result.transpose(transpose_for[int(deg_cw)])
        notes.append(f"Rotated {deg_cw:g}° clockwise with a lossless "
                     "transpose — no pixel was resampled.")
    else:
        fill = (0, 0, 0, 0) if _has_alpha(result) else 0
        result = result.rotate(-deg_cw, resample=Image.BICUBIC,
                               expand=True, fillcolor=fill)
        notes.append(f"Rotated {deg_cw:g}° clockwise — not a multiple "
                     "of 90°, so the picture was resampled (bicubic) "
                     "onto a canvas expanded to fit it.")
    if flip == "horizontal":
        result = ImageOps.mirror(result)
        notes.append("Flipped horizontally (mirror image).")
    elif flip == "vertical":
        result = ImageOps.flip(result)
        notes.append("Flipped vertically (upside down).")
    dest = _resolve_output(target, output,
                           f"{target.stem}_rotated{target.suffix}")
    final, save_notes = _save_result(Image, result, dest, image.format)
    if final is None:
        return save_notes
    return _result_line("Rotated", target, old_size, result.size,
                        final, notes + save_notes)


# ---------------------------------------------------------------------------
# ADJUST IMAGE
# ---------------------------------------------------------------------------

_ADJUST_FACTORS = ("brightness", "contrast", "color", "sharpness")


def adjust_image(path: str, brightness: float = 1.0, contrast: float = 1.0,
                 color: float = 1.0, sharpness: float = 1.0,
                 output: str = "") -> str:
    """Adjust brightness, contrast, colour and sharpness of one image.

    Each factor is 1.0 for "unchanged", 0.0–3.0 allowed (0.5 is
    half as bright, 2.0 twice the contrast, colour 0.0 drains the
    picture to grey tones). If ALL four factors are 1.0 there is
    nothing to do and the call is refused. Only the factors that
    actually change are applied — and named in the result. The
    input is never overwritten: the default output is
    '<name>_adjusted.<ext>' beside it, and a taken output name
    gets a ' (2)'-style suffix.
    """
    given = {"brightness": brightness, "contrast": contrast,
             "color": color, "sharpness": sharpness}
    factors: dict[str, float] = {}
    for name in _ADJUST_FACTORS:
        value = _as_float(given[name], name)
        if isinstance(value, str):
            return value
        if not 0.0 <= value <= 3.0:
            return (f"The {name} factor must be between 0.0 and 3.0 "
                    f"(1.0 = unchanged); got {value:g}.")
        factors[name] = value
    changed = {name: value for name, value in factors.items()
               if abs(value - 1.0) > 1e-9}
    if not changed:
        return ("Nothing to adjust — all four factors are 1.0 "
                "(unchanged), so no file was written. Set at least "
                "one factor away from 1.0.")
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_size = image.size
    Image, err = _load_pil()
    if err:
        return err
    from PIL import ImageEnhance

    enhancers = {"brightness": ImageEnhance.Brightness,
                 "contrast": ImageEnhance.Contrast,
                 "color": ImageEnhance.Color,
                 "sharpness": ImageEnhance.Sharpness}
    result = _rgb_ready(image)
    for name in _ADJUST_FACTORS:  # fixed, predictable order
        if name in changed:
            result = enhancers[name](result).enhance(changed[name])
    notes = ["Adjusted: " + ", ".join(
        f"{name} ×{changed[name]:g}" for name in _ADJUST_FACTORS
        if name in changed) + "."]
    dest = _resolve_output(target, output,
                           f"{target.stem}_adjusted{target.suffix}")
    final, save_notes = _save_result(Image, result, dest, image.format)
    if final is None:
        return save_notes
    return _result_line("Adjusted", target, old_size, result.size,
                        final, notes + save_notes)


# ---------------------------------------------------------------------------
# FILTER IMAGE
# ---------------------------------------------------------------------------

_FILTERS = ("grayscale", "sepia", "invert", "blur", "sharpen",
            "edges", "emboss", "posterize", "solarize")


_SEPIA_MATRIX = ((0.393, 0.769, 0.189),
                 (0.349, 0.686, 0.168),
                 (0.272, 0.534, 0.131))


def _sepia_pixel(r: int, g: int, b: int) -> tuple[int, int, int]:
    """The sepia colour matrix on ONE pixel — the pure core.

    Each term is rounded individually before summing so that the
    LUT-based whole-image path (_sepia_image) and this function
    agree EXACTLY on every pixel; sums clamp at 255.
    """
    out = []
    for row in _SEPIA_MATRIX:
        total = (round(row[0] * r) + round(row[1] * g)
                 + round(row[2] * b))
        out.append(255 if total > 255 else total)
    return out[0], out[1], out[2]


def _sepia_image(Image, image):
    """Apply _sepia_pixel's matrix to a whole image via LUTs."""
    from PIL import ImageChops

    alpha = None
    if _has_alpha(image):
        alpha = image.convert("RGBA").split()[3]
    rgb = image.convert("RGB")
    bands = rgb.split()
    out_bands = []
    for row in _SEPIA_MATRIX:
        acc = None
        for coef, band in zip(row, bands):
            lut = [min(255, round(coef * v)) for v in range(256)]
            term = band.point(lut)
            acc = term if acc is None else ImageChops.add(acc, term)
        out_bands.append(acc)
    sepia = Image.merge("RGB", out_bands)
    if alpha is not None:
        sepia = sepia.convert("RGBA")
        sepia.putalpha(alpha)
    return sepia


def filter_image(path: str, filter: str, output: str = "",
                 radius: float = 0) -> str:
    """Apply a named photo filter to one image, as a NEW file.

    (The parameter is named `filter` because that's the tool's
    schema; it shadows the builtin inside this function only.)

    grayscale, sepia, invert, blur, sharpen, edges, emboss,
    posterize or solarize. `radius` tunes the blur filter only
    (0.5–20, default 2); setting it for another filter is refused
    rather than silently ignored. The input is never overwritten:
    the default output is '<name>_<filter>.<ext>' beside it, and a
    taken output name gets a ' (2)'-style suffix.
    """
    kind = str(filter or "").strip().lower()
    if kind not in _FILTERS:
        return ("That filter isn't available — pick one of: "
                + ", ".join(_FILTERS) + ".")
    blur_radius = 2.0
    if radius:
        radius_value = _as_float(radius, "radius")
        if isinstance(radius_value, str):
            return radius_value
        if kind != "blur":
            return (f"radius only changes the blur filter — the "
                    f"'{kind}' filter has no radius to set. Leave "
                    "radius unset for it.")
        if not 0.5 <= radius_value <= 20:
            return (f"The blur radius must be between 0.5 and 20 "
                    f"(got {radius_value:g}); 2 is the default.")
        blur_radius = radius_value
    image, target, err = _load_oriented(path)
    if err:
        return err
    old_size = image.size
    Image, err = _load_pil()
    if err:
        return err
    from PIL import ImageFilter, ImageOps

    work = _rgb_ready(image)
    if kind == "grayscale":
        result = ImageOps.grayscale(work)
        note = "Converted to grayscale (luminance)."
    elif kind == "sepia":
        result = _sepia_image(Image, work)
        note = "Warm sepia tone applied."
    elif kind == "invert":
        if _has_alpha(work):
            rgba = work.convert("RGBA")
            r, g, b, a = rgba.split()
            inverted = Image.merge(
                "RGB", (ImageOps.invert(r), ImageOps.invert(g),
                        ImageOps.invert(b)))
            result = inverted.convert("RGBA")
            result.putalpha(a)
        else:
            result = ImageOps.invert(work.convert("RGB"))
        note = "Colours inverted (photo negative)."
    elif kind == "blur":
        result = work.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        note = f"Soft Gaussian blur applied (radius {blur_radius:g})."
    elif kind == "sharpen":
        result = work.filter(ImageFilter.SHARPEN)
        note = "Sharpening kernel applied."
    elif kind == "edges":
        result = work.convert("RGB").filter(ImageFilter.FIND_EDGES)
        note = "Edge detection — only the outlines remain."
    elif kind == "emboss":
        result = work.convert("RGB").filter(ImageFilter.EMBOSS)
        note = "Embossed relief effect applied."
    elif kind == "posterize":
        result = ImageOps.posterize(work.convert("RGB"), bits=3)
        note = "Posterized to 3 bits per channel — flat colour bands."
    else:  # solarize
        result = ImageOps.solarize(work.convert("RGB"), threshold=128)
        note = "Solarized — tones above the midpoint are inverted."
    dest = _resolve_output(target, output,
                           f"{target.stem}_{kind}{target.suffix}")
    final, save_notes = _save_result(Image, result, dest, image.format)
    if final is None:
        return save_notes
    return _result_line("Filtered", target, old_size, result.size,
                        final, [note] + save_notes)

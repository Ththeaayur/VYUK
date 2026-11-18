"""Creative skills impl - visual tools (ASCII art, palettes, generative pictures) + the shared file-writing helpers. Split from core/skills_creative.py; code moved verbatim."""

from pathlib import Path
import colorsys
import math
import random
import re

# ---------------------------------------------------------------------------
# ASCII ART — a 5-row block font, drawn as data (44 glyphs)
# ---------------------------------------------------------------------------

_ASCII_FONT = {
    "A": (" ███ ", "█   █", "█████", "█   █", "█   █"),
    "B": ("████ ", "█   █", "████ ", "█   █", "████ "),
    "C": (" ████", "█    ", "█    ", "█    ", " ████"),
    "D": ("████ ", "█   █", "█   █", "█   █", "████ "),
    "E": ("█████", "█    ", "████ ", "█    ", "█████"),
    "F": ("█████", "█    ", "████ ", "█    ", "█    "),
    "G": (" ████", "█    ", "█  ██", "█   █", " ███ "),
    "H": ("█   █", "█   █", "█████", "█   █", "█   █"),
    "I": ("█████", "  █  ", "  █  ", "  █  ", "█████"),
    "J": ("█████", "    █", "    █", "█   █", " ███ "),
    "K": ("█   █", "█  █ ", "███  ", "█  █ ", "█   █"),
    "L": ("█    ", "█    ", "█    ", "█    ", "█████"),
    "M": ("█   █", "██ ██", "█ █ █", "█   █", "█   █"),
    "N": ("█   █", "██  █", "█ █ █", "█  ██", "█   █"),
    "O": (" ███ ", "█   █", "█   █", "█   █", " ███ "),
    "P": ("████ ", "█   █", "████ ", "█    ", "█    "),
    "Q": (" ███ ", "█   █", "█   █", "█  █ ", " ██ █"),
    "R": ("████ ", "█   █", "████ ", "█  █ ", "█   █"),
    "S": (" ████", "█    ", " ███ ", "    █", "████ "),
    "T": ("█████", "  █  ", "  █  ", "  █  ", "  █  "),
    "U": ("█   █", "█   █", "█   █", "█   █", " ███ "),
    "V": ("█   █", "█   █", "█   █", " █ █ ", "  █  "),
    "W": ("█   █", "█   █", "█ █ █", "██ ██", "█   █"),
    "X": ("█   █", " █ █ ", "  █  ", " █ █ ", "█   █"),
    "Y": ("█   █", "█   █", " █ █ ", "  █  ", "  █  "),
    "Z": ("█████", "   █ ", "  █  ", " █   ", "█████"),
    "0": (" ███ ", "█  ██", "█ █ █", "██  █", " ███ "),
    "1": ("  █  ", " ██  ", "  █  ", "  █  ", " ███ "),
    "2": (" ███ ", "█   █", "   █ ", "  █  ", "█████"),
    "3": ("████ ", "    █", " ███ ", "    █", "████ "),
    "4": ("█   █", "█   █", "█████", "    █", "    █"),
    "5": ("█████", "█    ", "████ ", "    █", "████ "),
    "6": (" ███ ", "█    ", "████ ", "█   █", " ███ "),
    "7": ("█████", "    █", "   █ ", "  █  ", "  █  "),
    "8": (" ███ ", "█   █", " ███ ", "█   █", " ███ "),
    "9": (" ███ ", "█   █", " ████", "    █", " ███ "),
    " ": ("     ", "     ", "     ", "     ", "     "),
    ".": ("     ", "     ", "     ", "     ", " ██  "),
    ",": ("     ", "     ", "     ", " ██  ", " █   "),
    "!": (" ██  ", " ██  ", " ██  ", "     ", " ██  "),
    "?": (" ███ ", "█   █", "   █ ", "     ", "  █  "),
    "'": (" ██  ", " ██  ", "     ", "     ", "     "),
    "-": ("     ", "     ", "█████", "     ", "     "),
    ":": ("     ", " ██  ", "     ", " ██  ", "     "),
}

_ASCII_MAX_CHARS = 24
_ASCII_SUPPORTED = ("letters A–Z, digits 0–9, spaces and the "
                    "punctuation . , ! ? ' - :")


def _render_ascii(folded: str) -> str:
    """Join the 5 glyph rows for already-validated, folded text."""
    return "\n".join(
        " ".join(_ASCII_FONT[ch][row] for ch in folded)
        for row in range(5))


def ascii_art(text: str) -> str:
    """Render text in the built-in 5-row block font."""
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    if not text.strip():
        return ("Give me a word or short phrase and I'll render it "
                "in block letters.")
    if len(text) > _ASCII_MAX_CHARS:
        return (f"That's {len(text)} characters — block letters get "
                f"wide fast, so the cap is {_ASCII_MAX_CHARS}. "
                "Shorten it and I'll draw that.")
    folded = text.upper()
    bad = sorted({ch for ch in folded if ch not in _ASCII_FONT})
    if bad:
        shown = ", ".join(repr(ch) for ch in bad)
        return (f"I can't draw {shown} — the block font only has "
                f"{_ASCII_SUPPORTED}. Swap those characters and "
                "I'll render it.")
    return _render_ascii(folded)


# ---------------------------------------------------------------------------
# PALETTES — HSV harmony maths (colorsys), no images, just hex + roles
# ---------------------------------------------------------------------------

_PALETTE_STYLES = ("complementary", "analogous", "triadic", "monochrome")
_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def _hsv_hex(h: float, s: float, v: float) -> str:
    """One HSV colour (h/s/v in 0..1) as an #RRGGBB string."""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, min(1.0, max(0.0, s)),
                                  min(1.0, max(0.0, v)))
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255),
                                       round(b * 255))


def _harmonies(h: float, s: float, v: float) -> dict:
    """{style: [(role, hex), ...]} — five colours per harmony, all
    derived from the base (h, s, v). Hue offsets are the textbook
    ones: complement +1/2, triadic ±1/3, analogous ±1/12."""
    light_v = min(1.0, v + 0.22)
    deep_v = max(0.18, v - 0.32)
    soft_s = s * 0.55
    return {
        "complementary": [
            ("Base", _hsv_hex(h, s, v)),
            ("Complement", _hsv_hex(h + 0.5, s, v)),
            ("Base, light", _hsv_hex(h, soft_s, light_v)),
            ("Complement, light", _hsv_hex(h + 0.5, soft_s, light_v)),
            ("Deep shade", _hsv_hex(h, s * 0.8, deep_v)),
        ],
        "analogous": [
            ("Cool neighbour", _hsv_hex(h - 1 / 12, s, v)),
            ("Base", _hsv_hex(h, s, v)),
            ("Warm neighbour", _hsv_hex(h + 1 / 12, s, v)),
            ("Far neighbour", _hsv_hex(h + 2 / 12, s * 0.85, v)),
            ("Deep shade", _hsv_hex(h, s * 0.8, deep_v)),
        ],
        "triadic": [
            ("Base", _hsv_hex(h, s, v)),
            ("Triad 1", _hsv_hex(h + 1 / 3, s, v)),
            ("Triad 2", _hsv_hex(h + 2 / 3, s, v)),
            ("Base, light", _hsv_hex(h, soft_s, light_v)),
            ("Triad 1, deep", _hsv_hex(h + 1 / 3, s, deep_v)),
        ],
        "monochrome": [
            ("Darkest", _hsv_hex(h, s, 0.20)),
            ("Dark", _hsv_hex(h, s, 0.35)),
            ("Mid", _hsv_hex(h, s, 0.50)),
            ("Bright", _hsv_hex(h, s, 0.70)),
            ("Brightest", _hsv_hex(h, s, 0.90)),
        ],
    }


def generate_palette(style: str = "random", base: str = "") -> str:
    """Five-colour harmony from a base colour (or a random hue)."""
    style = (style or "random").strip().lower()
    if style not in _PALETTE_STYLES + ("random",):
        return (f"I don't know a '{style}' palette — styles are "
                "complementary, analogous, triadic, monochrome "
                "or random.")
    base = (base or "").strip()
    if base:
        m = _HEX_RE.match(base)
        if not m:
            return (f"'{base}' isn't a colour I can read — give "
                    "the base as #RRGGBB, e.g. #3B82F6, or leave "
                    "it empty and I'll pick a hue.")
        digits = m.group(1).upper()
        r, g, b = (int(digits[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        base_hex = "#" + digits
    else:
        h = random.random()
        s = 0.55 + random.random() * 0.40
        v = 0.70 + random.random() * 0.30
        base_hex = _hsv_hex(h, s, v)
    harmonies = _harmonies(h, s, v)
    if style == "random":
        chosen = random.choice(_PALETTE_STYLES)
        head = (f"Palette — {chosen} harmony (picked at random), "
                f"built on {base_hex}:")
    else:
        chosen = style
        head = f"Palette — {chosen} harmony, built on {base_hex}:"
    lines = [head]
    lines += [f"{hexv} — {role}" for role, hexv in harmonies[chosen]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared helpers for the two file-writing tools
# ---------------------------------------------------------------------------


def _non_clobbering(dest: Path) -> Path:
    """Return `dest`, or 'stem (2).suffix', 'stem (3).suffix', … —
    the house rule: a created file never silently overwrites."""
    if not dest.exists():
        return dest
    counter = 2
    while True:
        candidate = dest.with_name(f"{dest.stem} ({counter}){dest.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _suffix_note(dest: Path, final: Path) -> str:
    """The disclosure sentence when a taken name was suffixed."""
    if final == dest:
        return ""
    return (f" '{dest.name}' already existed, so the suffixed name "
            f"'{final.name}' was used instead.")


def _pick_seed(seed) -> tuple:
    """(seed_int, problem) — 0 means 'choose one for me'."""
    try:
        value = int(seed)
    except (TypeError, ValueError):
        return None, f"'{seed}' isn't a whole-number seed."
    if value < 0:
        return None, ("Seeds are 0 (I'll pick one and tell you) or "
                      "a positive whole number.")
    if value == 0:
        value = random.SystemRandom().randint(1, 999_999)
    return value, None


# ---------------------------------------------------------------------------
# CREATE ART — seeded generative PNGs from PIL primitives
# ---------------------------------------------------------------------------

_ART_STYLES = ("circles", "lines", "mosaic", "gradient",
               "phyllotaxis", "starfield")
_ART_MIN_SIZE = 256
_ART_MAX_SIZE = 2048


def _load_pil():
    """Lazy Pillow import, the media packs' pattern: (Image,
    ImageDraw, None) or (None, None, honest install line)."""
    try:
        from PIL import Image, ImageDraw
        return Image, ImageDraw, None
    except ImportError:
        return None, None, (
            "The drawing part didn't make it into this build of "
            "VYUK, so I can't draw pictures.")


def _hex_rgb(hexv: str) -> tuple:
    return tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5))


def _seeded_palette(rng: random.Random) -> tuple:
    """(harmony_style, [hex, ...]) drawn from `rng` — called FIRST
    in create_art, so the same seed always yields the same palette
    whichever picture style is drawn with it."""
    style = rng.choice(_PALETTE_STYLES)
    h = rng.random()
    s = 0.55 + rng.random() * 0.35
    v = 0.78 + rng.random() * 0.22
    entries = _harmonies(h, s, v)[style]
    return style, [hexv for _role, hexv in entries]


def _darken(rgb: tuple, factor: float) -> tuple:
    return tuple(max(0, min(255, round(c * factor))) for c in rgb)


def _draw_circles(Image, ImageDraw, size, rng, rgbs):
    img = Image.new("RGB", (size, size), _darken(rgbs[0], 0.30))
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(rng.randint(36, 80)):
        cx = rng.uniform(-0.10, 1.10) * size
        cy = rng.uniform(-0.10, 1.10) * size
        r = rng.uniform(0.03, 0.24) * size
        colour = rng.choice(rgbs) + (rng.randint(50, 150),)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer)
              .convert("RGB"), (0, 0))
    return img


def _draw_lines(Image, ImageDraw, size, rng, rgbs):
    img = Image.new("RGB", (size, size), _darken(rgbs[0], 0.25))
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width = max(2, size // 160)
    for _ in range(rng.randint(10, 18)):
        y_base = rng.uniform(0, size)
        phase = rng.uniform(0, 2 * math.pi)
        amp = rng.uniform(0.02, 0.12) * size
        freq = rng.uniform(0.8, 2.2)
        drift = rng.uniform(-0.15, 0.15) * size
        colour = rng.choice(rgbs) + (rng.randint(140, 230),)
        steps = 48
        points = []
        for i in range(steps + 1):
            x = size * i / steps
            y = (y_base + math.sin(x / size * 2 * math.pi * freq
                                   + phase) * amp
                 + drift * i / steps)
            points.append((x, y))
        draw.line(points, fill=colour, width=width, joint="curve")
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer)
              .convert("RGB"), (0, 0))
    return img


def _jitter(rgb: tuple, rng: random.Random, amount: int = 18) -> tuple:
    return tuple(max(0, min(255, c + rng.randint(-amount, amount)))
                 for c in rgb)


def _draw_mosaic(Image, ImageDraw, size, rng, rgbs):
    img = Image.new("RGB", (size, size), rgbs[0])
    draw = ImageDraw.Draw(img)
    n = rng.randint(10, 16)
    cell = size / n
    for row in range(n):
        for col in range(n):
            base = rng.choice(rgbs)
            x0, y0 = round(col * cell), round(row * cell)
            x1, y1 = round((col + 1) * cell), round((row + 1) * cell)
            draw.rectangle([x0, y0, x1 - 1, y1 - 1],
                           fill=_jitter(base, rng))
            if rng.random() < 0.25:
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                r = cell * 0.22
                accent = rgbs[(rgbs.index(base) + 1) % len(rgbs)]
                draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                             fill=accent)
    return img


def _draw_gradient(Image, ImageDraw, size, rng, rgbs):
    """Multi-stop diagonal gradient: interpolated per pixel on a
    small canvas, then upscaled smooth — no numpy needed."""
    small = max(32, size // 4)
    img = Image.new("RGB", (small, small))
    px = img.load()
    stops = rgbs  # five harmony colours, evenly spaced over t
    span = 2 * small - 2
    for y in range(small):
        for x in range(small):
            t = (x + y) / span * (len(stops) - 1)
            i = min(len(stops) - 2, int(t))
            frac = t - i
            a, b = stops[i], stops[i + 1]
            px[x, y] = tuple(round(a[c] + (b[c] - a[c]) * frac)
                             for c in range(3))
    return img.resize((size, size), Image.BICUBIC)


def _draw_phyllotaxis(Image, ImageDraw, size, rng, rgbs):
    """A sunflower seed head: dots placed at the golden angle
    (137.508°), spiralling outward, growing as they go and coloured
    by radius through the harmony palette. Deterministic in the
    palette alone — the spiral itself needs no randomness."""
    img = Image.new("RGB", (size, size), _darken(rgbs[0], 0.22))
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    n = size * 3
    max_r = size * 0.48
    golden = math.pi * (3 - math.sqrt(5))
    for i in range(n):
        frac = i / n
        r = max_r * math.sqrt(frac)
        theta = i * golden
        x = cx + r * math.cos(theta)
        y = cy + r * math.sin(theta)
        dot = 1.0 + frac * size * 0.014
        colour = rgbs[min(len(rgbs) - 1, int(frac * len(rgbs)))]
        draw.ellipse([x - dot, y - dot, x + dot, y + dot],
                     fill=colour)
    return img


def _draw_starfield(Image, ImageDraw, size, rng, rgbs):
    """A night sky: a vertical gradient in the palette's darkest
    colour, seeded stars of varied brightness (a few bright ones
    get a cross sparkle), and one soft glow cluster with a denser
    knot of stars around it."""
    top = _darken(rgbs[0], 0.10)
    bottom = _darken(rgbs[0], 0.38)
    img = Image.new("RGB", (size, size), top)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        colour = tuple(round(top[c] + (bottom[c] - top[c]) * t)
                       for c in range(3))
        draw.line([(0, y), (size, y)], fill=colour)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(layer)
    gx = rng.uniform(0.2, 0.8) * size
    gy = rng.uniform(0.15, 0.6) * size
    glow = rgbs[1] if len(rgbs) > 1 else rgbs[0]
    for k in range(6, 0, -1):
        r = size * 0.05 * k
        glow_draw.ellipse([gx - r, gy - r, gx + r, gy + r],
                          fill=glow + (14,))
    img = Image.alpha_composite(img.convert("RGBA"), layer) \
        .convert("RGB")
    draw = ImageDraw.Draw(img)
    star_count = max(60, size // 2)
    for _ in range(star_count):
        x = rng.uniform(0, size)
        y = rng.uniform(0, size)
        b = rng.random()
        if rng.random() < 0.25:
            tint = rng.choice(rgbs)
        else:
            tint = (255, 255, 255)
        colour = tuple(round(c * (0.35 + 0.65 * b)) for c in tint)
        r = 0.6 + b * size * 0.0016
        draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)
        if b > 0.93:
            s = r * 3
            draw.line([x - s, y, x + s, y], fill=colour, width=1)
            draw.line([x, y - s, x, y + s], fill=colour, width=1)
    for _ in range(star_count // 3):
        x = max(0.0, min(size - 1.0, rng.gauss(gx, size * 0.05)))
        y = max(0.0, min(size - 1.0, rng.gauss(gy, size * 0.05)))
        draw.point((x, y), fill=(235, 235, 245))
    return img


_ART_PAINTERS = {
    "circles": _draw_circles,
    "lines": _draw_lines,
    "mosaic": _draw_mosaic,
    "gradient": _draw_gradient,
    "phyllotaxis": _draw_phyllotaxis,
    "starfield": _draw_starfield,
}


def create_art(style: str = "circles", seed: int = 0, size: int = 512,
               output: str = ""):
    """Draw a seeded generative PNG (circles/lines/mosaic/gradient/
    phyllotaxis/starfield).

    Fix 7 (tools loopholes): the save is VERIFIED by re-reading the
    file (core.tool_outcome.file_written) — the result is an Outcome,
    verified only when the PNG is on disk. A save whose re-read
    fails is never claimed as a success.
    """
    style = (style or "circles").strip().lower()
    if style not in _ART_STYLES:
        return (f"I don't draw a '{style}' style — styles are "
                "circles, lines, mosaic, gradient, phyllotaxis "
                "and starfield.")
    try:
        size = int(size)
    except (TypeError, ValueError):
        return f"'{size}' isn't a whole number of pixels."
    if not (_ART_MIN_SIZE <= size <= _ART_MAX_SIZE):
        return (f"Art size must be between {_ART_MIN_SIZE} and "
                f"{_ART_MAX_SIZE} pixels — {size} is outside that.")
    chosen_seed, problem = _pick_seed(seed)
    if problem:
        return problem
    Image, ImageDraw, err = _load_pil()
    if err:
        return err
    rng = random.Random(chosen_seed)
    pal_style, hexes = _seeded_palette(rng)
    rgbs = [_hex_rgb(hexv) for hexv in hexes]
    img = _ART_PAINTERS[style](Image, ImageDraw, size, rng, rgbs)
    if output:
        dest = Path(str(output)).expanduser()
        if not dest.suffix:
            dest = dest.with_suffix(".png")
    else:
        dest = (Path.home() / "Pictures" / "Nova"
                / f"nova_art_{style}_{chosen_seed}.png")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        final = _non_clobbering(dest)
        img.save(final, "PNG")
    except OSError as e:
        return f"Couldn't save the picture to {dest}: {e}"
    from . import tool_outcome as _outcome  # Fix 7: verify the save
    _ok, _evidence = _outcome.file_written(final)
    if not _ok:
        return _outcome.failed(
            "The picture was drawn but the save to %s could not be "
            "confirmed: %s." % (final, _evidence), _evidence)
    return _outcome.verified(
        "Created %s art — a %d×%d PNG saved to "
        "%s (seed %d; %s palette: "
        "%s)."
        "%s Ask for seed %d "
        "with another style to iterate on the same palette."
        % (style, size, size, final, chosen_seed, pal_style,
           ", ".join(hexes), _suffix_note(dest, final), chosen_seed),
        _evidence)

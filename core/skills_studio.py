"""
Studio skills pack for VYUK (v5.14) — two COMPOSED tools that build
on machinery other packs already own, reused by call-time import and
never re-implemented:

* make_wallpaper  — core/skills_creative.create_art draws the
  picture; this module centre-crops it to 1920×1080; then
  core/skills_desktop_prefs.set_wallpaper applies it.
* palette_image   — the visual companion to
  core/skills_creative.generate_palette: the same HSV harmony core
  (skills_creative._harmonies) rendered as a labelled swatch PNG.

Composition honesty rule (the house rule for composed tools): every
step's outcome is reported individually. A failed step never fakes
another step's success, and a half-done job is stated as partial —
the art file existing is never reported as "wallpaper set" unless
the setter's own read-back confirmed it.

Seed ownership: make_wallpaper resolves the seed BEFORE generating
(via skills_creative._pick_seed, so 0 becomes a concrete pick) and
passes it explicitly, and it decides the output path itself (via
skills_creative._non_clobbering) and passes output= — so the seed
and the path in the result are known facts, never parsed out of
another tool's prose.

Read/write split: both tools write one PNG each (NOT read-only);
make_wallpaper additionally changes a system setting through the
setter, which is ungated — this tool keeps that parity. Handlers
never raise: every failure is an honest line naming the step.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import random
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared bits
# ---------------------------------------------------------------------------

_WALLPAPER_GEN_SIZE = 1920          # create_art is square-only
_WALLPAPER_W = 1920
_WALLPAPER_H = 1080
_SWATCH_W = 1200
_SWATCH_H = 240


def _truthy(value) -> bool:
    """Coerce a tool argument to a bool the way the executor's JSON
    sometimes needs: real bools pass through, strings like "false"
    must NOT become True just because they're non-empty."""
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value)


def _label_colour(rgb: tuple) -> tuple:
    """Black or white label text for a colour band, by perceived
    luminance (Rec. 601, the same weighting PIL's convert("L")
    uses): dark bands get white text, light bands get black."""
    r, g, b = rgb
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return (255, 255, 255) if luminance < 140 else (0, 0, 0)


# ---------------------------------------------------------------------------
# MAKE WALLPAPER — create_art → centre-crop → set_wallpaper
# ---------------------------------------------------------------------------


def make_wallpaper(style: str = "gradient", seed: int = 0,
                   set_it: bool = True) -> str:
    """Generate 1920×1080 desktop art and (by default) set it as
    the wallpaper, composing create_art + a centre-crop +
    set_wallpaper. Each step's verdict is reported; a set failure
    still leaves the picture saved and says exactly that."""

    # R1a (tools loopholes, seam pass 2): the crop already
    # re-reads the picture and checks its dimensions, and the set
    # step reads the Desktop setting back — the result is now an
    # Outcome carrying that verification instead of a bare string.
    from . import tool_outcome as _outcome  # R1a: seam wiring

    from . import skills_creative as creative

    style = (style or "gradient").strip().lower()
    # Resolve the seed first (0 -> a concrete pick) so the seed in
    # this result is a fact, and generation gets it explicitly.
    chosen_seed, problem = creative._pick_seed(seed)
    if problem:
        return problem
    # Decide the path here, pre-suffixed with creative's own
    # non-clobber rule, and hand it to create_art via output= so
    # the location is deterministic — never parsed from its text.
    token = re.sub(r"[^a-z0-9]+", "-", style).strip("-") or "art"
    dest = (Path.home() / "Pictures" / "Nova"
            / f"nova_wallpaper_{token}_{chosen_seed}.png")
    final = creative._non_clobbering(dest)
    suffix = creative._suffix_note(dest, final)

    # Step 1 — draw (square: create_art has no rectangle mode).
    art_text = creative.create_art(style=style, seed=chosen_seed,
                                   size=_WALLPAPER_GEN_SIZE,
                                   output=str(final))
    if not final.is_file():
        return ("Making the wallpaper stopped at the drawing step "
                f"— nothing was saved or set. The art tool said: "
                f"{art_text}")

    # Step 2 — centre-crop the square to 1920×1080 in place.
    Image, _ImageDraw, err = creative._load_pil()
    if err:
        return _outcome.failed(
            f"Partial success: the art was drawn and saved to "
            f"{final} (seed {chosen_seed}), but the crop step "
            f"couldn't run: {err} The file is still square, "
            "and the wallpaper was not set.",
            "crop step could not run: %s" % err)
    try:
        with Image.open(final) as img:
            gen_w, gen_h = img.size
            box_w = min(gen_w, gen_h * _WALLPAPER_W // _WALLPAPER_H)
            box_h = box_w * _WALLPAPER_H // _WALLPAPER_W
            left = (gen_w - box_w) // 2
            top = (gen_h - box_h) // 2
            cropped = img.crop((left, top, left + box_w,
                                top + box_h))
            if cropped.size != (_WALLPAPER_W, _WALLPAPER_H):
                cropped = cropped.resize(
                    (_WALLPAPER_W, _WALLPAPER_H), Image.LANCZOS)
            cropped.save(final, "PNG")
        with Image.open(final) as check_img:
            out_w, out_h = check_img.size
    except Exception as e:  # noqa: BLE001 — report, never raise
        return _outcome.failed(
            f"Partial success: the art was drawn and saved to "
            f"{final} (seed {chosen_seed}), but cropping it to "
            f"1920×1080 failed: {e} The wallpaper was not set.",
            "crop failed: %s" % e)
    if (out_w, out_h) != (_WALLPAPER_W, _WALLPAPER_H):
        return _outcome.failed(
            f"Partial success: the art was drawn and saved to "
            f"{final} (seed {chosen_seed}), but it came out "
            f"{out_w}×{out_h} instead of 1920×1080, so the "
            "wallpaper was not set.",
            "cropped size %dx%d, not 1920x1080" % (out_w, out_h))
    made = (f"{style} art (seed {chosen_seed}), generated at "
            f"{gen_w}×{gen_h} and centre-cropped to 1920×1080, "
            f"saved to {final}.{suffix}")

    # Step 3 — set it (the setter verifies by reading the Desktop
    # setting back; its clean success line is the contract).
    if not _truthy(set_it):
        return _outcome.verified(
            f"Wallpaper picture made: {made} I didn't set it — "
            "ask me to set that file as the wallpaper whenever "
            "you're ready.",
            "1920x1080 picture verified on disk at %s; not set "
            "by request" % final)
    from . import skills_desktop_prefs as desktop2

    verdict = desktop2.set_wallpaper(str(final))
    if verdict.startswith("Wallpaper set to '"):
        return _outcome.verified(
            f"Wallpaper made and set: {made} It's now your wallpaper.",
            "1920x1080 picture verified on disk; desktop setting "
            "read back to confirm: %s" % verdict)
    return _outcome.failed(
        f"Partial success: the wallpaper picture was made — "
        f"{made} — but the set step didn't confirm: {verdict} "
        "The picture stays saved; you can ask me to set that "
        "file as the wallpaper any time.",
        "set step did not confirm: %s" % verdict)


# ---------------------------------------------------------------------------
# PALETTE IMAGE — generate_palette's harmonies as a swatch PNG
# ---------------------------------------------------------------------------


def palette_image(style: str = "random", base: str = "",
                  output: str = "") -> str:
    """Render a palette from generate_palette as a 1200×240 PNG:
    five colour bands, each labelled with its hex."""
    from . import skills_creative as creative

    style = (style or "random").strip().lower()
    if style not in creative._PALETTE_STYLES + ("random",):
        return (f"I don't know a '{style}' palette — styles are "
                "complementary, analogous, triadic, monochrome "
                "or random.")
    # Resolve the base exactly the way generate_palette does, then
    # take the colours from the same factored harmony core it uses.
    base = (base or "").strip()
    if base:
        m = creative._HEX_RE.match(base)
        if not m:
            return (f"'{base}' isn't a colour I can read — give "
                    "the base as #RRGGBB, e.g. #3B82F6, or leave "
                    "it empty and I'll pick a hue.")
        digits = m.group(1).upper()
        r, g, b = (int(digits[i:i + 2], 16) / 255.0
                   for i in (0, 2, 4))
        import colorsys

        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        base_hex = "#" + digits
    else:
        h = random.random()
        s = 0.55 + random.random() * 0.40
        v = 0.70 + random.random() * 0.30
        base_hex = creative._hsv_hex(h, s, v)
    harmonies = creative._harmonies(h, s, v)
    chosen = (random.choice(creative._PALETTE_STYLES)
              if style == "random" else style)
    pairs = harmonies[chosen]
    hexes = [hexv for _role, hexv in pairs]

    Image, ImageDraw, err = creative._load_pil()
    if err:
        return err
    try:
        from PIL import ImageFont

        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001 — font is a nicety, not a step
        font = None
    img = Image.new("RGB", (_SWATCH_W, _SWATCH_H))
    draw = ImageDraw.Draw(img)
    band = _SWATCH_W // len(pairs)
    for i, (_role, hexv) in enumerate(pairs):
        rgb = creative._hex_rgb(hexv)
        x0 = i * band
        draw.rectangle([x0, 0, x0 + band - 1, _SWATCH_H - 1],
                       fill=rgb)
        draw.text((x0 + 10, _SWATCH_H - 26), hexv,
                  fill=_label_colour(rgb), font=font)

    if output:
        dest = Path(str(output)).expanduser()
        if not dest.suffix:
            dest = dest.with_suffix(".png")
    else:
        dest = (Path.home() / "Pictures" / "Nova"
                / f"nova_palette_{chosen}.png")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        final = creative._non_clobbering(dest)
        img.save(final, "PNG")
    except OSError as e:
        return f"Couldn't save the palette picture to {dest}: {e}"
    suffix = creative._suffix_note(dest, final)
    message = (f"Palette picture saved to {final} \u2014 {chosen} harmony "
               f"built on {base_hex}: {', '.join(hexes)} (a "
               f"{_SWATCH_W}\u00d7{_SWATCH_H} PNG, five bands labelled with "
               "their hex in PIL's built-in font)."
               f"{suffix}")
    # R1a: verify the save by re-reading the file — same pattern
    # as create_art (Fix 7).
    from . import tool_outcome as _outcome  # R1a: seam wiring

    ok, evidence = _outcome.file_written(final)
    if ok:
        return _outcome.verified(message, evidence)
    return _outcome.failed(
        f"The palette picture was drawn but the save to {final} "
        f"could not be confirmed: {evidence}.", evidence)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Studio pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="make_wallpaper",
        description="Make desktop wallpaper end-to-end in one ask: generate seeded art (styles: circles, lines, mosaic, gradient), centre-crop it to 1920×1080, save it to ~/Pictures/Nova/nova_wallpaper_<style>_<seed>.png and — unless set_it is false — set it as the Windows wallpaper (the setter confirms by reading the desktop setting back). The seed fixes the picture and its palette (0 picks one and the result reports it, so the user can recreate or restyle it). Each step is reported honestly: if setting fails, the result says the picture was saved but the wallpaper did not change, with the reason.",
        parameters={
            "type": "object",
            "properties": {
                "style": {"type": "string", "enum": ["circles", "lines", "mosaic", "gradient"], "description": "Art style (default gradient)"},
                "seed": {"type": "integer", "description": "Seed for the picture + palette (0 = pick one and report it)"},
                "set_it": {"type": "boolean", "description": "Also set it as the wallpaper (default true); false only saves the picture"},
            },
            "required": [],
        },
        handler=make_wallpaper,
    )
    registry.register(
        name="palette_image",
        description="The picture version of generate_palette: build a five-colour harmony (complementary, analogous, triadic, monochrome, or random) from a base colour as #RRGGBB (or a random hue when empty) and save it as a 1200×240 PNG swatch — five bands, each labelled with its hex in contrasting text (PIL's built-in font; no fancy typeface). Default save is ~/Pictures/Nova/nova_palette_<style>.png; an existing file is never overwritten (a ' (2)' suffix is added and reported). Returns the five hexes plus the path.",
        parameters={
            "type": "object",
            "properties": {
                "style": {"type": "string", "enum": ["complementary", "analogous", "triadic", "monochrome", "random"], "description": "The harmony to render (default random)"},
                "base": {"type": "string", "description": "Base colour as #RRGGBB (optional; a random hue is used when empty)"},
                "output": {"type": "string", "description": "Optional output path (default ~/Pictures/Nova/)"},
            },
            "required": [],
        },
        handler=palette_image,
    )

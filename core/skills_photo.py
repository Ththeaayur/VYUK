"""
Photo editing skills pack for VYUK (v5.13) — the EDITING lab.

The earlier media packs only ever PROCESSED images (resize,
convert, compress, strip metadata). This pack is where pictures
actually get EDITED: cropped, rotated, rebalanced, filtered,
watermarked, framed and combined into collages.

Same rules as core/skills_media_convert.py / core/skills_files_ops.py: plain
functions returning human-readable strings, never raising; Pillow
is imported lazily inside the helpers and guarded, so this module
imports cleanly on any OS. Nothing here ever overwrites: an output
name that is taken gets a " (2)"-style suffix and the result
reports the real name. Every input is validated — read and
measured — BEFORE anything is written, so a bad input aborts
cleanly with no partial output left behind.

Image geometry always follows the EXIF orientation (the v4.7
rule): every tool loads through _load_oriented(), so coordinates,
dimensions and results are always in the picture as displayed,
never the raw stored pixels.

Wiring: the parent registers this pack via register_all(registry)
— this module never touches the registry singleton itself.
"""

# SKILLS SPLIT PHASE A BATCH A17 INTENTIONAL: the old module's
# namespace carried its stdlib imports; they are re-exported here
# (dir-diff-empty bar, plan SS2 P2) rather than dropped.
import math
from pathlib import Path
from .skills_photo_helpers import (
    _human_size,
    _existing_file,
    _non_clobbering,
    _load_pil,
    _load_oriented,
    _has_alpha,
    _flatten_on_white,
    _resolve_output,
    _save_result,
    _result_line,
    _as_int,
    _as_float,
    _rgb_ready,
)
from .skills_photo_edit import (
    crop_image,
    rotate_image,
    _ADJUST_FACTORS,
    adjust_image,
    _FILTERS,
    _SEPIA_MATRIX,
    _sepia_pixel,
    _sepia_image,
    filter_image,
)
from .skills_photo_finish import (
    _FONT_CANDIDATES,
    _WATERMARK_POSITIONS,
    _load_font,
    watermark_image,
    _NAMED_COLORS,
    _parse_color,
    add_border,
    _crop_to_fill,
    make_collage,
)

# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register every Photo pack tool on a ToolRegistry-like object."""
    registry.register(
        name="crop_image",
        description="Crop ONE image to a pixel box, saving the result as a NEW file — the original is never touched. Coordinates are pixels in the picture as DISPLAYED (EXIF orientation honoured): left/top is the top-left corner, right/bottom the bottom-right (exclusive). A box reaching partly outside the picture is clamped to the edges and the result says so; an empty, inverted or fully-outside box is refused. Default output is '<name>_cropped.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants a picture trimmed to a region.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to crop"},
                "left": {"type": "integer", "description": "Left edge of the crop box, in pixels"},
                "top": {"type": "integer", "description": "Top edge of the crop box, in pixels"},
                "right": {"type": "integer", "description": "Right edge of the crop box, in pixels (exclusive)"},
                "bottom": {"type": "integer", "description": "Bottom edge of the crop box, in pixels (exclusive)"},
                "output": {"type": "string", "description": "Optional full path for the cropped image (default: '<name>_cropped.<ext>' beside the input)"},
            },
            "required": ["path", "left", "top", "right", "bottom"],
        },
        handler=crop_image,
    )
    registry.register(
        name="rotate_image",
        description="Rotate ONE image, saving the result as a NEW file — the original is never touched. Positive degrees turn the picture CLOCKWISE. Multiples of 90° rotate losslessly (no resampling); any other angle is resampled (bicubic) onto an expanded canvas and the result says so. The optional flip ('horizontal' or 'vertical') is applied after the rotation. Default output is '<name>_rotated.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants a picture turned or mirrored.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to rotate"},
                "degrees": {"type": "number", "description": "Clockwise rotation angle in degrees (default 90); any value, normalised mod 360"},
                "flip": {"type": "string", "enum": ["none", "horizontal", "vertical"], "description": "Optional mirror applied after rotating: 'none' (the default), 'horizontal' or 'vertical'"},
                "output": {"type": "string", "description": "Optional full path for the rotated image (default: '<name>_rotated.<ext>' beside the input)"},
            },
            "required": ["path"],
        },
        handler=rotate_image,
    )
    registry.register(
        name="adjust_image",
        description="Adjust ONE image's brightness, contrast, colour and sharpness, saving the result as a NEW file — the original is never touched. Each factor is 1.0 for unchanged and may range 0.0–3.0 (e.g. brightness 1.2 is 20% brighter, color 0.0 drains the picture to grey). If all four factors are 1.0 the call is refused — there would be nothing to change. The result names only the factors that actually changed. Default output is '<name>_adjusted.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants a picture lightened, darkened, punched up or softened.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to adjust"},
                "brightness": {"type": "number", "description": "Brightness factor, 0.0–3.0 (1.0 = unchanged)"},
                "contrast": {"type": "number", "description": "Contrast factor, 0.0–3.0 (1.0 = unchanged)"},
                "color": {"type": "number", "description": "Colour saturation factor, 0.0–3.0 (1.0 = unchanged, 0.0 = grey)"},
                "sharpness": {"type": "number", "description": "Sharpness factor, 0.0–3.0 (1.0 = unchanged)"},
                "output": {"type": "string", "description": "Optional full path for the adjusted image (default: '<name>_adjusted.<ext>' beside the input)"},
            },
            "required": ["path"],
        },
        handler=adjust_image,
    )
    registry.register(
        name="filter_image",
        description="Apply a photo filter to ONE image, saving the result as a NEW file — the original is never touched. Filters: grayscale (black & white), sepia (warm vintage tone), invert (photo negative), blur (soft Gaussian; its strength is set by radius, 0.5–20, default 2), sharpen, edges (edge detection), emboss (relief), posterize (flat colour bands), solarize (partial negative). Default output is '<name>_<filter>.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user asks for a picture in black and white, sepia, or another stylised look.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to filter"},
                "filter": {
                    "type": "string",
                    "enum": ["grayscale", "sepia", "invert", "blur", "sharpen",
                             "edges", "emboss", "posterize", "solarize"],
                    "description": "The filter to apply",
                },
                "output": {"type": "string", "description": "Optional full path for the filtered image (default: '<name>_<filter>.<ext>' beside the input)"},
                "radius": {"type": "number", "description": "Blur strength, 0.5–20 (0 = default 2); only applies to the blur filter"},
            },
            "required": ["path", "filter"],
        },
        handler=filter_image,
    )
    registry.register(
        name="watermark_image",
        description="Stamp a text watermark onto ONE image, saving the result as a NEW file — the original is never touched. The text is drawn on a transparent overlay and composited over the photo at the given opacity (0.1–1.0), in white, at a size proportional to the picture. Positions: bottom_right, bottom_left, top_right, top_left, center. A system TrueType font is used when available; otherwise Pillow's small built-in font is used and the result says so. Default output is '<name>_watermarked.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants their name or a label stamped on a picture.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to watermark"},
                "text": {"type": "string", "description": "The watermark text to stamp"},
                "position": {"type": "string", "enum": ["bottom_right", "bottom_left", "top_right", "top_left", "center"], "description": "Where the text goes (default bottom_right)"},
                "opacity": {"type": "number", "description": "Watermark opacity, 0.1–1.0 (default 0.5)"},
                "output": {"type": "string", "description": "Optional full path for the watermarked image (default: '<name>_watermarked.<ext>' beside the input)"},
            },
            "required": ["path", "text"],
        },
        handler=watermark_image,
    )
    registry.register(
        name="add_border",
        description="Add a solid border (frame) around ONE image, saving the result as a NEW file — the original is never touched. The border adds width_px (1–500) on EVERY side, so the picture grows by twice the width in each dimension. The colour is a '#RRGGBB' hex value or a named colour: black, white, red, blue, green, yellow, pink, purple, orange, gray. Default output is '<name>_framed.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants a picture framed or padded with a border.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to frame"},
                "width_px": {"type": "integer", "description": "Border width in pixels per side, 1–500 (default 24)"},
                "color": {"type": "string", "description": "Border colour: '#RRGGBB' hex or a named colour (default '#000000')"},
                "output": {"type": "string", "description": "Optional full path for the framed image (default: '<name>_framed.<ext>' beside the input)"},
            },
            "required": ["path"],
        },
        handler=add_border,
    )
    registry.register(
        name="make_collage",
        description="Combine 2–9 images into ONE grid collage, saved as a NEW file — the originals are never touched. Every input is validated first: one missing or corrupt picture refuses the whole collage (named in the result) and nothing is written. columns 0 chooses a near-square grid automatically. Each picture is centre-cropped to fill its cell exactly (crop-to-fill) on a background colour (black by default; a '#RRGGBB' hex or colour name can be given); cells use the median input size, capped at 1200px per side. Default output is '<first-stem>_collage.png' beside the first input (PNG, so the grid isn't recompressed); an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants several pictures combined into one grid image.",
        parameters={
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The image files to combine — a list of 2 to 9 full paths",
                },
                "columns": {"type": "integer", "description": "Grid columns (0 = automatic near-square layout)"},
                "output": {"type": "string", "description": "Optional full path for the collage (default: '<first-stem>_collage.png' beside the first input)"},
                "background": {"type": "string", "description": "Background colour behind transparent areas: '#RRGGBB' hex or a named colour (default black)"},
            },
            "required": ["paths"],
        },
        handler=make_collage,
    )

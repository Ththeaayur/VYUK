"""
Photo EFFECTS pack for VYUK (v5.14) — the creative sibling of
core/skills_photo.py (the v5.13 editing lab: crop, rotate,
adjust, filter, watermark, border, collage). Where that pack
EDITS pictures, this one plays with them: kaleidoscope mandalas,
duotone remapping, pixel-art pixelation and RGB glitch art.

Same rules as the rest of the photo family: plain functions
returning human-readable strings, never raising; Pillow imported
lazily and guarded, so this module imports cleanly anywhere.
Nothing ever overwrites — a taken output name gets a
" (2)"-style suffix and the result reports the real name. Every
input is validated and fully loaded BEFORE anything is written.

Geometry always follows the EXIF orientation (the v4.7 rule):
every tool loads through _load_oriented(), so all dimensions
and results are in the picture as displayed.

The kaleidoscope construction (the interesting one): the centre
square of the picture is mirror-tiled into a 3×3 canvas — a
plain photo has no pixels at the corners' radius for most
angles, so a 1:1 fold onto a square can only work if the source
is treated as reflective tiling. On that canvas, one wedge of
the pattern is built from the picture and the rest of the
mandala is completed with exact flips/transposes wherever the
segment count allows (segments divisible by 4 complete by
quadrant mirroring; the rest complete by half-plane mirroring),
so the mirror symmetries hold as exact pixel permutations, not
approximations.

Wiring: the parent registers this pack via register_all(registry)
— this module never touches the registry singleton itself.
"""

# SKILLS SPLIT PHASE A BATCH A6 INTENTIONAL: the old module's
# namespace carried its stdlib imports; they are re-exported here
# (dir-diff-empty bar, plan SS2 P2) rather than dropped.
import math
import random
from pathlib import Path
from .skills_photo2_helpers import (
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
    _rgb_ready,
    _split_alpha,
    _join_alpha,
)
from .skills_photo2_effects import (
    _NAMED_COLORS,
    _parse_color,
    _KALEIDO_MAX_SIDE,
    _tile3_mirror,
    _wedge_mask,
    _own_sector,
    _rotate_piece,
    _assemble_quadrants,
    _assemble_halves,
    _kaleidoscope_quadrants,
    _kaleidoscope_halves,
    kaleidoscope_image,
    _duotone_lut,
    duotone_image,
    pixelate_image,
    _offset_channels,
    _slice_shifts,
    _apply_slices,
    glitch_image,
)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Photo Effects pack tool on a ToolRegistry-like object."""
    registry.register(
        name="kaleidoscope_image",
        description="Turn ONE picture into a kaleidoscope mandala, saving it as a NEW square PNG — the original is never touched. The centre square of the picture (side = its smaller dimension) is folded into mirror-alternating wedges around the centre. segments must be EVEN, 4–16 (default 8): the wedges alternate mirrored/unmirrored around the full turn and an odd count cannot mirror back into itself. Very large pictures are scaled to a 1024-pixel square first and the result says so. Default output is '<name>_kaleidoscope.png' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants a mandala / kaleidoscope effect from a photo.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to fold"},
                "segments": {"type": "integer", "description": "Number of mirror wedges — EVEN, 4 to 16 (default 8)"},
                "output": {"type": "string", "description": "Optional full path for the mandala (default: '<name>_kaleidoscope.png' beside the input)"},
            },
            "required": ["path"],
        },
        handler=kaleidoscope_image,
    )
    registry.register(
        name="duotone_image",
        description="Remap ONE picture to a two-colour duotone, saving the result as a NEW file — the original is never touched. The picture is reduced to grayscale, then every tone is mapped along a ramp between the shadow colour (for black) and the highlight colour (for white) — the classic two-ink print look. Colours are '#RRGGBB' hex or named: black, white, red, blue, green, yellow, pink, purple, orange, gray. Defaults are a deep indigo shadow (#1a1a40) and a warm gold highlight (#f5d76e). Transparency is preserved untouched. Default output is '<name>_duotone.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants a stylised two-tone version of a picture.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to remap"},
                "shadow": {"type": "string", "description": "Colour for the darkest tones: '#RRGGBB' or a named colour (default '#1a1a40')"},
                "highlight": {"type": "string", "description": "Colour for the lightest tones: '#RRGGBB' or a named colour (default '#f5d76e')"},
                "output": {"type": "string", "description": "Optional full path for the duotone (default: '<name>_duotone.<ext>' beside the input)"},
            },
            "required": ["path"],
        },
        handler=duotone_image,
    )
    registry.register(
        name="pixelate_image",
        description="Pixelate ONE picture into retro pixel art, saving the result as a NEW file — the original is never touched. The picture is shrunk to one pixel per block and scaled back up with nearest-neighbour, so the DIMENSIONS ARE UNCHANGED and every block is one flat colour. pixel_size is the block size in pixels, 2–100 (default 12), and must be less than half the picture's smaller side. Default output is '<name>_pixelated.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants a mosaic / pixel-art / censor-pixel look.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to pixelate"},
                "pixel_size": {"type": "integer", "description": "Block size in pixels, 2–100 (default 12); must be less than half the smaller side"},
                "output": {"type": "string", "description": "Optional full path for the pixelated image (default: '<name>_pixelated.<ext>' beside the input)"},
            },
            "required": ["path"],
        },
        handler=pixelate_image,
    )
    registry.register(
        name="glitch_image",
        description="Give ONE picture an RGB-split glitch-art look, saving the result as a NEW file — the original is never touched. The red channel shifts `shift` pixels right (0–200, default 12) and the blue channel the same left, both WRAPPING around the edges, and 5–9 seeded horizontal slices are displaced sideways. The effect is DETERMINISTIC per seed — the same seed always makes the same file; seed 0 (default) picks one at random and the result reports it, so the look can be recreated. Transparency is left completely untouched (the original alpha is reattached after the colour channels are glitched). Default output is '<name>_glitch.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. Use when the user wants a glitch / vaporwave / broken-signal look.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to glitch"},
                "shift": {"type": "integer", "description": "Channel split in pixels, 0–200 (default 12)"},
                "seed": {"type": "integer", "description": "Seed for the slice displacement (default 0 = pick one at random and report it); the same seed always makes the same file"},
                "output": {"type": "string", "description": "Optional full path for the glitched image (default: '<name>_glitch.<ext>' beside the input)"},
            },
            "required": ["path"],
        },
        handler=glitch_image,
    )

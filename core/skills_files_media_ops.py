"""
Files & Media II skills pack for VYUK (v5.21, HANDS) — the file
jobs the earlier packs left on the table: comparing two images by
their actual pixels, tiling a folder of sprites into one sheet,
watermarking a whole folder at once, pulling the embedded images
out of a PDF, trimming and fading WAV audio, and a size map that
shows where a folder's bytes actually live.

Same rules as the packs before it (core/skills_files_inspect.py and
core/skills_photo.py): plain functions returning human-readable
strings, never raising; dependencies (PIL, pypdf, numpy) are
imported lazily inside the function that needs them and guarded,
so this module imports cleanly on any OS. Nothing here ever
overwrites silently: an output name that is taken gets a
" (2)"-style suffix and the result reports the real name.
batch_watermark delegates each stamp to skills_photo's
watermark_image — the very same draw the single-image tool uses —
and writes only into a separate output folder, so originals are
never touched.

Deliberately NOT here: single-image watermarking is watermark_image
(skills_photo), collage-making is make_collage there too (a collage
crops to fill its cells; a sprite sheet pads to a uniform cell and
keeps every pixel), PDF merging/splitting/info live in the earlier
packs, and folder_size already gives a folder's TOTAL — this pack's
folder_size_map gives the BREAKDOWN instead.

Audio scope is honest: trim and fade work on WAV files only
(stdlib wave + numpy). Anything else is refused in plain words
with a pointer to convert it first, never half-processed.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import math
import os
import wave
from pathlib import Path

# INTENTIONAL: re-exports of every moved name, so importers of the old
# module path keep working unchanged (facade discipline, plan section 2 P2).
from .skills_files4_helpers import (
    _IMAGE_EXTS,
    _MAP_ENTRY_CAP,
    _MAP_LIST_CAP,
    _PDF_IMAGE_LIST_CAP,
    _human_size,
    _existing_file,
    _existing_folder,
    _non_clobbering,
    _suffix_note,
    _load_pil,
    _load_pypdf,
    _load_numpy,
    _sha256_file,
    _fmt_rgb,
)
from .skills_files4_image import (
    image_compare,
    _list_images,
    make_sprite_sheet,
    _BATCH_POSITIONS,
    batch_watermark,
    pdf_extract_images,
)
from .skills_files4_audio_size import (
    _WAV_DTYPES,
    _WAV_FULL_SCALE,
    _read_wav,
    _write_wav,
    _wav_target,
    _fmt_seconds,
    audio_trim,
    audio_fade,
    _tree_size,
    folder_size_map,
)

# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Files & Media II pack tool on a ToolRegistry-like object."""
    registry.register(
        name="image_compare",
        description="Compare two image files by their actual pixels: dimensions, format and file size for each, each picture's average colour (RGB), the mean pixel distance (0-255, measured on 64x64 reductions), the share of pixels that differ noticeably, and a similarity verdict. Byte-identical files (SHA-256 match) are called identical outright; same-size files with a near-zero distance are 'nearly identical' (a re-save or metadata change); larger distances grade through very similar / somewhat similar / quite different. Differing dimensions are flagged — that comparison is look-alike, not pixel-perfect. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "Full path of the first image file"},
                "path_b": {"type": "string", "description": "Full path of the second image file"},
            },
            "required": ["path_a", "path_b"],
        },
        handler=image_compare,
    )
    registry.register(
        name="make_sprite_sheet",
        description="Tile every image directly inside a folder into ONE sprite-sheet PNG: a uniform grid whose cell is the widest/tallest image, each sprite centred in its cell with transparent padding — no image is scaled or cropped (for cropped, filled cells use make_collage instead). Files are placed in name order, left to right, top to bottom. columns=0 picks a near-square grid automatically. The sheet defaults to 'sprite_sheet.png' in the same folder and never overwrites (a ' (2)'-style suffix is used and reported). Files that can't be read as images are skipped and named.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Full path of the folder holding the sprite images"},
                "columns": {"type": "integer", "description": "Grid columns (default 0 = automatic near-square)"},
                "output_path": {"type": "string", "description": "Optional full path for the sheet PNG (default: 'sprite_sheet.png' in the same folder)"},
            },
            "required": ["folder"],
        },
        handler=make_sprite_sheet,
    )
    registry.register(
        name="batch_watermark",
        description="Stamp the same text watermark onto EVERY image in a folder at once — the same draw as the single-image watermark_image — writing the stamped copies into a separate output folder ('watermarked' inside the source folder by default) under the originals' names. Position and opacity can be set for the whole batch (bottom-right at half opacity by default, exactly like the single tool). The originals are never modified. Reports how many images were processed and names any that were skipped. Use watermark_image instead when only one picture needs stamping.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Full path of the folder holding the images"},
                "text": {"type": "string", "description": "Watermark text to stamp on every image"},
                "output_folder": {"type": "string", "description": "Optional folder for the stamped copies (default: a 'watermarked' subfolder of the source)"},
                "position": {"type": "string", "enum": ["bottom_right", "bottom_left", "top_right", "top_left", "center"], "description": "Where the text goes on every image (default bottom_right)"},
                "opacity": {"type": "number", "description": "Watermark opacity, 0.1–1.0 (default 0.5)"},
            },
            "required": ["folder", "text"],
        },
        handler=batch_watermark,
    )
    registry.register(
        name="pdf_extract_images",
        description="Extract the images EMBEDDED in a PDF and save them as files (JPEG/PNG/etc. exactly as stored) in an output folder ('<name>_images' beside the PDF by default), reporting how many came out, their names (first 20 shown) and their total size. Never overwrites (a ' (2)'-style suffix is used). A PDF with no embedded images is reported plainly — note that vector drawings and text are not images and don't extract. Encrypted PDFs can't be read without their password and say so.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the PDF file"},
                "output_folder": {"type": "string", "description": "Optional folder to write the images into (default: '<name>_images' beside the PDF)"},
            },
            "required": ["path"],
        },
        handler=pdf_extract_images,
    )
    registry.register(
        name="audio_trim",
        description="Trim a WAV audio file to a time range, saving the cut as a NEW file ('<name>_trimmed.wav' beside the input by default; never overwritten — a ' (2)'-style suffix is used and reported). start_s and end_s are in seconds (end_s of 0 means 'to the end of the file'); the range is checked against the real duration before anything is written. WAV ONLY: any other format is refused in plain words with a pointer to convert it first. Use to cut silence or lift a section out of a recording.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the WAV file"},
                "start_s": {"type": "number", "description": "Start of the kept range, in seconds from the beginning"},
                "end_s": {"type": "number", "description": "End of the kept range, in seconds (0 = to the end of the file)"},
                "output_path": {"type": "string", "description": "Optional full path for the trimmed WAV (default: '<name>_trimmed.wav' beside the input)"},
            },
            "required": ["path", "start_s", "end_s"],
        },
        handler=audio_trim,
    )
    registry.register(
        name="audio_fade",
        description="Apply linear fade-in and fade-out ramps to a WAV audio file, saving the result as a NEW file ('<name>_faded.wav' beside the input by default; never overwritten — a ' (2)'-style suffix is used and reported). The file's length and format don't change, only the volume envelope; fades longer than the file overlap gracefully and the result says so. WAV ONLY: any other format is refused in plain words with a pointer to convert it first. Use to soften the start/end of a recording or voice note.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the WAV file"},
                "fade_in_s": {"type": "number", "description": "Fade-in length in seconds (default 1.0; 0 = no fade-in)"},
                "fade_out_s": {"type": "number", "description": "Fade-out length in seconds (default 2.0; 0 = no fade-out)"},
            },
            "required": ["path"],
        },
        handler=audio_fade,
    )
    registry.register(
        name="folder_size_map",
        description="Map WHERE a folder's bytes live: the total size broken down per top-level entry (recursive sizes — a folder's number includes everything inside it), biggest first, top 15 shown with each entry's percentage share of the whole, plus the total number of files counted. depth 2 (default) also peeks inside the biggest folders, listing their top 3 children. Files counted are capped at 20000 and the result discloses it when the cap is hit. Read-only. Use when the user asks what's taking up space in a folder (folder_size gives only the total; for the biggest individual files use largest_files).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the folder to map"},
                "depth": {"type": "integer", "description": "1 = top level only, 2 = also peek into the biggest folders (default), 3 = same map with the peek applied more widely"},
            },
            "required": ["path"],
        },
        handler=folder_size_map,
    )

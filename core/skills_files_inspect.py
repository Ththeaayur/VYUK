"""
Files & Media III skills pack for VYUK (v5.12) — the file jobs the
earlier packs never covered: comparing two folders recursively,
stripping an image's embedded metadata into a clean copy, reading
a PDF's facts (pages, size, metadata, encryption), and rotating
a PDF's pages.

Deliberately NOT here (they already exist elsewhere): text-file
diffing is compare_files (core/skills_files_ops.py), PDF merging is
merge_pdfs there too, and splitting is split_pdf
(core/skills_media_convert.py). This pack never imports its siblings —
shared postures (page-spec parsing, non-clobbering outputs) are
re-factored locally, pack by pack, as the house pattern requires.

Same rules as core/skills_files_ops.py and core/skills_media_convert.py:
plain functions returning human-readable strings, never raising;
dependencies (PIL, pypdf) are imported lazily inside the function
that needs them and guarded, so this module imports cleanly on
any OS. Nothing here ever overwrites silently: an output name
that is taken gets a " (2)"-style suffix and the result reports
the real name. Inputs are validated — walked, counted, spec
checked against the real document — BEFORE anything is written.

Privacy posture: metadata tools report COUNTS and PRESENCE only
(how many EXIF tags, whether GPS data exists) — never the values
themselves, following image_info's precedent.

Wiring: the parent registers this pack via register_all(registry)
— this module never touches the registry singleton itself.
"""


# Skills split Phase A batch A3 INTENTIONAL: pure facade — the tool
# implementations live in skills_files3_helpers.py and
# skills_files3_inspect.py; this module keeps register_all verbatim
# plus re-exports of every old top-level name.
# Leaked-import re-exports (P2 dir-diff-empty bar): the old module's
# namespace carried its stdlib imports, so the facade carries them too;
# grep-proven no importer uses them, but nothing is dropped.
import hashlib
import os
import re
from pathlib import Path
from .skills_files3_helpers import (
    _HASH_CHUNK,
    _human_size,
    _existing_file,
    _existing_folder,
    _non_clobbering,
    _suffix_note,
    _load_pil,
    _load_pypdf,
    _sha256_file,
)
from .skills_files3_inspect import (
    _DIFF_MAX_FILES_PER_SIDE,
    _DIFF_MAX_HASH_PAIRS,
    _DIFF_LIST_CAP,
    _scan_tree,
    _diff_inventories,
    _format_name_list,
    folder_diff,
    _exif_summary,
    strip_image_metadata,
    _points_to_mm,
    pdf_info,
    _parse_page_selection,
    _page_list_text,
    rotate_pdf,
)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Files & Media III pack tool on a ToolRegistry-like object."""
    registry.register(
        name="folder_diff",
        description="Compare two folders RECURSIVELY and report the differences: files only in the first, files only in the second, and files present in both whose contents differ (decided by size, then by SHA-256 for same-size files — a size match alone never counts as identical). The result is a compact summary with up to 25 names per category plus each side's total size, not a dump. Caps are disclosed in the result: at most 2000 files walked per folder and 500 same-size pairs hash-checked. Read-only. Use when the user wants to know whether two folders match or what changed between them.",
        parameters={
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "Full path of the first folder"},
                "path_b": {"type": "string", "description": "Full path of the second folder"},
            },
            "required": ["path_a", "path_b"],
        },
        handler=folder_diff,
    )
    registry.register(
        name="strip_image_metadata",
        description="Save a metadata-free COPY of an image: EXIF (including GPS), ICC colour profile and embedded text chunks are removed by rebuilding the file from its raw pixels, so nothing hidden rides along. The EXIF orientation is baked into the pixels first, so the copy displays the same way up. The original is never touched; the default output is '<name>_clean.<ext>' beside it, and a taken name gets a ' (2)'-style suffix, reported in the result. The result summarises what was removed as counts and presence only (e.g. 'EXIF block (14 tags, incl. GPS)') — metadata values are never reported, and the saved copy is re-read and verified metadata-free before the result is reported. Use before sharing a photo when its location/camera data should not travel with it.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file"},
                "output": {"type": "string", "description": "Optional full path for the cleaned copy (default: '<name>_clean.<ext>' beside the input)"},
            },
            "required": ["path"],
        },
        handler=strip_image_metadata,
    )
    registry.register(
        name="pdf_info",
        description="Report a PDF's facts: PDF version (from the file header), file size, page count, the first page's size in millimetres (and points), and the document metadata — title, author, subject, keywords, creator, producer, and creation/modification dates — when present, plus whether the file is encrypted. An encrypted PDF reports its version and size only: the rest stays shut without the password, and the result says so. Read-only. Use when the user asks how many pages a PDF has, how big its pages are, or who created it.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the PDF file"}
            },
            "required": ["path"],
        },
        handler=pdf_info,
    )
    registry.register(
        name="rotate_pdf",
        description="Rotate pages of a PDF by 90, 180 or 270 degrees, saving the result as a NEW file ('<name>_rotated.pdf' beside the input by default; never overwritten — a ' (2)'-style suffix is used and reported). The pages spec is 'all' or 1-based pages/ranges like '1-3,5', checked against the document's real page count BEFORE anything is written — an out-of-range page refuses the rotation naming the count. Pages outside the spec pass through untouched, and the saved file is re-read and its rotations verified before the result is reported. Use when a scanned or exported PDF has pages facing the wrong way.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the PDF file"},
                "degrees": {
                    "type": "integer",
                    "enum": [90, 180, 270],
                    "description": "Clockwise rotation in degrees (default 90)",
                },
                "pages": {"type": "string", "description": "'all' (default) or the pages to rotate, 1-based: '1-3,5' style"},
                "output": {"type": "string", "description": "Optional full path for the rotated PDF (default: '<name>_rotated.pdf' beside the input)"},
            },
            "required": ["path"],
        },
        handler=rotate_pdf,
    )

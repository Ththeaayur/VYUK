"""
Files & Media III skills pack for VYUK (v5.30) — the file
jobs the earlier packs left on the table: a folder "cook" that runs
one named tidy-up recipe, blurring faces out of a photo before it
is shared, turning a screenshot into a dated text note, merging
PDFs with a bookmark per source, splitting a PDF wherever a marker
phrase appears, a labelled contact sheet of a folder's photos,
pitch-preserving speed changes and silence trimming for WAVs, a
perceptual-hash near-duplicate photo finder, and an age report
that names the files rotting in a folder.

Same rules as the packs before it (core/skills_files_inspect.py,
core/skills_files_media_ops.py): plain functions returning human-readable
strings, never raising; dependencies (PIL, pypdf, numpy, and —
only for face detection — OpenCV) are imported lazily inside the
function that needs them and guarded, so this module imports
cleanly on any OS. Nothing here ever overwrites an original: an
output name that is taken gets a " (2)"-style suffix and the
result reports the real name. Everything that writes is verified
after the fact (outputs re-opened, pages counted, durations
measured from the files on disk) before the result claims it.

Deliberately NOT here: plain PDF merging is merge_pdfs
(skills_files_ops), page-range splitting is split_pdf
(skills_media_convert), exact-duplicate finding is find_duplicates
(skills_system) — this pack's photo finder is the VISUAL
near-duplicate cut of the same idea, and its PDF tools add the
bookmark/marker behaviour the older ones don't have.

Audio scope is honest: WAV only (stdlib wave + numpy), speed
changes use a basic WSOLA stretch — pitch is preserved
approximately, quality is "good enough for speech", never
studio-grade, and the result says so.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""


# INTENTIONAL: re-exports of the old module's import bindings, so
# the facade's namespace matches the old module's exactly
# (dir-diff-empty bar, §2 P2).
import math
import os
import re
import wave
import zipfile
from datetime import datetime
from pathlib import Path

# INTENTIONAL: re-exports of every name moved into the impl modules.
from .skills_files5_helpers import (
    _IMAGE_EXTS,
    _COOK_SCAN_CAP,
    _SHEET_CAP,
    _SPLIT_PART_CAP,
    _PHOTO_SCAN_CAP,
    _AGE_LIST_CAP,
    _human_size,
    _existing_file,
    _existing_folder,
    _non_clobbering,
    _suffix_note,
    _load_pil,
    _load_pypdf,
    _load_numpy,
    _load_cv2,
    _fmt_seconds,
    _list_images,
    _safe_stem,
)
from .skills_files5_watch_blur_notes import (
    _RECIPES,
    _DATE_PREFIX,
    _WATCHES,
    _cook_rename_by_date,
    _cook_move_pdfs,
    _cook_zip_folder,
    _COOKERS,
    _run_recipe,
    folder_watch_cook,
    _face_cascade,
    _detect_faces,
    _pixelate,
    photo_face_blur,
    _ocr_image_text,
    screenshot_to_text_note,
)
from .skills_files5_pdf import (
    _count_outline_items,
    pdf_merge_bookmarks,
    _following_line,
    pdf_split_by_text,
    image_contact_sheet,
)
from .skills_files5_audio_dup_age import (
    _WAV_DTYPES,
    _WAV_FULL_SCALE,
    _AUDIO_MAX_SECONDS,
    _read_wav,
    _write_wav,
    _wav_target,
    _wav_duration,
    _wsola_stretch,
    audio_speed_change,
    silence_trim,
    _AHASH_BITS,
    _AHASH_MAX_DISTANCE,
    _average_hash,
    duplicate_photo_finder,
    file_age_report,
)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Files & Media III pack tool on a ToolRegistry-like object."""
    registry.register(
        name="folder_watch_cook",
        description="Run or remember a folder tidy-up recipe: rename_by_date prefixes each file with its modified date, move_pdfs files PDFs into a 'PDFs' subfolder, zip_folder archives the folder's files into one zip inside it. Actions: arm (remember folder+recipe for this session only — no background daemon), disarm, status, run_once (cook now, reporting exactly what changed on disk).",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["arm", "disarm", "status", "run_once"], "description": "'arm', 'disarm', 'status' (default) or 'run_once'"},
                "folder": {"type": "string", "description": "Full path of the folder (not needed for status)"},
                "recipe": {"type": "string", "description": "One of: rename_by_date, move_pdfs, zip_folder"},
            },
            "required": ["action"],
        },
        handler=folder_watch_cook,
    )
    registry.register(
        name="photo_face_blur",
        description="Blur the faces in a photo before sharing it: OpenCV's Haar detector finds front-facing faces and each one is pixelated into a mosaic. Always saves a NEW file ('<name>_faces_blurred', or the output_path given) and never touches the original; reports how many faces were blurred. Needs the optional OpenCV package — without it, or when no face is found, it says so plainly.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the photo file"},
                "output_path": {"type": "string", "description": "Optional full path for the blurred copy (default: '<name>_faces_blurred' beside the original)"},
            },
            "required": ["path"],
        },
        handler=photo_face_blur,
    )
    registry.register(
        name="screenshot_to_text_note",
        description="Read the text out of a screenshot image and save it as a dated text note ('<name>_text_YYYY-MM-DD.txt') beside the image, reporting the note's path and how the text starts. Prefers her bundled local Granite-Docling structured read when its weights are present, otherwise her bundled text-reading engine (Tesseract OCR) — without either, it says so plainly. To read a screenshot's text without saving a note, ocr_file does that.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the screenshot image file"},
            },
            "required": ["path"],
        },
        handler=screenshot_to_text_note,
    )
    registry.register(
        name="pdf_merge_bookmarks",
        description="Merge two or more PDFs into one — paths given as ONE semicolon-separated list, in the order they should appear — adding a PDF bookmark named after each source file at the page where it starts (plain merge_pdfs adds none). The merged file is re-opened afterwards to verify its page count and bookmarks before success is reported. Never overwrites an existing file.",
        parameters={
            "type": "object",
            "properties": {
                "paths": {"type": "string", "description": "The PDF paths as one semicolon-separated list, e.g. 'C:\\\\docs\\\\a.pdf; C:\\\\docs\\\\b.pdf'"},
                "output_path": {"type": "string", "description": "Optional full path for the merged PDF (default: 'merged_bookmarked.pdf' beside the first source)"},
            },
            "required": ["paths"],
        },
        handler=pdf_merge_bookmarks,
    )
    registry.register(
        name="pdf_split_by_text",
        description="Split a PDF wherever a page's text contains a marker phrase (e.g. 'Invoice'): a new part starts at each such page and is named after the line following the marker. Pages before the first marker become a '<name>_before_marker' part. Every part written is re-opened and page-counted. Matching is exact-case and needs extractable text — scanned-image pages carry none.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the PDF file"},
                "marker": {"type": "string", "description": "The phrase that starts a new part wherever a page contains it"},
                "output_folder": {"type": "string", "description": "Optional folder for the parts (default: a '<name>_split' folder beside the PDF)"},
            },
            "required": ["path", "marker"],
        },
        handler=pdf_split_by_text,
    )
    registry.register(
        name="image_contact_sheet",
        description="Build a contact sheet of a folder's photos: one JPEG grid ('contact_sheet.jpg' in the folder, never overwriting) with each photo labelled by its filename and date underneath. Up to 30 photos in name order; columns=0 picks a near-square grid automatically. Files that can't be read are skipped and named. Use make_collage instead for a decorative cropped collage.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Full path of the folder holding the photos"},
                "columns": {"type": "integer", "description": "Grid columns (default 0 = automatic near-square)"},
            },
            "required": ["folder"],
        },
        handler=image_contact_sheet,
    )
    registry.register(
        name="audio_speed_change",
        description="Change a WAV recording's speed (factor 0.5 = half speed, up to 2.0 = double) with a basic WSOLA stretch, so voices keep roughly their pitch instead of going squeaky or boomy. Saves a new file and reports both durations measured from the files on disk. WAV only, up to 10 minutes per file; quality is honest speech-grade, not studio-grade. The original is never modified.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the WAV file"},
                "factor": {"type": "number", "description": "Speed factor 0.5–2.0 (default 1.5)"},
                "output_path": {"type": "string", "description": "Optional full path for the new WAV (default: '<name>_x<factor>.wav' beside the input)"},
            },
            "required": ["path"],
        },
        handler=audio_speed_change,
    )
    registry.register(
        name="silence_trim",
        description="Trim the long silences out of a WAV recording: stretches quieter than threshold_db (default -40 dB) and longer than min_silence_s (default 1 second) are cut down to 0.25 s each. Saves a new file and reports how many seconds were removed, with both durations measured from the files on disk. WAV only; the original is never modified.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the WAV file"},
                "output_path": {"type": "string", "description": "Optional full path for the trimmed WAV (default: '<name>_silence_trimmed.wav' beside the input)"},
                "threshold_db": {"type": "number", "description": "Quiet cut-off in dB, -80 to 0 (default -40)"},
                "min_silence_s": {"type": "number", "description": "Only silences longer than this, in seconds, are cut (default 1.0)"},
            },
            "required": ["path"],
        },
        handler=silence_trim,
    )
    registry.register(
        name="duplicate_photo_finder",
        description="Find photos that LOOK the same in a folder (subfolders included): a perceptual 8x8 average hash per image groups pictures within 5 of 64 hash bits — re-saves, resizes and near-identical shots land together. Read-only: deletes and moves nothing; each group is listed with its files and combined size. For exact byte-identical files use find_duplicates instead.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Full path of the folder to search for look-alike photos"},
            },
            "required": ["folder"],
        },
        handler=duplicate_photo_finder,
    )
    registry.register(
        name="file_age_report",
        description="Report what's rotting in a folder (Downloads when no folder is given): the 15 oldest files by last-modified time, each with its size and how long it has sat untouched, plus a total for everything untouched for 90+ days. Counts files directly in the folder only, not subfolders. Read-only — a clean-up shortlist; nothing is deleted or moved.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Full path of the folder (default: the user's Downloads folder)"},
            },
            "required": [],
        },
        handler=file_age_report,
    )

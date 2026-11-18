"""
Files & Media II skills pack for VYUK (v5.5) — the file jobs the
first media pack never covered: merging PDFs into one document,
reading an image's real facts (format, oriented size, EXIF
highlights), securely shredding a single file, one-way folder
sync, and putting a FILE (not text) on the clipboard.

Same rules as core/skills_plus.py and core/skills_media.py: plain
functions returning human-readable strings, never raising;
platform-specific dependencies (PIL, pypdf, ctypes.windll) are
imported lazily inside the function that needs them and guarded,
so this module imports cleanly on any OS. Nothing here ever
overwrites silently: a merge whose output name is taken writes a
" (2)"-suffixed file instead, a sync leaves destination extras
untouched unless mirror_delete=true says otherwise, and the one
deliberately destructive tool (shred_file) overwrites, truncates
and unlinks only the single file it was aimed at.

Wiring: the parent registers this pack via register_all(registry)
— this module never touches the registry singleton itself.
"""


# INTENTIONAL (skills split, dir-diff-empty bar): the old module's namespace carried its stdlib imports, so the facade re-exports them rather than dropping them.
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
# INTENTIONAL (skills split): re-exports of every old top-level name, so importers keep working unchanged.
from .skills_files2_helpers import (
    _is_windows,
    _human_size,
    _existing_file,
    _non_clobbering,
    _load_pypdf,
    _load_pil,
)
from .skills_files2_pdf_files import (
    _parse_pdf_paths,
    merge_pdfs,
    image_info,
    _SHRED_MAX_BYTES,
    _SHRED_CHUNK,
    shred_file,
    _SYNC_MAX_FILES,
    sync_folder,
    _CF_HDROP,
    _DROPFILES_HEADER_SIZE,
    _dropfiles_blob,
    _set_clipboard_files_windows,
    _file_uri,
    copy_file_to_clipboard,
)

from .photo_organise import organise_photos, preview_photo_organisation
from .two_way_sync import sync_folders_two_way, two_way_sync_status


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Files & Media II pack tool on a ToolRegistry-like object."""
    registry.register(
        name="merge_pdfs",
        description="Merge two or more PDF files into ONE PDF, in the order given. The result goes beside the first file as 'merged.pdf' unless an output path is given; an existing output is NEVER overwritten — a ' (2)'-style suffix is used and reported. Every input is checked readable before anything is written, so a bad input aborts the merge naming that file, and the finished file is re-opened and page-checked before the result is reported. Use when the user wants several PDFs combined into one document.",
        parameters={
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The PDF files to merge, full paths, in the order they should appear (at least 2)",
                },
                "output": {"type": "string", "description": "Optional full path for the merged PDF (default: 'merged.pdf' beside the first input)"},
            },
            "required": ["paths"],
        },
        handler=merge_pdfs,
    )
    registry.register(
        name="image_info",
        description="Report an image file's facts: format, pixel size AS DISPLAYED (EXIF orientation honoured), colour mode, size on disk, and EXIF highlights when present — camera model, date taken, exposure settings (shutter, aperture, ISO, focal length) when recorded, and whether GPS data is present (coordinates are never reported). Use when the user asks about a photo's size, dimensions or camera details. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file"}
            },
            "required": ["path"],
        },
        handler=image_info,
    )
    registry.register(
        name="shred_file",
        description="PERMANENTLY destroy ONE file: overwrite its full contents with random data (1–3 passes), truncate it to zero and delete it, so normal undelete tools can't bring it back. Best-effort on SSDs — wear-levelling can leave copies in unallocated cells that no overwrite can reach. Folders and files over 2 GB are refused. Use only when the user explicitly wants a file gone for good.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the single file to shred"},
                "passes": {"type": "integer", "description": "Overwrite passes, 1–3 (default 1)"},
            },
            "required": ["path"],
        },
        handler=shred_file,
    )
    registry.register(
        name="sync_folder",
        description="One-way folder sync: copy files that are new or changed from the source folder into the destination folder (folders are created as needed), so the destination matches the source. Destination files that aren't in the source are only REPORTED by default — they are deleted only when mirror_delete=true. Symlinks are skipped. Pass dry_run=true to preview what would be copied (and, with mirror_delete, deleted) without changing anything on disk. Use for backups and keeping two folders in step.",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Full path of the folder to copy FROM"},
                "destination": {"type": "string", "description": "Full path of the folder to copy TO (created if missing)"},
                "mirror_delete": {"type": "boolean", "description": "true = also delete destination files the source doesn't have (default false: extras are reported, never touched)"},
                "dry_run": {"type": "boolean", "description": "true = preview only: report what would be copied/deleted and change nothing on disk (default false)"},
            },
            "required": ["source", "destination"],
        },
        handler=sync_folder,
    )
    registry.register(
        name="sync_folders_two_way",
        description="Two-way folder sync: keep TWO local folders in step with each other — files new or changed on one side are copied to the other, so either side can be edited. A file changed on BOTH sides since the last sync, or changed on one side while deleted on the other, is a CONFLICT: it is reported by name and left exactly as it was on both sides, never silently overwritten. Files deleted on one side are only mirror-deleted on the other when mirror_delete=true (default false: reported, never touched), and a mirror-deleted file is rescued into the central trash first. Whole-file copy, no delta transfer, no watching, local folders only. Pass dry_run=true to preview what would be copied, deleted, or conflicted without changing anything. Use when the user wants two folders to stay identical from either side.",
        parameters={
            "type": "object",
            "properties": {
                "folder_a": {"type": "string", "description": "Full path of the first folder"},
                "folder_b": {"type": "string", "description": "Full path of the second folder"},
                "mirror_delete": {"type": "boolean", "description": "true = also delete files on each side that the other side deleted (default false: deletions are reported, never touched)"},
                "dry_run": {"type": "boolean", "description": "true = preview only: report what would be copied/deleted/conflicted and change nothing on disk (default false)"},
            },
            "required": ["folder_a", "folder_b"],
        },
        handler=sync_folders_two_way,
    )
    registry.register(
        name="two_way_sync_status",
        description="List every known two-way sync pair and its last run: the two folders, how many files are in step, when it last synced, and which conflicts are still open. Pure read — it never touches the synced folders themselves. Read-only.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=two_way_sync_status,
    )
    registry.register(
        name="organise_photos",
        description="Sort a camera dump into dated, location-aware folders (elodie's scheme, own code — elodie itself is NOT adopted: its wheel pins 2017-2018 versions that conflict with this tree). Photos move to <destination>/<YYYY>-<MM>-<Mon>/<location>/<YYYY>-<MM>-<DD>_<HH>-<MM>-<SS>-<name>.<ext>, dated by EXIF DateTimeOriginal (file date when no EXIF), with a coarse GPS bucket (e.g. GPS-19.1N-72.9E, about 11 km) as the location folder and 'Unknown Location' when there is no GPS. Videos are left untouched (no video metadata reader). Never overwrites — a taken name gets a ' (2)'-style suffix — and every move is verified by byte comparison. Pass dry_run=true (default) to preview the plan without moving anything. Use when the user wants a folder of unsorted photos filed by date and place.",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Full path of the folder holding the unsorted photos"},
                "destination": {"type": "string", "description": "Full path of the folder the organised photos go into (created when missing; must sit outside the source)"},
                "dry_run": {"type": "boolean", "description": "true = preview only: report what would move where and change nothing on disk (default true)"},
            },
            "required": ["source", "destination"],
        },
        handler=organise_photos,
    )
    registry.register(
        name="preview_photo_organisation",
        description="Show exactly what organising a photo folder would do — which photos would move where — without moving, creating or writing anything. Pure read — the dry-run plan itself. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Full path of the folder holding the unsorted photos"},
                "destination": {"type": "string", "description": "Full path of the folder the organised photos would go into"},
            },
            "required": ["source", "destination"],
        },
        handler=preview_photo_organisation,
    )
    registry.register(
        name="copy_file_to_clipboard",
        description="Put a FILE on the clipboard (not its text) so it can be pasted into a folder in Explorer or a file manager, exactly like Ctrl+C on the file. Use when the user wants to copy a file ready to paste somewhere. For putting TEXT on the clipboard instead, write_clipboard.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the file to put on the clipboard"}
            },
            "required": ["path"],
        },
        handler=copy_file_to_clipboard,
    )

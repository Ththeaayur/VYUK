"""
Files & Media skills pack for VYUK (v2.8) — image work (convert /
resize / compress / OCR a file), file utilities (streaming hash,
recent files, folder size, batch rename, shortcuts), targeted
screenshots (a screen region — one-window captures live in
capture_window, which absorbed screenshot_window in v5.30), and
audio skills the master-volume tools never covered (record a clip,
list audio devices, mute the microphone, per-app volume).

Same rules as core/skills_plus.py and core/desktop/control_plus.py: plain
functions returning human-readable strings, never raising;
platform-specific dependencies (PIL, pytesseract, sounddevice,
pycaw, ctypes.windll) are imported lazily inside the function that
needs them and guarded, so this module imports cleanly on any OS —
Windows-only skills return a clear fallback string elsewhere.
Batch rename is the destructive one: it previews first and only
renames when called again with confirm=true, and name collisions
are never overwritten.

Wiring: the parent registers this pack via register_all(registry) —
this module never touches the registry singleton itself.
"""

# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: the old module's
# namespace carried its stdlib imports; they are re-exported here
# (dir-diff-empty bar, plan SS2 P2) rather than dropped.
import hashlib
import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
# INTENTIONAL (media split): implementations live in the impl modules;
# the facade re-exports every name another module imports from it.
from .skills_media_helpers import (
    _DATA_DIR,
    _RECORD_RATE,
    _is_windows,
    _human_size,
    _truncate,
    _NOT_WINDOWS,
    _IMAGE_FORMATS,
    _existing_file,
    _load_pil,
    _apply_exif_orientation,
    _save_screenshot,
)
from .skills_media_image import (
    convert_image,
    resize_image,
    compress_image,
    ocr_file,
)
from .skills_media_files import (
    _FOLDER_SKIP_DIRS,
    _FOLDER_MAX_DEPTH,
    file_hash,
    recent_files,
    folder_size,
    _RENAME_CAP,
    batch_rename,
)
from .skills_media_capture import (
    create_shortcut,
    screenshot_region,
)
from .skills_media_audio import (
    record_audio,
    list_audio_devices,
    _microphone_volume,
    set_microphone_mute,
    _audio_sessions,
    _session_label,
    app_volume,
)


def register_all(registry) -> None:
    """Register every Files & Media pack tool on a ToolRegistry-like object."""
    registry.register(
        name="convert_image",
        description="Convert an image file to another format (png, jpg, webp or bmp). The converted copy is saved beside the original, which is never overwritten. Use when the user asks to change an image's format.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image to convert"},
                "out_format": {"type": "string", "description": "Target format: 'png', 'jpg', 'webp' or 'bmp'"},
            },
            "required": ["path", "out_format"],
        },
        handler=convert_image,
    )
    registry.register(
        name="resize_image",
        description="Resize an image to a new width and/or height in pixels, saving a '<name>_resized' copy beside the original. By default the aspect ratio is kept; pass keep_aspect=false with both dimensions for an exact stretch.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image to resize"},
                "width": {"type": "integer", "description": "Target width in pixels (omit or 0 to scale by height only)"},
                "height": {"type": "integer", "description": "Target height in pixels (omit or 0 to scale by width only)"},
                "keep_aspect": {"type": "boolean", "description": "Keep the original proportions (default true)"},
            },
            "required": ["path"],
        },
        handler=resize_image,
    )
    registry.register(
        name="compress_image",
        description="Make an image file smaller by re-encoding it as a JPEG at the given quality (default 70), saving a '<name>_compressed.jpg' beside the original. Reports how much space was saved. target_kb instead asks for a maximum size and steps the quality down until the file fits (or says honestly when it can't).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image to compress"},
                "quality": {"type": "integer", "description": "JPEG quality 5–95 (default 70); lower means smaller and rougher"},
                "target_kb": {"type": "integer", "description": "Optional maximum size in KB (0 = no target, default); quality steps down from the requested quality until the JPEG fits"},
            },
            "required": ["path"],
        },
        handler=compress_image,
    )
    registry.register(
        name="ocr_file",
        description="Read the text inside an image FILE (a screenshot, scan or photo of text) using OCR, and return the words. For text on the live screen instead, use ocr_screen. For a PDF's text layer instead of an image, read_pdf_text.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to read"}
            },
            "required": ["path"],
        },
        handler=ocr_file,
    )
    registry.register(
        name="file_hash",
        description="Compute a file's fingerprint hash (md5, sha1 or sha256 — default sha256). Use to check a file hasn't changed or to compare a download against a published checksum. A matching hash only proves a file is unchanged — it says nothing about whether the file is safe.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the file to hash"},
                "algorithm": {"type": "string", "description": "'md5', 'sha1' or 'sha256' (default)"},
            },
            "required": ["path"],
        },
        handler=file_hash,
    )
    registry.register(
        name="recent_files",
        description="List the files the user recently opened, newest first, from the Windows Recent list. Use when the user asks what they were working on or can't find a file they just had open. It reads the Windows Recent list rather than scanning folders — for a folder's contents, file_control; to find a file by name anywhere, find_files.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many to show (default 15, max 50)"}
            },
            "required": [],
        },
        handler=recent_files,
    )
    registry.register(
        name="screenshot_region",
        description="Take a screenshot of just one rectangular part of the screen (given by its top-left corner x, y and its width and height in pixels) and save it to a file. Use take_screenshot instead for the whole screen.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Left edge of the region, in pixels from the screen's left"},
                "y": {"type": "integer", "description": "Top edge of the region, in pixels from the screen's top"},
                "width": {"type": "integer", "description": "Width of the region in pixels"},
                "height": {"type": "integer", "description": "Height of the region in pixels"},
            },
            "required": ["x", "y", "width", "height"],
        },
        handler=screenshot_region,
    )
    registry.register(
        name="batch_rename",
        description="Rename many files in one folder at once by replacing text in their names (e.g. find 'IMG_' replace 'Holiday-'). First call WITHOUT confirm to preview the plan — nothing is renamed; call again with confirm=true to do it. Files whose new name would collide are skipped, never overwritten. Only files directly in the folder; extensions change only when the find text matches inside them.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Full path of the folder whose files to rename"},
                "find": {"type": "string", "description": "Text to find in the file names"},
                "replace": {"type": "string", "description": "Text to replace it with (may be empty to just remove the found text)"},
                "confirm": {"type": "boolean", "description": "Must be true to actually rename; false (default) only previews"},
            },
            "required": ["folder", "find", "replace"],
        },
        handler=batch_rename,
    )
    registry.register(
        name="create_shortcut",
        description="Create a Windows shortcut (.lnk) to a file or folder, on the Desktop (default), in the Start Menu, or in a given folder. Use when the user wants quick access to something. To open the target right away instead of shortcutting it, open_path.",
        parameters={
            "type": "object",
            "properties": {
                "target_path": {"type": "string", "description": "Full path of the file or folder the shortcut points to"},
                "location": {"type": "string", "description": "'desktop' (default), 'start_menu', or a folder path"},
                "name": {"type": "string", "description": "Optional shortcut label (default: the target's name)"},
            },
            "required": ["target_path"],
        },
        handler=create_shortcut,
    )
    registry.register(
        name="folder_size",
        description="Measure how much space ONE folder takes up in total, how many files it holds, and which of its subfolders are the biggest. With by_extension=true it also breaks the total down by file type (the top 8 extensions by size, with counts). Use for a folder's total; for a per-entry breakdown of where the bytes live use folder_size_map, and for the biggest individual files anywhere use largest_files.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the folder to measure"},
                "top": {"type": "integer", "description": "How many of the biggest subfolders to list (default 5, max 20)"},
                "by_extension": {"type": "boolean", "description": "Also break the total down by file type (extension)"}
            },
            "required": ["path"],
        },
        handler=folder_size,
    )
    registry.register(
        name="record_audio",
        description="Record the microphone for the given number of seconds (1–120) and save the clip as a .wav file in VYUK's recordings folder. This call WAITS until the recording is finished, so expect it to take that many seconds. Use when the user asks to record a voice note or a sound.",
        parameters={
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "How long to record, in seconds (max 120)"},
                "filename": {"type": "string", "description": "Optional file name for the clip (default: recording with a timestamp)"},
            },
            "required": ["seconds"],
        },
        handler=record_audio,
    )
    registry.register(
        name="list_audio_devices",
        description="List the PC's microphones and speakers (audio input/output devices) and which ones are the defaults. Use when the user is troubleshooting sound or setting up a new headset. It only reports — per-app volumes are app_volume, and the speaker level is set_volume_level.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=list_audio_devices,
    )
    registry.register(
        name="set_microphone_mute",
        description="Mute or unmute the PC's microphone itself (the input device — use app_volume for one app's sound, set_volume_level for the speakers). state: 'toggle' (default), 'on'/'mute' or 'off'/'unmute'.",
        parameters={
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "'toggle' (default), 'on'/'mute', or 'off'/'unmute'"}
            },
            "required": [],
        },
        handler=set_microphone_mute,
    )
    registry.register(
        name="app_volume",
        description="Control ONE app's volume instead of the whole PC's: call with no app name to list every app that can make sound and its level; call with an app's process name (e.g. 'chrome', 'spotify') to read it, or pass level (0–100) and/or mute to change just that app. For everything at once use set_volume_level. For master-volume steps use adjust_volume; media keys (play/pause, next track) are media_control.",
        parameters={
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "The app's process name, e.g. 'chrome' or 'spotify'; omit or '*' to list all apps"},
                "level": {"type": "integer", "description": "New volume for that app, 0–100 (omit to leave unchanged)"},
                "mute": {"type": "boolean", "description": "true to mute that app, false to unmute (omit to leave unchanged)"},
            },
            "required": [],
        },
        handler=app_volume,
    )

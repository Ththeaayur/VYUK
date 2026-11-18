"""
Skills pack for VYUK (v2.7) — the everyday gaps the control packs
left behind: installed-apps inventory + uninstall, printing, reading
PDFs aloud-ready text, speak-on-demand, drive eject, Windows Focus
Assist (do-not-disturb), a friendlier cross-folder file search, and
monitor listing / window-to-monitor moves.

Same rules as core/control.py and core/desktop/control_plus.py: plain
functions returning human-readable strings; every platform-specific
dependency (winreg, pypdf, pyttsx3, ctypes.windll) is imported lazily
inside the function that needs it and guarded with try/except, so
this module imports cleanly on any OS — including headless Linux,
where Windows-only skills simply return a clear fallback string
instead of crashing. Destructive skills are confirm-gated and never
fake a success the OS did not confirm (uninstall launches the app's
own registered uninstaller — silent when Windows has a quiet string
for it, interactive otherwise, and the result says which).

Wiring: core/funnel/tools_registry.py calls register_all(registry) from
_register_skills_tools() — this module never touches the registry
singleton itself.
"""

# INTENTIONAL: the old module's namespace carried these stdlib imports;
# re-exporting them keeps the facade's namespace identical to the old
# module's (plan section 2 P2 dir-diff-empty bar).
import fnmatch
import os
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

# INTENTIONAL: re-export of every name moved into skills_plus_helpers.
from .skills_plus_helpers import _NOT_WINDOWS, _human_size, _is_windows
# INTENTIONAL: re-export of every name moved into skills_plus_apps_files.
from .skills_plus_apps_files import _FIND_EXPLICIT_MAX_DEPTH, _FIND_MAX_DEPTH, _FIND_ROOTS, _FIND_SKIP_DIRS, _UNINSTALL_KEYS, _UNINSTALL_META_OUTSIDE_QUOTES, _UNINSTALL_SHELL_EXECUTABLES, _installed_apps, _strip_quoted_spans, find_files, list_installed_apps, parse_uninstall_command, preview_uninstall_target, print_file, read_pdf_text, uninstall_app, validate_uninstall_target
# INTENTIONAL: re-export of every name moved into skills_plus_voice_focus.
from .skills_plus_voice_focus import _FOCUS_KEY, _FOCUS_VALUE, _speak_worker, set_focus_assist, speak_text
# INTENTIONAL: re-export of every name moved into skills_plus_devices.
from .skills_plus_devices import _monitor_rects, eject_drive, monitor_control

# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Skills pack tool on a ToolRegistry-like object."""
    registry.register(
        name="list_installed_apps",
        description="List the apps winget lists as installed on this PC (name, version, package id). Optionally filter by part of an app's name. Use when the user asks what's installed, or before uninstalling something. To remove one, uninstall_app; to see which have updates waiting, list_app_updates.",
        parameters={
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Optional part of an app name to filter by, e.g. 'chrome'"},
                "limit": {"type": "integer", "description": "How many to list (default 30, max 200); the result always states the true total"}
            },
            "required": [],
        },
        handler=list_installed_apps,
    )
    registry.register(
        name="uninstall_app",
        description="Uninstall an app by name via winget — the removal is verified against winget's installed list (an app winget doesn't manage falls back to the uninstaller Windows has on record for it). This removes software from the PC, so it requires confirm=true.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The app's name as shown by list_installed_apps, e.g. 'Zoom'"},
                "confirm": {"type": "boolean", "description": "Must be true to uninstall"},
            },
            "required": ["name"],
        },
        handler=uninstall_app,
    )
    registry.register(
        name="print_file",
        description="Print a file on the default printer (documents, PDFs, images — whatever Windows can print for that file type). Check or change the printer first with list_printers and set_default_printer.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the file to print"}
            },
            "required": ["path"],
        },
        handler=print_file,
    )
    registry.register(
        name="read_pdf_text",
        description="Read the words out of a PDF file and return them as text (first pages only; scanned-image PDFs have no readable text). Use when the user asks what's in a PDF, or wants it summarised. Pass start_page to begin at a later page (1-based) — handy for reading a long PDF in chunks with max_pages.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the PDF file"},
                "max_pages": {"type": "integer", "description": "How many pages to read from the start (default 10, max 50)"},
                "start_page": {"type": "integer", "description": "Page to start reading at, 1-based (default 1)"},
            },
            "required": ["path"],
        },
        handler=read_pdf_text,
    )
    registry.register(
        name="speak_text",
        description="Read a piece of text aloud right now in the PC's built-in system voice (not VYUK's own voice). Use when the user asks to hear a specific passage, document, or message read out. Up to 1000 characters per call.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The exact words to read aloud"}
            },
            "required": ["text"],
        },
        handler=speak_text,
    )
    registry.register(
        name="eject_drive",
        description="Safely eject a removable drive (USB stick, external drive, memory card) so it can be unplugged without corrupting files. Give the drive letter, like 'E'. See the drives and their letters with drive_info.",
        parameters={
            "type": "object",
            "properties": {
                "drive": {"type": "string", "description": "Drive letter, e.g. 'E' or 'F:'"}
            },
            "required": ["drive"],
        },
        handler=eject_drive,
    )
    registry.register(
        name="set_focus_assist",
        description="Turn Windows Focus Assist (do-not-disturb) on or off. When on, pop-up notifications are muted; alarms still get through. This is Windows' own muting — VYUK's spoken notices are controlled in Settings, under Notices.",
        parameters={
            "type": "object",
            "properties": {
                "on": {"type": "boolean", "description": "true = mute notifications, false = let them show again"}
            },
            "required": ["on"],
        },
        handler=set_focus_assist,
    )
    registry.register(
        name="find_files",
        description="Search for files by name across the usual places (home folder, Desktop, Documents, Downloads) or inside a chosen folder, and return their full paths and sizes. Plain words match part of a name; '*' patterns work too. Optional min_size_kb / max_size_kb keep only files in a size range (the result says how many matches the filter dropped), and newest_first sorts by modification time instead of name.",
        parameters={
            "type": "object",
            "properties": {
                "name_pattern": {"type": "string", "description": "Part of a file name, or a pattern like '*.pdf'"},
                "root": {"type": "string", "description": "Where to search: 'home' (default), 'desktop', 'documents', 'downloads', or a folder path"},
                "limit": {"type": "integer", "description": "Maximum results (default 20, max 50)"},
                "min_size_kb": {"type": "integer", "description": "Only keep files at least this many KB (0 = no minimum)"},
                "max_size_kb": {"type": "integer", "description": "Only keep files at most this many KB (0 = no maximum)"},
                "newest_first": {"type": "boolean", "description": "Sort results newest-first by modification time (default: by name)"},
            },
            "required": ["name_pattern"],
        },
        handler=find_files,
    )
    registry.register(
        name="monitor_control",
        description="Work with multiple screens: 'list' shows every monitor with its size and position; 'move_window' moves a window (matched by part of its title) onto the monitor with the given number from the list. To move a window onto a monitor by name, move_window_to_monitor.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'list' or 'move_window'"},
                "title_substring": {"type": "string", "description": "For move_window: part of the window's title, e.g. 'Chrome'"},
                "monitor_index": {"type": "integer", "description": "For move_window: the monitor number from 'list', e.g. 2"},
            },
            "required": ["action"],
        },
        handler=monitor_control,
    )

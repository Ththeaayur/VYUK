"""
System & Apps skills II for VYUK (v5.4, Pack B) — the second deep
Windows-management pack: top processes by CPU/memory, full-text file
search, the Windows event log, restore points, scheduled tasks,
printers, saved VPN connections and the screen-off timeout.

Same rules as core/skills_system.py: plain functions returning
human-readable strings; every platform-specific dependency (psutil,
PowerShell, schtasks, rasdial, powercfg, winspool via ctypes) is
reached lazily and guarded, so this module imports cleanly on any
OS — on non-Windows systems the Windows-only skills return a clear
fallback string instead of crashing. Nothing here fakes a success
the OS did not confirm: exit codes are checked, unparseable output
is reported as "couldn't read", and every cap (files scanned,
matches shown, tasks listed) is disclosed in the result text.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

# INTENTIONAL: re-exports of the old module's import bindings, so
# the facade's namespace matches the old module's exactly
# (dir-diff-empty bar, §2 P2).
import csv
import io
import os
import subprocess
import sys
import time
from pathlib import Path

# INTENTIONAL: re-exports of every name moved into the impl modules.
from .skills_system2_helpers import (
    _is_windows,
    _human_size,
    _NOT_WINDOWS,
    _run,
    _tail,
    _clamp_int,
    _SEP,
    _ps_structured,
)
from .skills_system2_processes import (
    top_processes,
    _format_percent,
    _SEARCH_DEFAULT_EXTS,
    _SEARCH_FILE_CAP,
    _SEARCH_MATCH_CAP,
    _SEARCH_SIZE_CAP,
    search_file_contents,
    _EVENT_LEVELS,
    _event_log_command,
    system_event_log,
)
from .disk_index import (
    disk_index_status,
    index_files,
    search_indexed_files,
)
from .skills_system2_devices import (
    _clean_restore_description,
    _newest_restore_point,
    create_restore_point,
    _query_task_status,
    _parse_task_rows,
    task_scheduler_control,
    _PRINTER_LIST_PS,
    _default_printer_name,
    list_printers,
    set_default_printer,
    vpn_control,
    _read_video_idle_seconds,
    _timeout_phrase,
    set_screen_timeout,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every System & Apps II pack tool on a ToolRegistry-like object."""
    registry.register(
        name="top_processes",
        description="List the processes using the most memory or CPU right now, heaviest first, each with its name, PID and figure — e.g. 'name (PID 1234) — 412 MB' or 'name (PID 1234) — 23% CPU'. CPU figures come from a short sampling look (a snapshot, not a trend); the memory view also totals what all readable processes hold between them. Use when the user asks what's slowing the PC down or what's eating memory. (This is the one process leaderboard — it absorbed the retired cpu_top and ram_top in v5.30.) Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "by": {"type": "string", "enum": ["cpu", "memory"],
                       "description": "Rank by current CPU use or by memory (default memory)"},
                "count": {"type": "integer",
                          "description": "How many to show, 1-20 (default 8)"},
            },
            "required": [],
        },
        handler=top_processes,
    )
    registry.register(
        name="search_file_contents",
        description="Search inside the user's text files for a word or phrase (case-insensitive) and return file:line matches with snippets. Defaults to the Documents folder and text-like extensions (.txt .md .py .log .json .csv .ini). Scans at most 600 files and returns at most 40 matches by default — pass max_matches (up to 200) to return more per search. The result always says how many files were scanned and if a cap stopped the search. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The text to look for"},
                "folder": {"type": "string", "description": "Folder to search (default: the user's Documents)"},
                "extensions": {"type": "string", "description": "Comma-separated extensions to include, e.g. '.txt,.md' (default: common text types)"},
                "max_matches": {"type": "integer", "description": "Maximum matches to return (default 40, max 200)"},
            },
            "required": ["query"],
        },
        handler=search_file_contents,
    )
    registry.register(
        name="index_files",
        description="Build or refresh a persistent, ranked search index over the text files in folders you choose (plain-text types: .txt .md .py .log .json .csv .ini). The index lives in VYUK's own data folder and is searched by search_indexed_files — it makes 'find the file that says X' fast instead of scanning every file each time. Indexing is incremental (only changed files are re-read); pass rebuild=true for a full re-index. Nothing is indexed until you name the folders, and nothing leaves the machine. Indexing also feeds document memory — text, Office and PDF files are stored as passages in the knowledge store, searchable by meaning with search_documents and listed with list_documents; images are skipped (OCR unavailable). Say 'remember this folder' to index a folder. Effectful (writes VYUK's own index).",
        parameters={
            "type": "object",
            "properties": {
                "folders": {"type": "string", "description": "Comma-separated folders to index, e.g. 'C:\\Users\\you\\Documents'"},
                "rebuild": {"type": "boolean", "description": "Rebuild the whole index from scratch instead of updating it (default false)"},
            },
            "required": ["folders"],
        },
        handler=index_files,
    )
    registry.register(
        name="search_indexed_files",
        description="Search the indexed files for words or a phrase and return the best matches first, ranked by relevance, with a snippet from each file. Only finds files that were indexed with index_files — if nothing is indexed yet it says so and points at the live scan (search_file_contents). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The text to look for"},
                "limit": {"type": "integer", "description": "How many matches to return, 1-50 (default 10)"},
            },
            "required": ["query"],
        },
        handler=search_indexed_files,
    )
    registry.register(
        name="disk_index_status",
        description="What the disk search index holds: the folders tracked, how many files are indexed, the index size, and when it was last built. Read-only.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=disk_index_status,
    )
    registry.register(
        name="system_event_log",
        description="Read recent entries from the Windows event log (System or Application channel), filtered to errors (critical+error), warnings, or all levels. Entries come back worst-first (critical before error before warning) with each entry's severity, time, source and message, and the header states the time window covered. Pass hours to only see entries from the last N hours. Use when the user asks why something crashed or whether Windows has been reporting problems. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["System", "Application"],
                            "description": "Which log to read (default System)"},
                "level": {"type": "string", "enum": ["errors", "warnings", "all"],
                          "description": "Severity filter (default errors)"},
                "count": {"type": "integer",
                          "description": "How many entries, 1-25 (default 10)"},
                "hours": {"type": "integer",
                          "description": "Only entries from the last N hours (default 0 = no time limit, max 720)"},
            },
            "required": [],
        },
        handler=system_event_log,
    )
    registry.register(
        name="create_restore_point",
        description="Create a Windows system restore point with the given description, so the user can roll the PC back to this moment. Windows requires an admin/elevated context and allows only one restore point per 24 hours; the result says plainly when either of those blocks it.",
        parameters={
            "type": "object",
            "properties": {
                "description": {"type": "string",
                                "description": "Label for the restore point (default 'VYUK restore point')"},
            },
            "required": [],
        },
        handler=create_restore_point,
    )
    registry.register(
        name="task_scheduler_control",
        description="Work with Windows scheduled tasks. action='list' shows the user's own tasks (built-in Microsoft tasks hidden, at most 30 shown with the total disclosed); pass include_microsoft=true to list the built-in Microsoft tasks too. action='run' starts a task now; 'enable'/'disable' switch a task's schedule on or off — those need the exact task name from the list. Some tasks need an admin context to change; the result surfaces Windows' own error when so.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["list", "run", "enable", "disable"],
                           "description": "What to do (default list)"},
                "name": {"type": "string",
                         "description": "Exact task name for run/enable/disable, e.g. '\\MyTask'"},
                "include_microsoft": {"type": "boolean",
                         "description": "For list: also show built-in Microsoft tasks (default false)"},
            },
            "required": [],
        },
        handler=task_scheduler_control,
    )
    registry.register(
        name="list_printers",
        description="List every printer installed on this PC, marking which one is the default. Read-only. Change the default with set_default_printer; send a document to a printer with print_file.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=list_printers,
    )
    registry.register(
        name="set_default_printer",
        description="Make an already-installed printer the Windows default printer. The name must match an installed printer exactly — use list_printers first if unsure. Print a document with print_file.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Exact installed printer name"},
            },
            "required": ["name"],
        },
        handler=set_default_printer,
    )
    registry.register(
        name="vpn_control",
        description="Check or change saved Windows VPN connections (rasdial). action='status' reports whether a VPN is connected and to what; 'connect' dials a saved connection by name (only works when its credentials are already saved — rasdial cannot prompt); 'disconnect' hangs up.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["status", "connect", "disconnect"],
                           "description": "What to do (default status)"},
                "name": {"type": "string",
                         "description": "Saved connection name for action='connect'"},
            },
            "required": [],
        },
        handler=vpn_control,
    )
    registry.register(
        name="set_screen_timeout",
        description="Set how many minutes before Windows turns the display off, on battery, when plugged in, or both. minutes=0 means never turn the screen off; the maximum is 600. For putting the whole PC to sleep, power_control.",
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer",
                            "description": "Minutes before the screen turns off, 0-600 (0 = never)"},
                "on": {"type": "string",
                       "enum": ["battery", "plugged", "both"],
                       "description": "Which power state to change (default both)"},
            },
            "required": ["minutes"],
        },
        handler=set_screen_timeout,
    )

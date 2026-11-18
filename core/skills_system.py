"""
System & Apps skills for VYUK (v2.8, Mega Skills 1 of 3) — the deep
Windows-management pack: installing apps with winget, startup programs,
Windows services, power plans, battery health, user environment
variables, read-only registry lookups, physical-disk health, temp-file
cleanup, largest-file hunts and duplicate-file detection.

Same rules as core/skills_plus.py and core/desktop/control_plus.py: plain
functions returning human-readable strings; every platform-specific
dependency (winreg, ctypes, sc.exe, powercfg, winget) is reached lazily
and guarded with try/except, so this module imports cleanly on any OS —
on non-Windows systems the Windows-only skills return a clear fallback
string instead of crashing. Destructive skills are confirm-gated
(confirm=true) and never fake a success the OS did not confirm:
startup-program disabling MOVES the Run value into VYUK's own backup
key (HKCU\\Software\\VYUK\\DisabledStartup) so enabling restores it
byte-for-byte; registry access in this module is read-only by design —
there is deliberately no registry-write tool here.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Phase A batch A31 part 1 (skills split): the apps-startup family
# moved verbatim to skills_system_apps_startup, and the shared
# helpers to skills_system_helpers. The names below are re-exports
# with identity, so existing importers keep working — including
# core/skills_system_bench.py's attribute read
# skills_system._startup_inventory() (function-local import) and
# tests/test_packaging.py's skills_system.register_all read.
# INTENTIONAL: the original stdlib import block above is kept
# verbatim (leaked-import preservation) so the facade namespace
# is unchanged; the impl modules import only what they use.
# SEAM NOTE: facade bindings are from-import snapshots — patching
# a facade binding alone NEVER drives impl code. A test
# substituting a moved name must patch ALL bindings (facade +
# skills_system_apps_startup + skills_system_helpers as bound).
from .skills_system_apps_startup import (_parse_winget_table, _WINGET_SCHEMAS, _parse_winget_table_struct, _winget_rows, _winget_bare_message, _winget_search, _describe_package, _winget_installed_row, _winget_pending_updates, winget_install, list_app_updates, winget_upgrade_all, _RUN_KEY, _DISABLED_KEY, _startup_folder, _disabled_folder, _read_run_values, _folder_items, _startup_inventory, list_startup_programs, set_startup_program)
from .skills_system_helpers import (_is_windows, _human_size, _NOT_WINDOWS, _run, _tail)

# Phase A batch A31 part 2 (skills split close-out): the
# services-power and env-disk families moved verbatim to
# skills_system_services_power and skills_system_env_disk.
# The names below are re-exports with identity, so existing
# importers keep working; register_all below stays verbatim.
# SEAM NOTE (part 2): facade bindings are from-import
# snapshots — patching a facade binding alone NEVER drives
# impl code. A test substituting a moved name must patch ALL
# bindings (facade + skills_system_services_power +
# skills_system_env_disk + skills_system_helpers as bound).
from .skills_system_services_power import (_parse_sc_query, list_services, _SC_ERROR_NOTES, _SERVICE_POLL_ATTEMPTS, _SERVICE_POLL_INTERVAL_S, _sc_state, _wait_for_service_state, _service_poll_budget_s, service_control, _GUID_RE, _parse_power_schemes, power_plans, _extract_after_label, _parse_grouped_number, _battery_figures, battery_health)
from .skills_system_env_disk import (_ENV_KEY, _SECRET_NAME_PARTS, _env_value_shown, env_var, _REG_ROOTS, _REG_TYPE_NAMES, _format_reg_value, registry_read, disk_health, _TEMP_SKIP_AGE_SECONDS, _temp_dir_paths, _measure_temp_dirs, clean_temp_files, _SCAN_ROOTS, _SCAN_SKIP_DIRS, _SCAN_MAX_DEPTH, _resolve_scan_root, _iter_scanned_files_with_stats, _iter_scanned_files, largest_files, _hash_file, find_duplicates)


# Winget localizes its table HEADERS with the Windows display language
# ("Name" -> "Nom", "Available" -> "Disponible", ...), so any parse
# keyed on the English words silently yields zero rows on a non-English
# PC — and a zero-row parse must never be read as a fact about the
# system (the v4.7 rule: an empty parse is an error to report, never a
# fact to assert). The structural parse below ignores header WORDS
# entirely: it locates the dashed separator line winget always prints,
# takes the column offsets from the label runs on the line above it,
# and assigns fields by position using winget's fixed column order.


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every System & Apps pack tool on a ToolRegistry-like object."""
    registry.register(
        name="winget_install",
        description="Install a desktop app using winget, the Windows package manager. VYUK resolves the request against winget search first and installs ONLY an exact name or package-id match; if there is no exact match it installs nothing and returns the closest candidates, so ask again with the exact name or id from that list. The confirmation message and the result name the resolved package precisely. Downloading software onto the PC requires confirm=true.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "App name or winget package id, e.g. 'VLC' or 'VideoLAN.VLC'"},
                "confirm": {"type": "boolean", "description": "Must be true to install"},
            },
            "required": ["query"],
        },
        handler=winget_install,
    )
    registry.register(
        name="list_app_updates",
        description="List the apps that winget says have a newer version available, showing current and new version numbers. Use when the user asks what's out of date. Apply them all with winget_upgrade_all; install something new with winget_install.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=list_app_updates,
    )
    registry.register(
        name="winget_upgrade_all",
        description="Update every app that winget manages to its latest version, in one go. This downloads and installs updates across the PC, so it requires confirm=true. See what's pending first with list_app_updates.",
        parameters={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true to update everything"},
            },
            "required": [],
        },
        handler=winget_upgrade_all,
    )
    registry.register(
        name="list_startup_programs",
        description="List everything that runs automatically when the user signs in to Windows — registry Run entries (this user and all users) and Startup folder items — including any that VYUK previously disabled, marked as disabled. To stop one of them starting, set_startup_program; for what each costs at sign-in, startup_impact.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=list_startup_programs,
    )
    registry.register(
        name="set_startup_program",
        description="Enable or disable a startup program for THIS user's account, reversibly: disabling moves its entry into VYUK's safekeeping (never deletes it), enabling restores it exactly. Entries that start for all users need an administrator and are refused with an explanation. Changing sign-in behaviour requires confirm=true.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Startup program name as shown by list_startup_programs"},
                "enabled": {"type": "boolean", "description": "true = run at sign-in, false = don't"},
                "confirm": {"type": "boolean", "description": "Must be true to make the change"},
            },
            "required": ["name", "enabled"],
        },
        handler=set_startup_program,
    )
    registry.register(
        name="list_services",
        description="List Windows services (name, description, and whether each is RUNNING or STOPPED). Optionally filter by a word matching the name, description or state, e.g. 'audio' or 'stopped'. The listing is read-only — to start, stop or restart one, service_control.",
        parameters={
            "type": "object",
            "properties": {
                "state_filter": {"type": "string", "description": "Optional text to filter by, e.g. 'print' or 'running'"},
                "limit": {"type": "integer", "description": "How many to list (default 80, max 300); the result always states the true total"},
            },
            "required": [],
        },
        handler=list_services,
    )
    registry.register(
        name="service_control",
        description="Start, stop or restart a Windows service by its service name (see list_services). Many services need VYUK to be running as administrator; if Windows refuses, the result says so. Requires confirm=true.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Service name, e.g. 'Spooler'"},
                "action": {"type": "string", "description": "'start', 'stop' or 'restart'"},
                "confirm": {"type": "boolean", "description": "Must be true to change a service"},
            },
            "required": ["name", "action"],
        },
        handler=service_control,
    )
    registry.register(
        name="power_plans",
        description="List Windows power plans (Balanced, High performance, Power saver...) showing which is active, or switch the active plan by name. Switching requires confirm=true. The battery's own state and health are in battery_info.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'list' (default) or 'set'"},
                "plan": {"type": "string", "description": "For 'set': part of the plan's name, e.g. 'High performance'"},
                "confirm": {"type": "boolean", "description": "Must be true to switch plans"},
            },
            "required": [],
        },
        handler=power_plans,
    )
    registry.register(
        name="battery_health",
        description="Report the laptop battery's health: design capacity vs the capacity it can still hold, the wear percentage, and how many charge cycles it has done — from Windows' own battery report. For the current charge level and time left, use battery_info.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=battery_health,
    )
    registry.register(
        name="env_var",
        description="Work with the current user's environment variables: 'list' shows them all, 'get' reads one, 'set' changes one (user scope only — never system variables). Setting requires confirm=true, and programs only pick up the change when they next start. Values of secret-named variables (KEY, TOKEN, SECRET, PASSWORD…) are shown masked — first/last 2 characters plus a length — because results land in chat and logs; 'get' with confirm=true reveals the full value.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'list' (default), 'get' or 'set'"},
                "name": {"type": "string", "description": "Variable name, for get/set, e.g. 'EDITOR'"},
                "value": {"type": "string", "description": "New value, for set"},
                "confirm": {"type": "boolean", "description": "Must be true to set a variable; for 'get' it also reveals a masked secret-named value"},
            },
            "required": [],
        },
        handler=env_var,
    )
    registry.register(
        name="registry_read",
        description="Look INSIDE the Windows registry without changing anything (read-only by design — VYUK never writes to the registry with this tool): give a path starting with HKCU, HKLM or HKCR to list its values, and optionally a value name to read just that one.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Registry path, e.g. 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion'"},
                "value_name": {"type": "string", "description": "Optional: read only this value"},
            },
            "required": ["path"],
        },
        handler=registry_read,
    )
    registry.register(
        name="disk_health",
        description="Show the PC's physical disks — model, type (SSD/HDD), size, and the health status Windows reports for each. For how full each drive is, drive_info.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=disk_health,
    )
    registry.register(
        name="clean_temp_files",
        description="Measure or clear the Windows temp folders. Without confirm, it only reports how much junk there is and how much is older than 7 days. With confirm=true it deletes the files older than 7 days (files in use are skipped and counted). Pass days to use a different age threshold than 7 days for both the report and the deletion.",
        parameters={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true to actually delete"},
                "days": {"type": "integer", "description": "Age threshold in days (default 7, 1-3650): only files older than this count as clearable"},
            },
            "required": [],
        },
        handler=clean_temp_files,
    )
    registry.register(
        name="largest_files",
        description="Find the biggest files in a place (home folder by default, or Desktop/Documents/Downloads, or any folder path) and list them biggest-first with sizes. Use when the user asks what's eating their disk space. For ONE folder's total use folder_size; for a per-folder breakdown of where bytes live use folder_size_map.",
        parameters={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "'home' (default), 'desktop', 'documents', 'downloads', or a folder path"},
                "limit": {"type": "integer", "description": "How many to list (default 10, max 50)"},
            },
            "required": [],
        },
        handler=largest_files,
    )
    registry.register(
        name="find_duplicates",
        description="Find duplicate files inside a folder — files with identical content — and report each group with the disk space the extra copies waste. It only reports; it never deletes anything. Pass min_size_kb to skip files below a size (e.g. min_size_kb=100 to ignore tiny identical files); exclusions are stated in the result.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Folder to scan, e.g. 'C:\\Users\\me\\Downloads'"},
                "limit_groups": {"type": "integer", "description": "Maximum groups to list (default 10, max 30)"},
                "min_size_kb": {"type": "integer", "description": "Ignore files smaller than this many KB (default 0 = no minimum)"},
            },
            "required": ["folder"],
        },
        handler=find_duplicates,
    )

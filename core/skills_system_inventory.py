"""
System & Apps skills III for VYUK (v5.5, Pack D) — the machine itself:
hardware inventory, signed drivers, drives, restore-point listing,
Defender + firewall status, firewall control, local user accounts,
installed fonts, power requests, sleep states, hibernate and fast
startup.

Same rules as core/skills_system.py / skills_system_monitor.py: plain
functions returning human-readable strings; every platform-specific
dependency (psutil, PowerShell CIM, powercfg, winreg) is reached
lazily and guarded, so this module imports cleanly on any OS — on
non-Windows systems the Windows-only skills return a clear fallback
string instead of crashing. Nothing here fakes a success the OS did
not confirm: state-changing tools re-read the state after writing
and report what is actually there, exit codes are checked, and
every cap (drivers shown, fonts shown, output truncated) is
disclosed in the result text. Where PowerShell returns data, fields
ride on a unit-separator character and are parsed structurally —
no localised label is ever matched (v4.7 rule). A missing psutil
degrades to an honest line, never an exception.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself. Gate placement
(firewall enable/disable, hibernate on/off, fast-startup on/off)
is likewise the parent's job; the handlers here just do the work
honestly.
"""

# INTENTIONAL: the old module's namespace carried its stdlib imports;
# re-exported so the facade's dir() matches the old module's exactly
# (dir-diff-empty bar, plan §2 P2; files3/files2 lesson).
import re
import subprocess
import sys


# INTENTIONAL: re-exports of every moved name, so importers of the
# facade see the same namespace the old module had, with identity
# (each facade name IS its impl module's object).
from .skills_system3_helpers import (
    _NOT_WINDOWS,
    _SEP,
    _bool_word,
    _field,
    _human_size,
    _is_windows,
    _looks_admin_denied,
    _ps_structured,
    _run,
    _tail,
)
from .skills_system3_hardware import (
    _DRIVER_CAP,
    _FONT_CAP,
    _FONT_SUFFIX_RE,
    _RESTORE_CAP,
    _clean_font_name,
    _format_drivers,
    _format_hardware,
    _format_restore_points,
    _format_users,
    _wmi_datetime,
    drive_info,
    hardware_info,
    list_drivers,
    list_fonts,
    list_restore_points,
    list_users,
)
from .skills_system3_security import (
    _FIREWALL_PS_READ,
    _format_firewall,
    _format_security,
    firewall_control,
    security_status,
)
from .skills_system3_power import (
    _POWER_KEY,
    _SESSION_POWER_KEY,
    _format_power_requests,
    _format_sleep_states,
    _read_dword,
    fast_startup_control,
    hibernate_setting,
    power_requests,
    sleep_states,
)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every System & Apps III pack tool on a ToolRegistry-like object."""
    registry.register(
        name="hardware_info",
        description="Full hardware inventory of this PC: CPU model, graphics card(s) with VRAM, motherboard, BIOS version and date, and each RAM stick (size and speed) with the total. Read from Windows CIM in one query. Use when the user asks what's inside the PC or what GPU/RAM it has. Read-only. This is the static inventory of what's installed; for live CPU/RAM/disk usage right now use get_system_info, and for GPU driver/VRAM depth use gpu_info.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=hardware_info,
    )
    registry.register(
        name="list_drivers",
        description="List signed device drivers (device name, driver version, provider), optionally filtered by part of a device name. Shows 40 by default (limit raises it) with the true total disclosed. Note: this reads Win32_PnpSignedDriver, one of the slower Windows inventory queries, so it can take a while. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "filter": {"type": "string",
                           "description": "Optional part of a device name to filter by, e.g. 'nvidia'"},
                "limit": {"type": "integer",
                          "description": "How many to list (default 40, max 200)"},
            },
            "required": [],
        },
        handler=list_drivers,
    )
    registry.register(
        name="drive_info",
        description="Show every mounted drive with its free space, total size and percent used, e.g. 'C:\\ — 212 GB free of 476 GB (55% used)'. Drives that can't be read are counted, not hidden. Use when the user asks about disk space. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=drive_info,
    )
    registry.register(
        name="list_restore_points",
        description="List the system restore points that already exist on this PC (date and description, newest first, at most 15 shown). 'No restore points found' means System Restore has none recorded — different from a read failure, which is reported as one. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=list_restore_points,
    )
    registry.register(
        name="security_status",
        description="Windows security posture in one summary: whether Defender antivirus and real-time protection are on, when signatures last updated and the last quick scan ended, plus the on/off state of the Domain/Private/Public firewall profiles. If the Defender cmdlets are unavailable (some third-party antivirus setups) the summary says so instead of guessing. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=security_status,
    )
    registry.register(
        name="firewall_control",
        description="Read or change the Windows firewall across all profiles (Domain, Private, Public). action='status' just reports the states. 'enable'/'disable' set every profile and then re-read them, reporting the states actually found; changing the firewall needs an administrator context and the result says plainly when Windows refuses.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["status", "enable", "disable"],
                           "description": "What to do (default status)"},
            },
            "required": [],
        },
        handler=firewall_control,
    )
    registry.register(
        name="list_users",
        description="List the local user accounts on this PC, marking which one is the current user and which are disabled. Read-only. Local accounts only — Microsoft and domain accounts aren't listed.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=list_users,
    )
    registry.register(
        name="list_fonts",
        description="List installed font family names, optionally filtered by part of a name. Shows at most 60 with the true total disclosed. Use when the user asks what fonts they have or whether a font is installed. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "filter": {"type": "string",
                           "description": "Optional part of a font name to filter by, e.g. 'cascadia'"},
            },
            "required": [],
        },
        handler=list_fonts,
    )
    registry.register(
        name="power_requests",
        description="Show what is currently keeping this PC awake — processes, drivers or settings that have filed power requests blocking sleep or display-off (powercfg /requests). When every category reports None, the answer says plainly that nothing is keeping the PC awake. Reading this needs an administrator context on most systems; the result says so when Windows refuses. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=power_requests,
    )
    registry.register(
        name="sleep_states",
        description="Show which sleep states this PC supports (S3 sleep, hibernate, hybrid sleep, fast startup availability and friends) as reported by powercfg /a, including why any state is unavailable. Read-only. To change the hibernate or fast-startup behaviour it reports, hibernate_setting and fast_startup_control.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=sleep_states,
    )
    registry.register(
        name="hibernate_setting",
        description="Read or change hibernation on this PC. action='status' reports the registry state; 'on'/'off' run powercfg /hibernate and confirm against the registry afterwards. Changing it needs an administrator context and the result says plainly when Windows refuses.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["on", "off", "status"],
                           "description": "What to do (default status)"},
            },
            "required": [],
        },
        handler=hibernate_setting,
    )
    registry.register(
        name="fast_startup_control",
        description="Read or change Windows Fast Startup (the HiberbootEnabled registry setting). action='status' reports it, noting when it is unset and the Windows default (on) applies; 'on'/'off' write it and read it back to confirm. Writing needs an administrator context and the result says plainly when Windows refuses.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["status", "on", "off"],
                           "description": "What to do (default status)"},
            },
            "required": [],
        },
        handler=fast_startup_control,
    )

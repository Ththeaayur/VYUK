"""
System skills VI for VYUK (v5.30, HANDS — System IV pack):
ten diagnostic and housekeeping tools that lean on the seams the
earlier system packs already proved — the structured PowerShell
channel (skills_system_monitor._ps_structured), its restore-point helpers,
the netsh Wi-Fi parse (skills_network), the watchers' idle probe
and psutil. What's new here is the synthesis: boot-time history,
a room-by-room Wi-Fi dead-spot map, a crash digest grouped by app,
a peripheral roll call with a saved baseline, an honest
screen-time counter and a propose-only update-restart planner.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise; every sampling
tool states its window; anything that acts (restore point,
eject) verifies the outcome and reports failure plainly instead
of claiming success. Windows-only tools answer with the family's
honest not-Windows line on other systems.

State this pack keeps — all small JSON files in the product data
dir (core/config.py's DATA_DIR, resolved fresh on every call):
wifi_dead_spots.json (readings from the current walk),
peripherals_snapshot.json (the last roll call's device list) and
screen_time.json (today's active-use day counter).

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

# INTENTIONAL: re-exports of the old module's import bindings, so
# the facade's namespace matches the old module's exactly
# (dir-diff-empty bar, §2 P2).
import json
import re
import sys
import time
from datetime import date, datetime, time as dtime, timedelta

# INTENTIONAL: re-exports of every name moved into the impl modules.
from .skills_system6_helpers import (
    _NOT_WINDOWS,
    _is_windows,
    _now,
    _today_str,
    _clamp_int,
    _tail,
    _duration_words,
    _state_path,
    _load_json,
    _save_json,
    _ps_events,
    _ps_run,
)
from .skills_system6_boot import (
    _boot_events,
    startup_time_report,
    _battery_status,
    _cpu_sample,
    battery_drain_detective,
)
from .skills_system6_diag import (
    _WIFI_MAP_FILE,
    _wifi_state_path,
    _signal_pct,
    _wifi_room_stats,
    _wifi_report_lines,
    wifi_dead_spot_map,
    _io_snapshot,
    _io_deltas,
    disk_write_hogs,
    _pending_updates,
    _reboot_pending,
    _calendar_busy,
    _propose_window,
    _window_words,
    update_restart_planner,
)
from .skills_system6_safety import (
    restore_point_before_changes,
    _crash_events,
    _group_crashes,
    app_crash_digest,
)
from .skills_system6_devices import (
    _PERIPHERAL_FILE,
    _peripheral_path,
    _list_devices,
    _device_counts,
    _diff_names,
    peripheral_roll_call,
    _SCREEN_TIME_FILE,
    _screen_time_path,
    _idle_seconds,
    _update_screen_counter,
    screen_time_today,
    _removable_drives,
    _eject_drive,
    safe_eject_all,
)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every System IV pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="startup_time_report",
        description="Report how long this PC took to start today versus its average over the last 7 days, from Windows' own boot-performance log (Diagnostics-Performance event 100): the total start time, how it splits between boot and getting to the desktop, and whether today was faster or slower than average. Use when sign-in or boot feels slow, or after removing startup apps to check it helped. Windows only. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=startup_time_report,
    )
    registry.register(
        name="battery_drain_detective",
        description="Find what's draining the battery right now: samples every process's CPU for a short window (20 seconds by default, up to 60) and names the heaviest users. On a plugged-in PC it says so and labels the result as current load instead; on a desktop it notes there's no battery. Use when the battery is dropping fast or the fans are spinning up. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "How long to sample CPU, 5–60 seconds (default 20)"},
            },
            "required": [],
        },
        handler=battery_drain_detective,
    )
    registry.register(
        name="wifi_dead_spot_map",
        description="Map Wi-Fi dead spots room by room. Actions: 'start' begins a fresh walk (clearing the old map), 'add_reading' saves the current signal under a room name, 'stop' ends the walk keeping the map, and 'report' shows each room's average and weakest signal and names the weakest room. Use when some rooms have bad Wi-Fi. Windows (netsh) only; readings persist in a small file in VYUK's data folder.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "add_reading", "stop", "report"], "description": "'start', 'add_reading', 'stop' or 'report' (default 'report')"},
                "room": {"type": "string", "description": "Room name for an 'add_reading' action, e.g. 'Kitchen'"},
            },
            "required": [],
        },
        handler=wifi_dead_spot_map,
    )
    registry.register(
        name="disk_write_hogs",
        description="Name the processes writing most to disk right now: samples per-process disk writes over a few seconds (5 by default, up to 15) and reports the top writers in megabytes, with the sample window stated. Use when the disk is churning or a drive light won't settle. Read-only — a short sample, not a history.",
        parameters={
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "How long to sample disk writes, 1–15 seconds (default 5)"},
            },
            "required": [],
        },
        handler=disk_write_hogs,
    )
    registry.register(
        name="update_restart_planner",
        description="Suggest a good time to restart for Windows updates — it never schedules or forces anything. Checks which updates are waiting and whether a restart is already owed, then proposes an early-morning window, checked against Google Calendar for the next 48 hours when it's connected (and says plainly when the calendar couldn't be checked). Windows only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=update_restart_planner,
    )
    registry.register(
        name="restore_point_before_changes",
        description="Create a safety restore point before making changes, then verify it: the point is created with Checkpoint-Computer and confirmed by reading the restore-point list back — the result names the point and its creation time, or says honestly if creation or confirmation failed. Takes an optional description. Windows only; needs an admin context.",
        parameters={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Optional name for the restore point (a timestamped default is used otherwise)"},
            },
            "required": [],
        },
        handler=restore_point_before_changes,
    )
    registry.register(
        name="app_crash_digest",
        description="Summarise recent application crashes and errors from the Windows Application event log over the last 7 days (1–30): grouped by app or source, worst first — 'Zoom.exe crashed 3 times, most recently …'. Use when programs keep crashing or the user asks what has been failing this week. Windows only. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days back to look, 1–30 (default 7)"},
            },
            "required": [],
        },
        handler=app_crash_digest,
    )
    registry.register(
        name="peripheral_roll_call",
        description="List the USB and Bluetooth devices connected right now by their friendly names, and compare with the last saved snapshot (kept in VYUK's data folder) to name anything missing or newly appeared. The first run just saves the baseline and says so. Use when a device seems to have vanished or to check what's plugged in. Windows only. Read-only apart from the snapshot file.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=peripheral_roll_call,
    )
    registry.register(
        name="screen_time_today",
        description="Estimate today's active use: folds the time since the last check into a small day counter (kept in VYUK's data folder) using the Windows idle-time reading, so time away from the keyboard isn't counted. It only covers time while VYUK has been running and says exactly what the counter holds — on its first run it reports that counting has only just started. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=screen_time_today,
    )
    registry.register(
        name="safe_eject_all",
        description="Safely eject every removable (USB) drive in one call, then check each one actually left: any drive that refuses is named, with the likely reason (usually a program still using it). Use before unplugging drives, or when the user says 'eject everything'. Windows only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=safe_eject_all,
    )

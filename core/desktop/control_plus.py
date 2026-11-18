"""
Control Complete pack for VYUK — the second wave of assistant-style
laptop control: click-by-text (OCR), click-by-name (Windows UI
Automation), exact volume level, network/Wi-Fi, display/wallpaper/
dark mode, virtual desktops, process inspection, battery, zip
archives, webcam snapshots, mouse drag, recycle bin, downloads.

Same rules as core/control.py: plain functions returning
human-readable strings; every platform-specific dependency
(pywinauto, pycaw, pytesseract, psutil, ctypes.windll,
vision) is imported lazily inside the function that needs it and
guarded with try/except, so this module imports cleanly on any
OS — including headless Linux, where unavailable features simply
return a clear fallback string instead of crashing.

Wiring: the parent build calls register_all(registry) from core/funnel/tools_handlers_pack_core2.py (_register_complete_tools) — this module never touches the registry itself.
"""

import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

def get_window_controls(title_substring: str = "") -> str:
    """List the named UI controls of a window via Windows UI Automation (pywinauto).

    The walk counts EVERY named control before the display cap is
    applied, so the header can disclose the true total beside the
    shown count (v5.9, audit H18), and it stops examining elements
    at a hard budget, saying so when it does.
    """
    if not _is_windows():
        return "UI Automation inspection is only available on Windows — this system is not Windows."
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        return "UI Automation isn't available in this build of VYUK — the UI Automation part didn't make it into the package."
    try:
        win, desc = _uia_window(title_substring)
    except Exception as e:  # noqa: BLE001
        return f"Could not connect to {desc if 'desc' in dir() else 'the window'}: {e}"
    try:
        elements = win.descendants()
    except Exception as e:  # noqa: BLE001
        return f"Could not read controls from {desc}: {e}"
    lines = []
    total_named = 0
    examined = 0
    stopped_early = False
    for el in elements:
        if examined >= _CONTROLS_WALK_BUDGET:
            stopped_early = True
            break
        examined += 1
        try:
            info = el.element_info
            name = (info.name or "").strip()
            if not name:
                continue
            total_named += 1
            if len(lines) < _CONTROLS_DISPLAY_CAP:
                lines.append(f"• {name} ({info.control_type})")
        except Exception:  # noqa: BLE001 — dead element mid-walk
            continue
    if total_named == 0 and not stopped_early:
        return f"No named controls found in {desc}."
    header = (f"Controls in {desc} ({total_named} named controls "
              f"in total; {len(lines)} shown, cap {_CONTROLS_DISPLAY_CAP}):")
    if stopped_early:
        header += (f" Note: the walk stopped after examining "
                   f"{_CONTROLS_WALK_BUDGET} elements, so the window "
                   "may hold more named controls than the total shown.")
    return _truncate(header + "\n" + "\n".join(lines), 1500)


def click_element(name: str, title_substring: str = "") -> str:
    """Click a named UI element (button, menu item, link…) via Windows UI Automation.

    Match discipline (v5.9, audit H4 — the uia_click_control
    standard): ALL matches are collected before anything is
    clicked. An exact name match beats controls that merely
    contain the text ("Save" must never silently land on
    "Save As"); when ambiguity remains — several plausible
    candidates in the winning tier — nothing is clicked and the
    candidates are listed with their rectangles. Disabled or
    invisible matches are skipped with disclosure. The scan,
    facts and usability helpers are shared with core/desktop/uia.py
    rather than duplicated here. The v4.3 failsafe is consulted
    before any click is dispatched (audit H3).
    """
    if not name or not str(name).strip():
        return "No element name given. Tell me the label of the control to click."
    blocked = _input_blocked()
    if blocked:
        return blocked
    if not _is_windows():
        return "Click-by-name via UI Automation is only available on Windows — this system is not Windows."
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        return "UI Automation isn't available in this build of VYUK — the UI Automation part didn't make it into the package."
    try:
        win, desc = _uia_window(title_substring)
    except Exception as e:  # noqa: BLE001
        return f"Could not connect to the target window: {e}"
    from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; D2b interim closed

    elements, failure = _uia._descendants(win)
    if failure is not None:
        return f"Could not scan controls in {desc}: {failure}"
    truncated = len(elements) > _uia._SCAN_CAP
    if truncated:
        elements = elements[:_uia._SCAN_CAP]
    needle = str(name).strip().lower()
    matches = []  # (el, facts) substring pairs for the shared pick
    for el in elements:
        facts = _uia._element_facts(el)
        if facts is None or not facts["name"]:
            continue
        lowered = facts["name"].lower()
        if needle not in lowered:
            continue
        matches.append((el, facts))
    if not matches:
        note = ""
        if truncated:
            note = (f" (the scan stopped at {_uia._SCAN_CAP} elements, "
                    "so the control may exist deeper in the window)")
        return f"No control with a name containing '{name}' was found in {desc}{note}."
    # D-01: the same shared pick as uia_click_control — exact
    # first, ambiguity refuses, truncation disclosed in the note.
    chosen, note_or_refusal = _uia._pick_click_target(
        matches, name, desc, truncated=truncated)
    if chosen is None:
        return note_or_refusal
    el, facts = chosen
    note = note_or_refusal
    # H5 probe (v5.44): snapshot state + foreground/focus BEFORE
    # the click, via core/desktop/uia.py's shared probe — the result then
    # reports what the click caused, like uia_click_control does.
    before_state = _uia._control_state(el)
    snap_before = _uia._safe_snapshot()
    # B13: the vision landing check asks the B5 backend whether the
    # click landed, with the element's name as the expectation.
    # Where vision cannot read, the honestly-unverified return
    # stands exactly as before (same message, same trailer). The
    # check runs once, outside both click attempts, so it can
    # never fall through to a second click.
    from .. import tool_outcome as _outcome  # B13: seam wiring

    clicked_msg = None
    try:
        el.click_input()
        base = (f"Clicked '{facts['name']}' ({facts['control_type']}) "
                f"in {desc}.{note}")
        clicked_msg = _uia._with_dispatch_trailer(
            base + _uia._probe_click_suffix(
                snap_before, _uia._safe_snapshot(), before_state,
                _uia._control_state(el), facts, "uia"))
    except Exception:  # noqa: BLE001 — fall back to the UIA Invoke pattern
        pass
    if clicked_msg is None:
        try:
            el.iface_invoke.Invoke()
            base = (f"Invoked '{facts['name']}' ({facts['control_type']}) "
                    f"in {desc}.{note}")
            clicked_msg = _uia._with_dispatch_trailer(
                base + _uia._probe_click_suffix(
                    snap_before, _uia._safe_snapshot(), before_state,
                    _uia._control_state(el), facts, "uia"))
        except Exception as e:  # noqa: BLE001
            return (f"Found '{facts['name']}' in {desc}, but could "
                    f"not click it: {e}")
    return _outcome.hands_landing_check(
        "click", facts["name"], clicked_msg)


def register_all(registry) -> None:
    """Register every Control Complete tool on a ToolRegistry-like object."""
    registry.register(
        name="click_text",
        description="Find visible text on the screen using OCR and click it. Use when the user says 'click the button that says X' or similar and no coordinates are known. If the text appears more than once on screen, nothing is clicked and the matches are listed with their coordinates instead of guessing. Needs her built-in text-reading engine and a display; returns a clear message if OCR finds no match.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The word or short phrase visible on screen to click (e.g. 'Submit', 'Save changes')"}
            },
            "required": ["text"],
        },
        handler=click_text,
    )
    registry.register(
        name="get_window_controls",
        description="List the named UI controls (buttons, text fields, menu items) of the foreground window — or of a window whose title contains the given text — using Windows UI Automation. Windows only, needs pywinauto. Use to discover what can be clicked by name.",
        parameters={
            "type": "object",
            "properties": {
                "title_substring": {"type": "string", "description": "Optional part of a window title; empty means the foreground window"}
            },
            "required": [],
        },
        handler=get_window_controls,
    )
    registry.register(
        name="click_element",
        description="Click a UI element by its visible name (e.g. a 'Save' button) in the foreground window, or in a window whose title contains title_substring. An exact name match wins over controls that merely contain the text; if several controls could match, nothing is clicked and the candidates are listed instead of guessing. Windows only via UI Automation (pywinauto) — more reliable than guessing pixel coordinates.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The control's visible name/label to click (exact matches are preferred over partial ones)"},
                "title_substring": {"type": "string", "description": "Optional part of a window title; empty means the foreground window"},
            },
            "required": ["name"],
        },
        handler=click_element,
    )
    registry.register(
        name="set_volume_level",
        description="Set the master volume to an EXACT percentage (0-100), unlike adjust_volume which only steps up/down. The level is read back from the device afterwards and the actual percentage reported; a request outside 0-100 is clamped and the clamp is disclosed. Windows only: uses pycaw when it is part of the build, else nircmd if present, else reports that the exact level is unavailable. For step up/down instead use adjust_volume; for one app only, app_volume.",
        parameters={
            "type": "object",
            "properties": {
                "percent": {"type": "integer", "description": "Volume level 0-100"}
            },
            "required": ["percent"],
        },
        handler=set_volume_level,
    )
    registry.register(
        name="network_control",
        description="Network and Wi-Fi control. action 'status' works anywhere (hostname, IPs, adapters). 'wifi_list', 'wifi_on', 'wifi_off', 'wifi_connect' are Windows-only (netsh); wifi_list shows up to 15 networks and states the true total when more are in range; wifi_connect uses the SAVED Windows profile for that network — it cannot join a brand-new network from a raw password. 'bluetooth_toggle' honestly reports that Bluetooth has no reliable command-line switch.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "wifi_on", "wifi_off", "wifi_list", "wifi_connect", "bluetooth_toggle"], "description": "What to do"},
                "ssid": {"type": "string", "description": "Network name for wifi_connect"},
                "password": {"type": "string", "description": "Accepted for wifi_connect but NOT used — Windows connects via its saved profile"},
            },
            "required": ["action"],
        },
        handler=network_control,
    )
    registry.register(
        name="display_control",
        description="Display actions: 'info' (screen resolution, any OS with a display), 'set_wallpaper' with an image path (Windows; the saved setting is read back to confirm), 'dark_on' / 'dark_off' to switch the Windows theme — both the app and system theme values are written and read back before the change is claimed (Windows). The dedicated toggle_dark_mode tool offers the same switch with a toggle option. Optional monitor names ONE screen for set_wallpaper (primary, left, right, second monitor, a device name like DISPLAY2, or all) — without it the image is set on all monitors at once.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["info", "set_wallpaper", "dark_on", "dark_off"], "description": "What to do"},
                "path": {"type": "string", "description": "Image file path for set_wallpaper"},
                "monitor": {"type": "string", "description": "Optional: which screen set_wallpaper changes — 'primary', 'left', 'right', 'second monitor', a device name like 'DISPLAY2', or 'all'. Without it the image is set on all monitors at once."},
            },
            "required": ["action"],
        },
        handler=display_control,
    )
    registry.register(
        name="desktop_control",
        description="Windows desktop and window-layout actions sent as system hotkeys: show_desktop (Win+D), minimize_all (Win+M), snap_left / snap_right for the active window, next_desktop / prev_desktop to switch virtual desktops, task_view (Win+Tab). Windows only. For clicking a named control use uia_click_control; for cursor moves and clicks by coordinates use cursor_control.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["show_desktop", "minimize_all", "snap_left", "snap_right", "next_desktop", "prev_desktop", "task_view"], "description": "The desktop action"}
            },
            "required": ["action"],
        },
        handler=desktop_control,
    )
    registry.register(
        name="process_control",
        description="Inspect running processes: 'list' (top 15 by CPU), 'top_memory' (top 10 by RAM), or 'kill_pid' to terminate one process by pid and verify that it actually exited — kill_pid requires confirm=true. For a quick snapshot of the heaviest processes, top_processes; to change a process's priority, set_process_priority.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "top_memory", "kill_pid"], "description": "What to do"},
                "name": {"type": "string", "description": "Currently unused label kept for future filtering"},
                "pid": {"type": "integer", "description": "Process id for kill_pid"},
                "confirm": {"type": "boolean", "description": "Must be true for kill_pid"},
            },
            "required": ["action"],
        },
        handler=process_control,
    )
    registry.register(
        name="battery_info",
        description="Report battery percentage, whether the machine is plugged in, and the estimated time left. Returns a clear message on desktops with no battery. For battery wear, lost capacity and charge cycles, use battery_health.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=battery_info,
    )
    registry.register(
        name="zip_control",
        description="Zip archive operations: 'zip' a file or folder into a .zip, 'unzip' an archive (refuses archives whose members would escape the destination folder), 'list_zip' to see contents without extracting (each entry's size plus the total uncompressed size).",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["zip", "unzip", "list_zip"], "description": "What to do"},
                "path": {"type": "string", "description": "File/folder to zip, or the .zip archive for unzip/list_zip"},
                "destination": {"type": "string", "description": "Optional output path (zip file for zip, folder for unzip)"},
            },
            "required": ["action", "path"],
        },
        handler=zip_control,
    )
    registry.register(
        name="webcam_snapshot",
        description="Capture a single frame from the webcam and save it as a JPEG (default location: ~/Pictures/nova_webcam_<timestamp>.jpg). Needs a camera and opencv; reports clearly if either is unavailable. For a picture of the screen instead, take_screenshot; to have the camera view described, describe_camera_view.",
        parameters={
            "type": "object",
            "properties": {
                "save_path": {"type": "string", "description": "Optional full path for the JPEG; empty uses the default Pictures location"}
            },
            "required": [],
        },
        handler=webcam_snapshot,
    )
    registry.register(
        name="watch_gestures",
        description="Watch the camera for hand gestures and face presence for a bounded time (default 10 seconds, at most 30), then report what was actually seen — the gestures that held long enough to trust (open palm, fist, pinch, point, wave, swipe left/right) and how many frames showed a face or a hand. The camera runs only for this watch, only after the capture guard allows it for the session. A refused camera, missing vision weights, or no camera is an honest refusal, never a guessed gesture. Gestures do NOT act on anything — this only reports what the camera saw.",
        parameters={
            "type": "object",
            "properties": {
                "duration_s": {"type": "number", "description": "How long to watch, in seconds (1 to 30; default 10)"}
            },
            "required": [],
        },
        handler=watch_gestures,
    )
    registry.register(
        name="drag_mouse",
        description="Drag the mouse with the left button held from (x1, y1) to (x2, y2) in smooth steps — for sliders, moving windows, drag-and-drop. Targets are fenced to the screen; any clamping is disclosed in the result. Needs a display (Windows SendInput).",
        parameters={
            "type": "object",
            "properties": {
                "x1": {"type": "integer", "description": "Start X"},
                "y1": {"type": "integer", "description": "Start Y"},
                "x2": {"type": "integer", "description": "End X"},
                "y2": {"type": "integer", "description": "End Y"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
        handler=drag_mouse,
    )
    registry.register(
        name="empty_recycle_bin",
        description="Empty the Windows Recycle Bin PERMANENTLY. Requires confirm=true; there is no undo. Windows only. To delete particular files instead of everything, file_control.",
        parameters={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true to empty the bin"}
            },
            "required": [],
        },
        handler=empty_recycle_bin,
    )
    registry.register(
        name="download_file",
        description="Download a file from an http(s) URL to ~/Downloads (or the given destination path/folder). Refuses non-http(s) URLs and anything over 25 MB. Open the result afterwards with open_path.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The http(s) URL to download"},
                "destination": {"type": "string", "description": "Optional destination file path or folder; empty means ~/Downloads"},
            },
            "required": ["url"],
        },
        handler=download_file,
    )


from .control_plus_helpers import _is_windows, _truncate, _capture_screen_bytes, _input_blocked, _note_input_pressed, _note_input_released, _uia_window, _CONTROLS_WALK_BUDGET, _CONTROLS_DISPLAY_CAP  # INTENTIONAL (B26: facade re-export; moved code lives in control_plus_helpers)
from .control_plus_click import click_text  # INTENTIONAL (B26: facade re-export; moved code lives in control_plus_click)
from .control_plus_volume_network import _pycaw_volume, set_volume_level, _net_status, _parse_wifi_ssids, _format_wifi_list, network_control  # INTENTIONAL (B26: facade re-export; moved code lives in control_plus_volume_network)
from .control_plus_desktop import display_control, _DESKTOP_HOTKEYS, desktop_control, process_control, battery_info  # INTENTIONAL (B26: facade re-export; moved code lives in control_plus_desktop)
from .control_plus_files import zip_control, webcam_snapshot, watch_gestures, drag_mouse, empty_recycle_bin, _MAX_DOWNLOAD_BYTES, download_file  # INTENTIONAL (B26: facade re-export; moved code lives in control_plus_files)

"""
Network, display & input skills for VYUK (v2.8) — the third mega-pack
module: rich Wi-Fi (scan with signal/auth, current-connection info,
joining a NEW network from a raw password, forgetting networks),
internet diagnostics (public IP, speed test, ping, DNS), Bluetooth
inventory, deep display control (per-monitor refresh rates, guarded
resolution changes), window layering (always-on-top, opacity), mouse
speed, and paste-anywhere text.

House rules, same as core/skills_plus.py: plain functions returning
human-readable strings; every platform dependency (netsh, ctypes
display APIs, pyperclip) is used lazily and guarded, so this module
imports cleanly on any OS and Windows-only skills return a clear
fallback string elsewhere. Anything that changes the machine's
network or display is confirm-gated FIRST — before the platform
check — so an unconfirmed call never touches the system on any OS.

Security note (connect_wifi): the password is only ever written into
the temporary WLAN profile XML, which is deleted in a finally block,
and every result string is scrubbed of it by construction — netsh
output is passed through a redaction of the exact password before it
can reach a result.

Wiring: the parent registers this module via register_all(registry);
this module never touches the registry singleton (core/funnel/tools_registry.py).

Audit vs the existing 75 tools (why these aren't duplicates):
- network_control 'wifi_list' lists nearby SSID *names only*, and its
  'wifi_connect' can only use SAVED Windows profiles — it cannot join
  a new network from a password. The Wi-Fi radar (scan_wifi_networks,
  v5.20) adds signal/auth/channel per network; connect_wifi creates
  a real profile from a raw password.
- display_control 'info' is primary-screen size only; display_details
  gives per-monitor resolution + refresh rate, and set_resolution /
  window_always_on_top / window_opacity / mouse_settings have no
  equivalent anywhere in the 75.
- write_clipboard copies; paste_text copies AND pastes (Ctrl+V).
"""

import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape


# INTENTIONAL: re-exports of every moved name (split A23). The facade
# keeps the core/skills_network.py module path, so live importers
# (core/skills_display_settings.py imports _monitor_details; core/skills_system_health.py
# reads skills_network._run) keep working with ZERO importer changes, and
# every re-export IS the impl module's object (from-import snapshot).
# The stdlib import lines above are kept verbatim (leaked-import lesson):
# the old module's namespace carried them, so dropping them would move
# the dir() diff.
from .skills_network_helpers import _NOT_WINDOWS, _is_windows, _run
from .skills_network_wifi_net import (
    _HOST_RE,
    _build_wifi_profile_xml,
    _current_wifi_ssid,
    _speed_finish,
    _speed_report,
    _upload_speed,
    _valid_host,
    _verify_wifi_connection,
    bluetooth_devices,
    connect_wifi,
    dns_flush,
    dns_lookup,
    forget_wifi,
    ip_renew,
    ping_host,
    public_ip,
    speed_test,
    wifi_info,
)
from .skills_network_display_input import (
    _devmode_for,
    _find_window,
    _find_windows,
    _monitor_details,
    _pick_window,
    display_details,
    mouse_settings,
    paste_text,
    set_resolution,
    window_always_on_top,
    window_opacity,
)


def register_all(registry) -> None:
    """Register every Network/Display/Input pack tool on a ToolRegistry."""
    registry.register(
        name="wifi_info",
        description="Show the current Wi-Fi connection: network name, connected state, signal strength, radio type, channel, security type and access-point address, and this PC's local IP address. Nearby networks are in scan_wifi_networks; previously joined ones in saved_wifi_networks.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=wifi_info,
    )
    registry.register(
        name="connect_wifi",
        description="Join a Wi-Fi network, including a brand-new one: creates a Windows profile from the network name and password you give (leave the password empty for an open network). This changes the PC's network, so it requires confirm=true. The password is never shown back.",
        parameters={
            "type": "object",
            "properties": {
                "ssid": {"type": "string", "description": "The network name, e.g. 'HomeWiFi'"},
                "password": {"type": "string", "description": "The network password; empty for an open network"},
                "confirm": {"type": "boolean", "description": "Must be true to connect"},
            },
            "required": ["ssid"],
        },
        handler=connect_wifi,
    )
    registry.register(
        name="forget_wifi",
        description="Forget a saved Wi-Fi network so this PC stops auto-joining it (deletes its Windows profile). Requires confirm=true. The saved list is saved_wifi_networks; the live connection is wifi_info.",
        parameters={
            "type": "object",
            "properties": {
                "ssid": {"type": "string", "description": "The network name to forget"},
                "confirm": {"type": "boolean", "description": "Must be true to forget"},
            },
            "required": ["ssid"],
        },
        handler=forget_wifi,
    )
    registry.register(
        name="public_ip",
        description="Look up this internet connection's public IP address (the one websites see). The result adds where that address is located (city and provider, from ip_geolocate). Look up any other address with ip_geolocate.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=public_ip,
    )
    registry.register(
        name="speed_test",
        description="Measure the internet speed — download, upload and latency (time to first byte) — against Cloudflare's public speed endpoints (takes up to ~35 seconds). Reports MB/s and Mbps for both directions. For a quick is-it-up check instead, ping_host; for the local link's latency and jitter, network_quality.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=speed_test,
    )
    registry.register(
        name="ping_host",
        description="Ping a host name or IP address and report average response time (with the fastest and slowest replies) and packet loss. Use to check whether a site or device is reachable and how fast it answers. For the full route a connection takes, trace_route.",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Host name or IP, e.g. 'google.com' or '192.168.1.1'"},
                "count": {"type": "integer", "description": "How many pings (default 4, max 10)"},
            },
            "required": ["host"],
        },
        handler=ping_host,
    )
    registry.register(
        name="dns_lookup",
        description="Resolve a host name to its IP addresses (IPv4 and IPv6). With reverse=true the host is an IP address and the lookup runs the other way — IP to host name. For this connection's public address, public_ip.",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Host name, e.g. 'example.com' — or an IP address when reverse is true"},
                "reverse": {"type": "boolean", "description": "true to look an IP address up to its host name instead"},
            },
            "required": ["host"],
        },
        handler=dns_lookup,
    )
    registry.register(
        name="dns_flush",
        description="Flush the PC's DNS cache so name lookups are fetched fresh. Harmless — changes no settings. The result is verified against ipconfig's own confirmation line. Use when a site won't load after its address changed.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=dns_flush,
    )
    registry.register(
        name="ip_renew",
        description="Release and renew this PC's IP address from the router. The connection drops for a few seconds while it happens, so it requires confirm=true. The result reports the new address Windows actually assigned.",
        parameters={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true to renew"},
            },
            "required": [],
        },
        handler=ip_renew,
    )
    registry.register(
        name="bluetooth_devices",
        description="List the Bluetooth devices Windows knows about (paired devices, headphones, mice…) with their status. Listing only — it can't pair or toggle Bluetooth. For Wi-Fi networks instead, scan_wifi_networks.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=bluetooth_devices,
    )
    registry.register(
        name="display_details",
        description="Show every monitor with its resolution, refresh rate (Hz), position, and which one is the main screen. Richer than display_control's basic info. Change the resolution or arrangement with display_control; brightness is set_brightness.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=display_details,
    )
    registry.register(
        name="set_resolution",
        description="Change a monitor's resolution (monitor number from display_details). Windows tests the new mode first and VYUK refuses if the test fails. It may rearrange open windows. Requires confirm=true.",
        parameters={
            "type": "object",
            "properties": {
                "monitor_index": {"type": "integer", "description": "Monitor number from display_details, e.g. 1"},
                "width": {"type": "integer", "description": "Horizontal pixels, e.g. 1920"},
                "height": {"type": "integer", "description": "Vertical pixels, e.g. 1080"},
                "confirm": {"type": "boolean", "description": "Must be true to change the resolution"},
            },
            "required": ["monitor_index", "width", "height"],
        },
        handler=set_resolution,
    )
    registry.register(
        name="window_always_on_top",
        description="Pin a window (matched by part of its title) so it stays on top of all other windows, or unpin it. Handy for keeping a video, notes, or a call visible. Find a window's title with window_control.",
        parameters={
            "type": "object",
            "properties": {
                "title_substring": {"type": "string", "description": "Part of the window's title, e.g. 'Calculator'"},
                "on": {"type": "boolean", "description": "true = pin on top (default), false = unpin"},
            },
            "required": ["title_substring"],
        },
        handler=window_always_on_top,
    )
    registry.register(
        name="window_opacity",
        description="Make a window (matched by part of its title) see-through: give an opacity from 30 to 100 percent. 100 restores it to fully solid. Below 30 is refused — the window would be unusable. For keeping a window visible above the others instead, window_always_on_top.",
        parameters={
            "type": "object",
            "properties": {
                "title_substring": {"type": "string", "description": "Part of the window's title, e.g. 'Notes'"},
                "percent": {"type": "integer", "description": "Opacity percent, 30–100"},
            },
            "required": ["title_substring", "percent"],
        },
        handler=window_opacity,
    )
    registry.register(
        name="mouse_settings",
        description="Read or change the Windows mouse pointer speed. action 'get' reports it (1 slow – 20 fast); action 'set' with a speed changes it and the change persists. Pointer speed only — button layout and scroll settings aren't covered.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'get' (default) or 'set'"},
                "speed": {"type": "integer", "description": "For 'set': 1 (slow) to 20 (fast)"},
            },
            "required": [],
        },
        handler=mouse_settings,
    )
    registry.register(
        name="paste_text",
        description="Paste text straight into whatever window is focused right now (puts it on the clipboard, then sends Ctrl+V). Faster and more reliable than typing long text. Up to 5000 characters; the text also stays on the clipboard afterwards.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to paste"},
            },
            "required": ["text"],
        },
        handler=paste_text,
    )

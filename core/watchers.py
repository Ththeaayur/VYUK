"""
Watchers pack for VYUK (v2.3): VYUK notices things on her own.

A WatcherEngine polls the machine on a daemon thread (default every
30 s) and reports noteworthy changes through a single callback,
on_event(kind, message) — messages are plain, speakable sentences
(v2.2 plain-language standard). The engine itself NEVER executes a
tool, NEVER speaks, and never touches the GUI: the window receives
the events (via its signal bridge), always posts them in chat, and
speaks the worthy ones through the connected brain. Which categories
are active is also the window's business (Settings → Presence →
Notices toggles, applied GUI-side); this module just notices.

Watchers (each a small state machine, so an event fires once per
episode, not once per poll):

- battery  — low (<=20%) / critical (<=10%) while unplugged, and
  full (100% while charging). Thresholds re-arm when charging is
  seen or the level rises 5+ points above the threshold.
  (v5.45, N9) While unplugged the watcher keeps a small
  percent-over-time history: an observed drain-rate estimate
  ("at this rate, about N minutes left (my estimate)") is used
  when the OS estimate is absent or wild, and a soft
  "battery_forecast" heads-up fires at 30% only when the
  projection says empty within ~45 minutes.
- downloads — a NEW file in ~/Downloads counts as finished only
  when (v5.45, N6) its size is stable across polls AND no live
  sibling partial (.crdownload/.part/...) with the same stem
  remains AND it has existed for a minimum age — a stalled
  download no longer qualifies on size alone. Files present when
  the engine starts are the baseline and are never announced;
  partial-download suffixes never count as candidates. Finished
  executables/archives earn an automatic sniff_file verdict in
  the notice, plus an offer to open the file (never auto-opened).
- network — two consecutive failed reachability probes announce
  "offline"; the next success announces "back online" (only if the
  outage was announced). (v5.45, N5) Only after the cheap probe
  fails does a diagnosis ladder run (gateway → DNS → HTTPS) and
  its verdict joins the offline notice; the current SSID is
  tracked and an SSID switch writes a QUIET "network_changed"
  line to the timeline (never spoken unless the engine is built
  with network_changed_quiet=False).
- foreground — (v5.45, N13) the foreground process NAME each
  tick (never a window title), cached engine-side like the
  privacy state: call apps feed the GUI's N1 call-aware speech
  gate as an additional signal, and a known media player in
  fullscreen suppresses the break nudge (the episode is logged
  to the timeline instead). Setting-gated via the
  "foreground_enabled" probe seam.
- ambient_light — (v5.45, N12) OPT-IN and off by default: at
  most one webcam frame per hour, reduced to a brightness
  NUMBER via core.senses.frame_brightness; the frame itself is
  never stored or transmitted, and the reading is logged as a
  structured "light" timeline event (N7 fields).
- idle — the user going idle for 20+ minutes arms an "away" state;
  activity again (<10 s idle) announces a welcome back with the
  away duration. (v5.43, N3) The away-start timestamp is stored
  when the episode arms, and the greeting gains a digest of what
  the senses log recorded while the user was out ("While you
  were out: …"), composed by compose_welcome_digest(); with
  nothing on the timeline the greeting is exactly the old one.
- disk — the system drive under 10% free announces once, and
  re-arms only after it recovers above 12%. (v5.43, N2) The
  notice names the largest top-level folders of the drive when a
  budget-capped scan finishes in time; when the scan blows its
  budget or fails, the notice offers the scan in words instead.
- drives — a mountpoint that appears after the baseline announces
  once; removals are silent.
- clipboard — (v5.20, Windows) the clipboard's TEXT changes to a
  link, a file path that exists, or a long passage (200+ chars).
  The probe returns only a fingerprint — category, length and a
  truncated hash — so the copied content itself never reaches a
  message, a log or this engine's state.
- privacy — (v5.20, Windows) an app starts using the camera or the
  microphone (the per-app ConsentStore "in use" flags). New users
  are announced once (one message per tick even if camera and mic
  start together); apps stopping are silent.
- fullscreen — (v5.20, Windows) the foreground window covers its
  monitor for 10+ minutes: one gentle break suggestion per
  episode, re-armed when fullscreen ends.

Everything is probe-driven: the constructor's `probes` dict can
replace any default probe (keys: "battery", "downloads", "network",
"idle_seconds", "disk", "drives", "clipboard", "privacy",
"fullscreen", "load", "top_cpu_process", "top_memory_process",
"disk_top_folders", "network_ssid", "network_diag",
"foreground_app", "foreground_enabled", "ambient_light_enabled",
"ambient_frame", "file_sniffer"), which is how tests drive the
engine deterministically with fake probes and a fake clock
(`now_fn`). Default probes are wrapped so a failure returns
None/empty and simply disables that watcher for that tick — a
broken probe must never raise, and tick() never raises either.
Timeline-only writes (the quiet network_changed line, the hourly
light reading, a held-back break nudge) go through the engine's
`event_writer` seam — core.senses_log.append_event by default,
injectable so tests capture them without touching the real log.
Battery/disk/drives need psutil (a hard dependency, imported lazily
here so this module imports cleanly without it); idle time, the
clipboard, the camera/mic flags and fullscreen detection are
Windows-only calls and are None (watcher off) anywhere else.

Standard library + lazy psutil only; no Qt, no package imports.
"""


import hashlib
import os
import socket
import sys
import threading
import time
import weakref
from datetime import datetime
from pathlib import Path


# INTENTIONAL (skills split B44): file-clipboard-privacy probe
# family moved to core/watchers_file_privacy.py; re-exported here
# so every existing importer and every bare-name use in the facade
# (engine methods, _DEFAULT_PROBES) keeps working. The two mutable
# caches (_privacy_state_cache, _structured_values) are import-time
# snapshots: the live state lives in the privacy module, reached
# through these same function objects; no in-tree reader touches
# the facade copies directly (grep-verified).
from .watchers_file_privacy import (
    _CONSENT_STORE,
    _STRUCTURED_MAX,
    _consent_app_active,
    _probe_ambient_frame,
    _probe_ambient_light_enabled,
    _probe_clipboard,
    _probe_disk,
    _probe_drives,
    _probe_file_sniffer,
    _probe_foreground_enabled,
    _probe_idle_seconds,
    _probe_network_ssid,
    _probe_next_events,
    _probe_privacy,
    _privacy_state_cache,
    _privacy_state_lock,
    _record_structured_value,
    _set_cached_privacy_state,
    _structured_lock,
    _structured_values,
    current_camera_users,
    current_mic_users,
    current_privacy_state,
    structured_value_for,
)


# INTENTIONAL (skills split B45): re-exported from core/watchers_network_system.py
from .watchers_network_system import (
    POLL_SECONDS,
    DISK_SCAN_BUDGET_SECONDS,
    DISK_SCAN_TOP_COUNT,
    _probe_battery,
    _probe_load,
    _human_bytes,
    _display_process_name,
    _scan_top_cpu_once,
    _probe_top_cpu_process,
    _probe_top_memory_process,
    _probe_disk_top_folders,
    _folder_phrase,
    _probe_downloads,
    _probe_network,
    _default_gateway_ip,
    _gateway_reachable,
    _dns_resolves,
    _https_fetch_ok,
    _probe_network_diag,
    diagnose_network_verdict,
)


# INTENTIONAL (skills split B45): re-exported from core/watchers_foreground_mic_camera.py
from .watchers_foreground_mic_camera import (
    _BATT_MIN_SPAN_SECONDS,
    _BATT_ESTIMATE_MAX_MINUTES,
    _CALL_APP_NAMES,
    _MEDIA_APP_NAMES,
    _normalise_process_name,
    _probe_foreground_app,
    _foreground_lock,
    _foreground_cache,
    _set_cached_foreground,
    current_foreground_name,
    current_foreground_call_app,
    current_foreground_media_app,
    estimate_minutes_to_empty,
    _os_estimate_is_wild,
    _probe_fullscreen,
    _DEFAULT_PROBES,
    _live_engine_ref,
    _set_live_engine,
    _clear_live_engine,
    _get_live_engine,
    network_state_snapshot,
    _is_probe_success,
)


# INTENTIONAL (skills split B45): re-exported from core/watchers_health_digest.py
from .watchers_health_digest import (
    watcher_health,
    _NUMBER_WORDS,
    _DIGEST_SKIP_KINDS,
    _count_word,
    _digest_clause,
    _parse_event_ts,
    compose_welcome_digest,
    _default_timeline_events,
    _default_event_writer,
)


# INTENTIONAL (skills split B45): re-exported from core/watchers_engine_lifecycle.py
from .watchers_engine_lifecycle import (
    WatcherEngineLifecycleMixin,
    FAST_POLL_SECONDS,
    SUPPRESS_SECONDS,
)


# INTENTIONAL (skills split B45): re-exported from core/watchers_engine_watchers.py
from .watchers_engine_watchers import (
    WatcherEngineWatchersMixin,
    DOWNLOAD_MIN_AGE_SECONDS,
    _SNIFF_EXTS,
    BATTERY_FORECAST_PERCENT,
    BATTERY_FORECAST_WITHIN_MINUTES,
    BATTERY_FORECAST_REARM_PERCENT,
    _BATT_HISTORY_MAX,
    AMBIENT_LIGHT_INTERVAL_SECONDS,
    BATTERY_LOW_PERCENT,
    BATTERY_CRITICAL_PERCENT,
    BATTERY_FULL_PERCENT,
    REARM_RISE,
    DISK_LOW_FRACTION,
    DISK_REARM_FRACTION,
    IDLE_AWAY_SECONDS,
    IDLE_BACK_SECONDS,
    NETWORK_FAILS_TO_ANNOUNCE,
    NETWORK_FLAP_COUNT,
    NETWORK_FLAP_WINDOW_SECONDS,
    LOAD_CPU_PERCENT,
    LOAD_MEM_PERCENT,
    LOAD_TICKS_TO_ANNOUNCE,
    LOAD_CLEAR_CPU,
    LOAD_CLEAR_MEM,
    CLIPBOARD_LONG_TEXT_CHARS,
    FULLSCREEN_SUGGEST_SECONDS,
    _TEMP_SUFFIXES,
)


# INTENTIONAL (skills split B45): composed class; the original class
# docstring lives here, methods live in the two mixins (B28 pattern).
class WatcherEngine(WatcherEngineLifecycleMixin, WatcherEngineWatchersMixin):
    """Polls the machine and calls on_event(kind, message).

    on_event runs on the engine's thread (or the caller's, for a
    manual tick()) — it must be quick or hand off itself. Exceptions
    inside on_event are caught and printed; they never kill a tick
    or the polling loop. tick() performs ONE poll of every watcher
    and returns the list of (kind, message) events it emitted —
    public so tests can drive it deterministically.
    """

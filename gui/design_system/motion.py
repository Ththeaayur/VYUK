"""Motion tokens (guide §5.5, §6).

All durations in milliseconds unless a name says otherwise. The frame
watchdog drops effects in WATCHDOG_DROP_ORDER when frames run slow;
hidden, occluded or minimised surfaces tick at zero. Blinking never
exceeds BLINK_MAX_HZ. SCAN_WIPE is 120 ms per the §5.5 token table
(the §6 move description's "about 160 ms" is approximate).
"""

# Component / panel / scene transition windows (guide §5.5).
HUD_SNAP_MS = 120
SCAN_WIPE_MS = 120
LOCK_ON_MS = 240
COMPONENT_MIN_MS = 180
COMPONENT_MAX_MS = 240
PANEL_MIN_MS = 220
PANEL_MAX_MS = 320
SCENE_MIN_MS = 320
SCENE_MAX_MS = 480

# Signature-move timings (guide §6).
LABEL_DECODE_MS = 180
STATUS_FLICKER_MS = 80
CURSOR_TRAIL_MS = 400
IRIS_SUMMON_MS = 200
TOAST_MS = 5000

# Frame budget and rates (guide §12).
FRAME_BUDGET_MS = 20
BLINK_MAX_HZ = 3
FPS_IDLE = 12
FPS_ACTIVE = 30
FPS_CAP = 45
FPS_BATTERY_SAVER = 8

# Watchdog degrade order: first dropped first (guide §5.5).
WATCHDOG_DROP_ORDER = ("scanlines", "glow", "radar sweep", "orb detail")

# Chamfer cut applied to panels and dialogs (guide §5.4).
CHAMFER_PX = 8
TICK_PX = 8

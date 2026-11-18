"""Screen 1 Home — the room model (sci-fi build, 2026-11-16).

Qt-free, dependency-free: the light table, the type scale, the
briefing summary, the demotions, the empty-state copy, and the
WCAG contrast math. The window wires this model to the real
state it already knows (status, energy, mute, connection, goal
pill) and to the one glow that already exists — AppShell's ambient
orb glow (``window_widgets_shell.AppShell.set_glow``) — which this
build RETUNES to the table below instead of adding a second light.
There is one lamp in the room, and it is the orb.

Light table (deep doc section 1a, as built):

    error      hold the last wash — a failure must not restyle
               the room around the words that explain it
    muted      2%  (her voice is off; the orb is already dimmed
               and stilled by visualizer.set_muted)
    speaking   4% + 10% x energy  (4-14% — the only state whose
               light moves with the voice)
    listening  8%
    thinking / connecting / setup  6%, held
    busy       8% steady (a goal is running — the pill says so)
    offline    2%  (not connected; distinguished from muted by
               words on the state label, never a second colour)
    idle       4%

Night (22:00-05:59): the wash is scaled by 0.7 and the particle
field's alpha by 0.6. The orb stays Aura at every hour; the canvas
is never darkened as a separate colour, so night is the same room
dimmed, not a different app.

Transitions ease toward the target over 220 ms on the tree's
EASE_STANDARD (OutCubic) — the same easing motion.py uses.
"""

from __future__ import annotations

import datetime
import math

# ------------------------------------------------------------------
# The light table (wash = fraction of the room's full light)
# ------------------------------------------------------------------

WASH_IDLE = 0.04
WASH_LISTENING = 0.08
WASH_THINKING = 0.06
WASH_SPEAKING_MIN = 0.04
WASH_SPEAKING_RANGE = 0.10
WASH_MUTED = 0.02
WASH_OFFLINE = 0.02
WASH_BUSY = 0.08
WASH_MAX = 0.14  # speaking at full energy — the brightest the room gets

# The shell glow's strength parameter at full wash. Kept from the
# pre-build behaviour (a loud reply drove the shell to 0.85), so
# the brightest the room ever gets is unchanged — only the lower
# states are retuned to the table.
SHELL_STRENGTH_AT_FULL_WASH = 0.85

NIGHT_FACTOR = 0.7
PARTICLE_NIGHT_FACTOR = 0.6
NIGHT_START_HOUR = 22  # 22:00-05:59 is night
NIGHT_END_HOUR = 6
TRANSITION_MS = 220  # DURATION_PAGE_MS — the tree's page easing

LIGHT_TABLE = {
    "error": None,  # hold the last wash
    "muted": WASH_MUTED,
    "speaking": (WASH_SPEAKING_MIN, WASH_SPEAKING_MIN + WASH_SPEAKING_RANGE),
    "listening": WASH_LISTENING,
    "thinking": WASH_THINKING,
    "busy": WASH_BUSY,
    "offline": WASH_OFFLINE,
    "idle": WASH_IDLE,
}

# ------------------------------------------------------------------
# The type scale (ratio 1.2; sizes in px, families are the bundled
# OFL trio — Sora body, Orbitron display-only, Share Tech Mono
# for data)
# ------------------------------------------------------------------

# name -> (size px, family, role)
# UI rethink Build 1 (2026-11-16): the scale reassigns by the plan —
# the greeting steps 28 -> 20 (the title rung, Orbitron, still the
# room's one display line), the briefing summary steps 19 -> 13
# (body), the wordmark steps 20 -> 13 (a Sora caption, muted). The
# 28 and 19 tokens stay defined in style_tokens (Activity still
# uses them; Build 2 deletes them) — this table only reassigns
# what Home itself uses.
TYPE_SCALE = {
    "display": (20, "Orbitron", "the greeting, and nothing else"),
    "lead": (13, "Sora", "the one-line briefing summary"),
    "title": (13, "Sora", "the VYUK wordmark under the orb"),
    "body": (13, "Sora", "state line, caption, card text"),
    "caption": (11.5, "Sora", "muted captions and hints"),
    "micro": (10.5, "Sora", "timestamps and eyebrow microcopy"),
    "data": (13, "Share Tech Mono", "the live vitals readout"),
}

# ------------------------------------------------------------------
# Empty-state copy (VYUK's voice: calm, plain words). These are
# the sentences the room already spoke, kept verbatim — the
# review session judges the voice, not a new draft.
# ------------------------------------------------------------------

ROOM_COPY = {
    # The briefing schedule, when there is nothing on it.
    "empty_schedule": "Nothing scheduled today — ask me to add something.",
    # The next-reminder line, when there is no next reminder.
    "empty_next": "No reminders coming up",
    # The idle state line (wake on). The wake-off line is longer
    # and lives on the state label itself.
    "idle": "Ready when you are",
    # The caption before the first connection.
    "caption_before_connect": "Press Connect, then just talk.",
}

# ------------------------------------------------------------------
# Demotions (parent ruling 9: never silently delete a feature —
# every demotion records where the thing went)
# ------------------------------------------------------------------

# (element, was, now, way back)
DEMOTIONS = (
    ("home strip (date / next reminder / Play briefing)",
     "its own card above the stage",
     "removed — the date already reads in the vitals line, the "
     "next reminder already reads in the briefing schedule",
     "Play briefing moved into the briefing card's header row; "
     "the date and the next reminder are unchanged in their "
     "existing lines"),
    ("voice / language chip",
     "a chip above the orb, always visible",
     "hidden (kept as a render target, mic-health-dot precedent: "
     "the widget still exists, is still wired, and stays in the "
     "tab-order chain — Qt skips it at runtime because it is hidden)",
     "the overflow menu (···) gains a \"Voice & language…\" item "
     "opening the same menu; Settings → Voice keeps the combos"),
    ("briefing card",
     "five rows always open",
     "one line (the summary) with the five rows behind one tap "
     "on the chevron",
     "the chevron in the card's own header row expands the same "
     "five rows in place"),
    ("voice pace chips + activity sparkline",
     "always visible under the briefing card",
     "visible only while she is speaking (the same condition as "
     "the transport row)",
     "the pace itself lives in Settings → the Brain card's speed "
     "slider, which the chips already drove; voice (\"speak "
     "slower\") works too. The sparkline's proper home is the "
     "Activity page (Screen 3) — recorded as a known imperfection "
     "until then"),
    ("mic level meter",
     "always visible under the briefing card",
     "visible only while listening",
     "the orb carries the level while she speaks; the mic-health "
     "dot in the overflow menu keeps the drop counter"),
    ("sidebar privacy card",
     "a blue-gradient card in the sidebar (a second blue, and a "
     "card competing with the orb)",
     "one muted line at the foot of the sidebar: the words "
     "\"Private by design\" with the full promise on hover",
     "the promise sentence itself moved verbatim to Settings → "
     "Privacy & security, where the standing switches live"),
)


# ------------------------------------------------------------------
# State mapping — from the live state the app already knows
# ------------------------------------------------------------------

def state_from(status_raw, *, connected, in_flight, muted, busy,
               fatal):
    """The room's light state for the current app state.

    Priority is the doc's: an error holds the room (the words
    explain it); a muted mic outranks speaking (the orb is
    already dimmed by set_muted); speaking follows the voice;
    listening, thinking/connecting/setup, a running goal, and
    offline follow in order; idle is the rest.
    """
    upper = (status_raw or "").upper()
    if fatal or "ERROR" in upper:
        return "error"
    if muted:
        return "muted"
    if "SPEAKING" in upper:
        return "speaking"
    if "LISTENING" in upper:
        return "listening"
    if ("THINKING" in upper or "CONNECTING" in upper
            or "SETUP" in upper or in_flight):
        return "thinking"
    if busy:
        return "busy"
    if not connected:
        return "offline"
    return "idle"


def is_night(hour=None):
    """True between 22:00 and 05:59. ``hour`` is 0-23; None reads
    the clock."""
    if hour is None:
        hour = datetime.datetime.now().hour
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def night_factor(hour=None):
    """The wash scale at this hour: 0.7 at night, 1.0 by day."""
    return NIGHT_FACTOR if is_night(hour) else 1.0


def wash_for(state, energy=0.0, hour=None):
    """The target wash (0-1) for a light state, night-scaled.

    ``state`` is one of the LIGHT_TABLE keys; ``energy`` is the
    voice level 0-1 and only moves the speaking wash. The error
    state has no target — the caller holds its last wash.
    """
    if state == "error":
        return None
    if state == "speaking":
        try:
            energy = max(0.0, min(1.0, float(energy or 0.0)))
        except (TypeError, ValueError):
            energy = 0.0
        base = WASH_SPEAKING_MIN + WASH_SPEAKING_RANGE * energy
    else:
        base = LIGHT_TABLE.get(state, WASH_IDLE)
    return base * night_factor(hour)


def shell_strength(wash):
    """Map a wash fraction to the AppShell glow strength.

    Linear, anchored so the brightest the room ever gets (full
    speaking wash) is the pre-build loud-speaking strength —
    the retune dims the lower states, it never brightens the top.
    """
    if wash is None:
        return None
    return max(0.0, min(1.0, wash / WASH_MAX * SHELL_STRENGTH_AT_FULL_WASH))


def out_cubic(t):
    """EASE_STANDARD (OutCubic): 1 - (1 - t)^3, t clamped 0-1."""
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


class RoomLight:
    """The room's light, eased toward its target.

    ``current`` is the wash being shown right now. ``step`` moves
    it toward the target over TRANSITION_MS on OutCubic; an error
    target holds the current wash (no step at all). The window
    calls :meth:`update` once per telemetry tick with the live
    state and the tick's dt, and reads ``current`` for the shell.
    """

    def __init__(self):
        self.current = WASH_IDLE
        self.state = "idle"
        self._from = WASH_IDLE
        self._to = WASH_IDLE
        self._elapsed_ms = TRANSITION_MS  # settled at rest

    def target(self, state, energy=0.0, hour=None):
        """The target wash for this state, or None for error."""
        return wash_for(state, energy=energy, hour=hour)

    def update(self, state, energy=0.0, hour=None, dt_ms=25.0):
        """Advance toward the target for one tick; return the wash
        to show. Error holds the last wash exactly."""
        target = self.target(state, energy=energy, hour=hour)
        if target is None:
            self.state = state
            self._elapsed_ms = TRANSITION_MS
            return self.current
        if target != self._to or self.state != state:
            self._from = self.current
            self._to = target
            self._elapsed_ms = 0.0
            self.state = state
        return self.step(dt_ms)

    def step(self, dt_ms=25.0):
        """Ease one tick toward the pending target."""
        if self._elapsed_ms >= TRANSITION_MS:
            self.current = self._to
            return self.current
        self._elapsed_ms += dt_ms
        progress = min(1.0, self._elapsed_ms / TRANSITION_MS)
        self.current = (self._from
                        + (self._to - self._from) * out_cubic(progress))
        return self.current


# ------------------------------------------------------------------
# Briefing summary — the one line the collapsed card shows
# ------------------------------------------------------------------

def briefing_summary_for(schedule_text):
    """The collapsed card's one line, derived from the schedule
    line the card already renders.

    The schedule line is clauses joined by " · " ("3 things coming
    up · Standup 09:00 · Reminders: Call bank"). The summary keeps
    the first two clauses and an ellipsis when there are more — the
    count and the first thing, which is what the line is for. One
    or two clauses, and the empty / loading / failure lines, pass
    through verbatim.
    """
    text = (schedule_text or "").strip()
    if not text:
        return ROOM_COPY["empty_schedule"]
    clauses = [c.strip() for c in text.split(" · ") if c.strip()]
    if len(clauses) <= 2:
        return " · ".join(clauses)
    return " · ".join(clauses[:2]) + " …"


# ------------------------------------------------------------------
# Contrast — WCAG, local so this module stays dependency-free.
# Colours arrive as hex strings from the token source (the caller
# passes them in); no colour literal lives in this file.
# ------------------------------------------------------------------

def _channel_luminance(channel):
    """sRGB channel (0-255) to linear light."""
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour):
    """Relative luminance of a hex colour (``#RRGGBB``)."""
    value = hex_colour.lstrip("#")
    r = int(value[0:2], 16)
    g = int(value[2:4], 16)
    b = int(value[4:6], 16)
    return (0.2126 * _channel_luminance(r)
            + 0.7152 * _channel_luminance(g)
            + 0.0722 * _channel_luminance(b))


def contrast_ratio(foreground, background):
    """WCAG contrast ratio between two hex colours."""
    light = luminance(foreground)
    dark = luminance(background)
    if light < dark:
        light, dark = dark, light
    return (light + 0.05) / (dark + 0.05)

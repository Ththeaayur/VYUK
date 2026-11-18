"""
Watch-my-screen mode for VYUK (v5.45, EYES E6).

An explicit, opt-in mode — OFF by default, never armed by a
launch, a setting restore, or a session start. While it is
active the window captures the (E7-cropped) screen every few
seconds, diffs each frame against the last frame actually SENT
to the brain, and only sends when more than CHANGE_THRESHOLD
(2%) of pixels changed, asking "what changed on my screen?".
Sends are rate-limited (MIN_SEND_INTERVAL_S) so a playing video
cannot flood the brain with a turn every poll.

NOTHING IS WRITTEN TO DISK. Frames live in memory only, as the
bytes of the last sent frame; stop() drops even that. This is
the deliberate contrast with the screen_diff TOOL
(core/skills_window_control), which saves a baseline PNG to data/ —
that tool is a user-invoked one-shot compare; this mode is a
continuous watcher, so it keeps its baseline in RAM and throws
it away at session end. The diff MATH is the tool's own
technique, reused: downscale to 480 px wide, greyscale, a
per-pixel threshold of 30 grey levels (skills_window_control's
_DIFF_THRESHOLD), fraction of pixels over it.

Ownership: the mode is GUI-OWNED state. The window
(gui/main_window) owns the ScreenWatcher engine, drives it from
a QTimer, renders the "Watching your screen" pill, and disables
the mode at session end / on disconnect. This module holds:

  * the engine (ScreenWatcher — capture/deliver are injected
    callables, so the window wires vision_manager.capture_screen
    and its own brain delivery, and suites wire fakes);
  * the process-wide switchboard (set_watch_enabled /
    watch_enabled / subscribe) the Settings switch, the pill and
    the voice surface all read and write, so every surface
    agrees on one state;
  * the voice-command recogniser (match_watch_command).

Voice control — investigated, wired honestly: there is NO
app_setting-style tool in the registry (all 477 declarations
were checked: settings tools are Windows-OS settings only), and
the registry is frozen for this wave, so the model cannot flip
this mode with a tool call and no new tool was invented. Voice
rides the surface the GUI already exposes for the user's own
words: the window's _on_transcript receives every genuine user
utterance (spoken and typed) exactly once, and already reacts
to them (it clears the briefing pace override, learns from
them). The window matches explicit watch phrases there via
match_watch_command and reads THIS switchboard; the sessions
read the same state through watch_enabled().

v5.48 (programme item 21) — ARMING RULE CHANGED: voice can no
longer ARM watch mode. Arming a continuous screen watcher is a
privacy-sensitive opt-in that must come from a deliberate
on-screen act — the Settings switch / on-screen control — not
from speech, which a TV, a video, or another person in the room
could supply. match_watch_command therefore returns None for
arm phrases (not a command at all, as far as the transcript
surface is concerned); DISARM phrases still return False, so
"stop watching my screen" by voice always works — stopping a
capture must be at least as easy as starting one was.
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable

CHANGE_THRESHOLD = 0.02        # >2% of pixels changed -> send
POLL_INTERVAL_S = 3.0          # capture cadence while active (2-5 s)
MIN_SEND_INTERVAL_S = 20.0     # min gap between brain-bound frames
_DIFF_SMALL_WIDTH = 480        # skills_window_control's diff working width

WATCH_FRAMING = (
    "What changed on my screen since the last frame you saw? "
    "Mention only what changed; if nothing meaningful changed, "
    "say so in a few words."
)
"""The framing every watch send rides on (brain-bound text)."""

PILL_TEXT = "Watching your screen"


def frame_change_fraction(previous: bytes, current: bytes) -> float | None:
    """Fraction of pixels that changed between two JPEG frames,
    in memory — skills_window_control.screen_diff's math (480-wide
    greyscale, per-pixel threshold from the tool) without its
    on-disk baseline. A size difference compares at the previous
    frame's size, like the tool. None when either frame can't be
    decoded (the caller treats unknown as "do not send")."""
    if not previous or not current:
        return None
    try:
        from PIL import Image, ImageChops, ImageStat

        from ..skills_window_control import _DIFF_THRESHOLD  # Phase 3 D3: file now in core/desktop/; skills stay flat in core/

        prev = Image.open(io.BytesIO(previous)).convert("RGB")
        curr = Image.open(io.BytesIO(current)).convert("RGB")
        if curr.size != prev.size:
            curr = curr.resize(prev.size)
        scale = min(1.0, _DIFF_SMALL_WIDTH / prev.width)
        small = (max(3, int(prev.width * scale)),
                 max(3, int(prev.height * scale)))
        prev_small = prev.convert("L").resize(small)
        curr_small = curr.convert("L").resize(small)
        diff = ImageChops.difference(prev_small, curr_small)
        mask = diff.point(lambda p: 255 if p > _DIFF_THRESHOLD else 0)
        return ImageStat.Stat(mask).mean[0] / 255.0
    except Exception:  # noqa: BLE001 — an unreadable frame is not a change
        return None


# ---------------------------------------------------------------------- #
# The switchboard: one process-wide state every surface agrees on
# ---------------------------------------------------------------------- #

class _WatchControl:
    """GUI-owned watch-mode state + change listeners.

    Listeners are called synchronously with the new bool on every
    CHANGE (the window syncs its pill, its Settings checkbox and
    its engine there). Thread-safe: the Settings switch runs on
    the GUI thread, the transcript recogniser too, but the lock
    costs nothing and keeps the contract simple."""

    def __init__(self):
        self._lock = threading.Lock()
        self._enabled = False
        self._listeners: list[Callable[[bool], None]] = []

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, on: bool, source: str = "") -> bool:
        """Sets the mode; returns True when the state CHANGED.
        `source` ("settings" / "voice" / "session-end" / …) is
        provenance for the caller's log line — the state itself
        doesn't care who flipped it."""
        with self._lock:
            on = bool(on)
            if on == self._enabled:
                return False
            self._enabled = on
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(on)
            except Exception:  # noqa: BLE001 — one bad listener
                pass  # must not wedge the switchboard
        return True

    def subscribe(self, fn: Callable[[bool], None]) -> None:
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)


CONTROL = _WatchControl()


def watch_enabled() -> bool:
    """The one question every surface asks: is watch mode on?"""
    return CONTROL.enabled


def set_watch_enabled(on: bool, source: str = "") -> bool:
    """Flips the mode through the switchboard (see _WatchControl)."""
    return CONTROL.set_enabled(on, source)


def subscribe_watch(fn: Callable[[bool], None]) -> None:
    CONTROL.subscribe(fn)


def pill_state(enabled: bool | None = None) -> tuple[bool, str]:
    """(visible, text) for the main window's watch pill: visible
    exactly while the mode is active, always the same words.
    Pure — the suite pins it, the window renders it."""
    if enabled is None:
        enabled = CONTROL.enabled
    return (bool(enabled), PILL_TEXT if enabled else "")


# ---------------------------------------------------------------------- #
# Voice surface: explicit watch phrases in the user's own words
# ---------------------------------------------------------------------- #

_STOP_PHRASES = (
    "stop watching my screen",
    "stop watching the screen",
    "stop watching",
    "don't watch my screen",
    "do not watch my screen",
    "stop keeping an eye on my screen",
    "stop keeping watch",
    "watch my screen off",
    "turn off watch my screen",
    "stop screen watching",
)

_START_PHRASES = (
    "watch my screen",
    "watch the screen",
    "watch my screen for me",
    "keep an eye on my screen",
    "keep watching my screen",
    "start watching my screen",
    "start watching the screen",
    "monitor my screen",
    "watch over my screen",
)


def match_watch_command(text: str) -> bool | None:
    """False = the user explicitly asked her to STOP watching,
    None = not a (dis)arm command. Deliberately tight phrase
    lists (a privacy mode must never react to a near-miss like
    "watch a film on my screen"); stop phrases are the only
    phrases matched at all.

    v5.48 (programme item 21): START phrases no longer return
    True — voice cannot arm watch mode (see the module
    docstring: arming requires the Settings switch / on-screen
    control). The _START_PHRASES list is kept for reference and
    for the GUI's honest "turn it on in Settings" reply copy,
    but matching one is explicitly NOT a command: it falls
    through to None exactly like unrelated text."""
    if not text:
        return None
    lowered = " ".join(str(text).lower().split())
    for phrase in _STOP_PHRASES:
        if phrase in lowered:
            return False
    # v5.48 (programme item 21): arm phrases intentionally fall
    # through — no return True exists in this function anymore.
    return None


# ---------------------------------------------------------------------- #
# The engine: poll -> diff against last SENT -> maybe send
# ---------------------------------------------------------------------- #

class ScreenWatcher:
    """The watch loop's brain, driven by the window's timer.

    Injected seams (the window wires the real ones, suites wire
    fakes):
      capture() -> (jpeg_bytes, mime) | None
      deliver(jpeg_bytes, mime, framing) -> None  (one brain turn)

    Memory only: the last sent frame's bytes are the baseline;
    stop() drops them. tick() returns a short outcome string for
    logs/tests: "off", "waiting" (poll throttle), "no-frame",
    "unreadable", "sent", "quiet" (<= threshold),
    "rate-limited" (changed, but the min send interval hasn't
    elapsed — the baseline stays the last SENT frame, so the
    change is still measured honestly next poll), or
    "blocked-sensitive" (v5.48, programme item 21: the
    foreground window is on the capture denylist, so the frame
    was SKIPPED — never captured, never sent; the skip is
    recorded on last_event / last_block_reason). Every outcome
    is also recorded on last_event — the watch status surface
    the window and the suites read."""

    def __init__(self,
                 capture: Callable[[], tuple[bytes, str] | None],
                 deliver: Callable[[bytes, str, str], None],
                 now: Callable[[], float] = time.monotonic,
                 poll_interval_s: float = POLL_INTERVAL_S,
                 min_send_interval_s: float = MIN_SEND_INTERVAL_S,
                 change_threshold: float = CHANGE_THRESHOLD):
        self._capture = capture
        self._deliver = deliver
        self._now = now
        self.poll_interval_s = float(poll_interval_s)
        self.min_send_interval_s = float(min_send_interval_s)
        self.change_threshold = float(change_threshold)
        self.active = False
        self.sends = 0
        self.last_change_fraction: float | None = None
        # v5.48 (programme item 21): the watch status surface —
        # last_event carries the most recent tick outcome, and
        # last_block_reason the denylist reason when that outcome
        # was "blocked-sensitive" (else None).
        self.last_event: str | None = None
        self.last_block_reason: str | None = None
        self._last_sent_frame: bytes | None = None
        self._last_sent_at = 0.0
        self._last_poll_at: float | None = None

    def start(self):
        """Arms the watcher with NO baseline: the first captured
        frame is sent (her first look), then becomes the
        baseline every later frame diffs against."""
        self.active = True
        self._last_sent_frame = None
        self._last_sent_at = 0.0
        self._last_poll_at = None
        self.last_change_fraction = None
        self.last_event = None
        self.last_block_reason = None

    def stop(self):
        """Disarms and DROPS the baseline frame — session end
        leaves nothing of the watched screen behind."""
        self.active = False
        self._last_sent_frame = None
        self._last_poll_at = None

    def _outcome(self, value: str) -> str:
        """Record a tick outcome on the status surface
        (last_event) and return it. A non-blocked outcome clears
        last_block_reason — the reason describes the LAST skip,
        and only while the skip is the latest event."""
        self.last_event = value
        if value != "blocked-sensitive":
            self.last_block_reason = None
        return value

    def tick(self, now: float | None = None) -> str:
        if not self.active:
            return self._outcome("off")
        moment = self._now() if now is None else float(now)
        if (self._last_poll_at is not None
                and moment - self._last_poll_at < self.poll_interval_s):
            return self._outcome("waiting")
        self._last_poll_at = moment
        # v5.48 (programme item 21): the sensitive-window
        # denylist is checked PER POLL, before the capture seam
        # is touched — a denylisted foreground (password
        # manager, banking page, private window) means this
        # frame is skipped entirely: not captured, not diffed,
        # not sent. The baseline stays the last SENT frame, so
        # the first frame after the sensitive window leaves is
        # still measured honestly against what the brain saw.
        try:
            from .. import capture_guard  # Phase 3 D3: file now in core/desktop/; capture_guard stays flat in core/

            blocked = capture_guard.foreground_capture_block()
        except Exception:  # noqa: BLE001 — a guard failure must
            blocked = None  # never wedge the watcher
        if blocked is not None:
            self.last_block_reason = blocked
            return self._outcome("blocked-sensitive")
        try:
            captured = self._capture()
        except Exception:  # noqa: BLE001 — a failed grab is a skipped poll
            return self._outcome("no-frame")
        if not captured:
            return self._outcome("no-frame")
        frame, mime = captured
        if not frame:
            return self._outcome("no-frame")
        if self._last_sent_frame is None:
            self._send(frame, mime, moment)
            return self._outcome("sent")
        fraction = frame_change_fraction(self._last_sent_frame, frame)
        if fraction is None:
            return self._outcome("unreadable")
        self.last_change_fraction = fraction
        if fraction <= self.change_threshold:
            return self._outcome("quiet")
        if moment - self._last_sent_at < self.min_send_interval_s:
            return self._outcome("rate-limited")
        self._send(frame, mime, moment)
        return self._outcome("sent")

    def _send(self, frame: bytes, mime: str, moment: float):
        self._deliver(frame, mime, WATCH_FRAMING)
        self._last_sent_frame = frame
        self._last_sent_at = moment
        self.sends += 1

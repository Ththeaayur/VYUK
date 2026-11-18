"""Wave 3 (v5.58) sound-event watcher registration / bus integration.

This is the watcher-registration file the sidecar workstream owns.
It does NOT edit core/watchers.py: WatcherEngine's tick list is
fixed, and another worker may own that file. Instead this module
bridges the sidecar onto the SAME bus contract the engine uses —
`on_event(kind, message)` with plain, speakable sentences — so
the window's existing watcher-event path (chat line always, speech
only while connected, throttled) can consume sound events without
any change to the engine, and so a coordinator/GUI worker can wire
one line:

    bridge = register_sound_event_watcher(
        on_event=lambda kind, message: window.signals.watcher_event.emit(kind, message),
        sidecar=sidecar)
    bridge.start()   # only after the sidecar's per-session opt-in

The bridge is a thin adapter, not a second detector: it owns no
thresholds, no cooldowns, no mic stream — all of that lives in
core/sound_event_sidecar.py, which is the only place sound-event
policy lives. The bridge's jobs are (1) translating a SoundEvent
into the bus's (kind, message) pair, (2) optionally writing the
same timeline-only record the engine writes for its quiet events
(via an injected event_writer, same (kind, message, value, unit)
shape as core/watchers' event_writer seam), and (3) exposing the
sidecar's running/paused state for Settings/Doctor surfaces to
read without touching the sidecar's internals.

Kind: "sound_event" (singular, matching the engine's singular
kinds like "download_done"). Messages are the sidecar's plain
sentences ("I heard the doorbell."), never scores or model names.

Standard library only; no Qt, no new dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sound_event_sidecar import SoundEvent, SoundEventSidecar

# The bus kind for every sound event, matching WatcherEngine's
# singular-kind convention.
SOUND_EVENT_KIND = "sound_event"


@dataclass
class SoundEventWatcherBridge:
    """Adapter from a SoundEventSidecar onto the watcher bus."""

    sidecar: SoundEventSidecar
    on_event: object = None  # fn(kind, message) — the engine's bus contract
    event_writer: object = None  # fn(kind, message, value, unit) | None

    def __post_init__(self):
        # The sidecar's own on_event seam becomes this bridge's
        # translator. A sidecar with an existing listener keeps it
        # only if the caller chained it; the bridge is the bus
        # listener by construction, so it replaces, not wraps.
        self.sidecar.on_event = self._deliver

    def _deliver(self, event: SoundEvent) -> None:
        if self.event_writer is not None:
            try:
                self.event_writer(SOUND_EVENT_KIND, event.message,
                                  event.score, "score")
            except Exception:  # noqa: BLE001 — timeline writes never sink delivery
                pass
        if self.on_event is not None:
            try:
                self.on_event(SOUND_EVENT_KIND, event.message)
            except Exception:  # noqa: BLE001 — a listener never kills listening
                pass

    def start(self, stream_factory=None) -> tuple[bool, str]:
        """Start the sidecar (opt-in gated inside the sidecar).
        Returns the sidecar's (ok, message) unchanged, so a
        refusal reads exactly like the sidecar's refusal."""
        return self.sidecar.start(stream_factory=stream_factory)

    def stop(self) -> None:
        self.sidecar.stop()

    @property
    def is_running(self) -> bool:
        return self.sidecar.is_running

    @property
    def is_paused_denylisted(self) -> bool:
        return self.sidecar.is_paused_denylisted

    def status(self) -> dict:
        """One plain dict for Settings/Doctor surfaces."""
        return {
            "kind": SOUND_EVENT_KIND,
            "running": self.is_running,
            "paused_denylisted": self.is_paused_denylisted,
            "opt_in_granted": self.sidecar.opt_in.is_granted(),
            "opt_in_satisfied": self.sidecar.opt_in.satisfied(),
            "indicator_kind": "sound_events",
        }


def register_sound_event_watcher(on_event, sidecar: SoundEventSidecar,
                                 event_writer=None) -> SoundEventWatcherBridge:
    """Build (not start) the bridge. Starting is a separate,
    deliberate call — constructing a bridge must never open the
    microphone, with or without an opt-in."""
    return SoundEventWatcherBridge(
        sidecar=sidecar, on_event=on_event, event_writer=event_writer)

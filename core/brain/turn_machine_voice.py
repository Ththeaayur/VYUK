from __future__ import annotations

# INTENTIONAL (skills split B20): voice family (VP-8.1 failover, VP-8.2 media ducking) moved here verbatim;
# the facade re-exports every name below.
import threading

# ---------------------------------------------------------------------------
# VP-8.1 — announced voice failover (Charon <-> George)
# ---------------------------------------------------------------------------

#: The two voices in the failover pair. Charon is the Live
#: (cloud) voice; George is the on-device voice (the user's
#: pick, v5.40). Failover moves BETWEEN them; it never
#: converts one into the other — voice conversion is
#: REJECTED inside VP-8.1 and no conversion code exists here.
VOICE_LIVE = "Charon"
VOICE_ON_DEVICE = "George"

FAILOVER_ANNOUNCEMENT_TEMPLATE = (
    "My voice is switching to {voice} for this session — same "
    "assistant, same conversation. I'll pick up where I left off.")


class VoiceFailoverGate:
    """Announced failover, never mid-sentence (VP-8.1, §10 T16).

    Rules:
    - Every applied failover produces an announcement (100%
      announced — the caller posts the returned string in chat
      and, when a live voice is available, speaks it BEFORE
      the switch takes effect for the next sentence).
    - A failover requested mid-sentence is QUEUED, not
      applied: it waits for the turn boundary
      (``note_turn_boundary``), so there are zero
      mid-sentence voice switches by construction.
    - The announcement names the new voice in plain words;
      it never claims the voice is the same person or hides
      the switch.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.current_voice = VOICE_LIVE
        self._pending_voice: str | None = None
        self.announced_count = 0
        self.mid_sentence_switches = 0

    def request_failover(self, to_voice: str, *, mid_sentence: bool = False) -> dict:
        """Request a switch. Returns a dict with ``action``:
        ``"announce_and_switch"`` (apply now, post
        ``announcement``), ``"queued"`` (mid-sentence — apply
        at the next turn boundary), or ``"noop"`` (already on
        that voice). A queued request never switches early."""
        target = str(to_voice or "").strip()
        if target not in (VOICE_LIVE, VOICE_ON_DEVICE):
            return {"action": "noop", "announcement": "",
                    "voice": self.current_voice}
        with self._lock:
            if target == self.current_voice and self._pending_voice is None:
                return {"action": "noop", "announcement": "",
                        "voice": self.current_voice}
            if mid_sentence:
                self._pending_voice = target
                return {"action": "queued", "announcement": "",
                        "voice": self.current_voice}
            self.current_voice = target
            self._pending_voice = None
            self.announced_count += 1
        return {"action": "announce_and_switch",
                "announcement": FAILOVER_ANNOUNCEMENT_TEMPLATE.format(
                    voice=target),
                "voice": target}

    def note_turn_boundary(self) -> dict:
        """A turn ended: apply any queued failover now, with
        its announcement. Returns ``{"action": "none"}`` when
        nothing was queued."""
        with self._lock:
            pending = self._pending_voice
            if pending is None:
                return {"action": "none", "announcement": "",
                        "voice": self.current_voice}
            self.current_voice = pending
            self._pending_voice = None
            self.announced_count += 1
        return {"action": "announce_and_switch",
                "announcement": FAILOVER_ANNOUNCEMENT_TEMPLATE.format(
                    voice=pending),
                "voice": pending}

    def pending_voice(self) -> str | None:
        with self._lock:
            return self._pending_voice

    def stats(self) -> dict:
        with self._lock:
            return {
                "current_voice": self.current_voice,
                "pending_voice": self._pending_voice,
                "announced_failovers": self.announced_count,
                "mid_sentence_switches": self.mid_sentence_switches,
            }


# ---------------------------------------------------------------------------
# VP-8.2 — defensive media ducking
# ---------------------------------------------------------------------------

#: Level owned media ducks to while she speaks (1.0 = full).
MEDIA_DUCKED_LEVEL = 0.35
MEDIA_FULL_LEVEL = 1.0


class MediaDucking:
    """Duck only media SHE started; never a call (VP-8.2).

    The caller registers each playback SHE started (music,
    video, podcast) with :meth:`register`; anything she did
    not start is unknown to this object and therefore never
    ducked, and kind ``"call"`` is never ducked even if
    registered — a call is someone else's live conversation,
    not background media. Unregistered streams always read
    full level, so a missing registration degrades to "don't
    touch it", never to ducking the wrong thing.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._owned: dict[str, str] = {}

    def register(self, stream_id: str, *, kind: str = "media") -> None:
        if not stream_id:
            return
        with self._lock:
            self._owned[str(stream_id)] = str(kind or "media")

    def unregister(self, stream_id: str) -> None:
        with self._lock:
            self._owned.pop(str(stream_id), None)

    def level_for(self, stream_id: str, *, assistant_speaking: bool) -> float:
        """Playback level for a stream. Full level unless
        (a) she is speaking, (b) the stream is registered as
        hers, and (c) it is not a call."""
        if not assistant_speaking:
            return MEDIA_FULL_LEVEL
        with self._lock:
            kind = self._owned.get(str(stream_id))
        if kind is None or kind == "call":
            return MEDIA_FULL_LEVEL
        return MEDIA_DUCKED_LEVEL

    def owned_stream_ids(self) -> list[str]:
        with self._lock:
            return list(self._owned)



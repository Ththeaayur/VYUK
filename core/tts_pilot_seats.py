"""Wave 3 (v5.58) TTS bake-off seats — PILOTS, never residents.

A "seat" here is a bake-off entry on the Wave 2 rig pattern: a
recorded licence verdict, a disabled-by-default switch, and an
injectable backend. Nothing in this module downloads, loads, or
bundles a model, and nothing here touches the resident TTS pipeline
(Kokoro via core/voice/local_client.py): there is no wiring from any seat
into speech output, no tool, and no declaration growth. Promotion is
impossible by code — `promote()` always refuses, because promotion
needs a measured per-language co-residence budget (plan §8 B-23)
and INV-21 accounting, neither of which a pilot module can grant.

Seats:
- PR-9 Chatterbox — licence CLEAR (MIT, see core/pilot_licences_
  v558.py). Seat exists, backend injectable, OFF by default. Its
  PerTh watermark is disclosed, never stripped: the disclosure
  string below is written for docs/config text and for the GUI
  worker to surface on the existing disclosure surface; this
  module edits no GUI file.
- VA-3 MOSS-TTS-Nano — licence STILL-OWED (O-6 unresolved, see
  pilot_licences_sound_translation.py). The seat is a BLOCKED record only: no
  backend slot that could run, no download, no weights. Building
  an inference path for it now would ship a pilot on an
  unverified licence, which the commercial standing law forbids.
- KittenTTS — deliberately ABSENT. Its seat is FROZEN (licence
  unread, Wave 4 comparison freeze); it is named only in
  FROZEN_SEATS so a test can prove it was not smuggled in as a
  seat, and nowhere else in this module is there KittenTTS code.

Standard library only; no Qt, no new dependencies, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The PerTh disclosure, in plain language (v5.57 copy-premium
# standard: every string reads premium). A pilot seat's output is
# synthetic speech; the listener-facing fact is that it is
# detectably synthetic, not the mechanism's brand name alone.
CHATTERBOX_WATERMARK_DISCLOSURE = (
    "Voices from the Chatterbox pilot carry Resemble AI's PerTh "
    "watermark: an imperceptible mark embedded in every generated "
    "clip that survives ordinary re-encoding, so the audio remains "
    "detectably synthetic. The watermark is part of the model and "
    "is never removed."
)

# Seats frozen by the plan — named here ONLY so tests can assert
# they have no seat object. No code for these seats exists.
FROZEN_SEATS: tuple[str, ...] = ("KittenTTS",)

# Promotion is a coordinator decision with a measured budget, not
# something a pilot can do to itself.
_PROMOTION_REFUSAL = (
    "Bake-off seats are never promoted by code: promotion needs a "
    "measured per-language co-residence budget (plan §8 B-23) and "
    "INV-21 accounting, granted by the coordinator, not by a seat."
)


@dataclass
class TtsPilotSeat:
    """One bake-off seat. `backend` is an injected callable
    (text, voice_ref) -> audio bytes (or None); the seat itself
    never imports a TTS library, so "no backend" is the honest
    default and synthesis without one refuses instead of
    falling back to the resident voice."""

    seat_id: str
    engine: str
    licence: str
    licence_clear: bool
    blocked_reason: str = ""
    watermark_disclosure: str = ""
    enabled: bool = False
    backend: object = field(default=None, repr=False)

    def available(self) -> bool:
        """True only when the licence is clear AND a backend was
        injected AND the seat was explicitly enabled. A blocked
        seat (MOSS) is never available, whatever is injected."""
        return bool(self.licence_clear and self.backend is not None
                    and self.enabled)

    def enable(self) -> bool:
        """Explicit pilot enable. Refuses for blocked seats."""
        if not self.licence_clear:
            return False
        self.enabled = True
        return True

    def disable(self) -> None:
        self.enabled = False

    def promote(self) -> tuple[bool, str]:
        """Always refuses — see the module docstring."""
        return False, _PROMOTION_REFUSAL

    def synthesise(self, text: str, voice_ref: str | None = None,
                   ) -> tuple[bytes | None, str]:
        """(audio, note). Refuses unless available(); a refusal
        never falls back to the resident voice, so a pilot can
        never silently become the voice the user hears."""
        if not self.licence_clear:
            return None, (
                f"{self.engine} pilot is blocked: "
                f"{self.blocked_reason}")
        if not self.enabled:
            return None, f"{self.engine} pilot is disabled by default."
        if self.backend is None:
            return None, (
                f"{self.engine} pilot has no backend injected — "
                "no model is bundled with this seat.")
        try:
            audio = self.backend(str(text), voice_ref)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 — a pilot must not raise
            return None, f"{self.engine} pilot backend failed: {exc}"
        if not isinstance(audio, (bytes, bytearray)):
            return None, f"{self.engine} pilot backend returned no audio."
        note = self.watermark_disclosure or ""
        return bytes(audio), note


def chatterbox_seat(backend=None) -> TtsPilotSeat:
    """PR-9 Chatterbox: MIT-verified, pilot-only, OFF by default."""
    return TtsPilotSeat(
        seat_id="PR-9",
        engine="Chatterbox",
        licence="MIT",
        licence_clear=True,
        watermark_disclosure=CHATTERBOX_WATERMARK_DISCLOSURE,
        backend=backend,
    )


def moss_seat() -> TtsPilotSeat:
    """VA-3 MOSS-TTS-Nano: BLOCKED record only. It deliberately
    takes no backend argument — there is no slot to inject one
    into, so no worker can "temporarily" wire the model up."""
    return TtsPilotSeat(
        seat_id="VA-3",
        engine="MOSS-TTS-Nano",
        licence="STILL-OWED (O-6)",
        licence_clear=False,
        blocked_reason=(
            "O-6 unresolved: the upstream 'not yet licensed for "
            "redistribution' caveat is still live on the GitHub "
            "README and the Hugging Face card, and the root "
            "LICENSE file's text + weights coverage are unproven "
            "(see core/pilot_licences_sound_translation.py). Closing step: "
            "read the root LICENSE text, confirm Apache-2.0, "
            "and confirm it covers the 100M weights + ONNX "
            "export, then re-record the verdict."),
    )


def all_seats() -> tuple[TtsPilotSeat, ...]:
    """Every seat this workstream owns. KittenTTS is absent by
    design — see FROZEN_SEATS."""
    return (chatterbox_seat(), moss_seat())


def seat_by_id(seat_id: str) -> TtsPilotSeat | None:
    for seat in all_seats():
        if seat.seat_id == seat_id:
            return seat
    return None

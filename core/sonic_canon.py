"""Wave 9 (v5.64) — sonic-identity canon, RULES ONLY.

This module contains NO audio assets and NO audio bytes: no
waveforms, no samples, no base64 blobs, no file paths to
sounds. It is the canon that any future sound must satisfy,
expressed as data and checks, so the identity can be reviewed
and enforced before a single sound is ever rendered.

The canon, in full:

* ONE ROOT MOTIF. Every sound in the product derives from a
  single motif (contour and interval shape below). A sound that
  cannot be described as a variation of the motif is not a
  VYUK sound, whatever it sounds like.
* HERO SOUND FOR HERO MOMENTS ONLY. The hero variation plays
  for hero moments — first launch, first completed task, a
  milestone — and for nothing else. Routine moments (turn
  start, turn end, notifications) never get the hero sound, no
  matter how tempting the occasion feels.
* FREQUENCY PROPORTIONAL TO INVERSE LOUDNESS. The louder a
  sound, the rarer it must be: allowed uses per day fall as
  loudness rises. A loud sound played often is noise; a quiet
  sound played often is texture. ``max_uses_per_day`` enforces
  the curve, and ``check_request`` is the single gate.

A request is a (sound, moment, loudness, uses-today) tuple;
``check_request`` returns allowed/denied with a reason, and
denied is the safe default for anything unrecognised. The
canon itself is data (``ROOT_MOTIF``, ``HERO_MOMENTS``,
``LOUDNESS_CURVE``) so a future sound-design pass can read
it without re-deriving it from prose.

Stdlib only. No telemetry. No audio.
"""

from __future__ import annotations

from dataclasses import dataclass

MODULE_VERSION = "5.64.0"

__all__ = [
    "MODULE_VERSION",
    "ROOT_MOTIF",
    "HERO_MOMENTS",
    "ROUTINE_MOMENTS",
    "LOUDNESS_CURVE",
    "max_uses_per_day",
    "is_hero_moment",
    "check_request",
    "SoundRequest",
    "CanonVerdict",
]

# The root motif, described abstractly: a rising contour of two
# steps (root, then a major third, then a perfect fifth above
# the root), gentle attack, short decay. No pitch is absolute —
# the motif is a SHAPE, transposable to any root — and no audio
# renders from this description; it is the identity contract.
ROOT_MOTIF = {
    "name": "root",
    "contour": "rising",
    "intervals_semitones": (0, 4, 7),
    "attack": "gentle",
    "decay": "short",
    "rule": "every product sound is a variation of this motif",
}

# Hero moments: the only moments the hero variation may play.
HERO_MOMENTS = frozenset({
    "first_launch",
    "first_live_connect",
    "first_completed_task",
    "milestone_reached",
    "version_first_launch",
})

# Routine moments: explicitly NOT hero moments, so a caller
# cannot promote one by accident or enthusiasm.
ROUTINE_MOMENTS = frozenset({
    "turn_start",
    "turn_end",
    "notification",
    "listening_start",
    "listening_stop",
    "error",
})

# Loudness (dBFS, negative = quieter) -> max uses per day.
# Louder sounds are rarer: the curve is monotonic decreasing
# in loudness, which the test suite asserts structurally so a
# future edit cannot quietly invert it.
LOUDNESS_CURVE = (
    (-48.0, 200),   # near-silent texture: effectively unlimited
    (-36.0, 60),
    (-24.0, 20),
    (-18.0, 8),     # at the TTS standard: a handful a day
    (-12.0, 3),
    (-6.0, 1),      # loud: once a day, hero moments only
)


def max_uses_per_day(loudness_dbfs: float) -> int:
    """Allowed uses per day at a loudness: the curve step at or
    below the loudness. Quieter than the quietest step gets the
    quietest allowance; louder than the loudest step gets 0 —
    there is no allowance beyond the top of the curve."""
    if loudness_dbfs > LOUDNESS_CURVE[-1][0]:
        return 0  # louder than the top of the curve: no allowance
    allowance = LOUDNESS_CURVE[0][1]
    for threshold, uses in LOUDNESS_CURVE:
        if loudness_dbfs >= threshold:
            allowance = uses
        else:
            break
    return allowance


def is_hero_moment(moment: str) -> bool:
    return moment in HERO_MOMENTS


@dataclass(frozen=True)
class SoundRequest:
    """A request to play a sound. ``is_hero`` marks the hero
    variation; ``uses_today`` counts prior plays today."""

    sound: str
    moment: str
    loudness_dbfs: float
    is_hero: bool = False
    uses_today: int = 0


@dataclass(frozen=True)
class CanonVerdict:
    """Gate verdict: allowed, and the reason either way."""

    allowed: bool
    reason: str


def check_request(request: SoundRequest) -> CanonVerdict:
    """The single gate. Denied is the default: unknown moments,
    hero sounds outside hero moments, and over-frequency
    requests are all refused with a reason, never silently
    downgraded to a different sound."""
    if request.moment not in HERO_MOMENTS \
            and request.moment not in ROUTINE_MOMENTS:
        return CanonVerdict(False, f"unknown moment: {request.moment}")
    if request.is_hero and request.moment not in HERO_MOMENTS:
        return CanonVerdict(
            False,
            "hero sound is for hero moments only; "
            f"{request.moment} is not a hero moment")
    if not request.is_hero and request.moment in HERO_MOMENTS:
        # A hero moment deserves the hero variation, not a
        # routine sound wearing its occasion.
        return CanonVerdict(
            False,
            f"hero moment {request.moment} requires the hero variation")
    allowance = max_uses_per_day(request.loudness_dbfs)
    if request.uses_today >= allowance:
        return CanonVerdict(
            False,
            f"frequency cap reached: {allowance} uses/day at "
            f"{request.loudness_dbfs} dBFS (inverse-loudness rule)")
    return CanonVerdict(True, "within canon")

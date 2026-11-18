"""Room calibration + per-device/room profiles (VYUK Wave 3, v5.58.0).

VP-7.7 / VP-7.8 (session half). Profiles are auto-selected by
mic device ID: each profile carries the gain, wake threshold
and noise-floor baseline measured for that device in that
room, so moving the laptop to a new room (or swapping mic)
does not carry the old room's numbers with it.

Capture protocol (VP-7.7):
- 20 s sweep: the user speaks / the room is swept while the
  calibration samples RMS. (The sweep content is the caller's
  job — this module only consumes RMS samples.)
- 5 s silence: the room is quiet; these samples set the
  noise-floor baseline.
Total capture is 25 s, inside the §10 T19 first-run budget
(<=90 s) with room for prompts; the flow below uses 4 prompts
(<=5) and the whole first-run sequence is budgeted at <=90 s.

Maths — cherry-picked from the tree's own proven estimator,
no new framework, no new dependency:
- Noise floor = ~10th percentile of the silence RMS window
  (the robust statistic core/voice/local_client.py's
  NoiseFloorEstimator uses for the same purpose: a few loud
  events in the window barely move it, unlike a mean).
- Effective wake threshold = max(config floor, floor x 3.5),
  capped at 4x the config floor — the exact v5.44 (EARS A3)
  formula (VAD_NOISE_MULTIPLIER = 3.5, VAD_NOISE_CAP_FACTOR
  = 4.0 in core/voice/local_client.py). Adaptation can raise the
  bar at most 4x past the user's own knob, never deafen her.
- Recommended gain (dB) = clamp(20*log10(TARGET_RMS /
  sweep_median_rms), -12, +24): the gain that would land
  the sweep's median level on the target speech RMS. The
  target and clamps are constants below, not magic in code.

§10 T19 honesty note: the "calibrated wake gain >=15 points
at 2 m in fan noise" leg CANNOT be measured in this VM —
there is no microphone, no room, and no fan here. This
module therefore reports fan-noise measurements as
``None`` (unmeasured) unless the caller supplies real
fan-noise samples, and :func:`wake_gain_at_2m_in_fan_noise`
returns ``None`` rather than a fabricated number. The
calibration maths, the profile store, the prompt budget and
the capture timing are all fully testable here; the acoustic
leg is a field measurement for the coordinator to run on a
real box, and the handoff says so plainly.

Stdlib only. The profile store is in-memory by default; a
JSON file path may be supplied by the caller (tests use temp
dirs). This module never writes into the app's data/ tree
on its own.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------------------
# Constants (all tunable in one place; see the coordinator pre-pin record)
# ---------------------------------------------------------------------------

#: Capture protocol durations (seconds).
ROOM_SWEEP_CAPTURE_S = 20.0
ROOM_SILENCE_CAPTURE_S = 5.0

#: §10 T19 first-run budgets.
FIRST_RUN_BUDGET_S = 90.0
FIRST_RUN_MAX_PROMPTS = 5

#: Wake-threshold maths, cherry-picked from v5.44 EARS A3
#: (core/voice/local_client.py: VAD_NOISE_MULTIPLIER / CAP_FACTOR).
WAKE_NOISE_MULTIPLIER = 3.5
WAKE_CAP_FACTOR = 4.0
WAKE_MIN_SILENCE_SAMPLES = 5

#: Gain maths: target RMS the sweep median is normalised to,
#: and the clamp on the recommended gain.
GAIN_TARGET_RMS = 0.08
GAIN_MIN_DB = -12.0
GAIN_MAX_DB = 24.0

#: Prompts of the first-run calibration flow, in order.
#: 4 prompts — inside the <=5 budget. Wording is plain
#: language, no engineering codes.
CALIBRATION_PROMPTS = (
    "First, I'll learn this room so I can hear you properly. "
    "It takes about 25 seconds.",
    "Now, talk normally for 20 seconds — read something out "
    "loud, or just chat to me.",
    "Now stay quiet for 5 seconds while I listen to the room itself.",
    "Done — I'll use these settings whenever this microphone "
    "is selected.",
)

DEFAULT_DEVICE_ID = "default"


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile (pct in [0, 1]).
    Returns None for an empty list; never raises."""
    try:
        clean = sorted(float(v) for v in values
                       if v is not None and float(v) == float(v)
                       and float(v) >= 0)
    except (TypeError, ValueError):
        return None
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = pct * (len(clean) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return clean[low]
    frac = rank - low
    return clean[low] * (1.0 - frac) + clean[high] * frac


def noise_floor_from_silence(silence_rms: list[float]) -> float | None:
    """~10th percentile of the silence window (v5.44's robust
    statistic). None until WAKE_MIN_SILENCE_SAMPLES samples
    — an untrusted floor must never set a threshold."""
    try:
        samples = list(silence_rms or [])
    except TypeError:
        return None
    if len(samples) < WAKE_MIN_SILENCE_SAMPLES:
        return None
    return _percentile(samples, 0.10)


def effective_wake_threshold(noise_floor_rms: float | None,
                             config_floor_rms: float) -> float:
    """max(config floor, floor x 3.5), capped at 4x config
    floor — the v5.44 A3 formula verbatim in structure. With
    no trusted floor, the config floor rules alone."""
    try:
        base = float(config_floor_rms)
    except (TypeError, ValueError):
        base = 0.008
    if not (base > 0):
        base = 0.008
    if noise_floor_rms is None:
        return base
    try:
        floor = float(noise_floor_rms)
    except (TypeError, ValueError):
        return base
    if not (floor >= 0):
        return base
    return min(max(base, floor * WAKE_NOISE_MULTIPLIER),
               base * WAKE_CAP_FACTOR)


def recommended_gain_db(sweep_rms: list[float]) -> float:
    """Gain (dB) that lands the sweep median on
    GAIN_TARGET_RMS, clamped to [-12, +24] dB. Empty sweep
    degrades to 0 dB (no change), never to a wild gain."""
    median = _percentile(list(sweep_rms or []), 0.50)
    if median is None or median <= 0:
        return 0.0
    gain = 20.0 * math.log10(GAIN_TARGET_RMS / median)
    return max(GAIN_MIN_DB, min(GAIN_MAX_DB, gain))


def rms_to_dbfs(rms: float) -> float:
    """RMS -> dBFS for display. 0 RMS reads as the floor."""
    try:
        value = float(rms)
    except (TypeError, ValueError):
        return -120.0
    if value <= 0:
        return -120.0
    return 20.0 * math.log10(value)


@dataclass
class RoomProfile:
    """One device in one room, as calibrated.

    ``wake_gain_at_2m_fan_noise_points`` is deliberately
    optional and defaults to None: it is a FIELD measurement
    (a 2 m talker in fan noise, before vs after calibration)
    and this module will not invent it. A profile with None
    there is honest, not incomplete — the §10 T19 fan-noise
    leg is owed until a real box measures it.
    """
    device_id: str = DEFAULT_DEVICE_ID
    room_name: str = ""
    gain_db: float = 0.0
    wake_threshold_rms: float = 0.008
    noise_floor_rms: float | None = None
    noise_floor_dbfs: float | None = None
    sweep_median_rms: float | None = None
    capture_s: float = ROOM_SWEEP_CAPTURE_S + ROOM_SILENCE_CAPTURE_S
    prompts_used: int = len(CALIBRATION_PROMPTS)
    wake_gain_at_2m_fan_noise_points: float | None = None
    fan_noise_measured: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RoomProfile":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        profile = cls()
        for key, value in clean.items():
            setattr(profile, key, value)
        return profile


def calibrate_room(*, device_id: str, room_name: str = "",
                   sweep_rms: list[float], silence_rms: list[float],
                   config_floor_rms: float = 0.008,
                   fan_noise_silence_rms: list[float] | None = None,
                   fan_noise_talk_rms: list[float] | None = None) -> RoomProfile:
    """Run the calibration maths over captured samples and
    return the profile. Never raises on bad samples: garbage
    degrades to the config floor and 0 dB gain.

    Fan-noise leg: only when BOTH fan-noise sample sets are
    supplied (silence-with-fan and talk-at-2m-with-fan) is a
    wake-gain figure computed, as (talk floor headroom after
    calibration) minus (before) in threshold points — and
    even then it is labelled a supplied-sample computation,
    not a live measurement. Without real samples the field
    stays None and ``fan_noise_measured`` stays False.
    """
    floor = noise_floor_from_silence(silence_rms)
    threshold = effective_wake_threshold(floor, config_floor_rms)
    gain = recommended_gain_db(sweep_rms)
    median = _percentile(list(sweep_rms or []), 0.50)
    fan_gain = None
    fan_measured = False
    if fan_noise_silence_rms and fan_noise_talk_rms:
        fan_floor = noise_floor_from_silence(fan_noise_silence_rms)
        talk_median = _percentile(list(fan_noise_talk_rms), 0.50)
        if fan_floor is not None and talk_median:
            # Headroom of the 2 m talker above the fan floor,
            # in dB, before vs after applying the calibrated
            # gain + threshold change: the gain figure the
            # §10 T19 leg wants, computed only from supplied
            # real samples.
            before_db = rms_to_dbfs(talk_median) - rms_to_dbfs(fan_floor)
            after_floor = effective_wake_threshold(fan_floor, config_floor_rms)
            after_db = (rms_to_dbfs(talk_median) + gain
                        - rms_to_dbfs(max(after_floor, 1e-9)))
            fan_gain = after_db - before_db
            fan_measured = True
    return RoomProfile(
        device_id=str(device_id or DEFAULT_DEVICE_ID),
        room_name=str(room_name or ""),
        gain_db=gain,
        wake_threshold_rms=threshold,
        noise_floor_rms=floor,
        noise_floor_dbfs=(rms_to_dbfs(floor) if floor is not None else None),
        sweep_median_rms=median,
        prompts_used=len(CALIBRATION_PROMPTS),
        wake_gain_at_2m_fan_noise_points=fan_gain,
        fan_noise_measured=fan_measured,
    )


def wake_gain_at_2m_in_fan_noise(profile: RoomProfile) -> float | None:
    """The §10 T19 fan-noise figure, or None when unmeasured.
    Callers must render None as "not measured on this box",
    never as zero and never as a pass."""
    if profile is None:
        return None
    if not getattr(profile, "fan_noise_measured", False):
        return None
    return getattr(profile, "wake_gain_at_2m_fan_noise_points", None)


class RoomProfileStore:
    """Per-device/room profiles, auto-selected by mic device
    ID. Unknown devices get the default profile (fail-open
    to yesterday's behaviour), never an error and never
    another device's numbers."""

    def __init__(self, *, file_path=None):
        self._lock = threading.Lock()
        self._profiles: dict[str, RoomProfile] = {}
        self._file_path = file_path

    def put(self, profile: RoomProfile) -> None:
        if profile is None or not profile.device_id:
            return
        with self._lock:
            self._profiles[str(profile.device_id)] = profile

    def select(self, device_id: str | None) -> RoomProfile:
        """Profile for this mic device, or the default when
        none was calibrated for it. Selection is by exact
        device ID only — no fuzzy matching across devices."""
        key = str(device_id or DEFAULT_DEVICE_ID)
        with self._lock:
            if key in self._profiles:
                return self._profiles[key]
            if DEFAULT_DEVICE_ID in self._profiles:
                return self._profiles[DEFAULT_DEVICE_ID]
        return RoomProfile(device_id=key)

    def all_device_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._profiles)

    def save(self) -> bool:
        """Persist to the caller-supplied path, if any.
        Returns False (not an exception) when no path was
        given or the write fails."""
        if self._file_path is None:
            return False
        try:
            with self._lock:
                data = {k: v.to_dict() for k, v in self._profiles.items()}
            with open(self._file_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            return True
        except Exception:  # noqa: BLE001 — persistence never breaks selection
            return False

    def load(self) -> bool:
        if self._file_path is None:
            return False
        try:
            with open(self._file_path, encoding="utf-8") as fh:
                data = json.load(fh)
            profiles = {}
            for key, value in (data or {}).items():
                profiles[str(key)] = RoomProfile.from_dict(value or {})
            with self._lock:
                self._profiles = profiles
            return True
        except Exception:  # noqa: BLE001
            return False


def first_run_calibration_flow() -> dict:
    """The first-run sequence as data: prompts, capture
    durations, and the §10 T19 budget check. The GUI renders
    these prompts; the maths runs in :func:`calibrate_room`."""
    total_capture = ROOM_SWEEP_CAPTURE_S + ROOM_SILENCE_CAPTURE_S
    return {
        "prompts": list(CALIBRATION_PROMPTS),
        "prompt_count": len(CALIBRATION_PROMPTS),
        "sweep_capture_s": ROOM_SWEEP_CAPTURE_S,
        "silence_capture_s": ROOM_SILENCE_CAPTURE_S,
        "total_capture_s": total_capture,
        "within_first_run_budget_s": total_capture <= FIRST_RUN_BUDGET_S,
        "within_prompt_budget": len(CALIBRATION_PROMPTS) <= FIRST_RUN_MAX_PROMPTS,
        "first_run_budget_s": FIRST_RUN_BUDGET_S,
        "max_prompts": FIRST_RUN_MAX_PROMPTS,
    }


__all__ = [
    "ROOM_SWEEP_CAPTURE_S", "ROOM_SILENCE_CAPTURE_S",
    "FIRST_RUN_BUDGET_S", "FIRST_RUN_MAX_PROMPTS",
    "WAKE_NOISE_MULTIPLIER", "WAKE_CAP_FACTOR",
    "WAKE_MIN_SILENCE_SAMPLES", "GAIN_TARGET_RMS",
    "GAIN_MIN_DB", "GAIN_MAX_DB", "CALIBRATION_PROMPTS",
    "DEFAULT_DEVICE_ID",
    "noise_floor_from_silence", "effective_wake_threshold",
    "recommended_gain_db", "rms_to_dbfs",
    "RoomProfile", "calibrate_room",
    "wake_gain_at_2m_in_fan_noise",
    "RoomProfileStore", "first_run_calibration_flow",
]

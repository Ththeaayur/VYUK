from __future__ import annotations

import datetime  # INTENTIONAL (B31: moved code resolves datetime by bare name)
import math  # INTENTIONAL (B31: moved code resolves math by bare name)
import wave  # INTENTIONAL (B31: moved code resolves wave by bare name)
from pathlib import Path  # INTENTIONAL (B31: moved code resolves Path by bare name)
from . import sonic_canon as _canon  # INTENTIONAL (B31: moved code resolves _canon by bare name)
from . import sound_pools_v564 as _pools  # INTENTIONAL (B31: moved code resolves _pools by bare name)

# ------------------------------------------------------------------ #
# 16 + 21. Sonic canon gate in front of every sound emission,
# and sound-pool selection ONLY through that gate
# ------------------------------------------------------------------ #

_CUE_MOMENTS = {
    "wake": "listening_start",
    "ack": "turn_end",
    "sent": "turn_start",
    "done": "turn_end",
    "error": "error",
    "connect": "listening_start",
    "disconnect": "listening_stop",
    "ready": "turn_end",
    "capture": "notification",
    "alert": "notification",
    # Command Deck Phase F (guide S14): the overlay cues. Unknown
    # cues are denied by the canon's safe default, so each new cue
    # needs its moment here or Earcons._play stays silent for it.
    "lock": "notification",
    "start": "turn_start",
    "step": "turn_end",
    "handback": "listening_stop",
    "toast": "notification",
}

_canon_uses: dict = {}
_last_emission: dict = {}


def _cue_loudness_dbfs(name: str) -> float:
    """RMS dBFS of the shipped cue WAV, or the quiet-texture tier
    (-36 dBFS, 60/day) when the file is absent. RMS, not peak: it
    is the loudness measure the soundscape module uses (rms_dbfs),
    and every shipped cue measures -20.00 dBFS RMS by design (the
    20/day tier). Measured, never guessed from the volume slider
    (which is a gain, not a level).
    """
    base = Path(__file__).resolve().parent.parent / "gui" / "assets"
    for candidate in (base / f"{name}.wav",
                      base / "earcons_soft" / f"{name}.wav"):
        try:
            if not candidate.is_file():
                continue
            with wave.open(str(candidate), "rb") as wav:
                frames = wav.readframes(wav.getnframes())
                width = wav.getsampwidth()
                channels = wav.getnchannels()
        except Exception:  # noqa: BLE001
            continue
        if not frames or width not in (1, 2, 4):
            continue
        import array as _array

        code = {1: "b", 2: "h", 4: "i"}[width]
        samples = _array.array(code)
        samples.frombytes(frames)
        if channels > 1:
            samples = samples[::channels]
        if not samples:
            return -36.0
        full = float(1 << (8 * width - 1))
        mean_square = sum(int(s) ** 2 for s in samples) / len(samples)
        if mean_square <= 0:
            return -36.0
        return 20.0 * math.log10(math.sqrt(mean_square) / full)
    return -36.0


def canon_gate_for_cue(name: str, family: str = "classic") -> dict:
    """Canon verdict for one earcon cue, with the per-day counter.

    Unknown cues are denied (denied is the canon's safe default);
    hero sounds are never requested here (no cue is a hero sound),
    so the hero-moment rule can only deny a mis-mapped cue. The
    counter increments only on an allowed verdict.
    """
    moment = _CUE_MOMENTS.get(name)
    if moment is None:
        verdict = _canon.CanonVerdict(False, f"unknown cue: {name}")
        return {"allowed": False, "reason": verdict.reason,
                "quarry_lines": []}
    loudness = _cue_loudness_dbfs(name)
    today = datetime.date.today().isoformat()
    key = (today, name)
    uses = _canon_uses.get(key, 0)
    request = _canon.SoundRequest(sound=name, moment=moment,
                                  loudness_dbfs=loudness, is_hero=False,
                                  uses_today=uses)
    verdict = _canon.check_request(request)
    if verdict.allowed:
        _canon_uses[key] = uses + 1
        lines = _pools.quarry_summary()
    else:
        lines = []
    record = {"cue": name, "moment": moment, "loudness_dbfs": loudness,
              "uses_today": uses, "allowed": verdict.allowed,
              "reason": verdict.reason, "quarry_lines": lines}
    _last_emission.clear()
    _last_emission.update(record)
    return record


def gate_sound_emission(name: str, family: str = "classic") -> bool:
    """The gate Earcons._play calls: True only when the canon
    allows this cue now. Sound-pool selection happens inside the
    gate (quarry lines attach to the last emission record); no
    audio asset is layered by this path — layering still refuses
    under the earcon veto (see sound_pools.attempt_layer)."""
    return bool(canon_gate_for_cue(name, family)["allowed"])


def last_emission_record() -> dict:
    """The most recent canon-gated emission record (tests/diagnostics)."""
    return dict(_last_emission)


def reset_canon_uses_for_tests() -> None:
    """Test seam only: clear the per-day canon counters."""
    _canon_uses.clear()
    _last_emission.clear()

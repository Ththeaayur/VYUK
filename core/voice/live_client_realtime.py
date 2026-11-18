from __future__ import annotations

from typing import Any

try:
    from google import genai
    from google.genai import types
except Exception:  # noqa: BLE001 — a build without google-genai must
    # still import: the local brain keeps working, and cloud paths
    # report the missing library honestly instead of crashing.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

# INTENTIONAL (skills split B47): realtime-input-config family moved
# from live_client.py. Imports _error_text from the B46 error-cost
# module (the B46 out-edge: _is_input_config_rejection resolves it).
from .live_client_error_cost import _error_text


# --- Cloud Live input config (v5.44, EARS A8) -------------------------
# Gemini's automatic activity detection used to run at silent
# server defaults. VYUK now sets it explicitly, matched to the
# local feel: the local VAD ends an utterance after 0.8 s of
# trailing silence (core/local_client.VAD_TRAILING_SILENCE_S), so
# the server gets the same 800 ms silence duration; start-of-
# speech sensitivity is HIGH (eager — she should start listening
# the moment the user speaks) and end-of-speech sensitivity is
# LOW (patient — a mid-sentence pause must not cut the user off,
# the 800 ms silence window is what ends the turn). A short
# 100 ms prefix padding keeps the first syllable from clipping.
LIVE_SILENCE_DURATION_MS = 800


LIVE_PREFIX_PADDING_MS = 100


def _build_realtime_input_config():
    """The explicit RealtimeInputConfig for A8, or None when the
    installed google-genai predates these types (the connect then
    proceeds without it, exactly as before — fail soft).

    v5.54 (Wave 0, wave-32 stay path): the config also pins
    TURN COVERAGE explicitly. On the 3.x Live models the default
    coverage (TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO) pulls
    every video frame into every turn — a frame VYUK sends
    deliberately (screen_look, the Share button) would be
    re-included turn after turn, paying context and cost for a
    picture the model already saw. TURN_INCLUDES_ALL_INPUT
    counts each input once, as it was sent: deliberate frames
    land in the turn they were sent in, and nothing is hoarded.
    Fail soft in the same shape as the rest of this builder: an
    SDK without the TurnCoverage enum yields a config without
    the pin, and a server that rejects the pin retries without
    the whole input config (see _is_input_config_rejection)."""
    try:
        aad_type = getattr(types, "AutomaticActivityDetection", None)
        ric_type = getattr(types, "RealtimeInputConfig", None)
        start_enum = getattr(types, "StartSensitivity", None)
        end_enum = getattr(types, "EndSensitivity", None)
        if None in (aad_type, ric_type, start_enum, end_enum):
            return None
        kwargs: dict[str, Any] = {
            "automatic_activity_detection": aad_type(
                disabled=False,
                start_of_speech_sensitivity=(
                    start_enum.START_SENSITIVITY_HIGH),
                end_of_speech_sensitivity=(
                    end_enum.END_SENSITIVITY_LOW),
                prefix_padding_ms=LIVE_PREFIX_PADDING_MS,
                silence_duration_ms=LIVE_SILENCE_DURATION_MS,
            ),
        }
        coverage_enum = getattr(types, "TurnCoverage", None)
        coverage = getattr(
            coverage_enum, "TURN_INCLUDES_ALL_INPUT", None)
        if coverage is not None:
            kwargs["turn_coverage"] = coverage
        return ric_type(**kwargs)
    except Exception:  # noqa: BLE001 — config must never block connect
        return None


def _is_input_config_rejection(exc: BaseException) -> bool:
    """True when a connect error names the realtime input config
    (A8's fail-soft trigger): that candidate is retried WITHOUT
    the config rather than failing the whole connect."""
    text = _error_text(exc)
    return any(marker in text for marker in (
        "realtime_input_config", "realtime input config",
        "automatic_activity_detection", "automatic activity detection",
        "silence_duration", "start_of_speech_sensitivity",
        "end_of_speech_sensitivity",
        "turn_coverage", "turn coverage",
    ))


def _without_realtime_input_config(session_config):
    """A copy of `session_config` with realtime_input_config
    removed (A8 retry). Best-effort across SDK shapes: pydantic
    model_copy first, then a shallow copy; returns the original
    when neither works (the retry then simply repeats and the
    caller's normal error handling decides)."""
    try:
        return session_config.model_copy(
            update={"realtime_input_config": None})
    except Exception:  # noqa: BLE001
        pass
    try:
        import copy as _copy

        clone = _copy.copy(session_config)
        clone.realtime_input_config = None
        return clone
    except Exception:  # noqa: BLE001
        return session_config

"""Turn behaviour for VYUK Wave 3 ("Voice Perfected", v5.58.0).

VP-2.5 + VP-2.6, VP-8.4, VP-8.1/8.2/8.3/8.6, VP-7.7/7.8 (session
half; room maths live in core/room_calibration.py).

This module sits ABOVE the turn detector. It does NOT detect
speech, classify audio, or endpoint utterances itself: the
detector (SmartTurn, owned by another Wave 3 worker) supplies
utterance / barge-in / backchannel events, and this machine
decides what the session DOES with them. Integration point for
the coordinator: the detector's event callback should call
``TurnStateMachine.on_user_speech_start``,
``on_barge_in``, ``on_backchannel`` and ``on_assistant_started /
on_assistant_finished``, and must consult
``DictationMode.endpoint_profile()`` before firing any
end-of-turn prediction (in dictation the profile disables
SmartTurn outright — see ``DictationMode``).

CURRENT detector interface assumed (verified against the tree
2026-10-24, no SmartTurn module exists yet):
- core/voice/local_client.py: ``VAD_TRAILING_SILENCE_S = 0.8`` — that
  much trailing silence ends an utterance; ``MAX_UTTERANCE_S``
  caps it. The Live path uses ``LIVE_SILENCE_DURATION_MS = 800``
  (core/voice/live_client.py). This machine never re-implements those
  numbers; it consumes their events.
- core/voice/audio.py: ``audio_manager.is_muted`` gates the mic
  transmission queue (mic_queue) but NOT the wake-word ring
  buffer — a fact the spoken-notice evaluation below depends on.

Design rules, all deliberate:
- Stdlib only, no imports from the rest of core at module
  level (no import cycles; the dictation flag sync to
  core/brain/turn_context.py is lazy and best-effort).
- Every time-dependent rule takes an injectable clock
  (``now_fn``) so tests drive time deterministically.
- Nothing here raises on bad input: a turn-behaviour bug must
  degrade to "act normally", never crash a live session.
- Nothing here executes a tool, sends a message, or touches
  the network. Dictation routing in particular can only
  RETURN text; execution is the caller's job and is blocked
  while dictation is active (see ``DictationMode``).
- No voice conversion anywhere in this file: VP-8.1's
  announced failover is signalling + prosody parity only.
  Conversion was REJECTED inside that item and is not built.
"""

from __future__ import annotations

import math
import re
import threading
import time

# INTENTIONAL (skills split B20): re-exports of the moved units;
# every name below is the same object as in its owning module.
from .turn_machine_state import (
    FALSE_INTERRUPTION_RESUME_WINDOW_S, BACKCHANNEL_COOLDOWN_S, ADAPTIVE_FLOOR_PER_FALSE, ADAPTIVE_FLOOR_DECAY_WINDOW_S, ADAPTIVE_FLOOR_CAP_MULTIPLE, DIRECTED_FOLLOW_UP_WINDOW_S, STATE_IDLE, STATE_USER_SPEAKING, STATE_ASSISTANT_SPEAKING, STATE_PAUSED_FALSE_INTERRUPTION, _BACKCHANNEL_WORDS, is_backchannel_text, TurnStateMachine, DIRECTED_RESPOND, DIRECTED_STAND_DOWN, DirectednessGate,
)
from .turn_machine_dictation import (
    DICTATION_ENTER_ANNOUNCEMENT, DICTATION_EXIT_ANNOUNCEMENT, DICTATION_BANNER_TEXT, DICTATION_TRAILING_SILENCE_S, DICTATION_MAX_SEGMENT_S, DictationExecutionBlocked, DictationMode,
)
from .turn_machine_voice import (
    VOICE_LIVE, VOICE_ON_DEVICE, FAILOVER_ANNOUNCEMENT_TEMPLATE, VoiceFailoverGate, MEDIA_DUCKED_LEVEL, MEDIA_FULL_LEVEL, MediaDucking,
)
from .turn_machine_input import (
    PTT_FIRST_PARTIAL_BUDGET_S, PTT_BUTTON_LABEL, PTT_BUTTON_TOOLTIP, PushToTalk, IRREVERSIBLE_ACTION_KINDS, _BARE_AFFIRMATIONS, confirmation_shape, explicit_confirmation_prompt, accepts_confirmation,
)
from .turn_machine_verifier import (
    AdvisorySpeakerVerifier, SPOKEN_NOTICE_VERDICT_DO_NOT_SPEAK, evaluate_spoken_diagnostic_notice,
)

# Backwards-friendly alias: the module is the turn behaviour
# layer; callers that expect a "session" name get the machine.
TurnSession = TurnStateMachine

__all__ = [
    "FALSE_INTERRUPTION_RESUME_WINDOW_S",
    "BACKCHANNEL_COOLDOWN_S",
    "ADAPTIVE_FLOOR_PER_FALSE",
    "ADAPTIVE_FLOOR_DECAY_WINDOW_S",
    "ADAPTIVE_FLOOR_CAP_MULTIPLE",
    "DIRECTED_FOLLOW_UP_WINDOW_S",
    "STATE_IDLE", "STATE_USER_SPEAKING", "STATE_ASSISTANT_SPEAKING",
    "STATE_PAUSED_FALSE_INTERRUPTION",
    "is_backchannel_text",
    "TurnStateMachine", "TurnSession",
    "DIRECTED_RESPOND", "DIRECTED_STAND_DOWN", "DirectednessGate",
    "DICTATION_ENTER_ANNOUNCEMENT", "DICTATION_EXIT_ANNOUNCEMENT",
    "DICTATION_BANNER_TEXT", "DICTATION_TRAILING_SILENCE_S",
    "DICTATION_MAX_SEGMENT_S", "DictationExecutionBlocked",
    "DictationMode",
    "VOICE_LIVE", "VOICE_ON_DEVICE", "FAILOVER_ANNOUNCEMENT_TEMPLATE",
    "VoiceFailoverGate",
    "MEDIA_DUCKED_LEVEL", "MEDIA_FULL_LEVEL", "MediaDucking",
    "PTT_FIRST_PARTIAL_BUDGET_S", "PTT_BUTTON_LABEL",
    "PTT_BUTTON_TOOLTIP", "PushToTalk",
    "IRREVERSIBLE_ACTION_KINDS", "confirmation_shape",
    "explicit_confirmation_prompt", "accepts_confirmation",
    "AdvisorySpeakerVerifier",
    "SPOKEN_NOTICE_VERDICT_DO_NOT_SPEAK",
    "evaluate_spoken_diagnostic_notice",
]

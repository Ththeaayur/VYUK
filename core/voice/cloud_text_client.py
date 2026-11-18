"""
Groq cloud text brain for VYUK (v5.15) — provider #1 of the
text-chat cloud family.

WHY THIS MODULE EXISTS
----------------------
VYUK's cloud brain until now was Gemini Live (core/voice/live_client.py):
a native live-AUDIO session — Google hears the user and speaks the
reply itself. This module is the other shape of cloud brain: a
text-chat session. The model only ever sees text and answers in
text (with tool calls); VYUK's own ears (faster-whisper) and voice
(Kokoro → Piper → SAPI) do the hearing and speaking, reused from
core/voice/local_client.py exactly the way the lite session reuses them.

Provider #1 is Groq, because Groq serves open models from its own
datacenter at very high speed — including Llama 3.3 70B, which no
user laptop could hold. The laptop sends and receives text; the
70B's arithmetic happens on Groq's chips, on Groq's electricity.
The endpoints are OpenAI-shaped (POST /chat/completions with
function tools), so OpenAI itself — and Anthropic-shaped text APIs
with a thin translation — slot into this same session shape later.
Gemini remains the native live-audio provider; nothing here
replaces it.

PIPELINE (mirrors core/voice/local_client.py's LocalLiveSession)
-----------------------------------------------------------
    mic 16 kHz PCM ──▶ energy VAD (constants imported from
        core.local_client) ──▶ faster-whisper STT (via a
        LocalLiveSession used purely as an ears+voice engine — the
        lite session's speech-helper pattern) ──▶ the Groq brain
        turn below ──▶ speech through the same helper (the full
        v5.1 mouth-craft chain: _speech_text normalisation,
        sentence streaming, crossfade joins, faded barge-in).

THE BRAIN TURN
--------------
POST https://api.groq.com/openai/v1/chat/completions, stdlib
urllib only (no new dependency), through the module's ONE
transport seam (_post_json / _get_json — every HTTP call in this
module goes through them, so tests fake exactly one place). The
system prompt is assembled at session start the CLOUD way
(core/voice/live_client.py): config.system_instruction + the memory
context read ONCE at start + injection.PROMPT_RULE verbatim.
Tool use is native: the registry's declarations are translated
to OpenAI function format by openai_tools(); a reply carrying
tool_calls is executed through the REAL tools.execute() — the
same funnel every engine uses — and the results go back as
role:"tool" messages, for at most MAX_TOOL_ROUNDS rounds
(parity with core.local_client). Tool results are NOT wrapped
or screened here: exactly like local_client, this session passes
them through — the untrusted-content envelope is applied by the
producers inside the tools layer, and double-wrapping would be a
bug (see core/injection.py).

ERROR HONESTY (house law — nothing fails silently, nothing hangs)
-----------------------------------------------------------------
No key → start() refuses with an "add your Groq key in Settings"
ERROR line and zero HTTP attempted. 401 → key-rejected line.
429 → a rate-limit line naming Groq. A transport failure → a
truthful couldn't-reach-Groq line. An unreadable 200 body → a
malformed-reply line. Every ERROR is also written to
data/activity.log with the [Groq] prefix and a 300-char detail
cap (parity with live_client's [Live] lines). A failed turn never
kills the session: the dangling turn is rolled out of history,
LISTENING resumes, and the next message can succeed. All network
I/O runs in an executor — the GUI's event loop never blocks on
Groq.

The brain guard rides along (v5.3's teatime lesson, mirrored from
local_client; widened to battery / network / download in v5.43
B11, one shared class->tool map): a guarded state question whose
turn used NO tool is a fabrication by construction, so the reply
is replaced — before it is spoken, shown, or stored — with one
forced repair through the real registry.

CONFIG SEAMS (the parent wires these into core/config.py)
---------------------------------------------------------
The key resolves from config.groq_api_key when that field exists,
else the GROQ_API_KEY environment variable — the exact pattern
config uses for GEMINI_API_KEY. The model resolves the same way
(config.groq_model / GROQ_MODEL), defaulting to
"llama-3.3-70b-versatile". Temperature: config.groq_temperature /
GROQ_TEMPERATURE when parseable, else DEFAULT_TEMPERATURE.

Licence/deps: stdlib only (urllib/json/asyncio). Groq is a remote
service; nothing of theirs is bundled.
"""


from __future__ import annotations

import asyncio
import json
import os
import queue
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

from .. import cost_ledger as _cost_ledger_mod  # Phase 3 V1b: moved to core/voice/
from ..memory import episodes  # Phase 3 V1b: moved to core/voice/
from .. import injection  # Phase 3 V1b: moved to core/voice/
from ..taskwake import cancel_waking  # Phase 3 V1b: moved to core/voice/
from ..config import DATA_DIR, config  # Phase 3 V1b: moved to core/voice/
from ..groq_action_journal import GroqActionJournal  # Phase 3 V1b: moved to core/voice/
from ..languages import reply_language_directive  # Phase 3 V1b: moved to core/voice/
from ..provider_identity import Engine  # GAPS-22 (B-V3): engine identity
from ..memory.memory import memory_store  # Phase 3 V1b: moved to core/voice/
from ..funnel.tools_registry import tools  # Phase 3 V1b: moved to core/voice/; Phase 4 S1b INTENTIONAL: registry split to funnel


# INTENTIONAL (B38 part 1 of 2): the helpers family moved verbatim
# into cloud_text_helpers.py; the facade re-exports every moved name
# so existing importers, facade-resident GroqSession methods, and
# tests patching through this module object keep working unchanged.
# NOTE: _COST_LEDGER is an import-time snapshot (None); the live
# ledger cache lives in cloud_text_helpers._COST_LEDGER, where
# _cost_ledger() and its callers are co-located (B25 pattern).
# No in-tree reader of cloud_text_client._COST_LEDGER exists.
from .cloud_text_helpers import (
    _bump_epoch,
    GROQ_CHAT_URL,
    GROQ_MODELS_URL,
    DEFAULT_GROQ_MODEL,
    MAX_TOOL_ROUNDS,
    HISTORY_WINDOW,
    HISTORY_CAP,
    CHAT_TIMEOUT_S,
    MODELS_TIMEOUT_S,
    DEFAULT_TEMPERATURE,
    NO_KEY_STATUS,
    NETWORK_ERROR_STATUS,
    LOOP_FALLBACK,
    _log_line,
    _groq_api_key,
    _groq_model,
    DEFAULT_GROQ_VISION_MODEL,
    VISION_FALLBACK_NOTE,
    _groq_vision_model,
    _groq_vision_enabled,
    groq_vision_payload,
    _groq_tool_router_enabled,
    groq_offered_declarations,
    _max_tool_rounds,
    _DeliberationBudget,
    _ambient_packet_text,
    _call_signature,
    _COST_LEDGER,
    _cost_ledger,
    _cost_prompt_text,
    _cost_preflight_or_raise,
    _cost_record,
    _groq_temperature,
    _build_opener,
    _request_json,
    _post_json,
    _get_json,
    _error_detail,
    _classify_http_error,
    _GroqTurnError,
    _TYPE_MAP,
    _convert_schema,
    openai_tools,
    _result_text,
    _state_guard_for,
    validate_groq_key,
    _parse_groq_goal_reply,
    _validate_tool_exchanges,
)



from .cloud_text_helpers import (
    _bump_epoch,
    GROQ_CHAT_URL,
    GROQ_MODELS_URL,
    DEFAULT_GROQ_MODEL,
    MAX_TOOL_ROUNDS,
    HISTORY_WINDOW,
    HISTORY_CAP,
    CHAT_TIMEOUT_S,
    MODELS_TIMEOUT_S,
    DEFAULT_TEMPERATURE,
    NO_KEY_STATUS,
    NETWORK_ERROR_STATUS,
    LOOP_FALLBACK,
    _log_line,
    _groq_api_key,
    _groq_model,
    DEFAULT_GROQ_VISION_MODEL,
    VISION_FALLBACK_NOTE,
    _groq_vision_model,
    _groq_vision_enabled,
    groq_vision_payload,
    _groq_tool_router_enabled,
    groq_offered_declarations,
    _max_tool_rounds,
    _DeliberationBudget,
    _ambient_packet_text,
    _call_signature,
    _COST_LEDGER,
    _cost_ledger,
    _cost_prompt_text,
    _cost_preflight_or_raise,
    _cost_record,
    _groq_temperature,
    _build_opener,
    _request_json,
    _post_json,
    _get_json,
    _error_detail,
    _classify_http_error,
    _GroqTurnError,
    _TYPE_MAP,
    _convert_schema,
    openai_tools,
    _result_text,
    _state_guard_for,
    validate_groq_key,
    _parse_groq_goal_reply,
    _validate_tool_exchanges,
)


# INTENTIONAL (B39 part 2 of 2): GroqSession split verbatim into
# three mixins — lifecycle/speech (cloud_text_lifecycle.py),
# turn/tool loop (cloud_text_turn.py), goal/vision
# (cloud_text_goal.py). The composed class below keeps the
# class docstring + LITE_PLAN_WARNING; __init__ lives in the
# lifecycle mixin. Cross-mixin self-calls resolve through the
# composed-class MRO (lifecycle <-> turn is bidirectional:
# _process_utterance -> _brain_turn, _brain_turn -> _speak /
# _notify_status; goal -> turn + lifecycle). Patch seams:
# tests patch facade `tools` / `_max_tool_rounds` AND the turn
# mixin bindings (B16/B33/B35 dual-patch pattern) because
# _run_tool_call / _guard_state_answer / _brain_turn resolve
# them in turn-mixin globals; helpers' `tools` snapshot is
# unexercised (no test drives groq_offered_declarations under
# a patched bus — all turn tests fake _chat_completion).
from .cloud_text_lifecycle import GroqSessionLifecycleMixin
from .cloud_text_turn import GroqSessionTurnMixin
from .cloud_text_goal import GroqSessionGoalMixin


class GroqSession(GroqSessionLifecycleMixin, GroqSessionTurnMixin,
                  GroqSessionGoalMixin):
    """Text-chat cloud session with the exact public surface of
    GeminiLiveSession / LocalLiveSession (callbacks, is_connected,
    client → None, start / send_text / send_image_frame / stop) —
    core/brain/brain.py and the window cannot tell the difference."""

    #: v5.49 (programme item 36): mirrors
    #: LocalLiveSession.LITE_PLAN_WARNING for surface parity —
    #: a cloud brain is never lite-tier, so the warning is never
    #: shown from here; the name exists so uniform session code
    #: sees the same surface on every brain.
    LITE_PLAN_WARNING = (
        "The small on-device brain is unreliable at multi-step "
        "tasks — switch to a cloud brain or a bigger model for this.")

    #: GAPS-22 (B-V3/B-V4/B-V5): engine identity, the connected
    #: model (set at start in the lifecycle mixin), and the no-op
    #: release_model — a cloud session holds no local model.
    engine = Engine.GROQ
    connected_model = None

    def release_model(self) -> None:
        return

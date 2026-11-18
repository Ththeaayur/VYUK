"""
Gemini Live API Client for Voice AI Assistant.
Manages bidirectional real-time audio/text streaming and autonomous tool invocation.
"""

from __future__ import annotations

import asyncio
import os
import time
import traceback
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

try:
    from google import genai
    from google.genai import types
except Exception:  # noqa: BLE001 — a build without google-genai must
    # still import: the local brain keeps working, and cloud paths
    # report the missing library honestly instead of crashing.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from ..memory import episodes  # Phase 3 V1b: moved to core/voice/
from .. import injection  # Phase 3 V1b: moved to core/voice/
from ..config import DATA_DIR, config, save_env_setting  # Phase 3 V1b: moved to core/voice/
from ..languages import reply_language_directive  # Phase 3 V1b: moved to core/voice/
from ..taskwake import cancel_waking  # Phase 3 V1b: moved to core/voice/
from ..memory.memory import memory_store  # Phase 3 V1b: moved to core/voice/
from ..provider_identity import Engine  # GAPS-22 (B-V3): engine identity
from ..funnel.tools_gate import _READ_ONLY_TOOLS  # Phase 3 V1b: moved to core/voice/; Phase 4 S1b INTENTIONAL: gate split to funnel
from ..funnel.tools_handlers import deadline_for, note_tool_timeout, timeout_cooldown_active  # Phase 4 S1b INTENTIONAL: handlers split to funnel
from ..funnel.tools_registry import tools  # Phase 4 S1b INTENTIONAL: registry split to funnel

# INTENTIONAL (skills split B46): model-error-cost family moved to
# live_client_error_cost.py; re-exported here so every existing
# importer and every facade-side bare-name caller (class methods,
# _apply_system_proxy_env, resume/realtime helpers) resolves
# identically. NOTE (B39 pattern): _COST_LEDGER here is an
# import-time snapshot (None); the live cache lives in
# live_client_error_cost._COST_LEDGER, where _cost_record_live
# rebinds it. No in-tree reader of live_client._COST_LEDGER exists.
from .live_client_error_cost import (
    _COST_LEDGER,
    _classify_key_error,
    _cost_record_live,
    _error_code,
    _error_text,
    _is_model_rejection,
    _is_network_error,
    _log_line,
    _short_exc,
)


# INTENTIONAL (skills split B47): part-2 re-exports — every moved name
# re-exported so existing importers resolve identically. The facade
# defines only the composed class + the live_session instance.
from .live_client_helpers import (
    _ALREADY_CONNECTED_BACKOFF_S,
    _bump_epoch,
    _chunk_rms,
    _offered_declarations,
    _SEND_REALTIME_TIMEOUT_S,
    _VOICE_RMS_FLOOR,
    _WATCHDOG_SILENCE_S,
    _WATCHDOG_TICK_S,
    CONNECT_BUDGET_S,
    validate_api_key,
)
from .live_client_interaction import (
    _call_id_of,
    _interaction_status_of,
    _turn_status_notification,
)
from .live_client_lifecycle import GeminiLiveSessionLifecycleMixin
from .live_client_proxy import (
    _apply_system_proxy_env,
    _PROXY_ENV_VARS,
    _remove_exported_proxy,
)
from .live_client_realtime import (
    _build_realtime_input_config,
    _is_input_config_rejection,
    _without_realtime_input_config,
    LIVE_PREFIX_PADDING_MS,
    LIVE_SILENCE_DURATION_MS,
)
from .live_client_receive import GeminiLiveSessionReceiveMixin
from .live_client_resume import (
    _ambient_packet_text,
    _build_context_window_compression,
    _build_session_resumption,
    _clear_resume_handle,
    _client_is_enterprise,
    _close_code_of,
    _config_resume_handle,
    _config_transparent,
    _connect_timeout_for,
    _is_already_connected_close,
    _is_resumption_rejection,
    _is_stale_handle_close,
    _is_transparent_rejection,
    _live_model_candidates,
    _load_resume_handle,
    _resume_config_for_mode,
    _resume_handle_age_s,
    _resume_path,
    _resume_path_override,
    _save_resume_handle,
    _strip_cleared_handle,
    _swap_resumption,
    _without_resume_handle,
    _without_transparent,
    COMPRESSION_TARGET_TOKENS,
    COMPRESSION_TRIGGER_TOKENS,
    CONNECT_FIRST_TIMEOUT_S,
    CONNECT_LATER_TIMEOUT_S,
    KNOWN_LIVE_MODELS,
    RESUME_HANDLE_FILE,
    RESUME_HANDLE_TTL_S,
)
from .live_client_send import GeminiLiveSessionSendMixin


class GeminiLiveSession(
    GeminiLiveSessionLifecycleMixin,
    GeminiLiveSessionReceiveMixin,
    GeminiLiveSessionSendMixin,
):
    # INTENTIONAL (skills split B47): composed class — the original
    # class had no docstring and no class attributes, so the composed
    # class carries neither; all 27 methods live in the mixins above
    # and resolve self-calls through this MRO.
    #
    # GAPS-22 (B-V3/B-V4/B-V5) adds only the interface surface:
    # engine identity, the connected model (set at connect in the
    # lifecycle mixin, cleared by stop()), and the no-op
    # release_model — a cloud session holds no local model, so
    # there is nothing to release; the method exists so _bind's
    # conformance check and the standby discard path treat every
    # session uniformly.
    engine = Engine.GEMINI
    connected_model = None

    def release_model(self) -> None:
        return


live_session = GeminiLiveSession()

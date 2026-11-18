"""
Lite session for VYUK (v2.7) — the invisible brain's first-run face.

A fresh install defaults to the local engine (core/config.py), but
the offline pack takes minutes to download. Rather than making a new
user watch a setup screen, core/brain/brain.py routes the local engine
here whenever the brain GGUF hasn't landed yet:

    LiteLiveSession — alive from the first second. It kicks the pack
    download off in the background (core/voice/local_assets.downloads),
    answers immediately through a small intent responder wired to the
    REAL tool registry (time, battery, system, weather, opening apps,
    remembering a fact, today's schedule, unread mail, screenshots),
    hears the user as soon as the Whisper weights land, speaks as
    soon as a voice is available — and the moment BOTH the brain and
    the ears are ready, it hands the session over to a real
    LocalLiveSession mid-flight ("My full brain is ready — I'm all
    here now.") and delegates everything from then on.

The public surface is identical to GeminiLiveSession /
LocalLiveSession (callbacks, is_connected, client → None,
start/send_text/send_image_frame/stop) — the window and the brain
proxy cannot tell the difference. One boundary matters (ARCHITECTURE
landmine 15): everything the lite session says is emitted as an
ASSISTANT transcript — never as user text — so the learning engine
can never mistake its canned lines for something the user said.

Heavy imports stay lazy: this module imports stdlib + local_assets +
config + memory + tools at module level; faster-whisper and
core.local_client load only when actually needed.
"""

import asyncio
import queue
import re
import time
from collections.abc import Callable
from typing import Any

from .. import failsafe, injection  # Phase 3 V1b: moved to core/voice/
from . import local_assets  # Phase 3 V2: local_assets now in core/voice/; V1b interim closed
from ..taskwake import cancel_waking  # Phase 3 V1b: moved to core/voice/
from ..config import config  # Phase 3 V1b: moved to core/voice/
from ..memory.memory import memory_store  # Phase 3 V1b: moved to core/voice/
from ..funnel.tools_registry import tools  # Phase 3 V1b: moved to core/voice/; Phase 4 S1b INTENTIONAL: registry split to funnel


# INTENTIONAL (B27): facade re-exports — moved names live in lite_client_helpers / lite_client_session.
from .lite_client_helpers import (
    _bump_epoch,
    WATCH_INTERVAL_S,
    _IMAGE_REPLY_COOLDOWN_S,
    _READY_LINE,
    _UPGRADE_FAILED_LINE,
    _IMAGE_LINE,
    _route_intent,
    apply_no_think,
    _download_pct,
)
from .lite_client_session import LiteLiveSession

"""
Brain selector for VYUK (v1.7) — one session object, two engines.

The GUI talks ONLY to this module's `live_session` singleton, a
BrainSession proxy with the exact public surface of
core/voice/live_client.GeminiLiveSession. Which engine actually runs is
decided fresh on every start() from config.brain_engine:

    "local"  → the on-device engine (no API key). Since v2.7 the
               session is picked by readiness: with the builtin
               backend, a downloaded brain GGUF
               (core.voice.local_assets.brain_ready()) gets a new
               core.voice.local_client.LocalLiveSession, while a pack that
               is still downloading gets a new
               core.voice.lite_client.LiteLiveSession — alive instantly on
               real tools, auto-upgrading to the full session when
               the brain lands. The ollama backend always gets
               LocalLiveSession, as before.
    anything → the existing core.voice.live_client.live_session singleton
    else       (Gemini Live — the singleton is reused, never rebuilt)

v5.17: a cloud engine whose start() returns without ever connecting
(bad key, Gemini Live not enabled on the key's project, dead
network) falls back ONCE to the local engine, announced with a
"FALLBACK: <reason>" status, instead of leaving the user with a
completely dead assistant. A cloud session that connected and later
dropped never falls back — reconnects are the GUI's job. Inner
statuses reach the GUI through a trampoline (_forward_status) that
records what the fallback decision needs.

All engines are imported lazily inside start(), so importing this
module pulls in neither google.genai nor faster-whisper/piper.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from ..config import config  # Phase 3 B2h: brain hub moved to core/brain/brain.py

# Warm standby (v5.44, BRAIN B2): when a cloud connect has been
# running this long without succeeding, the local brain's model
# is quietly pre-loaded in the background so the fallback — if
# the connect deadline is reached and the cloud start gives up —
# switches instantly instead of loading a multi-GB model while
# the user waits.
CLOUD_STANDBY_DELAY_S = 20.0

# INTENTIONAL (brain split B5): helpers moved to brain_helpers, re-exported so the facade namespace matches the old module.
from .brain_helpers import _ambient_packet_text, _bump_epoch
# INTENTIONAL (brain split B5): session mixins; the composed class
# below keeps every importer's path and the test_phase4 seams.
from .brain_session_core import BrainSessionCoreMixin
from .brain_session_status import BrainSessionStatusMixin


class BrainSession(BrainSessionCoreMixin, BrainSessionStatusMixin):
    """Proxy presenting GeminiLiveSession's surface over either engine."""

    @staticmethod
    def _standby_model_id() -> str:
        """Model id for standby coalescing (Phase 2 F06): the
        resolved brain model path. Falls back to the backend
        name when the path is unresolvable — a coarse key is
        still a bound (one load per backend), never an
        unbounded one."""
        try:
            from ..voice import local_assets  # Phase 3 B2h: brain hub moved to core/brain/brain.py; Phase 3 V2: local_assets moved to core/voice/

            return str(local_assets.model_path())
        except Exception:  # noqa: BLE001 — fallback key, never fatal
            return f"backend:{config.local_backend or 'builtin'}"

    async def _prepare_standby(self):
        """Builds the standby local session and pre-loads its
        model in an executor. Returns the session (unbound — the
        proxy's callbacks are applied only if it WINS and becomes
        the fallback), or None when standby preparation failed;
        a failed standby changes nothing, the fallback simply
        builds its session the old way.

        Phase 2 (audit 5 F06): preparation goes through the
        process-wide standby coalescer keyed by the model id —
        concurrent prepares for the same model share ONE load
        (one session object), tracked to completion, with the
        load step gated by a process-wide semaphore. Cancelling
        this waiter never cancels the shared load (shielded
        await): the load finishes for any other waiter and the
        registry drains via its done-callback. The brain proxy
        runs one cloud start at a time, so one standby is never
        claimed by two concurrent fallbacks through this path.
        """
        from .. import standby_loads as _standby_mod  # Phase 3 B2h: brain hub moved to core/brain/brain.py

        async def _build_and_preload():
            session = self._build_local_inner()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._preload_local_session, session)
            return session

        try:
            task = _standby_mod.default_coalescer.prepare(
                self._standby_model_id(), _build_and_preload)
            return await _standby_mod.default_coalescer.await_shared(
                task)
        except Exception:  # noqa: BLE001 — standby never breaks start
            # (CancelledError is BaseException: it propagates,
            # it is NOT swallowed here — a cancelled waiter
            # must not masquerade as a failed standby.)
            return None

    async def _start_cloud(self, inner, audio_manager):
        """Runs a cloud inner's start() with the B2 warm standby
        alongside (v5.44), then the v5.17 fallback decision.

        The cloud start runs as a task. If it is still running
        CLOUD_STANDBY_DELAY_S in without ever having connected,
        the local session is built and its model pre-loaded in
        the background — silently: the standby emits no status,
        opens no mic, starts no audio and speaks nothing. If the
        cloud then fails, the fallback starts THAT session, so
        the switch is instant (its model is already resident).
        If the cloud connects (or the start ends any other way
        that makes fallback unwarranted), the standby is
        discarded (Phase 4, C04): a still-running standby
        waiter's cancellation is shielded from the shared
        load, so the load is ABANDONED — its result is
        released on completion instead of handed to anyone —
        and an already-completed standby session is released
        directly. A worker thread already running a
        synchronous preload cannot be forcibly killed; the
        load finishes, its result is released, and the
        registry drains. No thread or session is left
        running, and no second preload can overlap."""
        cloud_task = asyncio.ensure_future(inner.start(audio_manager))
        standby_task: asyncio.Task | None = None
        try:
            done, _pending = await asyncio.wait(
                {cloud_task}, timeout=CLOUD_STANDBY_DELAY_S)
            if (not done and not self._saw_connected
                    and not self._stop_requested):
                standby_task = asyncio.ensure_future(
                    self._prepare_standby())
            await cloud_task
        except asyncio.CancelledError:
            cloud_task.cancel()
            if standby_task is not None:
                self._discard_standby(standby_task)
            raise
        standby = None
        if standby_task is not None:
            if self._fallback_warranted():
                try:
                    standby = await standby_task
                except Exception:  # noqa: BLE001
                    standby = None
            else:
                # The cloud won (or a stop did): discard cleanly.
                self._discard_standby(standby_task)
                standby = None
        self._standby_inner = standby
        await self._maybe_fallback_to_local(
            audio_manager, prepared=standby)
        self._standby_inner = None


live_session = BrainSession()

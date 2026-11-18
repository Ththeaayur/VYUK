# BrainSession mixin (B5 split): methods moved verbatim from
# core/brain/brain.py; called via self on the composed class.
#
# GAPS-22 (B-V5) — ownership, stated once, here: the proxy owns
# _inner except the borrowed Gemini singleton. Groq and local
# sessions are built fresh per start() and owned by the proxy;
# stop() stops the inner's connection and the companion holder.
# The Gemini module singleton (live_session) is BORROWED: stop()
# stops its connection, never destroys the module instance.
# _standby_inner is a never-started local session the proxy owns
# until it is installed or discarded (discarded through
# release_model(), never by attribute name).

import asyncio
from ..config import config  # Phase 3 B2h: brain hub moved to core/brain/brain.py
from ..provider_identity import Engine  # GAPS-22 (B-V3): engine identity
from .brain_helpers import _bump_epoch  # INTENTIONAL (brain split B5): helper moved out, imported where used
from .engine_health import can_serve_configured  # GAPS-22 (B-V10): can_serve
from .engine_session import check_session_conforms  # GAPS-22 (B-V4): _bind check


class BrainSessionCoreMixin:

    def __init__(self):
        # Same settable callback surface as GeminiLiveSession. The proxy
        # stores them; start() applies them to whichever inner session
        # the configured engine selects.
        self.on_status_change: Callable[[str], None] | None = None
        self.on_transcript: Callable[[str, str], None] | None = None
        # Chat UI (2026-11-16): per-chunk callback for the live
        # reply, forwarded to the inner like on_transcript.
        self.on_transcript_chunk: Callable[[str, str], None] | None = None
        self.on_audio_data: Callable[[bytes], None] | None = None
        self.on_tool_call: Callable[[str, dict[str, Any], Any], None] | None = None

        self._inner = None
        self._engine: Engine | None = None

        # v5.17 fallback bookkeeping: _forward_status records the
        # last inner status and whether this start ever saw a live
        # connection; stop() latches _stop_requested so a deliberate
        # stop mid-start never reads as a failed connect.
        self._last_status: str | None = None
        self._saw_connected = False
        self._stop_requested = False

        # N4 (v5.44): the ambient packet this session last held /
        # forwarded; refresh_ambient_context() compares against it.
        self._ambient_last: str | None = None
        # B2 (v5.44): the warm-standby local session, when one was
        # prepared during a slow cloud connect (see _start_cloud).
        self._standby_inner = None

        # Companion sessions (2026-11-05): the provider-blind holder
        # for the (Groq, local) pair — second-opinion brains now, the
        # Stage 1 free-form companion later. Created lazily on first
        # start(); None at rest. It never touches _inner, the fallback
        # state, or _standby_inner.
        self._companion_holder = None

        # Phase 1 (C03/C09): requested-vs-effective provider state.
        # _requested_provider is what the config asked for at the
        # last start(); _engine is what is actually serving (it
        # becomes "local" on a cloud→local fallback or a frozen
        # brain override). _fallback_reason is the plain-words
        # reason for that switch, or None when this session is
        # serving exactly what was requested. All three reset on
        # every start() — a new start is a new decision, and a
        # stale reason must never describe a fresh session.
        self._requested_provider: str | None = None
        self._fallback_reason: str | None = None
        self._frozen_override: bool = False

    @property
    def is_connected(self) -> bool:
        """Delegates to the active inner session; False before start()."""
        if self._inner is None:
            return False
        return bool(self._inner.is_connected)

    @property
    def client(self):
        """The genai.Client for secondary Gemini tasks (session summary).

        Only the Gemini engine has one — the local brain returns None,
        which callers already treat as "summaries unavailable".
        """
        if self._inner is None or self._engine is Engine.LOCAL:
            return None
        return self._inner.client

    def make_summariser(self):
        """A prompt->text callable for session summaries (v5.45,
        B5), built from WHICHEVER engine is active, or None when
        no brain can summarise (no session yet, a lite session,
        or a local model that isn't loaded). core/session_summary
        consumes it; the window-close call site passes it instead
        of only ever having a Gemini client to offer.

        Gemini: wraps the genai client's generate_content with
        the configured Live model name. Groq: one chat
        completion through the session's own _chat_completion
        seam. Local builtin: the session's _llama_chat (only when
        the model is resident). Local Ollama: _ollama_chat."""
        inner = self._inner
        if inner is None or getattr(inner, "is_lite", False):
            return None
        if self._engine is Engine.GEMINI:
            client = getattr(inner, "client", None)
            if client is None:
                return None
            # GAPS-22 (B-V5): the model actually serving — the
            # connected model the session pinned at connect, not
            # the config pick (which may name a model the session
            # is no longer on). Falls back to config only when
            # the session has not connected yet.
            model_name = getattr(inner, "connected_model", None) or config.model_name

            def _gemini_summarise(prompt: str) -> str:
                response = client.models.generate_content(
                    model=model_name, contents=prompt)
                return getattr(response, "text", "") or ""

            return _gemini_summarise
        if self._engine is Engine.GROQ:
            chat = getattr(inner, "_chat_completion", None)
            if not callable(chat):
                return None

            def _groq_summarise(prompt: str) -> str:
                status, parsed = chat(
                    [{"role": "user", "content": prompt}])
                if status == 200 and isinstance(parsed, dict):
                    choices = parsed.get("choices") or []
                    if choices:
                        msg = choices[0].get("message") or {}
                        return str(msg.get("content") or "")
                return ""

            return _groq_summarise
        # Local engine. B5: on the Lite tier the local model
        # never summarises (session_summary's skip) — say so here
        # by offering no summariser at all.
        try:
            from ..memory import session_summary as _ss  # Phase 3 B2h: brain hub moved to core/brain/brain.py

            if _ss.is_lite_tier():
                return None
        except Exception:  # noqa: BLE001
            pass
        if str(getattr(inner, "_backend", "builtin")).lower() == "ollama":
            ollama_chat = getattr(inner, "_ollama_chat", None)
            if not callable(ollama_chat):
                return None

            def _ollama_summarise(prompt: str) -> str:
                reply = ollama_chat(
                    [{"role": "user", "content": prompt}], [])
                msg = (reply or {}).get("message") or {}
                return str(msg.get("content") or "")

            return _ollama_summarise
        llama_chat = getattr(inner, "_llama_chat", None)
        if callable(llama_chat) and getattr(inner, "_llama", None) is not None:
            def _local_summarise(prompt: str) -> str:
                return str(llama_chat(
                    [{"role": "user", "content": prompt}]) or "")

            return _local_summarise
        return None

    def _forward_status(self, status: str):
        """Trampoline for inner-session statuses (v5.17).

        Records the status for the fallback decision and notes
        whether the inner session is connected at forward time — a
        cloud session that connected and later drops is the GUI's
        auto-reconnect problem, never a fallback case — then hands
        the status to the GUI callback unchanged."""
        self._last_status = status
        inner = self._inner
        if inner is not None and bool(getattr(inner, "is_connected", False)):
            self._saw_connected = True
        if self.on_status_change is not None:
            self.on_status_change(status)

    def _bind(self, inner):
        """Applies the proxy's callbacks to an inner session: the
        status callback goes through the trampoline (every engine,
        uniformly); the rest are applied directly, as always.

        GAPS-22 (B-V4): the inner must first conform to the
        EngineSession interface — a session missing a member
        fails here, with the member named, instead of later at
        a call site.
        """
        check_session_conforms(inner)
        self._inner = inner
        inner.on_status_change = self._forward_status
        inner.on_transcript = self.on_transcript
        # Chat UI (2026-11-16): the inner only carries the chunk
        # callback when it streams transcription (the Gemini live
        # session does; the local session does not) — set it only
        # where the attribute exists, so a conforming inner
        # without it is untouched.
        if hasattr(inner, "on_transcript_chunk"):
            inner.on_transcript_chunk = self.on_transcript_chunk
        inner.on_audio_data = self.on_audio_data
        inner.on_tool_call = self.on_tool_call

    def _build_local_inner(self):
        """Builds the local-engine session: ollama always gets a
        LocalLiveSession; the builtin backend gets one too when the
        brain GGUF has landed, and a LiteLiveSession (alive
        instantly on real tools, auto-upgrading) while the offline
        pack is still downloading."""
        backend = (config.local_backend or "builtin").strip().lower()
        if backend == "ollama":
            from ..voice.local_client import LocalLiveSession  # Phase 3 V1b: moved to core/voice/

            return LocalLiveSession()
        # v2.7 invisible brain: full session when the brain
        # has landed, lite session (which auto-upgrades) while
        # the offline pack is still downloading.
        from ..voice import local_assets  # Phase 3 B2h: brain hub moved to core/brain/brain.py; Phase 3 V2: local_assets moved to core/voice/

        if local_assets.brain_ready():
            from ..voice.local_client import LocalLiveSession  # Phase 3 V1b: moved to core/voice/

            return LocalLiveSession()
        from ..voice.lite_client import LiteLiveSession  # Phase 3 V1b: moved to core/voice/

        return LiteLiveSession()

    def _start_companion(self) -> None:
        """Start the companion-session holder alongside the primary.

        Best-effort: any failure (including a missing Groq key or
        a local model that cannot load) leaves the pair None and
        the second-opinion check OFF; it never breaks the primary
        start. The holder is created once and reused across
        restarts; its stop() (from BrainSession.stop()) resets it
        so a later start rebuilds the legs.
        """
        try:
            from . import companion_sessions as _cs
        except Exception:  # noqa: BLE001 — no holder, no pair
            return
        try:
            holder = getattr(self, "_companion_holder", None)
            if holder is None:
                holder = _cs.CompanionSessionHolder(
                    build_local_inner=self._build_local_inner)
                self._companion_holder = holder
            holder.start()
        except Exception:  # noqa: BLE001 — companion never breaks start
            pass

    def companion_pair(self):
        """(groq_session, local_session) when both companion legs
        are real and ready, else None. The TurnCoordinator
        construction site reads this; a None pair means the
        second-opinion check stays OFF. Never raises."""
        try:
            holder = getattr(self, "_companion_holder", None)
            if holder is None:
                return None
            return holder.pair
        except Exception:  # noqa: BLE001 — no pair, check stays OFF
            return None

    def companion_missing_legs(self):
        """Which companion legs are missing, as plain words ([] when
        the pair is ready). Never raises."""
        try:
            holder = getattr(self, "_companion_holder", None)
            if holder is None:
                return ["companion holder not started"]
            return holder.missing_legs()
        except Exception:  # noqa: BLE001 — holder unconsultable
            return ["companion holder not started"]

    def _fallback_warranted(self) -> bool:
        """The v5.17 fallback conditions, as a predicate (extracted
        v5.44 so the B2 standby can ask the same question BEFORE
        the cloud start returns): no stop requested, the cloud
        never connected during this start, and the inner does not
        still report connected."""
        if self._stop_requested or self._saw_connected:
            return False
        inner = self._inner
        if inner is not None and bool(getattr(inner, "is_connected", False)):
            return False
        return True

    @staticmethod
    def _preload_local_session(session) -> None:
        """The preload-only step of the B2 warm standby (sync —
        executor only): load the local brain's model WITHOUT
        starting the session — no mic, no audio, no speech, no
        statuses (the standby's callbacks are never bound, so
        even a load that wanted to announce itself has nowhere
        user-visible to go). Uses the session's own exposed
        preload seam when it has one (LocalLiveSession.
        preload_model_only), else its model loader directly.
        A lite session has no model to load — it IS instant — so
        for it this is a deliberate no-op."""
        loader = getattr(session, "preload_model_only", None)
        if callable(loader):
            try:
                loader()
            except Exception:  # noqa: BLE001 — standby is best-effort
                pass
            return
        ensure = getattr(session, "_ensure_llama", None)
        if callable(ensure):
            try:
                ensure()
            except Exception:  # noqa: BLE001 — standby is best-effort
                pass

    def _discard_standby(self, standby_task: asyncio.Task) -> None:
        """Discard a standby task (Phase 4, C04). Never raises
        and never breaks the start it runs inside.

        - Task still pending: cancel the WAITER (shielded, so
          the shared load is untouched) and ABANDON the shared
          load: when it completes, its result is released
          instead of handed to anyone.
        - Task already done with a session: release that
          session directly (its model refs are dropped).
        - Task done cancelled/failed: nothing to release."""
        try:
            if not standby_task.done():
                standby_task.cancel()
                try:
                    from .. import standby_loads as _standby_mod  # Phase 3 B2h: brain hub moved to core/brain/brain.py
                    _standby_mod.default_coalescer.abandon(
                        self._standby_model_id(),
                        release=self._release_standby_session)
                except Exception:  # noqa: BLE001 — abandon is
                    pass  # best-effort; the waiter is cancelled
            elif not standby_task.cancelled():
                try:
                    session = standby_task.result()
                except Exception:  # noqa: BLE001 — failed standby
                    session = None
                if session is not None:
                    self._release_standby_session(session)
        except Exception:  # noqa: BLE001 — discard never breaks
            pass  # start (including its CancelledError handler)

    @staticmethod
    def _release_standby_session(session) -> None:
        """Best-effort release of a NEVER-STARTED standby
        session (Phase 4, C04): drop its model refs and
        collect. Only ever called on discarded standby
        sessions — never on an installed or live session,
        which would break it. Never raises.

        GAPS-22 (B-V6): the session drops its own models
        through release_model() — the drop knowledge lives
        with the session, not in an attribute-name loop here.
        """
        try:
            if session is None:
                return
            release = getattr(session, "release_model", None)
            if callable(release):
                release()
            else:  # pragma: no cover — every session has release_model
                import gc as _gc

                _gc.collect()
        except Exception:  # noqa: BLE001 — release never raises
            pass

    async def _maybe_fallback_to_local(self, audio_manager,
                                       prepared=None):
        """One-level cloud→local fallback (v5.17), called after a
        cloud start() RETURNS. Fires only when the cloud session
        never connected during that start: a stop() requested
        mid-start, a session that connected at any point (its later
        drop belongs to the GUI's auto-reconnect), or an inner that
        still reports connected all suppress it — and the local
        session started here is never itself fallback-eligible.

        v5.44 (B2): `prepared` is the warm-standby session when
        one was pre-loaded during the cloud connect — it is
        started as-is (model already resident, so the switch is
        instant); without one, the session is built fresh, the
        pre-B2 behaviour."""
        if not self._fallback_warranted():
            return
        last = self._last_status or ""
        if last.startswith("ERROR:"):
            reason = last[len("ERROR:"):].strip()[:200]
        elif last == "MISSING_API_KEY":
            reason = last
        else:
            reason = ""
        if not reason:
            reason = "the cloud brain didn't connect"
        # Phase 1 (C03): record the switch durably on the proxy,
        # not just in the transient FALLBACK status string — the
        # GUI truth line and provider_status() read this, so the
        # requested-vs-effective gap survives past the notice.
        self._fallback_reason = reason
        if self.on_status_change is not None:
            self.on_status_change(f"FALLBACK: {reason}")
        local_inner = prepared if prepared is not None \
            else self._build_local_inner()
        self._engine = Engine.LOCAL
        _bump_epoch("brain fallback — session swap")  # v5.48 (item 20)
        self._bind(local_inner)
        # Reset the trampoline's memory so the local session's own
        # last status is readable afterwards (the cloud's is saved
        # in `reason` above).
        self._last_status = None
        await local_inner.start(audio_manager)
        # v5.26: when BOTH brains fail, say so — both of them. The
        # local session has already emitted its own ERROR (that is
        # what the GUI is showing), but on its own it reads as if
        # the local brain simply broke, when the truth is a chain:
        # the cloud failed first and the fallback failed second.
        # Aayur's field read of exactly this shape was "the local
        # AI and the Gemini key are colliding". A connected fallback
        # (incl. the lite session) never reaches this; a deliberate
        # stop never does either.
        if self._stop_requested:
            return
        if bool(getattr(local_inner, "is_connected", False)):
            return
        local_last = self._last_status or ""
        if local_last.startswith("ERROR:"):
            local_reason = local_last[len("ERROR:"):].strip()[:200]
            if self.on_status_change is not None:
                self.on_status_change(
                    f"ERROR: The cloud brain didn't connect ({reason}); "
                    f"the on-device fallback didn't either "
                    f"({local_reason})."
                )

    async def start(self, audio_manager):
        """Picks the engine FRESH from config.brain_engine and runs it."""
        _bump_epoch("brain start — session swap")  # v5.48 (item 20)
        self._stop_requested = False
        self._saw_connected = False
        self._last_status = None
        # Phase 1 (C03/C09): provider identity comes from the one
        # explicit resolver now, and the request is recorded BEFORE
        # any override below can change it — so provider_status()
        # can always say what was asked for vs what is serving.
        # Dispatch itself is unchanged (see provider_identity).
        from ..provider_identity import resolve_provider as _resolve  # Phase 3 B2h: brain hub moved to core/brain/brain.py

        _resolution = _resolve(config.brain_engine, config.cloud_provider)
        self._requested_provider = _resolution["provider"]
        self._fallback_reason = None
        self._frozen_override = False
        engine = _resolution["provider"]
        # v5.56 (Wave 2, SE-6.9 rung 3): a frozen brain refuses
        # cloud engines — the on-device brain answers instead.
        # GAPS-22 (B-V7): fail CLOSED. An unconsultable kill
        # chain reads as FROZEN: the safe direction is local
        # (nothing leaves the machine), and the override is
        # recorded so provider_status() says so. The user set
        # the rung, so no extra announcement is made here; the
        # engine display shows local. (Summariser cloud legs in
        # make_summariser are per-turn helpers, not the dispatch
        # point, and are not gated here.)
        try:
            from ..funnel import kill_chain as _kc  # Phase 3 B2h: brain hub moved to core/brain/brain.py (Phase 3 F2b: kill_chain moved to core/funnel/)

            if engine is not Engine.LOCAL and _kc.brain_frozen():
                # Phase 1 (C03): the override used to be invisible
                # except for the engine display reading "local".
                # It is now recorded: provider_status() reports
                # requested != effective with this flag, and the
                # Settings brain-truth line names it.
                self._frozen_override = True
                engine = Engine.LOCAL
        except Exception:  # noqa: BLE001 — fail closed, see above
            self._frozen_override = True
            self._fallback_reason = (
                "The kill-chain state could not be read, so the "
                "brain was treated as frozen — the on-device brain "
                "answered instead.")
            engine = Engine.LOCAL
        # GAPS-22 (B-V10): pre-flight — a cloud engine that
        # cannot serve (no key) is discovered here, before any
        # connect attempt, and the session goes straight to local
        # with the honest reason. The outcome is the same as a
        # failed connect + fallback, without the doomed attempt;
        # the fallback net stays in place for a connect that
        # fails after a passing pre-flight. A pre-flight that
        # itself errors is skipped (the old behaviour) — it must
        # never block a start that would have worked.
        if engine is not Engine.LOCAL:
            try:
                _health = can_serve_configured(engine)
            except Exception:  # noqa: BLE001 — pre-flight is advisory
                _health = None
            if _health is not None and not _health["ok"]:
                self._fallback_reason = _health["reason"]
                engine = Engine.LOCAL
        if engine is Engine.LOCAL:
            inner = self._build_local_inner()
            self._engine = Engine.LOCAL
        else:
            provider = _resolution["provider"]
            if provider is Engine.GROQ:
                # v5.15 text-chat cloud (Groq / Llama 3.3 70B): a
                # fresh session per start, local/lite style — the
                # provider's key and model are re-read by the
                # session itself at start.
                from ..voice.cloud_text_client import GroqSession  # Phase 3 V1b: moved to core/voice/

                inner = GroqSession()
                self._engine = Engine.GROQ
            else:
                from ..voice.live_client import live_session as gemini_session  # Phase 3 V1b: moved to core/voice/

                inner = gemini_session  # reuse the singleton — never a second one
                self._engine = Engine.GEMINI

        self._bind(inner)
        # Companion sessions (2026-11-05): start the holder alongside
        # the primary. Best-effort and guarded inside — a companion
        # failure never breaks the primary start; the pair is simply
        # None and the second-opinion check stays OFF.
        self._start_companion()
        if self._engine in (Engine.GEMINI, Engine.GROQ):
            # v5.44 (B2): the cloud start runs with the warm
            # standby alongside; the fallback decision (and the
            # prepared session, if any) is handled inside.
            await self._start_cloud(inner, audio_manager)
        else:
            await inner.start(audio_manager)

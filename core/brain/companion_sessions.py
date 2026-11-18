"""Provider-blind companion-session holder (2026-11-05).

ONE capability, TWO consumers: this holder starts alongside the
primary brain session and holds the (Groq text session, local
session) pair. The second-opinion check takes the pair through the
EXISTING factory (second_opinion_from_sessions) — no factory
change — and the voice free-form loop takes ONE companion from it
later (Stage 1, still held; the holder merely makes one available).

The pair is built ONLY when both legs are real:

- Groq leg: a real GroqSession, constructed only when a Groq key
  is configured (BYOK — config.groq_api_key or GROQ_API_KEY).
  No key means no Groq leg, the pair is None, and the check stays
  OFF exactly as before. The key itself is never this module's
  business to obtain or invent.
- Local leg: a SEPARATE local session built by the injected
  builder (production: the proxy's _build_local_inner — a
  reference for how a local inner is built, never a path to
  hijack). Ollama backend: the session is the leg as built, no
  preload (the model lives in the server). Builtin backend: the
  leg is ready only once the landed model is preloaded and
  resident (preload_model_only, executor only — never start():
  no mic, no audio, no statuses). A Lite session exposes no chat
  method, so it is never a leg.

Fail-closed, honest, no silent degradation: either leg missing
means the pair is None and the check stays OFF; missing_legs()
names precisely which leg is missing (and whether the local model
is still loading).

The holder NEVER disturbs the primary session: it does not rebind
the primary inner, does not trigger or confuse the primary's
fallback state, does not consume the warm-standby session (the
standby belongs to fallback — this module holds no standby
reference at all), and releases its own sessions cleanly on
stop(). The companion local session is a second model load when
the primary is also local (edge case; the default primary is
Gemini, which loads nothing locally).

FACTORY DISPATCH NOTE (proven 2026-11-05, follow-up owed): the
factory's local brain prefers _ollama_chat over _llama_chat, and
_ollama_chat lives on the lifecycle mixin — always in a real
LocalLiveSession's MRO. So a real builtin companion leg is asked
via Ollama HTTP, not via its resident model: without a reachable
Ollama server the local brain reports "unavailable" and the check
pauses (fail-closed, honest, but single-opinion). The preload
still stands — it is what makes the local leg genuinely real and
what Stage 1 needs resident — but whether the factory should
prefer the session's own backend is a factory decision for the
owner/parent, NOT this module's (no factory change in this batch).
"""

from __future__ import annotations

import asyncio
import gc


def _groq_key_present() -> bool:
    """True when a Groq key is configured (config or env).

    Module-level seam so tests can patch it without a key.
    Never raises: an unconsultable key reads as absent.
    """
    try:
        from ..voice.cloud_text_helpers import _groq_api_key
        return bool(_groq_api_key())
    except Exception:  # noqa: BLE001 — no key, no leg
        return False


def _build_groq_session():
    """Construct a real GroqSession (cheap: no network, no start).

    Module-level seam so tests can substitute a fake Groq leg.
    Raises on construction failure; the caller treats that as
    no Groq leg.
    """
    from ..voice.cloud_text_client import GroqSession
    return GroqSession()


class CompanionSessionHolder:
    """Holds the (Groq, local) companion pair alongside the primary.

    Constructed with an injected local builder; start() is sync,
    best-effort, and never raises. The pair is available through
    .pair once both legs are real and ready; .companion() returns
    a single leg for the Stage 1 free-form loop later.
    """

    def __init__(self, build_local_inner=None, local_backend=None):
        self._build_local_inner = build_local_inner
        self._local_backend = local_backend
        self._groq = None
        self._groq_key_absent = False
        self._local = None
        self._local_kind = None  # "builtin" | "ollama" | None
        self._local_ready = False
        self._local_failed = False
        self._preload_task = None
        self._generation = 0
        self._started = False

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Start both legs. Sync, best-effort, never raises.

        The Groq leg is constructed only when a key is present
        (no network at start). The local leg is built by the
        injected builder; a builtin leg kicks off its model
        preload on the running loop's executor (or synchronously
        when there is no running loop — diagnostics/tests only).
        A leg that cannot be built leaves the pair None.
        """
        if self._started:
            return
        self._started = True
        try:
            if _groq_key_present():
                try:
                    self._groq = _build_groq_session()
                except Exception:  # noqa: BLE001 — no Groq leg
                    self._groq = None
            else:
                self._groq_key_absent = True
        except Exception:  # noqa: BLE001 — key check never raises
            self._groq = None
        if self._build_local_inner is not None:
            try:
                session = self._build_local_inner()
            except Exception:  # noqa: BLE001 — no local leg
                session = None
            if session is not None:
                self._build_local_leg(session)

    def _build_local_leg(self, session) -> None:
        """Attach a built local session as the local leg (or not).

        Backend decides the readiness path, not hasattr order:
        ollama legs need no preload (model in the server);
        builtin legs need the model preloaded and resident; a
        session with no chat method (Lite) is never a leg.
        """
        backend = self._local_backend
        if backend is None:
            try:
                from ..config import config as _config
                backend = (_config.local_backend or "builtin").strip().lower()
            except Exception:  # noqa: BLE001 — default is builtin
                backend = "builtin"
        if backend == "ollama":
            if callable(getattr(session, "_ollama_chat", None)):
                self._local = session
                self._local_kind = "ollama"
                self._local_ready = True
            return
        if callable(getattr(session, "_llama_chat", None)):
            self._local = session
            self._local_kind = "builtin"
            self._kick_preload()

    def _kick_preload(self) -> None:
        """Preload the builtin leg's model (executor only).

        Mirrors the standby preload pattern
        (BrainSessionCoreMixin._preload_local_session): load the
        model WITHOUT starting the session — no mic, no audio, no
        statuses. The result gates _local_ready; a failed load
        leaves the leg unready and the pair None.
        """
        session = self._local
        if session is None:
            return
        loader = getattr(session, "preload_model_only", None)
        if not callable(loader):
            loader = getattr(session, "_ensure_llama", None)
        if not callable(loader):
            self._local_failed = True
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            # No running loop (diagnostics/tests): load inline.
            try:
                self._local_ready = bool(loader())
                if not self._local_ready:
                    self._local_failed = True
            except Exception:  # noqa: BLE001 — load failure
                self._local_failed = True
            return
        generation = self._generation
        try:
            fut = loop.run_in_executor(None, loader)
        except Exception:  # noqa: BLE001 — no executor, no leg
            self._local_failed = True
            return
        self._preload_task = fut
        fut.add_done_callback(
            lambda f: self._on_preload_done(f, generation))

    def _on_preload_done(self, fut, generation) -> None:
        """Record a preload result (generation-guarded)."""
        try:
            if generation != self._generation:
                return  # released meanwhile; stale result
            if fut.cancelled():
                return
            if bool(fut.result()):
                self._local_ready = True
            else:
                self._local_failed = True
        except Exception:  # noqa: BLE001 — load failure
            self._local_failed = True

    def stop(self) -> None:
        """Release both sessions cleanly. Never raises.

        The local session was never started, only preloaded, so
        release drops its model refs and collects — the same
        pattern as the standby release
        (_release_standby_session), applied to the holder's OWN
        session only, never to the primary inner or the standby.
        The Groq leg was never started either; stop() is safe on
        an un-started GroqSession (flags + task cancels only).
        """
        try:
            self._generation += 1
            task = self._preload_task
            self._preload_task = None
            if task is not None:
                try:
                    task.cancel()
                except Exception:  # noqa: BLE001 — cancel is best-effort
                    pass
            local = self._local
            self._local = None
            if local is not None:
                try:
                    for attr in ("_llama", "_whisper"):
                        try:
                            if getattr(local, attr, None) is not None:
                                setattr(local, attr, None)
                        except Exception:  # noqa: BLE001 — one bad attr
                            pass  # must not block the other or the gc
                    try:
                        gc.collect()
                    except Exception:  # noqa: BLE001 — gc is best-effort
                        pass
                except Exception:  # noqa: BLE001 — release never raises
                    pass
            groq = self._groq
            self._groq = None
            if groq is not None:
                try:
                    groq.stop()
                except Exception:  # noqa: BLE001 — release never raises
                    pass
            self._local_ready = False
            self._local_failed = False
            self._local_kind = None
            self._started = False
        except Exception:  # noqa: BLE001 — stop never raises
            pass

    # ------------------------------------------------------------- accessors
    def _local_usable(self) -> bool:
        """True when the local leg is built, ready, and resident."""
        if self._local is None or not self._local_ready:
            return False
        if self._local_kind == "builtin":
            return getattr(self._local, "_llama", None) is not None
        return True

    @property
    def pair(self):
        """(groq_session, local_session) when both legs are real and
        ready, else None. The pair is what second_opinion_from_
        sessions takes; a None pair means the check stays OFF."""
        if self._groq is None or not self._local_usable():
            return None
        return (self._groq, self._local)

    @property
    def groq(self):
        """The Groq leg (or None) — explicit choice for Stage 1."""
        return self._groq

    @property
    def local(self):
        """The local leg (or None) — explicit choice for Stage 1."""
        return self._local

    def companion(self):
        """ONE companion for the Stage 1 free-form loop later.

        Default prefers on-device (the local leg when usable —
        no key, no network); falls back to the Groq leg. Stage 1
        may choose explicitly via .groq / .local instead.
        """
        if self._local_usable():
            return self._local
        return self._groq

    def missing_legs(self):
        """Which legs are missing, precisely, as plain words.

        [] when the pair is ready. "groq key" when no key is
        configured; "groq session" when the key was present but
        the leg could not be built; "local model" when no local
        leg could be built or its model failed to load; "local
        model loading" while the preload is still in flight.
        """
        if not self._started:
            return ["companion holder not started"]
        missing = []
        if self._groq is None:
            missing.append("groq key" if self._groq_key_absent
                           else "groq session")
        if self._local is None:
            missing.append("local model")
        elif not self._local_ready:
            if (self._preload_task is not None
                    and not self._preload_task.done()):
                missing.append("local model loading")
            else:
                missing.append("local model")
        elif (self._local_kind == "builtin"
                and getattr(self._local, "_llama", None) is None):
            missing.append("local model")
        return missing


def companion_pair():
    """(groq, local) pair from the live session's holder, else None.

    The construction-site accessor: lazy live_session import
    (B12 pattern), guarded — any failure reads as no pair and
    the check stays OFF.
    """
    try:
        from .brain import live_session
        return live_session.companion_pair()
    except Exception:  # noqa: BLE001 — no pair, check stays OFF
        return None


def companion_missing_legs():
    """Missing companion legs from the live session's holder."""
    try:
        from .brain import live_session
        return live_session.companion_missing_legs()
    except Exception:  # noqa: BLE001 — holder unconsultable
        return ["companion holder not started"]

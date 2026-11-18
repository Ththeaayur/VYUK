# BrainSession mixin (B5 split): methods moved verbatim from
# core/brain/brain.py; called via self on the composed class.

import asyncio
from ..taskwake import cancel_waking
from .brain_helpers import _bump_epoch  # INTENTIONAL (brain split B5): helper moved out, imported where used
from .brain_helpers import _ambient_packet_text  # INTENTIONAL (brain split B5): helper moved out, imported where used


class BrainSessionStatusMixin:

    def set_speech_speed_override(self, factor):
        """v5.17 (Mouth M18): forwards a speech-pace override to
        the inner session when it supports one (the local/lite
        sessions do; the cloud sessions don't, and for them this is
        an honest no-op — their voices set their own pace)."""
        inner = self._inner
        if inner is None:
            return
        setter = getattr(inner, "set_speech_speed_override", None)
        if callable(setter):
            try:
                setter(factor)
            except Exception:  # noqa: BLE001
                pass

    def refresh_ambient_context(self) -> bool:
        """N4 refresh seam (v5.44): re-pulls the ambient packet
        and forwards it to the live session when it differs
        materially from what the session last had (any change in
        the packet text counts — the packet is small and already
        only carries what is worth saying). Live receives it as a
        silent context message (never spoken); the text brains
        stash it and fold it into the next turn's assembly.

        SYNCHRONOUS by contract: the GUI's watcher path calls
        this from its own thread with no loop to await on, so
        forwarding is fire-and-forget — the Live inner schedules
        its send onto the session loop itself. Returns True when
        a changed packet was forwarded. A missing/failing
        ambient module, an empty packet, an unchanged packet, or
        no live session all degrade to False — today's
        behaviour, never an error."""
        packet = _ambient_packet_text()
        if not packet:
            return False
        last = self._ambient_last
        if last is None:
            inner0 = self._inner
            last = (getattr(inner0, "_ambient_packet", "")
                    if inner0 is not None else "")
        if packet == last:
            self._ambient_last = packet
            return False
        self._ambient_last = packet
        inner = self._inner
        if inner is None:
            return False
        refresher = getattr(inner, "refresh_ambient_context", None)
        if callable(refresher):
            try:
                outcome = refresher(packet)
                if asyncio.iscoroutine(outcome):
                    # An async inner seam cannot be driven from
                    # this thread — close it cleanly and fall
                    # through to the stash paths below.
                    outcome.close()
                else:
                    return True
            except Exception:  # noqa: BLE001 — refresh never breaks
                pass
        setter = getattr(inner, "set_ambient_context", None)
        if callable(setter):
            try:
                setter(packet)
                return True
            except Exception:  # noqa: BLE001
                return False
        try:
            inner._ambient_context_pending = packet
            inner._ambient_packet = packet
            return True
        except Exception:  # noqa: BLE001
            return False

    def provider_status(self) -> dict:
        """Requested-vs-effective provider (C03) + fallback reason.

        Keys: requested (provider asked for at the last start(),
        or None before any start), effective (self._engine, or
        None before any start), fallback_active (True only when
        a cloud→local fallback actually fired this session),
        fallback_reason (the recorded reason, or None),
        frozen_override (True when a frozen brain forced local
        instead of the requested cloud provider),
        differs (requested != effective, either mechanism).
        """
        requested = self._requested_provider
        effective = self._engine
        fallback_active = bool(
            self._fallback_reason is not None and effective == "local"
            and requested in ("gemini", "groq"))
        return {
            "requested": requested,
            "effective": effective,
            "fallback_active": fallback_active,
            "fallback_reason": self._fallback_reason,
            "frozen_override": bool(self._frozen_override),
            "differs": requested is not None and requested != effective,
        }

    def readiness(self) -> dict:
        """Layered readiness probes (C08), replacing the single
        is_connected boolean for anything that needs to explain
        WHAT is not ready.

        Layers (each {"ok": bool, "detail": str}):

        - connection: is a live session up at all.
        - model: can the serving brain answer — for the cloud
          brains the live session IS the model link, so this
          tracks connection; for the on-device builtin brain it
          additionally requires the GGUF to be resident (a
          connected-but-model-less local session answers
          nothing); for Ollama the server serves the model, so
          connection suffices; the lite session answers from
          tools, so connection suffices there too.
        - voice: can spoken input be heard — for Gemini Live the
          live session carries voice itself; for Groq the mic
          loop runs with the session; for the local brains the
          faster-whisper ears must actually be loaded (C05: this
          is the signal _prepare_builtin used to discard — the
          local session now exposes voice_ready instead of
          degrading to typed input silently).
        """
        inner = self._inner
        connected = bool(self.is_connected)
        provider = self._engine or "none"
        if not connected:
            return {
                "provider": provider,
                "connection": {"ok": False,
                               "detail": "no live session"},
                "model": {"ok": False,
                          "detail": "no live session"},
                "voice": {"ok": False,
                          "detail": "no live session"},
            }
        if provider in ("gemini", "groq"):
            model = {"ok": True,
                     "detail": f"{provider} session is live"}
            voice = {"ok": True,
                     "detail": (f"{provider} session carries voice "
                                "input itself")}
        elif inner is not None and hasattr(inner, "_mic_started"):
            # Lite session, keyed on its mic flag rather than a
            # class-name string (which silently misses subclasses
            # and wrappers): the lite mic flag exists on no other
            # inner.
            model = {"ok": True,
                     "detail": "lite session answers from tools"}
            voice = {"ok": bool(getattr(inner, "_mic_started", False)),
                     "detail": ("lite mic is running" if getattr(
                         inner, "_mic_started", False) else
                         "lite mic has not started (voice model "
                         "not ready)")}
        else:
            # LocalLiveSession (or an unknown local inner).
            backend = str(getattr(inner, "_backend", "builtin") or
                          "builtin").strip().lower()
            if backend == "ollama":
                model = {"ok": True,
                         "detail": "Ollama session is live"}
            elif getattr(inner, "_llama", None) is not None:
                model = {"ok": True,
                         "detail": "on-device model is resident"}
            else:
                model = {"ok": False,
                         "detail": "on-device model is not loaded"}
            voice_ready = getattr(inner, "voice_ready", None)
            if callable(voice_ready):
                voice_ready = voice_ready()
            if voice_ready is True:
                voice = {"ok": True,
                         "detail": "speech-recognition ears are loaded"}
            elif voice_ready is False:
                voice = {"ok": False,
                         "detail": ("speech recognition is unavailable "
                                    "(typed input still works)")}
            else:
                voice = {"ok": False,
                         "detail": "voice readiness is unknown for "
                                    "this session"}
        return {
            "provider": provider,
            "connection": {"ok": True, "detail": "session is live"},
            "model": model,
            "voice": voice,
        }

    async def send_text(self, text: str):
        """Delegates to the active inner session; returns a
        delivery result instead of a silent no-op (Phase 1, F04).

        Returns {"status": "accepted"} when the inner session
        took the text, or {"status": "dropped", "reason": ...}
        when nothing was live to take it — including the race
        the GUI's submit-time check cannot see (connected at
        submit, dead by the time the coroutine runs). An inner
        session's own dropped result propagates unchanged;
        legacy inners that return None read as accepted, exactly
        as before. Exceptions still propagate to the caller's
        done callback — a dropped result is not an exception,
        and an exception is not a drop.
        """
        if self._inner is not None and self.is_connected:
            result = await self._inner.send_text(text)
            if isinstance(result, dict) and result.get("status"):
                return result
            return {"status": "accepted"}
        return {"status": "dropped", "reason": "not connected"}

    def stop_reply(self) -> bool:
        """Stop the reply in flight (chat UI, 2026-11-16).

        Delegates to the inner session when it knows how (the
        Gemini live session sets its stop flag — see
        live_client_receive.stop_reply); inners without the
        method (the local session, whose replies arrive as one
        block) return False and the GUI falls back to cancelling
        the in-flight typed send instead. The audio cut is the
        GUI's job, alongside this call. Returns False when
        nothing is connected.
        """
        if self._inner is None or not self.is_connected:
            return False
        _stop = getattr(self._inner, "stop_reply", None)
        if not callable(_stop):
            return False
        try:
            return bool(_stop())
        except Exception:  # noqa: BLE001 — a stop never raises
            return False

    async def send_image_frame(self, image_bytes: bytes, mime_type: str = "image/jpeg"):
        """Delegates to the active inner session; no-op when not connected.

        Returns the inner session's delivery status (v5.53,
        F8 — "delivered" / "degraded" / "no-session" /
        "error" from the vision-capable inners; the legacy
        inners return None, which callers must not read as
        delivery), or "no-session" when nothing is live."""
        if self._inner is not None and self.is_connected:
            return await self._inner.send_image_frame(image_bytes, mime_type)
        return "no-session"

    @property
    def engine_name(self) -> str:
        """The active engine's name, lowercased ("" when nothing
        is live). GAPS-28 (G-V5): the GUI's watch path used to
        read the private ``_engine`` to decide the Gemini notice;
        it reads this instead. ``_engine`` is assigned from the
        same Engine value as the inner's own ``engine`` class
        attribute, so the two never disagree; the inner's name
        is the fallback when no engine was recorded.
        """
        name = getattr(self, "_engine", None)
        if name is not None:
            return str(name).lower()
        inner = self._inner
        if inner is None:
            return ""
        return str(getattr(inner, "engine", "") or "").lower()

    async def send_watch_frame(self, image_bytes: bytes,
                               mime_type: str = "image/jpeg",
                               framing: str = ""):
        """Send one screen-watch frame, routing by engine
        (GAPS-28, G-V5): the Groq inner takes the frame with
        ``question=framing`` (its vision path answers the framing
        question); every other inner takes it through the plain
        proxy, exactly as ``send_image_frame`` does. Returns the
        inner's delivery status, or "no-session" when nothing
        is live.
        """
        if self._inner is None or not self.is_connected:
            return "no-session"
        inner = self._inner
        if getattr(inner, "engine", None) == "groq":
            return await inner.send_image_frame(image_bytes, mime_type,
                                                question=framing)
        return await inner.send_image_frame(image_bytes, mime_type)

    def interrupt_in_flight(self) -> None:
        """Interrupt whatever is in flight (GAPS-28, G-V5 —
        parent fix): the panic-hotkey follow-up used to reach
        into the live session's private ``_inner`` (and its
        ``_inner``) to call the inner's private
        ``_interrupt_playback``, clear its ``_speaking`` flag and
        cancel its pending turn tasks. It calls this instead.
        Same behaviour, same order: each candidate is tried in
        turn; the speaking flag is cleared only where an
        interrupt ran, and the pending tasks are cancelled for
        every candidate whether or not it had one. Every step
        is individually guarded — a panic path that can throw is
        a panic path that doesn't exist — so this never raises,
        and it is a no-op when nothing is live.
        """
        inner = self._inner
        for candidate in (inner, getattr(inner, "_inner", None)):
            if candidate is None:
                continue
            interrupt = getattr(candidate, "_interrupt_playback", None)
            if callable(interrupt):
                try:
                    interrupt()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    candidate._speaking = False
                except Exception:  # noqa: BLE001
                    pass
            for task in list(
                    getattr(candidate, "_turn_tasks", ()) or ()):
                cancel_waking(task)

    def stop(self):
        # Latch BEFORE delegating (v5.17): a stop requested while a
        # cloud start() is still in flight must not be mistaken for
        # a failed connect when that start() returns.
        self._stop_requested = True
        _bump_epoch("brain stop")  # v5.48 (programme item 20)
        if self._inner is not None:
            self._inner.stop()
        # Companion sessions (2026-11-05): release the holder's own
        # sessions. Best-effort and guarded — a companion release
        # failure must never break the primary stop. The holder never
        # touched _inner, the fallback state, or the standby.
        try:
            _holder = getattr(self, "_companion_holder", None)
            if _holder is not None:
                _holder.stop()
        except Exception:  # noqa: BLE001 — companion release never
            pass  # breaks the primary stop

"""Local-live session mixin: Frames out: typed text and shared image frames.

Batch 5 (skills split, local_client closer): moved verbatim
from core/voice/local_client.py; methods resolve self-calls
through the composed LocalLiveSession class.
"""

import asyncio
from .. import (injection)
from .local_client_guards import (_NO_VISION_PROMPT)
from .local_client_vlm import (_VLM_OCR_SPARSE_CHARS, _frame_window_title, _ocr_frame_text, _vlm_mode, _vlm_prompt_for_frame)
from ..memory.memory import (memory_store)


class LocalLiveSessionFramesMixin:
    async def send_text(self, text: str):
        """Sends manual typed user text through the local brain.

        Phase 1 (F04): returns a delivery result instead of a
        silent no-op — {"status": "dropped", "reason": ...} when
        no session is live, {"status": "accepted"} once the turn
        (or a bare-cancel turn) ran. Callers that ignored the old
        None return are unaffected."""
        if not self.is_connected:
            return {"status": "dropped", "reason": "not connected"}
        # Genuine typed user text lifts the injection taint (v5.3);
        # bracketed VYUK-internal injections (watcher notices and
        # the like, which also arrive through this method) do not —
        # they are not the user taking the wheel.
        if not (text or "").lstrip().startswith("["):
            injection.clear_taint()
        if self.on_transcript:
            self.on_transcript("user", text)
        memory_store.log_message("user", text)
        async with self._turn_lock:
            # v5.42 (H14): typed bare cancels take the same
            # pre-model stop path as spoken ones.
            if await self._handle_cancel_all(text):
                return {"status": "accepted"}
            await self._brain_turn(text)
        return {"status": "accepted"}

    @staticmethod
    def _describe_frame(image_bytes: bytes) -> str:
        """Builds the brain-turn prompt for a shared frame (sync —
        executor only; the OCR can block).

        OCR-vision (v2.7): the frame's bytes go through the same PIL +
        pytesseract read the ocr_screen tool uses (see
        _ocr_frame_text). When text is read, the prompt carries it —
        plus the foreground window's title when it can be had cheaply
        — and instructs the model to answer from that text only. When
        OCR yields nothing, the prompt is the pre-v2.7 honest
        _NO_VISION_PROMPT, unchanged.

        VLM preference (v5.45, E5b — the heuristic, documented):
        when the local vision pair is provisioned, frames are
        described by describe_frame_vlm instead of OCR in the
        cases OCR is the wrong tool — mode "always" (config
        local_vlm_describe / LOCAL_VLM_DESCRIBE) tries the VLM
        for every frame; the default "auto" keeps OCR as the fast
        path for text-rich frames and prefers the VLM when OCR's
        yield is sparse (< _VLM_OCR_SPARSE_CHARS — a visual frame)
        or empty; "never" restores the pre-E5b OCR-only path.
        Every VLM failure degrades to the OCR path below,
        byte-for-byte.
        """
        vlm_mode = _vlm_mode()
        if vlm_mode == "always":
            vlm_prompt = _vlm_prompt_for_frame(image_bytes)
            if vlm_prompt:
                return vlm_prompt
        ocr_text = _ocr_frame_text(image_bytes)
        fell_back = False
        if not ocr_text:
            # E7b (v5.44): a CROPPED frame (the E7 foreground-
            # window default) that reads as empty may simply be
            # the wrong window — recapture the FULL screen once
            # and re-read, the same fallback ocr_screen performs.
            # Only when the original capture actually was a crop
            # (vision.last_capture_info says so); an uncropped
            # empty frame keeps the honest no-vision prompt.
            try:
                from ..desktop import vision as _vision  # Phase 3 V1b: moved to core/voice/ (D4c: vision hub moved to core/desktop/)

                info = _vision.last_capture_info()
                if info and info.get("cropped"):
                    image2, _meta2 = _vision.capture_native_image(
                        full_screen=True)
                    import io as _io

                    buf = _io.BytesIO()
                    image2.save(buf, format="JPEG")
                    retry_text = _ocr_frame_text(buf.getvalue())
                    if retry_text:
                        ocr_text = retry_text
                        fell_back = True
            except Exception:  # noqa: BLE001 — fallback is best-effort
                ocr_text = ocr_text or ""
            if not ocr_text:
                # E5b: an OCR-empty frame may be pure picture —
                # the VLM gets its chance before the honest
                # no-vision prompt is returned.
                if vlm_mode != "never":
                    vlm_prompt = _vlm_prompt_for_frame(image_bytes)
                    if vlm_prompt:
                        return vlm_prompt
                return _NO_VISION_PROMPT
        if (vlm_mode == "auto"
                and len(ocr_text) < _VLM_OCR_SPARSE_CHARS):
            # E5b "auto": sparse OCR = a visual frame wearing a
            # caption's worth of text — prefer the VLM's read of
            # the whole picture when it can give one.
            vlm_prompt = _vlm_prompt_for_frame(image_bytes)
            if vlm_prompt:
                return vlm_prompt
        title = _frame_window_title()
        window_part = f" Window: {title}." if title else ""
        # Screen text is the classic injection surface (v5.3): it
        # reaches the brain inside the untrusted envelope, screened
        # for the session taint like every other external producer.
        wrapped = injection.wrap_untrusted("screen text", ocr_text)
        prompt = (
            f"[The user shared their screen.{window_part} "
            f"Text visible on screen: {wrapped}] "
            "[Answer using only the screen text above: describe what "
            "you can read there and help with it. Do not invent any "
            "detail you cannot read.]"
        )
        if fell_back:
            # Noted the way ocr_screen notes it (v5.44, E7b).
            prompt += (" The focused window held no readable text, "
                       "so I read the whole screen instead.")
        # E10 remainder (v5.43): the frame's true capture geometry
        # (core/vision.capture_geometry_line — the last capture's
        # true screen size vs the sent image size) leads the
        # description, so a local/Groq frame answer can reason
        # about coordinates against the real contract, the way the
        # take_screenshot tool result already states it. No
        # geometry on record -> the description is byte-identical
        # to before; geometry trouble never sinks the frame.
        try:
            from ..desktop.vision import capture_geometry_line  # Phase 3 V1b: moved to core/voice/ (D4c: vision hub moved to core/desktop/)

            geo_line = capture_geometry_line()
        except Exception:  # noqa: BLE001
            geo_line = ""
        return f"{geo_line} {prompt}" if geo_line else prompt

    async def send_image_frame(self, image_bytes: bytes, mime_type: str = "image/jpeg"):
        """A brain turn about a shared frame, grounded in OCR.

        The local brain has no image model, so the frame is read with
        OCR (see _describe_frame) and the model answers from the text
        that was actually read; a frame with no readable text gets the
        honest no-vision turn. Both backends share this path —
        _brain_turn dispatches to builtin/ollama. The bracketed prompt
        goes into history only — it is never echoed as a user
        transcript line or written to the conversation log.
        """
        if not self.is_connected:
            return
        async with self._turn_lock:
            loop = asyncio.get_running_loop()
            prompt = await loop.run_in_executor(
                None, self._describe_frame, image_bytes
            )
            await self._brain_turn(prompt)


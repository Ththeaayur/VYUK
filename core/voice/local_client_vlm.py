from . import (local_assets)
from .. import (injection)
from ..config import (config)
from .. import config_spec
from typing import (Any)
import json
import sys
import threading


_VLM_DESCRIBE_PROMPT = (
    "Describe this screenshot in plain words for someone who "
    "cannot see it: what app or page it shows, what is on it, "
    "any visible text that matters, and anything that looks like "
    "an error, a prompt waiting for an answer, or a notable "
    "change. Be specific and factual; do not invent details."
)


_VLM_MAX_TOKENS = 320


_vlm_llama = None


_vlm_llama_failed = False


_vlm_lock = threading.Lock()


def vlm_handler_available() -> bool:
    """True when the installed llama_cpp build exposes the
    Qwen2.5-VL mtmd chat handler this module drives. A build
    without it (older wheel, a non-mtmd build) is a normal,
    detected state — the caller degrades to OCR."""
    try:
        from llama_cpp.llama_chat_format import Qwen25VLChatHandler

        return Qwen25VLChatHandler is not None
    except Exception:  # noqa: BLE001 — any import surprise = unavailable
        return False


def _vlm_mode() -> str:
    """The describe-path override: the spec value
    (config.local_vlm_describe / env LOCAL_VLM_DESCRIBE) — "auto"
    (the default heuristic in _describe_frame), "always" (VLM first
    for every frame) or "never" (OCR only, pre-E5b behaviour).
    Anything unrecognised is "auto"."""
    raw = config_spec.env("LOCAL_VLM_DESCRIBE")
    mode = str(raw or "").strip().lower()
    return mode if mode in ("auto", "always", "never") else "auto"


def _ensure_vlm_llama():
    """Loads (and caches) the VLM Llama with its mtmd chat
    handler. Raises on failure — describe_frame_vlm converts any
    raise into the OCR degrade. Sync — executor only."""
    global _vlm_llama, _vlm_llama_failed
    if _vlm_llama is not None:
        return _vlm_llama
    if _vlm_llama_failed:
        raise RuntimeError("VLM load already failed this run")
    with _vlm_lock:
        if _vlm_llama is not None:
            return _vlm_llama
        try:
            from llama_cpp import Llama
            from llama_cpp.llama_chat_format import Qwen25VLChatHandler

            model_path, mmproj_path = local_assets.vlm_paths()
            handler = Qwen25VLChatHandler(
                clip_model_path=str(mmproj_path), verbose=False)
            _vlm_llama = Llama(
                model_path=str(model_path),
                chat_handler=handler,
                n_ctx=4096,
                n_gpu_layers=-1,
                verbose=False,
            )
        except Exception:
            # A GPU-less build may refuse n_gpu_layers=-1 — one
            # CPU retry, the _ensure_llama pattern; a second
            # failure latches so describes stop paying load costs.
            try:
                from llama_cpp import Llama
                from llama_cpp.llama_chat_format import (
                    Qwen25VLChatHandler,
                )

                model_path, mmproj_path = local_assets.vlm_paths()
                handler = Qwen25VLChatHandler(
                    clip_model_path=str(mmproj_path), verbose=False)
                _vlm_llama = Llama(
                    model_path=str(model_path),
                    chat_handler=handler,
                    n_ctx=4096,
                    n_gpu_layers=0,
                    verbose=False,
                )
            except Exception:
                _vlm_llama_failed = True
                raise
    return _vlm_llama


def describe_frame_vlm(image_bytes: bytes,
                       question: str = "") -> str | None:
    """A vision-language description of a shared frame, or None
    when the VLM cannot answer (see the degrade contract above).

    `question` rides along when the caller has one; the default
    is the standing describe prompt. Sync — executor only (the
    load + inference block). The returned text is a DESCRIPTION
    for the brain to answer from, exactly the role OCR text plays
    in _describe_frame — callers wrap it as untrusted screen
    content the same way."""
    if not image_bytes:
        return None
    # B5: the Qwen3-VL leg first, through the one vision adapter.
    # When the Qwen3-VL pair is ready the answer comes from it;
    # anything else (weights missing, handler missing, load or
    # inference failure) falls through to the existing Qwen2.5-VL
    # path below, unchanged — a machine without Qwen3-VL weights
    # behaves exactly as before.
    try:
        from ..vision_backend import describe as _vb_describe

        result = _vb_describe(image_bytes, question)
        if result.get("ok") and (result.get("text") or "").strip():
            return result["text"].strip()
    except Exception:  # noqa: BLE001 — adapter failure = old path
        pass
    try:
        if not local_assets.vlm_ready():
            return None
        if not vlm_handler_available():
            return None
        llama = _ensure_vlm_llama()
        import base64

        data_uri = ("data:image/jpeg;base64,"
                    + base64.b64encode(image_bytes).decode("ascii"))
        prompt = (question or "").strip() or _VLM_DESCRIBE_PROMPT
        reply = llama.create_chat_completion(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt},
                ],
            }],
            max_tokens=_VLM_MAX_TOKENS,
            temperature=0.2,
        )
        choices = reply.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        text = (message.get("content") or "").strip()
        return text or None
    except Exception:  # noqa: BLE001 — every failure is the OCR degrade
        return None


_VLM_OCR_SPARSE_CHARS = 40


def _vlm_prompt_for_frame(image_bytes: bytes) -> str | None:
    """The brain-turn prompt for a frame the VLM described, or
    None when the VLM couldn't describe it (the caller then takes
    the OCR path, unchanged). Same shape and discipline as the
    OCR prompt in LocalLiveSession._describe_frame: the
    description is untrusted screen content, enveloped by
    injection.wrap_untrusted, and the model is told to answer
    from it only."""
    description = describe_frame_vlm(image_bytes)
    if not description:
        return None
    title = _frame_window_title()
    window_part = f" Window: {title}." if title else ""
    wrapped = injection.wrap_untrusted("screen description", description)
    prompt = (
        f"[The user shared their screen.{window_part} "
        f"A vision model on this PC described the frame: {wrapped}] "
        "[Answer using only that description: say what the frame "
        "shows and help with it. Do not invent any detail the "
        "description does not contain.]"
    )
    try:
        from ..desktop.vision import capture_geometry_line  # Phase 3 V1b: moved to core/voice/ (D4c: vision hub moved to core/desktop/)

        geo_line = capture_geometry_line()
    except Exception:  # noqa: BLE001 — geometry never sinks a frame
        geo_line = ""
    return f"{geo_line} {prompt}" if geo_line else prompt


def _ocr_frame_text(image_bytes: bytes) -> str:
    """Extracts visible text from a shared frame's image bytes.

    Returns "" on ANY failure — pytesseract/PIL missing, the Tesseract
    binary absent, an undecodable frame, or genuinely no readable
    text; the caller (LocalLiveSession._describe_frame) falls back to
    the honest no-vision prompt. Never raises. Sync — executor only.
    """
    if not image_bytes:
        return ""
    try:
        import io

        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        # v5.44 (E2b): the frame OCR gets the SAME treatment the
        # tools' OCR gets (batch 1's E2): the resolved engine via
        # configure_pytesseract, the language from the user's
        # preference via resolve_ocr_language, and the explicit
        # --psm 6 --oem 1 config via ocr_tesseract_config — all
        # from core/voice/local_assets. Any failure in that routing
        # degrades to the plain default call it replaces.
        try:
            from . import local_assets  # Phase 3 V2: local_assets now in core/voice/; V1b interim closed

            ocr_config, ocr_lang, _ocr_note = (
                local_assets.ocr_tesseract_config())
            local_assets.configure_pytesseract(pytesseract)
            text = pytesseract.image_to_string(
                image, lang=ocr_lang, config=ocr_config).strip()
        except Exception:  # noqa: BLE001 — degrade to the plain read
            text = pytesseract.image_to_string(image).strip()
    except Exception:  # noqa: BLE001 — OCR is best-effort by design
        return ""
    if len(text) > 1500:
        text = text[:1500]
    return text


def _frame_window_title() -> str | None:
    """The foreground window's title for a shared frame, or None.

    Reuses core/desktop/control.py's get_active_window() (the same helper the
    get_active_window tool serves). Windows only — off-Windows, or on
    any failure, None: the title is a nicety, never a blocker.
    """
    if sys.platform != "win32":
        return None
    try:
        from ..desktop import control  # Phase 3 V1b: moved to core/voice/ (D4a: control hub moved to core/desktop/)

        result = control.get_active_window()
    except Exception:  # noqa: BLE001
        return None
    prefix = "Active window: "
    if result.startswith(prefix):
        title = result[len(prefix):].strip()
        return title or None
    return None


def _result_to_text(result: Any) -> str:
    """Flattens a tools.execute() result dict into the string Ollama sees."""
    if isinstance(result, dict):
        if result.get("status") == "success":
            value = result.get("result")
            return value if isinstance(value, str) else json.dumps(value, default=str)
        return f"Error: {result.get('message', 'unknown error')}"
    return str(result)


def _spoken_fallback(result: Any, text: str) -> str:
    """v5.51 (M-4): when a brain turn falls back to speaking a
    tool result itself (the model gave no answer), an ERROR
    result is spoken as core/voice/spoken_errors' butler line — the
    raw "Error: {message}" rendering above stays for the
    model's own reading (chat/feed), never for the ears. A
    success result keeps `text` byte-identical. Audit A5: the
    contract statuses, not just legacy "error"."""
    from ..funnel.tool_result import is_non_success as _is_non_success  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_result moved to core/funnel/

    if isinstance(result, dict) and _is_non_success(result):
        try:
            from .spoken_errors import spoken_error_text  # Phase 3 V1b: moved to core/voice/

            line = spoken_error_text(result)
            if line:
                return line
        except Exception:  # noqa: BLE001 — speech must not break
            pass
    return text


_FEED_TEXT_LIMIT = 1500


def _feed_text(result: Any) -> str:
    """_result_to_text() capped for feeding back into a brain turn
    (v5.19): a tool result re-enters the prompt, and an uncapped one
    (a long file listing, a page of search results) could eat the
    local model's whole context on its own. 1500 chars ≈ 470
    estimated tokens — enough to answer from, small enough to leave
    room to think. The spoken/displayed result is NOT capped; only
    the copy the model reads is."""
    text = _result_to_text(result)
    if len(text) > _FEED_TEXT_LIMIT:
        return text[:_FEED_TEXT_LIMIT] + "… (result truncated)"
    return text


def _executed_tool_succeeded(result: Any) -> bool:
    """Turn-truthfulness (6th-B06): did this executed call SUCCEED?

    The funnel's contract statuses decide — a failure, refusal,
    timeout, or unknown outcome is not a success, even though the
    call ran. Non-dict results are not funnel-shaped and keep the
    helper's legacy reading (not non-success). Callers use this
    for the attempted-vs-succeeded distinction: tools_used says a
    tool ran; only a succeeded result is fresh evidence a state
    question may be answered from, or a "Done." may be claimed on.
    """
    from ..funnel.tool_result import is_non_success as _is_non_success  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_result moved to core/funnel/

    return not _is_non_success(result)


def _format_action_trace(actions: list[dict[str, Any]]) -> str:
    """One compact line per recorded action: tool, status, outcome
    snippet. Statuses are succeeded / failed / rejected (rejected =
    nothing ran: schema validation or an unknown tool name). No
    status here claims independent verification — that belongs to
    durable receipts (Phase 2), not to this trace."""
    parts = []
    for action in actions:
        outcome = str(action.get("outcome") or "")[:120]
        parts.append(
            f"{action.get('tool', '?')} — {action.get('status', '?')}"
            + (f" ({outcome})" if outcome else ""))
    return "; ".join(parts)

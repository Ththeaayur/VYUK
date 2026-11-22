# Batch 1 (plan §3.3): epoch/name/transcription/guards, moved verbatim
# from core/voice/local_client.py. INTENTIONAL: imports below are the
# subset of the old module's import block that this group's code uses.
import json
import re
from typing import Any
from ..config import DATA_DIR, config
from ..funnel.tools_registry import tools
from ..languages import reply_language_directive, resolve_transcription_profile
from ..memory.memory import memory_store
from . import local_assets


def _bump_epoch(reason: str) -> None:
    """v5.48 (programme item 20): advance the session epoch at a
    session end/start seam, voiding any confirmation approval
    still in flight from the previous session (core/session_epoch).
    Guarded: epoch bookkeeping never breaks a session."""
    try:
        from .. import session_epoch  # Phase 3 V1b: moved to core/voice/

        session_epoch.bump_epoch(reason)
    except Exception:  # noqa: BLE001
        pass


WHISPER_BEAM_SIZE = 5          # the ONE switch for decode width.


WHISPER_NO_SPEECH_THRESHOLD = 0.6    # faster-whisper default, explicit


WHISPER_LOG_PROB_THRESHOLD = -1.0    # faster-whisper default, explicit


_PROMPT_APP_NAMES = (
    "Chrome", "Spotify", "Notepad", "Calculator", "File Explorer",
    "Settings", "WhatsApp", "YouTube",
)


_INITIAL_PROMPT_MAX_CHARS = 200


def _known_user_name() -> str:
    """The user's name as memory knows it, for the Whisper biasing
    prompt — or "" when memory holds no name fact. The seam is
    core/memory/memory.py's facts (there is no user_name config field):
    a fact whose KEY is a name key ("name", "user_name",
    "full_name", "my_name", …) in any category. Sanitised hard —
    a single short line of name-like characters only — because the
    value lands inside a recogniser prompt; anything else, and any
    failure, degrades to "" (no bias) rather than breaking STT."""
    try:
        rows = memory_store.search_facts("name")
    except Exception:  # noqa: BLE001 — biasing must never break ears
        return ""
    # The key spellings the app itself files names under:
    # core/learning.py's extractor uses "name" / "preferred_name",
    # and core/skills_fun.py's _user_first_name accepts "name",
    # "first name", "user name", "my name", "full name" — all
    # normalised to underscores before the comparison below.
    name_keys = {"name", "preferred_name", "first_name", "user_name",
                 "username", "full_name", "my_name", "your_name"}
    for row in rows or []:
        try:
            key = re.sub(r"[^a-z]+", "_", str(row.get("key", "")).lower())
            if key.strip("_") not in name_keys:
                continue
            value = " ".join(str(row.get("value", "")).split())
        except Exception:  # noqa: BLE001
            continue
        if (1 <= len(value) <= 40
                and re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", value)):
            return value
    return ""


def _transcription_initial_prompt(resolved) -> str:
    """The biasing prompt for one transcription call: her name,
    the user's name (when known), the profile's command terms and
    the common app names, comma-joined and capped — terms drop
    from the end when the cap bites, names never do."""
    parts: list[str] = []
    assistant = " ".join(
        str(getattr(config, "assistant_name", "") or "").split())
    if assistant:
        parts.append(assistant)
    user_name = _known_user_name()
    if user_name:
        parts.append(user_name)
    terms = [str(t) for t in (resolved.prompt_terms or ())]
    terms += [name for name in _PROMPT_APP_NAMES if name not in terms]
    prompt = ", ".join(parts + terms)
    while terms and len(prompt) > _INITIAL_PROMPT_MAX_CHARS:
        terms.pop()
        prompt = ", ".join(parts + terms)
    return prompt


def _transcription_settings() -> dict[str, Any]:
    """The kwargs for the faster-whisper transcribe() call (A1) —
    see the section header. `language` is always present (None is
    the deliberate auto-detect escape); `initial_prompt` is
    present whenever it built non-empty."""
    resolved = resolve_transcription_profile()
    settings: dict[str, Any] = {
        "language": resolved.whisper_language,
        "beam_size": WHISPER_BEAM_SIZE,
        "no_speech_threshold": WHISPER_NO_SPEECH_THRESHOLD,
        "log_prob_threshold": WHISPER_LOG_PROB_THRESHOLD,
    }
    prompt = _transcription_initial_prompt(resolved)
    if prompt:
        settings["initial_prompt"] = prompt
    return settings


OLLAMA_TAGS_TIMEOUT_S = 3


OLLAMA_CHAT_TIMEOUT_S = 120


MAX_TOOL_ROUNDS = 6            # v5.44 (B7): the local default —


MAX_TOOL_REPAIRS = 2           # self-correction cap inside one builtin


TOOL_SHAPE_FALLBACK = ("I couldn't get that action into the right "
                       "shape — nothing was run.")


HISTORY_WINDOW = 12            # prior turns sent with each chat request


HISTORY_CAP = 24               # turns kept on the instance


LLM_N_CTX = 4096               # llama context cap (v3.2 RAM diet: was


IDLE_UNLOAD_S = 600.0          # brain/STT idle this long → release the


HOUSEKEEPING_TICK_S = 60.0     # the session housekeeping loop cadence


_NO_VISION_PROMPT = (
    "[The user shared a screen image, but the local brain has no vision. "
    "Briefly tell them vision needs the Gemini brain, and offer what you "
    "can do instead.]"
)


_STATE_QUESTION_RE = re.compile(
    r"\b(?:what(?:'s| is) the (?:current )?time"
    r"|what time is it"
    r"|current time"
    r"|tell me the time"
    r"|what(?:'s| is) the (?:current )?date"
    r"|what date is it"
    r"|what day is (?:it|today))\b",
    re.IGNORECASE,
)


_BATTERY_QUESTION_RE = re.compile(
    r"\b(?:what(?:'s| is) (?:my |the )?(?:battery|charge)"
    r"|how much (?:battery|charge)"
    r"|battery (?:level|percentage|percent|status|left)"
    r"|is (?:my |the )?battery"
    r"|charge (?:level|percentage|percent|status))\b",
    re.IGNORECASE,
)


_NETWORK_QUESTION_RE = re.compile(
    r"\b(?:am i (?:online|connected)"
    r"|is (?:the )?(?:internet|wi-?fi|network) "
    r"(?:working|up|down|connected|on)"
    r"|what(?:'s| is) (?:my |the )?(?:wi-?fi|network|internet)"
    r"|network status"
    r"|internet (?:status|connection)"
    r"|wi-?fi (?:status|signal))\b",
    re.IGNORECASE,
)


_DOWNLOAD_QUESTION_RE = re.compile(
    r"\b(?:how(?:'s| is) (?:my |the )?download"
    r"|download (?:status|progress)"
    r"|is (?:my |the )?download (?:done|finished|complete)"
    r"|has (?:my |the )?download (?:finished|completed)"
    r"|how much of (?:my |the )?download)\b",
    re.IGNORECASE,
)


_STATE_GUARD_RULES = (
    ("time/date", _STATE_QUESTION_RE, "get_current_time_date", {}),
    ("battery", _BATTERY_QUESTION_RE, "battery_info", {}),
    ("network", _NETWORK_QUESTION_RE, "network_control",
     {"action": "status"}),
    ("download", _DOWNLOAD_QUESTION_RE, "download_eta", {}),
)


def _state_guard_for(user_text: str | None) -> tuple[str, dict] | None:
    """The forced-repair (tool, arguments) for a live-state
    question, or None when the text matches no guarded class
    (v5.43, B11 — see _STATE_GUARD_RULES). A rule only fires when
    its tool is actually registered: a build without the tool
    gets no guard for that class rather than a forced call into
    the void. Pure apart from the registry read; never raises."""
    text = user_text or ""
    if not text:
        return None
    try:
        registered = {d["name"] for d in tools.get_declarations()}
    except Exception:  # noqa: BLE001 — a broken registry read
        registered = set()  # must not un-guard every class
    for _label, pattern, name, args in _STATE_GUARD_RULES:
        if pattern.search(text) and (not registered
                                     or name in registered):
            return (name, dict(args))
    return None


def _max_tool_rounds() -> int:
    """Tool rounds allowed in one local brain turn (v5.44, B7):
    config.local_tool_rounds (default 6, env LOCAL_TOOL_ROUNDS),
    with the module constant as the fallback for a config that
    predates the field. A non-positive or garbled value can never
    shrink a turn to nothing — the constant wins instead."""
    try:
        value = int(getattr(config, "local_tool_rounds",
                            MAX_TOOL_ROUNDS))
    except (TypeError, ValueError):
        value = MAX_TOOL_ROUNDS
    return value if value > 0 else MAX_TOOL_ROUNDS


def prior_turn_failed_from_trace(trace) -> bool:
    """True when the last recorded turn's actions include a
    failure or rejection (7th-B09 turn-start feature). Turns
    with no recorded actions, and an absent trace, read as
    not-failed — never assume trouble without evidence."""
    try:
        entries = trace or []
        if not entries:
            return False
        actions = (entries[-1] or {}).get("actions") or []
        return any((a or {}).get("status") in ("failed", "rejected")
                   for a in actions)
    except Exception:  # noqa: BLE001 — feature never breaks a turn
        return False


def context_pressure_from_history(history, budget_tokens: int) -> float:
    """Estimated context fullness 0..1 for turn-start features
    (7th-B09): estimated history tokens over the turn budget.
    A broken budget or empty history reads as 0.0 — pressure
    must be measured, never assumed. The estimate uses the
    heuristic tokenizer (no model handle at turn start)."""
    try:
        if not budget_tokens or budget_tokens <= 0:
            return 0.0
        text = "\n".join(str((m or {}).get("content") or "")
                         for m in (history or []))
        if not text.strip():
            return 0.0
        from . import local_client as _lc  # INTENTIONAL (batch 1, B23):
        # _est_tokens stays in the facade (batch-2 region); phase2 patches
        # facade _est_tokens and drives turn paths through it, so this call
        # must resolve through the facade module object at call time — a
        # snapshot import here would go stale under the patch.
        return min(1.0, _lc._est_tokens(text) / budget_tokens)
    except Exception:  # noqa: BLE001 — feature never breaks a turn
        return 0.0


def local_system_prompt() -> str:
    """The local turn's system-prompt head: the persona, the
    reply-language directive, then the verbosity directive.
    Extracted from the builtin and Ollama turns' inline
    assembly (Stage 0f5) so the head is built once and the
    probe/suite can call the exact code the turns call.
    Balanced is byte-identical to the old inline assembly:
    the language directive is "" for English homes and the
    verbosity directive is "" at balanced."""
    system = config.system_instruction
    # v5.41: non-English home directive ("" for English); it is
    # part of `system` before every budget measurement below.
    system += reply_language_directive()
    # Stage 0 features batch 5, item 11: reply verbosity —
    # the length instruction rides next to the language
    # directive. Balanced is "" so the prompt stays
    # byte-identical to before.
    try:
        from ..verbosity import (current_verbosity,
                                 reply_verbosity_directive)

        system += reply_verbosity_directive(current_verbosity())
    except Exception:  # noqa: BLE001 — verbosity never breaks a turn
        pass
    return system


def sampling_profile(name: str | None) -> dict[str, Any]:
    """The {temperature, max_tokens} dict for a profile name,
    read from config's B13 fields with the historical defaults
    as fallback. Unknown/None name = the pre-B13 fixed profile
    (0.3 / 512) — summary and other non-turn callers keep it."""
    def _f(attr, default):
        raw = getattr(config, attr, None)
        try:
            return float(raw) if raw not in (None, "") else default
        except (TypeError, ValueError):
            return default

    def _i(attr, default):
        raw = getattr(config, attr, None)
        try:
            return int(raw) if raw not in (None, "") else default
        except (TypeError, ValueError):
            return default

    # Stage 0 features batch 5, item 11: the turn profiles'
    # max_tokens scales with reply verbosity — concise quarters
    # the cap, detailed doubles it. The default profile (summary
    # and other non-turn callers) is never scaled.
    try:
        from ..verbosity import (current_verbosity,
                                 verbosity_max_tokens_factor)

        _factor = verbosity_max_tokens_factor(current_verbosity())
    except Exception:  # noqa: BLE001 — verbosity never breaks a turn
        _factor = 1.0

    if name == "quick":
        return {"temperature": _f("local_quick_temperature", 0.2),
                "max_tokens": max(
                    1, int(round(_i("local_quick_max_tokens", 256)
                                 * _factor)))}
    if name == "think":
        return {"temperature": _f("local_think_temperature", 0.55),
                "max_tokens": max(
                    1, int(round(_i("local_think_max_tokens", 1024)
                                 * _factor)))}
    return {"temperature": 0.3, "max_tokens": 512}


def _is_lite_tier_model() -> bool:
    """True when the effective builtin model is the Lite tier
    (Qwen3-1.7B) — the no-think discipline's trigger (B13)."""
    try:
        return str(local_assets.effective_model().get("tier") or "") == "lite"
    except Exception:  # noqa: BLE001
        return False


def _memory_block(user_text: str | None = None) -> str:
    """memory_store's context block for this turn (B4, v5.45):
    pass the utterance so facts rank by relevance. Falls back
    to the no-argument call when the store (or a test stub) does
    not accept the keyword — ranking is an enhancement, the
    block itself is not.

    Conversion Wave 8: the block is folded with the PF-28
    learning-feed fact lines + the sleeptime ranked file-store
    block ("" on a default install), then pre-flighted against
    the hard memory budget (context ledger, enforced, not
    advisory): over budget drops the supplements first; if the
    memory block itself is still over, it is cut on a line
    boundary with the drop disclosed (the same disclosure shape
    the builtin turn already uses for its own cap). The frozen
    DB is never touched by the sleeptime block."""
    try:
        block = memory_store.get_memory_context_prompt(utterance=user_text)
    except TypeError:
        block = memory_store.get_memory_context_prompt()
    try:
        from .. import w8_wiring as _w8  # Phase 3 V1b: moved to core/voice/

        supplements = _w8.prompt_supplements()
        if supplements:
            block = block + supplements if block else supplements.strip()
        report = _w8.preflight_prompt({"memory": block})
        if not report["ok"]:
            if supplements and supplements in block:
                block = block.replace(supplements, "", 1)
                report = _w8.preflight_prompt({"memory": block})
            if not report["ok"]:
                # Memory stream over its hard budget: cut on a
                # line boundary and disclose the drop.
                from ..brain.context import HARD_BUDGETS_TOKENS  # Phase 3 V1b: moved to core/voice/

                budget_chars = int(
                    HARD_BUDGETS_TOKENS["memory"] * 3.2)
                cut = block.rfind("\n", 0, budget_chars)
                block = block[:cut if cut > 0 else budget_chars]
                block += ("\n- … (further memories trimmed for "
                          "the memory budget)")
    except Exception:  # noqa: BLE001 — memory must never break a turn
        pass
    return block


def _tool_call_signature(name: str, args: Any) -> str:
    """A stable identity for one tool call (name + canonical
    args) — the no-progress breaker's comparison key (v5.44, B7)."""
    try:
        return name + "|" + json.dumps(args or {}, sort_keys=True,
                                       default=str)
    except (TypeError, ValueError):
        return name + "|" + repr(args)


def _tool_progress_report(done: list[str]) -> str:
    """The spoken/text progress report a turn ends with when the
    no-progress breaker fires (v5.44, B7): what WAS accomplished,
    then why it stopped — never a silent fallback."""
    if done:
        summary = "; ".join(done[-3:])
        return (f"Here's what I managed: {summary}. I stopped "
                "there because the same step kept repeating "
                "without making progress.")
    return ("I stopped there because the same step kept repeating "
            "without making progress — nothing was accomplished "
            "before it started looping.")


def _ambient_packet_text() -> str:
    """The NOSE ambient packet for session start (v5.44, N4),
    lazily imported (house style): missing/failing ambient
    degrades to "" — today's behaviour — never an error."""
    try:
        from ..brain import ambient  # Phase 3 V1b: moved to core/voice/

        return ambient.ambient_packet() or ""
    except Exception:  # noqa: BLE001 — ambient is a nicety
        return ""


def _log_brain_guard(message: str) -> None:
    """Append one line to data/activity.log (the file the tool
    registry and the cloud client log to). Never raises."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / "activity.log", "a", encoding="utf-8") as fh:
            fh.write(f"[BrainGuard] {message}\n")
    except Exception:  # noqa: BLE001
        pass

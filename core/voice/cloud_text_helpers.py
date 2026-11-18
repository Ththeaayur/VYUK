"""Helpers split out of core/voice/cloud_text_client.py (B38 part 1 of 2):
config seams, key/model resolution, transport seam (_post_json /
_get_json), schema conversion, cost-ledger helpers, key validation,
goal-reply parsing, and tool-exchange validation, plus the two small
helper classes (_DeliberationBudget, _GroqTurnError). The GroqSession
class stays in the facade until B39 splits it into its 3 mixins.
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
from .. import config_spec
from ..groq_action_journal import GroqActionJournal  # Phase 3 V1b: moved to core/voice/
from ..languages import reply_language_directive  # Phase 3 V1b: moved to core/voice/
from ..memory.memory import memory_store  # Phase 3 V1b: moved to core/voice/
from ..funnel.tools_registry import tools  # Phase 3 V1b: moved to core/voice/; Phase 4 S1b INTENTIONAL: registry split to funnel


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


GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

MAX_TOOL_ROUNDS = 8        # v5.44 (B7): the cloud default —
                           # core/config.py's cloud_tool_rounds
                           # field (CLOUD_TOOL_ROUNDS) is the live
                           # value; this constant is the fallback
                           # when the field is absent.
HISTORY_WINDOW = 12        # prior messages sent with each request
                           # (parity with local_client's turn window;
                           # here it counts MESSAGES — the hosted
                           # context is generous, the discipline is
                           # the local sessions')
HISTORY_CAP = 24           # messages kept on the instance
CHAT_TIMEOUT_S = 120       # parity with OLLAMA_CHAT_TIMEOUT_S
MODELS_TIMEOUT_S = 30
DEFAULT_TEMPERATURE = 0.6

# --- Status lines (asserted verbatim by the v5.15 suite) ------------ #

NO_KEY_STATUS = (
    "ERROR: No Groq API key yet — add your Groq key in Settings, "
    "then reconnect to use the Groq brain."
)
NETWORK_ERROR_STATUS = (
    "ERROR: Couldn't reach Groq — check the internet connection "
    "and try again."
)
LOOP_FALLBACK = "Sorry — I got stuck in a loop there."


def _log_line(message: str):
    """Append one line to data/activity.log, prefixed [Groq] (the
    same file live_client writes [Live] lines to). Never raises."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / "activity.log", "a", encoding="utf-8") as fh:
            fh.write(f"[Groq] {message}\n")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------- #
# Config seams
# ---------------------------------------------------------------------- #

def _groq_api_key() -> str:
    """The user's Groq key: the spec value (config.groq_api_key /
    env GROQ_API_KEY)."""
    key = config_spec.env("GROQ_API_KEY")
    return (key or "").strip()


def _groq_model() -> str:
    model = config_spec.env("GROQ_MODEL")
    return (model or "").strip() or DEFAULT_GROQ_MODEL


# --- Groq vision (v5.45, EYES E5a) --------------------------------------
# The TEXT model above cannot see. Frame turns (send_image_frame)
# go to a vision-capable Groq model instead: Llama 4 Scout, the
# multimodal model in Groq's current lineup (Groq's own model
# page: meta-llama/llama-4-scout-17b-16e-instruct — native image
# input, OpenAI-style image_url content parts). No vision model
# id is referenced anywhere else in the tree or docs, so this
# default stands on the Groq docs; the id is a config field
# (config.groq_vision_model / env GROQ_VISION_MODEL) precisely so
# a lineup change is a setting, not a code change.
DEFAULT_GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

VISION_FALLBACK_NOTE = (
    "Vision was unavailable for this frame, so it was read with "
    "OCR text instead — mention briefly that vision fell back "
    "to OCR."
)
"""Prepended to the OCR prompt when a vision turn fails: the
fallback must be SAID, never silent (the E5a honesty rule)."""


def _groq_vision_model() -> str:
    model = config_spec.env("GROQ_VISION_MODEL")
    return (model or "").strip() or DEFAULT_GROQ_VISION_MODEL


def _groq_vision_enabled() -> bool:
    """Escape hatch: config.groq_vision / env GROQ_VISION set to
    0/false/no/off restores the pre-E5a OCR-only frame path."""
    raw = config_spec.env("GROQ_VISION")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def groq_vision_payload(image_bytes: bytes, mime_type: str,
                        question: str,
                        history: list[dict[str, Any]] | None = None,
                        system_prompt: str = "") -> dict[str, Any]:
    """The OpenAI-style vision request for one frame (pure — the
    suite pins its shape): system prompt (when given), the recent
    TEXT history (string-content messages only — an old image
    part must never ride along), then ONE user message whose
    content is a text part (the user's question/context) plus an
    image_url part carrying the frame as a base64 data URI. No
    tools are offered: the vision turn answers the frame; tool
    work continues on the text model."""
    import base64

    mime = (mime_type or "image/jpeg").strip() or "image/jpeg"
    data_uri = (f"data:{mime};base64,"
                + base64.b64encode(image_bytes).decode("ascii"))
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for msg in history or []:
        if isinstance(msg.get("content"), str):
            messages.append({"role": msg.get("role", "user"),
                             "content": msg["content"]})
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ],
    })
    return {
        "model": _groq_vision_model(),
        "messages": messages,
        "temperature": _groq_temperature(),
        "stream": False,
    }


def _groq_tool_router_enabled() -> bool:
    """B8 (v5.45): the toolbox router for Groq, default ON.
    Escape hatch to the old full-list behaviour: config field
    groq_tool_router or env GROQ_TOOL_ROUTER set to 0/false."""
    raw = config_spec.env("GROQ_TOOL_ROUTER")
    if isinstance(raw, bool):
        return raw
    if str(raw).strip().lower() in ("0", "false", "no", "off"):
        return False
    return True


def groq_offered_declarations() -> list[dict[str, Any]]:
    """The declarations a Groq request offers (B8): with the
    router on, the resident CORE set + use_toolbox in the
    toolbox's router order (router_declarations is the
    Gemini-shaped source; openai_tools adapts the shape);
    off, the full registry exactly as before."""
    if _groq_tool_router_enabled():
        try:
            from ..funnel import toolbox as _tb  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/

            return _tb.router_declarations(tools)
        except Exception as e:  # noqa: BLE001 — fall back to full
            _log_line(f"Router unavailable ({e}); offering full list.")
    return tools.get_declarations()


def _max_tool_rounds() -> int:
    """Tool rounds allowed in one Groq turn (v5.44, B7):
    config.cloud_tool_rounds (default 8, env CLOUD_TOOL_ROUNDS),
    the module constant as fallback. Never non-positive."""
    try:
        value = int(getattr(config, "cloud_tool_rounds",
                            MAX_TOOL_ROUNDS))
    except (TypeError, ValueError):
        value = MAX_TOOL_ROUNDS
    return value if value > 0 else MAX_TOOL_ROUNDS


class _DeliberationBudget:
    """Phase 2 (audit 6 B09): the deliberation-repair budget,
    INDEPENDENT of the tool-round budget. A deliberation
    retry consumes one unit here and never a tool round, so a
    repair loop cannot eat the turn's tool budget and tool
    rounds cannot starve repairs. Limit 1 (the single
    corrective retry the v5.52 guard already allowed)."""

    LIMIT = 1

    def __init__(self) -> None:
        self.used = 0

    def try_consume(self) -> bool:
        """True (and charged) when a repair attempt remains."""
        if self.used >= self.LIMIT:
            return False
        self.used += 1
        return True

    @property
    def remaining(self) -> int:
        return self.LIMIT - self.used


def _ambient_packet_text() -> str:
    """The NOSE ambient packet for session start (v5.44, N4),
    lazily imported (house style): missing/failing ambient
    degrades to "" — today's behaviour — never an error."""
    try:
        from ..brain import ambient  # Phase 3 V1b: moved to core/voice/

        return ambient.ambient_packet() or ""
    except Exception:  # noqa: BLE001 — ambient is a nicety
        return ""


def _call_signature(call: dict[str, Any]) -> str:
    """A stable identity for one OpenAI tool_call (name +
    canonical arguments) — the no-progress breaker's key (B7).
    Unparseable arguments signature on their raw text."""
    fn = call.get("function") or {}
    name = fn.get("name", "")
    raw = fn.get("arguments")
    args: Any = raw
    if isinstance(raw, str):
        try:
            args = json.loads(raw) if raw.strip() else {}
        except ValueError:
            args = raw
    try:
        return name + "|" + json.dumps(args or {}, sort_keys=True,
                                       default=str)
    except (TypeError, ValueError):
        return name + "|" + repr(args)


# MN-2.8 (v5.66): cost-ledger hooks. The ledger is stdlib-only
# and local-only; every call here is guarded so a ledger failure
# never breaks a turn. Caps are opt-in via VYUK_COST_* env vars
# (CostLedger.from_env); with no caps set the preflight always
# allows and behaviour is unchanged.
_COST_LEDGER: Any = None


def _cost_ledger() -> Any:
    """The process-wide env-configured ledger (lazy: importing
    this module must not touch the ledger file)."""
    global _COST_LEDGER
    if _COST_LEDGER is None:
        _COST_LEDGER = _cost_ledger_mod.CostLedger.from_env()
    return _COST_LEDGER


def _cost_prompt_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def _cost_preflight_or_raise(model: str, prompt_text: str) -> None:
    """Fail-closed cap gate for a discrete cloud call: at/over
    a configured cap, cloud spend proceeds ONLY if the
    ledger's confirm callback explicitly approves it (no
    callback = refusal). Warn-level projections pass through;
    the caller surfaces the warning via the activity log."""
    try:
        ledger = _cost_ledger()
        prompt_tokens = _cost_ledger_mod.estimate_tokens(prompt_text)
        verdict = ledger.preflight("cloud_text", model,
                                   prompt_tokens=prompt_tokens,
                                   completion_tokens=0)
        if verdict["warning"]:
            _log_line(f"Cost ledger: {verdict['warning']}")
        if not verdict["allowed"]:
            if verdict["requires_confirm"] and ledger.confirm_cloud_spend(
                    verdict["warning"] or ""):
                return
            raise _GroqTurnError(
                "Cost cap reached — cloud spend refused until"
                " explicitly confirmed (MN-2.8 ledger).")
    except _GroqTurnError:
        raise
    except Exception:  # noqa: BLE001 — ledger failure never breaks a turn
        pass


def _cost_record(parsed: Any, model: str, prompt_text: str) -> None:
    """One ledger row per completed Groq call: exact when the
    reply carried a usage block, estimated otherwise."""
    try:
        if not isinstance(parsed, dict):
            return
        usage = parsed.get("usage")
        completion_text = ""
        choices = parsed.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            if isinstance(msg.get("content"), str):
                completion_text = msg["content"]
        _cost_ledger().record_from_usage(
            "cloud_text", model, usage, prompt_text, completion_text)
    except Exception:  # noqa: BLE001 — ledger failure never breaks a turn
        pass


def _groq_temperature() -> float:
    raw = config_spec.env("GROQ_TEMPERATURE")
    try:
        return float(raw) if raw not in (None, "") else DEFAULT_TEMPERATURE
    except (TypeError, ValueError):
        return DEFAULT_TEMPERATURE


# ---------------------------------------------------------------------- #
# Transport seam — the ONLY urllib call sites in this module.
# _post_json(url, key, payload) -> (status, parsed) and its sibling
# _get_json(url, key) -> (status, parsed). HTTP error statuses are
# RETURNED (status + parsed body) so callers classify them; transport
# failures (URLError / OSError) RAISE so callers can tell "Groq said
# no" from "Groq couldn't be reached". A body that isn't valid JSON
# parses to None. Tests fake these two functions and nothing else.
# ---------------------------------------------------------------------- #

def _build_opener():
    """System-proxy opener — core.voice.local_assets._build_opener's
    approach in miniature (the v3.3 download lesson): honour
    urllib.request.getproxies() so a campus/system proxy is used,
    never log proxy values (they can carry credentials), and fall
    back to a plain opener when the proxy config is unusable."""
    try:
        proxies = urllib.request.getproxies() or {}
        clean = {
            scheme: value
            for scheme, value in proxies.items()
            if scheme in ("http", "https") and isinstance(value, str) and value
        }
        if clean:
            return urllib.request.build_opener(
                urllib.request.ProxyHandler(clean))
    except Exception:  # noqa: BLE001 — proxy trouble must not block the brain
        pass
    return urllib.request.build_opener()


def _request_json(method: str, url: str, key: str,
                  payload: dict[str, Any] | None,
                  timeout: float) -> tuple[int, Any]:
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)
    opener = _build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = resp.getcode()
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # An HTTP error status is an ANSWER, not a transport failure:
        # return it with its body so the caller classifies it.
        status = e.code
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            raw = ""
    try:
        parsed = json.loads(raw) if raw.strip() else None
    except ValueError:
        parsed = None
    return status, parsed if isinstance(parsed, (dict, list)) else None


def _post_json(url: str, key: str,
               payload: dict[str, Any]) -> tuple[int, Any]:
    """POSTs `payload` as JSON with the Bearer key. See the seam
    contract above."""
    return _request_json("POST", url, key, payload, CHAT_TIMEOUT_S)


def _get_json(url: str, key: str) -> tuple[int, Any]:
    """GETs with the Bearer key. See the seam contract above."""
    return _request_json("GET", url, key, None, MODELS_TIMEOUT_S)


def _error_detail(parsed: Any) -> str:
    """Groq's own words for a failure, when the body carries them."""
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
        if parsed.get("message"):
            return str(parsed["message"])
    return ""


def _classify_http_error(status: int, parsed: Any) -> str:
    """The exact ERROR status line for an HTTP-level failure,
    Groq's detail appended when there is any, capped at 300 chars
    overall (parity with live_client's ERROR cap)."""
    detail = _error_detail(parsed)
    if status == 401:
        line = ("ERROR: Groq rejected that API key (401) — it looks "
                "invalid or revoked. Check the key in Settings.")
    elif status == 403:
        line = ("ERROR: Groq refused that request (403) — the key "
                "may not have access to this model.")
    elif status == 429:
        line = ("ERROR: Groq rate limit reached (429) — too many "
                "requests right now. Give it a moment and try again.")
    elif status == 200:
        line = ("ERROR: Groq sent back a reply I couldn't read — "
                "nothing was run; try again.")
    else:
        line = f"ERROR: Groq answered with HTTP {status}."
    if detail and status != 200:
        line += f" Groq said: {detail}"
    return line[:300]


class _GroqTurnError(Exception):
    """A brain turn failed at the HTTP layer; carries the exact
    status line the session should emit for it."""

    def __init__(self, status_line: str):
        super().__init__(status_line)
        self.status_line = status_line


# ---------------------------------------------------------------------- #
# Declaration translation: registry (Gemini-shaped) → OpenAI functions
# ---------------------------------------------------------------------- #

_TYPE_MAP = {
    "OBJECT": "object", "STRING": "string", "NUMBER": "number",
    "INTEGER": "integer", "BOOLEAN": "boolean", "ARRAY": "array",
}


def _convert_schema(node: Any) -> Any:
    """Recursively rewrites Gemini-style schema types to JSON-Schema
    case anywhere a "type" key appears — the same conversion
    core.local_client._convert_schema performs for Ollama. Every
    other field (description / enum / required / items / nested
    properties) passes through untouched."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "type" and isinstance(value, str):
                out[key] = _TYPE_MAP.get(value.upper(), value.lower())
            else:
                out[key] = _convert_schema(value)
        return out
    if isinstance(node, list):
        return [_convert_schema(item) for item in node]
    return node


def openai_tools(declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translates tools.get_declarations() into OpenAI
    function-calling format — a pure function.

    Field mapping, exactly: each declaration's `name` and
    `description` carry over verbatim; `parameters` is the
    declaration's schema after _convert_schema (Gemini's UPPERCASE
    schema types become JSON-Schema lowercase), with `type` /
    `properties` defaulted to an empty object schema when the
    declaration omits them; the wrapper adds {"type": "function",
    "function": {...}}. NOTHING else is dropped or renamed — enum,
    required, nested items and property descriptions all survive.
    """
    converted = []
    for decl in declarations:
        params = _convert_schema(
            decl.get("parameters") or {"type": "object", "properties": {}})
        if isinstance(params, dict):
            params.setdefault("type", "object")
            params.setdefault("properties", {})
        converted.append({
            "type": "function",
            "function": {
                "name": decl["name"],
                "description": decl.get("description", ""),
                "parameters": params,
            },
        })
    return converted


def _result_text(result: Any) -> str:
    """tools.execute() result → the string a tool message carries.
    core.local_client._result_to_text, imported lazily (the lite
    session's pattern — this module's own import stays cheap)."""
    from .local_client import _result_to_text

    return _result_to_text(result)


def _state_guard_for(user_text: str | None):
    """The brain guard's class->tool resolution, shared with the
    local brain (v5.43, B11): ONE map for every text brain, so a
    class can never widen on one engine and not the others.
    core.local_client._state_guard_for, imported lazily like
    _result_text above."""
    from .local_client import _state_guard_for as _shared

    return _shared(user_text)


# ---------------------------------------------------------------------- #
# Key validation (mirrors live_client.validate_api_key's contract)
# ---------------------------------------------------------------------- #

def validate_groq_key(key: str,
                      model: str | None = None) -> tuple[bool, str, str]:
    """Cheapest real validation of a Groq key: one GET /models.

    Returns (ok, kind, message) with kind one of
    "ok" | "auth" | "network" | "http" — the same contract shape as
    live_client.validate_api_key. The ok message is truthful about
    the 70B either way: it names the model as available only when
    its id is actually in the list this key can see. Sync — call it
    off the UI thread."""
    key = (key or "").strip()
    if not key:
        return (False, "auth", "No API key to test.")
    model = (model or "").strip() or _groq_model()
    try:
        status, parsed = _get_json(GROQ_MODELS_URL, key)
    except (urllib.error.URLError, OSError) as e:
        return (False, "network",
                f"Couldn't reach Groq to test the key: {str(e)[:200]}")
    if status == 200 and isinstance(parsed, dict):
        ids = [m.get("id") for m in (parsed.get("data") or [])
               if isinstance(m, dict)]
        if model in ids:
            return (True, "ok",
                    f"Groq key works — {model} is available on this key.")
        return (True, "ok",
                f"Groq key works — but {model} isn't in the list this "
                f"key can use ({len(ids)} models listed), so pick "
                f"another Groq model in Settings.")
    if status in (401, 403):
        return (False, "auth",
                f"Groq rejected this key ({status}) — it looks "
                f"invalid or revoked.")
    detail = _error_detail(parsed)
    message = f"Groq answered the key test with HTTP {status}."
    if detail:
        message += f" Groq said: {detail}"
    return (False, "http", message[:300])


def _parse_groq_goal_reply(content: str) -> dict[str, Any] | None:
    """Parse ONE run_goal action from a Groq content reply
    (v5.49, programme item 29).

    JSON-in-text tolerant (a raw_decode scan over every "{" —
    prose and code fences around the object are fine): the first
    decisive object wins — {"done": summary} ends the goal,
    {"tool"/"name": ..., "arguments"/"args": {...}} (optionally
    with a free-text "expect") is the next action. An object that
    NAMES a tool is decisive: an unknown name returns None (the
    planner then stops honestly). Plain speech / unparseable
    replies return None."""
    if not content:
        return None
    try:
        valid_names: set[str] | None = {
            decl["name"] for decl in tools.get_declarations()}
    except Exception:  # noqa: BLE001 — the planner validates too
        valid_names = None

    def _from_obj(obj: Any) -> tuple[dict[str, Any] | None, bool]:
        if not isinstance(obj, dict):
            return None, False
        if obj.get("done"):
            return {"done": str(obj["done"])}, True
        name = obj.get("tool") or obj.get("name")
        if not isinstance(name, str) or not name:
            return None, False
        if valid_names is not None and name not in valid_names:
            return None, True  # unknown tool → None, per item 29
        args = obj.get("arguments", obj.get("args", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                return None, True
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None, True
        action: dict[str, Any] = {"tool": name, "args": args}
        expect = obj.get("expect")
        if isinstance(expect, str) and expect.strip():
            action["expect"] = expect
        return action, True

    decoder = json.JSONDecoder()
    for start, ch in enumerate(content):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(content[start:])
        except ValueError:
            continue
        parsed, decisive = _from_obj(obj)
        if decisive:
            return parsed
    return None


# ---------------------------------------------------------------------- #
# The session
# ---------------------------------------------------------------------- #

def _validate_tool_exchanges(msgs: list[dict[str, Any]]
                             ) -> list[dict[str, Any]]:
    """Turn-truthfulness (6th-B04): drop partial tool exchanges whole.

    Walks messages in order. An assistant message's tool calls are
    kept only when the immediately following tool messages cover
    EXACTLY its tool_call_ids (multiset equality — every call gets
    its response, no stray extras). A leading orphan tool message,
    a call group missing any response, or a group with stray extra
    responses is dropped whole — never half-retained — and
    validation continues after it. Plain user/assistant messages
    pass through untouched; a complete exchange passes through
    byte-identical.

    Distinct from the fifth audit's retracted trimming claim:
    that retraction stands for _cap_history orphans as a reported
    bug. This is the completeness validation both trimmers now
    enforce, so no outgoing request can carry a tool call without
    all of its required responses.
    """
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(msgs):
        msg = msgs[i]
        role = msg.get("role")
        if role == "tool":
            # Orphan: its call is gone; drop, never keep alone.
            i += 1
            continue
        calls = msg.get("tool_calls") if role == "assistant" else None
        if not calls:
            out.append(msg)
            i += 1
            continue
        ids = [c.get("id", "") for c in calls]
        following: list[dict[str, Any]] = []
        j = i + 1
        while j < len(msgs) and msgs[j].get("role") == "tool":
            following.append(msgs[j])
            j += 1
        fids = [m.get("tool_call_id", "") for m in following]
        if sorted(fids) == sorted(ids):
            out.append(msg)
            out.extend(following)
        # Else: drop the assistant message AND its partial/stray
        # tool messages — the whole exchange, never half of it.
        i = j
    return out

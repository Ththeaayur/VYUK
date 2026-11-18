from ..config import (config)
from ..funnel.toolbox import (CORE_TOOL_PRIORITY)
from ..funnel.tools_registry import (tools)
from .local_client_guards import (MAX_TOOL_REPAIRS)
from typing import (Any)
import math
import re


class _RepairBudget:
    """Counts the self-correction attempts of one builtin brain turn.

    A turn may repair a malformed tool call at most MAX_TOOL_REPAIRS
    times in total — parse failures and schema-validation rejections
    draw from the SAME budget, so a model that keeps fumbling the
    shape can never loop: when try_consume() returns False the turn
    ends with TOOL_SHAPE_FALLBACK and nothing further is attempted.
    Deliberately a tiny standalone class so the cap is unit-testable
    without a loaded model."""

    def __init__(self, limit: int = MAX_TOOL_REPAIRS):
        self.limit = limit
        self.used = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def try_consume(self) -> bool:
        """Spend one repair attempt; False when the budget is spent."""
        if self.exhausted:
            return False
        self.used += 1
        return True


class _DeliberationBudget:
    """Phase 2 (audit 6 B09): the deliberation-repair budget,
    INDEPENDENT of the tool-round budget (and of the
    _RepairBudget above). A deliberation retry consumes one
    unit here and never a tool round, so a repair loop cannot
    eat the turn's tool budget and tool rounds cannot starve
    repairs. Limit 1 (the single corrective retry the v5.52
    guard already allowed). The builtin turn ALSO takes the
    audit's second option — an immediate fallback on the
    final round — so a retry that cannot run is never
    injected."""

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


_ACTIVE_TOKENIZER_MODEL: Any = None


def _set_active_tokenizer(model: Any) -> None:
    """Registers `model` as the active tokenizer source (B3).
    Called by _ensure_llama on every successful load."""
    global _ACTIVE_TOKENIZER_MODEL
    _ACTIVE_TOKENIZER_MODEL = model


def _clear_active_tokenizer(model: Any = None) -> None:
    """Drops the active registration — unconditionally, or only
    when it still points at `model` (the idle unload's case)."""
    global _ACTIVE_TOKENIZER_MODEL
    if model is None or _ACTIVE_TOKENIZER_MODEL is model:
        _ACTIVE_TOKENIZER_MODEL = None


def _active_tokenizer() -> Any:
    """The registered loaded-model handle, or None pre-load."""
    return _ACTIVE_TOKENIZER_MODEL


def _est_tokens(text: str, model: Any = None) -> int:
    """A text's token count for the local model.

    v5.43 (B3): when a builtin model handle is available — passed
    explicitly as `model`, or the active handle _ensure_llama
    registered on load — the count is the model's OWN tokenizer:
    len(model.tokenize(text.encode())), one tokenize call per
    estimate. Qwen's BPE runs far denser than prose heuristics on
    JSON, and the budget assembly below is mostly JSON-shaped
    protocol text, so the heuristic silently mis-fit real prompts.

    The fallback is the historical heuristic, ceil(len/3.2)
    (Qwen's BPE averages ~3.5–4 chars per token on English prose,
    so 3.2 is deliberately conservative). It serves every pre-load
    estimate and ANY tokenizer trouble — a handle without
    tokenize, a tokenize that raises or returns something
    uncountable — because an estimate must never raise into a
    turn. Empty text is 0 and never touches the tokenizer."""
    if not text:
        return 0
    handle = model if model is not None else _ACTIVE_TOKENIZER_MODEL
    if handle is not None:
        tokenize = getattr(handle, "tokenize", None)
        if callable(tokenize):
            try:
                tokens = tokenize(text.encode("utf-8"))
                if tokens is not None:
                    return len(tokens)
            except Exception:  # noqa: BLE001 — fall through to
                pass  # the heuristic; never raise into a turn
    return math.ceil(len(text) / 3.2)


def _tool_profile_name() -> str:
    """The configured tool profile (v5.45, HANDS H13 —
    core/funnel/tool_profiles.py): "assistant" unless the user picked
    otherwise. Any failure resolving it degrades to "assistant",
    i.e. today's behaviour — the profile is a shaping knob,
    never a new failure mode for the local brain."""
    try:
        from ..funnel.tool_profiles import current_profile_name  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_profiles moved to core/funnel/

        return current_profile_name(config)
    except Exception:  # noqa: BLE001
        return "assistant"


def _resident_priority_names() -> tuple[str, ...]:
    """The resident set the local brain's renders lead with
    (v5.45, HANDS H13): the configured profile's resident names
    — CORE_TOOL_PRIORITY exactly for "assistant" (the default,
    so default renders stay byte-identical), the desktop set
    for "desktop", and the whole registry for "full" (whose
    profile helper returns None = no subset). Both resident
    renders — _render_tool_protocol (builtin) and
    _to_ollama_tools (Ollama) — claim their full-size places
    for these names first."""
    try:
        from ..funnel.tool_profiles import resident_names_for_profile  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_profiles moved to core/funnel/

        names = resident_names_for_profile(_tool_profile_name())
        if names is not None:
            return tuple(names)
        return tuple(d["name"] for d in tools.get_declarations())
    except Exception:  # noqa: BLE001
        return CORE_TOOL_PRIORITY


def _format_tool_line(decl: dict[str, Any], desc_chars: int = 90) -> str:
    """One tool's full protocol line — name, parameter names/types,
    ≤90 chars of description; the exact format the protocol has
    always emitted. desc_chars shrinks ONLY the description prose
    (the budgeted renderer's compact form, v5.21): the parameter
    spec — the part the model must get right — is never clipped."""
    params = decl.get("parameters") or {}
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    rendered = []
    for pname, pschema in props.items():
        ptype = str((pschema or {}).get("type", "any")).lower()
        suffix = ", required" if pname in required else ""
        rendered.append(f"{pname}: {ptype}{suffix}")
    desc = " ".join((decl.get("description") or "").split())[:desc_chars]
    return f"- {decl['name']}({'; '.join(rendered)}): {desc}"


def _tool_line(name: str) -> str | None:
    """The full protocol line for ONE tool by name, or None when no
    such tool is registered. The builtin repair path feeds this back
    on a schema-validation error so the model retries with the exact
    argument shape in front of it (v5.19)."""
    for decl in tools.get_declarations():
        if decl.get("name") == name:
            return _format_tool_line(decl)
    return None


_INDEX_LEAD = (
    "More tools exist. To use one of these, reply with its name in "
    "the same JSON format, with your best-guess arguments — if the "
    "arguments are wrong, the reply will show you that tool's exact "
    "schema so you can retry:"
)


_INDEX_LINE_WIDTH = 110


def _index_lines(names: list[str]) -> list[str]:
    """Chunks tool names into "Also available: a, b, c" lines of at
    most ~110 chars — the compact index form of the protocol."""
    out: list[str] = []
    chunk: list[str] = []
    width = len("Also available: ")
    for name in names:
        add = len(name) + (2 if chunk else 0)
        if chunk and width + add > _INDEX_LINE_WIDTH:
            out.append("Also available: " + ", ".join(chunk))
            chunk, width = [], len("Also available: ")
            add = len(name)
        chunk.append(name)
        width += add
    if chunk:
        out.append("Also available: " + ", ".join(chunk))
    return out


_QUERY_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for",
    "me", "my", "i", "you", "it", "is", "are", "can", "could",
    "please", "what", "whats", "that", "this", "with", "from",
    "your", "yours", "her", "she", "he", "we", "they", "them",
    "do", "does", "did", "will", "would", "should", "about",
    "into", "over", "under", "than", "then", "now", "just",
})


def _query_tokens(text: str | None) -> set[str]:
    """Content tokens of a sentence, for query promotion below."""
    if not text:
        return set()
    return {t for t in re.findall(r"[a-z0-9]+", text.lower())
            if len(t) >= 3 and t not in _QUERY_STOPWORDS}

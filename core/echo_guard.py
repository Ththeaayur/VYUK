"""
Echo guard for VYUK (v5.52, item 7) — deterministic, brain-agnostic.

The field failure: asked to "find flaws in every tool and
write it on Notepad", the model called type_text with the
payload "(find flaws in every tool)" — the instruction itself,
echoed as content, instead of performing any part of the task.
The editor dutifully received the request, not a report.

This module is the comparison half of the guard; the block
itself sits in ToolRegistry.execute() (core/funnel/tools_registry.py)
among the always-refuse checks, so EVERY brain and dispatch
path (native calls, the JSON-block protocol, use_toolbox
relays, planner runs) passes it.

Rule: for a text-injection action (type_text, uia_type_text,
paste_text, browser_type — the tools whose payload is
model-authored text destined to become keystrokes), compare
the payload with the current user instruction
(core/brain/turn_context), normalised (case / punctuation /
whitespace-insensitive). The payload is an ECHO when:
  - the normalised payload is CONTAINED in the normalised
    instruction, or
  - the payload's token overlap with the instruction is
    >= 0.85 of the payload's (unique) tokens,
and the payload carries at least 3 tokens (shorter payloads
cannot be told apart from legitimate short content).

Carve-outs — a false block on real dictation is worse than
the bug, so these pass even a verbatim echo:
  - dictation is active (turn_context flag, mirrored by
    turn_machine.DictationMode while a session is active);
  - the instruction EXPLICITLY asks for the quoted words:
    it carries a quoted span ("type exactly: …", 'write down
    my words: "…"') and the payload is (near-)exactly that
    span — the user named the content, so typing it IS the
    task, not an echo of the imperative.

Pure functions over strings; nothing here raises.
"""

import re

#: Text-injection tools -> the argument carrying the payload.
TEXT_PAYLOAD_ARGS: dict[str, str] = {
    "type_text": "text",
    "uia_type_text": "text",
    "paste_text": "text",
    "browser_type": "text",
}

#: Overlap at/above this fraction of the payload's tokens,
#: the payload counts as a near-verbatim echo.
ECHO_OVERLAP_THRESHOLD = 0.85

#: Shorter payloads are never judged — too little signal.
MIN_PAYLOAD_TOKENS = 3

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_QUOTED_RE = re.compile(r"[\"“]([^\"”]{1,})[\"”]|[']([^']{1,})[']")
_EXACTLY_RE = re.compile(
    r"\b(type|write|enter|put|note|take down|write down)\b"
    r"[^.]{0,40}?\bexactly\b", re.IGNORECASE)
_MY_WORDS_RE = re.compile(
    r"\b(my words|dictation|dictate this)\b", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _normalised(text: str) -> str:
    return " ".join(_tokens(text))


def payload_overlap(payload: str, instruction: str) -> float:
    """|payload tokens ∩ instruction tokens| / |payload tokens|
    over unique tokens — the echo ratio the threshold judges."""
    p = set(_tokens(payload))
    if not p:
        return 0.0
    i = set(_tokens(instruction))
    return len(p & i) / len(p)


def _quoted_span(instruction: str) -> str | None:
    """The instruction's explicitly quoted content, if any."""
    match = _QUOTED_RE.search(instruction or "")
    if match:
        span = match.group(1) or match.group(2) or ""
        return span.strip() or None
    return None


def _explicitly_dictated(payload: str, instruction: str) -> bool:
    """True when the user NAMED this payload as the content to
    type: a quoted span the payload (near-)exactly reproduces,
    or an explicit 'type exactly / my words / dictation'
    framing around a payload drawn from the instruction."""
    span = _quoted_span(instruction)
    if span:
        span_norm = _normalised(span)
        payload_norm = _normalised(payload)
        if span_norm and (payload_norm == span_norm
                          or payload_overlap(payload, span) >= 0.85
                          and payload_overlap(span, payload) >= 0.85):
            return True
    if _EXACTLY_RE.search(instruction or "") \
            or _MY_WORDS_RE.search(instruction or ""):
        # Framing says "type THESE words" — the payload being
        # drawn verbatim from the instruction is the request
        # being obeyed, not the request being echoed.
        return payload_overlap(payload, instruction) >= 0.85
    return False


def is_instruction_echo(payload: str, instruction: str) -> bool:
    """The guard's verdict for one payload under one
    instruction. False whenever there is too little to judge
    (no instruction, a tiny payload) or a carve-out applies."""
    try:
        if not payload or not instruction:
            return False
        payload_tokens = _tokens(payload)
        if len(payload_tokens) < MIN_PAYLOAD_TOKENS:
            return False
        if _explicitly_dictated(payload, instruction):
            return False
        payload_norm = _normalised(payload)
        instruction_norm = _normalised(instruction)
        if payload_norm and payload_norm in instruction_norm:
            return True
        return payload_overlap(payload, instruction) \
            >= ECHO_OVERLAP_THRESHOLD
    except Exception:  # noqa: BLE001 — a broken guard blocks nothing
        return False

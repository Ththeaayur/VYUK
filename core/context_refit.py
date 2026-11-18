"""Per-round context re-fit (sixth audit B07, Phase 2).

The local builtin turn budgets its context once, at assembly:
persona + memories + protocol + history must fit the window
minus the reply reserve and a working allowance. But tool
results and repair prompts then accumulate round after round
with no further fit check, so a long tool result plus several
repair rounds can overflow the window the assembly promised
to respect. This module re-fits the turn's messages before
EVERY model call, against the same budget.

Rules (all pinned by tests):
- The system message (messages[0]) is never dropped and never
  truncated — persona + protocol are the turn's spine.
- The user's original instruction (the first user message)
  is never dropped, and neither is the latest user message
  (the current instruction / latest feedback).
- Tool-result messages are atomic: a "Tool results:" message
  is dropped whole or truncated whole (with a disclosure
  suffix), never split — the local analogue of the B04 group
  rule (whole tool groups, never half-retained). The most
  recent "Tool results:" message is dropped last: it carries
  the freshest verified outcomes.
- Tool-result contents are bounded per message (default 2000
  chars) before any drop decision, with disclosure.
- Drops go oldest-first among the droppable remainder.
- If even the irreducible core (system + latest user +
  latest results) exceeds the budget, the report says so
  honestly (over_budget_remaining=True) instead of silently
  truncating the spine — the caller logs it; refusing the
  turn would be worse, and the initial assembly makes this
  unreachable in practice.

Cloud tool-role exchanges (assistant tool_calls + following
tool messages) have their own group rule, enforced by
cloud_text_client._validate_tool_exchanges: an assistant
message and its tool messages are ONE group. refit_groups()
applies the same atomic discipline to such exchanges as a
pure function (tested; the Groq turn keeps its existing
window cap + validator, so this function is available for a
later Groq adoption, not silently wired into it).

Stdlib-only, no core imports. The caller supplies the token
estimator (local turns pass _est_tokens with the loaded
model); estimates are the caller's, the discipline is here.
"""

from __future__ import annotations

from typing import Any, Callable

DEFAULT_PER_RESULT_CHARS = 2000
_TRUNC_SUFFIX = "\n… [tool result truncated to fit context]"


def _tokens(messages: list[dict], estimate: Callable[[str], int]) -> int:
    return sum(estimate(str(m.get("content") or "")) for m in messages)


def _is_results_message(message: dict) -> bool:
    return (message.get("role") == "user"
            and str(message.get("content") or "")
            .startswith("Tool results:"))


def _truncate_results_message(message: dict, cap_chars: int) -> dict:
    content = str(message.get("content") or "")
    if len(content) <= cap_chars:
        return message
    cut = content.rfind("\n", 0, cap_chars)
    trimmed = content[:cut if cut > 0 else cap_chars] + _TRUNC_SUFFIX
    return {**message, "content": trimmed}


def refit_messages(messages: list[dict], *, budget_tokens: int,
                   estimate: Callable[[str], int],
                   per_result_chars: int = DEFAULT_PER_RESULT_CHARS,
                   ) -> tuple[list[dict], dict]:
    """Re-fit a local turn's messages to budget_tokens. Returns
    (messages, report). The input list is never mutated."""
    if not messages:
        return [], {"dropped": 0, "truncated": 0, "tokens_before": 0,
                    "tokens_after": 0, "over_budget_remaining": False}
    out = [_truncate_results_message(m, per_result_chars)
           if _is_results_message(m) else dict(m) for m in messages]
    truncated = sum(1 for a, b in zip(messages, out)
                    if a.get("content") != b.get("content"))
    tokens_before = _tokens(messages, estimate)
    dropped = 0
    # Droppable, oldest-first: everything except the system
    # message (index 0), the FIRST user message (the original
    # instruction), the latest user message, and the most
    # recent "Tool results:" message.
    first_user_idx = next(
        (i for i, m in enumerate(out) if m.get("role") == "user"),
        None)
    latest_user_idx = max(
        (i for i, m in enumerate(out) if m.get("role") == "user"),
        default=None)
    latest_results_idx = max(
        (i for i, m in enumerate(out) if _is_results_message(m)),
        default=None)
    while _tokens(out, estimate) > budget_tokens and len(out) > 1:
        victim = None
        for i in range(1, len(out)):
            if i in (first_user_idx, latest_user_idx,
                     latest_results_idx):
                continue
            victim = i
            break
        if victim is None:
            break
        out.pop(victim)
        dropped += 1
        # Indices shift after a pop; recompute the protected ones.
        first_user_idx = next(
            (i for i, m in enumerate(out) if m.get("role") == "user"),
            None)
        latest_user_idx = max(
            (i for i, m in enumerate(out) if m.get("role") == "user"),
            default=None)
        latest_results_idx = max(
            (i for i, m in enumerate(out) if _is_results_message(m)),
            default=None)
    tokens_after = _tokens(out, estimate)
    # Last resort: shrink the latest results message itself
    # (whole-message truncation with disclosure) before admitting
    # defeat — never touch system or the current instruction.
    while (tokens_after > budget_tokens and latest_results_idx is not None
           and len(str(out[latest_results_idx].get("content") or "")) > 200):
        content = str(out[latest_results_idx].get("content") or "")
        halved = content[:max(100, len(content) // 2)] + _TRUNC_SUFFIX
        out[latest_results_idx] = {**out[latest_results_idx],
                                   "content": halved}
        truncated += 1
        tokens_after = _tokens(out, estimate)
    return out, {
        "dropped": dropped,
        "truncated": truncated,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "over_budget_remaining": tokens_after > budget_tokens,
    }


def refit_groups(messages: list[dict], *, budget_tokens: int,
                 estimate: Callable[[str], int]) -> tuple[list[dict], dict]:
    """Group-aware re-fit for tool-role exchanges (assistant
    tool_calls + following tool messages = ONE group, dropped
    whole or kept whole, never split). System + latest user
    messages are protected as in refit_messages. Returns
    (messages, report) with the same report shape."""
    if not messages:
        return [], {"dropped": 0, "truncated": 0, "tokens_before": 0,
                    "tokens_after": 0, "over_budget_remaining": False}
    tokens_before = _tokens(messages, estimate)
    # Partition into groups: a system/user message is its own
    # group; an assistant message with tool_calls plus the
    # following tool messages form one group.
    groups: list[list[int]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            group = [i]
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                group.append(j)
                j += 1
            groups.append(group)
            i = j
        else:
            groups.append([i])
            i += 1
    latest_user_group = max(
        (gi for gi, g in enumerate(groups)
         if messages[g[0]].get("role") == "user"),
        default=None)
    first_user_group = next(
        (gi for gi, g in enumerate(groups)
         if messages[g[0]].get("role") == "user"),
        None)
    dropped = 0
    kept = [True] * len(groups)
    # Never drop group 0 (system), the first user group (the
    # original instruction), or the latest user group.
    while True:
        current = [m for gi, g in enumerate(groups) if kept[gi]
                   for m in (messages[i] for i in g)]
        if _tokens(current, estimate) <= budget_tokens:
            break
        victim = None
        for gi in range(len(groups)):
            if gi in (0, first_user_group, latest_user_group) \
                    or not kept[gi]:
                continue
            victim = gi
            break
        if victim is None:
            break
        kept[victim] = False
        dropped += 1
    out = [messages[i] for gi, g in enumerate(groups) if kept[gi]
           for i in g]
    return out, {
        "dropped": dropped,
        "truncated": 0,
        "tokens_before": tokens_before,
        "tokens_after": _tokens(out, estimate),
        "over_budget_remaining":
            _tokens(out, estimate) > budget_tokens,
    }

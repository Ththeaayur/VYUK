"""Canonical tool-call envelopes (fourth audit C07, Phase 2).

The four brains speak tool calls in different shapes: the local
builtin brain emits a JSON block parsed to (name, args); Groq
emits OpenAI tool_calls (id + function.name + JSON-string
arguments); Gemini Live emits native function calls (name +
args dict); the lite brain matches intents, not calls. A working
tool registry does not guarantee consistent behaviour across
those shapes — the audit's C07 finding — so every dispatch now
passes through ONE envelope:

    ToolInvocation: tool name, args dict, a stable action_id,
    the source brain, and the provider-native call id ("" when
    the shape has none).

The action_id is content-derived (SHA-256 of tool + canonical
args), not random: the same call always carries the same id, so
duplicate detection, receipts, and traces can key on it. The
envelope never re-validates what the funnel already validates:
adapters convert shapes, execute_envelope dispatches through
the real funnel, and ToolOutcome maps the funnel's own result
dict (core/funnel/tool_result vocabulary) to a uniform outcome. The
original funnel dict rides along untouched (to_funnel), so
wiring the envelope into a brain path is behaviour-identical
by construction — the envelope adds structure, never a second
gate.

Stdlib-only, no core imports: every module may import this
one without a cycle.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

SOURCES = ("local", "groq", "live", "lite")

# Funnel statuses, mirrored from core/funnel/tool_result (imported lazily
# by consumers that need the mapping helpers, never here, so this
# module stays cycle-free).
STATUSES = ("success", "failure", "refused", "timeout", "unknown")


def canonical_args(args: Any) -> str:
    """Canonical JSON for an args value (sort_keys, str fallback),
    so the same call always hashes the same way."""
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(args)


def action_id_for(tool: str, args: Any) -> str:
    """Stable action id for a call: SHA-256 of tool + canonical
    args. Distinct calls differ; identical calls share the id."""
    blob = f"{tool}\0{canonical_args(args)}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class ToolInvocation:
    """One tool call in canonical form."""

    tool: str
    args: dict
    action_id: str
    source: str
    call_id: str = ""


@dataclass
class ToolOutcome:
    """A funnel result in uniform form. `funnel` is the original
    funnel dict, kept so to_funnel() returns byte-identical
    behaviour for callers that branch on it."""

    action_id: str
    tool: str
    status: str
    kind: str
    message: str
    result: Any
    funnel: dict | None = None

    def to_funnel(self) -> dict:
        """The original funnel dict when one exists, else the
        constructed refusal/validation dict for outcomes that
        never dispatched (validation branch of execute_envelope).
        Callers that branch on the funnel dict see exactly what
        the funnel returned."""
        if isinstance(self.funnel, dict):
            return self.funnel
        out: dict[str, Any] = {"status": self.status}
        if self.kind:
            out["kind"] = self.kind
        if self.message:
            out["message"] = self.message
        if self.status == "success" and self.result is not None:
            out["result"] = self.result
        return out


def _map_funnel(invocation: ToolInvocation, result: Any) -> ToolOutcome:
    """Map a funnel result dict to a uniform outcome. Legacy
    "error" statuses map by kind (refusal kinds -> "refused",
    the rest -> "failure"), mirroring core/funnel/tool_result."""
    if not isinstance(result, dict):
        return ToolOutcome(
            action_id=invocation.action_id, tool=invocation.tool,
            status="unknown", kind="",
            message="Tool returned a non-dict result; outcome unknown.",
            result=None, funnel=None)
    status = str(result.get("status", ""))
    kind = str(result.get("kind", ""))
    if status == "error":
        if kind in ("refused", "not_confirmed", "validation",
                    "budget", "timeout"):
            status = "refused"
        else:
            status = "failure"
    if status not in STATUSES:
        status = "unknown"
    message = result.get("message", "")
    if not isinstance(message, str):
        message = str(message)
    payload = result.get("result") if status == "success" else None
    return ToolOutcome(
        action_id=invocation.action_id, tool=invocation.tool,
        status=status, kind=kind, message=message, result=payload,
        funnel=result)


def from_local_call(name: str, args: Any) -> ToolInvocation:
    """Local builtin shape: a parsed (name, args) pair."""
    if not isinstance(args, dict):
        raise ValueError("local tool call arguments must be a JSON object")
    return ToolInvocation(
        tool=str(name), args=args,
        action_id=action_id_for(str(name), args), source="local")


def from_openai_call(call: dict) -> ToolInvocation:
    """OpenAI shape (Groq native tool_calls): id + function.name
    + function.arguments as a JSON string or an already-parsed
    dict. Raises ValueError on a malformed shape — callers that
    already validated arguments (Groq's _run_tool_call) never
    hit this; direct callers get a loud refusal, not a dispatch.
    """
    if not isinstance(call, dict):
        raise ValueError("OpenAI tool call must be an object")
    fn = call.get("function") or {}
    if not isinstance(fn, dict):
        raise ValueError("OpenAI tool call needs a function object")
    name = fn.get("name", "")
    raw_args = fn.get("arguments")
    if isinstance(raw_args, str):
        if raw_args.strip():
            try:
                args = json.loads(raw_args)
            except ValueError as exc:
                raise ValueError(
                    "OpenAI tool call arguments are not valid JSON") from exc
        else:
            args = {}
    else:
        args = raw_args
    if not isinstance(args, dict):
        raise ValueError("OpenAI tool call arguments must be a JSON object")
    return ToolInvocation(
        tool=str(name), args=args,
        action_id=action_id_for(str(name), args), source="groq",
        call_id=str(call.get("id", "") or ""))


def from_live_call(name: str, args: Any) -> ToolInvocation:
    """Gemini Live shape: a native function call (name + args
    dict). The live path's executor-thread worker calls this
    before dispatching through the same funnel."""
    if not isinstance(args, dict):
        raise ValueError("live tool call arguments must be an object")
    return ToolInvocation(
        tool=str(name), args=args,
        action_id=action_id_for(str(name), args), source="live")


def execute_envelope(invocation: ToolInvocation,
                     execute_fn: Callable[[str, dict], Any]) -> ToolOutcome:
    """Dispatch one envelope through the real funnel and map the
    result to a uniform outcome. The funnel dict is preserved
    verbatim (see ToolOutcome.to_funnel), so envelope wiring
    cannot change what a brain path does with the result — it
    only adds the canonical shape, the stable action id, and
    the uniform status/kind/message view."""
    result = execute_fn(invocation.tool, invocation.args)
    return _map_funnel(invocation, result)

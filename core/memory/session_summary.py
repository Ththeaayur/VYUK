"""
Session Summary Engine for Voice AI Assistant (reworked v5.45, B5).

Summarises a conversation with WHICHEVER brain is active and
stores the result as an EPISODE (core/episodes.py, kind
"session") — not as a memory fact. core/memory/memory.py's context
block (B4) reads the summaries back from the journal in their
own block; get_recent_summaries() below reads them from there
too (with a legacy-fact fallback for pre-B5 installs).

Who can summarise, in the order generate_session_summary tries:
  1. an explicit `summariser` callable — prompt in, text out
     (sync or async). core/brain/brain.py's make_summariser() builds
     the right one for the active engine (Gemini client, Groq
     chat path, local builtin/Ollama chat path).
  2. a Gemini client (`client.models.generate_content`) — the
     original v1 path, unchanged in shape.
  3. a session object passed as `client`: a local session
     (its _llama_chat builtin path or _ollama_chat path) or a
     Groq session (its _chat_completion path).

Triggers: the GUI's window-close call (which now passes the
active brain — see gui/main_window.py) AND a turn count:
TurnSummaryTrigger fires every SUMMARY_EVERY_TURNS turns
(env SESSION_SUMMARY_EVERY_TURNS, default 12, 0 disables);
the local and Groq sessions consult one per session and run
the summary in the background of the turn that trips it.

Lite tier: summaries SKIP entirely when the effective local
model is the Lite tier (Qwen3-1.7B — its tokens are needed for
answers, and a 1.7B summary is not worth the reply cap) or when
the summariser is a LiteLiveSession. is_lite_tier() is the one
predicate; tests monkeypatch it.
"""

import asyncio
import datetime
import os

from .memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
from .. import config_spec


def _every_turns() -> int:
    """Turns between automatic summaries (config-exposed via
    env; a config field may override when present)."""
    raw = config_spec.env("SESSION_SUMMARY_EVERY_TURNS")
    try:
        value = int(raw) if raw not in (None, "") else 12
    except (TypeError, ValueError):
        value = 12
    return max(0, value)


SUMMARY_EVERY_TURNS_DEFAULT = 12


class TurnSummaryTrigger:
    """Counts completed brain turns; should_fire() is True once
    every N turns (N = _every_turns()). mark_fired() restarts
    the count. N=0 disables the trigger entirely."""

    def __init__(self, every: int | None = None):
        self._every = _every_turns() if every is None else max(0, int(every))
        self._count = 0

    def note_turn(self) -> bool:
        if self._every <= 0:
            return False
        self._count += 1
        return self._count >= self._every

    def should_fire(self) -> bool:
        return self._every > 0 and self._count >= self._every

    def mark_fired(self):
        self._count = 0

    @property
    def count(self) -> int:
        return self._count


def is_lite_tier() -> bool:
    """True when the effective local brain is the Lite tier.
    Best-effort: any doubt answers False (summaries proceed)."""
    try:
        from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/

        return str(local_assets.effective_model().get("tier") or "") == "lite"
    except Exception:  # noqa: BLE001
        return False


def _is_lite_session(obj) -> bool:
    # GAPS-22 (B-V3): the lite session declares itself (is_lite),
    # never a class-name check.
    return bool(getattr(obj, "is_lite", False))


def _build_prompt(transcript: list[dict]) -> str | None:
    lines = []
    for msg in transcript[-30:]:
        if not isinstance(msg, dict):
            continue
        role = "User" if msg.get("role") == "user" else "VYUK"
        text = str(msg.get("content") or "").strip()
        if text:
            lines.append(f"{role}: {text[:200]}")
    if not lines:
        return None
    transcript_text = "\n".join(lines)
    return (
        "Summarize the following conversation in 3-5 concise bullet points. "
        "Focus on key user requests, facts learned, and outcomes. "
        "Keep it under 100 words total.\n\n"
        f"--- CONVERSATION ---\n{transcript_text}\n--- END ---"
    )


async def _call_summariser(summariser, prompt: str) -> str | None:
    result = summariser(prompt)
    if asyncio.iscoroutine(result):
        result = await result
    text = str(result or "").strip()
    return text or None


async def _summarise_with_session(session, prompt: str) -> str | None:
    """Summary via a session object's own chat path: local
    builtin (_llama_chat), local Ollama (_ollama_chat), or Groq
    (_chat_completion). Runs blocking paths in an executor."""
    loop = asyncio.get_running_loop()
    llama_chat = getattr(session, "_llama_chat", None)
    if callable(llama_chat) and getattr(session, "_llama", None) is not None:
        return await loop.run_in_executor(
            None, lambda: str(llama_chat(
                [{"role": "user", "content": prompt}]) or "").strip() or None)
    ollama_chat = getattr(session, "_ollama_chat", None)
    if callable(ollama_chat) and str(
            getattr(session, "_backend", "")).lower() == "ollama":
        reply = await loop.run_in_executor(
            None, lambda: ollama_chat(
                [{"role": "user", "content": prompt}], []))
        msg = (reply or {}).get("message") or {}
        return str(msg.get("content") or "").strip() or None
    chat_completion = getattr(session, "_chat_completion", None)
    if callable(chat_completion):
        status, parsed = await loop.run_in_executor(
            None, lambda: chat_completion(
                [{"role": "user", "content": prompt}]))
        if status == 200 and isinstance(parsed, dict):
            choices = parsed.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                return str(msg.get("content") or "").strip() or None
    return None


async def generate_session_summary(transcript: list[dict], client=None,
                                   model_name: str = "",
                                   summariser=None) -> str | None:
    """
    Summarise `transcript` with the active brain (see module
    docstring for the resolution order) and store the result as
    a session EPISODE. Returns the summary text or None.

    The old signature (transcript, gemini_client, model_name)
    keeps working unchanged. Skips entirely on the Lite tier /
    for a lite session, and for transcripts under 4 messages.
    """
    if not transcript or len(transcript) < 4:
        return None
    if _is_lite_session(client) or _is_lite_session(summariser):
        print("[Summary] Skipped: Lite tier session.")
        return None
    # Lite tier (the Qwen3-1.7B local model): skip entirely —
    # its tokens belong to answers, and a 1.7B summary is not
    # worth the reply cap. The skip covers every path whose work
    # would fall on that local model (a local session object, or
    # no summariser and no cloud client); a Gemini client path
    # is cloud work and is not the Lite tier's to skip. The
    # explicit-summariser path is guarded at its source:
    # brain.make_summariser returns None for a local engine on
    # the Lite tier, so a summariser that arrives here is a
    # cloud brain's.
    if is_lite_tier() and summariser is None and not (
            client is not None and hasattr(client, "models")):
        print("[Summary] Skipped: Lite tier model.")
        return None

    prompt = _build_prompt(transcript)
    if prompt is None:
        return None

    summary: str | None = None
    try:
        if summariser is not None:
            summary = await _call_summariser(summariser, prompt)
        elif client is not None and hasattr(client, "models"):
            response = client.models.generate_content(
                model=model_name, contents=prompt)
            summary = (response.text.strip()
                       if getattr(response, "text", None) else None)
        elif client is not None:
            summary = await _summarise_with_session(client, prompt)
    except Exception as e:  # noqa: BLE001
        print(f"[Summary] Generation failed: {e}")
        return None

    if summary:
        # B5: the summary is an EPISODE, not a fact (pre-B5 it was
        # ALSO journalled — that copy is now the only copy).
        try:
            from . import episodes

            episodes.record("session", summary)
        except Exception:  # noqa: BLE001 — journaling never breaks it
            pass
    return summary


def save_session_summary(summary: str):
    """Persists a summary as a session EPISODE (B5 — it used to
    save a session_summary FACT; episodes.record dedupes, so a
    summary already recorded by generate_session_summary is not
    stored twice)."""
    if not summary or not str(summary).strip():
        return
    try:
        from . import episodes

        episodes.record("session", str(summary).strip())
    except Exception as e:  # noqa: BLE001
        print(f"[Summary] Episode save failed: {e}")
    print(f"[Summary] Session saved as episode at "
          f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")


def get_recent_summaries(limit: int = 3) -> list[str]:
    """The most recent session summaries, oldest first — read
    from the episodes journal (B5); legacy session_summary facts
    are the fallback when the journal has none."""
    try:
        from . import episodes

        found = episodes.recent_session_summaries(limit=limit)
    except Exception:  # noqa: BLE001
        found = []
    if found:
        return found
    all_facts = memory_store.get_all_facts()
    summaries = [
        f["value"]
        for f in all_facts
        if f.get("category") == "session_summary"
    ]
    return summaries[-limit:]

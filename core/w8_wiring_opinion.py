"""w8_wiring part module (skills split B14): w8_wiring_opinion."""
from __future__ import annotations

# Module-level imports travel verbatim with the units that use them.
from . import second_opinion
# INTENTIONAL (skills split B14): gate constant from gates (P3).
from .w8_wiring_gates import SECOND_OPINION_TRIGGER_OWED



# ---------------------------------------------------------------------
# Second opinion: two REAL brain callables, explicit opt-in only (item 7)
# ---------------------------------------------------------------------


def second_opinion_from_sessions(cloud_session, local_session):
    """Build the two REAL brain callables for cross_check().

    cloud_session: a live cloud-text session exposing
    _chat_completion(messages) -> (status, parsed).
    local_session: a live local session exposing _llama_chat
    (resident backend, tried first) and/or _ollama_chat
    (fallback — see local_brain). Each callable takes the
    question string and returns answer text. A brain that
    errors or returns nothing makes the verdict
    "unavailable" in the module — never a silent fallback.
    """
    def cloud_brain(question: str) -> str:
        status, parsed = cloud_session._chat_completion(
            [{"role": "user", "content": question}])
        if status != 200 or not isinstance(parsed, dict):
            return ""
        choices = parsed.get("choices") or []
        if not choices:
            return ""
        return str((choices[0].get("message") or {}).get("content")
                   or "")

    def local_brain(question: str) -> str:
        # INTENTIONAL (factory dispatch fix, 2026-11-05, parent
        # ruling): resident backend FIRST. The companion holder
        # only supplies builtin legs after preload + resident
        # _llama, so _llama_chat is callable when the holder
        # supplies the leg; the resident path needs no server, so
        # the local opinion works on the default configuration
        # with no Ollama server running. _ollama_chat stays as
        # the fallback for ollama-backed legs (which have no
        # _llama_chat) and for a resident call that fails or
        # returns nothing. Both failing/empty returns "" so the
        # check maps the brain "unavailable" — never a fake
        # answer. The caller (cross_check) catches brain
        # exceptions, but the fallback must live here: cross_check
        # never retries the other backend, so an exception from
        # the first backend tried would otherwise make the local
        # side unavailable immediately.
        messages = [{"role": "user", "content": question}]

        def _text(reply):
            if isinstance(reply, dict):
                return str((reply.get("message") or {}).get("content")
                           or "")
            return str(reply or "")

        llama = getattr(local_session, "_llama_chat", None)
        if callable(llama):
            try:
                answer = _text(llama(messages))
            except Exception:  # noqa: BLE001 — fall back to Ollama
                answer = ""
            if answer.strip():
                return answer
        ollama = getattr(local_session, "_ollama_chat", None)
        if callable(ollama):
            try:
                answer = _text(ollama(messages, []))
            except Exception:  # noqa: BLE001 — both backends failed
                answer = ""
            if answer.strip():
                return answer
        return ""

    return cloud_brain, local_brain




def request_second_opinion(question: str, *, cloud_session=None,
                           local_session=None, brain_a=None,
                           brain_b=None):
    """EXPLICIT OPT-IN second-opinion path (trigger RULED 2026-11-05:
    destructive-word + side-effecting-tool conjunction, auto-invoked
    in the coordinator when brains are configured — see
    SECOND_OPINION_TRIGGER_OWED). No live answer path calls this
    function directly, so calling it is always a deliberate decision. With live sessions it cross-checks
    the two real brains; with injected callables it cross-
    checks those; with neither it returns "unavailable"
    honestly instead of a fake verdict. Disagreements
    escalate; resolved_answer is always None."""
    if brain_a is None or brain_b is None:
        if cloud_session is None or local_session is None:
            return {"status": "unavailable",
                    "detail": "no live brain sessions supplied; "
                              "no second opinion formed",
                    "trigger_owed": SECOND_OPINION_TRIGGER_OWED}
        brain_a, brain_b = second_opinion_from_sessions(
            cloud_session, local_session)
    result = second_opinion.cross_check(question, brain_a, brain_b)
    return {"status": result.verdict,
            "result": result,
            "rendered": second_opinion.render_for_ui(result),
            "escalation": second_opinion.escalation_payload(result),
            "resolved_answer": result.resolved_answer}



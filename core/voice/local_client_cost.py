"""cost concern extracted from core/voice/local_client.py (refactor Phase 1 step 2). Behaviour identical; every name here stays importable from core.local_client."""

from typing import Any


_COST_LEDGER: Any = None
def _cost_ledger_local() -> Any:
    global _COST_LEDGER
    if _COST_LEDGER is None:
        from .. import cost_ledger as _cl  # Phase 3 V1b: moved to core/voice/

        _COST_LEDGER = _cl.CostLedger.from_env()
    return _COST_LEDGER
def _cost_prompt_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)
def _cost_record_local(usage: Any, model: str, prompt_text: str,
                       completion_text: str = "") -> None:
    try:
        _cost_ledger_local().record_from_usage(
            "local", model, usage, prompt_text, completion_text)
    except Exception:  # noqa: BLE001 — ledger failure never breaks a turn
        pass

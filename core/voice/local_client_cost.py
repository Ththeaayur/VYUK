"""cost concern extracted from core/voice/local_client.py (refactor Phase 1 step 2). Behaviour identical; every name here stays importable from core.local_client."""

from typing import Any


_COST_LEDGER: Any = None
def _cost_ledger_local() -> Any:
    global _COST_LEDGER
    if _COST_LEDGER is None:
        from .. import cost_ledger as _cl  # Phase 3 V1b: moved to core/voice/

        _COST_LEDGER = _cl.CostLedger.from_env()
    return _COST_LEDGER


def _log_line(message: str):
    """Append one line to data/activity.log, prefixed [Local]
    (the same file the Live and Groq hooks write to). Never
    raises. The DATA_DIR import is lazy so importing this
    module never touches the config layer."""
    try:
        from ..config import DATA_DIR

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / "activity.log", "a", encoding="utf-8") as fh:
            fh.write(f"[Local] {message}\n")
    except Exception:  # noqa: BLE001
        pass


def _cost_ledger_db_path() -> str:
    """The ledger file the record site writes to, for the loss
    log line. Never raises: falls back to the default filename
    when the ledger itself cannot be built."""
    try:
        return str(_cost_ledger_local().db_path)
    except Exception:  # noqa: BLE001
        from .. import cost_ledger as _cl

        return _cl.DEFAULT_DB_FILENAME


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
    except Exception as exc:  # noqa: BLE001 — ledger failure
        # never breaks a turn. C3-F6: a lost row means the cap
        # totals undercount, so the loss is logged and the
        # ledger is marked degraded — the Activity feed surfaces
        # it (same shape as the Live hook, field-check fix #2).
        try:
            _log_line(
                "Local cost row lost: the turn's spend was not "
                "recorded (%s: %s) — ledger %s; the cost cap "
                "totals undercount until this is fixed."
                % (type(exc).__name__, str(exc).strip(),
                   _cost_ledger_db_path()))
        except Exception:  # noqa: BLE001 - the log must never raise
            pass
        try:
            from .. import cost_ledger as _cl

            _cl.note_record_failure("local", exc)
        except Exception:  # noqa: BLE001 - the note must never raise
            pass

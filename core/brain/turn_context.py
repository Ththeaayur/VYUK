"""
Turn context for VYUK (v5.52, item 7).

Process-wide holder for the CURRENT user instruction — the
text of the turn now being served — plus a dictation flag.
Deliberately tiny and dependency-free (stdlib only), in the
core/session_epoch pattern, so every client can publish the
turn without import-cycle risk and the executor funnel can
consult it lazily.

Why it exists: the echo guard in ToolRegistry.execute()
(core/funnel/tools_registry.py) compares text-injection payloads
against the instruction that prompted them. Without a seam
like this the executor cannot know what the user actually
said — handlers see only their own arguments.

Semantics:
- Clients set the instruction at the start of a genuine user
  turn (typed or transcribed). Bracketed VYUK-internal texts
  (watcher notices and the like) never overwrite it — the same
  rule the clients apply to injection.clear_taint.
- The value is whatever the LAST genuine turn set. Readers
  must treat a stale instruction as "unknown", never as
  evidence: the guard only blocks a near-verbatim echo, which
  a stale instruction can only cause if the model is echoing
  an old request verbatim — itself the failure being guarded.
- Dictation: an explicit dictation mode exists (turn_machine.
  DictationMode, PILOT). While it is active it mirrors this flag
  via _sync_turn_context, so the echo guard can tell dictated
  text from an instruction echo. The flag is unset whenever no
  dictation session is active.

Nothing in this module raises.
"""

import threading

_lock = threading.Lock()
_instruction = ""
_dictation_active = False


def set_current_instruction(text) -> None:
    """Publish the current turn's user instruction."""
    global _instruction
    try:
        value = str(text or "")
    except Exception:  # noqa: BLE001 — publishing must never raise
        return
    with _lock:
        _instruction = value


def current_instruction() -> str:
    """The last published instruction, or "" when none."""
    with _lock:
        return _instruction


def clear_current_instruction() -> None:
    global _instruction
    with _lock:
        _instruction = ""


def set_dictation_active(active: bool) -> None:
    """Mark/unmark an explicit dictation mode (turn_machine.DictationMode
    mirrors this flag while a dictation session is active)."""
    global _dictation_active
    with _lock:
        _dictation_active = bool(active)


def dictation_active() -> bool:
    with _lock:
        return _dictation_active

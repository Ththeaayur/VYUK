"""Durable per-action event journal for Groq turns.

Fourth audit C02 (Phase 0): a Groq turn appends tool-call and
tool-result messages to the session's ephemeral ``_history``,
and any later turn error deletes ``_history[turn_start:]``.
The desktop effects of completed actions do not un-happen when
history is trimmed, so a retry can replay a non-idempotent
action — or proceed unaware it ever ran. This journal is the
separate, immutable truth: every Groq tool execution is recorded
BEFORE it runs (invocation id, tool, sanitized args, turn
linkage) and its outcome AFTER it runs, and the turn-failure
rollback never touches this file.

Store discipline (mirrors core/brain/task_ledger.py): versioned
whole-file JSON, atomic tmp + ``os.replace``, lazy load with a
once-only guard. A corrupt or malformed file fails LOUDLY
(``ValueError`` on load) and all later writes are REFUSED —
never silently rewritten, never silently dropped. Journal
failures must never break a turn: callers catch journal
exceptions, log them, and proceed (the gap is visible in the
log, not fatal to the conversation).

Replay guard (implemented in cloud_text_client._run_tool_call,
using this module's lookups): an action that COMPLETED in a
turn that later FAILED is the dangerous case — history was
rolled back, so the model does not know it happened. Re-issuing
that (tool, args) pair is blocked ONCE per turn: the tool call
returns a prior-execution note instead of running, and the
event is marked surfaced in that turn. A re-issue in a LATER
turn is allowed — by then the prior execution has been
surfaced to the conversation, so the attempt is informed, not
blind, and a user confirmation between turns can authorize it.
Events from successful turns never trigger the guard (history
retains them; the model knows they ran), and failed/refused/
unknown outcomes never trigger it either (retrying a failed
action is legitimate).

Size bound: at most ``_MAX_EVENTS`` events; the oldest are
pruned first on save. The guard's horizon is therefore the
journal window — documented, not hidden. Args blobs are capped
(the signature is computed from the FULL sanitized args before
any truncation, so lookups still match).
"""

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

_SCHEMA_VERSION = 1
_DEFAULT_FILENAME = "groq_action_journal.json"
_MAX_EVENTS = 1000
_MAX_ARGS_CHARS = 4000
_MAX_ARGS_PREVIEW_CHARS = 500
_MAX_SUMMARY_CHARS = 500
_MAX_LABEL_CHARS = 200


def default_journal_path() -> Path:
    """Production path: data/groq_action_journal.json."""
    from .config import DATA_DIR

    return Path(DATA_DIR) / _DEFAULT_FILENAME


def _sanitize(value):
    """Crash-report sanitizer, reused (same as the task ledger):
    sensitive key names become [redacted] at every nesting level,
    secret-shaped strings are scrubbed. Never store raw keys."""
    from .distribution_hardening import _sanitize_value

    return _sanitize_value(value)


class GroqActionJournal:
    """Append-only (by event) durable journal for Groq actions."""

    def __init__(self, path=None, max_events: int = _MAX_EVENTS):
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self.path = Path(path) if path is not None else default_journal_path()
        self._max_events = max_events
        self._events: list[dict] = []
        self._loaded = False
        self._load_error = ""

    # -- load / save (task-ledger discipline) -------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            self._events = []
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — corrupt file is loud
            self._load_error = (
                f"groq action journal unreadable at {self.path}: {exc}")
            raise ValueError(self._load_error)
        if (not isinstance(payload, dict)
                or payload.get("version") != _SCHEMA_VERSION
                or not isinstance(payload.get("events"), list)):
            self._load_error = (
                f"groq action journal malformed at {self.path}: "
                "expected version 1 with an events list")
            raise ValueError(self._load_error)
        self._events = [e for e in payload["events"]
                        if isinstance(e, dict)]

    def _save(self) -> None:
        """Atomic whole-file rewrite. Refuses when load failed."""
        self._ensure_loaded()
        if self._load_error:
            raise ValueError(
                "refusing to overwrite unreadable groq action journal: "
                f"{self._load_error}")
        events = self._events
        if len(events) > self._max_events:
            events = events[-self._max_events:]
            self._events = events
        payload = {"version": _SCHEMA_VERSION, "events": events}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=self.path.name + ".",
                                        dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1, sort_keys=True)
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- writes ---------------------------------------------------------

    def record_started(self, *, tool: str, args_signature: str,
                       args, turn_id: str, turn_label: str = "") -> dict:
        """Durable pre-execution record. Returns the event (copy)."""
        sanitized = _sanitize(args)
        try:
            dumped = json.dumps(sanitized, sort_keys=True, default=str)
        except (TypeError, ValueError):
            dumped = repr(sanitized)
        if len(dumped) > _MAX_ARGS_CHARS:
            stored_args: object = {
                "_args_truncated": True,
                "preview": dumped[:_MAX_ARGS_PREVIEW_CHARS],
            }
        else:
            stored_args = sanitized
        label = _sanitize(turn_label)
        if not isinstance(label, str):
            label = str(label)
        event = {
            "event_id": uuid.uuid4().hex,
            "invocation_id": uuid.uuid4().hex,
            "tool": str(tool),
            "args_signature": str(args_signature),
            "args": stored_args,
            "turn_id": str(turn_id),
            "turn_label": label[:_MAX_LABEL_CHARS],
            "created_at": time.time(),
            "status": "started",
            "outcome_summary": "",
            "turn_failed": False,
            "surfaced_in_turn": None,
        }
        self._ensure_loaded()
        self._events.append(event)
        self._save()
        return dict(event)

    def record_outcome(self, invocation_id: str, *, status: str,
                       outcome_summary: str = "") -> dict:
        """Attach the outcome to a started event. Unknown id is an
        error (loud) — outcomes must never silently vanish."""
        self._ensure_loaded()
        for event in self._events:
            if event.get("invocation_id") == invocation_id:
                event["status"] = str(status)
                summary = outcome_summary
                if not isinstance(summary, str):
                    summary = str(summary)
                event["outcome_summary"] = summary[:_MAX_SUMMARY_CHARS]
                self._save()
                return dict(event)
        raise ValueError(
            f"groq action journal: no event for invocation "
            f"{invocation_id!r}")

    def mark_turn_failed(self, turn_id: str) -> bool:
        """Mark every event of a turn as belonging to a failed turn.
        Called from the turn-failure branches AFTER the history
        rollback — the journal keeps what history dropped. Returns
        True when anything changed (and was saved)."""
        self._ensure_loaded()
        changed = False
        for event in self._events:
            if event.get("turn_id") == str(turn_id) \
                    and not event.get("turn_failed"):
                event["turn_failed"] = True
                changed = True
        if changed:
            self._save()
        return changed

    def mark_surfaced(self, event_id: str, turn_id: str) -> bool:
        """Record that a prior execution was surfaced to the model
        in this turn (the replay guard's one-block-per-turn rule).
        Returns True when the event was found and marked."""
        self._ensure_loaded()
        for event in self._events:
            if event.get("event_id") == event_id:
                event["surfaced_in_turn"] = str(turn_id)
                self._save()
                return True
        return False

    # -- reads ----------------------------------------------------------

    def find_completed_failed_turn(self, *, tool: str,
                                   args_signature: str) -> dict | None:
        """Latest event for (tool, args) that COMPLETED in a turn
        that later FAILED — the replay-guard trigger. Events from
        successful turns, and non-success outcomes, never match."""
        self._ensure_loaded()
        for event in reversed(self._events):
            if (event.get("tool") == tool
                    and event.get("args_signature") == args_signature
                    and event.get("status") == "success"
                    and event.get("turn_failed")):
                return dict(event)
        return None

    def events(self) -> list[dict]:
        """A copy of all events (diagnosis / tests)."""
        self._ensure_loaded()
        return [dict(e) for e in self._events]

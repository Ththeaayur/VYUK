import json
from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py


# ---------------------------------------------------------------------------
# Small shared helpers — facts, the user's name, audio playback
# ---------------------------------------------------------------------------


def _fact_json(category: str, key: str, default: dict) -> dict:
    """A memory fact holding a JSON object, or a copy of default."""
    try:
        fact = memory_store.get_fact(category, key)
        if fact and fact.get("value"):
            data = json.loads(fact["value"])
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001 — memory is best-effort here
        pass
    return dict(default)


def _save_fact_json(category: str, key: str, data: dict) -> None:
    try:
        memory_store.save_fact(category, key, json.dumps(data))
    except Exception:  # noqa: BLE001 — the game must not crash on it
        pass


def _user_first_name() -> str | None:
    """The user's first name from memory facts, or None. Never
    guessed: only an explicit name fact counts."""
    try:
        facts = memory_store.get_all_facts()
    except Exception:  # noqa: BLE001
        return None
    for fact in facts or []:
        key = str(fact.get("key", "")).strip().lower()
        if key in ("name", "first name", "first_name", "user name",
                   "my name", "full name"):
            value = str(fact.get("value", "")).strip()
            if value:
                return value.split()[0]
    return None


class _AudioUnavailable(Exception):
    """Playback couldn't happen; the message is user-ready."""


def _play_float_samples(samples, rate: int) -> None:
    """Play float32 mono samples once, blocking. Raises
    _AudioUnavailable with a user-ready reason."""
    try:
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001 — missing lib counts too
        raise _AudioUnavailable(
            f"the audio library isn't available ({e})") from e
    try:
        sd.play(samples, rate)
        sd.wait()
    except Exception as e:  # noqa: BLE001
        raise _AudioUnavailable(
            f"no speaker could be opened ({e})") from e

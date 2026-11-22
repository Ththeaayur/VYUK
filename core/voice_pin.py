"""Voice PIN — the owner-set spoken unlock for a voice session
(loophole batch 4 — #1, #20, L-27, #19).

The owner-proof lock means a spoken yes/no cannot resolve a
pending confirmation in an unproven session. The on-screen unlock
is the primary path; the voice PIN is the voice-only path for
when the owner is away from the screen (hands busy, laptop
across the room). The owner sets a 4–8 digit PIN in Settings;
a voice session that says it ("voice PIN 1234", "unlock voice
1234", "my voice PIN is 1234") is marked proven owner for that
session only. A new session starts unproven again.

Storage mirrors the backup passphrase (core/backup.py): the PIN
is stored via the secrets store (protect_bytes) at
data/voice_pin.bin, 0600, never in plaintext, never inside a
backup, never echoed or spoken back. A PIN is never set by
default — with no PIN stored there is no voice unlock path at
all (the strictest default). A wrong PIN never unlocks, and a
spoken PIN is consumed as a command (never learned, never a
brain command).

Residual risk, stated honestly: a spoken PIN can be overheard
by anyone in the room, so it is weaker than the on-screen
unlock. It is opt-in (the owner chooses to set one), it only
unlocks the current session, and it is strictly better than no
check: a visitor, a TV or a recording that does not know the
PIN cannot approve anything.
"""

from __future__ import annotations

import os
import re

PIN_FILENAME = "voice_pin.bin"
MIN_PIN_LEN = 4
MAX_PIN_LEN = 8

# Phrases that carry a PIN: "voice PIN 1234", "unlock voice
# 1234", "my voice PIN is 1234", "voice pin 1234". The digits
# must be the whole token (4–8 digits, nothing else attached)
# so "voice PIN 123456789" (9 digits) and "voice PIN 12ab"
# never parse as a PIN.
_PIN_PHRASE_RE = re.compile(
    r"(?:voice\s+pin|unlock\s+voice|my\s+voice\s+pin\s+is)"
    r"\s+(\d{" + str(MIN_PIN_LEN) + r"," + str(MAX_PIN_LEN) + r"})"
    r"(?!\d)",
    re.IGNORECASE)


def parse_voice_pin_phrase(text: str) -> str | None:
    """The PIN digits in a spoken unlock phrase, or None when
    the utterance carries no well-formed PIN phrase. The match
    is on the whole digit token only."""
    if not text:
        return None
    match = _PIN_PHRASE_RE.search(str(text))
    return match.group(1) if match else None


def _pin_path(data_dir: str) -> str:
    return os.path.join(data_dir, PIN_FILENAME)


def set_voice_pin(data_dir: str, pin: str) -> str:
    """Store the voice PIN for this data dir, protected by the
    secrets store (never in plaintext). The PIN must be 4–8
    digits; anything else raises ValueError in plain words.
    An empty PIN clears it instead. Returns the path written."""
    from .secrets_store import protect_bytes

    path = _pin_path(data_dir)
    pin = str(pin or "").strip()
    if not pin:
        try:
            os.unlink(path)
        except OSError:
            pass
        return path
    if not pin.isdigit() or not MIN_PIN_LEN <= len(pin) <= MAX_PIN_LEN:
        raise ValueError(
            "A voice PIN is 4 to 8 digits — nothing else.")
    os.makedirs(data_dir, exist_ok=True)
    blob = protect_bytes(pin.encode("utf-8"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, blob)
    finally:
        os.close(fd)
    return path


def get_voice_pin(data_dir: str) -> str | None:
    """The stored voice PIN, or None when none is set. A
    corrupt/unreadable store reads as unset (never partial)."""
    from .secrets_store import unprotect_bytes

    path = _pin_path(data_dir)
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
        return unprotect_bytes(blob).decode("utf-8")
    except Exception:  # noqa: BLE001 — unset, corrupt or unreadable
        return None


def has_voice_pin(data_dir: str) -> bool:
    """True when a voice PIN is stored for this data dir."""
    return get_voice_pin(data_dir) is not None


def check_voice_pin(data_dir: str, pin: str) -> bool:
    """True only when a PIN is stored and the given PIN matches
    it exactly. False when none is set, the store is unreadable,
    or the PIN differs — a wrong PIN never unlocks."""
    stored = get_voice_pin(data_dir)
    if stored is None:
        return False
    candidate = str(pin or "").strip()
    if not candidate:
        return False
    return candidate == stored

"""Discord Rich Presence — an ADOPTION of qwertyquerty/pypresence (R8-9).

Show what VYUK is doing in Discord (Rich Presence): the game or
activity name, a details line and a state line, with elapsed time
since the presence was set. The cheapest gaming-as-proof surface in
round 8 — gaming is the product's hand-eye-coordination proof, not a
replacement for gamers.

ADOPT, not port — measured from the wheel itself (no install):
pypresence 4.6.2, wheel 13,734 B, sha256
6587e525a663699ca5fb8621ea147a9c264c135650c50afbccd7b5d9b4e28dc0,
License-Expression: MIT in the wheel's own METADATA, Requires-Dist
is dev extras ONLY (pytest/black/flake8/mypy/isort/sphinx) — ZERO
runtime dependencies. The repo LICENSE file reads "MIT License /
Copyright (c) 2022 qwertyquerty" (read from the repo itself). The
library is stdlib-only (asyncio/json/struct/socket), no network on
import, no telemetry, no auto-connect. Only `Presence` is used; the
full `Client` (OAuth2 authorize and the rest) is never imported.

Why adopt instead of porting the IPC protocol: the protocol's
platform-specific half is Windows named-pipe discovery
(\\\\?\\pipe\\discord-ipc-*) plus the handshake and opcode framing,
and the maintained implementation is 44 KB of straightforward code
that cannot be tested against a real Discord client in this lab (no
Discord client here). A port would re-derive that behaviour blind.

THREE CONSTRAINTS, NON-NEGOTIABLE:
1. The Discord application/client ID is OPERATOR-SUPPLIED, never
   hard-coded. VYUK ships no application ID of its own. The user
   creates their own application at discord.com/developers/applications
   and stores its Application ID with `set_discord_client_id` (or
   passes it per call). A client ID is a public identifier, not a
   secret — it is the application's public number, shown in every
   OAuth URL — but it is still the operator's, never ours.
2. Presence is a PUBLIC display of activity — OPT-IN, off by
   default, never auto-start. No presence is ever set without an
   explicit user request in the same session, and a presence set in
   an earlier session is never re-applied on boot. Only what the
   user asks to show is shown (details / state / name, all
   caller-supplied).
3. No Discord running = an honest refusal, never a silent fallback.
   The IPC is local only (named pipe on Windows, Unix socket
   elsewhere) — no cloud, no account, no token, no password anywhere
   in this batch.

HOW IT WORKS: Discord keeps a Rich Presence showing only while the
IPC pipe is open, so the module holds one connection open (module
level, in-memory only) from `show` until `clear` or the process
ends. `show` connects (or reuses the open connection), sends
SET_ACTIVITY and reads Discord's acknowledgement back — a show is
only claimed when the ack arrived. `clear` clears the activity and
closes the pipe. `status` is a pure in-memory read (configured?
showing? what?) plus a read-only check for the IPC pipe's existence
— it never opens a connection. If Discord quits, the pipe dies and
the next call refuses honestly and forgets the stale presence.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from core.funnel.tools_registry_core import _funnel_only_fn


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CLIENT_ID_FILE = _DATA_DIR / "discord_client_id.json"

# Discord application client IDs are numeric snowflakes: 17-19 digits.
_MIN_CLIENT_ID_LEN = 17
_MAX_CLIENT_ID_LEN = 19
# Discord caps each Rich Presence text field at 128 characters.
_MAX_FIELD_LEN = 128

_ACTIONS = ("show", "clear", "status")

# In-memory only, never persisted: the live IPC connection (held open
# while a presence shows — Discord clears the activity when the pipe
# closes) and what this session is currently showing. A presence set
# in an earlier session is NOT re-applied on boot: nothing here runs
# at import or at startup.
_CONNECTION = None  # pypresence.Presence | None
_CURRENT = None  # dict | None


def _valid_client_id(value) -> bool:
    """A Discord application ID is a numeric snowflake, 17-19 digits."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return (
        _MIN_CLIENT_ID_LEN <= len(text) <= _MAX_CLIENT_ID_LEN
        and text.isdigit()
    )


def _read_client_id() -> str | None:
    """The stored operator-supplied client ID, or None."""
    try:
        data = json.loads(_CLIENT_ID_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("client_id")
    return value if _valid_client_id(value) else None


def _write_client_id(client_id: str) -> bool:
    """Store the client ID, then re-read it: the write is only claimed
    when the re-read finds exactly what was written (A4-filestat)."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CLIENT_ID_FILE.write_text(
            json.dumps({"client_id": client_id}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return False
    return _read_client_id() == client_id


def _close_connection() -> None:
    """Drop the held IPC connection and forget the stale presence."""
    global _CONNECTION, _CURRENT
    if _CONNECTION is not None:
        try:
            _CONNECTION.close()
        except Exception:  # noqa: BLE001 — a dead pipe closes dirty
            pass
    _CONNECTION = None
    _CURRENT = None


def _clamp(text, *, default=None):
    """Discord caps each presence text field at 128 characters."""
    if text is None:
        return default
    text = str(text).strip()
    if not text:
        return default
    return text[:_MAX_FIELD_LEN]


def _presence_available() -> tuple[bool, str]:
    """Is the adopted library importable on this Python?"""
    try:
        import pypresence  # noqa: F401
    except ImportError:
        return False, (
            "pypresence is not installed on this Python — Discord "
            "presence is not available here."
        )
    return True, ""


def _ipc_pipe_exists() -> bool:
    """Read-only check: is a Discord IPC pipe present (no connection)."""
    try:
        from pypresence.utils import get_ipc_path
    except ImportError:
        return False
    try:
        return get_ipc_path() is not None
    except Exception:  # noqa: BLE001 — a pipe probe never raises
        return False


def _connect(client_id: str):
    """Open (or reuse) the local IPC connection to Discord. Raises
    the library's own errors — the caller turns them into plain
    words."""
    global _CONNECTION
    if _CONNECTION is not None:
        return _CONNECTION
    from pypresence import Presence

    presence = Presence(client_id)
    presence.connect()
    _CONNECTION = presence
    return presence


def discord_presence(
    action: str = "status",
    details: str | None = None,
    state: str | None = None,
    name: str | None = None,
    client_id: str | None = None,
) -> str:
    """Show / clear / report Discord Rich Presence, in plain words.

    ``"show"`` sets the presence (details / state / name are what
    Discord shows; the name defaults to "VYUK"; Discord shows how
    long the presence has been up, counted from this call).
    ``"clear"`` takes the presence down and closes the pipe.
    ``"status"`` reports whether a client ID is configured, whether
    a presence is showing this session, and whether Discord's IPC
    pipe is present — without opening a connection. Every result
    says what actually happened — a show is only claimed when
    Discord acknowledged it.
    """
    global _CURRENT
    if action not in _ACTIONS:
        return (
            "Give 'show', 'clear' or 'status' for Discord presence — "
            f"'{action}' isn't one of them."
        )

    if action == "status":
        configured = _read_client_id() is not None
        available, _ = _presence_available()
        if _CURRENT is None:
            showing = "No Discord presence is showing."
        else:
            showing = (
                f"Discord is showing '{_CURRENT['name']}'"
                + (f" — {_CURRENT['details']}" if _CURRENT.get("details") else "")
                + (f" ({_CURRENT['state']})" if _CURRENT.get("state") else "")
                + "."
            )
        return (
            f"{showing} "
            f"Client ID {'is configured' if configured else 'is not configured yet — set one with set_discord_client_id first (create an application at discord.com/developers/applications and copy its Application ID)'}. "
            f"{'pypresence is available on this Python.' if available else 'pypresence is not installed on this Python.'} "
            f"{'Discord is running (its IPC pipe is present).' if _ipc_pipe_exists() else 'Discord does not seem to be running (no IPC pipe found).'}"
        )

    if action == "clear":
        if _CONNECTION is None and _CURRENT is None:
            return "No Discord presence is showing — nothing to clear."
        try:
            _CONNECTION.clear()
        except Exception as exc:  # noqa: BLE001 — a dead pipe clears dirty
            _close_connection()
            return (
                "The Discord presence could not be cleared cleanly "
                f"({exc}) — the connection is dropped, so the presence "
                "is gone with it."
            )
        _close_connection()
        return "Discord presence cleared."

    # show
    available, reason = _presence_available()
    if not available:
        return reason
    chosen_id = client_id.strip() if isinstance(client_id, str) else None
    if chosen_id and not _valid_client_id(chosen_id):
        return (
            "That client ID doesn't look like a Discord application ID "
            "— it should be 17 to 19 digits. Copy the Application ID "
            "from your application at discord.com/developers/applications."
        )
    if not chosen_id:
        chosen_id = _read_client_id()
    if not chosen_id:
        return (
            "No Discord application ID is configured yet, so there is "
            "nothing to show presence under. Create an application at "
            "discord.com/developers/applications, copy its Application "
            "ID, then run set_discord_client_id with it."
        )
    show_name = _clamp(name, default="VYUK")
    show_details = _clamp(details)
    show_state = _clamp(state)
    try:
        connection = _connect(chosen_id)
        ack = connection.update(
            details=show_details,
            state=show_state,
            name=show_name,
            start=int(time.time()),
        )
    except Exception as exc:  # noqa: BLE001 — library errors in plain words
        _close_connection()
        message = str(exc)
        if "Invalid Client ID" in message or "InvalidID" in type(exc).__name__:
            return (
                "Discord refused that application ID — it doesn't match "
                "any Discord application. Check the Application ID at "
                "discord.com/developers/applications and set it again "
                "with set_discord_client_id."
            )
        if "DiscordNotFound" in type(exc).__name__ or "InvalidPipe" in type(exc).__name__:
            return (
                "Discord isn't running on this machine, so there is no "
                "presence to show — start Discord and ask again."
            )
        return f"The Discord presence didn't set ({exc})."
    if not isinstance(ack, dict) or ack.get("evt") == "ERROR":
        _close_connection()
        detail = ""
        if isinstance(ack, dict):
            data = ack.get("data") or {}
            detail = f" ({data.get('message', '')})" if isinstance(data, dict) else ""
        return f"Discord didn't accept the presence{detail} — nothing is showing."
    _CURRENT = {
        "name": show_name,
        "details": show_details,
        "state": show_state,
        "client_id": chosen_id,
        "since": int(time.time()),
    }
    shown = f"Discord is now showing '{show_name}'"
    if show_details:
        shown += f" — {show_details}"
    if show_state:
        shown += f" ({show_state})"
    return shown + "."


def set_discord_client_id(client_id: str = "") -> str:
    """Store the operator's Discord application ID, in plain words.

    The ID is the application's public number from
    discord.com/developers/applications (17-19 digits). It is NOT a
    secret — but it is the operator's, never VYUK's: no application
    ID ships with the product. Changing it drops any presence showing
    under the old ID.
    """
    if not _valid_client_id(client_id):
        return (
            "That doesn't look like a Discord application ID — it "
            "should be 17 to 19 digits. Copy the Application ID from "
            "your application at discord.com/developers/applications."
        )
    clean = str(client_id).strip()
    if _CURRENT is not None or _CONNECTION is not None:
        _close_connection()
        dropped = " The presence showing under the old ID is gone."
    else:
        dropped = ""
    if not _write_client_id(clean):
        return (
            "The Discord application ID could not be saved — the "
            "config file did not read back what was written."
        )
    return (
        "Discord application ID saved. Presence stays off until you "
        f"ask for it.{dropped}"
    )


@_funnel_only_fn
def _tool_discord_presence(
    action: str = "status",
    details: str | None = None,
    state: str | None = None,
    name: str | None = None,
    client_id: str | None = None,
) -> str:
    """Funnel handler: Discord Rich Presence, in plain words."""
    return discord_presence(
        action=action,
        details=details,
        state=state,
        name=name,
        client_id=client_id,
    )


@_funnel_only_fn
def _tool_set_discord_client_id(client_id: str = "") -> str:
    """Funnel handler: store the Discord application ID."""
    return set_discord_client_id(client_id=client_id)


def register_all(registry) -> None:
    """Register the Discord presence pack (R8-9, pypresence adoption)."""
    registry.register(
        name="discord_presence",
        description="Show, clear or report what Discord shows about you (Rich Presence) — the game or activity name, a details line and a state line, with how long it has been up. Opt-in and off by default: nothing is ever shown without an explicit request in the same session, and Discord is a local app on this machine (its IPC pipe) — no cloud, no account, no token. Needs your own Discord application ID (set_discord_client_id first, or pass client_id for this call only); without one, or without Discord running, it says so plainly instead of pretending. Use when the user asks Discord to show what they are doing, or asks what Discord is showing.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "What to do: 'show', 'clear' "
                           "or 'status' (default 'status')"},
                "details": {"type": "string",
                            "description": "The top line Discord shows "
                            "(optional; at most 128 characters)"},
                "state": {"type": "string",
                          "description": "The bottom line Discord shows "
                          "(optional; at most 128 characters)"},
                "name": {"type": "string",
                         "description": "The activity name Discord shows "
                         "(optional; defaults to 'VYUK')"},
                "client_id": {"type": "string",
                              "description": "A Discord application ID to "
                              "use for this call only (optional; otherwise "
                              "the stored one is used)"},
            },
            "required": [],
        },
        handler=_tool_discord_presence,
    )
    registry.register(
        name="set_discord_client_id",
        description="Save your own Discord application ID (the Application ID from your application at discord.com/developers/applications, 17 to 19 digits) so discord_presence knows which application to show presence under. The ID is a public application number, not a secret — but it is yours, never VYUK's: no application ID ships with the product. Changing it drops any presence showing under the old ID. Use when the user gives their Discord application ID, or asks to change it.",
        parameters={
            "type": "object",
            "properties": {
                "client_id": {"type": "string",
                              "description": "Your Discord application ID "
                              "(17 to 19 digits)"},
            },
            "required": ["client_id"],
        },
        handler=_tool_set_discord_client_id,
    )

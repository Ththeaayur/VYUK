from __future__ import annotations

import time

# INTENTIONAL (music_player split B8): _settle uses time;
# _playback_evidence imports skills_media function-locally
# (travels verbatim); the announce seam lives here whole.

_AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".opus",
               ".wma", ".aac"}


def _settle(seconds: float = 1.2) -> None:
    time.sleep(seconds)


def _playback_evidence() -> bool:
    """True when an audio render session exists (the media
    stack's own evidence — core/skills_media's pycaw session
    probe). Any failure = no evidence.

    NOTE (v6.2.x field fix, single-source guard):
    _audio_sessions() returns a (sessions, error) TUPLE —
    bool() on the tuple itself is always True, so the old
    form claimed evidence on every Windows box whether
    anything was sounding or not, and every "audio
    confirmed" claim built on it was vacuous. Unpack it: a
    session list is evidence; an error or an empty list is
    not.

    NOTE (R3): this deliberately stays "a session exists",
    NOT State-gated like _other_playback_evidence — it
    answers a different question ("is there anything to stop
    / anything at all behind a page that cannot speak"), and
    a just-starting player whose stream has not gone Active
    yet must still read as evidence here, or the stop key
    and the page-silent fallback would miss it."""
    try:
        from . import skills_media

        sessions, err = skills_media._audio_sessions()
        if err:
            return False
        return bool(sessions)
    except Exception:  # noqa: BLE001
        return False


# WASAPI session truth (R3 — the browser-name allowlist is
# GONE, replaced by what a session actually carries):
# a pycaw AudioSession (pycaw>=20230407, the tree's floor,
# and the current release — verified from both wheels) has
# exactly two facts the guard can use, and neither is a name:
#   * State — IAudioSessionControl::GetState: 0 Inactive
#     ("none of the streams in the session is currently
#     running"), 1 Active ("at least one stream is running"),
#     2 Expired ("contains no streams"). WASAPI has no pause
#     primitive — pausing stops the stream — so Active IS
#     "sounding now", and a lingerer after a page error reads
#     Inactive. (Microsoft Learn, AudioSessionState
#     enumeration.) State is the guard's real question
#     (sounding vs silent), which the name list only proxied.
#   * ProcessId — IAudioSessionControl2::GetProcessId: the
#     owning PID. System sounds is PID 0 (Process None) — a
#     PID fact, not a name.
# What the OS does NOT provide: any "is a browser" flag.
# DisplayName is empty unless the app sets it (Chrome does
# not); GroupingParam / Identifier are opaque GUIDs.
# REJECTED (in the module's reasoning, not just here): a
# PID-based "is this our attached Chrome" check — one WASAPI
# session covers a whole Chrome instance, so it cannot tell
# our dead tab's draining stream from another live tab in the
# same Chrome, and it would hide genuinely sounding other
# tabs and double-play, failing the explicit test that an
# unknown player counts even under a browser's name.
_AUDIO_SESSION_STATE_ACTIVE = 1


def _other_playback_evidence() -> bool:
    """True when some app holds a session that is SOUNDING
    right now — a competing music source (the Spotify app, a
    local player, another tab in the browser, any app's
    audio). Only sessions with a volume count (the app_volume
    "usable" definition), only sessions whose State reads
    Active count (Inactive/Expired sessions are silent
    lingerers, not music), and system sounds (ProcessId 0)
    do not count. NO process name is read anywhere: a
    sounding session counts whatever its name, and a silent
    one never counts whatever its name — so an unlisted
    browser, and a player hiding under a browser's name, are
    both classified by what the session IS. A session whose
    State or owner cannot be read is treated as competing
    (fail-closed for the guard: never start a second source
    over audio of unknown origin). Any probe failure reads
    as no evidence — the caller then judges on the page
    signal alone, never on a guess."""
    try:
        from . import skills_media

        sessions, err = skills_media._audio_sessions()
        if err or not sessions:
            return False
        for session in sessions:
            if getattr(session, "SimpleAudioVolume", None) is None:
                continue
            try:
                state = session.State
            except Exception:  # noqa: BLE001
                return True  # unreadable state: fail-closed
            try:
                sounding = int(state) == _AUDIO_SESSION_STATE_ACTIVE
            except Exception:  # noqa: BLE001
                return True  # unparseable state: fail-closed
            if not sounding:
                continue
            try:
                pid = session.ProcessId
            except Exception:  # noqa: BLE001
                return True  # unreadable owner: fail-closed
            try:
                if int(pid) == 0:
                    continue  # system sounds: PID 0, no process
            except Exception:  # noqa: BLE001
                return True  # unparseable owner: fail-closed
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------- continuity (F2)
#: Seconds after a confirmed browser-rung play when the one
#: continuity recheck fires. Long enough for a mid-stream cut
#: (the field failure landed at ~42 s; enforcement typically
#: lands in the first stretch), short enough that the user
#: still cares about the song.
_CONTINUITY_DELAY_S = 20.0

# The chat-announcement hook (fn(text) | None), registered by
# the GUI at startup — the watcher-event seam, the house road
# for proactive lines. None in headless/test use: _announce
# then simply has nowhere to go and drops the line.
_announce_hook = None


def set_announce_hook(fn) -> None:
    global _announce_hook
    _announce_hook = fn


def _announce(text: str) -> None:
    """One proactive chat line, through the registered hook.
    Never raises — an announcement must not break playback
    bookkeeping."""
    hook = _announce_hook
    if hook is None:
        return
    try:
        hook(text)
    except Exception:  # noqa: BLE001
        pass

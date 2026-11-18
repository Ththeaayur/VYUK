from __future__ import annotations

# INTENTIONAL (music_player split B8): verdicts read audio
# evidence through the evidence module; browser imports
# stay function-local (travel verbatim).
from .music_player_evidence import _playback_evidence

def _page_playback_state() -> str:
    """The browser layer's playback probe (core/browser.
    page_playback_state): "error" / "playing" / "paused" /
    "ended" / "no-video" / "unavailable". Any failure reads
    "unavailable" — the recheck then judges on audio evidence
    alone."""
    try:
        from . import browser

        return str(browser.page_playback_state())
    except Exception:  # noqa: BLE001
        return "unavailable"


def _page_playback_votes() -> dict:
    """The browser layer's BR-5 votes (core/browser.
    page_playback_votes): the in-page media-element vote and
    the MediaSession vote, or {} when the page cannot speak.
    Any failure reads {} — the callers then fall back to the
    legacy single-signal path, never to a fabricated vote."""
    try:
        from . import browser

        votes = browser.page_playback_votes()
        return votes if isinstance(votes, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def triangulate_playback(media_playing, media_session_playing,
                         audio_playing, *, player_error=False,
                         media_state=None) -> str:
    """BR-5 (v5.59): the 2-of-3 playback verdict. Votes are
    True / False / None (None = that signal has no vote to
    cast — no media element, MediaSession "none", audio probe
    failed). "playing" is claimed ONLY when at least two
    votes say playing: one signal, however loud, never claims
    it alone. A player-error flag, or a media element in its
    error state, is death regardless of the other votes.
    "paused" / "ended" come from the page's own media state
    (user-paused, track finished) when no two votes claim
    playing; anything else is "unknown" — and "unknown" never
    reads as a confirmed play. Pure function, no I/O, so the
    suite pins every vote combination deliberately."""
    if player_error or media_state == "error":
        return "error"
    true_votes = sum(1 for v in (media_playing,
                                 media_session_playing,
                                 audio_playing) if v is True)
    if true_votes >= 2:
        return "playing"
    if media_state == "paused":
        return "paused"
    if media_state == "ended":
        return "ended"
    return "unknown"


def mpv_ipc_verdict(ipc: dict) -> str:
    """BR-3 (v5.59): the mpv JSON IPC verification PATTERN —
    time-pos / pause / eof-reached as authoritative playback
    truth. This is pattern code only: no mpv binary is
    bundled, mpv is not a dependency, and nothing here shells
    out to mpv (the exact-build licence check, plan O-10, did
    not close — see core/playback_licences.py). Accepts
    both the IPC's own spellings ("time-pos", "pause",
    "eof-reached") and the snake_case aliases. eof-reached
    outranks pause (a finished track is ended, not paused);
    a present time-pos with pause clear is playing; a missing
    time-pos is "unknown", never a guessed play."""
    if not isinstance(ipc, dict):
        return "unknown"
    eof = ipc.get("eof_reached", ipc.get("eof-reached"))
    if eof:
        return "ended"
    paused = ipc.get("pause", ipc.get("paused"))
    if paused:
        return "paused"
    pos = ipc.get("time_pos", ipc.get("time-pos"))
    if pos is None:
        return "unknown"
    try:
        pos_f = float(pos)
    except (TypeError, ValueError):
        return "unknown"
    return "playing" if pos_f >= 0 else "unknown"


def playback_verdict_for_route(route: str, *, votes=None,
                               audio_playing=None,
                               ipc=None) -> str:
    """BR-5's collapse, in the pattern code (plan §5.4). On
    the browser route the verdict is the 2-of-3 triangulation.
    On the BR-2 -> BR-3 route (a yt-dlp stream URL fed to an
    external player over JSON IPC) the pattern COLLAPSES to
    the single IPC signal: there is no page to vote, and the
    audio session would only double-count the same player's
    output, so it is not counted as an independent vote. Any
    other route name is "unknown" — the collapse is explicit,
    never a silent default to one signal."""
    if route == "ipc":
        return mpv_ipc_verdict(ipc or {})
    if route == "browser":
        votes = votes or {}
        return triangulate_playback(
            votes.get("media_playing"),
            votes.get("media_session_playing"),
            audio_playing,
            player_error=bool(votes.get("player_error")),
            media_state=votes.get("media_state"))
    return "unknown"


def _browser_playback_confirmed() -> bool:
    """A browser rung's BR-5 confirmation: "playing" on the
    2-of-3 triangulation of the page's votes plus the audio
    session. When the page cannot speak at all (votes {}),
    the v5.53 fallback stands — audio evidence alone decides
    — because a silent page is a missing signal, not a vote
    against playback. Never claims on one vote alone when
    the page does speak."""
    votes = _page_playback_votes()
    if not votes:
        return _playback_evidence()
    verdict = triangulate_playback(
        votes.get("media_playing"),
        votes.get("media_session_playing"),
        _playback_evidence(),
        player_error=bool(votes.get("player_error")),
        media_state=votes.get("media_state"))
    return verdict == "playing"


def _stream_alive() -> bool:
    """The recheck's verdict. BR-5 first, legacy second.
    When the page speaks in votes: a player-error state is
    death even if a render session lingers; a verdict of
    playing / paused / ended is alive (paused/ended BY THE
    USER or the track itself is not a cut); an "unknown"
    verdict falls back to audio evidence. Only when the page
    cannot speak at all does the legacy single-signal path
    decide (error = death, playing/paused/ended = alive,
    otherwise audio evidence alone)."""
    votes = _page_playback_votes()
    if votes:
        if votes.get("player_error") or \
                votes.get("media_state") == "error":
            return False
        verdict = triangulate_playback(
            votes.get("media_playing"),
            votes.get("media_session_playing"),
            _playback_evidence(),
            player_error=bool(votes.get("player_error")),
            media_state=votes.get("media_state"))
        if verdict in ("playing", "paused", "ended"):
            return True
        if verdict == "error":
            return False
        return _playback_evidence()
    state = _page_playback_state()
    if state == "error":
        return False
    if state in ("playing", "paused", "ended"):
        return True
    return _playback_evidence()

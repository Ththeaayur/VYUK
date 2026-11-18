from __future__ import annotations

import os
from . import config_spec
import re
import threading
from urllib.parse import quote

# INTENTIONAL (music_player split B8): the rungs read
# settle/evidence/announce from evidence, verdicts from
# verdicts, and the ladder helpers from spotify. The
# continuity pair lives here (not in evidence) so the
# dependency graph stays acyclic: _continuity_recheck
# calls _web_rung/_local_rung, while the rungs call
# _settle/_playback_evidence/_announce.
from .music_player_evidence import (
    _CONTINUITY_DELAY_S,
    _announce,
    _other_playback_evidence,
    _playback_evidence,
    _settle,
)
from .music_player_spotify import (
    _launch_spotify,
    _local_tracks,
    _media_play,
    _open_local,
    _spotify_installed,
    _spotify_web_play,
    _stop_current_playback,
    _ytm_play,
)
from .music_player_verdicts import (
    _browser_playback_confirmed,
    _page_playback_state,
    _stream_alive,
)

# ------------------------------------------------- continuity guard
# Single-source guard (v6.2.x field fix): the ONE pending
# recheck lives here with a generation token. A new
# play_music() cancels it (a stale timer must never start a
# second source after the user asked for something else),
# and a timer that fires after its generation was superseded
# ignores itself. The lock guards the handle/token pair —
# the timer fires on its own thread while play_music runs
# on the funnel thread.
_continuity_lock = threading.Lock()
_continuity_timer = None
_continuity_token = 0


def _cancel_continuity() -> None:
    """Drop any pending continuity recheck and retire its
    generation. Never raises; safe when nothing is armed."""
    global _continuity_timer, _continuity_token
    with _continuity_lock:
        timer = _continuity_timer
        _continuity_timer = None
        _continuity_token += 1
    if timer is not None:
        try:
            timer.cancel()
        except Exception:  # noqa: BLE001
            pass


def _continuity_current(token) -> bool:
    """True when *token* is still the live generation (None
    means the caller carries no generation — legacy direct
    calls — which always run)."""
    if token is None:
        return True
    with _continuity_lock:
        return token == _continuity_token


def _schedule_continuity(rung: str, query: str) -> None:
    """Arm the ONE recheck for a just-confirmed browser rung.
    Replaces any pending recheck: only the newest play owns
    a timer. Non-blocking (daemon Timer); any failure to arm
    is silent — the play already succeeded on its own
    evidence."""
    global _continuity_timer, _continuity_token
    with _continuity_lock:
        old = _continuity_timer
        _continuity_token += 1
        token = _continuity_token
        timer = threading.Timer(
            _CONTINUITY_DELAY_S, _continuity_recheck,
            args=(rung, query, token))
        timer.daemon = True
        _continuity_timer = timer
    if old is not None:
        try:
            old.cancel()
        except Exception:  # noqa: BLE001
            pass
    try:
        timer.start()
    except Exception:  # noqa: BLE001
        pass


def _continuity_recheck(rung: str, query: str, token=None) -> None:
    """~20 s after a confirmed browser play: is the stream
    still alive? Silence when it is. When it died: announce
    the cut in chat, then attempt the NEXT ladder rung(s)
    once and announce their outcome. Never raises, never
    chains (fall-through plays arm no further recheck).

    Single-source guard: the recheck NEVER starts a second
    rung while another source is still sounding (a stale
    generation ignores itself, and live non-browser playback
    evidence blocks the fall-through outright)."""
    # Stale generation: a newer play superseded this timer —
    # it must not start anything.
    if not _continuity_current(token):
        return
    try:
        if _stream_alive():
            return
        # The browser stream died, but a NON-browser source
        # is still sounding (the Spotify app, a local player)
        # — starting another rung would put two sources on at
        # once. Say so and start nothing.
        if _other_playback_evidence():
            _announce(
                "The music stream was cut off, sir, but "
                "something else is still playing, so I "
                "didn't start another source.")
            return
        # A newer play may have started while this recheck
        # was judging: re-check the generation immediately
        # before starting anything.
        if not _continuity_current(token):
            return
        # v5.54.1: a player-error death leaves a diagnostic
        # line (playback state + online flag + URL + title at
        # death) for the 42 s investigation — audio-only
        # deaths log nothing, the page has nothing to say.
        try:
            if _page_playback_state() == "error":
                from . import browser as _browser_mod

                _browser_mod.log_playback_death()
        except Exception:  # noqa: BLE001 — diagnostics never
            pass  # break the recheck
        tried: list[str] = []
        outcome = None
        if rung == "ytm":
            outcome = _web_rung(query, tried)
        if outcome is None:
            outcome = _local_rung(query, tried)
        if outcome is not None and \
                outcome.startswith("needs-user-login:"):
            outcome = outcome.split(":", 1)[1].strip()
        if outcome is not None:
            _announce(
                f"The music stream was cut off, sir. {outcome}")
        elif tried:
            _announce(
                "The music stream was cut off, sir, and I "
                "couldn't restart it another way ("
                + "; ".join(tried) + ").")
        else:
            _announce(
                "The music stream was cut off, sir, and there "
                "was no other way to restart it.")
    except Exception:  # noqa: BLE001 — a recheck must never
        pass  # surface as a crash in a daemon thread


# ------------------------------------------------------------ BR-2 rung
# v5.59 (Wave 4, BR-2): yt-dlp as a PIP LIBRARY rung — a
# stream-URL extraction rung in the play_music ladder,
# bypassing the web player. Hard shape, all of it load-bearing:
#   * LIBRARY ONLY: `from yt_dlp import YoutubeDL`, lazily,
#     inside _ytdlp_extract. Never a CLI subprocess, never
#     the stock PyInstaller .exe (its bundled GPLv3+ code is
#     why the .exe is never redistributed or invoked — see
#     core/playback_licences.py). yt-dlp is NOT in
#     requirements.txt: it is lazy-imported, optional, and
#     the shipped default path never imports it.
#   * PINNED: _YTDLP_PINNED is the version this rung's option
#     set was written against; any other installed version
#     (or an unreadable one) makes the rung unavailable
#     rather than running unreviewed extractor code.
#   * SELF-UPDATE OFF: self-update lives in yt-dlp's CLI /
#     update module, which this rung never imports or calls
#     — extraction through YoutubeDL cannot self-update,
#     and the rung passes no update option anywhere.
#   * EXTRACTION / STREAMING ONLY: extract_info(...,
#     download=False) plus skip_download — no file is ever
#     written, no copyrighted audio is ever cached.
#   * GATED OFF BY DEFAULT: _ytdlp_enabled() is False unless
#     the operator explicitly opts in (setter, or the
#     NOVA_YTDLP_STREAM=1 environment flag). The site-ToS
#     posture (plan D-7) is UNRULED, so with the flag off the
#     rung is not even in the ladder order and browser
#     playback with BR-5 honesty stays the active default.
#   * PLAYBACK of an extracted URL goes through her
#     controllable browser (goto the stream URL), so the BR-5
#     triangulation still governs the claim — the rung does
#     not invent its own confirmation, and it never hands a
#     URL to an unlicensed player.
_YTDLP_PINNED = "2026.08.19"
_YTDLP_STREAM_ENABLED = False
_YTDLP_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
}


def set_ytdlp_stream_enabled(enabled: bool) -> None:
    """Explicit operator opt-in for the BR-2 rung (off by
    default; the shipped default path never calls this)."""
    global _YTDLP_STREAM_ENABLED
    _YTDLP_STREAM_ENABLED = bool(enabled)


def _ytdlp_enabled() -> bool:
    """True only on explicit opt-in: the setter above, or
    NOVA_YTDLP_STREAM=1 in the environment. Anything else —
    including a bare import — is OFF."""
    return _YTDLP_STREAM_ENABLED or \
        config_spec.env("NOVA_YTDLP_STREAM") == "1"


def _ytdlp_extract(query: str):
    """(stream_url, title) for `query` via the yt-dlp
    LIBRARY, or None when anything is missing, mismatched,
    or fails: no library installed, version not the pinned
    one, no stream URL in the result, any extractor error.
    Never downloads (download=False + skip_download), never
    writes a file, never shells out. The seams are
    module-level only through sys.modules, so the suite fakes
    the library without touching the real machine."""
    if not (query or "").strip():
        return None
    try:
        import yt_dlp
        try:
            from yt_dlp import version as _yt_version

            ver = getattr(_yt_version, "__version__", None)
        except Exception:  # noqa: BLE001
            ver = getattr(yt_dlp, "__version__", None)
    except Exception:  # noqa: BLE001 — library absent: rung
        return None  # unavailable, not an error to surface
    if ver != _YTDLP_PINNED:
        return None
    try:
        ydl = yt_dlp.YoutubeDL(dict(_YTDLP_OPTIONS))
        with ydl:
            info = ydl.extract_info("ytsearch1:" + query.strip(),
                                    download=False)
        entry = info
        if isinstance(info, dict) and info.get("entries"):
            entry = info["entries"][0]
        if not isinstance(entry, dict):
            return None
        url = entry.get("url")
        if not url:
            return None
        return (url, entry.get("title") or query.strip())
    except Exception:  # noqa: BLE001 — extractor failure is
        return None  # a fall-through reason, never a crash


def _ytdlp_rung(q: str, tried: list, schedule: bool = False):
    """The BR-2 rung. The rung's final text, or None with the
    reason appended to *tried*. With the flag off this rung
    is never placed in the ladder order (play_music) — a
    direct call still refuses, so the gate cannot be bypassed
    by calling the rung instead of the ladder. *schedule*
    arms the continuity recheck on a confirmed play (the tool
    path only); the recheck's fall-through for this rung is
    the local rung (it never chains)."""
    if not _ytdlp_enabled():
        tried.append("yt-dlp stream rung is off (default)")
        return None
    found = _ytdlp_extract(q)
    if found is None:
        tried.append(
            "yt-dlp stream extraction unavailable (library "
            "missing, version not pinned, or no stream found)")
        return None
    url, title = found
    try:
        from . import browser

        opened = browser.browser_control("goto", url=url)
    except Exception:  # noqa: BLE001
        opened = None
    if not isinstance(opened, str) or \
            opened.startswith(("Error", "Couldn't",
                               "needs-user-login")):
        tried.append(
            "yt-dlp found a stream but the browser couldn't "
            "open it")
        return None
    _settle()
    if _browser_playback_confirmed():
        if schedule:
            _schedule_continuity("ytdlp", q)
        return (f"Playing '{title}' via a yt-dlp stream "
                "(playback confirmed).")
    return (f"Opened the yt-dlp stream for '{q}'; couldn't "
            "confirm playback started.")


# ------------------------------------------------------------ the tool
# v5.54.1 (field fix): source qualifiers. The field failure was
# "Believer on youtube" playing on Spotify: the handler passed
# the query verbatim and the ladder ran availability-ordered,
# so the Spotify app rung won before anyone read the "on
# youtube". The qualifier is now parsed at play_music entry
# (case-insensitive), the matching rung runs FIRST, the
# qualifier is stripped from the search string (so the spoken
# result never echoes "on youtube" back), and the ladder order
# is otherwise unchanged. "youtube music" matches before
# "youtube" so the strip leaves no stray "music" behind.
_QUALIFIER_PATTERNS = (
    ("ytm", (r"\bon\s+youtube\s+music\b", r"\bon\s+youtube\b")),
    ("spotify", (r"\bon\s+spotify\b",)),
)


def _parse_source_qualifier(q: str):
    """(source, cleaned query) for a source qualifier in `q`,
    or (None, q) when there is none. Matching is
    case-insensitive with word boundaries (so "button
    youtube" never reads as a qualifier) and tolerates
    irregular spacing; the qualifier phrase is removed and
    the remaining words re-joined with single spaces."""
    for source, patterns in _QUALIFIER_PATTERNS:
        for pattern in patterns:
            m = re.search(pattern, q, re.IGNORECASE)
            if m is None:
                continue
            cleaned = re.sub(
                r"\s+", " ", (q[:m.start()] + " " + q[m.end():])
            ).strip()
            return source, cleaned
    return None, q


def _spotify_app_rung(q: str, tried: list):
    """The Spotify desktop app rung, via its URI protocol.
    The rung's final text, or None with the reason appended to
    *tried*. Extracted v5.54.1 so the qualifier-ordered ladder
    runs the SAME rung, not a copy."""
    if not _spotify_installed():
        tried.append("no Spotify app installed")
        return None
    if q:
        if _launch_spotify("spotify:search:" + quote(q)):
            _settle()
            if _playback_evidence():
                return (f"Playing in the Spotify app: '{q}' "
                        "(audio confirmed).")
            return (f"Opened the search in Spotify for "
                    f"'{q}'; couldn't confirm playback "
                    "started.")
        tried.append("the Spotify app wouldn't open")
        return None
    if _launch_spotify("spotify:"):
        _settle(0.6)
        _media_play()
        _settle()
        if _playback_evidence():
            return ("Spotify is playing in the app "
                    "(audio confirmed).")
        return ("Opened Spotify and pressed play; "
                "couldn't confirm audio started.")
    tried.append("the Spotify app wouldn't open")
    return None


def _ytm_rung(q: str, tried: list, schedule: bool = False):
    """The YouTube Music rung (plays logged-out). The rung's
    final text, a needs-user-login terminal string, or None
    with the reason appended to *tried*. Extracted v5.54.1
    like _spotify_app_rung; *schedule* arms the continuity
    recheck on a confirmed play (the tool path only)."""
    ytm = _ytm_play(q)
    if isinstance(ytm, str) and ytm.startswith("needs-user-login"):
        return ytm  # terminal: names the unlock, no retry
    if ytm:
        _settle()
        # v5.59 (BR-5): a browser rung is confirmed by the
        # 2-of-3 triangulation, not by the audio session alone.
        if _browser_playback_confirmed():
            # v5.53 (F2): confirmed is only "started" — arm
            # the one continuity recheck before claiming it.
            if schedule:
                _schedule_continuity("ytm", q)
            # v5.59 (BR-5): the claim is triangulated now, so
            # the wording says so — "(audio confirmed)" would
            # misdescribe a media+MediaSession confirmation
            # made while the audio session read quiet.
            return (f"Playing on YouTube Music: '{q}' "
                    "(playback confirmed)." if q else
                    "Playing on YouTube Music (playback confirmed).")
        return (f"Opened YouTube Music in my browser and "
                f"started the first result for '{q}'; couldn't "
                "confirm audio." if q else
                "Opened YouTube Music in my browser; couldn't "
                "confirm audio.")
    tried.append("YouTube Music didn't play in my browser")
    return None


def _web_rung(q: str, tried: list, schedule: bool = False):
    """The Spotify web rung, through his attached browser.
    Returns the rung's final text (a confirmed play claim,
    the unconfirmed honesty line, or the login terminal
    string), or None when the rung produced no outcome —
    the browser refused (not enabled / not attached — the
    rung never auto-enables, Fix 3) or the track click never
    landed (reason appended to *tried*).

    REVIVED R3: the v5.70 session probe (always False) is
    deleted, not worked around. The probe existed to avoid attempting a login-walled SPA, and it
    read VYUK's own browser profile for a session cookie —
    a profile that no longer exists, and a read that must
    never be repointed at his real Chrome cookie store. No
    pre-check is needed any more, because the wall is
    OBSERVED, not assumed: the drive goes through his
    attached Chrome (Fix 3 explicit enable), and the browser
    layer's own login-wall probe (a visible password field,
    _login_wall_result) returns the needs-user-login terminal
    string at the click when he is not signed in — no cookie
    reading anywhere in this path. The "owner's ruling" the
    Fix 7 audit awaited was for a cookie-store session
    check; this revival does not use one, so no new ruling
    is needed — the consent it uses is the one he already
    gave (Fix 3 enable + the YTM rung, which has played
    through his attached Chrome as the shipped default
    since v5.57.1). FIELD NOTE, not a lab claim: full
    tracks need HIS Spotify login in his Chrome; without
    it the rung can at most play previews, and the BR-5
    triangulation confirms only what actually sounded.
    Extracted in v5.53 (F2) so the continuity recheck's
    fall-through runs the SAME rung, not a copy; *schedule*
    arms the recheck on a confirmed play (the tool path
    only — a fall-through play never chains another
    recheck)."""
    web = _spotify_web_play(q)
    if isinstance(web, str) and \
            web.startswith("needs-user-login"):
        return web
    if web:
        _settle()
        # v5.59 (BR-5): same triangulation as the YTM rung.
        if _browser_playback_confirmed():
            if schedule:
                _schedule_continuity("spotify-web", q)
            # v5.59 (BR-5): triangulated wording, as above.
            return (f"Playing on Spotify in my browser: "
                    f"'{q}' (playback confirmed)." if q else
                    "Playing on Spotify in my browser "
                    "(playback confirmed).")
        return ("Opened Spotify in my browser and started "
                "the first result; couldn't confirm "
                "playback started.")
    tried.append("Spotify web didn't play")
    return None


def _local_rung(q: str, tried: list):
    """The local-files rung, in the Music folder. The rung's
    final text, or None with the reason appended to *tried*.
    Extracted in v5.53 (F2) for the recheck's fall-through,
    like _web_rung."""
    tracks = _local_tracks()
    if tracks:
        pick = tracks[0]
        if q:
            named = [t for t in tracks
                     if q.lower() in t.stem.lower()]
            if named:
                pick = named[0]
        if _open_local(pick):
            _settle()
            if _playback_evidence():
                return (f"Playing '{pick.name}' from your "
                        "Music folder (audio confirmed).")
            return (f"Opened '{pick.name}' in your default "
                    "player; couldn't confirm audio.")
        tried.append("the local player wouldn't open")
    else:
        tried.append("no audio files in your Music folder")
    return None


def play_music(query: str = "") -> str:
    """Start music. Ladder: YouTube Music -> Spotify app ->
    (Spotify web, through his attached browser — full
    tracks need his Spotify login in Chrome) -> local
    files. v5.59 (BR-2, gated OFF by default): when the
    operator explicitly enables the yt-dlp stream rung it
    sits directly behind YouTube Music in the order above;
    when off (the shipped default) the ladder is unchanged.
    v5.57.1 (owner scope change): YouTube Music is the
    DEFAULT — a bare request (no qualifier) tries it first, so
    the field's "defaulted to Spotify" report is answered by
    design, not by a workaround. v5.54.1: a source qualifier
    ("on youtube" / "on spotify") moves its rung FIRST and is
    stripped from the search string; without one the order
    above is unchanged. v6.2.x (single-source guard): a new
    play cancels any pending continuity recheck and stops
    whatever is playing first, so only one source ever
    sounds at a time."""
    q = (query or "").strip()
    source, q = _parse_source_qualifier(q)
    tried: list[str] = []

    # Single-source guard: a stale recheck from the previous
    # play must never fire afterwards, and whatever is
    # playing now stops before the new source starts.
    _cancel_continuity()
    stopped_old = _stop_current_playback()

    # Rung order with the qualifier's rung first; the rest keep
    # their ladder order behind it. A "spotify" qualifier puts
    # BOTH Spotify rungs (app, then web) ahead of YouTube Music.
    # v5.57.1: the no-qualifier default is (ytm, app, web,
    # local) — YouTube Music first. Qualifier orders unchanged.
    if source == "ytm":
        order = ("ytm", "app", "web", "local")
    elif source == "spotify":
        order = ("app", "web", "ytm", "local")
    else:
        order = ("ytm", "app", "web", "local")
    # v5.59 (BR-2): the yt-dlp stream rung joins the ladder
    # ONLY when explicitly enabled — directly behind the
    # YouTube Music rung, ahead of the rest — and is absent
    # from the order entirely when off (the shipped default),
    # so the default ladder is byte-for-byte the v5.57.1 one.
    if _ytdlp_enabled():
        order = list(order)
        order.insert(order.index("ytm") + 1, "ytdlp")
        order = tuple(order)
    for rung in order:
        if rung == "app":
            out = _spotify_app_rung(q, tried)
        elif rung == "ytm":
            out = _ytm_rung(q, tried, schedule=True)
        elif rung == "ytdlp":
            out = _ytdlp_rung(q, tried, schedule=True)
        elif rung == "web":
            out = _web_rung(q, tried, schedule=True)
        else:
            out = _local_rung(q, tried)
        if out is not None:
            return out

    failed = ("I couldn't start any music, sir — I tried: "
              + "; ".join(tried) + ".")
    if stopped_old:
        # Honest wording: the old source was stopped by the
        # guard above, so the user is not left wondering
        # where their music went.
        failed += " The previous playback was stopped."
    return failed

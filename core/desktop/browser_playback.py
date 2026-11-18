"""browser_playback concern extracted from core/browser.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/browser."""

from .browser_session import (_active_page, _log_browser_event, _need_connection, _submit)  # GAPS-18: narrowed to the 4 used names


_PLAYBACK_PROBE_JS = """() => {
  const bodyText = document.body ? (document.body.innerText || '') : '';
  const playerError = /something went wrong/i.test(bodyText);
  const v = document.querySelector('video');
  if (!v) return {video: false, playerError: playerError};
  return {video: true, playerError: playerError,
          paused: !!v.paused, ended: !!v.ended,
          mediaError: v.error ? v.error.code : 0};
}"""


def _impl_playback_state() -> str:
    """One of "error" / "playing" / "paused" / "ended" /
    "no-video" / "unavailable", read from the live page.
    Runs on the browser worker thread (page_playback_state
    submits it like every other page access)."""
    if _need_connection() is not None:
        return "unavailable"
    page = _active_page()
    if page is None:
        return "unavailable"
    try:
        raw = page.evaluate(_PLAYBACK_PROBE_JS)
    except Exception:  # noqa: BLE001
        return "unavailable"
    if not isinstance(raw, dict):
        return "unavailable"
    if raw.get("playerError") or raw.get("mediaError"):
        return "error"
    if not raw.get("video"):
        return "no-video"
    if raw.get("ended"):
        return "ended"
    return "paused" if raw.get("paused") else "playing"


def page_playback_state() -> str:
    """Public playback probe for core/music_player (v5.53,
    F2): the vocabulary of _impl_playback_state, with ANY
    failure (no session, wedged worker, bad page) reported
    as "unavailable" — the caller then falls back to
    audio-session evidence alone."""
    try:
        out = _submit(lambda: _impl_playback_state(), timeout=10,
                      kind="probe")
    except Exception:  # noqa: BLE001
        return "unavailable"
    return out if out in ("error", "playing", "paused", "ended",
                          "no-video") else "unavailable"


_PLAYBACK_TRIANGULATION_JS = """() => {
  const bodyText = document.body ? (document.body.innerText || '') : '';
  const playerError = /something went wrong/i.test(bodyText);
  const els = Array.from(document.querySelectorAll('video, audio'));
  let ms = 'none';
  try {
    if (navigator.mediaSession && navigator.mediaSession.playbackState)
      ms = navigator.mediaSession.playbackState;
  } catch (e) { ms = 'none'; }
  let el = null;
  for (const e of els) {
    if (!e.paused && !e.ended && !e.error) { el = e; break; }
  }
  if (!el && els.length) el = els[0];
  if (!el) return {hasMedia: false, playerError: playerError,
                   mediaSession: ms};
  const state = el.error ? 'error' : (el.ended ? 'ended'
                       : (el.paused ? 'paused' : 'playing'));
  return {hasMedia: true, playerError: playerError,
          mediaSession: ms, mediaState: state,
          mediaPlaying: (state === 'playing')};
}"""


def _impl_playback_votes() -> dict:
    """The two in-page BR-5 votes, as a dict. Runs on the
    browser worker thread like _impl_playback_state. {} when
    nothing could be read (no session, no page, probe failure)
    — the caller then falls back to the legacy single-signal
    path, never to a fabricated vote. Keys: "media_playing"
    (True / False / None — None when the page has no media
    element at all), "media_state" ("playing" / "paused" /
    "ended" / "error" / "no-media" / "unknown"),
    "media_session_playing" (True when MediaSession says
    "playing", False when it says "paused", None otherwise),
    "media_session_state" (the raw playbackState string),
    "player_error" (the player-error card / media error flag),
    "has_media"."""
    if _need_connection() is not None:
        return {}
    page = _active_page()
    if page is None:
        return {}
    try:
        raw = page.evaluate(_PLAYBACK_TRIANGULATION_JS)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(raw, dict):
        return {}
    session_state = raw.get("mediaSession") or "none"
    if session_state == "playing":
        session_vote = True
    elif session_state == "paused":
        session_vote = False
    else:
        session_vote = None
    if not raw.get("hasMedia"):
        media_vote = None
        media_state = "no-media"
    else:
        media_state = raw.get("mediaState") or "unknown"
        media_vote = True if media_state == "playing" else \
            False if media_state in ("paused", "ended", "error") \
            else None
    return {
        "media_playing": media_vote,
        "media_state": media_state,
        "media_session_playing": session_vote,
        "media_session_state": session_state,
        "player_error": bool(raw.get("playerError")),
        "has_media": bool(raw.get("hasMedia")),
    }


def page_playback_votes() -> dict:
    """Public BR-5 probe for core/music_player (v5.59): the
    two in-page votes from _impl_playback_votes, with ANY
    failure (no session, wedged worker, bad page, timed-out
    probe job) reported as {} — the caller then falls back
    to the legacy path, never to an invented vote."""
    try:
        out = _submit(lambda: _impl_playback_votes(), timeout=10,
                      kind="probe")
    except Exception:  # noqa: BLE001
        return {}
    return out if isinstance(out, dict) else {}


def _impl_playback_death() -> str:
    """One structured line about a dead playback, for the
    42 s investigation (v5.54.1): the page's own playback
    state, the browser's online flag, and the page URL +
    title — the three facts that separate "YouTube flagged
    her browser" from "the network died under her" from
    "the page itself broke". Runs on the browser worker
    thread like every other page access. "" when nothing
    could be read."""
    if _need_connection() is not None:
        return ""
    page = _active_page()
    if page is None:
        return ""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    try:
        title = page.title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    try:
        online = page.evaluate("navigator.onLine")
    except Exception:  # noqa: BLE001
        online = "unknown"
    state = _impl_playback_state()
    line = (f"playback death: state={state} online={online} "
            f"url={url} title={title}")
    _log_browser_event(line)
    return line


def log_playback_death() -> str:
    """Public playback-death diagnostic (v5.54.1): logs the
    _impl_playback_death line and returns it. Never raises;
    "" when no session or nothing could be read. Called by
    the music continuity recheck when the page itself reports
    the player-error state — not on audio-only deaths, where
    the page has nothing to say."""
    try:
        out = _submit(lambda: _impl_playback_death(), timeout=10,
                      kind="probe")
    except Exception:  # noqa: BLE001
        return ""
    return out if isinstance(out, str) else ""

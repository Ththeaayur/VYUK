from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, quote_plus

# INTENTIONAL (music_player split B8): _local_tracks reads
# the shared audio-extension set from the evidence module;
# browser/control imports stay function-local (verbatim).
from .music_player_evidence import _AUDIO_EXTS

# ------------------------------------------------------------ rung 1
def _spotify_installed() -> bool:
    if sys.platform != "win32":
        return False
    if shutil.which("Spotify.exe") or shutil.which("spotify"):
        return True
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft"
        / "WindowsApps" / "Spotify.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Spotify"
        / "Spotify.exe",
    ]
    return any(p.is_file() for p in candidates if str(p.parent) != ".")


def _launch_spotify(uri: str) -> bool:
    """App-protocol launch semantics (v5.46 discipline: a
    launch, not a navigation)."""
    try:
        os.startfile(uri)  # type: ignore[attr-defined]  # Windows
        return True
    except Exception:  # noqa: BLE001
        return False


def _media_play() -> None:
    try:
        from .desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        control.media_control("play_pause")
    except Exception:  # noqa: BLE001
        pass


def _stop_current_playback() -> bool:
    """Best-effort stop of whatever is playing now (the
    single-source guard, v6.2.x field fix): sends the media
    stop key, which reaches the active media session
    whatever the source (Spotify app, browser MediaSession,
    local player). Only sends when an audio session exists,
    so a quiet machine gets no stray key; never raises.
    Returns True when a stop was sent. Never kills an app
    or closes a tab — the source is stopped, not removed,
    and the new play starts afterwards."""
    try:
        from .music_player_evidence import _playback_evidence

        if not _playback_evidence():
            return False
        # Canonical seam (control_system defines
        # media_control; core.desktop.control only re-exports
        # it): read it fresh here so a test patch on the
        # defining module intercepts the call — patching the
        # re-export hub would not (dual-binding trap).
        from .desktop.control_system import media_control

        media_control("stop")
        return True
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------ rung 2/3
# v5.57.1 (item 5): the YouTube Music rung used to goto the
# search page and click the first result immediately — racing
# page render, so a slow render read as a failed rung. It now
# waits, bounded, for results to render, and dismisses a
# consent/cookie dialog first when one is present. A click
# that never lands returns None (fallthrough to the next rung)
# instead of claiming an unconfirmed play on a page where
# nothing was clicked.
_YTM_RESULT_SELECTORS = "ytmusic-card a, a.ytmusic-card, #thumbnail"
_YTM_WAIT_ATTEMPTS = 6
_YTM_WAIT_SECONDS = 1.0
# Consent buttons, privacy-first order: rejecting keeps the
# least tracking; accepting is the fallback when no Reject
# button is offered. Text-matched (markup-independent).
_YTM_CONSENT_TEXTS = ("Reject all", "Accept all")


def _ytm_dismiss_consent(browser) -> None:
    """Best-effort consent-dialog dismissal. Any outcome other
    than a click is ignored: absence is the common case, a
    no-guess multi-match answer must never block the rung, and
    a login wall is the main click's terminal to report."""
    for text in _YTM_CONSENT_TEXTS:
        try:
            clicked = browser.browser_click(text, by="text")
        except Exception:  # noqa: BLE001 — dismissal is best-effort
            return
        if isinstance(clicked, str) and clicked.startswith("Clicked"):
            return


def _ytm_play(query: str) -> str | None:
    """YouTube Music via her controllable browser. Returns
    the result text, or None when the rung is unavailable
    (browser layer refused — e.g. it could not start — or the
    result click never landed despite a bounded render wait)."""
    try:
        from . import browser

        url = ("https://music.youtube.com/search?q="
               + quote_plus(query)) if query else \
            "https://music.youtube.com/"
        opened = browser.browser_control("goto", url=url)
        if not isinstance(opened, str) or \
                opened.startswith(("Error", "needs-user-login")):
            return None
        if opened.startswith("Couldn't"):
            return None
        _ytm_dismiss_consent(browser)
        landed = False
        for _attempt in range(_YTM_WAIT_ATTEMPTS):
            clicked = browser.browser_click(
                _YTM_RESULT_SELECTORS, by="css")
            if isinstance(clicked, str) and \
                    clicked.startswith("needs-user-login"):
                return clicked
            if isinstance(clicked, str) and clicked.startswith("Clicked"):
                landed = True
                break
            # Results not rendered yet (or the click missed):
            # one bounded beat, then retry — never an unbounded
            # wait, and every browser job carries its own budget
            # behind _submit.
            try:
                browser.browser_control("wait",
                                        seconds=_YTM_WAIT_SECONDS)
            except Exception:  # noqa: BLE001 — the retry stands
                pass
        if not landed:
            return None
        return "ytm"
    except Exception:  # noqa: BLE001
        return None


def _spotify_web_play(query: str) -> str | None:
    """Spotify web player via her browser. Returns the result
    text, a needs-user-login terminal string, or None when
    unavailable.

    R3 (web-rung revival): the track click must LAND
    (startswith "Clicked", mirroring _ytm_play) — a missed
    click returns None instead of "spotify-web". The old form
    returned "spotify-web" on any click outcome, which hid a
    latent false claim: with no browser attached, goto and
    click both refuse ("No controllable browser session is
    active." — caught by NEITHER of the goto prefixes below,
    "Error"/"Couldn't"), and the rung's triangulation would
    then fall back to audio evidence alone, so a lingering
    session could read as a confirmed Spotify play that never
    happened. The landed-click requirement closes that hole:
    no attach, no click, no claim."""
    try:
        from . import browser

        url = ("https://open.spotify.com/search/" + quote(query)) \
            if query else "https://open.spotify.com/"
        opened = browser.browser_control("goto", url=url)
        if not isinstance(opened, str) or \
                opened.startswith(("Error", "Couldn't")):
            return None
        if opened.startswith("needs-user-login"):
            return opened
        clicked = browser.browser_click(
            '[data-testid="tracklist-row"], '
            '[data-testid="card"] a', by="css")
        if isinstance(clicked, str) and \
                clicked.startswith("needs-user-login"):
            return clicked
        if isinstance(clicked, str) and \
                clicked.startswith("Clicked"):
            return "spotify-web"
        return None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------ rung 4
def _local_tracks() -> list[Path]:
    music = Path.home() / "Music"
    if not music.is_dir():
        return []
    return sorted(
        p for p in music.rglob("*")
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS)


# T11 (field check 2026-11-17): the opener is a launcher, not the
# player — it normally exits at once (0 = handed off) or fails
# fast (non-zero = no handler; xdg-open exits 3 when no default
# audio player is registered). A launcher that execs the player
# stays alive instead, so still-running after this bounded wait
# also counts as opened. Waiting at most this long keeps a
# failing open from ever reading as a success.
_OPEN_LOCAL_WAIT_SECONDS = 2.0


def _open_local(path: Path) -> bool:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            proc = subprocess.Popen(
                ["open", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc = subprocess.Popen(
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            return proc.wait(timeout=_OPEN_LOCAL_WAIT_SECONDS) == 0
        except subprocess.TimeoutExpired:
            return True
    except Exception:  # noqa: BLE001
        return False

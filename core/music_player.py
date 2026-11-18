"""play_music (v5.52, item 4 + its 23:09 extension).

THE GAP: until now NO tool could start music — media_control
sends keys to whatever is (not) playing, compose_song is a
synth toy, music_trivia a gimmick. "Play music" did nothing,
and the brain narrated its toolbox hunt as the reply (the
deliberation guard, core/deliberation.py, covers that half).

THE LADDER (extension re-aim, user field message 2026-10-23
23:09 — the Spotify DESKTOP app is driven through the
spotify: URI protocol as an app LAUNCH, never the browser;
Spotify WEB is demoted to a conditional rung because it is a
login-walled SPA, while YouTube plays logged-out. v5.57.1
(owner scope change): YouTube Music is rung 1 by default,
Spotify app rung 2):

  1. YouTube Music in her controllable browser: search page
     (or home for a bare request), first result started.
  2. Spotify app (if installed): spotify:search:<query> for
     a request; bare request opens the app and sends the play
     key to resume the user's context.
  3. Spotify web — ONLY when her persistent browser profile
     already holds a Spotify session (sp_dc cookie probe,
     read-only; any doubt = no session).
  4. Local audio files in the user's Music folder, via the
     default player.

SINGLE SOURCE (v6.2.x field fix): a new play stops
whatever is playing first and cancels any pending
continuity recheck, so only one source ever sounds at a
time; the recheck never restarts over live audio from
another source.

VERIFICATION HONESTY (H5-style): after each rung acts, the
audio-session evidence (core/skills_media._audio_sessions —
an active render session) is probed. A rung that cannot be
verified says so in its own words ("opened the search in
Spotify; couldn't confirm playback started") rather than
claiming success. Bare request with nothing available
anywhere returns an honest failure naming what was tried.

The seams (_spotify_installed, _launch_spotify, _ytm_play,
_spotify_web_play, _local_tracks,
_open_local, _playback_evidence, _settle) are module-level
so the suite fakes them without touching the real machine.
R3: the v5.70 session probe is GONE (always False, deleted
with the web rung's revival through his attached browser —
no cookie-store reading anywhere).

CONTINUITY (v5.53, F2): a browser rung that confirms audio
has only proven the stream STARTED. In the field (2026-10-23
evening) YouTube played a full 42 s, then served its
player-error card and cut the stream — the dead playback
stood as the result. So a confirmed browser rung now also
schedules ONE non-blocking recheck ~20 s later (daemon
threading.Timer, the house pattern of core/voice/audio.py's
silence guard): audio evidence still live AND the page not
in a player-error state. A stream found dead is ANNOUNCED
in chat through the announce hook the GUI registers
(set_announce_hook — the watcher-event seam), and when a
further ladder rung exists it is attempted ONCE and its
outcome announced too. The recheck never chains (a
fall-through play does not schedule another recheck).

V5.59 (Wave 4, BR-5 / BR-2 / BR-3): honest playback, round
two. BR-5 replaces the single-signal confirmations above
with triangulation (core/browser.page_playback_votes supplies
the two in-page votes; the audio session is the third):
in-page media-element truth PRIMARY, MediaSession
corroboration only, audio-session demoted to third vote, and
"playing" is claimed only on 2-of-3 votes
(triangulate_playback). The one exception is a page that
cannot speak at all (votes {}): then, as in v5.53, audio
evidence alone decides — a silent page is a fallback, never
a fabricated vote. On the BR-2 -> BR-3 route the pattern
collapses to the single IPC signal by design (there is no
page, and the audio session would double-count the same
player output): mpv_ipc_verdict, via
playback_verdict_for_route("ipc"). BR-2 is a new,
flag-gated rung (default OFF — the site-ToS posture, plan
D-7, is still unruled, so browser playback with BR-5 honesty
stays the active default): yt-dlp as a PIP LIBRARY only
(lazy import, pinned version, self-update unreachable from
the library path, extraction/streaming only — never a
download, never a cache of copyrighted audio, never the
stock .exe, never a CLI subprocess). BR-3 ships as the IPC
verification PATTERN only: no mpv binary is bundled, mpv is
not in requirements, and no shipped path shells out to mpv
— the exact-build licence check (plan O-10) did not close,
so mpv stays on the gated shelf (see
core/playback_licences.py).
"""

from __future__ import annotations  # INTENTIONAL (music_player split B8):
# the old module carried this future import, which binds the name
# `annotations` in the module namespace; the facade keeps it so the
# dir diff against the old module is empty.

import os  # INTENTIONAL (music_player split B8): the old module's stdlib
import re  # imports, re-exported so the facade namespace matches the old one.
import shutil  # (sqlite3 is imported but never used in the old module;
import sqlite3  # it travels as a facade re-export for the dir-diff bar,
import subprocess  # not as an impl import — the senses json lesson.)
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote, quote_plus

# INTENTIONAL (music_player split B8): every name the old module defined
# is re-exported from the impl module that now owns it, so existing
# module-attribute importers (w4_wiring, tools_handlers, window_shell_frame)
# work unchanged and with identical function objects. The announce seam
# (_announce_hook + set_announce_hook + _announce) lives whole in the
# evidence module; the yt-dlp seam (_YTDLP_STREAM_ENABLED + setter +
# _ytdlp_enabled) lives whole in the rungs module.
from .music_player_evidence import (
    _AUDIO_EXTS,
    _CONTINUITY_DELAY_S,
    _announce,
    _announce_hook,
    _playback_evidence,
    _settle,
    set_announce_hook,
)
from .music_player_verdicts import (
    _browser_playback_confirmed,
    _page_playback_state,
    _page_playback_votes,
    _stream_alive,
    mpv_ipc_verdict,
    playback_verdict_for_route,
    triangulate_playback,
)
from .music_player_spotify import (
    _YTM_CONSENT_TEXTS,
    _YTM_RESULT_SELECTORS,
    _YTM_WAIT_ATTEMPTS,
    _YTM_WAIT_SECONDS,
    _launch_spotify,
    _local_tracks,
    _media_play,
    _open_local,
    _spotify_installed,
    _spotify_web_play,
    _ytm_dismiss_consent,
    _ytm_play,
)
from .music_player_rungs import (
    _QUALIFIER_PATTERNS,
    _YTDLP_OPTIONS,
    _YTDLP_PINNED,
    _YTDLP_STREAM_ENABLED,
    _continuity_recheck,
    _local_rung,
    _parse_source_qualifier,
    _schedule_continuity,
    _spotify_app_rung,
    _web_rung,
    _ytdlp_enabled,
    _ytdlp_extract,
    _ytdlp_rung,
    _ytm_rung,
    play_music,
    set_ytdlp_stream_enabled,
)

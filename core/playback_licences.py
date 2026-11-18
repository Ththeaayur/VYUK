"""Wave 4 (v5.59) playback licence verdicts — BR-2 / BR-3.

Record, not code: nothing in this module is imported by the
shipped playback path, and nothing here grants a shipment.
A verdict that fails or conflicts would have kept its item
waiting; both verdicts below were read from PRIMARY sources
(the projects' own repositories) on 2026-10-25, not from
aggregators or package-metadata claims. The coordinator's
ship-gate audit (CC-1) remains the gate that re-checks this
file against the shipped tree.

Unchanged by this wave, by instruction: the v5.58 gate's
num2words adapter stays DELETED (LGPL-barred — never
reintroduced); indic-num2words, pyloudnorm and pyaudiowpatch
stay allowlisted OPTIONAL and were NOT re-verified here and
were NOT moved into requirements.txt.
"""

# yt-dlp (BR-2) — VERDICT: LIBRARY-ONLY USE ALLOWED, GATED.
# Primary source: the repository's own LICENSE file, read
# 2026-10-25 at https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE
# — the full Unlicense text ("free and unencumbered software
# released into the public domain... for any purpose,
# commercial or non-commercial"). The repository's own README
# licensing section (same repository, read the same day)
# draws the line this wave obeys: the git repository, the
# source tarball, the PyPI source distribution and the PyPI
# wheel contain ONLY Unlicense code — while the PyInstaller-
# bundled executables include GPLv3+ code and the combined
# work is GPLv3+. Hence: pip library / wheel / source only;
# the stock .exe is never redistributed and never invoked,
# and the rung never shells out to any yt-dlp CLI.
# Pin: 2026.08.19 (latest stable release at verification).
# Self-update: unreachable from the library path (the updater
# lives in the CLI/update module, which the rung never
# imports or calls). Use: extraction/streaming only — never
# a download, never a cache of copyrighted audio.
# SHIPMENT SHAPE: the rung ships behind a flag that defaults
# OFF, because the site-ToS posture (plan D-7) is UNRULED —
# the licence is clean, the ToS decision is Aayur's, and the
# two are not conflated. With the flag off, browser playback
# with BR-5 honesty stays the active default.
YTDLP = {
    "item": "BR-2 yt-dlp (pip library)",
    "licence": "Unlicense (public-domain dedication)",
    "primary_source": "https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE",
    "also_read": "yt-dlp README licensing section, same repository",
    "verdict": "ALLOWED library-only, gated OFF by default",
    "pin": "2026.08.19",
    "forbidden": ["stock PyInstaller .exe (GPLv3+ combined work)",
                  "any CLI subprocess invocation",
                  "self-update (unreachable from the library path)",
                  "download or cache of copyrighted audio",
                  "requirements.txt entry (lazy optional import only)"],
    "open_decision": "D-7 site-ToS posture — UNRULED; flag defaults OFF",
}

# mpv (BR-3) — VERDICT: O-10 NOT CLOSED, BINARY STAYS SHELVED.
# Primary source: the repository's own README, read
# 2026-10-25 at https://github.com/mpv-player/mpv — "GPLv2
# 'or later' by default, LGPLv2.1 'or later' with
# -Dgpl=false. See details." (the Copyright file). An LGPL
# build is therefore possible IN PRINCIPLE, but O-10 asked
# for an EXACT BUILD plus redistribution mechanics and a
# source offer for THIS product, and that did not close:
# stock Windows builds are the default (GPL) configuration —
# the widely used third-party Windows build systems ship the
# default build, and the LGPL variant found in the wild is a
# libmpv-only patch (player executable disabled), not a
# redistributable mpv.exe with proven source-offer mechanics
# for this ZIP. Under the standing commercial rule a GPL
# binary in the distributed product is EXCLUDED-class, so no
# mpv binary is bundled, mpv is not in requirements, and no
# shipped path shells out to mpv. What DOES ship is the IPC
# verification PATTERN (time-pos / pause / eof-reached) via
# BR-5's collapse path — pattern code, not a player.
MPV = {
    "item": "BR-3 mpv (exact build, plan O-10)",
    "licence": "GPLv2+ by default; LGPLv2.1+ only with -Dgpl=false",
    "primary_source": "https://github.com/mpv-player/mpv (README, License section)",
    "verdict": "O-10 NOT CLOSED — binary stays on the gated shelf",
    "ships": "JSON IPC verification pattern only (via BR-5 collapse)",
    "forbidden": ["bundling any mpv binary",
                  "mpv in requirements.txt",
                  "shelling out to mpv in any shipped path",
                  "libmpv embedding"],
    "to_close": "a named compliant (LGPL) Windows build + audited "
                 "dependency list + source-offer mechanics for "
                 "this product's ZIP, re-verified from primary "
                 "sources before any mpv shipment is proposed",
}

VERDICTS = (YTDLP, MPV)

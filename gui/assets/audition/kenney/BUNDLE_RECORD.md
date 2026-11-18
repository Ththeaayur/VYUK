# Kenney audition bundle — B-R1-9 (audition material ONLY, never product audio)

Six Kenney CC0 sounds, fetched as single files through the
Mcamento8/open-game-sfx-index (index.json v0.1.1, 1451 sounds) at
pinned commit `34bbe8b525b5ceff3804915ea745532af11f6055` (main HEAD,
via `git ls-remote`; the GitHub API was rate-limited that day).
The index repo hosts the audio under `audio/<pack>/<file>.ogg`;
every file below was fetched from
`raw.githubusercontent.com/Mcamento8/open-game-sfx-index/<PIN>/audio/...`
and its sha256 verified against `index.json` (all six match).

## Why audition-only — read this before using these files

The shipped earcon identity is the synthesised v3 family
(`gui/assets/*.wav`, 15 cues, both families complete). There is
NO missing cue for these files to fill. The owner's v5.51 earcon
veto is STILL PENDING (`core/sound_pools_v564.py`,
`EARCON_VETO_PENDING = True`): `attempt_layer()` refuses
fail-closed for EVERY asset, including CC0-cleared ones. These
six files are AUDITION MATERIAL for the owner to A/B when he
rules on the veto — they are played only on explicit demand
from the Settings > Sounds audition row (`gui/audition.py`,
`KenneyAudition`), never by `Earcons`, never by any live UI
path, never layered under a shipped earcon, never an
always-on bed. A cleared licence answers "may we use this at
all"; the veto answers "may it sound like VYUK".

## The six candidates (2 per moment)

| file | moment it auditions for | pack (Kenney-authored) | OGG source (sha256, index.json) | WAV (decoded, sha256) | WAV bytes | format | duration | RMS dBFS |
|---|---|---|---|---|---|---|---|---|
| kenney_forceField_000.wav | boot / ready swell | Kenney Sci-Fi Sounds | forceField_000.ogg, 25,672 B, OGG sha256 `c2916f2a062c8ddd1aca2826d134fe90847037db31342726ffb0f9097afe339c` (verified against index.json at fetch) | bd3dce0c27373e17131f6216590734da0bca02697e6d3da42323833f45aaf6c5 | 84,222 | 44,100 Hz mono 16-bit | 0.954 s | −11.53 |
| kenney_forceField_001.wav | boot / ready swell (alternate) | Kenney Sci-Fi Sounds | forceField_001.ogg, 28,947 B, OGG sha256 `5574e69dd04e5f59322c5ddffde5978f077b09170ae5e29cec0bd9901828cd90` | 4bd53ebf7d23727f937f8e0ff60ab68381cde22207e8f3c2491ffc87d0131436 | 84,170 | 44,100 Hz mono 16-bit | 0.953 s | −11.59 |
| kenney_twoTone1.wav | notification two-tone | Kenney Digital Audio | twoTone1.ogg, 7,362 B, OGG sha256 `8732f14d6398407a8f1df77b986c2099cfd88f6583e1272cb60497cb2fe8da1d` | c02c33924c110efe5cb3fece738cfadc77a666cef19678318eba2dcb33f0209f | 63,662 | 44,100 Hz mono 16-bit | 0.721 s | −14.49 |
| kenney_twoTone2.wav | notification two-tone (alternate) | Kenney Digital Audio | twoTone2.ogg, 7,349 B, OGG sha256 `e8e4a2d9faa4344f4722de9ef81103290858bbcd80f1dc686879128cae52e9f0` | 3bdf8ee5d863bb4750ab2dc976fc756bbe8b491969946c878bac2ba6c9563e1f | 64,318 | 44,100 Hz mono 16-bit | 0.728 s | −15.03 |
| kenney_click_001.wav | orb-tap click | Kenney Interface Sounds | click_001.ogg, 4,876 B, OGG sha256 `ccfb7fa0cccdd9faec0eb16033c732b1e308d139d80f799161495d58f7adcdb9` | eb442a817bd39784b0990e31f53fa6bd754b0fe20fca763730df1d625200d520 | 8,644 | 44,100 Hz mono 16-bit | 0.097 s | −26.40 |
| kenney_click1.wav | orb-tap click (alternate) | Kenney UI Audio | click1.ogg, 4,983 B, OGG sha256 `59175ac17cd49a68dd736285738441287636112a84a6f7ce0d89921bda5a5360` | 842a53ed190b269c2926731c11553bff30b5175e4b068b1b52a5e5abd00ab4a2 | 16,118 | 44,100 Hz stereo 16-bit | 0.091 s | −22.46 |

Total WAVs: **321,134 B**. Total with this record + `licence_log.json`: **324,310 B**.

## Conversion (dev-only, build time — never shipped as a step)

The index carries OGG only (no WAV). The tree plays WAVs through
QSoundEffect (Media Foundation on Windows has no OGG decoder), so
the six OGGs were decoded to PCM WAV with system ffmpeg
(`ffmpeg -i in.ogg -c:a pcm_s16le out.wav`), preserving each
file's native sample rate and channels — no resampling, no
re-mastering, no gain applied to the files. The OGG sha256s were
verified against `index.json` before decoding; the WAV sha256s
above are the bundled bytes. OGG sources are NOT bundled.

## Loudness — A/B caveat, recorded honestly

The shipped earcons measure −20.00 dBFS RMS by design. These six
do NOT: forceField ≈ −11.5 dBFS (≈8.5 dB louder than shipped),
twoTone ≈ −14.5/−15.0 dBFS (≈5 dB louder), clicks −22.5/−26.4
dBFS (quieter). An A/B against shipped audio is invalid until
re-matched — the same warning the Wyzowl caveat carries. The
audition player (`gui/audition.py`) therefore applies a per-file
playback-only gain so what the owner hears is matched to the
shipped −20 dBFS standard (gain recorded in every play record);
the files themselves are never altered. Every licence-log entry
carries `loudness_matched: false`.

## Licence (PROPOSED — parent rules)

Read 2026-11-09 from the index repo README at the pinned commit
(`raw.githubusercontent.com/Mcamento8/open-game-sfx-index/<PIN>/README.md`):

- "Can I use these commercially? Yes. CC0 = public domain:
  commercial use, modification and redistribution allowed, no
  permission or attribution required (credit appreciated)."
- "Audio (`audio/`): CC0 1.0 Universal — public domain, by Kenney."
- "Code / metadata / docs: MIT — see LICENSE." (LICENSE: "MIT
  License (applies to code, scripts, docs, index.json and
  categories.json created for this index). Copyright (c) 2026
  Mcamento8" — the index code/metadata is NOT bundled here; only
  the CC0 audio files are.)

Per-asset licence log: `licence_log.json` in this directory,
generated with the tree's own `core/sound_pools_v564.py`
`write_licence_log` — all six records validate `cleared`
(audition material only; never "may layer"), 0 rejected, and
the log records `earcon_veto_pending: true`. Every log entry
names its pack, per the Kenney clearance caveat (the kenney.nl
assets index fetch itself carried no licence text; the
clearance covers Kenney-authored packs). Kenney CC0 was already
verified from the publisher's own bundle page in Wave 9
(`kenney.itch.io/kenney-game-assets-3`: "Public domain license
(suited for unlimited commercial projects)"; FAQ: "all included
game assets are public domain (Creative Commons Zero)
licensed") — this build re-verified it from the index repo
README at the pinned commit. No purchase: the bundle page
lists $9.95, but only freely available assets were used ($0
build-spend rule); licence clearance is not a purchase
authorisation.

## To provision / remove

These files ship in the tree as audition material. If the owner
rules against sampled audio, delete this whole directory plus
`gui/audition.py` and the Settings audition row — the shipped
earcons are untouched and nothing else references these files.
If he rules for a candidate, the layering path is opened by a
separate owner-recorded change to the veto gate, not by this
batch.

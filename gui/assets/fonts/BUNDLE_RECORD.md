# Google Fonts OFL stack — bundle record (B-R1-7, 2026-11-09; body face Sora since B-R1-7r, 2026-11-09)

One typeface stack, three families, all SIL Open Font License 1.1,
bundled in-tree (no pip dependency, no download at runtime).

## Source

google/fonts repo, pinned commit
`5bb28733cec313b63fbd415b574b5ae35c8aa8b8` (HEAD, 2026-11-08),
fetched file-by-file from `raw.githubusercontent.com` at that commit,
HTTP 200 each. Every file below was verified on fetch: TTF magic +
name-table family/subfamily read with fontTools, and a Qt render
probe at the UI sizes (see the suite).

## Files

| File | Bytes | SHA-256 | Family / subfamily |
|---|---|---|---|
| Orbitron-Variable.ttf | 38576 | f42db2dd16e642258e35782916eceb1dcdbea06fb958d77ad71dc5963587e8fd | Orbitron, variable wght 400–900; named instances Regular/Medium/SemiBold/Bold/ExtraBold/Black |
| Sora-Variable.ttf | 111400 | 84ff7096ae3ec6c8be47d906d1a0ba4de7f2ce78c615275c77301964a316e16c | Sora, variable wght 100–800; default instance Regular (400) |
| ShareTechMono-Regular.ttf | 43272 | 9ceab1f87414829af259c0f537573ae03ef7dd3147c0b27a36a1a0beb6732677 | Share Tech Mono / Regular |
| OFL-Orbitron.txt | 4426 | ab609b0e110d622435ff337cdf233288556e011bbf9bd0550be98846c0630819 | OFL 1.1, "Copyright 2018 The Orbitron Project Authors …, with Reserved Font Name: \"Orbitron\"" |
| OFL-Sora.txt | 4385 | ba0b9729c9428ba79a0459ab8ec575791b51509dbec213e383d0316d37fec299 | OFL 1.1, "Copyright 2019 The Sora Project Authors (https://github.com/sora-xor/sora-font)" — no Reserved Font Name |
| OFL-ShareTechMono.txt | 4429 | 9d96f445b6e9c701428811d0177f894874f8d6f07ecc30d568c506542368f3ff | OFL 1.1, "Copyright (c) 2012, Carrois Type Design, Ralph du Carrois …, with Reserved Font Name 'Share'" |

Total bundle 206,488 B.

## Roles (the one stack)

- Display / headings / HUD words: **Orbitron** (variable, wght 400–900;
  the QSS uses weight 600). One file covers every weight the UI uses —
  the same shape as the Space Grotesk variable file it replaces.
- Body text: **Sora** (variable, wght 100–800; the UI uses 400/500/600/
  700). One file covers every weight the UI uses. A surface that asks
  for a weight outside the axis gets Qt's nearest-weight match,
  honestly — never a silent swap to another family.
- Code / numbers: **Share Tech Mono** (single weight 400).

## What it replaced

B-R1-7 replaced the previous stack — Inter ×4, SpaceGrotesk-Variable,
Chakra Petch ×4, JetBrains Mono ×3 (2,596,647 B) — REMOVED in that
batch. One stack, no dead weight, no ambiguity about which face
renders. B-R1-7r (2026-11-09) replaced the B-R1-7 body face:
Rajdhani Regular/Medium/SemiBold/Bold (1,553,228 B) + OFL-Rajdhani.txt
(4,373 B) REMOVED, Sora-Variable.ttf (111,400 B) + OFL-Sora.txt
(4,385 B) added — net −1,441,816 B shipped in this batch. The owner
picked Sora himself ("whatever brings us close to scifi") after
challenging the Rajdhani pick as Hindi-first: Rajdhani is a Latin +
Devanagari font by Indian Type Foundry and the round-1 scouting
record gave no justification for it over other geometric sans fonts.
The stacks keep the system fallbacks (Segoe UI Variable / Segoe UI /
Cascadia / Consolas) after the bundled face, so a machine without the
bundle still renders presentably; a bundled face is never claimed
when the file is absent — the loader reports what it loaded, and the
suite asserts the files exist.

## Loophole family (closed by this design)

"The font lies, or the font is missing": a heading claiming Orbitron
while rendering in the system font; a font unreadable at its UI size;
a font shipped without its OFL.txt; a surface claimed covered with no
font. ONE design: one stack — every font a real OFL file (bundled,
never a placeholder); every font renders cleanly at its UI size
(measured in the suite at the smallest size it is shown at);
every font ships with its OFL.txt beside it; every surface the stack
claims to cover has a font, and a surface without one falls back to
the existing font, honestly. Rejected: a placeholder font (a lie); a
font unreadable at its UI size (unreadable); a font shipped without
its OFL.txt (a licence violation); a surface claimed as covered with
no font (a lie); a system-font fallback presented as the stack (a
lie); keeping the old stack alongside the new one (two full systems,
dead weight, ambiguous rendering).

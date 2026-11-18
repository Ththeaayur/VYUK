# Flow-style command palette — licence / pattern record (Sweep Build 4)

## What this build is, and is not

Nova's command palette is Nova's own code, in Nova's own style. It is
**pattern use only**, inspired by the general launcher-palette pattern
(type a few characters, see ranked actions, press Enter) that Flow
Launcher popularised on Windows. It is NOT Flow Launcher:

- **No Flow Launcher code is copied** into Nova — not a line, not a
  plugin, not a theme.
- **No Flow Launcher binary is bundled** with Nova.
- **Nova's palette is not named, branded, or presented as Flow
  Launcher**, and no Flow name, logo, or artwork is used.
- The palette that ships in Nova predates this sweep build
  (gui/window_widgets.py, `CommandPalette`: Ctrl+K, titlebar pill,
  filter-as-you-type). This build adds only Nova's own Qt-free
  matching/ranking logic (core/command_palette.py), a Globe page
  row the shipped palette was missing, and a second confirmation for
  the palette's consequential rows. Nothing in that delta is Flow
  code.

## Flow Launcher licence sighting (pattern use only)

Flow Launcher (Flow-Launcher/Flow.Launcher on GitHub) is a
community open-source Windows launcher. Sweep Phase 1 recorded an MIT
sighting for Flow Launcher, and public MIT statements for the project
were re-confirmed at this build by web search (project site and
secondary descriptions state MIT; the GitHub organisation's project
list carries MIT licence metadata for its site project). That sighting
satisfies **pattern use only**: the idea of a keyboard-driven ranked
action palette is not itself Flow's copyrighted code.

Because no Flow code ships, **no MIT notice is owed for this build**:
the MIT notice obligation attaches to copying Flow's copyrighted
code, and none is copied. If Flow code were ever copied in future,
its full MIT copyright notice would have to be filed with the copied
code before shipping — that line is stated here so a future build
cannot cross it silently.

## What would kill this item

Copying Flow Launcher source, bundling its binary, or presenting
Nova's palette as Flow Launcher would kill the pattern-only basis of
this record and require a fresh licence review (MIT notice filing,
plus a check that no Flow plugin or theme terms travel with the code)
before any ship. None of those things happened in this build.

## Rules the palette itself enforces

- Tool rows never execute a tool directly: they prefill the composer,
  so Nova's brain argument-filling and C-14 confirmation still run.
- Consequential palette rows (connect/disconnect, wake-word toggle)
  require a second explicit confirmation in the widget; anything
  else is denied by default.
- No fake data, no silent send/delete/purchase, no new sidebar entry,
  no stack renumbering, frozen schema and cap, telemetry OFF, $0
  spend, no new paid dependency.

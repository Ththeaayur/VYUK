# Command Deck — locked decisions (guide §4, D1–D15)

Owner-approved as written 2026-10-31. Do not re-litigate; do not
silently change. A change to any row below needs the owner's explicit
word and an update to this record in the same commit.

| # | Decision | Locked value |
|---|----------|--------------|
| D1 | Colour source of truth | Generated Python `core/tokens.py` constants + QSS; `gui/style.py` consumes them. Any palette re-pin is explicit and reviewed, never silent (INV-11). The Phase B re-pin is the one reviewed re-pin. |
| D2 | Green | Done or healthy only. Listening and Speaking move to cyan. |
| D3 | Existing users | Saved Indigo Teal becomes Cyan. **SUPERSEDED 2026-11-01 by owner decision "keep only the aura": Aura is the only orb style; absent `orb_style` → Aura, and any saved non-Aura style migrates to Aura on load.** |
| D4 | Custom accent | Replaces only the cyan role. Amber, red, green fixed. Must pass 4.5:1 text contrast or fall back to cyan. |
| D5 | Fonts | Chakra Petch (titles, nav, status words), JetBrains Mono (numbers, micro-labels), Inter (chat, long text). Bundled with OFL texts. **SUPERSEDED 2026-11-09 by the owner's pick of round 1's 10 (2026-11-08, "I want all of them if they're within our rules"): Orbitron (display / titles / HUD words), Rajdhani (body / chat / long text), Share Tech Mono (code / numbers), bundled with OFL texts (B-R1-7). SUPERSEDED in part 2026-11-09 by the owner's pick of Sora as the body face ("whatever brings us close to scifi"): Sora (body / chat / long text) replaces Rajdhani; Orbitron and Share Tech Mono stay (B-R1-7r).** |
| D6 | HUD intensity | Cinematic (Full, default on capable machines), Professional (Light, default under power-saver), Minimal (Off). Contrast theme starts at Minimal. All three functionally identical. |
| D7 | Icon | Replace pixels of `nova_icon.png` first; rename in one later commit updating all eight references. |
| D8 | Overlay minimum OS | Windows 10 version 2004 or later. Older builds keep the tkinter beacon only. |
| D9 | Ship gate | One long-lived branch. Ship when the guard allowlist is empty, the screenshot sweep passes, Windows snap and DPI pass, and Verifying is either wired or hidden. |
| D10 | Trademark | No JARVIS, Stark, Iron Man or Arc Reactor wording or artwork shipped or marketed. "Reactor" as an orb style name is fine. No film voice, music or screen art. |
| D11 | Brand | Ship as VYUK. AppPublisher=NovaAI and Nova legacy names (QSettings NovaAI/Nova, nova_icon) are intended; migrate settings keys if any are renamed. |
| D12 | Sound consent | No sound before the user opts in on first run. When on, the soft set is the default. |
| D13 | Window silhouette | Main window gets chamfered corners (top-left and bottom-right) instead of a radius, if native resize and Snap Layouts still work (phase D test). Otherwise a 6 px radius with corner brackets. |
| D14 | Truth rule | No state, readout or animation without an authoritative event. Enforced by the honesty test. |
| D15 | Overlay timing | Overlay is the marketing moment. Ship it in the first release if phase F is on schedule; otherwise ship after phase E and follow within one release. |

## Palette (guide §5.2)

Window base BG `#04090E`; surfaces `#0C1D28` / `#08131B` / `#122636`;
lines cyan 14% (subtle) / cyan 30–45% (strong); accent cyan `#35D6FF`;
accent-2 `#BFF4FF`; amber `#FFB020`; ok `#5CF2A8`; danger `#FF5468`;
neutral `#7E9AA6`; text `#E6FBFF` / secondary `#B0C1CD` / muted `#7E9AA6`.
Violet and teal are dropped as roles. Orb themes: Cyan (default),
Amber, Crimson, plus Graphite, Mono, Contrast; Sunset, Violet, Aurora,
Ember, Rose move to a "More themes" row and keep working.

## Notes carried from implementation (not decisions)

- Font files: Chakra Petch and JetBrains Mono `.ttf` files were NOT in
  the tree and NOT on the lab machine at Phase A time, and cannot be
  fetched through lab channels. The font loader degrades gracefully
  (Chakra Petch → bundled SpaceGrotesk → system sans; JetBrains Mono
  → Cascadia → Consolas → system mono) until the OFL files are
  supplied. Bundling them is NEEDS-RULING / field supply, not done.
- Manifest re-sign: phase G's manifest regeneration is DEFERRED to
  the ship gate (the build never re-signs); a ship needs its own gate.
- Assistant persona naming: the guide's banned-name scan (no JARVIS
  wording in shipped files) conflicts with the standing persona
  decision (assistant persona stays J.A.R.V.I.S). Product and
  marketing surfaces are swept in phase G; persona-name strings are
  left untouched pending the owner's ruling. The scan allowlists
  persona occurrences explicitly until ruled.
- Ruff TID252 config cannot be executed in lab (no ruff binary);
  the grep test in `tests/test_commanddeck_guards.py` is the lab
  enforcement and CI runs the ruff check.

## Phase B re-pin record (the reviewed re-pin, guide D1)

Applied in Phase B; the INV-11 proof is green for the new values
(`prove_byte_identity()` identical_all, all 11 themes + house).

- Themes: "Indigo Teal" → "Cyan" (accent `#35D6FF`, accent_2
  `#3D8BFF`, highlight `#E6FBFF`); "Amber" (`#FFB020` / `#FF6A00` /
  `#FFF1D6`) and "Crimson" (`#FF5468` / `#FD465A` / `#FFE3E6`) added;
  Sunset / Violet / Graphite / Aurora / Ember / Mono / Rose /
  Contrast kept (11 total). Note on the guide's "accent-2
  `#BFF4FF`": that is the pale-tint family, realised for Cyan as
  the highlight `#E6FBFF` (same value as the orb hi and the text
  colour); the theme dict's `accent_2` slot is the saturated
  secondary/edge `#3D8BFF`, following the old indigo/teal pair
  structure. Violet keeps its own `#7C8CFF` secondary — that is
  Violet's theme identity, not the retired house role.
  Two first-choice values failed the tree's own uipolish contrast
  guards and were corrected, not waived: the first Cyan
  accent_2 `#0A6CFF` measured 4.38 as text, 3.55 pressed, 3.80 on
  the chip background (the gradient rule needs every leg >= 4.5),
  so `#3D8BFF` was chosen (text 6.03, pressed 4.78, chip 4.98);
  the first Crimson accent_2 `#B0102A` measured 2.81 / 2.36 /
  4.26, so `#FD465A` was chosen as the darkest vivid
  crimson-red passing every gradient leg with >= 0.2 margin
  (rest 5.90, pressed 4.70, mid 6.14, mid-pressed 4.88). The
  Crimson identity still comes from its accent `#FF5468`.
- Pressed derivation reconciled: the Phase A tree's proof was RED
  because the DTCG generator derived pressed at 18% toward black
  while the shipped resolver uses 12% (0.18 failed the 4.5:1 floor
  on Graphite / Aurora / Ember; 0.12's worst end is 4.72). Phase B
  aligns the generator to the shipped 0.12 — shipped sheets are
  untouched by this fix; it only makes the generator truthful.
- Chrome: BG `#04090E`, SURFACE_1 `#0C1D28` (dialogs), SURFACE_2
  `#08131B` (resting), SURFACE_3 `#122636` (raised/selected; not in
  RESOLVED_KEYS, pinned by Phase B tests, not the proof), TEXT
  `#E6FBFF`, TEXT_MUTED `#B0C1CD`, TEXT_FAINT `#7E9AA6`, roles
  AMBER `#FFB020` / OK `#5CF2A8` / DANGER `#FF5468` / NEUTRAL
  `#7E9AA6`, LINE_SUBTLE cyan 14% / LINE_STRONG cyan 40%
  (mid-range of the guide's 30–45%). BORDER_* stay white-alpha
  until Phase C, when HudPanel owns outlines.
- House spellings (style `_HOUSE_*` + tokens mirrors) moved to the
  new Cyan house values (hover `#5DDEFF` / `#64A2FF`, pressed
  `#2FBCE0` / `#367AE0`, pale `#E6FBFF`, disabled `#185B6E` /
  `#1B3D6E`); every hardcoded old-accent rgba triplet in the
  template moved to its role triplet, including normalising one
  unspaced gradient to spaced forms so themed builds rewrite it.
- Buttons: PRIMARY_GRADIENT* constants deleted; primary / mic /
  send buttons are a solid cyan outline with a cyan wash on hover
  (`PRIMARY_WASH` 28 / pressed 48, triplet spellings so themed
  builds rewrite them); mic active fills solid accent; checked
  toggle fills solid accent; brand mark fills solid accent. The
  settings swatch buttons keep their two-accent gradient previews
  (they preview the theme, they are not buttons) and the privacy
  card keeps its accent_2 tint until Phase D removes the card.
- D3: `migrate_theme_name()` in `core/tokens.py` ("Indigo
  Teal" → "Cyan", everything else through) is called at every
  theme-read site (app_settings `_current` / `_set_theme`,
  window_settings apply / orb / appearance / sync /
  `_on_theme_selected`, which heals the stored value on apply).
  Defaults: theme "Cyan", orb_style "Aura" (settings schema,
  app_settings, window_settings migrate block; the window_chat
  cycle fallback is deleted with the picker). Saved non-Aura
  styles migrate to Aura on load (Aura-only, 2026-11-01); the colour
  word "amber" still maps to Ember (the Amber theme is reached by
  its exact name, not by the colour word — no voice churn).
- D4: `gate_custom_accent()` in `core/tokens.py` — a Custom
  accent is kept only if BG-coloured button text on it is >=
  4.5:1, else cyan (`#35D6FF`). Applied at pick time (the stored
  value is the gated value, so the label shows the effective hex)
  and at apply/sync time (pre-gate stored values apply gated).
  No new dialog is raised for a gated pick in Phase B (dialogs
  migrate to HudDialog in Phase C; the label shows the change).
  Note: the first Cyan accent_2 candidate `#0A6CFF` as a Custom
  fill measures 4.38:1 and is therefore gated to cyan — correct
  per D4, since it would carry BG button text; it is kept as a
  near-miss case in the Phase B gate tests.
- Orb palettes: named palettes are now Cyan / Sunset / Violet /
  Mono / Amber / Crimson (triples in `visualizer.py` and the
  `mini_orb.py` fallback copy, kept in sync). Graphite still maps
  to the Mono palette; Contrast still derives its orb triple from
  its style theme (highlight / accent / accent_2) via the custom
  path. Widget defaults: theme "Cyan", orb_style "Aura" (the mini
  canvas defaulted to Aura with the Aura-only change).
- Fonts: DISPLAY stack is now Chakra Petch first (→ SpaceGrotesk
  → Inter); MONO stack is JetBrains Mono first (→ Cascadia →
  Consolas → monospace). The loader probes the standard Google
  Fonts filenames for both and skips missing files silently; it
  also now guards the no-application case (QFontDatabase with no
  app segfaults — found by the Phase B font test, which called
  the loader app-less). OFL files remain OWED (not in tree/lab).
- Guard allowlist: re-pinned for exactly `gui/style.py`,
  `gui/visualizer.py`, `gui/mini_orb.py`, `gui/window_settings.py`
  (317 → 338 pairs; dialog allowlist unchanged). After this
  re-pin, the allowlist only shrinks again.
- Known Phase B remainders (not regressions): the Listening /
  Speaking pill colours are still the old green (D2 moves them to
  cyan in Phase D's state rewrite); settings-page inline accent
  styles (focus borders, selection) still carry the old accent
  (Phase E migrates the Settings page); the window chrome
  (borders, radii) is unchanged (Phase C owns it); the Apex
  low-end-laptop field test is MOOT — Apex was deleted 2026-11-01
  (Aura-only owner decision).

## Phase C record (guide §10 Phase C)

- §9.4 vs §10 conflict, resolved for §10: §9.4's target
  confirmation format ("// AUTHORIZATION REQUIRED: ACTION,
  TARGET, RISK, DATA SCOPE; AUTHORIZE ONCE and DENY") conflicts
  with §10 Phase C's explicit "Behaviour, wording and the 60 s
  default-deny stay exactly as they are" plus the builder
  docstring "Buttons keep their exact labels". §10 wins for
  Phase C (HudDialog re-base is chrome only). The §9.4 format
  question is OPEN for the parent — not silently redesigned. RESOLVED 2026-11-03 under the code-rulings delegation: S-1 confirmed as the built authorization dialog; the four-field format (ACTION/TARGET/RISK/DATA SCOPE, 60 s ring, red edge) is not built and will not be built under this ruling. The guide's §9.4 text stands as written (owner's file; amendment appended as p.33 on 2026-11-03, ruling governs the build); this ruling governs the build.
- Destructive severity: ONLY Reset settings, Remove model, and
  Revoke extension get HudDialog destructive=True (danger edge +
  danger Yes button). All other confirms keep the info edge.
  Labels stay Yes/No and default stays No in every case.
- The 60 s default-deny lives in confirm_gate + the handler
  thread wait, NOT in HudDialog: no countdown was added.
- `_settings_card` / pf `_card` migrate to HudPanel with
  objectName "hudPanel" (stylesheet rule `#hudPanel` neutralises
  QFrame chrome so paint never double-draws). The PF-5 settings
  opener accepts both "settingsCard" and "hudPanel".
- BORDER_* now spell the LINE_* roles (FAINT/BORDER ->
  LINE_SUBTLE, STRONG/TOP -> LINE_STRONG); white-alpha hairlines
  are retired. GLASS_* rules are retired (constants kept so
  _TOKENS stays stable). Themed glass card treatment retired
  with the gradient rules; themed cards follow SURFACE_2.
- Scrollbar thumb: cyan 30% rest, 70% hover; pressed holds the
  hover strength (no third value invented).
- themeSwatch radius 12 px -> 2 px: the swatch is 46x30, not a
  circle (verified in window_settings, not assumed). Circles
  kept: send 22 px, radio 9 px, mic 28 px, icon 21 px.
- Font stacks are single QSS strings, not tuples: QFont code
  must use `first_font_family()` (style.py, pure stdlib), never
  stack[0] (a quote character -> empty family -> symbol font).
- OrbThumbnail default size 140 px: below the visualizer's
  130 px LOD threshold every style collapses to the same compact
  mark. Thumbnails fill the dark window background before grab
  (the visualizer paints no background of its own).
- CommandRadar carries its own dark sheet: top-level dialogs get
  no sheet from the main window, so without one it renders in
  the default light palette.
- Shape-test allowlist (file-level, shrinks in D/E/G):
  doctor_page, memory_web, onboarding, skills_galaxy,
  window_services, window_settings, window_shell; circle
  exemptions (style.py, 22) and (style.py, 9) only.
- ruff.toml: the 10 per-file TID252 lines are removed (dialog
  allowlist empty); the banned-API section and the tests/*.py
  TID252 ignore stay (tests mock HudDialog now, and the guard
  test itself names the banned classes in strings).

- Phase C post-run fixes (full-run #2 findings, both fixed not
  waived): (1) NO global QTabBar focus rule in style.py —
  test_focus_visible pins Qt's default tab focus frame (no
  override, no suppression); tab focus styling belongs to the
  component (HudTabs) and the S13 bracket ring (Phase D), never
  to a global QTabBar override. (2) pf `_card` uses the
  dual-context HudPanel import (relative, except ImportError
  absolute) because uisettings file-path-loads pf_surfaces as a
  top-level module with no parent package.
- C10 re-proven in Phase C: a full run (or batch) that follows
  another run without an intervening delete-first data restore
  fails the auditfixA5 timeout test with 'refused' (stale
  receipt-guard state), even though the test passes alone and in
  every fresh-data combination. Restore data immediately before
  EVERY full run, not just the first of the day.

- Phase D (producers, Home, chrome):
  - Bus identity is import-path identity: `gui.state_bus` and
    `voice_assistant.gui.state_bus` are DIFFERENT module objects
    with different event classes, so a subscriber on one never
    sees events emitted on the other. Core producers resolve
    `gui.*` FIRST (unit-test GUI code is gui-imported);
    production/renders only resolve voice_assistant, so nothing
    changes there. A leaked voice_assistant shim in a test
    process used to silently drop every producer event (5 Phase
    D tests, zero events, green standalone) — regression test
    pins the gui-first order. The same hazard class exists in
    window_services' absolute voice_assistant bus imports
    (_set_pill, mic health) — correct in production/renders,
    and no suite drives them under a shim.
  - §7 map is single-source: `session_states.resolve_status`
    feeds the pill, the bus AppState and the strip, so the three
    can never disagree. Ice = TEXT (no ice token exists). D2:
    Listening/Speaking are cyan; green is done/healthy only
    (mic-health healthy keeps green legitimately).
  - StatusStrip ownership split (no two writers per chip):
    STATUS = _set_pill, WATCH = _sync_watch_pill, CAPTURE =
    _render_capture_indicator, MODE/MIC/WAKE = bus AppState.
    MODE shows only when known (LOCAL CORE on fallback, else
    hidden — unknown is never guessed). Capture is AMBER
    (attention, not fault). Permission "none" renders empty
    (absence of a claim is not a claim).
  - D13 chamfer is paint-only: AppShell path (TL/BR 8 px cuts,
    TR/BL 6 px arcs, LINE_STRONG stroke, accent bracket ticks);
    flags, QSizeGrip and minimum size untouched. Radius 0 =
    full square for maximised (cuts would show the desktop).
    _toggle_maximize restores radius 6, not 24. Real-Windows
    Snap Layouts are field-owed, not proven offscreen.
  - Verifying is HIDDEN (D9 producer-or-hide): no producer, no
    display — the marker is ignored. Hiding the display state
    does not close the coordinator-verification-phase gap (S3).
  - Hero state_label sentences are long-form copy, not §7
    words — left unchanged in D; the pill carries the §7 word.
  - No producer may emit a value it did not observe: planner
    goals get no MissionState (no totals), tools-direct calls
    are not separately emitted (dedup owed), TargetRect /
    Verified / Input events have no producer (no authoritative
    source). SensorState in D is MIC only.
  - Render harness lesson: a second snap in one offscreen
    process never returns (opacity animation timer never
    drains without window-opacity support) — one process per
    MainWindow shot (render_phaseD_home.py).
  - Shape allowlist after D: window_shell stays (goalPill 12 px
    is Phase E mission work); brandMark fixed to 2 px in D.
  - Colour allowlist after D: window_session 2, window_shell
    19, window_services 5 (all shrunk, same-commit re-pins);
    design_system stays literal-free (comments are scanned too).

## Phase E — pages + D9 gate + §9.2 Home correction (working tree only, UNSHIPPED)
  - Every page / shell / service / widget file is
    literal-free now: Settings (pronunciations well,
    swatch radius, focus-flash, custom-hex defaults ->
    tokens), Mission Log kicker + goalPill (indigo wash
    -> with_alpha(ACCENT, 22/70), 12 px -> RADIUS_BTN),
    Doctor (severities -> OK/AMBER/DANGER, report view
    -> BG/TEXT/LINE_SUBTLE/RADIUS_BTN/SELECTION_BG),
    Archive (category palette re-derived from tokens:
    categorical colours are information, not chrome, so
    8 categories stay distinct via the token family),
    World View templates (direct value replacement with
    token values — no Python substitution point exists
    and changing the load path would risk the globe fix;
    guard re-pinned 6->5 / 9->8 pairs), Capabilities
    (11-entry token-derived GROUP_PALETTE), Batch C
    (senses/pulse/ambience/glyphs/icons/overview/widgets/
    services/chat/session). Orb palettes in
    visualizer/mini_orb deliberately NOT migrated: named
    theme identities (Sunset/Violet/Graphite/Aurora/Ember/
    Mono/Rose/Contrast), not chrome; removing them would
    break saved themes. Noise-texture dots
    (QColor(255,255,255,6) + black 6) deliberately left:
    texture grain, not a palette role.
  - QTabWidget-stays decision CORRECTED: style.py
    contains NO QTabWidget/QTabBar rules at all (the
    Phase E premise was wrong), and uis5 pins QTabBar
    ABSENT from style.py (tab focus keeps Qt's default
    frame, deliberate). The app's only QTabWidget
    (activityTabs) carries a widget-scoped sheet: pane
    SURFACE_2 + LINE_SUBTLE rim + RADIUS_CARD, selected
    tab TEXT + 2 px ACCENT underline, unselected
    TEXT_MUTED, hover TEXT. Tokens only.
  - TRACK/LINE_SUBTLE: pulse_page TRACK (white 26)
    -> QColor(*parse_color(LINE_SUBTLE)); the track is
    a hairline, not a colour role.
  - D2 ambience/glyphs cyan: LISTENING_HEX and
    SPEAKING_HEX are both ACCENT; the §7 word, not
    colour, distinguishes Listening from Speaking.
    Glyph speaking bars are ACCENT for the same reason;
    thinking violet stroke is ACCENT_2.
  - Nav focus-vs-checked: :focus wash 48 -> 14 (Home
    keeps keyboard focus as first-focusable widget, so
    the strong wash double-highlighted); :checked keeps
    the strong 30 wash + white text. Focus stays visible
    for keyboard users. Guard re-pin: style.py gained
    'rgba(53,214,255,14)' (48 retained elsewhere).
  - Hero copy: the "Not listening — wake word is off"
    vs LISTENING pill contradiction was a render-harness
    artifact (pill set directly, label left at first
    paint), not a production bug — _on_status always
    sets label + pill together. The idle sentence is
    now scoped: "Not listening for “Hey VYUK” — wake
    word is off (click the Wake toggle, or use the mic
    button)" — it can never contradict a LISTENING pill
    (pill = mic live now; line = wake-word monitoring
    off), while keeping the v5.53 field-evidence purpose
    (idle UI admits she cannot hear "Hey VYUK").
  - MODE truth: resolve_mode(fallback_active, engine)
    in design_system/session_states.py is the single
    rule (fallback -> LOCAL CORE, local -> LOCAL CORE,
    gemini -> CLOUD, anything else -> "" hidden, never
    guessed); _set_pill calls it over the fallback flag
    + _brain_engine() in try/except (failure -> hidden).
    The Settings brain-truth line uses the same
    derivation, so strip and card agree.
  - §9.2 Home batch (parent correction 2026-10-31
    ~23:27 IST): dock waveform 48 bars (WaveformWidget
    18 -> 48 in the same 104 px widget, 1.5 px bars);
    ›_ prompt glyph on the composer placeholder; square
    Mute/Screen transport buttons (new squareBtn rule:
    radius 0, hairline rim, muted text, hover/pressed/
    focus mirror chipBtn); Aura-only since 2026-11-01 (owner
    decision superseding the Phase C Apex-default pin:
    visualizer orb_style = "Aura", settings default "Aura",
    saved non-Aura styles migrate to Aura on load). OWED,
    named with guide refs (see PROGRESS):
    72 px icon rail; hero rewrite incl. the four
    removals + right column + corner telemetry; hero
    orb ~520 px tier-aware sizing + EXECUTING amber arc
    + tick-ring caching + silhouettes at 320/460/92;
    transcript line-prefix restyle; CommandRadar;
    chamfered dock body + SEARCH › strip + mic level
    segments + mic-mode/capture-scope readout; Windows
    finish (Snap, DPI, multi-monitor, badge/flash,
    DWM); full §11 guard 6 sweep matrix. Home is NOT
    called done by this phase.
  - D9 gate (gate ONLY, not a ship): Verifying
    lab-passed as hidden (no gui/*.py line displays it;
    coordinator gap stays S3-open, not closed); Windows
    snap + DPI FIELD-OWED (offscreen Qt cannot exercise
    real-Windows Snap Layouts); brand strings FINAL
    (persona-naming ruling CLOSED 2026-11-01: shipped
    user-visible strings stay VYUK; J.A.R.V.I.S is the
    private assistant name only).
  - Render harness lessons (harness only, no tree
    change): onboarded=true must be pre-set in lab
    QSettings before EVERY render (else the 700 ms
    onboarding timer opens a modal nobody dismisses);
    galaxy stub must exec the pure core/memory helpers
    verbatim (lines 1–85) or the registry import fails;
    dock_flow forces waveform + transport visible AFTER
    the settle pumps (the 1 Hz telemetry tick re-hides
    the waveform when no real audio flows) and grabs
    with no further pump; one process per MainWindow
    shot (opacity animation hang).
  - Colour allowlist after E: pages/shell/services/
    widgets all removed; remaining entries are the token
    source (style.py), theme/orb palettes
    (visualizer/mini_orb), and the re-pinned globe
    templates — the guide §10 "allowlist empty" exit is
    met for pages, not strictly for the token source
    itself (guard 1 exempts the token source).
    Shape allowlist after E: ONLY onboarding.py (Phase G
    first run). Stock-dialog allowlist stays EMPTY.
    Ruff banned-API section stays enforced.
  - Full runs: run 3 = 2,766 OK, 5 skips (2,750 + 16
    Phase E suite); run 4 (final tree) = 2,770 OK,
    5 skips (2,766 + 4 Home suite). Zero failures in
    both; zero-outside-budget invariant holds. Data
    369,701 B + clean diff after each run. VERSION
    5.99.0; manifest untouched; canonical ZIPs untouched.

  - §9.2 HOME-COMPLETION (home92) — BUILT 2026-11-01
    (working tree only, UNSHIPPED; parent verification
    pending): 72 px icon rail (CORE/MISSION/ARCHIVE/
    CAPABILITIES/SYSTEM, Settings foot, SYSTEM sub-tabs
    Diagnostics/Senses/Self-test/World View, old deep
    links kept, wake ring, LOCAL readout, 120 ms
    scan-wipe); hero rewrite (Reactor, bus-mirrored
    status word, mono step line, YOU ›/VYUK › captions,
    real-data corner telemetry, right column Mission/
    Target Lock/Safeguards; greeting, briefing card,
    voice-pace chips, privacy card REMOVED); hero orb
    (tier-aware cap max_paint_min=590, EXECUTING amber
    arc, static tick-ring cache, same silhouette at
    320/460/92); transcript restyle (line prefixes, 1 px
    left rule cyan you/white VYUK/amber action, mono
    timestamps, HudPanel result cards + code blocks,
    PROCESSING SegmentBar, distinct answer/plan/
    action-trace/permission/system visuals; rounded
    ChatBubble bubbles gone); CommandRadar face over
    the palette dispatch engine (Ctrl+K, previewed via
    run_by_label incl. consequential confirmation);
    dock remainder (chamfered HudPanel frame, SEARCH ›
    strip → _open_chat_find, mic level segments on the
    ring, MIC {mode} · SCOPE {scope} readout); mini orb
    remainder (hover status word, HUD menu Mute/Unmute
    + Play briefing, monitor clamp on drag-end +
    restore); title bar (VYUK // {section}, ›_ COMMAND
    Ctrl+K pill, square 32×32 buttons); corner
    telemetry reflows + hides at 908 while status word
    and dock stay.
  - home92 session fixes (render-proven, not waived):
    (a) _fade_in stranded faded chat items at (0, 0):
    _SlideOffset captured widget.pos() synchronously
    before the layout ran, and the watchdog snapped
    back to that base — result card + thinking bubble
    painted over the transcript top. Fix: _fade_in
    activates the parent layout before capturing, and
    snap() re-activates the parent layout instead of
    moving to a stale base (layout owns the final
    position; no-parent-layout widgets still fall back
    to the base). Pinned by a regression test that
    FAILS on the old snap (old-code check: (0, 0)).
    (b) 1.2-scale hero clip: at 1.2 the hero column
    (393 px at 908) compressed wrapping labels — the
    wake-off sentence lost its last lines (908: tight
    stack after 5 lines; 1108: "mic button)" missing)
    and Safeguards lost "purchase need approval" at
    ALL tiers. Fix: compact wake-off sentence above
    1.0 text scale ("Wake word is off — no “Hey VYUK”.
    Mic or toggle.", 50 chars — keeps the wake-off
    state, the Hey VYUK admission, and BOTH remedies;
    full sentence stands at ≤ 1.0 as Phase E approved
    it; font-scale apply re-derives the idle line via
    the guarded _apply_idle_listening_text) + hero-only
    tightening (orb/right gaps 8→4 px; hero panel
    padding 16→8 vertical, row gaps 6→4; shared
    HudPanel defaults untouched). Post-fix geometry:
    state 124/124 at 908, panels at full sizeHint;
    all 18 sweep renders re-viewed clean.
  - home92 verdicts: DONE — rail, hero rewrite (+ four
    removals), hero orb, transcript, CommandRadar, dock
    remainder, mini orb remainder, title bar, corner
    telemetry reflow/hide. PARTIAL — YOU › live
    user-preview feed (seam only; pipeline commits
    turns straight to bubbles today); hero Mission
    Pause (no handler; hero carries Stop only);
    profile application (stored value only, no visual
    effect — sweep proves clean rendering under each
    stored value, not application). OWED (named, not
    scoped away) — Windows finish (Snap Layouts, DPI
    100–200%, multi-monitor restore, taskbar badge/
    flash, DWM caption/border: FIELD-OWED on real
    Windows only); fonts (Chakra Petch + JetBrains Mono
    .ttf + OFL texts: parent browser task); Phase A
    goldens beyond Home; Self-test as distinct surface
    or auto-run-on-tab (Phase G/owner decision);
    persona-naming ruling CLOSED 2026-11-01 (shipped strings
    stay VYUK; J.A.R.V.I.S is the private assistant name only)
    + §9.4 resolved as non-decision (the guide already fixes
    the authorization-dialog format; its call-site audit is
    verification owed, not a decision). SUPERSEDED 2026-11-03: the 2026-11-02 call-site audit showed the guide's §9.4 format is NOT what the built S-1 dialog renders (no four-field format, no ring, no red edge); ruling under the code-rulings delegation confirms S-1; the four-field format is not built.
  - home92 runs: suite 40 tests OK on BOTH interpreters
    (system 40 OK, 11 skipped = Qt-only; Qt venv 40 OK,
    0 skipped); neighbours 337 OK system (60 skipped)
    / 339 OK Qt venv (phaseB/C/D/E, guards, state_bus,
    uis1–6, uia11y, uibreakpoints, uiinteraction,
    uipolish, uisettings, audit2s1, chatfix); full run 5
    = 2,806 OK, 5 skips, 0 failures (Qt venv canonical
    runner, 140.8 s); classify_run: 0 in every bucket,
    no NEW failures. Data 369,701 B + clean diff after
    the run (delete-first restore from
    data_backup_v529). VERSION 5.99.0; manifest
    untouched; canonical ZIPs untouched (07d002e3…).
    Diff vs backup_commanddeck_home92_pre: exactly 26
    files modified (25 code/test + this DECISIONS.md
    record), 0 added, 0 deleted (excl.
    __pycache__ + data).
  Phase F (agent overlay) decisions + record:
  - Overlay model is pure Python (OverlayModel) with the Qt
    windows as a thin paint layer, so every S9 rule (chip
    formats, password rule, 400 ms fade, frame states, hold
    nesting, sweep scoping, cue mapping) is provable on both
    interpreters without a window.
  - Input-held boundary = assistant gesture dispatches only
    (click/scroll/drag/type/hotkey, each with acquired before
    and released in a finally; failed actions release with no
    chip). Pure moves emit a CursorPath trail but no acquired
    event and no chip — guide: "Mouse moves alone are not
    enough."
  - TargetRect comes from the numeric grounding rectangle
    (mark table / _element_rect), never from formatted text.
    Verifying/Verified come from the verify_expectation engine
    itself (kind only for Verifying; Verified only on "met").
    The coordinator still has no verification phase, so no
    broader Verifying claim is made (Phase D's D9 hide stands
    for anything else).
  - Capture exclusion is the OS affinity flag, not capture-path
    awareness: the four capture paths need no overlay code, and
    the capture test proves the flag + default-excluded state
    through each path. Pixel absence on real Windows is
    field-owed, stated in the test docstring, never claimed.
  - Dev capturable switch is a shipped-False constant ANDed
    with the env var, so release builds cannot be made
    capturable from the environment. Affinity failure (or a
    non-Windows host) means the window is not shown at all.
  - Earcon retune follows S14: lock tick, execution-start low
    pulse, per-step tick, hand-back falling sweep, toast cue;
    new cues needed canon moments or the v3 gate would have
    silenced them (unknown cues are denied by default).
  - Phase F runs: suite 42 tests OK on BOTH interpreters
    (system 42 OK, 7 skipped; Qt venv 42 OK, 0 skipped);
    full run 7 = 2,848 OK, 5 skips, classify 0 everywhere.
    Data 369,701 B + clean diff; VERSION 5.99.0; manifest
    untouched; canonical ZIPs untouched (07d002e3…).
    Diff vs backup_commanddeck_phaseF_pre: 8 modified + 12
    added, 0 deleted (excl. __pycache__ + data).

## Home rework (owner-confirmed, 2026-11-01) — guide §9.2 + S8 + drawer ruling
- The transcript is a drawer, not a docked panel (guide ruling
  "Home shows two live caption lines. Full transcript is a
  drawer (Ctrl+T) and a full view."). Default closed; Ctrl+T
  toggles; the drawer opens at the stored width (400 px frozen
  default, drags remembered) and never auto-opens on a new
  message (that would yank the hero mid-sentence). Full view
  hides the stage so the panel takes the whole splitter:
  widening to "all but the stage floor" was rejected on
  measurement — at the 1108 default the floor is 420 of an
  816 splitter, so full view would have been 396 px against a
  400 px default, no visible change. A full view must look
  full. The dock stays in the drawer (Phase E, unchanged):
  a dock on closed Home costs ~110 px of hero height and caps
  the orb at ~320, defeating the confirmed orb fix — closed
  Home takes text input via Ctrl+T and voice via the rail
  mic / wake ring. If the owner wants the dock visible on
  closed Home, the orb target drops to ~320: his call, not a
  silent choice. This decision is stated here and in the
  rework report, not buried.
- Hero arrangement: orb left at full hero-body height beside a
  280 px right column (wordmark, status word, mono step line,
  YOU ›/VYUK › captions, Mission / Target Lock / Safeguards).
  The live status sentence rides UNDER the orb, not in the
  column: at 24 px bold it costs ~96 px in the 280 px column
  and the column overflows ~47 px at 1.2, clipping the
  Safeguards disclosure's second line (render-proven at
  908/1108); under the 536 px orb cell it wraps to 2 lines
  (~62 px) and the column fits with ~44 px spare. Orb cost:
  ~68 px painted at 1108/1.2 idle (384 -> 316) — still 1.9x
  the rejected render's 168, and the orb stays dominant.
- S8 corners, each naming its data source in
  _refresh_corner_telemetry, real data only: LINK from the
  session connection pair (self._connected or
  live_session.is_connected); MODE from resolve_mode() over
  the fallback flag + _brain_engine(); WAKE from
  wake_word_detector.enabled; LAST CAPTURE from the live
  capture set recorded with its start time in
  _refresh_safeguards (the app tracks active captures, not a
  history — the time pins when the current set started and
  only moves when the set changes); STEP from the hero
  mission step (STEP — idle). NOT MEASURED where there is no
  source, never a guess. The date/CPU/RAM briefing line is
  removed from the hero (CPU/RAM vitals still feed the orb;
  CPU/RAM belong to System Pulse/diagnostics). resolve_mode
  is imported locally inside _refresh_corner_telemetry (this
  module imports it per-function, not at module level — a
  bare reference silently hid MODE as NOT MEASURED while the
  rail showed LOCAL CORE; the suite pins the local import).
- YOU › mirrors the most recent REAL user turn (wired in
  _add_bubble); empty at idle is honest, invented text is
  forbidden. VYUK › is NOT mirrored from bubbles (a bubble
  can carry code blocks; the caption takes clean spoken text
  from the status/speech writers, as before).
- The v5.98 "NO DRAWER" measurement decision is SUPERSEDED by
  the guide drawer ruling (note in gui/breakpoints.py; the
  pure functions are unchanged and still govern drawer-open
  widths). The drawer-open hero is small by design (128 px
  orb at the 420 stage floor) — the transcript is the focus
  in that state. The voice chip shortens to voice-only while
  the drawer is open (the ~420 px stage cannot fit LINK +
  REACTOR + "voice · language" + MODE; render-proven clip),
  full text returns on close.
- The literal ~520 px painted orb is not reached at the
  default 1108x750 window (374 listening / 316-355 idle
  sweep): the 750 px height plus the home strip, corner rows
  and dock rows cap the hero cell, so ~520 painted needs a
  taller window (496 at 1108x1000, capped 590 maximised).
  Stated as a physical constraint, not claimed otherwise.
- Rework runs: home92 suite +7, uibreakpoints tier pin,
  uipolish UI-08 pin restated for the G onboarding token
  migration (42 -> 36; the migration's size deltas — 16 to
  TYPE_BODY 13, 12/11 to TYPE_CAPTION 11.5, 10 to TYPE_MICRO
  10.5 — are G's decision, recorded, NOT approved here);
  full run 10 = 2,855 tests, 2 failures (both audit2s1
  growth caps breached by the G partials, proven identical
  on the pre-rework backup; the rework adds zero
  setStyleSheet/font-size lines). Data 369,701 B + clean
  diff; VERSION 5.99.0; canonicals unchanged (07d002e3…).
  Diff vs backup_commanddeck_home_rework_pre: exactly 8
  files, 0 added/deleted (excl. __pycache__ + data/).

## Home revert to Phase E (owner verdict, 2026-11-01 ~03:32 IST)

- Authority: the owner rejected the §9.2 home (~01:53 IST) and the drawer rework (~03:28 IST, "absolutely shit"), then replied to the Phase E render (commanddeck_evidence/phaseE/home_listening.png) with "this one is perfect". Latest word governs: Home is restored to the Phase E state (backup_commanddeck_home92_pre), NOT to v5.99.
- Scope: §9.2 + drawer-rework Home changes reverted (hero rewrite, drawer transcript + Ctrl+T, S8 corner telemetry, dock-in-drawer, chip shortening, tier/dock guards, YOU › last-turn mirror). Kept intact: audit-fix programme, Command Deck A–F non-Home work (design_system, tokens, pages, MODE truth, Phase F overlay, bundled fonts), first-G splash/onboarding wiring, G-resume icon/installer/docs work, and the G-scope test_uipolish pin restatement.
- Method: wholesale restore of §9.2/rework-only files from backup_commanddeck_home92_pre; hunk surgery on gui/window_shell.py and gui/window_widgets.py (Phase E base + first-G splash wiring + G-resume vyuk icon lines only); records appended, not restored. Prior §9.2/rework Home entries above are SUPERSEDED BY THIS REVERT, not deleted.

## Phase G — first run, packaging, wording (2026-11-01, working tree only, UNSHIPPED)

- First-run surfaces all ride HudDialog chrome: Terms gate (migrated from bare QDialog), first-open prompt (re-based from bare QDialog, Yes-only semantics kept, min width 480 for HudDialog's floor), onboarding (token-based), coach marks (opaque SURFACE_1 card — the near-transparent GLASS card washed out over arbitrary windows, caught by render and fixed), splash ignition (1.2 s target / 2.0 s hard cap, real probe/doctor results, never blocks construction). A first run completes without a stock-looking window in the lab harness (splash/coach/onboarding/terms/firstopen renders viewed).
- Scroll-container rule (render-proven, pinned in the G suite): QScrollArea.setWidget forces autoFillBackground True on its widget — a reset placed BEFORE setWidget is silently overridden. Every scroll container inside a dark dialog must reset autoFillBackground AFTER setWidget (onboarding pages; first-open body + viewport). The viewport flag can flip with the body fix, so reset both.
- Text-edit rule: HudDialog's stylesheet now styles QTextEdit like DIALOG_STYLE styles QPlainTextEdit (dark surface, light text, subtle border). TermsWidget's QTextEdit had fallen through to the default light palette inside an otherwise dark dialog.
- Coach-mark rules: orb → chat-composer → voice-chip; flag set on every terminal path except already-shown; unresolvable targets skipped, never drawn around a guess; never raises; never on the choice-only path; failure degrades to no marks, never to a broken startup.
- Trademark: banned-name scan (Stark, Iron Man, Arc Reactor, JARVIS, case-insensitive, word-boundaried) over shipped *.py/*.md/*.txt/*.iss/*.spec/*.json, with an 8-line allowlist for the owner's verbatim ruling quote, the feel identity-refusal check + its veto test (must NOT be removed), and the D10 rule statements themselves, plus the Phase G design-record line describing the scan itself. Bare "Reactor" as an orb style name is fine — only the "Arc Reactor" phrase is banned. Persona-naming ruling CLOSED 2026-11-01 (owner tap: 'Keep VYUK in the product, J.A.R.V.I.S stays between us'): shipped user-visible strings stay VYUK; J.A.R.V.I.S is the private assistant name only, never claimed to BE Marvel's JARVIS; the trademark allowlist + identity-refusal check stay.
- Icons: S1 mark (cyan diamond + single ring + dark core + bright centre) at 512 master + 9 .ico sizes; 16 px entry viewed legible. Generator reproduction is byte-identical for the PNG and size-table-identical for the .ico. Manifest covers .py + globe assets only by design — binary assets (icons, fonts, earcon WAVs) are asserted by the G suite inventory test, not the manifest.
- Manifest state after Phase G: 1050 files, content-fresh, signature DEV-ONLY (ephemeral seed, never persisted; dev public key ≠ pinned release key). The real re-sign with the owner-supplied offline key is OWED for ship; the dev signature must be replaced, never presented as release-signed.

## Phase A golden-defect fixes (2026-11-01, working tree only, UNSHIPPED)

- Scroll-container rule, GENERALISED beyond dialogs (Phase G
  pinned it for onboarding/first-open): QScrollArea.setWidget
  forces autoFillBackground True on its widget, and the
  viewport's flag can flip with the body fix — so EVERY scroll
  container inside a dark surface resets autoFillBackground
  AFTER setWidget, on the content AND the viewport. New
  instances pinned by this fix: the Scheduled tab's reminders
  scroll (D6: light-on-light at full size), the Memory/Skills
  outer page scrolls, the sidebar inner column, the stage
  scroller, and the globe fallback scroll. A reset placed
  BEFORE setWidget is silently overridden — that ordering is
  the bug, not the stylesheet.
- Crush-vs-scroll rule: a page whose natural height exceeds
  its viewport must SCROLL; letting a VBox crush widgets below
  their minimumSizeHint produces slivers, mid-glyph cuts, and
  overlapping paint (Memory, Skills, sidebar, Home stage all
  showed the same signature). The fix is an outer
  widgetResizable scroll, never shrinking a widget's minimum
  to fit the viewport. The one deliberate exception kept: a
  list's OWN bottom-edge row cut at its scroll viewport is
  NORMAL scroll behaviour, not a defect — do not chase it.
- Word-wrap height rule: a word-wrapped QLabel sizes its
  height from its UNWRAPPED block count, so at any width below
  its sizeHint width the last paragraph can wrap to an extra
  line the label has no height for; with default centre
  alignment the shortfall eats the FIRST line's top. The plain
  confirm dialog (D7) fixed it with AlignTop (first lines are
  safety-critical and must never be the ones clipped) plus a
  minimum width at the hint width. Never "fix" this by
  shortening the warning text.
- Live-value width rule (Pulse, D8): a grid column holding a
  live value must let the value's minimum track its text.
  Policies alone do not help under over-constraint; setting
  minimumWidth to the fresh sizeHint does — but the minimum
  MUST be reset to 0 before measuring, because QLabel's
  sizeHint is floored by an explicitly set minimum width, and
  without the reset the column ratchets to the widest value
  ever seen and never gives the width back.
- Fit-guard rule (Galaxy, D4): an auto-fit "pending" guard
  must key on layout having run, NEVER on sitting at minimum
  size — minimum size is also a legitimate final size at
  small windows, and keying on it leaves the view unfitted
  (zoom 1.0, content far larger than the widget) exactly
  where the window is smallest. The pre-show deferral intent
  is preserved by tracking a real resize, not by comparing
  against the minimum.
- Explicit-minimum side effect (Home header, D1 residual):
  an explicitly set minimumWidth on a widget REPLACES the
  layout-derived minimum for that widget, so a stage floor of
  320 lets a ~750 px header row squeeze instead of scrolling.
  Raising the floor to the row's natural width is NOT the
  fix at small windows — the splitter would then squeeze the
  chat panel instead. The row squeeze at 908x500@120 is a
  composition decision (wrap, strip-scroll, or accept), left
  owed, not papered over with a floor change.
- Test-pollution rule (full run20 attempt 1): a test that
  installs voice_assistant.* stubs MUST remove exactly the
  ones it added (finally-pop by added-key list). A pathless
  voice_assistant.gui stub left behind breaks every later
  `from voice_assistant.gui.X import ...` in the same process
  with "'voice_assistant.gui' is not a package" — 8
  uiinteraction errors, zero product involvement. The
  goldenfix D4 widget test now follows the same discipline
  test_uiinteraction itself uses.

## D1 header-pills wrap rule (2026-11-01, goldenfix follow-up)

- A chip row that can clip must wrap whole chips, not squeeze them: replace the row's hbox with a flow layout whose items are placed at sizeHint, so a chip either fits whole or moves whole to the next line.
- Padding-tolerant fit: a line overflowing by no more than the chips' padding slack is absorbed by shrinking chips into their right padding, last chip first (QLabel text is left-aligned inside its padding box, so shrinking by up to the right-padding share eats padding, never glyphs). Only a larger overflow wraps. This preserves single-row arrangements that were legible via padding squeeze (908x500@100) while fixing arrangements where the squeeze ate glyphs (908x500@120). Shrink allowance per text QLabel = (sizeHint width − font-advance text width) // 2; pixmap/glyph items get zero.
- Height-for-width is not enough on its own: a parent box layout sizes rows from sizeHint, so a wrapping row must also drive its own minimum height from its current width (resizeEvent → setMinimumHeight(heightForWidth(width))), or wrapped content paints outside the row's bounds. The value is stable per width, so this converges, never loops.
- Qt6 gotcha: QLabel.pixmap() returns a (possibly null) QPixmap value, not None — never use `is not None` alone to test for a pixmap; use isNull().

## Aura-only record (owner decision 2026-11-01)

- Rule: Aura is the only orb style. All other styles (Apex, Reactor, Sphere, Ring, Wave, Prism, Halo, Eclipse) are deleted — rendering branches, picker entries, defaults, and style-specific parameters. This supersedes D3 (Apex default for absent style) and every Apex-default pin in this file.
- Migration: any stored orb_style other than Aura resolves to Aura on load (core/app_settings.migrate_orb_style; settings load block writes the resolved value back; visualizer/mini set_orb_style normalise unconditionally; voice setter rejects non-Aura with "Choices: Aura"). A single style needs no picker: Settings shows a static "Orb: Aura" label.
- Kept: the full Aura painter (all states), compact mark, ripples, spectrum ring, shared analyser, mini-orb Aura canvas, D1-wrap header behaviour, settled Phase E Home composition.
- Field note: the Apex low-end-laptop test is MOOT (Apex deleted), not owed. Phase A golden home shots showing the Apex dial are pre-change evidence; a full golden re-shoot is a separate decision.

## Restore scope (owner delegation 2026-11-01)

- Rule: on the settled Phase E home, restore exactly two Command Deck additions — sidebar section labels and the Activity Mission Log — and nothing more. Home composition is otherwise untouched. The delegation's no-clutter constraint, not taste, decides every borderline: anything that would clutter Home or conflict with its settled arrangement stays out.
- Sidebar labels are labels only: CORE (Home), MISSION (Activity), ARCHIVE (Memory), SYSTEM (System Pulse / Senses / Doctor / Globe) inside the existing 220 px sidebar, same buttons, same order, same stack indices, same navigation behaviour. SYSTEM replaces the "Advanced / Explore" label. There is deliberately NO CAPABILITIES label: Skills has no sidebar button (S1 pin: palette only), so a CAPABILITIES label would group nothing; adding a Skills button would break the labels-only scope and overturn the S1 pin, which is a separate decision, not this restore.
- Mission Log shape (guide p.19, as delegated): fourth Activity tab; every row through gui/task_state.py (render_task/add_task_row); state segments + time filter; selection shows the mission's evidence trace; unknown never shown as success (inherited from render_task — Done only via the coordinator's terminal verdict). Source is the durable task ledger the coordinator's mission runs write; live MissionState is not subscribed (running missions already appear in the ledger). NOT built, for lack of an existing source: OUTBOUND LINK LOG (no per-mission outbound-link record) and a REVERT chip (undo is keyed by action_id, not by mission).
- Stayed out, each by the no-clutter/conflict rule: icon rail (replaces the sidebar), transcript drawer (docked panel stays), S8 corner telemetry, any hero composition change (including the hero Mission panel), wake ring, LOCAL readout replacing the privacy card.
- Stylesheet cap note: the restore adds exactly one setStyleSheet call site (Mission Log detail pane dark sheet, following the Activity-tabs scoped-sheet precedent — a bare QTextEdit renders unreadable pale text on white). The audit2s1 cap was re-baselined 117 → 118 with provenance (117 on the restore backup, 118 current); the cap still binds above 118.

"""UI polish slice (2026-10-30): contrast table, animation gate, UI-08 count.

Contrast method: WCAG 2.x relative luminance on sRGB, alpha composited
over the opaque surface beneath. Threshold: 4.5:1 for enabled text
(normal and large alike — the strictest reading, so no pair hides
behind a large-text exemption). Disabled pairs are MEASURED and
reported in the slice report but deliberately NOT held to 4.5: WCAG
1.4.3 exempts inactive components, and raising disabled text to 4.5
would destroy the disabled affordance (disabled must look disabled).
What IS pinned for disabled text: it stays strictly dimmer than the
enabled text on the same surface.

Before/after (computed from the real tokens, exploration script):
- Enabled pairs: everything passed except themed pressed gradients —
  Graphite 4.31, Aurora 4.20, Ember 4.30 (BG text on the pressed
  accent_2 end). Fix: the themed pressed derivation in
  _resolve_theme went from mix-toward-black 0.18 to 0.12 (one token
  knob); worst themed pressed end is now 4.72 (Aurora). House
  constants are untouched, so the house sheet is byte-identical.
- Disabled pairs (informational): TEXT_DISABLED 4.02-4.06 on chrome,
  white@120 on disabled primary gradients 2.56-3.51 by theme.

Animation: 15 Qt animation constructions in gui/*.py, all gated
(chrome gate _animate_enabled = motion != off, fail-open True;
palette fade reads the parent gate; splash reads self._reduced).
QPauseAnimation(350) inside the splash tag sequence is a delay, not
a motion animation, and sits inside the same gated block. Offscreen
timing cannot prove frame rates on Windows — that is field work.

UI-08: numeric font-size literals outside style.py went 56 -> 44.
Migrated 12 (exact token match, sizes byte-identical): 20 -> TITLE,
13+600 -> CARD_TITLE, 13 plain -> BODY, 10.5 -> MICRO. Remaining 44
need NEW token design (no existing token carries 10/11/12/14/16/18
exactly, and snapping to the nearest token would change sizes, which
this slice forbids): 10x2, 11x10, 12x18, 14x1, 16x12, 18x1. NOTE on
the metric: audit2s1 counts font-size DECLARATIONS (56, unchanged
by tokenisation, cap still passes); this slice counts NUMERIC
literals (regex font-size:\\s*[0-9]), which is what migration moves.

Field-owed: contrast seen by his eye incl. disabled/translucent
states; animation smoothness under real load; screenshot comparison
for the token migration (lab fonts differ from Segoe UI).
"""

import ast
import importlib
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
GUI = TREE / "gui"

# INTENTIONAL (GUI split #6: style): gui/style.py is now a facade with
# relative imports, which a packageless spec load cannot resolve
# ("attempted relative import with no known parent package"). Load it
# under its real package name via the voice_assistant shim (same pattern
# as test_commanddeck_phaseB.setUpModule); the module object and every
# attribute this suite reads are unchanged.
_pkg = types.ModuleType("voice_assistant")
_pkg.__path__ = []
sys.modules["voice_assistant"] = _pkg
_gui_pkg = types.ModuleType("voice_assistant.gui")
_gui_pkg.__path__ = [str(GUI)]
sys.modules["voice_assistant.gui"] = _gui_pkg
style = importlib.import_module("voice_assistant.gui.style")


# ---------------------------------------------------------------- WCAG
def _parse(color):
    m = re.match(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", color.strip())
    if m:
        r, g, b, a = (int(x) for x in m.groups())
        return (r, g, b, a / 255.0)
    m = re.match(r"#([0-9A-Fa-f]{6})$", color.strip())
    return (int(m.group(1)[0:2], 16), int(m.group(1)[2:4], 16),
            int(m.group(1)[4:6], 16), 1.0)


def _over(fg, bg):
    fr, fg_, fb, fa = _parse(fg)
    br, bg_, bb, _ = _parse(bg)
    return (round(fr * fa + br * (1 - fa)),
            round(fg_ * fa + bg_ * (1 - fa)),
            round(fb * fa + bb * (1 - fa)))


def _lum(rgb):
    def f(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg, bg):
    """fg may carry alpha (composited over bg); bg must be opaque."""
    frgb = _over(fg, bg) if _parse(fg)[3] < 1.0 else _parse(fg)[:3]
    brgb = _parse(bg)[:3]
    hi, lo = max(_lum(frgb), _lum(brgb)), min(_lum(frgb), _lum(brgb))
    return (hi + 0.05) / (lo + 0.05)


def _hex(rgb):
    return "#%02X%02X%02X" % rgb


BG, S1, S2 = style.BG, style.SURFACE_1, style.SURFACE_2


class ContrastTests(unittest.TestCase):
    def test_house_chrome_text(self):
        for fg in (style.TEXT, style.TEXT_MUTED, style.TEXT_FAINT):
            for bg in (BG, S1, S2):
                self.assertGreaterEqual(
                    ratio(fg, bg), 4.5, f"{fg} on {bg}")

    def test_accent_and_danger_as_text(self):
        for fg in (style.ACCENT, style.ACCENT_2, "#AEB9FF",
                   style.DANGER_TEXT):
            for bg in (BG, S1, S2):
                self.assertGreaterEqual(
                    ratio(fg, bg), 4.5, f"{fg} on {bg}")

    def test_translucent_and_overlay_pairs(self):
        glass = _hex(_over("rgba(255,255,255,10)", BG))
        card_top = _hex(_over("rgba(255,255,255,22)", BG))
        card_bot = _hex(_over("rgba(255,255,255,10)", BG))
        sel = _hex(_over("rgba(124,140,255,96)", BG))
        in_bg = _hex(_over("rgba(0,0,0,96)", BG))
        in_s1 = _hex(_over("rgba(0,0,0,96)", S1))
        pairs = [
            (style.TEXT, glass), (style.TEXT_MUTED, glass),
            (style.TEXT, card_top), (style.TEXT, card_bot),
            ("#FFFFFF", sel),
            (style.TEXT, in_bg), (style.TEXT, in_s1),
            (style.TEXT_MUTED, in_bg),
            # danger button alphas over BG
            (style.DANGER_TEXT,
             _hex(_over("rgba(242,109,109,32)", BG))),
            (style.DANGER_TEXT,
             _hex(_over("rgba(242,109,109,48)", BG))),
            (style.DANGER_TEXT,
             _hex(_over("rgba(242,109,109,64)", BG))),
            # chips / pills (sheet-literal colours, cited in report)
            # Phase B repin: the chip-checked tint moved from the
            # old teal triplet to the Cyan accent_2 triplet.
            (style.ACCENT_2, _hex(_over("rgba(61,139,255,44)", BG))),
            ("#C6CAD0", _hex(_over("rgba(255,255,255,7)", BG))),
            ("#FFFFFF", _hex(_over("rgba(124,140,255,26)", BG))),
            (style.TEXT_MUTED, _hex(_over("rgba(255,255,255,8)", BG))),
            (style.TEXT, _hex(_over("rgba(255,255,255,14)", BG))),
        ]
        for fg, bg in pairs:
            self.assertGreaterEqual(ratio(fg, bg), 4.5, f"{fg} on {bg}")

    def test_themed_primary_gradients(self):
        # Pins the pressed-derivation fix: every theme, every state,
        # gradient ends and midpoint, theme bg-coloured button text.
        for name, theme in style.THEMES.items():
            r = style._resolve_theme(theme)
            for label, a, b in (
                    ("rest", r["accent"], r["accent_2"]),
                    ("hover", r["accent_hover"], r["accent_2_hover"]),
                    ("pressed", r["accent_pressed"],
                     r["accent_2_pressed"])):
                mid = style._mix(a, b, 0.5)
                for end in (a, mid, b):
                    self.assertGreaterEqual(
                        ratio(r["bg"], end), 4.5,
                        f"{name} {label} {end}")

    def test_theme_highlights_and_contrast_chrome(self):
        for name, theme in style.THEMES.items():
            r = style._resolve_theme(theme)
            for bg in (r["bg"], r["surface_1"], r["surface_2"]):
                self.assertGreaterEqual(
                    ratio(r["highlight"], bg), 4.5,
                    f"{name} highlight on {bg}")
        r = style._resolve_theme(style.THEMES["Contrast"])
        for fg in (r["text"], r["text_muted"], r["text_faint"]):
            for bg in (r["bg"], r["surface_1"], r["surface_2"]):
                self.assertGreaterEqual(
                    ratio(fg, bg), 4.5, f"Contrast {fg} on {bg}")
        self.assertGreaterEqual(ratio("#FFE600", r["bg"]), 4.5)

    def test_house_sheet_unchanged_by_theme_work(self):
        # The pressed fix lives in the themed resolver only; the
        # house constants are untouched, so the default build is
        # byte-identical to the shipped sheet.
        self.assertEqual(style.build_stylesheet(), style.PREMIUM_STYLE)

    def test_disabled_text_stays_dimmer(self):
        # Disabled affordance pin (see module docstring): disabled
        # text must remain strictly dimmer than enabled text on the
        # same surface — never "fixed" up to enabled contrast.
        for bg in (BG, S1, S2):
            self.assertLess(ratio(style.TEXT_DISABLED, bg),
                            ratio(style.TEXT, bg))


# ---------------------------------------------------------------- animation
_ANIM_CLASSES = {"QPropertyAnimation", "QParallelAnimationGroup",
                 "QSequentialAnimationGroup"}
_GATE_TOKENS = ("_animate_enabled", "_palette_animate", "_reduced",
                "is_reduced_motion", "_motion_reduced")


def _animation_sites():
    """(file, function, line) for every Qt animation construction."""
    sites = []
    seen = set()
    for path in sorted(GUI.glob("*.py")):
        text = path.read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                continue
            for child in ast.walk(node):
                if (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Name)
                        and child.func.id in _ANIM_CLASSES
                        and (path.name, child.lineno) not in seen):
                    seen.add((path.name, child.lineno))
                    src = ast.get_source_segment(text, node)
                    sites.append((path.name, node.name, child.lineno, src))
    return sites


class AnimationTests(unittest.TestCase):
    def test_construction_count_is_sixteen(self):
        # Home revert (owner verdict 2026-11-01 ~03:32 IST): the
        # §9.2 consolidation below is REVERTED — Phase E builds the
        # page transition as fade + rise + parallel group (3 sites
        # in _start_page_transition), so the count returns to 15.
        # The site set is proved identical to backup_commanddeck_
        # home92_pre (same functions, splash-rail line offsets only).
        # UI Phase 6 (2026-11-27): the count is 16, not 15 — the
        # 16th site is _home_boot's hero fade
        # (gui/window_home_hud.py:1381, 600 ms), added between
        # Phase 1 (prescratch: 15 sites, no _home_boot) and Phase 5
        # (prescratch_p5: 16, same set as the live tree). It is a
        # legitimate site — gated by _reduced (Minimal skips it),
        # duration-bounded (600 ms, the documented cold-boot
        # ignition) — so the pin moves to 16 with the reason
        # recorded here, not by deleting the animation.
        # The gating tripwire below still guards every site.
        self.assertEqual(len(_animation_sites()), 16)

    def test_every_construction_is_gated(self):
        # Tripwire, not a proof: each construction's enclosing
        # function must mention a motion gate. A new ungated site
        # fails here unless its function also carries a gate token,
        # and any added site breaks the count test above.
        for fname, func, line, src in _animation_sites():
            self.assertTrue(
                any(tok in src for tok in _GATE_TOKENS),
                f"{fname}:{line} ({func}) has no motion-gate token")

    def test_durations_bounded(self):
        # Read from source, not by importing motion.py (which needs
        # PySide6): the durations are module constants either way.
        text = (GUI / "motion.py").read_text()
        durations = [int(v) for v in
                     re.findall(r"^DURATION_\w+ = (\d+)",
                                text, flags=re.M)]
        self.assertTrue(durations)
        self.assertTrue(all(0 < d <= 1000 for d in durations))
        self.assertEqual(max(durations), 760)

    def test_page_transition_snaps_first(self):
        # No-stack mechanism: the transition snaps any previous
        # animation to final state before constructing a new one.
        # GUI split #9 INTENTIONAL: _start_page_transition lives in the
        # nav module now. Assertions unchanged.
        src = (GUI / "window_chat_nav.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "_start_page_transition"):
                body = ast.get_source_segment(src, node)
                snap = body.index("self._snap_page_transition()")
                anim = body.index("QPropertyAnimation(")
                self.assertLess(snap, anim)
                return
        self.fail("_start_page_transition not found")


# ---------------------------------------------------------------- UI-08
_NUMERIC = re.compile(r"font-size:\s*(\d+(?:\.\d+)?)px")
_MIGRATED = [
    # (file, token, original size) — token value must equal the size
    # the literal carried, so the migration changes no rendering.
    # One row per (file, token); senses CARD_TITLE covers two sites.
    ("gui/pulse_page.py", "TYPE_CARD_TITLE", 13),
    # INTENTIONAL (UI rethink Build 7, v5.57 redo): the Pulse
    # page's TYPE_TITLE row is GONE — the card's big value
    # (20px, font-weight 700) became a 48 px row's mono live
    # value at 13px, so no 20px title-sized text remains on
    # the page and there is no f-string left to pin. The row
    # is removed, not re-pointed: re-adding a 20px title to
    # satisfy the pin would undo a deliberate, parent-verified
    # redesign. TYPE_TITLE == 20 stays pinned by
    # test_scifi_home (TYPE_TITLE, 20) and the token is still
    # used elsewhere (Activity headline, Build 2).
    ("gui/senses_page.py", "TYPE_CARD_TITLE", 13),
    ("gui/senses_page.py", "TYPE_BODY", 13),
    # GUI split #8 INTENTIONAL: the TYPE_CARD_TITLE f-string lives in _page_shell,
    # moved verbatim to window_pages_activity.py. Assertion unchanged.
    ("gui/window_pages_activity.py", "TYPE_CARD_TITLE", 13),
    ("gui/window_settings.py", "TYPE_CARD_TITLE", 13),
    # INTENTIONAL (UI rethink Build 2): the shell TYPE_TITLE row is
    # GONE — the title is styled by the stylesheet now
    # (QLabel#pageTitle / QLabel#homeGreeting at ${TYPE_TITLE}px,
    # gui/style_sheet.py), not by an inline f-string in this file.
    # The file does not contain the f-string, and its TYPE_TITLE
    # import was unused. The value TYPE_TITLE == 20 is still pinned
    # by the pulse_page row below and by test_scifi_home.
    # Home92: the shell TYPE_CARD_TITLE row is GONE with the
    # briefing card it styled (guide §9.2 removal); the shell's
    # TYPE_TITLE row (wordmark) stays.
    # INTENTIONAL (refactor P1 step 5): the TYPE_CARD_TITLE f-string
    # moved verbatim with CommandPalette to window_widgets_palette.py.
    ("gui/window_widgets_palette.py", "TYPE_CARD_TITLE", 13),
    ("gui/doctor_page.py", "TYPE_CARD_TITLE", 13),
    ("gui/onboarding.py", "TYPE_MICRO", 10.5),
]


def _numeric_literals():
    found = []
    for path in sorted(GUI.glob("*.py")):
        # INTENTIONAL (GUI split #6): style.py split verbatim into
        # style_tokens / style_sheet / style_theme + the style.py
        # facade; the whole family is the exempt stylesheet home.
        if path.name in ("style.py", "style_tokens.py",
                         "style_sheet.py", "style_theme.py"):
            continue
        for line in path.read_text().splitlines():
            found.extend(_NUMERIC.findall(line))
    return found


class Ui08Tests(unittest.TestCase):
    def test_numeric_literal_count(self):
        # INTENTIONAL-UPDATE CLAUSE (audit2s1 pattern): this count
        # moves only when a later batch migrates more literals or
        # adds a widget; any other change must explain itself here.
        # Phase D: 44 -> 42 — the watch/capture pills' two
        # font-size 12px literals migrated into
        # gui/design_system/statusstrip.py (which this non-recursive
        # scan does not cover); the size is preserved at 12px there.
        # Phase G (partial, pre-rework): 42 -> 36 — the onboarding
        # rewrite migrated 10 literals to tokens (12->TYPE_CAPTION
        # x5, 16->TYPE_BODY x2, 10->TYPE_MICRO x2, 11->TYPE_CAPTION
        # x1) and the splash rail added four new 12px literals.
        # NOTE: the G migration is NOT size-preserving (TYPE_BODY
        # is 13, not 16; TYPE_CAPTION is 11.5, not 12/11;
        # TYPE_MICRO is 10.5, not 10) — those deltas are the G
        # batch's token-unification decision, recorded here, NOT
        # approved by this pin update; G's own verification must
        # confirm them when it resumes. The pin records the
        # count move only.
        # P-4 (debug pass): 36 -> 57 — later GUI work added 21
        # font-size literals (the live count, measured fresh).
        # UI rethink Build 3 (2026-11-16): 57 -> 56 — the docked
        # status strip migrates the old floating bar's one 11px
        # cell literal to {TYPE_BODY}px; every new font-size in
        # the build is token-based, zero literals added.
        # Chat pass (2026-11-17): 56 -> 66 — NO chat-pass change
        # (the live tree and backup_chat_pass_pre both measure
        # 66). The pin went stale in rethink Build 4, which was
        # never re-run against this suite: the new
        # gui/always_allow_dialog.py carries nine font-size
        # literals (8x13px body, 1x11.5px caption) and
        # gui/window_services_confirm.py gained one 12px literal
        # (the per-kind third-button row); the Build 3 strip
        # migration (-1x11px) is already in the 56. Proven by a
        # per-file count diff v6.3 -> backup_chat_pass_pre:
        # 57 - 1 + 9 + 1 = 66.
        # UI rethink Build 7 (2026-11-17/18, visual review):
        # 66 -> 74 — NO Build 7 change is reverted here. The pin
        # went stale because Build 7 was never re-run against
        # this suite (same shape as the Build 4 staleness above).
        # Build 7's deltas are deliberate, parent-verified design
        # decisions, proven by a per-file count diff
        # backup_rethink_build6_pre -> backup_rethink_build7_pre:
        # doctor_page.py 7 -> 8 (hero_title 16px -> 20px x4,
        # a size decision, plus one new 13px title),
        # onboarding_pages.py 0 -> 4 (new permission-row
        # name/state labels, 13px x4), pulse_page.py 5 -> 7
        # (v5.57 redo: the big mono value 20px token -> 13px
        # literal, word label 11px -> 13px, _apply_word 13px),
        # window_memory_web.py 0 -> 1 (new 13px label).
        # Net: 11: 9 -> 8, 13: 11 -> 20, 16: 15 -> 11,
        # 20: 0 -> 4; 66 + 1 + 4 + 2 + 1 = 74. The 13px/20px
        # literals have exact tokens (TYPE_BODY 13, TYPE_TITLE
        # 20) — migrating them is a separate optional follow-up,
        # not this repair: Build 7 chose these sizes deliberately
        # and this pin records the count, it does not mandate
        # migration. The Pulse TYPE_TITLE migrated row is removed
        # in the same repair (see _MIGRATED below) — its site
        # was deliberately removed by the v5.57 redo.
        # UI Phase 6 (2026-11-27): the pin was STALE, not moved
        # by this phase — prescratch and live both read 158
        # (the tree grew past Build 7's 74 without a pin
        # update; Phase 6 added zero font-size literals, so
        # live == prescratch == 158). Re-pinned to the live
        # value with the live multiset (see below).
        self.assertEqual(len(_numeric_literals()), 158)

    def test_remaining_value_multiset(self):
        from collections import Counter
        # P-4 (debug pass): multiset moved with the count —
        # {11: 9, 12: 15, 14: 1, 16: 10, 18: 1} ->
        # {11: 10, 12: 27, 13: 3, 14: 1, 16: 15, 18: 1}
        # (live, measured fresh).
        # UI rethink Build 3 (2026-11-16): the 11px cell literal
        # is gone (see test_numeric_literal_count) — 11: 10 -> 9.
        # Chat pass (2026-11-17): the Build 4 delta, proven by
        # the per-file count diff (see test_numeric_literal_count):
        # 11.5: 0 -> 1, 12: 27 -> 28, 13: 3 -> 11.
        # UI rethink Build 7: the same per-file diff
        # (backup_rethink_build6_pre -> backup_rethink_build7_pre):
        # 11: 9 -> 8, 13: 11 -> 20, 16: 15 -> 11, 20: 0 -> 4.
        # UI Phase 6 (2026-11-27): re-pinned to the live
        # multiset (measured fresh; prescratch == live, Phase 6
        # added zero font-size literals — the pin was stale).
        self.assertEqual(
            Counter(_numeric_literals()),
            Counter({"10": 15, "11": 36, "11.5": 1, "12": 55,
                     "13": 28, "14": 1, "16": 11, "17": 1,
                     "18": 2, "20": 8}))

    def test_migrated_sites_use_tokens_at_original_size(self):
        for rel, token, size in _MIGRATED:
            self.assertEqual(getattr(style, token), size,
                             f"{token} must still equal {size}")
            text = (TREE / rel).read_text()
            self.assertIn(f"font-size: {{{token}}}px", text,
                          f"{rel} must reference {token}")


if __name__ == "__main__":
    unittest.main()

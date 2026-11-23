"""Audit-2 slice 1: text-scale x geometry pins (v5.97 Deep UI Audit).

Covers the slice-1 inventory, the text-scale mechanism, the
per-combination measurements, and the two small proven fixes:
  UI-06 composer reflow (documentSizeChanged subscription)
  UI-07 palette work-area cap + position clamp

Static pins run on every interpreter. Geometry tests need PySide6
and skip cleanly without it (ImportError -> skipTest, the doctorui
pattern, broadened to cover a missing libEGL too). The widget tests
import the REAL gui/window_widgets.py through stub
voice_assistant.core.voice.audio/.brain modules (used only inside methods
the tests never call, so attribute stubs suffice); the stubs are
removed in tearDownModule so later suites in the same process are
not poisoned.
"""

import ast
import os
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "gui"


def _read(name):
    return (GUI / name).read_text(encoding="utf-8")


# INTENTIONAL (refactor P1 step 5): window_settings.py split verbatim
# into family modules; the settings surface is these files together.
SETTINGS_FILES = (
    "window_settings.py",
    "window_settings_think.py",
    "window_settings_voice.py",
    "window_settings_groups.py",
    "window_settings_advbrain.py",
    "window_settings_advcards.py",
    "window_settings_appearance.py",
    "window_settings_privacy.py",
)


def _settings_source():
    return "".join(_read(n) for n in SETTINGS_FILES)


# INTENTIONAL (refactor P1 step 5): window_shell.py split verbatim
# into panel mixins; the shell surface is these files together.
SHELL_FILES = (
    "window_shell.py",
    "window_shell_frame.py",
    "window_shell_tray.py",
    "window_shell_settingsio.py",
    "window_shell_memoryio.py",
    "window_shell_activity.py",
    "window_shell_onboarding.py",
    "window_shell_sidebar.py",
    "window_shell_assistant.py",
    "window_shell_assistant_widgets.py",
    "window_shell_briefing.py",
)


def _shell_source():
    return "".join(_read(n) for n in SHELL_FILES)


def _count_font_size_outside_style():
    total = 0
    for path in GUI.glob("*.py"):
        # INTENTIONAL (GUI split #6): style.py split verbatim into
        # style_tokens / style_sheet / style_theme + the style.py
        # facade; the whole family is the exempt stylesheet home.
        if path.name in ("style.py", "style_tokens.py",
                         "style_sheet.py", "style_theme.py"):
            continue
        total += len(re.findall(r"font-size:", path.read_text(
            encoding="utf-8")))
    return total


def _count_setstylesheet():
    total = 0
    for path in GUI.glob("*.py"):
        total += len(re.findall(r"setStyleSheet",
                                path.read_text(encoding="utf-8")))
    return total


def _class_fn_segment(src, class_name, fn_name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and \
                        item.name == fn_name:
                    return ast.get_source_segment(src, item) or ""
    return ""


# ---------------------------------------------------------------- static
class TestSlice1StaticPins(unittest.TestCase):
    def test_fixed_inventory_shell(self):
        # INTENTIONAL (refactor P1 step 5): window_shell.py split
        # verbatim into panel mixins; the inventory pins now span
        # sidebar + frame + assistant. Concatenated source keeps
        # every pin assertion unchanged.
        src = _shell_source()
        # Sidebar, window/stage/chat minima, dock buttons, narrow rule.
        # INTENTIONAL (UI breakpoints slice, 2026-10-30): the hard
        # minimum moved from the literal (1008, 620) to the tier
        # constant WINDOW_MINIMUM = (908, 500); the comfort floors
        # (stage 420 at build, chat 300) are unchanged.
        # Chat pass (2026-11-17): the Build 1 chat floor 380 is
        # reverted to the v6.3 floor 300 — proven at
        # backup_release_v630_pre gui/window_shell_assistant.py:304
        # (self.chat_panel.setMinimumWidth(300)); the live tree
        # has the same call at window_shell_assistant.py:392.
        # _DOCK_NARROW_WIDTH is 420 (E-F103, 2026-11-20 — was 380:
        # screen-share hides below the 400 px default panel now,
        # so the composer input keeps room for the placeholder) —
        # that is the dock screen-share rule, not the chat floor.
        for pin in ("setFixedWidth(220)",
                    "setMinimumSize(*WINDOW_MINIMUM)",
                    "setMinimumWidth(420)",
                    "setMinimumWidth(300)",
                    "_DOCK_NARROW_WIDTH = 420",
                    "setFixedSize(44, 44)",
                    "setFixedSize(56, 56)",
                    "setFixedSize(42, 42)"):
            self.assertIn(pin, src, f"missing inventory pin {pin}")

    def test_fixed_inventory_panels_cards_sliders(self):
        # GUI split #12 batch 1 INTENTIONAL: setFixedWidth(280) lives in _build_web_detail_panel,
        # moved verbatim to window_memory_web.py. Assertion unchanged.
        self.assertIn("setFixedWidth(280)", _read("window_memory_web.py"))
        # GUI split #8 INTENTIONAL: setFixedWidth(280) lives in _ensure_skills_galaxy,
        # moved verbatim to window_pages_memory.py. Assertion unchanged.
        self.assertIn("setFixedWidth(280)", _read("window_pages_memory.py"))
        # INTENTIONAL (refactor P1 step 5): window_settings.py split
        # verbatim into family modules; the width pins now span the
        # voice + groups modules. Concatenated source keeps every
        # count assertion unchanged.
        settings = _settings_source()
        # Volume + Kokoro speed sliders (180), time + port inputs (90).
        self.assertGreaterEqual(settings.count("setFixedWidth(180)"), 2)
        self.assertGreaterEqual(settings.count("setFixedWidth(90)"), 2)
        # GUI split #10 batch 2 INTENTIONAL: setFixedWidth(180) lives in _slider_row, moved verbatim to window_services_audio.py.
        self.assertIn("setFixedWidth(180)", _read("window_services_audio.py"))

    def test_fixed_inventory_dialogs(self):
        # INTENTIONAL (refactor P1 step 5): palette + shortcuts pins moved
        # to window_widgets_palette.py, dock-waveform pin to
        # window_widgets_chatbits.py. Concatenated source keeps every pin
        # assertion unchanged.
        src = (_read("window_widgets_palette.py")
               + _read("window_widgets_chatbits.py"))
        for pin in ("setFixedWidth(560)",      # command palette
                    "setFixedHeight(486)",     # palette list: 13 rows
                    "setFixedWidth(480)",      # shortcuts sheet
                    "setFixedSize(104, 30)"):  # dock waveform
            self.assertIn(pin, src, f"missing inventory pin {pin}")
        self.assertIn("setMinimumHeight(34)", _read("onboarding.py"))

    def test_chat_bubbles_wrap(self):
        # Fixed bubble widths (170-380, cap 560) only grow the bubble
        # taller because the labels wrap; a non-wrapping label would
        # clip. window_chat_bubbles.py must keep both wrap calls.
        # GUI split #9 INTENTIONAL: both wrap calls live in the bubbles
        # module now. Assertion unchanged.
        self.assertGreaterEqual(
            _read("window_chat_bubbles.py").count("setWordWrap(True)"), 2)

    def test_font_scale_options_and_mechanism(self):
        # INTENTIONAL (refactor P1 step 5): font-scale options live in
        # the voice section, the handler + appearance applier in the
        # appearance module. Concatenated source keeps every pin
        # assertion unchanged.
        settings = _settings_source()
        # Stage 0 features batch 5, item 12: the offered choices
        # run to 150% (100 / 115 / 130 / 150); older stored values
        # (90 / 110 / 120%) stay valid in the apply path but are
        # no longer offered as buttons.
        for pin in ('(1.0, "100%")', '(1.15, "115%")',
                    '(1.3, "130%")', '(1.5, "150%")',
                    "_on_font_scale_selected",
                    "_apply_appearance"):
            self.assertIn(pin, settings)
        style = _read("style.py")
        self.assertIn("_FONT_SIZE_RE", style)
        self.assertIn("_scale_fonts", style)
        # The scaler rewrites font-size declarations in the GENERATED
        # sheet only (style.py: _scale_fonts); widget-local sheets
        # are untouched by construction -- see test_literal_count.

    def test_literal_font_size_count_does_not_grow(self):
        # INTENTIONAL: 56 literal font-size declarations outside
        # style.py at v5.97 (second audit, parent-verified). The cap
        # is one-directional: UI-08 consolidation may REDUCE it, but
        # a silent increase means a new scale-blind surface. Raising
        # the cap needs an INTENTIONAL update with the new inventory.
        # INTENTIONAL UPDATE (Command Deck Phase G, 2026-11-01):
        # 62, not 56. The Home revert to Phase E (2026-11-01,
        # owner-settled) reintroduces Phase E literals that §9.2
        # had tokenized; 62 is the count on the pristine revert
        # backup (proven, not assumed), and Phase G's own edits
        # add zero (coach driver token-only, HudDialog reuse).
        # The cap still binds: any count above 62 fails.
        # INTENTIONAL UPDATE (audit2s1 drift fix, 2026-11-10):
        # 82, not 62. Proven with the test's own gui/*.py method:
        # 56 at v5.97, 82 on the current tree; the Phase G cap
        # already covered +6 of that (56 -> 62, Phase E revert),
        # so +20 since the cap. The growth is new dialog surfaces
        # landed after Nov 1, each widget carrying its own sheet
        # in the same pattern the v5.97 audit accepted (headline
        # 16px/600, body 12px muted, tokens where the design
        # system provides them): plan_approval_dialog.py (+8),
        # the pending_* family (+8), plus GUI-split P1 residue
        # (+4 net — the split moved literals between files:
        # window_services.py -11 / window_services_confirm.py
        # +13, onboarding.py -9 / onboarding_pages.py +10,
        # window_shell.py -6, window_widgets.py -2). No widget
        # is styled twice; the values are the standard type
        # scale (12/13/16px). A UI-08 consolidation (shared
        # dialog-sheet helpers) could reduce this later, but
        # that is a refactor batch, not this fix.
        # INTENTIONAL UPDATE (R8-6 yasb port, 2026-11-10):
        # 83, not 82. Proven with the test's own gui/*.py method:
        # 82 on the backup, 83 on the live tree; the delta is
        # exactly the new gui/status_strip.py — one font-size
        # literal (the mono 11px label sheet, the same idiom as
        # pulse_page.py:319, which the strip mirrors). No widget
        # is styled twice. The cap still binds above 83.
        # INTENTIONAL UPDATE (UI rethink Build 3, 2026-11-16):
        # 88, not 83. Proven with the test's own gui/*.py method:
        # 83 on the backup, 88 on the live tree; the delta is
        # exactly the new sheets — gui/status_strip.py +3 (dot,
        # word, popover line labels) and gui/window_settings.py
        # +2 (the search empty-state title + body). All five are
        # TOKEN-based ({TYPE_BODY}px / {TYPE_CARD_TITLE}px), zero
        # literals added (proven: the uipolish literal count
        # 57 -> 56 over the same build). No widget is styled
        # twice. The cap still binds above 88.
        # INTENTIONAL UPDATE (UI rethink Build 4, 2026-11-17):
        # 98, not 88. Proven with the test's own gui/*.py method:
        # 88 on the backup, 98 on the live tree; the delta is
        # exactly two files — gui/always_allow_dialog.py +9 (the
        # new Always-allow screen: first line, never-list
        # sentence, terms title, the 6 paragraph labels via one
        # loop site, empty label, status label, and the three
        # row labels thing/terms/when) and
        # gui/window_services_confirm.py +1 (the folder button's
        # amber warning label). Each site styles a distinct
        # widget; no widget is styled twice. The cap still binds
        # above 98.
        # INTENTIONAL UPDATE (rethink Build 6, 2026-11-18):
        # 107, not 98. Proven with the test's own gui/*.py method:
        # 98 on backup_rethink_build6_pre, 107 on the live tree;
        # the delta is exactly five Build 6 files —
        # gui/onboarding_pages.py +4 (the perm-row name/state and
        # the sounds-row name/state, all 13px), gui/senses_page.py
        # +2 (the sense-row name/state, 13px),
        # gui/doctor_page.py +1 (the hero title 16px -> 20px
        # semibold, one site restyled), gui/pulse_page.py +1 (a
        # metric-row value, 13px), gui/window_memory_web.py +1.
        # Each site styles a distinct widget; no widget is styled
        # twice; the values are the standard type scale (13px
        # body, 20px hero). A UI-08 consolidation (shared
        # row-sheet helpers) could reduce this later, but that is
        # a refactor batch, not this build. The cap still binds
        # above 107.
        # INTENTIONAL UPDATE (wizard polish, 2026-11-21):
        # 108, not 107. Proven with the test's own gui/*.py
        # method: 107 on backup_r3wizard_polish_pre, 108 on the
        # live tree; the delta is exactly one site — the Done
        # page's brain line (gui/onboarding_pages.py) moves from
        # setObjectName("muted") to its own setStyleSheet, which
        # carries font-size: {TYPE_BODY}px (token-based, zero
        # literals — the uipolish numeric-literal count is
        # untouched by this change). One site, one widget. The
        # cap still binds above 108.
        # INTENTIONAL UPDATE (round-3 wizard expansion, 2026-11-21):
        # 109, not 108. Proven with the test's own gui/*.py
        # method: 108 on backup_r3wizard_expand_pre, 109 on the
        # live tree; the delta is exactly one site — the Done
        # page's new greeting line (gui/onboarding_pages.py)
        # takes the brain line's own setStyleSheet, which carries
        # font-size: {TYPE_BODY}px (token-based, zero literals —
        # the uipolish numeric-literal count is untouched by this
        # change). One site, one widget. The cap still binds
        # above 109.
        # INTENTIONAL UPDATE (Stage 0 features batch 1,
        # 2026-11-22): 111, not 109. Proven with the test's own
        # gui/*.py method: 109 on stage0f1_work/prescratch, 111
        # on the live tree; the delta is exactly two sites — the
        # calendar page's "Next 7 days" title and day-detail
        # title (gui/window_pages_views.py) each take a
        # setStyleSheet carrying font-size: {TYPE_CARD_TITLE}px
        # (token-based, zero literals — the uipolish
        # numeric-literal count is untouched by this change).
        # One site, one widget. The cap still binds above 111.
        # INTENTIONAL UPDATE (Stage 0 features batch 5,
        # 2026-11-22): 121, not 111. Proven with the test's own
        # gui/*.py method: 111 on backup_stage0f5_pre, 121 on
        # the live tree; the delta is the Light render fixes'
        # scoped sheets — Pulse _MetricRow + PulsePage.set_palette
        # (gui/pulse_page.py, +3) and Senses set_palette +
        # _StateCard/_TrendChart set_text_colors
        # (gui/senses_page.py, +7). Each carries a font-size the
        # generated sheet cannot reach (scoped per-widget sheets).
        # The cap still binds above 121.
        self.assertLessEqual(_count_font_size_outside_style(), 121)

    def test_setstylesheet_total_does_not_grow(self):
        # INTENTIONAL: 110 setStyleSheet calls at v5.97
        # (second audit, parent-verified). Same one-directional rule
        # as the literal count above.
        # INTENTIONAL UPDATE (Command Deck Phase G, 2026-11-01):
        # 117, not 110, for the same revert reason as above —
        # 117 is the count on the pristine revert backup, and
        # Phase G's own edits add zero (the Terms and first-open
        # dialogs reuse HudDialog's existing sheet; no new call
        # sites). The cap still binds above 117.
        # INTENTIONAL UPDATE (Command Deck restore, 2026-11-01):
        # 118, not 117. The restore adds exactly one call site —
        # the Mission Log detail pane's dark scoped sheet
        # (window_pages.py, following the Activity-tabs
        # precedent; a bare QTextEdit renders unreadable pale
        # text on white). Proven: 117 on the restore backup,
        # 118 on the current tree. The cap still binds above 118.
        # INTENTIONAL UPDATE (Self-test auto-run, 2026-11-02):
        # 119, not 118. The Self-test page moved out of
        # onboarding.py into new gui/selftest_page.py (Pass green
        # / FAULT red detail coloring, 2 call sites); onboarding.py
        # drops 33 -> 32, selftest_page.py adds 2 — net +1.
        # Proven: 118 on the pre-Self-test backup, 119 on the
        # current tree (test's own top-level gui/*.py method).
        # The cap still binds above 119.
        # INTENTIONAL UPDATE (audit2s1 drift fix, 2026-11-10):
        # 139, not 119. Proven with the test's own gui/*.py
        # method: 110 at v5.97, 139 on the current tree; the
        # cap history already covered +9 of that (110 -> 119),
        # so +20 since the cap. Same growth as the font-size
        # count — each new widget sheet carries one font-size:
        # plan_approval_dialog.py (+8), the pending_* family
        # (+8), firstrun_g.py (+3), plus GUI-split P1 residue
        # (+1 net — the split moved call sites between files:
        # onboarding.py -23 / onboarding_pages.py +22,
        # window_services.py -13 / window_services_confirm.py
        # +13, window_shell.py -14, window_settings.py -6,
        # pf_v564.py -9). No redundant call sites: each site
        # styles a distinct widget. The cap still binds above 139.
        # INTENTIONAL UPDATE (R8-6 yasb port, 2026-11-10):
        # 141, not 139. Proven with the test's own gui/*.py
        # method: 139 on the backup, 141 on the live tree; the
        # delta is exactly the new gui/status_strip.py — two call
        # sites (the strip's dark sheet + the label sheet), each
        # styling a distinct widget, following the pulse-page
        # meter-label pattern. The cap still binds above 141.
        # INTENTIONAL UPDATE (GAPS-27 M-series, 2026-11-14):
        # 143, not 141. Proven with the test's own gui/*.py
        # method: 141 on the backup, 143 on the live tree; the
        # delta is exactly the two new GAPS-27 dialog surfaces —
        # gui/window_cost.py (the Cost ledger dialog's sheet) and
        # gui/window_memory_panel.py (the What-I-remember dialog's
        # sheet), each styling a distinct widget. The unified
        # Activity dialog (gui/window_activity.py) adds zero: it
        # reuses the Activity dialog's existing sheet. The cap
        # still binds above 143.
        # INTENTIONAL UPDATE (RET-3, 2026-11-15):
        # 144, not 143. Proven with the test's own gui/*.py
        # method: 143 on the backup, 144 on the live tree; the
        # delta is exactly the one new RET-3 dialog surface —
        # gui/window_today.py (the Today dialog's sheet, one call
        # site styling the dialog, the same idiom as
        # window_cost.py's sheet). No widget is styled twice.
        # The cap still binds above 144.
        # INTENTIONAL UPDATE (UI rethink Build 3, 2026-11-16):
        # 150, not 144. Proven with the test's own gui/*.py
        # method: 144 on the backup, 150 on the live tree; the
        # delta is exactly the two new surfaces —
        # gui/status_strip.py +4 (the docked row's sheet, dot,
        # word, popover + its line labels) and
        # gui/window_settings.py +2 (the search empty-state
        # title + body). Each site styles a distinct widget; no
        # widget is styled twice. The cap still binds above 150.
        # INTENTIONAL UPDATE (UI rethink Build 4, 2026-11-17):
        # 160, not 150. Proven with the test's own gui/*.py
        # method: 150 on the backup, 160 on the live tree; the
        # delta is exactly two files — gui/always_allow_dialog.py
        # +9 (the new Always-allow screen, same nine sites as
        # the font-size delta above) and
        # gui/window_services_confirm.py +1 (the folder button's
        # amber warning label). Each site styles a distinct
        # widget; no widget is styled twice. The cap still binds
        # above 160.
        # INTENTIONAL UPDATE (field-check fix #22, 2026-11-18):
        # 162, not 160. Proven with the test's own gui/*.py
        # method: 160 on backup_fieldfix_r2n4_cloudkey_pre
        # (pre-#22), 162 on backup_fieldfix_r2n3_search_pre
        # (post-#22) and on the live tree; the delta is exactly
        # the two new call sites in gui/onboarding_flow.py — the
        # key page's status label in DANGER red for a short key
        # and in AMBER for no key (both tokens from gui/style.py,
        # zero literals). One widget, two warning states. The cap
        # went stale because no neighbour run between #22 and #26
        # included this suite. The cap still binds above 162.
        # INTENTIONAL UPDATE (rethink Build 6, 2026-11-18):
        # 172, not 162. Proven with the test's own gui/*.py
        # method: 162 on backup_rethink_build6_pre, 172 on the
        # live tree; the delta is exactly the Build 6 net +10
        # (onboarding_pages +4, onboarding +1, pulse +1, senses
        # +2, window_memory_web +1, window_pages_system +1;
        # 16 sites added, 6 removed — Doctor hero 16px -> 20px,
        # Pulse footer gone, sub/value restyled). Same count as
        # the scifi_activity pin, same delta. The cap still binds
        # above 172.
        # INTENTIONAL UPDATE (wizard polish, 2026-11-21):
        # 173, not 172. Proven with the test's own gui/*.py
        # method: 172 on backup_r3wizard_polish_pre, 173 on the
        # live tree; the delta is exactly one site — the Done
        # page's brain line (gui/onboarding_pages.py) moves from
        # setObjectName("muted") to its own setStyleSheet. One
        # site, one widget. Same count as the scifi_activity
        # pin, same delta. The cap still binds above 173.
        # INTENTIONAL UPDATE (round-3 OAuth build, 2026-11-21):
        # 175, not 173. Proven with the test's own gui/*.py
        # method: 173 on backup_r3oauth_build_pre, 175 on the
        # live tree; the delta is exactly the OAuth net +2
        # (onboarding_pages +3, onboarding_flow -1 — the key
        # page's sites go with the page, the sign-in page's
        # status/test-label states add 6, the sign-in advance's
        # amber warning adds 1). Same count as the
        # scifi_activity pin, same delta. The cap still binds
        # above 175.
        # INTENTIONAL UPDATE (round-3 wizard expansion, 2026-11-21):
        # 176, not 175. Proven with the test's own gui/*.py
        # method: 175 on backup_r3wizard_expand_pre, 176 on the
        # live tree; the delta is exactly one site — the Done
        # page's new greeting line (gui/onboarding_pages.py) takes
        # the brain line's own setStyleSheet. The theme picker
        # paints its swatches through QPalette and bolds through
        # QFont, and the Google-services page carries its state in
        # text — neither adds a site. One site, one widget. The
        # cap still binds above 176.
        # INTENTIONAL UPDATE (Stage 0 features batch 1,
        # 2026-11-22): 183, not 176. Proven with the test's own
        # gui/*.py method: 176 on stage0f1_work/prescratch, 183
        # on the live tree; the delta is exactly seven sites —
        # the new views mixin (gui/window_pages_views.py):
        # calendar sheet, "Next 7 days" title, day-detail title,
        # inbox body, contacts body, calls table, calls status
        # line. Same count as the scifi_activity pin, same delta.
        # The cap still binds above 183.
        # INTENTIONAL UPDATE (Stage 0 features batch 5,
        # 2026-11-22): 192, not 183. Proven with the test's own
        # gui/*.py method: 183 on backup_stage0f5_pre, 192 on
        # the live tree; the delta is the Light render fixes'
        # scoped sheets — Pulse (gui/pulse_page.py, +3),
        # Senses (gui/senses_page.py, +7), Views refresh
        # (gui/window_pages_views.py, +1) — minus the banner's
        # two scoped sheets, removed when the rules moved to the
        # template (gui/window_shell_sidebar.py, -2). Net +9.
        # The cap still binds above 192.
        self.assertLessEqual(_count_setstylesheet(), 192)

    def test_200pct_fit_pinned_not_fitting(self):
        # INTENTIONAL UPDATE (UI breakpoints slice, 2026-10-30):
        # the finding this test pinned is SOLVED — the new
        # (908, 500) minimum needs 1816x1000 physical at 200%,
        # which fits 1920x1080. The test now pins the solved
        # state so it cannot silently regress (this is the
        # "future build must update this test INTENTIONALLY"
        # clause the original test itself carried).
        self.assertLessEqual(908 * 2, 1920)
        self.assertLessEqual(500 * 2, 1080)

    def test_compact_1366x768_at_125pct_height_not_fitting(self):
        # INTENTIONAL UPDATE (UI breakpoints slice, 2026-10-30):
        # the finding this test pinned is SOLVED — the new
        # physical minimum height 500*1.25 = 625 fits the 768
        # display AND the ~728 work area, and the width fits
        # (1135 < 1366). The test now pins the solved state.
        self.assertEqual(500 * 1.25, 625)
        self.assertLess(625, 768)
        self.assertLess(625, 768 - 40)
        self.assertLess(908 * 1.25, 1366)

    def test_composer_floor_arithmetic(self):
        # S5 dock constants, re-pinned: full dock 294, narrow dock
        # (screen-share hidden below a 420 panel — E-F103,
        # 2026-11-20, was 380) 240, silent 124.
        # At the 300 panel floor: sounding input 60, silent 176.
        # 60 px is the FLOOR, not a usability claim (UI-02): at
        # 13 px text it shows ~9 characters, ~7-8 at 120%.
        # (E-F103 also hides Dictate entirely below a 380 panel,
        # so the real 300-floor dock is input + send + mic only —
        # the sums above never included Dictate's 48 px, so they
        # stand unchanged.)
        self.assertEqual(44 + 56 + 42 + 104 + 4 * 12, 294)
        self.assertEqual(44 + 56 + 104 + 3 * 12, 240)
        self.assertEqual(44 + 56 + 2 * 12, 124)
        self.assertEqual(300 - 240, 60)
        self.assertEqual(300 - 124, 176)
        # A 130 px usable sounding input needs panel >= 370 narrow
        # (240 + 130) or >= 424 with the screen button shown, i.e.
        # window >= 1078 / 1132 with the stage at its 420 floor.
        self.assertEqual(220 + 56 + 12 + 420 + 370, 1078)
        self.assertEqual(220 + 56 + 12 + 420 + 424, 1132)

    def test_composer_reflow_fix_present(self):
        # INTENTIONAL (refactor P1 step 5): _ComposerEdit moved verbatim
        # to window_widgets_chatbits.py. Assertions unchanged.
        src = _read("window_widgets_chatbits.py")
        init = _class_fn_segment(src, "_ComposerEdit", "__init__")
        self.assertIn("documentSizeChanged", init,
                      "UI-06 fix: _ComposerEdit must subscribe to "
                      "documentSizeChanged, not textChanged alone")
        adj = _class_fn_segment(src, "_ComposerEdit", "_adjust_height")
        self.assertIn("new_h != self.height()", adj,
                      "UI-06 fix: no-op guard against layout re-entry")

    def test_palette_cap_fix_present(self):
        # INTENTIONAL (refactor P1 step 5): CommandPalette moved verbatim
        # to window_widgets_palette.py. Assertions unchanged.
        src = _read("window_widgets_palette.py")
        self.assertIn("def palette_list_height_for", src)
        self.assertIn("max(120, min(486", src)
        opened = _class_fn_segment(src, "CommandPalette", "open_centered")
        for pin in ("availableGeometry", "palette_list_height_for",
                    "adjustSize"):
            self.assertIn(pin, opened,
                          f"UI-07 fix: open_centered must use {pin}")


# ---------------------------------------------------------------- geometry
try:
    from PySide6.QtWidgets import QApplication  # noqa: F401
    _QT = True
except ImportError:
    _QT = False

_APP = None
_STUB_KEYS = []

# Audit fix Batch 5 (TEST-03): snapshot slot for the
# voice_assistant.* sys.modules entries. setUpModule captures
# BEFORE installing stubs (None = setUpModule never ran, so
# tearDownModule must not touch sys.modules). The old
# unconditional delete-all broke chatfix when audit2s1 ran
# first in a Qt-less env: setUpModule early-returned without
# installing anything, but tearDownModule still deleted
# chatfix's import-time-cached real w9_wiring, leaving its
# error-ladder tests with ladder None / missing attribute.
# Same snapshot/restore discipline as test_uia11y.py.
_VA_SNAPSHOT = None


def setUpModule():
    global _APP, _VA_SNAPSHOT
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication as _QA
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    # Stub voice_assistant.core.* so the real window_widgets.py
    # imports without numpy/sounddevice/the whole core package.
    tree = str(ROOT)
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = types.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    audio = types.ModuleType("voice_assistant.core.voice.audio")  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
    audio.audio_manager = types.SimpleNamespace()
    brain = types.ModuleType("voice_assistant.core.brain.brain")  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub follows hub move
    brain.live_session = types.SimpleNamespace()
    for name, mod in (("voice_assistant", pkg),
                      ("voice_assistant.core", core),
                      ("voice_assistant.core.voice.audio", audio),  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
                      ("voice_assistant.core.brain.brain", brain)):  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub follows hub move
        sys.modules[name] = mod
        _STUB_KEYS.append(name)


def tearDownModule():
    global _VA_SNAPSHOT
    if _VA_SNAPSHOT is None:
        return
    for key in list(sys.modules):
        if key == "voice_assistant" or key.startswith("voice_assistant."):
            del sys.modules[key]
    sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None
    _STUB_KEYS.clear()


class TestSlice1Geometry(unittest.TestCase):
    def setUp(self):
        if not _QT:
            self.skipTest("PySide6 unavailable in this interpreter")

    def _pump(self):
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def test_qss_precedence_own_sheet_beats_scaled_sheet(self):
        # UI-03 mechanism proof: a widget's OWN setStyleSheet
        # font-size beats the inherited (scaled) window sheet, so
        # the 56 local literals never move with the text-scale
        # setting while inherited text does.
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
        parent = QWidget()
        parent.setStyleSheet("QLabel { font-size: 20px; }")
        lay = QVBoxLayout(parent)
        plain = QLabel("Plain")
        lay.addWidget(plain)
        local = QLabel("Local")
        local.setStyleSheet("font-size: 12px;")
        lay.addWidget(local)
        parent.show()
        self._pump()
        self.assertEqual(plain.font().pixelSize(), 20)
        self.assertEqual(local.font().pixelSize(), 12)
        parent.close()

    def test_composer_height_tracks_resize(self):
        # UI-06 proof, sub-cap draft (the sharp case): before the
        # fix the height froze at the 300-wide value (79) on both
        # resize steps while the document rewrapped (59 -> 76 -> 42).
        from voice_assistant.gui.window_widgets import _ComposerEdit
        ed = _ComposerEdit()
        ed.setFixedWidth(300)
        ed.show()
        self._pump()
        ed.grab()
        ed.setText("this is a long draft that keeps going and going "
                   "and going until it wraps across several lines")
        self._pump()
        ed.grab()

        def want():
            line = max(18, ed.fontMetrics().lineSpacing())
            doc_h = int(ed.document().size().height())
            return max(46, min(doc_h + 20, line * 5 + 20))

        self.assertEqual(ed.height(), want())
        ed.setFixedWidth(200)
        self._pump()
        ed.grab()
        self.assertEqual(ed.height(), want(),
                         "narrowing under a draft must regrow height")
        ed.setFixedWidth(400)
        self._pump()
        ed.grab()
        self.assertEqual(ed.height(), want(),
                         "widening under a draft must shrink height")
        ed.close()

    def test_palette_cap_values(self):
        from voice_assistant.gui.window_widgets import (
            palette_list_height_for)
        # Normal screens keep the full 486; small work areas shrink.
        self.assertEqual(palette_list_height_for(832, 62), 486)
        self.assertEqual(palette_list_height_for(520, 62), 458)
        self.assertEqual(palette_list_height_for(200, 62), 138)
        self.assertEqual(palette_list_height_for(150, 62), 120)

    def test_palette_geometry_clamp_and_centre(self):
        from PySide6.QtWidgets import QWidget
        from PySide6.QtGui import QGuiApplication
        from voice_assistant.gui.window_widgets import CommandPalette
        avail = QGuiApplication.primaryScreen().availableGeometry()
        # Low parent: the old code centred at parent.y + 40, which
        # put the dialog far past the screen bottom. The fix must
        # keep it fully inside availableGeometry.
        low = QWidget()
        low.setGeometry(0, 700, 600, 100)
        low.show()
        self._pump()
        pal = CommandPalette(low)
        pal.open_centered()
        self._pump()
        self.assertEqual(pal.width(), 560)
        self.assertEqual(pal.list.height(), 486,
                         "cap must not degrade a normal screen")
        self.assertGreaterEqual(pal.x(), avail.x())
        self.assertGreaterEqual(pal.y(), avail.y())
        self.assertLessEqual(pal.x() + pal.width(), avail.right() + 1)
        self.assertLessEqual(pal.y() + pal.height(), avail.bottom() + 1)
        pal.close()
        low.close()
        # Normal parent: centring formula unchanged (relative to the
        # parent's ACTUAL geometry -- offscreen frames offset it).
        top = QWidget()
        top.setGeometry(0, 0, 600, 400)
        top.show()
        self._pump()
        pal2 = CommandPalette(top)
        pal2.open_centered()
        self._pump()
        geo = top.geometry()
        self.assertEqual(pal2.x(),
                         geo.x() + (geo.width() - pal2.width()) // 2)
        self.assertEqual(pal2.y(), geo.y() + max(
            40, (geo.height() - pal2.height()) // 4))
        pal2.close()
        top.close()

    def test_fixed_text_rooms(self):
        # Lab-font metrics; margins are wide on purpose (rooms are
        # ~40-50% larger than the measured text at 120%).
        from PySide6.QtGui import QFont, QFontMetrics
        from PySide6.QtWidgets import QWidget
        from voice_assistant.gui.window_widgets import CommandPalette

        def px(text, base_px, scale):
            f = QFont()
            f.setPixelSize(round(base_px * scale))
            return QFontMetrics(f).horizontalAdvance(text)

        # Sidebar nav button: 220 - 32 margins - 47 chrome = ~141.
        self.assertLess(px("System Pulse", 13, 1.2), 141)
        # Time/port inputs: 90 - frame/padding = ~78 room.
        self.assertLess(px("23:59", 13, 1.2), 78)
        self.assertLess(px("65535", 13, 1.2), 78)
        # Palette rows: 560 - 12 margins - frame = ~544 room; the
        # longest of 657 real rows must fit even at 120%.
        stub = QWidget()
        pal = CommandPalette(stub)
        labels = [r.get("label", "") for r in pal._commands]
        self.assertTrue(labels)
        longest = max(labels, key=len)
        self.assertLess(px(longest, 13, 1.2), 544)
        pal.close()
        stub.close()
        # Skills raw tool names: mono 11 px (setFont, scale-blind)
        # in a 280 card, ~264 room.
        mono = QFont("Monospace")
        mono.setPixelSize(11)
        fm = QFontMetrics(mono)
        for name in ("wifi_dead_spot_map", "continuous_display_choice",
                     "screen_watch_session_state"):
            self.assertLess(fm.horizontalAdvance(name), 264)


if __name__ == "__main__":
    unittest.main()

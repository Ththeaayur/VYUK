# Refactor Phase 1 step 5: SettingsMixin card-group mixin, moved verbatim from gui/window_settings.py.
from voice_assistant.core.gui_facade import (
    config, gate_custom_accent, migrate_theme_name, normalize_verbosity,
    os_motion)
from .style import THEMES as STYLE_THEMES
from .visualizer import (
    THEMES,
)
from .style import (
    build_stylesheet,
    derive_theme,
    _resolve_theme,
    TYPE_CARD_TITLE,
    # Command Deck Phase E: Settings carries no colour literals of
    # its own — every value below is a style token.
    ACCENT,
    TEXT,
    TEXT_MUTED,
    INPUT_BG,
    INPUT_BG_FOCUS,
    LINE_SUBTLE,
    FOCUS_BORDER,
    SELECTION_BG,
    RADIUS_BTN,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import (
    QColor,
    QWheelEvent,
)
from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    Qt,
    QTime,
    QTimer,
)
from voice_assistant.core.gui_facade import qsettings_contains, qsettings_get, qsettings_set, qsettings_set_noted, qsettings_store



class SettingsAppearanceMixin:


    # ------------------------------------------------- shell glass

    def _apply_saved_glass(self):
        """Push the saved translucent-shell preference into the
        shell (v5.24, Eyes E32). Called at build, right after the
        shell exists; safe to re-call any time."""
        shell = getattr(self, "shell", None)
        if shell is None:
            return
        try:
            shell.set_glass(bool(qsettings_get("appearance/glass", settings=self.settings)))
        except Exception:  # noqa: BLE001
            pass

    def _on_glass_toggled(self, checked: bool):
        # Round-3 item 14: a glass choice that never persists
        # reverts at the next launch (the apply below re-reads the
        # stored value). Noted on failure, never swallowed.
        qsettings_set_noted("appearance/glass", bool(checked), settings=self.settings)
        self._apply_saved_glass()


    # ------------------------------------------------- ambience

    def _apply_ambience(self):
        """Push the saved particle preference + the reduced-motion
        setting into the Assistant page's particle field (v5.18 E5).
        v5.43 F5: the same reduced-motion push also reaches the orb
        visualizer, through this same channel."""
        field = getattr(self, "particle_field", None)
        enabled = bool(qsettings_get("appearance/particles", settings=self.settings))
        btn = getattr(self, "btn_particles", None)
        if btn is not None:
            btn.blockSignals(True)
            btn.setChecked(enabled)
            btn.blockSignals(False)
        # v5.43 F5: ParticleField's channel, same value — the orb's
        # ambient rotation / swirl / current phases freeze under
        # reduced motion; its energy core and palette keep working.
        viz = getattr(self, "visualizer", None)
        if viz is not None:
            try:
                viz.set_reduced_motion(self._motion_reduced())
            except Exception:  # noqa: BLE001
                pass
        if field is None:
            return
        try:
            field.set_enabled(enabled)
            field.set_reduced_motion(self._motion_reduced())
        except Exception:  # noqa: BLE001
            pass

    def _on_particles_toggled(self, checked: bool):
        # Round-3 item 14: same as the glass toggle above — a
        # particles choice that never persists reverts at the next
        # launch. Noted on failure, never swallowed.
        qsettings_set_noted("appearance/particles", bool(checked), settings=self.settings)
        self._apply_ambience()

    def _apply_saved_settings(self):
        # Conversion Wave 9: the shared settings model runs on every
        # settings apply (load path proof — snapshot after
        # migration, never a second write path).
        try:
            from voice_assistant.core.gui_facade import w9_wiring as _w9
            self._w9_settings_snapshot = _w9.settings_snapshot(
                self.settings)
        except Exception:  # noqa: BLE001
            pass
        # v5.54.1: the remembered-approvals list is live state
        # (a popup may have added a kind since the page was
        # built), so it refreshes whenever settings are
        # applied — guarded for pre-build calls.
        try:
            self._refresh_allowlist()
        except Exception:  # noqa: BLE001
            pass
        self._apply_ambience()
        theme = migrate_theme_name(str(qsettings_get("theme", settings=self.settings)))
        if theme not in STYLE_THEMES and theme not in THEMES and theme != "Custom":
            theme = "Cyan"
        self._on_theme_selected(theme)
        # Aura-only (owner decision 2026-11-01, supersedes guide D3):
        # any stored orb style other than Aura resolves to Aura on
        # load, and the resolved value is written back so a deleted
        # style can never come back at runtime.
        from voice_assistant.core.gui_facade import migrate_orb_style as _mig_orb
        _style = _mig_orb(qsettings_get("orb_style", settings=self.settings))
        qsettings_set("orb_style", _style, settings=self.settings)
        qsettings_set("orb_style_chosen", True, settings=self.settings)
        self.visualizer.set_orb_style(_style)
        _mini = getattr(self, "mini_orb", None)
        if _mini is not None:
            _mini.set_orb_style(_style)
        self.chk_sounds.setChecked(self.earcons.enabled)
        # v5.65 (Wave 10 — PF-24): sync the autostart box from the
        # real mechanism state (None/unavailable -> unchecked).
        # Guarded so the sync itself never fires the toggle handler.
        try:
            from voice_assistant.core.gui_facade import lifecycle as _lc
            _state = _lc.current_autostart_state()
            self._syncing_autostart = True
            try:
                self.chk_autostart.setChecked(bool(_state))
            finally:
                self._syncing_autostart = False
        except Exception:  # noqa: BLE001
            pass
        self.slider_volume.setValue(int(self.earcons.volume * 100))
        # v5.17: earcon family + per-cue switches, pace chips.
        try:
            self.earcons.set_family(str(qsettings_get("sounds/family", settings=self.settings)))
            from voice_assistant.gui.sounds import CUE_NAMES as _CUES
            for _cue in _CUES:
                self.earcons.set_cue_enabled(
                    _cue, bool(qsettings_get(f"sounds/cue_{_cue}", settings=self.settings)))
        except Exception:  # noqa: BLE001
            pass
        self._sync_earcon_family_buttons()
        self._sync_pace_buttons()

    def _on_theme_selected(self, name: str):
        # D3: heal a legacy stored name on apply, so the stored
        # value itself migrates to Cyan on next apply.
        name = migrate_theme_name(name)
        # SENSES v5.16: one Appearance choice drives BOTH the window
        # chrome (style.py themes) and the orb palette where a matching
        # orb palette exists. Chrome-only themes leave the orb as-is.
        # v5.17: that last sentence was a bug wearing a design's
        # clothes — the orb is the product's face, so EVERY theme now
        # recolours it (see _apply_theme_to_orb).
        qsettings_set("theme", name, settings=self.settings)
        self._apply_theme_to_orb()
        self._apply_appearance()

    def _apply_theme_to_orb(self):
        """Drives the orb (and mini orb) from the saved theme: the
        exact named orb palette when one matches, otherwise a custom
        palette derived from the theme's own colours — highlight as
        the orb's hi, accent as mid, accent_2 as edge."""
        try:
            name = migrate_theme_name(str(qsettings_get("theme", settings=self.settings)))
            orb_name = {"Graphite": "Mono"}.get(name, name)
            mini = getattr(self, "mini_orb", None)
            if orb_name in THEMES:
                self.visualizer.set_theme(orb_name)
                if mini is not None:
                    mini.set_theme(orb_name)
                return
            if name == "Custom":
                hexv = str(qsettings_get("appearance/custom_hex", settings=self.settings))
                theme_dict = derive_theme(hexv)
            else:
                theme_dict = STYLE_THEMES.get(name)
            if not theme_dict:
                return
            hi = theme_dict.get("highlight") or theme_dict["accent"]
            mid = theme_dict["accent"]
            edge = theme_dict.get("accent_2") or theme_dict["accent"]
            self.visualizer.set_custom_theme(hi, mid, edge)
            if mini is not None:
                mini.set_custom_theme(hi, mid, edge)
        except Exception:  # noqa: BLE001 — appearance must never break the app
            pass


    # ------------------------------------------------------- appearance
    def _apply_appearance(self):
        """Rebuild the window stylesheet from the saved appearance:
        theme (a style.py theme name, or "Custom" + a saved hex),
        text size (appearance/font_scale) and density. Safe to call
        before the Settings cards exist — control sync is guarded."""
        try:
            name = migrate_theme_name(str(qsettings_get("theme", settings=self.settings)))
            if name == "Custom":
                # D4: the applied Custom accent is the gated value
                # (a failing stored hex applies as cyan; the label
                # in _sync_appearance_controls shows the same).
                hexv = gate_custom_accent(str(qsettings_get("appearance/custom_hex", settings=self.settings)))
                theme_arg = derive_theme(hexv) or STYLE_THEMES["Cyan"]
            elif name in STYLE_THEMES:
                theme_arg = name
            elif name in THEMES:
                theme_arg = "Cyan"
            else:
                theme_arg = "Cyan"
            scale = self._current_font_scale()
            density = str(qsettings_get("appearance/density", settings=self.settings))
            if density not in ("comfortable", "compact"):
                density = "comfortable"
            # Conversion Wave 9: the live stylesheet is token-sourced
            # when the token sheet is byte-identical to the shipped
            # sheet (self-guarding fallback to the shipped build),
            # "Follow Windows" (opt-in QSettings key) recolours only
            # the accent token, and the window backdrop is re-applied
            # through the DWM gate (no-op off Windows).
            try:
                from .w9_wiring_theme import follow_windows_theme, stylesheet_for
                theme_arg, _sync = follow_windows_theme(
                    theme_arg, bool(qsettings_get("appearance/follow_windows", settings=self.settings)))
                self._w9_theme_sync = _sync
                _sheet, _src = stylesheet_for(
                    theme_arg, font_scale=scale, density=density)
                self._w9_stylesheet_source = _src
            except Exception:  # noqa: BLE001 — appearance never breaks boot
                _sheet = build_stylesheet(
                    theme_arg, font_scale=scale, density=density)
                self._w9_stylesheet_source = "shipped"
            self.setStyleSheet(_sheet)
            try:
                from .w9_wiring_theme import apply_window_backdrop
                self._w9_backdrop = apply_window_backdrop(self)
            except Exception:  # noqa: BLE001
                pass
            # E-F209: the custom-painted pages do not follow the QSS —
            # recolour their meters, bars and plot line from the
            # same resolved theme the chrome just took.
            try:
                self._apply_theme_to_painted_pages(theme_arg)
            except Exception:  # noqa: BLE001 — appearance never breaks boot
                pass
            # Look-fix 2026-11-24: QIcons do not follow the QSS
            # either — re-make them from the resolved theme.
            try:
                self._apply_theme_to_icons()
            except Exception:  # noqa: BLE001 — appearance never breaks boot
                pass
            # Stage 0 features batch 5: the shell paints its own
            # fill (AppShell.paintEvent), so the QSS cannot reach
            # it — drive it from the same resolved theme.
            try:
                self._apply_theme_to_shell(theme_arg)
            except Exception:  # noqa: BLE001 — appearance never breaks boot
                pass
            # Stage 0 features batch 5, item 12: the painted /
            # explicit-font text scales with the same value.
            try:
                self._apply_font_scale_to_painted_pages(scale)
            except Exception:  # noqa: BLE001 — appearance never breaks boot
                pass
            # UI Phase 2 look-fix (2026-11-25, defect 2): the
            # page HudBackdrops are only re-themed by
            # _apply_profile_backdrops, which runs on a page
            # switch — a theme change left every already-built
            # page's backdrop on the OLD theme (Light theme: the
            # Skills page stayed dark behind light headers)
            # until the user navigated away and back. Re-apply
            # the backdrops here, from the same resolved theme
            # the rest of the pass just took (via _home_theme),
            # so the backdrop follows the theme on the change
            # itself. Idempotent: _apply_profile_backdrops reuses
            # the existing backdrop, never stacks two.
            try:
                profile = getattr(self, "_hud_profile_name",
                                  "Cinematic")
                apply_backdrops = getattr(
                    self, "_apply_profile_backdrops", None)
                if apply_backdrops is not None:
                    apply_backdrops(profile)
            except Exception:  # noqa: BLE001 — appearance never breaks boot
                pass
        except Exception:  # noqa: BLE001 — appearance must never break boot
            pass
        self._sync_appearance_controls()

    def _apply_theme_to_painted_pages(self, theme_arg=None):
        """Recolour the custom-painted pages from a theme (E-F209).

        The theme system only regenerates the QSS, so the Pulse
        meters, the Overview plot line and the Senses 24 h bars —
        all painted from the fixed house tokens — never followed
        the theme. This drives their existing set_palette hooks
        from the same resolved theme the chrome uses: the
        (accent, accent_2) pair for Pulse/Overview, the
        (highlight, accent) pair for Senses. Under Cyan the pairs
        are the house tokens, so nothing changes. Defensive
        throughout: pages may not exist yet when the theme loads,
        and appearance must never break boot.
        Stage 0 features batch 5 (Light theme, render-found):
        the QSS is not the only surface the theme misses — the
        scoped sheets and the painted fills behind the tabs, the
        HudPanel cards, the Views panes, the Pulse row labels,
        the Overview backdrops and the Senses text all carried
        the house tokens too. This pass now drives them all from
        the fully resolved theme (bg / surface_2 / text /
        text_muted / text_faint / accent / accent_2). On a light
        theme the Senses section titles take the accent, not the
        pale highlight — the highlight is unreadable on light.
        """
        try:
            theme = None
            if isinstance(theme_arg, dict):
                theme = theme_arg
            elif isinstance(theme_arg, str) and theme_arg in STYLE_THEMES:
                theme = STYLE_THEMES[theme_arg]
            if theme is None:
                name = migrate_theme_name(
                    str(qsettings_get("theme", settings=self.settings)))
                if name == "Custom":
                    hexv = gate_custom_accent(str(qsettings_get(
                        "appearance/custom_hex", settings=self.settings)))
                    theme = derive_theme(hexv) or STYLE_THEMES["Cyan"]
                else:
                    theme = STYLE_THEMES.get(name) or STYLE_THEMES["Cyan"]
            if (not isinstance(theme, dict)
                    or "accent" not in theme
                    or "accent_2" not in theme):
                theme = STYLE_THEMES["Cyan"]
            resolved = _resolve_theme(theme)
            # Phase 0: remember the resolved theme — components
            # created AFTER this pass (a StatusPill per Calls
            # refresh, a HudToast per show_toast) never see it
            # through findChildren, so they theme themselves
            # from here at creation instead of painting house
            # colours on Light (proven: pill words invisible on
            # the Light 640 Calls render).
            self._resolved_theme = resolved
            accent = resolved["accent"]
            accent_2 = resolved["accent_2"]
            highlight = resolved["highlight"]
            bg = resolved["bg"]
            text = resolved["text"]
            text_muted = resolved["text_muted"]
            from .style_theme import _luminance
            light = _luminance(bg) > 0.5
            pulse = getattr(self, "pulse_page", None)
            if pulse is not None:
                pulse.set_palette(accent, accent_2, text, text_muted)
            overview = getattr(self, "_w9_overview_panel", None)
            if overview is not None:
                overview.set_palette(accent, accent_2, bg, text_muted)
            senses = getattr(self, "senses_page", None)
            if senses is not None:
                senses.set_palette(accent if light else highlight,
                                    accent, text, text_muted)
            # HudPanel cards paint their own fill (Settings cards
            # and any other HudPanel): drive them from the resolved
            # theme so they stop staying black on Light.
            try:
                from .design_system.panels import HudPanel
                for panel in self.findChildren(HudPanel):
                    panel.set_theme(resolved)
            except Exception:  # noqa: BLE001 — panels are a nicety
                pass
            # Phase 0: the wired painting components carry the
            # same seam (HudPanel precedent) — the switch track,
            # the tab bar sheet, the toast sheet and the pill
            # text all re-resolve from the theme, or they stay
            # house-dark on Light. Defensive throughout: pages
            # are lazily built and may not exist yet.
            try:
                from .design_system import (
                    HudSwitch, HudTabs, HudToast, StatusPill)
                for switch in self.findChildren(HudSwitch):
                    switch.set_theme(resolved)
                for tabs in self.findChildren(HudTabs):
                    tabs.set_theme(resolved)
                for toast in self.findChildren(HudToast):
                    toast.set_theme(resolved)
                for pill in self.findChildren(StatusPill):
                    pill.set_theme(resolved)
            except Exception:  # noqa: BLE001 — components are a nicety
                pass
            # Look-fix 2 (2026-11-24): the round-2 themed surfaces
            # — every one paints from a house colour the QSS never
            # reaches, so each carries a set_theme the pass drives
            # from the same resolved theme. Defensive throughout:
            # pages are lazily built and may not exist yet.
            try:
                from .design_system.progress import SegmentBar
                for bar in self.findChildren(SegmentBar):
                    bar.set_theme(resolved)
            except Exception:  # noqa: BLE001 — components are a nicety
                pass
            try:
                from .design_system.statusstrip import StatusStrip
                for strip in self.findChildren(StatusStrip):
                    strip.set_theme(resolved)
            except Exception:  # noqa: BLE001 — components are a nicety
                pass
            try:
                from .status_strip import StatusStrip as _DockedStrip
                strip = getattr(self, "vitals_strip", None)
                if strip is not None and isinstance(strip, _DockedStrip):
                    strip.set_theme(resolved)
            except Exception:  # noqa: BLE001 — components are a nicety
                pass
            try:
                catalog = getattr(self, "skills_catalog", None)
                if catalog is not None:
                    catalog.set_theme(resolved)
            except Exception:  # noqa: BLE001 — components are a nicety
                pass
            try:
                for hdr in getattr(self, "_settings_group_headers", {}):
                    hdr.setStyleSheet(
                        f"font-size: {TYPE_CARD_TITLE}px;"
                        f" font-weight: 600; color: {text};")
            except Exception:  # noqa: BLE001 — components are a nicety
                pass
            try:
                self._refresh_plain_text_inputs(resolved)
            except Exception:  # noqa: BLE001 — components are a nicety
                pass
            refresh_activity = getattr(self, "_refresh_activity_theme",
                                       None)
            if refresh_activity is not None:
                refresh_activity(resolved)
            refresh_views = getattr(self, "_refresh_views_theme", None)
            if refresh_views is not None:
                refresh_views(resolved)
            # UI Phase 1: the Home HUD's non-panel stylesheets
            # (labels, pills, hairlines, mini-bars) re-resolve from
            # the same theme — the Light-contrast path.
            apply_home = getattr(self, "_apply_home_theme", None)
            if apply_home is not None:
                apply_home(resolved)
        except Exception:  # noqa: BLE001 — appearance never breaks boot
            pass

    def _refresh_plain_text_inputs(self, resolved: dict) -> None:
        """Re-apply the hand-built QPlainTextEdit wells (look-fix 2).

        The app sheet styles the QLineEdit family only, so the two
        plain-text wells (the theme-pack JSON box in Settings and
        the pronunciations box in Voice) carry the input rule by
        hand — built once from the house tokens. On Light the house
        well is a mid-grey slab, so the appearance pass re-applies
        both from the resolved theme: the well + focus fill, the
        text, the selection, the focus ring. The border stays the
        house hairline, like every other hairline on the page.
        Called from _apply_theme_to_painted_pages; the build sites
        keep the house values as the default.
        """
        try:
            text = (resolved or {}).get("text", TEXT)
            well = (resolved or {}).get("input_bg", INPUT_BG)
            focus = (resolved or {}).get("focus_border") or FOCUS_BORDER
            selection = (resolved or {}).get("selection_bg", SELECTION_BG)
            sheet = (
                f"QPlainTextEdit {{ background-color: {well};"
                f" border: 1px solid {LINE_SUBTLE};"
                f" border-radius: {RADIUS_BTN}px; padding: 8px 12px;"
                f" color: {text};"
                f" selection-background-color: {selection};"
                f" selection-color: {text}; }}"
                f"QPlainTextEdit:focus {{ border: {focus};"
                f" background-color: {INPUT_BG_FOCUS}; }}")
            for widget in (getattr(self, "_w9_pack_input", None),
                           getattr(self, "input_pronunciations", None)):
                if widget is not None:
                    widget.setStyleSheet(sheet)
        except Exception:  # noqa: BLE001 — appearance never breaks boot
            pass

    def _current_font_scale(self) -> float:
        """The validated appearance font scale (batch 5, item 12).

        The offered choices run to 150% (1.0 / 1.15 / 1.3 / 1.5);
        older stored values (0.9 / 1.1 / 1.2) stay valid; anything
        else reads as 1.0. Shared by _apply_appearance and the
        lazy page builders, so a page built after the last
        appearance pass still opens at the current scale.
        """
        try:
            scale = float(qsettings_get(
                "appearance/font_scale", settings=self.settings))
        except (TypeError, ValueError):
            scale = 1.0
        if scale not in (0.9, 1.0, 1.1, 1.15, 1.2, 1.3, 1.5):
            scale = 1.0
        return scale

    def _apply_font_scale_to_painted_pages(self, scale: float) -> None:
        """Scale the painted / explicit-font text (batch 5, item 12).

        The QSS font scale reaches every styled label but not
        text painted in paintEvent, nor labels whose QFont is set
        explicitly in code — so the window drives those hooks
        from the same appearance value the sheet just took.
        Defensive throughout: pages may not exist yet (lazy
        builds) and appearance must never break boot. The
        scoped-stylesheet text (113 font-size sites, e.g. the
        memory row's "what" label) stays at its built size — a
        known limitation, reported plainly: it does not grow, so
        nothing clips.
        """
        try:
            senses = getattr(self, "senses_page", None)
            if senses is not None:
                senses.set_font_scale(scale)
            overview = getattr(self, "_w9_overview_panel", None)
            if overview is not None:
                overview.set_font_scale(scale)
            mem_web = getattr(self, "memory_web", None)
            if mem_web is not None:
                mem_web.set_font_scale(scale)
            galaxy = getattr(self, "skills_galaxy", None)
            if galaxy is not None:
                galaxy.set_font_scale(scale)
            catalog = getattr(self, "skills_catalog", None)
            if catalog is not None:
                catalog.set_font_scale(scale)
            mem_list = getattr(self, "memory_list", None)
            if mem_list is not None:
                for i in range(mem_list.count()):
                    row = mem_list.itemWidget(mem_list.item(i))
                    setter = getattr(row, "set_font_scale", None)
                    if setter is not None:
                        setter(scale)
        except Exception:  # noqa: BLE001 — appearance never breaks boot
            pass

    def _apply_theme_to_icons(self):
        """Re-make the Phosphor icons from the resolved theme.

        Look-fix 2026-11-24: every icon was painted once at build
        from the house TEXT / TEXT_MUTED constants (near-white), so
        on Light the composer send/mic and the briefing play glyph
        were near-invisible on the light pill, and the sidebar /
        window-control glyphs were faint. QIcons do not follow the
        QSS, so this pass re-makes them from the same resolved
        theme the painted pages use — the mapping stays the build
        mapping (text for the primary actions, text_muted for the
        rest), only the colours resolve. Under Cyan the resolved
        values are the house tokens, so nothing changes. Runs from
        _apply_appearance, after _apply_theme_to_painted_pages
        stores _resolved_theme. Defensive throughout: the dock,
        sidebar and title bar are lazily built and may not exist
        yet when the theme loads.
        """
        try:
            resolved = getattr(self, "_resolved_theme", None)
            if not isinstance(resolved, dict):
                return
            text = resolved.get("text", TEXT)
            muted = resolved.get("text_muted", TEXT_MUTED)
            from .icons import make_icon
            # Dock + briefing (built in window_shell_assistant).
            # The mute button shows volume_off while checked
            # (muted) — the theme pass keeps the state, only the
            # colour resolves.
            for attr, name, colour, size in (
                    ("btn_send", "send", text, 20),
                    ("btn_mic", "mic", text, 26),
                    ("_btn_dictate", "pencil", muted, 16),
                    ("btn_screen", "monitor", muted, 19),
                    ("btn_briefing", "play", text, 13)):
                btn = getattr(self, attr, None)
                if btn is not None:
                    try:
                        btn.setIcon(make_icon(name, colour, size))
                    except Exception:  # noqa: BLE001 — one icon never breaks boot
                        pass
            mute_btn = getattr(self, "btn_mute", None)
            if mute_btn is not None:
                try:
                    mute_btn.setIcon(make_icon(
                        "volume_off" if mute_btn.isChecked() else "volume",
                        muted, 16))
                except Exception:  # noqa: BLE001 — one icon never breaks boot
                    pass
            # Title bar (built in window_shell_frame).
            for attr, name, size in (
                    ("btn_cmd_pill", "search", 14),
                    ("btn_win_min", "minus", 13),
                    ("btn_win_max", "square", 12),
                    ("btn_win_close", "x", 13)):
                btn = getattr(self, attr, None)
                if btn is not None:
                    try:
                        btn.setIcon(make_icon(name, muted, size))
                    except Exception:  # noqa: BLE001 — one icon never breaks boot
                        pass
            # Sidebar nav (built in window_shell_sidebar): the
            # icons are owned by _refresh_nav_icons, which keeps
            # the active-page highlight — the theme pass re-runs
            # it for the page that is currently active, so a
            # navigation afterwards does not undo the theme.
            try:
                pages = getattr(self, "pages", None)
                current = (pages.currentIndex()
                           if pages is not None else 0)
                self._refresh_nav_icons(current)
            except Exception:  # noqa: BLE001 — one icon never breaks boot
                pass
            wake = getattr(self, "btn_wake", None)
            if wake is not None:
                try:
                    wake.setIcon(make_icon("mic", muted))
                except Exception:  # noqa: BLE001 — one icon never breaks boot
                    pass
            # Home quick-action tiles (built in window_home_hud):
            # the icon name follows the tile's objectName suffix.
            tile_icons = {"reminders": "bell", "email": "envelope",
                          "calendar": "calendar", "notes": "note",
                          "home": "house", "money": "currency-dollar"}
            for btn in self.findChildren(QPushButton):
                try:
                    obj = btn.objectName() or ""
                except Exception:  # noqa: BLE001 — defensive
                    continue
                if not obj.startswith("homeTile-"):
                    continue
                icon_name = tile_icons.get(obj[len("homeTile-"):])
                if icon_name is None:
                    continue
                try:
                    btn.setIcon(make_icon(icon_name, muted, 18))
                except Exception:  # noqa: BLE001 — one icon never breaks boot
                    pass
        except Exception:  # noqa: BLE001 — appearance never breaks boot
            pass

    def _apply_theme_to_shell(self, theme_arg=None):
        """Recolour the painted shell from a theme (batch 5).

        AppShell fills the whole window body in paintEvent, so
        the QSS background never shows through it: under Light
        the window stayed dark even with the Light sheet applied
        (found by the P4 probe leg). This drives the shell's
        set_chrome hook from the same resolved theme the chrome
        uses. Under Cyan the values are the house tokens, so
        nothing changes. Defensive throughout: the central widget
        may not be an AppShell (tests build partial windows).
        """
        try:
            theme = None
            if isinstance(theme_arg, dict):
                theme = theme_arg
            elif isinstance(theme_arg, str) and theme_arg in STYLE_THEMES:
                theme = STYLE_THEMES[theme_arg]
            if theme is None:
                name = migrate_theme_name(
                    str(qsettings_get("theme", settings=self.settings)))
                if name == "Custom":
                    hexv = gate_custom_accent(str(qsettings_get(
                        "appearance/custom_hex", settings=self.settings)))
                    theme = derive_theme(hexv) or STYLE_THEMES["Cyan"]
                else:
                    theme = STYLE_THEMES.get(name) or STYLE_THEMES["Cyan"]
            resolved = _resolve_theme(theme)
            shell = self.centralWidget()
            setter = getattr(shell, "set_chrome", None)
            if setter is not None:
                setter(resolved["bg"], resolved["accent"])
        except Exception:  # noqa: BLE001 — appearance never breaks boot
            pass

    def _sync_appearance_controls(self):
        try:
            name = migrate_theme_name(str(qsettings_get("theme", settings=self.settings)))
            if hasattr(self, "theme_name_label"):
                if name == "Custom":
                    hexv = gate_custom_accent(str(qsettings_get("appearance/custom_hex", settings=self.settings)))
                    self.theme_name_label.setText(f"Custom {hexv}")
                else:
                    self.theme_name_label.setText(name)
            for n, btn in getattr(self, "theme_buttons", {}).items():
                btn.setChecked(n == name)
            try:
                scale = float(qsettings_get("appearance/font_scale", settings=self.settings))
            except (TypeError, ValueError):
                scale = 1.0
            for v, btn in getattr(self, "font_scale_buttons", {}).items():
                btn.setChecked(abs(float(v) - scale) < 0.001)
            # Stage 0 features batch 5, item 11: reply verbosity
            # buttons follow the live config value.
            try:
                level = normalize_verbosity(
                    getattr(config, "reply_verbosity", "balanced"))
            except Exception:  # noqa: BLE001 — sync never breaks Settings
                level = "balanced"
            for lv, btn in getattr(self, "verbosity_buttons", {}).items():
                btn.setChecked(lv == level)
            density = str(qsettings_get("appearance/density", settings=self.settings))
            for d, btn in getattr(self, "density_buttons", {}).items():
                btn.setChecked(d == density)
            # UI Phase 1: the HUD profile buttons + the home-city
            # input follow the one canonical store. During a
            # Phase 4 preview the buttons follow the previewed
            # profile instead — the stores still hold the old
            # value until Apply.
            try:
                if getattr(self, "_hud_preview_before", None) \
                        is not None:
                    profile = str(getattr(
                        self, "_hud_preview_current", None)
                        or "Cinematic")
                else:
                    profile = str(qsettings_get(
                        "appearance/hud_profile", settings=self.settings)
                        or "Cinematic")
                for p, btn in getattr(
                        self, "hud_profile_buttons", {}).items():
                    btn.setChecked(p == profile)
            except Exception:  # noqa: BLE001
                pass
            # Phase 4 (D-P4-08, R-P4-E): the Pulse cadence mirror
            # follows the one shared key — the Pulse header may
            # have changed it while Settings was hidden.
            try:
                ms = int(qsettings_get(
                    "pulse/refresh_ms", settings=self.settings) or 1000)
            except (TypeError, ValueError):
                ms = 1000
            try:
                combo = getattr(self, "pulse_cadence_combo", None)
                if combo is not None:
                    idx = combo.findData(
                        ms if ms in (1000, 2000, 5000) else 1000)
                    if idx >= 0:
                        combo.blockSignals(True)
                        combo.setCurrentIndex(idx)
                        combo.blockSignals(False)
            except Exception:  # noqa: BLE001
                pass
            try:
                city = str(qsettings_get("home/city",
                                         settings=self.settings) or "")
                edit = getattr(self, "home_city_edit", None)
                if edit is not None and edit.text() != city:
                    edit.setText(city)
            except Exception:  # noqa: BLE001
                pass
            self._sync_motion_controls()
        except Exception:  # noqa: BLE001
            pass

    def _on_custom_accent(self):
        current = str(qsettings_get("appearance/custom_hex", settings=self.settings))
        color = QColorDialog.getColor(
            QColor(current), self, "Pick an accent colour")
        if not color.isValid():
            return
        # D4: store the gated (effective) value, so the label
        # always shows the accent actually applied. A failing pick
        # lands on cyan; no new dialog is raised here (dialogs
        # migrate to HudDialog in Phase C).
        hexv = gate_custom_accent(color.name().upper())
        if derive_theme(hexv) is None:
            return
        qsettings_set("appearance/custom_hex", hexv, settings=self.settings)
        self._on_theme_selected("Custom")

    def _on_font_scale_selected(self, scale: float):
        qsettings_set("appearance/font_scale", float(scale), settings=self.settings)
        self._apply_appearance()

    def _on_hud_profile_selected(self, profile_name: str):
        """Phase 4 (D-P4-32): the HUD profile PREVIEWS — the
        pick shows at once at the one call site but writes
        nothing; Apply keeps it, Cancel puts the previous
        profile back. The pre-preview value is read once per
        preview session (later clicks during preview must not
        overwrite it). The buttons' checked states are set
        here by hand — _sync_appearance_controls reads the
        stored value and would revert them."""
        try:
            if getattr(self, "_hud_preview_before", None) is None:
                before = ""
                try:
                    from voice_assistant.core.gui_facade import (
                        config as _cfg)

                    before = str(getattr(_cfg, "hud_profile", "") or "")
                except Exception:  # noqa: BLE001
                    before = ""
                if not before:
                    try:
                        before = str(qsettings_get(
                            "appearance/hud_profile",
                            settings=self.settings) or "")
                    except Exception:  # noqa: BLE001
                        before = ""
                self._hud_preview_before = before or "Cinematic"
            self._hud_preview_current = profile_name
            if hasattr(self, "_apply_hud_profile"):
                self._apply_hud_profile(profile_name)
            for p, btn in getattr(
                    self, "hud_profile_buttons", {}).items():
                btn.setChecked(p == profile_name)
            for widget in (getattr(self, "hud_preview_label", None),
                           getattr(self, "hud_apply_btn", None),
                           getattr(self, "hud_cancel_btn", None)):
                if widget is not None:
                    widget.setVisible(True)
        except Exception:  # noqa: BLE001 — preview never breaks Settings
            pass

    def _on_hud_profile_apply(self):
        """Phase 4 (D-P4-32): keep the previewed profile — the
        persist step the old handler did on every click, now
        only on Apply. ConfigSpec + QSettings + .env, the one
        canonical store."""
        try:
            profile_name = getattr(self, "_hud_preview_current", None)
            if not profile_name:
                return
            try:
                from voice_assistant.core.gui_facade import (
                    config as _cfg)

                _cfg.hud_profile = profile_name
            except Exception:  # noqa: BLE001
                pass
            try:
                qsettings_set("appearance/hud_profile", profile_name,
                              settings=self.settings)
            except Exception:  # noqa: BLE001
                pass
            try:
                self._persist_config_setting(
                    "VYUK_HUD_PROFILE", profile_name)
            except Exception:  # noqa: BLE001
                pass
            self._hud_preview_before = None
            self._hud_preview_current = None
            for widget in (getattr(self, "hud_preview_label", None),
                           getattr(self, "hud_apply_btn", None),
                           getattr(self, "hud_cancel_btn", None)):
                if widget is not None:
                    widget.setVisible(False)
            self._sync_appearance_controls()
        except Exception:  # noqa: BLE001 — apply never breaks Settings
            pass

    def _on_hud_profile_cancel(self):
        """Phase 4 (D-P4-32): drop the preview — restore the
        pre-preview profile through the one call site. The
        stores were never written during preview, so nothing
        needs un-writing."""
        try:
            before = getattr(self, "_hud_preview_before", None) \
                or "Cinematic"
            if hasattr(self, "_apply_hud_profile"):
                self._apply_hud_profile(before)
            for p, btn in getattr(
                    self, "hud_profile_buttons", {}).items():
                btn.setChecked(p == before)
            self._hud_preview_before = None
            self._hud_preview_current = None
            for widget in (getattr(self, "hud_preview_label", None),
                           getattr(self, "hud_cancel_btn", None),
                           getattr(self, "hud_apply_btn", None)):
                if widget is not None:
                    widget.setVisible(False)
        except Exception:  # noqa: BLE001 — cancel never breaks Settings
            pass

    def _on_pulse_cadence_selected(self, index: int):
        """Phase 4 (D-P4-08, R-P4-E): the Appearance mirror of
        the Pulse cadence — writes the one shared
        pulse/refresh_ms key; the Pulse page re-reads it on
        show, and this combo re-syncs from the key in
        _sync_appearance_controls."""
        try:
            combo = getattr(self, "pulse_cadence_combo", None)
            if combo is None:
                return
            ms = int(combo.itemData(index))
            if ms not in (1000, 2000, 5000):
                return
            qsettings_set("pulse/refresh_ms", ms,
                          settings=self.settings)
        except Exception:  # noqa: BLE001 — cadence never breaks Settings
            pass

    def _on_home_city_edit_finished(self):
        """UI Phase 1 (H-11): the home city, one canonical store
        (ConfigSpec home_city + QSettings home/city + .env)."""
        edit = getattr(self, "home_city_edit", None)
        if edit is None:
            return
        city = edit.text().strip()
        if not city:
            return
        try:
            from voice_assistant.core.gui_facade import config as _cfg

            _cfg.home_city = city
        except Exception:  # noqa: BLE001
            pass
        try:
            qsettings_set("home/city", city, settings=self.settings)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._persist_config_setting("VYUK_HOME_CITY", city)
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "_refresh_home_weather"):
                self._refresh_home_weather()
        except Exception:  # noqa: BLE001
            pass

    def _on_verbosity_selected(self, level: str):
        """Stage 0 features batch 5, item 11: reply verbosity.
        Persists like the voice settings (config field +
        .env) and applies from the next reply on every brain —
        the turn paths read the live config value per turn."""
        try:
            level = normalize_verbosity(level)
        except Exception:  # noqa: BLE001 — a bad level never breaks Settings
            level = "balanced"
        try:
            config.reply_verbosity = level
        except Exception:  # noqa: BLE001
            pass
        try:
            self._persist_config_setting("VYUK_REPLY_VERBOSITY", level)
        except Exception:  # noqa: BLE001 — in-session value already set
            pass
        self._sync_appearance_controls()

    def _on_density_selected(self, density: str):
        qsettings_set("appearance/density", density, settings=self.settings)
        self._apply_appearance()

    # ------------------------------------------------- v5.57 motion intensity
    # (redo, A9.3): the old single reduced-motion bool becomes three
    # levels. Full = everything moves. Subtle = the orb + particles
    # calm down, panels still glide. Off = the orb + particles calm
    # down AND panels snap (no fades/slides). The orb has no further
    # reduction without logic changes (and F6 freezes its state
    # signatures), so Subtle and Off share the orb reduction and
    # differ only in chrome — honestly distinct, nothing invented.
    # Stored as one key, motion/level ("full"/"subtle"/"off").
    # Migration (faithful, no user loses a setting): an old
    # motion/reduced=True meant chrome off + orb reduced, which is
    # exactly Off; False/absent means Full. motion/reduced keeps
    # being written (derived) for any outside reader.
    MOTION_LEVELS = ("full", "subtle", "off")

    def _motion_level(self) -> str:
        try:
            settings = getattr(self, "settings", None)
            if settings is None:
                settings = qsettings_store()
            level = str(qsettings_get("motion/level", settings=settings) or "").strip().lower()
            # v5.57 fix (battery: v516_wiring + test_v543_f5 (e)):
            # the old motion/reduced key is still written directly
            # (tests, legacy installs), so a direct write must win
            # over a previously migrated level — otherwise the
            # migration goes sticky and reduced=False after an
            # earlier reduced=True never takes effect. The new
            # control writes level + its derived reduced together
            # (see _on_motion_selected), so a consistent pair means
            # the control spoke last; an inconsistent pair means a
            # direct reduced write spoke last and re-migrates.
            reduced_flag = None
            if qsettings_contains("motion/reduced", settings=settings):
                reduced_flag = bool(qsettings_get("motion/reduced", settings=settings))
            if level in self.MOTION_LEVELS and reduced_flag is not None \
                    and reduced_flag != (level in ("subtle", "off")):
                level = "off" if reduced_flag else "full"
                try:
                    qsettings_set("motion/level", level, settings=settings)
                except Exception:  # noqa: BLE001
                    pass
                return level
            if level in self.MOTION_LEVELS:
                return level
            if reduced_flag:
                level = "off"
            elif os_motion.os_prefers_reduced_motion():
                # D-P6-12: no level stored and no legacy flag —
                # the OS preference is the default. Deliberately
                # NOT written back: the OS toggle is a live
                # default, not a user choice; persisting it would
                # go stale when the OS flips and would masquerade
                # as an explicit choice. The user's first explicit
                # pick (Settings → Motion) writes the key and wins
                # from then on.
                return "off"
            else:
                level = "full"
            try:
                qsettings_set("motion/level", level, settings=settings)
            except Exception:  # noqa: BLE001
                pass
            return level
        except Exception:  # noqa: BLE001
            return "full"

    def _on_motion_selected(self, level: str) -> None:
        """v5.57 (redo, A9.3): the Motion segmented control. Writes
        the level + its derived reduced flag together (no read in
        between, so _motion_level() never sees a half-written pair),
        then syncs the buttons for the picked level and pushes the
        new reduction to the orb + particle field."""
        if level not in self.MOTION_LEVELS:
            return
        # Round-3 item 14: the user's explicit motion choice must
        # persist — a write that never lands reverts the motion
        # level at the next launch. Noted on failure, never
        # swallowed.
        qsettings_set_noted("motion/level", level, settings=self.settings)
        qsettings_set_noted("motion/reduced", level in ("subtle", "off"), settings=self.settings)
        self._sync_motion_controls(level)
        try:
            self._apply_ambience()
        except Exception:  # noqa: BLE001
            pass

    def _sync_motion_controls(self, level: str | None = None) -> None:
        """Check the Motion button matching the current level (the
        level may be passed in by _on_motion_selected so the sync
        never re-reads a half-written pair)."""
        try:
            if level is None:
                level = self._motion_level()
            for lv, btn in getattr(self, "motion_buttons", {}).items():
                btn.setChecked(lv == level)
        except Exception:  # noqa: BLE001
            pass

    def _motion_reduced(self) -> bool:
        """Orb/particles reduction: on for Subtle and Off alike."""
        return self._motion_level() != "full"

    def _animate_enabled(self) -> bool:
        """Chrome gate (unchanged callers): True unless motion is Off.

        Page transitions, bubble entrances, and the palette fade
        consult this — they play on Full and Subtle, and snap on
        Off. The orb/particles read _motion_reduced() instead.
        """
        try:
            return self._motion_level() != "off"
        except Exception:  # noqa: BLE001
            return True

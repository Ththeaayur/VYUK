# Refactor Phase 1 step 5: SettingsMixin card-group mixin, moved verbatim from gui/window_settings.py.
from voice_assistant.core.gui_facade import (
    gate_custom_accent, migrate_theme_name)
from .style import THEMES as STYLE_THEMES
from .visualizer import (
    THEMES,
)
from .style import (
    build_stylesheet,
    derive_theme,
    TYPE_CARD_TITLE,
    # Command Deck Phase E: Settings carries no colour literals of
    # its own — every value below is a style token.
    ACCENT,
    TEXT,
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
from voice_assistant.core.gui_facade import qsettings_contains, qsettings_get, qsettings_set, qsettings_store



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
        try:
            qsettings_set("appearance/glass", bool(checked), settings=self.settings)
        except Exception:  # noqa: BLE001
            pass
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
        try:
            qsettings_set("appearance/particles", bool(checked), settings=self.settings)
        except Exception:  # noqa: BLE001
            pass
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
            try:
                scale = float(qsettings_get("appearance/font_scale", settings=self.settings))
            except (TypeError, ValueError):
                scale = 1.0
            if scale not in (0.9, 1.0, 1.1, 1.2):
                scale = 1.0
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
        except Exception:  # noqa: BLE001 — appearance must never break boot
            pass
        self._sync_appearance_controls()

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
            density = str(qsettings_get("appearance/density", settings=self.settings))
            for d, btn in getattr(self, "density_buttons", {}).items():
                btn.setChecked(d == density)
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
        try:
            qsettings_set("motion/level", level, settings=self.settings)
            qsettings_set("motion/reduced", level in ("subtle", "off"), settings=self.settings)
        except Exception:  # noqa: BLE001
            pass
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

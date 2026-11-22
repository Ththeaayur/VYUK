# Refactor Phase 1 step 5: SettingsMixin card-group mixin, moved verbatim from gui/window_settings.py.
from voice_assistant.core.gui_facade import confirm_gate
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import (
    qsettings_contains,
    qsettings_get,
    qsettings_set,
)



class SettingsPrivacyMixin:


    # ------------------------------------------- remembered approvals
    def _w10_export_data(self):
        """v5.65 (Wave 10 — PF-4): export settings + memory +
        milestones as one verified bundle."""
        try:
            from PySide6.QtWidgets import QFileDialog
            from voice_assistant.core.gui_facade import backup as _backup
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            from voice_assistant.core.gui_facade import portability as _port
            from voice_assistant.core.gui_facade import w10_wiring as _w10
            _path, _ = QFileDialog.getSaveFileName(
                self, "Export my data", "vyuk-data-bundle.vyukbak",
                "VYUK data bundle (*.vyukbak)")
            if not _path:
                return
            # Loophole batch 3 (#10): an export is an encrypted
            # bundle under the stored backup passphrase — without
            # one set, the export refuses and says so (never a
            # plaintext file).
            _passphrase = _backup.get_backup_passphrase(
                str(_cfg_mod.DATA_DIR))
            if not _passphrase:
                self._w10_port_status.setText(
                    "Export refused: no backup passphrase is set — "
                    "set one in Settings (Advanced → Backup "
                    "passphrase) first.")
                return
            _settings = _w10.collect_portability_settings(
                _cfg_mod.config,
                qsettings_get=lambda k, d=None: (
                    qsettings_get(k, settings=self.settings)
                    if qsettings_contains(k, settings=self.settings)
                    else None))
            _rep = _port.export_bundle(str(_cfg_mod.DATA_DIR),
                                       _settings, _path,
                                       passphrase=_passphrase)
            self._w10_port_status.setText(
                f"Exported {_rep['settings_keys']} settings, "
                f"{_rep['memory_files']} memory files, "
                f"{_rep['milestone_lines']} milestone lines "
                f"({_rep['size_bytes']} bytes).")
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            try:
                self._w10_port_status.setText(
                    f"Export failed: {exc}")
            except Exception:  # noqa: BLE001
                pass

    def _w10_import_data(self):
        """v5.65 (Wave 10 — PF-4): import a bundle; the report
        lists applied / missing / extra / failed — never hidden."""
        try:
            from PySide6.QtWidgets import QFileDialog
            from voice_assistant.core.gui_facade import backup as _backup
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            from voice_assistant.core.gui_facade import portability as _port
            from voice_assistant.core.gui_facade import w10_wiring as _w10
            _path, _ = QFileDialog.getOpenFileName(
                self, "Import a data bundle", "",
                "VYUK data bundle (*.vyukbak *.zip)")
            if not _path:
                return
            # Loophole batch 3 (#10): an import decrypts with the
            # stored backup passphrase; a legacy plain bundle or
            # a wrong passphrase is refused by import_bundle and
            # reported in the status line below.
            _rep = _port.import_bundle(
                _path, str(_cfg_mod.DATA_DIR),
                passphrase=_backup.get_backup_passphrase(
                    str(_cfg_mod.DATA_DIR)))
            _applied = _w10.apply_portability_settings(
                _rep.get("settings", {}),
                config_save_fn=_cfg_mod.save_env_setting,
                qsettings_set=lambda k, v: qsettings_set(
                    k, v, settings=self.settings))
            self._w10_port_status.setText(
                f"Imported: applied "
                f"{len(_rep.get('applied', []))} files, "
                f"{len(_applied['applied'])} settings; missing "
                f"{len(_rep.get('missing', []))}, extra "
                f"{len(_rep.get('extra', []))}, failed "
                f"{len(_rep.get('failed', []))}"
                + (f"; skipped settings: "
                   f"{', '.join(_applied['skipped'])}"
                   if _applied["skipped"] else "") + ".")
            self._apply_saved_settings()
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            try:
                self._w10_port_status.setText(
                    f"Import failed (nothing applied): {exc}")
            except Exception:  # noqa: BLE001
                pass

    def _w10_refresh_models(self):
        """v5.65 (Wave 10 — PF-13): list installed models + sizes."""
        try:
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            from voice_assistant.core.gui_facade import model_manager as _mm
            _found = _mm.discover_models(str(_cfg_mod.DATA_DIR))
            _models = _found.get("models", _found) \
                if isinstance(_found, dict) else []
            self._w10_model_combo.clear()
            if not _models:
                self._w10_models_label.setText(
                    "No models installed in the scanned locations.")
                return
            _lines = []
            _total = 0
            for _m in _models:
                _mid = _m.get("id", _m.get("name", "?")) \
                    if isinstance(_m, dict) else str(_m)
                _size = _m.get("size_bytes", 0) \
                    if isinstance(_m, dict) else 0
                _total += int(_size or 0)
                _lines.append(f"{_mid}: {_size} bytes")
                self._w10_model_combo.addItem(str(_mid))
            self._w10_models_label.setText(
                f"{len(_models)} model(s), {_total} bytes total.\n"
                + "\n".join(_lines))
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            try:
                self._w10_models_label.setText(
                    f"Model list unavailable: {exc}")
            except Exception:  # noqa: BLE001
                pass

    def _w10_remove_model(self):
        """v5.65 (Wave 10 — PF-13): remove the selected model and
        reclaim its space; the tier resolution falls back cleanly."""
        try:
            from .design_system.dialogs import HudDialog
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            from voice_assistant.core.gui_facade import model_manager as _mm
            _mid = self._w10_model_combo.currentText()
            if not _mid:
                return
            if not HudDialog.confirm(
                self, "Remove model",
                f"Remove '{_mid}' and reclaim its space? VYUK "
                "will fall back to the next available tier.",
                # UI interaction slice: a destructive question must
                # never default to Yes — Enter keeps the model.
                destructive=True):
                return
            _rep = _mm.remove_model(str(_cfg_mod.DATA_DIR), _mid)
            self._w10_models_label.setText(
                f"Removed '{_mid}': {_rep}.")
            self._w10_refresh_models()
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            try:
                self._w10_models_label.setText(
                    f"Removal failed: {exc}")
            except Exception:  # noqa: BLE001
                pass

    def _on_autostart_toggled(self, checked):
        """v5.65 (Wave 10 — PF-24): autostart toggle, both ways.

        Checking records consent first (the check IS the consent)
        and then enables through the frozen mechanism; unchecking
        turns it off (removal needs no consent). A failed mechanism
        call reverts the box and reports — never a silent no-op.
        """
        if getattr(self, "_syncing_autostart", False):
            return
        try:
            from voice_assistant.core.gui_facade import lifecycle as _lc
            if checked:
                _lc.record_autostart_consent(True)
            _res = _lc.set_autostart(bool(checked))
            if not _res.applied:
                self._syncing_autostart = True
                try:
                    self.chk_autostart.setChecked(not checked)
                finally:
                    self._syncing_autostart = False
            _label = getattr(self, "presence_status_label", None)
            if _label is not None:
                _label.setText(_res.message)
        except Exception as exc:  # noqa: BLE001 — never break Settings
            try:
                self._syncing_autostart = True
                try:
                    self.chk_autostart.setChecked(not checked)
                finally:
                    self._syncing_autostart = False
                _label = getattr(self, "presence_status_label", None)
                if _label is not None:
                    _label.setText(f"Auto-start could not change: {exc}")
            except Exception:  # noqa: BLE001
                pass

    # F-F402 reversal (loophole #2): a paused kind is shown with
    # this suffix in the Settings list. Kind names are tool names
    # (snake_case identifiers), so the suffix can never collide
    # with a real kind name.
    _PAUSED_SUFFIX = " — paused"

    def _allowlist_kind_of(self, item):
        """The raw kind for a list item (the paused suffix, if
        any, is display-only)."""
        text = item.text() if item is not None else ""
        if text.endswith(self._PAUSED_SUFFIX):
            return text[:-len(self._PAUSED_SUFFIX)]
        return text

    def _refresh_allowlist(self):
        """Re-read the confirm allowlist into the Settings
        list (v5.54.1). Guarded: _apply_saved_settings also
        calls this, and it may run before the card is built."""
        lst = getattr(self, "allowlist_list", None)
        if lst is None:
            return
        try:
            kinds = confirm_gate.list_allowlisted()
        except Exception:  # noqa: BLE001 — the list says so
            kinds = []
        try:
            paused = set(confirm_gate.list_paused_allowlisted())
        except Exception:  # noqa: BLE001 — unpaused, then
            paused = set()
        lst.clear()
        for kind in kinds:
            lst.addItem(kind + (self._PAUSED_SUFFIX
                                if kind in paused else ""))
        label = getattr(self, "allowlist_status_label", None)
        if label is not None:
            if kinds:
                label.setVisible(False)
            else:
                label.setText(
                    "Nothing remembered — VYUK asks before "
                    "every action that needs it.")
                label.setVisible(True)

    def _on_allowlist_remove(self):
        """Forget the selected kind (v5.54.1)."""
        lst = getattr(self, "allowlist_list", None)
        if lst is None:
            return
        item = lst.currentItem()
        if item is None:
            return
        try:
            confirm_gate.revoke_kind(self._allowlist_kind_of(item))
        except Exception:  # noqa: BLE001 — refresh shows the truth
            pass
        self._refresh_allowlist()

    def _on_allowlist_resume(self):
        """Resume the selected paused kind (F-F402 reversal):
        the always-allow stands again, and VYUK stops asking
        for that kind."""
        lst = getattr(self, "allowlist_list", None)
        if lst is None:
            return
        item = lst.currentItem()
        if item is None:
            return
        try:
            confirm_gate.resume_allowlist(
                self._allowlist_kind_of(item))
        except Exception:  # noqa: BLE001 — refresh shows the truth
            pass
        self._refresh_allowlist()

    def _open_always_allow_screen(self):
        """Open the Always-allow screen (UI rethink Build 4
        §4.4): the Remembered approvals card is the entry.
        Never raises — the card stays the way back."""
        try:
            from .always_allow_dialog import (
                open_always_allow_dialog as _open)

            _open(self)
        except Exception:  # noqa: BLE001 — the card still works
            pass

    def _on_allowlist_forget_all(self):
        """Forget every remembered kind (v5.54.1)."""
        try:
            for kind in confirm_gate.list_allowlisted():
                confirm_gate.revoke_kind(kind)
        except Exception:  # noqa: BLE001 — refresh shows the truth
            pass
        self._refresh_allowlist()

    def _refresh_settings_summary(self):
        """The Settings header's one live line: where VYUK thinks,
        plus the state that decides whether she can — the offline
        pack for the local brain, a saved key for the cloud brain."""
        label = getattr(self, "settings_summary_label", None)
        if label is None:
            return
        try:
            if self._brain_engine() == "local":
                tail = "her on-PC brain downloads on first connect"
                try:
                    from voice_assistant.core.gui_facade import storage_report

                    report = storage_report() or {}
                    if int(report.get("total_bytes", 0) or 0) > 0:
                        tail = "on-PC brain ready on this PC"
                except Exception:  # noqa: BLE001
                    pass
                label.setText(f"Thinking on this PC · {tail}")
            else:
                if self._cloud_provider() == "groq":
                    tail = ("Groq key saved" if str(getattr(config, "groq_api_key", "") or "")
                            else "add a Groq key below to connect")
                else:
                    tail = ("cloud key saved" if str(getattr(config, "api_key", "") or "")
                            else "add a cloud key below to connect")
                label.setText(f"Thinking in the cloud · {tail}")
        except Exception:  # noqa: BLE001
            pass
        # Settings redesign §4b: the card value + voice lines
        # refresh with the header — same cadence, same callers.
        self._refresh_settings_card_values()

    def _settings_card_value(self, title: str) -> str | None:
        """The current value of one Settings card, in words
        (Settings redesign §4b). None when the card has no
        single readable value — its value line stays hidden.
        Never raises: a missing control reads as no value."""
        try:
            if title == "Voice":
                combo = getattr(self, "combo_voice", None)
                if combo is not None and combo.currentText():
                    return str(combo.currentText())
                return str(getattr(config, "voice_name", "") or "")
            if title == "Whisper hours":
                chk = getattr(self, "chk_whisper_hours", None)
                if chk is None:
                    return None
                if not chk.isChecked():
                    return "Off"
                start = getattr(self, "time_whisper_start", None)
                end = getattr(self, "time_whisper_end", None)
                if start is not None and end is not None:
                    return (f"On, {start.time().toString('HH:mm')}–"
                            f"{end.time().toString('HH:mm')}")
                return "On"
            if title == "Briefing voice":
                chk = getattr(self, "chk_briefing_preset", None)
                return ("On" if chk is not None and chk.isChecked()
                        else "Off") if chk is not None else None
            if title == "Microphone":
                lbl = getattr(self, "lbl_input_dev", None)
                if lbl is not None and lbl.text():
                    text = str(lbl.text())
                    return text[len("Microphone: "):] \
                        if text.startswith("Microphone: ") else text
                return None
            if title == "Voice bridge":
                chk = getattr(self, "chk_voice_bridge", None)
                return ("On" if chk is not None and chk.isChecked()
                        else "Off") if chk is not None else None
            if title == "Sounds":
                chk = getattr(self, "chk_sounds", None)
                if chk is None:
                    return None
                if not chk.isChecked():
                    return "Off"
                slider = getattr(self, "slider_volume", None)
                if slider is not None:
                    return f"On, volume {int(slider.value())}%"
                return "On"
            if title == "Appearance":
                lbl = getattr(self, "theme_name_label", None)
                if lbl is not None and lbl.text():
                    return str(lbl.text())
                return None
            if title == "Presence":
                chk = getattr(self, "chk_autostart", None)
                if chk is None:
                    return None
                return ("Starts with Windows"
                        if chk.isChecked() else "Starts by hand")
            if title == "Notices":
                chk = getattr(self, "chk_watch_master", None)
                return ("On" if chk is not None and chk.isChecked()
                        else "Off") if chk is not None else None
            if title == "Suggestions":
                chk = getattr(self, "chk_suggestions_enabled", None)
                return ("On" if chk is not None and chk.isChecked()
                        else "Off") if chk is not None else None
            if title in ("Screen watching", "Browser control"):
                from voice_assistant.core.gui_facade import (
                    permission_grants as _pg)

                pid = ("screen" if title == "Screen watching"
                       else "browser")
                state = str(_pg.get_state(pid) or "")
                return {"allowed": "On", "denied": "Off",
                        "unset": "Not set yet"}.get(state, state or None)
            if title == "Remembered approvals":
                lst = getattr(self, "allowlist_list", None)
                if lst is None:
                    return None
                count = int(lst.count())
                return (f"{count} kind{'s' if count != 1 else ''} "
                        "remembered" if count else "None remembered")
            if title == "How VYUK thinks":
                engine = str(getattr(self, "_brain_engine", lambda: "")()
                             or "") if callable(
                                 getattr(self, "_brain_engine", None)) \
                    else ""
                if engine == "local":
                    return "Thinking on this PC"
                provider = str(getattr(self, "_cloud_provider",
                                       lambda: "gemini")()
                               or "gemini") if callable(
                                   getattr(self, "_cloud_provider", None)) \
                    else "gemini"
                return ("Thinking in the cloud with Groq"
                        if provider == "groq"
                        else "Thinking in the cloud with Gemini")
            if title == "Local brain":
                code = str(getattr(config, "preferred_language", "en-gb")
                           or "en-gb")
                label = code
                try:
                    from voice_assistant.core.gui_facade import (
                        LANGUAGES as _langs)

                    for entry in _langs:
                        if str(getattr(entry, "code", "")) == code:
                            label = str(getattr(entry, "label", code))
                            break
                except Exception:  # noqa: BLE001 — the code stands
                    pass
                voice = str(getattr(config, "local_voice", "") or "")
                return f"{label}" + (f" · {voice}" if voice else "")
            if title == "About":
                from voice_assistant.core.gui_facade import VERSION as _v

                return f"Version {_v}"
            return None
        except Exception:  # noqa: BLE001 — no value, line hidden
            return None

    def _refresh_settings_card_values(self):
        """Settings redesign §4b: refresh every card's value
        line (current value in words) and voice line (whether
        voice can change it, from the registry). Best-effort —
        a card that can't be read keeps its lines hidden."""
        try:
            from voice_assistant.core.gui_facade import (
                settings_search as _ss)
        except Exception:  # noqa: BLE001 — nothing to refresh from
            return
        for title, label in getattr(
                self, "_settings_card_value_labels", {}).items():
            try:
                value = self._settings_card_value(title)
                if value:
                    label.setText(f"Currently: {value}")
                    label.setVisible(True)
                else:
                    label.setVisible(False)
            except Exception:  # noqa: BLE001 — one card never breaks all
                continue
        for title, label in getattr(
                self, "_settings_card_voice_labels", {}).items():
            try:
                label.setText(_ss.card_voice_line(title))
                label.setVisible(True)
            except Exception:  # noqa: BLE001 — one card never breaks all
                continue

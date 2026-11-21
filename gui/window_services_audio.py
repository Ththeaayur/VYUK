"""MainWindow services audio mixin: audio devices + preference sliders.

Split from gui/window_services.py (GUI split #10 batch 2, move only).
Bodies moved verbatim; behaviour is unchanged.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import wake_word_detector
from voice_assistant.core.gui_facade import qsettings_get, qsettings_remove, qsettings_remove_noted, qsettings_set, qsettings_set_noted


class ServicesAudioMixin:
    """Audio-device + preference-slider handlers (GUI split #10 batch 2)."""

    def _populate_audio_devices(self):
        try:
            devs = audio_manager.get_devices()
            # v5.52 (item 3): the rebuild must not fire
            # _on_input_device_changed mid-build — the combo
            # passes through "Automatic" (data None) while it
            # is being cleared/refilled, and the handler would
            # read that as the user choosing Automatic, flipping
            # a manual pin to auto and deleting the pick keys
            # before the restore below ever reads them.
            self.combo_in_dev.blockSignals(True)
            try:
                self.combo_in_dev.clear()
                self.combo_in_dev.addItem(
                    "Automatic (this PC's default)", None)
                for d in devs.get("inputs", []):
                    self.combo_in_dev.addItem(f"{d['name']}", d["index"])
            finally:
                self.combo_in_dev.blockSignals(False)
            # v5.17 (Ears A3): restore the saved choice and hand it
            # to the audio manager (it reads the field when its
            # streams next start).
            # v5.37: the pick is restored by index AND name through
            # the resolver — Windows re-indexes devices, so the raw
            # index alone can now point at a silent or output-only
            # device. A pick that moved is re-found by name (and
            # the stored index self-heals); a pick that is gone
            # falls back to the system default for this run, and
            # the stored settings are KEPT — the device may simply
            # be unplugged today.
            # v5.52 (item 3) MIGRATION + MODE: the user's rule is
            # that the PC's own default mic is always the one
            # used. A stored mode decides: "manual" (a deliberate
            # Settings pin) keeps the v5.37 restore below;
            # anything else is AUTOMATIC. An install with a stored
            # pick but NO stored mode (every pre-v5.52 install)
            # reads Automatic — the spec table's default for
            # audio/input_mode (GAPS-11: the old migrate-on-read
            # write-back is gone; a read gets "auto" either way).
            # The pick keys are KEPT (the device stays in the list
            # above; selecting it again re-pins it as a manual
            # override).
            saved = qsettings_get("audio/input_device", settings=self.settings)
            saved_name = qsettings_get("audio/input_device_name", settings=self.settings)
            mode = qsettings_get("audio/input_mode", settings=self.settings)
            manual = (mode == "manual" and saved is not None)
            audio_manager.input_mode = "manual" if manual else "auto"
            if manual:
                try:
                    saved_idx: int | None = int(saved)
                except (TypeError, ValueError):
                    saved_idx = None
                if saved_idx is not None:
                    index, name, note = audio_manager.resolve_saved_input(
                        saved_idx,
                        str(saved_name) if saved_name is not None else None)
                    if note:
                        print(f"[Audio] {note}")
                    if index is not None:
                        combo_idx = self.combo_in_dev.findData(index)
                        if combo_idx >= 0:
                            self.combo_in_dev.setCurrentIndex(combo_idx)
                        audio_manager.input_device = index
                        audio_manager.input_device_name = name
                        if index != saved_idx:
                            qsettings_set("audio/input_device", index, settings=self.settings)
                    else:
                        audio_manager.input_device = None
                        audio_manager.input_device_name = None
            else:
                # Automatic: hand the manager the resolved system
                # default for display purposes; resolution at
                # stream start re-resolves fresh regardless.
                index, name, _note = audio_manager.resolve_saved_input(
                    None, None)
                audio_manager.input_device = index
                audio_manager.input_device_name = name
            self._refresh_input_device_label()
            # Field-check fix #12 (M11a): the speaker combo,
            # filled + restored in the same call as the mic
            # combo. No mode gate on output: a stored pick
            # governs (resolved by name, like the input pick);
            # no pick means PortAudio's own default (None).
            # A pick that is gone falls back to the system
            # default output for this run, and the stored
            # settings are KEPT — the device may simply be
            # unplugged today.
            self._populate_output_devices(devs)
        except Exception:  # noqa: BLE001, S110
            pass

    def _populate_output_devices(self, devs):
        """Fill combo_out_dev + restore the saved speaker pick.
        Split out of _populate_audio_devices so the output half
        reads on its own; called with the same device listing.
        The combo may not exist yet when this runs (an older
        caller building only the mic half) — then there is
        nothing to fill."""
        combo = getattr(self, "combo_out_dev", None)
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("Automatic (this PC's default)", None)
            for d in devs.get("outputs", []):
                combo.addItem(f"{d['name']}", d["index"])
        finally:
            combo.blockSignals(False)
        saved = qsettings_get("audio/output_device", settings=self.settings)
        saved_name = qsettings_get(
            "audio/output_device_name", settings=self.settings)
        if saved is None and saved_name is None:
            audio_manager.output_device = None
            audio_manager.output_device_name = None
            self._refresh_output_device_label()
            return
        try:
            saved_idx: int | None = int(saved)
        except (TypeError, ValueError):
            saved_idx = None
        index, name, note = audio_manager.resolve_saved_output(
            saved_idx,
            str(saved_name) if saved_name is not None else None)
        if note:
            print(f"[Audio] {note}")
        if index is not None:
            combo_idx = combo.findData(index)
            if combo_idx >= 0:
                combo.setCurrentIndex(combo_idx)
            audio_manager.output_device = index
            audio_manager.output_device_name = name
            if saved_idx is not None and index != saved_idx:
                qsettings_set(
                    "audio/output_device", index, settings=self.settings)
        else:
            audio_manager.output_device = None
            audio_manager.output_device_name = None
        self._refresh_output_device_label()

    def _refresh_input_device_label(self):
        """v5.52 (item 3) visibility: the Settings mic row names
        the EFFECTIVE device and its source, in words."""
        try:
            label = getattr(self, "lbl_input_dev", None)
            if label is None:
                return
            mode = qsettings_get("audio/input_mode", settings=self.settings)
            if mode == "manual" and audio_manager.input_device_name:
                label.setText(
                    f"Your pick: {audio_manager.input_device_name}")
            else:
                from voice_assistant.core.gui_facade import AudioManager
                in_name, _out = AudioManager.default_device_names()
                label.setText(
                    f"Automatic — using this PC's default: {in_name}")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------- v5.17 ears/mouth
    def _slider_row(self, layout, label_text, lo, hi, value, fmt, handler):
        """A labelled settings slider in the kokoro-pace idiom:
        muted label, 180px slider, live value readout. The value is
        set BEFORE the handler connects, so building a card never
        fires a save."""
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setObjectName("muted")
        row.addWidget(lbl)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setFixedWidth(180)
        slider.setValue(value)
        value_lbl = QLabel(fmt(value))
        value_lbl.setObjectName("muted")
        slider.valueChanged.connect(
            lambda v: (value_lbl.setText(fmt(v)), handler(v)))
        row.addWidget(slider)
        row.addWidget(value_lbl)
        row.addStretch()
        layout.addLayout(row)
        return slider, value_lbl

    def _refresh_output_device_label(self):
        """Field-check fix #12 (M11a): the Settings speaker row
        names the EFFECTIVE device and its source, in words —
        mirroring _refresh_input_device_label. "Your pick: …"
        for a stored pick, "Automatic — using this PC's
        default: …" otherwise. Guarded: the populate call runs
        before the label exists at build time. The default name
        comes from the manager instance itself, not the facade
        import — the facade resolves AudioManager lazily through
        the audio module, which the test harness stubs, so that
        import fails exactly where a label must still refresh."""
        try:
            label = getattr(self, "lbl_output_dev", None)
            if label is None:
                return
            if getattr(audio_manager, "output_device_name", None):
                label.setText(
                    f"Your pick: {audio_manager.output_device_name}")
            else:
                _in_name, out_name = audio_manager.default_device_names()
                label.setText(
                    f"Automatic — using this PC's default: {out_name}")
        except Exception:  # noqa: BLE001
            pass

    def _on_output_device_changed(self, _index: int):
        """Field-check fix #12 (M11a): the speaker chooser's
        handler — the output mirror of _on_input_device_changed
        minus the mode gate (output has no auto/manual rule: a
        pick governs, Automatic clears it). Choosing a speaker
        stores audio/output_device[_name] and hands the pick to
        the manager; the next output open resolves it by name."""
        try:
            combo = getattr(self, "combo_out_dev", None)
            if combo is None:
                return
            data = combo.currentData()
            if data is None:
                audio_manager.output_device = None
                audio_manager.output_device_name = None
                # Round-3 item 14: the user's explicit speaker
                # choice (and its clearing) must persist — a
                # removal that never lands leaves the old speaker
                # in place at the next launch. Noted on failure.
                qsettings_remove_noted(
                    "audio/output_device", settings=self.settings)
                qsettings_remove_noted(
                    "audio/output_device_name", settings=self.settings)
            else:
                audio_manager.output_device = int(data)
                name = combo.currentText()
                audio_manager.output_device_name = name
                qsettings_set_noted(
                    "audio/output_device", int(data),
                    settings=self.settings)
                qsettings_set_noted(
                    "audio/output_device_name", name,
                    settings=self.settings)
            self._refresh_output_device_label()
        except Exception:  # noqa: BLE001
            pass

    def _on_input_device_changed(self, _index: int):
        try:
            data = self.combo_in_dev.currentData()
            if data is None:
                # v5.52 (item 3): choosing "Automatic" is the
                # standing behaviour — the mode is stored, the
                # old pick keys go, and the manager is handed the
                # resolved default for display.
                audio_manager.input_mode = "auto"
                # Round-3 item 14: the user's explicit mic choice
                # (Automatic, or a manual pin below) must persist
                # — a write that never lands reverts the mic at the
                # next launch. Noted on failure, never swallowed.
                qsettings_set_noted("audio/input_mode", "auto", settings=self.settings)
                qsettings_remove_noted("audio/input_device", settings=self.settings)
                qsettings_remove_noted("audio/input_device_name", settings=self.settings)
                index, name, _note = audio_manager.resolve_saved_input(
                    None, None)
                audio_manager.input_device = index
                audio_manager.input_device_name = name
            else:
                # A deliberate device selection is a MANUAL pin
                # (v5.52 item 3): stored as mode + pick, resolved
                # by the v5.37 rules at stream start.
                # v5.37: persist the NAME with the index — the
                # restore path re-finds the pick by name when
                # Windows re-indexes the device list.
                audio_manager.input_mode = "manual"
                audio_manager.input_device = int(data)
                name = self.combo_in_dev.currentText()
                audio_manager.input_device_name = name
                qsettings_set_noted("audio/input_mode", "manual", settings=self.settings)
                qsettings_set_noted("audio/input_device", int(data), settings=self.settings)
                qsettings_set_noted("audio/input_device_name", name, settings=self.settings)
            self._refresh_input_device_label()
        except Exception:  # noqa: BLE001
            pass

    def _on_mic_gain_changed(self, value: int):
        gain = value / 100.0
        try:
            audio_manager.mic_gain = gain
            config.mic_gain = gain
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("MIC_GAIN", f"{gain:.2f}")

    def _on_noise_gate_changed(self, value: int):
        try:
            config.vad_rms_threshold = int(value)
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("VAD_RMS_THRESHOLD", str(int(value)))

    def _on_barge_in_changed(self, value: int):
        factor = value / 100.0
        try:
            config.barge_in_factor = factor
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("BARGE_IN_FACTOR", f"{factor:.2f}")

    def _on_barge_guard_changed(self, value: int):
        try:
            config.barge_in_guard_ms = int(value)
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("BARGE_IN_GUARD_MS", str(int(value)))

    def _on_barge_sustain_changed(self, value: int):
        try:
            config.barge_in_sustain_ms = int(value)
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("BARGE_IN_SUSTAIN_MS", str(int(value)))

    def _on_wake_sens_changed(self, value: int):
        sens = value / 100.0
        try:
            config.wake_sensitivity = sens
            wake_word_detector.set_sensitivity(sens)
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("WAKE_SENSITIVITY", f"{sens:.2f}")

    def _on_wake_phrase_changed(self):
        phrase = self.input_wake_phrase.text().strip().lower()
        try:
            config.wake_phrase_alt = phrase
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("WAKE_PHRASE_ALT", phrase)
        # UI settings slice: the restart wording lives on the
        # persistent label under the input too; this line
        # confirms the save itself.
        self._note_settings_feedback(
            "Extra wake phrase saved — VYUK learns it when she "
            "next starts.")

    def _on_listen_timeout_selected(self, secs: float):
        try:
            config.listen_timeout_s = float(secs)
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("LISTEN_TIMEOUT_S", str(float(secs)))
        self._sync_listen_timeout_buttons()

    def _sync_listen_timeout_buttons(self):
        try:
            current = float(getattr(config, "listen_timeout_s", 0.0) or 0.0)
        except (TypeError, ValueError):
            current = 0.0
        for secs, btn in getattr(self, "listen_timeout_buttons", {}).items():
            btn.setChecked(abs(float(secs) - current) < 0.01)

    def _on_ptt_mode_selected(self, mode: str):
        qsettings_set("ears/ptt_mode", mode, settings=self.settings)
        self._ptt_toggled_on = False
        self._sync_ptt_mode_buttons()
        # Voice-control batch (2026-11-16, P6): a mode flip is a
        # transient-state reset like a (re)connect — the toggle
        # AND the hold that belonged to the old mode end here,
        # and the mic returns to the new mode's idle state, which
        # is MUTED in both modes (hold: muted until held; toggle:
        # muted until toggled on). The first version of this fix
        # synced is_muted from the mute button — wrong: the PTT
        # unmute never touches btn_mute, so the button reads
        # unchecked while the mic is open and the strand
        # survived. Muting is the fail-closed direction — one
        # hold or toggle press re-opens it. The deliberate mute
        # button is untouched (PTT writes never take it, per
        # _sync_muted_label's own rule); the mic button's active
        # property follows the mute, as _ptt_set_active does.
        # Flipping hold→toggle mid-hold stranded the hold flag
        # too (release is a no-op in toggle mode), so it resets
        # here as well.
        try:
            self._ptt_hold_active = False
        except Exception:  # noqa: BLE001 — the flip never breaks
            pass
        try:
            audio_manager.is_muted = True
        except Exception:  # noqa: BLE001
            pass
        try:
            mic_btn = getattr(self, "btn_mic", None)
            if mic_btn is not None:
                mic_btn.setProperty("active", "false")
                mic_btn.setStyle(mic_btn.style())
        except Exception:  # noqa: BLE001
            pass

    def _sync_ptt_mode_buttons(self):
        mode = str(qsettings_get("ears/ptt_mode", settings=self.settings))
        for m, btn in getattr(self, "ptt_mode_buttons", {}).items():
            btn.setChecked(m == mode)

    def _on_media_pause_toggled(self, on: bool):
        qsettings_set("ears/pause_while_media", bool(on), settings=self.settings)
        # UI settings slice: read by the media path at the next
        # media event (window_session.py), so the change applies
        # then, not mid-playback — and it had no feedback.
        self._note_settings_feedback(
            "Pause while media plays on — applies the next time "
            "media plays." if on else
            "Pause while media plays off — applied.")

    def _start_mic_sample(self, mode: str):
        self._mic_sample_mode = mode
        self._mic_samples = []
        self.btn_mic_test.setEnabled(False)
        self.btn_mic_calibrate.setEnabled(False)
        self.lbl_mic_test.setText(
            "Listening to the room… stay quiet." if mode == "calibrate"
            else "Listening… talk at your normal volume.")
        self._mic_sample_timer.start()

    def _mic_sample_tick(self):
        try:
            self._mic_samples.append(float(audio_manager.mic_level))
        except Exception:  # noqa: BLE001
            self._mic_samples.append(0.0)
        want = 30 if self._mic_sample_mode == "calibrate" else 40
        if len(self._mic_samples) < want:
            return
        self._mic_sample_timer.stop()
        self.btn_mic_test.setEnabled(True)
        self.btn_mic_calibrate.setEnabled(True)
        samples = self._mic_samples or [0.0]
        if self._mic_sample_mode == "calibrate":
            # mic_level ≈ RMS × 5 in float domain; the gate lives in
            # int16 RMS units, set 2.5× above the room's quiet level.
            ambient_rms = (sum(samples) / len(samples)) / 5.0
            threshold = int(max(150, min(4000, round(ambient_rms * 32768 * 2.5))))
            self.slider_noise_gate.setValue(threshold)
            self.lbl_mic_test.setText(
                f"Noise gate set to {threshold} — just above your room's quiet level.")
        else:
            peak = max(samples)
            if peak < 0.05:
                verdict = "Barely a whisper reached her — move closer, or raise the microphone gain."
            elif peak > 0.98:
                verdict = "That clipped — lower the microphone gain a little."
            else:
                verdict = "Good level — she hears you clearly at that volume."
            self.lbl_mic_test.setText(f"Peak level {peak:.2f}. {verdict}")
        self._mic_sample_mode = None

    def _pronunciations_as_lines(self) -> str:
        try:
            import json as _json
            data = _json.loads(str(getattr(config, "pronunciations_json", "") or ""))
            if isinstance(data, dict):
                return "\n".join(
                    f"{k} = {v}" for k, v in sorted(data.items())
                    if isinstance(v, str))
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _on_pronunciations_saved(self):
        import json as _json
        pairs: dict[str, str] = {}
        for line in self.input_pronunciations.toPlainText().splitlines():
            line = line.strip()
            if not line:
                continue
            sep = "=" if "=" in line else (":" if ":" in line else None)
            if sep is None:
                continue
            word, sounds = line.split(sep, 1)
            word, sounds = word.strip().lower(), sounds.strip()
            if word and sounds:
                pairs[word] = sounds
        try:
            config.pronunciations_json = _json.dumps(pairs)
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting(
            "PRONUNCIATIONS_JSON", str(getattr(config, "pronunciations_json", "") or ""))
        self.lbl_pronunciations.setText(
            f"Saved {len(pairs)} — she'll say them that way from now on."
            if pairs else "Cleared — she'll say words as written.")

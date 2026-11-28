"""MainWindow services speech mixin: whisper/earcon/cues, activity days,
speech transport/pace, mic health/telemetry, pills/caption.

Split from gui/window_services.py (GUI split #10 batch 2, move only).
Bodies moved verbatim; behaviour is unchanged.
"""

import datetime
import time
from pathlib import Path
from PySide6.QtGui import QColor
from voice_assistant.core.gui_facade import perf as _perf  # Fix 6: own-process load read for the briefing line
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import wake_word_detector
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set
from .window_services_confirm import (
    _mic_health_presentation,  # GUI split #10 batch 2 INTENTIONAL: moved with the speech methods that call it
    # (batch 1's facade imported it for the remainder; the facade keeps its own re-export).
)


class ServicesSpeechMixin:
    """Speech-transport + mic-health + pill/caption handlers (GUI split #10 batch 2)."""


    def _on_whisper_hours_toggled(self, on: bool):
        try:
            config.whisper_hours_enabled = bool(on)
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("WHISPER_HOURS_ENABLED", "true" if on else "false")
        # UI settings slice: read per speech enqueue from config
        # (core/voice/local_client.py), so the change applies to the
        # next thing she hears — and it had no feedback.
        self._note_settings_feedback(
            "Whisper hours on — applies to the next thing she hears."
            if on else
            "Whisper hours off — applies to the next thing she hears.")

    def _on_whisper_times_changed(self, *_args):
        start = self.time_whisper_start.time().toString("HH:mm")
        end = self.time_whisper_end.time().toString("HH:mm")
        try:
            config.whisper_start = start
            config.whisper_end = end
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("WHISPER_START", start)
        self._persist_config_setting("WHISPER_END", end)
        # UI settings slice: same per-enqueue read as the hours
        # toggle above — applies to the next thing she hears.
        self._note_settings_feedback(
            f"Whisper hours {start}–{end} — applies to the next "
            "thing she hears.")

    def _on_whisper_factor_changed(self, value: int):
        factor = value / 100.0
        try:
            config.whisper_factor = factor
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("WHISPER_FACTOR", f"{factor:.2f}")

    def _on_briefing_speed_changed(self, value: int):
        qsettings_set("voice/briefing_speed", value / 100.0, settings=self.settings)

    def _on_earcon_family_selected(self, family: str):
        try:
            self.earcons.set_family(family)
        except Exception:  # noqa: BLE001
            pass
        qsettings_set("sounds/family", family, settings=self.settings)
        self._sync_earcon_family_buttons()

    def _sync_earcon_family_buttons(self):
        family = str(qsettings_get("sounds/family", settings=self.settings))
        for fam, btn in getattr(self, "earcon_family_buttons", {}).items():
            btn.setChecked(fam == family)

    def _on_cue_toggled(self, name: str, on: bool):
        try:
            self.earcons.set_cue_enabled(name, bool(on))
        except Exception:  # noqa: BLE001
            pass
        qsettings_set(f"sounds/cue_{name}", bool(on), settings=self.settings)
        # UI settings slice: the earcons object applies this
        # live, but nothing visible said so.
        self._note_settings_feedback(
            f"Cue sound “{name}” on — applied."
            if on else f"Cue sound “{name}” off — applied.")

    # ------------------------------------------------- v5.17 senses
    @staticmethod
    def _activity_days_path():
        from voice_assistant.core.gui_facade import DATA_DIR
        return Path(DATA_DIR) / "activity_days.json"

    def _load_activity_days(self) -> dict:
        try:
            import json as _json
            path = self._activity_days_path()
            if path.exists():
                data = _json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {str(k): [int(v[0]), int(v[1])]
                            for k, v in data.items()
                            if isinstance(v, (list, tuple)) and len(v) == 2}
        except Exception:  # noqa: BLE001
            pass
        return {}

    def _save_activity_days(self):
        try:
            import json as _json
            path = self._activity_days_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_json.dumps(self._activity_days), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _sync_speech_transport(self):
        """v5.17 (Mouth M8–M11): the transport row lives only while
        she's speaking; the caption carries the sentence she's on
        (karaoke), restored when speech ends if nothing replaced it."""
        playing = bool(audio_manager.is_playing)
        if hasattr(self, "transport_row"):
            self.transport_row.setVisible(playing)
        # Sci-fi Home: the pace box rides the same condition as
        # the transport row — it shows only while she is speaking.
        if hasattr(self, "pace_box"):
            if self.pace_box.isVisible() != playing:
                self.pace_box.setVisible(playing)
        if not playing:
            if self._karaoke_text is not None:
                if self.caption_label.text() == self._karaoke_text:
                    self.caption_label.setText("")
                self._karaoke_text = None
            return
        sentence = getattr(audio_manager, "speech_sentence", None)
        if sentence:
            try:
                _idx, _total, text = sentence
            except (TypeError, ValueError):
                text = ""
            if text and self.caption_label.text() != text:
                self._karaoke_text = text
                self.caption_label.setText(text)
        left = int(getattr(audio_manager, "sentences_left", 0) or 0)
        if hasattr(self, "lbl_speech_queue"):
            if left > 1:
                self.lbl_speech_queue.setText(f"{left} sentences in this reply")
            elif left == 1:
                self.lbl_speech_queue.setText("Last sentence")
            else:
                self.lbl_speech_queue.setText("")
        if hasattr(self, "btn_speech_pause"):
            paused = bool(getattr(audio_manager, "playback_paused", False))
            self.btn_speech_pause.setText("▶ Resume her" if paused else "⏸ Pause her")

    def _on_speech_pause_toggled(self):
        try:
            audio_manager.set_playback_paused(
                not bool(getattr(audio_manager, "playback_paused", False)))
        except Exception:  # noqa: BLE001
            pass

    def _on_speech_skip(self):
        try:
            audio_manager.skip_current_sentence()
        except Exception:  # noqa: BLE001
            pass

    def _on_pace_selected(self, pace: float):
        # Rides the existing Brain-card pace path so both controls
        # (chips here, slider there) move the same config value.
        if hasattr(self, "slider_kokoro_speed"):
            self.slider_kokoro_speed.setValue(int(round(pace * 100)))
        else:
            try:
                config.kokoro_speed = pace
                self._persist_config_setting("KOKORO_SPEED", str(pace))
            except Exception:  # noqa: BLE001
                pass
        self._sync_pace_buttons()

    def _sync_pace_buttons(self):
        try:
            current = float(getattr(config, "kokoro_speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            current = 1.0
        for value, btn in getattr(self, "pace_buttons", {}).items():
            btn.setChecked(abs(float(value) - current) < 0.051)

    # ---------------------------------- mic health dot (v5.51, E-2)
    def _refresh_mic_health(self):
        """Render the header mic-health dot from the LIVE audio
        manager's A9 drop counter. Called on the existing ~1 Hz
        vitals tick inside _sync_audio_telemetry (which the 25 ms
        audio_timer already drives) plus once from _setup_timers
        — deliberately NO new timer. A stream counts as having
        run when one is open now, the counter has moved, or this
        window has seen one open before (the latch keeps a
        stopped stream's final count on show instead of snapping
        back to neutral). Never raises: a display must not sink
        the audio it reports."""
        dot = getattr(self, "mic_health_dot", None)
        if dot is None:
            return
        try:
            try:
                count = int(audio_manager.get_mic_drop_count())
            except Exception:  # noqa: BLE001
                count = int(getattr(audio_manager,
                                    "mic_drop_count", 0) or 0)
            running = getattr(audio_manager, "_in_stream",
                              None) is not None
            if running:
                self._mic_stream_seen = True
            stream_ran = bool(getattr(self, "_mic_stream_seen",
                                      False)) or running or count > 0
            key, colour, tip = _mic_health_presentation(
                count, stream_ran)
            self._mic_health_count = count
            if key != getattr(self, "_mic_health_key", None):
                self._mic_health_key = key
                dot.setStyleSheet(
                    f"color: {colour}; font-size: 12px;"
                    f" padding: 6px 2px;")
                # Command Deck Phase D: the one Phase D sensor
                # producer. Emitted on key change only (not the
                # 1 Hz tick), from the same live counter the dot
                # renders — never a staged value. A dropping mic
                # stays "active" (it IS running) with its drop
                # count as the value; no stream yet is "off".
                try:
                    from voice_assistant.gui.state_bus import (
                        SensorState,
                        get_state_bus,
                    )
                    if key == "no_stream":
                        _sensor = SensorState(
                            name="MIC", state="off", value="",
                            unit="",
                            source="audio_manager A9 drop counter")
                    elif key == "healthy":
                        _sensor = SensorState(
                            name="MIC", state="active", value="0",
                            unit="drops",
                            source="audio_manager A9 drop counter")
                    else:
                        _sensor = SensorState(
                            name="MIC", state="active",
                            value=str(count), unit="drops",
                            source="audio_manager A9 drop counter")
                    get_state_bus().emit(_sensor)
                except Exception:  # noqa: BLE001 — bus never breaks
                    pass
            dot.setToolTip(tip)
        except Exception:  # noqa: BLE001
            pass

    def _on_audio_timer(self):
        """Single-shot audio-timer tick (GAPS-30).

        Re-arms the 25 ms timer AFTER the tick finishes, so at
        most one timeout is ever pending: the cadence stays
        25 ms while the tick is fast and degrades gracefully
        when it is not, instead of queueing timeout events
        faster than they are consumed (which starved the event
        loop — see _setup_timers). Re-armed in finally so a
        failing tick never silences telemetry.
        """
        try:
            self._sync_audio_telemetry()
        finally:
            self.audio_timer.start(25)

    def _sync_audio_telemetry(self):
        # The facade re-resolves audio_manager per access (no
        # cache), but this module's top-level import bound the
        # value at first import — a test harness that replaces
        # the audio stub per test (WindowBase._install_shim)
        # would leave the telemetry (and the mic ring) reading a
        # stale fake while the leg drives the current one.
        # Resolve fresh here so every read below is live.
        import voice_assistant.core.gui_facade as _facade
        audio_manager = _facade.audio_manager
        self._tick += 1
        if audio_manager.is_playing:
            energy = audio_manager.speaker_level
        else:
            energy = audio_manager.mic_level if not audio_manager.is_muted else 0.0
        bands = audio_manager.display_bands(audio_manager.is_playing)
        self.visualizer.update_audio_data(energy, bands)
        # v5.18 (Eyes E4): feed the waveform ribbon from the same
        # tick — her voice while she speaks, the room while she
        # listens, a resting line otherwise.
        ribbon = getattr(self, "wave_ribbon", None)
        if ribbon is not None:
            try:
                if audio_manager.is_playing:
                    ribbon.push(audio_manager.speaker_level, "speaking")
                elif audio_manager.is_muted:
                    ribbon.push(0.0, "idle")
                else:
                    ribbon.push(audio_manager.mic_level, "listening")
            except Exception:  # noqa: BLE001
                pass
        # UI Phase 1 (H-32): the mic meter is superseded by the ring
        # around the composer mic button (micLevelArc). The ring
        # shows only while listening; its level is the live mic
        # level. The old mic_meter is deleted with the v6.3 stage.
        try:
            arc = getattr(self, "mic_level_arc", None)
            if arc is not None:
                lvl = 0.0 if audio_manager.is_playing else (
                    0.0 if audio_manager.is_muted
                    else audio_manager.mic_level)
                arc.set_level(lvl)
                listening = "LISTENING" in (
                    getattr(self, "_status_raw", "") or "")
                visible = bool(listening and not audio_manager.is_muted)
                if arc.isVisible() != visible:
                    arc.setVisible(visible)
        except Exception:  # noqa: BLE001
            pass
        # v5.17 (Mouth M8–M11): karaoke caption + transport row.
        try:
            self._sync_speech_transport()
        except Exception:  # noqa: BLE001
            pass
        # Presence pack: the mini orb mirrors the same audio, but only
        # while it is on screen (it animates itself otherwise).
        mini = getattr(self, "mini_orb", None)
        if mini is not None and mini.isVisible():
            try:
                mini.set_audio(energy, bands)
            except Exception:  # noqa: BLE001
                pass

        # CPU/RAM values for the briefing line, ~1 Hz.
        # Refinement A: the same tick refreshes the one-line briefing
        # under the state label (date · time · CPU · RAM · battery) —
        # one psutil read, reused for both, missing pieces omitted.
        try:
            now = time.monotonic()
            if now - getattr(self, "_vitals_last", 0.0) >= 1.0:
                self._vitals_last = now
                # v5.51 (E-2): the mic health dot rides this same
                # ~1 Hz tick — no new timer for a slow counter.
                try:
                    self._refresh_mic_health()
                except Exception:  # noqa: BLE001
                    pass
                # v5.17 (Ears A13): one second of LISTENING/SPEAKING
                # per tick, accrued per day for the sparkline.
                try:
                    raw = self._status_raw or ""
                    if "SPEAKING" in raw or "LISTENING" in raw:
                        day = datetime.date.today().isoformat()
                        rec = self._activity_days.setdefault(day, [0, 0])
                        rec[1 if "SPEAKING" in raw else 0] += 1
                        if len(self._activity_days) > 14:
                            for _old in sorted(self._activity_days)[:-14]:
                                del self._activity_days[_old]
                        self._save_activity_days()
                        if hasattr(self, "sparkline"):
                            self.sparkline.set_days(self._activity_days)
                except Exception:  # noqa: BLE001
                    pass
                psutil = getattr(self, "_psutil_mod", None)
                if psutil is None:
                    try:
                        import psutil as _ps
                        psutil = _ps
                    except ImportError:
                        psutil = False
                    self._psutil_mod = psutil
                cpu_v = ram_v = None
                if psutil:
                    cpu_v = psutil.cpu_percent(interval=None) / 100.0
                    ram_v = psutil.virtual_memory().percent / 100.0
                    # Aura-only: the orb paints no vitals arcs, so the
                    # values feed only the briefing line below.
                if hasattr(self, "briefing_label"):
                    now_dt = datetime.datetime.now()
                    # Sci-fi Home: the room's hour, cached for the
                    # light model (night is 22:00-05:59).
                    self._room_hour = now_dt.hour
                    parts = [
                        now_dt.strftime("%a %d %b"),
                        now_dt.strftime("%H:%M"),
                    ]
                    if cpu_v is not None:
                        parts.append(f"CPU {cpu_v * 100:.0f}%")
                    if ram_v is not None:
                        parts.append(f"RAM {ram_v * 100:.0f}%")
                    if psutil:
                        try:
                            batt = psutil.sensors_battery()
                            if batt is not None:
                                parts.append(f"Battery {batt.percent:.0f}%")
                        except Exception:  # noqa: BLE001
                            pass
                    # Fix 6 (2026-11-05): the briefing line used to
                    # show only machine-wide CPU/RAM — the numbers
                    # that read "100% CPU" while Doctor itself was
                    # compiling. Own-process RSS and the orb's frame
                    # time ride the same 1 Hz tick, so the next
                    # "not smooth" report carries numbers that say
                    # whether VYUK itself or the machine is loaded,
                    # and whether the GUI thread is keeping up.
                    try:
                        _frame = None
                        _vis = getattr(self, "visualizer", None)
                        if _vis is not None:
                            _frame = _vis.frame_report()
                        parts.extend(_perf.format_load_line(
                            _perf.process_load(), _frame))
                    except Exception:  # noqa: BLE001
                        pass
                    self.briefing_label.setText("  ·  ".join(parts))
                # UI Phase 1: the same ~1 Hz tick refreshes the
                # Home telemetry (status-block mini-bars, battery,
                # clock) and the state-aware composer placeholder.
                # _room_hour is set above inside the briefing-label
                # guard; it is also set here so the room light keeps
                # its hour even if the label is ever removed.
                try:
                    self._room_hour = datetime.datetime.now().hour
                except Exception:  # noqa: BLE001
                    pass
                try:
                    if hasattr(self, "_refresh_home_telemetry"):
                        self._refresh_home_telemetry()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    if hasattr(self, "_sync_composer_placeholder"):
                        self._sync_composer_placeholder()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        accent = self.visualizer.accent_color()
        self.waveform.set_accent(accent)
        self.waveform.update_data(energy, bands)
        # Phase 2 (chatbox #19): dock width is only spent on the
        # waveform while audio is actually flowing. UI rethink
        # Build 1: and only while it fits — _dock_waveform_allowed
        # (see _apply_dock_narrow_rule) is False when the waveform
        # would crush the input, so the ribbon never takes the
        # input's width again.
        self.waveform.setVisible(
            isinstance(energy, (int, float)) and energy > 0.01
            and getattr(self, "_dock_waveform_allowed", True))

        # Phase 3: audio-reactive glow on the hero mic button.
        # Zero energy keeps only a subtle halo; loud voice blooms.
        try:
            shadow = getattr(self, "_mic_shadow", None)
            if shadow is not None:
                e = max(0.0, min(1.0, float(energy or 0.0)))
                shadow.setBlurRadius(18 + e * 38)
                shadow.setColor(QColor(
                    accent.red(), accent.green(), accent.blue(),
                    60 + int(e * 180)))
                shadow.setOffset(0, 6)
        except Exception:  # noqa: BLE001
            pass

        # Phase 2 (chatbox #12): the speaking equaliser sits in
        # the state row under the orb — bubbles stay static.
        _eq = getattr(self, "speaking_eq", None)
        if _eq is not None:
            speaking = "SPEAKING" in self._status_raw
            _eq.setVisible(speaking)
            if speaking:
                _eq.set_data(energy, bands)

        # Animated dots while connecting
        if self._status_raw.startswith("CONNECTING"):
            dots = "." * (1 + (self._tick // 8) % 3)
            self.state_label.setText(f"Connecting{dots}")

        # Sci-fi Home: the room's light follows the real state —
        # the shell glow is the wash now (see _sync_room_light).
        try:
            self._sync_room_light(energy)
        except Exception:  # noqa: BLE001 — light never breaks telemetry
            pass

    def _sync_room_light(self, energy):
        """Sci-fi Home: drive the room's one light from the live state.

        The shell glow IS the wash (see gui/room_home) — retuned to
        the light table instead of the old fixed strengths (0.35 +
        energy x 0.5 connected, 0.15 offline). The wash eases toward
        its target over 220 ms on the telemetry tick; the shell
        itself is still only repainted at the ~10 Hz cadence. Night
        dims the wash and the particle field together. Guarded
        throughout: light never breaks telemetry.
        """
        try:
            from voice_assistant.gui.room_home import (
                is_night, shell_strength, state_from)
        except Exception:  # noqa: BLE001 — no light without the model
            return
        try:
            room = getattr(self, "_room_light", None)
            if room is None:
                return
            hour = getattr(self, "_room_hour", None)
            if hour is None:
                hour = datetime.datetime.now().hour
            night = is_night(hour)
            if getattr(self, "_room_night", None) != night:
                self._room_night = night
                field = getattr(self, "particle_field", None)
                if field is not None:
                    try:
                        field.set_night(night)
                    except Exception:  # noqa: BLE001
                        pass
            try:
                # Resolve fresh via the facade (same staleness
                # class as _sync_audio_telemetry, leg 35): the
                # module-top audio_manager is the FIRST test's
                # fake, while the live fake is the current one.
                import voice_assistant.core.gui_facade as _facade
                muted = bool(_facade.audio_manager.is_muted)
            except Exception:  # noqa: BLE001
                muted = False
            try:
                pill = getattr(self, "goal_pill", None)
                busy = bool(pill is not None and pill.isVisible())
            except Exception:  # noqa: BLE001
                busy = False
            try:
                in_flight = bool(self._connection_in_flight())
            except Exception:  # noqa: BLE001
                in_flight = False
            state = state_from(
                getattr(self, "_status_raw", ""),
                connected=bool(getattr(self, "_connected", False)),
                in_flight=in_flight, muted=muted, busy=busy,
                fatal=bool(getattr(self, "_last_status_fatal", False)))
            wash = room.update(state, energy=energy, hour=hour, dt_ms=25.0)
            if self._tick % 4 == 0:
                shell = getattr(self, "shell", None)
                if shell is not None:
                    strength = shell_strength(wash)
                    if strength is not None:
                        center = self.visualizer.mapTo(
                            shell, self.visualizer.rect().center())
                        shell.set_glow(self.visualizer.accent_color(),
                                       strength, center)
        except Exception:  # noqa: BLE001 — light never breaks telemetry
            pass

    # ------------------------------------------------------------ handlers
    def _set_pill(self, text: str):
        # Command Deck Phase D: the pill is the §7 status word in
        # its role colour — resolved through
        # design_system.session_states, never a hardcoded hex
        # (D2: Listening/Speaking are cyan; green is done/healthy
        # only). The same word/role/glyph is emitted on the state
        # bus for the StatusStrip's secondary chips; the bus is
        # cosmetic, so every fact below is gathered defensively
        # and the emit can never break the pill itself.
        # UI rethink Build 1: the header shows one plain state word
        # (STATUS_DISPLAY_WORDS), not the HUD word, and the label
        # is quiet text in the role colour — no background, no
        # border, no padding. The bus still emits the HUD word, so
        # the pill, the bus and the strip can never disagree about
        # the state, only about how the header words it.
        # Conversion Wave 9: the v564 glyph beside the pill shows
        # the status glyph (synced from the §7 word) — hidden since
        # Build 1, the object stays and the sync stays.
        from voice_assistant.gui.design_system.session_states import (
            STATUS_DISPLAY_WORDS,
            resolve_mode,
            resolve_status,
        )
        from voice_assistant.gui.state_bus import AppState, get_state_bus
        from voice_assistant.gui.style import NEUTRAL

        word, role, _glyph, colour = resolve_status(text)
        # UI Phase 1: the state word lives on the Home state line
        # (homeStateLine, under the wordmark), coloured with the
        # visualizer's exact accent hex (leg 70; the accent passes
        # H-39's 4.5:1 on dark and Light). The vitals strip's
        # status_label is the brain source, never the state word.
        # Pre-HUD windows fall back to the old status_pill.
        line = getattr(self, "home_state_line", None)
        if line is not None:
            line.setText(STATUS_DISPLAY_WORDS.get(word, word))
            try:
                vis = getattr(self, "visualizer", None)
                accent = vis.accent_color().name() if vis is not None \
                    else colour
            except Exception:  # noqa: BLE001
                accent = colour
            line.setStyleSheet(
                f"color: {accent}; font-size: 13px;")
        else:
            self.status_pill.setText(STATUS_DISPLAY_WORDS.get(word, word))
            # Look-fix 2 (2026-11-24): a neutral-role word resolves
            # to the house NEUTRAL (#7E9AA6, ~2.5:1 on Light) — the
            # strip's set_theme re-resolves the resting sheet, but
            # this write would put the faint colour straight back
            # on the next status change. Substitute the resolved
            # theme's text_muted for the neutral role only; the
            # other role colours (cyan / green / amber / red) are
            # semantic and untouched.
            if colour == NEUTRAL:
                try:
                    _resolved = getattr(self, "_resolved_theme", None)
                    if isinstance(_resolved, dict):
                        colour = _resolved.get("text_muted", colour)
                except Exception:  # noqa: BLE001 — theme never breaks the pill
                    pass
            self.status_pill.setStyleSheet(
                f"color: {colour}; font-size: 12px;"
            )
        try:
            from .w9_wiring_theme import sync_status_glyph
            sync_status_glyph(self, word)
        except Exception:  # noqa: BLE001 — glyph is cosmetic
            pass
        try:
            mic_muted = bool(audio_manager.is_muted)
        except Exception:  # noqa: BLE001 — unknown mutes hide chips
            mic_muted = False
        try:
            speech_muted = bool(audio_manager.playback_paused)
        except Exception:  # noqa: BLE001
            speech_muted = False
        try:
            btn_wake = getattr(self, "btn_wake", None)
            if btn_wake is not None:
                wake_on = bool(btn_wake.isChecked())
            else:
                wake_on = bool(wake_word_detector.enabled)
        except Exception:  # noqa: BLE001
            wake_on = False
        # MODE is claimed only when known, from the brain-mode
        # source of truth (Command Deck Phase E): resolve_mode
        # over the fallback flag + _brain_engine() — the same
        # derivation the Settings brain-truth line uses. A
        # failure here degrades to "" (hidden), never a guess.
        try:
            mode = resolve_mode(
                bool(getattr(self, "_brain_fallback_active", False)),
                self._brain_engine())
        except Exception:  # noqa: BLE001 — unknown hides
            mode = ""
        try:
            from voice_assistant.core.gui_facade import capture_guard

            capture_active = bool(capture_guard.active_captures())
        except Exception:  # noqa: BLE001
            capture_active = False
        try:
            get_state_bus().emit(AppState(
                status=word, colour=role, glyph=_glyph,
                mic_muted=mic_muted, speech_muted=speech_muted,
                wake_on=wake_on, mode=mode,
                capture_active=capture_active,
                detail=getattr(self, "_status_caption", "") or ""))
        except Exception:  # noqa: BLE001 — bus never breaks status
            pass

    def _set_caption(self, text: str):
        self._status_caption = text
        self.caption_label.setText(text)

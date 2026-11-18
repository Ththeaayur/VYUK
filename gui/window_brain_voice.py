"""MainWindow brain mixin — language/voice/Kokoro/preview, whisper/transcription profiles, local TTS voice, brain status (GUI split #4).

Part of the composed BrainMixin in gui/window_brain.py;
methods moved verbatim from there.
"""

import json
import threading
import urllib.request
from pathlib import Path
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import (
    set_preferred_language,
)


class BrainVoiceMixin:

    def _on_language_picked(self, index: int):
        """v5.41: the Settings half of the one write path for the
        home language (core.languages.set_preferred_language):
        the language AND her voice (its curated Kokoro voice)
        change together, and the voice widgets sync through the
        same applier a manual voice pick uses."""
        code = self.combo_language.itemData(index)
        if not code:
            return
        entry = set_preferred_language(str(code))
        if entry is not None:
            self._apply_kokoro_voice(entry.voice)

    def _on_voice_auto_switch_toggled(self, on: bool):
        config.voice_auto_switch = bool(on)
        self._persist_config_setting(
            "VOICE_AUTO_SWITCH", "true" if on else "false")

    def _on_session_provenance_lock_toggled(self, on: bool):
        # v5.45 (PROGRAMME SPEAKER-LOCK), renamed v5.48
        # (programme item 24): persists like the neighbouring
        # toggles (config field + SESSION_PROVENANCE_LOCK in
        # .env). The gate itself lives in core/funnel/tools_gate.py
        # (_session_provenance_lock_gated), reading this config
        # field live.
        config.session_provenance_lock = bool(on)
        self._persist_config_setting(
            "SESSION_PROVENANCE_LOCK", "true" if on else "false")

    def _save_kokoro_voice(self):
        voice = self.input_kokoro_voice.text().strip()
        if not voice:
            return
        self._apply_kokoro_voice(voice)

    def _save_kokoro_blend(self):
        """v5.43 (MOUTH M4): persists the blend partner + amount
        like the neighbouring voice settings (config fields +
        KOKORO_BLEND_VOICE / KOKORO_BLEND_RATIO in .env). A
        non-numeric amount degrades to 0 (no blend) rather than
        storing garbage; the amount clamps to [0, 1]."""
        voice = self.input_kokoro_blend_voice.text().strip()
        raw_ratio = self.input_kokoro_blend_ratio.text().strip()
        try:
            ratio = float(raw_ratio) if raw_ratio else 0.0
        except (TypeError, ValueError):
            ratio = 0.0
        if ratio != ratio:  # NaN
            ratio = 0.0
        ratio = min(1.0, max(0.0, ratio))
        try:
            config.kokoro_blend_voice = voice
            config.kokoro_blend_ratio = ratio
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("KOKORO_BLEND_VOICE", voice)
        self._persist_config_setting(
            "KOKORO_BLEND_RATIO", f"{ratio:.2f}")

    def _on_kokoro_voice_picked(self, index: int):
        voice = self.combo_kokoro_voice.itemData(index)
        if voice:
            self._apply_kokoro_voice(str(voice))

    def _apply_kokoro_voice(self, voice: str):
        """One save path for the roster combo and the type-an-id line
        edit: persist the choice, then sync both widgets and the
        preview button to it."""
        config.kokoro_voice = voice
        self._persist_config_setting("KOKORO_VOICE", voice)
        if self.input_kokoro_voice.text() != voice:
            self.input_kokoro_voice.setText(voice)
        combo = self.combo_kokoro_voice
        index = combo.findData(voice)
        if index < 0:
            combo.addItem(f"Custom: {voice}", voice)
            index = combo.count() - 1
        if combo.currentIndex() != index:
            combo.setCurrentIndex(index)
        self._refresh_voice_preview_button()

    def _voice_preview_path(self, voice: str | None = None) -> Path:
        if voice is None:
            voice = str(self.combo_kokoro_voice.currentData()
                        or getattr(config, "kokoro_voice", "bm_george") or "bm_george")
        # v5.17 (M17): scripts b/c live beside script a as
        # <voice>__b.wav / <voice>__c.wav.
        script = getattr(self, "_preview_script", "a")
        name = f"{voice}.wav" if script == "a" else f"{voice}__{script}.wav"
        return Path(__file__).resolve().parent / "assets" / "voice_previews" / name

    def _on_preview_script_selected(self, script: str):
        self._preview_script = script
        for sid, btn in getattr(self, "preview_script_buttons", {}).items():
            btn.setChecked(sid == script)
        self._refresh_voice_preview_button()

    def _refresh_voice_preview_button(self):
        available = self._voice_preview_path().exists()
        self.btn_voice_preview.setEnabled(available)
        self.btn_voice_preview.setToolTip(
            "Hear this voice" if available else "Preview not available for this voice")

    def _play_voice_preview(self):
        # Plays the shipped preview WAV — no live session needed. Same
        # guarding as gui/sounds.py: no QtMultimedia, no sound, no fuss.
        self._stop_voice_preview()
        path = self._voice_preview_path()
        if not path.exists():
            self._refresh_voice_preview_button()
            return
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
        except Exception:  # noqa: BLE001
            return
        try:
            effect = QSoundEffect(self)
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.setVolume(float(getattr(getattr(self, "earcons", None), "volume", 0.5)))
            effect.play()
            self._voice_preview_effect = effect
        except Exception:  # noqa: BLE001
            self._voice_preview_effect = None

    def _stop_voice_preview(self):
        effect = getattr(self, "_voice_preview_effect", None)
        if effect is not None:
            try:
                effect.stop()
            except Exception:  # noqa: BLE001
                pass
            self._voice_preview_effect = None

    def _on_kokoro_speed_changed(self, value: int):
        self.lbl_kokoro_speed.setText(f"{value}%")
        speed = value / 100.0
        try:
            config.kokoro_speed = speed
        except Exception:  # noqa: BLE001
            pass
        self._persist_config_setting("KOKORO_SPEED", f"{speed:.2f}")
        self._sync_pace_buttons()

    def _on_whisper_model_changed(self, model: str):
        model = (model or "").strip()
        if not model:
            return
        config.whisper_model = model
        self._persist_config_setting("WHISPER_MODEL", model)
        # UI settings slice: read at local session build
        # (local_assets.whisper_model_arg), so the pick applies
        # at the next local connect — and it had no feedback.
        self._note_settings_feedback(
            f"Whisper model {model} — used next time you connect.")

    def _on_transcription_profile_picked(self, index: int):
        """v5.43 (A7): persist the Settings transcription-profile
        pick — the same one-field pattern as the whisper-model
        combo above; core/languages resolves what the id means."""
        profile = self.combo_transcription_profile.itemData(index)
        if profile not in ("auto", "standard", "hinglish"):
            return
        config.transcription_profile = profile
        self._persist_config_setting("TRANSCRIPTION_PROFILE", profile)
        # UI settings slice: resolved per transcription call
        # (core/voice/local_client.py _transcription_settings), so the
        # pick applies to the next transcription — and it had
        # no feedback.
        self._note_settings_feedback(
            f"Transcription profile {profile} — applies to the "
            "next transcription.")

    def _on_tool_profile_picked(self, index: int):
        """v5.45 (HANDS H13): persist the Settings tool-profile
        pick — the same one-field pattern as the transcription-
        profile combo above; core/funnel/tool_profiles resolves what
        the id means. The pick applies at the next connect /
        brain turn, where the profile shapes the resident set."""
        profile = self.combo_tool_profile.itemData(index)
        if profile not in ("assistant", "desktop", "full"):
            return
        config.tool_profile = profile
        self._persist_config_setting("TOOL_PROFILE", profile)
        # UI settings slice: the handler docstring already states
        # the timing (next connect / brain turn); this line makes
        # it visible, since the pick itself had no feedback.
        self._note_settings_feedback(
            f"Tool profile {profile} — applies at the next "
            "connect / brain turn.")

    def _save_local_tts_voice(self):
        voice = self.input_local_tts_voice.text().strip()
        config.local_tts_voice = voice
        self._persist_config_setting("LOCAL_TTS_VOICE", voice)
        # UI settings slice: the voice file is read per
        # synthesis (core/voice/local_client.py), so the save applies
        # to the next thing she says — and it had no feedback.
        self._note_settings_feedback(
            f"Local voice {voice} — used the next time she speaks.")

    def _set_brain_status(self, text: str):
        if hasattr(self, "brain_status_label"):
            self.brain_status_label.setText(text)

    def _check_local_brain(self):
        self._set_brain_status("Checking Ollama…")

        def check():
            host = str(getattr(config, "ollama_host", "http://127.0.0.1:11434") or "http://127.0.0.1:11434").rstrip("/")
            display_host = host.removeprefix("http://").removeprefix("https://")
            url_host = host if host.startswith(("http://", "https://")) else f"http://{host}"
            try:
                with urllib.request.urlopen(f"{url_host}/api/tags", timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                models = []
                for model in payload.get("models", []):
                    if isinstance(model, dict):
                        name = model.get("name") or model.get("model")
                        if name:
                            models.append(str(name))
                    elif model:
                        models.append(str(model))
                if models:
                    message = f"Ollama is running — models: {', '.join(models)}"
                else:
                    message = "Ollama is running. It has no thinking model yet — in Ollama, download one (for example qwen2.5:7b), then check again."
            except Exception:  # noqa: BLE001
                message = "Can't reach Ollama — make sure it's installed and running (from ollama.com), then check again."
            self.signals.brain_check_completed.emit(message)

        threading.Thread(target=check, daemon=True).start()

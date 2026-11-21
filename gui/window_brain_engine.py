"""MainWindow brain mixin — provider resolution, engine/backend/visibility (GUI split #4).

Part of the composed BrainMixin in gui/window_brain.py;
methods moved verbatim from there.
"""

import logging
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import qsettings_set, qsettings_set_noted


class BrainEngineMixin:

    @staticmethod
    def provider_resolution() -> dict:
        """Phase 1 (C09): the explicit provider resolution for
        the current config, from the one resolver in
        core/provider_identity — never a second inline
        normalization. Keys: provider, normalized,
        raw_engine, raw_cloud_provider, requested_cloud."""
        from voice_assistant.core.gui_facade import (
            resolve_provider as _resolve)

        return _resolve(config.brain_engine, config.cloud_provider)

    @staticmethod
    def _brain_engine() -> str:
        # Phase 1 (C09): the Settings combo only ever offers
        # gemini/local, so this still returns exactly one of
        # those two — but it is now derived from the explicit
        # resolver instead of its own inline normalization
        # (anything-not-gemini/local → gemini). The full
        # provider (incl. groq, which lives in cloud_provider)
        # is provider_resolution() above.
        # GUI split #4 INTENTIONAL: _brain_engine moved
        # verbatim to BrainEngineMixin; the self-reference
        # follows the mixin name (same staticmethod).
        resolution = BrainEngineMixin.provider_resolution()
        return "local" if resolution["provider"] == "local" else "gemini"

    @staticmethod
    def _persist_config_setting(key: str, value: str) -> bool:
        """v5.54.1: TWO fixes in one seam. First, the call
        itself: this used to call config.save_env_setting —
        but AppConfig has no such method (only the MODULE
        function save_env_setting exists, plus the save_api_key
        method), so EVERY save through here raised
        AttributeError and, second, the except swallowed it —
        a locked or read-only .env looked saved in-session
        while the next launch silently defaulted
        (BRAIN_ENGINE's default is "local" — the field flip:
        Settings brain picks never landed, ever). The call
        now goes to the module function (the pattern the
        rest of this file already uses), and a failure is
        NEVER silent again: it is logged (the log file is
        sendable; a hover tooltip is not) and reported to
        the caller, which decides what the user sees.
        Returns True when the value landed."""
        try:
            from voice_assistant.core.gui_facade import (
                save_env_setting as _save_env_setting)

            _save_env_setting(key, value)
            return True
        except Exception as e:  # noqa: BLE001 — logged, not swallowed
            logging.getLogger("nova.settings").warning(
                "could not save %s to .env (%s: %s) — the "
                "change works for this session only and will "
                "not survive a restart", key, type(e).__name__, e)
            return False

    def _on_brain_engine_changed(self, index: int):
        engine = self.combo_brain_engine.itemData(index)
        if engine not in ("gemini", "local"):
            return
        # v5.57.1 (item 3): a fresh pick is a fresh decision —
        # any fallback flag from the old session clears, and the
        # truth line refreshes with the pick (via the sync call
        # chained to this handler).
        self._brain_fallback_active = False
        config.brain_engine = engine
        if not self._persist_config_setting("BRAIN_ENGINE", engine):
            # The in-session value stands (this session thinks
            # with the picked engine), but the pick will NOT
            # survive a restart — say so in the open instead
            # of letting the next launch silently flip back.
            try:
                self._notify(
                    "I couldn't save that brain choice — it "
                    "works for now, but it won't survive a "
                    "restart. Check that voice_assistant\\.env "
                    "isn't read-only or locked, then pick the "
                    "brain again.")
            except Exception:  # noqa: BLE001 — the log line above stands
                pass
        # v5.23: picking Local here IS a brain choice — record that
        # the pack is wanted so provisioning is allowed (see
        # _local_pack_wanted). Switching to cloud leaves the flag
        # alone: a "both" user's pack stays wanted.
        # Round-3 item 14: a write that never persists means the
        # pack never downloads after an explicit Local pick — say
        # so in the open, the same shape as the .env failure above
        # (the log line + feed marker from the noted call stand
        # either way).
        if engine == "local":
            if not qsettings_set_noted(
                    "setup/local_pack_wanted", True,
                    settings=self.settings):
                try:
                    self._notify(
                        "I couldn't save that local-brain choice — "
                        "the on-PC pack won't download until it's "
                        "saved. Check that the settings file isn't "
                        "read-only, then pick Local again.")
                except Exception:  # noqa: BLE001 — the log line above stands
                    pass
        # v3.2: switching to the on-PC brain starts its pack download
        # right away (same rule as app launch), and the setup banner
        # follows the engine either way.
        self._maybe_auto_provision()
        self._refresh_setup_banner()

    def _on_brain_advanced_toggled(self, checked: bool):
        """Show/hide the Advanced card's fold. (v2.2: this was the
        Brain card's 'Advanced options' fold, keyed
        'brain/advanced_open'; v2.9 moved it into the standalone
        Advanced card under 'advanced/open' — the old key is
        orphaned, the handler and widget names are unchanged.)"""
        if hasattr(self, "brain_advanced_box"):
            self.brain_advanced_box.setVisible(bool(checked))
        if hasattr(self, "btn_brain_advanced"):
            self.btn_brain_advanced.setText(
                "Hide advanced options ▾" if checked else "Show advanced options ▸")
        try:
            qsettings_set("advanced/open", bool(checked), settings=self.settings)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _local_backend() -> str:
        backend = str(getattr(config, "local_backend", "builtin") or "builtin").strip().lower()
        return backend if backend in ("builtin", "ollama") else "builtin"

    def _on_local_backend_changed(self, index: int):
        backend = self.combo_local_backend.itemData(index)
        if backend not in ("builtin", "ollama"):
            return
        config.local_backend = backend
        self._persist_config_setting("LOCAL_BACKEND", backend)
        self._update_backend_visibility()
        # UI settings slice: the visibility sync is UI feedback
        # for the card, but the backend itself is used at
        # session creation — the timing had no feedback.
        self._note_settings_feedback(
            f"Local brain backend {backend} — used next time you "
            "connect.")

    @staticmethod
    def _local_model_file() -> str:
        return str(getattr(config, "local_model_file", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf") or "")

    def _on_brain_size_changed(self, index: int):
        # Connected to the combo's `activated` signal: user picks only.
        filename = self.combo_brain_size.itemData(index)
        if not filename:
            return
        config.local_model_file = filename
        self._persist_config_setting("LOCAL_MODEL_FILE", filename)
        self._set_brain_status(
            "Brain size set — the new model downloads on next local connect "
            "(existing model files are kept)."
        )

    def _update_backend_visibility(self):
        if not (hasattr(self, "ollama_group") and hasattr(self, "builtin_group")):
            return
        builtin = self._local_backend() == "builtin"
        self.builtin_group.setVisible(builtin)
        self.ollama_group.setVisible(not builtin)
        # v2.9: the offline-pack row now lives in "How VYUK thinks",
        # but it still belongs to the built-in backend only.
        pack_box = getattr(self, "think_pack_box", None)
        if pack_box is not None:
            pack_box.setVisible(builtin)

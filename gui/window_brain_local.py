"""MainWindow brain mixin — local setup, packs, download diagnosis/banner, provision, remove, Ollama (GUI split #4).

Part of the composed BrainMixin in gui/window_brain.py;
methods moved verbatim from there.
"""

import threading
import time
from PySide6.QtCore import (
    QTimer,
)
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import qsettings_contains, qsettings_get, qsettings_set, qsettings_set_noted


class BrainLocalMixin:

    def _setup_local_brain(self):
        # "Download her on-PC brain" — runs through the shared
        # DownloadManager in core.voice.local_assets, so a paused or interrupted
        # download resumes from its .part files instead of starting over.
        # Progress lines stream into the Brain status label; the manager's
        # done callback lands in _on_local_setup_completed via the signal.
        try:
            from voice_assistant.core.gui_facade import downloads
        except Exception as exc:  # noqa: BLE001
            self._set_brain_status(f"ERROR: on-PC brain unavailable — {exc}")
            return
        self._pack_last_line = ""

        def progress(line):
            self._pack_last_line = str(line)
            self.signals.brain_setup_progress.emit(str(line))

        def done(*_args):
            last = getattr(self, "_pack_last_line", "")
            if last.startswith("ERROR:") or "paused" in last.lower():
                final = last
            else:
                final = "Offline pack ready — model and voice are on this PC."
            self.signals.brain_setup_completed.emit(final)

        try:
            started = downloads.start(progress, done)
        except Exception as exc:  # noqa: BLE001
            self._set_brain_status(f"ERROR: download failed to start — {exc}")
            return
        if not started:
            self._set_brain_status("A download is already running.")
            return
        self._set_pack_downloading(True)
        self._set_brain_status("Downloading her on-PC brain…")

    def _set_pack_downloading(self, running: bool):
        if hasattr(self, "btn_setup_local"):
            self.btn_setup_local.setEnabled(not running)
        if hasattr(self, "btn_pause_download"):
            self.btn_pause_download.setVisible(running)

    def _pause_offline_download(self):
        try:
            from voice_assistant.core.gui_facade import downloads

            downloads.cancel()
        except Exception:  # noqa: BLE001
            pass
        self._set_brain_status("Pausing — the download will resume where it left off.")

    def _on_local_setup_completed(self, text: str):
        self._set_pack_downloading(False)
        self._set_brain_status(text)
        self._refresh_pack_storage()

    def _refresh_pack_storage(self):
        # "Offline pack on this PC: X (+ Y partial — resumes automatically)".
        # Reads data/models + data/voices sizes via core.voice.local_assets; any
        # failure just leaves the neutral placeholder.
        if not hasattr(self, "pack_storage_label"):
            return
        try:
            from voice_assistant.core.gui_facade import format_bytes, storage_report

            report = storage_report() or {}
            total = int(report.get("total_bytes", 0) or 0)
            partial = int(report.get("partial_bytes", 0) or 0)
            if total <= 0 and partial <= 0:
                text = "Her on-PC brain files: not downloaded"
            else:
                text = (f"Her on-PC brain files: {format_bytes(total)}"
                        if total > 0 else "Her on-PC brain files: not downloaded yet")
                if partial > 0:
                    text += f" (+ {format_bytes(partial)} partial — resumes automatically)"
        except Exception:  # noqa: BLE001
            text = "Her on-PC brain files: —"
        self.pack_storage_label.setText(text)
        # v5.17: surface the persisted download error right here —
        # the first place a stuck download should explain itself.
        if hasattr(self, "pack_error_label"):
            try:
                from voice_assistant.core.gui_facade import last_download_error
                err = last_download_error()
            except Exception:  # noqa: BLE001
                err = None
            if err:
                self.pack_error_label.setText(f"Last download problem: {err}")
                self.pack_error_label.setVisible(True)
            else:
                self.pack_error_label.setVisible(False)
        # The Settings summary header quotes the same pack state.
        self._refresh_settings_summary()

    def _diagnose_downloads(self):
        """v5.17: run local_assets.diagnose_downloads() off the UI
        thread (its probes can take seconds) and print the plain
        lines under the pack controls."""
        if not hasattr(self, "pack_diag_label"):
            return
        self.pack_diag_label.setText("Checking the download services…")
        self.pack_diag_label.setVisible(True)
        self.btn_diagnose_downloads.setEnabled(False)

        def _work():
            try:
                from voice_assistant.core.gui_facade import diagnose_downloads
                lines = diagnose_downloads()
            except Exception as exc:  # noqa: BLE001
                lines = ["The check itself couldn't run — please try again in a moment."]  # v5.51 (X-1): no raw exception text on a user surface
            self.signals.diagnosis_ready.emit(lines)

        threading.Thread(target=_work, daemon=True).start()

    def _show_diagnosis(self, lines):
        if hasattr(self, "pack_diag_label"):
            self.pack_diag_label.setText("\n".join(lines))
            self.pack_diag_label.setVisible(True)
        if hasattr(self, "btn_diagnose_downloads"):
            self.btn_diagnose_downloads.setEnabled(True)

    # ------------------------------------------------------------ setup banner (v3.2)
    def _start_banner_timer(self):
        timer = getattr(self, "_banner_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self._refresh_setup_banner)
            self._banner_timer = timer
        if not timer.isActive():
            timer.start()

    def _stop_banner_timer(self):
        timer = getattr(self, "_banner_timer", None)
        if timer is not None:
            timer.stop()

    def _refresh_setup_banner(self):
        """Repaints the setup banner from pack_progress().

        Visible only while engine == local and the pack is
        incomplete; on completion it shows "VYUK is fully set up ✓"
        for ~4 s and then hides for good (until a tier change makes
        the pack incomplete again). Refreshed by the downloads signal
        path (the same emissions the Brain card consumes), by the
        engine switch, and by a 1 s timer that runs only while the
        banner is on screen.
        """
        banner = getattr(self, "setup_banner", None)
        if banner is None:
            return
        try:
            if self._brain_engine() != "local":
                banner.setVisible(False)
                self._stop_banner_timer()
                return
            from voice_assistant.core.gui_facade import local_assets

            progress = local_assets.pack_progress()
        except Exception:  # noqa: BLE001 — the banner must never break the window
            return
        if progress.get("complete"):
            if getattr(self, "_banner_was_incomplete", False):
                self._banner_was_incomplete = False
                self._banner_done_until = time.monotonic() + 4.0
                self.earcons.ready()
            if time.monotonic() < getattr(self, "_banner_done_until", 0.0):
                self.setup_banner_label.setText("VYUK is fully set up ✓")
                self.setup_banner_bar.setRange(0, 100)
                self.setup_banner_bar.setValue(100)
                self.btn_setup_banner.setVisible(False)
                banner.setVisible(True)
                self._start_banner_timer()
            else:
                banner.setVisible(False)
                self._stop_banner_timer()
            return
        self._banner_was_incomplete = True
        self._banner_done_until = 0.0
        self.btn_setup_banner.setVisible(True)
        stage_names = {
            "ears": "her ears (speech recognition)",
            "voice": "her voice",
            "brain": "her brain",
        }
        by_stage = {s.get("stage"): s for s in progress.get("stages", [])}
        current = progress.get("current_stage") or "brain"
        info = by_stage.get(current) or {}
        what = stage_names.get(current, "her brain")
        # UI rethink Build 1: on FAILURE the banner stays as an
        # error line with Retry — it never auto-dismisses while
        # failed, and the failure is named in words instead of
        # hiding behind the downloading text. The top-level state
        # covers a failed stage that is not the current one; the
        # current stage's error covers the stage being downloaded.
        # The button's handler already retries when nothing is
        # running; only its label changes here.
        failed = (progress.get("state") in ("failed", "error")
                  or bool(info.get("error")))
        if failed:
            self.setup_banner_label.setText(
                f"Couldn't download {what} — check your connection "
                "and try again")
            bar_pct = info.get("pct")
            if bar_pct is None:
                bar_pct = progress.get("overall_pct")
            self.setup_banner_bar.setRange(0, 100)
            self.setup_banner_bar.setValue(
                max(0, min(100, int(bar_pct))) if bar_pct is not None
                else 0)
            self.btn_setup_banner.setText("Retry")
            banner.setVisible(True)
            self._start_banner_timer()
            return
        paused = (progress.get("state") == "paused"
                  or info.get("state") == "paused")
        if paused:
            text = f"Setup paused — {what} still to download"
        else:
            text = f"Setting VYUK up — downloading {what}"
        total = info.get("total_bytes")
        if total:
            from voice_assistant.core.gui_facade import format_bytes

            done = int(info.get("done_bytes") or 0)
            text += f" · {format_bytes(done)} of {format_bytes(int(total))}"
            if info.get("pct") is not None:
                text += f" ({int(info['pct'])}%)"
        self.setup_banner_label.setText(text)
        bar_pct = info.get("pct")
        if bar_pct is None:
            bar_pct = progress.get("overall_pct")
        if bar_pct is None:
            self.setup_banner_bar.setRange(0, 0)  # indeterminate — honestly unknown
        else:
            self.setup_banner_bar.setRange(0, 100)
            self.setup_banner_bar.setValue(max(0, min(100, int(bar_pct))))
        try:
            running = local_assets.downloads.is_running()
        except Exception:  # noqa: BLE001
            running = False
        self.btn_setup_banner.setText("Pause" if running else "Resume")
        banner.setVisible(True)
        self._start_banner_timer()

    def _on_setup_banner_button(self):
        """The banner's Pause/Resume/Retry — wired to the real
        DownloadManager through the same handlers the Brain card
        uses. On failure the button reads Retry and the same
        not-running path re-starts the download."""
        try:
            from voice_assistant.core.gui_facade import downloads

            running = downloads.is_running()
        except Exception:  # noqa: BLE001
            running = False
        if running:
            self._pause_offline_download()
        else:
            self._setup_local_brain()
        self._refresh_setup_banner()

    def _local_pack_wanted(self) -> bool:
        """Has the user ever chosen a brain that includes the
        on-device pack? (v5.23) The first-run choice records it
        (onboarding: setup/local_pack_wanted); flipping the engine
        to Local in Settings records it too. Until any choice
        exists, NOTHING downloads — auto-downloading at first run
        before the user picked a brain was the owner's explicit
        "very wrong". Installs onboarded before the choice existed
        migrate once: they onboarded under the old regime with the
        local engine, so the pack counts as wanted. A settings
        hiccup reads as "not wanted" (fail-closed, the compliant
        side); the manual Download button always works."""
        try:
            s = self.settings
            if qsettings_contains("setup/local_pack_wanted", settings=s):
                return bool(qsettings_get("setup/local_pack_wanted", settings=s))
            if bool(qsettings_get("onboarded", settings=s)):
                wanted = self._brain_engine() == "local"
                qsettings_set("setup/local_pack_wanted", wanted, settings=s)
                return wanted
            return False
        except Exception:  # noqa: BLE001
            return False

    def _maybe_auto_provision(self):
        """Launch-time provisioning (v3.2): when the local brain is
        selected and the pack hasn't landed, start the download
        unprompted — through the Brain card's own start path — so a
        first-run user never meets a half-installed VYUK at their
        first Connect. A download the user paused (its run state is
        still "paused") is respected, never overridden; the banner
        offers Resume instead. v5.23: gated on the user having
        CHOSEN a local-including brain (see _local_pack_wanted) —
        before any choice, this is a no-op."""
        try:
            if not self._local_pack_wanted():
                return
            if self._brain_engine() != "local":
                return
            from voice_assistant.core.gui_facade import local_assets

            if (local_assets.brain_ready() and local_assets.voice_ready()
                    and local_assets.whisper_ready()):
                return
            if local_assets.downloads.is_running():
                return
            if local_assets.pack_progress().get("state") == "paused":
                return
            # v3.3: a failed download backs off for 10 minutes instead
            # of being silently retried at every launch / engine flip.
            if not local_assets.auto_start_allowed():
                return
        except Exception:  # noqa: BLE001 — provisioning is best-effort
            return
        self._setup_local_brain()

    def _remove_offline_pack(self):
        # Two-click guard: the first click arms the button, the second one
        # (within this settings visit) actually frees the space.
        if not getattr(self, "_pack_remove_armed", False):
            self._pack_remove_armed = True
            if hasattr(self, "btn_remove_pack"):
                self.btn_remove_pack.setText("Click again to remove them and free the space")
            QTimer.singleShot(8000, self._disarm_remove_pack)
            return
        self._pack_remove_armed = False
        if hasattr(self, "btn_remove_pack"):
            self.btn_remove_pack.setText("Remove her on-PC brain files")
        try:
            from voice_assistant.core.gui_facade import remove_offline_pack

            result = remove_offline_pack()
        except Exception as exc:  # noqa: BLE001
            result = f"ERROR: could not remove her on-PC brain files — {exc}"
        self._set_brain_status(str(result))
        self._refresh_pack_storage()
        # v5.23: an explicit removal is also a choice — don't let
        # auto-provisioning re-download what the user just freed.
        # (Picking Local again in the brain card re-arms it.)
        # Round-3 item 14: a write that never persists re-downloads
        # the freed pack at the next launch. Noted on failure,
        # never swallowed.
        qsettings_set_noted("setup/local_pack_wanted", False, settings=self.settings)

    def _disarm_remove_pack(self):
        if not getattr(self, "_pack_remove_armed", False):
            return
        self._pack_remove_armed = False
        if hasattr(self, "btn_remove_pack"):
            try:
                self.btn_remove_pack.setText("Remove her on-PC brain files")
            except RuntimeError:
                pass

    def _save_ollama_model(self):
        model = self.input_ollama_model.text().strip()
        if not model:
            return
        config.ollama_model = model
        self._persist_config_setting("OLLAMA_MODEL", model)
        # UI settings slice: read at Ollama session creation
        # (core/voice/local_client.py), so the save applies at the
        # next Ollama connect — and it had no feedback.
        self._note_settings_feedback(
            f"Ollama model {model} — used next time you connect.")

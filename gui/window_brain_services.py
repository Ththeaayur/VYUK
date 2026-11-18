"""MainWindow brain mixin — env/browser settings, audit, updates, Google/world (GUI split #4).

Part of the composed BrainMixin in gui/window_brain.py;
methods moved verbatim from there.
"""

import json
import threading
from pathlib import Path
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QPushButton,
)
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import VERSION


class BrainServicesMixin:

    @staticmethod
    def _persist_env_setting(key: str, value: str):
        """v5.49: persist one .env setting through the MODULE-level
        save_env_setting (the working pattern _save_cloud_provider
        uses) — the config object itself carries no such method."""
        try:
            from voice_assistant.core.gui_facade import save_env_setting

            save_env_setting(key, value)
        except Exception:  # noqa: BLE001
            pass

    def _on_browser_real_chrome_toggled(self, on: bool):
        """v5.49 (programme item 33 follow-up): the real-Chrome
        attach opt-in — config field + .env, read live by
        core/browser.py's getattr seam on every attach attempt.
        Batch 3 (2026-11-05, L7): turning it OFF also detaches
        any live session — a flag flip alone left an attached
        session working. The detach runs on a worker thread
        (core.desktop.browser_enable.disable_browser_control);
        turning it ON changes nothing else (no attach attempt —
        that is the Enable button's job)."""
        config.browser_allow_real_chrome_attach = bool(on)
        self._persist_env_setting(
            "BROWSER_ALLOW_REAL_CHROME_ATTACH",
            "true" if on else "false")
        # UI settings slice: read live on every attach attempt
        # (core/browser.py getattr seam) — applies to the next
        # attach, and it had no feedback.
        self._note_settings_feedback(
            "Real-Chrome attach on — applies to the next attach "
            "attempt." if on else
            "Real-Chrome attach off — applied.")
        if not on:
            def _detach():
                try:
                    from voice_assistant.core.gui_facade import browser_enable as _be
                    result = _be.disable_browser_control()
                except Exception as exc:  # noqa: BLE001
                    result = {"state": "failed",
                              "message": f"Couldn't detach ({exc})."}
                try:
                    self.signals.browser_enable_completed.emit(
                        json.dumps(result))
                except Exception:  # noqa: BLE001 — feedback lost, off stands
                    pass

            threading.Thread(target=_detach, daemon=True).start()
        self._refresh_browser_enable_status()

    def _on_browser_debug_port_changed(self):
        """v5.49 (programme item 33 follow-up): the debug port —
        a sane port (1024–65535) persists; anything else snaps
        back to the field's current value, never a garbled save."""
        raw = self.input_browser_debug_port.text().strip()
        try:
            port = int(raw)
        except (TypeError, ValueError):
            port = -1
        if not (1024 <= port <= 65535):
            self.input_browser_debug_port.setText(
                str(getattr(config, "browser_debug_port", 9222)
                    or 9222))
            return
        config.browser_debug_port = port
        self._persist_env_setting("BROWSER_DEBUG_PORT", str(port))
        # UI settings slice: the invalid branch snaps back
        # visibly; the valid save had no feedback.
        self._note_settings_feedback(
            f"Browser debug port {port} saved — applies to the "
            "next attach attempt.")

    # --------------------------------- browser enable (Batch 3)
    def _browser_enable_set_busy(self, busy: bool):
        """Disable the two browser buttons while an enable /
        launch / restart runs on a worker thread."""
        for attr in ("btn_browser_enable", "btn_browser_chrome_action"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(not busy)

    def _on_browser_enable_clicked(self):
        """Settings → Browser control → Enable browser control.
        The one explicit enable action: runs
        core.desktop.browser_enable.enable_browser_control() on a
        worker thread (the attach can take ~10 s); the result
        dict lands on browser_enable_completed as JSON. Never a
        voice action; the checkbox toggle alone never calls
        this."""
        label = getattr(self, "browser_enable_status_label", None)
        if label is not None:
            label.setText("Enabling browser control…")
        self._browser_enable_set_busy(True)

        def run():
            try:
                from voice_assistant.core.gui_facade import browser_enable as _be
                result = _be.enable_browser_control()
            except Exception as exc:  # noqa: BLE001
                result = {"state": "failed",
                          "message": f"Couldn't enable browser control ({exc})."}
            try:
                self.signals.browser_enable_completed.emit(
                    json.dumps(result))
            except Exception:  # noqa: BLE001 — result lost, buttons stay busy
                pass

        threading.Thread(target=run, daemon=True).start()

    def _on_browser_chrome_action_clicked(self):
        """The launch/restart button, shown only after the enable
        result asked for it. Pressing it IS the confirmation the
        core functions require (confirm=True): for a restart the
        status line has already warned that Chrome will close
        and reopen (tabs come back; unsaved form work may be
        lost)."""
        pending = getattr(self, "_browser_chrome_pending", None)
        if pending not in ("launch", "restart"):
            return
        label = getattr(self, "browser_enable_status_label", None)
        if label is not None:
            label.setText(
                "Launching Chrome with browser control…"
                if pending == "launch" else
                "Restarting Chrome with browser control…")
        self._browser_enable_set_busy(True)

        def run():
            try:
                from voice_assistant.core.gui_facade import browser_enable as _be
                if pending == "launch":
                    result = _be.launch_chrome_for_browser_control(
                        confirm=True)
                else:
                    result = _be.restart_chrome_for_browser_control(
                        confirm=True)
            except Exception as exc:  # noqa: BLE001
                result = {"state": "failed",
                          "message": f"Couldn't start Chrome ({exc})."}
            try:
                self.signals.browser_enable_completed.emit(
                    json.dumps(result))
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=run, daemon=True).start()

    def _on_browser_enable_completed(self, payload: str):
        """GUI-thread slot for an enable/launch/restart/disable
        result (JSON). Renders the state line and shows the
        launch/restart button only when the state asks for it."""
        try:
            result = json.loads(payload) if payload else {}
        except Exception:  # noqa: BLE001 — unparseable payload
            result = {}
        if not isinstance(result, dict):
            result = {}
        state = str(result.get("state", ""))
        message = str(result.get("message", "")).strip()
        label = getattr(self, "browser_enable_status_label", None)
        if label is not None and message:
            label.setText(message)
        btn = getattr(self, "btn_browser_chrome_action", None)
        if state == "needs_launch":
            self._browser_chrome_pending = "launch"
            if btn is not None:
                btn.setText("Launch Chrome with browser control")
                btn.setVisible(True)
        elif state in ("needs_restart", "chrome_state_unknown"):
            # chrome_state_unknown offers the launch button (its
            # message tells the user to close Chrome first when
            # it is open); needs_restart offers the restart one.
            self._browser_chrome_pending = (
                "launch" if state == "chrome_state_unknown" else "restart")
            if btn is not None:
                btn.setText(
                    "Launch Chrome with browser control"
                    if state == "chrome_state_unknown" else
                    "Restart Chrome with browser control")
                btn.setVisible(True)
        else:
            self._browser_chrome_pending = None
            if btn is not None:
                btn.setVisible(False)
        self._browser_enable_set_busy(False)
        # Keep the checkbox honest with the stored opt-in.
        try:
            chk = getattr(self, "chk_browser_real_chrome", None)
            if chk is not None:
                from voice_assistant.core.gui_facade import config as _cfg
                chk.setChecked(bool(
                    getattr(_cfg, "browser_allow_real_chrome_attach",
                            False)))
        except Exception:  # noqa: BLE001 — checkbox sync never breaks
            pass

    def _refresh_browser_enable_status(self):
        """Cheap status line for the browser card: opt-in + connected
        only (two boolean reads — no Playwright call, so it is safe
        to run every time a page opens, the _show_page pattern).
        No-op until the card is built. Never raises."""
        label = getattr(self, "browser_enable_status_label", None)
        if label is None:
            return
        try:
            from voice_assistant.core.gui_facade import browser_enable as _be
            on = bool(_be._opt_in())
            connected = bool(_be._is_connected())
        except Exception:  # noqa: BLE001 — status never breaks Settings
            label.setText("Browser status unavailable.")
            return
        if connected:
            label.setText("Browser control is on — attached to your Chrome.")
        elif on:
            label.setText(
                "Browser control is on, but not attached — press "
                "'Enable browser control' to attach.")
        else:
            label.setText("Browser control is off.")

    def _open_browser_connect_dialog(self):
        """Open the browser-connect dialog from Settings →
        Browser control (first-run onboarding, §3) — the
        wizard's Browser page as a standalone dialog. The shared
        opener (gui/browser_connect_dialog) owns the double-open
        guard and the strip-flag clear. The card's inline Enable
        flow stays as the second way in."""
        try:
            from .browser_connect_dialog import (
                open_browser_connect_dialog as _open)

            _open(self)
        except Exception:  # noqa: BLE001 — the card stands
            pass

    # --------------------------------- audit (v5.49, item 38)
    @staticmethod
    def _audit_line(rec: dict) -> str:
        """One audit record as one human-readable line (v5.49,
        programme item 38): time · tool · the interesting
        verdicts (gate / taint / foreground) · duration."""
        if not isinstance(rec, dict):
            return str(rec)
        ts = rec.get("ts")
        when = str(ts)[:19] if ts else "—"
        parts = [when, str(rec.get("tool") or rec.get("kind")
                           or "tool")]
        args = rec.get("args")
        if args:
            parts.append(str(args)[:60])
        if rec.get("gate"):
            parts.append(f"gate: {rec['gate']}")
        taint = rec.get("taint")
        if isinstance(taint, dict):
            # The real log's taint is a record in itself; the
            # line wants its state word, not the whole dict.
            taint = taint.get("state") or ""
        if taint:
            parts.append(f"taint: {taint}")
        if rec.get("fg"):
            parts.append(f"foreground: {rec['fg']}")
        if rec.get("epoch") is not None:
            parts.append(f"epoch {rec['epoch']}")
        if rec.get("ms") is not None:
            parts.append(f"{rec['ms']} ms")
        return " · ".join(parts)

    def _refresh_audit(self):
        """v5.49 (programme item 38): reload the Audit view from
        core.audit.read_recent(limit=50). A missing module or an
        empty log both read as the one honest line: no audit
        records yet."""
        lst = getattr(self, "audit_list", None)
        if lst is None:
            return
        lst.clear()
        records = []
        try:
            from voice_assistant.core.gui_facade import audit as _audit_mod

            records = list(_audit_mod.read_recent(limit=50) or [])
        except Exception:  # noqa: BLE001 — module may not exist yet
            records = []
        if not records:
            lst.addItem("No audit records yet.")
            return
        for rec in records:
            lst.addItem(self._audit_line(rec))

    def _export_audit(self):
        """v5.49 (programme item 38): export the audit log to a
        file the user picks — defaulting OUTSIDE data/ (the home
        folder), so an export never lands back inside the app's
        own state directory."""
        try:
            default_path = str(
                Path.home() / "vyuk-audit-export.jsonl")
        except Exception:  # noqa: BLE001
            default_path = "vyuk-audit-export.jsonl"
        path, _chosen = QFileDialog.getSaveFileName(
            self, "Export audit log", default_path,
            "Audit log (*.jsonl *.txt);;All files (*)")
        if not path:
            return
        label = getattr(self, "audit_status_label", None)
        try:
            from voice_assistant.core.gui_facade import audit as _audit_mod

            written = int(_audit_mod.export_to(path))
            note = (f"Exported {written} audit record(s) to {path}")
        except Exception as exc:  # noqa: BLE001 — no module yet
            note = ("No audit records to export yet — the audit "
                    f"log isn't available ({exc}).")
        if label is not None:
            label.setText(note)
            label.setVisible(True)

    # ------------------------------------------------------- Updates
    def _on_updates_clicked(self):
        """About card update button (v5.2 — a real updater now, not a
        placebo). Two-step by state: with no staged offer it runs the
        feed check on a worker thread; with an 'available' result
        stored, it downloads + stages that build (verified by hash in
        core.health.updater) — installation itself happens at next launch via
        main.py's apply_staged(), never mid-session."""
        if getattr(self, "_update_busy", False):
            return
        label = getattr(self, "update_label", None)
        info = getattr(self, "_update_info", None)
        self._update_busy = True
        self.btn_updates.setEnabled(False)
        if info:
            self.btn_updates.setText("Downloading…")
            if label is not None:
                label.setText(f"Downloading VYUK {info.get('version', '')}…")

            def run_stage():
                try:
                    from voice_assistant.core.gui_facade import updater  # Phase 3 H1: moved to core/health/
                    result = updater.download_and_stage(info)
                    state = str(result.get("state", "error"))
                    payload = str(result.get("message", ""))
                except Exception as exc:  # noqa: BLE001
                    state, payload = "error", str(exc)
                self.signals.update_stage_completed.emit(state, payload)

            threading.Thread(target=run_stage, daemon=True).start()
            return
        self.btn_updates.setText("Checking…")
        if label is not None:
            label.setText("Checking for updates…")

        def run_check():
            try:
                from voice_assistant.core.gui_facade import updater  # Phase 3 H1: moved to core/health/
                result = updater.check()
                state = str(result.get("state", "error"))
                import json as _json
                payload = _json.dumps(result)
            except Exception as exc:  # noqa: BLE001
                state, payload = "error", str(exc)
            self.signals.update_check_completed.emit(state, payload)

        threading.Thread(target=run_check, daemon=True).start()

    def _on_update_check_completed(self, state: str, payload: str):
        self._update_busy = False
        label = getattr(self, "update_label", None)
        btn = getattr(self, "btn_updates", None)
        if label is None or btn is None:
            return
        btn.setEnabled(True)
        btn.setText("Check for updates")
        if state == "available":
            try:
                import json as _json
                info = _json.loads(payload)
            except Exception:  # noqa: BLE001
                info = {}
            self._update_info = info
            version = str(info.get("version", "")).strip()
            notes = str(info.get("notes", "")).strip()
            label.setText(
                f"VYUK {version} is available."
                + (f" {notes}" if notes else ""))
            btn.setText(f"Download VYUK {version}")
        elif state == "up_to_date":
            from voice_assistant.core.gui_facade import VERSION
            label.setText(f"You're up to date — VYUK {VERSION} is the latest build.")
        elif state == "no_feed":
            label.setText("Updates aren't configured for this build yet.")
        elif state == "untrusted":
            label.setText(
                "Update check blocked — the feed's signature didn't "
                "verify, so nothing it offers can be trusted.")
        else:
            detail = ""
            try:
                import json as _json
                detail = str(_json.loads(payload).get("message", "")).strip()
            except Exception:  # noqa: BLE001
                detail = payload.strip()
            label.setText(
                "Couldn't check for updates"
                + (f" — {detail[:160]}" if detail else "."))

    def _on_update_stage_completed(self, state: str, payload: str):
        self._update_busy = False
        label = getattr(self, "update_label", None)
        btn = getattr(self, "btn_updates", None)
        if label is None or btn is None:
            return
        info = getattr(self, "_update_info", None) or {}
        version = str(info.get("version", "")).strip()
        if state == "staged":
            label.setText(
                f"VYUK {version} is downloaded — restart VYUK to install it.")
            btn.setText("Ready to install on restart")
            btn.setEnabled(False)
            self._update_info = None
        elif state == "integrity":
            label.setText(
                "The download didn't match its checksum, so nothing "
                "was installed. You can try again.")
            btn.setText("Check for updates")
            btn.setEnabled(True)
            self._update_info = None
        else:
            detail = (payload or "").strip()
            label.setText(
                "The update download didn't finish"
                + (f" — {detail[:160]}" if detail else "."))
            btn.setText("Check for updates")
            btn.setEnabled(True)
            self._update_info = None

    # ------------------------------------------------- Knows your world
    @staticmethod
    def _google_services():
        """core.google_services, imported lazily and guarded (the same
        pattern as presence/watchers): a build without the module
        simply shows an unavailable line — never a crash."""
        try:
            from voice_assistant.core.gui_facade import google_services
            return google_services
        except Exception:  # noqa: BLE001
            return None

    def _google_connected(self) -> bool:
        module = self._google_services()
        if module is None:
            return False
        try:
            return bool(module.is_connected())
        except Exception:  # noqa: BLE001
            return False

    def _build_world_card(self, c_layout):
        """v2.4 'Your world' card — sits directly under the Brain card.
        Google Calendar + Gmail through the user's own OAuth codes;
        the sign-in itself happens in their browser. Since v5.2 she
        can also send mail and add events — each one approved by the
        user in a popup first; deleting stays impossible."""
        world = self._settings_card(
            c_layout, "Your world",
            "Connect your Google account so VYUK knows your day — lectures, deadlines and mail. She can read your calendar and mail, and she can send an email or add an event only after you approve it in a popup — she can never delete anything. Sign-in happens in your browser; everything stays on this device.")
        self._world_available = self._google_services() is not None
        self.world_status_label = QLabel()
        self.world_status_label.setObjectName("cardHint")
        self.world_status_label.setWordWrap(True)
        world.addWidget(self.world_status_label)
        if not self._world_available:
            self.world_status_label.setText(
                "Google connection isn't available in this build.")
            return
        id_lbl = QLabel("Client ID")
        id_lbl.setObjectName("muted")
        world.addWidget(id_lbl)
        self.input_google_client_id = QLineEdit(
            str(getattr(config, "google_client_id", "") or ""))
        self.input_google_client_id.setPlaceholderText("Paste your Client ID…")
        world.addWidget(self.input_google_client_id)
        secret_lbl = QLabel("Client secret")
        secret_lbl.setObjectName("muted")
        world.addWidget(secret_lbl)
        self.input_google_client_secret = QLineEdit(
            str(getattr(config, "google_client_secret", "") or ""))
        self.input_google_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_google_client_secret.setPlaceholderText("Paste your Client secret…")
        world.addWidget(self.input_google_client_secret)
        guide = QLabel("One-time setup (about 5 minutes): 1) Open Google Cloud Console and start a project. 2) Turn on Gmail and Calendar. 3) Create a 'Desktop app' sign-in for VYUK 4) Copy its two codes into the boxes below.")
        guide.setObjectName("cardHint")
        guide.setWordWrap(True)
        world.addWidget(guide)
        self.btn_google_save = QPushButton("Save codes")
        self.btn_google_save.clicked.connect(self._save_google_codes)
        world.addWidget(self.btn_google_save)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_google_connect = QPushButton("Connect Google")
        self.btn_google_connect.setObjectName("primaryBtn")
        self.btn_google_connect.setMinimumHeight(34)  # v5.54 (DPI rule ⑤)
        self.btn_google_connect.clicked.connect(self._connect_google)
        btn_row.addWidget(self.btn_google_connect, stretch=1)
        self.btn_google_disconnect = QPushButton("Disconnect")
        self.btn_google_disconnect.clicked.connect(self._disconnect_google)
        btn_row.addWidget(self.btn_google_disconnect)
        world.addLayout(btn_row)
        self.world_feedback_label = QLabel("")
        self.world_feedback_label.setObjectName("cardHint")
        self.world_feedback_label.setWordWrap(True)
        world.addWidget(self.world_feedback_label)
        self._refresh_world_status()

    def _refresh_world_status(self):
        """World card status line, from core.google_services. Called
        when the card is built, after every action, and each time a
        page is shown (the _show_page refresh pattern)."""
        label = getattr(self, "world_status_label", None)
        if label is None or not getattr(self, "_world_available", False):
            return
        label.setText(
            "Connected — VYUK can read your calendar and mail."
            if self._google_connected() else "Not connected")

    def _save_google_codes(self):
        client_id = self.input_google_client_id.text().strip()
        secret = self.input_google_client_secret.text().strip()
        if not client_id and not secret:
            self.world_feedback_label.setText(
                "Nothing to save yet — paste the two codes from Google Cloud Console first.")
            return
        self._persist_config_setting("GOOGLE_CLIENT_ID", client_id)
        self._persist_config_setting("GOOGLE_CLIENT_SECRET", secret)
        self.world_feedback_label.setText(
            "Codes saved — press Connect Google when you're ready.")

    def _set_world_busy(self, busy: bool):
        for attr in ("btn_google_connect", "btn_google_disconnect"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(not busy)

    def _connect_google(self):
        module = self._google_services()
        if module is None:
            return
        self._set_world_busy(True)
        self.world_feedback_label.setText(
            "Opening your browser — finish the sign-in there, then come back here.")

        def run():
            try:
                result = module.connect()
            except Exception as exc:  # noqa: BLE001
                result = f"Couldn't connect: {exc}"
            self.signals.world_status.emit(str(result))

        threading.Thread(target=run, daemon=True).start()

    def _disconnect_google(self):
        module = self._google_services()
        if module is None:
            return
        self._set_world_busy(True)
        self.world_feedback_label.setText("Disconnecting…")

        def run():
            try:
                result = module.disconnect()
            except Exception as exc:  # noqa: BLE001
                result = f"Couldn't disconnect: {exc}"
            self.signals.world_status.emit(str(result))

        threading.Thread(target=run, daemon=True).start()

    def _on_world_status(self, message: str):
        """connect()/disconnect() finished (worker thread → signal):
        show what core reported and re-read the connection state."""
        self._set_world_busy(False)
        label = getattr(self, "world_feedback_label", None)
        if label is not None and str(message or "").strip():
            label.setText(str(message))
        self._refresh_world_status()

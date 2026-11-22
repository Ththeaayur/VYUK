"""MainWindow brain mixin — API keys, cloud switch, providers, key tests (GUI split #4).

Part of the composed BrainMixin in gui/window_brain.py;
methods moved verbatim from there.
"""

import threading
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)
from voice_assistant.core.gui_facade import config


class BrainCloudMixin:

    def _save_api_key(self):
        key = self.input_api_key.text().strip()
        if key:
            config.save_api_key(key)
            if self._brain_engine() == "local":
                # Brain audit round 2, defect 7 named this seam;
                # v5.29 closes it. Saving a cloud key while she is
                # set to think on this PC used to end with a notice
                # that was easy to miss — and Connect then ran the
                # on-PC brain, which reads exactly like "the key
                # doesn't work" (field, 2026-10-22: it cost the
                # owner three evenings). Now the save OFFERS the
                # switch, in the open, and only an explicit yes
                # changes anything.
                if self._offer_cloud_switch():
                    self._switch_engine_to("gemini")
                    self._notify(
                        "Key saved, and she's now set to think "
                        "with Gemini in the cloud. You're ready to "
                        "Connect.")
                else:
                    self._notify(
                        "Key saved. Right now she's set to think on this PC, "
                        "so Connect will use the on-PC brain — the cloud key "
                        "works once she's set to think in the Cloud "
                        "(Settings → “How VYUK thinks”).")
            else:
                self._notify("Key saved. You're ready to Connect.")
            self._sync_test_key_button()

    def _offer_cloud_switch(self) -> bool:
        """v5.29: the modal switch offer after a key save on the
        local brain. True only on an explicit yes; a broken popup
        answers no (never switch silently). Test suites stub this
        method — a modal would block them."""
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle("VYUK — use the cloud brain?")
            dlg.setModal(True)
            layout = QVBoxLayout(dlg)
            title = QLabel("Key saved. Switch her brain to Gemini in the cloud?")
            title.setStyleSheet("font-size: 14px; font-weight: 600;")
            layout.addWidget(title)
            detail = QLabel(
                "Right now she's set to think on this PC, so Connect "
                "won't use the key you just saved. Switch to the "
                "cloud brain to use it — you can change back any "
                "time in Settings, under “How VYUK thinks”.")
            detail.setWordWrap(True)
            layout.addWidget(detail)
            row = QHBoxLayout()
            row.addStretch(1)
            btn_keep = QPushButton("Keep on this PC")
            btn_switch = QPushButton("Switch to cloud")
            row.addWidget(btn_keep)
            row.addWidget(btn_switch)
            layout.addLayout(row)
            # UI interaction slice: the safe choice is the default
            # and holds initial focus — Enter keeps, never switches.
            btn_keep.setDefault(True)
            btn_keep.setFocus()
            btn_keep.clicked.connect(dlg.reject)
            btn_switch.clicked.connect(dlg.accept)
            return dlg.exec() == QDialog.DialogCode.Accepted
        except Exception:  # noqa: BLE001 — a broken popup must not switch silently
            return False

    def _switch_engine_to(self, engine: str):
        """v5.29: move the brain engine through the Settings combo
        (its change handler persists BRAIN_ENGINE — one source of
        truth); fall back to a direct persist if the combo is not
        built yet."""
        combo = getattr(self, "combo_brain_engine", None)
        if combo is not None:
            idx = combo.findData(engine)
            if idx >= 0:
                combo.setCurrentIndex(idx)
                return
        config.brain_engine = engine
        if not self._persist_config_setting("BRAIN_ENGINE", engine):
            # v5.54.1: same no-silent-failure rule as the combo
            # handler — the session value stands, the user is
            # told it will not survive a restart.
            try:
                self._notify(
                    "I couldn't save that brain choice — it "
                    "works for now, but it won't survive a "
                    "restart. Check that voice_assistant\\.env "
                    "isn't read-only or locked, then pick the "
                    "brain again.")
            except Exception:  # noqa: BLE001 — the log line stands
                pass

    # -------------------------------- cloud provider (v5.15, Groq)
    @staticmethod
    def _cloud_text_module():
        """core.voice.cloud_text_client, imported lazily and guarded (the
        _live_client_module pattern)."""
        try:
            from voice_assistant.core.gui_facade import cloud_text_client  # Phase 3 V1b: moved to core/voice/
            return cloud_text_client
        except Exception:  # noqa: BLE001
            return None

    def _cloud_provider(self) -> str:
        combo = getattr(self, "combo_cloud_provider", None)
        if combo is not None and combo.currentIndex() >= 0:
            value = combo.itemData(combo.currentIndex())
            if value in ("gemini", "groq"):
                return str(value)
        return str(getattr(config, "cloud_provider", "gemini")
                   or "gemini").strip().lower()

    def _on_cloud_provider_changed(self):
        provider = self._cloud_provider()
        config.cloud_provider = provider
        try:
            from voice_assistant.core.gui_facade import save_env_setting
            save_env_setting("CLOUD_PROVIDER", provider)
        except Exception:  # noqa: BLE001
            pass
        self._sync_cloud_provider()
        self._refresh_settings_summary()

    def _sync_cloud_provider(self):
        provider = self._cloud_provider()
        gemini_box = getattr(self, "think_gemini_box", None)
        if gemini_box is not None:
            gemini_box.setVisible(provider == "gemini")
        groq_box = getattr(self, "think_groq_box", None)
        if groq_box is not None:
            groq_box.setVisible(provider == "groq")

    def _save_groq_key(self):
        key = self.input_groq_key.text().strip()
        if key:
            config.groq_api_key = key
            try:
                from voice_assistant.core.gui_facade import save_env_setting
                save_env_setting("GROQ_API_KEY", key)
            except Exception:  # noqa: BLE001
                pass
            # Same two-halves honesty as the Gemini key (v5.22):
            # the Groq key only drives Connect when she's thinking
            # in the cloud AND Groq is the selected cloud provider.
            if self._brain_engine() == "local":
                self._notify(
                    "Groq key saved. Right now she's set to think on "
                    "this PC, so Connect will use the local brain — "
                    "the Groq key applies when she's set to Cloud "
                    "with Groq as the cloud service in “How VYUK "
                    "thinks”.")
            elif self._cloud_provider() != "groq":
                self._notify(
                    "Groq key saved. Gemini is the selected cloud "
                    "service right now — the Groq key applies when "
                    "Groq is picked in “How VYUK thinks”.")
            else:
                self._notify("Groq key saved. You're ready to Connect.")
            self._sync_test_groq_button()

    def _sync_test_groq_button(self):
        btn = getattr(self, "btn_test_groq_key", None)
        if btn is None:
            return
        if getattr(self, "_groq_key_testing", False):
            btn.setEnabled(False)
            btn.setText("Testing…")
            return
        btn.setText("Test key")
        field = ""
        if hasattr(self, "input_groq_key"):
            field = self.input_groq_key.text().strip()
        btn.setEnabled(bool(field
                            or str(getattr(config, "groq_api_key", "")
                                   or "").strip()))

    def _test_groq_key(self):
        if getattr(self, "_groq_key_testing", False):
            return
        key = ""
        if hasattr(self, "input_groq_key"):
            key = self.input_groq_key.text().strip()
        if not key:
            key = str(getattr(config, "groq_api_key", "") or "").strip()
        label = getattr(self, "groq_key_test_label", None)
        if not key:
            if label is not None:
                label.setText(
                    "Paste a Groq key first — there's nothing to test yet.")
            return
        module = self._cloud_text_module()
        helper = (getattr(module, "validate_groq_key", None)
                  if module is not None else None)
        if helper is None:
            if label is not None:
                label.setText(
                    "The Groq check isn't available in this build — "
                    "the key will be checked when you connect.")
            return
        self._groq_key_testing = True
        self._sync_test_groq_button()
        if label is not None:
            label.setText("Testing the key with Groq…")

        def run():
            try:
                _ok, kind, message = helper(key)
            except Exception as exc:  # noqa: BLE001
                kind, message = "unknown", str(exc)
            self.signals.groq_key_test_completed.emit(
                str(kind), str(message))

        threading.Thread(target=run, daemon=True).start()

    def _on_groq_key_test_completed(self, kind: str, message: str):
        self._groq_key_testing = False
        self._sync_test_groq_button()
        label = getattr(self, "groq_key_test_label", None)
        if label is None:
            return
        if kind == "ok":
            label.setText(f"✓ {message}")
        elif kind == "auth":
            label.setText(
                "Groq rejected that key — check it for typos, or "
                "make a fresh one at console.groq.com.")
        elif kind == "network":
            label.setText(
                "Couldn't reach Groq — check the internet connection "
                "and try again.")
        else:
            label.setText(f"Groq answered unexpectedly: {message}")

    # --------------------------------------- cloud key test (v3.2)
    @staticmethod
    def _live_client_module():
        """core.voice.live_client, imported lazily and guarded (the same
        pattern as _google_services): a build without the module
        simply can't test keys — never a crash."""
        try:
            from voice_assistant.core.gui_facade import live_client  # Phase 3 V1b: moved to core/voice/
            return live_client
        except Exception:  # noqa: BLE001
            return None

    def _sync_test_key_button(self):
        """Test key is enabled exactly when there's a key to test —
        the field's current text, or the saved one — and no test is
        already running."""
        btn = getattr(self, "btn_test_key", None)
        if btn is None:
            return
        if getattr(self, "_key_testing", False):
            btn.setEnabled(False)
            btn.setText("Testing…")
            return
        btn.setText("Test key")
        field = ""
        if hasattr(self, "input_api_key"):
            field = self.input_api_key.text().strip()
        btn.setEnabled(bool(field or str(config.api_key or "").strip()))

    def _test_api_key(self):
        if getattr(self, "_key_testing", False):
            return
        key = ""
        if hasattr(self, "input_api_key"):
            key = self.input_api_key.text().strip()
        if not key:
            key = str(config.api_key or "").strip()
        label = getattr(self, "key_test_label", None)
        if not key:
            if label is not None:
                label.setText("Paste a key first — there's nothing to test yet.")
            return
        module = self._live_client_module()
        helper = getattr(module, "validate_api_key", None) if module is not None else None
        if helper is None:
            if label is not None:
                label.setText(
                    "The Google library isn't installed here, so the key "
                    "can't be tested — it will be checked when you connect.")
            return
        self._key_testing = True
        self._sync_test_key_button()
        if label is not None:
            label.setText("Testing the key with Google…")
        model = str(config.model_name or "")

        def run():
            # Fix 5: the key test is now the cloud diagnostic —
            # ONE probe, TWO doors (REST + Live, cloud_diag).
            # A REST pass no longer presents as "key works" when
            # the Live door is refused for this key+model.
            try:
                from voice_assistant.core.gui_facade import cloud_diag as _cd
                report = _cd.diagnose_cloud(key, model)
            except Exception as exc:  # noqa: BLE001
                kind, message = "unknown", str(exc)
            else:
                rest, live = report["rest"], report["live"]
                if (rest["status"] == "pass"
                        and live["status"] == "pass"):
                    kind = "ok"
                    message = (f"Key works \u2713 — REST passed, Live "
                               f"passed for '{model}'. Tier: unknown "
                               "(Google does not expose it).")
                elif live["status"] == "fail":
                    kind = live["kind"]
                    message = (f"REST: {rest['detail']} "
                               f"Live: {live['detail']}")
                elif rest["status"] == "fail":
                    kind = rest["kind"]
                    message = rest["detail"]
                else:
                    kind = "unknown"
                    message = (report["summary"] + " "
                               + live["detail"])
            self.signals.key_test_completed.emit(str(kind), str(message))

        threading.Thread(target=run, daemon=True).start()

    def _on_key_test_completed(self, kind: str, message: str):
        """Test-key verdict (worker thread → signal): one honest
        sentence per outcome, shown under the key buttons — the
        world-card feedback-label pattern, no page-jumping notify."""
        self._key_testing = False
        self._sync_test_key_button()
        label = getattr(self, "key_test_label", None)
        if label is None:
            return
        if kind == "ok":
            # Fix 5: the message is the two-door verdict from the
            # cloud diagnostic ("REST passed, Live passed ..."),
            # not the old single-door line.
            label.setText((message or "").strip()
                          or f"Key works ✓ — model '{config.model_name}' is available.")
        elif kind == "model":
            if "bidi" in (message or "").lower():
                label.setText(
                    "Key works for normal requests, but Google refused "
                    "it for live voice (not supported for "
                    "bidiGenerateContent) — try another voice model, "
                    "or a key from a project with the Gemini API "
                    "enabled.")
            else:
                label.setText(
                    "Key works, but the voice model name is wrong — VYUK "
                    "will auto-try alternates when you connect.")
        elif kind == "quota":
            # Fix 5: a quota refusal is its own verdict — it used
            # to read as "couldn't verify the key" (or "auth").
            label.setText(
                "Google refused with a quota / rate limit — this key "
                "has hit its Live usage limit for now. Wait a while, "
                "or turn on billing for the key's project in Google "
                "AI Studio.")
        elif kind == "auth":
            label.setText(
                "That key was rejected by Google — check it in AI "
                "Studio and paste it again.")
        elif kind == "network":
            label.setText("Couldn't reach Google from this PC.")
        elif kind == "library":
            label.setText(
                "The Google library isn't installed here, so the key "
                "can't be tested — it will be checked when you connect.")
        else:
            detail = (message or "").strip()
            if detail:
                label.setText(f"Couldn't verify the key — {detail[:160]}")
            else:
                label.setText("Couldn't verify the key.")

    def _refresh_gemini_signin_row(self):
        """Syncs the Settings Gemini sign-in row with the stored
        sign-in: the status line, the primary button's job (sign
        in, or re-test when already signed in), and the Sign out
        button. Read-only — refreshing never signs anyone in or
        out."""
        try:
            from voice_assistant.core.gui_facade import gemini_oauth as _oauth

            signed_in = _oauth.is_signed_in()
            email = _oauth.signed_in_email() if signed_in else None
        except Exception:  # noqa: BLE001 — no sign-in readable = signed out
            signed_in, email = False, None
        status = getattr(self, "gemini_signin_status", None)
        if status is not None:
            if signed_in and email:
                status.setText(f"Signed in as {email}.")
            elif signed_in:
                status.setText("Signed in with Google.")
            else:
                status.setText("Not signed in yet.")
        btn = getattr(self, "btn_gemini_signin", None)
        if btn is not None:
            btn.setText("Test sign-in" if signed_in
                        else "Continue with Google")
        signout = getattr(self, "btn_gemini_signout", None)
        if signout is not None:
            signout.setVisible(signed_in)

    def _on_gemini_signin_clicked(self):
        """The Settings sign-in button: when signed out it runs
        the OAuth flow (browser + consent) on a worker thread,
        then validates; when signed in it just re-validates. The
        verdict lands on gemini_signin_completed."""
        if getattr(self, "_gemini_signin_running", False):
            return
        try:
            from voice_assistant.core.gui_facade import gemini_oauth as _oauth

            already = _oauth.is_signed_in()
        except Exception:  # noqa: BLE001 — unreadable = signed out
            already = False
        self._gemini_signin_running = True
        btn = getattr(self, "btn_gemini_signin", None)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText("Testing…" if already else "Waiting for Google…")
        status = getattr(self, "gemini_signin_status", None)
        if status is not None and not already:
            status.setText(
                "Your browser should have opened Google's sign-in — "
                "approve it there, then come back here.")

        def run():
            try:
                from voice_assistant.core.gui_facade import (
                    gemini_oauth as _oauth)

                if not already:
                    message = _oauth.connect()
                    if not _oauth.is_signed_in():
                        self.signals.gemini_signin_completed.emit(
                            "auth", message)
                        return
                ok, kind, detail = _oauth.validate_token()
                self.signals.gemini_signin_completed.emit(
                    "ok" if ok else kind,
                    "" if ok else detail)
            except Exception as exc:  # noqa: BLE001
                self.signals.gemini_signin_completed.emit(
                    "unknown", str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _on_gemini_signin_completed(self, kind: str, message: str):
        self._gemini_signin_running = False
        btn = getattr(self, "btn_gemini_signin", None)
        if btn is not None:
            btn.setEnabled(True)
        self._refresh_gemini_signin_row()
        if kind != "ok":
            status = getattr(self, "gemini_signin_status", None)
            if status is not None:
                status.setText(message)

    def _on_gemini_signout_clicked(self):
        """Signs out of Google (revokes + deletes the local token)
        and refreshes the row. Sync — disconnect() is local work
        plus one best-effort revoke with a 3 s timeout."""
        try:
            from voice_assistant.core.gui_facade import gemini_oauth as _oauth

            message = _oauth.disconnect()
        except Exception as exc:  # noqa: BLE001
            message = f"Couldn't sign out ({exc})."
        self._refresh_gemini_signin_row()
        status = getattr(self, "gemini_signin_status", None)
        if status is not None:
            status.setText(message)

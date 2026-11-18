"""Settings redesign (2026-11-16, §4a + R3): the Connect screen.

One screen, two entries: the first-run wizard's Connect step
and the always-available repair flow. The same screen is
reachable in one tap from Home and deep-linked from every
"can't reach the cloud" error — the pain recurs whenever the
connection breaks, so the repair flow is where it recurs.

Content (no jargon — no "model", "endpoint", "tier"):
  what Gemini is, where to get a key (a link that opens the
  right Google page), a paste box, a Test button that says
  "connected" or the exact error in plain words, Save. The
  key goes in by paste/type only — never spoken (Class C).

The key test reuses the cloud diagnostic (core/voice/
cloud_diag.diagnose_cloud — the same two-door probe the
Settings card's Test key button runs) on a worker thread;
the verdict wording lives in key_test_verdict(), mirroring
the Settings card's lines (window_brain_cloud.
_on_key_test_completed) so the two screens say the same
thing about the same verdict.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .design_system.dialogs import HudDialog

#: Where to get a key, per provider (opened in the browser).
KEY_URLS = {
    "gemini": "https://aistudio.google.com/apikey",
    "groq": "https://console.groq.com/keys",
}

#: Plain-words key-test verdicts, per kind — the same lines the
#: Settings card shows (window_brain_cloud._on_key_test_completed
#: reads this function, so the wording has one source).
_KEY_TEST_VERDICTS = {
    "ok": None,  # the message carries the two-door detail
    "model": None,  # message-dependent: bidi vs wrong name
    "quota": ("Google refused with a quota / rate limit — this key "
              "has hit its Live usage limit for now. Wait a while, "
              "or turn on billing for the key's project in Google "
              "AI Studio."),
    "auth": ("That key was rejected by Google — check it in AI "
             "Studio and paste it again."),
    "network": "Couldn't reach Google from this PC.",
    "library": ("The Google library isn't installed here, so the key "
                "can't be tested — it will be checked when you connect."),
}


def key_test_verdict(kind: str, message: str) -> str:
    """One honest sentence for a key-test verdict kind.

    Pure (no Qt, no network): kind + the diagnostic's message
    in, plain words out. The "ok" and "model" kinds carry
    their detail in the message; every other kind has a fixed
    plain-words line.
    """
    kind = (kind or "").strip().lower()
    message = (message or "").strip()
    if kind == "ok":
        return message or "Key works — connected."
    if kind == "model":
        if "bidi" in message.lower():
            return ("Key works for normal requests, but Google refused "
                    "it for live voice (not supported for "
                    "bidiGenerateContent) — try another voice model, "
                    "or a key from a project with the Gemini API "
                    "enabled.")
        return ("Key works, but the voice model name is wrong — VYUK "
                "will auto-try alternates when you connect.")
    fixed = _KEY_TEST_VERDICTS.get(kind)
    if fixed is not None:
        return fixed
    if message:
        return f"Couldn't verify the key — {message[:160]}"
    return "Couldn't verify the key."


def diagnose_key(key: str, provider: str = "gemini"):
    """Run the key check synchronously (the caller threads it).

    Returns (kind, message) — kind in ok/auth/network/quota/
    model/library/unknown. Never raises: a broken probe reads
    as "unknown" with the error as its message.
    """
    key = (key or "").strip()
    provider = (provider or "gemini").strip().lower()
    if not key:
        return "unknown", ""
    try:
        if provider == "groq":
            from voice_assistant.core.gui_facade import cloud_text_client as _ctc

            helper = getattr(_ctc, "validate_groq_key", None)
            if helper is None:
                return "library", ""
            return helper(key)
        from voice_assistant.core.gui_facade import cloud_diag as _cd
        from voice_assistant.core.gui_facade import config as _config

        report = _cd.diagnose_cloud(key, str(_config.model_name or ""))
        rest, live = report["rest"], report["live"]
        if rest["status"] == "pass" and live["status"] == "pass":
            return ("ok",
                    f"Key works \u2713 — REST passed, Live passed for "
                    f"'{_config.model_name}'. Tier: unknown (Google "
                    "does not expose it).")
        if live["status"] == "fail":
            return live["kind"], (f"REST: {rest['detail']} "
                                  f"Live: {live['detail']}")
        if rest["status"] == "fail":
            return rest["kind"], rest["detail"]
        return "unknown", report["summary"] + " " + live["detail"]
    except Exception as exc:  # noqa: BLE001 — a broken probe is unknown
        return "unknown", str(exc)


class ConnectDialog(HudDialog):
    """The Connect screen as a standalone dialog (repair flow).

    ``parent`` is the MainWindow when opened from the app: Save
    offers the cloud switch through the parent's own methods
    (the same offer the Settings key save makes), and the
    "Open Settings" button asks the parent to show the "How
    VYUK thinks" card. Without a MainWindow parent the dialog
    still saves and tests — it just skips the switch offer.
    """

    key_test_completed = Signal(str, str)
    open_settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, title="Connect to the cloud")
        self.setAccessibleName("Connect dialog")
        layout = self.content_layout()
        layout.setSpacing(8)

        head = QLabel(
            "Gemini is Google's cloud brain — her sharpest mind and "
            "most natural voice. A free key from Google AI Studio "
            "is all it takes, and it stays only on this device.")
        head.setObjectName("muted")
        head.setWordWrap(True)
        layout.addWidget(head)

        provider_row = QHBoxLayout()
        provider_row.setSpacing(6)
        provider_label = QLabel("Cloud service")
        provider_label.setObjectName("cardHint")
        provider_row.addWidget(provider_label)
        self.combo_provider = QComboBox()
        self.combo_provider.setAccessibleName("Cloud brain provider")
        self.combo_provider.addItem("Gemini Live — best voice",
                                    "gemini")
        self.combo_provider.addItem(
            "Groq — Llama 3.3 70B, fast text brain", "groq")
        self.combo_provider.currentIndexChanged.connect(
            self._sync_provider)
        provider_row.addWidget(self.combo_provider, stretch=1)
        layout.addLayout(provider_row)

        self.key_hint = QLabel("")
        self.key_hint.setObjectName("cardHint")
        self.key_hint.setWordWrap(True)
        layout.addWidget(self.key_hint)

        self.input_key = QLineEdit()
        self.input_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_key.setAccessibleName("Connect API key")
        self.input_key.setPlaceholderText("Paste your key…")
        self.input_key.textChanged.connect(self._sync_test_button)
        layout.addWidget(self.input_key)

        link_row = QHBoxLayout()
        link_row.setSpacing(6)
        self.btn_get_key = QPushButton("Get a free key")
        self.btn_get_key.setObjectName("chipBtn")
        self.btn_get_key.setAccessibleName("Get a free key")
        self.btn_get_key.clicked.connect(self._open_key_url)
        link_row.addWidget(self.btn_get_key)
        link_row.addStretch()
        layout.addLayout(link_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_save_key = QPushButton("Save key")
        self.btn_save_key.setObjectName("primaryBtn")
        self.btn_save_key.setAccessibleName("Save key")
        self.btn_save_key.setMinimumHeight(34)
        self.btn_save_key.clicked.connect(self._save_key)
        btn_row.addWidget(self.btn_save_key, stretch=1)
        self.btn_test_key = QPushButton("Test key")
        self.btn_test_key.setAccessibleName("Test key")
        self.btn_test_key.setMinimumHeight(34)
        self.btn_test_key.clicked.connect(self._test_key)
        btn_row.addWidget(self.btn_test_key)
        layout.addLayout(btn_row)

        self.key_status = QLabel("")
        self.key_status.setObjectName("cardHint")
        self.key_status.setWordWrap(True)
        self.key_status.setAccessibleName("Key test result")
        layout.addWidget(self.key_status)

        self.btn_open_settings = QPushButton(
            "Open Settings → How VYUK thinks")
        self.btn_open_settings.setObjectName("chipBtn")
        self.btn_open_settings.clicked.connect(
            self.open_settings_requested.emit)
        layout.addWidget(self.btn_open_settings)

        self.key_test_completed.connect(self._on_key_test_completed)
        self._key_testing = False
        self._sync_provider()
        self._sync_test_button()

    # ------------------------------------------------------------ internals

    def _provider(self) -> str:
        combo = getattr(self, "combo_provider", None)
        if combo is not None and combo.currentIndex() >= 0:
            value = combo.itemData(combo.currentIndex())
            if value in ("gemini", "groq"):
                return str(value)
        return "gemini"

    def _sync_provider(self):
        provider = self._provider()
        if provider == "groq":
            self.key_hint.setText(
                "Groq thinks on its own computers — your laptop just "
                "sends and receives words. Get a free key from "
                "console.groq.com. Stored only on this device, never "
                "shared.")
            self.input_key.setPlaceholderText("Paste your Groq key…")
            self.btn_get_key.setText("Get a free Groq key")
        else:
            self.key_hint.setText(
                "Only needed for the cloud brain. Get a free key from "
                "Google AI Studio. Stored only on this device, never "
                "shared.")
            self.input_key.setPlaceholderText("Paste your key…")
            self.btn_get_key.setText("Get a free key")
        self._sync_test_button()

    def _sync_test_button(self):
        btn = getattr(self, "btn_test_key", None)
        if btn is None:
            return
        if getattr(self, "_key_testing", False):
            btn.setEnabled(False)
            btn.setText("Testing…")
            return
        btn.setText("Test key")
        btn.setEnabled(bool(self.input_key.text().strip()))

    def _open_key_url(self):
        url = KEY_URLS.get(self._provider(),
                           KEY_URLS["gemini"])
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:  # noqa: BLE001 — a failed open is not fatal
            pass

    def _save_key(self):
        key = self.input_key.text().strip()
        if not key:
            self.key_status.setText(
                "Paste a key first — there's nothing to save yet.")
            return
        provider = self._provider()
        try:
            from voice_assistant.core.gui_facade import config as _config

            if provider == "groq":
                _config.groq_api_key = key
                from voice_assistant.core.gui_facade import (
                    save_env_setting as _save_env)

                _save_env("GROQ_API_KEY", key)
            else:
                _config.save_api_key(key)
        except Exception as exc:  # noqa: BLE001 — a failed save says so
            self.key_status.setText(
                f"Couldn't save that key — {exc}")
            return
        # v5.22 two-halves honesty (the same rule the Settings
        # key saves follow): the key only drives Connect when
        # she's thinking in the cloud AND (for Groq) Groq is the
        # selected cloud service. The save says both halves, or
        # "Saved" reads as "the key worked" when Connect will
        # never use it on this engine.
        engine = ""
        cloud_provider = "gemini"
        try:
            from voice_assistant.core.gui_facade import config as _config

            engine = str(getattr(_config, "brain_engine", "")
                         or "").strip().lower()
            cloud_provider = str(getattr(
                _config, "cloud_provider", "gemini")
                or "gemini").strip().lower()
        except Exception:  # noqa: BLE001 — the plain line stands
            pass
        if engine == "local":
            # The Settings key save offers the cloud switch when
            # she's set to think on this PC (v5.29) — the same
            # offer here, through the parent's own methods, only
            # on an explicit yes, and only when the parent has
            # them. Without a parent, the line says both halves.
            parent = self.parentWidget()
            offered = False
            try:
                if (provider == "gemini" and parent is not None
                        and hasattr(parent, "_brain_engine")
                        and parent._brain_engine() == "local"
                        and hasattr(parent, "_offer_cloud_switch")
                        and parent._offer_cloud_switch()
                        and hasattr(parent, "_switch_engine_to")):
                    parent._switch_engine_to("gemini")
                    offered = True
            except Exception:  # noqa: BLE001 — the save stands
                pass
            if offered:
                self.key_status.setText(
                    "Key saved, and she's now set to think with "
                    "Gemini in the cloud. You're ready to Connect.")
            elif provider == "groq":
                self.key_status.setText(
                    "Groq key saved. Right now she's set to think "
                    "on this PC, so Connect will use the on-PC "
                    "brain — the Groq key applies when she's set "
                    "to Cloud with Groq as the cloud service in "
                    "“How VYUK thinks”.")
            else:
                self.key_status.setText(
                    "Key saved. Right now she's set to think on "
                    "this PC, so Connect will use the on-PC brain "
                    "— the cloud key works once she's set to "
                    "think in the Cloud (Settings → “How VYUK "
                    "thinks”).")
        elif provider == "groq" and cloud_provider != "groq":
            self.key_status.setText(
                "Groq key saved. Gemini is the selected cloud "
                "service right now — the Groq key applies when "
                "Groq is picked in “How VYUK thinks”.")
        elif provider == "groq":
            self.key_status.setText(
                "Groq key saved. You're ready to Connect.")
        else:
            self.key_status.setText(
                "Key saved. You're ready to Connect.")
        self._sync_test_button()

    def _test_key(self):
        if getattr(self, "_key_testing", False):
            return
        key = self.input_key.text().strip()
        if not key:
            self.key_status.setText(
                "Paste a key first — there's nothing to test yet.")
            return
        provider = self._provider()
        self._key_testing = True
        self._sync_test_button()
        self.key_status.setText("Testing the key with Google…"
                                if provider == "gemini"
                                else "Testing the key with Groq…")

        def run():
            kind, message = diagnose_key(key, provider)
            self.key_test_completed.emit(str(kind), str(message))

        threading.Thread(target=run, daemon=True).start()

    def _on_key_test_completed(self, kind: str, message: str):
        self._key_testing = False
        self._sync_test_button()
        self.key_status.setText(key_test_verdict(kind, message))

"""Onboarding pages mixin (split from gui/onboarding.py, GUI split #13 batch 1, move only).

Page builders, card/row helpers, and page-content updaters for the
first-run dialog. Composed into OnboardingDialog by gui/onboarding.py.
"""

import threading

from PySide6.QtCore import QDir, QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import first_run_asks
from voice_assistant.core.gui_facade import permission_grants
from voice_assistant.core.gui_facade import (
    qsettings_contains,
    qsettings_get,
)
from voice_assistant.core.gui_facade import LANGUAGES, set_preferred_language
from .style import (
    ACCENT,
    AMBER,
    BORDER,
    DANGER,
    GLASS,
    GLASS_HOVER,
    OK,
    PREMIUM_STYLE,
    PRIMARY_WASH,
    RADIUS_BTN,
    RADIUS_CARD,
    TEXT,
    TEXT_MUTED,
    TYPE_BODY,
    TYPE_CAPTION,
    TYPE_MICRO,
    load_bundled_fonts,
)
from .visualizer import OrbVisualizer
from .selftest_checks import run_first_run_self_tests
from .onboarding_widgets import _ChoiceCard, _MicSegments


def onboarding_mic_state(permissions_model_fn=None,
                         platform_probe=None, device_reader=None):
    """The live microphone state for onboarding's permissions page.

    B6 — one source, one row. Reads the SAME state the
    Permissions page reads: gui.pf_permissions.permissions_model()
    with no overrides (the durable grants store + the B3
    platform probe + the device reader), and returns the
    microphone entry's own wording — state_line() (VYUK-side),
    os_state_line() (Windows-side), diagnosis (the three-way
    distinction: denied by you / denied by Windows / no usable
    device). Returns None when the model cannot be built at all:
    the page then falls back to the static explainer instead of
    breaking onboarding or inventing a state. Read-only: it
    never writes the grants store, never pops the first-use ask
    (that fires on the wizard's Microphone page — D8 — and at a
    human Connect), never flips anything (D8: Settings-only
    flips).
    """
    try:
        if permissions_model_fn is None:
            from .pf_permissions import permissions_model as _model
            permissions_model_fn = _model
        entries = permissions_model_fn(
            platform_probe=platform_probe,
            device_reader=device_reader)
    except Exception:  # noqa: BLE001 — no model, no live row
        return None
    try:
        entry = next(e for e in entries
                     if e.permission_id == "microphone")
    except Exception:  # noqa: BLE001 — no mic entry, no live row
        return None
    return {
        "state": entry.state,
        "os_state": entry.os_state,
        "vyuk_line": entry.state_line(),
        "os_line": entry.os_state_line(),
        "diagnosis": entry.diagnosis or "",
    }


def _pick_folder(parent) -> str:
    """The folder picker for the wizard's Files ask — a module
    function so tests can patch it without opening a dialog.
    Returns "" when the picker is cancelled or fails; a cancel
    is a skip, never a grant."""
    try:
        return QFileDialog.getExistingDirectory(
            parent, "Choose a folder for VYUK", QDir.homePath())
    except Exception:  # noqa: BLE001 — a failed picker is a skip
        return ""


class PagesMixin:
    """Page builders for OnboardingDialog (GUI split #13 batch 1)."""

    def _page_welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        orb = OrbVisualizer()
        orb.setMinimumSize(170, 170)
        # E-F301: the orb's shared sizeHint is 320x320 (the Home
        # hero's hint, gui/visualizer_paint.py) — uncapped it
        # pushes the "Meet VYUK" title below the scroll viewport's
        # fold at the default 780x560 dialog (sliced mid-glyph),
        # and entirely off-screen at 760 wide, where the nav hint
        # wraps taller and the viewport shrinks to 319. Cap this
        # instance at 240: 240 + spacing + title block (68) = 314
        # fits the 336 viewport at 780 and the 319 viewport at
        # 760, title and body both above the fold. A maximum, not
        # a fixed size, so the orb can still shrink on a smaller
        # resized dialog. The Home hero orb is a separate instance,
        # untouched.
        orb.setMaximumSize(240, 240)
        orb.set_state("IDLE")
        layout.addWidget(orb, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_block(
            f"Meet {config.assistant_name}",
            "A calm voice assistant that listens, remembers, and gets things "
            "done — right from your desktop. Setup takes under a minute."))
        layout.addStretch()
        return page

    @staticmethod
    def _choice_row(glyph: str, color: str, text: str,
                    dim: bool = False) -> QLabel:
        lbl = QLabel(
            f'<span style="color:{color};">{glyph}</span>&nbsp; {text}')
        lbl.setWordWrap(True)
        style = (f"font-size: {TYPE_CAPTION}px; background: transparent;"
                 " border: none;")
        if dim:
            style += f" color: {TEXT_MUTED};"
        lbl.setStyleSheet(style)
        return lbl

    def _choice_card(self, choice: str, title: str, tagline: str,
                     pros: list, cons: list, footnote: str = None,
                     recommended: bool = False) -> _ChoiceCard:
        card = _ChoiceCard()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        header = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(f"font-size: {TYPE_BODY}px; font-weight: 600;"
                        " background: transparent; border: none;")
        header.addWidget(t)
        header.addStretch()
        marker = QLabel("✓ Selected")
        marker.setStyleSheet(f"color: {OK}; font-size: {TYPE_CAPTION}px;"
                             " font-weight: 600; background: transparent;"
                             " border: none;")
        marker.setVisible(False)
        header.addWidget(marker)
        lay.addLayout(header)
        if recommended:
            pill_row = QHBoxLayout()
            pill = QLabel("RECOMMENDED")
            pill.setStyleSheet(
                f"background: {PRIMARY_WASH}; color: {TEXT};"
                f" border-radius: {RADIUS_BTN}px; padding: 2px 8px;"
                f" font-size: {TYPE_MICRO}px; font-weight: 700;"
                " letter-spacing: 1px;")
            pill_row.addWidget(pill)
            pill_row.addStretch()
            lay.addLayout(pill_row)
        tag = QLabel(tagline)
        tag.setObjectName("muted")
        tag.setWordWrap(True)
        tag.setStyleSheet(f"font-size: {TYPE_CAPTION}px;"
                          " background: transparent; border: none;")
        lay.addWidget(tag)
        for text in pros:
            lay.addWidget(self._choice_row("✓", OK, text))
        for text in cons:
            lay.addWidget(self._choice_row("—", AMBER, text, dim=True))
        if footnote:
            foot = QLabel(footnote)
            foot.setObjectName("muted")
            foot.setWordWrap(True)
            foot.setStyleSheet(f"font-size: {TYPE_CAPTION}px;"
                               " background: transparent; border: none;")
            lay.addWidget(foot)
        lay.addStretch()
        card.clicked.connect(lambda c=choice: self._select_choice(c))
        self.choice_cards[choice] = card
        self._card_markers[choice] = marker
        return card

    def _page_choice(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._title_block(
            "How should she think?",
            "One decision, three honest options. Her hands, eyes, "
            "memory and voice are the same either way — this only "
            "chooses where her brain lives."))
        cards = QHBoxLayout()
        cards.setSpacing(12)
        cards.addWidget(self._choice_card(
            "local", "On this PC", "Her brain lives here, on this machine.",
            ["Works fully offline, forever",
             "Nothing you say ever leaves this PC",
             "No key, no account, no cost",
             "Every tool, the wake word and her voice run on-device",
             "Sized to this PC automatically (RAM-aware)"],
            [f"One-time download of {self._pack_size_text()}",
             "A compact on-device brain — capable, but the cloud's "
             "best is larger",
             "The first load takes a few minutes"]), stretch=1)
        cards.addWidget(self._choice_card(
            "cloud", "Cloud key", "Her brain lives at Google.",
            ["Google's Gemini Live — her sharpest mind",
             "The most natural, human-grade voice",
             "Starts in seconds — nothing to download",
             "Every tool still runs on your PC",
             "Free key from Google AI Studio — we'll walk you "
             "through it"],
            ["Needs an internet connection",
             "Your voice is processed by Google (their terms apply)",
             "Needs the free key pasted in on the next step"],
            footnote="Prefer Llama 3.3 70B? Groq is available in "
                     "Settings."), stretch=1)
        cards.addWidget(self._choice_card(
            "both", "Both", "Cloud first, on-PC as the safety net.",
            ["Everything in both options, in one choice",
             "Cloud thinks first — the on-PC brain takes over "
             "automatically if the cloud can't be reached",
             "Works offline too, once the one-time download lands",
             "The download starts only after you choose, in the "
             "background"],
            ["Uses the one-time download and the key",
             "Needs internet for the sharpest brain"],
            recommended=True), stretch=1)
        layout.addLayout(cards, stretch=1)
        # E-F302 option D: the switch note moved to the dialog
        # chrome (see _switch_note in onboarding.py) — it sat
        # below the fold at the bottom of this scrolling page.
        return page

    def _profile_card(self, profile: str, title: str, tagline: str,
                      pros: list, cons: list,
                      recommended: bool = False) -> _ChoiceCard:
        """A brain-choice-shaped card for a tool profile — same
        _ChoiceCard widget, its own registry (profile_cards), so
        the two choices never entangle."""
        card = _ChoiceCard()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        header = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(f"font-size: {TYPE_BODY}px; font-weight: 600;"
                        " background: transparent; border: none;")
        header.addWidget(t)
        header.addStretch()
        marker = QLabel("✓ Selected")
        marker.setStyleSheet(f"color: {OK}; font-size: {TYPE_CAPTION}px;"
                             " font-weight: 600; background: transparent;"
                             " border: none;")
        marker.setVisible(False)
        header.addWidget(marker)
        lay.addLayout(header)
        if recommended:
            pill_row = QHBoxLayout()
            pill = QLabel("RECOMMENDED")
            pill.setStyleSheet(
                f"background: {PRIMARY_WASH}; color: {TEXT};"
                f" border-radius: {RADIUS_BTN}px; padding: 2px 8px;"
                f" font-size: {TYPE_MICRO}px; font-weight: 700;"
                " letter-spacing: 1px;")
            pill_row.addWidget(pill)
            pill_row.addStretch()
            lay.addLayout(pill_row)
        tag = QLabel(tagline)
        tag.setObjectName("muted")
        tag.setWordWrap(True)
        tag.setStyleSheet(f"font-size: {TYPE_CAPTION}px;"
                          " background: transparent; border: none;")
        lay.addWidget(tag)
        for text in pros:
            lay.addWidget(self._choice_row("✓", OK, text))
        for text in cons:
            lay.addWidget(self._choice_row("—", AMBER, text, dim=True))
        lay.addStretch()
        card.clicked.connect(lambda p=profile: self._select_profile(p))
        self.profile_cards[profile] = card
        self._profile_markers[profile] = marker
        return card

    def _page_profile(self) -> QWidget:
        # v5.49 (programme item 34 completion): which tools she
        # keeps at hand. Phase G: advanced setup — the page never
        # gates Continue (the pending choice starts at the
        # configured profile, so doing nothing is a real choice:
        # the desktop default).
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._title_block(
            "What should she keep at hand?",
            "The tools loaded and ready the moment she connects. "
            "Anything not loaded stays one ask away through her "
            "tool router — this only chooses what she leads with."))
        cards = QHBoxLayout()
        cards.setSpacing(12)
        cards.addWidget(self._profile_card(
            "desktop", "Desktop",
            "Best for controlling the PC.",
            ["Mouse, keyboard, windows and clicking — ready instantly",
             "Screenshot, screen-reading and browser tools at hand",
             "File and clipboard tools loaded in full detail"],
            ["Everyday chat and knowledge tools ride the router "
             "instead — one extra beat when she fetches them"],
            recommended=True), stretch=1)
        cards.addWidget(self._profile_card(
            "assistant", "Assistant",
            "The everyday all-rounder.",
            ["The classic everyday tool set, ready instantly",
             "A lighter, chattier starting kit"],
            ["PC-control tools ride the router — a beat slower "
             "when she reaches for the mouse or a window"]),
            stretch=1)
        cards.addWidget(self._profile_card(
            "full", "Full",
            "Absolutely everything, loaded at once.",
            ["Every single tool loaded — nothing to fetch",
             "No router step, ever"],
            ["Uses the most context — her thinking room shrinks",
             "Heaviest start of the three"]),
            stretch=1)
        layout.addLayout(cards, stretch=1)
        # E-F302 option D: the switch note moved to the dialog
        # chrome (see _switch_note in onboarding.py) — it sat
        # below the fold at the bottom of this scrolling page.
        self._select_profile(self.profile_choice)
        return page

    def _clear_key_no_key_warning(self, _text: str = "") -> None:
        """Any edit to the key field re-arms the empty-key
        warning (round-2 N4 FAIL 2): a warned-then-edited field
        must warn again on Continue, never ride the old warning
        through to an advance."""
        self._key_no_key_warned = False

    def _save_key(self):
        key = self.ob_api_key.text().strip()
        if not key:
            # An empty key is fine, not an error — but say what it
            # means for the brain the user just chose.
            if self.brain_choice in ("cloud", "both"):
                self.ob_key_status.setText(
                    "No key added — she needs one for the cloud brain. "
                    "You can paste it any time in Settings.")
            else:
                self.ob_key_status.setText(
                    "No key added — VYUK will think on this PC. You can add one later in Settings.")
            self.ob_key_status.setStyleSheet(f"color: {OK};")
            return
        if len(key) < 10:
            self.ob_key_status.setText("That doesn't look like a full key yet — paste the whole thing.")
            self.ob_key_status.setStyleSheet(f"color: {DANGER};")
            return
        config.save_api_key(key)
        if self.brain_choice == "cloud":
            self.ob_key_status.setText(
                "Saved — this key becomes her cloud brain when you "
                "finish setup.")
        elif self.brain_choice == "both":
            self.ob_key_status.setText(
                "Saved — she'll think in the cloud first, with the "
                "on-PC brain as backup.")
        else:
            engine = str(getattr(config, "brain_engine", "local")
                         or "local").strip().lower()
            if engine == "local":
                # Brain audit round 2, defect 7: this page saves the key
                # while the engine stays on the on-PC brain by design —
                # say both halves, or "Saved" reads as "the key worked"
                # when Connect will never use it on this engine.
                self.ob_key_status.setText(
                    "Saved. VYUK is set to think on this PC, so Connect "
                    "will use the built-in brain — this cloud key applies "
                    "when she's set to Cloud in Settings, under "
                    "“How VYUK thinks”.")
            else:
                self.ob_key_status.setText("Saved — you're ready to connect.")
        self.ob_key_status.setStyleSheet(f"color: {OK};")

    def _test_ob_key(self):
        """Test the pasted key on this screen (Settings redesign
        §4a): the same two-door cloud diagnostic the Settings
        card runs, on a worker thread, with the plain-words
        verdict from gui/connect_dialog.key_test_verdict."""
        if getattr(self, "_ob_key_testing", False):
            return
        key = self.ob_api_key.text().strip()
        if not key:
            self.ob_key_test_label.setText(
                "Paste a key first — there's nothing to test yet.")
            return
        self._ob_key_testing = True
        self.ob_btn_test_key.setEnabled(False)
        self.ob_btn_test_key.setText("Testing…")
        self.ob_key_test_label.setText("Testing the key with Google…")

        def run():
            try:
                from .connect_dialog import diagnose_key as _diag

                kind, message = _diag(key, "gemini")
            except Exception as exc:  # noqa: BLE001
                kind, message = "unknown", str(exc)
            self.key_test_completed.emit(str(kind), str(message))

        threading.Thread(target=run, daemon=True).start()

    def _on_ob_key_test_completed(self, kind: str, message: str):
        self._ob_key_testing = False
        btn = getattr(self, "ob_btn_test_key", None)
        if btn is not None:
            btn.setEnabled(True)
            btn.setText("Test key")
        label = getattr(self, "ob_key_test_label", None)
        if label is not None:
            try:
                from .connect_dialog import key_test_verdict as _verdict

                label.setText(_verdict(kind, message))
            except Exception:  # noqa: BLE001 — never blank on a verdict
                label.setText("Couldn't verify the key.")

    def _page_api_key(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # v5.23: this page is reached only when the pending choice
        # is cloud or both (see _page_applies), so the key is no
        # longer an abstract "optional upgrade" — it's the brain
        # the user just picked. Round-2 N4 FAIL 2: Continue no
        # longer sails through with an empty field — it saves a
        # pasted key, warns once on an empty field with no stored
        # key, and advances only on the second, deliberate press
        # (see FlowMixin._advance_from_key_page). Skip setup
        # remains in the nav.
        layout.addWidget(self._title_block(
            "Your Gemini key",
            "It's free and takes about two minutes: open Google AI "
            "Studio, choose “Create API key”, and paste it here."))
        self.ob_api_key = QLineEdit(config.api_key)
        self.ob_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ob_api_key.setAccessibleName("Gemini API key")
        self.ob_api_key.setPlaceholderText("Paste your Google key…")
        layout.addWidget(self.ob_api_key)
        # Round-2 N4 FAIL 2: any edit re-arms the empty-key
        # warning — a warned-then-edited field must warn again,
        # never ride the old warning through.
        self.ob_api_key.textChanged.connect(
            self._clear_key_no_key_warning)
        key_hint = QLabel("From Google AI Studio — stored only on this device, never anywhere else.")
        key_hint.setObjectName("muted")
        key_hint.setWordWrap(True)
        layout.addWidget(key_hint)
        # Settings redesign §4a: the link opens the exact Google
        # page — no hunting for where a key comes from.
        key_link = QLabel(
            '<a href="https://aistudio.google.com/apikey">'
            "Get a free key from Google AI Studio</a>")
        key_link.setOpenExternalLinks(True)
        key_link.setWordWrap(True)
        key_link.setAccessibleName("Get a free key from Google AI Studio")
        layout.addWidget(key_link)
        # v5.54 (Wave 0): free-tier steering, in plain words and
        # with no dark pattern — a free key is never blocked or
        # nagged about again after this page; the note exists
        # because Google's terms for unpaid use (product
        # improvement, possible human review) are a fact a
        # person should hear BEFORE they paste, not after.
        tier_hint = QLabel(
            "One honest note about free keys: Google may use "
            "conversations on a free key to improve its products, "
            "and its reviewers may read some of them. A paid-tier "
            "key (billing turned on for the key's project in AI "
            "Studio) is never used that way. Either key works "
            "here — the paid one is the private choice.")
        tier_hint.setObjectName("muted")
        tier_hint.setWordWrap(True)
        layout.addWidget(tier_hint)
        self.ob_key_status = QLabel("")
        self.ob_key_status.setObjectName("muted")
        # E-F303: the warning this label carries is a long single
        # line (~200 chars); without wrapping its sizeHint blows
        # out the shared QStackedWidget's minimum width and every
        # later wizard page renders cut off at the right edge. The
        # neighbouring labels on this page all wrap — this one does
        # too.
        self.ob_key_status.setWordWrap(True)
        layout.addWidget(self.ob_key_status)
        self.ob_btn_save_key = QPushButton("Save key")
        self.ob_btn_save_key.setObjectName("primaryBtn")
        self.ob_btn_save_key.setMinimumHeight(34)  # v5.54 (DPI rule)
        self.ob_btn_save_key.clicked.connect(self._save_key)
        layout.addWidget(self.ob_btn_save_key)
        # Settings redesign §4a: the key is tested here, on this
        # screen, before Save is trusted — a bad key must say so
        # now, not on the first Connect.
        self.ob_btn_test_key = QPushButton("Test key")
        self.ob_btn_test_key.setAccessibleName("Onboarding test key")
        self.ob_btn_test_key.setMinimumHeight(34)
        self.ob_btn_test_key.clicked.connect(self._test_ob_key)
        layout.addWidget(self.ob_btn_test_key)
        self.ob_key_test_label = QLabel("")
        self.ob_key_test_label.setObjectName("muted")
        self.ob_key_test_label.setWordWrap(True)
        self.ob_key_test_label.setAccessibleName("Onboarding key test result")
        layout.addWidget(self.ob_key_test_label)
        layout.addStretch()
        return page

    def _page_mic(self) -> QWidget:
        # First-run onboarding (2026-11-16, D8): the mic ask moved
        # into the wizard — ask first, then the hear-back test.
        # The ask shows only when mic_first_use_ask_needed() holds
        # (denied, never decided, no session grant, not declined
        # this session); the test controls enable only when the mic
        # is usable (stored grant or session grant). Nothing here
        # writes on page entry — a page merely shown grants
        # nothing; the grant happens on the user's tap, through
        # the shared choice the Connect ask uses
        # (pf_permissions.apply_mic_first_use_choice).
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._title_block(
            "Microphone",
            "First, can VYUK hear you? Then check the mic works."))
        # --- the ask (first-run onboarding, D8) ---
        self._mic_ask_block = QWidget()
        ask_layout = QVBoxLayout(self._mic_ask_block)
        ask_layout.setContentsMargins(0, 0, 0, 0)
        ask_layout.setSpacing(8)
        ask_copy = first_run_asks.ASK_COPY["microphone"]
        ask_title = QLabel(ask_copy["title"])
        ask_title.setObjectName("pageTitle")
        ask_title.setWordWrap(True)
        ask_layout.addWidget(ask_title)
        ask_body = QLabel(ask_copy["body"])
        ask_body.setObjectName("muted")
        ask_body.setWordWrap(True)
        ask_layout.addWidget(ask_body)
        ask_explainer = QLabel(ask_copy["explainer"])
        ask_explainer.setObjectName("muted")
        ask_explainer.setWordWrap(True)
        ask_layout.addWidget(ask_explainer)
        ask_row = QHBoxLayout()
        ask_row.setSpacing(8)
        self._mic_ask_buttons: dict[str, QPushButton] = {}
        for label, choice in (("Always allow", "always"),
                              ("Allow once", "allow_once"),
                              ("Open Settings", "settings"),
                              ("Not now", "not_now")):
            btn = QPushButton(label)
            btn.setAccessibleName(label)
            btn.setObjectName(
                "primaryBtn" if choice == "always" else "chipBtn")
            btn.clicked.connect(
                lambda _checked=False, c=choice: self._on_mic_ask(c))
            ask_row.addWidget(btn)
            self._mic_ask_buttons[choice] = btn
        ask_row.addStretch()
        ask_layout.addLayout(ask_row)
        layout.addWidget(self._mic_ask_block)
        # The save-did-not-stick note lives OUTSIDE the ask block:
        # the block hides once the ask is answered, which used to
        # hide the note with it (field-check fix #9 — a note
        # nobody can read is not a note).
        self._mic_note = QLabel("")
        self._mic_note.setObjectName("muted")
        self._mic_note.setWordWrap(True)
        self._mic_note.setAccessibleName("Microphone ask note")
        self._mic_note.setVisible(False)
        layout.addWidget(self._mic_note)
        # --- the test (unchanged, Settings redesign §4a) ---
        self.ob_mic_level = _MicSegments()
        self.ob_mic_level.setAccessibleName("Microphone level")
        layout.addWidget(self.ob_mic_level)
        self._mic_status = QLabel("Press AUDITION, then speak.")
        self._mic_status.setObjectName("muted")
        self._mic_status.setWordWrap(True)
        layout.addWidget(self._mic_status)
        self._audition_btn = QPushButton("AUDITION")
        self._audition_btn.setObjectName("chipBtn")
        self._audition_btn.setCheckable(True)
        self._audition_btn.clicked.connect(self._toggle_audition)
        layout.addWidget(self._audition_btn)
        # Settings redesign §4a: hear yourself back — the mic
        # test is not a meter only. Plays the last few seconds
        # the mic heard, resampled to the playback rate, so the
        # user hears what she hears.
        self._hearback_btn = QPushButton("Hear yourself back")
        self._hearback_btn.setAccessibleName("Hear yourself back")
        self._hearback_btn.clicked.connect(self._hear_yourself_back)
        layout.addWidget(self._hearback_btn)
        self._hearback_status = QLabel("")
        self._hearback_status.setObjectName("muted")
        self._hearback_status.setWordWrap(True)
        self._hearback_status.setAccessibleName("Hear yourself back status")
        layout.addWidget(self._hearback_status)
        self.ob_mic_combo = QComboBox()
        self.ob_mic_combo.setAccessibleName("Microphone")
        try:
            devs = audio_manager.get_devices()
            self.ob_mic_combo.addItem("Default microphone", None)
            for d in devs.get("inputs", []):
                self.ob_mic_combo.addItem(f"{d['name']}", d["index"])
        except Exception:  # noqa: BLE001, S110
            pass
        layout.addWidget(self.ob_mic_combo)
        self._mic_off_note = QLabel("")
        self._mic_off_note.setObjectName("muted")
        self._mic_off_note.setWordWrap(True)
        self._mic_off_note.setAccessibleName("Microphone off note")
        self._mic_off_note.setVisible(False)
        layout.addWidget(self._mic_off_note)
        self._mic_settings_btn = QPushButton(
            "Open Settings → Permissions")
        self._mic_settings_btn.setObjectName("chipBtn")
        self._mic_settings_btn.setAccessibleName(
            "Open Settings Permissions page")
        self._mic_settings_btn.clicked.connect(
            self._open_onboarding_mic_settings)
        self._mic_settings_btn.setVisible(False)
        layout.addWidget(self._mic_settings_btn)
        layout.addStretch()
        self.mic_state = "unknown"
        self._refresh_mic_page()
        return page

    def _refresh_mic_page(self) -> None:
        """Re-read the live mic state into the mic page.

        The ask shows only when the first-use ask is needed; the
        test controls enable only when the mic is usable. A page
        merely shown never grants — this only reads. Called at
        build and after every ask answer."""
        try:
            needed = bool(
                permission_grants.mic_first_use_ask_needed())
        except Exception:  # noqa: BLE001 — no ask, no test
            needed = False
        try:
            stored = permission_grants.get_state("microphone")
        except Exception:  # noqa: BLE001 — unknown reads as off
            stored = "unknown"
        try:
            session = bool(
                permission_grants.has_session_grant("microphone"))
        except Exception:  # noqa: BLE001 — no session grant
            session = False
        usable = stored == "granted" or session
        if stored == "granted":
            self.mic_state = "granted"
        elif session:
            self.mic_state = "session"
        else:
            self.mic_state = stored
        ask_block = getattr(self, "_mic_ask_block", None)
        if ask_block is not None:
            ask_block.setVisible(needed)
        # AUDITION and the device picker need the mic — they
        # disable with it. Hear-yourself-back stays clickable:
        # its handler already reports the honest nothing-recorded
        # state (including the switched-off hint), so disabling
        # it would hide the explanation.
        for attr in ("_audition_btn", "ob_mic_combo"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setEnabled(usable)
        status = getattr(self, "_mic_status", None)
        if status is not None:
            if usable:
                status.setText("Press AUDITION, then speak.")
            else:
                # Never invite a press on the disabled AUDITION
                # button — the off note below carries the message.
                status.setText("")
        off_note = getattr(self, "_mic_off_note", None)
        settings_btn = getattr(self, "_mic_settings_btn", None)
        if not usable and not needed:
            if off_note is not None:
                off_note.setText(
                    "The microphone is switched off. Switch it on "
                    "in Settings → Permissions, then come back to "
                    "test it.")
                off_note.setVisible(True)
            if settings_btn is not None:
                settings_btn.setVisible(True)
        else:
            if off_note is not None:
                off_note.setVisible(False)
            if settings_btn is not None:
                settings_btn.setVisible(False)

    def _show_mic_note(self, message: str) -> None:
        """The save-did-not-stick note for the mic ask (the
        shared choice calls this when the durable save fails and
        the session grant stands instead)."""
        label = getattr(self, "_mic_note", None)
        if label is None:
            return
        label.setText(str(message or ""))
        label.setVisible(bool(message))

    def _on_mic_ask(self, choice: str) -> str:
        """Apply one mic ask choice from the wizard's mic page.

        "Open Settings" opens the Permissions page (the dialog
        closes — the same path the mic row's settings button
        takes). Any other choice goes through the shared
        apply_mic_first_use_choice (the Connect ask's exact
        implementation), then the page re-reads its state.
        "Not now" is the one exception to the sharing: in
        first-run setup it is a durable decision, not a
        deferral — it writes microphone: denied through the
        Settings-only path first, so the Connect ask never
        re-fires (field-check #9). The Connect ask's own
        "Not now" stays session-only, as its suite pins it.
        Returns the applied choice."""
        if choice == "settings":
            self._open_onboarding_mic_settings()
            return "settings"
        if choice == "not_now":
            # "denied", not "unknown": check_tool lets an explicit
            # "unknown" mic record through, so only a denial keeps
            # the mic off. The session decline below still runs,
            # so this session is covered even if the durable
            # write fails (the user is told, and is asked again
            # at the next Connect instead of silently).
            try:
                from .pf_permissions import apply_permission_changes

                record = apply_permission_changes(
                    [], {"microphone": "denied"}, how="first-run setup")
                if "microphone" in (record.get("failed") or {}):
                    self._show_mic_note(
                        "Couldn't save that — you'll be asked again "
                        "next time you connect.")
            except Exception:  # noqa: BLE001 — the session decline
                pass           # below still covers this session
        try:
            from .pf_permissions import (
                apply_mic_first_use_choice as _apply_choice)

            result = _apply_choice(choice, notify=self._show_mic_note,
                                   how="first-run setup")
        except Exception:  # noqa: BLE001 — a broken apply denies
            result = "not_now"
        # Rethink Build 6: a mic grant made in this walk is a walk
        # grant — "always" is durable, "allow_once" is session-only
        # (including the durable-save fallback, which reports
        # "allow_once"). "not_now" grants nothing.
        if result == "always":
            self._record_walk_grant("microphone", "durable")
        elif result == "allow_once":
            self._record_walk_grant("microphone", "session")
        self._refresh_mic_page()
        return result

    def _toggle_audition(self) -> None:
        """AUDITION is mic audition only: while on, the segmented
        meter follows the live mic level; while off, the meter
        holds dark. It never plays a fake sample and never claims
        a level the mic did not produce."""
        active = self._audition_btn.isChecked()
        self._audition_btn.setText("Stop AUDITION" if active else "AUDITION")
        if active:
            self._mic_status.setText("Listening… speak now.")
            self._mic_timer.start(50)
        else:
            self._mic_timer.stop()
            self.ob_mic_level.set_level(0.0)
            self._mic_status.setText("Press AUDITION, then speak.")

    def _hear_yourself_back(self):
        """Play the last ~2.5 s the mic heard (Settings redesign
        §4a). The ring buffer holds int16 at sample_rate_in
        (16 kHz); playback takes 24 kHz, so the audio is
        resampled with numpy — no new dependency. Honest at
        every step: nothing recorded, only silence, or speakers
        closed each say so instead of playing nothing."""
        label = getattr(self, "_hearback_status", None)
        try:
            chunks = audio_manager.get_recent_mic_audio(seconds=2.5)
        except Exception:  # noqa: BLE001 — a dead mic read is "nothing"
            chunks = []
        if not chunks:
            if label is not None:
                label.setText(
                    "Nothing recorded yet — press AUDITION and speak "
                    "first, then try again. If the microphone is "
                    "switched off in Settings, nothing can be recorded.")
            return
        try:
            import numpy as np

            mono = []
            for chunk in chunks:
                arr = np.asarray(chunk)
                if arr.ndim == 2:
                    arr = arr[:, 0]
                mono.append(arr.astype(np.int16))
            pcm = (np.concatenate(mono) if mono
                   else np.zeros(0, dtype=np.int16))
            if pcm.size == 0 or not np.any(pcm != 0):
                if label is not None:
                    label.setText(
                        "Only silence reached the microphone — speak "
                        "louder, or pick another microphone below.")
                return
            rate_in = int(getattr(audio_manager, "sample_rate_in",
                                  16000) or 16000)
            if rate_in != 24000:
                x_old = np.arange(pcm.size, dtype=np.float64)
                x_new = np.linspace(
                    0, pcm.size - 1,
                    int(pcm.size * 24000 / rate_in))
                pcm = np.interp(x_new, x_old,
                                pcm.astype(np.float64)).astype(np.int16)
            out_open = getattr(audio_manager, "_out_stream", None) is not None
            queued = audio_manager.enqueue_playback(pcm.tobytes())
            if label is not None:
                if out_open and queued > 0:
                    label.setText(
                        "Playing back what you just said — that's "
                        "how she hears you.")
                elif queued > 0:
                    label.setText(
                        "The recording is queued but the speakers "
                        "aren't open right now — check your output "
                        "device and try again.")
                else:
                    label.setText(
                        "Couldn't play it back — the speakers aren't "
                        "open right now.")
        except Exception:  # noqa: BLE001 — a failed playback says so
            if label is not None:
                label.setText("Couldn't play it back — try again.")

    def _update_mic_level(self):
        level = 0.0 if audio_manager.is_muted else audio_manager.mic_level
        self.ob_mic_level.set_level(level)
        if level <= 0.0:
            self._mic_status.setText(
                "No signal yet — speak louder, or pick another "
                "microphone below.")
        else:
            self._mic_status.setText("Signal live — the segments follow your voice.")
        # Conversion Wave 9: mic-meter peak is proof-run evidence.
        _peak = getattr(self, "_w9_mic_peak", 0)
        self._w9_mic_peak = max(_peak, int(level * 100))

    def _page_voice(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._title_block(
            "Pick a voice",
            "These are Google's voices, for the cloud brain — you'll hear her properly "
            "on your first Connect. Her on-device voice is chosen with her language, "
            "and either can be changed any time in Settings."))
        self.ob_voice_buttons: list[QRadioButton] = []
        for voice in config.available_voices:
            rb = QRadioButton(voice)
            rb.setStyleSheet(
                f"QRadioButton {{ padding: 9px 12px; background: {GLASS};"
                f" border: 1px solid {BORDER};"
                f" border-radius: {RADIUS_BTN}px; }}")
            rb.setChecked(voice == config.voice_name)
            self.ob_voice_buttons.append(rb)
            layout.addWidget(rb)
        # Settings redesign §4a: hear a sample of the picked
        # voice. The only always-available speaker is the PC's
        # own voice (pyttsx3) — the label says that honestly;
        # the cloud voice is heard properly on the first Connect.
        self._voice_sample_btn = QPushButton("Hear a sample")
        self._voice_sample_btn.setAccessibleName("Hear a voice sample")
        self._voice_sample_btn.clicked.connect(self._play_voice_sample)
        layout.addWidget(self._voice_sample_btn)
        self._voice_sample_status = QLabel("")
        self._voice_sample_status.setObjectName("muted")
        self._voice_sample_status.setWordWrap(True)
        self._voice_sample_status.setAccessibleName("Voice sample status")
        layout.addWidget(self._voice_sample_status)
        layout.addStretch()
        return page

    def _play_voice_sample(self):
        """Read one sentence aloud in the PC's voice (Settings
        redesign §4a). speak_text is the always-available
        speaker; it threads internally and returns its own
        status string when reading aloud isn't available."""
        label = getattr(self, "_voice_sample_status", None)
        picked = None
        for rb in getattr(self, "ob_voice_buttons", []):
            if rb.isChecked():
                picked = rb.text()
                break
        sentence = ("Hello, I'm VYUK — your voice assistant. I can "
                    "help with your day, your files, and your computer.")
        try:
            from voice_assistant.core.gui_facade import speak_text as _speak

            result = _speak(sentence)
        except Exception:  # noqa: BLE001 — a failed sample says so
            if label is not None:
                label.setText(
                    "Couldn't play a sample — reading aloud isn't "
                    "available in this build.")
            return
        if label is not None:
            text = str(result or "")
            low = text.lower()
            if ("isn't available" in low or "not available" in low
                    or "failed" in low or "couldn't confirm" in low):
                label.setText(
                    "Couldn't play a sample — the PC's voice engine "
                    "didn't start here. You'll hear the cloud voice "
                    "properly on your first Connect.")
            elif picked:
                label.setText(
                    f"Playing a sample in the PC's voice — you picked "
                    f"{picked}, and you'll hear the cloud voice "
                    f"properly on your first Connect.")
            else:
                label.setText(
                    "Playing a sample in the PC's voice — you'll hear "
                    "the cloud voice properly on your first Connect.")

    def _page_language(self) -> QWidget:
        # v5.41: the home-language step. The pick becomes her
        # home — its curated Kokoro voice (core/languages) becomes
        # hers and she answers in that language by default. It is
        # applied only at Finish (see _next), exactly like the
        # brain choice: Skip records nothing, and installs that
        # already onboarded never see this page again.
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._title_block(
            "Which language do you prefer?",
            "Her home language: the voice she speaks with, and the "
            "language she answers in by default. When a reply comes "
            "out in another language she switches voices on her own "
            "— and you can change this any time in Settings."))
        current = str(getattr(config, "preferred_language", "en-gb")
                      or "en-gb")
        self.ob_language_buttons: list[QRadioButton] = []
        for entry in LANGUAGES:
            rb = QRadioButton(entry.label)
            rb.setStyleSheet(
                f"QRadioButton {{ padding: 6px 12px; background: {GLASS};"
                f" border: 1px solid {BORDER};"
                f" border-radius: {RADIUS_BTN}px; }}")
            rb.setChecked(entry.code == current)
            rb.setProperty("language_code", entry.code)
            self.ob_language_buttons.append(rb)
            layout.addWidget(rb)
        layout.addStretch()
        return page

    def _page_browser(self) -> QWidget:
        """The Browser ask page — the one-tap connect widget, as
        a wizard page (scope §3, D1/D5/D7). The widget is the
        flow; the page is only its container. The widget is kept
        on the page as ``page.widget`` for the suite."""
        from .browser_connect_widget import BrowserConnectWidget

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        widget = BrowserConnectWidget(page)
        layout.addWidget(widget)
        layout.addStretch()
        page.widget = widget
        page.ask_result = "unanswered"
        # Build 5 (§9.1 step 0): "Not now" records the skip and
        # nothing else — the browser stays unknown, so a later
        # browser use still asks again. The widget already shows
        # the confirm line and hides its buttons; Continue moves
        # on from here, as after the other asks' Skip.
        widget.not_now_requested.connect(
            lambda: setattr(page, "ask_result", "skipped"))
        # Rethink Build 6: a connected browser in this walk is a
        # walk grant — the permissions page's browser row offers
        # Take back for it.
        widget.connect_result.connect(self._on_browser_connected)
        return page

    # Rethink Build 6 (§6.9): the ask pages whose grants this
    # walk can take back, and the wizard page each one asks on.
    _ASK_PAGE_ATTRS = {
        "microphone": "_mic_page",
        "browser": "_browser_page",
        "camera": "_camera_page",
        "screen": "_screen_page",
        "files": "_files_page",
    }

    def _record_walk_grant(self, pid: str, kind: str) -> None:
        """Note a grant made during this walk (pid -> "durable" |
        "session") for the permissions page's Take back."""
        grants = getattr(self, "_walk_grants", None)
        if not isinstance(grants, dict):
            grants = {}
            self._walk_grants = grants
        grants[str(pid)] = kind

    def _on_browser_connected(self, payload: str) -> None:
        """Record a browser connect in this walk as a walk grant."""
        try:
            import json as _json

            from .browser_connect_widget import _CONNECTED_STATES

            result = _json.loads(payload) if payload else {}
            state = str((result or {}).get("state", ""))
        except Exception:  # noqa: BLE001 — no grant recorded
            return
        if state in _CONNECTED_STATES:
            self._record_walk_grant("browser", "durable")
            page = getattr(self, "_browser_page", None)
            if page is not None:
                page.ask_result = "granted"

    def _page_camera(self) -> QWidget:
        return self._page_ask("camera")

    def _page_screen(self) -> QWidget:
        return self._page_ask("screen")

    def _page_files(self) -> QWidget:
        return self._page_ask("files")

    def _page_ask(self, permission_id: str) -> QWidget:
        """One first-run ask page (camera / screen / files).

        One ask, one Yes, one Skip. The Yes writes through the
        Settings-only path (apply_permission_changes /
        add_folder with via="settings") on the tap — never on
        page entry. Skip writes nothing: the permission stays
        undecided (files: she asks each time instead; camera /
        screen: stays off until switched on in Settings). The
        page carries ``ask_result`` ("unanswered" until answered)
        and ``chosen_folder`` (files only). Field-check #11: a
        page whose permission is already decided (an explicit
        granted/denied record in the store — a resumed wizard)
        hides the ask and names the state instead of re-asking,
        as the mic page already does; the state is read at build,
        and a resumed dialog builds fresh pages, so the read is
        current. Field-check fix #21: "decided" means an explicit
        record — get_state() fail-closes a missing camera record
        to "denied", which is NOT a decision (never-decided), so
        the ask still shows on a fresh install or when the store
        holds no camera record yet. A corrupt store still hides
        the ask (D2 no-ask-again, as the mic page does)."""
        copy = first_run_asks.ASK_COPY[permission_id]
        page = QWidget()
        page.permission_id = permission_id
        page.ask_result = "unanswered"
        page.chosen_folder = ""
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._title_block(copy["title"], copy["body"]))
        row = QHBoxLayout()
        row.setSpacing(8)
        yes_label = str(copy["buttons"][0])
        yes_btn = QPushButton(yes_label)
        yes_btn.setObjectName("primaryBtn")
        yes_btn.setAccessibleName(yes_label)
        yes_btn.clicked.connect(
            lambda _checked=False, p=page, pid=permission_id:
                self._on_ask_answer(p, pid, "yes"))
        row.addWidget(yes_btn)
        skip_btn = QPushButton("Not now")
        skip_btn.setObjectName("chipBtn")
        skip_btn.setAccessibleName("Not now")
        skip_btn.clicked.connect(
            lambda _checked=False, p=page, pid=permission_id:
                self._on_ask_answer(p, pid, "skip"))
        row.addWidget(skip_btn)
        row.addStretch()
        layout.addLayout(row)
        confirm = QLabel("")
        confirm.setObjectName("muted")
        confirm.setWordWrap(True)
        confirm.setAccessibleName("Ask answer")
        confirm.setVisible(False)
        layout.addWidget(confirm)
        page._confirm_label = confirm
        page._yes_button = yes_btn
        page._skip_button = skip_btn
        try:
            _state = permission_grants.get_state(permission_id)
            _decided = not permission_grants.permission_never_decided(
                permission_id)
        except Exception:  # noqa: BLE001 — undecided reads as ask
            _state = "unknown"
            _decided = False
        if _state in ("granted", "denied") and _decided:
            # Already decided — hide the ask, name the state.
            confirm.setText(self._decided_ask_line(
                permission_id, _state))
            confirm.setVisible(True)
            self._hide_ask_buttons(page)
        layout.addStretch()
        return page

    @staticmethod
    def _decided_ask_line(permission_id: str, state: str) -> str:
        """The state line for an already-decided ask page (#11).

        Mirrors the Yes-confirm wording, so a resumed wizard
        reads the same as a just-answered one."""
        if permission_id == "camera":
            if state == "granted":
                return ("Done — the camera is already on. You can "
                        "switch it off any time in Settings → "
                        "Permissions.")
            return ("The camera is switched off. You can switch it "
                    "on any time in Settings → Permissions.")
        if permission_id == "screen":
            if state == "granted":
                return ("Done — VYUK can already keep what she sees. "
                        "You can switch it off any time in Settings → "
                        "Permissions.")
            return ("Keeping what she sees is switched off. You can "
                    "switch it on any time in Settings → Permissions.")
        if state == "granted":
            return ("Done — VYUK can already use your files. You can "
                    "add more folders later in Settings → Permissions.")
        return ("Files are switched off. You can switch them on any "
                "time in Settings → Permissions.")

    @staticmethod
    def _hide_ask_buttons(page) -> None:
        for attr in ("_yes_button", "_skip_button"):
            btn = getattr(page, attr, None)
            if btn is not None:
                btn.setVisible(False)

    def _on_ask_answer(self, page, permission_id: str,
                       answer: str) -> str:
        """Apply one ask answer. Returns the result
        ("granted" | "folder" | "skipped" | "failed") and shows
        the confirmation line. A failed write keeps the buttons
        visible so the user can retry; a skip or a grant hides
        them — the ask is answered either way."""
        confirm = getattr(page, "_confirm_label", None)

        def _say(text: str) -> None:
            if confirm is not None:
                confirm.setText(text)
                confirm.setVisible(True)

        if answer == "skip":
            page.ask_result = "skipped"
            if permission_id == "files":
                _say("Not now — skipped. She'll ask you each time "
                     "instead.")
            else:
                _say("Not now — skipped. You can switch it on any "
                     "time in Settings → Permissions.")
            self._hide_ask_buttons(page)
            return "skipped"
        if permission_id == "files":
            folder = _pick_folder(self)
            if not folder:
                # Picker cancelled — a skip, not a grant.
                page.ask_result = "skipped"
                _say("No folder picked — skipped. She'll ask you "
                     "each time instead.")
                self._hide_ask_buttons(page)
                return "skipped"
            try:
                ok = bool(permission_grants.add_folder(
                    folder, via="settings"))
            except Exception:  # noqa: BLE001 — a failed add says so
                ok = False
            if not ok:
                page.ask_result = "failed"
                _say("Couldn't add that folder — try Settings → "
                     "Permissions.")
                return "failed"
            # Field-check #8: the folder alone grants nothing —
            # the permission is the gate, and the confirm below
            # claims "VYUK can use" the folder. Grant files too,
            # through the same Settings-only path the
            # camera/screen Yes uses (NOT grant_folder_and_files:
            # its only caller is the confirm dialog's folder
            # button, pinned by the rethink-Build-4 suite).
            try:
                from .pf_permissions import apply_permission_changes

                record = apply_permission_changes(
                    [], {"files": "granted"}, how="first-run setup")
                granted_ok = "files" not in (record.get("failed") or {})
            except Exception:  # noqa: BLE001 — a failed flip says so
                granted_ok = False
            if not granted_ok:
                page.ask_result = "failed"
                _say("Folder added, but the files permission "
                     "didn't switch on — try Settings → "
                     "Permissions.")
                return "failed"
            page.ask_result = "folder"
            page.chosen_folder = folder
            self._record_walk_grant("files", "durable")
            _say(f"Done — VYUK can use {folder}. You can add more "
                 "later in Settings → Permissions.")
            self._hide_ask_buttons(page)
            return "folder"
        try:
            from .pf_permissions import apply_permission_changes

            record = apply_permission_changes(
                [], {permission_id: "granted"},
                how="first-run setup")
            ok = permission_id not in (record.get("failed") or {})
        except Exception:  # noqa: BLE001 — a failed flip says so
            ok = False
        if not ok:
            page.ask_result = "failed"
            _say("Couldn't switch it on — try Settings → "
                 "Permissions.")
            return "failed"
        page.ask_result = "granted"
        self._record_walk_grant(permission_id, "durable")
        if permission_id == "camera":
            _say("Done — the camera is on. You can switch it off "
                 "any time in Settings → Permissions.")
        elif permission_id == "screen":
            _say("Done — VYUK can keep what she sees. You can "
                 "switch it off any time in Settings → Permissions.")
        else:
            _say("Done — switched on. You can switch it off any "
                 "time in Settings → Permissions.")
        self._hide_ask_buttons(page)
        return "granted"

    def _page_permissions(self) -> QWidget:
        # Rethink Build 6 (§6.9): the permissions page is a status
        # list, not explainers. One row per permission — name (13
        # semibold) + state word (13) + one action button — plus the
        # sounds opt-in as a row of its own. Diagnostics live behind
        # "Details" per row; the page shows state, it never flips
        # anything (D8: read + tap-to-ask, never a silent flip).
        # Rows granted earlier in this walk show "Take back" on the
        # spot, so a grant made two pages ago can be undone without
        # leaving the wizard. The Memory / Cloud explainers are
        # gone: they are not permissions — Memory's switch lives on
        # the Memory page (and the memory permission row in
        # Always-allow), Cloud's in Settings → How VYUK thinks.
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title = QLabel("Permissions — your call")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self._perm_rows: dict[str, dict] = {}
        try:
            from .pf_permissions import permissions_model

            entries = permissions_model()
        except Exception:  # noqa: BLE001 — rows build with "—"
            entries = []
        for entry in entries:
            self._build_perm_row(layout, entry)
        self._build_sounds_row(layout)
        self._perm_status = QLabel("")
        self._perm_status.setObjectName("muted")
        self._perm_status.setWordWrap(True)
        self._perm_status.setVisible(False)
        layout.addWidget(self._perm_status)
        layout.addStretch()
        self._refresh_permissions_page()
        return page

    def _build_perm_row(self, layout, entry) -> None:
        """One status row + its hidden Details panel.

        The mic row's Details panel holds the live state lines
        (B6: the row reads the same state the Permissions page
        reads, VYUK-side and Windows-side separately, plus the
        diagnosis). It never grants, flips, or pops the first-use
        ask: the ask fires on the wizard's Microphone page (D8)
        and at a human Connect, and the switch lives in Settings
        (D8). The panel's three labels keep their pinned attribute
        names (_ob_mic_vyuk / _ob_mic_os / _ob_mic_diag) and are
        filled by _refresh_onboarding_mic.
        """
        pid = entry.permission_id
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        name = QLabel(entry.title)
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        row_layout.addWidget(name)
        row_layout.addStretch(1)
        state = QLabel("—")
        state.setStyleSheet("font-size: 13px;")
        row_layout.addWidget(state)
        action = QPushButton("Details")
        action.setObjectName("chipBtn")
        action.clicked.connect(
            lambda _checked=False, p=pid: self._on_perm_row_action(p))
        row_layout.addWidget(action)
        layout.addWidget(row)
        details = QWidget()
        d_layout = QVBoxLayout(details)
        d_layout.setContentsMargins(12, 2, 0, 6)
        d_layout.setSpacing(4)
        detail_line = QLabel("")
        detail_line.setObjectName("muted")
        detail_line.setWordWrap(True)
        d_layout.addWidget(detail_line)
        os_line = QLabel("")
        os_line.setObjectName("muted")
        os_line.setWordWrap(True)
        d_layout.addWidget(os_line)
        diag_line = QLabel("")
        diag_line.setObjectName("muted")
        diag_line.setWordWrap(True)
        d_layout.addWidget(diag_line)
        wayback = QLabel("Change it in Settings → Permissions.")
        wayback.setObjectName("muted")
        wayback.setWordWrap(True)
        d_layout.addWidget(wayback)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        if pid == "microphone":
            self._ob_mic_refresh = QPushButton("Check again")
            self._ob_mic_refresh.setObjectName("chipBtn")
            self._ob_mic_refresh.setAccessibleName(
                "Check microphone state again")
            self._ob_mic_refresh.clicked.connect(
                self._refresh_onboarding_mic)
            btn_row.addWidget(self._ob_mic_refresh)
        settings_btn = QPushButton("Open Settings → Permissions")
        settings_btn.setObjectName("chipBtn")
        settings_btn.setAccessibleName(
            "Open Settings Permissions page")
        settings_btn.clicked.connect(
            self._open_onboarding_mic_settings)
        btn_row.addWidget(settings_btn)
        btn_row.addStretch()
        d_layout.addLayout(btn_row)
        details.setVisible(False)
        layout.addWidget(details)
        if pid == "microphone":
            self._ob_mic_vyuk = detail_line
            self._ob_mic_os = os_line
            self._ob_mic_diag = diag_line
            self._ob_mic_settings = settings_btn
        self._perm_rows[pid] = {
            "row": row, "name": name, "state": state,
            "action": action, "details": details,
            "detail_line": detail_line, "os_line": os_line,
            "diag_line": diag_line,
        }

    def _build_sounds_row(self, layout) -> None:
        """The sounds opt-in as a status row (D12).

        Sounds is a QSettings opt-in the page owns, not a grant —
        its row flips the opt-in, and the hidden checkbox stays the
        single source _persist_sound_choice reads at Finish.
        """
        try:
            checked = bool(qsettings_get("sounds_enabled")) \
                if qsettings_contains("sounds_enabled") else False
        except Exception:  # noqa: BLE001 — unchecked is the safe read
            checked = False
        self._sound_checkbox = QCheckBox(
            "Play sounds (earcons and voice feedback)")
        self._sound_checkbox.setAccessibleName("Play sounds opt-in")
        self._sound_checkbox.setChecked(checked)
        self._sound_checkbox.setVisible(False)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        name = QLabel("Sounds")
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        row_layout.addWidget(name)
        row_layout.addStretch(1)
        self._sounds_state = QLabel("")
        self._sounds_state.setStyleSheet("font-size: 13px;")
        row_layout.addWidget(self._sounds_state)
        self._sounds_action = QPushButton("Turn on")
        self._sounds_action.setObjectName("chipBtn")
        self._sounds_action.clicked.connect(self._on_sounds_toggle)
        row_layout.addWidget(self._sounds_action)
        layout.addWidget(row)
        self._refresh_sounds_row()

    def _on_sounds_toggle(self) -> None:
        checkbox = getattr(self, "_sound_checkbox", None)
        if checkbox is None:
            return
        checkbox.setChecked(not checkbox.isChecked())
        self._refresh_sounds_row()

    def _refresh_sounds_row(self) -> None:
        checkbox = getattr(self, "_sound_checkbox", None)
        label = getattr(self, "_sounds_state", None)
        action = getattr(self, "_sounds_action", None)
        if checkbox is None or label is None or action is None:
            return
        on = bool(checkbox.isChecked())
        label.setText("On" if on else "Off")
        action.setText("Turn off" if on else "Turn on")

    @staticmethod
    def _perm_state_word(pid: str, entry) -> str:
        """The row's one state word: On / Off / Never decided /
        This session only. Order matters, and two readings in the
        store lie about the decision:

        * A durable grant wins over a session grant: "Always
          allow" opens the mic for this session too, so the
          session grant is present alongside the durable one —
          the row must read "On", not "This session only".
        * A missing record reads as "denied" for the fail-closed
          ids (microphone, camera) — that is the funnel's safe
          default, not a decision. The never-decided check must
          therefore come BEFORE the denied branch, or a true
          fresh install reads "Off" for the mic.
        * A decided-unknown record (explicit "unknown" in the
          store) reads "Off" — nothing is granted — and its
          Details panel carries the exact sentence.
        A missing entry reads as unknown, never as granted.
        """
        if entry is None:
            return "—"
        if entry.state == "granted":
            return "On"
        try:
            if permission_grants.has_session_grant(pid):
                return "This session only"
        except Exception:  # noqa: BLE001 — fall through to the store
            pass
        try:
            never = permission_grants.permission_never_decided(pid)
        except Exception:  # noqa: BLE001 — unknown reads undecided
            never = True
        if never:
            return "Never decided"
        return "Off"

    def _refresh_permissions_page(self) -> None:
        """Re-read every row's state word and action (Build 6).

        Called at build and on every show of the page (_sync_nav),
        so a grant made on an earlier wizard page is visible here
        the moment the user arrives — the fix #29 hook, widened
        from the mic row to the whole list. Reads only; never
        writes, never pops an ask, never flips anything.
        """
        self._refresh_onboarding_mic()
        self._refresh_sounds_row()
        try:
            from .pf_permissions import permissions_model

            entries = permissions_model()
        except Exception:  # noqa: BLE001 — keep the old words
            entries = []
        by_id = {e.permission_id: e for e in entries}
        walk = getattr(self, "_walk_grants", None) or {}
        for pid, widgets in getattr(self, "_perm_rows", {}).items():
            entry = by_id.get(pid)
            widgets["state"].setText(
                self._perm_state_word(pid, entry))
            action = widgets["action"]
            if pid in walk:
                action.setText("Take back")
            elif pid == "microphone":
                # The mic's diagnostics are the point of its row —
                # its Details panel carries the Windows state and
                # the probe result, which no other row has.
                action.setText("Details")
            elif pid in self._ASK_PAGE_ATTRS:
                try:
                    never = permission_grants.permission_never_decided(
                        pid)
                except Exception:  # noqa: BLE001 — ask, don't hide
                    never = True
                action.setText("Ask me" if never else "Details")
            else:
                action.setText("Details")
            if pid != "microphone" and entry is not None:
                widgets["detail_line"].setText(entry.state_line())
                os_text = entry.os_state_line()
                widgets["os_line"].setText(os_text)
                widgets["os_line"].setVisible(bool(os_text))
                diag = entry.diagnosis or ""
                widgets["diag_line"].setText(diag)
                widgets["diag_line"].setVisible(bool(diag))

    def _on_perm_row_action(self, pid: str) -> None:
        """The row's one action button: Take back, Ask me, Details."""
        widgets = getattr(self, "_perm_rows", {}).get(pid)
        if widgets is None:
            return
        action = widgets["action"].text()
        if action == "Take back":
            self._on_take_back_permission(pid)
            return
        if action == "Ask me":
            target = self._ASK_PAGE_ATTRS.get(pid)
            page = getattr(self, target, None) if target else None
            if page is not None:
                self.pages.setCurrentWidget(page)
                self._sync_nav()
            return
        details = widgets["details"]
        details.setVisible(not details.isVisible())

    def _on_take_back_permission(self, pid: str) -> None:
        """Undo a grant made earlier in this walk (Build 6).

        The permission returns to never-decided, so the next use
        asks again. A durable grant is withdrawn through the
        store's take-back (the record is deleted, not flipped to
        a denial); a session-only grant is revoked; a files grant
        also removes the folder the walk added. "Always allow"
        also opened this session's mic, so a taken-back durable
        grant must not leave a session grant behind.
        """
        walk = getattr(self, "_walk_grants", None) or {}
        kind = walk.get(pid)
        ok = False
        if kind == "session":
            try:
                ok = bool(permission_grants.revoke_session_grant(pid))
            except Exception:  # noqa: BLE001 — reported below
                ok = False
        elif kind == "durable":
            try:
                ok = bool(permission_grants.clear_permission_decision(
                    pid, via="settings", how="first-run setup"))
            except Exception:  # noqa: BLE001 — reported below
                ok = False
            try:
                permission_grants.revoke_session_grant(pid)
            except Exception:  # noqa: BLE001 — the durable write stands
                pass
            if ok and pid == "files":
                page = getattr(self, "_files_page", None)
                folder = getattr(page, "chosen_folder", "") or ""
                if folder:
                    try:
                        permission_grants.remove_folder(
                            folder, via="settings")
                    except Exception:  # noqa: BLE001 — grant is back
                        pass
        status = getattr(self, "_perm_status", None)
        if ok:
            walk.pop(pid, None)
            if status is not None:
                status.setVisible(False)
            self._refresh_permissions_page()
        elif status is not None:
            status.setText(
                "Couldn't take that back — try Settings → "
                "Permissions.")
            status.setVisible(True)

    def _refresh_onboarding_mic(self) -> None:
        """Re-read the live mic state into the onboarding row.

        A read failure is shown honestly (unknown, not a
        denial) — the row never keeps a stale state and never
        invents one."""
        state = onboarding_mic_state()
        vyuk = getattr(self, "_ob_mic_vyuk", None)
        os_lbl = getattr(self, "_ob_mic_os", None)
        diag = getattr(self, "_ob_mic_diag", None)
        if vyuk is None:
            return
        if state is None:
            vyuk.setText(
                "Microphone state couldn't be read — unknown, "
                "not a denial.")
            if os_lbl is not None:
                os_lbl.setText("")
            if diag is not None:
                diag.setText(
                    "Check it in Settings → Permissions, or when "
                    "you press Connect for the first time.")
            return
        vyuk.setText(state["vyuk_line"])
        if os_lbl is not None:
            os_lbl.setText(state["os_line"])
        if diag is not None:
            diag.setText(state["diagnosis"])

    def _open_onboarding_mic_settings(self) -> None:
        """User-pressed path to the Permissions page (B6).

        Emits open_permissions_requested (the main window shows
        the Permissions page, stack 4 — the same page the
        first-use mic ask opens) and accepts the dialog, so the
        page is visible instead of hidden behind the modal
        dialog. Never grants, never flips, never pops the
        first-use ask."""
        sig = getattr(self, "open_permissions_requested", None)
        try:
            if sig is not None:
                sig.emit()
        except Exception:  # noqa: BLE001 — the dialog still closes
            pass
        try:
            self.accept()
        except Exception:  # noqa: BLE001 — never break onboarding
            pass

    def _page_selftest(self) -> QWidget:
        # Phase G stage 4, first half: the real self-test. The
        # page auto-runs when opened (showEvent) and re-runs on
        # Run again — one runner, off the GUI thread (see
        # gui/selftest_page.py). It never runs at construction:
        # the old build-time run went stale whenever the wizard
        # navigated back to this page, and it blocked the GUI
        # thread while the checks ran.
        from .selftest_page import _SelfTestPage

        return _SelfTestPage(
            self._title_block(
                "Self-test",
                "Real checks, run now. A FAULT line names what failed "
                "— the full Doctor with fixes lives in Diagnostics."),
            run_first_run_self_tests)

    @staticmethod
    def _pack_incomplete() -> bool:
        """True when any pack piece is still missing — pure pack
        state (v5.23: the brain choice hasn't been persisted yet at
        dialog build time, so the old engine check no longer belongs
        here; whether the Setting up page APPLIES is the flow's
        decision, in _page_applies). Any failure reads as "no page"
        — onboarding must never break on a progress check."""
        try:
            from voice_assistant.core.gui_facade import local_assets

            return not (local_assets.whisper_ready()
                        and local_assets.voice_ready()
                        and local_assets.brain_ready())
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _setup_body_text(done: bool) -> str:
        if done:
            return "All set — she's fully herself."
        size_clause = ""
        try:
            from voice_assistant.core.gui_facade import local_assets

            eff = local_assets.effective_model()
            tier = local_assets.MODEL_TIERS.get(eff.get("tier") or "")
            if tier:
                # Tier GB + Whisper small (~0.5 GB) + Kokoro (~0.34 GB).
                total_gb = float(tier["gb"]) + 0.84
                size_clause = (f" — about {total_gb:.1f} GB "
                               f"on {(eff['tier'] or '').capitalize()}")
        except Exception:  # noqa: BLE001 — the size is a nicety
            size_clause = ""
        return (f"One-time setup{size_clause}. VYUK already works while "
                "this finishes; waiting here just means she's at her "
                "best from your first word.")

    def _page_setup(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Setting up")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.setup_body = QLabel(self._setup_body_text(done=False))
        self.setup_body.setObjectName("muted")
        self.setup_body.setWordWrap(True)
        layout.addWidget(self.setup_body)
        self._setup_rows = {}
        for stage, name in (("ears", "Her ears — speech recognition"),
                            ("voice", "Her voice"),
                            ("brain", "Her brain")):
            row = QHBoxLayout()
            name_lbl = QLabel(name)
            state_lbl = QLabel("Waiting")
            state_lbl.setObjectName("muted")
            state_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(name_lbl)
            row.addStretch()
            row.addWidget(state_lbl)
            layout.addLayout(row)
            self._setup_rows[stage] = state_lbl
        self.setup_progress = QProgressBar()
        self.setup_progress.setRange(0, 100)
        self.setup_progress.setValue(0)
        self.setup_progress.setTextVisible(False)
        self.setup_progress.setFixedHeight(8)
        self.setup_progress.setStyleSheet(
            f"QProgressBar {{ background: {GLASS}; border: none;"
            f" border-radius: {RADIUS_BTN}px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT};"
            f" border-radius: {RADIUS_BTN}px; }}")
        layout.addWidget(self.setup_progress)
        # v4.2: rotating feature cards BELOW the stage rows — the
        # first-run wait is the one moment a new user is watching, so
        # the page cycles through what VYUK can do while the pack
        # lands. Phase G: the card area is labelled as what it is —
        # a SIMULATED showcase (descriptions of capabilities, not a
        # live demo), so nothing here reads as a real turn.
        self.setup_showcase_tag = QLabel(
            "SIMULATED SHOWCASE — descriptions of what she can do, "
            "not a live demo.")
        self.setup_showcase_tag.setObjectName("muted")
        self.setup_showcase_tag.setWordWrap(True)
        layout.addWidget(self.setup_showcase_tag)
        self.setup_feature_card = QWidget()
        self.setup_feature_card.setStyleSheet(
            f"QWidget {{ background: {GLASS};"
            f" border: 1px solid {BORDER};"
            f" border-radius: {RADIUS_CARD}px; }}")
        card_layout = QVBoxLayout(self.setup_feature_card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(4)
        self.setup_feature_title = QLabel("")
        self.setup_feature_title.setStyleSheet(
            "background: transparent; border: none; font-weight: 600;")
        card_layout.addWidget(self.setup_feature_title)
        self.setup_feature_body = QLabel("")
        self.setup_feature_body.setObjectName("muted")
        self.setup_feature_body.setWordWrap(True)
        self.setup_feature_body.setStyleSheet(
            "background: transparent; border: none;")
        card_layout.addWidget(self.setup_feature_body)
        self.setup_feature_indicator = QLabel("")
        self.setup_feature_indicator.setObjectName("cardHint")
        self.setup_feature_indicator.setStyleSheet(
            "background: transparent; border: none; letter-spacing: 3px;")
        card_layout.addWidget(self.setup_feature_indicator)
        layout.addWidget(self.setup_feature_card)
        self._show_feature_card(0)
        layout.addStretch()
        self._refresh_setup_page()
        return page

    def _show_feature_card(self, index: int):
        """Shows feature card `index` (wraps around) in the Setting
        up page's card area: title, one line, and the dot indicator."""
        cards = self.FEATURE_CARDS
        self._feature_index = index % len(cards)
        title, body = cards[self._feature_index]
        self.setup_feature_title.setText(title)
        self.setup_feature_body.setText(body)
        self.setup_feature_indicator.setText(
            " ".join("●" if i == self._feature_index else "○"
                     for i in range(len(cards))))

    def _advance_feature_card(self):
        self._show_feature_card(self._feature_index + 1)

    def _refresh_setup_page(self):
        """Repaints the Setting up page from pack_progress() — real
        stage states and byte-derived percentages only."""
        rows = getattr(self, "_setup_rows", None)
        if not rows:
            return
        try:
            from voice_assistant.core.gui_facade import local_assets

            progress = local_assets.pack_progress()
        except Exception:  # noqa: BLE001 — a broken probe leaves the page as-is
            return
        by_stage = {s.get("stage"): s for s in progress.get("stages", [])}
        for stage, lbl in rows.items():
            info = by_stage.get(stage) or {}
            state = info.get("state", "waiting")
            pct = info.get("pct")
            if state == "done":
                text = "✓ Done"
            elif state == "paused":
                text = "Paused"
            elif state in ("downloading", "partial"):
                text = (f"Downloading… {int(pct)}%" if pct is not None
                        else "Downloading…")
            else:
                text = "Waiting"
            lbl.setText(text)
        overall = progress.get("overall_pct")
        if overall is None:
            self.setup_progress.setRange(0, 0)  # indeterminate — honestly unknown
        else:
            self.setup_progress.setRange(0, 100)
            self.setup_progress.setValue(int(overall))
        if progress.get("complete"):
            self.setup_body.setText(self._setup_body_text(done=True))

    def _page_done(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._title_block(
            "You're all set",
            f"Press Connect, then just talk. Try: “Hey {config.assistant_name}, "
            "what can you do?” — or hold Space anywhere in the window to talk."))
        # v5.23: one line naming the brain the user actually chose
        # (filled in by _sync_nav when this page shows).
        self.done_brain_line = QLabel("")
        self.done_brain_line.setObjectName("muted")
        self.done_brain_line.setWordWrap(True)
        layout.addWidget(self.done_brain_line)
        # Phase G: the first real mission is a suggestion, not a
        # script — and the coach marks that follow are named here
        # so the overlay after Finish is never a surprise.
        self.done_mission_line = QLabel(
            "First mission: ask her “What can you do?” — then dismiss "
            "any tip you don't want; the orb, the composer, and the "
            "voice chip each get one short pointer.")
        self.done_mission_line.setObjectName("muted")
        self.done_mission_line.setWordWrap(True)
        layout.addWidget(self.done_mission_line)
        # Conversion Wave 9 (PF-1): the first-run proof widget on
        # the done page — the runner refreshes it from live
        # evidence whenever the done page is shown (_sync_nav).
        # A check passes only on evidence; the rest stay not-run
        # with a next step, never claimed.
        try:
            from voice_assistant.gui import pf_surfaces as _pf
            self._w9_proof_widget = _pf.FirstRunProofWidget()
            layout.addWidget(self._w9_proof_widget)
        except Exception:  # noqa: BLE001 — onboarding must still build
            self._w9_proof_widget = None
        layout.addStretch()
        return page

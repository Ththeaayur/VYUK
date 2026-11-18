"""
First-run onboarding for VYUK (Phase G, Command Deck).

Four required stages, in guide order — interaction, brain,
permissions, first real mission — under an INITIALIZING step
rail. Everything is skippable and resumable: Continue never
gates except on the brain-choice page (a choice must exist
before the flow can use it), Skip setup exits the whole flow,
and stage progress persists so a reopened flow resumes at the
first unfinished stage. Advanced setup (voice, language, tool
profile) is deferred behind an "Advanced setup" button — the
shipped defaults stand until the user opens it.

v5.23 — the three-option brain choice (the owner's explicit
design): before anything downloads, the user picks where her
brain lives — (1) On this PC, (2) Cloud key, (3) Both — on a
page that lists every feature AND every cost of each. The key
page only appears for cloud/both; the Setting up page only for
local/both with the pack still incomplete; downloads start
exactly once, as the user leaves the choice page with a
local/both pick — never at launch, never on Skip. Finish
persists the choice (QSettings setup/brain_choice +
setup/local_pack_wanted, and BRAIN_ENGINE in .env); skipping
persists nothing except the D12 sound safe default (see the
permissions page: a fresh install starts silent until the user
opts into sound).

Token-only: this file carries no colour literal (the Command
Deck colour guard scans gui/*.py); every colour comes from
gui/style.py tokens, and every corner uses a radius token.
"""

from PySide6.QtCore import QEvent, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import (
    qsettings_contains,
    qsettings_set,
    qsettings_store,
)

from .firstrun_g import FIRST_RUN_STAGES, FirstRunProgress, STAGE_TITLES
from .style import (
    PREMIUM_STYLE,
    TEXT_MUTED,
    TYPE_CAPTION,
    TYPE_MICRO,
    load_bundled_fonts,
)
from .onboarding_flow import FlowMixin
from .onboarding_pages import PagesMixin
from .onboarding_widgets import (
    _ChoiceCard,
    # GUI split #13 INTENTIONAL: _MicSegments is kept here for
    # importer completeness (batch-1 facade re-exported it).
    _MicSegments,
    _QSettingsStore,
    _feature_cards,
)
# The Self-test auto-run set lives in gui/selftest_checks.py
# (single place, stdlib-only at top level); re-exported here
# so existing importers keep working.
from .selftest_checks import run_first_run_self_tests


class OnboardingDialog(PagesMixin, FlowMixin, QDialog):
    # B6: the permissions page's "Open Settings → Permissions"
    # button asks the main window to show the Permissions page
    # (stack 4 — the same page the first-use mic ask opens).
    # The dialog accepts on that press, so the page is visible
    # instead of hidden behind the modal dialog.
    open_permissions_requested = Signal()
    # Settings redesign §4a: the key page's Test button runs the
    # cloud diagnostic on a worker thread and reports back on
    # this signal, so the verdict lands on the GUI thread.
    key_test_completed = Signal(str, str)
    """First-run onboarding dialog (GUI split #13).

    Shell lives here; page builders live in
    onboarding_pages.PagesMixin, flow logic in
    onboarding_flow.FlowMixin.
    """

    FEATURE_CARDS = _feature_cards()

    # v5.23: the Done page names the brain the user actually chose.
    _BRAIN_LINES = {
        "local": "She'll think on this PC — private, offline, and "
                 "always available.",
        "cloud": "She'll think in the cloud with your Gemini key — "
                 "her sharpest mind.",
        "both": "She'll think in the cloud first, with the on-PC "
                "brain as her safety net.",
    }

    # Round-2 N4 FAIL 2: the Done page must not claim a cloud
    # brain the user does not have. When no Gemini key is stored,
    # these lines replace the _BRAIN_LINES ones for cloud/both.
    _BRAIN_LINES_NO_KEY = {
        "cloud": "You picked the cloud brain, but no Gemini key is "
                 "saved yet — she'll think on this PC until you add "
                 "one in Settings.",
        "both": "She'll think on this PC for now — add your Gemini "
                "key in Settings and she'll think in the cloud "
                "first, with the on-PC brain as her safety net.",
    }

    # Phase G stage map: every built page belongs to exactly one
    # required stage; the advanced pages ride the detour only.
    # First-run onboarding (2026-11-16): the permissions stage
    # is the ask sequence now — browser, camera, screen, files,
    # then the sounds + mic-row page.
    _STAGE_PAGES = {
        "interaction": ("_welcome_page", "_mic_page"),
        "brain": ("_choice_page", "_key_page", "_setup_page"),
        "permissions": ("_browser_page", "_camera_page", "_screen_page",
                        "_files_page", "_permissions_page"),
        "first_mission": ("_selftest_page", "_done_page"),
    }
    _ADVANCED_PAGES = ("_voice_page", "_language_page", "_profile_page")

    def __init__(self, parent=None, choice_only: bool = False,
                 initial_choice: str | None = None):
        super().__init__(parent)
        # v5.25: choice_only mode — the dialog IS the brain choice
        # (choice → key/setup as they apply → done), for installs
        # that onboarded before the choice existed and for the
        # Settings "Compare all three brain options" button. The
        # welcome/mic/voice pages are built but never visited.
        self.choice_only = bool(choice_only)
        self._initial_choice = initial_choice
        self.setWindowTitle(f"Welcome to {config.assistant_name}")
        # UX-safe batch: no more unconditional fixed size. The
        # dialog is resizable, its pages scroll when the dialog is
        # smaller than their content (see the scroll wrap below),
        # and the initial size is constrained to the available
        # work area so it never opens taller than the display. A
        # modest minimum keeps it usable; choice cards and flow
        # are untouched.
        self.setMinimumSize(420, 300)
        try:
            _screen = QGuiApplication.primaryScreen()
            _avail = (_screen.availableGeometry()
                      if _screen is not None else None)
        except Exception:  # noqa: BLE001 — headless fallback below
            _avail = None
        if _avail is not None:
            self.resize(min(780, max(420, _avail.width() - 40)),
                        min(560, max(300, _avail.height() - 40)))
        else:
            self.resize(780, 560)
        load_bundled_fonts()  # v3.0: bundled Inter, as in the main window
        self.setStyleSheet(PREMIUM_STYLE)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 20)
        root.setSpacing(12)

        self._feature_index = 0
        # v5.23: the pending brain choice — None until a card is
        # clicked; persisted only at Finish (see _persist_brain_choice).
        self.brain_choice: str | None = None
        self.choice_cards: dict[str, _ChoiceCard] = {}
        self._card_markers: dict[str, QLabel] = {}
        self._downloads_kicked = False
        # Round-2 N4 FAIL 2: whether the empty-key warning has
        # been shown on this visit to the key page (see
        # FlowMixin._advance_from_key_page).
        self._key_no_key_warned = False
        # v5.49 (programme item 34 completion): the pending tool-
        # profile choice — starts at the configured profile (a
        # fresh install's new default, "desktop"), persisted only
        # at Finish (see _persist_profile_choice). Skip records
        # nothing, so a skipped first run keeps the desktop
        # default exactly as shipped.
        self.profile_choice: str = self._current_profile()
        self.profile_cards: dict[str, _ChoiceCard] = {}
        self._profile_markers: dict[str, QLabel] = {}
        # Phase G: resumable progress + the advanced detour.
        self._progress = FirstRunProgress(
            _QSettingsStore(qsettings_store()))
        self._detour = False
        self._return_page = None
        # Rethink Build 6 (§6.9): grants made during THIS walk,
        # pid -> "durable" | "session". The permissions page reads
        # it for Take back; a grant from before this walk (a
        # resumed wizard) is never in here, so its row never
        # offers to take it back.
        self._walk_grants: dict[str, str] = {}
        # D12 safe default: a fresh install (no stored sounds key)
        # starts silent until the permissions stage opts in. An
        # existing install's stored value is never touched here.
        try:
            if not qsettings_contains("sounds_enabled"):
                qsettings_set("sounds_enabled", False)
        except Exception:  # noqa: BLE001 — the checkbox still asks
            pass

        # Pages are ALL built once, in flow order; navigation walks
        # the applicable subset for the pending choice (the key page
        # applies only to cloud/both, Setting up only to local/both
        # with an incomplete pack). _sync_nav/_next/_back key off
        # widget identity, never raw indexes.
        self.pages = QStackedWidget()
        self._welcome_page = self._page_welcome()
        self.pages.addWidget(self._welcome_page)
        self._mic_page = self._page_mic()
        self.pages.addWidget(self._mic_page)
        self._choice_page = self._page_choice()
        self.pages.addWidget(self._choice_page)
        # v5.49 (programme item 34 completion): the tool-profile
        # page rides with the advanced detour in Phase G — the
        # shipped desktop default stands unless the user opens it.
        self._profile_page = self._page_profile()
        self.pages.addWidget(self._profile_page)
        self._key_page = self._page_api_key()
        self.pages.addWidget(self._key_page)
        # The Setting up page exists only while the pack is
        # incomplete (pure pack state — the choice hasn't persisted
        # yet at build time, so no engine check belongs here).
        self._setup_page = None
        if self._pack_incomplete():
            self._setup_page = self._page_setup()
            self.pages.addWidget(self._setup_page)
        self._voice_page = self._page_voice()
        self.pages.addWidget(self._voice_page)
        self._language_page = self._page_language()
        self.pages.addWidget(self._language_page)
        # First-run onboarding (2026-11-16): the ask sequence —
        # browser, camera, screen, files — sits between the
        # language page and the permissions (sounds) page, in the
        # scope §2 order (Mic → Voice → Cloud → Browser → Camera
        # → Screen → Files → Sounds → screen-watching last).
        self._browser_page = self._page_browser()
        self.pages.addWidget(self._browser_page)
        self._camera_page = self._page_camera()
        self.pages.addWidget(self._camera_page)
        self._screen_page = self._page_screen()
        self.pages.addWidget(self._screen_page)
        self._files_page = self._page_files()
        self.pages.addWidget(self._files_page)
        self._permissions_page = self._page_permissions()
        self.pages.addWidget(self._permissions_page)
        self._selftest_page = self._page_selftest()
        self.pages.addWidget(self._selftest_page)
        self._done_page = self._page_done()
        self.pages.addWidget(self._done_page)
        self._page_order = [self.pages.widget(i)
                            for i in range(self.pages.count())]
        # Settings redesign §4a: the key page's Test button
        # reports its verdict here (see _on_ob_key_test_completed
        # in onboarding_pages).
        self.key_test_completed.connect(self._on_ob_key_test_completed)
        # Phase G: the INITIALIZING step rail sits above the pages
        # — the four required stages with their state, plus the
        # current stage's title. It replaces the old dot row.
        self._rail_head = QLabel("INITIALIZING")
        self._rail_head.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: {TYPE_MICRO}px;"
            " letter-spacing: 2px; background: transparent; border: none;")
        root.addWidget(self._rail_head)
        self._rail_row = QHBoxLayout()
        self._rail_row.setSpacing(8)
        self._rail_labels: dict[str, QLabel] = {}
        for stage in FIRST_RUN_STAGES:
            lbl = QLabel(STAGE_TITLES[stage])
            lbl.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: {TYPE_CAPTION}px;"
                " background: transparent; border: none;")
            self._rail_row.addWidget(lbl)
            self._rail_labels[stage] = lbl
        self._rail_row.addStretch()
        root.addLayout(self._rail_row)
        # UX-safe batch: pages scroll inside the dialog (the rail
        # and nav row below stay fixed, so Back/Continue never
        # scroll away). The stacked pages themselves are untouched.
        self._pages_scroll = QScrollArea()
        self._pages_scroll.setWidgetResizable(True)
        self._pages_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._pages_scroll.setWidget(self.pages)
        # Phase G: QScrollArea.setWidget forces autoFillBackground
        # True on its widget, so the stacked pages would paint
        # their palette Window (light) and wash out the dialog's
        # light text. Reset it AFTER setWidget, so the dialog's
        # dark surface shows through the pages.
        self.pages.setAutoFillBackground(False)
        root.addWidget(self._pages_scroll, stretch=1)

        # Field-check #10: Skip keeps the answers already given
        # (ask taps write on tap) — the hint must say so, not
        # imply nothing was recorded. The brain, language, voice
        # and profile picks stage until Finish, so they are the
        # exception the hint names. Choice-only has no ask pages,
        # so its hint says nothing is kept.
        if self.choice_only:
            nav_hint = QLabel(
                "Every step is optional — Not now exits without "
                "saving your brain choice. Only the brain choice "
                "gates Continue.")
        else:
            nav_hint = QLabel(
                "Every step is optional — Continue moves ahead. "
                "Skip setup exits and keeps the answers you already "
                "gave; your brain, language, voice and profile "
                "picks are only saved when you Finish. Only the "
                "brain choice gates Continue.")
        nav_hint.setObjectName("muted")
        nav_hint.setWordWrap(True)
        root.addWidget(nav_hint)

        nav = QHBoxLayout()
        self.btn_skip = QPushButton("Skip setup")
        self.btn_skip.setObjectName("chipBtn")
        self.btn_skip.clicked.connect(self.reject)
        self.btn_back = QPushButton("Back")
        self.btn_back.setObjectName("chipBtn")
        self.btn_back.clicked.connect(self._back)
        self.btn_next = QPushButton("Continue")
        self.btn_next.setObjectName("primaryBtn")
        self.btn_next.setMinimumHeight(34)  # v5.54 (DPI rule): floor, not a freeze — the label may grow with font metrics
        self.btn_next.clicked.connect(self._next)
        nav.addWidget(self.btn_skip)
        nav.addStretch()
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)
        root.addLayout(nav)

        # Rethink Build 6 (§6.9 / §3.9): the bottom row loses
        # "Advanced setup" — it duplicates the choices. It becomes
        # a quiet link under the buttons, same detour, same button
        # object (its text still toggles to "Back to setup" while
        # the detour is open).
        self.btn_advanced = QPushButton("Advanced setup")
        self.btn_advanced.setStyleSheet(
            "background: transparent; border: none; "
            "text-align: left; padding: 2px 0px; "
            "text-decoration: underline; "
            "color: %s;" % TEXT_MUTED)
        self.btn_advanced.clicked.connect(self._toggle_advanced)
        adv_row = QHBoxLayout()
        adv_row.setContentsMargins(0, 0, 0, 0)
        adv_row.addWidget(self.btn_advanced)
        adv_row.addStretch()
        root.addLayout(adv_row)

        self._mic_timer = QTimer(self)
        self._mic_timer.timeout.connect(self._update_mic_level)
        self._setup_timer = QTimer(self)
        self._setup_timer.timeout.connect(self._refresh_setup_page)
        self._feature_timer = QTimer(self)
        self._feature_timer.setInterval(4000)
        self._feature_timer.timeout.connect(self._advance_feature_card)
        if self.choice_only:
            self.btn_skip.setText("Not now")
            self.btn_advanced.setVisible(False)
            self.pages.setCurrentWidget(self._choice_page)
            if self._initial_choice in ("local", "cloud", "both"):
                # Reopening the choice shows the current one picked.
                self._select_choice(self._initial_choice)
        elif self._progress.resume_stage() not in (None, "interaction"):
            # Resumable: a reopened flow jumps to the first
            # unfinished stage instead of restarting.
            target = self._first_page_of_stage(
                self._progress.resume_stage())
            if target is not None:
                self.pages.setCurrentWidget(target)
        self._sync_nav()
    def _title_block(self, title: str, body: str) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        b = QLabel(body)
        b.setObjectName("muted")
        b.setWordWrap(True)
        layout.addWidget(t)
        layout.addWidget(b)
        return box
    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        radios = getattr(self, "_brain_row_radios", None)
        if radios and event.type() == QEvent.Type.MouseButtonPress:
            rb = radios.get(obj)
            if rb is not None:
                rb.setChecked(True)
                return True
        return super().eventFilter(obj, event)
    def selected_choice(self) -> str | None:
        """The pending brain choice: "local" | "cloud" | "both",
        or None until the user clicks a card."""
        return self.brain_choice
    def _select_choice(self, choice: str):
        self.brain_choice = choice
        for name, card in self.choice_cards.items():
            card.set_selected(name == choice)
            marker = self._card_markers.get(name)
            if marker is not None:
                marker.setVisible(name == choice)
        self._sync_nav()
    @staticmethod
    def _pack_size_text() -> str:
        """"about 3.3 GB" for this PC's tier (brain tier + Whisper
        small ~0.5 GB + Kokoro ~0.34 GB, mirroring _setup_body_text),
        guarded — the card degrades to "a few GB", never breaks."""
        try:
            from voice_assistant.core.gui_facade import local_assets

            eff = local_assets.effective_model()
            tier = local_assets.MODEL_TIERS.get(eff.get("tier") or "")
            if tier:
                return f"about {float(tier['gb']) + 0.84:.1f} GB"
        except Exception:  # noqa: BLE001 — the size is a nicety
            pass
        return "a few GB"
    @staticmethod
    def _current_profile() -> str:
        """The configured tool profile, normalised by
        core/funnel/tool_profiles (an unknown stored value resolves to
        the default — "desktop" for a fresh install)."""
        try:
            from voice_assistant.core.gui_facade import (
                current_profile_name,
            )

            return current_profile_name(config)
        except Exception:  # noqa: BLE001 — onboarding never breaks
            return "desktop"
    def selected_profile(self) -> str:
        """The pending tool-profile choice (v5.49, programme
        item 34 completion): "desktop" | "assistant" | "full"."""
        return self.profile_choice
    def _select_profile(self, profile: str):
        self.profile_choice = profile
        for name, card in self.profile_cards.items():
            card.set_selected(name == profile)
            marker = self._profile_markers.get(name)
            if marker is not None:
                marker.setVisible(name == profile)

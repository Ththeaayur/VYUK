# Refactor Phase 1 step 5: ShellMixin panel mixin, moved verbatim from gui/window_shell.py.
from .style import (
    ACCENT,
    AMBER,
    BG,
    DANGER,
    LINE_SUBTLE,
    PREMIUM_STYLE,
    SURFACE_2,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
    TYPE_CARD_TITLE,
    TYPE_TITLE,
    RADIUS_BTN,
    load_bundled_fonts,
)
from .window_widgets import (
    _ASSETS,
    BridgeSignals,
    BackendWorker,
    AppShell,
    TitleBar,
    WaveformWidget,
    MiniEq,
    ThinkingDots,
    _ActivitySparkline,
    _ComposerEdit,
    ChatBubble,
    CommandPalette,
    SplashIgnition,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import (
    QEvent,
    QRectF,
    QSettings,
    QSize,
    QThread,
    Qt,
    QTimer,
    Signal,
)
from voice_assistant.core.gui_facade import config
from .icons import make_icon
from .design_system.palette import parse_color, with_alpha


class ShellSidebarMixin:


    def _build_ui(self):
        self.shell = AppShell()
        self.setCentralWidget(self.shell)
        # v5.24 (E32): the saved translucent-shell preference
        # applies from the first frame.
        self._apply_saved_glass()
        root = QVBoxLayout(self.shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_titlebar())

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        content.addWidget(self._build_sidebar())
        # v3.2: the page column carries the setup banner above the
        # page stack — a slim strip, visible on every page while the
        # offline pack is still landing, narrating it with real
        # numbers from local_assets.pack_progress().
        page_col = QVBoxLayout()
        page_col.setContentsMargins(0, 0, 0, 0)
        page_col.setSpacing(0)
        page_col.addWidget(self._build_setup_banner())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_assistant_page())
        self.pages.addWidget(self._build_activity_page())
        self.pages.addWidget(self._build_memory_page())
        self.pages.addWidget(self._build_skills_page())
        self.pages.addWidget(self._build_settings_page())
        self.pages.addWidget(self._build_pulse_page())
        self.pages.addWidget(self._build_senses_page())
        self.pages.addWidget(self._build_doctor_page())
        # Stage 0 features batch 1 (Group D): the views — Calendar,
        # Inbox, Contacts, Calls. Stack indices 0–7 are FROZEN;
        # these take 8–11, in that order.
        self.pages.addWidget(self._build_calendar_page())
        self.pages.addWidget(self._build_inbox_page())
        self.pages.addWidget(self._build_contacts_page())
        self.pages.addWidget(self._build_calls_page())
        # v5.57 (redo): the home voice chip can read the Settings
        # combos now that every page is built.
        try:
            self._ensure_voice_chip_wiring()
        except Exception:  # noqa: BLE001 — chip refreshes on home show
            pass
        page_col.addWidget(self.pages, stretch=1)
        content.addLayout(page_col, stretch=1)
        root.addLayout(content, stretch=1)

        # UI rethink Build 3 (§6.11): the status strip is the docked
        # bottom row (32 px, one hairline above it) — one dot + one
        # word, below all pages. The strip is built here, once, as a
        # child of the shell (see _build_docked_status_strip);
        # hiding it collapses the row, never the pages.
        root.addWidget(self._build_docked_status_strip())

        self._grip = QSizeGrip(self.shell)
        self._grip.setFixedSize(16, 16)
        self._grip.setStyleSheet("background: transparent;")
        self._apply_main_tab_order()


    def _apply_main_tab_order(self):
        """UI a11y foundations: one logical Tab chain for the
        persistent chrome — sidebar nav in visual order, then
        stage controls, then chat bubbles (wired dynamically in
        window_chat._add_bubble, slotting in before the
        composer), then composer and dock, then header, then
        titlebar. Construction order alone would wander (the
        titlebar is built first, the composer last); this chain
        follows the visual/logical order instead. Missing
        optional widgets are skipped, never fatal."""
        chain = []
        chain.extend(getattr(self, "nav_buttons", []))
        for attr in ("voice_chip", "btn_briefing", "btn_goal_stop",
                     "btn_speech_pause", "btn_speech_skip"):
            widget = getattr(self, attr, None)
            if widget is not None:
                chain.append(widget)
        pace = getattr(self, "pace_buttons", {})
        for value in (0.9, 1.0, 1.1):
            widget = pace.get(value) if isinstance(pace, dict) else None
            if widget is not None:
                chain.append(widget)
        # The chat anchor: the first bubble chains after the
        # last stage widget, and every bubble chains on to the
        # composer (see window_chat._add_bubble).
        self._chat_tab_anchor = chain[-1] if chain else None
        for attr in ("input_cmd", "btn_send", "btn_mic", "btn_screen",
                     "btn_find_prev", "btn_find_next", "btn_find_close",
                     "_btn_new_msgs", "btn_header_overflow",
                     "btn_connect", "btn_cmd_pill", "btn_win_min",
                     "btn_win_max", "btn_win_close"):
            widget = getattr(self, attr, None)
            if widget is not None:
                chain.append(widget)
        # UI rethink Build 3: the docked status strip is the window's
        # bottom row — its dot + word close the chain (visually last).
        strip = getattr(self, "vitals_strip", None)
        if strip is not None:
            chain.append(strip.dot_button)
            chain.append(strip.word_button)
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)


    def _build_setup_banner(self) -> QFrame:
        """The v3.2 setup banner: a slim strip above the page stack.

        Shown only while the local brain's offline pack is incomplete
        (see _refresh_setup_banner) — first-run setup used to be
        invisible until a Connect-time status line, and its scraped
        percentage could claim "100%" for a brain that hadn't landed.
        The banner instead reads local_assets.pack_progress() and
        names the stage in consumer words: her ears (speech
        recognition), her voice, her brain — with real byte counts.
        """
        banner = QFrame()
        banner.setObjectName("setupBanner")
        # Command Deck Phase D: the banner's violet wash was a
        # leftover of the retired palette — the strip is now the
        # resting surface with a cyan hairline, and its progress
        # chunk is the accent. The percent itself is untouched:
        # it still comes from local_assets.pack_progress() (real
        # byte counts, see _refresh_setup_banner).
        banner.setStyleSheet(
            f"QFrame#setupBanner {{ background: {SURFACE_2};"
            f" border-bottom: 1px solid {LINE_SUBTLE}; }}")
        row = QHBoxLayout(banner)
        row.setContentsMargins(14, 5, 10, 5)
        row.setSpacing(10)
        self.setup_banner_label = QLabel("Setting VYUK up…")
        # Golden-fix D1: at narrow widths / large scales the banner
        # row (label + fixed progress + Pause) exceeds the page
        # column and the label clipped mid-word. Wrapping is the
        # overflow handling: one line at normal widths, two lines
        # (taller banner, same content) when squeezed.
        self.setup_banner_label.setWordWrap(True)
        row.addWidget(self.setup_banner_label)
        self.setup_banner_bar = QProgressBar()
        self.setup_banner_bar.setRange(0, 100)
        self.setup_banner_bar.setValue(0)
        self.setup_banner_bar.setTextVisible(False)
        self.setup_banner_bar.setFixedSize(170, 4)
        self.setup_banner_bar.setStyleSheet(
            f"QProgressBar {{ background: {with_alpha(TEXT, 24)}; border: none;"
            " border-radius: 2px; }"
            f"QProgressBar::chunk {{ background: {ACCENT};"
            " border-radius: 2px; }")
        row.addWidget(self.setup_banner_bar)
        row.addStretch()
        self.btn_setup_banner = QPushButton("Pause")
        self.btn_setup_banner.setObjectName("chipBtn")
        self.btn_setup_banner.clicked.connect(self._on_setup_banner_button)
        row.addWidget(self.btn_setup_banner)
        banner.setVisible(False)
        self.setup_banner = banner
        self._banner_was_incomplete = False
        self._banner_done_until = 0.0
        return banner


    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        # Golden-fix D5: the sidebar column's natural height
        # (~590 px at 120% scale) exceeds short windows, and with
        # no scroll the VBox crushed every row below its minimum
        # — nav labels mangled ("Activitv"), wake button clipped,
        # privacy card squeezed to an empty frame (title/body at
        # 0 px). The chrome (220 px frame, background, border)
        # is unchanged; the column content now lives in a
        # vertical scroll area inside the frame, so at short
        # heights the footer scrolls into view instead of
        # crushing. At normal heights no bar shows and the
        # stretch below the nav keeps the footer pinned down.
        shell_layout = QVBoxLayout(sidebar)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        sidebar_scroll.setWidget(inner)
        # setWidget() flips autoFillBackground on (see D6) — the
        # inner column must stay transparent over the sidebar
        # frame's own background.
        inner.setAutoFillBackground(False)
        sidebar_scroll.viewport().setAutoFillBackground(False)
        shell_layout.addWidget(sidebar_scroll)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)

        brand = QHBoxLayout()
        mark = QLabel("V")
        mark.setObjectName("brandMark")
        mark.setFixedSize(30, 30)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel(config.assistant_name)
        name.setObjectName("brandName")
        brand.addWidget(mark)
        brand.addWidget(name)
        brand.addStretch()
        layout.addLayout(brand)
        layout.addSpacing(24)

        # Chat pass (2026-11-17, owner ruling: the sidebar is
        # v6.3 exactly): the section labels are back and the
        # Skills button is out — the block below is v6.3 verbatim.
        # UI Slice 1 (2026-10-30, complexity doc CX-01,
        # owner-approved): primary navigation is Home / Activity
        # / Memory; Settings lives in the footer below the
        # stretch; System Pulse, Senses, and Doctor live
        # under a "SYSTEM" section (restore 2026-11-01: the
        # §9.2 CORE / MISSION / ARCHIVE / SYSTEM labels, inside
        # this same 220 px sidebar — see below). Stack indices
        # NEVER change (0 Home, 1 Activity, 2 Memory, 3 Skills,
        # 4 Settings, 5 System Pulse, 6 Senses, 7 Doctor) —
        # _show_page keeps stack-index semantics and
        # the checked state is computed through
        # _nav_page_indices (see window_chat._show_page).
        # 11 nav buttons, 12 pages: Skills (stack 3) has no sidebar
        # button — it is reached from the command palette
        # (Ctrl+K, "Go to Skills"), which already listed it
        # before S1. Internal names are NOT renamed (CX-06
        # renames are held for user testing); they only move
        # under Advanced. Doctor's button is new in S1 (it was
        # buttonless since the v5.57 owner override); its
        # Settings-card, tray, and palette routes are unchanged.
        # No doctor glyph exists in gui/icons.py, so Doctor uses
        # "check" — the closest existing glyph (Doctor runs
        # health checks). The four views (Stage 0 features batch
        # 1) use the closest existing glyphs the same way: a grid
        # for the Calendar month, "send" for Inbox mail, a square
        # card for Contacts, "mic" for Calls (voice calls).
        # INTENTIONAL (world-view removal, 2026-11-05): the Globe
        # button is gone, so its "globe" icon leaves this list with
        # it; the icon template itself stays in gui/icons.py as part
        # of the inline template set (the honest fallback for names
        # the Phosphor bundle does not cover — B-R1-6).
        self._nav_icon_names = ["spark", "clock", "database",
                                "pulse", "senses", "check",
                                "grid", "send", "square", "mic",
                                "sliders"]
        self._nav_page_indices = [0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 4]
        self.nav_buttons: list[QPushButton] = []
        # (label, icon, stack page) in VISUAL order: the primary
        # three, the Advanced four, the Views four, then the
        # footer Settings.
        # _nav_page_indices above is aligned to this same order.
        _nav_defs = [("Home", "spark", 0),
                     ("Activity", "clock", 1),
                     ("Memory", "database", 2),
                     ("System Pulse", "pulse", 5),
                     ("Senses", "senses", 6),
                     ("Doctor", "check", 7),
                     ("Calendar", "grid", 8),
                     ("Inbox", "send", 9),
                     ("Contacts", "square", 10),
                     ("Calls", "mic", 11),
                     ("Settings", "sliders", 4)]
        for label, icon, page in _nav_defs:
            btn = QPushButton(label)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            # The label is the accessible name by derivation;
            # the description states the action for screen
            # readers (UI a11y foundations).
            btn.setAccessibleDescription(f"Show the {label} page")
            btn.setIcon(make_icon(icon, TEXT_MUTED))
            btn.setIconSize(QSize(18, 18))
            btn.clicked.connect(lambda _=False, idx=page: self._show_page(idx))
            self.nav_buttons.append(btn)
        # Restore (2026-11-01, owner-delegated): the §9.2
        # section labels inside the EXISTING 220 px sidebar —
        # labels only. Same buttons, same order, same stack
        # indices, same navigation behaviour. CORE groups Home,
        # MISSION groups Activity, ARCHIVE groups Memory, SYSTEM
        # groups System Pulse / Senses / Doctor
        # (replacing the "Advanced / Explore" label). Stage 0
        # features batch 1 adds VIEWS for Calendar / Inbox /
        # Contacts / Calls (stack 8–11). There is deliberately
        # NO CAPABILITIES label: Skills has no sidebar button
        # (S1 pin: palette only), so a CAPABILITIES label would
        # group nothing, and adding a button would break the
        # labels-only scope.
        _core_label = QLabel("CORE")
        _core_label.setObjectName("muted")
        layout.addWidget(_core_label)
        layout.addWidget(self.nav_buttons[0])
        layout.addSpacing(12)
        _mission_label = QLabel("MISSION")
        _mission_label.setObjectName("muted")
        layout.addWidget(_mission_label)
        layout.addWidget(self.nav_buttons[1])
        layout.addSpacing(12)
        _archive_label = QLabel("ARCHIVE")
        _archive_label.setObjectName("muted")
        layout.addWidget(_archive_label)
        layout.addWidget(self.nav_buttons[2])
        layout.addSpacing(12)
        _system_label = QLabel("SYSTEM")
        _system_label.setObjectName("muted")
        layout.addWidget(_system_label)
        for btn in self.nav_buttons[3:6]:
            layout.addWidget(btn)
        layout.addSpacing(12)
        # Stage 0 features batch 1 (Group D): the VIEWS section
        # for Calendar / Inbox / Contacts / Calls (stack 8–11).
        _views_label = QLabel("VIEWS")
        _views_label.setObjectName("muted")
        layout.addWidget(_views_label)
        for btn in self.nav_buttons[6:10]:
            layout.addWidget(btn)
        layout.addSpacing(12)
        self.nav_buttons[0].setChecked(True)
        self._refresh_nav_icons(0)

        layout.addStretch()
        # Footer: Settings lives here, not in the primary list.
        layout.addWidget(self.nav_buttons[10])

        wake_label = QLabel("Wake word")
        wake_label.setObjectName("muted")
        layout.addWidget(wake_label)
        self.btn_wake = QPushButton("Off — click to enable")
        self.btn_wake.setAccessibleDescription(
            "Turn the wake word on or off")
        self.btn_wake.setObjectName("chipBtn")
        self.btn_wake.setCheckable(True)
        self.btn_wake.clicked.connect(self._toggle_wake_word)
        layout.addWidget(self.btn_wake)
        layout.addSpacing(12)

        # Chat pass (2026-11-17, owner ruling): the v6.3
        # privacy card is back — the one muted line was the
        # sci-fi Home change, and the sidebar is v6.3 exactly.
        privacy = QFrame()
        privacy.setObjectName("privacyCard")
        p_layout = QVBoxLayout(privacy)
        p_layout.setContentsMargins(12, 12, 12, 12)
        p_layout.setSpacing(4)
        p_title = QLabel("Private by design")
        p_title.setObjectName("privacyTitle")
        # L-31 (loophole batch 8): the promise sentence lives in
        # core/privacy_copy.py (PRIVACY_PROMISE), shared with the
        # Settings privacy promise so the two cannot drift apart.
        # "Memory stays on this device" hid that a cloud turn
        # sends the facts she needs to answer to the cloud brain.
        from voice_assistant.core.gui_facade import privacy_copy
        p_body = QLabel(privacy_copy.PRIVACY_PROMISE)
        p_body.setObjectName("privacyBody")
        p_body.setWordWrap(True)
        p_layout.addWidget(p_title)
        p_layout.addWidget(p_body)
        layout.addWidget(privacy)
        return sidebar


    def _refresh_nav_icons(self, active: int):
        # `active` is a STACK page index. Nav buttons map through
        # _nav_page_indices (S1 order: Home 0, Activity 1, Memory
        # 2, System Pulse 5, Senses 6, Doctor 7, Calendar 8,
        # Inbox 9, Contacts 10, Calls 11, Settings 4 — visual
        # order, NOT stack order), so the highlighted button is
        # the one whose page matches, not the button at position
        # `active`. Skills (stack 3, no button) still highlights
        # nothing, as Doctor did before S1.
        _pages = getattr(self, "_nav_page_indices", None)
        for i, btn in enumerate(self.nav_buttons):
            _page = _pages[i] if _pages and i < len(_pages) else i
            color = TEXT if _page == active else TEXT_MUTED
            btn.setIcon(make_icon(self._nav_icon_names[i], color))



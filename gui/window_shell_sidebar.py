# Refactor Phase 1 step 5: ShellMixin panel mixin, moved verbatim from gui/window_shell.py.
from .breakpoints import SIDEBAR_WIDTH, SIDEBAR_WIDTH_COMPACT
from .style import (
    AMBER,
    BG,
    DANGER,
    PREMIUM_STYLE,
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
from PySide6.QtGui import QIcon
from voice_assistant.core.gui_facade import config
from .icons import make_icon
from .design_system.palette import parse_color


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
        # UI Phase 5 (2026-11-27): Notes / Finance / Devices take
        # stack 12–14, in that order (stack 4 is Settings, built
        # above at index 4).
        self.pages.addWidget(self._build_calendar_page())
        self.pages.addWidget(self._build_inbox_page())
        self.pages.addWidget(self._build_contacts_page())
        self.pages.addWidget(self._build_calls_page())
        self.pages.addWidget(self._build_notes_page())
        self.pages.addWidget(self._build_finance_page())
        self.pages.addWidget(self._build_devices_page())
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
        # Stage 0 features batch 5 (Light theme, render-found):
        # the strip + bar used to carry scoped sheets with the
        # house tokens, so on Light the strip stayed black with
        # theme-dark text — unreadable. Both live in the window
        # sheet now (QFrame#setupBanner / QProgressBar#setupBar
        # in style_sheet.py) and follow the theme; no sheet here.
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
        self.setup_banner_bar.setObjectName("setupBar")
        self.setup_banner_bar.setRange(0, 100)
        self.setup_banner_bar.setValue(0)
        self.setup_banner_bar.setTextVisible(False)
        self.setup_banner_bar.setFixedSize(170, 4)
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
        # Stage 0 features batch 5 (narrow window): below 908 px
        # the sidebar collapses to a 64 px icon rail
        # (_apply_sidebar_compact). The rail needs handles on the
        # text-bearing chrome, so the build keeps them: section
        # labels, brand name, wake label, privacy card, the inner
        # layout's margins, and the nav labels for restore.
        self._sidebar_compact = False
        self._sidebar_section_labels: list = []
        self._wake_full_text = "Off — click to enable"
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
        # UI Phase 5: fifteen buttons overflow the sidebar at
        # short heights — _show_page scrolls the checked button
        # into view through this area, so the selected workspace
        # is never half-cut below the fold.
        self._sidebar_scroll = sidebar_scroll
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
        self._sidebar_inner_layout = layout

        brand = QHBoxLayout()
        mark = QLabel("V")
        mark.setObjectName("brandMark")
        mark.setFixedSize(30, 30)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel(config.assistant_name)
        name.setObjectName("brandName")
        self._sidebar_brand_name = name
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
        # 4 Settings, 5 System Pulse, 6 Senses, 7 Doctor,
        # 8 Calendar, 9 Inbox, 10 Contacts, 11 Calls, 12 Notes,
        # 13 Finance, 14 Devices) —
        # _show_page keeps stack-index semantics and
        # the checked state is computed through
        # _nav_page_indices (see window_chat._show_page).
        # 15 nav buttons, 15 pages: Skills (stack 3) has its
        # own button under CAPABILITIES since UI Phase 2
        # (before that it was palette-only, S1 pin).
        # UI Phase 5 (2026-11-27): Notes / Finance / Devices join
        # as workspace buttons under WORKSPACES (stack 12–14);
        # Settings stays the footer button, last in the list.
        # Internal names are NOT renamed (CX-06
        # renames are held for user testing); they only move
        # under Advanced. Doctor's button is new in S1 (it was
        # buttonless since the v5.57 owner override); its
        # Settings-card, tray, and palette routes are unchanged.
        # The four views (Stage 0 features batch
        # 1) use the closest existing glyphs the same way: a grid
        # for the Calendar month, "send" for Inbox mail, an
        # address book for Contacts, a phone handset for Calls
        # (voice calls). INTENTIONAL (world-view removal,
        # 2026-11-05): the Globe button is gone, so its "globe"
        # icon leaves this list with it; the icon template
        # itself stays in gui/icons.py as part of the inline
        # template set (the honest fallback for names the
        # Phosphor bundle does not cover — B-R1-6).
        # UI Phase 2 (2026-11-24): Doctor takes
        # the Phosphor "heartbeat" (a pulse line in a heart —
        # the old "check" inline template read as a tick, not a
        # clinic), Contacts takes "address-book" (the old
        # "square" read as a blank card), Calls takes
        # "phone-call" (the old "mic" duplicated the wake
        # button's mic), and Skills takes "shapes" (the
        # catalogue mark). All four are Phosphor Duotone SVGs
        # in the bundle (same pinned commit, same MIT —
        # see gui/assets/icons/phosphor/BUNDLE_RECORD.md).
        self._nav_icon_names = ["spark", "clock", "database",
                                "shapes", "pulse", "senses",
                                "heartbeat", "grid", "send",
                                "address-book", "phone-call",
                                "note", "currency-dollar", "house",
                                "sliders"]
        self._nav_page_indices = [0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 4]
        self.nav_buttons: list[QPushButton] = []
        # (label, icon, stack page) in VISUAL order: the primary
        # three, the Capabilities one, the Advanced four, the
        # Views four, then the footer Settings.
        # _nav_page_indices above is aligned to this same order.
        _nav_defs = [("Home", "spark", 0),
                     ("Activity", "clock", 1),
                     ("Memory", "database", 2),
                     ("Skills", "shapes", 3),
                     ("System Pulse", "pulse", 5),
                     ("Senses", "senses", 6),
                     ("Doctor", "heartbeat", 7),
                     ("Calendar", "grid", 8),
                     ("Inbox", "send", 9),
                     ("Contacts", "address-book", 10),
                     ("Calls", "phone-call", 11),
                     ("Notes", "note", 12),
                     ("Finance", "currency-dollar", 13),
                     ("Devices", "house", 14),
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
        # UI Phase 2 (D-P2-11): the two sidebar badges — Activity
        # (pending approvals) and Inbox (unread mail). Created
        # here as children of their buttons (nav_buttons[1] and
        # [8] in the visual order above); refreshed by
        # _refresh_sidebar_badges on every _show_page. The pills
        # are display-only — tapping the button navigates.
        from .sidebar_badges import make_badge

        self._badge_activity = make_badge(self.nav_buttons[1])
        self._badge_inbox = make_badge(self.nav_buttons[8])
        self._nav_labels = [label for label, _, _ in _nav_defs]
        # Restore (2026-11-01, owner-delegated): the §9.2
        # section labels inside the EXISTING 220 px sidebar —
        # labels only. Same buttons, same order, same stack
        # indices, same navigation behaviour. CORE groups Home,
        # MISSION groups Activity, ARCHIVE groups Memory, SYSTEM
        # groups System Pulse / Senses / Doctor
        # (replacing the "Advanced / Explore" label). Stage 0
        # features batch 1 adds VIEWS for Calendar / Inbox /
        # Contacts / Calls (stack 8–11). UI Phase 2 adds
        # CAPABILITIES for Skills (stack 3) — the Centre's
        # button, after Memory.
        _core_label = QLabel("CORE")
        _core_label.setObjectName("muted")
        self._sidebar_section_labels.append(_core_label)
        layout.addWidget(_core_label)
        layout.addWidget(self.nav_buttons[0])
        layout.addSpacing(12)
        _mission_label = QLabel("MISSION")
        _mission_label.setObjectName("muted")
        self._sidebar_section_labels.append(_mission_label)
        layout.addWidget(_mission_label)
        layout.addWidget(self.nav_buttons[1])
        layout.addSpacing(12)
        _archive_label = QLabel("ARCHIVE")
        _archive_label.setObjectName("muted")
        self._sidebar_section_labels.append(_archive_label)
        layout.addWidget(_archive_label)
        layout.addWidget(self.nav_buttons[2])
        layout.addSpacing(12)
        _capabilities_label = QLabel("CAPABILITIES")
        _capabilities_label.setObjectName("muted")
        self._sidebar_section_labels.append(_capabilities_label)
        layout.addWidget(_capabilities_label)
        layout.addWidget(self.nav_buttons[3])
        layout.addSpacing(12)
        _system_label = QLabel("SYSTEM")
        _system_label.setObjectName("muted")
        self._sidebar_section_labels.append(_system_label)
        layout.addWidget(_system_label)
        for btn in self.nav_buttons[4:7]:
            layout.addWidget(btn)
        layout.addSpacing(12)
        # UI Phase 3 (D-P3-12): the workspace buttons
        # (Calendar / Inbox / Contacts / Calls, stack 8–11, plus
        # Notes / Finance / Devices, stack 12–14, since UI Phase 5)
        # sit under a WORKSPACES section at the VIEWS position. The
        # VIEWS label is retired — the section is empty otherwise,
        # and an empty section label is a lie. Relative order and
        # stack indices are unchanged.
        _views_label = QLabel("WORKSPACES")
        _views_label.setObjectName("muted")
        self._sidebar_section_labels.append(_views_label)
        layout.addWidget(_views_label)
        for btn in self.nav_buttons[7:14]:
            layout.addWidget(btn)
        # UI Phase 5: the three new workspaces carry a fuller
        # accessible description (what the page holds), not just
        # its name — the loop above sets "Show the X page".
        self.nav_buttons[11].setAccessibleDescription(
            "Show the Notes page — your notes, on this machine")
        self.nav_buttons[12].setAccessibleDescription(
            "Show the Finance page — money you record yourself")
        self.nav_buttons[13].setAccessibleDescription(
            "Show the Devices page — your home hub, on your network")
        layout.addSpacing(12)
        self.nav_buttons[0].setChecked(True)
        self._refresh_nav_icons(0)

        layout.addStretch()
        # Footer: Settings lives here, not in the primary list.
        layout.addWidget(self.nav_buttons[14])

        wake_label = QLabel("Wake word")
        wake_label.setObjectName("muted")
        self._sidebar_wake_label = wake_label
        layout.addWidget(wake_label)
        self.btn_wake = QPushButton("Off — click to enable")
        self.btn_wake.setAccessibleDescription(
            "Turn the wake word on or off")
        self.btn_wake.setObjectName("chipBtn")
        self.btn_wake.setCheckable(True)
        self.btn_wake.clicked.connect(self._toggle_wake_word)
        layout.addWidget(self.btn_wake)
        layout.addSpacing(12)

        # UI Phase 1 (Home plan §2): the sidebar privacy card is
        # ONE muted line — "Private by design" — at the foot of
        # the sidebar, the full promise on hover (the v6.3 title +
        # paragraph is the old form). The promise sentence still
        # lives in core/privacy_copy.py (PRIVACY_PROMISE), shared
        # with the Settings privacy promise so the two cannot
        # drift apart.
        privacy = QFrame()
        privacy.setObjectName("privacyCard")
        p_layout = QVBoxLayout(privacy)
        p_layout.setContentsMargins(12, 8, 12, 8)
        p_layout.setSpacing(0)
        from voice_assistant.core.gui_facade import privacy_copy
        p_title = QLabel("Private by design")
        p_title.setObjectName("privacyTitle")
        p_title.setToolTip(privacy_copy.PRIVACY_PROMISE)
        p_layout.addWidget(p_title)
        self._sidebar_privacy_card = privacy
        layout.addWidget(privacy)
        return sidebar

    def _apply_sidebar_compact(self, compact: bool) -> None:
        """Collapse / restore the sidebar icon rail (batch 5).

        Below 908 px the sidebar is a 64 px rail: nav buttons
        keep their 18 px icons with the labels hidden and the
        label as the tooltip (nothing unlabeled, nothing
        unreachable — the buttons, the wake toggle and Settings
        all stay one click away); section labels, the brand name,
        the wake label and the privacy card hide (the card is
        information only — the same promise lives in Settings);
        the wake button becomes an icon with its state text as
        the tooltip. Restoring re-applies every label from the
        stored names. Idempotent and safe before the sidebar
        exists (partial windows in tests); called from
        _apply_breakpoint_tier on every resize.
        """
        try:
            sidebar = self.findChild(QFrame, "sidebar")
            if sidebar is None:
                return
            self._sidebar_compact = bool(compact)
            sidebar.setFixedWidth(
                SIDEBAR_WIDTH_COMPACT if compact else SIDEBAR_WIDTH)
            labels = getattr(self, "_nav_labels", [])
            for i, btn in enumerate(getattr(self, "nav_buttons", [])):
                label = labels[i] if i < len(labels) else btn.text()
                if compact:
                    btn.setText("")
                    btn.setToolTip(label)
                else:
                    btn.setText(label)
                    btn.setToolTip("")
            for lbl in getattr(self, "_sidebar_section_labels", []):
                lbl.setVisible(not compact)
            brand = getattr(self, "_sidebar_brand_name", None)
            if brand is not None:
                brand.setVisible(not compact)
            wake_label = getattr(self, "_sidebar_wake_label", None)
            if wake_label is not None:
                wake_label.setVisible(not compact)
            wake = getattr(self, "btn_wake", None)
            if wake is not None:
                if compact:
                    full = getattr(self, "_wake_full_text", "") or wake.text()
                    if wake.text():
                        self._wake_full_text = wake.text()
                        full = wake.text()
                    # Look-fix 2026-11-24: the icon follows the
                    # resolved theme (the house TEXT_MUTED is
                    # near-white — invisible on Light).
                    _resolved = getattr(self, "_resolved_theme", None) or {}
                    _muted = (_resolved.get("text_muted", TEXT_MUTED)
                              if isinstance(_resolved, dict)
                              else TEXT_MUTED)
                    wake.setIcon(make_icon("mic", _muted))
                    wake.setIconSize(QSize(18, 18))
                    wake.setText("")
                    wake.setToolTip(full)
                else:
                    wake.setIcon(QIcon())
                    wake.setText(getattr(
                        self, "_wake_full_text",
                        "Off — click to enable"))
                    wake.setToolTip("")
            privacy = getattr(self, "_sidebar_privacy_card", None)
            if privacy is not None:
                privacy.setVisible(not compact)
            inner = getattr(self, "_sidebar_inner_layout", None)
            if inner is not None:
                if compact:
                    inner.setContentsMargins(8, 16, 8, 16)
                else:
                    inner.setContentsMargins(16, 16, 16, 16)
            # UI Phase 2: the badges ride their buttons, so a
            # tier change re-anchors them to the icon (full:
            # 12 px from the button's left; rail: centred).
            # Counts are not re-read here — only positions move.
            from .sidebar_badges import reposition_sidebar_badges

            reposition_sidebar_badges(self)
        except Exception:  # noqa: BLE001 — floors are a nicety
            pass

    def _refresh_nav_icons(self, active: int):
        # `active` is a STACK page index. Nav buttons map through
        # _nav_page_indices (visual order, NOT stack order), so
        # the highlighted button is the one whose page matches,
        # not the button at position `active`. Skills (stack 3)
        # highlights its CAPABILITIES button since UI Phase 2.
        # Look-fix 2026-11-24: the icon colours follow the
        # resolved theme (the house TEXT is near-white — the
        # active glyph was invisible on Light — and the house
        # TEXT_MUTED is pale). The theme pass calls this too, so
        # a theme change re-makes the icons with the page that
        # is currently active.
        _resolved = getattr(self, "_resolved_theme", None) or {}
        if isinstance(_resolved, dict):
            _text = _resolved.get("text", TEXT)
            _muted = _resolved.get("text_muted", TEXT_MUTED)
        else:
            _text, _muted = TEXT, TEXT_MUTED
        _pages = getattr(self, "_nav_page_indices", None)
        for i, btn in enumerate(self.nav_buttons):
            _page = _pages[i] if _pages and i < len(_pages) else i
            color = _text if _page == active else _muted
            btn.setIcon(make_icon(self._nav_icon_names[i], color))

    def _refresh_sidebar_badges(self):
        """Refresh both sidebar badges (UI Phase 2, D-P2-11).

        Delegates to gui/sidebar_badges: Activity from the ONE
        shared pending-approvals count, Inbox from the cached
        unread count. Called from _show_page (every page show)
        and after the count-changing events (an approval
        resolved, an Inbox refresh). Never raises — a failed
        read hides that badge, never the sidebar.
        """
        from .sidebar_badges import refresh_sidebar_badges

        refresh_sidebar_badges(self)



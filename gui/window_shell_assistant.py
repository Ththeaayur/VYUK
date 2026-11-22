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
    TYPE_BODY,
    TYPE_CARD_TITLE,
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
from .visualizer import (
    OrbVisualizer,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
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
    QMetaObject,
    QRectF,
    QSettings,
    QSize,
    QThread,
    Qt,
    QTimer,
    Signal,
    Slot,
)
import datetime
from .icons import make_icon
from .design_system.palette import parse_color, with_alpha
from .room_home import RoomLight
from .window_shell_assistant_widgets import _ChipFlowLayout, _ChipRow, _ChipScrollArea, _ChipStripWheelFilter, _HeroWidget, _DotsSplitterHandle, _DotsSplitter


class ShellAssistantMixin:


    def _build_assistant_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 16, 28, 20)
        layout.setSpacing(8)

        top = QHBoxLayout()
        greeting = QLabel(self._greeting())
        # Sci-fi Home: the greeting is the room's display line —
        # 20 in Orbitron (see QLabel#homeGreeting). It was the
        # shared pageTitle rule; the other pages keep that rule.
        greeting.setObjectName("homeGreeting")
        self.home_greeting = greeting
        top.addWidget(greeting)
        top.addStretch()
        # UIS2 (home simplification, CX-02): the header keeps one
        # quiet system status (glyph + pill) plus the connection
        # action; conversation actions live in an overflow menu
        # instead of crowding the header. "Export chat" moved
        # there (see _show_header_overflow_menu) — nothing is
        # deleted, the handler is the same _export_chat.
        self.btn_header_overflow = QPushButton("···")
        self.btn_header_overflow.setObjectName("chipBtn")
        self.btn_header_overflow.setAccessibleName(
            "More conversation actions")
        self.btn_header_overflow.setAccessibleDescription(
            "Open the menu with export chat, screen share, voice "
            "and language, and microphone health")
        self.btn_header_overflow.setToolTip("More conversation actions")
        self.btn_header_overflow.clicked.connect(
            self._show_header_overflow_menu)
        top.addWidget(self.btn_header_overflow)
        # Command Deck Phase D: the loose pills become one
        # StatusStrip (guide §8). The strip OWNS the label
        # objects, so the existing updaters keep working
        # unchanged: _set_pill writes status_pill, _sync_watch_pill
        # writes watch_pill, _render_capture_indicator writes
        # capture_pill — all through these aliases. The glyph
        # label is synced by _set_pill on every status change.
        from .design_system.statusstrip import StatusStrip
        self.status_strip = StatusStrip()
        top.addWidget(self.status_strip)
        self.status_glyph_label = self.status_strip.glyph_label
        self.status_pill = self.status_strip.status_label
        # EYES E6 (v5.45): the watch-my-screen chip — visible only
        # while the mode is active (see _sync_watch_pill, driven
        # by core/desktop/screen_watch.pill_state).
        self.watch_pill = self.status_strip.watch_label
        # v5.48 (programme item 21): the capture indicator — a
        # sibling of the watch chip, visible only while a camera /
        # microphone / screenshot-save capture is live (rendered
        # by _render_capture_indicator from
        # capture_guard.active_captures()).
        self.capture_pill = self.status_strip.capture_label
        # v5.51 (E-2): the microphone health dot — UIS2 moves
        # its VISIBLE presence out of the header into the
        # overflow menu (see _show_header_overflow_menu, which
        # reads this dot's live tooltip). The dot object itself
        # stays: _refresh_mic_health (window_services) renders
        # into it on the telemetry tick, so it is kept as a
        # hidden render target rather than deleted — deleting
        # it would silently stop the health computation. It is
        # never added to the header layout, so nothing shows
        # here. Grey until a stream has run, green while the
        # stream holds, red once the A9 counter moves.
        self.mic_health_dot = QLabel("●")
        self.mic_health_dot.setObjectName("micHealthDot")
        self.mic_health_dot.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 12px; padding: 6px 2px;")
        self.mic_health_dot.setToolTip(
            "Microphone health: no stream has run yet this "
            "session — connect and talk, and VYUK will keep an "
            "eye on dropped sound here.")
        self.mic_health_dot.setVisible(False)
        self._mic_health_key = "no_stream"
        self._mic_stream_seen = False
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setObjectName("primaryBtn")
        self.btn_connect.setAccessibleDescription(
            "Connect to the voice assistant, or disconnect when "
            "connected")
        self.btn_connect.setMinimumHeight(34)  # v5.54 (DPI rule ⑤)
        self.btn_connect.clicked.connect(self._toggle_connection)
        top.addWidget(self.btn_connect)
        layout.addLayout(top)

        # v5.49 (programme item 29): the goal/plan progress pill —
        # a slim strip under the header, visible only while a goal
        # (planner.run_goal) or a plan (planner.run_plan) is
        # running, fed by the SAME emit lines the clients already
        # send to the transcript (see _update_goal_pill, hooked in
        # _on_transcript, plus the plan_progress_line bridge for
        # GUI-started headless runs). Stop is the hard stop: it
        # trips the failsafe AND bumps the session epoch — the
        # planner checks both before every step.
        self.goal_pill = QFrame()
        self.goal_pill.setObjectName("goalPill")
        # Command Deck Phase E: the goal pill is a cyan wash at
        # the old translucencies (22/70), cut to the 2 px chip
        # radius — the indigo wash and 12 px radius are gone.
        self.goal_pill.setStyleSheet(
            f"QFrame#goalPill {{ background: {with_alpha(ACCENT, 22)};"
            f" border: 1px solid {with_alpha(ACCENT, 70)};"
            f" border-radius: {RADIUS_BTN}px; }}")
        gp_layout = QHBoxLayout(self.goal_pill)
        gp_layout.setContentsMargins(14, 7, 10, 7)
        gp_layout.setSpacing(10)
        self.goal_pill_label = QLabel("")
        self.goal_pill_label.setObjectName("muted")
        self.goal_pill_label.setWordWrap(True)
        gp_layout.addWidget(self.goal_pill_label, stretch=1)
        self.btn_goal_stop = QPushButton("Stop")
        self.btn_goal_stop.setAccessibleDescription(
            "Stop the running goal or plan")
        self.btn_goal_stop.setObjectName("chipBtn")
        self.btn_goal_stop.setToolTip(
            "Stop the running goal or plan now (emergency stop)")
        self.btn_goal_stop.clicked.connect(self._on_goal_stop_clicked)
        gp_layout.addWidget(self.btn_goal_stop)
        self.goal_pill.setVisible(False)
        layout.addWidget(self.goal_pill)
        self._goal_kind = "Goal"
        self._goal_total = 0
        self._goal_current = 0

        # Settings redesign §4c/§5/§8: the settings-voice strip —
        # the one place a pending proposal, its undo, a live
        # session grant, or a cloud-fallback repair offer is
        # shown and answered. Hidden by default; refreshed on a
        # 1 s timer from core/settings_voice state (the
        # _refresh_setup_banner pattern) — the funnel handler
        # never touches Qt, the strip polls it.
        self.settings_voice_strip = QFrame()
        self.settings_voice_strip.setObjectName("goalPill")
        self.settings_voice_strip.setAccessibleName(
            "Settings voice strip")
        sv_layout = QHBoxLayout(self.settings_voice_strip)
        sv_layout.setContentsMargins(14, 7, 10, 7)
        sv_layout.setSpacing(10)
        self.settings_voice_strip_label = QLabel("")
        self.settings_voice_strip_label.setObjectName("muted")
        self.settings_voice_strip_label.setWordWrap(True)
        sv_layout.addWidget(self.settings_voice_strip_label, stretch=1)
        self.btn_settings_voice_yes = QPushButton("Yes")
        self.btn_settings_voice_yes.setObjectName("chipBtn")
        self.btn_settings_voice_yes.setAccessibleName(
            "Confirm settings change")
        self.btn_settings_voice_yes.clicked.connect(
            self._on_settings_voice_yes)
        sv_layout.addWidget(self.btn_settings_voice_yes)
        self.btn_settings_voice_no = QPushButton("No")
        self.btn_settings_voice_no.setObjectName("chipBtn")
        self.btn_settings_voice_no.setAccessibleName(
            "Decline settings change")
        self.btn_settings_voice_no.clicked.connect(
            self._on_settings_voice_no)
        sv_layout.addWidget(self.btn_settings_voice_no)
        self.btn_settings_voice_undo = QPushButton("Undo")
        self.btn_settings_voice_undo.setObjectName("chipBtn")
        self.btn_settings_voice_undo.setAccessibleName(
            "Undo settings change")
        self.btn_settings_voice_undo.clicked.connect(
            self._on_settings_voice_undo)
        sv_layout.addWidget(self.btn_settings_voice_undo)
        self.btn_settings_voice_keep = QPushButton("Keep it on")
        self.btn_settings_voice_keep.setObjectName("chipBtn")
        self.btn_settings_voice_keep.setAccessibleName(
            "Keep session grant on in Settings")
        self.btn_settings_voice_keep.clicked.connect(
            self._on_settings_voice_keep)
        sv_layout.addWidget(self.btn_settings_voice_keep)
        self.btn_settings_voice_off = QPushButton("Turn off")
        self.btn_settings_voice_off.setObjectName("chipBtn")
        self.btn_settings_voice_off.setAccessibleName(
            "Turn off session grant")
        self.btn_settings_voice_off.clicked.connect(
            self._on_settings_voice_off)
        sv_layout.addWidget(self.btn_settings_voice_off)
        self.btn_settings_voice_fix = QPushButton("Fix the connection")
        self.btn_settings_voice_fix.setObjectName("chipBtn")
        self.btn_settings_voice_fix.setAccessibleName(
            "Fix the cloud connection")
        self.btn_settings_voice_fix.clicked.connect(
            self._open_connect_dialog)
        sv_layout.addWidget(self.btn_settings_voice_fix)
        # First-run onboarding (2026-11-16): the browser-connect
        # offer — shown when a browser tool call refused for lack
        # of a session, so the way back is one tap, not a trip
        # into Settings.
        self.btn_settings_voice_browser = QPushButton(
            "Connect my browser")
        self.btn_settings_voice_browser.setObjectName("chipBtn")
        self.btn_settings_voice_browser.setAccessibleName(
            "Connect my browser")
        self.btn_settings_voice_browser.clicked.connect(
            self._open_browser_connect_dialog)
        sv_layout.addWidget(self.btn_settings_voice_browser)
        # Build 5 (§9.1): dismiss for the first-launch browser
        # offer — one quiet offer, never repeated after dismiss.
        self.btn_settings_voice_dismiss = QPushButton("Dismiss")
        self.btn_settings_voice_dismiss.setObjectName("chipBtn")
        self.btn_settings_voice_dismiss.setAccessibleName(
            "Dismiss browser offer")
        self.btn_settings_voice_dismiss.clicked.connect(
            self._on_settings_voice_dismiss)
        sv_layout.addWidget(self.btn_settings_voice_dismiss)
        self.settings_voice_strip.setVisible(False)
        layout.addWidget(self.settings_voice_strip)
        self._connect_dialog = None
        self._browser_connect_dialog = None
        self._settings_voice_timer = QTimer(self)
        self._settings_voice_timer.setInterval(1000)
        self._settings_voice_timer.timeout.connect(
            self._refresh_settings_voice_strip)
        self._settings_voice_timer.start()

        # Sci-fi Home (2026-11-16): the home strip is gone — its
        # three things already live elsewhere. The date reads in
        # the vitals line under the orb; the next reminder reads
        # in the briefing schedule; Play briefing moved into the
        # briefing card's header row (see below). No feature is
        # deleted, only the card that repeated them.

        # v5.34 split layout (Aayur, 2026-10-22): the stage —
        # orb and her live features — owns the centre of the
        # page, and the conversation docks in a fixed side
        # panel on the right: the arrangement with the orb
        # and the chat switched. The hero no longer folds
        # away mid-conversation; the panel is what gives the
        # transcript its room.
        # v5.57 (redo, A9.3): the stage/chat divider is a real
        # splitter now — drag the ⋮ grip to resize the chat panel.
        # The chat still opens at 400 px (the frozen home shape);
        # it never squeezes below 300 px, the stage never below
        # its tier floor (420 comfort, 320 narrow — see
        # gui/breakpoints.py). Per-session only, not saved
        # (window memory is PF-21, Wave 9).
        split = _DotsSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)
        # v5.57 (redo): the splitter is stored so first show can
        # pin the chat at the frozen 400 px (setSizes alone only
        # proportions — at 1280 px wide it opened at 300 px) and
        # window resizes can keep a user-dragged width. Program
        # moves (the pin) never mark the width user-set.
        self._chat_splitter = split

        def _on_chat_splitter_moved(*_args):
            if not getattr(self, "_fixing_chat_width", False):
                self._chat_width_user_set = True
                # Store the dragged width at move time: by window-
                # resize time the splitter has already re-
                # proportioned it away, so the current width then
                # is no longer the user's width.
                self._chat_width_user = self.chat_panel.width()
                # UIS5: a drag changed the panel width — re-apply
                # the composer narrow rule (resizes alone don't
                # cover drags).
                self._apply_dock_narrow_rule()
                # UI rethink Build 1 (render fix): a drag also
                # changes the stage width, so the briefing summary
                # needs the same deferred re-elide as a resize
                # (its width only settles after the layout pass).
                try:
                    _reelide = getattr(
                        self, "_reelide_briefing_summary", None)
                    if _reelide is not None:
                        QTimer.singleShot(0, _reelide)
                except Exception:  # noqa: BLE001 — cosmetic
                    pass

        split.splitterMoved.connect(_on_chat_splitter_moved)
        layout.addWidget(split, stretch=1)
        stage = QWidget()
        stage.setMinimumWidth(420)
        # Golden-fix D1 (overflow handling ONLY — the settled Phase E
        # composition is untouched: same widgets, same order, same
        # floors): at short heights the stage column crushed below
        # its minimum (voice chip / briefing card / pace squeezed,
        # orb paint overlapping neighbours). The stage now scrolls
        # instead of squashing: at normal heights the scroll area
        # fills and no bar shows; at short heights the stage keeps
        # its natural height and scrolls. The splitter child is the
        # scroll area, so the tier floor lives on it too (see
        # _apply_breakpoint_tier); the inner stage keeps its own.
        self._stage_scroll = QScrollArea()
        self._stage_scroll.setWidgetResizable(True)
        self._stage_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # The splitter child carries the tier floor (the inner
        # stage's own floor stays, so the scrolled content never
        # narrows below it); _apply_breakpoint_tier keeps both in
        # step, starting here at the comfort floor.
        self._stage_scroll.setMinimumWidth(420)
        self._stage_scroll.setWidget(stage)
        # setWidget() flips autoFillBackground on (see D6) — reset
        # it, or the stage would paint palette-Window light.
        stage.setAutoFillBackground(False)
        self._stage_scroll.viewport().setAutoFillBackground(False)
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(8)
        split.addWidget(self._stage_scroll)
        self._stage_widget = stage
        self.chat_panel = QWidget()
        self.chat_panel.setObjectName("chatPanel")
        # Chat pass (2026-11-17, owner ruling: the chat size is
        # v6.3 exactly): the panel floor is 300 px again — the
        # Build 1 raise to 380 is reverted. The composer still
        # fits at 300: the Dictate icon-only collapse (see
        # _apply_dock_narrow_rule) is what keeps the input
        # usable, not the wider floor.
        self.chat_panel.setMinimumWidth(300)
        panel_layout = QVBoxLayout(self.chat_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)
        split.addWidget(self.chat_panel)
        split.setSizes([1000, 400])
        # v5.57 fix (battery: test_v530_batch4 widths): the stage
        # absorbs window resizes — the chat keeps its width and
        # the stage takes all of the change. QSplitter stretch
        # does this declaratively (stage 1, chat 0); the deferred
        # guard's resizeEvent store could never win the race (the
        # layout redistributes before any handler reads), so the
        # guard stays only as the first-show pin + floor enforcer.
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)

        # Phase 2 (chatbox #18): the old quick-actions row is gone —
        # its actions live in the one contextual chip row above the
        # dock (see _refresh_chip_row).

        # v5.18 (Eyes E5): the orb floats over an ambient particle
        # field — a StackAll hero where the field paints behind the
        # (background-transparent) visualizer. The field is
        # mouse-transparent, honours the Appearance toggle and the
        # reduced-motion setting (see _apply_ambience).
        hero = _HeroWidget()
        self.hero = hero
        hero.setMinimumSize(400, 80)
        hero_layout = QStackedLayout(hero)
        hero_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        hero_layout.setContentsMargins(0, 0, 0, 0)
        self.particle_field = None
        try:
            from voice_assistant.gui.ambience import ParticleField

            self.particle_field = ParticleField()
            hero_layout.addWidget(self.particle_field)
        except Exception as e:  # noqa: BLE001 — hero must still build
            print(f"[VYUK] particle field unavailable: {e}")
        self.visualizer = OrbVisualizer()
        self.visualizer.setMinimumSize(320, 80)
        hero_layout.addWidget(self.visualizer)
        # Sci-fi Home: the room's light model (see gui/room_home)
        # — one RoomLight for the window, driven from the telemetry
        # tick by _sync_room_light in window_services_speech.
        self._room_light = RoomLight()
        self._room_hour = None
        self._room_night = None
        stage_layout.addStretch()
        # v5.57 (redo, A9.3): voice/language chip — the current
        # voice + reply language; tap to switch (the menu drives
        # the existing Settings combos, so the same handlers run
        # — no second path). Sci-fi Home: the chip leaves the
        # stage — a second labelled control above the orb competes
        # with it. It is kept as a hidden render target (the
        # mic-health-dot precedent): still created, still wired,
        # still in the tab-order chain (Qt skips it at runtime
        # because it is hidden), and the same menu now opens from
        # the header overflow ("Voice & language…"). Deleting it
        # would silently stop the refresh wiring.
        self.voice_chip = QPushButton("Voice & language ▾")
        self.voice_chip.setObjectName("chipBtn")
        self.voice_chip.setToolTip(
            "Switch VYUK's voice or reply language")
        self.voice_chip.clicked.connect(self._show_voice_chip_menu)
        self.voice_chip.setVisible(False)
        # Stage 0 features batch 5 (narrow window, render-found
        # at 640 px): the hero used to be centre-aligned here, so
        # it took its 400 px sizeHint width at every window width
        # — at 640 the 400 px hero overflowed the 240 px stage and
        # the orb painted past the stage into the chat dock (and
        # the visualizer's 320 px floor starved the chat too).
        # Without the alignment the hero fills the stage width,
        # so the orb shrinks with the stage; the OrbVisualizer
        # paints the orb centred in its widget, so the orb stays
        # centred at every width, and at comfort widths the orb
        # is height-limited exactly as before.
        stage_layout.addWidget(hero, stretch=0)
        # Conversion Phase 0: breathing room between the hero
        # and the wordmark. The stage spacing is 8 px and the
        # orb's own paint now stops 16 px inside its widget
        # (see OrbVisualizer._ORB_PAINT_MARGIN /
        # _ORB_EDGE_INSET), so the painted orb edge sits at
        # least ~32 px above the wordmark even when the stage
        # is short and the hero shrinks to its cell.
        # UI rethink Build 1: the orb-to-wordmark gap is 16 px —
        # the layout spacing (8) plus this spacer (8).
        stage_layout.addSpacing(8)

        # v5.57 (redo): the VYUK wordmark under the orb, as mocked.
        # UI rethink Build 1: a caption now, not a headline — 13
        # Sora semibold, muted (see §6.1).
        self.home_wordmark = QLabel("VYUK")
        self.home_wordmark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.home_wordmark.setStyleSheet(
            f"font-size: {TYPE_BODY}px; font-weight: 600; color: {TEXT_MUTED};")
        stage_layout.addWidget(self.home_wordmark)
        self.state_label = QLabel("Ready when you are")
        self.state_label.setObjectName("stateLabel")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stage_layout.addWidget(self.state_label)
        # Phase 2 (chatbox #12): the speaking equaliser lives in
        # the state row now, not inside her last bubble — bubbles
        # never change height mid-conversation any more.
        self.speaking_eq = MiniEq()
        self.speaking_eq.setVisible(False)
        stage_layout.addWidget(self.speaking_eq,
                               alignment=Qt.AlignmentFlag.AlignCenter)
        # Refinement A: one-line live briefing (date · time · vitals),
        # refreshed ~1 Hz by _sync_audio_telemetry.
        self.briefing_label = QLabel("")
        self.briefing_label.setObjectName("briefingLine")
        self.briefing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stage_layout.addWidget(self.briefing_label)
        self.caption_label = QLabel("Press Connect, then just talk.")
        self.caption_label.setObjectName("captionLabel")
        self.caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption_label.setWordWrap(True)
        stage_layout.addWidget(self.caption_label)
        # UI rethink Build 1: the caption-to-briefing gap is 24 px —
        # the layout spacing (8) plus this spacer (16).
        stage_layout.addSpacing(16)

        # v5.57 (redo, A9.3): the day briefing card — greeting +
        # today's schedule + reminders + battery, from existing
        # sources only (reminders + battery locally, calendar via
        # the same next_events the heads-ups use, on a worker so
        # a slow calendar never stalls the page).
        self.briefing_card = QFrame()
        self.briefing_card.setObjectName("settingsCard")
        _b_layout = QVBoxLayout(self.briefing_card)
        _b_layout.setContentsMargins(16, 12, 16, 12)
        _b_layout.setSpacing(4)
        # Sci-fi Home: the card collapses to one line — the day's
        # summary — with the five rows behind one tap. The header
        # row holds the summary, the Play briefing button (moved
        # here from the retired home strip), and the chevron that
        # expands the same five rows in place.
        _b_head = QHBoxLayout()
        _b_head.setContentsMargins(0, 0, 0, 0)
        _b_head.setSpacing(8)
        self.briefing_summary = QLabel("")
        self.briefing_summary.setObjectName("briefingSummary")
        self.briefing_summary.setWordWrap(False)
        self.briefing_summary.setAccessibleDescription(
            "Today's briefing in one line — tap the arrow to see "
            "the full briefing.")
        _b_head.addWidget(self.briefing_summary, stretch=1)
        self.btn_briefing = QPushButton("Play briefing")
        self.btn_briefing.setObjectName("chipBtn")
        self.btn_briefing.setIcon(make_icon("play", TEXT, 13))
        self.btn_briefing.clicked.connect(self._play_briefing)
        _b_head.addWidget(self.btn_briefing)
        self.btn_briefing_expand = QPushButton("▾")
        self.btn_briefing_expand.setObjectName("chipBtn")
        self.btn_briefing_expand.setAccessibleName(
            "Show the full day briefing")
        self.btn_briefing_expand.setToolTip("Show the full day briefing")
        self.btn_briefing_expand.clicked.connect(
            self._toggle_briefing_card)
        _b_head.addWidget(self.btn_briefing_expand)
        _b_layout.addLayout(_b_head)
        self._briefing_details = QWidget()
        _b_details = QVBoxLayout(self._briefing_details)
        _b_details.setContentsMargins(0, 0, 0, 0)
        _b_details.setSpacing(4)
        # UI rethink Build 1: the "Connect calendar" link lives
        # behind the chevron, as the details' first row — the
        # schedule line itself stays one short sentence. Visible
        # only while the calendar is not connected (see
        # _render_briefing_schedule).
        self.btn_connect_calendar = QPushButton("Connect calendar")
        self.btn_connect_calendar.setObjectName("chipBtn")
        self.btn_connect_calendar.setAccessibleName(
            "Connect the calendar")
        self.btn_connect_calendar.setToolTip(
            "Open Settings on the Your world card")
        self.btn_connect_calendar.clicked.connect(
            lambda _=False: self.show_settings_card("Your world"))
        self.btn_connect_calendar.setVisible(False)
        _b_details.addWidget(self.btn_connect_calendar)
        self.briefing_kicker = QLabel("")
        self.briefing_kicker.setStyleSheet(
            f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600; color: {TEXT};")
        _b_details.addWidget(self.briefing_kicker)
        self.briefing_schedule = QLabel("")
        self.briefing_schedule.setObjectName("muted")
        self.briefing_schedule.setWordWrap(True)
        _b_details.addWidget(self.briefing_schedule)
        # B24 (second-opinion OFF surfacing): the home briefing
        # carries the same OFF line as the receipt/ledger/
        # Activity — one muted line under the schedule, hidden
        # unless the latest task recorded one (see
        # ShellBriefingMixin._refresh_briefing_second_opinion).
        self.briefing_second_opinion = QLabel("")
        self.briefing_second_opinion.setObjectName("muted")
        self.briefing_second_opinion.setWordWrap(True)
        self.briefing_second_opinion.setVisible(False)
        _b_details.addWidget(self.briefing_second_opinion)
        self.briefing_battery = QLabel("")
        self.briefing_battery.setObjectName("muted")
        self.briefing_battery.setWordWrap(True)
        _b_details.addWidget(self.briefing_battery)
        # Conversion Wave 9: on-this-day resurfacing from the live
        # milestone log, on the day-briefing card. Empty (the
        # normal case) hides the label.
        self.briefing_on_this_day = QLabel("")
        self.briefing_on_this_day.setObjectName("muted")
        self.briefing_on_this_day.setWordWrap(True)
        self.briefing_on_this_day.setVisible(False)
        _b_details.addWidget(self.briefing_on_this_day)
        _b_layout.addWidget(self._briefing_details)
        # Collapsed by default: the room shows one line, not five.
        self._briefing_details.setVisible(False)
        self._briefing_expanded = False
        self._briefing_summary_full = None
        # Round-3 wizard expansion (parent ruling:
        # skip-everything): the "finish setup" card — visible
        # only while setup is incomplete (onboarded but no brain
        # choice stored, see firstrun_g.setup_incomplete). Its
        # button re-opens the wizard, which resumes at the first
        # open stage. Hidden when setup is complete, so the frozen
        # v6.3 Home layout is unchanged for completed setups.
        # The card uses the existing settingsCard frame + chip
        # buttons — no new stylesheets, no layout change when
        # hidden (a hidden card takes no space).
        # Loophole batch 7 (#8 + #18): the card is per-piece
        # now (core/setup_pieces.py) — it names the unfinished
        # pieces (Google sign-in, Gmail, Calendar, Browser,
        # Microphone) and shows while any piece is open OR no
        # brain choice is stored. Its "Remind me later" snoozes
        # it for 7 days — never dismisses it for good.
        self.setup_card = QFrame()
        self.setup_card.setObjectName("settingsCard")
        _s_layout = QVBoxLayout(self.setup_card)
        _s_layout.setContentsMargins(16, 12, 16, 12)
        _s_layout.setSpacing(4)
        _s_title = QLabel("Finish setting up")
        _s_title.setObjectName("briefingSummary")
        _s_title.setWordWrap(True)
        _s_layout.addWidget(_s_title)
        self._setup_card_body = QLabel(
            "You skipped setup — your brain is local-only for now. "
            "Pick your language, voice and permissions whenever "
            "you're ready.")
        self._setup_card_body.setObjectName("muted")
        self._setup_card_body.setWordWrap(True)
        _s_layout.addWidget(self._setup_card_body)
        _s_row = QHBoxLayout()
        _s_row.setSpacing(8)
        self.btn_finish_setup = QPushButton("Finish setup")
        self.btn_finish_setup.setObjectName("primaryBtn")
        self.btn_finish_setup.setAccessibleName("Finish setup")
        self.btn_finish_setup.clicked.connect(
            lambda _=False: self._run_onboarding())
        _s_row.addWidget(self.btn_finish_setup)
        self.btn_setup_snooze = QPushButton("Remind me later")
        self.btn_setup_snooze.setObjectName("chipBtn")
        self.btn_setup_snooze.setAccessibleName("Remind me later")
        self.btn_setup_snooze.clicked.connect(
            self._on_setup_card_snooze)
        _s_row.addWidget(self.btn_setup_snooze)
        _s_row.addStretch()
        _s_layout.addLayout(_s_row)
        stage_layout.addWidget(self.setup_card)
        # Round-3 wizard expansion (parent ruling: a self-test
        # failure is never silent): the fix-it card — visible
        # only while firstrun/selftest_faults holds failing check
        # names. "Check again" re-runs the first-run checks
        # off-thread and clears the pin on a pass; "Open
        # Diagnostics" goes to the full Doctor with fixes.
        self.selftest_card = QFrame()
        self.selftest_card.setObjectName("settingsCard")
        _f_layout = QVBoxLayout(self.selftest_card)
        _f_layout.setContentsMargins(16, 12, 16, 12)
        _f_layout.setSpacing(4)
        _f_title = QLabel("Something needs attention")
        _f_title.setObjectName("briefingSummary")
        _f_title.setWordWrap(True)
        _f_layout.addWidget(_f_title)
        self._selftest_faults_label = QLabel("")
        self._selftest_faults_label.setObjectName("muted")
        self._selftest_faults_label.setWordWrap(True)
        _f_layout.addWidget(self._selftest_faults_label)
        _f_row = QHBoxLayout()
        _f_row.setSpacing(8)
        self.btn_selftest_rerun = QPushButton("Check again")
        self.btn_selftest_rerun.setObjectName("chipBtn")
        self.btn_selftest_rerun.setAccessibleName(
            "Run the self-test again")
        self.btn_selftest_rerun.clicked.connect(
            self._rerun_selftest_from_home)
        _f_row.addWidget(self.btn_selftest_rerun)
        self.btn_selftest_doctor = QPushButton("Open Diagnostics")
        self.btn_selftest_doctor.setObjectName("chipBtn")
        self.btn_selftest_doctor.setAccessibleName(
            "Open Diagnostics")
        self.btn_selftest_doctor.clicked.connect(
            lambda _=False: self._show_page(7))
        _f_row.addWidget(self.btn_selftest_doctor)
        _f_row.addStretch()
        _f_layout.addLayout(_f_row)
        stage_layout.addWidget(self.selftest_card)
        self._refresh_setup_cards()
        stage_layout.addWidget(self.briefing_card)
        self._briefing_worker = None
        self._refresh_briefing_card()

        # v5.17 sense strip (Ears A1/A13, Mouth M7/M9–M11): mic
        # level, the speech transport, voice pace, and the week's
        # activity sparkline — all fed by _sync_audio_telemetry.
        self._activity_days = self._load_activity_days()
        self._karaoke_text: str | None = None
        self.mic_meter = QProgressBar()
        self.mic_meter.setObjectName("micMeter")
        self.mic_meter.setRange(0, 100)
        self.mic_meter.setValue(0)
        self.mic_meter.setTextVisible(False)
        self.mic_meter.setFixedHeight(6)
        self.mic_meter.setToolTip("Microphone level")
        # Sci-fi Home: the meter shows only while listening — the
        # orb carries the level while she speaks, and at idle
        # there is nothing to meter. Driven from telemetry.
        self.mic_meter.setVisible(False)
        self.mic_meter.setStyleSheet(
            f"QProgressBar {{ background: {with_alpha(TEXT, 28)}; border: none;"
            " border-radius: 3px; }"
            # Command Deck Phase D: mic level is "alive", not
            # "done" — D2 reserves green for done/healthy, so the
            # meter chunk follows the cyan accent.
            f"QProgressBar::chunk {{ background: {ACCENT};"
            " border-radius: 3px; }")
        stage_layout.addWidget(self.mic_meter)
        self.transport_row = QWidget()
        t_layout = QHBoxLayout(self.transport_row)
        t_layout.setContentsMargins(0, 2, 0, 0)
        t_layout.setSpacing(8)
        t_layout.addStretch()
        self.btn_speech_pause = QPushButton("⏸ Pause her")
        self.btn_speech_pause.setObjectName("chipBtn")
        self.btn_speech_pause.setToolTip("Pause or resume her speech, mid-sentence")
        self.btn_speech_pause.clicked.connect(self._on_speech_pause_toggled)
        t_layout.addWidget(self.btn_speech_pause)
        self.btn_speech_skip = QPushButton("⏭ Skip sentence")
        self.btn_speech_skip.setObjectName("chipBtn")
        self.btn_speech_skip.setToolTip("Drop the rest of the sentence she's saying")
        self.btn_speech_skip.clicked.connect(self._on_speech_skip)
        t_layout.addWidget(self.btn_speech_skip)
        self.lbl_speech_queue = QLabel("")
        self.lbl_speech_queue.setObjectName("muted")
        t_layout.addWidget(self.lbl_speech_queue)
        t_layout.addStretch()
        self.transport_row.setVisible(False)
        stage_layout.addWidget(self.transport_row)
        self.pace_box = QWidget()
        pace_row = QHBoxLayout(self.pace_box)
        pace_row.setContentsMargins(0, 0, 0, 0)
        pace_row.setSpacing(6)
        pace_lbl = QLabel("Voice pace")
        pace_lbl.setObjectName("muted")
        pace_row.addWidget(pace_lbl)
        self.pace_buttons: dict[float, QPushButton] = {}
        for _pace, _label in ((0.9, "0.9×"), (1.0, "1.0×"), (1.1, "1.1×")):
            _btn = QPushButton(_label)
            _btn.setObjectName("chipBtn")
            _btn.setAccessibleDescription(f"Set speech pace to {_label}")
            _btn.setCheckable(True)
            _btn.clicked.connect(lambda _=False, p=_pace: self._on_pace_selected(p))
            self.pace_buttons[_pace] = _btn
            pace_row.addWidget(_btn)
        pace_row.addStretch()
        self.sparkline = _ActivitySparkline()
        self.sparkline.set_days(self._activity_days)
        pace_row.addWidget(self.sparkline)
        # Sci-fi Home: the pace box shows only while she is
        # speaking — the same condition as the transport row
        # (see _sync_speech_transport). At idle the pace lives in
        # Settings, on the Brain card's speed slider.
        self.pace_box.setVisible(False)
        stage_layout.addWidget(self.pace_box)

        # UI rethink Build 1: the speaking waveform ribbon is gone
        # — the tall glowing divider between the stage and the chat
        # was the room's noisiest element, and the dock waveform
        # (width-gated, see _apply_dock_narrow_rule) already shows
        # the voice where it belongs. The feed in
        # _sync_audio_telemetry guards on None, so the attribute
        # stays as None and nothing else changes.
        self.wave_ribbon = None
        stage_layout.addStretch()

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        chat_content = QWidget()
        self.chat_layout = QVBoxLayout(chat_content)
        self.chat_layout.setContentsMargins(4, 4, 4, 4)
        # UI rethink Build 1: the transcript sets its own rhythm —
        # spacing 0, and each row carries its top margin (6 above a
        # message, 2 between grouped messages, 14 between speakers,
        # see _add_bubble / _add_card / _add_system). A uniform
        # spacing made every gap the same and the transcript read
        # as a stack of boxes.
        self.chat_layout.setSpacing(0)
        # UI-feel pass: this stretch LEADS the layout. Every
        # chat item appends after it, so the conversation is
        # anchored to the bottom (messages grow upward from
        # the composer) instead of parking at the top of the
        # scroll area with a void underneath.
        self.chat_layout.addStretch()
        # UI-feel pass: the chat is a centred column, not a
        # full-width sprawl — the scroll area's widget is a
        # wrapper that centres the content at max 940 px.
        chat_content.setMaximumWidth(940)
        chat_wrap = QWidget()
        # The wrapper carries the chatContent name: it is the
        # widget the scroll area actually paints, so the
        # themed background must live on it, not the column.
        chat_wrap.setObjectName("chatContent")
        chat_content.setObjectName("chatColumn")
        wrap_layout = QHBoxLayout(chat_wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.setSpacing(0)
        wrap_layout.addStretch()
        wrap_layout.addWidget(chat_content)
        wrap_layout.addStretch()
        self.chat_scroll.setWidget(chat_wrap)
        # U6 (visual channel): the lesson panel lives in the settled
        # home, above the chat scroll area — hidden until a lesson
        # starts, so the home looks exactly as before when no lesson
        # is running. No second window, no Home redesign, no Aura
        # change. Guarded like the wave ribbon above: chat must
        # still build if the panel cannot.
        self.lesson_panel = None
        try:
            from voice_assistant.gui.lesson_panel import LessonPanel

            self.lesson_panel = LessonPanel(self.chat_panel)
            self.lesson_panel.setVisible(False)
            panel_layout.addWidget(self.lesson_panel)
        except Exception as e:  # noqa: BLE001 — chat must still build
            print(f"[VYUK] lesson panel unavailable: {e}")
        # U9 (progress report): the report panel lives in the settled
        # home beside the lesson panel, above the chat scroll area —
        # hidden until a report is shown, so the home looks exactly
        # as before when no report is showing. No second window, no
        # Home redesign, no Aura change. Guarded like the lesson
        # panel above: chat must still build if the panel cannot.
        self.progress_panel = None
        try:
            from voice_assistant.gui.progress_report_panel import (
                ProgressReportPanel,
            )

            self.progress_panel = ProgressReportPanel(self.chat_panel)
            self.progress_panel.setVisible(False)
            panel_layout.addWidget(self.progress_panel)
        except Exception as e:  # noqa: BLE001 — chat must still build
            print(f"[VYUK] progress panel unavailable: {e}")
        # Chat UI (2026-11-16): the slim header row above the
        # chat — Pinned (with a jump menu, hidden until something
        # is pinned), and Back to latest (only while a history
        # window is open). UI rethink Build 1: Conversations moved
        # to the header overflow menu (see
        # _show_header_overflow_menu) — the header row is empty
        # most of the time, so it hides until one of its buttons
        # is visible (see _refresh_chat_header).
        self._chat_header_row = QWidget()
        _header = QHBoxLayout(self._chat_header_row)
        _header.setContentsMargins(4, 2, 4, 2)
        _header.setSpacing(6)
        self._btn_pinned = QPushButton("Pinned (0)")
        self._btn_pinned.setObjectName("chipBtn")
        self._btn_pinned.setAccessibleName("Jump to a pinned message")
        self._btn_pinned.setToolTip("Pinned messages")
        self._btn_pinned.setVisible(False)
        self._btn_pinned.clicked.connect(self._chat_show_pinned_menu)
        _header.addWidget(self._btn_pinned)
        self._btn_back_latest = QPushButton("Back to latest")
        self._btn_back_latest.setObjectName("chipBtn")
        self._btn_back_latest.setAccessibleName(
            "Back to the latest conversation")
        self._btn_back_latest.setVisible(False)
        self._btn_back_latest.clicked.connect(
            self._chat_back_to_latest)
        _header.addWidget(self._btn_back_latest)
        _header.addStretch()
        # UI rethink Build 1: hidden until one of its buttons is
        # visible — an empty header row is a void above the chat.
        self._chat_header_row.setVisible(False)
        panel_layout.addWidget(self._chat_header_row)
        panel_layout.addWidget(self.chat_scroll, stretch=1)
        # Phase 2 (chatbox #10): the "new messages" pill, overlaid
        # on the chat viewport; shown only when the user has
        # scrolled up and new content lands below.
        self._chat_near_bottom = True
        self._btn_new_msgs = QPushButton(
            "↓ New messages", self.chat_scroll.viewport())
        self._btn_new_msgs.setObjectName("newMsgPill")
        self._btn_new_msgs.setAccessibleName("Jump to new messages")
        self._btn_new_msgs.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_new_msgs.setVisible(False)
        self._btn_new_msgs.clicked.connect(self._jump_to_latest)

        def _track_chat_scroll(_v=0):
            bar = self.chat_scroll.verticalScrollBar()
            self._chat_near_bottom = (
                bar.maximum() - bar.value() <= 80)
            if self._chat_near_bottom:
                self._btn_new_msgs.setVisible(False)
                # Chat UI (2026-11-16): reaching the bottom
                # clears the pill count and marks the newest
                # message seen — once per away-period, not per
                # scroll tick (the count is only > 0 until it
                # is cleared here).
                if getattr(self, "_new_msgs_since", 0) > 0:
                    self._new_msgs_since = 0
                    self._chat_first_new_msg_id = None
                    try:
                        self._chat_mark_last_seen()
                    except Exception:  # noqa: BLE001
                        pass

        self.chat_scroll.verticalScrollBar().valueChanged.connect(
            _track_chat_scroll)
        self.chat_scroll.viewport().installEventFilter(self)

        # v5.31: the find bar — hidden until Ctrl+F. Counts and
        # walks matches across every bubble's plain text.
        self._find_bar = QWidget()
        find_row = QHBoxLayout(self._find_bar)
        find_row.setContentsMargins(4, 2, 4, 2)
        find_row.setSpacing(6)
        self.find_input = QLineEdit()
        self.find_input.setObjectName("dockInput")
        self.find_input.setPlaceholderText("Find in chat…")
        self.find_input.textChanged.connect(self._chat_find_reset)
        self.find_input.installEventFilter(self)
        find_row.addWidget(self.find_input, stretch=1)
        self.find_count = QLabel("")
        self.find_count.setObjectName("muted")
        find_row.addWidget(self.find_count)
        self.btn_find_prev = QPushButton("↑")
        self.btn_find_prev.setObjectName("chipBtn")
        self.btn_find_prev.setAccessibleName("Find previous match")
        self.btn_find_prev.setToolTip("Previous match (Shift+Enter)")
        self.btn_find_prev.clicked.connect(
            lambda _=False: self._chat_find_step(-1))
        find_row.addWidget(self.btn_find_prev)
        self.btn_find_next = QPushButton("↓")
        self.btn_find_next.setObjectName("chipBtn")
        self.btn_find_next.setAccessibleName("Find next match")
        self.btn_find_next.setToolTip("Next match (Enter)")
        self.btn_find_next.clicked.connect(
            lambda _=False: self._chat_find_step(1))
        find_row.addWidget(self.btn_find_next)
        self.btn_find_close = QPushButton("✕")
        self.btn_find_close.setObjectName("chipBtn")
        self.btn_find_close.setAccessibleName("Close find bar")
        self.btn_find_close.setToolTip("Close (Esc)")
        self.btn_find_close.clicked.connect(self._close_chat_find)
        find_row.addWidget(self.btn_find_close)
        self._find_bar.setVisible(False)
        self._find_matches: list = []
        self._find_index = -1
        panel_layout.addWidget(self._find_bar)
        self._find_shortcut = QShortcut(
            QKeySequence("Ctrl+F"), self)
        self._find_shortcut.activated.connect(self._open_chat_find)

        # Phase 2 (chatbox #18): ONE contextual chip row above the
        # dock — starters and quick actions while the chat is
        # empty, follow-ups once the conversation is going. It
        # never vanishes for good (see _refresh_chip_row).
        self._suggest_row = _ChipRow()
        self._chip_layout = _ChipFlowLayout(self._suggest_row)
        self._chip_layout.setContentsMargins(4, 2, 4, 2)
        self._chip_layout.setSpacing(8)
        # E-F101: the chip row wraps onto a second line instead
        # of running past the panel edge — the scroll strip
        # (fix #8) kept the later chips cut mid-word at the
        # right edge at every width, day and night, on both
        # pages. The scroll area stays (it owns the strip's
        # frame and the wheel filter below), but it now sizes
        # the row to the panel width so the flow layout can
        # wrap, and its height follows the wrapped content
        # (see _ChipScrollArea) instead of the fixed 42 px
        # single line.
        self._chip_scroll = _ChipScrollArea()
        self._chip_scroll.setWidget(self._suggest_row)
        # Adopting the row into the scroll area flips its
        # auto-fill on with the (light) widget palette — kill
        # it, or the strip paints a pale band across the panel.
        self._suggest_row.setAutoFillBackground(False)
        self._chip_scroll.setWidgetResizable(True)
        self._chip_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._chip_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chip_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chip_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        # Round-3 fix #8 (A-UI5): the strip slides on the wheel.
        # It was built as a scroll strip, but nothing could ever
        # slide it (scrollbar always off, wheel did nothing), so
        # the chips past the panel edge were cut with no way to
        # reach them. Installed on the viewport, like the chat
        # scroll's own viewport filter above.
        self._chip_wheel_filter = _ChipStripWheelFilter(self._chip_scroll)
        self._chip_scroll.viewport().installEventFilter(
            self._chip_wheel_filter)
        panel_layout.addWidget(self._chip_scroll)
        self._refresh_chip_row()

        # Chat UI (2026-11-16): the voice-state line above the
        # composer — Listening / Thinking / Speaking / Dictating,
        # in plain words, and nothing when the voice is doing
        # nothing — and the composer context row, which says what
        # the composer is doing right now (editing a message, or
        # replying to one) with a × to cancel. Both are quiet
        # single lines; neither is a second preview (the caption
        # already shows the live draft of what was heard).
        self._voice_state_lbl = QLabel("")
        self._voice_state_lbl.setObjectName("muted")
        self._voice_state_lbl.setVisible(False)
        panel_layout.addWidget(self._voice_state_lbl)
        self._composer_context_row = QWidget()
        _ctx = QHBoxLayout(self._composer_context_row)
        _ctx.setContentsMargins(4, 2, 4, 2)
        _ctx.setSpacing(6)
        self._composer_context_lbl = QLabel("")
        self._composer_context_lbl.setObjectName("muted")
        _ctx.addWidget(self._composer_context_lbl, 1)
        _ctx_cancel = QPushButton("✕")
        _ctx_cancel.setObjectName("chipBtn")
        _ctx_cancel.setAccessibleName("Cancel editing or replying")
        _ctx_cancel.setToolTip("Cancel (Esc)")
        _ctx_cancel.clicked.connect(self._cancel_composer_context)
        _ctx.addWidget(_ctx_cancel)
        self._composer_context_row.setVisible(False)
        panel_layout.addWidget(self._composer_context_row)

        dock = QHBoxLayout()
        dock.setSpacing(12)
        self.input_cmd = _ComposerEdit()
        self.input_cmd.setObjectName("dockInput")
        # UI rethink Build 1: the text field never goes below 120 px
        # — the old dock crushed it to a sliver ("Type,") while the
        # mic slid off the window's edge. The panel floor (300) and
        # the narrow rule below guarantee the field keeps this.
        self.input_cmd.setMinimumWidth(120)
        # Placeholders are not accessible names — a screen
        # reader announces nothing for this input without one.
        self.input_cmd.setAccessibleName("Message to VYUK")
        self.input_cmd.setAccessibleDescription(
            "Type a message; Enter sends it, Up arrow recalls "
            "the last sent message")
        # Command Deck Phase E (§9.2 Dock): the ›_ prompt glyph.
        # E-F103 (2026-11-20): the placeholder was "Type, or hold
        # the mic to speak" (195 px in Sora 13 px) but the input
        # only shows ~106 px at the 400 px default panel and ~70 px
        # at the 300 px floor, so it wrapped to a hidden second
        # line and read "Type, or" at every width — the full copy
        # cannot fit at 908, measured, not assumed. The shortened
        # copy (112 px) keeps both actions and the mic instruction
        # ("hold mic" is the mic's own hold-to-talk wording) and
        # reads whole at every width once the narrow rule below
        # gives the input room (screen-share hides below 420,
        # Dictate hides below 380).
        self.input_cmd.setPlaceholderText("Type, or hold mic")
        self.input_cmd.on_send = self._send_typed_message
        self.input_cmd.on_recall = self._composer_recall
        dock.addWidget(self.input_cmd, stretch=1)

        # Phase 2 (chatbox #2): a real Send button — mouse users
        # should not have to discover Enter on their own.
        self.btn_send = QPushButton()
        self.btn_send.setObjectName("sendBtn")
        self.btn_send.setFixedSize(44, 44)
        self.btn_send.setIcon(make_icon("send", BG, 20))
        self.btn_send.setIconSize(QSize(20, 20))
        self.btn_send.setToolTip("Send (Enter)")
        self.btn_send.setAccessibleName("Send message")
        self.btn_send.setAccessibleDescription(
            "Send the typed message. Enter works too.")
        self.btn_send.setEnabled(False)
        # Chat UI (2026-11-16): the button dispatches — Stop
        # while a turn is in flight, Send otherwise (see
        # _on_send_button). Enter still sends through on_send.
        self.btn_send.clicked.connect(self._on_send_button)
        dock.addWidget(self.btn_send)
        self.input_cmd.textChanged.connect(self._sync_send_button)
        # Chat UI (2026-11-16): the composer's draft persists —
        # a 1 s debounce, not a write per keystroke — so a
        # half-typed message survives a page switch or a restart.
        self._chat_draft_timer = QTimer(self)
        self._chat_draft_timer.setSingleShot(True)
        self._chat_draft_timer.setInterval(1000)
        self._chat_draft_timer.timeout.connect(
            self._chat_save_composer_draft)
        self.input_cmd.textChanged.connect(
            self._chat_schedule_draft_save)

        # Chat UI (2026-11-16): Dictate — the mic's words stage
        # in the composer as a draft for review (Ctrl+D), never
        # sent unreviewed. UI rethink Build 1: it carries an icon
        # as well as the label, so it can collapse to icon-only
        # below 420 px panel width (see _apply_dock_narrow_rule)
        # — the label is what squeezed the input to "cta".
        # Field-check fix #6 (U3 #47): the icon is a pencil, not
        # a second mic — at the shipped 300 px panel width the
        # label is gone and the two controls were two identical
        # mic glyphs with only tooltips between them.
        self._btn_dictate = QPushButton("Dictate")
        self._btn_dictate.setObjectName("squareBtn")
        self._btn_dictate.setCheckable(True)
        self._btn_dictate.setIcon(make_icon("pencil", TEXT_MUTED, 16))
        self._btn_dictate.setIconSize(QSize(16, 16))
        self._btn_dictate.setToolTip(
            "Dictate into the composer (Ctrl+D)")
        self._btn_dictate.setAccessibleName("Dictate")
        self._btn_dictate.setAccessibleDescription(
            "Dictate into the composer as a draft for review; "
            "nothing is sent until you press Send")
        self._btn_dictate.clicked.connect(self._chat_toggle_dictation)
        dock.addWidget(self._btn_dictate)

        # Phase 2 (chatbox #19): the waveform only takes dock
        # width while sound is actually flowing (the telemetry
        # tick toggles it).
        self.waveform = WaveformWidget()
        self.waveform.setVisible(False)
        dock.addWidget(self.waveform)

        self.btn_mic = QPushButton()
        self.btn_mic.setObjectName("micBtn")
        self.btn_mic.setFixedSize(56, 56)
        self.btn_mic.setIcon(make_icon("mic", BG, 26))
        self.btn_mic.setIconSize(QSize(26, 26))
        self.btn_mic.setToolTip("Hold to talk (or hold Space)")
        self.btn_mic.setAccessibleName("Hold to talk")
        self.btn_mic.setAccessibleDescription(
            "Press and hold to talk; release to send. Holding "
            "the Space key works too, except while typing.")
        shadow = QGraphicsDropShadowEffect(self.btn_mic)
        shadow.setBlurRadius(26)
        shadow.setColor(QColor(*parse_color(ACCENT)[:3], 130))
        shadow.setOffset(0, 6)
        self.btn_mic.setGraphicsEffect(shadow)
        self._mic_shadow = shadow
        self.btn_mic.pressed.connect(self._on_ptt_pressed)
        self.btn_mic.released.connect(self._on_ptt_released)
        dock.addWidget(self.btn_mic)

        self.btn_mute = QPushButton("Mute")
        # Command Deck Phase E (§9.2 Dock): square transport
        # buttons, not pills/circles.
        self.btn_mute.setObjectName("squareBtn")
        self.btn_mute.setAccessibleDescription(
            "Mute or unmute her spoken replies")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setIcon(make_icon("volume", TEXT_MUTED, 16))
        self.btn_mute.clicked.connect(self._toggle_mute)
        # Phase 2 (chatbox #19): Mute lives with the other speech
        # transport controls now, not in the composer row.
        self.transport_row.layout().addWidget(self.btn_mute)

        # UIS5 (composer narrow rule): stored on self so the
        # narrow rule (_apply_dock_narrow_rule) can hide it when
        # the chat panel gets too narrow for a usable input.
        self.btn_screen = QPushButton()
        # Command Deck Phase E (§9.2 Dock): square transport
        # buttons, not pills/circles.
        self.btn_screen.setObjectName("squareBtn")
        self.btn_screen.setFixedSize(42, 42)
        self.btn_screen.setIcon(make_icon("monitor", TEXT_MUTED, 19))
        self.btn_screen.setToolTip("Share your screen with VYUK")
        self.btn_screen.setAccessibleName("Share screen")
        self.btn_screen.clicked.connect(self._send_screen_capture)
        dock.addWidget(self.btn_screen)
        panel_layout.addLayout(dock)
        return page

    def _refresh_setup_cards(self):
        """Round-3 wizard expansion: show or hide the two
        conditional Home cards (see _build_assistant_page).
        Loophole batch 7 (#8 + #18): the setup card is per-piece
        now (core/setup_pieces.py) — it shows while setup is
        incomplete (onboarded but no brain choice stored) OR any
        setup piece is still open, and while the card is not
        snoozed; its body names the unfinished pieces. The fix-it
        card shows only while firstrun/selftest_faults holds
        failing check names. Both hide for a completed, passing
        setup — the frozen v6.3 Home layout is unchanged there.
        Called from the build, from _show_page, and after the
        wizard closes."""
        incomplete = False
        pieces_state = None
        _onboarded = False
        _snoozed = False
        try:
            from .firstrun_g import setup_incomplete

            incomplete = bool(setup_incomplete())
        except Exception:  # noqa: BLE001 — no card on a read failure
            incomplete = False
        try:
            from voice_assistant.core.gui_facade import (
                qsettings_get, setup_pieces as _sp)

            _store = {
                _sp.DECLINED_KEY: str(
                    qsettings_get(_sp.DECLINED_KEY) or ""),
                _sp.SNOOZED_UNTIL_KEY: str(
                    qsettings_get(_sp.SNOOZED_UNTIL_KEY) or ""),
            }
            try:
                _onboarded = bool(qsettings_get("onboarded"))
            except Exception:  # noqa: BLE001 — unreadable is fresh
                _onboarded = False
            _snoozed = _sp.is_snoozed(
                _sp.read_snoozed_until(_store))
            pieces_state = _sp.setup_card_state(
                onboarded=_onboarded,
                signals=_sp.live_signals(),
                declines=_sp.live_declines(store=_store),
                snoozed_until=_sp.read_snoozed_until(_store))
        except Exception:  # noqa: BLE001 — the pieces read is
            pieces_state = None  # best-effort; the legacy gate stands
        card = getattr(self, "setup_card", None)
        if card is not None:
            if pieces_state is not None:
                # Show while onboarded and (no brain choice OR a
                # piece still open) and not snoozed. The pieces
                # state's "visible" already folds onboarded +
                # unfinished + snooze; the legacy gate only ADDS
                # the no-brain-choice case (a decline-then-reject
                # walk can leave every piece declined with no
                # brain choice stored — the card must still show).
                show = bool(
                    _onboarded
                    and (incomplete or pieces_state["unfinished"])
                    and not _snoozed)
                card.setVisible(show)
                body = getattr(self, "_setup_card_body", None)
                if body is not None and show:
                    if pieces_state["unfinished_labels"]:
                        body.setText(
                            "Still to finish: "
                            + " · ".join(
                                pieces_state["unfinished_labels"])
                            + ". Pick them up whenever you're ready.")
                    else:
                        body.setText(
                            "You skipped setup — your brain is "
                            "local-only for now. Pick your language, "
                            "voice and permissions whenever you're "
                            "ready.")
            else:
                card.setVisible(incomplete)
        faults = ""
        try:
            from voice_assistant.core.gui_facade import qsettings_get

            faults = str(
                qsettings_get("firstrun/selftest_faults") or "").strip()
        except Exception:  # noqa: BLE001 — no card on a read failure
            faults = ""
        scard = getattr(self, "selftest_card", None)
        if scard is not None:
            scard.setVisible(bool(faults))
        label = getattr(self, "_selftest_faults_label", None)
        if label is not None and faults:
            label.setText(
                f"Self-test found: {faults}. Run it again, or open "
                "Diagnostics for the full Doctor with fixes.")

    def _on_setup_card_snooze(self):
        """Loophole batch 7 (#8): the setup card's "Remind me
        later" — snoozes the card for SNOOZE_DAYS (7), never
        dismisses it. The card returns when the snooze expires
        (or when the wizard runs again). Guarded: a failed write
        leaves the card showing (a snooze that did not stick must
        never look like a dismissal)."""
        try:
            from voice_assistant.core.gui_facade import (
                qsettings_set, setup_pieces as _sp)

            qsettings_set(_sp.SNOOZED_UNTIL_KEY,
                          _sp.snooze_until(days=_sp.SNOOZE_DAYS))
        except Exception:  # noqa: BLE001 — the card stays up
            pass
        try:
            self._refresh_setup_cards()
        except Exception:  # noqa: BLE001 — the card keeps its state
            pass

    def _rerun_selftest_from_home(self):
        """The fix-it card's "Check again": re-run the first-run
        checks off the GUI thread (the same runner the wizard's
        self-test page uses). The worker only computes — the
        QSettings write and the card refresh happen on the GUI
        thread via _finish_home_selftest (QMetaObject.invokeMethod
        with a queued connection, the window_memory_presence
        pattern). A run already in flight is a no-op."""
        if getattr(self, "_home_selftest_running", False):
            return
        self._home_selftest_running = True
        self._home_selftest_failed = None
        btn = getattr(self, "btn_selftest_rerun", None)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText("Checking…")

        def run():
            try:
                from voice_assistant.gui.selftest_checks import (
                    run_first_run_self_tests)

                results = run_first_run_self_tests()
                self._home_selftest_failed = [
                    name for name, ok, _d in results if not ok]
            except Exception:  # noqa: BLE001 — a broken run keeps the pin
                self._home_selftest_failed = None
            QMetaObject.invokeMethod(
                self, "_finish_home_selftest", Qt.QueuedConnection)

        import threading as _threading

        _threading.Thread(target=run, daemon=True).start()

    @Slot()
    def _finish_home_selftest(self):
        """GUI-thread half of _rerun_selftest_from_home: record
        the run (an all-pass run clears the pin — see
        _on_selftest_done), restore the button, refresh the
        cards."""
        failed = getattr(self, "_home_selftest_failed", None)
        self._home_selftest_running = False
        if failed is not None:
            try:
                from voice_assistant.core.gui_facade import qsettings_set

                qsettings_set("firstrun/selftest_faults",
                              "; ".join(failed) if failed else "")
            except Exception:  # noqa: BLE001 — the pin keeps its state
                pass
        btn = getattr(self, "btn_selftest_rerun", None)
        if btn is not None:
            btn.setEnabled(True)
            btn.setText("Check again")
        try:
            self._refresh_setup_cards()
        except Exception:  # noqa: BLE001 — the cards keep their state
            pass

    # -- Settings redesign §4c/§5/§8: the settings-voice strip ----
    _SESSION_GRANT_WORDS = {
        "microphone": "Microphone",
        "camera": "Camera",
        "screen": "Screen watching",
        "files": "Files",
        "browser": "Browser control",
        "home_devices": "Home devices",
    }

    def _speak_strip_announcement(self, text: str):
        """Speak a strip announcement aloud (the proposal was
        spoken, so its answer is spoken too). Guarded: a tap
        that can't speak still applies."""
        try:
            if not text:
                return
            from voice_assistant.core.gui_facade import speak_text as _speak

            _speak(str(text))
        except Exception:  # noqa: BLE001 — the tap already applied
            pass

    def _show_strip_buttons(self, names):
        """Show only the named strip buttons (yes/no/undo/keep/
        off/fix/browser/dismiss); the rest hide."""
        for name in ("yes", "no", "undo", "keep", "off", "fix",
                     "browser", "dismiss"):
            btn = getattr(self, f"btn_settings_voice_{name}", None)
            if btn is not None:
                btn.setVisible(name in names)

    def _refresh_settings_voice_strip(self):
        """Repaint the strip from core/settings_voice state.
        One mode at a time, in priority order: pending proposal
        (Yes/No) → undo window → live session grant(s) → cloud
        fallback repair → browser connect. A queued GUI action
        (open Connect / open a Settings card) is consumed once,
        before any mode — the funnel queues it, the strip
        carries it out. Never raises: a broken read hides the
        strip."""
        strip = getattr(self, "settings_voice_strip", None)
        if strip is None:
            return
        try:
            from voice_assistant.core.gui_facade import (
                settings_voice as _sv)
        except Exception:  # noqa: BLE001 — the strip stays hidden
            return
        try:
            action = _sv.pending_gui_action()
            if action:
                kind = str(action.get("action") or "")
                if kind == "open_connect":
                    self._open_connect_dialog()
                elif kind == "open_settings":
                    title = str(action.get("card") or "")
                    if title:
                        try:
                            self.show_settings_card(title)
                        except Exception:  # noqa: BLE001
                            pass
                _sv.clear_gui_action()
            proposal = _sv.pending_proposal()
            if proposal is not None:
                self.settings_voice_strip_label.setText(
                    f"{proposal.get('message', '')} — say yes, "
                    "or tap Yes.")
                self._show_strip_buttons(("yes", "no"))
                strip.setVisible(True)
                return
            change = _sv.undo_available()
            if change is not None:
                seconds = int(change.get("seconds_left", 0) or 0)
                self.settings_voice_strip_label.setText(
                    f"{change.get('label', 'Setting')} is now "
                    f"{change.get('new', '')} — changed by voice.")
                self.btn_settings_voice_undo.setText(
                    f"Undo ({seconds}s)")
                self._show_strip_buttons(("undo",))
                strip.setVisible(True)
                return
            # Round-2 N4 FAIL 4: only grants that are honestly
            # session-only. A durable grant (e.g. "Always allow",
            # which also holds a session grant so the live mic
            # can open) must not show here — the Settings switch
            # HAS moved, so "the switch hasn't moved" would be
            # false. session_only_grants() filters those out;
            # active_session_grants() itself is unchanged (the
            # voice deny-all path still clears every held grant).
            grants = _sv.session_only_grants()
            if grants:
                words = [self._SESSION_GRANT_WORDS.get(pid, pid)
                         for pid in grants]
                joined = ", ".join(words)
                self.settings_voice_strip_label.setText(
                    f"{joined} {'is' if len(words) == 1 else 'are'} "
                    "on for this session only — the switch in "
                    "Settings hasn't moved.")
                self._show_strip_buttons(("keep", "off"))
                strip.setVisible(True)
                return
            if getattr(self, "_brain_fallback_active", False):
                self.settings_voice_strip_label.setText(
                    "The cloud brain didn't connect — she's "
                    "thinking on this PC. Check the key and "
                    "reconnect in one tap.")
                self._show_strip_buttons(("fix",))
                strip.setVisible(True)
                return
            # First-run onboarding (2026-11-16): a browser tool
            # call refused for lack of a session. The strip offers
            # the one-tap connect — the same dialog the wizard's
            # Browser page shows. Opening the dialog clears the
            # flag (the dialog's connect flow clears it on
            # success); a successful browser call clears it too.
            if _sv.browser_no_session_active():
                self.settings_voice_strip_label.setText(
                    "Your browser isn't connected yet — tap "
                    "'Connect my browser' and I'll set it up. "
                    "One tap, about a minute.")
                self._show_strip_buttons(("browser",))
                strip.setVisible(True)
                return
            # Build 5 (§9.1): the first launch after the update
            # shows ONE quiet offer on the Home strip —
            # "Connect my browser — one tap, about a minute" —
            # dismissible, never repeated. Lowest priority: any
            # live mode above outranks it. The durable flag is
            # set when the offer first PAINTS (not only on
            # dismiss), so a later launch never repeats it; the
            # session flag keeps it visible within this launch
            # until dismissed or connected. A broken connected
            # read fails closed (offer hidden) — the strip must
            # never offer a browser it cannot see the state of.
            if (not _sv.browser_offer_shown()
                    or _sv.browser_offer_active()):
                try:
                    from voice_assistant.core.gui_facade import (
                        browser_enable as _be)

                    connected = bool(_be._is_connected())
                except Exception:  # noqa: BLE001 — fail closed
                    connected = True
                if connected:
                    _sv.clear_browser_offer()
                else:
                    _sv.note_browser_offer_shown()
                    self.settings_voice_strip_label.setText(
                        "Connect my browser — one tap, about a "
                        "minute.")
                    self._show_strip_buttons(("browser", "dismiss"))
                    strip.setVisible(True)
                    return
            strip.setVisible(False)
        except Exception:  # noqa: BLE001 — the strip must never break Home
            try:
                strip.setVisible(False)
            except Exception:  # noqa: BLE001
                pass

    def _on_settings_voice_yes(self):
        try:
            from voice_assistant.core.gui_facade import (
                settings_voice as _sv)

            proposal = _sv.pending_proposal()
            if proposal is None:
                return
            announcement = _sv.confirm_proposal(str(proposal["id"]))
            self._speak_strip_announcement(announcement)
        except Exception:  # noqa: BLE001 — the tap stands
            pass
        self._refresh_settings_voice_strip()

    def _on_settings_voice_no(self):
        try:
            from voice_assistant.core.gui_facade import (
                settings_voice as _sv)

            proposal = _sv.pending_proposal()
            if proposal is None:
                return
            announcement = _sv.deny_proposal(str(proposal["id"]))
            self._speak_strip_announcement(announcement)
        except Exception:  # noqa: BLE001 — the tap stands
            pass
        self._refresh_settings_voice_strip()

    def _on_settings_voice_undo(self):
        try:
            from voice_assistant.core.gui_facade import (
                settings_voice as _sv)

            announcement = _sv.undo_last_change()
            self._speak_strip_announcement(announcement)
        except Exception:  # noqa: BLE001 — the tap stands
            pass
        self._refresh_settings_voice_strip()

    def _on_settings_voice_keep(self):
        """One tap to the exact Settings switch (§8/R1): the
        Permissions card, where the standing switch lives."""
        try:
            self.show_settings_card("Permissions")
        except Exception:  # noqa: BLE001 — the strip stays
            pass

    def _on_settings_voice_off(self):
        try:
            from voice_assistant.core.gui_facade import (
                settings_voice as _sv)

            announcement = _sv.revoke_all_session_grants()
            self._speak_strip_announcement(announcement)
        except Exception:  # noqa: BLE001 — the tap stands
            pass
        self._refresh_settings_voice_strip()

    def _open_connect_dialog(self):
        """Open the Connect dialog (Settings redesign §4a/R3) —
        the always-available repair flow. Guarded against
        double-open: a second request while it's open just
        raises the open one."""
        dlg = getattr(self, "_connect_dialog", None)
        if dlg is not None:
            try:
                dlg.raise_()
                dlg.activateWindow()
                return
            except Exception:  # noqa: BLE001 — rebuild below
                pass
        try:
            from .connect_dialog import ConnectDialog

            dlg = ConnectDialog(self)
            dlg.open_settings_requested.connect(
                lambda: (dlg.accept(),
                         self.show_settings_card("How VYUK thinks")))
            dlg.finished.connect(
                lambda *_: setattr(self, "_connect_dialog", None))
            self._connect_dialog = dlg
            dlg.show()
        except Exception:  # noqa: BLE001 — the repair offer stands
            pass

    def _on_settings_voice_dismiss(self):
        """Dismiss the first-launch browser offer (Build 5,
        §9.1). The durable flag was already set when the offer
        first painted, so dismissing only drops this launch's
        session flag and hides the strip — the offer never
        repeats on a later launch either way. Never raises."""
        try:
            from voice_assistant.core.gui_facade import (
                settings_voice as _sv)

            _sv.clear_browser_offer()
        except Exception:  # noqa: BLE001 — the strip still hides
            pass
        try:
            self._refresh_settings_voice_strip()
        except Exception:  # noqa: BLE001 — the strip still hides
            try:
                self.settings_voice_strip.setVisible(False)
            except Exception:  # noqa: BLE001
                pass

    def _open_browser_connect_dialog(self):
        """Open the browser-connect dialog (first-run onboarding,
        §3) — the wizard's Browser page as a standalone dialog,
        reachable from the strip after a no-session refusal. The
        shared opener (gui/browser_connect_dialog) owns the
        double-open guard and the strip-flag clear."""
        try:
            from .browser_connect_dialog import (
                open_browser_connect_dialog as _open)

            _open(self)
        except Exception:  # noqa: BLE001 — the strip offer stands
            pass

    # -- U9 (progress report): show/hide the report panel ------------
    def show_progress_report(self, report):
        """Show an assembled progress report dict in the home.

        ``report`` is ``core.brain.progress_report.assemble_report``
        output. A missing panel (or a bad report) is a no-op — the
        chat must still work.
        """
        panel = getattr(self, "progress_panel", None)
        if panel is not None:
            panel.show_report(report)

    def hide_progress_report(self):
        """Hide the progress report panel."""
        panel = getattr(self, "progress_panel", None)
        if panel is not None:
            panel.clear()

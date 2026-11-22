"""MainWindow pages mixin (part): activity pages.

Split from gui/window_pages.py (GUI split #8, move only).
_page_shell + reminders + activity + mission-log handlers.
"""

import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .style import (
    ACCENT,
    LINE_SUBTLE,
    RADIUS_CARD,
    SURFACE_2,
    TEXT,
    TEXT_MUTED,
    TYPE_CARD_TITLE,
)


class _PaddedSpinBox(QSpinBox):
    """A spin box that shows two digits ("09", not "9").

    UI rethink Build 2: the fire-at-a-set-time fields read as a
    clock time ("14:00"), so both the hour and the minute pad to
    two digits. Parsing is unchanged — "09" reads back as 9.
    """

    def textFromValue(self, value: int) -> str:
        return f"{int(value):02d}"


class PagesActivityMixin:
    def _page_shell(self, title: str, subtitle: str,
                    kicker: bool = False) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(10)
        t = QLabel(title.upper() if kicker else title)
        if kicker:
            # v5.57 (redo): the mockups' page headers are small
            # letter-spaced capitals, not the big Settings title.
            # Qt stylesheets have no letter-spacing, so the kicker
            # is caps + a small semibold face (per-widget style —
            # PREMIUM_STYLE itself is byte-unchanged).
            # Stage 0 features batch 5 (Light theme, render-found):
            # the sheet used to carry `color: {TEXT}` — the house
            # near-white — so on Light every kicker title was
            # invisible. The colour is dropped here: the label
            # inherits the theme text from the window sheet's base
            # QWidget rule, like the non-kicker title below it.
            t.setObjectName("pageKicker")
            t.setStyleSheet(f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600;")
        else:
            t.setObjectName("pageTitle")
        s = QLabel(subtitle)
        s.setObjectName("muted")
        # v3.0: title/subline sit tight (2px) as one header block; the
        # gap to page content below is unchanged.
        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)
        header.addWidget(t)
        header.addWidget(s)
        layout.addLayout(header)
        layout.addSpacing(6)
        return page, layout

    def _build_reminders_page(self) -> QWidget:
        # UI Slice 1: this builds the Scheduled-tab CONTENT of the
        # Activity page (stack 1), not a standalone page — the
        # page shell (title/subtitle) lives in
        # _build_activity_page, and the subtitle words are kept
        # here as the tab's own hint so the copy survives the
        # move. UX-safe batch structure is preserved: the content
        # lives inside an outer scroll area (widgetResizable) and
        # every widget object name below is unchanged, so the
        # handlers keep working. Data wiring is untouched: the
        # handlers already use the persistent scheduler
        # (scheduler_reminders.json).
        content = QWidget()
        _content_layout = QVBoxLayout(content)
        _content_layout.setContentsMargins(0, 0, 0, 0)
        _content_layout.setSpacing(10)
        # UI rethink Build 2: the Scheduled headline is one line
        # ("2 due today · next 09:00 · 1 already spoken") and the
        # explainer sentence lives behind the "?" beside it, not
        # on the page. The literal stays here so the restructure
        # guards still see the copy in this builder.
        _scheduled_help = ("VYUK will speak these out loud when "
                           "they fire. They're saved on this device, "
                           "so they survive a restart.")
        # Screen 3 Activity: the headline line sits above the
        # scroll area, so it stays put while the list scrolls.
        _content_layout.addWidget(
            self._activity_headline("scheduled",
                                    help_text=_scheduled_help))
        self.reminders_scroll = QScrollArea()
        self.reminders_scroll.setWidgetResizable(True)
        self.reminders_scroll.setFrameShape(QFrame.Shape.NoFrame)
        _reminders_content = QWidget()
        _reminders_layout = QVBoxLayout(_reminders_content)
        _reminders_layout.setContentsMargins(0, 0, 0, 0)
        _reminders_layout.setSpacing(10)
        self.reminders_scroll.setWidget(_reminders_content)
        # Golden-fix D6: QScrollArea.setWidget() flips the content
        # widget's autoFillBackground on — an opaque palette-Window
        # (light) fill that washed out the whole Scheduled tab
        # below the reminders card (near-white headings/hints on a
        # light ground, grey inputs from translucent INPUT_BG over
        # white). Reset both AFTER setWidget, as firstopen /
        # onboarding already do for their scroll bodies, so the
        # dark tab pane shows through.
        _reminders_content.setAutoFillBackground(False)
        self.reminders_scroll.viewport().setAutoFillBackground(False)
        _content_layout.addWidget(self.reminders_scroll, stretch=1)
        layout = _reminders_layout
        # B7F2 (Build 7 Finding 2): the routines section —
        # Morning briefing card first, then Scheduled routines —
        # sits ABOVE the Reminders list. The tab is named
        # Scheduled, so its first-class content is the scheduled
        # items. Pure block move: no widget, size, copy or
        # handler changes; the Reminders and Plans blocks below
        # are untouched.
        layout.addSpacing(8)
        brief_title = QLabel("Morning briefing")
        brief_title.setObjectName("activitySection")
        layout.addWidget(brief_title)
        brief_row = QHBoxLayout()
        brief_lbl = QLabel("Every day at")
        brief_lbl.setObjectName("muted")
        self.spin_brief_hour = QSpinBox()
        self.spin_brief_hour.setRange(0, 23)
        self.spin_brief_hour.setValue(8)
        self.spin_brief_hour.setSuffix(":00")
        btn_brief = QPushButton("Enable daily briefing")
        btn_brief.setObjectName("chipBtn")
        btn_brief.clicked.connect(self._enable_briefing)
        brief_row.addWidget(brief_lbl)
        brief_row.addWidget(self.spin_brief_hour)
        brief_row.addWidget(btn_brief)
        brief_row.addStretch()
        layout.addLayout(brief_row)

        # Presence pack (v2.1): saved routines + their schedules.
        # Rows are (re)built by _refresh_routines() — here we only make
        # the empty container it fills.
        layout.addSpacing(8)
        routines_title = QLabel("Scheduled routines")
        routines_title.setObjectName("activitySection")
        layout.addWidget(routines_title)
        routines_hint = QLabel("Routines VYUK has saved. Scheduled ones run by themselves; any of them can run right now.")
        routines_hint.setObjectName("muted")
        routines_hint.setWordWrap(True)
        layout.addWidget(routines_hint)
        self.routines_container = QWidget()
        self.routines_layout = QVBoxLayout(self.routines_container)
        self.routines_layout.setContentsMargins(0, 0, 0, 0)
        self.routines_layout.setSpacing(6)
        layout.addWidget(self.routines_container)

        # UI rethink Build 2: the reminders section head — the
        # section's name at 16 semibold, Refresh as a quiet text
        # button here (not in the add row), and Remove selected
        # appearing only when a reminder is selected. The old
        # summary label is gone: its line IS the Scheduled
        # headline now (see _refresh_reminders).
        _reminders_head = QHBoxLayout()
        _reminders_head.setContentsMargins(0, 0, 0, 0)
        _reminders_head.setSpacing(6)
        _reminders_title = QLabel("Reminders")
        _reminders_title.setObjectName("activitySection")
        _reminders_head.addWidget(_reminders_title)
        _reminders_head.addStretch()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setObjectName("activityTextAction")
        btn_refresh.clicked.connect(self._refresh_reminders)
        _reminders_head.addWidget(btn_refresh)
        self._reminders_remove_btn = QPushButton("Remove selected")
        self._reminders_remove_btn.setObjectName("activityTextAction")
        self._reminders_remove_btn.setVisible(False)
        self._reminders_remove_btn.clicked.connect(
            self._remove_selected_reminder)
        _reminders_head.addWidget(self._reminders_remove_btn)
        layout.addLayout(_reminders_head)
        card = QFrame()
        card.setObjectName("card")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        self.reminders_list = QListWidget()
        self.reminders_list.setObjectName("activityList")
        self.reminders_list.itemSelectionChanged.connect(
            self._update_reminder_remove_btn)
        c_layout.addWidget(self.reminders_list)
        layout.addWidget(card, stretch=1)

        row = QHBoxLayout()
        self.input_reminder_msg = QLineEdit()
        self.input_reminder_msg.setPlaceholderText("Remind me to…")
        self.input_reminder_min = QSpinBox()
        self.input_reminder_min.setRange(1, 1440)
        self.input_reminder_min.setValue(5)
        self.input_reminder_min.setSuffix(" min")
        btn_set = QPushButton("Add reminder")
        btn_set.setObjectName("primaryBtn")
        btn_set.clicked.connect(self._add_reminder_from_ui)
        row.addWidget(self.input_reminder_msg, stretch=1)
        row.addWidget(self.input_reminder_min)
        row.addWidget(btn_set)
        layout.addLayout(row)
        # S12 (R1): Del removes the selected reminder too.
        self._del_reminder_sc = QShortcut(
            QKeySequence("Del"), self.reminders_list)
        self._del_reminder_sc.setContext(
            Qt.ShortcutContext.WidgetShortcut)
        self._del_reminder_sc.activated.connect(
            self._remove_selected_reminder)
        # S12 (R3): or fire at a set clock time, like the
        # set_reminder skill already can.
        row_at = QHBoxLayout()
        self.chk_reminder_at = QCheckBox("Fire at a set time instead")
        # Two spin boxes, not a QTimeEdit: the stylesheet dresses
        # spin boxes, and a time edit renders as a pale native
        # box on this theme (seen in the S12 render check).
        # UI rethink Build 2: the fields pad to two digits and
        # sit tight around the colon, so the time reads "14:00"
        # instead of "14 : 0". The checkbox row sits under the
        # add-reminder row, not beside it.
        _next_hour = (datetime.datetime.now()
                      + datetime.timedelta(hours=1))
        self.input_reminder_h = _PaddedSpinBox()
        self.input_reminder_h.setRange(0, 23)
        self.input_reminder_h.setValue(_next_hour.hour)
        self.input_reminder_h.setEnabled(False)
        self.input_reminder_h.setMaximumWidth(48)
        self.input_reminder_m = _PaddedSpinBox()
        self.input_reminder_m.setRange(0, 59)
        self.input_reminder_m.setValue(0)
        self.input_reminder_m.setEnabled(False)
        self.input_reminder_m.setMaximumWidth(48)
        self.chk_reminder_at.toggled.connect(
            self.input_reminder_h.setEnabled)
        self.chk_reminder_at.toggled.connect(
            self.input_reminder_m.setEnabled)
        row_at.addWidget(self.chk_reminder_at)
        _clock = QHBoxLayout()
        _clock.setContentsMargins(0, 0, 0, 0)
        _clock.setSpacing(2)
        _clock.addWidget(self.input_reminder_h)
        _colon = QLabel(":")
        _colon.setObjectName("muted")
        _clock.addWidget(_colon)
        _clock.addWidget(self.input_reminder_m)
        row_at.addLayout(_clock)
        row_at.addStretch()
        layout.addLayout(row_at)

        # v5.49 (programme item 32): the Plans resume surface.
        # Plans had NO list UI anywhere before this — the
        # Reminders page is the consistent home (it already lists
        # the planner's routines, the plans' sibling records).
        # Rows are (re)built by _refresh_plans() from
        # planner.plan_progress(): done/total + the resume
        # cursor, and an interrupted plan (a step left in a
        # resumable state, or a partially-done one) gets a Resume
        # button that calls the existing run path — verified-done
        # steps are not re-run (planner.run_plan's contract).
        layout.addSpacing(8)
        plans_title = QLabel("Plans")
        plans_title.setObjectName("activitySection")
        layout.addWidget(plans_title)
        plans_hint = QLabel("Multi-step plans VYUK is working through, with their progress. An interrupted plan can be resumed where it left off — finished steps are not run again.")
        plans_hint.setObjectName("muted")
        plans_hint.setWordWrap(True)
        layout.addWidget(plans_hint)
        self.plans_container = QWidget()
        self.plans_layout = QVBoxLayout(self.plans_container)
        self.plans_layout.setContentsMargins(0, 0, 0, 0)
        self.plans_layout.setSpacing(6)
        layout.addWidget(self.plans_container)
        self._plan_resume_buttons: dict = {}
        self._refresh_plans()
        return content

    def _activity_headline(self, section: str,
                           help_text: str | None = None) -> QWidget:
        """One headline LINE for an Activity section (Build 2).

        A single label at 20 Sora semibold — "In progress — 4 ·
        1 waiting on you" — filled by _update_activity_headline
        from the model line. No giant figure on its own line
        anymore: the count joins its label. With help_text, a
        circled "?" beside the line opens the section's help (the
        Scheduled explainer lives there, not on the page). The
        line rides on self._activity_headlines[section] for the
        refresh to fill. The headline sits outside every scroll
        area, so it stays put while the list below scrolls.
        """
        headlines = getattr(self, "_activity_headlines", None)
        if headlines is None:
            headlines = self._activity_headlines = {}
        line = QLabel("")
        line.setObjectName("activityHeadline")
        line.setWordWrap(False)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(line)
        if help_text:
            # B7F3 (Build 7 Finding 3): the "?" is a circled
            # button with its own objectName — the shared
            # activityTextAction chrome (borderless) made it
            # read as a stray mark beside the headline.
            help_btn = QPushButton("?")
            help_btn.setObjectName("activityHelpButton")
            help_btn.setToolTip("About this section")
            help_btn.clicked.connect(
                lambda _=False, text=help_text:
                self._show_activity_section_help(section, text))
            row.addWidget(help_btn)
        row.addStretch()
        wrap = QWidget()
        wrap.setLayout(row)
        headlines[section] = {"line": line}
        return wrap

    def _show_activity_section_help(self, section: str,
                                    text: str) -> None:
        """The section's help, one sentence in a notice box."""
        try:
            from gui.design_system.dialogs import HudDialog
        except ImportError:
            from voice_assistant.gui.design_system.dialogs import HudDialog  # type: ignore
        titles = {"scheduled": "Scheduled reminders"}
        HudDialog.information(
            self, titles.get(section, "Activity"), text)

    def _update_reminder_remove_btn(self):
        """Remove selected appears only when a reminder is
        selected (Build 2 — same rule as the journal actions):
        never a button that can never be pressed. Guarded
        no-op until the Scheduled tab exists."""
        btn = getattr(self, "_reminders_remove_btn", None)
        lst = getattr(self, "reminders_list", None)
        if btn is None or lst is None:
            return
        show = False
        try:
            for item in lst.selectedItems():
                if item.data(Qt.ItemDataRole.UserRole):
                    show = True
                    break
        except Exception:  # noqa: BLE001 — a broken selection
            show = False  # never shows the button.
        btn.setVisible(show)

    def _refresh_activity_theme(self, resolved: dict) -> None:
        """Re-apply the Activity tab + mission-log sheets (batch 5).

        Both sheets are scoped to their widgets on purpose (see
        the Phase E note at the tab build site — no tab-bar rules
        in the global stylesheet), so the QSS cannot reach them:
        on Light the house tokens left a black pane with
        theme-dark text. The window's appearance pass re-runs
        this from the resolved theme; the build site runs it
        once from the house tokens. Defensive: either widget
        may not exist yet when the theme loads.
        """
        try:
            surface_2 = resolved.get("surface_2", SURFACE_2)
            line = resolved.get("line_subtle", LINE_SUBTLE)
            muted = resolved.get("text_muted", TEXT_MUTED)
            text = resolved.get("text", TEXT)
            accent = resolved.get("accent", ACCENT)
            tabs = getattr(self, "_activity_tabs", None)
            if tabs is not None:
                tabs.setStyleSheet(
                    f"QTabWidget::pane {{ background: {surface_2};"
                    f" border: 1px solid {line};"
                    f" border-radius: {RADIUS_CARD}px; }}"
                    f"QTabBar::tab {{ background: transparent;"
                    f" color: {muted}; padding: 8px 16px; border: none;"
                    f" border-bottom: 2px solid transparent; }}"
                    f"QTabBar::tab:selected {{ color: {text};"
                    f" border-bottom: 2px solid {accent}; }}"
                    f"QTabBar::tab:hover {{ color: {text}; }}")
            detail = getattr(self, "_mission_log_detail", None)
            if detail is not None:
                detail.setStyleSheet(
                    f"QTextEdit {{ background: {surface_2}; color: {text};"
                    f" border: 1px solid {line};"
                    f" border-radius: {RADIUS_CARD}px; }}")
        except Exception:  # noqa: BLE001 — appearance never breaks boot
            pass

    def _build_activity_page(self) -> QWidget:
        # UI Slice 1 (complexity doc CX-01, owner-approved):
        # stack 1 is the Activity page. In progress lists live +
        # paused ledger tasks; Scheduled is the pre-S1 Reminders
        # page content (builders/handlers untouched, now a tab);
        # History lists terminal ledger tasks. Tabs compose
        # existing views; nothing underneath is rewritten. Stack
        # index 1 is unchanged, so every pre-S1 route to
        # Reminders still lands here; the "Go to Reminders"
        # palette alias selects the Scheduled tab explicitly.
        page, layout = self._page_shell("Activity", "What VYUK is doing, what's scheduled, and what's done.", kicker=True)
        self._activity_tabs = QTabWidget()
        self._activity_tabs.setObjectName("activityTabs")
        # Command Deck Phase E (render-found): the app's only
        # tab widget rendered with the default light palette —
        # a white pane on the dark UI with near-invisible tabs.
        # The sheet is scoped to this widget on purpose: the
        # tree pins NO tab-bar rules in the global stylesheet
        # (uis5 test_focus_visible — tab focus keeps Qt's
        # default frame), so the dark pane + cyan selected tab
        # live here, not in style.py. Tokens only, no literals.
        # Stage 0 features batch 5 (Light theme, render-found):
        # the scoped house tokens made the pane a black card
        # with theme-dark text on Light — unreadable. The sheet
        # is built by _refresh_activity_theme, which the window's
        # appearance pass re-runs from the resolved theme.
        self._refresh_activity_theme({
            "surface_2": SURFACE_2, "line_subtle": LINE_SUBTLE,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})
        _in_progress = QWidget()
        _ip_layout = QVBoxLayout(_in_progress)
        _ip_layout.setContentsMargins(0, 0, 0, 0)
        self._activity_headlines = {}
        # Screen 3 Activity: the headline figure sits above the
        # pending row, so the five-second check survives the
        # scroll below it.
        _ip_layout.addWidget(self._activity_headline("in_progress"))
        # §7 consumers batch 7: the recovery offer for paused
        # tasks awaiting approval. Hidden while none is pending
        # (its visibility + count are refreshed by
        # _refresh_activity_tabs); clicking it offers the
        # EXISTING authorization dialog for each pending task
        # in turn, then resolves on a worker thread. No dialog
        # pops at startup on its own — the offer lives here,
        # in the recovery surface, until the user takes it.
        _pending_row = QHBoxLayout()
        _pending_row.setContentsMargins(0, 0, 0, 0)
        _pending_row.setSpacing(6)
        self._activity_pending_btn = QPushButton(
            "Review pending approvals")
        self._activity_pending_btn.setObjectName("chipBtn")
        self._activity_pending_btn.setVisible(False)
        self._activity_pending_btn.clicked.connect(
            self._review_pending_approvals)
        _pending_row.addWidget(self._activity_pending_btn)
        # B15: the recovery offer for paused tasks awaiting
        # an answer. Same shape as the approvals offer above
        # (hidden while none is pending, count refreshed by
        # _refresh_activity_tabs); clicking it offers the
        # question dialog for each pending task in turn.
        # A question is not an approval, so it gets its own
        # button and its own review path — the two queues
        # never mix.
        self._activity_question_btn = QPushButton(
            "Answer pending questions")
        self._activity_question_btn.setObjectName("chipBtn")
        self._activity_question_btn.setVisible(False)
        self._activity_question_btn.clicked.connect(
            self._review_pending_questions)
        _pending_row.addWidget(self._activity_question_btn)
        # B21: the recovery offer for paused tasks awaiting
        # a crash-reconcile decision. Same shape as the
        # approvals and questions offers above (hidden while
        # none is pending, count refreshed by
        # _refresh_activity_tabs); clicking it offers the
        # reconcile dialog for each pending task in turn.
        # A decision is not an answer and not an approval,
        # so it gets its own button and its own review path
        # — the three queues never mix.
        self._activity_reconcile_btn = QPushButton(
            "Decide what happened")
        self._activity_reconcile_btn.setObjectName("chipBtn")
        self._activity_reconcile_btn.setVisible(False)
        self._activity_reconcile_btn.clicked.connect(
            self._review_pending_reconciles)
        _pending_row.addWidget(self._activity_reconcile_btn)
        # B16: the recovery offer for every other interrupted
        # task or goal — the ones with no pending approval and
        # no pending question, which had no offer at all. Same
        # shape as the two offers above (hidden while none is
        # pending, count refreshed by _refresh_activity_tabs);
        # clicking it offers Resume / Not now / Abandon for
        # each in turn. Nothing auto-runs: a restart is not
        # consent to re-run a half-done goal, and a dismissed
        # record is offered again next startup until resumed
        # or abandoned.
        self._activity_recovery_btn = QPushButton(
            "Resume interrupted tasks")
        self._activity_recovery_btn.setObjectName("chipBtn")
        self._activity_recovery_btn.setVisible(False)
        self._activity_recovery_btn.clicked.connect(
            self._review_recovery)
        _pending_row.addWidget(self._activity_recovery_btn)
        _pending_row.addStretch()
        _ip_layout.addLayout(_pending_row)
        self._activity_in_progress_list = QListWidget()
        self._activity_in_progress_list.setObjectName("activityList")
        # Build 2: the list sizes to its rows (capped in the
        # refresh) instead of stretching to fill the pane — the
        # pane hugs the content, and below it is plain background.
        self._activity_in_progress_list.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        _ip_layout.addWidget(self._activity_in_progress_list)
        # §7 consumers batch 9: the journal reconcile surface,
        # on the Activity page (no Home change, no new page).
        # The list fills from the receipt store on every
        # Activity refresh (see _refresh_activity_tabs); rows
        # are attention-ordered journal entries, and Reconcile
        # runs only for a selected dispatched-without-outcome
        # row (the button enables on such a selection, and the
        # engine refuses anything else). Corruption renders
        # one loud row, never a fake list.
        self._activity_journal_label = QLabel(
            "Journal — needs attention")
        _ip_layout.addWidget(self._activity_journal_label)
        self._activity_journal_list = QListWidget()
        self._activity_journal_list.setObjectName("activityList")
        self._activity_journal_list.setMaximumHeight(220)
        self._activity_journal_list.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        _ip_layout.addWidget(self._activity_journal_list)
        _journal_row = QHBoxLayout()
        _journal_row.setContentsMargins(0, 0, 0, 0)
        _journal_row.setSpacing(6)
        # Build 2: Reconcile / Compensate are contextual — they
        # appear only when a journal entry is selected, never as
        # dimmed buttons at the bottom of the page. With nothing
        # selected they do not exist on the page; the hint line
        # below names them while there is something to act on.
        self._activity_journal_reconcile_btn = QPushButton(
            "Reconcile selected")
        self._activity_journal_reconcile_btn.setObjectName("chipBtn")
        self._activity_journal_reconcile_btn.setVisible(False)
        self._activity_journal_reconcile_btn.clicked.connect(
            self._reconcile_selected_journal_entry)
        _journal_row.addWidget(self._activity_journal_reconcile_btn)
        # §7 follow-up (journal Compensate button): runs the
        # selected row's declared compensations through the
        # EXISTING funnel compensate leg — authorization dialog
        # first, leg on a worker thread. Shows only for
        # declared-compensation rows (see
        # _update_journal_compensate_btn); the leg refuses
        # anything else anyway.
        self._activity_journal_compensate_btn = QPushButton(
            "Compensate selected")
        self._activity_journal_compensate_btn.setObjectName("chipBtn")
        self._activity_journal_compensate_btn.setVisible(False)
        self._activity_journal_compensate_btn.clicked.connect(
            self._compensate_selected_journal_entry)
        _journal_row.addWidget(self._activity_journal_compensate_btn)
        _journal_row.addStretch()
        _ip_layout.addLayout(_journal_row)
        self._activity_journal_hint = QLabel(
            "Select entries to reconcile or compensate")
        self._activity_journal_hint.setObjectName("muted")
        self._activity_journal_hint.setWordWrap(True)
        self._activity_journal_hint.setVisible(False)
        _ip_layout.addWidget(self._activity_journal_hint)
        self._activity_journal_list.itemSelectionChanged.connect(
            self._update_journal_reconcile_btn)
        self._activity_journal_list.itemSelectionChanged.connect(
            self._update_journal_compensate_btn)
        self._activity_journal_list.itemDoubleClicked.connect(
            self._show_journal_row_details)
        self._activity_tabs.addTab(_in_progress, "In progress")
        self._activity_tabs.addTab(self._build_reminders_page(), "Scheduled")
        _history = QWidget()
        _h_layout = QVBoxLayout(_history)
        _h_layout.setContentsMargins(0, 0, 0, 0)
        _h_layout.addWidget(self._activity_headline("history"))
        self._activity_history_list = QListWidget()
        self._activity_history_list.setObjectName("activityList")
        self._activity_history_list.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        _h_layout.addWidget(self._activity_history_list)
        self._activity_tabs.addTab(_history, "History")
        # Restore (2026-11-01, owner-delegated): the guide's
        # Mission Log as a fourth Activity tab. Rows go through
        # the single task-state component (gui/task_state.py) —
        # the same ledger the coordinator's mission runs write —
        # so "unknown never shown as success" holds here exactly
        # as on In progress / History. State segments filter by
        # rendered state (Running also covers Needs approval, a
        # live-task variant; Queued / Unknown rows show under
        # All only); the time filter compares the ledger's ISO
        # updated_at (unparseable / missing stamps are included,
        # never hidden by a filter). Selecting a mission shows
        # its evidence trace in the detail pane below the list.
        # Deliberately NOT built (no existing source): OUTBOUND
        # LINK LOG (no per-mission outbound-link record exists)
        # and a REVERT chip (undo is keyed by action_id, not by
        # mission). Live MissionState is not subscribed here:
        # running missions already appear in the ledger, so the
        # log covers them without new wiring.
        _mission_log = QWidget()
        _ml_layout = QVBoxLayout(_mission_log)
        _ml_layout.setContentsMargins(0, 0, 0, 0)
        _ml_layout.addWidget(self._activity_headline("mission_log"))
        _ml_filter_row = QHBoxLayout()
        _ml_filter_row.setContentsMargins(0, 0, 0, 0)
        _ml_filter_row.setSpacing(6)
        self._mission_log_state = "All"
        self._mission_log_days = 0  # 0 = all time
        _ml_state_group = QButtonGroup(_mission_log)
        for _state in ("All", "Running", "Done", "Failed", "Partial"):
            _btn = QPushButton(_state)
            _btn.setObjectName("chipBtn")
            _btn.setCheckable(True)
            _btn.setChecked(_state == "All")
            _btn.clicked.connect(
                lambda _=False, s=_state: self._set_mission_log_filter(s))
            _ml_state_group.addButton(_btn)
            _ml_filter_row.addWidget(_btn)
        _ml_filter_row.addStretch()
        _ml_time_label = QLabel("Time")
        _ml_time_label.setObjectName("muted")
        _ml_filter_row.addWidget(_ml_time_label)
        self._mission_log_time = QComboBox()
        self._mission_log_time.addItems(
            ["All time", "Last 7 days", "Last 30 days"])
        self._mission_log_time.currentTextChanged.connect(
            self._set_mission_log_time_filter)
        _ml_filter_row.addWidget(self._mission_log_time)
        _ml_layout.addLayout(_ml_filter_row)
        self._mission_log_list = QListWidget()
        self._mission_log_list.setObjectName("activityList")
        self._mission_log_list.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self._mission_log_list.currentItemChanged.connect(
            self._on_mission_log_selected)
        _ml_layout.addWidget(self._mission_log_list)
        self._mission_log_detail = QTextEdit()
        self._mission_log_detail.setReadOnly(True)
        self._mission_log_detail.setObjectName("missionLogDetail")
        # Dark pane, same scoped-stylesheet precedent as the
        # Activity tabs above: a bare QTextEdit renders with
        # Qt's default light palette (pale text on white — the
        # trace would be unreadable). Tokens only, no literals.
        # Batch 5: the sheet lives in _refresh_activity_theme
        # (re-run from the resolved theme on appearance change).
        self._refresh_activity_theme({
            "surface_2": SURFACE_2, "line_subtle": LINE_SUBTLE,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})
        self._mission_log_detail.setPlaceholderText(
            "Select a mission to see its evidence trace.")
        _ml_layout.addWidget(self._mission_log_detail, stretch=1)
        self._activity_tabs.addTab(_mission_log, "Mission Log")
        # UI rethink Build 2: the pane hugs the current tab's
        # content — the tabs do not stretch to the bottom of the
        # window, and below them is plain background. The height
        # follows the current tab (see _fit_activity_tabs_height).
        layout.addWidget(self._activity_tabs)
        layout.addStretch(1)
        self._activity_tabs.currentChanged.connect(
            self._on_activity_tab_changed)
        self._refresh_activity_tabs()
        return page

    def _on_activity_tab_changed(self, _index):
        """Tab switched (Build 2): fit now, and once more on the
        next turn when the new page's hints have settled."""
        try:
            self._fit_activity_tabs_height()
        except Exception:  # noqa: BLE001 — cosmetic
            pass
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(
                0, self._fit_activity_tabs_height_later)
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _fit_activity_tabs_height_later(self):
        """Re-fit on the next event-loop turn (Build 2): the
        lists' size hints settle after their layout runs, so a
        fit taken inside the refresh can measure a stale hint.
        Never raises — cosmetic, and the window may be gone."""
        try:
            self._fit_activity_tabs_height()
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _fit_activity_tabs_height(self):
        """Cap the tab widget at the current tab's content height.

        QTabWidget.sizeHint is the max over ALL pages, so a short
        tab (History, three rows) would still draw a tall pane
        without this. The cap is the current page's sizeHint plus
        the tab bar's share (the sizeHint-to-max-page delta, so
        no number is hand-copied). The layout may still shrink
        the widget below the cap when the window is short — the
        lists scroll inside their own caps then. Guarded no-op
        until the tabs exist.
        """
        tabs = getattr(self, "_activity_tabs", None)
        if tabs is None:
            return
        page = tabs.currentWidget()
        if page is None:
            return
        try:
            page_hints = [tabs.widget(i).sizeHint().height()
                          for i in range(tabs.count())]
            delta = tabs.sizeHint().height() - max(page_hints)
            tabs.setMaximumHeight(page.sizeHint().height() + delta)
        except Exception:  # noqa: BLE001 — a broken hint never
            pass  # breaks the page; the pane just stretches.

    def _select_activity_tab(self, name: str) -> None:
        # Palette alias support: land on Activity with a chosen
        # tab (e.g. "Scheduled" for the pre-S1 Reminders route).
        # No-op when the tabs do not exist yet — never crashes
        # a navigation action.
        tabs = getattr(self, "_activity_tabs", None)
        if tabs is None:
            return
        for i in range(tabs.count()):
            if tabs.tabText(i) == name:
                tabs.setCurrentIndex(i)
                return

    def _set_mission_log_filter(self, state: str) -> None:
        # Mission Log state segment: refill from the same ledger
        # read _refresh_activity_tabs already performs.
        self._mission_log_state = state
        self._refresh_activity_tabs()

    def _set_mission_log_time_filter(self, text: str) -> None:
        self._mission_log_days = {"Last 7 days": 7,
                                  "Last 30 days": 30}.get(text, 0)
        self._refresh_activity_tabs()

    def _on_mission_log_selected(self, current, previous) -> None:
        # Selection shows the mission's evidence trace (the
        # task-state details_text) in the detail pane. Rows
        # without a stored rendering (empty / unavailable rows)
        # clear the pane instead of showing a stale trace.
        detail = getattr(self, "_mission_log_detail", None)
        if detail is None:
            return
        rendered = None
        if current is not None:
            try:
                rendered = current.data(Qt.ItemDataRole.UserRole)
            except Exception:  # noqa: BLE001 — no rendering: clear
                rendered = None
        if isinstance(rendered, dict):
            detail.setPlainText(rendered.get("details_text", ""))
        else:
            detail.clear()

"""MainWindow pages mixin (part): activity pages.

Split from gui/window_pages.py (GUI split #8, move only).
_page_shell + reminders + activity + mission-log handlers.
"""

import datetime

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QButtonGroup,
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
from .design_system.backdrop import HudBackdrop, glass_tile
from .design_system.flowlayout import FlowLayout
from .design_system.mission import ActionTrace, MissionRail
from .design_system.panels import HudPanel
from .design_system.switches import HudSwitch, toggle_row
from .design_system.tabs import HudTabs

from .style import (
    ACCENT,
    LINE_SUBTLE,
    RADIUS_CARD,
    SURFACE_2,
    TEXT,
    TEXT_MUTED,
    TYPE_CARD_TITLE,
)


class _BackdropFitter(QObject):
    """Keep a HudBackdrop sized to its page (resize events).

    The backdrop is a plain child of the page, lowered behind
    the content; the page's layout never sizes it, so the
    fitter re-sizes it on every page resize. It never consumes
    the event — it only mirrors the geometry. ``on_resize``
    (optional) runs after the mirror — the Activity page uses
    it to re-fit the tab pane, whose fill height follows the
    window height.
    """

    def __init__(self, backdrop, on_resize=None):
        super().__init__(backdrop)
        self._backdrop = backdrop
        self._on_resize = on_resize

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.Resize:
            try:
                self._backdrop.setGeometry(watched.rect())
            except Exception:  # noqa: BLE001 — cosmetic
                pass
            if self._on_resize is not None:
                try:
                    self._on_resize()
                except Exception:  # noqa: BLE001 — cosmetic
                    pass
        return super().eventFilter(watched, event)


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
                    kicker: bool = True) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(10)
        t = QLabel(title.upper() if kicker else title)
        if kicker:
            # Phase 0: kicker=True is the standard now (the
            # decided header style) — the default is True,
            # every caller passes it explicitly too.
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
        # Phase 1: the Reminders list becomes a TIMELINE — the
        # spine + day-grouped rows live in the card below, built
        # by _rebuild_scheduled_timeline(). The old QListWidget
        # stays, hidden, as the selection backing store for
        # _remove_selected_reminder (gui/window_shell_memoryio.py,
        # outside this build's ownership): it holds one item per
        # reminder with the label on UserRole, and a timeline
        # row's click syncs its current item — so Remove selected
        # and Del keep working against the same identities.
        # The timeline's own Del shortcut rides on the timeline
        # widget (WidgetShortcut: it fires while a timeline row
        # holds focus, never from anywhere else on the window).
        self.reminders_list = QListWidget(content)
        self.reminders_list.setObjectName("activityList")
        self.reminders_list.setVisible(False)
        self.reminders_list.itemSelectionChanged.connect(
            self._update_reminder_remove_btn)
        self._timeline_selected_label = None
        card = QFrame()
        card.setObjectName("card")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        self._scheduled_timeline = QWidget()
        self._scheduled_timeline.setObjectName("scheduledTimeline")
        _tl_layout = QHBoxLayout(self._scheduled_timeline)
        _tl_layout.setContentsMargins(0, 0, 0, 0)
        _tl_layout.setSpacing(8)
        from .design_system.timeline import TimelineSpine
        self._scheduled_timeline_spine = TimelineSpine(
            self._scheduled_timeline)
        _tl_layout.addWidget(self._scheduled_timeline_spine, 0)
        self._scheduled_timeline_body = QWidget()
        self._scheduled_timeline_layout = QVBoxLayout(
            self._scheduled_timeline_body)
        self._scheduled_timeline_layout.setContentsMargins(0, 0, 0, 0)
        self._scheduled_timeline_layout.setSpacing(6)
        _tl_layout.addWidget(self._scheduled_timeline_body, 1)
        c_layout.addWidget(self._scheduled_timeline)
        layout.addWidget(card, stretch=1)
        self._del_timeline_sc = QShortcut(
            QKeySequence("Del"), self._scheduled_timeline)
        self._del_timeline_sc.setContext(
            Qt.ShortcutContext.WidgetShortcut)
        self._del_timeline_sc.activated.connect(
            self._remove_selected_reminder)

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
        self.chk_reminder_at = HudSwitch(text="Fire at a set time instead")
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
        # Phase 0: the toggle row is kept as an attribute — the
        # switch itself sits inside it, so geometry assertions
        # (the row sits under the add row) read the row, not the
        # switch's position inside its own row.
        self._reminder_at_row = toggle_row(self.chk_reminder_at)
        row_at.addWidget(self._reminder_at_row)
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
        """Remove selected appears only when a timeline row is
        selected (Build 2 — same rule as the journal actions):
        never a button that can never be pressed. The selection
        is the timeline's (_timeline_selected_label); the hidden
        reminders_list mirrors it for _remove_selected_reminder.
        Guarded no-op until the Scheduled tab exists."""
        btn = getattr(self, "_reminders_remove_btn", None)
        if btn is None:
            return
        btn.setVisible(bool(
            getattr(self, "_timeline_selected_label", None)))

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
            text = resolved.get("text", TEXT)
            # Phase 1 (B-A08): the resolved theme is kept for the
            # glass tiles (rail host + approval cards); the
            # backdrop re-resolves from it too.
            try:
                self._activity_theme_resolved = dict(resolved)
            except Exception:  # noqa: BLE001 — cosmetic
                pass
            backdrop = getattr(self, "_activity_backdrop", None)
            if backdrop is not None:
                try:
                    backdrop.set_theme(resolved)
                except Exception:  # noqa: BLE001 — cosmetic
                    pass
            host = getattr(self, "_mission_rail_host", None)
            if host is not None:
                try:
                    # Look-fix 2026-11-24: the glass tile IS the
                    # backdrop's grid, blurred — it only belongs
                    # where the backdrop actually shows a grid.
                    # Memory + Activity are vignette now (no grid),
                    # so the tile painted a checkerboard inside the
                    # rail card on a flat page. Gate it on the
                    # backdrop's effective layers; without a grid
                    # the panel paints its translucent fill alone.
                    bd = getattr(self, "_activity_backdrop", None)
                    grid_on = (bd is not None
                               and bd.effective_layers()[0])
                    host.set_glass_tile(
                        glass_tile(resolved) if grid_on else None)
                except Exception:  # noqa: BLE001 — cosmetic
                    pass
            tabs = getattr(self, "_activity_tabs", None)
            if tabs is not None:
                # Phase 1 (B-A08): the pane is transparent — the
                # page is transparent over the shell and the lists
                # are transparent over the backdrop behind it. An
                # opaque pane would hide the backdrop entirely.
                # The detail pane keeps its own opaque fill (below).
                tabs.setStyleSheet(
                    "QTabWidget::pane { background: transparent;"
                    f" border: 1px solid {line};"
                    f" border-radius: {RADIUS_CARD}px; }}")
                # Phase 0: the tab rules live on the HudTabs bar
                # itself (gui/design_system/tabs.py) — the pane
                # rule stays here, the bar re-resolves from the
                # same theme. (Before Phase 0 the QTabBar::tab
                # rules lived in this sheet; moving them to the
                # component is the adoption — test_commanddeck_
                # phaseE's tab-rules test moved with them.)
                bar = tabs.tabBar()
                setter = getattr(bar, "set_theme", None)
                if setter is not None:
                    setter(resolved)
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
        # Phase 1 (B-A08 + §10): the page is transparent over the
        # shell so the backdrop shows through; the lists are
        # transparent over the backdrop (their viewports are
        # reset below); the detail pane keeps its opaque fill.
        # Backdrop intensity is vignette (flat bg + vignette, no
        # grid, no scanlines) — look-fix 2026-11-24, the checkered
        # grid read as a checkerboard and the owner rejected it.
        page.setAutoFillBackground(False)
        self._activity_backdrop = HudBackdrop(page, intensity="vignette")
        self._activity_backdrop.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._activity_backdrop.lower()
        self._activity_backdrop_fitter = _BackdropFitter(
            self._activity_backdrop,
            on_resize=self._fit_activity_tabs_height)
        page.installEventFilter(self._activity_backdrop_fitter)
        self._activity_backdrop.setGeometry(page.rect())
        self._activity_tabs = QTabWidget()
        self._activity_tabs.setObjectName("activityTabs")
        self._activity_tabs.setTabBar(HudTabs())
        # Phase 0: the tab bar is a HudTabs (the shared component)
        # — set before any addTab call, so every tab is a HudTabs
        # tab from the start. The tab rules live on the bar itself
        # (tabs.py); _refresh_activity_theme keeps only the pane
        # rule and re-resolves the bar from the theme.
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
        _in_progress.setAutoFillBackground(False)
        _ip_layout = QVBoxLayout(_in_progress)
        _ip_layout.setContentsMargins(0, 0, 0, 0)
        self._activity_headlines = {}
        # Activity empty-state fix (2026-11-26): the two idle
        # stretches. They take factor 1 only while the In
        # progress tab is idle (set by _refresh_activity_tabs);
        # with live work they are 0 and the layout is exactly
        # as before. The first centres the rail in the filled
        # pane; the second anchors the journal + footer at the
        # pane's bottom. Without them the filled pane would
        # hold its short content at the top and the same void
        # below it, just inside the border.
        self._activity_in_progress_layout = _ip_layout
        self._activity_idle_stretch_top = None
        self._activity_idle_stretch_mid = None
        # Screen 3 Activity: the headline figure sits above the
        # pending row, so the five-second check survives the
        # scroll below it.
        _ip_layout.addWidget(self._activity_headline("in_progress"))
        self._activity_idle_stretch_top = _ip_layout.count()
        _ip_layout.addStretch(0)
        # Phase 1 (§3): the MissionRail at the top of In progress,
        # inside a HudPanel host — one of the page's only two
        # raised surfaces (the other is the inline approval
        # card). The rail is fed by the producer bridge in
        # _refresh_activity_tabs (ledger's newest live record);
        # Stop goes to the coordinator's cancel path, Pause to
        # its pause path (the seam exists — pause_task).
        self._mission_rail_host = HudPanel(glass=True)
        self._mission_rail = MissionRail()
        # Look-fix 2 (2026-11-24): theme the rail's SegmentBar
        # from the stored resolved theme now (the page builds
        # before the first appearance pass); the appearance pass
        # re-drives it on every theme change.
        try:
            _resolved = getattr(self, "_resolved_theme", None)
            if isinstance(_resolved, dict):
                self._mission_rail.set_theme(_resolved)
        except Exception:  # noqa: BLE001 — appearance never breaks boot
            pass
        self._mission_rail_host.content_layout().addWidget(
            self._mission_rail)
        _ip_layout.addWidget(self._mission_rail_host)
        self._mission_rail_task_id = None
        self._mission_rail.stop_requested.connect(
            self._on_mission_rail_stop)
        self._mission_rail.pause_requested.connect(
            self._on_mission_rail_pause)
        # §7 consumers batch 7: the recovery offer for paused
        # tasks awaiting approval. Hidden while none is pending
        # (its visibility + count are refreshed by
        # _refresh_activity_tabs); clicking it offers the
        # EXISTING authorization dialog for each pending task
        # in turn, then resolves on a worker thread. No dialog
        # pops at startup on its own — the offer lives here,
        # in the recovery surface, until the user takes it.
        # Phase 1 (A-07): the pending row wraps whole at narrow
        # widths (FlowLayout, the shared component) instead of
        # overflowing — the four buttons never clip mid-glyph.
        _pending_wrap = QWidget()
        _pending_row = FlowLayout(_pending_wrap, spacing=6)
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
        _ip_layout.addWidget(_pending_wrap)
        self._activity_in_progress_list = QListWidget()
        self._activity_in_progress_list.setObjectName("activityList")
        self._activity_in_progress_list.viewport().setAutoFillBackground(
            False)
        # Build 2: the list sizes to its rows (capped in the
        # refresh) instead of stretching to fill the pane — the
        # pane hugs the content, and below it is plain background.
        self._activity_in_progress_list.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        _ip_layout.addWidget(self._activity_in_progress_list)
        self._activity_idle_stretch_mid = _ip_layout.count()
        _ip_layout.addStretch(0)
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
        self._activity_journal_label.setObjectName("needsAttentionHeader")
        _ip_layout.addWidget(self._activity_journal_label)
        self._activity_journal_list = QListWidget()
        self._activity_journal_list.setObjectName("activityList")
        self._activity_journal_list.viewport().setAutoFillBackground(
            False)
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
        # Phase 1 (A-06): the journal footer names the reconciled
        # count, so a cleared row reads as progress, not loss.
        self._activity_journal_footer = QLabel("")
        self._activity_journal_footer.setObjectName("muted")
        self._activity_journal_footer.setWordWrap(True)
        self._activity_journal_footer.setVisible(False)
        _ip_layout.addWidget(self._activity_journal_footer)
        self._activity_journal_list.itemSelectionChanged.connect(
            self._update_journal_reconcile_btn)
        self._activity_journal_list.itemSelectionChanged.connect(
            self._update_journal_compensate_btn)
        self._activity_journal_list.itemDoubleClicked.connect(
            self._show_journal_row_details)
        # The in-progress tab's content (hero + list + journal)
        # is taller than the window when an approval card is
        # present (~738 px vs ~557 px available), so the
        # QVBoxLayout compressed the list and the journal painted
        # over the approval card's lower half (parent by-eye fix,
        # 2026-11-24). The tab scrolls instead: the content keeps
        # its full height and the journal sits below the card,
        # reachable by scrolling. Golden-fix D6: setWidget()
        # flips the content widget's autoFillBackground on — an
        # opaque light fill that would wash out the tab — so both
        # the content and the viewport are reset AFTER setWidget,
        # exactly as the Scheduled tab does.
        _ip_scroll = QScrollArea()
        _ip_scroll.setObjectName("activityInProgressScroll")
        _ip_scroll.setWidgetResizable(True)
        _ip_scroll.setFrameShape(QFrame.Shape.NoFrame)
        _ip_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _ip_scroll.setWidget(_in_progress)
        _in_progress.setAutoFillBackground(False)
        _ip_scroll.viewport().setAutoFillBackground(False)
        self._activity_tabs.addTab(_ip_scroll, "In progress")
        self._activity_tabs.addTab(self._build_reminders_page(), "Scheduled")
        _history = QWidget()
        _history.setAutoFillBackground(False)
        _h_layout = QVBoxLayout(_history)
        _h_layout.setContentsMargins(0, 0, 0, 0)
        _h_layout.addWidget(self._activity_headline("history"))
        # Phase 1 (§3, A-11): History's OWN filter set — All /
        # Done / Failed / Partial / Cancelled, with counts from
        # the same ledger read. No Running segment: History holds
        # terminal tasks only. A time window sits beside them,
        # same as the Mission Log.
        _hist_filter_row = QHBoxLayout()
        _hist_filter_row.setContentsMargins(0, 0, 0, 0)
        _hist_filter_row.setSpacing(6)
        self._history_state = "All"
        self._history_days = 0  # 0 = all time
        _hist_state_group = QButtonGroup(_history)
        for _state in ("All", "Done", "Failed", "Partial", "Cancelled"):
            _btn = QPushButton(_state)
            _btn.setObjectName(f"historyFilter-{_state}")
            _btn.setCheckable(True)
            _btn.setChecked(_state == "All")
            _btn.clicked.connect(
                lambda _=False, s=_state: self._set_history_filter(s))
            _hist_state_group.addButton(_btn)
            _hist_filter_row.addWidget(_btn)
        _hist_filter_row.addStretch()
        _hist_time_label = QLabel("Time")
        _hist_time_label.setObjectName("muted")
        _hist_filter_row.addWidget(_hist_time_label)
        self._history_time = QComboBox()
        self._history_time.addItems(
            ["All time", "Last 7 days", "Last 30 days"])
        self._history_time.currentTextChanged.connect(
            self._set_history_time_filter)
        _hist_filter_row.addWidget(self._history_time)
        _h_layout.addLayout(_hist_filter_row)
        self._activity_history_list = QListWidget()
        self._activity_history_list.setObjectName("activityList")
        self._activity_history_list.viewport().setAutoFillBackground(
            False)
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
        _mission_log.setAutoFillBackground(False)
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
        self._mission_log_list.viewport().setAutoFillBackground(False)
        self._mission_log_list.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self._mission_log_list.currentItemChanged.connect(
            self._on_mission_log_selected)
        _ml_layout.addWidget(self._mission_log_list)
        # Phase 1 (§3, A-13): the detail pane gains an ActionTrace
        # ABOVE the evidence text — the record's steps mapped to
        # trace rows (plan / action / outcome / verification), with
        # absolute paths stripped. The evidence text stays below;
        # the detail pane keeps its opaque fill (B-A08) — only
        # the lists are transparent.
        _detail_wrap = QWidget()
        _detail_layout = QVBoxLayout(_detail_wrap)
        _detail_layout.setContentsMargins(0, 0, 0, 0)
        _detail_layout.setSpacing(6)
        self._mission_log_trace = ActionTrace()
        self._mission_log_trace.setObjectName("missionLogTrace")
        _detail_layout.addWidget(self._mission_log_trace)
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
        _detail_layout.addWidget(self._mission_log_detail, stretch=1)
        _ml_layout.addWidget(_detail_wrap, stretch=1)
        self._activity_tabs.addTab(_mission_log, "Mission Log")
        # UI rethink Build 2: the pane hugs the current tab's
        # content — the tabs do not stretch to the bottom of the
        # window, and below them is plain background. The height
        # follows the current tab (see _fit_activity_tabs_height).
        # Activity empty-state fix (2026-11-26): the fit owns
        # who takes the leftover space — the bottom spacer by
        # default (the hug), the tabs widget while the In
        # progress tab is idle (the fill). The spacer's index
        # is recorded so the fit can toggle its factor.
        layout.addWidget(self._activity_tabs)
        self._activity_bottom_spacer_index = layout.count()
        layout.addStretch(1)
        self._activity_tabs_filled = False
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

        Activity empty-state fix (2026-11-26): when the In
        progress tab is idle (no live rows, no attention rows)
        the pane FILLS the window instead of hugging its short
        content — which is what left the page ~70% empty black
        space. The fill is done with stretch factors, not with
        a computed height: the tabs widget takes factor 1 and
        the bottom spacer factor 0, with the cap lifted, so the
        layout gives the tabs the whole available height and a
        window resize re-fills on its own (no height arithmetic
        to go stale). Other tabs, and In progress with live
        work, keep the hug exactly as before: the tabs take
        factor 0, the spacer factor 1, and the cap binds them
        to the content. The factors are only toggled when the
        idle state changes, so a resize-driven re-fit is a
        no-op instead of a layout loop.
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
            cap = page.sizeHint().height() + delta
            _idle = False
            if tabs.currentIndex() == 0:
                _ip = getattr(self, "_activity_in_progress_list",
                              None)
                _jl = getattr(self, "_activity_journal_list", None)
                _idle = (_ip is not None and _jl is not None
                         and _ip.count() == 0 and _jl.count() == 0)
            if _idle:
                if tabs.maximumHeight() != 16777215:
                    tabs.setMaximumHeight(16777215)
            elif tabs.maximumHeight() != cap:
                tabs.setMaximumHeight(cap)
            if getattr(self, "_activity_tabs_filled", False) != _idle:
                _page_layout = tabs.parentWidget().layout()
                if _page_layout is not None:
                    _page_layout.setStretchFactor(
                        tabs, 1 if _idle else 0)
                    _spacer = getattr(
                        self, "_activity_bottom_spacer_index", None)
                    if _spacer is not None:
                        _page_layout.setStretch(
                            _spacer, 0 if _idle else 1)
                self._activity_tabs_filled = _idle
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

    def _set_history_filter(self, state: str) -> None:
        # History state segment: refill from the same ledger
        # read _refresh_activity_tabs already performs.
        self._history_state = state
        self._refresh_activity_tabs()

    def _set_history_time_filter(self, text: str) -> None:
        self._history_days = {"Last 7 days": 7,
                              "Last 30 days": 30}.get(text, 0)
        self._refresh_activity_tabs()

    def _set_mission_log_filter(self, state: str) -> None:
        # Mission Log state segment: refill from the same ledger
        # read _refresh_activity_tabs already performs.
        self._mission_log_state = state
        self._refresh_activity_tabs()

    def _set_mission_log_time_filter(self, text: str) -> None:
        self._mission_log_days = {"Last 7 days": 7,
                                  "Last 30 days": 30}.get(text, 0)
        self._refresh_activity_tabs()

    def _fill_mission_log_trace(self, trace, rendered) -> None:
        """Build the Mission Log detail trace from one record's
        steps (Phase 1, §3/A-13).

        Plan step → "plan", tool run → "action", outcome →
        "outcome", verification → "verification". A step without
        an outcome renders as running (for a live record) or not
        run (for a finished one) — honestly, never as success.
        Absolute paths are stripped, as the ledger error lines
        already do. ``rendered`` is the stored dict: the render
        plus the record's steps / outcomes / verification.
        """
        try:
            from .room_activity import strip_absolute_paths
        except Exception:  # noqa: BLE001 — paths stay raw then
            strip_absolute_paths = lambda s: s
        trace.clear()
        steps = rendered.get("steps") or []
        outcomes = rendered.get("outcomes") or []
        verification = rendered.get("verification") or []
        live = (rendered.get("state") in
                ("Running", "Needs approval", "Verifying"))
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            label = (str(step.get("label") or "")
                     or str(step.get("tool") or "")
                     or f"step {i + 1}")
            tool = str(step.get("tool") or "")
            trace.append_step("plan", strip_absolute_paths(label))
            outcome = (outcomes[i] if i < len(outcomes)
                       and isinstance(outcomes[i], dict) else None)
            if outcome is not None:
                trace.append_step(
                    "action", strip_absolute_paths(tool or label))
                status = str(outcome.get("status") or "")
                message = str(outcome.get("message") or "")
                text = f"{label} — {status}"
                if message:
                    text += f": {message}"
                trace.append_step("outcome",
                                  strip_absolute_paths(text))
            else:
                trace.append_step(
                    "outcome",
                    strip_absolute_paths(
                        f"{label} — "
                        f"{'running' if live else 'not run'}"))
        for verdict in verification:
            if not isinstance(verdict, dict):
                continue
            vlabel = (str(verdict.get("label") or "")
                      or str(verdict.get("tool") or ""))
            vtext = f"{vlabel} — {verdict.get('verdict', '')}"
            vreason = str(verdict.get("reason") or "")
            if vreason:
                vtext += f": {vreason}"
            trace.append_step("verification",
                              strip_absolute_paths(vtext))

    def _on_mission_log_selected(self, current, previous) -> None:
        # Selection shows the mission's evidence trace (the
        # task-state details_text) in the detail pane, with the
        # ActionTrace above it built from the record's steps.
        # Rows without a stored rendering (empty / unavailable
        # rows) clear the pane instead of showing a stale trace.
        detail = getattr(self, "_mission_log_detail", None)
        trace = getattr(self, "_mission_log_trace", None)
        rendered = None
        if current is not None:
            try:
                rendered = current.data(Qt.ItemDataRole.UserRole)
            except Exception:  # noqa: BLE001 — no rendering: clear
                rendered = None
        if isinstance(rendered, dict):
            if trace is not None:
                self._fill_mission_log_trace(trace, rendered)
            if detail is not None:
                detail.setPlainText(rendered.get("details_text", ""))
        else:
            if trace is not None:
                trace.clear()
            if detail is not None:
                detail.clear()

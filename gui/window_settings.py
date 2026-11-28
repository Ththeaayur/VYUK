"""MainWindow settings mixin: the Settings page builder and
the appearance / audio-preference handlers that live with it.

v5.50 (programme item 43): split out of gui/main_window.py,
which remains as the re-export shim. Bodies moved verbatim;
behaviour is unchanged.
"""
from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QSettings,
    Qt,
    QTime,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from voice_assistant.core.gui_facade import confirm_gate
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import (
    gate_custom_accent, migrate_theme_name)
from voice_assistant.core.gui_facade import (
    LANGUAGES,
    TRANSCRIPTION_PROFILES,
)
from voice_assistant.core.gui_facade import VERSION
from .design_system.panels import HudPanel
from .design_system.switches import HudSwitch
from .style import (
    build_stylesheet,
    derive_theme,
    TYPE_BODY,
    TYPE_CARD_TITLE,
    # Command Deck Phase E: Settings carries no colour literals of
    # its own — every value below is a style token.
    ACCENT,
    TEXT,
    TEXT_MUTED,
    INPUT_BG,
    INPUT_BG_FOCUS,
    LINE_SUBTLE,
    FOCUS_BORDER,
    SELECTION_BG,
    RADIUS_BTN,
)
from .style import THEMES as STYLE_THEMES
from .visualizer import (
    THEMES,
)
from .window_widgets import (
    SHORTCUT_ROWS,
    _KOKORO_VOICE_ROSTER,
)

from .window_settings_think import SettingsThinkMixin as _SettingsThinkMixin
from .window_settings_voice import SettingsVoiceMixin as _SettingsVoiceMixin
from .window_settings_groups import SettingsGroupsMixin as _SettingsGroupsMixin
from .window_settings_advbrain import SettingsAdvBrainMixin as _SettingsAdvBrainMixin
from .window_settings_advcards import SettingsAdvCardsMixin as _SettingsAdvCardsMixin
from .window_settings_appearance import SettingsAppearanceMixin as _SettingsAppearanceMixin
from .window_settings_privacy import SettingsPrivacyMixin as _SettingsPrivacyMixin

class _SettingsWheelFilter(QObject):
    """Phase 0 field fix (Settings scroll): Qt routes a wheel event to
    the widget under the cursor, and combo boxes, sliders and spin
    boxes ACCEPT it (changing their own value) instead of letting it
    reach the page scroll area — so scrolling while hovered over any
    of the Settings page's many dropdowns/sliders did nothing to the
    page and silently changed a setting. This filter, installed at
    the Settings page level on every such control in the page's
    content, forwards an unfocused control's wheel event to the page
    scroll area's viewport (same deltas, so smooth/high-resolution
    scrolling behaves identically) and swallows the original, so the
    control's value is untouched and the page scrolls. A control that
    HAS keyboard focus keeps its wheel (the user deliberately put
    focus there). Scrollable text/list controls keep their internal
    scroll first and only forward once at their scroll limit in the
    wheel's direction — otherwise a short list/text box would trap
    the wheel even when it has nothing left to scroll.
    """

    def __init__(self, scroll: QScrollArea):
        super().__init__()
        self._scroll = scroll

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if event.type() != QEvent.Type.Wheel:
            return False
        if isinstance(watched, QAbstractScrollArea):
            bar = watched.verticalScrollBar()
            dy = event.pixelDelta().y() or event.angleDelta().y()
            if dy < 0 and bar.value() < bar.maximum():
                return False
            if dy > 0 and bar.value() > bar.minimum():
                return False
        elif watched.hasFocus():
            return False
        viewport = self._scroll.viewport()
        gpos = watched.mapToGlobal(watched.rect().center())
        fwd = QWheelEvent(
            QPointF(viewport.mapFromGlobal(gpos)), gpos,
            event.pixelDelta(), event.angleDelta(), event.buttons(),
            event.modifiers(), event.phase(), event.inverted(),
            event.source())
        QApplication.sendEvent(viewport, fwd)
        return True

from .window_settings_advcards import _about_hint_text  # refactor P1 step 5 (moved verbatim)
from .window_settings_voice import _SoundsEnabledStore  # refactor P1 step 5 (moved verbatim)


class SettingsMixin(_SettingsThinkMixin, _SettingsVoiceMixin, _SettingsGroupsMixin, _SettingsAdvBrainMixin, _SettingsAdvCardsMixin, _SettingsAppearanceMixin, _SettingsPrivacyMixin):

    def _settings_card(self, parent_layout: QVBoxLayout, title: str, hint: str | None = None):
        # Phase C: the card is a HudPanel (chamfer + role line);
        # the returned layout contract is unchanged.
        card = HudPanel()
        card.setObjectName("hudPanel")
        layout = card.content_layout()
        t = QLabel(title)
        t.setObjectName("sectionTitle")
        layout.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setObjectName("cardHint")
            h.setWordWrap(True)
            layout.addWidget(h)
        # Settings redesign §4b: every card gets the same shape —
        # its current value in words, a line saying whether
        # voice can change it, and (Phase 4, D-P4-31) a line
        # saying when a change on it takes effect. All three
        # labels are refreshed from _refresh_settings_card_values
        # (privacy mixin); the registry supplies the voice and
        # effect lines per title, so a card never has to word
        # its own class or timing.
        value_label = QLabel("")
        value_label.setObjectName("cardHint")
        value_label.setWordWrap(True)
        value_label.setVisible(False)
        layout.addWidget(value_label)
        voice_label = QLabel("")
        voice_label.setObjectName("cardHint")
        voice_label.setWordWrap(True)
        voice_label.setVisible(False)
        layout.addWidget(voice_label)
        effect_label = QLabel("")
        effect_label.setObjectName("cardHint")
        effect_label.setWordWrap(True)
        effect_label.setVisible(False)
        layout.addWidget(effect_label)
        try:
            self._settings_card_value_labels[title] = value_label
            self._settings_card_voice_labels[title] = voice_label
            self._settings_card_effect_labels[title] = effect_label
        except Exception:  # noqa: BLE001 — labels still render
            pass
        parent_layout.addWidget(card)
        return layout


    # ------------------------------------------------- v5.57 settings tiers
    def _on_more_toggled(self):
        """Flip the "Advanced" disclosure (default collapsed)."""
        expanded = not getattr(self, "_more_expanded", False)
        self._more_expanded = expanded
        try:
            self.more_container.setVisible(expanded)
            self.more_toggle_btn.setText(
                "Advanced ▾" if expanded else "Advanced ▸")
        except Exception:  # noqa: BLE001
            pass

    def _note_settings_feedback(self, text: str):
        """UI settings slice: report one honest feedback line for
        a settings change that has no other visible feedback.
        Never called for controls that already report through
        their own label, button state, or live effect, and never
        for a control that drives nothing (those are surfaced as
        not-wired, not confirmed). Guarded: a missing label must
        never break a settings change."""
        try:
            label = getattr(self, "settings_feedback_label", None)
            if label is not None:
                label.setText(text)
                label.setVisible(True)
        except Exception:  # noqa: BLE001 — feedback never breaks a change
            pass

    def _on_settings_search(self, text: str):
        """Filter Settings cards by their text (titles, hints,
        buttons, combos). While searching, Advanced opens so matches
        behind it stay visible; clearing restores the disclosure.
        Group headers hide while searching when their whole group
        hides, so a search never shows an empty group heading.

        Settings redesign §4b: the box matches PLAIN WORDS, not
        setting names — core/settings_search.match_cards maps a
        phrase like "make her quieter" or "stop listening" to the
        cards it means, and those cards show alongside any
        literal text match.
        """
        query = (text or "").strip().lower()
        try:
            if query:
                self.more_container.setVisible(True)
            else:
                self.more_container.setVisible(
                    getattr(self, "_more_expanded", False))
            matched_titles = set()
            _ranked_cards = []
            if query:
                try:
                    from voice_assistant.core.gui_facade import (
                        settings_search as _ss)

                    _titles = []
                    for _card in getattr(
                            self, "_settings_card_texts", {}):
                        for _lbl in _card.findChildren(
                                QLabel, "sectionTitle"):
                            _titles.append(_lbl.text())
                            break
                    matched_titles = set(
                        _ss.match_cards(query, _titles))
                    # Round-2 N3: relevance ranking — the matched
                    # cards, best tier first, page order within a
                    # tier (core/settings_search.match_tier). The
                    # visible SET is unchanged; only the order
                    # moves (see _settings_search_move_results).
                    _scored = []
                    for _idx, (_card, _hay) in enumerate(
                            getattr(self, "_settings_card_texts",
                                    {}).items()):
                        _t = ""
                        for _lbl in _card.findChildren(
                                QLabel, "sectionTitle"):
                            _t = _lbl.text()
                            break
                        _tier = _ss.match_tier(query, _t, _hay)
                        if _tier is not None:
                            _scored.append((_tier, _idx, _card))
                    _scored.sort(key=lambda _s: (_s[0], _s[1]))
                    _ranked_cards = [_c for _t, _i, _c in _scored]
                except Exception:  # noqa: BLE001 — literal match stands
                    matched_titles = set()
                    _ranked_cards = []
            # Round-2 N3: every search starts from the cards back
            # home (a no-op when nothing moved); clearing the
            # search restores the page exactly. Guarded: the bare
            # mixin in the tests has no results area.
            _restore = getattr(
                self, "_settings_search_restore_cards", None)
            if _restore is not None:
                _restore()
            for card, hay in getattr(
                    self, "_settings_card_texts", {}).items():
                title = ""
                for _lbl in card.findChildren(QLabel, "sectionTitle"):
                    title = _lbl.text()
                    break
                card.setVisible(not query or query in hay
                                or title in matched_titles)
            # Round-2 N3: the ranked matches move into the results
            # area at the top of the page, so the card the user
            # means is on top instead of whichever group comes
            # first in the page. Guarded: the bare mixin in the
            # tests has no results area.
            if query and _ranked_cards:
                _move = getattr(
                    self, "_settings_search_move_results", None)
                if _move is not None:
                    _move(_ranked_cards)
            # S4: group headers follow their group — a header
            # hides while searching only when every card in its
            # group hides. UI rethink Build 3: "every card", not
            # "every widget" — the old any-visible-widget rule
            # kept headers over empty pf wrappers (the wrapper
            # stays visible while its inner card hides), so a
            # no-match search left orphan headings over blank
            # space. A card counts when it descends from the
            # group box (the Permissions / Citizenship wrappers'
            # inner cards count for their groups). A group with
            # no cards in the map at all keeps the old rule, so
            # a header never vanishes for real non-card content.
            for _hdr, _box in getattr(
                    self, "_settings_group_headers", {}).items():
                _cards = [c for c in getattr(
                    self, "_settings_card_texts", {})
                    if _box.isAncestorOf(c)]
                if _cards:
                    _any = any(c.isVisible() for c in _cards)
                else:
                    _lay = _box.layout()
                    _any = False
                    if _lay is not None:
                        for _i in range(_lay.count()):
                            _item = _lay.itemAt(_i)
                            _w = _item.widget() if _item is not None else None
                            if _w is not None and _w is not _hdr \
                                    and _w.isVisible():
                                _any = True
                                break
                _hdr.setVisible(not query or _any)
            # Round-2 N3: while searching, the results are a flat
            # ranked list, so no group heading applies — hide them
            # all. (The loop above already hid the empty ones;
            # this covers a group whose every card moved into the
            # results area.) Clearing restores them through the
            # loop above.
            if query:
                for _hdr in getattr(
                        self, "_settings_group_headers", {}):
                    _hdr.setVisible(False)
            # UI rethink Build 3 (§3.4): the empty state — a search
            # matching no card shows the explanations + next step
            # instead of a blank page.
            _any_card_visible = any(
                card.isVisible()
                for card in getattr(self, "_settings_card_texts", {}))
            self._render_settings_search_empty(query, _any_card_visible)
            # v5.57 (redo): results start at the top — without
            # this, a search typed while scrolled deep leaves the
            # viewport past the shrunken content, showing blank.
            scroll = getattr(self, "settings_scroll", None)
            if scroll is not None:
                scroll.verticalScrollBar().setValue(0)
        except Exception:  # noqa: BLE001
            pass

    def _render_settings_search_empty(self, query, any_card_visible):
        """UI rethink Build 3 (§3.4): the search empty-state.

        Shown only when a search matches NO card. The header names
        the miss; the rows are the plain-line explanations whose
        title, plain line, or "where" matches the query; the last
        line is the next step. When no explanation matches either,
        the next step says so honestly instead of showing an empty
        list. The "three most-changed cards" the plan sketches for
        this state are NOT here: the tree keeps no most-changed /
        most-used record anywhere, so there is no honest source
        for them (see the Build 3 report).
        """
        empty = getattr(self, "settings_search_empty", None)
        if empty is None:
            return
        rows = self.settings_search_empty_rows
        while rows.count():
            item = rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        if not query or any_card_visible:
            empty.setVisible(False)
            return
        self.settings_search_empty_line.setText(
            f"No setting matches \u201c{query}\u201d.")
        try:
            from .pf_settings_help import SETTING_EXPLANATIONS
        except Exception:  # noqa: BLE001 — no explanations, next step stands
            SETTING_EXPLANATIONS = ()
        shown = 0
        for item in SETTING_EXPLANATIONS:
            hay = f"{item.title} {item.plain_line} {item.where}".lower()
            if query not in hay:
                continue
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)
            title = QLabel(item.title)
            title.setStyleSheet(
                f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600;"
                f" color: {TEXT};")
            title.setWordWrap(True)
            row_layout.addWidget(title)
            body = QLabel(item.plain_line)
            body.setStyleSheet(
                f"font-size: {TYPE_BODY}px; color: {TEXT_MUTED};")
            body.setWordWrap(True)
            row_layout.addWidget(body)
            where = QLabel(item.where)
            where.setObjectName("smallMuted")
            where.setWordWrap(True)
            row_layout.addWidget(where)
            rows.addWidget(row)
            shown += 1
        if shown:
            self.settings_search_empty_next.setText(
                "Clear the search to see every setting.")
        else:
            self.settings_search_empty_next.setText(
                "Next step: clear the search to see every setting,"
                " or open Settings and look under Advanced.")
        empty.setVisible(True)

    def show_settings_card(self, title: str):
        """v5.57 (redo): open Settings on one named card.

        Used by the command palette's setting rows and the Doctor
        page's next-step buttons. Expands "Advanced" when the
        card lives there, scrolls it into view, and flashes its
        border once. Unknown titles land on Settings itself.
        """
        try:
            self._show_page(4)
        except Exception:  # noqa: BLE001
            return
        try:
            content = getattr(self, "settings_content", None)
            scroll = getattr(self, "settings_scroll", None)
            if content is None or scroll is None:
                return
            target = None
            for label in content.findChildren(QLabel, "sectionTitle"):
                if label.text() == title:
                    target = label.parentWidget()
                    break
            if target is None:
                return
            more = getattr(self, "more_container", None)
            if more is not None:
                node = target
                behind = False
                while node is not None:
                    if node is more:
                        behind = True
                        break
                    node = node.parentWidget()
                if behind and not getattr(self, "_more_expanded", False):
                    self._on_more_toggled()
                    # Round-2 N3: the expansion only takes effect on
                    # the next layout pass, so a scroll issued right
                    # now measures the collapsed geometry and lands
                    # short (Language top task: scroll 6545, Local
                    # brain still out of view). Force both layouts
                    # now, scroll, and re-scroll once the layout
                    # pass has run (below).
                    try:
                        _more_lay = more.layout()
                        if _more_lay is not None:
                            _more_lay.activate()
                        _content_lay = content.layout()
                        if _content_lay is not None:
                            _content_lay.activate()
                    except Exception:  # noqa: BLE001 — the re-scroll stands
                        pass
            scroll.ensureWidgetVisible(target)
            target.setStyleSheet(f"border: 1px solid {ACCENT};")
            QTimer.singleShot(1400, lambda: target.setStyleSheet(""))
            # Round-2 N3: re-scroll once the layout pass has run —
            # the synchronous scroll above can still measure stale
            # geometry when the Advanced drawer just opened. Only
            # when Settings is still the shown page; never raises
            # (the target may be gone by then).
            def _rescroll_after_layout():
                try:
                    if scroll.isVisible():
                        scroll.ensureWidgetVisible(target)
                except Exception:  # noqa: BLE001 — target may be gone
                    pass
            QTimer.singleShot(0, _rescroll_after_layout)
        except Exception:  # noqa: BLE001
            pass

    def _settings_search_restore_cards(self):
        """Move every search-moved card back to its recorded home
        (parent layout + index). Round-2 N3: ranking moves matched
        cards into the results area while searching; every search
        — and clearing the search — starts from the cards home.
        Cards already home are untouched. Never raises."""
        try:
            homes = getattr(self, "_settings_card_homes", None) or {}
            results = getattr(self, "_settings_search_results", None)
            if results is not None:
                results.setVisible(False)
            for card, (lay, idx) in homes.items():
                try:
                    parent = card.parentWidget()
                    if parent is not None and parent.layout() is lay:
                        continue
                    lay.insertWidget(min(idx, lay.count()), card)
                except Exception:  # noqa: BLE001 — one card never blocks
                    pass
        except Exception:  # noqa: BLE001 — restore never raises
            pass

    def _settings_search_move_results(self, ranked_cards):
        """Move ranked cards into the results area, in order.
        Round-2 N3. addWidget re-parents a card from its home —
        or re-sorts one already here — so iterating in ranked
        order builds the ranked list. Never raises."""
        try:
            results = getattr(self, "_settings_search_results", None)
            layout = getattr(self, "_settings_search_results_layout", None)
            if results is None or layout is None:
                return
            for card in ranked_cards or ():
                try:
                    layout.addWidget(card)
                except Exception:  # noqa: BLE001 — one card never blocks
                    pass
            results.setVisible(True)
        except Exception:  # noqa: BLE001 — ranking never raises
            pass

    def _build_settings_page(self) -> QWidget:
        # v2.9 re-home: the same controls, a calmer map. UI rethink
        # Build 3 (§6.4): Settings search FIRST, then a quiet summary
        # header, then Top tasks, then five everyday groups —
        # Voice & language, Privacy & permissions, Appearance,
        # Notifications, Connections — each holding the cards that
        # belong to it (S4 settings audit), then an Advanced
        # disclosure (default collapsed) holding the Local brain
        # card and every reference/diagnostics card. Every widget
        # keeps its attribute name, handler and persisted key from
        # the pre-S4 layout — only the homes changed, plus the two
        # S4 dedups (ShortcutsWidget display, citizenship
        # autostart cross-link) and four value-wording hints.
        page, layout = self._page_shell("Settings", "Your key, voice, and look — all stored on this device.", kicker=True)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("chatContent")
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(2, 2, 2, 2)
        c_layout.setSpacing(12)

        # UI rethink Build 3 (§3.4/§6.4): the search box is FIRST on
        # the page — above the summary header and the Top tasks, not
        # below them. 13 Sora, placeholder "Type what you want to
        # change".
        self.settings_search = QLineEdit()
        self.settings_search.setAccessibleName("Search settings")
        self.settings_search.setPlaceholderText("Type what you want to change")
        self.settings_search.textChanged.connect(self._on_settings_search)
        c_layout.addWidget(self.settings_search)

        # UI settings slice: one page-level feedback line under the
        # search box. Handlers that previously changed a setting
        # with NO visible feedback report here; controls that
        # already report through their own label, button state,
        # or live effect never double-report through this line.
        self.settings_feedback_label = QLabel("")
        self.settings_feedback_label.setObjectName("muted")
        self.settings_feedback_label.setWordWrap(True)
        self.settings_feedback_label.setVisible(False)
        c_layout.addWidget(self.settings_feedback_label)

        # ---- Summary header (quiet block, not a card): who VYUK is
        # right now, in one line, refreshed whenever the page opens.
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(6, 2, 6, 0)
        header_layout.setSpacing(2)
        # Phase 0: the summary name wears the kicker face now, not
        # the large pageTitle — the page already has its header
        # (the SETTINGS kicker above); a second large title under
        # it is two headers. Same wordmark treatment as Home.
        header_name = QLabel("VYUK")
        header_name.setObjectName("pageKicker")
        header_name.setStyleSheet(
            f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600;")
        header_layout.addWidget(header_name)
        self.settings_summary_label = QLabel("")
        self.settings_summary_label.setObjectName("muted")
        self.settings_summary_label.setWordWrap(True)
        header_layout.addWidget(self.settings_summary_label)
        c_layout.addWidget(header)

        # Settings redesign §4b: Top tasks — the five things
        # people actually open Settings for, one tap to the
        # exact card (core/settings_search.TOP_TASKS). The 29
        # cards stay as the second way in, below.
        self._settings_card_value_labels: dict = {}
        self._settings_card_voice_labels: dict = {}
        self._settings_card_effect_labels: dict = {}
        # Phase 4 (D-P4-32): the HUD preview session — None
        # outside a preview; _hud_preview_before is the value
        # to restore on Cancel, _hud_preview_current the pick.
        self._hud_preview_before: str | None = None
        self._hud_preview_current: str | None = None
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self._settings_top_tasks: dict = {}
        try:
            from voice_assistant.core.gui_facade import (
                settings_search as _ss)

            _top_tasks = list(_ss.TOP_TASKS)
        except Exception:  # noqa: BLE001 — no top tasks, cards stand
            _top_tasks = []
        for _label, _card_title in _top_tasks:
            _btn = QPushButton(_label)
            _btn.setObjectName("chipBtn")
            _btn.setAccessibleName(f"Top task: {_label}")
            _btn.clicked.connect(
                lambda _checked=False, t=_card_title:
                self.show_settings_card(t))
            top_row.addWidget(_btn)
            self._settings_top_tasks[_label] = _btn
        top_row.addStretch()
        c_layout.addLayout(top_row)

        # UI rethink Build 3 (§3.4): the search empty-state. A search
        # that matches no card used to leave the page blank. Now it
        # shows what the old "Settings, explained" card held — the
        # plain-line explanations (pf_settings_help.
        # SETTING_EXPLANATIONS) filtered by the query — plus the
        # next step. The card itself is gone (43 → 42 cards); its
        # content lives here. The rows deliberately do NOT use the
        # "sectionTitle" object name, so the card-text map below
        # never mistakes this block for a card.
        self.settings_search_empty = QWidget()
        empty_layout = QVBoxLayout(self.settings_search_empty)
        empty_layout.setContentsMargins(6, 2, 6, 0)
        empty_layout.setSpacing(6)
        self.settings_search_empty_line = QLabel("")
        self.settings_search_empty_line.setObjectName("muted")
        self.settings_search_empty_line.setWordWrap(True)
        empty_layout.addWidget(self.settings_search_empty_line)
        self.settings_search_empty_rows = QVBoxLayout()
        self.settings_search_empty_rows.setContentsMargins(0, 0, 0, 0)
        self.settings_search_empty_rows.setSpacing(10)
        empty_layout.addLayout(self.settings_search_empty_rows)
        self.settings_search_empty_next = QLabel("")
        self.settings_search_empty_next.setObjectName("muted")
        self.settings_search_empty_next.setWordWrap(True)
        empty_layout.addWidget(self.settings_search_empty_next)
        self.settings_search_empty.setVisible(False)
        c_layout.addWidget(self.settings_search_empty)

        # S4 (settings audit): five everyday groups, in the doc's
        # order. Each group is a container + header; cards join
        # their group's layout (not c_layout) so display order
        # is group order regardless of build order. Headers use
        # "settingsGroupTitle" (never "sectionTitle") so Settings
        # search and the palette's derived card rows ignore them.
        # Group membership is recorded for search: a header hides
        # while searching only when its whole group hides.
        self._settings_group_headers: dict = {}

        def _group(title):
            box = QWidget()
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            hdr = QLabel(title)
            hdr.setObjectName("settingsGroupTitle")
            hdr.setStyleSheet(f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600;"
                              f" color: {TEXT};")
            lay.addWidget(hdr)
            c_layout.addWidget(box)
            self._settings_group_headers[hdr] = box
            return lay

        _g_voice = _group("Voice & language")
        _g_privacy = _group("Privacy & permissions")
        _g_appearance = _group("Appearance")
        _g_notices = _group("Notifications")
        _g_connect = _group("Connections")

        # Refactor Phase 1 step 5: card sections live in their group mixins; order unchanged.
        self._build_think_cards(_g_connect)
        # UI Phase 5 (D-P5-15): the Home hub card — the one new
        # settings surface, in Connections beside the brain card.
        self._build_ha_card(_g_connect)
        self._build_voice_cards(_g_voice, _g_appearance, _g_connect)
        self._build_notices_cards(_g_notices)
        more_layout = self._build_suggestions_cards(_g_notices, c_layout)
        self._build_privacy_cards(_g_privacy)
        self._build_eyes_cards(_g_privacy)
        self._build_firstopen_cards(_g_privacy)
        self._build_browser_cards(_g_privacy)
        self._build_audit_cards(_g_privacy)
        self._build_approvals_cards(_g_privacy)
        self._build_adv_brain_cards(more_layout)
        self._build_adv_cards1(_g_appearance, more_layout)
        self._build_adv_cards2(more_layout)


        c_layout.addStretch()
        # v5.57 (redo): keep the scroll/content + a card-text map
        # (titles, hints, buttons, combos, checks — lowercased)
        # for Settings search and show_settings_card.
        self.settings_scroll = scroll
        self.settings_content = content
        self._settings_card_texts = {}
        for _lbl in content.findChildren(QLabel, "sectionTitle"):
            _card = _lbl.parentWidget()
            if _card is None:
                continue
            _parts = []
            for _w in _card.findChildren(QLabel):
                _parts.append(_w.text())
            for _w in _card.findChildren(QPushButton):
                _parts.append(_w.text())
            for _w in _card.findChildren(QComboBox):
                for _i in range(_w.count()):
                    _parts.append(_w.itemText(_i))
            for _w in _card.findChildren(QCheckBox):
                _parts.append(_w.text())
            # Phase 0: Settings toggles are HudSwitches now — their
            # label rides as text(), so the search map reads it the
            # same way (the QCheckBox loop above stays for any
            # checkbox a later card adds).
            for _w in _card.findChildren(HudSwitch):
                _parts.append(_w.text())
            self._settings_card_texts[_card] = " ".join(_parts).lower()
        # Round-2 N3: the ranked search results area — matched
        # cards move here in relevance order while searching
        # (see _on_settings_search), and the homes map records
        # where each card lives so clearing the search puts every
        # card back exactly. The area sits right under the search
        # empty-state, above the groups; hidden when not searching.
        self._settings_search_results = QWidget()
        self._settings_search_results_layout = QVBoxLayout(
            self._settings_search_results)
        self._settings_search_results_layout.setContentsMargins(0, 0, 0, 0)
        self._settings_search_results_layout.setSpacing(12)
        self._settings_search_results.setVisible(False)
        c_layout.insertWidget(
            c_layout.indexOf(self.settings_search_empty) + 1,
            self._settings_search_results)
        self._settings_card_homes = {}
        for _card in self._settings_card_texts:
            _parent = _card.parentWidget()
            _lay = _parent.layout() if _parent is not None else None
            if _lay is not None:
                self._settings_card_homes[_card] = (
                    _lay, _lay.indexOf(_card))
        scroll.setWidget(content)
        # Phase 0 field fix: forward unfocused wheel events on the
        # page's value controls to the page scroll area (see
        # _SettingsWheelFilter). Installed here, after every card is
        # built, so it covers all controls on the page at once.
        self._settings_wheel_filter = _SettingsWheelFilter(scroll)
        # PySide6 findChildren takes a single type, not a tuple.
        for _t in (QComboBox, QSlider, QAbstractSpinBox,
                   QPlainTextEdit, QListWidget):
            for _w in content.findChildren(_t):
                _w.installEventFilter(self._settings_wheel_filter)
        layout.addWidget(scroll, stretch=1)
        # v5.56 (PF-5): the settings opener for failure-script
        # open_setting actions — switching to THIS page (the
        # captured `page` widget, never a hardcoded page index)
        # and scrolling the named card into view when it has a
        # known title ("voice" -> "Voice"; any other
        # target opens the page and names itself honestly).
        def _open_setting_card(card_name):
            titles = {"voice": "Voice"}
            want = titles.get(str(card_name or ""))
            try:
                pages = getattr(self, "pages", None)
                if pages is not None:
                    pages.setCurrentWidget(page)
                if want:
                    for frame in page.findChildren(QFrame):
                        # Phase C: migrated cards are HudPanels
                        # ("hudPanel"); unmigrated ones still carry
                        # "settingsCard". Both scroll into view.
                        if frame.objectName() not in (
                                "settingsCard", "hudPanel"):
                            continue
                        for label in frame.findChildren(QLabel):
                            if label.objectName() == "sectionTitle" \
                                    and label.text() == want:
                                scroll.ensureWidgetVisible(frame)
                                break
            except Exception:  # noqa: BLE001 — opener never raises
                pass
            return {"opened": str(card_name or ""), "page": "settings",
                    "card": want}
        try:
            from voice_assistant.core.gui_facade import failure_script as _fs  # Phase 3 F2b: failure_script moved to core/funnel/
            _fs.register_setting_opener(_open_setting_card)
        except Exception:  # noqa: BLE001 — opener is best-effort
            pass
        self._sync_think_card()
        # Settings redesign §4b: the card value + voice lines
        # are filled on first build (and refreshed with the
        # header whenever the brain choice changes).
        self._refresh_settings_summary()
        return page

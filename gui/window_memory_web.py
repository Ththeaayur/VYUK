"""Memory web handlers: voice picker tail, memory pills, memory web, web detail/facts.

Split from gui/window_memory.py (GUI split #12 batch 1, move only).
"""

import datetime
from PySide6.QtCore import (
    Qt,
    QTimer,
)
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
from .memory_view import memory_facts_view, memory_header_text
from .memory_view import memory_web_header_text
from .memory_web_graph import _fact_epoch
from .style import ACCENT_2, TEXT_MUTED, first_font_family, MONO_FONT_STACK
from .memory_web import (
    MemoryWebWidget,
    category_color,
    category_label,
)
from .window_chat import ChatMixin  # v5.50 item 43: _delete_layout_items lives there now
from voice_assistant.core.gui_facade import qsettings_set


def _mem_when_text(epoch):
    """Short 'when' for a memory row: today / yesterday / 12 Nov /
    12 Nov 2025. Empty when the fact carries no readable date."""
    if epoch is None:
        return ""
    try:
        stamp = datetime.datetime.fromtimestamp(epoch)
    except (OSError, OverflowError, ValueError):
        return ""
    today = datetime.date.today()
    day = stamp.date()
    if day == today:
        return "today"
    if day == today - datetime.timedelta(days=1):
        return "yesterday"
    if stamp.year == today.year:
        return stamp.strftime("%d %b")
    return stamp.strftime("%d %b %Y")


def _mem_recency_group(epoch):
    """Today / This week / Earlier, from the same epoch."""
    if epoch is None:
        return "Earlier"
    try:
        day = datetime.datetime.fromtimestamp(epoch).date()
    except (OSError, OverflowError, ValueError):
        return "Earlier"
    today = datetime.date.today()
    if day == today:
        return "Today"
    if 0 <= (today - day).days <= 7:
        return "This week"
    return "Earlier"


class _MemoryRowWidget(QWidget):
    """One memory row: what + when, with Forget on hover.

    The row keeps its height while the button hides (the minimum
    height covers the chip), so hovering never shifts the list.
    """

    def __init__(self, fact, when_text, on_forget, font_scale: float = 1.0):
        super().__init__()
        self._fact_id = fact.get("id")
        # Stage 0 features batch 5 (larger text): the explicit
        # mono QFont overrides the QSS size, so it does not scale
        # with the sheet — set_font_scale re-applies it at the
        # scaled size. The mixin passes the current scale at
        # construction (rows rebuild on every refresh).
        self._font_scale = 1.0
        self.setMinimumHeight(30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.what = QLabel(
            f"{fact.get('key', '')}  —  {fact.get('value', '')}")
        self.what.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.what, stretch=1)
        self.when = QLabel(when_text)
        self.when.setObjectName("muted")
        self._apply_when_font()
        layout.addWidget(self.when)
        self.forget_btn = QPushButton("Forget")
        self.forget_btn.setObjectName("chipBtn")
        self.forget_btn.setVisible(False)
        self.forget_btn.clicked.connect(
            lambda _checked=False: on_forget(self._fact_id))
        layout.addWidget(self.forget_btn)
        self.set_font_scale(font_scale)

    def _apply_when_font(self) -> None:
        when_font = QFont(first_font_family(MONO_FONT_STACK))
        when_font.setPointSizeF(11.5 * self._font_scale)
        self.when.setFont(when_font)

    def set_font_scale(self, scale: float) -> None:
        """Scale the mono timestamp (batch 5, item 12)."""
        try:
            self._font_scale = float(scale)
        except (TypeError, ValueError):
            self._font_scale = 1.0
        self._apply_when_font()

    def enterEvent(self, event):  # noqa: N802 (Qt naming)
        self.forget_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt naming)
        self.forget_btn.setVisible(False)
        super().leaveEvent(event)


class MemoryWebMixin:
    def _on_voice_changed(self, voice_name: str):
        config.voice_name = voice_name
        # UI settings slice: the Settings Voice pick was
        # session-only — the ONLY .env write path for VOICE_NAME
        # was the voice-command path (core/app_settings.py), so
        # a Settings pick was lost on restart with no feedback.
        # Persist through the same BrainMixin helper the other
        # voice picks use; the live session reads
        # config.voice_name at session creation
        # (core/voice/live_client.py), so the pick applies at the
        # next connect.
        try:
            self._persist_config_setting("VOICE_NAME", voice_name)
        except Exception:  # noqa: BLE001 — never break the pick
            pass
        self._note_settings_feedback(
            f"Voice set to {voice_name} — used next time you "
            "connect.")

    def _refresh_memories(self):
        if not hasattr(self, "memory_list"):
            return
        query = self.mem_search.text().strip()
        combo = getattr(self, "mem_cat_filter", None)
        chosen = "All categories"
        if combo is not None:
            chosen = combo.currentText() or "All categories"
        # GAPS-28 (G-V1): the store query, the category list and
        # the filter live in gui/memory_view.py — this method only
        # renders what the view returns.
        view = memory_facts_view(query=query, category=chosen)
        total = view["total"]
        # S12 (M4): keep the category list honest, then filter.
        cats = view["categories"]
        if combo is not None:
            existing = [combo.itemText(i) for i in range(combo.count())]
            if existing != ["All categories"] + cats:
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("All categories")
                combo.addItems(cats)
                idx = combo.findText(chosen)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.blockSignals(False)
                chosen = combo.currentText() or "All categories"
                # The chosen category changed under us (it
                # vanished): re-filter with the new choice.
                view = memory_facts_view(query=query, category=chosen)
        shown = view["facts"]
        # Rethink Build 6 (§6.5): the list is grouped by recency,
        # most recent first, one line per fact — what + when (13
        # Sora + 11.5 mono) — with a forget action on each row's
        # hover. Group headers are plain disabled items, never
        # selectable, so the selection bar only ever counts facts.
        self._mem_facts_by_id = {
            f.get("id"): f for f in shown if f.get("id") is not None}
        ordered = sorted(
            shown, key=lambda f: _fact_epoch(f) or 0, reverse=True)
        self.memory_list.clear()
        group = None
        for f in ordered:
            when_group = _mem_recency_group(_fact_epoch(f))
            if when_group != group:
                group = when_group
                header = QListWidgetItem(group.upper())
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setForeground(QBrush(QColor(TEXT_MUTED)))
                self.memory_list.addItem(header)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, f.get("id"))
            self.memory_list.addItem(item)
            row = _MemoryRowWidget(
                f, _mem_when_text(_fact_epoch(f)),
                self._forget_memory_fact,
                font_scale=self._current_font_scale())
            self.memory_list.setItemWidget(item, row)
        self.mem_empty.setVisible(not shown)
        if hasattr(self, "mem_art"):
            self.mem_art.setVisible(not shown)
        # S12 (M1): the count, always visible in list view.
        self._refresh_memory_web()
        # E-F204 + follow-up: the count names the facts actually
        # on the page FOR THE CURRENT VIEW — see
        # _set_mem_count_label. Set AFTER the web refresh: in Web
        # view the header counts the graph's fact nodes, and the
        # graph is rebuilt just above; setting it before would
        # read the previous facts and leave the header stale for
        # a whole show whenever the store changed under Web view.
        self._set_mem_count_label(len(shown), total)
        self._sync_mem_filter_button()
        self._on_mem_selection_changed()

    def _web_fact_count(self):
        """Fact nodes the web graph shows right now (E-F204
        follow-up): the kept store facts, post-cap (the graph caps
        each category at 40 nodes). None when the web widget has
        never been built, so the caller falls back to the store
        total. Reads the 2D canvas directly — it is always built
        and always holds the graph, whatever the 2D/3D mode."""
        widget = getattr(self, "memory_web", None)
        if widget is None:
            return None
        try:
            graph = widget.canvas2d.graph
        except Exception:  # noqa: BLE001 — no graph, no count
            return None
        if graph is None:
            return None
        return sum(1 for n in graph.nodes
                   if getattr(n, "kind", "") == "fact")

    def _set_mem_count_label(self, shown, total):
        """Set the header count line for the current view (E-F204 +
        follow-up). List view: the list rows shown/total PLUS the
        Learned-card rows (memory_header_text). Web view: the web's
        fact nodes (memory_web_header_text) — the card and list are
        hidden there, so their rows never count, and the plain form
        holds whatever the List search/category filter says (in Web
        view the search only dims non-matches). Called from
        _refresh_memories on every refresh and from _set_memory_view
        on every switch, so the header follows the view."""
        if not hasattr(self, "mem_count_label"):
            return
        if getattr(self, "_mem_view", "list") == "web":
            n = self._web_fact_count()
            if n is None:
                n = total
            self.mem_count_label.setText(memory_web_header_text(n))
            return
        learned_n = self._learned_fact_count()
        self.mem_count_label.setText(
            memory_header_text(shown, total, learned_n))

    def _on_mem_selection_changed(self):
        """The selection bar appears only while something is
        selected; the count names what the bar acts on."""
        bar = getattr(self, "_mem_sel_bar", None)
        label = getattr(self, "_mem_sel_label", None)
        if bar is None or label is None:
            return
        n = len([item for item in self.memory_list.selectedItems()
                 if item.data(Qt.ItemDataRole.UserRole) is not None])
        label.setText(f"{n} selected" if n != 1 else "1 selected")
        bar.setVisible(n > 0)

    def _sync_mem_filter_button(self):
        """The Filter button always names the current choice: the
        category, plus the web's shown count when it is narrowed."""
        button = getattr(self, "btn_mem_filter", None)
        if button is None:
            return
        combo = getattr(self, "mem_cat_filter", None)
        chosen = (combo.currentText() if combo is not None else "") \
            or "All categories"
        label = "All" if chosen == "All categories" else chosen
        text = f"Filter: {label}"
        show = getattr(self, "_mem_web_show", None) or {}
        if show and getattr(self, "_mem_view", "list") == "web":
            shown = sum(1 for v in show.values() if v)
            if shown != len(show):
                text += f" · Web {shown} of {len(show)}"
        button.setText(text)

    def _show_mem_filter_menu(self):
        """One menu, two groups — category, then show — with the
        current choice always named on the button (declutter move
        7). The Show group only exists in Web view: it filters the
        web's nodes, and there is no web to filter in List view."""
        menu = QMenu(self.btn_mem_filter)
        combo = getattr(self, "mem_cat_filter", None)
        cats = ["All categories"]
        if combo is not None:
            cats += [combo.itemText(i) for i in range(combo.count())
                     if combo.itemText(i) != "All categories"]
        current = (combo.currentText() if combo is not None else "") \
            or "All categories"
        group = QActionGroup(menu)
        group.setExclusive(True)
        for text in cats:
            act = QAction(
                "All" if text == "All categories" else text, menu)
            act.setCheckable(True)
            act.setChecked(text == current)
            act.triggered.connect(
                lambda _checked=False, t=text:
                self.mem_cat_filter.setCurrentText(t))
            group.addAction(act)
            menu.addAction(act)
        if getattr(self, "_mem_view", "list") == "web":
            menu.addSeparator()
            show = getattr(self, "_mem_web_show", None) or {}
            for cat in getattr(self, "_mem_filter_cats", []):
                act = QAction(category_label(cat), menu)
                act.setCheckable(True)
                act.setChecked(show.get(cat, True))
                act.triggered.connect(
                    lambda checked=False, c=cat:
                    self._on_mem_filter_toggled(c, checked))
                menu.addAction(act)
        menu.exec(self.btn_mem_filter.mapToGlobal(
            self.btn_mem_filter.rect().bottomLeft()))

    def _show_mem_overflow_menu(self):
        """Export, Import, Forget selected, Tour, Clear
        conversation history — the rare acts, one tap away."""
        menu = QMenu(self.btn_mem_overflow)
        menu.addAction(self._mem_export_action)
        menu.addAction(self._mem_import_action)
        menu.addAction(self._mem_forget_action)
        menu.addAction(self._mem_tour_action)
        menu.addSeparator()
        menu.addAction(self._mem_clear_action)
        menu.exec(self.btn_mem_overflow.mapToGlobal(
            self.btn_mem_overflow.rect().bottomLeft()))

    def _add_memory_fact(self):
        k = self.input_mem_k.text().strip()
        v = self.input_mem_v.text().strip()
        if k and v:
            if memory_store.save_fact("general", k, v):
                self.input_mem_k.clear()
                self.input_mem_v.clear()
            self._refresh_memories()

    # --------------------------------------- memory web (v3.1)
    def _set_memory_view(self, mode: str):
        """List | Web toggle for the Memory page. Web mode hides the
        learned card, the list card, the add-fact row and the
        Documents section (Darwin D1: the document half lives in
        the list view — hiding it keeps the graph's pre-D1
        geometry; a visible Documents card above the web
        container pushes the graph below the fold at 750 px and
        the E-F202 label tests read no ink). Clear history stays
        — it is about the conversation, not facts; the choice
        persists in memory/view."""
        mode = "web" if mode == "web" else "list"
        self._mem_view = mode
        try:
            qsettings_set("memory/view", mode, settings=self.settings)
        except Exception:  # noqa: BLE001
            pass
        for btn, value in ((self.btn_mem_view_list, "list"),
                           (self.btn_mem_view_web, "web")):
            btn.blockSignals(True)
            btn.setChecked(mode == value)
            btn.blockSignals(False)
        web = mode == "web"
        learned_card = getattr(self, "learned_card", None)
        if learned_card is not None:
            learned_card.setVisible(not web)
        self.mem_list_card.setVisible(not web)
        doc_section = getattr(self, "_doc_section", None)
        if doc_section is not None:
            doc_section.setVisible(not web)
        for widget in getattr(self, "_mem_add_row_widgets", []):
            widget.setVisible(not web)
        self.mem_web_container.setVisible(web)
        self.mem_timeline_row.setVisible(web)
        if web:
            widget = self._ensure_memory_web()
            facts = memory_store.get_all_facts()
            widget.refresh_if_changed(facts)
            widget.set_search_highlight(self.mem_search.text())
            self._sync_mem_web_tools(facts)
            self._on_mem_search_changed(self.mem_search.text())
            # E-F204 follow-up: the switch itself must move the
            # header — _refresh_memories is not called on the way
            # into Web view, so without this the header keeps its
            # last List-view value over the web. (facts is the whole
            # store here; the web branch of _set_mem_count_label
            # counts the graph's fact nodes and only falls back to
            # this total when the widget has never been built.)
            self._set_mem_count_label(len(facts), len(facts))
        else:
            widget = getattr(self, "memory_web", None)
            if widget is not None:
                try:
                    widget.stop_tour()
                except Exception:  # noqa: BLE001
                    pass
            action = getattr(self, "_mem_tour_action", None)
            if action is not None:
                action.blockSignals(True)
                action.setChecked(False)
                action.blockSignals(False)
            self._refresh_memories()

    def _ensure_memory_web(self) -> MemoryWebWidget:
        """The graph widget is built on first use only — the list
        stays the zero-cost default."""
        if getattr(self, "memory_web", None) is None:
            widget = MemoryWebWidget()
            widget.fact_selected.connect(self._on_web_fact_selected)
            widget.selection_cleared.connect(
                self._on_web_selection_cleared)
            self.memory_web = widget
            self.mem_web_layout.insertWidget(0, widget, 1)
            # Batch 5, item 12: a lazily built page missed the
            # last appearance pass — scale its painted labels now.
            self._apply_font_scale_to_painted_pages(
                self._current_font_scale())
        return self.memory_web

    def _refresh_memory_web(self):
        widget = getattr(self, "memory_web", None)
        if widget is not None:
            facts = memory_store.get_all_facts()
            widget.refresh_if_changed(facts)
            self._sync_mem_web_tools(facts)

    # --------------------------------------- web tools (v5.18)

    def _sync_mem_web_tools(self, facts: list):
        """Keep the Filter menu's Show group and the timeline span
        in step with the facts on show (E25/E27). The Show state is
        a dict of category -> shown (new categories start shown);
        the menu is built fresh on every open, so a refresh never
        steals a toggle the user just made."""
        cats = sorted({str(f.get("category") or "general").strip().lower()
                       for f in facts or []})
        if cats != self._mem_filter_cats:
            self._mem_filter_cats = cats
            show = dict(getattr(self, "_mem_web_show", None) or {})
            for cat in cats:
                show.setdefault(cat, True)
            # E-F204 follow-up: this was `del show[c]` — a NameError
            # (the comprehension's `c` never leaks) that fired
            # whenever a category vanished while the web widget
            # existed, aborting the refresh mid-way. The loop
            # variable is `cat`.
            for cat in [c for c in show if c not in cats]:
                del show[cat]
            self._mem_web_show = show
        epochs = [e for e in (_fact_epoch(f) for f in facts or [])
                  if e is not None]
        if len(epochs) >= 2 and max(epochs) > min(epochs):
            self._mem_time_span = (min(epochs), max(epochs))
            self.mem_timeline.setEnabled(True)
        else:
            self._mem_time_span = None
            self.mem_timeline.setEnabled(False)
            # No span to scrub — make sure no earlier cutoff lingers.
            self.mem_timeline.blockSignals(True)
            self.mem_timeline.setValue(1000)
            self.mem_timeline.blockSignals(False)
            self.mem_timeline_label.setText("All time")
            widget = getattr(self, "memory_web", None)
            if widget is not None:
                try:
                    widget.set_time_cutoff(None)
                except Exception:  # noqa: BLE001
                    pass
        self._sync_mem_filter_button()

    def _on_mem_filter_toggled(self, cat: str, checked: bool = True):
        """One Show-group toggle in the Filter menu: set the
        category's web visibility from the menu action's own
        checked state and re-apply the web filter."""
        show = dict(getattr(self, "_mem_web_show", None) or {})
        show[cat] = bool(checked)
        self._mem_web_show = show
        widget = getattr(self, "memory_web", None)
        if widget is None:
            return
        allowed = {c for c, shown in show.items() if shown}
        widget.set_category_filter(
            None if len(allowed) == len(show) else allowed)
        self._sync_mem_filter_button()

    def _on_mem_tour_toggled(self, checked: bool):
        widget = getattr(self, "memory_web", None)
        if checked and getattr(self, "_mem_view", "list") != "web":
            # The tour flies the web — switch to it first, then
            # start the tour once the web exists.
            self._set_memory_view("web")
            widget = getattr(self, "memory_web", None)
        if widget is None:
            action = getattr(self, "_mem_tour_action", None)
            if action is not None:
                action.blockSignals(True)
                action.setChecked(False)
                action.blockSignals(False)
            return
        if checked:
            widget.start_tour()
        else:
            widget.stop_tour()

    def _on_mem_timeline_changed(self, value: int):
        widget = getattr(self, "memory_web", None)
        span = self._mem_time_span
        if span is None:
            self.mem_timeline_label.setText("All time")
            return
        frac = value / 1000.0
        if frac >= 0.999 or widget is None:
            if widget is not None:
                widget.set_time_cutoff(None)
            self.mem_timeline_label.setText("All time")
            return
        epoch = span[0] + frac * (span[1] - span[0])
        widget.set_time_cutoff(epoch)
        self.mem_timeline_label.setText(
            datetime.datetime.fromtimestamp(epoch).strftime("%d %b %Y"))

    def _on_mem_search_changed(self, text: str):
        """In Web mode the search field doesn't filter — it dims
        whatever doesn't match, so the web's shape survives.
        v5.18 (E24): matches also wear a pulsing halo. (Build 6:
        the match-count label is gone — the count line above
        carries the shown count.)"""
        widget = getattr(self, "memory_web", None)
        if widget is not None:
            widget.set_search_highlight(text)

    def _build_web_detail_panel(self) -> QWidget:
        """The web's inspector: what the picked node is, when VYUK
        learned it, what it links to, and the way to forget it."""
        panel = QWidget()
        panel.setFixedWidth(280)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 4, 4, 4)
        lay.setSpacing(8)
        self.web_detail_chip = QLabel("")
        lay.addWidget(self.web_detail_chip)
        self.web_detail_key = QLabel("")
        self.web_detail_key.setObjectName("sectionTitle")
        self.web_detail_key.setWordWrap(True)
        lay.addWidget(self.web_detail_key)
        self.web_detail_value = QLabel("")
        self.web_detail_value.setWordWrap(True)
        lay.addWidget(self.web_detail_value)
        self.web_detail_date = QLabel("")
        self.web_detail_date.setObjectName("muted")
        lay.addWidget(self.web_detail_date)
        self.web_detail_links_title = QLabel("Linked memories")
        self.web_detail_links_title.setObjectName("sectionTitle")
        lay.addWidget(self.web_detail_links_title)
        self.web_detail_links = QVBoxLayout()
        self.web_detail_links.setSpacing(4)
        lay.addLayout(self.web_detail_links)
        lay.addStretch()
        self.btn_web_forget = QPushButton("Forget this fact")
        self.btn_web_forget.setObjectName("chipBtn")
        self.btn_web_forget.clicked.connect(self._forget_web_fact)
        lay.addWidget(self.btn_web_forget)
        panel.setVisible(False)
        return panel

    def _on_web_fact_selected(self, fact):
        self._web_selected_fact = fact
        self._disarm_web_forget()
        self._populate_web_detail(fact)
        self.mem_web_detail.setVisible(True)

    def _on_web_selection_cleared(self):
        self._web_selected_fact = None
        self._disarm_web_forget()
        if getattr(self, "mem_web_detail", None) is not None:
            self.mem_web_detail.setVisible(False)

    def _populate_web_detail(self, fact):
        from html import escape

        cat = str(fact.get("category") or "general").strip().lower() \
            or "general"
        self.web_detail_chip.setText(f"● {category_label(cat)}")
        self.web_detail_chip.setStyleSheet(
            f"color: {category_color(cat)}; font-weight: 600;")
        self.web_detail_key.setText(
            str(fact.get("key") or "").strip() or "(untitled)")
        self.web_detail_value.setText(str(fact.get("value") or ""))
        raw = str(fact.get("updated_at") or "").strip()
        date_text = ""
        if raw:
            try:
                parsed = datetime.datetime.strptime(
                    raw[:19], "%Y-%m-%d %H:%M:%S")
                date_text = "Remembered " + parsed.strftime("%d %b %Y")
            except ValueError:
                date_text = f"Remembered {raw}"
        self.web_detail_date.setText(date_text)
        # Cross-links: who this fact is woven to, and over which
        # shared word. Rows are links — following one re-selects
        # that node in the web.
        ChatMixin._delete_layout_items(self.web_detail_links)  # v5.50 item 43: was MainWindow._delete_layout_items (same staticmethod, now on ChatMixin)
        widget = getattr(self, "memory_web", None)
        links = widget.cross_links(f"fact:{fact.get('id')}") \
            if widget is not None else []
        self.web_detail_links_title.setVisible(bool(links))
        for other_id, other_label, tokens in links:
            shared = ", ".join(f"‘{escape(str(t))}’" for t in tokens)
            label = QLabel()
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setText(
                f'<a href="{escape(other_id, quote=True)}" '
                f'style="color: {ACCENT_2};">Linked to '
                f'‘{escape(str(other_label))}’</a> '
                f'<span style="color: {TEXT_MUTED};">— both mention '
                f'{shared}</span>')
            label.linkActivated.connect(self._on_web_link_activated)
            self.web_detail_links.addWidget(label)

    def _on_web_link_activated(self, node_id: str):
        widget = getattr(self, "memory_web", None)
        if widget is None:
            return
        node = widget.graph.node(node_id)
        if node is not None and node.kind == "fact":
            widget.expand_category(node.category)
        widget.select_node(node_id)

    def _forget_web_fact(self):
        """Forget from the detail panel. Two-step, like Clear
        history: first tap arms, second within ~4 s deletes.
        Learned facts go through learning's own category-verified
        path; everything else uses the store's delete."""
        fact = getattr(self, "_web_selected_fact", None)
        if not fact:
            return
        if not getattr(self, "_web_forget_armed", False):
            self._web_forget_armed = True
            self.btn_web_forget.setText("Tap again to forget")
            QTimer.singleShot(4000, self._disarm_web_forget)
            return
        self._disarm_web_forget()
        fact_id = fact.get("id")
        cat = str(fact.get("category") or "").strip().lower()
        ok = False
        if cat == "learned":
            module = self._learning_module()
            if module is not None:
                try:
                    ok = bool(module.forget_learned_fact(fact_id))
                except Exception:  # noqa: BLE001
                    ok = False
        else:
            try:
                ok = bool(memory_store.delete_fact(fact_id))
            except Exception:  # noqa: BLE001
                ok = False
        widget = getattr(self, "memory_web", None)
        if widget is not None:
            widget.clear_selection()
        else:
            self._on_web_selection_cleared()
        self._refresh_memories()
        if cat == "learned":
            self._refresh_learned_facts(mark_seen=True)
        if ok:
            self._add_system(
                "Forgot — I'll no longer remember "
                f"{fact.get('key') or 'that'}.")

    def _disarm_web_forget(self):
        if not getattr(self, "_web_forget_armed", False):
            return
        self._web_forget_armed = False
        btn = getattr(self, "btn_web_forget", None)
        if btn is not None:
            try:
                btn.setText("Forget this fact")
            except RuntimeError:
                pass

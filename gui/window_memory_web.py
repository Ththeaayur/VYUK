"""Memory web handlers: voice picker tail, memory pills, memory web, web detail/facts.

Split from gui/window_memory.py (GUI split #12 batch 1, move only).
"""

import datetime
from PySide6.QtCore import (
    Qt,
    QSize,
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
from .design_system.drawer import ContextDrawer
from .memory_view import memory_facts_view, memory_header_text
from .memory_view import memory_web_header_text
from .memory_web_graph import _fact_epoch
from .style import (
    ACCENT_2,
    LINE_STRONG,
    SURFACE_1,
    TEXT_MUTED,
    first_font_family,
    MONO_FONT_STACK,
)
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
        # The sections read the WHOLE store from one shared read
        # (never a second read per cell), and forget needs facts
        # the filter hides — so _mem_facts_by_id covers every
        # fact, not just the shown ones.
        all_facts = self._mem_all_facts()
        self._mem_facts_by_id = {
            f.get("id"): f for f in all_facts if f.get("id") is not None}
        # The learned strip's "no date recorded" bucket (M-02):
        # list the facts with no readable date.
        if getattr(self, "_mem_undated_only", False):
            shown = [f for f in shown if _fact_epoch(f) is None]
        # Phase 1: the list is MemoryFactCards, grouped by
        # recency, most recent first. Group headers are plain
        # disabled items, never selectable, so the selection bar
        # only ever counts facts (the test_rethink_build6
        # recency test reads card.when for the age word).
        ordered = sorted(
            shown, key=lambda f: _fact_epoch(f) or 0, reverse=True)
        # clear() removes the items but leaves the item widgets
        # orphaned as children of the viewport — a stale card
        # would still answer findChildren (and a leg reading
        # cards[0] would get the pre-search text). Take each item
        # and delete its widget explicitly. The widget is detached
        # from the window FIRST (setParent(None)) so it stops
        # answering findChildren immediately — deleteLater alone
        # leaves it visible to findChildren until the event loop
        # gets around to it.
        while self.memory_list.count():
            _item = self.memory_list.item(0)
            _widget = self.memory_list.itemWidget(_item)
            self.memory_list.takeItem(0)
            if _widget is not None:
                _widget.setParent(None)
                _widget.deleteLater()
            del _item
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
            card = self._make_fact_card(
                f, _mem_when_text(_fact_epoch(f)), query)
            item.setSizeHint(QSize(
                0, max(52, card.sizeHint().height())))
            self.memory_list.setItemWidget(item, card)
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
        self._refresh_memory_sections(all_facts, view)

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
        follow-up). List view: the unique facts shown/total
        (memory_header_text) — the Learned card renders the same
        facts a second time, so its rows do NOT add to the count
        (parent ruling, hunt fix (c) header round: with N stored
        user facts the header reads "N facts remembered"). Web
        view: the web's fact nodes (memory_web_header_text) — the
        card and list are hidden there, so their rows never count,
        and the plain form holds whatever the List search/category
        filter says (in Web view the search only dims non-matches).
        Called from _refresh_memories on every refresh and from
        _set_memory_view on every switch, so the header follows
        the view.
        Phase 1: in private mode the count line shows the
        private-mode line with NO number (M-01)."""
        if not hasattr(self, "mem_count_label"):
            return
        if self._mem_private_on():
            self.mem_count_label.setText("Private mode is on")
            return
        if getattr(self, "_mem_view", "list") == "web":
            n = self._web_fact_count()
            if n is None:
                n = total
            self.mem_count_label.setText(memory_web_header_text(n))
            return
        self.mem_count_label.setText(
            memory_header_text(shown, total))

    def _on_mem_selection_changed(self):
        """The selection bar appears only while something is
        selected; the count names what the bar acts on. The
        cards follow the list selection: a selected card reveals
        its actions (the hover/selected reveal, M-27) — keyboard
        selection counts as selected through the list."""
        if not hasattr(self, "memory_list"):
            return
        bar = getattr(self, "_mem_sel_bar", None)
        label = getattr(self, "_mem_sel_label", None)
        selected = [item for item in self.memory_list.selectedItems()
                    if item.data(Qt.ItemDataRole.UserRole) is not None]
        n = len(selected)
        if bar is not None and label is not None:
            label.setText(f"{n} selected" if n != 1 else "1 selected")
            bar.setVisible(n > 0)
        selected_ids = {item.data(Qt.ItemDataRole.UserRole)
                        for item in selected}
        for i in range(self.memory_list.count()):
            item = self.memory_list.item(i)
            widget = self.memory_list.itemWidget(item)
            setter = getattr(widget, "set_selected", None)
            if callable(setter):
                setter(item.data(Qt.ItemDataRole.UserRole)
                       in selected_ids)

    def _sync_mem_filter_button(self):
        """The Graph section's Show button names the web's shown
        count when it is narrowed (the "Filter: All" button is
        gone — the list filter is the chip row now)."""
        button = getattr(self, "btn_mem_show", None)
        if button is None:
            return
        show = getattr(self, "_mem_web_show", None) or {}
        if show:
            shown = sum(1 for v in show.values() if v)
            text = "Show" if shown == len(show) \
                else f"Show · {shown} of {len(show)}"
        else:
            text = "Show"
        button.setText(text)

    def _show_mem_filter_menu(self):
        """The Show menu (Graph section): which categories the
        web shows. Anchored to the Show button; the list's
        category filter moved to the chip row."""
        button = getattr(self, "btn_mem_show", None)
        if button is None:
            return
        menu = QMenu(button)
        show = getattr(self, "_mem_web_show", None) or {}
        for cat in getattr(self, "_mem_filter_cats", []):
            act = QAction(category_label(cat), menu)
            act.setCheckable(True)
            act.setChecked(show.get(cat, True))
            act.triggered.connect(
                lambda checked=False, c=cat:
                self._on_mem_filter_toggled(c, checked))
            menu.addAction(act)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

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
        """The add-fact row: a plain save, or — while an edit is
        open — the save_fact upsert (D-M3). The key is not
        editable inline: a changed key is forget-old + save-new
        in ONE action, the pin carries over, and one undo
        restores both. The save runs first so a refused value
        (a credential-shaped fact) leaves the old fact intact."""
        k = self.input_mem_k.text().strip()
        v = self.input_mem_v.text().strip()
        if not (k and v):
            return
        try:
            from voice_assistant.core.gui_facade import (
                memory_store as _store)
        except Exception:  # noqa: BLE001
            return
        editing = getattr(self, "_mem_editing_fact", None)
        if isinstance(editing, dict) and editing.get("id") is not None:
            old = editing
            old_key = str(old.get("key") or "")
            old_cat = str(old.get("category") or "general")
            old_source = str(old.get("source") or "user")
            was_pinned = bool(old.get("user_pinned"))
            self._mem_editing_fact = None
            widgets = getattr(self, "_mem_add_row_widgets", [])
            if len(widgets) > 2 and widgets[2] is not None:
                widgets[2].setText("Remember this")
            if k == old_key:
                _store.save_fact(old_cat, k, v, source=old_source)
            else:
                _store.save_fact(old_cat, k, v, source=old_source)
                fresh = _store.get_fact(old_cat, k)
                new_id = fresh.get("id") \
                    if isinstance(fresh, dict) else None
                if was_pinned and new_id is not None:
                    try:
                        _store.set_user_pinned(new_id, True)
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    _store.delete_fact(old["id"])
                except Exception:  # noqa: BLE001
                    pass
                old_copy = dict(old)
                old_copy["_rename_new_id"] = new_id
                self._show_mem_undo(
                    [old_copy], label=f"Renamed “{old_key}”.")
            self.input_mem_k.clear()
            self.input_mem_v.clear()
            self._refresh_memories()
            return
        if _store.save_fact("general", k, v):
            self.input_mem_k.clear()
            self.input_mem_v.clear()
        self._refresh_memories()

    # --------------------------------------- memory web (v3.1)

    def _set_memory_view(self, mode: str):
        """List | Web compatibility shim (Phase 1): the List/Web
        toggle is gone — List is the Overview tab, Web is the
        Knowledge Graph tab. Existing callers (tests) still call
        this; it maps the mode to its tab and lets the tab
        handler do the work (persist, placeholder, constellation,
        scroll into view). The memory/view key is retired: it is
        no longer persisted here, and the build migrates a saved
        value once, then removes the key."""
        mode = "web" if mode == "web" else "list"
        self._mem_view = mode
        tabs = getattr(self, "_memory_tabs", None)
        if tabs is not None:
            index = 4 if mode == "web" else 0
            tabs.blockSignals(True)
            tabs.setCurrentIndex(index)
            tabs.blockSignals(False)
            # setCurrentIndex to the already-current tab fires no
            # signal, so drive the handler explicitly.
            self._on_memory_section_changed(index)
        else:
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
            canvas = getattr(widget, "canvas2d", None)
            self._memory_canvas = canvas
            self._mem_sync_canvas_theme()
            if canvas is not None:
                try:
                    canvas.set_private_mode(self._mem_private_on())
                except Exception:  # noqa: BLE001
                    pass
                try:
                    canvas.set_constellation_enabled(
                        getattr(self, "_mem_view", "list") == "web")
                except Exception:  # noqa: BLE001
                    pass
        return self.memory_web

    def _refresh_memory_web(self):
        widget = getattr(self, "memory_web", None)
        if widget is not None:
            # The store is resolved at CALL time through the
            # facade: the module-global memory_store is the
            # harness's stub under test, and the repro legs
            # rebind the facade's store.
            try:
                from voice_assistant.core.gui_facade import (
                    memory_store as _store)
                facts = _store.get_all_facts()
            except Exception:  # noqa: BLE001
                facts = []
            if not isinstance(facts, list):
                facts = []
            widget.refresh_if_changed(facts)
            self._sync_mem_web_tools(facts)
            self._mem_sync_canvas_theme()


    def _mem_private_on(self) -> bool:
        """Private mode, live from the app config (fail-closed:
        an unreadable config reads as ON)."""
        try:
            from voice_assistant.core.gui_facade import (
                private_mode as _private_mode)
            return bool(_private_mode.private_mode_on())
        except Exception:  # noqa: BLE001
            return True

    def _mem_all_facts(self):
        """Every fact in the store, resolved at call time
        through the facade (see _refresh_memory_web)."""
        try:
            from voice_assistant.core.gui_facade import (
                memory_store as _store)
            facts = _store.get_all_facts()
        except Exception:  # noqa: BLE001
            return []
        return list(facts) if isinstance(facts, list) else []

    def _mem_sync_canvas_theme(self):
        """Recolour the web canvas from the window's resolved
        theme (M-21) — the same dict the appearance pass keeps
        in _resolved_theme. A lazily built canvas misses that
        pass, so this runs on every web refresh too."""
        canvas = getattr(self, "_memory_canvas", None)
        if canvas is None:
            return
        resolved = getattr(self, "_resolved_theme", None)
        if not isinstance(resolved, dict):
            return
        try:
            canvas.set_theme(resolved)
        except Exception:  # noqa: BLE001
            pass
        # Look-fix 2026-11-24: the dimension chip bar follows
        # the same theme (its dark house BG floated as a dark
        # rectangle over the Light graph).
        widget = getattr(self, "memory_web", None)
        if widget is not None:
            try:
                widget.set_theme(resolved)
            except Exception:  # noqa: BLE001
                pass
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
        """The web's inspector: what the picked node is, when
        VYUK last updated it, where it came from, what it links
        to — and the same Edit / Pin / Forget as the list
        (plan §3: the drawer IS the list inspector). The
        drawer's own Pin is renamed "Keep open": it means the
        rail stays visible when the selection clears. The drawer
        keeps its own fill (surface_1 + a LINE_STRONG left
        edge) while the web container goes transparent."""
        # Phase 0: the detail panel is the shared ContextDrawer —
        # same 280 px rail, same widgets, now with the drawer's
        # title + Pin. Pin means the rail stays visible when the
        # selection clears (the drawer's own contract); unpinned
        # it hides on clear, as before.
        panel = ContextDrawer(title="Fact detail")
        panel.setFixedWidth(280)
        panel.setObjectName("webDetailDrawer")
        panel.setStyleSheet(
            f"#webDetailDrawer {{ background: {SURFACE_1}; "
            f"border-left: 1px solid {LINE_STRONG}; }}")
        panel.layout().setContentsMargins(8, 4, 4, 4)
        try:
            panel._pin.setText("Keep open")
            panel._pin.setToolTip(
                "Keep this detail open when the selection clears")
        except Exception:  # noqa: BLE001
            pass
        lay = panel.content_layout()
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
        self.web_detail_source = QLabel("")
        self.web_detail_source.setObjectName("muted")
        lay.addWidget(self.web_detail_source)
        self.web_detail_links_title = QLabel("Linked memories")
        self.web_detail_links_title.setObjectName("sectionTitle")
        lay.addWidget(self.web_detail_links_title)
        self.web_detail_links = QVBoxLayout()
        self.web_detail_links.setSpacing(4)
        lay.addLayout(self.web_detail_links)
        lay.addStretch()
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.btn_web_edit = QPushButton("Edit")
        self.btn_web_edit.setObjectName("chipBtn")
        self.btn_web_edit.clicked.connect(
            lambda: self._on_fact_edit(
                getattr(self, "_web_selected_fact", None)))
        actions.addWidget(self.btn_web_edit)
        self.btn_web_pin = QPushButton("Pin fact")
        self.btn_web_pin.setObjectName("chipBtn")
        self.btn_web_pin.clicked.connect(self._on_web_pin_clicked)
        actions.addWidget(self.btn_web_pin)
        self.btn_web_forget = QPushButton("Forget this fact")
        self.btn_web_forget.setObjectName("chipBtn")
        self.btn_web_forget.clicked.connect(self._forget_web_fact)
        actions.addWidget(self.btn_web_forget)
        lay.addLayout(actions)
        panel.setVisible(False)
        return panel

    def _on_web_fact_selected(self, fact):
        # Private mode blanks the detail too (M-01): a picked
        # node shows nothing while it is on.
        if self._mem_private_on():
            return
        self._web_selected_fact = fact
        self._disarm_web_forget()
        self._populate_web_detail(fact)
        self.mem_web_detail.setVisible(True)


    def _on_web_pin_clicked(self):
        """The drawer's Pin fact: toggle the pin in the store,
        then re-read the row so the drawer shows the fresh
        pinned state (the selected dict is stale after the
        toggle)."""
        fact = getattr(self, "_web_selected_fact", None)
        if not isinstance(fact, dict) or fact.get("id") is None:
            return
        self._on_fact_pin(fact)
        fresh = None
        for row in self._mem_all_facts():
            if row.get("id") == fact.get("id"):
                fresh = row
                break
        if fresh is not None:
            self._web_selected_fact = fresh
            self._populate_web_detail(fresh)
    def _on_web_selection_cleared(self):
        self._web_selected_fact = None
        self._disarm_web_forget()
        detail = getattr(self, "mem_web_detail", None)
        # Phase 0: a pinned drawer stays visible on clear (the
        # drawer's contract — the last fact's detail stays up to
        # read); unpinned it hides, as before.
        if detail is not None and not getattr(detail, "pinned", False):
            detail.setVisible(False)

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
                date_text = "Last updated " + parsed.strftime("%d %b %Y")
            except ValueError:
                date_text = f"Last updated {raw}"
        else:
            date_text = "No date recorded"
        self.web_detail_date.setText(date_text)
        self.web_detail_source.setText(
            f"Source: {fact.get('source') or 'unknown'}")
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
        path; everything else uses the store's delete (resolved
        at call time through the facade — see
        _refresh_memory_web)."""
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
                from voice_assistant.core.gui_facade import (
                    memory_store as _store)
                ok = bool(_store.delete_fact(fact_id))
            except Exception:  # noqa: BLE001
                ok = False
        widget = getattr(self, "memory_web", None)
        if widget is not None:
            widget.clear_selection()
        else:
            self._on_web_selection_cleared()
        self._refresh_memories()
        # Phase 0: the web forget has no undo bar and no _notify —
        # without this the delete is silent. The toast is the only
        # confirmation, and it claims no undo (this path is not
        # undoable, unlike the list forget).
        if ok:
            self.show_toast("Forgot this fact.")
        if cat == "learned":
            self._refresh_learned_facts(mark_seen=True)
        if ok:
            # Private mode: the confirmation never names the fact
            # (M-26).
            if self._mem_private_on():
                self._add_system(
                    "Forgot — I no longer remember that.")
            else:
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

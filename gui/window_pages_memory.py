"""MainWindow pages mixin (part): memory + skills pages.

Split from gui/window_pages.py (GUI split #8, move only).
Memory page + skills page + galaxy wiring.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .empty_art import GenerativeArt
from .window_widgets import _TOOL_FRIENDLY_NAMES
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set


class PagesMemoryMixin:
    def _build_memory_page(self) -> QWidget:
        page, layout = self._page_shell("Memory", "Facts VYUK remembers about you. Stored only on this device.", kicker=True)
        # Rethink Build 6 (§6.5, declutter move 7): one toolbar —
        # search + one Filter menu + the List/Web switcher + a "···"
        # overflow. The three control rows (search + count +
        # category, the five-button view row, the web tools row)
        # become this one row; the timeline stays, because time is
        # how you actually browse a memory.
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.mem_search = QLineEdit()
        self.mem_search.setPlaceholderText("Search memory…")
        self.mem_search.textChanged.connect(self._refresh_memories)
        self.mem_search.textChanged.connect(self._on_mem_search_changed)
        toolbar.addWidget(self.mem_search, stretch=1)
        self.btn_mem_filter = QPushButton("Filter: All")
        self.btn_mem_filter.setObjectName("chipBtn")
        self.btn_mem_filter.setToolTip(
            "Filter by category, and choose what the web shows")
        self.btn_mem_filter.clicked.connect(self._show_mem_filter_menu)
        toolbar.addWidget(self.btn_mem_filter)
        # The List/Web switcher stays one tap away — switching
        # views is a daily act, not a rare one.
        self.btn_mem_view_list = QPushButton("List")
        self.btn_mem_view_list.setObjectName("chipBtn")
        self.btn_mem_view_list.setCheckable(True)
        self.btn_mem_view_list.clicked.connect(
            lambda _=False: self._set_memory_view("list"))
        toolbar.addWidget(self.btn_mem_view_list)
        self.btn_mem_view_web = QPushButton("Web")
        self.btn_mem_view_web.setObjectName("chipBtn")
        self.btn_mem_view_web.setCheckable(True)
        self.btn_mem_view_web.clicked.connect(
            lambda _=False: self._set_memory_view("web"))
        toolbar.addWidget(self.btn_mem_view_web)
        self.btn_mem_overflow = QPushButton("···")
        self.btn_mem_overflow.setObjectName("chipBtn")
        self.btn_mem_overflow.setToolTip("More memory actions")
        self.btn_mem_overflow.clicked.connect(self._show_mem_overflow_menu)
        toolbar.addWidget(self.btn_mem_overflow)
        layout.addLayout(toolbar)
        # The "what VYUK remembers" summary stays: the count line
        # under the toolbar. The category combo stays too, hidden,
        # as the filter's state holder (the Filter menu drives it,
        # exactly as the pills did).
        mem_meta_row = QHBoxLayout()
        self.mem_count_label = QLabel("")
        self.mem_count_label.setObjectName("muted")
        mem_meta_row.addWidget(self.mem_count_label)
        mem_meta_row.addStretch()
        layout.addLayout(mem_meta_row)
        self.mem_cat_filter = QComboBox(page)
        # Hidden state holder, never a visible control: hide it
        # explicitly so it can never paint at the page corner.
        self.mem_cat_filter.setVisible(False)
        self.mem_cat_filter.addItem("All categories")
        self.mem_cat_filter.currentTextChanged.connect(
            self._refresh_memories)
        self.mem_cat_filter.setVisible(False)
        # The overflow's actions, built once and re-added to a
        # fresh menu on every open (so the Tour check state and
        # the Forget enablement are always current).
        self._mem_export_action = QAction("Export", self)
        self._mem_export_action.triggered.connect(self._export_memory)
        self._mem_import_action = QAction("Import", self)
        self._mem_import_action.triggered.connect(self._import_memory)
        self._mem_forget_action = QAction("Forget selected", self)
        self._mem_forget_action.triggered.connect(
            self._forget_selected_memory)
        self._mem_tour_action = QAction("▶ Tour", self)
        self._mem_tour_action.setCheckable(True)
        self._mem_tour_action.setToolTip(
            "Fly the camera through every part of the web")
        self._mem_tour_action.toggled.connect(self._on_mem_tour_toggled)
        self._mem_clear_action = QAction(
            "Clear conversation history", self)
        self._mem_clear_action.triggered.connect(
            self._clear_chat_history)
        # v5.18 (Eyes E24–E27): the web's timeline scrubber.
        # Visible only in Web view. The "Show" filter chips and
        # the match label lived here; the chips moved into the
        # Filter menu's Show group (web view only) and the match
        # count is carried by the count line above.
        self.mem_timeline_row = QWidget()
        tl_row = QHBoxLayout(self.mem_timeline_row)
        tl_row.setContentsMargins(0, 0, 0, 0)
        tl_row.setSpacing(6)
        tl_lbl = QLabel("Timeline")
        tl_lbl.setObjectName("muted")
        tl_row.addWidget(tl_lbl)
        self.mem_timeline = QSlider(Qt.Orientation.Horizontal)
        self.mem_timeline.setRange(0, 1000)
        self.mem_timeline.setValue(1000)
        self.mem_timeline.setToolTip("Scrub back through when memories were saved")
        self.mem_timeline.valueChanged.connect(self._on_mem_timeline_changed)
        tl_row.addWidget(self.mem_timeline, stretch=1)
        self.mem_timeline_label = QLabel("All time")
        self.mem_timeline_label.setObjectName("muted")
        tl_row.addWidget(self.mem_timeline_label)
        self._mem_time_span: tuple[float, float] | None = None
        self.mem_timeline_row.setVisible(False)
        layout.addWidget(self.mem_timeline_row)
        self.mem_export_label = QLabel("")
        self.mem_export_label.setObjectName("cardHint")
        self.mem_export_label.setWordWrap(True)
        self.mem_export_label.setVisible(False)
        layout.addWidget(self.mem_export_label)
        self._mem_filter_cats: list[str] = []
        self._mem_web_show: dict[str, bool] = {}
        self._mem_view = "list"
        self._mem_undone_facts: list[dict] = []

        # She learns you (v2.6): what conversation taught her, listed
        # separately ABOVE the hand-saved general facts — reviewable
        # and erasable, fact by fact or all at once.
        self._build_learned_section(layout)

        # Rethink Build 6 (§6.5): the selection bar — bulk actions
        # live here and only here, appearing only while something
        # is selected; and the one-tap-undo bar, shown after a
        # forget. "Clear conversation history" left the page
        # bottom for the "···" overflow (one tap away); the
        # two-tap arm in _clear_chat_history still applies, and
        # its arming is now reported on the status line above.
        self._mem_sel_bar = QWidget()
        sel_row = QHBoxLayout(self._mem_sel_bar)
        sel_row.setContentsMargins(0, 0, 0, 0)
        sel_row.setSpacing(8)
        self._mem_sel_label = QLabel("")
        self._mem_sel_label.setObjectName("muted")
        sel_row.addWidget(self._mem_sel_label)
        sel_row.addStretch()
        self._mem_sel_forget = QPushButton("Forget")
        self._mem_sel_forget.setObjectName("chipBtn")
        self._mem_sel_forget.clicked.connect(
            self._forget_selected_memory)
        sel_row.addWidget(self._mem_sel_forget)
        self._mem_sel_clear = QPushButton("Clear")
        self._mem_sel_clear.setObjectName("chipBtn")
        self._mem_sel_clear.clicked.connect(
            lambda _checked=False: self.memory_list.clearSelection())
        sel_row.addWidget(self._mem_sel_clear)
        self._mem_sel_bar.setVisible(False)
        layout.addWidget(self._mem_sel_bar)
        self._mem_undo_bar = QWidget()
        undo_row = QHBoxLayout(self._mem_undo_bar)
        undo_row.setContentsMargins(0, 0, 0, 0)
        undo_row.setSpacing(8)
        self._mem_undo_label = QLabel("")
        self._mem_undo_label.setObjectName("muted")
        self._mem_undo_label.setWordWrap(True)
        undo_row.addWidget(self._mem_undo_label, stretch=1)
        self._mem_undo_btn = QPushButton("Undo")
        self._mem_undo_btn.setObjectName("chipBtn")
        self._mem_undo_btn.clicked.connect(self._undo_mem_forget)
        undo_row.addWidget(self._mem_undo_btn)
        self._mem_undo_bar.setVisible(False)
        layout.addWidget(self._mem_undo_bar)

        card = QFrame()
        card.setObjectName("card")
        self.mem_list_card = card
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        self.memory_list = QListWidget()
        # Build 6 (§6.5): bulk selection for the selection bar.
        self.memory_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.memory_list.itemSelectionChanged.connect(
            self._on_mem_selection_changed)
        c_layout.addWidget(self.memory_list)
        # v5.24 (Eyes E34): generative art over the empty state —
        # visible exactly when mem_empty is (see _refresh_memories).
        self.mem_art = GenerativeArt(seed=11)
        c_layout.addWidget(self.mem_art)
        self.mem_empty = QLabel("VYUK hasn't learned anything yet.\nTry: “VYUK, remember I prefer tea in the morning.”")
        self.mem_empty.setObjectName("muted")
        self.mem_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(self.mem_empty)
        layout.addWidget(card, stretch=1)

        row = QHBoxLayout()
        self.input_mem_k = QLineEdit()
        self.input_mem_k.setPlaceholderText("What — e.g. Favourite drink")
        self.input_mem_v = QLineEdit()
        self.input_mem_v.setPlaceholderText("Detail — e.g. Masala tea, no sugar")
        btn_save = QPushButton("Remember this")
        btn_save.setObjectName("primaryBtn")
        btn_save.clicked.connect(self._add_memory_fact)
        row.addWidget(self.input_mem_k)
        row.addWidget(self.input_mem_v)
        row.addWidget(btn_save)
        layout.addLayout(row)
        self._mem_add_row_widgets = [self.input_mem_k, self.input_mem_v,
                                     btn_save]

        # The web itself: graph on the left (created lazily on the
        # first switch), fact detail panel on the right, hidden
        # until a node is picked.
        self.memory_web = None
        self.mem_web_container = QFrame()
        self.mem_web_container.setObjectName("card")
        self.mem_web_layout = QHBoxLayout(self.mem_web_container)
        self.mem_web_layout.setContentsMargins(8, 8, 8, 8)
        self.mem_web_layout.setSpacing(8)
        self.mem_web_detail = self._build_web_detail_panel()
        self.mem_web_layout.addWidget(self.mem_web_detail)
        self.mem_web_container.setVisible(False)
        layout.addWidget(self.mem_web_container, stretch=1)

        # "Clear conversation history" lives in the "···" overflow
        # now (Build 6); its two-tap arm is unchanged.
        saved_view = qsettings_get("memory/view", settings=self.settings)
        self._set_memory_view("web" if saved_view == "web" else "list")
        # Golden-fix D2: the page's natural height (~980 px with
        # both web panels) far exceeds short viewports, and with
        # no page-level scroll the VBox crushed the Learned and
        # list cards below their minimums — text cut mid-glyph,
        # panels to slivers, and the empty label's wrap width
        # mis-computed so its line ran past the card edge at
        # 120%. Wrap the whole page the way the Doctor and Pulse
        # pages already are (outer QScrollArea, widgetResizable,
        # frameless): cards keep natural heights and the page
        # scrolls instead of squashing. Stack index, widget names
        # and handlers are unchanged; only the returned wrapper
        # is new. The list inside still shrinks at normal sizes
        # (widgetResizable fills the viewport, stretch applies).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        # setWidget() flips autoFillBackground on (see D6) — the
        # page must stay transparent over the dark shell.
        page.setAutoFillBackground(False)
        return scroll

    def _build_skills_page(self) -> QWidget:
        page, layout = self._page_shell("Skills", "Everything VYUK can do — her built-in skills, plus your own plugins.", kicker=True)
        # v5.18 (Eyes E21): the catalogue, two ways of looking at
        # it — the grouped list, or the Galaxy star map. The choice
        # persists (skills/view); the galaxy is built lazily.
        sview_row = QHBoxLayout()
        sview_row.setSpacing(6)
        self.btn_skills_view_list = QPushButton("Catalogue")
        self.btn_skills_view_list.setObjectName("chipBtn")
        self.btn_skills_view_list.setCheckable(True)
        self.btn_skills_view_list.clicked.connect(
            lambda _=False: self._set_skills_view("list"))
        sview_row.addWidget(self.btn_skills_view_list)
        self.btn_skills_view_galaxy = QPushButton("Galaxy")
        self.btn_skills_view_galaxy.setObjectName("chipBtn")
        self.btn_skills_view_galaxy.setCheckable(True)
        self.btn_skills_view_galaxy.clicked.connect(
            lambda _=False: self._set_skills_view("galaxy"))
        sview_row.addWidget(self.btn_skills_view_galaxy)
        self.skills_galaxy_search = QLineEdit()
        self.skills_galaxy_search.setPlaceholderText("Search the galaxy…")
        self.skills_galaxy_search.setVisible(False)
        self.skills_galaxy_search.textChanged.connect(
            self._on_galaxy_search_changed)
        sview_row.addWidget(self.skills_galaxy_search, stretch=1)
        sview_row.addStretch()
        # v5.57 (redo): the mockup's total, right-aligned in the
        # view row. Filled from the registry just below; it stays
        # empty (rather than wrong) if the registry cannot be read.
        self.skills_count_label = QLabel("")
        self.skills_count_label.setObjectName("muted")
        sview_row.addWidget(self.skills_count_label)
        layout.addLayout(sview_row)
        self.skills_galaxy = None
        self.skills_galaxy_card_widget = None
        self.skills_detail_card = None
        # Built-in catalogue (v3.2): all registered tools, grouped and
        # searchable — INCLUDING plugin-provided tools, which land
        # in "More skills" through the group_for_name fallback
        # (GAPS-29, E-V1). The page used to exclude them here
        # ("they live in the plugins list below, so nothing is
        # listed twice") — but that exclusion defeated the
        # fallback: the catalogue could never show every skill,
        # and grouped could never equal total. A plugin tool now
        # appears in the catalogue (under "More skills") AND in
        # the "Your plugins" management list below — the catalogue
        # is the complete inventory, the list below manages the
        # plugin files. The count label just below already counts
        # every declaration, so the two now agree.
        try:
            from voice_assistant.core.gui_facade import tools as _tool_registry  # Phase 4 S1b INTENTIONAL: registry split to funnel
            from voice_assistant.gui.skills_catalog import build_skills_catalog

            _decls = list(_tool_registry.get_declarations())
            cat_card = QFrame()
            cat_card.setObjectName("card")
            self.skills_cat_card = cat_card
            cat_layout = QVBoxLayout(cat_card)
            cat_layout.setContentsMargins(16, 12, 16, 12)
            self.skills_catalog = build_skills_catalog(_decls, _TOOL_FRIENDLY_NAMES)
            cat_layout.addWidget(self.skills_catalog)
            layout.addWidget(cat_card, stretch=3)
        except Exception as e:  # noqa: BLE001 — the page must still build
            print(f"[VYUK] Skills catalogue unavailable: {e}")
            self.skills_catalog = None
            self.skills_cat_card = None
        # v5.57 (redo): the mockup's total — every skill she has,
        # built-ins plus plugins, from the registry itself.
        try:
            from voice_assistant.core.gui_facade import tools as _count_registry  # Phase 4 S1b INTENTIONAL: registry split to funnel
            self.skills_count_label.setText(
                f"{len(_count_registry.get_declarations())} skills")
        except Exception:
            pass  # no count rather than a wrong one
        # The galaxy container: star map on the left, the picked
        # skill's detail card on the right — hidden until the
        # Galaxy view is chosen (built lazily in _ensure_skills_galaxy).
        self.skills_galaxy_container = QFrame()
        self.skills_galaxy_container.setObjectName("card")
        sg_layout = QHBoxLayout(self.skills_galaxy_container)
        sg_layout.setContentsMargins(8, 8, 8, 8)
        sg_layout.setSpacing(8)
        self.skills_galaxy_container.setVisible(False)
        layout.addWidget(self.skills_galaxy_container, stretch=3)
        saved_sview = qsettings_get("skills/view", settings=self.settings)
        self._set_skills_view("galaxy" if saved_sview == "galaxy" else "list")
        plugins_label = QLabel("Your plugins")
        plugins_label.setObjectName("cardTitle")
        layout.addWidget(plugins_label)
        card = QFrame()
        card.setObjectName("card")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        self.plugin_list = QListWidget()
        c_layout.addWidget(self.plugin_list)
        layout.addWidget(card, stretch=1)
        btn_rescan = QPushButton("Rescan plugins folder")
        btn_rescan.clicked.connect(self._reload_plugins)
        layout.addWidget(btn_rescan)
        # Golden-fix D3 + D4 (same mechanism as D2): at short
        # heights the page VBox crushed the catalogue card below
        # its minimum — the search field cut mid-glyph and the
        # list viewport down to one header row — and crushed the
        # galaxy container below the canvas minimum (280 px), so
        # the star ring showed only as a clipped strip of edge
        # dots. Wrap the whole page like Doctor / Pulse / Memory:
        # the catalogue keeps its natural height (search whole,
        # list at its sizeHint, scrollable within), the galaxy
        # canvas keeps >= 280 px, and the page scrolls instead of
        # squashing. The mid-glyph row cut at a list's own bottom
        # edge remains normal scroll behaviour and is NOT
        # "fixed" here. Stack index, widget names and handlers
        # are unchanged; only the returned wrapper is new.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        # setWidget() flips autoFillBackground on (see D6).
        page.setAutoFillBackground(False)
        return scroll

    # ------------------------------------------------- skills galaxy

    def _set_skills_view(self, mode: str):
        """Catalogue | Galaxy toggle for the Skills page (v5.18,
        Eyes E21). Galaxy mode swaps the catalogue card for the
        star map + detail card; the plugins list below stays —
        plugins aren't stars. The choice persists in skills/view."""
        mode = "galaxy" if mode == "galaxy" else "list"
        try:
            qsettings_set("skills/view", mode, settings=self.settings)
        except Exception:  # noqa: BLE001
            pass
        for btn, value in ((self.btn_skills_view_list, "list"),
                           (self.btn_skills_view_galaxy, "galaxy")):
            btn.blockSignals(True)
            btn.setChecked(mode == value)
            btn.blockSignals(False)
        galaxy = mode == "galaxy"
        cat_card = getattr(self, "skills_cat_card", None)
        if cat_card is not None:
            cat_card.setVisible(not galaxy)
        self.skills_galaxy_search.setVisible(galaxy)
        self.skills_galaxy_container.setVisible(galaxy)
        if galaxy:
            self._ensure_skills_galaxy()

    def _ensure_skills_galaxy(self):
        """Build the galaxy on first use only — the catalogue stays
        the zero-cost default. Fed exactly like the list: the real
        registry, plugin tools included (GAPS-29, E-V1 — they land
        in "More skills" through the group_for_name fallback)."""
        if getattr(self, "skills_galaxy", None) is not None:
            return self.skills_galaxy
        try:
            from voice_assistant.core.gui_facade import tools as _tool_registry  # Phase 4 S1b INTENTIONAL: registry split to funnel
            from voice_assistant.gui.skills_galaxy import (
                SkillDetailCard,
                SkillsGalaxyWidget,
            )

            _decls = list(_tool_registry.get_declarations())
            galaxy = SkillsGalaxyWidget()
            galaxy.set_data(_decls, _TOOL_FRIENDLY_NAMES)
            galaxy.skill_chosen.connect(self._on_galaxy_skill_chosen)
            galaxy.selection_cleared.connect(self._on_galaxy_selection_cleared)
            card = SkillDetailCard()
            card.setFixedWidth(280)
            card.ask_requested.connect(self._on_galaxy_ask)
            layout = self.skills_galaxy_container.layout()
            layout.addWidget(galaxy, stretch=1)
            layout.addWidget(card)
            self.skills_galaxy = galaxy
            self.skills_detail_card = card
            if self.skills_galaxy_search.text():
                galaxy.set_search_highlight(self.skills_galaxy_search.text())
        except Exception as e:  # noqa: BLE001 — list view still works
            print(f"[VYUK] Skills galaxy unavailable: {e}")
            self.skills_galaxy = None
        return self.skills_galaxy

    def _on_galaxy_skill_chosen(self, name: str):
        galaxy = getattr(self, "skills_galaxy", None)
        card = getattr(self, "skills_detail_card", None)
        if galaxy is None or card is None:
            return
        star = galaxy.galaxy.node(name)
        if star is None:
            return
        card.set_skill(star.name, star.label, star.group, star.description)

    def _on_galaxy_selection_cleared(self):
        card = getattr(self, "skills_detail_card", None)
        if card is not None:
            card.clear()

    def _on_galaxy_search_changed(self, text: str):
        galaxy = getattr(self, "skills_galaxy", None)
        if galaxy is not None:
            galaxy.set_search_highlight(text)

    def _on_galaxy_ask(self, name: str, friendly: str):
        """E22 'Ask with this': carry the picked skill to the
        Assistant page as the start of a typed request — the user
        finishes the sentence, nothing is sent for them."""
        self._show_page(0)
        self.input_cmd.setText(f"Use {friendly} to ")
        self.input_cmd.setFocus()

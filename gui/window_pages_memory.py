"""MainWindow pages mixin (part): memory + skills pages.

Split from gui/window_pages.py (GUI split #8, move only).
Memory page + skills page + galaxy wiring.
"""

import datetime
import json
import re
from html import escape

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .design_system import HudBackdrop, HudPanel, HudSwitch, HudTabs
from .design_system.empty import EmptyState
from .design_system.flowlayout import FlowLayout
from .design_system.switches import toggle_row
from .empty_art import GenerativeArt
from .memory_web_graph import (
    _fact_epoch,
    category_color,
    category_label,
)
from .style import (
    ACCENT, BG, LINE_SUBTLE, MONO_FONT_STACK, SURFACE_2, TEXT_MUTED,
    first_font_family)
from .window_memory_web import _mem_when_text
from .window_widgets import _TOOL_FRIENDLY_NAMES
from voice_assistant.core.gui_facade import (
    privacy_copy,
    qsettings_get,
    qsettings_remove,
    qsettings_set,
)


class _ResizeWatcher(QObject):
    """Calls fn() whenever the watched widget resizes. A small
    dedicated watcher (UI Phase 2, galaxy stacking): the window
    already has eventFilter definitions across several mixins,
    so the page does not add another one to that chain."""

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.Resize:
            try:
                self._fn()
            except Exception:  # noqa: BLE001
                pass  # a layout callback never breaks the page.
        return False


class MemoryFactCard(QWidget):
    """One fact as a card-row (Phase 1 §3/§10): bg fill + 1 px
    LINE_SUBTLE edge + the category colour chip (an 8 px colour
    square ONLY — the category NAME is text_muted text beside
    it), the fact text (key — value), the age ("last updated",
    from updated_at), the source, and Edit / Pin / Forget in a
    fixed trailing slot. The actions reveal instantly on hover
    or selection (M-27: visibility only, no fade, no slide) and
    the slot keeps its width while they hide, so revealing them
    never reflows the row. The selected card rises to a surface_2
    fill + accent edge + corner ticks; a pinned fact carries the
    accent edge + a text_muted PIN glyph (M-24). The search
    highlight (M-24) wraps the literal query term in an accent
    <span> inside the fact text (key/value) only, while a search
    is active. There is deliberately NO confidence bar (D-M2:
    the store has no confidence field)."""

    def __init__(self, fact, when_text, on_edit, on_pin, on_forget,
                 search_query="", font_scale=1.0):
        super().__init__()
        self.fact = dict(fact or {})
        self.fact_id = self.fact.get("id")
        self.fact_key = str(self.fact.get("key") or "")
        self._on_edit = on_edit
        self._on_pin = on_pin
        self._on_forget = on_forget
        self._selected = False
        self._hovered = False
        self._pinned = bool(self.fact.get("user_pinned"))
        cat = str(self.fact.get("category") or "general").strip().lower() \
            or "general"
        self._cat_color = category_color(cat)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)
        # Category colour chip: an 8 px square, nothing else.
        chip = QWidget()
        chip.setFixedSize(8, 8)
        chip.setStyleSheet(f"background: {self._cat_color}; border: none;")
        chip.setToolTip(category_label(cat))
        layout.addWidget(chip)
        cat_lbl = QLabel(category_label(cat))
        cat_lbl.setObjectName("muted")
        layout.addWidget(cat_lbl)
        # Fact text: key over value, so the value label can carry
        # the search highlight on its own (leg 54).
        text_wrap = QWidget()
        text_layout = QVBoxLayout(text_wrap)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)
        self.factKeyLabel = QLabel()
        self.factKeyLabel.setObjectName("factKeyLabel")
        self.factKeyLabel.setWordWrap(True)
        self.factValueLabel = QLabel()
        self.factValueLabel.setObjectName("factValueLabel")
        self.factValueLabel.setWordWrap(True)
        query = (search_query or "").strip()
        self._set_highlighted(self.factKeyLabel, self.fact_key, query)
        self._set_highlighted(
            self.factValueLabel, str(self.fact.get("value") or ""), query)
        text_layout.addWidget(self.factKeyLabel)
        text_layout.addWidget(self.factValueLabel)
        layout.addWidget(text_wrap, stretch=1)
        # Age ("last updated") + source, muted microcopy.
        meta = QWidget()
        meta_layout = QVBoxLayout(meta)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(0)
        self.when = QLabel(
            f"last updated {when_text}" if when_text
            else "no date recorded")
        self.when.setObjectName("muted")
        when_font = QFont(first_font_family(MONO_FONT_STACK))
        when_font.setPointSizeF(11.5 * float(font_scale or 1.0))
        self.when.setFont(when_font)
        meta_layout.addWidget(self.when)
        source_lbl = QLabel(str(self.fact.get("source") or "unknown"))
        source_lbl.setObjectName("muted")
        meta_layout.addWidget(source_lbl)
        layout.addWidget(meta)
        # Pin glyph: text_muted, never accent (M-24).
        self.factPinGlyph = QLabel("PIN")
        self.factPinGlyph.setObjectName("factPinGlyph")
        self.factPinGlyph.setStyleSheet(f"color: {TEXT_MUTED};")
        self.factPinGlyph.setVisible(self._pinned)
        layout.addWidget(self.factPinGlyph)
        # Actions in a fixed trailing slot (M-27): the container
        # keeps its width while the buttons hide, so the row
        # never reflows when they reveal.
        self._actions = QWidget()
        actions_layout = QHBoxLayout(self._actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)
        self.factEditBtn = QPushButton("Edit")
        self.factEditBtn.setObjectName("factEditBtn")
        self.factEditBtn.setToolTip("Edit this fact")
        self.factEditBtn.clicked.connect(
            lambda _checked=False: self._on_edit(self.fact))
        actions_layout.addWidget(self.factEditBtn)
        self.factPinBtn = QPushButton("Unpin" if self._pinned else "Pin")
        self.factPinBtn.setObjectName("factPinBtn")
        self.factPinBtn.setToolTip("Pin this fact to the top")
        self.factPinBtn.clicked.connect(
            lambda _checked=False: self._on_pin(self.fact))
        actions_layout.addWidget(self.factPinBtn)
        self.factForgetBtn = QPushButton("Forget")
        self.factForgetBtn.setObjectName("factForgetBtn")
        self.factForgetBtn.setToolTip("Forget this fact")
        self.factForgetBtn.clicked.connect(
            lambda _checked=False: self._on_forget(self.fact_id))
        actions_layout.addWidget(self.factForgetBtn)
        actions_width = sum(
            b.sizeHint().width() for b in (
                self.factEditBtn, self.factPinBtn, self.factForgetBtn)) + 8
        self._actions.setFixedWidth(actions_width)
        for btn in (self.factEditBtn, self.factPinBtn, self.factForgetBtn):
            btn.setVisible(False)
        layout.addWidget(self._actions)
        self._apply_style()

    @staticmethod
    def _set_highlighted(label, text, query):
        """The literal query term wrapped in an accent <span>
        (M-24: fact text only, functional while searching). Plain
        text stays plain — no escaping, no stray markup — unless
        the term actually matches."""
        if not query:
            label.setText(text)
            return
        idx = text.lower().find(query.lower())
        if idx < 0:
            label.setText(text)
            return
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(
            escape(text[:idx])
            + f'<span style="color: {ACCENT};">'
            + escape(text[idx:idx + len(query)])
            + "</span>"
            + escape(text[idx + len(query):]))

    def _apply_style(self):
        """Rest: bg fill + LINE_SUBTLE edge. Selected: surface_2
        fill + accent edge. Pinned: accent edge (pinning is
        visible)."""
        if self._selected:
            fill, edge = SURFACE_2, ACCENT
        elif self._pinned:
            fill, edge = BG, ACCENT
        else:
            fill, edge = BG, LINE_SUBTLE
        self.setStyleSheet(
            f"MemoryFactCard {{ background: {fill}; "
            f"border: 1px solid {edge}; }}")

    def set_selected(self, selected: bool) -> None:
        """Selection is a state change (edge + fill), instant —
        never an animation. The actions reveal with it."""
        selected = bool(selected)
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_style()
        self._update_actions()
        self.update()

    def _update_actions(self) -> None:
        show = self._selected or self._hovered
        # The buttons ride a fixed-width container (M-27: no
        # reflow) — the container itself must follow the same
        # show/hide, or the buttons stay invisible inside a
        # hidden parent no matter what setVisible they get.
        self._actions.setVisible(show)
        for btn in (self.factEditBtn, self.factPinBtn, self.factForgetBtn):
            btn.setVisible(show)

    def enterEvent(self, event):  # noqa: N802 (Qt naming)
        self._hovered = True
        self._update_actions()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt naming)
        self._hovered = False
        self._update_actions()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        super().paintEvent(event)
        if not (self._selected or self._pinned):
            return
        # Corner ticks (8 px) on the selected / pinned card — the
        # stylesheet alone cannot paint them.
        painter = QPainter(self)
        painter.setPen(QPen(QColor(ACCENT), 2))
        tick = 8
        w, h = self.width(), self.height()
        painter.drawLine(0, tick, 0, 0)
        painter.drawLine(0, 0, tick, 0)
        painter.drawLine(w - tick, h - 1, w - 1, h - 1)
        painter.drawLine(w - 1, h - tick, w - 1, h - 1)


class PagesMemoryMixin:
    def _build_memory_page(self) -> QWidget:
        # L-31 (loophole batch 8): the subtitle lives in
        # core/privacy_copy.py (MEMORY_PAGE_SUBTITLE). "Stored only
        # on this device" is true of the storage; the qualification
        # says what a cloud turn does with the stored facts.
        from voice_assistant.core.gui_facade import privacy_copy
        page, layout = self._page_shell(
            "Memory", privacy_copy.MEMORY_PAGE_SUBTITLE, kicker=True)
        self._mem_page = page
        # Phase 1 §10: one HudBackdrop behind the page (vignette
        # intensity — flat bg + vignette, no grid, no scanlines;
        # look-fix 2026-11-24, the checkered grid read as a
        # checkerboard and the owner rejected it. The profile
        # system drives it later). It is the page's first child so
        # it paints behind everything; its geometry is synced to
        # the page on every refresh (see _refresh_memory_sections)
        # — this mixin owns no resize event-filter seam.
        self._mem_backdrop = HudBackdrop(page, intensity="vignette")
        self._mem_backdrop.lower()
        # Rethink Build 6 (§6.5) → Phase 1 §2: one toolbar — the
        # search pill + the "···" overflow. The search stays ABOVE
        # the tabs, shared by every section. The List/Web switcher
        # is gone (List is Overview, Web is the Knowledge Graph
        # section below); the "Filter: All" button is gone too
        # (the Filter menu's category group goes away — categories
        # are filter chips on the page now).
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        search_pill = HudPanel(glass=True, pill=True)
        search_pill.setObjectName("memorySearchPill")
        pill_layout = search_pill.content_layout()
        pill_layout.setContentsMargins(16, 0, 16, 0)
        self.mem_search = QLineEdit()
        self.mem_search.setPlaceholderText("Search memory…")
        self.mem_search.setStyleSheet(
            "border: none; background: transparent;")
        self.mem_search.textChanged.connect(self._refresh_memories)
        self.mem_search.textChanged.connect(self._on_mem_search_changed)
        pill_layout.addWidget(self.mem_search)
        toolbar.addWidget(search_pill, stretch=1)
        self._memory_search = self.mem_search
        self.btn_mem_overflow = QPushButton("···")
        self.btn_mem_overflow.setObjectName("chipBtn")
        self.btn_mem_overflow.setToolTip("More memory actions")
        self.btn_mem_overflow.clicked.connect(self._show_mem_overflow_menu)
        toolbar.addWidget(self.btn_mem_overflow)
        layout.addLayout(toolbar)
        # The "what VYUK remembers" summary stays: the count line
        # under the toolbar (E-F204 rule kept). The category combo
        # stays too, hidden, as the filter's state holder (the
        # filter chips drive it, exactly as the menu did).
        mem_meta_row = QHBoxLayout()
        self.mem_count_label = QLabel("")
        self.mem_count_label.setObjectName("muted")
        mem_meta_row.addWidget(self.mem_count_label)
        mem_meta_row.addStretch()
        layout.addLayout(mem_meta_row)
        self.mem_export_label = QLabel("")
        self.mem_export_label.setObjectName("cardHint")
        self.mem_export_label.setWordWrap(True)
        self.mem_export_label.setVisible(False)
        layout.addWidget(self.mem_export_label)
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
        self._mem_filter_cats: list[str] = []
        self._mem_web_show: dict[str, bool] = {}
        self._mem_view = "list"
        self._mem_undone_facts: list[dict] = []
        self._mem_editing_fact: dict | None = None
        self._mem_undated_only = False
        self._memory_canvas = None
        self.memory_web = None
        # Phase 1 §2: the stat strip — four counts from their real
        # reads, zero reads as zero (HudPanel section container).
        # Stat numbers are TEXT, not accent (M-19).
        self._mem_stat_panel = HudPanel()
        self._mem_stat_panel.setObjectName("memoryStatStrip")
        stat_layout = self._mem_stat_panel.content_layout()
        stat_layout.setContentsMargins(16, 12, 16, 12)
        stat_row = QHBoxLayout()
        stat_row.setSpacing(16)
        self._mem_stat_numbers: list[QLabel] = []
        self._mem_stat_deltas: list[QLabel] = []
        for caption in ("Facts", "Learned", "Documents", "Episodes"):
            cell = QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(2)
            number = QLabel("0")
            cell.addWidget(number)
            self._mem_stat_numbers.append(number)
            cap_lbl = QLabel(caption)
            cap_lbl.setObjectName("muted")
            cell.addWidget(cap_lbl)
            # The delta line exists only on Facts + Learned
            # (M-23): Documents and Episodes have no updated_at
            # and carry no delta line — a missing delta means "no
            # weekly source for this cell", never zero. The empty
            # label keeps the cells aligned.
            delta = QLabel("")
            delta.setObjectName("muted")
            cell.addWidget(delta)
            self._mem_stat_deltas.append(delta)
            stat_row.addLayout(cell, stretch=1)
        stat_layout.addLayout(stat_row)
        self._mem_stat_private = QLabel("Private mode is on")
        self._mem_stat_private.setObjectName("muted")
        self._mem_stat_private.setVisible(False)
        stat_layout.addWidget(self._mem_stat_private)
        layout.addWidget(self._mem_stat_panel)
        # Phase 1 §2: the taxonomy tabs. HudTabs (the shared
        # component) as a bare tab bar — NOT a QTabWidget: the six
        # sections live in ONE scroll area (the page scrolls by
        # design, E-F302), and the bar only sets the ACTIVE
        # section — it persists in QSettings (memory/section),
        # drives the count line + search placeholder, and scrolls
        # the section into view. Sections are never hidden by a
        # tab switch (a hidden ancestor would fail the hover-
        # reveal leg, which needs a card's Pin visible while the
        # Projects tab is active).
        self._memory_tabs = HudTabs()
        self._memory_tabs.setObjectName("_memory_tabs")
        # "&" is a mnemonic marker in Qt tab text — a single "&"
        # renders as an underscore ("Projects_Context"). "&&"
        # displays the literal "&" the plan names; tabText()
        # returns the stored "&&", so leg 22 expects that form.
        for _name in ("Overview", "Profile", "Projects && Context",
                      "Episodes", "Knowledge Graph", "Review Inbox"):
            self._memory_tabs.addTab(_name)
        self._memory_tabs.currentChanged.connect(
            self._on_memory_section_changed)
        layout.addWidget(self._memory_tabs)
        # The Profile section is built BEFORE the learned card:
        # it owns the learning toggle (chk_learn), and the toggle
        # must exist before _refresh_learned_facts() runs. The
        # section widget joins the page layout below, in display
        # order — build order is not layout order.
        profile_section = self._build_memory_profile_section()
        # ---- Overview (the default section) ----
        # Learned today / this week strip (HudPanel): "added or
        # updated" — updated_at is a last-updated stamp (M-02).
        # Undated facts sit in an explicit "no date recorded"
        # bucket, and clicking it lists those facts.
        self._mem_learned_strip = HudPanel()
        self._mem_learned_strip.setObjectName("memoryLearnedStrip")
        strip_layout = self._mem_learned_strip.content_layout()
        strip_layout.setContentsMargins(16, 12, 16, 12)
        strip_row = QHBoxLayout()
        strip_row.setSpacing(16)
        self._mem_learned_today = QLabel("")
        self._mem_learned_week = QLabel("")
        self._mem_learned_undated = QPushButton("")
        self._mem_learned_undated.setObjectName("chipBtn")
        self._mem_learned_undated.setCheckable(True)
        self._mem_learned_undated.clicked.connect(
            self._on_mem_undated_toggled)
        strip_row.addWidget(self._mem_learned_today)
        strip_row.addWidget(self._mem_learned_week)
        strip_row.addWidget(self._mem_learned_undated)
        strip_row.addStretch()
        strip_layout.addLayout(strip_row)
        self._mem_learned_strip_private = QLabel("Private mode is on")
        self._mem_learned_strip_private.setObjectName("muted")
        self._mem_learned_strip_private.setVisible(False)
        strip_layout.addWidget(self._mem_learned_strip_private)
        layout.addWidget(self._mem_learned_strip)
        # Filter chips with counts (HudPanel): one chip per
        # category over the WHOLE store (M-07), "All" clears.
        # The learned card is never filtered by the chips.
        self._mem_chip_panel = HudPanel()
        self._mem_chip_panel.setObjectName("memoryFilterChips")
        chip_layout = self._mem_chip_panel.content_layout()
        chip_layout.setContentsMargins(16, 12, 16, 12)
        self._mem_chip_flow = FlowLayout(spacing=6)
        chip_layout.addLayout(self._mem_chip_flow)
        self._mem_chip_private = QLabel("Private mode is on")
        self._mem_chip_private.setObjectName("muted")
        self._mem_chip_private.setVisible(False)
        chip_layout.addWidget(self._mem_chip_private)
        self._mem_chip_buttons: list[QPushButton] = []
        layout.addWidget(self._mem_chip_panel)
        # She learns you (v2.6): what conversation taught her,
        # listed separately ABOVE the hand-saved general facts —
        # reviewable and erasable, fact by fact or all at once.
        # The master toggle moved to the Profile section; the
        # card itself does not move.
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
        # The fact list: cards, not rows (Phase 1 §3).
        card = QFrame()
        card.setObjectName("card")
        self.mem_list_card = card
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        self.memory_list = QListWidget()
        # Build 6 (§6.5): bulk selection for the selection bar.
        self.memory_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        # S-M09: the list's own selection background is disabled
        # — selection is shown by the card's own edge only.
        self.memory_list.setStyleSheet(
            "QListWidget::item:selected { background: transparent; }"
            "QListWidget::item:selected:!active "
            "{ background: transparent; }")
        self.memory_list.itemSelectionChanged.connect(
            self._on_mem_selection_changed)
        c_layout.addWidget(self.memory_list)
        # v5.24 (Eyes E34): generative art over the empty state —
        # visible exactly when mem_empty is (see _refresh_memories).
        self.mem_art = GenerativeArt(seed=11)
        c_layout.addWidget(self.mem_art)
        # Phase 0: the empty state is the shared EmptyState, not a
        # bare label — same words, plus the safe action (focus the
        # add-fact input). The generative art above it stays; the
        # visibility rule in _refresh_memories is unchanged.
        self.mem_empty = EmptyState(
            sentence="VYUK hasn't learned anything yet.",
            suggested_task="Try: “VYUK, remember I prefer tea in the morning.”",
            action_label="Remember something")
        self.mem_empty.action_requested.connect(
            lambda: self.input_mem_k.setFocus())
        c_layout.addWidget(self.mem_empty)
        self._mem_list_private = QLabel("Private mode is on")
        self._mem_list_private.setObjectName("muted")
        self._mem_list_private.setVisible(False)
        c_layout.addWidget(self._mem_list_private)
        layout.addWidget(card, stretch=1)
        # The add-fact row stays at the bottom of Overview. When
        # editing a fact, the same row is pre-filled and the
        # button reads "Save changes" (D-M3).
        row = QHBoxLayout()
        self.input_mem_k = QLineEdit()
        self.input_mem_k.setPlaceholderText("What — e.g. Favourite drink")
        self.input_mem_v = QLineEdit()
        self.input_mem_v.setPlaceholderText("Detail — e.g. Masala tea, no sugar")
        btn_save = QPushButton("Remember this")
        btn_save.setObjectName("primaryBtn")
        # ≥4.5:1 by measurement on the dark theme (TEXT on BG
        # 18.66, BG on ACCENT 11.63): a solid accent fill with
        # dark text, so the button cannot read as a hairline
        # outline (audit miss 9).
        btn_save.setStyleSheet(
            f"background: {ACCENT}; color: {BG}; "
            f"border: 1px solid {ACCENT}; border-radius: 16px; "
            "padding: 8px 20px; font-weight: 600;")
        btn_save.clicked.connect(self._add_memory_fact)
        row.addWidget(self.input_mem_k)
        row.addWidget(self.input_mem_v)
        row.addWidget(btn_save)
        layout.addLayout(row)
        self._mem_add_row_widgets = [self.input_mem_k, self.input_mem_v,
                                     btn_save]
        # Darwin D1: the Documents section — the Memory page's
        # document half, beside the learned-facts views above
        # (untouched). Lists indexed documents, searches them by
        # meaning (semantic first, FTS fallback, honest when the
        # model is absent), shows one document at a time, and
        # forgets one document on request. "Forget everything
        # learned" stays facts-only.
        self._build_documents_section(layout)
        # "Private by design": one muted line; the full promise
        # (PRIVACY_PROMISE) on hover.
        privacy_line = QLabel("Private by design")
        privacy_line.setObjectName("muted")
        privacy_line.setToolTip(privacy_copy.PRIVACY_PROMISE)
        layout.addWidget(privacy_line)
        # ---- the other five sections, in display order ----
        layout.addWidget(profile_section)
        layout.addWidget(self._build_memory_projects_section())
        layout.addWidget(self._build_memory_episodes_section())
        layout.addWidget(self._build_memory_graph_section())
        layout.addWidget(self._build_memory_review_section())
        # The saved view migrates once (M-08): "web" lands on the
        # Knowledge Graph section, then the key is retired. The
        # active tab persists in memory/section from here on.
        saved_view = qsettings_get("memory/view", settings=self.settings)
        try:
            qsettings_remove("memory/view", settings=self.settings)
        except Exception:  # noqa: BLE001 — the key may already be gone
            pass
        initial = 4 if saved_view == "web" else self._mem_saved_section()
        self._memory_tabs.setCurrentIndex(initial)
        self._refresh_memories()
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
        self._mem_page_scroll = scroll
        return scroll

    def _build_documents_section(self, layout) -> None:
        """Darwin D1: the Documents section of the Memory page.
        List + semantic search + per-document view + forget, over
        the user's indexed folders. The learned-facts views above
        are untouched; this section is the document half, so it
        lives in the LIST view — _set_memory_view hides the whole
        section (self._doc_section) in Web view, like the list
        card, so the graph keeps its pre-D1 geometry."""
        section = QWidget()
        self._doc_section = section
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(layout.spacing())
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        doc_title = QLabel("Documents")
        doc_title.setObjectName("cardTitle")
        title_row.addWidget(doc_title)
        self._doc_count_label = QLabel("")
        self._doc_count_label.setObjectName("muted")
        # Wraps: the count line ("3 file(s) · 3 passage(s) ·
        # semantic search off — …") is ~500 px unwrapped and
        # would force the whole Memory page 65 px wider than
        # its pre-D1 658 px minimum at a 640 px window; wrapped
        # it takes two lines at narrow widths and one at 1280.
        self._doc_count_label.setWordWrap(True)
        # Stretch 1 (no separate spacer): the label takes the
        # row's free width — one line at 1280, two wrapped lines
        # at 640 — while its wrapped minimum stays one word
        # wide, so it never forces the page wider.
        title_row.addWidget(self._doc_count_label, stretch=1)
        btn_doc_refresh = QPushButton("Refresh")
        btn_doc_refresh.setObjectName("chipBtn")
        btn_doc_refresh.setToolTip(
            "Re-index the remembered folders")
        btn_doc_refresh.clicked.connect(self._on_doc_refresh)
        title_row.addWidget(btn_doc_refresh)
        s_layout.addLayout(title_row)
        self._doc_hint = QLabel(
            "Your indexed folders, searchable by meaning. "
            "Say “remember this folder” to add one.")
        self._doc_hint.setObjectName("cardHint")
        self._doc_hint.setWordWrap(True)
        s_layout.addWidget(self._doc_hint)

        card = QFrame()
        card.setObjectName("card")
        self.mem_doc_card = card
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self._doc_search = QLineEdit()
        self._doc_search.setPlaceholderText(
            "Search documents by meaning…")
        self._doc_search.returnPressed.connect(self._on_doc_search)
        search_row.addWidget(self._doc_search, stretch=1)
        btn_doc_search = QPushButton("Search")
        btn_doc_search.setObjectName("chipBtn")
        btn_doc_search.clicked.connect(self._on_doc_search)
        search_row.addWidget(btn_doc_search)
        c_layout.addLayout(search_row)
        self._doc_list = QListWidget()
        self._doc_list.setMinimumHeight(120)
        self._doc_list.itemClicked.connect(self._on_doc_selected)
        c_layout.addWidget(self._doc_list)
        self._doc_detail = QLabel("Pick a document to read it.")
        self._doc_detail.setObjectName("cardHint")
        self._doc_detail.setWordWrap(True)
        self._doc_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        c_layout.addWidget(self._doc_detail)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_add = QPushButton("Add folder…")
        btn_add.setObjectName("chipBtn")
        btn_add.clicked.connect(self._on_doc_add_folder)
        btn_row.addWidget(btn_add)
        self._doc_forget_btn = QPushButton("Forget document")
        self._doc_forget_btn.setObjectName("chipBtn")
        self._doc_forget_btn.setEnabled(False)
        self._doc_forget_btn.clicked.connect(self._on_doc_forget)
        btn_row.addWidget(self._doc_forget_btn)
        btn_row.addStretch()
        c_layout.addLayout(btn_row)
        s_layout.addWidget(card, stretch=1)
        layout.addWidget(section, stretch=1)
        self._doc_results: list = []
        self._doc_selected_source: str | None = None
        self._refresh_documents()

    def _refresh_documents(self) -> None:
        """Re-list the indexed documents + the semantic state."""
        try:
            from voice_assistant.core.gui_facade import (
                document_memory as _dm)
            if not _dm.document_store_exists():
                # No store file yet: opening the page must not
                # create one. The user bridges their folders in
                # with Refresh (or adds one below).
                self._doc_count_label.setText(
                    "No documents in memory yet")
                self._doc_list.clear()
                self._doc_results = []
                self._doc_selected_source = None
                self._doc_forget_btn.setEnabled(False)
                self._doc_detail.setText(
                    "Press Refresh to bring your indexed folders "
                    "into document memory, or add a folder below.")
                return
            data = _dm.list_documents(limit=50)
        except Exception:  # noqa: BLE001 — the page must still build
            self._doc_count_label.setText(
                "Document memory unavailable")
            return
        files = data.get("files", []) or []
        if data.get("semantic_available"):
            state = "semantic search ready"
        elif data.get("model_ready"):
            state = "semantic search readying — documents not yet embedded"
        else:
            state = "semantic search off — embedding model not installed"
        self._doc_count_label.setText(
            f"{data.get('total_files', 0)} file(s) · "
            f"{data.get('total_passages', 0)} passage(s) · {state}")
        self._doc_results = files
        self._doc_selected_source = None
        self._doc_forget_btn.setEnabled(False)
        self._doc_list.clear()
        for entry in files:
            title = str(entry.get("title") or "Untitled")
            snippet = " ".join(
                str(entry.get("snippet") or "").split())
            self._doc_list.addItem(
                f"{title}  ·  {snippet}" if snippet else title)
        self._doc_detail.setText("Pick a document to read it.")

    def _on_doc_search(self) -> None:
        """Semantic-first document search (FTS fallback, honest
        when the model is absent). An empty query re-lists."""
        query = self._doc_search.text().strip()
        if not query:
            self._refresh_documents()
            return
        try:
            from voice_assistant.core.gui_facade import (
                document_memory as _dm)
            data = _dm.search_documents(query, limit=20)
        except Exception as exc:  # noqa: BLE001 — say so, don't crash
            self._doc_detail.setText(f"Document search failed: {exc}")
            return
        results = data.get("results", []) or []
        lanes = data.get("lanes", []) or []
        self._doc_results = results
        self._doc_selected_source = None
        self._doc_forget_btn.setEnabled(False)
        self._doc_list.clear()
        for item in results:
            title = str(item.get("title") or "Untitled")
            passage = " ".join(str(item.get("passage") or "").split())
            self._doc_list.addItem(
                f"{title}  ·  {passage[:160]}" if passage else title)
        if not results:
            self._doc_detail.setText(
                "No documents matched that — try different words, "
                "or index a folder first.")
            return
        lane_line = "Matched by: " + ", ".join(lanes) + "."
        if not data.get("semantic_available"):
            lane_line += (" Semantic search is unavailable "
                          "(embedding model not installed) — "
                          "these are keyword matches.")
        self._doc_detail.setText(
            f"{len(results)} match(es). {lane_line} "
            "Pick one to read it.")

    def _on_doc_selected(self, item) -> None:
        """Show one document: its passages, in order, capped so
        a huge file cannot flood the panel."""
        row = self._doc_list.row(item)
        if row < 0 or row >= len(self._doc_results):
            return
        entry = self._doc_results[row]
        source = str(entry.get("source") or "")
        title = str(entry.get("title") or "Untitled")
        self._doc_selected_source = source or None
        self._doc_forget_btn.setEnabled(bool(source))
        # A search hit already carries its passage; a list row
        # fetches the file's passages.
        if entry.get("passage"):
            self._doc_detail.setText(
                f"{title}\n{source}\n\n{entry.get('passage')}")
            return
        try:
            from voice_assistant.core.gui_facade import (
                document_memory as _dm)
            data = _dm.get_document_passages(source)
        except Exception as exc:  # noqa: BLE001 — say so, don't crash
            self._doc_detail.setText(f"Couldn't read it: {exc}")
            return
        passages = data.get("passages", []) or []
        if not passages:
            self._doc_detail.setText(
                f"{title}\n{source}\n\nNo passages stored for this file.")
            return
        text = "\n\n".join(
            str(p.get("passage") or "") for p in passages)
        if len(text) > 6000:
            text = text[:6000] + "\n\n… (truncated — the file is longer)"
        self._doc_detail.setText(f"{title}\n{source}\n\n{text}")

    def _on_doc_add_folder(self) -> None:
        """Pick a folder to index into document memory."""
        folder = QFileDialog.getExistingDirectory(
            self, "Index a folder into document memory")
        if not folder:
            return
        try:
            from voice_assistant.core.gui_facade import (
                document_memory as _dm)
            summary = _dm.index_folder(folder)
        except Exception as exc:  # noqa: BLE001 — say so, don't crash
            self._doc_detail.setText(f"Couldn't index it: {exc}")
            return
        self._doc_detail.setText(str(summary))
        self._refresh_documents()

    def _on_doc_refresh(self) -> None:
        """Re-index the remembered folders, then re-list."""
        try:
            from voice_assistant.core.gui_facade import (
                document_memory as _dm)
            summary = _dm.refresh_documents()
        except Exception as exc:  # noqa: BLE001 — say so, don't crash
            self._doc_detail.setText(f"Refresh failed: {exc}")
            return
        self._doc_detail.setText(str(summary))
        self._refresh_documents()

    def _on_doc_forget(self) -> None:
        """Forget the selected document (confirm first)."""
        source = self._doc_selected_source
        if not source:
            return
        answer = QMessageBox.question(
            self, "Forget document",
            f"Forget this document?\n{source}\n\nIts passages leave "
            "VYUK's memory. The file itself is not deleted.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            from voice_assistant.core.gui_facade import (
                document_memory as _dm)
            result = _dm.forget_documents(source)
        except Exception as exc:  # noqa: BLE001 — say so, don't crash
            self._doc_detail.setText(f"Couldn't forget it: {exc}")
            return
        if not result.get("ok"):
            self._doc_detail.setText(
                f"Couldn't forget it: {result.get('reason', 'unknown')}")
            return
        self._doc_detail.setText(
            f"Forgotten: {result.get('passages_removed', 0)} "
            "passage(s) removed.")
        self._refresh_documents()

    # ------------------------------------------------- Phase 1 sections

    def _mem_saved_section(self) -> int:
        """The persisted active tab (memory/section), clamped.

        Read straight from QSettings: memory/section is not in
        the config_spec table (only memory/view is), so the spec
        facade would raise KeyError on it."""
        try:
            value = int(self.settings.value("memory/section", 0))
        except Exception:  # noqa: BLE001
            return 0
        return max(0, min(5, value))

    def _mem_section_widget(self, index: int):
        """The section widget a tab scrolls into view."""
        anchors = [
            getattr(self, "_mem_learned_strip", None),
            getattr(self, "_mem_profile_section", None),
            getattr(self, "_mem_projects_section", None),
            getattr(self, "_mem_episodes_section", None),
            getattr(self, "_mem_graph_section", None),
            getattr(self, "_mem_review_section", None),
        ]
        if 0 <= index < len(anchors):
            return anchors[index]
        return None

    def _on_memory_section_changed(self, index: int):
        """The tabs set the ACTIVE section (Phase 1 binding 1):
        persist it, drive the count line + search placeholder,
        scroll the section into view, and enable the constellation
        only in the Graph section (binding 4). Sections are never
        hidden — a hidden ancestor would fail the hover-reveal
        leg, which needs a card's Pin visible while the Projects
        tab is active."""
        try:
            self.settings.setValue("memory/section", int(index))
        except Exception:  # noqa: BLE001
            pass
        self._mem_view = "web" if index == 4 else "list"
        if index == 4:
            self.mem_search.setPlaceholderText("Highlight in graph…")
        else:
            self.mem_search.setPlaceholderText("Search memory…")
        # Constellation: Graph section only (binding 4).
        if index == 4:
            self._ensure_memory_web()
        canvas = getattr(self, "_memory_canvas", None)
        if canvas is not None:
            try:
                canvas.set_constellation_enabled(index == 4)
            except Exception:  # noqa: BLE001
                pass
        # The tour only makes sense on the graph; leaving the
        # section stops it, as leaving Web view used to.
        if index != 4:
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
        widget = self._mem_section_widget(index)
        scroll = getattr(self, "_mem_page_scroll", None)
        if widget is not None and scroll is not None:
            try:
                scroll.ensureWidgetVisible(widget)
            except Exception:  # noqa: BLE001
                pass
        if index == 4:
            widget = getattr(self, "memory_web", None)
            if widget is not None:
                try:
                    from voice_assistant.core.gui_facade import (
                        memory_store as _store)
                    facts = _store.get_all_facts()
                except Exception:  # noqa: BLE001
                    facts = []
                if not isinstance(facts, list):
                    facts = []
                widget.refresh_if_changed(facts)
                widget.set_search_highlight(self.mem_search.text())
                self._sync_mem_web_tools(facts)
                self._on_mem_search_changed(self.mem_search.text())
                self._set_mem_count_label(len(facts), len(facts))
            return
        self._refresh_memories()

    def _on_mem_undated_toggled(self, checked: bool):
        """The learned strip's undated bucket: clicking it lists
        the facts with no date recorded (M-02)."""
        self._mem_undated_only = bool(checked)
        self._refresh_memories()

    def _make_fact_card(self, fact, when_text, search_query=""):
        """One MemoryFactCard, wired to the page's handlers."""
        return MemoryFactCard(
            fact, when_text, self._on_fact_edit, self._on_fact_pin,
            self._forget_memory_fact, search_query=search_query,
            font_scale=self._current_font_scale())

    def _on_fact_edit(self, fact):
        """Edit = the add-fact row, pre-filled (D-M3: the save_fact
        upsert; the key is not editable inline — a rename is
        forget-old + save-new in one action, see
        _add_memory_fact)."""
        if not isinstance(fact, dict):
            return
        self._mem_editing_fact = dict(fact)
        self.input_mem_k.setText(str(fact.get("key") or ""))
        self.input_mem_v.setText(str(fact.get("value") or ""))
        widgets = getattr(self, "_mem_add_row_widgets", [])
        if len(widgets) > 2 and widgets[2] is not None:
            widgets[2].setText("Save changes")
        tabs = getattr(self, "_memory_tabs", None)
        if tabs is not None:
            tabs.setCurrentIndex(0)
        self._mem_scroll_to(self.input_mem_k)
        self.input_mem_v.setFocus()

    def _on_fact_pin(self, fact):
        """Pin toggles the store flag (D-M4): the pin feeds the
        store's pinned-first ordering but does NOT exempt the
        fact from the memory cap."""
        if not isinstance(fact, dict) or fact.get("id") is None:
            return
        try:
            from voice_assistant.core.gui_facade import (
                memory_store as _store)
            _store.set_user_pinned(
                fact["id"], not bool(fact.get("user_pinned")))
        except Exception:  # noqa: BLE001
            return
        self._refresh_memories()

    def _mem_scroll_to(self, widget):
        scroll = getattr(self, "_mem_page_scroll", None)
        if scroll is not None and widget is not None:
            try:
                scroll.ensureWidgetVisible(widget)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------- section builders

    def _build_memory_profile_section(self) -> QWidget:
        """Profile — "who is the user?" (plan §3): preferences
        facts, routines, communication preferences, and the
        learning toggle (moved here from the learned card; its
        QSettings key learning/enabled and behaviour are
        unchanged)."""
        section = QWidget()
        self._mem_profile_section = section
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        title = QLabel("Profile")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)
        hint = QLabel(
            "Who you are — the preferences VYUK has saved, your "
            "routines, and how she should talk to you.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        pref_title = QLabel("Preferences")
        pref_title.setObjectName("cardTitle")
        lay.addWidget(pref_title)
        self._mem_profile_facts_wrap = QWidget()
        self._mem_profile_facts = QVBoxLayout(self._mem_profile_facts_wrap)
        self._mem_profile_facts.setContentsMargins(0, 0, 0, 0)
        self._mem_profile_facts.setSpacing(6)
        lay.addWidget(self._mem_profile_facts_wrap)
        self._mem_profile_empty = QLabel("No preferences saved yet.")
        self._mem_profile_empty.setObjectName("muted")
        lay.addWidget(self._mem_profile_empty)
        routines_title = QLabel("Routines")
        routines_title.setObjectName("cardTitle")
        lay.addWidget(routines_title)
        self._mem_profile_routines_wrap = QWidget()
        self._mem_profile_routines = QVBoxLayout(
            self._mem_profile_routines_wrap)
        self._mem_profile_routines.setContentsMargins(0, 0, 0, 0)
        self._mem_profile_routines.setSpacing(6)
        lay.addWidget(self._mem_profile_routines_wrap)
        self._mem_profile_routines_empty = QLabel(
            "No routines yet — ask VYUK: “save a routine…”")
        self._mem_profile_routines_empty.setObjectName("muted")
        self._mem_profile_routines_empty.setWordWrap(True)
        lay.addWidget(self._mem_profile_routines_empty)
        comm_title = QLabel("Communication preferences")
        comm_title.setObjectName("cardTitle")
        lay.addWidget(comm_title)
        self._mem_profile_comm_wrap = QWidget()
        self._mem_profile_comm = QVBoxLayout(self._mem_profile_comm_wrap)
        self._mem_profile_comm.setContentsMargins(0, 0, 0, 0)
        self._mem_profile_comm.setSpacing(6)
        lay.addWidget(self._mem_profile_comm_wrap)
        self._mem_profile_comm_empty = QLabel(
            "No communication preferences saved yet.")
        self._mem_profile_comm_empty.setObjectName("muted")
        lay.addWidget(self._mem_profile_comm_empty)
        self._mem_profile_private = QLabel("Private mode is on")
        self._mem_profile_private.setObjectName("muted")
        self._mem_profile_private.setVisible(False)
        lay.addWidget(self._mem_profile_private)
        # The learning toggle lives here now (plan §3): a profile
        # setting, not a list control. Key + behaviour unchanged.
        toggle_card = QFrame()
        toggle_card.setObjectName("card")
        toggle_layout = QVBoxLayout(toggle_card)
        toggle_layout.setContentsMargins(16, 12, 16, 12)
        self.chk_learn = HudSwitch(text="Learn from our conversations")
        toggle_layout.addWidget(toggle_row(self.chk_learn))
        lay.addWidget(toggle_card)
        # States-then-connections, the presence-card discipline:
        # setting the saved state must not fire the handler.
        self.chk_learn.setChecked(
            bool(qsettings_get("learning/enabled", settings=self.settings)))
        self.chk_learn.toggled.connect(self._on_learning_toggled)
        return section

    def _build_memory_projects_section(self) -> QWidget:
        """Projects & Context (plan §3): goals, plans, and the
        memories behind them."""
        section = QWidget()
        self._mem_projects_section = section
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        title = QLabel("Projects & Context")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)
        hint = QLabel(
            "Goals, plans, and the memories behind them — a fact "
            "is linked only by a real shared field, never invented.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._mem_projects_wrap = QWidget()
        self._mem_projects_layout = QVBoxLayout(self._mem_projects_wrap)
        self._mem_projects_layout.setContentsMargins(0, 0, 0, 0)
        self._mem_projects_layout.setSpacing(6)
        lay.addWidget(self._mem_projects_wrap)
        self._mem_projects_empty = QLabel("No goals or plans yet.")
        self._mem_projects_empty.setObjectName("muted")
        lay.addWidget(self._mem_projects_empty)
        return section

    def _build_memory_episodes_section(self) -> QWidget:
        """Episodes (plan §3): the day journal as a timeline —
        vertical spine + date dots, newest day first."""
        section = QWidget()
        self._mem_episodes_section = section
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        title = QLabel("Episodes")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)
        hint = QLabel("What happened — the day journal, newest first.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._mem_episodes_timeline = QWidget()
        self._mem_episodes_timeline.setObjectName("episodesTimeline")
        tl = QHBoxLayout(self._mem_episodes_timeline)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(8)
        self._mem_episodes_spine = QFrame()
        self._mem_episodes_spine.setFrameShape(QFrame.Shape.VLine)
        self._mem_episodes_spine.setStyleSheet(
            f"color: {LINE_SUBTLE};")
        self._mem_episodes_spine.setFixedWidth(1)
        tl.addWidget(self._mem_episodes_spine)
        content = QWidget()
        self._mem_episodes_content = QVBoxLayout(content)
        self._mem_episodes_content.setContentsMargins(0, 0, 0, 0)
        self._mem_episodes_content.setSpacing(10)
        tl.addWidget(content, stretch=1)
        lay.addWidget(self._mem_episodes_timeline)
        self._mem_episodes_empty = QLabel(
            "Nothing recorded yet — days you ask VYUK to remember "
            "show up here.")
        self._mem_episodes_empty.setObjectName("muted")
        self._mem_episodes_empty.setWordWrap(True)
        lay.addWidget(self._mem_episodes_empty)
        self._mem_episodes_private = QLabel("Private mode is on")
        self._mem_episodes_private.setObjectName("muted")
        self._mem_episodes_private.setVisible(False)
        lay.addWidget(self._mem_episodes_private)
        return section

    def _build_memory_graph_section(self) -> QWidget:
        """Knowledge Graph (plan §3): the existing web, as a
        section — not a toggle. The web container drops the card
        fill so the backdrop grid shows through; the drawer keeps
        its own fill."""
        section = QWidget()
        self._mem_graph_section = section
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        title = QLabel("Knowledge Graph")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)
        hint = QLabel(
            "The web — facts woven around you. Pick a node to "
            "inspect it.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        # The Filter menu's Show group lives here now (the
        # "Filter: All" button is gone): which categories the web
        # shows.
        show_row = QHBoxLayout()
        show_row.setSpacing(6)
        show_lbl = QLabel("Shown in graph")
        show_lbl.setObjectName("muted")
        show_row.addWidget(show_lbl)
        self.btn_mem_show = QPushButton("Show")
        self.btn_mem_show.setObjectName("chipBtn")
        self.btn_mem_show.setToolTip(
            "Choose which categories the web shows")
        self.btn_mem_show.clicked.connect(self._show_mem_filter_menu)
        show_row.addWidget(self.btn_mem_show)
        show_row.addStretch()
        lay.addLayout(show_row)
        # v5.18 (Eyes E24–E27): the web's timeline scrubber.
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
        self.mem_timeline.setToolTip(
            "Scrub back through when memories were saved")
        self.mem_timeline.valueChanged.connect(self._on_mem_timeline_changed)
        tl_row.addWidget(self.mem_timeline, stretch=1)
        self.mem_timeline_label = QLabel("All time")
        self.mem_timeline_label.setObjectName("muted")
        tl_row.addWidget(self.mem_timeline_label)
        self._mem_time_span: tuple[float, float] | None = None
        lay.addWidget(self.mem_timeline_row)
        # The web itself: graph on the left (created lazily on
        # the first Graph visit), fact detail panel on the right.
        self.memory_web = None
        self.mem_web_container = QFrame()
        self.mem_web_container.setObjectName("memWebContainer")
        self.mem_web_container.setStyleSheet(
            "background: transparent; border: none;")
        self.mem_web_layout = QHBoxLayout(self.mem_web_container)
        self.mem_web_layout.setContentsMargins(8, 8, 8, 8)
        self.mem_web_layout.setSpacing(8)
        self.mem_web_detail = self._build_web_detail_panel()
        self.mem_web_layout.addWidget(self.mem_web_detail)
        lay.addWidget(self.mem_web_container, stretch=1)
        self._mem_graph_private = QLabel("Private mode is on")
        self._mem_graph_private.setObjectName("muted")
        self._mem_graph_private.setVisible(False)
        lay.addWidget(self._mem_graph_private)
        return section

    def _build_memory_review_section(self) -> QWidget:
        """Review Inbox (plan §3, HudPanel section container):
        what needs your eyes — from the three real sources only
        (see core/memory/review_inbox.py)."""
        panel = HudPanel()
        panel.setObjectName("memoryReviewPanel")
        self._mem_review_section = panel
        content = panel.content_layout()
        header = QHBoxLayout()
        title = QLabel("Review Inbox")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        self._mem_inbox_count = QLabel("")
        self._mem_inbox_count.setObjectName("muted")
        header.addWidget(self._mem_inbox_count)
        content.addLayout(header)
        hint = QLabel(
            "Things that need your eyes — learned facts you "
            "haven't seen, inferred records, and fields stored "
            "twice.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        content.addWidget(hint)
        self._mem_inbox_rows_wrap = QWidget()
        self._mem_inbox_rows = QVBoxLayout(self._mem_inbox_rows_wrap)
        self._mem_inbox_rows.setContentsMargins(0, 0, 0, 0)
        self._mem_inbox_rows.setSpacing(8)
        content.addWidget(self._mem_inbox_rows_wrap)
        self._mem_inbox_empty = QLabel("Nothing to review.")
        self._mem_inbox_empty.setObjectName("muted")
        content.addWidget(self._mem_inbox_empty)
        self._mem_inbox_private = QLabel("Private mode is on")
        self._mem_inbox_private.setObjectName("muted")
        self._mem_inbox_private.setVisible(False)
        content.addWidget(self._mem_inbox_private)
        return panel

    # ------------------------------------------- section refreshes

    @staticmethod
    def _mem_clear_layout(layout) -> None:
        """Empty a layout now (hide + deleteLater, the learned
        card's pattern — an orphaned widget would keep painting
        at its old geometry over its replacement)."""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) FIRST (not just hide + deleteLater):
                # until the deferred delete lands, an orphaned widget
                # keeps answering findChildren (the orphan trap from
                # legs 51/54). Deleting the widget drops its children
                # (and any nested layouts) with it.
                widget.setParent(None)
                widget.deleteLater()

    def _refresh_memory_sections(self, all_facts, view) -> None:
        """Fan-out from _refresh_memories: every section below
        the fact list reads from the same facts (one shared
        read — never a second read per cell)."""
        private = self._mem_private_on()
        all_facts = list(all_facts or [])
        # Backdrop geometry: keep it behind the whole page.
        backdrop = getattr(self, "_mem_backdrop", None)
        page = getattr(self, "_mem_page", None)
        if backdrop is not None and page is not None:
            try:
                if page.width() > 0 and page.height() > 0:
                    backdrop.resize(page.size())
                backdrop.lower()
            except Exception:  # noqa: BLE001
                pass
        canvas = getattr(self, "_memory_canvas", None)
        if canvas is not None:
            try:
                canvas.set_private_mode(private)
            except Exception:  # noqa: BLE001
                pass
        self._refresh_memory_stat_strip(all_facts, private)
        self._refresh_memory_learned_strip(all_facts, private)
        self._refresh_memory_chips(all_facts, view, private)
        self._refresh_memory_profile(all_facts, private)
        self._refresh_memory_projects(all_facts, private)
        self._refresh_memory_episodes(private)
        self._refresh_memory_graph(private)
        self._refresh_memory_inbox(private)

    def _refresh_memory_stat_strip(self, all_facts, private) -> None:
        """Four counts from their real reads (plan §2). Facts +
        Learned carry "+N this week" trend deltas from real
        updated_at, dated facts only; Documents and Episodes
        carry NO delta line (M-23)."""
        numbers = getattr(self, "_mem_stat_numbers", [])
        deltas = getattr(self, "_mem_stat_deltas", [])
        if len(numbers) < 4:
            return
        if private:
            for lbl in numbers:
                lbl.setText("—")
            for lbl in deltas:
                lbl.setText("")
            self._mem_stat_private.setVisible(True)
            return
        self._mem_stat_private.setVisible(False)
        today = datetime.date.today()

        def _week_delta(facts):
            n = 0
            for fact in facts or []:
                epoch = _fact_epoch(fact)
                if epoch is None:
                    continue
                try:
                    day = datetime.datetime.fromtimestamp(epoch).date()
                except (OSError, OverflowError, ValueError):
                    continue
                if 0 <= (today - day).days <= 7:
                    n += 1
            return n

        # Hunt fix (c) item 6: the "Learned" stat counts what
        # the card shows — all user facts, not the "learned"
        # category alone — so it cannot drift from the card.
        learned_facts = []
        try:
            module = self._learning_module()
            if module is not None:
                getter = getattr(module, "get_all_user_facts",
                                 module.get_learned_facts)
                learned_facts = [
                    f for f in (getter() or [])
                    if isinstance(f, dict)]
        except Exception:  # noqa: BLE001
            learned_facts = []
        doc_n = self._mem_document_count()
        ep_n = self._mem_episode_count()
        counts = [len(all_facts), len(learned_facts), doc_n, ep_n]
        week = [_week_delta(all_facts), _week_delta(learned_facts),
                None, None]
        for i, lbl in enumerate(numbers):
            lbl.setText("—" if counts[i] is None else str(counts[i]))
        for i, lbl in enumerate(deltas):
            lbl.setText(
                f"+{week[i]} this week" if week[i] is not None else "")

    def _mem_document_count(self):
        """Stat-strip Documents count through the D1 boot guard:
        no store file → None (no fake zero)."""
        try:
            from voice_assistant.core.gui_facade import (
                document_memory as _dm)
            if not _dm.document_store_exists():
                return None
            data = _dm.list_documents(limit=50)
            return int(data.get("total_files", 0) or 0)
        except Exception:  # noqa: BLE001
            return None

    def _mem_episode_count(self) -> int:
        """Stat-strip Episodes count: day-journal entries."""
        try:
            from voice_assistant.core.gui_facade import (
                memory_store as _store)
            rows = _store._execute(
                "SELECT COUNT(*) AS n FROM episodes", fetch_one=True)
            if isinstance(rows, dict):
                return int(rows.get("n") or 0)
        except Exception:  # noqa: BLE001
            pass
        return 0

    def _refresh_memory_learned_strip(self, all_facts, private) -> None:
        """"Added or updated today / this week" (M-02) + the
        explicit "no date recorded" bucket."""
        today_lbl = getattr(self, "_mem_learned_today", None)
        if today_lbl is None:
            return
        if private:
            for widget in (self._mem_learned_today,
                           self._mem_learned_week,
                           self._mem_learned_undated):
                widget.setVisible(False)
            self._mem_learned_strip_private.setVisible(True)
            return
        self._mem_learned_strip_private.setVisible(False)
        for widget in (self._mem_learned_today,
                       self._mem_learned_week,
                       self._mem_learned_undated):
            widget.setVisible(True)
        today = datetime.date.today()
        n_today = n_week = n_undated = 0
        for fact in all_facts or []:
            epoch = _fact_epoch(fact)
            if epoch is None:
                n_undated += 1
                continue
            try:
                day = datetime.datetime.fromtimestamp(epoch).date()
            except (OSError, OverflowError, ValueError):
                n_undated += 1
                continue
            if day == today:
                n_today += 1
            if 0 <= (today - day).days <= 7:
                n_week += 1
        self._mem_learned_today.setText(
            f"Added or updated today: {n_today}")
        self._mem_learned_week.setText(
            f"Added or updated this week: {n_week}")
        # Hunt fix (c) item 12: the "No date recorded" chip
        # hides at 0 — a bucket of nothing is not a filter. (The
        # today / this week chips keep their ": 0" — they are
        # counts, not a "nothing" statement.)
        self._mem_learned_undated.setText(f"No date recorded: {n_undated}")
        self._mem_learned_undated.setVisible(n_undated > 0)
        self._mem_learned_undated.setChecked(
            bool(getattr(self, "_mem_undated_only", False)))

    def _refresh_memory_chips(self, all_facts, view, private) -> None:
        """Filter chips with counts (M-07): per-category totals
        over the WHOLE store, not the search-filtered set."""
        flow = getattr(self, "_mem_chip_flow", None)
        if flow is None:
            return
        while flow.count():
            item = flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) FIRST (the orphan trap from
                # legs 51/54): hide + deleteLater alone leaves the
                # widget answering findChildren until the event
                # loop destroys it.
                widget.setParent(None)
                widget.deleteLater()
        self._mem_chip_buttons = []
        if private:
            self._mem_chip_private.setVisible(True)
            return
        self._mem_chip_private.setVisible(False)
        counts: dict[str, int] = {}
        for fact in all_facts or []:
            cat = str((fact or {}).get("category") or "general")
            counts[cat] = counts.get(cat, 0) + 1
        cats = (view or {}).get("categories") or sorted(counts)
        combo = getattr(self, "mem_cat_filter", None)
        chosen = (combo.currentText() if combo is not None else "") \
            or "All categories"
        total = len(all_facts or [])
        all_btn = QPushButton(f"All ({total})")
        all_btn.setObjectName("chipBtn")
        all_btn.setCheckable(True)
        all_btn.setChecked(chosen == "All categories")
        all_btn.clicked.connect(
            lambda _checked=False: self.mem_cat_filter.setCurrentText(
                "All categories"))
        flow.addWidget(all_btn)
        self._mem_chip_buttons.append(all_btn)
        for cat in cats:
            btn = QPushButton(
                f"{category_label(cat)} ({counts.get(cat, 0)})")
            btn.setObjectName("chipBtn")
            btn.setCheckable(True)
            btn.setChecked(chosen == cat)
            btn.clicked.connect(
                lambda _checked=False, c=cat:
                self.mem_cat_filter.setCurrentText(c))
            flow.addWidget(btn)
            self._mem_chip_buttons.append(btn)

    def _refresh_memory_profile(self, all_facts, private) -> None:
        """Preferences facts + routines + communication prefs
        (plan §3). In private mode the fact surfaces blank; the
        routines and the learning toggle stay (they are not
        stored-fact content)."""
        wrap = getattr(self, "_mem_profile_facts", None)
        if wrap is None:
            return
        self._mem_clear_layout(wrap)
        self._mem_clear_layout(getattr(self, "_mem_profile_comm", None))
        self._mem_clear_layout(getattr(self, "_mem_profile_routines", None))
        prefs = [f for f in (all_facts or [])
                 if str(f.get("category") or "").strip().lower()
                 == "preferences"]
        comm = [f for f in (all_facts or [])
                if str(f.get("category") or "").strip().lower()
                == "communication"
                or "communicat" in str(f.get("key") or "").lower()]
        if private:
            self._mem_profile_facts_wrap.setVisible(False)
            self._mem_profile_empty.setVisible(False)
            self._mem_profile_comm_wrap.setVisible(False)
            self._mem_profile_comm_empty.setVisible(False)
            self._mem_profile_private.setVisible(True)
        else:
            self._mem_profile_private.setVisible(False)
            self._mem_profile_facts_wrap.setVisible(True)
            self._mem_profile_comm_wrap.setVisible(True)
            query = self.mem_search.text().strip()
            for fact in prefs:
                wrap.addWidget(self._make_fact_card(
                    fact, _mem_when_text(_fact_epoch(fact)), query))
            self._mem_profile_empty.setVisible(not prefs)
            for fact in comm:
                self._mem_profile_comm.addWidget(self._make_fact_card(
                    fact, _mem_when_text(_fact_epoch(fact)), query))
            self._mem_profile_comm_empty.setVisible(not comm)
        routines = []
        try:
            from voice_assistant.core.gui_facade import planner as _planner
            getter = getattr(_planner, "get_routines", None)
            if getter is not None:
                routines = [r for r in (getter() or [])
                            if isinstance(r, dict)]
        except Exception:  # noqa: BLE001
            routines = []
        for routine in routines:
            name = str(routine.get("name", "") or "Untitled").strip()
            schedule = routine.get("schedule", "")
            if isinstance(schedule, dict):
                schedule = " ".join(
                    f"{k} {v}" for k, v in schedule.items())
            text = name if not schedule else f"{name} — {schedule}"
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            self._mem_profile_routines.addWidget(lbl)
        self._mem_profile_routines_empty.setVisible(not routines)

    def _refresh_memory_projects(self, all_facts, private) -> None:
        """Goals + plans + supporting memories (plan §3). A goal
        shows WITHOUT memory links — and says so — when no fact
        shares its name."""
        layout = getattr(self, "_mem_projects_layout", None)
        if layout is None:
            return
        self._mem_clear_layout(layout)
        goals = self._mem_goal_entries(all_facts)
        plans = self._mem_plan_entries()
        for goal in goals:
            name = goal["name"]
            unit = f" {goal['unit']}" if goal.get("unit") else ""
            head = QLabel(
                f"Goal — {name}: {goal['progress']:g}{unit} of "
                f"{goal['target']:g}{unit}")
            head.setWordWrap(True)
            layout.addWidget(head)
            self._mem_projects_add_links(layout, name, all_facts, private)
        for plan in plans:
            head = QLabel(
                f"Plan — {plan['goal']}: {plan['done']}/{plan['total']} "
                "steps done")
            head.setWordWrap(True)
            layout.addWidget(head)
            self._mem_projects_add_links(
                layout, plan["goal"], all_facts, private)
        self._mem_projects_empty.setVisible(not goals and not plans)

    def _mem_projects_add_links(self, layout, name, all_facts, private):
        """The supporting memories for one goal/plan: facts whose
        category matches the project/goal name (exact store
        field, lower-cased — never an invented link)."""
        if private:
            lbl = QLabel("Private mode is on")
            lbl.setObjectName("muted")
            layout.addWidget(lbl)
            return
        wanted = str(name or "").strip().lower()
        linked = [f for f in (all_facts or [])
                  if str(f.get("category") or "").strip().lower() == wanted]
        if not linked:
            lbl = QLabel("No linked memories.")
            lbl.setObjectName("muted")
            layout.addWidget(lbl)
            return
        for fact in linked:
            lbl = QLabel(
                f"{fact.get('key', '')} — {fact.get('value', '')}")
            lbl.setObjectName("muted")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

    @staticmethod
    def _mem_goal_entries(all_facts):
        """Goals from the goals facts (category "goals", JSON
        values) — the same parse core/skills_notes._load_goals
        applies, over the page's shared read."""
        entries = []
        for fact in all_facts or []:
            if str(fact.get("category") or "").strip().lower() != "goals":
                continue
            try:
                stored = json.loads(fact.get("value") or "")
                if not isinstance(stored, dict):
                    continue
                name = str(stored["name"])
                if not name.strip():
                    continue
                target = float(stored["target"])
                if target <= 0:
                    continue
                progress = stored.get("progress")
                progress = float(progress) \
                    if progress is not None else 0.0
                if progress < 0:
                    progress = 0.0
                entries.append({
                    "name": name, "target": target,
                    "unit": str(stored.get("unit") or ""),
                    "progress": progress})
            except Exception:  # noqa: BLE001
                continue
        entries.sort(key=lambda e: e["name"].lower())
        return entries

    @staticmethod
    def _mem_plan_entries():
        """Plans via list_plans / plan_progress (the same read
        the Scheduled tab's Plans section uses)."""
        try:
            from voice_assistant.core.gui_facade import (
                planner_plans as _planner_plans)
            list_plans = _planner_plans.list_plans
            plan_progress = _planner_plans.plan_progress
        except Exception:  # noqa: BLE001
            return []
        try:
            text = list_plans()
        except Exception:  # noqa: BLE001
            return []
        ids = re.findall(r"(?m)^\s+(plan-\d+):", str(text or ""))
        out = []
        for plan_id in ids:
            try:
                prog = plan_progress(plan_id)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(prog, dict) or not prog.get("found"):
                continue
            out.append({
                "id": plan_id,
                "goal": str(prog.get("goal") or plan_id),
                "done": int(prog.get("done") or 0),
                "total": int(prog.get("total") or 0)})
        return out

    def _refresh_memory_episodes(self, private) -> None:
        """The day journal as a timeline (plan §3, round 5):
        newest day first, each day expandable to its entries,
        rendered VERBATIM — no paraphrase, no phrasing added
        (M-10). 50 entries per day, 500 chars (1200 for session
        summaries). In private mode the spine and date dots hide
        with the rows (M-26)."""
        timeline = getattr(self, "_mem_episodes_timeline", None)
        if timeline is None:
            return
        content = getattr(self, "_mem_episodes_content", None)
        self._mem_clear_layout(content)
        if private:
            timeline.setVisible(False)
            self._mem_episodes_empty.setVisible(False)
            self._mem_episodes_private.setVisible(True)
            return
        self._mem_episodes_private.setVisible(False)
        days = self._mem_episode_days()
        if not days:
            timeline.setVisible(False)
            self._mem_episodes_empty.setVisible(True)
            return
        timeline.setVisible(True)
        self._mem_episodes_empty.setVisible(False)
        for day_row in days:
            date_str = str(day_row.get("date") or "")
            count = int(day_row.get("count") or 0)
            try:
                parsed = datetime.date.fromisoformat(date_str)
                date_lbl = parsed.strftime("%A %d %B")
            except ValueError:
                date_lbl = date_str
            group = QWidget()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(4)
            header = QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(8)
            dot = QWidget()
            dot.setObjectName(f"episodeDot-{date_str}")
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(
                f"background: {ACCENT}; border-radius: 5px;")
            header.addWidget(dot)
            head_lbl = QLabel(f"{date_lbl} · {count} entries")
            head_lbl.setWordWrap(True)
            header.addWidget(head_lbl, stretch=1)
            entries_wrap = QWidget()
            entries_layout = QVBoxLayout(entries_wrap)
            entries_layout.setContentsMargins(0, 0, 0, 0)
            entries_layout.setSpacing(4)
            toggle = QPushButton("▾")
            toggle.setObjectName("chipBtn")
            toggle.setCheckable(True)
            toggle.setChecked(True)
            toggle.setToolTip("Show or hide this day's entries")
            toggle.clicked.connect(
                lambda _checked=False, w=entries_wrap, b=toggle:
                (w.setVisible(b.isChecked()),
                 b.setText("▾" if b.isChecked() else "▸")))
            header.addWidget(toggle)
            group_layout.addLayout(header)
            for entry in self._mem_episode_entries(date_str):
                kind = str(entry.get("kind") or "").strip().lower()
                text = str(entry.get("text") or "")
                cap = 1200 if kind == "session" else 500
                if len(text) > cap:
                    text = text[:cap] + " …"
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                kind_lbl = QLabel(kind.upper() if kind else "NOTE")
                kind_lbl.setObjectName("muted")
                row.addWidget(kind_lbl)
                text_lbl = QLabel(text)
                text_lbl.setWordWrap(True)
                text_lbl.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                row.addWidget(text_lbl, stretch=1)
                entries_layout.addLayout(row)
            group_layout.addWidget(entries_wrap)
            content.addWidget(group)

    def _mem_episode_days(self):
        """Recent journal days, newest first: [{"date", "count"}].

        Read through the facade store (the page's one store):
        the episodes module binds memory_store from the
        voice_assistant alias, which the harness stubs — and
        importing it through the alias fails outright, because
        core/memory/__init__ imports MemoryStore from that
        stubbed module. The SQL below is episodes.days()'s own
        read, so the rows are the same rows, verbatim."""
        try:
            from voice_assistant.core.gui_facade import (
                memory_store as _store)
            rows = _store._execute(
                "SELECT date, COUNT(*) AS count FROM episodes "
                "GROUP BY date ORDER BY date DESC LIMIT 30",
                fetch=True)
            return list(rows) if isinstance(rows, list) else []
        except Exception:  # noqa: BLE001
            return []

    def _mem_episode_entries(self, date_str):
        """One day's entries, oldest first (episodes.day()'s
        read, same caps applied at render)."""
        try:
            from voice_assistant.core.gui_facade import (
                memory_store as _store)
            rows = _store._execute(
                "SELECT id, date, ts, kind, text FROM episodes "
                "WHERE date = ? ORDER BY id ASC LIMIT 50",
                (date_str,), fetch=True)
            return list(rows) if isinstance(rows, list) else []
        except Exception:  # noqa: BLE001
            return []

    def _refresh_memory_graph(self, private) -> None:
        """Private mode blanks the web (M-01): the container,
        the timeline and the detail drawer hide together."""
        container = getattr(self, "mem_web_container", None)
        row = getattr(self, "mem_timeline_row", None)
        if container is None:
            return
        if private:
            container.setVisible(False)
            if row is not None:
                row.setVisible(False)
            self._mem_graph_private.setVisible(True)
        else:
            container.setVisible(True)
            if row is not None:
                row.setVisible(True)
            self._mem_graph_private.setVisible(False)

    def _refresh_memory_inbox(self, private) -> None:
        """Review Inbox (plan §3): the three real sources only,
        via core/memory/review_inbox.py. In private mode the rows
        hide but the COUNT still shows (M-01 refined)."""
        wrap = getattr(self, "_mem_inbox_rows", None)
        if wrap is None:
            return
        self._mem_clear_layout(wrap)
        rows = []
        try:
            from voice_assistant.core.gui_facade import (
                review_inbox as _review_inbox)
            collect_inbox_rows = _review_inbox.collect_inbox_rows
            from voice_assistant.core.gui_facade import (
                memory_store as _store)
            seen = self._learned_seen_ids()
            try:
                reviewed = qsettings_get(
                    "learning/reviewed_ids", settings=self.settings) or []
            except Exception:  # noqa: BLE001
                reviewed = []
            if isinstance(reviewed, (str, int)):
                reviewed = [reviewed]
            rows = collect_inbox_rows(
                _store, seen_ids=seen, reviewed_ids=reviewed)
        except Exception:  # noqa: BLE001
            rows = []
        if not isinstance(rows, list):
            rows = []
        self._mem_inbox_count.setText(
            f"{len(rows)} to review" if rows else "Nothing to review")
        if private:
            self._mem_inbox_rows_wrap.setVisible(False)
            self._mem_inbox_empty.setVisible(False)
            self._mem_inbox_private.setVisible(True)
            return
        self._mem_inbox_private.setVisible(False)
        self._mem_inbox_rows_wrap.setVisible(True)
        self._mem_inbox_empty.setVisible(not rows)
        for row in rows:
            wrap.addWidget(self._mem_inbox_row_widget(row))

    def _mem_inbox_row_widget(self, row):
        """One Review Inbox row: its title, its why line (the
        exact lowercase words from the real source), both values
        of a conflict with their ages and sources, and its
        actions."""
        widget = QWidget()
        widget.setObjectName("reviewInboxRow")
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        title = QLabel(str(row.get("title") or ""))
        title.setWordWrap(True)
        lay.addWidget(title)
        why = QLabel(str(row.get("why") or ""))
        why.setObjectName("reviewInboxWhy")
        why.setWordWrap(True)
        lay.addWidget(why)
        for fact in (row.get("facts") or []):
            if not isinstance(fact, dict):
                continue
            fact_lbl = QLabel(
                f"{fact.get('key', '')} — {fact.get('value', '')}")
            fact_lbl.setWordWrap(True)
            lay.addWidget(fact_lbl)
            age = str(fact.get("_age") or "")
            age_text = f"last updated {age}" if age else "no date recorded"
            meta = QLabel(
                f"{age_text} · source {fact.get('source') or 'unknown'}")
            meta.setObjectName("muted")
            lay.addWidget(meta)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        if row.get("kind") == "conflict":
            for label, choice in (
                    ("Keep the newer", "newer"),
                    ("Keep the hand-saved one", "hand_saved"),
                    ("Keep both", "both")):
                btn = QPushButton(label)
                btn.setObjectName("chipBtn")
                btn.clicked.connect(
                    lambda _checked=False, r=row, c=choice:
                    self._on_inbox_conflict(r, c))
                actions.addWidget(btn)
        else:
            approve = QPushButton("Approve")
            approve.setObjectName("chipBtn")
            approve.clicked.connect(
                lambda _checked=False, r=row: self._on_inbox_approve(r))
            actions.addWidget(approve)
            reject = QPushButton("Reject")
            reject.setObjectName("chipBtn")
            reject.clicked.connect(
                lambda _checked=False, r=row: self._on_inbox_reject(r))
            actions.addWidget(reject)
            revise = QPushButton("Revise")
            revise.setObjectName("chipBtn")
            revise.clicked.connect(
                lambda _checked=False, r=row: self._on_inbox_revise(r))
            actions.addWidget(revise)
        actions.addStretch()
        lay.addLayout(actions)
        return widget

    def _on_inbox_approve(self, row):
        """Approve = mark reviewed ONLY (M-04): the record keeps
        its kind and never returns to the inbox."""
        try:
            from voice_assistant.core.gui_facade import (
                review_inbox as _review_inbox)
            mark_reviewed = _review_inbox.mark_reviewed
            seen, reviewed = mark_reviewed(
                row, seen_ids=self._learned_seen_ids(),
                reviewed_ids=self._mem_reviewed_ids())
            self._store_learned_seen_ids(seen)
            qsettings_set("learning/reviewed_ids", reviewed,
                          settings=self.settings)
        except Exception:  # noqa: BLE001
            pass
        self._refresh_memories()

    def _mem_reviewed_ids(self):
        try:
            reviewed = qsettings_get(
                "learning/reviewed_ids", settings=self.settings) or []
        except Exception:  # noqa: BLE001
            return set()
        if isinstance(reviewed, (str, int)):
            reviewed = [reviewed]
        return {str(v) for v in reviewed}

    def _on_inbox_reject(self, row):
        """Reject = forget (the existing delete path + undo bar)."""
        ids = [f.get("id") for f in (row.get("facts") or [])
               if isinstance(f, dict) and f.get("id") is not None]
        if ids:
            self._forget_memory_facts(ids)

    def _on_inbox_revise(self, row):
        """Revise = edit (the save_fact upsert, D-M3)."""
        facts = [f for f in (row.get("facts") or [])
                 if isinstance(f, dict)]
        if facts:
            self._on_fact_edit(facts[0])

    def _on_inbox_conflict(self, row, choice):
        """A conflict row resolves only by an explicit choice —
        never a single silent resolve (M-03)."""
        try:
            from voice_assistant.core.gui_facade import (
                review_inbox as _review_inbox)
            mark_reviewed = _review_inbox.mark_reviewed
            resolve_conflict = _review_inbox.resolve_conflict
            from voice_assistant.core.gui_facade import (
                memory_store as _store)
            forgotten = resolve_conflict(_store, row, choice)
            if forgotten:
                self._show_mem_undo(forgotten)
            seen, reviewed = mark_reviewed(
                row, seen_ids=self._learned_seen_ids(),
                reviewed_ids=self._mem_reviewed_ids())
            self._store_learned_seen_ids(seen)
            qsettings_set("learning/reviewed_ids", reviewed,
                          settings=self.settings)
        except Exception:  # noqa: BLE001
            pass
        self._refresh_memories()

    def _build_skills_page(self) -> QWidget:
        page, layout = self._page_shell("Skills", "Everything VYUK can do — her built-in skills, plus your own plugins.", kicker=True)
        # UI Phase 2: the page is a left column (view row,
        # catalogue / galaxy, plugins, rescan) beside the detail
        # drawer. The left column carries the page shell's
        # spacing (10) so the row gaps match the old layout.
        left_widget = QWidget()
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
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
        left.addLayout(sview_row)
        self.skills_galaxy = None
        self.skills_galaxy_card_widget = None
        # Galaxy looks round (2026-11-25): there is no permanent
        # Galaxy detail card any more — a picked star opens the
        # shared catalogue drawer (skills_detail_drawer), so the
        # canvas takes the full container width.
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
            self.skills_catalog = build_skills_catalog(
                _decls, _TOOL_FRIENDLY_NAMES, settings=self.settings)
            # UI Phase 2, D-P2-17: the count line re-reads from
            # the registry (never cached) whenever the list
            # changes; a picked row opens the detail drawer.
            self.skills_catalog.counts_changed.connect(
                self._update_skills_count_line)
            self.skills_catalog.skill_chosen.connect(
                self._on_catalogue_skill_chosen)
            self.skills_catalog.set_private_mode(self._mem_private_on())
            cat_layout.addWidget(self.skills_catalog)
            left.addWidget(cat_card, stretch=3)
            # Batch 5, item 12: the page builds before the first
            # appearance pass — scale its painted headers now.
            try:
                self._apply_font_scale_to_painted_pages(
                    self._current_font_scale())
            except Exception:  # noqa: BLE001 — appearance never breaks boot
                pass
            # Look-fix 2 (2026-11-24): the same shape for the
            # theme — the catalogue delegate paints from the
            # resolved theme, and a page built before the first
            # appearance pass would otherwise paint house-dark
            # rows on Light until the next theme change.
            try:
                _resolved = getattr(self, "_resolved_theme", None)
                if isinstance(_resolved, dict):
                    self.skills_catalog.set_theme(_resolved)
            except Exception:  # noqa: BLE001 — appearance never breaks boot
                pass
        except Exception as e:  # noqa: BLE001 — the page must still build
            print(f"[VYUK] Skills catalogue unavailable: {e}")
            self.skills_catalog = None
            self.skills_cat_card = None
        # UI Phase 2, D-P2-17: the count line re-reads from the
        # registry (never cached) — "772 capabilities — 11 groups",
        # or "M of 772 shown" while filtering. Empty (rather than
        # wrong) if the registry cannot be read.
        self._update_skills_count_line()
        # The galaxy container: the star map, full width — hidden
        # until the Galaxy view is chosen (built lazily in
        # _ensure_skills_galaxy). Galaxy looks round (2026-11-25):
        # the picked skill's detail is the shared drawer beside
        # the page, not a permanent card inside this container.
        self.skills_galaxy_container = QFrame()
        self.skills_galaxy_container.setObjectName("card")
        sg_layout = QHBoxLayout(self.skills_galaxy_container)
        sg_layout.setContentsMargins(8, 8, 8, 8)
        sg_layout.setSpacing(8)
        self.skills_galaxy_container.setVisible(False)
        left.addWidget(self.skills_galaxy_container, stretch=3)
        saved_sview = qsettings_get("skills/view", settings=self.settings)
        self._set_skills_view("galaxy" if saved_sview == "galaxy" else "list")
        plugins_label = QLabel("Your plugins")
        plugins_label.setObjectName("cardTitle")
        left.addWidget(plugins_label)
        card = QFrame()
        card.setObjectName("card")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        self.plugin_list = QListWidget()
        # Look-fix 2 (2026-11-24): plugin rows are one long
        # "name — description" line; the default delegate let them
        # run past the edge and the list grew a horizontal
        # scrollbar. Elide at the row edge instead (the full text
        # stays on the item + tooltip), and never show the
        # horizontal bar — the list wraps nothing, it elides.
        self.plugin_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.plugin_list.setTextElideMode(
            Qt.TextElideMode.ElideRight)
        c_layout.addWidget(self.plugin_list)
        # UI Phase 2: private mode blanks the plugin names (the
        # same rule as the catalogue's Pinned section) — one line
        # says so instead of the list.
        self.skills_plugin_private_note = QLabel(
            "Plugin names are hidden while private mode is on")
        self.skills_plugin_private_note.setObjectName("muted")
        self.skills_plugin_private_note.setWordWrap(True)
        self.skills_plugin_private_note.setVisible(False)
        c_layout.addWidget(self.skills_plugin_private_note)
        left.addWidget(card, stretch=1)
        btn_rescan = QPushButton("Rescan plugins folder")
        btn_rescan.clicked.connect(self._reload_plugins)
        left.addWidget(btn_rescan)
        # UI Phase 2: the detail drawer beside the left column —
        # the shared detail content for a picked catalogue row.
        # Hidden until a row is picked; closing it returns to the
        # list with the filter and scroll position intact.
        drawer = self._build_skills_detail_drawer()
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(8)
        body_row.addWidget(left_widget, stretch=1)
        body_row.addWidget(drawer)
        layout.addLayout(body_row)
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
        # UI Phase 2: the filter is SHARED between the views —
        # switching carries the text across (the catalogue filters
        # on it, the galaxy highlights on it), so the view change
        # never resets what the user was looking for.
        try:
            self._sync_skills_filter(mode)
        except Exception:  # noqa: BLE001
            pass  # the view switch still lands.
        # Galaxy looks round (2026-11-25): the drawer is shared
        # between the views now — switching to the galaxy no
        # longer hides it (it shows the same skill either way).
        if galaxy:
            self._ensure_skills_galaxy()
            self._layout_skills_galaxy()

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
                SkillsGalaxyWidget,
            )

            _decls = list(_tool_registry.get_declarations())
            galaxy = SkillsGalaxyWidget()
            galaxy.set_data(_decls, _TOOL_FRIENDLY_NAMES)
            galaxy.skill_chosen.connect(self._on_galaxy_skill_chosen)
            galaxy.selection_cleared.connect(self._on_galaxy_selection_cleared)
            # Galaxy looks round (2026-11-25): no permanent
            # detail card — a picked star opens the shared
            # catalogue drawer, so the canvas takes the full
            # container width. The card class stays for its unit
            # tests; the page no longer uses it.
            layout = self.skills_galaxy_container.layout()
            layout.addWidget(galaxy, stretch=1)
            self.skills_galaxy = galaxy
            # Galaxy looks round: a resize watcher on the
            # container re-lays it out whenever the window width
            # changes (the canvas must keep filling it).
            watcher = _ResizeWatcher(self._layout_skills_galaxy,
                                     parent=self.skills_galaxy_container)
            self.skills_galaxy_container.installEventFilter(watcher)
            self._skills_galaxy_watcher = watcher
            self._layout_skills_galaxy()
            if self.skills_galaxy_search.text():
                galaxy.set_search_highlight(self.skills_galaxy_search.text())
            # Batch 5, item 12: a lazily built page missed the
            # last appearance pass — scale its painted labels now.
            self._apply_font_scale_to_painted_pages(
                self._current_font_scale())
        except Exception as e:  # noqa: BLE001 — list view still works
            print(f"[VYUK] Skills galaxy unavailable: {e}")
            self.skills_galaxy = None
        return self.skills_galaxy

    def _on_galaxy_skill_chosen(self, name: str):
        # Galaxy looks round (2026-11-25): a picked star opens
        # the shared detail drawer — the same surface, builder
        # and open behaviour as a picked catalogue row.
        galaxy = getattr(self, "skills_galaxy", None)
        if galaxy is None:
            return
        star = galaxy.galaxy.node(name)
        if star is None:
            return
        if self._populate_skills_detail(star.name):
            self.skills_detail_drawer.setVisible(True)

    def _on_galaxy_selection_cleared(self):
        # Galaxy looks round: clearing the selection (empty sky)
        # closes the drawer — unless it is pinned ("Keep open"),
        # which is the drawer's contract for staying visible.
        drawer = getattr(self, "skills_detail_drawer", None)
        if drawer is not None and drawer.isVisible() \
                and not drawer.pinned:
            drawer.setVisible(False)
            self._skills_detail_name = ""

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

    # ------------------------------------------------- skills detail
    # UI Phase 2: the detail drawer + the refresh that keeps the
    # Centre honest.

    def _build_skills_detail_drawer(self):
        """The Centre's detail surface (D-P2-06): the shared
        ContextDrawer rail (280 px) beside the list, filled from
        the ONE shared detail builder. Its own rail Pin is renamed
        "Keep open" (the drawer's contract — it means the rail
        stays visible when the selection clears); the skill Pin is
        a separate button in the actions row."""
        from .design_system.drawer import ContextDrawer
        from .style import LINE_STRONG, SURFACE_1

        panel = ContextDrawer(title="Skill detail")
        panel.setFixedWidth(280)
        panel.setObjectName("skillsDetailDrawer")
        panel.setStyleSheet(
            f"#skillsDetailDrawer {{ background: {SURFACE_1}; "
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
        self.skills_detail_title = QLabel("")
        self.skills_detail_title.setObjectName("sectionTitle")
        self.skills_detail_title.setWordWrap(True)
        lay.addWidget(self.skills_detail_title)
        self.skills_detail_group = QLabel("")
        self.skills_detail_group.setObjectName("muted")
        lay.addWidget(self.skills_detail_group)
        self.skills_detail_desc = QLabel("")
        self.skills_detail_desc.setWordWrap(True)
        lay.addWidget(self.skills_detail_desc)
        self.skills_detail_example = QLabel("")
        self.skills_detail_example.setObjectName("muted")
        self.skills_detail_example.setWordWrap(True)
        lay.addWidget(self.skills_detail_example)
        self.skills_detail_reqs = QLabel("")
        self.skills_detail_reqs.setObjectName("muted")
        self.skills_detail_reqs.setWordWrap(True)
        lay.addWidget(self.skills_detail_reqs)
        self.skills_detail_conn = QLabel("")
        self.skills_detail_conn.setObjectName("muted")
        self.skills_detail_conn.setWordWrap(True)
        lay.addWidget(self.skills_detail_conn)
        self.skills_detail_raw = QLabel("")
        self.skills_detail_raw.setObjectName("muted")
        self.skills_detail_raw.setFont(
            QFont(first_font_family(MONO_FONT_STACK)))
        lay.addWidget(self.skills_detail_raw)
        lay.addStretch()
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.btn_skills_detail_pin = QPushButton("Pin to Home")
        self.btn_skills_detail_pin.setObjectName("chipBtn")
        self.btn_skills_detail_pin.clicked.connect(
            self._on_skills_detail_pin_clicked)
        actions.addWidget(self.btn_skills_detail_pin)
        self.btn_skills_detail_ask = QPushButton("Ask with this")
        self.btn_skills_detail_ask.setObjectName("primaryBtn")
        self.btn_skills_detail_ask.clicked.connect(
            self._on_skills_detail_ask)
        actions.addWidget(self.btn_skills_detail_ask)
        self.btn_skills_detail_close = QPushButton("Close")
        self.btn_skills_detail_close.setObjectName("chipBtn")
        self.btn_skills_detail_close.clicked.connect(
            self._on_skills_detail_close)
        actions.addWidget(self.btn_skills_detail_close)
        lay.addLayout(actions)
        panel.setVisible(False)
        self.skills_detail_drawer = panel
        self._skills_detail_name = ""
        return panel

    def _populate_skills_detail(self, name: str) -> bool:
        """Fill the drawer from the ONE shared detail builder.
        Returns False when the tool is unknown (the drawer stays
        closed). Uncarried fields are omitted, never guessed."""
        from .skill_detail import build_skill_detail
        from .skill_pins import is_pinned

        try:
            detail = build_skill_detail(name, settings=self.settings)
        except Exception:  # noqa: BLE001
            detail = None
        if detail is None:
            return False
        self._skills_detail_name = name
        self.skills_detail_title.setText(detail["friendly"])
        self.skills_detail_group.setText(detail["group"])
        self.skills_detail_desc.setText(detail["description"])
        self.skills_detail_example.setText(detail["example"] or "")
        self.skills_detail_example.setVisible(bool(detail["example"]))
        self.skills_detail_reqs.setText(detail["requirements"] or "")
        self.skills_detail_reqs.setVisible(bool(detail["requirements"]))
        connection = detail["connection"]
        if connection == "connected":
            conn_text = "Connected — Google account linked"
        elif connection == "not_connected":
            conn_text = ("Not connected — link Google in "
                         "Settings → Your world")
        elif connection == "unknown":
            conn_text = "Couldn't check the connection just now"
        else:
            conn_text = ""
        self.skills_detail_conn.setText(conn_text)
        self.skills_detail_conn.setVisible(bool(conn_text))
        self.skills_detail_raw.setText(name)
        # Private mode suppresses the Pin action's target list
        # (D-P2-18): the button hides, the detail stays.
        private = self._mem_private_on()
        self.btn_skills_detail_pin.setVisible(not private)
        if not private:
            try:
                pinned = is_pinned(name, settings=self.settings)
            except Exception:  # noqa: BLE001
                pinned = False
            self.btn_skills_detail_pin.setText(
                "Unpin from Home" if pinned else "Pin to Home")
        return True

    def _on_catalogue_skill_chosen(self, name: str):
        """A picked catalogue row opens the drawer on the shared
        detail. The list keeps its filter and scroll position —
        the drawer sits beside it, it does not replace it."""
        if self._populate_skills_detail(name):
            self.skills_detail_drawer.setVisible(True)

    def _on_skills_detail_pin_clicked(self):
        from .skill_pins import is_pinned, set_pinned

        name = getattr(self, "_skills_detail_name", "")
        if not name:
            return
        try:
            pinned = not is_pinned(name, settings=self.settings)
            set_pinned(name, pinned, settings=self.settings)
        except Exception:  # noqa: BLE001
            return
        # Re-read the drawer (fresh pin state) and the
        # catalogue's Pinned section; the Home dock follows the
        # same store on its own 1 s tick.
        self._populate_skills_detail(name)
        cat = getattr(self, "skills_catalog", None)
        if cat is not None:
            try:
                cat.refresh_pins()
            except Exception:  # noqa: BLE001
                pass

    def _on_skills_detail_ask(self):
        name = getattr(self, "_skills_detail_name", "")
        if not name:
            return
        friendly = self.skills_detail_title.text() or name
        self._on_galaxy_ask(name, friendly)

    def _on_skills_detail_close(self):
        drawer = getattr(self, "skills_detail_drawer", None)
        if drawer is not None:
            drawer.setVisible(False)
        self._skills_detail_name = ""

    def _sync_skills_filter(self, mode: str):
        """The filter is shared between the views (D-P2-17):
        switching carries the text across, so the view change
        never resets what the user was looking for."""
        cat = getattr(self, "skills_catalog", None)
        cat_text = ""
        if cat is not None:
            try:
                cat_text = cat.search_input.text()
            except Exception:  # noqa: BLE001
                cat_text = ""
        gal_text = self.skills_galaxy_search.text()
        if mode == "galaxy":
            if cat_text and gal_text != cat_text:
                self.skills_galaxy_search.setText(cat_text)
        else:
            if gal_text and cat is not None and cat_text != gal_text:
                cat.set_filter(gal_text)

    def _update_skills_count_line(self):
        """The count line re-reads from the registry (D-P2-17) —
        "772 capabilities — 11 groups", or "M of 772 shown"
        while filtering. Empty (rather than wrong) if the
        registry cannot be read."""
        label = getattr(self, "skills_count_label", None)
        if label is None:
            return
        try:
            from voice_assistant.core.gui_facade import (
                catalogue_groups_data as _groups)
            from voice_assistant.core.gui_facade import (
                tools as _reg)

            total = len(_reg.get_declarations())
            n_groups = len(_groups.GROUP_ORDER)
            cat = getattr(self, "skills_catalog", None)
            if (cat is not None
                    and cat.search_input.text().strip()):
                label.setText(f"{cat.visible_count} of {total} shown")
            else:
                label.setText(
                    f"{total} capabilities — {n_groups} groups")
        except Exception:  # noqa: BLE001
            pass  # no count rather than a wrong one

    def _refresh_skills_page(self):
        """Rebuild the Centre from the live registry (D-P2-17):
        count line, catalogue data, galaxy data if built, private
        mode, plugin visibility. Preserves the open view, the
        filter text and the scroll position."""
        scroll = None
        page_v = None
        try:
            scroll = self.pages.widget(3)
            page_v = scroll.verticalScrollBar().value()
        except Exception:  # noqa: BLE001
            scroll = None
        cat = getattr(self, "skills_catalog", None)
        cat_v = None
        if cat is not None:
            try:
                cat_v = cat.skills_list.verticalScrollBar().value()
            except Exception:  # noqa: BLE001
                cat_v = None
        self._update_skills_count_line()
        try:
            from voice_assistant.core.gui_facade import (
                tools as _reg)

            _decls = list(_reg.get_declarations())
            if cat is not None:
                cat.refresh_data(_decls, _TOOL_FRIENDLY_NAMES)
            galaxy = getattr(self, "skills_galaxy", None)
            if galaxy is not None:
                galaxy.set_data(_decls, _TOOL_FRIENDLY_NAMES)
        except Exception:  # noqa: BLE001
            pass  # the stale list stays up rather than blanking.
        private = self._mem_private_on()
        if cat is not None:
            try:
                cat.set_private_mode(private)
            except Exception:  # noqa: BLE001
                pass
        plugin_list = getattr(self, "plugin_list", None)
        note = getattr(self, "skills_plugin_private_note", None)
        if plugin_list is not None:
            plugin_list.setVisible(not private)
        if note is not None:
            note.setVisible(private)
        drawer = getattr(self, "skills_detail_drawer", None)
        if (drawer is not None and drawer.isVisible()
                and getattr(self, "_skills_detail_name", "")):
            self._populate_skills_detail(self._skills_detail_name)
        if cat is not None and cat_v is not None:
            try:
                cat.skills_list.verticalScrollBar().setValue(cat_v)
            except Exception:  # noqa: BLE001
                pass
        if scroll is not None and page_v is not None:
            try:
                scroll.verticalScrollBar().setValue(page_v)
            except Exception:  # noqa: BLE001
                pass

    def _layout_skills_galaxy(self):
        """Galaxy layout (Galaxy looks round, 2026-11-25): the
        container holds ONLY the canvas now, at every width — the
        detail is the shared drawer beside the page, not a card
        inside the container, so there is nothing to stack
        (the old D-P2-20 stacking is gone).

        The one job left here is to keep the canvas FILLING the
        container: an alignment on a box-layout item stops it
        stretching to its cell — the old AlignLeft is what
        strangled the canvas to its 320 px minimum while ~420 px
        of dead space sat beside it. Clear any alignment so the
        stretch reaches the canvas."""
        container = getattr(self, "skills_galaxy_container", None)
        galaxy = getattr(self, "skills_galaxy", None)
        if container is None or galaxy is None:
            return
        layout = container.layout()
        if layout is None:
            return
        layout.setDirection(QBoxLayout.Direction.LeftToRight)
        layout.setStretch(0, 1)
        layout.setAlignment(galaxy, Qt.AlignmentFlag(0))

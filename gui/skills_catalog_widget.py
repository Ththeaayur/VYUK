"""
Skills catalogue (v3.2) — the built-in half of the Skills page.

The Skills page used to list only user plugins, so a fresh VYUK —
with well over a hundred built-in tools — looked like she could do
almost nothing. This widget is the catalogue that sits above the
plugins list: every built-in tool, grouped by the pack that
registered it, searchable, with a friendly name and a one-line
summary per skill.

Public API (the window wires exactly this):

    build_skills_catalog(declarations, friendly_names) -> QWidget

* ``declarations`` are ``tools.get_declarations()`` dicts
  (``{name, description, parameters}``).
* ``friendly_names`` is the window's ``_TOOL_FRIENDLY_NAMES`` map.

The returned widget carries ``search_input`` (its QLineEdit),
``skills_list`` and ``empty_label`` attributes, plus
``set_filter(text)`` so the page can drive the search from
outside. It deliberately has NO card frame of its own — the page
provides the card; this is the inner content. (Hunt fix (c)
item 3: the card-foot ``count_label`` was removed — the page
header carries the count now.)

Grouping: GROUP_MEMBERS below assigns every known tool name to
exactly one group, derived from the pack that registers it in
``core/funnel/tools_registry.py`` / the pack modules:

* "Desktop control"  — core/desktop/control.py + core/desktop/control_plus.py +
                       core/desktop/cursor.py packs (direct manipulation:
                       mouse, keyboard, windows, files, screen).
* "Intelligence"     — core/brain/planner.py (plans + routines) plus the
                       extended pack's set_reminder and the day
                       journal in core/episodes.py.
* "Browser & web"    — core/browser.py.
* "Your world"       — core/google_services.py.
* "Everyday skills"  — core/skills_plus.py.
* "Play & create"    — core/skills_games.py + core/skills_creative.py
                       (v5.13): games with persistent state and the
                       generative/creative tools.
* "System & apps"    — core/skills_system.py.
* "Files & media"    — core/skills_media.py.
* "Network & display"— core/skills_network.py.
* "Built-in"         — the default + extended tools registered in
                       core/funnel/tools_handlers_pack_core1.py (time, weather, memory
                       facts, system info, clipboard, basic files).
* "More skills"      — fallback for anything unplaced (e.g. plugin
                       declarations the registry also reports), so
                       a name is never dropped and never doubled.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen

# Command Deck Phase E: the delegate paints from the token palette.
from .design_system.empty import EmptyState
from .design_system.palette import parse_color
from .skill_detail import state_chips
from .skill_pins import pinned_tools
from .style import ACCENT, TEXT, TEXT_FAINT, TEXT_MUTED
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)
from voice_assistant.core.gui_facade import skill_search

# GUI split #2 INTENTIONAL: data half moved verbatim to
# skills_catalog_data.py; the widget imports the tables, roles and
# constants from there (no duplication, no behaviour change).
from .skills_catalog_data import (
    GROUP_ORDER,
    ROLE_CHIPS,
    ROLE_FRIENDLY,
    ROLE_GROUP,
    ROLE_KIND,
    ROLE_NAME,
    ROLE_SUMMARY,
    _FALLBACK_GROUP,
    _MAX_SUMMARY,
    _NAME_TO_GROUP,
    _summarise,
)

def group_for_name(name: str) -> str:
    """The catalogue group for one raw tool name (fallback-safe)."""
    return _NAME_TO_GROUP.get(name, _FALLBACK_GROUP)


def _humanise(name: str) -> str:
    """Friendly-name fallback for a tool missing from the map."""
    text = name.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else name


def _elide_word_boundary(metrics, text, width):
    """Elide to whole words, never mid-word. Hunt fix (c) item 4:
    the delegate's elidedText cut the friendly name and the
    summary mid-word ("Desktop cont…", "Setting…"), so a row
    read as a broken word. This mirrors the briefing summary's
    word-by-word elision (hunt fix (c) §7): take whole words
    while the next still fits with the ellipsis, and measure
    the candidate WITH the ellipsis as one string (the summed
    advances overstate it). Returns the text unchanged when it
    fits; when no whole word fits, returns "" (draw nothing)
    rather than a mid-word fragment ("C…")."""
    if not text:
        return text
    if width <= 0:
        return ""
    if metrics.horizontalAdvance(text) <= width:
        return text
    ellipsis = "…"
    visible = ""
    for word in text.split(" "):
        candidate = word if not visible else visible + " " + word
        if metrics.horizontalAdvance(candidate + ellipsis) <= width:
            visible = candidate
        else:
            break
    if not visible:
        return ""
    return visible + ellipsis


# _summarise lives in skills_catalog_data now (UI Phase 2): the
# one summary rule, shared by the rows, the detail builder and
# the Galaxy card without an import cycle. Re-exported here so
# the skills_catalog shim keeps working unchanged.
class _CatalogueDelegate(QStyledItemDelegate):
    """Paints rows: friendly name in demi-bold, summary in muted
    regular, headers as muted caps — the QListWidget keeps plain
    text on every item (name + " — " + summary) for accessibility
    and tests; this delegate only upgrades the rendering."""

    _HEADER_H = 30
    _TOOL_H = 38

    def __init__(self, parent=None):
        super().__init__(parent)
        # Stage 0 features batch 5 (larger text): the header
        # paints at an explicit 11 pt, so it does not follow the
        # QSS font scale — set_font_scale carries it (the row
        # text uses option.font, which the sheet scales).
        self._font_scale = 1.0
        # Look-fix 2 (2026-11-24): the row colours, re-resolved
        # from the theme by set_theme. Defaults are the house
        # tokens, so Cyan is unchanged.
        self._text = TEXT
        self._text_muted = TEXT_MUTED
        self._text_faint = TEXT_FAINT
        self._hairline = QColor(255, 255, 255, 12)

    def set_theme(self, resolved: dict) -> None:
        """Recolour the painted rows from a resolved theme.

        Look-fix 2: the delegate painted the friendly name, the
        summary and the group headers from the module-level dark
        TEXT / TEXT_MUTED / TEXT_FAINT, so on Light every row was
        washed out (near-white on white). Under Cyan the resolved
        values are the house tokens, so nothing changes. The row
        hairline follows the theme text at the same alpha (white
        on dark, dark on light).
        """
        try:
            resolved = resolved or {}
            self._text = resolved.get("text", TEXT)
            self._text_muted = resolved.get("text_muted", TEXT_MUTED)
            self._text_faint = resolved.get("text_faint", TEXT_FAINT)
            r, g, b, _a = parse_color(self._text)
            self._hairline = QColor(r, g, b, 12)
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass
        parent = self.parent()
        if parent is not None:
            parent.update()

    def set_font_scale(self, scale: float) -> None:
        """Scale the painted header (batch 5, item 12)."""
        try:
            self._font_scale = float(scale)
        except (TypeError, ValueError):
            self._font_scale = 1.0
        parent = self.parent()
        if parent is not None:
            parent.update()

    def sizeHint(self, option, index):  # noqa: N802 (Qt naming)
        size = super().sizeHint(option, index)
        kind = index.data(ROLE_KIND)
        size.setHeight(self._HEADER_H if kind in ("header", "note")
                       else self._TOOL_H)
        return size

    def paint(self, painter, option, index):  # noqa: N802 (Qt naming)
        kind = index.data(ROLE_KIND)
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if option.state & QStyle.StateFlag.State_Selected:
            _ar, _ag, _ab, _aa = parse_color(ACCENT)
            painter.fillRect(rect, QColor(_ar, _ag, _ab, 40))

        if kind == "note":
            # UI Phase 2: a muted one-line note (the private-mode
            # Pinned section) — plain text, never a tool row.
            font = QFont(option.font)
            painter.setFont(font)
            painter.setPen(QColor(self._text_muted))
            text_rect = rect.adjusted(12, 0, -12, 0)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                index.data(Qt.ItemDataRole.DisplayRole) or "",
            )
        elif kind == "header":
            font = QFont(option.font)
            font.setPointSizeF(11 * self._font_scale)
            font.setWeight(QFont.Weight.DemiBold)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
            painter.setFont(font)
            painter.setPen(QColor(self._text_faint))
            text_rect = rect.adjusted(12, 0, -12, 0)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                index.data(Qt.ItemDataRole.DisplayRole) or "",
            )
        else:
            friendly = index.data(ROLE_FRIENDLY) or ""
            summary = index.data(ROLE_SUMMARY) or ""
            left = rect.left() + 12
            right = rect.right() - 12
            center_y = rect.center().y()

            # State chips (UI Phase 2): muted caps at the row's
            # right edge — the summary elides to make room, so a
            # chip never overlaps the text. A ready tool carries
            # no chip.
            chips = index.data(ROLE_CHIPS) or []
            chip_font = QFont(option.font)
            chip_font.setPointSizeF(10 * self._font_scale)
            chip_font.setWeight(QFont.Weight.DemiBold)
            chip_font.setLetterSpacing(
                QFont.SpacingType.PercentageSpacing, 106)
            chip_metrics = QFontMetrics(chip_font)
            chip_texts = [str(c).upper() for c in chips if str(c)]
            chips_w = 0
            if chip_texts:
                chips_w = sum(chip_metrics.horizontalAdvance(t)
                              for t in chip_texts)
                chips_w += 8 * len(chip_texts)
            text_right = right - chips_w

            bold_font = QFont(option.font)
            bold_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(bold_font)
            painter.setPen(QColor(self._text))
            bold_metrics = QFontMetrics(bold_font)
            # UI Phase 2 (2026-11-25): the friendly name is elided
            # to the chips' left edge, like the summary — at narrow
            # widths (640) an unelided name ran UNDER the chips
            # (the summary was elided, the name was not).
            # Hunt fix (c) item 4: word-boundary elision, never
            # mid-word ("Desktop cont…").
            friendly = _elide_word_boundary(
                bold_metrics, friendly, max(0, text_right - left))
            friendly_w = bold_metrics.horizontalAdvance(friendly)
            painter.drawText(
                left,
                center_y + bold_metrics.ascent() // 2 - 1,
                friendly,
            )

            if summary:
                sep = " — "
                regular_font = QFont(option.font)
                regular_metrics = QFontMetrics(regular_font)
                sep_x = left + friendly_w
                sep_w = regular_metrics.horizontalAdvance(sep)
                # UI Phase 2 (2026-11-25): the separator is drawn
                # only when it fits before the chips — at 640 the
                # elided name already reaches the chips' edge, and
                # an unconditional " — " ran INTO the first chip.
                # Hunt fix (c) item 4: the separator is drawn only
                # when the summary has a whole word to show — if
                # no word fits, neither is drawn (a dangling " — "
                # with nothing after it reads as broken).
                if sep_x + sep_w <= text_right:
                    avail = max(0, text_right - (sep_x + sep_w))
                    # Hunt fix (c) item 4: word-boundary elision,
                    # never mid-word ("Setting…").
                    elided = _elide_word_boundary(
                        regular_metrics, summary, avail)
                    if elided:
                        painter.setFont(regular_font)
                        painter.setPen(QColor(self._text_muted))
                        painter.drawText(
                            sep_x,
                            center_y + regular_metrics.ascent() // 2 - 1,
                            sep,
                        )
                        painter.drawText(
                            sep_x + sep_w,
                            center_y + regular_metrics.ascent() // 2 - 1,
                            elided,
                        )

            if chip_texts:
                painter.setFont(chip_font)
                painter.setPen(QColor(self._text_faint))
                chip_x = right
                for text in reversed(chip_texts):
                    w = chip_metrics.horizontalAdvance(text)
                    chip_x -= w
                    painter.drawText(
                        chip_x,
                        center_y + chip_metrics.ascent() // 2 - 1,
                        text)
                    chip_x -= 8

        # Row hairline, matching the stylesheet's item border.
        painter.setPen(QPen(self._hairline, 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.restore()


class SkillsCatalogWidget(QWidget):
    """Search + grouped list + count footer for all registered
    tools (GAPS-29, E-V1: plugin tools included — they land in
    "More skills" through the group_for_name fallback).

    UI Phase 2: rows are clickable (``skill_chosen`` carries the
    raw tool name to the page's detail drawer); search runs
    through the ONE shared token index (core/skill_search), so
    a goal phrase finds the tool; a Pinned section tops the
    list when pins exist; rows carry state chips (Pinned /
    Needs permission / Needs connection) from the one shared
    detail builder; no match shows the shared EmptyState with
    a clear-search action. In private mode the Pinned section
    shows "Private mode is on" with no names, and the Pinned
    chip is suppressed (the pin store is user data).
    """

    skill_chosen = Signal(str)
    counts_changed = Signal()

    def __init__(self, declarations, friendly_names, parent=None,
                 settings=None):
        super().__init__(parent)
        self.setObjectName("skillsCatalog")

        self._settings = settings
        self._private_mode = False
        self._friendly_names = dict(friendly_names or {})
        self._tools: list[dict] = []
        self._total = 0
        self._visible = 0
        self._search_index = None
        self._load_tools(declarations)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search skills…")
        self.search_input.setClearButtonEnabled(True)
        # Phase 6 (D-02 family / repro leg61): Qt's built-in
        # clear button is an icon-only QToolButton with no
        # tooltip and no accessible name of its own. The
        # catalogue only builds under the harness since the
        # D-03 shim fix, so this button only started appearing
        # in the repro's icon-button sweep then. Name it so
        # every icon-only button names itself.
        try:
            from PySide6.QtWidgets import QToolButton as _QTB
            _clear = self.search_input.findChild(_QTB)
            if _clear is not None:
                _clear.setToolTip("Clear search")
                _clear.setAccessibleName("Clear search")
        except Exception:  # noqa: BLE001 — cosmetic
            pass
        self.search_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_input)

        self.skills_list = QListWidget()
        self.skills_list.setObjectName("skillsList")
        self._delegate = _CatalogueDelegate(self.skills_list)
        self.skills_list.setItemDelegate(self._delegate)
        self.skills_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.skills_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.skills_list.setUniformItemSizes(False)
        self.skills_list.itemClicked.connect(self._on_row_activated)
        self.skills_list.itemActivated.connect(self._on_row_activated)
        layout.addWidget(self.skills_list, stretch=1)

        # UI Phase 2 (D-P2-16): no match is the shared EmptyState,
        # not a bare label — same words, plus the safe action
        # (clear the search).
        self.empty_state = EmptyState(
            sentence="",
            suggested_task="",
            action_label="Clear search")
        self.empty_state.action_requested.connect(
            lambda: self.set_filter(""))
        self.empty_state.hide()
        layout.addWidget(self.empty_state)

        # Hunt fix (c) item 3: the card-foot count label is gone
        # ("772 capabilities" duplicated the page header's
        # "772 capabilities — 11 groups"). The header covers the
        # filter state too ("M of 772 shown"); counts_changed
        # still fires below so the header stays in sync.

        self._rebuild("")

    def _load_tools(self, declarations):
        """(Re)build the tool table + the search index from
        declarations. De-duplicate by raw name (first wins) so
        a tool can never appear twice, then bucket by group
        preserving registry order."""
        self._tools = []
        seen: set[str] = set()
        for decl in declarations or []:
            name = (decl or {}).get("name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            self._tools.append(
                {
                    "name": name,
                    "friendly": self._friendly_names.get(name) or _humanise(name),
                    "description": (decl or {}).get("description") or "",
                    "group": group_for_name(name),
                }
            )
        self._total = len(self._tools)
        # The search index is built from THIS widget's tools, so
        # a fixture catalogue searches its own rows and the live
        # catalogue searches the live registry — same builder,
        # same ranking, never a second matcher.
        self._search_index = skill_search.build_index(
            declarations=[{"name": t["name"],
                           "description": t["description"]}
                          for t in self._tools])

    # -- public surface -------------------------------------------------

    def set_filter(self, text: str) -> None:
        """Drive the search from outside (also updates the field)."""
        text = text or ""
        if self.search_input.text() != text:
            self.search_input.setText(text)  # textChanged -> _apply_filter
        else:
            self._apply_filter(text)

    def refresh_data(self, declarations, friendly_names) -> None:
        """Rebuild the catalogue from fresh declarations (the
        page calls this on _show_page(3), after Rescan, and
        after a pin change). The filter text survives — a
        refresh never clears what the user typed."""
        self._friendly_names = dict(friendly_names or {})
        self._load_tools(declarations)
        self._rebuild(self.search_input.text() or "")

    def refresh_pins(self) -> None:
        """Rebuild with the current filter after a pin changed
        elsewhere (the Galaxy card, the detail drawer) — the
        Pinned section and the row chips follow the store."""
        self._rebuild(self.search_input.text())

    def set_private_mode(self, private: bool) -> None:
        """Private mode hides the pin state (the Pinned section
        shows "Private mode is on" with no names, and the Pinned
        chip is suppressed — the pin store is user data)."""
        private = bool(private)
        if private == self._private_mode:
            return
        self._private_mode = private
        self._rebuild(self.search_input.text() or "")

    def group_counts(self) -> dict[str, int]:
        """Tool count per group over the FULL catalogue (unfiltered)."""
        counts: dict[str, int] = {}
        for tool in self._tools:
            counts[tool["group"]] = counts.get(tool["group"], 0) + 1
        return counts

    def tool_names(self) -> list[str]:
        """Every catalogued raw tool name, in display order —
        each once (a pinned tool also appears in its group, but
        the Pinned section is a shortcut, not a second tool)."""
        names: list[str] = []
        for row in range(self.skills_list.count()):
            item = self.skills_list.item(row)
            if item.data(ROLE_KIND) == "tool":
                name = item.data(ROLE_NAME)
                if name and name not in names:
                    names.append(name)
        return names

    @property
    def total_count(self) -> int:
        return self._total

    @property
    def visible_count(self) -> int:
        return self._visible

    # -- internals ------------------------------------------------------

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        # The list's own geometry settles in the layout pass that
        # follows this event — activate it now so the snap reads
        # the settled geometry synchronously. (A deferred snap
        # left a one-frame straddle that the render driver
        # captured: the drawer-open detail grab showed the bottom
        # row cut before the snap's frame landed.)
        try:
            layout = self.layout()
            if layout is not None:
                layout.activate()
        except Exception:  # noqa: BLE001 - layout never breaks resize
            pass
        self._snap_list_to_whole_row()

    def showEvent(self, event):  # noqa: N802 - Qt naming
        super().showEvent(event)
        # A resize before first show snaps nothing (the list is
        # not visible yet, so the guard returns early) and show
        # fires no resize when the size is unchanged — snap here
        # so the first shown frame is already on a whole row.
        try:
            layout = self.layout()
            if layout is not None:
                layout.activate()
        except Exception:  # noqa: BLE001 - layout never breaks show
            pass
        self._snap_list_to_whole_row()

    def _snap_list_to_whole_row(self):
        """Grow the list so its bottom row never straddles the
        viewport edge in the DEFAULT view (look-fix 2026-11-25,
        defect 1).

        At 1280 the catalogue's bottom row ("Typing") straddled
        the viewport edge by 16 px: the row's descenders fell
        below the edge, so "Typing" painted as "Tvoina" — a
        legible-but-wrong word, not an obviously cut row. A
        mid-glyph cut at a list's bottom edge is normal scroll
        behaviour once the user scrolls, so this snaps ONLY the
        default (unscrolled) view, on resize / rebuild: find the
        row straddling the viewport bottom edge and grow the
        list by exactly the shortfall, so that row ends exactly
        at the edge and the next row starts there (hidden, not
        straddling).

        Self-correcting, not accumulating: the minimum height is
        set to the CURRENT list height + the shortfall, so a
        later resize recomputes from the new layout instead of
        stacking growth. A whole-row viewport computes zero
        shortfall, so the steady state is stable; the minimum
        height is never cleared here (clearing would oscillate:
        shrink -> straddle -> grow -> shrink). The page lives
        in a scroll area, so the extra <38 px is absorbed there.
        """
        try:
            lst = self.skills_list
            if not lst.isVisible():
                return
            if lst.verticalScrollBar().value() != 0:
                return  # mid-scroll straddles are the user's own
            # Drawer-open: the detail drawer makes the list tall
            # and the page taller than the window (it scrolls), so
            # the bottom row is cut by the WINDOW edge, not by the
            # list's viewport — growing the list only pushes more
            # below the fold (the render showed a tall card with
            # the Media row clipped and the plugins hidden, where
            # the pre-fix short card had whole rows and the
            # plugins visible). The snap is for the default
            # (drawer-closed) catalogue view only.
            try:
                win = self.window()
                drawer = getattr(win, "skills_detail_drawer", None)
                if drawer is not None and drawer.isVisible():
                    return
            except Exception:  # noqa: BLE001 - a missing window
                pass  # never blocks the snap
            vp_h = lst.viewport().height()
            if vp_h <= 0 or lst.count() == 0:
                return
            model = lst.model()
            for row in range(lst.count()):
                vr = lst.visualRect(model.index(row, 0))
                if not vr.isValid():
                    continue
                # QRect.bottom() is INCLUSIVE (top + height - 1),
                # while the viewport shows [0, vp_h - 1]: the row
                # straddles when its bottom pixel is at or below
                # the viewport's bottom edge, and the shortfall
                # to land it exactly on the edge is bottom - vp_h
                # + 1 (the pre-fix 1280 case: bottom 249, vp_h
                # 234 -> shortfall 16 -> viewport 250, the row's
                # last pixel 249 is the viewport's last pixel).
                if vr.top() < vp_h <= vr.bottom():
                    shortfall = vr.bottom() - vp_h + 1
                    if shortfall > 0:
                        lst.setMinimumHeight(lst.height() + shortfall)
                    return
        except Exception:  # noqa: BLE001 — layout never breaks
            pass  # the catalogue

    def _on_row_activated(self, item: QListWidgetItem) -> None:
        """A tool row was clicked (or Entered) — open its detail.
        Headers and notes are not activatable."""
        if item is None or item.data(ROLE_KIND) != "tool":
            return
        name = item.data(ROLE_NAME) or ""
        if name:
            self.skill_chosen.emit(name)

    def _pins(self) -> list[str]:
        """The pinned tool names, in pin order — [] when the
        store cannot be read (a failed read means no Pinned
        section, never a broken catalogue)."""
        try:
            return [n for n in pinned_tools(settings=self._settings)
                    if any(t["name"] == n for t in self._tools)]
        except Exception:  # noqa: BLE001 — pins are a convenience
            return []

    def set_font_scale(self, scale: float) -> None:
        """Scale the painted headers (batch 5, item 12)."""
        self._delegate.set_font_scale(scale)

    def set_theme(self, resolved: dict) -> None:
        """Recolour the painted rows (look-fix 2, finding 3)."""
        self._delegate.set_theme(resolved)

    def _apply_filter(self, text: str) -> None:
        self._rebuild(text or "")

    def _row_chips(self, name: str, group: str, pinned: bool,
                   connected: bool) -> list[str]:
        """The state chips for one tool. The chip RULE lives in
        skill_detail.state_chips (one place); the inputs are
        gathered here cheaply — one classification lookup, one
        cached connection read, one pinned-set lookup — so a
        rebuild never pays a registry scan or a QSettings read
        per row. In private mode the Pinned chip is suppressed
        (the pin store is user data); the permission and
        connection chips are product data and stay."""
        try:
            from voice_assistant.core.gui_facade import (
                tool_classification)
            permission = tool_classification.classify(name)
        except Exception:  # noqa: BLE001 — a failed detail
            permission = "unclassified"  # means no chips.
        connection = None
        if group == "Your world":
            connection = ("connected" if connected
                          else "not_connected")
        chips = state_chips({
            "pinned": pinned,
            "permission": permission,
            "group": group,
            "connection": connection,
        })
        if self._private_mode:
            chips = [c for c in chips if c != "Pinned"]
        return chips

    def _add_tool_row(self, tool: dict, pinned: bool,
                      connected: bool) -> None:
        summary = _summarise(tool["description"])
        text = (
            f"{tool['friendly']} — {summary}" if summary
            else tool["friendly"]
        )
        item = QListWidgetItem(text)
        item.setData(ROLE_NAME, tool["name"])
        item.setData(ROLE_FRIENDLY, tool["friendly"])
        item.setData(ROLE_SUMMARY, summary)
        item.setData(ROLE_KIND, "tool")
        item.setData(ROLE_GROUP, tool["group"])
        item.setData(ROLE_CHIPS, self._row_chips(
            tool["name"], tool["group"], pinned, connected))
        if tool["description"]:
            item.setToolTip(tool["description"])
        self.skills_list.addItem(item)

    def _rebuild(self, query: str) -> None:
        needle = (query or "").strip()
        if needle:
            # Goal search (UI Phase 2, D-P2-04): the ONE shared
            # token index ranks the tools for the phrase — the
            # rows follow that ranking within each group. No cap:
            # the catalogue shows every match, grouped.
            ranked = skill_search.search(self._search_index, needle,
                                         limit=None)
            rank = {name: i for i, name in enumerate(ranked)}
            allowed = set(ranked)
            buckets: dict[str, list[dict]] = {
                group: [] for group in GROUP_ORDER}
            for tool in self._tools:
                if tool["name"] in allowed:
                    buckets[tool["group"]].append(tool)
            for rows in buckets.values():
                rows.sort(key=lambda t: rank[t["name"]])
        else:
            buckets = {group: [] for group in GROUP_ORDER}
            for tool in self._tools:
                buckets[tool["group"]].append(tool)

        self._visible = sum(len(rows) for rows in buckets.values())
        by_name = {t["name"]: t for t in self._tools}
        # One live connection read per rebuild (never one per
        # Your-world row), and one pinned-set lookup.
        try:
            from voice_assistant.core.gui_facade import google_services

            connected = bool(google_services.is_connected())
        except Exception:  # noqa: BLE001 — a failed read is
            connected = False  # "not connected", never a guess.
        pins = [] if needle else [n for n in self._pins()
                                  if n in by_name]
        pinned_set = set(pins)

        self.skills_list.clear()
        # Pinned section (UI Phase 2, D-P2-15): the pinned tools
        # as rows in the same shape, on top — absent when there
        # are no pins, never an empty box, and hidden while a
        # filter is active (the search results are the ranked
        # list; the section would double-count the visible
        # rows). The section is a shortcut: the tools stay in
        # their groups below.
        if not needle and self._private_mode:
            header = QListWidgetItem("PINNED")
            header.setData(ROLE_KIND, "header")
            header.setData(ROLE_GROUP, "Pinned")
            header.setToolTip("Pinned skills")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.skills_list.addItem(header)
            note = QListWidgetItem("Private mode is on")
            note.setData(ROLE_KIND, "note")
            note.setData(ROLE_GROUP, "Pinned")
            note.setFlags(Qt.ItemFlag.NoItemFlags)
            self.skills_list.addItem(note)
        elif not needle and pinned_set:
            header = QListWidgetItem(f"PINNED — {len(pins)}")
            header.setData(ROLE_KIND, "header")
            header.setData(ROLE_GROUP, "Pinned")
            header.setToolTip(f"{len(pins)} pinned skills")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.skills_list.addItem(header)
            for name in pins:
                self._add_tool_row(by_name[name], True, connected)
        for group in GROUP_ORDER:
            rows = buckets[group]
            if not rows:
                continue  # headers hide when their group empties
            header = QListWidgetItem(f"{group.upper()} — {len(rows)}")
            header.setData(ROLE_KIND, "header")
            header.setData(ROLE_GROUP, group)
            header.setToolTip(f"{len(rows)} skills")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.skills_list.addItem(header)
            for tool in rows:
                self._add_tool_row(
                    tool, tool["name"] in pinned_set, connected)

        if needle and self._visible == 0:
            self.empty_state.set_sentence(
                f"No skills match '{needle}'.")
            self.empty_state.show()
            self.skills_list.hide()
        else:
            self.empty_state.hide()
            self.skills_list.show()

        # Hunt fix (c) item 3: no card-foot count label anymore
        # (the page header carries the count); the signal stays.
        self.counts_changed.emit()
        # Look-fix 2026-11-25, defect 1: a rebuild re-lays the
        # rows — re-snap the default view to a whole-row boundary
        # (no-op while hidden or unscrolled-away; see the method).
        self._snap_list_to_whole_row()


def build_skills_catalog(declarations: list[dict],
                         friendly_names: dict[str, str],
                         settings=None) -> QWidget:
    """Build the skills catalogue widget (see module docstring) —
    all registered tools, plugin tools included (GAPS-29, E-V1).
    ``settings`` is the QSettings store the Pinned section reads
    (None reads as no pins)."""
    return SkillsCatalogWidget(declarations, friendly_names,
                               settings=settings)
